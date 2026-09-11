"""The local-first run bundle.

A run always writes a complete bundle on local disk BEFORE any remote publish
is attempted. Every other destination (S3, R2, a future GitHub publish) reads
from this bundle; none of them ever see in-flight probe state. That ordering
is what makes remote publish safely retryable: "probe completed locally,
remote publication failed, publish again later" is the whole point, and it
only holds if the local write is unconditionally first and self-contained.

Layout:

    runs/<date>/<run_id>/
        manifest.json       sha256 of every other artifact
        summary.json        conformance.report.summary_dict()
        results.jsonl       one line per trial (append-only, fsynced)
        report.md           the rendered report
        evidence/
            trial-0001.json     the same record as one results.jsonl line,
            trial-0002.json     addressable individually
            ...

No `latest.json` / `current.json`. The run_id is immutable and reused across
retried publishes -- a retry targets the SAME directory, it never creates a
new run.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

from .evidence import ProbeResult, sha256_hex, utc_now_iso

MANIFEST_SCHEMA = "llm-conformance-run/v1"


@dataclass
class RunBundle:
    run_id: str
    output_dir: str = "./runs"
    date_dir: str = field(default="")

    def __post_init__(self):
        if not self.date_dir:
            # Derived from the run_id's own embedded timestamp (…YYYYMMDDT…),
            # not wall-clock at bundle-open time, so date_dir and run_id never
            # disagree if a run straddles midnight. A regex search rather than
            # a fixed split-index, so an unusually shaped run_id (a synthetic
            # test id, say) degrades to today's date instead of silently
            # producing a directory like "run--".
            import re as _re
            m = _re.search(r"(\d{8})T\d{6}Z", self.run_id)
            if m:
                d = m.group(1)
                self.date_dir = f"{d[0:4]}-{d[4:6]}-{d[6:8]}"
            else:
                from datetime import datetime, timezone
                self.date_dir = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        os.makedirs(self.evidence_dir, exist_ok=True)
        self._trial_n = 0
        self._results_fh = open(self.artifact_path("results"), "a", encoding="utf-8")

    @property
    def run_dir(self) -> str:
        return os.path.join(self.output_dir, self.date_dir, self.run_id)

    @property
    def evidence_dir(self) -> str:
        return os.path.join(self.run_dir, "evidence")

    def artifact_path(self, name: str) -> str | None:
        return {
            "manifest": os.path.join(self.run_dir, "manifest.json"),
            "summary": os.path.join(self.run_dir, "summary.json"),
            "results": os.path.join(self.run_dir, "results.jsonl"),
            "report": os.path.join(self.run_dir, "report.md"),
        }.get(name)

    def artifact_files(self, name: str) -> list[str]:
        """Every file an artifact NAME expands to. Plural because
        'raw_evidence' is a directory, everything else is one file."""
        if name == "raw_evidence":
            if not os.path.isdir(self.evidence_dir):
                return []
            return sorted(
                os.path.join(self.evidence_dir, f)
                for f in os.listdir(self.evidence_dir)
            )
        path = self.artifact_path(name)
        return [path] if path and os.path.exists(path) else []

    def record(self, result: ProbeResult) -> None:
        """Append one trial. Fsynced immediately, same guarantee the old
        EvidenceWriter had: a crash mid-run loses nothing already probed."""
        line = result.to_json()
        self._results_fh.write(line + "\n")
        self._results_fh.flush()
        os.fsync(self._results_fh.fileno())

        self._trial_n += 1
        trial_path = os.path.join(self.evidence_dir, f"trial-{self._trial_n:04d}.json")
        with open(trial_path, "w", encoding="utf-8") as fh:
            fh.write(line)

    def write(self, result: ProbeResult) -> None:
        """Alias for record() -- lets a RunBundle drop into any code written
        against the old EvidenceWriter's .write() interface unchanged."""
        self.record(result)

    def close_results(self) -> None:
        self._results_fh.close()

    def write_summary(self, summary: dict[str, Any]) -> None:
        with open(self.artifact_path("summary"), "w", encoding="utf-8") as fh:
            json.dump(summary, fh, indent=2, sort_keys=True)

    def write_report(self, markdown: str) -> None:
        with open(self.artifact_path("report"), "w", encoding="utf-8") as fh:
            fh.write(markdown)

    def write_manifest(self, extra_artifact_names: tuple[str, ...] = ("raw_evidence",)) -> dict[str, Any]:
        """Hash every artifact currently on disk. Called last, after summary
        and report are written, so the manifest covers everything."""
        artifacts = []
        names = ("summary", "results", "report")
        for name in names:
            for path in self.artifact_files(name):
                artifacts.append(self._hash_entry(path, name))
        for name in extra_artifact_names:
            for path in self.artifact_files(name):
                artifacts.append(self._hash_entry(path, name))

        manifest = {
            "schema": MANIFEST_SCHEMA,
            "run_id": self.run_id,
            "generated_at": utc_now_iso(),
            "hash_algorithm": "sha256",
            "artifacts": artifacts,
        }
        with open(self.artifact_path("manifest"), "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, indent=2, sort_keys=True)
        return manifest

    def _hash_entry(self, path: str, artifact_name: str) -> dict[str, Any]:
        with open(path, "rb") as fh:
            data = fh.read()
        return {
            "name": os.path.relpath(path, self.run_dir),
            "artifact": artifact_name,
            "sha256": sha256_hex(data),
            "bytes": len(data),
        }


def open_bundle(run_id: str, output_dir: str = "./runs") -> RunBundle:
    return RunBundle(run_id=run_id, output_dir=output_dir)


def load_bundle(run_dir: str) -> RunBundle:
    """Reopen an existing run directory for publish/retry. Does not reopen
    results.jsonl for appending -- a loaded bundle is read-only; publishing
    never re-probes."""
    run_dir = run_dir.rstrip("/")
    run_id = os.path.basename(run_dir)
    date_dir = os.path.basename(os.path.dirname(run_dir))
    output_dir = os.path.dirname(os.path.dirname(run_dir)) or "."
    bundle = RunBundle.__new__(RunBundle)
    bundle.run_id = run_id
    bundle.date_dir = date_dir
    bundle.output_dir = output_dir
    bundle._trial_n = 0
    bundle._results_fh = None
    if not os.path.isdir(bundle.run_dir):
        raise FileNotFoundError(f"no such run directory: {run_dir}")
    return bundle


def verify_manifest(run_dir: str) -> list[str]:
    """Re-hash every artifact the manifest claims and report mismatches.
    Empty list means the bundle matches its own manifest bit-for-bit."""
    bundle = load_bundle(run_dir)
    manifest_path = bundle.artifact_path("manifest")
    if not os.path.exists(manifest_path):
        return [f"no manifest.json in {run_dir}"]
    with open(manifest_path, encoding="utf-8") as fh:
        manifest = json.load(fh)

    problems = []
    for entry in manifest.get("artifacts", []):
        path = os.path.join(bundle.run_dir, entry["name"])
        if not os.path.exists(path):
            problems.append(f"missing: {entry['name']}")
            continue
        with open(path, "rb") as fh:
            actual = sha256_hex(fh.read())
        if actual != entry["sha256"]:
            problems.append(f"hash mismatch: {entry['name']} "
                            f"(manifest {entry['sha256'][:12]}, actual {actual[:12]})")
    return problems

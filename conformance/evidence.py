"""Evidence records.

Hard requirement: every probe result must let a third party reproduce the
call or dispute the finding. That means recording the exact bytes we sent,
the exact bytes we got back, when, against which resolved model version.

Design notes:

* We record the request BODY AS SENT (the literal serialized bytes), not a
  reconstruction. This is why the harness speaks raw HTTP instead of using
  provider SDKs -- an SDK can silently rewrite a payload, and then the
  "exact request payload" in the record is a lie.

* We record response HEADERS in addition to the body. Provider request-ids
  (x-request-id, request-id, x-goog-request-id) are what lets a provider
  look up our call in THEIR logs. A finding a provider can audit is a
  finding they can concede; one they can't is one they'll dismiss.

* We record the model id we ASKED for and the model id the response
  REPORTS. Those differ more often than people expect (aliases resolving to
  snapshots), and the gap is itself a conformance signal.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import platform
import subprocess
import sys
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any

SCHEMA_VERSION = "conformance-evidence/1"

# Header names that carry a provider-side correlation id. Kept broad on
# purpose: a header we don't recognise is a dispute we can't win.
REQUEST_ID_HEADERS = (
    "x-request-id",
    "request-id",
    "x-amzn-requestid",
    "x-amz-request-id",
    "x-goog-request-id",
    "x-ms-request-id",
    "apim-request-id",
    "cf-ray",
)

# Anything matching these is redacted before an evidence bundle is written.
_SENSITIVE_HEADERS = {
    "authorization",
    "x-api-key",
    "api-key",
    "x-goog-api-key",
    "cookie",
    "set-cookie",
}


def utc_now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="milliseconds")


def sha256_hex(data: str | bytes) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def redact_headers(headers: dict[str, str]) -> dict[str, str]:
    """Strip credential material but keep proof that it was present.

    We record a fingerprint of the key rather than dropping it entirely so a
    later run can prove two results came from the same credential (or a
    different one) without ever exposing the key.
    """
    out: dict[str, str] = {}
    for k, v in headers.items():
        if k.lower() in _SENSITIVE_HEADERS:
            out[k] = f"[REDACTED sha256:{sha256_hex(v)[:12]}]"
        else:
            out[k] = v
    return out


def extract_request_id(headers: dict[str, str]) -> str | None:
    lowered = {k.lower(): v for k, v in headers.items()}
    for name in REQUEST_ID_HEADERS:
        if name in lowered:
            return f"{name}={lowered[name]}"
    return None


def harness_provenance() -> dict[str, Any]:
    """Identify the exact harness build that produced a result."""
    commit = None
    dirty = None
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        dirty = bool(
            subprocess.check_output(
                ["git", "status", "--porcelain"],
                cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                stderr=subprocess.DEVNULL,
                text=True,
            ).strip()
        )
    except Exception:
        pass
    return {
        "schema": SCHEMA_VERSION,
        "git_commit": commit,
        "git_dirty": dirty,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
    }


@dataclass
class HttpExchange:
    """The wire record. Everything needed to replay the call verbatim."""

    url: str
    method: str
    request_headers: dict[str, str]
    request_body: str            # exact serialized body as sent
    request_body_sha256: str
    status: int | None
    response_headers: dict[str, str]
    response_body: str           # raw, untouched, unparsed
    response_body_sha256: str
    provider_request_id: str | None
    started_at: str
    finished_at: str
    duration_ms: int
    transport_error: str | None = None

    def as_curl(self) -> str:
        """A literally runnable reproduction command.

        The auth header is left as a shell variable so the command is safe to
        paste into a report but still works once the reader exports their own
        key.
        """
        parts = [f"curl -sS -X {self.method} '{self.url}'"]
        for k, v in self.request_headers.items():
            if k.lower() in _SENSITIVE_HEADERS:
                v = "$PROVIDER_API_KEY"
            parts.append(f"  -H '{k}: {v}'")
        body = self.request_body.replace("'", "'\\''")
        parts.append(f"  -d '{body}'")
        return " \\\n".join(parts)


@dataclass
class ProbeResult:
    """One probe against one deployment: declared vs actual, plus evidence."""

    result_id: str
    run_id: str
    timestamp: str

    # what we probed
    deployment: dict[str, Any]   # full deployment tuple; see deployment.py
    trial_index: int             # 1-based repetition index
    trials_total: int
    provider: str
    model_requested: str
    model_reported: str | None
    endpoint: str
    capability: str              # "structured_output" | "tool_calling"
    mechanism: str               # HOW we asked -- see METHODOLOGY.md
    probe_id: str

    # the comparison
    declared: bool | None        # what models.dev says
    declared_source: str
    outcome: str                 # see probes/outcomes.py
    conforms: bool | None        # None = inconclusive, not a failure
    divergence: str              # none | silent | honest | undeclared | unknown

    detail: str                  # human-readable one-liner
    violations: list[str] = field(default_factory=list)

    exchange: HttpExchange | None = None
    provenance: dict[str, Any] = field(default_factory=harness_provenance)

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, sort_keys=True)


def new_run_id() -> str:
    return f"run_{_dt.datetime.now(_dt.timezone.utc):%Y%m%dT%H%M%SZ}_{uuid.uuid4().hex[:8]}"


def new_result_id() -> str:
    return f"res_{uuid.uuid4().hex[:16]}"


class EvidenceWriter:
    """Append-only JSONL. One result per line, flushed immediately.

    Append-only and flush-per-line matter: if a run dies halfway (rate limit,
    network), the evidence for everything already probed survives. A probe run
    that loses its results to a crash is a probe run you have to pay for twice.
    """

    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self._fh = open(path, "a", encoding="utf-8")

    def write(self, result: ProbeResult) -> None:
        self._fh.write(result.to_json() + "\n")
        self._fh.flush()
        os.fsync(self._fh.fileno())

    def close(self) -> None:
        self._fh.close()

    def __enter__(self) -> "EvidenceWriter":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

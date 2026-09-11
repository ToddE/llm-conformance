"""Output destinations: where a run's evidence goes.

A run always writes a COMPLETE local bundle first. Everything downstream --
remote publish, retry, sharing -- operates on that local bundle, never on
in-flight probe state. If a remote destination fails, the local run is already
valid and complete; publishing again later is a retry, never a re-probe. This
is what "local-first" means here and it is the one property every destination
implementation must preserve.

`OutputDestination.publish()` takes a `RunBundle` (see runbundle.py) and pushes
whatever artifacts that destination is configured to receive. Destinations
never see partial state and never trigger probing themselves.

S3Output speaks the S3 REST API directly over urllib with hand-rolled AWS
SigV4 signing (stdlib hashlib/hmac only) rather than depending on boto3. This
keeps the zero-dependency guarantee: cloning this repo and probing a
deployment needs nothing installed, and publishing to S3-compatible storage
(AWS S3, Cloudflare R2, MinIO -- they share the same signed-REST API) needs
nothing installed either. SigV4 is a fixed, well-documented algorithm; this
implements the single case actually needed here (PUT of a whole object, no
multipart), which keeps the surface small enough to read in one sitting.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import hmac
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any


class PublishError(RuntimeError):
    """A destination failed to publish. Never raised for a missing OPTIONAL
    destination's credentials -- that is a skip, not an error. Raised only for
    an actual attempted-and-failed publish, or a REQUIRED destination that
    cannot be reached at all."""


# The full set of valid `artifacts` entries in outputs.toml. Kept as one
# canonical dict (name -> description) rather than scattering the list across
# each destination class, so `deployments.toml`/`outputs.toml`'s "artifacts ="
# lines have exactly one place that can typo-check them, and one place that
# documents what each name means -- see CONFIG.md, which is generated from
# this by hand and must be kept in sync if this changes.
KNOWN_ARTIFACTS: dict[str, str] = {
    "manifest": "manifest.json -- sha256 of every other artifact in the run",
    "summary": "summary.json -- aggregate counts (conformance.report.summary_dict)",
    "results": "results.jsonl -- one line per trial, the full evidence stream",
    "report": "report.md -- the rendered declared-vs-actual report",
    "raw_evidence": "evidence/trial-*.json -- results.jsonl exploded to one file "
                    "per trial; the largest and most sensitive artifact, since it "
                    "carries the full request/response bodies",
}


class OutputsConfigValidationError(ValueError):
    pass


# `results.jsonl` embeds the full HttpExchange (request/response bodies,
# headers) on every line -- it is the same evidence as `raw_evidence`, just
# grouped one-file-per-run instead of one-file-per-trial rather than
# something lighter. So the two are equally sensitive, and a "public"
# destination must refuse BOTH, not just raw_evidence.
PUBLIC_SAFE_ARTIFACTS = frozenset({"manifest", "summary", "report"})

VISIBILITY_VALUES = frozenset({"private", "public"})


def validate_artifacts(names: Any, *, where: str, visibility: str = "private") -> tuple[str, ...]:
    """Check an `artifacts = [...]` list against KNOWN_ARTIFACTS.

    Fails loudly on a typo rather than silently publishing nothing for it --
    a destination configured for "raw_evidance" (missing an e) would otherwise
    just quietly never receive any files, and nothing would say why. That
    silent-misconfiguration failure mode is exactly what this project exists
    to catch in OTHER people's systems; it would be a bad look to have it in
    our own config loader.

    `"all"` is a recognized shorthand for every known artifact, so a config
    does not need to spell out the full list and re-break every time a new
    artifact type is added.

    `visibility="public"` additionally refuses `results` and `raw_evidence`:
    both carry full request/response bodies, and a destination meant to be
    world-readable must not be handed that by a one-line typo in this list.
    """
    if names is None:
        names = list(KNOWN_ARTIFACTS)
    elif list(names) == ["all"]:
        names = list(KNOWN_ARTIFACTS)

    unknown = [n for n in names if n not in KNOWN_ARTIFACTS]
    if unknown:
        raise OutputsConfigValidationError(
            f"{where}: unknown artifact name(s) {unknown!r}. "
            f"Valid values: {sorted(KNOWN_ARTIFACTS)} (or \"all\" for every artifact)"
        )

    if visibility == "public":
        unsafe = [n for n in names if n not in PUBLIC_SAFE_ARTIFACTS]
        if unsafe:
            raise OutputsConfigValidationError(
                f"{where}: visibility=\"public\" but artifacts include {unsafe!r}, "
                f"which carry full request/response bodies. A public destination "
                f"may only receive {sorted(PUBLIC_SAFE_ARTIFACTS)}. Set "
                f"visibility=\"private\" if this destination is not actually public."
            )

    return tuple(names)


def _validate_visibility(cfg: dict[str, Any], *, where: str) -> str:
    v = cfg.get("visibility", "private")
    if v not in VISIBILITY_VALUES:
        raise OutputsConfigValidationError(
            f"{where}: visibility={v!r} is not valid. "
            f"Valid values: {sorted(VISIBILITY_VALUES)}"
        )
    return v


@dataclass
class PublishResult:
    destination_id: str
    ok: bool
    detail: str
    artifacts_written: list[str] = field(default_factory=list)


class OutputDestination:
    """One place a run's artifacts can go."""

    id: str = ""
    required: bool = False
    artifacts: tuple[str, ...] = ()
    visibility: str = "private"

    def available(self) -> tuple[bool, str]:
        """(True, "") if this destination is configured and reachable enough to
        attempt a publish; (False, reason) otherwise. Never raises."""
        raise NotImplementedError

    def publish(self, bundle: Any) -> PublishResult:
        raise NotImplementedError

    def wants(self, artifact_name: str) -> bool:
        return artifact_name in self.artifacts


class FilesystemOutput(OutputDestination):
    """Writes the bundle to a local directory tree. Always required in
    practice -- this is what makes every other destination a retryable
    publish rather than the only copy of the evidence."""

    def __init__(self, cfg: dict[str, Any]):
        self.id = cfg.get("id", "local")
        self.required = bool(cfg.get("required", True))
        self.visibility = _validate_visibility(cfg, where=f"outputs.toml [{self.id}]")
        self.artifacts = validate_artifacts(
            cfg.get("artifacts"), where=f"outputs.toml [{self.id}]", visibility=self.visibility
        )
        self.path = cfg.get("path", "./runs")

    def available(self) -> tuple[bool, str]:
        try:
            os.makedirs(self.path, exist_ok=True)
            return True, ""
        except OSError as exc:
            return False, f"cannot create {self.path}: {exc}"

    def publish(self, bundle: Any) -> PublishResult:
        # The bundle already wrote itself to bundle.run_dir during the probe
        # run (see runbundle.RunBundle) -- filesystem publish for the SAME
        # root just confirms the files are present. A filesystem output at a
        # DIFFERENT path (e.g. a mounted backup volume) actually copies.
        written = []
        target_root = os.path.join(self.path, bundle.date_dir, bundle.run_id)
        same_root = os.path.abspath(target_root) == os.path.abspath(bundle.run_dir)
        for name in self.artifacts:
            for src in bundle.artifact_files(name):
                if same_root:
                    written.append(os.path.relpath(src, bundle.run_dir))
                    continue
                dst = os.path.join(target_root, os.path.relpath(src, bundle.run_dir))
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                with open(src, "rb") as fsrc, open(dst, "wb") as fdst:
                    fdst.write(fsrc.read())
                written.append(os.path.relpath(dst, target_root))
        return PublishResult(self.id, True, f"{len(written)} artifact(s) at {target_root}", written)


class S3Output(OutputDestination):
    """S3-compatible object storage: AWS S3, Cloudflare R2, MinIO, and
    anything else speaking the same signed-REST API.

    Config references environment variable NAMES, never literal credentials --
    see outputs.toml. A missing env var makes this destination unavailable
    (skipped, not a hard failure) unless `required = true`.
    """

    def __init__(self, cfg: dict[str, Any]):
        self.id = cfg.get("id", "s3")
        self.required = bool(cfg.get("required", False))
        self.visibility = _validate_visibility(cfg, where=f"outputs.toml [{self.id}]")
        self.artifacts = validate_artifacts(
            cfg.get("artifacts") or ["manifest", "summary", "results", "report"],
            where=f"outputs.toml [{self.id}]", visibility=self.visibility,
        )
        self.prefix = cfg.get("prefix", "")
        self._endpoint = os.environ.get(cfg.get("endpoint_env", ""), "")
        self._region = os.environ.get(cfg.get("region_env", ""), "") or "auto"
        self._bucket = os.environ.get(cfg.get("bucket_env", ""), "")
        self._access_key = os.environ.get(cfg.get("access_key_id_env", ""), "")
        self._secret_key = os.environ.get(cfg.get("secret_access_key_env", ""), "")

    def available(self) -> tuple[bool, str]:
        missing = [n for n, v in (
            ("endpoint", self._endpoint), ("bucket", self._bucket),
            ("access key", self._access_key), ("secret key", self._secret_key),
        ) if not v]
        if missing:
            return False, f"missing: {', '.join(missing)}"
        return True, ""

    def publish(self, bundle: Any) -> PublishResult:
        ok, reason = self.available()
        if not ok:
            if self.required:
                raise PublishError(f"{self.id}: required destination unavailable ({reason})")
            return PublishResult(self.id, False, f"skipped: {reason}")

        written = []
        for name in self.artifacts:
            for src in bundle.artifact_files(name):
                key = f"{self.prefix}{bundle.date_dir}/{bundle.run_id}/{os.path.relpath(src, bundle.run_dir)}"
                with open(src, "rb") as fh:
                    data = fh.read()
                try:
                    self._put(key, data)
                except (urllib.error.URLError, PublishError) as exc:
                    if self.required:
                        raise PublishError(f"{self.id}: failed to publish {key}: {exc}") from exc
                    return PublishResult(self.id, False, f"failed on {key}: {exc}", written)
                written.append(key)
        return PublishResult(self.id, True, f"{len(written)} object(s) in s3://{self._bucket}/{self.prefix}", written)

    # ── AWS SigV4, stdlib only ───────────────────────────────────────────────

    def _put(self, key: str, data: bytes) -> None:
        url, headers = self._sign_put(key, data)
        req = urllib.request.Request(url, data=data, headers=headers, method="PUT")
        with urllib.request.urlopen(req, timeout=30) as resp:
            if resp.status not in (200, 201):
                raise PublishError(f"unexpected status {resp.status} for {key}")

    def _sign_put(self, key: str, data: bytes) -> tuple[str, dict[str, str]]:
        host = urllib.parse.urlparse(self._endpoint).netloc or self._endpoint
        scheme = urllib.parse.urlparse(self._endpoint).scheme or "https"
        path = f"/{self._bucket}/{urllib.parse.quote(key)}"
        url = f"{scheme}://{host}{path}"

        now = _dt.datetime.now(_dt.timezone.utc)
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        date_stamp = now.strftime("%Y%m%d")
        payload_hash = hashlib.sha256(data).hexdigest()

        headers = {
            "host": host,
            "x-amz-content-sha256": payload_hash,
            "x-amz-date": amz_date,
            "content-type": "application/octet-stream",
        }
        signed_headers = ";".join(sorted(headers))
        canonical_headers = "".join(f"{k}:{v}\n" for k, v in sorted(headers.items()))
        canonical_request = "\n".join([
            "PUT", path, "", canonical_headers, signed_headers, payload_hash,
        ])

        credential_scope = f"{date_stamp}/{self._region}/s3/aws4_request"
        string_to_sign = "\n".join([
            "AWS4-HMAC-SHA256", amz_date, credential_scope,
            hashlib.sha256(canonical_request.encode()).hexdigest(),
        ])

        def _hmac(key: bytes, msg: str) -> bytes:
            return hmac.new(key, msg.encode(), hashlib.sha256).digest()

        k_date = _hmac(f"AWS4{self._secret_key}".encode(), date_stamp)
        k_region = _hmac(k_date, self._region)
        k_service = _hmac(k_region, "s3")
        k_signing = _hmac(k_service, "aws4_request")
        signature = hmac.new(k_signing, string_to_sign.encode(), hashlib.sha256).hexdigest()

        auth = (
            f"AWS4-HMAC-SHA256 Credential={self._access_key}/{credential_scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        )
        headers["Authorization"] = auth
        return url, headers


DESTINATION_TYPES: dict[str, type[OutputDestination]] = {
    "filesystem": FilesystemOutput,
    "s3": S3Output,
}


def build_destinations(cfg: dict[str, Any]) -> list[OutputDestination]:
    out = []
    for entry in cfg.get("outputs") or []:
        if not entry.get("enabled", True):
            continue
        cls = DESTINATION_TYPES.get(entry.get("type", ""))
        if cls is None:
            raise ValueError(
                f"outputs.toml [{entry.get('id')}]: unknown type {entry.get('type')!r}. "
                f"Valid values: {sorted(DESTINATION_TYPES)}"
            )
        out.append(cls(entry))
    return out

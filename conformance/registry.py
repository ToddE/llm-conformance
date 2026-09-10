"""models.dev — the declared side of the comparison.

We pin a local snapshot rather than fetching live on every run. The registry
changes; a finding that says "declared true, observed false" is meaningless
unless you can show WHAT the registry said AT THE TIME OF THE PROBE. The
snapshot is committed alongside results so the comparison stays auditable
after models.dev updates.
"""

from __future__ import annotations

import json
import os
import urllib.request
from typing import Any

from .evidence import sha256_hex, utc_now_iso

API_URL = "https://models.dev/api.json"
SNAPSHOT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "registry")
SNAPSHOT_PATH = os.path.join(SNAPSHOT_DIR, "models.dev.snapshot.json")
META_PATH = os.path.join(SNAPSHOT_DIR, "models.dev.snapshot.meta.json")

# models.dev capability field -> our capability id
CAPABILITY_FIELD = {
    "structured_output": "structured_output",
    "tool_calling": "tool_call",
}


def fetch_snapshot(force: bool = False) -> dict[str, Any]:
    """Download and pin the registry. Reuses the local snapshot unless forced."""
    if os.path.exists(SNAPSHOT_PATH) and not force:
        with open(SNAPSHOT_PATH, encoding="utf-8") as fh:
            return json.load(fh)

    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    req = urllib.request.Request(API_URL, headers={"User-Agent": "llm-conformance/0.1"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        raw = resp.read().decode("utf-8")

    with open(SNAPSHOT_PATH, "w", encoding="utf-8") as fh:
        fh.write(raw)
    meta = {
        "source": API_URL,
        "fetched_at": utc_now_iso(),
        "sha256": sha256_hex(raw),
        "bytes": len(raw.encode("utf-8")),
    }
    with open(META_PATH, "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=2)
    return json.loads(raw)


def snapshot_meta() -> dict[str, Any]:
    if os.path.exists(META_PATH):
        with open(META_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    return {}


class Registry:
    def __init__(self, data: dict[str, Any]):
        self.data = data

    @classmethod
    def load(cls, force_refresh: bool = False) -> "Registry":
        return cls(fetch_snapshot(force=force_refresh))

    def model(self, provider: str, model_id: str) -> dict[str, Any] | None:
        return self.data.get(provider, {}).get("models", {}).get(model_id)

    def declares(self, provider: str, model_id: str, capability: str) -> bool | None:
        """What the registry claims. None = no claim on record.

        None is distinct from False on purpose: "the registry is silent" and
        "the registry says no" are different situations, and only the second
        one can be contradicted.
        """
        entry = self.model(provider, model_id)
        if entry is None:
            return None
        field = CAPABILITY_FIELD.get(capability)
        if field is None:
            return None
        return entry.get(field)

    def source_ref(self, provider: str, model_id: str) -> str:
        meta = snapshot_meta()
        stamp = meta.get("fetched_at", "unknown")
        digest = meta.get("sha256", "")[:12]
        return f"models.dev/api.json@{stamp} sha256:{digest} [{provider}.{model_id}]"

    def models_for(self, provider: str) -> dict[str, Any]:
        return self.data.get(provider, {}).get("models", {})

    def resolve_probe_models(self, provider: str, limit: int) -> list[str]:
        """Pick models to probe when a target doesn't name them explicitly.

        Prefers models declaring BOTH capabilities and sorts by recency --
        a divergence on a current, widely-used model matters more than one on
        a deprecated snapshot nobody deploys.
        """
        models = self.models_for(provider)
        both = [
            (mid, entry)
            for mid, entry in models.items()
            if entry.get("structured_output") and entry.get("tool_call")
        ]
        both.sort(
            key=lambda kv: (kv[1].get("last_updated") or kv[1].get("release_date") or ""),
            reverse=True,
        )
        return [mid for mid, _ in both[:limit]]

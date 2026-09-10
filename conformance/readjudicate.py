"""Re-classify stored evidence under the current taxonomy.

The evidence bundle stores the RAW response body, so adjudication is a pure
function of stored data -- it can be re-run at any time without touching a
provider or spending anything.

This matters more than it first appears:

* When the taxonomy improves (as it did when `unrepresentable` and
  `fail_advisory_only` were split out of `silent`), every historical run can be
  re-scored on the same footing. Otherwise old and new numbers are not
  comparable and the drift archive -- the actual long-term asset here -- is
  worthless.
* It lets a third party dispute a CLASSIFICATION without disputing the
  measurement. They can re-run adjudication over our published evidence with
  their own rules and see exactly where they'd disagree.
* It keeps a taxonomy bugfix from costing another round of API spend.

The raw response is never modified. Only the derived fields are recomputed.
"""

from __future__ import annotations

import json
from typing import Any

from . import outcomes as O
from .adapters.google import GoogleAdapter
from .catalog import WIRE_FORMATS, build_adapters
from .evidence import HttpExchange
from .probes.definitions import ALL_PROBES


def _adapter_for(row: dict[str, Any]):
    """Resolve the adapter that produced a stored row.

    Resolution order matters for historical bundles: prefer the catalog entry
    (current config), fall back to the recorded wire format. A bundle written
    before a provider was renamed still re-scores correctly as long as its
    recorded `adapter` names a known wire.
    """
    dep = row.get("deployment") or {}
    provider = dep.get("provider", row["provider"])
    wire = dep.get("adapter", "")

    adapters, _ = build_adapters(include_disabled=True)
    cls = adapters.get(provider)

    if cls is None or (wire and getattr(cls, "adapter_id", None) != wire):
        # Legacy spelling, or a provider no longer in the catalog.
        cls = WIRE_FORMATS.get(wire) or WIRE_FORMATS.get(
            {"openai-compatible": "openai", "anthropic": "anthropic",
             "google": "gemini"}.get(wire, ""), cls)

    return cls("readjudicate-no-key") if cls else None


def readjudicate_row(row: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of the row with derived fields recomputed."""
    out = dict(row)
    if row["outcome"] == O.SKIPPED_NO_CREDENTIAL or not row.get("exchange"):
        return out

    adapter = _adapter_for(row)
    if adapter is None:
        return out

    ex = row["exchange"]
    exchange = HttpExchange(
        url=ex["url"], method=ex["method"],
        request_headers=ex["request_headers"], request_body=ex["request_body"],
        request_body_sha256=ex["request_body_sha256"], status=ex["status"],
        response_headers=ex["response_headers"], response_body=ex["response_body"],
        response_body_sha256=ex["response_body_sha256"],
        provider_request_id=ex["provider_request_id"],
        started_at=ex["started_at"], finished_at=ex["finished_at"],
        duration_ms=ex["duration_ms"], transport_error=ex.get("transport_error"),
    )

    probe = ALL_PROBES[row["capability"]]
    # Gemini's dialect attribution depends on what translation dropped;
    # recompute it from the probe rather than trusting the stored note.
    if isinstance(adapter, GoogleAdapter):
        from .adapters.google import to_gemini_schema
        schema = (probe.schema if row["capability"] == "structured_output"
                  else probe.tool_schema)
        _, adapter.dropped_assertions = to_gemini_schema(schema)

    interp = (adapter.interpret_structured_output(exchange, probe)
              if row["capability"] == "structured_output"
              else adapter.interpret_tool_calling(exchange, probe))

    divergence, conforms = O.classify_divergence(row["declared"], interp.outcome)
    out.update({
        "outcome": interp.outcome,
        "detail": interp.detail,
        "violations": interp.violations,
        "divergence": divergence,
        "conforms": conforms,
        "readjudicated": True,
        "original_outcome": row["outcome"],
        "original_divergence": row["divergence"],
    })
    return out


def readjudicate_file(path: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Returns (rows, changes) where changes lists reclassified rows."""
    rows, changes = [], []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            original = json.loads(line)
            new = readjudicate_row(original)
            rows.append(new)
            if new.get("outcome") != original["outcome"]:
                changes.append({
                    "model": new["model_requested"],
                    "capability": new["capability"],
                    "trial": new.get("trial_index"),
                    "from": original["outcome"],
                    "to": new["outcome"],
                })
    return rows, changes

"""Adapter interface + shared response interpretation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .. import outcomes as O
from ..evidence import HttpExchange
from ..jsonschema import all_advisory, tier, validate
from ..probes.definitions import StructuredOutputProbe, ToolCallingProbe


@dataclass
class Interpretation:
    outcome: str
    detail: str
    violations: list[str]
    model_reported: str | None = None


# Error text that indicates the endpoint rejected our SCHEMA DIALECT rather
# than the capability itself. This distinction is a headline finding: it is
# the proof that `structured_output: true` is the wrong data model, because
# support is a dialect with an unpublished subset, not a boolean.
_DIALECT_MARKERS = (
    "additionalproperties",
    "unknown name",
    "unsupported field",
    "invalid schema",
    "schema is invalid",
    "not supported in schema",
    "required is required",
    "must be an array of the keys",
    "unsupported json schema",
    "proto field is not repeating",
    "cannot find field",
    "$schema",
    "format is not supported",
)

# Error text indicating the capability itself is unavailable for this model.
_CAPABILITY_MARKERS = (
    "response_format",
    "json_schema",
    "structured output",
    "tool_choice",
    "does not support tool",
    "tools are not supported",
    "function calling is not supported",
    "not supported with this model",
    "unsupported_value",
    "unsupported parameter",
    "is not supported",
)


def classify_error_response(exchange: HttpExchange, capability: str) -> Interpretation:
    """Turn a non-2xx response into an outcome.

    Order matters. A 400 that names our schema is a dialect problem; a 400
    that names the capability is an honest rejection; anything else is an
    error we refuse to interpret as a capability signal.
    """
    status = exchange.status
    body = (exchange.response_body or "").lower()

    if exchange.transport_error:
        return Interpretation(O.ERROR_TRANSPORT, exchange.transport_error, [])

    if status in (401, 403):
        return Interpretation(
            O.ERROR_AUTH,
            f"HTTP {status}: credential rejected -- check whether the key has "
            f"expired or been rotated (python3 run.py credentials)", [])
    if status == 429:
        return Interpretation(O.ERROR_RATE_LIMIT, "HTTP 429: rate limited after retries", [])
    if status == 404 or "model_not_found" in body or "does not exist" in body:
        return Interpretation(
            O.ERROR_NOT_FOUND,
            f"HTTP {status}: model not reachable with this credential",
            [],
        )
    if status is not None and status >= 500:
        return Interpretation(O.ERROR_TRANSPORT, f"HTTP {status}: server error", [])

    if status is not None and 400 <= status < 500:
        snippet = _error_message(exchange) or f"HTTP {status}"
        low = snippet.lower()
        if any(m in low for m in _DIALECT_MARKERS):
            return Interpretation(
                O.UNSUPPORTED_DIALECT,
                f"HTTP {status}: schema dialect rejected -- {snippet[:300]}",
                [snippet[:500]],
            )
        if any(m in low for m in _CAPABILITY_MARKERS):
            return Interpretation(
                O.REJECTED,
                f"HTTP {status}: capability refused -- {snippet[:300]}",
                [snippet[:500]],
            )
        # A 4xx we can't attribute. Do NOT score it as a capability failure.
        return Interpretation(
            O.ERROR_TRANSPORT, f"HTTP {status}: unattributed client error -- {snippet[:300]}", []
        )

    return Interpretation(O.ERROR_TRANSPORT, f"HTTP {status}: unexpected", [])


def _error_message(exchange: HttpExchange) -> str:
    try:
        data = json.loads(exchange.response_body)
    except Exception:
        return (exchange.response_body or "")[:500]
    if isinstance(data, list) and data:
        data = data[0]
    if isinstance(data, dict):
        err = data.get("error", data)
        if isinstance(err, dict):
            return str(err.get("message") or err.get("code") or json.dumps(err)[:500])
        return str(err)
    return str(data)[:500]


def check_structured_payload(
    text: str, probe: StructuredOutputProbe, model_reported: str | None
) -> Interpretation:
    """Shared adjudication: does the returned text satisfy the probe schema?"""
    stripped = (text or "").strip()
    if not stripped:
        return Interpretation(
            O.FAIL_UNPARSEABLE, "empty response body content", [], model_reported
        )

    try:
        parsed = json.loads(stripped)
    except (json.JSONDecodeError, ValueError) as exc:
        return Interpretation(
            O.FAIL_UNPARSEABLE,
            f"response is not valid JSON ({exc}); first 200 chars: {stripped[:200]!r}",
            [f"not-json: {stripped[:300]}"],
            model_reported,
        )

    violations = validate(parsed, probe.schema)
    if violations:
        if all_advisory(violations):
            return Interpretation(
                O.FAIL_ADVISORY_ONLY,
                f"structural constraints (type/enum/required/additionalProperties) "
                f"ALL honored; {len(violations)} advisory-keyword violation(s) only "
                f"-- not scored as a silent failure",
                violations,
                model_reported,
            )
        structural = [v for v in violations if tier(v) == "structural"]
        return Interpretation(
            O.FAIL_VIOLATED,
            f"valid JSON but {len(structural)} STRUCTURAL schema violation(s)"
            + (f" (plus {len(violations)-len(structural)} advisory)"
               if len(structural) != len(violations) else ""),
            violations,
            model_reported,
        )
    return Interpretation(O.PASS, "response conforms to the strict schema", [], model_reported)


def check_tool_arguments(
    args: Any, probe: ToolCallingProbe, model_reported: str | None, tool_name: str
) -> Interpretation:
    """A tool call came back. Do its arguments satisfy the tool's own schema?"""
    if isinstance(args, str):
        try:
            args = json.loads(args) if args.strip() else {}
        except (json.JSONDecodeError, ValueError) as exc:
            return Interpretation(
                O.FAIL_VIOLATED,
                f"tool call returned but arguments are not valid JSON ({exc})",
                [f"not-json-args: {str(args)[:300]}"],
                model_reported,
            )

    violations = validate(args, probe.tool_schema)
    if violations:
        if all_advisory(violations):
            return Interpretation(
                O.FAIL_ADVISORY_ONLY,
                f"tool call '{tool_name}' returned; structural constraints honored, "
                f"advisory-keyword violation(s) only -- not a silent failure",
                violations,
                model_reported,
            )
        return Interpretation(
            O.FAIL_VIOLATED,
            f"tool call '{tool_name}' returned but arguments violate its input schema",
            violations,
            model_reported,
        )
    return Interpretation(
        O.PASS, f"tool call '{tool_name}' returned with schema-valid arguments", [], model_reported
    )


class Adapter:
    """One provider surface."""

    name: str = ""
    env_vars: tuple[str, ...] = ()
    registry_id: str = ""
    so_mechanism: str = ""
    tc_mechanism: str = ""

    def __init__(self, api_key: str):
        self.api_key = api_key

    def structured_output_request(self, model: str, probe: StructuredOutputProbe):
        raise NotImplementedError

    def tool_calling_request(self, model: str, probe: ToolCallingProbe):
        raise NotImplementedError

    def interpret_structured_output(self, exchange, probe) -> Interpretation:
        raise NotImplementedError

    def interpret_tool_calling(self, exchange, probe) -> Interpretation:
        raise NotImplementedError

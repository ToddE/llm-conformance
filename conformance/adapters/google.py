"""Google Gemini (Generative Language API / AI Studio surface).

DIALECT NOTE -- this is the most interesting adapter in the project.

Gemini's `responseSchema` accepts a subset of OpenAPI Schema. It does NOT
accept `additionalProperties`. Sending our probe schema verbatim yields a 400
that tells us nothing about enforcement.

So we TRANSLATE the schema into Gemini's dialect and record exactly which
assertions had to be dropped (`dropped_assertions`). Then -- and this is the
important part -- we still validate the response against the FULL original
schema.

That combination produces the finding this whole project is arguably about:

    The constraint was not violated. The constraint was INEXPRESSIBLE.

`structured_output: true` in a registry cannot represent that. A team that
ports a strict schema from OpenAI to Gemini silently loses
additionalProperties enforcement, gets no error, and finds out in production.
That is a materially different fact from "supported / not supported", and no
declared registry captures it.
"""

from __future__ import annotations

import copy
from typing import Any

from .. import outcomes as O
from ..http import parse_json_body
from ..probes.definitions import StructuredOutputProbe, ToolCallingProbe
from .base import (
    Adapter,
    Interpretation,
    check_structured_payload,
    check_tool_arguments,
    classify_error_response,
)

# Keywords Gemini's responseSchema dialect does not accept.
_UNSUPPORTED_KEYWORDS = ("additionalProperties", "$schema", "const")


def to_gemini_schema(schema: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Translate to Gemini's dialect; report every assertion lost in transit."""
    dropped: list[str] = []

    def _walk(node: Any, path: str) -> Any:
        if isinstance(node, list):
            return [_walk(v, f"{path}[{i}]") for i, v in enumerate(node)]
        if not isinstance(node, dict):
            return node
        out: dict[str, Any] = {}
        for key, value in node.items():
            if key in _UNSUPPORTED_KEYWORDS:
                dropped.append(f"{path}.{key}={value!r}")
                continue
            if key == "properties" and isinstance(value, dict):
                out[key] = {k: _walk(v, f"{path}.{k}") for k, v in value.items()}
            else:
                out[key] = _walk(value, f"{path}.{key}")
        return out

    return _walk(copy.deepcopy(schema), "$"), dropped


class GoogleAdapter(Adapter):
    base_url = "https://generativelanguage.googleapis.com/v1beta"
    name = "google"
    registry_id = "google"
    env_vars = ("GEMINI_API_KEY", "GOOGLE_GENERATIVE_AI_API_KEY", "GOOGLE_API_KEY")
    so_mechanism = "generationConfig.responseSchema (OpenAPI subset, translated)"
    tc_mechanism = "toolConfig.functionCallingConfig.mode=ANY"
    max_tokens_value = 2048

    def __init__(self, api_key: str):
        super().__init__(api_key)
        self.dropped_assertions: list[str] = []

    def _headers(self) -> dict[str, str]:
        return {"x-goog-api-key": self.api_key}

    def _url(self, model: str) -> str:
        return f"{self.base_url}/models/{model}:generateContent"

    def structured_output_request(self, model: str, probe: StructuredOutputProbe):
        gemini_schema, dropped = to_gemini_schema(probe.schema)
        self.dropped_assertions = dropped
        payload = {
            "systemInstruction": {"parts": [{"text": probe.system}]},
            "contents": [{"role": "user", "parts": [{"text": probe.user}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseSchema": gemini_schema,
                "maxOutputTokens": self.max_tokens_value,
            },
        }
        return self._url(model), self._headers(), payload

    def tool_calling_request(self, model: str, probe: ToolCallingProbe):
        tool_schema, dropped = to_gemini_schema(probe.tool_schema)
        self.dropped_assertions = dropped
        payload = {
            "systemInstruction": {"parts": [{"text": probe.system}]},
            "contents": [{"role": "user", "parts": [{"text": probe.user}]}],
            "tools": [
                {
                    "functionDeclarations": [
                        {
                            "name": probe.tool_name,
                            "description": probe.tool_description,
                            "parameters": tool_schema,
                        }
                    ]
                }
            ],
            "toolConfig": {"functionCallingConfig": {"mode": "ANY"}},
            "generationConfig": {"maxOutputTokens": self.max_tokens_value},
        }
        return self._url(model), self._headers(), payload

    # ── interpretation ──────────────────────────────────────────────────────

    def _candidate(self, exchange):
        data = parse_json_body(exchange)
        if not isinstance(data, dict):
            return None, None, None
        model_reported = data.get("modelVersion")
        candidates = data.get("candidates") or []
        return data, model_reported, (candidates[0] if candidates else None)

    def _parts(self, candidate) -> list[dict[str, Any]]:
        return ((candidate or {}).get("content") or {}).get("parts") or []

    def interpret_structured_output(self, exchange, probe) -> Interpretation:
        if exchange.status != 200:
            return classify_error_response(exchange, probe.capability)

        data, model_reported, candidate = self._candidate(exchange)
        if candidate is None:
            return Interpretation(
                O.ERROR_TRANSPORT, "200 OK but no candidates returned", [], model_reported
            )

        if candidate.get("finishReason") == "MAX_TOKENS":
            return Interpretation(
                O.ERROR_TRUNCATED,
                "output truncated at maxOutputTokens -- inconclusive, not scored",
                [],
                model_reported,
            )
        if candidate.get("finishReason") in ("SAFETY", "RECITATION", "PROHIBITED_CONTENT"):
            return Interpretation(
                O.ERROR_TRANSPORT,
                f"blocked by {candidate.get('finishReason')} filter (not a capability signal)",
                [],
                model_reported,
            )

        text = "".join(p.get("text", "") for p in self._parts(candidate))
        result = check_structured_payload(text, probe, model_reported)

        if result.outcome == O.FAIL_VIOLATED and self.dropped_assertions:
            result = self._attribute_to_dialect(result)
        return result

    def _attribute_to_dialect(self, result: Interpretation) -> Interpretation:
        """Separate violations we never asked for from violations we did.

        A constraint dropped in translation was never sent, so the endpoint
        cannot be said to have ignored it. But if the response ALSO breaks a
        constraint that WAS sent, that part is a genuine silent failure and
        must stay counted as one. Only a response whose every violation is
        attributable to the dialect gap gets reclassified.
        """
        keywords = {d.rsplit(".", 1)[-1].split("=")[0] for d in self.dropped_assertions}
        attributable, genuine = [], []
        for v in result.violations:
            (attributable if any(k in v for k in keywords) else genuine).append(v)

        if not attributable:
            return result

        note = (
            f"constraint(s) dropped in translation to Gemini's schema dialect "
            f"({'; '.join(self.dropped_assertions)}) -- the endpoint was never asked "
            f"to enforce them"
        )
        result.violations.append(f"dropped_from_request={self.dropped_assertions}")

        if genuine:
            # Mixed. The genuine violations decide the outcome; it stays silent.
            result.detail = (
                f"{len(genuine)} genuine schema violation(s) PLUS {len(attributable)} "
                f"attributable to the dialect gap. Counted as a silent failure on the "
                f"genuine violation(s). Note: {note}."
            )
            return result

        # Every violation traces to a constraint we could not send.
        result.outcome = O.UNREPRESENTABLE
        result.detail = (
            f"response violates the schema ONLY on {note}. Not a silent failure: "
            f"the endpoint honored every constraint it was actually given. The "
            f"constraint was unrepresentable, not ignored."
        )
        return result

    def interpret_tool_calling(self, exchange, probe) -> Interpretation:
        if exchange.status != 200:
            return classify_error_response(exchange, probe.capability)

        data, model_reported, candidate = self._candidate(exchange)
        if candidate is None:
            return Interpretation(
                O.ERROR_TRANSPORT, "200 OK but no candidates returned", [], model_reported
            )

        parts = self._parts(candidate)
        calls = [p["functionCall"] for p in parts if "functionCall" in p]

        if not calls:
            if candidate.get("finishReason") == "MAX_TOKENS":
                return Interpretation(
                    O.ERROR_TRUNCATED,
                    "output truncated before any function call -- inconclusive",
                    [],
                    model_reported,
                )
            text = "".join(p.get("text", "") for p in parts).strip()
            return Interpretation(
                O.FAIL_VIOLATED,
                "functionCallingConfig.mode=ANY was accepted but NO functionCall was "
                f"returned; model answered in text instead: {text[:200]!r}",
                [f"finishReason={candidate.get('finishReason')}", f"text_response={text[:300]}"],
                model_reported,
            )

        call = calls[0]
        return check_tool_arguments(
            call.get("args", {}), probe, model_reported, call.get("name", "?")
        )

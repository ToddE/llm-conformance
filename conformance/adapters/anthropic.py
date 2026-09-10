"""Anthropic Messages API.

MECHANISM NOTE -- important for honest comparison.

Anthropic's canonical route to guaranteed-shape output is a forced tool call
with an `input_schema`, not an OpenAI-style `response_format`. So the
structured-output probe here exercises a DIFFERENT mechanism than it does on
OpenAI.

That difference is recorded in every result's `mechanism` field, and the
report never averages across mechanisms without saying so. Comparing
"Anthropic structured output" to "OpenAI structured output" as if they were
the same test would be the kind of sloppiness that gets a finding dismissed.
"""

from __future__ import annotations

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

ANTHROPIC_VERSION = "2023-06-01"


class AnthropicAdapter(Adapter):
    base_url = "https://api.anthropic.com/v1"
    name = "anthropic"
    registry_id = "anthropic"
    env_vars = ("ANTHROPIC_API_KEY",)
    so_mechanism = "forced tool with input_schema (tool_choice={type:tool})"
    tc_mechanism = "tool_choice={type:any}"
    max_tokens_value = 2048

    def _headers(self) -> dict[str, str]:
        return {
            "x-api-key": self.api_key,
            "anthropic-version": ANTHROPIC_VERSION,
        }

    def structured_output_request(self, model: str, probe: StructuredOutputProbe):
        payload = {
            "model": model,
            "max_tokens": self.max_tokens_value,
            "system": probe.system,
            "messages": [{"role": "user", "content": probe.user}],
            "tools": [
                {
                    "name": probe.schema_name,
                    "description": "Emit the assessment as structured data.",
                    "input_schema": probe.schema,
                }
            ],
            "tool_choice": {"type": "tool", "name": probe.schema_name},
        }
        return f"{self.base_url}/messages", self._headers(), payload

    def tool_calling_request(self, model: str, probe: ToolCallingProbe):
        payload = {
            "model": model,
            "max_tokens": self.max_tokens_value,
            "system": probe.system,
            "messages": [{"role": "user", "content": probe.user}],
            "tools": [
                {
                    "name": probe.tool_name,
                    "description": probe.tool_description,
                    "input_schema": probe.tool_schema,
                }
            ],
            "tool_choice": {"type": "any"},
        }
        return f"{self.base_url}/messages", self._headers(), payload

    # ── interpretation ──────────────────────────────────────────────────────

    def _blocks(self, exchange):
        data = parse_json_body(exchange)
        if not isinstance(data, dict):
            return None, None, []
        return data, data.get("model"), (data.get("content") or [])

    def interpret_structured_output(self, exchange, probe) -> Interpretation:
        if exchange.status != 200:
            return classify_error_response(exchange, probe.capability)

        data, model_reported, blocks = self._blocks(exchange)
        if data is None:
            return Interpretation(O.ERROR_TRANSPORT, "200 OK but body is not JSON", [])

        if data.get("stop_reason") == "max_tokens":
            return Interpretation(
                O.ERROR_TRUNCATED,
                "output truncated at max_tokens -- inconclusive, not scored",
                [],
                model_reported,
            )

        tool_uses = [b for b in blocks if b.get("type") == "tool_use"]
        if not tool_uses:
            text = " ".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip()
            return Interpretation(
                O.FAIL_VIOLATED,
                "forced tool_choice was accepted but no tool_use block was returned; "
                f"model produced text instead: {text[:200]!r}",
                [f"stop_reason={data.get('stop_reason')}", f"text_response={text[:300]}"],
                model_reported,
            )

        # The tool input IS the structured payload -- validate it directly
        # rather than round-tripping through a JSON string.
        import json as _json

        return check_structured_payload(
            _json.dumps(tool_uses[0].get("input", {})), probe, model_reported
        )

    def interpret_tool_calling(self, exchange, probe) -> Interpretation:
        if exchange.status != 200:
            return classify_error_response(exchange, probe.capability)

        data, model_reported, blocks = self._blocks(exchange)
        if data is None:
            return Interpretation(O.ERROR_TRANSPORT, "200 OK but body is not JSON", [])

        tool_uses = [b for b in blocks if b.get("type") == "tool_use"]
        if not tool_uses:
            if data.get("stop_reason") == "max_tokens":
                return Interpretation(
                    O.ERROR_TRUNCATED,
                    "output truncated before any tool call -- inconclusive",
                    [],
                    model_reported,
                )
            text = " ".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip()
            return Interpretation(
                O.FAIL_VIOLATED,
                "tool_choice={type:any} was accepted but NO tool_use block was returned; "
                f"model answered in text instead: {text[:200]!r}",
                [f"stop_reason={data.get('stop_reason')}", f"text_response={text[:300]}"],
                model_reported,
            )

        call = tool_uses[0]
        return check_tool_arguments(
            call.get("input", {}), probe, model_reported, call.get("name", "?")
        )

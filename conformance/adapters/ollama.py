"""Ollama -- local runtime, reachable with no credentials.

Two adapters against the SAME model on the SAME machine:

* `OllamaOpenAIAdapter`  -> /v1/chat/completions  (OpenAI-compatible shim)
* `OllamaNativeAdapter`  -> /api/chat             (native)

Same weights, same hardware, same moment. Any behavioral difference between
them is attributable to the ADAPTER alone -- every other term in the
deployment tuple is held constant. That is a controlled experiment for P3
("capabilities belong to the deployment, not the model") that costs nothing
and needs no vendor account.

It is also the cleanest available test of what an "OpenAI-compatible" claim is
worth, since here we can see both sides of the shim.
"""

from __future__ import annotations

import json

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
from .openai_compatible import OpenAICompatibleAdapter

DEFAULT_HOST = "http://localhost:11434"


class OllamaOpenAIAdapter(OpenAICompatibleAdapter):
    """Ollama through its OpenAI-compatible surface."""

    base_url = f"{DEFAULT_HOST}/v1"
    name = "ollama"
    registry_id = "ollama-cloud"   # closest models.dev entry; often absent
    env_vars = ()
    adapter_id = "openai-compatible"
    max_tokens_field = "max_tokens"

    def _headers(self) -> dict[str, str]:
        return {}   # local, unauthenticated


class OllamaNativeAdapter(Adapter):
    """Ollama's own /api/chat surface.

    Structured output is `format: <json schema>` at the top level. Note there
    is NO tool_choice equivalent in this API -- so the forced-call probe tests
    something real: does the runtime honor a constraint the protocol has no
    way to express? If it returns text, that is UNREPRESENTABLE (the protocol
    cannot state it), not a silent lie.
    """

    base_url = f"{DEFAULT_HOST}/api"
    name = "ollama"
    registry_id = "ollama-cloud"
    env_vars = ()
    adapter_id = "ollama-native"
    so_mechanism = "format=<json schema> (native)"
    tc_mechanism = "tools[] with NO tool_choice equivalent in this API"
    max_tokens_value = 2048

    def _headers(self) -> dict[str, str]:
        return {}

    def _base(self, model: str, system: str, user: str) -> dict:
        return {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "options": {"num_predict": self.max_tokens_value},
        }

    def structured_output_request(self, model: str, probe: StructuredOutputProbe):
        payload = self._base(model, probe.system, probe.user)
        payload["format"] = probe.schema
        return f"{self.base_url}/chat", self._headers(), payload

    def tool_calling_request(self, model: str, probe: ToolCallingProbe):
        payload = self._base(model, probe.system, probe.user)
        payload["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": probe.tool_name,
                    "description": probe.tool_description,
                    "parameters": probe.tool_schema,
                },
            }
        ]
        return f"{self.base_url}/chat", self._headers(), payload

    # ── interpretation ──────────────────────────────────────────────────────

    def interpret_structured_output(self, exchange, probe) -> Interpretation:
        if exchange.status != 200:
            return classify_error_response(exchange, probe.capability)
        data = parse_json_body(exchange)
        if not isinstance(data, dict):
            return Interpretation(O.ERROR_TRANSPORT, "200 OK but body is not JSON", [])
        model_reported = data.get("model")
        if data.get("done_reason") == "length":
            return Interpretation(
                O.ERROR_TRUNCATED, "output truncated at num_predict -- inconclusive",
                [], model_reported)
        content = (data.get("message") or {}).get("content") or ""
        return check_structured_payload(content, probe, model_reported)

    def interpret_tool_calling(self, exchange, probe) -> Interpretation:
        if exchange.status != 200:
            return classify_error_response(exchange, probe.capability)
        data = parse_json_body(exchange)
        if not isinstance(data, dict):
            return Interpretation(O.ERROR_TRANSPORT, "200 OK but body is not JSON", [])
        model_reported = data.get("model")
        message = data.get("message") or {}
        calls = message.get("tool_calls") or []

        if not calls:
            if data.get("done_reason") == "length":
                return Interpretation(
                    O.ERROR_TRUNCATED, "output truncated before any tool call", [],
                    model_reported)
            text = (message.get("content") or "").strip()
            # The native API has no way to REQUIRE a call. Nothing was ignored,
            # because nothing could be asked. That is unrepresentable, not silent.
            return Interpretation(
                O.UNREPRESENTABLE,
                "no tool call returned -- but /api/chat has no tool_choice "
                "equivalent, so a forced call is UNREPRESENTABLE in this protocol. "
                f"Model answered in text: {text[:160]!r}",
                [f"done_reason={data.get('done_reason')}",
                 "protocol_gap: no tool_choice field exists in ollama /api/chat",
                 f"text_response={text[:300]}"],
                model_reported,
            )

        fn = calls[0].get("function") or {}
        return check_tool_arguments(
            fn.get("arguments", {}), probe, model_reported, fn.get("name", "?"))

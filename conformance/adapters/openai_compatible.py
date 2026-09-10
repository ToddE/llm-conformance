"""OpenAI Chat Completions surface. Covers OpenAI and DeepSeek.

DeepSeek advertises OpenAI compatibility. Whether that compatibility extends
to *enforcement* -- as opposed to merely accepting the parameter -- is exactly
the kind of question this project exists to answer, so it gets the same probe
through the same code path.
"""

from __future__ import annotations

from .. import outcomes as O
from ..evidence import HttpExchange
from ..http import parse_json_body
from ..probes.definitions import StructuredOutputProbe, ToolCallingProbe
from .base import (
    Adapter,
    Interpretation,
    check_structured_payload,
    check_tool_arguments,
    classify_error_response,
)


class OpenAICompatibleAdapter(Adapter):
    base_url = "https://api.openai.com/v1"
    name = "openai"
    registry_id = "openai"
    env_vars = ("OPENAI_API_KEY",)
    so_mechanism = "response_format.json_schema (strict=true)"
    tc_mechanism = "tool_choice=required"
    # Newer OpenAI models reject `max_tokens` in favour of
    # `max_completion_tokens`. Sending the wrong one yields a 400 that has
    # nothing to do with the capability under test, so it is a per-provider
    # setting rather than a shared default.
    max_tokens_field = "max_completion_tokens"
    max_tokens_value = 2048

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"}

    def _messages(self, system: str, user: str) -> list[dict[str, str]]:
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]

    def structured_output_request(self, model: str, probe: StructuredOutputProbe):
        payload = {
            "model": model,
            "messages": self._messages(probe.system, probe.user),
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": probe.schema_name,
                    "strict": True,
                    "schema": probe.schema,
                },
            },
            self.max_tokens_field: self.max_tokens_value,
        }
        return f"{self.base_url}/chat/completions", self._headers(), payload

    def tool_calling_request(self, model: str, probe: ToolCallingProbe):
        payload = {
            "model": model,
            "messages": self._messages(probe.system, probe.user),
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": probe.tool_name,
                        "description": probe.tool_description,
                        "parameters": probe.tool_schema,
                        "strict": True,
                    },
                }
            ],
            "tool_choice": "required",
            self.max_tokens_field: self.max_tokens_value,
        }
        return f"{self.base_url}/chat/completions", self._headers(), payload

    # ── interpretation ──────────────────────────────────────────────────────

    def _first_choice(self, exchange: HttpExchange):
        data = parse_json_body(exchange)
        if not isinstance(data, dict):
            return None, None, None
        model_reported = data.get("model")
        choices = data.get("choices") or []
        if not choices:
            return data, model_reported, None
        return data, model_reported, choices[0]

    def interpret_structured_output(self, exchange, probe) -> Interpretation:
        if exchange.status != 200:
            return classify_error_response(exchange, probe.capability)

        data, model_reported, choice = self._first_choice(exchange)
        if choice is None:
            return Interpretation(
                O.ERROR_TRANSPORT, "200 OK but no choices in response", [], model_reported
            )

        if choice.get("finish_reason") == "length":
            return Interpretation(
                O.ERROR_TRUNCATED,
                "output truncated at token limit -- inconclusive, not scored",
                [],
                model_reported,
            )

        # A refusal is the model declining the task, which is orthogonal to
        # whether the endpoint enforces the schema. Not a conformance failure.
        message = choice.get("message") or {}
        if message.get("refusal"):
            return Interpretation(
                O.ERROR_TRANSPORT,
                f"model refused the task (not a capability signal): {message['refusal'][:200]}",
                [],
                model_reported,
            )

        return check_structured_payload(message.get("content") or "", probe, model_reported)

    def interpret_tool_calling(self, exchange, probe) -> Interpretation:
        if exchange.status != 200:
            return classify_error_response(exchange, probe.capability)

        data, model_reported, choice = self._first_choice(exchange)
        if choice is None:
            return Interpretation(
                O.ERROR_TRANSPORT, "200 OK but no choices in response", [], model_reported
            )

        message = choice.get("message") or {}
        tool_calls = message.get("tool_calls") or []

        if not tool_calls:
            if choice.get("finish_reason") == "length":
                return Interpretation(
                    O.ERROR_TRUNCATED,
                    "output truncated before any tool call -- inconclusive",
                    [],
                    model_reported,
                )
            text = (message.get("content") or "").strip()
            return Interpretation(
                O.FAIL_VIOLATED,
                "tool_choice=required was accepted but NO tool call was returned; "
                f"model answered in text instead: {text[:200]!r}",
                [f"finish_reason={choice.get('finish_reason')}", f"text_response={text[:300]}"],
                model_reported,
            )

        call = tool_calls[0]
        fn = call.get("function") or {}
        return check_tool_arguments(
            fn.get("arguments", "{}"), probe, model_reported, fn.get("name", "?")
        )


class OpenAIAdapter(OpenAICompatibleAdapter):
    pass


class DeepSeekAdapter(OpenAICompatibleAdapter):
    base_url = "https://api.deepseek.com/v1"
    name = "deepseek"
    registry_id = "deepseek"
    env_vars = ("DEEPSEEK_API_KEY",)
    # DeepSeek follows the older Chat Completions spelling.
    max_tokens_field = "max_tokens"

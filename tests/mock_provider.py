"""A deliberately misbehaving provider, for verifying the harness itself.

Without this, the harness is untested code that will be pointed at paid APIs
to produce a number someone is expected to trust. That is not acceptable. The
failure mode we most need to rule out is a FALSE POSITIVE -- reporting a
provider as non-conforming when it is fine -- because one of those destroys
the credibility of every other finding.

Model id selects the misbehavior. Each maps to exactly one expected outcome,
asserted in tests/test_harness.py.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

CONFORMING_OBJECT = {"verdict": "false", "confidence": 99, "reasoning": "The core is iron-nickel."}


class MockHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # silence
        pass

    def _send(self, status: int, body: dict):
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("x-request-id", "mock-req-abc123")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(length) or b"{}")
        path = self.path

        if "generateContent" in path:
            model = path.split("/models/")[1].split(":")[0]
            return self._gemini(model, payload)
        if path.endswith("/messages"):
            return self._anthropic(payload.get("model", ""), payload)
        return self._openai(payload.get("model", ""), payload)

    # ── OpenAI shape ────────────────────────────────────────────────────────
    def _openai(self, model: str, payload: dict):
        is_tool = "tools" in payload

        if model == "mock-reject":
            return self._send(400, {"error": {"message":
                "response_format of type 'json_schema' is not supported with this model",
                "type": "invalid_request_error"}})
        if model == "mock-dialect":
            return self._send(400, {"error": {"message":
                "Invalid schema for response_format: 'additionalProperties' is required to be "
                "supplied and to be false", "type": "invalid_request_error"}})
        if model == "mock-auth":
            return self._send(401, {"error": {"message": "Incorrect API key provided"}})
        if model == "mock-notfound":
            return self._send(404, {"error": {"message":
                "The model 'mock-notfound' does not exist", "code": "model_not_found"}})

        def wrap(message: dict, finish="stop"):
            return {"id": "chatcmpl-mock", "model": f"{model}-2026-01-01",
                    "choices": [{"index": 0, "finish_reason": finish, "message": message}]}

        if is_tool:
            if model == "mock-no-tool-call":
                return self._send(200, wrap({"role": "assistant",
                    "content": "The capital of France is Paris.", "tool_calls": None}))
            if model == "mock-bad-args":
                return self._send(200, wrap({"role": "assistant", "tool_calls": [
                    {"id": "c1", "type": "function", "function": {"name": "record_answer",
                     "arguments": json.dumps({"answer": "Paris", "certainty": "absolutely",
                                              "extra_field": "oops"})}}]}))
            if model == "mock-truncated":
                return self._send(200, wrap({"role": "assistant", "content": "The capital"},
                                            finish="length"))
            return self._send(200, wrap({"role": "assistant", "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "record_answer",
                 "arguments": json.dumps({"answer": "Paris", "certainty": "high"})}}]}))

        if model == "mock-prose":
            return self._send(200, wrap({"role": "assistant", "content":
                "Great question! The claim is ridiculous -- the core is iron and nickel."}))
        if model == "mock-advisory-only":
            # Every structural constraint honored; only maxLength overrun.
            return self._send(200, wrap({"role": "assistant", "content": json.dumps(
                {"verdict": "false", "confidence": 99, "reasoning": "x" * 400})}))
        if model == "mock-silent-violation":
            return self._send(200, wrap({"role": "assistant", "content": json.dumps(
                {"verdict": "ridiculous", "confidence": "very high",
                 "reasoning": "It is iron.", "notes": "I added this field."})}))
        if model == "mock-truncated":
            return self._send(200, wrap({"role": "assistant",
                "content": '{"verdict": "fal'}, finish="length"))
        return self._send(200, wrap({"role": "assistant",
                                     "content": json.dumps(CONFORMING_OBJECT)}))

    # ── Anthropic shape ─────────────────────────────────────────────────────
    def _anthropic(self, model: str, payload: dict):
        forced_tool = payload.get("tool_choice", {}).get("type") == "tool"

        if model == "mock-no-tool-call":
            return self._send(200, {"model": model, "stop_reason": "end_turn",
                "content": [{"type": "text", "text": "The capital of France is Paris."}]})
        if model == "mock-silent-violation":
            return self._send(200, {"model": model, "stop_reason": "tool_use", "content": [
                {"type": "tool_use", "name": "claim_assessment", "id": "t1",
                 "input": {"verdict": "ridiculous", "confidence": "very high",
                           "reasoning": "iron", "notes": "extra"}}]})
        name = "claim_assessment" if forced_tool else "record_answer"
        inp = CONFORMING_OBJECT if forced_tool else {"answer": "Paris", "certainty": "high"}
        return self._send(200, {"model": model, "stop_reason": "tool_use", "content": [
            {"type": "tool_use", "name": name, "id": "t1", "input": inp}]})

    # ── Gemini shape ────────────────────────────────────────────────────────
    def _gemini(self, model: str, payload: dict):
        is_tool = "tools" in payload

        # Reject any schema that still carries additionalProperties -- this is
        # what the real API does, and it verifies our translation actually
        # removed it.
        raw = json.dumps(payload)
        if "additionalProperties" in raw:
            return self._send(400, {"error": {"code": 400, "message":
                "Invalid JSON payload received. Unknown name \"additionalProperties\" at "
                "'generation_config.response_schema': Cannot find field.",
                "status": "INVALID_ARGUMENT"}})

        def wrap(parts, finish="STOP"):
            return {"modelVersion": f"{model}-001",
                    "candidates": [{"finishReason": finish, "content": {"parts": parts}}]}

        if is_tool:
            if model == "mock-no-tool-call":
                return self._send(200, wrap([{"text": "The capital of France is Paris."}]))
            return self._send(200, wrap([{"functionCall": {"name": "record_answer",
                "args": {"answer": "Paris", "certainty": "high"}}}]))

        if model == "mock-dialect-gap-mixed":
            # Extra field (attributable to the dropped constraint) AND a bad
            # enum value (genuine -- that constraint WAS sent). Must stay silent.
            return self._send(200, wrap([{"text": json.dumps(
                {"verdict": "ridiculous", "confidence": 99, "reasoning": "iron",
                 "notes": "extra"})}]))
        if model == "mock-dialect-gap":
            # Schema-valid on everything Gemini COULD enforce, but carries an
            # extra field -- because additionalProperties was dropped in
            # translation and never sent. The signature dialect-gap finding.
            return self._send(200, wrap([{"text": json.dumps(
                {**CONFORMING_OBJECT, "notes": "extra field, nobody asked Gemini to forbid it"})}]))
        return self._send(200, wrap([{"text": json.dumps(CONFORMING_OBJECT)}]))


def start(port: int = 0) -> tuple[HTTPServer, str]:
    server = HTTPServer(("127.0.0.1", port), MockHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_port}"


if __name__ == "__main__":
    srv, base = start(8901)
    print(f"mock provider on {base}")
    srv.serve_forever()

"""End-to-end verification of the adjudication logic.

Each case asserts: given a provider that behaves in a specific way, does the
harness reach the correct outcome AND the correct divergence class?

Run: python3 tests/test_harness.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from conformance import outcomes as O
from conformance.adapters.anthropic import AnthropicAdapter
from conformance.adapters.google import GoogleAdapter
from conformance.adapters.openai_compatible import OpenAIAdapter
from conformance.probes.definitions import STRUCTURED_OUTPUT_V1, TOOL_CALLING_V1
from conformance.http import post_json
from mock_provider import start

# (label, adapter_cls, model, capability, declared, expected_outcome, expected_divergence)
CASES = [
    ("openai conforming",       OpenAIAdapter, "mock-good",             "structured_output", True,  O.PASS,                O.DIV_NONE),
    ("openai invents fields",   OpenAIAdapter, "mock-silent-violation", "structured_output", True,  O.FAIL_VIOLATED,       O.DIV_SILENT),
    ("openai returns prose",    OpenAIAdapter, "mock-prose",            "structured_output", True,  O.FAIL_UNPARSEABLE,    O.DIV_SILENT),
    ("openai advisory only",    OpenAIAdapter, "mock-advisory-only",     "structured_output", True,  O.FAIL_ADVISORY_ONLY,  O.DIV_ADVISORY),
    ("openai honest refusal",   OpenAIAdapter, "mock-reject",           "structured_output", True,  O.REJECTED,            O.DIV_HONEST),
    ("openai dialect refusal",  OpenAIAdapter, "mock-dialect",          "structured_output", True,  O.UNSUPPORTED_DIALECT, O.DIV_HONEST),
    ("openai truncated",        OpenAIAdapter, "mock-truncated",        "structured_output", True,  O.ERROR_TRUNCATED,     O.DIV_UNKNOWN),
    ("openai bad credential",   OpenAIAdapter, "mock-auth",             "structured_output", True,  O.ERROR_AUTH,          O.DIV_UNKNOWN),
    ("openai model missing",    OpenAIAdapter, "mock-notfound",         "structured_output", True,  O.ERROR_NOT_FOUND,     O.DIV_UNKNOWN),
    ("openai undeclared works", OpenAIAdapter, "mock-good",             "structured_output", False, O.PASS,                O.DIV_UNDECLARED),

    ("openai tool ok",          OpenAIAdapter, "mock-good",             "tool_calling",      True,  O.PASS,                O.DIV_NONE),
    ("openai ignores required", OpenAIAdapter, "mock-no-tool-call",     "tool_calling",      True,  O.FAIL_VIOLATED,       O.DIV_SILENT),
    ("openai bad tool args",    OpenAIAdapter, "mock-bad-args",         "tool_calling",      True,  O.FAIL_VIOLATED,       O.DIV_SILENT),
    ("openai tool truncated",   OpenAIAdapter, "mock-truncated",        "tool_calling",      True,  O.ERROR_TRUNCATED,     O.DIV_UNKNOWN),

    ("anthropic conforming",    AnthropicAdapter, "mock-good",             "structured_output", True, O.PASS,          O.DIV_NONE),
    ("anthropic violates",      AnthropicAdapter, "mock-silent-violation", "structured_output", True, O.FAIL_VIOLATED, O.DIV_SILENT),
    ("anthropic tool ok",       AnthropicAdapter, "mock-good",             "tool_calling",      True, O.PASS,          O.DIV_NONE),
    ("anthropic ignores any",   AnthropicAdapter, "mock-no-tool-call",     "tool_calling",      True, O.FAIL_VIOLATED, O.DIV_SILENT),

    ("gemini conforming",       GoogleAdapter, "mock-good",        "structured_output", True, O.PASS,          O.DIV_NONE),
    ("gemini unrepresentable",  GoogleAdapter, "mock-dialect-gap",       "structured_output", True, O.UNREPRESENTABLE, O.DIV_UNREPRESENTABLE),
    ("gemini mixed stays silent",GoogleAdapter,"mock-dialect-gap-mixed", "structured_output", True, O.FAIL_VIOLATED,   O.DIV_SILENT),
    ("gemini tool ok",          GoogleAdapter, "mock-good",        "tool_calling",      True, O.PASS,          O.DIV_NONE),
    ("gemini ignores ANY",      GoogleAdapter, "mock-no-tool-call","tool_calling",      True, O.FAIL_VIOLATED, O.DIV_SILENT),
]


CELL_VERDICT_CASES = [
    # (label, conformance_classes across a cell's conclusive trials, expected verdict)
    ("unanimous honored",     [O.CONF_HONORED] * 5,                      O.CONF_HONORED),
    ("unanimous silent",      [O.CONF_SILENT] * 5,                       O.CONF_SILENT),
    ("unanimous advisory",    [O.CONF_ADVISORY] * 3,                     O.CONF_ADVISORY),
    ("real anthropic split",  [O.CONF_ADVISORY]*3 + [O.CONF_HONORED]*2,  O.CELL_INCONSISTENT),
    ("4 pass, 1 silent",      [O.CONF_HONORED]*4 + [O.CONF_SILENT],      O.CELL_INCONSISTENT),
    ("three-way split",       [O.CONF_HONORED, O.CONF_SILENT, O.CONF_ADVISORY], O.CELL_INCONSISTENT),
    ("single trial",          [O.CONF_HONORED],                          O.CONF_HONORED),
    ("no conclusive trials",  [],                                        O.CONF_INCONCLUSIVE),
]


def run_cell_verdict_cases() -> tuple[int, int]:
    passed = failed = 0
    for label, classes, want in CELL_VERDICT_CASES:
        got = O.cell_verdict(classes)
        if got == want:
            passed += 1
            print(f"  PASS  {label:<22} -> {got}")
        else:
            failed += 1
            print(f"  FAIL  {label:<22} -> got {got}, want {want}")
    return passed, failed


def run() -> int:
    server, base = start()
    passed = failed = 0
    try:
        for label, cls, model, capability, declared, want_outcome, want_div in CASES:
            adapter = cls("mock-key")
            # Point the adapter at the mock instead of the real provider.
            adapter.base_url = base if cls is not GoogleAdapter else base
            cls_base = cls.base_url
            cls.base_url = base
            try:
                probe = STRUCTURED_OUTPUT_V1 if capability == "structured_output" else TOOL_CALLING_V1
                if capability == "structured_output":
                    url, headers, payload = adapter.structured_output_request(model, probe)
                else:
                    url, headers, payload = adapter.tool_calling_request(model, probe)
                exchange = post_json(url, headers, payload, max_attempts=1, timeout=10)
                interp = (adapter.interpret_structured_output(exchange, probe)
                          if capability == "structured_output"
                          else adapter.interpret_tool_calling(exchange, probe))
            finally:
                cls.base_url = cls_base

            got_div, _ = O.classify_divergence(declared, interp.outcome)
            ok = interp.outcome == want_outcome and got_div == want_div
            if ok:
                passed += 1
                print(f"  PASS  {label:<26} -> {interp.outcome} / {got_div}")
            else:
                failed += 1
                print(f"  FAIL  {label:<26} -> got {interp.outcome}/{got_div}, "
                      f"want {want_outcome}/{want_div}")
                print(f"        detail: {interp.detail[:160]}")
    finally:
        server.shutdown()

    print()
    cv_passed, cv_failed = run_cell_verdict_cases()
    passed += cv_passed
    failed += cv_failed

    print(f"\n{passed} passed, {failed} failed, {len(CASES) + len(CELL_VERDICT_CASES)} total")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(run())

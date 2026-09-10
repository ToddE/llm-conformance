"""Full pipeline against the mock provider: runner -> evidence -> report.

Produces a SYNTHETIC evidence bundle. Its results are fabricated by design --
they exercise the plumbing, they say nothing about any real provider. The run
id is prefixed MOCK so a synthetic bundle can never be mistaken for a real
measurement.
"""
from __future__ import annotations

import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from conformance.adapters.anthropic import AnthropicAdapter
from conformance.adapters.google import GoogleAdapter
from conformance.adapters.openai_compatible import OpenAIAdapter
from conformance.evidence import EvidenceWriter
from conformance.registry import Registry
from conformance.report import render, summary_dict
from conformance.runner import Target, run_all
from mock_provider import start


class FakeRegistry(Registry):
    """models.dev claims everything works -- the realistic starting point."""
    def declares(self, provider, model_id, capability): return True
    def source_ref(self, provider, model_id): return f"MOCK-REGISTRY[{provider}.{model_id}]"


def main() -> int:
    server, base = start()
    OpenAIAdapter.base_url = base
    AnthropicAdapter.base_url = base
    GoogleAdapter.base_url = base

    targets = [
        Target("openai", ["mock-good", "mock-silent-violation", "mock-prose", "mock-reject"]),
        Target("anthropic", ["mock-good", "mock-no-tool-call"]),
        Target("google", ["mock-good", "mock-dialect-gap"]),
        Target("deepseek", ["mock-unreachable"]),  # no key -> skipped
    ]
    env = {"OPENAI_API_KEY": "k", "ANTHROPIC_API_KEY": "k", "GEMINI_API_KEY": "k"}
    run_id = "MOCK_run_synthetic_demo"
    out = os.path.join("results", f"{run_id}.jsonl")
    if os.path.exists(out):
        os.remove(out)

    try:
        with EvidenceWriter(out) as w:
            results = run_all(run_id, targets, FakeRegistry({}), env, w,
                              ["structured_output", "tool_calling"])
    finally:
        server.shutdown()

    rows = [json.loads(r.to_json()) for r in results]
    print(render(rows, color=sys.stdout.isatty()))
    print(f"synthetic evidence: {out}")
    print(json.dumps(summary_dict(rows), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

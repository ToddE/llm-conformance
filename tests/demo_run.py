"""Full pipeline against the mock provider: runner -> run bundle -> report.

Produces a SYNTHETIC run bundle under runs/. Its results are fabricated by
design -- they exercise the plumbing, they say nothing about any real
provider. The run id is prefixed MOCK so a synthetic bundle can never be
mistaken for a real measurement, and runs/ is gitignored so it never lands in
git by accident either.
"""
from __future__ import annotations

import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from conformance.registry import Registry
from conformance.report import render, render_markdown, summary_dict
from conformance.runbundle import open_bundle
from conformance.runner import ADAPTERS, run_all
from mock_provider import start


class FakeRegistry(Registry):
    """models.dev claims everything works -- the realistic starting point."""
    def declares(self, provider, model_id, capability): return True
    def source_ref(self, provider, model_id): return f"MOCK-REGISTRY[{provider}.{model_id}]"


def main() -> int:
    server, base = start()
    # ADAPTERS["openai"] etc. are dynamically-built subclasses from
    # catalog.adapter_class(), NOT the same class objects as
    # conformance.adapters.openai_compatible.OpenAIAdapter -- patching the
    # wire-format base classes would silently do nothing here. Patch the
    # actual classes run_all() resolves through ADAPTERS.
    for surface in ("openai", "anthropic", "google"):
        ADAPTERS[surface].base_url = base

    def models(surface, ids):
        return [(surface, m, None) for m in ids]

    plan = (
        models("openai", ["mock-good", "mock-silent-violation", "mock-prose", "mock-reject"])
        + models("anthropic", ["mock-good", "mock-no-tool-call"])
        + models("google", ["mock-good", "mock-dialect-gap"])
        + models("deepseek", ["mock-unreachable"])  # no key -> skipped
    )
    env = {"OPENAI_API_KEY": "k", "ANTHROPIC_API_KEY": "k", "GEMINI_API_KEY": "k"}
    run_id = "MOCK_run_synthetic_demo"

    bundle = open_bundle(run_id, output_dir="./runs")
    try:
        results = run_all(run_id, plan, FakeRegistry({}), env, bundle,
                          ["structured_output", "tool_calling"], trials=1)
    finally:
        bundle.close_results()
        server.shutdown()

    rows = [json.loads(r.to_json()) for r in results]
    summary = summary_dict(rows)
    bundle.write_summary(summary)
    bundle.write_report(render_markdown(rows, run_id))
    manifest = bundle.write_manifest()

    print(render(rows, color=sys.stdout.isatty()))
    print(f"synthetic bundle: {bundle.run_dir}")
    print(f"manifest: {len(manifest['artifacts'])} artifact(s) hashed")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

---
title: Home
layout: home
nav_order: 1
---

# llm-conformance

**Does an LLM deployment actually honor the capabilities it declares?**

Every model registry in existence, models.dev, LiteLLM's model map,
OpenRouter's, publishes **declared** capability data. Someone read the
provider's documentation and wrote `structured_output: true` in a JSON file.
Nobody probes the endpoint to check.

This project probes the endpoint.
{: .fs-6 .fw-300 }

[Get started](getting-started){: .btn .btn-primary .fs-5 .mb-4 .mb-md-0 .mr-2 }
[Methodology](methodology){: .btn .fs-5 .mb-4 .mb-md-0 }

---

## The question this project exists to answer

Is declared-vs-actual divergence **material**?

If registries turn out to be broadly accurate, there is no product here and
the correct move is to stop. That is a valid outcome, and the harness is
built to report it plainly rather than manufacture a finding.

## Why declared data is not enough

A registry flag is a boolean. Reality has at least four states.

| What happens | How a registry shows it | Consequence |
|---|---|---|
| Endpoint enforces the constraint | `true` | Correct |
| Endpoint **rejects** the parameter | `true` | Wrong, but visible immediately |
| Endpoint **accepts and ignores** it | `true` | Breaks in production |
| Constraint is **unrepresentable** in that platform's schema dialect | `true` | Silently degraded, no error |

Rows three and four are the reason this project exists.

## Two capabilities, probed deeply

1. **Structured output.** Send a strict JSON schema, check whether the
   response conforms.
2. **Tool calling.** Send forced tool choice, check whether a tool call
   comes back with arguments valid against the tool's own schema.

Against direct provider APIs (OpenAI, Anthropic, Google Gemini, DeepSeek)
and local runtimes (Ollama, with catalog entries ready for LM Studio, Msty,
llama.cpp, vLLM, and Jan).

## Silent failure is the headline distinction

An endpoint that **rejects** an unsupported parameter is behaving correctly.
An endpoint that **accepts** the parameter and ignores it is the failure
that reaches production undetected. Every result on this site reports these
separately and never sums them into one number.

See [Methodology](methodology) for the full outcome taxonomy, and
[Findings](findings) for what has actually been measured so far.

## Reproducible by design

Every result records the exact request body as sent, the raw response, the
model id requested and reported, and a runnable `curl` reproduction. The
harness itself is Python standard library only, no dependencies to install.
See [Reproducing a finding](reproducing).

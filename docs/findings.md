---
title: Findings
nav_order: 6
---

# Findings
{: .no_toc }

1. TOC
{:toc}

---

{: .warning }
Every result below is a small sample. Fifteen or more deployment
configurations are required before a result is treated as conclusive
evidence on the P1 premise; the measurements here are two and three
deployments respectively. They demonstrate the harness end to end and are
reported honestly as preliminary, not as a finished answer.

## Anthropic direct, 2026-09-11

`claude-opus-5`, `claude-sonnet-5`, `claude-fable-5-1`, n=5 trials, 22 total.

| | result |
|---|---|
| Silent failure | **0.0% (0/22)** |
| Advisory leak | 13.6% (3/22) |
| Honest refusal | 9.1% (2/22) |

`claude-fable-5-1` is a genuine registry inaccuracy: models.dev declares
`tool_call: true` and `structured_output: true`; the endpoint returns
`400: tool_choice: type "tool" and "any" are not supported for this model`
for both. The refusal arrives at request time, so an integrator finds out
immediately rather than in production.

The advisory result is worth reading closely as a methodology example. It
first scored as a **13.6% silent-failure rate**, which would have been the
headline number. That was a false positive: every structural constraint
held (`type`, `enum`, `required`, `additionalProperties`); only `maxLength`
was exceeded, and `maxLength` sits outside the enforceable subset of strict
decoding on most platforms. It now has its own outcome class and is
excluded from the silent-failure rate. See
[Methodology: guardrails against false positives](methodology#rules-that-protect-the-numbers).

## Local runtime: Ollama, 2026-09-11

`qwen3-coder:30B` (Q4_K_M), n=5 trials. Same model, same machine, same
moment, two wire formats.

| Deployment | `structured_output` | `tool_calling` |
|---|---|---|
| Ollama OpenAI-compatible shim | honored 5/5 | **silent failure 5/5** |
| Ollama native API | honored 5/5 | unrepresentable 5/5 |

The OpenAI-compatible shim accepts `tool_choice: "required"`, returns HTTP
200, and returns no tool call: the body is `"content": "Paris"` with
`finish_reason: "stop"`. Deterministic across all five trials.

The native endpoint has no `tool_choice` field at all, so a forced call is
unrepresentable in that protocol rather than ignored. The endpoint honored
every constraint it was actually given, which is why the two results are
never summed into one number.

## Reading these together

Anthropic's first-party API produced zero silent failures in this sample.
The one measured silent failure sits in a compatibility shim reached
through a different wire format on the same underlying model. That pattern,
if it holds at a larger sample, supports the claim that "OpenAI-compatible"
does not mean behaviorally identical, and that capability data needs to be
attached to the deployment rather than the model.

## Registry self-inconsistency (no probing required)

Separate from any live probe, the declared registry data is internally
inconsistent in a way worth noting. Of 1,079 model ids listed by more than
one provider in the pinned models.dev snapshot, 257 (24%) carry a hard
true-versus-false contradiction between providers on `structured_output` or
`tool_call`. Registry coverage of `structured_output` also collapses
precisely on the higher-value cloud-marketplace targets: 100% on Anthropic
direct, 83% on OpenAI direct, but 37% on Azure, 58% on Bedrock, and 7% on
Anthropic-via-Vertex.

This does not resolve whether declared-vs-observed divergence is material.
It does establish that the declared data cannot be taken as self-consistent
ground truth, independent of what live probing finds.

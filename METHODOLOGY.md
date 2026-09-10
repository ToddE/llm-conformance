# Methodology

This document exists so a finding can be disputed on its merits. If a provider
disagrees with a result, everything needed to argue about it is here or in the
evidence bundle.

## What is being tested

One question, asked of a specific deployment:

> When this endpoint accepts a parameter that declares a capability, does it
> actually honor that capability?

This is **conformance and interoperability testing**, not benchmarking. No
claim is made about output quality, accuracy, latency, or cost. The subject is
whether an endpoint honors its own accepted parameters.

## Capabilities probed (v1)

| Capability | Constraint sent | Conformance means |
|---|---|---|
| `structured_output` | A strict JSON Schema | Response parses as JSON and validates against that schema |
| `tool_calling` | Forced tool choice (`required` / `any` / `ANY`) | A tool call is returned, with arguments valid against the tool's input schema |

## Probe design: pressure is the point

A trivial schema on a capable model passes everywhere and measures nothing.
Both probes put the **prompt in direct conflict with the constraint**.

The structured-output probe asks for prose, asks for a verdict word outside
the enum, asks for confidence as a phrase rather than an integer, and
explicitly invites extra fields — against `additionalProperties: false`. The
tool-calling probe asks a question answerable from memory and tells the model
not to use tools, while sending forced tool choice.

This is a fair test, and it is the whole test. The product claim behind
`"strict": true` is that enforcement happens at decode time and is not
negotiable by prompt. **If a sentence of user text can break it, it was never
enforcement — it was a polite request.** An endpoint that genuinely constrains
decoding passes regardless of what the prompt says.

## Outcome taxonomy

The distinction that matters is not "works / doesn't work":

| Outcome | Meaning | Class |
|---|---|---|
| `pass` | Constraint honored | match |
| `fail_violated` | Accepted the parameter, returned a well-formed response that breaks the contract | **silent** |
| `fail_unparseable` | Accepted the constraint, returned the wrong shape entirely | **silent** |
| `rejected` | Refused at request time, naming the capability | honest |
| `unsupported_dialect` | Accepted the capability, rejected our *schema* | honest |
| `error_truncated` | Hit the output token limit mid-generation | inconclusive |
| `error_transport` / `error_auth` / `error_rate_limit` / `error_not_found` | Infrastructure or setup | inconclusive |
| `skipped_no_credential` | No key for this provider | inconclusive |

**Silent vs. honest is the load-bearing distinction.** An endpoint that
*rejects* an unsupported parameter is behaving correctly — you find out at
integration time and handle it. An endpoint that *accepts* the parameter and
ignores it is the failure that reaches production: your code works in testing,
then one response in fifty is unparseable and the pipeline breaks at 2am.

Both are "declared true, doesn't work." Only one is dangerous. Collapsing them
into a single divergence percentage destroys the finding.

## Rules that protect the numbers

1. **Inconclusive results are never counted as failures.** A divergence rate
   inflated by timeouts is worse than no rate at all.
2. **Truncation is always inconclusive.** A truncated JSON object is invalid
   JSON. Scoring that as a schema violation would be a false positive — the
   most damaging bug this harness could have, because one refutable finding
   costs more credibility than ten correct ones gain.
3. **Model refusals are not capability failures.** A model declining a task is
   orthogonal to whether the endpoint enforces a schema.
4. **Unattributable 4xx responses are not capability failures.** If an error
   cannot be tied to the capability or the schema, it is recorded as an error.
5. **Denominators are always published.** "40% divergence" over 5 probes is
   noise, and presenting it without n is misleading.
6. **Mechanism is recorded per result** and never averaged across (below).

## Mechanism differences are recorded, not hidden

The same capability is requested differently on each surface:

| Provider | Structured output | Tool calling |
|---|---|---|
| OpenAI / DeepSeek | `response_format.json_schema`, `strict: true` | `tool_choice: "required"` |
| Anthropic | Forced tool with `input_schema` | `tool_choice: {type: "any"}` |
| Google Gemini | `generationConfig.responseSchema` | `functionCallingConfig.mode: "ANY"` |

Anthropic's canonical route to guaranteed-shape output is a forced tool call,
not an OpenAI-style `response_format`. Comparing "Anthropic structured output"
against "OpenAI structured output" as though they were the same test would be
the kind of sloppiness that gets a whole report dismissed. Every result records
its `mechanism`.

## The schema dialect problem

Gemini's `responseSchema` accepts a subset of OpenAPI Schema and does **not**
accept `additionalProperties`. Sending our schema verbatim yields a 400 that
teaches us nothing about enforcement.

So the harness **translates** the schema into the provider's dialect, records
every assertion dropped in transit (`dropped_assertions`), and then validates
the response against the **full original schema**.

That combination surfaces a finding a boolean registry cannot express:

> The constraint was not violated. The constraint was **inexpressible**.

A team porting a strict schema from OpenAI to Gemini silently loses
`additionalProperties` enforcement, receives no error, and finds out in
production. `structured_output: true` cannot represent that. This is a
materially different fact from "supported / not supported."

## Evidence

Every result records:

- the **exact request body as sent** (literal serialized bytes) plus its SHA-256
- endpoint URL and request headers, with credentials replaced by a
  `sha256:` fingerprint — enough to prove two runs used the same key, never
  enough to expose it
- the **raw response body**, untouched and unparsed, plus its SHA-256
- response headers, including the **provider's own request id** — this is what
  lets a provider look the call up in *their* logs. A finding a provider can
  audit is a finding they can concede
- the model id **requested** and the model id the API **reported** (these
  differ when aliases resolve to snapshots; the gap is itself a signal)
- UTC timestamps, request duration
- the registry claim being tested, with the pinned snapshot's hash and fetch time
- harness git commit, Python version, platform, probe id and version

The harness speaks **raw HTTP over the standard library** rather than using
provider SDKs. An SDK can rewrite a payload, retry, or coerce a malformed
response — any of which would make "the exact request payload" a lie, or hide
the exact failures being hunted.

`HttpExchange.as_curl()` emits a runnable reproduction command for any result.

## Registry pinning

`models.dev` is fetched once and pinned to `registry/models.dev.snapshot.json`
with a SHA-256 and fetch timestamp. Registries change; a finding that says
"declared true, observed false" is meaningless unless you can show what the
registry said *at probe time*.

## Known limitations

- **Single sample per probe.** Model behavior is stochastic. A single `pass`
  does not prove enforcement — it proves non-violation on one draw. Only
  constraint-level enforcement (rejections, dialect errors) is deterministic.
  **Repeat sampling is the most important v2 change.**
- **Single point in time.** Providers roll out changes per-region and
  per-model. A result is a timestamped observation, not a permanent property.
- **Two capabilities.** Reasoning deltas, prompt caching, streaming tool calls,
  citations, and long-context behavior are unprobed.
- **Direct APIs only.** The cloud marketplaces (Azure OpenAI, Bedrock, Vertex)
  are the higher-value targets and are not yet wired.
- **Not adversarial to the platform.** Probes are ordinary API calls at
  ordinary volume. No rate-limit or availability testing.

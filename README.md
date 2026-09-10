# llm-conformance

**Do LLM deployments actually honor the capabilities they declare?**

Every model registry in existence — [models.dev](https://models.dev),
LiteLLM's model map, OpenRouter's — publishes **declared** capability data.
Someone read the docs and wrote `structured_output: true` in a JSON file.
Nobody probes the endpoint to check.

This project probes the endpoint.

> ⚠️ **Status: harness complete, measurement not yet run.** The code below is
> built and tested end-to-end against a mock provider (21/21 adjudication
> cases pass). No real provider has been probed, because no API keys were
> available. **No claim about real-world divergence is made anywhere in this
> repository yet.** See [Current status](#current-status).

---

## The question this project exists to answer

Is declared-vs-actual divergence **material**?

If registries turn out to be broadly accurate, there is no product here and
the correct move is to stop. That is a valid and useful outcome, and the
harness is built to be equally capable of reporting it — the report has a
"NOT MATERIAL" verdict path and will print it without hedging.

Everything else — naming, architecture, monetization — is downstream of this
one measurement.

---

## Why "declared" data isn't good enough

A registry flag is a boolean. Reality has at least four states:

| What actually happens | How a registry shows it | Consequence |
|---|---|---|
| Endpoint enforces the constraint | `true` | ✅ correct |
| Endpoint **rejects** the parameter | `true` | ⚠️ wrong, but you find out immediately |
| Endpoint **accepts and ignores** it | `true` | 🔥 **breaks in production** |
| Constraint is **inexpressible** in that platform's schema dialect | `true` | 🔥 **silently degraded, no error at all** |

The third and fourth rows are the reason this project exists. They are
invisible to documentation, invisible to registries, and invisible in testing
right up until they aren't.

### Silent vs. honest failure

This distinction is load-bearing throughout the codebase:

- **Honest failure** — the endpoint returns `400: response_format is not
  supported with this model`. Annoying, but safe. Integration testing catches
  it on day one.
- **Silent failure** — the endpoint returns `200 OK` with a response that
  breaks the schema you supplied. Your code works in testing. Then one
  response in fifty is unparseable prose and the pipeline breaks at 2am.

Both are "declared true, doesn't work." Only one is dangerous. The harness
never sums them into a single divergence percentage.

---

## What it probes (v1)

Two capabilities, deliberately narrow:

1. **Structured output** — send a strict JSON schema, check whether the
   response actually conforms.
2. **Tool calling** — send forced tool choice, check whether a tool call is
   actually returned, with arguments valid against the tool's own schema.

Against four direct APIs: **OpenAI, Anthropic, Google Gemini, DeepSeek**.

Cloud marketplaces (Azure OpenAI, AWS Bedrock, GCP Vertex) are the
higher-value targets — same model, different platform, different behavior —
but their account onboarding is slow, so they come later. `.env` documents
their credentials already.

### Probes apply real pressure

A trivial schema on a frontier model passes everywhere and measures nothing.
Both probes put the **prompt in direct conflict with the constraint** — asking
for prose where JSON is mandated, for an enum value that isn't in the enum,
for extra fields against `additionalProperties: false`.

That's a fair test, and it's the whole test. The claim behind `"strict": true`
is that enforcement happens at decode time and isn't negotiable by prompt. If
a sentence of user text can break it, it was never enforcement — it was a
polite request.

---

## Quick start

No dependencies. No `pip install`. No virtualenv.

```bash
git clone <this repo> && cd llm-conformance
cp .env.example .env      # then add whichever keys you have
python3 run.py doctor     # what can I probe right now?
python3 run.py probe      # probe everything with a key
```

```bash
# more control
python3 run.py probe --dry-run                          # print the plan, send nothing
python3 run.py probe --provider openai --models gpt-5.6
python3 run.py probe --capability structured_output
python3 run.py report results/<run-id>.jsonl --json
python3 run.py credentials                              # key expiry / rotation
python3 run.py credentials --json                       # for cron + alerting
python3 run.py readjudicate results/<run-id>.jsonl      # re-score, no API calls
python3 run.py probe --profile useast                   # one region profile
```

### Credential lifecycle and region profiles

Keys carry rotation metadata (`_EXPIRES_AT`, `_ROTATED_AT`, `_NOTE`), surfaced
by `run.py credentials` with exit codes for alerting (0 healthy / 1 no expiry
recorded / 2 rotate soon / 3 expired). A key with no expiry recorded reports as
`no_expiry_set`, never as healthy — unknown is not the same as fine.

Providers support **named region profiles** (`ANTHROPIC_USEAST_API_KEY` +
`ANTHROPIC_USEAST_REGION`). Each becomes a separate deployment with its own
`deployment_id`, because region is part of deployment identity — which is what
makes "same model, different region, different observed capability" testable at
all. Profiles are auto-discovered; there is no list to register.

### Re-adjudication

Evidence stores raw responses, so `readjudicate` re-scores any historical
bundle under the current taxonomy with **no API calls**. This is what keeps the
drift archive comparable when the taxonomy improves — and it is how the
`maxLength` false positive above was corrected across an existing run without
re-spending.

`.env` documents where to obtain every key, what access tier is needed, and
which are slow to onboard. A missing key is **skipped and reported**, never a
crash — a partial run is still a valid, publishable result.

**Cost:** a full run is a couple dozen tiny requests. Well under $0.10.

---

## Requirements: reproducibility and disputability

Two hard requirements shaped most of the design.

### 1. Every result must be reproducible or disputable by a third party

Each result records the **exact request body as sent** (literal bytes, plus
SHA-256), the **raw response body** (untouched, plus SHA-256), the model id
requested *and* the model id the API reported back, UTC timestamps, the
endpoint, the mechanism used, the registry claim being tested with the pinned
snapshot's hash, and the harness git commit.

It also records the **provider's own request id** from the response headers.
That's what lets a provider look the call up in *their* logs. A finding a
provider can audit is a finding they can concede; one they can't is one
they'll dismiss.

Credentials are replaced with a `sha256:` fingerprint — enough to prove two
runs used the same key, never enough to expose it. Any result can emit a
runnable `curl` reproduction via `HttpExchange.as_curl()`.

### 2. A language that makes third-party reproduction easy

**Python 3, standard library only, raw HTTP — no SDKs.** Three reasons:

- **Zero dependencies.** Someone disputing a finding runs `python3 run.py`.
  No pip, no lockfile drift, no supply chain.
- **The recorded payload is the real payload.** SDKs inject, rename, and
  default fields. Recording "what we passed to the SDK" would not be what went
  on the wire, which would make the evidence a lie.
- **SDKs hide the failures we're hunting.** A client that retries or coerces a
  malformed response would paper over the exact thing being measured.

The JSON Schema validator is hand-rolled for the same reason — a ~150-line
file you can audit by reading it is a stronger guarantee than a large
dependency.

---

## Adding a provider is config, not code

The maintenance asymmetry that drives the architecture: **wire formats are few
and grow slowly** (four today). **Deployments grow without bound** — providers ×
models × regions × accounts × adapters. So only the first is Python.

| | lives in | grows |
|---|---|---|
| Wire format (request/response shape) | `conformance/adapters/` + `WIRE_FORMATS` | rarely |
| Deployment (provider, endpoint, model, region, credential) | [deployments.json](deployments.json) + env | constantly |

Adding a provider that speaks an existing wire is a JSON block and a
credential — no Python, no release:

```json
"myprovider": {
  "wire": "openai",
  "base_url": "https://api.example.com/v1",
  "env_prefix": "MYPROVIDER",
  "registry_id": "myprovider",
  "models": "auto"
}
```

`"models": "auto"` takes the most recently updated models declaring both
capabilities from the pinned models.dev snapshot, so model lists aren't
hand-maintained either. Credentials are discovered from `env_prefix` at
runtime, so the same catalog works against `.env` locally and Vault/SSM
elsewhere — **`deployments.json` contains no secrets and is committed
deliberately**, since publishing the exact deployment matrix is part of the
methodology.

A genuinely new wire format still needs an adapter. That part is irreducible:
a new shape means new serialization and new response parsing.

## Local runtimes

`python3 run.py discover` scans for local model servers. Ollama, LM Studio,
Msty, llama.cpp, vLLM and Jan have catalog entries; the non-Ollama ones ship
`"enabled": false` with documented default ports — **confirm the port in the
app's own settings before enabling**, since those defaults are documented
values, not verified.

Local runtimes are not a convenience feature. They are load-bearing:

- **Credential-free reproduction.** A third party can re-run a finding with no
  vendor account at all. That is the strongest form of "reproducible by a third
  party" this project can offer.
- **They hold the model constant and vary the server.** Same GGUF via Ollama vs
  LM Studio vs llama.cpp isolates the adapter/server as the only variable — the
  cleanest available test of P3.
- **"OpenAI-compatible" claims are thinnest here**, and that is already where
  the measured silent failure is.
- **On-prem vLLM is a real enterprise target**, especially in regulated
  industries where inference can't leave the building — and capability data for
  those deployments is nonexistent.

**Quantization is part of deployment identity**, not metadata. The same model
at Q4 and Q8 is two different artifacts, and quantization measurably affects
whether a model emits schema-valid output. Folding them into one deployment
would average two different things together.

## How it works

```
models.dev snapshot ─┐
  (declared claim)   ├─► compare ─► outcome ─► divergence class ─► report
provider endpoint ───┘                              │
  (observed behavior)                               └─► evidence bundle (JSONL)
```

| Path | Role |
|---|---|
| [conformance/outcomes.py](conformance/outcomes.py) | **The outcome taxonomy — the most important file here.** Silent vs. honest vs. inconclusive |
| [conformance/probes/definitions.py](conformance/probes/definitions.py) | The exact payloads under test, versioned |
| [conformance/adapters/](conformance/adapters/) | Three API shapes: OpenAI-compatible, Anthropic, Gemini |
| [conformance/evidence.py](conformance/evidence.py) | Evidence records, redaction, provenance, `as_curl()` |
| [conformance/jsonschema.py](conformance/jsonschema.py) | Zero-dep strict validator |
| [conformance/registry.py](conformance/registry.py) | models.dev fetch + pinned snapshot |
| [conformance/http.py](conformance/http.py) | Raw stdlib HTTP, retry only on transport faults |
| [conformance/report.py](conformance/report.py) | Declared-vs-actual rendering + materiality verdict |
| [tests/mock_provider.py](tests/mock_provider.py) | A deliberately misbehaving provider |
| [METHODOLOGY.md](METHODOLOGY.md) | What counts as a failure, and why |

### The schema dialect problem

Gemini's `responseSchema` doesn't accept `additionalProperties`. Rather than
hide this, the harness **translates** the schema into each provider's dialect,
records every assertion dropped in transit, then validates the response
against the **full original schema**.

That surfaces a finding a boolean cannot express:

> The constraint was not violated. The constraint was **inexpressible**.

Port a strict schema from OpenAI to Gemini and you silently lose
`additionalProperties` enforcement with no error. That is materially different
from "supported / not supported," and no declared registry captures it.

---

## Guardrails against false positives

The most damaging bug this harness could have is reporting a provider as
non-conforming when it isn't. One refutable finding costs more credibility
than ten correct ones gain. So:

- **Truncated output is always inconclusive.** A truncated JSON object is
  invalid JSON; scoring that as a schema violation would be a false positive.
- **Inconclusive is never a failure.** Timeouts, 429s, auth errors and missing
  keys get their own line and are excluded from every published rate.
- **Model refusals aren't capability failures.** A model declining a task is
  orthogonal to whether the endpoint enforces a schema.
- **Unattributable 4xx responses aren't capability failures.**
- **Advisory schema keywords are not silent failures.** `maxLength`, `pattern`,
  `minimum` and friends are outside what strict decoders enforce. Violations of
  them get their own class, excluded from the silent rate. This deliberately
  *lowers* our own divergence number — the correct direction for a measurement
  project to be wrong in.
- **`conforms` is tri-state** (`true`/`false`/`null`) so "we learned nothing"
  can never masquerade as "it failed."
- **Denominators are always printed.**

## Testing

```bash
python3 tests/test_harness.py   # 23 adjudication cases against a mock provider
python3 tests/demo_run.py       # full pipeline -> synthetic evidence + report
```

`tests/mock_provider.py` serves all three API shapes and misbehaves on demand:
invents fields, returns prose instead of JSON, ignores forced tool choice,
returns schema-violating tool arguments, rejects the capability, rejects the
schema dialect, truncates. Each maps to exactly one expected outcome.

Synthetic runs are prefixed `MOCK_` so a fabricated bundle can never be
mistaken for a real measurement.

---

## Current status

| | |
|---|---|
| Harness | ✅ complete, 22/22 adjudication tests pass |
| Evidence recording | ✅ audited — exact payload, raw response, provenance, no credential leakage |
| Repeat trials (n≥5) | ✅ implemented |
| Full deployment tuple | ✅ `adapter` and `proxy_path` first-class |
| `unrepresentable` class | ✅ separated from silent failure |
| **First real measurement** | ✅ **run — local Ollama, 2 deployments** |
| **E1 (≥15 deployments)** | ❌ **blocked on API keys** |
| Azure / Bedrock / Vertex | ⬜ deferred (slow onboarding) |

### Measurements (2026-09-10)

**Anthropic direct** — `claude-opus-5`, `claude-sonnet-5`, `claude-fable-5-1`, n=5:

| | result |
|---|---|
| SILENT failure | **0.0% (0/22)** |
| advisory leak | 13.6% (3/22) — `sonnet-5` overran `maxLength`, all structural constraints held |
| honest refusal | 9.1% (2/22) — `fable-5-1` returns 400: `tool_choice: type "tool" and "any" are not supported for this model` |

`fable-5-1` is a **real registry inaccuracy**: models.dev declares
`tool_call: true` and `structured_output: true`, and the endpoint refuses both
forced-tool modes. But it refuses *honestly*, at request time — you find out on
day one.

The `maxLength` result is worth studying as a methodology lesson: it initially
scored as a 13.6% **silent-failure rate**, which would have been the headline
number. It was a false positive. Every structural constraint (`type`, `enum`,
`required`, `additionalProperties`) held; only `maxLength` leaked — and
`maxLength` is outside the enforceable subset of strict decoding (OpenAI's
strict mode rejects it as an unsupported keyword). It now has its own class and
is excluded from the silent rate. See [Guardrails](#guardrails-against-false-positives).

### Local runtime measurement (2026-09-10)

Same model, same machine, same moment — **two adapters, different observed
capability**:

| Deployment | `structured_output` | `tool_calling` |
|---|---|---|
| `qwen3-coder:30B` via Ollama **OpenAI-compatible** | honored 5/5 | 🔥 **silent failure 5/5** |
| `qwen3-coder:30B` via Ollama **native** | honored 5/5 | unrepresentable 5/5 |

The OpenAI-compatible shim accepts `tool_choice: "required"`, returns HTTP 200,
and returns no tool call at all — just `"content": "Paris"` with
`finish_reason: "stop"`. Deterministic across every trial.

The native endpoint has no `tool_choice` field, so a forced call is
*unrepresentable* rather than ignored — a genuinely different failure, and the
reason those two are never summed.

**The contrast is the interesting part.** Anthropic's first-party API had zero
silent failures. The silent failure is in the *compatibility shim*. That is a
hypothesis worth testing at scale, and it is exactly the thesis's claim that
"OpenAI-compatible" does not mean behaviorally identical.

This is **not** an E1 result — E1 requires ≥15 deployment configurations and
this is 2. The harness says so itself and refuses to report it as one. It is
early support for P3 (*capabilities belong to the deployment, not the model*)
obtained at zero cost and with no vendor account.

### Two rates, never one

`silent-failure rate` is computed from **capability conformance** — what the
endpoint did — not from registry divergence. A deployment absent from the
registry must never be scored as conforming; otherwise the absence of a claim
would launder a real failure into a clean result. Registry divergence is
reported separately, only where a claim actually exists.

---

## Scope

**In scope:** does an endpoint honor a parameter it accepts.

**Out of scope:** output quality, accuracy, latency, cost, availability.
Latency in particular is deliberately excluded — it's noisy, location-dependent,
and ArtificialAnalysis and OpenRouter already cover it.

This is **conformance and interoperability testing**, not benchmarking. The
distinction is deliberate: it keeps the work factual, reproducible, and
narrowly about interoperability.

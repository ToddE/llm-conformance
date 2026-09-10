# aiOrka — Thesis and Validation Plan

**Status:** Draft, 2026-09-10. Pre-adoption. No external users.

This document states what we intend to build, what we are deliberately not
building, and — most importantly — how we intend to find out whether the idea
is wrong before spending a year on it.

---

## 1. The problem

Application code binds itself to model names. Model capabilities, pricing,
context limits, tool-calling behavior, structured-output enforcement, latency,
and availability then change independently underneath it. "OpenAI-compatible"
does not mean behaviorally identical: the same model reached through two
different deployments can differ in what it actually supports.

The conventional answer is a gateway — one API in front of many providers.
That space is solved. LiteLLM (MIT, self-hostable) already provides provider
normalization, 100+ adapters, retries, fallbacks, load balancing, spend
tracking, and an OpenAI-compatible proxy. Portkey, OpenRouter, and Requesty
cover adjacent ground. **We are not going to win by building a better
gateway.**

## 2. The thesis

> LiteLLM decides *how to call* a selected model deployment.
> aiOrka decides *whether a deployment is eligible at all* — and refuses to
> pretend when it isn't.

The specific wedge is **capability-preserving fallback**.

Today's fallback is naive. When provider A fails, the gateway sends the same
request to provider B without asking whether B can satisfy the same contract.
If the request required fresh web data with inline citations and enforced JSON
schema, and the fallback target is a plain chat model, the caller receives a
confident, well-formed, *stale and uncited* answer — with no error and no
signal that anything was lost.

That is a real defect with real consequences in regulated domains, and it is
the failure mode we intend to make impossible:

```
requested:  [web_search, citations, structured_output, streaming]
provided:   [streaming]
missing:    [web_search, citations, structured_output]
degraded:   true
```

A request either gets its contract satisfied, or it gets an explicit,
machine-readable account of what was lost. Never a silent downgrade.

## 3. Why this requires verified capability data

Capability-preserving fallback is only as good as the capability data behind
it. To refuse an ineligible fallback target, we must know what that target is
*actually* capable of — not what its documentation claims.

Every registry in this space today is **declared**, not verified. models.dev is
community-maintained. LiteLLM's cost/capability map is community-maintained.
OpenRouter's metadata is provider-supplied. None of them probe. A provider that
accepts `response_format: json_schema` and then returns non-conforming output
is recorded as supporting structured output.

So the foundation of this project is a measurement layer, not a routing layer.
That work lives in a separate project (`~/Workspace/llm-conformance`) for
reasons of neutrality: a vendor selling a router has an obvious conflict of
interest publishing capability data about providers it routes to.

**aiOrka consumes that feed. It does not hand-maintain a YAML registry.**

### Capability is not a boolean

`supports_structured_output: true` cannot represent what we need to know.
Building the v1 probe harness surfaced three distinct failure modes that a
boolean collapses into one:

- **Honest failure** — the endpoint returns 400 naming the unsupported
  capability. Safe. Caught on day one of integration.
- **Silent failure** — the endpoint returns 200 and ignores the constraint.
  This is the one that reaches production.
- **Inexpressible** — the constraint could not be stated in the provider's
  schema dialect at all. Gemini's `responseSchema`, for instance, cannot
  express `additionalProperties`. The constraint was not violated; it was never
  representable.

The third category may be the strongest argument that declared registries are
structurally inadequate — it holds regardless of how the divergence rate comes
out, because no boolean flag can encode it.

Beyond failure mode, capability claims need **levels** that separate evidence
from guarantee:

```yaml
structured_output:
  declared: true                # what the registry claims
  observed:
    parameter_accepted: true
    json_parseable: true
    schema_conforming: 0.98     # across n trials, not one
    strict_enforcement: false
  guarantee: best_effort        # what aiOrka will promise a caller
```

A 98% conformance rate is evidence. It is not strict enforcement. aiOrka must
never route a request requiring a hard guarantee to a deployment that offers
only a rate. **Observed behavior versus promised guarantee** is the distinction
the entire contract layer rests on.

## 4. Architecture

```
Application
    │  intent + constraints, not a model name
    ▼
aiOrka control plane
    │  capability resolution, hard constraints, contract enforcement
    ├── conformance feed (verified capability data)
    ▼
Execution substrate
    ├── LiteLLM gateway  → hosted providers
    ├── direct adapters  → where capability fidelity matters
    └── local runtimes   → Ollama, vLLM, llama.cpp
```

Two structural decisions:

- **LiteLLM is a substrate, not a competitor.** It handles the mature mechanics
  of provider access. We do not reimplement 100+ adapters. It must not become a
  hard dependency — the engine stays independently usable.
- **Inbound API is OpenAI-compatible**, so existing clients work unchanged.
  aiOrka metadata rides in `extra_body`; absent it, the endpoint behaves like a
  normal model endpoint. Outbound we keep native adapters where compat shims
  would flatten the capability detail we exist to track.
- **A native endpoint exists alongside the compatible one.** `extra_body` is
  not safe as the only channel — intermediary proxies may silently discard
  unknown fields, and losing the contract metadata is exactly the failure this
  project exists to prevent. So `POST /v1/contracts/execute` carries full
  contract semantics and degradation reporting natively, while the
  OpenAI-compatible path stays the zero-friction adoption route. If contract
  metadata is absent *or was dropped in transit*, the request is served as an
  ordinary completion — never as a contract we cannot prove we honored.

## 5. Explicitly out of scope

Scope discipline is the main risk to this project. The following are deferred,
each with the condition that would reopen it:

| Deferred | Why | Reopens when |
|---|---|---|
| Workflow / capability-graph orchestration | LangGraph, LlamaIndex, Temporal, DSPy, Mastra already occupy this. Moving here enters a *more* crowded space, not a less crowded one. | Capability contracts are proven and customers ask for multi-step plans |
| Device-state awareness (battery, thermal, memory pressure, model-loaded state) | Genuinely novel and genuinely unvalidated. Requires the mobile SDK path. | On-device demand is demonstrated, not assumed |
| Kotlin/Native FFI bindings for Python/Go/Rust | Ships ~1000 lines of filtering logic via a C ABI that forces `runBlocking`, needs a Mac in CI, and requires libcurl on consumers. A service endpoint serves those languages better. | Never, absent a concrete need |
| Latency benchmarking | ArtificialAnalysis and OpenRouter already publish this. Noisy and crowded. | Never |
| Building our own provider adapters at scale | LiteLLM has them. | Only for providers where capability fidelity is load-bearing |

## 6. Falsifiable premises

The thesis rests on three claims. Each can be killed by measurement, and each
should be tested *before* significant building.

**P1 — Registries are inaccurate in ways that matter.**
Declared capability data materially diverges from actual behavior — and the
divergence includes *silent* failures, not just honest rejections. A registry
that is wrong in a way the caller discovers immediately is a documentation bug.
A registry that is wrong in a way that reaches production is a product.
*If false:* consume models.dev directly, and there is no measurement product.

**P2 — Naive fallback causes silent degradation.**
Current gateways will fall back to a target that cannot satisfy the original
contract, and will not tell the caller.
*If false:* the contract layer solves a problem nobody has.

**P3 — Capabilities belong to the deployment, not the model.**
The same model reached via different platforms (Azure vs OpenAI direct,
Bedrock vs Anthropic direct) differs in observable capability.
*If false:* model-level registries suffice and the deployment abstraction is
unnecessary complexity.

## 7. How we test before building

Four experiments, each capable of killing the thesis. The first three test
whether the *problem* exists; the fourth tests whether anyone will pay to
solve it. That last distinction matters — a real problem nobody buys is still
not a business.

### Deployment identity

Every experiment records the same tuple, because a capability result may belong
to the deployment configuration rather than to the model:

```
deployment = provider + endpoint + model ID + model snapshot
           + region + API version + request parameters + sampling settings
           + whether the call passed through LiteLLM
```

Plus, per trial: timestamp, repetition index, HTTP status, provider error, and
the raw response. Without this, findings are neither reproducible nor
attributable, and an unreproducible finding is worse than no finding.

### E1 — Capability divergence (tests P1)

Probe strict JSON-schema conformance and `tool_choice: required` across every
reachable deployment, classifying each result as pass / honest failure / silent
failure / inexpressible / inconclusive. Compare against what models.dev
declares.

**Depth over breadth — a deliberate departure.** An external review proposed
opening with five capabilities (adding vision, streaming integrity, and
web-grounded citations). We are keeping E1 at two, tested deeply: repeated
trials, nested and enum-bearing schemas, long prompts, multi-tool definitions,
and invalid-tool temptation cases. E1 is a *kill test*. Two capabilities probed
deeply give a stronger signal on P1 than five probed shallowly, and the other
three each need different fixtures and a different harness shape. They are
follow-on work, after P1 survives.

- **Sample:** ≥15 deployment configurations, n≥5 trials per probe. Single-shot
  results are not admissible for anything response-level — those outcomes are
  stochastic. Constraint-level results (rejections, dialect errors) are
  deterministic and valid at n=1.
- **Selection bias is a known hazard.** The reachable set overrepresents
  popular, well-documented providers. Record what is *not* covered — enterprise
  deployments, regional endpoints, older model aliases — and state it as a
  limitation rather than generalizing past it.
- **Cost:** ~1 day, a few dollars of API spend.
- **Report three numbers:** unweighted divergence rate, **silent-failure rate**
  (the dangerous subset), and severity-weighted exposure — divergence rate ×
  plausible request volume × consequence severity × cost of detection.
- **Kill criterion, with a hard floor:** if the silent-failure rate is under 2%
  across that sample, P1 is false and we stop. Severity weighting may argue for
  continuing *above* that floor; it may not be used to rescue a result below
  it. A metric that can always be argued into significance is not a kill
  criterion.

### E2 — Semantic contract violation (tests P2)

The original framing — "does LiteLLM silently fall back?" — is too narrow and
too easy to dismiss as a configuration mistake. The real claim is more general
and harder to wave away:

> A gateway can return a response that passes every syntactic check while
> violating the caller's semantic contract.

Required: current web evidence, citations, JSON schema. Returned: valid JSON,
no current evidence, no citations. The parser passes. The task fails. Nothing
in the response says so.

Test the cases separately, since they fail differently:

| Case | Question |
|---|---|
| Primary times out | Does fallback preserve the contract? |
| Primary returns HTTP error | Does fallback preserve the contract? |
| Primary returns malformed output | Is fallback triggered at all? |
| Primary lacks a required capability | Is it rejected *before* execution? |
| Fallback lacks a required capability | Is degradation surfaced to the caller? |
| Streaming started before failure | Can the system recover safely mid-stream? |

- **Cost:** a few hours.
- **Kill criterion:** if callers receive a clear error or machine-readable
  degradation signal across these cases, existing gateways already preserve
  semantic contracts and P2 is false. Note that failing *one* case in the way
  we predicted is not sufficient — we need the general claim, not one
  misconfiguration.
- **If it reproduces, this is the most persuasive demo this project will ever
  have.** Record it properly the first time: full transcript, both responses,
  timestamps.

### E3 — Deployment divergence (tests P3)

The claim, stated precisely enough to be falsifiable:

> Observable capability is a function of provider, endpoint, model identifier,
> snapshot, region, API version, parameters, and adapter — not merely the model
> family.

**Start with the cheap pairs.** Bedrock and Azure onboarding is days of
request-and-approve work, and it is not needed to get a first answer. These
pairs are reachable now:

- Direct provider API vs. an OpenAI-compatible proxy for the same model
- Same model through LiteLLM vs. through a native adapter
- Same provider, two API versions
- Hosted model vs. the same weights on a local runtime
- Same model with and without schema/tool translation

If divergence shows up in these, P3 is supported without spending a week on
cloud-marketplace accounts. Bedrock and Azure then become confirmation and
commercial evidence, not the first test.

- **Kill criterion:** if the same model behaves consistently across all of
  these deployment axes, P3 is false, model-level registries suffice, and the
  deployment abstraction is unnecessary complexity we should delete.

### E4 — Buyer validation (tests whether anyone pays)

E1–E3 can all succeed and still leave no business. After there is evidence in
hand — not before, since this conversation only works with real findings —
interview engineers running regulated AI systems, Azure/Bedrock/Vertex
deployments, structured-extraction pipelines, and tool-using agents in
production. Show them the actual results and ask:

1. Have you hit this failure?
2. How would you detect it today?
3. Who owns it when it happens — and what did the last incident cost?
4. Would you consume a live conformance feed?
5. Hosted feed, self-hosted scanner, or both?
6. **Would your security and procurement process permit an external service to
   probe your production endpoints?**

Question 6 is the one that can quietly invalidate the hosted model. If the
answer is broadly no, the product is a **self-hosted scanner producing signed
reports**, not a hosted probing service — a materially different build. Better
to learn that from six conversations than from a year of engineering.

### Sequencing

E1 and E2 first — both runnable as soon as credentials exist. E3's cheap pairs
follow immediately (they reuse the E1 harness). E4 once there is evidence worth
showing. Cloud-marketplace onboarding only after E1–E3 survive.

## 8. How we test as we build

- **The conformance suite is both product and regression harness.** Every
  capability we claim to route on has a probe that verifies it. The suite runs
  continuously against live deployments, not against mocks.
- **Every contract has a test proving the guarantee holds or the request is
  refused.** `StructuredGeneration.v1` either returns schema-valid output or
  fails loudly. There is no third outcome.
- **Silent degradation is an assertion failure.** Tests deliberately induce
  fallback to an under-capable target and assert that `degraded: true` and the
  missing-capability list are present. A silent pass is a test failure.
- **Every probe result records** the exact request payload, model version,
  region, timestamp, and raw response — so any finding can be reproduced or
  disputed by a third party. Unreproducible findings get dismissed, correctly.
- **Integration tests hit real providers.** Mocked capability tests are
  worthless here; the entire premise is that documentation and reality diverge.

## 9. Open questions

- Does a verified capability feed become a product, or a feature LiteLLM
  absorbs? Current answer: the defensible asset is the accumulated historical
  drift archive plus neutrality, neither of which is code.
- Is the buyer the gateway vendors (small pool) or compliance-driven
  enterprises on Azure/Bedrock/Vertex (budget, real pain, no source of truth)?
  Current bet is the latter.
- Licensing. The engine is currently BSL 1.1, which guards commodity code while
  costing adoption against MIT-licensed LiteLLM. Recommendation is Apache-2.0
  for the engine with the commercial boundary around data and hosted service.
  Note that `README.md` currently contradicts itself on this point.

## 10. Immediate next action

**Run E1.** The harness exists — `~/Workspace/llm-conformance`, stdlib-only
Python, verified against a deliberately misbehaving mock provider. The only
blocker is credentials: `.env` exists with every key blank.

Two harness changes this document implies, both already identified there as v2
gaps:

- **n≥5 trials per response-level probe.** Single samples are stochastic and
  cannot support a conformance rate.
- **Record the full deployment tuple** from §7, not just provider and model.

Everything else in this document is downstream of whether declared capability
data is actually wrong.

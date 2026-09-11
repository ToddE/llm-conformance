# Capability Model

This document defines how LLM Conformance describes, tests, and reports AI
deployment capabilities.

The purpose is not to maintain a list of features that providers claim to
support. The purpose is to determine whether a specific deployment can satisfy
a specific application requirement under a reproducible test.

## Contents

- [Purpose](#purpose)
- [What Is a Capability?](#what-is-a-capability)
- [Capability Support Is Not Boolean](#capability-support-is-not-boolean)
- [Capability Structure](#capability-structure)
- [Capability Lifecycle](#capability-lifecycle)
- [Result Classifications](#result-classifications)
- [Guarantee Levels](#guarantee-levels)
- [Deployment Identity](#deployment-identity)
- [Declared and Observed Support](#declared-and-observed-support)
- [Probe Methodology](#probe-methodology)
- [Evidence and Reproducibility](#evidence-and-reproducibility)
- [Current Capabilities](#current-capabilities)
- [Structured Output](#structured-output)
- [Required Tool Calling](#required-tool-calling)
- [Planned Capabilities](#planned-capabilities)
- [Limitations](#limitations)

## Purpose

Applications increasingly depend on model capabilities that are described as
simple yes-or-no features:

- Structured output.
- JSON Schema support.
- Tool calling.
- Required tool selection.
- Vision input.
- Streaming.
- Web search.
- Citations.
- Long-context input.

In practice, these capabilities vary by:

- Provider.
- Endpoint.
- Model identifier.
- Model snapshot or version.
- Region.
- API version.
- Request parameters.
- Sampling settings.
- Adapter or proxy.
- Local inference runtime.
- Schema or tool dialect.

An endpoint may claim to support a feature while:

- Rejecting some valid requests.
- Accepting a request but ignoring part of it.
- Supporting only a subset of the requested schema.
- Producing inconsistent results.
- Behaving differently through an adapter or proxy.
- Being unable to express the requested constraint at all.

LLM Conformance exists to measure those differences.

## What Is a Capability?

A capability is an application-level behavior that a deployment may be required
to provide.

Examples include:

- Returning output that conforms to a caller-provided JSON Schema.
- Selecting a required tool.
- Producing tool arguments that conform to a declared schema.
- Accepting image input.
- Preserving structured output during streaming.
- Returning current web evidence with citations.
- Maintaining a context requirement up to a specified size.

A capability is not necessarily one indivisible feature. It is usually composed
of multiple constraints.

For example:

```text
structured_output
├── response is valid JSON
├── response conforms to the supplied schema
├── nested objects are supported
├── arrays are supported
├── enum values are enforced
├── nullable values are supported
├── additional properties are handled correctly
└── invalid output is rejected or otherwise controlled
```

A deployment may support some of these constraints and not others.

Therefore:

> Passing one probe does not establish that a deployment supports the entire
> capability.

## Capability Support Is Not Boolean

A simple registry entry such as this is insufficient:

```yaml
supports_structured_output: true
supports_tools: true
```

That representation cannot distinguish between:

- A deployment that strictly enforces a JSON Schema.
- A deployment that merely returns parseable JSON.
- A deployment that supports only simple object schemas.
- A deployment that cannot represent `additionalProperties`.
- A deployment that accepts a tool definition but ignores `tool_choice: required`.
- A deployment that passes some trials but fails others.

LLM Conformance represents capability support using:

- Capability definitions.
- Individual constraints.
- Provider expressions.
- Probe fixtures.
- Validators.
- Observed evidence.
- Result classifications.
- Guarantee levels.

The relationship is:

```text
Capability
    ↓
Constraint
    ↓
Provider expression
    ↓
Probe
    ↓
Validator
    ↓
Evidence
    ↓
Classification
    ↓
Guarantee level
```

## Capability Structure

Each capability should define the following information.

### Capability identifier

A stable, machine-readable name.

```text
structured_output
required_tool_call
vision_input
streaming_integrity
web_grounded_citations
```

Capability identifiers should remain stable as implementations evolve.

### Capability version

A versioned definition of what the capability means.

```text
structured_output.v1
required_tool_call.v1
```

A capability version should change when its requirements or interpretation
change materially.

### Description

A plain-language explanation of the behavior being tested.

### Constraints

The specific requirements that make up the capability.

### Provider expression

The mechanism by which a provider represents the requirement.

Examples include:

- `response_format`.
- `json_schema`.
- `tools`.
- `tool_choice`.
- Provider-specific schema fields.
- HTTP request headers.
- Model-specific request parameters.
- Runtime-specific configuration.

### Probes

The executable tests used to evaluate the capability.

### Validators

The rules used to determine whether the response satisfies the probe.

### Evidence requirements

The information that must be recorded for the result to be reproducible.

A conceptual capability definition may look like this:

```yaml
id: structured_output
version: "1.0"

description: >
  The deployment returns output conforming to a caller-provided JSON Schema.

constraints:
  - id: json_parseable
    required: true

  - id: schema_conforming
    required: true

  - id: nested_objects
    required: false

  - id: enum_values
    required: false

  - id: additional_properties
    required: false

  - id: strict_enforcement
    required: false

probes:
  - structured-output-basic
  - structured-output-nested
  - structured-output-enum
  - structured-output-long-prompt
```

## Capability Lifecycle

Capabilities move through a defined lifecycle.

### Proposed

The capability has been identified but has not yet been implemented or tested.

### Experimental

The capability has probes and validators, but coverage is limited and results
should not be treated as production guarantees.

### Verified under probe suite

The deployment passed the defined tests for the capability under the recorded
conditions.

This does not mean that every possible prompt, schema, tool, or future version
will behave identically.

### Stable

The capability definition, probes, validators, and reporting format have been
used repeatedly and are considered reliable for supported deployment types.

### Deprecated

The capability definition is no longer recommended. Existing reports may still
refer to it for historical reasons.

### Removed

The capability is no longer supported by the current tool version.

## Result Classifications

Each probe produces a classification rather than only `pass` or `fail`.

### `pass`

The deployment satisfied the tested requirement.

Example:

```text
The response was valid JSON and conformed to the supplied schema.
```

### `honest_failure`

The deployment rejected the request explicitly and identified the unsupported,
invalid, or unavailable requirement.

Example:

```text
HTTP 400: this endpoint does not support strict JSON Schema output.
```

An honest failure is generally safer than a misleading success.

### `silent_failure`

The deployment accepted the request but ignored or violated a requirement
without clearly reporting the problem.

Examples:

- The request specifies a schema, but the response violates it.
- `tool_choice: required` is accepted, but no tool is selected.
- A provider accepts a structured-output parameter but returns unconstrained
  text.
- A proxy removes a constraint before the request reaches the model.

Silent failure is the most dangerous result because the application may believe
that the requirement was honored.

### `unrepresentable`

The provider's API or schema dialect cannot express the requested constraint.

Examples:

- The provider supports structured output but cannot represent a particular
  JSON Schema keyword.
- The provider supports tools but cannot express required selection.
- The provider supports image input but cannot represent the requested image
  format or detail level.

An unrepresentable constraint is different from a violated constraint. The
deployment could not be tested for that requirement because the request could
not express it.

### `partial`

The deployment satisfies some tested constraints but not all.

Example:

```text
- JSON parsing: passed
- Nested object schema: passed
- Enum enforcement: passed
- Additional properties constraint: unrepresentable
- Strict enforcement: not demonstrated
```

**Not implemented.** This needs a different result shape than the harness
currently produces: a per-constraint vector (one verdict per schema keyword,
as in the example above) rather than one collapsed verdict per trial.
`conformance.jsonschema.validate()` already returns the list of individual
violations that a `partial` classification would be built from, and the
structural-vs-advisory keyword tiering added to fix a `maxLength`
false-positive is a step toward this, but it still only feeds a binary
decision today. Building `partial` properly means changing what
`check_structured_payload()` and `check_tool_arguments()` in
`conformance/adapters/base.py` return, which changes every stored result's
shape, beyond adding a new outcome value alongside the existing ones.

### `inconsistent`

Repeated trials produce materially different results under the same recorded
conditions.

Example:

```text
4 of 5 trials conformed to the schema
1 of 5 trials violated the schema
```

An inconsistent result must not be treated as strict support.

**Implemented.** `conformance.outcomes.cell_verdict()` aggregates a
deployment+capability cell's conclusive-trial conformance classes: unanimous
agreement returns that class, any split returns `inconsistent`. Surfaced in
the report's `Rates` section (cell level), the `INCONSISTENT cells` list, and
`summary_dict()["inconsistent_cells"]`. Confirmed against real evidence: an
Anthropic `claude-sonnet-5` structured-output cell where 3 of 5 trials
exceeded `maxLength` and 2 did not reports as `inconsistent` rather than as
either a clean pass or a clean failure.

### `inconclusive`

The result cannot be attributed confidently.

Possible causes include:

- Provider outage.
- Network failure.
- Timeout before a response.
- Rate limiting.
- Invalid credentials.
- Unknown request transformation.
- Insufficient trial count.
- Ambiguous provider error.
- Unidentified model version.
- Unexpected proxy behavior.

An inconclusive result is not evidence of either support or non-support.

## Guarantee Levels

Observed behavior and promised behavior are separate concepts.

A deployment may produce conforming output in repeated trials without providing
a strict guarantee. Results must therefore distinguish what was observed from
what can safely be promised to an application.

### `unsupported`

The deployment does not support the requested capability or constraint.

### `honest_failure`

The deployment clearly rejected the request as unsupported or invalid.

### `best_effort`

The deployment usually satisfies the requirement, but the evidence does not
support a strict guarantee.

Example:

```text
Schema conformance: 49 of 50 trials
Guarantee: best_effort
```

### `partial`

The deployment supports some constraints within the capability but not all.

### `verified_under_probe_suite`

The deployment passed the defined probe suite under the recorded conditions.

This means:

```text
The deployment passed these tests.
```

It does not mean:

```text
The deployment will satisfy every possible request.
```

### `strict`

A strict guarantee should be reported only when:

- The provider explicitly enforces the constraint.
- The provider's API can represent the complete constraint.
- The probe confirms enforcement behavior.
- The result is deterministic or the provider documents an explicit guarantee.
- No unsupported portion of the requirement has been hidden by an adapter.

A successful trial alone is not sufficient to report `strict`.

### `inconclusive`

There is not enough reliable evidence to assign a support or guarantee level.

## Deployment Identity

A capability result belongs to a deployment configuration, not merely to a
model family.

Every result should record the deployment identity:

```text
provider
endpoint
model_id
model_snapshot
region
api_version
request_parameters
sampling_settings
adapter
proxy_path
```

A more complete identity can be represented as:

```yaml
deployment:
  provider: example-provider
  endpoint: [https://api.example.com/v1](https://api.example.com/v1)
  model_id: example-model
  model_snapshot: example-model-2026-09-01
  region: us-east-1
  api_version: "2026-08-01"
  adapter: native
  proxy_path: none
  request_parameters:
    response_format: json_schema
    tool_choice: required
  sampling_settings:
    temperature: 0
```

The same model may behave differently when accessed through:

- A native provider API.
- An OpenAI-compatible proxy.
- LiteLLM.
- A cloud marketplace.
- A regional endpoint.
- A different API version.
- A local inference runtime.
- A provider-specific adapter.

A result without sufficient deployment identity should be marked incomplete or
inconclusive.

## Declared and Observed Support

Capability data has at least two sources:

```text
declared support
observed support
```

### Declared support

What the provider, registry, adapter, or deployment configuration claims.

Examples:

```yaml
declared:
  supported: true
  source: provider-documentation
```

or:

```yaml
declared:
  supported: true
  source: models.dev
```

Declared support is useful for comparison and test selection, but it is not
proof of behavior.

### Observed support

What happened when the deployment was tested.

Example:

```yaml
observed:
  parameter_accepted: true
  json_parseable_rate: 1.0
  schema_conforming_rate: 0.98
  strict_enforcement: false
```

Observed support must include:

- Probe identifier.
- Probe version.
- Trial count.
- Passed and failed trial counts.
- Classification.
- Timestamp.
- Deployment identity.
- Relevant request parameters.
- Response metadata.
- Error information.
- Evidence references.

### Comparison

The important comparison is not simply:

```text
declared: true
observed: true
```

It is:

```text
declared:
  structured_output: supported

observed:
  json_parseable: true
  schema_conforming: partial
  strict_enforcement: false
  additional_properties: unrepresentable
```

This shows that a broad provider claim may not satisfy a specific application
contract.

## Probe Methodology

A probe is a repeatable test of one or more capability constraints.

Each probe should define:

- Probe identifier.
- Probe version.
- Capability identifier.
- Constraint identifiers.
- Request template.
- Provider-specific expression.
- Expected behavior.
- Validation rules.
- Classification rules.
- Evidence requirements.

A conceptual probe definition:

```yaml
id: structured-output-nested
version: "1.0"
capability: structured_output

tests:
  - json_parseable
  - schema_conforming
  - nested_objects

request:
  schema:
    type: object
    required:
      - user
    properties:
      user:
        type: object
        required:
          - id
          - name
        properties:
          id:
            type: integer
          name:
            type: string

validation:
  parse_json: true
  validate_json_schema: true

classification:
  invalid_json: silent_failure
  schema_mismatch: silent_failure
  explicit_unsupported_error: honest_failure
  provider_cannot_express_schema: unrepresentable
  schema_match: pass
```

### Repeated trials

Response-level behavior is potentially stochastic. A single successful response
does not establish a reliable conformance rate.

Response-level probes should record:

- Trial count.
- Trial index.
- Individual result.
- Aggregate result.
- Pass count.
- Failure count.
- Classification count.

Example:

```yaml
trials:
  count: 5
  passed: 4
  failed: 1
  classifications:
    pass: 4
    silent_failure: 1
```

Constraint-level outcomes may be deterministic. For example, a provider may
return a consistent error stating that a schema keyword is unsupported. Such a
result may be valid after one trial, although repeated testing is still useful
for detecting inconsistent behavior.

### Probe depth

Initial probes should test meaningful variations rather than only the
simplest successful case.

Examples include:

- Basic object schemas.
- Nested objects.
- Arrays.
- Enumerations.
- Nullable fields.
- Additional properties.
- Multiple tools.
- Long prompts.
- Invalid-tool temptation.
- Conflicting tool descriptions.
- Required versus automatic tool selection.

### Sampling controls

Where possible, probes should use deterministic or controlled sampling
parameters.

The result must still record the sampling settings because a capability observed
at temperature zero may not behave identically under other settings.

### Provider-specific expressions

A provider adapter may translate a normalized capability requirement into a
provider-specific request.

For example:

```text
normalized requirement:
  structured_output.schema_conforming = required

provider A:
  response_format.type = json_schema

provider B:
  responseSchema = ...

provider C:
  unsupported

provider D:
  OpenAI-compatible response_format is accepted but not enforced
```

The adapter must preserve the distinction between:

- Expressible and supported.
- Expressible but violated.
- Unrepresentable.
- Unknown.

It must not convert every unsupported or unrepresentable condition into a
generic boolean failure.

## Evidence and Reproducibility

Every probe result should record enough information for another party to
understand, reproduce, or dispute the finding.

At minimum, record:

- Deployment identity.
- Probe identifier and version.
- Capability and constraint identifiers.
- Timestamp.
- Trial index.
- Request metadata.
- Relevant request parameters.
- HTTP status.
- Provider error.
- Response metadata.
- Validation output.
- Classification.
- Tool or schema definition hash.
- Request and response hashes.
- Runtime and adapter version.

Example:

```yaml
evidence:
  probe_id: structured-output-nested
  probe_version: "1.0"
  trial: 3
  observed_at: "2026-09-10T21:00:00Z"
  request_hash: sha256:...
  response_hash: sha256:...
  adapter_version: "0.1.0"
  validator_version: "0.1.0"
```

### Raw responses

Raw requests and responses may contain:

- User data.
- Private documents.
- API keys accidentally included in prompts.
- Proprietary schemas.
- Sensitive tool definitions.
- Personal information.

Raw evidence should not be committed to a public repository unless it has
been reviewed and sanitized.

Public reports may contain hashes, redacted content, metadata, and reproducible
fixtures instead of the complete raw response.

### Historical results

Capability behavior can change over time. Historical records should not be
overwritten merely because a newer probe produces a different result.

Instead, retain:

- Previous result.
- New result.
- Observation timestamp.
- Deployment identity.
- Probe version.
- Change classification.

Example:

```text
2026-09-01: schema conformance passed
2026-09-10: schema conformance partial
```

This allows the project to detect capability drift.

## Current Capabilities

The initial project focuses on two capability families.

| Capability | Status | Initial coverage |
| --- | --- | --- |
| Structured output | Experimental | JSON parsing, JSON Schema conformance, nested schemas, enums, repeated trials |
| Required tool calling | Experimental | Required selection, multiple tools, invalid-tool temptation, argument conformance |
| Deployment identity | Implemented | Provider, endpoint, model, snapshot, region, API version, adapter, parameters |
| Declared-versus-observed comparison | In progress | Registry claims compared with probe evidence |
| Contract enforcement | Planned | Refuse or report requests that cannot be satisfied |
| Streaming integrity | Planned | Partial output, interruption, and fallback behavior |
| Vision input | Planned | Image acceptance and modality behavior |
| Web-grounded citations | Planned | Freshness, evidence, and citation requirements |

The current capability list is deliberately small. The project is testing whether
capability divergence exists and matters before expanding to additional
modalities and workflows.

## Structured Output

### Definition

Structured output is the ability of a deployment to produce output that
conforms to a caller-provided structural definition, such as a JSON Schema.

### Initial constraints

The initial structured-output probes may test:

- The request parameter is accepted.
- The response is valid JSON.
- The response conforms to the supplied schema.
- Nested objects are supported.
- Arrays are supported.
- Enumerations are supported.
- Nullable values are supported.
- Additional properties are handled correctly.
- Invalid output is rejected, constrained, or otherwise reported.
- The behavior is repeatable across trials.

### Example schema

```json
{
  "type": "object",
  "required": ["user"],
  "properties": {
    "user": {
      "type": "object",
      "required": ["id", "name"],
      "properties": {
        "id": {
          "type": "integer"
        },
        "name": {
          "type": "string"
        }
      },
      "additionalProperties": false
    }
  },
  "additionalProperties": false
}
```

### Important distinctions

The following are not equivalent:

```text
valid JSON
```

```text
JSON matching the supplied schema
```

```text
JSON matching the supplied schema under repeated trials
```

```text
provider-enforced strict schema conformance
```

A deployment may satisfy one level without satisfying the next.

### Unrepresentable schema features

A provider may support structured output while being unable to represent a
particular schema feature.

Examples may include:

- `additionalProperties`.
- Certain combinations of `oneOf` or `anyOf`.
- Recursive schemas.
- Complex unions.
- Conditional requirements.
- Provider-specific limits on nesting or schema size.

If the provider cannot express the requested schema feature, the result should
be classified as `unrepresentable`, not as a normal schema violation.

### Example result

```yaml
capability: structured_output
probe: structured-output-nested
trials:
  count: 5
  passed: 4
  failed: 1

observed:
  parameter_accepted: true
  json_parseable_rate: 1.0
  schema_conforming_rate: 0.8
  strict_enforcement: false

classification: inconsistent
guarantee: best_effort
```

## Required Tool Calling

### Definition

Required tool calling is the ability of a deployment to select and invoke a
tool when the request requires one.

This is distinct from merely accepting a tool definition.

### Initial constraints

The initial tool-calling probes may test:

- Tool definitions are accepted.
- A required tool is selected.
- The selected tool is one of the declared tools.
- Tool arguments conform to the declared schema.
- Multiple tools can be represented.
- The required tool is selected when another tool is more tempting.
- Invalid or irrelevant tools are not selected.
- Tool choice is preserved through an adapter or proxy.
- Tool calls remain valid under repeated trials.

### Example requirement

```yaml
capability: required_tool_call
constraint:
  tool_choice: required
  required_tool: lookup_customer
  arguments_must_conform: true
```

### Tool choice distinctions

The following behaviors should be treated separately:

```text
tools accepted
```

```text
model may choose a tool
```

```text
model must choose a tool
```

```text
model must choose a specific tool
```

```text
selected tool arguments conform to the declared schema
```

A deployment that accepts tools but ignores a required tool-selection constraint
should not be classified as fully supporting required tool calling.

### Invalid-tool temptation

A useful probe includes multiple tools where one is clearly relevant and
another is designed to be tempting but invalid for the task.

This helps identify deployments that:

- Ignore `tool_choice`.
- Choose tools based only on superficial wording.
- Produce tool calls that do not satisfy the request.
- Behave differently when several tools are available.

### Example result

```yaml
capability: required_tool_call
probe: required-tool-invalid-temptation
trials:
  count: 5
  passed: 5
  failed: 0

observed:
  tools_accepted: true
  required_tool_selected_rate: 1.0
  argument_schema_conforming_rate: 1.0

classification: pass
guarantee: verified_under_probe_suite
```

This does not establish universal tool-calling reliability. It establishes that
the deployment passed this probe under the recorded conditions.

## Planned Capabilities

The following capabilities are planned but are not part of the initial
conformance judgment.

### Streaming integrity

Potential constraints include:

- Tokens begin streaming within the expected protocol.
- Stream events follow the documented format.
- Structured output remains valid after reassembly.
- Tool calls are not truncated silently.
- A provider failure after partial output is reported.
- Fallback does not produce an invalid mixed response.
- Cancellation is handled correctly.

Streaming requires a separate harness because partial output and mid-stream
failure cannot be evaluated like a normal completed response.

### Vision input

Potential constraints include:

- Image input is accepted.
- Supported image formats are documented and honored.
- Multiple images are handled correctly.
- Image detail or resolution parameters are honored.
- Text-only fallback is not silently substituted.
- Image content is actually incorporated into the response.

### Web-grounded citations

Potential constraints include:

- Web retrieval actually occurs.
- Results satisfy freshness requirements.
- Citations refer to retrieved sources.
- Claims are supported by cited sources.
- Citation metadata survives provider and adapter translation.
- A fallback without web access is identified as degraded.
- The response distinguishes retrieved evidence from model knowledge.

### Long-context behavior

Potential constraints include:

- The deployment accepts the claimed context size.
- Earlier content remains available at the claimed length.
- Truncation is reported rather than silent.
- Tool definitions remain intact under long context.
- Structured output remains valid near context limits.

### Multimodal output

Potential constraints may include:

- Image generation.
- Audio generation.
- Audio transcription.
- Video input.
- Multimodal streaming.
- Cross-modal tool interactions.

Each modality requires dedicated fixtures and validators.

## Contract Enforcement

Conformance testing and contract enforcement are related but separate.

Conformance testing asks:

```text
What did this deployment actually do?
```

Contract enforcement asks:

```text
Should this deployment be allowed to handle this request?
```

A future contract-enforcement layer may use conformance results to:

- Reject ineligible deployments.
- Prevent silent fallback.
- Report missing capabilities.
- Mark responses as degraded.
- Select another eligible deployment.
- Require explicit user approval before best-effort execution.

A contract result may look like:

```yaml
requested:
  - structured_output
  - required_tool_call

deployment:
  id: provider-model-direct

provided:
  - structured_output

missing:
  - required_tool_call

degraded: true
action: refuse
```

Contract enforcement is not part of the initial measurement experiment. It should
be built only after the capability model and evidence format have been validated.

## Capability Drift

Capability results are time-dependent.

A deployment may change because of:

- Model updates.
- Provider backend changes.
- API-version changes.
- Region changes.
- Adapter updates.
- Proxy behavior.
- Schema-parser changes.
- Runtime changes.
- Safety or policy changes.

A previously passing deployment may later become partial, inconsistent, or
unrepresentable for the same probe.

Therefore, conformance results should include:

- Observation timestamp.
- Probe version.
- Validator version.
- Deployment identity.
- Adapter version.
- Result history.

A capability feed should preserve historical results rather than replacing them
with only the latest observation.

## Limitations

LLM Conformance does not prove that a model is:

- Generally intelligent.
- Factually accurate.
- Safe for every use case.
- Secure.
- Free from hallucinations.
- Reliable for every prompt.
- Compliant with every regulation.
- Suitable for a particular business process.

A conformance result is limited to:

- The deployment that was tested.
- The request shape that was used.
- The constraints that were represented.
- The probes that were executed.
- The conditions under which they ran.
- The evidence that was recorded.

Passing the probe suite means:

```text
This deployment satisfied these tested requirements under these conditions.
```

It does not mean:

```text
This deployment will satisfy every possible request.
```

## Design Principle

The central principle of LLM Conformance is:

> A capability claim is meaningful only when its requirements are explicit, its
> constraints are representable, its behavior is tested, and its evidence is
> attributable to a specific deployment.

Capability support should never be treated as a simple boolean when the
application depends on a guarantee.
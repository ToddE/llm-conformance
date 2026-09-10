## Capabilities

LLM Conformance tests whether an AI deployment can satisfy specific
capability requirements—not merely whether a provider claims to support a
feature.

Initial capabilities include:

- Structured JSON output.
- Required tool calling.
- Schema and argument conformance.
- Deployment-specific behavior across providers, endpoints, and adapters.

Capability support is not treated as a simple true/false value. A deployment
may support a capability fully, partially, inconsistently, honestly reject it,
or be unable to express a requested constraint.

See [Capabilities.md](Capabilities.md) for the capability model, probe
definitions, result classifications, and current coverage.


## Project status: Early experimental research. 
The project has no external users yet and is
currently validating whether declared AI capabilities differ materially from
observed deployment behavior.

The initial experiments focus on:

- Structured JSON output.
- Required tool calling.
- Differences between deployment configurations.

**This project is not yet a production compliance tool, certification system,
model leaderboard, or general-purpose AI gateway.**


## Why this matters

AI applications increasingly depend on guarantees such as:

- "The response will match this JSON Schema."
- "The model will call one of these tools."
- "This fallback supports the same capabilities as the primary model."

Those guarantees are not always portable between providers, models, regions,
API versions, proxies, or local runtimes.

An API may return HTTP 200 and valid JSON while still failing to honor the
actual requirement. That makes a syntactically successful response
semantically incorrect—and difficult to detect in production.

LLM Conformance is intended to make those differences measurable and
reproducible.

## Capability support is not a boolean

LLM Conformance does not reduce a deployment to flags such as:

```yaml
supports_structured_output: true
supports_tools: true
```

Those flags are too coarse for production systems.

Instead, each capability is described by:

- The behavior an application requires.
- The constraints needed to provide that behavior.
- The way a provider expresses those constraints.
- The probe used to test them.
- The observed result.
- The level of guarantee supported by the evidence.

For example, "structured output" may include separate requirements for:

- Valid JSON.
- JSON Schema conformance.
- Nested objects.
- Arrays.
- Enumerations.
- Nullable values.
- Additional properties.
- Strict rejection of invalid output.
- Streaming structured output.


## What is a deployment?

A model name alone is not enough to identify behavior.

A test result is associated with a deployment configuration, including:

- Provider.
- Endpoint.
- Model identifier.
- Model snapshot or version.
- Region.
- API version.
- Request parameters.
- Sampling settings.
- Adapter or proxy path.

The same model may behave differently when accessed through different
providers, regions, API versions, adapters, or compatibility layers.

## Result categories

Each probe classifies behavior rather than returning only pass or fail:

| Result | Meaning |
| --- | --- |
| Pass | The deployment satisfied the tested requirement. |
| Honest failure | The deployment rejected the unsupported request explicitly. |
| Silent failure | The deployment accepted the request but did not honor the requirement. |
| Unrepresentable | The provider's schema or API dialect could not express the requirement. |
| Inconclusive | The result could not be attributed confidently. |

Silent failure is the most dangerous result because the application may believe
the requirement was honored.


## Quick start

```bash
git clone [https://github.com/ToddE/llm-conformance.git](https://github.com/ToddE/llm-conformance.git)
cd llm-conformance

python -m venv .venv
source .venv/bin/activate
pip install -e .

cp .env.example .env
# Add provider credentials to .env

llm-conform probe --config deployments.yaml
```

The exact commands may change while the project is experimental.

## Configuration

Deployment configuration is written in YAML because it is easier for people to
read and edit:

```yaml
deployments:
  - id: openai-direct
    provider: openai
    endpoint: [https://api.openai.com/v1](https://api.openai.com/v1)
    model: example-model
    adapter: native
    region: global
    api_version: latest
    credentials:
      api_key_env: OPENAI_API_KEY

  - id: local-ollama
    provider: ollama
    endpoint: http://localhost:11434
    model: example-model
    adapter: native
    region: local
    credentials: {}
```

Credentials are referenced by environment-variable name and are never stored
in this file.

## Evidence and reproducibility

Probe results record the deployment identity, probe version, timestamp, trial
number, request metadata, response metadata, and validation result.

Raw prompts and responses may contain sensitive information. Do not commit
credentials, private endpoint details, or unsanitized production responses.

## Roadmap

- [ ] Validate structured-output probes.
- [ ] Validate required-tool-call probes.
- [ ] Run repeated trials across reachable deployments.
- [ ] Compare observed behavior with declared capability metadata.
- [ ] Test deployment differences across adapters and providers.
- [ ] Publish reproducible reports.
- [ ] Evaluate whether a conformance feed or enforcement layer is useful.

## Non-goals

LLM Conformance is not currently intended to be:

- A model leaderboard.
- A latency benchmark.
- An AI agent framework.
- A general-purpose LLM gateway.
- A replacement for LiteLLM.
- A guarantee that a model is generally safe or accurate.

## License

Apache-2.0
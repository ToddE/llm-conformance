## Capabilities

LLM Conformance tests whether an AI deployment can satisfy specific
capability requirements. A provider claiming to support a feature is a
separate question.

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
semantically incorrect, and difficult to detect in production.

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

No install step. Standard library only.

```bash
git clone https://github.com/ToddE/llm-conformance.git
cd llm-conformance

cp .env.example .env
# add provider credentials to .env

python3 run.py doctor     # what can be probed right now
python3 run.py probe      # probe everything reachable
```

`run.py probe` writes a complete local run bundle first, then publishes to any
remote destination configured in `outputs.toml`. If a remote publish fails,
the local bundle is already valid. Retry it without re-probing:

```bash
python3 run.py publish runs/2026-09-11/run_20260911T015009Z_e045f4e9
```

## Configuration

Deployment configuration is written in TOML in `deployments.toml`. TOML over
YAML because `tomllib` has been in the Python standard library since 3.11 and
reads without installing anything; YAML would need an external parser.

See [CONFIG.md](CONFIG.md) for the complete field reference for both
`deployments.toml` and `outputs.toml`, including every accepted value for
fields like `wire`, `models`, `type`, `visibility`, and `artifacts`. Every
field with a fixed set of legal values is validated at load time and names
the full set in its error message, so a typo fails immediately rather than
silently doing less than configured.

```toml
config_version = 1

[[providers]]
id = "openai"
wire = "openai"
base_url = "https://api.openai.com/v1"
env_prefix = "OPENAI"
models = "auto"

[[providers]]
id = "ollama"
wire = "openai"
base_url = "http://localhost:11434/v1"
auth = "none"
local = true
models = "auto-local"
```

Credentials are referenced by environment-variable name and never stored in
this file. Adding a provider that speaks an existing wire format is a block
in this file, not a code change. See "Adding a provider" below.

Output destinations (local disk, S3-compatible storage) are configured
separately in `outputs.toml`, since where results go and what gets probed vary
independently. A run always writes to local disk first. Publishing to a
remote destination is a second step that reads the completed local bundle and
never triggers a new probe.

```toml
config_version = 1

[run]
output_dir = "./runs"

[[outputs]]
id = "local"
type = "filesystem"
required = true
path = "./runs"
artifacts = ["manifest", "summary", "results", "report", "raw_evidence"]

[[outputs]]
id = "r2"
type = "s3"
enabled = false
bucket_env = "S3_BUCKET"
access_key_id_env = "S3_ACCESS_KEY_ID"
secret_access_key_env = "S3_SECRET_ACCESS_KEY"
artifacts = ["manifest", "summary", "results", "report"]
```

`type = "s3"` speaks the standard S3 REST API with hand-rolled AWS SigV4
signing over `urllib` and `hmac`, no `boto3` dependency. AWS S3, Cloudflare
R2, and MinIO all implement the same API, so one destination type covers all
three. Credentials come from the environment variables named in `outputs.toml`,
never from the file itself.

## Evidence and reproducibility

A run produces an immutable directory:

```
runs/2026-09-11/run_20260911T015009Z_e045f4e9/
  manifest.json      sha256 of every other artifact
  summary.json        aggregate counts
  results.jsonl        one line per trial, append-only
  report.md            the rendered report
  evidence/
    trial-0001.json    the same record as one results.jsonl line
    trial-0002.json
```

The manifest lets anyone re-hash the bundle and confirm nothing changed after
the run. `python3 run.py publish <run_dir>` checks this before publishing and
warns if the bundle no longer matches its own manifest.

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
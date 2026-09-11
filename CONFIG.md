# Configuration reference

Field-by-field reference for `deployments.toml` and `outputs.toml`. Both are
loaded by `tomllib` (standard library, Python 3.11+); the loaders live in
`conformance/catalog.py` and `conformance/outputs_config.py` and that is the
authoritative source if this drifts from the code. `config_version = 1` on
both files is checked at load time; a mismatch fails loudly rather than
guessing at a schema it does not understand.

## deployments.toml

Top level:

| Field | Required | Meaning |
|---|---|---|
| `config_version` | yes | Must equal `1`. |
| `[wire_formats]` | no | Reference table only. Not read by the loader; documents what each `wire` value in `WIRE_FORMATS` (conformance/catalog.py) actually speaks. |
| `[[providers]]` | yes, one or more | One deployment surface per block. |

Each `[[providers]]` block:

| Field | Required | Meaning |
|---|---|---|
| `id` | yes | Unique surface name. Used as the `--provider` CLI value and in evidence records. |
| `wire` | yes | Which adapter implementation to use. Must be a key in `WIRE_FORMATS`: `openai`, `anthropic`, `gemini`, or `ollama-native`. An unknown value fails to load with a message naming the problem, since that is the one case config cannot cover on its own. |
| `base_url` | yes | The API root this surface is called at. |
| `env_prefix` | one of this or `local`/`auth="none"` | Credential discovery prefix. `<env_prefix>_API_KEY` is the primary credential variable; see [profiles.py](conformance/profiles.py) for the region/multi-account profile convention built on top of it. |
| `env_fallbacks` | no | Additional env var names tried if `<env_prefix>_API_KEY` is unset, for providers with more than one historical key name (Gemini's `GOOGLE_API_KEY` vs `GOOGLE_GENERATIVE_AI_API_KEY`, for instance). |
| `registry_id` | no, defaults to `id` | The provider key this surface's models are looked up under in the pinned models.dev snapshot. |
| `api_version` | no | Passed through to the adapter (currently used by the Anthropic wire's `anthropic-version` header). |
| `max_tokens_field` | no | Overrides the adapter's default output-length parameter name (`max_tokens` vs `max_completion_tokens`, which differs across OpenAI-compatible surfaces). |
| `max_tokens_value` | no | Overrides the adapter's default output-length value. |
| `auth` | no | Set to `"none"` for an unauthenticated local surface. |
| `local` | no, default `false` | Marks a surface as a local runtime: no credential required, included in `local_surfaces()`, scanned by `run.py discover`. |
| `enabled` | no, default `true` | `false` keeps the entry in the catalog as a template without probing it. `run.py probe` skips it unless named explicitly; `run.py discover` still checks whether it is reachable and tells you to flip this. |
| `models` | no, default `"auto"` | `"auto"` pulls the most recently updated models declaring both probed capabilities from the pinned models.dev snapshot. `"auto-local"` asks the running local server what it actually has loaded. Anything else is treated as an explicit model list (rare; `--models` on the CLI is the usual way to pin one). |
| `note` | no | Free text, printed as the generated adapter class's docstring. |
| `doc` | no | Link to the provider's own API documentation. Displayed by `run.py discover` next to an offline local surface, so the next step is a click rather than a guess. |

### `wire`, accepted values

The complete set, from `WIRE_FORMATS` in `conformance/catalog.py`. Adding a
value here means writing an adapter; there is no config-only way to add a
fifth.

| Value | Adapter | Speaks |
|---|---|---|
| `"openai"` | `OpenAICompatibleAdapter` | `response_format.json_schema`, `tool_choice` |
| `"anthropic"` | `AnthropicAdapter` | forced tool + `input_schema`, `tool_choice.{type}` |
| `"gemini"` | `GoogleAdapter` | `responseSchema`, `functionCallingConfig` |
| `"ollama-native"` | `OllamaNativeAdapter` | `format=<schema>`, no `tool_choice` equivalent |

Loading a `[[providers]]` block with any other `wire` value fails immediately
with a message naming the bad value and listing these four.

## outputs.toml

Top level:

| Field | Required | Meaning |
|---|---|---|
| `config_version` | yes | Must equal `1`. |
| `[run]` | yes | `output_dir` (default `./runs`) and `hash_algorithm` (informational; the manifest always uses sha256 regardless). |
| `[[outputs]]` | yes, one or more | One publish destination per block. |

Each `[[outputs]]` block:

| Field | Required | Meaning |
|---|---|---|
| `id` | yes | Destination name. Used with `run.py publish --output <id>` to target a retry at one destination. |
| `type` | yes | `filesystem` or `s3`. `s3` covers AWS S3, Cloudflare R2, and MinIO, since all three speak the same signed REST API. |
| `enabled` | no, default `true` | `false` excludes the destination from both automatic publish and `run.py publish` (a filtered `--output` targeting a disabled id publishes nothing). |
| `required` | no, default `false` for `s3`, `true` for `filesystem` | A required destination that cannot publish counts as a run failure and is reported as such; an optional one is silently skipped when unavailable (missing credentials, unreachable endpoint). |
| `visibility` | no, default `"private"` | `"private"` or `"public"`. `"public"` is enforced, not advisory: it restricts `artifacts` to `manifest`, `summary`, `report` at load time. See below. |
| `artifacts` | no, default every value | Which files this destination receives. `"all"` is shorthand for every value in the table below. |
| `path` | `filesystem` only | Target directory. |
| `endpoint_env`, `region_env`, `bucket_env`, `access_key_id_env`, `secret_access_key_env` | `s3` only | Names of environment variables holding the actual values. The TOML file itself never contains a credential, only the name of where to find one. |
| `prefix` | `s3` only, default `""` | Key prefix under the bucket. |

### `type`, accepted values

From `DESTINATION_TYPES` in `conformance/storage.py`. An unknown value fails
to load with a message naming it and listing these two.

| Value | Class | Covers |
|---|---|---|
| `"filesystem"` | `FilesystemOutput` | Local disk. The run's local-first write target. |
| `"s3"` | `S3Output` | Any S3-compatible signed REST API: AWS S3, Cloudflare R2, MinIO. One implementation, since they share the wire protocol. |

### `artifacts`, accepted values

From `KNOWN_ARTIFACTS` in `conformance/storage.py`. This is the single
source of truth both the filesystem and S3 destinations validate against, so
adding a sixth artifact name means editing that dict, not either destination
class.

| Value | File(s) it maps to | Contains |
|---|---|---|
| `"manifest"` | `manifest.json` | sha256 and byte count of every other artifact in the run |
| `"summary"` | `summary.json` | Aggregate counts (`conformance.report.summary_dict()`) |
| `"results"` | `results.jsonl` | The full evidence stream, one line per trial |
| `"report"` | `report.md` | The rendered declared-vs-actual report |
| `"raw_evidence"` | `evidence/trial-*.json` | `results.jsonl` exploded to one file per trial. **The largest and most sensitive artifact.** It carries full request/response bodies. Omit it from any destination that is not private and trusted. |

A typo here (`"raw_evidance"`, a missing `s`) is rejected at load time rather
than silently publishing zero files for it and saying nothing. That failure
mode, an operation that looks like it succeeded but quietly did less than
configured, is exactly the class of bug this project exists to catch in other
systems, so it gets caught here too rather than getting a pass.

### `visibility`, accepted values

| Value | Meaning |
|---|---|
| `"private"` (default) | No restriction on `artifacts`. |
| `"public"` | `artifacts` is restricted to `manifest`, `summary`, `report`. Listing `results` or `raw_evidence` under `visibility = "public"` fails to load. |

This exists because `results.jsonl` and `evidence/trial-*.json` are equally
sensitive: `results.jsonl` embeds the exact same record (full request and
response bodies) that `raw_evidence` splits one file per trial. A destination
meant to be world-readable must not receive either by a one-line mistake, so
the check runs at config-load time rather than depending on whoever edits the
`artifacts` list to remember the rule.

None of this needs to be memorized to add a provider or a destination: copy the
nearest existing block in either file and change the values that differ. Every
field above that has a fixed set of legal values is validated at load time and
names the full set in its error message; nothing here relies on getting the
spelling right by memory.

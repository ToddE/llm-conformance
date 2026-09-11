---
title: Getting started
nav_order: 2
---

# Getting started
{: .no_toc }

1. TOC
{:toc}

---

## Requirements

None beyond Python 3.11 or later. The harness speaks raw HTTP over the
standard library, so there is nothing to `pip install` to probe a
deployment.

`tomllib`, used to read the config files, has been in the standard library
since 3.11; that sets the practical version floor.

## Clone and run

```bash
git clone https://github.com/ToddE/llm-conformance.git
cd llm-conformance

cp .env.example .env
# add provider credentials to .env -- each entry documents where to get one

python3 run.py doctor     # what can be probed right now
python3 run.py probe      # probe everything reachable
```

`doctor` reports which deployment surfaces have a usable credential (or, for
local runtimes, are actually reachable) without sending a single probe
request.

## No credentials? Start local

Local model runtimes need no credential at all and are the cheapest path to
a first measurement:

```bash
python3 run.py discover        # scan for a running local server
python3 run.py probe --provider ollama --trials 5
```

This is also the strongest test of the deployment-identity thesis this
project is built around: the same weights, on the same machine, reached
through two different wire formats (Ollama's OpenAI-compatible shim and its
native API), have already shown different observed capability. See
[Findings](findings).

## Reading the output

A run always writes a complete local bundle before attempting anything
remote:

```
runs/2026-09-11/run_20260911T015009Z_e045f4e9/
  manifest.json      sha256 of every other artifact
  summary.json         aggregate counts
  results.jsonl         one line per trial
  report.md             the rendered report
  evidence/
    trial-0001.json      the same record as one results.jsonl line
```

```bash
python3 run.py report runs/<date>/<run_id>/results.jsonl
```

## Configuring what gets probed

`deployments.toml` is the deployment catalog; `outputs.toml` controls where
a run's evidence goes. Both are plain TOML, no install needed to read or
write them. See [Configuration reference](configuration) for every field
and its accepted values.

## Dry runs cost nothing

```bash
python3 run.py probe --dry-run
```

Prints the exact plan, including which deployments have a credential and
which capability the registry declares for each, without sending a single
request.

---
title: Reproducing a finding
nav_order: 7
---

# Reproducing a finding
{: .no_toc }

1. TOC
{:toc}

---

Every result this project publishes ships with enough evidence for a third
party to reproduce it, or to dispute it on its merits. This page is what
that evidence contains and how to use it.

## What each result records

- The **exact request body as sent**: literal serialized bytes, plus a
  SHA-256 hash of them.
- The **raw response body**: untouched, unparsed, plus its own SHA-256.
- Request and response **headers**, with credentials replaced by a
  `sha256:` fingerprint. This proves two runs used the same key without
  ever exposing it.
- The provider's own **request id**, read from the response headers. This
  is what lets a provider look a specific call up in their own logs. A
  finding a provider can audit is a finding they can concede.
- The model id **requested** and the model id the API **reported back**.
  These differ more often than expected, since an alias can resolve to a
  different snapshot over time; the gap is itself recorded as a signal.
- The **registry claim** under test, with the pinned snapshot's fetch
  timestamp and content hash, so a later change to the registry cannot
  retroactively make an old finding look wrong (or right).
- The **harness git commit** that produced the result.

## Why the harness speaks raw HTTP

The harness uses the Python standard library's `urllib`, not a provider
SDK. Two reasons this matters for reproducibility specifically:

An SDK can rewrite, rename, or default fields on its way to the wire.
Recording what was passed into an SDK is not the same claim as recording
what was actually sent, and would make "the exact request payload" untrue.

An SDK that retries automatically or coerces a malformed response can
paper over the exact failure this project is trying to measure.

## Getting a runnable command from a result

Every stored `HttpExchange` can emit a literal `curl` command:

```python
from conformance.evidence import HttpExchange
# exchange loaded from a result's "exchange" field
print(exchange.as_curl())
```

The credential in that command is left as a shell variable
(`$PROVIDER_API_KEY`), so the command is safe to paste into a report and
still works once the reader exports their own key.

## Re-scoring a result without re-probing

Because the raw response is stored, a result can be re-adjudicated under a
newer version of the outcome taxonomy without spending anything or
contacting the provider again:

```bash
python3 run.py readjudicate runs/<date>/<run_id>/results.jsonl
```

This is how a taxonomy correction (see the advisory-leak note on the
[Findings](findings) page) gets applied to an existing measurement instead
of requiring it to be re-run.

## Verifying a bundle has not been altered

Every run directory carries a `manifest.json` with the SHA-256 of every
other artifact in it.

```bash
python3 -c "from conformance.runbundle import verify_manifest; \
print(verify_manifest('runs/<date>/<run_id>') or 'matches')"
```

An empty result means the bundle matches its own manifest bit for bit.

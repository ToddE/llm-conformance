"""Raw HTTP over the standard library.

Deliberately not using provider SDKs. Three reasons:

1. Reproduction. The evidence record must contain the exact request payload.
   SDKs inject, rename, and default fields; recording "what we passed to the
   SDK" would not be what went on the wire.
2. Zero dependencies. A third party reproduces a finding with `python3 run.py`
   and nothing else -- no pip, no venv, no lockfile drift, no supply chain.
3. SDKs paper over the exact failures we're hunting. A client that retries or
   coerces a malformed response would hide the finding.
"""

from __future__ import annotations

import json
import random
import time
import urllib.error
import urllib.request
from typing import Any

from .evidence import (
    HttpExchange,
    extract_request_id,
    redact_headers,
    sha256_hex,
    utc_now_iso,
)

DEFAULT_TIMEOUT = 90
RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}
USER_AGENT = "llm-conformance/0.1 (+probe; https://github.com/toddemerson/llm-conformance)"


def post_json(
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    *,
    timeout: int = DEFAULT_TIMEOUT,
    max_attempts: int = 3,
) -> HttpExchange:
    """POST a JSON payload, capturing the full exchange verbatim.

    Retries only on transport faults and retryable status codes. A 4xx that
    names a capability is a RESULT, not an error -- returned immediately and
    never retried, because retrying it would just cost money to learn the same
    thing.
    """
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    body_bytes = body.encode("utf-8")
    send_headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
        **headers,
    }

    last: HttpExchange | None = None

    for attempt in range(1, max_attempts + 1):
        started = utc_now_iso()
        t0 = time.monotonic()
        req = urllib.request.Request(
            url, data=body_bytes, headers=send_headers, method="POST"
        )

        status: int | None = None
        resp_headers: dict[str, str] = {}
        resp_body = ""
        transport_error: str | None = None

        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                status = resp.status
                resp_headers = dict(resp.headers.items())
                resp_body = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            # An HTTP error response is data we want, not an exception.
            status = exc.code
            resp_headers = dict(exc.headers.items()) if exc.headers else {}
            try:
                resp_body = exc.read().decode("utf-8", errors="replace")
            except Exception:
                resp_body = ""
        except Exception as exc:
            transport_error = f"{type(exc).__name__}: {exc}"

        exchange = HttpExchange(
            url=url,
            method="POST",
            request_headers=redact_headers(send_headers),
            request_body=body,
            request_body_sha256=sha256_hex(body),
            status=status,
            response_headers=resp_headers,
            response_body=resp_body,
            response_body_sha256=sha256_hex(resp_body),
            provider_request_id=extract_request_id(resp_headers),
            started_at=started,
            finished_at=utc_now_iso(),
            duration_ms=int((time.monotonic() - t0) * 1000),
            transport_error=transport_error,
        )
        last = exchange

        retryable = transport_error is not None or (
            status is not None and status in RETRYABLE_STATUS
        )
        if not retryable or attempt == max_attempts:
            return exchange

        # Exponential backoff with jitter; honor Retry-After when given.
        delay = min(2 ** attempt, 16) + random.uniform(0, 1)
        retry_after = resp_headers.get("retry-after") or resp_headers.get("Retry-After")
        if retry_after:
            try:
                delay = max(delay, float(retry_after))
            except ValueError:
                pass
        time.sleep(delay)

    assert last is not None
    return last


def parse_json_body(exchange: HttpExchange) -> Any | None:
    """Parse a response body, or None if it isn't JSON. Never raises."""
    if not exchange.response_body:
        return None
    try:
        return json.loads(exchange.response_body)
    except (json.JSONDecodeError, ValueError):
        return None

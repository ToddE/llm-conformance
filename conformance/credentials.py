"""Credential lifecycle tracking.

API keys rotate. Some expire hard (Azure, short-lived tokens); most are
rotated by policy. Either way, a key that dies mid-run turns a conformance
measurement into a pile of `error_auth` results, and a project whose whole
output is longitudinal drift data cannot afford silent gaps in its own
collection.

Convention -- for any key `FOO_API_KEY`:

    FOO_API_KEY=...
    FOO_API_KEY_EXPIRES_AT=2027-03-01     # hard expiry OR rotate-by (ISO)
    FOO_API_KEY_ROTATED_AT=2026-09-10     # when it was last issued (optional)
    FOO_API_KEY_NOTE=owner, ticket, url   # free text            (optional)

Naming: `_EXPIRES_AT`, not `_EXPIRES`. The `_AT` suffix is the standard marker
for a point in time (`created_at`, `updated_at`, OAuth 2's `expires_at`), and
it disambiguates against OAuth 2's `expires_in`, which is a DURATION. A bare
`_EXPIRES` reads as either -- or as a boolean -- and the values here are
timestamps, sometimes with a UTC offset. `_EXPIRES` is still accepted as a
legacy alias so existing files keep working.

One field covers both hard expiry and rotate-by-policy: the action is
identical either way -- go get a new key -- and two fields with one action
invites them to disagree.

A key with NO expiry recorded is reported as `no_expiry_set` rather than as
healthy. Unknown is not the same as fine, and defaulting unknown to fine is
how rotation deadlines get missed.

This module never returns or logs key material -- only a last-4 and a
fingerprint.
"""

from __future__ import annotations

import datetime as _dt
import os
from dataclasses import asdict, dataclass
from typing import Any

from .evidence import sha256_hex

# status -> (severity, human label). Severity drives exit codes and alerting.
STATUS_EXPIRED = "expired"
STATUS_CRITICAL = "critical"
STATUS_WARNING = "warning"
STATUS_OK = "ok"
STATUS_NO_EXPIRY = "no_expiry_set"
STATUS_ABSENT = "absent"

SEVERITY = {
    STATUS_EXPIRED: 3,
    STATUS_CRITICAL: 3,
    STATUS_WARNING: 2,
    STATUS_NO_EXPIRY: 1,
    STATUS_OK: 0,
    STATUS_ABSENT: 0,   # not an alert -- an unconfigured provider is a choice
}

CRITICAL_DAYS = 7
WARNING_DAYS = 30


@dataclass
class CredentialStatus:
    env_var: str
    present: bool
    status: str
    days_remaining: int | None
    expires_on: str | None
    rotated_on: str | None
    age_days: int | None
    note: str | None
    key_last4: str | None
    key_fingerprint: str | None

    @property
    def severity(self) -> int:
        return SEVERITY.get(self.status, 0)

    @property
    def needs_action(self) -> bool:
        return self.severity >= 2

    def message(self) -> str:
        if self.status == STATUS_ABSENT:
            return f"{self.env_var}: not set"
        if self.status == STATUS_NO_EXPIRY:
            age = f", {self.age_days}d old" if self.age_days is not None else ""
            return (f"{self.env_var}: present (…{self.key_last4}){age} "
                    f"but no {self.env_var}_EXPIRES_AT recorded")
        if self.status == STATUS_EXPIRED:
            return (f"{self.env_var}: EXPIRED on {self.expires_on} "
                    f"({abs(self.days_remaining)}d ago) -- rotate now")
        if self.status == STATUS_CRITICAL:
            return (f"{self.env_var}: expires {self.expires_on} "
                    f"in {self.days_remaining}d -- rotate now")
        if self.status == STATUS_WARNING:
            return (f"{self.env_var}: expires {self.expires_on} "
                    f"in {self.days_remaining}d")
        return f"{self.env_var}: valid until {self.expires_on} ({self.days_remaining}d)"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["severity"] = self.severity
        d["needs_action"] = self.needs_action
        d["message"] = self.message()
        return d


def _parse_date(value: str | None) -> _dt.date | None:
    if not value:
        return None
    value = value.strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y", "%Y-%m-%dT%H:%M:%S"):
        try:
            return _dt.datetime.strptime(value[:len(fmt) + 2], fmt).date()
        except ValueError:
            continue
    try:  # ISO 8601 with timezone
        return _dt.datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _lookup(name: str, env: dict[str, str]) -> str | None:
    return os.environ.get(name) or env.get(name)


def check(env_var: str, env: dict[str, str], today: _dt.date | None = None) -> CredentialStatus:
    today = today or _dt.datetime.now(_dt.timezone.utc).date()
    key = _lookup(env_var, env)
    # Canonical name first, legacy alias second.
    expires_raw = (_lookup(f"{env_var}_EXPIRES_AT", env)
                   or _lookup(f"{env_var}_EXPIRES", env))
    rotated_raw = (_lookup(f"{env_var}_ROTATED_AT", env)
                   or _lookup(f"{env_var}_ROTATED", env))
    note = _lookup(f"{env_var}_NOTE", env)

    expires = _parse_date(expires_raw)
    rotated = _parse_date(rotated_raw)
    age = (today - rotated).days if rotated else None

    if not key:
        return CredentialStatus(env_var, False, STATUS_ABSENT, None,
                                expires.isoformat() if expires else None,
                                rotated.isoformat() if rotated else None,
                                age, note, None, None)

    last4, fp = key[-4:], sha256_hex(key)[:12]

    if expires is None:
        # An unparseable date is worse than a missing one -- it looks set.
        if expires_raw:
            note = (f"UNPARSEABLE {env_var}_EXPIRES_AT={expires_raw!r}"
                    + (f" | {note}" if note else ""))
        return CredentialStatus(env_var, True, STATUS_NO_EXPIRY, None, None,
                                rotated.isoformat() if rotated else None,
                                age, note, last4, fp)

    days = (expires - today).days
    if days < 0:
        status = STATUS_EXPIRED
    elif days <= CRITICAL_DAYS:
        status = STATUS_CRITICAL
    elif days <= WARNING_DAYS:
        status = STATUS_WARNING
    else:
        status = STATUS_OK

    return CredentialStatus(env_var, True, status, days, expires.isoformat(),
                            rotated.isoformat() if rotated else None,
                            age, note, last4, fp)


def check_all(env_vars: list[str], env: dict[str, str],
              today: _dt.date | None = None) -> list[CredentialStatus]:
    seen: set[str] = set()
    out: list[CredentialStatus] = []
    for var in env_vars:
        if var in seen:
            continue
        seen.add(var)
        out.append(check(var, env, today))
    return out


def worst_severity(statuses: list[CredentialStatus]) -> int:
    return max((s.severity for s in statuses), default=0)


def alert_payload(statuses: list[CredentialStatus]) -> dict[str, Any]:
    """Machine-readable summary for a cron job, webhook, or email template."""
    actionable = [s for s in statuses if s.needs_action]
    return {
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "worst_severity": worst_severity(statuses),
        "action_required": bool(actionable),
        "summary": (
            f"{len(actionable)} credential(s) need rotation"
            if actionable else "all configured credentials healthy"
        ),
        "credentials": [s.to_dict() for s in statuses],
    }

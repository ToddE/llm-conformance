"""Credential profiles: the same provider, multiple regions or accounts.

P3 says capabilities belong to the DEPLOYMENT, not the model -- and `region`
is a term in the deployment tuple. So a single key per provider cannot express
what this project needs to measure. Anthropic workspaces can be created per
region; Azure OpenAI deployments are per-region by construction and roll out
independently; Bedrock model access is granted per region.

Convention -- a profile is a suffix on the provider prefix:

    ANTHROPIC_API_KEY=...                        # the "default" profile
    ANTHROPIC_API_KEY_EXPIRES_AT=2027-01-01

    ANTHROPIC_USEAST_API_KEY=...                 # profile "useast"
    ANTHROPIC_USEAST_REGION=us-east-1
    ANTHROPIC_USEAST_API_KEY_EXPIRES_AT=2027-01-01
    ANTHROPIC_USEAST_ENDPOINT=https://...        # optional override
    ANTHROPIC_USEAST_API_VERSION=2023-06-01      # optional override
    ANTHROPIC_USEAST_NOTE=eu workspace, owner: todd

Each profile becomes a SEPARATE deployment with its own deployment_id, because
region and endpoint are already part of deployment identity. That is the whole
point: "same model, different region, different observed capability" has to be
expressible, or P3 cannot be tested.

Profiles are DISCOVERED from the environment -- no registration list to keep in
sync. Anything matching `<PREFIX>_<PROFILE>_API_KEY` with a non-empty value is
a profile.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Suffixes that are metadata about a key, not a profile name.
_RESERVED = {
    "EXPIRES", "EXPIRES_AT", "ROTATED", "ROTATED_AT", "NOTE",
    "REGION", "ENDPOINT", "API_VERSION", "PROFILES", "SKIP",
}


@dataclass(frozen=True)
class Profile:
    """One credential + the deployment overrides that travel with it."""

    provider_prefix: str        # ANTHROPIC
    name: str                   # "default" | "useast" | ...
    key_var: str                # env var holding the key
    region: str | None = None
    endpoint: str | None = None
    api_version: str | None = None
    note: str | None = None
    skip: bool = False

    @property
    def region_is_verified(self) -> bool:
        """True only when the endpoint we call actually encodes the region.

        A region asserted without a matching endpoint is an operator label, not
        an observation. It must never be recorded as though the request went
        somewhere it did not.
        """
        return bool(self.region and self.endpoint)

    @property
    def warnings(self) -> list[str]:
        out: list[str] = []
        if self.region and not self.endpoint:
            out.append(
                f"{self.provider_prefix} profile '{self.name}' declares "
                f"region={self.region} but no {self.provider_prefix}_"
                f"{self.name.upper().replace('-', '_')}_ENDPOINT -- requests will go to "
                f"the DEFAULT endpoint. The region will be recorded as "
                f"'declared_only' (unverified), not as an observed network path."
            )
        return out

    @property
    def is_default(self) -> bool:
        return self.name == "default"

    def label(self) -> str:
        bits = [self.name]
        if self.region:
            bits.append(f"region={self.region}")
        return " ".join(bits)


def _get(env: dict[str, str], name: str) -> str | None:
    import os
    v = os.environ.get(name) or env.get(name)
    return v.strip() if v and v.strip() else None


def discover(provider_prefix: str, env: dict[str, str]) -> list[Profile]:
    """Find every configured profile for a provider prefix (e.g. ANTHROPIC)."""
    import os

    combined: dict[str, str] = {**env, **{k: v for k, v in os.environ.items()}}
    profiles: list[Profile] = []

    # Default profile: <PREFIX>_API_KEY
    default_var = f"{provider_prefix}_API_KEY"
    if _get(combined, default_var):
        profiles.append(Profile(
            provider_prefix=provider_prefix,
            name="default",
            key_var=default_var,
            region=_get(combined, f"{provider_prefix}_REGION"),
            endpoint=_get(combined, f"{provider_prefix}_ENDPOINT"),
            api_version=_get(combined, f"{provider_prefix}_API_VERSION"),
            note=_get(combined, f"{provider_prefix}_NOTE"),
            skip=(_get(combined, f"{provider_prefix}_SKIP") or "").lower() in ("1","true","yes"),
        ))

    # Named profiles: <PREFIX>_<PROFILE>_API_KEY
    pattern = re.compile(rf"^{re.escape(provider_prefix)}_(.+)_API_KEY$")
    for var, value in sorted(combined.items()):
        m = pattern.match(var)
        if not m or not (value or "").strip():
            continue
        raw = m.group(1)
        if raw.upper() in _RESERVED:
            continue
        base = f"{provider_prefix}_{raw}"
        profiles.append(Profile(
            provider_prefix=provider_prefix,
            name=raw.lower().replace("_", "-"),
            key_var=var,
            region=_get(combined, f"{base}_REGION"),
            endpoint=_get(combined, f"{base}_ENDPOINT"),
            api_version=_get(combined, f"{base}_API_VERSION"),
            note=_get(combined, f"{base}_NOTE"),
            skip=(_get(combined, f"{base}_SKIP") or "").lower() in ("1","true","yes"),
        ))

    return profiles


def prefix_for(env_vars: tuple[str, ...]) -> str | None:
    """Derive the provider prefix from an adapter's declared env vars."""
    for var in env_vars:
        if var.endswith("_API_KEY"):
            return var[: -len("_API_KEY")]
    return None


def key_vars_for(provider_prefix: str, env: dict[str, str]) -> list[str]:
    """Every key env var configured for this provider, for lifecycle checks."""
    return [p.key_var for p in discover(provider_prefix, env)]

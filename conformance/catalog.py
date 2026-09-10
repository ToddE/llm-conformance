"""Deployment catalog: config-driven providers.

The maintenance problem this solves: the number of deployments to probe grows
without bound (providers x models x regions x accounts x adapters), while the
number of distinct WIRE FORMATS grows very slowly. If both live in Python,
every new region costs a code change and a review.

So they are separated:

    wire format  -> Python. A request/response shape. Four of them today.
    deployment   -> deployments.json + env. Unbounded, config only.

Adding a provider that speaks an existing wire is a JSON block and a
credential. No Python, no release. Adding a genuinely new wire format is the
only thing that needs code -- and that is irreducible, because a new shape
means new serialization and new response parsing.

Credentials never appear here. The catalog is committed on purpose (publishing
the deployment matrix is part of the methodology); secrets are discovered from
`env_prefix` at runtime, so the same catalog works against .env locally and
Vault/SSM/Secrets Manager elsewhere.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

from .adapters.anthropic import AnthropicAdapter
from .adapters.base import Adapter
from .adapters.google import GoogleAdapter
from .adapters.ollama import OllamaNativeAdapter
from .adapters.openai_compatible import OpenAICompatibleAdapter

CATALOG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "deployments.json"
)

# The only thing that requires Python. Keys here are the `wire` values allowed
# in deployments.json.
WIRE_FORMATS: dict[str, type[Adapter]] = {
    "openai": OpenAICompatibleAdapter,
    "anthropic": AnthropicAdapter,
    "gemini": GoogleAdapter,
    "ollama-native": OllamaNativeAdapter,
}

MODELS_AUTO = "auto"
MODELS_AUTO_LOCAL = "auto-local"


class CatalogError(ValueError):
    pass


@dataclass
class ProviderConfig:
    id: str
    wire: str
    base_url: str
    registry_id: str = ""
    env_prefix: str | None = None
    env_fallbacks: list[str] = field(default_factory=list)
    api_version: str | None = None
    max_tokens_field: str | None = None
    max_tokens_value: int | None = None
    auth: str | None = None          # "none" to send no credential
    local: bool = False
    models: Any = MODELS_AUTO
    note: str | None = None
    doc: str | None = None
    enabled: bool = True

    @property
    def env_vars(self) -> tuple[str, ...]:
        if self.local or self.auth == "none":
            return ()
        primary = f"{self.env_prefix}_API_KEY" if self.env_prefix else None
        out = ([primary] if primary else []) + list(self.env_fallbacks)
        return tuple(out)


def load(path: str = CATALOG_PATH) -> dict[str, ProviderConfig]:
    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh)

    providers: dict[str, ProviderConfig] = {}
    for pid, entry in (raw.get("providers") or {}).items():
        if pid.startswith("_"):
            continue
        wire = entry.get("wire")
        if wire not in WIRE_FORMATS:
            raise CatalogError(
                f"provider '{pid}' declares unknown wire '{wire}'. "
                f"Known wires: {', '.join(sorted(WIRE_FORMATS))}. "
                f"A genuinely new wire format needs an adapter in "
                f"conformance/adapters/ and an entry in WIRE_FORMATS -- that is "
                f"the one case config cannot cover."
            )
        if not entry.get("base_url"):
            raise CatalogError(f"provider '{pid}' has no base_url")
        if not (entry.get("env_prefix") or entry.get("local") or entry.get("auth") == "none"):
            raise CatalogError(
                f"provider '{pid}' needs an env_prefix (for credential discovery) "
                f"or local/auth=none"
            )
        providers[pid] = ProviderConfig(
            id=pid,
            wire=wire,
            base_url=entry["base_url"],
            registry_id=entry.get("registry_id", pid),
            env_prefix=entry.get("env_prefix"),
            env_fallbacks=list(entry.get("env_fallbacks") or []),
            api_version=entry.get("api_version"),
            max_tokens_field=entry.get("max_tokens_field"),
            max_tokens_value=entry.get("max_tokens_value"),
            auth=entry.get("auth"),
            local=bool(entry.get("local")),
            models=entry.get("models", MODELS_AUTO),
            note=entry.get("note"),
            doc=entry.get("doc"),
            enabled=entry.get("enabled", True),
        )
    if not providers:
        raise CatalogError(f"no providers defined in {path}")
    return providers


def adapter_class(cfg: ProviderConfig) -> type[Adapter]:
    """Build an adapter class for a catalog entry.

    A dynamic subclass rather than an instance-configured object, so that every
    existing `cls.base_url` / `cls.env_vars` access keeps working and the wire
    implementations stay plain, readable classes.
    """
    base = WIRE_FORMATS[cfg.wire]
    attrs: dict[str, Any] = {
        "name": cfg.id,
        "base_url": cfg.base_url,
        "registry_id": cfg.registry_id or cfg.id,
        "env_vars": cfg.env_vars,
        "adapter_id": getattr(base, "adapter_id", None) or cfg.wire,
        "__doc__": cfg.note or f"{cfg.id} ({cfg.wire} wire)",
    }
    if cfg.max_tokens_field:
        attrs["max_tokens_field"] = cfg.max_tokens_field
    if cfg.max_tokens_value:
        attrs["max_tokens_value"] = cfg.max_tokens_value
    if cfg.api_version and cfg.wire == "anthropic":
        attrs["api_version_header"] = cfg.api_version

    if cfg.auth == "none" or cfg.local:
        attrs["_headers"] = lambda self: {}

    return type(f"{cfg.id.title().replace('-', '')}Adapter", (base,), attrs)


def build_adapters(path: str = CATALOG_PATH, include_disabled: bool = False
                   ) -> tuple[dict[str, type[Adapter]], dict[str, ProviderConfig]]:
    configs = load(path)
    active = {pid: cfg for pid, cfg in configs.items()
              if cfg.enabled or include_disabled}
    return {pid: adapter_class(cfg) for pid, cfg in active.items()}, active


def local_surfaces(configs: dict[str, ProviderConfig]) -> set[str]:
    return {pid for pid, cfg in configs.items() if cfg.local or cfg.auth == "none"}

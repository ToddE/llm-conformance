"""Probe runner: execute probes across deployments, with repetition."""

from __future__ import annotations

import os
from typing import Any, Callable

from . import outcomes as O
from .adapters.base import Adapter, Interpretation
from .catalog import build_adapters, local_surfaces
from .deployment import Deployment
from .profiles import Profile, discover, prefix_for
from .evidence import EvidenceWriter, ProbeResult, new_result_id, utc_now_iso
from .http import post_json
from .probes.definitions import ALL_PROBES
from .registry import Registry

# Built from deployments.json at import time -- adding a provider is a config
# change, not a code change. Keys name a DEPLOYMENT SURFACE, not just a vendor:
# ollama appears twice because the same model over two wire formats is two
# deployments, and telling them apart is the point.
ADAPTERS: dict[str, type[Adapter]]
PROVIDER_CONFIGS: dict
ADAPTERS, PROVIDER_CONFIGS = build_adapters()

# Surfaces needing no credential. Reachable now; the whole point of E3's
# cheap pairs, and the reason a third party can reproduce findings with no
# vendor account at all.
LOCAL_SURFACES = local_surfaces(PROVIDER_CONFIGS)

# Response-level outcomes are stochastic and need repetition. Constraint-level
# outcomes (rejections, dialect errors) are deterministic and valid at n=1.
DEFAULT_TRIALS = 5


def load_dotenv(path: str = ".env") -> dict[str, str]:
    values: dict[str, str] = {}
    if not os.path.exists(path):
        return values
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            val = val.strip().strip('"').strip("'")
            if val:
                values[key.strip()] = val
    return values


def resolve_key(adapter_cls: type[Adapter], env: dict[str, str],
                profile: Profile | None = None) -> str | None:
    if profile is not None:
        return os.environ.get(profile.key_var) or env.get(profile.key_var)
    for var in adapter_cls.env_vars:
        val = os.environ.get(var) or env.get(var)
        if val:
            return val
    return None


def profiles_for(surface: str, env: dict[str, str]) -> list[Profile | None]:
    """Every configured credential profile for a surface.

    [None] means "no profile system applies" -- either a local runtime needing
    no credential, or a provider with nothing configured (so the run still
    reports it as skipped rather than silently omitting it).
    """
    cls = ADAPTERS[surface]
    if surface in LOCAL_SURFACES:
        return [None]
    prefix = prefix_for(cls.env_vars)
    if not prefix:
        return [None]
    found = [p for p in discover(prefix, env) if not p.skip]
    if not found:
        # Fall back to alternate env var names the adapter declares
        # (e.g. GOOGLE_API_KEY vs GEMINI_API_KEY).
        for var in cls.env_vars:
            alt = prefix_for((var,))
            if alt and alt != prefix:
                found = [p for p in discover(alt, env) if not p.skip]
                if found:
                    break
    return list(found) if found else [None]


def local_model_details(surface: str, model: str) -> dict[str, str | None]:
    """Quantization/size for a local model. Part of deployment identity."""
    cfg = PROVIDER_CONFIGS.get(surface)
    if not cfg or not (cfg.local or cfg.auth == "none"):
        return {}
    import json as _json
    import urllib.request
    root = cfg.base_url.rsplit("/", 1)[0]
    for url in (f"{root}/api/tags",):
        try:
            with urllib.request.urlopen(url, timeout=4) as r:
                for m in _json.load(r).get("models", []):
                    if m.get("name") == model:
                        d = m.get("details") or {}
                        return {"quantization": d.get("quantization_level"),
                                "parameter_size": d.get("parameter_size")}
        except Exception:
            continue
    return {}


def make_deployment(surface: str, model: str,
                    profile: Profile | None = None) -> Deployment:
    cls = ADAPTERS[surface]
    adapter_id = getattr(cls, "adapter_id", None) or (
        "openai-compatible" if issubclass(cls, OpenAIAdapter) else cls.name
    )
    cfg = PROVIDER_CONFIGS.get(surface)
    api_version = cfg.api_version if cfg else None
    endpoint = cls.base_url
    region = None
    region_source = None
    cred_profile = "default"
    if profile is not None:
        endpoint = profile.endpoint or endpoint
        api_version = profile.api_version or api_version
        region = profile.region
        cred_profile = profile.name
        if region:
            # Verified only if the URL we actually call encodes the region.
            region_source = "endpoint" if profile.endpoint else "declared_only"
    return Deployment(
        provider=cls.name,
        endpoint=endpoint,
        model_id=model,
        adapter=adapter_id,
        proxy_path="direct",
        credential_profile=cred_profile,
        region=region,
        region_source=region_source,
        api_version=api_version,
        sampling_settings={"temperature": "provider-default", "seed": None},
        request_parameters={"max_output_tokens": getattr(cls, "max_tokens_value", None)},
        **local_model_details(surface, model),
    )


def _result(run_id: str, dep: Deployment, capability: str, declared, source: str,
            interp: Interpretation, mechanism: str, endpoint: str, exchange,
            trial: int, trials: int) -> ProbeResult:
    divergence, conforms = O.classify_divergence(declared, interp.outcome)
    d = dep.to_dict()
    d["model_snapshot"] = interp.model_reported
    return ProbeResult(
        result_id=new_result_id(), run_id=run_id, timestamp=utc_now_iso(),
        deployment=d, trial_index=trial, trials_total=trials,
        provider=dep.provider, model_requested=dep.model_id,
        model_reported=interp.model_reported, endpoint=endpoint,
        capability=capability, mechanism=mechanism,
        probe_id=ALL_PROBES[capability].id,
        declared=declared, declared_source=source,
        outcome=interp.outcome, conforms=conforms, divergence=divergence,
        detail=interp.detail, violations=interp.violations, exchange=exchange,
    )


def run_probe_trial(run_id: str, adapter: Adapter, dep: Deployment, capability: str,
                    registry: Registry, trial: int, trials: int) -> ProbeResult:
    probe = ALL_PROBES[capability]
    declared = registry.declares(adapter.registry_id or dep.provider, dep.model_id, capability)
    source = registry.source_ref(adapter.registry_id or dep.provider, dep.model_id)

    if capability == "structured_output":
        url, headers, payload = adapter.structured_output_request(dep.model_id, probe)
        mechanism = adapter.so_mechanism
    else:
        url, headers, payload = adapter.tool_calling_request(dep.model_id, probe)
        mechanism = adapter.tc_mechanism

    exchange = post_json(url, headers, payload)
    interp = (adapter.interpret_structured_output(exchange, probe)
              if capability == "structured_output"
              else adapter.interpret_tool_calling(exchange, probe))
    return _result(run_id, dep, capability, declared, source, interp,
                   mechanism, url, exchange, trial, trials)


def run_all(run_id: str, plan: list[tuple[str, str, "Profile | None"]], registry: Registry,
            env: dict[str, str], writer: EvidenceWriter, capabilities: list[str],
            trials: int = DEFAULT_TRIALS,
            on_result: Callable[[ProbeResult], None] | None = None) -> list[ProbeResult]:
    """plan is a list of (surface, model, profile) triples."""
    results: list[ProbeResult] = []

    for surface, model, profile in plan:
        cls = ADAPTERS[surface]
        key = "" if surface in LOCAL_SURFACES else resolve_key(cls, env, profile)
        dep = make_deployment(surface, model, profile)

        if key is None:
            declared = registry.declares(cls.registry_id or cls.name, model, capabilities[0])
            for capability in capabilities:
                r = _result(
                    run_id, dep, capability,
                    registry.declares(cls.registry_id or cls.name, model, capability),
                    registry.source_ref(cls.registry_id or cls.name, model),
                    Interpretation(O.SKIPPED_NO_CREDENTIAL,
                                   f"no credential: set one of {', '.join(cls.env_vars)}", []),
                    "(not called)", "(not called)", None, 1, 1)
                writer.write(r); results.append(r)
                if on_result: on_result(r)
            continue

        for capability in capabilities:
            for trial in range(1, trials + 1):
                # Fresh adapter per trial: adapters carry per-request state
                # (dropped_assertions) that must not leak between trials.
                adapter = cls(key)
                if profile is not None and profile.endpoint:
                    adapter.base_url = profile.endpoint
                r = run_probe_trial(run_id, adapter, dep, capability,
                                    registry, trial, trials)
                writer.write(r); results.append(r)
                if on_result: on_result(r)
                # Constraint-level outcomes are deterministic -- repeating them
                # burns money and time to learn the same thing.
                if r.outcome in (O.REJECTED, O.UNSUPPORTED_DIALECT, O.ERROR_AUTH,
                                 O.ERROR_NOT_FOUND):
                    break
    return results

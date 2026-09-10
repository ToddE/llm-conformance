"""Deployment identity.

A capability result may belong to the deployment CONFIGURATION rather than to
the model. Recording only (provider, model) makes findings unattributable --
and an unattributable finding is worse than no finding, because it invites a
correct dismissal.

`adapter` and `proxy_path` are first-class rather than a single "went through
LiteLLM" boolean, because two adapters can independently transform
`response_format`, tool schemas, `tool_choice`, system messages, streaming
events, and unsupported parameters. That makes this statement expressible:

    same provider + same model + different adapter -> different observed capability

which is likely to be one of the more practical findings this project produces.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class Deployment:
    provider: str               # openai, anthropic, ollama, ...
    endpoint: str               # base URL ACTUALLY called
    model_id: str               # id as requested
    adapter: str                # which wire format WE spoke
    proxy_path: str = "direct"  # direct | litellm | openrouter | ...
    credential_profile: str = "default"
    region: str | None = None
    # How we know the region. "endpoint" = the URL we called encodes it, so it
    # is verified. "declared_only" = the operator asserted it but the call went
    # to the default endpoint, so NOTHING in the network path confirms it.
    # Recording a region the request never reflected would be fabricated
    # provenance, which is disqualifying for evidence whose whole value is that
    # a third party can check it.
    region_source: str | None = None
    # Local-runtime terms. Quantization belongs in IDENTITY, not metadata: the
    # same model name at Q4 and Q8 is two different artifacts, and quantization
    # measurably affects whether a model emits schema-valid output. Treating
    # them as one deployment would average two different things together.
    quantization: str | None = None
    parameter_size: str | None = None
    api_version: str | None = None
    model_snapshot: str | None = None   # filled from the response when reported
    request_parameters: dict[str, Any] = field(default_factory=dict)
    sampling_settings: dict[str, Any] = field(default_factory=dict)

    # Bump when the identity payload changes shape. The id carries it, so a
    # scheme change is VISIBLE rather than silently fragmenting the drift
    # archive -- two ids with different versions are known-incomparable instead
    # of looking like two different deployments.
    IDENTITY_VERSION = 3

    def identity(self) -> str:
        """Stable id over the config, excluding response-derived fields.

        model_snapshot is deliberately excluded: it is observed, not
        configured, and a provider silently moving an alias to a new snapshot
        must not fragment the deployment's history. That drift is a finding to
        report, not a new deployment.
        """
        payload = {
            "identity_version": self.IDENTITY_VERSION,
            "provider": self.provider,
            "endpoint": self.endpoint,
            "model_id": self.model_id,
            "adapter": self.adapter,
            "proxy_path": self.proxy_path,
            # The credential is part of deployment identity. Different accounts
            # genuinely differ in observed capability -- Bedrock grants model
            # access per account, providers gate betas per org, tiers differ.
            # Two credentials against the same URL are two deployments.
            "credential_profile": self.credential_profile,
            "region": self.region,
            "quantization": self.quantization,
            "parameter_size": self.parameter_size,
            "api_version": self.api_version,
            "request_parameters": self.request_parameters,
            "sampling_settings": self.sampling_settings,
        }
        blob = json.dumps(payload, sort_keys=True)
        return f"dep{self.IDENTITY_VERSION}_" + hashlib.sha256(blob.encode()).hexdigest()[:16]

    def label(self) -> str:
        bits = [self.provider, self.model_id, f"via={self.adapter}"]
        if self.credential_profile != "default":
            bits.append(f"profile={self.credential_profile}")
        if self.proxy_path != "direct":
            bits.append(f"proxy={self.proxy_path}")
        if self.api_version:
            bits.append(f"apiver={self.api_version}")
        if self.region:
            suffix = "" if self.region_source == "endpoint" else " (UNVERIFIED)"
            bits.append(f"region={self.region}{suffix}")
        if self.quantization:
            bits.append(f"quant={self.quantization}")
        return " ".join(bits)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["deployment_id"] = self.identity()
        return d

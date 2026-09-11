"""Loader for outputs.toml -- where a run's artifacts go.

Kept separate from catalog.py (deployments.toml -- what gets probed) because
the two vary independently: the deployment matrix is methodology and gets
published; output destinations are operational and often include private
infrastructure (a company's own S3 bucket, say) that has no reason to share a
file with the deployment catalog.
"""

from __future__ import annotations

import os
import tomllib
from typing import Any

OUTPUTS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "outputs.toml"
)

SUPPORTED_CONFIG_VERSION = 1

DEFAULT_CONFIG: dict[str, Any] = {
    "config_version": 1,
    "run": {"output_dir": "./runs", "hash_algorithm": "sha256"},
    "outputs": [
        {"id": "local", "type": "filesystem", "required": True, "path": "./runs",
         "artifacts": ["manifest", "summary", "results", "report", "raw_evidence"]},
    ],
}


class OutputsConfigError(ValueError):
    pass


def load(path: str = OUTPUTS_PATH) -> dict[str, Any]:
    if not os.path.exists(path):
        return DEFAULT_CONFIG
    with open(path, "rb") as fh:
        raw = tomllib.load(fh)
    version = raw.get("config_version")
    if version != SUPPORTED_CONFIG_VERSION:
        raise OutputsConfigError(
            f"{path}: config_version={version!r}, this harness understands "
            f"{SUPPORTED_CONFIG_VERSION}."
        )
    return raw

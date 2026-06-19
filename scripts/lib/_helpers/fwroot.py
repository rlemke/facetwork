"""Python twin of _bootstrap.sh — resolve the repo root robustly.

Used by the Python fw commands (fleet, fleet-agent, terminate-workflow,
build-cache-index, _cache_to_minio_move) and by _fleet_bootstrap._repo_root().
Resolution order mirrors _bootstrap.sh: FW_ROOT_OVERRIDE -> git toplevel ->
walk up to the pyproject.toml sentinel.
"""

from __future__ import annotations

import os
import subprocess


def fw_root() -> str:
    override = os.environ.get("FW_ROOT_OVERRIDE")
    if override:
        return override
    here = os.path.dirname(os.path.abspath(__file__))
    try:
        return subprocess.check_output(
            ["git", "-C", here, "rev-parse", "--show-toplevel"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        pass
    d = here
    while d != "/" and not os.path.isfile(os.path.join(d, "pyproject.toml")):
        d = os.path.dirname(d)
    return d


def helpers_dir() -> str:
    """Directory holding the sourced helpers (_fleet_lib, _fleet_bootstrap, …)."""
    return os.path.join(fw_root(), "scripts", "lib", "_helpers")

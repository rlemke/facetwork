"""Cache-root derivation for the s2 tools (slim, local-only scaffold).

Conforms to agent-spec/cache-layout: artifacts live under
``$AFL_CACHE_ROOT/<namespace>/<cache_type>/...`` where ``AFL_CACHE_ROOT``
defaults to ``$AFL_DATA_ROOT/cache`` and ``AFL_DATA_ROOT`` defaults to a
local dir. This scaffold ships the ``local`` backend only; an ``hdfs`` /
``s3`` backend can be added the way fwh_osm / fwh_save_earth do (see their
``_*_tools/storage.py`` for the full Storage ABC + finalize-from-local).
"""

from __future__ import annotations

import os

LOCAL_DEFAULT_ROOT = os.path.expanduser("~/afl_data")


def data_root() -> str:
    return os.environ.get("AFL_DATA_ROOT") or LOCAL_DEFAULT_ROOT


def cache_root() -> str:
    return os.environ.get("AFL_CACHE_ROOT") or os.path.join(data_root(), "cache")


def output_root() -> str:
    """Where rendered map bundles are written (kept local — object stores
    don't do partial writes; handlers stage locally and finalize)."""
    return os.environ.get("AFL_OUTPUT_BASE") or os.path.join(data_root(), "output")


def join(*parts: str) -> str:
    return os.path.join(*[p for p in parts if p])

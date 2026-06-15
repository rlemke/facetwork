"""Per-entry cache sidecar (slim scaffold).

Each cached artifact ``<path>`` gets a sibling ``<path>.meta.json`` recording
size, sha256, source, and tool lineage — so N writers on N keys never contend
and a cache hit is a cheap presence + checksum check. This mirrors the
fuller sidecar in fwh_osm / fwh_save_earth (which adds fcntl locking and a
staging→finalize dance for remote backends); the contract — one sidecar per
entry, payload dir stays pure — is identical.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from typing import Any

from _s2_tools import storage

SIDECAR_SUFFIX = ".meta.json"


def utcnow_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def cache_path(cache_type: str, relative_path: str) -> str:
    """Absolute path of a cached artifact: ``<cache_root>/s2/<cache_type>/<rel>``."""
    return storage.join(storage.cache_root(), storage.NAMESPACE if hasattr(storage, "NAMESPACE") else "s2", cache_type, relative_path)


def _meta_path(abs_path: str) -> str:
    return abs_path + SIDECAR_SUFFIX


def exists(cache_type: str, relative_path: str) -> bool:
    """True when both the artifact and its sidecar are present."""
    p = cache_path(cache_type, relative_path)
    return os.path.exists(p) and os.path.exists(_meta_path(p))


def sha256_file(abs_path: str) -> str:
    h = hashlib.sha256()
    with open(abs_path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write(cache_type: str, relative_path: str, data: bytes, *, source: str, tool: str,
          extras: dict[str, Any] | None = None) -> dict[str, Any]:
    """Write ``data`` to the cache and its sidecar. Returns the sidecar dict."""
    abs_path = cache_path(cache_type, relative_path)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    tmp = abs_path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, abs_path)
    meta = {
        "version": 1,
        "cache_type": cache_type,
        "relative_path": relative_path,
        "size_bytes": len(data),
        "sha256": sha256_file(abs_path),
        "source": source,
        "tool": tool,
        "created_at": utcnow_iso(),
        "extras": extras or {},
    }
    with open(_meta_path(abs_path), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)
    return meta


def read_meta(cache_type: str, relative_path: str) -> dict[str, Any]:
    with open(_meta_path(cache_path(cache_type, relative_path)), encoding="utf-8") as f:
        return json.load(f)


def list_entries(cache_type: str) -> list[str]:
    """Relative paths of cached artifacts of ``cache_type`` (sidecars excluded)."""
    base = storage.join(storage.cache_root(), "s2", cache_type)
    if not os.path.isdir(base):
        return []
    out: list[str] = []
    for root, _dirs, files in os.walk(base):
        for fn in files:
            if fn.endswith(SIDECAR_SUFFIX):
                continue
            out.append(os.path.relpath(os.path.join(root, fn), base))
    return sorted(out)

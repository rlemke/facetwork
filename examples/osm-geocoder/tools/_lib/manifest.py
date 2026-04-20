"""Shared manifest I/O for OSM cache directories.

Each cache subdirectory under ``$AFL_OSM_CACHE_ROOT`` (default
``/Volumes/afl_data/osm``) contains a ``manifest.json`` that tracks
downloaded files. Writes are atomic (``os.replace``) and serialized via
an ``fcntl`` advisory lock on ``manifest.json.lock`` so concurrent tools
cannot corrupt the index.

The manifest schema is intentionally simple and forward-compatible:

    {
      "version": 1,
      "entries": {
        "<relative/path/inside/cache/subdir>": {
          "relative_path": "...",
          "source_url": "...",
          "size_bytes": 12345,
          "sha256": "...",
          "source_checksum": {"algo": "md5", "value": "...", "url": "...md5"},
          "downloaded_at": "2026-04-20T14:03:22Z",
          "source_timestamp": "2026-04-18T21:22:02Z",
          "extra": { ... }
        }
      }
    }

Readers must tolerate unknown fields inside entries. Bump ``version``
only for breaking layout changes.
"""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

MANIFEST_VERSION = 1
DEFAULT_CACHE_ROOT = "/Volumes/afl_data/osm"


def cache_root() -> Path:
    """Return the OSM cache root directory (``$AFL_OSM_CACHE_ROOT`` or default)."""
    return Path(os.environ.get("AFL_OSM_CACHE_ROOT", DEFAULT_CACHE_ROOT))


def cache_dir(cache_type: str) -> Path:
    """Return the subdirectory for a given cache type (``pbf``, ``geojson``, ...)."""
    return cache_root() / cache_type


def manifest_path(cache_type: str) -> Path:
    return cache_dir(cache_type) / "manifest.json"


def lock_path(cache_type: str) -> Path:
    return cache_dir(cache_type) / "manifest.json.lock"


def utcnow_iso() -> str:
    """Current UTC time formatted as an ISO-8601 ``Z`` string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _empty_manifest() -> dict[str, Any]:
    return {"version": MANIFEST_VERSION, "entries": {}}


def _read_manifest_unlocked(path: Path) -> dict[str, Any]:
    if not path.exists():
        return _empty_manifest()
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict) or "entries" not in data:
        raise ValueError(f"Malformed manifest at {path}: missing 'entries'")
    data.setdefault("version", MANIFEST_VERSION)
    return data


def _write_manifest_atomic(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=".manifest.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp:
            json.dump(data, tmp, indent=2, sort_keys=True)
            tmp.write("\n")
            tmp.flush()
            os.fsync(tmp.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


@contextmanager
def _lock(cache_type: str, *, exclusive: bool) -> Iterator[None]:
    cdir = cache_dir(cache_type)
    cdir.mkdir(parents=True, exist_ok=True)
    lpath = lock_path(cache_type)
    with open(lpath, "a+", encoding="utf-8") as lockf:
        fcntl.flock(lockf.fileno(), fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        try:
            yield
        finally:
            fcntl.flock(lockf.fileno(), fcntl.LOCK_UN)


@contextmanager
def manifest_transaction(cache_type: str) -> Iterator[dict[str, Any]]:
    """Exclusive read-modify-write of a cache-type manifest.

    Usage::

        with manifest_transaction("pbf") as manifest:
            manifest["entries"]["europe/germany/berlin-latest.osm.pbf"] = {...}

    The manifest is read under the lock, the dict is yielded for mutation,
    and then atomically written back before the lock is released.
    """
    with _lock(cache_type, exclusive=True):
        manifest = _read_manifest_unlocked(manifest_path(cache_type))
        yield manifest
        _write_manifest_atomic(manifest_path(cache_type), manifest)


def read_manifest(cache_type: str) -> dict[str, Any]:
    """Return a snapshot of the manifest under a shared lock.

    Safe against concurrent writers. The returned dict is a fresh copy —
    mutating it does not persist. Use ``manifest_transaction`` to modify.
    """
    with _lock(cache_type, exclusive=False):
        return _read_manifest_unlocked(manifest_path(cache_type))

"""Shared manifest I/O for OSM cache directories.

Manifests live at ``<cache_root>/<cache_type>/manifest.json`` where
``cache_root`` is resolved per storage backend (local filesystem or HDFS).
All file I/O is routed through the ``Storage`` abstraction in
``_lib.storage``; see that module for backend-specific notes (notably
HDFS's single-writer semantics — it does not support advisory locking).

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

Readers must tolerate unknown fields inside entries. Bump ``version`` only
for breaking layout changes.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from _lib.storage import LocalStorage, Storage, default_cache_root

MANIFEST_VERSION = 1


def _storage(storage: Storage | None) -> Storage:
    return storage if storage is not None else LocalStorage()


def cache_root(storage: Storage | None = None) -> str:
    s = _storage(storage)
    return default_cache_root(s.name)


def cache_dir(cache_type: str, storage: Storage | None = None) -> str:
    s = _storage(storage)
    return Storage.join(cache_root(s), cache_type)


def manifest_path(cache_type: str, storage: Storage | None = None) -> str:
    s = _storage(storage)
    return Storage.join(cache_dir(cache_type, s), "manifest.json")


def lock_path(cache_type: str, storage: Storage | None = None) -> str:
    s = _storage(storage)
    return Storage.join(cache_dir(cache_type, s), "manifest.json.lock")


def utcnow_iso() -> str:
    """Current UTC time formatted as an ISO-8601 ``Z`` string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _empty_manifest() -> dict[str, Any]:
    return {"version": MANIFEST_VERSION, "entries": {}}


def _read_unlocked(storage: Storage, path: str) -> dict[str, Any]:
    if not storage.exists(path):
        return _empty_manifest()
    data = json.loads(storage.read_text(path))
    if not isinstance(data, dict) or "entries" not in data:
        raise ValueError(f"Malformed manifest at {path}: missing 'entries'")
    data.setdefault("version", MANIFEST_VERSION)
    return data


def _write_atomic(storage: Storage, path: str, data: dict[str, Any]) -> None:
    text = json.dumps(data, indent=2, sort_keys=True) + "\n"
    storage.write_text_atomic(path, text)


@contextmanager
def manifest_transaction(
    cache_type: str, storage: Storage | None = None
) -> Iterator[dict[str, Any]]:
    """Exclusive read-modify-write of a cache-type manifest.

    On the local backend this is serialized across processes by an
    ``fcntl`` advisory lock on ``manifest.json.lock``. On HDFS the lock is
    a no-op; callers must ensure single-writer access externally.

    Usage::

        with manifest_transaction("pbf", storage) as manifest:
            manifest["entries"]["..."] = {...}
    """
    s = _storage(storage)
    s.mkdir_p(cache_dir(cache_type, s))
    with s.lock(lock_path(cache_type, s), exclusive=True):
        manifest = _read_unlocked(s, manifest_path(cache_type, s))
        yield manifest
        _write_atomic(s, manifest_path(cache_type, s), manifest)


def read_manifest(
    cache_type: str, storage: Storage | None = None
) -> dict[str, Any]:
    """Return a snapshot of the manifest under a shared lock (where supported).

    The returned dict is a fresh copy — mutating it does not persist. Use
    ``manifest_transaction`` to modify.
    """
    s = _storage(storage)
    s.mkdir_p(cache_dir(cache_type, s))
    with s.lock(lock_path(cache_type, s), exclusive=False):
        return _read_unlocked(s, manifest_path(cache_type, s))

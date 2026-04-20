"""Shared Geofabrik PBF download library.

This module is the single source of truth for downloading Geofabrik PBF
files into the OSM cache. Both the ``download-pbf`` CLI tool and the
Facetwork FFL handlers (``osm.ops.CacheRegion``) call ``download_region``
here, so they share:

- the same on-disk layout (``<cache_root>/pbf/<region>-latest.osm.pbf``
  mirroring Geofabrik's hierarchy),
- the same manifest (``<cache_root>/pbf/manifest.json``) with SHA-256,
  MD5, source timestamp, and download timestamp per entry,
- the same upstream MD5 verification on every fresh download.

Thread-safety:

- A per-region ``threading.Lock`` ensures that if the handler runtime
  invokes ``CacheRegion`` concurrently for the same region, only one
  download actually happens — the second caller waits and then picks up
  the cached result.
- A module-level ``threading.Lock`` serializes manifest read-modify-write
  across threads within one process. The underlying ``fcntl`` advisory
  lock in ``manifest.manifest_transaction`` handles cross-process
  serialization on the local backend.

HDFS notes: the storage abstraction supports an HDFS backend, but HDFS
does not support advisory locking — single-writer semantics are assumed
for HDFS caches. See ``_lib/storage.py``.
"""

from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import timezone
from email.utils import parsedate_to_datetime
from typing import Any, Callable

try:
    import requests
except ImportError:  # pragma: no cover - optional, only needed for bulk streaming
    requests = None

from _lib.manifest import (
    cache_dir,
    manifest_transaction,
    read_manifest,
    utcnow_iso,
)
from _lib.storage import LocalStorage, Storage

CACHE_TYPE = "pbf"
GEOFABRIK_BASE = "https://download.geofabrik.de"
USER_AGENT = "facetwork-osm-geocoder/1.0 (OSM PBF downloader)"
CHUNK_SIZE = 1024 * 1024  # 1 MiB

ProgressCallback = Callable[[str, int, int, bool], None]
"""Progress callback: (label, bytes_so_far, total_bytes, is_final)."""


@dataclass
class DownloadResult:
    """Outcome of a ``download_region`` call."""

    region: str
    path: str                   # absolute filesystem/HDFS path to the cached file
    relative_path: str          # path relative to the pbf/ cache dir
    source_url: str
    size_bytes: int
    sha256: str
    md5: str
    source_timestamp: str | None
    downloaded_at: str
    was_cached: bool            # True if the download was skipped (manifest was up-to-date)
    manifest_entry: dict[str, Any] = field(default_factory=dict)


class DownloadError(RuntimeError):
    """Raised when a download fails (network, MD5 mismatch, etc.)."""


# In-process concurrency primitives. These are module-level because the
# manifest and per-region state are process-wide.
_region_locks: dict[str, threading.Lock] = {}
_region_locks_guard = threading.Lock()
_manifest_write_lock = threading.Lock()


def _region_lock(region: str) -> threading.Lock:
    with _region_locks_guard:
        lock = _region_locks.get(region)
        if lock is None:
            lock = threading.Lock()
            _region_locks[region] = lock
        return lock


def _local_staging_dir() -> str:
    """Return the local-disk staging directory for in-flight PBF downloads.

    Honors ``$AFL_OSM_LOCAL_TMP_DIR`` if set; otherwise uses the system
    temp dir (``$TMPDIR`` / ``/tmp``). Staging keeps the socket-read rate
    decoupled from the destination write rate — writing directly to a
    network-attached volume can stall the TCP receive window on a slow
    mount, masquerading as a hung download.
    """
    base = os.environ.get("AFL_OSM_LOCAL_TMP_DIR") or tempfile.gettempdir()
    staging = os.path.join(base, "facetwork-pbf-staging")
    os.makedirs(staging, exist_ok=True)
    return staging


def staging_path(region: str) -> str:
    """Path on local disk where a region is staged before finalization.

    Public so callers (e.g. the CLI progress display) can report it.
    """
    safe = region.replace("/", "_")
    return os.path.join(_local_staging_dir(), f"{safe}-latest.osm.pbf.tmp")


def region_to_paths(region: str) -> tuple[str, str]:
    """Return ``(relative_path, remote_url)`` for a Geofabrik region key."""
    region = region.strip().strip("/")
    if not region:
        raise ValueError("Empty region")
    rel = f"{region}-latest.osm.pbf"
    url = f"{GEOFABRIK_BASE}/{rel}"
    return rel, url


def _request(url: str, method: str = "GET") -> urllib.request.Request:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    req.get_method = lambda m=method: m
    return req


def fetch_md5(url: str) -> str:
    """Fetch Geofabrik's ``.md5`` file for ``url``; return the hex digest."""
    md5_url = url + ".md5"
    with urllib.request.urlopen(_request(md5_url), timeout=30) as resp:
        body = resp.read().decode("utf-8").strip()
    parts = body.split()
    if not parts or len(parts[0]) != 32:
        raise DownloadError(f"Unexpected .md5 body from {md5_url}: {body!r}")
    return parts[0].lower()


def head_last_modified(url: str) -> str | None:
    """Best-effort HEAD for upstream ``Last-Modified``; ISO-8601 UTC or ``None``."""
    try:
        with urllib.request.urlopen(_request(url, "HEAD"), timeout=30) as resp:
            lm = resp.headers.get("Last-Modified")
    except urllib.error.URLError:
        return None
    if not lm:
        return None
    try:
        dt = parsedate_to_datetime(lm)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _already_cached(
    manifest: dict[str, Any],
    rel_path: str,
    expected_md5: str,
    cache_file: str,
    storage: Storage,
) -> bool:
    entry = manifest.get("entries", {}).get(rel_path)
    if not entry:
        return False
    if entry.get("source_checksum", {}).get("value") != expected_md5:
        return False
    if not storage.exists(cache_file):
        return False
    return storage.size(cache_file) == entry.get("size_bytes")


STREAM_CHUNK_SIZE = 64 * 1024   # 64 KiB, matches handlers/shared/downloader.py
READ_TIMEOUT_SECONDS = 120      # per-read stall timeout
CONNECT_TIMEOUT_SECONDS = 30


def _stream_download(
    url: str,
    writer,
    label: str,
    on_progress: ProgressCallback | None,
) -> tuple[int, str, str]:
    """Stream ``url`` into ``writer`` using ``requests``; compute SHA-256 and MD5.

    Uses the ``requests`` library with (connect, read) timeouts. The read
    timeout is enforced per underlying socket read, so a stalled server
    surfaces as a clear ``ReadTimeout`` error rather than a silent hang.
    ``iter_content`` yields chunks as the socket delivers them, so progress
    updates are responsive on slow links.

    Falls back to ``urllib.request`` if the ``requests`` library is not
    installed, though that path does not detect stalls as reliably.
    """
    if requests is None:
        return _stream_download_urllib(url, writer, label, on_progress)

    sha = hashlib.sha256()
    md5 = hashlib.md5()
    size = 0
    start = time.monotonic()
    last_report = start
    last_bytes_at = start

    with requests.get(
        url,
        stream=True,
        headers={"User-Agent": USER_AGENT},
        timeout=(CONNECT_TIMEOUT_SECONDS, READ_TIMEOUT_SECONDS),
    ) as resp:
        resp.raise_for_status()
        total = int(resp.headers.get("Content-Length") or 0)
        for chunk in resp.iter_content(chunk_size=STREAM_CHUNK_SIZE):
            if not chunk:
                continue
            writer.write(chunk)
            sha.update(chunk)
            md5.update(chunk)
            size += len(chunk)
            now = time.monotonic()
            last_bytes_at = now
            if on_progress and now - last_report >= 2.0:
                on_progress(label, size, total, False)
                last_report = now
    if on_progress:
        on_progress(label, size, total or size, True)
    _ = last_bytes_at  # reserved for future stall heuristics
    return size, sha.hexdigest(), md5.hexdigest().lower()


def _stream_download_urllib(
    url: str,
    writer,
    label: str,
    on_progress: ProgressCallback | None,
) -> tuple[int, str, str]:
    """Fallback streaming path using ``urllib.request`` (no ``requests``)."""
    sha = hashlib.sha256()
    md5 = hashlib.md5()
    size = 0
    start = time.monotonic()
    last_report = start
    with urllib.request.urlopen(_request(url), timeout=READ_TIMEOUT_SECONDS) as resp:
        total = int(resp.headers.get("Content-Length") or 0)
        while True:
            chunk = resp.read1(STREAM_CHUNK_SIZE)
            if not chunk:
                break
            writer.write(chunk)
            sha.update(chunk)
            md5.update(chunk)
            size += len(chunk)
            now = time.monotonic()
            if on_progress and now - last_report >= 2.0:
                on_progress(label, size, total, False)
                last_report = now
    if on_progress:
        on_progress(label, size, total or size, True)
    return size, sha.hexdigest(), md5.hexdigest().lower()


def is_region_cached(
    region: str, *, storage: Storage | None = None
) -> bool:
    """Quick check whether a region is cached and its local file still exists.

    Does **not** contact Geofabrik — for that, use ``download_region`` and
    inspect ``was_cached`` in the result.
    """
    storage = storage or LocalStorage()
    rel_path, _ = region_to_paths(region)
    manifest = read_manifest(CACHE_TYPE, storage)
    entry = manifest.get("entries", {}).get(rel_path)
    if not entry:
        return False
    cache_file = Storage.join(cache_dir(CACHE_TYPE, storage), rel_path)
    if not storage.exists(cache_file):
        return False
    return storage.size(cache_file) == entry.get("size_bytes")


def manifest_entry_for(
    region: str, *, storage: Storage | None = None
) -> dict[str, Any] | None:
    """Return the manifest entry for ``region`` if present, else ``None``."""
    storage = storage or LocalStorage()
    rel_path, _ = region_to_paths(region)
    manifest = read_manifest(CACHE_TYPE, storage)
    return manifest.get("entries", {}).get(rel_path)


def cached_path(
    region: str, *, storage: Storage | None = None
) -> str:
    """Return the absolute cache path for ``region`` (whether or not it exists)."""
    storage = storage or LocalStorage()
    rel_path, _ = region_to_paths(region)
    return Storage.join(cache_dir(CACHE_TYPE, storage), rel_path)


def download_region(
    region: str,
    *,
    storage: Storage | None = None,
    force: bool = False,
    on_progress: ProgressCallback | None = None,
) -> DownloadResult:
    """Download a Geofabrik PBF for ``region`` into the OSM cache.

    This is the single entry point used by both the CLI tool and the FFL
    handlers. The function is thread-safe: concurrent calls for the same
    region serialize on a per-region lock, so only one download happens
    and the other caller(s) observe ``was_cached=True``.

    Args:
        region: Geofabrik region path (e.g. ``"africa/algeria"``,
            ``"europe/germany/berlin"``), without the ``-latest.osm.pbf``
            suffix.
        storage: Storage backend (default ``LocalStorage``).
        force: If True, re-download even if the manifest reports an
            up-to-date cached copy.
        on_progress: Optional progress callback; see ``ProgressCallback``.

    Returns:
        ``DownloadResult`` with the cached file path, metadata, and
        ``was_cached`` flag.

    Raises:
        DownloadError: On network failure or MD5 mismatch against
            Geofabrik's published ``.md5``.
    """
    storage = storage or LocalStorage()
    rel_path, url = region_to_paths(region)
    cdir = cache_dir(CACHE_TYPE, storage)
    cache_file = Storage.join(cdir, rel_path)

    with _region_lock(region):
        storage.mkdir_p(Storage.dirname(cache_file))

        expected_md5 = fetch_md5(url)
        source_ts = head_last_modified(url)

        if not force:
            manifest = read_manifest(CACHE_TYPE, storage)
            if _already_cached(manifest, rel_path, expected_md5, cache_file, storage):
                entry = manifest["entries"][rel_path]
                return DownloadResult(
                    region=region,
                    path=cache_file,
                    relative_path=rel_path,
                    source_url=url,
                    size_bytes=entry.get("size_bytes", storage.size(cache_file)),
                    sha256=entry.get("sha256", ""),
                    md5=entry.get("source_checksum", {}).get("value", expected_md5),
                    source_timestamp=entry.get("source_timestamp"),
                    downloaded_at=entry.get("downloaded_at", ""),
                    was_cached=True,
                    manifest_entry=entry,
                )

        # Stage the download onto local disk first. Writing directly to
        # the destination (which may be a slow network- or USB-attached
        # volume) can stall the TCP receive window and look like a hung
        # download. Local staging decouples socket-read from the possibly
        # slow finalize copy.
        staged = staging_path(region)
        if os.path.exists(staged):
            os.unlink(staged)

        try:
            with open(staged, "wb") as writer:
                size, sha256_hex, md5_hex = _stream_download(
                    url, writer, region, on_progress
                )
        except BaseException:
            if os.path.exists(staged):
                os.unlink(staged)
            raise

        if md5_hex != expected_md5:
            os.unlink(staged)
            raise DownloadError(
                f"MD5 mismatch for {region}: upstream={expected_md5} computed={md5_hex}"
            )

        storage.finalize_from_local(staged, cache_file)

        downloaded_at = utcnow_iso()
        entry = {
            "relative_path": rel_path,
            "source_url": url,
            "size_bytes": size,
            "sha256": sha256_hex,
            "source_checksum": {
                "algo": "md5",
                "value": md5_hex,
                "url": url + ".md5",
            },
            "downloaded_at": downloaded_at,
            "source_timestamp": source_ts,
            "extra": {"region": region},
        }
        with _manifest_write_lock, manifest_transaction(
            CACHE_TYPE, storage
        ) as manifest:
            manifest.setdefault("entries", {})[rel_path] = entry

        return DownloadResult(
            region=region,
            path=cache_file,
            relative_path=rel_path,
            source_url=url,
            size_bytes=size,
            sha256=sha256_hex,
            md5=md5_hex,
            source_timestamp=source_ts,
            downloaded_at=downloaded_at,
            was_cached=False,
            manifest_entry=entry,
        )


def to_osm_cache(result: DownloadResult) -> dict[str, Any]:
    """Convert a ``DownloadResult`` into the ``OSMCache`` dict shape that
    the FFL handlers return downstream.

    The ``OSMCache`` schema (see ``osm.types.OSMCache``) is::

        { url, path, date, size, wasInCache }

    Handlers also include a ``source`` field by convention.
    """
    return {
        "url": result.source_url,
        "path": result.path,
        "date": result.downloaded_at,
        "size": result.size_bytes,
        "wasInCache": result.was_cached,
        "source": "cache" if result.was_cached else "download",
    }

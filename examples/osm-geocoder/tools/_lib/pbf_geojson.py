"""Shared PBF → GeoJSON conversion library.

Single source of truth for converting cached PBFs to GeoJSON via
``osmium export``. Used by both the ``convert-pbf-geojson`` CLI tool
and the FFL ``osm.ops.ConvertPbfToGeoJson`` handler, so they share the
same on-disk layout, the same manifest, and the same skip logic.

Thread safety:

- Per-region ``threading.Lock`` serializes concurrent calls for the
  same region so only one conversion happens and the second caller
  observes ``was_cached=True``.
- Module-level lock wraps the manifest read-modify-write across threads
  within one process. Cross-process is handled by ``manifest_transaction``'s
  ``fcntl`` advisory lock on the local backend.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from _lib.manifest import (
    cache_dir,
    manifest_transaction,
    read_manifest,
    utcnow_iso,
)
from _lib.storage import LocalStorage

SOURCE_CACHE_TYPE = "pbf"
OUTPUT_CACHE_TYPE = "geojson"
FORMAT_EXT = {"geojson": "geojson", "geojsonseq": "geojsonseq"}
DEFAULT_FORMAT = "geojsonseq"
CHUNK_SIZE = 1024 * 1024

_region_locks: dict[str, threading.Lock] = {}
_region_locks_guard = threading.Lock()
_manifest_write_lock = threading.Lock()


def _region_lock(region: str, fmt: str) -> threading.Lock:
    key = f"{region}::{fmt}"
    with _region_locks_guard:
        lock = _region_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _region_locks[key] = lock
        return lock


@dataclass
class ConvertResult:
    """Outcome of a ``convert_region`` call."""

    region: str
    path: str                   # absolute path to the GeoJSON file
    relative_path: str          # path relative to the geojson cache dir
    format: str
    size_bytes: int
    sha256: str
    generated_at: str
    duration_seconds: float
    was_cached: bool
    source_url: str             # Geofabrik URL of the source PBF
    source_pbf_path: str        # absolute path of the source PBF
    manifest_entry: dict[str, Any] = field(default_factory=dict)


class ConversionError(RuntimeError):
    """Raised when a conversion fails (osmium failure, missing PBF, etc.)."""


def pbf_rel_path(region: str) -> str:
    return f"{region}-latest.osm.pbf"


def pbf_abs_path(region: str) -> Path:
    return Path(cache_dir(SOURCE_CACHE_TYPE)) / pbf_rel_path(region)


def geojson_rel_path(region: str, fmt: str) -> str:
    return f"{region}-latest.{FORMAT_EXT[fmt]}"


def geojson_abs_path(region: str, fmt: str) -> Path:
    return Path(cache_dir(OUTPUT_CACHE_TYPE)) / geojson_rel_path(region, fmt)


def _osmium_version(osmium_bin: str) -> str:
    try:
        result = subprocess.run(
            [osmium_bin, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        first_line = (result.stdout or "").splitlines()
        return first_line[0].strip() if first_line else "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def _sha256_file(path: Path) -> tuple[int, str]:
    sha = hashlib.sha256()
    size = 0
    with path.open("rb") as f:
        while True:
            chunk = f.read(CHUNK_SIZE)
            if not chunk:
                break
            sha.update(chunk)
            size += len(chunk)
    return size, sha.hexdigest()


def _staging_path(region: str, fmt: str) -> Path:
    base = os.environ.get("AFL_OSM_LOCAL_TMP_DIR") or tempfile.gettempdir()
    safe = region.replace("/", "_")
    return Path(base) / "facetwork-geojson-staging" / f"{safe}-latest.{FORMAT_EXT[fmt]}.tmp"


def is_up_to_date(region: str, fmt: str, pbf_entry: dict, out_abs: Path) -> bool:
    """True if the cached GeoJSON still matches the source PBF's SHA-256."""
    geo_manifest = read_manifest(OUTPUT_CACHE_TYPE)
    out_rel = geojson_rel_path(region, fmt)
    existing = geo_manifest.get("entries", {}).get(out_rel)
    if not existing:
        return False
    if existing.get("format") != fmt:
        return False
    if existing.get("source", {}).get("sha256") != pbf_entry.get("sha256"):
        return False
    if not out_abs.exists():
        return False
    return out_abs.stat().st_size == existing.get("size_bytes")


def convert_region(
    region: str,
    *,
    fmt: str = DEFAULT_FORMAT,
    force: bool = False,
    osmium_bin: str = "osmium",
) -> ConvertResult:
    """Convert a region's PBF to GeoJSON. Thread-safe per (region, fmt)."""
    if fmt not in FORMAT_EXT:
        raise ConversionError(f"unknown format: {fmt!r} (valid: {', '.join(FORMAT_EXT)})")

    pbf_manifest = read_manifest(SOURCE_CACHE_TYPE)
    pbf_rel = pbf_rel_path(region)
    pbf_entry = pbf_manifest.get("entries", {}).get(pbf_rel)
    if not pbf_entry:
        raise ConversionError(
            f"no pbf manifest entry for {region!r}; run download-pbf first"
        )
    src_pbf = pbf_abs_path(region)
    if not src_pbf.exists():
        raise ConversionError(f"pbf file missing on disk: {src_pbf}")
    source_url = pbf_entry.get("source_url", "")

    with _region_lock(region, fmt):
        out_abs = geojson_abs_path(region, fmt)
        out_rel = geojson_rel_path(region, fmt)

        if not force and is_up_to_date(region, fmt, pbf_entry, out_abs):
            existing = read_manifest(OUTPUT_CACHE_TYPE).get("entries", {}).get(out_rel, {})
            return ConvertResult(
                region=region,
                path=str(out_abs),
                relative_path=out_rel,
                format=fmt,
                size_bytes=existing.get("size_bytes", out_abs.stat().st_size),
                sha256=existing.get("sha256", ""),
                generated_at=existing.get("generated_at", ""),
                duration_seconds=0.0,
                was_cached=True,
                source_url=source_url,
                source_pbf_path=str(src_pbf),
                manifest_entry=existing,
            )

        out_abs.parent.mkdir(parents=True, exist_ok=True)
        staging = _staging_path(region, fmt)
        staging.parent.mkdir(parents=True, exist_ok=True)
        if staging.exists():
            staging.unlink()

        cmd = [
            osmium_bin,
            "export",
            "-f",
            fmt,
            "-o",
            str(staging),
            "--overwrite",
            str(src_pbf),
        ]
        start = time.monotonic()
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as exc:
            if staging.exists():
                staging.unlink()
            stderr = (exc.stderr or "").strip()
            raise ConversionError(f"osmium export failed: {stderr or exc}") from exc
        except BaseException:
            if staging.exists():
                staging.unlink()
            raise
        elapsed = time.monotonic() - start

        size, sha256_hex = _sha256_file(staging)

        storage = LocalStorage()
        storage.finalize_from_local(str(staging), str(out_abs))

        generated_at = utcnow_iso()
        entry = {
            "relative_path": out_rel,
            "format": fmt,
            "size_bytes": size,
            "sha256": sha256_hex,
            "generated_at": generated_at,
            "duration_seconds": round(elapsed, 2),
            "source": {
                "cache_type": SOURCE_CACHE_TYPE,
                "relative_path": pbf_rel,
                "sha256": pbf_entry.get("sha256"),
                "size_bytes": pbf_entry.get("size_bytes"),
                "source_checksum": pbf_entry.get("source_checksum"),
                "source_timestamp": pbf_entry.get("source_timestamp"),
                "downloaded_at": pbf_entry.get("downloaded_at"),
            },
            "tool": {
                "command": "osmium export",
                "osmium_version": _osmium_version(osmium_bin),
            },
            "extra": {"region": region},
        }
        with _manifest_write_lock, manifest_transaction(OUTPUT_CACHE_TYPE) as manifest:
            manifest.setdefault("entries", {})[out_rel] = entry

        return ConvertResult(
            region=region,
            path=str(out_abs),
            relative_path=out_rel,
            format=fmt,
            size_bytes=size,
            sha256=sha256_hex,
            generated_at=generated_at,
            duration_seconds=elapsed,
            was_cached=False,
            source_url=source_url,
            source_pbf_path=str(src_pbf),
            manifest_entry=entry,
        )


def to_osm_cache(result: ConvertResult) -> dict[str, Any]:
    """Map a ``ConvertResult`` to the ``OSMCache`` dict FFL handlers return."""
    return {
        "url": result.source_url,
        "path": result.path,
        "date": result.generated_at,
        "size": result.size_bytes,
        "wasInCache": result.was_cached,
        "source": "cache" if result.was_cached else "convert",
    }

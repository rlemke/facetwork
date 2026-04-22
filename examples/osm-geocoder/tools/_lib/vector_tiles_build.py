"""Vector-tile build library — GeoJSONSeq → PMTiles via ``tippecanoe``.

Takes any pre-filtered GeoJSONSeq produced by the tool set (the
whole-region ``geojson/`` output or a category extract like
``water/``, ``parks/``, ``roads_routable/``, etc.) and produces a
PMTiles file ready for web rendering.

Cache layout::

    <cache_root>/vector_tiles/<region>-latest/<source>.pmtiles
    <cache_root>/vector_tiles/manifest.json

Manifest key is ``<region>-latest/<source>``. The ``<source>`` token
is either ``geojson`` (the whole-region extract) or any category name
from ``pbf_extract.CATEGORIES`` — the same identifier used on the CLI.

Cache validity requires all three:

- Source GeoJSONSeq's SHA-256 still matches what we recorded when
  we built this tileset (reads the producing tool's manifest),
- ``tippecanoe_version`` still matches, AND
- The tiling options (min/max zoom, layer name) still match.

Any difference triggers a rebuild.
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
from _lib.pbf_extract import CATEGORIES
from _lib.storage import LocalStorage

OUTPUT_CACHE_TYPE = "vector_tiles"

# Output format and the default tippecanoe options we use for balanced
# results on OSM-style data. Users can override via the CLI/library args.
DEFAULT_MIN_ZOOM = 0
DEFAULT_MAX_ZOOM = 14
DEFAULT_TIMEOUT_SECONDS = 3600
CHUNK_SIZE = 1024 * 1024

_build_locks: dict[tuple[str, str], threading.Lock] = {}
_build_locks_guard = threading.Lock()
_manifest_write_lock = threading.Lock()


def _build_lock(region: str, source: str) -> threading.Lock:
    key = (region, source)
    with _build_locks_guard:
        lock = _build_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _build_locks[key] = lock
        return lock


# The "source" identifier controls which input GeoJSONSeq we tile.
# ``geojson`` is the whole-region extract produced by convert-pbf-geojson.
# Any other value must be an extract category name.
GEOJSON_SOURCE = "geojson"


def valid_sources() -> list[str]:
    return [GEOJSON_SOURCE, *sorted(CATEGORIES)]


def _source_cache_type(source: str) -> str:
    """The cache subdirectory name that produces ``source`` GeoJSONSeq files."""
    if source == GEOJSON_SOURCE:
        return "geojson"
    if source in CATEGORIES:
        return source
    raise BuildError(
        f"unknown source: {source!r}. Valid: {', '.join(valid_sources())}"
    )


def _source_rel_path(source: str, region: str) -> str:
    """Manifest key within the source cache_type for the given region."""
    return f"{region}-latest.geojsonseq"


def _source_abs_path(source: str, region: str) -> Path:
    return Path(cache_dir(_source_cache_type(source))) / _source_rel_path(source, region)


def _source_manifest_entry(source: str, region: str) -> dict | None:
    return read_manifest(_source_cache_type(source)).get("entries", {}).get(
        _source_rel_path(source, region)
    )


@dataclass
class BuildResult:
    """Outcome of a ``build_tiles`` call."""

    region: str
    source: str
    path: str                   # absolute path to the .pmtiles file
    relative_path: str          # relative to vector_tiles/ cache dir
    size_bytes: int
    sha256: str
    min_zoom: int
    max_zoom: int
    layer_name: str
    tippecanoe_version: str
    generated_at: str
    duration_seconds: float
    was_cached: bool
    source_path: str
    source_sha256: str
    manifest_entry: dict[str, Any] = field(default_factory=dict)


class BuildError(RuntimeError):
    """Raised when vector-tile build fails."""


def tileset_rel_path(region: str, source: str) -> str:
    return f"{region}-latest/{source}.pmtiles"


def tileset_abs_path(region: str, source: str) -> Path:
    return Path(cache_dir(OUTPUT_CACHE_TYPE)) / tileset_rel_path(region, source)


def _staging_path(region: str, source: str) -> Path:
    """Stage adjacent to the final destination. Override with
    ``AFL_OSM_CONVERT_STAGING=tmp`` to fall back to local tmp.
    """
    if (os.environ.get("AFL_OSM_CONVERT_STAGING") or "").lower() == "tmp":
        base = os.environ.get("AFL_OSM_LOCAL_TMP_DIR") or tempfile.gettempdir()
        safe = region.replace("/", "_")
        return Path(base) / "facetwork-vector-tiles-staging" / safe / f"{source}.pmtiles"
    out = tileset_abs_path(region, source)
    return out.with_name(out.name + ".tmp")


def _tippecanoe_version(tippecanoe_bin: str) -> str:
    try:
        result = subprocess.run(
            [tippecanoe_bin, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        # tippecanoe prints version to stderr, most versions
        out = result.stdout or result.stderr or ""
        first = out.splitlines()
        return first[0].strip() if first else "unknown"
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


def is_up_to_date(
    region: str,
    source: str,
    *,
    min_zoom: int,
    max_zoom: int,
    layer_name: str,
) -> bool:
    """True if cached tileset matches current source SHA + build options."""
    cache_manifest = read_manifest(OUTPUT_CACHE_TYPE)
    rel = tileset_rel_path(region, source)
    existing = cache_manifest.get("entries", {}).get(rel)
    if not existing:
        return False
    source_entry = _source_manifest_entry(source, region)
    if not source_entry:
        return False
    if existing.get("source", {}).get("sha256") != source_entry.get("sha256"):
        return False
    opts = existing.get("options", {})
    if opts.get("min_zoom") != min_zoom:
        return False
    if opts.get("max_zoom") != max_zoom:
        return False
    if opts.get("layer_name") != layer_name:
        return False
    out_abs = tileset_abs_path(region, source)
    if not out_abs.exists():
        return False
    return out_abs.stat().st_size == existing.get("size_bytes")


def build_tiles(
    region: str,
    source: str = GEOJSON_SOURCE,
    *,
    min_zoom: int = DEFAULT_MIN_ZOOM,
    max_zoom: int = DEFAULT_MAX_ZOOM,
    layer_name: str | None = None,
    force: bool = False,
    tippecanoe_bin: str = "tippecanoe",
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> BuildResult:
    """Build a vector-tile PMTiles file from a GeoJSONSeq source."""
    if source not in valid_sources():
        raise BuildError(
            f"unknown source: {source!r}. Valid: {', '.join(valid_sources())}"
        )
    if not (0 <= min_zoom <= max_zoom <= 22):
        raise BuildError(
            f"invalid zoom range: min={min_zoom} max={max_zoom} (must satisfy 0 <= min <= max <= 22)"
        )
    # Default layer name: the source itself is a good canonical label.
    effective_layer_name = layer_name or source

    src_entry = _source_manifest_entry(source, region)
    if not src_entry:
        raise BuildError(
            f"no {_source_cache_type(source)!r} manifest entry for region "
            f"{region!r}. Run the producing tool first "
            f"({'convert-pbf-geojson' if source == GEOJSON_SOURCE else f'extract {source}'})."
        )
    src_path = _source_abs_path(source, region)
    if not src_path.exists():
        raise BuildError(f"source geojsonseq missing on disk: {src_path}")
    src_sha = src_entry.get("sha256", "")

    with _build_lock(region, source):
        out_abs = tileset_abs_path(region, source)
        rel = tileset_rel_path(region, source)

        if not force and is_up_to_date(
            region,
            source,
            min_zoom=min_zoom,
            max_zoom=max_zoom,
            layer_name=effective_layer_name,
        ):
            existing = read_manifest(OUTPUT_CACHE_TYPE).get("entries", {}).get(rel, {})
            return BuildResult(
                region=region,
                source=source,
                path=str(out_abs),
                relative_path=rel,
                size_bytes=existing.get("size_bytes", out_abs.stat().st_size),
                sha256=existing.get("sha256", ""),
                min_zoom=min_zoom,
                max_zoom=max_zoom,
                layer_name=effective_layer_name,
                tippecanoe_version=existing.get("tool", {}).get("tippecanoe_version", ""),
                generated_at=existing.get("generated_at", ""),
                duration_seconds=0.0,
                was_cached=True,
                source_path=str(src_path),
                source_sha256=src_sha,
                manifest_entry=existing,
            )

        staging = _staging_path(region, source)
        staging.parent.mkdir(parents=True, exist_ok=True)
        if staging.exists():
            staging.unlink()

        # tippecanoe (mapbox fork) outputs MBTiles; we convert to PMTiles
        # afterwards via the ``pmtiles`` CLI.
        # staging may be "foo.pmtiles" or "foo.pmtiles.tmp" — place the
        # mbtiles sibling next to it with a clean ".mbtiles" extension.
        mbtiles_staging = staging.with_name(source + ".mbtiles")
        if mbtiles_staging.exists():
            mbtiles_staging.unlink()

        cmd = [
            tippecanoe_bin,
            "-o",
            str(mbtiles_staging),
            "-Z",
            str(min_zoom),
            "-z",
            str(max_zoom),
            "--force",
            "--layer",
            effective_layer_name,
            "--drop-densest-as-needed",
            "--coalesce-densest-as-needed",
            "--read-parallel",
            str(src_path),
        ]
        start = time.monotonic()
        try:
            subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=True,
            )
        except subprocess.CalledProcessError as exc:
            mbtiles_staging.unlink(missing_ok=True)
            stderr = (exc.stderr or "").strip()
            raise BuildError(f"tippecanoe failed: {stderr or exc}") from exc
        except subprocess.TimeoutExpired as exc:
            mbtiles_staging.unlink(missing_ok=True)
            raise BuildError(
                f"tippecanoe timed out after {timeout_seconds}s"
            ) from exc
        except BaseException:
            mbtiles_staging.unlink(missing_ok=True)
            raise

        # Convert MBTiles → PMTiles.
        pmtiles_bin = os.environ.get("PMTILES_BIN", "pmtiles")
        try:
            subprocess.run(
                [pmtiles_bin, "convert", str(mbtiles_staging), str(staging)],
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=True,
            )
        except FileNotFoundError:
            mbtiles_staging.unlink(missing_ok=True)
            raise BuildError(
                f"'{pmtiles_bin}' not found. Install with: brew install pmtiles"
            )
        except subprocess.CalledProcessError as exc:
            mbtiles_staging.unlink(missing_ok=True)
            staging.unlink(missing_ok=True)
            stderr = (exc.stderr or "").strip()
            raise BuildError(f"pmtiles convert failed: {stderr or exc}") from exc
        finally:
            mbtiles_staging.unlink(missing_ok=True)

        elapsed = time.monotonic() - start

        size, sha256_hex = _sha256_file(staging)

        storage = LocalStorage()
        storage.finalize_from_local(str(staging), str(out_abs))

        generated_at = utcnow_iso()
        entry = {
            "relative_path": rel,
            "region": region,
            "source_kind": source,    # CLI identifier: "geojson" or a category name
            "size_bytes": size,
            "sha256": sha256_hex,
            "options": {
                "min_zoom": min_zoom,
                "max_zoom": max_zoom,
                "layer_name": effective_layer_name,
            },
            "generated_at": generated_at,
            "duration_seconds": round(elapsed, 2),
            "source": {               # lineage of the input GeoJSONSeq
                "cache_type": _source_cache_type(source),
                "relative_path": _source_rel_path(source, region),
                "sha256": src_sha,
                "size_bytes": src_entry.get("size_bytes"),
                "generated_at": src_entry.get("generated_at", ""),
            },
            "tool": {
                "command": "tippecanoe",
                "tippecanoe_version": _tippecanoe_version(tippecanoe_bin),
            },
            "extra": {},
        }
        with _manifest_write_lock, manifest_transaction(OUTPUT_CACHE_TYPE) as manifest:
            manifest.setdefault("entries", {})[rel] = entry

        return BuildResult(
            region=region,
            source=source,
            path=str(out_abs),
            relative_path=rel,
            size_bytes=size,
            sha256=sha256_hex,
            min_zoom=min_zoom,
            max_zoom=max_zoom,
            layer_name=effective_layer_name,
            tippecanoe_version=entry["tool"]["tippecanoe_version"],
            generated_at=generated_at,
            duration_seconds=elapsed,
            was_cached=False,
            source_path=str(src_path),
            source_sha256=src_sha,
            manifest_entry=entry,
        )

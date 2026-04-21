"""Shared PBF → ESRI Shapefile conversion library.

Single source of truth for converting cached PBFs to multi-layer
shapefile bundles via ``ogr2ogr``. Used by both the
``convert-pbf-shapefile`` CLI tool and the FFL
``osm.ops.ConvertPbfToShapefile`` handler, so they share the same
on-disk layout, the same manifest, and the same skip logic.

Output for each region is a **directory** of shapefile bundles (one
sibling ``.shp``/``.shx``/``.dbf``/``.prj``/``.cpg`` set per layer),
because shapefile requires one geometry type per file.

The ``other_relations`` layer (GeometryCollection) is never produced —
shapefile cannot represent it. Use ``pbf_geojson`` if you need those
relations.
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
OUTPUT_CACHE_TYPE = "shapefiles"
CHUNK_SIZE = 1024 * 1024

OSM_LAYER_NAMES: tuple[str, ...] = (
    "points",
    "lines",
    "multilinestrings",
    "multipolygons",
)

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


@dataclass
class ConvertResult:
    """Outcome of a ``convert_region`` call."""

    region: str
    path: str                    # absolute path to the shapefile bundle directory
    relative_path: str           # relative path within the shapefiles/ cache
    requested_layers: tuple[str, ...]
    layers: list[dict[str, Any]]
    total_size_bytes: int        # every file in the bundle dir
    shp_size_bytes: int          # just the .shp files
    generated_at: str
    duration_seconds: float
    was_cached: bool
    source_url: str
    source_pbf_path: str
    manifest_entry: dict[str, Any] = field(default_factory=dict)


class ConversionError(RuntimeError):
    """Raised when a conversion fails (ogr2ogr failure, missing PBF, etc.)."""


def pbf_rel_path(region: str) -> str:
    return f"{region}-latest.osm.pbf"


def pbf_abs_path(region: str) -> Path:
    return Path(cache_dir(SOURCE_CACHE_TYPE)) / pbf_rel_path(region)


def shapefile_rel_path(region: str) -> str:
    return f"{region}-latest"


def shapefile_abs_path(region: str) -> Path:
    return Path(cache_dir(OUTPUT_CACHE_TYPE)) / shapefile_rel_path(region)


def _staging_dir(region: str) -> Path:
    base = os.environ.get("AFL_OSM_LOCAL_TMP_DIR") or tempfile.gettempdir()
    safe = region.replace("/", "_")
    return Path(base) / "facetwork-shapefile-staging" / safe


def _ogr2ogr_version(ogr2ogr_bin: str) -> str:
    try:
        result = subprocess.run(
            [ogr2ogr_bin, "--version"],
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


def _layer_metadata(out_dir: Path) -> list[dict]:
    layers: list[dict] = []
    if not out_dir.exists():
        return layers
    for shp in sorted(out_dir.glob("*.shp")):
        size, sha256_hex = _sha256_file(shp)
        layers.append({"name": shp.stem, "size_bytes": size, "sha256": sha256_hex})
    return layers


def normalize_layers(layers: tuple[str, ...] | list[str] | str | None) -> tuple[str, ...]:
    """Canonicalize a user-supplied layer selection.

    Accepts: None (defaults to all four), a comma-separated string, a
    list, or a tuple. Order is normalized to ``OSM_LAYER_NAMES`` order
    so on-disk layer order is deterministic regardless of input form.
    Unknown layer names raise ``ConversionError``.
    """
    if layers is None:
        return OSM_LAYER_NAMES
    if isinstance(layers, str):
        raw = [n.strip() for n in layers.split(",") if n.strip()]
    else:
        raw = list(layers)
    if not raw:
        return OSM_LAYER_NAMES
    invalid = [n for n in raw if n not in OSM_LAYER_NAMES]
    if invalid:
        raise ConversionError(
            f"unknown layer(s): {', '.join(invalid)}. "
            f"Valid: {', '.join(OSM_LAYER_NAMES)}"
        )
    seen = set(raw)
    return tuple(n for n in OSM_LAYER_NAMES if n in seen)


def is_up_to_date(
    region: str,
    pbf_entry: dict,
    out_abs: Path,
    requested_layers: tuple[str, ...],
) -> bool:
    """True if the cached bundle still satisfies the request.

    Cache hit semantics:
    - pbf sha256 matches, AND
    - manifest's ``requested_layers`` is a superset of the request, AND
    - every requested layer's ``.shp`` is present at the recorded size.
    """
    geo_manifest = read_manifest(OUTPUT_CACHE_TYPE)
    out_rel = shapefile_rel_path(region)
    existing = geo_manifest.get("entries", {}).get(out_rel)
    if not existing:
        return False
    if existing.get("source", {}).get("sha256") != pbf_entry.get("sha256"):
        return False
    if not out_abs.exists():
        return False
    existing_layers = set(existing.get("requested_layers", []))
    if not existing_layers.issuperset(requested_layers):
        return False
    size_by_name = {layer["name"]: layer["size_bytes"] for layer in existing.get("layers", [])}
    for name in requested_layers:
        layer_file = out_abs / f"{name}.shp"
        if not layer_file.exists():
            return False
        if name in size_by_name and layer_file.stat().st_size != size_by_name[name]:
            return False
    return True


def convert_region(
    region: str,
    *,
    layers: tuple[str, ...] | list[str] | str | None = None,
    force: bool = False,
    ogr2ogr_bin: str = "ogr2ogr",
) -> ConvertResult:
    """Convert a region's PBF to a multi-layer shapefile bundle."""
    layers_tuple = normalize_layers(layers)

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

    with _region_lock(region):
        out_abs = shapefile_abs_path(region)
        out_rel = shapefile_rel_path(region)

        if not force and is_up_to_date(region, pbf_entry, out_abs, layers_tuple):
            existing = read_manifest(OUTPUT_CACHE_TYPE).get("entries", {}).get(out_rel, {})
            return ConvertResult(
                region=region,
                path=str(out_abs),
                relative_path=out_rel + "/",
                requested_layers=layers_tuple,
                layers=existing.get("layers", []),
                total_size_bytes=existing.get("total_size_bytes", 0),
                shp_size_bytes=existing.get("shp_size_bytes", 0),
                generated_at=existing.get("generated_at", ""),
                duration_seconds=0.0,
                was_cached=True,
                source_url=source_url,
                source_pbf_path=str(src_pbf),
                manifest_entry=existing,
            )

        staging = _staging_dir(region)
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True, exist_ok=True)

        cmd = [
            ogr2ogr_bin,
            "-f",
            "ESRI Shapefile",
            "-lco",
            "ENCODING=UTF-8",
            "-skipfailures",
            str(staging),
            str(src_pbf),
            *layers_tuple,
        ]
        start = time.monotonic()
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as exc:
            shutil.rmtree(staging, ignore_errors=True)
            stderr = (exc.stderr or "").strip()
            raise ConversionError(f"ogr2ogr failed: {stderr or exc}") from exc
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        elapsed = time.monotonic() - start

        storage = LocalStorage()
        storage.finalize_dir_from_local(str(staging), str(out_abs))

        layer_metadata = _layer_metadata(out_abs)
        total_size_shp = sum(layer["size_bytes"] for layer in layer_metadata)
        total_size_all = sum(f.stat().st_size for f in out_abs.rglob("*") if f.is_file())
        generated_at = utcnow_iso()

        entry = {
            "relative_path": out_rel + "/",
            "format": "shapefile",
            "requested_layers": list(layers_tuple),
            "total_size_bytes": total_size_all,
            "shp_size_bytes": total_size_shp,
            "layers": layer_metadata,
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
                "command": "ogr2ogr -f 'ESRI Shapefile'",
                "ogr2ogr_version": _ogr2ogr_version(ogr2ogr_bin),
            },
            "extra": {"region": region},
        }
        with _manifest_write_lock, manifest_transaction(OUTPUT_CACHE_TYPE) as manifest:
            manifest.setdefault("entries", {})[out_rel] = entry

        return ConvertResult(
            region=region,
            path=str(out_abs),
            relative_path=out_rel + "/",
            requested_layers=layers_tuple,
            layers=layer_metadata,
            total_size_bytes=total_size_all,
            shp_size_bytes=total_size_shp,
            generated_at=generated_at,
            duration_seconds=elapsed,
            was_cached=False,
            source_url=source_url,
            source_pbf_path=str(src_pbf),
            manifest_entry=entry,
        )


def to_osm_cache(result: ConvertResult) -> dict[str, Any]:
    """Map a ``ConvertResult`` to the ``OSMCache`` dict FFL handlers return.

    ``path`` is the bundle **directory**, not a single file — downstream
    handlers that expect a file path need to join a specific layer's
    filename (e.g. ``Path(cache['path']) / 'points.shp'``).
    """
    return {
        "url": result.source_url,
        "path": result.path,
        "date": result.generated_at,
        "size": result.total_size_bytes,
        "wasInCache": result.was_cached,
        "source": "cache" if result.was_cached else "convert",
    }

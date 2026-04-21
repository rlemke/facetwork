"""Category-based feature extraction from cached OSM PBFs.

Produces one pre-filtered GeoJSON cache per *category* (water, parks,
forests, etc.) so downstream consumers read a small, already-filtered
file instead of re-parsing the full PBF every time.

Each category has its own cache subdirectory under ``$AFL_OSM_CACHE_ROOT``,
its own ``manifest.json``, and the same Geofabrik path mirroring the
``pbf/`` and ``geojson/`` caches use. See the ``CATEGORIES`` registry
below for what's defined; new categories are a single dict entry.

Extraction runs in two osmium passes, piped through a local staging dir:

1. ``osmium tags-filter`` — produces a filtered ``.osm.pbf`` with only
   entities matching the category's tag expression, plus the nodes/ways
   those entities reference so geometries remain assemblable.
2. ``osmium export -f geojsonseq`` — converts the filtered PBF to
   streaming GeoJSON. Multipolygon relations assemble into proper
   ``MultiPolygon`` geometries.

After MD5/SHA of the output, the staged file is moved to its final
location via the storage abstraction (``storage.finalize_from_local``).

Cache validity requires:

- The source PBF's SHA-256 still matches what the category's manifest
  entry recorded, AND
- The category definition's ``filter_version`` still matches. Bumping
  ``filter_version`` on a registry change is how we invalidate all
  cache entries for that category without forcing every extract.
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
DEFAULT_FORMAT = "geojsonseq"
CHUNK_SIZE = 1024 * 1024


@dataclass(frozen=True)
class CategoryDef:
    """Definition of an extractable feature category.

    - ``name``: cache subdirectory name, CLI identifier, manifest key.
    - ``facet_name``: FFL event facet name exposed in ``osm.ops`` (e.g.
      ``ExtractWater``).
    - ``return_param``: FFL return parameter name (e.g. ``water``).
    - ``description``: one-line summary surfaced in ``--help`` / FFL docs.
    - ``filter_expression``: osmium ``tags-filter`` argument list, as a
      single string (space-separated specs). Each spec is
      ``<type>/<tag>`` where ``<type>`` is ``n``, ``w``, ``r``, or a
      combination (``nwr``), and ``<tag>`` is either ``KEY`` (any value)
      or ``KEY=VAL1,VAL2``.
    - ``filter_version``: bump when the expression changes to invalidate
      existing cache entries without forcing unchanged categories.
    """

    name: str
    facet_name: str
    return_param: str
    description: str
    filter_expression: str
    filter_version: int = 1


CATEGORIES: dict[str, CategoryDef] = {
    "water": CategoryDef(
        name="water",
        facet_name="ExtractWater",
        return_param="water",
        description="Lakes, ponds, reservoirs, rivers, streams, canals.",
        filter_expression=(
            "nwr/natural=water "
            "nwr/waterway=river,stream,canal,drain,ditch "
            "nwr/water"
        ),
        filter_version=1,
    ),
    "protected_areas": CategoryDef(
        name="protected_areas",
        facet_name="ExtractProtectedAreas",
        return_param="protectedAreas",
        description=(
            "National parks, state parks, wilderness areas, nature reserves."
        ),
        filter_expression=(
            "r/boundary=national_park,protected_area "
            "nwr/leisure=nature_reserve"
        ),
        filter_version=1,
    ),
    "parks": CategoryDef(
        name="parks",
        facet_name="ExtractParks",
        return_param="parks",
        description=(
            "City-level parks, playgrounds, sports pitches, stadiums, "
            "gardens — recreation rather than protected wildlands."
        ),
        filter_expression=(
            "nwr/leisure=park,playground,pitch,sports_centre,stadium,garden"
        ),
        filter_version=1,
    ),
    "forests": CategoryDef(
        name="forests",
        facet_name="ExtractForests",
        return_param="forests",
        description="Forests and wood-covered land.",
        filter_expression="nwr/natural=wood nwr/landuse=forest",
        filter_version=1,
    ),
    "roads_routable": CategoryDef(
        name="roads_routable",
        facet_name="ExtractRoadsRoutable",
        return_param="roadsRoutable",
        description=(
            "Full road network for routing — every highway=* way plus "
            "tagged junction/crossing nodes. Preserves all routing "
            "attributes (oneway, maxspeed, access, surface, etc.)."
        ),
        filter_expression="nwr/highway",
        filter_version=1,
    ),
    "turn_restrictions": CategoryDef(
        name="turn_restrictions",
        facet_name="ExtractTurnRestrictions",
        return_param="turnRestrictions",
        description=(
            "OSM turn-restriction relations (type=restriction). Only "
            "meaningful paired with the road network — routing engines "
            "consume both."
        ),
        filter_expression="r/type=restriction",
        filter_version=1,
    ),
    "railways_routable": CategoryDef(
        name="railways_routable",
        facet_name="ExtractRailwaysRoutable",
        return_param="railwaysRoutable",
        description=(
            "Active rail network for multimodal routing: heavy rail, "
            "light rail, subway, tram, narrow gauge, funicular, monorail. "
            "Abandoned/disused rail is excluded."
        ),
        filter_expression=(
            "nwr/railway=rail,light_rail,subway,tram,"
            "narrow_gauge,funicular,monorail"
        ),
        filter_version=1,
    ),
    "cycle_routes": CategoryDef(
        name="cycle_routes",
        facet_name="ExtractCycleRoutes",
        return_param="cycleRoutes",
        description="Cycling route relations — on-road bike routes and MTB trails.",
        filter_expression="r/route=bicycle,mtb",
        filter_version=1,
    ),
    "hiking_routes": CategoryDef(
        name="hiking_routes",
        facet_name="ExtractHikingRoutes",
        return_param="hikingRoutes",
        description="Hiking/walking route relations — long-distance trails and foot paths.",
        filter_expression="r/route=hiking,foot",
        filter_version=1,
    ),
}


# Per-(region, category) locks so concurrent extract calls for the same
# region+category serialize. Different (region, category) pairs run in
# parallel unhindered.
_extract_locks: dict[tuple[str, str], threading.Lock] = {}
_extract_locks_guard = threading.Lock()

# Per-category manifest write locks, so concurrent extracts that target
# the same manifest serialize across threads. (fcntl.flock handles
# cross-process; this handles in-process.)
_manifest_locks: dict[str, threading.Lock] = {}
_manifest_locks_guard = threading.Lock()


def _extract_lock(region: str, category: str) -> threading.Lock:
    key = (region, category)
    with _extract_locks_guard:
        lock = _extract_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _extract_locks[key] = lock
        return lock


def _manifest_lock(category: str) -> threading.Lock:
    with _manifest_locks_guard:
        lock = _manifest_locks.get(category)
        if lock is None:
            lock = threading.Lock()
            _manifest_locks[category] = lock
        return lock


@dataclass
class ExtractResult:
    """Outcome of an ``extract_region`` call."""

    region: str
    category: str
    path: str                   # absolute path to the extracted GeoJSON
    relative_path: str          # relative to the category cache dir
    size_bytes: int
    sha256: str
    feature_count: int
    filter_version: int
    generated_at: str
    duration_seconds: float
    was_cached: bool
    source_url: str
    source_pbf_path: str
    manifest_entry: dict[str, Any] = field(default_factory=dict)


class ExtractionError(RuntimeError):
    """Raised when extraction fails (osmium failure, missing PBF, unknown category, etc.)."""


def pbf_rel_path(region: str) -> str:
    return f"{region}-latest.osm.pbf"


def pbf_abs_path(region: str) -> Path:
    return Path(cache_dir(SOURCE_CACHE_TYPE)) / pbf_rel_path(region)


def extract_rel_path(region: str) -> str:
    return f"{region}-latest.{DEFAULT_FORMAT}"


def extract_abs_path(region: str, category: str) -> Path:
    return Path(cache_dir(category)) / extract_rel_path(region)


def _staging_dir(region: str, category: str) -> Path:
    """Stage adjacent to the final destination. Two intermediate files
    land here during extraction (filtered.osm.pbf + the exported
    geojsonseq); both get cleaned up after the geojsonseq is finalized
    into the destination directory. Override with
    ``AFL_OSM_CONVERT_STAGING=tmp`` to fall back to local tmp.
    """
    if (os.environ.get("AFL_OSM_CONVERT_STAGING") or "").lower() == "tmp":
        base = os.environ.get("AFL_OSM_LOCAL_TMP_DIR") or tempfile.gettempdir()
        safe = region.replace("/", "_")
        return Path(base) / "facetwork-extract-staging" / category / safe
    out = extract_abs_path(region, category)
    return out.with_name(out.name + ".staging")


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


def _count_features_geojsonseq(path: Path) -> int:
    """Count features in a GeoJSONSeq file — one per non-empty line."""
    count = 0
    with path.open("rb") as f:
        for line in f:
            # GeoJSONSeq may use the record-separator (0x1E) prefix.
            if line.strip(b"\x1e \t\r\n"):
                count += 1
    return count


def is_up_to_date(
    region: str,
    category: str,
    pbf_entry: dict,
    out_abs: Path,
) -> bool:
    """True if the cached extract still reflects both the source PBF SHA
    and the category's current filter_version."""
    cat = CATEGORIES[category]
    cache_manifest = read_manifest(category)
    out_rel = extract_rel_path(region)
    existing = cache_manifest.get("entries", {}).get(out_rel)
    if not existing:
        return False
    if existing.get("source", {}).get("sha256") != pbf_entry.get("sha256"):
        return False
    if existing.get("filter", {}).get("version") != cat.filter_version:
        return False
    if not out_abs.exists():
        return False
    return out_abs.stat().st_size == existing.get("size_bytes")


def extract_region(
    region: str,
    category: str,
    *,
    force: bool = False,
    osmium_bin: str = "osmium",
) -> ExtractResult:
    """Extract one category's features from a region's cached PBF."""
    if category not in CATEGORIES:
        raise ExtractionError(
            f"unknown category: {category!r}. "
            f"Valid: {', '.join(sorted(CATEGORIES))}"
        )
    cat = CATEGORIES[category]

    pbf_manifest = read_manifest(SOURCE_CACHE_TYPE)
    pbf_rel = pbf_rel_path(region)
    pbf_entry = pbf_manifest.get("entries", {}).get(pbf_rel)
    if not pbf_entry:
        raise ExtractionError(
            f"no pbf manifest entry for {region!r}; run download-pbf first"
        )
    src_pbf = pbf_abs_path(region)
    if not src_pbf.exists():
        raise ExtractionError(f"pbf file missing on disk: {src_pbf}")
    source_url = pbf_entry.get("source_url", "")

    with _extract_lock(region, category):
        out_abs = extract_abs_path(region, category)
        out_rel = extract_rel_path(region)

        if not force and is_up_to_date(region, category, pbf_entry, out_abs):
            existing = read_manifest(category).get("entries", {}).get(out_rel, {})
            return ExtractResult(
                region=region,
                category=category,
                path=str(out_abs),
                relative_path=out_rel,
                size_bytes=existing.get("size_bytes", out_abs.stat().st_size),
                sha256=existing.get("sha256", ""),
                feature_count=existing.get("feature_count", 0),
                filter_version=cat.filter_version,
                generated_at=existing.get("generated_at", ""),
                duration_seconds=0.0,
                was_cached=True,
                source_url=source_url,
                source_pbf_path=str(src_pbf),
                manifest_entry=existing,
            )

        out_abs.parent.mkdir(parents=True, exist_ok=True)
        staging = _staging_dir(region, category)
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True, exist_ok=True)

        filtered_pbf = staging / "filtered.osm.pbf"
        extract_out = staging / extract_rel_path(region).replace("/", "_")

        start = time.monotonic()
        try:
            # Step 1: tag filter into a compact PBF.
            filter_cmd = [
                osmium_bin,
                "tags-filter",
                "--overwrite",
                "-o",
                str(filtered_pbf),
                str(src_pbf),
                *cat.filter_expression.split(),
            ]
            subprocess.run(filter_cmd, check=True, capture_output=True, text=True)

            # Step 2: export filtered PBF to GeoJSONSeq.
            export_cmd = [
                osmium_bin,
                "export",
                "-f",
                DEFAULT_FORMAT,
                "-o",
                str(extract_out),
                "--overwrite",
                str(filtered_pbf),
            ]
            subprocess.run(export_cmd, check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as exc:
            shutil.rmtree(staging, ignore_errors=True)
            stderr = (exc.stderr or "").strip()
            raise ExtractionError(f"osmium step failed: {stderr or exc}") from exc
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        elapsed = time.monotonic() - start

        size, sha256_hex = _sha256_file(extract_out)
        feature_count = _count_features_geojsonseq(extract_out)

        # Finalize only the GeoJSONSeq; clean up the intermediate filtered PBF.
        storage = LocalStorage()
        storage.finalize_from_local(str(extract_out), str(out_abs))
        # The finalize removes the staging file, but the filtered.osm.pbf and
        # the staging dir itself remain. Clean them up.
        shutil.rmtree(staging, ignore_errors=True)

        generated_at = utcnow_iso()
        entry = {
            "relative_path": out_rel,
            "category": category,
            "format": DEFAULT_FORMAT,
            "size_bytes": size,
            "sha256": sha256_hex,
            "feature_count": feature_count,
            "generated_at": generated_at,
            "duration_seconds": round(elapsed, 2),
            "filter": {
                "kind": "osmium-tags-filter",
                "expression": cat.filter_expression,
                "version": cat.filter_version,
            },
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
                "command": "osmium tags-filter | osmium export",
                "osmium_version": _osmium_version(osmium_bin),
            },
            "extra": {"region": region},
        }
        with _manifest_lock(category), manifest_transaction(category) as manifest:
            manifest.setdefault("entries", {})[out_rel] = entry

        return ExtractResult(
            region=region,
            category=category,
            path=str(out_abs),
            relative_path=out_rel,
            size_bytes=size,
            sha256=sha256_hex,
            feature_count=feature_count,
            filter_version=cat.filter_version,
            generated_at=generated_at,
            duration_seconds=elapsed,
            was_cached=False,
            source_url=source_url,
            source_pbf_path=str(src_pbf),
            manifest_entry=entry,
        )


def to_osm_cache(result: ExtractResult) -> dict[str, Any]:
    """Map an ``ExtractResult`` to the ``OSMCache`` dict FFL handlers return."""
    return {
        "url": result.source_url,
        "path": result.path,
        "date": result.generated_at,
        "size": result.size_bytes,
        "wasInCache": result.was_cached,
        "source": "cache" if result.was_cached else "extract",
    }

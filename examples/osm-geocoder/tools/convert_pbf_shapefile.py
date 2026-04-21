"""Convert cached OSM PBF files to multi-layer ESRI Shapefile bundles.

A "shapefile" is a set of sibling files (``.shp``, ``.shx``, ``.dbf``,
``.prj``, ``.cpg``) with a shared base name. GDAL's OSM driver emits one
such bundle per geometry category — typically ``points``, ``lines``,
``multilinestrings``, ``multipolygons``, and ``other_relations`` — so
each region's output is a **directory** of bundles rather than a single
file. The output layout mirrors the pbf cache::

    <cache_root>/shapefiles/<region>-latest/
        points.shp  .shx  .dbf  .prj  .cpg
        lines.shp   ...
        multipolygons.shp ...
        ...

The shapefile manifest records one entry per region: the source PBF's
SHA-256 and a per-layer size + SHA-256. Reruns skip regions whose
source PBF hasn't changed; pass ``--force`` to reconvert.

Why this tool exists alongside ``convert-pbf-geojson``:

- Shapefile is still the lingua franca for many desktop GIS and legacy
  toolchains.
- Geofabrik publishes free shapefiles only for sub-country regions with
  a fixed attribute set; converting from PBF gives full coverage and
  full fidelity. See ``docs/architecture/llm-ffl-fluency.md`` (no,
  wrong doc) — see the tools README for context.

Shapefile format limitations callers should know about:

- Field names are truncated to 10 characters; ogr2ogr does this
  automatically and its warnings pass through on stderr.
- Each ``.shp`` / ``.shx`` file is capped at 2 GiB. Continent-sized
  regions may hit this on the ``lines`` or ``multipolygons`` layer;
  prefer GeoJSON or sub-region splits when that happens.
- Attribute set is whatever GDAL's default ``osmconf.ini`` surfaces.
  A future ``--osmconf`` flag could point at a custom config.

Local-backend only — shapefile conversion writes a directory tree, and
the HDFS backend does not currently support directory uploads. See
``_lib/storage.py``.

Usage::

    python convert_pbf_shapefile.py europe/liechtenstein
    python convert_pbf_shapefile.py --all-under europe/germany --jobs 4
    python convert_pbf_shapefile.py --update-all

Requires the ``ogr2ogr`` command-line tool (GDAL) on ``PATH``.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _lib.manifest import (  # noqa: E402
    cache_dir,
    manifest_transaction,
    read_manifest,
    utcnow_iso,
)
from _lib.pbf_download import (  # noqa: E402
    filter_leaves,
    regions_from_pbf_manifest,
)
from _lib.storage import LocalStorage  # noqa: E402

SOURCE_CACHE_TYPE = "pbf"
OUTPUT_CACHE_TYPE = "shapefiles"
DEFAULT_JOBS = 2
CHUNK_SIZE = 1024 * 1024

# GDAL's OSM driver produces a fixed set of layers whose names follow a
# geometry-oriented convention. We surface these names in ``--help`` so
# users know what to expect inside each region's output directory.
OSM_LAYER_NAMES = ("points", "lines", "multilinestrings", "multipolygons")

OSM_LAYERS: tuple[tuple[str, str, str], ...] = (
    (
        "points",
        "Point",
        "Tagged nodes — POIs, amenities, places, shops, addresses, etc. "
        "Anything with tags that isn't a way or relation member.",
    ),
    (
        "lines",
        "LineString",
        "Non-area linear ways — highways, railways, waterways, barriers, "
        "power lines, and any tagged open way.",
    ),
    (
        "multilinestrings",
        "MultiLineString",
        "OSM relations that form linear structures, typically "
        "route relations (bus, bicycle, hiking, ski).",
    ),
    (
        "multipolygons",
        "MultiPolygon",
        "Closed ways and OSM relations of type=multipolygon / "
        "type=boundary — buildings, land use, natural areas, "
        "administrative boundaries.",
    ),
)

# Naming conventions users may encounter elsewhere in the OSM ecosystem,
# kept in the help text as a pointer (we don't rename our layers to these):
#
#   - osm2pgsql (PostGIS):
#       planet_osm_point / planet_osm_line / planet_osm_polygon /
#       planet_osm_roads
#   - Geofabrik free shapefiles (pre-packaged downloads, not this tool's
#     output): gis_osm_*_free_1 tables split by *feature category*
#     (gis_osm_roads_free_1, gis_osm_buildings_a_free_1, ...)
#
# This tool follows the GDAL OSM driver convention, which is geometry-
# oriented, because that's what ogr2ogr naturally produces from a PBF.


def _help_epilog() -> str:
    layer_lines = []
    for name, geom, desc in OSM_LAYERS:
        layer_lines.append(f"  {name:<17} ({geom}) — {desc}")
    return (
        "layers produced per region (GDAL OSM driver convention):\n"
        + "\n".join(layer_lines)
        + "\n\n"
        "  other_relations     (skipped) — mixed-geometry OSM relations; "
        "shapefile's\n"
        "                       format cannot represent GeometryCollection, "
        "so ogr2ogr\n"
        "                       would fail on this layer. Use --format "
        "geopackage (future)\n"
        "                       or convert-pbf-geojson if you need these.\n"
        "\n"
        "related layer-naming conventions in the OSM ecosystem (for reference only):\n"
        "  osm2pgsql (PostGIS)  planet_osm_point / _line / _polygon / _roads\n"
        "  Geofabrik downloads  gis_osm_<category>_free_1 (roads, buildings, "
        "waterways, ...)\n"
        "\n"
        "output format:\n"
        "  ESRI Shapefile (.shp/.shx/.dbf/.prj/.cpg per layer). Format limits:\n"
        "  - field names truncated to 10 chars (ogr2ogr warns)\n"
        "  - each .shp capped at 2 GiB — very large regions may hit this\n"
        "  - one geometry type per .shp — hence the multi-layer output\n"
        "  For regions that don't fit these limits, prefer convert-pbf-geojson\n"
        "  (GeoJSONSeq has no size cap) or a future convert-pbf-geopackage tool."
    )

# In-process lock for manifest updates. flock alone doesn't reliably
# serialize concurrent threads inside the same process on all platforms.
_MANIFEST_LOCK = threading.Lock()


def pbf_rel_path(region: str) -> str:
    return f"{region}-latest.osm.pbf"


def pbf_abs_path(region: str) -> Path:
    return Path(cache_dir(SOURCE_CACHE_TYPE)) / pbf_rel_path(region)


def shapefile_rel_path(region: str) -> str:
    """Manifest key — directory path (no trailing slash) for a region's bundle."""
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


def _read_regions_file(path: Path) -> list[str]:
    regions: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        regions.append(line)
    return regions


def _layer_metadata(out_dir: Path) -> list[dict]:
    """Record size + SHA-256 for every ``.shp`` in ``out_dir``.

    Only the ``.shp`` file is hashed — it carries the geometry payload
    and is what downstream consumers typically care about. The
    ``.dbf`` / ``.shx`` / ``.prj`` / ``.cpg`` siblings are tracked via
    the directory listing but not individually hashed, to keep the
    manifest small for regions with many layers.
    """
    layers: list[dict] = []
    if not out_dir.exists():
        return layers
    for shp in sorted(out_dir.glob("*.shp")):
        size, sha256_hex = _sha256_file(shp)
        layers.append(
            {
                "name": shp.stem,
                "size_bytes": size,
                "sha256": sha256_hex,
            }
        )
    return layers


def is_up_to_date(
    region: str,
    pbf_entry: dict,
    out_abs: Path,
    requested_layers: tuple[str, ...],
) -> bool:
    """Check whether an existing shapefile bundle satisfies the request.

    Cache is considered up-to-date when:
    - manifest has an entry for this region
    - the source PBF's SHA-256 still matches
    - the previously converted layer set is a **superset** of the request
      (so a "points,lines,multipolygons,multilinestrings" cache hit serves
      a "points" request, but not the other way around)
    - every requested layer's ``.shp`` exists at the recorded size
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
    # Every requested layer's .shp must still exist with the recorded size.
    size_by_name = {layer["name"]: layer["size_bytes"] for layer in existing.get("layers", [])}
    for name in requested_layers:
        layer_file = out_abs / f"{name}.shp"
        if not layer_file.exists():
            return False
        if name in size_by_name and layer_file.stat().st_size != size_by_name[name]:
            return False
    return True


def _up_to_date_cheap(region: str, requested_layers: tuple[str, ...]) -> bool:
    """Cheap local-only staleness check (used by ``--update-all``)."""
    pbf_manifest = read_manifest(SOURCE_CACHE_TYPE)
    pbf_entry = pbf_manifest.get("entries", {}).get(pbf_rel_path(region))
    if not pbf_entry:
        return False
    return is_up_to_date(region, pbf_entry, shapefile_abs_path(region), requested_layers)


def convert_region(
    region: str,
    *,
    layers: tuple[str, ...],
    force: bool,
    dry_run: bool,
    ogr2ogr_bin: str,
) -> str:
    """Convert one region to a shapefile bundle. Returns outcome string."""
    pbf_manifest = read_manifest(SOURCE_CACHE_TYPE)
    pbf_rel = pbf_rel_path(region)
    pbf_entry = pbf_manifest.get("entries", {}).get(pbf_rel)
    if not pbf_entry:
        raise RuntimeError(
            f"no pbf manifest entry for {region!r}; run download-pbf first"
        )

    src_pbf = pbf_abs_path(region)
    if not src_pbf.exists():
        raise RuntimeError(f"pbf file missing on disk: {src_pbf}")

    out_abs = shapefile_abs_path(region)
    out_rel = shapefile_rel_path(region)

    if not force and is_up_to_date(region, pbf_entry, out_abs, layers):
        print(f"[{region}] up-to-date, skipping", file=sys.stderr)
        return "skipped"

    if dry_run:
        print(
            f"[{region}] would convert {src_pbf} -> {out_abs}/ "
            f"(layers: {','.join(layers)})",
            file=sys.stderr,
        )
        return "dry-run"

    # Stage into local /tmp first so the destination volume (possibly a
    # slow external/network disk) doesn't throttle ogr2ogr's write rate.
    staging = _staging_dir(region)
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True, exist_ok=True)

    print(
        f"[{region}] converting -> {out_rel}/ (layers: {','.join(layers)})",
        file=sys.stderr,
    )
    # GDAL's OSM driver exposes 5 layers: points, lines, multilinestrings,
    # multipolygons, other_relations. The last is a GeometryCollection
    # layer (mixed-geometry OSM relations) that shapefile cannot
    # represent, so it is never selectable. Passing layer names
    # positionally after the source tells ogr2ogr "copy only these".
    # -skipfailures lets ogr2ogr continue past any individual feature
    # whose geometry still can't be emitted (rare but not impossible).
    cmd = [
        ogr2ogr_bin,
        "-f",
        "ESRI Shapefile",
        "-lco",
        "ENCODING=UTF-8",
        "-skipfailures",
        str(staging),
        str(src_pbf),
        *layers,
    ]
    start = time.monotonic()
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        shutil.rmtree(staging, ignore_errors=True)
        stderr = (exc.stderr or "").strip()
        raise RuntimeError(f"ogr2ogr failed: {stderr or exc}") from exc
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    elapsed = time.monotonic() - start

    # Move the whole staged directory to its final home.
    storage = LocalStorage()
    storage.finalize_dir_from_local(str(staging), str(out_abs))

    # Walk the finalized output for per-layer and total sizes.
    layer_metadata = _layer_metadata(out_abs)
    total_size_shp = sum(layer["size_bytes"] for layer in layer_metadata)
    total_size_all = sum(
        f.stat().st_size for f in out_abs.rglob("*") if f.is_file()
    )

    entry = {
        "relative_path": out_rel + "/",
        "format": "shapefile",
        "requested_layers": list(layers),
        "total_size_bytes": total_size_all,
        "shp_size_bytes": total_size_shp,
        "layers": layer_metadata,
        "generated_at": utcnow_iso(),
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
    with _MANIFEST_LOCK, manifest_transaction(OUTPUT_CACHE_TYPE) as manifest:
        manifest.setdefault("entries", {})[out_rel] = entry

    print(
        f"[{region}] done ({total_size_all / (1024 * 1024):.1f} MiB total, "
        f"{len(layer_metadata)} layer(s): {','.join(l['name'] for l in layer_metadata)}, "
        f"{elapsed:.1f}s)",
        file=sys.stderr,
    )
    return "converted"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Convert cached OSM PBF files to ESRI Shapefile bundles via ogr2ogr.",
        epilog=_help_epilog(),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "regions",
        nargs="*",
        help="Region keys to convert, e.g. europe/germany/berlin. "
        "Must already be present in the pbf cache.",
    )
    parser.add_argument(
        "--regions-file",
        type=Path,
        help="Read regions from a file (one per line; '#' comments allowed).",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Convert every region currently in the pbf manifest.",
    )
    parser.add_argument(
        "--all-under",
        metavar="PREFIX",
        help="Convert every cached PBF whose region path starts with PREFIX.",
    )
    parser.add_argument(
        "--include-parents",
        action="store_true",
        help="When using --all / --all-under, include parent regions "
        "alongside their descendants. Default is leaves-only.",
    )
    parser.add_argument(
        "--update-all",
        action="store_true",
        help="Convert every region in the pbf manifest whose shapefile "
        "bundle is missing or out of date relative to its source PBF.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="Print the resolved region list to stdout and exit; no conversions.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Reconvert even when the existing shapefile matches the source PBF's SHA-256.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be converted without running ogr2ogr.",
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=DEFAULT_JOBS,
        help=f"Number of concurrent conversions (default: {DEFAULT_JOBS}). "
        "Each job spawns an ogr2ogr subprocess; each can use 1-2 GB RAM "
        "for a country-sized PBF.",
    )
    parser.add_argument(
        "--layers",
        default=",".join(OSM_LAYER_NAMES),
        metavar="LIST",
        help="Comma-separated list of layers to emit. "
        f"Valid names: {', '.join(OSM_LAYER_NAMES)}. "
        f"Default: all four ({','.join(OSM_LAYER_NAMES)}). "
        "Specifying a subset produces a smaller bundle and converts faster.",
    )
    parser.add_argument(
        "--ogr2ogr",
        default="ogr2ogr",
        help="Path to the ogr2ogr binary (default: 'ogr2ogr' on PATH).",
    )
    args = parser.parse_args()

    if args.jobs < 1:
        parser.error("--jobs must be >= 1")

    # Parse and validate --layers
    requested: list[str] = [
        name.strip() for name in args.layers.split(",") if name.strip()
    ]
    if not requested:
        parser.error("--layers must list at least one layer")
    invalid = [n for n in requested if n not in OSM_LAYER_NAMES]
    if invalid:
        parser.error(
            f"unknown layer(s): {', '.join(invalid)}. "
            f"Valid: {', '.join(OSM_LAYER_NAMES)}"
        )
    # Canonicalize to OSM_LAYER_NAMES order so on-disk layer order and the
    # ogr2ogr argument order are deterministic regardless of how the user
    # typed the list.
    seen_layers = set(requested)
    layers_tuple: tuple[str, ...] = tuple(
        n for n in OSM_LAYER_NAMES if n in seen_layers
    )

    ogr2ogr_bin = args.ogr2ogr
    if shutil.which(ogr2ogr_bin) is None and not Path(ogr2ogr_bin).is_file():
        print(
            f"error: ogr2ogr binary not found ({ogr2ogr_bin!r}). "
            "Install GDAL (e.g. 'brew install gdal' or 'apt install gdal-bin').",
            file=sys.stderr,
        )
        return 2

    regions: list[str] = list(args.regions)
    if args.regions_file:
        regions.extend(_read_regions_file(args.regions_file))

    if args.all or args.all_under is not None:
        from_manifest = regions_from_pbf_manifest(under=args.all_under)
        before = len(from_manifest)
        if not args.include_parents:
            from_manifest = filter_leaves(from_manifest)
        print(
            f"pbf manifest: {before} region(s) matched, "
            f"{len(from_manifest)} selected after "
            f"{'leaves-only' if not args.include_parents else 'include-parents'} filter",
            file=sys.stderr,
        )
        regions.extend(from_manifest)

    if args.update_all:
        from_manifest = regions_from_pbf_manifest()
        if not args.include_parents:
            from_manifest = filter_leaves(from_manifest)
        needs_work = [
            r for r in from_manifest if not _up_to_date_cheap(r, layers_tuple)
        ]
        print(
            f"update-all: {len(from_manifest)} cached pbf(s), "
            f"{len(needs_work)} need conversion "
            f"({len(from_manifest) - len(needs_work)} already current)",
            file=sys.stderr,
        )
        regions.extend(needs_work)

    seen: set[str] = set()
    deduped: list[str] = []
    for r in regions:
        if r and r not in seen:
            seen.add(r)
            deduped.append(r)
    regions = deduped

    if not regions:
        if args.update_all and not (args.all or args.all_under or args.regions):
            print(
                "update-all: nothing to do, all shapefile bundles are current",
                file=sys.stderr,
            )
            return 0
        parser.error(
            "no regions provided (pass as args, or use --regions-file, "
            "--all, --all-under, or --update-all)"
        )

    if args.list:
        for r in regions:
            print(r)
        print(f"{len(regions)} region(s)", file=sys.stderr)
        return 0

    results = {"converted": 0, "skipped": 0, "dry-run": 0, "failed": 0}
    failures: list[tuple[str, str]] = []

    def _run(region: str) -> tuple[str, str | None]:
        try:
            outcome = convert_region(
                region,
                layers=layers_tuple,
                force=args.force,
                dry_run=args.dry_run,
                ogr2ogr_bin=ogr2ogr_bin,
            )
            return outcome, None
        except Exception as exc:  # noqa: BLE001
            return "failed", str(exc)

    if args.jobs == 1 or len(regions) == 1:
        for region in regions:
            outcome, err = _run(region)
            if err:
                failures.append((region, err))
                print(f"[{region}] FAILED: {err}", file=sys.stderr)
            results[outcome] += 1
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
            futures = {pool.submit(_run, r): r for r in regions}
            for fut in concurrent.futures.as_completed(futures):
                region = futures[fut]
                outcome, err = fut.result()
                if err:
                    failures.append((region, err))
                    print(f"[{region}] FAILED: {err}", file=sys.stderr)
                results[outcome] += 1

    print(
        f"\nSummary: {results['converted']} converted, "
        f"{results['skipped']} skipped, "
        f"{results['dry-run']} dry-run, "
        f"{results['failed']} failed",
        file=sys.stderr,
    )
    if failures:
        for region, msg in failures:
            print(f"  - {region}: {msg}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

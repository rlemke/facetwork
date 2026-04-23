"""Stitch cached save-earth GeoJSON layers into a single MapLibre HTML map.

Reads from the per-source caches the downloaders populate and produces
one ``index.html`` at
``$AFL_CACHE_ROOT/save-earth/maps/<region>/index.html`` with per-source
layer toggles and click popups that surface each feature's upstream
``properties`` (name, description, status, etc.).

Usage::

    # Default: include every cached layer, region key 'global'
    python build_save_earth_map.py

    # Write somewhere other than the cache
    python build_save_earth_map.py --output-dir /tmp/save-earth

    # Custom region key (affects the output subdirectory)
    python build_save_earth_map.py --region us

    # Customize the map view
    python build_save_earth_map.py --center 40.0,-100.0 --zoom 3
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _lib import epa_cleanups, map_render, openlittermap, sidecar  # noqa: E402
from _lib.storage import LocalStorage  # noqa: E402


# Standard layer pipeline. Each entry becomes a toggleable map layer
# iff its cached GeoJSON exists. The color palette is chosen so the
# distinctions stay legible when many layers overlap.
#
# Default OpenLitterMap reference: the clusters-zoom4 feed (global
# overview). Users who download additional (mode, zoom, bbox)
# combinations can pass --include with custom LayerSpecs via the
# extension point below.
DEFAULT_LAYERS: list[map_render.LayerSpec] = [
    map_render.LayerSpec(
        name="openlittermap",
        title="Litter clusters (OpenLitterMap)",
        source_cache_type=openlittermap.CACHE_TYPE,
        source_relative_path=f"clusters-zoom{openlittermap.DEFAULT_ZOOM}.geojson",
        color="#d9534f",
        radius=6,
        description_fields=[
            "point_count",
            "point_count_abbreviated",
            "datetime",
            "verified",
            "picked_up",
            "username",
        ],
    ),
    map_render.LayerSpec(
        name="epa-superfund",
        title="EPA Superfund (NPL) sites",
        source_cache_type=epa_cleanups.CACHE_TYPE,
        source_relative_path="superfund.geojson",
        color="#5e3c99",
        radius=7,
        description_fields=[
            "primary_name",
            "location_address",
            "city_name",
            "state_code",
            "epa_region",
            "pgm_sys_id",
            "facility_url",
        ],
    ),
    map_render.LayerSpec(
        name="epa-brownfields",
        title="EPA Brownfield sites (ACRES)",
        source_cache_type=epa_cleanups.CACHE_TYPE,
        source_relative_path="brownfields.geojson",
        color="#c66a00",
        radius=6,
        description_fields=[
            "primary_name",
            "location_address",
            "city_name",
            "state_code",
            "epa_region",
            "pgm_sys_id",
            "facility_url",
        ],
    ),
]


def _parse_center(s: str) -> tuple[float, float]:
    try:
        parts = [float(p) for p in s.split(",")]
    except ValueError as exc:
        raise SystemExit(f"error: --center needs 2 comma-separated numbers: {exc}")
    if len(parts) != 2:
        raise SystemExit("error: --center needs exactly 2 values (lat,lon)")
    return tuple(parts)  # type: ignore[return-value]


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--region",
        default="global",
        help="Output sub-directory name under maps/ (default: global).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Write index.html + sidecar here instead of the cache.",
    )
    parser.add_argument(
        "--center",
        default="39.8283,-98.5795",
        help="Initial map center 'lat,lon' (default: USA centroid).",
    )
    parser.add_argument("--zoom", type=float, default=4.0)
    parser.add_argument(
        "--include",
        action="append",
        default=None,
        help=(
            "Only include these layer names (repeatable). Default: every "
            "cached layer from the standard pipeline."
        ),
    )
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
    )

    center = _parse_center(args.center)
    storage = LocalStorage()

    # Pick the layers with actual cached data present. Users with only
    # OpenLitterMap cached shouldn't be forced to download every EPA
    # dataset to render a map.
    present: list[map_render.LayerSpec] = []
    for layer in DEFAULT_LAYERS:
        if args.include and layer.name not in args.include:
            continue
        geojson_path = sidecar.cache_path(
            map_render.NAMESPACE,
            layer.source_cache_type,
            layer.source_relative_path,
            storage,
        )
        if os.path.exists(geojson_path):
            present.append(layer)
        else:
            logging.info(
                "skipping layer %s — no cache at %s", layer.name, geojson_path
            )

    if not present:
        print(
            "error: no cached layers found. Run download-openlittermap.sh and/or "
            "download-epa-cleanups.sh first.",
            file=sys.stderr,
        )
        return 1

    try:
        bundle = map_render.render_map(
            region_key=args.region,
            layers=present,
            center=center,
            zoom=args.zoom,
            output_dir=args.output_dir,
            storage=storage,
        )
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    counts_str = ", ".join(
        f"{name}={count:,}" for name, count in bundle.layer_counts.items()
    )
    print(
        f"[map] {bundle.html_path}\n"
        f"      layers: {counts_str}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

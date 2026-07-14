"""Atlas phase - fan out over cities, tile each, combine to ONE map:
ResolveCities, CityTiles, BuildAtlasMap.

Real mode (FW_EQUITY_SOURCE=real) tiles each US city from live TIGER/ACS/OSM
(+ optional Microsoft footprints); offline mode synthesises deterministic tiles
so the FFL fan-out + combine can be tested with no network."""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any

import numpy as np

from handlers.shared import equity_utils as U
from handlers.shared import sources_real as R

NAMESPACE = "osm.equity"

# US incorporated places (name, lon, lat, 2020 population). Real ACS/OSM are
# US-scoped, so "north america" resolves to these US cities.
_CITY_TABLE = [
    ("New York, NY", -74.02, 40.70, 8804190),
    ("Los Angeles, CA", -118.30, 34.03, 3898747),
    ("Chicago, IL", -87.68, 41.86, 2746388),
    ("Houston, TX", -95.39, 29.75, 2304580),
    ("Phoenix, AZ", -112.07, 33.46, 1608139),
    ("Philadelphia, PA", -75.16, 39.95, 1603797),
    ("San Antonio, TX", -98.50, 29.44, 1434625),
    ("San Diego, CA", -117.15, 32.72, 1386932),
    ("Dallas, TX", -96.80, 32.78, 1304379),
    ("San Jose, CA", -121.88, 37.32, 1013240),
    ("Austin, TX", -97.74, 30.27, 961855),
    ("Jacksonville, FL", -81.66, 30.33, 949611),
    ("Fort Worth, TX", -97.33, 32.75, 918915),
    ("Columbus, OH", -82.99, 39.97, 905748),
    ("Charlotte, NC", -80.84, 35.23, 874579),
    ("San Francisco, CA", -122.43, 37.77, 873965),
    ("Indianapolis, IN", -86.15, 39.77, 887642),
    ("Seattle, WA", -122.33, 47.61, 737015),
    ("Denver, CO", -104.99, 39.74, 715522),
    ("Washington, DC", -77.03, 38.90, 689545),
    ("Boston, MA", -71.06, 42.35, 675647),
    ("El Paso, TX", -106.44, 31.77, 678815),
    ("Nashville, TN", -86.78, 36.17, 689447),
    ("Oklahoma City, OK", -97.52, 35.47, 681054),
    ("Las Vegas, NV", -115.14, 36.17, 641903),
    ("Detroit, MI", -83.06, 42.35, 639111),
    ("Portland, OR", -122.66, 45.52, 652503),
    ("Memphis, TN", -90.03, 35.13, 633104),
    ("Louisville, KY", -85.76, 38.25, 633045),
    ("Baltimore, MD", -76.61, 39.29, 585708),
    ("Milwaukee, WI", -87.94, 43.04, 577222),
    ("Albuquerque, NM", -106.62, 35.10, 564559),
    ("Tucson, AZ", -110.94, 32.22, 542629),
    ("Fresno, CA", -119.78, 36.74, 542107),
    ("Sacramento, CA", -121.49, 38.58, 524943),
    ("Mesa, AZ", -111.83, 33.42, 504258),
    ("Kansas City, MO", -94.58, 39.10, 508090),
    ("Atlanta, GA", -84.39, 33.75, 498715),
]
_US_REGIONS = {"north america", "north-america", "united states", "usa", "us", "u.s."}


def _log(params, msg, level="success"):
    sl = params.get("_step_log")
    if sl is not None:
        sl.append({"message": msg, "level": level})


def _truthy(v: Any) -> bool:
    return v.strip().lower() in {"1", "true", "yes", "on"} if isinstance(v, str) else bool(v)


def _d(v):
    return json.loads(v) if isinstance(v, str) else v


# ---------------------------------------------------------------------------
# ResolveCities
# ---------------------------------------------------------------------------
def handle_resolve_cities(params: dict[str, Any]) -> dict[str, Any]:
    regions = [r.strip().lower() for r in _d(params.get("regions", ["north-america"]))]
    min_pop = int(params.get("min_population", 500000))
    half = float(params.get("half_deg", 0.12))
    want_all = any(r in _US_REGIONS for r in regions)
    cities = []
    for name, lon, lat, pop in _CITY_TABLE:
        if pop < min_pop:
            continue
        if want_all or any(r in name.lower() for r in regions):
            cities.append(
                {"name": name, "lon": lon, "lat": lat, "half_deg": half, "population": pop}
            )
    _log(params, f"Resolved {len(cities)} cities (>= {min_pop:,} pop) from {regions}")
    return {"cities": cities}


# ---------------------------------------------------------------------------
# CityTiles - the fan-out unit
# ---------------------------------------------------------------------------
def _bbox(city):
    h = float(city["half_deg"])
    return (city["lon"] - h, city["lat"] - h, city["lon"] + h, city["lat"] + h)


def _offline_city_tiles(city, tiles):
    """Deterministic synthetic tiles (income gradient drives OSM richness)."""
    recs, n = [], max(1, len(tiles) - 1)
    for i, tile in enumerate(tiles):
        seed = int(hashlib.sha256(f"{city['name']}:{tile['geoid']}".encode()).hexdigest()[:8], 16)
        rng = np.random.default_rng(seed)
        wealth = min(1.0, max(0.0, i / n + float(rng.normal(0, 0.1))))
        eq = {
            "geoid": tile["geoid"],
            "median_income": round(30000 + wealth * 140000, 1),
            "pct_rent_burdened": round(0.5 - wealth * 0.3, 4),
            "pct_bipoc": round(0.7 - wealth * 0.4, 4),
            "pct_no_hs_diploma": round(0.3 - wealth * 0.2, 4),
            "pct_limited_english": round(0.2 - wealth * 0.15, 4),
            "pct_zero_vehicle": round(0.3 - wealth * 0.2, 4),
            "pct_internet_subscription": round(0.5 + wealth * 0.45, 4),
        }
        intr = {
            "geoid": tile["geoid"],
            "building_density": round(20 + wealth * 300, 3),
            "road_km_per_km2": round(2 + wealth * 12, 4),
            "attr_completeness": round(
                min(1.0, max(0.0, 0.2 + wealth * 0.6 + float(rng.normal(0, 0.04)))), 4
            ),
            "poi_diversity": round(
                min(1.0, max(0.0, wealth * 0.85 + float(rng.normal(0, 0.04)))), 4
            ),
            "median_recency_days": round(400 * (1.4 - wealth), 1),
        }
        recs.append(
            {
                "geoid": f"{U._slug(city['name'])}:{tile['geoid']}",
                "geometry_wkt": tile["geometry_wkt"],
                "equity": eq,
                "intrinsic": intr,
                "extrinsic": {
                    "geoid": tile["geoid"],
                    "footprint_completeness": -1.0,
                    "positional_accuracy_m": -1.0,
                    "has_reference": False,
                },
            }
        )
    return recs


def handle_city_tiles(params: dict[str, Any]) -> dict[str, Any]:
    city = _d(params["city"])
    tile_sqmi = float(params.get("tile_sqmi", 2.0))
    acs_year = int(params.get("acs_year", 2022))
    osm_kinds = params.get("osm_kinds", "poi")
    with_fp = _truthy(params.get("with_footprints", False))
    slug = U._slug(city["name"])
    bbox = _bbox(city)
    tiles = R.build_tiles(bbox, sqmi=tile_sqmi)

    if U.equity_mode() != "real":
        recs = _offline_city_tiles(city, tiles)
        _log(params, f"{city['name']}: {len(recs)} synthetic tiles")
        return {"records": recs}

    # real: TIGER tracts + ACS -> areal-interpolate equity onto tiles
    key = os.environ.get("CENSUS_API_KEY", "")
    tract_records = []
    for t in R.real_resolve_tracts(bbox):
        try:
            eq = R.real_fetch_census_equity(t["geoid"], acs_year, key)
        except RuntimeError:
            continue
        tract_records.append({"geometry_wkt": t["geometry_wkt"], "equity": eq})
    tile_eq = R.areal_interpolate_equity(tiles, tract_records)

    # real OSM (kinds) once for the city, cached; footprints optional
    prev = os.environ.get("FW_EQUITY_OSM_KINDS")
    os.environ["FW_EQUITY_OSM_KINDS"] = osm_kinds
    try:
        osm_path = U.out_dir() / f"atlas_osm_{slug}.geojson"
        if not osm_path.exists():
            feats, _snap = R.real_fetch_region_osm(bbox)
            osm_path.write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
    finally:
        if prev is None:
            os.environ.pop("FW_EQUITY_OSM_KINDS", None)
        else:
            os.environ["FW_EQUITY_OSM_KINDS"] = prev

    fp_path, fp_ok = "", False
    if with_fp:
        fp_file = U.out_dir() / f"atlas_fp_{slug}.geojson"
        if not fp_file.exists():
            ff = R.real_fetch_footprints(bbox, str(U.out_dir()))
            if ff:
                fp_file.write_text(json.dumps({"type": "FeatureCollection", "features": ff}))
        if fp_file.exists():
            fp_path, fp_ok = str(fp_file), True

    recs = []
    for tile in tiles:
        eq = tile_eq.get(tile["geoid"])
        if not eq:
            continue
        clip = U.clip_tract_osm(str(osm_path), tile["geometry_wkt"])
        intr = U.compute_attribute_quality(clip, tile["area_km2"], tile["geoid"])
        extr = U.compute_extrinsic_quality(
            clip, tile["geometry_wkt"], fp_path, fp_ok, tile["geoid"]
        )
        recs.append(
            {
                "geoid": f"{slug}:{tile['geoid']}",
                "geometry_wkt": tile["geometry_wkt"],
                "equity": eq,
                "intrinsic": intr,
                "extrinsic": extr,
            }
        )
    U._clip_index.pop(str(osm_path), None)
    _log(params, f"{city['name']}: {len(recs)} tiles (real, footprints={fp_ok})")
    return {"records": recs}


# ---------------------------------------------------------------------------
# BuildAtlasMap - one combined map
# ---------------------------------------------------------------------------
_METRIC_LABEL = {
    "attr_completeness": "Attribute completeness",
    "footprint_completeness": "OSM building completeness vs Microsoft",
    "poi_diversity": "POI diversity",
    "building_density": "Building density (per km2)",
}


def handle_build_atlas_map(params: dict[str, Any]) -> dict[str, Any]:
    from shapely import wkt as W
    from shapely.geometry import mapping

    records = _d(params.get("records", []))
    stats_res = _d(params.get("stats", {}))
    metric = params.get("metric", "attr_completeness")
    title = params.get("title", "OSM Mapping Equity Atlas")
    deserts = U.data_deserts(records)
    feats = []
    for r in records:
        p = {
            "geoid": r["geoid"],
            "city": r["geoid"].split(":")[0],
            "median_income": r["equity"]["median_income"],
            "attr_completeness": r["intrinsic"]["attr_completeness"],
            "poi_diversity": r["intrinsic"]["poi_diversity"],
            "building_density": r["intrinsic"]["building_density"],
            "footprint_completeness": r["extrinsic"]["footprint_completeness"],
            "has_reference": r["extrinsic"]["has_reference"],
            "data_desert": r["geoid"] in deserts,
        }
        feats.append(
            {"type": "Feature", "properties": p, "geometry": mapping(W.loads(r["geometry_wkt"]))}
        )
    html = U.render_maplibre_map(
        feats,
        title,
        stats_res,
        deserts,
        synthetic=U.equity_mode() != "real",
        metric=metric,
        metric_label=_METRIC_LABEL.get(metric, metric),
    )
    map_path = U.out_dir() / "atlas_map.html"
    map_path.write_text(html)
    city_count = len({r["geoid"].split(":")[0] for r in records})
    _log(params, f"Atlas map: {len(records)} tiles across {city_count} cities")
    return {"map_html": str(map_path), "tile_count": len(records), "city_count": city_count}


_DISPATCH: dict[str, Any] = {
    f"{NAMESPACE}.ResolveCities": handle_resolve_cities,
    f"{NAMESPACE}.CityTiles": handle_city_tiles,
    f"{NAMESPACE}.BuildAtlasMap": handle_build_atlas_map,
}


def handle(payload: dict) -> dict:
    return _DISPATCH[payload["_facet_name"]](payload)


def register_handlers(runner) -> None:
    for facet_name in _DISPATCH:
        runner.register_handler(
            facet_name=facet_name,
            module_uri=f"file://{os.path.abspath(__file__)}",
            entrypoint="handle",
        )


def register_atlas_handlers(poller) -> None:
    for facet_name, fn in _DISPATCH.items():
        poller.register(facet_name, fn)

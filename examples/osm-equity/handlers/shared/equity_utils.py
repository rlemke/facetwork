"""Shared computation for the osm.equity mapping-equity study.

Everything here is real geometry (shapely) + real statistics (scipy +
pure-numpy Moran's I and locally-weighted regression). The *data source* is
selected by :func:`equity_mode` (env ``FW_EQUITY_SOURCE``):

  * ``real`` -> live sources in :mod:`sources_real`: Census TIGERweb tract
    polygons, the Census ACS 5-year API (needs ``CENSUS_API_KEY``), OSM from
    Overpass, and Microsoft Global ML Building Footprints as the extrinsic
    benchmark. Realistic at city/metro scale; where the region is out of
    footprint coverage, has_reference stays false (gated).
  * ``offline`` (default) -> a deterministic generator that tiles the region
    into a grid of synthetic tracts whose income gradient is spatially smooth
    AND drives OSM density/recency/diversity (the "digital divide" signal), so
    the whole workflow runs with no network and the analysis recovers a known
    relationship. Used by the test-suite; seeded from the geoid, so re-runs are
    byte-identical (no os.urandom / unseeded RNG).
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
from scipy import stats
from shapely import wkt as shapely_wkt
from shapely.geometry import Point, mapping, shape

# Amenity vocabulary used to synthesise + score POI diversity.
_AMENITY_TYPES = [
    "cafe",
    "restaurant",
    "pharmacy",
    "school",
    "clinic",
    "bank",
    "library",
    "supermarket",
    "bar",
    "fuel",
    "hospital",
    "post_office",
]
# Attribute-completeness tags scored per POI (Phase 3.1).
_KEY_TAGS = ["name", "addr:street", "opening_hours"]

# Rough study-region bounding boxes (lon_min, lat_min, lon_max, lat_max).
# TODO(real): resolve via a geocoder / TIGER PLACE lookup.
_REGION_BBOX = {
    "oakland, ca": (-122.34, 37.72, -122.15, 37.85),
    "detroit, mi": (-83.29, 42.26, -82.91, 42.45),
    "atlanta, ga": (-84.55, 33.65, -84.29, 33.89),
    "california": (-124.41, 32.53, -114.13, 42.01),
    "los angeles, ca": (-118.67, 33.70, -118.16, 34.34),
    "san francisco, ca": (-122.52, 37.70, -122.36, 37.83),
    "north america": (-168.0, 5.0, -52.0, 72.0),
    "united states": (-124.8, 24.4, -66.9, 49.4),
}
_DEFAULT_BBOX = (-122.34, 37.72, -122.15, 37.85)


# ---------------------------------------------------------------------------
# Paths / IO
# ---------------------------------------------------------------------------


def _slug(region: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in region.strip().lower())


def out_dir() -> Path:
    base = (
        os.environ.get("FW_CACHE_ROOT") or os.environ.get("FW_OUTPUT_BASE") or tempfile.gettempdir()
    )
    d = Path(base) / "osm_equity"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _rng(*parts: Any) -> np.random.Generator:
    """A deterministic numpy Generator seeded from the given parts."""
    h = hashlib.sha256("::".join(str(p) for p in parts).encode()).digest()
    return np.random.default_rng(int.from_bytes(h[:8], "big"))


def _write_geojson(path: Path, features: list[dict]) -> None:
    path.write_text(json.dumps({"type": "FeatureCollection", "features": features}))


def _read_geojson(path: str) -> list[dict]:
    return json.loads(Path(path).read_text()).get("features", [])


def grid_size() -> int:
    try:
        return max(3, int(os.environ.get("FW_EQUITY_GRID", "6")))
    except ValueError:
        return 6


def equity_mode() -> str:
    """'real' -> live TIGER/ACS/Overpass sources; 'offline' (default) -> the
    deterministic generator. Opt in with FW_EQUITY_SOURCE=real (needs network +
    CENSUS_API_KEY; realistic at city/metro scale)."""
    return os.environ.get("FW_EQUITY_SOURCE", "offline").strip().lower()


# ---------------------------------------------------------------------------
# Phase 1 / 2 - study area, tracts, region OSM, footprints
# ---------------------------------------------------------------------------


def region_bbox(region: str) -> tuple[float, float, float, float]:
    return _REGION_BBOX.get(region.strip().lower(), _DEFAULT_BBOX)


def tract_profile(geoid: str, region: str | None = None) -> dict[str, float]:
    """Deterministic per-tract latent profile.

    Income rises smoothly toward the NE corner of the grid (giving Moran's I
    a real positive signal) plus a small seeded jitter. Home-internet
    subscription tracks income; the two together define the "connectivity"
    that drives OSM richness.
    """
    n = grid_size()
    i, j = tract_ij(geoid)
    # smooth spatial gradient in [0, 1]
    grad = ((i + j) / (2.0 * (n - 1))) if n > 1 else 0.5
    jitter = float(_rng(geoid, "income").normal(0.0, 0.06))
    wealth = min(1.0, max(0.0, grad + jitter))
    median_income = 28000.0 + wealth * 145000.0
    pct_internet = 0.45 + wealth * 0.5 + float(_rng(geoid, "net").normal(0, 0.03))
    pct_internet = min(0.99, max(0.2, pct_internet))
    return {
        "wealth": wealth,
        "median_income": round(median_income, 1),
        "pct_internet_subscription": round(pct_internet, 4),
    }


def tract_ij(geoid: str) -> tuple[int, int]:
    """Grid coordinates encoded in the last 4 chars of the geoid."""
    tail = geoid[-4:]
    return int(tail[:2]), int(tail[2:])


def make_tracts(region: str) -> list[dict[str, Any]]:
    """Phase 2 analysis units. Real mode -> Census TIGERweb tract polygons for
    the region bbox; offline -> a synthetic grid tiling the bbox."""
    if equity_mode() == "real":
        from . import sources_real as R

        return R.real_resolve_tracts(region_bbox(region))
    n = grid_size()
    lon0, lat0, lon1, lat1 = region_bbox(region)
    dlon = (lon1 - lon0) / n
    dlat = (lat1 - lat0) / n
    # crude km-per-degree at this latitude for area
    midlat = (lat0 + lat1) / 2.0
    km_per_deg_lat = 110.574
    km_per_deg_lon = 111.320 * math.cos(math.radians(midlat))
    cell_km2 = abs(dlon * km_per_deg_lon) * abs(dlat * km_per_deg_lat)
    tracts = []
    for i in range(n):
        for j in range(n):
            x0, y0 = lon0 + i * dlon, lat0 + j * dlat
            x1, y1 = x0 + dlon, y0 + dlat
            poly_wkt = f"POLYGON (({x0} {y0}, {x1} {y0}, {x1} {y1}, {x0} {y1}, {x0} {y0}))"
            geoid = f"06001{i:02d}{j:02d}"
            tracts.append(
                {
                    "geoid": geoid,
                    "name": f"Tract {i}.{j}",
                    "geometry_wkt": poly_wkt,
                    "area_km2": round(cell_km2, 4),
                }
            )
    return tracts


def generate_region_osm(region: str, tracts: list[dict]) -> list[dict]:
    """Synthesize OSM features for the whole region as GeoJSON.

    Feature density, tag completeness, POI diversity and edit recency all
    scale with the tract's connectivity - the digital-divide signal the
    study is designed to detect.

    TODO(real): replace with an Overpass/ohsome bbox pull (buildings, highways,
    amenities) with full metadata (timestamps) - fetched ONCE for the region.
    """
    features: list[dict] = []
    for t in tracts:
        geoid = t["geoid"]
        prof = tract_profile(geoid, region)
        wealth = prof["wealth"]
        poly = shapely_wkt.loads(t["geometry_wkt"])
        x0, y0, x1, y1 = poly.bounds
        rng = _rng(geoid, "osm")
        # counts scale with connectivity
        n_buildings = int(15 + wealth * 120 + rng.integers(0, 12))
        n_roads = int(3 + wealth * 12)
        n_pois = int(2 + wealth * 30)
        # buildings
        for _ in range(n_buildings):
            px = float(rng.uniform(x0, x1))
            py = float(rng.uniform(y0, y1))
            features.append(
                {
                    "type": "Feature",
                    "properties": {
                        "geoid": geoid,
                        "osm_kind": "building",
                        "building": "yes",
                        # wealthier tracts edited more recently
                        "recency_days": int(rng.uniform(20, 400) * (1.6 - wealth)),
                    },
                    "geometry": mapping(Point(px, py)),
                }
            )
        # roads as short linestrings
        for _ in range(n_roads):
            px = float(rng.uniform(x0, x1))
            py = float(rng.uniform(y0, y1))
            qx = px + float(rng.uniform(-0.004, 0.004))
            qy = py + float(rng.uniform(-0.004, 0.004))
            features.append(
                {
                    "type": "Feature",
                    "properties": {
                        "geoid": geoid,
                        "osm_kind": "road",
                        "highway": "residential",
                        "recency_days": int(rng.uniform(20, 400) * (1.6 - wealth)),
                    },
                    "geometry": {"type": "LineString", "coordinates": [[px, py], [qx, qy]]},
                }
            )
        # POIs - richer, better-tagged, more diverse in connected tracts
        n_types = max(1, int(1 + wealth * (len(_AMENITY_TYPES) - 1)))
        for k in range(n_pois):
            px = float(rng.uniform(x0, x1))
            py = float(rng.uniform(y0, y1))
            amenity = _AMENITY_TYPES[int(rng.integers(0, n_types))]
            props = {
                "geoid": geoid,
                "osm_kind": "poi",
                "amenity": amenity,
                "recency_days": int(rng.uniform(20, 400) * (1.6 - wealth)),
            }
            # tag completeness scales with connectivity
            for tag in _KEY_TAGS:
                if rng.random() < 0.35 + wealth * 0.6:
                    props[tag] = f"{amenity}-{k}" if tag == "name" else "set"
            features.append(
                {"type": "Feature", "properties": props, "geometry": mapping(Point(px, py))}
            )
    return features


def fetch_region_osm(region: str, update: bool, tracts: list[dict]) -> tuple[str, str, bool]:
    """Update-gated region OSM fetch. Returns (path, snapshot, was_cached).

    If the extract already exists it is reused unless `update` is set -
    exactly the "only fetch if the update flag is set" behaviour, and the
    reason region OSM is pulled once rather than per tract.
    """
    path = out_dir() / f"osm_{_slug(region)}.geojson"
    snap_path = out_dir() / f"osm_{_slug(region)}.snapshot"
    if path.exists() and snap_path.exists() and not update:
        return str(path), snap_path.read_text().strip(), True
    if equity_mode() == "real":
        from . import sources_real as R

        features, snapshot = R.real_fetch_region_osm(region_bbox(region))
        _write_geojson(path, features)
    else:
        features = generate_region_osm(region, tracts)
        _write_geojson(path, features)
        # snapshot stamp is derived from content, not wall-clock (determinism)
        snapshot = "osm-" + hashlib.sha256(path.read_bytes()).hexdigest()[:12]
    snap_path.write_text(snapshot)
    return str(path), snapshot, False


def fetch_region_footprints(
    region: str, source: str, update: bool, tracts: list[dict]
) -> tuple[str, bool]:
    """Update-gated authoritative-footprint fetch (extrinsic benchmark).

    Returns (path, available). `available` is false when no reference layer
    covers the region - the workflow then records has_reference=false per
    tract rather than fabricating an extrinsic score.

    Real mode -> Microsoft Global ML Building Footprints for the region bbox
    (city/metro scale). If no tiles cover the region, has_reference stays false.
    Offline -> a synthetic reference layer.
    """
    if equity_mode() == "real":
        path = out_dir() / f"footprints_{_slug(region)}.geojson"
        if path.exists() and not update:
            return str(path), True
        from . import sources_real as R

        feats = R.real_fetch_footprints(region_bbox(region), str(out_dir()))
        if not feats:
            return "", False
        _write_geojson(path, feats)
        return str(path), True
    # Offline: a deployment might have no benchmark for some regions/sources.
    available = source.strip().lower() in {
        "microsoft_open_buildings",
        "google_open_buildings",
        "municipal",
    }
    path = out_dir() / f"footprints_{_slug(region)}.geojson"
    if not available:
        return "", False
    if path.exists() and not update:
        return str(path), True
    # reference building count per tract = "true" count the OSM sample under-covers
    feats = []
    for t in tracts:
        geoid = t["geoid"]
        poly = shapely_wkt.loads(t["geometry_wkt"])
        x0, y0, x1, y1 = poly.bounds
        rng = _rng(geoid, "ref")
        # authoritative count is the "complete" total; OSM completeness = osm/ref
        true_count = int(60 + rng.integers(0, 60))
        for _ in range(true_count):
            px = float(rng.uniform(x0, x1))
            py = float(rng.uniform(y0, y1))
            feats.append(
                {
                    "type": "Feature",
                    "properties": {"geoid": geoid, "ref": "building"},
                    "geometry": mapping(Point(px, py)),
                }
            )
    _write_geojson(path, feats)
    return str(path), True


# ---------------------------------------------------------------------------
# Phase 2/3 - census equity, clip, intrinsic + extrinsic quality
# ---------------------------------------------------------------------------


def fetch_census_equity(geoid: str, acs_year: int, region: str | None = None) -> dict[str, Any]:
    """Per-tract ACS equity variables (Phase 2 key variables).

    Real mode -> Census ACS 5-year API (B19013 income, B25070 rent burden,
    B03002 race, B06009 education, C16002 English, B08201 vehicles, B28002
    internet), fetched per county and cached. Offline -> synthetic profile.
    """
    if equity_mode() == "real":
        from . import sources_real as R

        return R.real_fetch_census_equity(
            geoid, int(acs_year), os.environ.get("CENSUS_API_KEY", "")
        )
    prof = tract_profile(geoid, region)
    wealth = prof["wealth"]
    rng = _rng(geoid, "acs", acs_year)

    def _pct(base, span, lo=0.0, hi=1.0):
        return round(min(hi, max(lo, base + span + float(rng.normal(0, 0.02)))), 4)

    return {
        "geoid": geoid,
        "median_income": prof["median_income"],
        # disadvantage indicators fall as wealth rises
        "pct_rent_burdened": _pct(0.55, -wealth * 0.35),
        "pct_bipoc": _pct(0.75, -wealth * 0.5),
        "pct_no_hs_diploma": _pct(0.32, -wealth * 0.28),
        "pct_limited_english": _pct(0.24, -wealth * 0.2),
        "pct_zero_vehicle": _pct(0.30, -wealth * 0.24),
        "pct_internet_subscription": prof["pct_internet_subscription"],
    }


# region-extract STRtree cache: {region_osm_path: (features, rep_points, STRtree)}
# built once, reused across all per-tract clips (so a 254k-feature real extract
# is indexed a single time rather than rescanned per tract).
_clip_index: dict[str, tuple[list[dict], list, Any]] = {}


def _region_index(region_osm_path: str):
    cached = _clip_index.get(region_osm_path)
    if cached is not None:
        return cached
    from shapely.strtree import STRtree

    feats = _read_geojson(region_osm_path)
    reps = []
    for f in feats:
        g = shape(f["geometry"])
        reps.append(g if g.geom_type == "Point" else g.representative_point())
    tree = STRtree(reps)
    entry = (feats, reps, tree)
    _clip_index[region_osm_path] = entry
    return entry


def clip_tract_osm(region_osm_path: str, geometry_wkt: str) -> str:
    """Clip the shared region extract to a tract polygon - LOCAL, no network.
    Uses a cached STRtree over the region so each tract is a fast index query."""
    poly = shapely_wkt.loads(geometry_wkt)
    feats, reps, tree = _region_index(region_osm_path)
    kept = []
    for i in tree.query(poly):  # bbox candidates
        if poly.covers(reps[i]):
            kept.append(feats[i])
    tag = hashlib.sha256((region_osm_path + geometry_wkt).encode()).hexdigest()[:10]
    path = out_dir() / f"tract_{tag}.geojson"
    _write_geojson(path, kept)
    return str(path)


def _haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lon1, lat1, lon2, lat2 = map(math.radians, [a[0], a[1], b[0], b[1]])
    dlon, dlat = lon2 - lon1, lat2 - lat1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(h))


def compute_attribute_quality(osm_path: str, area_km2: float, geoid: str) -> dict[str, Any]:
    """Phase 3.1 intrinsic metrics from the tract's OSM features."""
    feats = _read_geojson(osm_path)
    buildings = [f for f in feats if f["properties"].get("osm_kind") == "building"]
    roads = [f for f in feats if f["properties"].get("osm_kind") == "road"]
    pois = [f for f in feats if f["properties"].get("osm_kind") == "poi"]

    area = max(area_km2, 1e-6)
    building_density = len(buildings) / area

    road_km = 0.0
    for r in roads:
        coords = r["geometry"]["coordinates"]
        road_km += _haversine_km(tuple(coords[0]), tuple(coords[-1]))
    road_km_per_km2 = road_km / area

    # attribute completeness: mean fraction of key tags present across POIs
    if pois:
        completeness = float(
            np.mean(
                [sum(1 for t in _KEY_TAGS if p["properties"].get(t)) / len(_KEY_TAGS) for p in pois]
            )
        )
    else:
        completeness = 0.0

    # POI diversity: Shannon entropy over amenity types, normalised to [0,1]
    poi_diversity = 0.0
    if pois:
        counts: dict[str, int] = {}
        for p in pois:
            a = p["properties"].get("amenity", "other")
            counts[a] = counts.get(a, 0) + 1
        total = sum(counts.values())
        ent = -sum((c / total) * math.log(c / total) for c in counts.values())
        poi_diversity = ent / math.log(len(_AMENITY_TYPES))

    recencies = [
        f["properties"].get("recency_days")
        for f in feats
        if f["properties"].get("recency_days") is not None
    ]
    median_recency = float(np.median(recencies)) if recencies else 0.0

    return {
        "geoid": geoid,
        "building_density": round(building_density, 3),
        "road_km_per_km2": round(road_km_per_km2, 4),
        "attr_completeness": round(completeness, 4),
        "poi_diversity": round(poi_diversity, 4),
        "median_recency_days": round(median_recency, 1),
    }


def compute_extrinsic_quality(
    osm_path: str, geometry_wkt: str, footprints_path: str, reference_available: bool, geoid: str
) -> dict[str, Any]:
    """Phase 3.2 extrinsic metrics - GATED on reference availability.

    When there is no authoritative footprint layer, has_reference is false
    and the numeric metrics are emitted as -1.0 sentinels (explicitly N/A),
    never silently zeroed.
    """
    if not reference_available or not footprints_path:
        return {
            "geoid": geoid,
            "footprint_completeness": -1.0,
            "positional_accuracy_m": -1.0,
            "has_reference": False,
        }
    poly = shapely_wkt.loads(geometry_wkt)
    osm_buildings = [
        shape(f["geometry"])
        for f in _read_geojson(osm_path)
        if f["properties"].get("osm_kind") == "building"
    ]
    # reference buildings inside this tract, via the cached STRtree over the
    # whole footprint layer (fast even at ~300k Microsoft footprints)
    ref_feats, ref_reps, ref_tree = _region_index(footprints_path)
    ref_idx = [i for i in ref_tree.query(poly) if poly.covers(ref_reps[i])]
    ref_pts = [ref_reps[i] for i in ref_idx]
    ref_n = len(ref_pts)
    completeness = (len(osm_buildings) / ref_n) if ref_n else 0.0
    # positional accuracy: mean nearest-neighbour distance OSM->reference (m),
    # nearest looked up through the footprint STRtree
    if osm_buildings and ref_n:
        dists = []
        for p in osm_buildings[:120]:  # cap for speed
            nearest = ref_tree.nearest(p)
            r = ref_reps[nearest]
            dists.append(_haversine_km((p.x, p.y), (r.x, r.y)) * 1000.0)
        positional = float(np.mean(dists))
    else:
        positional = -1.0
    return {
        "geoid": geoid,
        "footprint_completeness": round(min(completeness, 1.0), 4),
        "positional_accuracy_m": round(positional, 2),
        "has_reference": True,
    }


# ---------------------------------------------------------------------------
# Phase 4 - statistics & spatial analysis
# ---------------------------------------------------------------------------


def _as_list(records: Any) -> list[dict]:
    if isinstance(records, str):
        records = json.loads(records)
    return list(records or [])


def _centroids(records: list[dict]) -> np.ndarray:
    pts = []
    for r in records:
        c = shapely_wkt.loads(r["geometry_wkt"]).centroid
        pts.append([c.x, c.y])
    return np.array(pts)


def _income(records: list[dict]) -> np.ndarray:
    return np.array([float(r["equity"]["median_income"]) for r in records])


def spearman(records: list[dict]) -> tuple[float, float]:
    """Phase 4.1 - Spearman rank correlation: income vs POI diversity."""
    recs = _as_list(records)
    inc = _income(recs)
    div = np.array([float(r["intrinsic"]["poi_diversity"]) for r in recs])
    if len(recs) < 3:
        return 0.0, 1.0
    rho, p = stats.spearmanr(inc, div)
    return float(round(rho, 4)), float(round(p, 5))


def _weights(coords: np.ndarray, k: int = 4) -> np.ndarray:
    """Row-standardised k-nearest-neighbour spatial weights matrix."""
    n = len(coords)
    W = np.zeros((n, n))
    for i in range(n):
        d = np.array(
            [
                _haversine_km(tuple(coords[i]), tuple(coords[j])) if j != i else np.inf
                for j in range(n)
            ]
        )
        nn = np.argsort(d)[: min(k, n - 1)]
        W[i, nn] = 1.0
    rs = W.sum(axis=1, keepdims=True)
    rs[rs == 0] = 1.0
    return W / rs


def morans_i(
    records: list[dict], value: str = "attr_completeness", perms: int = 199
) -> tuple[float, float]:
    """Phase 4.2 - Global Moran's I on a quality metric, with a seeded
    permutation p-value. Detects whether 'data deserts' cluster spatially."""
    recs = _as_list(records)
    n = len(recs)
    if n < 4:
        return 0.0, 1.0
    coords = _centroids(recs)
    x = np.array([float(r["intrinsic"][value]) for r in recs])
    W = _weights(coords)
    z = x - x.mean()
    denom = (z**2).sum()

    def _I(zv):
        num = (W * np.outer(zv, zv)).sum()
        return (n / W.sum()) * (num / denom) if denom else 0.0

    obs = _I(z)
    rng = _rng("morans", n, round(float(x.sum()), 3))
    ge = 1
    for _ in range(perms):
        if _I(rng.permutation(z)) >= obs:
            ge += 1
    return float(round(obs, 4)), float(round(ge / (perms + 1), 5))


def gwr(records: list[dict]) -> tuple[str, float]:
    """Phase 4.3 - a simplified geographically weighted regression of
    attribute completeness ~ income, fit locally with a Gaussian kernel over
    each tract's neighbourhood. Returns (summary_path, mean_local_r2).

    TODO(real): swap in mgwr.gwr.GWR with an adaptive-bandwidth selector."""
    recs = _as_list(records)
    n = len(recs)
    coords = _centroids(recs)
    x = _income(recs)
    y = np.array([float(r["intrinsic"]["attr_completeness"]) for r in recs])
    x = (x - x.mean()) / (x.std() or 1.0)
    rows = []
    r2s = []
    # bandwidth = median pairwise distance
    dmat = np.array(
        [[_haversine_km(tuple(coords[i]), tuple(coords[j])) for j in range(n)] for i in range(n)]
    )
    bw = float(np.median(dmat[dmat > 0])) or 1.0
    for i in range(n):
        w = np.exp(-(dmat[i] ** 2) / (2 * bw**2))
        W = np.diag(w)
        X = np.column_stack([np.ones(n), x])
        try:
            beta = np.linalg.solve(X.T @ W @ X, X.T @ W @ y)
        except np.linalg.LinAlgError:
            continue
        pred = X @ beta
        ss_res = (w * (y - pred) ** 2).sum()
        ss_tot = (w * (y - np.average(y, weights=w)) ** 2).sum()
        local_r2 = 1.0 - (ss_res / ss_tot) if ss_tot else 0.0
        r2s.append(local_r2)
        rows.append(
            {
                "geoid": recs[i]["geoid"],
                "intercept": round(float(beta[0]), 4),
                "income_coef": round(float(beta[1]), 4),
                "local_r2": round(float(local_r2), 4),
            }
        )
    mean_r2 = float(np.mean(r2s)) if r2s else 0.0
    path = out_dir() / "gwr_summary.json"
    path.write_text(
        json.dumps(
            {"bandwidth_km": round(bw, 3), "mean_local_r2": round(mean_r2, 4), "local_fits": rows},
            indent=2,
        )
    )
    return str(path), round(mean_r2, 4)


def temporal_evolution(records: list[dict], snapshot: str) -> tuple[str, float]:
    """Phase 4.4 - is the mapping divide widening? Uses edit recency as the
    temporal signal: compares mean feature age in the poorest vs richest
    income tercile. A positive gap (poorer areas older) = a persistent divide.

    TODO(real): drive this from the ohsome quality API time series."""
    recs = _as_list(records)
    if len(recs) < 3:
        return "insufficient_data", 0.0
    inc = _income(recs)
    rec_days = np.array([float(r["intrinsic"]["median_recency_days"]) for r in recs])
    order = np.argsort(inc)
    k = max(1, len(recs) // 3)
    poor = rec_days[order[:k]].mean()
    rich = rec_days[order[-k:]].mean()
    gap = float(poor - rich)  # days older in poor tracts
    trend = "widening" if gap > 30 else ("narrowing" if gap < -30 else "stable")
    return trend, round(gap, 1)


# ---------------------------------------------------------------------------
# Phase 5 - reporting
# ---------------------------------------------------------------------------


def data_deserts(records: list[dict]) -> list[str]:
    """Bottom-quartile tracts by a composite completeness score = priority
    neighbourhoods where missing maps may hinder emergency/transit routing."""
    recs = _as_list(records)
    if not recs:
        return []
    scored = []
    for r in recs:
        iq = r["intrinsic"]
        score = (
            0.5 * iq["attr_completeness"]
            + 0.3 * min(iq["building_density"] / 100.0, 1.0)
            + 0.2 * iq["poi_diversity"]
        )
        scored.append((r["geoid"], score))
    scored.sort(key=lambda s: s[1])
    cut = max(1, len(scored) // 4)
    return [g for g, _ in scored[:cut]]


def render_maplibre_map(
    feats: list[dict],
    title: str,
    stats_res: dict,
    deserts: list[str],
    synthetic: bool = True,
    metric: str = "attr_completeness",
    metric_label: str = "Attribute completeness",
) -> str:
    """Self-contained MapLibre GL choropleth ("heat map") of a per-unit OSM
    metric (default attribute completeness), styled to match the facetwork-maps
    gallery: inline GeoJSON, CARTO Voyager raster basemap, click popups, a
    legend, and a findings banner. Data-desert units get a red outline.
    Negative metric values (N/A sentinels, e.g. no footprint reference) render
    grey rather than as the worst colour."""
    fc = json.dumps({"type": "FeatureCollection", "features": feats})
    # total bounds via shapely (handles Polygon + MultiPolygon uniformly)
    xmin = ymin = float("inf")
    xmax = ymax = float("-inf")
    for f in feats:
        a, b, c, d = shape(f["geometry"]).bounds
        xmin, ymin, xmax, ymax = min(xmin, a), min(ymin, b), max(xmax, c), max(ymax, d)
    bounds = (
        [[xmin, ymin], [xmax, ymax]] if xmin != float("inf") else [[-124.4, 32.5], [-114.1, 42.0]]
    )
    rho = stats_res.get("spearman_rho", 0.0)
    moran = stats_res.get("morans_i", 0.0)
    trend = stats_res.get("temporal_trend", "n/a")
    synth = (
        '<div class="synth">&#9888; SYNTHETIC DEMONSTRATION DATA - methodology demo, '
        "not real OSM/Census values</div>"
        if synthetic
        else ""
    )
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<script src="https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.js"></script>
<link href="https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.css" rel="stylesheet">
<style>
  html,body{{margin:0;height:100%;font-family:system-ui,sans-serif}}
  #map{{position:absolute;inset:0}}
  .panel{{position:absolute;top:12px;left:12px;z-index:2;background:rgba(255,255,255,.94);
    padding:12px 14px;border-radius:8px;box-shadow:0 1px 6px rgba(0,0,0,.3);max-width:320px;font-size:13px}}
  .panel h1{{font-size:15px;margin:0 0 6px}}
  .synth{{color:#b00;font-weight:600;font-size:11px;margin-bottom:6px}}
  .legend{{position:absolute;bottom:20px;left:12px;z-index:2;background:rgba(255,255,255,.94);
    padding:8px 10px;border-radius:8px;box-shadow:0 1px 6px rgba(0,0,0,.3);font-size:12px}}
  .bar{{height:10px;width:180px;background:linear-gradient(90deg,#d73027,#fc8d59,#fee08b,#91cf60,#1a9850);
    border-radius:2px;margin:4px 0}}
  .lab{{display:flex;justify-content:space-between;width:180px}}
  .maplibregl-popup-content{{font-size:12px}}
</style></head><body>
<div id="map"></div>
<div class="panel">
  {synth}
  <h1>{title}</h1>
  <div>Tract OSM <b>attribute completeness</b> (green = well-mapped, red = under-mapped).
  Red-outlined tracts are <b>data deserts</b> (bottom quartile).</div>
  <div style="margin-top:6px;color:#333">
    Spearman income~POI diversity <b>&rho;={rho:.2f}</b> &middot;
    Moran's I <b>{moran:.2f}</b> &middot; divide <b>{trend}</b>
  </div>
</div>
<div class="legend">
  <div>{metric_label}</div>
  <div class="bar"></div>
  <div class="lab"><span>0.0</span><span>0.5</span><span>1.0</span></div>
</div>
<script>
  const data = {fc};
  const map = new maplibregl.Map({{
    container:'map',
    style:{{version:8,sources:{{carto:{{type:'raster',
      tiles:['https://a.basemaps.cartocdn.com/rastertiles/voyager/{{z}}/{{x}}/{{y}}.png',
             'https://b.basemaps.cartocdn.com/rastertiles/voyager/{{z}}/{{x}}/{{y}}.png'],
      tileSize:256,attribution:'&copy; OpenStreetMap &copy; CARTO'}}}},
      layers:[{{id:'carto',type:'raster',source:'carto'}}]}},
    bounds:{json.dumps(bounds)}, fitBoundsOptions:{{padding:30}}
  }});
  map.addControl(new maplibregl.NavigationControl());
  map.on('load',()=>{{
    map.addSource('tracts',{{type:'geojson',data}});
    map.addLayer({{id:'fill',type:'fill',source:'tracts',paint:{{
      'fill-color':['case',['<',['to-number',['get','{metric}']],0],'#999999',
        ['interpolate',['linear'],['to-number',['get','{metric}']],
         0.0,'#d73027',0.25,'#fc8d59',0.5,'#fee08b',0.75,'#91cf60',1.0,'#1a9850']],
      'fill-opacity':0.72}}}});
    map.addLayer({{id:'outline',type:'line',source:'tracts',paint:{{
      'line-color':'#333','line-width':0.4}}}});
    map.addLayer({{id:'desert',type:'line',source:'tracts',
      filter:['==',['get','data_desert'],true],
      paint:{{'line-color':'#b00','line-width':2.2}}}});
    map.on('click','fill',(e)=>{{
      const p=e.features[0].properties;
      const fc = p.has_reference==='true'||p.has_reference===true
        ? Number(p.footprint_completeness).toFixed(2) : 'N/A';
      new maplibregl.Popup().setLngLat(e.lngLat).setHTML(
        `<b>Tract ${{p.geoid}}</b><br>Median income: $${{Number(p.median_income).toLocaleString()}}<br>`+
        `Attr completeness: ${{Number(p.attr_completeness).toFixed(2)}}<br>`+
        `POI diversity: ${{Number(p.poi_diversity).toFixed(2)}}<br>`+
        `Footprint completeness: ${{fc}}<br>`+
        `Data desert: ${{p.data_desert==='true'||p.data_desert===true?'YES':'no'}}`).addTo(map);
    }});
    map.on('mouseenter','fill',()=>map.getCanvas().style.cursor='pointer');
    map.on('mouseleave','fill',()=>map.getCanvas().style.cursor='');
  }});
</script></body></html>"""


def build_report(records: list[dict], stats_res: dict, title: str) -> tuple[str, str, list[str]]:
    """Phase 5 - interactive MapLibre heat map + an HTML findings report +
    the ranked data-desert list, with intervention recommendations.
    Returns (report_html_path, map_html_path, deserts)."""
    recs = _as_list(records)
    deserts = data_deserts(recs)
    n_ref = sum(1 for r in recs if r["extrinsic"].get("has_reference"))

    # choropleth features: tract polygons + per-tract properties
    feats = []
    for r in recs:
        feats.append(
            {
                "type": "Feature",
                "properties": {
                    "geoid": r["geoid"],
                    "median_income": r["equity"]["median_income"],
                    "attr_completeness": r["intrinsic"]["attr_completeness"],
                    "poi_diversity": r["intrinsic"]["poi_diversity"],
                    "footprint_completeness": r["extrinsic"]["footprint_completeness"],
                    "has_reference": r["extrinsic"]["has_reference"],
                    "data_desert": r["geoid"] in deserts,
                },
                "geometry": mapping(shapely_wkt.loads(r["geometry_wkt"])),
            }
        )
    _write_geojson(out_dir() / "equity_choropleth.geojson", feats)

    # the interactive heat map (this is the returned map_path)
    map_path = out_dir() / "equity_map.html"
    map_path.write_text(
        render_maplibre_map(feats, title, stats_res, deserts, synthetic=equity_mode() != "real")
    )

    rho = stats_res.get("spearman_rho", 0.0)
    moran = stats_res.get("morans_i", 0.0)
    rows = "".join(
        f"<tr><td>{r['geoid']}</td><td>{r['equity']['median_income']:.0f}</td>"
        f"<td>{r['intrinsic']['attr_completeness']:.2f}</td>"
        f"<td>{r['intrinsic']['poi_diversity']:.2f}</td>"
        f"<td>{'N/A' if not r['extrinsic']['has_reference'] else format(r['extrinsic']['footprint_completeness'], '.2f')}</td>"
        f"<td>{'YES' if r['geoid'] in deserts else ''}</td></tr>"
        for r in sorted(recs, key=lambda x: x["equity"]["median_income"])
    )
    html = f"""<!doctype html><html><head><meta charset="utf-8"><title>{title}</title>
<style>body{{font-family:system-ui;margin:2rem;max-width:900px}}table{{border-collapse:collapse;width:100%}}
td,th{{border:1px solid #ccc;padding:4px 8px;text-align:right}}td:first-child,th:first-child{{text-align:left}}
.desert{{color:#b00}}</style></head><body>
<h1>{title}</h1>
<p><b>Hypothesis under test:</b> lower-income / lower-connectivity tracts show lower OSM completeness.</p>
<h2>Phase 4 - findings</h2>
<ul>
<li>Spearman(median income, POI diversity): <b>&rho; = {rho:.3f}</b> (p = {stats_res.get("spearman_pvalue", 1):.4f})</li>
<li>Global Moran's I (attribute completeness): <b>{moran:.3f}</b> (p = {stats_res.get("morans_pvalue", 1):.4f}) - {"clustered" if moran > 0.1 else "dispersed/random"}</li>
<li>GWR mean local R&sup2;: <b>{stats_res.get("gwr_mean_r2", 0):.3f}</b></li>
<li>Temporal divide: <b>{stats_res.get("temporal_trend", "n/a")}</b> ({stats_res.get("temporal_gap_change", 0):.0f} days older in poorest tercile)</li>
</ul>
<h2>Phase 5 - data deserts ({len(deserts)} of {len(recs)} tracts)</h2>
<p class="desert">Priority tracts: {", ".join(deserts) or "none"}</p>
<p><b>Extrinsic reference coverage:</b> {n_ref}/{len(recs)} tracts had an authoritative footprint layer.</p>
<h3>Recommended interventions</h3>
<ul>
<li>Targeted mapathons in the priority tracts, prioritising healthcare/transit POIs.</li>
<li>AI-assisted building footprint import where OSM lags the authoritative layer.</li>
<li>Re-survey stale tracts (highest median recency) to close the temporal gap.</li>
</ul>
<h2>Per-tract detail</h2>
<table><tr><th>GEOID</th><th>Median income</th><th>Attr completeness</th><th>POI diversity</th><th>Footprint compl.</th><th>Data desert</th></tr>
{rows}</table>
</body></html>"""
    report_path = out_dir() / "equity_report.html"
    report_path.write_text(html)
    return str(report_path), str(map_path), deserts

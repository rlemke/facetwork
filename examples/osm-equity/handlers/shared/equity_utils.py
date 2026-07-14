"""Shared computation for the osm.equity mapping-equity study.

Everything here is real geometry (shapely) + real statistics (scipy +
pure-numpy Moran's I and locally-weighted regression). The *data source*
has two paths:

  * a deterministic offline generator (default) that tiles the study
    region into a grid of synthetic census tracts whose income gradient
    is spatially smooth AND drives OSM feature density/recency/diversity
    (the "digital divide" signal) - so the downstream correlation,
    spatial-autocorrelation and GWR steps find a real relationship and
    the whole workflow runs with no network; and
  * hooks where a real deployment would swap in TIGER tracts, an Overpass
    /ohsome OSM pull, an ACS API call, and an authoritative footprint
    layer (Microsoft/Google Open Buildings). Those are marked TODO(real).

Determinism: all randomness is seeded from the geoid, so a re-run with the
same inputs is byte-identical (no os.urandom / unseeded RNG).
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
    """Tile the region bbox into a grid of tract polygons (Phase 2 units).

    TODO(real): replace with a Census TIGER/Line tract fetch for the region.
    """
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

    TODO(real): pull Microsoft/Google Open Buildings (or municipal open data)
    for the region bbox.
    """
    # A deployment might have no benchmark for some regions/sources.
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

    TODO(real): call the Census ACS API (needs CENSUS_API_KEY) for the given
    year and tract, pulling B19013 (income), B25070 (rent burden), B03002
    (race), B15003 (education), B16004 (English), B08201 (vehicles),
    B28002 (internet).
    """
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


def clip_tract_osm(region_osm_path: str, geometry_wkt: str) -> str:
    """Clip the shared region extract to a tract polygon - LOCAL, no network."""
    poly = shapely_wkt.loads(geometry_wkt)
    kept = []
    for f in _read_geojson(region_osm_path):
        geom = shape(f["geometry"])
        rep = geom if geom.geom_type == "Point" else geom.representative_point()
        if poly.covers(rep):
            kept.append(f)
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
        f for f in _read_geojson(osm_path) if f["properties"].get("osm_kind") == "building"
    ]
    ref_buildings = [
        f
        for f in _read_geojson(footprints_path)
        if f["properties"].get("geoid") == geoid or poly.covers(shape(f["geometry"]))
    ]
    ref_n = len(ref_buildings)
    completeness = (len(osm_buildings) / ref_n) if ref_n else 0.0
    # positional accuracy: mean nearest-neighbour distance OSM->reference (m)
    if osm_buildings and ref_buildings:
        ref_pts = [shape(f["geometry"]) for f in ref_buildings]
        dists = []
        for b in osm_buildings[:80]:  # cap for speed
            p = shape(b["geometry"])
            nn = min(_haversine_km((p.x, p.y), (r.x, r.y)) for r in ref_pts[:120])
            dists.append(nn * 1000.0)
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


def build_report(records: list[dict], stats_res: dict, title: str) -> tuple[str, str, list[str]]:
    """Phase 5 - HTML report + a simple GeoJSON choropleth artifact +
    the ranked data-desert list, with intervention recommendations."""
    recs = _as_list(records)
    deserts = data_deserts(recs)
    n_ref = sum(1 for r in recs if r["extrinsic"].get("has_reference"))

    # choropleth artifact: tract polygons coloured by completeness
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
    map_path = out_dir() / "equity_choropleth.geojson"
    _write_geojson(map_path, feats)

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

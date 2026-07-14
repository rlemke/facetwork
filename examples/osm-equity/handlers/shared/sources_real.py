"""Live data sources for the osm.equity study (opt-in via FW_EQUITY_SOURCE=real).

Replaces the deterministic offline generator with real APIs:

  * census tract geometry  -> Census TIGERweb ArcGIS REST (keyless)
  * equity variables       -> Census ACS 5-year API  (needs CENSUS_API_KEY)
  * OSM features           -> Overpass API (buildings/highways/amenities+meta)

Scope: real mode targets a CITY / METRO bounding box. Overpass and TIGERweb
will not return a whole continent in one request, and ACS is fetched per county
(one HTTP round-trip per county, cached), so continent-scale runs should use the
offline generator. All fetches retry with fallback endpoints; failures raise
explicitly rather than silently degrading (the caller decides mode).
"""

from __future__ import annotations

import math
import time
from datetime import UTC, datetime
from typing import Any

import requests
from shapely.geometry import shape

_UA = {"User-Agent": "facetwork-osm-equity/1.0 (research; github.com/rlemke/facetwork)"}

_TIGER_TRACTS = (
    "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/Tracts_Blocks/MapServer/4/query"
)
_OVERPASS_EPS = [
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]

# ACS 5-year detailed-table variables (one request per county returns all).
_ACS_VARS = [
    "B19013_001E",  # median household income
    "B25070_001E",
    "B25070_007E",
    "B25070_008E",
    "B25070_009E",
    "B25070_010E",  # rent burden >=30%
    "B03002_001E",
    "B03002_003E",  # total pop, white-non-hispanic (BIPOC = 1 - w/total)
    "B06009_001E",
    "B06009_002E",  # education total, less-than-HS
    "C16002_001E",
    "C16002_004E",
    "C16002_007E",
    "C16002_010E",
    "C16002_013E",  # limited-English HH
    "B08201_001E",
    "B08201_002E",  # households, zero-vehicle
    "B28002_001E",
    "B28002_013E",  # internet total, no-internet
]

_NULL = -666666600  # ACS uses -666666666 etc. as missing-value sentinels

_census_cache: dict[tuple[str, str, int], dict[str, dict]] = {}


def _km_per_deg(lat: float) -> tuple[float, float]:
    return 110.574, 111.320 * math.cos(math.radians(lat))


# ---------------------------------------------------------------------------
# TIGERweb — real census-tract polygons intersecting the region bbox
# ---------------------------------------------------------------------------


def real_resolve_tracts(bbox: tuple[float, float, float, float]) -> list[dict[str, Any]]:
    xmin, ymin, xmax, ymax = bbox
    tracts: list[dict[str, Any]] = []
    offset = 0
    while True:
        params = {
            "geometry": f"{xmin},{ymin},{xmax},{ymax}",
            "geometryType": "esriGeometryEnvelope",
            "inSR": "4326",
            "spatialRel": "esriSpatialRelIntersects",
            "outFields": "GEOID,NAME",
            "returnGeometry": "true",
            "outSR": "4326",
            "f": "geojson",
            "resultOffset": offset,
            "resultRecordCount": 800,
        }
        r = requests.get(_TIGER_TRACTS, params=params, headers=_UA, timeout=60)
        r.raise_for_status()
        feats = r.json().get("features", [])
        if not feats:
            break
        for f in feats:
            if not f.get("geometry"):
                continue
            geom = shape(f["geometry"])
            c = geom.centroid
            kpl, kpo = _km_per_deg(c.y)
            p = f.get("properties", {})
            tracts.append(
                {
                    "geoid": p.get("GEOID", ""),
                    "name": p.get("NAME", ""),
                    "geometry_wkt": geom.wkt,
                    "area_km2": round(geom.area * kpl * kpo, 4),
                }
            )
        if len(feats) < 800:
            break
        offset += 800
    if not tracts:
        raise RuntimeError("TIGERweb returned no tracts for the region bbox")
    return tracts


# ---------------------------------------------------------------------------
# Census ACS — real equity variables (per county, cached)
# ---------------------------------------------------------------------------


def _num(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f > _NULL else None


def _parse_acs_row(row: list, idx: dict[str, int]) -> dict[str, Any]:
    """Turn one ACS API row into an EquityVars dict (derived ratios, nulls dropped)."""

    def g(k: str) -> float | None:
        return _num(row[idx[k]])

    def ratio(numer_keys: list[str], denom_key: str) -> float:
        d = g(denom_key) or 0
        if not d:
            return 0.0
        n = sum(x for x in (g(k) for k in numer_keys) if x)
        return round(n / d, 4)

    geoid = row[idx["state"]] + row[idx["county"]] + row[idx["tract"]]
    inc = g("B19013_001E")
    bt, wnh = g("B03002_001E") or 0, g("B03002_003E") or 0
    it, noi = g("B28002_001E") or 0, g("B28002_013E") or 0
    return {
        "geoid": geoid,
        "median_income": float(inc) if inc is not None else 0.0,
        "pct_rent_burdened": ratio(
            ["B25070_007E", "B25070_008E", "B25070_009E", "B25070_010E"], "B25070_001E"
        ),
        "pct_bipoc": round(1 - wnh / bt, 4) if bt else 0.0,
        "pct_no_hs_diploma": ratio(["B06009_002E"], "B06009_001E"),
        "pct_limited_english": ratio(
            ["C16002_004E", "C16002_007E", "C16002_010E", "C16002_013E"], "C16002_001E"
        ),
        "pct_zero_vehicle": ratio(["B08201_002E"], "B08201_001E"),
        "pct_internet_subscription": round(1 - noi / it, 4) if it else 0.0,
    }


def real_region_census(state: str, county: str, year: int, key: str) -> dict[str, dict]:
    ck = (state, county, year)
    if ck in _census_cache:
        return _census_cache[ck]
    url = f"https://api.census.gov/data/{year}/acs/acs5"
    params = {
        "get": "NAME," + ",".join(_ACS_VARS),
        "for": "tract:*",
        "in": f"state:{state} county:{county}",
        "key": key,
    }
    r = requests.get(url, params=params, headers=_UA, timeout=60)
    r.raise_for_status()
    rows = r.json()
    idx = {h: i for i, h in enumerate(rows[0])}
    out: dict[str, dict] = {}
    for row in rows[1:]:
        rec = _parse_acs_row(row, idx)
        out[rec["geoid"]] = rec
    _census_cache[ck] = out
    return out


def real_fetch_census_equity(geoid: str, year: int, key: str) -> dict[str, Any]:
    if not key:
        raise RuntimeError("real mode needs CENSUS_API_KEY for ACS equity data")
    state, county = geoid[:2], geoid[2:5]
    reg = real_region_census(state, county, year, key)
    v = reg.get(geoid)
    if v is None:
        # tract with no ACS row (e.g. water/institutional) — explicit, not fabricated
        raise RuntimeError(f"no ACS data for tract {geoid}")
    return v


# ---------------------------------------------------------------------------
# Overpass — real OSM features for the region bbox
# ---------------------------------------------------------------------------


def _overpass(query: str, retries: int = 3) -> list[dict]:
    last = ""
    for _ in range(retries):
        for ep in _OVERPASS_EPS:
            try:
                r = requests.post(ep, data={"data": query}, headers=_UA, timeout=180)
                if r.status_code == 200 and r.headers.get("content-type", "").startswith(
                    "application/json"
                ):
                    return r.json().get("elements", [])
                last = f"{ep} -> HTTP {r.status_code}"
            except Exception as e:  # noqa: BLE001
                last = f"{ep} -> {str(e)[:60]}"
        time.sleep(3)
    raise RuntimeError(f"Overpass unavailable after retries ({last})")


def _recency_days(ts: str | None, now: datetime) -> int:
    if not ts:
        return 0
    try:
        dt = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
        return max(0, (now - dt).days)
    except ValueError:
        return 0


def real_fetch_region_osm(bbox: tuple[float, float, float, float]) -> tuple[list[dict], str]:
    xmin, ymin, xmax, ymax = bbox
    b = f"{ymin},{xmin},{ymax},{xmax}"  # Overpass order: S,W,N,E
    now = datetime.now(UTC)

    # buildings + amenities: centroids are enough (count/density/tags)
    q_bp = (
        f"[out:json][timeout:180];("
        f'way["building"]({b});node["amenity"]({b});way["amenity"]({b});'
        f");out center meta tags;"
    )
    # highways need geometry for length
    q_hw = f'[out:json][timeout:180];(way["highway"]({b}););out geom meta tags;'

    feats: list[dict] = []
    for el in _overpass(q_bp):
        tags = el.get("tags", {}) or {}
        rec = _recency_days(el.get("timestamp"), now)
        if el["type"] == "node":
            lon, lat = el.get("lon"), el.get("lat")
        else:
            c = el.get("center") or {}
            lon, lat = c.get("lon"), c.get("lat")
        if lon is None or lat is None:
            continue
        if "building" in tags:
            props = {"osm_kind": "building", "building": tags["building"], "recency_days": rec}
        else:
            props = {
                "osm_kind": "poi",
                "amenity": tags.get("amenity", "other"),
                "recency_days": rec,
            }
            for k in ("name", "addr:street", "opening_hours"):
                if tags.get(k):
                    props[k] = tags[k]
        feats.append(
            {
                "type": "Feature",
                "properties": props,
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
            }
        )

    for el in _overpass(q_hw):
        geom = el.get("geometry") or []
        if len(geom) < 2:
            continue
        coords = [[p["lon"], p["lat"]] for p in geom]
        feats.append(
            {
                "type": "Feature",
                "properties": {
                    "osm_kind": "road",
                    "highway": (el.get("tags") or {}).get("highway", "road"),
                    "recency_days": _recency_days(el.get("timestamp"), now),
                },
                "geometry": {"type": "LineString", "coordinates": coords},
            }
        )
    if not feats:
        raise RuntimeError("Overpass returned no OSM features for the region bbox")
    return feats, "osm-live-" + now.strftime("%Y%m%d")


def bbox_area_km2(bbox: tuple[float, float, float, float]) -> float:
    xmin, ymin, xmax, ymax = bbox
    kpl, kpo = _km_per_deg((ymin + ymax) / 2)
    return abs((xmax - xmin) * kpo) * abs((ymax - ymin) * kpl)

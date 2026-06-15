"""STAC search for Sentinel-2 L2A scenes.

Queries a STAC API (default Element84 Earth Search v1 over AWS Open Data) with a
bbox + datetime + ``eo:cloud_cover`` filter, paginating the item feed. Scene
asset (band) hrefs are resolved on demand via ``get_item_assets``. The mock path
returns a deterministic scene list offline. Dependency-light: ``requests`` only,
so the tool stays runtime-free per the tools-pattern contract.
"""

from __future__ import annotations

from typing import Any

from _s2_tools import s2_mocks

# Element84 Earth Search asset keys for the bands we use.
_BAND_ASSETS = {"red": "red", "nir": "nir", "green": "green", "swir16": "swir16"}
_USER_AGENT = "facetwork-sentinel2-landchange"
_PAGE_LIMIT = 100
_MAX_PAGES = 20  # safety cap; bump for very wide windows


def parse_bbox(aoi: str) -> tuple[float, float, float, float]:
    parts = [p.strip() for p in aoi.split(",")]
    if len(parts) != 4:
        raise ValueError(f"aoi must be 'min_lon,min_lat,max_lon,max_lat' (got {aoi!r})")
    return (float(parts[0]), float(parts[1]), float(parts[2]), float(parts[3]))


def search(
    aoi: str,
    date_from: str,
    date_to: str,
    *,
    max_cloud: float = 20.0,
    collection: str = "sentinel-2-l2a",
    stac_url: str = "https://earth-search.aws.element84.com/v1",
    use_mock: bool = False,
) -> list[dict[str, Any]]:
    """Return a list of scene dicts {scene_id, datetime, cloud_pct, cog_href}."""
    parse_bbox(aoi)  # validate early
    if use_mock:
        ids = s2_mocks.mock_scene_ids(aoi, date_from, date_to, max_cloud)
        return [
            {"scene_id": sid, "datetime": f"{date_from}T10:00:00Z", "cloud_pct": 5.0,
             "cog_href": f"mock://{sid}"}
            for sid in ids
        ]
    return _search_real(aoi, date_from, date_to, max_cloud, collection, stac_url)


def _search_real(aoi, date_from, date_to, max_cloud, collection, stac_url):
    import requests

    bbox = list(parse_bbox(aoi))
    body = {
        "collections": [collection],
        "bbox": bbox,
        "datetime": f"{date_from}T00:00:00Z/{date_to}T23:59:59Z",
        "query": {"eo:cloud_cover": {"lt": max_cloud}},
        "limit": _PAGE_LIMIT,
    }
    url = stac_url.rstrip("/") + "/search"
    out: list[dict[str, Any]] = []
    session = requests.Session()
    session.headers["User-Agent"] = _USER_AGENT
    for _page in range(_MAX_PAGES):
        resp = session.post(url, json=body, timeout=30)
        resp.raise_for_status()
        doc = resp.json()
        for feat in doc.get("features", []):
            props = feat.get("properties", {})
            out.append({
                "scene_id": feat["id"],
                "datetime": props.get("datetime", ""),
                "cloud_pct": float(props.get("eo:cloud_cover", 0.0)),
                "cog_href": "",  # band hrefs resolved lazily via get_item_assets
            })
        nxt = next((lnk for lnk in doc.get("links", []) if lnk.get("rel") == "next"), None)
        if not nxt:
            break
        # Earth Search "next" carries the full body in the link; re-POST it.
        body = nxt.get("body", body)
        url = nxt.get("href", url)
    return out


def get_item_assets(
    scene_id: str,
    *,
    collection: str = "sentinel-2-l2a",
    stac_url: str = "https://earth-search.aws.element84.com/v1",
) -> dict[str, str]:
    """Resolve a scene's band asset hrefs: {band_name: href} for red/nir/green/swir16."""
    import requests

    url = f"{stac_url.rstrip('/')}/collections/{collection}/items/{scene_id}"
    resp = requests.get(url, headers={"User-Agent": _USER_AGENT}, timeout=30)
    resp.raise_for_status()
    assets = resp.json().get("assets", {})
    hrefs: dict[str, str] = {}
    for band, asset_key in _BAND_ASSETS.items():
        asset = assets.get(asset_key)
        if asset and asset.get("href"):
            hrefs[band] = asset["href"]
    return hrefs

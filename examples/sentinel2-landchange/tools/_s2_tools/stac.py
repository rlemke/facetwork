"""STAC search for Sentinel-2 L2A scenes.

Real path queries a STAC API (default Element84 Earth Search v1 over AWS Open
Data) with a bbox + datetime + ``eo:cloud_cover`` filter. Mock path returns a
deterministic scene list offline.

TODO (real path): implement ``_search_real`` with ``requests`` (or the
``pystac-client`` lib) against ``{stac_url}/search``:
    POST {"collections":[collection], "bbox":[...], "datetime":"from/to",
          "query":{"eo:cloud_cover":{"lt":max_cloud}}, "limit":100}
paginate via the ``next`` link, and project each item to a SceneRef-shaped dict
(scene_id=item.id, datetime=item.properties.datetime, cloud_pct, and the COG
band hrefs from item.assets, e.g. red/nir/green/swir16). Keep it dependency-light
(requests only) so the tool stays runtime-free per the tools-pattern contract.
"""

from __future__ import annotations

from typing import Any

from _s2_tools import s2_mocks


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
    """Return a list of scene dicts {scene_id, datetime, cloud_pct, cog_href...}."""
    parse_bbox(aoi)  # validate early
    if use_mock:
        ids = s2_mocks.mock_scene_ids(aoi, date_from, date_to, max_cloud)
        return [
            {"scene_id": sid, "datetime": f"{date_from}T10:00:00Z", "cloud_pct": 5.0,
             "cog_href": f"mock://{sid}"}
            for sid in ids
        ]
    return _search_real(aoi, date_from, date_to, max_cloud, collection, stac_url)


def _search_real(aoi, date_from, date_to, max_cloud, collection, stac_url):  # pragma: no cover
    raise NotImplementedError(
        "Real STAC search not implemented yet. See the module TODO. "
        "Dependencies: requests (or pystac-client). Until then run with use_mock=true."
    )

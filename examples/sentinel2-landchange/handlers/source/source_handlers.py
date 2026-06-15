"""Source handlers — STAC search + per-scene index extraction.

Thin parameter-coercion layers over ``_s2_tools`` functions; side effects are
confined to the filesystem cache the tools manage.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from ..shared.s2_utils import raster, stac

logger = logging.getLogger("s2.source")
NAMESPACE = "s2.source"


def handle_search_scenes(params: dict[str, Any]) -> dict[str, Any]:
    scenes = stac.search(
        aoi=params["aoi"],
        date_from=params["date_from"],
        date_to=params["date_to"],
        max_cloud=float(params.get("max_cloud", 20.0)),
        collection=params.get("collection", "sentinel-2-l2a"),
        stac_url=params.get("stac_url", "https://earth-search.aws.element84.com/v1"),
        use_mock=bool(params.get("use_mock", False)),
    )
    scene_ids = [s["scene_id"] for s in scenes]
    logger.info("SearchScenes aoi=%s %s..%s -> %d scenes", params["aoi"],
                params["date_from"], params["date_to"], len(scene_ids))
    return {"count": len(scene_ids), "scene_ids": scene_ids}


def handle_fetch_scene_index(params: dict[str, Any]) -> dict[str, Any]:
    return raster.fetch_scene_index(
        scene_id=params["scene_id"],
        aoi=params["aoi"],
        index=params.get("index", "ndvi"),
        force=bool(params.get("force", False)),
        use_mock=bool(params.get("use_mock", False)),
    )


_DISPATCH: dict[str, Any] = {
    f"{NAMESPACE}.SearchScenes": handle_search_scenes,
    f"{NAMESPACE}.FetchSceneIndex": handle_fetch_scene_index,
}


def handle(payload: dict) -> dict:
    """RegistryRunner entrypoint."""
    return _DISPATCH[payload["_facet_name"]](payload)


def register_handlers(runner) -> None:
    """Register with a RegistryRunner."""
    for facet_name in _DISPATCH:
        runner.register_handler(
            facet_name=facet_name,
            module_uri=f"file://{os.path.abspath(__file__)}",
            entrypoint="handle",
        )


def register_source_handlers(poller) -> None:
    """Register with an AgentPoller."""
    for facet_name, handler in _DISPATCH.items():
        poller.register(facet_name, handler)

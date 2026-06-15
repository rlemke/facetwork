"""Render handler — change raster -> MapLibre HTML bundle."""

from __future__ import annotations

import logging
import os
from typing import Any

from ..shared.s2_utils import map_render

logger = logging.getLogger("s2.render")
NAMESPACE = "s2.render"


def handle_change_map(params: dict[str, Any]) -> dict[str, Any]:
    return map_render.render_change_map(
        change_rel=params["change_path"],
        aoi_key=params["aoi_key"],
        title=params.get("title", "Sentinel-2 land-cover change"),
        basemap_url=params.get("basemap_url", ""),
    )


_DISPATCH: dict[str, Any] = {
    f"{NAMESPACE}.ChangeMap": handle_change_map,
}


def handle(payload: dict) -> dict:
    """RegistryRunner entrypoint."""
    return _DISPATCH[payload["_facet_name"]](payload)


def register_handlers(runner) -> None:
    for facet_name in _DISPATCH:
        runner.register_handler(
            facet_name=facet_name,
            module_uri=f"file://{os.path.abspath(__file__)}",
            entrypoint="handle",
        )


def register_render_handlers(poller) -> None:
    for facet_name, handler in _DISPATCH.items():
        poller.register(facet_name, handler)

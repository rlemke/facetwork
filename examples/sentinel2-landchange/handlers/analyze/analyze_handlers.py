"""Analysis handlers — epoch composite + change detection.

Pure compute over the cached per-scene / composite rasters. No network.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from ..shared.s2_utils import raster

logger = logging.getLogger("s2.analyze")
NAMESPACE = "s2.analyze"


def handle_composite(params: dict[str, Any]) -> dict[str, Any]:
    return raster.composite(
        aoi=params["aoi"],
        date_from=params["date_from"],
        date_to=params["date_to"],
        scene_ids=params.get("scene_ids") or None,
        index=params.get("index", "ndvi"),
        reducer=params.get("reducer", "median"),
        use_mock=bool(params.get("use_mock", False)),
    )


def handle_detect_change(params: dict[str, Any]) -> dict[str, Any]:
    return raster.detect_change(
        baseline_rel=params["baseline_path"],
        recent_rel=params["recent_path"],
        aoi_key_str=params["aoi_key"],
        method=params.get("method", "difference"),
        threshold=float(params.get("threshold", 0.15)),
        use_mock=bool(params.get("use_mock", False)),
    )


_DISPATCH: dict[str, Any] = {
    f"{NAMESPACE}.Composite": handle_composite,
    f"{NAMESPACE}.DetectChange": handle_detect_change,
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


def register_analyze_handlers(poller) -> None:
    for facet_name, handler in _DISPATCH.items():
        poller.register(facet_name, handler)

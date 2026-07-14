"""Phase 3 - per-tract metrics: ClipTractOSM, FetchCensusEquity,
ComputeAttributeQuality, ComputeExtrinsicQuality, AssembleTractQuality."""

from __future__ import annotations

import json
import os
from typing import Any

from handlers.shared import equity_utils as U

NAMESPACE = "osm.equity"


def _d(v):
    return json.loads(v) if isinstance(v, str) else v


def _truthy(v: Any) -> bool:
    if isinstance(v, str):
        return v.strip().lower() in {"1", "true", "yes", "on"}
    return bool(v)


def handle_clip_tract_osm(params: dict[str, Any]) -> dict[str, Any]:
    path = U.clip_tract_osm(params["region_osm_path"], params["geometry_wkt"])
    return {"osm_path": path}


def handle_fetch_census_equity(params: dict[str, Any]) -> dict[str, Any]:
    vars_ = U.fetch_census_equity(params["geoid"], int(params.get("acs_year", 2022)))
    return {"vars": vars_}


def handle_compute_attribute_quality(params: dict[str, Any]) -> dict[str, Any]:
    q = U.compute_attribute_quality(
        params["osm_path"], float(params.get("area_km2", 1.0)), params.get("geoid", "")
    )
    return {"quality": q}


def handle_compute_extrinsic_quality(params: dict[str, Any]) -> dict[str, Any]:
    q = U.compute_extrinsic_quality(
        params["osm_path"],
        params["geometry_wkt"],
        params.get("footprints_path", ""),
        _truthy(params.get("reference_available", False)),
        params.get("geoid", ""),
    )
    return {"quality": q}


def handle_assemble_tract_quality(params: dict[str, Any]) -> dict[str, Any]:
    record = {
        "geoid": params["geoid"],
        "geometry_wkt": params["geometry_wkt"],
        "equity": _d(params["equity"]),
        "intrinsic": _d(params["intrinsic"]),
        "extrinsic": _d(params["extrinsic"]),
    }
    return {"record": record}


_DISPATCH: dict[str, Any] = {
    f"{NAMESPACE}.ClipTractOSM": handle_clip_tract_osm,
    f"{NAMESPACE}.FetchCensusEquity": handle_fetch_census_equity,
    f"{NAMESPACE}.ComputeAttributeQuality": handle_compute_attribute_quality,
    f"{NAMESPACE}.ComputeExtrinsicQuality": handle_compute_extrinsic_quality,
    f"{NAMESPACE}.AssembleTractQuality": handle_assemble_tract_quality,
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


def register_metrics_handlers(poller) -> None:
    for facet_name, fn in _DISPATCH.items():
        poller.register(facet_name, fn)

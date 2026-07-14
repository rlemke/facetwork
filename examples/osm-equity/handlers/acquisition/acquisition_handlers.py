"""Phase 2 - data acquisition: ResolveStudyTracts, FetchRegionOSM,
FetchRegionFootprints. Region-level fetches are update-gated (reuse the
cached extract unless `update` is set)."""

from __future__ import annotations

import os
from typing import Any

from handlers.shared import equity_utils as U

NAMESPACE = "osm.equity"


def _truthy(v: Any) -> bool:
    if isinstance(v, str):
        return v.strip().lower() in {"1", "true", "yes", "on"}
    return bool(v)


def _log(params, msg, level="success"):
    sl = params.get("_step_log")
    if sl is not None:
        sl.append({"message": msg, "level": level})


def handle_resolve_study_tracts(params: dict[str, Any]) -> dict[str, Any]:
    area = params.get("area", {})
    if isinstance(area, str):
        import json

        area = json.loads(area)
    region = area.get("region", "Oakland, CA")
    tracts = U.make_tracts(region)
    _log(params, f"Resolved {len(tracts)} census tracts for {region}")
    return {"tracts": tracts}


def handle_fetch_region_osm(params: dict[str, Any]) -> dict[str, Any]:
    region = params.get("region", "Oakland, CA")
    update = _truthy(params.get("update", False))
    tracts = U.make_tracts(region)
    path, snapshot, cached = U.fetch_region_osm(region, update, tracts)
    _log(params, f"Region OSM {'reused (cache)' if cached else 'fetched'} -> {snapshot}")
    return {"osm_path": path, "snapshot": snapshot}


def handle_fetch_region_footprints(params: dict[str, Any]) -> dict[str, Any]:
    region = params.get("region", "Oakland, CA")
    source = params.get("benchmark_source", "microsoft_open_buildings")
    update = _truthy(params.get("update", False))
    tracts = U.make_tracts(region)
    path, available = U.fetch_region_footprints(region, source, update, tracts)
    _log(
        params,
        f"Footprint reference '{source}': {'available' if available else 'NOT available (extrinsic metrics will be N/A)'}",
        level="success" if available else "warning",
    )
    return {"footprints_path": path, "available": available}


_DISPATCH: dict[str, Any] = {
    f"{NAMESPACE}.ResolveStudyTracts": handle_resolve_study_tracts,
    f"{NAMESPACE}.FetchRegionOSM": handle_fetch_region_osm,
    f"{NAMESPACE}.FetchRegionFootprints": handle_fetch_region_footprints,
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


def register_acquisition_handlers(poller) -> None:
    for facet_name, fn in _DISPATCH.items():
        poller.register(facet_name, fn)

"""Phase 1 - study design: DefineStudyArea."""

from __future__ import annotations

import os
from typing import Any

NAMESPACE = "osm.equity"


def handle_define_study_area(params: dict[str, Any]) -> dict[str, Any]:
    area = {
        "region": params.get("region", "Oakland, CA"),
        "scale": params.get("scale", "census_tract"),
        "hypothesis": params.get("hypothesis", ""),
        "acs_year": int(params.get("acs_year", 2022)),
    }
    _log(params, f"Study area defined: {area['region']} ({area['scale']}, ACS {area['acs_year']})")
    return {"area": area}


def _log(params, msg, level="success"):
    sl = params.get("_step_log")
    if sl is not None:
        sl.append({"message": msg, "level": level})


_DISPATCH: dict[str, Any] = {
    f"{NAMESPACE}.DefineStudyArea": handle_define_study_area,
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


def register_design_handlers(poller) -> None:
    for facet_name, fn in _DISPATCH.items():
        poller.register(facet_name, fn)

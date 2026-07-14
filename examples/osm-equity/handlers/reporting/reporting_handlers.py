"""Phase 5 - actionable reporting: BuildEquityReport."""

from __future__ import annotations

import json
import os
from typing import Any

from handlers.shared import equity_utils as U

NAMESPACE = "osm.equity"


def handle_build_equity_report(params: dict[str, Any]) -> dict[str, Any]:
    records = params.get("records", [])
    if isinstance(records, str):
        records = json.loads(records)
    stats_res = params.get("stats", {})
    if isinstance(stats_res, str):
        stats_res = json.loads(stats_res)
    title = params.get("title", "Digital Divide and OSM Mapping Equity")
    report_html, map_path, deserts = U.build_report(records, stats_res, title)

    sl = params.get("_step_log")
    if sl is not None:
        sl.append(
            {
                "message": f"Report built: {len(deserts)} data deserts across {len(records)} tracts",
                "level": "success",
            }
        )
    return {"report_html": report_html, "map_path": map_path, "data_deserts": deserts}


_DISPATCH: dict[str, Any] = {
    f"{NAMESPACE}.BuildEquityReport": handle_build_equity_report,
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


def register_reporting_handlers(poller) -> None:
    for facet_name, fn in _DISPATCH.items():
        poller.register(facet_name, fn)

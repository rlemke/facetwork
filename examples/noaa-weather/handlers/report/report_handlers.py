"""Report handlers for the noaa-weather example."""

from __future__ import annotations

import json
import os
from typing import Any

from handlers.shared.weather_utils import generate_batch_summary, generate_station_report

NAMESPACE = "weather.Report"


def handle_generate_station_report(params: dict[str, Any]) -> dict[str, Any]:
    """Handle GenerateStationReport event facet."""
    station_id = params.get("station_id", "")
    station_name = params.get("station_name", "")
    year = params.get("year", 2023)
    location = params.get("location", "")
    daily_stats = params.get("daily_stats", [])
    annual_precip = params.get("annual_precip", 0.0)
    narrative = params.get("narrative", "")
    if isinstance(year, str):
        year = int(year)
    if isinstance(daily_stats, str):
        daily_stats = json.loads(daily_stats)
    if isinstance(annual_precip, str):
        annual_precip = float(annual_precip)

    report = generate_station_report(
        station_id,
        station_name,
        year,
        location,
        daily_stats,
        annual_precip,
        narrative,
    )

    step_log = params.get("_step_log")
    if step_log is not None:
        msg = f"Station report generated: {report.get('report_id', '')}"
        if callable(step_log):
            step_log(msg, "success")
        else:
            step_log.append({"message": msg, "level": "success"})

    return {"report": report}


def handle_generate_batch_summary(params: dict[str, Any]) -> dict[str, Any]:
    """Handle GenerateBatchSummary event facet."""
    batch_id = params.get("batch_id", "")
    station_count = params.get("station_count", 0)
    results = params.get("results", [])
    if isinstance(station_count, str):
        station_count = int(station_count)
    if isinstance(results, str):
        results = json.loads(results)

    report_id, completed, failed, summary = generate_batch_summary(
        batch_id,
        station_count,
        results,
    )

    step_log = params.get("_step_log")
    if step_log is not None:
        msg = f"Batch summary for {batch_id}: {completed} completed, {failed} failed"
        if callable(step_log):
            step_log(msg, "success")
        else:
            step_log.append({"message": msg, "level": "success"})

    return {
        "report_id": report_id,
        "completed": completed,
        "failed": failed,
        "summary": summary,
    }


_DISPATCH: dict[str, Any] = {
    f"{NAMESPACE}.GenerateStationReport": handle_generate_station_report,
    f"{NAMESPACE}.GenerateBatchSummary": handle_generate_batch_summary,
}


def handle(payload: dict) -> dict:
    """RegistryRunner entrypoint."""
    facet = payload["_facet_name"]
    handler = _DISPATCH[facet]
    return handler(payload)


def register_handlers(runner) -> None:
    """Register with RegistryRunner."""
    for facet_name in _DISPATCH:
        runner.register_handler(
            facet_name=facet_name,
            module_uri=f"file://{os.path.abspath(__file__)}",
            entrypoint="handle",
        )


def register_report_handlers(poller) -> None:
    """Register with AgentPoller."""
    for facet_name, handler in _DISPATCH.items():
        poller.register(facet_name, handler)

"""Visualization handlers for the noaa-weather example."""

from __future__ import annotations

import json
import os
from typing import Any

from handlers.shared.weather_utils import render_html_report, render_station_map

NAMESPACE = "weather.Visualize"


def handle_render_html_report(params: dict[str, Any]) -> dict[str, Any]:
    """Handle RenderHTMLReport event facet."""
    station_id = params.get("station_id", "")
    station_name = params.get("station_name", "")
    year = params.get("year", 2023)
    location = params.get("location", "")
    daily_stats = params.get("daily_stats", [])
    annual_precip = params.get("annual_precip", 0.0)
    temp_range = params.get("temp_range", "N/A")
    narrative = params.get("narrative", "")
    if isinstance(year, str):
        year = int(year)
    if isinstance(daily_stats, str):
        daily_stats = json.loads(daily_stats)
    if isinstance(annual_precip, str):
        annual_precip = float(annual_precip)

    report_id = render_html_report(
        station_id,
        station_name,
        year,
        location,
        daily_stats,
        annual_precip,
        temp_range,
        narrative,
    )

    step_log = params.get("_step_log")
    if step_log is not None:
        msg = f"HTML report stored: {report_id}"
        if callable(step_log):
            step_log(msg, "success")
        else:
            step_log.append({"message": msg, "level": "success"})

    return {"report_id": report_id}


def handle_render_station_map(params: dict[str, Any]) -> dict[str, Any]:
    """Handle RenderStationMap event facet."""
    station_id = params.get("station_id", "")
    station_name = params.get("station_name", "")
    lat = params.get("lat", 0.0)
    lon = params.get("lon", 0.0)
    year = params.get("year", 2023)
    temp_range = params.get("temp_range", "N/A")
    if isinstance(lat, str):
        lat = float(lat)
    if isinstance(lon, str):
        lon = float(lon)
    if isinstance(year, str):
        year = int(year)

    report_id = render_station_map(
        station_id,
        station_name,
        lat,
        lon,
        year,
        temp_range,
    )

    step_log = params.get("_step_log")
    if step_log is not None:
        msg = (
            f"Station map stored: {report_id}" if report_id else "Map skipped (folium unavailable)"
        )
        if callable(step_log):
            step_log(msg, "success")
        else:
            step_log.append({"message": msg, "level": "success"})

    return {"report_id": report_id}


_DISPATCH: dict[str, Any] = {
    f"{NAMESPACE}.RenderHTMLReport": handle_render_html_report,
    f"{NAMESPACE}.RenderStationMap": handle_render_station_map,
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


def register_visualize_handlers(poller) -> None:
    """Register with AgentPoller."""
    for facet_name, handler in _DISPATCH.items():
        poller.register(facet_name, handler)

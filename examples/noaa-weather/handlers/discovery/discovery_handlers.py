"""Discovery handlers for the noaa-weather example."""

from __future__ import annotations

import os
from typing import Any

from handlers.shared.weather_utils import (
    download_station_inventory,
    filter_active_stations,
    parse_station_inventory,
)

NAMESPACE = "weather.Discovery"


def handle_discover_stations(params: dict[str, Any]) -> dict[str, Any]:
    """Handle DiscoverStations event facet."""
    country = params.get("country", "US")
    state = params.get("state", "")
    max_stations = params.get("max_stations", 10)
    if isinstance(max_stations, str):
        max_stations = int(max_stations)

    csv_text = download_station_inventory()
    all_stations = parse_station_inventory(csv_text)
    filtered = filter_active_stations(all_stations, country, state, max_stations)

    step_log = params.get("_step_log")
    if step_log is not None:
        msg = f"Discovered {len(filtered)} stations for {country}" + (f"/{state}" if state else "")
        if callable(step_log):
            step_log(msg, "success")
        else:
            step_log.append({"message": msg, "level": "success"})

    return {
        "stations": filtered,
        "station_count": len(filtered),
    }


_DISPATCH: dict[str, Any] = {
    f"{NAMESPACE}.DiscoverStations": handle_discover_stations,
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


def register_discovery_handlers(poller) -> None:
    """Register with AgentPoller."""
    for facet_name, handler in _DISPATCH.items():
        poller.register(facet_name, handler)

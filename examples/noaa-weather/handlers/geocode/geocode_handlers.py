"""Geocode handlers for the noaa-weather example."""

from __future__ import annotations

import os
from typing import Any

from handlers.shared.weather_utils import reverse_geocode_nominatim

NAMESPACE = "weather.Geocode"


def handle_reverse_geocode(params: dict[str, Any]) -> dict[str, Any]:
    """Handle ReverseGeocode event facet."""
    lat = params.get("lat", 0.0)
    lon = params.get("lon", 0.0)
    if isinstance(lat, str):
        lat = float(lat)
    if isinstance(lon, str):
        lon = float(lon)

    geo = reverse_geocode_nominatim(lat, lon)

    step_log = params.get("_step_log")
    if step_log is not None:
        msg = f"Geocoded ({lat}, {lon}): {geo.get('display_name', '')}"
        if callable(step_log):
            step_log(msg, "success")
        else:
            step_log.append({"message": msg, "level": "success"})

    return {"geo": geo}


_DISPATCH: dict[str, Any] = {
    f"{NAMESPACE}.ReverseGeocode": handle_reverse_geocode,
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


def register_geocode_handlers(poller) -> None:
    """Register with AgentPoller."""
    for facet_name, handler in _DISPATCH.items():
        poller.register(facet_name, handler)

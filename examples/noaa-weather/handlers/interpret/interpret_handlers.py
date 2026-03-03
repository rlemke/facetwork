"""Interpret handlers for the noaa-weather example."""

from __future__ import annotations

import json
import os
from typing import Any

from handlers.shared.weather_utils import generate_narrative_fallback

NAMESPACE = "weather.Interpret"


def handle_generate_narrative(params: dict[str, Any]) -> dict[str, Any]:
    """Handle GenerateNarrative event facet (prompt block fallback)."""
    station_name = params.get("station_name", "")
    year = params.get("year", 2023)
    daily_stats = params.get("daily_stats", [])
    geo_context = params.get("geo_context", {})
    if isinstance(year, str):
        year = int(year)
    if isinstance(daily_stats, str):
        daily_stats = json.loads(daily_stats)
    if isinstance(geo_context, str):
        geo_context = json.loads(geo_context)

    narrative, highlights = generate_narrative_fallback(
        station_name, year, daily_stats, geo_context
    )

    step_log = params.get("_step_log")
    if step_log is not None:
        msg = f"Narrative generated for {station_name} ({year}): {len(highlights)} highlights"
        if callable(step_log):
            step_log(msg, "success")
        else:
            step_log.append({"message": msg, "level": "success"})

    return {
        "narrative": narrative,
        "highlights": highlights,
    }


_DISPATCH: dict[str, Any] = {
    f"{NAMESPACE}.GenerateNarrative": handle_generate_narrative,
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


def register_interpret_handlers(poller) -> None:
    """Register with AgentPoller."""
    for facet_name, handler in _DISPATCH.items():
        poller.register(facet_name, handler)

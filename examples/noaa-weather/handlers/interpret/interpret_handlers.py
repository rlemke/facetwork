"""Interpret handlers for the noaa-weather example."""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from handlers.shared.weather_utils import generate_narrative_fallback

NAMESPACE = "weather.Interpret"
_log = logging.getLogger(__name__)

HAS_ANTHROPIC = False
try:
    import anthropic

    HAS_ANTHROPIC = bool(os.environ.get("ANTHROPIC_API_KEY"))
except ImportError:
    pass


def _generate_with_claude(
    station_name: str,
    year: int,
    daily_stats: list[dict[str, Any]],
    geo_context: dict[str, Any],
) -> tuple[str, list[dict[str, str]]]:
    """Generate narrative via Claude API, returning (narrative, highlights)."""
    # Summarize daily_stats into compact stats for the prompt
    total_days = len(daily_stats)
    temps = [d["temp_max"] for d in daily_stats if d.get("temp_max") is not None]
    temp_mins = [d["temp_min"] for d in daily_stats if d.get("temp_min") is not None]
    precips = [d.get("precip_total", 0) for d in daily_stats]
    rainy_days = sum(1 for p in precips if p > 0)
    precip_total = round(sum(precips), 1)

    temp_range = ""
    avg_high = avg_low = 0.0
    if temps and temp_mins:
        temp_range = f"{min(temp_mins):.1f}°C to {max(temps):.1f}°C"
        avg_high = round(sum(temps) / len(temps), 1)
        avg_low = round(sum(temp_mins) / len(temp_mins), 1)

    # Find extremes
    hottest = max(daily_stats, key=lambda d: d.get("temp_max") or -999) if daily_stats else {}
    coldest = min(daily_stats, key=lambda d: d.get("temp_min") or 999) if daily_stats else {}
    wettest = max(daily_stats, key=lambda d: d.get("precip_total") or 0) if daily_stats else {}

    location_str = ""
    if geo_context:
        parts = [
            geo_context.get("city", ""),
            geo_context.get("state", ""),
            geo_context.get("country", ""),
        ]
        location_str = ", ".join(p for p in parts if p)

    prompt_text = (
        f"Write a concise meteorologist-style annual weather summary for {station_name} ({year}).\n"
        f"Location: {location_str}\n"
        f"Stats: {total_days} days recorded, temp range {temp_range}, "
        f"avg high {avg_high}°C, avg low {avg_low}°C, "
        f"{rainy_days} rainy days, {precip_total}mm total precipitation.\n"
        f"Hottest day: {hottest.get('date', 'N/A')} at {hottest.get('temp_max', 'N/A')}°C\n"
        f"Coldest day: {coldest.get('date', 'N/A')} at {coldest.get('temp_min', 'N/A')}°C\n"
        f"Wettest day: {wettest.get('date', 'N/A')} with {wettest.get('precip_total', 0)}mm\n\n"
        'Respond in JSON: {"narrative": "...", "highlights": [{"type": "hottest", "date": "...", "value": "..."}, ...]}'
    )

    client = anthropic.Anthropic()
    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt_text}],
    )

    raw = response.content[0].text
    try:
        data = json.loads(raw)
        return data["narrative"], data.get("highlights", [])
    except (json.JSONDecodeError, KeyError):
        # Claude responded but not valid JSON — extract text as narrative,
        # derive highlights from data
        highlights = []
        if hottest:
            highlights.append(
                {
                    "type": "hottest",
                    "date": hottest.get("date", ""),
                    "value": f"{hottest.get('temp_max', '')}°C",
                }
            )
        if coldest:
            highlights.append(
                {
                    "type": "coldest",
                    "date": coldest.get("date", ""),
                    "value": f"{coldest.get('temp_min', '')}°C",
                }
            )
        if wettest and wettest.get("precip_total", 0) > 0:
            highlights.append(
                {
                    "type": "wettest",
                    "date": wettest.get("date", ""),
                    "value": f"{wettest.get('precip_total', 0)}mm",
                }
            )
        return raw, highlights


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

    if HAS_ANTHROPIC:
        try:
            narrative, highlights = _generate_with_claude(
                station_name, year, daily_stats, geo_context
            )
        except Exception:
            _log.debug("Claude narrative generation failed, using fallback", exc_info=True)
            narrative, highlights = generate_narrative_fallback(
                station_name, year, daily_stats, geo_context
            )
    else:
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

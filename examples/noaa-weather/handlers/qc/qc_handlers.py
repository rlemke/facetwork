"""QC handlers for the noaa-weather example."""

from __future__ import annotations

import json
import os
from typing import Any

from handlers.shared.weather_utils import compute_missing_pct, validate_temperature_range

NAMESPACE = "weather.QC"


def handle_validate_quality(params: dict[str, Any]) -> dict[str, Any]:
    """Handle ValidateQuality event facet (script block fallback)."""
    observations = params.get("observations", [])
    station_id = params.get("station_id", "")
    max_missing_pct = params.get("max_missing_pct", 20.0)
    if isinstance(observations, str):
        observations = json.loads(observations)
    if isinstance(max_missing_pct, str):
        max_missing_pct = float(max_missing_pct)

    missing_pct = compute_missing_pct(observations)
    temp_ok = validate_temperature_range(observations)
    total = len(observations)
    plausible = missing_pct <= max_missing_pct and temp_ok

    msg = (
        f"QC passed for {station_id}"
        if plausible
        else f"QC failed for {station_id}: missing={missing_pct:.1f}%, temp_ok={temp_ok}"
    )

    step_log = params.get("_step_log")
    if step_log is not None:
        level = "success" if plausible else "warning"
        if callable(step_log):
            step_log(msg, level)
        else:
            step_log.append({"message": msg, "level": level})

    return {
        "qc": {
            "plausible": plausible,
            "total_records": total,
            "missing_pct": round(missing_pct, 2),
            "temp_range_ok": temp_ok,
            "message": msg,
        },
    }


_DISPATCH: dict[str, Any] = {
    f"{NAMESPACE}.ValidateQuality": handle_validate_quality,
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


def register_qc_handlers(poller) -> None:
    """Register with AgentPoller."""
    for facet_name, handler in _DISPATCH.items():
        poller.register(facet_name, handler)

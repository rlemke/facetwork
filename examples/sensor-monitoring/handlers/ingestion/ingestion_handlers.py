"""Ingestion handlers -- IngestReading, ValidateReading."""

from __future__ import annotations

import json
import os
from typing import Any

from handlers.shared.sensor_utils import ingest_reading, validate_reading

NAMESPACE = "monitor.Ingestion"


def handle_ingest_reading(params: dict[str, Any]) -> dict[str, Any]:
    """Handle IngestReading event facet."""
    sensor_id = params.get("sensor_id", "unknown")
    value = float(params.get("value", 0.0))
    unit = params.get("unit", "celsius")
    last_reading = params.get("last_reading")
    if isinstance(last_reading, str):
        try:
            last_reading = json.loads(last_reading)
        except (json.JSONDecodeError, ValueError):
            last_reading = None
    # Treat "null" JSON string as None
    if last_reading == "null":
        last_reading = None

    reading, quality = ingest_reading(sensor_id, value, unit, last_reading)

    step_log = params.get("_step_log")
    if step_log:
        step_log.append({"message": f"Ingested {sensor_id}: quality={quality}", "level": "success"})

    return {"reading": reading, "quality": quality}


def handle_validate_reading(params: dict[str, Any]) -> dict[str, Any]:
    """Handle ValidateReading event facet."""
    reading = params.get("reading", {})
    if isinstance(reading, str):
        reading = json.loads(reading)
    sensor_config = params.get("sensor_config")
    if isinstance(sensor_config, str):
        try:
            sensor_config = json.loads(sensor_config)
        except (json.JSONDecodeError, ValueError):
            sensor_config = None

    valid, calibrated_value = validate_reading(reading, sensor_config)

    step_log = params.get("_step_log")
    if step_log:
        step_log.append({"message": f"Validated: valid={valid}, calibrated={calibrated_value}", "level": "success"})

    return {"valid": valid, "calibrated_value": calibrated_value}


_DISPATCH: dict[str, Any] = {
    f"{NAMESPACE}.IngestReading": handle_ingest_reading,
    f"{NAMESPACE}.ValidateReading": handle_validate_reading,
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


def register_ingestion_handlers(poller) -> None:
    """Register with AgentPoller."""
    for facet_name, handler in _DISPATCH.items():
        poller.register(facet_name, handler)

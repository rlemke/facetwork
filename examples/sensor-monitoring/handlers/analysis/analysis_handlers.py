"""Analysis handlers -- DetectAnomaly, ClassifyAlert."""

from __future__ import annotations

import json
import os
from typing import Any

from handlers.shared.sensor_utils import detect_anomaly, classify_alert

NAMESPACE = "monitor.Analysis"


def handle_detect_anomaly(params: dict[str, Any]) -> dict[str, Any]:
    """Handle DetectAnomaly event facet."""
    reading = params.get("reading", {})
    if isinstance(reading, str):
        reading = json.loads(reading)
    threshold_low = float(params.get("threshold_low", -10.0))
    threshold_high = float(params.get("threshold_high", 50.0))
    critical_low = float(params.get("critical_low", -40.0))
    critical_high = float(params.get("critical_high", 80.0))

    result = detect_anomaly(reading, threshold_low, threshold_high, critical_low, critical_high)

    step_log = params.get("_step_log")
    if step_log:
        step_log.append({
            "message": f"Anomaly check: severity={result['severity']}, breached={result['threshold_breached']}",
            "level": "success",
        })

    return {"result": result}


def handle_classify_alert(params: dict[str, Any]) -> dict[str, Any]:
    """Handle ClassifyAlert event facet."""
    anomaly = params.get("anomaly", {})
    if isinstance(anomaly, str):
        anomaly = json.loads(anomaly)
    sensor_id = params.get("sensor_id", "unknown")
    override_config = params.get("override_config")
    if isinstance(override_config, str):
        try:
            override_config = json.loads(override_config)
        except (json.JSONDecodeError, ValueError):
            override_config = None
    if override_config == "null":
        override_config = None

    alert = classify_alert(anomaly, sensor_id, override_config)

    step_log = params.get("_step_log")
    if step_log:
        step_log.append({
            "message": f"Alert classified: priority={alert['priority']}, channel={alert['channel']}",
            "level": "success",
        })

    return {"alert": alert}


_DISPATCH: dict[str, Any] = {
    f"{NAMESPACE}.DetectAnomaly": handle_detect_anomaly,
    f"{NAMESPACE}.ClassifyAlert": handle_classify_alert,
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


def register_analysis_handlers(poller) -> None:
    """Register with AgentPoller."""
    for facet_name, handler in _DISPATCH.items():
        poller.register(facet_name, handler)

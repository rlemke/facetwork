"""Reporting handlers -- RunDiagnostics, GenerateSummary."""

from __future__ import annotations

import json
import os
from typing import Any

from handlers.shared.sensor_utils import run_diagnostics, generate_summary

NAMESPACE = "monitor.Reporting"


def handle_run_diagnostics(params: dict[str, Any]) -> dict[str, Any]:
    """Handle RunDiagnostics event facet."""
    sensor_id = params.get("sensor_id", "unknown")
    anomaly_result = params.get("anomaly_result", {})
    if isinstance(anomaly_result, str):
        anomaly_result = json.loads(anomaly_result)
    reading = params.get("reading", {})
    if isinstance(reading, str):
        reading = json.loads(reading)

    report = run_diagnostics(sensor_id, anomaly_result, reading)

    step_log = params.get("_step_log")
    if step_log:
        step_log.append({
            "message": f"Diagnostics: {report['health_status']}, {report['anomalies_found']} anomalies in {report['readings_checked']} readings",
            "level": "success",
        })

    return {"report": report}


def handle_generate_summary(params: dict[str, Any]) -> dict[str, Any]:
    """Handle GenerateSummary event facet."""
    sensor_id = params.get("sensor_id", "unknown")
    diagnostic = params.get("diagnostic", {})
    if isinstance(diagnostic, str):
        diagnostic = json.loads(diagnostic)
    alert = params.get("alert", {})
    if isinstance(alert, str):
        alert = json.loads(alert)

    summary = generate_summary(sensor_id, diagnostic, alert)

    step_log = params.get("_step_log")
    if step_log:
        step_log.append({
            "message": f"Summary: {summary['report']}",
            "level": "success",
        })

    return {"summary": summary}


_DISPATCH: dict[str, Any] = {
    f"{NAMESPACE}.RunDiagnostics": handle_run_diagnostics,
    f"{NAMESPACE}.GenerateSummary": handle_generate_summary,
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


def register_reporting_handlers(poller) -> None:
    """Register with AgentPoller."""
    for facet_name, handler in _DISPATCH.items():
        poller.register(facet_name, handler)

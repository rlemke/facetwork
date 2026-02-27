"""Profiling handlers -- ProfileDataset, DetectAnomalies."""

from __future__ import annotations

import json
import os
from typing import Any

from handlers.shared.quality_utils import profile_dataset, detect_anomalies

NAMESPACE = "dq.Profiling"


def handle_profile_dataset(params: dict[str, Any]) -> dict[str, Any]:
    """Handle ProfileDataset event facet."""
    dataset = params.get("dataset", "unknown")
    columns = params.get("columns", ["col_a", "col_b", "col_c"])
    if isinstance(columns, str):
        columns = json.loads(columns)

    profiles, row_count = profile_dataset(dataset, columns)

    step_log = params.get("_step_log")
    if step_log:
        step_log.append({"message": f"Profiled '{dataset}': {len(profiles)} columns, {row_count} rows", "level": "success"})

    return {"profiles": profiles, "row_count": row_count}


def handle_detect_anomalies(params: dict[str, Any]) -> dict[str, Any]:
    """Handle DetectAnomalies event facet."""
    profiles = params.get("profiles", [])
    if isinstance(profiles, str):
        profiles = json.loads(profiles)
    row_count = int(params.get("row_count", 1000))
    missing_threshold = float(params.get("missing_threshold", 0.1))

    anomaly_count, flagged_columns = detect_anomalies(profiles, row_count, missing_threshold)

    step_log = params.get("_step_log")
    if step_log:
        step_log.append({"message": f"Detected {anomaly_count} anomalies", "level": "success"})

    return {"anomaly_count": anomaly_count, "flagged_columns": flagged_columns}


_DISPATCH: dict[str, Any] = {
    f"{NAMESPACE}.ProfileDataset": handle_profile_dataset,
    f"{NAMESPACE}.DetectAnomalies": handle_detect_anomalies,
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


def register_profiling_handlers(poller) -> None:
    """Register with AgentPoller."""
    for facet_name, handler in _DISPATCH.items():
        poller.register(facet_name, handler)

"""Validation handlers -- ValidateCompleteness, ValidateAccuracy."""

from __future__ import annotations

import json
import os
from typing import Any

from handlers.shared.quality_utils import validate_completeness, validate_accuracy

NAMESPACE = "dq.Validation"


def handle_validate_completeness(params: dict[str, Any]) -> dict[str, Any]:
    """Handle ValidateCompleteness event facet."""
    profiles = params.get("profiles", [])
    if isinstance(profiles, str):
        profiles = json.loads(profiles)
    row_count = int(params.get("row_count", 1000))
    missing_threshold = float(params.get("missing_threshold", 0.1))

    results, completeness_score = validate_completeness(profiles, row_count, missing_threshold)

    step_log = params.get("_step_log")
    if step_log:
        step_log.append({"message": f"Completeness: {completeness_score}", "level": "success"})

    return {"results": results, "completeness_score": completeness_score}


def handle_validate_accuracy(params: dict[str, Any]) -> dict[str, Any]:
    """Handle ValidateAccuracy event facet."""
    profiles = params.get("profiles", [])
    if isinstance(profiles, str):
        profiles = json.loads(profiles)
    type_error_max = int(params.get("type_error_max", 5))

    results, accuracy_score = validate_accuracy(profiles, type_error_max)

    step_log = params.get("_step_log")
    if step_log:
        step_log.append({"message": f"Accuracy: {accuracy_score}", "level": "success"})

    return {"results": results, "accuracy_score": accuracy_score}


_DISPATCH: dict[str, Any] = {
    f"{NAMESPACE}.ValidateCompleteness": handle_validate_completeness,
    f"{NAMESPACE}.ValidateAccuracy": handle_validate_accuracy,
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


def register_validation_handlers(poller) -> None:
    """Register with AgentPoller."""
    for facet_name, handler in _DISPATCH.items():
        poller.register(facet_name, handler)

"""Remediation handlers -- PlanRemediation, GenerateReport."""

from __future__ import annotations

import json
import os
from typing import Any

from handlers.shared.quality_utils import plan_remediation, generate_report

NAMESPACE = "dq.Remediation"


def handle_plan_remediation(params: dict[str, Any]) -> dict[str, Any]:
    """Handle PlanRemediation event facet."""
    results = params.get("results", [])
    if isinstance(results, str):
        results = json.loads(results)
    flagged_columns = params.get("flagged_columns", [])
    if isinstance(flagged_columns, str):
        flagged_columns = json.loads(flagged_columns)

    actions = plan_remediation(results, flagged_columns)

    step_log = params.get("_step_log")
    if step_log:
        step_log.append({"message": f"Planned {len(actions)} remediation actions", "level": "success"})

    return {"actions": actions}


def handle_generate_report(params: dict[str, Any]) -> dict[str, Any]:
    """Handle GenerateReport event facet."""
    dataset = params.get("dataset", "unknown")
    grade = params.get("grade", "F")
    passed = params.get("passed", False)
    if isinstance(passed, str):
        passed = passed.lower() in ("true", "1", "yes")
    overall = float(params.get("overall", 0.0))
    scores = params.get("scores", [])
    if isinstance(scores, str):
        scores = json.loads(scores)
    actions = params.get("actions", [])
    if isinstance(actions, str):
        actions = json.loads(actions)

    report = generate_report(dataset, grade, passed, overall, scores, actions)

    step_log = params.get("_step_log")
    if step_log:
        step_log.append({"message": f"Report: {report['summary']}", "level": "success"})

    return {"report": report}


_DISPATCH: dict[str, Any] = {
    f"{NAMESPACE}.PlanRemediation": handle_plan_remediation,
    f"{NAMESPACE}.GenerateReport": handle_generate_report,
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


def register_remediation_handlers(poller) -> None:
    """Register with AgentPoller."""
    for facet_name, handler in _DISPATCH.items():
        poller.register(facet_name, handler)

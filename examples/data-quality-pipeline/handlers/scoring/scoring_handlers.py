"""Scoring handlers -- ComputeScores, AssignGrade."""

from __future__ import annotations

import os
from typing import Any

from handlers.shared.quality_utils import assign_grade, compute_scores

NAMESPACE = "dataquality.Scoring"


def handle_compute_scores(params: dict[str, Any]) -> dict[str, Any]:
    """Handle ComputeScores event facet."""
    completeness_score = float(params.get("completeness_score", 0.0))
    accuracy_score = float(params.get("accuracy_score", 0.0))
    w_completeness = float(params.get("w_completeness", 0.4))
    w_accuracy = float(params.get("w_accuracy", 0.35))
    w_freshness = float(params.get("w_freshness", 0.25))

    scores, overall = compute_scores(
        completeness_score,
        accuracy_score,
        w_completeness,
        w_accuracy,
        w_freshness,
    )

    step_log = params.get("_step_log")
    if step_log:
        step_log.append({"message": f"Overall score: {overall}", "level": "success"})

    return {"scores": scores, "overall": overall}


def handle_assign_grade(params: dict[str, Any]) -> dict[str, Any]:
    """Handle AssignGrade event facet."""
    overall = float(params.get("overall", 0.0))
    min_score = float(params.get("min_score", 0.7))

    grade, passed = assign_grade(overall, min_score)

    step_log = params.get("_step_log")
    if step_log:
        step_log.append(
            {"message": f"Grade: {grade} ({'PASSED' if passed else 'FAILED'})", "level": "success"}
        )

    return {"grade": grade, "passed": passed}


_DISPATCH: dict[str, Any] = {
    f"{NAMESPACE}.ComputeScores": handle_compute_scores,
    f"{NAMESPACE}.AssignGrade": handle_assign_grade,
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


def register_scoring_handlers(poller) -> None:
    """Register with AgentPoller."""
    for facet_name, handler in _DISPATCH.items():
        poller.register(facet_name, handler)

"""Scoring handlers -- ScoreRound, EvaluateConvergence."""

from __future__ import annotations

import json
import os
from typing import Any

from handlers.shared.rounds_utils import evaluate_convergence, score_round

NAMESPACE = "multidebate.Scoring"


def handle_score_round(params: dict[str, Any]) -> dict[str, Any]:
    """Handle ScoreRound event facet."""
    arguments = params.get("arguments", [])
    if isinstance(arguments, str):
        arguments = json.loads(arguments)
    challenges = params.get("challenges", [])
    if isinstance(challenges, str):
        challenges = json.loads(challenges)
    prev_scores = params.get("prev_scores", [])
    if isinstance(prev_scores, str):
        prev_scores = json.loads(prev_scores)
    round_num = int(params.get("round_num", 1))

    scores = score_round(arguments, challenges, prev_scores, round_num)

    step_log = params.get("_step_log")
    if step_log:
        step_log.append(
            {"message": f"Scored round {round_num} for {len(scores)} agents", "level": "success"}
        )

    return {"scores": scores}


def handle_evaluate_convergence(params: dict[str, Any]) -> dict[str, Any]:
    """Handle EvaluateConvergence event facet."""
    current_scores = params.get("current_scores", [])
    if isinstance(current_scores, str):
        current_scores = json.loads(current_scores)
    prev_scores = params.get("prev_scores", [])
    if isinstance(prev_scores, str):
        prev_scores = json.loads(prev_scores)
    round_num = int(params.get("round_num", 1))

    metrics = evaluate_convergence(current_scores, prev_scores, round_num)

    step_log = params.get("_step_log")
    if step_log:
        step_log.append(
            {
                "message": f"Convergence at round {round_num}: delta={metrics['score_delta']}",
                "level": "success",
            }
        )

    return {"metrics": metrics}


_DISPATCH: dict[str, Any] = {
    f"{NAMESPACE}.ScoreRound": handle_score_round,
    f"{NAMESPACE}.EvaluateConvergence": handle_evaluate_convergence,
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

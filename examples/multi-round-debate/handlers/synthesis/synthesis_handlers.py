"""Synthesis handlers -- SummarizeRound, DeclareOutcome."""

from __future__ import annotations

import json
import os
from typing import Any

from handlers.shared.rounds_utils import declare_outcome, summarize_round

NAMESPACE = "multidebate.Synthesis"


def handle_summarize_round(params: dict[str, Any]) -> dict[str, Any]:
    """Handle SummarizeRound event facet."""
    arguments = params.get("arguments", [])
    if isinstance(arguments, str):
        arguments = json.loads(arguments)
    challenges = params.get("challenges", [])
    if isinstance(challenges, str):
        challenges = json.loads(challenges)
    scores = params.get("scores", [])
    if isinstance(scores, str):
        scores = json.loads(scores)
    round_num = int(params.get("round_num", 1))

    synthesis, key_shifts = summarize_round(arguments, challenges, scores, round_num)

    step_log = params.get("_step_log")
    if step_log:
        step_log.append({"message": f"Summarized round {round_num}", "level": "success"})

    return {
        "synthesis": synthesis,
        "key_shifts": key_shifts,
    }


def handle_declare_outcome(params: dict[str, Any]) -> dict[str, Any]:
    """Handle DeclareOutcome event facet."""
    round_syntheses = params.get("round_syntheses", [])
    if isinstance(round_syntheses, str):
        round_syntheses = json.loads(round_syntheses)
    final_scores = params.get("final_scores", [])
    if isinstance(final_scores, str):
        final_scores = json.loads(final_scores)
    convergence_trajectory = params.get("convergence_trajectory", [])
    if isinstance(convergence_trajectory, str):
        convergence_trajectory = json.loads(convergence_trajectory)
    topic = params.get("topic", "unknown")

    outcome = declare_outcome(round_syntheses, final_scores, convergence_trajectory, topic)

    step_log = params.get("_step_log")
    if step_log:
        step_log.append(
            {"message": f"Declared outcome: winner={outcome['winner']}", "level": "success"}
        )

    return {"outcome": outcome}


_DISPATCH: dict[str, Any] = {
    f"{NAMESPACE}.SummarizeRound": handle_summarize_round,
    f"{NAMESPACE}.DeclareOutcome": handle_declare_outcome,
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


def register_synthesis_handlers(poller) -> None:
    """Register with AgentPoller."""
    for facet_name, handler in _DISPATCH.items():
        poller.register(facet_name, handler)

"""Evaluation handlers — ScoreArguments, JudgeDebate."""

from __future__ import annotations

import json
import os
from typing import Any

from handlers.shared.debate_utils import score_arguments, judge_debate

NAMESPACE = "debate.Evaluation"


def handle_score_arguments(params: dict[str, Any]) -> dict[str, Any]:
    """Handle ScoreArguments event facet."""
    arguments = params.get("arguments", [])
    if isinstance(arguments, str):
        arguments = json.loads(arguments)
    rebuttals = params.get("rebuttals", [])
    if isinstance(rebuttals, str):
        rebuttals = json.loads(rebuttals)

    scores = score_arguments(arguments, rebuttals)

    step_log = params.get("_step_log")
    if step_log:
        step_log.append({"message": f"Scored {len(arguments)} arguments with {len(rebuttals)} rebuttals", "level": "success"})

    return {"scores": scores}


def handle_judge_debate(params: dict[str, Any]) -> dict[str, Any]:
    """Handle JudgeDebate event facet."""
    topic = params.get("topic", "unknown")
    synthesis = params.get("synthesis", "")
    if isinstance(synthesis, str) and synthesis.startswith("{"):
        synthesis = json.loads(synthesis)
        synthesis = synthesis.get("synthesis", str(synthesis))
    scores = params.get("scores", [])
    if isinstance(scores, str):
        scores = json.loads(scores)

    verdict = judge_debate(topic, synthesis, scores)

    step_log = params.get("_step_log")
    if step_log:
        step_log.append({"message": f"Judged debate on '{topic}': winner is '{verdict['winner']}'", "level": "success"})

    return {"verdict": verdict}


_DISPATCH: dict[str, Any] = {
    f"{NAMESPACE}.ScoreArguments": handle_score_arguments,
    f"{NAMESPACE}.JudgeDebate": handle_judge_debate,
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


def register_evaluation_handlers(poller) -> None:
    """Register with AgentPoller."""
    for facet_name, handler in _DISPATCH.items():
        poller.register(facet_name, handler)

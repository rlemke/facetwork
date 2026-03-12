"""Argumentation handlers -- RefineArgument, ChallengeArgument."""

from __future__ import annotations

import json
import os
from typing import Any

from handlers.shared.rounds_utils import challenge_argument, refine_argument

NAMESPACE = "multidebate.Argumentation"


def handle_refine_argument(params: dict[str, Any]) -> dict[str, Any]:
    """Handle RefineArgument event facet."""
    agent = params.get("agent", "agent_0")
    topic = params.get("topic", "unknown")
    stance = params.get("stance", "neutral")
    round_num = int(params.get("round_num", 1))
    prev_synthesis = params.get("prev_synthesis", "")

    result = refine_argument(agent, topic, stance, round_num, prev_synthesis)

    step_log = params.get("_step_log")
    if step_log:
        step_log.append(
            {
                "message": f"{agent} refined {stance} argument (round {round_num})",
                "level": "success",
            }
        )

    return {"refined": result}


def handle_challenge_argument(params: dict[str, Any]) -> dict[str, Any]:
    """Handle ChallengeArgument event facet."""
    agent = params.get("agent", "agent_0")
    target_agent = params.get("target_agent", "agent_1")
    target_argument = params.get("target_argument", "")
    if isinstance(target_argument, str):
        try:
            target_argument = json.loads(target_argument)
        except (json.JSONDecodeError, ValueError):
            pass
    round_num = int(params.get("round_num", 1))

    result = challenge_argument(agent, target_agent, target_argument, round_num)

    step_log = params.get("_step_log")
    if step_log:
        step_log.append(
            {
                "message": f"{agent} challenged {target_agent} (round {round_num})",
                "level": "success",
            }
        )

    return {
        "challenge": result["challenge"],
        "weaknesses": result["weaknesses"],
    }


_DISPATCH: dict[str, Any] = {
    f"{NAMESPACE}.RefineArgument": handle_refine_argument,
    f"{NAMESPACE}.ChallengeArgument": handle_challenge_argument,
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


def register_argumentation_handlers(poller) -> None:
    """Register with AgentPoller."""
    for facet_name, handler in _DISPATCH.items():
        poller.register(facet_name, handler)

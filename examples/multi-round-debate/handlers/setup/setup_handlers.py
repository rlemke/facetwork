"""Setup handlers -- InitiateRound, AssignPositions."""

from __future__ import annotations

import json
import os
from typing import Any

from handlers.shared.rounds_utils import assign_positions, initiate_round

NAMESPACE = "multidebate.Setup"


def handle_initiate_round(params: dict[str, Any]) -> dict[str, Any]:
    """Handle InitiateRound event facet."""
    topic = params.get("topic", "unknown")
    round_num = int(params.get("round_num", 1))
    num_agents = int(params.get("num_agents", 3))
    prev_synthesis = params.get("prev_synthesis", "")
    prev_scores = params.get("prev_scores", [])
    if isinstance(prev_scores, str):
        prev_scores = json.loads(prev_scores)

    result = initiate_round(topic, round_num, num_agents, prev_synthesis, prev_scores)

    step_log = params.get("_step_log")
    if step_log:
        step_log.append(
            {"message": f"Initiated round {round_num} on '{topic}'", "level": "success"}
        )

    return {"round_state": result}


def handle_assign_positions(params: dict[str, Any]) -> dict[str, Any]:
    """Handle AssignPositions event facet."""
    round_state = params.get("round_state", {})
    if isinstance(round_state, str):
        round_state = json.loads(round_state)
    round_num = int(params.get("round_num", 1))

    assignments = assign_positions(round_state, round_num)

    step_log = params.get("_step_log")
    if step_log:
        step_log.append(
            {
                "message": f"Assigned {len(assignments)} positions for round {round_num}",
                "level": "success",
            }
        )

    return {"assignments": assignments}


_DISPATCH: dict[str, Any] = {
    f"{NAMESPACE}.InitiateRound": handle_initiate_round,
    f"{NAMESPACE}.AssignPositions": handle_assign_positions,
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


def register_setup_handlers(poller) -> None:
    """Register with AgentPoller."""
    for facet_name, handler in _DISPATCH.items():
        poller.register(facet_name, handler)

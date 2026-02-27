"""Framing handlers — FrameDebate, AssignRoles."""

from __future__ import annotations

import json
import os
from typing import Any

from handlers.shared.debate_utils import frame_debate, assign_roles

NAMESPACE = "debate.Framing"


def handle_frame_debate(params: dict[str, Any]) -> dict[str, Any]:
    """Handle FrameDebate event facet."""
    topic = params.get("topic", "unknown")
    num_agents = int(params.get("num_agents", 3))

    result = frame_debate(topic, num_agents)

    step_log = params.get("_step_log")
    if step_log:
        step_log.append({"message": f"Framed debate on '{topic}' with {num_agents} agents", "level": "success"})

    return {
        "topic_analysis": result["topic_analysis"],
        "positions": result["positions"],
        "stakes": result["stakes"],
    }


def handle_assign_roles(params: dict[str, Any]) -> dict[str, Any]:
    """Handle AssignRoles event facet."""
    topic_analysis = params.get("topic_analysis", "")
    positions = params.get("positions", [])
    if isinstance(positions, str):
        positions = json.loads(positions)

    assignments = assign_roles(topic_analysis, positions)

    step_log = params.get("_step_log")
    if step_log:
        step_log.append({"message": f"Assigned {len(assignments)} debate roles", "level": "success"})

    return {"assignments": assignments}


_DISPATCH: dict[str, Any] = {
    f"{NAMESPACE}.FrameDebate": handle_frame_debate,
    f"{NAMESPACE}.AssignRoles": handle_assign_roles,
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


def register_framing_handlers(poller) -> None:
    """Register with AgentPoller."""
    for facet_name, handler in _DISPATCH.items():
        poller.register(facet_name, handler)

"""Planning handlers — PlanResearch, DecomposeIntoSubtopics."""

from __future__ import annotations

import json
import os
from typing import Any

from handlers.shared.research_utils import plan_topic, decompose_topic

NAMESPACE = "research.Planning"


def handle_plan_research(params: dict[str, Any]) -> dict[str, Any]:
    """Handle PlanResearch event facet."""
    topic = params.get("topic", "unknown")
    depth = int(params.get("depth", 3))
    max_subtopics = int(params.get("max_subtopics", 5))

    plan = plan_topic(topic, depth, max_subtopics)

    step_log = params.get("_step_log")
    if step_log:
        step_log.append({"message": f"Planned research for '{topic}' at depth {depth}", "level": "success"})

    return {"plan": plan}


def handle_decompose_into_subtopics(params: dict[str, Any]) -> dict[str, Any]:
    """Handle DecomposeIntoSubtopics event facet."""
    topic = params.get("topic", {})
    if isinstance(topic, str):
        topic = json.loads(topic)
    max_subtopics = int(params.get("max_subtopics", 5))

    subtopics = decompose_topic(topic, max_subtopics)

    step_log = params.get("_step_log")
    if step_log:
        name = topic.get("name", "unknown") if isinstance(topic, dict) else str(topic)
        step_log.append({"message": f"Decomposed '{name}' into {len(subtopics)} subtopics", "level": "success"})

    return {"subtopics": subtopics}


_DISPATCH: dict[str, Any] = {
    f"{NAMESPACE}.PlanResearch": handle_plan_research,
    f"{NAMESPACE}.DecomposeIntoSubtopics": handle_decompose_into_subtopics,
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


def register_planning_handlers(poller) -> None:
    """Register with AgentPoller."""
    for facet_name, handler in _DISPATCH.items():
        poller.register(facet_name, handler)

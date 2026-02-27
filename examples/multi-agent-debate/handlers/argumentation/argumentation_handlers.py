"""Argumentation handlers — GenerateArgument, GenerateRebuttal."""

from __future__ import annotations

import json
import os
from typing import Any

from handlers.shared.debate_utils import generate_argument, generate_rebuttal

NAMESPACE = "debate.Argumentation"


def handle_generate_argument(params: dict[str, Any]) -> dict[str, Any]:
    """Handle GenerateArgument event facet."""
    role = params.get("role", {})
    if isinstance(role, str):
        role = json.loads(role)
    topic = params.get("topic", "unknown")
    context = params.get("context", "")

    argument = generate_argument(role, topic, context)

    step_log = params.get("_step_log")
    if step_log:
        persona = role.get("persona", "unknown") if isinstance(role, dict) else "unknown"
        step_log.append({"message": f"Generated argument by '{persona}' on '{topic}'", "level": "success"})

    return {"argument": argument}


def handle_generate_rebuttal(params: dict[str, Any]) -> dict[str, Any]:
    """Handle GenerateRebuttal event facet."""
    role = params.get("role", {})
    if isinstance(role, str):
        role = json.loads(role)
    arguments = params.get("arguments", [])
    if isinstance(arguments, str):
        arguments = json.loads(arguments)

    rebuttal = generate_rebuttal(role, arguments)

    step_log = params.get("_step_log")
    if step_log:
        persona = role.get("persona", "unknown") if isinstance(role, dict) else "unknown"
        step_log.append({"message": f"Generated rebuttal by '{persona}' against {len(arguments)} arguments", "level": "success"})

    return {"rebuttal": rebuttal}


_DISPATCH: dict[str, Any] = {
    f"{NAMESPACE}.GenerateArgument": handle_generate_argument,
    f"{NAMESPACE}.GenerateRebuttal": handle_generate_rebuttal,
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

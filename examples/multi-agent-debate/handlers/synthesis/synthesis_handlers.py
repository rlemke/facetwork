"""Synthesis handlers — SynthesizePositions, BuildConsensus."""

from __future__ import annotations

import json
import os
from typing import Any

from handlers.shared.debate_utils import synthesize_positions, build_consensus

NAMESPACE = "debate.Synthesis"


def handle_synthesize_positions(params: dict[str, Any]) -> dict[str, Any]:
    """Handle SynthesizePositions event facet."""
    arguments = params.get("arguments", [])
    if isinstance(arguments, str):
        arguments = json.loads(arguments)
    rebuttals = params.get("rebuttals", [])
    if isinstance(rebuttals, str):
        rebuttals = json.loads(rebuttals)
    scores = params.get("scores", [])
    if isinstance(scores, str):
        scores = json.loads(scores)

    synthesis, themes = synthesize_positions(arguments, rebuttals, scores)

    step_log = params.get("_step_log")
    if step_log:
        step_log.append({"message": f"Synthesized {len(arguments)} arguments into {len(themes)} themes", "level": "success"})

    return {"synthesis": synthesis, "themes": themes}


def handle_build_consensus(params: dict[str, Any]) -> dict[str, Any]:
    """Handle BuildConsensus event facet."""
    verdict = params.get("verdict", {})
    if isinstance(verdict, str):
        verdict = json.loads(verdict)
    synthesis = params.get("synthesis", "")
    themes = params.get("themes", [])
    if isinstance(themes, str):
        themes = json.loads(themes)

    consensus = build_consensus(verdict, synthesis, themes)

    step_log = params.get("_step_log")
    if step_log:
        step_log.append({"message": f"Built consensus: agreement level {consensus['agreement_level']}", "level": "success"})

    return {"consensus": consensus}


_DISPATCH: dict[str, Any] = {
    f"{NAMESPACE}.SynthesizePositions": handle_synthesize_positions,
    f"{NAMESPACE}.BuildConsensus": handle_build_consensus,
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

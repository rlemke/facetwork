"""Analysis handlers — SynthesizeFindings, IdentifyGaps."""

from __future__ import annotations

import json
import os
from typing import Any

from handlers.shared.research_utils import synthesize_findings, identify_gaps

NAMESPACE = "research.Analysis"


def handle_synthesize_findings(params: dict[str, Any]) -> dict[str, Any]:
    """Handle SynthesizeFindings event facet."""
    topic = params.get("topic", {})
    if isinstance(topic, str):
        topic = json.loads(topic)
    all_findings = params.get("all_findings", [])
    if isinstance(all_findings, str):
        all_findings = json.loads(all_findings)

    analysis = synthesize_findings(topic, all_findings)

    step_log = params.get("_step_log")
    if step_log:
        name = topic.get("name", "unknown") if isinstance(topic, dict) else str(topic)
        step_log.append({"message": f"Synthesized findings for '{name}': {len(analysis['themes'])} themes", "level": "success"})

    return {"analysis": analysis}


def handle_identify_gaps(params: dict[str, Any]) -> dict[str, Any]:
    """Handle IdentifyGaps event facet."""
    analysis = params.get("analysis", {})
    if isinstance(analysis, str):
        analysis = json.loads(analysis)
    topic = params.get("topic", {})
    if isinstance(topic, str):
        topic = json.loads(topic)

    gaps, recommendations = identify_gaps(analysis, topic)

    step_log = params.get("_step_log")
    if step_log:
        name = topic.get("name", "unknown") if isinstance(topic, dict) else str(topic)
        step_log.append({"message": f"Identified {len(gaps)} gaps for '{name}'", "level": "success"})

    return {"gaps": gaps, "recommendations": recommendations}


_DISPATCH: dict[str, Any] = {
    f"{NAMESPACE}.SynthesizeFindings": handle_synthesize_findings,
    f"{NAMESPACE}.IdentifyGaps": handle_identify_gaps,
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


def register_analysis_handlers(poller) -> None:
    """Register with AgentPoller."""
    for facet_name, handler in _DISPATCH.items():
        poller.register(facet_name, handler)

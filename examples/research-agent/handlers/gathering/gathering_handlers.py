"""Gathering handlers — GatherSources, ExtractFindings."""

from __future__ import annotations

import json
import os
from typing import Any

from handlers.shared.research_utils import gather_sources, extract_findings

NAMESPACE = "research.Gathering"


def handle_gather_sources(params: dict[str, Any]) -> dict[str, Any]:
    """Handle GatherSources event facet."""
    subtopic = params.get("subtopic", {})
    if isinstance(subtopic, str):
        subtopic = json.loads(subtopic)
    max_sources = int(params.get("max_sources", 5))

    sources = gather_sources(subtopic, max_sources)

    step_log = params.get("_step_log")
    if step_log:
        name = subtopic.get("name", "unknown") if isinstance(subtopic, dict) else str(subtopic)
        step_log.append({"message": f"Gathered {len(sources)} sources for '{name}'", "level": "success"})

    return {"sources": sources}


def handle_extract_findings(params: dict[str, Any]) -> dict[str, Any]:
    """Handle ExtractFindings event facet."""
    subtopic = params.get("subtopic", {})
    if isinstance(subtopic, str):
        subtopic = json.loads(subtopic)
    sources = params.get("sources", [])
    if isinstance(sources, str):
        sources = json.loads(sources)

    findings = extract_findings(subtopic, sources)

    step_log = params.get("_step_log")
    if step_log:
        name = subtopic.get("name", "unknown") if isinstance(subtopic, dict) else str(subtopic)
        step_log.append({"message": f"Extracted {len(findings)} findings for '{name}'", "level": "success"})

    return {"findings": findings}


_DISPATCH: dict[str, Any] = {
    f"{NAMESPACE}.GatherSources": handle_gather_sources,
    f"{NAMESPACE}.ExtractFindings": handle_extract_findings,
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


def register_gathering_handlers(poller) -> None:
    """Register with AgentPoller."""
    for facet_name, handler in _DISPATCH.items():
        poller.register(facet_name, handler)

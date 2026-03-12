"""Research handlers -- SearchMarketTrends, GatherRegulations, AnalyzeCompetitors."""

from __future__ import annotations

import os
from typing import Any

from handlers.shared.debate_utils import (
    analyze_competitors,
    gather_regulations,
    search_market_trends,
)

NAMESPACE = "siteselection.Research"


def handle_search_market_trends(params: dict[str, Any]) -> dict[str, Any]:
    """Handle SearchMarketTrends event facet."""
    candidate_id = params.get("candidate_id", "unknown")
    market_area = params.get("market_area", "default")

    result = search_market_trends(candidate_id, market_area)

    step_log = params.get("_step_log")
    if step_log:
        step_log.append(
            {
                "message": f"Searched market trends for '{candidate_id}': growth={result['growth_rate']}",
                "level": "success",
            }
        )

    return {"research": result}


def handle_gather_regulations(params: dict[str, Any]) -> dict[str, Any]:
    """Handle GatherRegulations event facet."""
    candidate_id = params.get("candidate_id", "unknown")
    jurisdiction = params.get("jurisdiction", "default")

    result = gather_regulations(candidate_id, jurisdiction)

    step_log = params.get("_step_log")
    if step_log:
        step_log.append(
            {
                "message": f"Gathered regulations for '{candidate_id}': permit_difficulty={result['permit_difficulty']}",
                "level": "success",
            }
        )

    return {"regulations": result}


def handle_analyze_competitors(params: dict[str, Any]) -> dict[str, Any]:
    """Handle AnalyzeCompetitors event facet."""
    candidate_id = params.get("candidate_id", "unknown")
    radius_km = float(params.get("radius_km", 5.0))

    competitors, threat_level = analyze_competitors(candidate_id, radius_km)

    step_log = params.get("_step_log")
    if step_log:
        step_log.append(
            {
                "message": f"Analyzed competitors for '{candidate_id}': {len(competitors)} found, threat={threat_level}",
                "level": "success",
            }
        )

    return {"competitors": competitors, "threat_level": threat_level}


_DISPATCH: dict[str, Any] = {
    f"{NAMESPACE}.SearchMarketTrends": handle_search_market_trends,
    f"{NAMESPACE}.GatherRegulations": handle_gather_regulations,
    f"{NAMESPACE}.AnalyzeCompetitors": handle_analyze_competitors,
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


def register_research_handlers(poller) -> None:
    """Register with AgentPoller."""
    for facet_name, handler in _DISPATCH.items():
        poller.register(facet_name, handler)

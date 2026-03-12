"""Spatial handlers -- ScoreCandidate, RankCandidates, ComputeAccessibility."""

from __future__ import annotations

import json
import os
from typing import Any

from handlers.shared.debate_utils import compute_accessibility, rank_candidates, score_candidate

NAMESPACE = "siteselection.Spatial"


def handle_score_candidate(params: dict[str, Any]) -> dict[str, Any]:
    """Handle ScoreCandidate event facet."""
    candidate_id = params.get("candidate_id", "unknown")
    demographics = params.get("demographics", {})
    if isinstance(demographics, str):
        demographics = json.loads(demographics)
    competition = params.get("competition", {})
    if isinstance(competition, str):
        competition = json.loads(competition)
    weights = params.get("weights")
    if isinstance(weights, str):
        weights = json.loads(weights)
    penalty = float(params.get("penalty", 0.0))

    result = score_candidate(candidate_id, demographics, competition, weights, penalty)

    step_log = params.get("_step_log")
    if step_log:
        step_log.append(
            {
                "message": f"Scored candidate '{candidate_id}': {result['overall_score']}",
                "level": "success",
            }
        )

    return {"score": result}


def handle_rank_candidates(params: dict[str, Any]) -> dict[str, Any]:
    """Handle RankCandidates event facet."""
    scores = params.get("scores", [])
    if isinstance(scores, str):
        scores = json.loads(scores)
    top_n = int(params.get("top_n", 5))
    weights = params.get("weights")
    if isinstance(weights, str) and weights != "null":
        weights = json.loads(weights)
    elif weights == "null":
        weights = None

    ranked, top = rank_candidates(scores, top_n, weights)

    step_log = params.get("_step_log")
    if step_log:
        step_log.append(
            {"message": f"Ranked {len(ranked)} candidates, top: {top}", "level": "success"}
        )

    return {"ranked": ranked, "top_candidate": top}


def handle_compute_accessibility(params: dict[str, Any]) -> dict[str, Any]:
    """Handle ComputeAccessibility event facet."""
    candidate_id = params.get("candidate_id", "unknown")
    location = params.get("location", {})
    if isinstance(location, str):
        location = json.loads(location)

    result = compute_accessibility(candidate_id, location)

    step_log = params.get("_step_log")
    if step_log:
        step_log.append(
            {
                "message": f"Computed accessibility for '{candidate_id}': walk_score={result['walk_score']}",
                "level": "success",
            }
        )

    return {"metrics": result}


_DISPATCH: dict[str, Any] = {
    f"{NAMESPACE}.ScoreCandidate": handle_score_candidate,
    f"{NAMESPACE}.RankCandidates": handle_rank_candidates,
    f"{NAMESPACE}.ComputeAccessibility": handle_compute_accessibility,
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


def register_spatial_handlers(poller) -> None:
    """Register with AgentPoller."""
    for facet_name, handler in _DISPATCH.items():
        poller.register(facet_name, handler)

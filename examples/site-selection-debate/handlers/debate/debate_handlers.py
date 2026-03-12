"""Debate handlers -- PresentAnalysis, ChallengePosition, ScoreArguments."""

from __future__ import annotations

import json
import os
from typing import Any

from handlers.shared.debate_utils import challenge_position, present_analysis, score_arguments

NAMESPACE = "siteselection.Debate"


def handle_present_analysis(params: dict[str, Any]) -> dict[str, Any]:
    """Handle PresentAnalysis event facet."""
    agent_role = params.get("agent_role", "financial_analyst")
    candidate_id = params.get("candidate_id", "unknown")
    spatial_score = params.get("spatial_score", {})
    if isinstance(spatial_score, str):
        spatial_score = json.loads(spatial_score)
    market_data = params.get("market_data", {})
    if isinstance(market_data, str):
        market_data = json.loads(market_data)
    round_num = int(params.get("round_num", 1))

    result = present_analysis(agent_role, candidate_id, spatial_score, market_data, round_num)

    step_log = params.get("_step_log")
    if step_log:
        step_log.append(
            {
                "message": f"{agent_role} presented analysis for '{candidate_id}' (round {round_num})",
                "level": "success",
            }
        )

    return {"position": result}


def handle_challenge_position(params: dict[str, Any]) -> dict[str, Any]:
    """Handle ChallengePosition event facet."""
    agent_role = params.get("agent_role", "financial_analyst")
    target_role = params.get("target_role", "community_analyst")
    target_position = params.get("target_position", "")
    if isinstance(target_position, str) and target_position.startswith("{"):
        target_position = json.loads(target_position)
    round_num = int(params.get("round_num", 1))

    rebuttal, weaknesses = challenge_position(agent_role, target_role, target_position, round_num)

    step_log = params.get("_step_log")
    if step_log:
        step_log.append(
            {
                "message": f"{agent_role} challenged {target_role} ({len(weaknesses)} weaknesses)",
                "level": "success",
            }
        )

    return {"rebuttal": rebuttal, "weaknesses": weaknesses}


def handle_score_arguments(params: dict[str, Any]) -> dict[str, Any]:
    """Handle ScoreArguments event facet."""
    positions = params.get("positions", [])
    if isinstance(positions, str):
        positions = json.loads(positions)
    challenges = params.get("challenges", [])
    if isinstance(challenges, str):
        challenges = json.loads(challenges)
    prev_rankings = params.get("prev_rankings")
    if isinstance(prev_rankings, str) and prev_rankings != "null":
        prev_rankings = json.loads(prev_rankings)
    elif prev_rankings == "null" or prev_rankings is None:
        prev_rankings = None
    round_num = int(params.get("round_num", 1))

    result = score_arguments(positions, challenges, prev_rankings, round_num)

    step_log = params.get("_step_log")
    if step_log:
        step_log.append(
            {
                "message": f"Scored arguments round {round_num}: consensus={result['consensus_level']}",
                "level": "success",
            }
        )

    return {"rankings": result}


_DISPATCH: dict[str, Any] = {
    f"{NAMESPACE}.PresentAnalysis": handle_present_analysis,
    f"{NAMESPACE}.ChallengePosition": handle_challenge_position,
    f"{NAMESPACE}.ScoreArguments": handle_score_arguments,
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


def register_debate_handlers(poller) -> None:
    """Register with AgentPoller."""
    for facet_name, handler in _DISPATCH.items():
        poller.register(facet_name, handler)

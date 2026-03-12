"""Synthesis handlers -- SummarizeRound, ProduceRanking, GenerateReport."""

from __future__ import annotations

import json
import os
from typing import Any

from handlers.shared.debate_utils import generate_report, produce_ranking, summarize_round

NAMESPACE = "siteselection.Synthesis"


def handle_summarize_round(params: dict[str, Any]) -> dict[str, Any]:
    """Handle SummarizeRound event facet."""
    positions = params.get("positions", [])
    if isinstance(positions, str):
        positions = json.loads(positions)
    challenges = params.get("challenges", [])
    if isinstance(challenges, str):
        challenges = json.loads(challenges)
    rankings = params.get("rankings")
    if isinstance(rankings, str) and rankings != "null":
        rankings = json.loads(rankings)
    elif rankings == "null" or rankings is None:
        rankings = None
    round_num = int(params.get("round_num", 1))

    synthesis, key_arguments = summarize_round(positions, challenges, rankings, round_num)

    step_log = params.get("_step_log")
    if step_log:
        step_log.append(
            {
                "message": f"Summarized round {round_num}: {len(key_arguments)} key arguments",
                "level": "success",
            }
        )

    return {"synthesis": synthesis, "key_arguments": key_arguments}


def handle_produce_ranking(params: dict[str, Any]) -> dict[str, Any]:
    """Handle ProduceRanking event facet."""
    round_syntheses = params.get("round_syntheses", [])
    if isinstance(round_syntheses, str):
        round_syntheses = json.loads(round_syntheses)
    final_rankings = params.get("final_rankings")
    if isinstance(final_rankings, str) and final_rankings != "null":
        final_rankings = json.loads(final_rankings)
    elif final_rankings == "null" or final_rankings is None:
        final_rankings = None
    candidate_scores = params.get("candidate_scores", [])
    if isinstance(candidate_scores, str):
        candidate_scores = json.loads(candidate_scores)

    ranked, top, confidence = produce_ranking(round_syntheses, final_rankings, candidate_scores)

    step_log = params.get("_step_log")
    if step_log:
        step_log.append(
            {"message": f"Produced ranking: top={top}, confidence={confidence}", "level": "success"}
        )

    return {"ranked": ranked, "top_candidate": top, "confidence": confidence}


def handle_generate_report(params: dict[str, Any]) -> dict[str, Any]:
    """Handle GenerateReport event facet."""
    top_candidate = params.get("top_candidate", "unknown")
    ranked_candidates = params.get("ranked_candidates", [])
    if isinstance(ranked_candidates, str):
        ranked_candidates = json.loads(ranked_candidates)
    round_syntheses = params.get("round_syntheses", [])
    if isinstance(round_syntheses, str):
        round_syntheses = json.loads(round_syntheses)
    confidence = float(params.get("confidence", 0.8))

    result = generate_report(top_candidate, ranked_candidates, round_syntheses, confidence)

    step_log = params.get("_step_log")
    if step_log:
        step_log.append({"message": f"Generated report for '{top_candidate}'", "level": "success"})

    return {"report": result}


_DISPATCH: dict[str, Any] = {
    f"{NAMESPACE}.SummarizeRound": handle_summarize_round,
    f"{NAMESPACE}.ProduceRanking": handle_produce_ranking,
    f"{NAMESPACE}.GenerateReport": handle_generate_report,
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

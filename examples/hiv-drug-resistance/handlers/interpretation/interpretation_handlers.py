"""Interpretation handlers for the hiv-drug-resistance example."""

from __future__ import annotations

import json
import os
from typing import Any

from handlers.shared.resistance_utils import interpret_results, score_resistance

NAMESPACE = "hiv.Interpretation"


def handle_score_resistance(params: dict[str, Any]) -> dict[str, Any]:
    """Handle ScoreResistance event facet."""
    sample_id = params.get("sample_id", "")
    mutations_raw = params.get("mutations", [])
    if isinstance(mutations_raw, str):
        mutations_raw = json.loads(mutations_raw)

    drug_scores, total_drugs, highest_level = score_resistance(
        sample_id,
        mutations_raw,
    )

    step_log = params.get("_step_log")
    if step_log is not None:
        msg = f"Scored {total_drugs} drugs for {sample_id}: highest={highest_level}"
        if callable(step_log):
            step_log(msg, "info")
        else:
            step_log.append({"message": msg, "level": "info"})

    return {
        "drug_scores": drug_scores,
        "total_drugs_scored": total_drugs,
        "highest_level": highest_level,
    }


def handle_interpret_results(params: dict[str, Any]) -> dict[str, Any]:
    """Handle InterpretResults event facet."""
    sample_id = params.get("sample_id", "")
    drug_scores_raw = params.get("drug_scores", [])
    clinical_context = params.get("clinical_context", "")
    if isinstance(drug_scores_raw, str):
        drug_scores_raw = json.loads(drug_scores_raw)

    result = interpret_results(sample_id, drug_scores_raw, clinical_context)

    step_log = params.get("_step_log")
    if step_log is not None:
        msg = f"Interpretation for {sample_id}: {result['resistance_level']}"
        if callable(step_log):
            step_log(msg, "success")
        else:
            step_log.append({"message": msg, "level": "success"})

    return result


_DISPATCH: dict[str, Any] = {
    f"{NAMESPACE}.ScoreResistance": handle_score_resistance,
    f"{NAMESPACE}.InterpretResults": handle_interpret_results,
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


def register_interpretation_handlers(poller) -> None:
    """Register with AgentPoller."""
    for facet_name, handler in _DISPATCH.items():
        poller.register(facet_name, handler)

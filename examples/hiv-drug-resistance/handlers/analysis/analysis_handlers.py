"""Analysis handlers for the hiv-drug-resistance example."""

from __future__ import annotations

import json
import os
from typing import Any

from handlers.shared.resistance_utils import (
    call_variants,
    classify_mutations,
    generate_consensus,
)

NAMESPACE = "hiv.Analysis"


def handle_call_variants(params: dict[str, Any]) -> dict[str, Any]:
    """Handle CallVariants event facet."""
    sample_id = params.get("sample_id", "")
    bam_path = params.get("bam_path", "")
    min_frequency = params.get("min_frequency", 0.01)
    min_depth = params.get("min_depth", 100)
    if isinstance(min_frequency, str):
        min_frequency = float(min_frequency)
    if isinstance(min_depth, str):
        min_depth = int(min_depth)

    variants, stats = call_variants(sample_id, bam_path, min_frequency, min_depth)

    step_log = params.get("_step_log")
    if step_log is not None:
        msg = (
            f"Called {stats['total_variants']} variants ({stats['drm_count']} DRMs) for {sample_id}"
        )
        if callable(step_log):
            step_log(msg, "info")
        else:
            step_log.append({"message": msg, "level": "info"})

    return {
        "variants": variants,
        "total_variants": stats["total_variants"],
        "drm_count": stats["drm_count"],
    }


def handle_generate_consensus(params: dict[str, Any]) -> dict[str, Any]:
    """Handle GenerateConsensus event facet."""
    sample_id = params.get("sample_id", "")
    bam_path = params.get("bam_path", "")
    coverage_threshold = params.get("coverage_threshold", 50)
    if isinstance(coverage_threshold, str):
        coverage_threshold = int(coverage_threshold)

    result = generate_consensus(sample_id, bam_path, coverage_threshold)

    step_log = params.get("_step_log")
    if step_log is not None:
        msg = f"Consensus for {sample_id}: {result['consensus_length']}bp, subtype {result['subtype']}"
        if callable(step_log):
            step_log(msg, "success")
        else:
            step_log.append({"message": msg, "level": "success"})

    return result


def handle_classify_mutations(params: dict[str, Any]) -> dict[str, Any]:
    """Handle ClassifyMutations event facet."""
    sample_id = params.get("sample_id", "")
    variants_raw = params.get("variants", [])
    gene_region = params.get("gene_region", "PR+RT+IN")

    if isinstance(variants_raw, str):
        variants_raw = json.loads(variants_raw)

    mutations, drm_count, apobec_count = classify_mutations(
        sample_id,
        variants_raw,
        gene_region,
    )

    step_log = params.get("_step_log")
    if step_log is not None:
        msg = f"Classified {len(mutations)} mutations: {drm_count} DRMs, {apobec_count} APOBEC for {sample_id}"
        if callable(step_log):
            step_log(msg, "info")
        else:
            step_log.append({"message": msg, "level": "info"})

    return {
        "mutations": mutations,
        "drm_count": drm_count,
        "apobec_count": apobec_count,
    }


_DISPATCH: dict[str, Any] = {
    f"{NAMESPACE}.CallVariants": handle_call_variants,
    f"{NAMESPACE}.GenerateConsensus": handle_generate_consensus,
    f"{NAMESPACE}.ClassifyMutations": handle_classify_mutations,
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

"""Sequencing handlers for the hiv-drug-resistance example."""

from __future__ import annotations

import os
from typing import Any

from handlers.shared.resistance_utils import align_reads, assess_read_quality

NAMESPACE = "hiv.Sequencing"


def handle_assess_quality(params: dict[str, Any]) -> dict[str, Any]:
    """Handle AssessQuality event facet."""
    sample_id = params.get("sample_id", "")
    fastq_path = params.get("fastq_path", "")
    min_quality = params.get("min_quality", 30)
    min_depth = params.get("min_depth", 100)
    if isinstance(min_quality, str):
        min_quality = int(min_quality)
    if isinstance(min_depth, str):
        min_depth = int(min_depth)

    result = assess_read_quality(sample_id, fastq_path, min_quality, min_depth)

    step_log = params.get("_step_log")
    if step_log is not None:
        status = "passed" if result["passed"] else "failed"
        msg = f"QC {status} for {sample_id}: Q={result['mean_quality']}, depth={result['coverage_depth']}"
        if callable(step_log):
            step_log(msg, "success" if result["passed"] else "warning")
        else:
            step_log.append({"message": msg, "level": "success" if result["passed"] else "warning"})

    return result


def handle_align_reads(params: dict[str, Any]) -> dict[str, Any]:
    """Handle AlignReads event facet."""
    sample_id = params.get("sample_id", "")
    fastq_path = params.get("fastq_path", "")
    reference = params.get("reference", "HXB2")

    alignment = align_reads(sample_id, fastq_path, reference)

    step_log = params.get("_step_log")
    if step_log is not None:
        msg = f"Aligned {sample_id}: {alignment['mapped_reads']} reads, {alignment['coverage_pct']}% coverage"
        if callable(step_log):
            step_log(msg, "success")
        else:
            step_log.append({"message": msg, "level": "success"})

    return {"alignment": alignment}


_DISPATCH: dict[str, Any] = {
    f"{NAMESPACE}.AssessQuality": handle_assess_quality,
    f"{NAMESPACE}.AlignReads": handle_align_reads,
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


def register_sequencing_handlers(poller) -> None:
    """Register with AgentPoller."""
    for facet_name, handler in _DISPATCH.items():
        poller.register(facet_name, handler)

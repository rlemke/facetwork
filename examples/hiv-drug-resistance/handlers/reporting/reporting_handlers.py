"""Reporting handlers for the hiv-drug-resistance example."""

from __future__ import annotations

import json
import os
from typing import Any

from handlers.shared.resistance_utils import generate_batch_report, generate_sample_report

NAMESPACE = "hiv.Reporting"


def handle_generate_sample_report(params: dict[str, Any]) -> dict[str, Any]:
    """Handle GenerateSampleReport event facet."""
    sample_id = params.get("sample_id", "")
    qc_passed = params.get("qc_passed", False)
    alignment_raw = params.get("alignment", {})
    variants_raw = params.get("variants", [])
    resistance_raw = params.get("resistance", {})

    if isinstance(alignment_raw, str):
        alignment_raw = json.loads(alignment_raw)
    if isinstance(variants_raw, str):
        variants_raw = json.loads(variants_raw)
    if isinstance(resistance_raw, str):
        resistance_raw = json.loads(resistance_raw)

    qc = {"passed": qc_passed}
    report_path, report_summary = generate_sample_report(
        sample_id,
        qc,
        alignment_raw,
        variants_raw,
        resistance_raw,
    )

    step_log = params.get("_step_log")
    if step_log is not None:
        msg = f"Report generated for {sample_id}: {report_path}"
        if callable(step_log):
            step_log(msg, "success")
        else:
            step_log.append({"message": msg, "level": "success"})

    return {
        "report_path": report_path,
        "report_summary": report_summary,
    }


def handle_generate_batch_report(params: dict[str, Any]) -> dict[str, Any]:
    """Handle GenerateBatchReport event facet."""
    batch_id = params.get("batch_id", "")
    sample_count = params.get("sample_count", 0)
    results_raw = params.get("results", [])
    if isinstance(sample_count, str):
        sample_count = int(sample_count)
    if isinstance(results_raw, str):
        results_raw = json.loads(results_raw)

    report_path, passed, resistance_detected = generate_batch_report(
        batch_id,
        sample_count,
        results_raw,
    )

    step_log = params.get("_step_log")
    if step_log is not None:
        msg = f"Batch report for {batch_id}: {passed} passed, {resistance_detected} with resistance"
        if callable(step_log):
            step_log(msg, "success")
        else:
            step_log.append({"message": msg, "level": "success"})

    return {
        "report_path": report_path,
        "summary": {
            "batch_id": batch_id,
            "total_samples": sample_count,
            "passed_qc": passed,
            "failed_qc": sample_count - passed,
            "resistance_detected": resistance_detected,
        },
    }


_DISPATCH: dict[str, Any] = {
    f"{NAMESPACE}.GenerateSampleReport": handle_generate_sample_report,
    f"{NAMESPACE}.GenerateBatchReport": handle_generate_batch_report,
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


def register_reporting_handlers(poller) -> None:
    """Register with AgentPoller."""
    for facet_name, handler in _DISPATCH.items():
        poller.register(facet_name, handler)

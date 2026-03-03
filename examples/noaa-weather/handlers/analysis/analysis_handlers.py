"""Analysis handlers for the noaa-weather example."""

from __future__ import annotations

import json
import os
from typing import Any

from handlers.shared.weather_utils import (
    compute_annual_summary,
    compute_daily_stats,
    compute_missing_pct,
)

NAMESPACE = "weather.Analysis"


def handle_compute_daily_stats(params: dict[str, Any]) -> dict[str, Any]:
    """Handle ComputeDailyStats event facet."""
    observations = params.get("observations", [])
    station_id = params.get("station_id", "")
    if isinstance(observations, str):
        observations = json.loads(observations)

    daily = compute_daily_stats(observations)
    annual = compute_annual_summary(daily)

    step_log = params.get("_step_log")
    if step_log is not None:
        msg = f"Computed {len(daily)} daily stats for {station_id}, annual precip={annual['annual_precip']}mm"
        if callable(step_log):
            step_log(msg, "success")
        else:
            step_log.append({"message": msg, "level": "success"})

    return {
        "daily_stats": daily,
        "total_days": len(daily),
        "annual_precip": annual["annual_precip"],
    }


def handle_sparse_analysis(params: dict[str, Any]) -> dict[str, Any]:
    """Handle SparseAnalysis event facet."""
    observations = params.get("observations", [])
    station_id = params.get("station_id", "")
    if isinstance(observations, str):
        observations = json.loads(observations)

    total = len(observations)
    # Coverage: fraction of expected hourly obs (365 * 24 = 8760)
    expected = 8760
    coverage_pct = round(total / expected * 100, 1) if expected > 0 else 0.0
    missing_pct = compute_missing_pct(observations)

    summary = f"Sparse data for {station_id}: {total} records, {coverage_pct}% coverage, {missing_pct:.1f}% missing temp"

    step_log = params.get("_step_log")
    if step_log is not None:
        if callable(step_log):
            step_log(summary, "info")
        else:
            step_log.append({"message": summary, "level": "info"})

    return {
        "summary": summary,
        "record_count": total,
        "coverage_pct": coverage_pct,
    }


_DISPATCH: dict[str, Any] = {
    f"{NAMESPACE}.ComputeDailyStats": handle_compute_daily_stats,
    f"{NAMESPACE}.SparseAnalysis": handle_sparse_analysis,
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

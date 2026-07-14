"""Phase 4 - statistics & spatial analysis: SpearmanCorrelation, MoransI,
GeographicallyWeightedRegression, TemporalEvolution, CombineStats."""

from __future__ import annotations

import os
from typing import Any

from handlers.shared import equity_utils as U

NAMESPACE = "osm.equity"


def handle_spearman(params: dict[str, Any]) -> dict[str, Any]:
    rho, p = U.spearman(params.get("records", []))
    return {"rho": rho, "pvalue": p}


def handle_morans_i(params: dict[str, Any]) -> dict[str, Any]:
    i, p = U.morans_i(params.get("records", []))
    return {"i": i, "pvalue": p}


def handle_gwr(params: dict[str, Any]) -> dict[str, Any]:
    path, r2 = U.gwr(params.get("records", []))
    return {"summary_path": path, "mean_r2": r2}


def handle_temporal(params: dict[str, Any]) -> dict[str, Any]:
    trend, gap = U.temporal_evolution(params.get("records", []), params.get("snapshot", ""))
    return {"trend": trend, "gap_change": gap}


def handle_combine_stats(params: dict[str, Any]) -> dict[str, Any]:
    results = {
        "spearman_rho": float(params.get("spearman_rho", 0.0)),
        "spearman_pvalue": float(params.get("spearman_p", 1.0)),
        "morans_i": float(params.get("moran_i", 0.0)),
        "morans_pvalue": float(params.get("moran_p", 1.0)),
        "gwr_summary_path": params.get("gwr_summary", ""),
        "gwr_mean_r2": float(params.get("gwr_r2", 0.0)),
        "temporal_trend": params.get("temporal_trend", "n/a"),
        "temporal_gap_change": float(params.get("temporal_gap_change", 0.0)),
    }
    return {"results": results}


_DISPATCH: dict[str, Any] = {
    f"{NAMESPACE}.SpearmanCorrelation": handle_spearman,
    f"{NAMESPACE}.MoransI": handle_morans_i,
    f"{NAMESPACE}.GeographicallyWeightedRegression": handle_gwr,
    f"{NAMESPACE}.TemporalEvolution": handle_temporal,
    f"{NAMESPACE}.CombineStats": handle_combine_stats,
}


def handle(payload: dict) -> dict:
    return _DISPATCH[payload["_facet_name"]](payload)


def register_handlers(runner) -> None:
    for facet_name in _DISPATCH:
        runner.register_handler(
            facet_name=facet_name,
            module_uri=f"file://{os.path.abspath(__file__)}",
            entrypoint="handle",
        )


def register_analysis_handlers(poller) -> None:
    for facet_name, fn in _DISPATCH.items():
        poller.register(facet_name, fn)

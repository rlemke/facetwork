"""Event facet handler for risk report generation.

Handles the GenerateReport event facet.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from typing import Any

from facetwork.config import get_output_base

_LOCAL_OUTPUT = get_output_base()
_RISK_REPORTS_DIR = os.path.join(_LOCAL_OUTPUT, "risk-reports")

NAMESPACE = "risk.Reporting"


def handle_generate_report(params: dict[str, Any]) -> dict[str, Any]:
    """Compile a JSON summary of portfolio risk analysis."""
    portfolio = params.get("portfolio", {})
    metrics = params.get("metrics", {})
    greeks = params.get("greeks", {})
    stress_results = params.get("stress_results", {})
    step_log = params.get("_step_log")

    if isinstance(portfolio, str):
        portfolio = json.loads(portfolio)
    if isinstance(metrics, str):
        metrics = json.loads(metrics)
    if isinstance(greeks, str):
        greeks = json.loads(greeks)

    timestamp = datetime.now(UTC).isoformat()

    summary = {
        "portfolio_name": portfolio.get("name", "unknown"),
        "total_value": portfolio.get("total_value", 0),
        "num_positions": len(portfolio.get("positions", [])),
        "risk_metrics": metrics,
        "portfolio_greeks": {
            "delta": greeks.get("portfolio_delta", 0),
            "vega": greeks.get("portfolio_vega", 0),
        },
        "stress_scenarios": stress_results,
        "generated_at": timestamp,
    }

    if step_log:
        step_log(
            f"GenerateReport: portfolio={portfolio.get('name', 'unknown')} timestamp={timestamp}",
            level="success",
        )

    return {
        "report": {
            "report_path": os.path.join(
                _RISK_REPORTS_DIR, f"{portfolio.get('name', 'unknown')}_{timestamp}.json"
            ),
            "summary": summary,
            "timestamp": timestamp,
        }
    }


# ---------------------------------------------------------------------------
# RegistryRunner dispatch
# ---------------------------------------------------------------------------

_DISPATCH: dict[str, Any] = {
    f"{NAMESPACE}.GenerateReport": handle_generate_report,
}


def handle(payload: dict) -> dict:
    """RegistryRunner dispatch entrypoint."""
    facet_name = payload["_facet_name"]
    handler = _DISPATCH.get(facet_name)
    if handler is None:
        raise ValueError(f"Unknown facet: {facet_name}")
    return handler(payload)


def register_handlers(runner) -> None:
    """Register all facets with a RegistryRunner."""
    for facet_name in _DISPATCH:
        runner.register_handler(
            facet_name=facet_name,
            module_uri=f"file://{os.path.abspath(__file__)}",
            entrypoint="handle",
        )


def register_reporting_handlers(poller) -> None:
    """Register all reporting handlers with the poller."""
    for facet_name, handler in _DISPATCH.items():
        poller.register(facet_name, handler)

"""Event facet handler for site-selection scoring.

Handles the ScoreCounties event facet.
"""

import os
from typing import Any

from .scoring_builder import score_counties

NAMESPACE = "sitesel.Scoring"


def handle_score_counties(params: dict[str, Any]) -> dict[str, Any]:
    """Score counties by food-service suitability.

    Params:
        demographics_path: Path to demographics GeoJSON.
        restaurants_path: Path to restaurants GeoJSON.
        state_fips: Two-digit FIPS code.
    """
    demographics_path = params["demographics_path"]
    restaurants_path = params["restaurants_path"]
    state_fips = params["state_fips"]
    step_log = params.get("_step_log")

    try:
        result = score_counties(demographics_path, restaurants_path,
                                state_fips)
        if step_log:
            step_log(f"ScoreCounties: state={state_fips} "
                     f"counties={result['county_count']} "
                     f"top={result['top_county']} ({result['top_score']:.2f})",
                     level="success")
        return {"result": result}
    except Exception as exc:
        if step_log:
            step_log(f"ScoreCounties: {exc}", level="error")
        raise


# RegistryRunner dispatch adapter
_DISPATCH: dict[str, Any] = {
    f"{NAMESPACE}.ScoreCounties": handle_score_counties,
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


def register_scoring_handlers(poller) -> None:
    """Register all scoring handlers with the poller."""
    for facet_name, handler in _DISPATCH.items():
        poller.register(facet_name, handler)

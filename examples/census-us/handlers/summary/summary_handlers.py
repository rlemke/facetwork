"""Event facet handlers for Census summary operations.

Handles JoinGeo and SummarizeState event facets.
"""

import os
from typing import Any

from .summary_builder import join_geo, summarize_state

NAMESPACE = "census.Summary"


def handle_join_geo(params: dict[str, Any]) -> dict[str, Any]:
    """Join ACS data with TIGER geographic boundaries.

    Params:
        acs_path: Path to ACS CSV output.
        tiger_path: Path to TIGER GeoJSON output.
        join_field: Field to join on (default "GEOID").
    """
    acs_path = params.get("acs_path", "")
    tiger_path = params.get("tiger_path", "")
    join_field = params.get("join_field", "GEOID")
    step_log = params.get("_step_log")

    try:
        result = join_geo(
            acs_path=acs_path,
            tiger_path=tiger_path,
            join_field=join_field,
        )

        if step_log:
            step_log(
                f"JoinGeo: {result.feature_count} features joined on {join_field}",
                level="success",
            )

        return {
            "result": {
                "state_fips": "",
                "state_name": "",
                "output_path": result.output_path,
                "tables_joined": 1,
                "record_count": result.feature_count,
            }
        }
    except Exception as exc:
        if step_log:
            step_log(f"JoinGeo: {exc}", level="error")
        raise


def handle_summarize_state(params: dict[str, Any]) -> dict[str, Any]:
    """Build state-level summary from multiple ACS results.

    Params:
        population: ACSResult from ExtractPopulation.
        income: ACSResult from ExtractIncome.
        housing: ACSResult from ExtractHousing.
        education: ACSResult from ExtractEducation.
        commuting: ACSResult from ExtractCommuting.
    """
    population = params.get("population", {})
    income = params.get("income", {})
    housing = params.get("housing", {})
    education = params.get("education", {})
    commuting = params.get("commuting", {})
    race = params.get("race")
    poverty = params.get("poverty")
    employment = params.get("employment")
    step_log = params.get("_step_log")

    try:
        kwargs: dict[str, Any] = {}
        if race is not None:
            kwargs["race"] = race
        if poverty is not None:
            kwargs["poverty"] = poverty
        if employment is not None:
            kwargs["employment"] = employment

        result = summarize_state(
            population=population,
            income=income,
            housing=housing,
            education=education,
            commuting=commuting,
            **kwargs,
        )

        if step_log:
            step_log(
                f"SummarizeState: {result.tables_joined} tables, "
                f"{result.record_count} records (state={result.state_fips})",
                level="success",
            )

        return {
            "result": {
                "state_fips": result.state_fips,
                "state_name": result.state_name,
                "output_path": result.output_path,
                "tables_joined": result.tables_joined,
                "record_count": result.record_count,
            }
        }
    except Exception as exc:
        if step_log:
            step_log(f"SummarizeState: {exc}", level="error")
        raise


# RegistryRunner dispatch adapter
_DISPATCH: dict[str, Any] = {
    f"{NAMESPACE}.JoinGeo": handle_join_geo,
    f"{NAMESPACE}.SummarizeState": handle_summarize_state,
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


def register_summary_handlers(poller) -> None:
    """Register all summary handlers with the poller."""
    for facet_name, handler in _DISPATCH.items():
        poller.register(facet_name, handler)

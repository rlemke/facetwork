"""Event facet handlers for site-selection data extraction.

Handles JoinDemographics and ExtractRestaurants event facets.
"""

import os
from typing import Any

from .demographics_extractor import join_demographics
from .restaurant_extractor import extract_restaurants

NAMESPACE = "sitesel.Extract"


def handle_join_demographics(params: dict[str, Any]) -> dict[str, Any]:
    """Join ACS + TIGER into demographics GeoJSON.

    Params:
        acs_path: Path to ACS CSV file.
        tiger_path: Path to TIGER GeoJSON file.
        state_fips: Two-digit FIPS code.
    """
    acs_path = params["acs_path"]
    tiger_path = params["tiger_path"]
    state_fips = params["state_fips"]
    step_log = params.get("_step_log")

    try:
        result = join_demographics(acs_path, tiger_path, state_fips)
        if step_log:
            step_log(f"JoinDemographics: state={state_fips} "
                     f"features={result['feature_count']}",
                     level="success")
        return {"result": result}
    except Exception as exc:
        if step_log:
            step_log(f"JoinDemographics: {exc}", level="error")
        raise


def handle_extract_restaurants(params: dict[str, Any]) -> dict[str, Any]:
    """Extract food amenities from PBF.

    Params:
        pbf_path: Path to the PBF file.
        region: Region name.
    """
    pbf_path = params["pbf_path"]
    region = params["region"]
    step_log = params.get("_step_log")

    try:
        result = extract_restaurants(pbf_path, region)
        if step_log:
            step_log(f"ExtractRestaurants: region={region} "
                     f"count={result['restaurant_count']}",
                     level="success")
        return {"result": result}
    except Exception as exc:
        if step_log:
            step_log(f"ExtractRestaurants: {exc}", level="error")
        raise


# RegistryRunner dispatch adapter
_DISPATCH: dict[str, Any] = {
    f"{NAMESPACE}.JoinDemographics": handle_join_demographics,
    f"{NAMESPACE}.ExtractRestaurants": handle_extract_restaurants,
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


def register_extract_handlers(poller) -> None:
    """Register all extract handlers with the poller."""
    for facet_name, handler in _DISPATCH.items():
        poller.register(facet_name, handler)

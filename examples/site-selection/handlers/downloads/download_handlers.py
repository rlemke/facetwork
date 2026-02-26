"""Event facet handlers for site-selection data downloads.

Handles DownloadACS, DownloadTIGER, and DownloadPBF event facets
by delegating to the shared downloader module.
"""

import os
from typing import Any

from ..shared.downloader import download_acs, download_tiger, download_pbf

NAMESPACE = "sitesel.Downloads"


def handle_download_acs(params: dict[str, Any]) -> dict[str, Any]:
    """Download ACS summary file for a state.

    Params:
        state_fips: Two-digit FIPS code (default "01")
    """
    state_fips = params.get("state_fips", "01")
    step_log = params.get("_step_log")

    try:
        result = download_acs(state_fips=state_fips)
        source = "cache" if result["wasInCache"] else "download"
        if step_log:
            step_log(f"DownloadACS: state={state_fips} ({source})",
                     level="success")
        return {"file": {
            "path": result["path"],
            "state_fips": result["state_fips"],
            "wasInCache": result["wasInCache"],
        }}
    except Exception as exc:
        if step_log:
            step_log(f"DownloadACS: {exc}", level="error")
        raise


def handle_download_tiger(params: dict[str, Any]) -> dict[str, Any]:
    """Download TIGER/Line shapefile for a state.

    Params:
        state_fips: Two-digit FIPS code (default "01")
        geo_level: Geography level (default "COUNTY")
    """
    state_fips = params.get("state_fips", "01")
    geo_level = params.get("geo_level", "COUNTY")
    step_log = params.get("_step_log")

    try:
        result = download_tiger(state_fips=state_fips, geo_level=geo_level)
        source = "cache" if result["wasInCache"] else "download"
        if step_log:
            step_log(f"DownloadTIGER: state={state_fips} level={geo_level} ({source})",
                     level="success")
        return {"file": {
            "path": result["path"],
            "state_fips": result["state_fips"],
            "geo_level": result["geo_level"],
            "wasInCache": result["wasInCache"],
        }}
    except Exception as exc:
        if step_log:
            step_log(f"DownloadTIGER: {exc}", level="error")
        raise


def handle_download_pbf(params: dict[str, Any]) -> dict[str, Any]:
    """Download Geofabrik PBF file for a region.

    Params:
        region: State/region name (default "alabama")
    """
    region = params.get("region", "alabama")
    step_log = params.get("_step_log")

    try:
        result = download_pbf(region=region)
        source = "cache" if result["wasInCache"] else "download"
        if step_log:
            step_log(f"DownloadPBF: region={region} ({source})",
                     level="success")
        return {"file": {
            "path": result["path"],
            "region": result["region"],
            "wasInCache": result["wasInCache"],
        }}
    except Exception as exc:
        if step_log:
            step_log(f"DownloadPBF: {exc}", level="error")
        raise


# RegistryRunner dispatch adapter
_DISPATCH: dict[str, Any] = {
    f"{NAMESPACE}.DownloadACS": handle_download_acs,
    f"{NAMESPACE}.DownloadTIGER": handle_download_tiger,
    f"{NAMESPACE}.DownloadPBF": handle_download_pbf,
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


def register_download_handlers(poller) -> None:
    """Register all download handlers with the poller."""
    for facet_name, handler in _DISPATCH.items():
        poller.register(facet_name, handler)

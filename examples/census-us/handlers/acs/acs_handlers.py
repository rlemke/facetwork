"""Event facet handlers for ACS data extraction.

Handles ExtractPopulation, ExtractIncome, ExtractHousing, ExtractEducation,
and ExtractCommuting event facets.
"""

import os
from typing import Any

from .acs_extractor import ACS_TABLES, extract_acs_table

NAMESPACE = "census.ACS"

# Facet name → ACS table ID
_FACET_TABLE_MAP = {
    "ExtractPopulation": "B01003",
    "ExtractIncome": "B19013",
    "ExtractHousing": "B25001",
    "ExtractEducation": "B15003",
    "ExtractCommuting": "B08301",
    "ExtractTenure": "B25003",
    "ExtractHouseholds": "B11001",
    "ExtractAge": "B01001",
    "ExtractVehicles": "B25044",
    "ExtractRace": "B02001",
    "ExtractPoverty": "B17001",
    "ExtractEmployment": "B23025",
}


def _make_acs_handler(facet_name: str, table_id: str):
    """Create a handler for an ACS extraction event facet."""
    def handler(params: dict[str, Any]) -> dict[str, Any]:
        file_info = params.get("file", {})
        csv_path = file_info.get("path", "") if isinstance(file_info, dict) else ""
        state_fips = params.get("state_fips", "")
        geo_level = params.get("geo_level", "county")
        step_log = params.get("_step_log")

        try:
            result = extract_acs_table(
                csv_path=csv_path,
                table_id=table_id,
                state_fips=state_fips,
                geo_level=geo_level,
            )

            if step_log:
                label = ACS_TABLES[table_id]["label"]
                step_log(
                    f"{facet_name}: {label} — {result.record_count} records "
                    f"(state={state_fips}, level={geo_level}) "
                    f"output={result.output_path}",
                    level="success",
                )

            return {
                "result": {
                    "table_id": result.table_id,
                    "output_path": result.output_path,
                    "record_count": result.record_count,
                    "geography_level": result.geography_level,
                    "year": result.year,
                    "extraction_date": result.extraction_date,
                }
            }
        except Exception as exc:
            if step_log:
                step_log(f"{facet_name}: {exc}", level="error")
            raise
    return handler


# Build handlers for each facet
_HANDLERS = {
    facet: _make_acs_handler(facet, table_id)
    for facet, table_id in _FACET_TABLE_MAP.items()
}

# RegistryRunner dispatch adapter
_DISPATCH: dict[str, Any] = {
    f"{NAMESPACE}.{facet}": handler
    for facet, handler in _HANDLERS.items()
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


def register_acs_handlers(poller) -> None:
    """Register all ACS extraction handlers with the poller."""
    for facet_name, handler in _DISPATCH.items():
        poller.register(facet_name, handler)

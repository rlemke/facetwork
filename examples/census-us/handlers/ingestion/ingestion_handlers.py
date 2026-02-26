"""Event facet handlers for MongoDB ingestion of census data.

Handles PopulationToDB, IncomeToDB, HousingToDB, EducationToDB,
CommutingToDB, CountiesToDB, JoinedToDB, and SummaryToDB event facets.
Each reads an upstream handler's output file and upserts it into MongoDB.
"""

import os
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from .db_ingest import OutputStore, get_mongo_db

NAMESPACE = "census.Ingestion"


def _ingestion_result(dataset_key: str, record_count: int,
                      data_type: str) -> dict[str, Any]:
    """Build the standard IngestionResult return dict."""
    return {
        "ingestion": {
            "dataset_key": dataset_key,
            "record_count": record_count,
            "data_type": data_type,
            "imported_at": datetime.now(UTC).isoformat(),
        }
    }


# ------------------------------------------------------------------
# ACS → MongoDB (CSV)
# ------------------------------------------------------------------

def _make_acs_db_handler(table_id: str) -> Callable:
    """Factory: create a handler that ingests an ACS CSV into MongoDB."""
    def handler(params: dict[str, Any]) -> dict[str, Any]:
        result = params.get("result", {})
        state_fips = params.get("state_fips", "")
        step_log = params.get("_step_log")
        facet_name = params.get("_facet_name", "")

        try:
            store = OutputStore(get_mongo_db())
            dataset_key = f"census.acs.{table_id.lower()}.{state_fips}"
            count = store.ingest_csv(
                path=result.get("output_path", ""),
                dataset_key=dataset_key,
                feature_key_field="GEOID",
                facet_name=facet_name,
            )

            if step_log:
                step_log(
                    f"{facet_name}: ingested {count} ACS records "
                    f"(table={table_id}, state={state_fips})",
                    level="success",
                )

            return _ingestion_result(dataset_key, count, "csv_record")
        except Exception as exc:
            if step_log:
                step_log(f"{facet_name}: {exc}", level="error")
            raise
    return handler


# ------------------------------------------------------------------
# TIGER counties → MongoDB (GeoJSON)
# ------------------------------------------------------------------

def _handle_counties_to_db(params: dict[str, Any]) -> dict[str, Any]:
    """Ingest TIGER county GeoJSON into MongoDB."""
    result = params.get("result", {})
    state_fips = params.get("state_fips", "")
    step_log = params.get("_step_log")
    facet_name = params.get("_facet_name", "")

    try:
        store = OutputStore(get_mongo_db())
        dataset_key = f"census.tiger.county.{state_fips}"
        count = store.ingest_geojson(
            path=result.get("output_path", ""),
            dataset_key=dataset_key,
            feature_key_field="GEOID",
            facet_name=facet_name,
        )

        if step_log:
            step_log(
                f"{facet_name}: ingested {count} county features "
                f"(state={state_fips})",
                level="success",
            )

        return _ingestion_result(dataset_key, count, "geojson_feature")
    except Exception as exc:
        if step_log:
            step_log(f"{facet_name}: {exc}", level="error")
        raise


# ------------------------------------------------------------------
# Joined geo → MongoDB (GeoJSON)
# ------------------------------------------------------------------

def _handle_joined_to_db(params: dict[str, Any]) -> dict[str, Any]:
    """Ingest joined ACS+TIGER GeoJSON into MongoDB."""
    result = params.get("result", {})
    state_fips = params.get("state_fips", "")
    step_log = params.get("_step_log")
    facet_name = params.get("_facet_name", "")

    try:
        store = OutputStore(get_mongo_db())
        dataset_key = f"census.joined.{state_fips}"
        output_path = result.get("output_path", "")

        if output_path:
            count = store.ingest_geojson(
                path=output_path,
                dataset_key=dataset_key,
                feature_key_field="GEOID",
                facet_name=facet_name,
            )
        else:
            count = 0

        if step_log:
            step_log(
                f"{facet_name}: ingested {count} joined features "
                f"(state={state_fips})",
                level="success",
            )

        return _ingestion_result(dataset_key, count, "geojson_feature")
    except Exception as exc:
        if step_log:
            step_log(f"{facet_name}: {exc}", level="error")
        raise


# ------------------------------------------------------------------
# Summary → MongoDB (JSON)
# ------------------------------------------------------------------

def _handle_summary_to_db(params: dict[str, Any]) -> dict[str, Any]:
    """Ingest state summary JSON into MongoDB."""
    result = params.get("result", {})
    state_fips = params.get("state_fips", "")
    step_log = params.get("_step_log")
    facet_name = params.get("_facet_name", "")

    try:
        store = OutputStore(get_mongo_db())
        dataset_key = f"census.summary.{state_fips}"
        output_path = result.get("output_path", "")

        if output_path:
            count = store.ingest_json(
                path=output_path,
                dataset_key=dataset_key,
                key_field="state_fips",
                facet_name=facet_name,
            )
        else:
            count = 0

        if step_log:
            step_log(
                f"{facet_name}: ingested summary (state={state_fips})",
                level="success",
            )

        return _ingestion_result(dataset_key, count, "json_object")
    except Exception as exc:
        if step_log:
            step_log(f"{facet_name}: {exc}", level="error")
        raise


# ------------------------------------------------------------------
# Dispatch
# ------------------------------------------------------------------

_DISPATCH: dict[str, Any] = {
    f"{NAMESPACE}.PopulationToDB": _make_acs_db_handler("B01003"),
    f"{NAMESPACE}.IncomeToDB": _make_acs_db_handler("B19013"),
    f"{NAMESPACE}.HousingToDB": _make_acs_db_handler("B25001"),
    f"{NAMESPACE}.EducationToDB": _make_acs_db_handler("B15003"),
    f"{NAMESPACE}.CommutingToDB": _make_acs_db_handler("B08301"),
    f"{NAMESPACE}.TenureToDB": _make_acs_db_handler("B25003"),
    f"{NAMESPACE}.HouseholdsToDB": _make_acs_db_handler("B11001"),
    f"{NAMESPACE}.AgeToDB": _make_acs_db_handler("B01001"),
    f"{NAMESPACE}.VehiclesToDB": _make_acs_db_handler("B25044"),
    f"{NAMESPACE}.RaceToDB": _make_acs_db_handler("B02001"),
    f"{NAMESPACE}.PovertyToDB": _make_acs_db_handler("B17001"),
    f"{NAMESPACE}.EmploymentToDB": _make_acs_db_handler("B23025"),
    f"{NAMESPACE}.CountiesToDB": _handle_counties_to_db,
    f"{NAMESPACE}.JoinedToDB": _handle_joined_to_db,
    f"{NAMESPACE}.SummaryToDB": _handle_summary_to_db,
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


def register_ingestion_handlers(poller) -> None:
    """Register all ingestion handlers with the poller."""
    for facet_name, handler in _DISPATCH.items():
        poller.register(facet_name, handler)

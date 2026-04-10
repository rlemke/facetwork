"""Event facet handler for site-selection output.

Handles the ExportScored event facet, copying scored GeoJSON
into the output store for dashboard consumption.
"""

import logging
import os
from typing import Any

from facetwork.config import get_output_base

logger = logging.getLogger(__name__)

NAMESPACE = "sitesel.Output"


def _try_output_store_ingest(scored_path: str, state_fips: str, facet_name: str) -> int:
    """Attempt to ingest into MongoDB OutputStore. Returns record count or 0."""
    try:
        from facetwork.runtime.persistence.output_store import OutputStore

        db = None
        try:
            from pymongo import MongoClient

            url = os.environ.get("AFL_MONGODB_URL")
            if url:
                db_name = os.environ.get("AFL_EXAMPLES_DATABASE", "facetwork_examples")
                db = MongoClient(url)[db_name]
        except Exception:
            pass
        if db is None:
            return 0

        store = OutputStore(db)
        dataset_key = f"sitesel.scored.{state_fips}"
        count = store.ingest_geojson(
            path=scored_path,
            dataset_key=dataset_key,
            feature_key_field="GEOID",
            facet_name=facet_name,
        )
        return count
    except Exception as exc:
        logger.warning("OutputStore ingest failed (non-fatal): %s", exc)
        return 0


def handle_export_scored(params: dict[str, Any]) -> dict[str, Any]:
    """Export scored GeoJSON to output store.

    Params:
        scored_path: Path to scored GeoJSON file.
        state_fips: Two-digit FIPS code.
    """
    scored_path = params["scored_path"]
    state_fips = params["state_fips"]
    step_log = params.get("_step_log")
    facet_name = params.get("_facet_name", f"{NAMESPACE}.ExportScored")

    try:
        # Attempt DB ingestion
        count = _try_output_store_ingest(scored_path, state_fips, facet_name)

        # Always copy to the local output directory as well
        output_dir = get_output_base()
        dest_dir = os.path.join(output_dir, "sitesel-export")
        os.makedirs(dest_dir, exist_ok=True)
        dest_path = os.path.join(dest_dir, f"{state_fips}_scored.geojson")

        if scored_path and os.path.exists(scored_path):
            with open(scored_path) as src, open(dest_path, "w") as dst:
                dst.write(src.read())

        if step_log:
            step_log(
                f"ExportScored: state={state_fips} db_records={count} path={dest_path}",
                level="success",
            )

        return {
            "result": {
                "output_path": dest_path,
                "format": "geojson",
            }
        }
    except Exception as exc:
        if step_log:
            step_log(f"ExportScored: {exc}", level="error")
        raise


# RegistryRunner dispatch adapter
_DISPATCH: dict[str, Any] = {
    f"{NAMESPACE}.ExportScored": handle_export_scored,
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


def register_output_handlers(poller) -> None:
    """Register all output handlers with the poller."""
    for facet_name, handler in _DISPATCH.items():
        poller.register(facet_name, handler)

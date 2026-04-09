#!/usr/bin/env python3
"""Seed script — compiles all FFL sources and populates MongoDB.

This script:
1. Reads all FFL sources in dependency order (OSM types first, then
   operations, cache, GH, GTFS, zoom, continental workflows)
2. Concatenates into a single source, parses, and validates
3. Stores compiled flow in MongoDB `flows` collection
4. Creates sample execution tasks for each top-level workflow

Run locally:
    cd examples/continental-lz
    PYTHONPATH=../.. AFL_MONGODB_URL=mongodb://localhost:27019 python scripts/seed.py

Via Docker:
    docker compose --profile seed run --rm seed
"""

import logging
import os
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

# Add parent to path for afl imports
sys.path.insert(0, os.environ.get("PYTHONPATH", "/app"))

from pymongo import MongoClient

from facetwork.emitter import emit_dict
from facetwork.parser import parse
from facetwork.validator import validate

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("continental-lz-seed")

# FFL source files in dependency order
# When running in Docker, paths are relative to /app
# When running locally, paths are relative to the script location


def _find_afl_root() -> Path:
    """Determine the FFL source root based on environment."""
    # Docker: files are copied to /app/osm-afl/ and /app/continental-afl/
    if Path("/app/osm-afl").exists():
        return Path("/app")
    # Local: relative to this script
    return Path(__file__).resolve().parent.parent


def _get_source_files(root: Path) -> list[tuple[str, Path]]:
    """Return FFL source files in dependency order."""
    # Determine paths based on environment
    if (root / "osm-afl").exists():
        # Docker layout
        osm_dir = root / "osm-afl"
        cont_dir = root / "continental-afl"
    else:
        # Local layout
        osm_dir = root / ".." / "osm-geocoder" / "afl"
        cont_dir = root / "afl"

    return [
        ("osm.types", osm_dir / "osmtypes.ffl"),
        ("osm.ops", osm_dir / "osmoperations.ffl"),
        ("osm.cache.*", osm_dir / "osmcache.ffl"),
        ("osm.ops.GraphHopper", osm_dir / "osmgraphhopper.ffl"),
        ("osm.cache.GraphHopper.*", osm_dir / "osmgraphhoppercache.ffl"),
        ("osm.Transit.GTFS", osm_dir / "osmgtfs.ffl"),
        ("osm.Roads.ZoomBuilder", osm_dir / "osmzoombuilder.ffl"),
        ("osm.Population", osm_dir / "osmfilters_population.ffl"),
        ("continental.types", cont_dir / "continental_types.ffl"),
        ("continental.lz", cont_dir / "continental_lz_workflows.ffl"),
        ("continental.transit", cont_dir / "continental_gtfs_workflows.ffl"),
        ("continental", cont_dir / "continental_full.ffl"),
    ]


def seed_database() -> None:
    """Compile FFL sources and seed the database."""
    mongodb_url = os.environ.get("AFL_MONGODB_URL", "mongodb://localhost:27019")
    database = os.environ.get("AFL_MONGODB_DATABASE", "afl_continental_lz")

    logger.info(f"Connecting to {mongodb_url}/{database}")
    client = MongoClient(mongodb_url)
    db = client[database]

    flows_col = db["flows"]
    tasks_col = db["tasks"]

    # Step 1: Read all FFL sources
    root = _find_afl_root()
    source_files = _get_source_files(root)

    sources = ""
    source_docs = []
    for ns_name, path in source_files:
        resolved = path.resolve()
        if not resolved.exists():
            logger.error(f"  Missing: {resolved} ({ns_name})")
            sys.exit(1)
        content = resolved.read_text()
        sources += content + "\n"
        source_docs.append(
            {
                "name": resolved.name,
                "content": content,
                "language": "afl",
                "namespace": ns_name,
            }
        )
        logger.info(f"  Loaded: {resolved.name} ({ns_name})")

    # Step 2: Parse and validate
    logger.info("Parsing concatenated FFL sources...")
    try:
        ast = parse(sources, filename="continental-lz-combined.ffl")
    except Exception as e:
        logger.error(f"Parse error: {e}")
        sys.exit(1)

    result = validate(ast)
    if not result.is_valid:
        logger.error(f"Validation errors: {result.errors}")
        sys.exit(1)

    logger.info("Validation passed")

    # Step 3: Emit compiled output
    compiled = emit_dict(ast)

    # Extract workflow names from declarations
    workflow_names = []
    for decl in compiled.get("declarations", []):
        if decl.get("type") == "Namespace":
            for inner in decl.get("declarations", []):
                if inner.get("type") == "WorkflowDecl":
                    workflow_names.append(f"{decl['name']}.{inner['name']}")

    logger.info(f"Compiled {len(workflow_names)} workflows:")
    for w in workflow_names:
        logger.info(f"  {w}")

    # Step 4: Store flow document
    flow_id = str(uuid.uuid4())
    flow_doc = {
        "uuid": flow_id,
        "name": "continental-lz-pipeline",
        "path": "/continental-lz/",
        "sources": source_docs,
        "compiled": compiled,
        "created": datetime.now(UTC).isoformat(),
        "seeded": True,
    }
    flow_doc["workflows"] = workflow_names

    flows_col.update_one(
        {"name": flow_doc["name"]},
        {"$set": flow_doc},
        upsert=True,
    )
    logger.info(f"Stored flow: {flow_doc['name']}")

    # Step 5: Create sample execution tasks for top-level workflows
    continental_workflows = [w for w in workflow_names if w.startswith("continental.")]
    for wf_name in continental_workflows:
        task_id = str(uuid.uuid4())
        inputs = {}
        if "LZ" in wf_name or "Full" in wf_name:
            inputs["output_base"] = "/data/lz-output"

        task_doc = {
            "uuid": task_id,
            "name": "fw:execute",
            "flow_id": flow_id,
            "workflow_id": "",
            "workflow_name": wf_name,
            "runner_id": "",
            "step_id": "",
            "state": "pending",
            "created": datetime.now(UTC).isoformat(),
            "updated": datetime.now(UTC).isoformat(),
            "data": {"inputs": inputs},
            "data_type": "execute",
            "task_list_name": "fw:execute",
            "seeded": True,
        }

        tasks_col.update_one(
            {"workflow_name": wf_name, "seeded": True},
            {"$set": task_doc},
            upsert=True,
        )
        logger.info(f"  Created task: {wf_name}")

    # Summary
    logger.info("")
    logger.info("=" * 60)
    logger.info("Continental LZ Seed Complete!")
    logger.info("=" * 60)
    logger.info(f"Flows: {flows_col.count_documents({})}")
    logger.info(f"Tasks: {tasks_col.count_documents({})}")
    logger.info(f"Workflows: {len(workflow_names)}")
    logger.info("")
    logger.info("View the dashboard at: http://localhost:8081")
    logger.info("=" * 60)


if __name__ == "__main__":
    seed_database()

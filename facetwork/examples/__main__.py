"""CLI: register handlers for one or more examples into the registry.

Usage::

    python -m facetwork.examples [--seed] [NAME ...]

With no names, operates on every discovered example. ``--seed`` also
compiles each example's FFL and upserts a FlowDefinition + its
WorkflowDefinitions so the workflows appear in the dashboard's Flows tab
(idempotent — re-running replaces the example's prior seed). Used by
``fw runner start``, ``fw ffl seed``, and the per-example
runner container entrypoint; safe to call directly for ad-hoc registration.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from facetwork.config import MongoDBConfig
from facetwork.examples import (
    discover_all_examples,
    filter_examples,
    seed_example_flows,
)
from facetwork.runtime import Evaluator, RegistryRunner, Telemetry
from facetwork.runtime.mongo_store import MongoStore


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    do_seed = "--seed" in argv
    selected = [a for a in argv if a != "--seed"] or None

    repo_root = Path(os.environ.get("REPO_ROOT", os.getcwd()))
    examples = list(filter_examples(discover_all_examples(repo_root), include=selected))

    if not examples:
        print("No examples found (checked entry points and examples/).", file=sys.stderr)
        return 1

    # Honor AFL_MONGODB_URL + AFL_MONGODB_DATABASE consistently with the
    # runner and dashboard.  Without this the script wrote to the
    # default "facetwork" database while AFL_MONGODB_DATABASE was
    # respected by the other components — handler registrations
    # vanished from the dashboard's view.
    cfg = MongoDBConfig.from_env()
    store = MongoStore(cfg.url, database_name=cfg.database)
    runner = RegistryRunner(store, Evaluator(store, Telemetry()))

    print("Registering handlers (upsert)...")
    print()
    total = 0
    for pkg in examples:
        if pkg.handlers_path is None and pkg.source == "local":
            print(f"  {pkg.name}: SKIP (no handlers/)")
            continue
        before = store._db.handler_registrations.count_documents({})
        try:
            pkg.register_handlers(runner)
        except Exception as e:
            print(f"  {pkg.name}: ERROR — {e}")
            continue
        count = store._db.handler_registrations.count_documents({}) - before
        total += count
        print(f"  {pkg.name}: {count} handlers registered  [{pkg.source}]")

    print()
    print(f"Total: {total} handlers registered")

    if do_seed:
        print()
        print("Seeding example workflows (FlowDefinition + WorkflowDefinition)...")
        print()
        seeded_flows = 0
        seeded_workflows = 0
        for pkg in examples:
            try:
                n_flows, n_workflows, warnings = seed_example_flows(pkg, store)
            except Exception as e:
                print(f"  {pkg.name}: seed ERROR — {e}")
                continue
            if n_workflows == 0:
                print(f"  {pkg.name}: no workflows to seed")
                continue
            seeded_flows += n_flows
            seeded_workflows += n_workflows
            line = f"  {pkg.name}: {n_workflows} workflow(s) seeded"
            if warnings:
                line += f"  [{', '.join(warnings)}]"
            print(line)
        print()
        print(f"Seeded {seeded_flows} flow(s) with {seeded_workflows} workflow(s)")

    store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

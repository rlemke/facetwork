"""Migrate seeded flows from the legacy ``example:`` prefix to ``domain:``.

Only the domain packages migrate; genuine teaching examples keep ``example:``.
Idempotent (a no-op once migrated) and UUID-preserving: only ``flows.name.path``
and ``workflows.namespace_id`` are rewritten, never ``flows.uuid`` — so an
in-flight ``fw:execute:<Workflow>`` bootstrap task keeps resolving.

Run via ``fw maint migrate-seed-prefix [--apply]`` (default dry-run) or
``python -m facetwork.domains.migrate``.
"""

from __future__ import annotations

from facetwork.domains.catalog import domain_names

LEGACY_PREFIX = "example:"
NEW_PREFIX = "domain:"


def migrate_seed_prefix(store, *, apply: bool = False, names=None) -> dict:
    """Flip ``example:<name>`` seeds to ``domain:<name>`` for each domain name.

    Returns ``{"flows": n, "workflows": m, "names": [...]}`` — the counts that
    were (or, in dry-run, would be) migrated. Idempotent.
    """
    db = store._db
    names = list(names if names is not None else domain_names())
    migrated_names: list[str] = []
    flows = 0
    workflows = 0
    for n in names:
        old = f"{LEGACY_PREFIX}{n}"
        new = f"{NEW_PREFIX}{n}"
        n_flows = db.flows.count_documents({"name.path": old})
        n_wfs = db.workflows.count_documents({"namespace_id": old})
        if not n_flows and not n_wfs:
            continue
        migrated_names.append(n)
        flows += n_flows
        workflows += n_wfs
        if not apply:
            continue
        # If domain:<n> already exists (a new runner seeded it), the example:<n>
        # docs are SUPERSEDED — delete them rather than flipping (which would
        # create a duplicate path). Otherwise flip in place, rewriting only the
        # embedded path / namespace, never the uuid.
        if db.flows.count_documents({"name.path": new}):
            old_ids = [d["uuid"] for d in db.flows.find({"name.path": old}, {"uuid": 1})]
            db.workflows.delete_many({"namespace_id": old})
            if old_ids:
                db.workflows.delete_many({"flow_id": {"$in": old_ids}})
            db.flows.delete_many({"name.path": old})
        else:
            db.flows.update_many({"name.path": old}, {"$set": {"name.path": new}})
            db.workflows.update_many({"namespace_id": old}, {"$set": {"namespace_id": new}})
    return {"flows": flows, "workflows": workflows, "names": migrated_names}


def main(argv: list[str] | None = None) -> int:
    import sys

    from facetwork.config import MongoDBConfig
    from facetwork.runtime.mongo_store import MongoStore

    argv = list(sys.argv[1:] if argv is None else argv)
    apply = "--apply" in argv

    cfg = MongoDBConfig.from_env()
    store = MongoStore(cfg.url, database_name=cfg.database)
    try:
        result = migrate_seed_prefix(store, apply=apply)
    finally:
        store.close()

    if not result["names"]:
        print("Nothing to migrate — no example:<domain> seeds found (already domain:?).")
        return 0
    verb = "Migrated" if apply else "[dry-run] would migrate"
    print(
        f"{verb} {result['flows']} flow(s) + {result['workflows']} workflow(s) "
        f"from example: to domain: for: {', '.join(result['names'])}"
    )
    if not apply:
        print("Re-run with --apply to write the changes.")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())

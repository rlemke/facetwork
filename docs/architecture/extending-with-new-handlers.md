# Extending Facetwork with new handlers (for a request that needs new capability)

A playbook for the case where a natural-language request needs a capability **no
existing facet provides, but that could be built**. Written so a Claude landing
fresh on any machine knows exactly what to do. It pairs with:

- [Claude workflow catalog](claude-workflow-catalog.md) — author/run workflows with no file.
- [`use` resolution in the catalog](catalog-use-resolution.md) — why a facet must be a pinned library, not a loose file.
- [`agent-spec/tools-pattern.agent-spec.yaml`](../../agent-spec/tools-pattern.agent-spec.yaml) — the canonical handler/tools/cache contract for a domain package.

## The two kinds of "new capability"

| | New **workflow** (FFL) | New **handler** (Python) |
|---|---|---|
| What | compose existing facets a new way | a genuinely new operation no facet performs |
| Surface | typed, validated, capability-bounded | arbitrary code (I/O, side effects) on the fleet |
| Gate | `fw_catalog_publish` (review the plan) | code review + tests + registration (review the code) |
| Claude autonomy | up to the publish gate | **propose** a reviewed change; never self-deploy |

Most requests are the first kind — try composition first. This doc is about the
second: adding a handler.

## Step 0 — let the gap announce itself

Don't guess whether a capability exists. Compose the FFL and let the tooling tell you:

- **`fw_validate`** (MCP) / `afl <file> --check` — catches an FFL-level gap: a facet
  you referenced isn't declared anywhere (`USE_UNKNOWN_NAMESPACE` / undefined reference).
- **The catalog run preflight** — on `fw_catalog_run` (or `CatalogService.run`), every
  event facet reachable from the entry workflow is checked against the fleet registry;
  a missing/unimportable handler → `CatalogRunBlocked` naming the facet, **before any
  task is posted**. (This is what blocked `osm.Roads.Motorways`, a declared facet with no handler.)
- **`fw ffl scaffold --detect-gaps`** — list the handler gaps for a composed
  workflow offline:

  ```bash
  fw ffl scaffold --detect-gaps --ffl workflow.ffl --entry MyWorkflow \
      --registered "osm.ops.CacheRegion,osm.viz.RenderMap"   # or --registered @facets.txt
  ```

If there's no gap, you're in the "new workflow" case — just author + validate + publish.

## Step 1 — scaffold the facet + handler + test (for review)

```bash
fw ffl scaffold osm.Filters.FilterGeoJSONByTagContains \
    --params  "input_path:String,tag_key:String,substring:String" \
    --returns "result:OSMFilteredFeatures" \
    --doc "Keep GeoJSON features whose tag value CONTAINS substring." \
    --out scaffold/
```

This writes four review stubs (it never edits a package or deploys anything):

- `<Facet>.ffl` — the **`event facet` declaration** (the typed contract). Paste into the
  package's FFL inside the right `namespace`.
- `<facet>_handler.py` — a Python handler whose `handle(payload)` raises
  `NotImplementedError` until filled in, plus a `register_handlers(runner)`.
- `test_<facet>_handler.py` — a contract-test stub.
- `REVIEW.md` — the implement → review → deploy checklist.

## Step 2 — implement, in the right place

Put the **reusable logic** in the package's `_<pkg>_tools/` (e.g. `_osm_tools/`) and call
it from the handler via the `handlers/shared/<domain>_utils.py` shim — per
`agent-spec/tools-pattern.agent-spec.yaml`. The handler reads the FFL params from
`payload`, calls the logic, and returns a dict whose keys match the FFL return clause.
Long, blocking work should call `payload["_task_heartbeat"]` periodically (or register
with `timeout_ms=0` and rely on the global execution timeout). This session's
`extract_roads` and `FilterGeoJSONByTagPrefix` are worked examples.

Wire **registration** to match the package's existing pattern — either keep the stub's
`register_handlers(runner)`, or add the facet to the area's `FACETS` list + dispatch
(see `road_handlers.py` / `filter_handlers.py`). On runner start this writes a
`HandlerRegistration` (`facet_name`, `module_uri=file://…`, `entrypoint`) to the
`handler_registrations` collection, after which the runner advertises and claims the
facet's tasks.

## Step 3 — validate, test, review, commit

- `fw_validate` the package FFL (the new facet resolves against its namespace).
- Run the package's test suite (replace the `NotImplementedError` stub test with a real one).
- **Code review.** This is new executable code on the fleet — the human review gate.
  It is committed to the **handler package** (e.g. `fwh_osm`), not to a catalog entry.

## Step 4 — deploy + expose

- `pip install -e <pkg>` against the runner's venv, then **(re)start the runner** so it
  registers the new handler. Confirm with `fw runner list` or by re-running the
  catalog preflight (the gap is gone).
- `fw ffl catalog import-package <pkg>` to refresh the cataloged library so workflows
  can `use` the new facet (the catalog is hermetic — a facet must be in a pinned library,
  not a loose file: see [`use` resolution](catalog-use-resolution.md)).

## Step 5 — author the workflow

Now compose the FFL that uses the new facet → `fw_validate` → `fw_catalog_save` (with an
authoring `summary`) → review → `fw_catalog_publish`. The preflight now passes; the run
fans out across the fleet as usual.

## The boundary to hold

Claude generates the facet, the handler, the tests, and the workflow. The two **approval
gates stay human**: `publish` for the plan, code review for the handler. New capability
never deploys itself — the `script python` inline escape hatch is being removed precisely
so capability extension goes through this reviewed-handler path. "AI generates, human
reviews, skill seals, fleet executes."

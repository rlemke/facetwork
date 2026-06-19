# Claude workflow catalog

A way for Claude (or any author) to **write, store, version, discover, and run
FFL workflows without a file** — and to re-run them with different parameters
against a frozen, immutable body so an LLM can't inadvertently change the
workflow underneath an existing run configuration.

It is a thin layer over machinery the runtime already has: workflows are
stored as `FlowDefinition`s (not files), the dashboard already renders any
stored flow, and execution is the existing `fw:execute` bootstrap-task path.
The catalog adds discovery metadata, immutable versioning, library
composition, and a review gate.

## Two layers

1. **Runnable layer (reused).** Every saved revision materializes a normal
   `FlowDefinition` + `WorkflowDefinition`(s) under the path
   `claude:<slug>:v<version>` (mirroring the `example:<name>` seed path). This
   gives UI rendering, the runner, repair tooling, and the bootstrap run-path
   for free.
2. **Catalog layer (new).** Two collections:
   - `claude_workflows` — one **`CatalogEntry`** per logical workflow/library
     (stable `slug`, `kind`, `title`, `description`, `tags`, `latest_version`,
     `published_version`). Mutable metadata.
   - `claude_workflow_revisions` — append-only, immutable **`CatalogRevision`**s
     (frozen `ffl_source`, `content_hash`, the materialized `flow_id` +
     entry `workflow_id`, `param_schema`, `facets_used`, pinned `depends_on`,
     `status`, `is_valid`, and a descriptive `summary`).

`facetwork/catalog/`: `entities.py`, `store.py` (`CatalogStore` protocol +
`InMemoryCatalogStore` + `MongoCatalogStore`), `service.py` (`CatalogService`).
The service is storage-agnostic — it takes a `CatalogStore` plus a `flow_store`
(`MongoStore` in prod, an in-memory store in tests) — so the logic is fully
testable offline.

## Immutability & versioning

- **Parameters are runtime inputs, never part of the body.** Changing params
  cannot change the workflow; the catalog records the `param_schema` (read from
  the compiled AST) for the UI form and for callers.
- **Revisions are content-hashed and append-only.** `content_hash =
  sha256(ffl_source + sorted pinned-dep hashes)`. Saving identical content
  **de-dupes** to the existing revision (no version churn); any change creates a
  new version. The old revision — its own `FlowDefinition` — stays runnable
  forever. A run **pins a revision**, so re-running with new inputs always
  re-uses the identical body.
- **The authoring summary travels with the body.** Each revision carries a
  free-text (markdown) `summary` — the request the workflow was built from and
  how it addresses it — recorded by the author (Claude via `fw_catalog_save`'s
  `summary`, or `--summary` / `--summary-file` on `import`) and shown in the
  dashboard, so the workflow can be *understood*, not just read as FFL. It is
  descriptive metadata (not part of the content hash), so refining it on a
  re-save updates it without churning the version.

## Review gate

Each revision has `status: draft | published`. `CatalogService.run` requires a
**published** revision on its default (unattended) path; a draft can only be run
with `allow_unpublished=True` (an explicit attended test). `publish()` refuses
an invalid revision. This keeps LLM-authored drafts from running unattended
until reviewed.

## Run safety — handler preflight & execution isolation

Two further guards on `CatalogService.run`:

- **Handler preflight.** Before posting the bootstrap task, `run` computes the
  event facets transitively reachable from the entry workflow (scoped to the
  entry — *not* every facet in a large pinned library) and, for each, calls
  `RegistryDispatcher.check_loadable`: the facet must be registered, its handler
  module must import, and its entrypoint must be callable. If any fails, `run`
  raises `CatalogRunBlocked` naming the facet and reason — before any task is
  posted — instead of letting the run dead-letter mid-flight on an unimplemented
  or unimportable handler (e.g. `osm.Roads.Motorways`, declared with no handler).
  Skipped when no registry is populated (no runner up yet — can't assess). A
  broken *lazy* import inside a dispatched handler body is **not** caught here:
  handler modules commonly host many facets' handlers behind one dispatch
  entrypoint, so a module-level scan can't attribute a sibling's broken import to
  this facet without false-positives — that surfaces at dispatch.
- **Execution isolation.** Each run mints a **fresh execution `workflow_id`** plus
  a per-run `WorkflowDefinition` (same immutable flow + entry workflow, new uuid).
  `rev.workflow_id` is the *definition* id, shared by every run of the revision;
  reusing it as the execution scope let a prior terminated/failed run's steps
  collide with the next. The fresh id keeps runs independent — mirroring the CLI
  `submit` path, which already generates a fresh `wf_id` per run.

## Library composition

A `kind="library"` entry defines reusable facets/sub-workflows (no entry
point). A workflow `depends_on` libraries **pinned by revision**; at save time
the service merges the pinned library sources + the workflow source into one
compiled program (the same multi-file merge the seeder uses). Because deps are
pinned by revision, evolving a base library never alters an existing dependent —
upgrading is an explicit new dependent revision.

FFL `use` statements resolve **only** against this merged set (own source +
pinned libraries) — the catalog never reads the filesystem or the resolver's
`afl_sources` collection, so a `use` of a file-based namespace must first be
imported as a library and pinned. See
[`use` resolution: file-based vs. the catalog](catalog-use-resolution.md).

## MCP tools (the author-facing API)

On the `agentflow` MCP server:

| Tool | Purpose |
|------|---------|
| `fw_catalog_search` | Find a reusable workflow before authoring (query/tags/facet/kind) |
| `fw_catalog_get` | Inspect an entry + a revision (FFL, params, deps, versions) |
| `fw_catalog_save` | Validate + merge deps + compile → immutable draft revision (no file). Pass `summary` to record *why* the workflow exists (intent / conversation), shown in the UI |
| `fw_catalog_publish` | Review-approve a revision for unattended runs |
| `fw_catalog_run` | Pin a revision + post a `fw:execute` task with inputs |

Typical loop: `fw_catalog_search` → reuse, or `fw_validate` → `fw_catalog_save`
→ (review) `fw_catalog_publish` → `fw_catalog_run` (re-run with new inputs any
time; the body is pinned).

## Backup / restore / import (`fw ffl catalog`)

The catalog is self-describing — each revision carries its FFL, content hash,
version, status, and pinned deps — so a backup is just the entries + revisions as
JSON; the materialized `FlowDefinition`s are NOT backed up (they're regenerable).
`facetwork/catalog/backup.py` + `facetwork/catalog/cli.py`, driven by
`fw ffl catalog`:

```bash
fw ffl catalog list                          # packages + workflows overview
fw ffl catalog backup catalog.json          # dump entries + revisions to JSON
fw ffl catalog restore catalog.json          # restore + rebuild runnable flows
fw ffl catalog import path/to/wf.ffl --slug demo.x --publish   # file -> catalog
fw ffl catalog import examples/dir/ --tags imported            # whole directory
fw ffl catalog import-package osm-geocoder --tags osm          # whole package
```

- **Backup** writes a portable, readable, git-friendly JSON (`format:
  facetwork-catalog-backup`).
- **Restore** writes entries + revisions verbatim — **preserving revision_id,
  version, content_hash, status, and pinned deps** — then recompiles each
  revision to rebuild a runnable flow in the target DB (so the publish gate and
  dependency pins survive a wipe). A revision whose FFL no longer compiles is
  restored as a non-runnable record and reported. `--no-recompile` skips the flow
  rebuild.
- **Import** registers file-based `.ffl` workflows so Claude can discover and run
  them: each file becomes a catalog entry (slug from the file stem or `--slug`),
  validated and (optionally) published. This is how existing file-based workflows
  enter the catalog. Verified end-to-end against MongoDB: import → backup → wipe
  DB → restore → published + runnable.
- **Import-package** brings a whole multi-file FFL package (e.g. `osm-geocoder`,
  84 files / 99 workflows that only compile together via cross-file `use`) into
  the catalog. It merges every `.ffl` once into a single `kind="library"` entry —
  the **one shared flow** holding all the workflows — then creates one thin
  `kind="workflow"` entry per workflow: empty own FFL, a single pinned dependency
  on the library, pointing at its workflow within the shared flow. This keeps the
  catalog at one materialized flow per package instead of one (multi-MB) compiled
  program per workflow. `rematerialize` is shared-flow aware: a thin entry rebinds
  to the library's flow rather than building its own, so backup/restore stays at
  one flow per package too (verified live: 99 osm workflows → 1 `FlowDefinition`,
  preserved across a full backup → restore into a wiped DB). `--also <pkg>` merges
  extra packages for cross-package `use` deps; `--dir` imports a loose directory.

## Dashboard

A **Catalog** page (`/catalog`, `facetwork/dashboard/routes/execution/catalog.py`)
with three modes: a **grouped overview** (packages/libraries with member counts,
standalone workflows, and a per-package workflow tally), a `?package=<slug>`
drill-in listing one package's workflows, and `?q=` ranked search. Per entry it
shows the authoring **summary** (purpose / intent), the revision history with
**Publish** buttons, the parameter schema, pinned library deps, the FFL source, a
link to the materialized compiled flow, links to these design docs, and a **Run**
form (inputs as JSON, with an "allow unpublished"
opt-in) that submits a bootstrap run to the fleet. With a non-Mongo store the page
degrades to an "unavailable" notice. The grouping is backed by
`CatalogService.list_all()` — the same data the `fw ffl catalog list` CLI uses.

## What's not here yet

- Semantic/embedding search (current search is tags + keyword ranking).
- The handler preflight catches a missing / unimportable handler module +
  entrypoint, but a broken *lazy* import inside a dispatched handler body still
  surfaces only at dispatch — it can't be attributed to one facet in a shared
  dispatch module without false-positives.

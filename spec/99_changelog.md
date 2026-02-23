# Implementation Changelog

## Completed (v0.12.74) - Skip orphan tasks when duplicate steps are dropped
- **Root cause**: when `_commit_changes()` silently skipped a duplicate step (Layer 3 `DuplicateKeyError` catch), the associated task was still committed — its `step_id` referenced a phantom step that was never persisted, causing agents to fail with `ValueError: Step not found` on `continue_step()`, which set the runner to `failed` state
- **Fix**: `_commit_changes()` in `MongoStore` now tracks `skipped_step_ids` and filters out any `created_tasks` whose `step_id` references a skipped step, logging a debug message
- **2 new tests** in `test_mongo_store.py`: `test_orphan_task_skipped_when_step_is_duplicate` (task dropped when step is duplicate) + `test_task_committed_when_step_succeeds` (task kept when step commits normally)
- 2 files changed; test suite: 2432 passed, 79 skipped; total collected 2511

## Completed (v0.12.73) - Verify concurrent step dedup with 3 evaluators
- **Integration verification**: ran 3 concurrent `Evaluator.execute()` instances against the same `AnalyzeAllStates` workflow (50 US states, ~3300 steps) — all three competed to create the same steps simultaneously
- **Result**: 3321 total steps (3 root steps × 1 per evaluator + 3318 unique statement steps), **0 duplicate `(statement_id, block_id, container_id)` triples** — all three layers (application checks, unique index, DuplicateKeyError catch) working correctly
- No code changes; test suite unchanged: 2430 passed, 79 skipped; total collected 2509

## Completed (v0.12.72) - Fix concurrent step duplication race condition
- **Three-layer defense** against duplicate step creation when multiple runners call `evaluator.resume()` concurrently:
  1. **Application-level idempotency**: `_create_block_steps()` in `blocks.py` now checks `block_step_exists()` and pending creates before creating block steps; `_process_foreach()` in `block_execution.py` now checks `step_exists()` and pending creates before creating foreach sub-blocks
  2. **Database unique index**: new `step_dedup_index` compound unique index on `(statement_id, block_id, container_id)` with `partialFilterExpression` for non-null `statement_id` — first runner to commit wins
  3. **Commit-time catch**: `_commit_changes()` in `MongoStore` catches `DuplicateKeyError` on step inserts and logs a debug message instead of crashing
- **Normalized block `statement_id`**: single-body andThen blocks now produce `statement_id="block-0"` (was `None`, defeating any index-based dedup); foreach sub-blocks now produce `statement_id="foreach-{i}"` (was missing entirely)
- **New `block_step_exists()` API**: added to `PersistenceAPI` (abstract), `MongoStore` (count_documents query on `statement_id` + `container_id`), and `MemoryStore` (linear scan) — block steps use `container_id` not `block_id` for hierarchy, so need a dedicated check
- **7 new tests**: 3 in `test_mongo_store.py` (block_step_exists round-trip, duplicate step insert silently skipped, different statement_ids allowed) + 4 in `test_evaluator.py` (single-body block gets statement_id, multi-body indexed IDs, foreach indexed IDs, block step idempotency)
- 7 files changed, 437 insertions, 3 deletions; test suite: 2430 passed, 79 skipped; total collected 2509

## Completed (v0.12.71) - Store compiled AST on FlowDefinition to eliminate runner recompilation
- **New `compiled_ast` field** on `FlowDefinition`: stores the full program AST (declarations-format JSON) immutably at flow creation time — runners read it directly instead of recompiling AFL sources on every `resume()`
- **`seed-examples` script updated**: compiles each example's AFL sources and stores the resulting AST in `flow.compiled_ast` during seeding
- **MongoStore round-trip**: `compiled_ast` persisted as a native BSON document in `_flow_to_doc()` / `_doc_to_flow()`
- **5 new tests**: compiled_ast round-trip through MongoStore, legacy flow without compiled_ast, submit endpoint stores compiled_ast
- 5 files changed; test suite: 2423 passed, 79 skipped; total collected 2502

## Completed (v0.12.70) - Deduplicate Continue block processing in the evaluator
- **Dirty-block tracking** in `ExecutionContext`: new `_dirty_blocks: set[StepId] | None` field tracks which block IDs need Continue re-evaluation — `None` = all dirty (first iteration), empty `set()` = nothing dirty, populated set = only those blocks re-evaluated
- **Three helper methods** on `ExecutionContext`: `mark_block_dirty(block_id)`, `is_block_dirty(block_id)`, `mark_block_processed(block_id)` — manage the dirty set lifecycle
- **`_run_iteration()` skip logic**: Continue-state blocks (`BLOCK_EXECUTION_CONTINUE`, `STATEMENT_BLOCKS_CONTINUE`, `MIXIN_BLOCKS_CONTINUE`) are skipped when not in the dirty set; blocks processed with no progress are removed from the dirty set
- **`_process_step()` dirty propagation**: when a step changes state, its `block_id` and `container_id` are marked dirty so parent Continue blocks get re-evaluated in subsequent iterations
- **`resume()` initialization**: first iteration uses `_dirty_blocks=None` (processes all blocks); after first iteration, switches to `set()` seeded from updated steps' parent block/container IDs (before commit clears changes)
- **`resume_step()` initialization**: starts with `_dirty_blocks=set()` and seeds Continue-state blocks from the ancestor chain walk
- **12 new tests** in `TestDirtyBlockTracking`: ExecutionContext helper unit tests (6), _run_iteration skip/process/clean tests (3), _process_step dirty propagation (1), resume() first-iteration semantics (1), resume_step() chain seeding (1)
- 2 files changed, 446 insertions, 1 deletion; test suite: 2418 passed, 79 skipped; total collected 2497

## Completed (v0.12.69) - Extract cache-dependent logic into FromCache facets
- **22 FromCache composition facets** added across two AFL files: `example_routes_visualization.afl` (8 facets) and `osmworkflows_composed.afl` (14 facets, excluding `TransitAnalysis` which has no Cache dependency)
- **Transformation pattern**: each workflow that previously created a Cache and then ran multi-step logic now delegates to a `facet XFromCache(cache: OSMCache)` that accepts the cache directly — the workflow becomes a thin wrapper (`cache = Cache(region = $.region)`, `f = XFromCache(cache = cache.cache)`, `yield`)
- **Extra parameters preserved**: workflows with additional params beyond `region` (e.g. `min_pop`, `output_dir`, `max_concurrent`, `gtfs_url`) pass them through to the FromCache facet, which preserves their defaults
- **`use osm.types` added** to both `examples.routes` and `examples.composed` namespace blocks to resolve the `OSMCache` schema type in FromCache parameter signatures
- **`AnalyzeRegion` workflow updated** (`osm_analyze_states.afl`): now creates a single shared cache and passes it to all 10 `FromCache` facets instead of each sub-workflow creating its own cache independently
- **16 new tests** in `test_osm_composed_workflows.py`: `test_all_from_cache_facets_present`, 14 individual `test_*_from_cache` tests verifying params/steps/returns, and `test_cache_workflows_delegate_to_from_cache` cross-cutting assertion
- **4 test files updated** to match new 2-step workflow structure: `test_osm_composed_workflows.py`, `test_osm_validation.py`, `test_osm_zoom_validation.py`, `test_composed_workflows.py`
- 7 files changed, 477 insertions, 237 deletions; test suite: 2406 passed, 79 skipped; total collected 2485

## Completed (v0.12.68) - Add v2 handlers page with namespace-tabbed list and inline detail
- **5 new handler endpoints** in `routes/dashboard_v2.py` under `/v2` prefix: handler list with namespace-prefix tabs (`GET /v2/handlers`), HTMX partial for 5s auto-refresh (`GET /v2/handlers/partial`), handler detail (`GET /v2/handlers/{facet_name:path}`), detail partial for live refresh (`GET /v2/handlers/{facet_name:path}/partial`), and delete (`POST /v2/handlers/{facet_name:path}/delete`)
- **Namespace-prefix sub-tabs**: dynamically discovered prefixes (first dotted segment of `facet_name`, e.g. `osm` from `osm.geo.Cache`) with per-tab counts — "All" tab shows everything; tab filtering via `_filter_handlers_by_prefix()` and `_count_handlers_by_prefix()` helpers
- **Namespace-group accordion**: handlers grouped by full namespace (all segments except last) using `<details class="ns-group">` — each group shows a table with short facet name, module URI, entrypoint, version, timeout, and registered timestamp
- **Handler detail page**: summary cards (module URI, entrypoint, timeout), two-column layout (details table + actions/requirements/metadata), delete button with HTMX confirm — all with HTMX 5s polling for live updates
- **New helpers** in `helpers.py`: `extract_handler_prefix()` (first dotted segment or `(top-level)`) and `group_handlers_by_namespace()` (groups by full namespace, returns sorted `{"namespace", "handlers", "total"}` dicts) — reuses existing `extract_namespace()` for grouping
- **Nav link updated**: Handlers link in `base.html` More dropdown changed from `/handlers` to `/v2/handlers` with `active_tab` highlighting; old `/handlers` route continues working unchanged
- **4 new templates**: `v2/handlers/list.html`, `v2/handlers/_handler_groups.html`, `v2/handlers/detail.html`, `v2/handlers/_detail_content.html` — reuses existing `.subnav`, `.ns-group`, `.summary-grid`, `.badge` CSS classes
- **28 new tests** in `tests/dashboard/test_handlers_v2.py`: 4 `extract_handler_prefix` tests + 6 `group_handlers_by_namespace` tests + 7 list route tests (empty, tabs, filtering, partial, counts) + 8 detail route tests (found, not found, partial, version, delete, requirements, metadata) + 3 nav tests (v2 link, highlighting, old route)
- 9 files changed, 635 insertions, 5 deletions; test suite: 2390 passed, 79 skipped; total collected 2469

## Completed (v0.12.67) - Add v2 servers page with state-grouped list and inline detail
- **4 new server endpoints** in `routes/dashboard_v2.py` under `/v2` prefix: server list with state tabs (`GET /v2/servers`), HTMX partial for 5s auto-refresh (`GET /v2/servers/partial`), server detail (`GET /v2/servers/{id}`), and detail partial for live ping/state refresh (`GET /v2/servers/{id}/partial`)
- **State sub-tabs**: Running / Startup / Error / Shutdown with per-tab counts — mirrors the workflow page pattern with `_filter_servers()` and `_count_servers_by_tab()` helpers
- **Server group accordion**: servers grouped by `server_group` field using `<details class="ns-group">` — each group shows a table with name, service, state badge, IPs, last ping, and handler count
- **Server detail page**: summary cards (UUID, start time, last ping), two-column layout (details table + topics/handlers lists), handled statistics table, error display — all with HTMX 5s polling for live updates
- **New `group_servers_by_group()` helper** in `helpers.py`: groups servers by `server_group`, returns sorted list of `{"group", "servers", "total"}` dicts — follows same pattern as `group_runners_by_namespace()`
- **Nav link updated**: Servers link in `base.html` changed from `/servers` to `/v2/servers`; old `/servers` route continues working unchanged
- **4 new templates**: `v2/servers/list.html`, `v2/servers/_server_groups.html`, `v2/servers/detail.html`, `v2/servers/_detail_content.html` — reuses existing `.subnav`, `.ns-group`, `.summary-grid`, `.badge` CSS classes
- **21 new tests** in `tests/dashboard/test_servers_v2.py`: 5 helper unit tests (grouping, sorting, empty, single group) + 8 list route tests (empty, tabs, filtering, partial, counts) + 5 detail route tests (found, not found, partial, handlers) + 3 nav tests (v2 link, highlighting, old route)
- 10 files changed, 599 insertions, 6 deletions; test suite: 2362 passed, 79 skipped; total collected 2441

## Completed (v0.12.66) - Redesign dashboard UI with 2-tab nav and namespace-grouped workflows
- **New `/v2/workflows` routes** with 5 endpoints: workflow list with Running/Completed/Failed sub-tabs, HTMX partial for 5s auto-refresh, workflow detail with step sub-tabs (Running/Error/Complete), step table partial, and inline step expansion partial
- **Namespace-grouped runner display**: runners grouped by workflow namespace prefix using `<details>` accordion — each group shows a table with short workflow name, truncated ID, state badge, start time, and duration
- **New `afl/dashboard/helpers.py`** with 4 shared utilities: `extract_namespace()` (splits qualified name on last dot), `short_workflow_name()`, `categorize_step_state()` (maps StepState to running/complete/error), `group_runners_by_namespace()` (groups and sorts runners with per-namespace state counts)
- **Consolidated navigation**: replaced 10-link nav bar with **Workflows** + **Servers** + **More** dropdown (Handlers, Events, Tasks, Locks, Sources, Flows, Runners, Namespaces, New Workflow); added `{% block subnav %}` to `base.html` for sub-tab bars
- **Home redirect**: `GET /` now returns 302 to `/v2/workflows` instead of rendering a summary dashboard
- **3 new Jinja2 filters** in `filters.py`: `short_workflow_name`, `step_category`, `namespace_of`
- **CSS additions** in `style.css`: `.subnav` pill-style tab bar, `.ns-group` accordion styling, `.wf-expand` inline expansion, `.nav-dropdown` hover menu, `.nav-active` highlighting
- **Inline step inspection**: "Details" button on each step row loads params table, returns table, associated task info, and retry button (for error steps) via HTMX into a placeholder row
- **All existing routes unchanged**: `/runners`, `/flows`, `/steps`, `/servers`, etc. continue working at their old URLs
- **33 new tests** in `tests/dashboard/test_dashboard_v2.py`: helper unit tests (extract_namespace, short_workflow_name, categorize_step_state, group_runners_by_namespace) + route integration tests (list, tabs, partials, detail, step expand, home redirect, nav structure, old routes still work)
- 14 files changed, 1027 insertions, 54 deletions; test suite: 2341 passed, 79 skipped

## Completed (v0.12.65) - Reorganize osm-geocoder into functional subdirectories
- **Restructured `examples/osm-geocoder/handlers/`** from a flat directory (~46 modules) into 16 category subpackages plus a `shared/` package: `amenities`, `boundaries`, `buildings`, `cache`, `composed_workflows`, `downloads`, `filters`, `graphhopper`, `parks`, `poi`, `population`, `roads`, `routes`, `shapefiles`, `visualization`, `voting`
- Each category contains its own handler `.py` files, `afl/` subdirectory (42 AFL files total), `tests/` subdirectory (36 test files), and `README.md` (renamed from root-level `.md` docs)
- **Backward-compatible `_AliasImporter` facade** in `handlers/__init__.py`: 41-entry `_MODULE_MAP` with a custom `importlib.abc.MetaPathFinder` that lazily redirects old flat imports (e.g. `from handlers.cache_handlers import REGION_REGISTRY`) to new subpackage paths (`handlers.cache.cache_handlers`) — only activates when the active `handlers` package is from osm-geocoder
- **Shared utilities** moved to `handlers/shared/`: `_output.py`, `downloader.py`, `region_resolver.py`
- **Cross-category imports** updated: `operations_handlers.py` → `from ..cache.cache_handlers import REGION_REGISTRY`; `region_resolver.py` → same pattern
- **pytest cross-example isolation**: root `conftest.py` patches `_pytest.python.importtestmodule` to purge stale `handlers.*` modules immediately before each osm-geocoder module import — fixes collection conflicts where genomics/jenkins handlers would shadow osm-geocoder handlers
- **Updated `scripts/seed-examples`** `discover_examples()`: recursive AFL glob now deduplicates by `os.path.realpath()` and excludes files resolving outside the example root (symlinks) and files inside `tests/` directories (test fixtures)
- **continental-lz symlink** preserved: `collect_ignore_glob` in `examples/continental-lz/conftest.py` prevents test collection from symlinked handler tests
- 181 files changed, 930 insertions, 156 deletions; test suite unchanged: 2308 passed, 79 skipped

## Completed (v0.12.64) - Add resume_step() for O(depth) step resumption, fix concurrent resume loss
- **Three performance/correctness fixes** for large-scale workflow execution (500K+ steps):
  1. **AgentPoller concurrent resume fix**: `_resume_workflow()` non-blocking lock was silently dropping resumes when contended — added `_resume_pending` set so the lock holder re-runs after its iteration completes, ensuring no step transitions are lost
  2. **`get_actionable_steps_by_workflow()`**: new method on `PersistenceAPI`, `MemoryStore`, and `MongoStore` that filters out terminal (`Complete`/`Error`) and non-transitioning `EventTransmit` steps at the DB level — `MongoStore` uses a `$nor` query; reduces evaluator iteration scope from all steps to only actionable ones
  3. **`Evaluator.resume_step()`**: focused single-step resume that walks the continued step's container+block chain with iterative commit until fixed point — O(depth) instead of O(total_steps); `AgentPoller._do_resume()` now calls `resume_step()` when a `step_id` is available, falls back to full `resume()` for pending re-runs
- **Docker env var passthrough**: added `AFL_CACHE_DIR` and `AFL_GEOFABRIK_MIRROR` to `docker-compose.yml` for `runner`, `agent-osm-geocoder`, and `agent-osm-geocoder-lite` services — without these, agents couldn't locate PBF files in HDFS and fell back to failed Geofabrik downloads
- **Added `AFL_CACHE_DIR`** to `.env.example` (commented) and `.env`
- Files changed: `afl/runtime/evaluator.py`, `afl/runtime/agent_poller.py`, `afl/runtime/persistence.py`, `afl/runtime/mongo_store.py`, `afl/runtime/memory_store.py`, `docker-compose.yml`, `.env.example`

## Completed (v0.12.63) - Write OSM extractor output to HDFS via AFL_OSM_OUTPUT_BASE
- **New helper module `examples/osm-geocoder/handlers/_output.py`** with `resolve_output_dir(category)`, `open_output(path)`, and `ensure_dir(path)` — routes extractor output to HDFS when `AFL_OSM_OUTPUT_BASE` is set (e.g. `hdfs://namenode:8020/osm-output`), unchanged local `/tmp/` behavior when unset
- **Updated 10 extractor handlers** to use the shared output helpers instead of hardcoded local paths and direct `open()` / `_storage.open()` calls:
  - `boundary_extractor.py` → `osm-boundaries/` category
  - `park_extractor.py` → `osm-parks/` category
  - `route_extractor.py` → `osm-routes/` category
  - `building_extractor.py` → `osm-buildings/` category
  - `amenity_extractor.py` → `osm-amenities/` category
  - `road_extractor.py` → `osm-roads/` category
  - `osm_type_filter.py` → `osm-filtered/` category
  - `population_filter.py` → `osm-population/` category
  - `osmose_verifier.py` → `osm-osmose/` category
  - `zoom_graph.py` — `RoadGraph.save()` uses `open_output()` + `ensure_dir()`
- **Added `AFL_OSM_OUTPUT_BASE`** to `.env.example` and `.env` (active, `hdfs://namenode:8020/osm-output`)
- **Passed `AFL_OSM_OUTPUT_BASE` to Docker containers** in `docker-compose.yml`: added to `runner`, `agent-osm-geocoder`, and `agent-osm-geocoder-lite` services via `${AFL_OSM_OUTPUT_BASE:-}` interpolation — without this, the env var was only on the host and extractors kept writing to local `/tmp/`
- HDFS directory creation handled automatically via `ensure_dir()` calling `backend.makedirs()`; local paths use `Path.mkdir(parents=True)`
- **End-to-end verified**: submitted `StateBoundariesWithStats(region="Delaware")` workflow — Cache found PBF in HDFS, boundary extractor wrote 86 KB GeoJSON to `hdfs://namenode:8020/osm-output/osm-boundaries/delaware-latest.osm_admin4.geojson`, confirmed valid FeatureCollection with Delaware state boundary (admin_level 4)

## Completed (v0.12.62) - Use .env data dirs in run_osm scripts
- **Fixed `run_osm_cache_states.sh` and `run_osm_analyze_states.sh`** to use `HDFS_NAMENODE_DIR`, `HDFS_DATANODE_DIR`, and `MONGODB_DATA_DIR` from `.env` (via `_env.sh`) instead of hardcoding `~/data/hdfs/*` and `~/data/mongodb`
- **MongoDB data dir is now optional**: when `MONGODB_DATA_DIR` is unset/empty, the scripts skip `--mongodb-data-dir` and MongoDB uses a Docker volume — avoids WiredTiger "Operation not permitted" crashes from bind-mounted directories

## Completed (v0.12.61) - Make poll_interval_ms configurable via AFL_POLL_INTERVAL_MS
- **All runner configs** (`AgentPollerConfig`, `RegistryRunnerConfig`, `RunnerConfig`, `AgentConfig`) now read `AFL_POLL_INTERVAL_MS` env var with a default of **1000 ms** (was hardcoded 2000 ms)
- **Runner CLI** `--poll-interval` flag respects the env var as its default

## Completed (v0.12.60) - Make max_concurrent configurable and add progress logging
- **`AFL_MAX_CONCURRENT` env var**: all runner configs (`AgentPollerConfig`, `RegistryRunnerConfig`, `RunnerConfig`, `AgentConfig`) now read this env var with a default of **2** (was hardcoded 5); runner CLI `--max-concurrent` flag also respects the env var
- **Progress logging in `downloader.py`**: cache-hit/miss/seed events with human-readable sizes; progress every 100 MB during mirror-to-HDFS copies and HTTP downloads
- **HDFS upload logging in `storage.py`**: `_WebHDFSWriteStream` logs upload start/complete with size in MB

## Completed (v0.12.59) - Fix HDFS PBF file access for pyosmium extractors
- **Added `localize()` function to `afl/runtime/storage.py`**: streams HDFS files to a local cache directory (`/tmp/osm-local/`) via WebHDFS OPEN with 64 KB chunked streaming; skips re-download when a local copy with matching byte-size already exists; returns local paths unchanged
- **Fixed all 11 OSM extractors** to use `localize()` before calling pyosmium `apply_file()`: `boundary_extractor.py`, `park_extractor.py`, `route_extractor.py`, `building_extractor.py`, `amenity_extractor.py`, `road_extractor.py`, `osm_type_filter.py`, `population_filter.py`, `osmose_verifier.py`, `zoom_graph.py`, `postgis_importer.py`
- **Fixed `boundary_extractor.py` existence check**: replaced `Path(pbf_path).exists()` (local filesystem only) with `get_storage_backend(path).exists(path)` (works with both local and HDFS paths)
- **Root cause**: Cache steps return HDFS URIs (`hdfs://namenode:8020/osm-cache/...`), but all extractors converted these to `Path` objects — `Path.exists()` always returned `False` for HDFS URIs, and pyosmium cannot read HDFS URIs directly

## Completed (v0.12.58) - Fix route visualization workflows to use Cache(region) parameter
- **Replaced 8 hardcoded `osm.geo.cache.Europe.Liechtenstein()` calls** with `Cache(region = $.region_name)` in `examples/osm-geocoder/afl/example_routes_visualization.afl` — all 8 workflows (`BicycleRoutesMap`, `HikingTrailsMap`, `TrainRoutesMap`, `BusRoutesMap`, `PublicTransportMap`, `BicycleRoutesWithStats`, `HikingTrailsWithStats`, `NationalCycleNetwork`) now use their `region_name` parameter instead of ignoring it

## Completed (v0.12.57) - Fix genomics.afl parse error
- **Reordered comments in `examples/genomics/afl/genomics.afl`**: moved `//` line comments before `/** */` doc comments on both `SamplePipeline` and `CohortAnalysis` workflows — doc comments must be immediately followed by a declaration keyword, not separated by other comments

## Completed (v0.12.56) - Fix easy.sh --clean flag causing setup to exit early
- **Removed `--clean` from `SETUP_ARGS`** in `scripts/easy.sh`: `scripts/setup --clean` exits after cleaning without starting containers, so `--clean --build` together skipped the build and start phases entirely
- Since `easy.sh` already runs `scripts/teardown --all` first, the `--clean` flag was redundant

## Completed (v0.12.55) - Document .env.example and _env.sh configuration workflow
- **Added "Environment Configuration" section to `spec/90_nonfunctional.md`**: documents the `.env.example` → `.env` → `_env.sh` pipeline, how `scripts/easy.sh` translates env vars to CLI flags, precedence rules (CLI flags > env vars > `.env` > defaults), and a full variable reference table grouped by category (MongoDB, Scaling, Overlays, Data directories)
- **Updated convenience scripts listing** in `spec/90_nonfunctional.md`: added `scripts/_env.sh` (shared env loader) and `scripts/easy.sh` (one-command pipeline)
- **Added "Environment configuration" note to `CLAUDE.md`** after "Quick commands" so contributors can discover the `.env` workflow without reading the full spec

## Completed (v0.12.54) - Extract run_agent() helper to eliminate example agent.py duplication
- **New module `afl/runtime/agent_runner.py`** with `AgentConfig` dataclass, `make_store()` public helper, and `run_agent()` bootstrap function that encapsulates store creation, evaluator setup, signal handling, and the RegistryRunner/AgentPoller branching logic
- **Exported** `AgentConfig`, `make_store`, `run_agent` from `afl/runtime/__init__.py`
- **Rewrote 5 example `agent.py` files** to use `run_agent()`: `genomics`, `aws-lambda`, `jenkins`, `osm-geocoder`, `continental-lz` — each reduced from ~100 lines to ~20-45 lines
- **Updated `examples/maven/agent.py`** to use `make_store()` (maven uses a custom `MavenArtifactRunner`, so it keeps its own startup logic)
- **Added `tests/runtime/test_agent_runner.py`** with 9 tests: `make_store` memory/MongoDB/database-precedence, `AgentConfig` defaults/custom, `run_agent` registry/poller/topics/config-forwarding

## Completed (v0.12.53) - Remove normalize calls, wire implicit defaults, spec cleanup
- **Removed 13 no-op `normalize_program_ast()` calls** from all AST entry points: `submit.py`, `service.py` (2 sites), `registry_runner.py`, `agent_poller.py`, `server.py` (3 sites), `steps.py`, `flows.py` (2 sites), `workflows.py` (2 sites). The function itself is preserved in `afl/ast_utils.py` and `afl/__init__.py` for external/legacy JSON consumers.
- **Wired implicit declaration defaults into the runtime**:
  - Added `get_implicit_args()` and `_search_implicit_declarations()` to `ExecutionContext` in `afl/runtime/evaluator.py` — scans program AST for `ImplicitDecl` nodes matching a facet name
  - Added implicit default resolution in `FacetInitializationBeginHandler.process_state()` in `afl/runtime/handlers/initialization.py` — applies between explicit args and facet defaults (priority: explicit > implicit > facet default)
  - Added 4 tests in `tests/runtime/test_evaluator.py::TestImplicitDefaults`: provides default, explicit overrides implicit, implicit overrides facet default, no-implicit-no-effect
- **Added implicit validation** in `afl/validator.py`:
  - `_validate_implicit_decl()` checks that the implicit's call target references a known facet and that call args match target facet parameters
  - Called from both `_validate_program()` and `_validate_namespace()`
  - Added 3 tests in `tests/test_validator.py::TestImplicitValidation`
- **Updated spec files** for declarations-only format:
  - `spec/20_compiler.md`: added "Declarations-Only Output Format" subsection under JSON Emitter
  - `spec/11_semantics.md`: added note clarifying Python AST vs JSON serialization
  - `spec/90_nonfunctional.md`: updated JSON Format Stability section

## Completed (v0.12.52) - Remove Categorized Keys from Emitter and Consumers
- **Emitter no longer emits categorized keys**: removed `namespaces`, `facets`, `eventFacets`, `workflows`, `implicits`, `schemas` from `_program()` and `_namespace()` in `afl/emitter.py`; only the unified `declarations` list is emitted
- **Implicits now included in declarations**: both `_program()` and `_namespace()` now add `ImplicitDecl` nodes to the `declarations` list (previously omitted from declarations but present in categorized `implicits` key)
- **Simplified `find_workflow`/`find_all_workflows`** in `afl/ast_utils.py`: removed all categorized-key fallback paths from `_find_simple()`, `_find_qualified()`, `_search_namespace_workflows()`, and `_collect_workflows()`; these functions now only iterate `declarations`
- **`normalize_program_ast` / `_normalize_node` unchanged**: backward-compat paths for old/external JSON are preserved
- **Updated 10 test files** to use declarations-based lookups instead of categorized keys:
  - `tests/test_emitter.py`: added `_decls_by_type()`/`_first_decl()` helpers; replaced ~87 categorized key accesses
  - `tests/test_ast_utils.py`: converted fixtures to declarations format; removed 7 dead-path tests
  - `tests/test_cli.py`: 4 accesses updated
  - `tests/test_source.py`: 3 accesses updated
  - `examples/jenkins/tests/.../test_jenkins_compilation.py`: rewrote `_collect_names()` helper
  - `examples/maven/tests/.../test_maven_compilation.py`: rewrote `_collect_names()` and `_find_event_facet()` helpers
  - `examples/aws-lambda/tests/.../test_aws_lambda_compilation.py`: rewrote `_collect_names()` helper
  - `examples/osm-geocoder/tests/.../test_osm_composed_workflows.py`: rewrote `_find_wf()` helper
  - `examples/osm-geocoder/tests/.../test_osm_validation.py`: rewrote all inline search helpers
  - `examples/osm-geocoder/tests/.../test_osm_zoom_validation.py`: rewrote all inline search helpers
- **Updated 2 example scripts**:
  - `examples/hello-agent/run.py`: uses `find_all_workflows()` instead of `compiled["workflows"][0]`
  - `examples/continental-lz/scripts/seed.py`: iterates `declarations` for workflow extraction
- Test count: 2290 passed, 79 skipped (7 dead-path tests removed from v0.12.51's 2297)

## Completed (v0.12.51) - Wire normalize_program_ast at All Entry Points
- Wired `normalize_program_ast()` at all 13 AST ingestion points so downstream code always receives declarations-only dicts:
  - `afl/runtime/submit.py` (after JSON parse)
  - `afl/runtime/runner/service.py` (execute path + resume path — 2 sites)
  - `afl/runtime/registry_runner.py` (AST load path)
  - `afl/runtime/agent_poller.py` (AST load path)
  - `afl/mcp/server.py` (`_tool_compile`, `_tool_execute_workflow`, `_tool_resume_workflow` — 3 sites)
  - `afl/dashboard/routes/flows.py` (`flow_run_detail`, `flow_run_execute` — 2 sites)
  - `afl/dashboard/routes/workflows.py` (`workflow_new`, `submit_workflow` — 2 sites)
  - `afl/dashboard/routes/steps.py` (step name lookup)
- Cleaned up evaluator dual-format workaround: removed `+ decl.get("eventFacets", [])` concatenation in `evaluator.py:427` — after normalization, `eventFacets` is always folded into `declarations`
- Added 2 new tests in `tests/test_ast_utils.py::TestNormalizeNamespaceEventFacets`:
  - `test_namespace_eventfacets_moved_to_declarations`: unit test confirming eventFacets are folded into declarations
  - `test_compile_event_facet_normalize_roundtrip`: integration test compiling AFL with event facets, normalizing, verifying structure

## Completed (v0.12.50) - AST Format Normalization and Shared find_workflow
- Added `afl/ast_utils.py` with three public functions:
  - `normalize_program_ast()`: strips categorized keys (`namespaces`, `facets`, `eventFacets`, `workflows`, `implicits`, `schemas`), keeps `declarations` as the single source of truth; builds `declarations` from categorized keys if missing; recursively normalizes namespace nodes; idempotent and non-mutating
  - `find_workflow()`: finds a WorkflowDecl by simple or qualified name; supports flat namespace match, nested navigation, and recursive search; works on both normalized and unnormalized input
  - `find_all_workflows()`: collects all WorkflowDecl nodes including inside namespaces; deduplicates when both formats present
- Exported all three functions from `afl/__init__.py`
- Replaced 5 duplicate `_find_workflow` implementations (~254 lines removed) with thin wrappers delegating to `afl.ast_utils.find_workflow`:
  - `afl/mcp/server.py`
  - `afl/runtime/submit.py` (+ removed `_search_namespace_workflows`)
  - `afl/runtime/registry_runner.py`
  - `afl/runtime/agent_poller.py`
  - `afl/runtime/runner/service.py` (+ removed `_search_namespace_workflows`)
- Updated dashboard routes to use shared utilities:
  - `afl/dashboard/routes/flows.py`: imports `find_workflow` from `afl.ast_utils`
  - `afl/dashboard/routes/workflows.py`: uses `find_all_workflows` and `find_workflow`
  - `afl/dashboard/routes/steps.py`: uses `find_all_workflows`
- Updated test helpers to delegate to `afl.ast_utils.find_workflow`:
  - `tests/test_lifecycle_integration.py`
  - `examples/jenkins/tests/.../test_jenkins_compilation.py`
  - `examples/aws-lambda/tests/.../test_aws_lambda_compilation.py`
- Added `tests/test_ast_utils.py` with 29 tests covering normalize, find_workflow, find_all_workflows, and compile-normalize-find round-trips

## Completed (v0.12.49) - Add AnalyzeRegion and AnalyzeAllStates Workflows
- Added `osm_analyze_states.afl` with two workflows in namespace `osm.geo.UnitedStates.analysis`:
  - `AnalyzeRegion(region)`: runs 10 composed workflows (VisualizeBicycleRoutes, AnalyzeParks, LargeCitiesMap, TransportOverview, NationalParksAnalysis, CityAnalysis, TransportMap, StateBoundariesWithStats, DiscoverCitiesAndTowns, RegionalAnalysis) for a single region
  - `AnalyzeAllStates()`: calls AnalyzeRegion for all 50 US states plus DC (51 steps, each expanding to 10 sub-workflow calls)
- Added `run_osm_analyze_states.sh` convenience script: sets up Docker stack, compiles with all OSM library files, and submits the workflow

## Completed (v0.12.48) - Rename osmstates30 to osm_cache_states with All 50 States
- Renamed `osmstates30.afl` → `osm_cache_states.afl`; expanded from 30 states to all 50 US states plus DC
- Renamed workflow `Download30States` → `DownloadAllStates` (namespace `osm.geo.UnitedStates.cache`)
- Renamed `run_30states.sh` → `run_osm_cache_states.sh` with updated AFL path, output path, and workflow name
- Removed tracked `osmstates30.json` compiled artifact; updated `.gitignore` for new output filename

## Completed (v0.12.47) - Replace Region-Specific Workflows with Generic CityAnalysis
- Removed `GermanyCityAnalysis` and `FranceCityAnalysis` (hardcoded regions, no parameters)
- Added `CityAnalysis(region: String, min_population: Long)` — generic replacement using `Operations.Cache`
- Fixed `NationalParksAnalysis` — added missing `region: String = "Liechtenstein"` parameter
- Updated mocked and real integration tests; updated `COMPOSED_WORKFLOWS.md` Pattern 6

## Completed (v0.12.46) - Refactor Scripts with Shared .env Configuration
- Added `.env.example` template with all configurable settings (MongoDB, scaling, overlays, external data dirs)
- Added `scripts/_env.sh` shared helper: loads `.env` without overriding already-set env vars, exports `_compute_compose_args()` for overlay-aware compose file/profile computation
- Added `.env` and `.afl-active-config` to `.gitignore`
- Refactored `scripts/setup`: defaults now read from env vars (`AFL_RUNNERS`, `AFL_AGENTS`, `AFL_OSM_AGENTS`, etc.); writes `.afl-active-config` after computing overlay state
- Refactored `scripts/rebuild`: overlay-aware build and `--up` — reads `.afl-active-config` or `.env` so containers start with correct compose files (mirror, HDFS, PostGIS); fixes bug where `rebuild --up` started containers without overlay mounts
- Refactored `scripts/teardown`: sources `_env.sh` to populate `AFL_GEOFABRIK_MIRROR` from `.env`
- Refactored `scripts/easy.sh`: reads all config from `.env` instead of hardcoding `/Volumes/afl_data/osm`, `3 runners`, `4 osm-agents`
- Added `source _env.sh` to `seed-examples`, `run-workflow`, `publish`, `db-stats`, `server`, `runner` for consistent MongoDB connection via `.env`

## Completed (v0.12.45) - Add Step Logging to AgentPoller and Example Handlers
- Added `_emit_step_log()` method to AgentPoller (mirrors RegistryRunner pattern)
- AgentPoller now emits framework-level step logs: task claimed, dispatching, handler completed, handler error
- AgentPoller injects `_step_log` callback into handler payloads for handler-level logging
- Updated all example handler files to use `_step_log` for operational messages visible in the dashboard:
  - `docker/agents/addone_agent.py`: AddOne, Multiply, Greet handlers
  - `examples/osm-geocoder/agent.py`: geocode_handler
  - `examples/osm-geocoder/handlers/`: region, operations, cache, park, building, road, poi, amenity, boundary, elevation, filter, graphhopper, gtfs, osmose, population, route, tiger, validation, visualization, zoom, airquality, postgis handlers
  - `examples/genomics/handlers/`: cache, genomics, index, operations, resolve handlers
  - `examples/jenkins/handlers/`: build, artifact, deploy, notify, scm, test handlers
  - `examples/aws-lambda/handlers/`: lambda, stepfunctions handlers
  - `examples/maven/handlers/`: runner handlers
- Added 3 new AgentPoller step log tests: test_step_logs_on_success, test_step_logs_on_failure, test_step_log_callback_injection

## Completed (v0.12.44) - Merge EventDefinition into TaskDefinition
- Removed EventDefinition dataclass, EventState, EVENT_TRANSITIONS, EventManager, EventDispatcher, LocalEventHandler
- Removed EventError, EventId, event_id(), EventDefinitionDAO
- Removed events MongoDB collection and all indexes
- Removed events from IterationChanges (created_events, updated_events)
- Removed get_event()/save_event() from PersistenceAPI
- Consolidated dashboard /events page to query tasks instead of events
- Removed event_count from dashboard home page
- Updated API /api/events endpoint to return tasks with event-compatible field names
- Tasks already contain all needed fields: name (event_type), data (payload), step_id, workflow_id

## Completed (v0.1.0) - Compiler
- Lark LALR grammar for AFL syntax
- AST dataclasses for all language constructs
- Parser with line/column error reporting
- JSON emitter with optional source locations
- Semantic validator (name uniqueness, references, yields)
- CLI for file and stdin parsing with validation

## Completed (v0.2.0) - Runtime
- State machine with 20+ states for step execution
- Three state changers (Step, Block, Yield) with transition tables
- Iterative evaluator with atomic commits at iteration boundaries
- Dependency-driven step creation from AST
- Expression evaluation (InputRef, StepRef, BinaryExpr, ConcatExpr)
- In-memory persistence for testing
- MongoDB persistence with auto-created collections and indexes
- Configurable database name for developer/test isolation
- Event lifecycle management
- Structured telemetry logging
- Spec examples 21.1 and 21.2 passing

## Completed (v0.3.0) - Dashboard
- FastAPI + Jinja2 + htmx web dashboard for monitoring workflows
- Summary home page with runner/server/task counts
- Runner list (filterable by state), detail with steps/logs/params
- Step detail with attributes (params/returns), retry action
- Flow list, detail, AFL source view with syntax highlighting, compiled JSON view
- Server status, log viewer, task queue pages
- Action endpoints: cancel/pause/resume runners, retry failed steps
- JSON API endpoints for programmatic access
- htmx auto-refresh (5s polling) for live step/runner state updates
- 361 tests passing

## Completed (v0.4.0) - Distributed Runner Service
- RunnerService: long-lived process polling for blocked steps and pending tasks
- Distributed locking via MongoDB for coordinated multi-instance execution
- ToolRegistry dispatch with continue_step and workflow resume
- Server registration with heartbeat and handled-count stats
- ThreadPoolExecutor for concurrent work item processing
- Per-work-item lock extension threads
- Graceful shutdown with SIGTERM/SIGINT signal handling
- RunnerConfig dataclass for all tunable parameters
- CLI entry point (python -m afl.runtime.runner) with argparse
- get_steps_by_state() persistence API extension
- Embedded HTTP status server (`/health`, `/status`) with auto-port probing

## Completed (v0.4.1) - Event Task-Queue Architecture
- Qualified facet names: steps store `"namespace.FacetName"` for namespaced facets
- `resolve_qualified_name()` on `ExecutionContext` and `DependencyGraph`
- `get_facet_definition()` supports both qualified (`ns.Facet`) and short (`Facet`) lookups
- Task creation at EVENT_TRANSMIT: `EventTransmitHandler` creates `TaskDefinition` in task queue
- Tasks committed atomically alongside steps and events via `IterationChanges.created_tasks`
- `claim_task()` on `PersistenceAPI`: atomic PENDING -> RUNNING transition
- MemoryStore `claim_task()` with `threading.Lock` for atomicity
- MongoStore `claim_task()` via `find_one_and_update()` with compound index
- Partial unique index `(step_id, state=running)` ensures one agent per event step
- RunnerService claims event tasks from task queue instead of polling step state
- `_process_event_task()`: dispatch -> continue_step -> resume -> mark completed/failed
- `runner_id` propagated from RunnerService through Evaluator to task creation
- `--topics` accepts qualified facet names (e.g. `ns.CountDocuments`)
- 618 tests passing

## Completed (v0.5.0) - MCP Server
- MCP server exposing AFL compiler and runtime to LLM agents
- 6 tools: compile, validate, execute_workflow, continue_step, resume_workflow, manage_runner
- 10 resources: runners, steps, flows, servers, tasks (with sub-resources)
- stdio transport (standard for local MCP servers)
- Reusable serializers module for entity-to-dict conversion
- CLI entry point (python -m afl.mcp) with argparse
- 599 tests passing

## Completed (v0.5.1) - Agent Poller Library
- `Evaluator.fail_step(step_id, error_message)`: marks event-blocked step as STATEMENT_ERROR
- `AgentPollerConfig` dataclass: service_name, server_group, task_list, poll_interval_ms, max_concurrent, heartbeat_interval_ms
- `AgentPoller` class: standalone event polling library for building AFL Agent services
- `register(facet_name, callback)`: register callbacks for qualified event facet names
- `start()` / `stop()`: blocking poll loop with server registration and heartbeat
- `poll_once()`: synchronous single cycle for testing (no thread pool)
- Short name fallback: qualified task name `ns.Facet` matches handler registered as `Facet`
- AST caching: `cache_workflow_ast()` / `_load_workflow_ast()` for workflow resume
- Error handling: callback exceptions -> `fail_step()` + task FAILED
- RunnerService updated to call `fail_step()` on event task failure
- AddOne agent integration test: end-to-end event facet handling with `handlers.AddOne`
- 639 tests passing

## Completed (v0.5.2) - Schema Declarations
- `schema` declarations at top level and inside namespaces
- `SchemaDecl`, `SchemaField`, `ArrayType` AST nodes
- Array types `[Type]` supported in schema fields and parameter signatures
- Schema names usable as types in facet/workflow parameters
- Schema name uniqueness validation (same pool as facets/workflows)
- Schema field name uniqueness validation
- JSON emitter support for schemas and array types
- 660 tests passing

## Completed (v0.6.0) - Scala Agent Library
- sbt project under `agents/scala/afl-agent/` with Scala 3.3.x
- `Protocol` object: constants matching `agents/protocol/constants.json`
- `AgentPollerConfig` case class with `fromConfig`/`resolve`/`fromEnvironment`
- `AgentPoller` class: poll loop, handler registration, lifecycle (mirrors Python AgentPoller)
- `MongoOps` class: claimTask, readStepParams, writeStepReturns, markTaskCompleted/Failed, insertResumeTask
- `ServerRegistration` class: register/deregister/heartbeat via MongoDB
- BSON model codecs: `TaskDocument`, `StepAttributes`, `ServerDocument` with fromBson/toBson
- Short name fallback: qualified task name `ns.Facet` matches handler registered as `Facet`
- `afl:resume` protocol task insertion for Python RunnerService workflow resumption
- 42 tests passing (protocol constants verification, serialization round-trips, poller unit tests)

## Completed (v0.5.3) - OSM Geocoder Example
- 22 AFL source files defining geographic data processing workflows
- `OSMCache` and `GraphHopperCache` schema declarations (`osmtypes.afl`)
- ~250 cache event facets across 11 geographic namespaces (`osmcache.afl`)
- 13 operations event facets: Download, Tile, RoutingGraph, Status, PostGisImport, DownloadShapefile (plus *All variants) (`osmoperations.afl`)
- 8 POI event facets: POI, Cities, Towns, Suburbs, Villages, Hamlets, Countries (`osmpoi.afl`)
- **GraphHopper routing graph integration**:
  - 6 operation facets: BuildGraph, BuildMultiProfile, BuildGraphAll, ImportGraph, ValidateGraph, CleanGraph (`osmgraphhopper.afl`)
  - ~200 per-region cache facets across 9 namespaces (`osmgraphhoppercache.afl`)
  - Regional workflow compositions: BuildMajorEuropeGraphs, BuildNorthAmericaGraphs, BuildWestCoastGraphs, BuildEastCoastGraphs (`osmgraphhopper_workflows.afl`)
  - `recreate` flag parameter to control graph rebuilding (default: use cached graph if exists)
  - Supports routing profiles: car, bike, foot, motorcycle, truck, hike, mtb, racingbike
- City-to-city pairwise routing workflow (`osmcityrouting.afl`): 9-step pipeline composing ResolveRegion -> Download -> BuildGraph -> ValidateGraph -> ExtractPlacesWithPopulation -> FilterByPopulationRange -> PopulationStatistics -> ComputePairwiseRoutes -> RenderLayers
- Regional workflows composing cache lookups with download operations (Africa, Asia, Australia, Canada, Central America, Europe, North America, South America, United States, Continents)
- Shapefile download workflow for Europe (`osmshapefiles.afl`)
- World workflow orchestrating all regional workflows (`osmworld.afl`)
- Python handler modules with Geofabrik URL registry and factory-pattern handler generation
- Multi-format downloader: `download(region, fmt="pbf"|"shp")` supports PBF and Geofabrik free shapefiles
- `register_all_handlers(poller)` for batch registration of ~480+ event facet handlers
- **Region name resolution** (`osmregion.afl`, `region_resolver.py`, `region_handlers.py`):
  - Resolves human-friendly names ("Colorado", "UK", "the Alps") to Geofabrik download paths
  - ~280 indexed regions, ~78 aliases, 25 geographic features
  - `prefer_continent` disambiguation for ambiguous names (e.g. Georgia)
  - 3 event facets: `ResolveRegion`, `ResolveRegions`, `ListRegions`
  - 3 composed region-based workflows
  - 20+ end-to-end pipeline examples with mock handlers
  - 80 unit tests for resolver
- 879+ tests passing (main suite) + 80 region resolver tests

## Completed (v0.7.0) - LLM Integration & Multi-Language Agents
- **Async AgentPoller**: `register_async()` for async/await handlers (LLM integration)
- **Partial results**: `update_step()` for streaming handler output
- **Prompt templates**: `prompt {}` block syntax for LLM-based event facets
  - `system`, `template`, `model` directives
  - Placeholder validation (`{param_name}` must match facet parameters)
- **Script blocks**: `script {}` block syntax for inline Python execution
  - Sandboxed execution with restricted built-ins
  - `params` dict for input, `result` dict for output
- **Source loaders**: MongoDB and Maven source loading implemented
  - `SourceLoader.load_mongodb(collection_id, display_name)` for flows collection
  - `SourceLoader.load_maven(group_id, artifact_id, version, classifier)` for Maven Central
  - CLI: `--mongo UUID:Name` and `--maven group:artifact:version[:classifier]`
- **Go agent library** (`agents/go/afl-agent/`)
- **TypeScript agent library** (`agents/typescript/afl-agent/`)
- **Java agent library** (`agents/java/afl-agent/`)
- 879 tests passing

## Completed (v0.7.1) - Live Integration Examples
- Integration test infrastructure (`examples/osm-geocoder/integration/`)
- AddOne, region resolution, population pipeline, and city routing integration tests
- `ComputePairwiseRoutes` handler with GraphHopper API and great-circle fallback
- Tests require `--mongodb` flag, optional deps auto-skipped with `pytest.importorskip`

## Completed (v0.8.0) - Expressions, Collections & Multiple Blocks
- **BinaryExpr**: arithmetic operators `+`, `-`, `*`, `/`, `%` with correct precedence hierarchy
- **Statement-level andThen body**: steps can have inline `andThen` blocks for sub-workflows
- **Multiple andThen blocks**: facets/workflows support multiple concurrent `andThen` blocks
- **Collection literals**: array `[expr, ...]`, map `#{"key": expr, ...}`, indexing `expr[expr]`, grouping `(expr)`
- **Expression type checking**: lightweight type inference catches string+int and bool+arithmetic errors at compile time
- Grammar: `postfix_expr` for indexing, `additive_expr`/`multiplicative_expr` for arithmetic precedence
- AST: `BinaryExpr`, `ArrayLiteral`, `MapEntry`, `MapLiteral`, `IndexExpr` nodes
- Runtime: expression evaluation and dependency extraction for all new expression types
- Validator: `_extract_references` recursive walker, `_infer_type` type checker
- 963 tests passing

## Completed (v0.8.1) - Multi-Block Fix, Foreach Execution & Iteration Traces
- **Multi-block AST resolution fix**: workflows with multiple `andThen` blocks now track block body index via `statement_id="block-N"`; `get_block_ast()` selects the correct body element from list
- **Foreach runtime execution**: `andThen foreach var in expr { ... }` creates one sub-block per array element
  - `StepDefinition` gains `foreach_var`/`foreach_value` fields for iteration binding
  - `BlockExecutionBeginHandler` evaluates iterable expression, creates sub-blocks with cached body AST
  - `BlockExecutionContinueHandler` tracks sub-block completion directly (bypasses DependencyGraph)
  - `FacetInitializationBeginHandler` propagates foreach variable to `EvaluationContext` for expression evaluation
  - Empty iterables produce no sub-blocks and complete immediately
- **Iteration-level trace tests**: step-by-step state progression verification at each commit boundary
  - Example 2 trace: 8 steps, 8 iterations, `Adder(a=1, b=2) -> result=3`
  - Example 3 trace: 11 steps, 11 iterations, nested `andThen -> result=13`
  - Example 4 trace: 11 steps, 2 evaluator runs (PAUSE at EventTransmit, resume after continue), `result=15`
  - Key runtime behavior: yield steps are created lazily (when dependencies are available), not eagerly in iteration 0
- **Acceptance tests**: event facet blocking at EventTransmit, step continue/resume, multi-run execution, nested statement blocks, facet definition lookup (namespaced EventFacetDecl)
- 978 tests passing

## Completed (v0.8.2) - Schema Field Comma Separators
- **Grammar**: `schema_fields` rule now requires commas between field definitions, consistent with `params` rule
- **Syntax**: `schema Foo { name: String, age: Int }` (commas required between fields, no trailing comma)
- Empty schemas and single-field schemas unchanged (no comma needed)
- Updated all 18 AFL example files (~45 schemas) across genomics and OSM geocoder examples
- Updated spec documentation (`10_language.md`, `12_validation.md`)
- 987 tests passing

## Completed (v0.9.0) - LLM Integration Features
- **Prompt template evaluation**: `PromptBlock` bodies on EventFacetDecl are interpolated with step params via `_evaluate_prompt_template()`; supports `system`, `template`, and `model` overrides
- **Multi-turn tool use loop**: `_call_claude()` now loops on `stop_reason="tool_use"`, executing intermediate tool calls and continuing the conversation until the target facet tool is called or `max_turns` is reached
- **Intelligent retry**: when the multi-turn loop fails to produce a target tool_use, the runner appends a retry message and re-attempts up to `max_retries` times
- **Script block execution**: `FacetScriptsBeginHandler` now executes `ScriptBlock` bodies via `ScriptExecutor`, writing results to step returns; error handling for syntax and runtime errors
- **LLMHandler utility class**: standalone `LLMHandler` + `LLMHandlerConfig` for building LLM-backed handlers compatible with `AgentPoller.register()`; supports prompt templates, multi-turn tool use, retry, and async dispatch
- `ToolDefinition` gains `prompt_block` field; `ClaudeAgentRunner` gains `max_turns` and `max_retries` constructor parameters
- 1015 tests passing

## Completed (v0.9.1) - HDFS Storage Abstraction
- **StorageBackend protocol**: `afl.runtime.storage` module with `exists`, `open`, `makedirs`, `getsize`, `getmtime`, `isfile`, `isdir`, `listdir`, `walk`, `rmtree`, `join`, `dirname`, `basename`
- **LocalStorageBackend**: thin wrapper around `os`, `os.path`, `shutil`, `builtins.open`
- **HDFSStorageBackend**: wraps `pyarrow.fs.HadoopFileSystem` with `hdfs://` URI parsing and caching per host:port
- **Factory function**: `get_storage_backend(path)` returns HDFS backend for `hdfs://` URIs, local singleton otherwise
- **Handler updates**: all OSM geocoder handlers (downloader, graphhopper, 6 extractors) and genomics cache handlers use storage abstraction
- **Configurable cache paths**: `AFL_CACHE_DIR` for OSM cache, `AFL_GENOMICS_CACHE_DIR` for genomics cache (supports `hdfs://` URIs)
- **Optional dependency**: `pyarrow>=14.0` in `[hdfs]` extra
- 1049 tests passing

## Completed (v0.9.2) - ScriptExecutor Subprocess Timeout
- **Subprocess execution**: `ScriptExecutor._execute_python` now runs scripts in a subprocess via `subprocess.run([sys.executable, "-c", ...])` instead of in-process `exec()`
- **Timeout enforcement**: `timeout` parameter (default 30s) is enforced via `subprocess.run(timeout=...)`, killing runaway scripts
- **Safe builtins**: `_SAFE_BUILTIN_NAMES` list drives sandbox reconstruction in both parent and subprocess; user `print()` calls captured via `io.StringIO` to prevent JSON protocol corruption
- **Worker protocol**: user code is base64-encoded, params serialized as JSON in, result dict serialized as JSON out via stdout
- **Error handling**: `subprocess.TimeoutExpired` → `ScriptResult(success=False, error="Script timed out after {timeout}s")`; non-serializable params fail early before subprocess launch
- 1056 tests passing

## Completed (v0.9.3) - Composed Facet Decomposition & Default Parameters
- **Composed facet**: `LoadVolcanoData` decomposed from a single event facet into a regular facet with `andThen` body chaining three event facets: `CheckRegionCache` → `DownloadVolcanoData` → `FilterByType`
- **Facet default parameter resolution**: `FacetInitializationBeginHandler` now applies default values from the facet definition for any params not provided in the call; fixes InputRef (`$.param`) resolution in facet bodies when callers omit defaulted args
- **New event facets**: `CheckRegionCache(region)` → `VolcanoCache`, `DownloadVolcanoData(region, cache_path)` → `VolcanoDataset`, `FilterByType(volcanoes, volcano_type)` → `VolcanoDataset`
- **New schema**: `VolcanoCache { region, path, cached }` for cache check results
- **Volcano-query example**: 7 event facets + 1 composed facet, 6 pause/resume cycles (was 4 event facets, 4 cycles)
- 1056 tests passing

## Completed (v0.9.4) - Cross-Namespace Composition & Generic Cache Delegation
- **osmoperations.afl fixes**: removed double `=>` on `Cache` event facet, fixed `OsmCache` → `OSMCache` typo on `Download`
- **FormatGeoJSON**: new event facet in `osm.geo.Visualization` with `FormatResult` schema (`output_path`, `text`, `feature_count`, `format`, `title`)
- **osmcache.afl refactor**: 280 event facets across 11 namespaces (`Africa`, `Asia`, `Australia`, `Europe`, `NorthAmerica`, `Canada`, `CentralAmerica`, `SouthAmerica`, `UnitedStates`, `Antarctica`, `Continents`) converted from `event facet` to composed `facet` with `andThen` body delegating to generic `Cache(region = "<Name>")`
- **osmgraphhoppercache.afl refactor**: 262 event facets across 8 namespaces converted from `event facet` to composed `facet` with `andThen` body delegating to generic `BuildGraph(cache, profile, recreate)`
- **volcano-query rewrite**: replaced all custom schemas and 7 event facets with cross-namespace composition using `osm.geo.Operations` (Cache, Download), `osm.geo.Filters` (FilterByOSMTag), `osm.geo.Elevation` (FilterByMaxElevation), `osm.geo.Visualization` (RenderMap, FormatGeoJSON)
- **AFL-only example**: removed volcano-query handlers, test runner, and agent — now relies entirely on existing OSM geocoder infrastructure
- **dl_*.downloadCache fix**: 254 attribute references across 10 continent workflow files corrected from `dl_*.cache` to `dl_*.downloadCache` (latent bug exposed by `OsmCache` → `OSMCache` type fix)
- **osmvoting.afl fix**: moved `TIGERCache` and `VotingDistrictResult` schemas into `census.types` namespace (schemas cannot be top-level); added `use census.types` to `Districts`, `Processing`, and `Workflows` namespaces
- **osmworkflows_composed.afl fix**: corrected `osm.geo.POI` → `osm.geo.POIs` namespace reference; fixed return attribute names (`pois` → `cities`/`towns`/`villages`)
- **Cross-example disambiguation**: qualified `Download` as `osm.geo.Operations.Download` in volcano.afl; added `use genomics.cache.Operations` to genomics_cache_workflows.afl to resolve ambiguous facet references when compiling all examples together
- **All examples compile together**: 47 AFL sources (volcano-query + osm-geocoder + genomics), 0 errors
- 1056 tests passing

## Completed (v0.9.5) - Standalone Local PBF/GeoJSON Verifier
- **OSMOSE API removed**: replaced external OSMOSE REST API integration (`osmose.openstreetmap.fr`) with a standalone local verifier — no network dependency
- **osmose_verifier.py**: new core module with `VerificationHandler(osmium.SimpleHandler)` for single-pass PBF processing (nodes → ways → relations); checks reference integrity, coordinate ranges (including null island), degenerate geometry, unclosed polygons, tag completeness, duplicate IDs; also validates GeoJSON files for structure, geometry, and property completeness
- **Severity levels**: level 1 (error) for reference integrity failures, out-of-bounds coords, degenerate geometry, duplicates; level 2 (warning) for missing name on named features, unclosed polygons; level 3 (info) for empty tag values
- **New event facets**: `VerifyAll(cache, output_dir, check_*)`, `VerifyGeometry(cache)`, `VerifyTags(cache, required_tags)`, `VerifyGeoJSON(input_path)`, `ComputeVerifySummary(input_path)`
- **New schemas**: `VerifyResult` (output_path, issue_count, node/way/relation counts, format, verify_date), `VerifySummary` (issue counts by type and severity, tag_coverage_pct, avg_tags_per_element)
- **osmose_handlers.py**: thin wrappers delegating to `osmose_verifier`; `register_osmose_handlers(poller)` signature preserved — `__init__.py` unchanged
- **Pattern 12 rewrite**: `OsmoseQualityCheck` workflow now uses cache-based local verification (Cache → VerifyAll → ComputeVerifySummary) instead of bbox-based API queries
- 1092 tests passing

## Completed (v0.9.6) - GTFS Transit Feed Support
- **osmgtfs.afl**: new `osm.geo.Transit.GTFS` namespace with `use osm.types`; 9 schemas (`StopResult`, `RouteResult`, `FrequencyResult`, `TransitStats`, `NearestStopResult`, `AccessibilityResult`, `CoverageResult`, `DensityResult`, `TransitReport`) and 10 event facets
- **GTFSFeed schema**: added to `osm.types` namespace (url, path, date, size, wasInCache, agency_name, has_shapes) — analogous to `OSMCache` and `GraphHopperCache`
- **Core event facets**: `DownloadFeed` (ZIP download with URL-hash caching), `ExtractStops` (stops.txt → GeoJSON points, location_type=0 filter), `ExtractRoutes` (shapes.txt linestrings with stop-sequence fallback), `ServiceFrequency` (trips-per-stop-per-day from stop_times.txt + calendar.txt), `TransitStatistics` (aggregate counts by route type)
- **OSM integration facets**: `NearestStops` (brute-force haversine nearest-neighbor lookup), `StopAccessibility` (400m/800m/beyond walk-distance bands)
- **Coverage facets**: `CoverageGaps` (grid overlay detecting cells with OSM features but no stops), `RouteDensity` (routes per grid cell), `GenerateReport` (consolidated analysis)
- **gtfs_extractor.py**: pure-stdlib implementation (`csv`, `zipfile`, `json`, `math`) — no new dependencies; `GTFSRouteType(IntEnum)` with `from_string()` and `label()` classmethods; safety cap of 10,000 grid cells; handles GTFS ZIPs with nested subdirectories; streams stop_times.txt for large feeds
- **gtfs_handlers.py**: thin factory-pattern wrappers (10 factories + `GTFS_FACETS` list + `register_gtfs_handlers(poller)`); follows `park_handlers.py` pattern with `_*_to_dict()` converters and `_empty_*()` helpers
- **Pattern 13 — TransitAnalysis**: `DownloadFeed → ExtractStops + ExtractRoutes → TransitStatistics` composed workflow
- **Pattern 14 — TransitAccessibility**: `OSM Cache + DownloadFeed → ExtractBuildings + ExtractStops → StopAccessibility → CoverageGaps` composed workflow
- 1092 tests passing

## Completed (v0.9.7) - Low-Zoom Road Infrastructure Builder (Zoom 2–7)
- **osmzoombuilder.afl**: new `osm.geo.Roads.ZoomBuilder` namespace with `use osm.types`; 6 schemas (`LogicalEdge`, `ZoomEdgeResult`, `ZoomBuilderResult`, `ZoomBuilderMetrics`, `ZoomBuilderConfig`, `CellBudget`) and 9 event facets (`BuildLogicalGraph`, `BuildAnchors`, `ComputeSBS`, `ComputeScores`, `DetectBypasses`, `DetectRings`, `SelectEdges`, `ExportZoomLayers`, `BuildZoomLayers`)
- **zoom_graph.py**: `TopologyHandler(osmium.SimpleHandler)` for two-pass PBF processing — caches node coordinates, collects highway-tagged ways, identifies decision nodes (degree ≥ 3, FC change, ref change, endpoints), splits ways at decision nodes, merges degree-2 chains into logical edges; `LogicalEdge` dataclass with FC scoring (base score + ref/bridge/tunnel/surface/access modifiers); `RoadGraph` class with adjacency lists, Dijkstra shortest path for backbone repair, JSON serialization/deserialization
- **zoom_sbs.py**: Structural Betweenness Sampling — `SegmentIndex` grid-based spatial index (~500m cells) for route-to-logical-edge snapping with point-to-segment perpendicular distance; `build_anchors()` snaps cities to nearest graph nodes with population thresholds per zoom level; `sample_od_pairs()` with minimum straight-line distance filtering and deterministic RNG; `route_batch_parallel()` via `ThreadPoolExecutor` hitting GraphHopper HTTP API; `accumulate_votes()` and `normalize_sbs()` (log-normalized against P95)
- **zoom_detection.py**: bypass detection via settlement models (city/town/village core radii), entry/exit node identification at outer boundary crossings, route comparison (unconstrained vs through-center waypoint) with time ratio, core fraction, and FC advantage thresholds; ring road detection for cities ≥ 100K population using radial entry nodes, orbital candidate vote accumulation, and geometry validation (coefficient of variation ≤ 0.35, mean radius range check)
- **zoom_selection.py**: per-zoom score computation with weight schedule (SB weight decreasing z2→z7, FC weight increasing); H3 hexagonal cell budgets at resolution 7 with density-adaptive factors (sparse 1.3× to ultra-dense 0.4×); greedy budgeted selection; backbone connectivity repair via BFS + Dijkstra path insertion; sparse region floor enforcement; monotonic reveal (cumulative set union z2→z7) assigning final `minZoom` per edge
- **zoom_builder.py**: 9-step pipeline orchestrator wiring graph construction → anchor building → SBS computation (z2–z6, z7 reuses z6) → bypass/ring detection → scoring → cell budgets → selection → monotonic reveal → export; outputs `segment_scores.csv`, `edge_importance.jsonl`, per-zoom cumulative GeoJSON (`roads_z{2..7}.geojson`), and `metrics.json`
- **zoom_handlers.py**: 9 thin factory-pattern wrappers following `park_handlers.py` convention; `ZOOM_FACETS` list + `register_zoom_handlers(poller)`
- **Pattern 15 — RoadZoomBuilder**: `Cache → BuildGraph → BuildZoomLayers(cache, graph)` composed workflow
- 1092 tests passing

## Completed (v0.9.8) - Fix Hardcoded Cache Calls in Composed Workflows
- **osmworkflows_composed.afl**: 13 workflows accepted a `region: String` parameter but hardcoded the cache call to `osm.geo.cache.Europe.Liechtenstein()`, ignoring the parameter entirely; replaced all 13 with `osm.geo.Operations.Cache(region = $.region)` so the region parameter is actually respected
- **Affected workflows**: `VisualizeBicycleRoutes`, `AnalyzeParks`, `LargeCitiesMap`, `TransportOverview`, `NationalParksAnalysis`, `TransportMap`, `StateBoundariesWithStats`, `DiscoverCitiesAndTowns`, `RegionalAnalysis`, `ValidateAndSummarize`, `OsmoseQualityCheck`, `TransitAccessibility`, `RoadZoomBuilder`
- **Unchanged** (correctly hardcoded — no `region` parameter): `GermanyCityAnalysis` (`Europe.Germany()`), `FranceCityAnalysis` (`Europe.France()`), `TransitAnalysis` (no cache, takes `gtfs_url` only)
- 1174 tests passing

## Completed (v0.9.9) - Fix AgentPoller Resume & Add Missing Handlers
- **AgentPoller program_ast propagation**: `_resume_workflow()` was calling `evaluator.resume(workflow_id, workflow_ast)` without `program_ast`, causing `get_facet_definition()` to return `None` for all facets; `EventTransmitHandler` then passed through without blocking, so event facet steps completed immediately with empty outputs
- **New `_program_ast_cache`**: `AgentPoller` now maintains a separate `_program_ast_cache` dict alongside `_ast_cache`; `cache_workflow_ast()` accepts optional `program_ast` parameter; `_resume_workflow()` looks up and passes cached `program_ast` to `evaluator.resume()`
- **`run_to_completion()` fix**: integration test helper now passes `program_ast` when calling `poller.cache_workflow_ast()`
- **`osm.geo.Operations.Cache` handler**: new `_cache_handler()` in `operations_handlers.py` resolves region names to Geofabrik paths via flat lookup built from `cache_handlers.REGION_REGISTRY`, with case-insensitive fallback; downloads PBF and returns `cache: OSMCache`
- **`osm.geo.Operations.Validation.*` handlers**: new `validation_handlers.py` with 5 handlers (`ValidateCache`, `ValidateGeometry`, `ValidateTags`, `ValidateBounds`, `ValidationSummary`) delegating to `osmose_verifier`; registered in `handlers/__init__.py` via `register_validation_handlers(poller)`
- **Unit test updates**: `test_agent_poller_extended.py` updated for `program_ast=None` keyword argument; new `test_resume_with_cached_program_ast` test
- 1121 unit tests, 29 integration tests passing

## Completed (v0.9.10) - GraphHopper 8.0 Config-File CLI
- **`_run_graphhopper_import()` rewrite**: GraphHopper 8.0 replaced `--datareader.file=` command-line flags with a YAML config file passed as a positional argument to the `import` subcommand; updated handler to generate a temporary config file with `datareader.file`, `graph.location`, `import.osm.ignored_highways`, and profile with `custom_model_files: []`
- **Profile-aware ignored highways**: motorized profiles (`car`, `motorcycle`, `truck`) ignore `footway,cycleway,path,pedestrian,steps`; non-motorized profiles (`bike`, `mtb`, `racingbike`) ignore `motorway,trunk`; other profiles (e.g. `foot`, `hike`) ignore nothing
- **`test_liechtenstein_city_routes` now passes**: full 9-step CityRouteMap pipeline (ResolveRegion → Cache → BuildGraph → ExtractPlaces → FindCities → BicycleRoutes → RenderMap → FormatGeoJSON → Visualization) completes end-to-end
- 1121 unit tests, 30 integration tests passing

## Completed (v0.10.0) - Automatic Dependency Resolution & Source Publishing
- **`afl/resolver.py`**: new module with `NamespaceIndex` (filesystem scanner mapping namespace names to `.afl` files), `MongoDBNamespaceResolver` (queries `afl_sources` collection), and `DependencyResolver` (iterative fixpoint loop: parse → find missing `use` namespaces → load from filesystem/MongoDB → merge → repeat until stable; max 100 iterations safety bound)
- **`afl/publisher.py`**: new module with `SourcePublisher` — publishes AFL source files to MongoDB `afl_sources` collection indexed by namespace name; parses source to extract namespace names, creates one `PublishedSource` document per namespace with SHA-256 checksum; supports versioning, force-overwrite, unpublish, and list operations
- **`AFLParser.parse_and_resolve()`**: new method that calls `parse_sources()` then runs `DependencyResolver`; automatically scans primary file's sibling directory plus configured `source_paths`; optionally queries MongoDB when `mongodb_resolve=True`
- **`ResolverConfig` dataclass**: added to `AFLConfig` with `source_paths` (colon-separated `AFL_RESOLVER_SOURCE_PATHS`), `auto_resolve` (`AFL_RESOLVER_AUTO_RESOLVE`), `mongodb_resolve` (`AFL_RESOLVER_MONGODB_RESOLVE`)
- **`PublishedSource` entity**: new dataclass in `afl/runtime/entities.py` with `uuid`, `namespace_name`, `source_text`, `namespaces_defined`, `version`, `published_at`, `origin`, `checksum`
- **MongoStore extensions**: `afl_sources` collection with `(namespace_name, version)` unique compound index and `namespaces_defined` multikey index; new methods `save_published_source()`, `get_source_by_namespace()`, `get_sources_by_namespaces()` (batch `$in`), `delete_published_source()`, `list_published_sources()`
- **CLI subcommands**: `afl compile` (default, backward-compatible) with new `--auto-resolve`, `--source-path PATH`, `--mongo-resolve` flags; `afl publish` subcommand with `--version`, `--force`, `--list`, `--unpublish` options
- **Qualified call resolution**: resolver scans `CallExpr` names (e.g. `osm.geo.Operations.Cache`) to extract candidate namespace prefixes, not just `use` statements; candidates are matched against the filesystem index so only real namespaces are loaded; enables `--auto-resolve` for files like `osmworkflows_composed.afl` that reference facets by fully-qualified names without `use` imports
- **OSM geocoder verified**: `afl compile --primary osmworkflows_composed.afl --auto-resolve` resolves 29 namespaces in 3 iterations from a single primary file
- **Backward compatibility**: bare `afl input.afl -o out.json` still works unchanged; subcommand routing treats non-subcommand first arguments as compile input
- 1159 tests passing

## Completed (v0.10.1) - Publish & Run-Workflow Scripts
- **`scripts/publish`**: bash script with inline Python that compiles AFL source and publishes to MongoDB; creates `FlowDefinition` (with AFL source in `compiled_sources`), `WorkflowDefinition` entries for each workflow found, and publishes namespaces to `afl_sources` via `SourcePublisher`; supports `--auto-resolve`, `--source-path` (repeatable), `--primary`/`--library` (repeatable), `--version`, `--config`
- **`scripts/run-workflow`**: bash script with inline Python for interactive or non-interactive workflow execution from MongoDB; `--list` prints a table of all workflows; interactive mode lists workflows with numbers and prompts for selection; extracts parameters with types and compile-time defaults from the workflow AST; prompts for each parameter showing type and default; smart value parsing (bool, int, float, JSON objects/arrays, string fallback); `--workflow NAME` and `--input JSON` flags for non-interactive use; `--flow-id` to select by flow; executes via `Evaluator.execute()` with full `program_ast`
- **`MongoStore.get_all_workflows()`**: new method returning workflows sorted by `date` descending with configurable limit, following existing `get_all_runners()` pattern
- **Runner workflow lookup fix**: `_find_workflow_in_program` and `_search_namespace_workflows` now search both `namespaces`/`workflows` keys (emitter format) and `declarations` (alternative AST format); previously qualified workflow names like `handlers.AddOneWorkflow` failed in distributed execution because the runner only checked `declarations` while the emitter outputs under `namespaces`
- 1159 tests passing; 457 OSM geocoder tests passing (including distributed)

## Completed (v0.10.2) - Registry Runner
- **`HandlerRegistration` dataclass**: new entity in `entities.py` mapping a qualified facet name to a Python module + entrypoint; fields: `facet_name` (primary key), `module_uri` (dotted path or `file://` URI), `entrypoint`, `version`, `checksum` (cache invalidation), `timeout_ms`, `requirements`, `metadata`, `created`, `updated`
- **`PersistenceAPI` extensions**: 4 new abstract methods — `save_handler_registration()` (upsert by facet_name), `get_handler_registration()`, `list_handler_registrations()`, `delete_handler_registration()`
- **`MemoryStore` implementation**: dict-backed CRUD with `clear()` support
- **`MongoStore` implementation**: `handler_registrations` collection with unique index on `facet_name`; `_handler_reg_to_doc()` / `_doc_to_handler_reg()` serialization helpers
- **`RegistryRunnerConfig` dataclass**: extends `AgentPollerConfig` pattern with `registry_refresh_interval_ms` (default 30s) for periodic re-read of handler registrations from persistence
- **`RegistryRunner` class**: universal runner that reads handler registrations from persistence, dynamically loads Python modules, caches them by `(module_uri, checksum)`, and dispatches event tasks — eliminates the need for per-facet microservices
  - `register_handler()`: convenience method to create and persist a `HandlerRegistration`
  - `_import_handler()`: supports dotted module paths (`importlib.import_module`) and `file://` URIs (`spec_from_file_location`); validates entrypoint is callable
  - `_load_handler()`: cache lookup by `(module_uri, checksum)`, imports on miss
  - `_refresh_registry()` / `_maybe_refresh_registry()`: periodic re-read of registered facet names from persistence
  - `_process_event()`: looks up `HandlerRegistration` → loads handler → dispatches (sync or async) → `continue_step` → `_resume_workflow` → mark task COMPLETED; graceful error handling for missing registrations, import failures, and handler exceptions
  - Full lifecycle: `start()` / `stop()` / `poll_once()`, server registration, heartbeat, AST caching — mirrors `AgentPoller` patterns
- **Exported** from `afl.runtime`: `HandlerRegistration`, `RegistryRunner`, `RegistryRunnerConfig`
- 25 new tests across 6 test classes: `TestHandlerRegistrationCRUD`, `TestDynamicModuleLoading`, `TestModuleCaching`, `TestRegistryRunnerPollOnce`, `TestRegistryRunnerLifecycle`, `TestRegistryRefresh`
- 1184 tests passing

## Completed (v0.10.3) - Dispatch Adapter Migration for RegistryRunner
- **`RegistryRunner._process_event` payload injection**: shallow-copies `task.data` before handler invocation; injects `payload["_facet_name"] = task.name` so dispatch entrypoints know which facet they are handling; injects `payload["_handler_metadata"]` when registration has non-empty metadata; 4 new tests
- **Dispatch adapter pattern**: all 27 handler modules (22 OSM + 5 genomics) now expose a `_DISPATCH` dict (mapping qualified facet names to handler callables), a `handle(payload)` entrypoint that routes via `payload["_facet_name"]`, and a `register_handlers(runner)` function that persists `HandlerRegistration` entries
  - **Factory-based modules** (park, amenity, filter, population, road, route, building, visualization, gtfs, zoom): `_build_dispatch()` iterates `*_FACETS` list at module load time
  - **Direct dict modules** (region, elevation, routing, osmose, validation, airquality, genomics core, genomics resolve, genomics operations): `_DISPATCH` built as a literal dict
  - **Complex modules** (cache, operations, poi, graphhopper, tiger, boundary, genomics cache, genomics index): custom `_build_dispatch()` over nested registries
- **`__init__.py` extensions**: both `examples/osm-geocoder/handlers/__init__.py` and `examples/genomics/handlers/__init__.py` gain `register_all_registry_handlers(runner)` — imports and calls each module's `register_handlers(runner)`; existing `register_all_handlers(poller)` unchanged for backward compatibility
- **Agent entry points**: `examples/osm-geocoder/agent.py` updated with dual-mode support — `AFL_USE_REGISTRY=1` uses `RegistryRunner`, default uses `AgentPoller`; new `examples/genomics/agent.py` with same dual-mode pattern
- **New tests**: `test_handler_dispatch_osm.py` (58 tests) and `test_handler_dispatch_genomics.py` (18 tests) verify `_DISPATCH` key counts, `handle()` dispatch, unknown-facet errors, and `register_handlers()` call counts; use `sys.modules` cleanup for cross-file isolation
- 1264 tests passing

## Completed (v0.10.4) - Topic-Based Filtering for RegistryRunner
- **`RegistryRunnerConfig.topics`**: new `list[str]` field (default empty) accepting glob patterns to filter which registered facets a runner will handle; when empty, all registrations are polled (backward-compatible default)
- **`RegistryRunner._matches_topics()`**: new helper using `fnmatch.fnmatch()` to match facet names against configured topic patterns; supports exact names (`ns.A`), glob wildcards (`osm.geo.cache.*`), prefix patterns (`genomics.*`), and `?`/`[seq]` syntax
- **`_refresh_registry()` filtering**: when `topics` is non-empty, filters `_registered_names` to only include facet names matching at least one pattern; downstream methods (`poll_once`, `_poll_cycle`, `_register_server`, `claim_task`) automatically use the filtered list
- **`AFL_RUNNER_TOPICS` env var**: both `examples/osm-geocoder/agent.py` and `examples/genomics/agent.py` read comma-separated topic patterns from `AFL_RUNNER_TOPICS` and pass to `RegistryRunnerConfig(topics=...)`; prints active filter when set
- **5 new tests** in `TestRegistryRunnerTopics`: exact match filtering, glob pattern filtering, empty-means-all default, poll_once topic-scoped claiming, server definition topics reflection
- 1269 tests passing

## Completed (v0.10.5) - MCP Handler Registration Tools & Resources
- **`afl_manage_handlers` tool**: new MCP tool (7th) with action-based dispatch for managing handler registrations; actions: `list` (all registrations), `get` (by facet_name), `register` (create/upsert with `created` timestamp preservation), `delete` (by facet_name); validation for required fields per action; error pattern matches existing tools (`{"success": false, "error": "..."}`)
- **`serialize_handler_registration()`**: new serializer in `afl/mcp/serializers.py` converting `HandlerRegistration` to dict with all fields (facet_name, module_uri, entrypoint, version, checksum, timeout_ms, requirements, metadata, created, updated)
- **Handler resources**: two new MCP resources — `afl://handlers` (list all registrations) and `afl://handlers/{facet_name}` (detail by facet name); routed in `_handle_resource()` with not-found error handling
- **Tool count**: 6 → 7; **Resource count**: 10 → 12
- 17 new tests: `TestManageHandlersTool` (13 tests covering list/get/register/delete with validation and edge cases) and 4 handler resource tests in `TestResources`
- 1286 tests passing

## Completed (v0.10.6) - Dashboard Handler Registrations Page
- **Handler routes** (`afl/dashboard/routes/handlers.py`): new route module with list (`GET /handlers`), detail (`GET /handlers/{facet_name}`), and delete (`POST /handlers/{facet_name}/delete`) endpoints; uses `{facet_name:path}` converter for dotted names
- **Handler templates**: `handlers/list.html` table with facet name (linked), module URI, entrypoint, version, timeout, registered/updated timestamps; `handlers/detail.html` grid layout with details table, delete action (htmx POST with confirm), requirements list, metadata display, not-found handling
- **Navigation**: "Handlers" link added to `base.html` nav between Servers and Tasks
- **Home summary**: handler count card added to dashboard index; "Handler Registrations" added to quick links
- **JSON API**: `GET /api/handlers` endpoint returns all registrations as JSON array
- 9 new tests: `TestHandlerRoutes` (list empty/with data, detail, detail not found, delete, delete not found, API empty/with data, home handler count)

## Completed (v0.10.7) - Events Dashboard Page
- **Event routes** (`afl/dashboard/routes/events.py`): new route module with list (`GET /events`) and detail (`GET /events/{event_id}`) endpoints
- **Event templates**: `events/list.html` table with ID (truncated+linked), step ID, workflow ID, state (badge), event type; `events/detail.html` grid layout with details table and payload as JSON `<pre>` block, not-found handling
- **Store additions**: `get_all_events(limit=500)` added to `MongoStore` (sorted by `_id` descending) and `MemoryStore`
- **Navigation**: "Events" link added to `base.html` nav between Handlers and Tasks
- **Home summary**: event count card added to dashboard index; "All Events" added to quick links
- **JSON API**: `GET /api/events` endpoint returns all events as JSON array
- 7 new tests: `TestEventRoutes` (list empty/with data, detail, detail not found, detail shows payload, API empty/with data)

## Completed (v0.10.8) - Published Sources Dashboard Page
- **Source routes** (`afl/dashboard/routes/sources.py`): new route module with list (`GET /sources`), detail (`GET /sources/{namespace_name}`), and delete (`POST /sources/{namespace_name}/delete`) endpoints; uses `{namespace_name:path}` converter for dotted names
- **Source templates**: `sources/list.html` table with namespace name (linked), version, origin, published at, checksum (truncated), namespaces defined; `sources/detail.html` grid layout with details table, delete action (htmx POST with confirm), source text with `<pre><code>`, not-found handling
- **Navigation**: "Sources" link added to `base.html` nav between Events and Tasks
- **Home summary**: source count card added to dashboard index; "Published Sources" added to quick links
- **JSON API**: `GET /api/sources` endpoint returns all published sources as JSON array
- 9 new tests: `TestSourceRoutes` (list empty/with data, detail, detail not found, detail shows source text, delete, API empty/with data, home source count)

## Completed (v0.10.9) - Workflow Validation UI
- **Validate endpoint** (`POST /workflows/validate`): parses and validates AFL source without compiling, extracts AST summary (namespaces, facets, workflows), renders `workflows/validate.html` with valid/invalid status, error list with line/column, and namespace/facet/workflow summary
- **Validate template** (`workflows/validate.html`): shows Valid/Invalid status, error list, namespace list, facet list, workflow list, "Back to editor" link
- **Editor update**: "Validate Only" button added to `workflows/new.html` with `formaction="/workflows/validate"` and `class="outline"`
- 4 new tests: `TestWorkflowValidation` (valid source, invalid source, shows namespaces, validate button exists)

## Completed (v0.10.10) - Namespace Browser
- **Namespace routes** (`afl/dashboard/routes/namespaces.py`): new route module with list (`GET /namespaces`) and detail (`GET /namespaces/{namespace_name}`) endpoints; aggregates namespace data across all flows via `_aggregate_namespaces()` helper; resolves namespace IDs to names via `_resolve_ns_name()`
- **Namespace templates**: `namespaces/list.html` table with namespace name (linked), flow count, facet count, workflow count; `namespaces/detail.html` with facets table, workflows table, not-found handling
- **Navigation**: "Namespaces" link added to `base.html` nav between Sources and Locks
- 4 new tests: `TestNamespaceRoutes` (list empty/with data, detail, detail not found)

## Completed (v0.10.11) - Server Detail Page
- **Server detail endpoint** (`GET /servers/{server_id}`): added to existing `servers.py` route module
- **Server detail template** (`servers/detail.html`): grid layout with details table (UUID, group, service, name, IPs, state, start/ping times, manager), topics list, handlers list, handled statistics table, error display with JSON formatting, not-found handling
- **Server list linking**: server names in `servers/list.html` now link to detail page via `<a href="/servers/{{ server.uuid }}">`
- **JSON API**: `GET /api/servers/{server_id}` endpoint with 404 handling
- 7 new tests extending `TestServerRoutes` (list links to detail, detail, detail not found, handled stats, error display, API detail, API not found)

## Completed (v0.10.12) - Lock Visibility
- **Lock routes** (`afl/dashboard/routes/locks.py`): new route module with list (`GET /locks`), detail (`GET /locks/{lock_key}`), and release (`POST /locks/{lock_key}/release`) endpoints; uses `{lock_key:path}` converter for compound keys; annotates locks with expired/active status
- **Lock templates**: `locks/list.html` table with key (linked+mono), acquired at, expires at, expired badge (active/expired), topic, handler; `locks/detail.html` grid layout with details table, release action (htmx POST with confirm), metadata display (topic, handler, step_name, step_id), not-found handling
- **Store additions**: `get_all_locks()` added to `MongoStore` and `MemoryStore` returning all locks including expired for dashboard visibility
- **Navigation**: "Locks" link added to `base.html` nav between Namespaces and Tasks
- **Home summary**: active lock count card (filtered by `expires_at > now`) added to dashboard index; "Active Locks" added to quick links
- **JSON API**: `GET /api/locks` endpoint returns all locks as JSON array with metadata
- 8 new tests: `TestLockRoutes` (list empty/with data, detail, detail not found, detail shows meta, release, API empty/with data)
- 1334 tests passing

## Completed (v0.10.13) - Dashboard Enhancements
- **Task detail page** (`GET /tasks/{task_id}`): new route and template with grid layout showing task details (UUID, name, state, task list, data type, created, updated), related entities (runner, workflow, flow, step links), error display, and data display; not-found handling
- **Task list filtering** (`GET /tasks?state=`): state filter nav (All, Pending, Running, Completed, Failed, Ignored, Canceled) with badge display; task UUIDs now link to detail page
- **Event list filtering** (`GET /events?state=`): state filter nav (All, Created, Dispatched, Processing, Completed, Error) with badge display
- **Server list filtering** (`GET /servers?state=`): state filter nav (All, Startup, Running, Shutdown, Error) with badge display
- **Flow list search** (`GET /flows?q=`): text search input filtering flows by name (case-insensitive)
- **Handler list search** (`GET /handlers?q=`): text search input filtering handlers by facet name (case-insensitive)
- **Source list search** (`GET /sources?q=`): text search input filtering sources by namespace name (case-insensitive)
- **Flow detail improvements**: namespaces table (linked to namespace browser), facets table, execution history table (runners sorted by start_time, limit 20)
- **Store additions**: `get_tasks_by_state()` and `get_events_by_state()` on `MongoStore`
- **API expansion**: `GET /api/tasks` (with `?state=` filter), `GET /api/tasks/{task_id}` (404 handling), `GET /api/flows/{flow_id}` (namespaces, facets, counts), `GET /api/namespaces` (aggregated across flows); `?state=` filter on `GET /api/events` and `GET /api/servers`; `?q=` text search on `GET /api/flows`, `GET /api/handlers`, `GET /api/sources`
- 24 new dashboard tests across 4 test classes: `TestTaskDetailAndFiltering` (6), `TestFlowDetailImprovements` (3), `TestListFiltering` (5), `TestApiExpansion` (10)
- **RegistryRunner integration tests** (`tests/runtime/test_registry_runner_integration.py`): 24 end-to-end tests across 5 classes — `TestRegistryRunnerAddOne` (7, mirrors test_addone_agent.py), `TestRegistryRunnerMultiStep` (4, Double→Square pipeline with data flow), `TestRegistryRunnerAsync` (6, async handlers, partial updates, type hints), `TestRegistryRunnerComplexResume` (3, three-step A→B→C pipeline), `TestRegistryRunnerForeach` (4, foreach iteration with event facets)
- 1382 tests passing

## Completed (v0.11.0) - Runtime Inline Handler Dispatch
- **`HandlerDispatcher` protocol** (`afl/runtime/dispatcher.py`): new `@runtime_checkable` protocol with `can_dispatch(facet_name) -> bool` and `dispatch(facet_name, payload) -> dict | None` for inline event execution during evaluation
- **`RegistryDispatcher`**: persistence-backed dispatcher extracted from RegistryRunner; dynamic module loading with `file://` URI and dotted module path support, `(module_uri, checksum)` cache, sync/async handler detection via `inspect.iscoroutinefunction`
- **`InMemoryDispatcher`**: wraps `dict[str, Callable]` with `register()` and `register_async()` methods; short-name fallback for qualified facet names (e.g. `ns.Facet` falls back to `Facet`)
- **`ToolRegistryDispatcher`**: adapter wrapping existing `ToolRegistry` from `afl/runtime/agent.py` as a `HandlerDispatcher`
- **`CompositeDispatcher`**: chains multiple dispatchers with priority ordering; first dispatcher that `can_dispatch` wins
- **`ExecutionContext.dispatcher`**: new optional field; when set, `EventTransmitHandler` attempts inline dispatch before creating a task — if the dispatcher handles the facet, the step completes immediately without PAUSED status, no task created, no polling round-trip
- **`Evaluator.execute()` / `Evaluator.resume()`**: new `dispatcher` parameter (default `None`) passed through to `ExecutionContext`; existing callers unaffected
- **`EventTransmitHandler.process_state()`**: inline dispatch check before task creation — if `dispatcher.can_dispatch()` returns True and `dispatch()` returns a result, return values are set as return attributes and the step transitions forward; exceptions produce `STATEMENT_ERROR`; when dispatcher is None or cannot dispatch, falls back to existing task+PAUSED behavior
- **`RegistryRunner` integration**: creates `RegistryDispatcher` internally, passes to `evaluator.resume()` — subsequent event facets in multi-step workflows are dispatched inline during auto-resume instead of creating additional tasks
- **Module loading extracted**: `_import_handler()`, `_load_handler()`, and `_module_cache` moved from `RegistryRunner` to `RegistryDispatcher`; `RegistryRunner._process_event()` delegates to `self._dispatcher.dispatch()`
- **Exported** from `afl.runtime`: `HandlerDispatcher`, `RegistryDispatcher`, `InMemoryDispatcher`, `ToolRegistryDispatcher`, `CompositeDispatcher`
- 23 new dispatcher unit tests (`tests/runtime/test_dispatcher.py`): `TestRegistryDispatcher` (10), `TestInMemoryDispatcher` (6), `TestToolRegistryDispatcher` (3), `TestCompositeDispatcher` (4)
- 14 new inline dispatch integration tests (`tests/runtime/test_inline_dispatch.py`): `TestInlineDispatchAddOne` (4), `TestInlineDispatchMultiStep` (3), `TestInlineDispatchForeach` (2), `TestInlineDispatchFallback` (3), `TestInlineDispatchWithRegistryRunner` (2)
- 1419 tests passing

## Completed (v0.11.1) - Continental LZ Pipeline Example
- **New example** (`examples/continental-lz/`): self-contained Docker-based pipeline orchestrating the Low-Zoom (LZ) road infrastructure algorithm and GTFS transit analysis across three continental regions (United States, Canada, 12 European countries)
- **4 AFL source files** defining 20 workflows across 4 namespaces:
  - `continental_types.afl`: 4 schemas (`RegionLZResult`, `TransitAgencyResult`, `ContinentalLZSummary`, `ContinentalTransitSummary`) in `continental.types` namespace
  - `continental_lz_workflows.afl`: 4 LZ workflows (`BuildUSLowZoom`, `BuildCanadaLowZoom`, `BuildEuropeLowZoom` with 12 countries in parallel, `BuildContinentalLZ` orchestrator) in `continental.lz` namespace; each follows cache → GraphHopper build → BuildZoomLayers pattern
  - `continental_gtfs_workflows.afl`: 15 GTFS workflows (11 per-agency: 4 US, 3 Canada, 4 Europe; plus 4 aggregators) in `continental.transit` namespace; each agency follows DownloadFeed → TransitStatistics → ExtractStops → ExtractRoutes
  - `continental_full.afl`: `FullContinentalPipeline` combining LZ + Transit in parallel in `continental` namespace
- **Handler reuse**: symlink `handlers → ../osm-geocoder/handlers` for local dev; `COPY` in Docker; registers 6 handler modules (cache, operations, graphhopper, population, zoom, gtfs) via `register_handlers(runner)`
- **Docker stack** (`docker-compose.yml`): 5 services — MongoDB (port 27019), dashboard (port 8081), runner, agent (16 GB memory limit for GraphHopper JVM), seed (profile: seed); isolated database `afl_continental_lz`; 5 named volumes (mongodb_data, osm_data, graphhopper_data, lz_output, gtfs_data)
- **`Dockerfile.agent`**: python:3.12-slim + libgeos + libproj + Java JRE + GraphHopper 8.0 JAR; copies AFL compiler, OSM geocoder handlers, and agent entry point
- **`Dockerfile.seed`**: lightweight python:3.12-slim + lark + pymongo; compiles all 12 AFL source files and seeds MongoDB
- **`agent.py`**: RegistryRunner entry point with `max_concurrent=4`, `service_name="continental-lz"`; dual-mode MongoDB/MemoryStore based on `AFL_MONGODB_URL`
- **`scripts/seed.py`**: reads 12 AFL sources in dependency order, parses + validates + emits, stores compiled flow and sample execution tasks in MongoDB; supports both Docker (`/app/osm-afl/`) and local (`../osm-geocoder/afl/`) layouts
- **`scripts/run_region.py`**: standalone single-region smoke test using MemoryStore; generates inline AFL for any of 14 regions; `--region Belgium --output-dir /tmp/lz-belgium`
- **Data scale**: 14 regions totaling ~28 GB PBF downloads, ~44 GB GraphHopper graphs, estimated 12-30 hours for full continental run
- **GTFS transit agencies** (11): Amtrak, MBTA, CTA, MTA (US); TransLink, TTC, OC Transpo (Canada); Deutsche Bahn, SNCF, Renfe, Trenitalia (Europe)
- 1419 tests passing

## Completed (v0.12.0) - Testing & Hardening + Documentation
- **136 new tests** (1419 → 1555), **87% code coverage** (with `fail_under = 75` enforced)
- **Coverage configuration**: `pyproject.toml` gains `fail_under`, `show_missing`, exclusion patterns, HTML output directory; `CLAUDE.md` updated with coverage commands
- **MCP server tests** (`tests/mcp/test_error_handling.py`, 29 tests): tool dispatch errors (missing/empty args for all 7 tools), input validation (None/unicode/large source, handler registration edge cases), resource boundary conditions (malformed URIs, large datasets, dotted facet names)
- **MCP workflow tests** (`tests/mcp/test_tool_workflows.py`, 14 tests): compile-then-execute patterns, event facet PAUSED verification, resume edge cases (nonexistent workflow, missing workflow name), execute variants (namespaced, andThen body, params, facet-not-workflow)
- **MCP serializer tests** (`tests/mcp/test_serializers.py`, 11 new): handler registration serialization (all fields, requirements, metadata, defaults), edge cases (runner without params, step with returns, flow without workflows, paused/timeout execution results)
- **Dashboard edge cases** (`tests/dashboard/test_edge_cases.py`, 39 tests): health route, runner/step/flow/task/server/event/source/lock edge cases, filtering edge cases
- **Dashboard template rendering** (`tests/dashboard/test_template_rendering.py`, 14 tests): state color CSS classes, navigation links, table column headers, form elements
- **Dashboard filter edge cases** (`tests/dashboard/test_filters.py`, 7 new): duration/timestamp/state_color boundary values
- **Entry point tests** (`tests/test_entry_points.py`, 9 tests): module importability and `main()` existence for MCP, Dashboard, Runner; CLI `--check`, `-o`, invalid file
- **Cross-component integration** (`tests/test_lifecycle_integration.py`, 13 tests): full lifecycle (AFL source → compile → execute → pause → continue → resume → verify outputs), multi-step data flow, RegistryRunner dispatch, MCP tool chaining
- **Documentation** (7 new files, 1548 lines):
  - `docs/architecture.md` (239 lines): system overview, ASCII component diagram, compiler pipeline, runtime engine, agent execution models, MCP server, data flow narrative, key abstractions table
  - `docs/tutorial.md` (634 lines): 7-part progressive AFL tutorial (Hello World, Event Facets, Namespaces & Schemas, Composition, Foreach, Expressions, full pipeline example) with quick-reference table
  - `docs/deployment.md` (339 lines): Docker quick start, single/multi-node architecture, configuration reference, service reference, monitoring, scaling, security, backup, troubleshooting
  - `agents/go/afl-agent/README.md` (81 lines): Go agent quickstart
  - `agents/typescript/afl-agent/README.md` (82 lines): TypeScript agent quickstart
  - `agents/scala/afl-agent/README.md` (80 lines): Scala agent quickstart
  - `agents/java/afl-agent/README.md` (93 lines): Java agent quickstart
- 1555 tests passing

## Completed (v0.12.1) - Docker HDFS Integration for OSM Agents & Tests
- **Docker Compose override** (`docker-compose.hdfs.yml`): new override file wiring HDFS into OSM agent services; sets `AFL_CACHE_DIR=hdfs://namenode:8020/osm-cache`, `GRAPHHOPPER_GRAPH_DIR=hdfs://namenode:8020/graphhopper`, `AFL_GTFS_CACHE_DIR=hdfs://namenode:8020/gtfs-cache`; adds `depends_on: namenode` so agents wait for HDFS; sets `INSTALL_HDFS=true` build arg for pyarrow installation
- **Dockerfile HDFS support**: `docker/Dockerfile.osm-geocoder`, `docker/Dockerfile.osm-geocoder-lite`, and `docker/Dockerfile.runner` gain `ARG INSTALL_HDFS=false` with conditional `pip install pyarrow>=14.0` when set to `true`
- **Shared WebHDFS test helpers** (`tests/hdfs_helpers.py`): extracted `WebHDFSClient` class (with new `getsize()` method), `hdfs` fixture, and `workdir` fixture from `tests/runtime/test_hdfs_storage.py` into a shared module for reuse across HDFS test files
- **`tests/runtime/test_hdfs_storage.py` refactored**: imports `WebHDFSClient`, `hdfs`, `workdir` from `tests.hdfs_helpers`; local definitions removed; all 19 existing tests preserved
- **OSM handler HDFS integration tests** (`tests/test_osm_handlers_hdfs.py`, 12 tests): `TestStorageBackendHDFSSelection` (4 tests — local/None path returns `LocalStorageBackend`, `hdfs://` URI returns `HDFSStorageBackend`, host:port caching), `TestWebHDFSCacheOperations` (5 tests — create/size/listing/overwrite/isdir on cache files), `TestHDFSCachePatterns` (3 tests — OSM PBF nested region cache, GraphHopper graph directory, GTFS feed cache); all guarded by `--hdfs` flag
- **Setup script** (`scripts/setup`): when `--hdfs` is set, uses `docker compose -f docker-compose.yml -f docker-compose.hdfs.yml` for both build and up commands; prints HDFS support status message
- **Deployment docs** (`docs/deployment.md`): new "HDFS Integration" section with starting HDFS, building with HDFS support, running OSM agents with HDFS cache (env var table), and running HDFS tests
- 1586 tests collected (1555 passed, 31 skipped without `--hdfs`/`--mongodb`)

## Completed (v0.12.2) - Docker Profiles for Jenkins & PostGIS
- **Jenkins service** (`docker-compose.yml`, profile: `jenkins`): `jenkins/jenkins:lts` image as `afl-jenkins`; ports `9090:8080` (Web UI) and `50000:50000` (agent); `jenkins_home` volume for persistence; Docker socket mount (`/var/run/docker.sock`) for Docker-in-Docker builds; healthcheck on `/login` with 60s `start_period`
- **PostGIS service** (`docker-compose.yml`, profile: `postgis`): `postgis/postgis:16-3.4` image as `afl-postgis`; port `5432:5432`; environment `POSTGRES_DB=afl_gis`, `POSTGRES_USER=afl`, `POSTGRES_PASSWORD=afl`; `postgis_data` volume; healthcheck via `pg_isready -U afl`
- **PostGIS compose override** (`docker-compose.postgis.yml`): new override file (same pattern as `docker-compose.hdfs.yml`) wiring PostGIS into OSM agents; sets `AFL_POSTGIS_URL=postgresql://afl:afl@postgis:5432/afl_gis`; adds `depends_on: postgis`; sets `INSTALL_POSTGIS=true` build arg for `psycopg2-binary` installation
- **Dockerfile PostGIS support**: `docker/Dockerfile.osm-geocoder` and `docker/Dockerfile.osm-geocoder-lite` gain `ARG INSTALL_POSTGIS=false` with conditional `pip install psycopg2-binary` when set to `true`
- **Setup script** (`scripts/setup`): new `--jenkins` and `--postgis` flags with defaults, arg parsing, profile/compose-file handling, and status output lines
- **Deployment docs** (`docs/deployment.md`): new "Jenkins CI/CD" section (starting Jenkins, initial admin password retrieval, setup script usage) and "PostGIS Integration" section (starting PostGIS, connection details table, building OSM agents with override file, environment variables table)
- 1555 passed, 31 skipped

## Completed (v0.12.3) - Real PostGIS Import Handler
- **PostGIS import engine** (`examples/osm-geocoder/handlers/postgis_importer.py`): new module parsing PBF files via pyosmium and importing nodes/ways into PostGIS via psycopg2; `HAS_OSMIUM`/`HAS_PSYCOPG2` flags for graceful degradation; `get_postgis_url()` reads `AFL_POSTGIS_URL` env var (default `postgresql://afl:afl@localhost:5432/afl_gis`); `sanitize_url()` strips password for logging; `ensure_schema()` creates PostGIS extension and three tables (`osm_nodes`, `osm_ways`, `osm_import_log`) with spatial GIST and GIN indexes; `NodeCollector` and `WayCollector` osmium handlers flush batches of 10,000 via `psycopg2.extras.execute_values()` with `ON CONFLICT DO UPDATE`; `import_to_postgis()` orchestrates connection, schema, prior-import detection, node+way collection, and import logging; returns `ImportResult` dataclass
- **PostGIS handler adapter** (`examples/osm-geocoder/handlers/postgis_handlers.py`): new dispatch adapter following standard pattern; `_postgis_import_handler` extracts cache dict, calls `import_to_postgis()`, returns OSMCache-shaped `stats` with `size` = total features, `path` = sanitized PostGIS URL, `wasInCache` = prior import detected; graceful fallback on missing deps or errors
- **Operations handler cleanup** (`operations_handlers.py`): removed `"PostGisImport": "stats"` from `OPERATIONS_FACETS` — no longer handled by the generic factory stub
- **Handler registration** (`handlers/__init__.py`): wired `postgis_handlers` into imports, `__all__`, `register_all_handlers()`, and `register_all_registry_handlers()`
- **`--postgis` pytest option** (`conftest.py`): new CLI flag following `--mongodb`/`--hdfs` pattern for gating live PostGIS integration tests
- **Dispatch tests** (`tests/test_handler_dispatch_osm.py`): added `TestOsmPostgisHandlers` class (4 tests: dispatch_keys, handle_dispatches, handle_unknown_facet, register_handlers)
- **PostGIS import tests** (`tests/test_postgis_import.py`): `TestPostgisImporterModule` (7 tests — boolean flags, default/env URL, sanitize_url, DDL keywords); `TestPostgisHandlerDispatch` (5 tests — dispatch key, count, handle with stats, unknown facet, register count); `TestPostgisImportLive` (4 tests gated by `--postgis` — ensure_schema creates tables, spatial indexes exist, import log entry written, reimport detects prior)
- 1571 passed, 35 skipped (without `--hdfs`/`--mongodb`/`--postgis`)

## Completed (v0.12.4) - Configurable External Storage for HDFS
- **Docker Compose** (`docker-compose.yml`): HDFS volume mounts now use env var substitution — `${HDFS_NAMENODE_DIR:-hadoop_namenode}:/hadoop/dfs/name` and `${HDFS_DATANODE_DIR:-hadoop_datanode}:/hadoop/dfs/data`; when unset, uses Docker named volumes (unchanged default); when set to a host path, creates bind mounts to external storage (NFS, SSD, dedicated disk)
- **Setup script** (`scripts/setup`): added `--hdfs-namenode-dir PATH` and `--hdfs-datanode-dir PATH` options; exports the env vars and auto-enables `--hdfs`; prints configured paths in status output
- **Deployment docs** (`docs/deployment.md`): new "External Storage for HDFS" section with usage examples, env var table, and permissions note

## Completed (v0.12.27) - Hierarchical Tree View for Dashboard Step List

- **`afl/dashboard/tree.py`** (NEW): `StepNode` dataclass and `build_step_tree()` function that converts a flat step list into a hierarchical tree using `container_id` / `block_id` relationships — no extra DB queries needed
- **`afl/dashboard/templates/partials/step_tree.html`** (NEW): recursive Jinja2 template rendering tree nodes as nested `<details>` elements; root and first-level blocks default open, deeper levels collapsed
- **`afl/dashboard/routes/runners.py`**: `runner_detail()` and `runner_steps()` now pass `tree=build_step_tree(list(steps))` to template context
- **`afl/dashboard/routes/api.py`**: `api_runner_steps()` accepts `view=tree` query parameter; when `partial=true&view=tree`, renders tree partial instead of flat rows
- **`afl/dashboard/templates/runners/detail.html`**: toggle button group (Flat / Tree) with both views; flat table has htmx polling, tree container polls with `?view=tree`
- **`afl/dashboard/templates/steps/list.html`**: same toggle + both views for standalone steps page
- **`afl/dashboard/static/style.css`**: `.view-toggle` segmented button group, `.step-tree-container` with left-border guide lines and indentation, `.tree-facet` / `.tree-duration` muted secondary info
- **11 new tests** (`tests/dashboard/test_step_tree.py`): 7 `build_step_tree` unit tests (empty, single root, block+statements, deep nesting, multiple roots, order preservation, orphans) + 4 integration tests (tree partial, flat partial unchanged, detail toggle, steps page toggle)
- 2173 passed, 80 skipped (without `--hdfs`/`--mongodb`/`--postgis`/`--boto3`)

## Completed (v0.12.28) - Download Lock Deduplication for Concurrent Cache Access

- **Per-path thread locks** (`handlers/downloader.py`): added `_path_locks` dict with a `_path_locks_guard` and `_get_path_lock()` helper — prevents duplicate HTTP downloads when multiple RegistryRunner threads request the same cached file simultaneously; uses double-checked locking pattern (fast-path `exists()` check without lock, re-check after acquiring lock)
- **Atomic temp-file writes** (`handlers/downloader.py`): `download()` and `download_url()` now write to a temp file (`path.tmp.{pid}.{tid}`) then `os.replace()` to the final path — cache file is always either absent or complete (never partial); on error, temp file is cleaned up via `storage.remove()` with OSError suppression
- **Extracted helpers** (`handlers/downloader.py`): `_cache_hit()`, `_cache_miss()`, and `_stream_to_file()` reduce duplication between `download()` and `download_url()`
- **HDFS-aware path handling** (`handlers/downloader.py`): `download_url()` uses atomic temp-file pattern for local paths only; HDFS paths (`hdfs://`) write directly since `os.replace()` cannot rename across filesystems
- **`StorageBackend.remove()`** (`afl/runtime/storage.py`): added `remove(path)` to the protocol and both implementations — `LocalStorageBackend` delegates to `os.remove()`, `HDFSStorageBackend` uses WebHDFS DELETE (non-recursive)
- **6 new tests** (`test_downloader.py`): `TestDownloadLockDeduplication` (5-thread single-fetch, lock re-check returns cache hit, different paths not blocked), `TestDownloadUrlLockDeduplication` (3-thread single-fetch), `TestDownloadAtomicWrite` (partial download cleanup, temp file not visible as cache path)
- 2179 passed, 80 skipped (without `--hdfs`/`--mongodb`/`--postgis`/`--boto3`)

## Completed (v0.12.43) - Step Logs Collection for Event Facet Lifecycle

- **New entity** (`afl/runtime/entities.py`): `StepLogEntry` dataclass with uuid, step_id, workflow_id, runner_id, facet_name, source, level, message, details, time; `StepLogLevel` (info/warning/error/success) and `StepLogSource` (framework/handler) constants
- **Persistence protocol** (`afl/runtime/persistence.py`): 3 new abstract methods — `save_step_log()`, `get_step_logs_by_step()`, `get_step_logs_by_workflow()`
- **MemoryStore** (`afl/runtime/memory_store.py`): `_step_logs` list with time-sorted retrieval; cleared in `clear()`
- **MongoStore** (`afl/runtime/mongo_store.py`): `step_logs` collection with indexes (uuid unique, step_id, workflow_id); `_step_log_to_doc()` / `_doc_to_step_log()` serializers
- **RegistryRunner** (`afl/runtime/registry_runner.py`): `_emit_step_log()` helper; framework-level logs at 4 points in `_process_event()` — task claimed (info), dispatching handler (info), handler completed with timing (success), handler error (error); `_step_log` callback injected into handler payload for handler-level logging
- **Dashboard filter** (`afl/dashboard/filters.py`): `step_log_color()` filter mapping info→primary, warning→warning, error→danger, success→success
- **Step detail** (`routes/steps.py`, `templates/steps/detail.html`): fetches and displays step logs in a table (time, source, level badge, message) anchored at `#step-logs`
- **Step list views** (`templates/steps/list.html`, `templates/runners/detail.html`): added "Logs" column header to flat tables
- **Log count badges** (`partials/step_row.html`, `partials/step_tree.html`): clickable badge linking to `/steps/{id}#step-logs` when log count > 0; `step_log_counts` dict computed in runner routes and API partial renders
- **JSON API** (`routes/api.py`): `GET /api/steps/{step_id}/logs` returns step log entries as JSON array
- **CSS** (`static/style.css`): `.tree-log-badge` style for inline tree view badges
- **Public exports** (`afl/runtime/__init__.py`): `StepLogEntry`, `StepLogLevel`, `StepLogSource`
- **11 new tests**: 5 MemoryStore tests (save/get by step, get by workflow, time ordering, empty results, clear); 3 RegistryRunner tests (success logs, failure logs, `_step_log` callback injection); 3 dashboard tests (JSON API returns entries, empty for unknown, step detail shows section)
- **3 test fixes**: existing capture-handler tests updated to filter out callable `_step_log` before `json.dump()`
- 2280 passed, 80 skipped (without `--hdfs`/`--mongodb`/`--postgis`/`--boto3`)

## Completed (v0.12.42) - Inline Retry Buttons for Failed Steps in Dashboard

- **Fix retry backend** (`afl/dashboard/routes/steps.py`): `POST /steps/{step_id}/retry` now resets step to `EVENT_TRANSMIT` (was incorrectly resetting to `initialization.Begin`); matches `evaluator.retry_step()` logic — sets `transition.current_state`, clears error, sets `request_transition = False`, marks `changed = True`; also resets the associated task to `pending` with `error = None` via `store.get_task_for_step()`
- **Retry button in flat step table** (`partials/step_row.html`, `steps/list.html`): new "Actions" column header; failed/error steps show an HTMX retry button (`hx-post` + `hx-confirm`); non-failed steps show dash
- **Retry button in tree view** (`partials/step_tree.html`): inline retry button after facet/duration spans for failed steps; uses `event.stopPropagation()` to prevent toggling the details node
- **`.btn-retry-inline` CSS** (`static/style.css`): compact inline button style for table rows and tree summaries
- **Updated detail page** (`steps/detail.html`): confirmation message updated from "initialization" to "EventTransmit"
- **2 test changes** (`tests/dashboard/test_routes.py`): `test_retry_step` updated to assert `state.EventTransmit`; new `test_retry_step_resets_task` verifies associated task reset to `pending` with `error = None`
- 2269 passed, 80 skipped (without `--hdfs`/`--mongodb`/`--postgis`/`--boto3`)

## Completed (v0.12.41) - Copy Mirror Data to Cache Instead of Serving Directly

- **Mirror as seed source** (`handlers/downloader.py`): when `AFL_GEOFABRIK_MIRROR` is set and a mirror file exists, the data is now copied into the configured cache directory (local or HDFS) instead of returning the mirror path directly; downstream handlers always see the real cache location (`AFL_CACHE_DIR`) regardless of whether data came from the mirror
- **`_copy_to_cache()` helper** (`handlers/downloader.py`): new function copies a local file to the cache using atomic temp-file + `os.replace` for local paths and `shutil.copyfileobj` streaming for HDFS; follows the same concurrent-safety pattern as the download path (per-path lock, double-check after lock acquisition)
- **Removed `_local_storage`** (`handlers/downloader.py`): the dedicated local storage backend for mirror paths is no longer needed since mirror hits now go through `_storage` (the configured cache backend)
- **Updated mirror tests** (`test_downloader.py`): `test_mirror_hit_returns_cached` → `test_mirror_hit_copies_to_cache` (expects `wasInCache=False` and cache path); `test_mirror_path_structure` now asserts cache path instead of mirror path
- **2 new tests** (`test_downloader.py`): `test_mirror_copies_to_cache` verifies physical file copy from mirror to cache using real files; `test_mirror_skips_copy_when_cache_exists` verifies fast-path cache hit when cache already has the file
- 2261 passed, 80 skipped (without `--hdfs`/`--mongodb`/`--postgis`/`--boto3`)

## Completed (v0.12.40) - Validate Globally Ambiguous Unqualified Facet Calls

- **Validator rule** (`afl/validator.py`): unqualified facet calls are now flagged as ambiguous when the short name exists in more than one namespace globally, even if only one namespace is imported; local definitions (current namespace) still take precedence without error; prevents the runtime from silently resolving to the wrong namespace
- **Qualified colliding calls** (13 OSM example `.afl` files): all ambiguous unqualified calls across Africa, Asia, Australia, Canada, Central America, Europe, North America, South America, United States, shapefiles, elevation, and region workflow files were updated to use fully-qualified names
- **Spec update** (`spec/12_validation.md`): documented the global ambiguity rule with examples
- **5 new tests** (`tests/test_validator.py`): globally ambiguous short name error, qualified call passes, local definition takes precedence, single-namespace short name passes, different-name facets not ambiguous
- 2259 passed, 80 skipped (without `--hdfs`/`--mongodb`/`--postgis`/`--boto3`)

## Completed (v0.12.39) - Geofabrik Mirror Support for Docker OSM Agents

- **Docker Compose override** (`docker-compose.mirror.yml`): new override file mounts a host directory read-only at `/data/osm-mirror` in `agent-osm-geocoder` and `agent-osm-geocoder-lite` containers; sets `AFL_GEOFABRIK_MIRROR=/data/osm-mirror` env var; uses `${AFL_GEOFABRIK_MIRROR:?...}` for clear error on missing var
- **Setup script** (`scripts/setup`): added `--mirror PATH` flag; exports `AFL_GEOFABRIK_MIRROR`, sets `MIRROR=true`, appends `-f docker-compose.mirror.yml` to compose files; prints mirror path and mount point in status output
- **Run script** (`examples/osm-geocoder/tests/real/scripts/run_30states.sh`): refactored setup invocation to use bash array `SETUP_ARGS`; conditionally appends `--mirror "$GEOFABRIK_MIRROR"` when `AFL_GEOFABRIK_MIRROR` is set; reads env var at script start
- **Dashboard formatting** (`namespaces/detail.html`, `flows/detail.html`, `flows/namespace.html`): bold facet/workflow names with inline documentation below (replaces separate Documentation column)
- 2277 passed, 68 skipped (without `--hdfs`/`--mongodb`/`--postgis`)

## Completed (v0.12.38) - Doc Comments on All Dashboard Pages & Docker Seed Fix

- **Seed** (`docker/seed/seed.py`): both `seed_inline_source()` and `seed_example_directory()` now populate `WorkflowDefinition.documentation` from the compiled JSON `"doc"` field (was always `None`)
- **Runtime** (`entities.py`): widened `WorkflowDefinition.documentation` type from `str | None` to `dict | str | None` (matching `NamespaceDefinition` and `FacetDefinition`)
- **Dashboard — flow detail** (`flows/detail.html`): imports `render_doc` macro; Namespaces table and Facets table now show a Documentation column with rendered doc comments
- **Dashboard — namespace** (`flows/namespace.html`, `routes/flows.py`): added Facets section below Workflows table showing Name, Parameters, Returns, and Documentation columns; `flow_namespace()` route now filters and passes `ns_facets` to template
- **Dashboard — run page** (`flows/run.html`, `routes/flows.py`): shows workflow documentation (from compiled JSON `@param`/`@return` tags) above the parameters form; parameters table gains a Description column populated from `@param` tag descriptions; `flow_run_form()` route extracts `workflow_doc` and per-parameter descriptions from compiled AST
- **Docker** (`Dockerfile.dashboard`): added `markdown` to fallback pip install line
- **Tests**: 8 new tests — `TestDocCommentDisplay` (6 route tests for namespace/facet/workflow doc display, facets section, run page doc and param descriptions) and `TestSeedWorkflowDocumentation` (2 tests for `_collect_workflows` doc propagation and `WorkflowDefinition` dict doc acceptance)
- 2256 passed, 80 skipped (without `--hdfs`/`--mongodb`/`--postgis`/`--boto3`)

## Completed (v0.12.37) - Structured Doc Comments (`@param`/`@return`)

- **AST** (`ast.py`): new `DocParam(name, description)` and `DocComment(description, params, returns)` dataclasses; changed `doc` field type from `str | None` to `DocComment | None` on all 5 node types (`FacetDecl`, `EventFacetDecl`, `WorkflowDecl`, `SchemaDecl`, `Namespace`)
- **Transformer** (`transformer.py`): rewrote `_clean_doc_comment()` to parse `@param name desc` and `@return name desc` tags into `DocParam` objects; non-tag lines become the `description` field; return type of `_extract_doc_comment()` changed to `DocComment | None`
- **Emitter** (`emitter.py`): new `_doc_comment()` helper emits structured `{"description": ..., "params": [...], "returns": [...]}` dicts; changed all 5 doc guards from `if node.doc:` to `if node.doc is not None:` (empty description with tags is valid)
- **Runtime** (`entities.py`): widened `documentation` type to `dict | str | None` on `NamespaceDefinition` and `FacetDefinition` for backward compatibility
- **Dashboard**: added `markdown>=3.5` to dashboard deps in `pyproject.toml`; 3 new Jinja2 filters (`doc_description`, `doc_params`, `doc_returns`) in `filters.py` with `str | dict` backward compatibility; new `partials/_doc_comment.html` macro renders description as markdown HTML with parameter/return `<dl>` tables; updated `namespaces/detail.html` and `flows/namespace.html` to use the macro; widened type hints in `routes/namespaces.py`; added CSS styles for `.doc-comment`, `.doc-section`
- **Tests**: updated all 12 existing parser assertions to use `DocComment` fields; added 3 new parser tests (`test_doc_comment_multiple_params`, `test_doc_comment_description_with_markdown`, `test_doc_comment_params_no_description`); updated 5 emitter assertions to structured dict format; added `test_doc_with_tags_emits_structured`; added 13 new filter tests (`TestDocDescription`, `TestDocParams`, `TestDocReturns`)
- 2248 passed, 80 skipped (without `--hdfs`/`--mongodb`/`--postgis`/`--boto3`)

## Completed (v0.12.36) - Doc Comments (`/** ... */`)

- **Grammar** (`afl.lark`): new `DOC_COMMENT.3` terminal with priority 3 (higher than `BLOCK_COMMENT`); regex consumes trailing whitespace and newlines to avoid LALR conflicts; attached as optional prefix to `namespace_block`, `facet_decl`, `event_facet_decl`, `workflow_decl`, and `schema_decl` rules
- **AST** (`ast.py`): added `doc: str | None = None` keyword-only field to `FacetDecl`, `EventFacetDecl`, `WorkflowDecl`, `SchemaDecl`, and `Namespace` dataclasses
- **Transformer** (`transformer.py`): new `_clean_doc_comment()` strips `/** */` delimiters and leading `*` from each line; `_extract_doc_comment()` pops optional `DOC_COMMENT` token from items; applied in all 5 declaration methods
- **Emitter** (`emitter.py`): emits `"doc"` key in JSON output for all documented declarations; key omitted when `doc` is `None`
- **Entities** (`runtime/entities.py`): added `documentation: str | None = None` to `NamespaceDefinition` and `FacetDefinition` (`WorkflowDefinition` already had it)
- **Seed** (`docker/seed/seed.py`): inline AFL sources updated with doc comments on namespace, event facets, and workflows; `_extract_flow_structure()` passes `documentation=decl.get("doc")` for namespaces and facets
- **Dashboard** (`routes/namespaces.py`, `templates/namespaces/detail.html`): `WorkflowEntry` and `FacetEntry` gain `documentation` field; namespace detail template shows documentation below each workflow/facet name
- **Spec** (`spec/10_language.md`): section 1.2 updated with doc comment syntax
- **Tests**: 12 parser tests (`TestDocComments`), 5 emitter tests (`TestDocCommentEmission`)
- 2231 passed, 80 skipped (without `--hdfs`/`--mongodb`/`--postgis`/`--boto3`)

## Completed (v0.12.35) - Namespace Page Workflow Counts and Run/View Buttons

- **Fix workflow count always zero** (`routes/namespaces.py`): `_aggregate_namespaces()` now also queries `store.get_workflows_by_flow()` for each flow and matches workflows to namespaces by qualified name prefix (longest-prefix-first via `_match_ns_by_name()`) — previously only checked the embedded `flow.workflows` list which was always empty for seeded flows
- **Deduplicate workflows by name**: workflows stored multiple times (from `_collect_workflows` traversing both `workflows` and `declarations` arrays) are deduplicated by qualified name within each namespace
- **New dataclasses**: `WorkflowEntry` (carries `flow_id`, `uuid`, `short_name` for Run links) and `FacetEntry` (carries `parameters`, `return_type` for display)
- **Namespace detail template** (`templates/namespaces/detail.html`): rewritten — workflows listed first with **Run** buttons (linking to `/flows/{flow_id}/run/{workflow_id}`), facets second with **Parameters** and **Returns** columns showing type signatures; short names displayed (namespace prefix stripped)
- **Namespace list page**: workflow counts now correct (e.g. `handlers`: 4 workflows, 3 facets; `chain`: 1 workflow; `parallel`: 1 workflow)
- 2214 passed, 80 skipped (without `--hdfs`/`--mongodb`/`--postgis`/`--boto3`)

## Completed (v0.12.34) - Full Dashboard Seed Data

- **Flow structure extraction** (`docker/seed/seed.py`): new `_extract_flow_structure()` helper walks compiled JSON `namespaces[].declarations[]` and populates `NamespaceDefinition`, `FacetDefinition`, `BlockDefinition`, and `StatementDefinition` lists; passed into `FlowDefinition` in both `seed_inline_source()` and `seed_example_directory()` — flow detail page now shows real structural counts (3 namespaces, 3 facets, 6 blocks, 19 statements for inline-examples)
- **Handler registrations**: seeds 3 `HandlerRegistration` entries (`handlers.AddOne`, `handlers.Multiply`, `handlers.Greet`) with `metadata.seeded_by` for cleanup identification — populates the Handlers dashboard page
- **Sample runner execution trace**: creates a completed `RunnerDefinition` for `AddOneWorkflow(input=5)` with 142ms duration, 2 `StepDefinition`s (AddOne step + yield), 1 `EventDefinition` (completed), 1 `TaskDefinition` (completed), and 3 `LogDefinition`s — populates Runners, Steps, Events, Tasks pages
- **Server registration**: seeds 1 `ServerDefinition` (`server_group="docker:seed"`, `service_name="addone-agent"`) with 3 handlers and 1 handled count — populates the Servers dashboard page
- **Published source**: seeds 1 `PublishedSource` for the `handlers` namespace with `origin=SEED_PATH` — populates the Sources dashboard page
- **Cleanup cascade** (`clean_seeds()`): extended to remove runners, steps, events, tasks, logs (by workflow/runner ID), handler registrations (by `metadata.seeded_by`), servers (by `server_group`), and published sources (by `origin`) — idempotent re-run cleans all seed entities
- **Return type change**: `seed_inline_source()` returns `(flow_id, workflow_count)` instead of just `workflow_count` to pass flow_id to downstream seed functions
- All 10 dashboard pages (Home, Flows, Runners, Steps, Events, Tasks, Handlers, Servers, Sources, Locks) now show non-zero data out of the box after `docker compose --profile seed run --rm seed`
- 2214 passed, 80 skipped (without `--hdfs`/`--mongodb`/`--postgis`/`--boto3`)

## Completed (v0.12.33) - Namespace-Level Navigation on Flow Detail Page

- **Namespace grouping on flow detail** (`routes/flows.py`): `flow_detail()` now groups workflows by namespace prefix derived from qualified names (`wf.name.rsplit('.', 1)`) and passes a `namespace_list` to the template — each entry has `name`, `prefix`, and `count`; unqualified workflows grouped under `(top-level)` with prefix `_top`
- **New route `GET /flows/{flow_id}/ns/{namespace_name:path}`** (`routes/flows.py`): `flow_namespace()` filters workflows by namespace prefix, builds display list with short names (last segment of qualified name), and renders `flows/namespace.html`; uses `:path` converter for dotted namespace names (same pattern as `/namespaces/{namespace_name:path}` and `/handlers/{facet_name:path}`)
- **Flow detail template** (`templates/flows/detail.html`): replaced flat workflow table with namespace list table — columns: Namespace (linked to `/ns/` route), Workflows (count); heading shows total workflow count (`Workflows (N)`)
- **Namespace template** (`templates/flows/namespace.html`, NEW): shows workflows within a specific namespace — header with flow name and namespace, source/JSON links, workflow table with short names/version/documentation/Run button, back link to flow detail
- **17 new tests** (`tests/dashboard/test_flow_namespaces.py`): `TestFlowDetailNamespaces` (6 tests: namespace list shown, links to `/ns/`, total count, per-namespace counts, top-level group, no flat workflow names) and `TestFlowNamespaceView` (11 tests: correct filtering, short names, nested namespace paths, exclusion, Run buttons, back link, source/JSON links, missing flow, empty namespace, `_top` prefix, heading)
- **Updated existing test** (`tests/dashboard/test_flow_run.py`): `test_run_link_appears_in_detail` → `test_run_link_appears_in_namespace_view` — navigates through namespace sub-page to verify Run link
- 2214 passed, 80 skipped (without `--hdfs`/`--mongodb`/`--postgis`/`--boto3`)

## Completed (v0.12.32) - Docker Seed Rewrite and Dashboard Health Check Fix

- **Docker seed rewrite** (`docker/seed/seed.py`): replaced raw pymongo document creation with proper `MongoStore` + runtime entity classes (`FlowDefinition`, `FlowIdentity`, `SourceText`, `WorkflowDefinition`); seeded flows now have `compiled_sources` so the Dashboard "Run" button works; also discovers and seeds all `examples/` directories (7 flows, 352 workflows: inline-examples 24, aws-lambda 16, continental-lz 80, genomics 20, jenkins 16, osm-geocoder 192, volcano-query 4); cleans legacy seed documents on each run; uses `docker:seed` as path identifier
- **Dashboard health check fix** (`docker-compose.yml`, `Dockerfile.dashboard`): replaced `curl -f` (not available in `python:3.12-slim`) with `python -c "import urllib.request; urllib.request.urlopen(...)"` — dashboard container now reports `healthy` status correctly

## Completed (v0.12.31) - Run Workflow from Flow Detail Page

- **Run button on flow detail** (`templates/flows/detail.html`): each workflow row now shows a "Run" button linking to `/flows/{flow_id}/run/{workflow_id}` — only displayed when `flow.compiled_sources` exists (seeded flows have sources; flows without sources show no button)
- **Parameter input form** (`templates/flows/run.html`, NEW): shows flow/workflow metadata header, parameter table with Name/Type/Default/Value columns, and JS that collects inputs into a hidden `inputs_json` field on submit; follows the same pattern as `workflows/compile.html`
- **GET `/flows/{flow_id}/run/{workflow_id}`** (`routes/flows.py`): compiles the flow's AFL source via `AFLParser` + `JSONEmitter`, finds the workflow via `_find_workflow_in_program()` (from `afl/runtime/submit.py`), extracts params with defaults, and renders the form; returns "Flow not found" for missing flows
- **POST `/flows/{flow_id}/run/{workflow_id}`** (`routes/flows.py`): creates only `RunnerDefinition` + `TaskDefinition` (reuses existing `FlowDefinition` + `WorkflowDefinition`), merges AST defaults with user-provided `inputs_json`, and redirects to `/runners/{runner_id}` (303)
- **14 new tests** (`tests/dashboard/test_flow_run.py`): Run link visibility (with/without compiled sources), GET form rendering (params, defaults, back link, missing flow), POST execution (runner creation, task creation, redirect, flow/workflow reuse, input override, defaults, missing flow)
- 2197 passed, 80 skipped (without `--hdfs`/`--mongodb`/`--postgis`/`--boto3`)

## Completed (v0.12.30) - Seed Examples Script

- **`scripts/seed-examples`** (NEW): Bash shell script that compiles all example AFL directories and pushes `FlowDefinition` + `WorkflowDefinition` entities to MongoDB so they appear in the Dashboard Flow UI; for each example, parses all `afl/*.afl` files via `AFLParser.parse()`, merges ASTs via `Program.merge()`, emits JSON via `JSONEmitter`, recursively collects workflow qualified names from compiled JSON (handles both nested and flat emitter formats), then creates one `FlowDefinition` (path=`cli:seed`) and one `WorkflowDefinition` per workflow; only creates Flow + Workflow entities (no Runner/Task — those are created at execution time); validation errors are treated as non-fatal warnings since some examples (`continental-lz`, `volcano-query`) depend on types from `osm-geocoder`
- **Options**: `--dry-run` (show what would be seeded without writing), `--include PATTERN` / `--exclude PATTERN` (regex filters on example names), `--clean` (remove existing `cli:seed` flows and their workflows before seeding), `--config FILE` (custom AFL config path)
- **Coverage**: discovers 7 example directories (aws-lambda, continental-lz, genomics, jenkins, maven, osm-geocoder, volcano-query); seeds 6 flows with 328 workflows (maven skipped — event facets only, no workflows)
- 2183 passed, 80 skipped (without `--hdfs`/`--mongodb`/`--postgis`/`--boto3`)

## Completed (v0.12.29) - Geofabrik Mirror Prefetch Script and Local Mirror Mode

- **`scripts/osm-prefetch`** (NEW): Bash shell script that delegates to inline Python to prefetch all ~258 unique Geofabrik region files into a local mirror directory; imports `REGION_REGISTRY` from `cache_handlers.py`, deduplicates paths, and downloads `{path}-latest.osm.pbf` (and/or `.free.shp.zip`) files; writes `manifest.json` with URL-to-relative-path mapping; supports `--mirror-dir`, `--fmt` (pbf/shp/all), `--dry-run`, `--delay`, `--include`/`--exclude` regex filters, and `--resume` (skip existing files); follows existing script conventions (`set -euo pipefail`, `.venv` Python detection, `REPO_ROOT` anchoring)
- **`AFL_GEOFABRIK_MIRROR` env var** (`handlers/downloader.py`): new `GEOFABRIK_MIRROR` module-level variable read from `AFL_GEOFABRIK_MIRROR`; `download()` checks the mirror directory between the cache check and HTTP download — if `{mirror_dir}/{region_path}-latest.{ext}` exists, returns a cache hit directly (read-only, no lock needed); avoids hammering `download.geofabrik.de` during test runs when a local mirror is available
- **4 new tests** (`test_downloader.py`): `TestDownloadMirror` class — mirror hit returns `wasInCache=True` with mirror path and no HTTP request, mirror miss falls through to HTTP download, mirror not set skips `os.path.isfile` check entirely, mirror path structure verified for both pbf and shp formats

## Completed (v0.12.26) - Descriptive Step Variable Names in Cache Facets

- **`osmcache.afl`**: renamed single-letter `c` step variable to camelCase facet name in all 225 cache facets (e.g. `c = Cache(region = "Africa")` → `africa = Cache(region = "Africa")`, `c.cache` → `africa.cache`)
- **`osmgraphhoppercache.afl`**: renamed `g` step variable to camelCase facet name in all 55 GraphHopper facets (e.g. `g = BuildGraph(...)` → `africa = BuildGraph(...)`, `g.graph` → `africa.graph`)
- **`volcano.afl`**: renamed `c` → `loadVolcanoData` to match enclosing facet name
- 2162 passed, 80 skipped (without `--hdfs`/`--mongodb`/`--postgis`/`--boto3`)

## Completed (v0.12.25) - retry_step() Runtime Operation

- **`Evaluator.retry_step(step_id)`** (`evaluator.py`): resets a step from `STATEMENT_ERROR` back to `EVENT_TRANSMIT` so agents can re-execute it; clears the step error and resets the associated task from `failed` to `pending` — eliminates manual MongoDB manipulation for transient failures (e.g. SSL errors during downloads)
- **`StepTransition.clear_error()`** (`step.py`): new method to clear the error field on a step's transition
- **`PersistenceAPI.get_task_for_step(step_id)`** (`persistence.py`): new abstract method to find the most recent task associated with a step; implemented in `MemoryStore` (iterates tasks, returns max by `created`) and `MongoStore` (queries tasks collection sorted by `created` descending, uses existing `task_step_id_index`)
- **`afl_retry_step` MCP tool** (`server.py`): new tool accepting `step_id`, calls `evaluator.retry_step()`, returns success/error JSON
- **5 new tests**: 3 evaluator tests (`test_retry_step_not_found`, `test_retry_step_wrong_state`, `test_retry_step_resets_to_event_transmit`) + 2 MCP tests (`test_retry_step_success`, `test_retry_step_not_found`)
- 2162 passed, 80 skipped (without `--hdfs`/`--mongodb`/`--postgis`/`--boto3`)

## Completed (v0.12.24) - Generic Download Facet for Arbitrary URLs

- **Download facet signature change** (`osmoperations.afl`): `event facet Download(cache:OSMCache)` → `event facet Download(url:String, path:String, force:Boolean) => (downloadCache:OSMCache)` — Download is now a general-purpose file downloader accepting any URL and any destination path (local or HDFS), decoupled from OSM-specific cache semantics
- **`download_url()` function** (`handlers/downloader.py`): new generic download function that fetches any URL to any file path; uses `get_storage_backend(path)` so HDFS URIs (`hdfs://namenode:8020/...`) work transparently; `force=True` re-downloads even when the file exists; returns OSMCache-compatible dict with `url`, `path`, `date`, `size`, `wasInCache` fields
- **Custom `_download_handler`** (`operations_handlers.py`): replaces the generic `_make_operation_handler` passthrough with a dedicated handler that extracts `url`, `path`, `force` from the payload and calls `download_url()`; registered in both `register_operations_handlers()` and `_build_dispatch()`; `"Download"` removed from the generic `OPERATIONS_FACETS` map
- **Removed redundant Download steps from 9 regional AFL files** (`osmafrica.afl`, `osmasia.afl`, `osmaustralia.afl`, `osmcanada.afl`, `osmcentralamerica.afl`, `osmeurope.afl`, `osmnorthamerica.afl`, `osmsouthamerica.afl`, `osmunitedstates.afl`): all `dl_xxx = Download(cache = xxx.cache)` steps removed; yield statements now reference `xxx.cache` directly since the Cache facet already performs the download; `use osm.geo.Operations` import removed from all 9 files
- **Cleaned up `osmcontinents.afl`**: removed Download references from commented-out code block
- **6 new tests** (`test_downloader.py`): `TestDownloadUrlCacheHit` (cache hit returns without HTTP, force re-downloads), `TestDownloadUrlCacheMiss` (downloads and returns, streams to storage, HDFS path routing), `TestDownloadUrlHttpError` (HTTP errors propagate)
- 2157 passed, 80 skipped (without `--hdfs`/`--mongodb`/`--postgis`/`--boto3`)

## Completed (v0.12.23) - Runner State Completion After Event Processing

- **Fix runner never marked complete**: `AgentPoller._resume_workflow()` and `RegistryRunner._resume_workflow()` now check the `ExecutionResult` returned by `evaluator.resume()` — when status is `COMPLETED`, updates runner state to `RunnerState.COMPLETED` with `end_time` and `duration`; when `ERROR`, updates to `RunnerState.FAILED`
- **Propagate `runner_id` through resume**: both `_resume_workflow()` methods now pass `runner_id` to `evaluator.resume()` so that event tasks created during resume inherit the runner_id — without this, child tasks (e.g. Download events created after Cache events complete) had empty `runner_id` and the final resume could not update the runner
- **Per-workflow resume lock**: added `_resume_locks: dict[str, threading.Lock]` to both `AgentPoller` and `RegistryRunner` — prevents concurrent `resume()` calls for the same workflow from overwriting each other's step state via `replace_one` (uses non-blocking `acquire`; if lock is held, the resume is skipped)
- **Verified with `run_30states.sh`**: `Download30States` workflow (121 tasks: 1 execute + 30 Cache + 90 Download) completes with runner state correctly transitioning to `completed` in ~289 seconds
- 2148 passed, 80 skipped (without `--hdfs`/`--mongodb`/`--postgis`/`--boto3`)

## Completed (v0.12.22) - Multi-andThen Block Fix and Dependency Chain Tests

- **Fix `FacetScriptsBeginHandler` crash on multi-block bodies**: `scripts.py` line 47 crashed with `'list' object has no attribute 'get'` when a workflow had multiple `andThen` blocks (emitter produces a list of block dicts instead of a single dict) — added `isinstance(body, list)` guard to pass through correctly
- **AddLongs dependency chain test**: 10-step workflow (`s1`–`s10`) with cross-step arithmetic using non-event facet `LongValue(value: Long)` — verifies full compile → execute lifecycle with `input=1` (output=223) and `input=5` (output=331)
- **MultiAndThenEventTest**: 5 concurrent `andThen` blocks, each with 6 cross-dependent steps calling `facet Value(a, b) => (value:Int) andThen { yield Value(value = $.a + $.b) }` — verifies compilation (5 blocks × 6 steps), execution with `parameter=1` (all outputs=25) and `parameter=5` (all outputs=37)
- 2135 passed, 80 skipped (without `--hdfs`/`--mongodb`/`--postgis`/`--boto3`)

## Completed (v0.12.21) - 30-State Workflow End-to-End Fixes

- **Pass `program_ast` to `resume()`**: `RunnerService._resume_workflow()`, `AgentPoller._load_workflow_ast()`, and `RegistryRunner._load_workflow_ast()` now cache and pass the full `program_dict` when calling `evaluator.resume()` — fixes facet `andThen` body expansion during resume (wrapper facet bodies were silently empty because `get_facet_definition()` returned None when `program_ast` was None)
- **Fix Download handler return value**: `OPERATIONS_FACETS["Download"]` changed from `None` to `"downloadCache"` in `examples/osm-geocoder/handlers/operations_handlers.py` — the AFL definition declares `event facet Download(cache:OSMCache) => (downloadCache:OSMCache)` so the handler must return `{downloadCache: {...}}`
- **30-state workflow validated end-to-end**: `Download30States` workflow runs to completion in Docker (1080/1080 steps complete, 0 errors, 481/481 tasks completed) — all Cache, Download, and wrapper steps produce correct return attributes

## Completed (v0.12.20) - CLI Submit Module

- **New `afl/runtime/submit.py`**: CLI module (`python -m afl.runtime.submit`) for submitting AFL workflows to the runtime via MongoDB — compiles AFL sources, validates, creates `FlowDefinition`/`WorkflowDefinition`/`RunnerDefinition`/`TaskDefinition` entities, and queues an `afl:execute` task for the RunnerService
- **Multi-source input**: supports `--primary FILE` (repeatable) and `--library FILE` (repeatable) flags mirroring `afl compile`, plus legacy positional arg for single-file mode
- **Workflow lookup**: `--workflow NAME` with qualified-name resolution (e.g. `ns.sub.WorkflowName`) matching the RunnerService's `_find_workflow_in_program` logic
- **Default parameter merging**: extracts default values from workflow AST params and merges with `--inputs JSON` overrides
- **Source concatenation**: all AFL source texts concatenated into a single `compiled_sources` entry, as required by `RunnerService._execute_workflow` which reads `compiled_sources[0].content`
- **Console script**: `afl-submit` entry point added to `pyproject.toml`
- **`run_30states.sh` updated**: step 5 now passes AFL source files via `--primary`/`--library` instead of pre-compiled JSON
- **Flat namespace fix**: `_find_workflow_in_program` in both `submit.py` and `RunnerService` now handles flat dotted namespace names (e.g. `osm.geo.UnitedStates.sample`) that the emitter produces for multi-file compilations — tries flat prefix matching before falling back to step-by-step nested navigation
- **`run_30states.sh` MongoDB port**: sets `AFL_MONGODB_URL` to use `MONGODB_PORT` (default 27018) matching the Docker Compose host-side port mapping
- **WebHDFS storage backend**: replaced pyarrow's native `HadoopFileSystem` (requires libhdfs.so JNI library) with WebHDFS REST API via `requests` — works on any platform (ARM64/macOS/Linux) without Hadoop native libraries; uses `AFL_WEBHDFS_PORT` env var (default 9870)
- **Docker simplification**: removed `INSTALL_HDFS` build arg and pyarrow from all Dockerfiles (runner, osm-geocoder, osm-geocoder-lite); HDFS support now only requires `requests`
- 2127 passed, 80 skipped (without `--hdfs`/`--mongodb`/`--postgis`/`--boto3`)

## Completed (v0.12.19) - Reorganize Example Tests into Standardized Structure

- **New directory layout**: every example now has `tests/{mocked,real}/{afl,scripts,py}` — mocked tests (unit/compile-time with mocks/stubs) in `mocked/py/`, integration tests requiring live services in `real/py/`, and AFL test fixtures in `real/afl/`
- **53 test files moved** across 5 examples: aws-lambda (2), genomics (3), jenkins (2), maven (3), osm-geocoder (43 — 38 mocked + 5 real/py + 3 real/afl); 4 examples with empty structure only (continental-lz, doc, hello-agent, volcano-query)
- **osm-geocoder/integration/ removed**: 6 test files + conftest/helpers → `tests/real/py/`, 3 AFL fixtures → `tests/real/afl/`, relative imports (`from .helpers`) converted to absolute
- **Per-example conftest.py** in each `tests/mocked/py/`: adds example root to `sys.path`, purges stale `handlers` package from `sys.modules` to prevent cross-example import conflicts, autouse fixture re-establishes correct path before each test
- **Import path fixes**: all `Path(__file__).resolve().parent` chains updated for new directory depth (4 `.parent` calls from `tests/mocked/py/` to example root); docstring run-paths updated in all 27 osm-geocoder test files
- **pyproject.toml**: `testpaths` expanded from `["tests"]` to `["tests", "examples"]`
- **real/py conftest.py** scoped skip: `pytest_collection_modifyitems` now only marks tests in its own directory with the `--mongodb` skip, preventing global test skipping
- **Follow-up moves**: `test_data/` → `tests/mocked/data/`; `osmstates30.afl`, `osmstates30.json`, `run_30states.sh` → `tests/real/{afl,scripts}/` with script path references updated
- 2098 passed, 80 skipped (without `--hdfs`/`--mongodb`/`--postgis`/`boto3`)

## Completed (v0.12.18) - Remove Dead Handler Registrations for Non-Event Facets
- **`cache_handlers.py` stripped to data-only module**: removed `register_cache_handlers()`, `_DISPATCH`, `_build_dispatch()`, `handle()`, and `register_handlers()` — these registered ~250 handlers for regular facets (e.g., `osm.geo.cache.Africa.Malawi`) that expand inline via `andThen` bodies and never produce event tasks; retained `REGION_REGISTRY` (used by `operations_handlers.py` and `region_resolver.py`) and `_make_handler()`
- **`graphhopper_handlers.py` cache portion removed**: deleted `_make_cache_handler()`, `GRAPHHOPPER_CACHE_REGISTRY` (~250 entries across 9 namespaces), and cache registration loops from `_build_dispatch()` and `register_graphhopper_handlers()`; only the 6 `osm.geo.Operations.GraphHopper.*` event facet handlers remain
- **`handlers/__init__.py` updated**: removed `register_cache_handlers` import/call from `register_all_handlers()` and `reg_cache` import/call from `register_all_registry_handlers()`
- **Tests updated**: deleted `TestOsmCacheHandlers` class (3 tests); `TestOsmGraphhopperHandlers.test_dispatch_keys` now asserts `== 6` with `osm.geo.Operations.GraphHopper.*` prefix check; `TestOsmInitRegistryHandlers` threshold adjusted
- ~500 dead handler registrations removed, reducing Dashboard Handlers page clutter and memory usage
- 1684 passed, 36 skipped (without `--hdfs`/`--mongodb`/`--postgis`)

## Completed (v0.12.17) - 30-State OSM Download Workflow
- **`osmstates30.afl`** (`examples/osm-geocoder/afl/osmstates30.afl`): new AFL workflow in `osm.geo.UnitedStates.sample` namespace; `Download30States` workflow downloads OSM data for 30 randomly chosen US states (Alaska, Arizona, California, Colorado, Connecticut, Florida, Georgia, Idaho, Illinois, Indiana, Iowa, Kansas, Kentucky, Louisiana, Maine, Maryland, Michigan, Minnesota, Missouri, Montana, Nevada, NewYork, NorthCarolina, Ohio, Oregon, Pennsylvania, Tennessee, Texas, Virginia, Washington); follows the `UnitedStatesIndividually` pattern — calls each state's cache facet, downloads via `Download(cache = ...)`, yields concatenated `downloadCache` results using `++`
- **`run_30states.sh`** (`examples/osm-geocoder/run_30states.sh`): convenience startup script that creates `~/data/hdfs/{namenode,datanode}` and `~/data/mongodb` directories, bootstraps the Docker stack via `scripts/setup` with `--hdfs`, `--hdfs-namenode-dir`, `--hdfs-datanode-dir`, `--mongodb-data-dir`, and `--osm-agents 1`, waits for MongoDB readiness, compiles the AFL file with all library dependencies, submits the workflow, and prints dashboard access instructions
- 1697 passed, 35 skipped (without `--hdfs`/`--mongodb`/`--postgis`)

## Completed (v0.12.16) - Refactor MavenArtifactRunner to Subclass RegistryRunner
- **`MavenRunnerConfig(RegistryRunnerConfig)`**: now extends `RegistryRunnerConfig` instead of duplicating all infrastructure fields; only declares Maven-specific fields (`cache_dir`, `repository_url`, `java_command`, `default_timeout_ms`) plus `service_name` override (`"afl-maven-runner"`)
- **`MavenArtifactRunner(RegistryRunner)`**: now extends `RegistryRunner`, inheriting poll loop, heartbeat, server registration, thread pool/futures management, AST caching, task claiming, workflow resume, and shutdown — ~500 lines of duplicated infrastructure removed
- **Overridden methods**: `__init__` (sets `self._dispatcher = None` to disable Python module dispatch, adds Maven-specific state), `register_handler` (validates `mvn:` URI scheme then delegates to super), `_refresh_registry` (filters to `mvn:` URI registrations only), `_process_event` (Maven subprocess dispatch — unchanged logic)
- **`self._dispatcher = None`**: inherited `_resume_workflow` passes `dispatcher=self._dispatcher` to `evaluator.resume()`; with `None`, no inline dispatch occurs during resume — event facets create tasks picked up in the next poll cycle (correct behavior for Maven)
- **Test update**: `_current_time_ms` import changed from `maven_runner` to `afl.runtime.registry_runner`; all 41 Maven runner tests pass unchanged
- **No changes to `RegistryRunner`**: subclassing works without modifications to the base class
- File reduced from 853 lines to ~370 lines

## Completed (v0.12.15) - Strip Maven Example to MavenArtifactRunner Only
- **Removed simulated build lifecycle**: deleted 10 AFL files (`maven_build.afl`, `maven_resolve.afl`, `maven_publish.afl`, `maven_quality.afl`, `maven_mixins.afl`, `maven_composed.afl`, `maven_pipelines.afl`, `maven_advanced.afl`, `maven_workflows.afl`, `maven_orchestrator.afl`) and 4 handler modules (`build_handlers.py`, `resolve_handlers.py`, `publish_handlers.py`, `quality_handlers.py`) that contained only simulated/stub implementations
- **Retained runner-only files**: `maven_runner.afl` (RunMavenArtifact + RunMavenPlugin event facets), `maven_types.afl` (ExecutionResult + PluginExecutionResult schemas), `runner_handlers.py`, `maven_runner.py` (MavenArtifactRunner)
- **Simplified agent**: removed tri-mode switching (AgentPoller/RegistryRunner/MavenArtifactRunner); `agent.py` now runs MavenArtifactRunner directly — no `AFL_USE_REGISTRY`/`AFL_USE_MAVEN_RUNNER` env vars
- **Reduced `maven_types.afl`**: 9 schemas → 2 (removed ArtifactInfo, DependencyTree, BuildResult, TestReport, PublishResult, QualityReport, ProjectInfo)
- **Simplified `handlers/__init__.py`**: removed imports/registrations for resolve, build, publish, quality handlers; kept only runner_handlers
- **Pruned tests**: removed 6 test classes (`TestMavenMixins`, `TestMavenWorkflows`, `TestMavenComposedFacets`, `TestMavenPipelinesWorkflows`, `TestMavenOrchestratorWorkflows`, `TestMavenAdvancedWorkflows`) and 5 handler test classes; updated schema count (9→2) and handler registration count (14→2)
- **Rewrote documentation**: README.md and USER_GUIDE.md focused on MavenArtifactRunner only

## Completed (v0.12.14) - Composed Facets, Multiple andThen Blocks, Arithmetic & Statement-level andThen
- **Validator fix**: `_validate_and_then_block` now accepts `extra_yield_targets` parameter; inline step bodies pass the step's call target as an additional valid yield target, so `yield ResolveDependencies(...)` inside a step body validates correctly
- **`maven_composed.afl`** (new file): `maven.composed` namespace with 2 **composed facets** — `CompileAndTest` (resolve → compile+Retry → test+Timeout → package) and `FullQualityGate` (checkstyle+Timeout, dependency check+Timeout) with **arithmetic** (`total_issues = style.report.issues + security.report.issues`)
- **`maven_pipelines.afl`** (new file): `maven.pipelines` namespace with 2 workflows — `FullBuildPipeline` using **multiple andThen blocks** (concurrent build + quality paths) and `InstrumentedBuild` using **statement-level andThen** on the deps step plus **arithmetic** for duration aggregation (`total_duration_ms = build.result.duration_ms + tests.report.duration_ms`)
- **~17 new tests**: 2 validator tests (`TestStepBodyValidation` — valid/invalid inner yield targets), 7 composed facet tests (`TestMavenComposedFacets` — compilation, facet presence, steps, mixins, arithmetic, CLI check), 8 pipeline tests (`TestMavenPipelinesWorkflows` — compilation, workflow presence, multiple blocks, block steps, statement andThen, arithmetic, CLI check)
- **Documentation**: README.md with Pipelines 8-9, updated AFL source files table; USER_GUIDE.md gains "Composed Facets", "Multiple andThen Blocks", "Arithmetic Expressions", "Statement-level andThen Body" sections

## Completed (v0.12.13) - Maven Plugin Execution & Workflow-as-Step Orchestration
- **`PluginExecutionResult` schema** (`maven_types.afl`): 9th schema in `maven.types` — captures Maven plugin goal execution output with `plugin_key`, `goal`, `phase`, `exit_code`, `success`, `duration_ms`, `output`, `artifact_path`
- **`RunMavenPlugin` event facet** (`maven_runner.afl`): 2nd event facet in `maven.runner` — runs a Maven plugin goal within a workspace; parameters: `workspace_path`, `plugin_group_id`, `plugin_artifact_id`, `plugin_version`, `goal`, optional `phase`, `jvm_args`, `properties`; returns `PluginExecutionResult`
- **Plugin handler** (`runner_handlers.py`): `_run_maven_plugin_handler` with `_default_phase()` helper mapping goals to lifecycle phases; `_DISPATCH` expanded from 1 to 2 entries; total registrations 13 → 14
- **`maven_orchestrator.afl`** (new file): `maven.orchestrator` namespace with 2 workflows using **workflow-as-step** orchestration — `BuildTestAndRun` (calls `BuildAndTest` + `RunArtifactPipeline` as sub-workflows) and `PluginVerifyAndRun` (runs checkstyle + spotbugs plugins with `Timeout` mixins, then calls `RunArtifactPipeline` as sub-workflow)
- **~10 new tests**: orchestrator compilation, both workflow names present, step names for each workflow, return field verification, mixin presence on plugin steps, CLI `--check`; updated schema count (8→9), dispatch keys (1→2), register count (13→14), added plugin dispatch test
- **Documentation**: README.md with Pipelines 6-7, updated handler/AFL tables and counts; USER_GUIDE.md gains "Workflow-as-Step Orchestration" section

## Completed (v0.12.12) - RunMavenArtifact Event Facet
- **`ExecutionResult` schema** (`maven_types.afl`): 8th schema in `maven.types` namespace capturing JVM subprocess results — `exit_code`, `success`, `duration_ms`, `stdout`, `stderr`, `artifact_path`
- **`maven_runner.afl`** (new file): `maven.runner` namespace with `RunMavenArtifact` event facet — models the core MavenArtifactRunner operation (resolve Maven artifact, launch `java -jar`); parameters: `step_id`, `group_id`, `artifact_id`, `version`, optional `classifier`, `entrypoint`, `jvm_args`, `workflow_id`, `runner_id`; returns `ExecutionResult`
- **`runner_handlers.py`** (new file): simulated handler following the `_DISPATCH` pattern; builds realistic artifact path from Maven coordinates, simulates successful JVM execution; dual-mode registration (AgentPoller + RegistryRunner)
- **Handler wiring** (`handlers/__init__.py`): `register_runner_handlers` added to imports, `__all__`, `register_all_handlers()`, and `register_all_registry_handlers()` (12 → 13 total handler registrations)
- **`RunArtifactPipeline` workflow** (`maven_workflows.afl`): 5th workflow — resolves dependencies then runs Maven artifact as JVM subprocess with `Timeout(minutes = 10)` mixin; returns `success`, `exit_code`, `duration_ms`
- **7 new tests**: 3 compilation tests (runner facet parsing, parameter verification, pipeline step names) and 4 handler dispatch tests (dispatch keys, handle dispatches with result assertions, unknown facet error, register count)
- **Documentation**: README.md updated with Pipeline 5, handler/AFL tables, counts; USER_GUIDE.md gains new "Run Maven Artifacts" walkthrough section
- 1723 passed, 35 skipped (without `--hdfs`/`--mongodb`/`--postgis`)

## Completed (v0.12.11) - Maven Build Lifecycle Example
- **New example** (`examples/maven/`): Maven build lifecycle agent demonstrating AFL mixin composition and the MavenArtifactRunner JVM subprocess execution model — following the Jenkins/AWS Lambda example pattern
- **7 AFL files** defining the Maven build lifecycle domain: `maven.types` (7 schemas: ArtifactInfo, DependencyTree, BuildResult, TestReport, PublishResult, QualityReport, ProjectInfo), `maven.mixins` (6 mixin facets + 3 implicits: Retry, Timeout, Repository, Profile, JvmArgs, Settings), `maven.resolve` (3 event facets), `maven.build` (4 event facets), `maven.publish` (3 event facets), `maven.quality` (2 event facets), `maven.workflows` (4 workflows: BuildAndTest, ReleaseArtifact, DependencyAudit, MultiModuleBuild)
- **4 handler modules** (`examples/maven/handlers/`): simulated handlers with `_DISPATCH` dict pattern, `handle()` entrypoint, dual-mode registration (AgentPoller + RegistryRunner) — resolve_handlers (3), build_handlers (4), publish_handlers (3), quality_handlers (2)
- **MavenArtifactRunner** (`examples/maven/maven_runner.py`): moved from `afl/runtime/maven_runner.py` to live in the example — runs external JVM programs packaged as Maven artifacts via `mvn:groupId:artifactId:version[:classifier]` URI scheme; thread-safe artifact caching, subprocess dispatch, step continuation
- **Tri-mode agent** (`examples/maven/agent.py`): supports AgentPoller (default), RegistryRunner (`AFL_USE_REGISTRY=1`), and MavenArtifactRunner (`AFL_USE_MAVEN_RUNNER=1`) — unique to this example
- **Documentation**: README.md with pipeline descriptions, ASCII flow diagrams, reference tables; USER_GUIDE.md with step-by-step walkthrough, MavenArtifactRunner execution model concept, facet encapsulation pattern
- **~70 tests**: `tests/test_maven_compilation.py` (AFL compilation), `tests/test_handler_dispatch_maven.py` (handler dispatch), `tests/test_maven_runner.py` (runner unit tests moved from `tests/runtime/`)
- **Removed from core runtime**: `MavenArtifactRunner` and `MavenRunnerConfig` no longer exported from `afl.runtime`

## Completed (v0.12.10) - Facet Encapsulation Tutorial
- **Tutorial Part 8** (`docs/tutorial.md`): new "Facet Encapsulation" section teaching the composed facet pattern — using regular facets with `andThen` bodies to wrap event facet sequences into reusable subroutine-like units
- **The problem / solution**: explains why calling event facets directly doesn't scale and introduces composed facets as the abstraction layer
- **Before/after example**: `FetchAndTransform` wrapping `FetchData` + `TransformData` in a `pipeline` namespace; both AFL snippets verified with `afl --check`
- **Real-world example**: adapted from `examples/volcano-query/` showing `LoadVolcanoData` wrapping `Cache` + `Download`, called by `FindVolcanoes` workflow
- **Benefits table**: hide complexity, enforce ordering, swap implementations, reuse across workflows, layer abstractions
- **Baking in mixins**: adapted from `examples/jenkins/` showing `BuildAndTest` with embedded `Credentials`, `Timeout`, and `Retry` mixins invisible to callers
- **Quick Reference**: added "Composed facet" row to the reference table
- **Intro updated**: "seven progressive parts" → "eight progressive parts"
- 1633 passed, 36 skipped

## Completed (v0.12.9) - Workflows Callable as Steps in andThen Blocks
- **Emitter fix**: workflows now included in the unified `declarations` list in both `_program()` and `_namespace()`, enabling the runtime to resolve workflow names during step execution
- **Runtime fix**: `"WorkflowDecl"` added to type-check tuples in `dependency.py` (`_resolve_in_declarations`) and `evaluator.py` (`_resolve_in_declarations`, `_search_declarations_qualified` ×2, `_search_declarations`), allowing workflow declarations to be found during qualified-name resolution and facet definition lookup
- **New capability**: workflows can now be called as steps inside `andThen` blocks — an outer workflow can invoke an inner workflow, and the inner workflow's body expands inline just like a facet with a body
- **7 new tests**: 3 emitter tests (top-level, namespaced, and mixed declarations), 2 validator tests (same-namespace and cross-namespace workflow calls), 2 evaluator tests (workflow-as-step body expansion at top level and in namespace)

## Completed (v0.12.8) - AWS Lambda + Step Functions Example with LocalStack
- **New example** (`examples/aws-lambda/`): AWS serverless pipeline example with real boto3 calls against a LocalStack Docker environment, demonstrating andThen chains, call-time mixin composition, cross-namespace workflows, and foreach iteration
- **5 AFL files**: `aws.lambda.types` (7 schemas), `aws.lambda.mixins` (6 mixin facets + 3 implicits), `aws.lambda` (7 Lambda event facets), `aws.stepfunctions` (5 Step Functions event facets), `aws.lambda.workflows` (4 workflows)
- **12 handlers** across 2 modules (`lambda_handlers`, `stepfunctions_handlers`) making real boto3 calls to LocalStack (`LOCALSTACK_URL` env var, default `http://localhost:4566`); follows dual-mode dispatch adapter pattern (AgentPoller + RegistryRunner)
- **4 workflows**: DeployAndInvoke (pure andThen chain), BlueGreenDeploy (andThen + call-time mixins), StepFunctionPipeline (cross-namespace andThen + mixins), BatchProcessor (foreach + per-iteration mixins)
- **Docker integration**: LocalStack service (`localstack/localstack`) with Lambda, Step Functions, S3, IAM services; `agent-aws-lambda` service; both under `localstack` profile; `Dockerfile.aws-lambda` based on python:3.12-slim
- **23 new tests**: 13 compilation tests (types, mixins, event facets, workflows with step/foreach verification) and 10 handler dispatch tests (dispatch keys, unknown facet errors, registry registration counts); handler tests use `pytest.importorskip("boto3")` for graceful skipping
- 1636 passed, 35 skipped (without `--hdfs`/`--mongodb`/`--postgis`)

## Completed (v0.12.7) - Jenkins CI/CD Example with Mixin Composition
- **New example** (`examples/jenkins/`): Jenkins CI/CD pipeline example showcasing AFL's `with` mixin composition — small reusable facets (Retry, Timeout, Credentials, Notification, AgentLabel, Stash) composed onto event facets at both signature and call time
- **9 AFL files**: `jenkins.types` (7 schemas), `jenkins.mixins` (6 mixin facets + 3 implicits), `jenkins.scm` (2 event facets with signature-level mixin), `jenkins.build` (4), `jenkins.test` (3), `jenkins.artifact` (3), `jenkins.deploy` (3), `jenkins.notify` (2), `jenkins.pipeline` (4 workflows)
- **17 handlers** across 6 modules (`scm_handlers`, `build_handlers`, `test_handlers`, `artifact_handlers`, `deploy_handlers`, `notify_handlers`) following the dual-mode dispatch adapter pattern (AgentPoller + RegistryRunner)
- **4 workflows** demonstrating: call-time single/multiple mixins, signature-level mixin, `foreach` with per-iteration mixins, parallel stages with independent mixin composition, string concatenation in mixin args
- **42 new tests**: 17 compilation tests (types, mixins, event facets, pipeline workflows with mixin AST verification) and 25 handler dispatch tests (dispatch keys, handle routing, unknown facet errors, registry registration counts)

## Completed (v0.12.6) - Configurable External Storage for Jenkins & GraphHopper
- **Docker Compose** (`docker-compose.yml`): Jenkins and GraphHopper volume mounts now use env var substitution — `${JENKINS_HOME_DIR:-jenkins_home}:/var/jenkins_home` and `${GRAPHHOPPER_DATA_DIR:-graphhopper_data}:/data/graphhopper`; when unset, uses Docker named volumes (unchanged default); when set to a host path, creates bind mounts to external storage
- **Setup script** (`scripts/setup`): added `--jenkins-home-dir PATH` and `--graphhopper-data-dir PATH` options; `--jenkins-home-dir` auto-enables `--jenkins`; prints configured paths in status output
- **Deployment docs** (`docs/deployment.md`): new "External Storage for Jenkins" and "External Storage for GraphHopper" sections with usage examples and env var tables

## Completed (v0.12.5) - Configurable External Storage for MongoDB & PostGIS
- **Docker Compose** (`docker-compose.yml`): MongoDB and PostGIS volume mounts now use env var substitution — `${MONGODB_DATA_DIR:-mongodb_data}:/data/db` and `${POSTGIS_DATA_DIR:-postgis_data}:/var/lib/postgresql/data`; when unset, uses Docker named volumes (unchanged default); when set to a host path, creates bind mounts to external storage
- **Setup script** (`scripts/setup`): added `--mongodb-data-dir PATH` and `--postgis-data-dir PATH` options; `--postgis-data-dir` auto-enables `--postgis`; prints configured paths in status output
- **Deployment docs** (`docs/deployment.md`): new "External Storage for PostGIS" and "External Storage for MongoDB" sections with usage examples and env var tables

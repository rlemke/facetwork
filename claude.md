# CLAUDE.md — Facetwork

Facetwork is a platform for defining and executing distributed workflows. You write workflows in **FFL** (Facetwork Flow Language), and the runtime handles execution, dependency resolution, retries, and monitoring.

## Getting Started

| Guide | Audience | What You'll Learn |
|-------|----------|-------------------|
| **[Beginner's Guide](docs/getting-started/beginners-guide.md)** | New users | Local setup, running your first workflow from the UI, writing basic FFL |
| **[README](README.md)** | Developers | Installation, Docker setup, parser/emitter API, CLI usage |
| **[Full Technical Reference](#full-technical-reference)** | Contributors | Compiler internals, runtime architecture, all commands, code conventions |

## Quick Start (Local)

```bash
# Docker: start everything
docker compose up
docker compose run seed
# Open http://localhost:8080

# Or without Docker:
pip install -e ".[dev,test,dashboard,mcp,mongodb]"
cp .env.example .env              # edit MongoDB connection
scripts/seed-examples             # seed example workflows
python -m facetwork.dashboard --log-format text
# Open http://localhost:8080
```

## Running Workflows from the Dashboard

1. Open http://localhost:8080 and click **Workflows**
2. Click **New** to create a run
3. Select a workflow, fill in parameters, click **Run**
4. Watch execution on the detail page — steps, logs, and progress update live

To find running workflows, use the **Running** tab. Use **Cmd+K** to search by name.

## Common Operations

```bash
# Start/stop runners
scripts/start-runner --example osm-geocoder -- --log-format text
scripts/stop-runners
scripts/drain-runners              # stop + reset running tasks to pending

# Monitor
scripts/list-runners               # show runner fleet
scripts/db-stats                   # database document counts

# PostGIS maintenance (after large imports)
scripts/postgis-vacuum             # reclaim space + update statistics
scripts/postgis-vacuum-status      # check vacuum progress and table sizes
scripts/postgis-kill-vacuum        # kill autovacuum blocking imports
```

## Key Concepts

| Term | Meaning |
|------|---------|
| **Workflow** | Entry point for execution — defined in FFL with `andThen` steps |
| **Event Facet** | A step that requires a handler (external code) to execute |
| **Handler** | Python module that implements an event facet's logic |
| **Runner** | Service that picks up tasks and dispatches them to handlers |
| **Step** | A single unit of work within a workflow execution |
| **Source Adapter** | Namespace that extracts features from a specific data source (PBF, PostGIS, GeoJSON) into GeoJSON for downstream analysis |

### Source Adapter Pattern

The OSM geocoder uses a **source adapter pattern** to decouple data extraction from analysis. Three source namespaces normalize different inputs into GeoJSON:

| Source Namespace | Input | Handler |
|-----------------|-------|---------|
| `osm.Source.PBF` | `.osm.pbf` files via osmium | `handlers/sources/pbf_source.py` |
| `osm.Source.PostGIS` | SQL queries against `osm_nodes`/`osm_ways` | `handlers/sources/postgis_source.py` |
| `osm.Source.GeoJSON` | Existing GeoJSON files | `handlers/sources/geojson_source.py` |

Each source provides 8 extraction facets (routes, amenities, roads, parks, buildings, boundaries, population, POIs). All produce the same category-specific output schemas (`RouteFeatures`, `AmenityFeatures`, etc.) so downstream analysis facets work identically regardless of source:

```
Source Layer                        Algorithm Layer (unchanged)
─────────────                       ──────────────────────────
osm.Source.PBF.ExtractRoutes    ─┐
osm.Source.PostGIS.ExtractRoutes ─┼→ GeoJSON → RouteStatistics / FilterRoutesByType / RenderMap
osm.Source.GeoJSON.LoadRoutes   ─┘
```

Composed workflows in `osm.workflows.sourced` demonstrate the pattern:
- `BicycleRoutesPBF` / `BicycleRoutesPostGIS` / `BicycleRoutesGeoJSON` — same pipeline, different sources
- `HealthcareMapPostGIS`, `LargeCitiesPostGIS` — PostGIS-backed analysis
- `RoadsAndParksPostGIS` — multi-layer concurrent extraction

**PostGIS source** connects to the `osm` database (default: `AFL_POSTGIS_URL`). The `PostGISSource` schema takes `postgis_url` and `region` parameters. Queries use `tags JSONB` for filtering (e.g. `tags->>'amenity' = 'hospital'`).

## Project Layout

| Directory | What's There |
|-----------|-------------|
| `facetwork/` | Compiler + runtime engine |
| `facetwork/dashboard/` | Web monitoring UI (FastAPI) |
| `examples/` | 15+ example workflows with FFL, handlers, and tests |
| `docs/` | All documentation: getting-started, guides, reference, operations, architecture, contributing |
| `spec/` | Redirect stubs (documentation moved to `docs/`) |
| `scripts/` | Operations scripts (start, stop, deploy, vacuum, etc.) |
| `agents/` | Multi-language agent libraries (Python, Scala, Go, TypeScript, Java) |
| `grafana/` | Grafana provisioning: data sources, dashboards (OSM overview, spatial explorer) |

## Documentation Map

| Topic | Document |
|-------|----------|
| FFL syntax | [docs/reference/language/grammar.md](docs/reference/language/grammar.md) |
| Runtime execution model | [docs/reference/runtime.md](docs/reference/runtime.md) |
| Distributed step processing | [docs/reference/runtime.md §10.3.1](docs/reference/runtime.md) |
| Runtime implementation details | [docs/reference/runtime-impl.md](docs/reference/runtime-impl.md) |
| Building handlers | [docs/reference/agent-sdk.md](docs/reference/agent-sdk.md) |
| LLM integration | [docs/guides/llm-integration.md](docs/guides/llm-integration.md) |
| Long-running handlers | [docs/guides/long-running-handlers.md](docs/guides/long-running-handlers.md) |
| FFL examples | [docs/reference/examples.md](docs/reference/examples.md) |
| Execution traces | [docs/reference/execution-traces.md](docs/reference/execution-traces.md) |
| Build & run reference | [docs/reference/cli.md](docs/reference/cli.md) |
| Non-functional requirements | [docs/reference/nonfunctional.md](docs/reference/nonfunctional.md) |
| Architecture overview | [docs/architecture/overview.md](docs/architecture/overview.md) |
| Deployment guide | [docs/operations/deployment.md](docs/operations/deployment.md) |
| Tutorial | [docs/getting-started/tutorial.md](docs/getting-started/tutorial.md) |

---

## Full Technical Reference

*Everything below is detailed reference for contributors and operators.*

### Terminology
- **Facetwork**: The platform (compiler + runtime + agents)
- **FFL**: Facetwork Flow Language — the `.ffl` DSL for defining workflows
- **RegistryRunner** (recommended): Universal runner that auto-loads handlers from DB. Register handlers via `register_handler()` or the MCP `afl_manage_handlers` tool.

### Authoring roles

- **Domain programmers** write FFL to define workflows, facets, schemas, and composition logic — no Python needed.
- **Service provider programmers** write handler implementations (Python modules) for event facets.
- **Claude** can author both FFL definitions and handler implementations from requirements descriptions.

### Core constructs
- **Facet**: typed attribute structure with parameters and optional return clause
- **Event Facet**: facet prefixed with `event` — triggers agent execution
- **Workflow**: facet designated as an entry point for execution
- **Step**: assignment of a call expression within an `andThen` block
- **Schema**: named typed structure (`schema Name { field: Type }`) — must be defined inside a namespace

### Reserved task names

The `afl:` prefix is reserved for internal runtime tasks. User workflows, handlers, and external processes must **not** create tasks with names starting with `afl:`. The runner treats these as built-in protocol tasks with special dispatch and claiming logic.

Current internal tasks:
- `fw:execute:<WorkflowName>` — bootstrap task that starts a workflow execution (created by dashboard/CLI)
- `fw:resume:<FacetName>` — signals the RunnerService to resume a workflow after an external agent has completed a step (created by agent SDKs)

### Agent execution models
- **RegistryRunner** (recommended): auto-loads handlers from DB
- **AgentPoller**: standalone agent services with `register()` callback
- **RunnerService**: distributed orchestration with thread pool and heartbeat
- **ClaudeAgentRunner**: LLM-driven in-process execution via Claude API

### Composition features
- **Mixins**: `with FacetA() with FacetB()`
- **Implicit facets**: `implicit name = Call()`
- **andThen / yield**: multi-step logic with concurrent `andThen` blocks
- **andThen foreach**: iterate over collections with parallel execution
- **Statement-level andThen**: `s = F(x = 1) andThen { ... }`
- **catch blocks**: `catch { ... }` or `catch when { ... }` for error recovery
- **prompt blocks**: `prompt { system "..." template "..." model "..." }`
- **script blocks**: `script python "code..."` — sandboxed Python

### Expression features
- Arithmetic: `+`, `-`, `*`, `/`, `%`; concatenation: `++`
- Comparison: `==`, `!=`, `>`, `<`, `>=`, `<=`
- Boolean: `&&`, `||`, `!`
- Collections: `[1, 2, 3]`, `#{"key": "value"}`, `arr[0]`
- Conditional: `andThen when { case condition => { ... } case _ => { ... } }`

### All commands

```bash
# Tests
pytest tests/ examples/ -v
pytest tests/ examples/ -v -x
pytest tests/ examples/ --cov=afl --cov-report=term-missing

# CLI
afl input.ffl -o output.json
afl input.ffl --check

# Services
python -m facetwork.dashboard --log-format text   # web UI (port 8080)
python -m facetwork.runtime.runner                # runner service
python -m facetwork.mcp                           # MCP server (stdio)

# Runner management
scripts/start-runner --example hiv-drug-resistance -- --log-format text
scripts/stop-runners
scripts/drain-runners                  # stop + reset running tasks
scripts/drain-runners --tasks-only     # reset tasks without stopping
scripts/drain-runners --dry            # preview

# Fleet inspection
scripts/list-runners
scripts/list-runners --state running
scripts/list-runners --json

# Remote management (requires AFL_RUNNER_HOSTS or --host)
scripts/start-runner --all --example hiv-drug-resistance
scripts/stop-runners --all
scripts/rolling-deploy --example hiv-drug-resistance

# PostGIS management
scripts/start_postgres                 # start local PostgreSQL/PostGIS server
scripts/postgis-tune                   # tune PostgreSQL for bulk imports (32GB)
scripts/postgis-tune --show            # show current vs recommended settings
scripts/postgis-drop-tables            # drop osm_nodes, osm_ways, osm_import_log
scripts/postgis-drop-tables --yes      # skip confirmation
scripts/postgis-vacuum                 # VACUUM ANALYZE osm_nodes + osm_ways
scripts/postgis-vacuum --nodes         # nodes only
scripts/postgis-vacuum --ways          # ways only
scripts/postgis-vacuum --full          # VACUUM FULL (rewrites tables)
scripts/postgis-vacuum-status          # active vacuums, last times, table sizes
scripts/postgis-kill-vacuum            # kill autovacuum blocking imports
scripts/postgis-kill-vacuum --dry      # preview

# Grafana (operational monitoring — independent of dashboard)
scripts/start-grafana                  # start Grafana on port 3000
scripts/start-grafana --stop           # stop Grafana
scripts/start-grafana --status         # check if running
```

### Environment configuration
Copy `.env.example` to `.env` to configure MongoDB, scaling, overlays, and data directories. All `scripts/` commands source `_env.sh` which loads `.env` without overriding already-set vars. See `docs/reference/cli.md` for the full variable reference.

MongoDB, HDFS, and PostGIS run on external servers (defined in `/etc/hosts`): `afl-mongodb`, `afl-hadoop-hdfs`, `afl-hadoop-yarn`, `afl-postgres` — they are **not** managed by Docker Compose.

### Multi-server database access

By default, the database start scripts bind to `127.0.0.1` (localhost only). To allow other machines to connect (e.g. runners on a second server), the databases must bind to `0.0.0.0`:

- **MongoDB** — `scripts/start_mongo` uses `--bind_ip 0.0.0.0`. If you see `Connection refused` from remote hosts, verify the script has `0.0.0.0` (not `127.0.0.1`).
- **PostgreSQL** — edit `postgresql.conf` and set `listen_addresses = '*'`, then ensure `pg_hba.conf` allows connections from the remote subnet (e.g. `host all all 0.0.0.0/0 md5`).

On each remote server, add `/etc/hosts` entries pointing `afl-mongodb` and `afl-postgres` to the database server's IP address. Then start runners normally — they connect via the hostnames.

```bash
# On the database server:
scripts/start_mongo                    # binds 0.0.0.0
scripts/start_postgres                 # check listen_addresses in postgresql.conf

# On each worker server:
# /etc/hosts: 192.168.x.x afl-mongodb afl-postgres
scripts/start-runner --example osm-geocoder -- --log-format text
```

Set `ANTHROPIC_API_KEY` to enable live Claude API calls for prompt-block event facets.

Set `AFL_POSTGIS_URL` (e.g. `postgresql://afl:afl@afl-postgres:5432/afl_gis`) for PostGIS imports. Without this, the importer falls back to a hardcoded default that may not match your setup.

### Runner resilience tuning

| Variable | Default | Description |
|----------|---------|-------------|
| `AFL_REAPER_TIMEOUT_MS` | `120000` (2min) | Dead-server detection threshold |
| `AFL_STUCK_TIMEOUT_MS` | `1800000` (30min) | Stuck-task watchdog timeout |
| `AFL_TASK_EXECUTION_TIMEOUT_MS` | `900000` (15min) | Per-task execution timeout; timed-out tasks reset to pending |
| `AFL_LEASE_DURATION_MS` | `300000` (5min) | Task lease duration; renewed by handler heartbeat |

Examples can override these defaults via `runner.env` files (e.g. `examples/osm-geocoder/runner.env` sets a 4-hour execution timeout for PostGIS imports). The `start-runner` script sources these automatically. Handlers that perform blocking I/O (where heartbeats cannot fire) should register with `timeout_ms=0` and rely on the global execution timeout instead.

### MCP server

The MCP server (`python -m facetwork.mcp`) exposes FFL compiler tools, runtime management, and a PostGIS query tool. Configure it in `.mcp.json` for Claude Code integration.

**PostGIS query tool** (`afl_postgis_query`): runs read-only SQL against the OSM database. Write operations are blocked at two levels (SQL keyword filter + `default_transaction_read_only=on`). Schema:
- `osm_nodes` (osm_id, region, tags JSONB, geom Point)
- `osm_ways` (osm_id, region, tags JSONB, geom LineString)
- `osm_import_log` (region, node_count, way_count, imported_at)

Tags are JSONB — query with `tags->>'key'` or `tags?'key'`. Common tags: `amenity`, `shop`, `highway`, `building`, `name`, `cuisine`. Use `ST_*` functions for spatial queries.

osm2pgsql-compatible views (zero-storage, auto-created by `ensure_schema`):
- `planet_osm_point` — nodes with flattened tag columns (name, amenity, shop, highway, building, tourism, place, etc.)
- `planet_osm_line` — ways with flattened tag columns (highway, railway, waterway, surface, lanes, etc.)
- `planet_osm_roads` — filtered ways where `highway` or `railway` is present

### Grafana monitoring

Grafana runs independently of the dashboard for operational monitoring. Start with `scripts/start-grafana` (Docker, port 3000). Pre-provisioned dashboards:
- **OSM Import Overview** — region counts, total nodes/ways, database size, import timeline, top amenities
- **OSM Spatial Explorer** — geomap of amenities (hospitals, schools), road density, highway types, city/town/village map

Data sources connect to PostGIS (`afl_gis`) and OSM (`osm`) databases via `host.docker.internal`.

### Step recovery actions

The dashboard step detail page provides four recovery actions for failed or completed steps:

| Action | When | What it does |
|--------|------|-------------|
| **Retry** | Errored steps | Resets the step to EventTransmit; resets errored ancestor blocks |
| **Retry All Errors** | Errored blocks | Recursively finds and retries all errored leaf steps under a block |
| **Reset Block** | Errored blocks | Deletes all descendant steps/tasks/logs and restarts the block from scratch |
| **Re-run From Here** | Completed or errored | Resets the step, clears its results, deletes all downstream dependent steps, and re-executes the block from that point |

"Re-run From Here" is the primary tool for re-running a step after changing data or handler code — downstream steps are deleted and will be cleanly re-created with the new results.

### PostGIS data management
PostGIS data directory: `/Volumes/afl_data/local_servers/postgis/data`. Start with `scripts/start_postgres`, tune with `scripts/postgis-tune`. After large import batches, run `scripts/postgis-vacuum` to reclaim space and update statistics. During bulk imports, autovacuum may compete for I/O — kill it with `scripts/postgis-kill-vacuum`. Tables have `autovacuum_analyze_threshold = 1,000,000` to reduce frequency during imports.

### Workflow repair

When a workflow gets stuck (e.g. after server restarts, MongoDB downtime, or premature runner completion), use `repair-workflow` to diagnose and fix all issues at once:

```bash
scripts/repair-workflow <runner_id>         # apply repairs
scripts/repair-workflow --dry <runner_id>   # preview without changes
```

Also available as a dashboard button ("Repair Workflow" on workflow detail page) and MCP tool (`afl_repair_workflow`).

The repair performs five checks:
1. **Runner state** — if completed/failed but has non-terminal work, resets to running
2. **Orphaned tasks** — running tasks on dead/shutdown servers → pending
3. **Transient step errors** — connection/timeout errors → retry (EventTransmit)
4. **Ancestor blocks** — resets errored ancestors so execution resumes
5. **Inconsistent steps** — steps marked Complete but with failed tasks → reset to EventTransmit

**Preventative**: Runners now verify all tasks are terminal before marking a workflow as completed.

### Graceful runner shutdown
Use `scripts/drain-runners` instead of `scripts/stop-runners` when you need running tasks reset to pending. Each drained task gets a step log entry for audit visibility.

### How Claude should build and review

**Proactive design review:** When building or modifying any component, apply distributed systems best practices (Kleppmann, Nygard, Temporal) proactively. Before implementing, flag design decisions that violate known patterns — don't wait for bugs to surface. Specifically:
- For every state transition: "what if this crashes halfway?" Design the recovery path.
- For every timeout: make it heartbeat-aware. Distinguish start-to-close from last-activity.
- For every retry: add a max count and backoff. No infinite loops.
- For every shared resource (thread pool, connection, queue): consider isolation/bulkheads.
- For every log message at WARNING+: include a qualified human-readable name, not just IDs.
- For every ID: distinguish definition IDs (shared/immutable) from execution IDs (unique per run).
- For every network binding: default to `0.0.0.0`, not `127.0.0.1`.
- For every error handler: never silently return empty defaults. Fail explicitly or re-raise.

**Domain research before implementation:** Before building any handler, workflow, or integration, research the domain's established best practices, data models, and known pitfalls. For example:
- OSM/geospatial: osmium processing patterns, PostGIS indexing strategies, coordinate system conventions, bulk import best practices (COPY vs INSERT, WAL tuning, autovacuum management).
- Genomics: GATK best practices, reference genome conventions, VCF format requirements.
- Financial/risk: market data vendor conventions, time series storage patterns, numerical precision requirements.
- ETL/data pipelines: idempotent loads, schema-on-read vs schema-on-write, backfill strategies.

Apply this research to the design before writing code. Flag domain-specific constraints that affect architecture (e.g. "PostGIS bulk imports should disable autovacuum during load" or "OSM PBF files must be processed in a single pass — no random access").

See `docs/architecture/lessons-learned.md` for the full catalogue of requirements and the future roadmap.

### How Claude should review changes

**Language/compiler correctness:**
- Parsing errors must include line/column
- Grammar must be LALR-compatible (no conflicts)
- AST nodes must use dataclasses
- Emitter output uses `declarations` only (not categorized keys)

**Testing requirements:**
- All grammar constructs must have parser tests
- Error cases must verify line/column reporting
- Emitter must round-trip all AST node types

**Code quality:**
- Type hints on all functions
- Docstrings on public API
- No runtime dependencies beyond lark (dashboard, mcp deps are optional)

# CLAUDE.md — AgentFlow

AgentFlow is a platform for defining and executing distributed workflows. You write workflows in **AFL** (Agent Flow Language), and the runtime handles execution, dependency resolution, retries, and monitoring.

## Getting Started

| Guide | Audience | What You'll Learn |
|-------|----------|-------------------|
| **[Beginner's Guide](docs/beginners-guide.md)** | New users | Local setup, running your first workflow from the UI, writing basic AFL |
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
python -m afl.dashboard --log-format text
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
| **Workflow** | Entry point for execution — defined in AFL with `andThen` steps |
| **Event Facet** | A step that requires a handler (external code) to execute |
| **Handler** | Python module that implements an event facet's logic |
| **Runner** | Service that picks up tasks and dispatches them to handlers |
| **Step** | A single unit of work within a workflow execution |

## Project Layout

| Directory | What's There |
|-----------|-------------|
| `afl/` | Compiler + runtime engine |
| `afl/dashboard/` | Web monitoring UI (FastAPI) |
| `examples/` | 15+ example workflows with AFL, handlers, and tests |
| `spec/` | Language and runtime specifications |
| `scripts/` | Operations scripts (start, stop, deploy, vacuum, etc.) |
| `agents/` | Multi-language agent libraries (Python, Scala, Go, TypeScript, Java) |
| `grafana/` | Grafana provisioning: data sources, dashboards (OSM overview, spatial explorer) |

## Documentation Map

| Topic | Document |
|-------|----------|
| AFL syntax | [spec/10_language.md](spec/10_language.md) |
| Runtime execution model | [spec/30_runtime.md](spec/30_runtime.md) |
| Distributed step processing | [spec/30_runtime.md §10.3.1](spec/30_runtime.md) |
| Runtime implementation details | [spec/31_runtime_impl.md](spec/31_runtime_impl.md) |
| Building handlers | [spec/60_agent_sdk.md](spec/60_agent_sdk.md) |
| LLM integration | [spec/61_llm_agent_integration.md](spec/61_llm_agent_integration.md) |
| AFL examples | [spec/70_examples.md](spec/70_examples.md) |
| Execution traces | [spec/75_execution_traces.md](spec/75_execution_traces.md) |
| Deployment & Docker | [spec/90_nonfunctional.md](spec/90_nonfunctional.md) |
| Architecture overview | [architecture.md](architecture.md) |
| Deployment guide | [deployment.md](deployment.md) |
| Tutorial | [tutorial.md](tutorial.md) |

---

## Full Technical Reference

*Everything below is detailed reference for contributors and operators.*

### Terminology
- **AgentFlow**: The platform (compiler + runtime + agents)
- **AFL**: Agent Flow Language — the `.afl` DSL for defining workflows
- **RegistryRunner** (recommended): Universal runner that auto-loads handlers from DB. Register handlers via `register_handler()` or the MCP `afl_manage_handlers` tool.

### Authoring roles

- **Domain programmers** write AFL to define workflows, facets, schemas, and composition logic — no Python needed.
- **Service provider programmers** write handler implementations (Python modules) for event facets.
- **Claude** can author both AFL definitions and handler implementations from requirements descriptions.

### Core constructs
- **Facet**: typed attribute structure with parameters and optional return clause
- **Event Facet**: facet prefixed with `event` — triggers agent execution
- **Workflow**: facet designated as an entry point for execution
- **Step**: assignment of a call expression within an `andThen` block
- **Schema**: named typed structure (`schema Name { field: Type }`) — must be defined inside a namespace

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
afl input.afl -o output.json
afl input.afl --check

# Services
python -m afl.dashboard --log-format text   # web UI (port 8080)
python -m afl.runtime.runner                # runner service
python -m afl.mcp                           # MCP server (stdio)

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
Copy `.env.example` to `.env` to configure MongoDB, scaling, overlays, and data directories. All `scripts/` commands source `_env.sh` which loads `.env` without overriding already-set vars. See `spec/90_nonfunctional.md` for the full variable reference.

MongoDB, HDFS, and PostGIS run on external servers (defined in `/etc/hosts`): `afl-mongodb`, `afl-hadoop-hdfs`, `afl-hadoop-yarn`, `afl-postgres` — they are **not** managed by Docker Compose.

Set `ANTHROPIC_API_KEY` to enable live Claude API calls for prompt-block event facets.

Set `AFL_POSTGIS_URL` (e.g. `postgresql://afl:afl@afl-postgres:5432/afl_gis`) for PostGIS imports. Without this, the importer falls back to a hardcoded default that may not match your setup.

### Runner resilience tuning

| Variable | Default | Description |
|----------|---------|-------------|
| `AFL_REAPER_TIMEOUT_MS` | `120000` (2min) | Dead-server detection threshold |
| `AFL_STUCK_TIMEOUT_MS` | `1800000` (30min) | Stuck-task watchdog timeout |
| `AFL_TASK_EXECUTION_TIMEOUT_MS` | `900000` (15min) | Per-task execution timeout; timed-out tasks reset to pending |
| `AFL_LEASE_DURATION_MS` | `300000` (5min) | Task lease duration; renewed by handler heartbeat |

### MCP server

The MCP server (`python -m afl.mcp`) exposes AFL compiler tools, runtime management, and a PostGIS query tool. Configure it in `.mcp.json` for Claude Code integration.

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

### Graceful runner shutdown
Use `scripts/drain-runners` instead of `scripts/stop-runners` when you need running tasks reset to pending. Each drained task gets a step log entry for audit visibility.

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

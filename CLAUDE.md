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

# Local-first import (reduces I/O on main PG during large imports)
scripts/start-import-pg            # start disposable Docker PG on port 5433
scripts/start-import-pg --stop     # stop and remove
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

The osm-geocoder example demonstrates a **source adapter pattern** that decouples data extraction (PBF / PostGIS / GeoJSON inputs) from analysis (statistics, filtering, rendering). The detailed contract — namespace layout, schema unification, and composed workflows — lives in the [osm-geocoder repo's CLAUDE.md](https://github.com/rlemke/fwh_osm/blob/main/CLAUDE.md).

When building a new domain pipeline that ingests from multiple data sources, mirror this pattern: one source namespace per input format, all producing the same category-specific output schemas so downstream analysis is source-agnostic.

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
| **Multi-language (polyglot) handlers** — Java/Go/Scala/TS handlers alongside Python via the Mongo-coordinated agent protocol (name-filtered claim → coexistence); worked example: the embedded-GraphHopper Java agent (`fwh_osm/java/osm-gh-router`) | [docs/guides/multi-language-handlers.md](docs/guides/multi-language-handlers.md) |
| LLM integration | [docs/guides/llm-integration.md](docs/guides/llm-integration.md) |
| Long-running handlers | [docs/guides/long-running-handlers.md](docs/guides/long-running-handlers.md) |
| FFL examples | [docs/reference/examples.md](docs/reference/examples.md) |
| Execution traces | [docs/reference/execution-traces.md](docs/reference/execution-traces.md) |
| Build & run reference | [docs/reference/cli.md](docs/reference/cli.md) |
| Non-functional requirements | [docs/reference/nonfunctional.md](docs/reference/nonfunctional.md) |
| Architecture overview | [docs/architecture/overview.md](docs/architecture/overview.md) |
| **`/ffl-first`** — fulfill a request as a reviewed FFL workflow (discover→reuse→gated-author→gated-scaffold→FFL-only data→run); two review gates (workflow shown before run, handler shown before use); `scripts/ffl-run` is its dashboard-visible run mechanism | [docs/guides/ffl-first.md](docs/guides/ffl-first.md) |
| Claude workflow catalog (store/version/run FFL with no file; `fw_catalog_*` MCP tools) | [docs/architecture/claude-workflow-catalog.md](docs/architecture/claude-workflow-catalog.md) |
| `use` resolution: file-based compile vs. the catalog (hermetic pinned-dep model) | [docs/architecture/catalog-use-resolution.md](docs/architecture/catalog-use-resolution.md) |
| Extending with new handlers (NL needs a capability no facet provides → detect gap → scaffold facet+handler+test) — `scripts/scaffold-handler` | [docs/architecture/extending-with-new-handlers.md](docs/architecture/extending-with-new-handlers.md) |
| Composable facet library (design): orthogonal/complete/discoverable/distributed primitives for LLM-composed workflows + the memory-of-solved-requests moat | [docs/architecture/composable-facet-library.md](docs/architecture/composable-facet-library.md) |
| Approximate freeway routing (`osm.Network`, design): pure in-process graph search over a tiny noded-freeway artifact — no engine daemon; tiny network → read-once-per-runner, embarrassingly parallel | [docs/architecture/approximate-freeway-routing.md](docs/architecture/approximate-freeway-routing.md) |
| Deployment guide | [docs/operations/deployment.md](docs/operations/deployment.md) |
| Full-stack Docker Compose (one runner per fwh_* example) | [docs/operations/full-stack-compose.md](docs/operations/full-stack-compose.md) |
| **Multi-server fleet** (`fleet`/`fleet-agent`/`start-runner --fleet`: shared external MinIO+MongoDB, central config, encrypted secrets, discovery) **+ local simulation** (`scripts/simulate-fleet`) | [#multi-server-runner-fleet--local-simulation](#multi-server-runner-fleet--local-simulation) · [docs/operations/deployment.md](docs/operations/deployment.md) |
| **Fleet rollouts & runner lifecycle** — image-based change deployment (buildx→registry→`fleet set --image`), what happens to running tasks during a rollout (graceful drain → reaper recovery → retry/dead-letter), start/stop/drain runners from the CLI, and how this auto-deploy compares to Kubernetes/Temporal-grade pipelines | [docs/operations/fleet-rollouts.md](docs/operations/fleet-rollouts.md) |
| **Informal fleet — your team's own machines as the cluster** — only central MongoDB+MinIO must be stable; runner machines (desktops/laptops) are stateless & disposable and can come/go (reaper → re-claim); who this model is for (small/research teams); large-scale data-center operation is architecturally **possible but untested** — treat it as a real engineering investment (hardening/scheduling/observability), not a config change | [docs/operations/informal-fleet.md](docs/operations/informal-fleet.md) |
| Tutorial | [docs/getting-started/tutorial.md](docs/getting-started/tutorial.md) |
| Tools + handlers pattern (per-domain CLI + `_<pkg>_tools/` + shim) | [agent-spec/tools-pattern.agent-spec.yaml](agent-spec/tools-pattern.agent-spec.yaml) |
| Cache layout (sidecars, namespaces, cache types) | [agent-spec/cache-layout.agent-spec.yaml](agent-spec/cache-layout.agent-spec.yaml) |
| MCP context-engineering design (rule_ids, docs URIs) | [docs/architecture/mcp-context-engineering.md](docs/architecture/mcp-context-engineering.md) |
| Validator rule docs (one file per rule_id) | [docs/reference/rules/](docs/reference/rules/) |
| Canonical FFL examples | [examples/canonical/](examples/canonical/) |

## Writing and fixing FFL — the validator-driven loop

The validator emits structured `rule_id`s with `docs_uri` pointers. Use this loop instead of guessing:

1. Run `fw_validate` (MCP) or `afl <file> --check` on the source.
2. For each error, fetch `afl://docs/rules/{rule_id}` for paired wrong/right examples and a "why".
3. Apply the fix and re-validate.

When writing new FFL, start from a canonical example under [`examples/canonical/`](examples/canonical/) — every file there is a small, validator-clean template. The MCP server also exposes them at `afl://examples/canonical/{name}`.

Key constraints the rule docs cover (and the language enforces):

- **Workflows and schemas must live inside a namespace** (`WORKFLOW_AT_TOP_LEVEL`, `SCHEMA_AT_TOP_LEVEL`).
- **References are always `step.field` form** — bare step names are not valid expressions (`REF_INVALID_STEP_FORMAT`).
- **`foreach` loop variables are accessed via `$.<varname>`** inside the block (same scope as workflow inputs).
- **Yield targets** must be the containing facet OR one of its declared mixins (`YIELD_INVALID_TARGET`).
- **`when` blocks need a default case, last** (`WHEN_MISSING_DEFAULT`, `WHEN_DEFAULT_NOT_LAST`).
- **No truthy/falsy coercion** — comparisons return Boolean, and `&&`/`||`/`!` only accept Boolean operands.

When adding a new validator check, give it a `rule_id` AND write the matching `docs/reference/rules/{rule_id}.md` in the same change. Coverage is currently exact (41/41 emitted rule_ids documented); the script that diffs them lives in [docs/architecture/mcp-context-engineering.md](docs/architecture/mcp-context-engineering.md).

## Standalone example packages

Examples can live either in this repo under `examples/<name>/` or as
separate pip-installable packages declaring the `facetwork.examples`
entry point. Both are discovered by `scripts/start-runner --example <name>`
and `scripts/seed-examples`. See `example-template/` for the standalone
package layout. To clone + install one (or all) of the standalone
examples, use the registry-driven helper:

```bash
scripts/install-example --list              # see what's registered
scripts/install-example anthropic --check   # clone, pip install -e, verify
scripts/install-example --all               # install every registered example
scripts/install-anthropic --all --check     # convenience wrapper for the
                                            # anthropic package — picks pip
                                            # extras (agent_sdk, mcp) and
                                            # reports env readiness
```

It clones into `~/fw_handlers/` (override via `--dir` or
`FWH_HANDLERS_ROOT`), `pip install -e`s each repo against the current
venv (auto-detected as `.venv/` if present), and the `--check` flag
confirms the example surfaces in `facetwork.examples`'s entry-point
discovery.

The following examples have been extracted into their own repos and
surface as `--example <name>` exactly like in-repo examples:

- [osm-geocoder](https://github.com/rlemke/fwh_osm) — production-scale OSM ingestion
- [osm-lz](https://github.com/rlemke/fwh_osm_lz) — pure-FFL workflow catalog over `fwh_osm`
- [noaa-weather](https://github.com/rlemke/fwh_noaa_weather) — NOAA GHCN/NDBC climate trends
- [jenkins](https://github.com/rlemke/fwh_jenkins) — CI/CD mixin composition demo
- [census-us](https://github.com/rlemke/fwh_census_us) — US Census ACS + TIGER
- [genomics](https://github.com/rlemke/fwh_genomics) — foreach fan-out, joint genotyping
- [sensor-monitoring](https://github.com/rlemke/fwh_sensor_monitoring) — sensor pipelines, RegistryRunner-first
- [anthropic](https://github.com/rlemke/fwh_anthropic) — multi-area wrappers for surfaces at github.com/anthropics (16 facets across Messages / Batch / Files / Agent SDK / Claude Code / Computer Use + `DocumentQA` composition workflow + opt-in live tests)
- [save-earth](https://github.com/rlemke/fwh_save_earth) — open environmental datasets (OpenLitterMap, EPA Superfund/Brownfields, EPA TRI) → cached GeoJSON → MapLibre HTML maps; source-adapter + tools/`_<pkg>_tools`/shim pattern

## Domain pipelines — tools / handlers / cache pattern

Every domain ingestion pipeline (osm-geocoder, noaa-weather, …) follows one contract: a `tools/` dir of Python CLIs + shell wrappers backed by `tools/_<pkg>_tools/`, FFL handlers that call into the same `_<pkg>_tools/` via a `handlers/shared/<domain>_utils.py` shim, and a sidecar-backed cache under `$AFL_CACHE_ROOT/<namespace>/`. Canonical examples:

- [github.com/rlemke/fwh_osm](https://github.com/rlemke/fwh_osm) — OSM PBF → GeoJSON → tiles → HTML maps (standalone repo)
- [github.com/rlemke/fwh_noaa_weather](https://github.com/rlemke/fwh_noaa_weather) — NOAA GHCN → station CSVs → climate trends (standalone repo; `pip install -e ~/fw_handlers/fwh_noaa_weather`)

When building or modifying a domain pipeline, read [`agent-spec/tools-pattern.agent-spec.yaml`](agent-spec/tools-pattern.agent-spec.yaml) first — it defines the directory layout, CLI contract (argparse / stderr / stdout / exit codes), `_<pkg>_tools/` vs handler split, cache sidecar protocol, and test-writing rules.

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

### Task claiming — a runner only takes tasks it can run

A runner **only claims tasks for facets it has a handler for**, plus the
`fw:execute` / `fw:resume` protocol tasks (every runner can bootstrap a
workflow and process resume tasks). `claim_task()` is name-filtered server-side
(exact match or `<name>:` prefix), so a runner never picks up a task outside the
set of names it polls for. A `--registry` runner advertises a facet only if its
handler module is *importable in that process* (`preload(verify=True)`), so in a
one-runner-per-example deployment each runner claims just its own example's
facets. If a task does land on a runner with no handler for it, the runner
releases it back to `pending` (with backoff) rather than failing it — it is
failed only after retries are exhausted, i.e. no runner in the fleet could
service it. See [docs/reference/runtime-impl.md §17.1.1](docs/reference/runtime-impl.md).

### Per-runner polling — runners do not contend

Each runner is an autonomous process; all coordination goes through MongoDB.
`claim_task()` is a single atomic `find_one_and_update` — there is no shared
in-process dispatcher, no leader, no queue lock. Two consequences:

- **Different filters → disjoint pools.** RunnerA polling for `osm.*` and
  RunnerB polling for `anthropic.*` issue different Mongo queries and never
  see each other's tasks. A 1000-task `osm` backlog adds zero latency to an
  `anthropic` claim.
- **Same filters → race, not queue.** When multiple runners can handle a task,
  they race on the atomic claim; exactly one wins, the rest get `None` and
  poll again. There is no fairness, no FIFO across runners — just per-poll
  first-come-first-served on whatever is pending.

Task creation is also lock-free: each task has a unique `uuid` and is written
once by its creator, so there's no write contention either. See
[docs/reference/runtime-impl.md §17.1.3](docs/reference/runtime-impl.md).

### Task-list routing — derived from the facet namespace

Tasks carry a `task_list_name` for per-domain queue isolation (so a flooded
`osm` queue can't starve `anthropic` claims), and it is **derived from the
facet's top-level namespace** — never configured. The same intrinsic fact (the
qualified name) is used on **both** sides of the queue, so the label can't
desync from where the handler lives:

- **Producing** — a task for `osm.cache.Download` is tagged with list `osm`
  (`namespace_of`); submission tags the `fw:execute:<Workflow>` bootstrap with
  the workflow's namespace.
- **Consuming** — a runner polls the namespaces of the handlers it loaded
  (`namespaces_for`) plus its `--task-list` (default `"default"`, for shared/
  unnamespaced work). An `osm.*` runner polls `osm`.

A task is claimed only if it's on one of the runner's namespace lists **and**
the runner has its handler, so work always reaches a runner that can serve it.
Continuation tasks (`_fw_continue`) stay on a shared internal list every runner
polls (step-state-machine machinery, not handler work). There is **no**
`AFL_WORKFLOW_TASK_LIST_MAP` and no `--task-list` plumbing for routing — the
namespace is the routing key. See
[facetwork/runtime/task_list_routing.py](facetwork/runtime/task_list_routing.py).

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

# Local-first PostGIS import (Docker-based disposable instance)
scripts/start-import-pg                # start import PG on port 5433
scripts/start-import-pg --stop         # stop and remove container
scripts/start-import-pg --status       # check if running
scripts/start-import-pg --url          # print connection URL

# Grafana (operational monitoring — independent of dashboard)
scripts/start-grafana                  # start Grafana on port 3000
scripts/start-grafana --stop           # stop Grafana
scripts/start-grafana --status         # check if running
```

### Environment configuration
Copy `.env.example` to `.env` to configure MongoDB, scaling, overlays, and data directories. All `scripts/` commands source `_env.sh` which loads `.env` without overriding already-set vars. See `docs/reference/cli.md` for the full variable reference.

MongoDB, HDFS, PostGIS, and the shared MinIO object store run on external/infra servers (defined in `/etc/hosts`): `afl-mongodb`, `afl-hadoop-hdfs`, `afl-hadoop-yarn`, `afl-postgres`, `afl-minio` — they are **not** managed by Docker Compose. `afl-minio` is the fleet-wide S3 endpoint default (`AFL_S3_ENDPOINT=http://afl-minio:9000`); add an `/etc/hosts` entry on each server pointing it at the infra host (e.g. `192.168.68.96 afl-minio`). For Docker runner containers (which don't read the host's `/etc/hosts`), `docker-compose.fleet.yml` maps `afl-minio`/`afl-mongodb` to the infra host via `extra_hosts` (defaulting `AFL_INFRA_IP` to the infra IP), and the bundled `docker-compose.full-stack.yml` exposes MinIO under the `afl-minio` network alias.

### Durable storage backend (cache + outputs)

Handler caches and outputs live on a backend selected by `AFL_STORAGE` + `AFL_DATA_ROOT`: `local` (a path such as `/Volumes/afl_data`), `hdfs://`, or `s3://` — AWS S3 or a self-hosted **MinIO**. On `s3`/`hdfs`, step payloads carry portable `s3://`/`hdfs://` URIs that any runner on any host can resolve, so a multi-server fleet needs no shared disk; object stores don't do partial writes, so handlers stage to a local scratch dir and finalize on close (keep `AFL_OUTPUT_BASE`/`AFL_LOCAL_SCRATCH` **local**).

`docker-compose.full-stack.yml` **bundles a MinIO service** and the OSM runners (`osm-geocoder`, `osm-lz`) default to it — durable cache + output go to `s3://afl-cache` (console http://localhost:9001, `minioadmin`/`minioadmin`), **no external disk**. The legacy local cache at `/Volumes/afl_data/cache` was migrated into the bundled MinIO with `scripts/_cache_to_minio_move.py` (host-driven, verify-before-delete, idempotent). Full setup, the env contract, and the cache-migration recipe live in [docs/operations/deployment.md](docs/operations/deployment.md) → **S3 / MinIO Integration**.

### Multi-server database access

By default, the database start scripts bind to `127.0.0.1` (localhost only). To allow other machines to connect (e.g. runners on a second server), the databases must bind to `0.0.0.0`:

- **MongoDB** — `scripts/start_mongo` uses `--bind_ip 0.0.0.0`. If you see `Connection refused` from remote hosts, verify the script has `0.0.0.0` (not `127.0.0.1`).
- **PostgreSQL** — edit `postgresql.conf` and set `listen_addresses = '*'`, then ensure `pg_hba.conf` allows connections from the remote subnet (e.g. `host all all 0.0.0.0/0 md5`).

On each remote server, add `/etc/hosts` entries pointing `afl-mongodb` and `afl-postgres` to the database server's IP address. Then start runners normally — they connect via the hostnames.

```bash
# On the database server:
scripts/start_mongo                    # binds 0.0.0.0
scripts/start_postgres                 # check listen_addresses in postgresql.conf

# On each runner server:
# /etc/hosts: 192.168.x.x afl-mongodb afl-postgres
scripts/start-runner --example osm-geocoder -- --log-format text
```

Set `ANTHROPIC_API_KEY` to enable live Claude API calls for prompt-block event facets.

Set `AFL_POSTGIS_URL` (e.g. `postgresql://afl:afl@afl-postgres:5432/afl_gis`) for PostGIS imports. Without this, the importer falls back to a hardcoded default that may not match your setup.

### Multi-server runner fleet + local simulation

**Server-role model — there is no "master".** The fleet has exactly two kinds of
participant: (1) **infra services** — MongoDB, MinIO, and the Dashboard — each
identified by its **access URL only**; the fleet never enumerates their cluster
members, so each may be a single node, a replica set / distributed deployment, or
a managed service; and (2) **runner servers** — every Facetwork server is a
homogeneous, stateless runner (`AFL_SERVER_GROUP` role tag, default `runner`),
holding nothing but local scratch. Runners are leaderless and don't contend
(coordination is the atomic `claim_task()` in Mongo), so any runner with Mongo
access can also **seed** workflow definitions (`scripts/seed-examples`) — no box
is privileged. The central `fleet set --mongo-url … --minio … --dashboard-url …`
records those three service URLs; the dashboard's Fleet page shows them as
URL-addressed services, not as fleet servers.

A fleet of runner servers shares **one** MongoDB + **one** MinIO; each extra
server runs *runners only* and self-configures from a central config. The
controller lives in `scripts/`:

| Script | Role |
|--------|------|
| `scripts/start-runner --fleet` | Bring up runner **containers** on this host against an external Mongo+MinIO (`.env.fleet`); preflight-checks both. `--example NAME` runs any per-example runner (default osm-geocoder + osm-lz); `--docker` is the same but against the local bundled infra. `docker-compose.fleet.yml` is the override that drops the bundled infra (now applied to every per-example runner via a YAML anchor). `scripts/start-worker` is a back-compat shim for `start-runner --fleet`. |
| `scripts/fleet` | Admin (run anywhere): `set` the central config (MinIO endpoint / replicas / image), `status` (live runners + per-host drift), `secret gen-key\|set\|show` (MinIO creds encrypted in Mongo with `AFL_FLEET_KEY`). |
| `scripts/fleet-agent` | Per server: `apply` (one-shot) or `watch` (daemon) — reads the central config, **discovers Mongo** (explicit → `AFL_MONGODB_URL` → mDNS → `afl-mongodb`), decrypts creds, brings runners up, records drift. Bootstrap = the Mongo URL only. |
| `scripts/fleet-advertise` | Optional mDNS advertiser for the infra host (needs `zeroconf`). |

A new server joins with one command — `export AFL_FLEET_KEY=<key>; scripts/fleet-agent watch` — and the whole fleet is driven by editing the central config once (`fleet set --osm-replicas N`, `--image …`). Full guide: [docs/operations/deployment.md](docs/operations/deployment.md) → **"Adding a server to the fleet"** / **"Central fleet config"**.

> **Bringing up an ADDITIONAL runner server that points at this fleet's shared
> infra (MongoDB + MinIO)? Follow [docs/operations/join-fleet-from-new-server.md](docs/operations/join-fleet-from-new-server.md)** — it
> has the infra host's coordinates, the clone/venv/config steps, and `cp
> .env.fleet.preset .env.fleet && scripts/start-runner --fleet`. The pre-filled
> [`.env.fleet.preset`](../.env.fleet.preset) points at the shared infra URLs.

**Local simulation — verify the whole thing on one box.** `scripts/simulate-fleet`
spins up N "servers" as separate compose projects on the running full-stack's
shared network, each brought up by `fleet-agent` from the central config +
encrypted secret store, then runs a fan-out heat map across them and confirms the
leaves distributed across servers and the merge pulled every server's MinIO output.

```bash
# Prereqs: the full stack must be up (shared MinIO+MongoDB), and the venv must have
# pymongo + cryptography (for the secret store; zeroconf only for mDNS):
docker compose -f docker-compose.full-stack.yml up -d        # shared infra
pip install -e ".[mongodb,s3]" && pip install cryptography   # if not already

scripts/simulate-fleet --servers 3                           # bring up 3 servers + fleet status
#   then submit a fan-out:  scripts/ffl-run --primary .../osmheatmap.ffl --library … \
#     --workflow osm.heatmap.ContinentHeatmap \
#     --inputs '{"region_names":["California","Nevada","Arizona"]}' --task-list osm
scripts/simulate-fleet --down                                # tear it all down
```

It addresses the shared MinIO/Mongo by the host's LAN IP (reachable from both the
host and the sim containers). Each sim "server" is `fleet-srv1/2/3`; `fleet status`
shows them up-to-date and the fan-out's `ByScript` leaves run on different servers.

### Runner resilience tuning

| Variable | Default | Description |
|----------|---------|-------------|
| `AFL_REAPER_TIMEOUT_MS` | `120000` (2min) | Dead-server detection threshold |
| `AFL_STUCK_TIMEOUT_MS` | `1800000` (30min) | Stuck-task watchdog timeout |
| `AFL_TASK_EXECUTION_TIMEOUT_MS` | `900000` (15min) | Per-task execution timeout; timed-out tasks reset to pending |
| `AFL_LEASE_DURATION_MS` | `300000` (5min) | Task lease duration; renewed by handler heartbeat |

Examples can override these defaults — local examples via a `runner.env` file in their directory, standalone packages via `runner_env={...}` on their `ExamplePackage`. The `start-runner` script applies them automatically. Handlers that perform blocking I/O (where heartbeats cannot fire) should register with `timeout_ms=0` and rely on the global execution timeout instead.

### MCP server

The MCP server (`python -m facetwork.mcp`) exposes FFL compiler tools, runtime management, and a PostGIS query tool. Configure it in `.mcp.json` for Claude Code integration.

**Facet capability index** (`fw_capabilities`): the facet-level analogue of `fw_catalog_search` (which finds workflows). Searches the deployed/seeded facet library — or an FFL `source` snippet you're authoring — and returns ranked facets `{qualified_name, purpose, signature, params, returns, namespace, is_event, effect, cost}` so an LLM discovers composable primitives by *intent* ("what operates on a GeoJSON path?", "reverse geocode") instead of guessing names. Facets may declare **effect/cost** via `with Effect(kind="pure"|"external"|"io")` / `with Cost(tier="free".."expensive")` mixins (cost is also inferred from a `with Timeout(minutes=…)`); `fw_capabilities(effect=, max_cost=)` filters on them so the composer prefers pure/cheap primitives and sees which steps hit an engine. Built from the compiled FFL (`emit_dict` / a flow's `compiled_ast`) by `facetwork/capabilities/`; pairs with a domain tag vocabulary (e.g. `osm.Vocab.ResolveTag`, NL term → `key=value`) for NL→tag, together turning compose-a-workflow into *lookup-then-compose*. See [docs/architecture/composable-facet-library.md](docs/architecture/composable-facet-library.md) §6.

**Reuse-first catalog matching** (`fw_catalog_match`): the workflow-level counterpart — before authoring, match an NL `request` against the catalog **by intent** (each revision's authoring `summary`, weighted over title/tags/description/facets) and get a `reuse` / `review` / `author_new` verdict + the best candidate's `param_schema` to fill. Reuse an existing workflow (run via `fw_catalog_run`) instead of re-authoring; a miss → author one, which then joins the catalog. See `CatalogService.match`.

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

### Local-first PostGIS import

For large imports, a disposable Docker-based PostgreSQL instance can absorb the hours of PBF parsing I/O, then bulk-transfer the finished data to the main server. This isolates the main server from sustained write pressure during imports.

```bash
# Start the local import instance (Docker, port 5433)
scripts/start-import-pg
scripts/start-import-pg --status       # check if running
scripts/start-import-pg --stop         # stop and remove

# Enable local-first import (add to .env or runner.env)
AFL_IMPORT_POSTGIS_URL=postgresql://afl_osm:afl_osm_2024@localhost:5433/osm
```

When `AFL_IMPORT_POSTGIS_URL` is set, `import_to_postgis()` follows this flow:
1. Check prior-import log on the **main** server (skip if already imported)
2. Parse PBF and stage data on the **local** instance (fast — disposable, no WAL)
3. Merge staging into local main tables (no index contention with readers)
4. Transfer via `COPY` binary stream from local to main server staging tables
5. Batched merge into main server tables (upsert or plain insert)
6. Write audit log on the **main** server

The local instance is tuned with `fsync=off`, `synchronous_commit=off`, and `autovacuum=off` — it's disposable, so crash recovery is simply re-importing from PBF. Works on the same host as the main PostgreSQL (different port) or on a separate machine.

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

### Terminating abandoned workflows

For runs that retry/repair can't recover (e.g. a test that failed at the
handler layer because of a missing API key or removed binary), use
`scripts/terminate-workflow` to mark the runner, all non-terminal steps,
and pending/running tasks as terminal so the stuck-step sweep stops
re-processing them.

```bash
scripts/terminate-workflow <runner_id> [<runner_id> ...]
scripts/terminate-workflow --workflow osm.UnitedStates.analysis.AnalyzeRegion  # all stuck runs of this workflow
scripts/terminate-workflow --workflow Foo --workflow Bar --dry                 # preview
```

Use this only after deciding the work isn't recoverable — `repair-workflow`
is the right call for transient failures.

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

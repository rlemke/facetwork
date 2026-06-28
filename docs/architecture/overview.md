# Facetwork Architecture Overview

Facetwork is a platform for distributed workflow execution. It combines a compiler for the **Facetwork Flow Language (FFL)**, a runtime engine that evaluates workflows iteratively, and multi-language agent libraries that process event-driven tasks. MongoDB serves as the persistence and coordination layer.

## System Architecture

```
                         FFL Source (.ffl files)
                                |
                    +-----------v-----------+
                    |      FFL Compiler      |
                    |  Parser -> Transformer |
                    |  -> Validator -> Emitter|
                    +-----------+-----------+
                                |
                          JSON Workflow
                          Definition
                                |
          +---------------------+---------------------+
          |                                           |
+---------v---------+                     +-----------v-----------+
|   Runtime Engine   |                     |     MCP Server        |
| Evaluator, State   |<----- MongoDB ---->|  (LLM Integration)    |
| Machine, Persist.  |                     +-----------------------+
+---------+----------+
          |
     Event Tasks
          |
    +-----+------+-----+------+-----+
    |            |            |           |
+---v---+  +----v----+  +----v----+  +---v---+
|Python |  |  Scala  |  |   Go    |  | Java/ |
|Agent  |  |  Agent  |  |  Agent  |  |  TS   |
+-------+  +---------+  +---------+  +-------+

+---------------------------------------------------+
|                  Dashboard (FastAPI)                |
|         Web monitoring UI on port 8080             |
+---------------------------------------------------+
```

## Compiler Pipeline

The compiler transforms FFL source code into JSON workflow definitions through four stages.

| Stage | Module | Description |
|-------|--------|-------------|
| **Parser** | `afl/parser.py` | Lark LALR parser reads `.ffl` source, produces a parse tree. Errors include line/column. |
| **Transformer** | `afl/transformer.py` | Converts the Lark parse tree into typed AST dataclass nodes. |
| **Validator** | `afl/validator.py` | Semantic checks: duplicate names, type mismatches, schema resolution, unresolved references. |
| **Emitter** | `afl/emitter.py` | Serializes the AST to stable JSON output with optional source locations and provenance. |

**Key files:**
- Grammar definition: `afl/grammar/afl.lark`
- AST node types: `afl/ast.py`
- CLI entry point: `afl/cli.py`
- Source management: `afl/source.py`, `afl/loader.py`

## Runtime Engine

The runtime evaluates compiled workflows using an iterative, dependency-driven execution model.

### Evaluator (`afl/runtime/evaluator.py`)

The Evaluator orchestrates workflow execution:
1. Creates a starting step from the workflow definition
2. Iterates: for each step, runs the state machine until the step reaches a terminal or blocked state
3. When a step reaches `EVENT_TRANSMIT`, the workflow pauses and creates a task for external agents
4. On resume, the evaluator picks up where it left off, creating new steps as dependencies resolve

**Distributed execution:** For multi-server deployments, `process_single_step()` replaces the full-workflow `resume()`. Each server processes one step at a time, cascading up through parent blocks and generating continuation events (`_afl_continue` tasks) for blocks that need re-evaluation. This eliminates per-workflow locks and enables linear scaling across 100+ servers. Step updates use optimistic concurrency (`version.sequence`) to safely handle concurrent processing.

### State Machine (`afl/runtime/changers/`)

Each step progresses through a state machine with phases:
- **Initialization**: resolve parameters and input references
- **Scripts**: execute prompt/script blocks (if any)
- **Mixin blocks**: process `with` compositions
- **Event transmit**: pause for external agent processing (event facets only)
- **Statement blocks**: process `andThen` sub-blocks
- **Capture**: collect return values
- **Completion**: mark step as done

State changers are registered handlers that implement transitions between states.

### Persistence (`afl/runtime/persistence.py`)

The `PersistenceAPI` abstract class defines the storage interface. Two implementations:
- `MemoryStore` (`afl/runtime/memory_store.py`): in-memory, for testing and simple workflows
- `MongoStore` (`afl/runtime/mongo_store.py`): MongoDB-backed, for production

## Agent Execution Models

Facetwork supports four models for processing event facet tasks. All models work identically whether running as Docker containers or local processes — the only difference is how they are started.

### RunnerService + RegistryRunner (Recommended)

`afl/runtime/runner/service.py` with `--registry` flag

The recommended production model. Combines the `RunnerService` (distributed orchestration with atomic task claiming, thread pool, HTTP status endpoints, and heartbeat-based health checking) with the `RegistryRunner` pattern (auto-loads handler implementations from MongoDB).

**Flow:** Poll tasks -> Claim atomically -> Match handler by facet name -> Load module -> Dispatch -> Write results -> Resume workflow

**Docker:**
```bash
docker compose up -d --scale runner=3
```

**Local:**
```bash
fw runner start --instances 3 -- --log-format text
```

Both start identical `RunnerService` processes. Each registers in MongoDB's `servers` collection, sends heartbeats, and claims tasks via atomic `find_one_and_update`. Multiple instances across multiple machines cooperate automatically.

### AgentPoller

`afl/runtime/agent.py`

Standalone agent services with a `register()` callback pattern. Each agent polls MongoDB for tasks matching its registered facet names.

Available in Python, Scala, Go, TypeScript, and Java.

### ClaudeAgentRunner

`afl/runtime/claude_agent.py`

LLM-driven in-process execution via the Claude API. Processes event facets synchronously using Claude as the agent.

## MCP Server

`afl/mcp/server.py`

The Model Context Protocol server exposes Facetwork to LLM agents. It provides:

**Tools:**
| Tool | Description |
|------|-------------|
| `afl_compile` | Compile FFL source to JSON |
| `afl_validate` | Validate FFL source semantically |
| `afl_execute_workflow` | Execute a workflow from source |
| `afl_continue_step` | Continue an event-blocked step with results |
| `afl_resume_workflow` | Resume a paused workflow |
| `afl_manage_runner` | Cancel/pause/resume a runner |
| `afl_manage_handlers` | List/get/register/delete handler registrations |

**Resources:** Runners, steps, flows, servers, tasks, handlers (via `fw://` URIs)

## Dashboard

`facetwork/dashboard/`

A FastAPI web application providing monitoring and management. The UI is **v3**
and is the default (`/` → `/v3/workflows`); the older v2 UI and the original
non-prefixed page routes have been removed (only `/namespaces` and `/sources`
remain outside `/v3`). See [the dashboard reference](../reference/dashboard.md).
- **v3 shell** (sidebar + app switcher + acting-as) over: Runs, Library, Catalog,
  Filters, Servers, Handlers, Fleet, Tasks, Events, Output, PostGIS, Users/Teams
- A run's detail page has a **live execution graph** (polled JSON) and SSE step logs
- A **global Filters page** persists Flow/Workflow filters (cookie) and applies
  them to the Library and Runs lists; flows carry a real `created_at` (publish time)
- Domain "apps" on the same shell: Census Maps, Site Selection, Climate Trends
- API endpoints (`/api/*`), SSE log streams, and POST action endpoints are the
  backend the v3 pages call

## Data Flow

The complete data flow from FFL source to completed workflow:

```
1. Write FFL source     ->  facet/workflow definitions
2. Compile (afl CLI)    ->  JSON workflow definition
3. Execute (Evaluator)  ->  Creates steps, iterates state machine
4. Pause (EVENT_TRANSMIT) -> Creates task in MongoDB
5. Agent claims task    ->  Reads step parameters
6. Agent processes      ->  Runs business logic
7. Agent writes results ->  Updates step attributes
8. Agent signals resume ->  continue_step() advances past EventTransmit
9. Step processing      ->  process_single_step() cascades to parents
10. Continuation        ->  Continuation events notify remaining blocks
11. Complete            ->  Outputs collected, workflow done
```

In distributed mode, steps 8-10 happen across multiple servers. Each server claims tasks independently, processes steps, and generates continuation events. No per-workflow coordination is needed.

## Key Abstractions

| Abstraction | Module | Description |
|-------------|--------|-------------|
| `WorkflowDefinition` | `afl/runtime/entities.py` | Workflow metadata: name, starting step, parameters |
| `StepDefinition` | `afl/runtime/step.py` | Runtime step instance with state, attributes, facet reference |
| `TaskDefinition` | `afl/runtime/entities.py` | Work item for agent processing (pending/running/completed/failed) |
| `FlowDefinition` | `afl/runtime/entities.py` | Container for compiled sources, namespaces, workflows |
| `RunnerDefinition` | `afl/runtime/entities.py` | Active workflow execution instance |
| `ServerDefinition` | `afl/runtime/entities.py` | Registered agent server with heartbeat |
| `HandlerRegistration` | `afl/runtime/entities.py` | Registered handler module for a facet name |
| `EventDefinition` | `afl/runtime/persistence.py` | Event record for task lifecycle tracking |
| `FacetAttributes` | `afl/runtime/step.py` | Parameter and return value container for steps |
| `ExecutionResult` | `afl/runtime/evaluator.py` | Outcome of execute/resume: success, outputs, status |

## Composition with External Engines (Spark, OSRM, …)

Facetwork is not a replacement for heavyweight distributed engines like Spark, OSRM, Valhalla, GraphHopper, or pgRouting — it **composes** with them. The composition runs both ways.

### Pattern 1 — An engine *inside* a handler

A facet's handler is just a Python function (or any of the polyglot agent SDKs). It can wrap a Spark job, an OSRM/Valhalla query, a pgRouting SQL call, or anything else. Each engine is good at something different:

| Engine | Sweet spot |
|---|---|
| **Spark** (with Sedona for geospatial) | Selects / joins / filters / aggregations over massive data spread across HDFS — anything that benefits from shuffle-based parallelism on a Hadoop cluster |
| **OSRM / Valhalla / GraphHopper** | Pre-built routing graphs answering shortest-path / matrix / isochrone queries at high QPS |
| **pgRouting / PostGIS** | Spatial analytical queries (within-distance, spatial joins) backed by a relational store |
| **In-process `networkx` / `shapely`** | Tiny graph or geometry work that doesn't justify a separate engine — see `osm.Network` |

Facetwork's job around any of these is the **orchestration**: lock-free task claiming, retries, lifecycle, heartbeat/timeout, dashboard visibility, content-addressed caching of the result, and uniform schemas. The heavy engine does its specific work inside one facet call; the rest of the workflow doesn't have to know which engine was used.

The five-engine routing family (`osm.Routing.{OSRM,API,Valhalla,GraphHopper,PgRouting}`) is this pattern in production: each is a thin adapter facet that returns the same `osm.Routing.Types` schemas, so workflows swap engines by changing one namespace and the rest of the pipeline doesn't notice (see [composable-facet-library.md](composable-facet-library.md) and [lessons-learned.md](lessons-learned.md)). A `osm.Spark.SelectJoin`-style facet wrapping a Spark job over HDFS fits the same slot.

### Pattern 2 — Facetwork *simplifies* the engine's job

Going the other direction: what would otherwise be **one monolithic Spark job** with many internal stages can be decomposed into **several facets**, each doing a focused piece. The cross-step parallelism then moves *out* of Spark's DAG scheduler and *into* facetwork's lock-free task queue:

- Each facet's Spark job (if it still uses Spark) becomes **smaller and simpler** — fewer stages, fewer shuffles, easier to reason about and tune.
- **Unrelated steps run concurrently across the runner fleet** without Spark having to coordinate them — that fan-out is already what the task queue does.
- Each step is free to use a **different tool** — one facet can be Spark, the next pyosmium, the next in-process `networkx`. The contract between them is a typed result schema and a durable artifact (file URI), not a Spark DataFrame.
- Steps remain **independently retriable / cacheable / observable** — facetwork's standard mechanics — without the engine's session having to survive.

The net effect is a deliberate division of labour: the **distributed engine** handles what it's built for (heavy in-job parallelism over big data) and **facetwork** handles what *it's* built for (coordinating many independent steps across a fleet, with shared durable artifacts via the storage backend). Neither replaces the other; together they do less work, more clearly, than either would alone.

## Configuration

Facetwork is configured via `afl.config.json` or environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `FW_MONGODB_URL` | `mongodb://localhost:27017` | MongoDB connection string |
| `FW_MONGODB_DATABASE` | `afl` | Database name |
| `FW_MONGODB_USERNAME` | | MongoDB username |
| `FW_MONGODB_PASSWORD` | | MongoDB password |
| `FW_CONFIG` | | Path to config file |

## Directory Structure

```
afl/
  grammar/afl.lark      # Lark LALR grammar
  ast.py                 # AST dataclass nodes
  parser.py              # Lark parser wrapper
  transformer.py         # Parse tree -> AST
  validator.py           # Semantic validation
  emitter.py             # AST -> JSON
  cli.py                 # CLI entry point
  config.py              # Configuration loading
  runtime/
    evaluator.py         # Workflow execution engine
    step.py              # Step definition and attributes
    persistence.py       # Abstract persistence API
    memory_store.py      # In-memory store
    mongo_store.py       # MongoDB store
    registry_runner.py   # RegistryRunner (recommended agent model)
    agent.py             # AgentPoller
    claude_agent.py      # ClaudeAgentRunner
    changers/            # State machine transition handlers
    runner/
      service.py         # RunnerService (distributed)
      __main__.py        # CLI entry point
  mcp/
    server.py            # MCP server implementation
    serializers.py       # Entity serialization
    __main__.py          # CLI entry point
  dashboard/
    app.py               # FastAPI application factory
    dependencies.py      # MongoStore dependency injection
    helpers.py           # Shared utilities (grouping, categorization)
    viewdata.py          # View-data builders (filter/count/enrich) shared by v3 routes
    filters.py           # Jinja2 template filters
    viewfilters.py       # Global Flow/Workflow filter state (cookie + matchers)
    routes/v3/           # The v3 UI (default): workflows, library, filters, admin, …
    routes/              # Backend kept behind v3: /api/*, POST actions, SSE, namespaces, sources
    templates/v3/        # Jinja2 templates for the v3 shell + pages
    static/v3.css        # v3 design system
    __main__.py          # CLI entry point
agents/
  python/               # Python agent library
  scala/fw-agent/      # Scala 3 agent library
  go/fw-agent/         # Go agent library
  typescript/fw-agent/ # TypeScript/Node.js agent library
  java/fw-agent/       # Java 17 agent library
  protocol/             # Cross-language protocol specification
  templates/            # Agent bootstrapping templates
```

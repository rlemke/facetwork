# Facetwork Architecture Overview

Facetwork is a platform for distributed workflow execution. It combines a compiler for the **Facetwork Flow Language (AFL)**, a runtime engine that evaluates workflows iteratively, and multi-language agent libraries that process event-driven tasks. MongoDB serves as the persistence and coordination layer.

## System Architecture

```
                         AFL Source (.afl files)
                                |
                    +-----------v-----------+
                    |      AFL Compiler      |
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

The compiler transforms AFL source code into JSON workflow definitions through four stages.

| Stage | Module | Description |
|-------|--------|-------------|
| **Parser** | `afl/parser.py` | Lark LALR parser reads `.afl` source, produces a parse tree. Errors include line/column. |
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
scripts/start-runner --instances 3 -- --log-format text
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
| `afl_compile` | Compile AFL source to JSON |
| `afl_validate` | Validate AFL source semantically |
| `afl_execute_workflow` | Execute a workflow from source |
| `afl_continue_step` | Continue an event-blocked step with results |
| `afl_resume_workflow` | Resume a paused workflow |
| `afl_manage_runner` | Cancel/pause/resume a runner |
| `afl_manage_handlers` | List/get/register/delete handler registrations |

**Resources:** Runners, steps, flows, servers, tasks, handlers (via `afl://` URIs)

## Dashboard

`afl/dashboard/`

A FastAPI web application providing monitoring and management:
- **V2 views** with 2-tab navigation (Workflows / Servers) and namespace/group accordion grouping
- Runner, flow, task, server, event, handler, source, lock, and namespace views
- Real-time status with HTMX 5s auto-refresh on v2 pages
- Workflow compilation and validation
- API endpoints (`/api/*`) for programmatic access

## Data Flow

The complete data flow from AFL source to completed workflow:

```
1. Write AFL source     ->  facet/workflow definitions
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

## Configuration

Facetwork is configured via `afl.config.json` or environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `AFL_MONGODB_URL` | `mongodb://localhost:27017` | MongoDB connection string |
| `AFL_MONGODB_DATABASE` | `afl` | Database name |
| `AFL_MONGODB_USERNAME` | | MongoDB username |
| `AFL_MONGODB_PASSWORD` | | MongoDB password |
| `AFL_CONFIG` | | Path to config file |

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
    filters.py           # Template filters
    routes/              # Route handlers (dashboard_v2, home, runners, flows, etc.)
    templates/           # Jinja2 templates (v2/workflows/, v2/servers/, legacy)
    static/              # CSS/JS assets
    __main__.py          # CLI entry point
agents/
  python/               # Python agent library
  scala/afl-agent/      # Scala 3 agent library
  go/afl-agent/         # Go agent library
  typescript/afl-agent/ # TypeScript/Node.js agent library
  java/afl-agent/       # Java 17 agent library
  protocol/             # Cross-language protocol specification
  templates/            # Agent bootstrapping templates
```

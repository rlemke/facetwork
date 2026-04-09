
# AFL Agent SDK Specification

This document defines how external services (**AFL Agents**) interact with
the Facetwork runtime to process event facet tasks. It covers the three
agent execution models, the task lifecycle, and the public API contract.

This specification is the authoritative reference for **service provider
programmers** who write handler implementations for event facets. Domain
programmers who write AFL do not need this document — they define workflows
in `.afl` files and the platform handles execution. Claude can also generate
handler implementations from natural-language descriptions of the desired
behavior.

Cross-references to `runtime.md` and `event-system.md`
are provided where relevant.

---

## 1. Introduction

An **AFL Agent** is a service that:

1. accepts event tasks from the Facetwork task queue,
2. performs the required action (computation, API call, LLM inference, etc.),
3. updates the originating step with a result or error, and
4. signals the runtime to continue evaluation.

Facetwork provides four execution models for building agents:

| Model | Use case | Transport |
|-------|----------|-----------|
| **RegistryRunner** | Production (recommended) — auto-loads handlers | Task queue polling + dynamic module loading |
| **AgentPoller** | Standalone agent services | Task queue polling |
| **RunnerService** | Distributed orchestration with locking | Task queue + step polling + HTTP |
| **ClaudeAgentRunner** | LLM-driven execution | In-process synchronous |

The **recommended approach** for production is `RegistryRunner`. Developers
register handler implementations (Python modules) in the database. The
runner automatically discovers, loads, caches, and dispatches them — no
custom agent service code required.

All three models share the same underlying primitives: `claim_task()`,
`continue_step()`, `fail_step()`, and `resume()`.

---

## 2. Agent Lifecycle

### 2.1 Task Creation

When the evaluator processes a step that invokes an **event facet**, the
`EventTransmitHandler` creates a `TaskDefinition` in the task queue.
This occurs at the `EVENT_TRANSMIT` state (see `runtime.md` §8.1
and `event-system.md`).

The task is committed atomically alongside step and event changes via
`IterationChanges.created_tasks`.

> **Note:** Tasks and events are distinct concepts. An event models the
> *domain lifecycle* of the external work (what, why, outcome). A task
> models the *distribution mechanism* (claiming, routing, locking). Both
> are created together at `EVENT_TRANSMIT` but consumed by different
> subsystems. See `event-system.md` §9 for the full explanation.

### 2.2 Poll → Claim → Dispatch → Continue → Resume

The agent lifecycle follows a five-phase cycle:

```
                    ┌──────────────────────────────────────────┐
                    │            Facetwork Runtime              │
                    │                                          │
                    │  Evaluator executes workflow              │
                    │       │                                   │
                    │       ▼                                   │
                    │  Step reaches EVENT_TRANSMIT              │
                    │       │                                   │
                    │       ▼                                   │
                    │  EventTransmitHandler creates task        │
                    │  (TaskState.PENDING)                      │
                    │       │                                   │
                    └───────┼──────────────────────────────────┘
                            │
                            ▼
┌───────────────────────────────────────────────────────────────┐
│                       AFL Agent                               │
│                                                               │
│  1. POLL    — query task queue for matching tasks             │
│       │                                                       │
│       ▼                                                       │
│  2. CLAIM   — claim_task() atomically: PENDING → RUNNING     │
│       │                                                       │
│       ▼                                                       │
│  3. DISPATCH — invoke registered callback with task payload   │
│       │                                                       │
│       ├─── success ──┐                                        │
│       │              ▼                                        │
│       │     continue_step(step_id, result)                    │
│       │     mark task COMPLETED                               │
│       │                                                       │
│       └─── failure ──┐                                        │
│                      ▼                                        │
│              fail_step(step_id, error_message)                │
│              mark task FAILED                                 │
│                                                               │
└───────────────────────────┬───────────────────────────────────┘
                            │
                            ▼
┌───────────────────────────────────────────────────────────────┐
│                       Facetwork Runtime                       │
│                                                               │
│  4. CONTINUE — step unblocked, attributes merged              │
│       │                                                       │
│       ▼                                                       │
│  5. RESUME  — evaluator resumes iteration loop                │
│              (runs to next fixed point or completion)          │
│                                                               │
└───────────────────────────────────────────────────────────────┘
```

### 2.3 Execution Returns PAUSED

When the evaluator reaches a fixed point with one or more steps blocked
at `EVENT_TRANSMIT`, `execute()` returns an `ExecutionResult` with
`status=PAUSED`. The workflow remains in the persistence store and MAY
be resumed after the agent processes the event.

---

## 3. Task Queue Contract

### 3.1 TaskDefinition

A task is represented by the `TaskDefinition` dataclass
(`afl/runtime/entities.py`):

```python
@dataclass
class TaskDefinition:
    uuid: str
    name: str                          # Qualified facet name (e.g. "ns.CountDocs")
    runner_id: str                     # ID of the runner that created this task
    workflow_id: str
    flow_id: str
    step_id: str
    state: str = TaskState.PENDING
    created: int = 0                   # Creation timestamp (ms since epoch)
    updated: int = 0                   # Last update timestamp (ms since epoch)
    error: Optional[dict] = None
    task_list_name: str = "default"
    data_type: str = ""
    data: Optional[dict] = None        # Payload (step params, facet info)
```

### 3.2 TaskState Transitions

```
                ┌─────────┐
                │ PENDING │
                └────┬────┘
                     │  claim_task()
                     ▼
                ┌─────────┐
                │ RUNNING │
                └────┬────┘
                     │
            ┌────────┼────────┐
            ▼                 ▼
      ┌───────────┐    ┌──────────┐
      │ COMPLETED │    │  FAILED  │
      └───────────┘    └──────────┘
```

Valid states (from `TaskState`):

| Constant | Value | Description |
|----------|-------|-------------|
| `PENDING` | `"pending"` | Task created, awaiting claim |
| `RUNNING` | `"running"` | Claimed by an agent, processing |
| `COMPLETED` | `"completed"` | Successfully processed |
| `FAILED` | `"failed"` | Processing failed |
| `IGNORED` | `"ignored"` | Skipped (no matching handler) |
| `CANCELED` | `"canceled"` | Canceled by operator |

### 3.3 Atomic Claim Semantics

The `claim_task()` method on `PersistenceAPI` MUST provide **atomic
claim semantics**:

```python
def claim_task(
    self,
    task_names: list[str],
    task_list: str = "default",
    server_id: str = "",
) -> Optional[TaskDefinition]
```

- The implementation MUST atomically transition exactly one matching task
  from `PENDING` to `RUNNING` and return it.
- If no matching task exists, it MUST return `None`.
- Concurrent callers MUST NOT receive the same task.
- The `server_id` parameter stamps the task document with the claiming
  server's UUID, enabling the orphaned task reaper to detect tasks
  orphaned by crashed servers and reset them to `PENDING`.
- The MemoryStore implementation uses `threading.Lock` for atomicity.
- The MongoStore implementation uses `find_one_and_update()` with a
  compound index for atomicity.
- A partial unique index on `(step_id, state=running)` ensures at most
  one agent processes a given event step at any time.

### 3.4 Task Naming

Tasks are named using **qualified facet names** of the form
`"namespace.FacetName"` (e.g. `"billing.ProcessPayment"`).

When matching tasks to handlers:

1. The agent SHOULD first attempt an exact match on the qualified name.
2. If no exact match is found, the agent SHOULD attempt a **short-name
   fallback** — matching only the facet name portion after the last dot.

This allows handlers to be registered with either qualified names
(`"billing.ProcessPayment"`) or short names (`"ProcessPayment"`).

---

## 4. AgentPoller API

The `AgentPoller` class (`afl/runtime/agent_poller.py`) is a standalone
polling library for building AFL Agent services without the full
`RunnerService`.

### 4.1 AgentPollerConfig

```python
@dataclass
class AgentPollerConfig:
    service_name: str = "afl-agent"
    server_group: str = "default"
    server_name: str = ""              # Auto-populated with socket.gethostname()
    task_list: str = "default"
    poll_interval_ms: int = 2000
    max_concurrent: int = 5
    heartbeat_interval_ms: int = 10000
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `service_name` | `str` | `"afl-agent"` | Identifies the agent type in server registry |
| `server_group` | `str` | `"default"` | Logical grouping of servers |
| `server_name` | `str` | hostname | Human-readable server name |
| `task_list` | `str` | `"default"` | Task list to poll |
| `poll_interval_ms` | `int` | `2000` | Milliseconds between poll cycles |
| `max_concurrent` | `int` | `5` | Maximum concurrent task processing |
| `heartbeat_interval_ms` | `int` | `10000` | Milliseconds between heartbeat pings |

### 4.2 Constructor

```python
def __init__(
    self,
    persistence: PersistenceAPI,
    evaluator: Evaluator,
    config: Optional[AgentPollerConfig] = None,
) -> None
```

The `AgentPoller` requires a `PersistenceAPI` for task queue access and
an `Evaluator` for `continue_step()` / `fail_step()` / `resume()`.

### 4.3 register()

```python
def register(self, facet_name: str, callback: Callable[[dict], dict]) -> None
```

Registers a handler callback for a given facet name. The callback
signature MUST be:

```python
Callable[[dict], dict]
```

- **Input**: a `dict` containing the task payload (step parameters and
  facet metadata from `task.data`).
- **Output**: a `dict` containing the result to merge into the step's
  return attributes via `continue_step()`.

A handler MAY be registered with either a qualified name
(`"ns.FacetName"`) or a short name (`"FacetName"`). Short-name fallback
applies during dispatch (see §3.4).

### 4.4 registered_names()

```python
def registered_names(self) -> list[str]
```

Returns the list of all registered facet names. Used to build the
`task_names` list for `claim_task()` and the `handlers` list in the
server registration record.

### 4.5 start() / stop()

```python
def start(self) -> None
def stop(self) -> None
```

`start()` enters a **blocking poll loop** that:

1. Registers a `ServerDefinition` with the persistence store.
2. Starts a background heartbeat thread.
3. Repeatedly calls `claim_task()` with the registered names.
4. Dispatches claimed tasks to the matching callback.
5. On success: calls `continue_step()`, `resume()`, marks task `COMPLETED`.
6. On failure: calls `fail_step()`, marks task `FAILED`.

`stop()` signals the poll loop to exit gracefully. The server record
is updated to `ServerState.SHUTDOWN`.

### 4.6 poll_once()

```python
def poll_once(self) -> int
```

Executes a **single synchronous poll cycle** without starting the full
loop. Returns the number of tasks dispatched. This method is intended
for testing and MUST NOT start background threads or the heartbeat loop.

### 4.7 cache_workflow_ast()

```python
def cache_workflow_ast(self, workflow_id: str, ast: dict) -> None
```

Pre-caches a workflow AST so that `resume()` can retrieve it without
a database lookup. This is required when the agent needs to resume a
workflow after processing an event — the AST is needed for the evaluator
to continue from the paused state.

### 4.8 Properties

| Property | Type | Description |
|----------|------|-------------|
| `server_id` | `str` | Unique identifier for this agent instance |
| `is_running` | `bool` | Whether the poll loop is currently active |

---

## 5. Server Registration

### 5.1 ServerDefinition

Agents register themselves with the runtime via a `ServerDefinition`
(`afl/runtime/entities.py`):

```python
@dataclass
class ServerDefinition:
    uuid: str
    server_group: str
    service_name: str
    server_name: str
    server_ips: list[str] = field(default_factory=list)
    start_time: int = 0               # Server start timestamp (ms)
    ping_time: int = 0                # Last heartbeat timestamp (ms)
    topics: list[str] = field(default_factory=list)
    handlers: list[str] = field(default_factory=list)
    handled: list[HandledCount] = field(default_factory=list)
    state: str = ServerState.STARTUP
    manager: str = ""
    error: Optional[dict] = None
```

The `handlers` field MUST contain the list of facet names this agent
can process. The `topics` field MAY contain a subset for filtering.

### 5.2 ServerState Transitions

```
  STARTUP  ──→  RUNNING  ──→  SHUTDOWN
                   │
                   └──→  ERROR
```

| Constant | Value | Description |
|----------|-------|-------------|
| `STARTUP` | `"startup"` | Server registered, initializing |
| `RUNNING` | `"running"` | Actively polling and processing |
| `SHUTDOWN` | `"shutdown"` | Graceful shutdown in progress or complete |
| `ERROR` | `"error"` | Unrecoverable error |

### 5.3 Lifecycle

1. **Registration**: at `start()`, the agent creates a `ServerDefinition`
   with `state=STARTUP`, saves it via persistence, then transitions to
   `RUNNING`.
2. **Heartbeat**: a background thread updates `ping_time` at the
   configured `heartbeat_interval_ms`. This allows the dashboard and
   other services to detect stale servers.
3. **Deregistration**: at `stop()`, the agent updates the server record
   to `state=SHUTDOWN`.

---

## 6. Error Handling

### 6.1 Callback Exceptions

If a registered callback raises an exception during dispatch:

1. The agent MUST call `fail_step(step_id, error_message)` on the
   evaluator.
2. The agent MUST mark the task as `TaskState.FAILED`.
3. The agent MUST NOT re-raise the exception to the poll loop.

There are **no implicit retries**. A failed task remains in `FAILED`
state until explicitly retried by an operator (e.g. via the dashboard
retry action).

### 6.2 Evaluator.fail_step()

```python
def fail_step(self, step_id: StepId, error_message: str) -> None
```

This method:

1. Retrieves the step from persistence.
2. Verifies the step is at `EVENT_TRANSMIT` state.
3. Calls `mark_error()` on the step with the error message.
4. Saves the step directly to persistence.

The step transitions to `STATEMENT_ERROR`, which the evaluator treats
as a terminal error for that step.

### 6.3 Evaluator.continue_step()

```python
def continue_step(self, step_id: StepId, result: Optional[dict] = None) -> None
```

This method:

1. Retrieves the step from persistence.
2. Verifies the step is at `EVENT_TRANSMIT` state.
3. Merges `result` into the step's return attributes.
4. Calls `request_state_change(True)` on the step's transition.
5. Saves the step directly to persistence.

### 6.4 Evaluator.resume()

```python
def resume(
    self,
    workflow_id_val: WorkflowId,
    workflow_ast: dict,
    program_ast: Optional[dict] = None,
    inputs: Optional[dict] = None,
) -> ExecutionResult
```

Resumes a paused workflow from its current state. The evaluator
re-enters the iteration loop and runs until the next fixed point or
completion.

### 6.4 Long-Running Handlers

Handlers that perform bulk database imports, large file processing, or
multi-hour computations require special attention to heartbeats and timeouts.
See the **[Long-Running Handlers Guide](../guides/long-running-handlers.md)**
for patterns including staging tables, batched merges, and timeout configuration.

---

## 7. Step-Log Emission

Agents emit **step log entries** during task processing to provide
observability in the dashboard. Step logs capture key lifecycle milestones
and are stored in the `step_logs` MongoDB collection.

### 7.1 StepLogEntry

```python
@dataclass
class StepLogEntry:
    uuid: str
    step_id: str
    workflow_id: str
    runner_id: str = ""
    facet_name: str = ""
    source: str = StepLogSource.FRAMEWORK
    level: str = StepLogLevel.INFO
    message: str = ""
    details: dict = field(default_factory=dict)
    time: int = 0                      # Timestamp (ms since epoch)
```

### 7.2 Level and Source Constants

**Levels** (`StepLogLevel`):

| Constant | Value | Usage |
|----------|-------|-------|
| `INFO` | `"info"` | Normal lifecycle events (task claimed, dispatching) |
| `WARNING` | `"warning"` | Non-fatal issues |
| `ERROR` | `"error"` | Handler failures, missing handlers |
| `SUCCESS` | `"success"` | Handler completed successfully |

**Sources** (`StepLogSource`):

| Constant | Value | Description |
|----------|-------|-------------|
| `FRAMEWORK` | `"framework"` | Emitted by the agent framework automatically |
| `HANDLER` | `"handler"` | Emitted by handler code via the `_step_log` callback |

### 7.3 Framework Emission Points

All agent execution models (Python `AgentPoller`, `RegistryRunner`, and
the non-Python SDKs in Scala, Go, TypeScript, Java) MUST emit step logs
at the following five points during `processTask` / `_process_event`:

| # | When | Level | Message pattern |
|---|------|-------|-----------------|
| 1 | Task claimed | `info` | `"Task claimed: {facet_name}"` |
| 2 | No handler found | `error` | `"Handler error: No handler registered for: {facet_name}"` |
| 3 | Dispatching handler | `info` | `"Dispatching handler: {facet_name}"` |
| 4 | Handler completed | `success` | `"Handler completed: {facet_name} ({duration_ms}ms)"` |
| 5 | Handler error | `error` | `"Handler error: {error_message}"` |

All five use `source=framework`. The `runner_id` field is set to the
agent's `server_id`.

### 7.4 Best-Effort Semantics

Step-log insertion MUST be **best-effort**: errors from `insertOne` into
the `step_logs` collection are caught internally and logged at debug
level. A step-log write failure MUST NOT cause the task to fail or
prevent normal processing.

### 7.5 Handler-Level Logging

All SDKs (Python, Scala, Go, TypeScript, Java) inject a `_step_log`
callback into the handler params before invocation. Handlers can call
this to emit custom log entries visible in the dashboard.

**Python:**

```python
def handle(payload: dict) -> dict:
    step_log = payload.get("_step_log")
    if step_log:
        step_log("Starting data download", level="info")
    # ... do work ...
    if step_log:
        step_log("Downloaded 1,234 records", level="success")
    return {"count": 1234}
```

The Python callback signature is:

```python
def _step_log(message: str, level: str = "info", details: dict | None = None) -> None
```

**Non-Python SDKs:**

| SDK | Callback type | Signature |
|-----|--------------|-----------|
| Scala | `(String, String) => Unit` | `(message, level)` |
| Go | `func(string, string)` | `(message, level)` |
| TypeScript | `async (string, string?) => void` | `(message, level="info")` |
| Java | `BiConsumer<String, String>` | `(message, level)` |

Handler-emitted logs use `source=handler`.

### 7.6 MongoDB Document Schema

```json
{
    "uuid": "<generated UUID>",
    "step_id": "<step UUID>",
    "workflow_id": "<workflow UUID>",
    "runner_id": "<server UUID>",
    "facet_name": "<qualified facet name>",
    "source": "framework|handler",
    "level": "info|warning|error|success",
    "message": "<descriptive message>",
    "details": {},
    "time": 1709251200000
}
```

Indexes on the `step_logs` collection:

| Index | Fields | Purpose |
|-------|--------|---------|
| `step_log_uuid_index` | `uuid` (unique) | Primary key |
| `step_log_step_id_index` | `step_id` | Step detail page queries |
| `step_log_workflow_id_index` | `workflow_id` | Workflow-level log aggregation |
| `step_log_facet_name_index` | `facet_name` | Handler activity page queries |

### 7.7 Non-Python SDK Implementation

The protocol constants file (`agents/protocol/constants.json`) defines
the `step_logs` collection name, level values, source values, and the
`insert_step_log` operation schema. Each non-Python SDK implements:

| File | Addition |
|------|----------|
| Protocol constants | `step_logs` collection + level/source constants |
| MongoOps | `insertStepLog(...)` — best-effort `insertOne` |
| AgentPoller | `emitStepLog(...)` helper + 5 emission points in `processTask` |

All non-Python SDKs also inject the handler-level `_step_log` callback
(§7.5) into params before handler invocation, using `source=handler`.

### 7.8 Type Hint Inference (Non-Python SDKs)

When non-Python SDKs build `StepAttributes` from handler return values,
they infer a `type_hint` string for each value using `inferTypeHint()`.
In Scala this lives on the `StepAttributes` companion object (as
`private[agent]`); in Go, TypeScript, and Java it is an internal helper
within the poller.

| Value type | Inferred hint |
|------------|---------------|
| Boolean | `"Boolean"` |
| Int / Short | `"Long"` |
| Long | `"Long"` |
| Float | `"Double"` |
| Double | `"Double"` |
| String | `"String"` |
| Seq / Array / List | `"List"` |
| Map / Object | `"Map"` |
| null / undefined | `"Any"` |
| other | `"Any"` |

---

## 8. RunnerService

The `RunnerService` (`afl/runtime/runner/service.py`) is a superset of
the `AgentPoller` that adds distributed coordination capabilities.

### 8.1 RunnerConfig

```python
@dataclass
class RunnerConfig:
    server_group: str = "default"
    service_name: str = "afl-runner"
    server_name: str = ""              # Auto-populated with socket.gethostname()
    topics: list[str] = field(default_factory=list)
    task_list: str = "default"
    poll_interval_ms: int = 2000
    heartbeat_interval_ms: int = 10000
    max_concurrent: int = 5
    shutdown_timeout_ms: int = 30000
    http_port: int = 8080
    http_max_port_attempts: int = 20
```

### 8.2 Capabilities Beyond AgentPoller

| Capability | AgentPoller | RunnerService |
|------------|-------------|---------------|
| Task queue polling | Yes | Yes |
| Handler registration | `register()` | `ToolRegistry` |
| HTTP status server | No | Yes (`/health`, `/status`) |
| Non-event tasks (`afl:execute`) | No | Yes |
| ThreadPoolExecutor concurrency | No | Yes |
| Signal handling (SIGTERM/SIGINT) | No | Yes |
| Graceful shutdown timeout | No | Yes (`shutdown_timeout_ms`) |

### 8.3 ToolRegistry

The `RunnerService` uses a `ToolRegistry` for handler dispatch instead
of direct callback registration. The registry supports:

- Registration by qualified facet name.
- Short-name fallback matching.
- A default handler fallback for unmatched tasks.

### 8.4 HTTP Status Server

The `RunnerService` starts an embedded HTTP server with two endpoints:

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Returns `200 OK` if the service is running |
| `/status` | GET | Returns JSON with server ID, state, uptime, and statistics |

The server auto-probes ports starting from `http_port`, incrementing up
to `http_max_port_attempts` times if the port is in use.

---

## 9. RegistryRunner (Recommended)

The `RegistryRunner` (`afl/runtime/registry_runner.py`) is a universal,
handler-agnostic runner that eliminates the need for per-facet
microservices. Instead of writing custom agent code, developers register
handler implementations in the database and the RegistryRunner
dynamically loads and dispatches them.

### 9.1 Why RegistryRunner?

With the `AgentPoller` or `RunnerService`, building an agent requires:

1. Writing a Python service that imports handler modules.
2. Manually calling `register()` for each facet name.
3. Deploying and maintaining the service.

With `RegistryRunner`, the workflow is:

1. Write a handler module (a Python file with a callable).
2. Register it in the database (via API, MCP tool, or dashboard).
3. Start the RegistryRunner service — it auto-loads everything.

Handler registrations are **persisted** and survive restarts. The
RegistryRunner re-reads registrations periodically (default: every 30s)
and picks up new handlers without restarting.

### 9.2 RegistryRunnerConfig

```python
@dataclass
class RegistryRunnerConfig:
    service_name: str = "afl-registry-runner"
    server_group: str = "default"
    server_name: str = ""              # Auto-populated with hostname
    task_list: str = "default"
    poll_interval_ms: int = 2000
    max_concurrent: int = 5
    heartbeat_interval_ms: int = 10000
    registry_refresh_interval_ms: int = 30000
    topics: list[str] = field(default_factory=list)
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `service_name` | `str` | `"afl-registry-runner"` | Identifies the runner type in server registry |
| `server_group` | `str` | `"default"` | Logical grouping of servers |
| `server_name` | `str` | hostname | Human-readable server name |
| `task_list` | `str` | `"default"` | Task list to poll |
| `poll_interval_ms` | `int` | `2000` | Milliseconds between poll cycles |
| `max_concurrent` | `int` | `5` | Maximum concurrent task processing |
| `heartbeat_interval_ms` | `int` | `10000` | Milliseconds between heartbeat pings |
| `registry_refresh_interval_ms` | `int` | `30000` | Milliseconds between registry re-reads |
| `topics` | `list[str]` | `[]` | Glob patterns to filter which facets to handle (empty = all) |

### 9.3 Constructor

```python
def __init__(
    self,
    persistence: PersistenceAPI,
    evaluator: Evaluator,
    config: Optional[RegistryRunnerConfig] = None,
) -> None
```

### 9.4 Handler Registration

```python
def register_handler(
    self,
    facet_name: str,
    module_uri: str,
    entrypoint: str = "handle",
    version: str = "1.0.0",
    checksum: str = "",
    timeout_ms: int = 30000,
    requirements: list[str] | None = None,
    metadata: dict | None = None,
) -> None
```

Creates a `HandlerRegistration` and saves it to the persistence store.
The registration is picked up on the next registry refresh.

**Parameters:**

| Parameter | Description |
|-----------|-------------|
| `facet_name` | Qualified event facet name (e.g. `"ns.CountDocuments"`) |
| `module_uri` | Python module path (`"my.handlers"`) or file URI (`"file:///path/to/handler.py"`) |
| `entrypoint` | Function name within the module (default: `"handle"`) |
| `version` | Handler version string for tracking |
| `checksum` | Cache-invalidation checksum; changing this forces module reload |
| `timeout_ms` | Handler timeout in milliseconds |
| `requirements` | Optional pip requirements (informational) |
| `metadata` | Optional metadata dict; injected into payload as `_handler_metadata` |

**Registration methods (in order of preference):**

1. **MCP tool** — `afl_manage_handlers` with `action: "register"` (for
   LLM agents and automation)
2. **Dashboard** — via the Handler Registrations page (`/handlers`)
3. **Python API** — `runner.register_handler(...)` (for scripts and setup)
4. **Direct persistence** — `store.save_handler_registration(...)` (for
   migration/seeding)

### 9.5 HandlerRegistration Entity

```python
@dataclass
class HandlerRegistration:
    facet_name: str          # Primary key: "ns.FacetName"
    module_uri: str          # "my.handlers" or "file:///path/to.py"
    entrypoint: str = "handle"
    version: str = "1.0.0"
    checksum: str = ""
    timeout_ms: int = 30000
    requirements: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    created: int = 0         # Timestamp (ms)
    updated: int = 0         # Timestamp (ms)
```

Stored in the `handler_registrations` collection (MongoDB) or the
`_handler_registrations` dict (MemoryStore).

### 9.6 Dynamic Module Loading

The RegistryRunner supports two module URI formats:

| Format | Example | Mechanism |
|--------|---------|-----------|
| Python module path | `"my.handlers.cache"` | `importlib.import_module()` |
| File URI | `"file:///opt/handlers/cache.py"` | `importlib.util.spec_from_file_location()` |

Loaded modules are **cached** by `(module_uri, checksum)`. Changing the
`checksum` field in the registration forces a module reload on the next
invocation.

### 9.7 Dispatch Flow

When a task is claimed and processed:

1. **Lookup**: Find `HandlerRegistration` by `task.name` (exact match,
   then short-name fallback).
2. **Load**: Dynamically import the handler module (cached).
3. **Inject**: Add `_facet_name` and `_handler_metadata` to payload.
4. **Invoke**: Call the handler (auto-detects sync vs async via
   `inspect.iscoroutinefunction()`).
5. **Continue**: Call `evaluator.continue_step(step_id, result)`.
6. **Resume**: Call `evaluator.resume(workflow_id, workflow_ast)`.
7. **Complete**: Mark task `COMPLETED`.

On any exception: call `evaluator.fail_step()`, mark task `FAILED`.

### 9.8 Topic-Based Filtering

When `topics` is set in the config, the RegistryRunner only handles
facets matching at least one glob pattern:

```python
config = RegistryRunnerConfig(topics=["osm.*", "validation.*"])
# Handles: osm.cache.Africa, validation.CheckBounds
# Ignores: billing.ProcessPayment
```

Pattern matching uses `fnmatch.fnmatch()` and supports `*`, `?`, and
`[seq]` syntax.

### 9.9 Streaming Support

The RegistryRunner provides `update_step()` for handlers that produce
partial/streaming results:

```python
def update_step(self, step_id: str, partial_result: dict) -> None
```

This merges `partial_result` into the step's return attributes
immediately, without completing the step. The handler can call this
multiple times before returning the final result.

### 9.10 Complete Example: Event Facet with Auto-Loading

**1. Define the event facet in AFL:**

```afl
namespace billing

event facet ProcessPayment(amount: Double, currency: String) => (transaction_id: String, status: String)

workflow Checkout(total: Double) => (receipt: String) andThen {
    payment = ProcessPayment(amount = $.total, currency = "USD")
    yield Checkout(receipt = payment.transaction_id)
}
```

**2. Write the handler module** (`handlers/billing.py`):

```python
def process_payment(payload: dict) -> dict:
    """Handle the ProcessPayment event facet."""
    amount = payload.get("amount", 0)
    currency = payload.get("currency", "USD")
    # ... call payment gateway ...
    return {
        "transaction_id": "txn-12345",
        "status": "approved",
    }
```

**3. Register the handler** (one-time setup):

```python
# Via Python API:
runner.register_handler(
    facet_name="billing.ProcessPayment",
    module_uri="handlers.billing",
    entrypoint="process_payment",
)

# Or via MCP tool:
# afl_manage_handlers(action="register", facet_name="billing.ProcessPayment",
#                     module_uri="handlers.billing", entrypoint="process_payment")

# Or via direct persistence:
# store.save_handler_registration(HandlerRegistration(...))
```

**4. Start the RegistryRunner** — no custom agent code needed:

```python
from afl.runtime import MongoStore, Evaluator
from afl.runtime.registry_runner import RegistryRunner, RegistryRunnerConfig

store = MongoStore()
evaluator = Evaluator(persistence=store)
runner = RegistryRunner(
    persistence=store,
    evaluator=evaluator,
    config=RegistryRunnerConfig(service_name="billing-runner"),
)
runner.start()  # blocks; auto-loads handlers from persistence
```

The runner automatically discovers `billing.ProcessPayment` from the
handler registrations table, loads `handlers.billing.process_payment`,
and dispatches incoming tasks to it.

### 9.11 Handler Module Patterns

#### Simple handler (one facet per module):

```python
# handlers/count.py
def handle(payload: dict) -> dict:
    collection = payload.get("collection", "")
    return {"count": len(collection)}
```

Register with `entrypoint="handle"` (the default).

#### Multi-facet dispatch module:

```python
# handlers/cache.py
_DISPATCH = {
    "cache.Africa": lambda p: {"data": download("africa")},
    "cache.Europe": lambda p: {"data": download("europe")},
}

def handle(payload: dict) -> dict:
    facet = payload["_facet_name"]
    handler = _DISPATCH.get(facet)
    if handler is None:
        raise ValueError(f"Unknown facet: {facet}")
    return handler(payload)
```

Register all facets with `module_uri="handlers.cache"`,
`entrypoint="handle"`. The `_facet_name` injected into the payload
allows the handler to route internally.

#### Factory-based registration:

```python
# handlers/regions.py
REGIONS = {"Africa": "africa", "Europe": "europe", "Asia": "asia"}

def register_handlers(runner):
    for name, path in REGIONS.items():
        runner.register_handler(
            facet_name=f"geo.cache.{name}",
            module_uri="handlers.regions",
            entrypoint="handle",
        )

def handle(payload: dict) -> dict:
    facet = payload["_facet_name"]
    region = facet.rsplit(".", 1)[-1]
    path = REGIONS[region]
    return {"data": download(path)}
```

### 9.12 Properties

| Property | Type | Description |
|----------|------|-------------|
| `server_id` | `str` | Unique identifier for this runner instance |
| `is_running` | `bool` | Whether the poll loop is currently active |

### 9.13 Non-Python RegistryRunner (DB-Driven Topic Filtering)

The non-Python SDKs (Scala, Go, TypeScript, Java) each provide a
`RegistryRunner` class that wraps `AgentPoller` and adds **DB-driven
topic filtering**. Unlike the Python RegistryRunner (which dynamically
loads modules at runtime), non-Python SDKs register handlers at compile
time via `register()`. The `RegistryRunner` restricts polling to only
those handler names that also appear in the `handler_registrations`
MongoDB collection.

**Architecture:** Composition over inheritance. Each `RegistryRunner`
wraps an `AgentPoller` instance and provides:

1. `CollectionHandlerRegistrations = "handler_registrations"` protocol constant
2. `refreshTopics()` — reads `handler_registrations` from MongoDB
3. `effectiveHandlers()` — returns the intersection of registered handlers and active DB topics
4. Auto-started refresh loop (default 30s) to pick up new registrations

**Key logic** (same across all SDKs):

```
refreshTopics():
  docs = db.handler_registrations.find({})
  activeTopics = {doc.facet_name for doc in docs}

effectiveHandlers():
  return registeredHandlers ∩ activeTopics
```

**Refresh loop wiring:** All SDKs auto-start the refresh loop in
`start()` — creating their own MongoDB client from poller config,
calling `refreshTopics()` once immediately, then scheduling periodic
refreshes every 30 seconds. The MongoDB client is disconnected in
`stop()`.

| SDK | File | Refresh mechanism | Topic storage |
|-----|------|-------------------|---------------|
| Scala | `RegistryRunner.scala` | Daemon thread with sleep loop | `AtomicReference[Set[String]]` |
| Go | `registry_runner.go` | `time.Ticker` goroutine + `stopCh` select | `sync.RWMutex` protected set |
| TypeScript | `registry-runner.ts` | `setInterval` (auto-started) | `Set<string>` |
| Java | `RegistryRunner.java` | `ScheduledExecutorService` (daemon factory) | `ConcurrentHashMap.newKeySet()` |

### 9.14 Handler Metadata Injection (All SDKs)

All SDKs (Python, Scala, Go, TypeScript, Java) inject `_facet_name` and
`_handler_metadata` into the handler params before invocation:

| Injected key | Value | Purpose |
|-------------|-------|---------|
| `_facet_name` | Qualified event facet name (e.g. `"ns.CountDocuments"`) | Allows handlers to identify which facet triggered them |
| `_handler_metadata` | Metadata dict from `HandlerRegistration.metadata` | Passes registration-time config to handler code |
| `_step_log` | Step logging callback | Handler-visible logging (see §9.15) |
| `_task_heartbeat` | Liveness callback | Prevents timeout reaping (see §9.15) |
| `_task_uuid` | Task UUID string | Unique task identifier |
| `_retry_count` | Integer (0 on first run) | Number of prior attempts |
| `_is_retry` | Boolean | `True` when reclaiming a previously-attempted task |

**Non-Python SDK implementation:**

| SDK | Metadata source | Injection point |
|-----|----------------|-----------------|
| Go | `metadataProvider func(string) map[string]interface{}` on `AgentPoller` | `processTask()` after `_step_log` |
| TypeScript | `metadataProvider: ((name: string) => Record\|undefined)\|null` on `AgentPoller` | `processTask()` after `_step_log` |
| Scala | `metadataProvider: String => Option[Map[String, Any]]` on `AgentPoller` | `processTask()` after `_step_log` |
| Java | `Function<String, Map<String,Object>>` via `setMetadataProvider()` on `AgentPoller` | `processTask()` after `_step_log` |

The `RegistryRunner` in each SDK stores handler metadata during
`refreshTopics()` (reading the `metadata` field from each
`handler_registrations` document) and wires a metadata provider into the
poller at construction time.

### 9.15 Handler Responsibilities: Errors, Heartbeats, Timeouts, and Retries

Handlers are responsible for cooperating with the runtime to ensure
reliable execution. This section specifies the contract between a
handler and the runner that dispatches it.

#### Injected Payload Fields

The runner injects these fields into every handler payload before
dispatch. Handlers should treat all `_`-prefixed keys as runtime
metadata — never return them in results.

| Key | Type | Description |
|-----|------|-------------|
| `_facet_name` | `str` | Qualified event facet name (e.g. `"osm.ops.PostGisImport"`) |
| `_step_log` | `callable(message, level?, details?)` | Emit a step log visible in the dashboard |
| `_task_heartbeat` | `callable(progress_pct?, progress_message?)` | Signal liveness to avoid timeout |
| `_task_uuid` | `str` | Task UUID |
| `_retry_count` | `int` | Number of prior attempts (0 on first execution) |
| `_is_retry` | `bool` | `True` if this is a reclaimed/retried task |
| `_handler_metadata` | `dict` | From `HandlerRegistration.metadata` (if present) |

#### Error Handling

Handlers communicate failure by raising an exception. The runtime
catches it and either retries (incrementing `retry_count`) or
dead-letters the task (when `retry_count >= max_retries`).

**Rules:**

1. **Raise on failure** — never return a partial result and hope
   downstream steps can cope. An uncaught exception triggers the
   retry/dead-letter cycle. A returned dict is treated as success.

2. **Wrap external errors with context** — re-raise with a message
   that includes the operation, region, or resource so the step log
   is actionable:
   ```python
   except psycopg2.OperationalError as exc:
       raise RuntimeError(f"PostGIS connection failed for {region}: {exc}") from exc
   ```

3. **Transient vs permanent** — the workflow repair tool
   (`repair_workflow`) auto-retries steps whose error matches
   transient patterns (connection refused, timeout, I/O error). For
   permanent errors (bad input, missing data), include a clear
   message so operators know not to blindly retry.

4. **Never silently return empty defaults** — if a required resource
   is missing, raise rather than returning `{"count": 0}`.

#### Heartbeat / Liveness

The runner monitors task liveness via two mechanisms:

- **Execution timeout** (`AFL_TASK_EXECUTION_TIMEOUT_MS`, default
  15 min): the runner kills tasks with no heartbeat activity beyond
  this threshold.
- **Stuck-task watchdog** (`AFL_STUCK_TIMEOUT_MS`, default 30 min):
  a background sweep resets stuck tasks. If the handler registration
  has `timeout_ms > 0`, that per-handler timeout is used instead.

**Rules:**

1. **Call `_task_heartbeat()` periodically** for any operation that
   may exceed 60 seconds. The runtime uses the heartbeat timestamp
   as "last known alive" — without it, the task appears stuck.

2. **Call heartbeat early** — emit an initial heartbeat at handler
   entry before any slow I/O begins. This establishes the liveness
   baseline immediately:
   ```python
   def handle(payload: dict) -> dict:
       heartbeat = payload.get("_task_heartbeat")
       if heartbeat:
           heartbeat(progress_message="Starting import")
       # ... slow work follows ...
   ```

3. **Include progress info** — pass `progress_message` (and
   optionally `progress_pct` 0–100) so operators can see what the
   handler is doing from the dashboard:
   ```python
   heartbeat(progress_pct=45, progress_message=f"Imported {count:,} rows")
   ```

4. **Heartbeat interval** — every 30–60 seconds is ideal. More
   frequent is harmless but wastes a database write. Less frequent
   risks falling outside the timeout window.

5. **Long-running handlers should register with `timeout_ms=0`** —
   this disables the per-handler stuck-task watchdog and relies
   entirely on heartbeat + the global execution timeout. The default
   registration timeout of 30 seconds is only appropriate for fast
   handlers.

#### Timeouts

| Timeout | Source | Default | Scope |
|---------|--------|---------|-------|
| `timeout_ms` on `HandlerRegistration` | Per-handler | 30s | Stuck-task watchdog kills tasks exceeding this |
| `AFL_TASK_EXECUTION_TIMEOUT_MS` | Global env var | 900s (15min) | Runner kills tasks with no heartbeat beyond this |
| `AFL_STUCK_TIMEOUT_MS` | Global env var | 1800s (30min) | Background sweep resets orphaned running tasks |
| `AFL_REAPER_TIMEOUT_MS` | Global env var | 120s (2min) | Dead-server detection threshold |

When a task times out, the runner increments `retry_count` and resets
the task to pending. After `max_retries` (default 5) timeouts, the
task is dead-lettered.

**Controlling timeouts per handler:**

Handlers that perform long-running operations (bulk imports, large
file processing) should set `timeout_ms=0` at registration time to
disable the per-handler stuck-task watchdog:

```python
runner.register_handler(
    facet_name="osm.ops.PostGisImport",
    module_uri="handlers.postgis",
    entrypoint="handle",
    timeout_ms=0,  # disable per-handler timeout; rely on heartbeat
)
```

With `timeout_ms=0`, the handler depends on the global
`AFL_TASK_EXECUTION_TIMEOUT_MS` and heartbeats. If the handler has
phases where no heartbeat can fire (e.g., blocking database calls),
increase the global timeout.

**Per-example timeout configuration:**

Each example can provide a `runner.env` file that overrides global
defaults when that example is started via `scripts/start-runner`:

```bash
# examples/osm-geocoder/runner.env
AFL_TASK_EXECUTION_TIMEOUT_MS=14400000   # 4 hours for PostGIS imports
```

The `start-runner` script sources `runner.env` from each selected
example before starting the runner, so different examples can have
different timeout profiles without affecting each other:

```bash
# Uses osm-geocoder/runner.env (4-hour timeout)
scripts/start-runner --example osm-geocoder

# Uses default timeout (15 min) — no runner.env
scripts/start-runner --example hiv-drug-resistance
```

#### Retries and Reclaim

When a handler fails or times out, the task is reset to pending and
eventually reclaimed by a runner. The handler receives retry context
so it can avoid redoing completed work:

```python
def handle(payload: dict) -> dict:
    is_retry = payload.get("_is_retry", False)
    retry_count = payload.get("_retry_count", 0)

    if is_retry:
        # Check what was already completed in previous attempts
        existing = check_prior_work(payload)
        if existing:
            return existing  # skip re-processing
    # ... normal processing ...
```

**`_is_retry`** is `True` when `retry_count > 0` — meaning the task
was previously attempted and either timed out, failed, or was
reclaimed after a server shutdown.

**`_retry_count`** is the number of prior attempts. Handlers can use
this for:

- **Idempotency checks** — query the target system for evidence of
  prior work (e.g., check `osm_import_log` for a region already
  imported, check for output files already written).

- **Progressive backoff** — wait longer before retrying an external
  service that was previously unavailable.

- **Defensive cleanup** — delete partial state from a prior crashed
  attempt before restarting (e.g., `DELETE FROM table WHERE region =
  $region` before re-importing).

**Best practices for retry-safe handlers:**

1. **Design for idempotency** — use upserts, check-before-write, or
   transactional cleanup so that re-running the same handler with
   the same inputs produces the same result.

2. **Check for prior completion** — on retry, query the target
   system first. If the work was fully done, return the result
   immediately. The PostGIS importer checks `osm_import_log`; a
   file-writing handler can check if the output file exists.

3. **Log retries explicitly** — use `_step_log` to indicate a retry
   is happening so operators know:
   ```python
   if is_retry:
       step_log(f"Retry #{retry_count}: checking for prior work")
   ```

4. **Clean up partial state** — if the handler writes to a database
   without transactions, previous partial writes may be present. On
   retry, either delete-and-reinsert or use upserts.

5. **Do not assume ordering** — a different runner (on a different
   server) may reclaim the task. Local temp files from the prior
   attempt may not exist.

### 9.16 Streaming/Partial Updates (All SDKs)

All SDKs provide an `_update_step` callback injected into handler params,
enabling handlers to publish partial results before returning the final
result.

**Python (RegistryRunner):** Uses `update_step()` method (§9.9).

**Non-Python SDKs:** Each SDK implements `UpdateStepReturns` in its
MongoOps module — a MongoDB `$set` operation on
`attributes.returns.<name>` fields, filtered only by step UUID (no state
guard, unlike `WriteStepReturns`). The callback is injected alongside
`_step_log`, `_facet_name`, and `_handler_metadata`:

| SDK | MongoOps method | Callback type |
|-----|----------------|---------------|
| Go | `UpdateStepReturns(ctx, stepID, partial)` | `func(map[string]interface{})` |
| TypeScript | `updateStepReturns(stepId, partial)` | `async (partial) => void` |
| Scala | `updateStepReturns(stepId, partial)` | `Map[String, Any] => Unit` |
| Java | `updateStepReturns(stepId, partial)` | `Consumer<Map<String,Object>>` |

Each value in the partial map is stored with a type hint via
`inferTypeHint()` (§7.8).

---

## 10. ClaudeAgentRunner

The `ClaudeAgentRunner` is a synchronous in-process execution model
designed for LLM-driven workflow processing.

### 10.1 Characteristics

| Aspect | Behavior |
|--------|----------|
| Execution model | Synchronous, in-process |
| Handler dispatch | `ToolRegistry` + Anthropic Claude API |
| Task queue | Not used |
| Server registration | Not used |
| Persistence | Required (for step/event storage) |
| Concurrency | Single-threaded |

### 10.2 Execution Flow

1. The `ClaudeAgentRunner` receives a workflow AST and inputs.
2. The evaluator executes until it reaches `EVENT_TRANSMIT`.
3. The runner extracts the event payload and sends it to the Claude API.
4. The Claude response is passed to `continue_step()`.
5. The evaluator resumes and continues to the next event or completion.

Unlike `AgentPoller` and `RunnerService`, the `ClaudeAgentRunner` does
not poll a task queue. It drives execution directly through the evaluator
in a tight loop.

---

## 11. Concurrency Model

The `AgentPoller`, `RegistryRunner`, and `RunnerService` process multiple event tasks
concurrently. This section defines how in-memory state is kept isolated
between concurrent executions.

### 11.1 Design Principle

The `Evaluator`, `AgentPoller`, `RegistryRunner`, and `RunnerService` are **shared
instances**, but all mutable execution state is **created per-invocation**.
The shared `PersistenceAPI` acts as the sole coordination point, with
atomicity guarantees enforced at the storage layer.

```
┌──────────────────────────────────────────────────────┐
│              Shared (long-lived)                      │
│                                                      │
│  Evaluator   AgentPoller/RunnerService   Persistence │
│     │               │                       │        │
│     │   ┌───────────┼───────────┐           │        │
│     │   │  Thread A  │  Thread B │           │        │
│     │   │           │           │           │        │
│     │   │  ┌────────┴─┐  ┌─────┴──────┐    │        │
│     │   │  │ Context A │  │ Context B  │    │        │
│     │   │  │ Changes A │  │ Changes B  │    │        │
│     │   │  │ Step copy │  │ Step copy  │    │        │
│     │   │  └──────────┘  └────────────┘    │        │
│     │   └───────────────────────────────┘   │        │
│     │                                       │        │
│     └───────────── read/write ──────────────┘        │
└──────────────────────────────────────────────────────┘
```

### 11.2 Per-Invocation ExecutionContext

Each call to `execute()` or `resume()` MUST create a **fresh
`ExecutionContext`** with a new `IterationChanges` instance:

```python
context = ExecutionContext(
    persistence=self.persistence,
    telemetry=self.telemetry,
    changes=IterationChanges(),   # fresh per invocation
    workflow_id=wf_id,
    workflow_ast=workflow_ast,
    ...
)
```

The `ExecutionContext` contains per-invocation caches
(`_block_graphs`, `_completed_step_cache`) that are private to each
execution. Concurrent threads calling `resume()` for different events
operate on entirely separate context objects.

### 11.3 Deep-Copy Persistence Pattern

The `MemoryStore` MUST return a **deep copy** of every `StepDefinition`
on read and MUST clone before storing on write:

```python
def get_step(self, step_id: StepId) -> Optional[StepDefinition]:
    step = self._steps.get(step_id)
    if step:
        return step.clone()    # copy.deepcopy
    return None
```

This ensures that each thread operates on its own copy of step and
event data. Concurrent modifications to the same step in different
threads do not collide in memory.

The `MongoStore` achieves the same isolation naturally — each read
deserializes a fresh object from the database document.

### 11.4 Atomic Task Claiming

The `claim_task()` method MUST guarantee that exactly one caller
receives a given task:

- **MemoryStore**: uses a `threading.Lock` around the
  `PENDING → RUNNING` transition.
- **MongoStore**: uses `find_one_and_update()` with a state filter,
  which is atomic at the database level.
- A partial unique index on `(step_id, state=running)` ensures at most
  one agent processes a given event step at any time.

### 11.5 Isolation Guarantees

| Layer | Mechanism | Scope |
|-------|-----------|-------|
| `ExecutionContext` | Fresh instance per `execute()` / `resume()` | Per-invocation |
| `IterationChanges` | Fresh instance per context | Per-invocation |
| `StepDefinition` reads | Deep copy (`clone()`) in MemoryStore; deserialization in MongoStore | Per-read |
| `StepDefinition` writes | Clone before store (MemoryStore); serialize to document (MongoStore) | Per-write |
| Task claiming | `threading.Lock` (memory) / `find_one_and_update` (MongoDB) | Global |

### 11.6 Known Benign Races

The following shared state is accessed without synchronization. These
races are benign and do not affect correctness:

- **AST cache** (`_ast_cache` in `AgentPoller` and `RunnerService`):
  concurrent threads may populate the same key simultaneously. The
  worst case is duplicate loading of an immutable AST — no corruption
  occurs.
- **Handled-count statistics** (`_handled_counts` in `RunnerService`):
  counter increments are not atomic, so counts may drift slightly under
  contention. These are cosmetic statistics only.

---

## 12. Key Files Reference

| File | Description |
|------|-------------|
| `afl/runtime/registry_runner.py` | `RegistryRunner` class, `RegistryRunnerConfig`, dynamic handler loading |
| `afl/runtime/agent_poller.py` | `AgentPoller` class and `AgentPollerConfig` |
| `afl/runtime/runner/service.py` | `RunnerService` class and `RunnerConfig` |
| `afl/runtime/entities.py` | `TaskDefinition`, `TaskState`, `ServerDefinition`, `ServerState`, `HandlerRegistration` |
| `afl/runtime/persistence.py` | `PersistenceAPI` protocol (including `claim_task()`, `register_handler()`) |
| `afl/runtime/evaluator.py` | `ExecutionContext`, `continue_step()`, `fail_step()`, `resume()` |
| `afl/runtime/events.py` | `EventManager`, `EventDispatcher`, event lifecycle |
| `afl/runtime/memory_store.py` | In-memory `PersistenceAPI` with deep-copy isolation |
| `afl/runtime/mongo_store.py` | MongoDB `PersistenceAPI` with atomic operations |
| `afl/runtime/runner/__main__.py` | CLI entry point: `python -m afl.runtime.runner` |
| `afl/runtime/handlers/initialization.py` | `EventTransmitHandler` (task creation) |

---

## 13. Comparison Matrix

| Feature | RegistryRunner | AgentPoller | RunnerService | ClaudeAgentRunner |
|---------|----------------|-------------|---------------|-------------------|
| Task queue polling | Yes | Yes | Yes | No |
| Step state polling | No | No | Yes | No |
| Distributed locking | No | No | Yes | No |
| HTTP status server | No | No | Yes | No |
| Server registration | Yes | Yes | Yes | No |
| Heartbeat | Yes | Yes | Yes | No |
| Handler model | `HandlerRegistration` auto-load | `register()` callback | `ToolRegistry` | `ToolRegistry` + Claude API |
| Concurrency | Sequential (`poll_once`) | Sequential (`poll_once`) | `ThreadPoolExecutor` | Single-threaded |
| Signal handling | No | No | Yes (SIGTERM/SIGINT) | No |
| Non-event tasks | No | No | Yes | No |
| AST caching | `cache_workflow_ast()` | `cache_workflow_ast()` | `cache_workflow_ast()` | In-process |
| Short-name fallback | Yes | Yes | Yes | Yes |
| Error → `fail_step()` | Yes | Yes | Yes | Yes |
| Dynamic handler loading | Yes (from DB) | No | No | No |
| Module caching | Yes (by checksum) | N/A | N/A | N/A |
| Topic filtering | Yes | No | Yes | No |
| Registry refresh | Yes (periodic) | N/A | N/A | N/A |
| Custom code required | No (handlers only) | Yes (service) | Yes (service) | Yes (service) |
| Intended use | Production (recommended) | Standalone agent services | Distributed orchestration | LLM-driven execution |

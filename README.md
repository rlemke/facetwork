# AgentFlow

> This project was built entirely with [Claude](https://claude.ai) — the only human input is the specification documents in `spec/`. And actually claude wrote those, I just gave suggestions. Having been retired for a while, when I heard about Claude, I needed to see how much of my previous career had just been eliminated (or at least made possible in a very small fraction of the time). This is a substantial, fully functional platform written exclusively through AI-assisted development, although it should only be used as an example of what beginners can do with AI coding assistance. This is still a work in progress and goes many ways as I say "I want to try this or that". In the development of this, most of my time has been watching youTube channels. If I understood Claude when I started, this project would be a few days at the most

**AgentFlow** is a platform for defining and executing distributed workflows with facets, events, and dependency-driven execution.

**AFL** (Agent Flow Language) is the workflow DSL used by AgentFlow. AFL source files use the `.afl` extension and are compiled to JSON workflow definitions that can be executed by the runtime.

## The Contract: Specifications

The `spec/` directory contains the authoritative specifications for the AgentFlow platform. These are the contract — start here to understand the system design.

| Start Here | Document | What It Covers |
|------------|----------|----------------|
| **Language** | [spec/10_language.md](spec/10_language.md) | AFL syntax reference — lexical rules, EBNF grammar, all language constructs |
| **Runtime** | [spec/30_runtime.md](spec/30_runtime.md) | Execution semantics — iteration model, determinism, idempotency guarantees |
| **Persistence** | [spec/40_database.md](spec/40_database.md) | MongoDB schema — collections, indexes, atomic commit boundaries |
| **Events** | [spec/50_event_system.md](spec/50_event_system.md) | Event/agent protocol — lifecycle, dispatch, task queue, step locking |
| **Agent SDK** | [spec/60_agent_sdk.md](spec/60_agent_sdk.md) | Building agents — how external services process event facets |

**Supporting specifications:**
- [spec/00_overview.md](spec/00_overview.md) — Implementation constraints and terminology
- [spec/11_semantics.md](spec/11_semantics.md) — AST structure and Lark transformer requirements
- [spec/12_validation.md](spec/12_validation.md) — Semantic validation rules
- [spec/20_compiler.md](spec/20_compiler.md) — Compiler architecture (AFL → JSON)
- [spec/51_state_system.md](spec/51_state_system.md) — State handler architecture (discrete step transitions)
- [spec/61_llm_agent_integration.md](spec/61_llm_agent_integration.md) — LLM agent patterns and prompts
- [spec/70_examples.md](spec/70_examples.md) — AFL code examples
- [spec/80_acceptance_tests.md](spec/80_acceptance_tests.md) — Test requirements

## Quick Start

### Installation

```bash
# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install the package (includes lark dependency)
pip install -e .

# For development (adds pytest, ruff, mypy, pre-commit)
pip install -e ".[dev]"

# For running tests with mongomock
pip install -e ".[test]"

# For full stack (dashboard + MCP + MongoDB)
pip install -e ".[dev,test,dashboard,mcp,mongodb]"
```

**Dependency groups** (defined in `pyproject.toml`):
| Group | Includes |
|-------|----------|
| (base) | `lark` |
| `dev` | pytest, pytest-cov, ruff, mypy, pre-commit |
| `test` | pytest, pytest-cov, mongomock |
| `mongodb` | pymongo |
| `dashboard` | fastapi, uvicorn, jinja2 |
| `mcp` | mcp |

### Docker: One-Command Local Stack

The easiest way to run the full AgentFlow stack is with Docker Compose:

```bash
# Start everything: MongoDB + Dashboard + Runner + Sample Agent
docker compose up

# In another terminal, seed example workflows
docker compose run seed
```

**Services started:**
| Service | Port | Description |
|---------|------|-------------|
| Dashboard | http://localhost:8080 | Web UI for monitoring workflows |
| MongoDB | localhost:27017 | Database for workflow state |
| Runner | - | Distributed execution service |
| Agent | - | Sample AddOne agent |

**After seeding**, the dashboard will show example workflows you can execute:
- `handlers.AddOneWorkflow` — Adds 1 to input (try input=41, expect output=42)
- `handlers.DoubleAddOne` — Adds 1 twice
- `handlers.MultiplyAndAdd` — Multiplies then adds 1
- `chain.ChainOfThree` — Three sequential additions

```bash
# Stop all services
docker compose down

# Stop and remove data volumes
docker compose down -v
```

### Running Tests

```bash
# Run all tests
pytest tests/ -v

# Run with coverage
pytest tests/ --cov=afl --cov-report=term-missing

# Run a specific test
pytest tests/test_parser.py::TestWorkflows -v

# Run MongoDB tests against a real server (uses AFL config for connection)
pytest tests/runtime/test_mongo_store.py --mongodb -v

# Run dashboard tests
pytest tests/dashboard/ -v
```

### Using the Parser

```python
from afl import parse, AFLParser, ParseError

# Parse AFL source code
source = """
facet User(name: String, email: String)

workflow SendEmail(to: String, body: String) => (status: String) andThen {
    user = User(name = $.to, email = $.to)
    result = EmailService(recipient = user.email, content = $.body)
    yield SendEmail(status = result.status)
}
"""

# Using the convenience function
ast = parse(source)

# Or create a parser instance (recommended for repeated parsing)
parser = AFLParser()
ast = parser.parse(source)

# Access AST nodes
for workflow in ast.workflows:
    print(f"Workflow: {workflow.sig.name}")
    for param in workflow.sig.params:
        print(f"  Param: {param.name}: {param.type.name}")
```

### Emitting JSON

```python
from afl import parse, emit_json, emit_dict

ast = parse("facet User(name: String)")

# Get JSON string
json_str = emit_json(ast)
print(json_str)

# Get dictionary
data = emit_dict(ast)
print(data["facets"][0]["name"])  # "User"

# Compact output without locations
json_str = emit_json(ast, include_locations=False, indent=None)
```

### Command-Line Interface

After installation, the `afl` command is available:

```bash
# Parse file and emit JSON (includes validation)
afl input.afl

# Parse from stdin
echo 'facet Test()' | afl

# Output to file
afl input.afl -o output.json

# Compact JSON without locations
afl input.afl --compact --no-locations

# Syntax check only (includes validation)
afl input.afl --check

# Skip semantic validation
afl input.afl --no-validate

# Use a custom config file
afl input.afl --config /path/to/afl.config.json

# Load library sources from MongoDB
afl input.afl --mongo FLOW_UUID:DisplayName

# Load library sources from Maven Central
afl input.afl --maven com.example:artifact:1.0
afl input.afl --maven com.example:artifact:1.0:sources  # with classifier
```

### Source Loaders

AFL supports loading library sources from multiple locations:

**MongoDB**: Load AFL sources stored in the `flows` collection:

```python
from afl import SourceLoader

# Load from MongoDB by flow UUID
entry = SourceLoader.load_mongodb(
    collection_id="abc123",
    display_name="MyLibrary",
    is_library=True
)
```

**Maven Central**: Download and extract `.afl` files from Maven artifacts:

```python
# Load from Maven Central
entry = SourceLoader.load_maven(
    group_id="com.example",
    artifact_id="afl-stdlib",
    version="1.0.0",
    classifier="sources",  # optional
    is_library=True
)
```

### Configuration

AFL resolves configuration from (in order): explicit `--config` path, `AFL_CONFIG` env var, `afl.config.json` in cwd / `~/.afl/` / `/etc/afl/`, `AFL_MONGODB_*` env vars, or built-in defaults.

**Example `afl.config.json`:**

```json
{
  "mongodb": {
    "url": "mongodb://localhost:27017",
    "username": "",
    "password": "",
    "authSource": "admin",
    "database": "afl"
  }
}
```

For authenticated connections, set credentials via environment variables (recommended) or in the config file. Each developer can use their own database name to avoid conflicts (e.g. `"afl_dev_alice"`, `"afl_test"`).

**Environment variables:** `AFL_MONGODB_URL`, `AFL_MONGODB_USERNAME`, `AFL_MONGODB_PASSWORD`, `AFL_MONGODB_AUTH_SOURCE`, `AFL_MONGODB_DATABASE`.

```python
from afl import load_config, AFLConfig, MongoDBConfig

# Load from default resolution order
config = load_config()

# Load from explicit file
config = load_config("/path/to/afl.config.json")

# Access MongoDB settings
print(config.mongodb.url)
print(config.mongodb.username)
```

### Semantic Validation

```python
from afl import parse, validate

ast = parse("""
facet Data(value: String) => (result: String)
workflow Test(input: String) => (output: String) andThen {
    step1 = Data(value = $.input)
    yield Test(output = step1.result)
}
""")

result = validate(ast)
if result.is_valid:
    print("Valid!")
else:
    for error in result.errors:
        print(f"Error: {error}")
```

**Validation checks:**
- Name uniqueness (facets, workflows, schemas, steps)
- Schema field name uniqueness within each schema
- Valid input references (`$.param` must exist)
- Valid step references (`step.attr` must exist)
- Yield targets (must be containing facet or mixin)
- No duplicate yield targets
- Use statements (must reference existing namespaces)
- Facet name resolution (ambiguity detection with qualified names)

### Handling Parse Errors

```python
from afl import parse, ParseError

try:
    ast = parse("facet Invalid(")
except ParseError as e:
    print(f"Error: {e}")
    print(f"Line: {e.line}, Column: {e.column}")
```

### Executing Workflows

```python
from afl import emit_dict
from afl.runtime import Evaluator, MemoryStore, Telemetry

# Compile AFL to AST, then to dict
ast = parse("""
workflow AddNumbers(a: Long = 1, b: Long = 2) => (result: Long) andThen {
    sum = Compute(input = $.a)
    yield AddNumbers(result = sum.input)
}
""")
workflow_ast = emit_dict(ast)["workflows"][0]

# Create runtime components
store = MemoryStore()
telemetry = Telemetry(enabled=True)
evaluator = Evaluator(persistence=store, telemetry=telemetry)

# Execute with inputs
result = evaluator.execute(workflow_ast, inputs={"a": 5, "b": 10})

if result.success:
    print(f"Result: {result.outputs}")
else:
    print(f"Error: {result.error}")
```

### End-to-End: Agent Dispatch and Resume

The previous example showed a simple workflow. This example demonstrates the **distributed execution model** — the core of AgentFlow:

1. **Compile** AFL source to JSON
2. **Execute** workflow — runtime pauses at event facet
3. **Agent** polls for tasks, dispatches to handler, writes result
4. **Resume** workflow — runtime continues to completion

```python
from afl import parse, emit_dict
from afl.runtime import Evaluator, MemoryStore, Telemetry, ExecutionStatus
from afl.runtime.agent_poller import AgentPoller, AgentPollerConfig

# ─────────────────────────────────────────────────────────────────────────────
# Step 1: Compile AFL source
# ─────────────────────────────────────────────────────────────────────────────

source = """
namespace demo {
    event facet AddOne(input: Long) => (output: Long)
}

workflow Increment(x: Long) => (result: Long) andThen {
    step = demo.AddOne(input = $.x)
    yield Increment(result = step.output)
}
"""

ast = parse(source)
compiled = emit_dict(ast)
workflow_ast = compiled["workflows"][0]
program_ast = compiled  # Full program needed for facet resolution

# ─────────────────────────────────────────────────────────────────────────────
# Step 2: Execute workflow — pauses at event facet
# ─────────────────────────────────────────────────────────────────────────────

store = MemoryStore()
evaluator = Evaluator(persistence=store, telemetry=Telemetry(enabled=False))

result = evaluator.execute(workflow_ast, inputs={"x": 41}, program_ast=program_ast)

assert result.status == ExecutionStatus.PAUSED  # Blocked at AddOne event facet
print(f"Workflow paused: {result.workflow_id}")

# ─────────────────────────────────────────────────────────────────────────────
# Step 3: Agent processes the event
# ─────────────────────────────────────────────────────────────────────────────

def addone_handler(payload: dict) -> dict:
    """Agent logic: reads input, returns output."""
    return {"output": payload["input"] + 1}

poller = AgentPoller(
    persistence=store,
    evaluator=evaluator,
    config=AgentPollerConfig(service_name="demo-agent"),
)
poller.register("demo.AddOne", addone_handler)

# Cache AST for resume (in production, stored in MongoDB)
poller.cache_workflow_ast(result.workflow_id, workflow_ast)

# Poll once: claims task, calls handler, writes result, creates resume task
dispatched = poller.poll_once()
assert dispatched == 1
print(f"Agent dispatched {dispatched} task(s)")

# ─────────────────────────────────────────────────────────────────────────────
# Step 4: Resume workflow to completion
# ─────────────────────────────────────────────────────────────────────────────

final = evaluator.resume(result.workflow_id, workflow_ast, program_ast)

assert final.status == ExecutionStatus.COMPLETED
assert final.outputs["result"] == 42  # 41 + 1
print(f"Workflow completed: {final.outputs}")
```

**What happens under the hood:**

| Phase | Runtime State | Agent Action |
|-------|---------------|--------------|
| Execute | Creates steps, evaluates until `AddOne` event facet | — |
| Pause | Step blocked at `EVENT_TRANSMIT`, task created in queue | — |
| Dispatch | — | Claims task, reads `step.input`, calls handler |
| Continue | — | Writes `step.output`, marks task complete, creates `afl:resume` task |
| Resume | Continues from blocked step, evaluates yield, completes | — |

This is the same flow that happens in production with MongoDB persistence and distributed agents — the `MemoryStore` just keeps everything in-process for demonstration.

## Dashboard

AgentFlow includes a web dashboard for monitoring and managing running workflows. Built with FastAPI, Jinja2, and htmx — no JavaScript build toolchain required.

### Installation

```bash
pip install -e ".[dashboard,mongodb]"
```

### Launch

```bash
# Start on default port 8080
python -m afl.dashboard

# Custom port and config
python -m afl.dashboard --port 9000 --config /path/to/afl.config.json

# Development mode with auto-reload
python -m afl.dashboard --reload
```

### Features

The dashboard uses a 2-tab navigation (**Workflows** / **Servers**) with a **More** dropdown for secondary pages. `GET /` redirects to `/v2/workflows`.

- **Workflows (v2)** — namespace-grouped runner list with Running/Completed/Failed sub-tabs, inline step expansion, HTMX 5s auto-refresh
- **Servers (v2)** — server-group accordion with Running/Startup/Error/Shutdown sub-tabs, detail page with handlers/stats, HTMX 5s auto-refresh
- **Runners** — list (filterable by state), detail with steps/logs/parameters (legacy)
- **Steps** — detail view with attributes (params and returns tables)
- **Flows** — list, detail, AFL source with syntax highlighting, compiled JSON view
- **Logs** — log entries per runner ordered by time
- **Tasks** — task queue with state and metadata
- **Actions** — cancel/pause/resume runners, retry failed steps (via htmx POST with confirmation)
- **Auto-refresh** — running workflow pages poll every 5 seconds for live updates
- **JSON API** — all data available as JSON at `/api/*` endpoints

## Distributed Runner Service

The runner service is a long-lived process that polls MongoDB for blocked steps and pending tasks, acquires distributed locks, and dispatches events to registered tool handlers. Multiple instances can run concurrently on different machines.

### Launch

```bash
# Start the runner service
python -m afl.runtime.runner

# Custom configuration
python -m afl.runtime.runner --config /path/to/afl.config.json

# Handle specific event topics only
python -m afl.runtime.runner --topics TopicA TopicB

# Increase concurrency
python -m afl.runtime.runner --max-concurrent 10 --poll-interval 500
```

### Programmatic Usage

```python
from afl.runtime import RunnerService, RunnerConfig, Evaluator, MemoryStore, Telemetry
from afl.runtime.agent import ToolRegistry

store = MemoryStore()
evaluator = Evaluator(persistence=store, telemetry=Telemetry())

registry = ToolRegistry()
registry.register("CountDocuments", lambda payload: {"output": len(payload)})

config = RunnerConfig(max_concurrent=10, poll_interval_ms=1000)
service = RunnerService(store, evaluator, config, registry)

# For testing: run a single poll cycle
dispatched = service.run_once()

# For production: blocking start (use service.stop() from signal handler)
service.start()
```

## MCP Server

AgentFlow includes an MCP (Model Context Protocol) server that exposes the AFL compiler and runtime as tools and resources for LLM agents. The server communicates over stdio, the standard transport for local MCP servers.

### Installation

```bash
pip install -e ".[mcp,mongodb]"
```

### Launch

```bash
# Start the MCP server (stdio transport)
python -m afl.mcp

# Custom config
python -m afl.mcp --config /path/to/afl.config.json

# Or use the convenience script
scripts/mcp-server
```

### Protocol

MCP uses JSON-RPC 2.0 over stdio. The AFL server handles these protocol messages:

| Message | Direction | Purpose |
|---------|-----------|---------|
| `initialize` | Client → Server | Handshake; server advertises `tools` + `resources` capabilities |
| `tools/list` | Client → Server | Returns 6 tool definitions with JSON Schema input validation |
| `tools/call` | Client → Server | Executes a tool by name with arguments, returns JSON result |
| `resources/list` | Client → Server | Returns 10 resource definitions with `afl://` URIs |
| `resources/read` | Client → Server | Reads a resource by URI, returns JSON data |

### Tools

| Tool | Parameters | Returns |
|------|-----------|---------|
| `afl_compile` | `source: str` | `{ success, json?, errors? }` |
| `afl_validate` | `source: str` | `{ valid, errors: [{ message, line?, column? }] }` |
| `afl_execute_workflow` | `source: str`, `workflow_name: str`, `inputs?: dict` | `{ success, workflow_id, status, iterations, outputs, error? }` |
| `afl_continue_step` | `step_id: str`, `result?: dict` | `{ success, error? }` |
| `afl_resume_workflow` | `workflow_id: str`, `source: str`, `workflow_name: str`, `inputs?: dict` | `{ success, workflow_id, status, iterations, outputs, error? }` |
| `afl_manage_runner` | `runner_id: str`, `action: str` (cancel/pause/resume) | `{ success, error? }` |

### Resources

| URI Pattern | Response Shape |
|-------------|---------------|
| `afl://runners` | `[{ uuid, workflow_id, workflow_name, state, start_time, end_time, duration, parameters }]` |
| `afl://runners/{id}` | `{ uuid, workflow_id, workflow_name, state, ... }` |
| `afl://runners/{id}/steps` | `[{ id, workflow_id, object_type, state, statement_id, container_id, block_id, params?, returns? }]` |
| `afl://runners/{id}/logs` | `[{ uuid, order, runner_id, step_id, note_type, message, state, time }]` |
| `afl://steps/{id}` | `{ id, workflow_id, object_type, state, facet_name?, params?, returns? }` |
| `afl://flows` | `[{ uuid, name, path, workflows: [{ uuid, name, version }], sources, facets }]` |
| `afl://flows/{id}` | `{ uuid, name, path, workflows, sources, facets }` |
| `afl://flows/{id}/source` | `{ uuid, name, sources: [{ name, content, language }] }` |
| `afl://servers` | `[{ uuid, server_group, service_name, server_name, state, ping_time, handlers, handled }]` |
| `afl://tasks` | `[{ uuid, name, runner_id, workflow_id, step_id, state, created, updated }]` |

## Agent Integration Libraries

AgentFlow agents can be built in any language. The `agents/` directory contains shared protocol constants and language-specific libraries that handle the polling, task claiming, and workflow resumption protocol.

### How It Works

When a workflow reaches an event facet, the runtime creates a **task** in MongoDB. An external agent (in any language) processes the event by:

1. **Claim** a pending task atomically from the `tasks` collection
2. **Read** the step's input parameters from the `steps` collection
3. **Perform** the work (API calls, data processing, etc.)
4. **Write** return attributes back to the step
5. **Mark** the task as completed
6. **Insert** an `afl:resume` task so the Python RunnerService resumes the workflow

The protocol constants in `agents/protocol/constants.json` define all collection names, state values, document schemas, and MongoDB operation patterns needed to implement this in any language.

### Python (built-in)

The Python `AgentPoller` is built into the AFL runtime:

```python
from afl.runtime import Evaluator, AgentPoller, AgentPollerConfig
from afl.runtime import MongoStore, Telemetry
from afl import load_config

config = load_config()
store = MongoStore(config.mongodb)
evaluator = Evaluator(persistence=store, telemetry=Telemetry())

poller = AgentPoller(
    persistence=store,
    evaluator=evaluator,
    config=AgentPollerConfig(service_name="my-agent")
)

# Register sync handlers for event facets
poller.register("ns.ProcessData", lambda data: {"output": process(data)})
poller.register("ns.FetchUrl", lambda data: {"content": fetch(data["url"])})

# Register async handlers (for LLM integrations, etc.)
async def llm_handler(params: dict) -> dict:
    response = await call_llm_api(params["prompt"])
    return {"response": response}

poller.register_async("ns.LLMQuery", llm_handler)

# Update step with partial results (for streaming)
poller.update_step(step_id, {"partial": "streaming data..."})

# Start polling (blocking)
poller.start()
```

### Scala

The Scala library (`agents/scala/afl-agent/`) provides the same polling pattern as a standalone sbt library. It delegates workflow resumption to the Python RunnerService via `afl:resume` tasks — no evaluator or AST parsing is needed in Scala.

**Setup:**

```bash
cd agents/scala/afl-agent
sbt compile
```

**Usage:**

```scala
import afl.agent.{AgentPoller, AgentPollerConfig}

val config = AgentPollerConfig(
  serviceName = "my-scala-agent",
  mongoUrl = sys.env.getOrElse("AFL_MONGODB_URL", "mongodb://localhost:27017"),
  database = "afl"
)

val poller = AgentPoller(config)

// Register handlers — receive step params, return result attributes
poller.register("ns.ProcessData") { params =>
  val input = params("input").toString
  Map("output" -> doWork(input))
}

poller.register("ns.FetchUrl") { params =>
  val url = params("url").toString
  Map("content" -> fetch(url), "status" -> 200)
}

// Start polling (blocking) — or use pollOnce() for testing
poller.start()
```

**Configuration from `afl.config.json`:**

```scala
// Resolve config using standard search order (--config, AFL_CONFIG, cwd, ~/.afl/, /etc/afl/)
val config = AgentPollerConfig.resolve()

// Or load from an explicit path
val config = AgentPollerConfig.fromConfig("/path/to/afl.config.json")
```

**Testing with `pollOnce()`:**

```scala
val poller = AgentPoller(config)
poller.register("MyEvent") { params => Map("result" -> "ok") }

// Single synchronous poll cycle — returns number of tasks dispatched
val dispatched = poller.pollOnce()
```

**Build and test:**

```bash
cd agents/scala/afl-agent
sbt compile    # compile
sbt test       # run 42 tests
sbt package    # build JAR
```

### Go

The Go library (`agents/go/afl-agent/`) provides a native Go agent implementation with goroutine-based concurrency.

**Setup:**

```bash
cd agents/go/afl-agent
go build ./...
```

**Usage:**

```go
package main

import (
    "context"
    aflagent "github.com/agentflow/afl-agent"
)

func main() {
    cfg := aflagent.ResolveConfig("")
    poller := aflagent.NewAgentPoller(cfg)

    // Register handlers
    poller.Register("ns.ProcessData", func(params map[string]interface{}) (map[string]interface{}, error) {
        input := params["input"].(string)
        return map[string]interface{}{"output": process(input)}, nil
    })

    // Start polling (blocking)
    ctx := context.Background()
    poller.Start(ctx)
}
```

**Build and test:**

```bash
cd agents/go/afl-agent
go build ./...   # compile
go test ./...    # run tests
```

### TypeScript

The TypeScript library (`agents/typescript/afl-agent/`) provides an async/await-based agent implementation for Node.js.

**Setup:**

```bash
cd agents/typescript/afl-agent
npm install
npm run build
```

**Usage:**

```typescript
import { AgentPoller, resolveConfig, Handler } from "@afl/agent";

const config = resolveConfig();
const poller = new AgentPoller(config);

const processHandler: Handler = async (params) => {
    const input = params.input as string;
    return { output: await process(input) };
};

poller.register("ns.ProcessData", processHandler);

// Start polling
await poller.start();
```

**Build and test:**

```bash
cd agents/typescript/afl-agent
npm run build   # compile TypeScript
npm test        # run Jest tests
```

### Java

The Java library (`agents/java/afl-agent/`) provides a Java 17+ agent implementation with ExecutorService-based concurrency.

**Setup:**

```bash
cd agents/java/afl-agent
mvn compile
```

**Usage:**

```java
import afl.agent.AgentPoller;
import afl.agent.AgentPollerConfig;
import afl.agent.Handler;

public class MyAgent {
    public static void main(String[] args) throws Exception {
        AgentPollerConfig config = AgentPollerConfig.resolve(null);
        AgentPoller poller = new AgentPoller(config);

        // Register handlers
        poller.register("ns.ProcessData", params -> {
            String input = (String) params.get("input");
            return Map.of("output", process(input));
        });

        // Start polling (blocking)
        poller.start();
    }
}
```

**Build and test:**

```bash
cd agents/java/afl-agent
mvn compile    # compile
mvn test       # run JUnit tests
mvn package    # build JAR
```

### Building Agents in Other Languages

Any language with a MongoDB driver can implement an AFL agent. See `agents/protocol/constants.json` for the complete protocol specification, including:

- Collection names (`steps`, `tasks`, `servers`)
- Task and step state constants
- Document schemas with exact field names
- MongoDB operation patterns (claim, update returns, create resume task)

The key operations are:

```javascript
// 1. Claim a pending task (atomic)
db.tasks.findOneAndUpdate(
  { state: "pending", name: { $in: ["ns.MyEvent"] }, task_list_name: "default" },
  { $set: { state: "running", updated: Date.now() } },
  { returnDocument: "after" }
)

// 2. Read step params
db.steps.findOne({ uuid: task.step_id })
// → access doc.attributes.params.<name>.{name, value, type_hint}

// 3. Write return attributes
db.steps.updateOne(
  { uuid: task.step_id, state: "state.facet.execution.EventTransmit" },
  { $set: { "attributes.returns.output": { name: "output", value: "result", type_hint: "String" } } }
)

// 4. Mark task completed + insert afl:resume
db.tasks.replaceOne({ uuid: task.uuid }, { ...task, state: "completed" })
db.tasks.insertOne({
  uuid: newUUID(), name: "afl:resume", state: "pending",
  step_id: task.step_id, workflow_id: task.workflow_id,
  data: { step_id: task.step_id, workflow_id: task.workflow_id },
  task_list_name: "default", data_type: "resume", ...
})
```

### Starting a new agent in a separate repo

If your agent lives in its own git repository, copy the template files to give Claude (or any developer) full context on the AFL agent protocol:

```bash
cp agents/templates/CLAUDE.md /path/to/my-agent/CLAUDE.md
cp agents/protocol/constants.json /path/to/my-agent/constants.json
```

The `CLAUDE.md` template is self-contained — it documents the complete protocol, MongoDB schemas, operations, configuration, type hints, and existing library examples. Start Claude Code in your agent directory and it will have everything it needs.

## Documentation

> **Note:** For the authoritative specifications, see [The Contract: Specifications](#the-contract-specifications) at the top of this document.

### Guides

| Document | Description |
|----------|-------------|
| [docs/architecture.md](docs/architecture.md) | System architecture — compiler, runtime, persistence, agent dispatch, and component interactions |
| [docs/tutorial.md](docs/tutorial.md) | AFL tutorial — progressive walkthrough of all language constructs with examples |
| [docs/deployment.md](docs/deployment.md) | Deployment and operations — Docker, configuration, monitoring, HDFS, PostGIS, Jenkins |

### OSM Geocoder Documentation

The `examples/osm-geocoder/` directory contains detailed documentation for the OSM data processing agent:

| Document | Description |
|----------|-------------|
| [examples/osm-geocoder/README.md](examples/osm-geocoder/README.md) | Overview, setup, running instructions, and file index |
| [examples/osm-geocoder/CACHE.md](examples/osm-geocoder/CACHE.md) | Cache system — OSMCache schema, 11 geographic namespaces, region registry, Geofabrik URL mapping |
| [examples/osm-geocoder/DOWNLOADS.md](examples/osm-geocoder/DOWNLOADS.md) | Downloads and operations — 13 event facets, downloader API, format support, caching behavior |
| [examples/osm-geocoder/POI.md](examples/osm-geocoder/POI.md) | POI extraction — 8 event facets, handler dispatch, return parameter grouping |
| [examples/osm-geocoder/SHAPEFILES.md](examples/osm-geocoder/SHAPEFILES.md) | Shapefile downloads — Geofabrik free shapefiles, handler flow, availability constraints |

### Agent Protocol

| Document | Description |
|----------|-------------|
| [agents/protocol/README.md](agents/protocol/README.md) | Cross-language agent protocol documentation |
| [agents/protocol/constants.json](agents/protocol/constants.json) | Shared protocol constants (collection names, states, schemas, MongoDB operations) |
| [agents/templates/CLAUDE.md](agents/templates/CLAUDE.md) | Drop-in CLAUDE.md template for new agent repositories |

## Examples

See [examples/README.md](examples/README.md) for a complete overview of all 14 examples with feature matrices and complexity progression.

### OSM Geocoder

A full-scale example agent demonstrating schemas, event facets, namespaced workflows, and the AgentPoller library. Located in `examples/osm-geocoder/` — see the [documentation section](#osm-geocoder-documentation) above for detailed guides.

**AFL sources** (17 files):
- `osmtypes.afl` — `OSMCache` schema used across all OSM facets
- `osmcache.afl` — ~250 cache event facets across 11 geographic namespaces
- `osmoperations.afl` — 13 data processing event facets (Download, Tile, RoutingGraph, Status, GeoOSMCache, PostGisImport, DownloadShapefile, plus *All variants)
- `osmpoi.afl` — 8 POI extraction event facets
- `osmafrica.afl` through `osmworld.afl` — regional workflows composing cache lookups with download operations
- `osmshapefiles.afl` — Europe shapefile download workflow

**Python handlers** (`handlers/` package):
- `cache_handlers.py` — ~250 handlers with Geofabrik download URL registry
- `operations_handlers.py` — 13 handlers for OSM data processing operations (including shapefile downloads)
- `poi_handlers.py` — 8 handlers for point-of-interest extraction
- `__init__.py` — `register_all_handlers(poller)` convenience function

```bash
# Run offline test (no network)
python examples/osm-geocoder/test_geocoder.py

# Start the live agent
python examples/osm-geocoder/agent.py

# Check all AFL sources parse
for f in examples/osm-geocoder/afl/*.afl; do afl "$f" --check; done
```

### Genomics Cohort Analysis

Bioinformatics pipeline demonstrating **foreach fan-out** (per-sample QC, alignment, variant calling) and **linear fan-in** (joint genotyping, annotation, publishing). Located in `examples/genomics/` — see [examples/genomics/README.md](examples/genomics/README.md).

**AFL sources** (7 files):
- `genomics.afl` — 8 schemas, 9 event facets, 2 workflows (SamplePipeline, CohortAnalysis)
- `genomics_cache.afl` — 17 per-entity cache event facets (reference genomes, annotations, SRA archives)
- `genomics_index_cache.afl` — 10 aligner index event facets (BWA, STAR, Bowtie2)
- `genomics_resolve.afl` — 4 name-based resource resolution facets
- `genomics_operations.afl` — 5 low-level cache operations
- `genomics_cache_types.afl` — cache layer schemas
- `genomics_cache_workflows.afl` — 3 cache-aware workflows (PrepareReference, PrepareSample, CachedCohortAnalysis)

**Python handlers** (5 modules, 45 dispatch keys):
- `genomics_handlers.py` — 9 core pipeline handlers
- `cache_handlers.py` — 17 factory-built cache handlers
- `index_handlers.py` — 10 aligner index handlers
- `resolve_handlers.py` — 4 name resolution handlers with alias maps
- `operations_handlers.py` — 5 low-level cache operations

```bash
# Start the agent
PYTHONPATH=. python examples/genomics/agent.py

# RegistryRunner mode
AFL_USE_REGISTRY=1 PYTHONPATH=. python examples/genomics/agent.py
```

### Jenkins CI/CD Pipelines

CI/CD pipeline example showcasing AFL's **mixin composition** — small reusable facets (Retry, Timeout, Credentials, Notification, AgentLabel, Stash) composed onto event facets at both signature and call time. Located in `examples/jenkins/` — see [examples/jenkins/README.md](examples/jenkins/README.md).

**AFL sources** (9 files):
- `jenkins_types.afl` — 7 schemas (ScmInfo, BuildResult, TestReport, QualityReport, Artifact, DeployResult, PipelineStatus)
- `jenkins_mixins.afl` — 6 mixin facets + 3 implicit defaults
- `jenkins_scm.afl` through `jenkins_notify.afl` — 17 event facets across 6 domain namespaces
- `jenkins_pipelines.afl` — 4 workflows (JavaMavenCI, DockerK8sDeploy, MultiModuleBuild, FullCIPipeline)

**Python handlers** (6 modules, 17 dispatch keys):
- `scm_handlers.py` — GitCheckout, GitMerge
- `build_handlers.py` — MavenBuild, GradleBuild, NpmBuild, DockerBuild
- `test_handlers.py` — RunTests, CodeQuality, SecurityScan
- `artifact_handlers.py` — ArchiveArtifacts, PublishToRegistry, DockerPush
- `deploy_handlers.py` — DeployToEnvironment, DeployToK8s, RollbackDeploy
- `notify_handlers.py` — SlackNotify, EmailNotify

```bash
# Start the agent
PYTHONPATH=. python examples/jenkins/agent.py

# RegistryRunner mode
AFL_USE_REGISTRY=1 PYTHONPATH=. python examples/jenkins/agent.py
```

### AWS Lambda + Step Functions

Serverless pipeline example with **real boto3 calls** against a **LocalStack** Docker environment. Demonstrates andThen chains, call-time mixin composition, cross-namespace workflows, and foreach iteration. Located in `examples/aws-lambda/` — see [examples/aws-lambda/README.md](examples/aws-lambda/README.md).

**AFL sources** (5 files):
- `lambda_types.afl` — 7 schemas (FunctionConfig, InvokeResult, FunctionInfo, LayerInfo, StateMachineConfig, ExecutionResult, ExecutionInfo)
- `lambda_mixins.afl` — 6 mixin facets + 3 implicit defaults (Retry, Timeout, DLQ, VpcConfig, Tracing, MemorySize)
- `lambda_functions.afl` — 7 Lambda event facets (CreateFunction, InvokeFunction, etc.)
- `lambda_stepfunctions.afl` — 5 Step Functions event facets (CreateStateMachine, StartExecution, etc.)
- `lambda_workflows.afl` — 4 workflows (DeployAndInvoke, BlueGreenDeploy, StepFunctionPipeline, BatchProcessor)

**Python handlers** (2 modules, 12 dispatch keys):
- `lambda_handlers.py` — 7 handlers with real boto3 Lambda calls
- `stepfunctions_handlers.py` — 5 handlers with real boto3 Step Functions calls

```bash
# Start LocalStack
docker compose --profile localstack up -d

# Start the agent
LOCALSTACK_URL=http://localhost:4566 PYTHONPATH=. python examples/aws-lambda/agent.py

# RegistryRunner mode
AFL_USE_REGISTRY=1 LOCALSTACK_URL=http://localhost:4566 PYTHONPATH=. python examples/aws-lambda/agent.py
```

## Scripts

Executable convenience scripts in `scripts/` — no `PYTHONPATH` setup needed:

```bash
# Compile AFL source
scripts/compile input.afl -o output.json
scripts/compile input.afl --check            # syntax check only
echo 'facet Foo()' | scripts/compile         # pipe from stdin

# Compile + publish to MongoDB
scripts/publish input.afl                    # compile and publish
scripts/publish input.afl --version 1.0.0    # with version tag
scripts/publish input.afl --auto-resolve --source-path ./libs

# Run a workflow from MongoDB (interactive)
scripts/run-workflow                         # list → pick → prompt → run
scripts/run-workflow --list                  # list available workflows
scripts/run-workflow --workflow MyWorkflow    # prompt for params
scripts/run-workflow --workflow MyWorkflow --input '{"x": 1}'

# Execute a workflow from MongoDB (server mode)
scripts/server --workflow MyWorkflow
scripts/server --flow-id ID --input '{"x": 1}'

# Start the distributed runner service
scripts/runner                               # default settings
scripts/runner --topics TopicA --max-concurrent 10

# Start the web dashboard
scripts/dashboard                            # default port 8080
scripts/dashboard --port 9000 --reload       # dev mode

# Start the MCP server
scripts/mcp-server
scripts/mcp-server --config ./my-config.json

# Database statistics
scripts/db-stats                             # human-readable output
scripts/db-stats --json                      # JSON output
scripts/db-stats --config ./my-config.json   # custom config
```

All scripts support `--help` for full usage details.

## Language Overview

### Facets

Basic data structures:

```
facet User(name: String, email: String)
facet Config(debug: Boolean, maxRetries: Int)
```

### Schemas

Named typed structures for defining JSON shapes. Schema names can be used as types in parameter signatures:

```
schema UserRequest {
    name: String
    age: Int
    tags: [String]
}

schema UserResponse {
    id: String
    user: UserRequest
}

event facet CreateUser(request: UserRequest) => (user: UserResponse)
```

Array types `[Type]` are supported in schema fields and regular parameter signatures:

```
facet BatchProcess(items: [String], counts: [Int])

schema Matrix {
    rows: [[Int]]
}
```

Schemas can also appear inside namespaces:

```
namespace app.models {
    schema Config {
        host: String
        port: Int
        debug: Boolean
    }

    facet LoadConfig() => (config: Config)
}
```

### Schema Instantiation in Steps

Schemas can be instantiated like function calls within workflow steps. Schema fields are stored as step returns, making them accessible via `step.fieldName`:

```
schema Config {
    timeout: Long
    retries: Long
}

event facet DoSomething(timeout: Long, retries: Long) => (result: String)

workflow Example() => (output: String) andThen {
    cfg = Config(timeout = 30, retries = 3)
    result = DoSomething(timeout = cfg.timeout, retries = cfg.retries)
    yield Example(output = result.result)
}
```

Schema instantiation follows simplified state transitions (no event dispatch or block execution) and completes synchronously. This is useful for:
- Grouping related configuration values
- Creating structured data to pass to event facets
- Improving workflow readability

**Validation rules for schema instantiation:**
- All arguments must be valid schema fields
- Unknown fields produce validation errors
- Schemas cannot have mixins (`Config() with Mixin()` is invalid)

### Event Facets

Facets that trigger agent execution:

```
event facet ProcessData(input: String) => (output: String)
```

### Prompt Templates

Event facets can use `prompt` blocks for LLM-based handlers:

```
event facet Summarize(document: String) => (summary: String)
prompt {
    system "You are a document summarizer."
    template "Summarize the following document:\n\n{document}"
    model "claude-3-opus"
}
```

The `template` directive is required. Placeholders `{param_name}` must match facet parameters. The `system` and `model` directives are optional.

### Script Blocks

Facets can include inline Python scripts for simple transformations:

```
facet Transform(data: String) => (output: String)
script {
    python """
import json
parsed = json.loads(params["data"])
result["output"] = parsed["value"].upper()
"""
}
```

Scripts execute in a sandboxed environment with restricted built-ins (no `import`, `open`, `eval`, `exec`). The `params` dict contains input parameters; set values in the `result` dict for return attributes.

### Workflows

Entry points with execution logic:

```
workflow ProcessAllRegions(regions: Json) => (results: Json) andThen {
    data = FetchData(source = $.regions)
    processed = Transform(input = data.output)
    yield ProcessAllRegions(results = processed.result)
}
```

### Foreach Iteration

Parallel iteration over collections:

```
workflow ProcessItems(items: Json) => (results: Json) andThen foreach item in $.items {
    result = ProcessItem(data = item.value)
    yield ProcessItems(results = result.output)
}
```

### Namespaces

Organize code into modules:

```
namespace team.data.processing {
    uses team.common.utils

    facet DataPoint(x: Long, y: Long)

    workflow Aggregate(points: Json) => (total: Long) andThen {
        sum = Sum(values = $.points)
        yield Aggregate(total = sum.result)
    }
}
```

### Mixins

Compose additional capabilities:

```
facet Job(input: String) with Retry(maxAttempts = 3) with Timeout(seconds = 60)
```

### Default Parameter Values

Parameters can have default values:

```
facet Config(host: String = "localhost", port: Int = 8080, debug: Boolean = false)

workflow ProcessData(input: String = "default") => (output: String = "none") andThen {
    step = Transform(value = $.input)
    yield ProcessData(output = step.result)
}
```

Default values are emitted as a `"default"` key in the JSON AST and are used by the runtime evaluator when no explicit input is provided.

### Implicit Declarations

Default values:

```
implicit defaultUser = User(name = "system", email = "system@example.com")
```

## Project Structure

```
agentflow/
├── afl/                     # AFL compiler package
│   ├── __init__.py          # Package exports
│   ├── ast.py               # AST node dataclasses
│   ├── parser.py            # Lark parser wrapper
│   ├── transformer.py       # Parse tree → AST transformer
│   ├── emitter.py           # AST → JSON emitter
│   ├── validator.py         # Semantic validator
│   ├── source.py            # Source input and provenance
│   ├── loader.py            # Source loaders (file, MongoDB, Maven)
│   ├── config.py            # Configuration (MongoDB, etc.)
│   ├── cli.py               # Command-line interface
│   ├── grammar/
│   │   └── afl.lark         # Lark grammar definition
│   ├── runtime/             # AFL runtime engine
│   │   ├── evaluator.py     # Main iteration loop
│   │   ├── step.py          # StepDefinition and state
│   │   ├── states.py        # State machine definitions
│   │   ├── persistence.py   # Storage protocol
│   │   ├── memory_store.py  # In-memory implementation
│   │   ├── mongo_store.py   # MongoDB persistence
│   │   ├── dependency.py    # Dependency graph from AST
│   │   ├── expression.py    # Expression evaluation
│   │   ├── changers/        # State changers
│   │   ├── handlers/        # State handlers
│   │   └── runner/          # Distributed runner service
│   ├── mcp/                 # MCP server for LLM agents
│   │   ├── server.py        # MCP Server with tools + resources
│   │   ├── serializers.py   # Entity → dict converters
│   │   └── __main__.py      # python -m afl.mcp entry point
│   └── dashboard/           # Web monitoring dashboard
│       ├── app.py           # FastAPI app factory
│       ├── __main__.py      # python -m afl.dashboard entry point
│       ├── dependencies.py  # MongoStore dependency injection
│       ├── filters.py       # Jinja2 template filters
│       ├── helpers.py       # Shared utilities (grouping, categorization)
│       ├── routes/          # Route modules (dashboard_v2, home, runners, flows, etc.)
│       ├── templates/       # Jinja2 templates (v2/workflows/, v2/servers/, legacy)
│       └── static/          # CSS
├── tests/
│   ├── test_parser.py       # Parser tests
│   ├── test_emitter.py      # Emitter tests
│   ├── test_validator.py    # Validator tests
│   ├── test_source.py       # Source input tests
│   ├── test_config.py       # Configuration tests
│   ├── test_cli.py          # CLI tests
│   ├── test_runner_main.py  # Runner CLI entry point tests
│   ├── runtime/             # Runtime tests
│   │   ├── test_evaluator.py# Integration tests
│   │   ├── test_events.py   # Event lifecycle tests
│   │   ├── test_agent_poller_extended.py # AgentPoller edge cases
│   │   └── test_mongo_store.py # MongoDB persistence tests
│   ├── mcp/                 # MCP server tests
│   │   ├── test_server.py   # Tool + resource integration tests
│   │   ├── test_server_extended.py # Extended tool/resource tests
│   │   ├── test_serializers.py # Serializer unit tests
│   │   └── test_store.py    # Store singleton tests
│   └── dashboard/           # Dashboard tests
│       ├── test_app.py      # App creation tests
│       ├── test_filters.py  # Filter unit tests
│       ├── test_routes.py   # Route integration tests
│       ├── test_step_routes.py # Step detail tests
│       ├── test_dependencies.py # Dependency injection tests
│       ├── test_dashboard_v2.py # V2 workflow helpers and routes
│       ├── test_servers_v2.py # V2 server helpers and routes
│       ├── test_template_rendering.py # Navigation and template tests
│       ├── test_workflows.py # Workflow routes
│       ├── test_flow_namespaces.py # Flow namespace tests
│       ├── test_flow_run.py # Flow run tests
│       ├── test_edge_cases.py # Edge case tests
│       └── test_step_tree.py # Step tree view tests
├── agents/                  # Multi-language agent integration libraries
│   ├── protocol/            # Cross-language protocol constants
│   │   ├── constants.json   # Collection names, states, document schemas
│   │   └── README.md        # External agent workflow docs
│   ├── templates/           # Standalone files for new agent repos
│   │   ├── CLAUDE.md        # Drop-in CLAUDE.md with full protocol reference
│   │   └── README.md        # Template usage instructions
│   ├── scala/
│   │   └── afl-agent/       # Scala agent library (sbt, Scala 3.3.x)
│   ├── go/
│   │   └── afl-agent/       # Go agent library (go.mod, goroutine pool)
│   ├── typescript/
│   │   └── afl-agent/       # TypeScript/Node.js agent library (npm, async/await)
│   └── java/
│       └── afl-agent/       # Java agent library (Maven, ExecutorService)
├── examples/                # Example agents and workflows
│   └── osm-geocoder/        # OSM geocoding + ~272 data event handlers
│       ├── afl/             # 16 AFL source files
│       ├── handlers/        # Python event handler modules
│       ├── agent.py         # Live agent (AgentPoller)
│       └── test_geocoder.py # Offline end-to-end test
├── scripts/                 # Convenience scripts
│   ├── compile              # Compile AFL to JSON
│   ├── publish              # Compile + publish to MongoDB
│   ├── run-workflow         # Interactive workflow execution
│   ├── server               # Execute a workflow from MongoDB
│   ├── runner               # Start the distributed runner
│   ├── dashboard            # Start the web dashboard
│   ├── mcp-server           # Start the MCP server
│   └── db-stats             # Database collection statistics
├── spec/                    # Language specifications
├── pyproject.toml           # Project configuration
└── README.md
```

## Terminology

| Term | Definition |
|------|------------|
| **AgentFlow** | The platform for distributed workflow execution |
| **AFL** | Agent Flow Language — the DSL for defining workflows |
| **Facet** | A typed attribute structure; persisted in step state |
| **Step** | An instantiation of a facet in a workflow |
| **Event Facet** | A facet that triggers agent execution |
| **Workflow** | A facet designated as an entry point for execution |
| **Schema** | A named typed structure for defining JSON shapes; usable as a type |
| **Schema Instantiation** | Creating a schema instance in a step; fields become step returns accessible via `step.field` |
| **AFL Agent** | A service that processes event facets by polling MongoDB for tasks, performing work, and signaling the runtime to continue |
| **Agent Integration Library** | Language-specific library for building AFL agents (Python, Scala) |
| **afl:resume** | Protocol task that signals the RunnerService to resume a workflow |

## Types

Built-in types:
- `String` - Text values
- `Int` - 32-bit integers
- `Long` - 64-bit integers
- `Boolean` - true/false
- `Json` - JSON data structures

Array types:
- `[String]` - Array of strings
- `[Int]` - Array of integers
- `[[String]]` - Nested array (array of string arrays)

Custom types via qualified names:
- `team.types.CustomData`

Schema types (defined via `schema` declarations):
- `UserRequest` - References a schema defined in the same scope
- `app.models.Config` - Qualified schema reference

## References

Input parameters: `$.fieldName`
Step outputs: `stepName.outputField`
Nested access: `$.data.nested.field` or `step.output.nested`

## Requirements

- Python 3.11+
- lark >= 1.1.0
- Dashboard (optional): fastapi >= 0.100, uvicorn >= 0.20, jinja2 >= 3.1
- MCP server (optional): mcp >= 1.0
- MongoDB (optional): pymongo >= 4.0

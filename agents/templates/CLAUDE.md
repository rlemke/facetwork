# CLAUDE.md — FFL Agent Project

## What this project is

This is an **FFL Agent** — a service that processes event facets for the Facetwork platform. The Facetwork runtime (Python) manages workflow execution and state machines. This agent handles specific event types by polling MongoDB for tasks, performing work, and signaling the runtime to continue.

## How FFL agents work

When an FFL workflow reaches an **event facet**, the runtime creates a **task** in MongoDB and pauses the step at `EventTransmit` state. This agent picks up that task and processes it.

### Agent lifecycle (6 steps)

1. **Claim** a pending task atomically from the `tasks` collection (`findOneAndUpdate`: pending → running)
2. **Read** the step's input parameters from the `steps` collection
3. **Perform** the work (API calls, data processing, computation, etc.)
4. **Write** return attributes back to the step document
5. **Mark** the event task as `completed`
6. **Insert** an `fw:resume` task so the Python RunnerService resumes the workflow

The Python RunnerService polls for `fw:resume` tasks, validates the step's return attributes, transitions the step past `EventTransmit`, and advances the workflow.

---

## Key concepts

| Term | Definition |
|------|------------|
| **Facetwork** | Platform for distributed workflow execution (compiler + runtime + agents) |
| **FFL** | Facetwork Flow Language — the DSL for defining workflows (`.ffl` files) |
| **Event Facet** | A facet prefixed with `event` that triggers external agent execution |
| **Task** | A claimable work item in the MongoDB `tasks` collection |
| **Step** | A runtime instance of a facet, with params (inputs) and returns (outputs) |
| **fw:resume** | Protocol task that signals the RunnerService to resume a workflow |

### Task states

| State | Meaning |
|-------|---------|
| `pending` | Waiting to be claimed by an agent |
| `running` | Claimed and being processed |
| `completed` | Successfully finished |
| `failed` | Error occurred during processing |

### Relevant step states

| State | Value | Meaning |
|-------|-------|---------|
| EVENT_TRANSMIT | `state.facet.execution.EventTransmit` | Step is blocked, waiting for agent |
| STATEMENT_ERROR | `state.facet.execution.StatementError` | Agent reported an error |
| COMPLETED | `state.facet.completion.Completed` | Step finished successfully |

---

## MongoDB document schemas

### Task document (`tasks` collection)

```json
{
  "uuid": "string — unique task ID",
  "name": "string — event facet name (e.g. 'ns.ProcessData') or 'fw:resume'",
  "runner_id": "string — runner UUID that created the task",
  "workflow_id": "string — workflow UUID",
  "flow_id": "string — flow UUID",
  "step_id": "string — step UUID",
  "state": "string — pending | running | completed | failed | ignored | canceled",
  "created": "int — milliseconds since Unix epoch",
  "updated": "int — milliseconds since Unix epoch",
  "error": "object|null — { message: string } on failure",
  "task_list_name": "string — routing key (default: 'default')",
  "data_type": "string — type discriminator",
  "data": "object|null — task-specific payload"
}
```

### Step document (`steps` collection) — relevant fields

```json
{
  "uuid": "string — unique step ID",
  "workflow_id": "string — parent workflow UUID",
  "state": "string — current step state",
  "facet_name": "string — resolved facet name (may be qualified: 'ns.Facet')",
  "attributes": {
    "params": {
      "<param_name>": {
        "name": "string",
        "value": "any",
        "type_hint": "String | Long | Double | Boolean | List | Map | Any"
      }
    },
    "returns": {
      "<return_name>": {
        "name": "string",
        "value": "any",
        "type_hint": "String | Long | Double | Boolean | List | Map | Any"
      }
    }
  }
}
```

### Server document (`servers` collection)

```json
{
  "uuid": "string — unique server ID",
  "server_group": "string — logical group name",
  "service_name": "string — service identifier",
  "server_name": "string — hostname",
  "server_ips": ["string — IP addresses"],
  "start_time": "int — milliseconds since Unix epoch",
  "ping_time": "int — last heartbeat timestamp (ms)",
  "topics": ["string — event facet names this server handles"],
  "handlers": ["string — registered handler names"],
  "handled": [{ "handler": "string", "handled": 0, "not_handled": 0 }],
  "state": "string — startup | running | shutdown | error",
  "manager": "string",
  "error": "object|null"
}
```

---

## MongoDB operations

### 1. Claim a pending task (atomic)

```javascript
db.tasks.findOneAndUpdate(
  {
    state: "pending",
    name: { $in: ["ns.MyEvent", "ns.OtherEvent"] },
    task_list_name: "default"
  },
  { $set: { state: "running", updated: Date.now() } },
  { returnDocument: "after" }
)
```

### 2. Read step params

```javascript
doc = db.steps.findOne({ uuid: task.step_id })
// Access: doc.attributes.params.<name>.value
// Type hint: doc.attributes.params.<name>.type_hint
```

### 3. Write return attributes

```javascript
db.steps.updateOne(
  {
    uuid: task.step_id,
    state: "state.facet.execution.EventTransmit"
  },
  {
    $set: {
      "attributes.returns.<name>": {
        name: "<name>",
        value: "<result_value>",
        type_hint: "<String|Long|Double|Boolean>"
      }
    }
  }
)
```

### 4. Mark task completed

```javascript
db.tasks.replaceOne(
  { uuid: task.uuid },
  { ...task, state: "completed", updated: Date.now() }
)
```

### 5. Mark task failed (on error)

```javascript
db.tasks.replaceOne(
  { uuid: task.uuid },
  { ...task, state: "failed", updated: Date.now(), error: { message: "error details" } }
)
```

### 6. Insert fw:resume task

```javascript
db.tasks.insertOne({
  uuid: "<generate UUID>",
  name: "fw:resume:" + task.name,  // includes facet name for visibility
  runner_id: "",
  workflow_id: task.workflow_id,
  flow_id: "",
  step_id: task.step_id,
  state: "pending",
  created: Date.now(),
  updated: Date.now(),
  error: null,
  task_list_name: "default",
  data_type: "resume",
  data: {
    step_id: task.step_id,
    workflow_id: task.workflow_id
  }
})
```

---

## Configuration

FFL agents connect to the same MongoDB instance as the Facetwork runtime. Configuration is resolved from:

1. Explicit `--config FILE` argument
2. `AFL_CONFIG` environment variable
3. `afl.config.json` in current directory, `~/.ffl/`, or `/etc/ffl/`
4. Environment variables (see below)
5. Built-in defaults

**`afl.config.json` format:**

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

**Environment variables (recommended for credentials):**

| Variable | Default |
|----------|---------|
| `AFL_MONGODB_URL` | `mongodb://localhost:27017` |
| `AFL_MONGODB_USERNAME` | (empty) |
| `AFL_MONGODB_PASSWORD` | (empty) |
| `AFL_MONGODB_AUTH_SOURCE` | `admin` |
| `AFL_MONGODB_DATABASE` | `afl` |

---

## Type hints

When writing return attributes, use these type hint strings:

| Scala/Java Type | Python Type | Type Hint String |
|-----------------|-------------|-----------------|
| `Boolean` | `bool` | `"Boolean"` |
| `Int`, `Long` | `int` | `"Long"` |
| `Double`, `Float` | `float` | `"Double"` |
| `String` | `str` | `"String"` |
| `List`, `Seq` | `list` | `"List"` |
| `Map` | `dict` | `"Map"` |
| other | other | `"Any"` |

---

## Existing agent libraries

If your agent is written in Python or Scala, you can use the existing libraries instead of implementing the protocol directly:

### Python (AgentPoller)

Available in the Facetwork runtime package (`afl.runtime.agent_poller`):

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

poller.register("ns.MyEvent", lambda data: {"output": process(data)})
poller.start()
```

### Scala (fw-agent library)

Available as an sbt dependency from the Facetwork repo (`agents/scala/fw-agent/`):

```scala
import afl.agent.{AgentPoller, AgentPollerConfig}

val config = AgentPollerConfig(
  serviceName = "my-scala-agent",
  mongoUrl = sys.env.getOrElse("AFL_MONGODB_URL", "mongodb://localhost:27017"),
  database = "afl"
)
val poller = AgentPoller(config)

poller.register("ns.MyEvent") { params =>
  Map("output" -> doWork(params("input").toString))
}
poller.start()
```

### Other languages

Implement the 6-step protocol above using any MongoDB driver. Copy `constants.json` into your project for the exact field names and state values.

---

## Server registration (optional but recommended)

Agents should register themselves in the `servers` collection so the dashboard and other tools can see them. Upsert a server document on startup with `state: "running"`, update `ping_time` periodically (heartbeat), and set `state: "shutdown"` on exit.

---

## How Claude should help with this project

- Implement handlers for specific event facet names
- Follow the 6-step protocol exactly — the field names and state strings must match
- Always use atomic `findOneAndUpdate` for task claiming
- Always filter by `state: "state.facet.execution.EventTransmit"` when writing step returns
- Always insert an `fw:resume` task after completing an event task
- Handle errors by marking the task as `failed` with an error message
- Use `afl.config.json` or environment variables for MongoDB connection settings

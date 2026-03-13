# AFL Database Schema Documentation

This document describes the MongoDB collections used by the AFL event-driven workflow execution engine.

## Database: `afl`

The system uses 8 MongoDB collections to persist workflow definitions, execution state, tasks, and operational data.

---

## Collections Overview

| Collection | Purpose | Key Entity |
|------------|---------|------------|
| `flows` | Workflow definitions (AFL compiled) | `FlowDefinition` |
| `workflows` | Named workflow entry points | `WorkflowDefinition` |
| `runners` | Workflow execution instances | `RunnerDefinition` |
| `steps` | Step execution records | `StepDefinition` |
| `tasks` | Task queue for event dispatch and async operations | `TaskDefinition` |
| `logs` | Audit and execution logs | `LogDefinition` |
| `servers` | Agent/server registration | `ServerDefinition` |

---

## Collection: `flows`

Stores compiled AFL flow definitions including facets, workflows, blocks, and generated code.

**DAO:** `FlowDefinitionDAO`
**Entity:** `FlowDefinition`

### Entity Definition

```python
@dataclass
class FlowIdentity:
    """Flow identification."""
    name: str
    path: str
    uuid: str


@dataclass
class FlowDefinition:
    """Compiled AFL flow definition."""
    uuid: str
    name: FlowIdentity
    namespaces: list[NamespaceDefinition] = field(default_factory=list)
    facets: list[FacetDefinition] = field(default_factory=list)
    workflows: list[WorkflowDefinition] = field(default_factory=list)
    mixins: list[MixinDefinition] = field(default_factory=list)
    blocks: list[BlockDefinition] = field(default_factory=list)
    statements: list[StatementDefinition] = field(default_factory=list)
    arguments: list[StatementArguments] = field(default_factory=list)
    references: list[StatementReferences] = field(default_factory=list)
    script_code: list[ScriptCode] = field(default_factory=list)
    file_artifacts: list[FileArtifact] = field(default_factory=list)
    jar_artifacts: list[JarArtifact] = field(default_factory=list)
    resources: list[ResourceSource] = field(default_factory=list)
    text_sources: list[TextSource] = field(default_factory=list)
    inline: Optional[InlineSource] = None
    classification: Optional[Classifier] = None
    publisher: Optional[UserDefinition] = None
    ownership: Optional[Ownership] = None
    compiled_sources: list[SourceText] = field(default_factory=list)
```

### Fields

| Field | Type | Description |
|-------|------|-------------|
| `uuid` | str | Unique identifier (primary key) |
| `name` | FlowIdentity | Flow identity (nested: `name`, `path`, `uuid`) |
| `namespaces` | list[NamespaceDefinition] | Defined namespaces |
| `facets` | list[FacetDefinition] | Facet definitions |
| `workflows` | list[WorkflowDefinition] | Workflow definitions |
| `mixins` | list[MixinDefinition] | Mixin definitions |
| `blocks` | list[BlockDefinition] | Code blocks |
| `statements` | list[StatementDefinition] | Statements |
| `arguments` | list[StatementArguments] | Statement arguments |
| `references` | list[StatementReferences] | Dependency graph |
| `script_code` | list[ScriptCode] | Generated script code |
| `file_artifacts` | list[FileArtifact] | File artifacts |
| `jar_artifacts` | list[JarArtifact] | JAR artifacts |
| `resources` | list[ResourceSource] | Resource sources |
| `text_sources` | list[TextSource] | Text sources |
| `inline` | Optional[InlineSource] | Inline source code |
| `classification` | Optional[Classifier] | Flow classification |
| `publisher` | Optional[UserDefinition] | Publisher info |
| `ownership` | Optional[Ownership] | Ownership info |
| `compiled_sources` | list[SourceText] | Compiled source code |

### Indexes

| Index Name | Fields | Properties |
|------------|--------|------------|
| `flow_uuid_index` | `uuid` | UNIQUE |
| `flow_path_index` | `name.path` | |
| `flow_name_index` | `name.name` | |
| `flow_name_id_index` | `name.uuid` | |

---

## Collection: `workflows`

Stores workflow entry point definitions that reference flows and starting steps.

**DAO:** `WorkflowDefinitionDAO`
**Entity:** `WorkflowDefinition`

### Entity Definition

```python
@dataclass
class WorkflowDefinition:
    """Named workflow entry point."""
    uuid: str
    name: str
    namespace_id: str
    facet_id: str
    flow_id: str
    starting_step: str
    version: str
    metadata: Optional[WorkflowMetaData] = None
    documentation: Optional[str] = None
    date: int = 0  # Creation timestamp (milliseconds)
```

### Fields

| Field | Type | Description |
|-------|------|-------------|
| `uuid` | str | Unique identifier (primary key) |
| `name` | str | Workflow name |
| `namespace_id` | str | Reference to namespace |
| `facet_id` | str | Reference to facet |
| `flow_id` | str | Reference to parent flow |
| `starting_step` | str | Initial step |
| `version` | str | Version info |
| `metadata` | Optional[WorkflowMetaData] | Optional metadata |
| `documentation` | Optional[str] | Optional documentation |
| `date` | int | Creation timestamp (ms) |

### Indexes

| Index Name | Fields | Properties |
|------------|--------|------------|
| `workflow_uuid_index` | `uuid` | UNIQUE |
| `workflow_name_index` | `name` | |
| `workflow_flow_id_index` | `flow_id` | |

---

## Collection: `runners`

Stores workflow execution instances with runtime state and parameters.

**DAO:** `RunnerDefinitionDAO`
**Entity:** `RunnerDefinition`

### Entity Definition

```python
@dataclass
class RunnerDefinition:
    """Workflow execution instance."""
    uuid: str
    workflow_id: str
    workflow: WorkflowDefinition
    parameters: list[Parameter] = field(default_factory=list)
    step_id: str | None = None
    user: UserDefinition | None = None
    start_time: int = 0  # Execution start timestamp (ms)
    end_time: int = 0    # Execution end timestamp (ms)
    duration: int = 0    # Total execution duration (ms)
    retain: int = 0      # Retention period (ms)
    state: str = RunnerState.CREATED
    compiled_ast: dict | None = None  # Snapshotted program AST at workflow start
    workflow_ast: dict | None = None  # Snapshotted workflow node AST at workflow start
```

### Fields

| Field | Type | Description |
|-------|------|-------------|
| `uuid` | str | Unique identifier (primary key) |
| `workflow_id` | str | Reference to workflow |
| `workflow` | WorkflowDefinition | Embedded workflow definition |
| `parameters` | list[Parameter] | Runtime parameters |
| `step_id` | Optional[str] | Current step |
| `user` | Optional[UserDefinition] | User who initiated |
| `start_time` | int | Execution start timestamp (ms) |
| `end_time` | int | Execution end timestamp (ms) |
| `duration` | int | Total execution duration (ms) |
| `retain` | int | Retention period (ms) |
| `state` | str | Runner state |
| `compiled_ast` | dict \| None | Snapshotted full program AST at workflow start |
| `workflow_ast` | dict \| None | Snapshotted workflow node AST at workflow start |

### Runner States

- `created` - Runner initialized
- `running` - Workflow executing
- `completed` - Workflow finished successfully
- `failed` - Workflow failed with error
- `paused` - Workflow paused
- `cancelled` - Workflow cancelled
- `unknown` - State unknown

### Indexes

| Index Name | Fields | Properties |
|------------|--------|------------|
| `runner_uuid_index` | `uuid` | UNIQUE |
| `runner_workflow_id_index` | `workflow_id` | |
| `runner_flow_id_index` | `flow_id` | |
| `runner_state_index` | `state` | |

---

## Collection: `steps`

Stores step execution records tracking individual workflow step states.

**DAO:** `StepDefinitionDAO`
**Entity:** `StepDefinition`

### Entity Definition

```python
@dataclass
class StepDefinition:
    """Step execution record."""
    uuid: str
    object_type: str
    runner_id: str
    workflow_id: str
    statement_id: Optional[str] = None
    container_type: Optional[str] = None
    container_id: Optional[str] = None
    root_id: Optional[str] = None
    block_id: Optional[str] = None
    timestamp: Optional[FacetRecordTimestamp] = None
    facets: Optional[Facets] = None
    flow_definition_id: Optional[str] = None
    flow_definition_json: Optional[dict] = None
    extra_data: Optional[ExtraStepData] = None
    lock_status: str = ""  # "" or "locked"
    is_starting_step: bool = False
    parameters: list[Parameter] = field(default_factory=list)
    state: str = ""
    error: Optional[Error] = None
```

### Fields

| Field | Type | Description |
|-------|------|-------------|
| `uuid` | str | Unique identifier (primary key) |
| `object_type` | str | Step type |
| `runner_id` | str | Reference to runner |
| `workflow_id` | str | Reference to workflow |
| `statement_id` | Optional[str] | Reference to statement |
| `container_type` | Optional[str] | Container type |
| `container_id` | Optional[str] | Parent step ID |
| `root_id` | Optional[str] | Root step ID |
| `block_id` | Optional[str] | Block ID |
| `timestamp` | Optional[FacetRecordTimestamp] | Timestamp info |
| `facets` | Optional[Facets] | Facet data |
| `flow_definition_id` | Optional[str] | Flow definition ID |
| `flow_definition_json` | Optional[dict] | Flow definition JSON |
| `extra_data` | Optional[ExtraStepData] | Extra step data |
| `lock_status` | str | Lock status (`""` or `"locked"`) |
| `is_starting_step` | bool | Whether this is the start step |
| `parameters` | list[Parameter] | Step parameters |
| `state` | str | Step state |
| `error` | Optional[Error] | Error details |

### Object Types

- `VariableAssignment` - Variable assignment step
- `Facet` - Facet invocation
- `AndThen` - Sequential execution
- `AndMap` - Parallel execution
- `AndMatch` - Conditional branching

### Indexes

| Index Name | Fields | Properties |
|------------|--------|------------|
| `step_uuid_index` | `uuid` | UNIQUE |
| `step_workflow_id_index` | `workflow_id` | |
| `step_flow_id_index` | `flow_id` | |
| `step_state_index` | `state` | |

---

## Collection: `tasks`

Task queue for asynchronous workflow operations.

**DAO:** `TaskDefinitionDAO`
**Entity:** `TaskDefinition`

### Entity Definition

```python
@dataclass
class TaskDefinition:
    """Async task queue entry."""
    uuid: str
    name: str
    runner_id: str
    workflow_id: str
    flow_id: str
    step_id: str
    state: str = "pending"
    created: int = 0   # Creation timestamp (ms)
    updated: int = 0   # Last updated timestamp (ms)
    error: Optional[dict] = None
    task_list_name: str = "default"
    data_type: str = ""
    data: Optional[dict] = None
```

### Fields

| Field | Type | Description |
|-------|------|-------------|
| `uuid` | str | Unique identifier (primary key) |
| `name` | str | Task name |
| `runner_id` | str | Reference to runner |
| `workflow_id` | str | Reference to workflow |
| `flow_id` | str | Reference to flow |
| `step_id` | str | Reference to step |
| `state` | str | Task state |
| `created` | int | Creation timestamp (ms) |
| `updated` | int | Last updated timestamp (ms) |
| `error` | Optional[dict] | Error details |
| `task_list_name` | str | Task list identifier (default: `"default"`) |
| `data_type` | str | Data type identifier |
| `data` | Optional[dict] | Task data |

### Task States

- `pending` - Awaiting execution
- `running` - Currently executing
- `completed` - Successfully completed
- `failed` - Failed with error
- `ignored` - Skipped
- `canceled` - Cancelled

### Indexes

| Index Name | Fields | Properties |
|------------|--------|------------|
| `task_uuid_index` | `uuid` | UNIQUE |
| `task_runner_id_index` | `runner_id` | |
| `task_step_id_index` | `step_id` | |
| `task_list_name_index` | `task_list_name` | |
| `task_state_index` | `state` | |
| `task_step_id_running_unique_index` | `step_id` | UNIQUE, PARTIAL (state="running") |

The partial unique index ensures only one task per step can be in "running" state.

---

## Collection: `logs`

Audit and execution logging.

**DAO:** `LogDefinitionDAO`
**Entity:** `LogDefinition`

### Entity Definition

```python
@dataclass
class LogDefinition:
    """Audit and execution log entry."""
    uuid: str
    order: int
    runner_id: str
    step_id: Optional[str] = None
    object_id: str = ""
    object_type: str = ""
    note_originator: str = "workflow"  # workflow, agent
    note_type: str = "info"           # error, info, warning
    note_importance: int = 5          # 1=high, 5=normal, 10=low
    message: str = ""
    state: str = ""
    line: int = 0
    file: str = ""
    details: dict = field(default_factory=dict)
    time: int = 0  # Log timestamp (ms)
```

### Fields

| Field | Type | Description |
|-------|------|-------------|
| `uuid` | str | Unique identifier (primary key) |
| `order` | int | Log sequence number |
| `runner_id` | str | Reference to runner |
| `note_originator` | str | Log originator |
| `note_type` | str | Log type |
| `note_importance` | int | Importance level |
| `step_id` | Optional[str] | Reference to step |
| `object_id` | str | ID of logged object |
| `object_type` | str | Type of logged object |
| `message` | str | Log message |
| `state` | str | Current state |
| `line` | int | Source file line number |
| `file` | str | Source file path |
| `details` | dict | JSON details |
| `time` | int | Log timestamp (ms) |

### Note Types

- `error` - Error log
- `info` - Informational log
- `warning` - Warning log

### Note Originators

- `workflow` - From workflow execution
- `agent` - From agent processing

### Importance Levels

- `1` - High importance
- `5` - Normal (note)
- `10` - Low importance

### Indexes

| Index Name | Fields | Properties |
|------------|--------|------------|
| `log_uuid_index` | `uuid` | UNIQUE |
| `log_runner_id_index` | `runner_id` | |
| `log_object_id_index` | `object_id` | |

---

## Collection: `servers`

Agent and server registration for distributed processing.

**DAO:** `ServerDefinitionDAO`
**Entity:** `ServerDefinition`

### Entity Definition

```python
@dataclass
class ServerDefinition:
    """Agent/server registration."""
    uuid: str
    server_group: str
    service_name: str
    server_name: str
    server_ips: list[str] = field(default_factory=list)
    start_time: int = 0   # Server start timestamp (ms)
    ping_time: int = 0    # Last ping timestamp (ms)
    topics: list[str] = field(default_factory=list)
    handlers: list[str] = field(default_factory=list)
    handled: list[HandledCount] = field(default_factory=list)
    state: str = "startup"
    manager: str = ""
    error: Optional[dict] = None


@dataclass
class HandledCount:
    """Event handling statistics."""
    handler: str
    handled: int = 0
    not_handled: int = 0
```

### Fields

| Field | Type | Description |
|-------|------|-------------|
| `uuid` | str | Unique identifier (primary key) |
| `server_group` | str | Server group name |
| `service_name` | str | Service name |
| `server_name` | str | Hostname |
| `server_ips` | list[str] | List of IP addresses |
| `start_time` | int | Server start timestamp (ms) |
| `ping_time` | int | Last ping timestamp (ms) |
| `topics` | list[str] | Kafka topics handled |
| `handlers` | list[str] | Event handlers |
| `handled` | list[HandledCount] | Handled event counts |
| `state` | str | Server state |
| `manager` | str | Manager identifier |
| `error` | Optional[dict] | Error details |

### Server States

- `startup` - Server starting
- `running` - Server active
- `shutdown` - Server shutting down
- `error` - Server in error state

### Indexes

| Index Name | Fields | Properties |
|------------|--------|------------|
| `server_uuid_index` | `uuid` | UNIQUE |

---

---

## Entity Relationships

```
+------------------------------------------------------------------+
|                         FlowDefinition                            |
|  (Contains workflow definitions, facets, blocks, statements)      |
+-----------------------------+------------------------------------+
                              | 1:N
                              v
+------------------------------------------------------------------+
|                      WorkflowDefinition                           |
|            (Named entry point into a flow)                        |
+-----------------------------+------------------------------------+
                              | 1:N
                              v
+------------------------------------------------------------------+
|                       RunnerDefinition                            |
|              (Workflow execution instance)                        |
+-----------------------------+------------------------------------+
                              | 1:N
          +-------------------+-------------------+-----------------+
          v                                       v                 v
+-----------------+                      +-----------+     +---------+
|  StepDefinition |                      |   Task    |     |   Log   |
|  (Step record)  |                      | Definition|     |Definition|
+-----------------+                      +-----------+     +---------+
```

## Query Patterns

### Common Query Field Paths

All reference IDs use dot notation for nested objects:

| Query | Field Path |
|-------|------------|
| Runner ID | `runner_id` |
| Workflow ID | `workflow_id` |
| Flow ID | `flow_id` |
| Step ID | `step_id` |
| User email | `user.email` |
| Flow path | `name.path` |
| Flow name | `name.name` |

### DataServices Protocol

The `DataServices` protocol provides access to all DAOs:

```python
@runtime_checkable
class DataServices(Protocol):
    """Protocol providing access to all DAOs."""

    @property
    def step(self) -> StepDefinitionDAO:
        """Step execution records."""
        ...

    @property
    def flow(self) -> FlowDefinitionDAO:
        """Flow definitions."""
        ...

    @property
    def workflow(self) -> WorkflowDefinitionDAO:
        """Workflow definitions."""
        ...

    @property
    def runner(self) -> RunnerDefinitionDAO:
        """Execution instances."""
        ...

    @property
    def task(self) -> TaskDefinitionDAO:
        """Task queue."""
        ...

    @property
    def log(self) -> LogDefinitionDAO:
        """Audit logs."""
        ...

    @property
    def server(self) -> ServerDefinitionDAO:
        """Server registration."""
        ...

```

### DAO Protocol Definitions

```python
@runtime_checkable
class FlowDefinitionDAO(Protocol):
    """Data access for flows collection."""

    def get_by_id(self, uuid: str) -> Optional[FlowDefinition]:
        """Get flow by UUID."""
        ...

    def get_by_path(self, path: str) -> Optional[FlowDefinition]:
        """Get flow by path."""
        ...

    def get_by_name(self, name: str) -> Optional[FlowDefinition]:
        """Get flow by name."""
        ...

    def save(self, flow: FlowDefinition) -> None:
        """Save or update a flow."""
        ...

    def delete(self, uuid: str) -> bool:
        """Delete a flow by UUID."""
        ...


@runtime_checkable
class WorkflowDefinitionDAO(Protocol):
    """Data access for workflows collection."""

    def get_by_id(self, uuid: str) -> Optional[WorkflowDefinition]:
        """Get workflow by UUID."""
        ...

    def get_by_name(self, name: str) -> Optional[WorkflowDefinition]:
        """Get workflow by name."""
        ...

    def get_by_flow(self, flow_id: str) -> Sequence[WorkflowDefinition]:
        """Get all workflows for a flow."""
        ...

    def save(self, workflow: WorkflowDefinition) -> None:
        """Save or update a workflow."""
        ...


@runtime_checkable
class RunnerDefinitionDAO(Protocol):
    """Data access for runners collection."""

    def get_by_id(self, uuid: str) -> Optional[RunnerDefinition]:
        """Get runner by UUID."""
        ...

    def get_by_workflow(self, workflow_id: str) -> Sequence[RunnerDefinition]:
        """Get all runners for a workflow."""
        ...

    def get_by_state(self, state: str) -> Sequence[RunnerDefinition]:
        """Get runners by state."""
        ...

    def save(self, runner: RunnerDefinition) -> None:
        """Save or update a runner."""
        ...

    def update_state(self, uuid: str, state: str) -> None:
        """Update runner state."""
        ...


@runtime_checkable
class TaskDefinitionDAO(Protocol):
    """Data access for tasks collection."""

    def get_by_id(self, uuid: str) -> Optional[TaskDefinition]:
        """Get task by UUID."""
        ...

    def get_pending(self, task_list: str) -> Sequence[TaskDefinition]:
        """Get pending tasks for a task list."""
        ...

    def save(self, task: TaskDefinition) -> None:
        """Save or update a task."""
        ...

    def update_state(self, uuid: str, state: str) -> None:
        """Update task state."""
        ...


@runtime_checkable
class LogDefinitionDAO(Protocol):
    """Data access for logs collection."""

    def get_by_runner(self, runner_id: str) -> Sequence[LogDefinition]:
        """Get logs for a runner."""
        ...

    def save(self, log: LogDefinition) -> None:
        """Save a log entry."""
        ...


@runtime_checkable
class ServerDefinitionDAO(Protocol):
    """Data access for servers collection."""

    def get_by_id(self, uuid: str) -> Optional[ServerDefinition]:
        """Get server by UUID."""
        ...

    def get_by_state(self, state: str) -> Sequence[ServerDefinition]:
        """Get servers by state."""
        ...

    def save(self, server: ServerDefinition) -> None:
        """Save or update a server."""
        ...

    def update_ping(self, uuid: str, ping_time: int) -> None:
        """Update server ping time."""
        ...


```

---

## Timestamps

All timestamps are stored as `int` values representing milliseconds since Unix epoch.

| Field Pattern | Description |
|---------------|-------------|
| `created` | Record creation time |
| `updated` | Last modification time |
| `start_time` | Execution start |
| `end_time` | Execution end |
| `time` | Event/log timestamp |
| `ping_time` | Last heartbeat |
| `acquired_at` | Lock acquisition |
| `expires_at` | Lock expiration |

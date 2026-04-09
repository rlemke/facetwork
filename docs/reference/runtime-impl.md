# FFL Runtime Implementation Guide

> This document describes the **Python reference implementation** of the FFL runtime.
> For the formal specification, see [runtime.md](runtime.md).

---

## 1. Compilation Phase

> Implements [compiler.md](compiler.md) and [runtime.md](runtime.md) §2.

FFL source files are compiled by the FFL compiler (`afl/cli.py`):

```
FFL Source → Lark Parser → AST → JSON Emitter → MongoDB / JSON file
```

The compiled output contains:
- **WorkflowDecl** - Named entrypoints with starting steps
- **FacetDecl / EventFacetDecl** - Component templates with typed attributes
- **StepStmt** - Individual operations (statements)
- **AndThenBlock** - Control flow constructs with sequential execution

---

## 2. Iterative Execution

> Implements [runtime.md](runtime.md) §9–10.

The `Evaluator` (`afl/runtime/evaluator.py`) orchestrates execution:

```
Evaluator.run()
    └── iterate() until fixed point
            └── Process each eligible step via StateChanger
                    └── Dispatch to StateHandler per state
```

---

## 3. Step State Machine Overview

> Implements [runtime.md](runtime.md) §6.

Each step follows a state machine defined in `afl/runtime/states.py`:

```
Created
    ↓
FacetInitializationBegin → FacetInitializationEnd
    ↓
FacetScriptsBegin → FacetScriptsEnd
    ↓
MixinBlocksBegin → MixinBlocksContinue → MixinBlocksEnd
    ↓
MixinCaptureBegin → MixinCaptureEnd
    ↓
EventTransmit
    ↓
StatementBlocksBegin → StatementBlocksContinue → StatementBlocksEnd
    ↓
StatementCaptureBegin → StatementCaptureEnd
    ↓
StatementEnd → StatementComplete
```

**Error State:** `StatementError` (terminal state for failures)

### State Constants (Hierarchical Naming)

- **Facet Initialization:** `state.facet.initialization.Begin/End`
- **Facet Scripts:** `state.facet.scripts.Begin/End`
- **Statement Scripts:** `state.statement.scripts.Begin/End`
- **Mixin Blocks:** `state.mixin.blocks.Begin/Continue/End`
- **Mixin Capture:** `state.mixin.capture.Begin/End`
- **Statement Blocks:** `state.statement.blocks.Begin/Continue/End`
- **Block Execution:** `state.block.execution.Begin/Continue/End`
- **Statement Capture:** `state.statement.capture.Begin/End`
- **Completion:** `state.statement.End/Complete`

---

## 4. StateChanger Architecture

> Implements [runtime.md](runtime.md) §6, §8, §9.

### 4.1 StateChanger Base Class

**Location:** `afl/runtime/changers/base.py`

The `StateChanger` drives the state machine in a loop:

```python
class StateChanger(ABC):
    """Abstract base for state machine orchestrators."""

    def __init__(self, step: StepDefinition, context: ExecutionContext):
        self.step = step
        self.context = context

    def process(self) -> StateChangeResult:
        if self.step.is_complete:
            return StateChangeResult(step=self.step, continue_processing=False)

        while True:
            if self.step.is_requesting_state_change:
                next_state = self.select_state()
                if next_state and next_state != self.step.current_state:
                    self.step.change_state(next_state)

            result = self.execute_state(self.step.current_state)
            self.step = result.step

            if self.step.is_terminal:
                return StateChangeResult(step=self.step, continue_processing=False)

            if not self.step.is_requesting_state_change:
                break

        return StateChangeResult(
            step=self.step,
            continue_processing=self.step.transition.is_requesting_push,
        )

    @abstractmethod
    def select_state(self) -> Optional[str]: ...

    @abstractmethod
    def execute_state(self, state: str) -> StateChangeResult: ...
```

### 4.2 StateChanger Types

Three StateChanger implementations handle different step types:

| Type | Handles | State Machine |
|------|---------|---------------|
| `StepStateChanger` | `VariableAssignment` | Full state machine (all states) |
| `BlockStateChanger` | `AndThen`, `AndMap`, `AndMatch` | Simplified: `BlockExecutionBegin → Continue → End` |
| `YieldStateChanger` | `YieldAssignment` | Skips to end after facet initialization |
| Schema steps | `SchemaInstantiation` | Minimal: `Created → FacetInit → End → Complete` (uses `SCHEMA_TRANSITIONS`) |

**Factory function** (in `afl/runtime/evaluator.py`):

```python
def create_state_changer(
    step: StepDefinition, context: ExecutionContext
) -> StateChanger:
    if step.object_type == ObjectType.VARIABLE_ASSIGNMENT:
        return StepStateChanger(step, context)
    elif step.object_type == ObjectType.YIELD_ASSIGNMENT:
        return YieldStateChanger(step, context)
    elif ObjectType.is_block(step.object_type):
        return BlockStateChanger(step, context)
```

### 4.3 Transition Tables

**Location:** `afl/runtime/states.py`

#### Step Transitions (Full State Machine)

```python
class StepStateChanger(StateChanger):
    """State changer for VariableAssignment steps.

    Implements the full state machine with all phases:
    facet initialization, facet scripts, mixin blocks, mixin capture,
    event transmit, statement blocks, statement capture, completion.
    """

    def select_state(self) -> Optional[str]:
        """Select next state using full transition table."""
        current = self.step.current_state
        next_state = STEP_TRANSITIONS.get(current)
        if next_state is None or next_state == current:
            return None
        return next_state

    def execute_state(self, state: str) -> StateChangeResult:
        """Dispatch to the appropriate handler for the current state."""
        handler = get_handler(state, self.step, self.context)
        if handler is None:
            self.step.request_state_change(True)
            return StateChangeResult(step=self.step)
        return handler.process()
```

```python
STEP_TRANSITIONS: dict[str, str] = {
    StepState.CREATED:                  StepState.FACET_INIT_BEGIN,
    StepState.FACET_INIT_BEGIN:         StepState.FACET_INIT_END,
    StepState.FACET_INIT_END:           StepState.FACET_SCRIPTS_BEGIN,
    StepState.FACET_SCRIPTS_BEGIN:      StepState.FACET_SCRIPTS_END,
    StepState.FACET_SCRIPTS_END:        StepState.MIXIN_BLOCKS_BEGIN,
    StepState.MIXIN_BLOCKS_BEGIN:       StepState.MIXIN_BLOCKS_CONTINUE,
    StepState.MIXIN_BLOCKS_CONTINUE:    StepState.MIXIN_BLOCKS_END,
    StepState.MIXIN_BLOCKS_END:         StepState.MIXIN_CAPTURE_BEGIN,
    StepState.MIXIN_CAPTURE_BEGIN:      StepState.MIXIN_CAPTURE_END,
    StepState.MIXIN_CAPTURE_END:        StepState.EVENT_TRANSMIT,
    StepState.EVENT_TRANSMIT:           StepState.STATEMENT_BLOCKS_BEGIN,
    StepState.STATEMENT_BLOCKS_BEGIN:   StepState.STATEMENT_BLOCKS_CONTINUE,
    StepState.STATEMENT_BLOCKS_CONTINUE: StepState.STATEMENT_BLOCKS_END,
    StepState.STATEMENT_BLOCKS_END:     StepState.STATEMENT_CAPTURE_BEGIN,
    StepState.CATCH_BEGIN:              StepState.CATCH_CONTINUE,
    StepState.CATCH_CONTINUE:           StepState.CATCH_END,
    StepState.CATCH_END:                StepState.STATEMENT_CAPTURE_BEGIN,
    StepState.STATEMENT_CAPTURE_BEGIN:  StepState.STATEMENT_CAPTURE_END,
    StepState.STATEMENT_CAPTURE_END:    StepState.STATEMENT_END,
    StepState.STATEMENT_END:            StepState.STATEMENT_COMPLETE,
}
```

#### Yield Transitions (Minimal State Machine)

```python
class YieldStateChanger(StateChanger):
    """State changer for YieldAssignment steps.

    Implements minimal state machine — skips blocks, goes directly
    from facet scripts to statement end.
    """

    def select_state(self) -> Optional[str]:
        current = self.step.current_state
        next_state = YIELD_TRANSITIONS.get(current)
        if next_state is None or next_state == current:
            return None
        return next_state
```

```python
YIELD_TRANSITIONS: dict[str, str] = {
    StepState.CREATED:             StepState.FACET_INIT_BEGIN,
    StepState.FACET_INIT_BEGIN:    StepState.FACET_INIT_END,
    StepState.FACET_INIT_END:      StepState.FACET_SCRIPTS_BEGIN,
    StepState.FACET_SCRIPTS_BEGIN: StepState.FACET_SCRIPTS_END,
    StepState.FACET_SCRIPTS_END:   StepState.STATEMENT_END,   # Skip blocks
    StepState.STATEMENT_END:       StepState.STATEMENT_COMPLETE,
}
```

#### Schema Instantiation Transitions

Schema instantiation steps use a simplified state machine that evaluates arguments and stores them as **returns** (not params). See [runtime.md](runtime.md) §8.5 for the normative semantics.

```python
SCHEMA_TRANSITIONS: dict[str, str] = {
    StepState.CREATED:          StepState.FACET_INIT_BEGIN,
    StepState.FACET_INIT_BEGIN: StepState.FACET_INIT_END,
    StepState.FACET_INIT_END:   StepState.STATEMENT_END,
    StepState.STATEMENT_END:    StepState.STATEMENT_COMPLETE,
}
```

#### Block Transitions

```python
class BlockStateChanger(StateChanger):
    """State changer for block steps (AndThen, AndMap, etc.).

    Simplified state machine: Created → BlockExecution → End → Complete.
    """

    def select_state(self) -> Optional[str]:
        current = self.step.current_state
        next_state = BLOCK_TRANSITIONS.get(current)
        if next_state is None or next_state == current:
            return None
        return next_state
```

```python
BLOCK_TRANSITIONS: dict[str, str] = {
    StepState.CREATED:                    StepState.BLOCK_EXECUTION_BEGIN,
    StepState.BLOCK_EXECUTION_BEGIN:      StepState.BLOCK_EXECUTION_CONTINUE,
    StepState.BLOCK_EXECUTION_CONTINUE:   StepState.BLOCK_EXECUTION_END,
    StepState.BLOCK_EXECUTION_END:        StepState.STATEMENT_END,
    StepState.STATEMENT_END:              StepState.STATEMENT_COMPLETE,
}
```

---

## 5. Transition Control

> Implements [runtime.md](runtime.md) §6 state guarantees.

**Location:** `afl/runtime/step.py`

The `StepTransition` dataclass manages state transitions with control flags:

```python
@dataclass
class StepTransition:
    """Manages state transition control for a step."""
    original_state: str
    current_state: str
    changed: bool = False
    request_transition: bool = False
    push_me: bool = False
    error: Optional[Exception] = None

    def request_state_change(self, request: bool = True) -> None:
        self.request_transition = request
        if request:
            self.changed = True

    def change_and_transition(self) -> None:
        self.changed = True
        self.request_transition = True

    def set_push_me(self, push: bool) -> None:
        self.push_me = push
```

### Transition Methods

| Method | Effect |
|--------|--------|
| `request_state_change()` | Trigger `select_state()` to advance to next state |
| `set_push_me(True)` | Re-queue step for continued processing (polling loop) |
| `change_and_transition()` | Mark changed + request transition |

### Transition Semantics

- **`request_transition`**: When `True`, StateChanger invokes `select_state()` to determine next state
- **`push_me`**: When `True`, step is re-queued for continued processing (loops in same state)
- **`changed`**: Marks step as modified for persistence
- **`error`**: Contains error if step fails

---

## 6. StateHandler Base Class

> Implements [runtime.md](runtime.md) §6 state execution.

**Location:** `afl/runtime/handlers/base.py`

```python
class StateHandler(ABC):
    """Abstract base for state handlers."""

    def __init__(self, step: StepDefinition, context: ExecutionContext):
        self.step = step
        self.context = context

    def process(self) -> StateChangeResult:
        self.context.telemetry.log_state_begin(self.step, self.state_name)
        try:
            result = self.process_state()
            self.context.telemetry.log_state_end(self.step, self.state_name)
            return result
        except Exception as e:
            self.context.telemetry.log_error(self.step, self.state_name, e)
            return StateChangeResult(step=self.step, success=False, error=e)

    @abstractmethod
    def process_state(self) -> StateChangeResult: ...

    def transition(self) -> StateChangeResult:
        """Request transition to next state."""
        self.step.request_state_change(True)
        return StateChangeResult(step=self.step)

    def stay(self, push: bool = False) -> StateChangeResult:
        """Stay in current state, optionally re-queuing."""
        self.step.request_state_change(False)
        self.step.transition.set_push_me(push)
        return StateChangeResult(step=self.step, continue_processing=push)
```

---

## 7. Block Execution Handlers

> Implements [runtime.md](runtime.md) §8, §11.

Blocks (AndThen, AndMap, AndMatch) follow a simplified state machine:

```
Created → BlockExecutionBegin → BlockExecutionContinue (loop) → BlockExecutionEnd → StatementEnd → StatementComplete
```

### BlockExecutionBegin

**Location:** `afl/runtime/handlers/block_execution.py`

```python
class BlockExecutionBeginHandler(StateHandler):
    """Initialize block execution: build dependency graph, create ready steps."""

    def process_state(self) -> StateChangeResult:
        block_ast = self.context.get_block_ast(self.step)
        if block_ast is None:
            self.step.request_state_change(True)
            return StateChangeResult(step=self.step)

        graph = DependencyGraph.from_ast(
            block_ast, self._get_workflow_inputs(),
            program_ast=self.context.program_ast,
        )
        self.context.set_block_graph(self.step.id, graph)
        self._create_ready_steps(graph, completed=set())

        self.step.request_state_change(True)
        return StateChangeResult(step=self.step)
```

### BlockExecutionContinue

**Location:** `afl/runtime/handlers/block_execution.py`

Polls until all child steps complete:

```python
class BlockExecutionContinueHandler(StateHandler):
    """Poll block progress, create newly eligible steps."""

    def process_state(self) -> StateChangeResult:
        graph = self.context.get_block_graph(self.step.id)
        steps = list(self.context.persistence.get_steps_by_block(self.step.id))

        analysis = StepAnalysis.load(
            block=self.step,
            statements=graph.get_all_statements(),
            steps=steps,
        )

        if analysis.done:
            self.step.request_state_change(True)
            return StateChangeResult(step=self.step)

        completed_ids = {
            str(s.statement_id) for s in analysis.completed if s.statement_id
        }
        self._create_ready_steps(graph, completed_ids)
        return self.stay(push=True)  # Re-queue for next iteration
```

### Step Creation Within Blocks

**Location:** `afl/runtime/handlers/block_execution.py`

```python
def _create_ready_steps(
    self,
    graph: DependencyGraph,
    completed: set[str],
) -> None:
    ready = graph.get_ready_statements(completed)
    for stmt in ready:
        if self.context.persistence.step_exists(stmt.id, self.step.id):
            continue
        step = StepDefinition.create(
            workflow_id=self.step.workflow_id,
            object_type=stmt.object_type,
            facet_name=stmt.facet_name,
            statement_id=stmt.id,
            block_id=self.step.id,
            container_id=self.step.container_id,
            root_id=self.step.root_id or self.step.container_id,
        )
        self.context.changes.add_created_step(step)
```

---

## 8. Dependency Resolution

> Implements [runtime.md](runtime.md) §7, §11.

**Location:** `afl/runtime/block.py`

The `StepAnalysis` dataclass tracks block execution state:

```python
@dataclass
class StepAnalysis:
    """Analysis of step execution state within a block."""
    block: StepDefinition
    statements: Sequence[StatementDefinition]

    missing: list[StatementDefinition] = field(default_factory=list)
    steps: list[StepDefinition] = field(default_factory=list)
    completed: list[StepDefinition] = field(default_factory=list)
    requesting_push: list[StepDefinition] = field(default_factory=list)
    requesting_transition: list[StepDefinition] = field(default_factory=list)
    pending_event: list[StepDefinition] = field(default_factory=list)
    pending_mixin: list[StepDefinition] = field(default_factory=list)
    pending_blocks: list[StepDefinition] = field(default_factory=list)
    done: bool = False
```

### Dependency Checking

`can_be_created()` determines which statements can have steps created:

```python
def can_be_created(self) -> Sequence[StatementDefinition]:
    """Return statements whose dependencies are all satisfied."""
    completed_ids = {
        str(s.statement_id) for s in self.completed if s.statement_id
    }
    ready = []
    for stmt in self.missing:
        if stmt.dependencies.issubset(completed_ids):
            ready.append(stmt)
    return ready
```

A step is created only when all its dependencies point to completed steps.

---

## 9. Mixin Blocks vs Statement Blocks

> Implements [runtime.md](runtime.md) §8.2.

Both follow the same Begin → Continue → End pattern:

### Mixin Blocks

Execute facet-level blocks (from mixin compositions):

- **MixinBlocksBegin** - Creates block steps with `container_type="Facet"`
- **MixinBlocksContinue** - Polls with `BlockAnalysis.load(step, blocks, mixins=True)`
- **MixinBlocksEnd** - Advances to next state

### Statement Blocks

Execute statement-level blocks (from `andThen` bodies):

- **StatementBlocksBegin** - Creates block steps for each `AndThenBlock`
- **StatementBlocksContinue** - Polls with `BlockAnalysis.load(step, blocks, mixins=False)`
- **StatementBlocksEnd** - Advances to capture phase

---

## 10. Catch Execution Handlers

> Implements [runtime.md](runtime.md) §8.4.

When a step errors and has a `catch` clause, execution enters the catch phase instead of transitioning to `STATEMENT_ERROR`. This allows error recovery.

**Location:** `afl/runtime/handlers/catch_execution.py`

### State Flow

```
Error path without catch:
  ... → error → STATEMENT_ERROR (terminal)

Error path with catch:
  ... → error → CATCH_BEGIN → CATCH_CONTINUE → CATCH_END → STATEMENT_CAPTURE_BEGIN
                                    ↓ (catch fails)
                               STATEMENT_ERROR
```

### Catch Interception Points

Two places check for catch before calling `mark_error()`:

1. **`StatementBlocksContinueHandler`** — when child blocks error
2. **`StateChanger.process()`** — when event handler errors

### CatchBeginHandler

- Stores error info as pseudo-returns: `step.set_attribute("error", ...)` and `step.set_attribute("error_type", ...)`
- Simple catch: creates a single sub-block (`object_type=AND_CATCH`, `statement_id="catch-block-0"`)
- Catch when: evaluates conditions, creates sub-blocks per matching case (`statement_id="catch-case-{i}"`)

### CatchContinueHandler

- Polls catch sub-blocks (same pattern as `BlockExecutionContinueHandler`)
- All complete → transition to `CATCH_END`
- Any errored → `mark_error()` (catch itself failed, propagate)
- Not done → `stay(push=True)`

### CatchEndHandler

- Pass-through: transitions to `STATEMENT_CAPTURE_BEGIN` to resume normal flow

### Object Type

Catch sub-blocks use `ObjectType.AND_CATCH = "AndCatch"` (included in `is_block()`).

---

## 11. Capture/Yield System

> Implements [runtime.md](runtime.md) §11.1.

### StatementCaptureBegin

**Location:** `afl/runtime/handlers/capture.py`

Merges results from yield/capture blocks:

```python
class StatementCaptureBeginHandler(StateHandler):
    """Merge yield results from statement blocks (andThen)."""

    def process_state(self) -> StateChangeResult:
        blocks = self.context.persistence.get_blocks_by_step(self.step.id)
        statement_blocks = [b for b in blocks if b.is_complete]

        for block in statement_blocks:
            self._merge_yields_from_block(block)

        self.step.request_state_change(True)
        return StateChangeResult(step=self.step)

    def _merge_yield(self, yield_step: StepDefinition) -> None:
        for name, attr in yield_step.attributes.params.items():
            self.step.attributes.set_return(name, attr.value, attr.type_hint)
```

---

## 12. Completion and Notification

> Implements [runtime.md](runtime.md) §12.

### StatementComplete

**Location:** `afl/runtime/handlers/completion.py`

```python
class StatementCompleteHandler(StateHandler):
    """Mark step as complete and notify containing block."""

    def process_state(self) -> StateChangeResult:
        self.step.mark_completed()
        self._notify_container()
        return StateChangeResult(step=self.step, continue_processing=False)

    def _notify_container(self) -> None:
        # Container notification is handled implicitly through iteration:
        # completed steps unblock dependent steps in the next iteration.
        pass
```

### Container Notification

In the Python implementation, container notification is handled implicitly by the iterative evaluator. When a step completes, the evaluator's next iteration detects that dependent steps are now unblocked and schedules them. This replaces the explicit event-based `NotifyContainingBlock` pattern with dependency-driven scheduling.

---

## 13. Object Types

**Location:** `afl/runtime/types.py`

```python
class ObjectType:
    """Object type constants for step classification."""
    VARIABLE_ASSIGNMENT = "VariableAssignment"  # Regular statement
    YIELD_ASSIGNMENT = "YieldAssignment"        # Capture/output statement
    SCHEMA_INSTANTIATION = "SchemaInstantiation"  # Schema data object creation
    WORKFLOW = "Workflow"

    # Block types:
    AND_THEN = "AndThen"     # Sequential execution
    AND_MAP = "AndMap"       # Parallel/mapping
    AND_MATCH = "AndMatch"   # Conditional/pattern matching

    FACET = "Facet"          # Mixin/facet type
    BEFORE = "Before"        # Mixin hook
    AFTER = "After"          # Mixin hook
    BLOCK = "Block"

    @classmethod
    def is_block(cls, object_type: str) -> bool:
        return object_type in (cls.AND_THEN, cls.AND_MAP, cls.AND_MATCH, cls.BLOCK)

    @classmethod
    def is_statement(cls, object_type: str) -> bool:
        return object_type in (cls.VARIABLE_ASSIGNMENT, cls.YIELD_ASSIGNMENT)
```

---

## 14. Step Definition Structure

**Location:** `afl/runtime/step.py`

```python
@dataclass
class StepDefinition:
    """Persistent step definition representing a runtime step instance."""
    id: StepId
    object_type: str

    # Hierarchy
    workflow_id: WorkflowId
    statement_id: Optional[StatementId] = None
    container_type: Optional[str] = None
    container_id: Optional[StepId] = None
    block_id: Optional[BlockId] = None
    root_id: Optional[StepId] = None

    # State machine
    state: str = field(default=StepState.CREATED)
    transition: StepTransition = field(default_factory=StepTransition.initial)

    # Data
    facet_name: str = ""
    attributes: FacetAttributes = field(default_factory=FacetAttributes)

    @classmethod
    def create(cls, workflow_id, object_type, facet_name="",
               statement_id=None, container_id=None,
               block_id=None, root_id=None, **kwargs) -> "StepDefinition":
        return cls(
            id=step_id(),
            object_type=object_type,
            workflow_id=workflow_id,
            statement_id=statement_id,
            container_id=container_id,
            block_id=block_id,
            root_id=root_id,
            facet_name=facet_name,
        )
```

---

## 15. Visual Execution Flow

```
Workflow Start
    │
    ▼
┌─────────────────────────────────────────────────┐
│  StepStateChanger (VariableAssignment)          │
│  ┌─────────────────────────────────────────┐    │
│  │ FacetInit → FacetScripts → MixinBlocks  │    │
│  │     ↓                                    │    │
│  │ EventTransmit → StatementBlocks          │    │
│  │     ↓                                    │    │
│  │ StatementCapture → Complete              │    │
│  └─────────────────────────────────────────┘    │
│                    │                            │
│                    ▼                            │
│  ┌─────────────────────────────────────────┐    │
│  │ BlockStateChanger (AndThen block)       │    │
│  │  BlockBegin → BlockContinue (loop)      │    │
│  │       ↓            ↑                    │    │
│  │  Create child   poll until              │    │
│  │  steps          all done                │    │
│  │       ↓                                 │    │
│  │  BlockEnd → Complete                    │    │
│  └─────────────────────────────────────────┘    │
└─────────────────────────────────────────────────┘
    │
    ▼
Workflow Complete
```

---

## 16. Key Architectural Patterns

1. **State Machine Per Step**: Each step instance follows its own state machine lifecycle
2. **Hierarchical Nesting**: Steps contain blocks, which contain statements, which contain steps (recursive)
3. **Dependency Graph**: Next steps determined by `DependencyGraph` references between statements
4. **Polling/Looping**: `BlockExecutionContinue` and `StatementBlocksContinue` use `set_push_me(True)` to re-queue for polling
5. **Iterative Completion**: When a step completes, the evaluator's next iteration detects newly unblocked steps
6. **Yield Merging**: Capture handlers merge yield step attributes into the containing step's returns

---

## 17. Task Resilience and Workflow Recovery

> Long-running distributed workflows face compounding failure modes that don't appear in short test runs. This section documents the mechanisms that allow workflows to self-heal and run to completion despite infrastructure failures, process crashes, and transient errors.

### 17.1 Task Lifecycle with Recovery States

```
PENDING ──claim_task()──► RUNNING ──handler succeeds──► COMPLETED
   ▲                        │
   │                        ├── handler fails ──► FAILED
   │                        │                       │
   │                        ├── server dies ────┐   │
   │                        │                   │   │
   │                        └── stuck (4h) ─────┤   │
   │                                            │   │
   └────── reaper / watchdog / dashboard ───────┘   │
   └────── manual retry / dashboard retry ──────────┘
```

### 17.2 Layer 1: Orphan Reaper (v0.39.0)

**Problem:** A runner crashes (OOM, SIGKILL, power loss) without graceful shutdown. Its in-flight tasks remain in `running` state forever.

**Mechanism:** Every 60s, each runner's poll loop calls `reap_orphaned_tasks()`:
1. Query servers where `state ∈ {running, startup}` AND `ping_time < now - 5min`
2. Find tasks where `server_id ∈ dead_servers` AND `(task_heartbeat missing OR stale)`
3. Atomically reset matching tasks to `pending` with empty `server_id`
4. Write step log entries for audit visibility

**Files:** `mongo_store.py:reap_orphaned_tasks()`, `runner/service.py:_maybe_reap_orphaned_tasks()`

**Safety:** Servers in `shutdown` state (graceful drain) are not reaped. The 5-minute threshold avoids false positives from temporary network hiccups.

### 17.3 Layer 2: Stuck Task Watchdog (v0.42.0)

**Problem:** A runner is alive and pinging, but a handler is blocked indefinitely (e.g. waiting for a database connection during PostgreSQL WAL recovery). The orphan reaper won't catch this because the server isn't dead.

**Mechanism:** `reap_stuck_tasks()` runs in the same 60s cycle:
- **Pass 1 (explicit timeout):** Tasks with `timeout_ms > 0` where `now - max(task_heartbeat, updated) > timeout_ms`
- **Pass 2 (default timeout):** Tasks without explicit timeout where `now - max(task_heartbeat, updated) > AFL_STUCK_TIMEOUT_MS` (default: 4 hours)

**Heartbeat-aware:** Handlers calling `update_task_heartbeat()` during long operations (e.g. PostGIS bulk import) keep their tasks alive even if the server heartbeat is stale due to I/O contention.

**Files:** `mongo_store.py:reap_stuck_tasks()`, `runner/service.py`, `agent_poller.py`

### 17.4 Layer 3: Lease-Based Task Ownership (v0.43.0)

**Problem:** The 5-minute reaper threshold is too slow for some failure modes. A runner that crashes during task execution leaves the task locked for 5 minutes before recovery.

**Mechanism:**
- Tasks have a `lease_expires` timestamp set at claim time
- Runners renew leases via heartbeat during execution
- Expired leases allow other runners to reclaim without waiting for the full reaper cycle
- Execution timeout (default: 15 min, `AFL_EXECUTION_TIMEOUT_MS`) kills hung futures and releases capacity
- `_safe_save_task()` retries with exponential backoff on transient MongoDB errors

### 17.5 Layer 4: Errored Step Recovery (v0.44.0)

**Problem:** A step fails (e.g. database connection refused during PostgreSQL restart). The step moves to `STATEMENT_ERROR`. Later, the task is reset to pending and a runner retries it. The handler succeeds. But `continue_step()` sees the step is already in a terminal state and silently skips. The step remains in `STATEMENT_ERROR` forever. Downstream steps never execute. The workflow is permanently stuck with no visible errors.

**Mechanism:** `continue_step()` now detects when `step.state == STATEMENT_ERROR` and a result is provided:
1. Reset step state to `EVENT_TRANSMIT`
2. Clear the step's error field
3. Apply the result as return attributes
4. Advance the step to `STATEMENT_BLOCKS_BEGIN` (next state in transition table)
5. Continue normal state machine processing (blocks, capture, completion)

**Files:** `evaluator.py:continue_step()`

### 17.5.1 Notification-Driven Resume: `resume_step()` (v0.44.0)

**Problem:** After a handler completes at `EventTransmit`, the workflow needs to advance the step through its remaining states and cascade completion up to parent blocks. The original approach was to call `evaluator.resume()` which scans ALL steps in the workflow and iterates until a fixed point. For a 303-step workflow, this is O(N²) MongoDB queries per iteration — each iteration loads all non-terminal steps, and each `BlockExecutionContinueHandler` queries its children. Combined with MongoDB connection timeouts (30s each), a single resume could take hours or hang indefinitely.

**Mechanism:** The runner uses `evaluator.resume_step()` (O(depth)) instead of `evaluator.resume()` (O(all steps)). Rather than walking the ancestor chain, `resume_step()` uses a **parent notification cascade**:

```
Handler completes
  → continue_step(step_id, result)
      Advances step past EventTransmit to StatementBlocksBegin
      Saves directly to persistence (step is no longer at EventTransmit)
  → resume_step(workflow_id, step_id, ...)
      Round 1: process the continued step
        StatementBlocksBeginHandler creates andThen children (if any)
        Step advances through blocks → capture → complete
        _process_step notifies parent: marks block_id + container_id dirty
      Round 2: process only the notified parents
        Parent block checks children → all done → completes
        Notifies its own parent
      Round 3+: cascade continues until no more notifications
```

Each round only loads steps that received a child-completion notification. Steps that don't need re-evaluation are never touched.

**Key design decisions:**

1. **Step state advanced before save, without request_transition:** `continue_step()` advances the step to `STATEMENT_BLOCKS_BEGIN` (the next state after `EventTransmit`) before saving to persistence but does NOT set `request_transition=True`. This is critical for two reasons: (a) the step is past `EventTransmit` so it won't trigger a duplicate task on crash recovery, and (b) the `StatementBlocksBeginHandler` must execute (to create andThen children) before the step transitions to `StatementBlocksContinue`. Setting `request_transition=True` would cause the state changer loop to skip the Begin handler entirely.

2. **Per-workflow locking:** A per-workflow in-memory `threading.Lock` prevents concurrent `resume_step()` calls from sibling handler threads. Non-blocking: if the lock is held, the call is skipped — the active resume will see all completed children when it checks the block.

3. **Always complete the task:** `_process_event_task()` always marks the task as `COMPLETED` after the handler returns a result, even if `continue_step()` or `resume_step()` throws. This ensures the thread future always finishes and capacity is always freed. If the resume failed, the stuck-step sweep will retry.

4. **Resume timeout:** `_resume_workflow()` (the full `resume()` fallback) runs with a configurable timeout (`AFL_RESUME_TIMEOUT_S`, default 10 min). On timeout, the resume is abandoned and the sweep retries on the next cycle.

**Performance comparison (303-step Africa OSM import):**

| Approach | Steps processed | Queries | Time |
|---|---|---|---|
| `resume()` | All 303 per iteration | O(N²) | 2+ min per iteration, hangs with MongoDB issues |
| `resume_step()` | ~4-6 (notified parents) | O(depth) | 22ms |

**Files:** `runner/service.py:_resume_workflow_for_step()`, `evaluator.py:resume_step()`

### 17.5.3 Per-Step Processing: `process_single_step()` (v0.45.0)

**Problem:** The `resume_step()` mechanism (§17.5.1) is O(depth) per step but still requires per-workflow locking — only one server can resume a given workflow at a time. For large distributed deployments (100+ servers processing a foreach workflow with 50 states), this lock becomes a bottleneck. Server B's handler completion must wait for Server A's resume to finish before it can advance its own step.

**Mechanism:** `process_single_step()` replaces per-workflow locking with per-step atomic operations and continuation events:

```
Handler completes on Server A:
  → continue_step(step_id, result)          # advances past EventTransmit
  → process_single_step(step_id, ...)       # per-step, no workflow lock
      Round 1: process target step
        Creates andThen children, processes them (inline dispatch if available)
        Marks parent block_id + container_id as dirty
        Commits atomically: step updates + created steps + tasks + continuations
      Round 2: process dirty parents (from work_queue)
        Parent block checks children → not all done → stays at Continue
        No progress → no more dirty blocks → exit
      Remaining dirty blocks get continuation tasks
```

**Key components:**

1. **`process_single_step()` in `evaluator.py`:**
   - Processes one step and cascades up through dirty-block notifications
   - Multiple rounds within a single call (max 50), each committing atomically
   - Generates continuation events only for dirty blocks not processed locally
   - Bumps `version.sequence` on all updated steps for optimistic concurrency

2. **Continuation events (`continuation.py`):**
   - Generates `TaskDefinition` entries on the `_afl_continue` task list
   - Each continuation carries only `step_id` and `reason` (lightweight)
   - Deduplicated per target step — at most one pending continuation per step
   - Committed atomically alongside step changes (no partial state)

3. **Optimistic concurrency (`version.sequence`):**
   - Each `StepDefinition.version` has a `sequence` counter (monotonic)
   - `process_single_step()` increments the sequence before committing
   - `MongoStore._commit_changes()` uses conditional `replace_one` with version check
   - If two servers process the same step concurrently, only one write succeeds
   - The loser's write falls back to unconditional update (safe — the winner already advanced the step)

4. **RegistryRunner integration:**
   - `_poll_cycle()` claims both handler tasks (from `default` task list) and continuation tasks (from `_afl_continue` task list)
   - `poll_once()` also processes continuations (for testing)
   - `_process_continuation()` calls `process_single_step()` on the target step
   - `_process_event()` calls `continue_step()` then falls back to `_resume_workflow()` for inline dispatch compatibility

**Multi-server execution model:**

```
Server A (claims handler task for step X):
  1. Handler runs → produces result
  2. continue_step(X, result) → step X at StatementBlocksBegin
  3. process_single_step(X) → X completes → parent block notified
  4. Continuation task created for parent block → committed to DB

Server B (claims continuation task for parent block):
  1. process_single_step(parent_block) → checks children → 3/5 done
  2. No progress → returns (idempotent, safe)

Server C (claims handler task for step Y in same workflow):
  1. Handler runs → Y completes
  2. continue_step(Y, result) → process_single_step(Y)
  3. Parent block notified → continuation task created

Server D (claims continuation for parent block again):
  1. process_single_step(parent_block) → checks children → 5/5 done
  2. Block completes → workflow root notified → continuation created

Server E (claims continuation for workflow root):
  1. process_single_step(root) → all blocks done → workflow COMPLETED
```

No server holds a lock on the workflow. Each processes its step independently. The continuation task queue coordinates parent notification across servers.

**Performance characteristics:**

| Deployment | Approach | Throughput |
|---|---|---|
| 1 server | `resume()` (O(N²)) | Sequential, limited by scan cost |
| 1 server | `resume_step()` (O(depth)) | Sequential, 22ms per step |
| 100 servers | `process_single_step()` | Parallel — each server processes independently |

**Files:** `evaluator.py:process_single_step()`, `continuation.py`, `registry_runner.py:_process_continuation()`

### 17.5.2 Stuck-Step Sweep (safety net)

**Problem:** The event-driven processing (§17.5.1, §17.5.3) handles the 99% case, but edge cases can leave steps stuck: MongoDB goes down during commit, the runner crashes between `continue_step()` and `process_single_step()`, continuation events are lost, or tasks are never created for new `EventTransmit` steps.

**Mechanism:** The sweep runs every 5 minutes as a safety net:

1. Finds all workflows with steps at intermediate states (`EventTransmit` with `request_transition`, `blocks.Begin`, `block.execution.Begin`)
2. For each stuck step: calls `process_single_step()` directly to cascade completion and generate continuation events
3. For `EventTransmit` steps with event facets but no pending/running task: creates a new task so the handler can run

The sweep never calls full `resume()` — it processes each stuck step individually via `process_single_step()`, avoiding O(N²) scans and generating continuation events for any remaining dirty blocks.

**Files:** `registry_runner.py:_maybe_sweep_stuck_steps()`, `runner/service.py:_maybe_sweep_stuck_steps()`

### 17.6 Layer 5: Dashboard Reaper (v0.44.0)

**Problem:** All runners are at capacity with stale futures (the deadlock scenario). No runner can run the reaper because the reaper runs inside the poll loop which is gated by capacity. The system is stuck.

**Mechanism:** The dashboard runs an independent asyncio background task:
- Every 60s (configurable: `AFL_DASHBOARD_REAP_INTERVAL_S`), calls `reap_orphaned_tasks()` and `reap_stuck_tasks()`
- Completely independent of runners — runs in the FastAPI lifespan
- Breaks the deadlock: dashboard resets orphaned tasks → capacity freed → runners resume claiming

**Files:** `dashboard/app.py:_reaper_loop()`

### 17.7 Failure Modes and Which Layer Handles Them

| Failure Mode | Example | Recovery Layer |
|---|---|---|
| Runner crash (OOM, kill -9) | Process killed during import | Layer 1: Orphan reaper (5 min) |
| Handler blocked on dead resource | PostgreSQL in WAL recovery, DNS failure | Layer 2: Stuck watchdog (4h default) |
| Handler timeout | Infinite loop, deadlocked connection | Layer 3: Execution timeout (15 min) |
| Transient error, retry succeeds | Network blip, brief DB maintenance | Layer 4: Step recovery on retry |
| All runners at capacity with stale futures | Handler succeeds but continue_step skipped | Layer 5: Dashboard reaper (60s) |
| Missing dependency on runner | `psycopg2` not installed on remote machine | Manual: restart runner after install |
| Concurrent resource contention | `CREATE EXTENSION` race condition | Code fix: catch both exception types |
| Stale module state | Runner started before dep installed | Manual: restart runner |

### 17.8 Configuration

| Variable | Default | Description |
|---|---|---|
| `AFL_REAPER_TIMEOUT_MS` | 300,000 (5 min) | Server heartbeat stale threshold |
| `AFL_STUCK_TIMEOUT_MS` | 14,400,000 (4h) | Default stuck task timeout |
| `AFL_EXECUTION_TIMEOUT_MS` | 900,000 (15 min) | Per-task execution timeout |
| `AFL_DASHBOARD_REAP_INTERVAL_S` | 60 | Dashboard reaper cycle interval |
| `AFL_MAX_CONCURRENT` | 2 | Max concurrent tasks per runner |
| `AFL_POLL_INTERVAL_MS` | 1,000 | Runner poll cycle interval |

---

## 18. Key Python Source Files

All source files are located in `afl/runtime/`.

### Resilience
- `afl/runtime/mongo_store.py` — `reap_orphaned_tasks()`, `reap_stuck_tasks()`, `claim_task()` with lease
- `afl/runtime/runner/service.py` — `_maybe_reap_orphaned_tasks()`, stuck watchdog, execution timeout
- `afl/runtime/agent_poller.py` — parallel reaper/watchdog for standalone pollers
- `afl/runtime/evaluator.py` — `continue_step()` with errored step recovery
- `afl/dashboard/app.py` — `_reaper_loop()` independent background reaper

### State Handlers
- `afl/runtime/handlers/base.py` — `StateHandler` abstract base class
- `afl/runtime/changers/base.py` — `StateChanger` orchestrator + `StateChangeResult`
- `afl/runtime/changers/step_changer.py` — `StepStateChanger` (full state machine)
- `afl/runtime/changers/block_changer.py` — `BlockStateChanger` (block state machine)
- `afl/runtime/changers/yield_changer.py` — `YieldStateChanger` (yield state machine)

### Block Execution
- `afl/runtime/handlers/block_execution.py` — `BlockExecutionBeginHandler`, `BlockExecutionContinueHandler`, `BlockExecutionEndHandler`
- `afl/runtime/block.py` — `StepAnalysis`, `BlockAnalysis`, `StatementDefinition`

### Capture and Completion
- `afl/runtime/handlers/capture.py` — `StatementCaptureBeginHandler`, `MixinCaptureBeginHandler`
- `afl/runtime/handlers/completion.py` — `StatementCompleteHandler`, `EventTransmitHandler`

### Models
- `afl/runtime/states.py` — `StepState` constants, transition tables (`STEP_TRANSITIONS`, `BLOCK_TRANSITIONS`, `YIELD_TRANSITIONS`, `SCHEMA_TRANSITIONS`)
- `afl/runtime/step.py` — `StepDefinition`, `StepTransition`
- `afl/runtime/types.py` — `ObjectType`, `FacetAttributes`, `AttributeValue`, ID types

### Core Engine
- `afl/runtime/evaluator.py` — `Evaluator`, `ExecutionContext`, iteration loop, `process_single_step()`
- `afl/runtime/continuation.py` — Continuation event generation for distributed step processing
- `afl/runtime/dependency.py` — `DependencyGraph` from compiled AST
- `afl/runtime/persistence.py` — `PersistenceAPI` protocol, `IterationChanges` (with `continuation_tasks`)
- `afl/runtime/memory_store.py` — In-memory persistence for testing
- `afl/runtime/mongo_store.py` — MongoDB persistence with optimistic concurrency (`version.sequence`)

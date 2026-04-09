# FFL Event Framework Documentation

This document describes the event-driven architecture of the FFL workflow execution engine, including the task-based dispatch model, step blocking, and multi-run execution.

## Overview

FFL uses an event-driven pattern where workflow state transitions are driven by tasks. The Python runtime uses a **synchronous iterative evaluator** rather than async polling threads. The system ensures:

- **Ordered processing** - Tasks are processed in creation order
- **At-most-once delivery per step** - Only one task per step can be in `running` state at a time
- **Handler-based dispatch** - Tasks are routed to registered handlers by task name (event type)
- **Atomic iteration commits** - Changes are accumulated in memory and committed at iteration boundaries

## Architecture Diagram

```
+-----------------------------------------------------------------------------+
|                              Evaluator                                       |
|  +-------------------+  +-------------------+  +-------------------+        |
|  |  Iteration 1      |  |  Iteration 2      |  |  Iteration N      |        |
|  |  (process steps)  |  |  (process steps)  |  |  (fixed point)    |        |
|  +---------+---------+  +---------+---------+  +---------+---------+        |
|            |                      |                      |                  |
|            +----------------------+----------------------+                  |
|                                   |                                         |
|                       +-----------v-----------+                             |
|                       | EventTransmitHandler  |                             |
|                       | (creates tasks for    |                             |
|                       |  event facet steps)   |                             |
|                       +-----------------------+                             |
+-----------------------------------------------------------------------------+
                                    |
                                    v
+-----------------------------------------------------------------------------+
|                         PersistenceAPI                                       |
|  +-------------------------------------------------------------------+     |
|  |                      IterationChanges                              |     |
|  |  - Accumulated step creates/updates per iteration                  |     |
|  |  - Accumulated task creates per iteration                          |     |
|  |  - Atomic commit at iteration boundary                             |     |
|  +-------------------------------------------------------------------+     |
+-----------------------------------------------------------------------------+
```

---

## 1. Task-Based Event Lifecycle

### Components

| Component | File | Purpose |
|-----------|------|---------|
| `EventTransmitHandler` | `afl/runtime/handlers/completion.py` | Creates tasks for event facet steps and blocks the step |
| `PersistenceAPI` | `afl/runtime/persistence.py` | Task storage abstraction |
| `TaskDefinition` | `afl/runtime/persistence.py` | Task data structure (replaces former EventDefinition) |
| `IterationChanges` | `afl/runtime/persistence.py` | Accumulated changes committed atomically |

### Processing Model

The evaluator processes steps iteratively. Each iteration:

1. Processes all eligible steps through their state machines
2. Accumulates changes in `IterationChanges`
3. Commits all changes atomically at iteration boundary
4. Repeats until a fixed point is reached (no more changes)

Tasks are created during step execution (in `EventTransmitHandler`) and stored in `IterationChanges` for atomic commit.

---

## 2. Task Definition

Tasks serve as both the domain lifecycle record and the distribution mechanism. When a step invokes an event facet, the runtime creates a `TaskDefinition` that captures:

- **What** needs to happen (`name` -- the event type / facet name, e.g. `"example.4.CountDocuments"`)
- **Why** it needs to happen (`step_id` -- the step that triggered it)
- **What data** to send (`data` -- built from evaluated step attributes)
- **Where it stands** (`state` -- pending -> running -> completed/failed)
- **Routing** (`task_list_name` -- determines which queue the work appears in)
- **Runner context** (`runner_id` -- tracks which runner claimed the work)

### Task States

- `pending` - Awaiting execution
- `running` - Currently executing (claimed by a runner)
- `completed` - Successfully completed
- `failed` - Failed with error
- `ignored` - Skipped
- `canceled` - Cancelled

### Step Locking

The system ensures only one task per step can be in `running` state at a time. In MongoDB, this is enforced with a **unique partial index**:

```json
{
  "key": {"step_id": 1},
  "name": "task_step_id_running_unique_index",
  "unique": true,
  "partialFilterExpression": {"state": "running"}
}
```

This index:
- Applies **only** to documents where `state = "running"`
- Enforces **uniqueness** on `step_id` within those documents
- Allows multiple pending tasks for the same step
- Prevents multiple running tasks for the same step

### Lock Lifecycle

```
+-----------+     findOneAndUpdate      +-----------+
|  pending  | ----------------------->  |  running  |
+-----------+    (atomic transition)    +-----+-----+
                                              |
                        +---------------------+---------------------+
                        |                     |                     |
                        v                     v                     v
                 +-----------+         +-----------+         +-----------+
                 | completed |         |   failed  |         |  ignored  |
                 +-----------+         +-----------+         +-----------+
```

When processing completes, the task transitions from `running` to `completed`/`failed`, releasing the "lock" and allowing the next pending task for that step to be claimed.

---

## 3. Task Dispatch

### Dispatch Flow

```
Step Execution (EventTransmitHandler)
      |
      v
+-------------------------------------------+
|  EventTransmitHandler.process_state()     |
|  - Check if facet is EventFacetDecl       |
|  - Build payload from step attributes     |
|  - Create TaskDefinition                  |
+---------------------+---------------------+
                      |
                      v
+-------------------------------------------+
|  IterationChanges.add_created_task()      |
|  - Store for atomic commit                |
+---------------------+---------------------+
                      |
                      v
+-------------------------------------------+
|  PersistenceAPI.commit(changes)           |
|  - Persist all tasks atomically           |
+-------------------------------------------+
```

### EventTransmitHandler

**File:** `afl/runtime/handlers/completion.py`

The `EventTransmitHandler` creates tasks for event facets:

```python
class EventTransmitHandler(StateHandler):
    """Handler for state.EventTransmit.

    Dispatches tasks to external agents for processing.
    """

    def process_state(self) -> StateChangeResult:
        """Transmit task to agent."""
        facet_def = self.context.get_facet_definition(self.step.facet_name)

        if facet_def and facet_def.get("type") == "EventFacetDecl":
            task = TaskDefinition(
                id=task_id(),
                name=self.step.facet_name,
                step_id=self.step.id,
                workflow_id=self.step.workflow_id,
                state="pending",
                data=self._build_payload(),
            )

            self.context.changes.add_created_task(task)

        self.step.request_state_change(True)
        return StateChangeResult(step=self.step)

    def _build_payload(self) -> dict:
        """Build task payload from step attributes."""
        payload = {}
        for name, attr in self.step.attributes.params.items():
            payload[name] = attr.value
        return payload
```

### StateHandler Base Class

**File:** `afl/runtime/handlers/base.py`

All handlers extend `StateHandler(ABC)`:

```python
class StateHandler(ABC):
    """Abstract base for state handlers."""

    def __init__(self, step: StepDefinition, context: ExecutionContext):
        self.step = step
        self.context = context

    def process(self) -> StateChangeResult:
        """Process this state with logging and error handling."""
        self.context.telemetry.log_state_begin(self.step, self.state_name)
        try:
            result = self.process_state()
            self.context.telemetry.log_state_end(self.step, self.state_name)
            return result
        except Exception as e:
            self.context.telemetry.log_error(self.step, self.state_name, e)
            return StateChangeResult(
                step=self.step, success=False, error=e,
            )

    @abstractmethod
    def process_state(self) -> StateChangeResult:
        """Process the state logic. Subclasses implement this."""
        ...

    def transition(self) -> StateChangeResult:
        """Request transition to next state."""
        self.step.request_state_change(True)
        return StateChangeResult(step=self.step)

    def stay(self, push: bool = False) -> StateChangeResult:
        """Stay in current state, optionally re-queue."""
        self.step.request_state_change(False)
        self.step.transition.set_push_me(push)
        return StateChangeResult(step=self.step, continue_processing=push)

    def error(self, exception: Exception) -> StateChangeResult:
        """Mark step as errored."""
        self.step.mark_error(exception)
        return StateChangeResult(
            step=self.step, success=False, error=exception,
            continue_processing=False,
        )
```

---

## 4. EventTransmit Blocking Behavior

> **Implemented** -- see `spec/70_examples.md` Example 4 and `spec/30_runtime.md` Section 8.1.

The `EventTransmitHandler` has two distinct behaviors based on the facet type of the step being processed:

### Non-Event Facets (`FacetDecl`)

For steps that call a regular (non-event) facet:
- No task is created.
- The handler calls `request_state_change(True)`.
- The step immediately transitions to `state.statement.blocks.Begin`.
- This is a **pass-through** -- no blocking occurs.

### Event Facets (`EventFacetDecl`)

For steps that call an event facet (e.g., `event CountDocuments(...)`):
1. The handler creates a `TaskDefinition` with:
   - `name`: the facet name (e.g., `"example.4.CountDocuments"`)
   - `data`: built from the step's evaluated attributes
   - `state`: `"pending"`
2. The task is added to `IterationChanges`.
3. The handler calls `request_state_change(False)` -- the step **stays** at `EventTransmit`.
4. The step is **blocked** until `continue_step()` or `fail_step()` is called by an external agent.

This blocking behavior is what causes the evaluator to reach a fixed point and pause (see Section 6).

---

## 5. Step Continuation

> **Implemented** -- see `spec/30_runtime.md` Section 12.1.

`continue_step()` and `fail_step()` are the mechanisms used to resume steps blocked at `state.EventTransmit`.

### Processing Flow

1. External agent claims a pending task via `claim_task(server_id=...)` (atomic pending -> running transition). The `server_id` stamps the task for crash recovery tracking.
2. Agent performs the work described in `task.data`.
3. Agent calls `continue_step(step_id, result)` to unblock the step with a result, or `fail_step(step_id, error)` to mark it as failed.
4. The continuation writes the result into the step's return attributes.
5. Evaluator resumes: `EventTransmit` -> `state.statement.blocks.Begin`.
6. Normal evaluation continues.

### Crash Recovery — Orphaned Task Reaper

If a runner crashes while processing tasks, those tasks remain in `running` state indefinitely. The **orphaned task reaper** (running in every `RunnerService` and `AgentPoller`) automatically detects and recovers these:

1. Each `claim_task()` records the claiming server's `server_id` on the task document.
2. Every 60 seconds, the reaper queries for servers whose heartbeat (`ping_time`) is >5 minutes stale while their state is still `running`/`startup`.
3. All tasks in `running` state with a `server_id` matching a dead server are atomically reset to `pending`.
4. Healthy runners pick them up on the next poll cycle.

Servers that shut down gracefully (state = `shutdown`) are **not** considered dead — the reaper only targets servers that crashed without deregistering.

### Idempotency

- `continue_step()` for a step that has already advanced past `EventTransmit` is a **no-op**.
- Duplicate continuation calls MUST NOT cause errors or duplicate side effects.

---

## 6. Multi-Run Execution

> **Implemented** -- see `spec/70_examples.md` Example 4 for the full sequence.

Workflows that invoke event facets require multiple evaluator runs separated by external agent processing.

### Sequence

```
+--------------+                  +-------------------+                  +--------------+
|  Evaluator   |                  |   Persistence     |                  |   External   |
|              |                  |   (Database)      |                  |   Agent      |
+------+-------+                  +--------+----------+                  +------+-------+
       |                                   |                                    |
       |-- Run 1: process to fixed point ->|                                    |
       |   (steps + tasks committed)       |                                    |
       |                                   |                                    |
       |   (evaluator paused)              |  <-- poll for tasks ------------- |
       |                                   |                                    |
       |                                   |  -- task found, claimed --------> |
       |                                   |                                    |
       |                                   |  <-- processing complete -------- |
       |                                   |     + continue_step(result)        |
       |                                   |                                    |
       |  <-- step unblocked ------------- |                                    |
       |                                   |                                    |
       |-- Run 2: resume, process -------->|                                    |
       |   to next fixed point or done     |                                    |
       |                                   |                                    |
```

### Key Properties

- **State persistence**: All execution state is fully persisted at each pause boundary. The evaluator can be restarted from persistence.
- **Multiple pauses**: A workflow MAY pause and resume multiple times if it contains multiple event facet invocations at different points in the dependency graph.
- **No lost work**: Changes from all prior iterations are committed before pausing. External agents see the complete state.

---

## 7. Handler Examples

**StatementCompleteHandler** (`completion.py`):

```python
class StatementCompleteHandler(StateHandler):
    """Handler for state.statement.Complete."""

    def process_state(self) -> StateChangeResult:
        """Complete statement execution."""
        self.step.mark_completed()
        self._notify_container()
        return StateChangeResult(
            step=self.step,
            continue_processing=False,
        )

    def _notify_container(self) -> None:
        """Notify containing block that this step is complete.
        Container notification is handled implicitly through iteration."""
        pass
```

**FacetInitializationBeginHandler** (`initialization.py`):

```python
class FacetInitializationBeginHandler(StateHandler):
    """Handler for state.facet.initialization.Begin."""

    def process_state(self) -> StateChangeResult:
        """Evaluate facet attribute expressions."""
        stmt_def = self.context.get_statement_definition(self.step)
        if stmt_def is None:
            # Workflow root step
            self.step.request_state_change(True)
            return StateChangeResult(step=self.step)

        ctx = self._build_context()
        try:
            args = stmt_def.args
            evaluated = evaluate_args(args, ctx)
            for name, value in evaluated.items():
                self.step.set_attribute(name, value)
            self.step.request_state_change(True)
            return StateChangeResult(step=self.step)
        except Exception as e:
            return self.error(e)
```

---

## Summary

| Aspect | Implementation |
|--------|----------------|
| **Processing Model** | Synchronous iterative evaluator (not async polling) |
| **Task Lifecycle** | pending -> running -> completed/failed |
| **Task Distribution** | Task queue with `claim_task()` for atomic claiming |
| **Step Locking** | MongoDB partial unique index on `(step_id, state=running)` |
| **Atomic Commits** | `IterationChanges` accumulated and committed at iteration boundary |
| **Dispatch** | `EventTransmitHandler` creates tasks during step execution |
| **EventTransmit Blocking** | Event facets block at EventTransmit; non-event facets pass through |
| **Step Continuation** | `continue_step()` / `fail_step()` resume blocked steps |
| **Multi-Run Execution** | Evaluator pauses at fixed point, resumes after external processing |

## Key Files Reference

| Component | Path |
|-----------|------|
| TaskDefinition | `afl/runtime/persistence.py` |
| IterationChanges | `afl/runtime/persistence.py` |
| PersistenceAPI | `afl/runtime/persistence.py` |
| EventTransmitHandler | `afl/runtime/handlers/completion.py` |
| StateHandler base | `afl/runtime/handlers/base.py` |
| Handler registry | `afl/runtime/handlers/__init__.py` |

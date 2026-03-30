# Execution Traces

Detailed step-by-step traces of workflow execution showing the runtime's internal behavior: state machine transitions, in-memory change accumulation, persistence commits, event handler protocols, and continuation events.

These traces are the authoritative reference for how the evaluator processes workflows. Each trace follows the same structure:

1. **AFL source** and dependency graph
2. **Phase-by-phase execution** showing state transitions, in-memory changes, and commits
3. **Handler protocol** describing what external event handlers must do
4. **Summary table** with step counts, task counts, and commit boundaries

### How to read these traces

- **In-memory changes** accumulate in `IterationChanges` (created steps, updated steps, tasks, continuation tasks) and are committed atomically at iteration boundaries.
- **State transitions** are shown as `State1 → State2 → ... → FinalState`. Most transitions happen within a single `StateChanger.process()` call — the step advances through multiple states without returning to the iteration loop.
- **EventTransmit** is the blocking point for event facets. The step stays at this state until an external handler completes and calls `continue_step()`.
- **Commits** are atomic persistence operations. Everything in a commit is visible to other servers simultaneously — no partial state.
- **Continuation tasks** (`_afl_continue`) are generated when parent blocks need re-evaluation after a child completes. In single-server mode, the parent cascade happens in-process; in multi-server mode, any server can claim and process them.

### Conventions

| Notation | Meaning |
|----------|---------|
| `step ✓` | Step reached `StatementComplete` |
| `step ✗` | Step reached `StatementError` |
| `→` | State transition (within one StateChanger loop) |
| `BLOCKS` | Step stays at current state, returns to iteration loop |
| `stay(push=True)` | Handler returns without advancing; step re-queued for next iteration |

---

## Trace 1: Concurrent Event Facets

Two independent event facet calls that execute concurrently, followed by a regular facet that depends on both results.

### AFL Source

```afl
namespace test.example1 {
    event facet AddOne(input:Long) => (output: Long)
    facet Value(value:Long)

    workflow UseAddOne(input:Long) => (output:Long) andThen {
        s1 = AddOne(input = 1)
        s2 = AddOne(input = 1)
        s3 = Value(value = s1.output + s2.output)
        yield UseAddOne(output = s3.value)
    }
}
```

### Dependency Graph

```
s1 ──┐
     ├──→ s3 ──→ yield
s2 ──┘
```

s1 and s2 have no dependencies — they are eligible for concurrent execution. s3 depends on both s1 and s2 completing. The yield depends on s3.

### Phase 1: Initial Execution (`evaluator.execute()`)

The evaluator creates the workflow root step and iterates until reaching a fixed point.

#### Iteration 1 — Create root and advance through state machine

**Root step created:**

| Action | Step | Object Type | State |
|--------|------|-------------|-------|
| Create | `UseAddOne` (root) | Workflow | `CREATED` |

The root step is added to `changes.created_steps` with `params = {input: 1}`.

**State machine runs on root step:**

```
CREATED
  → FacetInitBegin        (resolve params: input = 1)
  → FacetInitEnd
  → FacetScriptsBegin     (no script block)
  → FacetScriptsEnd
  → MixinBlocksBegin      (no mixins)
  → MixinBlocksContinue
  → MixinBlocksEnd
  → MixinCaptureBegin     (no mixin yields)
  → MixinCaptureEnd
  → EventTransmit
```

At **EventTransmit**: `UseAddOne` is a Workflow, not an event facet. `EventTransmitHandler` checks the facet definition, finds `type = "WorkflowDecl"`, and passes through without creating a task:

```
  → StatementBlocksBegin
```

**`StatementBlocksBeginHandler`**: finds the `andThen` body in the workflow AST. Creates one block step:

| Action | Step | Object Type | State |
|--------|------|-------------|-------|
| Create | `block-0` | AndThen | `CREATED` |

`block-0` has `container_id = UseAddOne.id`, `statement_id = "block-0"`.

Root advances to `StatementBlocksContinue` and returns `stay(push=True)` — waiting for block-0 to complete.

**State machine runs on block-0** (newly created, processed in same iteration):

```
CREATED → BlockExecutionBegin
```

**`BlockExecutionBeginHandler`**: builds a `DependencyGraph` from the compiled AST's statement list. Analyzes dependencies:

| Statement | Dependencies | Ready? |
|-----------|-------------|--------|
| s1 | none | **yes** |
| s2 | none | **yes** |
| s3 | s1.output, s2.output | no |
| yield | s3.value | no |

Creates steps for the two ready statements:

| Action | Step | Object Type | Facet | State |
|--------|------|-------------|-------|-------|
| Create | `s1` | VariableAssignment | AddOne | `CREATED` |
| Create | `s2` | VariableAssignment | AddOne | `CREATED` |

Both have `block_id = block-0.id`, `container_id = UseAddOne.id`.

Block-0 advances to `BlockExecutionContinue` and returns `stay(push=True)`.

**State machine runs on s1** (newly created):

```
CREATED
  → FacetInitBegin        (resolve params: input = 1)
  → FacetInitEnd
  → FacetScriptsBegin
  → FacetScriptsEnd
  → MixinBlocksBegin
  → MixinBlocksContinue
  → MixinBlocksEnd
  → MixinCaptureBegin
  → MixinCaptureEnd
  → EventTransmit
```

At **EventTransmit**: `AddOne` IS an event facet (`type = "EventFacetDecl"`). `EventTransmitHandler`:

1. Checks if an inline dispatcher is available: `dispatcher.can_dispatch("test.example1.AddOne")`
2. **No inline dispatcher available** (typical distributed deployment): creates a task

| Action | Task | Details |
|--------|------|---------|
| Create task | `Task(s1)` | `name="test.example1.AddOne"`, `step_id=s1.id`, `data={input: 1}`, `state=PENDING`, `task_list_name="default"` |

3. Returns `stay(push=False)` — **step BLOCKS at EventTransmit**. The step will not advance until an external handler calls `continue_step()`.

**State machine runs on s2** (newly created): identical to s1.

| Action | Task | Details |
|--------|------|---------|
| Create task | `Task(s2)` | `name="test.example1.AddOne"`, `step_id=s2.id`, `data={input: 1}`, `state=PENDING` |

s2 also **BLOCKS at EventTransmit**.

**`BlockExecutionContinueHandler` runs on block-0**: loads children — s1 (EventTransmit), s2 (EventTransmit). Neither is terminal. Returns `stay(push=True)`.

**`StatementBlocksContinueHandler` runs on root**: loads blocks — block-0 is not complete. Returns `stay(push=True)`.

#### Commit 1 (atomic)

| Category | Items |
|----------|-------|
| **Created steps** | UseAddOne (root), block-0, s1, s2 |
| **Updated steps** | UseAddOne (→ `blocks.Continue`), block-0 (→ `execution.Continue`) |
| **Created tasks** | Task(s1, AddOne), Task(s2, AddOne) |
| **Continuation tasks** | (none) |

All written atomically to persistence. After this commit, the two tasks are visible to external runners.

#### Iteration 2 — Fixed point

The evaluator loads actionable steps:
- s1: `EventTransmit` without `request_transition` → **skipped** (blocked)
- s2: `EventTransmit` without `request_transition` → **skipped** (blocked)
- block-0: `execution.Continue`, not dirty → **skipped** (no children changed)
- root: `blocks.Continue`, not dirty → **skipped** (no blocks changed)

No progress. Event-blocked steps detected (s1, s2).

**Evaluator returns `ExecutionResult(status=PAUSED)`.**

---

### Phase 2: External Handler Processing

Two tasks are now visible in the persistence layer's `tasks` collection:

```
Task 1: {name: "test.example1.AddOne", step_id: s1.id, data: {input: 1}, state: "pending"}
Task 2: {name: "test.example1.AddOne", step_id: s2.id, data: {input: 1}, state: "pending"}
```

#### Event handler protocol

Any server running a `RegistryRunner` with a registered handler for `test.example1.AddOne` can claim these tasks. The handler must:

1. **Receive** the payload: `{input: 1, _step_log: <callback>, _task_heartbeat: <callback>, _facet_name: "test.example1.AddOne"}`
2. **Compute** the result. For AddOne, the handler adds 1 to the input: `output = input + 1 = 2`
3. **Return** a dict matching the facet's return clause: `{"output": 2}`

The handler does NOT need to call `continue_step()` or manage step state — the runner handles that automatically after the handler returns.

**Servers can process s1 and s2 concurrently.** Different servers claim different tasks via atomic `find_one_and_update` on the tasks collection.

---

### Phase 3: s1 Handler Completes (Server A)

Server A claims Task(s1), runs the handler, gets `{output: 2}`.

#### Runner calls `continue_step(s1.id, {output: 2})`

1. Loads s1 from persistence (state: `EventTransmit`)
2. Applies result as return attributes: `s1.attributes.returns = {output: AttributeValue("output", 2, "Long")}`
3. Advances state: `EventTransmit → StatementBlocksBegin` (next state in transition table)
4. Sets `transition.changed = True` but does NOT set `request_transition = True` — the `StatementBlocksBeginHandler` must execute before the step advances further
5. Saves s1 directly to persistence

#### Runner calls `_resume_workflow()` → `evaluator.resume()`

**Iteration 1:**

Loads actionable steps. s2 is at EventTransmit without `request_transition` → skipped.

**State machine runs on s1** (now at `StatementBlocksBegin`):

`StatementBlocksBeginHandler`: checks if AddOne has an `andThen` body → it does not. No blocks to create. Advances immediately:

```
StatementBlocksBegin
  → StatementBlocksContinue  (no blocks → immediately done)
  → StatementBlocksEnd
  → StatementCaptureBegin    (capture return attributes)
  → StatementCaptureEnd
  → StatementEnd
  → StatementComplete ✓
```

s1 is now **complete** with `returns = {output: 2}`.

`_process_step` detects state changed → marks `block_id` (block-0) and `container_id` (root) as dirty.

**`BlockExecutionContinueHandler` runs on block-0** (dirty):
- Loads children from persistence: s1 (Complete ✓), s2 (EventTransmit)
- s1 is newly complete → checks dependency graph for newly ready statements
- s3 depends on s1 AND s2. s2 is not complete → **s3 is NOT ready**
- No new steps to create
- Returns `stay(push=True)` — still waiting for s2

**`StatementBlocksContinueHandler` runs on root** (dirty):
- Loads blocks: block-0 is not complete
- Returns `stay(push=True)`

#### Commit 2 (atomic)

| Category | Items |
|----------|-------|
| **Updated steps** | s1 (→ `Complete`) |

No new tasks or continuation events. Evaluator returns `PAUSED` (s2 still blocked).

Runner marks Task(s1) as `COMPLETED`.

---

### Phase 4: s2 Handler Completes (Server B)

Server B claims Task(s2), runs the handler, gets `{output: 2}`.

#### Runner calls `continue_step(s2.id, {output: 2})`

Same as s1 — applies result, advances to `StatementBlocksBegin`, saves.

#### Runner calls `_resume_workflow()` → `evaluator.resume()`

**Iteration 1:**

**State machine runs on s2**: `StatementBlocksBegin → ... → StatementComplete ✓`

s2 is now **complete** with `returns = {output: 2}`.

**`BlockExecutionContinueHandler` runs on block-0** (dirty):
- Loads children: s1 (Complete ✓), s2 (Complete ✓)
- Both s1 and s2 are complete → check dependency graph for newly ready statements
- **s3 depends on s1 and s2 — both complete → s3 is ready**
- Expression evaluator resolves s3's params: `value = s1.output + s2.output = 2 + 2 = 4`

Creates step for s3:

| Action | Step | Object Type | Facet | Params |
|--------|------|-------------|-------|--------|
| Create | `s3` | VariableAssignment | Value | `{value: 4}` |

Returns `stay(push=True)` — has pending work (s3 just created).

**State machine runs on s3** (newly created, processed in same iteration):

```
CREATED
  → FacetInitBegin        (resolve params: value = 4)
  → FacetInitEnd
  → FacetScriptsBegin
  → FacetScriptsEnd
  → MixinBlocksBegin
  → MixinBlocksContinue
  → MixinBlocksEnd
  → MixinCaptureBegin
  → MixinCaptureEnd
  → EventTransmit
```

At **EventTransmit**: `Value` is a regular facet (not `event`). `EventTransmitHandler` passes through — no task created:

```
  → StatementBlocksBegin  (no andThen body → passes through)
  → StatementBlocksContinue
  → StatementBlocksEnd
  → StatementCaptureBegin
  → StatementCaptureEnd
  → StatementEnd
  → StatementComplete ✓
```

s3 completes immediately with `returns = {value: 4}`. No external handler needed.

#### Commit 3 (mid-iteration)

| Category | Items |
|----------|-------|
| **Created steps** | s3 |
| **Updated steps** | s2 (→ `Complete`), block-0 (→ `execution.Continue`) |

**Iteration 2** (dirty-block tracking active):

**`BlockExecutionContinueHandler` runs on block-0** (dirty from s3 completion):
- Loads children: s1 ✓, s2 ✓, s3 ✓
- Check dependency graph: yield depends on s3 — **s3 is complete → yield is ready**
- Expression evaluator resolves yield's params: `output = s3.value = 4`

Creates step for yield:

| Action | Step | Object Type | Params |
|--------|------|-------------|--------|
| Create | `yield` | YieldAssignment | `{output: 4}` |

**State machine runs on yield** (uses `YieldStateChanger` — minimal transitions):

```
CREATED
  → FacetInitBegin        (resolve: output = s3.value = 4)
  → FacetInitEnd
  → FacetScriptsBegin
  → FacetScriptsEnd
  → StatementEnd          (yield skips blocks and capture)
  → StatementComplete ✓
```

The yield step captures `output = 4` and merges it into the workflow root's return attributes via the capture mechanism.

**`BlockExecutionContinueHandler` runs on block-0** (dirty from yield completion):
- All 4 children complete: s1 ✓, s2 ✓, s3 ✓, yield ✓
- Advances:

```
BlockExecutionContinue → BlockExecutionEnd → StatementEnd → StatementComplete ✓
```

**`StatementBlocksContinueHandler` runs on root** (dirty from block-0 completion):
- block-0 is complete ✓
- Root advances:

```
StatementBlocksContinue
  → StatementBlocksEnd
  → StatementCaptureBegin  (captures yield output = 4)
  → StatementCaptureEnd
  → StatementEnd
  → StatementComplete ✓
```

Root step completes with `returns = {output: 4}`.

#### Commit 4 (final, atomic)

| Category | Items |
|----------|-------|
| **Created steps** | yield |
| **Updated steps** | s3 (→ `Complete`), block-0 (→ `Complete`), root (→ `Complete`), yield (→ `Complete`) |

**Evaluator returns `ExecutionResult(status=COMPLETED, outputs={output: 4})`.**

Runner marks Task(s2) as `COMPLETED`.

---

### Execution Summary

| Step | Type | Facet | Task Created? | Handler Result | Completes In |
|------|------|-------|---------------|---------------|-------------|
| UseAddOne (root) | Workflow | UseAddOne | No (workflow) | — | Commit 4 |
| block-0 | AndThen | — | No (block) | — | Commit 4 |
| s1 | VariableAssignment | AddOne | **Yes** (event) | `{output: 2}` | Commit 2 |
| s2 | VariableAssignment | AddOne | **Yes** (event) | `{output: 2}` | Commit 3 |
| s3 | VariableAssignment | Value | No (regular) | — | Commit 3 |
| yield | YieldAssignment | UseAddOne | No (yield) | — | Commit 4 |

**Totals:**
- **Steps created:** 6
- **External handler invocations:** 2 (s1 and s2, parallelizable across servers)
- **Persistence commits:** 4 (initial + s1 completion + s2 completion with s3 inline + final cascade)
- **Final output:** `{output: 4}`

### Multi-Server Execution Pattern

In a distributed deployment with multiple servers:

```
Server A                          Server B
────────                          ────────

claims Task(s1)                   claims Task(s2)
handler: input=1 → output=2      handler: input=1 → output=2
continue_step(s1, {output:2})     continue_step(s2, {output:2})
resume → s1 completes             resume → s2 completes
  block-0: 1/2 done, waits         block-0: 2/2 done!
                                    → creates s3 (inline, no task)
                                    → creates yield (inline)
                                    → block-0 completes
                                    → root completes
                                  workflow COMPLETED, output=4
```

The server that processes the **last** remaining event facet (s2 in this case) drives the workflow to completion — s3, yield, block-0, and root all complete within that server's resume call.

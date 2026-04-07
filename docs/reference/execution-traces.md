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

---

## Trace 2: Concurrent andThen Blocks with Partial Yields

A workflow with two `andThen` blocks that execute concurrently. Each block has its own event facets, dependency graph, and yield. The yields merge into the workflow's return attributes independently.

### AFL Source

```afl
namespace test.example {
    event facet AddOne(input:Long) => (output: Long)
    facet Value(value:Long)

    workflow UseMultiAndThen(input:Long) => (output1:Long, output2:Long) andThen {
        s1 = AddOne(input = $.input)
        s2 = AddOne(input = 1)
        s3 = Value(value = s1.output + s2.output)
        yield UseMultiAndThen(output1 = s3.value)
    } andThen {
        s1 = AddOne(input = $.input)
        s2 = AddOne(input = 2)
        s3 = Value(value = s1.output + s2.output)
        yield UseMultiAndThen(output2 = s3.value)
    }
}
```

### Key Design Point: Concurrent Blocks

Multiple `andThen` blocks on the same facet or workflow execute **concurrently**, not sequentially. The runtime creates one block step per `andThen` clause (`block-0`, `block-1`) and waits for **all** to complete before advancing. Each block has its own statement namespace — both blocks can have `s1`, `s2`, `s3` without conflict because steps are scoped by `block_id`.

### Dependency Graphs (per block)

```
Block-0:                         Block-1:
  s1(input=$.input) ──┐           s1(input=$.input) ──┐
                      ├→ s3 → yield                    ├→ s3 → yield
  s2(input=1) ────────┘           s2(input=2) ────────┘
```

### Step Hierarchy

```
UseMultiAndThen (root)
├── block-0 (AndThen)
│   ├── s1 = AddOne(input=$.input)     ← event facet
│   ├── s2 = AddOne(input=1)           ← event facet
│   ├── s3 = Value(value=s1.output+s2.output)
│   └── yield(output1=s3.value)
└── block-1 (AndThen)
    ├── s1 = AddOne(input=$.input)     ← event facet
    ├── s2 = AddOne(input=2)           ← event facet
    ├── s3 = Value(value=s1.output+s2.output)
    └── yield(output2=s3.value)
```

With `input = 5`:
- Block-0: `s1.output = 6`, `s2.output = 2`, `s3.value = 8` → `output1 = 8`
- Block-1: `s1.output = 6`, `s2.output = 3`, `s3.value = 9` → `output2 = 9`

---

### Phase 1: Initial Execution (`evaluator.execute()`, input=5)

#### Iteration 1 — Create root, blocks, and initial steps

**Root step created and state machine runs:**

```
CREATED → FacetInitBegin (params: input=5) → ... → EventTransmit (workflow, passes through)
  → StatementBlocksBegin
```

**`StatementBlocksBeginHandler`**: the body is a `list[AndThenBlock]` with 2 elements. Creates one block step per element:

| Action | Step | Object Type | Statement ID |
|--------|------|-------------|-------------|
| Create | `block-0` | AndThen | `block-0` |
| Create | `block-1` | AndThen | `block-1` |

Both have `container_id = root.id`. Root advances to `StatementBlocksContinue`, returns `stay(push=True)`.

**State machine runs on block-0:**

```
CREATED → BlockExecutionBegin
```

`BlockExecutionBeginHandler` builds dependency graph for block-0's statements. s1 and s2 have no dependencies → ready.

| Action | Step | Facet | Block | Params |
|--------|------|-------|-------|--------|
| Create | `b0.s1` | AddOne | block-0 | `{input: 5}` (from `$.input`) |
| Create | `b0.s2` | AddOne | block-0 | `{input: 1}` |

Block-0 → `BlockExecutionContinue`, returns `stay(push=True)`.

**State machine runs on block-1:** identical structure, different params.

| Action | Step | Facet | Block | Params |
|--------|------|-------|-------|--------|
| Create | `b1.s1` | AddOne | block-1 | `{input: 5}` (from `$.input`) |
| Create | `b1.s2` | AddOne | block-1 | `{input: 2}` |

Block-1 → `BlockExecutionContinue`, returns `stay(push=True)`.

**State machines run on all 4 event facet steps** (b0.s1, b0.s2, b1.s1, b1.s2):

Each advances through initialization to `EventTransmit` where `AddOne` (event facet) creates a task and **BLOCKS**.

| Task | Step | Params | State |
|------|------|--------|-------|
| Task 1 | b0.s1 | `{input: 5}` | PENDING |
| Task 2 | b0.s2 | `{input: 1}` | PENDING |
| Task 3 | b1.s1 | `{input: 5}` | PENDING |
| Task 4 | b1.s2 | `{input: 2}` | PENDING |

**Continue handlers** run on block-0, block-1, root — no children complete yet, all return `stay(push=True)`.

#### Commit 1 (atomic)

| Category | Items |
|----------|-------|
| **Created steps** | root, block-0, block-1, b0.s1, b0.s2, b1.s1, b1.s2 (7 steps) |
| **Updated steps** | root (→ `blocks.Continue`), block-0 (→ `execution.Continue`), block-1 (→ `execution.Continue`) |
| **Created tasks** | Task(b0.s1), Task(b0.s2), Task(b1.s1), Task(b1.s2) (4 tasks) |

#### Iteration 2 — Fixed point

All 4 event steps blocked at EventTransmit. No progress.

**Evaluator returns `PAUSED`.** Four tasks visible to runners.

---

### Phase 2: Handler Processing (4 tasks, parallelizable)

Four tasks are pending. Up to 4 servers can process them concurrently:

| Task | Handler Input | Expected Output |
|------|--------------|-----------------|
| b0.s1 | `{input: 5}` | `{output: 6}` |
| b0.s2 | `{input: 1}` | `{output: 2}` |
| b1.s1 | `{input: 5}` | `{output: 6}` |
| b1.s2 | `{input: 2}` | `{output: 3}` |

The order in which handlers complete determines how many commits are needed. We trace the most interesting case: **interleaved completion across blocks**.

---

### Phase 3: b0.s1 Completes (Server A)

Handler returns `{output: 6}`.

#### `continue_step(b0.s1, {output: 6})` → `resume()`

b0.s1: `EventTransmit → StatementBlocksBegin → ... → StatementComplete ✓` with `returns = {output: 6}`.

**block-0 Continue handler**: children are b0.s1 (Complete ✓), b0.s2 (EventTransmit). s3 depends on both → **not ready**. Returns `stay(push=True)`.

#### Commit 2

| **Updated steps** | b0.s1 (→ `Complete`) |
|---|---|

Evaluator returns `PAUSED`. Three event steps still blocked.

---

### Phase 4: b1.s2 Completes (Server B)

Handler returns `{output: 3}`.

Note: this is from **block-1**, not block-0. The blocks progress independently.

#### `continue_step(b1.s2, {output: 3})` → `resume()`

b1.s2: `→ StatementComplete ✓` with `returns = {output: 3}`.

**block-1 Continue handler**: children are b1.s1 (EventTransmit), b1.s2 (Complete ✓). s3 depends on both → **not ready**. Returns `stay(push=True)`.

#### Commit 3

| **Updated steps** | b1.s2 (→ `Complete`) |
|---|---|

---

### Phase 5: b0.s2 Completes (Server C)

Handler returns `{output: 2}`.

#### `continue_step(b0.s2, {output: 2})` → `resume()`

b0.s2: `→ StatementComplete ✓` with `returns = {output: 2}`.

**block-0 Continue handler**: b0.s1 ✓, b0.s2 ✓ — **both complete!**

- s3 depends on s1 and s2 → **ready**
- Expression evaluator: `value = b0.s1.output + b0.s2.output = 6 + 2 = 8`

Creates and processes s3:

| Action | Step | Facet | Params |
|--------|------|-------|--------|
| Create | `b0.s3` | Value | `{value: 8}` |

**b0.s3 state machine**: `Value` is a regular facet → passes through EventTransmit → `StatementComplete ✓` with `returns = {value: 8}`.

**block-0 Continue handler** (dirty from b0.s3 completion): s1 ✓, s2 ✓, s3 ✓ → yield is ready.

Creates and processes yield:

| Action | Step | Type | Params |
|--------|------|------|--------|
| Create | `b0.yield` | YieldAssignment | `{output1: 8}` |

**b0.yield** completes: `→ StatementComplete ✓`. Captures `output1 = 8`.

**block-0 Continue handler**: all 4 children complete → block-0 advances:

```
BlockExecutionContinue → BlockExecutionEnd → StatementEnd → StatementComplete ✓
```

**Root `StatementBlocksContinueHandler`** (dirty from block-0 completion):
- block-0: Complete ✓
- block-1: `execution.Continue` (still waiting for b1.s1)
- **Not all blocks done** → returns `stay(push=True)`

#### Commit 4

| Category | Items |
|----------|-------|
| **Created steps** | b0.s3, b0.yield |
| **Updated steps** | b0.s2 (→ `Complete`), b0.s3 (→ `Complete`), b0.yield (→ `Complete`), block-0 (→ `Complete`) |

Evaluator returns `PAUSED`. Block-0 is fully done. Block-1 still waiting for b1.s1.

---

### Phase 6: b1.s1 Completes (Server D) — Final Cascade

Handler returns `{output: 6}`.

#### `continue_step(b1.s1, {output: 6})` → `resume()`

b1.s1: `→ StatementComplete ✓` with `returns = {output: 6}`.

**block-1 Continue handler**: b1.s1 ✓, b1.s2 ✓ — **both complete!**

- s3 depends on s1 and s2 → **ready**
- Expression evaluator: `value = b1.s1.output + b1.s2.output = 6 + 3 = 9`

Creates and processes b1.s3 → `StatementComplete ✓` with `returns = {value: 9}`.

Creates and processes b1.yield → `StatementComplete ✓`. Captures `output2 = 9`.

**block-1 completes**: all children done → `StatementComplete ✓`.

**Root `StatementBlocksContinueHandler`** (dirty):
- block-0: Complete ✓
- block-1: Complete ✓
- **All blocks done!** Root advances:

```
StatementBlocksContinue → StatementBlocksEnd
  → StatementCaptureBegin
```

**`StatementCaptureBeginHandler`**: iterates over completed blocks and merges yields:
- From block-0: `output1 = 8` → merged into root returns
- From block-1: `output2 = 9` → merged into root returns

```
  → StatementCaptureEnd → StatementEnd → StatementComplete ✓
```

Root completes with `returns = {output1: 8, output2: 9}`.

#### Commit 5 (final)

| Category | Items |
|----------|-------|
| **Created steps** | b1.s3, b1.yield |
| **Updated steps** | b1.s1 (→ `Complete`), b1.s3 (→ `Complete`), b1.yield (→ `Complete`), block-1 (→ `Complete`), root (→ `Complete`) |

**Evaluator returns `ExecutionResult(status=COMPLETED, outputs={output1: 8, output2: 9})`.**

---

### Execution Summary

| Step | Block | Facet | Task? | Result | Completes In |
|------|-------|-------|-------|--------|-------------|
| root | — | UseMultiAndThen | No | `{output1:8, output2:9}` | Commit 5 |
| block-0 | root | — | No | — | Commit 4 |
| block-1 | root | — | No | — | Commit 5 |
| b0.s1 | block-0 | AddOne | **Yes** | `{output: 6}` | Commit 2 |
| b0.s2 | block-0 | AddOne | **Yes** | `{output: 2}` | Commit 4 |
| b0.s3 | block-0 | Value | No | `{value: 8}` | Commit 4 |
| b0.yield | block-0 | UseMultiAndThen | No | `{output1: 8}` | Commit 4 |
| b1.s1 | block-1 | AddOne | **Yes** | `{output: 6}` | Commit 5 |
| b1.s2 | block-1 | AddOne | **Yes** | `{output: 3}` | Commit 3 |
| b1.s3 | block-1 | Value | No | `{value: 9}` | Commit 5 |
| b1.yield | block-1 | UseMultiAndThen | No | `{output2: 9}` | Commit 5 |

**Totals:**
- **Steps created:** 11 (root + 2 blocks + 4 event steps + 2 value steps + 2 yields)
- **External handler invocations:** 4 (all parallelizable across up to 4 servers)
- **Persistence commits:** 5
- **Final output:** `{output1: 8, output2: 9}`

### Key Observations

1. **Block independence:** block-0 completed (Commit 4) while block-1 was still waiting for b1.s1. The blocks made progress independently — neither blocked the other.

2. **Statement namespace scoping:** Both blocks have `s1`, `s2`, `s3` names. These do not conflict because steps are scoped by `block_id`. The runtime uses `(statement_id, block_id)` as the uniqueness key.

3. **Partial yield merging:** Each block yields to a different output field (`output1` vs `output2`). The `StatementCaptureBeginHandler` merges yields from all completed blocks into the root's return attributes. If both blocks yielded to the same field, the merge order is deterministic (block-0 first, block-1 second) but last-write-wins.

4. **Optimal parallelism:** With 4 servers, all 4 handler tasks can run simultaneously. The theoretical minimum commits is 3 (initial + one handler completion that unlocks a block + final handler completion that unlocks the other block and completes the workflow). The actual commit count depends on handler completion order.

### Multi-Server Execution Pattern

```
Server A          Server B          Server C          Server D
────────          ────────          ────────          ────────
claims b0.s1      claims b1.s2      claims b0.s2      claims b1.s1
  input=5           input=2           input=1           input=5
  output=6          output=3          output=2          output=6

resume:           resume:           resume:           resume:
  b0.s1 ✓           b1.s2 ✓           b0.s2 ✓           b1.s1 ✓
  block-0: 1/2      block-1: 1/2      block-0: 2/2!     block-1: 2/2!
  waits              waits             → b0.s3 inline     → b1.s3 inline
                                       → b0.yield          → b1.yield
                                       → block-0 ✓         → block-1 ✓
                                       root: 1/2 blocks    root: 2/2 blocks!
                                       waits               → capture yields
                                                           → root ✓
                                                         COMPLETED
                                                         {output1:8, output2:9}
```

The last server to complete a block's final event facet drives that block to completion. The last server to complete a block overall drives the entire workflow to completion.

---

## Trace 3: Concurrent Continuations and Idempotent Block Assessment

Seven independent event facets completing concurrently across multiple servers. This trace emphasizes the **continuation storm** that occurs when many siblings complete near-simultaneously, and how the block's Continue handler safely handles duplicate and late-arriving continuations.

### AFL Source

```afl
namespace test.example {
    event facet AddOne(input:Long) => (output: Long)
    facet Value(value:Long)

    workflow ContinueIgnores(input:Long) => (output:[Long]) andThen {
        s1 = AddOne(input = $.input)
        s2 = AddOne(input = 2)
        s3 = AddOne(input = 3)
        s4 = AddOne(input = 4)
        s5 = AddOne(input = 5)
        s6 = AddOne(input = 6)
        s7 = AddOne(input = 7)
        s8 = Value(value = s1.output ++ s2.output ++ s3.output ++ s4.output
                         ++ s5.output ++ s6.output ++ s7.output)
        yield ContinueIgnores(output = s8.value)
    }
}
```

### Design Point: The Continuation Storm Problem

When 7 event facets complete near-simultaneously across 7 servers, each server:
1. Calls `continue_step()` for its step
2. Calls `_resume_workflow()` or `process_single_step()` which processes the step and notifies the parent block

This means the parent block (block-0) receives **7 continuation notifications** — one per completed child. Each notification triggers a `BlockExecutionContinueHandler` execution that loads all children, counts completions, and decides whether the block is done.

The critical scenario: **Server G completes s7 (the last step) and advances the block to Complete. But Server F's continuation for s6 arrives moments later, targeting a block that has already completed.** The block's Continue handler must be idempotent — processing a continuation for an already-completed block must be a no-op.

### Dependency Graph

```
s1 ──┐
s2 ──┤
s3 ──┤
s4 ──┼──→ s8 ──→ yield
s5 ──┤
s6 ──┤
s7 ──┘
```

All 7 AddOne steps are independent. s8 depends on all 7. The `++` operator concatenates values into a list.

### Step Hierarchy

```
ContinueIgnores (root)
└── block-0 (AndThen)
    ├── s1 = AddOne(input=$.input)   ← event facet
    ├── s2 = AddOne(input=2)         ← event facet
    ├── s3 = AddOne(input=3)         ← event facet
    ├── s4 = AddOne(input=4)         ← event facet
    ├── s5 = AddOne(input=5)         ← event facet
    ├── s6 = AddOne(input=6)         ← event facet
    ├── s7 = AddOne(input=7)         ← event facet
    ├── s8 = Value(value=...)        ← regular facet, depends on all 7
    └── yield(output=s8.value)
```

With `input = 1`:
- Expected: `s1.output=2, s2.output=3, s3.output=4, s4.output=5, s5.output=6, s6.output=7, s7.output=8`
- `s8.value = [2, 3, 4, 5, 6, 7, 8]`
- `output = [2, 3, 4, 5, 6, 7, 8]`

---

### Phase 1: Initial Execution (input=1)

#### Iteration 1

Root step created, state machine runs to `StatementBlocksBegin`. Creates block-0. Block-0's `BlockExecutionBeginHandler` builds the dependency graph:

| Statement | Dependencies | Ready? |
|-----------|-------------|--------|
| s1–s7 | none | **yes** (all 7) |
| s8 | s1, s2, s3, s4, s5, s6, s7 | no |
| yield | s8 | no |

Creates 7 steps and processes each through state machine to `EventTransmit` where each **BLOCKS** and creates a task:

| Task | Step | Params |
|------|------|--------|
| Task 1 | s1 | `{input: 1}` |
| Task 2 | s2 | `{input: 2}` |
| Task 3 | s3 | `{input: 3}` |
| Task 4 | s4 | `{input: 4}` |
| Task 5 | s5 | `{input: 5}` |
| Task 6 | s6 | `{input: 6}` |
| Task 7 | s7 | `{input: 7}` |

#### Commit 1 (atomic)

| Category | Count |
|----------|-------|
| **Created steps** | 9 (root, block-0, s1–s7) |
| **Updated steps** | 2 (root → `blocks.Continue`, block-0 → `execution.Continue`) |
| **Created tasks** | 7 |

**Evaluator returns `PAUSED`.** Seven tasks visible to runners.

---

### Phase 2: The Continuation Storm (7 servers, near-simultaneous)

Seven servers each claim one task. In a real deployment, handlers complete at slightly different times due to network latency, CPU load, and task complexity.

| Server | Claims | Handler Input | Handler Output | Completes At |
|--------|--------|--------------|----------------|-------------|
| A | s1 | `{input: 1}` | `{output: 2}` | T+10ms |
| B | s2 | `{input: 2}` | `{output: 3}` | T+12ms |
| C | s3 | `{input: 3}` | `{output: 4}` | T+11ms |
| D | s4 | `{input: 4}` | `{output: 5}` | T+15ms |
| E | s5 | `{input: 5}` | `{output: 6}` | T+13ms |
| F | s6 | `{input: 6}` | `{output: 7}` | T+14ms |
| G | s7 | `{input: 7}` | `{output: 8}` | T+16ms |

Each server independently:
1. `continue_step(step_id, result)` — advances step to `StatementBlocksBegin`
2. `_resume_workflow()` → `resume()` — processes the step to Complete, then evaluates block-0

---

### Phase 3: Detailed Continuation Processing

We trace what each server sees when it evaluates block-0's Continue handler after its step completes. The key insight is that **each server loads the current state of ALL children from persistence** — it sees the latest committed state, which includes completions from other servers that committed before it.

#### T+10ms — Server A completes s1

`continue_step(s1, {output: 2})` → `resume()`:
- s1: `StatementBlocksBegin → ... → StatementComplete ✓`
- **block-0 Continue handler loads children:**
  - s1: Complete ✓ (just completed)
  - s2–s7: EventTransmit (still blocked)
  - **1/7 complete → not done → `stay(push=True)`**

Commit: `s1 → Complete`

#### T+11ms — Server C completes s3

`continue_step(s3, {output: 4})` → `resume()`:
- s3: `→ StatementComplete ✓`
- **block-0 Continue handler loads children:**
  - s1: Complete ✓ (from Server A's commit)
  - s3: Complete ✓ (just completed)
  - s2, s4–s7: EventTransmit
  - **2/7 complete → not done → `stay(push=True)`**

Commit: `s3 → Complete`

#### T+12ms — Server B completes s2

- s2: `→ StatementComplete ✓`
- **block-0 Continue: 3/7 complete → not done**

#### T+13ms — Server E completes s5

- s5: `→ StatementComplete ✓`
- **block-0 Continue: 4/7 complete → not done**

#### T+14ms — Server F completes s6

- s6: `→ StatementComplete ✓`
- **block-0 Continue: 5/7 complete → not done**

#### T+15ms — Server D completes s4

- s4: `→ StatementComplete ✓`
- **block-0 Continue: 6/7 complete → not done**

#### T+16ms — Server G completes s7 (the last step)

`continue_step(s7, {output: 8})` → `resume()`:
- s7: `→ StatementComplete ✓`
- **block-0 Continue handler loads children:**
  - s1–s7: ALL Complete ✓
  - **7/7 complete → all dependencies for s8 satisfied!**

Creates s8:
- Expression evaluator: `value = s1.output ++ s2.output ++ ... ++ s7.output = [2, 3, 4, 5, 6, 7, 8]`
- s8 is a regular facet → completes inline: `StatementComplete ✓` with `returns = {value: [2, 3, 4, 5, 6, 7, 8]}`

Creates yield:
- `output = s8.value = [2, 3, 4, 5, 6, 7, 8]` → `StatementComplete ✓`

block-0: all 9 children complete → `StatementComplete ✓`

Root: block-0 complete → `StatementCaptureBegin` (merges yield) → `StatementComplete ✓`

**Commit: s7, s8, yield, block-0, root → all Complete**

**Workflow COMPLETED with `{output: [2, 3, 4, 5, 6, 7, 8]}`**

---

### Phase 4: Late Continuation — The Idempotency Guarantee

Here is the critical scenario. Server F completed s6 at T+14ms and committed `s6 → Complete`. Its `resume()` call evaluated block-0 and found 5/7 complete → not done → returned `PAUSED`.

But what if Server F's `resume()` was **slow** (e.g., MongoDB connection delay) and didn't commit until T+17ms — **after Server G already completed the workflow?**

```
Timeline:
  T+14ms  Server F: handler returns {output: 7}
  T+14ms  Server F: continue_step(s6, {output: 7}) — saves s6 to persistence
  T+16ms  Server G: handler returns, continue_step(s7), resume()
  T+16ms  Server G: block-0 sees 7/7 → creates s8, yield → block-0 ✓ → root ✓
  T+17ms  Server F: resume() finally starts executing
```

**What happens when Server F's `resume()` runs at T+17ms?**

The `resume()` call loads actionable steps. But:

1. **s6 is already at `StatementComplete`** — terminal state, skipped by `_process_step()`
2. **block-0 is already at `StatementComplete`** — terminal state, skipped
3. **Root is already at `StatementComplete`** — terminal state, skipped
4. **No actionable steps remain** — no progress, no event-blocked steps
5. **`resume()` returns `COMPLETED`** — the workflow is already done

**No harm done.** Server F's late resume is a no-op. It observes the final state and exits cleanly.

#### The `process_single_step()` Path (distributed mode)

In the per-step processing model, the situation is even simpler. When Server G's `process_single_step(s7)` completes the workflow, it commits everything atomically. If Server F subsequently calls `process_single_step(block-0)` via a continuation task:

1. Loads block-0 from persistence → state is `StatementComplete` (terminal)
2. `process_single_step()` detects `step.is_terminal` → returns immediately
3. Continuation task marked `COMPLETED` — no work done, no harm

#### The `continue_step()` Idempotency

What if two servers call `continue_step()` for the same step? This cannot happen under normal operation — `claim_task()` is atomic, so only one server gets each task. But if it did (e.g., task reprocessing after crash recovery):

1. First call: step at `EventTransmit` → advances to `StatementBlocksBegin`, saves
2. Second call: step at `StatementBlocksBegin` (not `EventTransmit`) → raises `ValueError("Step X is at state.statement.blocks.Begin, expected state.EventTransmit")` → caller handles the error, no corruption

If the step already reached a terminal state:
1. `continue_step()` detects `step.is_terminal` → logs warning, returns (no-op)

---

### Idempotency Guarantees — Summary

The runtime provides multiple layers of idempotency protection for concurrent continuations:

| Scenario | Protection | Outcome |
|----------|-----------|---------|
| Block Continue handler runs after block already completed | `step.is_terminal` check in `_process_step()` | Handler never runs, no-op |
| Continuation task targets a completed step | `step.is_terminal` check in `process_single_step()` | Returns immediately, task marked completed |
| `resume()` runs after workflow already completed | No actionable steps remain | Returns `COMPLETED`, no-op |
| `continue_step()` called twice for same step | State check: expected `EventTransmit` | Second call raises error or is no-op (terminal) |
| Two servers process the same continuation task | `claim_task()` is atomic (`findOneAndUpdate`) | Only one server gets the task |
| Optimistic concurrency conflict on step update | `version.sequence` check in `replace_one` | Loser's write falls back safely |
| Block Continue handler creates s8 twice | `step_exists(statement_id, block_id)` check | Duplicate creation prevented |

**The fundamental principle:** every handler, every Continue evaluation, every continuation task processing is designed to be safe to re-execute. The worst case is wasted work (loading children, counting completions, discovering it's already done) — never corruption or duplicate side effects.

---

### Execution Summary

| Step | Facet | Task? | Result | Server |
|------|-------|-------|--------|--------|
| root | ContinueIgnores | No | `{output: [2,3,4,5,6,7,8]}` | G (final) |
| block-0 | — | No | — | G (final) |
| s1 | AddOne | **Yes** | `{output: 2}` | A |
| s2 | AddOne | **Yes** | `{output: 3}` | B |
| s3 | AddOne | **Yes** | `{output: 4}` | C |
| s4 | AddOne | **Yes** | `{output: 5}` | D |
| s5 | AddOne | **Yes** | `{output: 6}` | E |
| s6 | AddOne | **Yes** | `{output: 7}` | F |
| s7 | AddOne | **Yes** | `{output: 8}` | G |
| s8 | Value | No | `{value: [2,3,4,5,6,7,8]}` | G (inline) |
| yield | ContinueIgnores | No | `{output: [2,3,4,5,6,7,8]}` | G (inline) |

**Totals:**
- **Steps created:** 11
- **External handler invocations:** 7 (fully parallelizable across 7 servers)
- **Persistence commits:** 8 (initial + 7 handler completions; the last one cascades s8, yield, block-0, root)
- **Continuation notifications to block-0:** 7 (one per handler completion)
- **Continuations that actually advanced the block:** 1 (the 7th, which found all children complete)
- **Wasted block evaluations:** 6 (each loaded children, counted completions, found incomplete, returned)
- **Final output:** `{output: [2, 3, 4, 5, 6, 7, 8]}`

### Multi-Server Timeline

```
T+0ms   Evaluator: execute() → 7 tasks created → PAUSED

T+10ms  Server A: s1 handler done → continue → resume → block-0: 1/7 → PAUSED
T+11ms  Server C: s3 handler done → continue → resume → block-0: 2/7 → PAUSED
T+12ms  Server B: s2 handler done → continue → resume → block-0: 3/7 → PAUSED
T+13ms  Server E: s5 handler done → continue → resume → block-0: 4/7 → PAUSED
T+14ms  Server F: s6 handler done → continue → resume → block-0: 5/7 → PAUSED
T+15ms  Server D: s4 handler done → continue → resume → block-0: 6/7 → PAUSED
T+16ms  Server G: s7 handler done → continue → resume → block-0: 7/7!
                  → s8 created (inline) → yield created (inline)
                  → block-0 ✓ → root ✓ → COMPLETED {output: [2,3,4,5,6,7,8]}

T+17ms  Server F: (late resume from slow MongoDB) → all steps terminal → no-op
```

**Key takeaway:** In a 7-server deployment, all handlers run in parallel. The total wall-clock time is determined by the slowest handler, not the sum. Each intermediate block evaluation (6 wasted) costs only a few milliseconds of database reads. The system prioritizes correctness (idempotent re-evaluation) over eliminating redundant work.

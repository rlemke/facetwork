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

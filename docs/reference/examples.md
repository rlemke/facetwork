
## 1. Example

```afl
namespace example.1 {
    facet Value(input:Long)
    workflow WF(input:Long = 2) => (output:Long)
        andThen {
            step1 = Value(input = $.input + 42)
            yield WF(output = step1.output)
        }
}
```

In the above example the following stages occur:
1. First the workflow is compiled
2. It is given to the workflow runner with optional input parameters
3. A step is created for the workflow called `WF_run_<unique id>`
4. The step goes through the following states:
    - `Created`
    - `facet.initialization.Begin` — evaluate attributes and place into facet
    - `facet.initialization.End` — record the end of initialization
    - `facet.scripts.Begin` — execute the facet's `script` block if present (inline Python via `script python "code..."` or `script "code..."`)
    - `mixin.blocks.Begin` — start processing mixin blocks (for this example there are none)
    - `mixin.blocks.Continue` — continue processing mixin blocks (if any) until done
    - `mixin.blocks.End` — finish processing mixin blocks
    - `mixin.capture.Begin` — start mixin capture phase. Capture yield statements and merge in attributes
    - `mixin.capture.End` — end mixin capture phase
    - `EventTransmit` — if this is an event facet or has a mixin that is an event then the event is transmitted. This step will not continue until a StatementContinue event is received
    - `statement.blocks.Begin` — start processing statement blocks. In this example there is one AndThen block
    - `statement.blocks.Continue` — continue processing statement blocks until done
    - `statement.blocks.End` — finish processing statement blocks
    - `statement.capture.Begin` — start statement capture phase. Capture yield statements and merge in attributes
    - `statement.capture.End` — end statement capture phase
    - `statement.End` — record the end of the statement
    - `statement.Complete` — mark the statement as complete. This is a successful completion and steps that are dependent on this step can continue. If this is the workflow starting step, then the workflow is marked complete.

## 2. Example

```afl
namespace example.2 {
    facet Value(input:Long)
    facet Adder(a:Long, b:Long) => (sum:Long)
        andThen {
            s1 = Value(input = $.a)
            s2 = Value(input = $.b)
            yield Adder(sum = s1.input + s2.input)
        }
    workflow AddWorkflow(x:Long = 1, y:Long = 2) => (result:Long)
        andThen {
            addition = Adder(a = $.x, b = $.y)
            yield AddWorkflow(result = addition.sum)
        }
}
```

### Steps Created

| # | Step Name         | Object Type        | State Machine | Parent (container) |
|---|-------------------|--------------------|---------------|--------------------|
| 1 | AddWorkflow_run   | VariableAssignment | STEP          | — (root)           |
| 2 | block_AW          | AndThen            | BLOCK         | AddWorkflow_run    |
| 3 | addition          | VariableAssignment | STEP          | block_AW           |
| 4 | yield_AW          | YieldAssignment    | YIELD         | block_AW           |
| 5 | block_Adder       | AndThen            | BLOCK         | addition           |
| 6 | s1                | VariableAssignment | STEP          | block_Adder        |
| 7 | s2                | VariableAssignment | STEP          | block_Adder        |
| 8 | yield_Adder       | YieldAssignment    | YIELD         | block_Adder        |

### Iteration-by-Iteration Trace

> **Note:** Yield steps are created **lazily** — they are not created in iteration 0 with the other steps. Instead, yields are created by `BlockExecutionContinue` only when all their referenced steps are committed as complete. See `spec/30_runtime.md` §11.1 for details.

**Iteration 0 — Setup**

The evaluator creates the root step `AddWorkflow_run` for `AddWorkflow(x=1, y=2)`.

| Step            | State progression                                                             |
|-----------------|-------------------------------------------------------------------------------|
| AddWorkflow_run | Created → facet.initialization.Begin → ... → statement.blocks.Begin (creates block_AW) → statement.blocks.Continue (BLOCKED — waiting on block_AW) |
| block_AW        | Created → block.execution.Begin (creates addition) → block.execution.Continue (BLOCKED — waiting on addition; yield_AW deferred) |
| addition        | Created → facet.initialization.Begin → ... → statement.blocks.Begin (creates block_Adder) → statement.blocks.Continue (BLOCKED — waiting on block_Adder) |
| block_Adder     | Created → block.execution.Begin (creates s1, s2) → block.execution.Continue (BLOCKED — waiting on s1, s2; yield_Adder deferred) |
| s1              | Created → facet.initialization.Begin → ... → statement.Complete ✓ |
| s2              | Created → facet.initialization.Begin → ... → statement.Complete ✓ |

**Commit 0:** 6 steps created. s1 and s2 reach `statement.Complete`. Yield steps not yet created (dependencies not yet committed).

> **Parallelism:** s1 and s2 are independent — they execute in parallel within the same iteration.

**Iteration 1 — yield_Adder created and completes**

s1 and s2 are now committed as complete. `BlockExecutionContinue` for block_Adder creates yield_Adder (lazy creation). yield_Adder evaluates `s1.input + s2.input` and completes in the same iteration.

| Step            | State progression                                                   |
|-----------------|---------------------------------------------------------------------|
| yield_Adder     | **Created** → facet.initialization.Begin → ... → statement.Complete ✓ (lazy creation) |
| block_Adder     | block.execution.Continue — still waiting (yield_Adder just finished, not yet committed) |

**Commit 1:** yield_Adder created and reaches `statement.Complete`. Total: 7 steps.

**Iteration 2 — block_Adder completes**

All children of block_Adder (s1, s2, yield_Adder) are now complete.

| Step          | State progression                                               |
|---------------|-----------------------------------------------------------------|
| block_Adder   | block.execution.Continue → block.execution.End → statement.End → statement.Complete ✓ |

**Commit 2:** block_Adder reaches `statement.Complete`.

**Iteration 3 — addition unblocks**

block_Adder is complete, so `addition` can advance past `statement.blocks.Continue`.

| Step      | State progression                                                                     |
|-----------|---------------------------------------------------------------------------------------|
| addition  | statement.blocks.Continue → statement.blocks.End → statement.capture.Begin → ... → statement.Complete ✓ |

**Commit 3:** addition reaches `statement.Complete`. Its attributes now include `sum = 3`.

**Iteration 4 — yield_AW created and completes**

addition is complete with `sum = 3`. `BlockExecutionContinue` for block_AW creates yield_AW (lazy creation). yield_AW evaluates `addition.sum` and completes in the same iteration.

| Step      | State progression                                                   |
|-----------|---------------------------------------------------------------------|
| yield_AW  | **Created** → facet.initialization.Begin → ... → statement.Complete ✓ (lazy creation) |
| block_AW  | block.execution.Continue — still waiting (yield_AW not yet committed) |

**Commit 4:** yield_AW created and reaches `statement.Complete`. Total: 8 steps (all steps now exist).

**Iteration 5 — block_AW completes**

All children of block_AW (addition, yield_AW) are now complete.

| Step     | State progression                                               |
|----------|-----------------------------------------------------------------|
| block_AW | block.execution.Continue → block.execution.End → statement.End → statement.Complete ✓ |

**Commit 5:** block_AW reaches `statement.Complete`.

**Iteration 6 — AddWorkflow_run completes**

block_AW is complete, so the root workflow step can advance.

| Step            | State progression                                                                     |
|-----------------|---------------------------------------------------------------------------------------|
| AddWorkflow_run | statement.blocks.Continue → statement.blocks.End → statement.capture.Begin → ... → statement.Complete ✓ |

**Commit 6:** AddWorkflow_run reaches `statement.Complete`. Workflow is done.

**Iteration 7 — Fixed point**

No step makes progress. The evaluator terminates.

**Workflow output:** `result = addition.sum = s1.input + s2.input = 1 + 2 = 3`

### Parallelism Summary

| Iteration | Steps Created | Steps Progressed                     | Notes                                    |
|-----------|--------------|--------------------------------------|------------------------------------------|
| 0         | 6            | AddWorkflow_run, block_AW, addition, block_Adder, s1, s2 | Setup: 6 steps created; s1 ‖ s2 run in parallel; yields deferred |
| 1         | +1 (7)       | yield_Adder                          | Lazy creation + completion; s1/s2 committed |
| 2         | —            | block_Adder                          | All children complete                    |
| 3         | —            | addition                             | Child block complete                     |
| 4         | +1 (8)       | yield_AW                             | Lazy creation + completion; addition committed |
| 5         | —            | block_AW                             | All children complete                    |
| 6         | —            | AddWorkflow_run                      | Child block complete — workflow done      |
| 7         | —            | (none)                               | Fixed point — evaluator terminates        |

The only true parallelism is **s1 ‖ s2** in iteration 0: both are `Value` facets with no inter-dependencies, so they execute and complete within the same iteration.


## 3. Example

```afl
namespace example.3 {
    facet Value(input:Long)
    facet SomeFacet(input:Long) => (output:Long)
    facet Adder(a:Long, b:Long) => (sum:Long)
        andThen {
            s1 = SomeFacet(input = $.a) andThen {
                subStep1 = Value(input = $.input)
                yield SomeFacet(output = subStep1.input + 10)
            }
            s2 = Value(input = $.b)
            yield Adder(sum = s1.output + s2.input)
        }

    workflow AddWorkflow(x:Long = 1, y:Long = 2) => (result:Long)
        andThen {
            addition = Adder(a = $.x, b = $.y)
            yield AddWorkflow(result = addition.sum)
        }
}
```

The key difference from Example 2: s1 has a **statement-level `andThen` block**, creating a deeper nesting. s1 calls `SomeFacet` and then overrides its body with a nested block containing `subStep1` and a yield.

### Steps Created (11 total)

| # | Step Name       | Object Type        | State Machine | Parent          |
|---|-----------------|---------------------|---------------|-----------------|
| 1 | AddWorkflow_run | VariableAssignment | STEP          | — (root)        |
| 2 | block_AW        | AndThen            | BLOCK         | AddWorkflow_run |
| 3 | addition        | VariableAssignment | STEP          | block_AW        |
| 4 | yield_AW        | YieldAssignment    | YIELD         | block_AW        |
| 5 | block_Adder     | AndThen            | BLOCK         | addition        |
| 6 | s1              | VariableAssignment | STEP          | block_Adder     |
| 7 | s2              | VariableAssignment | STEP          | block_Adder     |
| 8 | yield_Adder     | YieldAssignment    | YIELD         | block_Adder     |
| 9 | block_s1        | AndThen            | BLOCK         | s1              |
| 10| subStep1        | VariableAssignment | STEP          | block_s1        |
| 11| yield_SF        | YieldAssignment    | YIELD         | block_s1        |

Compared to Example 2, there are 3 additional steps (9–11) for s1's nested `andThen` block.

### State Machines

Three state machines (from `afl/runtime/states.py`):

**STEP** (VariableAssignment — steps 1, 3, 6, 7, 10):
```
Created → facet.initialization.Begin → facet.initialization.End →
facet.scripts.Begin → facet.scripts.End →
mixin.blocks.Begin → mixin.blocks.Continue → mixin.blocks.End →
mixin.capture.Begin → mixin.capture.End → EventTransmit →
statement.blocks.Begin → statement.blocks.Continue → statement.blocks.End →
statement.capture.Begin → statement.capture.End →
statement.End → statement.Complete
```
Blocking point: `statement.blocks.Continue` — waits for child block steps to complete.

**BLOCK** (AndThen — steps 2, 5, 9):
```
Created → block.execution.Begin → block.execution.Continue →
block.execution.End → statement.End → statement.Complete
```
Blocking point: `block.execution.Continue` — waits for all child steps to complete.

**YIELD** (YieldAssignment — steps 4, 8, 11):
```
Created → facet.initialization.Begin → facet.initialization.End →
facet.scripts.Begin → facet.scripts.End →
statement.End → statement.Complete
```
Blocking point: `facet.initialization.Begin` — blocks if referenced step attributes aren't available yet.

### Iteration-by-Iteration Trace

> **Note:** Yield steps are created **lazily** — see `spec/30_runtime.md` §11.1.

**Iteration 0 — Setup**

Steps are created in a cascade as each parent processes and creates children within the same iteration. Yield steps are deferred until their dependencies are committed.

| Step            | State progression |
|-----------------|-------------------|
| AddWorkflow_run | Created → ... → statement.blocks.Begin (creates block_AW) → statement.blocks.Continue (BLOCKED — waiting on block_AW) |
| block_AW        | Created → block.execution.Begin (creates addition) → block.execution.Continue (BLOCKED — waiting on addition; yield_AW deferred) |
| addition        | Created → ... → statement.blocks.Begin (creates block_Adder) → statement.blocks.Continue (BLOCKED — waiting on block_Adder) |
| block_Adder     | Created → block.execution.Begin (creates s1, s2) → block.execution.Continue (BLOCKED — waiting on s1, s2; yield_Adder deferred) |
| s1              | Created → ... → statement.blocks.Begin (creates block_s1) → statement.blocks.Continue (BLOCKED — waiting on block_s1) |
| s2              | Created → ... → statement.Complete ✓ |
| block_s1        | Created → block.execution.Begin (creates subStep1) → block.execution.Continue (BLOCKED — waiting on subStep1; yield_SF deferred) |
| subStep1        | Created → ... → statement.Complete ✓ |

**Commit 0:** 8 steps created. s2 and subStep1 reach `statement.Complete`. Yield steps not yet created.

> **Parallelism:** s1 ‖ s2 are siblings with no data dependency. subStep1 (inside s1's block) also runs in the same iteration as s2.

**Iteration 1 — yield_SF created and completes**

subStep1 is now committed as complete. `BlockExecutionContinue` for block_s1 creates yield_SF (lazy creation). yield_SF evaluates `subStep1.input + 10 = 1 + 10 = 11` and completes in the same iteration.

| Step       | State progression |
|------------|-------------------|
| yield_SF   | **Created** → facet.initialization.Begin → ... → statement.Complete ✓ (lazy creation) |
| block_s1   | block.execution.Continue — still waiting (yield_SF not yet committed) |

**Commit 1:** yield_SF created and reaches `statement.Complete`. Total: 9 steps.

**Iteration 2 — block_s1 completes**

All children of block_s1 (subStep1, yield_SF) are now complete.

| Step     | State progression |
|----------|-------------------|
| block_s1 | block.execution.Continue → block.execution.End → statement.End → statement.Complete ✓ |

**Commit 2:** block_s1 reaches `statement.Complete`.

**Iteration 3 — s1 unblocks**

block_s1 is complete, so s1 advances past `statement.blocks.Continue`.

| Step | State progression |
|------|-------------------|
| s1   | statement.blocks.Continue → statement.blocks.End → statement.capture.Begin → ... → statement.Complete ✓ |

**Commit 3:** s1 reaches `statement.Complete` with `output = 11`.

**Iteration 4 — yield_Adder created and completes**

s1 is complete (output = 11), s2 was already complete (input = 2). `BlockExecutionContinue` for block_Adder creates yield_Adder (lazy creation). yield_Adder evaluates `s1.output + s2.input = 11 + 2 = 13` and completes in the same iteration.

| Step         | State progression |
|--------------|-------------------|
| yield_Adder  | **Created** → facet.initialization.Begin → ... → statement.Complete ✓ (lazy creation) |
| block_Adder  | block.execution.Continue — still waiting (yield_Adder not yet committed) |

**Commit 4:** yield_Adder created and reaches `statement.Complete`. Total: 10 steps.

**Iteration 5 — block_Adder completes**

All children of block_Adder (s1, s2, yield_Adder) are now complete.

| Step        | State progression |
|-------------|-------------------|
| block_Adder | block.execution.Continue → block.execution.End → statement.End → statement.Complete ✓ |

**Commit 5:** block_Adder reaches `statement.Complete`.

**Iteration 6 — addition unblocks**

| Step     | State progression |
|----------|-------------------|
| addition | statement.blocks.Continue → statement.blocks.End → statement.capture.Begin → ... → statement.Complete ✓ |

**Commit 6:** addition reaches `statement.Complete` with `sum = 13`.

**Iteration 7 — yield_AW created and completes**

addition is complete with sum = 13. `BlockExecutionContinue` for block_AW creates yield_AW (lazy creation). yield_AW evaluates `addition.sum` and completes in the same iteration.

| Step     | State progression |
|----------|-------------------|
| yield_AW | **Created** → facet.initialization.Begin → ... → statement.Complete ✓ (lazy creation) |
| block_AW | block.execution.Continue — still waiting (yield_AW not yet committed) |

**Commit 7:** yield_AW created and reaches `statement.Complete`. Total: 11 steps (all steps now exist).

**Iteration 8 — block_AW completes**

All children of block_AW (addition, yield_AW) are now complete.

| Step     | State progression |
|----------|-------------------|
| block_AW | block.execution.Continue → block.execution.End → statement.End → statement.Complete ✓ |

**Commit 8:** block_AW reaches `statement.Complete`.

**Iteration 9 — AddWorkflow_run completes**

block_AW is complete, so the root workflow step can advance.

| Step            | State progression |
|-----------------|-------------------|
| AddWorkflow_run | statement.blocks.Continue → statement.blocks.End → statement.capture.Begin → ... → statement.Complete ✓ |

**Commit 9:** AddWorkflow_run reaches `statement.Complete`. Workflow is done.

**Iteration 10 — Fixed point**

No step makes progress. The evaluator terminates.

**Workflow output:** `result = addition.sum = s1.output + s2.input = 11 + 2 = 13`

### Concurrency Summary

| Iteration | Steps Created | Steps Progressed | Notes |
|-----------|--------------|-----------------|-------|
| 0 | 8 | AddWorkflow_run, block_AW, addition, block_Adder, s1, s2, block_s1, subStep1 | Setup: 8 steps created; s1 ‖ s2 in parallel; yields deferred |
| 1 | +1 (9) | yield_SF | Lazy creation + completion; subStep1 committed |
| 2 | — | block_s1 | All children complete |
| 3 | — | s1 | Child block complete (output = 11) |
| 4 | +1 (10) | yield_Adder | Lazy creation + completion; s1/s2 committed |
| 5 | — | block_Adder | All children complete |
| 6 | — | addition | Child block complete (sum = 13) |
| 7 | +1 (11) | yield_AW | Lazy creation + completion; addition committed |
| 8 | — | block_AW | All children complete |
| 9 | — | AddWorkflow_run | Child block complete — workflow done |
| 10 | — | (none) | Fixed point — evaluator terminates |

The only true parallelism is **s1 ‖ s2** in iteration 0: both are siblings in Adder's `andThen` with no inter-dependency. However, s1 blocks at `statement.blocks.Continue` while s2 runs to completion — s2 finishes in iteration 0 while s1 takes until iteration 3 (waiting for its nested block_s1 → subStep1 → yield_SF chain).

Compared to Example 2 (8 steps, 8 iterations), Example 3 adds 3 steps and 3 iterations due to the nested block on s1.


## 4. Example

```afl
namespace example.4 {
    facet Value(input:Long)
    facet SomeFacet(input:Long) => (output:Long)
    event CountDocuments(input:Long) => (output:Long)
    facet Adder(a:Long, b:Long) => (sum:Long)
        andThen {
            s1 = SomeFacet(input = $.a) andThen {
                subStep1 = CountDocuments(input = "some.file")
                yield SomeFacet(output = subStep1.input + 10)
            }
            s2 = Value(input = $.b)
            yield Adder(sum = s1.output + s2.input)
        }
    workflow AddWorkflow(x:Long = 1, y:Long = 2) => (result:Long)
        andThen {
            addition = Adder(a = $.x, b = $.y)
            yield AddWorkflow(result = addition.sum)
        }
}
```

The key difference from Example 3: `subStep1` now calls an **event facet** (`CountDocuments`) instead of a regular facet (`Value`). This introduces the event lifecycle — when `subStep1` reaches `EventTransmit`, execution pauses until an external microservice processes the event and sends back a `StepContinue`.

### Steps Created (11 total)

| # | Step Name       | Object Type        | State Machine | Parent          | Notes |
|---|-----------------|---------------------|---------------|-----------------|-------|
| 1 | AddWorkflow_run | VariableAssignment | STEP          | — (root)        | |
| 2 | block_AW        | AndThen            | BLOCK         | AddWorkflow_run | |
| 3 | addition        | VariableAssignment | STEP          | block_AW        | Calls Adder |
| 4 | yield_AW        | YieldAssignment    | YIELD         | block_AW        | Depends on addition.sum |
| 5 | block_Adder     | AndThen            | BLOCK         | addition        | |
| 6 | s1              | VariableAssignment | STEP          | block_Adder     | Calls SomeFacet, has statement-level andThen |
| 7 | s2              | VariableAssignment | STEP          | block_Adder     | Calls Value |
| 8 | yield_Adder     | YieldAssignment    | YIELD         | block_Adder     | Depends on s1.output + s2.input |
| 9 | block_s1        | AndThen            | BLOCK         | s1              | |
| 10| subStep1        | VariableAssignment | STEP          | block_s1        | **Event facet** (CountDocuments) |
| 11| yield_SF        | YieldAssignment    | YIELD         | block_s1        | Depends on subStep1.input |

Same step structure as Example 3, but subStep1 (step 10) invokes an event facet.

### State Machines

Four state machines govern execution:

**STEP** (VariableAssignment — steps 1, 3, 6, 7, 10):
```
Created → facet.initialization.Begin → facet.initialization.End →
facet.scripts.Begin → facet.scripts.End →
mixin.blocks.Begin → mixin.blocks.Continue → mixin.blocks.End →
mixin.capture.Begin → mixin.capture.End →
EventTransmit →                              ← subStep1 BLOCKS here (event facet)
statement.blocks.Begin → statement.blocks.Continue → statement.blocks.End →
statement.capture.Begin → statement.capture.End →
statement.End → statement.Complete
```

**BLOCK** (AndThen — steps 2, 5, 9):
```
Created → block.execution.Begin → block.execution.Continue →
block.execution.End → statement.End → statement.Complete
```

**YIELD** (YieldAssignment — steps 4, 8, 11):
```
Created → facet.initialization.Begin → facet.initialization.End →
facet.scripts.Begin → facet.scripts.End →
statement.End → statement.Complete
```

**EVENT** (event lifecycle for CountDocuments event):
```
event.Created → event.Dispatched → event.Processing → event.Completed
```

For non-event facets, `EventTransmit` is a pass-through — the step continues immediately. For event facets like CountDocuments, `EventTransmit` creates an `EventDefinition` and **blocks** until a `StepContinue` signal is received from an external agent.

### Iteration-by-Iteration Trace

Execution is split into two evaluator runs, separated by external microservice processing.

---

#### Evaluator Run 1 — Internal Processing

**Iteration 0 — Setup and cascade**

> **Note:** Yield steps are created **lazily** — see `spec/30_runtime.md` §11.1.

Steps are created in a cascade. Each parent creates its children within the same iteration. Yield steps are deferred until their dependencies are committed.

| Step            | State progression |
|-----------------|-------------------|
| AddWorkflow_run | Created → ... → statement.blocks.Begin (creates block_AW) → statement.blocks.Continue (BLOCKED — waiting on block_AW) |
| block_AW        | Created → block.execution.Begin (creates addition) → block.execution.Continue (BLOCKED — yield_AW deferred) |
| addition        | Created → ... → statement.blocks.Begin (creates block_Adder) → statement.blocks.Continue (BLOCKED) |
| block_Adder     | Created → block.execution.Begin (creates s1, s2) → block.execution.Continue (BLOCKED — yield_Adder deferred) |
| s1              | Created → ... → statement.blocks.Begin (creates block_s1) → statement.blocks.Continue (BLOCKED) |
| s2              | Created → ... → statement.Complete ✓ |
| block_s1        | Created → block.execution.Begin (creates subStep1) → block.execution.Continue (BLOCKED — yield_SF deferred) |
| subStep1        | Created → facet.initialization.Begin → ... → **EventTransmit** (creates EventDefinition for "example.4.CountDocuments") → **BLOCKED** (waiting for external agent) |

**Commit 0 contents:**
- 8 created steps (yield steps deferred)
- 1 created event: `CountDocuments` (state: `event.Created`, payload: `{input: "some.file"}`)
- s2 reaches `statement.Complete`
- subStep1 parked at `EventTransmit`

**Iteration 1 — Fixed point**

No step can make progress. subStep1 is blocked on an external event. All other blocked steps are waiting (directly or transitively) on subStep1. The evaluator pauses.

> **Cache state at pause:** 8 steps and the CountDocuments event are persisted in the database. The evaluator has no more internal work to do.

---

#### External Microservice Processing

The CountDocuments event is now visible in the persistence layer. An external **CountDocuments agent** (microservice) participates in the event lifecycle:

```
┌──────────────┐     poll      ┌───────────────────┐     process     ┌──────────────┐
│  Evaluator   │ ───commit───▶ │   Persistence     │ ◀───poll──────  │ CountDocs    │
│  (paused)    │               │   (Database)      │                 │ Agent        │
└──────────────┘               └───────────────────┘                 └──────────────┘
                                        │                                   │
                                        │  1. event.Created                 │
                                        │  ◀──── agent polls ──────────────┘
                                        │                                   │
                                        │  2. event.Dispatched              │
                                        │  ────── agent claims ────────────▶│
                                        │                                   │
                                        │  3. event.Processing              │
                                        │         (agent does work:         │
                                        │          counts docs in           │
                                        │          "some.file")             │
                                        │                                   │
                                        │  4. event.Completed               │
                                        │  ◀──── agent writes result ──────┘
                                        │        + StepContinue for
                                        │          subStep1.id
                                        │
```

**Event state transitions:**

| Transition | Actor | What happens |
|------------|-------|-------------|
| event.Created → event.Dispatched | CountDocuments agent polls database, finds event, claims it | Agent acquires lock on stepId (MongoDB: unique partial index on running events) |
| event.Dispatched → event.Processing | CountDocuments agent | Agent begins processing: counts documents in "some.file" |
| event.Processing → event.Completed | CountDocuments agent | Agent writes result payload and sends `StepContinue` event for subStep1's step ID |

The `StepContinue` event signals the evaluator that subStep1 can resume.

---

#### Evaluator Run 2 — Resumption

The evaluator receives the `StepContinue` event for subStep1. Execution resumes.

**Iteration 2 — subStep1 resumes past EventTransmit**

subStep1 was parked at `EventTransmit`. With `StepContinue` received, it can now advance.

| Step     | State progression |
|----------|-------------------|
| subStep1 | EventTransmit → statement.blocks.Begin → statement.blocks.Continue → statement.blocks.End → statement.capture.Begin → ... → statement.Complete ✓ |

**Commit 2:** subStep1 reaches `statement.Complete`.

**Iteration 3 — yield_SF created and completes**

subStep1 is committed as complete. `BlockExecutionContinue` for block_s1 creates yield_SF (lazy creation). yield_SF evaluates `subStep1.input + 10` and completes in the same iteration.

| Step       | State progression |
|------------|-------------------|
| yield_SF   | **Created** → facet.initialization.Begin → ... → statement.Complete ✓ (lazy creation) |
| block_s1   | block.execution.Continue — still waiting (yield_SF not yet committed) |

**Commit 3:** yield_SF created and reaches `statement.Complete`. Total: 9 steps.

**Iteration 4 — block_s1 completes**

All children of block_s1 (subStep1, yield_SF) are now complete.

| Step     | State progression |
|----------|-------------------|
| block_s1 | block.execution.Continue → block.execution.End → statement.End → statement.Complete ✓ |

**Commit 4:** block_s1 reaches `statement.Complete`.

**Iteration 5 — s1 unblocks**

block_s1 is complete, so s1 advances past `statement.blocks.Continue`.

| Step | State progression |
|------|-------------------|
| s1   | statement.blocks.Continue → statement.blocks.End → statement.capture.Begin → ... → statement.Complete ✓ |

**Commit 5:** s1 reaches `statement.Complete` with `output` from yield_SF.

**Iteration 6 — yield_Adder created and completes**

s1 and s2 are both complete. `BlockExecutionContinue` for block_Adder creates yield_Adder (lazy creation). yield_Adder evaluates `s1.output + s2.input` and completes in the same iteration.

| Step         | State progression |
|--------------|-------------------|
| yield_Adder  | **Created** → facet.initialization.Begin → ... → statement.Complete ✓ (lazy creation) |
| block_Adder  | block.execution.Continue — still waiting |

**Commit 6:** yield_Adder created and reaches `statement.Complete`. Total: 10 steps.

**Iteration 7 — block_Adder completes**

| Step        | State progression |
|-------------|-------------------|
| block_Adder | block.execution.Continue → block.execution.End → statement.End → statement.Complete ✓ |

**Commit 7:** block_Adder reaches `statement.Complete`.

**Iteration 8 — addition unblocks**

| Step     | State progression |
|----------|-------------------|
| addition | statement.blocks.Continue → statement.blocks.End → statement.capture.Begin → ... → statement.Complete ✓ |

**Commit 8:** addition reaches `statement.Complete` with `sum` from yield_Adder.

**Iteration 9 — yield_AW created and completes**

`BlockExecutionContinue` for block_AW creates yield_AW (lazy creation). yield_AW evaluates `addition.sum` and completes in the same iteration.

| Step     | State progression |
|----------|-------------------|
| yield_AW | **Created** → facet.initialization.Begin → ... → statement.Complete ✓ (lazy creation) |
| block_AW | block.execution.Continue — still waiting |

**Commit 9:** yield_AW created and reaches `statement.Complete`. Total: 11 steps (all steps now exist).

**Iteration 10 — block_AW completes**

| Step     | State progression |
|----------|-------------------|
| block_AW | block.execution.Continue → block.execution.End → statement.End → statement.Complete ✓ |

**Commit 10:** block_AW reaches `statement.Complete`.

**Iteration 11 — AddWorkflow_run completes**

| Step            | State progression |
|-----------------|-------------------|
| AddWorkflow_run | statement.blocks.Continue → statement.blocks.End → statement.capture.Begin → ... → statement.Complete ✓ |

**Commit 11:** AddWorkflow_run reaches `statement.Complete`. Workflow is done.

**Iteration 12 — Fixed point.** Evaluator terminates.

**Workflow output:** `result = addition.sum = s1.output + s2.input`

### Concurrency Summary

| Iteration | Steps Created | Steps progressed | Notes |
|-----------|---------------|-----------------|-------|
| 0 | 8 | AddWorkflow_run, block_AW, addition, block_Adder, s1, s2, block_s1, subStep1 (event-blocked) | Setup: 8 steps created (yields deferred); **s1 ‖ s2** run in parallel; subStep1 blocks at EventTransmit |
| 1 | — | (none) | Fixed point — evaluator pauses, cache committed to DB |
| — | — | *External: CountDocuments agent processes event* | event.Created → Dispatched → Processing → Completed + StepContinue |
| 2 | — | subStep1 | Resumes past EventTransmit → statement.Complete |
| 3 | +1 (yield_SF) | yield_SF | Lazy creation: yield_SF created and completes |
| 4 | — | block_s1 | All children complete |
| 5 | — | s1 | Child block complete |
| 6 | +1 (yield_Adder) | yield_Adder | Lazy creation: yield_Adder created and completes |
| 7 | — | block_Adder | All children complete |
| 8 | — | addition | Child block complete |
| 9 | +1 (yield_AW) | yield_AW | Lazy creation: yield_AW created and completes |
| 10 | — | block_AW | All children complete |
| 11 | — | AddWorkflow_run | Child block complete — workflow done |
| 12 | — | (none) | Fixed point — evaluator terminates |

**True parallelism:** s1 ‖ s2 in iteration 0 (siblings with no data dependency). However, s1 blocks at `statement.blocks.Continue` while s2 completes immediately. s2 finishes in iteration 0; s1 does not complete until iteration 5 (waiting for the external event chain: CountDocuments agent → subStep1 → yield_SF → block_s1).

### Cache Commit Points

Each commit atomically persists all `IterationChanges` (created steps, updated steps, created events, updated events) to the database.

| Commit | What is persisted | Significance |
|--------|------------------|--------------|
| 0 | 8 steps (s2 complete, subStep1 at EventTransmit) + 1 event (CountDocuments, state: event.Created) | **Event becomes visible to external agents.** Yield steps deferred. This is the handoff point — the CountDocuments microservice can now poll and find this event. |
| 1 | (no changes — fixed point) | Evaluator pauses. All internal work exhausted. |
| 2 | subStep1 → statement.Complete | First commit after external event completes. Unblocks the yield chain. |
| 3 | +1 step (yield_SF created and completes) | Lazy yield creation — total: 9 steps |
| 4–5 | block_s1, s1 complete | Hierarchy unwinding |
| 6 | +1 step (yield_Adder created and completes) | Lazy yield creation — total: 10 steps |
| 7–8 | block_Adder, addition complete | Hierarchy unwinding |
| 9 | +1 step (yield_AW created and completes) | Lazy yield creation — total: 11 steps (all steps now exist) |
| 10–11 | block_AW, AddWorkflow_run complete | Final hierarchy unwinding — workflow done |

### Microservice Interaction Model

```
     Evaluator                  Database                  CountDocuments Agent
        │                          │                              │
        │── commit 0 ─────────▶   │                              │
        │   (8 steps + event)      │                              │
        │                          │  ◀── poll for events ────── │
        │                          │                              │
        │   (paused at             │  ── event.Created found ──▶ │
        │    fixed point)          │                              │
        │                          │  ◀── claim: Dispatched ──── │
        │                          │                              │
        │                          │  ◀── update: Processing ─── │
        │                          │         (doing work...)      │
        │                          │                              │
        │                          │  ◀── update: Completed ──── │
        │                          │     + StepContinue           │
        │                          │       (subStep1.id)          │
        │                          │                              │
        │  ◀── StepContinue ────── │                              │
        │                          │                              │
        │── commit 2 ─────────▶   │                              │
        │   (subStep1 complete)    │                              │
        │                          │                              │
        │── commits 3-11 ────────▶ │                              │
        │   (unwinding hierarchy)  │                              │
        │                          │                              │
        │── commit 12 (done) ────▶ │                              │
        │                          │                              │
```

### Comparison to Example 3

| Aspect | Example 3 | Example 4 |
|--------|-----------|-----------|
| Steps at iteration 0 | 8 (yields deferred) | 8 (yields deferred, same structure) |
| Total steps (final) | 11 | 11 (same structure) |
| subStep1 facet | `Value` (regular) | `CountDocuments` (event) |
| subStep1 at EventTransmit | Pass-through | **Blocks** — waits for external agent |
| Evaluator runs | 1 continuous run | 2 runs separated by external processing |
| Total iterations | 11 (0–10) | 13 (0–1, pause, 2–12) |
| External actors | None | CountDocuments microservice |
| Events created | 0 | 1 (CountDocuments) |
| Cache commits with events | 0 | 1 (commit 0 includes the event) |

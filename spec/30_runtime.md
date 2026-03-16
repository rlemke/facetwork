
# AFL Runtime Specification

This document defines the **formal execution semantics** of the AFL (Agent Workflow Language) runtime.
It is the authoritative contract governing correctness, determinism, persistence, and agent interaction.

This specification applies regardless of implementation language, database technology, or deployment topology.

---

## 1. Runtime Overview

The AFL runtime is responsible for executing workflows compiled from AFL source.
Execution is **dependency-driven**, **iterative**, **distributed**, and **restart-safe**.

The runtime consists of:
- an **Evaluator** that orchestrates execution,
- an **external persistence API** that encapsulates database access,
- an **event system** that dispatches work to agents.

The runtime MUST support:
- parallel execution,
- forward references,
- resumable execution,
- and idempotent recovery from failure.

---

## 2. Compiler Output and Runtime Mapping

The compiler produces a **compiled AST** describing:
- workflows,
- steps,
- facets,
- mixins,
- blocks (`andThen`),
- dependency relationships.

The runtime maps this AST into:
- runtime execution structures, and
- persistent representations stored via the persistence API.

The compiled AST is the **source of truth** for workflow structure.
Persistence is the **source of truth** for execution state.

---

## 3. Persistence Abstraction Boundary

The Evaluator MUST NOT directly access the database.

All persistence operations are performed through an **external persistence API**, which:
- hides database implementation details,
- enforces concurrency and locking semantics,
- provides atomicity guarantees.

The evaluator interacts with persistence exclusively through this API.

---

## 4. Persistence API Requirements

The persistence API MUST support:

### Step and Event Access
- Fetch a step by persistent step ID
- Fetch an event by persistent event ID
- Persist newly created steps
- Persist step state transitions
- Persist events and event state transitions

### Block-Based Access
- Fetch all persisted steps belonging to a block ID
- Fetch all persisted blocks belonging to a containing step ID

---

## 5. Step Lifecycle Phases (Conceptual)

Each step progresses through **three conceptual phases**.
These are **derived**, not persisted.

1. **Creation Eligible**
   - Step exists in the compiled AST
   - All dependencies are `state.statement.Complete`

2. **Execution Eligible**
   - Step exists in persistence
   - Step is not complete or errored
   - Step is not waiting on an external event

3. **Execution Scheduled**
   - Step selected at an iteration boundary for evaluation

---

## 6. Step State Machine

Each step progresses through the following states:

state.statement.Created

state.facet.initialization.Begin
state.facet.initialization.End

state.facet.scripts.Begin
state.facet.scripts.End

state.statement.scripts.Begin
state.statement.scripts.End

state.mixin.blocks.Begin
state.mixin.blocks.Continue
state.mixin.blocks.End

state.mixin.capture.Begin
state.mixin.capture.End

state.statement.capture.Begin
state.statement.capture.End

state.statement.blocks.Begin
state.statement.blocks.Continue
state.statement.blocks.End

state.block.execution.Begin
state.block.execution.Continue
state.block.execution.End

state.EventTransmit

state.statement.End
state.statement.Complete
state.statement.Error

### State Guarantees
- Only **one agent** may transition a step at a time
- Once a step reaches `state.statement.Complete`, it is **immutable**
- Any unrecoverable failure transitions the step to `state.statement.Error`

> See [31_runtime_impl.md](31_runtime_impl.md) §4.3 for Python transition tables and StateChanger class hierarchy.

---

## 7. Attribute Evaluation Semantics

During `state.facet.initialization.Begin`:

- All attribute expressions MUST be evaluated
- Results MUST be stored in the step’s persistent facet structure
- Expressions MAY include arithmetic, grouping, and references

### Call-Site Mixin Argument Evaluation

> **Implemented** (v0.21.0) — `FacetInitializationBeginHandler` in `afl/runtime/handlers/initialization.py`.

A step’s call expression MAY include **call-site mixins**: `with MixinName(args) as alias`.

The compiled AST stores these in the call’s `mixins` list:

```json
{
  "call": {
    "target": "FacetName",
    "args": [...],
    "mixins": [
      {"type": "MixinCall", "target": "RetryPolicy", "args": [{"name": "max_retries", "value": {"type": "Int", "value": 5}}], "alias": "retry"},
      {"type": "MixinCall", "target": "AlertConfig", "args": [{"name": "channel", "value": {"type": "String", "value": "alerts"}}]}
    ]
  }
}
```

During `FACET_INIT_BEGIN`, after evaluating the step’s own call args, the runtime MUST evaluate mixin args with these rules:

1. **Evaluation order:** Call args are evaluated first, then mixins in declaration order.

2. **Aliased mixins** (`with Foo(x=1) as alias`): The evaluated mixin args are stored as a **nested dict** under the alias key. The handler receives `params["alias"] = {"x": 1}`.

3. **Non-aliased mixins** (`with Foo(x=1)`): The evaluated mixin args are **flat-merged** into the step params. A mixin arg MUST NOT override an explicit call arg with the same name.

4. **Dependencies:** Mixin args MAY contain step references. The dependency graph MUST scan mixin args in addition to call args when computing step dependencies.

5. **Implicit fallback:** Implicit defaults still apply for params not provided by either the call args or the mixin args.

**Example:**

```afl
ingest = IngestReading(sensor_id = $.id) with RetryPolicy(max_retries = 5) as retry
```

The handler receives:

```python
params = {
    "sensor_id": "sensor_001",   # from call arg
    "retry": {"max_retries": 5}, # from aliased mixin
}
```

### Dependency Enforcement

A step MAY NOT evaluate expressions that reference another step unless the referenced step is in `state.statement.Complete`.

#### Parallel Evaluation

Steps with no blocking references MAY evaluate concurrently.

---

## 8. Block Execution Semantics

Blocks (`andThen`) are first-class execution units.

Rules:
- A block MAY execute only after its containing step reaches:

state.statement.blocks.Begin

- Blocks within the same phase MAY execute concurrently
- Block completion contributes to step completion
- A step MUST NOT transition to `state.EventTransmit` until all required blocks complete

### 8.1 EventTransmit Blocking Semantics

> **Implemented** — see `spec/70_examples.md` Example 4 for the authoritative execution trace.

The `EventTransmit` state has **two distinct behaviors** depending on whether the step's facet is an event facet:

- **Non-event facets** (`FacetDecl`): EventTransmit is a **pass-through**. The handler calls `request_state_change(True)` and the step continues immediately to `state.statement.blocks.Begin`.

- **Event facets** (`EventFacetDecl`): EventTransmit **creates an `EventDefinition`** with payload built from step attributes, adds it to `IterationChanges`, and then calls `request_state_change(False)`. The step **blocks** at `EventTransmit` and does NOT advance. This causes the evaluator to eventually reach a fixed point (see §10.1).

The handler MUST resolve the facet type by looking up the facet declaration in the full Program AST (see §11.1).

### 8.2 Statement-Level Block Creation

> **Implemented** — `StatementBlocksBeginHandler` creates blocks for any step with an `andThen` body.

The `StatementBlocksBeginHandler` MUST create blocks for **any step** that has an `andThen` body — not just the workflow root. The handler checks two sources for block definitions:

1. **Statement-level block**: The step's statement in the compiled AST has an inline `andThen` block (e.g., `s1 = SomeFacet(input = $.a) andThen { ... }`).
2. **Facet-level block**: The facet declaration referenced by the step has an `andThen` body (e.g., `facet Adder(...) andThen { ... }`).

**Precedence rule:** Statement-level blocks take precedence over facet-level blocks. If a step has both, the statement-level block is used.

For each block found, the handler creates a `StepDefinition` with `ObjectType.AND_THEN`, with `container_id` set to the current step.

### 8.3 Block AST Resolution

> **Implemented** — `get_block_ast()` resolves workflow root, statement-level, and facet-level block ASTs.

When a block step enters `BlockExecutionBegin`, the handler MUST resolve the correct AST for the block's contents:

- **Workflow root block**: The AST is the workflow declaration's `andThen` body (from `WorkflowDecl.body`).
- **Facet-level block**: The AST is the facet declaration's `andThen` body (from `FacetDecl.body`), resolved by looking up the facet by name in the Program AST.
- **Statement-level block**: The AST is the inline `andThen` body attached to the step statement in the compiled AST (from `StepStmt.body`).

The evaluator MUST have access to the full Program AST to resolve these references (see §11.1).

### 8.4 Catch Block Semantics

When a step encounters an error and the step's facet or statement has a `catch` clause, the runtime MUST intercept the error before transitioning to `state.statement.Error`. Instead, the step enters the catch phase:

```
state.catch.Begin → state.catch.Continue → state.catch.End → state.statement.capture.Begin
```

**Catch interception** occurs at two points:
1. When child blocks error (during `state.statement.blocks.Continue`)
2. When event handler processing errors

**Catch execution rules:**
- The runtime MUST store error information as pseudo-returns on the step: `error` (the error message) and `error_type` (the error class name). These are accessible via `step.error` and `step.error_type` in catch block expressions.
- **Simple catch** (`catch { ... }`): Creates a single catch sub-block.
- **Conditional catch** (`catch when { case condition => { ... } case _ => { ... } }`): Evaluates conditions and creates sub-blocks for each matching case. A default case (`case _ =>`) is **required**.
- Catch sub-blocks use `ObjectType.AND_CATCH` and follow the same block execution pattern as `andThen` blocks.
- If all catch sub-blocks complete successfully, the step resumes normal flow at `state.statement.capture.Begin`.
- If any catch sub-block itself errors, the step transitions to `state.statement.Error` (catch failure propagates).

> See [31_runtime_impl.md](31_runtime_impl.md) §10 for catch handler implementations.

### 8.5 Schema Instantiation Semantics

Schema instantiation steps (`SchemaInstantiation`) use a simplified state machine:

```
state.statement.Created → state.facet.initialization.Begin → state.facet.initialization.End → state.statement.End → state.statement.Complete
```

Schema instantiation:
- Evaluates arguments during `state.facet.initialization.Begin`
- Stores evaluated values as **returns** (accessible via `step.fieldName`), not as params
- Skips all script, mixin, event, and block phases
- Completes immediately after initialization

> See [31_runtime_impl.md](31_runtime_impl.md) §4.3 for the `SCHEMA_TRANSITIONS` table.

---

## 9. Iterative Execution Model

Execution proceeds in **iterations**.

### Iteration Rules

Within an iteration:
- Step evaluation occurs entirely **in memory**
- State transitions and data mutations are accumulated in memory
- No persistence writes occur mid-iteration

### Iteration Completion

An iteration ends when:
- no additional steps can advance due to dependencies

At iteration completion:
1. All in-memory state changes are atomically committed
2. All generated events are published
3. Control returns to the system

> See [31_runtime_impl.md](31_runtime_impl.md) §2 for the `Evaluator` class and iteration loop implementation.

---

## 10. Iteration Progression

After an iteration completes:

- The evaluator re-evaluates all steps
- Steps previously blocked by dependencies MAY become eligible
- Newly eligible steps are scheduled in the **next iteration**

Execution continues until a **fixed point** is reached.

### 10.1 Fixed Point and Event Pause

> **Implemented** — see `spec/70_examples.md` Example 4, Iteration 1.

A fixed point occurs when no step can advance in an iteration. When a fixed point is reached and **at least one step is blocked at `state.EventTransmit`** (waiting for an external event), the evaluator MUST:

1. Atomically commit all accumulated changes (steps and events) to persistence.
2. **Pause** execution. Events created during prior iterations become visible to external agents in the persistence layer.
3. Yield control to the external system.

The evaluator does NOT terminate — it pauses and waits for a resumption signal (see §10.2).

### 10.2 Multi-Run Execution Model

> **Implemented** — see `spec/70_examples.md` Example 4 for the full two-run trace.

The evaluator supports **distributed multi-run execution**:

1. **Run 1** — The evaluator processes all internal work until reaching a fixed point. Steps blocked at `EventTransmit` have their events committed to persistence.
2. **Pause** — External agents (microservices) poll the persistence layer, discover events, process them, and send `StepContinue` signals (see §12.1).
3. **Run N** — The evaluator resumes when a `StepContinue` event is received. The blocked step advances past `EventTransmit` and execution continues.

State is fully persisted at each pause boundary. The evaluator MUST be restartable — it reconstructs execution state entirely from persistence.

A workflow MAY require multiple pause/resume cycles if multiple event facets are encountered at different points in the execution graph.

---

## 11. Step Creation Responsibilities

Using the compiled AST, the evaluator MUST:

1. Identify steps not yet persisted
2. Verify all dependencies are complete
3. Create persistent step records
4. Initialize them in `state.statement.Created`

Steps MUST NOT be created prematurely.

### 11.1 Lazy Yield Creation

> **Implemented** — yield steps are created by `BlockExecutionContinueHandler._create_ready_steps()` only after all non-yield statements in the block are terminal.

Yield steps (`YieldAssignment`) are created **lazily** — they are deferred until **all non-yield statements** in the same block are terminal (complete or error), regardless of the yield's explicit dependencies.

This means:
- In a block with steps `s1`, `s2`, and `yield F(output = s1.x)`, the yield is **not** created when `s1` completes — it waits until `s2` also completes, even though `s2` is not an explicit dependency of the yield.
- The yield step is created in the first iteration where all non-yield statements (`s1` and `s2`) are committed as `statement.Complete`.
- Because the yield's dependencies are already satisfied at creation time, the yield step runs to `statement.Complete` in the same iteration it is created.

**Rationale:** Yield statements are not regular dependency-graph participants. They represent the block's output and should only execute after the block's regular work is fully done. The `DependencyGraph.get_ready_statements()` method enforces this by checking that all non-yield statement IDs are in the completed set before including any yield in the ready list.

**Effect on step counts:** The total number of steps in a workflow grows over iterations as yield steps are created. For example, a workflow with 8 total steps may have only 6 steps after iteration 0, with the remaining 2 yield steps created in later iterations.

**Effect on iteration counts:** Lazy yield creation does not change the total number of iterations. Yield steps complete in the same iteration they are created, so no additional iterations are needed.

### 11.2 Multi-Block Body Index

> **Implemented** — `StatementBlocksBeginHandler._create_block_steps()` assigns `statement_id="block-N"` for multi-block workflows.

When a workflow or facet has multiple `andThen` blocks, each block step is assigned a `statement_id` of `"block-N"` (where N is the zero-based index into the body list). This allows `get_block_ast()` to resolve the correct body element for each block.

### 11.3 Foreach Block Execution

> **Implemented** — `BlockExecutionBeginHandler._process_foreach()` creates sub-blocks per array element.

When a block has a `foreach` clause (`andThen foreach var in expr { ... }`), the `BlockExecutionBegin` handler:

1. Evaluates the iterable expression using the current evaluation context.
2. Creates one sub-block step per array element, each with:
   - `object_type=AND_THEN`
   - `block_id` set to the parent foreach block
   - `foreach_var` and `foreach_value` set for the iteration variable binding
3. Caches the body AST (block without foreach clause) for each sub-block.
4. Skips normal `DependencyGraph` creation — sub-blocks handle their own dependencies.

The `BlockExecutionContinue` handler detects foreach blocks and tracks sub-block completion directly (all sub-blocks must reach `statement.Complete`).

The `FacetInitializationBegin` handler propagates `foreach_var`/`foreach_value` from the containing block step to the `EvaluationContext`, making the iteration variable available in child step expressions.

Empty iterables produce no sub-blocks and the foreach block completes immediately.

### 11.4 Facet Definition Resolution

> **Implemented** — `get_facet_definition()` performs qualified and short-name lookups across the Program AST.

The evaluator MUST have access to the **full Program AST** (not just the `WorkflowDecl`) to look up `FacetDecl` and `EventFacetDecl` declarations by name. This is required for:

- **EventTransmit** (§8.1): The handler must determine whether a step's facet is an `EventFacetDecl` to decide between pass-through and blocking behavior.
- **Block AST resolution** (§8.3): The handler must look up facet-level `andThen` bodies when a step calls a facet that has its own block.
- **Statement-level block creation** (§8.2): The handler must distinguish between statement-level and facet-level blocks.

The evaluator's `ExecutionContext` MUST provide a `get_facet_definition(facet_name)` method that returns the full facet declaration node from the Program AST.

### 11.5 Block AST Cache

> **Implemented** — `ExecutionContext._block_ast_cache` provides direct AST lookup for foreach sub-blocks and multi-block bodies.

The `ExecutionContext` maintains a `_block_ast_cache` that maps block step IDs to their AST bodies. This cache is checked first in `get_block_ast()`, before traversing the containment hierarchy. It is used by:

- **Foreach sub-blocks** (§11.3): Each sub-block's body AST is cached at creation time.
- **Multi-block bodies** (§11.2): `_select_block_body()` uses the block's `statement_id` to index into the body list.

---

## 12. Event Lifecycle Semantics

Events are persistent entities with at least the following lifecycle:

event.Created
event.Dispatched
event.Processing
event.Completed
event.Error

### Event Guarantees
- An event is processed by exactly **one agent at a time**
- Failed events MAY be retried
- Event completion MAY unblock dependent steps

### 12.1 StepContinue Events

> **Implemented** — see `spec/70_examples.md` Example 4 for the full interaction model.

`StepContinue` is a **system event type** that resumes steps blocked at `state.EventTransmit`. It is the mechanism by which external agents signal that event processing is complete.

**Event structure:**
- `event_type`: `"StepContinue"`
- `payload`: `{ "step_id": <StepId of the blocked step> }`

**Processing flow:**
1. An external agent completes processing an event and writes the result to persistence.
2. The agent sends a `StepContinue` event targeting the step that is blocked at `EventTransmit`.
3. The evaluator receives the `StepContinue` event (via polling or notification).
4. The evaluator finds the matching step, verifies it is at `state.EventTransmit`, and allows it to transition past `EventTransmit` to `state.statement.blocks.Begin`.

**Idempotency:** Processing a `StepContinue` for a step that has already advanced past `EventTransmit` MUST be a no-op. Duplicate `StepContinue` events MUST NOT cause errors.

---

## 13. Failure and Retry Policy

- `state.statement.Error` is terminal by default
- Retry behavior is **event-level**
- Evaluator MUST treat retries as idempotent
- No implicit evaluator-level retries are permitted

---

## 14. Idempotency and Restart Safety

All runtime operations MUST be idempotent, including:
- step creation
- state transitions
- event publication

The system MUST tolerate:
- evaluator restarts
- agent restarts
- duplicate execution attempts

without producing duplicate side effects.

---

## 15. Determinism Guarantees

- Dependency resolution is deterministic
- Final outputs are deterministic for identical inputs
- Ordering of concurrent execution is explicitly undefined
- Execution MUST converge to the same final persisted state

---

## 16. Agent Authority Boundaries

Agents MAY:
- read the step associated with their event
- update that step
- signal completion or error

Agents MUST NOT:
- modify other steps
- create steps
- alter workflow structure
- bypass evaluator control

---

## 17. Versioning and Compatibility

All persisted artifacts MUST include:
- `workflow_version`
- `step_schema_version`
- `runtime_version`

The evaluator MUST refuse or safely handle incompatible versions.

---

## 18. Observability Requirements

The runtime MUST emit structured telemetry for:
- step state transitions
- dependency resolution
- iteration boundaries
- event publication

Telemetry MUST NOT affect execution semantics.

---

## 19. Execution Contract Summary

> The AFL runtime executes workflows as deterministic, dependency-driven, iterative evaluations using in-memory execution, abstracted persistence, explicit state machines, and strict agent boundaries.

This contract is **non-negotiable**.

---

## 20. Non-Goals (v1)

The following are explicitly out of scope:
- speculative execution
- dynamic workflow mutation
- agent-created steps
- implicit retries

These MAY be added in future versions.

---

## 21. Examples

### 21.1 Initialization, Dependency-Driven Evaluation, and Yield Capture

```afl
namespace test.one {

  facet Value(input: Long, output: Long)

  workflow TestOne(input: Long = 1) => (output: Long) andThen {
    s1 = Value(input = $.input + 1)
    s2 = Value(input = s1.input + 1)
    yield TestOne(output = s2.input + 1)
  }
}
```

**Execution Walkthrough**

1. **Workflow initialization**
   - Before s1 can be evaluated, the workflow step TestOne MUST be initialized with its attributes.
   - In this example, TestOne.input takes its default value:
   - `TestOne.input = 1`

2. **Step s1 evaluation**
   - s1 has no blocking step references (it references `$.input`, which is local workflow input).
   - The evaluator evaluates:
   - `s1.input = $.input + 1 = 1 + 1 = 2`
   - The evaluated value is stored in the persistent representation of step s1.

3. **Step s2 evaluation (dependency enforcement)**
   - s2 references `s1.input`, so s1 MUST be in `state.statement.Complete` before s2 begins evaluation.
   - Once s1 is complete:
   - `s2.input = s1.input + 1 = 2 + 1 = 3`
   - The evaluated value is stored in the persistent representation of step s2.

4. **Yield capture and merge into the containing step**
   - The `yield TestOne(...)` does not mutate TestOne immediately.
   - Yield capture is performed during the containing step's capture phase:
   - `state.statement.capture.Begin`
   - This deferred capture is REQUIRED because:
     - a containing step may have multiple blocks (andThen) and multiple yields,
     - the containing step must remain immutable while blocks are executing,
     - yields must only be merged once all relevant blocks have completed.

5. **Yield attribute merge**
   - After all steps in the andThen block are complete, the evaluator collects the yield result and merges yielded attributes into the containing step (TestOne):
   - `TestOne.output = s2.input + 1 = 3 + 1 = 4`
   - The merge produces the final persisted attribute set for TestOne, after which TestOne may transition to `state.statement.Complete`.

**Key Guarantees Demonstrated**

- Workflow inputs (`$.input`) must be initialized before dependent steps can evaluate.
- Dependency-driven scheduling: s2 cannot evaluate until s1 is complete.
- Yield capture is deferred to `state.statement.capture.Begin` to preserve immutability during block execution.
- Yield results are merged into the containing step only after block completion.

---

### 21.2 Parallel Steps, Fan-in Dependency, and Iteration Eligibility

```afl
namespace test.two {

  facet Value(input: Long, output: Long)

  workflow TestTwo(input: Long = 1) => (output: Long) andThen {
    a = Value(input = $.input + 1)
    b = Value(input = $.input + 10)
    c = Value(input = a.input + b.input)
    yield TestTwo(output = c.input)
  }
}
```

**Execution Walkthrough**

1. **Workflow initialization**
   - The workflow step TestTwo MUST be initialized before any block step may evaluate.
   - With the default:
   - `TestTwo.input = 1`

2. **Parallel evaluation (a and b)**
   - a references only `$.input` and has no blocking step references.
   - `a.input = $.input + 1 = 1 + 1 = 2`
   - b references only `$.input` and has no blocking step references.
   - `b.input = $.input + 10 = 1 + 10 = 11`
   - Because a and b have no inter-dependencies, they MAY be evaluated concurrently in the same iteration.

3. **Fan-in dependency (c)**
   - c references both `a.input` and `b.input`.
   - Therefore, c MUST NOT begin evaluation until:
     - a is in `state.statement.Complete`, and
     - b is in `state.statement.Complete`.
   - Once both are complete:
   - `c.input = a.input + b.input = 2 + 11 = 13`

4. **Yield capture and merge**
   - Yield capture occurs in the containing step (TestTwo) during:
   - `state.statement.capture.Begin`
   - The yield assigns:
   - `TestTwo.output = c.input = 13`
   - Yield results are merged only after all steps in the block have completed.

**Mapping to the Iteration Model**

The evaluator progresses through iterations based on dependency eligibility.

- **Iteration 1 (eligible at start)**
  - Eligible steps: a, b
  - Actions:
    - Evaluate a and b (in memory)
    - They reach completion (or an event boundary), and their results become available
  - c is NOT eligible yet because its dependencies were not complete at the beginning of the iteration.

- **Iteration boundary**
  - The evaluator commits in-memory updates and/or publishes events (per the runtime contract).
  - The evaluator re-evaluates step eligibility.

- **Iteration 2 (newly eligible)**
  - Newly eligible step: c
  - Because both a and b are now complete, c becomes eligible and may execute.

This illustrates the core rule:

> A step that becomes unblocked by dependencies in one iteration is scheduled in a subsequent iteration, never mid-iteration.

**Key Guarantees Demonstrated**

- Independent steps may run concurrently.
- Fan-in steps wait for all dependencies.
- Eligibility expands only between iterations.
- Yield merge remains deferred until block completion to preserve step immutability.

---

### 21.3 Multiple andThen Blocks

> **Note:** Steps can have the same name if in different blocks (`andThen`), but must be unique within a block. All blocks will be executed concurrently. The yields will be performed after the block is executed by the TestThree step.

```afl
namespace test.three {

  facet Value(input: Long, output: Long)

  workflow TestThree(input: Long = 1) => (output1: Long, output2: Long, output3: Long) andThen {
    a = Value(input = $.input + 1)
    b = Value(input = $.input + 10)
    c = Value(input = a.input + b.input)
    yield TestThree(output1 = c.input)
  } andThen {
    a = Value(input = $.input + 1)
    b = Value(input = $.input + 10)
    c = Value(input = a.input + b.input)
    yield TestThree(output2 = c.input)
  } andThen {
    a = Value(input = $.input + 1)
    b = Value(input = $.input + 10)
    c = Value(input = a.input + b.input)
    yield TestThree(output3 = c.input)
  }
}
```

---

> For the Python reference implementation (state changers, handlers, transition tables, source file map), see [31_runtime_impl.md](31_runtime_impl.md).


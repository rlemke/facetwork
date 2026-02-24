
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

---

## 7. Attribute Evaluation Semantics

During `state.facet.initialization.Begin`:

- All attribute expressions MUST be evaluated
- Results MUST be stored in the step’s persistent facet structure
- Expressions MAY include arithmetic, grouping, and references

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


## 22. Appendix: Reference Implementation (Python)

> **Note:** This section contains reference implementation code in Python. This is provided for illustrative purposes only and is **not normative**. The specification sections above define the required behavior; implementations may use any language or approach that satisfies those requirements.

### State Changers Overview

There are three types of state changers for steps, blocks, and yields. Each has:
- A **state selector** (`select_state()`) that determines the next state given the current state
- A **state executor** (`execute_state()`) that performs the work for each state

### 22.1 Step State Execution

**Location:** `afl/runtime/changers/step_changer.py`

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

The full transition table (`STEP_TRANSITIONS` in `afl/runtime/states.py`):

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
    StepState.STATEMENT_CAPTURE_BEGIN:  StepState.STATEMENT_CAPTURE_END,
    StepState.STATEMENT_CAPTURE_END:    StepState.STATEMENT_END,
    StepState.STATEMENT_END:            StepState.STATEMENT_COMPLETE,
}
```

### 22.2 Yield State Execution

**Location:** `afl/runtime/changers/yield_changer.py`

```python
class YieldStateChanger(StateChanger):
    """State changer for YieldAssignment steps.

    Implements minimal state machine — skips blocks, goes directly
    from facet scripts to statement end.
    """

    def select_state(self) -> Optional[str]:
        """Select next state using yield transition table."""
        current = self.step.current_state
        next_state = YIELD_TRANSITIONS.get(current)
        if next_state is None or next_state == current:
            return None
        return next_state

    def execute_state(self, state: str) -> StateChangeResult:
        handler = get_handler(state, self.step, self.context)
        if handler is None:
            self.step.request_state_change(True)
            return StateChangeResult(step=self.step)
        return handler.process()
```

The yield transition table:

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

### 22.2.1 Schema Instantiation State Execution

Schema instantiation steps use a simplified state machine that evaluates arguments and stores them as **returns** (not params):

```python
SCHEMA_TRANSITIONS: dict[str, str] = {
    StepState.CREATED:          StepState.FACET_INIT_BEGIN,
    StepState.FACET_INIT_BEGIN: StepState.FACET_INIT_END,
    StepState.FACET_INIT_END:   StepState.STATEMENT_END,
    StepState.STATEMENT_END:    StepState.STATEMENT_COMPLETE,
}
```

Schema instantiation:
- Evaluates arguments in `FACET_INIT_BEGIN`
- Stores evaluated values as **returns** (accessible via `step.fieldName`)
- Skips all script, mixin, event, and block phases
- Completes immediately after initialization

### 22.3 Block State Execution

**Location:** `afl/runtime/changers/block_changer.py`

```python
class BlockStateChanger(StateChanger):
    """State changer for block steps (AndThen, AndMap, etc.).

    Simplified state machine: Created → BlockExecution → End → Complete.
    """

    def select_state(self) -> Optional[str]:
        """Select next state using block transition table."""
        current = self.step.current_state
        next_state = BLOCK_TRANSITIONS.get(current)
        if next_state is None or next_state == current:
            return None
        return next_state

    def execute_state(self, state: str) -> StateChangeResult:
        handler = get_handler(state, self.step, self.context)
        if handler is None:
            self.step.request_state_change(True)
            return StateChangeResult(step=self.step)
        return handler.process()
```

The block transition table:

```python
BLOCK_TRANSITIONS: dict[str, str] = {
    StepState.CREATED:                    StepState.BLOCK_EXECUTION_BEGIN,
    StepState.BLOCK_EXECUTION_BEGIN:      StepState.BLOCK_EXECUTION_CONTINUE,
    StepState.BLOCK_EXECUTION_CONTINUE:   StepState.BLOCK_EXECUTION_END,
    StepState.BLOCK_EXECUTION_END:        StepState.STATEMENT_END,
    StepState.STATEMENT_END:              StepState.STATEMENT_COMPLETE,
}
```


# AFL Workflow Execution System

This document describes how the AFL (Agent Flow Language) runtime executes workflows. AFL is an event-driven workflow engine that processes workflows defined in `.afl` files through iterative evaluation and state machines.

## Overview

AFL workflows are compiled from `.afl` source files into JSON workflow definitions, then executed through an iterative state machine evaluator. Each step in a workflow progresses through a well-defined sequence of states, with dependency resolution and event handling driving transitions.

## Compilation Phase

AFL source files are compiled by the AFL compiler (`afl/cli.py`):

```
AFL Source → Lark Parser → AST → JSON Emitter → MongoDB / JSON file
```

The compiled output contains:
- **WorkflowDecl** - Named entrypoints with starting steps
- **FacetDecl / EventFacetDecl** - Component templates with typed attributes
- **StepStmt** - Individual operations (statements)
- **AndThenBlock** - Control flow constructs with sequential execution

## Iterative Execution

The `Evaluator` (`afl/runtime/evaluator.py`) orchestrates execution:

```
Evaluator.run()
    └── iterate() until fixed point
            └── Process each eligible step via StateChanger
                    └── Dispatch to StateHandler per state
```

## Step State Machine

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

## StateChanger: The Orchestrator

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

### StateChanger Types

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

## Transition Control

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

## StateHandler Base Class

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

## Block Execution

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

## Dependency Resolution

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

## Mixin Blocks vs Statement Blocks

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

## Capture/Yield System

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

## Completion and Notification

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

## Object Types

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

## Step Definition Structure

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

## Visual Execution Flow

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

## Key Architectural Patterns

1. **State Machine Per Step**: Each step instance follows its own state machine lifecycle
2. **Hierarchical Nesting**: Steps contain blocks, which contain statements, which contain steps (recursive)
3. **Dependency Graph**: Next steps determined by `DependencyGraph` references between statements
4. **Polling/Looping**: `BlockExecutionContinue` and `StatementBlocksContinue` use `set_push_me(True)` to re-queue for polling
5. **Iterative Completion**: When a step completes, the evaluator's next iteration detects newly unblocked steps
6. **Yield Merging**: Capture handlers merge yield step attributes into the containing step's returns

## Key Python Source Files

All source files are located in `afl/runtime/`.

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
- `afl/runtime/evaluator.py` — `Evaluator`, `ExecutionContext`, iteration loop
- `afl/runtime/dependency.py` — `DependencyGraph` from compiled AST
- `afl/runtime/persistence.py` — `PersistenceAPI` protocol
- `afl/runtime/memory_store.py` — In-memory persistence for testing
- `afl/runtime/mongo_store.py` — MongoDB persistence implementation


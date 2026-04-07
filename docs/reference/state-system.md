# AFL State Handlers Documentation

This document describes the state handler architecture used by AFL to execute workflow steps through discrete state transitions.

## Overview

AFL uses a **state machine architecture** where workflow execution progresses through discrete states. Each step type (statement, block, yield) has a defined state sequence, and state handlers implement the logic for each state transition. The runtime is synchronous — state changers loop through transitions within a single evaluator iteration.

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         Evaluator Layer                                      │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐          │
│  │  StepStateChanger │  │ BlockStateChanger │  │ YieldStateChanger │          │
│  └─────────┬────────┘  └─────────┬────────┘  └─────────┬────────┘          │
│            │                     │                     │                    │
│            └─────────────────────┼─────────────────────┘                    │
│                                  │                                          │
│                      ┌───────────▼───────────┐                             │
│                      │     StateChanger      │                             │
│                      │  (Abstract Base)      │                             │
│                      └───────────┬───────────┘                             │
└──────────────────────────────────┼──────────────────────────────────────────┘
                                   │
┌──────────────────────────────────┼──────────────────────────────────────────┐
│                         State Handler Layer                                  │
│                                  │                                          │
│    ┌─────────────────────────────┼─────────────────────────────────────┐   │
│    │                             ▼                                     │   │
│    │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐              │   │
│    │  │   Facet     │  │   Block     │  │  Statement  │              │   │
│    │  │Initialization│  │  Execution  │  │   Capture   │              │   │
│    │  └─────────────┘  └─────────────┘  └─────────────┘              │   │
│    │                                                                   │   │
│    │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐              │   │
│    │  │   Mixin     │  │   Event     │  │  Statement  │              │   │
│    │  │   Blocks    │  │  Transmit   │  │  Complete   │              │   │
│    │  └─────────────┘  └─────────────┘  └─────────────┘              │   │
│    └───────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## State Changers

State changers orchestrate the state machine, selecting and executing states in sequence.

### Base Class: StateChanger

**File:** `afl/runtime/changers/base.py`

```python
class StateChanger(ABC):
    """Abstract base for state machine orchestrators."""

    def __init__(self, step: StepDefinition, context: ExecutionContext):
        self.step = step
        self.context = context

    def process(self) -> StateChangeResult:
        """Process the step through its state machine.

        Loops through state transitions until the step:
        - Reaches a terminal state (Complete/Error)
        - Is no longer requesting state changes
        - Is blocked waiting on external work
        """
        if self.step.is_complete:
            return StateChangeResult(step=self.step, continue_processing=False)

        try:
            while True:
                if self.step.is_requesting_state_change:
                    next_state = self.select_state()
                    if next_state and next_state != self.step.current_state:
                        self.step.change_state(next_state)

                result = self.execute_state(self.step.current_state)

                if not result.success:
                    self.step.mark_error(result.error)
                    return StateChangeResult(
                        step=self.step, success=False,
                        error=result.error, continue_processing=False,
                    )

                self.step = result.step

                if self.step.is_terminal:
                    return StateChangeResult(
                        step=self.step, continue_processing=False,
                    )

                if not self.step.is_requesting_state_change:
                    break

            return StateChangeResult(
                step=self.step,
                continue_processing=self.step.transition.is_requesting_push,
            )
        except Exception as e:
            self.step.mark_error(e)
            return StateChangeResult(
                step=self.step, success=False,
                error=e, continue_processing=False,
            )

    @abstractmethod
    def select_state(self) -> Optional[str]:
        """Select the next state for the step."""
        ...

    @abstractmethod
    def execute_state(self, state: str) -> StateChangeResult:
        """Execute the handler for a state."""
        ...
```

### StateChangeResult

```python
@dataclass
class StateChangeResult:
    """Result of a state change operation."""
    step: StepDefinition
    success: bool = True
    error: Optional[Exception] = None
    continue_processing: bool = True
```

### Type-Specific State Changers

| State Changer | Handles | File |
|---------------|---------|------|
| `StepStateChanger` | `VariableAssignment` | `afl/runtime/changers/step_changer.py` |
| `BlockStateChanger` | `AndThen`, `AndMap`, `AndMatch`, `Block` | `afl/runtime/changers/block_changer.py` |
| `YieldStateChanger` | `YieldAssignment` | `afl/runtime/changers/yield_changer.py` |

**Factory Function** (`afl/runtime/changers/__init__.py`):

```python
def get_state_changer(step: StepDefinition, context: ExecutionContext) -> StateChanger:
    """Factory function to get appropriate StateChanger for a step."""
    if step.object_type == ObjectType.YIELD_ASSIGNMENT:
        return YieldStateChanger(step, context)
    elif ObjectType.is_block(step.object_type):
        return BlockStateChanger(step, context)
    else:
        return StepStateChanger(step, context)
```

---

## State Sequences

### Statement Execution (StepStateChanger)

```
Created
    │
    ▼
FacetInitializationBegin ──▶ FacetInitializationEnd
    │
    ▼
FacetScriptsBegin ──▶ FacetScriptsEnd
    │
    ▼
MixinBlocksBegin ──▶ MixinBlocksContinue ──▶ MixinBlocksEnd
    │                      ▲        │
    │                      └────────┘ (loop until done)
    ▼
MixinCaptureBegin ──▶ MixinCaptureEnd
    │
    ▼
EventTransmit
    │
    ▼
StatementBlocksBegin ──▶ StatementBlocksContinue ──▶ StatementBlocksEnd
    │                           ▲        │
    │                           └────────┘ (loop until done)
    ▼
StatementCaptureBegin ──▶ StatementCaptureEnd
    │
    ▼
StatementEnd ──▶ StatementComplete
```

### Block Execution (BlockStateChanger)

```
Created
    │
    ▼
BlockExecutionBegin
    │
    ▼
BlockExecutionContinue ◀──┐
    │                     │ (loop until all items done)
    └─────────────────────┘
    │
    ▼
BlockExecutionEnd
    │
    ▼
StatementEnd ──▶ StatementComplete
```

### Yield Execution (YieldStateChanger)

```
Created
    │
    ▼
FacetInitializationBegin ──▶ FacetInitializationEnd
    │
    ▼
FacetScriptsBegin ──▶ FacetScriptsEnd
    │
    ▼
StatementEnd ──▶ StatementComplete
```

---

## State Constants

**File:** `afl/runtime/states.py`

```python
class StepState:
    """Step state constants using hierarchical naming convention."""

    # Initial state
    CREATED = "state.statement.Created"

    # Facet initialization phase
    FACET_INIT_BEGIN = "state.facet.initialization.Begin"
    FACET_INIT_END = "state.facet.initialization.End"

    # Facet scripts phase
    FACET_SCRIPTS_BEGIN = "state.facet.scripts.Begin"
    FACET_SCRIPTS_END = "state.facet.scripts.End"

    # Statement scripts phase
    STATEMENT_SCRIPTS_BEGIN = "state.statement.scripts.Begin"
    STATEMENT_SCRIPTS_END = "state.statement.scripts.End"

    # Mixin blocks phase
    MIXIN_BLOCKS_BEGIN = "state.mixin.blocks.Begin"
    MIXIN_BLOCKS_CONTINUE = "state.mixin.blocks.Continue"
    MIXIN_BLOCKS_END = "state.mixin.blocks.End"

    # Mixin capture phase
    MIXIN_CAPTURE_BEGIN = "state.mixin.capture.Begin"
    MIXIN_CAPTURE_END = "state.mixin.capture.End"

    # Event transmit
    EVENT_TRANSMIT = "state.EventTransmit"

    # Statement blocks phase
    STATEMENT_BLOCKS_BEGIN = "state.statement.blocks.Begin"
    STATEMENT_BLOCKS_CONTINUE = "state.statement.blocks.Continue"
    STATEMENT_BLOCKS_END = "state.statement.blocks.End"

    # Block execution phase (for block steps)
    BLOCK_EXECUTION_BEGIN = "state.block.execution.Begin"
    BLOCK_EXECUTION_CONTINUE = "state.block.execution.Continue"
    BLOCK_EXECUTION_END = "state.block.execution.End"

    # Statement capture phase
    STATEMENT_CAPTURE_BEGIN = "state.statement.capture.Begin"
    STATEMENT_CAPTURE_END = "state.statement.capture.End"

    # Terminal states
    STATEMENT_END = "state.statement.End"
    STATEMENT_COMPLETE = "state.statement.Complete"
    STATEMENT_ERROR = "state.statement.Error"

    @classmethod
    def is_terminal(cls, state: str) -> bool:
        """Check if state is terminal (Complete or Error)."""
        return state in (cls.STATEMENT_COMPLETE, cls.STATEMENT_ERROR)

    @classmethod
    def is_complete(cls, state: str) -> bool:
        """Check if state is Complete."""
        return state == cls.STATEMENT_COMPLETE
```

### Transition Tables

State transitions are defined as `dict[str, str]` mappings (`states.py:77-125`):

```python
# Full state machine for VariableAssignment steps
STEP_TRANSITIONS: dict[str, str] = {
    StepState.CREATED: StepState.FACET_INIT_BEGIN,
    StepState.FACET_INIT_BEGIN: StepState.FACET_INIT_END,
    StepState.FACET_INIT_END: StepState.FACET_SCRIPTS_BEGIN,
    # ... through all phases ...
    StepState.STATEMENT_END: StepState.STATEMENT_COMPLETE,
}

# Simplified for block steps
BLOCK_TRANSITIONS: dict[str, str] = {
    StepState.CREATED: StepState.BLOCK_EXECUTION_BEGIN,
    StepState.BLOCK_EXECUTION_BEGIN: StepState.BLOCK_EXECUTION_CONTINUE,
    StepState.BLOCK_EXECUTION_CONTINUE: StepState.BLOCK_EXECUTION_END,
    StepState.BLOCK_EXECUTION_END: StepState.STATEMENT_END,
    StepState.STATEMENT_END: StepState.STATEMENT_COMPLETE,
}

# Minimal for yield steps (skip blocks)
YIELD_TRANSITIONS: dict[str, str] = {
    StepState.CREATED: StepState.FACET_INIT_BEGIN,
    StepState.FACET_INIT_BEGIN: StepState.FACET_INIT_END,
    StepState.FACET_INIT_END: StepState.FACET_SCRIPTS_BEGIN,
    StepState.FACET_SCRIPTS_BEGIN: StepState.FACET_SCRIPTS_END,
    StepState.FACET_SCRIPTS_END: StepState.STATEMENT_END,
    StepState.STATEMENT_END: StepState.STATEMENT_COMPLETE,
}
```

---

## State Handler Base

**File:** `afl/runtime/handlers/base.py`

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

    @property
    def state_name(self) -> str:
        """Get the state name this handler processes."""
        return self.__class__.__name__

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

### Handler Registry

**File:** `afl/runtime/handlers/__init__.py`

Handlers are registered in a dict mapping states to handler classes:

```python
STATE_HANDLERS: dict[str, type[StateHandler]] = {
    StepState.CREATED: StatementBeginHandler,
    StepState.FACET_INIT_BEGIN: FacetInitializationBeginHandler,
    StepState.FACET_INIT_END: FacetInitializationEndHandler,
    StepState.FACET_SCRIPTS_BEGIN: FacetScriptsBeginHandler,
    StepState.FACET_SCRIPTS_END: FacetScriptsEndHandler,
    StepState.MIXIN_BLOCKS_BEGIN: MixinBlocksBeginHandler,
    StepState.MIXIN_BLOCKS_CONTINUE: MixinBlocksContinueHandler,
    StepState.MIXIN_BLOCKS_END: MixinBlocksEndHandler,
    StepState.MIXIN_CAPTURE_BEGIN: MixinCaptureBeginHandler,
    StepState.MIXIN_CAPTURE_END: MixinCaptureEndHandler,
    StepState.EVENT_TRANSMIT: EventTransmitHandler,
    StepState.STATEMENT_BLOCKS_BEGIN: StatementBlocksBeginHandler,
    StepState.STATEMENT_BLOCKS_CONTINUE: StatementBlocksContinueHandler,
    StepState.STATEMENT_BLOCKS_END: StatementBlocksEndHandler,
    StepState.STATEMENT_CAPTURE_BEGIN: StatementCaptureBeginHandler,
    StepState.STATEMENT_CAPTURE_END: StatementCaptureEndHandler,
    StepState.STATEMENT_END: StatementEndHandler,
    StepState.STATEMENT_COMPLETE: StatementCompleteHandler,
    StepState.BLOCK_EXECUTION_BEGIN: BlockExecutionBeginHandler,
    StepState.BLOCK_EXECUTION_CONTINUE: BlockExecutionContinueHandler,
    StepState.BLOCK_EXECUTION_END: BlockExecutionEndHandler,
}

def get_handler(
    state: str,
    step: StepDefinition,
    context: ExecutionContext,
) -> Optional[StateHandler]:
    """Get the appropriate handler for a state."""
    handler_class = STATE_HANDLERS.get(state)
    if handler_class is None:
        return None
    return handler_class(step, context)
```

---

## Individual State Handlers

### Facet Initialization

#### FacetInitializationBeginHandler

**File:** `afl/runtime/handlers/initialization.py`

**Purpose:** Evaluates facet attributes and applies parameters.

**Processing:**
1. Gets statement definition from context
2. Builds `EvaluationContext` with workflow inputs and step output getter
3. Calls `evaluate_args()` to evaluate expressions
4. Stores evaluated attributes on step
5. Requests state change

```python
class FacetInitializationBeginHandler(StateHandler):
    def process_state(self) -> StateChangeResult:
        stmt_def = self.context.get_statement_definition(self.step)
        if stmt_def is None:
            self.step.request_state_change(True)
            return StateChangeResult(step=self.step)

        ctx = self._build_context()
        try:
            evaluated = evaluate_args(stmt_def.args, ctx)
            for name, value in evaluated.items():
                self.step.set_attribute(name, value)
            self.step.request_state_change(True)
            return StateChangeResult(step=self.step)
        except Exception as e:
            return self.error(e)
```

#### FacetInitializationEndHandler

**File:** `afl/runtime/handlers/initialization.py`

**Purpose:** Marks facet initialization complete. Requests state change.

---

### Facet Scripts

#### FacetScriptsBeginHandler / FacetScriptsEndHandler

**File:** `afl/runtime/handlers/scripts.py`

**Purpose:** Execute and complete facet-level scripts. Currently pass-through states that request state change.

---

### Mixin Blocks

#### MixinBlocksBeginHandler

**File:** `afl/runtime/handlers/blocks.py`

**Purpose:** Creates block steps for mixin definitions. Requests state change.

#### MixinBlocksContinueHandler

**File:** `afl/runtime/handlers/blocks.py`

**Purpose:** Monitors mixin block completion.

**Processing:**
1. Loads blocks via `persistence.get_blocks_by_step()`
2. Filters to mixin blocks (`container_type == "Facet"`)
3. Creates `BlockAnalysis.load(step, mixin_blocks, mixins=True)`
4. If `analysis.done`: requests state change
5. If not done: calls `self.stay(push=True)` to re-queue

```python
class MixinBlocksContinueHandler(StateHandler):
    def process_state(self) -> StateChangeResult:
        blocks = self.context.persistence.get_blocks_by_step(self.step.id)
        mixin_blocks = [b for b in blocks if b.container_type == "Facet"]

        if not mixin_blocks:
            self.step.request_state_change(True)
            return StateChangeResult(step=self.step)

        analysis = BlockAnalysis.load(self.step, mixin_blocks, mixins=True)
        if analysis.done:
            self.step.request_state_change(True)
            return StateChangeResult(step=self.step)
        else:
            return self.stay(push=True)
```

#### MixinBlocksEndHandler

**File:** `afl/runtime/handlers/blocks.py`

**Purpose:** Marks mixin blocks processing complete. Requests state change.

---

### Mixin Captures

#### MixinCaptureBeginHandler

**File:** `afl/runtime/handlers/capture.py`

**Purpose:** Extracts and merges captured data from mixin blocks.

**Processing:**
1. Gets completed mixin blocks via persistence
2. For each block, finds yield steps (`ObjectType.YIELD_ASSIGNMENT`)
3. Merges yield attributes into step's return values

#### MixinCaptureEndHandler

**File:** `afl/runtime/handlers/capture.py`

**Purpose:** Marks mixin capture complete. Requests state change.

---

### Event Transmission

#### EventTransmitHandler

**File:** `afl/runtime/handlers/completion.py`

**Purpose:** Dispatches events defined by `EventFacetDecl`.

**Processing:**
1. Retrieves facet definition from context
2. If facet is `EventFacetDecl`: creates `EventDefinition` with payload
3. Adds event to `IterationChanges` for atomic commit
4. Requests state change

#### EventTransmit Blocking

> **Implemented** — see `spec/70_examples.md` Example 4 and `spec/30_runtime.md` §8.1.

The `EventTransmitHandler` MUST differentiate between event and non-event facets:

- **Non-event facets**: Pass-through. Calls `request_state_change(True)`. Step advances immediately.
- **Event facets**: Creates `EventDefinition`, calls `request_state_change(False)`. Step **stays** at `EventTransmit` until a `StepContinue` event is received from an external agent.

This is the **only state** in the step state machine that may span multiple evaluator runs. All other blocking states (e.g., `statement.blocks.Continue`) are resolved within the same evaluator run.

#### StepContinue Event Handling

> **Implemented** — see `spec/30_runtime.md` §12.1.

The lifecycle for event-blocked steps:

```
Step reaches EventTransmit
    │
    ▼
EventTransmitHandler detects EventFacetDecl
    │
    ├── Creates EventDefinition (event.Created)
    ├── Calls request_state_change(False)
    │
    ▼
Evaluator reaches fixed point → pauses
    │
    ▼
External agent processes event
    │
    ├── event.Created → event.Dispatched → event.Processing → event.Completed
    ├── Sends StepContinue for step_id
    │
    ▼
Evaluator resumes (new run)
    │
    ├── Receives StepContinue
    ├── Step transitions: EventTransmit → statement.blocks.Begin
    │
    ▼
Normal execution continues
```

---

### Statement Blocks

#### StatementBlocksBeginHandler

**File:** `afl/runtime/handlers/blocks.py`

**Purpose:** Creates block steps for statement-level andThen blocks.

**Processing:**
1. Gets workflow AST body for workflow root steps
2. Creates `StepDefinition` with `ObjectType.AND_THEN`
3. Adds to `IterationChanges.add_created_step()`
4. Requests state change

#### StatementBlocksContinueHandler

**File:** `afl/runtime/handlers/blocks.py`

**Purpose:** Monitors statement block completion.

**Processing:**
1. Loads blocks via `persistence.get_blocks_by_step()`
2. Includes pending created steps from `IterationChanges`
3. Creates `BlockAnalysis.load(step, blocks, mixins=False)`
4. If `analysis.done`: requests state change
5. If not done: calls `self.stay(push=True)` to re-queue

#### StatementBlocksEndHandler

**File:** `afl/runtime/handlers/blocks.py`

**Purpose:** Marks statement blocks processing complete. Requests state change.

#### Block AST Resolution

> **Implemented** — see `spec/30_runtime.md` §8.2 and §8.3.

The `StatementBlocksBeginHandler` MUST resolve block definitions from two sources:

1. **Statement-level `andThen`**: An inline block attached to a step statement (e.g., `s1 = SomeFacet(...) andThen { ... }`). This is stored as the `body` property of the `StepStmt` AST node.
2. **Facet-level `andThen`**: A block defined on the facet declaration itself (e.g., `facet Adder(...) andThen { ... }`). This is stored as the `body` property of the `FacetDecl` AST node.

**Precedence:** Statement-level blocks take precedence over facet-level blocks.

**Current limitation:** The handler only creates blocks for the workflow root step. It must be extended to create blocks for any step that has either a statement-level or facet-level `andThen` body.

When the resulting block step enters `BlockExecutionBegin`, the `get_block_ast()` method MUST return the correct AST based on the block's origin (workflow body, facet body, or statement body).

---

### Statement Captures

#### StatementCaptureBeginHandler

**File:** `afl/runtime/handlers/capture.py`

**Purpose:** Extracts and merges captured data from statement blocks.

**Processing:**
1. Gets completed statement blocks from persistence and pending changes
2. For each block, finds yield steps (`ObjectType.YIELD_ASSIGNMENT`)
3. Merges yield `params` into step's `returns`

```python
class StatementCaptureBeginHandler(StateHandler):
    def process_state(self) -> StateChangeResult:
        blocks = self.context.persistence.get_blocks_by_step(self.step.id)
        statement_blocks = [b for b in blocks if b.is_complete]

        for pending_step in self.context.changes.updated_steps:
            if (pending_step.container_id == self.step.id and
                pending_step.is_block and pending_step.is_complete and
                pending_step not in statement_blocks):
                statement_blocks.append(pending_step)

        for block in statement_blocks:
            self._merge_yields_from_block(block)

        self.step.request_state_change(True)
        return StateChangeResult(step=self.step)
```

#### StatementCaptureEndHandler

**File:** `afl/runtime/handlers/capture.py`

**Purpose:** Marks statement capture complete. Requests state change.

---

### Block Execution

#### BlockExecutionBeginHandler

**File:** `afl/runtime/handlers/block_execution.py`

**Purpose:** Begins execution of block contents.

**Processing:**
1. Gets block AST from context
2. Builds `DependencyGraph.from_ast()` for the block
3. Stores graph via `context.set_block_graph()`
4. Creates steps for statements with no dependencies
5. Requests state change

```python
class BlockExecutionBeginHandler(StateHandler):
    def process_state(self) -> StateChangeResult:
        block_ast = self.context.get_block_ast(self.step)
        if block_ast is None:
            self.step.request_state_change(True)
            return StateChangeResult(step=self.step)

        workflow_inputs = self._get_workflow_inputs()
        graph = DependencyGraph.from_ast(block_ast, workflow_inputs)
        self.context.set_block_graph(self.step.id, graph)
        self._create_ready_steps(graph, set())

        self.step.request_state_change(True)
        return StateChangeResult(step=self.step)
```

#### BlockExecutionContinueHandler

**File:** `afl/runtime/handlers/block_execution.py`

**Purpose:** Continues iterating through block items.

**Processing:**
1. Gets `DependencyGraph` from context
2. Loads current steps in block (persisted + pending)
3. Builds `StepAnalysis.load()` to track progress
4. If `analysis.done`: requests state change
5. Otherwise: creates newly ready steps and calls `self.stay(push=True)`

#### BlockExecutionEndHandler

**File:** `afl/runtime/handlers/block_execution.py`

**Purpose:** Marks block execution complete. Requests state change.

---

### Statement Completion

#### StatementBeginHandler

**File:** `afl/runtime/handlers/initialization.py`

**Purpose:** Initializes statement execution. Requests state change.

#### StatementEndHandler

**File:** `afl/runtime/handlers/completion.py`

**Purpose:** Prepares statement for completion. Requests state change.

#### StatementCompleteHandler

**File:** `afl/runtime/handlers/completion.py`

**Purpose:** Finalizes statement execution and notifies parent.

**Processing:**
1. Calls `step.mark_completed()`
2. Container notification handled implicitly through iteration
3. Returns `StateChangeResult` with `continue_processing=False`

```python
class StatementCompleteHandler(StateHandler):
    def process_state(self) -> StateChangeResult:
        self.step.mark_completed()
        self._notify_container()
        return StateChangeResult(
            step=self.step,
            continue_processing=False,
        )
```

**Critical Role:** This handler stops the state machine loop. The parent block is notified implicitly — the evaluator will re-process the containing block on the next iteration, where `BlockAnalysis` or `StepAnalysis` will detect this step is complete.

---

## Block Analysis

### StepAnalysis

**File:** `afl/runtime/block.py`

Provides detailed analysis of step execution state within a block:

```python
@dataclass
class StepAnalysis:
    """Analysis of step execution state within a block."""
    block: StepDefinition
    statements: Sequence[StatementDefinition]

    # Step collections
    missing: list[StatementDefinition] = field(default_factory=list)
    steps: list[StepDefinition] = field(default_factory=list)
    completed: list[StepDefinition] = field(default_factory=list)
    requesting_push: list[StepDefinition] = field(default_factory=list)
    requesting_transition: list[StepDefinition] = field(default_factory=list)
    pending_event: list[StepDefinition] = field(default_factory=list)
    pending_mixin: list[StepDefinition] = field(default_factory=list)
    pending_blocks: list[StepDefinition] = field(default_factory=list)

    done: bool = False

    @classmethod
    def load(cls, block, statements, steps) -> StepAnalysis:
        """Load analysis from persisted steps."""
        ...

    def can_be_created(self) -> Sequence[StatementDefinition]:
        """Get statements with all dependencies satisfied."""
        ...

    def is_blocked(self) -> bool:
        """Check if execution is blocked on dependencies."""
        ...

    def has_pending_work(self) -> bool:
        """Check if there is pending work to do."""
        ...
```

**Done Criteria:** `len(missing) == 0 and len(completed) == len(statements)`

### BlockAnalysis

**File:** `afl/runtime/block.py`

Tracks block completion for containing steps:

```python
@dataclass
class BlockAnalysis:
    """Analysis of all blocks for a step."""
    step: StepDefinition
    blocks: list[StepDefinition]
    completed: list[StepDefinition] = field(default_factory=list)
    pending: list[StepDefinition] = field(default_factory=list)
    done: bool = False

    @classmethod
    def load(cls, step, blocks, mixins=False) -> BlockAnalysis:
        """Load analysis from persisted blocks."""
        ...
```

**Done Criteria:** `len(pending) == 0`

---

## Control Flow Flags

Steps use the `StepTransition` dataclass to control state machine behavior.

**File:** `afl/runtime/step.py`

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
```

### request_state_change(request: bool)

- `True` (default): Continue to next state
- `False`: Stop state machine loop

**Used by:** `StatementCompleteHandler` to terminate loop after completion.

### set_push_me(push: bool)

- `True`: Re-queue step for later processing
- `False`: Allow normal state transition

**Used by:** Continuation handlers (`BlockExecutionContinueHandler`, `StatementBlocksContinueHandler`, `MixinBlocksContinueHandler`) via `self.stay(push=True)`.

### change_and_transition()

- Marks step as `changed = True`
- Sets `request_transition = True`
- Triggers state machine to select next state

---

## Execution Flow Example

### Simple Statement Execution

```
Evaluator processes step
  │ Calls get_state_changer(step, context)
  │ Returns StepStateChanger
  ▼
StepStateChanger.process()
  │
  ├─▶ select_state(): CREATED → FACET_INIT_BEGIN
  │   execute_state(FACET_INIT_BEGIN)
  │     → FacetInitializationBeginHandler.process_state()
  │     → evaluate_args() evaluates expressions
  │     → step.set_attribute(name, value)
  │     → step.request_state_change(True)
  │
  ├─▶ select_state(): FACET_INIT_BEGIN → FACET_INIT_END
  │   execute_state(FACET_INIT_END)
  │     → step.request_state_change(True)
  │
  ├─▶ ... continues through all states ...
  │
  └─▶ select_state(): STATEMENT_END → STATEMENT_COMPLETE
      execute_state(STATEMENT_COMPLETE)
        → StatementCompleteHandler.process_state()
        → step.mark_completed()
        → _notify_container() (implicit via iteration)
        → Returns StateChangeResult(continue_processing=False)

process() returns StateChangeResult
  │
  ▼
Step is COMPLETED
Evaluator commits changes via IterationChanges
Next iteration processes parent block
```

### Block with Multiple Items

```
StatementBlocksBeginHandler
  │ Creates child block step (ObjectType.AND_THEN)
  │ Adds to IterationChanges
  │ Requests state change
  ▼
StatementBlocksContinueHandler [Iteration 1]
  │ BlockAnalysis: completed=[], done=False
  │ self.stay(push=True) → re-queue step
  │ Returns StateChangeResult
  ▼
... evaluator commits, next iteration starts ...
... child block steps execute and complete ...
  ▼
StatementBlocksContinueHandler [Iteration N]
  │ BlockAnalysis: completed=[all], done=True
  │ step.request_state_change(True) → normal transition
  │ Returns StateChangeResult
  ▼
StatementBlocksEndHandler
  │ Requests state change
  ▼
StatementCaptureBeginHandler
  │ Merges yield results from completed blocks
  │ Continues with capture phase
  ...
```

---

## State Handler Summary Table

| Handler | State | Purpose | Key Operation |
|---------|-------|---------|---------------|
| `StatementBeginHandler` | `CREATED` | Initialize statement | `request_state_change` |
| `FacetInitializationBeginHandler` | `FACET_INIT_BEGIN` | Evaluate facet | `evaluate_args` |
| `FacetInitializationEndHandler` | `FACET_INIT_END` | Mark init done | `request_state_change` |
| `FacetScriptsBeginHandler` | `FACET_SCRIPTS_BEGIN` | Execute facet scripts | `request_state_change` |
| `FacetScriptsEndHandler` | `FACET_SCRIPTS_END` | Mark scripts done | `request_state_change` |
| `MixinBlocksBeginHandler` | `MIXIN_BLOCKS_BEGIN` | Create mixin blocks | `request_state_change` |
| `MixinBlocksContinueHandler` | `MIXIN_BLOCKS_CONTINUE` | Check mixin completion | `BlockAnalysis` + `stay(push=True)` |
| `MixinBlocksEndHandler` | `MIXIN_BLOCKS_END` | Mark mixins done | `request_state_change` |
| `MixinCaptureBeginHandler` | `MIXIN_CAPTURE_BEGIN` | Merge mixin captures | Yield merge |
| `MixinCaptureEndHandler` | `MIXIN_CAPTURE_END` | Mark captures done | `request_state_change` |
| `EventTransmitHandler` | `EVENT_TRANSMIT` | Dispatch events | `EventDefinition` creation |
| `StatementBlocksBeginHandler` | `STATEMENT_BLOCKS_BEGIN` | Create statement blocks | `StepDefinition.create` |
| `StatementBlocksContinueHandler` | `STATEMENT_BLOCKS_CONTINUE` | Check block completion | `BlockAnalysis` + `stay(push=True)` |
| `StatementBlocksEndHandler` | `STATEMENT_BLOCKS_END` | Mark blocks done | `request_state_change` |
| `StatementCaptureBeginHandler` | `STATEMENT_CAPTURE_BEGIN` | Merge captures | Yield merge |
| `StatementCaptureEndHandler` | `STATEMENT_CAPTURE_END` | Mark captures done | `request_state_change` |
| `StatementEndHandler` | `STATEMENT_END` | Prepare completion | `request_state_change` |
| `StatementCompleteHandler` | `STATEMENT_COMPLETE` | Finalize statement | `mark_completed` |
| `BlockExecutionBeginHandler` | `BLOCK_EXECUTION_BEGIN` | Start block iteration | `DependencyGraph` |
| `BlockExecutionContinueHandler` | `BLOCK_EXECUTION_CONTINUE` | Continue block items | `StepAnalysis` + `stay(push=True)` |
| `BlockExecutionEndHandler` | `BLOCK_EXECUTION_END` | Mark block done | `request_state_change` |

---

## File Reference

| Component | Path |
|-----------|------|
| StateChanger base | `afl/runtime/changers/base.py` |
| StepStateChanger | `afl/runtime/changers/step_changer.py` |
| BlockStateChanger | `afl/runtime/changers/block_changer.py` |
| YieldStateChanger | `afl/runtime/changers/yield_changer.py` |
| get_state_changer | `afl/runtime/changers/__init__.py` |
| StateHandler base | `afl/runtime/handlers/base.py` |
| Handler registry | `afl/runtime/handlers/__init__.py` |
| Initialization handlers | `afl/runtime/handlers/initialization.py` |
| Script handlers | `afl/runtime/handlers/scripts.py` |
| Block handlers | `afl/runtime/handlers/blocks.py` |
| Block execution handlers | `afl/runtime/handlers/block_execution.py` |
| Capture handlers | `afl/runtime/handlers/capture.py` |
| Completion handlers | `afl/runtime/handlers/completion.py` |
| StepState constants | `afl/runtime/states.py` |
| Transition tables | `afl/runtime/states.py` |
| StepDefinition | `afl/runtime/step.py` |
| StepTransition | `afl/runtime/step.py` |
| StepAnalysis | `afl/runtime/block.py` |
| BlockAnalysis | `afl/runtime/block.py` |
| ObjectType | `afl/runtime/types.py` |

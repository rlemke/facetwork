# Hello Agent Example

A minimal end-to-end example demonstrating the Facetwork execution model.

## What It Does

1. **Compiles** `workflow.ffl` to JSON
2. **Executes** the `SayHello` workflow with input `name = "World"`
3. **Pauses** when it reaches the `hello.Greet` event facet
4. **Agent** processes the event and returns `message = "Hello, World!"`
5. **Resumes** the workflow to completion
6. **Outputs** `greeting = "Hello, World!"`

## Files

| File | Description |
|------|-------------|
| `workflow.ffl` | FFL source defining the workflow and event facet |
| `run.py` | Self-contained script demonstrating the full cycle |

## Run It

```bash
# From the repo root (after pip install -e ".[dev]")
python3 examples/hello-agent/run.py
```

## Expected Output

```
============================================================
STEP 1: Compile FFL source
============================================================

Source file: examples/hello-agent/workflow.ffl

// Hello Agent Example
...

Compiled to JSON: 1 namespace(s), 1 workflow(s)
Workflow: SayHello

============================================================
STEP 2: Execute workflow (pauses at event facet)
============================================================

Inputs: {'name': 'World'}
Status: PAUSED
Workflow ID: <uuid>

Workflow paused - waiting for agent to process 'hello.Greet' event

============================================================
STEP 3: Agent processes the event
============================================================

Agent polling for tasks...

  Agent received: name = 'World'
  Agent returns:  message = 'Hello, World!'

Dispatched 1 task(s)

============================================================
STEP 4: Resume workflow to completion
============================================================

Status: COMPLETED
Iterations: 1

Outputs: {'greeting': 'Hello, World!'}

============================================================
SUCCESS: Hello, World!
============================================================
```

## Key Concepts Demonstrated

- **Event Facet**: `hello.Greet` is an `event facet` — the runtime pauses when it reaches this step and creates a task for an agent to process.

- **Agent Handler**: The `greet_handler` function is registered with the `AgentPoller` to handle `hello.Greet` events. It receives the step's input parameters and returns output values.

- **Persistence**: The `MemoryStore` keeps all state in-process. In production, you'd use `MongoStore` for distributed execution across multiple machines.

- **Resume**: After the agent writes its results, `evaluator.resume()` continues the workflow from where it paused.

## Adapting This Example

To handle different events, register additional handlers:

```python
poller.register("my.namespace.MyEvent", lambda payload: {
    "output": do_something(payload["input"])
})
```

For production with MongoDB:

```python
from afl.runtime import MongoStore
from afl import load_config

config = load_config()
store = MongoStore.from_config(config.mongodb)
```

# Hello Agent — User Guide

> See also: [Examples Guide](../doc/GUIDE.md) | [README](README.md)

## When to Use This Example

Use this as your starting point if you are:
- **New to Facetwork** and want to understand the execution model
- Building your **first event facet and workflow**
- Learning how the **compile-execute-pause-resume cycle** works

## What You'll Learn

1. How FFL source compiles to a JSON workflow definition
2. How the runtime pauses at event facets and creates tasks
3. How an agent handler processes tasks and returns results
4. How the workflow resumes and completes with final outputs

## Step-by-Step Walkthrough

### 1. The FFL Source

The entire workflow is in `workflow.ffl`:

```afl
namespace hello {
    event facet Greet(name: String) => (message: String)
}

workflow SayHello(name: String) => (greeting: String) andThen {
    result = hello.Greet(name = $.name)
    yield SayHello(greeting = result.message)
}
```

**Key concepts:**
- `namespace hello` groups the event facet
- `event facet Greet` declares an external operation — the runtime pauses here and waits for an agent
- `workflow SayHello` is the entry point — it takes `name` as input and produces `greeting` as output
- `$.name` references the workflow's input parameter
- `result.message` references the output of the `Greet` step
- `yield` writes the final workflow output

### 2. The Execution Script

`run.py` is a self-contained script that demonstrates the full cycle:

```python
# Step 1: Compile FFL to JSON
program = parse(afl_source)
compiled = emit_dict(program)

# Step 2: Execute workflow (pauses at event facet)
result = evaluator.execute("SayHello", {"name": "World"})
# result.status == PAUSED

# Step 3: Agent processes the task
poller.register("hello.Greet", greet_handler)
poller.poll()  # picks up task, calls handler, writes result

# Step 4: Resume workflow to completion
result = evaluator.resume(workflow_id)
# result.status == COMPLETED
# result.outputs == {"greeting": "Hello, World!"}
```

### 3. Run It

```bash
# From repo root
source .venv/bin/activate
pip install -e ".[dev]"
python3 examples/hello-agent/run.py
```

You'll see each step printed with clear labels showing the workflow progressing through compile, execute, pause, agent dispatch, and resume.

## Key Concepts

### Event Facets vs Regular Facets

- A **regular facet** (`facet Foo(...)`) is a passive data structure
- An **event facet** (`event facet Foo(...)`) triggers agent execution — the runtime creates a task and pauses

### The Pause/Resume Cycle

```
Execute  →  Hit event facet  →  PAUSED  →  Agent processes task  →  Resume  →  COMPLETED
```

This is the fundamental Facetwork pattern. Every example builds on this cycle.

### Persistence

This example uses `MemoryStore` (in-process). For distributed execution, swap to `MongoStore`:

```python
from afl.runtime.mongo_store import MongoStore
store = MongoStore(connection_string="mongodb://localhost:27017", database_name="afl")
```

## Adapting for Your Use Case

### Add a second event facet

```afl
namespace hello {
    event facet Greet(name: String) => (message: String)
    event facet Farewell(name: String) => (message: String)
}

workflow GreetAndFarewell(name: String) => (hello: String, goodbye: String) andThen {
    hi = hello.Greet(name = $.name)
    bye = hello.Farewell(name = $.name)
    yield GreetAndFarewell(hello = hi.message, goodbye = bye.message)
}
```

### Register a handler for the new facet

```python
poller.register("hello.Farewell", lambda payload: {
    "message": f"Goodbye, {payload['name']}!"
})
```

## Next Steps

Once you understand this example, move on to:
- **[volcano-query](../volcano-query/USER_GUIDE.md)** — composing existing facets without writing handlers
- **[genomics](../genomics/USER_GUIDE.md)** — foreach iteration for parallel processing
- **[jenkins](../jenkins/USER_GUIDE.md)** — mixin composition for cross-cutting concerns

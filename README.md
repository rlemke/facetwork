# Facetwork

> This project was built entirely with [Claude](https://claude.ai) — the only human input is the specification documents in `spec/`. And actually claude wrote those, I just gave suggestions. Having been retired for a while, when I heard about Claude, I needed to see how much of my previous career had just been eliminated (or at least made possible in a very small fraction of the time). This is a substantial, fully functional platform written exclusively through AI-assisted development, although it should only be used as an example of what beginners can do with AI coding assistance. This is still a work in progress and goes many ways as I say "I want to try this or that". 
> It has been over a month now. Claude and I have come close to breaking up now and then :-) But I am amazed how far it has come without serious detailed help from me.
> If I do a quick grading these are the areas: 1. A+ : Creating a DSL, truely amazed how it created the AST tree and serialization and parsing. 2. A : Create focus api to other services (ie MongoDb, PostGres, downloads, etc) 3: B+ Understaning the overall intent, that is, this is a workflow language, 4: C: implementing the workflow engine in a distributed fashion. It kept try to make it operate like a controlling process like Jenkins. This is where we had some serious layout of plans and examples. Now I think it understands.
> But to be honest, I still have not learned Python yet so I have not detailed examined the code. There is a .md in specs called 75_execution_traces.md that I use to check it method of execution.
> I have not learned Python yet, I suspect many others are in the same boat. I am worried about that for key validations.
> For example: if it were written is scala or java, I could quickly scan the code and have a good idea of what it was doing and did it look correct especially with respect to concurrency, but I can not do that with python yet.
> The good news is that this has made programing fun again. I get to develop my ideas without spending days if not weeks on learning api and interface behaviors that may or may not be correct.
> The other good news, if something goes wrong it does a great job of find out what went wrong. Although this can lead to a rabbit hole where it really does not understand the issue and puts in fixes that actually makes the situation worse for other scenarios.
> 

## Start Here: Read the Thesis Documents

If you are new to Facetwork, **start with the thesis documents in [`docs/thesis/`](docs/thesis/)** rather than the reference specs. The specs are written for developers who need to implement against the system; the thesis documents explain what Facetwork is, why it was built this way, and where it might go — and are far more informative for a general reader.

| Document | What it covers |
|----------|----------------|
| [`thesis.md`](docs/thesis/thesis.md) / [`thesis.pdf`](docs/thesis/thesis.pdf) | The core thesis: a language-directed, lock-free model for live-updatable distributed workflow execution |
| [`defense.md`](docs/thesis/defense.md) / [`defense.pdf`](docs/thesis/defense.pdf) | Thesis defense Q&A — the design decisions examined under challenge |
| [`ai-authorship.md`](docs/thesis/ai-authorship.md) / [`ai-authorship.pdf`](docs/thesis/ai-authorship.pdf) | How Facetwork's design changes when AI agents, not humans, are the primary authors |
| [`future-thoughts-ai-native.md`](docs/thesis/future-thoughts-ai-native.md) / [`.pdf`](docs/thesis/future-thoughts-ai-native.pdf) | A forward-looking exploration of an AI-native workflow system |
| [`future-thoughts-positioning-dissent.md`](docs/thesis/future-thoughts-positioning-dissent.md) / [`.pdf`](docs/thesis/future-thoughts-positioning-dissent.pdf) | Dissenting companion on Facetwork's positioning in the AI-agent era |

Once you've read enough to understand the shape of the system, continue with the Quick Start and the developer-facing guides below.

---

**Facetwork** is a platform for defining and executing distributed workflows. You describe what should happen in a simple language called **FFL** (Facetwork Flow Language), and Facetwork handles the execution, retries, monitoring, and scaling.

You don't need to be a developer to use Facetwork — if you can fill in a form, you can run workflows from the dashboard.

## Choose Your Path

| I want to... | Start here |
|--------------|------------|
| **Run workflows** from the web UI | [Beginner's Guide](docs/getting-started/beginners-guide.md) |
| **Set up a local server** quickly | [Quick Start](#quick-start) (below) |
| **Write my own workflows** in FFL | [FFL Tutorial](docs/getting-started/tutorial.md) |
| **Build handlers** in Python | [Agent SDK](spec/60_agent_sdk.md) |
| **Build agents** in other languages | [Agent Libraries](#agent-integration-libraries) |
| **Deploy to a cluster** | [Deployment Guide](docs/operations/deployment.md) |
| **Understand the architecture** | [Architecture](docs/architecture/overview.md) |
| **Contribute to Facetwork** | [Full Technical Reference](claude.md) |

## Quick Start

### Docker (recommended for first-timers)

```bash
git clone https://github.com/rlemke/facetwork.git
cd facetwork

# Start everything: MongoDB + Dashboard + Runner + Sample Agent
docker compose up

# In another terminal, seed example workflows
docker compose run seed
```

Open **http://localhost:8080** — that's the dashboard. Click **Workflows** to see what's available, then click **New** to run one.

| Service | URL | Description |
|---------|-----|-------------|
| Dashboard | http://localhost:8080 | Web UI for running and monitoring workflows |
| MongoDB | localhost:27017 | Database (managed by Docker) |
| Runner | (internal) | Processes workflow tasks automatically |

```bash
docker compose down       # stop services
docker compose down -v    # stop and remove data
```

### Local Python

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,test,dashboard,mcp,mongodb]"
cp .env.example .env     # edit MongoDB connection string
scripts/seed-examples
python -m afl.dashboard --log-format text
```

Open http://localhost:8080.

## Using the Dashboard

The dashboard is where you run workflows, monitor progress, and troubleshoot issues.

**Running a workflow:**
1. Click **Workflows** in the sidebar
2. Click **New**
3. Select a workflow, fill in the parameters, click **Run**
4. Watch it execute on the detail page — steps, logs, and progress update automatically

**Finding things:**
- Use **Cmd+K** (or click the search bar) to find any workflow, handler, or server by name
- **Running** / **Completed** / **Failed** tabs filter the workflow list
- Click any step to see its parameters, return values, logs, and execution duration

**Key pages:** Workflows, Handlers (registered event facet code), Servers (runner health), Fleet (bird's-eye view), Steps (individual step detail with logs).

## What is FFL?

FFL is a simple language for describing workflows. Here's a taste:

```afl
namespace myapp {
    /** Fetches weather data for a city. */
    event facet GetWeather(city: String) => (temperature: Long, conditions: String)

    /** Gets weather for two cities and picks the warmer one. */
    workflow CompareWeather(city_a: String, city_b: String) => (warmer: String) andThen {
        weather_a = GetWeather(city = $.city_a)
        weather_b = GetWeather(city = $.city_b)
        yield CompareWeather(warmer = weather_a.temperature)
    }
}
```

- **`event facet`** — a step that needs a handler (your code) to do the actual work
- **`workflow`** — the entry point that chains steps together
- **`$`** — the workflow's input parameters
- **`step.field`** — output from a previous step

You write the workflow logic in FFL. A Python handler does the real work (API calls, data processing, etc.). Facetwork connects them.

To learn more: [FFL Tutorial](docs/getting-started/tutorial.md) | [Language Reference](docs/reference/language/grammar.md) | [Examples](docs/reference/examples.md)

## Sharing Workflows Like Libraries

FFL workflows are designed to be shared and composed — just like importing a library in a regular programming language. Teams publish their facets, schemas, and workflows as **namespaces** that other teams can `use` in their own workflows.

```afl
namespace analytics.reports {
    use data.warehouse        // import another team's data facets
    use ml.predictions        // import the ML team's prediction facets

    workflow MonthlyReport(month: String) => (report_path: String) andThen {
        // Use the data team's extraction facet — you didn't write it, just call it
        raw = ExtractSalesData(period = $.month)

        // Use the ML team's forecasting facet
        forecast = PredictNextMonth(history = raw.data)

        // Your team's rendering step
        report = RenderReport(sales = raw.data, forecast = forecast.prediction)
        yield MonthlyReport(report_path = report.output_path)
    }
}
```

**How sharing works:**
- Teams publish their FFL namespaces to MongoDB via `scripts/publish mylib.ffl`
- Other teams import published namespaces with `use team.namespace`
- The compiler resolves and validates all cross-team references at compile time
- Handlers are registered independently — teams deploy and update their own handlers without affecting other teams' workflows

This means a domain expert can build a workflow by composing facets from across the organization — data engineering, ML, visualization, notification — without needing to know how any of them are implemented. It's the same idea as `pip install` or `npm install`, but for workflow steps.

## Built for Long-Running, Distributed Work

Facetwork doesn't run workflows on a single machine and hope for the best. It runs on a **cluster of runner servers** backed by MongoDB, designed for workloads that take minutes, hours, or days.

**How it works:**
- When a workflow reaches a step that needs work (an *event facet*), the runtime creates a **task** in MongoDB
- Any available **runner server** in the cluster picks up the task, executes the handler, and writes the result back
- The workflow automatically advances to the next step — no single machine needs to stay alive the whole time

**Why this matters:**

| Capability | How Facetwork handles it |
|-----------|--------------------------|
| **Long-running jobs** | A step can take hours (e.g., importing geographic data, training a model). If a runner crashes or times out, the task is automatically reset to pending and another runner picks it up. |
| **Scalability** | Add more runner servers to handle more tasks in parallel. Each runner independently polls MongoDB for work — no central coordinator needed. |
| **Rolling updates** | Update handler code on runners one at a time with `scripts/rolling-deploy`. Running tasks finish on the old code; new tasks pick up the new code. No downtime. |
| **Fault tolerance** | If a server goes down, its orphaned tasks are automatically detected and reassigned. Workflows resume from exactly where they left off. |
| **Monitoring** | The dashboard shows every runner's health, active tasks, step logs, and execution duration in real time. |

A local Docker setup is great for development, but production workflows run on a cluster. See the [Deployment Guide](docs/operations/deployment.md) for setting up multiple runners across machines.

---

## Developer Guide

Everything below is for developers who want to build handlers, extend Facetwork, or understand the internals.

### Installation

```bash
# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install the package (includes lark dependency)
pip install -e .

# For development (adds pytest, ruff, mypy, pre-commit)
pip install -e ".[dev]"

# For running tests with mongomock
pip install -e ".[test]"

# For full stack (dashboard + MCP + MongoDB)
pip install -e ".[dev,test,dashboard,mcp,mongodb]"
```

**Dependency groups** (defined in `pyproject.toml`):
| Group | Includes |
|-------|----------|
| (base) | `lark` |
| `dev` | pytest, pytest-cov, ruff, mypy, pre-commit |
| `test` | pytest, pytest-cov, mongomock |
| `mongodb` | pymongo |
| `dashboard` | fastapi, uvicorn, jinja2 |
| `mcp` | mcp |

### Running Tests

```bash
pytest tests/ -v                                     # all tests
pytest tests/ --cov=afl --cov-report=term-missing    # with coverage
pytest tests/test_parser.py::TestWorkflows -v         # specific test
pytest tests/runtime/test_mongo_store.py --mongodb -v # real MongoDB
pytest tests/dashboard/ -v                            # dashboard tests
```

### Using the Parser

```python
from afl import parse, AFLParser, ParseError

source = """
facet User(name: String, email: String)

workflow SendEmail(to: String, body: String) => (status: String) andThen {
    user = User(name = $.to, email = $.to)
    result = EmailService(recipient = user.email, content = $.body)
    yield SendEmail(status = result.status)
}
"""

ast = parse(source)

for workflow in ast.workflows:
    print(f"Workflow: {workflow.sig.name}")
    for param in workflow.sig.params:
        print(f"  Param: {param.name}: {param.type.name}")
```

### Emitting JSON

```python
from afl import parse, emit_json, emit_dict

ast = parse("facet User(name: String)")

json_str = emit_json(ast)
data = emit_dict(ast)

# Compact output without locations
json_str = emit_json(ast, include_locations=False, indent=None)
```

### Command-Line Interface

```bash
afl input.ffl                        # parse and emit JSON
afl input.ffl -o output.json         # output to file
afl input.ffl --check                # syntax check only
afl input.ffl --compact --no-locations # compact JSON
echo 'facet Test()' | afl            # parse from stdin
```

### Executing Workflows Programmatically

```python
from afl import parse, emit_dict
from afl.runtime import Evaluator, MemoryStore, Telemetry, ExecutionStatus
from afl.runtime.agent_poller import AgentPoller, AgentPollerConfig

# Compile FFL
source = """
namespace demo {
    event facet AddOne(input: Long) => (output: Long)
}
workflow Increment(x: Long) => (result: Long) andThen {
    step = demo.AddOne(input = $.x)
    yield Increment(result = step.output)
}
"""
ast = parse(source)
compiled = emit_dict(ast)
workflow_ast = compiled["workflows"][0]
program_ast = compiled

# Execute — pauses at event facet
store = MemoryStore()
evaluator = Evaluator(persistence=store, telemetry=Telemetry(enabled=False))
result = evaluator.execute(workflow_ast, inputs={"x": 41}, program_ast=program_ast)
# result.status == PAUSED (blocked at AddOne)

# Agent processes the event
def addone_handler(payload: dict) -> dict:
    return {"output": payload["input"] + 1}

poller = AgentPoller(
    persistence=store, evaluator=evaluator,
    config=AgentPollerConfig(service_name="demo-agent"),
)
poller.register("demo.AddOne", addone_handler)
poller.cache_workflow_ast(result.workflow_id, workflow_ast)
poller.poll_once()

# Resume to completion
final = evaluator.resume(result.workflow_id, workflow_ast, program_ast)
assert final.outputs["result"] == 42  # 41 + 1
```

### Dashboard

```bash
python -m afl.dashboard                          # port 8080
python -m afl.dashboard --port 9000 --reload     # dev mode
```

### Distributed Runner Service

```bash
python -m afl.runtime.runner                                    # default
python -m afl.runtime.runner --topics TopicA --max-concurrent 10 # custom
```

### MCP Server

The MCP server exposes FFL compiler and runtime as tools for LLM agents:

```bash
python -m afl.mcp              # stdio transport
```

**Tools:** `afl_compile`, `afl_validate`, `afl_execute_workflow`, `afl_continue_step`, `afl_resume_workflow`, `afl_manage_runner`

**Resources:** `afl://runners`, `afl://runners/{id}`, `afl://steps/{id}`, `afl://flows`, `afl://servers`, `afl://tasks`

### Agent Integration Libraries

Facetwork agents can be built in any language. The `agents/` directory has libraries for:

| Language | Directory | Build |
|----------|-----------|-------|
| **Python** | Built into `afl.runtime` | `pip install -e .` |
| **Scala** | `agents/scala/fw-agent/` | `sbt compile` |
| **Go** | `agents/go/fw-agent/` | `go build ./...` |
| **TypeScript** | `agents/typescript/fw-agent/` | `npm install && npm run build` |
| **Java** | `agents/java/fw-agent/` | `mvn compile` |

Any language with a MongoDB driver can implement an agent. See `agents/protocol/constants.json` for the complete protocol specification.

**Starting a new agent in a separate repo:**
```bash
cp agents/templates/CLAUDE.md /path/to/my-agent/CLAUDE.md
cp agents/protocol/constants.json /path/to/my-agent/constants.json
```

### Scripts

```bash
scripts/compile input.ffl -o output.json     # compile FFL
scripts/publish input.ffl                    # compile + publish to MongoDB
scripts/run-workflow                         # interactive workflow execution
scripts/start-runner --example osm-geocoder  # start runner
scripts/stop-runners                         # stop all runners
scripts/drain-runners                        # stop + reset tasks to pending
scripts/list-runners                         # show runner fleet
scripts/db-stats                             # database statistics
scripts/postgis-vacuum                       # PostGIS maintenance
scripts/postgis-vacuum-status                # check vacuum progress
```

All scripts support `--help`.

## Examples

See [examples/README.md](examples/README.md) for a complete overview of all 15+ examples with feature matrices.

| Example | Highlights |
|---------|-----------|
| `examples/osm-geocoder/` | Full-scale: 42 FFL files, 16 handler categories, PostGIS, pgRouting |
| `examples/hiv-drug-resistance/` | Bioinformatics: QC branching, error recovery, batch processing |
| `examples/noaa-weather/` | Real data: AWS S3, climate analysis, linear regression |
| `examples/devops-deploy/` | Conditional branching, prompt/script blocks, mixins |
| `examples/research-agent/` | LLM integration: 8 prompt-block facets, Claude API |
| `examples/aws-lambda/` | Real boto3: LocalStack, Step Functions, blue-green deploy |
| `examples/jenkins/` | CI/CD: mixin composition, 4 pipeline workflows |
| `examples/genomics/` | Bioinformatics: foreach fan-out, joint genotyping |

## Specifications

The `docs/reference/` directory is the authoritative reference:

| Document | What It Covers |
|----------|----------------|
| [language/grammar.md](docs/reference/language/grammar.md) | FFL syntax — EBNF grammar, all language constructs |
| [runtime.md](docs/reference/runtime.md) | Execution semantics — iteration model, determinism |
| [database.md](docs/reference/database.md) | MongoDB schema — collections, indexes, atomic commits |
| [event-system.md](docs/reference/event-system.md) | Event/agent protocol — lifecycle, dispatch, task queue |
| [agent-sdk.md](docs/reference/agent-sdk.md) | Building agents — processing event facets |

**Supporting docs:** [overview](docs/reference/overview.md), [AST semantics](docs/reference/language/semantics.md), [validation](docs/reference/language/validation.md), [compiler](docs/reference/compiler.md), [state system](docs/reference/state-system.md), [LLM integration](docs/guides/llm-integration.md), [examples](docs/reference/examples.md), [tests](docs/contributing/testing.md)

## FFL Language Reference

### Types

`String`, `Int`, `Long`, `Boolean`, `Json`, `[String]` (arrays), `[[Int]]` (nested arrays), schema types

### Constructs

```afl
facet Name(param: Type)                                    # data structure
event facet Name(param: Type) => (ret: Type)               # triggers handler
workflow Name(param: Type) => (ret: Type) andThen { ... }  # entry point
schema Name { field: Type }                                # typed structure
namespace ns.name { ... }                                  # grouping
implicit name = Call(...)                                   # defaults
```

### Composition

```afl
facet Job(x: String) with Retry(max = 3) with Timeout(seconds = 60)  # mixins
andThen foreach item in $.items { ... }                                # iteration
andThen when { case cond => { ... } case _ => { ... } }               # branching
catch { ... }                                                          # error recovery
prompt { system "..." template "..." model "..." }                     # LLM
script python "..."                                                    # inline code
```

### References

`$.fieldName` (input parameters), `stepName.outputField` (step outputs), `step.result.nested` (nested access)

## Requirements

- Python 3.11+
- lark >= 1.1.0

# Facetwork
> This project was built entirely with [Claude](https://claude.ai) — the only human input is the specification 
> documents in `docs/`. And actually claude wrote those, I just gave suggestions. Having been retired for a while, 
> when I heard about Claude, I needed to see how much of my previous career had just been eliminated 
> (or at least made possible in a very small fraction of the time). 
> This is a substantial, fully functional platform written exclusively through AI-assisted development, 
> although it should only be used as an example of what beginners can do with AI coding assistance. 
> This is still a work in progress and goes many ways as I say "I want to try this or that".

> UPDATE: It's been 3 months since I started this project with Claude, and the progress has been incredible
> especially considering the only time I have put into the project is between cruises, family travel, bike riding, and
> other things people do in retirement. I get up at night, give a few suggestions to Claude, and wake up to a working system 
> that has evolved in ways I didn't even expect (mostly good but there are times ....) .
> The core platform is fully functional, with a custom DSL, distributed workflow execution, 
> and a web dashboard — all authored through AI-assisted development. 
> While there were challenges, especially around implementing the workflow engine in a distributed fashion, 
> the system now understands the design and executes workflows as intended. 
> I am still learning Python, so I haven't deeply examined the code, but the execution traces show it's working correctly.
> This experience has made programming fun again, allowing me to focus on developing ideas without getting bogged down in API details.
> If something goes wrong, it does a great job of diagnosing issues, though sometimes it can lead to a rabbit hole of fixes that
> don't fully address the underlying problem. 
> Overall, I'm amazed at how far it's come with minimal detailed help from me.
> I have learned it is more about asking Claude questions and asking for alternatives than giving it detailed instructions. 
> I have also learned that it is important to give it feedback on the code it generates,
> 
> UPDATE: I think the project is now at a point where it can be used to build real applications. 
> Especially for small teams or solo developers, the ability to quickly iterate on workflow design and implementation with AI assistance is a game-changer.
> So if you are a research team or a small start up, you can use this platform to build your own workflow-based applications using
> your desktop or laptop computers, without needing to hire a team of engineers or set up complex infrastructure.
> There are a lot of examples especially with OSM which is a great dataset to experiment with and show the kinds of 
> workflows that this platform can support. The platform is designed to be flexible and extensible, 
> so you can build on top of it and customize it to your needs.
> 

## See it in action — live example outputs

Facetwork workflows publish interactive maps to a live site:
**[facetwork-maps →](https://rlemke.github.io/facetwork-maps/)** ([repo](https://github.com/rlemke/facetwork-maps)).
It collects real outputs produced end-to-end by FFL workflows on the runtime:

**United States** ([all →](https://rlemke.github.io/facetwork-maps/census/index.html))
- [Per-county metric explorer](https://rlemke.github.io/facetwork-maps/census/metrics/index.html) — 13 ACS metrics with a dropdown, all 50 states + DC
- [State rankings](https://rlemke.github.io/facetwork-maps/census/rankings/index.html) — states ranked on each metric
- [Social Vulnerability Index](https://rlemke.github.io/facetwork-maps/census/svi/index.html) — 6-indicator SVI choropleth, all states
- [Health-facility mapping equity](https://rlemke.github.io/facetwork-maps/census/health-mapping/index.html) — OSM health facilities per capita, state & county
- [H-1B visa approvals](https://rlemke.github.io/facetwork-maps/census/h1b/index.html) — by state & county, multi-year (FY2009–2023)
- [OSM tag-quality by state](https://rlemke.github.io/facetwork-maps/census/tag-quality-states/index.html) — where OSM tags deviate from valid conventions (Osmose QA), per 1,000 km²

**World** ([all →](https://rlemke.github.io/facetwork-maps/world/index.html))
- [Ethnic & cultural enclaves](https://rlemke.github.io/facetwork-maps/world/enclaves/index.html) — heritage-named neighbourhoods from OSM (Chinatown, Japantown, Little Italy, …)
- [Nuclear power sites](https://rlemke.github.io/facetwork-maps/world/nuclear/index.html) · [Major volcanoes](https://rlemke.github.io/facetwork-maps/world/volcanoes/index.html) · [Research telescopes](https://rlemke.github.io/facetwork-maps/world/telescopes/index.html) — from OpenStreetMap
- [LGBTQ+ bars & restaurants](https://rlemke.github.io/facetwork-maps/world/lgbtq/index.html) · [Tesla charging stations](https://rlemke.github.io/facetwork-maps/world/tesla/index.html) — from OpenStreetMap
- [Earthquakes & fault lines](https://rlemke.github.io/facetwork-maps/world/seismic/index.html) — USGS quakes over Bird-2002 plate boundaries
- [Armed conflict](https://rlemke.github.io/facetwork-maps/world/conflict/index.html) — UCDP / UNHCR / IDMC / IPC choropleth
- [OSM under-mapping](https://rlemke.github.io/facetwork-maps/world/under-mapping/index.html) — health facilities per capita, where OSM is under-mapped
- [Power infrastructure](https://rlemke.github.io/facetwork-maps/world/power/index.html) — power plants by source (hydro/coal/solar/wind/nuclear, WRI) + ≥500 kV transmission lines (OSM)
- [OSM tag-quality by country](https://rlemke.github.io/facetwork-maps/world/tag-quality/index.html) — where OSM tags deviate from valid conventions (Osmose QA deprecated + incorrect tags), per 1,000 km²

**Health** ([all →](https://rlemke.github.io/facetwork-maps/health/index.html)) — chronic disease burden (cancer / diabetes / Alzheimer's / stroke) + respiratory-virus hospitalizations over time
- [US mortality by state](https://rlemke.github.io/facetwork-maps/health/us-mortality/index.html) — age-adjusted death rates, all four causes (CDC NCHS)
- [US prevalence by county](https://rlemke.github.io/facetwork-maps/health/us-prevalence/index.html) — adult prevalence of cancer/diabetes/stroke (CDC PLACES, 2,956 counties)
- [World NCD burden](https://rlemke.github.io/facetwork-maps/health/world-ncd/index.html) — diabetes prevalence + NCD mortality by country (WHO / World Bank)
- [US respiratory hospitalizations over time](https://rlemke.github.io/facetwork-maps/health/us-respiratory/index.html) — COVID-19 / flu / RSV new admissions per 100k by state, with a month slider (~5 yrs, CDC NHSN)
- [US hospital strain — bed occupancy](https://rlemke.github.io/facetwork-maps/health/us-hospital-strain/index.html) — % of inpatient & ICU beds occupied + share held by each virus, month slider (CDC NHSN)
- [US respiratory ICU severity](https://rlemke.github.io/facetwork-maps/health/us-icu-severity/index.html) — share of hospitalized COVID/flu/RSV patients in the ICU, month slider (CDC NHSN)
- [US respiratory admissions — children vs adults](https://rlemke.github.io/facetwork-maps/health/us-ped-vs-adult/index.html) — admission rates per 100k by age group (RSV/flu in kids), month slider (CDC NHSN)
- [US "tripledemic" combined burden](https://rlemke.github.io/facetwork-maps/health/us-tripledemic/index.html) — combined COVID + flu + RSV admissions per 100k, winter over winter (CDC NHSN)

**Cancer genomics** ([all →](https://rlemke.github.io/facetwork-maps/cancer/index.html)) — ranked, explainable **gene-evidence tables** (not maps). Given a cancer type, an FFL *evidence graph* fans out over open public genomics — TCGA/GDC tumor-vs-matched-normal expression, GTEx healthy-tissue specificity, GDC survival + somatic mutations, intOGen drivers — into one ranked table where **every score links back to its public dataset and the FFL facet** that computed it.
- [Lung adenocarcinoma](https://rlemke.github.io/facetwork-maps/cancer/luad/index.html) · [Breast](https://rlemke.github.io/facetwork-maps/cancer/brca/index.html) · [Kidney (clear-cell)](https://rlemke.github.io/facetwork-maps/cancer/kirc/index.html) · [Thyroid](https://rlemke.github.io/facetwork-maps/cancer/thca/index.html) · [Prostate](https://rlemke.github.io/facetwork-maps/cancer/prad/index.html) · [Lung squamous](https://rlemke.github.io/facetwork-maps/cancer/lusc/index.html) · [Liver](https://rlemke.github.io/facetwork-maps/cancer/lihc/index.html) · [Head & neck](https://rlemke.github.io/facetwork-maps/cancer/hnsc/index.html) · [Colon](https://rlemke.github.io/facetwork-maps/cancer/coad/index.html) — the 9 TCGA cancers with usable matched normals

Every map carries a footer linking back to the **FFL workflow** (and its parameters)
that generated it, so each output is a worked example of the platform in use. The maps
themselves are pushed to GitHub Pages by an FFL workflow (`census.workflows.PublishToSite`).



## Start Here: Read the Thesis Documents

If you are new to Facetwork, **start with the thesis documents in [`docs/thesis/`](docs/thesis/)** rather than the reference specs. The specs are written for developers who need to implement against the system; the thesis documents explain what Facetwork is, why it was built this way, and where it might go — and are far more informative for a general reader.

| Document | What it covers |
|----------|----------------|
| [`thesis.md`](docs/thesis/thesis.md) / [`thesis.pdf`](docs/thesis/thesis.pdf) | The core thesis: a language-directed, lock-free model for live-updatable distributed workflow execution |
| [`defense.md`](docs/thesis/defense.md) / [`defense.pdf`](docs/thesis/defense.pdf) | Thesis defense Q&A — the design decisions examined under challenge |
| [`ai-authorship.md`](docs/thesis/ai-authorship.md) / [`ai-authorship.pdf`](docs/thesis/ai-authorship.pdf) | How Facetwork's design changes when AI agents, not humans, are the primary authors |
| [`future-thoughts-ai-native.md`](docs/thesis/future-thoughts-ai-native.md) / [`.pdf`](docs/thesis/future-thoughts-ai-native.pdf) | A forward-looking exploration of an AI-native workflow system |
| [`future-thoughts-positioning-dissent.md`](docs/thesis/future-thoughts-positioning-dissent.md) / [`.pdf`](docs/thesis/future-thoughts-positioning-dissent.pdf) | Dissenting companion on Facetwork's positioning in the AI-agent era |

### Research papers

Narrower write-ups drawn from running the system, each built on a specific
production incident or measured result rather than on the design in the
abstract. They also live in [`docs/thesis/`](docs/thesis/).

| Paper | What it argues |
|-------|----------------|
| [`paper-parity-gaps.md`](docs/thesis/paper-parity-gaps.md) / [`.pdf`](docs/thesis/paper-parity-gaps.pdf) | *Passing Every Test While Completely Broken* — how single-address-space test doubles structurally hide distributed coordination bugs; a parity-gap taxonomy from the FFL `catch` clause, which passed its whole suite while broken on the fleet |
| [`paper-informal-fleet.md`](docs/thesis/paper-informal-fleet.md) / [`.pdf`](docs/thesis/paper-informal-fleet.pdf) | *An Informal Fleet* — experience report on running a leaderless workflow runtime across commodity desktops and laptops, where hosts come and go and only the database and object store are stable |
| [`paper-liveness-coverage-drift.md`](docs/thesis/paper-liveness-coverage-drift.md) / [`.pdf`](docs/thesis/paper-liveness-coverage-drift.pdf) | *The Safety Net With a Hole* — coverage drift between the failure states a runtime can enter and the ones its recovery sweep actually looks for; the states a self-healing loop silently stops covering |
| [`paper-intrinsic-routing.md`](docs/thesis/paper-intrinsic-routing.md) / [`.pdf`](docs/thesis/paper-intrinsic-routing.pdf) | *Intrinsic Facts as Routing Keys* — derive routing from facts the program already carries instead of configuring it, plus two production bugs found at the boundary where derivation stops |
| [`paper-geofabrik-replacement.md`](docs/thesis/paper-geofabrik-replacement.md) / [`.pdf`](docs/thesis/paper-geofabrik-replacement.pdf) | *Become Your Own Geofabrik* — self-hosting OSM regional extracts as a fan-out-native, cost-bearing data substrate |
| [`paper-llm-composition.md`](docs/thesis/paper-llm-composition.md) / [`.pdf`](docs/thesis/paper-llm-composition.pdf) | *Lookup, Don't Generate* (**seed draft**) — a capability catalog as the substrate for LLM workflow composition: reuse a catalogued workflow rather than emitting bespoke code per task |

Once you've read enough to understand the shape of the system, continue with the Quick Start and the developer-facing guides below.

---

**Facetwork** is a platform for defining and executing distributed workflows. You describe what should happen in a simple language called **FFL** (Facetwork Flow Language), and Facetwork handles the execution, retries, monitoring, and scaling.

You don't need to be a developer to use Facetwork — if you can fill in a form, you can run workflows from the dashboard.

## Choose Your Path

| I want to... | Start here |
|--------------|------------|
| **Run workflows** from the web UI | [Beginner's Guide](docs/getting-started/beginners-guide.md) |
| **Set up a local server** quickly | [Quick Start](#quick-start) (below) |
| **Run every example (8 standalone repos) in Docker** | [Full-stack Docker Compose](docs/operations/full-stack-compose.md) |
| **Write my own workflows** in FFL | [FFL Tutorial](docs/getting-started/tutorial.md) |
| **Build handlers** in Python | [Agent SDK](spec/60_agent_sdk.md) |
| **Build agents** in other languages | [Agent Libraries](#agent-integration-libraries) |
| **Deploy to a cluster** | [Deployment Guide](docs/operations/deployment.md) |
| **Understand the architecture** | [Architecture](docs/architecture/overview.md) |
| **Contribute to Facetwork** | [Full Technical Reference](CLAUDE.md) |

## Quick Start

> **Brand new — no tools installed, nothing cloned?** Start with the
> **[First-time Install guide](docs/getting-started/install.md)**: what the pieces
> are, prerequisites from scratch, which repo to clone, and four setups (one
> machine · your-machine-as-hub + teammate runners · shared infra on dedicated
> servers · company/cloud deployment). The steps below are the one-machine path.

### Docker (recommended for first-timers)

```bash
git clone https://github.com/rlemke/facetwork.git
cd facetwork

# Start everything: MongoDB + Dashboard + Runner + Sample Agent
docker compose up

# In another terminal, seed example workflows
docker compose run seed
```

Open **http://localhost:8080** — that's the dashboard (it lands on **Runs**). Click **New run** to start one, or browse the **Library** to see what's available.

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
fw ffl seed
python -m facetwork.dashboard --log-format text
```

Open http://localhost:8080.

## Using the Dashboard

The dashboard is where you run workflows, monitor progress, and troubleshoot issues.

The UI is **v3** and is the default — opening `/` lands on **Runs**.

**Running a workflow:**
1. Click **New run** in the sidebar
2. Pick a workflow, fill in the parameters, click **Run**
3. Watch it execute on the detail page — live execution graph, step logs, and progress update automatically

**Finding things:**
- The **Running** / **Completed** / **Failed** tabs and the name box filter the Runs list
- The **Filters** page sets persistent, cross-page filters for the **Library** (Flows) and **Runs** (Workflows) lists — by team, author/runner-user, created/run date range, state, and more (every selector has an **Any**)
- Click any run, then any step, to see its parameters, return values, logs, and duration

**Key pages:** Runs, Library (compiled flows), Catalog, Filters, Servers, Handlers, Fleet, Tasks, Events, Output, PostGIS, and Users/Teams. Full reference: [docs/reference/dashboard.md](docs/reference/dashboard.md).

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
- Teams publish their FFL namespaces to MongoDB via `fw ffl publish mylib.ffl`
- Other teams import published namespaces with `use team.namespace`
- The compiler resolves and validates all cross-team references at compile time
- Handlers are registered independently — teams deploy and update their own handlers without affecting other teams' workflows

This means a domain expert can build a workflow by composing facets from across the organization — data engineering, ML, visualization, notification — without needing to know how any of them are implemented. It's the same idea as `pip install` or `npm install`, but for workflow steps.

## Claude-Authored Workflows (the Catalog)

The same DSL that lets teams share workflows also lets an AI agent **author, version, and run** them — without a file, and without the risk of an LLM silently changing a workflow a team depends on. Claude writes FFL and stores it in a **workflow catalog** (MongoDB collections `claude_workflows` + `claude_workflow_revisions`), exposed through MCP tools:

| Tool | What it does |
|------|--------------|
| `fw_catalog_search` | Find an existing workflow to reuse before authoring a new one |
| `fw_catalog_get` | Inspect an entry + a revision (FFL, parameters, dependencies, versions) |
| `fw_catalog_save` | Store FFL as an immutable, content-hashed revision (no file) |
| `fw_catalog_publish` | Review-approve a revision so it can run unattended |
| `fw_catalog_run` | Run a pinned revision with given parameters (executes on the runner fleet) |

**Why this is safe in a team:**
- **Immutable, versioned revisions.** Saving identical FFL de-dupes; any change creates a new version, and the old version stays runnable — so a teammate or a scheduled job that pinned v3 keeps getting v3 even after Claude writes v4.
- **Run with different parameters, same workflow.** Parameters are runtime inputs, never baked into the body; re-running pins a revision, so you get the identical workflow every time and never worry the LLM changed it underneath you.
- **Review gate.** Every revision starts as a `draft`; `fw_catalog_run` refuses to run it unattended until a human **publishes** it. AI proposes, a person approves, the fleet executes.
- **Discover and reuse.** Claude searches the catalog for a workflow that fits the request instead of regenerating one — the team accumulates a shared, searchable library rather than N near-duplicates.
- **Composable, pinned libraries.** Mark an entry as a `library`; other workflows depend on it by pinned revision, so improving the base never breaks anything built on it.
- **Viewable in the UI.** Each revision materializes a normal flow, so it appears in the dashboard (source, compiled graph, runs) like any other workflow.

The full loop — Claude authors FFL → stores a draft → a reviewer publishes → the runner fleet executes the pinned revision — is verified end to end. See [docs/architecture/claude-workflow-catalog.md](docs/architecture/claude-workflow-catalog.md) for the design.

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
| **Rolling updates** | Update handler code on runners one at a time with `fw fleet rolling-deploy`. Running tasks finish on the old code; new tasks pick up the new code. No downtime. |
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
pytest tests/ --cov=facetwork --cov-report=term-missing    # with coverage
pytest tests/test_parser.py::TestWorkflows -v         # specific test
pytest tests/runtime/test_mongo_store.py --mongodb -v # real MongoDB
pytest tests/dashboard/ -v                            # dashboard tests
```

### Using the Parser

```python
from facetwork import parse, FFLParser, ParseError

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
from facetwork import parse, emit_json, emit_dict

ast = parse("facet User(name: String)")

json_str = emit_json(ast)
data = emit_dict(ast)

# Compact output without locations
json_str = emit_json(ast, include_locations=False, indent=None)
```

### Command-Line Interface

```bash
facetwork input.ffl                        # parse and emit JSON
facetwork input.ffl -o output.json         # output to file
facetwork input.ffl --check                # syntax check only
facetwork input.ffl --compact --no-locations # compact JSON
echo 'facet Test()' | facetwork            # parse from stdin
```

### Executing Workflows Programmatically

```python
from facetwork import parse, emit_dict
from facetwork.runtime import Evaluator, MemoryStore, Telemetry, ExecutionStatus
from facetwork.runtime.agent_poller import AgentPoller, AgentPollerConfig

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
python -m facetwork.dashboard                          # port 8080
python -m facetwork.dashboard --port 9000 --reload     # dev mode
```

### Distributed Runner Service

```bash
python -m facetwork.runtime.runner                                    # default
python -m facetwork.runtime.runner --topics TopicA --max-concurrent 10 # custom
```

### MCP Server

The MCP server exposes FFL compiler and runtime as tools for LLM agents:

```bash
python -m facetwork.mcp              # stdio transport
```

**Tools:** `fw_compile`, `fw_validate`, `fw_execute_workflow`, `fw_continue_step`, `fw_resume_workflow`, `fw_manage_runner`

**Resources:** `fw://runners`, `fw://runners/{id}`, `fw://steps/{id}`, `fw://flows`, `fw://servers`, `fw://tasks`

### Agent Integration Libraries

Facetwork agents can be built in any language. The `agents/` directory has libraries for:

| Language | Directory | Build |
|----------|-----------|-------|
| **Python** | Built into `facetwork.runtime` | `pip install -e .` |
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
fw ffl compile input.ffl -o output.json     # compile FFL
fw ffl publish input.ffl                    # compile + publish to MongoDB
fw ffl run-workflow                         # interactive workflow execution
fw runner start --example osm-geocoder  # start runner
fw runner stop                         # stop all runners
fw runner drain                        # stop + reset tasks to pending
fw runner list                         # show runner fleet
fw db stats                             # database statistics
fw db postgis vacuum                       # PostGIS maintenance
fw db postgis vacuum-status                # check vacuum progress
```

All scripts support `--help`.

## Examples

See [examples/README.md](examples/README.md) for a complete overview of all 15+ examples with feature matrices.

| Example | Highlights |
|---------|-----------|
| [osm-geocoder](https://github.com/rlemke/fwh_osm) | Standalone repo: full-scale OSM ingestion, 23 handler subpackages, PostGIS, pgRouting, GraphHopper |
| `examples/hiv-drug-resistance/` | Bioinformatics: QC branching, error recovery, batch processing |
| [noaa-weather](https://github.com/rlemke/fwh_noaa_weather) | Standalone repo: NOAA GHCN, NDBC buoys, ISD-Lite, climate trends, Nominatim geocoding |
| `examples/devops-deploy/` | Conditional branching, prompt/script blocks, mixins |
| `examples/research-agent/` | LLM integration: 8 prompt-block facets, Claude API |
| `examples/aws-lambda/` | Real boto3: LocalStack, Step Functions, blue-green deploy |
| [jenkins](https://github.com/rlemke/fwh_jenkins) | Standalone repo: CI/CD pipelines (mixin composition, 4 pipeline workflows, 17 simulator handlers + CLI tools) |
| [genomics](https://github.com/rlemke/fwh_genomics) | Bioinformatics: foreach fan-out, joint genotyping (cohort analysis simulator). Standalone repo: install with `pip install -e ~/fw_handlers/fwh_genomics`. |
| [anthropic](https://github.com/rlemke/fwh_anthropic) | Multi-area home for Facetwork wrappers around the surfaces at [github.com/anthropics](https://github.com/anthropics) — Messages, Batch, Files, Agent SDK, Claude Code, Computer Use. 16 facets wired across 6 areas + a cross-area `DocumentQA` composition workflow (Files-API RAG) + opt-in live tests against the real API. Standalone repo: install with `pip install -e ~/fw_handlers/fwh_anthropic`. |

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

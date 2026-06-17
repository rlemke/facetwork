# Facetwork Beginner's Guide

Welcome to Facetwork! This guide gets you from zero to running your first workflow in about 10 minutes.

## What is Facetwork?

Facetwork is a platform for building and running multi-step workflows. You describe *what* should happen in a simple language (FFL), and Facetwork handles the execution, retries, and monitoring.

Think of it like a pipeline builder: you define steps, connect them, and let the system run them — locally on your laptop or distributed across a cluster.

## Local Setup (5 minutes)

This sets up a single-machine environment. For production/cluster deployment, see [deployment.md](../operations/deployment.md).

### Prerequisites

- Python 3.11+
- MongoDB (local or remote)
- Docker (optional, for one-command setup)

### Option A: Docker (easiest)

```bash
git clone https://github.com/rlemke/facetwork.git
cd facetwork

# Start everything: MongoDB + Dashboard + Runner
docker compose up

# In another terminal, seed example workflows
docker compose run seed
```

Open http://localhost:8080 — you're done!

### Option B: Local Python

```bash
git clone https://github.com/rlemke/facetwork.git
cd facetwork

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install with all extras
pip install -e ".[dev,test,dashboard,mcp,mongodb]"

# Copy environment config
cp .env.example .env
# Edit .env to set your MongoDB connection string

# Seed example workflows into the database
scripts/seed-examples

# Start the dashboard
python -m afl.dashboard --log-format text
```

Open http://localhost:8080.

## Running Your First Workflow

### From the Dashboard UI

1. Go to http://localhost:8080
2. Click **Workflows** in the sidebar — this shows all available workflows
3. Click **New** to create a new workflow run
4. Pick a workflow from the dropdown (e.g., `handlers.AddOneWorkflow`)
5. Fill in the input parameters (e.g., `value: 41`)
6. Click **Run**
7. You'll be redirected to the workflow detail page where you can watch it execute

The workflow moves through steps. Each step that needs external work (an *event facet*) creates a task. A **runner** picks up the task, executes the handler, and returns the result. The workflow then advances to the next step.

### From the Command Line

```bash
# Start a runner to process tasks
scripts/start-runner --example hiv-drug-resistance -- --log-format text

# Submit a workflow (dashboard-visible — creates the runner record the UI tracks)
scripts/ffl-run path/to/workflow.ffl --workflow handlers.AddOneWorkflow --inputs '{"value": 41}'
```

All of the above — the dashboard's **New run** button and `scripts/ffl-run` —
run workflows **without any AI involved**. Facetwork is a plain workflow engine;
Claude is optional (see [AI-assisted authoring](#ai-assisted-authoring-claude--mcp) below).

## The Catalog — reusable, versioned workflows

The **Catalog** (dashboard page `/v3/catalog`) stores workflows you can run
**by name (slug), with no file** — each save is an immutable, versioned revision,
and a workflow can pin library dependencies. It's how you share a finished
workflow so anyone can re-run it with new parameters.

**Populate it (no Claude needed)** with the `scripts/catalog` CLI:

```bash
scripts/catalog list                                   # what's already in the catalog
scripts/catalog import my_workflow.ffl --slug demo.my-workflow --publish
scripts/catalog import-package osm-geocoder --tags osm # import an example package's workflows
scripts/catalog backup catalog.json                    # back up / move the catalog
scripts/catalog restore catalog.json
```

`--publish` marks the revision runnable for unattended use (omit it to save a
draft first). Once published, **run it from the dashboard**: open `/v3/catalog`,
pick the workflow, fill parameters, **Run** — again, no AI.

(Claude can also populate the catalog via the MCP `fw_catalog_save` /
`fw_catalog_publish` tools — see below — but the CLI is the no-AI path.)

## AI-assisted authoring (Claude + MCP)

Claude is **optional** but handy for writing, validating, discovering, and
running workflows in plain language. It talks to Facetwork through an **MCP
server** (Model Context Protocol) that exposes the compiler, the catalog, and
runtime tools.

**1. Set up the MCP.** The repo ships an `.mcp.json` at its root — Claude Code
auto-detects it when you open the project. It launches the server with
`python -m facetwork.mcp` and points it at your MongoDB:

```json
{
  "mcpServers": {
    "facetwork": {
      "command": "${FW_PYTHON:-/path/to/.venv/bin/python}",
      "args": ["-m", "facetwork.mcp", "--log-level", "WARNING"],
      "env": { "AFL_MONGODB_URL": "mongodb://localhost:27017" }
    }
  }
}
```

Edit the Python path / Mongo URL to match your setup. (You can run the server
by hand to check it: `python -m facetwork.mcp`.) For other MCP clients, point
them at the same command.

**2. Example Claude prompts** (each maps to MCP tools — `fw_validate`,
`fw_capabilities`, `fw_catalog_match` / `_run` / `_save`):

- *"Validate this FFL and explain any errors."* → `fw_validate`, with rule docs.
- *"What facets operate on a GeoJSON path?"* → `fw_capabilities` (discover primitives).
- *"Find a cataloged workflow that charts a lake's water over time and run it on the Great Salt Lake."* → `fw_catalog_match` → `fw_catalog_run` (reuse, no re-authoring).
- *"Save this workflow to the catalog as `geo.lake-water` and publish it."* → `fw_catalog_save` + `fw_catalog_publish`.
- *"/ffl-first show Tesla charging stations across the US as a heatmap"* → the `ffl-first` skill: discover → reuse-or-author (with review gates) → run.

Claude composes and runs the same workflows you can run by hand — it just turns
a sentence into the right FFL + tool calls. Full MCP reference:
[../architecture/claude-workflow-catalog.md](../architecture/claude-workflow-catalog.md)
and the **MCP server** section of [CLAUDE.md](../../CLAUDE.md).

## Understanding the Dashboard

The dashboard UI is **v3** and is the default — opening `/` lands on **Runs**
(`/v3/workflows`). Full reference: [../reference/dashboard.md](../reference/dashboard.md).

### Key Pages

| Page | URL | What It Shows |
|------|-----|---------------|
| **Runs** | `/v3/workflows` | All workflow runs grouped by namespace, with running/completed/failed tabs and a name filter |
| **Run Detail** | `/v3/workflows/{id}` | Live execution graph, step logs, progress, and recovery actions for a single run |
| **Library** | `/v3/flows` | Compiled FFL flows (namespaces/facets/workflows); shows when each flow was created |
| **Catalog** | `/v3/catalog` | Reusable, versioned workflows/libraries you can run |
| **Filters** | `/v3/filters` | Global, persistent filters for the Library and Runs lists (see below) |
| **Handlers** | `/v3/handlers` | Registered event facet handlers — the code that does the work |
| **Servers** | `/v3/servers` | Runner processes, health status, what they're handling |
| **Fleet** | `/v3/fleet` | Infra services (MongoDB/MinIO/Dashboard, by URL) + runner roles (every server is a runner — no master) |
| **Step Detail** | `/v3/steps/{id}` | Individual step detail — state, parameters, returns, logs, duration |
| **Users / Teams** | `/v3/users`, `/v3/teams` | Manage identities and teams; set who you're "acting as" |

### Finding Your Workflow

- **By state**: on **Runs**, use the **Running** / **Completed** / **Failed** tabs
- **By name**: type in the filter box on the Runs (or Library) page
- **Persistent filters**: the **Filters** page narrows the Library and Runs lists by team, author / runner-user, created / run date range, state, and more — every selector has an **Any** option, and your selections stick across pages until you clear them

### Reading Step Logs

Each step shows a log timeline with:
- **Start time** and **duration** in the attributes table
- **Handler progress** messages (e.g., "processing 245,000 nodes")
- **Success/error** status on the final entry
- **Duration column** on the last log row showing total elapsed time

## Writing Your First FFL Workflow

FFL (Facetwork Flow Language) is how you define workflows. Here's a minimal example:

```afl
namespace myapp {
    /** Adds two numbers together. */
    event facet Add(a: Long, b: Long) => (sum: Long)

    /** Adds 1 to the input, then doubles it. */
    workflow AddAndDouble(value: Long) => (result: Long) andThen {
        added = Add(a = $.value, b = 1)
        doubled = Add(a = added.sum, b = added.sum)
        yield AddAndDouble(result = doubled.sum)
    }
}
```

Key concepts:
- **namespace** — groups related facets and workflows
- **event facet** — a step that needs a handler (external code) to execute
- **workflow** — an entry point with an `andThen` block defining the step sequence
- **`$`** — refers to the workflow's input parameters
- **`step.field`** — references the output of a previous step
- **yield** — returns the workflow's final output

To compile and check your FFL:
```bash
afl myworkflow.ffl --check        # syntax check
afl myworkflow.ffl -o output.json # compile to JSON
```

## Using Other Teams' Workflows

You don't have to build everything from scratch. FFL namespaces work like libraries — other teams publish their facets, and you `use` them in your workflow:

```afl
namespace my.analysis {
    use data.warehouse        // the data team's extraction facets
    use ml.predictions        // the ML team's forecasting facets

    workflow QuarterlyForecast(quarter: String) => (report: String) andThen {
        raw = ExtractSalesData(period = $.quarter)         // data team's facet
        forecast = PredictNextQuarter(history = raw.data)  // ML team's facet
        report = RenderReport(data = forecast.prediction)  // your facet
        yield QuarterlyForecast(report = report.output_path)
    }
}
```

To publish your own workflows for others to use:
```bash
scripts/publish my_workflows.ffl              # publish to MongoDB
scripts/publish my_workflows.ffl --version 2.0  # with a version tag
```

Other teams then import your namespace with `use` — the compiler validates all cross-team references at compile time. Each team deploys and updates their own handlers independently.

## How Facetwork Runs Your Workflows

When you click **Run** in the dashboard, your workflow doesn't execute on a single machine. Facetwork distributes the work across a **cluster of runner servers**:

1. The runtime breaks your workflow into **steps**
2. Each step that needs work creates a **task** in the database
3. An available **runner server** picks up the task, runs the handler, and writes the result
4. The workflow advances to the next step automatically

This means:
- **Long jobs are safe** — if a step takes hours (importing a large dataset, training a model), and a server goes down, the task is automatically reassigned to another server. Nothing is lost.
- **More servers = more throughput** — add runner servers to handle more tasks in parallel. No code changes needed.
- **Updates without downtime** — handler code can be updated on servers one at a time. Running tasks finish on the old code; new tasks use the new code.
- **Everything is visible** — the dashboard shows every server's health, active tasks, step logs, and timing in real time.

For local development, Docker runs everything on your machine. For production, see the [Deployment Guide](../operations/deployment.md).

## What's Next?

- **[FFL Language Reference](../reference/language/grammar.md)** — full syntax guide with all constructs
- **[Workflow Design Patterns](../reference/examples.md)** — real-world FFL examples
- **[Building Handlers](../reference/agent-sdk.md)** — write Python handlers for your event facets
- **[Runtime & Execution](../reference/runtime.md)** — how the engine evaluates workflows
- **[Deployment Guide](../operations/deployment.md)** — cluster setup with multiple runners
- **[Full Reference (CLAUDE.md)](../../CLAUDE.md)** — complete technical reference for contributors

## Getting Help

- Browse the `examples/` directory — each example has FFL files, handlers, and tests
- Check `docs/reference/` for detailed specifications
- Use the MCP server (`python -m facetwork.mcp`) for AI-assisted authoring — see [AI-assisted authoring](#ai-assisted-authoring-claude--mcp)

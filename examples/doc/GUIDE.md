# Facetwork Examples Guide

This guide helps you choose the right example as a starting point for your own FFL workflows and agents.

## At a Glance

| Example | Complexity | Key Pattern | Handlers | Best For |
|---------|-----------|-------------|----------|----------|
| [hello-agent](../hello-agent/) | Beginner | Single event facet + workflow | 1 (inline) | Learning the execution model |
| [osm-lz](https://github.com/rlemke/fwh_osm_lz) | Beginner | Cross-namespace composition | 0 (reuses OSM) | Composing existing facets into new workflows |
| [genomics](https://github.com/rlemke/fwh_genomics) | Intermediate | foreach fan-out, linear fan-in | 45 | Parallel batch processing pipelines. **Standalone repo.** |
| [jenkins](https://github.com/rlemke/fwh_jenkins) | Intermediate | Mixin composition (`with`) | 17 | Cross-cutting concerns (retry, timeout, auth). **Standalone repo.** |
| [aws-lambda](../aws-lambda/) | Intermediate | Real cloud calls + mixins | 12 | Cloud service integration with LocalStack |
| [census-us](https://github.com/rlemke/fwh_census_us) | Intermediate | API + shapefile ETL, DB ingestion | 36 | US Census ACS + TIGER county demographics with dashboard map visualization. **Standalone repo.** |
| [noaa-weather](https://github.com/rlemke/fwh_noaa_weather) | Intermediate | Tools/handlers/shared-lib pattern, deterministic mocks | 13 | NOAA GHCN/NDBC ingest + climate trends. **Standalone repo.** |
| [osm-geocoder](https://github.com/rlemke/fwh_osm) | Advanced | Large-scale event facets, source adapters | 132+ | Production-scale OSM ingestion + PostGIS + routing graphs. **Standalone repo.** |
| [osm-lz](https://github.com/rlemke/fwh_osm_lz) | Advanced | Pure-FFL composition (workflow catalog) | 0 (consumes fwh_osm) | Continental-scale OSM LZ road infra + GTFS transit. **Standalone repo, depends on fwh_osm.** |
| [site-selection](../site-selection/) | Intermediate | OSM + Census scoring | 22 | Spatial scoring pipelines |
| [monte-carlo-risk](../monte-carlo-risk/) | Intermediate | Pure-Python math stubs | 8 | Financial risk simulation |
| [ml-hyperparam-sweep](../ml-hyperparam-sweep/) | Intermediate | Statement-level andThen, prompt blocks | 6 | ML training pipelines |
| [research-agent](../research-agent/) | Intermediate | Composed facets + typed parsers over `anthropic.messages.CreateMessage` (fwh_anthropic) | 8 parsers | LLM-driven research workflows |
| [multi-agent-debate](../multi-agent-debate/) | Intermediate | Multi-agent personas, scoring/voting | 8 | Multi-agent interaction patterns |
| [multi-round-debate](../multi-round-debate/) | Intermediate | Composed facets, cross-round state | 8 | Iterative rounds with convergence |
| [tool-use-agent](../tool-use-agent/) | Intermediate | Tool-as-event-facet, planning | 8 | Tool-use agent orchestration |
| [data-quality-pipeline](../data-quality-pipeline/) | Intermediate | Schema instantiation, array types, `(expr)` | 8 | Data quality assessment pipelines |
| [sensor-monitoring](https://github.com/rlemke/fwh_sensor_monitoring) | Intermediate | Unary negation, null literals, map indexing, mixin alias | 6 | Sensor monitoring with RegistryRunner. **Standalone repo.** |
| [anthropic](https://github.com/rlemke/fwh_anthropic) | Foundation | Multi-area integration package, per-area `_lib/` + handlers/ subpackages, JSON-bridge fields for cross-area composition, opt-in live tests | 16 | Home for Facetwork wrappers around the surfaces at github.com/anthropics (Messages 6, Batch 4, Files 3, Agent SDK 1, Claude Code 1, Computer Use 1) + `DocumentQA` cross-area composition workflow. **Standalone repo.** |

## Learning Path

Start simple and build up to more complex patterns:

```
1. hello-agent          Understand the core execution model
       |
2. volcano-query        Learn namespace composition (no handlers needed)
       |
   +---+---+---+---+
   |       |   |   |
3a. genomics  3b. jenkins  3c. census-us  3d. site-selection
    foreach       mixins       API + ETL       OSM + Census scoring
       |           |           + maps
       +-----+-----+
             |
4. aws-lambda           Real cloud integration + mixins + foreach
       |
   +---+---+---+
   |       |   |
5a. monte-carlo-risk  5b. ml-hyperparam-sweep  5c. research-agent
    math stubs             prompt blocks             LLM integration
       |                   + andThen foreach          + ClaudeAgentRunner
       +--------+----------+
                |
6. multi-agent-debate   Multi-agent personas + scoring/voting
       |
   +---+---+
   |       |
6a. multi-round-debate  6b. tool-use-agent
    composed facets          tool-as-event-facet
    + convergence            + planning
       |                     |
       +----------+----------+
                  |
7. data-quality-pipeline  Schema instantiation + array types + (expr) grouping
       |
8. sensor-monitoring     Unary negation + null literals + map indexing + mixin alias
       |                  + RegistryRunner-first
9. osm-geocoder         Full production-scale agent (standalone repo: github.com/rlemke/fwh_osm)
       |
10. osm-lz              Continental-scale workflow catalog (standalone repo: github.com/rlemke/fwh_osm_lz; consumes fwh_osm handlers)
```

> **Note on standalone repos.** Most examples now live in their own
> GitHub repos (`fwh_anthropic`, `fwh_osm`, `fwh_jenkins`, …). They
> surface in Facetwork via the `facetwork.examples` entry point and
> show up as `--example <name>` exactly like the in-repo examples
> below. Install one (or all) with the registry-driven helper:
>
> ```bash
> scripts/install-example --list              # see what's registered
> scripts/install-example anthropic --check   # clone + pip install + verify
> scripts/install-example --all               # install everything
> scripts/install-anthropic --all --check     # convenience wrapper for the
>                                             # anthropic package (handles
>                                             # agent_sdk/mcp extras and
>                                             # reports env readiness)
> ```
>
> The script clones into `~/fw_handlers/` (override via `--dir` or
> `FWH_HANDLERS_ROOT`), `pip install -e`s each repo, and (with
> `--check`) verifies the entry point is discoverable.

## Choosing an Example

### "I want to understand how Facetwork works"

Start with **[hello-agent](../hello-agent/)**. It's a single file that walks through the entire execution cycle: compile FFL, execute workflow, pause at event facet, agent processes task, resume to completion.

### "I want to compose existing facets into new workflows"

Look at **[osm-lz](https://github.com/rlemke/fwh_osm_lz)**. It has zero custom handlers — it imports event facets from the OSM geocoder and composes them into a new query pipeline using `use` imports and `andThen` chains.

### "I want to process items in parallel"

Use **[genomics](https://github.com/rlemke/fwh_genomics)** (standalone repo) as your template. It demonstrates `andThen foreach` for fan-out (per-sample processing) and linear `andThen` chains for fan-in (cohort analysis). The cache layer shows factory-built handlers from a resource registry. Install with `pip install -e ~/fw_handlers/fwh_genomics`.

### "I want to add retry, timeout, or other cross-cutting concerns"

Use **[jenkins](https://github.com/rlemke/fwh_jenkins)** (standalone repo) as your template. It demonstrates the `with` mixin composition pattern at both signature level and call time, plus implicit defaults. Install with `pip install -e ~/fw_handlers/fwh_jenkins`.

### "I want to build an ETL pipeline with API and shapefile data"

Use **[census-us](https://github.com/rlemke/fwh_census_us)** (standalone repo) as your template. It downloads ACS demographics from the Census API and TIGER shapefiles, extracts and joins the data, ingests into MongoDB with GeoJSON indexes, and visualizes results on an interactive Leaflet.js map in the dashboard. The choropleth dropdown lets you color counties by population density, income, education, commuting, and more. Install with `pip install -e ~/fw_handlers/fwh_census_us`.

### "I want to integrate with real cloud services"

Use **[aws-lambda](../aws-lambda/)** as your template. The handlers make real boto3 calls to LocalStack, showing how to build agents that interact with actual APIs.

### "I want to build a large-scale agent with many event facets"

Study **[osm-geocoder](https://github.com/rlemke/fwh_osm)** (standalone repo). With 132+ handlers across 23 subpackages, it demonstrates how to organize a production-scale agent with factory-built handlers, geographic registries, source adapters (PBF / PostGIS / GeoJSON), and namespace-per-domain architecture. Install with `pip install -e ~/fw_handlers/fwh_osm`.

### "I want a domain pipeline with both CLIs and FFL handlers backed by one library"

Study **[noaa-weather](https://github.com/rlemke/fwh_noaa_weather)** (standalone repo). It is the canonical reference for the **tools / handlers / shared-lib** pattern: every operation has a CLI under `tools/` *and* an FFL handler, and both call into the same `tools/_noaa_tools/` modules via a `handlers/shared/<domain>_utils.py` shim. Run a single ingest step from the shell or thread it into a workflow — the cache + outputs are identical either way. Install with `pip install -e ~/fw_handlers/fwh_noaa_weather`.

### "I want to simulate financial risk or run pure-Python analytics"

Use **[monte-carlo-risk](../monte-carlo-risk/)**. It demonstrates GBM simulation, VaR/CVaR, Greeks, and stress testing using deterministic pure-Python stubs with no external dependencies.

### "I want to sweep hyperparameters or train ML models"

Use **[ml-hyperparam-sweep](../ml-hyperparam-sweep/)**. It showcases statement-level andThen, prompt blocks, map literals, and andThen foreach as a central pattern for parallel training runs.

### "I want to build LLM-driven research workflows"

Use **[research-agent](../research-agent/)**. Each domain step is a composed facet that calls `anthropic.messages.CreateMessage` (from the [fwh_anthropic](https://github.com/rlemke/fwh_anthropic) standalone package) with a JSON-shape system prompt, then hands the response to a typed `Parse<Schema>` event facet. This is the canonical pattern for getting structured LLM data into a Facetwork workflow without resorting to prompt blocks or ClaudeAgentRunner.

For more direct API surface coverage — Messages, Batch, Files, Agent SDK, Claude Code, Computer Use — see the **[anthropic](https://github.com/rlemke/fwh_anthropic)** standalone repo. It is the multi-area home for Facetwork wrappers around the surfaces at github.com/anthropics, with 16 facets wired across 6 areas plus a cross-area `DocumentQA` composition workflow that demonstrates Files-API RAG (`UploadFile → CreateMessageWithFile`). Install with `pip install -e ~/fw_handlers/fwh_anthropic`. Unit tests mock at the SDK boundary; opt-in live tests (`pytest -m live --run-live`) exercise the real API.

### "I want to build multi-agent interaction patterns"

Use **[multi-agent-debate](../multi-agent-debate/)**. Three debate agents (proposer, critic, synthesizer) with distinct personas argue, rebut, score, and synthesize positions. It demonstrates agent-to-agent output dependency, scoring/voting mechanisms, and multi-agent persona patterns.

### "I want to build iterative multi-round systems with convergence"

Use **[multi-round-debate](../multi-round-debate/)**. It demonstrates composed facets as the primary architectural pattern — a `DebateRound` facet encapsulates 12 steps. The workflow calls it 3 times with cross-round state (synthesis and scores flow from round to round). Convergence metrics use arithmetic (`/`, `%`) to detect stabilization.

### "I want to model tools as event facets with planned orchestration"

Use **[tool-use-agent](../tool-use-agent/)**. Six tools (web search, deep search, calculator, code executor, synthesizer, formatter) are modeled as event facets. A planning facet decides tool order and strategy. Demonstrates statement-level andThen for chaining tool calls, `++` concatenation, and andThen foreach for parallel subtopic searches.

### "I want to use schema instantiation, array types, or expression grouping"

Use **[data-quality-pipeline](../data-quality-pipeline/)**. It is the first example to showcase schema instantiation as steps (`cfg = QualityConfig(...)` in andThen blocks), array type annotations (`[ColumnProfile]`, `[ValidationResult]`), and parenthesized expression grouping (`(a + b) * c`). The pipeline profiles datasets, validates quality, computes weighted scores, and generates remediation reports.

### "I want to use unary negation, null literals, computed map keys, or mixin aliases"

Use **[sensor-monitoring](https://github.com/rlemke/fwh_sensor_monitoring)** (standalone repo). It is the first example to showcase unary negation (`-10.0`, `-40.0`), null literals as call arguments (`last_reading = null`), computed map indexing (`$.configs[step.field]`), and mixin aliases (`with RetryPolicy() as retry`). It also demonstrates RegistryRunner as the primary agent entry point. Install with `pip install -e ~/fw_handlers/fwh_sensor_monitoring`.

### "I want to score and rank spatial locations"

Use **[site-selection](../site-selection/)**. It combines OSM amenity extraction with Census demographics to score and rank counties for restaurant site selection.

### "I want to run a pipeline in Docker with MongoDB persistence"

Start with **[osm-lz](https://github.com/rlemke/fwh_osm_lz)** (standalone repo). It is the canonical demo of cross-package FFL composition — a pure workflow catalog that orchestrates `osm.cache.*`, `osm.cache.GraphHopper.*`, `osm.Roads.ZoomBuilder.*`, and `osm.Transit.GTFS.*` facets across 14 regions and 11 transit agencies, contributing zero handlers itself. Install with `pip install -e ~/fw_handlers/fwh_osm` (handlers) + `pip install -e ~/fw_handlers/fwh_osm_lz` (catalog).

## FFL Patterns by Example

### Namespaces and Schemas

Every example uses namespaces. Schemas are defined inside namespaces and referenced via `use` imports or fully-qualified names.

```afl
// hello-agent: minimal namespace
namespace hello {
    event facet Greet(name: String) => (message: String)
}

// genomics: schema + namespace
namespace genomics.types {
    schema QcReport {
        sample_id: String, total_reads: Long, ...
    }
}
namespace genomics.Facets {
    use genomics.types
    event facet QcReads(...) => (report: QcReport)
}
```

### andThen Chains

Sequential step composition — every example except `hello-agent` uses this pattern.

```afl
// aws-lambda: pure chain
workflow DeployAndInvoke(...) => (...) andThen {
    created = aws.lambda.CreateFunction(...)
    invoked = aws.lambda.InvokeFunction(...)
    info = aws.lambda.GetFunctionInfo(...)
    yield DeployAndInvoke(...)
}
```

### foreach Fan-Out

Parallel iteration over a collection. Used in genomics and aws-lambda.

```afl
// genomics: per-sample processing
workflow SamplePipeline(...) => (...) andThen foreach sample in $.samples {
    qc = QcReads(sample_id = $.sample.sample_id, ...)
    aligned = AlignReads(...)
    called = CallVariants(...)
    yield SamplePipeline(...)
}
```

### Mixin Composition

Attach cross-cutting behaviors with `with`. Used in jenkins and aws-lambda.

```afl
// jenkins: call-time mixins
build = MavenBuild(workspace_path = src.info.workspace_path,
    goals = "clean package") with Timeout(minutes = 20) with Retry(maxAttempts = 2)

// jenkins: signature-level mixin
event facet GitCheckout(...) => (info: ScmInfo) with Timeout(minutes = 10)

// jenkins: implicit defaults
implicit defaultRetry = Retry(maxAttempts = 3, backoffSeconds = 30)
```

### Facet Encapsulation

Wrap low-level event facets in composed facets to expose simple, domain-focused interfaces. This is the **library facet** pattern — infrastructure teams define the composed facets; consumers use them without knowing the internal steps.

```afl
// Low-level: 3 separate event facets (agent developers write handlers for these)
cached = osm.ops.CacheRegion(region = "Belgium")
downloaded = osm.ops.DownloadPBF(cache = cached.cache)
graph = osm.ops.RoutingGraph(cache = downloaded.downloadCache)

// Encapsulated: 1 composed facet (workflow authors use this)
routable = BuildRoutingData(region = "Belgium")
```

The composed facet is a regular `facet` (not `event facet`) with an `andThen` body. The runtime expands its steps inline — the internal event facets still pause for agents, but the user only sees the simple outer interface.

Every intermediate-to-advanced example uses this pattern:

| Example | Composed Facets | What They Hide |
|---------|----------------|----------------|
| [genomics](https://github.com/rlemke/fwh_genomics) | `ProcessSample`, `AnalyzeCohort` | QC→Align→CallVariants chain, genotyping→annotation pipeline |
| [jenkins](https://github.com/rlemke/fwh_jenkins) | `BuildAndTest`, `DeployWithNotification` | Credentials, timeouts, retries, notification channels |
| [aws-lambda](../aws-lambda/) | `DeployFunction`, `UpdateAndVerify` | Lambda create→invoke→verify steps |
| [census-us](https://github.com/rlemke/fwh_census_us) | `AnalyzeState`, `AnalyzeStateWithDB` | Download→Extract→Join→Ingest pipeline per state |
| [osm-geocoder](https://github.com/rlemke/fwh_osm) | `PrepareRegion`, `BuildRoutingData` | Cache→download→tile/graph pipeline |

### Cross-Namespace Composition

Import facets from other namespaces to compose new workflows.

```afl
// volcano-query: compose OSM facets
namespace volcano {
    use osm.ops
    use osm.Filters
    workflow FindVolcanoes(...) => (...) andThen {
        data = LoadVolcanoData(region = $.state)
        filtered = FilterByOSMTag(...)
        yield FindVolcanoes(...)
    }
}
```

### String Concatenation

The `++` operator for building dynamic strings.

```afl
// jenkins: dynamic messages
message = "Deployed " ++ $.image_tag ++ " to k8s/" ++ $.k8s_namespace

// volcano-query: dynamic titles
title = $.state ++ " Volcanoes"
```

## Handler Patterns

### Dispatch Adapter (Recommended)

All examples with handlers use this pattern:

```python
NAMESPACE = "my.namespace"

_DISPATCH = {
    f"{NAMESPACE}.FacetA": _handler_a,
    f"{NAMESPACE}.FacetB": _handler_b,
}

def handle(payload: dict) -> dict:
    handler = _DISPATCH[payload["_facet_name"]]
    return handler(payload)
```

### Factory-Built Handlers

For large numbers of similar handlers, use a factory. See genomics `cache_handlers.py`:

```python
def _make_handler(resource_name, resource_url, resource_type):
    def handler(payload):
        return {"cache": {"url": resource_url, "path": f"/cache/{resource_name}", ...}}
    return handler

_DISPATCH = {f"genomics.cache.reference.{name}": _make_handler(name, url, "reference")
             for name, url in REFERENCE_REGISTRY.items()}
```

### Dual-Mode Registration

Every agent supports both AgentPoller and RegistryRunner:

```python
# AgentPoller (standalone)
def register_my_handlers(poller):
    for fqn, func in _DISPATCH.items():
        poller.register(fqn, func)

# RegistryRunner (database-driven)
def register_handlers(runner):
    for facet_name in _DISPATCH:
        runner.register_handler(facet_name=facet_name,
            module_uri=f"file://{os.path.abspath(__file__)}", entrypoint="handle")
```

## Running Any Example

### In-repo examples

```bash
# 1. Install dependencies
source .venv/bin/activate
pip install -e ".[dev]"
pip install -r examples/<name>/requirements.txt  # if exists

# 2. Compile check
afl examples/<name>/ffl/<file>.ffl --check

# 3. Register handlers + start the runner (recommended)
scripts/start-runner --example <name> -- --log-format text

# 4. Or run the legacy AgentPoller directly
PYTHONPATH=. python examples/<name>/agent.py
```

### Standalone-repo examples (jenkins, noaa-weather, osm-geocoder)

```bash
# 1. Clone + install once
git clone https://github.com/rlemke/<fwh_repo>.git ~/fw_handlers/<fwh_repo>
pip install -e ~/fw_handlers/<fwh_repo>

# 2. From a Facetwork checkout — same start-runner flag as in-repo examples
scripts/start-runner --example <name> -- --log-format text
```

The `facetwork.examples` entry point declared in each standalone repo's
`pyproject.toml` makes it discoverable without any edits to the
Facetwork repo. Each standalone repo also ships a `tools/` directory
with CLIs that call the same shared tool-library the FFL handlers use.

## Detailed Documentation

Each example has its own detailed user guide:

| Example | User Guide |
|---------|-----------|
| hello-agent | [USER_GUIDE.md](../hello-agent/USER_GUIDE.md) |
| osm-lz | [USER_GUIDE.md](https://github.com/rlemke/fwh_osm_lz/blob/main/USER_GUIDE.md) — in standalone repo |
| genomics | [USER_GUIDE.md](https://github.com/rlemke/fwh_genomics/blob/main/USER_GUIDE.md) — in standalone repo |
| jenkins | [USER_GUIDE.md](https://github.com/rlemke/fwh_jenkins/blob/main/USER_GUIDE.md) — in standalone repo |
| aws-lambda | [USER_GUIDE.md](../aws-lambda/USER_GUIDE.md) |
| census-us | [USER_GUIDE.md](https://github.com/rlemke/fwh_census_us/blob/main/USER_GUIDE.md) — in standalone repo |
| noaa-weather | [USER_GUIDE.md](https://github.com/rlemke/fwh_noaa_weather/blob/main/USER_GUIDE.md) — in standalone repo |
| osm-geocoder | [USER_GUIDE.md](https://github.com/rlemke/fwh_osm/blob/main/USER_GUIDE.md) — in standalone repo |
| osm-lz | [USER_GUIDE.md](https://github.com/rlemke/fwh_osm_lz/blob/main/USER_GUIDE.md) — in standalone repo |
| site-selection | [USER_GUIDE.md](../site-selection/USER_GUIDE.md) |
| monte-carlo-risk | [USER_GUIDE.md](../monte-carlo-risk/USER_GUIDE.md) |
| ml-hyperparam-sweep | [USER_GUIDE.md](../ml-hyperparam-sweep/USER_GUIDE.md) |
| research-agent | [USER_GUIDE.md](../research-agent/USER_GUIDE.md) |
| multi-agent-debate | [USER_GUIDE.md](../multi-agent-debate/USER_GUIDE.md) |
| multi-round-debate | [USER_GUIDE.md](../multi-round-debate/USER_GUIDE.md) |
| tool-use-agent | [USER_GUIDE.md](../tool-use-agent/USER_GUIDE.md) |
| data-quality-pipeline | [USER_GUIDE.md](../data-quality-pipeline/USER_GUIDE.md) |
| sensor-monitoring | [USER_GUIDE.md](https://github.com/rlemke/fwh_sensor_monitoring/blob/main/USER_GUIDE.md) — in standalone repo |
| anthropic | [README.md](https://github.com/rlemke/fwh_anthropic/blob/main/README.md) + [CLAUDE.md](https://github.com/rlemke/fwh_anthropic/blob/main/CLAUDE.md) — in standalone repo |

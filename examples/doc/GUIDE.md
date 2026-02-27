# AgentFlow Examples Guide

This guide helps you choose the right example as a starting point for your own AFL workflows and agents.

## At a Glance

| Example | Complexity | Key Pattern | Handlers | Best For |
|---------|-----------|-------------|----------|----------|
| [hello-agent](../hello-agent/) | Beginner | Single event facet + workflow | 1 (inline) | Learning the execution model |
| [volcano-query](../volcano-query/) | Beginner | Cross-namespace composition | 0 (reuses OSM) | Composing existing facets into new workflows |
| [genomics](../genomics/) | Intermediate | foreach fan-out, linear fan-in | 45 | Parallel batch processing pipelines |
| [jenkins](../jenkins/) | Intermediate | Mixin composition (`with`) | 17 | Cross-cutting concerns (retry, timeout, auth) |
| [aws-lambda](../aws-lambda/) | Intermediate | Real cloud calls + mixins | 12 | Cloud service integration with LocalStack |
| [census-us](../census-us/) | Intermediate | API + shapefile ETL, DB ingestion | 30 | Census data pipeline with dashboard visualization |
| [osm-geocoder](../osm-geocoder/) | Advanced | Large-scale event facets | 580+ | Production-scale agent with many namespaces |
| [continental-lz](../continental-lz/) | Advanced | Docker orchestration | Linked | Multi-region pipeline with Docker Compose |
| [site-selection](../site-selection/) | Intermediate | OSM + Census scoring | 22 | Spatial scoring pipelines |
| [monte-carlo-risk](../monte-carlo-risk/) | Intermediate | Pure-Python math stubs | 8 | Financial risk simulation |
| [ml-hyperparam-sweep](../ml-hyperparam-sweep/) | Intermediate | Statement-level andThen, prompt blocks | 6 | ML training pipelines |
| [research-agent](../research-agent/) | Intermediate | Prompt blocks, ClaudeAgentRunner | 8 | LLM-driven research workflows |
| [multi-agent-debate](../multi-agent-debate/) | Intermediate | Multi-agent personas, scoring/voting | 8 | Multi-agent interaction patterns |

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
7. osm-geocoder         Full production-scale agent
       |
8. continental-lz       Docker-orchestrated multi-region pipeline
```

## Choosing an Example

### "I want to understand how AgentFlow works"

Start with **[hello-agent](../hello-agent/)**. It's a single file that walks through the entire execution cycle: compile AFL, execute workflow, pause at event facet, agent processes task, resume to completion.

### "I want to compose existing facets into new workflows"

Look at **[volcano-query](../volcano-query/)**. It has zero custom handlers — it imports event facets from the OSM geocoder and composes them into a new query pipeline using `use` imports and `andThen` chains.

### "I want to process items in parallel"

Use **[genomics](../genomics/)** as your template. It demonstrates `andThen foreach` for fan-out (per-sample processing) and linear `andThen` chains for fan-in (cohort analysis). The cache layer shows factory-built handlers from a resource registry.

### "I want to add retry, timeout, or other cross-cutting concerns"

Use **[jenkins](../jenkins/)** as your template. It demonstrates the `with` mixin composition pattern at both signature level and call time, plus implicit defaults.

### "I want to build an ETL pipeline with API and shapefile data"

Use **[census-us](../census-us/)** as your template. It downloads ACS demographics from the Census API and TIGER shapefiles, extracts and joins the data, ingests into MongoDB with GeoJSON indexes, and visualizes results on an interactive Leaflet.js map in the dashboard. The choropleth dropdown lets you color counties by population density, income, education, commuting, and more.

### "I want to integrate with real cloud services"

Use **[aws-lambda](../aws-lambda/)** as your template. The handlers make real boto3 calls to LocalStack, showing how to build agents that interact with actual APIs.

### "I want to build a large-scale agent with many event facets"

Study **[osm-geocoder](../osm-geocoder/)**. With 580+ handlers across 15 modules and 44 AFL files, it demonstrates how to organize a production-scale agent with factory-built handlers, geographic registries, and namespace-per-domain architecture.

### "I want to simulate financial risk or run pure-Python analytics"

Use **[monte-carlo-risk](../monte-carlo-risk/)**. It demonstrates GBM simulation, VaR/CVaR, Greeks, and stress testing using deterministic pure-Python stubs with no external dependencies.

### "I want to sweep hyperparameters or train ML models"

Use **[ml-hyperparam-sweep](../ml-hyperparam-sweep/)**. It showcases statement-level andThen, prompt blocks, map literals, and andThen foreach as a central pattern for parallel training runs.

### "I want to build LLM-driven research workflows"

Use **[research-agent](../research-agent/)**. Every event facet has a prompt block, making it the showcase for ClaudeAgentRunner / LLMHandler. It demonstrates chained LLM steps across planning, gathering, analysis, and writing.

### "I want to build multi-agent interaction patterns"

Use **[multi-agent-debate](../multi-agent-debate/)**. Three debate agents (proposer, critic, synthesizer) with distinct personas argue, rebut, score, and synthesize positions. It demonstrates agent-to-agent output dependency, scoring/voting mechanisms, and multi-agent persona patterns.

### "I want to score and rank spatial locations"

Use **[site-selection](../site-selection/)**. It combines OSM amenity extraction with Census demographics to score and rank counties for restaurant site selection.

### "I want to run a pipeline in Docker with MongoDB persistence"

Start with **[continental-lz](../continental-lz/)**. It has its own `docker-compose.yml` with MongoDB, dashboard, runner, and agent services, demonstrating a complete deployment topology.

## AFL Patterns by Example

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
cached = osm.geo.Operations.Cache(region = "Belgium")
downloaded = osm.geo.Operations.Download(cache = cached.cache)
graph = osm.geo.Operations.RoutingGraph(cache = downloaded.downloadCache)

// Encapsulated: 1 composed facet (workflow authors use this)
routable = BuildRoutingData(region = "Belgium")
```

The composed facet is a regular `facet` (not `event facet`) with an `andThen` body. The runtime expands its steps inline — the internal event facets still pause for agents, but the user only sees the simple outer interface.

Every intermediate-to-advanced example uses this pattern:

| Example | Composed Facets | What They Hide |
|---------|----------------|----------------|
| [genomics](../genomics/) | `ProcessSample`, `AnalyzeCohort` | QC→Align→CallVariants chain, genotyping→annotation pipeline |
| [jenkins](../jenkins/) | `BuildAndTest`, `DeployWithNotification` | Credentials, timeouts, retries, notification channels |
| [aws-lambda](../aws-lambda/) | `DeployFunction`, `UpdateAndVerify` | Lambda create→invoke→verify steps |
| [census-us](../census-us/) | `AnalyzeState`, `AnalyzeStateWithDB` | Download→Extract→Join→Ingest pipeline per state |
| [osm-geocoder](../osm-geocoder/) | `PrepareRegion`, `BuildRoutingData` | Cache→download→tile/graph pipeline |

### Cross-Namespace Composition

Import facets from other namespaces to compose new workflows.

```afl
// volcano-query: compose OSM facets
namespace volcano {
    use osm.geo.Operations
    use osm.geo.Filters
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

```bash
# 1. Install dependencies
source .venv/bin/activate
pip install -e ".[dev]"
pip install -r examples/<name>/requirements.txt  # if exists

# 2. Compile check
python -m afl.cli examples/<name>/afl/<file>.afl --check

# 3. Run the agent
PYTHONPATH=. python examples/<name>/agent.py

# 4. RegistryRunner mode
AFL_USE_REGISTRY=1 PYTHONPATH=. python examples/<name>/agent.py

# 5. With MongoDB persistence
AFL_MONGODB_URL=mongodb://localhost:27017 AFL_MONGODB_DATABASE=afl \
    PYTHONPATH=. python examples/<name>/agent.py
```

## Detailed Documentation

Each example has its own detailed user guide:

| Example | User Guide |
|---------|-----------|
| hello-agent | [USER_GUIDE.md](../hello-agent/USER_GUIDE.md) |
| volcano-query | [USER_GUIDE.md](../volcano-query/USER_GUIDE.md) |
| genomics | [USER_GUIDE.md](../genomics/USER_GUIDE.md) |
| jenkins | [USER_GUIDE.md](../jenkins/USER_GUIDE.md) |
| aws-lambda | [USER_GUIDE.md](../aws-lambda/USER_GUIDE.md) |
| census-us | *(no user guide yet)* |
| osm-geocoder | [USER_GUIDE.md](../osm-geocoder/USER_GUIDE.md) |
| continental-lz | [USER_GUIDE.md](../continental-lz/USER_GUIDE.md) |
| site-selection | *(no user guide yet)* |
| monte-carlo-risk | *(no user guide yet)* |
| ml-hyperparam-sweep | *(no user guide yet)* |
| research-agent | *(no user guide yet)* |
| multi-agent-debate | [USER_GUIDE.md](../multi-agent-debate/USER_GUIDE.md) |

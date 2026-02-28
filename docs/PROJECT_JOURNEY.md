# AgentFlow: The Journey of Building a Distributed Workflow Platform - no human coding -- only Claude -- even the docs

*From a grammar file to 500,000-step distributed workflows across 50 US states*

---

## What is AgentFlow?

AgentFlow is a platform for defining, compiling, and executing distributed workflows. At its core is **AFL** (Agent Flow Language) — a domain-specific language for describing multi-step computations that are compiled to JSON and executed by distributed agents across a microservice architecture.

The system spans:
- **A custom programming language** with an LALR grammar, parser, AST, validator, and JSON emitter
- **A distributed runtime** with a 20+ state machine, dependency-driven evaluation, and MongoDB persistence
- **A microservice architecture** with Docker-orchestrated runners, agents, HDFS storage, and a web dashboard
- **Agent SDKs** in five languages (Python, Scala, Go, TypeScript, Java)
- **A real-world example application** — an OpenStreetMap geocoder with 42 AFL files, ~80 handler modules, and interactive map visualizations

The project grew from a single grammar file to **3,211 passing tests**, **100+ changelog versions**, and workflows that process all 50 US states in parallel — generating interactive maps of bicycle routes, national parks, city populations, transit networks, and administrative boundaries.

---

## Part 1: The Language

### AFL — Agent Flow Language

AFL is a typed, declarative DSL designed for expressing distributed computations. Here is the complete grammar — 141 lines that define the entire language:

```lark
// AFL v1 Grammar (Lark LALR)

start: _NL* (namespace_block | top_level_decl)*

namespace_block: DOC_COMMENT? "namespace" QNAME "{" _NL* namespace_body "}" _NL*
namespace_body: (uses_decl | facet_decl | event_facet_decl
               | workflow_decl | implicit_decl | schema_decl)*

uses_decl: ("uses" | "use") QNAME _stmt_end

facet_decl:       DOC_COMMENT? "facet" facet_sig facet_def_tail? _stmt_end
event_facet_decl: DOC_COMMENT? "event" "facet" facet_sig facet_def_tail? _stmt_end
workflow_decl:    DOC_COMMENT? "workflow" facet_sig facet_def_tail? _stmt_end

facet_sig: IDENT "(" [params] ")" [return_clause] mixin_sig*
return_clause: "=>" "(" [params] ")"
params: _NL* param (_NL* "," _NL* param)* _NL*
param: IDENT ":" type ("=" expr)?
type: array_type | TYPE_BUILTIN | QNAME
array_type: "[" type "]"
mixin_sig: "with" QNAME "(" [named_args] ")"

facet_def_tail: "andThen" foreach_clause? block more_andthen_block*
              | _NL* "prompt" prompt_block
              | _NL* "script" script_block

foreach_clause: "foreach" IDENT "in" reference
block: "{" _NL* block_body "}" _NL*
block_body: (step_stmt _NL*)* (yield_stmt _NL*)*
step_stmt: IDENT "=" call_expr step_body?
yield_stmt: "yield" call_expr
call_expr: QNAME "(" [named_args] ")" mixin_call*

expr: concat_expr
concat_expr: additive_expr ("++" _NL* additive_expr)*
additive_expr: multiplicative_expr (ADD_OP _NL* multiplicative_expr)*
multiplicative_expr: unary_expr (MUL_OP _NL* unary_expr)*
```

The grammar supports **namespaces**, **typed parameters**, **schemas**, **event facets** (external agent dispatch), **andThen blocks** (multi-step composition), **foreach iteration**, **mixin composition**, **arithmetic/string expressions**, **collection literals**, and **prompt/script blocks** for LLM and inline code execution.

### Core Language Concepts

**Facets** are the fundamental building blocks — typed function signatures with parameters and returns:

```afl
facet Retry(maxAttempts: Int = 3, backoffMs: Int = 1000)
```

**Event facets** mark computation boundaries where the runtime pauses and dispatches work to external agents:

```afl
event facet RenderMap(geojson_path: String, title: String = "Map",
    format: String = "html", color: String = "#3388ff") => (result: MapResult)
```

**Workflows** are entry points that compose steps using `andThen` blocks:

```afl
workflow FindVolcanoes(state: String, min_elevation_ft: Long)
    => (map: MapResult, text: FormatResult) andThen {
    data = LoadVolcanoData(region = $.state)
    filtered = FilterByOSMTag(input_path = data.cache.path,
        tag_key = "natural", tag_value = "volcano")
    elevated = FilterByMaxElevation(input_path = filtered.result.output_path,
        min_max_elevation_ft = $.min_elevation_ft)
    map = RenderMap(geojson_path = elevated.result.output_path,
        title = $.state ++ " Volcanoes")
    yield FindVolcanoes(map = map.result, text = fmt.result)
}
```

**Schemas** define typed structures that flow between steps:

```afl
namespace osm.geo.Visualization {
    schema MapResult {
        output_path: String,
        format: String,
        feature_count: Long,
        bounds: String,
        title: String,
        extraction_date: String
    }
}
```

**Mixin composition** attaches cross-cutting concerns at call sites:

```afl
invoked = aws.lambda.InvokeFunction(function_name = $.name)
    with Retry(maxAttempts = 3, backoffMs = 2000)
    with Timeout(seconds = 60)
```

---

## Part 2: The Compiler

The AFL compiler is a four-stage pipeline:

```
AFL Source → Lark LALR Parser → AST (dataclasses) → Validator → JSON Emitter
```

**Parsing**: The Lark library generates an LALR parser from the grammar. Parse errors include line and column numbers. The grammar is carefully designed to avoid LALR conflicts — no newlines are allowed between signature tokens like `)`, `=>`, `with`, and `andThen`.

**AST**: All nodes are Python dataclasses — `FacetDecl`, `WorkflowDecl`, `EventFacetDecl`, `SchemaDecl`, `AndThenBlock`, `StepStmt`, `YieldStmt`, `CallExpr`, and others. Source provenance (file path, line number) is tracked for debugging.

**Validation**: Semantic checks catch errors at compile time — duplicate names, unresolvable type references, invalid string+int operations, bool+arithmetic mismatches, and schema-must-be-in-namespace violations.

**Emission**: The emitter produces a unified `declarations` list in JSON. A single AFL file like the OSM geocoder compiles to a JSON document that the runtime loads and executes.

```bash
afl input.afl -o output.json       # compile to JSON
afl input.afl --check              # syntax check only
```

The compiler supports multi-file compilation with `--library` flags, enabling large projects to be split across many AFL files while sharing type definitions and namespace references.

---

## Part 3: The Runtime

### State Machine

The runtime evaluator drives each step through a 20+ state machine:

```
Created → FacetInitializationBegin → FacetInitializationEnd →
  Scripts → MixinBlocks → StatementBlocks → BlockExecution →
  EventTransmit → [agent processes] → Complete | Error
```

**Regular facets** (no `event` prefix) pass through evaluation internally. **Event facets** block at `EventTransmit`, where a task is created in MongoDB for external agents to claim and process.

### Iterative Evaluation

The evaluator runs in a fixed-point loop: each iteration creates new steps, advances state transitions, and commits changes atomically. It pauses when all remaining steps are blocked on external events, then resumes when agents complete tasks.

For large workflows (500K+ steps), the `resume_step()` optimization walks only the ancestor chain of a completed step — O(depth) instead of O(total_steps) — making per-task resume instantaneous.

### Dependency-Driven Step Creation

Steps within an `andThen` block execute concurrently by default. The dependency graph tracks which steps reference other steps' outputs:

```afl
cache = osm.geo.Operations.Cache(region = $.region)
// These all depend on `cache` and execute in parallel once it completes:
bicycleRoutes = examples.composed.VisualizeBicycleRoutesFromCache(cache = cache.cache)
parks = examples.composed.AnalyzeParksFromCache(cache = cache.cache)
largeCities = examples.composed.LargeCitiesMapFromCache(cache = cache.cache)
```

Yields are deferred until all non-yield statements in their block are terminal — ensuring complete results before aggregation.

### Concurrency Control

Three layers prevent duplicate step creation under concurrent evaluation:
1. **Application-level check**: `block_step_exists()` query before creation
2. **MongoDB unique index**: `(statement_id, block_id, container_id)` compound index
3. **DuplicateKeyError catch**: graceful handling of race conditions

Verified with 3 concurrent evaluators producing zero duplicate steps.

---

## Part 4: The Microservice Architecture

### Docker Stack

```
┌──────────────────────────────────────────────────────────────┐
│                        Dashboard (:8080)                      │
│              FastAPI + HTMX + Jinja2 templates                │
├──────────────┬──────────────┬──────────────┬─────────────────┤
│  Runner (×3) │ Runner (×3)  │ Runner (×3)  │  MCP Server     │
│  polls tasks │ polls tasks  │ polls tasks  │  (Claude/LLM)   │
├──────────────┴──────────────┴──────────────┴─────────────────┤
│                        MongoDB                                │
│         workflows, steps, tasks, runners, locks               │
├──────────────┬──────────────┬────────────────────────────────┤
│ OSM Agent ×3 │ AddOne Agent │       HDFS (NameNode +         │
│ (pyosmium,   │ (sample)     │        DataNode)               │
│  folium,     │              │                                │
│  GraphHopper)│              │                                │
└──────────────┴──────────────┴────────────────────────────────┘
```

**Runners** poll MongoDB for pending tasks, acquire distributed locks, and dispatch event facets to the appropriate agent handlers. Multiple runners operate concurrently with no coordination beyond the database.

**Agents** register handlers for specific event facets. The recommended approach is **RegistryRunner** — handlers are registered in the database and auto-loaded at startup, requiring no custom service code:

```python
register_handler(
    facet_name="osm.geo.Visualization.RenderMap",
    module_uri="file:///handlers/visualization/map_renderer.py",
    entrypoint="handle"
)
```

**HDFS** provides distributed storage for large geographic data files. The `localize()` function transparently downloads HDFS files to a local cache for processing by tools like pyosmium that require filesystem access.

**The Dashboard** provides real-time workflow monitoring with namespace-grouped runners, state-filtered step views, handler activity tracking, and an output file browser for viewing generated maps and data.

### Scaling

The architecture scales horizontally:

| Parameter | Default | Production |
|-----------|---------|------------|
| Runners | 1 | 3+ |
| OSM Agents | 0 | 3+ |
| Max Concurrent Tasks | 2 | 10+ |
| Poll Interval | 1000ms | configurable |

Environment configuration via `.env`:
```bash
AFL_RUNNERS=3
AFL_OSM_AGENTS=3
AFL_MAX_CONCURRENT=2
AFL_HDFS=true
AFL_GEOFABRIK_MIRROR=/Volumes/afl_data/osm
```

One command bootstraps everything:
```bash
scripts/easy.sh    # teardown → rebuild → setup → seed
```

---

## Part 5: The OSM Geocoder — A Real-World Application

The largest example application is a complete OpenStreetMap analysis platform:

| Metric | Count |
|--------|-------|
| AFL workflow files | 42 |
| Handler Python modules | ~80 |
| Handler categories | 16 |
| Event facets | ~500+ |
| Seeded workflows | 75 |
| Passing tests | 3,211 |

### Handler Categories

| Category | Purpose | Key Capabilities |
|----------|---------|-----------------|
| **cache** | Region caching | ~250 cache facets across 11 namespaces |
| **routes** | Transportation | Bicycle, hiking, train, bus, GTFS, elevation |
| **boundaries** | Administrative | Country/state/county/city boundary extraction |
| **parks** | Protected areas | National parks, state parks, area statistics |
| **population** | Demographics | City extraction, population filtering, statistics |
| **visualization** | Map rendering | Interactive HTML (Folium/Leaflet), static PNG |
| **roads** | Road networks | Extraction, classification, zoom-level builder |
| **filters** | Data refinement | Radius, OSM tags, OSMOSE validation |
| **amenities** | Points of interest | Restaurants, shops, healthcare, air quality |
| **buildings** | Building footprints | Extraction from PBF files |
| **downloads** | Data acquisition | PBF download, tile processing, PostGIS import |
| **graphhopper** | Routing | ~200 routing graph facets |
| **voting** | Electoral | US Census TIGER voting district data |
| **composed_workflows** | Patterns | 15 multi-stage composition patterns |

### Composition Patterns

The composed workflows demonstrate real-world pipeline patterns in AFL:

**Pattern 1 — Cache, Extract, Visualize** (3 stages):
```afl
/** Caches a region, extracts bicycle routes, and renders them on a map. */
workflow VisualizeBicycleRoutes(region: String = "Liechtenstein")
    => (map_path: String, route_count: Long) andThen {
    cache = osm.geo.Operations.Cache(region = $.region)
    routes = osm.geo.Routes.BicycleRoutes(cache = cache.cache,
        include_infrastructure = true)
    map = osm.geo.Visualization.RenderMap(
        geojson_path = routes.result.output_path,
        title = "Bicycle Routes", color = "#27ae60")
    yield VisualizeBicycleRoutes(
        map_path = map.result.output_path,
        route_count = routes.result.feature_count)
}
```

**Pattern 3 — Cache, Extract, Filter, Visualize** (4 stages):
```afl
/** Extracts cities, filters by population, renders qualifying cities on a map. */
facet LargeCitiesMapFromCache(cache: OSMCache, min_pop: Long = 10000)
    => (map_path: String, city_count: Long) andThen {
    cities = osm.geo.Population.Cities(cache = $.cache, min_population = 0)
    large = osm.geo.Population.FilterByPopulation(
        input_path = cities.result.output_path,
        min_population = $.min_pop, place_type = "city", operator = "gte")
    map = osm.geo.Visualization.RenderMap(
        geojson_path = large.result.output_path,
        title = "Large Cities", color = "#e74c3c")
    yield LargeCitiesMapFromCache(
        map_path = map.result.output_path,
        city_count = large.result.feature_count)
}
```

**Pattern 4 — Parallel Extraction with Aggregated Statistics**:
```afl
/** Extracts bicycle, hiking, train, and bus routes in parallel
    and aggregates length statistics. */
facet TransportOverviewFromCache(cache: OSMCache)
    => (bicycle_km: Double, hiking_km: Double,
        train_km: Double, bus_routes: Long) andThen {
    bicycle = osm.geo.Routes.BicycleRoutes(cache = $.cache)
    hiking  = osm.geo.Routes.HikingTrails(cache = $.cache)
    train   = osm.geo.Routes.TrainRoutes(cache = $.cache)
    bus     = osm.geo.Routes.BusRoutes(cache = $.cache)
    bicycle_stats = osm.geo.Routes.RouteStatistics(
        input_path = bicycle.result.output_path)
    hiking_stats  = osm.geo.Routes.RouteStatistics(
        input_path = hiking.result.output_path)
    train_stats   = osm.geo.Routes.RouteStatistics(
        input_path = train.result.output_path)
    bus_stats     = osm.geo.Routes.RouteStatistics(
        input_path = bus.result.output_path)
    yield TransportOverviewFromCache(
        bicycle_km = bicycle_stats.stats.total_length_km,
        hiking_km  = hiking_stats.stats.total_length_km,
        train_km   = train_stats.stats.total_length_km,
        bus_routes  = bus_stats.stats.route_count)
}
```

### The AnalyzeRegion Workflow

The crown jewel is `AnalyzeRegion` — a workflow that runs **10 composed analysis pipelines** for any geographic region using a single shared cache:

```afl
/** Runs 10 composed analysis workflows for a single region
    using a shared cache. */
workflow AnalyzeRegion(region: String = "Liechtenstein")
    => (completed_region: String) andThen {
    cache          = osm.geo.Operations.Cache(region = $.region)
    bicycleRoutes  = examples.composed.VisualizeBicycleRoutesFromCache(cache = cache.cache)
    parks          = examples.composed.AnalyzeParksFromCache(cache = cache.cache)
    largeCities    = examples.composed.LargeCitiesMapFromCache(cache = cache.cache, min_pop = 10000)
    transport      = examples.composed.TransportOverviewFromCache(cache = cache.cache)
    nationalParks  = examples.composed.NationalParksAnalysisFromCache(cache = cache.cache)
    cityAnalysis   = examples.composed.CityAnalysisFromCache(cache = cache.cache, min_population = 100000)
    transportMap   = examples.composed.TransportMapFromCache(cache = cache.cache)
    boundaries     = examples.composed.StateBoundariesWithStatsFromCache(cache = cache.cache)
    cities         = examples.composed.DiscoverCitiesAndTownsFromCache(cache = cache.cache)
    regional       = examples.composed.RegionalAnalysisFromCache(cache = cache.cache)
    yield AnalyzeRegion(completed_region = $.region)
}
```

And the scaling variants invoke `AnalyzeRegion` for multiple states in parallel:

```afl
/** Runs AnalyzeRegion for 2 US states. */
workflow AnalyzeStates_02() => (states_completed: Long) andThen {
    alabama = AnalyzeRegion(region = "Alabama")
    alaska  = AnalyzeRegion(region = "Alaska")
    yield AnalyzeStates_02(states_completed = 2)
}
```

The full `AnalyzeAllStates` workflow invokes AnalyzeRegion for all **51 regions** (50 states + DC) concurrently — generating hundreds of thousands of steps processed by distributed agents.

---

## Part 6: Results — Maps and Data

Running `AnalyzeStates_02` against Alabama and Alaska produced **147 steps** across 3 OSM agents in approximately **18.5 minutes**, generating interactive Leaflet/OpenStreetMap maps and detailed extraction statistics.

### Generated Map Visualizations

All maps are interactive HTML files using Folium (Leaflet.js) with OpenStreetMap tiles, zoom/pan controls, feature tooltips, and automatic bounds fitting.

| Map | State | Size | Features |
|-----|-------|------|----------|
| `alabama-latest.osm_bicycle_routes.html` | Alabama | 5.5 MB | 6,107 bicycle routes (3,205 km) |
| `alabama-latest.osm_all_parks.html` | Alabama | 2.8 MB | 1,677 parks (5,678 km²) |
| `alabama-latest.osm_all_parks_national.html` | Alabama | 23 KB | 7 national parks |
| `alabama-latest.osm_admin4.html` | Alabama | 161 KB | 1 state boundary |
| `alabama-latest.osm_city_pop_100000.html` | Alabama | 16 KB | 4 cities (pop > 100K) |
| `alabama-latest.osm_city_pop_pop_10000.html` | Alabama | 20 KB | 11 cities (pop > 10K) |
| `alaska-latest.osm_bicycle_routes.html` | Alaska | 5.0 MB | 6,728 bicycle routes (4,327 km) |
| `alaska-latest.osm_all_parks_national.html` | Alaska | 2.3 MB | 17 national parks |
| `alaska-latest.osm_admin4.html` | Alaska | 7.7 KB | Borough boundaries |
| `alaska-latest.osm_city_pop_100000.html` | Alaska | 13 KB | 1 city (Anchorage, pop 291K) |
| `alaska-latest.osm_city_pop_pop_10000.html` | Alaska | 16 KB | 13 cities |

These maps are accessible through the dashboard file browser at `http://localhost:8080/output` or directly at:
```
/Volumes/afl_data/output/maps/
```

### Extraction Statistics

**Alabama:**
- 6,107 bicycle routes totaling 3,205 km
- 87,701 hiking trails totaling 35,622 km
- 7,571 train routes totaling 8,460 km
- 130 bus routes
- 1,677 parks covering 5,678 km² (7 national, 9 state)
- 1 state boundary polygon
- 11 cities, 4 with population > 100,000 (total 803,383)

**Alaska:**
- 6,728 bicycle routes totaling 4,327 km
- 37,390 hiking trails totaling 40,758 km
- 1,376 train routes totaling 1,229 km
- 62 bus routes
- 790 parks covering 1,025,221 km² (17 national, 42 state)
- 13 cities, 1 with population > 100,000 (Anchorage: 291,247)

### Viewing the Maps

The generated maps are interactive Leaflet.js visualizations. To view them:

1. **Via the dashboard**: Navigate to `http://localhost:8080/output` and click into the `maps/` directory. Click any `.html` file to view inline.

2. **Directly in a browser**: Open any file from `/Volumes/afl_data/output/maps/` — they are self-contained HTML with all data embedded.

3. **Via the API**: `GET http://localhost:8080/output/view?path=maps/alaska-latest.osm_bicycle_routes.html`

Each map features:
- OpenStreetMap tile background
- Color-coded GeoJSON feature overlays
- Hover tooltips showing feature properties
- Zoom and pan controls
- Automatic bounds fitting to data extent

---

## Part 7: Project Evolution

### Timeline of Major Milestones

| Version | Milestone | Tests |
|---------|-----------|-------|
| **v0.1.0** | Lark LALR parser, AST dataclasses, JSON emitter | — |
| **v0.2.0** | 20+ step state machine, in-memory and MongoDB persistence | — |
| **v0.3.0** | FastAPI dashboard, distributed RunnerService with locking | 600+ |
| **v0.4.0** | Task queue architecture, server registration with heartbeat | 600+ |
| **v0.5.0** | MCP server for LLM integration, AgentPoller library | — |
| **v0.6.0** | Multi-language agent SDKs (Python, Scala, Go, TypeScript, Java) | — |
| **v0.7.0** | Arithmetic operators, string concat, arrays, maps, indexing | — |
| **v0.8.0** | Multiple andThen blocks, foreach execution, OSM geocoder (22 AFL files) | 960+ |
| **v0.10.0** | Schema declarations, event facet blocking semantics | 1,400+ |
| **v0.12.0** | Full OSM pipeline, RegistryRunner auto-loading | 1,555 |
| **v0.12.30** | resume_step() O(depth) optimization for 500K+ steps | 2,183 |
| **v0.12.52** | Declarations-only JSON format, compiled_ast persistence | 2,308 |
| **v0.12.65** | OSM reorganized into 16 category subdirectories (114 handlers) | 2,308 |
| **v0.12.72** | Three-layer concurrent step dedup (0 duplicates with 3 evaluators) | 2,430 |
| **v0.12.79** | Dashboard v2 redesign with HTMX, state tabs, manual refresh | 2,489 |
| **v0.12.85** | Lite agent HAS_OSMIUM guards (prevents silent empty results) | 2,491 |
| **v0.12.87** | Output browser, host-mounted maps, clickable file links | 2,522 |
| **v0.15.0** | Script blocks (pre-processing + andThen), grammar newline fix | 2,845 |
| **v0.16.0** | ML hyperparameter sweep example, statement-level andThen | 2,904 |
| **v0.17.0** | Research agent — first LLM integration, prompt blocks | 2,942 |
| **v0.18.0** | Multi-agent debate — first multi-agent interaction | 2,981 |
| **v0.19.0** | Multi-round debate + tool-use agent patterns | 3,057 |
| **v0.20.0** | Data quality pipeline — schema instantiation, array types | 3,095 |
| **v0.21.0** | Sensor monitoring — unary negation, null literals, mixin alias | 3,141 |
| **v0.22.0** | Site-selection debate — spatial + research + debate combined | 3,180 |
| **v0.23.0** | Dashboard UI redesign — sidebar nav, command palette, search | 3,211 |
| **v0.24.0** | SDK step-log emission, PyPI packaging, cleanups | **3,211** |

### The Numbers

| Metric | Value |
|--------|-------|
| Grammar size | 142 lines |
| Total tests | 3,211 passed + 84 skipped (3,295 collected) |
| Changelog entries | 100+ versions (v0.1.0 – v0.24.0) |
| AFL files (OSM example) | 42 |
| Handler modules (OSM) | ~80 |
| Event facets (OSM) | ~500+ |
| Seeded workflows (all examples) | 156 across 7 flows |
| Agent SDKs | 5 languages |
| Docker services | 8 (MongoDB, dashboard, runner ×3, OSM agent ×3, HDFS) |
| Largest workflow tested | AnalyzeAllStates — 51 regions, 500K+ steps |

---

## Part 8: Architecture Lessons

### What Worked

**Declarative composition scales.** AFL's `andThen` blocks with automatic dependency detection make it natural to express parallel pipelines. The `AnalyzeRegion` workflow — 10 parallel analysis pipelines sharing a cache — reads almost like a table of contents.

**Event facets as boundaries.** The distinction between regular facets (evaluated internally) and event facets (dispatched to agents) creates a clean separation between workflow logic and execution infrastructure. Adding a new handler requires only a Python module and a database registration — no code changes to the runtime.

**Three-layer dedup.** Distributed step creation is inherently racy. The app-level check catches 99% of duplicates, the MongoDB unique index catches the rest, and the DuplicateKeyError handler ensures graceful recovery. Zero duplicates verified with 3 concurrent evaluators.

**Dirty-block tracking.** When a workflow has 500K steps, re-evaluating all "Continue" blocks after each task completion is prohibitive. Tracking which blocks are actually affected by a state change reduced resume time from O(total_steps) to O(affected_blocks).

### What Was Hard

**HDFS compatibility.** Python's `Path()` collapses `//` in `hdfs://` URIs. Every handler that touched file paths needed auditing — 7 files, 19 individual fixes across map renderers, road extractors, amenity extractors, and route handlers.

**Lite agent races.** When both the full agent (with pyosmium) and the lite agent (without it) registered identical handlers, the lite agent would win extraction tasks and silently return empty results. The fix — `HAS_OSMIUM` guards on 9 registration functions — was simple, but the failure mode was insidious.

**Large file downloads.** WebHDFS connections break on files over 100MB (e.g., Alaska parks at 340MB). The retry logic helps but doesn't solve fundamental connection timeout issues. The workaround — pre-caching large files via `docker cp` — is manual but reliable.

---

## Appendix: Running It Yourself

### Prerequisites
- Docker and Docker Compose
- Python 3.11+ with virtualenv
- ~10 GB disk for OSM data and HDFS

### Quick Start

```bash
# Configure
cp .env.example .env
# Edit .env: set AFL_GEOFABRIK_MIRROR, scaling, data dirs

# Full pipeline: teardown → rebuild → setup → seed
scripts/easy.sh

# Run 2-state analysis
examples/osm-geocoder/tests/real/scripts/run_osm_analyze_states_02.sh

# Watch progress
open http://localhost:8080

# View generated maps
open /Volumes/afl_data/output/maps/alaska-latest.osm_bicycle_routes.html
```

### Project Structure

```
agentflow/
├── afl/                          # Compiler package
│   ├── grammar/afl.lark          # 141-line LALR grammar
│   ├── parser.py                 # Lark parser
│   ├── transformer.py            # AST builder
│   ├── emitter.py                # JSON emitter
│   ├── validator.py              # Semantic validation
│   ├── runtime/                  # Execution engine
│   │   ├── evaluator.py          # State machine + iteration
│   │   ├── persistence.py        # MongoDB/memory store
│   │   └── runner/               # Distributed runner
│   ├── dashboard/                # FastAPI web UI
│   └── mcp/                      # LLM integration server
├── agents/                       # SDKs (Python, Scala, Go, TS, Java)
├── examples/
│   ├── osm-geocoder/             # 70 AFL files, 114 handlers
│   │   ├── afl/                  # Core workflow definitions
│   │   ├── handlers/             # 16 category subdirectories
│   │   └── tests/real/           # Scaling variants (2–45 states)
│   ├── aws-lambda/               # Lambda deployment workflows
│   ├── volcano-query/            # Cross-namespace composition
│   ├── continental-lz/           # Landing zone patterns
│   ├── genomics/                 # Bioinformatics pipelines
│   └── jenkins/                  # CI/CD workflows
├── tests/                        # 2,522 tests
├── spec/                         # 11 specification documents
├── docker/                       # Dockerfiles for all services
└── scripts/                      # Convenience scripts
```

---

*AgentFlow — v0.24.0 — February 2026*

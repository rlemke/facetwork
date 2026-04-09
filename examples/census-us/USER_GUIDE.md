# Census-US — User Guide

> See also: [Examples Guide](../doc/GUIDE.md) | [README](../README.md)

## When to Use This Example

Use this as your starting point if you are:
- Building **ETL pipelines** that download, extract, join, and ingest tabular + geospatial data
- Working with **US Census Bureau** ACS demographics and TIGER/Line shapefiles
- Designing **wide parallel andThen blocks** with many concurrent extraction steps
- Using **array literals** built from upstream step results
- Composing **workflow-calling-workflow** patterns for multi-state analysis

## What You'll Learn

1. How to decompose a pipeline across multiple FFL files and namespaces
2. How schemas defined in one namespace are reused across all others via `use` imports
3. How 12+ extraction steps run concurrently in a single andThen block
4. How array literals collect step results (`[income.result.output_path, ...]`)
5. How workflows call other workflows as steps (`AnalyzeStates_03` calls `AnalyzeState` 3x)
6. How factory-generated handlers eliminate boilerplate for uniform facet signatures
7. How `OutputStore` provides idempotent MongoDB ingestion with upsert

## Step-by-Step Walkthrough

### 1. The Problem

You want to analyze US county demographics for one or more states. The pipeline must download ACS survey data from the Census Bureau REST API, download TIGER/Line geographic shapefiles, extract 12 demographic topic tables, join them to county geometries by GEOID, compute derived metrics (population density, poverty rate, unemployment, etc.), and optionally persist everything to MongoDB for dashboard visualization.

### 2. Namespace Decomposition

The pipeline is organized across 6 namespaces in separate FFL files:

| Namespace | FFL File | Purpose |
|-----------|----------|---------|
| `census.types` | `census_operations.ffl` | 4 shared schemas: CensusFile, ACSResult, TIGERResult, CensusSummary |
| `census.Operations` | `census_operations.ffl` | 3 download facets |
| `census.ACS` | `census_acs.ffl` | 12 ACS extraction facets |
| `census.TIGER` | `census_tiger.ffl` | 4 TIGER extraction facets |
| `census.Summary` | `census_summary.ffl` | JoinGeo + SummarizeState |
| `census.Ingestion` | `census_ingestion.ffl` | 15 database ingestion facets |
| `census.workflows` | `census.ffl` | 3 workflows |

Every namespace imports `census.types` with `use census.types` so all schemas are available by short name.

### 3. The Schema Library Pattern

Shared schemas are defined once in `census.types`:

```afl
namespace census.types {
    schema CensusFile { url: String, path: String, date: String, size: Long, wasInCache: Boolean }
    schema ACSResult { table_id: String, output_path: String, record_count: Long, ... }
    schema TIGERResult { output_path: String, feature_count: Long, ... }
    schema CensusSummary { state_fips: String, state_name: String, output_path: String, ... }
}
```

Other namespaces reference these types in their parameter and return signatures without redefining them.

### 4. Wide Parallel Extraction

The `AnalyzeState` workflow downloads three files, then runs 12 ACS extractions concurrently:

```afl
workflow AnalyzeState(state_fips: String = "01", state_name: String = "Alabama") => (summary: CensusSummary) andThen {
    acs = DownloadACS(state_fips = $.state_fips)
    acs_detail = DownloadACSDetailed(state_fips = $.state_fips)
    tiger = DownloadTIGER(state_fips = $.state_fips, geo_level = "COUNTY")

    // 12 parallel ACS extractions (all depend on acs.file, run concurrently)
    pop = ExtractPopulation(file = acs.file, state_fips = $.state_fips)
    income = ExtractIncome(file = acs.file, state_fips = $.state_fips)
    housing = ExtractHousing(file = acs.file, state_fips = $.state_fips)
    education = ExtractEducation(file = acs.file, state_fips = $.state_fips)
    // ... 8 more extraction steps ...

    counties = ExtractCounties(file = tiger.file, state_fips = $.state_fips)
    joined = JoinGeo(acs_path = pop.result.output_path, tiger_path = counties.result.output_path,
        extra_acs_paths = [income.result.output_path, housing.result.output_path, ...])
    summary = SummarizeState(population = pop.result, income = income.result, ...)
    yield AnalyzeState(summary = summary.result)
}
```

The FFL runtime detects that all 12 extraction steps share the same dependency (`acs.file`) and schedules them concurrently. No explicit parallelism annotation is needed.

### 5. Array Literals from Step Results

The `JoinGeo` step collects output paths from multiple upstream steps into an array:

```afl
joined = JoinGeo(
    acs_path = pop.result.output_path,
    tiger_path = counties.result.output_path,
    extra_acs_paths = [income.result.output_path, housing.result.output_path,
                       education.result.output_path, commuting.result.output_path,
                       race.result.output_path, poverty.result.output_path,
                       employment.result.output_path, tenure.result.output_path,
                       vehicles.result.output_path]
)
```

This builds a 9-element array of step references that the runtime resolves at execution time.

### 6. Workflow-Calling-Workflow

`AnalyzeStates_03` calls `AnalyzeState` three times as independent steps:

```afl
workflow AnalyzeStates_03() => (states_completed: Long) andThen {
    alabama = AnalyzeState(state_fips = "01", state_name = "Alabama")
    alaska = AnalyzeState(state_fips = "02", state_name = "Alaska")
    arizona = AnalyzeState(state_fips = "04", state_name = "Arizona")
    yield AnalyzeStates_03(states_completed = 3)
}
```

Each state pipeline runs as an independent sub-workflow with its own 27+ steps, all executing in parallel.

### 7. Running

```bash
# From repo root
source .venv/bin/activate
pip install -e ".[dev]"
pip install -r examples/census-us/requirements.txt  # requests, fiona, shapely, pyshp

# Compile check
afl examples/census-us/ffl/census.ffl --check

# Run tests (no network required)
pytest examples/census-us/tests/ -v
```

## Key Concepts

### Derived Metrics

The `JoinGeo` handler computes 13+ derived metrics from raw ACS columns:

| Metric | Source |
|--------|--------|
| `population_density_km2` | TIGER ALAND + ACS B01003 |
| `pct_below_poverty` | B17001_002E / B17001_001E |
| `unemployment_rate` | B23025_005E / B23025_003E |
| `pct_bachelors_plus` | sum(B15003_022-025E) / B15003_001E |
| `pct_owner_occupied` | B25003_002E / B25003_001E |
| `vehicles_per_household` | weighted sum of B25044 |

Zero-denominator cases are omitted rather than producing divide-by-zero errors.

### Factory-Generated Handlers

The 12 ACS handlers are generated from a single factory function:

```python
def _make_acs_handler(facet_name, table_id):
    def handler(params):
        result = extract_acs(params, table_id)
        return {"result": result}
    return handler

_DISPATCH = {f"census.ACS.Extract{name}": _make_acs_handler(name, tid)
             for name, tid in ACS_TABLES.items()}
```

This eliminates 12 nearly-identical handler functions.

### OutputStore Ingestion

The `AnalyzeStateWithDB` variant adds 15 ingestion steps that upsert into MongoDB:

```python
store = OutputStore(db)
store.ingest_geojson("census.counties.01", geojson_path, feature_key="GEOID")
```

The compound index `(dataset_key, feature_key)` ensures re-running replaces records rather than creating duplicates.

## Handler Design

All 36 handlers use deterministic stubs for testing:
- **Download handlers**: return cached file metadata without network calls
- **ACS extractors**: parse CSV from test fixtures, filter by state FIPS
- **TIGER extractors**: convert shapefiles to GeoJSON (fiona preferred, pyshp fallback)
- **Summary builders**: compute derived metrics from joined data
- **Ingestion handlers**: bulk upsert to MongoDB with compound unique index

## Adapting for Your Use Case

### Add more ACS tables

Define a new event facet in `census_acs.ffl` and add the table ID to `ACS_TABLES`:

```python
ACS_TABLES["Insurance"] = "B27001"  # Health Insurance Coverage
```

The factory automatically generates the handler.

### Change geographic level

Replace `"COUNTY"` with `"TRACT"` or `"BLOCK_GROUP"` in the TIGER download:

```afl
tiger = DownloadTIGER(state_fips = $.state_fips, geo_level = "TRACT")
```

### Add dashboard visualization

The Census maps dashboard module reads from MongoDB collections populated by the `*ToDB` handlers. Enable it with `AFL_MONGODB_URL` and run the dashboard to see interactive choropleth maps.

## Next Steps

- **[site-selection](../site-selection/USER_GUIDE.md)** — combines Census demographics with OSM amenity data for spatial scoring
- **[osm-geocoder](../osm-geocoder/USER_GUIDE.md)** — full production-scale agent with 580+ handlers
- **[continental-lz](../continental-lz/USER_GUIDE.md)** — Docker-orchestrated multi-region pipeline

# Continental LZ Pipeline — User Guide

> See also: [Examples Guide](../doc/GUIDE.md) | [README](README.md)

## When to Use This Example

Use this as your starting point if you are:
- Deploying a **multi-service pipeline** with Docker Compose
- Running **long-duration workflows** (hours) with MongoDB persistence
- Orchestrating **regional parallel processing** across multiple geographies
- Building on the **OSM geocoder infrastructure** for large-scale data processing

## What You'll Learn

1. How to run Facetwork with a full Docker stack (MongoDB, dashboard, runner, agent)
2. How to compose workflows that span multiple continental regions
3. How to structure a Docker deployment with custom agent images
4. How to use the seed pattern to load compiled workflows into MongoDB
5. How to monitor long-running workflows via the dashboard

## Overview

This example orchestrates Low-Zoom (LZ) road infrastructure building and GTFS transit analysis across 15+ regions:

- **US, Canada, and 12 European countries** for road infrastructure
- **11 transit agencies** (Amtrak, MBTA, CTA, MTA, TransLink, TTC, OC Transpo, Deutsche Bahn, SNCF, Renfe, Trenitalia)
- **72+ GB of data** (PBF downloads + GraphHopper routing graphs)
- **12-30 hours** for a full continental run

## Architecture

```
docker-compose.yml
  mongodb (27019)      — isolated database (afl_continental_lz)
  dashboard (8081)     — web monitoring UI
  runner               — workflow evaluator
  agent                — RegistryRunner with cache/GH/zoom/GTFS handlers
  seed (profile: seed) — compiles AFL + seeds MongoDB
```

The agent image includes Java (for GraphHopper), Python geospatial stack, and all OSM handler modules (symlinked from `examples/osm-geocoder/handlers/`).

## Step-by-Step Walkthrough

### 1. Start the Stack

```bash
cd examples/continental-lz
docker compose up -d
```

This starts MongoDB, the dashboard, the runner, and the agent.

### 2. Seed Workflows

```bash
docker compose --profile seed run --rm seed
```

The seed service compiles all AFL files and loads the compiled workflow definitions into MongoDB.

### 3. Monitor via Dashboard

Open http://localhost:8081 to see workflow status, runner progress, and step execution.

### 4. Smoke Test with a Small Region

Before running the full continental pipeline, test with Belgium (~0.4 GB):

```bash
cd examples/continental-lz
PYTHONPATH=../.. python scripts/run_region.py --region Belgium --output-dir /tmp/lz-belgium
```

### 5. Run the Full Pipeline

From the dashboard, start the `continental.FullContinentalPipeline` workflow, or trigger it via the MCP server or API.

## Workflows

| Workflow | Description | Est. Time |
|----------|-------------|-----------|
| `BuildUSLowZoom` | US road infrastructure | 4-8 hrs |
| `BuildCanadaLowZoom` | Canada road infrastructure | 1-3 hrs |
| `BuildEuropeLowZoom` | 12 European countries (parallel) | 2-4 hrs |
| `BuildContinentalLZ` | All three regions in parallel | 8-12 hrs |
| `AnalyzeUSTransit` | 4 US transit agencies | 1-2 hrs |
| `AnalyzeCanadaTransit` | 3 Canadian transit agencies | 30-60 min |
| `AnalyzeEuropeTransit` | 4 European transit agencies | 1-2 hrs |
| `ContinentalTransitAnalysis` | All 11 agencies | 2-4 hrs |
| `FullContinentalPipeline` | LZ + Transit combined | 12-30 hrs |

## Key Concepts

### Self-Contained Docker Deployment

This example has its own `docker-compose.yml` (separate from the root one) with an isolated MongoDB database (`afl_continental_lz`). It demonstrates the deployment pattern for a production Facetwork installation.

### Seed Pattern

The seed service is a one-shot container that:
1. Compiles all AFL source files
2. Loads the compiled JSON into MongoDB
3. Exits

```bash
docker compose --profile seed run --rm seed
```

### Handler Reuse via Symlink

The continental pipeline reuses the OSM geocoder handlers via a symlink:

```
examples/continental-lz/handlers -> ../osm-geocoder/handlers
```

No duplicate handler code — the agent image copies the symlink target.

### Resource Management

- **Disk**: ~72 GB (28 GB PBF + 44 GB GraphHopper graphs)
- **Memory**: 16 GB recommended (GraphHopper JVM needs 4-8 GB per graph build)
- **Agent concurrency**: limited to 4 to manage memory pressure

### AFL File Organization

| File | Content |
|------|---------|
| `continental_types.afl` | Shared schemas |
| `continental_lz_workflows.afl` | LZ road infrastructure workflows |
| `continental_gtfs_workflows.afl` | GTFS transit analysis workflows |
| `continental_full.afl` | Top-level combined pipeline |

All workflows compose operations from the OSM geocoder AFL files (imported as library dependencies during seed compilation).

## Adapting for Your Use Case

### Add a new region

1. Add the region to the appropriate workflow in `continental_lz_workflows.afl`
2. Ensure the region's cache facet exists in the OSM geocoder's `osmcache.afl`
3. Re-seed the database

### Add a new transit agency

1. Add the agency's GTFS feed URL to the transit workflow
2. Ensure the GTFS handler can process the feed format
3. Re-seed the database

### Create a focused regional pipeline

Instead of running the full continental pipeline, create a workflow for just your region of interest:

```afl
namespace myregion {
    use osm.types
    workflow BuildMyRegion() => (...) andThen {
        cache = osm.cache.Europe.Netherlands()
        download = osm.ops.DownloadPBF(cache = cache.cache)
        graph = osm.GraphHopper.BuildGraph(pbf_path = download.downloadCache.path)
        yield BuildMyRegion(...)
    }
}
```

### Deploy to a cloud environment

The Docker Compose stack maps directly to cloud container services:

| Service | Cloud Equivalent |
|---------|-----------------|
| MongoDB | MongoDB Atlas / DocumentDB |
| Dashboard | ECS/EKS service with ALB |
| Runner | ECS/EKS service (scalable) |
| Agent | ECS/EKS service (scalable, GPU optional) |

## Next Steps

- **[osm-geocoder](../osm-geocoder/USER_GUIDE.md)** — understand the underlying handler infrastructure
- **[aws-lambda](../aws-lambda/USER_GUIDE.md)** — cloud service integration patterns
- **[genomics](../genomics/USER_GUIDE.md)** — foreach fan-out for batch processing

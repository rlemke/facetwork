# Continental LZ Pipeline

Self-contained example that orchestrates the Low-Zoom (LZ) road infrastructure
pipeline and GTFS transit analysis across three continental regions, all running
in Docker with MongoDB persistence.

## Regions

| Region | PBF Size | GH Graph | LZ Time (est.) |
|--------|----------|----------|-----------------|
| United States | ~9 GB | ~15 GB | 4-8 hrs |
| Canada | ~3 GB | ~5 GB | 1-3 hrs |
| Germany | ~3.8 GB | ~6 GB | 2-4 hrs |
| France | ~3.8 GB | ~6 GB | 2-4 hrs |
| UK | ~1.3 GB | ~2 GB | 1-2 hrs |
| Spain | ~1.0 GB | ~1.5 GB | 1-2 hrs |
| Italy | ~1.5 GB | ~2.5 GB | 1-2 hrs |
| Poland | ~1.2 GB | ~2 GB | 1-2 hrs |
| Netherlands | ~1.1 GB | ~1.5 GB | 30-60 min |
| Belgium | ~0.4 GB | ~0.5 GB | 15-30 min |
| Switzerland | ~0.4 GB | ~0.5 GB | 15-30 min |
| Austria | ~0.5 GB | ~0.7 GB | 20-40 min |
| Sweden | ~0.8 GB | ~1.2 GB | 30-60 min |
| Norway | ~0.6 GB | ~0.9 GB | 20-40 min |
| **Total** | **~28 GB** | **~44 GB** | **12-30 hrs** |

## GTFS Transit Agencies (11)

**US**: Amtrak, MBTA (Boston), CTA (Chicago), MTA (NYC Subway)
**Canada**: TransLink (Vancouver), TTC (Toronto), OC Transpo (Ottawa)
**Europe**: Deutsche Bahn, SNCF (France), Renfe (Spain), Trenitalia

## Quick Start

```bash
# 1. Start the stack
docker compose up -d

# 2. Seed workflows into MongoDB
docker compose --profile seed run --rm seed

# 3. View the dashboard
open http://localhost:8081
```

## Architecture

```
docker-compose.yml
  mongodb (27019)      — isolated database (afl_continental_lz)
  dashboard (8081)     — web monitoring UI
  runner               — workflow evaluator
  agent                — RegistryRunner with cache/GH/zoom/GTFS handlers
  seed (profile: seed) — compiles AFL + seeds MongoDB
```

The agent image includes:
- Java runtime (for GraphHopper routing graph builds)
- Python geospatial stack (osmium, shapely, pyproj, folium)
- GraphHopper 8.0 JAR
- Handler modules (symlinked from `examples/osm-geocoder/handlers/`)

## Workflows

| Workflow | Description |
|----------|-------------|
| `continental.lz.BuildUSLowZoom` | LZ pipeline for full US |
| `continental.lz.BuildCanadaLowZoom` | LZ pipeline for Canada |
| `continental.lz.BuildEuropeLowZoom` | LZ for 12 European countries (parallel) |
| `continental.lz.BuildContinentalLZ` | All three regions in parallel |
| `continental.transit.AnalyzeUSTransit` | 4 US transit agencies |
| `continental.transit.AnalyzeCanadaTransit` | 3 Canada transit agencies |
| `continental.transit.AnalyzeEuropeTransit` | 4 Europe transit agencies |
| `continental.transit.ContinentalTransitAnalysis` | All 11 agencies |
| `continental.FullContinentalPipeline` | LZ + Transit combined |

## Smoke Test (Single Region)

Test with a small region (Belgium ~0.4 GB) without Docker:

```bash
cd examples/continental-lz
PYTHONPATH=../.. python scripts/run_region.py --region Belgium --output-dir /tmp/lz-belgium
```

## AFL Compilation Verification

```bash
cd examples/continental-lz
python -c "
from afl.parser import parse
from afl.validator import validate
from afl.emitter import emit_dict
import sys; sys.path.insert(0, '../..')
sources = ''
for f in ['../osm-geocoder/ffl/osmtypes.ffl', '../osm-geocoder/ffl/osmoperations.ffl',
          '../osm-geocoder/ffl/osmcache.ffl', '../osm-geocoder/ffl/osmgraphhopper.ffl',
          '../osm-geocoder/ffl/osmgraphhoppercache.ffl', '../osm-geocoder/ffl/osmgtfs.ffl',
          '../osm-geocoder/ffl/osmzoombuilder.ffl', '../osm-geocoder/ffl/osmfilters_population.ffl',
          'afl/continental_types.ffl', 'afl/continental_lz_workflows.ffl',
          'afl/continental_gtfs_workflows.ffl', 'afl/continental_full.ffl']:
    with open(f) as fh: sources += fh.read() + '\n'
ast = parse(sources)
r = validate(ast)
print(f'Valid: {r.is_valid}, errors: {r.errors}')
compiled = emit_dict(ast)
wfs = [f\"{ns['name']}.{wf['name']}\" for ns in compiled.get('namespaces',[]) for wf in ns.get('workflows',[])]
print(f'Workflows: {len(wfs)}')
for w in wfs: print(f'  {w}')
"
```

## Resource Requirements

- **Disk**: ~72 GB (28 GB PBF + 44 GB GraphHopper graphs)
- **Memory**: 16 GB recommended (GraphHopper JVM needs 4-8 GB per graph build)
- **Agent concurrency**: limited to 4 to manage memory pressure
- **Time**: 12-30 hours for full continental run

## File Structure

```
examples/continental-lz/
  afl/
    continental_types.afl           — shared schemas
    continental_lz_workflows.afl    — LZ road infrastructure workflows
    continental_gtfs_workflows.afl  — GTFS transit analysis workflows
    continental_full.afl            — top-level combined pipeline
  docker/
    Dockerfile.agent                — agent image (Java + GraphHopper)
    Dockerfile.seed                 — seed image (compile + load)
  scripts/
    seed.py                         — compile AFL + seed MongoDB
    run_region.py                   — single region smoke test
  handlers -> ../osm-geocoder/handlers  (symlink)
  agent.py                          — RegistryRunner entry point
  requirements.txt                  — Python dependencies
  docker-compose.yml                — full Docker stack
  README.md                         — this file
```

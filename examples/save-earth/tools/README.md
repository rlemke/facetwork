# Save Earth — Tools

Standalone CLI utilities for fetching and mapping geolocated data from environmental-action sources: crowd-sourced litter observations, authoritative cleanup-site registries, and (in later PRs) tree-equity scores, 311 cleanup requests, coastal-cleanup chapters, and tree-planting projects. All outputs overlay cleanly on an OSM basemap and follow the same cache + handler pattern as `osm-geocoder` and `noaa-weather`.

Pattern contract: [`agent-spec/tools-pattern.agent-spec.yaml`](../../../agent-spec/tools-pattern.agent-spec.yaml)
Cache layout contract: [`agent-spec/cache-layout.agent-spec.yaml`](../../../agent-spec/cache-layout.agent-spec.yaml)

## Setup

```bash
./tools/install-tools.sh          # installs requests into the repo .venv
```

## Pipeline

```
   ┌──────────────────────────────┐     ┌──────────────────────────────┐
   │  download-openlittermap      │     │  download-epa-cleanups       │
   │  (crowd-sourced litter)      │     │  Superfund / Brownfields /   │
   │                              │     │  RCRA — authoritative sites  │
   └──────────────┬───────────────┘     └──────────────┬───────────────┘
                  │ openlittermap/                     │ epa-cleanups/
                  ▼                                    ▼
            ┌─────────────────────────────────────────────────┐
            │  build-save-earth-map                           │
            │  (MapLibre HTML with per-source layer toggles,  │
            │  OSM basemap, click popups w/ descriptions)     │
            └──────────────────────┬──────────────────────────┘
                                   ▼
                                maps/<region>/index.html
```

Every arrow is sidecar-mediated: each artifact has a sibling `.meta.json` with size, SHA-256, upstream URL, tool + version, and source-specific `extra` (feature count, dataset name, bbox if any).

## Available tools

| Tool | Input | Output cache | Purpose |
|------|-------|--------------|---------|
| `download-openlittermap` | `--url`, `--bbox` optional | `openlittermap/points.geojson` | Fetch crowd-sourced litter observations |
| `download-epa-cleanups` | `--dataset {superfund,brownfields}` (repeatable) | `epa-cleanups/<dataset>.geojson` | Fetch EPA authoritative remediation-site data from `geopub.epa.gov/EMEF/efpoints` (auto-paginates past the 10,000-record server cap) |
| `build-save-earth-map` | `--region`, `--center`, `--zoom` | `maps/<region>/index.html` | Stitch every cached layer into a single MapLibre HTML page |

Every tool supports:
- `--help` — show flags
- `--force` — re-download even if cached (where applicable)
- `--use-mock` — deterministic offline data (no network required)
- `--log-level` — Python logging level (default: INFO)

Defaults are real endpoints; if an upstream URL rotates, pass `--url` and it'll be recorded in the sidecar.

## Data sources

| Source | Shape | Licence | Notes |
|--------|-------|---------|-------|
| **OpenLitterMap** (openlittermap.com) | Points | CC-BY-SA 4.0 | Crowd-sourced, ~1M+ geotagged litter photos globally |
| **EPA Superfund NPL** — `geopub.epa.gov/EMEF/efpoints` layer 0 | Points | US Government public domain | ~1,400 NPL sites |
| **EPA Brownfields (ACRES)** — `geopub.epa.gov/EMEF/efpoints` layer 5 | Points | US Government public domain | ~40,000+ redevelopment sites |

Feature popups preserve the upstream `properties` so every point carries a real name, status, description, and (where available) a link back to the source system.

## Cache layout

All outputs live at `$AFL_CACHE_ROOT/save-earth/` (default: `/Volumes/afl_data/cache/save-earth/`):

```
cache/save-earth/
├── openlittermap/
│   └── points.geojson + .meta.json
├── epa-cleanups/
│   ├── superfund.geojson + .meta.json
│   └── brownfields.geojson + .meta.json
└── maps/
    └── <region>/
        ├── index.html
        └── .meta.json      (sibling of the file, per cache-layout spec)
```

## Typical workflows

**Bootstrap + render a global map (offline mock data, first-run friendly):**

```bash
./tools/install-tools.sh

./tools/download-openlittermap.sh  --use-mock
./tools/download-epa-cleanups.sh   --use-mock
./tools/build-save-earth-map.sh

open "$AFL_CACHE_ROOT/save-earth/maps/global/index.html"
```

**Real data, US-focused:**

```bash
./tools/download-openlittermap.sh  --bbox 24.4,49.4,-125.0,-66.9
./tools/download-epa-cleanups.sh
./tools/build-save-earth-map.sh --region us --center 39.8,-98.6 --zoom 4

open "$AFL_CACHE_ROOT/save-earth/maps/us/index.html"
```

**Only EPA data (no litter layer):**

```bash
./tools/download-epa-cleanups.sh --dataset superfund --dataset brownfields
./tools/build-save-earth-map.sh --include epa-superfund --include epa-brownfields
```

## `_lib/` — shared library

| Module | Role |
|--------|------|
| `sidecar.py` | Per-entry `.meta.json` read/write, per-entry locking |
| `storage.py` | LocalStorage / HdfsStorage abstraction + root-path derivation |
| `openlittermap.py` | OpenLitterMap fetch + cache + GeoJSON normalization + bbox trim |
| `epa_cleanups.py` | EPA Superfund / Brownfield / RCRA ArcGIS REST fetch + cache per dataset |
| `map_render.py` | MapLibre HTML renderer — inlines each cached GeoJSON as a toggleable layer, with click popups showing upstream `properties` |

The downloaders are pure fetch-and-cache — no transformation beyond normalizing the wrapper shape to a valid FeatureCollection. All per-feature metadata is preserved verbatim so popups can surface it.

## Standards + references

- **GeoJSON RFC 7946** — feature layer exchange format.
- **OSM basemap** — https://tile.openstreetmap.org (the default raster source; respect the OSM usage policy at https://operations.osmfoundation.org/policies/tiles/).
- **CC-BY-SA 4.0** for OpenLitterMap content — when redistributing the derived HTML, attribute OpenLitterMap contributors.
- **US Government public domain** for EPA datasets.

## Planned additions (later PRs)

- `download-rcra-corrective-action` — RCRA corrective-action sites (filtered from ECHO or pulled from per-region EPA feature services)
- `download-tree-equity` — American Forests Tree Equity Score (per-census-block canopy % + equity score) → `tree-equity/<state>.geojson`
- `download-open311` — municipal 311 "illegal dumping" / "cleanup needed" feeds per city → `open311/<city>/<service_code>.geojson`
- `download-surfrider` — Surfrider chapter directory + beach-cleanup events → `surfrider/{chapters,beach-cleanups}.geojson`
- `weather.save_earth` FFL handlers wrapping each downloader, so the runtime can produce the same artifacts as the CLI.

When those ship they'll slot into `build-save-earth-map` without any changes to the map renderer — new entries just get added to `DEFAULT_LAYERS`.

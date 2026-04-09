# OSM Geocoder Agent

A geocoding agent that resolves street addresses to geographic coordinates using the [OpenStreetMap Nominatim API](https://nominatim.openstreetmap.org/), plus handlers for ~580 OSM data processing event facets covering caching, boundaries, routes, parks, population, buildings, amenities, roads, visualization, GraphHopper routing graphs, and more.

## What it does

This example demonstrates:
- **Schema declarations** for typed data structures (`GeoCoordinate`, `OSMCache`)
- **Event facets** for external agent dispatch (`osm.Geocode`, cache, operations, POI)
- **AgentPoller** for building a standalone agent service
- **Foreach iteration** for batch geocoding (`GeocodeAll`)
- **Namespace-qualified handlers** registered programmatically from region registries
- **Multi-format downloads** — PBF (default) and Geofabrik free shapefiles (`.shp.zip`) — see [shapefiles README](handlers/shapefiles/README.md)

### AFL Workflow

```afl
schema GeoCoordinate {
    lat: String
    lon: String
    display_name: String
}

namespace osm.geocode {
    event facet Geocode(address: String) => (result: GeoCoordinate)

    workflow GeocodeAddress(address: String) => (location: GeoCoordinate) andThen {
        geo = Geocode(address = $.address)
        yield GeocodeAddress(location = geo.result)
    }
}
```

### Execution flow

1. `GeocodeAddress` workflow receives an address string
2. The `Geocode` event step pauses execution and creates a task
3. The geocoder agent picks up the task, calls the Nominatim API
4. The agent writes the result back and the workflow resumes
5. The workflow yields the `GeoCoordinate` as its output

## Prerequisites

```bash
# From the repo root
source .venv/bin/activate
pip install -r examples/osm-geocoder/requirements.txt
```

## Running

### Offline test (no network, mock handler)

```bash
PYTHONPATH=. python examples/osm-geocoder/test_geocoder.py
```

Expected output:
```
Executing GeocodeAddress workflow...
  Status: ExecutionStatus.PAUSED
Agent processing Geocode event...
  Dispatched: 1 task(s)
Resuming workflow...
  Status: ExecutionStatus.COMPLETED
  Outputs: {'location': {'lat': '48.8566', 'lon': '2.3522', 'display_name': 'Mock result for: 1600 Pennsylvania Avenue, Washington DC'}}

All assertions passed.
```

### Live agent (calls Nominatim API)

```bash
PYTHONPATH=. python examples/osm-geocoder/agent.py
```

This starts a long-running agent that polls for `osm.Geocode` tasks and ~330 OSM data events (caching, boundaries, routes, parks, population, visualization, etc.). In production, you would pair it with a runner service that executes workflows.

### Compile check

```bash
# Check all AFL sources (recursively discovers files in handler subdirectories)
find examples/osm-geocoder -name '*.ffl' -not -path '*/tests/*' -exec scripts/compile {} --check \;
```

## Project structure

The `handlers/` package is organized into 16 functional category subpackages, each containing its own handler modules, AFL source files, tests, and README:

```
handlers/
├── __init__.py              # backward-compatible facade + register_all_handlers()
├── shared/                  # shared utilities (_output.py, downloader.py, region_resolver.py)
├── amenities/               # amenity extraction (restaurants, shops, healthcare)
├── boundaries/              # administrative and natural boundaries
├── buildings/               # building footprint extraction
├── cache/                   # geographic region cache (~250 facets, 11 namespaces)
├── composed_workflows/      # example composed workflows
├── downloads/               # download and data processing operations
├── filters/                 # radius, OSM type, and validation filtering
├── graphhopper/             # GraphHopper routing graphs (~200 facets)
├── parks/                   # national parks, protected areas
├── poi/                     # points of interest
├── population/              # population-based filtering
├── roads/                   # road network extraction + zoom builder
├── routes/                  # bicycle, hiking, train, bus, city routing, GTFS, elevation
├── shapefiles/              # shapefile downloads
├── visualization/           # GeoJSON map rendering with Leaflet
└── voting/                  # US Census TIGER voting districts
```

Each category directory follows the same layout:

```
handlers/{category}/
├── __init__.py              # package marker
├── *_handlers.py            # handler modules
├── *_extractor.py           # extractors (where applicable)
├── afl/*.afl                # AFL source files for this category
├── tests/test_*.py          # category-specific tests
└── README.md                # documentation (moved from root-level .md files)
```

The core geocoding AFL file remains at `afl/geocoder.afl`.

## Handler categories

| Category | Event facets | Handler modules | Description |
|----------|-------------|-----------------|-------------|
| [cache](handlers/cache/) | ~250 | cache_handlers, region_handlers | Geographic region caching across 11 namespaces |
| [graphhopper](handlers/graphhopper/) | ~200 | graphhopper_handlers | Routing graph operations |
| [amenities](handlers/amenities/) | 29 | amenity_handlers, amenity_extractor, airquality_handlers | Restaurants, shops, healthcare, air quality |
| [roads](handlers/roads/) | 15 | road_handlers, road_extractor, zoom_* (6 modules) | Road networks + zoom builder |
| [downloads](handlers/downloads/) | 13 | operations_handlers, postgis_handlers, postgis_importer | Download, tile, routing, PostGIS import |
| [population](handlers/population/) | 11 | population_handlers, population_filter | Population-based filtering |
| [buildings](handlers/buildings/) | 9 | building_handlers, building_extractor | Building footprint extraction |
| [voting](handlers/voting/) | 9 | tiger_handlers, tiger_downloader | US Census TIGER data |
| [routes](handlers/routes/) | 8 | route_handlers, route_extractor, elevation_handlers, routing_handlers, gtfs_* | Bicycle, hiking, train, bus, city routing, GTFS |
| [poi](handlers/poi/) | 8 | poi_handlers | Points of interest |
| [parks](handlers/parks/) | 8 | park_handlers, park_extractor | National parks, protected areas |
| [boundaries](handlers/boundaries/) | 7 | boundary_handlers, boundary_extractor | Administrative and natural boundaries |
| [filters](handlers/filters/) | 7 | filter_handlers, radius_filter, osm_type_filter, validation_handlers, osmose_* | Radius, type, validation, OSMOSE |
| [visualization](handlers/visualization/) | 5 | visualization_handlers, map_renderer | GeoJSON rendering with Leaflet/Folium |
| [shapefiles](handlers/shapefiles/) | — | *(reuses downloads)* | Shapefile download workflows |
| [composed_workflows](handlers/composed_workflows/) | — | *(none)* | Workflow composition examples |

## Other files

| File | Description |
|------|-------------|
| `agent.py` | Live agent using AgentPoller + Nominatim API + all OSM handlers |
| `requirements.txt` | Python dependencies (`requests`, `pyosmium`, `shapely`, `pyproj`, `folium`) |
| `conftest.py` | pytest isolation for cross-example test collection |

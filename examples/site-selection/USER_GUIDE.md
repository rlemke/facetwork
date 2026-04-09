# Site Selection — User Guide

> See also: [Examples Guide](../doc/GUIDE.md) | [README](../README.md)

## When to Use This Example

Use this as your starting point if you are:
- Building **geospatial scoring pipelines** that fuse multiple data sources
- Combining **Census demographics** with **OpenStreetMap amenity data**
- Designing **multi-stage ETL** with download, extract, score, and export phases
- Learning how **implicit parallelism** works from AFL's dependency graph

## What You'll Learn

1. How a scoring pipeline downloads from Census API, TIGER shapefiles, and Geofabrik PBF files in parallel
2. How to join ACS demographics with TIGER county geometries and compute derived metrics
3. How to extract food amenities from OpenStreetMap PBF files using pyosmium
4. How the suitability formula combines demand factors with competition density
5. How workflows call other workflows for multi-state fan-out
6. How optional dependencies (shapely, pyosmium) are handled gracefully

## Step-by-Step Walkthrough

### 1. The Problem

You want to rank US counties for food-service business suitability. High-demand counties (high population density, high income, low poverty, educated workforce) with low existing restaurant competition should score highest.

### 2. The Data Pipeline

The `AnalyzeSite` workflow chains 8 steps across 4 phases:

```
Phase 1: Download (parallel)         Phase 2: Extract (parallel)
  DownloadACS ──────────────┐          JoinDemographics ←── ACS + TIGER
  DownloadTIGER ────────────┤
  DownloadPBF ──────────────┘          ExtractRestaurants ←── PBF

Phase 3: Score                        Phase 4: Export
  ScoreCounties ←── demographics       ExportScored → GeoJSON + MongoDB
                  + restaurants
```

In AFL:

```afl
workflow AnalyzeSite(state_fips: String = "01", state_name: String = "Alabama",
    region: String = "alabama") => (result: ScoredResult) andThen {
    acs = DownloadACS(state_fips = $.state_fips)
    tiger = DownloadTIGER(state_fips = $.state_fips, geo_level = "COUNTY")
    pbf = DownloadPBF(region = $.region)
    demographics = JoinDemographics(acs_path = acs.file.path, tiger_path = tiger.file.path, state_fips = $.state_fips)
    restaurants = ExtractRestaurants(pbf_path = pbf.file.path, region = $.region)
    scored = ScoreCounties(demographics_path = demographics.result.output_path,
        restaurants_path = restaurants.result.output_path, state_fips = $.state_fips)
    exported = ExportScored(scored_path = scored.result.output_path, state_fips = $.state_fips)
    yield AnalyzeSite(result = scored.result)
}
```

The three downloads have no inter-dependencies and run in parallel. `JoinDemographics` waits for ACS + TIGER; `ExtractRestaurants` waits only for PBF — so extraction also parallelizes.

### 3. Chained Schema Field Access

Step results flow through typed schemas with chained dot access:

```afl
demographics = JoinDemographics(acs_path = acs.file.path, tiger_path = tiger.file.path, ...)
```

Here `acs` is a step returning `(file: ACSFile)`, and `ACSFile` has a `path: String` field. The runtime resolves `acs.file.path` through two levels.

### 4. The Scoring Formula

Counties are ranked by suitability, which rewards high demand with low competition:

```
demand_index = weighted sum of 6 normalized demographic factors
restaurants_per_1000 = restaurant_count / (population / 1000)
suitability_score = demand_index * 100 / (1 + restaurants_per_1000)
```

| Factor | Weight | Source |
|--------|--------|--------|
| Population density | 0.25 | TIGER ALAND + ACS B01003 |
| Median income | 0.20 | ACS B19013 |
| Inverse poverty | 0.20 | 100 - pct_below_poverty |
| Labor force participation | 0.15 | ACS B23025 |
| Bachelor's degree+ | 0.10 | ACS B15003 |
| Owner-occupancy | 0.10 | ACS B25003 |

### 5. Multi-State Fan-Out

`AnalyzeSites_03` calls `AnalyzeSite` three times as independent sub-workflows:

```afl
workflow AnalyzeSites_03() => (states_completed: Long) andThen {
    alabama = AnalyzeSite(state_fips = "01", state_name = "Alabama", region = "alabama")
    alaska = AnalyzeSite(state_fips = "02", state_name = "Alaska", region = "alaska")
    arizona = AnalyzeSite(state_fips = "04", state_name = "Arizona", region = "arizona")
    yield AnalyzeSites_03(states_completed = 3)
}
```

### 6. Running

```bash
# From repo root
source .venv/bin/activate
pip install -e ".[dev]"
pip install -r examples/site-selection/requirements.txt  # shapely, pyosmium, requests

# Compile check
afl examples/site-selection/ffl/sitesel.afl --check

# Run tests (no network required)
pytest examples/site-selection/tests/ -v
```

## Key Concepts

### Graceful Optional Dependencies

Both `pyosmium` (for PBF extraction) and `shapely` (for point-in-polygon scoring) are optional. Handlers fall back to empty results rather than crashing:

```python
try:
    import osmium
    HAS_OSMIUM = True
except ImportError:
    HAS_OSMIUM = False

def extract_restaurants(pbf_path, region):
    if not HAS_OSMIUM:
        return {"type": "FeatureCollection", "features": []}
    # ... real extraction ...
```

Tests verify both the happy path and the absent-library fallback.

### Food Amenity Extraction

The restaurant extractor uses pyosmium's `SimpleHandler` to find OSM nodes tagged with `amenity` in: `restaurant`, `fast_food`, `cafe`, `bar`, `pub`, `food_court`, `ice_cream`. Each match becomes a GeoJSON Point feature.

### OutputStore Integration

The export handler writes scored GeoJSON locally and optionally pushes to MongoDB:

```python
store = OutputStore(db)
store.ingest_geojson(f"sitesel.scored.{state_fips}", scored_path, feature_key="GEOID")
```

MongoDB failure is logged as a warning but is non-fatal — the local file is always written.

## Adapting for Your Use Case

### Change the scoring weights

Modify the weight dictionary in `scoring_builder.py`:

```python
WEIGHTS = {
    "population_density_km2": 0.30,  # increase population weight
    "median_income": 0.25,
    # ...
}
```

### Add new amenity types

Extend the amenity filter in `restaurant_extractor.py`:

```python
FOOD_AMENITIES = {"restaurant", "fast_food", "cafe", "bar", "pub", "food_court", "ice_cream", "bakery"}
```

### Score different business types

Replace the restaurant extractor with your own OSM tag filter (e.g., `shop=supermarket` for grocery store analysis).

## Next Steps

- **[census-us](../census-us/USER_GUIDE.md)** — deeper Census ETL with 12 ACS tables and MongoDB ingestion
- **[osm-geocoder](../osm-geocoder/USER_GUIDE.md)** — full production-scale OSM agent
- **[continental-lz](../continental-lz/USER_GUIDE.md)** — Docker-orchestrated multi-region pipeline

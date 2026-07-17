# Continental Emergency-Access Atlas (`osm.emergency`)

**Status:** design — no code yet. Pilot scope (§8) before any continent-scale run.
**Repo split:** facets/handlers will live in [`fwh_osm`](https://github.com/rlemke/fwh_osm)
under a new `osm.emergency` namespace; the composed workflow is a catalog
candidate (osm-lz-style). This doc is the contract to review before scaffolding.

## 1. What and why

One workflow that answers "how ready is each region of a continent for an
emergency?" — and, deliberately, the best **distribution showcase** the
platform has: a task graph that visibly expands, partially reduces, expands
again, and converges, instead of a flat N-region conversion.

```
Continent
  └─ fan out: Geofabrik/osmfr regions (~50 for Europe)
       └─ fan out: largest cities per region (~10)
            ├─ fan out: emergency categories (hospitals, fire, police, [shelters])
            └─ fan out: city→facility route pairs (nearest ~5 per category)
                 └─ fan in: per-city accessibility metrics
       └─ fan in: regional readiness score + GeoJSON layer
  └─ fan in: MergeLayers → continental map + ranked table
```

~50 × 10 × 4 × 5 ≈ **10,000 leaf tasks** across four nested levels. Because
artifacts finalize to shared MinIO, one server can extract a region, others
route its cities, and a third performs the merge — the same cross-server flow
already verified by the heatmap fan-outs.

## 2. Composition pattern (the FFL contract)

Under relative `$`-scoping a block sees only its container's attributes and
same-block steps — so **every fan-in lives in the return of a child facet**,
and each level calls the next level down as a step. No cross-block refs, no
nested gating:

```ffl
workflow ContinentalEmergencyAtlas(continent: String)
    => (map_html: String, rankings: Json)
andThen {
    regions = AnalyzeRegions(continent = $.continent)   // fan-out level 1 inside
    merged  = MergeLayers(inputs = regions.layers)      // continental fan-in
    ranked  = RankRegions(metrics = regions.metrics)
    map     = RenderMap(input_path = merged.output_path)
    yield ContinentalEmergencyAtlas(map_html = map.html_path, rankings = ranked.results)
}
```

`AnalyzeRegions` foreach-fans over regions and returns `(layers: [String],
metrics: Json)`; `AnalyzeRegion` fans over cities; `AnalyzeCity` fans over
categories; `RouteFacilities` fans over route pairs. Each returns the
aggregate the next level up consumes. This child-facet-return pattern is the
canonical shape the validator enforces (`REF_CROSS_BLOCK_STEP` et al.).

## 3. Existing building blocks (verified)

| Block | Where | Role here |
|---|---|---|
| `ContinentHeatmap` | `fwh_osm/.../visualization/ffl/osmheatmap.ffl:92` | regional fan-out + final render precedent |
| `RouteFanout` | `fwh_osm/.../network/ffl/osmnetwork_workflows.ffl:91` | distributed route-pair calculation |
| `CitiesAndRoutesByZoomFanout` | `fwh_osm/.../cities/ffl/osmcities_routes_fanout.ffl:54` | closest end-to-end foundation |
| `MergeLayers` | `fwh_osm/.../transform/ffl/osmtransform.ffl:29` | continental fan-in (pure, cheap) |
| `osm.Region.ResolveRegions` / `osm.cache.Download` | region enumeration + cache-aware extract download | level-1 inputs |
| `osm.Network` (approximate routing) | [approximate-freeway-routing.md](approximate-freeway-routing.md) | in-process routing, per-region artifact |

New facets needed: `osm.emergency.ExtractFacilities` (PBF → per-category
GeoJSON via the source-adapter pattern), `AnalyzeCity` / `CityMetrics`,
`ReadinessScore`, `RankRegions`. Everything else is composition.

## 4. Design decision — routing granularity (the one real mismatch)

`osm.Network`'s freeway-grade artifact was designed for **long-distance**
approximate routing. "City center → nearest hospital" is a 2–10 km
intra-urban trip that mostly never touches a freeway — nearest-facility
distances computed on that network would be junk precisely where the atlas
claims the most.

**Decision:** two network tiers, both in-process:

- **City metrics** route on a per-region network built at
  `motorway|trunk|primary|secondary` granularity — still a small artifact
  (built per region, not per continent), read once per runner.
- **Inter-city / regional connectivity** (optional layer) keeps the freeway
  artifact as designed.

Metrics that remain honest on a coarse network: **reachability buckets**
(facilities within 10/25/50 km network distance) and **nearest-facility
network distance** on the secondary-grade tier. Anything finer (door-to-door
minutes) is out of scope and stays out of the map's claims.

## 5. Design decision — facilities from the PBF, never Overpass

Each region's extract is already downloaded; emergency categories come from
the extract via the source-adapter pattern (`amenity=hospital|clinic`,
`amenity=fire_station`, `amenity=police`, `emergency=*`). The whole atlas is
then cache-hermetic and offline. Overpass at 50 regions × 10 cities × 4
categories is the documented "when *not* to fan out" trap (per-IP rate
limits; see the save-earth power-infrastructure notes).

**Honest-scope constraints carried onto the map:**

- `emergency=shelter` is thinly mapped nearly everywhere → either omit or
  label the layer "**mapped** shelters" so sparse data can't read as "no
  shelters exist."
- OSM `population` tags are patchy → "facilities per 100k" uses the tag when
  present with a disclosed fallback (largest-N city selection by place rank),
  or the metric is dropped for cities without population. No silent guesses.
- Extract provider is `FW_OSM_EXTRACT_PROVIDER=osmfr` (Geofabrik has this
  fleet's IP banned); deltas follow automatically.

## 6. Design decision — bounded region concurrency

Concurrent heavy fan-outs at this exact shape caused the Docker-VM disk
incident (51-state fan-outs → disk full → Mongo crash-loop). The
download/extract/network-build tier runs with **bounded region concurrency
(5–8 in flight)** plus the disk-guard; the cheap leaf tiers (routes, metric
combines) fan out freely. The 10k leaf tasks themselves are fine for the
runtime — atomic claims and the `osm` task list held at this scale — the
throttle exists for disk and extract I/O, not task count.

This run is also the deepest `andThen` nesting attempted, i.e. the best
stress test yet of the shared `_fw_continue` backlog — instrument it, and
treat the results as input to the
[ffl-runner orchestration tier](ffl-runner-orchestration-tier.md) decision.

## 7. Design decision — readiness score is a disclosed value judgment

Same contract as the livability and cancer domains: the regional readiness
score's **weights are workflow parameters** with defaults shown in the map's
"About this data" popup, and every regional score decomposes in the popup to
the city metrics that produced it (which themselves decompose to facility
counts and route distances). No opaque numbers anywhere in the chain.

Per-city metrics (from §4's honest set):

- nearest-facility network distance, per category
- facilities within 10/25/50 km network distance, per category
- facilities per 100k residents (population-tagged cities only)

Regional score: weighted mean of city percentiles, cities weighted by
population where known — weights disclosed, method identical in spirit to the
livability composite.

## 8. Execution plan

1. **Pilot (v1): 5-region cluster** — Benelux + neighbors. Validates the
   composition, per-level cost, cache layout, and the secondary-grade network
   size before any continent run. Target: complete on the current 4-host
   fleet in one evening, dashboard graph showing all four expansion levels.
2. **Instrument**: continuation-backlog depth, per-level task latencies,
   region-tier disk high-water mark.
3. **Europe (~50 regions)** with the concurrency bound from §6.
4. Rendering: MapLibre continental map (readiness choropleth by region,
   facility layers toggleable per category) + ranked table page — the
   established gallery pattern, publishable via `census.Publish.PublishToSite`.

Cache layout (per [`agent-spec/cache-layout.agent-spec.yaml`](../../agent-spec/cache-layout.agent-spec.yaml)):
facilities and networks are per-region sidecar-backed artifacts under
`$FW_CACHE_ROOT/osm/emergency/<region>/…`, so a re-run re-fans only what
changed.

## 9. Open questions (decide at pilot review)

- Clinics in the hospital category, or a fifth category? (OSM tagging splits
  them; `healthcare=*` adds coverage but noise.)
- City selection: place-rank top-N per region vs population threshold — the
  patchy-population problem again; pilot should report how many candidate
  cities lack population tags.
- Does `RouteFanout`'s pair shape (`pairs: Json`) carry category labels
  through, or does `RouteFacilities` need its own pair schema?
- Whether the inter-city freeway layer earns its cost in v1 or waits.

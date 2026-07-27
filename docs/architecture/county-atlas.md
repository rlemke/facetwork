# County Atlas — a per-county map platform (design + as-built)

> **Status:** the design in §1–§8 is **shipped** as the standalone domain `fwh_county_atlas`,
> run to national scale (3,167 counties), and deployed on the fleet. **[§9 "As built"](#9-as-built-shipped-2026-07)**
> records what was actually implemented, where reality diverged from the design, the fan-out
> results, deployment, publishing/curation, and the operational lessons.

A **map per US county** (~3,143) plus a **master index** (US → state → county) that lets
a viewer toggle a large set of information layers by checkbox, read a panel of
calculated indicators, and drill from the nation down to one county.

The design goal is **one generic renderer driven by a layer catalog**, not hundreds of
bespoke handlers. Adding a layer is adding a row to a catalog, never writing code. It
reuses the domains Facetwork already has — above all the **per-county OSM PBF tree**
produced by `osm.planet.BuildAdminFanout` (`north-america/us/<state>/<county>-latest.osm.pbf`).

> **Data-availability rule (hard):** the catalog includes a layer **only** if there is a
> real, downloadable, national- (or near-national-) coverage, county/tract-resolution,
> openly-licensed source for it. Local-only, proprietary, real-time, or
> privacy-suppressed items are **excluded** (see [Excluded layers](#excluded-layers)),
> not faked. This mirrors the "be honest where coverage is thin" discipline used across
> the `fwh_*` docs.

---

## 1. Architecture in three pieces

| Piece | What it is | Analogue in the repo |
|---|---|---|
| **`layers.json`** | A registry, one small spec per layer (source, geometry, privacy, tier). | `domains.json` |
| **Generic county-atlas renderer** | Reads a per-county `manifest.json`, draws whatever layers materialized, builds the checkbox tree from the catalog's category structure. | the MapLibre renderers in `fwh_health` / `fwh_census_us` |
| **Master index** | US → state → county navigation, generated from a coverage matrix. | the national county choropleth in `fwh_census_us` |

You never author a "parks handler" and a "hospitals handler" and 250 more. You author a
**layer spec** that points at a source you already have; the renderer does the rest. The
pasted taxonomy *is* the category tree in the UI and the grouping in the catalog — data,
not code.

## 2. The layer spec (the whole system in one object)

```jsonc
{
  "id": "osm.parks",
  "label": "Parks & open space",
  "category": "Parks and outdoor recreation",   // = a section in the checkbox tree
  "geometry": "polygon",                          // point|line|polygon|choropleth|raster
  "source": { "kind": "osm_pbf", "filter": "leisure=park OR boundary=national_park" },
  "privacy": "public",                            // public|aggregate|generalized|suppressed
  "aggregation": null,                            // {"unit":"tract","measure":"rate","denom":"population"}
  "calc": null,                                   // {"op":"isochrone_coverage","base":"osm.parks","minutes":10,"mode":"walk"}
  "coverage": "national",                         // national|state|sparse  (honest availability)
  "tier": 1, "min_zoom": 8,
  "style": { "fill": "#2e7d32", "opacity": 0.4 },
  "license": "ODbL", "attribution": "© OpenStreetMap contributors"
}
```

`source.kind` is one of a handful — the entire integration surface:

- **`osm_pbf`** — an `osmium tags-filter … | export` over the county PBF (already extracted).
- **`domain`** — call an existing facet (`census-us`, `health`, `save-earth`, `noaa-weather`).
- **`http`** — a national open dataset fetched + clipped to the county (EPA/USGS/FEMA/…).
- **`calc`** — a derived layer (see §5).

## 3. Data sources — what actually has downloadable county-resolution data

Every layer below has a real, fetchable, openly-licensed, national (or near-national) source.

### From the county OSM PBF (`osm_pbf`) — free, already extracted
Boundaries (county/municipal/place), cities/towns/villages, roads by class
(interstate → local, `highway=*`), bridges & tunnels, railways & stations, airports,
bus stops, cycleways & shared-use paths, EV chargers, public parking; parks / national &
state parks / nature reserves / wilderness (`leisure=*`, `boundary=protected_area`),
trails (+ `sac_scale` difficulty), trailheads, campgrounds, picnic areas, viewpoints,
restrooms, boat ramps & marinas, beaches, golf/dog parks/playgrounds/sports pitches;
rivers/streams/canals, lakes/reservoirs/ponds, wetlands, coastline, springs, tree cover;
government (city halls, courthouses, police, fire stations, post offices, libraries,
community centres); healthcare **points** (hospitals, clinics, pharmacies, dentists,
nursing homes); schools/colleges/childcare; landmarks & historic sites; landfills &
recycling; wastewater plants; land use (`landuse=*`), historic districts.
License: **ODbL / © OpenStreetMap contributors**.

### From Census — `census-us` domain (ACS + TIGER)
Geometry: census tracts & block groups, ZCTAs, school-district boundaries,
congressional/legislative districts, tribal areas (AIANNH). Attributes (ACS,
**aggregate/choropleth**): population, density, growth; age, household size, family
composition; race/ethnicity, language, LEP, foreign-born, veterans, disability;
educational attainment; internet & vehicle access; median household & per-capita income,
poverty & child poverty; unemployment, labor-force participation; commute time, WFH;
median home value & rent, rent burden, housing-cost burden, homeownership, vacancy,
building age, units-in-structure, housing density. License: **US Census (public domain)**.

### From CDC — `health` domain
CDC **PLACES** (census-tract, aggregate): adult prevalence of cancer, diabetes, stroke,
asthma, obesity, smoking, mental distress, physical inactivity, insurance coverage,
checkup/preventive rates. **County Health Rankings / NCHS**: life expectancy, premature
death, infant mortality. **CDC WONDER** (county, suppressed <10): overdose mortality.
License: **public domain (CDC/NCHS/RWJF-CHR)**.

### From EPA (`http`)
Superfund sites (SEMS), Brownfields (ACRES), TRI facilities (Toxics Release Inventory),
air-quality monitors + annual AQI (AQS/AirNow), public drinking-water systems (SDWIS).
License: **public domain (EPA)**.

### From USGS (`http`) — some via `save-earth`
Earthquakes M≥ (ANSS), Quaternary faults, principal aquifers, NHD hydrography, 3DEP
elevation/hillshade/slope, NLCD land cover / impervious surface / tree canopy (raster).
License: **public domain (USGS)**.

### From FEMA (`http`)
**National Risk Index** (county: composite + per-hazard — wildfire, flood, heat, drought,
tornado…), disaster declarations, NFHL flood zones where published.
License: **public domain (FEMA)**.

### From NOAA — `noaa-weather` domain
Climate normals: average temperature, precipitation, extreme-heat days (GHCN stations →
county). License: **public domain (NOAA)**.

### Other confirmed-downloadable national sets (`http`)
USDA Cropland Data Layer (crop types, raster) + Prime Farmland; USDA Food Access Research
Atlas (tract); HUD public/subsidized housing + CoC PIT homelessness counts (CoC-level,
**aggregate**); FCC National Broadband Map (block/county availability); Eviction Lab
(county, **aggregate**, historical). Licenses per agency (public domain / open).

## 4. Privacy — a required, enforced catalog field

The taxonomy's own cautions become four **enforced** tiers. Catalog validation rejects any
spec whose representation violates its tier; the renderer will not draw it the wrong way.

| Tier | Rule | Examples |
|---|---|---|
| `public` | render as-is (points OK) | hospitals, parks, roads, schools, POI |
| `aggregate` | **must** join to a census area and render as choropleth/rate; **never** raw points | demographics, income, health prevalence, crime rate |
| `generalized` | snap to grid / show service area, not exact site | some utility & emergency infrastructure |
| `suppressed` | never from exact locations; area-rate or omit | homeless encampments, individual crash victims, vulnerable-person parcels |

## 5. Calculated indicators — a closed set of operators

The "useful calculated indicators" all reduce to ~6 ops applied to a base layer + a
denominator, so they are catalog rows too, not bespoke code:

`per_capita` · `density` · `nearest_distance` · `isochrone_coverage` (% residents within
N min of X — reuses **`osm.Network`** approx-routing + block-group population) · `ratio` ·
`change_over_time` (across ACS vintages).

"Residents within a 10-min walk of a park," "beds per 10k," "fire coverage by response
time" are each just `(op, base_layer, params)`.

## 6. Generation — reuse the planet-pipeline fan-out

A new standalone domain `fwh_county_atlas`, same shape as `osm.planet`:

```
county.atlas.BuildCountyAtlas(state, county, tier) =>
    read north-america/us/<state>/<county> PBF
    for each catalog layer whose source covers this county:
        materialize -> GeoJSON (small) or PMTiles (heavy: roads)
    write manifest.json + render index.html

county.atlas.BuildAtlasFanout(tier) =>
    foreach child in ListExtracts("north-america/us/*/*")   # the ~3,143 county PBFs
        BuildCountyAtlas(child)

county.atlas.BuildMasterIndex =>   US + per-state index pages from the coverage matrix
```

Identical to `BuildAdminFanout` — it just **consumes** the county PBFs instead of
producing them. Wall-clock ≈ slowest county.

**Scale via tiering + laziness**, never brute force (3,143 × ~250 ≈ 800k artifacts):
- **Tier 1** (OSM-derived): near-free — the PBFs already exist.
- **Tier 2** (census/health/EPA choropleths): reuse existing domain outputs joined to
  county/tract geometry.
- **Tier 3** (isochrone/calc): expensive → on demand or priority counties only.
- Heavy vector layers → **PMTiles per county** so the browser toggles without re-fetching.
- The `manifest.json` advertises **only** layers that materialized for that county → no
  dead checkboxes, honest coverage per county.

## 7. Output layout & UX

```
atlas/index.html                      US map -> click a state
atlas/<state>/index.html              county choropleth + searchable list
atlas/<state>/<county>/index.html     the atlas: map + category-grouped checkbox tree
atlas/<state>/<county>/manifest.json  + a calculated-indicator dashboard panel
atlas/<state>/<county>/layers/*.{geojson,pmtiles}
```

The checkbox tree **is** the taxonomy — collapsible by category, searchable; layer data
lazy-loads on toggle; each layer carries its own legend + attribution; a side panel shows
the county's calculated indicators. Production rendering is MapLibre GL + PMTiles
(basemap from a tile provider); a self-contained SVG variant is used for embeddable
previews.

## 8. Excluded layers

Included in the taxonomy but **omitted** because there is no uniform national, open,
county-resolution downloadable source (would have to be faked or scraped per-jurisdiction):

Parcels & property boundaries; voting precincts; county/municipal & special-service
districts; neighborhood names; address points; speed limits, traffic volumes/congestion,
road closures/construction, crash locations, high-injury corridors, snow-removal/truck
routes; transit travel times & full GTFS stop networks; park-and-ride, individual
campsites/RV hookups; streetlight coverage, hydrant completeness, power outages, utility
service areas (real-time/local); short-term rentals, foreclosures, property micro-data
(proprietary); traffic citations, calls-for-service, police/fire response times (local);
homeless encampment locations (suppressed by policy); raw crime incident points (kept only
as rates where a county source exists).

Each stays a **known gap** in the catalog (`coverage: excluded`), so the map shows
"not available" rather than inventing data — and can be promoted later if an open national
source appears.

---

# Part II — As built

## 9. As built (shipped 2026-07)

Everything in §1–§8 is implemented as the standalone domain **`fwh_county_atlas`**
(github.com/rlemke/fwh_county_atlas), run to national scale, and deployed on the fleet.
This section is the record of what was actually built and where it diverged from the design.

### 9.1 Design → as-built

| Design (§1–§8) | As built | Why the divergence |
|---|---|---|
| MapLibre GL + PMTiles renderer | **Self-contained inline SVG** "plate" (one file/county, zero external requests) | Servable from any static host — MinIO with **no tile provider**, GitHub Pages, or an artifact. Cost: the size wall (§9.6). PMTiles stays the production path for the heavy layers. |
| 99-layer catalog | **87/99 layers wired** | The 12 unwired need a raster/tile renderer (NLCD/CDL/3DEP land cover, NFHL floodplains) SVG can't draw, or the 500 MB national ZCTA set. |
| 6 calc operators (§5) | **3 wired**: `ratio`, `nearest_distance` (straight-line, honestly labelled), `per_capita` | `isochrone_coverage` needs `osm.Network`; `density`/`change_over_time` deferred. |
| `source.kind` handful | **8 source-adapter modules, 7 fetch patterns** (§9.2) | Same idea (catalog is the integration surface); more source shapes than the design's 4 kinds. |

### 9.2 Source adapters — the integration surface as built

One module per source under `src/county_atlas/tools/_county_atlas_tools/`:

| Module | Source & pattern |
|---|---|
| `census.py` | ACS via **reuse** of `census_us._lib.metrics` registry + `census_var` direct-column choropleths; TIGER tracts (shared cache) |
| `health.py` | CDC **PLACES** Socrata `cwsq-ngmh` by `countyfips`, reuses census tract geometry |
| `epa.py` | **TRI** per-county (Envirofacts `STATE_ABBR`+`COUNTY_NAME`+`ROWS/JSON`) + **Superfund/Brownfields** via EMEF national → shared cache → bbox clip |
| `usgs.py` | earthquakes (FDSN), aquifers, faults — all **bbox envelope** on ArcGIS FeatureServers |
| `fema.py` | **National Risk Index** tract choropleth (FeatureServer by `STCOFIPS`) |
| `noaa.py` | **GHCN** `ghcnd-stations.txt` (10 MB, shared cache), reuses `noaa_weather.parse_stations` |
| `hud.py` | Public/subsidized **housing** points (bbox FeatureServer) |
| `tiger.py` | block-group + school-district overlays (per-state shapefile, shared cache) |
| `calc.py` | tier-3 derived indicators |

Two patterns made a 3,167-county fan-out tractable without re-downloading the internet:

1. **Resolve FIPS + tract geometry once per county**, then share that geometry across
   census / health / FEMA (they all join to the same tracts).
2. **Shared national cache** (`s3://…/county-atlas/_shared/`) for datasets with **no
   county-level filter** (EPA EMEF, NOAA GHCN, per-state TIGER shapefiles): the first
   county to need it fetches the whole national/state file once; every other county in the
   fan-out reads the cache and bbox-filters. Without this, ~9,400 redundant census.gov
   shapefile pulls.

**Cross-domain reuse** (the "lookup-then-compose" moat, made concrete): `census_us._lib.metrics`,
`save_earth` EPA endpoints, `noaa_weather.parse_stations`, `livability` NRI FeatureServer.

### 9.3 Renderer as built

Self-contained SVG + category checkbox tree + a mutually-exclusive choropleth legend +
a calculated-indicator panel. Size/quality techniques (rural county **6.5 MB → 1.7 MB**):

- **`<defs>`/`<use>` geometry dedup** — each tract shape is defined once and referenced by
  every choropleth that colours it.
- **Collinearity line-simplification** (tol² = 0.8) on boundary/road geometry.
- **Point gate** — only `geometry:"point"` layers draw markers. Fixed the "red dots with
  nothing selected" bug: polygon/line layers carried stray OSM Point nodes that rendered as
  default circles.
- **viewBox zoom/pan** — wheel + pointer-drag + buttons, with a live scalebar.
- **Click popups** on point layers (per-feature name/attributes).

### 9.4 Fan-out — run to national scale

`county.atlas.workflows.BuildAtlasFanout(prefix="north-america/us", tier=3)` over the
per-county OSM PBF tree (`north-america/us/<state>/<county>-latest.osm.pbf`, produced by
`osm.planet.BuildAdminFanout`) → **3,167 county atlases, 0 failed**, ~7.0 GB in MinIO
(avg 2.5 MB), then `BuildMasterIndex` → 51-state index. Run by **7 native detached runners
(14 workers) on server3**, ~23 s/county warm.

**Why native runners, not the Docker fleet:** the seeded FFL handlers carry `file://`
handler paths that a native runner reads as host paths directly; containerized fleet
runners can't. Validated on Oregon (36/36) before the national run. Recipe:

```bash
FW_MONGODB_URL=mongodb://localhost:27017 FW_S3_ENDPOINT=http://localhost:9000 \
FW_ATLAS_BUCKET=osm-extracts CENSUS_API_KEY=<fleet-secret> \
.venv/bin/python -m facetwork.runtime.runner --registry --topics "county.atlas.*" --log-format text
```

Gotchas: `--mongo`/`--max-workers` are **ENV**, not CLI flags (only `--topics` is a flag);
handlers persist in Mongo **`handler_registrations`**, not `db.handlers` (which is empty for
every domain — a red herring).

### 9.5 Deployment & publishing

- **Fleet config**: in `domains.json` (`task_list=county`, `fleet_default`, `scaled`),
  `gen-compose`'d, FFL seeded, repo made public. **Baked into the fleet image**
  (`server3.local:5050/facetwork-runner:46de3bc`, 1 of 19 baked domains) for permanence.
  **Not** a standing runner role — it's a batch domain, so bake = on-demand availability;
  no `runner-county-atlas` container auto-starts (fine; add a role only if continuous
  serving is wanted).
- **Full set (local)**: the `osm-extracts` MinIO bucket is whole-bucket public (the
  self-hosted-Geofabrik PBF serving) → the 3,167-county archive browses at
  `http://server3.local:9000/osm-extracts/county-atlas/index.html`.
- **GitHub Pages (curated, 2026-07-28)**: **4 small examples** (Coos OR, San Juan CO,
  Loving TX, Petroleum MT) + a custom `county-atlas/index.html` linking the full local
  archive. The **2 large examples** (Santa Clara CA, Harris TX) are **local-only** — 404 on
  Pages, still served from MinIO — a deliberate "a few examples, don't overload the repo"
  split, live at https://rlemke.github.io/facetwork-maps/county-atlas/.

### 9.6 Scale constraint (the wall) & the production path

Self-contained SVG **duplicates tract geometry per choropleth** → ~7 MB/rural county,
20–50 MB dense-urban → 3,167 counties ≈ **30–50 GB**. Feasible in MinIO; **not publishable
wholesale to GitHub Pages** (~1 GB Pages limit). A national GitHub publish would need the
**MapLibre GL + PMTiles** renderer from the original design (shared tract geometry, tiles
fetched on toggle) — the deferred production path. Until then: full set on MinIO, curated
subset on Pages.

### 9.7 Operational lessons (reusable)

- **`publish_bundles` clobbers a staged custom index.** It auto-generates a plain section
  landing at `<dest>/index.html`. To keep a custom index, overwrite it *afterward* via the
  GitHub contents API (PUT with the file's current `sha`) — no re-clone of the big repo.
- **Big HTML gzips ~30× in git.** Dropping the two large examples barely shrank the repo
  (97 → 98 MB): repeated inline-SVG structure compresses away, so the repo weight is the
  accumulated *history of all map families*, not any one big file. Real reclaim is
  `git filter-repo --path <dir> --invert-paths` + force-push (git-filter-repo via
  `pip install git-filter-repo`, run `python -m git_filter_repo`).
- **Version-skew in an informal fleet is a correctness hazard.** Old-image runners on other
  hosts silently re-contaminated the county extraction (claimed `BuildAdminSet`, wrote
  suffixed duplicate keys); it converged only after `docker rm -f` on the stale containers.

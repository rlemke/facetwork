# County Atlas — a per-county map platform (design)

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

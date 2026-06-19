# A composable facet library for LLM-authored workflows (design)

The blueprint for Facetwork's next step: turn a *pile of facets that grew organically for
humans* into a **deliberately-designed, complete, orthogonal, discoverable, distributed
library of primitives** — so Claude can satisfy a complex natural-language request by
*composing* it, remember how (the catalog + authoring summary), and reuse it without
replaying the refinement. OSM is the worked case; the model generalizes to any domain.

Pairs with: [Claude workflow catalog](claude-workflow-catalog.md) (the memory of solved
requests), [extending with new handlers](extending-with-new-handlers.md) (the growth path),
[`use` resolution](catalog-use-resolution.md) (libraries are pinned, not loose files),
[`agent-spec/tools-pattern.agent-spec.yaml`](../../agent-spec/tools-pattern.agent-spec.yaml)
(the handler/tools/cache contract), and the AI-authorship thesis (`docs/thesis/ai-authorship.md`).

## 1. The shift, and the moat

Facetwork started as "humans write workflows in a DSL." The value now is a different shape:

- **A vast library of reusable functionality** (facets/events) — the *primitives*.
- **Claude composes** a workflow that satisfies an NL request from those primitives.
- **The catalog remembers** how the request was satisfied — an immutable, parameterized,
  reviewed workflow with an **authoring summary** of intent.
- **The fleet executes** it, distributed (per-region `foreach` fan-out, lock-free).

The moat is the third point. The expensive part of an NL request is the **refinement** —
turning *"map the interstate freeways in California"* into a precise spec (`highway=motorway`
∧ `ref` starts "I "; which facets; which params). Running is cheap by comparison. The catalog
+ summary make that refinement a **one-time cost per request-family**; the library makes the
spec *expressible* by composition. Library and memory reinforce each other: the memory shows
which compositions recur → which primitives to harden and add.

## 2. The starting point — and how the gap was closed

The osm package had ~99 workflows and many facets, but it grew *organically for humans*, so it
**was not** a composable library. The symptoms diagnosed below were real; §9 tracks how each was
closed. **As of this writing the primitive library is built** — every layer of the §4 taxonomy
ships, proven on real data, and the composition-time items (6 reuse-first matching, 7 effect/cost
annotations) are shipped too — **all seven roadmap items are complete** (§9).

The original symptoms (all now addressed except the two noted extract holes):

- **Holes** — several `extract_*` functions were declared but never written. `extract_amenities`,
  `extract_places_with_population`, `extract_parks`, and `extract_buildings` are now implemented
  (joining `extract_roads`); `extract_routes` (relation stitching) and `extract_pois` (aggregate
  shape) remain.
- **Inconsistent contracts** — `FilterByOSMTag` filters a PBF; `FilterGeoJSONByOSMType` filters
  GeoJSON; `CacheRegion` yields the *full* region PBF, not category extracts.
- **No semantic discovery** — Claude must already know "fast food" → `amenity=fast_food`. There
  is no queryable index of facet capabilities or the domain's tag vocabulary.
- **Not designed for repeated composition** — a single business search filtered the full 1.2 GB
  California PBF (~54 min) because nothing was designed to be re-composed cheaply.
  → *Closed*: `ExtractCategory` warms cheap cached category extracts (then instant), and `Clip`
  cuts a region to a metro bbox first, so re-composition is seconds, not a full-PBF re-scan.

The first two symptoms map to §9 item 1 (extraction; only `routes`/`pois` extract holes remain),
the third to item 5 (discovery), the fourth to items 1–2. **The gap this document specified is
closed**; what follows is the design that was realized, with shipped-status markers in §9.

## 3. Design principles

1. **Orthogonal** — one facet, one operation. No facet should do "extract *and* filter *and*
   render"; those are three composable steps.
2. **Complete** — no declared-but-unimplemented facets. A facet in the library *works*.
3. **Uniform contracts** — primitives in a layer share an I/O convention (below), so any output
   feeds any compatible input. This is what makes composition predictable for an LLM.
4. **Single-region leaf, fan-out at the workflow** — leaf facets take ONE region; multi-region is
   `andThen foreach` at the workflow, so the fleet parallelises (the established preference).
5. **Cheap to re-compose** — operate on cached *category* extracts (or PostGIS), never re-scan the
   full region PBF per query. Cache + idempotency are first-class.
6. **Discoverable** — every facet carries machine-readable purpose, typed params, effects, and a
   rough cost, and the domain ships a **vocabulary** (the tag ontology) Claude can query.
7. **Distributed by construction** — composition is over steps the lock-free fleet schedules.

## 4. The primitive taxonomy (layered)

A request decomposes through layers; each layer has one contract. (✓ = exists, ◐ = partial/
inconsistent, ✗ = to build.)

The layering is corroborated by the open-source OSM ecosystem (see §4.1): the verbs below are
exactly the ones that recur across osmium, Overpass, the routing engines, the geocoders, and the
geometry kernels. The status column names a representative tool establishing each as a primitive.

| Layer | Role | Contract | Status — and the OSS tools that establish it |
|---|---|---|---|
| **Source** | region/file → queryable handle | `region → handle` | `CacheRegion` ✓; **per-category warm extracts** ✓ (`CombinedScan`/`ExtractCategory`, cached single pass); PostGIS source ◐; `Merge`/`Sort`/`ConvertFormat`/`ApplyChanges` ✗ (osmium, osmosis) |
| **Clip** | spatial subset of a region | `handle + bbox/polygon → handle` | `ClipByBBox`/`ClipByPolygon` ✓ (`osm.Clip`, osmium-extract → clipped `OSMCache`; osmconvert `-b`, ogr2ogr, Overpass bbox) |
| **Extract** | one category → GeoJSON | `handle + category → GeoJSON` | roads/amenities/places/parks/buildings ✓; uniform cached `ExtractCategory` ✓; routes/pois ✗ (osmium tags-filter) |
| **Filter** | narrow a GeoJSON by predicate | `GeoJSON + predicate → GeoJSON` | tag-exact ✓; tag-**prefix** ✓; **contains/regex** ✓; by-element-type ✓; radius ✓; `CompleteWays`/recurse ✗ (PBF-only — N/A at GeoJSON level) (osmfilter, Overpass has-kv / recurse) |
| **Transform / Analyze** | combine, reduce | `GeoJSON(s) → GeoJSON / scalar` | per-class stats ◐; `MergeLayers` / `Summarize` (subsumes `Count`) / `Dissolve` ✓ (`osm.Transform`, shapely); generic `Count` folded into `Summarize` (turf, shapely, PostGIS) |
| **Spatial** | relate geometries — the universal verb | `GeoJSON(s) → GeoJSON / scalar` | `WithinDistance`/`BeyondDistance`/`Nearest`/`SpatialJoin`/`Buffer`/`Intersect`/`Union`/`Centroid`/`Simplify` ✓ — **complete** (`osm.Spatial`, shapely STRtree + local AEQD) (Overpass around/area, turf, PostGIS `ST_*`, routing isochrone) |
| **Routing** | answer over the road network | `points + profile → route / matrix / area` | `Route`/`MultiStopRoute`/`Isochrone`/`Matrix`/`Nearest`/`MapMatch`/`Trip` ✓ across **five swappable engines** `osm.Routing.{OSRM,API,Valhalla,GraphHopper,PgRouting}` — **complete** (uniform `osm.Routing.Types` schemas; HTTP or SQL, all with graceful great-circle fallback) |
| **Network** (approx routing) | engine-free routing over a coarse graph | `edges → network`; `network + points → route / matrix` | `BuildNetwork`/`ApproxRoute`/`RouteMatrix` ✓ — **complete, all `effect=pure`** (`osm.Network`, shapely noding + networkx Dijkstra over a tiny MB-scale `osm/network/` cache artifact). No daemon, read-once-per-runner, embarrassingly parallel — proven live multi-server in Docker. Reports closest-reachable-point + `gap_to_b_km` on partial coverage. (NetworkX, the coarse/highway tier of pgRouting) |
| **Geocoding** | name/address ↔ coordinate | `query → coords` / `coords → address` | `ResolveRegion` ◐; `Geocode` / `ReverseGeocode` ✓ (`osm.geocode`, Nominatim HTTP — forward + reverse) (Photon, Pelias) |
| **Render / Tiles** | produce a viewable artifact | `GeoJSON(s) → artifact` | `RenderMap` ✓, `RenderLayers` ✓, `RenderStyledMap` ✓, `BuildVectorTiles` ✓ (`osm.Tiles`, tippecanoe → MBTiles/PMTiles) (Mapnik, OpenMapTiles) |

The biggest capability gaps, in priority order: the **Spatial** family (`WithinDistance`/`Nearest`/
`SpatialJoin`) — the single most universal verb across the ecosystem and the unlock for "food
deserts" or "schools without a pharmacy within 1 mile". The distance core of this family —
`WithinDistance`, its complement `BeyondDistance`, and `Nearest` — is now **shipped** (`osm.Spatial`,
see §9 item 2); `SpatialJoin`/`Buffer`/`Intersect` remain. **`Clip`** (cheap sub-region queries) is
also **shipped** (`osm.Clip`, §9 item 2). Then the two self-contained service families that turn our
extracts into answers, **Routing** and **Geocoding**; and **vector tiles** for scalable visualization.

### 4.1 Grounded in the OSS ecosystem

This taxonomy isn't invented — it's the distillation of what the mature open-source OSM programs
already do. Surveying them, the same handful of operations recur, and they fall cleanly onto the
layers above:

| Tool family | Programs | Operations they expose |
|---|---|---|
| Process / convert | [osmium-tool](https://osmcode.org/osmium-tool/manual.html), Osmosis, osmconvert/osmfilter, GDAL/ogr2ogr | [extract (bbox/polygon)](https://docs.osmcode.org/osmium/latest/osmium-extract.html), [tags-filter](https://docs.osmcode.org/osmium/latest/osmium-tags-filter.html), merge, sort, convert, apply diffs |
| Spatial query | [Overpass API / QL](https://wiki.openstreetmap.org/wiki/Overpass_API/Overpass_QL) | tag filter (has-kv), bbox, [`area`](https://dev.overpass-api.de/overpass-doc/en/full_data/area.html), `around` (radius), `recurse` (members/parents) |
| Routing | [OSRM](https://project-osrm.org/docs/v5.5.1/api/), Valhalla, GraphHopper, pgRouting ([overview](https://gis-ops.com/open-source-routing-engines-and-algorithms-an-overview/)) | route, table/matrix, nearest, map-match, isochrone, trip (TSP) |
| Geocoding | [Nominatim](https://github.com/osm-search/Nominatim), Photon, Pelias | forward geocode, reverse geocode, lookup |
| Render / tiles | Mapnik, [tippecanoe](https://github.com/mapbox/tippecanoe), [OpenMapTiles](https://openmaptiles.org/docs/generate/custom-vector-from-shapefile-geojson/) | GeoJSON→raster, GeoJSON→vector tiles (MBTiles), style, serve |
| Persist | [osm2pgsql](https://osm2pgsql.org/), imposm3 | import to PostGIS, schema mapping, keep-updated |
| Geometry kernel | GEOS, shapely, turf.js, PostGIS `ST_*` | buffer, centroid, simplify, area/length, within/contains, intersect/union, nearest |

The signal: a *small, stable* set of verbs underlies the whole ecosystem, and operations ranked by
how many independent tools implement them are the highest-value primitives. **Spatial
within-distance / nearest** appears in Overpass (`around`), every routing engine (as table /
isochrone), turf, and PostGIS — it is the universal verb, and our largest gap. **Clip by
bbox/polygon** appears in osmium, osmconvert, ogr2ogr, and Overpass. That cross-tool recurrence is
what justifies promoting these to first-class facets, and the survey adds three coherent service
families the original taxonomy didn't enumerate: Routing, Geocoding, and Tiles.

## 5. Contract conventions (the calling convention)

- **GeoJSON-path I/O.** Extract/Filter/Transform pass GeoJSON *file paths* (cache-backed), so
  steps chain and the runtime persists artifacts. Render consumes paths.
- **Tags preserved.** Filters and transforms keep OSM properties (`name`, `ref`, `amenity`, …) so
  downstream steps can filter further. (This is why the prefix filter could follow an extract.)
- **Typed schemas.** Every result is an FFL schema (`RoadFeatures`, `OSMFilteredFeatures`,
  `MapResult`, …) with `output_path` + `feature_count`, so the composer reasons about shapes.
- **One region per leaf; `foreach` to fan out.** A multi-region request is a `CollectX` workflow
  that `foreach`-es and a renderer that consumes the aggregated layers (the `FindBusinessMap`
  shape) — the runtime schedules the regions across servers.
- **Idempotent + cached.** Re-running a step with the same inputs hits the cache; primitives
  operate on cached category extracts, not the full PBF.

## 6. The discovery & semantics layer

NL → facet must be *reliable*, not guesswork. Two pieces:

1. **A capability index** — ✅ *shipped* (`facetwork/capabilities/`, MCP tool `fw_capabilities`).
   For every facet: a one-line purpose (from its doc comment), its typed params + returns, its
   namespace, whether it's an event facet, and a rendered signature — derived from the compiled FFL
   (`emit_dict` / a flow's stored `compiled_ast`), so it needs no separate registry. Exposed as the
   facet-level analogue of `fw_catalog_search`: `fw_capabilities(query, namespace, kind, source?)`
   ranks facets by intent over free text + param/return types. It indexes the deployed/seeded
   library by default, or a `source` snippet you're authoring. Proven over the 813-facet osm
   library: "dissolve features by group" → `osm.Transform.Dissolve`, "reverse geocode coordinates"
   → `osm.geocode.ReverseGeocode`, "buffer service area" → `osm.Spatial.Buffer`, namespace-filtered
   "within distance" → the `osm.Spatial` distance verbs. The index also carries **effect** (`pure` /
   `external`) and **cost** (`free`…`expensive`) per facet (§9 item 7), so `fw_capabilities(effect=,
   max_cost=)` can return e.g. only the pure/cheap primitives that are free to chain. It likewise
   carries **author** and **teams** ownership tags from the `with Author(email = …)` /
   `with Teams(names = […])` annotation mixins (same `with`-mixin mechanism as `Effect`/`Cost`, no
   new grammar). The runtime reads those mixins off the **entry workflow** when a run or catalog
   revision is created: the run is attributed to the author (resolved to a `User` record when one
   exists) and listed under each named team, and the dashboard filters runs and flows by team. See
   the canonical template `examples/canonical/11-author-teams.ffl`.
2. **A domain vocabulary (ontology)** — ✅ *shipped* (`osm.Vocab`, backed by
   `_osm_tools/vocab.py`). A curated OSM tag ontology — `amenity` / `shop` / `highway` / `tourism` /
   `leisure` / `natural` / `landuse` values, each with synonyms — exposed as `ResolveTag(term, key?)`
   → `(osm_key, osm_value, confidence, alternatives)` and `ListTagValues(key)`. This is what lets
   "find a pharmacy" map to `amenity=pharmacy` deterministically (confidence 1.0 on a canonical
   value, 0.9 on a synonym), and tells the composer whether a "business type" keys on `amenity` or
   `shop`. Proven: "gas station" → `amenity=fuel`, "grocery store" → `shop=supermarket`, "freeway"
   → `highway=motorway`, "coffee shop" → `amenity=cafe`, "gym" → `leisure=fitness_centre`; an
   unknown term resolves to confidence 0. The resolved `(osm_key, osm_value)` feeds straight into
   `ExtractCategory` + `FilterGeoJSONByOSMType`, and `ResolveTag` itself is discoverable via the
   capability index — the two halves close the loop.

Together these turn "compose a workflow" from pattern-matching on memorised tags into a
*lookup-then-compose*.

## 7. Worked decomposition

*"Show the food deserts in California"* (areas far from a supermarket) decomposes onto the target
library as:

```
foreach region in [California]:
    cache  = Source.Cache(region)
    shops  = Extract(cache, category="supermarket")      # shop=supermarket
    pop    = Extract(cache, category="population_places") # place=*
    far    = SpatialBeyondDistance(pop, shops, "1mi")     # population points > 1mi from any shop
    yield layers += [far]
RenderLayers(layers)
```

It needs exactly two primitives: complete category extraction (shipped, item 1) + a **spatial
within/beyond-distance** transform. As of item 2 both exist, so the decomposition is now
*expressible* — shipped as the parameterized `osm.Spatial.workflows.PlacesBeyondReach`
(`Cache → ExtractCategory(amenities) → Filter(tag=value) → ExtractCategory(population) →
BeyondDistance → RenderMap`), of which "food deserts" is the `shop=supermarket` instance and
"healthcare deserts" the `amenity=hospital` instance. The interstate example we shipped earlier
(`Source.Cache → Extract(roads,motorway) → Filter(ref prefix "I ") → Render`) already fit the
model — it just exposed that `Extract(roads)` and the prefix filter had to be built first.

## 8. Memory + reuse (the flywheel)

- **Reuse-first.** ✅ *shipped* (`fw_catalog_match` MCP tool → `CatalogService.match`). Before
  authoring, match the request against the catalog **by intent** — primarily each revision's
  recorded authoring `summary`, then title/tags/description/facets — and get a verdict: `reuse`
  (fill `best.param_schema` and run via `fw_catalog_run`), `review` (candidates exist — inspect),
  or `author_new` (no good match — author one, which then joins the catalog). Confidence is the
  share of request terms a candidate covers. The workflow-level analogue of `fw_capabilities`.
- **Parameterize for families.** One workflow should cover a request *family* (`FindBusinessMap`:
  name + type + regions → thousands of requests), not a single phrasing.
- **Grow from solved requests.** When composition hits a missing primitive, the run preflight
  names the gap → `fw ffl scaffold` scaffolds it → review → it *joins the library*. The
  library's growth is driven by real demand, and the catalog records which compositions recur,
  pointing at which primitives to harden next.

## 9. Roadmap (prioritized)

1. **Extraction complete + uniform + cheap** — ✅ *shipped*. Holes filled (amenities, places,
   parks, buildings, roads); the uniform cached `ExtractCategory` facade warms the cheap point
   categories in one cached single pass (heartbeat-safe on full-state PBFs), so a business query
   is seconds-then-instant instead of a 54-minute full-PBF filter. Proven end-to-end:
   `FindBusinessMapCheap` on California rendered **12,189 `fast_food` outlets**
   (`CacheRegion → ExtractCategory(amenities) → FilterGeoJSONByOSMType → RenderMap`), and the
   warm cache makes subsequent business queries on the region an instant lookup. The run also
   surfaced a real distributed-systems lesson: a multi-minute single pass must heartbeat (or
   register `timeout_ms=0`) or the task lease expires and it retries forever. The first warm was
   then made ~29× faster by pushing a C-level `KeyFilter` (union of the plugins' interest keys)
   into osmium for node-only scans — benchmarking showed the node-location index wasn't the cost,
   the per-element Python callback over ~99%-untagged nodes was (264 MB region: 347s → 12s, counts
   unchanged), extrapolating California's warm from ~26 min to ~1 min. Remaining: `routes`/`pois`.
2. **`Clip` + the `Spatial` family** — the distance core is ✅ *shipped*: `osm.Spatial` now exposes
   `WithinDistance` (keep subject features within *d* of any reference — Overpass `around` /
   PostGIS `ST_DWithin`), `BeyondDistance` (its complement — the "food desert" primitive), and
   `Nearest` (annotate each subject with its nearest reference distance — KNN / turf nearestPoint).
   Contract is uniform with the Filter layer (subject GeoJSON path + reference GeoJSON path +
   distance → GeoJSON path, tags preserved); distances are metric in a local azimuthal-equidistant
   projection centered on the reference centroid, indexed with a shapely `STRtree` (O(log n) per
   subject feature, subject streamed). Proven by deterministic unit tests against geodesic ground
   truth (within/beyond partition the subject exactly; nearest orders by distance; unit conversion;
   empty-reference edge), then **proven end-to-end on the live fleet**: the parameterized
   `osm.Spatial.workflows.PlacesBeyondReach` ran on California (PBF cache hit + a ~1.5-min
   `amenities` warm pass) as `CacheRegion → ExtractCategory(amenities) →
   FilterGeoJSONByOSMType(amenity=hospital) → ExtractCategory(population) → BeyondDistance(10 mi) →
   RenderMap`. It filtered **142 hospitals** (from 278,795 amenity features), related **4,992
   populated places** against them via the STRtree in ~1 s, and found **3,223 places (≈65%) beyond
   10 mi of the nearest hospital** — the rendered map (bounds spanning the whole state) flags exactly
   the rural places one expects (Buttonwillow, Desert Center). "Food deserts" is the same workflow
   with `tag_key="shop", tag_value="supermarket"` (verified live: 2,224 supermarkets → 4,075/4,992
   places beyond 1 mi).
   **`Clip` is also ✅ shipped**: `osm.Clip` exposes `ClipByBBox` / `ClipByPolygon`, wired to the
   `osmium extract` tool (`pbf-clips` cache_type with SHA-validity sidecars) and returning a new
   `OSMCache` over the clipped PBF — which composes with `ExtractCategory`/Spatial for free because
   every extractor reads `cache.path`. The blocking clip runs under a heartbeat pump so the lease
   survives a multi-minute extract. Proven live (`osm.Clip.workflows.ClipAndExtract`): clipping
   California (**1,311 MB**) to a Bay-Area bbox produced a **162 MB** PBF (**8× smaller**), then
   `ExtractCategory(amenities)` ran on the *clip* (77,377 features) — the "cheap sub-region query"
   that makes continental-scale spatial work tractable. (The live run also surfaced + fixed a latent
   bug in the previously-untested `pbf_clip` tool: `osmium extract` needs an explicit
   `--output-format pbf` because the staging filename hides the extension.)
   **`SpatialJoin` and `Buffer` complete the Spatial family** (also `osm.Spatial`): `SpatialJoin`
   attaches a reference layer's properties onto each subject feature by a topological predicate
   (`intersects`/`within`/`contains` — the point-in-polygon join, projection-invariant so it queries
   the WGS84 STRtree directly), with `how="inner"`/`"left"`; `Buffer` expands each feature by a
   distance into polygons (metric AEQD project → `buffer` → reproject) for service-area coverage.
   Both proven on real California data and **cross-validated** against `BeyondDistance`: buffering the
   142 hospitals by 10 mi and joining the 4,992 places `within` the coverage found **1,768 covered**;
   1,768 + 3,223 (beyond) = 4,991 of 4,992 — two independent implementations agreeing to a single
   boundary point. **Item 2's Spatial family is complete** — the set-ops `Intersect` (clip each
   subject geometry to a mask layer, ST_Intersection) and `Union` (aggregate-merge geometries into
   one feature, ST_Union — distinct from the per-group `Dissolve`) also shipped, proven on synthetic
   geometry (exact areas) + a real-data union of 4,075 CA place points. **`Centroid`** (feature →
   centroid Point) and **`Simplify`** (Douglas-Peucker at a metric tolerance, via AEQD) close out
   the family — proven by chaining on real data: buffering the 142 hospitals to circles (9,230
   vertices) then `Simplify`@1000 m cut them to 1,278 vertices (~7×) and `Centroid` reduced them to
   142 points. **The Spatial family is complete** (9 verbs).
3. **The missing filters/transforms** — ✅ *shipped*. The Filter layer gains `FilterGeoJSONByTagContains`
   (substring, case-insensitive by default) and `FilterGeoJSONByTagRegex` (`re.search`) in `osm.Filters`,
   completing the exact/prefix/contains/regex match family. A new `osm.Transform` namespace adds the
   generic combine/reduce verbs: `MergeLayers` (concatenate GeoJSON layers — the foreach-fanout
   aggregator), `Summarize` (count, optionally grouped by a tag, with count/sum/avg/min/max over a
   measure — subsumes the listed `Count`; writes a JSON breakdown), and `Dissolve` (union each group's
   geometries — PostGIS `ST_Union … GROUP BY` / turf dissolve). `CompleteWays`/recurse is **N/A** at the
   GeoJSON level (features are already complete; it's a PBF-only concept, available via `FilterByOSMType`'s
   `include_dependencies`). Proven on real California data, internally cross-consistent: `Summarize` of
   278,795 amenities → 448 categories in ~1 s (restaurant 21,170 + cafe 7,165); the regex filter
   `amenity ~ /^(cafe|restaurant)$/` returns exactly 28,335 = 21,170 + 7,165; `MergeLayers` of that with
   the 1,556 `name⊃"Starbucks"` matches = exactly 29,891; `Dissolve` of the 142 hospital-coverage circles
   → one MultiPolygon.
4. **The service families** — in progress. **`Geocoding` ✅ shipped**: `osm.geocode.Geocode`
   (forward — the facet was declared but had no handler) and the new `osm.geocode.ReverseGeocode`,
   both backed by a Nominatim HTTP client (`_osm_tools/geocode.py`) with a configurable endpoint
   (`AFL_NOMINATIM_URL` — point at a self-hosted instance for volume), a required User-Agent, and a
   polite ~1 req/s throttle for the public instance; results are the typed `GeoCoordinate`, cached on
   a synthetic key, and an unresolved query fails explicitly. Proven live against public Nominatim:
   "Golden Gate Bridge" → (37.820, −122.479), Google HQ → "Google Building 41", a clean
   forward→reverse round-trip, and reverse of Eiffel-Tower coords → "Avenue Gustave Eiffel, Paris".
   **`Routing` core was already present** (`osm.Routing.{API,OSRM}.Route` / `MultiStopRoute` /
   `Isochrone`, plus GraphHopper/Valhalla graph builders). **`BuildVectorTiles` is now ✅ shipped**
   (`osm.Tiles`): a path-based `build_from_geojson` over tippecanoe (→ MBTiles, → PMTiles when the
   pmtiles CLI is present), the scalable counterpart to `RenderMap` — tile any Extract/Filter/
   Transform output. Proven live: the 94.7 MB California amenities extract (278,795 features) → a
   23 MB PMTiles at z0–14 in ~12 s. **The OSRM-backed `Matrix` / `Nearest` / `MapMatch` verbs are now
   ✅ shipped** (`osm.Routing.OSRM`, on `/table` / `/nearest` / `/match`, each with a graceful
   great-circle fallback when OSRM is down — the `Route` philosophy). Proven live against an OSRM
   graph built from an `osm.Clip` Bay-Area extract (osrm-extract→partition→customize→routed):
   the 3×3 `Matrix` gave realistic drive times (SF→Oakland 20 min, SF→San Jose 60 min), `Nearest`
   snapped an SF point to "Market Street" 6 m away, and `MapMatch` snapped a 4-point trace to an
   82-vertex road-following line (`radiuses` loosens matching for noisy traces). **`Trip` (TSP) also
   shipped** (`/trip`): proven live optimizing a 5-stop Bay-Area tour (input
   SF→SanJose→Oakland→Fremont→Berkeley reordered to SF→SanJose→Fremont→Oakland→Berkeley, 178 km
   roundtrip). **Alternative query engines added**: `osm.Routing.Valhalla` (Route/Matrix/Isochrone,
   POST+JSON, polyline6-decoded) and `osm.Routing.GraphHopper` (Route/Isochrone, GET) — HTTP adapters
   that reuse the *same* `osm.Routing.Types` schemas as OSRM, so a workflow swaps engines by
   namespace alone (the source-adapter payoff). Each degrades to the great-circle estimate when its
   server is down; mocked-HTTP tests cover request shaping + response marshalling (a live run needs a
   running Valhalla/GraphHopper server — neither is trivially provisionable here, unlike the OSRM
   path which was proven live). **`pgRouting` also shipped** (`osm.Routing.PgRouting`) — the
   DB-backed engine: Route/Matrix/Isochrone via `pgr_dijkstra` / `pgr_dijkstraCostMatrix` /
   `pgr_drivingDistance` over an osm2pgrouting topology (points snapped to the nearest network
   vertex), again reusing the shared schemas with a great-circle fallback and mocked-DB tests. The
   Routing family now spans **five interchangeable engines** (OSRM, public API, Valhalla,
   GraphHopper, pgRouting) — no engine gaps remain.
5. **Build the discovery layer** — ✅ *shipped* (both halves). **The capability index**
   (`facetwork/capabilities/` + the `fw_capabilities` MCP tool, see §6.1): NL → *facet* lookup over
   the deployed library or an authored source snippet, proven over 813 osm facets. **The domain tag
   vocabulary** (`osm.Vocab.ResolveTag` / `ListTagValues`, see §6.2): NL → *tag* resolution
   (`"pharmacy"` → `amenity=pharmacy`) over a curated, synonym-rich OSM ontology. Together they turn
   compose-a-workflow into *lookup-then-compose*: `ResolveTag` fixes the `(key, value)`, the
   capability index finds the primitives that consume it (`ExtractCategory` →
   `FilterGeoJSONByOSMType` → render), and the catalog remembers the result.
6. **Reuse-first catalog matching** — ✅ *shipped*. `fw_catalog_match(request)` →
   `CatalogService.match`: intent-first matching (the authoring `summary` weighted highest) over the
   catalog, returning a `reuse` / `review` / `author_new` verdict plus the best candidate's intent +
   `param_schema` + confidence, so the composer fills params and runs an existing workflow instead of
   re-authoring. The workflow-level analogue of `fw_capabilities` (§6.1).
7. **Effect + cost annotations** — ✅ *shipped*. Facets carry effect/cost via the existing `with`-mixin
   syntax (no new grammar): `with Effect(kind = "pure"|"external"|"io")` and
   `with Cost(tier = "free"|"cheap"|"moderate"|"expensive")`; cost is also **inferred** from an
   existing `with Timeout(minutes = …)` (≥30 → expensive). The capability indexer parses these into
   `effect`/`cost` fields and `fw_capabilities` gains `effect=` / `max_cost=` filters, so the composer
   prefers pure/cheap primitives and sees which steps hit an engine. Proven on the osm library: the 9
   `osm.Spatial` + 3 `osm.Transform` verbs report `pure`/`cheap`; `ExtractCategory`/`Clip`/
   `BuildVectorTiles` report `external`/`expensive` (cost inferred from their `Timeout`); `osm.geocode`
   reports `external`/`cheap`. **All 247 osm event facets are now annotated** (100% coverage),
   classified by parameter type and namespace: PBF extractors / builds / imports / downloads →
   `external`/`expensive`; PostGIS + routing engines → `external`/`moderate`; GeoJSON filters / stats /
   Spatial / Transform → `pure`/`cheap`; loaders + render → `io`/`cheap`; vocab → `pure`/`free`.
   Explicit `with Effect`/`Cost` mixins remain the override for hand-tuning.

Each item is additive — it extends the typed/validated/distributed substrate, never relaxes it
— consistent with the thesis's design position. The end state: a complex OSM NL request is
*looked up or composed* from an orthogonal, discoverable primitive set, **remembered** as a
parameterized workflow, and **executed across the fleet** — and the library compounds with every
request it learns to satisfy.

**Status.** Items 1–5 are ✅ shipped and proven on real data — the orthogonal/cheap extraction
layer; the `Clip` + full `Spatial` (9 verbs) + `Transform` + `Filter` primitives; the service
families (`Geocoding`, `Routing` across five swappable engines, `BuildVectorTiles`); and the
discovery layer (the `fw_capabilities` facet index + the `osm.Vocab` tag ontology). The primitive
library has no capability or engine gaps left; the only remaining extract holes are `routes`/`pois`.
**Items 6 and 7 are now ✅ shipped too**: `fw_catalog_match` closes the composition loop (match an
NL request to an existing workflow by intent → else compose from the discoverable primitives → the
catalog remembers it), and effect/cost annotations (`with Effect`/`Cost` mixins, surfaced through
`fw_capabilities`) let the composer prefer pure/cheap primitives. **All seven roadmap items are
complete** — the orthogonal, complete, discoverable, distributed primitive library the design called
for is realized end to end, with the only loose ends being the `routes`/`pois` extract holes and
the (opt-in, additive) effect/cost tagging of the remaining facets.

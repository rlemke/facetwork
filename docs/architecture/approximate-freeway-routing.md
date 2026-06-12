# Approximate Freeway Routing (`osm.Network`)

**Status:** Phase 0 (scaffolded) — design + namespace stubs landed; algorithm core in Phase 1.
**Repo split:** facets/handlers live in [`fwh_osm`](https://github.com/rlemke/fwh_osm) under `osm.Network`; the cache layout contract lives in [`agent-spec/cache-layout.agent-spec.yaml`](../../agent-spec/cache-layout.agent-spec.yaml).

## 1. Why

The composable facet library already has full road routing via external engines
(`osm.Routing.OSRM|Valhalla|GraphHopper|pgRouting`). But that path has a hard
edge for continental, multi-server runs (see the "routes between all US cities
> 1M" analysis):

- The US graph **build** is the one heavy serial step — tens of GB RAM, hours,
  and a blocking subprocess.
- The **build-graph → running-engine lifecycle is not wired as facets**: the
  build facets emit a `GraphHopperCache`/`ValhallaCache` directory, but the
  query facets hit a *running* daemon at `AFL_OSRM_URL`. Nothing in the facet
  chain starts the engine from the built graph and points queries at it.
- Replicating a multi-GB graph across K routing hosts is real operational work.

**Approximate freeway routing** trades exactness for a tiny network. Restrict
the graph to interstate/freeway LineStrings (~tens of thousands of segments,
single-digit MB) and route by **pure, in-process graph search** — no daemon, no
GB-scale build, no replica lifecycle. The network becomes a small, content-
addressed cache artifact that every routing runner loads once into memory.

This is not a replacement for turn-by-turn routing; it answers "which corridor,
roughly how far, and can you even get there by freeway" — and it is
*embarrassingly* parallel.

## 2. The approach

Reduce routing to a small custom graph and three pure facets:

1. **`BuildNetwork`** — node the freeway LineStrings at intersections and build a
   routable graph, cached as a directory artifact.
2. **`ApproxRoute`** — snap A and B to the nearest network nodes, shortest path
   by segment length, return the route plus the **closest reachable point to B**
   (and the residual straight-line gap when B is off-network).
3. **`RouteMatrix`** — all-pairs over the small graph, one single-source Dijkstra
   per origin — no external engine.

The "stop at the closest reachable point to B" semantics is the honest output of
a graph search on a *partial* network: if B's area is not connected to A's
component, you reach the closest connected node and report `reached_b = false`
plus `gap_to_b_km`. This mirrors the `backend: estimate` graceful-degradation
already used elsewhere in the routing facets.

## 3. The artifact — how a network is stored

A new **`network` cache_type under the `osm` namespace** (adding a cache_type is
non-breaking per the cache-layout spec). The graph is a **directory artifact**:

```
cache/osm/network/north-america/us/interstates@tol25/      ← directory artifact
├── nodes.geojson      # graph vertices (Points): {node_id, lon, lat}
│                      #   = freeway endpoints + interchange junctions after noding
├── edges.geojson      # noded segments (LineStrings): {u, v, length_m, ref, name}
│                      #   one edge per node-to-node segment
└── graph.json         # adjacency for fast load:
                       #   {node_id: [[neighbor, length_m, edge_idx], ...]}
cache/osm/network/north-america/us/interstates@tol25.meta.json   ← SIBLING sidecar
```

Design choices:

- **`relative_path` mirrors the source** (the spec's `mirror_source_path`
  property), so lineage is visible in the path. National =
  `north-america/us/interstates`; per-state = `north-america/us/california/interstates`.
- **`graph.json` is the authoritative serialized form** — a plain, inspectable,
  language-neutral adjacency list keyed on node id, edge weight = segment length
  in meters. Any SDK can load it; Python rebuilds a `networkx` graph from it in
  milliseconds. (An optional `.pickle` may be cached for faster Python load, but
  `graph.json` stays authoritative.)
- **Build params that change the graph** (`snap_tolerance_m`, `ref_filter`) are
  encoded in the `relative_path` suffix (`@tol25`) so distinct tolerances are
  distinct cache entries that never collide.
- **The sidecar `extra` block carries build-quality metrics** — the way we catch
  bad noding (a fragmented graph → spurious "unreachable"):

  ```json
  "extra": {
    "node_count": 18432, "edge_count": 24117,
    "connected_components": 3, "largest_component_frac": 0.991,
    "snap_tolerance_m": 25.0, "ref_filter": "I ", "bbox": [...]
  }
  ```

- **Writes use the existing staging-then-atomic-rename directory protocol**
  (`finalize_dir_from_local`): build into staging, rename into `cache_root`,
  write the sibling sidecar last. Atomic publish guarantees no `ApproxRoute`
  runner ever observes a half-noded graph. Lock-free on distinct keys.
- **Hit/miss** = sidecar present + artifact exists (the standard read protocol).

## 4. How the network is shared between servers

The network rides the **sharing models the system already has** (see
`docs/operations/deployment.md`). Because the artifact is tiny, all of them work
without strain:

1. **HDFS backend (recommended for multi-server).**
   `AFL_CACHE_ROOT=hdfs://afl-hadoop-hdfs:8020/cache`. `BuildNetwork` writes the
   `network/` directory artifact once via WebHDFS; every routing runner on every
   server reads the same `cache/osm/network/.../graph.json`. No transfer logic to
   write — it is just a path.
2. **S3 / MinIO object store.** `AFL_STORAGE=s3`, `AFL_DATA_ROOT=s3://afl-cache`
   (+ `AFL_S3_ENDPOINT` for MinIO). The `network/` artifact and the merged
   layers land on the object store; runners localize `graph.json` once and route
   from memory. The tiny read-once artifact is ideal for HTTP/object fan-out;
   step payloads carry portable `s3://` URIs across the fleet. See
   [S3 / MinIO Integration](../operations/deployment.md#s3--minio-integration).
3. **Shared NFS mount of `AFL_DATA_ROOT`.** Identical absolute path on all
   servers; build-once, read-everywhere, lock-free on distinct keys.
4. **Independent per-server local caches.** On a cache miss a runner re-derives —
   and here the small size pays off: re-noding the interstate extract is seconds,
   versus an OSRM US graph build of hours. (Optional future plumbing: a lazy
   content-addressed *pull* verified against the sidecar `sha256`. Not on the
   critical path — HDFS/NFS already covers the multi-server case.)

**The key cross-server property:** `ApproxRoute`/`RouteMatrix` are pure and the
graph is **read-once**. Each routing runner loads `graph.json` into an in-process
`networkx` object on first use (a module-level cache keyed by `network_path` +
sidecar `sha256`), then answers all subsequent routing tasks from memory. The
cross-server cost is "read a few MB once per runner process," not per query —
which is exactly what makes this fan out far better than a central engine. There
is **no daemon, no `AFL_*_URL`, no replica lifecycle** to manage; the "engine" is
a serialized graph file in the normal cache.

## 5. Facets

```ffl
namespace osm.Network {

    schema NetworkResult {
        network_path: String,           // cache directory artifact
        node_count: Long,
        edge_count: Long,
        connected_components: Long,
        largest_component_frac: Double,
        snap_tolerance_m: Double,
        extraction_date: String
    }

    schema ApproxRouteResult {
        route_path: String,             // GeoJSON LineString of the traversed segments
        distance_km: Double,
        reached_lat: Double,            // closest reachable point to B
        reached_lon: Double,
        gap_to_b_km: Double,            // straight-line residual when B is off-network
        reached_b: Boolean,
        node_hops: Long,
        extraction_date: String
    }

    schema RouteMatrixResult {
        result_path: String,            // JSON: pairs[] of {from, to, distance_km, reached_b}
        pair_count: Long,
        reachable_count: Long,
        extraction_date: String
    }

    /** Node freeway LineStrings at intersections and build a routable graph
        (shapely unary_union to node + networkx adjacency, weight = segment
        length). Output is a cached directory artifact. */
    event facet BuildNetwork(edges_path: String, snap_tolerance_m: Double = 25.0, ref_filter: String = "") => (result: NetworkResult) with Effect(kind = "pure") with Cost(tier = "moderate")

    /** Approximate route over a freeway network: snap A and B to the nearest
        network nodes, shortest path by segment length, and return the route plus
        the closest reachable point to B (and the residual gap when B is off-net). */
    event facet ApproxRoute(network_path: String, from_lat: Double, from_lon: Double, to_lat: Double, to_lon: Double) => (result: ApproxRouteResult) with Effect(kind = "pure") with Cost(tier = "cheap")

    /** All-pairs approximate routing over the small network — pure in-process,
        no engine. `points` is a JSON list of {lon, lat, name}. */
    event facet RouteMatrix(network_path: String, points: String) => (result: RouteMatrixResult) with Effect(kind = "pure") with Cost(tier = "cheap")
}
```

All three are `Effect(kind="pure")` — no external service — which is what tells
the planner/scheduler they parallelize freely. `BuildNetwork` is `moderate` (one
noding pass); the routing verbs are `cheap`.

## 6. Algorithm internals

- **BuildNetwork** has two connectivity modes; it auto-selects by peeking the
  first input feature:
  - **Node-ID topology (primary, recommended).** When the extractor preserved
    each way's OSM node-id sequence (`properties.node_ids` — `ExtractRoads` emits
    it), `node_id_graph()` builds the graph on **shared OSM node IDs**: two ways
    that reference the *same* node id connect, and only then — exactly how
    OSRM/pgRouting/Valhalla build their graphs. Degree-2 interior nodes are
    contracted so each edge spans junction→junction. **Zero tolerance, no
    heuristics.** Because OSM node ids are global, separate per-region PBF
    extracts auto-stitch at shared border nodes. Cached under a `@nodeid` key.
  - **Coordinate-snap noding (fallback, legacy).** When `node_ids` are absent,
    `node_linestrings()` falls back to `shapely.ops.unary_union(lines)` (node at
    crossings) + merge endpoints within `snap_tolerance_m`. This *reconstructs*
    topology from geometry and is lossy — see §10. Cached under `@tol<N>`.
  - Both emit the same `nodes.geojson` / `edges.geojson` / `graph.json` artifact
    and a `networkx.Graph` (weight = `length_m`); the sidecar records the
    `topology` (`node_id` | `coord_snap`) and `connected_components`.
- **ApproxRoute**: snap A and B to the nearest network node (shapely
  `nearest_points` + the projected entry via `line.project`/`interpolate`);
  `networkx.dijkstra_path` by length. If B's nearest node is unreachable from
  A's component, return the reachable node with min great-circle distance to B,
  set `reached_b = false`, and report `gap_to_b_km`. Output the traversed
  segments as one `route_path` LineString.
- **RouteMatrix**: load the graph once, run single-source Dijkstra from each
  source node, fill the N×N matrix — O(N · E log V) on a tiny E.

## 7. Distribution / parallelization

- **Build phase fans out per region.** `andThen foreach state` →
  `ExtractCategory(roads)` → `FilterGeoJSONByOSMType(motorway)` →
  `FilterGeoJSONByTagPrefix(ref, "I ")`, then `MergeLayers` (the
  yield-aggregation sync point) → **one** `BuildNetwork` over the national set.
  Cheap and short.
- **Routing phase fans out per pair / per source city.**
  `andThen foreach pair … ApproxRoute(network, a, b)` — each leaf is `pure/cheap`,
  claimed across the fleet by the lock-free protocol, each runner serving from
  its in-memory graph. No engine contention, no central server.
- **Task-list isolation:** the routing flood lands on its own queue
  automatically — `osm.Network.*` facets route to the `osm` namespace list
  (task lists are derived from the facet namespace), so a big all-pairs job is
  isolated to runners serving that namespace and does not starve other work.
- **Sync points:** `MergeLayers` (gather per-region interstates) and the final
  matrix assembly (`RouteMatrix`, or aggregating per-pair yields).

## 8. Phasing & milestones

- **Phase 0 ✅** — design doc, `osm.Network` namespace scaffold (validator-clean
  FFL + handler wiring + `network_ops` signatures + tests), `networkx`
  dependency, `network` cache_type registered in the spec.
- **Phase 1 ✅** — `BuildNetwork` (noding + adjacency + cache write).
  Live-proven on the real California PBF: 13,230 nodes / 14,507 edges in 1.3 s,
  8.2 MB artifact, 9 ms cache hit; 99.4 % in one connected component spanning
  the state (the 7 small components are benign ramp-less spurs).
- **Phase 2 ✅** — `ApproxRoute` (snap + Dijkstra + closest-reachable-point).
  Live: SF→LA **613.6 km** (≈615 km real I-5), LA→SD 192.6 km, SF→Sac 141.9 km,
  all 45–248 ms; SF→Yosemite honestly reports a 143 km off-freeway gap.
- **Phase 3 ✅** — `RouteMatrix` (all-pairs) + the `CityRoutesByPopulation`
  workflow + the GeoJSON→waypoints adapter. Live capstone: the 3 real CA cities
  ≥1M (LA/SD/SJ) routed end-to-end (LA↔SD 193, LA↔SJ 611, SD↔SJ 799 km).
- **Phase 4 ✅** — multi-server proof, **run live in Docker**
  ([`docker-compose.network-phase4.yml`](../../docker-compose.network-phase4.yml)):
  containerized MongoDB + 2 independent `route-runner` instances sharing one
  prebuilt network artifact via a volume. The `RouteFanout` workflow fanned out
  21 city-pair routes; the runners claimed the tasks **lock-free, 22/20 across
  the two instances** (coordinating only through Mongo), each **loaded the shared
  network once** (read-once-per-runner), and the results were **identical to the
  single-host run**. Confirms the cross-server sharing model of §4 end to end.
- **Phase 5** — docs/CLAUDE.md catalog, capability-index + catalog entries,
  lessons-learned.

## 9. Testing

Unit (noding: known intersections → shared nodes; component count), golden (CA
SF→LA distance band), degradation (off-net B), determinism (`RouteMatrix` ==
sequential `ApproxRoute`), cache (sidecar shape, atomic publish, hit/miss,
distinct-tolerance keys do not collide), parallel (fleet matrix == single-runner
matrix).

## 10. Risks / limitations

- **Noding tolerance** *(coordinate-snap mode only)* is the main accuracy lever
  there — too tight fragments the graph, too loose falsely joins grade-separated
  crossings. **The node-ID topology mode removes this lever entirely** (exact
  shared-node connectivity, no tolerance; overpasses that cross in 2-D but share
  no node correctly stay disconnected). Prefer node-ID; the European run (§12)
  showed coordinate-snap plateaus and bridging heuristics don't recover it.
- **Class-downgrade / unmapped-road gaps** *(both modes)*: node-ID can't invent
  road that isn't in the extract. Where a through-route drops from `motorway` to
  `trunk` (e.g. the ~10 km D-100/E80 *trunk* stretch across the Turkey↔Bulgaria
  Kapıkule border), a motorway-only network breaks there. Fix = extract `trunk`
  too (a `motorway+trunk` network) — proven to reconnect İstanbul↔Sofia — at the
  cost of a larger graph and "major through-road" rather than strict-freeway
  semantics. Genuinely sea-separated nodes (UK across the Channel) are
  unbridgeable by any topology.
- **Directionality / dual carriageways:** interstates are often two parallel
  ways; the graph is undirected (approximate by design). Acceptable for
  corridor-level distance; documented as a limitation.
- **OSM `ref` tagging inconsistency** ("I 5" vs "I-5" vs on the relation) —
  `ref_filter` + a vocab-backed normalization; reuse the proven CA filter.
- **Approximation:** freeways only → no surface-street first/last mile; ignores
  ramps and turn restrictions. Good for corridor / rough drive distance, not
  turn-by-turn.

## 11. Relationship to the rest of the library

Approximate routing is a *tier*: it can carry the long-haul middle of a
continental route while full OSRM handles first/last mile — roughly how
production routers do continental queries. As a standalone, it is a clean, pure,
massively-parallel primitive that the library absorbs with just `BuildNetwork` +
`ApproxRoute` (+ `RouteMatrix`), discoverable via `fw_capabilities`
(`effect=pure`) like the rest of the Spatial/Transform layers.

## 12. Production findings (US + Europe)

- **US interstates — works cleanly.** A single connected interstate network;
  SF→LA 613.6 km matches reality. The approach shines when the target network is
  one well-mapped, continuously-`motorway` component.
- **Europe exposed coordinate-snap's ceiling.** Building the 45-country map by
  per-country `motorway` extract + **coordinate-snap** left the network in ~400
  components: the European giant component dead-ended at Niš while Sofia sat in a
  611-node island, so Belgrade↔Sofia was unreachable — despite the A4/E80 being
  tagged `motorway` end to end. The break was a **26 m gap a 25 m tolerance
  missed by one metre.** Raising tolerance plateaued (~17%→29% reachable) and
  *neither* ref-aware nor proximity dead-end bridging recovered city reachability
  (+0 points): you can't reliably rebuild graph topology from geometry once the
  node IDs are gone.
- **Node-ID topology fixed it.** Rebuilding on shared OSM node IDs (§6)
  reconnected Belgrade↔Sofia at an accurate **382 km**, grew the giant component
  to ~472k nodes, and stitched per-country extracts at shared border nodes for
  free. European city-pair reachability rose 17% → **27%**, and **all of
  Turkey's 19 cities** formed one connected cluster (vs. shattered under
  coordinate-snap).
- **The residual is class-downgrade, not topology.** What still caps Europe at
  27% is genuine: Turkey's cluster reaches Europe only via the **trunk**-class
  Kapıkule border stretch (§10), and the UK is sea-isolated. A `motorway+trunk`
  network connects İstanbul↔Sofia (proven) and would lift reachability further,
  at the cost of a larger, "major-road" (not strict-freeway) graph.
- **Takeaway:** prefer the node-ID topology mode for any multi-region build;
  treat `snap_tolerance_m` as a legacy fallback. Decide `motorway` vs
  `motorway+trunk` by whether you want strict freeways or major-city
  connectivity.

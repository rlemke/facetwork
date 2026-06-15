# sentinel2-landchange

A Facetwork pipeline that detects **land-cover change from Sentinel-2 imagery**
between two time windows over an area of interest (AOI), and renders the result
as an interactive map. Built on open data and open algorithms; designed to show
off Facetwork's per-scene fan-out, content-addressed caching, and source-adapter
shape.

> **Scaffold status.** The FFL, handlers, tool library, cache protocol, tests,
> and an offline **mock** path are complete and green. The two heavy lifts — the
> real STAC query and the real COG raster math — are stubbed with clear TODOs and
> raise `NotImplementedError` until implemented (run with `use_mock=true` for
> now). See *Implementing the real path* below.

## What it does

For each of a **baseline** and a **recent** date window:

1. **`s2.source.SearchScenes`** — query a STAC catalog for Sentinel-2 L2A scenes
   intersecting the AOI under a cloud-cover ceiling → a list of scene ids.
2. **`s2.scan.ScanScenes`** — `andThen foreach` fan-out: one parallel step per
   scene calling **`s2.source.FetchSceneIndex`**, which window-reads the bands
   (COG range requests), computes a spectral index (NDVI/NDWI/NDBI), and caches
   the AOI-clipped raster.
3. **`s2.analyze.Composite`** — reduce the cached per-scene rasters into one
   cloud-robust median composite for the epoch.

Then **`s2.analyze.DetectChange`** compares the two composites (index-difference
+ threshold, or a random-forest classify) and **`s2.render.ChangeMap`** renders a
MapLibre HTML viewer (loss in red, gain in green). The entry workflow is
**`s2.workflows.AnalyzeAOI`**.

Every scene raster and composite is content-addressed in the cache
(`$AFL_CACHE_ROOT/s2/`), so changing the threshold, the change method, or adding
a third epoch re-uses everything already fetched — the expensive imagery I/O
happens once.

## Open data & algorithms

- **Data:** Sentinel-2 L2A, free, via the Element84 Earth Search STAC API
  (`https://earth-search.aws.element84.com/v1`) over AWS Open Data. No key.
- **Algorithms:** NDVI/NDWI/NDBI spectral indices; median compositing;
  index-difference change detection; (optional) random-forest land-cover classify.

## Run it (offline mock)

```bash
# the mock path needs no network, GDAL, or extra deps
pytest examples/sentinel2-landchange/tests/ -q

# or via the runtime (FFL-first):
scripts/start-runner --example sentinel2-landchange -- --log-format text
scripts/ffl-run examples/sentinel2-landchange/ffl/sentinel2_landchange.ffl \
  --workflow s2.workflows.AnalyzeAOI \
  --inputs '{"use_mock": true}' --task-list s2
```

The run appears in the dashboard's **Runs** list; the per-scene `FetchSceneIndex`
steps fan out under `ScanScenes`, and the final `AnalyzeAOI` yields the path to
the rendered `index.html`.

## Implementing the real path

Two stubs to fill (signatures + cache keys + return shapes already fixed, so
handlers and FFL don't change):

- **`tools/_s2_tools/stac.py` → `_search_real`** — query `{stac_url}/search`
  (bbox + datetime + `eo:cloud_cover<max`), paginate, project items to scene
  dicts with COG band hrefs. Deps: `requests` (or `pystac-client`).
- **`tools/_s2_tools/raster.py` → `_fetch_real`** (and real `composite` /
  `detect_change`) — window-read COG bands for the AOI, compute the index, write
  a Cloud-Optimized GeoTIFF, median-composite and difference with numpy. Deps:
  `rasterio` + `rio-tiler` (or GDAL) + `numpy`. For `render`, turn the change COG
  into web tiles (rio-tiler / gdal2tiles / titiler) and point the MapLibre source
  at them.

Keep the tools runtime-free (stdlib + domain libs only), per
`agent-spec/tools-pattern.agent-spec.yaml`.

## Layout

```
ffl/sentinel2_landchange.ffl   the namespaces, schemas, facets, workflows (validator-clean)
handlers/                      thin dispatchers (source / analyze / render) + shared/ shim
tools/_s2_tools/               shared library: stac, raster, map_render, sidecar, storage, mocks
tools/search_scenes.py(.sh)    reference CLI (others follow the same pattern)
tests/                         FFL compile + offline mock end-to-end + handler dispatch
runner.env                     task-list / timeout tuning (namespace `s2`)
```

To promote this to a production, pip-installable package (`fwh_sentinel2`),
scaffold from `example-template/` and move `ffl/` + `handlers/` + `tools/` under
`src/sentinel2/`, adding the `facetwork.examples` entry point — the same shape as
`fwh_osm` / `fwh_save_earth`.

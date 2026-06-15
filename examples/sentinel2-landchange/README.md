# sentinel2-landchange

A Facetwork pipeline that detects **land-cover change from Sentinel-2 imagery**
between two time windows over an area of interest (AOI), and renders the result
as an interactive map. Built on open data and open algorithms; designed to show
off Facetwork's per-scene fan-out, content-addressed caching, and source-adapter
shape.

> **Status.** Real path **implemented and verified live**: the real STAC search
> (`requests`) and the real COG window-read + NDVI/NDWI/NDBI (`rio-tiler` +
> `numpy`) both work against Element84 Earth Search + the public Sentinel-2 COGs.
> The composite → change → render chain is shared numpy code (one path for real
> and mock). An offline **mock** path (`use_mock=true`) runs the whole chain with
> no network/GDAL — that's what the default test suite exercises; a live STAC
> test is opt-in (`S2_LIVE=1`).

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

Then **`s2.analyze.DetectChange`** compares the two composites — `method`:
- **`difference`** — index delta thresholded into loss / stable / gain.
- **`classify`** — bin each epoch into land-cover classes (water / built-bare /
  sparse-veg / dense-veg by NDVI) and report the per-pixel class transition;
  `class_counts` then carries the per-class histograms and the from→to transition
  matrix (e.g. `built_bare→water: 33`). An interpretable threshold classifier; a
  trained random-forest over the full spectral stack is the drop-in upgrade.

Both emit the same loss(-1)/stable(0)/gain(+1) raster, so **`s2.render.ChangeMap`**
(MapLibre tiled map, loss red / gain green) is method-agnostic. The entry
workflow is **`s2.workflows.AnalyzeAOI`** (`method="classify"` to switch).

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

## Real run

Install the geospatial deps and drop `use_mock`:

```bash
pip install rio-tiler requests        # rio-tiler pulls rasterio; requests for STAC
S2_LIVE=1 pytest examples/sentinel2-landchange/tests/ -q -k live   # opt-in live STAC check

scripts/start-runner --example sentinel2-landchange -- --log-format text
scripts/ffl-run examples/sentinel2-landchange/ffl/sentinel2_landchange.ffl \
  --workflow s2.workflows.AnalyzeAOI \
  --inputs '{"aoi":"-122.46,37.76,-122.44,37.78","use_mock":false}' --task-list s2
```

How the real path works (all runtime-free — stdlib + domain libs only, per
`agent-spec/tools-pattern.agent-spec.yaml`):

- **`_s2_tools/stac.py`** — `search` POSTs to `{stac_url}/search` (bbox + datetime
  + `eo:cloud_cover<max`, paginated); `get_item_assets` resolves a scene's band
  COG hrefs. `requests` only.
- **`_s2_tools/raster.py`** — `_fetch_real` window-reads the index bands for the
  AOI with `rio-tiler` (reprojected to a fixed grid so scenes/epochs align) and
  computes the normalized-difference index; `composite`/`detect_change` are
  shared `numpy` (median stack, then thresholded delta). Rasters are cached as
  `.npz` (array + bounds + CRS).

- **`_s2_tools/map_render.py`** — `ChangeMap` colorizes the change raster to a
  georeferenced RGBA `change.tif`, slices it into an **XYZ PNG tile pyramid**
  (reprojected to Web Mercator via `rio-tiler` + `morecantile`), and writes a
  **MapLibre GL** viewer that loads `tiles/{z}/{x}/{y}.png` over a CARTO basemap.
  Zoom range is matched to the AOI + raster resolution, and the tile count is
  capped (logged if hit). When the geo stack is absent it degrades to the
  self-contained canvas view, so the offline mock still renders.

Bigger AOIs / longer windows just mean more `FetchSceneIndex` steps fanned out by
`ScanScenes` — each cached once, so re-runs are cheap.

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

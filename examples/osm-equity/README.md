# osm-equity — Digital Divide & OSM Mapping Equity

A 5-phase FFL study of how socioeconomic disparity shapes the completeness/
quality of OpenStreetMap data, at census-tract scale. The FFL **is** the
methodology; the runtime schedules it by data flow.

```
ffl/osm_equity.ffl        the workflow (namespace osm.equity)
handlers/                 event-facet implementations (real geometry + stats)
  shared/equity_utils.py    computation + deterministic offline data source
  design/ acquisition/ metrics/ analysis/ reporting/
tests/test_osm_equity.py  FFL-compile + unit + end-to-end signal recovery
```

## Phases → FFL

| Phase | Plan section | FFL |
|-------|--------------|-----|
| 1 | Study design | `DefineStudyArea` + workflow params |
| 2 | Data acquisition | `ResolveStudyTracts`, `FetchRegionOSM`, `FetchRegionFootprints` (region-level, **update-gated**) |
| 3 | Metric calculation | per-tract `foreach`: `ClipTractOSM` (local), `FetchCensusEquity`, `ComputeAttributeQuality` (intrinsic), `ComputeExtrinsicQuality` (extrinsic, gated) |
| 4 | Statistical & spatial | `SpearmanCorrelation`, `MoransI`, `GeographicallyWeightedRegression`, `TemporalEvolution` — run concurrently over the full tract set |
| 5 | Actionable reporting | `BuildEquityReport` → interactive MapLibre heat map + HTML findings report + ranked data deserts |

## Design choices (folded-in improvements)

1. **Fetch OSM once, clip per tract locally.** Region OSM is pulled a single
   time and each tract is obtained by a local geometry clip — no per-tract
   remote calls (Overpass/ohsome rate limits). `ClipTractOSM` is pure-local.
2. **Update-gated fetch.** `FetchRegionOSM` / `FetchRegionFootprints` reuse the
   cached extract and **only re-fetch when `update = true`**.
3. **Geometry carried into Phase 4** so Moran's I / GWR build a real spatial-
   weights matrix (`TractQuality.geometry_wkt`).
4. **POI diversity measured** (Shannon entropy) and correlated with income.
5. **Extrinsic benchmark is pluggable + gated:** `ExtrinsicQuality.has_reference`
   is `false` where no authoritative footprint layer exists — metrics are an
   explicit N/A sentinel (`-1.0`), never silently zeroed.
6. **Provenance:** `acs_year` param + a content-derived OSM `snapshot` for
   reproducible re-runs.

## Data source

Handlers do **real** geometry (shapely) and statistics (scipy Spearman, a
pure-numpy permutation Moran's I, a Gaussian-kernel local GWR). The data source
is selected by `FW_EQUITY_SOURCE`:

- **`offline`** (default) — a deterministic generator tiles the region into a
  grid of synthetic tracts whose income gradient drives OSM richness, so the
  pipeline runs with **no network** and the analysis recovers a known signal
  (used by the test suite).
- **`real`** — live sources (`handlers/shared/sources_real.py`): Census
  **TIGERweb** tract polygons, the Census **ACS 5-year API** (`CENSUS_API_KEY`),
  and **OSM via Overpass** (buildings/highways/amenities + edit metadata).
  Realistic at **city/metro scale**. The extrinsic footprint benchmark isn't
  wired, so real mode honestly reports `has_reference=false` (gated).

```bash
# real run (needs network + CENSUS_API_KEY):
FW_EQUITY_SOURCE=real python -c "..."   # or a runner with the env set
```

## Run the tests

```bash
pytest examples/osm-equity/tests -q
```

The end-to-end test drives every handler in the workflow's data order and
asserts the built-in digital-divide signal is recovered (Spearman ρ > 0.3,
p < 0.05; Moran's I > 0) and that data deserts are the low-income tracts.
Set `FW_EQUITY_GRID` to change the grid resolution (default 6 → 36 tracts).

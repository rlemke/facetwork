# sentinel2-landchange tools

CLI surface over `_s2_tools/` — the same functions the FFL handlers call, so the
terminal and the runtime share one cache (`$AFL_CACHE_ROOT/s2/`) and one code path.

```
search_scenes.py   STAC search → {count, scene_ids}              [shipped]
fetch_scene_index  one scene → cached AOI-clipped index raster   [TODO CLI; handler + lib done]
composite          epoch median composite over cached scenes     [TODO CLI; handler + lib done]
detect_change      baseline vs recent → change raster + stats     [TODO CLI; handler + lib done]
render_change_map  change raster → MapLibre HTML + tiles          [TODO CLI; handler + lib done]
```

Each CLI is one `<name>.py` (argparse, stdout=JSON, stderr=logs, `main() -> int`)
plus a thin `<name>.sh` wrapper, per `agent-spec/tools-pattern.agent-spec.yaml`.
`search_scenes` is the shipped reference; the rest wrap the corresponding
`_s2_tools` function the same way (the library and FFL handlers already exist —
only the CLI shell is pending).

Pipe example (offline):

```bash
# note: use --aoi=... (leading-minus bbox would otherwise look like a flag)
python tools/search_scenes.py --aoi=-122.55,37.70,-122.35,37.85 \
  --from 2024-06-01 --to 2024-09-30 --use-mock
```

`--use-mock` runs against deterministic fixtures (`_s2_tools/s2_mocks.py`) — no
network, no GDAL. Drop it to hit the real path (real STAC search + COG read are
implemented in `_s2_tools/stac.py` and `_s2_tools/raster.py`); that needs
`pip install rio-tiler requests`.

"""Render a change raster as a real web map.

Preferred path (when rasterio/rio-tiler are installed): colorize the change
raster to a georeferenced RGBA GeoTIFF, slice it into an XYZ PNG tile pyramid
(reprojected to Web Mercator), and emit a MapLibre GL viewer that loads those
tiles over a basemap. Output bundle::

    output/s2/<aoi_key>/
      index.html            MapLibre viewer (basemap + change raster overlay)
      change.tif            georeferenced RGBA COG (downloadable)
      tiles/{z}/{x}/{y}.png XYZ pyramid

Fallback path (no rasterio/rio-tiler — e.g. the offline mock test env without
the geo stack): a self-contained HTML that paints the change grid to a canvas.
Either way the title contains "land-cover change" and the bundle has the same
shape, so callers/handlers don't change.
"""

from __future__ import annotations

import json
import math
import os
from typing import Any

from _s2_tools import raster, storage

# Categorical colors (loss / gain). Stable + nodata render transparent.
_COLORS = {-1: (215, 48, 39), 1: (26, 152, 80)}

# CARTO Voyager raster basemap — free, no key, works from file://; MapLibre needs
# the subdomains expanded (it doesn't interpolate {s}).
_BASEMAP = [
    f"https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{{z}}/{{x}}/{{y}}.png"
    for s in ("a", "b", "c", "d")
]
_BASEMAP_ATTR = "&copy; OpenStreetMap &copy; CARTO"

_MAX_TILES = 600  # safety cap on the pyramid (logged if hit)


def render_change_map(
    change_rel: str, aoi_key: str, *, title: str = "Sentinel-2 land-cover change",
    basemap_url: str = "", detail: str = ""
) -> dict[str, Any]:
    out_dir = storage.join(storage.output_root(), "s2", aoi_key)
    tiles_dir = storage.join(out_dir, "tiles")
    os.makedirs(tiles_dir, exist_ok=True)
    html_path = storage.join(out_dir, "index.html")

    tiled = _render_tiles(change_rel, out_dir, tiles_dir)
    if tiled is not None:
        html = _maplibre_html(title, detail or change_rel, tiled, basemap_url)
    else:
        # No geo stack — fall back to the self-contained canvas view.
        html = _canvas_html(title, detail or change_rel, raster.load_change_grid(change_rel))

    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)
    return {"aoi_key": aoi_key, "output_dir": out_dir, "html_path": html_path,
            "tiles_path": tiles_dir}


# ── real tiles ──────────────────────────────────────────────────────────────────


def _render_tiles(change_rel: str, out_dir: str, tiles_dir: str) -> dict[str, Any] | None:
    """Write change.tif + an XYZ pyramid. Return tile metadata, or None if the
    geo stack is unavailable (caller falls back to the canvas view)."""
    try:
        import morecantile
        import numpy as np
        import rasterio
        from rasterio.transform import from_bounds
        from rio_tiler.io import Reader
    except ImportError:
        return None

    arr, bounds, _crs = raster._load(raster.CHANGE, change_rel)
    west, south, east, north = (float(b) for b in bounds)
    h, w = arr.shape

    # int8 change grid -> 3-band RGB + a visibility mask (stable/nodata transparent).
    rgb = np.zeros((3, h, w), dtype="uint8")
    mask = np.zeros((h, w), dtype="uint8")
    for val, (r, g, b) in _COLORS.items():
        sel = arr == val
        rgb[0][sel], rgb[1][sel], rgb[2][sel] = r, g, b
        mask[sel] = 255

    cog_path = storage.join(out_dir, "change.tif")
    transform = from_bounds(west, south, east, north, w, h)
    profile = dict(driver="GTiff", height=h, width=w, count=3, dtype="uint8",
                   crs="EPSG:4326", transform=transform, photometric="RGB",
                   compress="deflate", tiled=True, blockxsize=256, blockysize=256)
    with rasterio.open(cog_path, "w", **profile) as dst:
        dst.write(rgb)
        dst.write_mask(mask)

    min_zoom, max_zoom = _zoom_range(west, south, east, north, max(w, h))
    tms = morecantile.tms.get("WebMercatorQuad")
    count = 0
    capped = False
    with Reader(cog_path, tms=tms) as r:
        for z in range(min_zoom, max_zoom + 1):
            if capped:
                break
            for t in tms.tiles(west, south, east, north, [z]):
                if count >= _MAX_TILES:
                    capped = True
                    break
                try:
                    img = r.tile(t.x, t.y, t.z)
                except Exception:
                    continue  # tile fully outside data
                png = img.render(img_format="PNG", add_mask=True)
                tdir = storage.join(tiles_dir, str(t.z), str(t.x))
                os.makedirs(tdir, exist_ok=True)
                with open(storage.join(tdir, f"{t.y}.png"), "wb") as f:
                    f.write(png)
                count += 1
    if capped:
        # No silent caps — say what was dropped.
        print(f"[map_render] tile cap {_MAX_TILES} hit at zoom {max_zoom}; "
              f"higher-zoom tiles skipped", flush=True)
    return {"min_zoom": min_zoom, "max_zoom": max_zoom, "tile_count": count,
            "bounds": [west, south, east, north], "cog_path": cog_path}


def _zoom_range(west, south, east, north, px: int) -> tuple[int, int]:
    """Pick a min zoom that fits the AOI in ~1 tile and a max zoom matched to the
    raster's native resolution (so we don't oversample a 16x16 mock to z18)."""
    span = max(east - west, 1e-6)
    min_zoom = max(0, int(math.floor(math.log2(360.0 / span))))
    res_levels = max(1, math.ceil(math.log2(max(px, 1) / 256.0)) + 2)
    max_zoom = min(18, min_zoom + max(2, res_levels))
    return min_zoom, max_zoom


# ── HTML templates ──────────────────────────────────────────────────────────────


def _maplibre_html(title: str, detail: str, tiled: dict[str, Any], basemap_url: str) -> str:
    basemap = [basemap_url] if basemap_url else _BASEMAP
    cfg = {
        "basemap": basemap, "basemapAttr": _BASEMAP_ATTR,
        "minzoom": tiled["min_zoom"], "maxzoom": tiled["max_zoom"],
        "bounds": tiled["bounds"],
    }
    return _MAPLIBRE.replace("__TITLE__", title).replace("__DETAIL__", detail).replace(
        "__CFG__", json.dumps(cfg))


_MAPLIBRE = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>__TITLE__</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<link href="https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.css" rel="stylesheet">
<script src="https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.js"></script>
<style>body{margin:0;font-family:system-ui}#m{position:absolute;inset:0}
.legend{position:absolute;bottom:16px;left:16px;background:#fff;padding:8px 12px;border-radius:6px;font:13px system-ui;box-shadow:0 1px 4px rgba(0,0,0,.3);z-index:1}
.legend i{display:inline-block;width:12px;height:12px;margin-right:4px;vertical-align:middle}</style>
</head><body>
<div id="m"></div>
<div class="legend"><b>__TITLE__</b><br>
<i style="background:#d73027"></i>loss &nbsp; <i style="background:#1a9850"></i>gain<br>
<small>__DETAIL__</small></div>
<script>
var cfg = __CFG__;
var map = new maplibregl.Map({
  container: 'm',
  style: {
    version: 8,
    sources: {
      base: {type:'raster', tiles: cfg.basemap, tileSize: 256, attribution: cfg.basemapAttr},
      change: {type:'raster', tiles: ['tiles/{z}/{x}/{y}.png'], tileSize: 256,
               minzoom: cfg.minzoom, maxzoom: cfg.maxzoom, bounds: cfg.bounds}
    },
    layers: [
      {id:'base', type:'raster', source:'base'},
      {id:'change', type:'raster', source:'change', paint:{'raster-opacity':0.75}}
    ]
  }
});
map.addControl(new maplibregl.NavigationControl());
map.on('load', function(){ map.fitBounds([[cfg.bounds[0],cfg.bounds[1]],[cfg.bounds[2],cfg.bounds[3]]], {padding:30}); });
</script>
</body></html>
"""


def _canvas_html(title: str, detail: str, change: dict[str, Any]) -> str:
    return _CANVAS.replace("__TITLE__", title).replace("__DETAIL__", detail).replace(
        "__CHANGE__", json.dumps(change))


_CANVAS = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>__TITLE__</title>
<style>body{margin:0;font-family:system-ui}.legend{position:absolute;bottom:16px;left:16px;background:#fff;padding:8px 12px;border-radius:6px;font:13px system-ui;box-shadow:0 1px 4px rgba(0,0,0,.3)}.legend i{display:inline-block;width:12px;height:12px;margin-right:4px;vertical-align:middle}</style>
</head><body>
<div class="legend"><b>__TITLE__</b><br>
<i style="background:#d73027"></i>loss &nbsp; <i style="background:#1a9850"></i>gain &nbsp; <i style="background:#eee"></i>stable<br>
<small>__DETAIL__ (canvas fallback — install rio-tiler for a real tiled map)</small></div>
<canvas id="c"></canvas>
<script>
var change = __CHANGE__;
var cv=document.getElementById('c'),ctx=cv.getContext('2d');
var W=change.width,H=change.height,px=Math.max(4,Math.floor(Math.min(innerWidth,innerHeight)/Math.max(W,H)));
cv.width=W*px;cv.height=H*px;
var col={'-1':'#d73027','0':'#eeeeee','1':'#1a9850'};
for(var y=0;y<H;y++)for(var x=0;x<W;x++){ctx.fillStyle=col[String(change.data[y][x])];ctx.fillRect(x*px,y*px,px,px);}
</script>
</body></html>
"""

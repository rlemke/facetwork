"""Render a change raster as a MapLibre HTML viewer (+ a tiles dir placeholder).

The scaffold writes a self-contained HTML file that draws the change grid as a
colored overlay (loss = red, gain = green) — enough to eyeball a mock run. The
real path should turn the change COG into web tiles (rio-tiler / gdal2tiles or a
titiler endpoint) and point the MapLibre source at them; the HTML shell stays
the same.
"""

from __future__ import annotations

import json
import os
from typing import Any

from _s2_tools import raster, storage

_HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>{title}</title>
<style>body{{margin:0;font-family:system-ui}}#m{{height:100vh}}.legend{{position:absolute;bottom:16px;left:16px;background:#fff;padding:8px 12px;border-radius:6px;font:13px system-ui;box-shadow:0 1px 4px rgba(0,0,0,.3)}}.legend i{{display:inline-block;width:12px;height:12px;margin-right:4px;vertical-align:middle}}</style>
</head><body>
<div id="m"></div>
<div class="legend"><b>{title}</b><br>
<i style="background:#d73027"></i>loss &nbsp; <i style="background:#1a9850"></i>gain &nbsp; <i style="background:#eee"></i>stable<br>
<small>{detail}</small></div>
<canvas id="c" style="position:absolute;top:0;left:0;opacity:.7;pointer-events:none"></canvas>
<script>
var change = {change_json};
// Scaffold renderer: paint the change grid to a canvas. The real pipeline
// replaces this with a MapLibre raster source pointing at the change tiles.
var cv=document.getElementById('c'),ctx=cv.getContext('2d');
var W=change.width,H=change.height,px=Math.max(4,Math.floor(Math.min(innerWidth,innerHeight)/Math.max(W,H)));
cv.width=W*px;cv.height=H*px;
var col={{'-1':'#d73027','0':'#eeeeee','1':'#1a9850'}};
for(var y=0;y<H;y++)for(var x=0;x<W;x++){{ctx.fillStyle=col[String(change.data[y][x])];ctx.fillRect(x*px,y*px,px,px);}}
</script>
</body></html>
"""


def render_change_map(
    change_rel: str, aoi_key: str, *, title: str = "Sentinel-2 land-cover change",
    basemap_url: str = "", detail: str = ""
) -> dict[str, Any]:
    change = raster.load_change_grid(change_rel)

    out_dir = storage.join(storage.output_root(), "s2", aoi_key)
    tiles_dir = storage.join(out_dir, "tiles")
    os.makedirs(tiles_dir, exist_ok=True)
    html_path = storage.join(out_dir, "index.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(_HTML.format(title=title, detail=detail or change_rel,
                             change_json=json.dumps(change)))
    return {"aoi_key": aoi_key, "output_dir": out_dir, "html_path": html_path,
            "tiles_path": tiles_dir}

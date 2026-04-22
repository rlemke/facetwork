"""HTML map-page renderer — MapLibre GL JS + PMTiles.

Consumes the PMTiles files produced by ``build-vector-tiles`` and
generates a per-region static HTML page plus a repo-wide master index.

Cache layout::

    <cache_root>/html/
    ├── manifest.json
    ├── index.html                                ← master index (regions)
    └── <region>-latest/
        ├── index.html                            ← the MapLibre page
        └── style.json                            ← generated layer style

Cache validity per region:

- Set of source PMTiles present matches what the manifest recorded, AND
- Every source PMTiles' SHA-256 still matches, AND
- ``STYLE_VERSION`` still matches.

Bumping ``STYLE_VERSION`` below (e.g. after tweaking layer colors or
sub-filter rules) invalidates every region's rendered HTML without
per-region ``--force``.

The renderer itself loads MapLibre and pmtiles.js from a CDN by default
so individual region pages stay small (~3 KB each); a ``--bundle-assets``
flag could later copy those libs into ``html/assets/`` for offline use.

Sub-classification (highways by class, parks by level, water by kind,
shops by type) is done at *render* time via MapLibre filter expressions
on the already-extracted PMTiles — no extra extract runs needed.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import struct
import textwrap
import threading
import time
from dataclasses import dataclass, field
from html import escape as html_escape
from pathlib import Path
from typing import Any

from _lib.manifest import (
    cache_dir,
    manifest_transaction,
    read_manifest,
    utcnow_iso,
)
from _lib.storage import LocalStorage
from _lib.vector_tiles_build import tileset_abs_path, valid_sources

OUTPUT_CACHE_TYPE = "html"
VECTOR_CACHE_TYPE = "vector_tiles"

# Bump when anything about the generated HTML/style JSON changes in a
# way that should invalidate existing rendered pages.
STYLE_VERSION = 1

CHUNK_SIZE = 1024 * 1024

MAPLIBRE_CSS = "https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.css"
MAPLIBRE_JS = "https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.js"
PMTILES_JS = "https://unpkg.com/pmtiles@3.2.1/dist/pmtiles.js"

_render_locks: dict[str, threading.Lock] = {}
_render_locks_guard = threading.Lock()
_manifest_write_lock = threading.Lock()


def _render_lock(region: str) -> threading.Lock:
    with _render_locks_guard:
        lock = _render_locks.get(region)
        if lock is None:
            lock = threading.Lock()
            _render_locks[region] = lock
        return lock


@dataclass
class RenderResult:
    region: str
    html_dir: str               # absolute path to the per-region directory
    relative_path: str          # relative path within html/ cache
    sources: list[str]          # names of source PMTiles layered into the page
    total_size_bytes: int       # size of generated HTML + style.json
    style_version: int
    generated_at: str
    duration_seconds: float
    was_cached: bool
    manifest_entry: dict[str, Any] = field(default_factory=dict)


class RenderError(RuntimeError):
    pass


def html_rel_path(region: str) -> str:
    return f"{region}-latest"


def html_abs_path(region: str) -> Path:
    return Path(cache_dir(OUTPUT_CACHE_TYPE)) / html_rel_path(region)


def master_index_path() -> Path:
    return Path(cache_dir(OUTPUT_CACHE_TYPE)) / "index.html"


def _staging_dir(region: str) -> Path:
    out = html_abs_path(region)
    return out.with_name(out.name + ".tmp")


def _sha256_file(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(CHUNK_SIZE)
            if not chunk:
                break
            sha.update(chunk)
    return sha.hexdigest()


# ---------------------------------------------------------------------------
# Layer styles — the core of how raw PMTiles become sub-classified map layers.
# Each key is a source name (as produced by build-vector-tiles). The value is
# a list of MapLibre layer definitions that reference that source, each with
# its own filter expression so one PMTiles renders as multiple styled layers.
#
# Ordering matters: later entries paint on top. Polygons first, then lines,
# then points.
# ---------------------------------------------------------------------------

def _layer_styles_for(source: str) -> list[dict[str, Any]]:
    """Return the MapLibre layers derived from a given vector-tiles source."""
    if source == "water":
        return [
            {
                "id": "water-polygons",
                "source": source,
                "source-layer": source,
                "type": "fill",
                "filter": ["any",
                    ["==", ["get", "natural"], "water"],
                    ["in", ["get", "water"],
                        ["literal", ["lake", "pond", "reservoir", "basin", "lagoon"]]
                    ],
                ],
                "paint": {"fill-color": "#9ccbe3", "fill-opacity": 0.7},
            },
            {
                "id": "water-rivers-line",
                "source": source,
                "source-layer": source,
                "type": "line",
                "filter": ["==", ["get", "waterway"], "river"],
                "paint": {"line-color": "#4a7ba6", "line-width": 1.6},
            },
            {
                "id": "water-streams",
                "source": source,
                "source-layer": source,
                "type": "line",
                "filter": ["==", ["get", "waterway"], "stream"],
                "paint": {"line-color": "#6797be", "line-width": 0.8},
                "minzoom": 10,
            },
            {
                "id": "water-canals",
                "source": source,
                "source-layer": source,
                "type": "line",
                "filter": ["==", ["get", "waterway"], "canal"],
                "paint": {"line-color": "#3d6990", "line-width": 1.2},
            },
        ]

    if source == "protected_areas":
        return [
            {
                "id": "protected-national-park",
                "source": source,
                "source-layer": source,
                "type": "fill",
                "filter": ["==", ["get", "boundary"], "national_park"],
                "paint": {"fill-color": "#2e7d32", "fill-opacity": 0.25,
                          "fill-outline-color": "#1b5e20"},
            },
            {
                "id": "protected-state-park",
                "source": source,
                "source-layer": source,
                "type": "fill",
                "filter": ["any",
                    ["all",
                        ["==", ["get", "boundary"], "protected_area"],
                        ["has", "protection_title"],
                        ["in", "State Park", ["get", "protection_title"]],
                    ],
                ],
                "paint": {"fill-color": "#43a047", "fill-opacity": 0.22,
                          "fill-outline-color": "#2e7d32"},
            },
            {
                "id": "protected-other",
                "source": source,
                "source-layer": source,
                "type": "fill",
                "filter": ["all",
                    ["==", ["get", "boundary"], "protected_area"],
                ],
                "paint": {"fill-color": "#81c784", "fill-opacity": 0.18,
                          "fill-outline-color": "#558b5a"},
            },
            {
                "id": "nature-reserve",
                "source": source,
                "source-layer": source,
                "type": "fill",
                "filter": ["==", ["get", "leisure"], "nature_reserve"],
                "paint": {"fill-color": "#a5d6a7", "fill-opacity": 0.22,
                          "fill-outline-color": "#66bb6a"},
            },
        ]

    if source == "parks":
        return [
            {
                "id": "parks-city",
                "source": source,
                "source-layer": source,
                "type": "fill",
                "filter": ["==", ["get", "leisure"], "park"],
                "paint": {"fill-color": "#c8e6c9", "fill-opacity": 0.6,
                          "fill-outline-color": "#81c784"},
            },
            {
                "id": "parks-garden",
                "source": source,
                "source-layer": source,
                "type": "fill",
                "filter": ["==", ["get", "leisure"], "garden"],
                "paint": {"fill-color": "#d7e4c0", "fill-opacity": 0.6},
            },
            {
                "id": "parks-playground",
                "source": source,
                "source-layer": source,
                "type": "fill",
                "filter": ["==", ["get", "leisure"], "playground"],
                "paint": {"fill-color": "#ffe0b2", "fill-opacity": 0.7},
            },
        ]

    if source == "forests":
        return [
            {
                "id": "forests",
                "source": source,
                "source-layer": source,
                "type": "fill",
                "filter": ["any",
                    ["==", ["get", "natural"], "wood"],
                    ["==", ["get", "landuse"], "forest"],
                ],
                "paint": {"fill-color": "#689f38", "fill-opacity": 0.35},
            },
        ]

    if source == "roads_routable":
        # Classification follows OSM's universal highway=* scale.
        # Labels ("Interstate", "Autobahn", "A-road") are country-specific
        # and can be rendered via the `ref` tag — the CLASSIFICATION is
        # universal.
        return [
            {
                "id": "roads-residential",
                "source": source,
                "source-layer": source,
                "type": "line",
                "filter": ["any",
                    ["==", ["get", "highway"], "residential"],
                    ["==", ["get", "highway"], "unclassified"],
                    ["==", ["get", "highway"], "service"],
                ],
                "paint": {"line-color": "#ffffff", "line-width": 1.0},
                "minzoom": 12,
            },
            {
                "id": "roads-tertiary",
                "source": source,
                "source-layer": source,
                "type": "line",
                "filter": ["in", ["get", "highway"],
                           ["literal", ["tertiary", "tertiary_link"]]],
                "paint": {"line-color": "#ffffff", "line-width": 1.5},
                "minzoom": 10,
            },
            {
                "id": "roads-secondary",
                "source": source,
                "source-layer": source,
                "type": "line",
                "filter": ["in", ["get", "highway"],
                           ["literal", ["secondary", "secondary_link"]]],
                "paint": {"line-color": "#fffaeb", "line-width": 2.0},
                "minzoom": 8,
            },
            {
                "id": "roads-primary",
                "source": source,
                "source-layer": source,
                "type": "line",
                "filter": ["in", ["get", "highway"],
                           ["literal", ["primary", "primary_link"]]],
                "paint": {"line-color": "#ffdd99", "line-width": 2.5},
                "minzoom": 6,
            },
            {
                "id": "roads-trunk",
                "source": source,
                "source-layer": source,
                "type": "line",
                "filter": ["in", ["get", "highway"],
                           ["literal", ["trunk", "trunk_link"]]],
                "paint": {"line-color": "#fcb165", "line-width": 2.8},
                "minzoom": 5,
            },
            {
                "id": "roads-motorway",
                "source": source,
                "source-layer": source,
                "type": "line",
                "filter": ["in", ["get", "highway"],
                           ["literal", ["motorway", "motorway_link"]]],
                "paint": {"line-color": "#e892a2", "line-width": 3.2},
                "minzoom": 4,
            },
        ]

    if source == "railways_routable":
        return [
            {
                "id": "railways",
                "source": source,
                "source-layer": source,
                "type": "line",
                "filter": ["all",
                    ["has", "railway"],
                    ["==", ["geometry-type"], "LineString"],
                ],
                "paint": {"line-color": "#555", "line-width": 2.0,
                          "line-dasharray": [4, 2]},
                "minzoom": 5,
            },
        ]

    if source == "cycle_routes":
        return [
            {
                "id": "cycle-routes",
                "source": source,
                "source-layer": source,
                "type": "line",
                "paint": {"line-color": "#1e88e5", "line-width": 1.5,
                          "line-dasharray": [3, 1]},
                "minzoom": 8,
            },
        ]

    if source == "hiking_routes":
        return [
            {
                "id": "hiking-routes",
                "source": source,
                "source-layer": source,
                "type": "line",
                "paint": {"line-color": "#d32f2f", "line-width": 1.2,
                          "line-dasharray": [4, 2]},
                "minzoom": 9,
            },
        ]

    # POI categories — all rendered as simple circles with per-category color.
    poi_colors = {
        "food": "#ef5350",
        "healthcare": "#e91e63",
        "education": "#7e57c2",
        "government": "#546e7a",
        "public_transport": "#1976d2",
        "culture": "#ab47bc",
        "religion": "#795548",
        "sports": "#00897b",
        "shopping": "#fb8c00",
        "accommodation": "#3949ab",
        "finance": "#558b2f",
        "fuel_charging": "#ef6c00",
        "parking": "#90a4ae",
        "entertainment": "#d81b60",
        "toilets": "#00838f",
        "emergency": "#b71c1c",
    }
    if source in poi_colors:
        return [
            {
                "id": f"poi-{source}",
                "source": source,
                "source-layer": source,
                "type": "circle",
                "paint": {
                    "circle-color": poi_colors[source],
                    "circle-radius": 4,
                    "circle-stroke-color": "#ffffff",
                    "circle-stroke-width": 1,
                },
                "minzoom": 12,
            },
        ]

    # geojson (whole-region): skip — usually too broad to style meaningfully
    # alongside the focused category layers. Could be enabled as an opt-in.
    return []


# ---------------------------------------------------------------------------
# Layer groups — maps a human-readable group name to the layer IDs that
# belong to it, plus a swatch color for the legend.  Order here controls
# the order in the toggle panel.
# ---------------------------------------------------------------------------

# Ordered list of (group_label, color, [layer_ids]).
# Built dynamically from the style layers so it stays in sync.

_GROUP_DEFINITIONS: list[tuple[str, str, list[str]]] = [
    ("Water",               "#4a7ba6", ["water-polygons", "water-rivers-line", "water-streams", "water-canals"]),
    ("Forests",             "#689f38", ["forests"]),
    ("Protected Areas",     "#2e7d32", ["protected-national-park", "protected-state-park", "protected-other", "nature-reserve"]),
    ("Parks",               "#81c784", ["parks-city", "parks-garden", "parks-playground"]),
    ("Motorways",           "#e892a2", ["roads-motorway"]),
    ("Trunk Roads",         "#fcb165", ["roads-trunk"]),
    ("Primary Roads",       "#ffdd99", ["roads-primary"]),
    ("Secondary Roads",     "#fffaeb", ["roads-secondary"]),
    ("Tertiary Roads",      "#ffffff", ["roads-tertiary"]),
    ("Residential Roads",   "#ffffff", ["roads-residential"]),
    ("Railways",            "#666666", ["railways"]),
    ("Cycle Routes",        "#1e88e5", ["cycle-routes"]),
    ("Hiking Routes",       "#d32f2f", ["hiking-routes"]),
    ("Food",                "#ef5350", ["poi-food"]),
    ("Healthcare",          "#e91e63", ["poi-healthcare"]),
    ("Education",           "#7e57c2", ["poi-education"]),
    ("Shopping",            "#fb8c00", ["poi-shopping"]),
    ("Public Transport",    "#1976d2", ["poi-public_transport"]),
    ("Accommodation",       "#3949ab", ["poi-accommodation"]),
    ("Culture",             "#ab47bc", ["poi-culture"]),
    ("Entertainment",       "#d81b60", ["poi-entertainment"]),
    ("Sports",              "#00897b", ["poi-sports"]),
    ("Religion",            "#795548", ["poi-religion"]),
    ("Government",          "#546e7a", ["poi-government"]),
    ("Finance",             "#558b2f", ["poi-finance"]),
    ("Fuel & Charging",     "#ef6c00", ["poi-fuel_charging"]),
    ("Parking",             "#90a4ae", ["poi-parking"]),
    ("Toilets",             "#00838f", ["poi-toilets"]),
    ("Emergency",           "#b71c1c", ["poi-emergency"]),
]


def _layer_groups_for_style(style_layers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the subset of group definitions whose layers actually exist in
    the style, as JSON-serialisable dicts for the template."""
    present = {layer["id"] for layer in style_layers}
    groups: list[dict[str, Any]] = []
    for label, color, layer_ids in _GROUP_DEFINITIONS:
        existing = [lid for lid in layer_ids if lid in present]
        if existing:
            groups.append({"label": label, "color": color, "layers": existing})
    return groups


def _build_style(
    region: str,
    sources_with_paths: list[tuple[str, str]],
    bbox: tuple[float, float, float, float] | None = None,
) -> dict[str, Any]:
    """Assemble the full MapLibre style JSON for a region.

    ``sources_with_paths`` is a list of ``(source_name, relative_pmtiles_url)``
    — the URL is relative to the emitted HTML file (so a static HTTP server
    rooted at the cache root can serve everything).
    """
    sources: dict[str, Any] = {}
    layers: list[dict[str, Any]] = []

    # Background fill so there's something to see before tiles paint.
    layers.append(
        {
            "id": "background",
            "type": "background",
            "paint": {"background-color": "#f5f2ea"},
        }
    )

    # Base map raster sources — one visible at a time, toggled by the UI.
    # All start hidden except "Plain" (the background layer above).
    _BASEMAPS: list[tuple[str, str, list[str], int]] = [
        # (id, label, tile_urls, max_zoom)
        ("basemap-osm", "OpenStreetMap",
         ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"], 19),
        ("basemap-satellite", "Satellite",
         ["https://server.arcgisonline.com/ArcGIS/rest/services/"
          "World_Imagery/MapServer/tile/{z}/{y}/{x}"], 18),
        ("basemap-topo", "Topographic",
         ["https://server.arcgisonline.com/ArcGIS/rest/services/"
          "World_Topo_Map/MapServer/tile/{z}/{y}/{x}"], 18),
    ]
    for bm_id, _label, tile_urls, max_zoom in _BASEMAPS:
        sources[bm_id] = {
            "type": "raster",
            "tiles": tile_urls,
            "tileSize": 256,
            "maxzoom": max_zoom,
            "attribution": "",
        }
        layers.append({
            "id": bm_id,
            "type": "raster",
            "source": bm_id,
            "layout": {"visibility": "none"},
        })

    for source_name, rel_url in sources_with_paths:
        sources[source_name] = {
            "type": "vector",
            "url": f"pmtiles://{rel_url}",
        }
        for layer in _layer_styles_for(source_name):
            layers.append(layer)

    center = [0.0, 20.0]
    zoom = 2.5
    if bbox is not None:
        west, south, east, north = bbox
        center = [(west + east) / 2.0, (south + north) / 2.0]
        # Rough zoom estimate — biggest dimension determines it.
        import math
        span = max(east - west, (north - south) * 2.0)
        if span > 0:
            zoom = max(2.0, min(12.0, math.log2(360.0 / span)))

    return {
        "version": 8,
        "name": f"Facetwork — {region}",
        "metadata": {
            "facetwork:region": region,
            "facetwork:style_version": STYLE_VERSION,
        },
        "sources": sources,
        "layers": layers,
        "center": center,
        "zoom": zoom,
    }


def _html_template(
    region: str,
    popup_layer_ids: list[str],
    layer_groups: list[dict[str, Any]],
) -> str:
    """Return the per-region index.html as a single string."""
    popup_ids_json = json.dumps(popup_layer_ids)
    groups_json = json.dumps(layer_groups)
    title = html_escape(f"Facetwork · {region}")
    return textwrap.dedent(f"""\
        <!doctype html>
        <html lang="en">
        <head>
          <meta charset="utf-8">
          <title>{title}</title>
          <meta name="viewport" content="width=device-width,initial-scale=1">
          <link rel="stylesheet" href="{MAPLIBRE_CSS}">
          <style>
            html, body {{ margin: 0; padding: 0; height: 100%; font-family: system-ui, sans-serif; }}
            #map {{ width: 100%; height: 100%; }}
            .maplibregl-popup-content {{
              max-width: 320px; font-size: 12px; line-height: 1.3;
            }}
            .maplibregl-popup-content table {{
              border-collapse: collapse;
            }}
            .maplibregl-popup-content td {{
              padding: 2px 6px; vertical-align: top;
              border-bottom: 1px solid #eee;
            }}
            .maplibregl-popup-content td:first-child {{
              color: #555; font-weight: 600; white-space: nowrap;
            }}
            #banner {{
              position: absolute; top: 10px; left: 10px;
              background: rgba(255,255,255,0.92); padding: 6px 12px;
              border-radius: 4px; font-size: 13px; z-index: 1;
              box-shadow: 0 1px 3px rgba(0,0,0,0.2);
            }}
            #layers-panel {{
              position: absolute; top: 10px; right: 50px;
              background: rgba(255,255,255,0.95); border-radius: 4px;
              font-size: 12px; z-index: 1;
              box-shadow: 0 1px 3px rgba(0,0,0,0.2);
              max-height: calc(100vh - 40px); overflow-y: auto;
              min-width: 180px;
            }}
            #layers-toggle {{
              padding: 6px 10px; cursor: pointer; font-weight: 600;
              font-size: 13px; user-select: none; display: flex;
              align-items: center; justify-content: space-between;
            }}
            #layers-toggle:hover {{ background: #f0f0f0; border-radius: 4px; }}
            #layers-toggle .arrow {{ font-size: 10px; margin-left: 8px; }}
            #basemap-section.collapsed, #zoom-override.collapsed, #layers-list.collapsed {{ display: none; }}
            #layers-list {{ padding: 4px 0; border-top: 1px solid #e0e0e0; }}
            .layer-group {{
              display: flex; align-items: center; padding: 3px 10px;
              cursor: pointer; user-select: none;
            }}
            .layer-group:hover {{ background: #f5f5f5; }}
            .layer-group .swatch {{
              width: 12px; height: 12px; border-radius: 2px;
              margin-right: 8px; flex-shrink: 0;
              border: 1px solid rgba(0,0,0,0.15);
            }}
            .layer-group.off .swatch {{ opacity: 0.25; }}
            .layer-group.off .lbl {{ color: #aaa; text-decoration: line-through; }}
            .layers-actions {{
              display: flex; gap: 8px; padding: 4px 10px 6px;
              border-top: 1px solid #e0e0e0; font-size: 11px;
            }}
            .layers-actions span {{ cursor: pointer; color: #1976d2; }}
            .layers-actions span:hover {{ text-decoration: underline; }}
            #basemap-section {{
              padding: 4px 10px 6px; border-bottom: 1px solid #e0e0e0;
            }}
            #basemap-section label {{
              font-weight: 600; font-size: 11px; color: #555;
              text-transform: uppercase; letter-spacing: 0.5px;
              display: block; margin-bottom: 3px;
            }}
            #basemap-select {{
              width: 100%; font-size: 12px; padding: 2px 4px;
              border: 1px solid #ccc; border-radius: 3px;
              background: #fff; cursor: pointer;
            }}
            #zoom-override {{
              display: flex; align-items: center; padding: 5px 10px;
              border-bottom: 1px solid #e0e0e0; cursor: pointer;
              user-select: none; font-size: 12px;
            }}
            #zoom-override:hover {{ background: #f5f5f5; }}
            #zoom-override input {{ margin: 0 8px 0 0; cursor: pointer; }}
            #zoom-override.active {{ background: #e3f2fd; }}
          </style>
        </head>
        <body>
          <div id="banner">{html_escape(region)}</div>
          <div id="layers-panel">
            <div id="layers-toggle">Layers <span class="arrow">&#9660;</span></div>
            <div id="basemap-section">
              <label>Base map</label>
              <select id="basemap-select">
                <option value="plain" selected>Plain</option>
                <option value="basemap-osm">OpenStreetMap</option>
                <option value="basemap-satellite">Satellite</option>
                <option value="basemap-topo">Topographic</option>
              </select>
            </div>
            <label id="zoom-override"><input type="checkbox"> Show all at any zoom</label>
            <div id="layers-list"></div>
          </div>
          <div id="map"></div>
          <script src="{MAPLIBRE_JS}"></script>
          <script src="{PMTILES_JS}"></script>
          <script>
            const protocol = new pmtiles.Protocol();
            maplibregl.addProtocol("pmtiles", protocol.tile);

            const layerGroups = {groups_json};

            const map = new maplibregl.Map({{
              container: "map",
              style: "./style.json",
              hash: true
            }});
            map.addControl(new maplibregl.NavigationControl(), "top-right");
            map.addControl(new maplibregl.ScaleControl(), "bottom-left");

            // --- Layer toggle panel ---
            const listEl = document.getElementById("layers-list");
            const basemapEl = document.getElementById("basemap-section");
            const zoomOverrideEl = document.getElementById("zoom-override");
            const toggleEl = document.getElementById("layers-toggle");
            let panelOpen = true;
            toggleEl.addEventListener("click", () => {{
              panelOpen = !panelOpen;
              listEl.classList.toggle("collapsed", !panelOpen);
              basemapEl.classList.toggle("collapsed", !panelOpen);
              zoomOverrideEl.classList.toggle("collapsed", !panelOpen);
              toggleEl.querySelector(".arrow").innerHTML = panelOpen ? "&#9660;" : "&#9654;";
            }});
            // --- Base map switcher ---
            const basemapIds = ["basemap-osm", "basemap-satellite", "basemap-topo"];
            const basemapSelect = document.getElementById("basemap-select");
            basemapSelect.addEventListener("change", () => {{
              const val = basemapSelect.value;
              basemapIds.forEach(id => {{
                if (map.getLayer(id)) {{
                  map.setLayoutProperty(id, "visibility", id === val ? "visible" : "none");
                }}
              }});
              // Toggle the background layer for "plain"
              if (map.getLayer("background")) {{
                map.setPaintProperty("background", "background-opacity", val === "plain" ? 1 : 0);
              }}
            }});

            const groupState = layerGroups.map(() => true);
            function buildPanel() {{
              listEl.innerHTML = "";
              layerGroups.forEach((g, i) => {{
                const row = document.createElement("div");
                row.className = "layer-group" + (groupState[i] ? "" : " off");
                row.innerHTML = '<div class="swatch" style="background:' + g.color + '"></div>'
                              + '<span class="lbl">' + g.label + '</span>';
                row.addEventListener("click", () => {{
                  groupState[i] = !groupState[i];
                  row.classList.toggle("off", !groupState[i]);
                  const vis = groupState[i] ? "visible" : "none";
                  g.layers.forEach(lid => {{
                    if (map.getLayer(lid)) map.setLayoutProperty(lid, "visibility", vis);
                  }});
                }});
                listEl.appendChild(row);
              }});
              const actions = document.createElement("div");
              actions.className = "layers-actions";
              const allBtn = document.createElement("span");
              allBtn.textContent = "All";
              allBtn.addEventListener("click", () => setAll(true));
              const noneBtn = document.createElement("span");
              noneBtn.textContent = "None";
              noneBtn.addEventListener("click", () => setAll(false));
              actions.appendChild(allBtn);
              actions.appendChild(noneBtn);
              listEl.appendChild(actions);
            }}
            function setAll(on) {{
              const vis = on ? "visible" : "none";
              layerGroups.forEach((g, i) => {{
                groupState[i] = on;
                g.layers.forEach(lid => {{
                  if (map.getLayer(lid)) map.setLayoutProperty(lid, "visibility", vis);
                }});
              }});
              buildPanel();
            }}

            // --- "Show all at any zoom" override ---
            const zoomEl = document.getElementById("zoom-override");
            const zoomCb = zoomEl.querySelector("input");
            const savedMinZoom = {{}};
            zoomCb.addEventListener("change", () => {{
              const force = zoomCb.checked;
              zoomEl.classList.toggle("active", force);
              const style = map.getStyle();
              style.layers.forEach(layer => {{
                if (layer.id === "background" || layer.type === "raster") return;
                const lid = layer.id;
                if (force) {{
                  const cur = map.getLayer(lid) && layer.minzoom;
                  if (cur !== undefined && cur > 0) {{
                    savedMinZoom[lid] = cur;
                    map.setLayerZoomRange(lid, 0, layer.maxzoom || 24);
                  }}
                }} else {{
                  if (savedMinZoom[lid] !== undefined) {{
                    map.setLayerZoomRange(lid, savedMinZoom[lid], layer.maxzoom || 24);
                  }}
                }}
              }});
            }});

            // Click popups — show feature tags for any interactive layer.
            const popupLayers = {popup_ids_json};
            map.on("load", () => {{
              buildPanel();
              popupLayers.forEach(layerId => {{
                if (!map.getLayer(layerId)) return;
                map.on("click", layerId, (e) => {{
                  const f = e.features && e.features[0];
                  if (!f) return;
                  const rows = Object.entries(f.properties || {{}})
                    .map(([k, v]) => `<tr><td>${{k}}</td><td>${{String(v)}}</td></tr>`)
                    .join("");
                  new maplibregl.Popup()
                    .setLngLat(e.lngLat)
                    .setHTML(`<table>${{rows}}</table>`)
                    .addTo(map);
                }});
                map.on("mouseenter", layerId, () => {{
                  map.getCanvas().style.cursor = "pointer";
                }});
                map.on("mouseleave", layerId, () => {{
                  map.getCanvas().style.cursor = "";
                }});
              }});
            }});
          </script>
        </body>
        </html>
        """)


def _discover_sources(region: str) -> list[tuple[str, str, str]]:
    """Return ``[(source_name, pmtiles_abs_path, sha256), ...]`` for a region.

    Reads the vector_tiles manifest to find every PMTiles file that exists
    for this region, verifies each file is present on disk, and captures
    the recorded SHA-256 for cache-validity checks.
    """
    vt_manifest = read_manifest(VECTOR_CACHE_TYPE)
    prefix = f"{region}-latest/"
    suffix = ".pmtiles"
    out: list[tuple[str, str, str]] = []
    for rel, entry in vt_manifest.get("entries", {}).items():
        if not rel.startswith(prefix) or not rel.endswith(suffix):
            continue
        source = rel[len(prefix) : -len(suffix)]
        abs_path = tileset_abs_path(region, source)
        if not abs_path.exists():
            continue
        sha = entry.get("sha256", "")
        out.append((source, str(abs_path), sha))
    # Stable order — the style generator ordering depends on this.
    out.sort(key=lambda t: t[0])
    return out


def is_up_to_date(
    region: str,
    sources: list[tuple[str, str, str]],
) -> bool:
    """Cache hit when recorded sources' SHAs + STYLE_VERSION all still match."""
    cache_manifest = read_manifest(OUTPUT_CACHE_TYPE)
    rel = html_rel_path(region)
    existing = cache_manifest.get("entries", {}).get(rel)
    if not existing:
        return False
    if existing.get("style_version") != STYLE_VERSION:
        return False
    existing_sources = existing.get("sources", {}) or {}
    current_names = {name for name, _, _ in sources}
    if set(existing_sources) != current_names:
        return False
    for name, _path, sha in sources:
        if existing_sources.get(name, {}).get("sha256") != sha:
            return False
    html_dir = html_abs_path(region)
    if not (html_dir / "index.html").exists():
        return False
    if not (html_dir / "style.json").exists():
        return False
    return True


def _bbox_from_sources(
    sources: list[tuple[str, str, str]],
) -> tuple[float, float, float, float] | None:
    """Compute the union bounding box from PMTiles v3 headers.

    Each source is ``(name, abs_path, sha)``.  Returns ``(west, south, east,
    north)`` or *None* if no bounds could be read.

    PMTiles v3 header stores bounds as int32 values at fixed offsets
    (min_lon_e7 @ 102, min_lat_e7 @ 106, max_lon_e7 @ 110, max_lat_e7 @ 114),
    each representing degrees × 10⁷.
    """
    west, south, east, north = 180.0, 90.0, -180.0, -90.0
    found = False
    for _name, abs_path, _sha in sources:
        try:
            with open(abs_path, "rb") as f:
                header = f.read(127)
            if len(header) < 118 or header[0:7] != b"PMTiles":
                continue
            w = struct.unpack_from("<i", header, 102)[0] / 1e7
            s = struct.unpack_from("<i", header, 106)[0] / 1e7
            e = struct.unpack_from("<i", header, 110)[0] / 1e7
            n = struct.unpack_from("<i", header, 114)[0] / 1e7
        except Exception:
            continue
        west = min(west, w)
        south = min(south, s)
        east = max(east, e)
        north = max(north, n)
        found = True
    return (west, south, east, north) if found else None


def render_region(
    region: str,
    *,
    bbox: tuple[float, float, float, float] | None = None,
    force: bool = False,
) -> RenderResult:
    """Generate the per-region HTML + style.json under ``html/<region>-latest/``."""
    with _render_lock(region):
        sources = _discover_sources(region)
        if not sources:
            raise RenderError(
                f"no vector_tiles entries for region {region!r}. "
                "Run build-vector-tiles first."
            )

        if bbox is None:
            bbox = _bbox_from_sources(sources)

        html_dir = html_abs_path(region)
        rel = html_rel_path(region)

        if not force and is_up_to_date(region, sources):
            existing = read_manifest(OUTPUT_CACHE_TYPE).get("entries", {}).get(rel, {})
            return RenderResult(
                region=region,
                html_dir=str(html_dir),
                relative_path=rel + "/",
                sources=sorted(existing.get("sources", {}).keys()),
                total_size_bytes=existing.get("total_size_bytes", 0),
                style_version=STYLE_VERSION,
                generated_at=existing.get("generated_at", ""),
                duration_seconds=0.0,
                was_cached=True,
                manifest_entry=existing,
            )

        staging = _staging_dir(region)
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True, exist_ok=True)

        # Build relative URLs from the HTML file to each PMTiles.
        # Both live under cache_dir; depth to root = len(region.split("/")) + 1.
        # Style references "../../../vector_tiles/<region>-latest/<source>.pmtiles"
        # from an HTML at html/<region>-latest/index.html.
        depth = len(region.split("/")) + 1
        up = "../" * depth
        sources_with_urls: list[tuple[str, str]] = []
        source_records: dict[str, dict[str, Any]] = {}
        for name, abs_path, sha in sources:
            rel_url = f"{up}{VECTOR_CACHE_TYPE}/{region}-latest/{name}.pmtiles"
            sources_with_urls.append((name, rel_url))
            source_records[name] = {
                "sha256": sha,
                "relative_path": f"{region}-latest/{name}.pmtiles",
            }

        start = time.monotonic()
        style = _build_style(region, sources_with_urls, bbox=bbox)
        (staging / "style.json").write_text(
            json.dumps(style, indent=2), encoding="utf-8"
        )

        popup_layer_ids = [layer["id"] for layer in style["layers"]
                           if layer.get("id") != "background"]
        layer_groups = _layer_groups_for_style(style["layers"])
        html = _html_template(region, popup_layer_ids, layer_groups)
        (staging / "index.html").write_text(html, encoding="utf-8")
        elapsed = time.monotonic() - start

        # Finalize the staged dir into the final location.
        storage = LocalStorage()
        storage.finalize_dir_from_local(str(staging), str(html_dir))

        total_size = sum(
            f.stat().st_size for f in html_dir.rglob("*") if f.is_file()
        )
        generated_at = utcnow_iso()
        entry = {
            "relative_path": rel + "/",
            "region": region,
            "style_version": STYLE_VERSION,
            "total_size_bytes": total_size,
            "sources": source_records,
            "generated_at": generated_at,
            "duration_seconds": round(elapsed, 3),
            "tool": {"command": "python-html-render + maplibre-gl + pmtiles.js"},
            "extra": {},
        }
        with _manifest_write_lock, manifest_transaction(OUTPUT_CACHE_TYPE) as manifest:
            manifest.setdefault("entries", {})[rel] = entry

        return RenderResult(
            region=region,
            html_dir=str(html_dir),
            relative_path=rel + "/",
            sources=[name for name, _, _ in sources],
            total_size_bytes=total_size,
            style_version=STYLE_VERSION,
            generated_at=generated_at,
            duration_seconds=elapsed,
            was_cached=False,
            manifest_entry=entry,
        )


def _master_index_html(entries: list[dict[str, Any]]) -> str:
    """Build the master ``html/index.html`` listing every rendered region."""
    rows = []
    for e in entries:
        region = e.get("region", "")
        rel_dir = e.get("relative_path", "").rstrip("/")
        src_count = len(e.get("sources", {}) or {})
        size_mib = e.get("total_size_bytes", 0) / 1024.0
        generated = e.get("generated_at", "") or ""
        rows.append(
            f"<tr>"
            f"<td><a href={json.dumps(rel_dir + '/index.html')}>{html_escape(region)}</a></td>"
            f"<td>{src_count}</td>"
            f"<td>{size_mib:.1f} KiB</td>"
            f"<td>{html_escape(generated)}</td>"
            f"</tr>"
        )
    body = "\n".join(rows) if rows else "<tr><td colspan=4><em>no regions rendered yet</em></td></tr>"
    return textwrap.dedent(f"""\
        <!doctype html>
        <html lang="en">
        <head>
          <meta charset="utf-8">
          <title>Facetwork — OSM Map Index</title>
          <meta name="viewport" content="width=device-width,initial-scale=1">
          <style>
            body {{ font-family: system-ui, sans-serif; max-width: 960px;
                    margin: 40px auto; padding: 0 20px; color: #222; }}
            h1 {{ font-size: 22px; margin-bottom: 8px; }}
            p.lede {{ color: #666; margin-top: 0; }}
            table {{ border-collapse: collapse; width: 100%; margin-top: 20px; }}
            th, td {{ text-align: left; padding: 8px 12px;
                      border-bottom: 1px solid #eee; font-size: 14px; }}
            th {{ color: #555; font-weight: 600; background: #f7f7f5; }}
            a {{ color: #1565c0; text-decoration: none; }}
            a:hover {{ text-decoration: underline; }}
            footer {{ margin-top: 40px; color: #999; font-size: 12px; }}
          </style>
        </head>
        <body>
          <h1>Facetwork — OSM Map Index</h1>
          <p class="lede">
            Rendered region pages. Each map loads PMTiles from the
            <code>vector_tiles/</code> cache and styles water, parks, roads,
            protected areas, and POIs as interactive layers. Click any
            feature for its raw OSM tags.
          </p>
          <table>
            <thead><tr>
              <th>Region</th><th>Source layers</th><th>HTML size</th><th>Generated</th>
            </tr></thead>
            <tbody>
            {body}
            </tbody>
          </table>
          <footer>
            Generated {html_escape(utcnow_iso())}. Serve with
            <code>python -m http.server --directory &lt;cache_root&gt; 8000</code>
            then open <code>http://localhost:8000/html/</code>.
          </footer>
        </body>
        </html>
        """)


def write_master_index() -> None:
    """Regenerate ``html/index.html`` from the current manifest."""
    cache_manifest = read_manifest(OUTPUT_CACHE_TYPE)
    entries = sorted(
        cache_manifest.get("entries", {}).values(),
        key=lambda e: e.get("region", ""),
    )
    out_path = master_index_path()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(_master_index_html(entries), encoding="utf-8")


def list_rendered() -> list[dict[str, Any]]:
    cache_manifest = read_manifest(OUTPUT_CACHE_TYPE)
    out = list(cache_manifest.get("entries", {}).values())
    out.sort(key=lambda e: e.get("region", ""))
    return out

"""Render a top-level landing page for $AFL_CACHE_ROOT.

Walks every known pipeline namespace under the cache root and emits
``$AFL_CACHE_ROOT/index.html`` with one card per pipeline linking at
its master index / primary maps. A single bookmark that gives you
everything at once.

Currently detects:

- ``osm/html/index.html``               — master OSM map index
- ``noaa-weather/climate-report/``      — climate-report master index
                                            + warming choropleth
- ``save-earth/maps/<region>/``         — every save-earth region map

Usage::

    python scripts/build-cache-index.py
    python scripts/build-cache-index.py --cache-root /custom/path
    python scripts/build-cache-index.py --open                 # also xdg-open

Safe to re-run — overwrites the existing ``index.html`` and sidecar.
"""

from __future__ import annotations

import argparse
import hashlib
import html as html_mod
import json
import logging
import os
import sys
import textwrap
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("afl.cache-index")


def _default_cache_root() -> Path:
    """Same resolution order as the _lib/storage modules in each example."""
    env = os.environ.get("AFL_CACHE_ROOT")
    if env:
        return Path(env)
    data_root = os.environ.get("AFL_DATA_ROOT", "/Volumes/afl_data")
    return Path(data_root) / "cache"


@dataclass
class PipelineCard:
    """One cache pipeline's entry on the landing page."""

    name: str
    title: str
    blurb: str
    primary_links: list[tuple[str, str]] = field(default_factory=list)
    secondary_links: list[tuple[str, str]] = field(default_factory=list)
    stats: list[tuple[str, str]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Per-pipeline discovery.
# ---------------------------------------------------------------------------

def _discover_noaa_weather(cache_root: Path) -> PipelineCard | None:
    ns = cache_root / "noaa-weather"
    if not ns.is_dir():
        return None
    card = PipelineCard(
        name="noaa-weather",
        title="NOAA Weather",
        blurb=(
            "Climate reports aggregated from NOAA GHCN-Daily station records. "
            "Each region produces a self-contained HTML bundle plus JSON / MD, "
            "with monthly normals, anomaly bars, warming stripes, and a "
            "climograph."
        ),
    )
    index_rel = "noaa-weather/climate-report/index.html"
    if (cache_root / index_rel).is_file():
        card.primary_links.append(("Master report index", index_rel))
        card.stats.extend(_summarise_report_index(cache_root / index_rel))
    warming_rel = "noaa-weather/climate-report/warming-map.html"
    if (cache_root / warming_rel).is_file():
        card.primary_links.append(("Warming choropleth", warming_rel))
    for rel, label in [
        ("noaa-weather/climate-report/warming-point-map.html", "Annual anomaly (slider)"),
        ("noaa-weather/climate-report/warming-trend-map.html", "Running trend (slider)"),
    ]:
        if (cache_root / rel).is_file():
            card.primary_links.append((label, rel))
    # NDBC marine outputs sit next to the land-station catalog under the
    # same noaa-weather namespace.
    buoys_map_rel = "noaa-weather/ndbc-catalog/buoys-map.html"
    if (cache_root / buoys_map_rel).is_file():
        card.primary_links.append(("NDBC buoys map", buoys_map_rel))

    # Catalog hint — useful for confirming ingestion.
    catalog_rel = "noaa-weather/catalog/stations.txt"
    catalog_meta_rel = catalog_rel + ".meta.json"
    if (cache_root / catalog_meta_rel).is_file():
        meta = _read_sidecar(cache_root / catalog_meta_rel)
        size = meta.get("size_bytes") if meta else None
        if size:
            card.stats.append(
                ("GHCN catalog", f"stations.txt {size / 1024:.0f} KB")
            )
    ndbc_json_meta = cache_root / "noaa-weather/ndbc-catalog/stations.json.meta.json"
    if ndbc_json_meta.is_file():
        meta = _read_sidecar(ndbc_json_meta)
        if meta:
            n = (meta.get("extra") or {}).get("station_count")
            if n:
                card.stats.append(("NDBC catalog", f"{n:,} active buoys"))
    stdmet_root = cache_root / "noaa-weather/ndbc-stdmet"
    if stdmet_root.is_dir():
        stdmet_years = _count_artifacts(stdmet_root)
        if stdmet_years:
            card.stats.append(
                ("NDBC stdmet", f"{stdmet_years:,} cached station-year(s)")
            )
    if not card.primary_links:
        return None
    return card


def _discover_save_earth(cache_root: Path) -> PipelineCard | None:
    ns = cache_root / "save-earth"
    if not ns.is_dir():
        return None
    card = PipelineCard(
        name="save-earth",
        title="Save Earth",
        blurb=(
            "Environmental-action data overlaid on an OSM basemap. "
            "Crowd-sourced litter observations (OpenLitterMap) plus "
            "authoritative EPA remediation sites (Superfund, Brownfields) "
            "rendered as a MapLibre page with per-source layer toggles."
        ),
    )
    maps_root = ns / "maps"
    if maps_root.is_dir():
        for region_dir in sorted(maps_root.iterdir()):
            idx = region_dir / "index.html"
            if idx.is_file():
                rel = idx.relative_to(cache_root).as_posix()
                card.primary_links.append((f"Map — {region_dir.name}", rel))

    # Layer stats.
    for cache_type in ("openlittermap", "epa-cleanups", "tri"):
        sub = ns / cache_type
        if sub.is_dir():
            files = [f for f in sub.iterdir() if f.suffix == ".geojson"]
            if files:
                label = cache_type.replace("-", " ")
                if cache_type == "tri":
                    # Surface the feature count too since TRI is one big file.
                    tri_side = sub / "facilities.geojson.meta.json"
                    if tri_side.is_file():
                        meta = _read_sidecar(tri_side)
                        if meta:
                            n = (meta.get("extra") or {}).get("feature_count")
                            if n:
                                card.stats.append(
                                    ("TRI", f"{n:,} facilities")
                                )
                                continue
                card.stats.append((label, f"{len(files)} cached file(s)"))
    if not card.primary_links:
        return None
    return card


def _discover_osm(cache_root: Path) -> PipelineCard | None:
    ns = cache_root / "osm"
    if not ns.is_dir():
        return None
    card = PipelineCard(
        name="osm",
        title="OSM Geocoder",
        blurb=(
            "OpenStreetMap ingestion pipeline: Geofabrik PBFs → GeoJSON → "
            "vector tiles → interactive MapLibre HTML per region, plus "
            "routing graphs (GraphHopper / Valhalla / OSRM)."
        ),
    )
    html_index = cache_root / "osm/html/index.html"
    if html_index.is_file():
        card.primary_links.append(
            ("Master region index", html_index.relative_to(cache_root).as_posix())
        )

    # Stats across the subsystem.
    for cache_type, label in [
        ("pbf", "PBF files"),
        ("geojson", "GeoJSON"),
        ("vector_tiles", "Vector tiles"),
        ("html", "Region maps"),
        ("graphhopper", "GraphHopper graphs"),
    ]:
        sub = ns / cache_type
        if sub.is_dir():
            count = _count_artifacts(sub)
            if count:
                card.stats.append((label, f"{count} cached entr{'y' if count == 1 else 'ies'}"))
    if not card.primary_links and not card.stats:
        return None
    return card


DISCOVERERS = [_discover_noaa_weather, _discover_save_earth, _discover_osm]


# ---------------------------------------------------------------------------
# Sidecar + stats helpers.
# ---------------------------------------------------------------------------

def _read_sidecar(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("unreadable sidecar %s: %s", path, exc)
        return None


def _count_artifacts(root: Path) -> int:
    """Count non-sidecar files at any depth. Cheap filesystem walk."""
    if not root.is_dir():
        return 0
    total = 0
    for dirpath, _dirs, files in os.walk(root):
        for fn in files:
            if fn.endswith(".meta.json"):
                continue
            total += 1
    return total


def _summarise_report_index(
    index_path: Path,
) -> list[tuple[str, str]]:
    """Pull report count + continent count from the index sidecar."""
    side_path = index_path.parent / (index_path.name + ".meta.json")
    side = _read_sidecar(side_path)
    if not side:
        return []
    extra = side.get("extra") or {}
    rc = extra.get("report_count")
    continents = extra.get("continents") or []
    out: list[tuple[str, str]] = []
    if isinstance(rc, int):
        out.append(("Reports", f"{rc:,}"))
    if continents:
        out.append(("Continents", ", ".join(continents)))
    return out


# ---------------------------------------------------------------------------
# HTML rendering.
# ---------------------------------------------------------------------------

def _render_html(cards: list[PipelineCard]) -> str:
    title = "AFL Data Cache"
    style = textwrap.dedent(
        """\
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
               max-width: 1000px; margin: 2em auto; padding: 0 1.2em; color: #222;
               background: #fafafa; }
        header { border-bottom: 2px solid #333; padding-bottom: 0.6em;
                 margin-bottom: 1.4em; }
        header h1 { margin: 0 0 4px; }
        header p { margin: 0; color: #555; font-size: 14px; }
        .cards { display: grid; grid-template-columns: 1fr 1fr; gap: 1.2em; }
        @media (max-width: 720px) { .cards { grid-template-columns: 1fr; } }
        .card { background: #fff; border-radius: 10px;
                box-shadow: 0 1px 4px rgba(0,0,0,0.08); padding: 16px 18px;
                display: flex; flex-direction: column; }
        .card h2 { margin: 0 0 6px; font-size: 17px;
                   border-bottom: 1px solid #eee; padding-bottom: 6px; }
        .card p.blurb { color: #555; font-size: 13px; margin: 4px 0 10px; }
        .card a.primary { display: inline-block; margin: 3px 8px 3px 0;
                          padding: 5px 10px; background: #1a56db; color: white;
                          border-radius: 5px; text-decoration: none;
                          font-size: 13px; font-weight: 600; }
        .card a.primary:hover { background: #1e429f; }
        .card .stats { margin-top: auto; padding-top: 10px;
                       border-top: 1px dashed #ddd; font-size: 12px; color: #666; }
        .card .stats dl { margin: 0; display: grid;
                          grid-template-columns: auto 1fr; gap: 3px 10px; }
        .card .stats dt { font-weight: 600; color: #444; }
        .card .stats dd { margin: 0; }
        footer { margin-top: 2em; font-size: 12px; color: #888; text-align: center; }
        """
    ).strip()

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    parts: list[str] = []
    parts.append("<!doctype html><html lang='en'><head>")
    parts.append("<meta charset='utf-8'>")
    parts.append(f"<title>{html_mod.escape(title)}</title>")
    parts.append(f"<style>{style}</style>")
    parts.append("</head><body>")
    parts.append("<header>")
    parts.append(f"<h1>{html_mod.escape(title)}</h1>")
    parts.append(
        f"<p>Landing page for every pipeline's outputs. "
        f"Regenerated on every <code>build-cache-index</code> run. "
        f"Last refresh: {html_mod.escape(now)}</p>"
    )
    parts.append("</header>")

    if not cards:
        parts.append(
            "<p style='font-size:14px;color:#666'>No cached pipelines found "
            "under this root. Run one of the example downloaders first.</p>"
        )
    else:
        parts.append("<div class='cards'>")
        for card in cards:
            parts.append(
                f"<div class='card'>"
                f"<h2>{html_mod.escape(card.title)}</h2>"
                f"<p class='blurb'>{html_mod.escape(card.blurb)}</p>"
            )
            for label, href in card.primary_links:
                parts.append(
                    f"<a class='primary' href='{html_mod.escape(href)}'>"
                    f"{html_mod.escape(label)}</a>"
                )
            for label, href in card.secondary_links:
                parts.append(
                    f"<a href='{html_mod.escape(href)}'>"
                    f"{html_mod.escape(label)}</a> "
                )
            if card.stats:
                parts.append("<div class='stats'><dl>")
                for k, v in card.stats:
                    parts.append(
                        f"<dt>{html_mod.escape(k)}</dt>"
                        f"<dd>{html_mod.escape(v)}</dd>"
                    )
                parts.append("</dl></div>")
            parts.append("</div>")
        parts.append("</div>")

    parts.append(
        "<footer>"
        "See <code>agent-spec/cache-layout.agent-spec.yaml</code> "
        "and <code>agent-spec/tools-pattern.agent-spec.yaml</code> for "
        "the per-cache-type contracts."
        "</footer>"
    )
    parts.append("</body></html>")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Sidecar write (minimal, no dependency on the example _lib modules so
# this runs standalone).
# ---------------------------------------------------------------------------

def _write_sidecar_minimal(
    path: Path, *, size_bytes: int, sha256_hex: str, extra: dict[str, Any]
) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    data = {
        "version": 1,
        "namespace": "",
        "cache_type": "",
        "relative_path": path.parent.name + "/" + path.name.replace(".meta.json", ""),
        "kind": "file",
        "size_bytes": size_bytes,
        "sha256": sha256_hex,
        "generated_at": now,
        "tool": {"name": "build-cache-index", "version": "1.0"},
        "extra": extra,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--cache-root",
        type=Path,
        default=None,
        help="Override $AFL_CACHE_ROOT (default: env or $AFL_DATA_ROOT/cache).",
    )
    parser.add_argument(
        "--open",
        action="store_true",
        help="Call ``open`` / ``xdg-open`` on the result.",
    )
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
    )

    cache_root = (args.cache_root or _default_cache_root()).resolve()
    if not cache_root.is_dir():
        print(
            f"error: cache root does not exist: {cache_root}\n"
            f"       Set --cache-root or populate $AFL_CACHE_ROOT / $AFL_DATA_ROOT.",
            file=sys.stderr,
        )
        return 1

    cards: list[PipelineCard] = []
    for discover in DISCOVERERS:
        card = discover(cache_root)
        if card is not None:
            cards.append(card)

    html = _render_html(cards)
    out_path = cache_root / "index.html"
    body_bytes = html.encode("utf-8")
    out_path.write_text(html, encoding="utf-8")
    _write_sidecar_minimal(
        cache_root / "index.html.meta.json",
        size_bytes=len(body_bytes),
        sha256_hex=hashlib.sha256(body_bytes).hexdigest(),
        extra={
            "pipelines": [c.name for c in cards],
            "pipeline_count": len(cards),
        },
    )

    print(f"[cache-index] wrote {out_path} ({len(cards)} pipeline(s))")
    for card in cards:
        print(f"  • {card.title}")

    if args.open:
        import subprocess
        opener = "open" if sys.platform == "darwin" else "xdg-open"
        try:
            subprocess.run([opener, str(out_path)], check=False)
        except FileNotFoundError:
            print(f"warning: '{opener}' not found; open {out_path} manually",
                  file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())

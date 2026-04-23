"""Generate a regional climate report: JSON + Markdown + HTML + SVG charts.

Aggregates the cached per-station GHCN data for a region into:

- ``report.json`` — raw aggregates (annual + monthly + normals + anomalies)
- ``report.md``   — narrative + tables, diffable
- ``report.html`` — self-contained: renders the report and EMBEDS all SVG
                    charts inline (one file you can email / drop in a browser)
- ``climograph.svg``          — monthly temperature curve + precip bars
- ``annual_trend.svg``        — year-over-year mean temp with trendline
- ``warming_stripes.svg``     — coloured stripe per year (Ed Hawkins)
- ``heatmap.svg``             — year × month temperature heatmap
- ``anomaly_bars.svg``        — anomaly vs. 1991–2020 baseline

All outputs land under
``$AFL_CACHE_ROOT/noaa-weather/climate-report/<country>/<region_key>/``
with a sibling ``.meta.json`` sidecar on each file.

Usage::

    # Single state / US region
    climate-report.sh --country US --state NY --start-year 1950 --end-year 2024

    # Geofabrik region
    climate-report.sh --region europe/germany --start-year 1950 --end-year 2024

    # Preview which stations feed the report, no output written
    climate-report.sh --region europe/germany --dry-run
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
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _lib import (  # noqa: E402
    climate_analysis,
    climate_charts,
    geofabrik_regions,
    ghcn_download,
    ghcn_parse,
    sidecar,
)
from _lib.storage import LocalStorage  # noqa: E402

NAMESPACE = "noaa-weather"
CACHE_TYPE = "climate-report"

DEFAULT_BASELINE = (1991, 2020)  # WMO's current 30-year normal
BULK_PROCESS_THRESHOLD = 500


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--country", default="US", help="FIPS country code (default: US)."
    )
    parser.add_argument(
        "--state",
        default="",
        help="State code (tags the report and filters by state bbox).",
    )
    parser.add_argument(
        "--region",
        default="",
        help="Geofabrik region path (overrides country filter unless explicit).",
    )
    parser.add_argument("--min-years", type=int, default=20)
    parser.add_argument(
        "--required",
        action="append",
        default=None,
        help="Required element (repeatable). Default: TMAX TMIN PRCP.",
    )
    parser.add_argument(
        "--max-stations",
        type=int,
        default=0,
        help="Cap on stations. 0 = no cap.",
    )
    parser.add_argument("--start-year", type=int, default=1950)
    parser.add_argument("--end-year", type=int, default=2024)
    parser.add_argument(
        "--baseline",
        default=f"{DEFAULT_BASELINE[0]}-{DEFAULT_BASELINE[1]}",
        help=(
            "Normals baseline window, inclusive. Format: START-END. "
            f"Default WMO standard: {DEFAULT_BASELINE[0]}-{DEFAULT_BASELINE[1]}."
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help=(
            "Write outputs here instead of the namespaced cache path. "
            "Sidecars are still produced for every file."
        ),
    )
    parser.add_argument(
        "--force-catalog",
        action="store_true",
        help="Re-download the GHCN catalog + inventory even if cached.",
    )
    parser.add_argument(
        "--force-download",
        action="store_true",
        help="Re-download station CSVs even if cached.",
    )
    parser.add_argument(
        "--use-mock",
        action="store_true",
        help=(
            "Opt in to deterministic mock data instead of live NOAA fetches. "
            "Default is real data."
        ),
    )
    parser.add_argument(
        "--i-know-this-is-huge",
        action="store_true",
        help=(
            f"Override the {BULK_PROCESS_THRESHOLD}-station safety guard."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List the stations that would feed the report, but don't aggregate.",
    )
    parser.add_argument(
        "--log-level", default="INFO", help="Python logging level (default: INFO)."
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
    )
    log = logging.getLogger("noaa-weather.report")

    baseline = _parse_baseline(args.baseline)
    country_explicit = any(
        a == "--country" or a.startswith("--country=") for a in sys.argv[1:]
    )
    country_filter = args.country
    if args.region and not country_explicit:
        country_filter = ""

    # --- 1. Resolve the station list from the cache. -----------------------
    stations_text = ghcn_download.read_catalog_file(
        "stations", force=args.force_catalog, use_mock=args.use_mock or None
    )
    inventory_text = ghcn_download.read_catalog_file(
        "inventory", force=args.force_catalog, use_mock=args.use_mock or None
    )
    stations = ghcn_parse.parse_stations(stations_text)
    inventory = ghcn_parse.parse_inventory(inventory_text)

    bbox = None
    region_info = None
    if args.region:
        try:
            region_info = geofabrik_regions.resolve_region(
                args.region, use_mock=args.use_mock or None
            )
        except KeyError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        bbox = region_info.bbox

    cap = args.max_stations if args.max_stations > 0 else len(stations)
    filtered = ghcn_parse.filter_stations(
        stations,
        inventory,
        country=country_filter,
        state=args.state,
        bbox=bbox,
        max_stations=cap,
        min_years=args.min_years,
        required_elements=args.required,
    )

    if not filtered:
        print("error: no stations matched the filter — widen --state / --region / --min-years", file=sys.stderr)
        return 1

    if len(filtered) > BULK_PROCESS_THRESHOLD and not args.i_know_this_is_huge:
        print(
            f"error: filter resolved to {len(filtered):,} stations, above the "
            f"{BULK_PROCESS_THRESHOLD}-station safety threshold.\n"
            f"       Narrow the filter or pass --i-know-this-is-huge.",
            file=sys.stderr,
        )
        return 2

    region_label = _region_label(args, region_info)
    log.info(
        "resolved %d station(s) for report %r (years %d-%d)",
        len(filtered),
        region_label,
        args.start_year,
        args.end_year,
    )

    if args.dry_run:
        print(f"# dry-run: report would aggregate {len(filtered):,} station(s)")
        for s in filtered:
            print(
                f"{s['station_id']}  {s.get('name', '')}  "
                f"inv-years={s.get('first_year')}-{s.get('last_year')}"
            )
        return 0

    # --- 2. Pull daily data and roll up per-station. -----------------------
    annual_rows: list[dict[str, Any]] = []
    monthly_rows: list[dict[str, Any]] = []
    station_meta: list[dict[str, Any]] = []
    for idx, s in enumerate(filtered, 1):
        sid = s["station_id"]
        log.info(
            "[%d/%d] %s (%s) — downloading + parsing",
            idx,
            len(filtered),
            sid,
            s.get("name", "")[:40],
        )
        extra = {
            "name": s.get("name"),
            "lat": s.get("lat"),
            "lon": s.get("lon"),
            "elevation": s.get("elevation"),
            "first_year": s.get("first_year"),
            "last_year": s.get("last_year"),
            "elements": s.get("elements"),
        }
        res = ghcn_download.download_station_csv(
            sid,
            force=args.force_download,
            use_mock=args.use_mock or None,
            extra_metadata=extra,
        )
        daily = ghcn_parse.parse_ghcn_csv(
            res.absolute_path, args.start_year, args.end_year
        )
        if not daily:
            continue
        annual_rows.extend(
            climate_analysis.compute_yearly_summaries(
                daily, station_id=sid, state=args.state
            )
        )
        monthly_rows.extend(
            climate_analysis.compute_monthly_summaries(
                daily, station_id=sid, state=args.state
            )
        )
        station_meta.append(
            {
                "station_id": sid,
                "name": s.get("name"),
                "lat": s.get("lat"),
                "lon": s.get("lon"),
                "elevation": s.get("elevation"),
                "first_year": s.get("first_year"),
                "last_year": s.get("last_year"),
            }
        )

    if not annual_rows:
        print("error: no daily data in year range for any resolved station", file=sys.stderr)
        return 1

    # --- 3. Regional rollups ----------------------------------------------
    regional_annual = _aggregate_annual(annual_rows, state=args.state)
    regional_monthly = _aggregate_monthly(monthly_rows, state=args.state)
    normals = climate_analysis.monthly_climate_normals(
        regional_monthly, baseline_start=baseline[0], baseline_end=baseline[1]
    )
    anomalies = climate_analysis.annual_anomalies(
        regional_annual, baseline_start=baseline[0], baseline_end=baseline[1]
    )
    trend = climate_analysis.aggregate_region_trend(
        annual_rows,
        state=args.state,
        start_year=args.start_year,
        end_year=args.end_year,
    )

    report: dict[str, Any] = {
        "region": {
            "country": country_filter,
            "state": args.state,
            "path": region_info.path if region_info else None,
            "name": region_info.name if region_info else None,
            "bbox": list(region_info.bbox) if region_info else None,
            "label": region_label,
        },
        "year_range": [args.start_year, args.end_year],
        "baseline": list(baseline),
        "station_count": len(station_meta),
        "stations": station_meta,
        "annual": regional_annual,
        "monthly": regional_monthly,
        "monthly_normals": {str(m): v for m, v in normals.items()},
        "anomalies": anomalies,
        "trend": {
            "warming_rate_per_decade": trend["warming_rate_per_decade"],
            "precip_change_pct": trend["precip_change_pct"],
            "decades": trend["decades"],
            "narrative": trend["narrative"],
        },
    }

    # --- 4. Chart generation ----------------------------------------------
    charts: dict[str, str] = {}
    charts["climograph.svg"] = climate_charts.climograph(
        normals, region_label=region_label, baseline=baseline
    )
    charts["annual_trend.svg"] = climate_charts.annual_trend(
        regional_annual,
        region_label=region_label,
        slope_per_decade=trend["warming_rate_per_decade"],
    )
    charts["warming_stripes.svg"] = climate_charts.warming_stripes(
        regional_annual, region_label=region_label
    )
    charts["heatmap.svg"] = climate_charts.year_month_heatmap(
        regional_monthly, region_label=region_label, value_field="temp_mean"
    )
    charts["anomaly_bars.svg"] = climate_charts.anomaly_bars(
        anomalies, region_label=region_label, baseline=baseline
    )

    # --- 5. Markdown + HTML ------------------------------------------------
    md = _render_markdown(report, list(charts.keys()))
    html = _render_html(report, charts, md)

    # --- 6. Write everything to the cache ---------------------------------
    country_dir = args.country or "ALL"
    region_dir = _region_key(args, region_info)
    out_dir = _resolve_output_dir(args, country_dir, region_dir)
    _write_outputs(
        out_dir=out_dir,
        country_dir=country_dir,
        region_dir=region_dir,
        report=report,
        md=md,
        html=html,
        charts=charts,
    )
    log.info("wrote report bundle to %s", out_dir)
    print(f"[report] {out_dir}")
    return 0


# ---------------------------------------------------------------------------
# Aggregation helpers.
# ---------------------------------------------------------------------------

def _aggregate_annual(
    per_station_yearly: list[dict[str, Any]], *, state: str
) -> list[dict[str, Any]]:
    """Collapse per-station annual rows into per-year regional averages."""
    by_year: dict[int, list[dict[str, Any]]] = {}
    for r in per_station_yearly:
        y = r.get("year")
        if isinstance(y, int):
            by_year.setdefault(y, []).append(r)
    out: list[dict[str, Any]] = []
    for y in sorted(by_year):
        recs = by_year[y]
        temps = [r["temp_mean"] for r in recs if r.get("temp_mean") is not None]
        precips = [r["precip_annual"] for r in recs if r.get("precip_annual") is not None]
        hot = sum(r.get("hot_days", 0) or 0 for r in recs)
        frost = sum(r.get("frost_days", 0) or 0 for r in recs)
        if not temps:
            continue
        out.append(
            {
                "state": state,
                "year": y,
                "station_count": len(recs),
                "temp_mean": round(sum(temps) / len(temps), 2),
                "temp_min_avg": round(
                    sum(r["temp_min_avg"] for r in recs if r.get("temp_min_avg") is not None)
                    / max(1, sum(1 for r in recs if r.get("temp_min_avg") is not None)),
                    2,
                ),
                "temp_max_avg": round(
                    sum(r["temp_max_avg"] for r in recs if r.get("temp_max_avg") is not None)
                    / max(1, sum(1 for r in recs if r.get("temp_max_avg") is not None)),
                    2,
                ),
                "precip_annual": round(sum(precips) / len(precips), 1) if precips else 0.0,
                "hot_days": hot,
                "frost_days": frost,
            }
        )
    return out


def _aggregate_monthly(
    per_station_monthly: list[dict[str, Any]], *, state: str
) -> list[dict[str, Any]]:
    """Collapse per-station (year, month) rows into regional (year, month) averages."""
    by_ym: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for r in per_station_monthly:
        y = r.get("year")
        m = r.get("month")
        if isinstance(y, int) and isinstance(m, int):
            by_ym.setdefault((y, m), []).append(r)

    out: list[dict[str, Any]] = []
    for (y, m) in sorted(by_ym):
        recs = by_ym[(y, m)]
        temps = [r["temp_mean"] for r in recs if r.get("temp_mean") is not None]
        mins = [r["temp_min_avg"] for r in recs if r.get("temp_min_avg") is not None]
        maxs = [r["temp_max_avg"] for r in recs if r.get("temp_max_avg") is not None]
        precs = [r["precip_total"] for r in recs if r.get("precip_total") is not None]
        if not temps:
            continue
        out.append(
            {
                "state": state,
                "year": y,
                "month": m,
                "station_count": len(recs),
                "temp_mean": round(sum(temps) / len(temps), 2),
                "temp_min_avg": round(sum(mins) / len(mins), 2) if mins else None,
                "temp_max_avg": round(sum(maxs) / len(maxs), 2) if maxs else None,
                "precip_total": round(sum(precs) / len(precs), 1) if precs else None,
            }
        )
    return out


# ---------------------------------------------------------------------------
# Markdown + HTML renderers.
# ---------------------------------------------------------------------------

MONTH_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _render_markdown(report: dict[str, Any], chart_files: list[str]) -> str:
    region = report["region"]["label"]
    yrs = report["year_range"]
    base = report["baseline"]
    trend = report["trend"]
    annual = report["annual"]
    normals = report["monthly_normals"]

    lines: list[str] = []
    lines.append(f"# Climate report — {region}")
    lines.append("")
    lines.append(
        f"- **Year range**: {yrs[0]}–{yrs[1]}"
    )
    lines.append(
        f"- **Baseline (climate normals)**: {base[0]}–{base[1]} (WMO 30-year standard)"
    )
    lines.append(f"- **Stations contributing**: {report['station_count']}")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(trend["narrative"])
    lines.append("")
    lines.append(
        f"- **Warming rate**: {trend['warming_rate_per_decade']:+.2f} °C / decade"
    )
    lines.append(
        f"- **Precipitation change** (first vs last year in range): "
        f"{trend['precip_change_pct']:+.1f} %"
    )
    lines.append("")

    # Charts referenced.
    lines.append("## Charts")
    lines.append("")
    for f in chart_files:
        lines.append(f"- [{f}]({f})")
    lines.append("")

    # Monthly normals table.
    lines.append("## Monthly climate normals")
    lines.append("")
    lines.append(
        "| Month | Mean °C | Min °C | Max °C | Precip (mm) | Years |"
    )
    lines.append("|---|---:|---:|---:|---:|---:|")
    for m in range(1, 13):
        n = normals[str(m)]
        lines.append(
            f"| {MONTH_ABBR[m - 1]} "
            f"| {_fmt(n['temp_mean'])} "
            f"| {_fmt(n['temp_min_avg'])} "
            f"| {_fmt(n['temp_max_avg'])} "
            f"| {_fmt(n['precip_total'])} "
            f"| {n['years_counted']} |"
        )
    lines.append("")

    # Decadal comparison.
    lines.append("## Decadal comparison")
    lines.append("")
    lines.append("| Decade | Avg temp °C | Avg precip (mm) | Years w/ data |")
    lines.append("|---|---:|---:|---:|")
    for dec, vals in sorted(trend["decades"].items()):
        lines.append(
            f"| {dec} | {vals['avg_temp']} | {vals['avg_precip']} | {vals['years_with_data']} |"
        )
    lines.append("")

    # Annual time series — condensed if long.
    lines.append("## Annual time series")
    lines.append("")
    lines.append("| Year | Mean °C | Min °C | Max °C | Precip (mm) | Hot days | Frost days | Stations |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|")
    for r in annual:
        lines.append(
            f"| {r['year']} "
            f"| {_fmt(r.get('temp_mean'))} "
            f"| {_fmt(r.get('temp_min_avg'))} "
            f"| {_fmt(r.get('temp_max_avg'))} "
            f"| {_fmt(r.get('precip_annual'))} "
            f"| {r.get('hot_days', 0)} "
            f"| {r.get('frost_days', 0)} "
            f"| {r.get('station_count', 0)} |"
        )
    lines.append("")

    # Station list.
    lines.append("## Stations contributing")
    lines.append("")
    lines.append("| Station ID | Name | Lat | Lon | Elev m | Inv. years |")
    lines.append("|---|---|---:|---:|---:|---|")
    for s in report["stations"]:
        lines.append(
            f"| {s['station_id']} | {s.get('name') or ''} "
            f"| {_fmt(s.get('lat'))} | {_fmt(s.get('lon'))} "
            f"| {_fmt(s.get('elevation'))} "
            f"| {s.get('first_year')}-{s.get('last_year')} |"
        )
    lines.append("")

    return "\n".join(lines)


def _render_html(
    report: dict[str, Any],
    charts: dict[str, str],
    markdown_text: str,
) -> str:
    region = report["region"]["label"]
    # Strip each SVG's XML prolog so they nest cleanly inside HTML.
    embedded: list[tuple[str, str]] = []
    for name, svg in charts.items():
        body = svg
        if "<svg" in body:
            body = body[body.index("<svg"):]
        embedded.append((name, body))

    # Simple manual Markdown → HTML (tables, headers, lists, paragraphs).
    md_html = _markdown_to_html(markdown_text)

    style = textwrap.dedent(
        """
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
               max-width: 960px; margin: 2em auto; padding: 0 1em; color: #222; }
        h1 { border-bottom: 2px solid #333; padding-bottom: 0.2em; }
        h2 { border-bottom: 1px solid #bbb; padding-bottom: 0.15em; margin-top: 2em; }
        table { border-collapse: collapse; margin: 0.8em 0; }
        th, td { border: 1px solid #ddd; padding: 4px 8px; text-align: left; }
        th { background: #f3f3f3; }
        td.num { text-align: right; font-variant-numeric: tabular-nums; }
        .chart { margin: 1.2em 0; }
        .chart figcaption { font-size: 0.9em; color: #555; margin-top: 0.3em; }
        code { background: #f5f5f5; padding: 1px 4px; border-radius: 3px; }
        """
    ).strip()

    chart_block_parts = []
    for name, svg in embedded:
        caption = html_mod.escape(name)
        chart_block_parts.append(
            f'<figure class="chart" id="{html_mod.escape(name)}">'
            f'{svg}'
            f'<figcaption>{caption}</figcaption>'
            f'</figure>'
        )
    chart_block = "\n".join(chart_block_parts)

    title = html_mod.escape(f"Climate report — {region}")
    return textwrap.dedent(
        f"""\
        <!doctype html>
        <html lang="en">
        <head>
          <meta charset="utf-8">
          <title>{title}</title>
          <style>{style}</style>
        </head>
        <body>
        {md_html}
        <h2 id="embedded-charts">Embedded charts</h2>
        {chart_block}
        </body>
        </html>
        """
    )


def _markdown_to_html(md: str) -> str:
    """Deliberately tiny Markdown → HTML — handles what this tool emits.

    Supports: H1/H2, unordered lists, pipe tables (with header + |---|),
    paragraphs. Ignores inline formatting beyond bold ``**x**``.
    """
    out: list[str] = []
    lines = md.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            i += 1
            continue

        if stripped.startswith("# "):
            out.append(f"<h1>{_inline(stripped[2:])}</h1>")
            i += 1
            continue
        if stripped.startswith("## "):
            out.append(f"<h2>{_inline(stripped[3:])}</h2>")
            i += 1
            continue

        if stripped.startswith("- "):
            out.append("<ul>")
            while i < len(lines) and lines[i].strip().startswith("- "):
                out.append(f"<li>{_inline(lines[i].strip()[2:])}</li>")
                i += 1
            out.append("</ul>")
            continue

        if stripped.startswith("|") and i + 1 < len(lines) and "---" in lines[i + 1]:
            # Pipe table.
            header_cells = [c.strip() for c in stripped.strip("|").split("|")]
            sep_cells = lines[i + 1].strip().strip("|").split("|")
            aligns = ["right" if "---:" in c else "left" for c in sep_cells]
            out.append("<table>")
            out.append("<thead><tr>")
            for c in header_cells:
                out.append(f"<th>{_inline(c)}</th>")
            out.append("</tr></thead>")
            out.append("<tbody>")
            i += 2
            while i < len(lines) and lines[i].strip().startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                out.append("<tr>")
                for j, c in enumerate(cells):
                    klass = ' class="num"' if j < len(aligns) and aligns[j] == "right" else ""
                    out.append(f"<td{klass}>{_inline(c)}</td>")
                out.append("</tr>")
                i += 1
            out.append("</tbody></table>")
            continue

        # Paragraph — collect consecutive non-empty non-special lines.
        buf = [stripped]
        i += 1
        while (
            i < len(lines)
            and lines[i].strip()
            and not lines[i].strip().startswith(("#", "-", "|"))
        ):
            buf.append(lines[i].strip())
            i += 1
        out.append(f"<p>{_inline(' '.join(buf))}</p>")
    return "\n".join(out)


def _inline(text: str) -> str:
    escaped = html_mod.escape(text)
    # Very minimal bold support: **text** → <strong>text</strong>
    out: list[str] = []
    in_b = False
    buf = ""
    i = 0
    while i < len(escaped):
        if escaped[i : i + 2] == "**":
            if buf:
                out.append(buf)
                buf = ""
            out.append("</strong>" if in_b else "<strong>")
            in_b = not in_b
            i += 2
        else:
            buf += escaped[i]
            i += 1
    if buf:
        out.append(buf)
    return "".join(out)


def _fmt(v: Any) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:g}"
    return str(v)


# ---------------------------------------------------------------------------
# I/O: writing the bundle + sidecars.
# ---------------------------------------------------------------------------

def _parse_baseline(s: str) -> tuple[int, int]:
    try:
        start_s, end_s = s.split("-")
        start, end = int(start_s), int(end_s)
    except (ValueError, TypeError):
        raise SystemExit(f"error: --baseline must be START-END, got {s!r}")
    if start >= end:
        raise SystemExit(f"error: --baseline start must be < end (got {s!r})")
    return start, end


def _region_label(args: argparse.Namespace, region_info) -> str:
    if region_info is not None:
        return region_info.name
    if args.state:
        return f"{args.country}/{args.state}"
    return args.country


def _region_key(args: argparse.Namespace, region_info) -> str:
    """Stable on-disk key for the output directory. Mirrors discover/trend."""
    if region_info is not None:
        return region_info.path.replace("/", "__")
    state = args.state or "ALL"
    return state


def _resolve_output_dir(
    args: argparse.Namespace, country_dir: str, region_dir: str
) -> Path:
    if args.output_dir:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        return args.output_dir
    relative_dir = f"{country_dir}/{region_dir}"
    abs_dir = sidecar.cache_path(NAMESPACE, CACHE_TYPE, relative_dir, LocalStorage())
    Path(abs_dir).mkdir(parents=True, exist_ok=True)
    return Path(abs_dir)


def _write_outputs(
    *,
    out_dir: Path,
    country_dir: str,
    region_dir: str,
    report: dict[str, Any],
    md: str,
    html: str,
    charts: dict[str, str],
) -> None:
    storage = LocalStorage()

    def _write(name: str, text: str, content_kind: str) -> None:
        file_path = out_dir / name
        file_path.write_text(text, encoding="utf-8")
        body_bytes = text.encode("utf-8")
        relative_path = f"{country_dir}/{region_dir}/{name}"
        sidecar.write_sidecar(
            NAMESPACE,
            CACHE_TYPE,
            relative_path,
            kind="file",
            size_bytes=len(body_bytes),
            sha256=hashlib.sha256(body_bytes).hexdigest(),
            tool={"name": "climate_report", "version": "1.0"},
            extra={
                "content_kind": content_kind,
                "region": report["region"],
                "year_range": report["year_range"],
                "baseline": report["baseline"],
                "station_count": report["station_count"],
            },
            storage=storage,
        )

    _write(
        "report.json",
        json.dumps(report, indent=2, sort_keys=True, default=_json_default) + "\n",
        "json",
    )
    _write("report.md", md + ("\n" if not md.endswith("\n") else ""), "markdown")
    _write("report.html", html, "html")
    for chart_name, svg in charts.items():
        _write(chart_name, svg, "svg")


def _json_default(obj: Any) -> Any:
    if isinstance(obj, Path):
        return str(obj)
    raise TypeError(f"not JSON-serializable: {type(obj).__name__}")


if __name__ == "__main__":
    sys.exit(main())

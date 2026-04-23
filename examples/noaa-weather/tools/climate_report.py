"""Generate a regional climate report: JSON + Markdown + HTML + SVG charts.

Thin CLI over :func:`_lib.climate_report.generate_climate_report`. Both
this tool and the FFL handler ``weather.Report.GenerateClimateReport``
call the same core function, so the terminal run and the runtime
produce identical output and share the same cache.

Output bundle at ``cache/noaa-weather/climate-report/<country>/<region>/``:

  report.json / report.md / report.html + 5 SVG charts
  (climograph, annual_trend, warming_stripes, heatmap, anomaly_bars)

Usage::

    climate-report.sh --country US --state NY --start-year 1950 --end-year 2026
    climate-report.sh --region europe/germany --start-year 1950 --end-year 2026
    climate-report.sh --region europe/germany --dry-run
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _lib import geofabrik_regions, ghcn_download, ghcn_parse  # noqa: E402
from _lib.climate_report import (  # noqa: E402
    DEFAULT_BASELINE,
    DEFAULT_BULK_THRESHOLD,
    ReportError,
    generate_climate_report,
)


def _parse_baseline(s: str) -> tuple[int, int]:
    try:
        start_s, end_s = s.split("-")
        start, end = int(start_s), int(end_s)
    except (ValueError, TypeError):
        raise SystemExit(f"error: --baseline must be START-END, got {s!r}")
    if start >= end:
        raise SystemExit(f"error: --baseline start must be < end (got {s!r})")
    return start, end


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument("--country", default="US", help="FIPS country code (default: US).")
    parser.add_argument("--state", default="", help="State code (tags + bbox-filters).")
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
    parser.add_argument("--max-stations", type=int, default=0, help="Cap on stations. 0 = no cap.")
    parser.add_argument("--start-year", type=int, default=1950)
    parser.add_argument("--end-year", type=int, default=2026)
    parser.add_argument(
        "--baseline",
        default=f"{DEFAULT_BASELINE[0]}-{DEFAULT_BASELINE[1]}",
        help=(
            f"Normals baseline window, inclusive. Format: START-END. "
            f"Default WMO standard: {DEFAULT_BASELINE[0]}-{DEFAULT_BASELINE[1]}."
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Write outputs here instead of the namespaced cache path.",
    )
    parser.add_argument("--force-catalog", action="store_true")
    parser.add_argument("--force-download", action="store_true")
    parser.add_argument("--use-mock", action="store_true")
    parser.add_argument(
        "--i-know-this-is-huge",
        action="store_true",
        help=f"Override the {DEFAULT_BULK_THRESHOLD}-station safety guard.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List the stations that would feed the report, but don't aggregate.",
    )
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
    )

    baseline = _parse_baseline(args.baseline)
    country_explicit = any(
        a == "--country" or a.startswith("--country=") for a in sys.argv[1:]
    )

    # Dry-run resolves station list without generating charts / writing output.
    if args.dry_run:
        return _dry_run(args, country_explicit)

    try:
        bundle = generate_climate_report(
            country=args.country,
            state=args.state,
            region=args.region,
            start_year=args.start_year,
            end_year=args.end_year,
            baseline=baseline,
            min_years=args.min_years,
            required_elements=args.required,
            max_stations=args.max_stations,
            force_catalog=args.force_catalog,
            force_download=args.force_download,
            use_mock=args.use_mock or None,
            output_dir=args.output_dir,
            override_bulk_guard=args.i_know_this_is_huge,
            country_explicit=country_explicit,
        )
    except KeyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except ReportError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(f"[report] {bundle.output_dir}")
    return 0


def _dry_run(args: argparse.Namespace, country_explicit: bool) -> int:
    """Resolve the station list only, for a --dry-run preview."""
    country_filter = args.country
    if args.region and not country_explicit:
        country_filter = ""

    stations_text = ghcn_download.read_catalog_file(
        "stations", force=args.force_catalog, use_mock=args.use_mock or None
    )
    inventory_text = ghcn_download.read_catalog_file(
        "inventory", force=args.force_catalog, use_mock=args.use_mock or None
    )
    stations = ghcn_parse.parse_stations(stations_text)
    inventory = ghcn_parse.parse_inventory(inventory_text)

    bbox = None
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
    print(f"# dry-run: report would aggregate {len(filtered):,} station(s)")
    for s in filtered:
        print(
            f"{s['station_id']}  {s.get('name', '')}  "
            f"inv-years={s.get('first_year')}-{s.get('last_year')}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())

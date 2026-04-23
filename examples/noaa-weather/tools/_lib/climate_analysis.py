"""Climate analysis — yearly summaries and trend regression (pure, no I/O).

Operates on the daily-record shape produced by
:func:`_lib.ghcn_parse.parse_ghcn_csv`. Emits yearly summary dicts that
downstream code (CLI tools, handlers) can persist to files, MongoDB,
or pass further up the pipeline.
"""

from __future__ import annotations

from typing import Any

# Thresholds used for the "hot" / "frost" day counts.
HOT_DAY_TMAX_C = 35.0
FROST_DAY_TMIN_C = 0.0


def compute_yearly_summaries(
    daily_data: list[dict[str, Any]],
    station_id: str = "",
    state: str = "",
) -> list[dict[str, Any]]:
    """Group daily data by year and compute annual climate summaries.

    Input dicts must have ``date`` (YYYYMMDD), ``tmax``, ``tmin``, ``prcp``
    (None allowed for missing). Output has one dict per year with keys:

    ``year``, ``station_id``, ``state``, ``temp_mean``, ``temp_min_avg``,
    ``temp_max_avg``, ``precip_annual``, ``hot_days``, ``frost_days``,
    ``precip_days``, ``obs_days``.
    """
    by_year: dict[int, list[dict[str, Any]]] = {}
    for d in daily_data:
        date_str = d.get("date", "")
        if len(date_str) < 4:
            continue
        try:
            year = int(date_str[:4])
        except ValueError:
            continue
        by_year.setdefault(year, []).append(d)

    summaries: list[dict[str, Any]] = []
    for year in sorted(by_year):
        days = by_year[year]
        tmaxs = [d["tmax"] for d in days if d.get("tmax") is not None]
        tmins = [d["tmin"] for d in days if d.get("tmin") is not None]
        prcps = [d["prcp"] for d in days if d.get("prcp") is not None]

        daily_means: list[float] = [
            (d["tmax"] + d["tmin"]) / 2.0
            for d in days
            if d.get("tmax") is not None and d.get("tmin") is not None
        ]

        temp_mean = round(sum(daily_means) / len(daily_means), 2) if daily_means else None
        temp_min_avg = round(sum(tmins) / len(tmins), 2) if tmins else None
        temp_max_avg = round(sum(tmaxs) / len(tmaxs), 2) if tmaxs else None
        precip_annual = round(sum(prcps), 1) if prcps else 0.0

        hot_days = sum(1 for t in tmaxs if t > HOT_DAY_TMAX_C)
        frost_days = sum(1 for t in tmins if t < FROST_DAY_TMIN_C)
        precip_days = sum(1 for p in prcps if p > 0.0)

        summaries.append(
            {
                "year": year,
                "station_id": station_id,
                "state": state,
                "temp_mean": temp_mean,
                "temp_min_avg": temp_min_avg,
                "temp_max_avg": temp_max_avg,
                "precip_annual": precip_annual,
                "hot_days": hot_days,
                "frost_days": frost_days,
                "precip_days": precip_days,
                "obs_days": len(days),
            }
        )

    return summaries


def simple_linear_regression(
    xs: list[float],
    ys: list[float],
) -> tuple[float, float]:
    """OLS regression. Returns ``(slope, intercept)``.

    Degenerate inputs: empty → ``(0.0, 0.0)``; one point → ``(0.0, ys[0])``;
    vertical (zero x-variance) → ``(0.0, mean(ys))``.
    """
    n = len(xs)
    if n == 0:
        return 0.0, 0.0
    if n == 1:
        return 0.0, ys[0]

    sum_x = sum(xs)
    sum_y = sum(ys)
    sum_xy = sum(x * y for x, y in zip(xs, ys))
    sum_xx = sum(x * x for x in xs)

    denom = n * sum_xx - sum_x * sum_x
    if abs(denom) < 1e-12:
        return 0.0, sum_y / n

    slope = (n * sum_xy - sum_x * sum_y) / denom
    intercept = (sum_y - slope * sum_x) / n
    return slope, intercept


def aggregate_region_trend(
    yearly_records: list[dict[str, Any]],
    *,
    state: str,
    start_year: int,
    end_year: int,
) -> dict[str, Any]:
    """Aggregate per-station yearly records into a region trend.

    Each input record must have keys ``year``, ``temp_mean``,
    ``precip_annual``, ``hot_days``, ``frost_days`` (station_id /
    metadata ignored). Records from different stations for the same
    year are averaged.

    Returns a dict with ``state``, ``start_year``, ``end_year``,
    ``years_data`` (per-year aggregate rows), ``warming_rate_per_decade``,
    ``precip_change_pct``, ``decades``, and ``narrative``.
    """
    by_year: dict[int, list[dict[str, Any]]] = {}
    for r in yearly_records:
        yr = r.get("year")
        if yr is None:
            continue
        by_year.setdefault(yr, []).append(r)

    years_data: list[dict[str, Any]] = []
    for yr in sorted(by_year):
        recs = by_year[yr]
        temps = [r["temp_mean"] for r in recs if r.get("temp_mean") is not None]
        precips = [r["precip_annual"] for r in recs if r.get("precip_annual") is not None]
        if not temps:
            continue
        years_data.append(
            {
                "state": state,
                "year": yr,
                "station_count": len(recs),
                "temp_mean": round(sum(temps) / len(temps), 2),
                "temp_min_avg": round(min(temps), 2),
                "temp_max_avg": round(max(temps), 2),
                "precip_annual": round(sum(precips) / len(precips), 1) if precips else 0.0,
                "hot_days": sum(r.get("hot_days", 0) or 0 for r in recs),
                "frost_days": sum(r.get("frost_days", 0) or 0 for r in recs),
                "precip_days": 0,
            }
        )

    xs = [float(d["year"]) for d in years_data]
    ys_temp = [d["temp_mean"] for d in years_data]
    ys_precip = [d["precip_annual"] for d in years_data]

    slope_temp, _ = simple_linear_regression(xs, ys_temp) if len(xs) >= 2 else (0.0, 0.0)
    warming_per_decade = round(slope_temp * 10, 2)

    if len(ys_precip) >= 2 and ys_precip[0] != 0:
        precip_change_pct = round((ys_precip[-1] - ys_precip[0]) / abs(ys_precip[0]) * 100, 2)
    else:
        precip_change_pct = 0.0

    decades: dict[str, dict[str, Any]] = {}
    for d in years_data:
        decade = f"{(d['year'] // 10) * 10}s"
        dec = decades.setdefault(decade, {"temps": [], "precips": [], "count": 0})
        dec["temps"].append(d["temp_mean"])
        dec["precips"].append(d["precip_annual"])
        dec["count"] += 1

    decades_summary: dict[str, dict[str, Any]] = {}
    for dec_name, dec_data in decades.items():
        decades_summary[dec_name] = {
            "avg_temp": round(sum(dec_data["temps"]) / len(dec_data["temps"]), 2),
            "avg_precip": (
                round(sum(dec_data["precips"]) / len(dec_data["precips"]), 1)
                if dec_data["precips"]
                else 0.0
            ),
            "years_with_data": dec_data["count"],
        }

    region = state if state else "the region"
    direction = "warmed" if warming_per_decade > 0 else "cooled"
    narrative = (
        f"Climate analysis for {region} from {start_year} to {end_year}. "
        f"Temperatures have {direction} at {abs(warming_per_decade)}°C per decade. "
        f"Annual precipitation has {'increased' if precip_change_pct > 0 else 'decreased'} "
        f"by {abs(precip_change_pct)}%."
    )

    return {
        "state": state,
        "start_year": start_year,
        "end_year": end_year,
        "years_data": years_data,
        "warming_rate_per_decade": warming_per_decade,
        "precip_change_pct": precip_change_pct,
        "decades": decades_summary,
        "narrative": narrative,
    }

"""Climate aggregation and trend handlers for the noaa-weather example."""

from __future__ import annotations

import json
import os
from typing import Any

from handlers.shared.weather_utils import (
    ClimateStore,
    WeatherReportStore,
    compute_annual_summary,
    compute_daily_stats,
    compute_missing_pct,
    download_isd_lite,
    get_weather_db,
    parse_isd_lite_file,
    simple_linear_regression,
    validate_temperature_range,
)

NAMESPACE = "climate.Aggregate"


# ---------------------------------------------------------------------------
# AggregateStateYear
# ---------------------------------------------------------------------------


def handle_aggregate_state_year(params: dict[str, Any]) -> dict[str, Any]:
    """Aggregate station weather_reports for a state+year into a climate summary."""
    state = params.get("state", "")
    year = params.get("year", 2023)
    if isinstance(year, str):
        year = int(year)

    db = get_weather_db()
    reports = list(
        db["weather_reports"].find(
            {"report.state": state, "year": year},
            {"_id": 0},
        )
    )

    # If no reports matched on report.state, try the location field
    if not reports:
        reports = list(
            db["weather_reports"].find(
                {"year": year},
                {"_id": 0},
            )
        )
        # Filter by state appearing in location string
        if state:
            reports = [r for r in reports if state.lower() in (r.get("location") or "").lower()]

    station_count = len(reports)

    if station_count == 0:
        yearly = {
            "state": state,
            "year": year,
            "station_count": 0,
            "temp_mean": 0.0,
            "temp_min_avg": 0.0,
            "temp_max_avg": 0.0,
            "precip_annual": 0.0,
            "hot_days": 0,
            "frost_days": 0,
            "precip_days": 0,
        }
        climate_store = ClimateStore(db)
        climate_store.upsert_state_year(yearly)
        return {"yearly": yearly}

    # Aggregate across stations
    all_temp_means: list[float] = []
    all_temp_mins: list[float] = []
    all_temp_maxs: list[float] = []
    total_precip = 0.0
    total_hot_days = 0
    total_frost_days = 0
    total_precip_days = 0

    for report in reports:
        daily_stats = report.get("daily_stats", [])
        if isinstance(daily_stats, str):
            daily_stats = json.loads(daily_stats)

        for day in daily_stats:
            t_mean = day.get("temp_mean")
            t_min = day.get("temp_min")
            t_max = day.get("temp_max")
            precip = day.get("precip_total", 0) or 0

            if t_mean is not None:
                all_temp_means.append(float(t_mean))
            if t_min is not None:
                all_temp_mins.append(float(t_min))
                if float(t_min) < 0:
                    total_frost_days += 1
            if t_max is not None:
                all_temp_maxs.append(float(t_max))
                if float(t_max) > 35:
                    total_hot_days += 1
            if precip > 0:
                total_precip_days += 1
            total_precip += float(precip)

    yearly = {
        "state": state,
        "year": year,
        "station_count": station_count,
        "temp_mean": round(sum(all_temp_means) / len(all_temp_means), 2) if all_temp_means else 0.0,
        "temp_min_avg": round(sum(all_temp_mins) / len(all_temp_mins), 2) if all_temp_mins else 0.0,
        "temp_max_avg": round(sum(all_temp_maxs) / len(all_temp_maxs), 2) if all_temp_maxs else 0.0,
        "precip_annual": round(total_precip, 1),
        "hot_days": total_hot_days,
        "frost_days": total_frost_days,
        "precip_days": total_precip_days,
    }

    climate_store = ClimateStore(db)
    climate_store.upsert_state_year(yearly)

    _log(params, f"Aggregated {station_count} stations for {state}/{year}")
    return {"yearly": yearly}


# ---------------------------------------------------------------------------
# ComputeClimateTrend
# ---------------------------------------------------------------------------


def handle_compute_climate_trend(params: dict[str, Any]) -> dict[str, Any]:
    """Compute linear climate trend over yearly summaries."""
    state = params.get("state", "")
    start_year = int(params.get("start_year", 1944))
    end_year = int(params.get("end_year", 2024))

    db = get_weather_db()
    climate_store = ClimateStore(db)
    yearly_data = climate_store.get_state_years(state, start_year, end_year)

    if not yearly_data:
        trend = _empty_trend(state, start_year, end_year)
        climate_store.upsert_trend(trend)
        return {"trend": trend}

    years = [d["year"] for d in yearly_data]
    temps = [d["temp_mean"] for d in yearly_data]

    # Linear regression on temp_mean vs year
    slope, _intercept = simple_linear_regression(
        [float(y) for y in years], [float(t) for t in temps]
    )
    warming_rate = round(slope * 10, 4)  # per decade

    # Precipitation change %
    first_precip = yearly_data[0].get("precip_annual", 0)
    last_precip = yearly_data[-1].get("precip_annual", 0)
    if first_precip > 0:
        precip_change_pct = round((last_precip - first_precip) / first_precip * 100, 2)
    else:
        precip_change_pct = 0.0

    # Decade grouping
    decades = _group_by_decade(yearly_data)

    trend = {
        "state": state,
        "start_year": start_year,
        "end_year": end_year,
        "warming_rate_per_decade": warming_rate,
        "precip_change_pct": precip_change_pct,
        "decades": decades,
    }

    climate_store.upsert_trend(trend)

    _log(params, f"Trend for {state}: {warming_rate}°C/decade, precip {precip_change_pct}%")
    return {"trend": trend}


def _empty_trend(state: str, start_year: int, end_year: int) -> dict[str, Any]:
    """Return a zero-valued trend when no data exists."""
    return {
        "state": state,
        "start_year": start_year,
        "end_year": end_year,
        "warming_rate_per_decade": 0.0,
        "precip_change_pct": 0.0,
        "decades": {},
    }


def _group_by_decade(yearly_data: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    """Group yearly data into decade buckets with averages."""
    buckets: dict[str, list[dict[str, Any]]] = {}
    for d in yearly_data:
        decade = f"{(d['year'] // 10) * 10}s"
        buckets.setdefault(decade, []).append(d)

    result: dict[str, dict[str, float]] = {}
    for decade, items in sorted(buckets.items()):
        temps = [i["temp_mean"] for i in items if i.get("temp_mean")]
        precips = [i["precip_annual"] for i in items if i.get("precip_annual")]
        result[decade] = {
            "avg_temp": round(sum(temps) / len(temps), 2) if temps else 0.0,
            "avg_precip": round(sum(precips) / len(precips), 1) if precips else 0.0,
            "years_with_data": len(items),
        }
    return result


# ---------------------------------------------------------------------------
# GenerateClimateNarrative (prompt block fallback)
# ---------------------------------------------------------------------------


def handle_generate_climate_narrative(params: dict[str, Any]) -> dict[str, Any]:
    """Generate a narrative summary of climate trends.

    Uses Claude API when ANTHROPIC_API_KEY is set, otherwise falls back
    to a deterministic narrative.
    """
    state = params.get("state", "")
    trend = params.get("trend", {})
    if isinstance(trend, str):
        trend = json.loads(trend)

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if api_key:
        try:
            return _climate_narrative_llm(state, trend, api_key)
        except Exception:
            pass

    return _climate_narrative_fallback(state, trend)


def _climate_narrative_llm(state: str, trend: dict[str, Any], api_key: str) -> dict[str, Any]:
    """Call Claude API for a narrative summary."""
    from anthropic import Anthropic

    client = Anthropic(api_key=api_key)
    prompt = (
        f"Summarize the climate trends for {state}. "
        f"Trend data: {json.dumps(trend)}. "
        "Highlight the warming rate per decade, precipitation changes, "
        "and notable decade-over-decade shifts. Keep it under 200 words."
    )
    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=512,
        messages=[{"role": "user", "content": prompt}],
    )
    narrative = message.content[0].text

    # Store narrative alongside trend
    try:
        db = get_weather_db()
        db["climate_trends"].update_one(
            {"state": state}, {"$set": {"narrative": narrative}}, upsert=True
        )
    except Exception:
        pass

    return {"narrative": narrative, "highlights": []}


def _climate_narrative_fallback(state: str, trend: dict[str, Any]) -> dict[str, Any]:
    """Deterministic narrative when no API key is available."""
    warming = trend.get("warming_rate_per_decade", 0)
    precip_pct = trend.get("precip_change_pct", 0)
    start = trend.get("start_year", "?")
    end = trend.get("end_year", "?")
    decades = trend.get("decades", {})

    parts: list[str] = []
    highlights: list[dict[str, str]] = []

    parts.append(f"Climate analysis for {state} from {start} to {end}.")

    if warming > 0:
        parts.append(f"Temperatures have risen at {warming}°C per decade.")
        highlights.append({"type": "warming", "value": f"{warming}°C/decade"})
    elif warming < 0:
        parts.append(f"Temperatures have cooled at {abs(warming)}°C per decade.")
        highlights.append({"type": "cooling", "value": f"{abs(warming)}°C/decade"})
    else:
        parts.append("Temperatures have remained stable.")

    if precip_pct > 0:
        parts.append(f"Annual precipitation has increased by {precip_pct}%.")
    elif precip_pct < 0:
        parts.append(f"Annual precipitation has decreased by {abs(precip_pct)}%.")

    if decades:
        decade_names = sorted(decades.keys())
        if len(decade_names) >= 2:
            first_dec = decade_names[0]
            last_dec = decade_names[-1]
            first_temp = decades[first_dec].get("avg_temp", 0)
            last_temp = decades[last_dec].get("avg_temp", 0)
            parts.append(
                f"Average temperature shifted from {first_temp}°C in the {first_dec} "
                f"to {last_temp}°C in the {last_dec}."
            )

    narrative = " ".join(parts)

    # Store narrative alongside trend
    try:
        db = get_weather_db()
        db["climate_trends"].update_one(
            {"state": state}, {"$set": {"narrative": narrative}}, upsert=True
        )
    except Exception:
        pass

    return {"narrative": narrative, "highlights": highlights}


# ---------------------------------------------------------------------------
# AnalyzeStationClimate (consolidated fast-path)
# ---------------------------------------------------------------------------


def handle_analyze_station_climate(params: dict[str, Any]) -> dict[str, Any]:
    """Analyze a station across a year range in one call.

    Internally loops start_year..end_year: download → parse → QC → daily
    stats → annual summary → write report.  Per-year errors are caught so
    one bad year doesn't fail the whole station.
    """
    usaf = params.get("usaf", "")
    wban = params.get("wban", "")
    station_name = params.get("station_name", "")
    lat = float(params.get("lat", 0))
    lon = float(params.get("lon", 0))
    start_year = int(params.get("start_year", 1944))
    end_year = int(params.get("end_year", 2024))
    max_missing_pct = float(params.get("max_missing_pct", 20.0))

    force = bool(params.get("force", False))
    station_id = f"{usaf}-{wban}"
    db = get_weather_db()
    store = WeatherReportStore(db)
    yearly_summaries: list[dict[str, Any]] = []
    years_ok = 0
    years_cached = 0

    for year in range(start_year, end_year + 1):
        try:
            # Skip years that already have reports in MongoDB
            if not force:
                existing = store.get_report(station_id, year)
                if existing and existing.get("report"):
                    yearly_summaries.append({"year": year, "status": "cached"})
                    years_ok += 1
                    years_cached += 1
                    continue

            raw_path = download_isd_lite(usaf, wban, year)
            observations = parse_isd_lite_file(raw_path)

            # QC
            missing = compute_missing_pct(observations)
            temp_ok = validate_temperature_range(observations)
            if missing > max_missing_pct or not temp_ok:
                yearly_summaries.append({"year": year, "status": "qc_failed"})
                continue

            daily = compute_daily_stats(observations)
            summary = compute_annual_summary(daily)

            # Write to weather_reports for downstream aggregation
            location = f"{lat:.2f},{lon:.2f}"
            store.upsert_report(
                station_id=station_id,
                station_name=station_name,
                year=year,
                location=location,
                report={
                    "total_days": summary["total_days"],
                    "annual_precip": summary["annual_precip"],
                    "temp_range": f"{summary.get('temp_min', 'N/A')} to {summary.get('temp_max', 'N/A')}",
                },
                daily_stats=daily,
            )

            yearly_summaries.append({"year": year, "status": "ok", **summary})
            years_ok += 1
        except Exception as exc:
            yearly_summaries.append({"year": year, "status": "error", "error": str(exc)})

    total = end_year - start_year + 1 if end_year >= start_year else 0
    cached_msg = f", {years_cached} cached" if years_cached else ""
    _log(
        params,
        f"AnalyzeStationClimate {station_id}: {years_ok}/{total} years OK{cached_msg}",
    )
    return {
        "yearly_summaries": yearly_summaries,
        "years_analyzed": years_ok,
        "years_cached": years_cached,
        "station_id": station_id,
    }


# ---------------------------------------------------------------------------
# ComputeRegionTrend (consolidated fast-path)
# ---------------------------------------------------------------------------


def _aggregate_single_year(db: Any, state: str, year: int) -> dict[str, Any]:
    """Aggregate station reports for a single state+year (extracted from handle_aggregate_state_year)."""
    reports = list(
        db["weather_reports"].find(
            {"report.state": state, "year": year},
            {"_id": 0},
        )
    )
    if not reports:
        reports = list(db["weather_reports"].find({"year": year}, {"_id": 0}))
        if state:
            reports = [r for r in reports if state.lower() in (r.get("location") or "").lower()]

    if not reports:
        return {
            "state": state,
            "year": year,
            "station_count": 0,
            "temp_mean": 0.0,
            "temp_min_avg": 0.0,
            "temp_max_avg": 0.0,
            "precip_annual": 0.0,
            "hot_days": 0,
            "frost_days": 0,
            "precip_days": 0,
        }

    all_temp_means: list[float] = []
    all_temp_mins: list[float] = []
    all_temp_maxs: list[float] = []
    total_precip = 0.0
    total_hot_days = 0
    total_frost_days = 0
    total_precip_days = 0

    for report in reports:
        daily_stats = report.get("daily_stats", [])
        if isinstance(daily_stats, str):
            daily_stats = json.loads(daily_stats)
        for day in daily_stats:
            t_mean = day.get("temp_mean")
            t_min = day.get("temp_min")
            t_max = day.get("temp_max")
            precip = day.get("precip_total", 0) or 0
            if t_mean is not None:
                all_temp_means.append(float(t_mean))
            if t_min is not None:
                all_temp_mins.append(float(t_min))
                if float(t_min) < 0:
                    total_frost_days += 1
            if t_max is not None:
                all_temp_maxs.append(float(t_max))
                if float(t_max) > 35:
                    total_hot_days += 1
            if precip > 0:
                total_precip_days += 1
            total_precip += float(precip)

    return {
        "state": state,
        "year": year,
        "station_count": len(reports),
        "temp_mean": round(sum(all_temp_means) / len(all_temp_means), 2) if all_temp_means else 0.0,
        "temp_min_avg": round(sum(all_temp_mins) / len(all_temp_mins), 2) if all_temp_mins else 0.0,
        "temp_max_avg": round(sum(all_temp_maxs) / len(all_temp_maxs), 2) if all_temp_maxs else 0.0,
        "precip_annual": round(total_precip, 1),
        "hot_days": total_hot_days,
        "frost_days": total_frost_days,
        "precip_days": total_precip_days,
    }


def handle_compute_region_trend(params: dict[str, Any]) -> dict[str, Any]:
    """Compute region trend in one call: aggregate all years → trend → narrative."""
    state = params.get("state", "")
    start_year = int(params.get("start_year", 1944))
    end_year = int(params.get("end_year", 2024))

    db = get_weather_db()
    climate_store = ClimateStore(db)

    # Aggregate each year
    yearly_data: list[dict[str, Any]] = []
    for year in range(start_year, end_year + 1):
        yearly = _aggregate_single_year(db, state, year)
        if yearly["station_count"] > 0:
            climate_store.upsert_state_year(yearly)
            yearly_data.append(yearly)

    # Compute trend
    if not yearly_data:
        trend = _empty_trend(state, start_year, end_year)
    else:
        years = [d["year"] for d in yearly_data]
        temps = [d["temp_mean"] for d in yearly_data]
        slope, _intercept = simple_linear_regression(
            [float(y) for y in years], [float(t) for t in temps]
        )
        warming_rate = round(slope * 10, 4)
        first_precip = yearly_data[0].get("precip_annual", 0)
        last_precip = yearly_data[-1].get("precip_annual", 0)
        precip_change_pct = (
            round((last_precip - first_precip) / first_precip * 100, 2) if first_precip > 0 else 0.0
        )
        decades = _group_by_decade(yearly_data)
        trend = {
            "state": state,
            "start_year": start_year,
            "end_year": end_year,
            "warming_rate_per_decade": warming_rate,
            "precip_change_pct": precip_change_pct,
            "decades": decades,
        }

    climate_store.upsert_trend(trend)

    # Generate narrative
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if api_key:
        try:
            narr = _climate_narrative_llm(state, trend, api_key)
        except Exception:
            narr = _climate_narrative_fallback(state, trend)
    else:
        narr = _climate_narrative_fallback(state, trend)

    _log(
        params,
        f"ComputeRegionTrend {state}: {len(yearly_data)} years, warming={trend['warming_rate_per_decade']}°C/decade",
    )
    return {
        "trend": trend,
        "narrative": narr["narrative"],
        "highlights": narr.get("highlights", []),
    }


# ---------------------------------------------------------------------------
# Logging helper
# ---------------------------------------------------------------------------


def _log(params: dict[str, Any], msg: str) -> None:
    """Emit a step log message if _step_log is available."""
    step_log = params.get("_step_log")
    if step_log is not None:
        if callable(step_log):
            step_log(msg, "success")
        else:
            step_log.append({"message": msg, "level": "success"})


# ---------------------------------------------------------------------------
# Dispatch and registration
# ---------------------------------------------------------------------------


_DISPATCH: dict[str, Any] = {
    f"{NAMESPACE}.AggregateStateYear": handle_aggregate_state_year,
    f"{NAMESPACE}.ComputeClimateTrend": handle_compute_climate_trend,
    f"{NAMESPACE}.GenerateClimateNarrative": handle_generate_climate_narrative,
    f"{NAMESPACE}.ComputeRegionTrend": handle_compute_region_trend,
    "climate.Station.AnalyzeStationClimate": handle_analyze_station_climate,
}


def handle(payload: dict) -> dict:
    """RegistryRunner entrypoint."""
    facet = payload["_facet_name"]
    handler = _DISPATCH[facet]
    return handler(payload)


def register_handlers(runner) -> None:
    """Register with RegistryRunner."""
    for facet_name in _DISPATCH:
        runner.register_handler(
            facet_name=facet_name,
            module_uri=f"file://{os.path.abspath(__file__)}",
            entrypoint="handle",
        )


def register_climate_handlers(poller) -> None:
    """Register with AgentPoller."""
    for facet_name, handler in _DISPATCH.items():
        poller.register(facet_name, handler)

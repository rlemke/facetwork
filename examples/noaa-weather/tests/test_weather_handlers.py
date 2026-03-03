"""Tests for the noaa-weather example handlers."""

from __future__ import annotations

import json
import os

import pytest


# ---------------------------------------------------------------------------
# TestWeatherUtils — utility function tests
# ---------------------------------------------------------------------------
class TestWeatherUtils:
    def test_parse_isd_lite_line_valid(self):
        from handlers.shared.weather_utils import parse_isd_lite_line

        # ISD-Lite fixed-width: 4d year, 02d month/day/hour, then 6d fields
        line = "2023 01 15 12   -50   -80 10130   270    30     4    10 -9999"
        rec = parse_isd_lite_line(line)
        assert rec is not None
        assert rec["date"] == "2023-01-15"
        assert rec["hour"] == 12
        assert rec["air_temp"] == -5.0
        assert rec["dew_point"] == -8.0
        assert rec["sea_level_pressure"] == 1013.0
        assert rec["wind_direction"] == 270
        assert rec["wind_speed"] == 3.0
        assert rec["precipitation"] == 1.0

    def test_parse_isd_lite_line_missing(self):
        from handlers.shared.weather_utils import parse_isd_lite_line

        line = "2023 06 01 00 -9999 -9999 -9999 -9999 -9999 -9999 -9999 -9999"
        rec = parse_isd_lite_line(line)
        assert rec is not None
        assert rec["air_temp"] is None
        assert rec["wind_speed"] is None
        assert rec["precipitation"] is None

    def test_parse_isd_lite_line_short(self):
        from handlers.shared.weather_utils import parse_isd_lite_line

        rec = parse_isd_lite_line("short")
        assert rec is None

    def test_parse_isd_lite_file(self, tmp_path):
        from handlers.shared.weather_utils import parse_isd_lite_file

        content = (
            "2023 01 01 00   100    50 10100   180    20     2     0 -9999\n"
            "2023 01 01 06   120    60 10110   200    30     3     5 -9999\n"
        )
        f = tmp_path / "test.txt"
        f.write_text(content)
        recs = parse_isd_lite_file(str(f))
        assert len(recs) == 2
        assert recs[0]["air_temp"] == 10.0
        assert recs[1]["air_temp"] == 12.0

    def test_station_inventory_filter(self):
        from handlers.shared.weather_utils import (
            filter_active_stations,
            parse_station_inventory,
        )

        csv = (
            '"USAF","WBAN","STATION NAME","CTRY","STATE","LAT","LON","ELEV(M)","BEGIN","END"\n'
            '"725030","14732","LA GUARDIA","US","NY","40.779","-73.880","3.4","19730101","20231231"\n'
            '"722020","12839","MIAMI","US","FL","25.791","-80.316","8.8","19730101","20231231"\n'
            '"012345","99999","LONDON","UK","","51.5","-0.12","24.0","19730101","20231231"\n'
        )
        stations = parse_station_inventory(csv)
        assert len(stations) == 3
        us = filter_active_stations(stations, country="US")
        assert len(us) == 2
        ny = filter_active_stations(stations, country="US", state="NY")
        assert len(ny) == 1
        assert ny[0]["station_name"] == "LA GUARDIA"

    def test_compute_missing_pct(self):
        from handlers.shared.weather_utils import compute_missing_pct

        obs = [{"air_temp": 10}, {"air_temp": None}, {"air_temp": 20}, {"air_temp": None}]
        assert compute_missing_pct(obs) == 50.0
        assert compute_missing_pct([]) == 100.0

    def test_validate_temperature_range(self):
        from handlers.shared.weather_utils import validate_temperature_range

        assert validate_temperature_range([{"air_temp": 20}, {"air_temp": -10}]) is True
        assert validate_temperature_range([{"air_temp": 70}]) is False  # >60
        assert validate_temperature_range([{"air_temp": None}]) is False  # no valid temps

    def test_compute_daily_stats(self):
        from handlers.shared.weather_utils import compute_daily_stats

        obs = [
            {"date": "2023-01-01", "air_temp": 5, "wind_speed": 10, "precipitation": 2},
            {"date": "2023-01-01", "air_temp": 10, "wind_speed": 15, "precipitation": 3},
            {"date": "2023-01-02", "air_temp": -2, "wind_speed": 5, "precipitation": 0},
        ]
        daily = compute_daily_stats(obs)
        assert len(daily) == 2
        assert daily[0]["date"] == "2023-01-01"
        assert daily[0]["temp_min"] == 5
        assert daily[0]["temp_max"] == 10
        assert daily[0]["temp_mean"] == 7.5
        assert daily[0]["precip_total"] == 5.0
        assert daily[0]["wind_max"] == 15

    def test_narrative_fallback(self):
        from handlers.shared.weather_utils import generate_narrative_fallback

        daily = [
            {"date": "2023-07-15", "temp_max": 38, "temp_min": 25, "precip_total": 0},
            {"date": "2023-01-10", "temp_max": 5, "temp_min": -12, "precip_total": 15},
            {"date": "2023-03-20", "temp_max": 15, "temp_min": 3, "precip_total": 25},
        ]
        narrative, highlights = generate_narrative_fallback("TEST STATION", 2023, daily)
        assert "TEST STATION" in narrative
        assert "2023" in narrative
        assert len(highlights) >= 2
        types = {h["type"] for h in highlights}
        assert "hottest" in types
        assert "coldest" in types

    def test_narrative_fallback_empty(self):
        from handlers.shared.weather_utils import generate_narrative_fallback

        narrative, highlights = generate_narrative_fallback("EMPTY", 2023, [])
        assert "No data" in narrative
        assert highlights == []


# ---------------------------------------------------------------------------
# TestDiscoveryHandlers — station discovery handler tests
# ---------------------------------------------------------------------------
class TestDiscoveryHandlers:
    def test_handle_discover_stations(self):
        from handlers.discovery.discovery_handlers import handle_discover_stations

        result = handle_discover_stations({"country": "US", "max_stations": 5})
        assert "stations" in result
        assert "station_count" in result
        assert result["station_count"] <= 5
        assert isinstance(result["stations"], list)

    def test_handle_discover_stations_state_filter(self):
        from handlers.discovery.discovery_handlers import handle_discover_stations

        result = handle_discover_stations({"country": "US", "state": "NY", "max_stations": 10})
        for s in result["stations"]:
            assert s["state"] == "NY"

    def test_handle_discover_stations_step_log(self):
        from handlers.discovery.discovery_handlers import handle_discover_stations

        messages: list[tuple[str, str]] = []
        handle_discover_stations(
            {
                "country": "US",
                "max_stations": 3,
                "_step_log": lambda msg, level: messages.append((msg, level)),
            }
        )
        assert len(messages) == 1
        assert "Discovered" in messages[0][0]


# ---------------------------------------------------------------------------
# TestIngestHandlers — download and parse handler tests
# ---------------------------------------------------------------------------
class TestIngestHandlers:
    def test_handle_download_observations(self):
        from handlers.ingest.ingest_handlers import handle_download_observations

        result = handle_download_observations(
            {
                "usaf": "725030",
                "wban": "14732",
                "year": 2023,
            }
        )
        assert "raw_path" in result
        assert "file_size" in result
        assert "station_id" in result
        assert result["station_id"] == "725030-14732"

    def test_handle_parse_observations(self, tmp_path):
        from handlers.ingest.ingest_handlers import handle_parse_observations

        content = "2023 01 01 00   100    50 10100   180    20     2     0 -9999\n"
        f = tmp_path / "test.txt"
        f.write_text(content)
        result = handle_parse_observations(
            {
                "raw_path": str(f),
                "station_id": "TEST-001",
            }
        )
        assert "observations" in result
        assert "record_count" in result
        assert result["record_count"] == 1

    def test_handle_parse_empty(self):
        from handlers.ingest.ingest_handlers import handle_parse_observations

        result = handle_parse_observations(
            {
                "raw_path": "/nonexistent/path.txt",
                "station_id": "NONE",
            }
        )
        assert result["record_count"] == 0
        assert result["observations"] == []


# ---------------------------------------------------------------------------
# TestQCHandlers — quality validation handler tests
# ---------------------------------------------------------------------------
class TestQCHandlers:
    def test_handle_validate_quality_pass(self):
        from handlers.qc.qc_handlers import handle_validate_quality

        obs = [{"air_temp": 20}, {"air_temp": 15}, {"air_temp": 25}]
        result = handle_validate_quality({"observations": obs, "station_id": "TEST"})
        assert "qc" in result
        assert result["qc"]["plausible"] is True
        assert result["qc"]["missing_pct"] == 0.0
        assert result["qc"]["temp_range_ok"] is True

    def test_handle_validate_quality_fail(self):
        from handlers.qc.qc_handlers import handle_validate_quality

        # 60% missing → should fail with default 20% threshold
        obs = [
            {"air_temp": None},
            {"air_temp": None},
            {"air_temp": None},
            {"air_temp": 10},
            {"air_temp": 15},
        ]
        result = handle_validate_quality({"observations": obs, "station_id": "TEST"})
        assert result["qc"]["plausible"] is False
        assert result["qc"]["missing_pct"] == 60.0

    def test_handle_validate_quality_step_log(self):
        from handlers.qc.qc_handlers import handle_validate_quality

        messages: list[tuple[str, str]] = []
        handle_validate_quality(
            {
                "observations": [{"air_temp": 20}],
                "station_id": "TEST",
                "_step_log": lambda msg, level: messages.append((msg, level)),
            }
        )
        assert len(messages) == 1
        assert "QC" in messages[0][0]


# ---------------------------------------------------------------------------
# TestAnalysisHandlers — daily stats and sparse analysis handler tests
# ---------------------------------------------------------------------------
class TestAnalysisHandlers:
    def test_handle_compute_daily_stats(self):
        from handlers.analysis.analysis_handlers import handle_compute_daily_stats

        obs = [
            {"date": "2023-01-01", "air_temp": 5, "wind_speed": 10, "precipitation": 2},
            {"date": "2023-01-01", "air_temp": 10, "wind_speed": 15, "precipitation": 3},
            {"date": "2023-01-02", "air_temp": -2, "wind_speed": 5, "precipitation": 1},
        ]
        result = handle_compute_daily_stats({"observations": obs, "station_id": "TEST"})
        assert "daily_stats" in result
        assert "total_days" in result
        assert "annual_precip" in result
        assert result["total_days"] == 2
        assert result["annual_precip"] == 6.0

    def test_handle_compute_daily_stats_json_string(self):
        from handlers.analysis.analysis_handlers import handle_compute_daily_stats

        obs = [{"date": "2023-06-01", "air_temp": 30, "wind_speed": 5, "precipitation": 0}]
        result = handle_compute_daily_stats({"observations": json.dumps(obs), "station_id": "T"})
        assert result["total_days"] == 1

    def test_handle_sparse_analysis(self):
        from handlers.analysis.analysis_handlers import handle_sparse_analysis

        obs = [{"air_temp": 10} for _ in range(100)]
        result = handle_sparse_analysis({"observations": obs, "station_id": "SPARSE"})
        assert "summary" in result
        assert "record_count" in result
        assert "coverage_pct" in result
        assert result["record_count"] == 100
        assert result["coverage_pct"] > 0

    def test_handle_compute_daily_stats_step_log(self):
        from handlers.analysis.analysis_handlers import handle_compute_daily_stats

        messages: list[tuple[str, str]] = []
        handle_compute_daily_stats(
            {
                "observations": [
                    {"date": "2023-01-01", "air_temp": 10, "wind_speed": 5, "precipitation": 0}
                ],
                "station_id": "TEST",
                "_step_log": lambda msg, level: messages.append((msg, level)),
            }
        )
        assert len(messages) == 1
        assert "daily stats" in messages[0][0]


# ---------------------------------------------------------------------------
# TestGeocodeHandlers — reverse geocode handler tests
# ---------------------------------------------------------------------------
class TestGeocodeHandlers:
    def test_handle_reverse_geocode(self):
        from handlers.geocode.geocode_handlers import handle_reverse_geocode

        result = handle_reverse_geocode({"lat": 40.779, "lon": -73.88})
        assert "geo" in result
        geo = result["geo"]
        assert "display_name" in geo
        assert "city" in geo
        assert "state" in geo
        assert "country" in geo

    def test_handle_reverse_geocode_deterministic(self):
        from handlers.geocode.geocode_handlers import handle_reverse_geocode

        r1 = handle_reverse_geocode({"lat": 40.779, "lon": -73.88})
        r2 = handle_reverse_geocode({"lat": 40.779, "lon": -73.88})
        assert r1 == r2

    def test_handle_reverse_geocode_step_log(self):
        from handlers.geocode.geocode_handlers import handle_reverse_geocode

        messages: list[tuple[str, str]] = []
        handle_reverse_geocode(
            {
                "lat": 40.0,
                "lon": -74.0,
                "_step_log": lambda msg, level: messages.append((msg, level)),
            }
        )
        assert len(messages) == 1
        assert "Geocoded" in messages[0][0]


# ---------------------------------------------------------------------------
# TestInterpretHandlers — narrative generation handler tests
# ---------------------------------------------------------------------------
class TestInterpretHandlers:
    def test_handle_generate_narrative(self):
        from handlers.interpret.interpret_handlers import handle_generate_narrative

        daily = [
            {"date": "2023-07-15", "temp_max": 38, "temp_min": 25, "precip_total": 0},
            {"date": "2023-01-10", "temp_max": 5, "temp_min": -12, "precip_total": 15},
        ]
        result = handle_generate_narrative(
            {
                "station_name": "LA GUARDIA",
                "year": 2023,
                "daily_stats": daily,
                "geo_context": {"city": "New York", "state": "New York"},
            }
        )
        assert "narrative" in result
        assert "highlights" in result
        assert "LA GUARDIA" in result["narrative"]

    def test_handle_generate_narrative_highlights(self):
        from handlers.interpret.interpret_handlers import handle_generate_narrative

        daily = [
            {"date": "2023-07-15", "temp_max": 40, "temp_min": 28, "precip_total": 0},
            {"date": "2023-01-10", "temp_max": 2, "temp_min": -15, "precip_total": 20},
            {"date": "2023-09-01", "temp_max": 30, "temp_min": 22, "precip_total": 50},
        ]
        result = handle_generate_narrative(
            {
                "station_name": "TEST",
                "year": 2023,
                "daily_stats": daily,
            }
        )
        types = {h["type"] for h in result["highlights"]}
        assert "hottest" in types
        assert "coldest" in types
        assert "wettest" in types

    def test_handle_generate_narrative_step_log(self):
        from handlers.interpret.interpret_handlers import handle_generate_narrative

        messages: list[tuple[str, str]] = []
        handle_generate_narrative(
            {
                "station_name": "TEST",
                "year": 2023,
                "daily_stats": [
                    {"date": "2023-01-01", "temp_max": 10, "temp_min": 0, "precip_total": 5}
                ],
                "_step_log": lambda msg, level: messages.append((msg, level)),
            }
        )
        assert len(messages) == 1
        assert "Narrative" in messages[0][0]


# ---------------------------------------------------------------------------
# TestReportHandlers — report generation handler tests
# ---------------------------------------------------------------------------
class TestReportHandlers:
    def test_handle_generate_station_report(self):
        from handlers.report.report_handlers import handle_generate_station_report

        result = handle_generate_station_report(
            {
                "station_id": "725030-14732",
                "station_name": "LA GUARDIA AIRPORT",
                "year": 2023,
                "location": "New York, NY",
                "daily_stats": [
                    {
                        "date": "2023-01-01",
                        "temp_min": -5,
                        "temp_max": 5,
                        "precip_total": 2,
                        "wind_max": 10,
                        "obs_count": 8,
                    },
                ],
                "annual_precip": 1100.0,
                "narrative": "A typical year.",
            }
        )
        assert "report" in result
        report = result["report"]
        assert report["station_id"] == "725030-14732"
        assert report["year"] == 2023
        assert report["report_path"].endswith(".json")

    def test_handle_generate_batch_summary(self):
        from handlers.report.report_handlers import handle_generate_batch_summary

        result = handle_generate_batch_summary(
            {
                "batch_id": "US-NY",
                "station_count": 5,
                "results": [
                    {"status": "completed"},
                    {"status": "completed"},
                    {"status": "error"},
                ],
            }
        )
        assert "report_path" in result
        assert "completed" in result
        assert "failed" in result
        assert "summary" in result
        assert result["completed"] == 2
        assert result["failed"] == 3

    def test_handle_generate_station_report_step_log(self):
        from handlers.report.report_handlers import handle_generate_station_report

        messages: list[tuple[str, str]] = []
        handle_generate_station_report(
            {
                "station_id": "TEST-001",
                "station_name": "TEST",
                "year": 2023,
                "location": "Somewhere",
                "daily_stats": [],
                "annual_precip": 0,
                "narrative": "N/A",
                "_step_log": lambda msg, level: messages.append((msg, level)),
            }
        )
        assert len(messages) == 1
        assert "report" in messages[0][0].lower()


# ---------------------------------------------------------------------------
# TestDispatch — dispatch table structure and routing
# ---------------------------------------------------------------------------
class TestDispatch:
    def test_discovery_dispatch_count(self):
        from handlers.discovery.discovery_handlers import _DISPATCH

        assert len(_DISPATCH) == 1

    def test_ingest_dispatch_count(self):
        from handlers.ingest.ingest_handlers import _DISPATCH

        assert len(_DISPATCH) == 2

    def test_qc_dispatch_count(self):
        from handlers.qc.qc_handlers import _DISPATCH

        assert len(_DISPATCH) == 1

    def test_analysis_dispatch_count(self):
        from handlers.analysis.analysis_handlers import _DISPATCH

        assert len(_DISPATCH) == 2

    def test_geocode_dispatch_count(self):
        from handlers.geocode.geocode_handlers import _DISPATCH

        assert len(_DISPATCH) == 1

    def test_interpret_dispatch_count(self):
        from handlers.interpret.interpret_handlers import _DISPATCH

        assert len(_DISPATCH) == 1

    def test_report_dispatch_count(self):
        from handlers.report.report_handlers import _DISPATCH

        assert len(_DISPATCH) == 2

    def test_all_dispatch_names_have_namespace_prefix(self):
        from handlers.analysis.analysis_handlers import _DISPATCH as d1
        from handlers.discovery.discovery_handlers import _DISPATCH as d2
        from handlers.geocode.geocode_handlers import _DISPATCH as d3
        from handlers.ingest.ingest_handlers import _DISPATCH as d4
        from handlers.interpret.interpret_handlers import _DISPATCH as d5
        from handlers.qc.qc_handlers import _DISPATCH as d6
        from handlers.report.report_handlers import _DISPATCH as d7

        all_names = (
            list(d1.keys())
            + list(d2.keys())
            + list(d3.keys())
            + list(d4.keys())
            + list(d5.keys())
            + list(d6.keys())
            + list(d7.keys())
        )
        assert len(all_names) == 10
        assert all(n.startswith("weather.") for n in all_names)


# ---------------------------------------------------------------------------
# TestCompilation — AFL parsing and AST checks
# ---------------------------------------------------------------------------
class TestCompilation:
    @pytest.fixture()
    def parsed_ast(self):
        from afl.parser import AFLParser

        afl_path = os.path.join(os.path.dirname(__file__), "..", "afl", "weather.afl")
        with open(afl_path) as f:
            source = f.read()
        return AFLParser().parse(source)

    def test_afl_parses(self, parsed_ast):
        assert parsed_ast is not None

    def test_schema_count(self, parsed_ast):
        schemas = []
        for ns in parsed_ast.namespaces:
            schemas.extend(ns.schemas)
        assert len(schemas) == 6

    def test_event_facet_count(self, parsed_ast):
        event_facets = []
        for ns in parsed_ast.namespaces:
            event_facets.extend(ns.event_facets)
        assert len(event_facets) == 10

    def test_workflow_count(self, parsed_ast):
        workflows = []
        for ns in parsed_ast.namespaces:
            workflows.extend(ns.workflows)
        assert len(workflows) == 3

    def test_namespace_count(self, parsed_ast):
        assert len(parsed_ast.namespaces) == 10

    def test_prompt_block_present(self, parsed_ast):
        """Verify prompt block appears on GenerateNarrative."""
        from afl.ast import PromptBlock

        prompt_count = 0
        for ns in parsed_ast.namespaces:
            for ef in ns.event_facets:
                body = ef.body
                if isinstance(body, PromptBlock):
                    prompt_count += 1
        assert prompt_count == 1, f"Expected 1 prompt block, got {prompt_count}"

    def test_script_block_present(self, parsed_ast):
        """Verify script block appears on ValidateQuality."""
        from afl.ast import ScriptBlock

        script_count = 0
        for ns in parsed_ast.namespaces:
            for ef in ns.event_facets:
                if isinstance(ef.body, ScriptBlock):
                    script_count += 1
                elif hasattr(ef, "pre_script") and ef.pre_script is not None:
                    script_count += 1
        assert script_count >= 1, "Expected at least 1 script block"

    def test_when_block_present(self, parsed_ast):
        """Verify andThen when block appears in AnalyzeStation workflow."""
        from afl.ast import WhenBlock

        wf_ns = [ns for ns in parsed_ast.namespaces if ns.name == "weather.workflows"]
        analyze_wf = [w for w in wf_ns[0].workflows if w.sig.name == "AnalyzeStation"][0]
        body = analyze_wf.body
        assert isinstance(body, list)
        # Third block should be the when block (after download and parse+geo+qc)
        when_body = [b for b in body if b.when is not None]
        assert len(when_body) == 1
        assert isinstance(when_body[0].when, WhenBlock)
        assert len(when_body[0].when.cases) == 2

    def test_catch_present(self, parsed_ast):
        """Verify catch block appears in AnalyzeStation (download step)."""
        wf_ns = [ns for ns in parsed_ast.namespaces if ns.name == "weather.workflows"]
        analyze_wf = [w for w in wf_ns[0].workflows if w.sig.name == "AnalyzeStation"][0]
        body = analyze_wf.body
        # First andThen block → .block → steps[0] has catch
        first_block = body[0]
        step = first_block.block.steps[0]
        assert step.catch is not None


# ---------------------------------------------------------------------------
# TestAgentIntegration — end-to-end handler registration
# ---------------------------------------------------------------------------
class TestAgentIntegration:
    def test_registry_runner_poll_once(self):
        """RegistryRunner dispatches all handlers via ToolRegistry."""
        from handlers.analysis.analysis_handlers import _DISPATCH as d1
        from handlers.discovery.discovery_handlers import _DISPATCH as d2
        from handlers.geocode.geocode_handlers import _DISPATCH as d3
        from handlers.ingest.ingest_handlers import _DISPATCH as d4
        from handlers.interpret.interpret_handlers import _DISPATCH as d5
        from handlers.qc.qc_handlers import _DISPATCH as d6
        from handlers.report.report_handlers import _DISPATCH as d7

        from afl.runtime.agent import ToolRegistry

        registry = ToolRegistry()
        for dispatch in [d1, d2, d3, d4, d5, d6, d7]:
            for facet_name, handler in dispatch.items():
                tool_name = facet_name.split(".")[-1]
                registry.register(tool_name, handler)

        tool_names = [
            "DiscoverStations",
            "DownloadObservations",
            "ParseObservations",
            "ValidateQuality",
            "ComputeDailyStats",
            "SparseAnalysis",
            "ReverseGeocode",
            "GenerateNarrative",
            "GenerateStationReport",
            "GenerateBatchSummary",
        ]
        for name in tool_names:
            assert registry.has_handler(name), f"Missing handler: {name}"

    def test_registry_runner_handler_names(self):
        """Verify all dispatch tables have correct namespace prefixes."""
        from handlers.analysis.analysis_handlers import _DISPATCH as d1
        from handlers.discovery.discovery_handlers import _DISPATCH as d2
        from handlers.geocode.geocode_handlers import _DISPATCH as d3
        from handlers.ingest.ingest_handlers import _DISPATCH as d4
        from handlers.interpret.interpret_handlers import _DISPATCH as d5
        from handlers.qc.qc_handlers import _DISPATCH as d6
        from handlers.report.report_handlers import _DISPATCH as d7

        all_names = (
            list(d1.keys())
            + list(d2.keys())
            + list(d3.keys())
            + list(d4.keys())
            + list(d5.keys())
            + list(d6.keys())
            + list(d7.keys())
        )
        assert len(all_names) == 10
        assert all(n.startswith("weather.") for n in all_names)

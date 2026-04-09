"""Tests for sensor-monitoring handlers and FFL compilation."""

from __future__ import annotations

import json
import os

import pytest


# ---------------------------------------------------------------------------
# TestSensorUtils
# ---------------------------------------------------------------------------
class TestSensorUtils:
    def test_ingest_reading_structure(self):
        from handlers.shared.sensor_utils import ingest_reading

        reading, quality = ingest_reading("sensor_001", 25.5, "celsius")
        assert reading["sensor_id"] == "sensor_001"
        assert reading["value"] == 25.5
        assert reading["unit"] == "celsius"
        assert "timestamp" in reading
        assert isinstance(quality, str)

    def test_ingest_reading_null_last(self):
        from handlers.shared.sensor_utils import ingest_reading

        _, quality = ingest_reading("s1", 10.0, "fahrenheit", last_reading=None)
        assert quality == "initial"

    def test_ingest_reading_with_last(self):
        from handlers.shared.sensor_utils import ingest_reading

        _, quality = ingest_reading("s1", 10.0, "celsius", last_reading={"value": 9.0})
        assert quality == "continuous"

    def test_validate_reading_no_config(self):
        from handlers.shared.sensor_utils import validate_reading

        valid, calibrated = validate_reading({"value": 25.0})
        assert valid is True
        assert calibrated == 25.0

    def test_validate_reading_with_config(self):
        from handlers.shared.sensor_utils import validate_reading

        config = {"calibration_offset": 1.0, "calibration_scale": 1.1, "min": -50, "max": 100}
        valid, calibrated = validate_reading({"value": 20.0}, config)
        assert valid is True
        assert calibrated == round(20.0 * 1.1 + 1.0, 4)

    def test_detect_anomaly_negative_threshold(self):
        from handlers.shared.sensor_utils import detect_anomaly

        result = detect_anomaly(
            {"value": -45.0},
            threshold_low=-10.0,
            threshold_high=50.0,
            critical_low=-40.0,
            critical_high=80.0,
        )
        assert result["is_anomaly"] is True
        assert result["severity"] == "critical"
        assert result["threshold_breached"] == "critical_low"
        assert result["deviation"] == 5.0

    def test_detect_anomaly_normal(self):
        from handlers.shared.sensor_utils import detect_anomaly

        result = detect_anomaly(
            {"value": 25.0},
            threshold_low=-10.0,
            threshold_high=50.0,
            critical_low=-40.0,
            critical_high=80.0,
        )
        assert result["is_anomaly"] is False
        assert result["severity"] == "normal"

    def test_classify_alert_null_override(self):
        from handlers.shared.sensor_utils import classify_alert

        anomaly = {"severity": "critical"}
        alert = classify_alert(anomaly, "s1", override_config=None)
        assert alert["priority"] == 1
        assert alert["channel"] == "default"
        assert alert["sensor_id"] == "s1"


# ---------------------------------------------------------------------------
# TestIngestionHandlers
# ---------------------------------------------------------------------------
class TestIngestionHandlers:
    def test_ingest_reading_default(self):
        from handlers.ingestion.ingestion_handlers import handle_ingest_reading

        result = handle_ingest_reading(
            {
                "sensor_id": "sensor_001",
                "value": 22.5,
                "unit": "celsius",
            }
        )
        assert "reading" in result
        assert "quality" in result
        assert result["reading"]["sensor_id"] == "sensor_001"

    def test_ingest_null_json_string(self):
        from handlers.ingestion.ingestion_handlers import handle_ingest_reading

        result = handle_ingest_reading(
            {
                "sensor_id": "s1",
                "value": "10.0",
                "unit": "celsius",
                "last_reading": "null",
            }
        )
        assert result["quality"] == "initial"

    def test_validate_reading_default(self):
        from handlers.ingestion.ingestion_handlers import handle_validate_reading

        result = handle_validate_reading(
            {
                "reading": {"value": 25.0, "sensor_id": "s1"},
            }
        )
        assert "valid" in result
        assert "calibrated_value" in result

    def test_validate_map_key_lookup(self):
        from handlers.ingestion.ingestion_handlers import handle_validate_reading

        config = json.dumps(
            {"calibration_offset": 0.5, "calibration_scale": 1.0, "min": -100, "max": 100}
        )
        result = handle_validate_reading(
            {
                "reading": json.dumps({"value": 20.0, "sensor_id": "s1"}),
                "sensor_config": config,
            }
        )
        assert result["valid"] is True
        assert result["calibrated_value"] == 20.5


# ---------------------------------------------------------------------------
# TestAnalysisHandlers
# ---------------------------------------------------------------------------
class TestAnalysisHandlers:
    def test_detect_anomaly_default(self):
        from handlers.analysis.analysis_handlers import handle_detect_anomaly

        result = handle_detect_anomaly(
            {
                "reading": {"value": 25.0},
                "threshold_low": -10.0,
                "threshold_high": 50.0,
                "critical_low": -40.0,
                "critical_high": 80.0,
            }
        )
        assert "result" in result
        assert result["result"]["severity"] == "normal"

    def test_detect_negative_baseline(self):
        from handlers.analysis.analysis_handlers import handle_detect_anomaly

        result = handle_detect_anomaly(
            {
                "reading": json.dumps({"value": -50.0}),
                "threshold_low": "-10.0",
                "threshold_high": "50.0",
                "critical_low": "-40.0",
                "critical_high": "80.0",
            }
        )
        assert result["result"]["severity"] == "critical"
        assert result["result"]["threshold_breached"] == "critical_low"

    def test_classify_alert_default(self):
        from handlers.analysis.analysis_handlers import handle_classify_alert

        result = handle_classify_alert(
            {
                "anomaly": {"severity": "warning"},
                "sensor_id": "s1",
            }
        )
        assert "alert" in result
        assert result["alert"]["priority"] == 2

    def test_classify_null_override(self):
        from handlers.analysis.analysis_handlers import handle_classify_alert

        result = handle_classify_alert(
            {
                "anomaly": json.dumps({"severity": "critical"}),
                "sensor_id": "s2",
                "override_config": "null",
            }
        )
        assert result["alert"]["priority"] == 1
        assert result["alert"]["channel"] == "default"


# ---------------------------------------------------------------------------
# TestReportingHandlers
# ---------------------------------------------------------------------------
class TestReportingHandlers:
    def test_run_diagnostics_default(self):
        from handlers.reporting.reporting_handlers import handle_run_diagnostics

        result = handle_run_diagnostics(
            {
                "sensor_id": "s1",
                "anomaly_result": {"is_anomaly": False, "severity": "normal"},
                "reading": {"value": 25.0},
            }
        )
        assert "report" in result
        assert result["report"]["sensor_id"] == "s1"
        assert "health_status" in result["report"]

    def test_run_diagnostics_json_string(self):
        from handlers.reporting.reporting_handlers import handle_run_diagnostics

        result = handle_run_diagnostics(
            {
                "sensor_id": "s2",
                "anomaly_result": json.dumps({"is_anomaly": True, "severity": "critical"}),
                "reading": json.dumps({"value": -50.0}),
            }
        )
        assert result["report"]["anomalies_found"] >= 1

    def test_generate_summary_default(self):
        from handlers.reporting.reporting_handlers import handle_generate_summary

        result = handle_generate_summary(
            {
                "sensor_id": "s1",
                "diagnostic": {"anomalies_found": 2, "health_status": "degraded"},
                "alert": {"priority": 1},
            }
        )
        assert "summary" in result
        assert result["summary"]["critical_count"] == 1

    def test_generate_summary_json_string(self):
        from handlers.reporting.reporting_handlers import handle_generate_summary

        result = handle_generate_summary(
            {
                "sensor_id": "s1",
                "diagnostic": json.dumps({"anomalies_found": 0, "health_status": "healthy"}),
                "alert": json.dumps({"priority": 3}),
            }
        )
        assert result["summary"]["critical_count"] == 0


# ---------------------------------------------------------------------------
# TestDispatch
# ---------------------------------------------------------------------------
class TestDispatch:
    def test_ingestion_dispatch(self):
        from handlers.ingestion.ingestion_handlers import _DISPATCH

        assert len(_DISPATCH) == 2
        assert "monitor.Ingestion.IngestReading" in _DISPATCH
        assert "monitor.Ingestion.ValidateReading" in _DISPATCH

    def test_analysis_dispatch(self):
        from handlers.analysis.analysis_handlers import _DISPATCH

        assert len(_DISPATCH) == 2
        assert "monitor.Analysis.DetectAnomaly" in _DISPATCH
        assert "monitor.Analysis.ClassifyAlert" in _DISPATCH

    def test_reporting_dispatch(self):
        from handlers.reporting.reporting_handlers import _DISPATCH

        assert len(_DISPATCH) == 2
        assert "monitor.Reporting.RunDiagnostics" in _DISPATCH
        assert "monitor.Reporting.GenerateSummary" in _DISPATCH

    def test_total_handler_count(self):
        from handlers.analysis.analysis_handlers import _DISPATCH as d2
        from handlers.ingestion.ingestion_handlers import _DISPATCH as d1
        from handlers.reporting.reporting_handlers import _DISPATCH as d3

        assert len(d1) + len(d2) + len(d3) == 6

    def test_registry_handle_routes(self):
        from handlers.analysis.analysis_handlers import handle as h2
        from handlers.ingestion.ingestion_handlers import handle as h1
        from handlers.reporting.reporting_handlers import handle as h3

        # Verify the handle() entrypoints dispatch correctly
        r1 = h1(
            {
                "_facet_name": "monitor.Ingestion.IngestReading",
                "sensor_id": "s1",
                "value": 1.0,
                "unit": "c",
            }
        )
        assert "reading" in r1
        r2 = h2(
            {
                "_facet_name": "monitor.Analysis.DetectAnomaly",
                "reading": {"value": 0.0},
                "threshold_low": -10,
                "threshold_high": 50,
                "critical_low": -40,
                "critical_high": 80,
            }
        )
        assert "result" in r2
        r3 = h3(
            {
                "_facet_name": "monitor.Reporting.RunDiagnostics",
                "sensor_id": "s1",
                "anomaly_result": {},
                "reading": {},
            }
        )
        assert "report" in r3


# ---------------------------------------------------------------------------
# TestCompilation
# ---------------------------------------------------------------------------
class TestCompilation:
    @pytest.fixture()
    def parsed_ast(self):
        from facetwork.parser import FFLParser

        afl_path = os.path.join(os.path.dirname(__file__), "..", "afl", "monitor.ffl")
        with open(afl_path) as f:
            source = f.read()
        return FFLParser().parse(source)

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
        assert len(event_facets) == 6

    def test_workflow_count(self, parsed_ast):
        workflows = []
        for ns in parsed_ast.namespaces:
            workflows.extend(ns.workflows)
        assert len(workflows) == 2

    def test_mixin_facet_count(self, parsed_ast):
        mixins_ns = [ns for ns in parsed_ast.namespaces if ns.name == "monitor.mixins"][0]
        assert len(mixins_ns.facets) == 2

    def test_implicit_count(self, parsed_ast):
        implicits = []
        for ns in parsed_ast.namespaces:
            implicits.extend(ns.implicits)
        assert len(implicits) == 2

    def test_null_literal_in_call(self, parsed_ast):
        """Verify null literal appears in IngestReading and ClassifyAlert defaults."""
        from facetwork.ast import Literal

        null_count = 0
        for ns in parsed_ast.namespaces:
            for ef in ns.event_facets:
                for p in ef.sig.params:
                    if isinstance(p.default, Literal) and p.default.kind == "null":
                        null_count += 1
        assert null_count >= 2, f"Expected >=2 null literal defaults, got {null_count}"

    def test_mixin_alias_present(self, parsed_ast):
        """Verify mixin alias ('as retry', 'as alertcfg') in workflow steps."""
        from facetwork.ast import AndThenBlock, MixinCall

        aliases = []
        for ns in parsed_ast.namespaces:
            for w in ns.workflows:
                blocks = w.body if isinstance(w.body, list) else [w.body] if w.body else []
                for at in blocks:
                    if not isinstance(at, AndThenBlock) or not at.block:
                        continue
                    for step in at.block.steps:
                        if hasattr(step, "call") and step.call and step.call.mixins:
                            for m in step.call.mixins:
                                if isinstance(m, MixinCall) and m.alias:
                                    aliases.append(m.alias)
        assert "retry" in aliases, f"Expected 'retry' alias, got {aliases}"

    def test_unary_negation_in_schema_inst(self, parsed_ast):
        """Verify unary negation appears in ThresholdConfig instantiation."""
        from facetwork.ast import AndThenBlock, UnaryExpr

        unary_count = 0
        for ns in parsed_ast.namespaces:
            for w in ns.workflows:
                blocks = w.body if isinstance(w.body, list) else [w.body] if w.body else []
                for at in blocks:
                    if not isinstance(at, AndThenBlock) or not at.block:
                        continue
                    for step in at.block.steps:
                        if hasattr(step, "call") and step.call:
                            for arg in step.call.args:
                                if isinstance(arg.value, UnaryExpr):
                                    unary_count += 1
        assert unary_count >= 2, f"Expected >=2 unary negation args, got {unary_count}"


# ---------------------------------------------------------------------------
# TestAgentIntegration
# ---------------------------------------------------------------------------
class TestAgentIntegration:
    def test_registry_runner_poll_once(self):
        """RegistryRunner dispatches all handlers via ToolRegistry."""
        from handlers.analysis.analysis_handlers import _DISPATCH as d2
        from handlers.ingestion.ingestion_handlers import _DISPATCH as d1
        from handlers.reporting.reporting_handlers import _DISPATCH as d3

        from facetwork.runtime.agent import ToolRegistry

        registry = ToolRegistry()
        for dispatch in [d1, d2, d3]:
            for facet_name, handler in dispatch.items():
                tool_name = facet_name.split(".")[-1]
                registry.register(tool_name, handler)

        tool_names = [
            "IngestReading",
            "ValidateReading",
            "DetectAnomaly",
            "ClassifyAlert",
            "RunDiagnostics",
            "GenerateSummary",
        ]
        for name in tool_names:
            assert registry.has_handler(name), f"Missing handler: {name}"

    def test_registry_runner_handler_names(self):
        """Verify all dispatch tables have correct namespace prefixes."""
        from handlers.analysis.analysis_handlers import _DISPATCH as d2
        from handlers.ingestion.ingestion_handlers import _DISPATCH as d1
        from handlers.reporting.reporting_handlers import _DISPATCH as d3

        all_names = list(d1.keys()) + list(d2.keys()) + list(d3.keys())
        assert len(all_names) == 6
        assert all(n.startswith("monitor.") for n in all_names)

    def test_claude_agent_runner_with_sensor_handler(self):
        from handlers.ingestion.ingestion_handlers import handle_ingest_reading

        from facetwork.runtime import Evaluator, ExecutionStatus, MemoryStore, Telemetry
        from facetwork.runtime.agent import ClaudeAgentRunner, ToolRegistry

        store = MemoryStore()
        evaluator = Evaluator(persistence=store, telemetry=Telemetry(enabled=False))

        registry = ToolRegistry()
        registry.register("IngestReading", handle_ingest_reading)

        runner = ClaudeAgentRunner(
            evaluator=evaluator,
            persistence=store,
            tool_registry=registry,
        )

        workflow_ast = {
            "type": "WorkflowDecl",
            "name": "TestSensor",
            "params": [{"name": "sensor_id", "type": "String"}],
            "returns": [{"name": "result", "type": "String"}],
            "body": {
                "type": "AndThenBlock",
                "steps": [
                    {
                        "type": "StepStmt",
                        "id": "step-ingest",
                        "name": "ing",
                        "call": {
                            "type": "CallExpr",
                            "target": "IngestReading",
                            "args": [
                                {
                                    "name": "sensor_id",
                                    "value": {"type": "InputRef", "path": ["sensor_id"]},
                                },
                                {"name": "value", "value": {"type": "Double", "value": 22.5}},
                                {"name": "unit", "value": {"type": "String", "value": "celsius"}},
                            ],
                        },
                    },
                ],
                "yield": {
                    "type": "YieldStmt",
                    "id": "yield-TS",
                    "call": {
                        "type": "CallExpr",
                        "target": "TestSensor",
                        "args": [
                            {
                                "name": "result",
                                "value": {"type": "StepRef", "path": ["ing", "quality"]},
                            }
                        ],
                    },
                },
            },
        }

        program_ast = {
            "type": "Program",
            "declarations": [
                {
                    "type": "EventFacetDecl",
                    "name": "IngestReading",
                    "params": [
                        {"name": "sensor_id", "type": "String"},
                        {"name": "value", "type": "Double"},
                        {"name": "unit", "type": "String"},
                    ],
                    "returns": [
                        {"name": "reading", "type": "Json"},
                        {"name": "quality", "type": "String"},
                    ],
                },
            ],
        }

        result = runner.run(
            workflow_ast,
            inputs={"sensor_id": "test_sensor"},
            program_ast=program_ast,
        )

        assert result.success is True
        assert result.status == ExecutionStatus.COMPLETED

    def test_agent_poller_with_mixin_args(self):
        """Verify mixin args are passed to handler via AgentPoller."""
        from facetwork.runtime import Evaluator, ExecutionStatus, MemoryStore, Telemetry
        from facetwork.runtime.agent_poller import AgentPoller, AgentPollerConfig

        store = MemoryStore()
        evaluator = Evaluator(persistence=store, telemetry=Telemetry(enabled=False))

        received_params = {}

        def capture_handler(params):
            received_params.update(params)
            return {"reading": {"sensor_id": "s1"}, "quality": "initial"}

        poller = AgentPoller(
            persistence=store,
            evaluator=evaluator,
            config=AgentPollerConfig(service_name="test-mixin-agent"),
        )
        poller.register("ns.IngestReading", capture_handler)

        workflow_ast = {
            "type": "WorkflowDecl",
            "name": "W",
            "params": [],
            "returns": [{"name": "out", "type": "String"}],
            "body": {
                "type": "AndThenBlock",
                "steps": [
                    {
                        "type": "StepStmt",
                        "id": "step-ing",
                        "name": "ing",
                        "call": {
                            "type": "CallExpr",
                            "target": "IngestReading",
                            "args": [
                                {"name": "sensor_id", "value": {"type": "String", "value": "s1"}},
                            ],
                            "mixins": [
                                {
                                    "type": "MixinCall",
                                    "target": "RetryPolicy",
                                    "args": [
                                        {
                                            "name": "max_retries",
                                            "value": {"type": "Int", "value": 7},
                                        },
                                    ],
                                    "alias": "retry",
                                },
                            ],
                        },
                    },
                ],
                "yield": {
                    "type": "YieldStmt",
                    "id": "yield-W",
                    "call": {
                        "type": "CallExpr",
                        "target": "W",
                        "args": [
                            {
                                "name": "out",
                                "value": {"type": "StepRef", "path": ["ing", "quality"]},
                            },
                        ],
                    },
                },
            },
        }

        program_ast = {
            "type": "Program",
            "declarations": [
                {
                    "type": "Namespace",
                    "name": "ns",
                    "declarations": [
                        {
                            "type": "EventFacetDecl",
                            "name": "IngestReading",
                            "params": [
                                {"name": "sensor_id", "type": "String"},
                                {"name": "retry", "type": "Json"},
                            ],
                            "returns": [
                                {"name": "reading", "type": "Json"},
                                {"name": "quality", "type": "String"},
                            ],
                        },
                    ],
                },
            ],
        }

        result = evaluator.execute(workflow_ast, inputs={}, program_ast=program_ast)
        assert result.status == ExecutionStatus.PAUSED

        poller.poll_once()

        # Verify mixin args were passed with alias nesting
        assert "retry" in received_params
        assert received_params["retry"]["max_retries"] == 7

        final = evaluator.resume(result.workflow_id, workflow_ast, program_ast)
        assert final.success

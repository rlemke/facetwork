"""Tests for data-quality-pipeline handlers and AFL compilation."""

from __future__ import annotations

import json
import os

import pytest


# ---------------------------------------------------------------------------
# TestQualityUtils
# ---------------------------------------------------------------------------
class TestQualityUtils:
    def test_profile_dataset_structure(self):
        from handlers.shared.quality_utils import profile_dataset

        profiles, row_count = profile_dataset("sales", ["price", "quantity"])
        assert len(profiles) == 2
        assert row_count > 0
        assert profiles[0]["column_name"] == "price"
        assert "missing_count" in profiles[0]
        assert "distinct_count" in profiles[0]
        assert profiles[0]["dtype"] in ("string", "integer", "float", "boolean")

    def test_detect_anomalies_flags_high_missing(self):
        from handlers.shared.quality_utils import detect_anomalies

        profiles = [
            {"column_name": "a", "missing_count": 50},
            {"column_name": "b", "missing_count": 5},
        ]
        count, flagged = detect_anomalies(profiles, 100, 0.1)
        assert count >= 1
        assert any(f["column"] == "a" for f in flagged)

    def test_validate_completeness_score(self):
        from handlers.shared.quality_utils import validate_completeness

        profiles = [
            {"column_name": "x", "missing_count": 10},
            {"column_name": "y", "missing_count": 0},
        ]
        results, score = validate_completeness(profiles, 100, 0.1)
        assert len(results) == 2
        assert 0 <= score <= 1
        assert results[1]["passed"] is True

    def test_validate_accuracy_determinism(self):
        from handlers.shared.quality_utils import validate_accuracy

        profiles = [{"column_name": "col_a", "dtype": "integer"}]
        r1, s1 = validate_accuracy(profiles, 5)
        r2, s2 = validate_accuracy(profiles, 5)
        assert r1 == r2
        assert s1 == s2

    def test_compute_scores_weighted(self):
        from handlers.shared.quality_utils import compute_scores

        scores, overall = compute_scores(0.9, 0.8, 0.4, 0.35, 0.25)
        assert len(scores) == 3
        assert all("dimension" in s for s in scores)
        assert 0 <= overall <= 1

    def test_assign_grade_mapping(self):
        from handlers.shared.quality_utils import assign_grade

        assert assign_grade(0.95)[0] == "A"
        assert assign_grade(0.85)[0] == "B"
        assert assign_grade(0.75)[0] == "C"
        assert assign_grade(0.65)[0] == "D"
        assert assign_grade(0.45)[0] == "F"

    def test_plan_remediation_actions(self):
        from handlers.shared.quality_utils import plan_remediation

        results = [
            {"check_name": "completeness:col_a", "passed": False, "score": 0.5},
            {"check_name": "accuracy:col_b", "passed": True, "score": 0.9},
        ]
        flagged = [{"column": "col_a", "missing_rate": 0.3}]
        actions = plan_remediation(results, flagged)
        assert len(actions) >= 2
        assert actions[0]["target_column"] == "col_a"

    def test_generate_report_structure(self):
        from handlers.shared.quality_utils import generate_report

        scores = [
            {"dimension": "completeness", "raw_score": 0.9, "weighted_score": 0.36},
            {"dimension": "accuracy", "raw_score": 0.8, "weighted_score": 0.28},
        ]
        report = generate_report("sales", "B", True, 0.85, scores, [])
        assert report["dataset"] == "sales"
        assert report["grade"] == "B"
        assert report["passed"] is True
        assert "dimensions" in report
        assert "PASSED" in report["summary"]


# ---------------------------------------------------------------------------
# TestProfilingHandlers
# ---------------------------------------------------------------------------
class TestProfilingHandlers:
    def test_profile_dataset_default(self):
        from handlers.profiling.profiling_handlers import handle_profile_dataset

        result = handle_profile_dataset({"dataset": "orders", "columns": ["id", "amount"]})
        assert "profiles" in result
        assert "row_count" in result
        assert len(result["profiles"]) == 2

    def test_detect_anomalies_default(self):
        from handlers.profiling.profiling_handlers import handle_detect_anomalies

        profiles = [{"column_name": "col", "missing_count": 500}]
        result = handle_detect_anomalies(
            {"profiles": profiles, "row_count": 1000, "missing_threshold": 0.1}
        )
        assert "anomaly_count" in result
        assert "flagged_columns" in result

    def test_profile_dataset_json_string(self):
        from handlers.profiling.profiling_handlers import handle_profile_dataset

        result = handle_profile_dataset(
            {
                "dataset": "test",
                "columns": json.dumps(["a", "b"]),
            }
        )
        assert len(result["profiles"]) == 2


# ---------------------------------------------------------------------------
# TestValidationHandlers
# ---------------------------------------------------------------------------
class TestValidationHandlers:
    def test_validate_completeness_default(self):
        from handlers.validation.validation_handlers import handle_validate_completeness

        profiles = [{"column_name": "x", "missing_count": 5}]
        result = handle_validate_completeness({"profiles": profiles, "row_count": 100})
        assert "results" in result
        assert "completeness_score" in result

    def test_validate_completeness_json_string(self):
        from handlers.validation.validation_handlers import handle_validate_completeness

        profiles = [{"column_name": "x", "missing_count": 5}]
        result = handle_validate_completeness(
            {
                "profiles": json.dumps(profiles),
                "row_count": 100,
            }
        )
        assert "results" in result

    def test_validate_accuracy_default(self):
        from handlers.validation.validation_handlers import handle_validate_accuracy

        profiles = [{"column_name": "col", "dtype": "integer"}]
        result = handle_validate_accuracy({"profiles": profiles})
        assert "results" in result
        assert "accuracy_score" in result

    def test_validate_accuracy_custom_max(self):
        from handlers.validation.validation_handlers import handle_validate_accuracy

        profiles = [{"column_name": "col", "dtype": "float"}]
        result = handle_validate_accuracy({"profiles": profiles, "type_error_max": 10})
        assert "accuracy_score" in result


# ---------------------------------------------------------------------------
# TestScoringHandlers
# ---------------------------------------------------------------------------
class TestScoringHandlers:
    def test_compute_scores_default(self):
        from handlers.scoring.scoring_handlers import handle_compute_scores

        result = handle_compute_scores(
            {
                "completeness_score": 0.9,
                "accuracy_score": 0.8,
            }
        )
        assert "scores" in result
        assert "overall" in result
        assert len(result["scores"]) == 3

    def test_compute_scores_custom_weights(self):
        from handlers.scoring.scoring_handlers import handle_compute_scores

        result = handle_compute_scores(
            {
                "completeness_score": 0.9,
                "accuracy_score": 0.8,
                "w_completeness": 0.5,
                "w_accuracy": 0.3,
                "w_freshness": 0.2,
            }
        )
        assert "overall" in result

    def test_assign_grade_default(self):
        from handlers.scoring.scoring_handlers import handle_assign_grade

        result = handle_assign_grade({"overall": 0.85})
        assert result["grade"] == "B"
        assert result["passed"] is True

    def test_assign_grade_failing(self):
        from handlers.scoring.scoring_handlers import handle_assign_grade

        result = handle_assign_grade({"overall": 0.5, "min_score": 0.7})
        assert result["grade"] == "F"
        assert result["passed"] is False


# ---------------------------------------------------------------------------
# TestRemediationHandlers
# ---------------------------------------------------------------------------
class TestRemediationHandlers:
    def test_plan_remediation_default(self):
        from handlers.remediation.remediation_handlers import handle_plan_remediation

        results = [{"check_name": "completeness:col", "passed": False, "score": 0.4}]
        result = handle_plan_remediation({"results": results})
        assert "actions" in result
        assert len(result["actions"]) >= 1

    def test_plan_remediation_json_string(self):
        from handlers.remediation.remediation_handlers import handle_plan_remediation

        results = [{"check_name": "accuracy:col", "passed": False, "score": 0.3}]
        result = handle_plan_remediation(
            {
                "results": json.dumps(results),
                "flagged_columns": json.dumps([{"column": "col", "missing_rate": 0.5}]),
            }
        )
        assert "actions" in result

    def test_generate_report_default(self):
        from handlers.remediation.remediation_handlers import handle_generate_report

        scores = [{"dimension": "completeness", "raw_score": 0.9, "weighted_score": 0.36}]
        result = handle_generate_report(
            {
                "dataset": "sales",
                "grade": "B",
                "passed": True,
                "overall": 0.85,
                "scores": scores,
                "actions": [],
            }
        )
        assert "report" in result
        assert result["report"]["grade"] == "B"

    def test_generate_report_json_string(self):
        from handlers.remediation.remediation_handlers import handle_generate_report

        result = handle_generate_report(
            {
                "dataset": "test",
                "grade": "A",
                "passed": "true",
                "overall": "0.95",
                "scores": json.dumps([]),
                "actions": json.dumps([]),
            }
        )
        assert "report" in result


# ---------------------------------------------------------------------------
# TestDispatch
# ---------------------------------------------------------------------------
class TestDispatch:
    def test_profiling_dispatch(self):
        from handlers.profiling.profiling_handlers import _DISPATCH

        assert len(_DISPATCH) == 2
        assert "dataquality.Profiling.ProfileDataset" in _DISPATCH
        assert "dataquality.Profiling.DetectAnomalies" in _DISPATCH

    def test_validation_dispatch(self):
        from handlers.validation.validation_handlers import _DISPATCH

        assert len(_DISPATCH) == 2
        assert "dataquality.Validation.ValidateCompleteness" in _DISPATCH
        assert "dataquality.Validation.ValidateAccuracy" in _DISPATCH

    def test_scoring_dispatch(self):
        from handlers.scoring.scoring_handlers import _DISPATCH

        assert len(_DISPATCH) == 2
        assert "dataquality.Scoring.ComputeScores" in _DISPATCH
        assert "dataquality.Scoring.AssignGrade" in _DISPATCH

    def test_remediation_dispatch(self):
        from handlers.remediation.remediation_handlers import _DISPATCH

        assert len(_DISPATCH) == 2
        assert "dataquality.Remediation.PlanRemediation" in _DISPATCH
        assert "dataquality.Remediation.GenerateReport" in _DISPATCH

    def test_total_handler_count(self):
        from handlers.profiling.profiling_handlers import _DISPATCH as d1
        from handlers.remediation.remediation_handlers import _DISPATCH as d4
        from handlers.scoring.scoring_handlers import _DISPATCH as d3
        from handlers.validation.validation_handlers import _DISPATCH as d2

        assert len(d1) + len(d2) + len(d3) + len(d4) == 8


# ---------------------------------------------------------------------------
# TestCompilation
# ---------------------------------------------------------------------------
class TestCompilation:
    @pytest.fixture()
    def parsed_ast(self):
        from afl.parser import AFLParser

        afl_path = os.path.join(os.path.dirname(__file__), "..", "afl", "quality.afl")
        with open(afl_path) as f:
            source = f.read()
        return AFLParser().parse(source)

    def test_afl_parses(self, parsed_ast):
        assert parsed_ast is not None

    def test_schema_count(self, parsed_ast):
        schemas = []
        for ns in parsed_ast.namespaces:
            schemas.extend(ns.schemas)
        assert len(schemas) == 7

    def test_event_facet_count(self, parsed_ast):
        event_facets = []
        for ns in parsed_ast.namespaces:
            event_facets.extend(ns.event_facets)
        assert len(event_facets) == 8

    def test_workflow_count(self, parsed_ast):
        workflows = []
        for ns in parsed_ast.namespaces:
            workflows.extend(ns.workflows)
        assert len(workflows) == 2

    def test_prompt_block_count(self, parsed_ast):
        from afl.ast import PromptBlock

        count = 0
        for ns in parsed_ast.namespaces:
            for ef in ns.event_facets:
                if isinstance(ef.body, PromptBlock):
                    count += 1
        assert count == 8

    def test_mixin_facet_count(self, parsed_ast):
        mixins_ns = [ns for ns in parsed_ast.namespaces if ns.name == "dataquality.mixins"][0]
        assert len(mixins_ns.facets) == 2

    def test_implicit_count(self, parsed_ast):
        implicits = []
        for ns in parsed_ast.namespaces:
            implicits.extend(ns.implicits)
        assert len(implicits) == 2

    def test_array_type_present(self, parsed_ast):
        from afl.ast import ArrayType

        array_count = 0
        for ns in parsed_ast.namespaces:
            for ef in ns.event_facets:
                for p in ef.sig.params:
                    if isinstance(p.type, ArrayType):
                        array_count += 1
                if ef.sig.returns:
                    for r in ef.sig.returns.params:
                        if isinstance(r.type, ArrayType):
                            array_count += 1
            for w in ns.workflows:
                for p in w.sig.params:
                    if isinstance(p.type, ArrayType):
                        array_count += 1
        assert array_count >= 10, f"Expected >=10 array annotations, got {array_count}"


# ---------------------------------------------------------------------------
# TestAgentIntegration
# ---------------------------------------------------------------------------
class TestAgentIntegration:
    def test_quality_registry_dispatches_all_handlers(self):
        from handlers.profiling.profiling_handlers import _DISPATCH as d1
        from handlers.remediation.remediation_handlers import _DISPATCH as d4
        from handlers.scoring.scoring_handlers import _DISPATCH as d3
        from handlers.validation.validation_handlers import _DISPATCH as d2

        from afl.runtime.agent import ToolRegistry

        registry = ToolRegistry()
        for dispatch in [d1, d2, d3, d4]:
            for facet_name, handler in dispatch.items():
                tool_name = facet_name.split(".")[-1]
                registry.register(tool_name, handler)

        tool_names = [
            "ProfileDataset",
            "DetectAnomalies",
            "ValidateCompleteness",
            "ValidateAccuracy",
            "ComputeScores",
            "AssignGrade",
            "PlanRemediation",
            "GenerateReport",
        ]
        for name in tool_names:
            assert registry.has_handler(name), f"Missing handler: {name}"

    def test_claude_agent_runner_with_custom_handlers(self):
        from handlers.profiling.profiling_handlers import handle_profile_dataset

        from afl.runtime import Evaluator, ExecutionStatus, MemoryStore, Telemetry
        from afl.runtime.agent import ClaudeAgentRunner, ToolRegistry

        store = MemoryStore()
        evaluator = Evaluator(persistence=store, telemetry=Telemetry(enabled=False))

        registry = ToolRegistry()
        registry.register("ProfileDataset", handle_profile_dataset)

        runner = ClaudeAgentRunner(
            evaluator=evaluator,
            persistence=store,
            tool_registry=registry,
        )

        workflow_ast = {
            "type": "WorkflowDecl",
            "name": "TestQuality",
            "params": [{"name": "dataset", "type": "String"}],
            "returns": [{"name": "result", "type": "String"}],
            "body": {
                "type": "AndThenBlock",
                "steps": [
                    {
                        "type": "StepStmt",
                        "id": "step-profile",
                        "name": "prof",
                        "call": {
                            "type": "CallExpr",
                            "target": "ProfileDataset",
                            "args": [
                                {
                                    "name": "dataset",
                                    "value": {"type": "InputRef", "path": ["dataset"]},
                                }
                            ],
                        },
                    },
                ],
                "yield": {
                    "type": "YieldStmt",
                    "id": "yield-TQ",
                    "call": {
                        "type": "CallExpr",
                        "target": "TestQuality",
                        "args": [
                            {
                                "name": "result",
                                "value": {"type": "StepRef", "path": ["prof", "profiles"]},
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
                    "name": "ProfileDataset",
                    "params": [{"name": "dataset", "type": "String"}],
                    "returns": [{"name": "profiles", "type": "Json"}],
                },
            ],
        }

        result = runner.run(
            workflow_ast,
            inputs={"dataset": "test_data"},
            program_ast=program_ast,
        )

        assert result.success is True
        assert result.status == ExecutionStatus.COMPLETED

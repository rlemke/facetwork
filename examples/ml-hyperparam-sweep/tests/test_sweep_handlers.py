"""Tests for ml-hyperparam-sweep handlers and FFL compilation."""

from __future__ import annotations

import json
import os

import pytest


# ---------------------------------------------------------------------------
# TestMLUtils — shared utility functions
# ---------------------------------------------------------------------------
class TestMLUtils:
    def test_dataset_shape(self):
        from handlers.shared.ml_utils import generate_synthetic_dataset

        ds = generate_synthetic_dataset(name="test", num_features=5, num_samples=200)
        assert ds["name"] == "test"
        assert ds["num_features"] == 5
        assert ds["num_samples"] == 200
        assert len(ds["feature_names"]) == 5

    def test_dataset_determinism(self):
        from handlers.shared.ml_utils import generate_synthetic_dataset

        d1 = generate_synthetic_dataset(seed=99)
        d2 = generate_synthetic_dataset(seed=99)
        assert d1["_labels"] == d2["_labels"]

    def test_split_ratios(self):
        from handlers.shared.ml_utils import split_dataset

        result = split_dataset(num_samples=1000, train_ratio=0.7, val_ratio=0.15)
        assert result["train_count"] == 700
        assert result["val_count"] == 150
        assert result["test_count"] == 150

    def test_split_remainder(self):
        from handlers.shared.ml_utils import split_dataset

        result = split_dataset(num_samples=100, train_ratio=0.8, val_ratio=0.1)
        total = result["train_count"] + result["val_count"] + result["test_count"]
        assert total == 100

    def test_train_loss_range(self):
        from handlers.shared.ml_utils import train_model_stub

        result = train_model_stub(
            dataset_info=None,
            hyperparams={"learning_rate": 0.01, "epochs": 50, "dropout": 0.3},
        )
        assert 0.0 < result["final_loss"] < 10.0

    def test_train_accuracy_range(self):
        from handlers.shared.ml_utils import train_model_stub

        result = train_model_stub(
            dataset_info=None,
            hyperparams={"learning_rate": 0.01, "epochs": 50, "dropout": 0.3},
        )
        assert 0.50 <= result["accuracy"] <= 0.99

    def test_eval_metrics_range(self):
        from handlers.shared.ml_utils import evaluate_model_stub

        result = evaluate_model_stub(model_id="test_model_42")
        assert 0.5 <= result["accuracy"] <= 1.0
        assert 0.5 <= result["precision"] <= 1.0
        assert 0.4 <= result["recall"] <= 1.0
        assert 0.0 < result["f1_score"] <= 1.0
        assert len(result["confusion_matrix"]) == 2

    def test_compare_best_selection(self):
        from handlers.shared.ml_utils import compare_results

        results = [
            {"model_id": "a", "f1_score": 0.80},
            {"model_id": "b", "f1_score": 0.95},
            {"model_id": "c", "f1_score": 0.70},
        ]
        comp = compare_results(results, metric_name="f1_score")
        assert comp["best_model_id"] == "b"
        assert len(comp["ranking"]) == 3
        assert comp["ranking"][0]["model_id"] == "b"


# ---------------------------------------------------------------------------
# TestDataHandlers
# ---------------------------------------------------------------------------
class TestDataHandlers:
    def test_prepare_default(self):
        from handlers.data.data_handlers import handle_prepare_dataset

        result = handle_prepare_dataset(
            {"dataset_name": "test_ds", "num_features": 5, "num_samples": 100}
        )
        ds = result["dataset"]
        assert ds["name"] == "test_ds"
        assert ds["num_features"] == 5
        assert ds["num_samples"] == 100
        assert "_labels" not in ds

    def test_prepare_max_samples(self):
        from handlers.data.data_handlers import handle_prepare_dataset

        result = handle_prepare_dataset({"num_samples": 5000})
        assert result["dataset"]["num_samples"] == 5000

    def test_split_ratios(self):
        from handlers.data.data_handlers import handle_split_dataset

        result = handle_split_dataset(
            {
                "dataset": {"num_samples": 1000},
                "config": {"train_ratio": 0.7, "val_ratio": 0.15, "random_seed": 42},
            }
        )
        assert result["train_count"] == 700
        assert result["val_count"] == 150
        assert result["test_count"] == 150

    def test_split_json_string_params(self):
        from handlers.data.data_handlers import handle_split_dataset

        result = handle_split_dataset(
            {
                "dataset": json.dumps({"num_samples": 200}),
                "config": json.dumps({"train_ratio": 0.8, "val_ratio": 0.1}),
            }
        )
        assert result["train_count"] == 160
        assert result["val_count"] == 20
        assert result["test_count"] == 20


# ---------------------------------------------------------------------------
# TestTrainingHandlers
# ---------------------------------------------------------------------------
class TestTrainingHandlers:
    def test_result_structure(self):
        from handlers.training.training_handlers import handle_train_model

        result = handle_train_model(
            {
                "dataset_name": "test",
                "hyperparams": {
                    "learning_rate": 0.01,
                    "epochs": 50,
                    "dropout": 0.3,
                    "batch_size": 32,
                },
                "model_config": {"model_type": "mlp"},
                "run_label": "test_run",
            }
        )
        tr = result["result"]
        assert "model_id" in tr
        assert "final_loss" in tr
        assert "accuracy" in tr
        assert tr["run_label"] == "test_run"

    def test_json_string_params(self):
        from handlers.training.training_handlers import handle_train_model

        result = handle_train_model(
            {
                "dataset_name": "test",
                "hyperparams": json.dumps({"learning_rate": 0.01, "epochs": 10}),
                "model_config": json.dumps({"model_type": "cnn"}),
                "run_label": "json_run",
            }
        )
        assert "mlp" not in result["result"]["model_id"]

    def test_different_configs_different_metrics(self):
        from handlers.training.training_handlers import handle_train_model

        r1 = handle_train_model(
            {
                "hyperparams": {"learning_rate": 0.001, "epochs": 100, "dropout": 0.3},
                "run_label": "low_lr",
            }
        )
        r2 = handle_train_model(
            {
                "hyperparams": {"learning_rate": 0.1, "epochs": 10, "dropout": 0.0},
                "run_label": "high_lr",
            }
        )
        assert r1["result"]["final_loss"] != r2["result"]["final_loss"]


# ---------------------------------------------------------------------------
# TestEvaluationHandlers
# ---------------------------------------------------------------------------
class TestEvaluationHandlers:
    def test_eval_fields(self):
        from handlers.evaluation.evaluation_handlers import handle_evaluate_model

        result = handle_evaluate_model({"model_id": "mlp_test_42", "test_path": "/data/test.csv"})
        ev = result["result"]
        for field in (
            "model_id",
            "accuracy",
            "precision",
            "recall",
            "f1_score",
            "confusion_matrix",
        ):
            assert field in ev

    def test_confusion_matrix(self):
        from handlers.evaluation.evaluation_handlers import handle_evaluate_model

        result = handle_evaluate_model({"model_id": "mlp_cm_42"})
        cm = result["result"]["confusion_matrix"]
        assert len(cm) == 2
        assert len(cm[0]) == 2

    def test_compare_selects_highest(self):
        from handlers.evaluation.evaluation_handlers import handle_compare_to_best

        result = handle_compare_to_best(
            {
                "eval_results": [
                    {"model_id": "a", "f1_score": 0.70},
                    {"model_id": "b", "f1_score": 0.90},
                    {"model_id": "c", "f1_score": 0.85},
                ],
                "metric_name": "f1_score",
            }
        )
        assert result["comparison"]["best_model_id"] == "b"

    def test_compare_default_metric(self):
        from handlers.evaluation.evaluation_handlers import handle_compare_to_best

        result = handle_compare_to_best(
            {
                "eval_results": [
                    {"model_id": "x", "f1_score": 0.88},
                    {"model_id": "y", "f1_score": 0.92},
                ],
            }
        )
        assert result["comparison"]["metric_name"] == "f1_score"
        assert result["comparison"]["best_model_id"] == "y"


# ---------------------------------------------------------------------------
# TestReportingHandlers
# ---------------------------------------------------------------------------
class TestReportingHandlers:
    def test_report_structure(self):
        from handlers.reporting.report_handlers import handle_generate_sweep_report

        result = handle_generate_sweep_report(
            {
                "dataset_name": "test_ds",
                "comparison": {
                    "best_model_id": "best_1",
                    "metric_name": "f1_score",
                    "ranking": [{"model_id": "best_1", "score": 0.95}],
                    "summary": "Best model: best_1",
                },
                "sweep_config": {"num_configs": 4},
            }
        )
        rpt = result["report"]
        assert rpt["dataset_name"] == "test_ds"
        assert "timestamp" in rpt
        assert "summary_text" in rpt

    def test_total_configs_matches(self):
        from handlers.reporting.report_handlers import handle_generate_sweep_report

        ranking = [{"model_id": f"m{i}", "score": 0.8 + i * 0.01} for i in range(3)]
        result = handle_generate_sweep_report(
            {
                "dataset_name": "ds",
                "comparison": {
                    "best_model_id": "m2",
                    "metric_name": "accuracy",
                    "ranking": ranking,
                    "summary": "ok",
                },
                "sweep_config": {},
            }
        )
        assert result["report"]["total_configs"] == 3


# ---------------------------------------------------------------------------
# TestDispatch
# ---------------------------------------------------------------------------
class TestDispatch:
    def test_data_dispatch(self):
        from handlers.data.data_handlers import _DISPATCH

        assert len(_DISPATCH) == 2
        assert "ml.Data.PrepareDataset" in _DISPATCH
        assert "ml.Data.SplitDataset" in _DISPATCH

    def test_training_dispatch(self):
        from handlers.training.training_handlers import _DISPATCH

        assert len(_DISPATCH) == 1
        assert "ml.Training.TrainModel" in _DISPATCH

    def test_evaluation_dispatch(self):
        from handlers.evaluation.evaluation_handlers import _DISPATCH

        assert len(_DISPATCH) == 2
        assert "ml.Evaluation.EvaluateModel" in _DISPATCH
        assert "ml.Evaluation.CompareToBestModel" in _DISPATCH

    def test_reporting_dispatch(self):
        from handlers.reporting.report_handlers import _DISPATCH

        assert len(_DISPATCH) == 1
        assert "ml.Reporting.GenerateSweepReport" in _DISPATCH

    def test_total_handler_count(self):
        from handlers.data.data_handlers import _DISPATCH as d1
        from handlers.evaluation.evaluation_handlers import _DISPATCH as d3
        from handlers.reporting.report_handlers import _DISPATCH as d4
        from handlers.training.training_handlers import _DISPATCH as d2

        assert len(d1) + len(d2) + len(d3) + len(d4) == 6


# ---------------------------------------------------------------------------
# TestCompilation
# ---------------------------------------------------------------------------
class TestCompilation:
    @pytest.fixture()
    def parsed_ast(self):
        from facetwork.parser import FFLParser

        afl_path = os.path.join(os.path.dirname(__file__), "..", "afl", "sweep.ffl")
        with open(afl_path) as f:
            source = f.read()
        return FFLParser().parse(source)

    def test_afl_parses(self, parsed_ast):
        assert parsed_ast is not None

    def test_schema_count(self, parsed_ast):
        schemas = []
        for ns in parsed_ast.namespaces:
            schemas.extend(ns.schemas)
        assert len(schemas) == 8

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
        mixins_ns = [ns for ns in parsed_ast.namespaces if ns.name == "ml.mixins"][0]
        assert len(mixins_ns.facets) == 4

    def test_implicit_count(self, parsed_ast):
        implicits = []
        for ns in parsed_ast.namespaces:
            implicits.extend(ns.implicits)
        assert len(implicits) == 3

    def test_prompt_block_present(self, parsed_ast):
        from facetwork.ast import PromptBlock

        reporting_ns = [ns for ns in parsed_ast.namespaces if ns.name == "ml.Reporting"][0]
        ef = reporting_ns.event_facets[0]
        assert isinstance(ef.body, PromptBlock)
        assert ef.body.system is not None
        assert ef.body.template is not None
        assert ef.body.model is not None

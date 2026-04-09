"""Tests for dashboard step routes."""

from unittest.mock import MagicMock

from facetwork.dashboard.routes.execution.steps import (
    _find_statement_in_block,
    _resolve_step_names,
)


class TestFindStatementInBlock:
    """Test _find_statement_in_block helper."""

    def test_finds_step_by_id(self):
        block = {
            "steps": [
                {"id": "stmt-1", "name": "step1", "call": {"target": "DoWork"}},
                {"id": "stmt-2", "name": "step2", "call": {"target": "Process"}},
            ]
        }
        result = _find_statement_in_block("stmt-1", block)
        assert result == "step1 = DoWork()"

    def test_returns_none_when_not_found(self):
        block = {
            "steps": [
                {"id": "stmt-1", "name": "step1", "call": {"target": "DoWork"}},
            ]
        }
        assert _find_statement_in_block("nonexistent", block) is None

    def test_step_with_name_only(self):
        block = {
            "steps": [
                {"id": "stmt-1", "name": "step1", "call": {}},
            ]
        }
        result = _find_statement_in_block("stmt-1", block)
        assert result == "step1"

    def test_step_with_target_only(self):
        block = {
            "steps": [
                {"id": "stmt-1", "name": "", "call": {"target": "Process"}},
            ]
        }
        result = _find_statement_in_block("stmt-1", block)
        assert result == "Process"

    def test_finds_in_nested_body(self):
        block = {
            "steps": [
                {
                    "id": "stmt-1",
                    "name": "outer",
                    "call": {"target": "Outer"},
                    "body": {
                        "steps": [
                            {"id": "stmt-2", "name": "inner", "call": {"target": "Inner"}},
                        ]
                    },
                },
            ]
        }
        result = _find_statement_in_block("stmt-2", block)
        assert result == "inner = Inner()"

    def test_finds_yield_statement(self):
        block = {
            "steps": [],
            "yield": {
                "id": "yield-1",
                "call": {"target": "MyWorkflow"},
            },
        }
        result = _find_statement_in_block("yield-1", block)
        assert result == "yield MyWorkflow()"

    def test_yield_without_target(self):
        block = {
            "steps": [],
            "yield": {
                "id": "yield-1",
                "call": {},
            },
        }
        result = _find_statement_in_block("yield-1", block)
        assert result == "yield"

    def test_empty_block(self):
        block = {"steps": []}
        assert _find_statement_in_block("any-id", block) is None


class TestResolveStepNames:
    """Test _resolve_step_names helper."""

    def test_resolves_workflow_name(self):
        step = MagicMock()
        step.workflow_id = "wf-1"
        step.facet_name = None
        step.statement_id = None
        step.container_id = None
        step.block_id = None

        runner = MagicMock()
        runner.workflow_id = "wf-1"
        runner.workflow.name = "TestWorkflow"

        store = MagicMock()
        store.get_all_runners.return_value = [runner]

        names = _resolve_step_names(step, store)
        assert names["workflow"] == "TestWorkflow"

    def test_resolves_facet_name(self):
        step = MagicMock()
        step.workflow_id = None
        step.facet_name = "DoWork"
        step.statement_id = None
        step.container_id = None
        step.block_id = None

        store = MagicMock()
        store.get_all_runners.return_value = []

        names = _resolve_step_names(step, store)
        assert names["facet"] == "DoWork"

    def test_resolves_container_name(self):
        step = MagicMock()
        step.workflow_id = None
        step.facet_name = None
        step.statement_id = None
        step.container_id = "c-1"
        step.block_id = None

        container_step = MagicMock()
        container_step.object_type = "VariableAssignment"
        container_step.facet_name = "Process"

        store = MagicMock()
        store.get_all_runners.return_value = []
        store.get_step.return_value = container_step

        names = _resolve_step_names(step, store)
        assert "container" in names
        assert "Process" in names["container"]

    def test_resolves_block_name(self):
        step = MagicMock()
        step.workflow_id = None
        step.facet_name = None
        step.statement_id = None
        step.container_id = None
        step.block_id = "b-1"

        block_step = MagicMock()
        block_step.object_type = "AndThen"
        block_step.facet_name = None

        store = MagicMock()
        store.get_all_runners.return_value = []
        store.get_step.return_value = block_step

        names = _resolve_step_names(step, store)
        assert "block" in names
        assert "AndThen" in names["block"]

    def test_handles_missing_runner(self):
        step = MagicMock()
        step.workflow_id = "wf-1"
        step.facet_name = None
        step.statement_id = None
        step.container_id = None
        step.block_id = None

        store = MagicMock()
        store.get_all_runners.return_value = []

        names = _resolve_step_names(step, store)
        assert "workflow" not in names

    def test_handles_exception_gracefully(self):
        step = MagicMock()
        step.workflow_id = "wf-1"
        step.facet_name = None
        step.statement_id = None
        step.container_id = "c-1"
        step.block_id = None

        store = MagicMock()
        store.get_all_runners.side_effect = Exception("db error")
        store.get_step.side_effect = Exception("db error")

        names = _resolve_step_names(step, store)
        # Should not raise, just return empty for failed lookups
        assert "workflow" not in names
        assert "container" not in names

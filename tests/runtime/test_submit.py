"""Tests for FFL CLI submit module (afl.runtime.submit)."""

from unittest.mock import patch

import pytest

from facetwork.runtime.submit import main

try:
    import mongomock

    MONGOMOCK_AVAILABLE = True
except ImportError:
    MONGOMOCK_AVAILABLE = False


# ============================================================================
# Argument parsing and source loading
# ============================================================================


class TestArgumentParsing:
    """Test CLI argument validation."""

    def test_help_flag(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            main(["--help"])
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "--workflow" in captured.out

    def test_missing_workflow_flag(self, capsys):
        """--workflow is required."""
        with pytest.raises(SystemExit) as exc_info:
            main(["some.ffl"])
        assert exc_info.value.code != 0

    def test_no_source_files(self, capsys):
        """Error when no source files provided."""
        result = main(["--workflow", "Test"])
        assert result == 1
        captured = capsys.readouterr()
        assert "No source files" in captured.err

    def test_conflicting_input_modes(self, tmp_path, capsys):
        """Cannot mix positional input with --primary."""
        f = tmp_path / "a.ffl"
        f.write_text("facet A()")
        result = main([str(f), "--primary", str(f), "--workflow", "A"])
        assert result == 1
        captured = capsys.readouterr()
        assert "Cannot use positional input" in captured.err

    def test_file_not_found(self, capsys):
        result = main(["nonexistent.ffl", "--workflow", "Test"])
        assert result == 1
        captured = capsys.readouterr()
        assert "File not found" in captured.err

    def test_primary_file_not_found(self, capsys):
        result = main(["--primary", "nonexistent.ffl", "--workflow", "Test"])
        assert result == 1
        captured = capsys.readouterr()
        assert "File not found" in captured.err

    def test_library_file_not_found(self, tmp_path, capsys):
        f = tmp_path / "main.ffl"
        f.write_text("facet A()")
        result = main(["--primary", str(f), "--library", "missing.ffl", "--workflow", "A"])
        assert result == 1
        captured = capsys.readouterr()
        assert "File not found" in captured.err


# ============================================================================
# Parse and validation errors
# ============================================================================


class TestCompileErrors:
    """Test parse/validate error reporting."""

    def test_parse_error(self, tmp_path, capsys):
        f = tmp_path / "bad.ffl"
        f.write_text("this is not valid FFL syntax @@@@")
        result = main([str(f), "--workflow", "Test"])
        assert result == 1
        captured = capsys.readouterr()
        assert "Parse error" in captured.err

    def test_validation_error(self, tmp_path, capsys):
        f = tmp_path / "dup.ffl"
        f.write_text("facet Dup()\nfacet Dup()")
        result = main([str(f), "--workflow", "Dup"])
        assert result == 1
        captured = capsys.readouterr()
        assert "Validation error" in captured.err

    def test_workflow_not_found(self, tmp_path, capsys):
        f = tmp_path / "ok.ffl"
        f.write_text("facet Hello()")
        result = main([str(f), "--workflow", "NonExistent"])
        assert result == 1
        captured = capsys.readouterr()
        assert "not found" in captured.err

    def test_invalid_inputs_json(self, tmp_path, capsys):
        f = tmp_path / "wf.ffl"
        f.write_text("namespace ns {\n  workflow Run()\n}")
        result = main([str(f), "--workflow", "ns.Run", "--inputs", "not-json"])
        assert result == 1
        captured = capsys.readouterr()
        assert "Invalid --inputs JSON" in captured.err


# ============================================================================
# Workflow lookup
# ============================================================================


class TestWorkflowLookup:
    """Test that workflows can be found by qualified and simple names."""

    @pytest.fixture
    def simple_wf(self, tmp_path):
        f = tmp_path / "simple.ffl"
        f.write_text("namespace test {\n  workflow Run()\n}")
        return f

    @pytest.fixture
    def namespaced_wf(self, tmp_path):
        f = tmp_path / "ns.ffl"
        f.write_text("namespace app {\n  workflow Execute()\n}")
        return f

    @pytest.fixture
    def nested_wf(self, tmp_path):
        f = tmp_path / "nested.ffl"
        f.write_text("namespace a {\n  namespace b {\n    workflow Deep()\n  }\n}")
        return f

    def test_simple_workflow_found(self, simple_wf, capsys):
        """Simple name lookup — fails at MongoDB step (workflow was found)."""
        _result = main([str(simple_wf), "--workflow", "test.Run"])
        captured = capsys.readouterr()
        # Should fail at MongoDB connection, not "not found"
        assert "not found" not in captured.err

    def test_qualified_workflow_found(self, namespaced_wf, capsys):
        _result = main([str(namespaced_wf), "--workflow", "app.Execute"])
        captured = capsys.readouterr()
        assert "not found" not in captured.err

    def test_nested_qualified_workflow_found(self, nested_wf, capsys):
        _result = main([str(nested_wf), "--workflow", "a.b.Deep"])
        captured = capsys.readouterr()
        assert "not found" not in captured.err

    def test_flat_dotted_namespace_found(self, tmp_path, capsys):
        """Multi-file compile produces flat dotted namespace names."""
        lib = tmp_path / "lib.ffl"
        lib.write_text("namespace osm.geocode {\n  schema Region { name: String }\n}")
        main_f = tmp_path / "main.ffl"
        main_f.write_text(
            "namespace osm.sample {\n  use osm.geocode\n  workflow Download(region: osm.Region)\n}"
        )
        _result = main(
            [
                "--primary",
                str(main_f),
                "--library",
                str(lib),
                "--workflow",
                "osm.sample.Download",
            ]
        )
        captured = capsys.readouterr()
        assert "not found" not in captured.err

    def test_wrong_namespace_not_found(self, namespaced_wf, capsys):
        result = main([str(namespaced_wf), "--workflow", "wrong.Execute"])
        assert result == 1
        captured = capsys.readouterr()
        assert "not found" in captured.err


# ============================================================================
# MongoDB integration (mongomock)
# ============================================================================


@pytest.mark.skipif(not MONGOMOCK_AVAILABLE, reason="mongomock not installed")
class TestMongoSubmit:
    """Test full submit flow with mongomock-backed store."""

    @pytest.fixture
    def mock_store(self):
        from facetwork.runtime.mongo_store import MongoStore

        client = mongomock.MongoClient()
        store = MongoStore(database_name="afl_test_submit", client=client)
        yield store
        store.drop_database()
        store.close()

    def _run_with_mock_store(self, mock_store, args):
        """Run main() with _connect_store patched to return mock_store."""
        with patch("facetwork.runtime.submit._connect_store", return_value=mock_store):
            return main(args)

    @staticmethod
    def _parse_ids(output: str) -> dict[str, str]:
        """Extract Runner ID and Flow ID from submit output."""
        ids = {}
        for line in output.splitlines():
            if "Runner ID:" in line:
                ids["runner_id"] = line.split("Runner ID:")[1].strip()
            elif "Flow ID:" in line:
                ids["flow_id"] = line.split("Flow ID:")[1].strip()
        return ids

    def test_submit_simple_workflow(self, tmp_path, mock_store, capsys):
        f = tmp_path / "wf.ffl"
        f.write_text("namespace test {\n  workflow Run()\n}")
        result = self._run_with_mock_store(mock_store, [str(f), "--workflow", "test.Run"])
        assert result == 0
        captured = capsys.readouterr()
        assert "Submitted workflow 'test.Run'" in captured.out
        ids = self._parse_ids(captured.out)
        assert "runner_id" in ids
        assert "flow_id" in ids

        # Verify entities in store
        runner = mock_store.get_runner(ids["runner_id"])
        assert runner is not None
        assert runner.state == "created"

        task = mock_store.get_tasks_by_runner(ids["runner_id"])[0]
        assert task.name == "fw:execute"
        assert task.state == "pending"
        assert task.data["workflow_name"] == "test.Run"

    def test_submit_namespaced_workflow(self, tmp_path, mock_store, capsys):
        f = tmp_path / "ns.ffl"
        f.write_text("namespace app {\n  workflow Deploy()\n}")
        result = self._run_with_mock_store(mock_store, [str(f), "--workflow", "app.Deploy"])
        assert result == 0
        ids = self._parse_ids(capsys.readouterr().out)
        task = mock_store.get_tasks_by_runner(ids["runner_id"])[0]
        assert task.data["workflow_name"] == "app.Deploy"

    def test_submit_with_primary_and_library(self, tmp_path, mock_store, capsys):
        lib = tmp_path / "types.ffl"
        lib.write_text("namespace types {\n  schema Config { url: String }\n}")
        main_f = tmp_path / "main.ffl"
        main_f.write_text("namespace app {\n  use types\n  workflow Start(cfg: types.Config)\n}")
        result = self._run_with_mock_store(
            mock_store,
            ["--primary", str(main_f), "--library", str(lib), "--workflow", "app.Start"],
        )
        assert result == 0

        # Verify compiled_sources contains both files' content
        ids = self._parse_ids(capsys.readouterr().out)
        flow = mock_store.get_flow(ids["flow_id"])
        assert flow is not None
        source_content = flow.compiled_sources[0].content
        assert "namespace types" in source_content
        assert "namespace app" in source_content

    def test_submit_with_default_params(self, tmp_path, mock_store, capsys):
        f = tmp_path / "wf.ffl"
        f.write_text('namespace n {\n  workflow Go(count: Int = 5, name: String = "test")\n}')
        result = self._run_with_mock_store(mock_store, [str(f), "--workflow", "n.Go"])
        assert result == 0
        ids = self._parse_ids(capsys.readouterr().out)
        task = mock_store.get_tasks_by_runner(ids["runner_id"])[0]
        inputs = task.data["inputs"]
        assert inputs["count"] == 5
        assert inputs["name"] == "test"

    def test_submit_with_input_overrides(self, tmp_path, mock_store, capsys):
        f = tmp_path / "wf.ffl"
        f.write_text("namespace n {\n  workflow Go(count: Int = 5)\n}")
        result = self._run_with_mock_store(
            mock_store,
            [str(f), "--workflow", "n.Go", "--inputs", '{"count": 99}'],
        )
        assert result == 0
        ids = self._parse_ids(capsys.readouterr().out)
        task = mock_store.get_tasks_by_runner(ids["runner_id"])[0]
        assert task.data["inputs"]["count"] == 99

    def test_submit_custom_task_list(self, tmp_path, mock_store, capsys):
        f = tmp_path / "wf.ffl"
        f.write_text("namespace test {\n  workflow Run()\n}")
        result = self._run_with_mock_store(
            mock_store, [str(f), "--workflow", "test.Run", "--task-list", "priority"]
        )
        assert result == 0
        ids = self._parse_ids(capsys.readouterr().out)
        task = mock_store.get_tasks_by_runner(ids["runner_id"])[0]
        assert task.task_list_name == "priority"

    def test_submit_creates_linked_entities(self, tmp_path, mock_store, capsys):
        """Verify flow, workflow, runner, and task are correctly linked."""
        f = tmp_path / "wf.ffl"
        f.write_text("namespace test {\n  workflow Run()\n}")
        result = self._run_with_mock_store(mock_store, [str(f), "--workflow", "test.Run"])
        assert result == 0

        ids = self._parse_ids(capsys.readouterr().out)
        flow = mock_store.get_flow(ids["flow_id"])
        runner = mock_store.get_runner(ids["runner_id"])
        assert flow is not None
        assert runner is not None

        wf = mock_store.get_workflows_by_flow(ids["flow_id"])[0]
        task = mock_store.get_tasks_by_runner(ids["runner_id"])[0]

        # Check linkage
        assert wf.flow_id == flow.uuid
        assert runner.workflow_id == wf.uuid
        assert task.flow_id == flow.uuid
        assert task.runner_id == runner.uuid
        assert task.data["runner_id"] == runner.uuid
        assert task.data["flow_id"] == flow.uuid

    def test_submit_stores_compiled_ast(self, tmp_path, mock_store, capsys):
        """Verify submit stores compiled_ast on FlowDefinition."""
        f = tmp_path / "wf.ffl"
        f.write_text("namespace n {\n  workflow Go(count: Int = 5)\n}")
        result = self._run_with_mock_store(mock_store, [str(f), "--workflow", "n.Go"])
        assert result == 0
        ids = self._parse_ids(capsys.readouterr().out)
        flow = mock_store.get_flow(ids["flow_id"])
        assert flow is not None
        assert flow.compiled_ast is not None
        assert isinstance(flow.compiled_ast, dict)
        assert "declarations" in flow.compiled_ast

    def test_submit_compiled_ast_has_stable_ids(self, tmp_path, mock_store, capsys):
        """Verify that compiled_ast statement IDs are stable (not regenerated)."""
        f = tmp_path / "wf.ffl"
        f.write_text("namespace n {\n  workflow Go(count: Int = 5)\n}")
        result = self._run_with_mock_store(mock_store, [str(f), "--workflow", "n.Go"])
        assert result == 0
        ids = self._parse_ids(capsys.readouterr().out)
        flow = mock_store.get_flow(ids["flow_id"])

        # Read the compiled_ast twice — same dict, same IDs
        ast1 = flow.compiled_ast
        ast2 = flow.compiled_ast
        assert ast1 == ast2


# ============================================================================
# MongoDB connection error
# ============================================================================


class TestMongoConnectionError:
    """Test graceful handling of MongoDB connection failures."""

    def test_connection_error(self, tmp_path, capsys):
        f = tmp_path / "wf.ffl"
        f.write_text("namespace test {\n  workflow Run()\n}")
        with patch(
            "facetwork.runtime.submit._connect_store",
            side_effect=ConnectionError("refused"),
        ):
            result = main([str(f), "--workflow", "test.Run"])
        assert result == 1
        captured = capsys.readouterr()
        assert "Error connecting to MongoDB" in captured.err

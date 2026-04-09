"""Tests for the event-driven ETL example handlers."""

from __future__ import annotations

import json
import os

import pytest


# ---------------------------------------------------------------------------
# TestETLUtils — utility function tests
# ---------------------------------------------------------------------------
class TestETLUtils:
    def test_extract_csv_returns_records(self):
        from handlers.shared.etl_utils import extract_csv

        records, count = extract_csv("sales.csv")
        assert count >= 3
        assert len(records) == count
        assert all("id" in r and "name" in r and "value" in r for r in records)

    def test_extract_csv_deterministic(self):
        from handlers.shared.etl_utils import extract_csv

        r1, c1 = extract_csv("data.csv")
        r2, c2 = extract_csv("data.csv")
        assert r1 == r2
        assert c1 == c2

    def test_extract_json_returns_records(self):
        from handlers.shared.etl_utils import extract_json

        records, count = extract_json("data.json")
        assert count >= 2
        assert len(records) == count

    def test_validate_schema_all_valid(self):
        from handlers.shared.etl_utils import validate_schema

        records = [{"id": "1", "name": "a"}, {"id": "2", "name": "b"}]
        valid, errors, error_list = validate_schema(records, ["id", "name"])
        assert len(valid) == 2
        assert errors == 0
        assert len(error_list) == 0

    def test_validate_schema_with_errors(self):
        from handlers.shared.etl_utils import validate_schema

        records = [{"id": "1", "name": "a"}, {"id": "2"}]
        valid, errors, error_list = validate_schema(records, ["id", "name"])
        assert len(valid) == 1
        assert errors == 1
        assert error_list[0]["missing_fields"] == ["name"]

    def test_transform_records_dedup(self):
        from handlers.shared.etl_utils import transform_records

        records = [{"id": "1", "v": "a"}, {"id": "1", "v": "b"}, {"id": "2", "v": "c"}]
        transformed, count, dropped = transform_records(records, deduplicate=True)
        assert count == 2
        assert dropped == 1

    def test_load_to_store_success(self):
        from handlers.shared.etl_utils import load_to_store

        result = load_to_store([{"id": "1"}], "warehouse")
        assert result["target"] == "warehouse"
        assert result["rows_written"] == 1
        assert result["status"] == "success"

    def test_load_to_store_empty(self):
        from handlers.shared.etl_utils import load_to_store

        result = load_to_store([], "warehouse")
        assert result["rows_written"] == 0
        assert result["status"] == "empty"

    def test_generate_report_success(self):
        from handlers.shared.etl_utils import generate_report

        result = {"target": "db", "rows_written": 10, "duration_ms": 100, "status": "success"}
        report, success = generate_report("src.csv", "db", 10, 0, result)
        assert success is True
        assert "SUCCESS" in report

    def test_generate_report_partial(self):
        from handlers.shared.etl_utils import generate_report

        result = {"target": "db", "rows_written": 8, "duration_ms": 100, "status": "success"}
        report, success = generate_report("src.csv", "db", 10, 2, result)
        assert success is False
        assert "PARTIAL" in report

    def test_transform_rename_map(self):
        from handlers.shared.etl_utils import transform_records

        records = [{"id": "1", "old_name": "x"}]
        transformed, count, dropped = transform_records(
            records, rename_map={"old_name": "new_name"}
        )
        assert count == 1
        assert "new_name" in transformed[0]
        assert "old_name" not in transformed[0]


# ---------------------------------------------------------------------------
# TestExtractHandlers — extract handler wrapper tests
# ---------------------------------------------------------------------------
class TestExtractHandlers:
    def test_handle_extract_csv(self):
        from handlers.extract.extract_handlers import handle_extract_csv

        result = handle_extract_csv({"source": "test.csv", "delimiter": ","})
        assert "records" in result
        assert "row_count" in result
        assert result["row_count"] == len(result["records"])

    def test_handle_extract_json(self):
        from handlers.extract.extract_handlers import handle_extract_json

        result = handle_extract_json({"source": "data.json", "json_path": "$"})
        assert "records" in result
        assert "row_count" in result

    def test_handle_validate_schema_with_json_string(self):
        from handlers.extract.extract_handlers import handle_validate_schema

        records = json.dumps([{"id": "1", "name": "a"}, {"id": "2"}])
        result = handle_validate_schema(
            {
                "records": records,
                "expected_fields": ["id", "name"],
            }
        )
        assert result["error_count"] == 1
        assert len(result["valid_records"]) == 1

    def test_handle_extract_csv_step_log_list(self):
        from handlers.extract.extract_handlers import handle_extract_csv

        log: list[dict] = []
        handle_extract_csv({"source": "test.csv", "_step_log": log})
        assert len(log) == 1
        assert "Extracted" in log[0]["message"]

    def test_handle_extract_csv_step_log_callable(self):
        from handlers.extract.extract_handlers import handle_extract_csv

        messages: list[tuple[str, str]] = []
        handle_extract_csv(
            {
                "source": "test.csv",
                "_step_log": lambda msg, level: messages.append((msg, level)),
            }
        )
        assert len(messages) == 1
        assert "Extracted" in messages[0][0]


# ---------------------------------------------------------------------------
# TestTransformHandlers
# ---------------------------------------------------------------------------
class TestTransformHandlers:
    def test_handle_transform_records(self):
        from handlers.transform.transform_handlers import handle_transform_records

        records = [{"id": "1", "name": "a"}, {"id": "2", "name": "b"}]
        result = handle_transform_records(
            {
                "records": records,
                "config": {"filter_expr": "true", "rename_map": {}, "deduplicate": False},
            }
        )
        assert result["transform_count"] == 2
        assert result["dropped_count"] == 0

    def test_handle_transform_records_with_json_string(self):
        from handlers.transform.transform_handlers import handle_transform_records

        records = json.dumps([{"id": "1"}, {"id": "1"}, {"id": "2"}])
        result = handle_transform_records(
            {
                "records": records,
                "config": json.dumps(
                    {"filter_expr": "true", "rename_map": {}, "deduplicate": True}
                ),
            }
        )
        assert result["transform_count"] == 2
        assert result["dropped_count"] == 1

    def test_handle_transform_records_step_log(self):
        from handlers.transform.transform_handlers import handle_transform_records

        log: list[dict] = []
        handle_transform_records(
            {
                "records": [{"id": "1"}],
                "config": {},
                "_step_log": log,
            }
        )
        assert len(log) == 1
        assert "Transformed" in log[0]["message"]


# ---------------------------------------------------------------------------
# TestLoadHandlers
# ---------------------------------------------------------------------------
class TestLoadHandlers:
    def test_handle_load_to_store(self):
        from handlers.load.load_handlers import handle_load_to_store

        result = handle_load_to_store(
            {
                "records": [{"id": "1"}],
                "target": "warehouse",
                "mode": "append",
            }
        )
        assert result["result"]["rows_written"] == 1
        assert result["result"]["status"] == "success"

    def test_handle_generate_report(self):
        from handlers.load.load_handlers import handle_generate_report

        load_result = {"target": "db", "rows_written": 5, "duration_ms": 100, "status": "success"}
        result = handle_generate_report(
            {
                "source": "data.csv",
                "target": "db",
                "row_count": 5,
                "error_count": 0,
                "load_result": load_result,
            }
        )
        assert result["success"] is True
        assert "SUCCESS" in result["report"]

    def test_handle_generate_report_with_json_string(self):
        from handlers.load.load_handlers import handle_generate_report

        load_result = json.dumps(
            {"target": "db", "rows_written": 3, "duration_ms": 50, "status": "success"}
        )
        result = handle_generate_report(
            {
                "source": "data.csv",
                "target": "db",
                "row_count": 5,
                "error_count": 2,
                "load_result": load_result,
            }
        )
        assert result["success"] is False

    def test_handle_load_step_log(self):
        from handlers.load.load_handlers import handle_load_to_store

        log: list[dict] = []
        handle_load_to_store(
            {
                "records": [{"id": "1"}],
                "target": "warehouse",
                "_step_log": log,
            }
        )
        assert len(log) == 1
        assert "Loaded" in log[0]["message"]


# ---------------------------------------------------------------------------
# TestDispatch — dispatch table structure and routing
# ---------------------------------------------------------------------------
class TestDispatch:
    def test_extract_dispatch_count(self):
        from handlers.extract.extract_handlers import _DISPATCH

        assert len(_DISPATCH) == 3

    def test_transform_dispatch_count(self):
        from handlers.transform.transform_handlers import _DISPATCH

        assert len(_DISPATCH) == 1

    def test_load_dispatch_count(self):
        from handlers.load.load_handlers import _DISPATCH

        assert len(_DISPATCH) == 2

    def test_all_dispatch_names_have_namespace_prefix(self):
        from handlers.extract.extract_handlers import _DISPATCH as d1
        from handlers.load.load_handlers import _DISPATCH as d3
        from handlers.transform.transform_handlers import _DISPATCH as d2

        all_names = list(d1.keys()) + list(d2.keys()) + list(d3.keys())
        assert len(all_names) == 6
        assert all(n.startswith("etl.") for n in all_names)

    def test_extract_handle_routes_correctly(self):
        from handlers.extract.extract_handlers import handle

        result = handle({"_facet_name": "etl.Extract.ExtractCSV", "source": "test.csv"})
        assert "records" in result

    def test_load_handle_routes_correctly(self):
        from handlers.load.load_handlers import handle

        result = handle(
            {
                "_facet_name": "etl.Load.LoadToStore",
                "records": [],
                "target": "db",
            }
        )
        assert "result" in result


# ---------------------------------------------------------------------------
# TestCompilation — FFL parsing and AST checks
# ---------------------------------------------------------------------------
class TestCompilation:
    @pytest.fixture()
    def parsed_ast(self):
        from facetwork.parser import FFLParser

        afl_path = os.path.join(os.path.dirname(__file__), "..", "afl", "etl.ffl")
        with open(afl_path) as f:
            source = f.read()
        return FFLParser().parse(source)

    def test_afl_parses(self, parsed_ast):
        assert parsed_ast is not None

    def test_schema_count(self, parsed_ast):
        schemas = []
        for ns in parsed_ast.namespaces:
            schemas.extend(ns.schemas)
        assert len(schemas) == 3

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

    def test_namespace_count(self, parsed_ast):
        assert len(parsed_ast.namespaces) == 5

    def test_schemas_in_types_namespace(self, parsed_ast):
        types_ns = [ns for ns in parsed_ast.namespaces if ns.name == "etl.types"]
        assert len(types_ns) == 1
        assert len(types_ns[0].schemas) == 3

    def test_array_type_present(self, parsed_ast):
        """Verify [String] array type annotation appears in the AST."""
        from facetwork.ast import ArrayType

        found_array = False
        for ns in parsed_ast.namespaces:
            for ef in ns.event_facets:
                for p in ef.sig.params:
                    if isinstance(p.type, ArrayType):
                        found_array = True
            for wf in ns.workflows:
                for p in wf.sig.params:
                    if isinstance(p.type, ArrayType):
                        found_array = True
        assert found_array, "Expected at least one [Type] array annotation"

    def test_map_literal_present(self, parsed_ast):
        """Verify #{} map literal appears in the AST."""
        from facetwork.ast import AndThenBlock, MapLiteral

        def _check_args(call):
            for arg in getattr(call, "args", []):
                if isinstance(getattr(arg, "value", None), MapLiteral):
                    return True
            return False

        def _search_and_then(at_block):
            if at_block is None:
                return False
            block = getattr(at_block, "block", None)
            if block is None:
                return False
            for stmt in getattr(block, "steps", []):
                if _check_args(getattr(stmt, "call", None)):
                    return True
                # Recurse into statement-level andThen
                if _search_and_then(getattr(stmt, "body", None)):
                    return True
            return False

        found = False
        for ns in parsed_ast.namespaces:
            for wf in ns.workflows:
                body = wf.body
                if isinstance(body, list):
                    for b in body:
                        if _search_and_then(b):
                            found = True
                elif isinstance(body, AndThenBlock):
                    if _search_and_then(body):
                        found = True
        assert found, "Expected at least one #{} map literal"


# ---------------------------------------------------------------------------
# TestAgentIntegration — end-to-end handler registration
# ---------------------------------------------------------------------------
class TestAgentIntegration:
    def test_registry_runner_poll_once(self):
        """RegistryRunner dispatches all handlers via ToolRegistry."""
        from handlers.extract.extract_handlers import _DISPATCH as d1
        from handlers.load.load_handlers import _DISPATCH as d3
        from handlers.transform.transform_handlers import _DISPATCH as d2

        from facetwork.runtime.agent import ToolRegistry

        registry = ToolRegistry()
        for dispatch in [d1, d2, d3]:
            for facet_name, handler in dispatch.items():
                tool_name = facet_name.split(".")[-1]
                registry.register(tool_name, handler)

        tool_names = [
            "ExtractCSV",
            "ExtractJSON",
            "ValidateSchema",
            "TransformRecords",
            "LoadToStore",
            "GenerateReport",
        ]
        for name in tool_names:
            assert registry.has_handler(name), f"Missing handler: {name}"

    def test_registry_runner_handler_names(self):
        """Verify all dispatch tables have correct namespace prefixes."""
        from handlers.extract.extract_handlers import _DISPATCH as d1
        from handlers.load.load_handlers import _DISPATCH as d3
        from handlers.transform.transform_handlers import _DISPATCH as d2

        all_names = list(d1.keys()) + list(d2.keys()) + list(d3.keys())
        assert len(all_names) == 6
        assert all(n.startswith("etl.") for n in all_names)

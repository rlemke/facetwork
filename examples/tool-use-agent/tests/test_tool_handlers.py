"""Tests for tool-use-agent handlers and FFL compilation."""

from __future__ import annotations

import json
import os

import pytest


# ---------------------------------------------------------------------------
# TestToolUtils
# ---------------------------------------------------------------------------
class TestToolUtils:
    def test_plan_tool_use_structure(self):
        from handlers.shared.tool_utils import plan_tool_use

        result = plan_tool_use("What is quantum computing?")
        assert "strategy" in result
        assert "search_queries" in result
        assert "tool_order" in result
        assert len(result["search_queries"]) >= 1

    def test_select_next_tool_cycling(self):
        from handlers.shared.tool_utils import select_next_tool

        plan = {"tool_order": ["search", "calculate", "execute"]}
        r0 = select_next_tool(plan, completed_tools=[])
        r1 = select_next_tool(plan, completed_tools=["search"])
        r2 = select_next_tool(plan, completed_tools=["search", "calculate"])
        assert r0["next_tool"] == "search"
        assert r1["next_tool"] == "calculate"
        assert r2["next_tool"] == "execute"

    def test_web_search_results_capped(self):
        from handlers.shared.tool_utils import web_search

        result = web_search("test query", max_results=3)
        assert "results" in result
        assert len(result["results"]) <= 3
        assert result["source_count"] <= 3

    def test_web_search_determinism(self):
        from handlers.shared.tool_utils import web_search

        a = web_search("quantum computing", 5)
        b = web_search("quantum computing", 5)
        assert a == b

    def test_deep_search_higher_relevance(self):
        from handlers.shared.tool_utils import deep_search

        sr, knowledge = deep_search("AI research", depth=2)
        assert "results" in sr
        assert "knowledge" in knowledge or "topic" in knowledge
        assert knowledge["confidence"] >= 0.7

    def test_calculate_steps(self):
        from handlers.shared.tool_utils import calculate

        result = calculate("2 + 3", precision=2)
        assert "result" in result
        assert "steps" in result
        assert len(result["steps"]) == 3
        assert result["precision"] == 2

    def test_execute_code_exit_zero(self):
        from handlers.shared.tool_utils import execute_code

        result = execute_code("print('hello')", "python")
        assert result["exit_code"] == 0
        assert result["code"] == "print('hello')"
        assert result["runtime_ms"] >= 10

    def test_format_answer_citations(self):
        from handlers.shared.tool_utils import format_answer

        result = format_answer("synth", ["f1", "f2"], 0.85, "query")
        assert "citations" in result
        assert len(result["citations"]) == 2
        assert result["confidence"] == 0.85


# ---------------------------------------------------------------------------
# TestPlanningHandlers
# ---------------------------------------------------------------------------
class TestPlanningHandlers:
    def test_plan_tool_use_default(self):
        from handlers.planning.planning_handlers import handle_plan_tool_use

        result = handle_plan_tool_use({"query": "test query"})
        assert "plan" in result
        assert "strategy" in result["plan"]

    def test_select_next_tool_default(self):
        from handlers.planning.planning_handlers import handle_select_next_tool

        plan = {"tool_order": ["search", "calculate"]}
        result = handle_select_next_tool({"plan": plan})
        assert "next_tool" in result
        assert result["next_tool"] == "search"

    def test_plan_tool_use_json_string(self):
        from handlers.planning.planning_handlers import handle_plan_tool_use

        tools = ["search", "calculate"]
        result = handle_plan_tool_use(
            {
                "query": "test",
                "available_tools": json.dumps(tools),
            }
        )
        assert "plan" in result


# ---------------------------------------------------------------------------
# TestSearchHandlers
# ---------------------------------------------------------------------------
class TestSearchHandlers:
    def test_web_search_default(self):
        from handlers.search.search_handlers import handle_web_search

        result = handle_web_search({"query": "quantum computing"})
        assert "search_result" in result
        assert "results" in result["search_result"]

    def test_web_search_custom_max(self):
        from handlers.search.search_handlers import handle_web_search

        result = handle_web_search({"query": "AI", "max_results": 3})
        assert result["search_result"]["source_count"] <= 3

    def test_deep_search_default(self):
        from handlers.search.search_handlers import handle_deep_search

        result = handle_deep_search({"query": "machine learning"})
        assert "search_result" in result
        assert "knowledge" in result

    def test_deep_search_json_string(self):
        from handlers.search.search_handlers import handle_deep_search

        result = handle_deep_search(
            {
                "query": "AI",
                "initial_results": json.dumps([{"title": "test"}]),
            }
        )
        assert "search_result" in result


# ---------------------------------------------------------------------------
# TestComputeHandlers
# ---------------------------------------------------------------------------
class TestComputeHandlers:
    def test_calculate_default(self):
        from handlers.compute.compute_handlers import handle_calculate

        result = handle_calculate({"expression": "2 + 3"})
        assert "calculation" in result
        assert "result" in result["calculation"]

    def test_calculate_custom_precision(self):
        from handlers.compute.compute_handlers import handle_calculate

        result = handle_calculate({"expression": "10 / 3", "precision": 4})
        assert result["calculation"]["precision"] == 4

    def test_execute_code_default(self):
        from handlers.compute.compute_handlers import handle_execute_code

        result = handle_execute_code({"code": "print('hi')"})
        assert "code_result" in result
        assert result["code_result"]["exit_code"] == 0

    def test_execute_code_custom_language(self):
        from handlers.compute.compute_handlers import handle_execute_code

        result = handle_execute_code({"code": "echo hi", "language": "bash"})
        assert result["code_result"]["exit_code"] == 0


# ---------------------------------------------------------------------------
# TestOutputHandlers
# ---------------------------------------------------------------------------
class TestOutputHandlers:
    def test_synthesize_results_default(self):
        from handlers.output.output_handlers import handle_synthesize_results

        result = handle_synthesize_results({"query": "test"})
        assert "synthesis" in result
        assert "confidence" in result
        assert "key_findings" in result

    def test_synthesize_results_json_string(self):
        from handlers.output.output_handlers import handle_synthesize_results

        result = handle_synthesize_results(
            {
                "search_results": json.dumps([{"query": "q1"}]),
                "calculations": json.dumps([]),
                "query": "test",
            }
        )
        assert "synthesis" in result

    def test_format_answer_default(self):
        from handlers.output.output_handlers import handle_format_answer

        result = handle_format_answer(
            {
                "synthesis": "test synthesis",
                "key_findings": ["f1", "f2"],
                "confidence": 0.9,
                "query": "test",
            }
        )
        assert "answer" in result
        assert result["answer"]["confidence"] == 0.9

    def test_format_answer_json_string(self):
        from handlers.output.output_handlers import handle_format_answer

        result = handle_format_answer(
            {
                "synthesis": "test",
                "key_findings": json.dumps(["f1"]),
                "confidence": "0.85",
                "query": "test",
            }
        )
        assert "answer" in result


# ---------------------------------------------------------------------------
# TestDispatch
# ---------------------------------------------------------------------------
class TestDispatch:
    def test_planning_dispatch(self):
        from handlers.planning.planning_handlers import _DISPATCH

        assert len(_DISPATCH) == 2
        assert "tools.Planning.PlanToolUse" in _DISPATCH
        assert "tools.Planning.SelectNextTool" in _DISPATCH

    def test_search_dispatch(self):
        from handlers.search.search_handlers import _DISPATCH

        assert len(_DISPATCH) == 2
        assert "tools.Search.WebSearch" in _DISPATCH
        assert "tools.Search.DeepSearch" in _DISPATCH

    def test_compute_dispatch(self):
        from handlers.compute.compute_handlers import _DISPATCH

        assert len(_DISPATCH) == 2
        assert "tools.Compute.Calculate" in _DISPATCH
        assert "tools.Compute.ExecuteCode" in _DISPATCH

    def test_output_dispatch(self):
        from handlers.output.output_handlers import _DISPATCH

        assert len(_DISPATCH) == 2
        assert "tools.Output.SynthesizeResults" in _DISPATCH
        assert "tools.Output.FormatAnswer" in _DISPATCH

    def test_total_handler_count(self):
        from handlers.compute.compute_handlers import _DISPATCH as d3
        from handlers.output.output_handlers import _DISPATCH as d4
        from handlers.planning.planning_handlers import _DISPATCH as d1
        from handlers.search.search_handlers import _DISPATCH as d2

        assert len(d1) + len(d2) + len(d3) + len(d4) == 8


# ---------------------------------------------------------------------------
# TestCompilation
# ---------------------------------------------------------------------------
class TestCompilation:
    @pytest.fixture()
    def parsed_ast(self):
        from facetwork.parser import FFLParser

        afl_path = os.path.join(os.path.dirname(__file__), "..", "afl", "toolbox.ffl")
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
        assert len(event_facets) == 8

    def test_workflow_count(self, parsed_ast):
        workflows = []
        for ns in parsed_ast.namespaces:
            workflows.extend(ns.workflows)
        assert len(workflows) == 2

    def test_prompt_block_count(self, parsed_ast):
        from facetwork.ast import PromptBlock

        count = 0
        for ns in parsed_ast.namespaces:
            for ef in ns.event_facets:
                if isinstance(ef.body, PromptBlock):
                    count += 1
        assert count == 8

    def test_mixin_facet_count(self, parsed_ast):
        mixins_ns = [ns for ns in parsed_ast.namespaces if ns.name == "tools.mixins"][0]
        assert len(mixins_ns.facets) == 2

    def test_implicit_count(self, parsed_ast):
        implicits = []
        for ns in parsed_ast.namespaces:
            implicits.extend(ns.implicits)
        assert len(implicits) == 2

    def test_foreach_present(self, parsed_ast):
        workflows_ns = [ns for ns in parsed_ast.namespaces if ns.name == "tools.workflows"][0]
        research = [w for w in workflows_ns.workflows if w.sig.name == "ResearchAndCompute"][0]
        has_foreach = any(block.foreach is not None for block in research.body)
        assert has_foreach


# ---------------------------------------------------------------------------
# TestAgentIntegration
# ---------------------------------------------------------------------------
class TestAgentIntegration:
    def test_tool_registry_dispatches_all_handlers(self):
        from handlers.compute.compute_handlers import _DISPATCH as d3
        from handlers.output.output_handlers import _DISPATCH as d4
        from handlers.planning.planning_handlers import _DISPATCH as d1
        from handlers.search.search_handlers import _DISPATCH as d2

        from facetwork.runtime.agent import ToolRegistry

        registry = ToolRegistry()
        for dispatch in [d1, d2, d3, d4]:
            for facet_name, handler in dispatch.items():
                tool_name = facet_name.split(".")[-1]
                registry.register(tool_name, handler)

        tool_names = [
            "PlanToolUse",
            "SelectNextTool",
            "WebSearch",
            "DeepSearch",
            "Calculate",
            "ExecuteCode",
            "SynthesizeResults",
            "FormatAnswer",
        ]
        for name in tool_names:
            assert registry.has_handler(name), f"Missing handler: {name}"

    def test_claude_agent_runner_with_custom_handlers(self):
        from handlers.planning.planning_handlers import handle_plan_tool_use

        from facetwork.runtime import Evaluator, ExecutionStatus, MemoryStore, Telemetry
        from facetwork.runtime.agent import ClaudeAgentRunner, ToolRegistry

        store = MemoryStore()
        evaluator = Evaluator(persistence=store, telemetry=Telemetry(enabled=False))

        registry = ToolRegistry()
        registry.register("PlanToolUse", handle_plan_tool_use)

        runner = ClaudeAgentRunner(
            evaluator=evaluator,
            persistence=store,
            tool_registry=registry,
        )

        workflow_ast = {
            "type": "WorkflowDecl",
            "name": "TestTools",
            "params": [{"name": "query", "type": "String"}],
            "returns": [{"name": "result", "type": "String"}],
            "body": {
                "type": "AndThenBlock",
                "steps": [
                    {
                        "type": "StepStmt",
                        "id": "step-plan",
                        "name": "plan",
                        "call": {
                            "type": "CallExpr",
                            "target": "PlanToolUse",
                            "args": [
                                {
                                    "name": "query",
                                    "value": {"type": "InputRef", "path": ["query"]},
                                }
                            ],
                        },
                    },
                ],
                "yield": {
                    "type": "YieldStmt",
                    "id": "yield-TT",
                    "call": {
                        "type": "CallExpr",
                        "target": "TestTools",
                        "args": [
                            {
                                "name": "result",
                                "value": {"type": "StepRef", "path": ["plan", "plan"]},
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
                    "name": "PlanToolUse",
                    "params": [{"name": "query", "type": "String"}],
                    "returns": [{"name": "plan", "type": "Json"}],
                },
            ],
        }

        result = runner.run(
            workflow_ast,
            inputs={"query": "quantum computing"},
            program_ast=program_ast,
        )

        assert result.success is True
        assert result.status == ExecutionStatus.COMPLETED

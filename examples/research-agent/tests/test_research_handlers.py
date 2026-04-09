"""Tests for research-agent handlers and FFL compilation."""

from __future__ import annotations

import json
import os

import pytest


# ---------------------------------------------------------------------------
# TestResearchUtils — shared utility functions
# ---------------------------------------------------------------------------
class TestResearchUtils:
    def test_plan_structure(self):
        from handlers.shared.research_utils import plan_topic

        plan = plan_topic("quantum computing", depth=3, max_subtopics=5)
        assert plan["name"] == "quantum computing"
        assert plan["depth"] == 3
        assert plan["max_subtopics"] == 5
        assert isinstance(plan["keywords"], list)
        assert len(plan["keywords"]) == 3  # min(3*4, 16) / 4 = 3
        assert "Research plan for" in plan["summary"]

    def test_plan_determinism(self):
        from handlers.shared.research_utils import plan_topic

        p1 = plan_topic("test topic", depth=2)
        p2 = plan_topic("test topic", depth=2)
        assert p1 == p2

    def test_decompose_count(self):
        from handlers.shared.research_utils import decompose_topic, plan_topic

        topic = plan_topic("AI safety")
        subtopics = decompose_topic(topic, max_subtopics=4)
        assert len(subtopics) == 4
        for s in subtopics:
            assert "name" in s
            assert "parent_topic" in s
            assert s["parent_topic"] == "AI safety"

    def test_sources_count(self):
        from handlers.shared.research_utils import gather_sources

        subtopic = {"name": "alignment", "description": "AI alignment research"}
        sources = gather_sources(subtopic, max_sources=3)
        assert len(sources) == 3
        for s in sources:
            assert "title" in s
            assert "url" in s
            assert "relevance_score" in s
            assert 0.5 <= s["relevance_score"] <= 1.0

    def test_sources_capped_at_five(self):
        from handlers.shared.research_utils import gather_sources

        sources = gather_sources({"name": "test"}, max_sources=10)
        assert len(sources) == 5  # capped at 5

    def test_findings_count(self):
        from handlers.shared.research_utils import extract_findings

        subtopic = {"name": "testing"}
        sources = [{"title": f"Source {i}"} for i in range(3)]
        findings = extract_findings(subtopic, sources)
        assert len(findings) == 3
        for f in findings:
            assert "claim" in f
            assert "confidence" in f
            assert 0.4 <= f["confidence"] <= 0.95

    def test_synthesis_structure(self):
        from handlers.shared.research_utils import synthesize_findings

        topic = {"name": "climate"}
        findings = [
            [{"claim": "f1"}, {"claim": "f2"}],
            [{"claim": "f3"}],
        ]
        analysis = synthesize_findings(topic, findings)
        assert "themes" in analysis
        assert "contradictions" in analysis
        assert "gaps" in analysis
        assert "summary" in analysis
        assert 0.5 <= analysis["confidence_score"] <= 0.9

    def test_review_score_range(self):
        from handlers.shared.research_utils import review_draft

        draft = {"title": "Test Report", "sections": [], "word_count": 100, "citations": []}
        topic = {"name": "test"}
        analysis = {"confidence_score": 0.8, "themes": [], "gaps": []}

        review = review_draft(draft, topic, analysis)
        assert 55 <= review["score"] <= 94
        assert isinstance(review["approved"], bool)
        assert review["approved"] == (review["score"] >= 70)


# ---------------------------------------------------------------------------
# TestPlanningHandlers
# ---------------------------------------------------------------------------
class TestPlanningHandlers:
    def test_plan_default(self):
        from handlers.planning.planning_handlers import handle_plan_research

        result = handle_plan_research({"topic": "machine learning"})
        assert "plan" in result
        assert result["plan"]["name"] == "machine learning"
        assert result["plan"]["depth"] == 3

    def test_plan_custom_depth(self):
        from handlers.planning.planning_handlers import handle_plan_research

        result = handle_plan_research({"topic": "NLP", "depth": 5, "max_subtopics": 3})
        assert result["plan"]["depth"] == 5
        assert result["plan"]["max_subtopics"] == 3
        assert len(result["plan"]["keywords"]) == 4  # min(5*4, 16) / 4 = 4

    def test_decompose_json_string_topic(self):
        from handlers.planning.planning_handlers import handle_decompose_into_subtopics

        topic = {
            "name": "robotics",
            "depth": 2,
            "keywords": ["nav"],
            "summary": "test",
            "max_subtopics": 3,
        }
        result = handle_decompose_into_subtopics({"topic": json.dumps(topic), "max_subtopics": 3})
        assert len(result["subtopics"]) == 3
        assert result["subtopics"][0]["parent_topic"] == "robotics"


# ---------------------------------------------------------------------------
# TestGatheringHandlers
# ---------------------------------------------------------------------------
class TestGatheringHandlers:
    def test_sources_list(self):
        from handlers.gathering.gathering_handlers import handle_gather_sources

        result = handle_gather_sources(
            {
                "subtopic": {"name": "deep learning", "description": "DL methods"},
                "max_sources": 4,
            }
        )
        assert len(result["sources"]) == 4
        for s in result["sources"]:
            assert "title" in s
            assert "url" in s

    def test_json_string_subtopic(self):
        from handlers.gathering.gathering_handlers import handle_gather_sources

        subtopic = {
            "name": "RL",
            "parent_topic": "AI",
            "description": "reinforcement learning",
            "priority": 1,
        }
        result = handle_gather_sources({"subtopic": json.dumps(subtopic), "max_sources": 2})
        assert len(result["sources"]) == 2

    def test_findings_structure(self):
        from handlers.gathering.gathering_handlers import handle_extract_findings

        result = handle_extract_findings(
            {
                "subtopic": {"name": "testing"},
                "sources": [{"title": "S1"}, {"title": "S2"}],
            }
        )
        assert len(result["findings"]) == 2
        assert all("confidence" in f for f in result["findings"])

    def test_empty_sources(self):
        from handlers.gathering.gathering_handlers import handle_extract_findings

        result = handle_extract_findings(
            {
                "subtopic": {"name": "empty test"},
                "sources": [],
            }
        )
        assert result["findings"] == []


# ---------------------------------------------------------------------------
# TestAnalysisHandlers
# ---------------------------------------------------------------------------
class TestAnalysisHandlers:
    def test_synthesis_output(self):
        from handlers.analysis.analysis_handlers import handle_synthesize_findings

        result = handle_synthesize_findings(
            {
                "topic": {"name": "climate"},
                "all_findings": [[{"claim": "f1"}, {"claim": "f2"}], [{"claim": "f3"}]],
            }
        )
        analysis = result["analysis"]
        assert "themes" in analysis
        assert "summary" in analysis
        assert "confidence_score" in analysis

    def test_nested_findings(self):
        from handlers.analysis.analysis_handlers import handle_synthesize_findings

        # JSON string all_findings
        findings = json.dumps([[{"claim": "a"}], [{"claim": "b"}, {"claim": "c"}]])
        result = handle_synthesize_findings(
            {
                "topic": {"name": "test"},
                "all_findings": findings,
            }
        )
        assert len(result["analysis"]["themes"]) == 3  # 3 findings = min(3, 3) themes

    def test_gaps_returns_lists(self):
        from handlers.analysis.analysis_handlers import handle_identify_gaps

        result = handle_identify_gaps(
            {
                "analysis": {"gaps": ["Gap A", "Gap B"], "themes": [], "contradictions": []},
                "topic": {"name": "test"},
            }
        )
        assert isinstance(result["gaps"], list)
        assert isinstance(result["recommendations"], list)
        assert len(result["gaps"]) == 2
        assert len(result["recommendations"]) == 2

    def test_json_string_analysis(self):
        from handlers.analysis.analysis_handlers import handle_identify_gaps

        analysis = {
            "gaps": ["G1"],
            "themes": ["T1"],
            "contradictions": [],
            "summary": "ok",
            "confidence_score": 0.8,
        }
        result = handle_identify_gaps(
            {
                "analysis": json.dumps(analysis),
                "topic": json.dumps({"name": "test"}),
            }
        )
        assert len(result["gaps"]) == 1


# ---------------------------------------------------------------------------
# TestWritingHandlers
# ---------------------------------------------------------------------------
class TestWritingHandlers:
    def test_draft_sections(self):
        from handlers.writing.writing_handlers import handle_draft_report

        result = handle_draft_report(
            {
                "topic": {"name": "AI"},
                "analysis": {
                    "themes": ["T1", "T2"],
                    "summary": "Analysis summary",
                    "confidence_score": 0.8,
                    "gaps": [],
                },
                "gaps": [{"description": "Gap 1"}],
            }
        )
        draft = result["draft"]
        assert len(draft["sections"]) == 5
        assert draft["sections"][0]["title"] == "Introduction"
        assert draft["sections"][-1]["title"] == "Conclusion"

    def test_word_count(self):
        from handlers.writing.writing_handlers import handle_draft_report

        result = handle_draft_report(
            {
                "topic": {"name": "testing"},
                "analysis": {"themes": [], "summary": "short", "gaps": []},
                "gaps": [],
            }
        )
        assert result["draft"]["word_count"] > 0

    def test_review_score(self):
        from handlers.writing.writing_handlers import handle_review_draft

        result = handle_review_draft(
            {
                "draft": {"title": "Report", "sections": [], "word_count": 500, "citations": []},
                "topic": {"name": "testing"},
                "analysis": {"confidence_score": 0.85, "themes": []},
            }
        )
        review = result["review"]
        assert 55 <= review["score"] <= 94
        assert isinstance(review["approved"], bool)

    def test_approved_threshold(self):
        from handlers.writing.writing_handlers import handle_review_draft

        result = handle_review_draft(
            {
                "draft": {"title": "Report", "sections": [], "word_count": 500, "citations": []},
                "topic": {"name": "threshold_test"},
                "analysis": {"confidence_score": 0.9, "themes": []},
            }
        )
        review = result["review"]
        assert review["approved"] == (review["score"] >= 70)


# ---------------------------------------------------------------------------
# TestDispatch
# ---------------------------------------------------------------------------
class TestDispatch:
    def test_planning_dispatch(self):
        from handlers.planning.planning_handlers import _DISPATCH

        assert len(_DISPATCH) == 2
        assert "research.Planning.PlanResearch" in _DISPATCH
        assert "research.Planning.DecomposeIntoSubtopics" in _DISPATCH

    def test_gathering_dispatch(self):
        from handlers.gathering.gathering_handlers import _DISPATCH

        assert len(_DISPATCH) == 2
        assert "research.Gathering.GatherSources" in _DISPATCH
        assert "research.Gathering.ExtractFindings" in _DISPATCH

    def test_analysis_dispatch(self):
        from handlers.analysis.analysis_handlers import _DISPATCH

        assert len(_DISPATCH) == 2
        assert "research.Analysis.SynthesizeFindings" in _DISPATCH
        assert "research.Analysis.IdentifyGaps" in _DISPATCH

    def test_writing_dispatch(self):
        from handlers.writing.writing_handlers import _DISPATCH

        assert len(_DISPATCH) == 2
        assert "research.Writing.DraftReport" in _DISPATCH
        assert "research.Writing.ReviewDraft" in _DISPATCH

    def test_total_handler_count(self):
        from handlers.analysis.analysis_handlers import _DISPATCH as d3
        from handlers.gathering.gathering_handlers import _DISPATCH as d2
        from handlers.planning.planning_handlers import _DISPATCH as d1
        from handlers.writing.writing_handlers import _DISPATCH as d4

        assert len(d1) + len(d2) + len(d3) + len(d4) == 8


# ---------------------------------------------------------------------------
# TestCompilation
# ---------------------------------------------------------------------------
class TestCompilation:
    @pytest.fixture()
    def parsed_ast(self):
        from facetwork.parser import FFLParser

        afl_path = os.path.join(os.path.dirname(__file__), "..", "afl", "research.ffl")
        with open(afl_path) as f:
            source = f.read()
        return FFLParser().parse(source)

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
        from facetwork.ast import PromptBlock

        count = 0
        for ns in parsed_ast.namespaces:
            for ef in ns.event_facets:
                if isinstance(ef.body, PromptBlock):
                    count += 1
        assert count == 8

    def test_mixin_facet_count(self, parsed_ast):
        mixins_ns = [ns for ns in parsed_ast.namespaces if ns.name == "research.mixins"][0]
        assert len(mixins_ns.facets) == 2

    def test_implicit_count(self, parsed_ast):
        implicits = []
        for ns in parsed_ast.namespaces:
            implicits.extend(ns.implicits)
        assert len(implicits) == 2

    def test_foreach_present(self, parsed_ast):
        workflows_ns = [ns for ns in parsed_ast.namespaces if ns.name == "research.workflows"][0]
        deep_dive = [w for w in workflows_ns.workflows if w.sig.name == "DeepDive"][0]
        # DeepDive has an andThen foreach block
        has_foreach = any(block.foreach is not None for block in deep_dive.body)
        assert has_foreach


# ---------------------------------------------------------------------------
# TestAgentIntegration
# ---------------------------------------------------------------------------
class TestAgentIntegration:
    def test_tool_registry_dispatches_all_handlers(self):
        from handlers.analysis.analysis_handlers import _DISPATCH as d3
        from handlers.gathering.gathering_handlers import _DISPATCH as d2
        from handlers.planning.planning_handlers import _DISPATCH as d1
        from handlers.writing.writing_handlers import _DISPATCH as d4

        from facetwork.runtime.agent import ToolRegistry

        registry = ToolRegistry()
        for dispatch in [d1, d2, d3, d4]:
            for facet_name, handler in dispatch.items():
                # Strip namespace prefix for tool name
                tool_name = facet_name.split(".")[-1]
                registry.register(tool_name, handler)

        # Verify all 8 handlers registered
        tool_names = [
            "PlanResearch",
            "DecomposeIntoSubtopics",
            "GatherSources",
            "ExtractFindings",
            "SynthesizeFindings",
            "IdentifyGaps",
            "DraftReport",
            "ReviewDraft",
        ]
        for name in tool_names:
            assert registry.has_handler(name), f"Missing handler: {name}"

    def test_claude_agent_runner_with_custom_handlers(self):
        from handlers.planning.planning_handlers import handle_plan_research

        from facetwork.runtime import Evaluator, ExecutionStatus, MemoryStore, Telemetry
        from facetwork.runtime.agent import ClaudeAgentRunner, ToolRegistry

        store = MemoryStore()
        evaluator = Evaluator(persistence=store, telemetry=Telemetry(enabled=False))

        registry = ToolRegistry()
        # Register the real PlanResearch handler
        registry.register("PlanResearch", handle_plan_research)

        runner = ClaudeAgentRunner(
            evaluator=evaluator,
            persistence=store,
            tool_registry=registry,
        )

        # Minimal workflow AST with a single PlanResearch step
        workflow_ast = {
            "type": "WorkflowDecl",
            "name": "TestResearch",
            "params": [{"name": "topic", "type": "String"}],
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
                            "target": "PlanResearch",
                            "args": [
                                {
                                    "name": "topic",
                                    "value": {"type": "InputRef", "path": ["topic"]},
                                }
                            ],
                        },
                    },
                ],
                "yield": {
                    "type": "YieldStmt",
                    "id": "yield-TR",
                    "call": {
                        "type": "CallExpr",
                        "target": "TestResearch",
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
                    "name": "PlanResearch",
                    "params": [{"name": "topic", "type": "String"}],
                    "returns": [{"name": "plan", "type": "Json"}],
                },
            ],
        }

        result = runner.run(
            workflow_ast,
            inputs={"topic": "AI safety"},
            program_ast=program_ast,
        )

        assert result.success is True
        assert result.status == ExecutionStatus.COMPLETED

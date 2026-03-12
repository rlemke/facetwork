"""Tests for site-selection-debate handlers and AFL compilation."""

from __future__ import annotations

import json
import os

import pytest


# ---------------------------------------------------------------------------
# TestDebateUtils
# ---------------------------------------------------------------------------
class TestDebateUtils:
    def test_score_candidate_structure(self):
        from handlers.shared.debate_utils import score_candidate

        result = score_candidate("site_A")
        assert "candidate_id" in result
        assert "demographics_score" in result
        assert "competition_score" in result
        assert "accessibility_score" in result
        assert "overall_score" in result
        assert result["candidate_id"] == "site_A"

    def test_score_candidate_determinism(self):
        from handlers.shared.debate_utils import score_candidate

        a = score_candidate("site_B", penalty=-0.15)
        b = score_candidate("site_B", penalty=-0.15)
        assert a == b

    def test_score_candidate_penalty(self):
        from handlers.shared.debate_utils import score_candidate

        no_penalty = score_candidate("site_C", penalty=0.0)
        with_penalty = score_candidate("site_C", penalty=-5.0)
        assert with_penalty["overall_score"] < no_penalty["overall_score"]

    def test_accessibility_structure(self):
        from handlers.shared.debate_utils import compute_accessibility

        result = compute_accessibility("site_D")
        assert "walk_score" in result
        assert "transit_coverage" in result
        assert "highway_distance_km" in result
        assert 20 <= result["walk_score"] <= 100

    def test_confidence_increases_with_round(self):
        from handlers.shared.debate_utils import present_analysis

        r1 = present_analysis("financial_analyst", "site_A", round_num=1)
        r3 = present_analysis("financial_analyst", "site_A", round_num=3)
        assert r3["confidence"] >= r1["confidence"]

    def test_challenge_weaknesses(self):
        from handlers.shared.debate_utils import challenge_position

        rebuttal, weaknesses = challenge_position(
            "financial_analyst", "community_analyst", "test position", 1
        )
        assert isinstance(rebuttal, str)
        assert len(weaknesses) >= 1

    def test_convergence_detection(self):
        from handlers.shared.debate_utils import score_arguments

        # First round with no previous rankings
        r1 = score_arguments(
            [{"agent_role": "fa"}, {"agent_role": "ca"}],
            ["ch1", "ch2"],
            prev_rankings=None,
            round_num=1,
        )
        assert "consensus_level" in r1
        assert "converged" in r1
        assert isinstance(r1["converged"], bool)

    def test_report_structure(self):
        from handlers.shared.debate_utils import generate_report

        result = generate_report("site_A", [{"candidate_id": "site_A"}], ["s1", "s2"], 0.85)
        assert result["top_candidate"] == "site_A"
        assert result["num_rounds"] == 2
        assert result["num_candidates_evaluated"] == 1
        assert "recommendation" in result


# ---------------------------------------------------------------------------
# TestSpatialHandlers
# ---------------------------------------------------------------------------
class TestSpatialHandlers:
    def test_score_candidate_default(self):
        from handlers.spatial.spatial_handlers import handle_score_candidate

        result = handle_score_candidate({"candidate_id": "site_A"})
        assert "score" in result
        assert result["score"]["candidate_id"] == "site_A"

    def test_rank_candidates_null_weights(self):
        from handlers.spatial.spatial_handlers import handle_rank_candidates

        scores = [
            {"candidate_id": "A", "overall_score": 80},
            {"candidate_id": "B", "overall_score": 90},
        ]
        result = handle_rank_candidates({"scores": scores, "weights": "null"})
        assert "ranked" in result
        assert "top_candidate" in result
        assert result["top_candidate"] == "B"

    def test_compute_accessibility_json_string(self):
        from handlers.spatial.spatial_handlers import handle_compute_accessibility

        result = handle_compute_accessibility(
            {
                "candidate_id": "site_X",
                "location": json.dumps({"lat": 40.7, "lng": -74.0}),
            }
        )
        assert "metrics" in result
        assert result["metrics"]["candidate_id"] == "site_X"


# ---------------------------------------------------------------------------
# TestResearchHandlers
# ---------------------------------------------------------------------------
class TestResearchHandlers:
    def test_search_market_trends(self):
        from handlers.research.research_handlers import handle_search_market_trends

        result = handle_search_market_trends({"candidate_id": "site_A"})
        assert "research" in result
        assert "growth_rate" in result["research"]

    def test_gather_regulations(self):
        from handlers.research.research_handlers import handle_gather_regulations

        result = handle_gather_regulations({"candidate_id": "site_A"})
        assert "regulations" in result
        assert "permit_difficulty" in result["regulations"]

    def test_analyze_competitors_default(self):
        from handlers.research.research_handlers import handle_analyze_competitors

        result = handle_analyze_competitors({"candidate_id": "site_A"})
        assert "competitors" in result
        assert "threat_level" in result

    def test_analyze_competitors_json_string(self):
        from handlers.research.research_handlers import handle_analyze_competitors

        result = handle_analyze_competitors(
            {
                "candidate_id": "site_B",
                "radius_km": "10.0",
            }
        )
        assert "competitors" in result
        assert "threat_level" in result


# ---------------------------------------------------------------------------
# TestDebateHandlers
# ---------------------------------------------------------------------------
class TestDebateHandlers:
    def test_present_analysis_default(self):
        from handlers.debate.debate_handlers import handle_present_analysis

        result = handle_present_analysis(
            {
                "agent_role": "financial_analyst",
                "candidate_id": "site_A",
            }
        )
        assert "position" in result
        assert result["position"]["agent_role"] == "financial_analyst"

    def test_present_analysis_custom_round(self):
        from handlers.debate.debate_handlers import handle_present_analysis

        result = handle_present_analysis(
            {
                "agent_role": "community_analyst",
                "candidate_id": "site_B",
                "round_num": 3,
            }
        )
        assert result["position"]["confidence"] > 0

    def test_challenge_position_default(self):
        from handlers.debate.debate_handlers import handle_challenge_position

        result = handle_challenge_position(
            {
                "agent_role": "financial_analyst",
                "target_role": "community_analyst",
                "target_position": "site is good for community",
            }
        )
        assert "rebuttal" in result
        assert "weaknesses" in result

    def test_score_arguments_null_prev(self):
        from handlers.debate.debate_handlers import handle_score_arguments

        result = handle_score_arguments(
            {
                "positions": [{"agent_role": "fa"}],
                "challenges": ["ch1"],
                "prev_rankings": "null",
            }
        )
        assert "rankings" in result
        assert "consensus_level" in result["rankings"]


# ---------------------------------------------------------------------------
# TestSynthesisHandlers
# ---------------------------------------------------------------------------
class TestSynthesisHandlers:
    def test_summarize_round_default(self):
        from handlers.synthesis.synthesis_handlers import handle_summarize_round

        result = handle_summarize_round(
            {
                "positions": [{"agent_role": "fa"}, {"agent_role": "ca"}],
                "challenges": ["ch1"],
            }
        )
        assert "synthesis" in result
        assert "key_arguments" in result

    def test_summarize_round_json_string(self):
        from handlers.synthesis.synthesis_handlers import handle_summarize_round

        result = handle_summarize_round(
            {
                "positions": json.dumps([{"agent_role": "fa"}]),
                "challenges": json.dumps(["ch1"]),
            }
        )
        assert "synthesis" in result

    def test_produce_ranking(self):
        from handlers.synthesis.synthesis_handlers import handle_produce_ranking

        result = handle_produce_ranking(
            {
                "round_syntheses": ["s1", "s2"],
                "candidate_scores": [
                    {"candidate_id": "A", "overall_score": 80},
                    {"candidate_id": "B", "overall_score": 90},
                ],
            }
        )
        assert "ranked" in result
        assert "top_candidate" in result
        assert result["top_candidate"] == "B"

    def test_generate_report(self):
        from handlers.synthesis.synthesis_handlers import handle_generate_report

        result = handle_generate_report(
            {
                "top_candidate": "site_A",
                "ranked_candidates": [{"candidate_id": "site_A"}],
                "round_syntheses": ["s1", "s2", "s3"],
                "confidence": 0.9,
            }
        )
        assert "report" in result
        assert result["report"]["top_candidate"] == "site_A"
        assert result["report"]["num_rounds"] == 3


# ---------------------------------------------------------------------------
# TestDispatch
# ---------------------------------------------------------------------------
class TestDispatch:
    def test_spatial_dispatch(self):
        from handlers.spatial.spatial_handlers import _DISPATCH

        assert len(_DISPATCH) == 3
        assert "siteselection.Spatial.ScoreCandidate" in _DISPATCH
        assert "siteselection.Spatial.RankCandidates" in _DISPATCH
        assert "siteselection.Spatial.ComputeAccessibility" in _DISPATCH

    def test_research_dispatch(self):
        from handlers.research.research_handlers import _DISPATCH

        assert len(_DISPATCH) == 3
        assert "siteselection.Research.SearchMarketTrends" in _DISPATCH
        assert "siteselection.Research.GatherRegulations" in _DISPATCH
        assert "siteselection.Research.AnalyzeCompetitors" in _DISPATCH

    def test_debate_dispatch(self):
        from handlers.debate.debate_handlers import _DISPATCH

        assert len(_DISPATCH) == 3
        assert "siteselection.Debate.PresentAnalysis" in _DISPATCH
        assert "siteselection.Debate.ChallengePosition" in _DISPATCH
        assert "siteselection.Debate.ScoreArguments" in _DISPATCH

    def test_synthesis_dispatch(self):
        from handlers.synthesis.synthesis_handlers import _DISPATCH

        assert len(_DISPATCH) == 3
        assert "siteselection.Synthesis.SummarizeRound" in _DISPATCH
        assert "siteselection.Synthesis.ProduceRanking" in _DISPATCH
        assert "siteselection.Synthesis.GenerateReport" in _DISPATCH

    def test_total_handler_count(self):
        from handlers.debate.debate_handlers import _DISPATCH as d3
        from handlers.research.research_handlers import _DISPATCH as d2
        from handlers.spatial.spatial_handlers import _DISPATCH as d1
        from handlers.synthesis.synthesis_handlers import _DISPATCH as d4

        assert len(d1) + len(d2) + len(d3) + len(d4) == 12


# ---------------------------------------------------------------------------
# TestCompilation
# ---------------------------------------------------------------------------
class TestCompilation:
    @pytest.fixture()
    def parsed_ast(self):
        from afl.parser import AFLParser

        afl_path = os.path.join(os.path.dirname(__file__), "..", "afl", "sitesel_debate.afl")
        with open(afl_path) as f:
            source = f.read()
        return AFLParser().parse(source)

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
        assert len(event_facets) == 12

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
        assert count == 12

    def test_mixin_facet_count(self, parsed_ast):
        mixins_ns = [ns for ns in parsed_ast.namespaces if ns.name == "siteselection.mixins"][0]
        assert len(mixins_ns.facets) == 2

    def test_implicit_count(self, parsed_ast):
        implicits = []
        for ns in parsed_ast.namespaces:
            implicits.extend(ns.implicits)
        assert len(implicits) == 2

    def test_null_literals_present(self, parsed_ast):
        """Verify null literal appears in at least 2 event facet defaults."""
        from afl.ast import Literal

        null_count = 0
        for ns in parsed_ast.namespaces:
            for ef in ns.event_facets:
                for p in ef.sig.params:
                    if isinstance(p.default, Literal) and p.default.kind == "null":
                        null_count += 1
        assert null_count >= 2, f"Expected >=2 null literal defaults, got {null_count}"

    def test_composed_facet_present(self, parsed_ast):
        """Verify EvaluationRound composed facet exists with internal steps."""
        comp_ns = [ns for ns in parsed_ast.namespaces if ns.name == "siteselection.composition"][0]
        assert len(comp_ns.facets) == 1
        eval_round = comp_ns.facets[0]
        assert eval_round.sig.name == "EvaluationRound"


# ---------------------------------------------------------------------------
# TestAgentIntegration
# ---------------------------------------------------------------------------
class TestAgentIntegration:
    def test_tool_registry_dispatches_all_handlers(self):
        from handlers.debate.debate_handlers import _DISPATCH as d3
        from handlers.research.research_handlers import _DISPATCH as d2
        from handlers.spatial.spatial_handlers import _DISPATCH as d1
        from handlers.synthesis.synthesis_handlers import _DISPATCH as d4

        from afl.runtime.agent import ToolRegistry

        registry = ToolRegistry()
        for dispatch in [d1, d2, d3, d4]:
            for facet_name, handler in dispatch.items():
                tool_name = facet_name.split(".")[-1]
                registry.register(tool_name, handler)

        tool_names = [
            "ScoreCandidate",
            "RankCandidates",
            "ComputeAccessibility",
            "SearchMarketTrends",
            "GatherRegulations",
            "AnalyzeCompetitors",
            "PresentAnalysis",
            "ChallengePosition",
            "ScoreArguments",
            "SummarizeRound",
            "ProduceRanking",
            "GenerateReport",
        ]
        for name in tool_names:
            assert registry.has_handler(name), f"Missing handler: {name}"

    def test_claude_agent_runner_with_score_candidate(self):
        from handlers.spatial.spatial_handlers import handle_score_candidate

        from afl.runtime import Evaluator, ExecutionStatus, MemoryStore, Telemetry
        from afl.runtime.agent import ClaudeAgentRunner, ToolRegistry

        store = MemoryStore()
        evaluator = Evaluator(persistence=store, telemetry=Telemetry(enabled=False))

        registry = ToolRegistry()
        registry.register("ScoreCandidate", handle_score_candidate)

        runner = ClaudeAgentRunner(
            evaluator=evaluator,
            persistence=store,
            tool_registry=registry,
        )

        workflow_ast = {
            "type": "WorkflowDecl",
            "name": "TestSSD",
            "params": [{"name": "candidate_id", "type": "String"}],
            "returns": [{"name": "result", "type": "String"}],
            "body": {
                "type": "AndThenBlock",
                "steps": [
                    {
                        "type": "StepStmt",
                        "id": "step-score",
                        "name": "sc",
                        "call": {
                            "type": "CallExpr",
                            "target": "ScoreCandidate",
                            "args": [
                                {
                                    "name": "candidate_id",
                                    "value": {"type": "InputRef", "path": ["candidate_id"]},
                                }
                            ],
                        },
                    },
                ],
                "yield": {
                    "type": "YieldStmt",
                    "id": "yield-TSSD",
                    "call": {
                        "type": "CallExpr",
                        "target": "TestSSD",
                        "args": [
                            {
                                "name": "result",
                                "value": {"type": "StepRef", "path": ["sc", "score"]},
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
                    "name": "ScoreCandidate",
                    "params": [{"name": "candidate_id", "type": "String"}],
                    "returns": [{"name": "score", "type": "Json"}],
                },
            ],
        }

        result = runner.run(
            workflow_ast,
            inputs={"candidate_id": "test_site"},
            program_ast=program_ast,
        )

        assert result.success is True
        assert result.status == ExecutionStatus.COMPLETED

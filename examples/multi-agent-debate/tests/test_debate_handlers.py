"""Tests for multi-agent-debate handlers and AFL compilation."""

from __future__ import annotations

import json
import os

import pytest


# ---------------------------------------------------------------------------
# TestDebateUtils — shared utility functions
# ---------------------------------------------------------------------------
class TestDebateUtils:
    def test_frame_structure(self):
        from handlers.shared.debate_utils import frame_debate
        result = frame_debate("artificial intelligence", 3)
        assert "topic_analysis" in result
        assert "positions" in result
        assert "stakes" in result
        assert len(result["positions"]) == 3
        assert result["positions"][0]["stance"] == "for"
        assert result["positions"][1]["stance"] == "against"
        assert result["positions"][2]["stance"] == "neutral"

    def test_frame_determinism(self):
        from handlers.shared.debate_utils import frame_debate
        r1 = frame_debate("climate change", 3)
        r2 = frame_debate("climate change", 3)
        assert r1 == r2

    def test_role_count(self):
        from handlers.shared.debate_utils import assign_roles
        positions = [
            {"stance": "for", "rationale": "r1", "priority": 3},
            {"stance": "against", "rationale": "r2", "priority": 2},
            {"stance": "neutral", "rationale": "r3", "priority": 1},
        ]
        roles = assign_roles("analysis text", positions)
        assert len(roles) == 3
        assert roles[0]["persona"] == "proposer"
        assert roles[1]["persona"] == "critic"
        assert roles[2]["persona"] == "synthesizer"

    def test_argument_structure(self):
        from handlers.shared.debate_utils import generate_argument
        role = {"persona": "proposer", "position": "for"}
        arg = generate_argument(role, "AI safety", "context")
        assert arg["agent_role"] == "proposer"
        assert arg["position"] == "for"
        assert len(arg["claims"]) == 3
        assert len(arg["evidence"]) == 3
        assert 0.4 <= arg["confidence"] <= 0.95

    def test_rebuttal_references(self):
        from handlers.shared.debate_utils import generate_rebuttal
        role = {"persona": "critic"}
        arguments = [
            {"agent_role": "proposer", "claims": ["c1"], "evidence": ["e1"]},
            {"agent_role": "synthesizer", "claims": ["c2"], "evidence": ["e2"]},
        ]
        reb = generate_rebuttal(role, arguments)
        assert reb["agent_role"] == "critic"
        assert reb["target_role"] == "proposer"
        assert len(reb["counter_claims"]) == 2
        assert 0.3 <= reb["strength"] <= 0.9

    def test_score_range(self):
        from handlers.shared.debate_utils import score_arguments
        arguments = [
            {"agent_role": "proposer"},
            {"agent_role": "critic"},
        ]
        rebuttals = [{"agent_role": "proposer", "counter_claims": []}]
        scores = score_arguments(arguments, rebuttals)
        assert len(scores) == 2
        for s in scores:
            assert 40 <= s["clarity"] <= 95
            assert 35 <= s["evidence_quality"] <= 90
            assert 30 <= s["persuasiveness"] <= 95
            assert "overall" in s

    def test_verdict_structure(self):
        from handlers.shared.debate_utils import judge_debate
        scores = [
            {"agent_role": "proposer", "overall": 80},
            {"agent_role": "critic", "overall": 70},
            {"agent_role": "synthesizer", "overall": 75},
        ]
        verdict = judge_debate("AI", "synthesis text", scores)
        assert verdict["winner"] == "proposer"
        assert "margin" in verdict
        assert "rationale" in verdict
        assert isinstance(verdict["dissenting_points"], list)

    def test_synthesis_themes(self):
        from handlers.shared.debate_utils import synthesize_positions
        arguments = [{"agent_role": "proposer"}, {"agent_role": "critic"}, {"agent_role": "synthesizer"}]
        rebuttals = [{"agent_role": "proposer"}]
        scores = [{"overall": 80}]
        synthesis, themes = synthesize_positions(arguments, rebuttals, scores)
        assert isinstance(synthesis, str)
        assert len(themes) >= 3
        assert "evidence-based" in themes[0]

    def test_consensus_level(self):
        from handlers.shared.debate_utils import build_consensus
        verdict = {"winner": "proposer", "margin": 12.5}
        consensus = build_consensus(verdict, "synthesis text", ["theme1", "theme2"])
        assert 0.2 <= consensus["agreement_level"] <= 0.9
        assert isinstance(consensus["common_ground"], list)
        assert isinstance(consensus["unresolved"], list)
        assert "summary" in consensus


# ---------------------------------------------------------------------------
# TestFramingHandlers
# ---------------------------------------------------------------------------
class TestFramingHandlers:
    def test_frame_default(self):
        from handlers.framing.framing_handlers import handle_frame_debate
        result = handle_frame_debate({"topic": "renewable energy"})
        assert "topic_analysis" in result
        assert len(result["positions"]) == 3
        assert "stakes" in result

    def test_custom_num_agents(self):
        from handlers.framing.framing_handlers import handle_frame_debate
        result = handle_frame_debate({"topic": "space exploration", "num_agents": 4})
        assert len(result["positions"]) == 4

    def test_assign_roles_json_string(self):
        from handlers.framing.framing_handlers import handle_assign_roles
        positions = [
            {"stance": "for", "rationale": "r1", "priority": 2},
            {"stance": "against", "rationale": "r2", "priority": 1},
        ]
        result = handle_assign_roles({
            "topic_analysis": "analysis text",
            "positions": json.dumps(positions),
        })
        assert len(result["assignments"]) == 2
        assert result["assignments"][0]["persona"] == "proposer"


# ---------------------------------------------------------------------------
# TestArgumentationHandlers
# ---------------------------------------------------------------------------
class TestArgumentationHandlers:
    def test_argument_structure(self):
        from handlers.argumentation.argumentation_handlers import handle_generate_argument
        result = handle_generate_argument({
            "role": {"persona": "proposer", "position": "for"},
            "topic": "AI regulation",
        })
        arg = result["argument"]
        assert arg["agent_role"] == "proposer"
        assert len(arg["claims"]) == 3
        assert len(arg["evidence"]) == 3

    def test_rebuttal_with_arguments(self):
        from handlers.argumentation.argumentation_handlers import handle_generate_rebuttal
        arguments = [
            {"agent_role": "proposer", "claims": ["c1"]},
            {"agent_role": "synthesizer", "claims": ["c2"]},
        ]
        result = handle_generate_rebuttal({
            "role": {"persona": "critic"},
            "arguments": arguments,
        })
        reb = result["rebuttal"]
        assert reb["agent_role"] == "critic"
        assert reb["target_role"] == "proposer"

    def test_json_string_role(self):
        from handlers.argumentation.argumentation_handlers import handle_generate_argument
        role = {"persona": "critic", "position": "against", "expertise": "analysis"}
        result = handle_generate_argument({
            "role": json.dumps(role),
            "topic": "testing",
        })
        assert result["argument"]["agent_role"] == "critic"

    def test_empty_arguments(self):
        from handlers.argumentation.argumentation_handlers import handle_generate_rebuttal
        result = handle_generate_rebuttal({
            "role": {"persona": "synthesizer"},
            "arguments": [],
        })
        reb = result["rebuttal"]
        assert reb["target_role"] == "unknown"
        assert len(reb["counter_claims"]) >= 1


# ---------------------------------------------------------------------------
# TestEvaluationHandlers
# ---------------------------------------------------------------------------
class TestEvaluationHandlers:
    def test_score_range(self):
        from handlers.evaluation.evaluation_handlers import handle_score_arguments
        result = handle_score_arguments({
            "arguments": [{"agent_role": "proposer"}],
            "rebuttals": [{"agent_role": "critic"}],
        })
        scores = result["scores"]
        assert len(scores) == 1
        assert 40 <= scores[0]["clarity"] <= 95
        assert 35 <= scores[0]["evidence_quality"] <= 90

    def test_multiple_arguments(self):
        from handlers.evaluation.evaluation_handlers import handle_score_arguments
        result = handle_score_arguments({
            "arguments": [{"agent_role": "proposer"}, {"agent_role": "critic"}, {"agent_role": "synthesizer"}],
            "rebuttals": [],
        })
        assert len(result["scores"]) == 3

    def test_judge_verdict(self):
        from handlers.evaluation.evaluation_handlers import handle_judge_debate
        result = handle_judge_debate({
            "topic": "climate policy",
            "synthesis": "balanced analysis",
            "scores": [
                {"agent_role": "proposer", "overall": 85},
                {"agent_role": "critic", "overall": 72},
            ],
        })
        verdict = result["verdict"]
        assert verdict["winner"] == "proposer"
        assert "rationale" in verdict

    def test_json_string_scores(self):
        from handlers.evaluation.evaluation_handlers import handle_judge_debate
        scores = [{"agent_role": "critic", "overall": 90}]
        result = handle_judge_debate({
            "topic": "test",
            "synthesis": "test synthesis",
            "scores": json.dumps(scores),
        })
        assert result["verdict"]["winner"] == "critic"


# ---------------------------------------------------------------------------
# TestSynthesisHandlers
# ---------------------------------------------------------------------------
class TestSynthesisHandlers:
    def test_synthesis_output(self):
        from handlers.synthesis.synthesis_handlers import handle_synthesize_positions
        result = handle_synthesize_positions({
            "arguments": [{"agent_role": "proposer"}, {"agent_role": "critic"}],
            "rebuttals": [{"agent_role": "proposer"}],
            "scores": [{"overall": 80}],
        })
        assert "synthesis" in result
        assert "themes" in result
        assert isinstance(result["themes"], list)
        assert len(result["themes"]) >= 3

    def test_consensus_level(self):
        from handlers.synthesis.synthesis_handlers import handle_build_consensus
        result = handle_build_consensus({
            "verdict": {"winner": "proposer", "margin": 10.0},
            "synthesis": "test synthesis",
            "themes": ["theme1", "theme2"],
        })
        consensus = result["consensus"]
        assert 0.2 <= consensus["agreement_level"] <= 0.9
        assert isinstance(consensus["common_ground"], list)

    def test_json_string_inputs(self):
        from handlers.synthesis.synthesis_handlers import handle_synthesize_positions
        result = handle_synthesize_positions({
            "arguments": json.dumps([{"agent_role": "proposer"}]),
            "rebuttals": json.dumps([]),
            "scores": json.dumps([]),
        })
        assert "synthesis" in result
        assert len(result["themes"]) >= 3

    def test_agreement_detection(self):
        from handlers.synthesis.synthesis_handlers import handle_build_consensus
        result = handle_build_consensus({
            "verdict": json.dumps({"winner": "critic", "margin": 5.0}),
            "synthesis": "high agreement synthesis",
            "themes": json.dumps(["t1", "t2", "t3"]),
        })
        consensus = result["consensus"]
        assert "summary" in consensus
        assert len(consensus["unresolved"]) >= 1


# ---------------------------------------------------------------------------
# TestDispatch
# ---------------------------------------------------------------------------
class TestDispatch:
    def test_framing_dispatch(self):
        from handlers.framing.framing_handlers import _DISPATCH
        assert len(_DISPATCH) == 2
        assert "debate.Framing.FrameDebate" in _DISPATCH
        assert "debate.Framing.AssignRoles" in _DISPATCH

    def test_argumentation_dispatch(self):
        from handlers.argumentation.argumentation_handlers import _DISPATCH
        assert len(_DISPATCH) == 2
        assert "debate.Argumentation.GenerateArgument" in _DISPATCH
        assert "debate.Argumentation.GenerateRebuttal" in _DISPATCH

    def test_evaluation_dispatch(self):
        from handlers.evaluation.evaluation_handlers import _DISPATCH
        assert len(_DISPATCH) == 2
        assert "debate.Evaluation.ScoreArguments" in _DISPATCH
        assert "debate.Evaluation.JudgeDebate" in _DISPATCH

    def test_synthesis_dispatch(self):
        from handlers.synthesis.synthesis_handlers import _DISPATCH
        assert len(_DISPATCH) == 2
        assert "debate.Synthesis.SynthesizePositions" in _DISPATCH
        assert "debate.Synthesis.BuildConsensus" in _DISPATCH

    def test_total_handler_count(self):
        from handlers.framing.framing_handlers import _DISPATCH as d1
        from handlers.argumentation.argumentation_handlers import _DISPATCH as d2
        from handlers.evaluation.evaluation_handlers import _DISPATCH as d3
        from handlers.synthesis.synthesis_handlers import _DISPATCH as d4
        assert len(d1) + len(d2) + len(d3) + len(d4) == 8


# ---------------------------------------------------------------------------
# TestCompilation
# ---------------------------------------------------------------------------
class TestCompilation:
    @pytest.fixture()
    def parsed_ast(self):
        from afl.parser import AFLParser
        afl_path = os.path.join(os.path.dirname(__file__), "..", "afl", "debate.afl")
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
        mixins_ns = [ns for ns in parsed_ast.namespaces if ns.name == "debate.mixins"][0]
        assert len(mixins_ns.facets) == 2

    def test_implicit_count(self, parsed_ast):
        implicits = []
        for ns in parsed_ast.namespaces:
            implicits.extend(ns.implicits)
        assert len(implicits) == 2

    def test_foreach_present(self, parsed_ast):
        workflows_ns = [ns for ns in parsed_ast.namespaces if ns.name == "debate.workflows"][0]
        consensus = [w for w in workflows_ns.workflows if w.sig.name == "ConsensusDebate"][0]
        has_foreach = any(
            block.foreach is not None
            for block in consensus.body
        )
        assert has_foreach


# ---------------------------------------------------------------------------
# TestAgentIntegration
# ---------------------------------------------------------------------------
class TestAgentIntegration:
    def test_tool_registry_dispatches_all_handlers(self):
        from afl.runtime.agent import ToolRegistry
        from handlers.framing.framing_handlers import _DISPATCH as d1
        from handlers.argumentation.argumentation_handlers import _DISPATCH as d2
        from handlers.evaluation.evaluation_handlers import _DISPATCH as d3
        from handlers.synthesis.synthesis_handlers import _DISPATCH as d4

        registry = ToolRegistry()
        for dispatch in [d1, d2, d3, d4]:
            for facet_name, handler in dispatch.items():
                tool_name = facet_name.split(".")[-1]
                registry.register(tool_name, handler)

        tool_names = [
            "FrameDebate", "AssignRoles", "GenerateArgument",
            "GenerateRebuttal", "ScoreArguments", "JudgeDebate",
            "SynthesizePositions", "BuildConsensus",
        ]
        for name in tool_names:
            assert registry.has_handler(name), f"Missing handler: {name}"

    def test_claude_agent_runner_with_custom_handlers(self):
        from afl.runtime import Evaluator, ExecutionStatus, MemoryStore, Telemetry
        from afl.runtime.agent import ClaudeAgentRunner, ToolRegistry
        from handlers.framing.framing_handlers import handle_frame_debate

        store = MemoryStore()
        evaluator = Evaluator(persistence=store, telemetry=Telemetry(enabled=False))

        registry = ToolRegistry()
        registry.register("FrameDebate", handle_frame_debate)

        runner = ClaudeAgentRunner(
            evaluator=evaluator,
            persistence=store,
            tool_registry=registry,
        )

        workflow_ast = {
            "type": "WorkflowDecl",
            "name": "TestDebate",
            "params": [{"name": "topic", "type": "String"}],
            "returns": [{"name": "result", "type": "String"}],
            "body": {
                "type": "AndThenBlock",
                "steps": [
                    {
                        "type": "StepStmt",
                        "id": "step-frame",
                        "name": "frame",
                        "call": {
                            "type": "CallExpr",
                            "target": "FrameDebate",
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
                    "id": "yield-TD",
                    "call": {
                        "type": "CallExpr",
                        "target": "TestDebate",
                        "args": [
                            {
                                "name": "result",
                                "value": {"type": "StepRef", "path": ["frame", "topic_analysis"]},
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
                    "name": "FrameDebate",
                    "params": [{"name": "topic", "type": "String"}],
                    "returns": [{"name": "topic_analysis", "type": "String"}],
                },
            ],
        }

        result = runner.run(
            workflow_ast,
            inputs={"topic": "AI ethics"},
            program_ast=program_ast,
        )

        assert result.success is True
        assert result.status == ExecutionStatus.COMPLETED

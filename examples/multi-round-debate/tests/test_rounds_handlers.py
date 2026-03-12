"""Tests for multi-round-debate handlers and AFL compilation."""

from __future__ import annotations

import json
import os

import pytest


# ---------------------------------------------------------------------------
# TestRoundsUtils
# ---------------------------------------------------------------------------
class TestRoundsUtils:
    def test_initiate_round_structure(self):
        from handlers.shared.rounds_utils import initiate_round

        result = initiate_round("artificial intelligence", 1, 3)
        assert "round_num" in result
        assert "topic" in result
        assert "agents" in result
        assert result["round_num"] == 1
        assert len(result["agents"]) == 3

    def test_assign_positions_cycling(self):
        from handlers.shared.rounds_utils import assign_positions

        state = {"agents": ["agent_0", "agent_1", "agent_2"]}
        r1 = assign_positions(state, round_num=1)
        r2 = assign_positions(state, round_num=2)
        assert len(r1) == 3
        # Stances should cycle: round_num % 3 shifts stance index
        assert r1[0]["stance"] != r2[0]["stance"]

    def test_refine_argument_confidence(self):
        from handlers.shared.rounds_utils import refine_argument

        r1 = refine_argument("agent_0", "AI", "for", round_num=1)
        assert 0.0 < r1["confidence"] <= 1.0
        assert r1["agent"] == "agent_0"
        assert r1["stance"] == "for"

    def test_refine_argument_determinism(self):
        from handlers.shared.rounds_utils import refine_argument

        a = refine_argument("agent_0", "climate", "against", 2)
        b = refine_argument("agent_0", "climate", "against", 2)
        assert a == b

    def test_challenge_argument_weaknesses(self):
        from handlers.shared.rounds_utils import challenge_argument

        result = challenge_argument("agent_0", "agent_1", "test argument", 1)
        assert "challenge" in result
        assert "weaknesses" in result
        assert len(result["weaknesses"]) >= 1

    def test_score_round_improvement(self):
        from handlers.shared.rounds_utils import score_round

        args = [{"agent": "agent_0"}, {"agent": "agent_1"}, {"agent": "agent_2"}]
        scores = score_round(args, [], None, 1)
        assert len(scores) == 3
        for s in scores:
            assert "score" in s
            assert "improvement" in s

    def test_evaluate_convergence_metrics(self):
        from handlers.shared.rounds_utils import evaluate_convergence

        current = [{"score": 80.0}, {"score": 82.0}]
        prev = [{"score": 78.0}, {"score": 79.0}]
        result = evaluate_convergence(current, prev, 2)
        assert "score_delta" in result
        assert "converged" in result
        assert isinstance(result["converged"], bool)

    def test_declare_outcome_winner(self):
        from handlers.shared.rounds_utils import declare_outcome

        scores = [
            {"agent": "agent_0", "score": 85.0},
            {"agent": "agent_1", "score": 90.0},
        ]
        result = declare_outcome(["s1", "s2"], scores, [], "AI")
        assert result["winner"] == "agent_1"
        assert result["total_rounds"] == 2


# ---------------------------------------------------------------------------
# TestSetupHandlers
# ---------------------------------------------------------------------------
class TestSetupHandlers:
    def test_initiate_round_default(self):
        from handlers.setup.setup_handlers import handle_initiate_round

        result = handle_initiate_round({"topic": "AI ethics"})
        assert "round_state" in result
        assert result["round_state"]["topic"] == "AI ethics"

    def test_assign_positions_default(self):
        from handlers.setup.setup_handlers import handle_assign_positions

        state = {"agents": ["agent_0", "agent_1", "agent_2"]}
        result = handle_assign_positions({"round_state": state, "round_num": 1})
        assert "assignments" in result
        assert len(result["assignments"]) == 3

    def test_assign_positions_json_string(self):
        from handlers.setup.setup_handlers import handle_assign_positions

        state = {"agents": ["agent_0", "agent_1"]}
        result = handle_assign_positions(
            {
                "round_state": json.dumps(state),
                "round_num": 2,
            }
        )
        assert len(result["assignments"]) == 2


# ---------------------------------------------------------------------------
# TestArgumentationHandlers
# ---------------------------------------------------------------------------
class TestArgumentationHandlers:
    def test_refine_argument_default(self):
        from handlers.argumentation.argumentation_handlers import handle_refine_argument

        result = handle_refine_argument(
            {
                "agent": "agent_0",
                "topic": "climate",
                "stance": "for",
            }
        )
        assert "refined" in result
        assert result["refined"]["agent"] == "agent_0"

    def test_refine_argument_custom_round(self):
        from handlers.argumentation.argumentation_handlers import handle_refine_argument

        result = handle_refine_argument(
            {
                "agent": "agent_1",
                "topic": "AI",
                "stance": "against",
                "round_num": 3,
            }
        )
        assert result["refined"]["stance"] == "against"

    def test_challenge_argument_default(self):
        from handlers.argumentation.argumentation_handlers import handle_challenge_argument

        result = handle_challenge_argument(
            {
                "agent": "agent_0",
                "target_agent": "agent_1",
                "target_argument": "AI is dangerous",
            }
        )
        assert "challenge" in result
        assert "weaknesses" in result

    def test_challenge_argument_json_string(self):
        from handlers.argumentation.argumentation_handlers import handle_challenge_argument

        arg_dict = {"argument": "test arg", "agent": "agent_1"}
        result = handle_challenge_argument(
            {
                "agent": "agent_0",
                "target_agent": "agent_1",
                "target_argument": json.dumps(arg_dict),
            }
        )
        assert "challenge" in result


# ---------------------------------------------------------------------------
# TestScoringHandlers
# ---------------------------------------------------------------------------
class TestScoringHandlers:
    def test_score_round_default(self):
        from handlers.scoring.scoring_handlers import handle_score_round

        args = [{"agent": "agent_0"}, {"agent": "agent_1"}]
        result = handle_score_round({"arguments": args, "challenges": []})
        assert "scores" in result
        assert len(result["scores"]) == 2

    def test_score_round_json_string(self):
        from handlers.scoring.scoring_handlers import handle_score_round

        args = [{"agent": "agent_0"}]
        result = handle_score_round(
            {
                "arguments": json.dumps(args),
                "challenges": "[]",
            }
        )
        assert len(result["scores"]) == 1

    def test_evaluate_convergence_default(self):
        from handlers.scoring.scoring_handlers import handle_evaluate_convergence

        result = handle_evaluate_convergence(
            {
                "current_scores": [{"score": 80.0}],
            }
        )
        assert "metrics" in result
        assert "score_delta" in result["metrics"]

    def test_evaluate_convergence_json_string(self):
        from handlers.scoring.scoring_handlers import handle_evaluate_convergence

        result = handle_evaluate_convergence(
            {
                "current_scores": json.dumps([{"score": 85.0}]),
                "prev_scores": json.dumps([{"score": 80.0}]),
            }
        )
        assert "metrics" in result
        assert isinstance(result["metrics"]["converged"], bool)


# ---------------------------------------------------------------------------
# TestSynthesisHandlers
# ---------------------------------------------------------------------------
class TestSynthesisHandlers:
    def test_summarize_round_default(self):
        from handlers.synthesis.synthesis_handlers import handle_summarize_round

        result = handle_summarize_round(
            {
                "arguments": [{"agent": "a0"}, {"agent": "a1"}],
                "challenges": ["c1"],
                "scores": [{"score": 80}],
            }
        )
        assert "synthesis" in result
        assert "key_shifts" in result

    def test_summarize_round_json_string(self):
        from handlers.synthesis.synthesis_handlers import handle_summarize_round

        result = handle_summarize_round(
            {
                "arguments": json.dumps([{"agent": "a0"}]),
                "challenges": json.dumps(["c1"]),
                "scores": json.dumps([{"score": 80}]),
            }
        )
        assert "synthesis" in result

    def test_declare_outcome_default(self):
        from handlers.synthesis.synthesis_handlers import handle_declare_outcome

        result = handle_declare_outcome(
            {
                "round_syntheses": ["s1", "s2"],
                "final_scores": [{"agent": "agent_0", "score": 90}],
                "convergence_trajectory": [],
                "topic": "AI",
            }
        )
        assert "outcome" in result
        assert result["outcome"]["winner"] == "agent_0"

    def test_declare_outcome_json_string(self):
        from handlers.synthesis.synthesis_handlers import handle_declare_outcome

        result = handle_declare_outcome(
            {
                "round_syntheses": json.dumps(["s1"]),
                "final_scores": json.dumps([{"agent": "a0", "score": 85}]),
                "convergence_trajectory": json.dumps([]),
                "topic": "climate",
            }
        )
        assert "outcome" in result


# ---------------------------------------------------------------------------
# TestDispatch
# ---------------------------------------------------------------------------
class TestDispatch:
    def test_setup_dispatch(self):
        from handlers.setup.setup_handlers import _DISPATCH

        assert len(_DISPATCH) == 2
        assert "multidebate.Setup.InitiateRound" in _DISPATCH
        assert "multidebate.Setup.AssignPositions" in _DISPATCH

    def test_argumentation_dispatch(self):
        from handlers.argumentation.argumentation_handlers import _DISPATCH

        assert len(_DISPATCH) == 2
        assert "multidebate.Argumentation.RefineArgument" in _DISPATCH
        assert "multidebate.Argumentation.ChallengeArgument" in _DISPATCH

    def test_scoring_dispatch(self):
        from handlers.scoring.scoring_handlers import _DISPATCH

        assert len(_DISPATCH) == 2
        assert "multidebate.Scoring.ScoreRound" in _DISPATCH
        assert "multidebate.Scoring.EvaluateConvergence" in _DISPATCH

    def test_synthesis_dispatch(self):
        from handlers.synthesis.synthesis_handlers import _DISPATCH

        assert len(_DISPATCH) == 2
        assert "multidebate.Synthesis.SummarizeRound" in _DISPATCH
        assert "multidebate.Synthesis.DeclareOutcome" in _DISPATCH

    def test_total_handler_count(self):
        from handlers.argumentation.argumentation_handlers import _DISPATCH as d2
        from handlers.scoring.scoring_handlers import _DISPATCH as d3
        from handlers.setup.setup_handlers import _DISPATCH as d1
        from handlers.synthesis.synthesis_handlers import _DISPATCH as d4

        assert len(d1) + len(d2) + len(d3) + len(d4) == 8


# ---------------------------------------------------------------------------
# TestCompilation
# ---------------------------------------------------------------------------
class TestCompilation:
    @pytest.fixture()
    def parsed_ast(self):
        from afl.parser import AFLParser

        afl_path = os.path.join(os.path.dirname(__file__), "..", "afl", "rounds.afl")
        with open(afl_path) as f:
            source = f.read()
        return AFLParser().parse(source)

    def test_afl_parses(self, parsed_ast):
        assert parsed_ast is not None

    def test_schema_count(self, parsed_ast):
        schemas = []
        for ns in parsed_ast.namespaces:
            schemas.extend(ns.schemas)
        assert len(schemas) == 5

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
        mixins_ns = [ns for ns in parsed_ast.namespaces if ns.name == "multidebate.mixins"][0]
        assert len(mixins_ns.facets) == 2

    def test_implicit_count(self, parsed_ast):
        implicits = []
        for ns in parsed_ast.namespaces:
            implicits.extend(ns.implicits)
        assert len(implicits) == 2

    def test_foreach_present(self, parsed_ast):
        workflows_ns = [ns for ns in parsed_ast.namespaces if ns.name == "multidebate.workflows"][0]
        agent_focused = [w for w in workflows_ns.workflows if w.sig.name == "AgentFocusedDebate"][0]
        has_foreach = any(block.foreach is not None for block in agent_focused.body)
        assert has_foreach


# ---------------------------------------------------------------------------
# TestAgentIntegration
# ---------------------------------------------------------------------------
class TestAgentIntegration:
    def test_tool_registry_dispatches_all_handlers(self):
        from handlers.argumentation.argumentation_handlers import _DISPATCH as d2
        from handlers.scoring.scoring_handlers import _DISPATCH as d3
        from handlers.setup.setup_handlers import _DISPATCH as d1
        from handlers.synthesis.synthesis_handlers import _DISPATCH as d4

        from afl.runtime.agent import ToolRegistry

        registry = ToolRegistry()
        for dispatch in [d1, d2, d3, d4]:
            for facet_name, handler in dispatch.items():
                tool_name = facet_name.split(".")[-1]
                registry.register(tool_name, handler)

        tool_names = [
            "InitiateRound",
            "AssignPositions",
            "RefineArgument",
            "ChallengeArgument",
            "ScoreRound",
            "EvaluateConvergence",
            "SummarizeRound",
            "DeclareOutcome",
        ]
        for name in tool_names:
            assert registry.has_handler(name), f"Missing handler: {name}"

    def test_claude_agent_runner_with_custom_handlers(self):
        from handlers.setup.setup_handlers import handle_initiate_round

        from afl.runtime import Evaluator, ExecutionStatus, MemoryStore, Telemetry
        from afl.runtime.agent import ClaudeAgentRunner, ToolRegistry

        store = MemoryStore()
        evaluator = Evaluator(persistence=store, telemetry=Telemetry(enabled=False))

        registry = ToolRegistry()
        registry.register("InitiateRound", handle_initiate_round)

        runner = ClaudeAgentRunner(
            evaluator=evaluator,
            persistence=store,
            tool_registry=registry,
        )

        workflow_ast = {
            "type": "WorkflowDecl",
            "name": "TestRounds",
            "params": [{"name": "topic", "type": "String"}],
            "returns": [{"name": "result", "type": "String"}],
            "body": {
                "type": "AndThenBlock",
                "steps": [
                    {
                        "type": "StepStmt",
                        "id": "step-init",
                        "name": "init",
                        "call": {
                            "type": "CallExpr",
                            "target": "InitiateRound",
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
                        "target": "TestRounds",
                        "args": [
                            {
                                "name": "result",
                                "value": {"type": "StepRef", "path": ["init", "round_state"]},
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
                    "name": "InitiateRound",
                    "params": [{"name": "topic", "type": "String"}],
                    "returns": [{"name": "round_state", "type": "Json"}],
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

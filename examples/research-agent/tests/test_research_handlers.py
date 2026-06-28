"""Tests for the fwh_anthropic-powered research agent.

The 8 event-facet handlers each call ``anthropic.tools._lib.messages.create_message``
via fwh_anthropic, parse Claude's JSON response, and return the typed
schema. The unit tests mock the Anthropic client at the ``get_client``
boundary so no real API calls are made.

Three test surfaces:

1. **Parser utilities** (``extract_json_payload``, ``coerce_*``,
   ``synthetic_*``) — pure functions, exercised with hand-crafted
   inputs.
2. **Per-area handlers** — drive each handler end-to-end with a mocked
   client returning canned JSON; verify schema-shaped output.
3. **FFL compilation** — confirm the workflow file parses and exposes
   the expected structural counts (8 event facets, 3 workflows, etc).
"""

from __future__ import annotations

import json
import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Mock factory — Anthropic-shaped client returning canned text
# ---------------------------------------------------------------------------


def _mock_client(reply_text: str):
    """Client whose messages.create returns *reply_text* verbatim."""
    client = MagicMock()
    client.messages.create = MagicMock(
        return_value=SimpleNamespace(
            content=[SimpleNamespace(type="text", text=reply_text)],
            model="claude-sonnet-4-6",
            stop_reason="end_turn",
            usage=SimpleNamespace(input_tokens=10, output_tokens=5),
        )
    )
    return client


def _patch_anthropic_client(client):
    """Patch fwh_anthropic's get_client to return *client*.

    research-agent depends on the external ``fwh_anthropic`` domain package (see
    requirements.txt); the handler tests exercise its ``messages`` module, so
    when it isn't installed (e.g. base CI) they skip rather than error — matching
    the repo convention (``pytest.importorskip`` for boto3/shapely/etc.). The
    stdlib-only parser/coercion tests in this module keep running regardless.
    """
    msg_lib = pytest.importorskip(
        "anthropic_handlers.tools._lib.messages",
        reason="fwh_anthropic not installed; see examples/research-agent/requirements.txt",
    )

    return patch.object(msg_lib, "get_client", return_value=client)


# ---------------------------------------------------------------------------
# Parser utilities — JSON extraction and coercion
# ---------------------------------------------------------------------------


class TestExtractJsonPayload:
    def test_bare_json_object(self):
        from handlers.shared.research_utils import extract_json_payload

        assert extract_json_payload('{"x": 1}') == {"x": 1}

    def test_bare_json_array(self):
        from handlers.shared.research_utils import extract_json_payload

        assert extract_json_payload("[1, 2, 3]") == [1, 2, 3]

    def test_strips_code_fence(self):
        from handlers.shared.research_utils import extract_json_payload

        wrapped = 'Here is the data:\n```json\n{"a": 1, "b": [2, 3]}\n```\nDone.'
        assert extract_json_payload(wrapped) == {"a": 1, "b": [2, 3]}

    def test_strips_unlabelled_fence(self):
        from handlers.shared.research_utils import extract_json_payload

        wrapped = '```\n[{"id": "x"}]\n```'
        assert extract_json_payload(wrapped) == [{"id": "x"}]

    def test_finds_first_balanced_object(self):
        from handlers.shared.research_utils import extract_json_payload

        msg = 'Sure! The JSON is {"score": 87, "approved": true} — let me know.'
        assert extract_json_payload(msg) == {"score": 87, "approved": True}

    def test_finds_first_balanced_array(self):
        from handlers.shared.research_utils import extract_json_payload

        msg = 'Here you go: ["a", "b"] and that\'s all.'
        assert extract_json_payload(msg) == ["a", "b"]

    def test_empty_text_raises(self):
        from handlers.shared.research_utils import extract_json_payload

        with pytest.raises(ValueError, match="empty"):
            extract_json_payload("")

    def test_garbage_raises(self):
        from handlers.shared.research_utils import extract_json_payload

        with pytest.raises(ValueError, match="could not parse"):
            extract_json_payload("this is just prose with no json")


class TestCoercion:
    def test_coerce_topic_fills_defaults(self):
        from handlers.shared.research_utils import coerce_topic

        out = coerce_topic({"name": "x"}, topic_name="fallback", depth=3, max_subtopics=5)
        assert out["name"] == "x"
        assert out["depth"] == 3
        assert out["max_subtopics"] == 5

    def test_coerce_topic_synth_fallback_for_non_dict(self):
        from handlers.shared.research_utils import coerce_topic

        out = coerce_topic("not a dict", topic_name="t", depth=2, max_subtopics=3)
        assert out["name"] == "t"
        assert out["depth"] == 2

    def test_coerce_subtopics_caps_count(self):
        from handlers.shared.research_utils import coerce_subtopics

        parsed = [{"name": f"s{i}", "description": f"d{i}", "priority": i} for i in range(10)]
        out = coerce_subtopics(parsed, parent_topic="P", max_subtopics=3)
        assert len(out) == 3
        assert all(s["parent_topic"] == "P" for s in out)

    def test_coerce_sources_synth_fallback(self):
        from handlers.shared.research_utils import coerce_sources

        out = coerce_sources(None, subtopic_name="s", max_sources=2)
        assert len(out) == 2

    def test_coerce_findings_keeps_only_dicts(self):
        from handlers.shared.research_utils import coerce_findings

        parsed = [
            {"claim": "a", "evidence": "b", "confidence": 0.8, "source_title": "t"},
            "not a finding",  # ignored
            {"claim": "c"},
        ]
        out = coerce_findings(parsed, subtopic_name="s")
        assert len(out) == 2
        assert out[0]["confidence"] == 0.8

    def test_coerce_analysis_synth_fallback(self):
        from handlers.shared.research_utils import coerce_analysis

        out = coerce_analysis(None, topic_name="t")
        assert "themes" in out
        assert 0.5 <= out["confidence_score"] <= 0.9

    def test_coerce_review_explicit_approved(self):
        from handlers.shared.research_utils import coerce_review

        out = coerce_review({"score": 90, "approved": False}, topic_name="t")
        assert out["approved"] is False


# ---------------------------------------------------------------------------
# Per-area handlers — drive end-to-end with mocked Anthropic
# ---------------------------------------------------------------------------


class TestPlanningHandlers:
    def test_plan_research_returns_typed_topic(self):
        from handlers.planning.planning_handlers import handle_plan_research

        body = json.dumps(
            {
                "name": "climate adaptation",
                "depth": 4,
                "keywords": ["resilience"],
                "summary": "plan",
                "max_subtopics": 3,
            }
        )
        with _patch_anthropic_client(_mock_client(body)):
            out = handle_plan_research(
                {"topic": "climate adaptation", "depth": 4, "max_subtopics": 3}
            )
        assert out["plan"]["name"] == "climate adaptation"
        assert out["plan"]["keywords"] == ["resilience"]

    def test_plan_research_synthetic_fallback(self):
        from handlers.planning.planning_handlers import handle_plan_research

        with _patch_anthropic_client(_mock_client("no JSON here")):
            out = handle_plan_research({"topic": "t", "depth": 2, "max_subtopics": 4})
        assert out["plan"]["name"] == "t"
        assert out["plan"]["max_subtopics"] == 4

    def test_decompose_with_fenced_response(self):
        from handlers.planning.planning_handlers import handle_decompose_into_subtopics

        body = (
            '```json\n[{"name": "a", "parent_topic": "P", "description": "d", "priority": 1}]\n```'
        )
        with _patch_anthropic_client(_mock_client(body)):
            out = handle_decompose_into_subtopics(
                {"topic": {"name": "P", "keywords": []}, "max_subtopics": 5}
            )
        assert len(out["subtopics"]) == 1
        assert out["subtopics"][0]["name"] == "a"

    def test_decompose_accepts_json_string_topic(self):
        from handlers.planning.planning_handlers import handle_decompose_into_subtopics

        body = json.dumps([{"name": "x", "parent_topic": "P", "description": "d", "priority": 1}])
        with _patch_anthropic_client(_mock_client(body)):
            out = handle_decompose_into_subtopics(
                {"topic": json.dumps({"name": "P", "keywords": []}), "max_subtopics": 5}
            )
        assert out["subtopics"][0]["parent_topic"] == "P"


class TestGatheringHandlers:
    def test_gather_sources(self):
        from handlers.gathering.gathering_handlers import handle_gather_sources

        body = json.dumps(
            [
                {"title": "T1", "url": "u1", "relevance_score": 0.9, "source_type": "journal"},
                {"title": "T2", "url": "u2", "relevance_score": 0.7, "source_type": "book"},
            ]
        )
        with _patch_anthropic_client(_mock_client(body)):
            out = handle_gather_sources(
                {"subtopic": {"name": "s", "description": "d"}, "max_sources": 5}
            )
        assert len(out["sources"]) == 2
        assert out["sources"][0]["relevance_score"] == 0.9

    def test_gather_sources_caps(self):
        from handlers.gathering.gathering_handlers import handle_gather_sources

        sources = [
            {"title": f"S{i}", "url": "u", "relevance_score": 0.5, "source_type": "web"}
            for i in range(20)
        ]
        with _patch_anthropic_client(_mock_client(json.dumps(sources))):
            out = handle_gather_sources(
                {"subtopic": {"name": "s", "description": "d"}, "max_sources": 3}
            )
        assert len(out["sources"]) == 3

    def test_extract_findings_falls_back(self):
        from handlers.gathering.gathering_handlers import handle_extract_findings

        with _patch_anthropic_client(_mock_client("no JSON")):
            out = handle_extract_findings({"subtopic": {"name": "x"}, "sources": []})
        assert isinstance(out["findings"], list)
        assert all("claim" in f for f in out["findings"])


class TestAnalysisHandlers:
    def test_synthesize_findings(self):
        from handlers.analysis.analysis_handlers import handle_synthesize_findings

        body = json.dumps(
            {
                "themes": ["t1", "t2"],
                "contradictions": [],
                "gaps": ["g1"],
                "summary": "s",
                "confidence_score": 0.82,
            }
        )
        with _patch_anthropic_client(_mock_client(body)):
            out = handle_synthesize_findings(
                {"topic": {"name": "T"}, "all_findings": [[{"claim": "a"}]]}
            )
        assert out["analysis"]["themes"] == ["t1", "t2"]
        assert out["analysis"]["confidence_score"] == 0.82

    def test_identify_gaps_pair(self):
        from handlers.analysis.analysis_handlers import handle_identify_gaps

        body = json.dumps(
            {
                "gaps": [{"description": "g1", "severity": "high", "area": "method"}],
                "recommendations": [
                    {"action": "do x", "priority": "high", "estimated_effort": "low"}
                ],
            }
        )
        with _patch_anthropic_client(_mock_client(body)):
            out = handle_identify_gaps(
                {"analysis": {"gaps": [], "confidence_score": 0.8}, "topic": {"name": "T"}}
            )
        assert len(out["gaps"]) == 1
        assert len(out["recommendations"]) == 1


class TestWritingHandlers:
    def test_draft_report(self):
        from handlers.writing.writing_handlers import handle_draft_report

        body = json.dumps(
            {
                "title": "Report on X",
                "sections": [{"title": "Intro", "content": "one two three"}],
                "citations": ["[1] src"],
            }
        )
        with _patch_anthropic_client(_mock_client(body)):
            out = handle_draft_report(
                {"topic": {"name": "X"}, "analysis": {"themes": []}, "gaps": []}
            )
        assert out["draft"]["title"] == "Report on X"
        assert out["draft"]["word_count"] >= 3  # derived from sections

    def test_review_draft(self):
        from handlers.writing.writing_handlers import handle_review_draft

        body = json.dumps(
            {"score": 78, "approved": True, "feedback": ["good"], "suggested_edits": []}
        )
        with _patch_anthropic_client(_mock_client(body)):
            out = handle_review_draft(
                {
                    "draft": {"title": "R", "word_count": 100},
                    "topic": {"name": "X"},
                    "analysis": {"confidence_score": 0.8},
                }
            )
        assert out["review"]["score"] == 78
        assert out["review"]["approved"] is True


# ---------------------------------------------------------------------------
# Dispatch + registration
# ---------------------------------------------------------------------------


class TestDispatch:
    def test_planning_dispatch(self):
        from handlers.planning.planning_handlers import _DISPATCH

        assert set(_DISPATCH.keys()) == {
            "research.Planning.PlanResearch",
            "research.Planning.DecomposeIntoSubtopics",
        }

    def test_gathering_dispatch(self):
        from handlers.gathering.gathering_handlers import _DISPATCH

        assert set(_DISPATCH.keys()) == {
            "research.Gathering.GatherSources",
            "research.Gathering.ExtractFindings",
        }

    def test_analysis_dispatch(self):
        from handlers.analysis.analysis_handlers import _DISPATCH

        assert set(_DISPATCH.keys()) == {
            "research.Analysis.SynthesizeFindings",
            "research.Analysis.IdentifyGaps",
        }

    def test_writing_dispatch(self):
        from handlers.writing.writing_handlers import _DISPATCH

        assert set(_DISPATCH.keys()) == {
            "research.Writing.DraftReport",
            "research.Writing.ReviewDraft",
        }

    def test_total_handler_count(self):
        from handlers.analysis.analysis_handlers import _DISPATCH as d3
        from handlers.gathering.gathering_handlers import _DISPATCH as d2
        from handlers.planning.planning_handlers import _DISPATCH as d1
        from handlers.writing.writing_handlers import _DISPATCH as d4

        assert len(d1) + len(d2) + len(d3) + len(d4) == 8


# ---------------------------------------------------------------------------
# FFL compilation
# ---------------------------------------------------------------------------


class TestCompilation:
    @pytest.fixture()
    def parsed_ast(self):
        from facetwork.parser import FFLParser

        afl_path = os.path.join(os.path.dirname(__file__), "..", "ffl", "research.ffl")
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

    def test_no_prompt_blocks(self, parsed_ast):
        """Port removed all prompt blocks in favour of CreateMessage handler calls."""
        from facetwork.ast import PromptBlock

        count = 0
        for ns in parsed_ast.namespaces:
            for ef in ns.event_facets:
                if isinstance(ef.body, PromptBlock):
                    count += 1
        assert count == 0

    def test_workflow_count(self, parsed_ast):
        workflows = []
        for ns in parsed_ast.namespaces:
            workflows.extend(ns.workflows)
        # ResearchTopic + DeepDive
        assert len(workflows) == 2

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
        has_foreach = any(block.foreach is not None for block in deep_dive.body)
        assert has_foreach

    def test_research_topic_workflow_present(self, parsed_ast):
        """run.py drives ResearchTopic (with statement-level andThen fan-out)."""
        workflows_ns = [ns for ns in parsed_ast.namespaces if ns.name == "research.workflows"][0]
        names = {w.sig.name for w in workflows_ns.workflows}
        assert "ResearchTopic" in names

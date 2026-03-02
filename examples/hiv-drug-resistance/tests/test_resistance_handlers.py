"""Tests for the hiv-drug-resistance example handlers."""

from __future__ import annotations

import json
import os

import pytest


# ---------------------------------------------------------------------------
# TestResistanceUtils — utility function tests
# ---------------------------------------------------------------------------
class TestResistanceUtils:
    def test_assess_quality_passed(self):
        from handlers.shared.resistance_utils import assess_read_quality

        result = assess_read_quality("sample-001", "/data/s1.fastq.gz", 20, 50)
        assert "passed" in result
        assert "total_reads" in result
        assert "mean_quality" in result
        assert "coverage_depth" in result
        assert isinstance(result["passed"], bool)

    def test_assess_quality_deterministic(self):
        from handlers.shared.resistance_utils import assess_read_quality

        r1 = assess_read_quality("sample-001", "/data/s1.fastq.gz")
        r2 = assess_read_quality("sample-001", "/data/s1.fastq.gz")
        assert r1 == r2

    def test_align_reads_returns_fields(self):
        from handlers.shared.resistance_utils import align_reads

        result = align_reads("sample-001", "/data/s1.fastq.gz", "HXB2")
        assert "bam_path" in result
        assert "mapped_reads" in result
        assert "coverage_pct" in result
        assert "mean_depth" in result
        assert result["bam_path"].endswith(".bam")

    def test_call_variants_returns_list_and_stats(self):
        from handlers.shared.resistance_utils import call_variants

        variants, stats = call_variants("sample-001", "/tmp/s1.bam")
        assert isinstance(variants, list)
        assert len(variants) >= 5
        assert "total_variants" in stats
        assert "drm_count" in stats
        assert stats["total_variants"] == len(variants)

    def test_classify_mutation_drm_detection(self):
        from handlers.shared.resistance_utils import classify_mutation

        # RT position 184 is a known NRTI DRM (M184V)
        m = classify_mutation("RT", 184, "M", "V")
        assert m["is_drm"] is True
        assert m["drug_class"] == "NRTI"
        assert m["notation"] == "M184V"

    def test_classify_mutation_apobec_detection(self):
        from handlers.shared.resistance_utils import classify_mutation

        # G→A is an APOBEC signature
        m = classify_mutation("RT", 50, "G", "A")
        assert m["is_apobec"] is True

    def test_score_resistance_levels(self):
        from handlers.shared.resistance_utils import LEVEL_ORDER, score_resistance

        mutations = [
            {"gene": "RT", "position": 184, "is_drm": True, "drug_class": "NRTI"},
            {"gene": "RT", "position": 103, "is_drm": True, "drug_class": "NNRTI"},
        ]
        drug_scores, total, highest = score_resistance("sample-001", mutations)
        assert isinstance(drug_scores, list)
        assert total > 0
        assert highest in LEVEL_ORDER

    def test_generate_sample_report(self):
        from handlers.shared.resistance_utils import generate_sample_report

        path, summary = generate_sample_report(
            "sample-001",
            {"passed": True},
            {"coverage_pct": 95.5},
            [{"is_drm": True}, {"is_drm": False}],
            {},
        )
        assert path.endswith(".html")
        assert "sample-001" in summary
        assert "PASS" in summary


# ---------------------------------------------------------------------------
# TestSequencingHandlers — sequencing handler wrapper tests
# ---------------------------------------------------------------------------
class TestSequencingHandlers:
    def test_handle_assess_quality(self):
        from handlers.sequencing.sequencing_handlers import handle_assess_quality

        result = handle_assess_quality(
            {
                "sample_id": "s1",
                "fastq_path": "/data/s1.fastq.gz",
            }
        )
        assert "passed" in result
        assert "total_reads" in result
        assert "mean_quality" in result

    def test_handle_align_reads(self):
        from handlers.sequencing.sequencing_handlers import handle_align_reads

        result = handle_align_reads(
            {
                "sample_id": "s1",
                "fastq_path": "/data/s1.fastq.gz",
                "reference": "HXB2",
            }
        )
        assert "alignment" in result
        assert "bam_path" in result["alignment"]
        assert "mapped_reads" in result["alignment"]

    def test_handle_assess_quality_step_log(self):
        from handlers.sequencing.sequencing_handlers import handle_assess_quality

        messages: list[tuple[str, str]] = []
        handle_assess_quality(
            {
                "sample_id": "s1",
                "fastq_path": "/data/s1.fastq.gz",
                "_step_log": lambda msg, level: messages.append((msg, level)),
            }
        )
        assert len(messages) == 1
        assert "QC" in messages[0][0]


# ---------------------------------------------------------------------------
# TestAnalysisHandlers — analysis handler wrapper tests
# ---------------------------------------------------------------------------
class TestAnalysisHandlers:
    def test_handle_call_variants(self):
        from handlers.analysis.analysis_handlers import handle_call_variants

        result = handle_call_variants(
            {
                "sample_id": "s1",
                "bam_path": "/tmp/s1.bam",
            }
        )
        assert "variants" in result
        assert "total_variants" in result
        assert "drm_count" in result
        assert isinstance(result["variants"], list)

    def test_handle_generate_consensus(self):
        from handlers.analysis.analysis_handlers import handle_generate_consensus

        result = handle_generate_consensus(
            {
                "sample_id": "s1",
                "bam_path": "/tmp/s1.bam",
            }
        )
        assert "consensus_length" in result
        assert "ambiguous_positions" in result
        assert "subtype" in result

    def test_handle_classify_mutations(self):
        from handlers.analysis.analysis_handlers import handle_classify_mutations

        variants = [
            {"gene": "RT", "position": 184, "ref_aa": "M", "alt_aa": "V"},
            {"gene": "PR", "position": 50, "ref_aa": "I", "alt_aa": "V"},
        ]
        result = handle_classify_mutations(
            {
                "sample_id": "s1",
                "variants": variants,
            }
        )
        assert "mutations" in result
        assert "drm_count" in result
        assert "apobec_count" in result

    def test_handle_classify_mutations_json_string(self):
        from handlers.analysis.analysis_handlers import handle_classify_mutations

        variants = [
            {"gene": "RT", "position": 184, "ref_aa": "M", "alt_aa": "V"},
        ]
        result = handle_classify_mutations(
            {
                "sample_id": "s1",
                "variants": json.dumps(variants),
            }
        )
        assert result["drm_count"] >= 1


# ---------------------------------------------------------------------------
# TestInterpretationHandlers — interpretation handler wrapper tests
# ---------------------------------------------------------------------------
class TestInterpretationHandlers:
    def test_handle_score_resistance(self):
        from handlers.interpretation.interpretation_handlers import handle_score_resistance

        mutations = [
            {"gene": "RT", "position": 184, "is_drm": True, "drug_class": "NRTI"},
        ]
        result = handle_score_resistance(
            {
                "sample_id": "s1",
                "mutations": mutations,
            }
        )
        assert "drug_scores" in result
        assert "total_drugs_scored" in result
        assert "highest_level" in result

    def test_handle_interpret_results(self):
        from handlers.interpretation.interpretation_handlers import handle_interpret_results

        drug_scores = [
            {"drug_name": "TDF", "drug_class": "NRTI", "score": 5, "level": "susceptible"},
        ]
        result = handle_interpret_results(
            {
                "sample_id": "s1",
                "drug_scores": drug_scores,
            }
        )
        assert "summary" in result
        assert "recommendations" in result
        assert "resistance_level" in result

    def test_handle_score_resistance_prompt_facet(self):
        """ScoreResistance is a prompt facet — handler provides deterministic fallback."""
        from handlers.interpretation.interpretation_handlers import handle_score_resistance
        from handlers.shared.resistance_utils import LEVEL_ORDER

        result = handle_score_resistance(
            {
                "sample_id": "s1",
                "mutations": [],
            }
        )
        assert result["highest_level"] in LEVEL_ORDER

    def test_handle_interpret_results_step_log(self):
        from handlers.interpretation.interpretation_handlers import handle_interpret_results

        messages: list[tuple[str, str]] = []
        handle_interpret_results(
            {
                "sample_id": "s1",
                "drug_scores": [],
                "_step_log": lambda msg, level: messages.append((msg, level)),
            }
        )
        assert len(messages) == 1
        assert "Interpretation" in messages[0][0]


# ---------------------------------------------------------------------------
# TestReportingHandlers — reporting handler wrapper tests
# ---------------------------------------------------------------------------
class TestReportingHandlers:
    def test_handle_generate_sample_report(self):
        from handlers.reporting.reporting_handlers import handle_generate_sample_report

        result = handle_generate_sample_report(
            {
                "sample_id": "s1",
                "qc_passed": True,
                "alignment": {"coverage_pct": 95.0},
                "variants": [{"is_drm": True}],
                "resistance": {},
            }
        )
        assert "report_path" in result
        assert "report_summary" in result
        assert result["report_path"].endswith(".html")

    def test_handle_generate_batch_report(self):
        from handlers.reporting.reporting_handlers import handle_generate_batch_report

        result = handle_generate_batch_report(
            {
                "batch_id": "batch-001",
                "sample_count": 10,
                "results": [
                    {"status": "completed", "resistance_level": "low"},
                    {"status": "completed", "resistance_level": "susceptible"},
                ],
            }
        )
        assert "report_path" in result
        assert "summary" in result
        assert result["summary"]["batch_id"] == "batch-001"
        assert result["summary"]["total_samples"] == 10

    def test_handle_generate_sample_report_step_log(self):
        from handlers.reporting.reporting_handlers import handle_generate_sample_report

        messages: list[tuple[str, str]] = []
        handle_generate_sample_report(
            {
                "sample_id": "s1",
                "qc_passed": True,
                "alignment": {},
                "variants": [],
                "resistance": {},
                "_step_log": lambda msg, level: messages.append((msg, level)),
            }
        )
        assert len(messages) == 1
        assert "Report" in messages[0][0]


# ---------------------------------------------------------------------------
# TestDispatch — dispatch table structure and routing
# ---------------------------------------------------------------------------
class TestDispatch:
    def test_sequencing_dispatch_count(self):
        from handlers.sequencing.sequencing_handlers import _DISPATCH

        assert len(_DISPATCH) == 2

    def test_analysis_dispatch_count(self):
        from handlers.analysis.analysis_handlers import _DISPATCH

        assert len(_DISPATCH) == 3

    def test_interpretation_dispatch_count(self):
        from handlers.interpretation.interpretation_handlers import _DISPATCH

        assert len(_DISPATCH) == 2

    def test_reporting_dispatch_count(self):
        from handlers.reporting.reporting_handlers import _DISPATCH

        assert len(_DISPATCH) == 2

    def test_all_dispatch_names_have_namespace_prefix(self):
        from handlers.analysis.analysis_handlers import _DISPATCH as d1
        from handlers.interpretation.interpretation_handlers import _DISPATCH as d2
        from handlers.reporting.reporting_handlers import _DISPATCH as d3
        from handlers.sequencing.sequencing_handlers import _DISPATCH as d4

        all_names = list(d1.keys()) + list(d2.keys()) + list(d3.keys()) + list(d4.keys())
        assert len(all_names) == 9
        assert all(n.startswith("hiv.") for n in all_names)


# ---------------------------------------------------------------------------
# TestCompilation — AFL parsing and AST checks
# ---------------------------------------------------------------------------
class TestCompilation:
    @pytest.fixture()
    def parsed_ast(self):
        from afl.parser import AFLParser

        afl_path = os.path.join(os.path.dirname(__file__), "..", "afl", "resistance.afl")
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
        assert len(event_facets) == 9

    def test_workflow_count(self, parsed_ast):
        workflows = []
        for ns in parsed_ast.namespaces:
            workflows.extend(ns.workflows)
        assert len(workflows) == 2

    def test_namespace_count(self, parsed_ast):
        assert len(parsed_ast.namespaces) == 7

    def test_prompt_block_present(self, parsed_ast):
        """Verify prompt blocks appear on event facets."""
        from afl.ast import PromptBlock

        prompt_count = 0
        for ns in parsed_ast.namespaces:
            for ef in ns.event_facets:
                body = ef.body
                if isinstance(body, PromptBlock):
                    prompt_count += 1
        assert prompt_count == 3, f"Expected 3 prompt blocks, got {prompt_count}"

    def test_script_block_present(self, parsed_ast):
        """Verify script block appears on ClassifyMutations."""
        from afl.ast import ScriptBlock

        script_count = 0
        for ns in parsed_ast.namespaces:
            for ef in ns.event_facets:
                if isinstance(ef.body, ScriptBlock):
                    script_count += 1
                elif hasattr(ef, "pre_script") and ef.pre_script is not None:
                    script_count += 1
        assert script_count >= 1, "Expected at least 1 script block"

    def test_when_block_present(self, parsed_ast):
        """Verify andThen when block appears in AnalyzeSample workflow."""
        from afl.ast import WhenBlock

        wf_ns = [ns for ns in parsed_ast.namespaces if ns.name == "hiv.workflows"]
        analyze_wf = [w for w in wf_ns[0].workflows if w.sig.name == "AnalyzeSample"][0]
        body = analyze_wf.body
        assert isinstance(body, list)
        when_body = body[1]
        assert when_body.when is not None
        assert isinstance(when_body.when, WhenBlock)
        assert len(when_body.when.cases) == 2

    def test_catch_present(self, parsed_ast):
        """Verify catch block appears in BatchAnalysis workflow."""
        wf_ns = [ns for ns in parsed_ast.namespaces if ns.name == "hiv.workflows"]
        batch_wf = [w for w in wf_ns[0].workflows if w.sig.name == "BatchAnalysis"][0]
        body = batch_wf.body
        step = body.block.steps[0]
        assert step.catch is not None


# ---------------------------------------------------------------------------
# TestAgentIntegration — end-to-end handler registration
# ---------------------------------------------------------------------------
class TestAgentIntegration:
    def test_registry_runner_poll_once(self):
        """RegistryRunner dispatches all handlers via ToolRegistry."""
        from handlers.analysis.analysis_handlers import _DISPATCH as d1
        from handlers.interpretation.interpretation_handlers import _DISPATCH as d2
        from handlers.reporting.reporting_handlers import _DISPATCH as d3
        from handlers.sequencing.sequencing_handlers import _DISPATCH as d4

        from afl.runtime.agent import ToolRegistry

        registry = ToolRegistry()
        for dispatch in [d1, d2, d3, d4]:
            for facet_name, handler in dispatch.items():
                tool_name = facet_name.split(".")[-1]
                registry.register(tool_name, handler)

        tool_names = [
            "AssessQuality",
            "AlignReads",
            "CallVariants",
            "GenerateConsensus",
            "ClassifyMutations",
            "ScoreResistance",
            "InterpretResults",
            "GenerateSampleReport",
            "GenerateBatchReport",
        ]
        for name in tool_names:
            assert registry.has_handler(name), f"Missing handler: {name}"

    def test_registry_runner_handler_names(self):
        """Verify all dispatch tables have correct namespace prefixes."""
        from handlers.analysis.analysis_handlers import _DISPATCH as d1
        from handlers.interpretation.interpretation_handlers import _DISPATCH as d2
        from handlers.reporting.reporting_handlers import _DISPATCH as d3
        from handlers.sequencing.sequencing_handlers import _DISPATCH as d4

        all_names = list(d1.keys()) + list(d2.keys()) + list(d3.keys()) + list(d4.keys())
        assert len(all_names) == 9
        assert all(n.startswith("hiv.") for n in all_names)

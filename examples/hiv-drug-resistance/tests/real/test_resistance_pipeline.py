"""Integration tests for the HIV drug resistance pipeline.

Exercises the full runtime pipeline: compile AFL → evaluate → dispatch
handlers → resume → completion. Uses MemoryStore (no MongoDB required).
"""

from __future__ import annotations

from hiv_helpers import compile_resistance_afl, extract_workflow, run_to_completion

from afl.runtime import ExecutionStatus

# ============================================================================
# Compilation tests — always run (no network, no MongoDB)
# ============================================================================


class TestCompilation:
    """Verify resistance.afl compiles and workflows are extractable."""

    def test_resistance_afl_compiles(self):
        """AFL compiles without errors through the full pipeline."""
        program = compile_resistance_afl()
        assert "declarations" in program

    def test_analyze_sample_found(self):
        """AnalyzeSample workflow is extractable from the compiled program."""
        program = compile_resistance_afl()
        wf = extract_workflow(program, "AnalyzeSample")
        assert wf["name"] == "AnalyzeSample"
        assert wf["type"] == "WorkflowDecl"

    def test_batch_analysis_found(self):
        """BatchAnalysis workflow is extractable from the compiled program."""
        program = compile_resistance_afl()
        wf = extract_workflow(program, "BatchAnalysis")
        assert wf["name"] == "BatchAnalysis"
        assert wf["type"] == "WorkflowDecl"


# ============================================================================
# Full pipeline tests — run AnalyzeSample through the evaluator + poller
# ============================================================================


class TestAnalyzeSample:
    """Run AnalyzeSample through the full evaluator/poller pipeline."""

    @staticmethod
    def _register_handlers(poller):
        """Register all 9 HIV handlers with the poller."""
        from handlers import register_all_handlers

        register_all_handlers(poller)

    @staticmethod
    def _default_inputs():
        return {
            "sample_id": "HIV-TEST-001",
            "fastq_path": "/data/samples/HIV-TEST-001.fastq.gz",
            "reference": "HXB2",
            "clinical_context": "Treatment-naive patient",
        }

    def test_full_pipeline_completes(self, compiled_program, evaluator, poller):
        """AnalyzeSample runs to COMPLETED with all 9 handlers dispatched."""
        self._register_handlers(poller)
        workflow = extract_workflow(compiled_program, "AnalyzeSample")

        result = run_to_completion(
            evaluator,
            poller,
            workflow,
            compiled_program,
            inputs=self._default_inputs(),
            max_rounds=50,
        )

        assert result.status == ExecutionStatus.COMPLETED

    def test_output_has_status_and_detail(self, compiled_program, evaluator, poller):
        """Output dict contains 'status' and 'detail' keys."""
        self._register_handlers(poller)
        workflow = extract_workflow(compiled_program, "AnalyzeSample")

        result = run_to_completion(
            evaluator,
            poller,
            workflow,
            compiled_program,
            inputs=self._default_inputs(),
            max_rounds=50,
        )

        assert result.status == ExecutionStatus.COMPLETED
        assert "status" in result.outputs
        assert "detail" in result.outputs
        # QC should pass with default deterministic handler → full pipeline
        assert result.outputs["status"] == "completed"
        assert "Resistance level" in result.outputs["detail"]

    def test_steps_created(self, compiled_program, evaluator, poller, memory_store):
        """MemoryStore has >=9 steps (root + QC + pipeline steps + yield)."""
        self._register_handlers(poller)
        workflow = extract_workflow(compiled_program, "AnalyzeSample")

        result = run_to_completion(
            evaluator,
            poller,
            workflow,
            compiled_program,
            inputs=self._default_inputs(),
            max_rounds=50,
        )

        assert result.status == ExecutionStatus.COMPLETED

        # Count all steps for this workflow
        steps = memory_store.get_steps_by_workflow(result.workflow_id)
        # Root + qc + align + variants + consensus + classify + score +
        # interpret + report + yield + block/when steps = at least 9
        assert len(steps) >= 9

    def test_qc_fail_branch(self, compiled_program, evaluator, poller):
        """Forced QC failure triggers the when-fail branch → status='qc_failed'."""
        # Register all handlers first
        self._register_handlers(poller)

        # Override AssessQuality with a handler that always fails QC
        def handle_qc_fail(params):
            return {
                "passed": False,
                "total_reads": 1000,
                "mean_quality": 15.0,
                "coverage_depth": 10,
                "message": "Low quality reads detected",
            }

        poller.register("hiv.Sequencing.AssessQuality", handle_qc_fail)

        workflow = extract_workflow(compiled_program, "AnalyzeSample")

        result = run_to_completion(
            evaluator,
            poller,
            workflow,
            compiled_program,
            inputs=self._default_inputs(),
            max_rounds=50,
        )

        assert result.status == ExecutionStatus.COMPLETED
        assert result.outputs["status"] == "qc_failed"
        assert "QC failed" in result.outputs["detail"]

    def test_downloaded_reference_as_input(self, hxb2_fasta, compiled_program, evaluator, poller):
        """Uses downloaded HXB2 FASTA path as fastq_path input."""
        self._register_handlers(poller)
        workflow = extract_workflow(compiled_program, "AnalyzeSample")

        inputs = {
            "sample_id": "HIV-HXB2-REF",
            "fastq_path": hxb2_fasta,
            "reference": "HXB2",
            "clinical_context": "",
        }

        result = run_to_completion(
            evaluator,
            poller,
            workflow,
            compiled_program,
            inputs=inputs,
            max_rounds=50,
        )

        assert result.status == ExecutionStatus.COMPLETED
        assert result.outputs["status"] in ("completed", "qc_failed")

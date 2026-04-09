"""Integration tests for the HIV drug resistance pipeline.

Exercises the full runtime pipeline: compile FFL → evaluate → dispatch
handlers → resume → completion. Uses MemoryStore (no MongoDB required).
"""

from __future__ import annotations

import os

from hiv_helpers import compile_resistance_afl, extract_workflow, run_to_completion

from facetwork.runtime import ExecutionStatus

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


# ============================================================================
# Batch pipeline tests — run BatchAnalysis (foreach + catch) with real data
# ============================================================================


class TestBatchAnalysis:
    """Run BatchAnalysis through the full evaluator/poller pipeline.

    BatchAnalysis uses ``andThen foreach`` to iterate over sample_ids,
    running AnalyzeSample per sample with ``catch`` for error recovery.
    Each sample goes through all 9 handlers (QC → align → variants →
    consensus → classify → score → interpret → report).
    """

    @staticmethod
    def _register_handlers(poller):
        """Register all 9 HIV handlers with the poller."""
        from handlers import register_all_handlers

        register_all_handlers(poller)

    def test_batch_3_samples_completes(self, compiled_program, evaluator, poller):
        """BatchAnalysis with 3 samples runs to COMPLETED."""
        self._register_handlers(poller)
        workflow = extract_workflow(compiled_program, "BatchAnalysis")

        inputs = {
            "batch_id": "BATCH-2026-001",
            "sample_ids": ["HIV-PT-101", "HIV-PT-102", "HIV-PT-103"],
            "fastq_dir": "/data/clinical/batch001",
            "reference": "HXB2",
        }

        result = run_to_completion(
            evaluator,
            poller,
            workflow,
            compiled_program,
            inputs=inputs,
            max_rounds=100,
        )

        assert result.status == ExecutionStatus.COMPLETED

    def test_batch_steps_per_sample(self, compiled_program, evaluator, poller, memory_store):
        """Each sample creates >=9 steps; batch total >=30 (3 samples + root + blocks)."""
        self._register_handlers(poller)
        workflow = extract_workflow(compiled_program, "BatchAnalysis")

        inputs = {
            "batch_id": "BATCH-STEPS",
            "sample_ids": ["HIV-S1", "HIV-S2", "HIV-S3"],
            "fastq_dir": "/data/samples",
            "reference": "HXB2",
        }

        result = run_to_completion(
            evaluator,
            poller,
            workflow,
            compiled_program,
            inputs=inputs,
            max_rounds=100,
        )

        assert result.status == ExecutionStatus.COMPLETED

        steps = memory_store.get_steps_by_workflow(result.workflow_id)
        # Root + foreach block + 3 sub-blocks + 3 AnalyzeSample calls
        # + each AnalyzeSample's blocks and inner steps ≈ 20+
        assert len(steps) >= 20

    def test_batch_mixed_qc_outcomes(self, compiled_program, evaluator, poller):
        """Batch with 1 forced QC failure and 2 passes completes without error."""
        self._register_handlers(poller)

        # Override QC to fail for one specific sample
        original_qc = poller._handlers.get("hiv.Sequencing.AssessQuality")

        def handle_selective_qc_fail(params):
            if params.get("sample_id") == "HIV-FAIL-001":
                return {
                    "passed": False,
                    "total_reads": 500,
                    "mean_quality": 12.0,
                    "coverage_depth": 5,
                    "message": "Severely degraded sample",
                }
            return original_qc(params)

        poller.register("hiv.Sequencing.AssessQuality", handle_selective_qc_fail)

        workflow = extract_workflow(compiled_program, "BatchAnalysis")

        inputs = {
            "batch_id": "BATCH-MIXED",
            "sample_ids": ["HIV-PASS-001", "HIV-FAIL-001", "HIV-PASS-002"],
            "fastq_dir": "/data/clinical/mixed",
            "reference": "HXB2",
        }

        result = run_to_completion(
            evaluator,
            poller,
            workflow,
            compiled_program,
            inputs=inputs,
            max_rounds=100,
        )

        # Batch completes even with per-sample QC failures
        assert result.status == ExecutionStatus.COMPLETED

    def test_batch_single_sample(self, compiled_program, evaluator, poller):
        """BatchAnalysis with a single sample works (degenerate foreach)."""
        self._register_handlers(poller)
        workflow = extract_workflow(compiled_program, "BatchAnalysis")

        inputs = {
            "batch_id": "BATCH-SINGLE",
            "sample_ids": ["HIV-SOLO-001"],
            "fastq_dir": "/data/solo",
            "reference": "HXB2",
        }

        result = run_to_completion(
            evaluator,
            poller,
            workflow,
            compiled_program,
            inputs=inputs,
            max_rounds=100,
        )

        assert result.status == ExecutionStatus.COMPLETED

    def test_batch_5_samples_real_ids(
        self, hxb2_fasta, compiled_program, evaluator, poller, memory_store
    ):
        """Batch of 5 samples with realistic patient IDs and HXB2 reference path."""
        self._register_handlers(poller)
        workflow = extract_workflow(compiled_program, "BatchAnalysis")

        inputs = {
            "batch_id": "CLINIC-2026-03-01",
            "sample_ids": [
                "PT-9281-A",
                "PT-4517-B",
                "PT-7033-C",
                "PT-1156-D",
                "PT-8842-E",
            ],
            "fastq_dir": str(hxb2_fasta).rsplit("/", 1)[0],
            "reference": "HXB2",
        }

        result = run_to_completion(
            evaluator,
            poller,
            workflow,
            compiled_program,
            inputs=inputs,
            max_rounds=200,
        )

        assert result.status == ExecutionStatus.COMPLETED

        steps = memory_store.get_steps_by_workflow(result.workflow_id)
        # 5 samples × ~7 steps each + root + foreach + sub-blocks ≈ 35+
        assert len(steps) >= 35


# ============================================================================
# Synthetic FASTQ pipeline tests — real file parsing, no network
# ============================================================================


class TestSyntheticFastqPipeline:
    """Run pipelines with synthetic FASTQ files that exercise real QC parsing.

    The synthetic files are generated by ``generate_synthetic_fastq()`` and
    parsed by ``parse_fastq_quality()`` inside ``assess_read_quality()``.
    Downstream steps (alignment, variant calling, etc.) still use hash-based
    mocks since they'd require bwa/samtools.
    """

    @staticmethod
    def _register_handlers(poller):
        from handlers import register_all_handlers

        register_all_handlers(poller)

    def test_high_quality_sample_passes_qc(
        self, synthetic_fastq_dir, compiled_program, evaluator, poller
    ):
        """High-quality synthetic FASTQ (Q35, 5000 reads) passes QC → full pipeline."""
        self._register_handlers(poller)
        workflow = extract_workflow(compiled_program, "AnalyzeSample")

        fastq_path = os.path.join(synthetic_fastq_dir, "HIV-SRA-001.fastq.gz")
        result = run_to_completion(
            evaluator,
            poller,
            workflow,
            compiled_program,
            inputs={
                "sample_id": "HIV-SRA-001",
                "fastq_path": fastq_path,
                "reference": "HXB2",
                "clinical_context": "",
            },
            max_rounds=50,
        )

        assert result.status == ExecutionStatus.COMPLETED
        assert result.outputs["status"] == "completed"

    def test_low_quality_sample_fails_qc(
        self, synthetic_fastq_dir, compiled_program, evaluator, poller
    ):
        """Low-quality synthetic FASTQ (Q18, 500 reads) fails QC → when-fail branch."""
        self._register_handlers(poller)
        workflow = extract_workflow(compiled_program, "AnalyzeSample")

        fastq_path = os.path.join(synthetic_fastq_dir, "HIV-SRA-002.fastq.gz")
        result = run_to_completion(
            evaluator,
            poller,
            workflow,
            compiled_program,
            inputs={
                "sample_id": "HIV-SRA-002",
                "fastq_path": fastq_path,
                "reference": "HXB2",
                "clinical_context": "",
            },
            max_rounds=50,
        )

        assert result.status == ExecutionStatus.COMPLETED
        assert result.outputs["status"] == "qc_failed"

    def test_qc_parsed_real_read_count(self, synthetic_fastq_dir):
        """QC step parses actual read count from synthetic FASTQ (5000, not hash-derived)."""
        from handlers.shared.resistance_utils import assess_read_quality

        fastq_path = os.path.join(synthetic_fastq_dir, "HIV-SRA-001.fastq.gz")
        result = assess_read_quality("HIV-SRA-001", fastq_path)
        assert result["total_reads"] == 5000

    def test_batch_mixed_quality_synthetic(
        self, synthetic_fastq_dir, compiled_program, evaluator, poller
    ):
        """BatchAnalysis with 3 synthetic samples (mixed quality) completes."""
        self._register_handlers(poller)
        workflow = extract_workflow(compiled_program, "BatchAnalysis")

        result = run_to_completion(
            evaluator,
            poller,
            workflow,
            compiled_program,
            inputs={
                "batch_id": "BATCH-SYNTH-MIX",
                "sample_ids": ["HIV-SRA-001", "HIV-SRA-002", "HIV-SRA-003"],
                "fastq_dir": synthetic_fastq_dir,
                "reference": "HXB2",
            },
            max_rounds=150,
        )

        assert result.status == ExecutionStatus.COMPLETED

    def test_batch_all_high_quality_synthetic(
        self, synthetic_fastq_dir, compiled_program, evaluator, poller
    ):
        """BatchAnalysis with only high-quality samples → all pass QC."""
        self._register_handlers(poller)
        workflow = extract_workflow(compiled_program, "BatchAnalysis")

        result = run_to_completion(
            evaluator,
            poller,
            workflow,
            compiled_program,
            inputs={
                "batch_id": "BATCH-SYNTH-HQ",
                "sample_ids": ["HIV-SRA-001", "HIV-SRA-003"],
                "fastq_dir": synthetic_fastq_dir,
                "reference": "HXB2",
            },
            max_rounds=100,
        )

        assert result.status == ExecutionStatus.COMPLETED


# ============================================================================
# Real SRA data tests — require --sra flag and network
# ============================================================================


class TestRealSRAData:
    """Tests using real FASTQ data from the Sequence Read Archive.

    Gated by ``--sra`` flag. Downloads SRR8806312 (~13 MB) from ENA.
    """

    def test_parse_real_sra_fastq(self, sra_fastq_path):
        """Parse real SRA FASTQ and verify plausible metrics."""
        from handlers.shared.resistance_utils import parse_fastq_quality

        result = parse_fastq_quality(sra_fastq_path)
        # Real FASTQ should have meaningful data
        assert result["total_reads"] > 100
        assert result["mean_quality"] > 10.0
        assert result["total_bases"] > 10000
        assert result["mean_read_length"] > 50

    def test_analyze_sample_with_sra(self, sra_fastq_path, compiled_program, evaluator, poller):
        """Full AnalyzeSample pipeline with real SRA data."""
        from handlers import register_all_handlers

        register_all_handlers(poller)
        workflow = extract_workflow(compiled_program, "AnalyzeSample")

        result = run_to_completion(
            evaluator,
            poller,
            workflow,
            compiled_program,
            inputs={
                "sample_id": "SRR8806312",
                "fastq_path": sra_fastq_path,
                "reference": "HXB2",
                "clinical_context": "SRA integration test",
            },
            max_rounds=50,
        )

        assert result.status == ExecutionStatus.COMPLETED
        assert result.outputs["status"] in ("completed", "qc_failed")

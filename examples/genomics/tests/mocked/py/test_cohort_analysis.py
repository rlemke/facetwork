#!/usr/bin/env python3
"""Example: Genomics cohort analysis pipeline.

Demonstrates two FFL workflow patterns:
  Phase 1 — SamplePipeline (andThen foreach fan-out):
    For each of 4 samples: QcReads -> AlignReads -> CallVariants
  Phase 2 — CohortAnalysis (linear andThen fan-in):
    IngestReference -> JointGenotype -> NormalizeFilter -> Annotate
    -> CohortAnalytics -> Publish

Uses mock handlers with realistic 1000 Genomes Project sample data.
No external dependencies. Run from the repo root:

    PYTHONPATH=. python examples/genomics/tests/mocked/py/test_cohort_analysis.py
"""

from facetwork import emit_dict, parse
from facetwork.runtime import Evaluator, ExecutionStatus, MemoryStore, Telemetry

# ---------------------------------------------------------------------------
# Program AST — declares event facets the runtime needs to recognise.
# ---------------------------------------------------------------------------


def _ef(name: str, params: list[dict], returns: list[dict]) -> dict:
    """Shorthand for an EventFacetDecl node."""
    return {"type": "EventFacetDecl", "name": name, "params": params, "returns": returns}


PROGRAM_AST = {
    "type": "Program",
    "declarations": [
        {
            "type": "Namespace",
            "name": "genomics",
            "declarations": [
                {
                    "type": "Namespace",
                    "name": "Facets",
                    "declarations": [
                        _ef(
                            "IngestReference",
                            [{"name": "reference_build", "type": "String"}],
                            [{"name": "result", "type": "ReferenceBundle"}],
                        ),
                        _ef(
                            "QcReads",
                            [
                                {"name": "sample_id", "type": "String"},
                                {"name": "r1_uri", "type": "String"},
                                {"name": "r2_uri", "type": "String"},
                            ],
                            [{"name": "result", "type": "QcReport"}],
                        ),
                        _ef(
                            "AlignReads",
                            [
                                {"name": "sample_id", "type": "String"},
                                {"name": "clean_fastq_path", "type": "String"},
                                {"name": "reference_build", "type": "String"},
                            ],
                            [{"name": "result", "type": "AlignmentResult"}],
                        ),
                        _ef(
                            "CallVariants",
                            [
                                {"name": "sample_id", "type": "String"},
                                {"name": "bam_path", "type": "String"},
                                {"name": "reference_build", "type": "String"},
                            ],
                            [{"name": "result", "type": "VariantResult"}],
                        ),
                        _ef(
                            "JointGenotype",
                            [
                                {"name": "gvcf_dir", "type": "String"},
                                {"name": "reference_build", "type": "String"},
                                {"name": "sample_count", "type": "Long"},
                            ],
                            [{"name": "result", "type": "CohortVariantResult"}],
                        ),
                        _ef(
                            "NormalizeFilter",
                            [
                                {"name": "vcf_path", "type": "String"},
                                {"name": "reference_build", "type": "String"},
                            ],
                            [{"name": "result", "type": "CohortVariantResult"}],
                        ),
                        _ef(
                            "Annotate",
                            [
                                {"name": "vcf_path", "type": "String"},
                                {"name": "annotation_path", "type": "String"},
                            ],
                            [{"name": "result", "type": "AnnotationResult"}],
                        ),
                        _ef(
                            "CohortAnalytics",
                            [
                                {"name": "variant_table_path", "type": "String"},
                                {"name": "dataset_id", "type": "String"},
                            ],
                            [{"name": "result", "type": "CohortStatsResult"}],
                        ),
                        _ef(
                            "Publish",
                            [
                                {"name": "variant_table_path", "type": "String"},
                                {"name": "qc_report_path", "type": "String"},
                                {"name": "stats_path", "type": "String"},
                                {"name": "dataset_id", "type": "String"},
                            ],
                            [{"name": "result", "type": "AnalysisPackage"}],
                        ),
                    ],
                },
            ],
        },
    ],
}


# ---------------------------------------------------------------------------
# Workflow FFL sources
# ---------------------------------------------------------------------------

SAMPLE_PIPELINE_AFL = """\
namespace genomics.pipeline {
    workflow SamplePipeline(
        samples: Json,
        reference_build: String = "GRCh38"
    ) => (gvcf_path: String, sample_id: String, variant_count: Long) andThen foreach sample in $.samples {
        qc = QcReads(
            sample_id = $.sample.sample_id,
            r1_uri = $.sample.r1_uri,
            r2_uri = $.sample.r2_uri
        )
        aligned = AlignReads(
            sample_id = qc.result.sample_id,
            clean_fastq_path = qc.result.clean_fastq_path,
            reference_build = $.reference_build
        )
        called = CallVariants(
            sample_id = aligned.result.sample_id,
            bam_path = aligned.result.bam_path,
            reference_build = $.reference_build
        )
        yield SamplePipeline(
            gvcf_path = called.result.gvcf_path,
            sample_id = called.result.sample_id,
            variant_count = called.result.variant_count
        )
    }
}
"""

COHORT_ANALYSIS_AFL = """\
namespace genomics.pipeline {
    workflow CohortAnalysis(
        dataset_id: String,
        reference_build: String = "GRCh38",
        gvcf_dir: String
    ) => (package_path: String, cohort_vcf_path: String,
          variant_table_path: String, total_variants: Long,
          sample_count: Long) andThen {
        ref = IngestReference(reference_build = $.reference_build)
        joint = JointGenotype(
            gvcf_dir = $.gvcf_dir,
            reference_build = $.reference_build,
            sample_count = 4
        )
        norm = NormalizeFilter(
            vcf_path = joint.result.cohort_vcf_path,
            reference_build = $.reference_build
        )
        annotated = Annotate(
            vcf_path = norm.result.filtered_vcf_path,
            annotation_path = ref.result.annotation_path
        )
        stats = CohortAnalytics(
            variant_table_path = annotated.result.variant_table_path,
            dataset_id = $.dataset_id
        )
        published = Publish(
            variant_table_path = annotated.result.variant_table_path,
            qc_report_path = stats.result.qc_report_path,
            stats_path = stats.result.stats_path,
            dataset_id = $.dataset_id
        )
        yield CohortAnalysis(
            package_path = published.result.package_path,
            cohort_vcf_path = joint.result.cohort_vcf_path,
            variant_table_path = annotated.result.variant_table_path,
            total_variants = annotated.result.variant_count,
            sample_count = joint.result.sample_count
        )
    }
}
"""


def compile_workflow(afl_source: str, workflow_name: str) -> dict:
    """Compile an FFL workflow to a runtime AST dict."""
    tree = parse(afl_source)
    program = emit_dict(tree)
    for ns in program.get("namespaces", []):
        for wf in ns.get("workflows", []):
            if wf["name"] == workflow_name:
                return wf
    raise RuntimeError(f"Workflow '{workflow_name}' not found in compiled output")


# ---------------------------------------------------------------------------
# Mock data — 4 samples from the 1000 Genomes Project (CEU + GBR)
# ---------------------------------------------------------------------------

SAMPLES = [
    {
        "sample_id": "NA12878",
        "r1_uri": "s3://1000g/NA12878/NA12878_R1.fq.gz",
        "r2_uri": "s3://1000g/NA12878/NA12878_R2.fq.gz",
        "total_reads": 1_100_000_000,
        "pass_rate": 97,
        "mapping_rate": 99,
        "coverage": 32,
        "snp_count": 4_100_000,
        "indel_count": 720_000,
        "variant_count": 4_820_000,
    },
    {
        "sample_id": "NA12891",
        "r1_uri": "s3://1000g/NA12891/NA12891_R1.fq.gz",
        "r2_uri": "s3://1000g/NA12891/NA12891_R2.fq.gz",
        "total_reads": 980_000_000,
        "pass_rate": 96,
        "mapping_rate": 99,
        "coverage": 30,
        "snp_count": 3_900_000,
        "indel_count": 680_000,
        "variant_count": 4_580_000,
    },
    {
        "sample_id": "NA12892",
        "r1_uri": "s3://1000g/NA12892/NA12892_R1.fq.gz",
        "r2_uri": "s3://1000g/NA12892/NA12892_R2.fq.gz",
        "total_reads": 1_050_000_000,
        "pass_rate": 97,
        "mapping_rate": 99,
        "coverage": 31,
        "snp_count": 4_000_000,
        "indel_count": 700_000,
        "variant_count": 4_700_000,
    },
    {
        "sample_id": "HG00096",
        "r1_uri": "s3://1000g/HG00096/HG00096_R1.fq.gz",
        "r2_uri": "s3://1000g/HG00096/HG00096_R2.fq.gz",
        "total_reads": 890_000_000,
        "pass_rate": 96,
        "mapping_rate": 99,
        "coverage": 28,
        "snp_count": 3_800_000,
        "indel_count": 660_000,
        "variant_count": 4_460_000,
    },
]

# Per-sample mock handler results keyed by sample_id
SAMPLE_MOCK_DATA = {s["sample_id"]: s for s in SAMPLES}


# ---------------------------------------------------------------------------
# Mock handlers
# ---------------------------------------------------------------------------


def _mock_qc_reads(params: dict) -> dict:
    sid = params.get("sample_id", "unknown")
    data = SAMPLE_MOCK_DATA.get(sid, SAMPLES[0])
    passed = int(data["total_reads"] * data["pass_rate"] / 100)
    return {
        "result": {
            "sample_id": sid,
            "total_reads": data["total_reads"],
            "passed_reads": passed,
            "failed_reads": data["total_reads"] - passed,
            "pass_rate": data["pass_rate"],
            "clean_fastq_path": f"/fastq/{sid}/{sid}_clean.fq.gz",
            "tool_version": "fastp-0.23.4",
        },
    }


def _mock_align_reads(params: dict) -> dict:
    sid = params.get("sample_id", "unknown")
    data = SAMPLE_MOCK_DATA.get(sid, SAMPLES[0])
    total = int(data["total_reads"] * data["pass_rate"] / 100)
    mapped = int(total * data["mapping_rate"] / 100)
    return {
        "result": {
            "sample_id": sid,
            "bam_path": f"/bam/{sid}/{sid}.sorted.bam",
            "total_reads": total,
            "mapped_reads": mapped,
            "mapping_rate": data["mapping_rate"],
            "duplicate_rate": 8,
            "mean_coverage": data["coverage"],
        },
    }


def _mock_call_variants(params: dict) -> dict:
    sid = params.get("sample_id", "unknown")
    data = SAMPLE_MOCK_DATA.get(sid, SAMPLES[0])
    return {
        "result": {
            "sample_id": sid,
            "gvcf_path": f"/gvcf/{sid}/{sid}.g.vcf.gz",
            "variant_count": data["variant_count"],
            "snp_count": data["snp_count"],
            "indel_count": data["indel_count"],
        },
    }


MOCK_HANDLERS = {
    "IngestReference": lambda p: {
        "result": {
            "fasta_path": f"/ref/{p.get('reference_build', 'GRCh38')}/{p.get('reference_build', 'GRCh38')}.fa",
            "annotation_path": f"/ref/{p.get('reference_build', 'GRCh38')}/annotation.gtf",
            "build": p.get("reference_build", "GRCh38"),
            "size_bytes": 3_200_000_000,
        },
    },
    "QcReads": _mock_qc_reads,
    "AlignReads": _mock_align_reads,
    "CallVariants": _mock_call_variants,
    "JointGenotype": lambda p: {
        "result": {
            "cohort_vcf_path": "/vcf/cohort/joint_genotyped.vcf.gz",
            "filtered_vcf_path": "/vcf/cohort/joint_genotyped.vcf.gz",
            "sample_count": p.get("sample_count", 4),
            "variant_count": 6_500_000,
            "pass_rate": 89,
        },
    },
    "NormalizeFilter": lambda p: {
        "result": {
            "cohort_vcf_path": p.get("vcf_path", ""),
            "filtered_vcf_path": "/vcf/cohort/filtered.vcf.gz",
            "sample_count": 4,
            "variant_count": 5_800_000,
            "pass_rate": 89,
        },
    },
    "Annotate": lambda p: {
        "result": {
            "variant_table_path": "/annotation/cohort/variant_table.tsv",
            "variant_count": 5_800_000,
            "annotated_count": 5_600_000,
            "gene_count": 20_100,
        },
    },
    "CohortAnalytics": lambda p: {
        "result": {
            "qc_report_path": f"/reports/{p.get('dataset_id', 'unknown')}/qc_report.html",
            "stats_path": f"/reports/{p.get('dataset_id', 'unknown')}/cohort_stats.json",
            "sample_count": 4,
            "mean_depth": 30,
            "variant_count": 5_800_000,
        },
    },
    "Publish": lambda p: {
        "result": {
            "package_path": f"/publish/{p.get('dataset_id', 'unknown')}/package.tar.gz",
            "manifest_path": f"/publish/{p.get('dataset_id', 'unknown')}/manifest.json",
            "dataset_id": p.get("dataset_id", "unknown"),
            "sample_count": 4,
            "variant_count": 5_800_000,
            "build": "GRCh38",
        },
    },
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def find_event_blocked_steps(store: MemoryStore, workflow_id: str) -> list[tuple[str, str]]:
    """Find all steps blocked waiting for an event handler.

    Returns list of (step_id, short_facet_name).
    """
    results = []
    for step in store._steps.values():
        if step.workflow_id == workflow_id and step.state == "state.EventTransmit":
            short = (
                step.facet_name.rsplit(".", 1)[-1] if "." in step.facet_name else step.facet_name
            )
            results.append((step.id, short))
    return results


# ---------------------------------------------------------------------------
# Phase 1: SamplePipeline (foreach fan-out)
# ---------------------------------------------------------------------------


def run_sample_pipeline() -> dict:
    """Run per-sample processing for 4 samples, return summary."""
    print("=" * 70)
    print("PHASE 1: SamplePipeline (foreach fan-out)")
    print("=" * 70)

    print("\nCompiling SamplePipeline from FFL source...")
    workflow_ast = compile_workflow(SAMPLE_PIPELINE_AFL, "SamplePipeline")
    print("  OK\n")

    store = MemoryStore()
    evaluator = Evaluator(persistence=store, telemetry=Telemetry(enabled=False))

    sample_inputs = [
        {"sample_id": s["sample_id"], "r1_uri": s["r1_uri"], "r2_uri": s["r2_uri"]} for s in SAMPLES
    ]

    print(f"Executing: SamplePipeline with {len(SAMPLES)} samples")
    print("  Per-sample: QcReads -> AlignReads -> CallVariants\n")

    result = evaluator.execute(
        workflow_ast,
        inputs={"samples": sample_inputs, "reference_build": "GRCh38"},
        program_ast=PROGRAM_AST,
    )
    assert result.status == ExecutionStatus.PAUSED, f"Expected PAUSED, got {result.status}"

    # Process event steps — foreach creates parallel sub-blocks, each with
    # 3 sequential event steps. We resolve one batch at a time.
    step_num = 0
    samples_seen = set()

    while True:
        blocked = find_event_blocked_steps(store, result.workflow_id)
        if not blocked:
            break

        for step_id, facet_short in blocked:
            step_num += 1
            handler = MOCK_HANDLERS.get(facet_short)
            assert handler, f"No mock handler for '{facet_short}'"

            step = store.get_step(step_id)
            params = {k: v.value for k, v in step.attributes.params.items()}
            sid = params.get("sample_id", "?")
            samples_seen.add(sid)
            print(f"  Step {step_num:>2}: {facet_short:<16} (sample={sid})")

            handler_result = handler(params)
            evaluator.continue_step(step_id, handler_result)

        result = evaluator.resume(result.workflow_id, workflow_ast, PROGRAM_AST)

    assert result.status == ExecutionStatus.COMPLETED, f"Expected COMPLETED, got {result.status}"
    assert result.success

    # Summary table
    print(
        f"\n  {'Sample':<10} {'Reads':>12} {'Pass%':>6} {'Map%':>6} {'Cov':>5} {'Variants':>12} {'SNPs':>10} {'Indels':>10}"
    )
    print(f"  {'-' * 10} {'-' * 12} {'-' * 6} {'-' * 6} {'-' * 5} {'-' * 12} {'-' * 10} {'-' * 10}")
    for s in SAMPLES:
        print(
            f"  {s['sample_id']:<10} {s['total_reads']:>12,} {s['pass_rate']:>5}% "
            f"{s['mapping_rate']:>5}% {s['coverage']:>4}x {s['variant_count']:>12,} "
            f"{s['snp_count']:>10,} {s['indel_count']:>10,}"
        )

    assert len(samples_seen) == 4, f"Expected 4 samples, saw {len(samples_seen)}"
    print(f"\nPhase 1 complete: {step_num} event steps across {len(samples_seen)} samples")

    return {
        "step_count": step_num,
        "sample_count": len(samples_seen),
        "gvcf_dir": "/gvcf/cohort/",
    }


# ---------------------------------------------------------------------------
# Phase 2: CohortAnalysis (linear fan-in)
# ---------------------------------------------------------------------------


def run_cohort_analysis(gvcf_dir: str) -> None:
    """Run cohort-level joint analysis pipeline."""
    print(f"\n{'=' * 70}")
    print("PHASE 2: CohortAnalysis (linear fan-in)")
    print("=" * 70)

    print("\nCompiling CohortAnalysis from FFL source...")
    workflow_ast = compile_workflow(COHORT_ANALYSIS_AFL, "CohortAnalysis")
    print("  OK\n")

    store = MemoryStore()
    evaluator = Evaluator(persistence=store, telemetry=Telemetry(enabled=False))

    dataset_id = "1000G_CEU_GBR_2026Q1"

    print(f'Executing: CohortAnalysis(dataset_id="{dataset_id}")')
    print(
        "  Pipeline: IngestReference -> JointGenotype -> NormalizeFilter "
        "-> Annotate -> CohortAnalytics -> Publish\n"
    )

    result = evaluator.execute(
        workflow_ast,
        inputs={
            "dataset_id": dataset_id,
            "reference_build": "GRCh38",
            "gvcf_dir": gvcf_dir,
        },
        program_ast=PROGRAM_AST,
    )
    assert result.status == ExecutionStatus.PAUSED, f"Expected PAUSED, got {result.status}"

    # Process 6 linear event steps
    step_num = 0
    while True:
        blocked = find_event_blocked_steps(store, result.workflow_id)
        if not blocked:
            break

        for step_id, facet_short in blocked:
            step_num += 1
            handler = MOCK_HANDLERS.get(facet_short)
            assert handler, f"No mock handler for '{facet_short}'"

            step = store.get_step(step_id)
            params = {k: v.value for k, v in step.attributes.params.items()}
            print(f"  Step {step_num}: {facet_short}")

            handler_result = handler(params)
            evaluator.continue_step(step_id, handler_result)

        result = evaluator.resume(result.workflow_id, workflow_ast, PROGRAM_AST)

    assert result.status == ExecutionStatus.COMPLETED, f"Expected COMPLETED, got {result.status}"
    assert result.success

    outputs = result.outputs

    print(f"\n{'-' * 70}")
    print("COHORT RESULTS")
    print(f"{'-' * 70}")
    print(f"  Dataset:            {dataset_id}")
    print("  Reference build:    GRCh38")
    print(f"  Samples:            {outputs.get('sample_count')}")
    print(f"  Cohort VCF:         {outputs.get('cohort_vcf_path')}")
    print(f"  Variant table:      {outputs.get('variant_table_path')}")
    print(f"  Total variants:     {outputs.get('total_variants'):,}")
    print(f"  Analysis package:   {outputs.get('package_path')}")

    # Assertions
    assert outputs["package_path"] == f"/publish/{dataset_id}/package.tar.gz"
    assert outputs["cohort_vcf_path"] == "/vcf/cohort/joint_genotyped.vcf.gz"
    assert outputs["variant_table_path"] == "/annotation/cohort/variant_table.tsv"
    assert outputs["total_variants"] == 5_800_000
    assert outputs["sample_count"] == 4

    print(f"\nPhase 2 complete: {step_num} event steps processed")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    """Run both pipeline phases end-to-end."""
    phase1 = run_sample_pipeline()
    run_cohort_analysis(phase1["gvcf_dir"])

    print(f"\n{'=' * 70}")
    print("ALL ASSERTIONS PASSED")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Example: Genomics cache/download pipeline.

Demonstrates the cache layer added alongside the existing genomics pipeline:
  Phase 1 — Reference cache + index: resolve "hg38" -> GenomicsCache -> BWA index
  Phase 2 — Annotation + SRA cache: resolve "dbsnp" + SRA accession
  Phase 3 — CachedCohortAnalysis: full end-to-end with resolve -> index -> analyze

Uses mock handlers with realistic genomics resource metadata.
No external dependencies. Run from the repo root:

    PYTHONPATH=. python examples/genomics/tests/mocked/py/test_cache_pipeline.py
"""

from facetwork import emit_dict, parse
from facetwork.runtime import Evaluator, ExecutionStatus, MemoryStore, Telemetry
from examples.genomics.handlers.operations_handlers import download, index
from examples.genomics.handlers.resolve_handlers import (
    resolve_annotation,
    resolve_reference,
    resolve_sample,
)

# ---------------------------------------------------------------------------
# Program AST — declares event facets the runtime needs to recognise.
# ---------------------------------------------------------------------------


def _ef(name: str, params: list[dict], returns: list[dict]) -> dict:
    """Shorthand for an EventFacetDecl node."""
    return {"type": "EventFacetDecl", "name": name, "params": params, "returns": returns}


# Event facet declarations for the cache layer + original pipeline facets
PROGRAM_AST = {
    "type": "Program",
    "declarations": [
        {
            "type": "Namespace",
            "name": "genomics",
            "declarations": [
                {
                    "type": "Namespace",
                    "name": "cache",
                    "declarations": [
                        {
                            "type": "Namespace",
                            "name": "Resolve",
                            "declarations": [
                                _ef(
                                    "ResolveReference",
                                    [
                                        {"name": "name", "type": "String"},
                                        {"name": "prefer_source", "type": "String"},
                                    ],
                                    [
                                        {"name": "cache", "type": "GenomicsCache"},
                                        {"name": "resolution", "type": "ResourceResolution"},
                                    ],
                                ),
                                _ef(
                                    "ResolveAnnotation",
                                    [
                                        {"name": "name", "type": "String"},
                                        {"name": "version", "type": "String"},
                                    ],
                                    [
                                        {"name": "cache", "type": "GenomicsCache"},
                                        {"name": "resolution", "type": "ResourceResolution"},
                                    ],
                                ),
                                _ef(
                                    "ResolveSample",
                                    [{"name": "accession", "type": "String"}],
                                    [
                                        {"name": "cache", "type": "GenomicsCache"},
                                        {"name": "resolution", "type": "ResourceResolution"},
                                    ],
                                ),
                            ],
                        },
                        {
                            "type": "Namespace",
                            "name": "Operations",
                            "declarations": [
                                _ef(
                                    "Download",
                                    [{"name": "cache", "type": "GenomicsCache"}],
                                    [{"name": "cache", "type": "GenomicsCache"}],
                                ),
                                _ef(
                                    "Index",
                                    [
                                        {"name": "cache", "type": "GenomicsCache"},
                                        {"name": "aligner", "type": "String"},
                                    ],
                                    [{"name": "cache", "type": "IndexCache"}],
                                ),
                            ],
                        },
                    ],
                },
                # Original pipeline facets needed by CachedCohortAnalysis
                {
                    "type": "Namespace",
                    "name": "Facets",
                    "declarations": [
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
# Mock handlers for all event facets used in the test
# ---------------------------------------------------------------------------

MOCK_HANDLERS = {
    "ResolveReference": resolve_reference,
    "ResolveAnnotation": resolve_annotation,
    "ResolveSample": resolve_sample,
    "Download": download,
    "Index": index,
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
# Workflow FFL sources
# ---------------------------------------------------------------------------

PREPARE_REFERENCE_AFL = """\
namespace genomics.cache.workflows {
    workflow PrepareReference(
        reference_name: String,
        aligner: String = "bwa"
    ) => (cache: Json, index: Json) andThen {
        resolved = ResolveReference(name = $.reference_name)
        indexed = Index(cache = resolved.cache, aligner = $.aligner)
        yield PrepareReference(
            cache = resolved.cache,
            index = indexed.cache
        )
    }
}
"""

CACHED_COHORT_AFL = """\
namespace genomics.cache.workflows {
    workflow CachedCohortAnalysis(
        dataset_id: String,
        reference_name: String = "hg38",
        accessions: Json
    ) => (package_path: String, variant_count: Long,
          sample_count: Long) andThen {
        ref = ResolveReference(name = $.reference_name)
        annot = ResolveAnnotation(name = "dbsnp")
        indexed = Index(cache = ref.cache, aligner = "bwa")
        joint = JointGenotype(
            gvcf_dir = "/gvcf/cohort/",
            reference_build = ref.resolution.matched_name,
            sample_count = 4
        )
        norm = NormalizeFilter(
            vcf_path = joint.result.cohort_vcf_path,
            reference_build = ref.resolution.matched_name
        )
        annotated = Annotate(
            vcf_path = norm.result.filtered_vcf_path,
            annotation_path = annot.cache.path
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
        yield CachedCohortAnalysis(
            package_path = published.result.package_path,
            variant_count = annotated.result.variant_count,
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
        # Check nested namespaces
        for sub_ns in ns.get("namespaces", []):
            for wf in sub_ns.get("workflows", []):
                if wf["name"] == workflow_name:
                    return wf
    raise RuntimeError(f"Workflow '{workflow_name}' not found in compiled output")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def find_event_blocked_steps(store: MemoryStore, workflow_id: str) -> list[tuple[str, str]]:
    """Find steps blocked waiting for an event handler."""
    results = []
    for step in store._steps.values():
        if step.workflow_id == workflow_id and step.state == "state.EventTransmit":
            short = (
                step.facet_name.rsplit(".", 1)[-1] if "." in step.facet_name else step.facet_name
            )
            results.append((step.id, short))
    return results


def _fmt_size(size_bytes: int) -> str:
    """Format byte size to human readable."""
    if size_bytes >= 1_000_000_000:
        return f"{size_bytes / 1_000_000_000:.1f} GB"
    if size_bytes >= 1_000_000:
        return f"{size_bytes / 1_000_000:.1f} MB"
    return f"{size_bytes:,} B"


# ---------------------------------------------------------------------------
# Phase 1: Reference cache + BWA index
# ---------------------------------------------------------------------------


def run_reference_cache() -> None:
    """Resolve hg38, build BWA index, verify cache metadata."""
    print("=" * 70)
    print("PHASE 1: Reference Cache + Index")
    print("=" * 70)

    print("\nCompiling PrepareReference from FFL source...")
    workflow_ast = compile_workflow(PREPARE_REFERENCE_AFL, "PrepareReference")
    print("  OK\n")

    store = MemoryStore()
    evaluator = Evaluator(persistence=store, telemetry=Telemetry(enabled=False))

    print('Executing: PrepareReference(reference_name="hg38", aligner="bwa")')
    print("  Pipeline: ResolveReference -> Index\n")

    result = evaluator.execute(
        workflow_ast,
        inputs={"reference_name": "hg38", "aligner": "bwa"},
        program_ast=PROGRAM_AST,
    )
    assert result.status == ExecutionStatus.PAUSED, f"Expected PAUSED, got {result.status}"

    step_num = 0
    resources = []
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

            # Track for summary
            if "cache" in handler_result:
                c = handler_result["cache"]
                resources.append(
                    (
                        facet_short,
                        c.get("resource_type", c.get("aligner", "index")),
                        c.get("size", 0),
                        c.get("wasInCache", False),
                    )
                )

        result = evaluator.resume(result.workflow_id, workflow_ast, PROGRAM_AST)

    assert result.status == ExecutionStatus.COMPLETED, f"Expected COMPLETED, got {result.status}"
    assert result.success

    outputs = result.outputs
    cache = outputs.get("cache", {})
    idx = outputs.get("index", {})

    print(f"\n{'-' * 70}")
    print("REFERENCE CACHE RESULTS")
    print(f"{'-' * 70}")
    print("  Reference:      GRCh38")
    print(f"  Cache path:     {cache.get('path', '')}")
    print(f"  Cache size:     {_fmt_size(cache.get('size', 0))}")
    print(f"  Was in cache:   {cache.get('wasInCache', False)}")
    print(f"  Index dir:      {idx.get('index_dir', '')}")
    print(f"  Aligner:        {idx.get('aligner', '')}")
    print(f"  Contigs:        {idx.get('contig_count', 0)}")

    # Assertions
    assert cache["wasInCache"] is True
    assert "GRCh38" in cache.get("path", "")
    assert cache["resource_type"] == "reference"
    assert idx["aligner"] == "bwa"

    # Summary table
    if resources:
        print(f"\n  {'Resource':<20} {'Type':<12} {'Size':>12} {'Cached':>8}")
        print(f"  {'-' * 20} {'-' * 12} {'-' * 12} {'-' * 8}")
        for name, rtype, size, cached in resources:
            print(f"  {name:<20} {rtype:<12} {_fmt_size(size):>12} {'Yes' if cached else 'No':>8}")

    print(f"\nPhase 1 complete: {step_num} event steps processed")


# ---------------------------------------------------------------------------
# Phase 2: Annotation + SRA cache
# ---------------------------------------------------------------------------


def run_annotation_sra_cache() -> None:
    """Resolve dbsnp annotation and SRA accession, verify metadata."""
    print(f"\n{'=' * 70}")
    print("PHASE 2: Annotation + SRA Cache")
    print("=" * 70)

    # Resolve annotation
    print("\n  Resolving annotation: 'dbsnp'")
    annot_result = resolve_annotation({"name": "dbsnp"})
    annot_cache = annot_result["cache"]
    annot_res = annot_result["resolution"]

    print(f"    Matched:    {annot_res['matched_name']}")
    print(f"    URL:        {annot_cache['url'][:60]}...")
    print(f"    Path:       {annot_cache['path']}")
    print(f"    Size:       {_fmt_size(annot_cache['size'])}")
    print(f"    Type:       {annot_cache['resource_type']}")
    print(f"    Ambiguous:  {annot_res['is_ambiguous']}")

    assert annot_cache["url"].startswith("https://")
    assert annot_cache["path"] == "/cache/annotation/dbsnp156/dbsnp156.vcf.gz"
    assert annot_cache["resource_type"] == "annotation"
    assert not annot_res["is_ambiguous"]

    # Resolve SRA sample
    print("\n  Resolving SRA accession: 'SRR622461'")
    sra_result = resolve_sample({"accession": "SRR622461"})
    sra_cache = sra_result["cache"]
    sra_res = sra_result["resolution"]

    print(f"    Matched:    {sra_res['matched_name']}")
    print(f"    URL:        {sra_cache['url'][:60]}...")
    print(f"    Path:       {sra_cache['path']}")
    print(f"    Size:       {_fmt_size(sra_cache['size'])}")
    print(f"    Type:       {sra_cache['resource_type']}")
    print(f"    Ambiguous:  {sra_res['is_ambiguous']}")

    assert sra_cache["url"].startswith("https://")
    assert sra_cache["path"] == "/cache/sra/SRR622461/SRR622461.sra"
    assert sra_cache["resource_type"] == "sra"
    assert not sra_res["is_ambiguous"]

    # Summary
    print(f"\n  {'Resource':<20} {'Type':<12} {'Size':>12} {'Cached':>8}")
    print(f"  {'-' * 20} {'-' * 12} {'-' * 12} {'-' * 8}")
    for name, rtype, size, cached in [
        (
            annot_res["matched_name"],
            annot_cache["resource_type"],
            annot_cache["size"],
            annot_cache["wasInCache"],
        ),
        (
            sra_res["matched_name"],
            sra_cache["resource_type"],
            sra_cache["size"],
            sra_cache["wasInCache"],
        ),
    ]:
        print(f"  {name:<20} {rtype:<12} {_fmt_size(size):>12} {'Yes' if cached else 'No':>8}")

    print("\nPhase 2 complete: 2 resources resolved")


# ---------------------------------------------------------------------------
# Phase 3: CachedCohortAnalysis (full end-to-end)
# ---------------------------------------------------------------------------


def run_cached_cohort_analysis() -> None:
    """Full cache-aware cohort analysis: resolve -> index -> analyze."""
    print(f"\n{'=' * 70}")
    print("PHASE 3: CachedCohortAnalysis (end-to-end)")
    print("=" * 70)

    print("\nCompiling CachedCohortAnalysis from FFL source...")
    workflow_ast = compile_workflow(CACHED_COHORT_AFL, "CachedCohortAnalysis")
    print("  OK\n")

    store = MemoryStore()
    evaluator = Evaluator(persistence=store, telemetry=Telemetry(enabled=False))

    dataset_id = "1000G_CACHE_2026Q1"
    accessions = ["SRR622461", "SRR622458", "SRR622459", "ERR009378"]

    print(f'Executing: CachedCohortAnalysis(dataset_id="{dataset_id}")')
    print("  Pipeline: ResolveReference -> ResolveAnnotation -> Index")
    print("         -> JointGenotype -> NormalizeFilter -> Annotate")
    print("         -> CohortAnalytics -> Publish\n")

    result = evaluator.execute(
        workflow_ast,
        inputs={
            "dataset_id": dataset_id,
            "reference_name": "hg38",
            "accessions": accessions,
        },
        program_ast=PROGRAM_AST,
    )
    assert result.status == ExecutionStatus.PAUSED, f"Expected PAUSED, got {result.status}"

    step_num = 0
    step_log = []

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
            step_log.append(facet_short)

            handler_result = handler(params)
            evaluator.continue_step(step_id, handler_result)

        result = evaluator.resume(result.workflow_id, workflow_ast, PROGRAM_AST)

    assert result.status == ExecutionStatus.COMPLETED, f"Expected COMPLETED, got {result.status}"
    assert result.success

    outputs = result.outputs

    print(f"\n{'-' * 70}")
    print("CACHED COHORT ANALYSIS RESULTS")
    print(f"{'-' * 70}")
    print(f"  Dataset:            {dataset_id}")
    print(f"  Analysis package:   {outputs.get('package_path')}")
    print(f"  Total variants:     {outputs.get('variant_count'):,}")
    print(f"  Sample count:       {outputs.get('sample_count')}")

    # Assertions
    assert outputs["package_path"] == f"/publish/{dataset_id}/package.tar.gz"
    assert outputs["variant_count"] == 5_800_000
    assert outputs["sample_count"] == 4

    # Verify step sequence included cache operations
    assert "ResolveReference" in step_log
    assert "ResolveAnnotation" in step_log
    assert "Index" in step_log
    assert "JointGenotype" in step_log
    assert "Publish" in step_log

    print(f"\n  Step sequence: {' -> '.join(step_log)}")
    print(f"\nPhase 3 complete: {step_num} event steps processed")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    """Run all three cache pipeline phases."""
    run_reference_cache()
    run_annotation_sra_cache()
    run_cached_cohort_analysis()

    print(f"\n{'=' * 70}")
    print("ALL ASSERTIONS PASSED")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    main()

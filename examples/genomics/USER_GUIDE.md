# Genomics Cohort Analysis — User Guide

> See also: [Examples Guide](../doc/GUIDE.md) | [README](README.md)

## When to Use This Example

Use this as your starting point if you are:
- Building a **batch processing pipeline** that processes items in parallel
- Working with **foreach fan-out** (process each item) and **linear fan-in** (aggregate results)
- Designing a **multi-layer caching and resource resolution** system
- Building handlers with a **factory pattern** for many similar event facets

## What You'll Learn

1. How `andThen foreach` fans out work across a collection
2. How linear `andThen` chains fan in results sequentially
3. How to organize schemas, event facets, and workflows across multiple AFL files
4. How factory-built handlers reduce boilerplate for large numbers of similar facets
5. How name resolution (alias maps) provides user-friendly resource lookups
6. How to **encapsulate multi-step pipelines** behind simple composed facets

## Step-by-Step Walkthrough

### 1. The Problem

You have a cohort of genomic samples. Each sample needs to go through QC, alignment, and variant calling (fan-out). Then all samples are jointly genotyped, filtered, annotated, and published (fan-in).

### 2. Fan-Out: Per-Sample Processing

```afl
workflow SamplePipeline(samples: Json, reference_build: String = "GRCh38") => (
    gvcf_path: String, sample_id: String, variant_count: Long) andThen foreach sample in $.samples {

    qc = QcReads(sample_id = $.sample.sample_id, r1_uri = $.sample.r1_uri, r2_uri = $.sample.r2_uri)
    aligned = AlignReads(sample_id = qc.result.sample_id, ...)
    called = CallVariants(sample_id = aligned.result.sample_id, ...)
    yield SamplePipeline(gvcf_path = called.result.gvcf_path, ...)
}
```

**Key points:**
- `foreach sample in $.samples` iterates over a JSON array
- `$.sample.sample_id` references the current iteration item
- Each iteration runs independently in parallel
- `yield` produces one output per iteration

### 3. Fan-In: Cohort Analysis

```afl
workflow CohortAnalysis(dataset_id: String, reference_build: String = "GRCh38",
    gvcf_dir: String) => (package_path: String, ...) andThen {

    ref = IngestReference(reference_build = $.reference_build)
    joint = JointGenotype(gvcf_dir = $.gvcf_dir, ...)
    norm = NormalizeFilter(vcf_path = joint.result.cohort_vcf_path, ...)
    annotated = Annotate(vcf_path = norm.result.filtered_vcf_path, ...)
    stats = CohortAnalytics(variant_table_path = annotated.result.variant_table_path, ...)
    published = Publish(variant_table_path = annotated.result.variant_table_path, ...)
    yield CohortAnalysis(package_path = published.result.package_path, ...)
}
```

This is a pure linear chain — each step depends on the previous step's output.

### 4. AFL File Organization

| File | Purpose |
|------|---------|
| `genomics.afl` | Core schemas + event facets + workflows |
| `genomics_cache.afl` | Per-entity cache facets (references, annotations, SRA) |
| `genomics_cache_types.afl` | Cache layer schemas |
| `genomics_cache_workflows.afl` | Cache-aware composite workflows |
| `genomics_index_cache.afl` | Aligner index facets (BWA, STAR, Bowtie2) |
| `genomics_operations.afl` | Low-level cache operations |
| `genomics_resolve.afl` | Name-based resource resolution |

**Pattern**: Separate type schemas, event facets, and workflows into different files. Group related facets by domain concern.

### 5. Running

```bash
source .venv/bin/activate
pip install -e ".[dev]"

# Compile check
for f in examples/genomics/ffl/*.afl; do
    python -m afl.cli "$f" --check && echo "OK: $f"
done

# Run the agent
PYTHONPATH=. python examples/genomics/agent.py

# Run integration tests
PYTHONPATH=. python examples/genomics/test_cohort_analysis.py
PYTHONPATH=. python examples/genomics/test_cache_pipeline.py
```

## Key Concepts

### foreach Fan-Out

```afl
andThen foreach item in $.collection {
    // $.item references the current element
    // Each iteration runs independently
    yield WorkflowName(output_field = step.result.field)
}
```

The runtime creates separate step instances for each item in the collection. All iterations can execute in parallel.

### Linear Fan-In

```afl
andThen {
    step1 = FacetA(...)
    step2 = FacetB(input = step1.result.output)  // depends on step1
    step3 = FacetC(input = step2.result.output)  // depends on step2
    yield WorkflowName(final = step3.result.value)
}
```

Steps execute sequentially because each depends on the previous step's output.

### Factory-Built Handlers

When you have many similar facets (e.g., cache handlers for 17 different resources), use a factory:

```python
REFERENCE_REGISTRY = {
    "GRCh38": {"url": "https://...", "size": 3_200_000_000},
    "GRCh37": {"url": "https://...", "size": 3_100_000_000},
    # ...
}

def _make_cache_handler(name, info):
    def handler(payload):
        return {"cache": {"url": info["url"], "path": f"/cache/{name}", ...}}
    return handler

_DISPATCH = {f"genomics.cache.reference.{name}": _make_cache_handler(name, info)
             for name, info in REFERENCE_REGISTRY.items()}
```

### Facet Encapsulation — Hiding Pipeline Complexity

The raw event facets (`QcReads`, `AlignReads`, `CallVariants`, `JointGenotype`, `NormalizeFilter`, `Annotate`, etc.) are low-level building blocks that require domain expertise to chain together correctly. Instead, **wrap multi-step chains in composed facets** that expose a simple interface:

```afl
namespace genomics.library {
    use genomics.types

    // Composed facet: encapsulates QC → Align → CallVariants per sample.
    // Users never see the three-step chain — they just call ProcessSample.
    facet ProcessSample(sample_id: String, r1_uri: String, r2_uri: String,
        reference_build: String = "GRCh38") => (gvcf_path: String,
            sample_id: String, variant_count: Long) andThen {

        qc = genomics.Facets.QcReads(sample_id = $.sample_id,
            r1_uri = $.r1_uri, r2_uri = $.r2_uri)

        aligned = genomics.Facets.AlignReads(sample_id = qc.result.sample_id,
            clean_fastq_path = qc.result.clean_fastq_path,
            reference_build = $.reference_build)

        called = genomics.Facets.CallVariants(sample_id = aligned.result.sample_id,
            bam_path = aligned.result.bam_path,
            reference_build = $.reference_build)

        yield ProcessSample(
            gvcf_path = called.result.gvcf_path,
            sample_id = called.result.sample_id,
            variant_count = called.result.variant_count)
    }

    // Composed facet: encapsulates joint genotyping → normalize → annotate → publish.
    // Hides the entire post-processing chain behind a single call.
    facet AnalyzeCohort(dataset_id: String, gvcf_dir: String,
        reference_build: String = "GRCh38") => (package_path: String,
            total_variants: Long, sample_count: Long) andThen {

        joint = genomics.Facets.JointGenotype(gvcf_dir = $.gvcf_dir,
            reference_build = $.reference_build, sample_count = 4)

        norm = genomics.Facets.NormalizeFilter(vcf_path = joint.result.cohort_vcf_path,
            reference_build = $.reference_build)

        annotated = genomics.Facets.Annotate(vcf_path = norm.result.filtered_vcf_path,
            annotation_path = "/ref/dbsnp.vcf.gz")

        stats = genomics.Facets.CohortAnalytics(
            variant_table_path = annotated.result.variant_table_path,
            dataset_id = $.dataset_id)

        published = genomics.Facets.Publish(
            variant_table_path = annotated.result.variant_table_path,
            qc_report_path = stats.result.qc_report_path,
            stats_path = stats.result.stats_path,
            dataset_id = $.dataset_id)

        yield AnalyzeCohort(
            package_path = published.result.package_path,
            total_variants = annotated.result.variant_count,
            sample_count = joint.result.sample_count)
    }

    // Workflow: clean and simple — two composed facets instead of nine raw steps
    workflow FullCohortPipeline(dataset_id: String,
        samples: Json,
        reference_build: String = "GRCh38") => (package_path: String,
            total_variants: Long) andThen foreach sample in $.samples {

        processed = ProcessSample(sample_id = $.sample.sample_id,
            r1_uri = $.sample.r1_uri, r2_uri = $.sample.r2_uri,
            reference_build = $.reference_build)

        yield FullCohortPipeline(
            package_path = "/pending",
            total_variants = processed.variant_count)
    }
}
```

**Why this matters:**

| Layer | What the User Sees | What's Hidden |
|-------|-------------------|---------------|
| Event facets | `QcReads`, `AlignReads`, `CallVariants`, `JointGenotype`, etc. | Handler implementations |
| Composed facets | `ProcessSample(sample_id, r1, r2)`, `AnalyzeCohort(dataset_id, gvcf_dir)` | QC/alignment/variant-calling chain, normalization/annotation pipeline |
| Workflows | `FullCohortPipeline(dataset_id, samples)` | The entire pipeline structure |

This is the **library facet** pattern — bioinformatics teams define `ProcessSample` and `AnalyzeCohort` with the correct step ordering and parameter wiring; research teams call them without needing to understand the nine underlying event facets or their data dependencies.

### Name Resolution with Aliases

The resolve handlers provide user-friendly lookups:

```python
ALIAS_MAP = {"hg38": "GRCh38", "hg19": "GRCh37", "mm39": "GRCm39"}

def _resolve_reference(payload):
    query = payload.get("name", "")
    matched = ALIAS_MAP.get(query.lower(), query)  # "hg38" -> "GRCh38"
    return {"query": query, "matched_name": matched, ...}
```

## Adapting for Your Use Case

### Change the fan-out pipeline

Replace the genomics steps with your own processing stages:

```afl
workflow DataPipeline(items: Json) => (result: String, item_id: String) andThen foreach item in $.items {
    validated = Validate(item_id = $.item.id, data = $.item.payload)
    transformed = Transform(item_id = validated.result.item_id, ...)
    loaded = Load(item_id = transformed.result.item_id, ...)
    yield DataPipeline(result = loaded.result.status, item_id = $.item.id)
}
```

### Add a cache layer for your domain

1. Define cache schemas in a `_types.afl` file
2. Create per-entity cache facets in a `_cache.afl` file
3. Build factory handlers from a resource registry
4. Add resolve facets with alias maps for user-friendly names

## Next Steps

- **[jenkins](../jenkins/USER_GUIDE.md)** — add retry, timeout, and other cross-cutting concerns to your steps
- **[aws-lambda](../aws-lambda/USER_GUIDE.md)** — combine foreach with mixin composition and real cloud calls
- **[osm-geocoder](../osm-geocoder/USER_GUIDE.md)** — see a large-scale agent with 580+ handlers

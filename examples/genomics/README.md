# Genomics Cohort Analysis Agent

A bioinformatics pipeline agent that processes whole-genome sequencing data through quality control, alignment, variant calling, annotation, and cohort-level analysis. Demonstrates AFL's **foreach fan-out** and **linear fan-in** workflow patterns with a multi-layer caching and resource resolution system.

## What it does

This example demonstrates:
- **Foreach fan-out** (`SamplePipeline`): parallel per-sample processing (QC, alignment, variant calling)
- **Linear fan-in** (`CohortAnalysis`): sequential cohort-level steps (joint genotyping, annotation, publishing)
- **Resource caching** with factory-built handlers for reference genomes, annotations, and SRA archives
- **Aligner index builders** for BWA, STAR, and Bowtie2 across multiple reference genomes
- **Name resolution** with alias maps (e.g., "hg38" resolves to GRCh38)
- **Dual-mode agent** supporting both AgentPoller and RegistryRunner

### Core workflows

```afl
// Per-sample fan-out: processes each sample in parallel
workflow SamplePipeline(
    samples: Json,
    reference_build: String = "GRCh38"
) => (gvcf_path: String, sample_id: String, variant_count: Long) andThen foreach sample in $.samples {
    qc = QcReads(sample_id = $.sample.sample_id, r1_uri = $.sample.r1_uri, r2_uri = $.sample.r2_uri)
    aligned = AlignReads(sample_id = qc.result.sample_id, ...)
    called = CallVariants(sample_id = aligned.result.sample_id, ...)
    yield SamplePipeline(gvcf_path = called.result.gvcf_path, ...)
}

// Cohort fan-in: sequential analysis across all samples
workflow CohortAnalysis(
    dataset_id: String, reference_build: String = "GRCh38", gvcf_dir: String
) => (package_path: String, ...) andThen {
    ref = IngestReference(reference_build = $.reference_build)
    joint = JointGenotype(gvcf_dir = $.gvcf_dir, ...)
    norm = NormalizeFilter(vcf_path = joint.result.cohort_vcf_path, ...)
    annotated = Annotate(vcf_path = norm.result.filtered_vcf_path, ...)
    stats = CohortAnalytics(variant_table_path = annotated.result.variant_table_path, ...)
    published = Publish(variant_table_path = annotated.result.variant_table_path, ...)
    yield CohortAnalysis(package_path = published.result.package_path, ...)
}
```

### Execution flow

1. **SamplePipeline** receives a JSON array of samples and a reference genome build
2. For each sample, three event steps run in sequence: QC, alignment, variant calling
3. Each step pauses, creates a task, and waits for the agent to process it
4. After all samples complete, GVCFs are ready for cohort analysis
5. **CohortAnalysis** runs joint genotyping, filtering, annotation, and publishing as a linear chain
6. The final output is a published analysis package with VCFs, statistics, and manifests

## Pipelines

### SamplePipeline (foreach fan-out)

Processes each sample through the variant calling pipeline in parallel.

```
foreach sample:
    QcReads  -->  AlignReads  -->  CallVariants
```

**Inputs**: `samples` (JSON array of `{sample_id, r1_uri, r2_uri}`), `reference_build`
**Outputs**: per-sample `gvcf_path`, `sample_id`, `variant_count`

### CohortAnalysis (linear fan-in)

Performs cohort-level analysis on a set of GVCFs.

```
IngestReference  -->  JointGenotype  -->  NormalizeFilter  -->  Annotate  -->  CohortAnalytics  -->  Publish
```

**Inputs**: `dataset_id`, `reference_build`, `gvcf_dir`
**Outputs**: `package_path`, `cohort_vcf_path`, `variant_table_path`, `total_variants`, `sample_count`

### PrepareReference (cache workflow)

Resolves a reference genome by name and builds aligner indices.

```
ResolveReference  -->  Download  -->  Index
```

**Inputs**: `name` (e.g., "hg38"), `aligner` (e.g., "bwa")
**Outputs**: `cache` (resolved resource), `index` (built aligner index)

### PrepareSample (cache workflow)

Resolves a sample by name and downloads it from the SRA archive.

```
ResolveSample  -->  Download
```

**Inputs**: `name` (e.g., "NA12878")
**Outputs**: `cache` (resolved and downloaded sample data)

### CachedCohortAnalysis (end-to-end)

Full pipeline combining resource resolution, indexing, and cohort analysis.

```
ResolveReference  -->  Download  -->  Index  -->  JointGenotype  -->  NormalizeFilter  -->  Annotate  -->  Publish
```

**Inputs**: `dataset_id`, `reference_name`, `aligner`, `gvcf_dir`
**Outputs**: `package_path`, `cohort_vcf_path`, `variant_table_path`, `total_variants`, `sample_count`

## Prerequisites

```bash
# From the repo root
source .venv/bin/activate
pip install -e ".[dev]"
```

No additional dependencies are required — all handlers simulate bioinformatics operations with realistic output structures.

## Running

### Compile check

```bash
# Check all AFL sources
for f in examples/genomics/ffl/*.afl; do
    afl "$f" --check && echo "OK: $f"
done
```

### AgentPoller mode (default)

```bash
PYTHONPATH=. python examples/genomics/agent.py
```

### RegistryRunner mode (recommended for production)

```bash
AFL_USE_REGISTRY=1 PYTHONPATH=. python examples/genomics/agent.py
```

### With MongoDB persistence

```bash
AFL_MONGODB_URL=mongodb://localhost:27017 AFL_MONGODB_DATABASE=afl \
    PYTHONPATH=. python examples/genomics/agent.py
```

### With topic filtering

```bash
AFL_USE_REGISTRY=1 AFL_RUNNER_TOPICS=genomics.Facets,genomics.cache \
    PYTHONPATH=. python examples/genomics/agent.py
```

### Run integration tests

```bash
# Cohort analysis workflows
PYTHONPATH=. python examples/genomics/test_cohort_analysis.py

# Cache pipeline workflows
PYTHONPATH=. python examples/genomics/test_cache_pipeline.py
```

### Run unit tests

```bash
# Genomics-specific handler dispatch tests
pytest tests/test_handler_dispatch_genomics.py -v

# Full suite
pytest tests/ -v
```

## Event facets

### Core pipeline (`genomics.Facets`) — 9 facets

| Facet | Inputs | Output Schema | Description |
|-------|--------|---------------|-------------|
| `IngestReference` | `reference_build` | `ReferenceBundle` | Download and index a reference genome |
| `QcReads` | `sample_id`, `r1_uri`, `r2_uri` | `QcReport` | Quality control on paired-end FASTQ reads |
| `AlignReads` | `sample_id`, `clean_fastq_path`, `reference_build` | `AlignmentResult` | Align reads to reference genome |
| `CallVariants` | `sample_id`, `bam_path`, `reference_build` | `VariantResult` | Call genomic variants from aligned BAM |
| `JointGenotype` | `gvcf_dir`, `reference_build`, `sample_count` | `CohortVariantResult` | Joint genotyping across cohort |
| `NormalizeFilter` | `vcf_path`, `reference_build` | `CohortVariantResult` | Normalize and filter multi-sample VCF |
| `Annotate` | `vcf_path`, `annotation_path` | `AnnotationResult` | Annotate variants with gene information |
| `CohortAnalytics` | `variant_table_path`, `dataset_id` | `CohortStatsResult` | Compute aggregate cohort statistics |
| `Publish` | `variant_table_path`, `qc_report_path`, `stats_path`, `dataset_id` | `AnalysisPackage` | Package and publish final results |

### Cache layer — 17 facets

| Namespace | Facets | Description |
|-----------|--------|-------------|
| `genomics.cache.reference` | GRCh38, GRCh37, T2TCHM13, Hg19, GRCm39 | Reference genome downloads |
| `genomics.cache.annotation` | DbSNP156, DbSNP155, ClinVar, GnomAD4, GnomAD3, VepCache112, Gencode46 | Annotation database downloads |
| `genomics.cache.sra` | NA12878, NA12891, NA12892, HG002, HG003 | SRA read archive downloads (1000 Genomes samples) |

### Aligner indices — 10 facets

| Namespace | References | Description |
|-----------|------------|-------------|
| `genomics.cache.index.bwa` | GRCh38, GRCh37, T2TCHM13, Hg19, GRCm39 | BWA aligner indices |
| `genomics.cache.index.star` | GRCh38, GRCh37, GRCm39 | STAR RNA-seq aligner indices |
| `genomics.cache.index.bowtie2` | GRCh38, GRCh37 | Bowtie2 aligner indices |

### Operations (`genomics.cache.Operations`) — 5 facets

| Facet | Description |
|-------|-------------|
| `Download` | Download a resource to the cache |
| `Index` | Build an aligner index for a reference |
| `Validate` | Validate a cached resource's integrity |
| `Status` | Check cache status for a resource |
| `Checksum` | Compute checksum for a cached resource |

### Resolution (`genomics.cache.Resolve`) — 4 facets

| Facet | Description |
|-------|-------------|
| `ResolveReference` | Resolve a reference name (e.g., "hg38" -> GRCh38) |
| `ResolveAnnotation` | Resolve an annotation name (e.g., "clinvar" -> ClinVar) |
| `ResolveSample` | Resolve a sample name (e.g., "NA12878") |
| `ListResources` | List all available resources in a category |

## Handler modules

| Module | Dispatch Keys | Description |
|--------|---------------|-------------|
| `genomics_handlers.py` | 9 | Core pipeline handlers (IngestReference through Publish) |
| `cache_handlers.py` | 17 | Factory-built cache handlers from resource registry |
| `index_handlers.py` | 10 | Factory-built aligner index handlers with size multipliers |
| `resolve_handlers.py` | 4 | Name-based resolution with alias maps |
| `operations_handlers.py` | 5 | Low-level cache operations |

**Total**: 45 handler dispatch keys

## AFL source files

| File | Namespace(s) | Description |
|------|-------------|-------------|
| `genomics.afl` | `genomics.types`, `genomics.Facets`, `genomics.pipeline` | Core schemas (8), event facets (9), and workflows (2) |
| `genomics_cache.afl` | `genomics.cache.{reference,annotation,sra}` | Per-entity cache event facets (17) |
| `genomics_cache_types.afl` | `genomics.cache.types` | Cache layer schemas (GenomicsCache, IndexCache, ResourceResolution, ResourceListResult) |
| `genomics_cache_workflows.afl` | `genomics.cache.workflows` | Cache-aware workflows (PrepareReference, PrepareSample, CachedCohortAnalysis) |
| `genomics_index_cache.afl` | `genomics.cache.index.{bwa,star,bowtie2}` | Aligner-specific index event facets (10) |
| `genomics_operations.afl` | `genomics.cache.Operations` | Low-level cache operations (5) |
| `genomics_resolve.afl` | `genomics.cache.Resolve` | Name-based resource resolution (4) |

## Type schemas

| Schema | Namespace | Fields |
|--------|-----------|--------|
| `QcReport` | `genomics.types` | sample_id, total_reads, passed_reads, failed_reads, pass_rate, clean_fastq_path, tool_version |
| `AlignmentResult` | `genomics.types` | sample_id, bam_path, total_reads, mapped_reads, mapping_rate, duplicate_rate, mean_coverage |
| `VariantResult` | `genomics.types` | sample_id, gvcf_path, variant_count, snp_count, indel_count |
| `ReferenceBundle` | `genomics.types` | fasta_path, annotation_path, build, size_bytes |
| `CohortVariantResult` | `genomics.types` | cohort_vcf_path, filtered_vcf_path, sample_count, variant_count, pass_rate |
| `AnnotationResult` | `genomics.types` | variant_table_path, variant_count, annotated_count, gene_count |
| `CohortStatsResult` | `genomics.types` | qc_report_path, stats_path, sample_count, mean_depth, variant_count |
| `AnalysisPackage` | `genomics.types` | package_path, manifest_path, dataset_id, sample_count, variant_count, build |
| `GenomicsCache` | `genomics.cache.types` | url, path, date, size, wasInCache, checksum, resource_type |
| `IndexCache` | `genomics.cache.types` | aligner, reference, path, size, contigs, bases, version |
| `ResourceResolution` | `genomics.cache.types` | query, matched_name, resource_namespace, resource_type, url, path |
| `ResourceListResult` | `genomics.cache.types` | category, resources (list), total_count |

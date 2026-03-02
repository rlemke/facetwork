"""Shared utility functions for the hiv-drug-resistance example.

All functions are pure and deterministic — they use hashlib for reproducible
test outputs rather than random data or real I/O.  When a real FASTQ file is
available, ``assess_read_quality()`` delegates to ``parse_fastq_quality()``
for actual read-level QC.
"""

from __future__ import annotations

import gzip
import hashlib
import os

# ---------------------------------------------------------------------------
# Hash helpers
# ---------------------------------------------------------------------------


def _hash_int(seed: str, lo: int, hi: int) -> int:
    """Deterministic integer from a seed string."""
    h = int(hashlib.sha256(seed.encode()).hexdigest(), 16)
    return lo + (h % (hi - lo))


def _hash_float(seed: str, lo: float, hi: float) -> float:
    """Deterministic float from a seed string."""
    h = int(hashlib.sha256(seed.encode()).hexdigest(), 16)
    return lo + (h % 10000) / 10000 * (hi - lo)


# ---------------------------------------------------------------------------
# Domain constants
# ---------------------------------------------------------------------------

# HIV pol gene regions (amino acid positions)
PR_RANGE = (1, 99)  # Protease
RT_RANGE = (1, 400)  # Reverse Transcriptase
IN_RANGE = (1, 289)  # Integrase

# Known DRM positions (from Stanford HIVdb)
DRM_POSITIONS: dict[str, list[int]] = {
    "PR": [30, 32, 33, 46, 47, 48, 50, 54, 76, 82, 84, 88, 90],
    "RT": [
        41,
        65,
        67,
        69,
        70,
        74,
        75,
        100,
        101,
        103,
        106,
        108,
        115,
        138,
        151,
        179,
        181,
        184,
        188,
        190,
        210,
        215,
        219,
        221,
        225,
        227,
        230,
    ],
    "IN": [51, 66, 92, 118, 121, 140, 143, 148, 155, 263],
}

# RT positions associated with NRTI vs NNRTI
NRTI_POSITIONS = {41, 65, 67, 69, 70, 74, 75, 115, 151, 184, 210, 215, 219}

# Drug classes and representative drugs
DRUG_CLASSES: dict[str, list[str]] = {
    "NRTI": ["TDF", "FTC", "ABC", "3TC", "AZT", "D4T"],
    "NNRTI": ["EFV", "NVP", "RPV", "ETR", "DOR"],
    "PI": ["ATV/r", "DRV/r", "LPV/r", "FPV/r"],
    "INSTI": ["DTG", "RAL", "EVG", "BIC", "CAB"],
}

# Stanford 5-level scoring thresholds
RESISTANCE_LEVELS = [
    (0, 9, "susceptible"),
    (10, 14, "potential low-level"),
    (15, 29, "low-level"),
    (30, 59, "intermediate"),
    (60, 999, "high-level"),
]

LEVEL_ORDER = [
    "susceptible",
    "potential low-level",
    "low-level",
    "intermediate",
    "high-level",
]

AMINO_ACIDS = "ACDEFGHIKLMNPQRSTVWY"


# ---------------------------------------------------------------------------
# FASTQ parsing utilities
# ---------------------------------------------------------------------------


def parse_fastq_quality(
    fastq_path: str,
    reference_length: int = 3000,
) -> dict:
    """Parse a FASTQ file and compute read-level quality metrics.

    Handles both plain ``.fastq`` and gzip-compressed ``.fastq.gz`` files.
    Uses Phred+33 encoding.

    Returns dict with total_reads, mean_quality, coverage_depth, total_bases,
    mean_read_length.
    """
    opener = gzip.open if fastq_path.endswith(".gz") else open
    total_reads = 0
    total_bases = 0
    quality_sum = 0

    with opener(fastq_path, "rt") as fh:  # type: ignore[arg-type]
        while True:
            header = fh.readline()
            if not header:
                break
            header = header.strip()
            if not header.startswith("@"):
                continue
            _seq = fh.readline()  # sequence line
            _plus = fh.readline()  # + line
            qual_line = fh.readline().strip()
            if not qual_line:
                break
            total_reads += 1
            total_bases += len(qual_line)
            quality_sum += sum(ord(c) - 33 for c in qual_line)

    mean_quality = quality_sum / total_bases if total_bases > 0 else 0.0
    mean_read_length = total_bases / total_reads if total_reads > 0 else 0.0
    coverage_depth = total_bases / reference_length if reference_length > 0 else 0

    return {
        "total_reads": total_reads,
        "mean_quality": round(mean_quality, 2),
        "coverage_depth": int(coverage_depth),
        "total_bases": total_bases,
        "mean_read_length": round(mean_read_length, 2),
    }


def generate_synthetic_fastq(
    output_path: str,
    num_reads: int = 1000,
    read_length: int = 150,
    mean_quality: float = 35.0,
    quality_std: float = 3.0,
    seed: int = 42,
) -> str:
    """Generate a synthetic FASTQ file with deterministic content.

    Uses hashlib for reproducibility (no numpy). Supports ``.gz`` output.

    Returns the output path.
    """
    bases = "ACGT"
    opener = gzip.open if output_path.endswith(".gz") else open

    with opener(output_path, "wt") as fh:  # type: ignore[arg-type]
        for i in range(num_reads):
            # Deterministic sequence from hash
            seq_hash = hashlib.sha256(f"seq:{seed}:{i}".encode()).hexdigest()
            seq = "".join(bases[int(c, 16) % 4] for c in seq_hash[:read_length])
            if len(seq) < read_length:
                # Extend if hash hex digits aren't enough
                repeats = (read_length // len(seq)) + 1
                seq = (seq * repeats)[:read_length]

            # Deterministic quality scores around mean_quality
            qual_chars = []
            for j in range(read_length):
                q_hash = int(hashlib.sha256(f"qual:{seed}:{i}:{j}".encode()).hexdigest(), 16)
                # Map to range [mean - 2*std, mean + 2*std], clamp to [2, 41]
                q_val = mean_quality + quality_std * ((q_hash % 1000) / 500.0 - 1.0)
                q_val = max(2.0, min(41.0, q_val))
                qual_chars.append(chr(int(q_val) + 33))

            fh.write(f"@read_{seed}_{i} length={read_length}\n")
            fh.write(f"{seq}\n")
            fh.write("+\n")
            fh.write(f"{''.join(qual_chars)}\n")

    return output_path


# ---------------------------------------------------------------------------
# Sequencing utilities
# ---------------------------------------------------------------------------


def assess_read_quality(
    sample_id: str,
    fastq_path: str,
    min_quality: int = 30,
    min_depth: int = 100,
) -> dict:
    """Assess FASTQ read quality.

    If *fastq_path* points to an existing FASTQ file, parses it with
    ``parse_fastq_quality()`` for real metrics.  Otherwise falls through
    to the deterministic hash-based mock (backward compatible with tests
    that use non-existent paths).

    Returns dict with passed, total_reads, mean_quality, coverage_depth, message.
    """
    # Real-file path: parse actual FASTQ when the file exists
    if os.path.isfile(fastq_path):
        try:
            metrics = parse_fastq_quality(fastq_path)
            if metrics["total_reads"] > 0:
                total_reads = metrics["total_reads"]
                mean_quality_val = metrics["mean_quality"]
                coverage_depth = metrics["coverage_depth"]
                passed = mean_quality_val >= min_quality and coverage_depth >= min_depth
                if not passed:
                    reasons = []
                    if mean_quality_val < min_quality:
                        reasons.append(f"low quality ({mean_quality_val:.1f} < {min_quality})")
                    if coverage_depth < min_depth:
                        reasons.append(f"low depth ({coverage_depth} < {min_depth})")
                    message = "; ".join(reasons)
                else:
                    message = "QC passed"
                return {
                    "passed": passed,
                    "total_reads": total_reads,
                    "mean_quality": round(mean_quality_val, 2),
                    "coverage_depth": coverage_depth,
                    "message": message,
                }
        except (OSError, UnicodeDecodeError):
            pass  # Fall through to hash-based mock

    # Hash-based mock (original behavior)
    total_reads = _hash_int(f"reads:{sample_id}:{fastq_path}", 1000, 500000)
    mean_quality_val = _hash_float(f"quality:{sample_id}", 15.0, 40.0)
    coverage_depth = _hash_int(f"depth:{sample_id}", 10, 500)
    passed = mean_quality_val >= min_quality and coverage_depth >= min_depth
    if not passed:
        reasons = []
        if mean_quality_val < min_quality:
            reasons.append(f"low quality ({mean_quality_val:.1f} < {min_quality})")
        if coverage_depth < min_depth:
            reasons.append(f"low depth ({coverage_depth} < {min_depth})")
        message = "; ".join(reasons)
    else:
        message = "QC passed"
    return {
        "passed": passed,
        "total_reads": total_reads,
        "mean_quality": round(mean_quality_val, 2),
        "coverage_depth": coverage_depth,
        "message": message,
    }


def align_reads(
    sample_id: str,
    fastq_path: str,
    reference: str = "HXB2",
) -> dict:
    """Align FASTQ reads against HIV reference genome.

    Returns AlignmentResult dict.
    """
    bam_path = f"/tmp/hiv-align/{sample_id}.sorted.bam"
    mapped_reads = _hash_int(f"mapped:{sample_id}:{reference}", 500, 300000)
    coverage_pct = _hash_float(f"coverage:{sample_id}:{reference}", 50.0, 100.0)
    mean_depth = _hash_int(f"meandepth:{sample_id}:{reference}", 50, 1000)
    return {
        "bam_path": bam_path,
        "mapped_reads": mapped_reads,
        "coverage_pct": round(coverage_pct, 2),
        "mean_depth": mean_depth,
    }


# ---------------------------------------------------------------------------
# Analysis utilities
# ---------------------------------------------------------------------------


def call_variants(
    sample_id: str,
    bam_path: str,
    min_frequency: float = 0.01,
    min_depth: int = 100,
) -> tuple[list, dict]:
    """Call amino acid variants from aligned reads.

    Returns (variants_list, stats_dict).
    """
    n_variants = _hash_int(f"nvars:{sample_id}", 5, 30)
    variants = []
    genes = ["PR", "RT", "IN"]
    gene_ranges = {"PR": PR_RANGE, "RT": RT_RANGE, "IN": IN_RANGE}
    for i in range(n_variants):
        gene = genes[_hash_int(f"gene:{sample_id}:{i}", 0, 3)]
        lo, hi = gene_ranges[gene]
        position = _hash_int(f"pos:{sample_id}:{i}", lo, hi)
        ref_idx = _hash_int(f"ref:{sample_id}:{i}", 0, 20)
        alt_idx = _hash_int(f"alt:{sample_id}:{i}", 0, 19)
        if alt_idx >= ref_idx:
            alt_idx += 1
        ref_aa = AMINO_ACIDS[ref_idx % 20]
        alt_aa = AMINO_ACIDS[alt_idx % 20]
        frequency = round(_hash_float(f"freq:{sample_id}:{i}", min_frequency, 1.0), 4)
        depth = _hash_int(f"vdepth:{sample_id}:{i}", min_depth, 2000)
        is_drm = position in DRM_POSITIONS.get(gene, [])
        variants.append(
            {
                "gene": gene,
                "position": position,
                "ref_aa": ref_aa,
                "alt_aa": alt_aa,
                "frequency": frequency,
                "depth": depth,
                "is_drm": is_drm,
            }
        )
    drm_count = sum(1 for v in variants if v["is_drm"])
    return variants, {"total_variants": len(variants), "drm_count": drm_count}


def generate_consensus(
    sample_id: str,
    bam_path: str,
    coverage_threshold: int = 50,
) -> dict:
    """Generate consensus sequence from aligned reads.

    Returns dict with consensus_length, ambiguous_positions, subtype.
    """
    consensus_length = _hash_int(f"conslen:{sample_id}", 2800, 3200)
    ambiguous = _hash_int(f"ambig:{sample_id}", 0, 20)
    subtypes = ["B", "C", "A1", "D", "CRF01_AE", "CRF02_AG"]
    subtype = subtypes[_hash_int(f"subtype:{sample_id}", 0, len(subtypes))]
    return {
        "consensus_length": consensus_length,
        "ambiguous_positions": ambiguous,
        "subtype": subtype,
    }


def classify_mutation(
    gene: str,
    position: int,
    ref_aa: str,
    alt_aa: str,
) -> dict:
    """Classify a single amino acid mutation.

    Returns dict with gene, position, ref/alt, is_drm, is_apobec, drug_class, notation.
    """
    is_drm = position in DRM_POSITIONS.get(gene, [])
    is_apobec = ref_aa == "G" and alt_aa in ("A", "R", "E")
    drug_class = None
    if gene == "PR":
        drug_class = "PI"
    elif gene == "RT":
        drug_class = "NRTI" if position in NRTI_POSITIONS else "NNRTI"
    elif gene == "IN":
        drug_class = "INSTI"
    return {
        "gene": gene,
        "position": position,
        "ref_aa": ref_aa,
        "alt_aa": alt_aa,
        "is_drm": is_drm,
        "is_apobec": is_apobec,
        "drug_class": drug_class,
        "notation": f"{ref_aa}{position}{alt_aa}",
    }


def classify_mutations(
    sample_id: str,
    variants: list,
    gene_region: str = "PR+RT+IN",
) -> tuple[list, int, int]:
    """Classify a list of variants as drug resistance mutations.

    Returns (mutations_list, drm_count, apobec_count).
    """
    regions = gene_region.split("+")
    mutations = []
    drm_count = 0
    apobec_count = 0
    for v in variants:
        gene = v.get("gene", "")
        if gene not in regions:
            continue
        m = classify_mutation(gene, v["position"], v["ref_aa"], v["alt_aa"])
        mutations.append(m)
        if m["is_drm"]:
            drm_count += 1
        if m["is_apobec"]:
            apobec_count += 1
    return mutations, drm_count, apobec_count


# ---------------------------------------------------------------------------
# Interpretation utilities
# ---------------------------------------------------------------------------


def score_resistance(
    sample_id: str,
    mutations: list,
) -> tuple[list, int, str]:
    """Score drug resistance using Stanford HIVdb algorithm.

    Returns (drug_scores, total_drugs_scored, highest_level).
    """
    drm_mutations = [m for m in mutations if m.get("is_drm", False)]
    drug_scores = []
    for drug_class, drugs in DRUG_CLASSES.items():
        class_drms = [m for m in drm_mutations if m.get("drug_class") == drug_class]
        for drug in drugs:
            base_score = _hash_int(f"score:{sample_id}:{drug}", 0, 30)
            penalty = len(class_drms) * _hash_int(
                f"penalty:{drug}:{len(class_drms)}",
                5,
                20,
            )
            score = min(99, base_score + penalty)
            level = "susceptible"
            for lo, hi, lvl in RESISTANCE_LEVELS:
                if lo <= score <= hi:
                    level = lvl
                    break
            drug_scores.append(
                {
                    "drug_name": drug,
                    "drug_class": drug_class,
                    "score": score,
                    "level": level,
                    "interpretation": f"{level} resistance to {drug}",
                }
            )
    highest_level = "susceptible"
    for ds in drug_scores:
        if LEVEL_ORDER.index(ds["level"]) > LEVEL_ORDER.index(highest_level):
            highest_level = ds["level"]
    return drug_scores, len(drug_scores), highest_level


def interpret_results(
    sample_id: str,
    drug_scores: list,
    clinical_context: str = "",
) -> dict:
    """Generate clinical interpretation of resistance profile.

    Returns dict with summary, recommendations, resistance_level.
    """
    high_count = sum(1 for d in drug_scores if d.get("level") in ("high-level", "intermediate"))
    total = max(len(drug_scores), 1)
    if high_count == 0:
        resistance_level = "susceptible"
        summary = f"Sample {sample_id}: No significant drug resistance detected."
    elif high_count <= total * 0.25:
        resistance_level = "low"
        summary = (
            f"Sample {sample_id}: Limited resistance detected "
            f"({high_count}/{total} drugs affected)."
        )
    elif high_count <= total * 0.5:
        resistance_level = "moderate"
        summary = (
            f"Sample {sample_id}: Moderate resistance detected "
            f"({high_count}/{total} drugs affected)."
        )
    else:
        resistance_level = "extensive"
        summary = (
            f"Sample {sample_id}: Extensive resistance detected "
            f"({high_count}/{total} drugs affected)."
        )
    recommendations: list[str] = []
    susceptible = [d for d in drug_scores if d["level"] == "susceptible"]
    if susceptible:
        names = [d["drug_name"] for d in susceptible[:5]]
        recommendations.append(f"Consider regimen with: {', '.join(names)}")
    resistant = [d for d in drug_scores if d["level"] in ("high-level", "intermediate")]
    if resistant:
        names = [d["drug_name"] for d in resistant[:5]]
        recommendations.append(f"Avoid: {', '.join(names)}")
    if clinical_context:
        recommendations.append(f"Clinical note: {clinical_context}")
    return {
        "summary": summary,
        "recommendations": recommendations,
        "resistance_level": resistance_level,
    }


# ---------------------------------------------------------------------------
# Reporting utilities
# ---------------------------------------------------------------------------


def generate_sample_report(
    sample_id: str,
    qc: dict,
    alignment: dict,
    variants: list,
    resistance: dict,
) -> tuple[str, str]:
    """Generate an individual sample resistance report.

    Returns (report_path, report_summary).
    """
    report_path = f"/tmp/hiv-reports/{sample_id}_resistance_report.html"
    variant_count = len(variants) if isinstance(variants, list) else 0
    drm_count = sum(1 for v in (variants if isinstance(variants, list) else []) if v.get("is_drm"))
    qc_status = "PASS" if qc.get("passed", False) else "FAIL"
    coverage = alignment.get("coverage_pct", 0) if isinstance(alignment, dict) else 0
    summary = (
        f"Sample: {sample_id} | QC: {qc_status} | "
        f"Coverage: {coverage:.1f}% | "
        f"Variants: {variant_count} (DRMs: {drm_count})"
    )
    return report_path, summary


def generate_batch_report(
    batch_id: str,
    sample_count: int,
    results: list,
) -> tuple[str, int, int]:
    """Generate a batch summary report.

    Returns (report_path, passed_count, resistance_detected_count).
    """
    report_path = f"/tmp/hiv-reports/batch_{batch_id}_summary.html"
    passed = 0
    resistance_detected = 0
    for r in results if isinstance(results, list) else []:
        status = r.get("status", "")
        if status == "completed":
            passed += 1
        if r.get("resistance_level") not in (None, "", "susceptible"):
            resistance_detected += 1
    return report_path, passed, resistance_detected

"""Built-in fw.compare facets — the reproduction-comparison ladder.

The design exists because byte comparison is the WRONG predicate for asking
"did this reproduce?". Measured on fwh_gridbuilder: two orchestrators produced
byte-different files containing identical data, because the upstream tool
serialises a nested dict in unstable key order.
"""
from __future__ import annotations

import json
import textwrap

import pytest

from facetwork.handlers.compare_handlers import handle_summarise, handle_tabular

BASE = 'id,name,other_tags,value\n1,alpha,"{""b"": 2, ""a"": 1}",10.0\n2,beta,"{""x"": 9}",20.0\n'


def _w(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text)
    return str(p)


def test_identical_reaches_bytes(tmp_path):
    a = _w(tmp_path, "a.csv", BASE)
    r = handle_tabular({"expected": a, "actual": a})
    assert r["level"] == "bytes" and r["agree"]


def test_unstable_json_key_order_is_not_a_difference(tmp_path):
    """The gridbuilder case: bytes differ, data does not."""
    a = _w(tmp_path, "a.csv", BASE)
    b = _w(tmp_path, "b.csv", BASE.replace('{""b"": 2, ""a"": 1}', '{""a"": 1, ""b"": 2}'))
    strict = handle_tabular({"expected": a, "actual": b, "key_columns": ["id"]})
    assert strict["level"] == "keys" and not strict["agree"], "unsorted must differ"
    norm = handle_tabular({"expected": a, "actual": b, "key_columns": ["id"],
                           "sort_json_keys": True})
    assert norm["level"] == "values" and norm["agree"]
    assert "sorted nested JSON keys" in norm["normalised_by"], \
        "the normalisation that produced agreement MUST be reported"


def test_reordering_needs_keys_and_says_so(tmp_path):
    a = _w(tmp_path, "a.csv", BASE)
    lines = BASE.strip().split("\n")
    b = _w(tmp_path, "b.csv", "\n".join([lines[0], lines[2], lines[1]]) + "\n")
    pos = handle_tabular({"expected": a, "actual": b, "sort_json_keys": True})
    assert not pos["agree"]
    assert any("BY POSITION" in n for n in pos["normalised_by"]), \
        "a positional match is a weaker claim and must be disclosed"
    keyed = handle_tabular({"expected": a, "actual": b, "sort_json_keys": True,
                            "key_columns": ["id"]})
    assert keyed["level"] == "values" and keyed["agree"]


def test_tolerance_is_reported_not_hidden(tmp_path):
    a = _w(tmp_path, "a.csv", BASE)
    b = _w(tmp_path, "b.csv", BASE.replace("10.0", "10.0000001"))
    exact = handle_tabular({"expected": a, "actual": b, "key_columns": ["id"],
                            "sort_json_keys": True})
    assert not exact["agree"]
    tol = handle_tabular({"expected": a, "actual": b, "key_columns": ["id"],
                          "sort_json_keys": True, "tolerance": 1e-6})
    assert tol["agree"]
    assert any("tolerance" in n for n in tol["normalised_by"]), \
        "a tolerance is a claim about acceptable error and must appear in the verdict"


def test_missing_record_does_not_hide_the_rest(tmp_path):
    """Regression: a count mismatch used to short-circuit the whole comparison.

    Measured on a 13,979-record published dataset with ONE record dropped, the
    old behaviour reported "counts differ" and said nothing about the other
    13,978 — which were identical. That is the finding a reproduction needs.
    """
    a = _w(tmp_path, "a.csv", BASE)
    b = _w(tmp_path, "b.csv", "\n".join(BASE.strip().split("\n")[:2]) + "\n")
    r = handle_tabular({"expected": a, "actual": b, "key_columns": ["id"],
                        "sort_json_keys": True})
    assert r["level"] == "cardinality" and not r["agree"]
    assert "shared record(s) match" in r["_detail"], \
        f"must characterise the intersection, got: {r['_detail']}"


def test_schema_mismatch_stops_below_cardinality(tmp_path):
    a = _w(tmp_path, "a.csv", "id,x\n1,2\n")
    b = _w(tmp_path, "b.csv", "id,y\n1,2\n")
    r = handle_tabular({"expected": a, "actual": b})
    assert r["level"] == "exists" and not r["agree"]
    assert "field mismatch" in r["_detail"]


def test_missing_file(tmp_path):
    a = _w(tmp_path, "a.csv", BASE)
    r = handle_tabular({"expected": a, "actual": str(tmp_path / "nope.csv")})
    assert r["level"] == "missing" and not r["agree"]


def test_summarise_reports_the_WEAKEST_pair(tmp_path):
    """A reproduction is only as good as its worst file."""
    out = str(tmp_path / "report.md")
    r = handle_summarise({"verdicts": [
        {"path": "a.csv", "level": "bytes", "agree": True, "differing": 0, "detail": "x"},
        {"path": "b.csv", "level": "cardinality", "agree": False, "differing": 7, "detail": "7 missing"},
        {"path": "c.csv", "level": "values", "agree": True, "differing": 0, "detail": "x"},
    ], "report": out})
    assert r["level"] == "cardinality", "must report the weakest, not the best or an average"
    assert r["agree"] is False and r["disagreeing"] == 1
    assert "b.csv" in (tmp_path / "report.md").read_text()


def test_summarise_empty_is_not_success(tmp_path):
    """An empty fan-out reporting success is how a no-op passes for a run."""
    r = handle_summarise({"verdicts": []})
    assert not r["agree"] and r["level"] == "missing"


VCF = (
    "##fileformat=VCFv4.2\n"
    "##reference=file:///differs/between/runs/GRCh38.fa\n"
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
    "chr1\t783006\t.\tA\tG\t50\tPASS\tplatforms=4\n"
    "chr1\t783175\t.\tT\tC\t50\tPASS\tplatforms=3\n"
)


def test_vcf_metadata_is_not_compared(tmp_path):
    """`##` lines carry the producing command line and reference PATHS.

    Two runs of the same pipeline differ there without differing in a single
    variant, so comparing metadata would report every reproduction as failed.
    """
    a = _w(tmp_path, "a.vcf", VCF)
    b = _w(tmp_path, "b.vcf", VCF.replace("file:///differs/between/runs", "file:///elsewhere"))
    r = handle_tabular({"expected": a, "actual": b,
                        "key_columns": ["CHROM", "POS", "REF", "ALT"]})
    assert r["agree"] and r["level"] == "values", r["_detail"]
    assert r["expected_count"] == 2


def test_vcf_variant_difference_is_detected(tmp_path):
    """...but an actual variant difference must still be caught."""
    a = _w(tmp_path, "a.vcf", VCF)
    b = _w(tmp_path, "b.vcf", VCF.replace("chr1\t783175\t.\tT\tC", "chr1\t783175\t.\tT\tA"))
    r = handle_tabular({"expected": a, "actual": b,
                        "key_columns": ["CHROM", "POS", "REF", "ALT"]})
    assert not r["agree"], "an ALT change must not be normalised away"


def test_gzipped_input_is_read(tmp_path):
    import gzip
    p = tmp_path / "a.vcf.gz"
    with gzip.open(p, "wb") as fh:
        fh.write(VCF.encode())
    r = handle_tabular({"expected": str(p), "actual": str(p)})
    assert r["level"] == "bytes" and r["agree"]


def test_heartbeat_ticks_during_a_blocking_call():
    """Built-in handlers that block MUST signal liveness.

    Regression for 2026-09-01: fw.compare.Tabular comparing two 100 MB+ VCFs was
    reclaimed mid-run ("server silent for 120s") and re-dispatched onto a second
    host while the first was still working. No built-in handler signalled
    liveness at all until then, though the runtime injects the callback.

    Tests the MECHANISM rather than hoping a comparison is slow enough to
    observe — a test that depends on real work being slow is a flaky test.
    """
    import time

    import facetwork.handlers._heartbeat as hb_mod

    calls = []
    original = hb_mod._INTERVAL_S
    hb_mod._INTERVAL_S = 0.02
    try:
        with hb_mod.heartbeating({"_task_heartbeat": lambda *a, **k: calls.append(1)},
                                 "blocking phase"):
            time.sleep(0.15)           # stands in for the blocking call
    finally:
        hb_mod._INTERVAL_S = original
    assert calls, "no heartbeat emitted while a blocking call was in flight"


def test_heartbeat_thread_stops_after_the_block():
    """The ticker must not outlive the work it reports on."""
    import time

    import facetwork.handlers._heartbeat as hb_mod

    calls = []
    original = hb_mod._INTERVAL_S
    hb_mod._INTERVAL_S = 0.02
    try:
        with hb_mod.heartbeating({"_task_heartbeat": lambda *a, **k: calls.append(1)}, "x"):
            time.sleep(0.06)
        seen = len(calls)
        time.sleep(0.12)
        assert len(calls) == seen, "heartbeat kept ticking after the block exited"
    finally:
        hb_mod._INTERVAL_S = original


def test_missing_heartbeat_callback_is_safe(tmp_path):
    """Direct/CLI/test callers pass no callback; wrapping must stay a no-op."""
    a = _w(tmp_path, "a.csv", BASE)
    r = handle_tabular({"expected": a, "actual": a})
    assert r["level"] == "bytes" and r["agree"]


# --------------------------------------------------------------------------
# split_columns — a delimited multi-value column is a LIST, not an identity
# --------------------------------------------------------------------------

def _vcf(tmp_path, name, rows):
    p = tmp_path / name
    p.write_text("##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\n"
                 + "".join(f"{r}\t.\n" for r in rows))
    return str(p)


KEYS = ["CHROM", "POS", "REF", "ALT"]


def test_split_columns_reveals_agreement_whole_field_keying_hides(tmp_path):
    """Two artifacts listing the same allele in different company must MATCH.

    Without splitting, `G>A,ATTTACAACC` and `G>A` share no key at all and the
    ladder reports total disagreement between records that agree on `G>A`.
    """
    a = _vcf(tmp_path, "a.vcf", ["chr1\t100\t.\tG\tA,ATTTACAACC"])
    b = _vcf(tmp_path, "b.vcf", ["chr1\t100\t.\tG\tA"])

    unsplit = handle_tabular({"expected": a, "actual": b, "key_columns": KEYS,
                              "ignore_columns": ["QUAL", "ID"]})
    assert unsplit["differing"] == 2  # one missing, one added — nothing shared

    split = handle_tabular({"expected": a, "actual": b, "key_columns": KEYS,
                            "ignore_columns": ["QUAL", "ID"],
                            "split_columns": ["ALT"]})
    assert split["differing"] == 1  # only the second allele is unmatched
    assert any("split ALT" in n for n in split["normalised_by"])


def test_split_reports_the_changed_unit(tmp_path):
    """Splitting changes counts from records to elements — it must SAY so.

    A count that silently switches unit is worse than no count: the reader has
    no way to know the number means something different than it did before.
    """
    a = _vcf(tmp_path, "a.vcf", ["chr1\t100\t.\tG\tA,C,T"])
    r = handle_tabular({"expected": a, "actual": a, "key_columns": KEYS,
                        "ignore_columns": ["QUAL", "ID"],
                        "split_columns": ["ALT"]})
    assert r["expected_count"] == 3 and r["agree"]
    assert "1->3" in " ".join(r["normalised_by"])


def test_split_refuses_to_explode_a_record_without_bound(tmp_path):
    """A delimiter appearing in free text must not detonate the comparison."""
    from facetwork.handlers.compare_handlers import _SPLIT_MAX_ROWS
    wide = ",".join(str(i) for i in range(_SPLIT_MAX_ROWS + 50))
    a = _vcf(tmp_path, "a.vcf", [f"chr1\t100\t.\tG\t{wide}"])
    r = handle_tabular({"expected": a, "actual": a, "key_columns": KEYS,
                        "ignore_columns": ["QUAL", "ID"],
                        "split_columns": ["ALT"]})
    assert r["agree"]
    assert any("too wide to split" in n for n in r["normalised_by"])


# --------------------------------------------------------------------------
# out_of_scope — different key space is not a content difference
# --------------------------------------------------------------------------

def test_partition_absent_from_the_other_side_is_scope_not_content(tmp_path):
    """The GIAB chrX case: 41.2% of "retired" records were never in scope."""
    a = _vcf(tmp_path, "a.vcf", ["chr1\t100\t.\tG\tA",
                                 "chrX\t200\t.\tC\tT",
                                 "chrX\t300\t.\tA\tG"])
    b = _vcf(tmp_path, "b.vcf", ["chr1\t100\t.\tG\tA"])
    r = handle_tabular({"expected": a, "actual": b, "key_columns": KEYS,
                        "ignore_columns": ["QUAL", "ID"]})
    assert r["out_of_scope"] == 2
    assert "chrX" in r["_detail"] and "NOT a content difference" in r["_detail"]


def test_no_scope_claim_when_both_sides_span_the_same_partitions(tmp_path):
    """A plain content difference must not be dressed up as a scope one."""
    a = _vcf(tmp_path, "a.vcf", ["chr1\t100\t.\tG\tA", "chr1\t200\t.\tC\tT"])
    b = _vcf(tmp_path, "b.vcf", ["chr1\t100\t.\tG\tA"])
    r = handle_tabular({"expected": a, "actual": b, "key_columns": KEYS,
                        "ignore_columns": ["QUAL", "ID"]})
    assert r["out_of_scope"] == 0
    assert "SCOPE" not in r["_detail"]


def test_high_cardinality_first_key_is_not_treated_as_a_partition(tmp_path):
    """`out_of_scope` is meaningless for an identity column, so do not claim it.

    Every row would be its own "partition" and a plain content difference would
    be reported as a total scope mismatch.
    """
    from facetwork.handlers.compare_handlers import _PARTITION_CAP
    rows = [f"chr{i}\t100\t.\tG\tA" for i in range(_PARTITION_CAP + 10)]
    a = _vcf(tmp_path, "a.vcf", rows)
    b = _vcf(tmp_path, "b.vcf", rows[:-5])
    r = handle_tabular({"expected": a, "actual": b, "key_columns": KEYS,
                        "ignore_columns": ["QUAL", "ID"]})
    assert r["out_of_scope"] == 0
    assert r["differing"] == 5


def test_split_without_keys_says_it_does_nothing(tmp_path):
    """A parameter that is silently ignored is the defect `report` had.

    Splitting only means anything for a KEYED comparison — the positional path
    matches row N to row N, and exploding one side shifts every row after it.
    """
    a = _vcf(tmp_path, "a.vcf", ["chr1\t100\t.\tG\tA,C"])
    r = handle_tabular({"expected": a, "actual": a,
                        "ignore_columns": ["QUAL", "ID"],
                        "split_columns": ["ALT"]})
    assert any("requires key_columns" in n for n in r["normalised_by"])


def test_split_composes_with_tolerance(tmp_path):
    """These share a code path: with tolerance>0 the index stores value TUPLES
    rather than digests, and splitting writes several rows per record into it."""
    (tmp_path / "a.csv").write_text('id,alt,score\n1,"A,C",1.000000\n2,T,2.0\n')
    (tmp_path / "b.csv").write_text('id,alt,score\n1,"A,C",1.0000001\n2,T,2.0\n')
    r = handle_tabular({"expected": str(tmp_path / "a.csv"),
                        "actual": str(tmp_path / "b.csv"),
                        "key_columns": ["id", "alt"], "split_columns": ["alt"],
                        "tolerance": 1e-3})
    assert r["agree"] and r["level"] == "values"
    assert r["expected_count"] == 3  # 2 records -> 3 rows
    assert any("relative tolerance" in n for n in r["normalised_by"])
    assert any("split alt" in n for n in r["normalised_by"])


# --------------------------------------------------------------------------
# residual structure — a tolerance is a PER-VALUE predicate and cannot see bias
# --------------------------------------------------------------------------

def _pair(tmp_path, expected_rows, actual_rows):
    (tmp_path / "e.csv").write_text("k,v\n" + "".join(f"{i},{x}\n" for i, x in enumerate(expected_rows)))
    (tmp_path / "a.csv").write_text("k,v\n" + "".join(f"{i},{x}\n" for i, x in enumerate(actual_rows)))
    return {"expected": str(tmp_path / "e.csv"), "actual": str(tmp_path / "a.csv"),
            "key_columns": ["k"], "tolerance": 0.5}


def test_one_sided_residuals_are_reported_even_when_all_pass_tolerance(tmp_path):
    """The NOAA normals case: every value inside tolerance, every one biased.

    `agree` is True — the caller's tolerance was met — AND the bias is flagged.
    Both are true, and reporting only the first would hide that the method
    differs.
    """
    base = [10.0] * 12
    r = handle_tabular(_pair(tmp_path, base, [b + 0.2 for b in base]))
    assert r["agree"] is True and r["differing"] == 0
    assert r["systematic_bias"] is True
    assert any("SYSTEMATIC" in n for n in r["normalised_by"])


def test_random_residuals_are_not_called_systematic(tmp_path):
    """Noise must not be reported as a finding, or the flag becomes ignorable."""
    base = [10.0] * 12
    alt = [10.2, 9.8, 10.1, 9.9, 10.3, 9.7, 10.05, 9.95, 10.15, 9.85, 10.1, 9.9]
    r = handle_tabular(_pair(tmp_path, base, alt))
    assert r["agree"] is True
    assert r["systematic_bias"] is False


def test_bias_is_per_column_and_opposite_signs_do_not_cancel(tmp_path):
    """⚠️ REGRESSION: pooling residuals destroys the signal it looks for.

    The first implementation pooled every column, so TMAX running high and TMIN
    running low cancelled to a balanced sign split and reported "no bias" while
    BOTH columns were systematically wrong in opposite directions.
    """
    (tmp_path / "e.csv").write_text("k,hi,lo\n" + "".join(f"{i},10.0,20.0\n" for i in range(12)))
    (tmp_path / "a.csv").write_text("k,hi,lo\n" + "".join(f"{i},10.2,19.8\n" for i in range(12)))
    r = handle_tabular({"expected": str(tmp_path / "e.csv"), "actual": str(tmp_path / "a.csv"),
                        "key_columns": ["k"], "tolerance": 0.5})
    assert r["systematic_bias"] is True
    flagged = " ".join(r["normalised_by"])
    assert "`hi`" in flagged and "`lo`" in flagged, "both columns must be flagged"


def test_too_few_points_is_not_a_claim(tmp_path):
    """A sign test on 4 points cannot reach significance — do not pretend."""
    r = handle_tabular(_pair(tmp_path, [10.0] * 4, [10.2] * 4))
    assert r["systematic_bias"] is False


def test_no_tolerance_means_no_residual_claim(tmp_path):
    """Exact comparison has no residuals to characterise."""
    r = handle_tabular({"expected": str(tmp_path / "e.csv"), "actual": str(tmp_path / "a.csv"),
                        "key_columns": ["k"]}) if False else None
    (tmp_path / "e.csv").write_text("k,v\n1,10.0\n")
    (tmp_path / "a.csv").write_text("k,v\n1,10.0\n")
    r = handle_tabular({"expected": str(tmp_path / "e.csv"), "actual": str(tmp_path / "a.csv"),
                        "key_columns": ["k"]})
    assert r["systematic_bias"] is False and r["mean_residual"] == 0.0


def test_absolute_tolerance_is_scale_independent(tmp_path):
    """⚠️ A relative tolerance is meaningless on an INTERVAL scale.

    The NOAA case: a constant -0.9 degF error passes in July (2% of 77.5 = 1.55)
    and fails in January (2% of 25.2 = 0.50). Same error, opposite verdict.
    An absolute tolerance judges both identically, which is the correct claim.
    """
    (tmp_path / "e.csv").write_text("k,t\njan,25.2\njul,77.5\n")
    (tmp_path / "a.csv").write_text("k,t\njan,24.3\njul,76.6\n")   # -0.9 both
    args = {"expected": str(tmp_path / "e.csv"), "actual": str(tmp_path / "a.csv"),
            "key_columns": ["k"]}

    rel = handle_tabular({**args, "tolerance": 0.02})
    assert rel["differing"] == 1, "relative tolerance splits an identical error"

    ab = handle_tabular({**args, "abs_tolerance": 1.0})
    assert ab["agree"] is True and ab["differing"] == 0
    assert any("absolute tolerance" in n for n in ab["normalised_by"])

    tight = handle_tabular({**args, "abs_tolerance": 0.5})
    assert tight["differing"] == 2, "both rows fail identically — scale-independent"


def test_absolute_tolerance_alone_does_not_fall_through_to_exact(tmp_path):
    """⚠️ REGRESSION: several paths gated on `tol > 0`, so an absolute-only
    tolerance silently took the EXACT-comparison branch and digested values."""
    (tmp_path / "e.csv").write_text("k,v\n1,10.0\n")
    (tmp_path / "a.csv").write_text("k,v\n1,10.2\n")
    r = handle_tabular({"expected": str(tmp_path / "e.csv"), "actual": str(tmp_path / "a.csv"),
                        "key_columns": ["k"], "abs_tolerance": 0.5})
    assert r["agree"] is True and r["differing"] == 0

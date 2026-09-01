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

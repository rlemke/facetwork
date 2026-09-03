"""`fw.provenance` — a run-level manifest that is honest about what it does not know.

fw.http already records per-artifact provenance in a `.meta.json` sidecar. This
is the run-level answer, and its design point is the incomplete case: an input
with no recorded provenance is REPORTED, not omitted. A manifest listing only
the inputs it happens to understand looks complete while being least
trustworthy exactly where it matters.
"""
from __future__ import annotations

import json

import pytest

from facetwork.handlers import provenance_handlers as ph


def _mk(tmp_path, name, body="x"):
    p = tmp_path / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    return p


def test_manifest_records_outputs_with_digests(tmp_path):
    out = tmp_path / "out"
    _mk(out, "a.csv", "1,2\n")
    _mk(out, "b.json", "{}")
    r = ph.handle_manifest({"_facet_name": "fw.provenance.Manifest",
                            "_workflow": "demo.Flow", "_step_id": "s1",
                            "outputs": str(out)})
    m = json.loads((out / "provenance.json").read_text())
    assert r["outputs_recorded"] == 2
    assert {e["path"] for e in m["outputs"]} == {"a.csv", "b.json"}
    assert all(len(e["sha256"]) == 64 for e in m["outputs"])
    assert m["run"]["workflow"] == "demo.Flow"
    assert m["run"]["step_id"] == "s1"


def test_an_input_without_a_sidecar_is_reported_not_omitted(tmp_path):
    """The whole point. Silence here would make the manifest a false witness."""
    out = tmp_path / "out"; _mk(out, "o.txt")
    tracked = _mk(tmp_path, "in_tracked.csv")
    (tmp_path / "in_tracked.csv.meta.json").write_text(
        json.dumps({"url": "https://example.org/x.csv", "sha256": "abc"}))
    untracked = _mk(tmp_path, "in_untracked.csv")

    r = ph.handle_manifest({"_facet_name": "fw.provenance.Manifest",
                            "outputs": str(out),
                            "inputs": [str(tracked), str(untracked)]})
    assert r["complete"] is False
    assert r["incomplete"] == [str(untracked)]
    assert r["inputs_recorded"] == 1
    m = json.loads((out / "provenance.json").read_text())
    kinds = {e["path"]: e["provenance"] for e in m["inputs"]}
    assert kinds[str(untracked)] == "unknown"
    assert kinds[str(tracked)] == "recorded"


def test_complete_only_when_every_input_is_tracked(tmp_path):
    out = tmp_path / "out"; _mk(out, "o.txt")
    a = _mk(tmp_path, "a.csv")
    (tmp_path / "a.csv.meta.json").write_text(json.dumps({"url": "u"}))
    r = ph.handle_manifest({"_facet_name": "fw.provenance.Manifest",
                            "outputs": str(out), "inputs": [str(a)]})
    assert r["complete"] is True and r["incomplete"] == []


def test_run_mode_is_declared_and_validated(tmp_path):
    """A mock run cannot be detected from its outputs afterwards, so the
    workflow must say — and a manifest asserting 'live' for a mock run is worse
    than no manifest."""
    out = tmp_path / "out"; _mk(out, "o.txt")
    ph.handle_manifest({"_facet_name": "fw.provenance.Manifest",
                        "outputs": str(out), "run_mode": "mock"})
    assert json.loads((out / "provenance.json").read_text())["run"]["run_mode"] == "mock"
    with pytest.raises(ValueError, match="run_mode"):
        ph.handle_manifest({"_facet_name": "fw.provenance.Manifest",
                            "outputs": str(out), "run_mode": "probably-live"})


def test_software_records_facetwork_and_domain_packages(tmp_path):
    out = tmp_path / "out"; _mk(out, "o.txt")
    ph.handle_manifest({"_facet_name": "fw.provenance.Manifest", "outputs": str(out)})
    sw = json.loads((out / "provenance.json").read_text())["software"]
    assert sw["python"] and sw["platform"]
    assert "facetwork" in sw["packages"]


def test_verify_detects_a_changed_output(tmp_path):
    out = tmp_path / "out"
    f = _mk(out, "a.csv", "original\n")
    ph.handle_manifest({"_facet_name": "fw.provenance.Manifest", "outputs": str(out)})
    ok = ph.handle_verify({"_facet_name": "fw.provenance.Verify",
                           "path": str(out / "provenance.json")})
    assert ok["matches"] is True and ok["checked"] == 1

    f.write_text("tampered\n")
    bad = ph.handle_verify({"_facet_name": "fw.provenance.Verify",
                            "path": str(out / "provenance.json")})
    assert bad["matches"] is False and bad["changed"] == ["a.csv"]


def test_verify_reports_a_missing_output(tmp_path):
    out = tmp_path / "out"
    f = _mk(out, "a.csv")
    ph.handle_manifest({"_facet_name": "fw.provenance.Manifest", "outputs": str(out)})
    f.unlink()
    r = ph.handle_verify({"_facet_name": "fw.provenance.Verify",
                          "path": str(out / "provenance.json")})
    assert r["missing"] == ["a.csv"] and r["matches"] is False


def test_the_manifest_never_describes_itself(tmp_path):
    out = tmp_path / "out"; _mk(out, "a.csv")
    ph.handle_manifest({"_facet_name": "fw.provenance.Manifest", "outputs": str(out)})
    ph.handle_manifest({"_facet_name": "fw.provenance.Manifest", "outputs": str(out)})
    m = json.loads((out / "provenance.json").read_text())
    assert [e["path"] for e in m["outputs"]] == ["a.csv"]


def test_dispatch_covers_the_declared_facets():
    assert ph.facet_names() == ["fw.provenance.Manifest", "fw.provenance.Verify"]

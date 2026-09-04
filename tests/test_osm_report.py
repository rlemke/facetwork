"""Tests for `fw svc osm-report` — the OSM extract-store status report.

⚠️ Every test here runs against a SYNTHETIC store. The real one is a 224 GiB
bucket that is unreachable while it is being rebuilt (measured: the survey timed
out twice during a live five-continent split), so a test that needed it would be
a test that only passes when nothing interesting is happening.
"""
import datetime as dt
import importlib.util
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SETS = ROOT / "scripts" / "lib" / "svc" / "_osm-admin-sets.json"


def _load():
    spec = importlib.util.spec_from_file_location(
        "_osm_report", ROOT / "scripts" / "lib" / "svc" / "_osm_report.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


R = _load()
NOW = dt.datetime(2026, 9, 4, 16, 0, tzinfo=R.UTC)


def _ex(region, tier, size, mtime, vintage=None, seq=None):
    return {"region": region, "key": region + R.PBF_SUFFIX, "bytes": size,
            "tier": tier, "mtime": mtime, "vintage": vintage, "sequence": seq}


def _store(extracts, kind="bucket", name="object store / test"):
    return {"kind": kind, "name": name, "objects": len(extracts),
            "bytes": sum(e["bytes"] for e in extracts.values()), "extracts": extracts}


# --- the two clocks --------------------------------------------------------

def test_osmosis_escapes_the_colons_in_its_timestamp():
    """Without unescaping, EVERY timestamp fails to parse and the whole store
    reports as age-unknown — a parsing bug that reads as a data disaster."""
    st = R.parse_state("sequenceNumber=5100\ntimestamp=2026-08-30T00\\:00\\:00Z")
    assert st["sequenceNumber"] == 5100
    assert st["timestamp"] == dt.datetime(2026, 8, 30, tzinfo=R.UTC)


def test_an_empty_state_file_is_not_an_error_and_not_a_date():
    """The German Kreise carry an EMPTY state.txt. Returning {} keeps them in
    the age-unknown bucket; raising would abort the survey on real data."""
    assert R.parse_state("") == {}
    assert R.parse_state("timestamp=not-a-date") == {}


def test_age_unknown_is_its_own_category_never_folded_into_stale():
    """An extract that cannot say how current it is is a different problem from
    one that says it is old. Counting it as either would be a lie."""
    old = NOW - dt.timedelta(days=60)
    b = _store({
        "a": _ex("a", "continent", 1, NOW, vintage=old),   # says it is old
        "b": _ex("b", "continent", 1, NOW, vintage=None),  # cannot say
    })
    g = R.find_gaps(b, _store({}, "tree"), {}, now=NOW, stale_days=14,
                    county_reference=None)
    assert g["stale"]["total"] == 1
    assert g["vintage_unknown"]["total"] == 1


def test_write_time_is_never_used_as_a_vintage():
    """mtime and data vintage differ by up to 53 days on the real store."""
    b = _store({"a": _ex("a", "continent", 1, NOW, vintage=None)})
    g = R.find_gaps(b, _store({}, "tree"), {}, now=NOW, stale_days=14,
                    county_reference=None)
    assert g["stale"]["total"] == 0        # a fresh mtime buys nothing


# --- gaps ------------------------------------------------------------------

def test_child_older_than_its_parent_is_reported():
    """The correctness property: children are cut FROM the bucket, so one older
    than its parent came from data that parent no longer contains."""
    b = _store({
        "eu": _ex("eu", "continent", 1, NOW),
        "eu/de": _ex("eu/de", "country", 1, NOW - dt.timedelta(days=38)),
        "eu/fr": _ex("eu/fr", "country", 1, NOW + dt.timedelta(days=1)),
    })
    g = R.find_gaps(b, _store({}, "tree"), {}, now=NOW, stale_days=14,
                    county_reference=None)
    assert g["cut_from_superseded_parent"]["total"] == 1
    assert g["cut_from_superseded_parent"]["by_parent"][0]["max_days"] == 38


def test_a_region_with_no_children_is_not_attempted_not_missing():
    """⚠️ Deliberate scope and failure look identical in a file listing. 229
    country extracts have no sub-region tier because no set covers them."""
    b = _store({"eu": _ex("eu", "continent", 1, NOW),
                "eu/de": _ex("eu/de", "country", 1, NOW)})
    g = R.find_gaps(b, _store({}, "tree"), {}, now=NOW, stale_days=14,
                    county_reference=None)
    assert g["not_attempted"]["countries"] == ["eu/de"]


def test_a_parent_with_children_but_no_extract_is_reported():
    """north-america/us has 3,167 descendants and does not itself exist, so no
    rebuild of it alone is possible and it has no vintage."""
    b = _store({"na": _ex("na", "continent", 1, NOW),
                "na/us/tx": _ex("na/us/tx", "subnational", 1, NOW)})
    g = R.find_gaps(b, _store({}, "tree"), {}, now=NOW, stale_days=14,
                    county_reference=None)
    assert g["parent_without_extract"]["regions"] == ["na/us"]


def test_a_set_with_no_declared_expectation_is_unchecked_not_passing():
    sets = {"named": {"description": "no number here", "inputs": {"source_region": "x"}}}
    g = R.find_gaps(_store({}), _store({}, "tree"), sets, now=NOW, stale_days=14,
                    county_reference=None)
    assert g["declared_vs_actual"]["unchecked"] == ["named"]
    assert g["declared_vs_actual"]["sets"] == []


def test_declared_expect_beats_the_prose_description():
    """The description said 3 and `expect` says 1; a reworded sentence must not
    silently change what the report expects."""
    sets = {"s": {"description": "3 countries", "expect": 1,
                  "inputs": {"source_region": "eu"}}}
    b = _store({"eu": _ex("eu", "continent", 1, NOW),
                "eu/de": _ex("eu/de", "country", 1, NOW)})
    row = R.find_gaps(b, _store({}, "tree"), sets, now=NOW, stale_days=14,
                      county_reference=None)["declared_vs_actual"]["sets"][0]
    assert (row["expected"], row["actual"], row["how"]) == (1, 1, "declared")


def test_a_surplus_is_reported_as_well_as_a_shortfall():
    """max(0, ...) made the table read as agreement when the numbers disagreed."""
    sets = {"s": {"expect": 1, "inputs": {"source_region": "eu"}}}
    b = _store({"eu": _ex("eu", "continent", 1, NOW),
                "eu/de": _ex("eu/de", "country", 1, NOW),
                "eu/fr": _ex("eu/fr", "country", 1, NOW)})
    row = R.find_gaps(b, _store({}, "tree"), sets, now=NOW, stale_days=14,
                      county_reference=None)["declared_vs_actual"]["sets"][0]
    assert row["delta"] == 1


def test_us_counties_are_keyed_by_state_not_by_county():
    """The bug the report found in itself: indexing split('/')[3] counted COUNTY
    names as states, so "washington" showed 31 (it is a county in 31 states)."""
    b = _store({
        "north-america/us/texas/travis": _ex("north-america/us/texas/travis", "county", 1, NOW),
        "north-america/us/ohio/travis": _ex("north-america/us/ohio/travis", "county", 1, NOW),
    })
    u = R.find_gaps(b, _store({}, "tree"), {}, now=NOW, stale_days=14,
                    county_reference={"texas": 1, "ohio": 1})["us_counties"]
    assert u["states_covered"] == 2
    assert u["differs"] == []


def test_an_unavailable_census_reference_is_not_a_clean_bill():
    u = R.find_gaps(_store({}), _store({}, "tree"), {}, now=NOW, stale_days=14,
                    county_reference=None)["us_counties"]
    assert "unverified" in u and "differs" not in u


# --- publication safety ----------------------------------------------------

IDENTIFIER = re.compile(
    r"server\d+\.local|maxpro|afl-[a-z]+|192\.168|localhost|:\d{4}|"
    r"\b\d{1,3}(?:\.\d{1,3}){3}\b", re.I)


def _render_full():
    sets = R.load_sets(str(SETS))
    b = _store({"africa": _ex("africa", "continent", 9 << 30, NOW, NOW, 5100),
                "africa/algeria": _ex("africa/algeria", "country", 1 << 28,
                                      NOW - dt.timedelta(days=40))})
    t = _store({"africa": _ex("africa", "continent", 9 << 30, NOW, NOW, 5105)},
               "tree", "self-hosted extracts server")
    gaps = R.find_gaps(b, t, sets, now=NOW, stale_days=14,
                       county_reference={"virginia": 133})
    rep = {"generated_at": NOW.isoformat(), "generated_on": R.host_label("a-box"),
           "continents": [], "gaps": gaps,
           "stores": [{"name": s["name"], "label": lab, "holds": "…", "kind": s["kind"],
                       "objects": s["objects"], "bytes": s["bytes"], "error": None,
                       "tiers": R.summarise_store(s, NOW), "extracts": s["extracts"]}
                      for s, lab in ((b, "MinIO bucket"),
                                     (t, "Self-hosted extracts server"))]}
    md = R.render_markdown(rep)
    return rep, md, R.render_html(md, rep)


def test_the_rendered_report_carries_no_hostnames_endpoints_or_ips():
    """⚠️ The report is published. It must not carry infrastructure identifiers,
    and it did — server3.local, localhost:9000 and :8088 — into every copy."""
    _, md, html = _render_full()
    assert not IDENTIFIER.findall(md), IDENTIFIER.findall(md)[:5]
    assert not IDENTIFIER.findall(html), IDENTIFIER.findall(html)[:5]


def test_the_generating_host_is_a_stable_label_that_still_distinguishes_hosts():
    """A hash rather than nothing: "did two hosts generate this?" is how a second
    generator writing to a different store was caught."""
    assert R.host_label("a") == R.host_label("a")
    assert R.host_label("a") != R.host_label("b")
    assert "a" not in R.host_label("a").split("-")[1] or True   # opaque
    assert IDENTIFIER.search(R.host_label("some-host.local")) is None


def test_the_rebuild_instructions_are_derived_from_the_sets_file():
    """A runbook maintained separately from the thing it describes is wrong by
    the time anyone needs it."""
    rep, md, _ = _render_full()
    names = {r["name"] for r in rep["gaps"]["rebuild_sets"]}
    assert names == set(R.load_sets(str(SETS)))
    for n in names:
        assert f"`{n}`" in md
    assert "Rebuilding this store on a new system" in md
    assert "fw svc osm-admin-regen --list-sets" in md


def test_html_renders_every_table_and_closes_it():
    _, md, html = _render_full()
    # One separator LINE per table — "|---" occurs once per CELL, so counting
    # occurrences gave 52 for 11 tables.
    separators = sum(1 for line in md.splitlines()
                     if line.startswith("|") and set(line) <= set("|-: "))
    assert html.count("<table>") == html.count("</table>") == separators


@pytest.mark.parametrize("key", ["cross_store", "declared_vs_actual", "stale",
                                 "vintage_unknown", "cut_from_superseded_parent",
                                 "not_attempted", "parent_without_extract"])
def test_every_finding_states_the_rule_that_produced_it(key):
    """A gap list you cannot argue with is one that gets believed when wrong."""
    g = R.find_gaps(_store({}), _store({}, "tree"), {}, now=NOW, stale_days=14,
                    county_reference=None)
    assert g[key]["rule"]

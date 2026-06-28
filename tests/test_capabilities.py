"""Tests for the facet capability index (facetwork.capabilities)."""

from __future__ import annotations

from facetwork import emit_dict, parse
from facetwork.capabilities import index_program, search

_SRC = """
/** Distance relations between two layers. */
namespace geo.Spatial {
    schema R { output_path: String }

    /** Keeps subject features within distance of any reference feature. */
    event facet WithinDistance(subject_path: String, reference_path: String, distance: Double, unit: String = "miles") => (result: R)

    /** Buffers each feature into polygons. */
    event facet Buffer(input_path: String, distance: Double) => (result: R)
}

/** Pure helpers. */
namespace geo.Util {
    /** Adds one to a number. */
    facet AddOne(x: Long) => (y: Long)
}
"""


def _caps():
    return index_program(emit_dict(parse(_SRC)))


def test_indexes_facets_across_namespaces():
    caps = _caps()
    by_qn = {c.qualified_name: c for c in caps}
    assert "geo.Spatial.WithinDistance" in by_qn
    assert "geo.Spatial.Buffer" in by_qn
    assert "geo.Util.AddOne" in by_qn
    # Schemas are not facets.
    assert all("R" != c.name for c in caps)


def test_captures_purpose_params_returns_namespace_kind():
    wd = next(c for c in _caps() if c.name == "WithinDistance")
    assert wd.namespace == "geo.Spatial"
    assert wd.is_event is True
    assert wd.purpose == "Keeps subject features within distance of any reference feature."
    assert [(p.name, p.type) for p in wd.params] == [
        ("subject_path", "String"),
        ("reference_path", "String"),
        ("distance", "Double"),
        ("unit", "String"),
    ]
    # the unit param has a default
    assert next(p for p in wd.params if p.name == "unit").has_default is True
    assert [(r.name, r.type) for r in wd.returns] == [("result", "R")]

    addone = next(c for c in _caps() if c.name == "AddOne")
    assert addone.is_event is False


def test_search_by_query_ranks_name_and_purpose():
    caps = _caps()
    hits = search(caps, query="within")
    assert hits and hits[0].name == "WithinDistance"
    # purpose text is searchable too
    assert any(c.name == "Buffer" for c in search(caps, query="polygons"))


def test_search_and_semantics():
    caps = _caps()
    # both terms must hit: "buffer" + "feature" -> Buffer (purpose has "feature")
    hits = search(caps, query="buffer feature")
    assert [c.name for c in hits] == ["Buffer"]
    # a term that matches nothing kills the result
    assert search(caps, query="within nonexistentterm") == []


def test_filter_by_namespace_and_kind():
    caps = _caps()
    assert {c.name for c in search(caps, namespace="geo.Spatial")} == {"WithinDistance", "Buffer"}
    assert {c.name for c in search(caps, namespace="geo")} == {"WithinDistance", "Buffer", "AddOne"}
    assert {c.name for c in search(caps, kind="facet")} == {"AddOne"}
    assert {c.name for c in search(caps, kind="event_facet")} == {"WithinDistance", "Buffer"}


def test_signature_render():
    wd = next(c for c in _caps() if c.name == "WithinDistance")
    sig = wd.signature
    assert sig.startswith("event facet geo.Spatial.WithinDistance(")
    assert "=> (result: R)" in sig


# --- effect / cost annotations (item 7) ----------------------------------------

_ANNOT_SRC = """
namespace eng {
    /** Pure in-process transform. */
    event facet PureOp(p: String) => (out: String) with Effect(kind = "pure") with Cost(tier = "cheap")

    /** Hits an external engine. */
    event facet ExternalOp(p: String) => (out: String) with Effect(kind = "external")

    /** Big cached scan. */
    event facet ScanOp(region: String) => (out: String) with Timeout(minutes = 120)

    /** Unannotated. */
    event facet PlainOp(p: String) => (out: String)
}
"""


def _annot():
    return index_program(emit_dict(parse(_ANNOT_SRC)))


def test_effect_cost_parsed_from_mixins():
    by = {c.name: c for c in _annot()}
    assert (by["PureOp"].effect, by["PureOp"].cost) == ("pure", "cheap")
    assert by["ExternalOp"].effect == "external" and by["ExternalOp"].cost == ""
    assert by["PlainOp"].effect == "" and by["PlainOp"].cost == ""
    # mixin target names are captured (regression: was read from the wrong key)
    assert "Effect" in by["PureOp"].mixins and "Cost" in by["PureOp"].mixins
    assert by["PureOp"].to_dict()["effect"] == "pure"


def test_cost_inferred_from_timeout():
    scan = next(c for c in _annot() if c.name == "ScanOp")
    assert scan.cost == "expensive"  # 120 min -> expensive
    assert "Timeout" in scan.mixins


def test_search_effect_filter_excludes_unannotated():
    caps = _annot()
    pure = {c.name for c in search(caps, effect="pure")}
    assert pure == {"PureOp"}  # PlainOp (unknown effect) excluded
    assert {c.name for c in search(caps, effect="external")} == {"ExternalOp"}


def test_search_max_cost_keeps_cheap_and_unknown_drops_expensive():
    caps = _annot()
    names = {c.name for c in search(caps, max_cost="cheap")}
    assert "PureOp" in names  # cheap <= cheap
    assert "ScanOp" not in names  # expensive > cheap -> dropped
    assert "PlainOp" in names  # unknown cost passes the ceiling

"""The gallery's world low-zoom index: one level per page, gaps shown with reasons."""

from __future__ import annotations

from facetwork import mapserver as ms

STATUS = {
    "generated_at": "2026-10-02T12:00:00+00:00",
    "max_mb": 400,
    "continents": ["central-america", "europe"],
    "nodes": {
        "central-america": {
            "label": "Central America",
            "kind": "split",
            "state": "split",
            "children": ["central-america/haiti", "central-america/cuba"],
        },
        "central-america/haiti": {"label": "Haiti", "kind": "map", "state": "done", "mb": 70},
        "central-america/cuba": {
            "label": "Cuba",
            "kind": "map",
            "state": "failed",
            "mb": 70,
            "reason": "no routing graph was built",
        },
        "europe": {
            "label": "Europe",
            "kind": "split",
            "state": "split",
            "children": ["europe/france", "europe/monaco"],
        },
        "europe/france": {
            "label": "France",
            "kind": "split",
            "state": "split",
            "reason": "5,792 MB is over 400 MB",
            "children": ["europe/france/bretagne"],
        },
        "europe/france/bretagne": {
            "label": "Bretagne",
            "kind": "map",
            "state": "planned",
            "mb": 300,
        },
        "europe/monaco": {"label": "Monaco", "kind": "map", "state": "done", "mb": 1},
    },
}


def test_root_lists_continents_only_with_counts():
    code, html = ms.render_lz(STATUS, "/")
    assert code == 200
    assert "/lz/central-america/" in html and "/lz/europe/" in html
    assert "Haiti" not in html, "the root must not list countries"
    assert "2 of 4 maps built" in html


def test_a_country_page_lists_its_regions_and_links_finished_maps():
    _, html = ms.render_lz(STATUS, "/central-america/")
    assert f"/m/{ms.LZ_REL}central-america/haiti/" in html
    assert "no routing graph was built" in html, "a failure says why"
    _, html = ms.render_lz(STATUS, "/europe/")
    assert "/lz/europe/france/" in html and "0 of 1 maps built" in html


def test_unknown_region_and_missing_plan():
    assert ms.render_lz(STATUS, "/atlantis/")[0] == 404
    code, html = ms.render_lz(None, "/")
    assert code == 200 and "_status.json" in html


def test_world_maps_are_not_listed_in_the_flat_gallery():
    key = ms.PREFIX + ms.LZ_REL + "europe/monaco/index.html"
    assert ms._classify(key)[2].startswith(ms.LZ_REL)


def test_the_gallery_links_the_world_index_even_when_empty():
    assert 'href="/lz/"' in ms.render_gallery([])


def test_a_finished_map_carries_its_build_date_in_the_tag():
    st = {**STATUS, "nodes": {**STATUS["nodes"]}}
    st["nodes"]["central-america/haiti"] = {
        **st["nodes"]["central-america/haiti"],
        "built": "2026-10-07",
    }
    _, html = ms.render_lz(st, "/central-america/")
    assert "built 2026-10-07" in html
    # a failed map shows no date: it was never built
    assert html.count("2026-10-07") == 1

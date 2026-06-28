"""Tests for reuse-first catalog matching (CatalogService.match)."""

from __future__ import annotations

from facetwork.catalog.entities import STATUS_PUBLISHED, CatalogEntry, CatalogRevision
from facetwork.catalog.service import (
    CatalogService,
    _match_score,
    _match_verdict,
    _tokenize_request,
)

# --- pure helpers --------------------------------------------------------------


def test_tokenize_drops_filler_and_short_words():
    toks = _tokenize_request("Show me all the areas far from a supermarket")
    assert "areas" in toks and "far" in toks and "supermarket" in toks
    assert "the" not in toks and "me" not in toks and "all" not in toks
    # de-duplicated, lowercased
    assert _tokenize_request("Roads roads ROADS") == ["roads"]


def test_match_verdict_thresholds():
    assert _match_verdict(0.6) == "reuse"
    assert _match_verdict(0.45) == "review"
    assert _match_verdict(0.2) == "author_new"


def _entry(slug, title="", desc="", tags=None):
    return CatalogEntry(slug=slug, title=title, description=desc, tags=tags or [], latest_version=1)


def _rev(slug, summary="", facets=None, version=1, status=STATUS_PUBLISHED):
    return CatalogRevision(
        revision_id=f"{slug}-r{version}",
        slug=slug,
        version=version,
        content_hash="h",
        ffl_source="",
        flow_id="f",
        entry_workflow=f"x.{slug}",
        workflow_id="w",
        param_schema=[{"name": "region", "type": "String", "default": "California"}],
        facets_used=facets or [],
        status=status,
        summary=summary,
    )


def test_match_score_weights_summary_over_description():
    entry = _entry("x", title="X", desc="supermarket")
    rev_summary = _rev("x", summary="supermarket")
    rev_desc = CatalogRevision(
        revision_id="y",
        slug="x",
        version=1,
        content_hash="h",
        ffl_source="",
        flow_id="f",
        entry_workflow="x",
        workflow_id="w",
        summary="",
    )
    # term in the authoring summary scores higher than the same term only in description.
    s_summary, _ = _match_score(["supermarket"], _entry("x"), rev_summary)
    s_desc, _ = _match_score(["supermarket"], entry, rev_desc)
    assert s_summary > s_desc


# --- match() over a fake catalog ----------------------------------------------


class _FakeCatalog:
    def __init__(self, pairs):
        self._pairs = pairs  # list[(entry, [revs])]

    def list_entries(self):
        return [e for e, _ in self._pairs]

    def get_revisions_for_slug(self, slug):
        for e, revs in self._pairs:
            if e.slug == slug:
                return revs
        return []

    def get_revision_by_version(self, slug, version):
        for _e, revs in self._pairs:
            for r in revs:
                if r.slug == slug and r.version == version:
                    return r
        return None


def _service():
    pairs = [
        (
            _entry("food-deserts", title="Food deserts", tags=["spatial", "health"]),
            [
                _rev(
                    "food-deserts",
                    summary="Map the populated areas that lie beyond walking distance of a supermarket.",
                    facets=["osm.Spatial.BeyondDistance"],
                )
            ],
        ),
        (
            _entry("interstate-freeways", title="Interstate freeways", tags=["roads"]),
            [
                _rev(
                    "interstate-freeways",
                    summary="Map the California interstate freeway network from the road extracts.",
                    facets=["osm.Filters.FilterGeoJSONByTagPrefix"],
                )
            ],
        ),
    ]
    return CatalogService(_FakeCatalog(pairs), flow_store=None)


def test_match_reuses_on_strong_intent_overlap():
    res = _service().match("map areas beyond walking distance from a supermarket")
    assert res["verdict"] == "reuse"
    assert res["best"]["slug"] == "food-deserts"
    assert res["best"]["confidence"] >= 0.6
    # the candidate exposes the recorded intent + the params to fill
    assert "supermarket" in res["best"]["summary"]
    assert res["best"]["param_schema"][0]["name"] == "region"


def test_match_author_new_when_nothing_relevant():
    res = _service().match("compute tidal current forecasts for harbor buoys")
    assert res["verdict"] == "author_new"
    assert res["best"] is None and res["count"] == 0


def test_match_ranks_the_more_relevant_entry_first():
    res = _service().match("california freeway road network")
    assert res["candidates"][0]["slug"] == "interstate-freeways"


def test_match_kind_filter():
    res = _service().match("supermarket areas", kind="library")  # entries are workflows
    assert res["count"] == 0 and res["verdict"] == "author_new"

"""Gallery front-page listing: per-domain, skips the low-zoom tree, and says
when what it shows is incomplete."""

from __future__ import annotations

import datetime as dt

import pytest

from facetwork import mapserver as ms

T = dt.datetime(2026, 10, 22, tzinfo=dt.UTC)


class _Pager:
    def __init__(self, store):
        self.store = store

    def paginate(self, Bucket, Prefix, Delimiter=None):  # noqa: N803
        self.store.calls.append((Prefix, Delimiter))
        if any(Prefix.startswith(b) for b in self.store.broken):
            raise TimeoutError(f"read timeout listing {Prefix}")
        keys = sorted(k for k in self.store.keys if k.startswith(Prefix))
        if Delimiter:
            subs = sorted(
                {
                    Prefix + k[len(Prefix) :].split("/", 1)[0] + "/"
                    for k in keys
                    if "/" in k[len(Prefix) :]
                }
            )
            yield {"CommonPrefixes": [{"Prefix": p} for p in subs]}
        else:
            yield {"Contents": [{"Key": k, "Size": 10, "LastModified": T} for k in keys]}


class _S3:
    def __init__(self, keys, broken=()):
        self.keys, self.broken, self.calls = list(keys), list(broken), []

    def get_paginator(self, name):
        return _Pager(self)

    def head_object(self, Bucket, Key):  # noqa: N803
        if Key not in self.keys:
            raise KeyError(Key)


P = ms.PREFIX
KEYS = [
    f"{P}pds/maps/audit/index.html",
    f"{P}pds/output/audit/audit.json",
    f"{P}census-us/output/national/us-income/index.html",
    f"{P}osm/maps/lz/europe/malta/index.html",
    f"{P}osm/maps/us-z2-z4-lowzoom-tiled/index.html",
    f"{P}osm/geojson/asia/thailand-latest.geojson",
]


@pytest.fixture(autouse=True)
def _fresh_cache():
    ms._maps_cache.update(maps=None, ts=0.0, failed=[], error=None, refreshing=False)
    yield


def test_lists_each_domain_and_skips_low_zoom_tree():
    s3 = _S3(KEYS)
    maps = ms.list_maps(s3)
    assert {m["map_rel"] for m in maps} == {
        "pds/maps/audit",
        "census-us/output/national/us-income",
        "osm/maps/us-z2-z4-lowzoom-tiled",
    }
    listed = [p for p, _d in s3.calls]
    assert not any(p.startswith(f"{P}osm/maps/lz/") for p in listed)  # never walked
    assert f"{P}" not in [p for p, d in s3.calls if d is None]  # never all of PREFIX
    assert not any(p.startswith(f"{P}osm/geojson") for p in listed)  # caches untouched


def test_a_failing_domain_keeps_its_last_entries_and_is_named():
    ms._refresh_maps(_S3(KEYS))
    assert any(m["domain"] == "census-us" for m in ms._maps_cache["maps"])
    ms._refresh_maps(
        _S3(KEYS + [f"{P}pds/maps/coverage/mars/index.html"], broken=[f"{P}census-us/"])
    )
    rels = {m["map_rel"] for m in ms._maps_cache["maps"]}
    assert "pds/maps/coverage/mars" in rels  # the new map shows up
    assert "census-us/output/national/us-income" in rels  # kept as last seen
    notice = ms.listing_notice()
    assert "could not list census-us/" in notice and "last seen" in notice


def test_notice_is_empty_when_complete_and_recent_and_names_age_otherwise():
    ms._refresh_maps(_S3(KEYS))
    assert ms.listing_notice() == ""
    later = ms._maps_cache["ts"] + 3600
    assert "60 min ago" in ms.listing_notice(now=later)


def test_list_maps_raises_rather_than_return_a_partial_list():
    with pytest.raises(RuntimeError, match="census-us"):
        ms.list_maps(_S3(KEYS, broken=[f"{P}census-us/"]))


def test_map_exists_is_one_head_on_the_page():
    s3 = _S3(KEYS)
    assert ms._map_exists(s3, "pds/maps/audit")
    assert not ms._map_exists(s3, "pds/maps/nothing")
    assert not ms._map_exists(s3, "pds/../osm/maps/x")
    assert not ms._map_exists(s3, "pds/output")  # the domain itself is not a map page
    assert s3.calls == []  # no listing


def test_render_gallery_shows_the_notice():
    assert "STALE-NOTE" in ms.render_gallery([], notice="<p>STALE-NOTE</p>")

"""Tests for namespace-derived task-list routing."""

from __future__ import annotations

import pytest

from facetwork.runtime.task_list_routing import (
    DEFAULT_TASK_LIST,
    namespace_of,
    namespaces_for,
)


@pytest.mark.parametrize(
    "name,expected",
    [
        ("osm.cache.Download", "osm"),
        ("osm.AnalyzeRegion", "osm"),
        ("anthropic.Messages.Create", "anthropic"),
        ("census.acs.Fetch", "census"),
        ("", DEFAULT_TASK_LIST),
        ("osm", DEFAULT_TASK_LIST),  # unqualified (no dot) -> default
        ("fw:execute:osm.heatmap.X", DEFAULT_TASK_LIST),  # protocol task
        ("_fw_continue", DEFAULT_TASK_LIST),
    ],
)
def test_namespace_of(name, expected):
    assert namespace_of(name) == expected


def test_namespace_of_custom_default():
    assert namespace_of("Bare", default="") == ""
    assert namespace_of("fw:execute", default="shared") == "shared"


def test_namespaces_for_distinct_sorted():
    # A runner serving osm.* facets across sub-namespaces polls just "osm".
    names = [
        "osm.cache.Download",
        "osm.viz.RenderHeatmap",
        "osm.Region.ResolveRegions",
        "anthropic.Messages.Create",
        "Bare",  # unqualified -> contributes nothing
        "fw:execute",  # protocol -> contributes nothing
    ]
    assert namespaces_for(names) == ["anthropic", "osm"]


def test_namespaces_for_empty():
    assert namespaces_for([]) == []
    assert namespaces_for(None) == []


def test_producer_consumer_agree():
    # The intrinsic property: a task's list (producer) is always in the poll set
    # (consumer) of any runner that serves the facet — they can't desync.
    facet = "osm.cache.Download"
    task_list = namespace_of(facet)
    runner_lists = namespaces_for([facet, "osm.viz.RenderHeatmap"])
    assert task_list in runner_lists

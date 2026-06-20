"""Tests for facetwork.domains (entry-point + local discovery, domain: seeding)."""

from __future__ import annotations

import importlib.metadata

import pytest

from facetwork import domains as domains_mod
from facetwork.domains import (
    DomainPackage,
    discover_all_domains,
    discover_entry_point_domains,
    seed_domain_flows,
)


class _FakeEP:
    def __init__(self, name: str, target):
        self.name = name
        self._target = target

    def load(self):
        return self._target


def _patch_entry_points(monkeypatch, by_group: dict):
    """Patch importlib.metadata.entry_points to return per-group fakes."""
    monkeypatch.setattr(
        importlib.metadata,
        "entry_points",
        lambda group: by_group.get(group, []),
    )


class TestEntryPointDiscovery:
    def test_loads_domains_from_new_group(self, tmp_path, monkeypatch):
        target = DomainPackage(
            name="installed", ffl_dir=tmp_path, register_handlers=lambda r: None,
            source="entry_point",
        )
        _patch_entry_points(monkeypatch, {"facetwork.domains": [_FakeEP("installed", target)]})
        [pkg] = discover_entry_point_domains()
        assert pkg.name == "installed" and pkg.source == "entry_point"

    def test_dual_read_legacy_group(self, tmp_path, monkeypatch):
        """Transitional: a package still on facetwork.examples surfaces as a domain."""
        legacy = DomainPackage(
            name="oldpkg", ffl_dir=tmp_path, register_handlers=lambda r: None,
            source="entry_point",
        )
        _patch_entry_points(monkeypatch, {"facetwork.examples": [_FakeEP("oldpkg", legacy)]})
        names = [p.name for p in discover_entry_point_domains()]
        assert "oldpkg" in names

    def test_new_group_wins_dedupe(self, tmp_path, monkeypatch):
        new = DomainPackage(name="dup", ffl_dir=tmp_path, register_handlers=lambda r: None)
        old = DomainPackage(name="dup", ffl_dir=None, register_handlers=lambda r: None)
        _patch_entry_points(monkeypatch, {
            "facetwork.domains": [_FakeEP("dup", new)],
            "facetwork.examples": [_FakeEP("dup", old)],
        })
        pkgs = discover_entry_point_domains()
        assert len(pkgs) == 1 and pkgs[0].ffl_dir is not None  # the new-group one

    def test_skips_wrong_type(self, monkeypatch):
        _patch_entry_points(monkeypatch, {"facetwork.domains": [_FakeEP("bad", "not a Package")]})
        assert discover_entry_point_domains() == []

    def test_entry_point_wins_over_local_domains_dir(self, tmp_path, monkeypatch):
        # A local domains/<name> with a handlers pkg…
        d = tmp_path / "domains" / "shared" / "handlers"
        d.mkdir(parents=True)
        (d / "__init__.py").write_text("def register_all_registry_handlers(r):\n    pass\n")
        installed = DomainPackage(
            name="shared", ffl_dir=None, register_handlers=lambda r: None,
            source="entry_point",
        )
        _patch_entry_points(monkeypatch, {"facetwork.domains": [_FakeEP("shared", installed)]})
        [pkg] = discover_all_domains(tmp_path)
        assert pkg.source == "entry_point" and pkg.handlers_path is None


_SEED_FFL = """\
namespace seedy {
    event facet AddOne(value: Long) => (result: Long)

    workflow AddOneWorkflow(input: Long) => (output: Long) andThen {
        added = AddOne(value = $.input)
        yield AddOneWorkflow(output = added.result)
    }
}
"""


class _RecordingStore:
    def __init__(self):
        self.flows: list = []
        self.workflows: list = []

    def save_flow(self, flow):
        self.flows.append(flow)

    def save_workflow(self, wf):
        self.workflows.append(wf)


class TestSeedDomainFlows:
    def test_seeds_under_domain_prefix(self, tmp_path, monkeypatch):
        # Build a local domains/<name>/ffl/*.ffl; isolate from real entry points.
        _patch_entry_points(monkeypatch, {})
        ffl = tmp_path / "domains" / "seedy" / "ffl"
        ffl.mkdir(parents=True)
        (ffl / "seedy.ffl").write_text(_SEED_FFL)
        [pkg] = discover_all_domains(tmp_path)

        store = _RecordingStore()
        n_flows, n_workflows, _w = seed_domain_flows(pkg, store, replace=False)
        assert (n_flows, n_workflows) == (1, 1)
        assert store.flows[0].name.path == "domain:seedy"
        assert store.workflows[0].namespace_id == "domain:seedy"

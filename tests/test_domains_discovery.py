"""Tests for facetwork.domains (entry-point + local discovery, domain: seeding)."""

from __future__ import annotations

import importlib.metadata

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
            name="installed",
            ffl_dir=tmp_path,
            register_handlers=lambda r: None,
            source="entry_point",
        )
        _patch_entry_points(monkeypatch, {"facetwork.domains": [_FakeEP("installed", target)]})
        [pkg] = discover_entry_point_domains()
        assert pkg.name == "installed" and pkg.source == "entry_point"

    def test_ignores_legacy_group(self, tmp_path, monkeypatch):
        """Clean break: a package still on facetwork.examples is NOT a domain."""
        legacy = DomainPackage(
            name="oldpkg",
            ffl_dir=tmp_path,
            register_handlers=lambda r: None,
            source="entry_point",
        )
        _patch_entry_points(monkeypatch, {"facetwork.examples": [_FakeEP("oldpkg", legacy)]})
        assert discover_entry_point_domains() == []

    def test_skips_wrong_type(self, monkeypatch):
        _patch_entry_points(monkeypatch, {"facetwork.domains": [_FakeEP("bad", "not a Package")]})
        assert discover_entry_point_domains() == []

    def test_entry_point_wins_over_local_domains_dir(self, tmp_path, monkeypatch):
        # A local domains/<name> with a handlers pkg…
        d = tmp_path / "domains" / "shared" / "handlers"
        d.mkdir(parents=True)
        (d / "__init__.py").write_text("def register_all_registry_handlers(r):\n    pass\n")
        installed = DomainPackage(
            name="shared",
            ffl_dir=None,
            register_handlers=lambda r: None,
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


class TestPackageTopicGlobs:
    """package_topic_globs scopes a domain runner to its OWN facet namespaces
    (the fix for domain runners over-claiming every namespace's work, incl. heavy
    osm PBF, on any host)."""

    def _pkg(self, ffl_dir):
        return DomainPackage(
            name="d", ffl_dir=ffl_dir, register_handlers=lambda r: None, source="local"
        )

    def test_globs_are_event_facet_namespaces_only(self, tmp_path):
        from facetwork._pkg_discovery import package_topic_globs

        # Own event facet in `mydom`; a workflow-only `demo` ns and a `use`d
        # `other` ns must NOT widen the scope.
        (tmp_path / "prod.ffl").write_text(
            "namespace other { event facet Ext(a: Long) => (b: Long) }\n"
            "namespace mydom {\n"
            "    use other\n"
            "    event facet Work(x: Long) => (y: Long)\n"
            "    workflow Flow(x: Long) => (r: Long) andThen { s = Work(x = $.x) "
            "yield Flow(r = s.y) }\n"
            "}\n"
            "namespace demo {\n"
            "    workflow Only(x: Long) => (r: Long) andThen { yield Only(r = $.x) }\n"
            "}\n"
        )
        # a test fixture must be ignored
        (tmp_path / "tests" / "real" / "ffl").mkdir(parents=True)
        (tmp_path / "tests" / "real" / "ffl" / "t.ffl").write_text(
            "namespace throwaway { event facet T(x: Long) => (y: Long) }\n"
        )
        globs = package_topic_globs(self._pkg(tmp_path))
        # `mydom` + `other` (both declare event facets); NOT `demo` (workflow-only)
        # and NOT `throwaway` (test fixture).
        assert "mydom.*" in globs
        assert "demo.*" not in globs
        assert "throwaway.*" not in globs

    def test_no_ffl_returns_empty(self, tmp_path):
        from facetwork._pkg_discovery import package_topic_globs

        # Nested empty ffl dir so collect_ffl_files' parent-walk can't pick up
        # sibling pytest tmp dirs.
        ffl = tmp_path / "pkg" / "ffl"
        ffl.mkdir(parents=True)
        assert package_topic_globs(self._pkg(ffl)) == []

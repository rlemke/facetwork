"""Tests for catalog backup / restore / import (facetwork.catalog.backup)."""

import json

from facetwork.catalog import CatalogService, InMemoryCatalogStore, backup
from facetwork.runtime import MemoryStore

WF = """namespace claude.demo {
    workflow Adder(a: Int = 2, b: Int = 3) => (sum: Int) andThen {
        yield Adder(sum = $.a + $.b)
    }
}"""

LIB = """namespace claude.lib.geo {
    schema Pt { lat: Double, lon: Double }
    event facet Locate(name: String) => (point: Pt)
}"""

DEP_WF = """namespace claude.demo2 {
    use claude.lib.geo
    workflow Find(name: String = "x") => (lat: Double) andThen {
        p = Locate(name = $.name)
        yield Find(lat = p.point.lat)
    }
}"""


class _FlowStore(MemoryStore):
    """MemoryStore + in-memory flow/workflow CRUD (Mongo-only in prod)."""

    def __init__(self):
        super().__init__()
        self._flow_defs = {}
        self._wf_defs = {}

    def save_flow(self, flow):
        self._flow_defs[flow.uuid] = flow

    def get_flow(self, flow_id):
        return self._flow_defs.get(flow_id)

    def save_workflow(self, wf):
        self._wf_defs[wf.uuid] = wf

    def get_workflow(self, workflow_id):
        return self._wf_defs.get(workflow_id)

    def get_workflows_by_flow(self, flow_id):
        return [w for w in self._wf_defs.values() if w.flow_id == flow_id]


def _svc():
    return CatalogService(InMemoryCatalogStore(), _FlowStore())


def _seed_full(svc):
    """A library + a workflow depending on it + a standalone published workflow."""
    svc.save("lib.geo", kind="library", ffl_source=LIB)
    svc.publish("lib.geo")
    svc.save("demo.find", ffl_source=DEP_WF, depends_on=[{"slug": "lib.geo"}])
    svc.save("demo.adder", ffl_source=WF, title="Adder", tags=["math"])
    svc.publish("demo.adder")


def test_export_shape():
    svc = _svc()
    _seed_full(svc)
    data = backup.export(svc)
    assert data["format"] == "facetwork-catalog-backup"
    slugs = {e["slug"] for e in data["entries"]}
    assert slugs == {"lib.geo", "demo.find", "demo.adder"}
    # revisions carry the FFL + pins (the source of truth)
    adder = next(r for r in data["revisions"] if r["slug"] == "demo.adder")
    assert "namespace claude.demo" in adder["ffl_source"] and adder["status"] == "published"
    find = next(r for r in data["revisions"] if r["slug"] == "demo.find")
    assert find["depends_on"] and find["depends_on"][0]["slug"] == "lib.geo"


def test_backup_restore_roundtrip_into_fresh_db():
    src = _svc()
    _seed_full(src)
    data = backup.export(src)

    # Fresh, empty catalog + flow store (simulates a wiped dev DB).
    dst = _svc()
    res = backup.restore(data, dst)
    assert res["entries"] == 3 and res["rematerialized"] == 3 and not res["failed"]

    # Identity preserved: revision_id, version, content_hash, status, pins.
    src_adder = src._catalog.get_revision_by_version("demo.adder", 1)
    dst_adder = dst._catalog.get_revision_by_version("demo.adder", 1)
    assert dst_adder.revision_id == src_adder.revision_id
    assert dst_adder.content_hash == src_adder.content_hash
    assert dst_adder.status == "published"  # publish state survived
    dst_find = dst._catalog.get_revision_by_version("demo.find", 1)
    assert dst_find.depends_on[0].slug == "lib.geo"

    # Flows were rebuilt in the fresh DB and are runnable (publish gate honored).
    assert dst._flows.get_flow(dst_adder.flow_id) is not None
    run = dst.run("demo.adder", inputs={"a": 4, "b": 6})
    assert run["version"] == 1 and run["workflow"] == "claude.demo.Adder"


def test_restore_from_file(tmp_path):
    src = _svc()
    _seed_full(src)
    path = tmp_path / "catalog.json"
    summary = backup.export_to_file(src, path)
    assert summary["entries"] == 3
    assert json.loads(path.read_text())["format"] == "facetwork-catalog-backup"

    dst = _svc()
    res = backup.restore_from_file(path, dst)
    assert res["entries"] == 3 and res["rematerialized"] == 3
    assert dst.get("demo.adder")["status"] == "published"


def test_import_ffl_file(tmp_path):
    svc = _svc()
    f = tmp_path / "adder.ffl"
    f.write_text(WF)
    results = backup.import_files(svc, [str(f)], slug="demo.adder", tags=["math"], publish=True)
    assert len(results) == 1
    _, res = results[0]
    assert res.ok and res.is_valid and res.status == "published"
    # Imported and immediately runnable (published).
    assert svc.run("demo.adder", inputs={"a": 1, "b": 1})["version"] == 1


def test_import_directory_derives_slugs(tmp_path):
    svc = _svc()
    (tmp_path / "adder.ffl").write_text(WF)
    (tmp_path / "geo_lib.ffl").write_text(LIB)
    results = backup.import_files(svc, [str(tmp_path)], kind="workflow")
    imported = {res.slug for _, res in results}
    assert "adder" in imported and "geo-lib" in imported  # stem, _ -> -


def test_summary_survives_backup_restore():
    src = _svc()
    src.save("demo.adder", ffl_source=WF, summary="Adds two ints; from a calculator request.")
    src.publish("demo.adder")
    data = backup.export(src)
    dst = _svc()
    backup.restore(data, dst)
    assert "calculator request" in dst.get("demo.adder")["summary"]


def test_restore_is_idempotent():
    src = _svc()
    _seed_full(src)
    data = backup.export(src)
    dst = _svc()
    backup.restore(data, dst)
    r2 = backup.restore(data, dst)  # again
    assert r2["entries"] == 3
    # still exactly one revision per (slug, version)
    assert len(dst._catalog.get_revisions_for_slug("demo.adder")) == 1


# --- package import (one shared library flow + one entry per workflow) -------

PKG_FACETS = """namespace pkg.lib {
    event facet Greet(name: String) => (msg: String)
}"""

PKG_FLOWS = """namespace pkg.flows {
    use pkg.lib
    workflow Hello(name: String = "a") => (msg: String) andThen {
        g = Greet(name = $.name)
        yield Hello(msg = g.msg)
    }
    workflow Hola(name: String = "b") => (msg: String) andThen {
        g = Greet(name = $.name)
        yield Hola(msg = g.msg)
    }
}"""


def _write_pkg(tmp_path):
    # Two files that only compile together (cross-file `use`) — like a real package.
    (tmp_path / "facets.ffl").write_text(PKG_FACETS)
    (tmp_path / "flows.ffl").write_text(PKG_FLOWS)
    return str(tmp_path)


def test_import_package_one_library_many_thin_workflows(tmp_path):
    svc = _svc()
    results = backup.import_package(svc, ffl_dir=_write_pkg(tmp_path), lib_slug="pkg", tags=["t"])

    libname, libres = results[0]
    workflows = results[1:]
    assert libres.is_valid and svc._catalog.get_entry("pkg").kind == "library"
    assert {s for s, _ in workflows} == {"pkg.flows.Hello", "pkg.flows.Hola"}

    hello = svc._catalog.get_revision_by_version("pkg.flows.Hello", 1)
    hola = svc._catalog.get_revision_by_version("pkg.flows.Hola", 1)
    # Both are published, valid, and SHARE the library's single flow.
    assert hello.status == "published" and hola.status == "published"
    assert hello.flow_id == hola.flow_id == libres.flow_id
    assert hello.workflow_id != hola.workflow_id  # distinct workflows in that flow
    assert hello.ffl_source == "" and hello.depends_on[0].slug == "pkg"
    # Exactly one materialized flow for the whole package.
    assert len(svc._flows._flow_defs) == 1
    # A thin entry is runnable through the shared flow.
    run = svc.run("pkg.flows.Hello", inputs={"name": "z"})
    assert run["workflow"] == "pkg.flows.Hello"


def test_import_package_is_idempotent(tmp_path):
    svc = _svc()
    d = _write_pkg(tmp_path)
    backup.import_package(svc, ffl_dir=d, lib_slug="pkg")
    backup.import_package(svc, ffl_dir=d, lib_slug="pkg")  # again — no version churn
    assert len(svc._catalog.get_revisions_for_slug("pkg.flows.Hello")) == 1
    assert len(svc._flows._flow_defs) == 1


def test_import_package_backup_restore_keeps_one_flow(tmp_path):
    src = _svc()
    backup.import_package(src, ffl_dir=_write_pkg(tmp_path), lib_slug="pkg")
    data = backup.export(src)

    dst = _svc()
    res = backup.restore(data, dst)
    assert res["rematerialized"] == 3 and not res["failed"]  # 1 lib + 2 workflows
    # Restore rebuilt ONE shared flow, not one per workflow.
    assert len(dst._flows._flow_defs) == 1
    hello = dst._catalog.get_revision_by_version("pkg.flows.Hello", 1)
    hola = dst._catalog.get_revision_by_version("pkg.flows.Hola", 1)
    lib = dst._catalog.get_revision_by_version("pkg", 1)
    assert hello.flow_id == hola.flow_id == lib.flow_id
    assert hello.status == "published"
    assert dst.run("pkg.flows.Hola", inputs={"name": "q"})["workflow"] == "pkg.flows.Hola"


# ── import-all CLI subcommand (facetwork.catalog.cli._cmd_import_all) ──────────


def _import_all_args(**over):
    import argparse

    base = dict(tags="", only=[], exclude=[], dir=[], no_publish=False, list_only=False)
    base.update(over)
    return argparse.Namespace(**base)


def test_import_all_imports_dir_targets(tmp_path):
    from facetwork.catalog.cli import _cmd_import_all

    svc = _svc()
    pkg_dir = _write_pkg(tmp_path)
    # `only=["__none__"]` filters out every discovered in-repo package, leaving
    # just the explicit --dir target — keeps the test deterministic.
    rc = _cmd_import_all(svc, _import_all_args(only=["__none__"], dir=[pkg_dir]))
    assert rc == 0
    # The dir is imported as a package: a shared library + its workflows.
    libname = tmp_path.name
    assert svc._catalog.get_entry(libname) is not None
    assert svc._catalog.get_entry(libname).kind == "library"
    assert {s["slug"] for s in svc.list_all() if s["kind"] == "workflow"} == {
        "pkg.flows.Hello",
        "pkg.flows.Hola",
    }


def test_import_all_list_only_imports_nothing(tmp_path, capsys):
    from facetwork.catalog.cli import _cmd_import_all

    svc = _svc()
    rc = _cmd_import_all(svc, _import_all_args(dir=[_write_pkg(tmp_path)], list_only=True))
    assert rc == 0
    assert "would be imported" in capsys.readouterr().out
    assert svc.list_all() == []  # nothing written

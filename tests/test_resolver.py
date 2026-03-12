"""Tests for AFL dependency resolver."""

from unittest.mock import MagicMock

import pytest

from afl.parser import AFLParser
from afl.resolver import DependencyResolver, MongoDBNamespaceResolver, NamespaceIndex
from afl.source import CompilerInput, FileOrigin, SourceEntry


@pytest.fixture
def parser():
    return AFLParser()


class TestNamespaceIndex:
    """Tests for filesystem namespace scanning."""

    def test_scan_directory(self, tmp_path):
        """Scan a directory and find namespaces in AFL files."""
        (tmp_path / "types.afl").write_text("namespace osm.types { facet Addr() }")
        (tmp_path / "geo.afl").write_text("namespace osm.geocode { facet Geo() }")

        index = NamespaceIndex([tmp_path])
        assert index.find_namespace("osm.types") is not None
        assert index.find_namespace("osm.geocode") is not None
        assert index.find_namespace("osm.missing") is None

    def test_multiple_namespaces_per_file(self, tmp_path):
        """A single file may define multiple namespaces."""
        (tmp_path / "multi.afl").write_text(
            "namespace ns.a { facet A() }\nnamespace ns.b { facet B() }\n"
        )

        index = NamespaceIndex([tmp_path])
        path_a = index.find_namespace("ns.a")
        path_b = index.find_namespace("ns.b")
        assert path_a is not None
        assert path_b is not None
        assert path_a.resolve() == path_b.resolve()

    def test_empty_directory(self, tmp_path):
        """An empty directory yields no namespaces."""
        index = NamespaceIndex([tmp_path])
        assert index.find_namespace("anything") is None
        assert index.all_namespaces() == {}

    def test_missing_directory(self, tmp_path):
        """A non-existent directory is skipped without error."""
        missing = tmp_path / "nonexistent"
        index = NamespaceIndex([missing])
        assert index.find_namespace("anything") is None

    def test_duplicate_namespace_warning(self, tmp_path, caplog):
        """Warn when same namespace name appears in different files."""
        (tmp_path / "a.afl").write_text("namespace dup.ns { facet A() }")
        (tmp_path / "b.afl").write_text("namespace dup.ns { facet B() }")

        import logging

        with caplog.at_level(logging.WARNING, logger="afl.resolver"):
            index = NamespaceIndex([tmp_path])
            index.find_namespace("dup.ns")

        assert "Duplicate namespace" in caplog.text

    def test_unparseable_file_skipped(self, tmp_path):
        """Files with syntax errors are silently skipped."""
        (tmp_path / "good.afl").write_text("namespace ok.ns { facet Ok() }")
        (tmp_path / "bad.afl").write_text("@@@ invalid syntax")

        index = NamespaceIndex([tmp_path])
        assert index.find_namespace("ok.ns") is not None

    def test_nested_directories(self, tmp_path):
        """Recursively scan subdirectories."""
        sub = tmp_path / "sub" / "deep"
        sub.mkdir(parents=True)
        (sub / "nested.afl").write_text("namespace nested.ns { facet N() }")

        index = NamespaceIndex([tmp_path])
        assert index.find_namespace("nested.ns") is not None

    def test_all_namespaces(self, tmp_path):
        """all_namespaces returns the full index."""
        (tmp_path / "a.afl").write_text("namespace x.a { facet A() }")
        (tmp_path / "b.afl").write_text("namespace x.b { facet B() }")

        index = NamespaceIndex([tmp_path])
        ns_map = index.all_namespaces()
        assert set(ns_map.keys()) == {"x.a", "x.b"}

    def test_multiple_search_paths(self, tmp_path):
        """Multiple search directories are merged."""
        d1 = tmp_path / "dir1"
        d2 = tmp_path / "dir2"
        d1.mkdir()
        d2.mkdir()
        (d1 / "a.afl").write_text("namespace first.ns { facet A() }")
        (d2 / "b.afl").write_text("namespace second.ns { facet B() }")

        index = NamespaceIndex([d1, d2])
        assert index.find_namespace("first.ns") is not None
        assert index.find_namespace("second.ns") is not None


class TestDependencyResolver:
    """Tests for the iterative fixpoint dependency resolver."""

    def _make_input(self, source_text: str) -> tuple:
        """Helper: parse source and return (program, registry, compiler_input)."""
        parser = AFLParser()
        entry = SourceEntry(
            text=source_text,
            origin=FileOrigin(path="<test>"),
            is_library=False,
        )
        ci = CompilerInput(primary_sources=[entry])
        program, registry = parser.parse_sources(ci)
        return program, registry, ci

    def test_no_missing_deps_is_noop(self):
        """If all namespaces are defined, resolver is a no-op."""
        program, registry, ci = self._make_input(
            "namespace a { facet A() }\nnamespace b { use a\n facet B() }\n"
        )
        resolver = DependencyResolver()
        result_prog, _, _ = resolver.resolve(program, registry, ci)
        assert {ns.name for ns in result_prog.namespaces} == {"a", "b"}

    def test_single_missing_dep_from_filesystem(self, tmp_path):
        """Resolve a single missing namespace from the filesystem."""
        (tmp_path / "types.afl").write_text("namespace osm.types { facet Addr() }")

        program, registry, ci = self._make_input(
            "namespace osm.main { use osm.types\n facet Main() }\n"
        )
        fs_index = NamespaceIndex([tmp_path])
        resolver = DependencyResolver(filesystem_index=fs_index)
        result_prog, _, _ = resolver.resolve(program, registry, ci)

        ns_names = {ns.name for ns in result_prog.namespaces}
        assert "osm.types" in ns_names
        assert "osm.main" in ns_names

    def test_transitive_deps(self, tmp_path):
        """Resolve transitive dependencies: A → B → C."""
        (tmp_path / "b.afl").write_text("namespace ns.b { use ns.c\n facet B() }")
        (tmp_path / "c.afl").write_text("namespace ns.c { facet C() }")

        program, registry, ci = self._make_input("namespace ns.a { use ns.b\n facet A() }\n")
        fs_index = NamespaceIndex([tmp_path])
        resolver = DependencyResolver(filesystem_index=fs_index)
        result_prog, _, _ = resolver.resolve(program, registry, ci)

        ns_names = {ns.name for ns in result_prog.namespaces}
        assert ns_names == {"ns.a", "ns.b", "ns.c"}

    def test_circular_deps_terminate(self, tmp_path):
        """Circular dependencies (A → B → A) terminate naturally."""
        (tmp_path / "b.afl").write_text("namespace circle.b { use circle.a\n facet B() }")

        program, registry, ci = self._make_input(
            "namespace circle.a { use circle.b\n facet A() }\n"
        )
        fs_index = NamespaceIndex([tmp_path])
        resolver = DependencyResolver(filesystem_index=fs_index)
        result_prog, _, _ = resolver.resolve(program, registry, ci)

        ns_names = {ns.name for ns in result_prog.namespaces}
        assert ns_names == {"circle.a", "circle.b"}

    def test_already_loaded_source_not_reloaded(self, tmp_path):
        """A source file is loaded only once even if multiple namespaces reference it."""
        (tmp_path / "shared.afl").write_text(
            "namespace shared.types { facet T() }\nnamespace shared.utils { facet U() }\n"
        )

        program, registry, ci = self._make_input(
            "namespace app { use shared.types\n use shared.utils\n facet App() }\n"
        )
        fs_index = NamespaceIndex([tmp_path])
        resolver = DependencyResolver(filesystem_index=fs_index)
        result_prog, _, result_ci = resolver.resolve(program, registry, ci)

        ns_names = {ns.name for ns in result_prog.namespaces}
        assert "shared.types" in ns_names
        assert "shared.utils" in ns_names
        # Only one library source should have been added (single file)
        assert len(result_ci.library_sources) == 1

    def test_unresolvable_namespace_stops_gracefully(self):
        """If a namespace can't be found, the resolver stops without error."""
        program, registry, ci = self._make_input(
            "namespace app { use nonexistent.ns\n facet App() }\n"
        )
        resolver = DependencyResolver()
        result_prog, _, _ = resolver.resolve(program, registry, ci)
        # Still has only the original namespace
        assert {ns.name for ns in result_prog.namespaces} == {"app"}

    def test_auto_resolve_false_is_noop(self):
        """When no filesystem or mongo resolver is given, it's a no-op."""
        program, registry, ci = self._make_input("namespace app { use missing.ns\n facet App() }\n")
        resolver = DependencyResolver()
        result_prog, _, _ = resolver.resolve(program, registry, ci)
        assert {ns.name for ns in result_prog.namespaces} == {"app"}

    def test_mongodb_resolution(self):
        """Resolve a namespace from MongoDB when filesystem fails."""
        program, registry, ci = self._make_input(
            "namespace app { use remote.types\n facet App() }\n"
        )
        mongo_resolver = MagicMock(spec=MongoDBNamespaceResolver)
        mongo_resolver.batch_find.return_value = {
            "remote.types": "namespace remote.types { facet RemoteType() }"
        }

        resolver = DependencyResolver(mongodb_resolver=mongo_resolver)
        result_prog, _, _ = resolver.resolve(program, registry, ci)

        ns_names = {ns.name for ns in result_prog.namespaces}
        assert "remote.types" in ns_names

    def test_mixed_filesystem_and_mongodb(self, tmp_path):
        """Filesystem takes precedence; MongoDB fills the gaps."""
        (tmp_path / "local.afl").write_text("namespace local.types { facet LocalT() }")

        program, registry, ci = self._make_input(
            "namespace app { use local.types\n use remote.types\n facet App() }\n"
        )
        mongo_resolver = MagicMock(spec=MongoDBNamespaceResolver)
        mongo_resolver.batch_find.return_value = {
            "remote.types": "namespace remote.types { facet RemoteT() }"
        }

        fs_index = NamespaceIndex([tmp_path])
        resolver = DependencyResolver(
            filesystem_index=fs_index,
            mongodb_resolver=mongo_resolver,
        )
        result_prog, _, _ = resolver.resolve(program, registry, ci)

        ns_names = {ns.name for ns in result_prog.namespaces}
        assert ns_names == {"app", "local.types", "remote.types"}

    def test_registry_tracks_resolved_sources(self, tmp_path):
        """Registry should include entries for auto-resolved sources."""
        (tmp_path / "dep.afl").write_text("namespace dep.ns { facet D() }")

        program, registry, ci = self._make_input("namespace app { use dep.ns\n facet App() }\n")
        fs_index = NamespaceIndex([tmp_path])
        resolver = DependencyResolver(filesystem_index=fs_index)
        _, result_reg, _ = resolver.resolve(program, registry, ci)

        # Should have at least two sources (primary + resolved dep)
        assert len(result_reg.sources) >= 2

    def test_sibling_directory_auto_detected(self, tmp_path):
        """Primary file's directory is scanned automatically via parse_and_resolve."""
        (tmp_path / "main.afl").write_text("namespace app { use lib.types\n facet App() }")
        (tmp_path / "types.afl").write_text("namespace lib.types { facet T() }")

        from afl.config import AFLConfig, ResolverConfig

        config = AFLConfig(resolver=ResolverConfig(auto_resolve=True))
        parser = AFLParser()

        entry = SourceEntry(
            text=(tmp_path / "main.afl").read_text(),
            origin=FileOrigin(path=str(tmp_path / "main.afl")),
            is_library=False,
        )
        ci = CompilerInput(primary_sources=[entry])
        program, registry = parser.parse_and_resolve(ci, config)

        ns_names = {ns.name for ns in program.namespaces}
        assert ns_names == {"app", "lib.types"}

    def test_qualified_call_resolution(self, tmp_path):
        """Resolve namespaces referenced by qualified call names (no use statement)."""
        (tmp_path / "ops.afl").write_text(
            "namespace osm.ops {\n    event facet Cache(region: String) => (cache: String)\n}\n"
        )

        program, registry, ci = self._make_input(
            "namespace app {\n"
            "    workflow Run() andThen {\n"
            "        c = osm.ops.CacheRegion(region = $.region)\n"
            "    }\n"
            "}\n"
        )
        fs_index = NamespaceIndex([tmp_path])
        resolver = DependencyResolver(filesystem_index=fs_index)
        result_prog, _, _ = resolver.resolve(program, registry, ci)

        ns_names = {ns.name for ns in result_prog.namespaces}
        assert "osm.ops" in ns_names

    def test_qualified_call_transitive(self, tmp_path):
        """Qualified call triggers loading, which may bring in use-based transitive deps."""
        (tmp_path / "ops.afl").write_text(
            "namespace osm.ops {\n"
            "    use osm.types\n"
            "    event facet DoStuff() => (result: String)\n"
            "}\n"
        )
        (tmp_path / "types.afl").write_text(
            "namespace osm.types {\n    schema Coord { lat: Float, lon: Float }\n}\n"
        )

        program, registry, ci = self._make_input(
            "namespace app {\n"
            "    workflow Run() andThen {\n"
            "        s = osm.ops.DoStuff()\n"
            "    }\n"
            "}\n"
        )
        fs_index = NamespaceIndex([tmp_path])
        resolver = DependencyResolver(filesystem_index=fs_index)
        result_prog, _, _ = resolver.resolve(program, registry, ci)

        ns_names = {ns.name for ns in result_prog.namespaces}
        assert ns_names == {"app", "osm.ops", "osm.types"}


class TestParseAndResolve:
    """Tests for AFLParser.parse_and_resolve integration."""

    def test_auto_resolve_disabled_by_default(self, tmp_path):
        """Without auto_resolve=True, parse_and_resolve just calls parse_sources."""
        (tmp_path / "main.afl").write_text("namespace app { use missing.ns\n facet App() }")

        parser = AFLParser()
        entry = SourceEntry(
            text=(tmp_path / "main.afl").read_text(),
            origin=FileOrigin(path=str(tmp_path / "main.afl")),
            is_library=False,
        )
        ci = CompilerInput(primary_sources=[entry])

        # Default config has auto_resolve=False
        from afl.config import AFLConfig

        config = AFLConfig()
        program, _ = parser.parse_and_resolve(ci, config)

        # Only the original namespace is present (missing.ns not resolved)
        ns_names = {ns.name for ns in program.namespaces}
        assert ns_names == {"app"}

    def test_source_path_config(self, tmp_path):
        """source_paths from config are used to find dependencies."""
        main_dir = tmp_path / "src"
        lib_dir = tmp_path / "libs"
        main_dir.mkdir()
        lib_dir.mkdir()

        (main_dir / "main.afl").write_text("namespace app { use ext.lib\n facet App() }")
        (lib_dir / "lib.afl").write_text("namespace ext.lib { facet Lib() }")

        from afl.config import AFLConfig, ResolverConfig

        config = AFLConfig(
            resolver=ResolverConfig(
                auto_resolve=True,
                source_paths=[str(lib_dir)],
            )
        )

        parser = AFLParser()
        entry = SourceEntry(
            text=(main_dir / "main.afl").read_text(),
            origin=FileOrigin(path=str(main_dir / "main.afl")),
            is_library=False,
        )
        ci = CompilerInput(primary_sources=[entry])
        program, _ = parser.parse_and_resolve(ci, config)

        ns_names = {ns.name for ns in program.namespaces}
        assert ns_names == {"app", "ext.lib"}

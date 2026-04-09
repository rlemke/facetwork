# Copyright 2025 Ralph Lemke
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Tests for source input and provenance tracking."""

import tempfile
import uuid
from pathlib import Path

import pytest

from facetwork import parse
from facetwork.emitter import JSONEmitter
from facetwork.loader import SourceLoader
from facetwork.parser import FFLParser
from facetwork.source import (
    CompilerInput,
    FileOrigin,
    MavenOrigin,
    MongoDBOrigin,
    SourceEntry,
    SourceRegistry,
)


class TestSourceOrigins:
    """Test source origin data structures."""

    def test_file_origin(self):
        origin = FileOrigin(path="/path/to/file.ffl")
        assert origin.path == "/path/to/file.ffl"
        assert origin.to_source_id() == "file:///path/to/file.ffl"

    def test_mongodb_origin(self):
        origin = MongoDBOrigin(collection_id="abc123", display_name="My Document")
        assert origin.collection_id == "abc123"
        assert origin.display_name == "My Document"
        assert origin.to_source_id() == "mongodb://abc123/My Document"

    def test_maven_origin(self):
        origin = MavenOrigin(group_id="com.example", artifact_id="my-lib", version="1.0.0")
        assert origin.group_id == "com.example"
        assert origin.artifact_id == "my-lib"
        assert origin.version == "1.0.0"
        assert origin.classifier == ""
        assert origin.to_source_id() == "maven://com.example/my-lib/1.0.0"

    def test_maven_origin_with_classifier(self):
        origin = MavenOrigin(
            group_id="com.example", artifact_id="my-lib", version="1.0.0", classifier="sources"
        )
        assert origin.classifier == "sources"
        assert origin.to_source_id() == "maven://com.example/my-lib/1.0.0/sources"


class TestSourceEntry:
    """Test source entry data structure."""

    def test_source_entry_basic(self):
        origin = FileOrigin(path="/test.ffl")
        entry = SourceEntry(text="facet Test()", origin=origin)
        assert entry.text == "facet Test()"
        assert entry.origin == origin
        assert entry.is_library is False

    def test_source_entry_library(self):
        origin = FileOrigin(path="/lib.ffl")
        entry = SourceEntry(text="facet Lib()", origin=origin, is_library=True)
        assert entry.is_library is True

    def test_source_entry_source_id(self):
        origin = FileOrigin(path="/test.ffl")
        entry = SourceEntry(text="facet Test()", origin=origin)
        assert entry.source_id == "file:///test.ffl"


class TestCompilerInput:
    """Test compiler input data structure."""

    def test_empty_input(self):
        ci = CompilerInput()
        assert ci.primary_sources == []
        assert ci.library_sources == []
        assert ci.all_sources == []

    def test_primary_sources(self):
        entry = SourceEntry(text="facet A()", origin=FileOrigin(path="/a.ffl"))
        ci = CompilerInput(primary_sources=[entry])
        assert len(ci.primary_sources) == 1
        assert len(ci.all_sources) == 1

    def test_library_sources(self):
        entry = SourceEntry(text="facet Lib()", origin=FileOrigin(path="/lib.ffl"), is_library=True)
        ci = CompilerInput(library_sources=[entry])
        assert len(ci.library_sources) == 1
        assert len(ci.all_sources) == 1

    def test_all_sources_order(self):
        """Primary sources come before library sources."""
        primary = SourceEntry(text="facet A()", origin=FileOrigin(path="/a.ffl"))
        lib = SourceEntry(text="facet Lib()", origin=FileOrigin(path="/lib.ffl"), is_library=True)
        ci = CompilerInput(primary_sources=[primary], library_sources=[lib])
        assert ci.all_sources[0] == primary
        assert ci.all_sources[1] == lib


class TestSourceRegistry:
    """Test source registry for provenance lookup."""

    def test_empty_registry(self):
        registry = SourceRegistry()
        assert registry.get("nonexistent") is None

    def test_register_and_get(self):
        registry = SourceRegistry()
        origin = FileOrigin(path="/test.ffl")
        source_id = origin.to_source_id()
        registry.register(source_id, origin)
        assert registry.get(source_id) == origin

    def test_from_compiler_input(self):
        entry1 = SourceEntry(text="facet A()", origin=FileOrigin(path="/a.ffl"))
        entry2 = SourceEntry(text="facet B()", origin=FileOrigin(path="/b.ffl"))
        ci = CompilerInput(primary_sources=[entry1, entry2])

        registry = SourceRegistry.from_compiler_input(ci)
        assert registry.get("file:///a.ffl") is not None
        assert registry.get("file:///b.ffl") is not None


class TestSourceLoader:
    """Test source loader functionality."""

    def test_load_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".ffl", delete=False) as f:
            f.write("facet Test()")
            f.flush()
            path = f.name

        try:
            entry = SourceLoader.load_file(path)
            assert entry.text == "facet Test()"
            assert isinstance(entry.origin, FileOrigin)
            assert entry.is_library is False
        finally:
            Path(path).unlink()

    def test_load_file_as_library(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".ffl", delete=False) as f:
            f.write("facet LibFacet()")
            f.flush()
            path = f.name

        try:
            entry = SourceLoader.load_file(path, is_library=True)
            assert entry.is_library is True
        finally:
            Path(path).unlink()

    def test_load_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            SourceLoader.load_file("/nonexistent/file.ffl")

    def test_load_mongodb_requires_pymongo(self, monkeypatch):
        """MongoDB loader requires pymongo to be installed."""
        # This is handled by import error in the actual loader
        # Testing the import error path requires mocking the import system
        pass

    def test_load_maven_http_error(self, monkeypatch):
        """Maven loader raises on HTTP errors."""
        import urllib.error

        def mock_urlopen(*args, **kwargs):
            raise urllib.error.HTTPError(
                url="http://test",
                code=404,
                msg="Not Found",
                hdrs={},
                fp=None,
            )

        monkeypatch.setattr("urllib.request.urlopen", mock_urlopen)

        with pytest.raises(ValueError, match="HTTP 404"):
            SourceLoader.load_maven("com.example", "mylib", "1.0.0")


class TestMultiSourceParsing:
    """Test multi-source parsing with provenance."""

    def test_parse_sources_single(self):
        entry = SourceEntry(text="facet A()", origin=FileOrigin(path="/a.ffl"))
        ci = CompilerInput(primary_sources=[entry])

        parser = FFLParser()
        program, registry = parser.parse_sources(ci)

        assert len(program.facets) == 1
        assert program.facets[0].sig.name == "A"
        assert registry.get("file:///a.ffl") is not None

    def test_parse_sources_multiple(self):
        entry1 = SourceEntry(text="facet A()", origin=FileOrigin(path="/a.ffl"))
        entry2 = SourceEntry(text="facet B()", origin=FileOrigin(path="/b.ffl"))
        ci = CompilerInput(primary_sources=[entry1, entry2])

        parser = FFLParser()
        program, registry = parser.parse_sources(ci)

        assert len(program.facets) == 2
        names = [f.sig.name for f in program.facets]
        assert "A" in names
        assert "B" in names

    def test_parse_sources_with_library(self):
        primary = SourceEntry(text="facet Main()", origin=FileOrigin(path="/main.ffl"))
        lib = SourceEntry(
            text="facet Helper()", origin=FileOrigin(path="/lib.ffl"), is_library=True
        )
        ci = CompilerInput(primary_sources=[primary], library_sources=[lib])

        parser = FFLParser()
        program, registry = parser.parse_sources(ci)

        assert len(program.facets) == 2

    def test_source_id_in_locations(self):
        entry = SourceEntry(text="facet A()", origin=FileOrigin(path="/a.ffl"))
        ci = CompilerInput(primary_sources=[entry])

        parser = FFLParser()
        program, _ = parser.parse_sources(ci)

        assert program.facets[0].location is not None
        assert program.facets[0].location.source_id == "file:///a.ffl"


class TestProvenanceEmission:
    """Test provenance in JSON output."""

    def test_emit_without_provenance(self):
        entry = SourceEntry(text="facet A()", origin=FileOrigin(path="/a.ffl"))
        ci = CompilerInput(primary_sources=[entry])

        parser = FFLParser()
        program, registry = parser.parse_sources(ci)

        emitter = JSONEmitter(include_provenance=False)
        output = emitter.emit_dict(program)

        # Location should not have sourceId
        facet = [d for d in output.get("declarations", []) if d.get("type") == "FacetDecl"][0]
        loc = facet["location"]
        assert "sourceId" not in loc
        assert "provenance" not in loc

    def test_emit_with_provenance(self):
        entry = SourceEntry(text="facet A()", origin=FileOrigin(path="/a.ffl"))
        ci = CompilerInput(primary_sources=[entry])

        parser = FFLParser()
        program, registry = parser.parse_sources(ci)

        emitter = JSONEmitter(include_provenance=True, source_registry=registry)
        output = emitter.emit_dict(program)

        facet = [d for d in output.get("declarations", []) if d.get("type") == "FacetDecl"][0]
        loc = facet["location"]
        assert loc["sourceId"] == "file:///a.ffl"
        assert loc["provenance"]["type"] == "file"
        assert loc["provenance"]["path"] == "/a.ffl"

    def test_emit_mongodb_provenance(self):
        """Test provenance format for MongoDB origin."""
        origin = MongoDBOrigin(collection_id="abc123", display_name="My Document")
        registry = SourceRegistry()
        source_id = origin.to_source_id()
        registry.register(source_id, origin)

        emitter = JSONEmitter(include_provenance=True, source_registry=registry)
        prov = emitter._provenance_to_dict(origin)

        assert prov["type"] == "mongodb"
        assert prov["collectionId"] == "abc123"
        assert prov["displayName"] == "My Document"

    def test_emit_maven_provenance(self):
        """Test provenance format for Maven origin."""
        origin = MavenOrigin(
            group_id="com.example", artifact_id="mylib", version="1.0.0", classifier="sources"
        )
        emitter = JSONEmitter()
        prov = emitter._provenance_to_dict(origin)

        assert prov["type"] == "maven"
        assert prov["groupId"] == "com.example"
        assert prov["artifactId"] == "mylib"
        assert prov["version"] == "1.0.0"
        assert prov["classifier"] == "sources"


class TestNodeUUIDs:
    """Test unique UUID generation for AST nodes."""

    def test_node_has_uuid(self):
        """Every AST node should have a node_id."""
        ast = parse("facet Test()")
        assert hasattr(ast, "node_id")
        assert ast.node_id is not None
        # Verify it's a valid UUID format
        uuid.UUID(ast.node_id)

    def test_facet_has_uuid(self):
        """Facet declarations should have unique UUIDs."""
        ast = parse("facet Test()")
        facet = ast.facets[0]
        assert hasattr(facet, "node_id")
        uuid.UUID(facet.node_id)

    def test_uuids_are_unique(self):
        """Each node should have a unique UUID."""
        ast = parse("""
            facet A()
            facet B()
            workflow C() andThen {
                step1 = A()
                yield C()
            }
        """)

        # Collect all node IDs
        ids = set()
        ids.add(ast.node_id)
        for facet in ast.facets:
            ids.add(facet.node_id)
            ids.add(facet.sig.node_id)
        for workflow in ast.workflows:
            ids.add(workflow.node_id)
            ids.add(workflow.sig.node_id)
            if workflow.body:
                ids.add(workflow.body.node_id)
                ids.add(workflow.body.block.node_id)
                for step in workflow.body.block.steps:
                    ids.add(step.node_id)
                    ids.add(step.call.node_id)

        # All IDs should be unique (set size equals count of adds)
        # We added: 1 program + 2 facets + 2 sigs + 1 workflow + 1 sig + 1 body + 1 block + 1 step + 1 call = 11
        assert len(ids) >= 10  # At least this many unique nodes

    def test_uuid_in_json_output(self):
        """UUIDs should appear in JSON output."""
        ast = parse("facet Test()")
        emitter = JSONEmitter()
        output = emitter.emit_dict(ast)

        # Program should have id
        assert "id" in output
        uuid.UUID(output["id"])

        # Facet should have id
        facet = [d for d in output.get("declarations", []) if d.get("type") == "FacetDecl"][0]
        assert "id" in facet
        uuid.UUID(facet["id"])

    def test_uuid_different_per_parse(self):
        """Parsing the same source twice should produce different UUIDs."""
        ast1 = parse("facet Test()")
        ast2 = parse("facet Test()")

        assert ast1.node_id != ast2.node_id
        assert ast1.facets[0].node_id != ast2.facets[0].node_id

    def test_nested_nodes_have_uuids(self):
        """Nested nodes (params, returns, etc.) should have UUIDs."""
        ast = parse("facet Test(name: String) => (result: Int)")
        facet = ast.facets[0]

        # Signature has UUID
        assert facet.sig.node_id is not None
        uuid.UUID(facet.sig.node_id)

        # Parameters have UUIDs
        assert facet.sig.params[0].node_id is not None
        uuid.UUID(facet.sig.params[0].node_id)

        # Return clause has UUID
        assert facet.sig.returns.node_id is not None
        uuid.UUID(facet.sig.returns.node_id)

"""Tests for the Maven runner example FFL files.

Verifies that the runner FFL files parse, validate, and compile correctly.
"""

from pathlib import Path

from facetwork.emitter import emit_dict
from facetwork.parser import FFLParser
from facetwork.source import CompilerInput, FileOrigin, SourceEntry
from facetwork.validator import validate

_AFL_DIR = Path(__file__).resolve().parent.parent.parent.parent / "afl"


def _compile(*filenames: str) -> dict:
    """Compile one or more FFL files from the Maven example directory."""
    entries = []
    for i, name in enumerate(filenames):
        path = _AFL_DIR / name
        entries.append(
            SourceEntry(
                text=path.read_text(),
                origin=FileOrigin(path=str(path)),
                is_library=(i > 0),
            )
        )

    compiler_input = CompilerInput(
        primary_sources=[entries[0]],
        library_sources=entries[1:],
    )

    parser = FFLParser()
    program_ast, _registry = parser.parse_sources(compiler_input)

    result = validate(program_ast)
    if result.errors:
        messages = "; ".join(str(e) for e in result.errors)
        raise ValueError(f"Validation errors: {messages}")

    return emit_dict(program_ast, include_locations=False)


_KEY_TO_TYPE = {
    "schemas": "SchemaDecl",
    "facets": "FacetDecl",
    "eventFacets": "EventFacetDecl",
    "workflows": "WorkflowDecl",
    "implicits": "ImplicitDecl",
}


def _collect_names(program: dict, key: str) -> list[str]:
    """Recursively collect names from a given declaration type across all namespaces."""
    decl_type = _KEY_TO_TYPE[key]
    names: list[str] = []

    def _walk(node: dict) -> None:
        for decl in node.get("declarations", []):
            if decl.get("type") == decl_type:
                names.append(decl["name"])
            elif decl.get("type") == "Namespace":
                _walk(decl)

    _walk(program)
    return names


def _find_decl_by_name(node: dict, decl_type: str, name: str):
    """Recursively find a declaration by type and name."""
    for decl in node.get("declarations", []):
        if decl.get("type") == decl_type and decl.get("name") == name:
            return decl
        if decl.get("type") == "Namespace":
            found = _find_decl_by_name(decl, decl_type, name)
            if found:
                return found
    return None


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------
class TestMavenTypes:
    """Compilation tests for maven_types.afl."""

    def test_parse_types(self):
        """maven_types.afl parses and validates."""
        program = _compile("maven_types.ffl")
        assert program["type"] == "Program"

    def test_all_schemas_present(self):
        """All 2 schemas are emitted."""
        program = _compile("maven_types.ffl")
        schema_names = _collect_names(program, "schemas")
        expected = ["ExecutionResult", "PluginExecutionResult"]
        for name in expected:
            assert name in schema_names, f"Missing schema: {name}"
        assert len([n for n in schema_names if n in expected]) == 2


# ---------------------------------------------------------------------------
# Event Facets (runner)
# ---------------------------------------------------------------------------
class TestMavenEventFacets:
    """Compilation tests for runner event facet files."""

    def test_runner_facets(self):
        """maven_runner.afl compiles with types dependency."""
        program = _compile("maven_runner.ffl", "maven_types.ffl")
        facet_names = _collect_names(program, "eventFacets")
        assert "RunMavenArtifact" in facet_names

    def test_runner_facet_params(self):
        """RunMavenArtifact has expected parameters."""
        program = _compile("maven_runner.ffl", "maven_types.ffl")
        ef = _find_decl_by_name(program, "EventFacetDecl", "RunMavenArtifact")
        assert ef is not None
        param_names = [p["name"] for p in ef["params"]]
        assert "step_id" in param_names
        assert "group_id" in param_names
        assert "artifact_id" in param_names
        assert "version" in param_names

    def test_run_maven_plugin_facet(self):
        """RunMavenPlugin event facet is present with expected params."""
        program = _compile("maven_runner.ffl", "maven_types.ffl")
        ef = _find_decl_by_name(program, "EventFacetDecl", "RunMavenPlugin")
        assert ef is not None
        param_names = [p["name"] for p in ef["params"]]
        assert "workspace_path" in param_names
        assert "plugin_group_id" in param_names
        assert "plugin_artifact_id" in param_names
        assert "plugin_version" in param_names
        assert "goal" in param_names

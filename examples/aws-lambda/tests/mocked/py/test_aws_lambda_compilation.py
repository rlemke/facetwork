"""Tests for the AWS Lambda + Step Functions example FFL files.

Verifies that all 5 FFL files parse, validate, and compile correctly,
and that the 4 workflows using mixin composition compile with
all dependencies.
"""

from pathlib import Path

from facetwork.cli import main
from facetwork.emitter import emit_dict
from facetwork.parser import FFLParser
from facetwork.source import CompilerInput, FileOrigin, SourceEntry
from facetwork.validator import validate

_AFL_DIR = Path(__file__).resolve().parent.parent.parent.parent / "afl"


def _compile(*filenames: str) -> dict:
    """Compile one or more FFL files from the AWS Lambda example directory."""
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


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------
class TestAwsLambdaTypes:
    """Compilation tests for lambda_types.afl."""

    def test_parse_types(self):
        """lambda_types.afl parses and validates."""
        program = _compile("lambda_types.ffl")
        assert program["type"] == "Program"

    def test_all_schemas_present(self):
        """All 7 schemas are emitted."""
        program = _compile("lambda_types.ffl")
        schema_names = _collect_names(program, "schemas")
        expected = [
            "FunctionConfig",
            "InvokeResult",
            "FunctionInfo",
            "LayerInfo",
            "StateMachineConfig",
            "ExecutionResult",
            "ExecutionInfo",
        ]
        for name in expected:
            assert name in schema_names, f"Missing schema: {name}"
        assert len([n for n in schema_names if n in expected]) == 7


# ---------------------------------------------------------------------------
# Mixins
# ---------------------------------------------------------------------------
class TestAwsLambdaMixins:
    """Compilation tests for lambda_mixins.afl."""

    def test_parse_mixins(self):
        """lambda_mixins.afl parses and validates."""
        program = _compile("lambda_mixins.ffl")
        assert program["type"] == "Program"

    def test_mixin_facets_present(self):
        """All 6 mixin facets are emitted."""
        program = _compile("lambda_mixins.ffl")
        facet_names = _collect_names(program, "facets")
        expected = ["Retry", "Timeout", "DLQ", "VpcConfig", "Tracing", "MemorySize"]
        for name in expected:
            assert name in facet_names, f"Missing mixin facet: {name}"

    def test_implicits_present(self):
        """All 3 implicit declarations are emitted."""
        program = _compile("lambda_mixins.ffl")
        implicit_names = _collect_names(program, "implicits")
        expected = ["defaultRetry", "defaultTimeout", "defaultTracing"]
        for name in expected:
            assert name in implicit_names, f"Missing implicit: {name}"


# ---------------------------------------------------------------------------
# Event Facets (domain files)
# ---------------------------------------------------------------------------
class TestAwsLambdaEventFacets:
    """Compilation tests for domain event facet files."""

    def test_lambda_facets(self):
        """lambda_functions.afl compiles with types dependency."""
        program = _compile("lambda_functions.ffl", "lambda_types.ffl")
        facet_names = _collect_names(program, "eventFacets")
        expected = [
            "CreateFunction",
            "InvokeFunction",
            "UpdateFunctionCode",
            "DeleteFunction",
            "ListFunctions",
            "GetFunctionInfo",
            "PublishLayer",
        ]
        for name in expected:
            assert name in facet_names, f"Missing lambda facet: {name}"
        assert len([n for n in facet_names if n in expected]) == 7

    def test_stepfunctions_facets(self):
        """lambda_stepfunctions.afl compiles with types dependency."""
        program = _compile("lambda_stepfunctions.ffl", "lambda_types.ffl")
        facet_names = _collect_names(program, "eventFacets")
        expected = [
            "CreateStateMachine",
            "StartExecution",
            "DescribeExecution",
            "DeleteStateMachine",
            "ListExecutions",
        ]
        for name in expected:
            assert name in facet_names, f"Missing stepfunctions facet: {name}"
        assert len([n for n in facet_names if n in expected]) == 5


# ---------------------------------------------------------------------------
# Workflows
# ---------------------------------------------------------------------------
class TestAwsLambdaWorkflows:
    """Compilation tests for lambda_workflows.afl with mixin composition."""

    _DEPS = [
        "lambda_types.ffl",
        "lambda_mixins.ffl",
        "lambda_functions.ffl",
        "lambda_stepfunctions.ffl",
    ]

    def _compile_workflows(self) -> dict:
        return _compile("lambda_workflows.ffl", *self._DEPS)

    def test_workflows_compile(self):
        """lambda_workflows.afl compiles with all dependencies."""
        program = self._compile_workflows()
        assert program["type"] == "Program"

    def test_all_workflows_present(self):
        """All 4 workflow names are emitted."""
        program = self._compile_workflows()
        wf_names = _collect_names(program, "workflows")
        expected = [
            "DeployAndInvoke",
            "BlueGreenDeploy",
            "StepFunctionPipeline",
            "BatchProcessor",
        ]
        for name in expected:
            assert name in wf_names, f"Missing workflow: {name}"

    def test_deploy_and_invoke_steps(self):
        """DeployAndInvoke has the expected step names."""
        program = self._compile_workflows()
        wf = self._find_workflow(program, "DeployAndInvoke")
        assert wf is not None
        step_names = [s["name"] for s in wf["body"]["steps"]]
        assert "created" in step_names
        assert "invoked" in step_names
        assert "info" in step_names

    def test_batch_processor_foreach(self):
        """BatchProcessor uses foreach iteration."""
        program = self._compile_workflows()
        wf = self._find_workflow(program, "BatchProcessor")
        assert wf is not None
        body = wf["body"]
        foreach = body.get("foreach")
        assert foreach is not None
        assert foreach["variable"] == "item"

    def test_step_function_pipeline_steps(self):
        """StepFunctionPipeline has cross-namespace steps."""
        program = self._compile_workflows()
        wf = self._find_workflow(program, "StepFunctionPipeline")
        assert wf is not None
        step_names = [s["name"] for s in wf["body"]["steps"]]
        assert "fn" in step_names
        assert "sm" in step_names
        assert "exec" in step_names
        assert "result" in step_names

    def test_cli_check_workflows(self):
        """The CLI --check flag succeeds for lambda_workflows.afl."""
        args = [
            "--primary",
            str(_AFL_DIR / "lambda_workflows.ffl"),
        ]
        for dep in self._DEPS:
            args.extend(["--library", str(_AFL_DIR / dep)])
        args.append("--check")
        result = main(args)
        assert result == 0

    @staticmethod
    def _find_workflow(program: dict, name: str) -> dict | None:
        """Find a workflow by name in the emitted program."""
        from facetwork.ast_utils import find_workflow

        return find_workflow(program, name)

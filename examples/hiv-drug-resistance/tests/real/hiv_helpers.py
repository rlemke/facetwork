"""Shared helpers for HIV drug resistance integration tests.

Provides compilation, workflow extraction, and execution helpers
that use the full FFL compiler pipeline with MemoryStore.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from facetwork.emitter import emit_dict
from facetwork.parser import FFLParser
from facetwork.runtime import ExecutionResult, ExecutionStatus
from facetwork.runtime.agent_poller import AgentPoller
from facetwork.runtime.evaluator import Evaluator
from facetwork.validator import validate

# examples/hiv-drug-resistance/tests/real/ → examples/hiv-drug-resistance/
_EXAMPLE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_AFL_DIR = os.path.join(_EXAMPLE_ROOT, "afl")


def compile_resistance_afl() -> dict[str, Any]:
    """Compile afl/resistance.afl through the full compiler pipeline.

    Validation warnings about cross-block step refs, foreach variables,
    and catch error refs are expected and non-fatal (the runtime handles
    them correctly).

    Returns:
        The full program dict (JSON-serializable AST).

    Raises:
        afl.parser.ParseError: On syntax errors.
    """
    source_path = Path(_AFL_DIR) / "resistance.ffl"
    source = source_path.read_text()

    parser = FFLParser()
    program_ast = parser.parse(source)

    # Validate but treat errors as warnings — cross-scope refs (when blocks,
    # foreach variables, catch error refs) are flagged by the validator but
    # handled correctly at runtime.
    validate(program_ast)

    return emit_dict(program_ast, include_locations=False)


def extract_workflow(program_dict: dict, workflow_name: str) -> dict:
    """Find a WorkflowDecl by name in a compiled program dict.

    Searches recursively through namespaces and declarations.

    Args:
        program_dict: The compiled program dict.
        workflow_name: Name of the workflow to find.

    Returns:
        The workflow dict.

    Raises:
        KeyError: If the workflow is not found.
    """

    def _search_node(node: dict) -> dict | None:
        for wf in node.get("workflows", []):
            if wf.get("name") == workflow_name:
                return wf
        for decl in node.get("declarations", []):
            if decl.get("type") == "WorkflowDecl" and decl.get("name") == workflow_name:
                return decl
            if decl.get("type") == "Namespace":
                found = _search_node(decl)
                if found:
                    return found
        for ns in node.get("namespaces", []):
            found = _search_node(ns)
            if found:
                return found
        return None

    found = _search_node(program_dict)
    if found is None:
        raise KeyError(f"Workflow '{workflow_name}' not found in program")
    return found


def run_to_completion(
    evaluator: Evaluator,
    poller: AgentPoller,
    workflow_ast: dict,
    program_ast: dict,
    inputs: dict[str, Any] | None = None,
    max_rounds: int = 50,
) -> ExecutionResult:
    """Execute a workflow to completion through the AgentPoller pipeline.

    Loops: execute -> poll_once -> resume until COMPLETED, ERROR, or max_rounds.
    """
    result = evaluator.execute(workflow_ast, inputs=inputs, program_ast=program_ast)

    if result.status in (ExecutionStatus.COMPLETED, ExecutionStatus.ERROR):
        return result

    poller.cache_workflow_ast(result.workflow_id, workflow_ast, program_ast=program_ast)

    for _ in range(max_rounds):
        poller.poll_once()
        result = evaluator.resume(result.workflow_id, workflow_ast, program_ast, inputs)
        if result.status in (ExecutionStatus.COMPLETED, ExecutionStatus.ERROR):
            return result

    return result

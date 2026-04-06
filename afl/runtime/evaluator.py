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

"""AFL runtime evaluator.

The Evaluator orchestrates workflow execution:
- Creates initial workflow step
- Runs iterations until fixed point
- Commits changes atomically at iteration boundaries
"""

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .dispatcher import HandlerDispatcher

logger = logging.getLogger(__name__)

import time as _time

from .block import StatementDefinition
from .changers import get_state_changer
from .dependency import DependencyGraph
from .entities import StepLogEntry, StepLogLevel, StepLogSource
from .expression import evaluate_default
from .persistence import IterationChanges, PersistenceAPI
from .states import StepState
from .step import StepDefinition
from .telemetry import Telemetry
from .types import BlockId, ObjectType, StepId, WorkflowId, generate_id, workflow_id


class ExecutionStatus:
    """Status constants for execution results."""

    COMPLETED = "COMPLETED"
    PAUSED = "PAUSED"
    ERROR = "ERROR"
    TIMEOUT = "TIMEOUT"


@dataclass
class ExecutionResult:
    """Result of workflow execution."""

    success: bool
    workflow_id: str  # WorkflowId (str-based NewType)
    outputs: dict[str, Any] = field(default_factory=dict)
    error: Exception | None = None
    iterations: int = 0
    status: str = ExecutionStatus.COMPLETED
    token_usage: dict | None = None


@dataclass
class ExecutionContext:
    """Context for step execution.

    Provides access to persistence, telemetry, and workflow data.
    """

    persistence: PersistenceAPI
    telemetry: Telemetry
    changes: IterationChanges
    workflow_id: str  # WorkflowId (str-based NewType)
    workflow_ast: dict | None = None
    workflow_defaults: dict = field(default_factory=dict)
    program_ast: dict | None = None
    runner_id: str = ""
    dispatcher: "HandlerDispatcher | None" = None

    # Cache for block dependency graphs (keyed by block step ID)
    _block_graphs: dict[str, DependencyGraph] = field(default_factory=dict)

    # Cache for block AST overrides (e.g., foreach sub-blocks)
    _block_ast_cache: dict[str, dict] = field(default_factory=dict)

    # Cache for completed steps by name within blocks
    _completed_step_cache: dict[str, StepDefinition] = field(default_factory=dict)

    # Track which block IDs need Continue re-evaluation.
    # None = all dirty (first iteration), empty set = nothing dirty.
    _dirty_blocks: set[str] | None = field(default=None)

    def mark_block_dirty(self, block_id: StepId | BlockId | None) -> None:
        """Mark a block as needing Continue re-evaluation."""
        if block_id and self._dirty_blocks is not None:
            self._dirty_blocks.add(block_id)

    def is_block_dirty(self, block_id: StepId | BlockId) -> bool:
        """Check if a block needs Continue re-evaluation."""
        return self._dirty_blocks is None or block_id in self._dirty_blocks

    def mark_block_processed(self, block_id: StepId | BlockId) -> None:
        """Remove a block from the dirty set after processing."""
        if self._dirty_blocks is not None:
            self._dirty_blocks.discard(block_id)

    def get_workflow_ast(self) -> dict | None:
        """Get the workflow AST."""
        return self.workflow_ast

    def get_workflow_root(self) -> StepDefinition | None:
        """Get the workflow root step.

        Checks in-memory changes first (updated_steps, then created_steps)
        so that pre_script results are visible within the same iteration.
        """
        for step in self.changes.updated_steps:
            if (
                step.root_id is None
                and step.container_id is None
                and step.workflow_id == self.workflow_id
            ):
                return step
        for step in self.changes.created_steps:
            if (
                step.root_id is None
                and step.container_id is None
                and step.workflow_id == self.workflow_id
            ):
                return step
        return self.persistence.get_workflow_root(self.workflow_id)

    def get_statement_definition(self, step: StepDefinition) -> StatementDefinition | None:
        """Get the statement definition for a step."""
        if step.statement_id and step.block_id:
            graph = self._block_graphs.get(step.block_id)
            if graph:
                return graph.get_statement(str(step.statement_id))
        return None

    def get_block_ast(self, block_step: StepDefinition) -> dict | None:
        """Get the AST for a block step.

        Resolves the correct AST body for this block by tracing
        up the containment hierarchy:
        - If block AST cache has an entry → return cached AST
        - If container is workflow root → workflow body
        - If container has an inline andThen body (statement-level) → that body
        - If container calls a facet with a body (facet-level) → facet body

        For multi-block workflows (body is a list), the block's statement_id
        encodes the index ("block-N") to select the correct body element.

        Args:
            block_step: The block step to resolve AST for

        Returns:
            The andThen block AST dict, or None
        """
        # Check block AST cache first (used by foreach sub-blocks)
        if block_step.id in self._block_ast_cache:
            return self._block_ast_cache[block_step.id]

        # Foreach sub-blocks: derive body from parent foreach block's AST
        # (cache may be empty after resume, so reconstruct on the fly)
        if block_step.foreach_var is not None and block_step.block_id:
            parent = self._find_step(block_step.block_id)
            if parent:
                parent_ast = self.get_block_ast(parent)
                if parent_ast and "foreach" in parent_ast:
                    body_ast = {k: v for k, v in parent_ast.items() if k != "foreach"}
                    self._block_ast_cache[block_step.id] = body_ast
                    return body_ast

        # When case sub-blocks: derive body from parent when block's AST
        stmt_id = str(block_step.statement_id) if block_step.statement_id else ""
        if stmt_id.startswith("when-case-") and block_step.block_id:
            parent = self._find_step(block_step.block_id)
            if parent:
                parent_ast = self.get_block_ast(parent)
                if parent_ast and "when" in parent_ast:
                    try:
                        case_index = int(stmt_id.split("-")[-1])
                        cases = parent_ast["when"].get("cases", [])
                        if 0 <= case_index < len(cases):
                            case = cases[case_index]
                            case_body: dict = {"type": "AndThenBlock"}
                            if "steps" in case:
                                case_body["steps"] = case["steps"]
                            if "yield" in case:
                                case_body["yield"] = case["yield"]
                            if "yields" in case:
                                case_body["yields"] = case["yields"]
                            self._block_ast_cache[block_step.id] = case_body
                            return case_body
                    except (ValueError, IndexError):
                        pass

        if not block_step.container_id:
            # Block has no container — shouldn't normally happen
            if self.workflow_ast:
                body = self.workflow_ast.get("body")
                return self._select_block_body(body, block_step)
            return None

        # Find the container step
        container = self._find_step(block_step.container_id)
        if not container:
            return None

        # If container is the workflow root (no container_id itself),
        # this block represents the workflow's andThen body
        if container.container_id is None:
            if self.workflow_ast:
                body = self.workflow_ast.get("body")
                return self._select_block_body(body, block_step)
            return None

        # Check for statement-level inline body on the container
        inline_body = self._find_statement_body(container)
        if inline_body:
            return inline_body

        # Check for facet-level body on the container's facet
        if container.facet_name:
            facet_def = self.get_facet_definition(container.facet_name)
            if facet_def and "body" in facet_def:
                return self._select_block_body(facet_def["body"], block_step)

        return None

    def _select_block_body(self, body: Any, block_step: StepDefinition) -> dict | None:
        """Select the correct body element for a block step.

        When a workflow has multiple andThen blocks, the body is a list.
        The block step's statement_id encodes "block-N" to select the
        correct element.

        Args:
            body: The body (dict for single block, list for multiple)
            block_step: The block step

        Returns:
            The selected body dict, or None
        """
        if body is None:
            return None
        if isinstance(body, list):
            # Multi-block: extract index from statement_id
            if block_step.statement_id and str(block_step.statement_id).startswith("block-"):
                try:
                    index = int(str(block_step.statement_id).split("-", 1)[1])
                    if 0 <= index < len(body):
                        return body[index]
                except (ValueError, IndexError):
                    pass
            # Fallback: return first body if no index
            return body[0] if body else None
        return body

    def _find_step(self, step_id: StepId | BlockId) -> StepDefinition | None:
        """Find a step by ID, checking pending changes first.

        Args:
            step_id: The step ID to find (StepId or BlockId,
                since blocks are steps)

        Returns:
            The step, or None
        """
        # Check pending created steps
        for step in self.changes.created_steps:
            if step.id == step_id:
                return step
        # Check pending updated steps
        for step in self.changes.updated_steps:
            if step.id == step_id:
                return step
        # Check persistence
        return self.persistence.get_step(step_id)

    def _find_statement_body(self, step: StepDefinition) -> dict | None:
        """Find the inline andThen body for a step's statement.

        Looks up the step's statement AST node in the containing block's
        AST to check if it has an inline body.

        Args:
            step: The step to check for inline body

        Returns:
            The inline body dict, or None
        """
        if not step.statement_id:
            return None

        # Get the AST for the block containing this step
        containing_block_ast = self._find_containing_block_ast(step)
        if not containing_block_ast:
            return None

        # Search for the statement with matching id
        for stmt_ast in containing_block_ast.get("steps", []):
            if stmt_ast.get("id") == str(step.statement_id) or stmt_ast.get("name") == str(
                step.statement_id
            ):
                return stmt_ast.get("body")

        return None

    def _find_statement_catch(self, step: StepDefinition) -> dict | None:
        """Find the catch clause for a step.

        Checks three sources (mirrors _find_statement_body):
        1. Workflow root → workflow_ast.get("catch")
        2. Statement-level → stmt_ast.get("catch") from containing block
        3. Facet-level → facet_def.get("catch")

        Args:
            step: The step to check for catch clause

        Returns:
            The catch clause dict, or None
        """
        # 1. Workflow root step
        if step.container_id is None:
            if self.workflow_ast:
                return self.workflow_ast.get("catch")
            return None

        # 2. Statement-level catch
        if step.statement_id:
            containing_block_ast = self._find_containing_block_ast(step)
            if containing_block_ast:
                for stmt_ast in containing_block_ast.get("steps", []):
                    if stmt_ast.get("id") == str(step.statement_id) or stmt_ast.get("name") == str(
                        step.statement_id
                    ):
                        catch = stmt_ast.get("catch")
                        if catch:
                            return catch

        # 3. Facet-level catch
        if step.facet_name:
            facet_def = self.get_facet_definition(step.facet_name)
            if facet_def and "catch" in facet_def:
                return facet_def["catch"]

        return None

    def _find_containing_block_ast(self, step: StepDefinition) -> dict | None:
        """Find the AST for the block containing a step.

        Traces up the hierarchy to resolve the block's AST.

        Args:
            step: The step whose containing block AST we need

        Returns:
            The block AST dict, or None
        """
        if not step.block_id:
            return None

        # Find the block step
        block_step = self._find_step(step.block_id)
        if not block_step:
            return None

        # Recursively resolve block AST
        return self.get_block_ast(block_step)

    def get_block_graph(self, block_id: StepId | BlockId) -> DependencyGraph | None:
        """Get cached dependency graph for a block."""
        return self._block_graphs.get(block_id)

    def set_block_graph(self, block_id: StepId | BlockId, graph: DependencyGraph) -> None:
        """Cache a dependency graph for a block."""
        self._block_graphs[block_id] = graph

    def get_completed_step_by_name(
        self,
        step_name: str,
        block_id: StepId | BlockId | None,
    ) -> StepDefinition | None:
        """Get a completed step by name within a block.

        Searches the specified block first. If not found and a block_id
        was given, falls back to a workflow-wide search to resolve
        cross-block step references (e.g. ``qc.passed`` inside an
        ``andThen when`` case that references a step from a prior
        ``andThen`` block).

        Args:
            step_name: The step name to find
            block_id: The block containing the step

        Returns:
            The completed step, or None if not found
        """
        result = self._find_completed_step_in(step_name, block_id)
        if result is not None:
            return result

        # Cross-block fallback: search all workflow steps
        if block_id:
            return self._find_completed_step_in(step_name, None)

        return None

    def _find_completed_step_in(
        self,
        step_name: str,
        block_id: StepId | BlockId | None,
    ) -> StepDefinition | None:
        """Search for a completed step by name in a specific scope.

        Args:
            step_name: The step name to find
            block_id: Block scope to search, or None for workflow-wide

        Returns:
            The completed step, or None if not found
        """
        # Check cache first
        cache_key = f"{block_id}:{step_name}"
        if cache_key in self._completed_step_cache:
            return self._completed_step_cache[cache_key]

        # Search in persistence
        if block_id:
            steps = self.persistence.get_steps_by_block(block_id)
        else:
            steps = self.persistence.get_steps_by_workflow(self.workflow_id)

        # Also check pending changes
        all_steps = list(steps)
        for pending in self.changes.created_steps:
            if pending.block_id == block_id:
                all_steps.append(pending)
        for pending in self.changes.updated_steps:
            for i, s in enumerate(all_steps):
                if s.id == pending.id:
                    all_steps[i] = pending

        # Find by name
        for step in all_steps:
            if step.statement_id and step.is_complete:
                # Check statement_name directly (persisted on step)
                if step.statement_name == step_name:
                    self._completed_step_cache[cache_key] = step
                    return step
                # Fall back to AST-based name lookup (needs dependency graph)
                stmt = self.get_statement_definition(step)
                if stmt and stmt.name == step_name:
                    self._completed_step_cache[cache_key] = step
                    return step

        return None

    def resolve_qualified_name(self, short_name: str) -> str:
        """Resolve a short facet name to its qualified form.

        Walks the program AST declarations to find the namespace
        containing the facet, and returns 'Namespace.FacetName' for
        namespaced facets or 'FacetName' for top-level facets.

        Args:
            short_name: The unqualified facet name

        Returns:
            The qualified name (e.g. 'ns.SubNs.FacetName') or the
            original name if no namespace is found.
        """
        if not self.program_ast:
            return short_name

        declarations = self.program_ast.get("declarations", [])
        result = self._resolve_in_declarations(declarations, short_name, prefix="")
        return result if result else short_name

    def _resolve_in_declarations(
        self, declarations: list, short_name: str, prefix: str
    ) -> str | None:
        """Recursively search declarations to resolve a qualified name.

        Args:
            declarations: List of declaration dicts
            short_name: The facet name to find
            prefix: Current namespace prefix (e.g. 'ns.SubNs')

        Returns:
            Qualified name or None
        """
        for decl in declarations:
            decl_type = decl.get("type", "")
            if decl_type in ("FacetDecl", "EventFacetDecl", "WorkflowDecl"):
                if decl.get("name") == short_name:
                    if prefix:
                        return f"{prefix}.{short_name}"
                    return short_name
            elif decl_type == "Namespace":
                ns_name = decl.get("name", "")
                nested = decl.get("declarations", [])
                new_prefix = f"{prefix}.{ns_name}" if prefix else ns_name
                result = self._resolve_in_declarations(nested, short_name, new_prefix)
                if result:
                    return result
        return None

    def get_facet_definition(self, facet_name: str) -> dict | None:
        """Get facet definition from program AST.

        Searches program declarations (including inside namespaces)
        for a FacetDecl or EventFacetDecl matching the given name.
        Accepts both qualified names ('ns.FacetName') and short names.

        Args:
            facet_name: The facet name to look up (qualified or short)

        Returns:
            The facet declaration dict, or None if not found
        """
        if not self.program_ast:
            return None

        declarations = self.program_ast.get("declarations", [])

        # If the name contains a dot, try qualified lookup first
        if "." in facet_name:
            result = self._search_declarations_qualified(declarations, facet_name)
            if result:
                return result

        # Fall back to short name search
        return self._search_declarations(declarations, facet_name)

    def _search_declarations_qualified(
        self, declarations: list, qualified_name: str
    ) -> dict | None:
        """Search declarations using a qualified name (e.g. 'ns.Sub.FacetName').

        Handles both nested namespace structures (ns > Sub > FacetName) and
        flat namespace names ('ns.Sub') as emitted by the AFL compiler.

        Args:
            declarations: List of declaration dicts
            qualified_name: Dot-separated qualified name

        Returns:
            Matching declaration dict or None
        """
        parts = qualified_name.split(".")
        facet_short = parts[-1]

        # Strategy 1: flat namespace match — try every possible split point.
        # e.g. "osm.Region.ResolveRegion" tries namespace "osm.Region"
        for i in range(len(parts) - 1, 0, -1):
            ns_name = ".".join(parts[:i])
            for decl in declarations:
                if decl.get("type") == "Namespace" and decl.get("name") == ns_name:
                    inner = decl.get("declarations", [])
                    target = parts[i]
                    for inner_decl in inner:
                        if inner_decl.get("type") in (
                            "FacetDecl",
                            "EventFacetDecl",
                            "WorkflowDecl",
                        ):
                            if inner_decl.get("name") == target:
                                return inner_decl
                    # Also check nested namespaces within this namespace
                    result = self._search_declarations(decl.get("declarations", []), facet_short)
                    if result:
                        return result

        # Strategy 2: nested namespace navigation (ns > Sub > FacetName).
        ns_parts = parts[:-1]
        current_decls = declarations
        for ns_name in ns_parts:
            found = False
            for decl in current_decls:
                if decl.get("type") == "Namespace" and decl.get("name") == ns_name:
                    current_decls = decl.get("declarations", [])
                    found = True
                    break
            if not found:
                return None

        for decl in current_decls:
            if decl.get("type") in ("FacetDecl", "EventFacetDecl", "WorkflowDecl"):
                if decl.get("name") == facet_short:
                    return decl
        return None

    def _search_declarations(self, declarations: list, facet_name: str) -> dict | None:
        """Search a list of declarations for a facet by short name.

        Args:
            declarations: List of declaration dicts
            facet_name: The facet name to find

        Returns:
            Matching declaration dict or None
        """
        for decl in declarations:
            decl_type = decl.get("type", "")
            if decl_type in ("FacetDecl", "EventFacetDecl", "WorkflowDecl"):
                if decl.get("name") == facet_name:
                    return decl
            elif decl_type == "Namespace":
                # Search nested declarations
                nested = decl.get("declarations", [])
                result = self._search_declarations(nested, facet_name)
                if result:
                    return result
        return None

    def get_implicit_args(self, facet_name: str) -> dict | None:
        """Get implicit default args for a facet from program AST.

        Searches all ImplicitDecl nodes whose call target matches facet_name.
        Returns the first matching implicit's args as a dict, or None.

        Args:
            facet_name: The facet name to look up (qualified or short)

        Returns:
            Dict of {arg_name: value_expr} or None if no matching implicit
        """
        if not self.program_ast:
            return None
        return self._search_implicit_declarations(
            self.program_ast.get("declarations", []), facet_name
        )

    def _search_implicit_declarations(self, declarations: list, facet_name: str) -> dict | None:
        """Search declarations for an ImplicitDecl targeting facet_name.

        Args:
            declarations: List of declaration dicts
            facet_name: The facet name to match

        Returns:
            Dict of {arg_name: value_expr} or None
        """
        short_name = facet_name.split(".")[-1] if "." in facet_name else facet_name
        for decl in declarations:
            if decl.get("type") == "ImplicitDecl":
                call = decl.get("call", {})
                target = call.get("target", "")
                target_short = target.split(".")[-1] if "." in target else target
                if target == facet_name or target_short == short_name:
                    return {arg["name"]: arg["value"] for arg in call.get("args", [])}
            elif decl.get("type") == "Namespace":
                result = self._search_implicit_declarations(
                    decl.get("declarations", []), facet_name
                )
                if result:
                    return result
        return None

    def set_block_ast_cache(self, block_id: StepId, ast: dict) -> None:
        """Cache a block AST for direct lookup (e.g., foreach sub-blocks).

        Args:
            block_id: The block step ID
            ast: The AST dict to cache
        """
        self._block_ast_cache[block_id] = ast

    def clear_caches(self) -> None:
        """Clear caches for new iteration."""
        self._completed_step_cache.clear()


class Evaluator:
    """Main evaluator for AFL workflow execution.

    Executes workflows through iterative evaluation:
    1. Create initial workflow step
    2. Run iterations until fixed point
    3. Each iteration processes all eligible steps
    4. Changes are committed atomically at iteration end
    """

    def __init__(
        self,
        persistence: PersistenceAPI,
        telemetry: Telemetry | None = None,
        max_iterations: int = 1000,
    ):
        """Initialize evaluator.

        Args:
            persistence: Persistence API implementation
            telemetry: Optional telemetry collector
            max_iterations: Maximum iterations before timeout
        """
        self.persistence = persistence
        self.telemetry = telemetry or Telemetry()
        self.max_iterations = max_iterations

    def execute(
        self,
        workflow_ast: dict,
        inputs: dict[str, Any] | None = None,
        program_ast: dict | None = None,
        runner_id: str = "",
        dispatcher: "HandlerDispatcher | None" = None,
        wf_id: str = "",
    ) -> ExecutionResult:
        """Execute a workflow.

        Args:
            workflow_ast: Compiled workflow AST
            inputs: Optional input parameter values
            program_ast: Optional program AST for facet lookups
            runner_id: Optional runner ID for task creation context
            dispatcher: Optional handler dispatcher for inline event execution
            wf_id: Optional explicit workflow ID (used to align with submitted workflow record)

        Returns:
            ExecutionResult with outputs or error
        """
        wf_id = WorkflowId(wf_id) if wf_id else workflow_id()
        workflow_name = workflow_ast.get("name", "unknown")

        logger.info(
            "Workflow started: workflow_id=%s workflow_name=%s inputs=%s",
            wf_id,
            workflow_name,
            list((inputs or {}).keys()),
        )

        self.telemetry.log_workflow_start(wf_id, workflow_name)

        # Build default values from AST params
        defaults = self._extract_defaults(workflow_ast, inputs or {})

        # Create execution context
        context = ExecutionContext(
            persistence=self.persistence,
            telemetry=self.telemetry,
            changes=IterationChanges(),
            workflow_id=wf_id,
            workflow_ast=workflow_ast,
            workflow_defaults=defaults,
            program_ast=program_ast,
            runner_id=runner_id,
            dispatcher=dispatcher,
        )

        try:
            # Create initial workflow step
            root_step = self._create_workflow_step(workflow_ast, wf_id, defaults)
            context.changes.add_created_step(root_step)
            self._commit_iteration(context)

            # Run iterations
            iteration = 0
            while iteration < self.max_iterations:
                iteration += 1
                self.telemetry.log_iteration_start(wf_id, iteration)
                context.clear_caches()

                progress = self._run_iteration(context)

                self.telemetry.log_iteration_end(
                    wf_id,
                    iteration,
                    len(context.changes.created_steps),
                    len(context.changes.updated_steps),
                )

                self._commit_iteration(context)

                if not progress:
                    # Check if we're paused on event-blocked steps
                    if self._has_event_blocked_steps(context):
                        return ExecutionResult(
                            success=True,
                            workflow_id=wf_id,
                            iterations=iteration,
                            status=ExecutionStatus.PAUSED,
                        )
                    # Fixed point reached
                    break

            # Get final result
            result = self._build_result(wf_id, iteration)
            logger.info(
                "Workflow finished: workflow_id=%s status=%s iterations=%d",
                wf_id,
                result.status,
                result.iterations,
            )
            return result

        except Exception as e:
            self.telemetry.log_workflow_error(wf_id, e)
            logger.error(
                "Workflow failed: workflow_id=%s error=%s",
                wf_id,
                e,
            )
            return ExecutionResult(
                success=False,
                workflow_id=wf_id,
                error=e,
                status=ExecutionStatus.ERROR,
            )

    def _extract_defaults(
        self,
        workflow_ast: dict,
        inputs: dict[str, Any],
    ) -> dict[str, Any]:
        """Extract default values from workflow params.

        Args:
            workflow_ast: The workflow AST
            inputs: Provided input values

        Returns:
            Dict of parameter name -> value (inputs override defaults)
        """
        defaults = {}
        params = workflow_ast.get("params", [])

        for param in params:
            name = param.get("name", "")
            # Check for default value in AST
            # The default would be in the param dict if present
            # For now, we check if there's a literal default
            if "default" in param:
                defaults[name] = evaluate_default(param["default"])

        # Override with provided inputs
        defaults.update(inputs)
        return defaults

    def _create_workflow_step(
        self,
        workflow_ast: dict,
        wf_id: WorkflowId,
        defaults: dict[str, Any],
    ) -> StepDefinition:
        """Create the initial workflow step.

        Args:
            workflow_ast: The workflow AST
            wf_id: Workflow ID
            defaults: Default parameter values

        Returns:
            Initial workflow step
        """
        step = StepDefinition.create(
            workflow_id=wf_id,
            object_type=ObjectType.WORKFLOW,
            facet_name=workflow_ast.get("name", ""),
        )

        # Set initial attribute values
        for name, value in defaults.items():
            step.set_attribute(name, value)

        return step

    def _run_iteration(self, context: ExecutionContext) -> bool:
        """Run a single iteration.

        Processes all eligible steps in memory. Each step is processed
        exactly once per iteration.

        Args:
            context: Execution context

        Returns:
            True if progress was made
        """
        progress = False
        processed_ids: set[StepId] = set()

        # Get actionable steps for this workflow (excludes terminal and
        # EventTransmit steps without pending transitions).
        steps = list(self.persistence.get_actionable_steps_by_workflow(context.workflow_id))
        logger.debug(
            "Iteration start: workflow_id=%s actionable_step_count=%d",
            context.workflow_id,
            len(steps),
        )

        # Continue states: blocks polling for child completion
        CONTINUE_STATES = {
            StepState.BLOCK_EXECUTION_CONTINUE,
            StepState.STATEMENT_BLOCKS_CONTINUE,
            StepState.MIXIN_BLOCKS_CONTINUE,
        }

        # Process existing steps
        for step in steps:
            if step.id in processed_ids:
                continue
            processed_ids.add(step.id)

            # Skip Continue blocks that haven't been dirtied by a child change
            if step.state in CONTINUE_STATES and not context.is_block_dirty(step.id):
                continue

            result_progress = self._process_step(step, context)
            if result_progress:
                progress = True
            elif step.state in CONTINUE_STATES:
                # Processed but no progress — remove from dirty set
                context.mark_block_processed(step.id)

        # Process newly created steps (may be created during processing)
        # Use a list to collect all created steps for this iteration
        pending_created: list[StepDefinition] = []

        while True:
            # Get any new steps that were created
            if not context.changes.created_steps:
                break

            # Move created steps to pending
            new_steps = list(context.changes.created_steps)
            context.changes.created_steps.clear()

            for step in new_steps:
                if step.id not in processed_ids:
                    self.telemetry.log_step_created(step)
                    processed_ids.add(step.id)
                    if self._process_step(step, context):
                        progress = True
                # Always add to pending for commit
                pending_created.append(step)

        # Restore created steps for commit
        context.changes.created_steps = pending_created

        return progress

    def _process_step(
        self,
        step: StepDefinition,
        context: ExecutionContext,
    ) -> bool:
        """Process a single step.

        Args:
            step: The step to process
            context: Execution context

        Returns:
            True if step made progress
        """
        if step.is_terminal:
            return False

        # Skip steps parked at EventTransmit (waiting for external continue)
        # unless they've been unblocked via continue_step()
        if (
            step.state == StepState.EVENT_TRANSMIT
            and not step.transition.is_requesting_state_change
        ):
            return False

        # Record the state before processing to detect real progress
        state_before = step.state

        # Get appropriate state changer
        changer = get_state_changer(step, context)

        # Process the step
        result = changer.process()

        # Detect real progress: state actually changed
        if result.step.state != state_before:
            logger.debug(
                "Step progressed: step_id=%s state_before=%s state_after=%s",
                step.id,
                state_before,
                result.step.state,
            )
            # Emit step log when a step transitions to an error state
            if result.step.is_error:
                error_msg = str(result.step.transition.error or result.error or "Unknown error")
                self._emit_step_log(
                    step_id=step.id,
                    workflow_id=step.workflow_id,
                    message=error_msg,
                    level=StepLogLevel.ERROR,
                    facet_name=step.facet_name,
                )
            # Mark parent blocks as needing re-evaluation
            context.mark_block_dirty(result.step.block_id)
            context.mark_block_dirty(result.step.container_id)
            context.changes.add_updated_step(result.step)
            return True

        # No state change but step was modified (e.g., attributes set)
        if result.step.transition.changed and not result.continue_processing:
            context.mark_block_dirty(result.step.block_id)
            context.mark_block_dirty(result.step.container_id)
            context.changes.add_updated_step(result.step)
            return True

        return False

    def _emit_step_log(
        self,
        step_id: str,
        workflow_id: str,
        message: str,
        level: str = StepLogLevel.INFO,
        facet_name: str = "",
    ) -> None:
        """Write a step log entry for evaluator-managed state transitions."""
        entry = StepLogEntry(
            uuid=generate_id(),
            step_id=step_id,
            workflow_id=workflow_id,
            source=StepLogSource.FRAMEWORK,
            level=level,
            message=message,
            facet_name=facet_name,
            time=int(_time.time() * 1000),
        )
        try:
            self.persistence.save_step_log(entry)
        except Exception:
            logger.debug("Could not save step log for step %s", step_id, exc_info=True)

    def _build_result(self, wf_id: str, iteration: int) -> ExecutionResult:
        """Build the final execution result.

        Args:
            wf_id: Workflow ID
            iteration: Number of iterations executed

        Returns:
            ExecutionResult
        """
        root = self.persistence.get_workflow_root(wf_id)
        if root and root.is_complete:
            outputs = {name: attr.value for name, attr in root.attributes.returns.items()}
            self.telemetry.log_workflow_complete(wf_id, outputs)
            return ExecutionResult(
                success=True,
                workflow_id=wf_id,
                outputs=outputs,
                iterations=iteration,
                status=ExecutionStatus.COMPLETED,
            )
        elif root and root.is_error:
            error = root.transition.error or Exception("Workflow error")
            self.telemetry.log_workflow_error(wf_id, error)
            return ExecutionResult(
                success=False,
                workflow_id=wf_id,
                error=error,
                iterations=iteration,
                status=ExecutionStatus.ERROR,
            )
        else:
            return ExecutionResult(
                success=False,
                workflow_id=wf_id,
                error=Exception("Workflow did not complete"),
                iterations=iteration,
            )

    def _has_event_blocked_steps(self, context: ExecutionContext) -> bool:
        """Check if any workflow steps are blocked at EventTransmit.

        Args:
            context: Execution context

        Returns:
            True if any steps are at EVENT_TRANSMIT state
        """
        # Use actionable query to avoid loading terminal steps; any
        # EventTransmit step (without request_transition) that still
        # exists indicates event-blocked work.
        steps = self.persistence.get_steps_by_workflow(context.workflow_id)
        for step in steps:
            if step.state == StepState.EVENT_TRANSMIT and not step.is_terminal:
                logger.debug(
                    "Event-blocked step detected: workflow_id=%s step_id=%s",
                    context.workflow_id,
                    step.id,
                )
                return True
        return False

    def continue_step(self, step_id: str, result: dict | None = None) -> None:
        """Continue an event-blocked step with a result.

        Called by external code (or test harness) between evaluator runs
        to unblock a step parked at EventTransmit.

        Args:
            step_id: The step ID to continue
            result: Optional dict of return attribute values
        """
        logger.info(
            "Continue step: step_id=%s result_keys=%s",
            step_id,
            list((result or {}).keys()),
        )
        step = self.persistence.get_step(step_id)
        if step is None:
            raise ValueError(f"Step {step_id} not found")
        if step.state != StepState.EVENT_TRANSMIT:
            if StepState.is_error(step.state) and result is not None:
                # Step was in Error but a retry succeeded — reset to
                # EventTransmit so the result can be applied normally.
                logger.info(
                    "continue_step: step %s recovering from %s with successful result",
                    step_id,
                    step.state,
                )
                step.state = StepState.EVENT_TRANSMIT
                step.transition.current_state = StepState.EVENT_TRANSMIT
                step.error = None
            elif step.is_terminal:
                # Step already completed (e.g. task was reprocessed after
                # a runner crash left it in running state).  Treat as no-op
                # so the caller can mark the task completed.
                logger.warning(
                    "continue_step: step %s already at terminal state %s — skipping",
                    step_id,
                    step.state,
                )
                return
            else:
                raise ValueError(
                    f"Step {step_id} is at {step.state}, expected {StepState.EVENT_TRANSMIT}"
                )

        # Apply result as return attributes
        if result:
            for name, value in result.items():
                step.set_attribute(name, value, is_return=True)

        # Advance the step past EventTransmit so it won't trigger a new
        # task even if the subsequent resume() times out or the server dies.
        # The transition table says EventTransmit → StatementBlocksBegin.
        from .states import STEP_TRANSITIONS

        next_state = STEP_TRANSITIONS.get(step.state)
        if next_state:
            step.state = next_state
            step.transition.current_state = next_state
        # Do NOT set request_state_change(True) here. The step is now at
        # StatementBlocksBegin and must execute the Begin handler (which
        # creates andThen block children) before transitioning to Continue.
        # Setting request_state_change(True) would cause the state changer
        # loop to skip Begin and jump straight to Continue, leaving no
        # children created.
        step.transition.changed = True

        # Save directly to persistence
        self.persistence.save_step(step)

    def fail_step(
        self,
        step_id: str,
        error_message: str,
        *,
        workflow_ast: dict | None = None,
        program_ast: dict | None = None,
    ) -> None:
        """Fail an event-blocked step with an error.

        If *workflow_ast* / *program_ast* are supplied and the step has a
        ``catch`` clause, the step transitions to CATCH_BEGIN instead of
        STATEMENT_ERROR so that the normal catch-recovery path executes on
        the next ``resume()`` call.

        Args:
            step_id: The step ID to fail
            error_message: Human-readable error description
            workflow_ast: Optional workflow AST for catch-clause lookup
            program_ast: Optional full program AST for facet lookups
        """
        logger.warning(
            "Fail step: step_id=%s error_message=%s",
            step_id,
            error_message,
        )
        step = self.persistence.get_step(step_id)
        if step is None:
            raise ValueError(f"Step {step_id} not found")
        if step.state != StepState.EVENT_TRANSMIT:
            if step.is_terminal:
                logger.warning(
                    "fail_step: step %s already at terminal state %s — skipping",
                    step_id,
                    step.state,
                )
                return
            raise ValueError(
                f"Step {step_id} is at {step.state}, expected {StepState.EVENT_TRANSMIT}"
            )

        error = RuntimeError(error_message)

        # Check for a catch clause using a temporary execution context.
        if workflow_ast or program_ast:
            catch_ast = self._check_catch_for_step(step, workflow_ast, program_ast)
            if catch_ast:
                logger.info(
                    "Catch clause found for step %s — transitioning to CATCH_BEGIN",
                    step_id,
                )
                step.transition.error = error
                step.change_state(StepState.CATCH_BEGIN)
                # Do NOT request state change — let the CatchBeginHandler
                # execute at CATCH_BEGIN when resume() processes this step.
                self.persistence.save_step(step)
                return

        step.mark_error(error)
        self.persistence.save_step(step)

    def _check_catch_for_step(
        self,
        step: "StepDefinition",
        workflow_ast: dict | None,
        program_ast: dict | None,
    ) -> dict | None:
        """Build a temporary ExecutionContext and look up the catch clause.

        Returns the catch AST dict if found, or None.
        """
        ctx = ExecutionContext(
            persistence=self.persistence,
            telemetry=Telemetry(enabled=False),
            changes=IterationChanges(),
            workflow_id=step.workflow_id,
            workflow_ast=workflow_ast,
            program_ast=program_ast,
            runner_id="",
        )
        return ctx._find_statement_catch(step)

    def retry_step(self, step_id: StepId) -> None:
        """Retry a failed step by resetting it to EVENT_TRANSMIT.

        Resets a step from STATEMENT_ERROR back to EVENT_TRANSMIT so that
        the agent can re-execute it. Also resets the associated task from
        failed back to pending, and resets any errored ancestor blocks so
        execution can continue once the retried step completes.

        Args:
            step_id: The step ID to retry
        """
        logger.info("Retry step: step_id=%s", step_id)
        step = self.persistence.get_step(step_id)
        if step is None:
            raise ValueError(f"Step {step_id} not found")
        if step.state != StepState.STATEMENT_ERROR:
            raise ValueError(
                f"Step {step_id} is at {step.state}, expected {StepState.STATEMENT_ERROR}"
            )

        # Reset step state to EVENT_TRANSMIT
        step.state = StepState.EVENT_TRANSMIT
        step.transition.current_state = StepState.EVENT_TRANSMIT
        step.transition.clear_error()
        step.transition.request_transition = False
        step.transition.changed = True
        self.persistence.save_step(step)

        # Reset associated task to pending
        task = self.persistence.get_task_for_step(step_id)
        if task is not None:
            task.state = "pending"
            task.error = None
            self.persistence.save_task(task)

        # Reset errored ancestor blocks/containers so execution resumes
        self._reset_errored_ancestors(step)

    def retry_block(self, step_id: StepId) -> int:
        """Retry all errored leaf steps under a block, recursively.

        Finds every step in STATEMENT_ERROR under the given block (walking
        nested blocks), resets each leaf error to EVENT_TRANSMIT, resets
        its task, and resets all errored ancestor blocks/containers so
        execution can resume.

        Args:
            step_id: The block or container step ID to retry from.

        Returns:
            Number of leaf steps retried.
        """
        logger.info("Retry block: step_id=%s", step_id)
        root = self.persistence.get_step(step_id)
        if root is None:
            raise ValueError(f"Step {step_id} not found")

        # Collect all steps in this workflow
        all_steps = list(self.persistence.get_steps_by_workflow(root.workflow_id))

        # Build parent->children maps on block_id and container_id
        by_block: dict[str, list[StepDefinition]] = {}
        by_container: dict[str, list[StepDefinition]] = {}
        for s in all_steps:
            if s.block_id:
                by_block.setdefault(s.block_id, []).append(s)
            if s.container_id:
                by_container.setdefault(s.container_id, []).append(s)

        # Walk down to find all errored leaf steps (event facet steps)
        errored_leaves: list[StepDefinition] = []
        stack = [step_id]
        seen: set[str] = set()
        while stack:
            sid = stack.pop()
            if sid in seen:
                continue
            seen.add(sid)
            children = by_block.get(sid, []) + by_container.get(sid, [])
            if not children:
                # Leaf — check if errored
                step = next((s for s in all_steps if s.id == sid), None)
                if step and step.state == StepState.STATEMENT_ERROR:
                    errored_leaves.append(step)
            else:
                for child in children:
                    if child.state == StepState.STATEMENT_ERROR:
                        stack.append(child.id)

        # Retry each errored leaf
        for leaf in errored_leaves:
            leaf.state = StepState.EVENT_TRANSMIT
            leaf.transition.current_state = StepState.EVENT_TRANSMIT
            leaf.transition.clear_error()
            leaf.transition.request_transition = False
            leaf.transition.changed = True
            self.persistence.save_step(leaf)

            task = self.persistence.get_task_for_step(leaf.id)
            if task is not None:
                task.state = "pending"
                task.error = None
                self.persistence.save_task(task)

            self._reset_errored_ancestors(leaf)

        logger.info("Retry block done: step_id=%s retried=%d", step_id, len(errored_leaves))
        return len(errored_leaves)

    def reset_block(self, step_id: StepId) -> int:
        """Reset a block by deleting all descendant steps and restarting it.

        Recursively deletes every step, task, and step log under the given
        block, then resets the block step itself to BLOCK_EXECUTION_BEGIN
        so it re-executes from scratch as if it had never run.  Also resets
        errored ancestors so the workflow can continue.

        Args:
            step_id: The block or container step ID to reset.

        Returns:
            Number of descendant steps deleted.
        """
        logger.info("Reset block: step_id=%s", step_id)
        block = self.persistence.get_step(step_id)
        if block is None:
            raise ValueError(f"Step {step_id} not found")

        # Collect all descendant step IDs recursively
        all_steps = list(self.persistence.get_steps_by_workflow(block.workflow_id))
        by_block: dict[str, list[str]] = {}
        by_container: dict[str, list[str]] = {}
        for s in all_steps:
            if s.block_id:
                by_block.setdefault(s.block_id, []).append(s.id)
            if s.container_id:
                by_container.setdefault(s.container_id, []).append(s.id)

        descendant_ids: list[str] = []
        stack: list[str] = [step_id]
        seen: set[str] = set()
        while stack:
            sid = stack.pop()
            if sid in seen:
                continue
            seen.add(sid)
            children = by_block.get(sid, []) + by_container.get(sid, [])
            for child_id in children:
                descendant_ids.append(child_id)
                stack.append(child_id)

        # Delete descendants (steps, tasks, logs)
        if descendant_ids:
            self.persistence.delete_step_logs_for_steps(descendant_ids)
            self.persistence.delete_tasks_for_steps(descendant_ids)
            self.persistence.delete_steps(descendant_ids)

        # Reset the block itself to BLOCK_EXECUTION_BEGIN
        block.state = StepState.BLOCK_EXECUTION_BEGIN
        block.transition.current_state = StepState.BLOCK_EXECUTION_BEGIN
        block.transition.clear_error()
        block.transition.request_transition = False
        block.transition.changed = True
        self.persistence.save_step(block)

        # Reset errored ancestors so the workflow resumes
        self._reset_errored_ancestors(block)

        logger.info("Reset block done: step_id=%s deleted=%d", step_id, len(descendant_ids))
        return len(descendant_ids)

    def rerun_step(self, step_id: StepId) -> dict:
        """Re-run a completed step by resetting it and deleting downstream steps.

        Resets the step to EVENT_TRANSMIT regardless of its current state,
        rebuilds the dependency graph for the containing block, deletes all
        downstream dependent steps (and their sub-blocks/tasks/logs), and
        resets ancestor blocks so execution resumes from this step.

        Args:
            step_id: The step ID to re-run.

        Returns:
            Dict with counts: {"reset": 1, "deleted": N}
        """
        logger.info("Rerun step: step_id=%s", step_id)
        step = self.persistence.get_step(step_id)
        if step is None:
            raise ValueError(f"Step {step_id} not found")

        # Find the runner to get the compiled AST for dependency resolution
        all_steps = list(self.persistence.get_steps_by_workflow(step.workflow_id))
        block_id = step.block_id

        # Find downstream statement IDs using the dependency graph
        downstream_stmt_ids: set[str] = set()
        if block_id and step.statement_id:
            downstream_stmt_ids = self._find_downstream_statements(
                step,
                all_steps,
            )

        # Collect step IDs to delete: all steps in same block whose
        # statement_id is downstream, plus all their descendants
        by_block: dict[str, list[str]] = {}
        by_container: dict[str, list[str]] = {}
        for s in all_steps:
            if s.block_id:
                by_block.setdefault(s.block_id, []).append(s.id)
            if s.container_id:
                by_container.setdefault(s.container_id, []).append(s.id)

        # Find sibling steps in the same block that are downstream
        to_delete: list[str] = []
        for s in all_steps:
            if s.block_id == block_id and str(s.statement_id) in downstream_stmt_ids:
                to_delete.append(s.id)
                # Also collect all descendants of this step
                stack: list[str] = [s.id]
                seen: set[str] = {s.id}
                while stack:
                    sid = stack.pop()
                    for child_id in by_block.get(sid, []) + by_container.get(sid, []):
                        if child_id not in seen:
                            seen.add(child_id)
                            to_delete.append(child_id)
                            stack.append(child_id)

        # Delete downstream steps
        if to_delete:
            self.persistence.delete_step_logs_for_steps(to_delete)
            self.persistence.delete_tasks_for_steps(to_delete)
            self.persistence.delete_steps(to_delete)

        # Reset the target step to EVENT_TRANSMIT
        step.state = StepState.EVENT_TRANSMIT
        step.transition.current_state = StepState.EVENT_TRANSMIT
        step.transition.clear_error()
        step.transition.request_transition = False
        step.transition.changed = True
        step.attributes.returns = {}  # Clear old results
        self.persistence.save_step(step)

        # Reset associated task to pending
        task = self.persistence.get_task_for_step(step_id)
        if task is not None:
            task.state = "pending"
            task.error = None
            self.persistence.save_task(task)

        # Reset ancestor blocks so execution resumes
        self._reset_ancestors_to_continue(step)

        logger.info(
            "Rerun step done: step_id=%s deleted=%d downstream",
            step_id,
            len(to_delete),
        )
        return {"reset": 1, "deleted": len(to_delete)}

    def _find_downstream_statements(
        self,
        target_step: StepDefinition,
        all_steps: list[StepDefinition],
    ) -> set[str]:
        """Find all statement IDs that are transitively downstream of a step.

        Uses the step references in the same block to infer dependencies
        without needing the compiled AST.
        """
        target_stmt_id = str(target_step.statement_id)
        block_id = target_step.block_id

        # Get all sibling steps in the same block
        siblings = [s for s in all_steps if s.block_id == block_id]

        # Build a reverse dependency map from step attributes:
        # If step B's params reference step A's returns, B depends on A.
        # We can infer this from statement_name references in param values.
        stmt_name_to_id: dict[str, str] = {}
        for s in siblings:
            if s.statement_name:
                stmt_name_to_id[s.statement_name] = str(s.statement_id)

        # Build dependency edges by scanning parameter values for step references
        deps: dict[str, set[str]] = {}
        for s in siblings:
            stmt_id = str(s.statement_id)
            deps.setdefault(stmt_id, set())
            # Check if any param values reference other step names
            for _, attr in s.attributes.params.items():
                refs = self._extract_step_refs_from_value(attr.value)
                for ref_name in refs:
                    dep_stmt_id = stmt_name_to_id.get(ref_name)
                    if dep_stmt_id:
                        deps[stmt_id].add(dep_stmt_id)

        # Find all statements transitively downstream of target
        downstream: set[str] = set()
        # Reverse the graph: for each stmt, find what depends on it
        reverse: dict[str, set[str]] = {}
        for stmt_id, dep_set in deps.items():
            for dep_id in dep_set:
                reverse.setdefault(dep_id, set()).add(stmt_id)

        # BFS from target
        queue = [target_stmt_id]
        while queue:
            current = queue.pop(0)
            for dependent in reverse.get(current, set()):
                if dependent not in downstream:
                    downstream.add(dependent)
                    queue.append(dependent)

        return downstream

    def _extract_step_refs_from_value(self, value: Any) -> set[str]:
        """Extract step reference names from an attribute value."""
        refs: set[str] = set()
        if isinstance(value, dict):
            # Could be a structured value with step refs embedded
            for v in value.values():
                refs |= self._extract_step_refs_from_value(v)
        elif isinstance(value, list):
            for item in value:
                refs |= self._extract_step_refs_from_value(item)
        return refs

    def _reset_ancestors_to_continue(self, step: StepDefinition) -> None:
        """Reset ancestor blocks/containers to continue state.

        Like _reset_errored_ancestors but works on any terminal state,
        not just errors.
        """
        seen: set[str] = set()

        current_id = step.block_id
        while current_id and current_id not in seen:
            seen.add(current_id)
            ancestor = self.persistence.get_step(current_id)
            if ancestor is None:
                break
            if StepState.is_terminal(ancestor.state):
                ancestor.state = StepState.BLOCK_EXECUTION_CONTINUE
                ancestor.transition.current_state = StepState.BLOCK_EXECUTION_CONTINUE
                ancestor.transition.clear_error()
                ancestor.transition.request_transition = False
                ancestor.transition.changed = True
                self.persistence.save_step(ancestor)
            current_id = ancestor.block_id

        current_id = step.container_id
        while current_id and current_id not in seen:
            seen.add(current_id)
            ancestor = self.persistence.get_step(current_id)
            if ancestor is None:
                break
            if StepState.is_terminal(ancestor.state):
                ancestor.state = StepState.STATEMENT_BLOCKS_CONTINUE
                ancestor.transition.current_state = StepState.STATEMENT_BLOCKS_CONTINUE
                ancestor.transition.clear_error()
                ancestor.transition.request_transition = False
                ancestor.transition.changed = True
                self.persistence.save_step(ancestor)
            next_id = ancestor.block_id or ancestor.container_id
            current_id = next_id

    def _reset_errored_ancestors(self, step: StepDefinition) -> None:
        """Reset errored ancestor blocks/containers so execution can resume.

        Walks up the block_id and container_id chain from *step*, resetting
        any ancestor that is in STATEMENT_ERROR back to its appropriate
        continue state (BLOCK_EXECUTION_CONTINUE for andThen blocks,
        STATEMENT_BLOCKS_CONTINUE for statement containers).
        """
        seen: set[str] = set()

        # Walk up block_id chain (andThen blocks)
        current_id = step.block_id
        while current_id and current_id not in seen:
            seen.add(current_id)
            ancestor = self.persistence.get_step(current_id)
            if ancestor is None:
                break
            if ancestor.state == StepState.STATEMENT_ERROR:
                ancestor.state = StepState.BLOCK_EXECUTION_CONTINUE
                ancestor.transition.current_state = StepState.BLOCK_EXECUTION_CONTINUE
                ancestor.transition.clear_error()
                ancestor.transition.request_transition = False
                ancestor.transition.changed = True
                self.persistence.save_step(ancestor)
                logger.info(
                    "Reset ancestor block: step_id=%s to BLOCK_EXECUTION_CONTINUE",
                    current_id,
                )
            current_id = ancestor.block_id

        # Walk up container_id chain (statement containers)
        current_id = step.container_id
        while current_id and current_id not in seen:
            seen.add(current_id)
            ancestor = self.persistence.get_step(current_id)
            if ancestor is None:
                break
            if ancestor.state == StepState.STATEMENT_ERROR:
                ancestor.state = StepState.STATEMENT_BLOCKS_CONTINUE
                ancestor.transition.current_state = StepState.STATEMENT_BLOCKS_CONTINUE
                ancestor.transition.clear_error()
                ancestor.transition.request_transition = False
                ancestor.transition.changed = True
                self.persistence.save_step(ancestor)
                logger.info(
                    "Reset ancestor container: step_id=%s to STATEMENT_BLOCKS_CONTINUE",
                    current_id,
                )
            # Continue up: block_id then container_id
            next_id = ancestor.block_id or ancestor.container_id
            current_id = next_id

    def resume(
        self,
        workflow_id_val: str,
        workflow_ast: dict,
        program_ast: dict | None = None,
        inputs: dict[str, Any] | None = None,
        runner_id: str = "",
        dispatcher: "HandlerDispatcher | None" = None,
    ) -> ExecutionResult:
        """Resume execution of a paused workflow.

        Reconstructs execution context and runs the iteration loop.

        Args:
            workflow_id_val: The workflow ID to resume
            workflow_ast: The workflow AST
            program_ast: Optional program AST for facet lookups
            inputs: Original input values
            runner_id: Optional runner ID for task creation context
            dispatcher: Optional handler dispatcher for inline event execution

        Returns:
            ExecutionResult
        """
        logger.info("Workflow resuming: workflow_id=%s", workflow_id_val)

        defaults = self._extract_defaults(workflow_ast, inputs or {})

        context = ExecutionContext(
            persistence=self.persistence,
            telemetry=self.telemetry,
            changes=IterationChanges(),
            workflow_id=workflow_id_val,
            workflow_ast=workflow_ast,
            workflow_defaults=defaults,
            program_ast=program_ast,
            runner_id=runner_id,
            dispatcher=dispatcher,
        )

        try:
            iteration = 0
            while iteration < self.max_iterations:
                iteration += 1
                self.telemetry.log_iteration_start(workflow_id_val, iteration)
                context.clear_caches()

                progress = self._run_iteration(context)

                self.telemetry.log_iteration_end(
                    workflow_id_val,
                    iteration,
                    len(context.changes.created_steps),
                    len(context.changes.updated_steps),
                )

                # After first iteration, switch to dirty-block tracking.
                # Seed the set from steps that changed this iteration
                # (before commit clears the changes).
                if context._dirty_blocks is None:
                    dirty: set[str] = set()
                    for s in context.changes.updated_steps:
                        if s.block_id:
                            dirty.add(s.block_id)
                        if s.container_id:
                            dirty.add(s.container_id)
                    context._dirty_blocks = dirty

                self._commit_iteration(context)

                if not progress:
                    if self._has_event_blocked_steps(context):
                        return ExecutionResult(
                            success=True,
                            workflow_id=workflow_id_val,
                            iterations=iteration,
                            status=ExecutionStatus.PAUSED,
                        )
                    break

            result = self._build_result(workflow_id_val, iteration)
            logger.info(
                "Workflow resumed: workflow_id=%s status=%s iterations=%d",
                workflow_id_val,
                result.status,
                result.iterations,
            )
            return result

        except Exception as e:
            self.telemetry.log_workflow_error(workflow_id_val, e)
            logger.error(
                "Workflow resume failed: workflow_id=%s error=%s",
                workflow_id_val,
                e,
            )
            return ExecutionResult(
                success=False,
                workflow_id=workflow_id_val,
                error=e,
                status=ExecutionStatus.ERROR,
            )

    def resume_step(
        self,
        workflow_id_val: str,
        step_id: str,
        workflow_ast: dict,
        program_ast: dict | None = None,
        runner_id: str = "",
    ) -> ExecutionResult:
        """Resume execution via parent notification cascade.

        Event-driven: processes the continued step, then follows
        notifications up to parent blocks.  When a step progresses,
        ``_process_step`` marks its ``block_id`` and ``container_id``
        dirty.  The next round processes only those notified parents,
        which may in turn notify *their* parents, cascading to the root.

        This is O(depth) — only steps that receive a child-completion
        notification are loaded and processed.

        Args:
            workflow_id_val: The workflow ID
            step_id: The step that was continued via ``continue_step()``
            workflow_ast: The workflow AST
            program_ast: Optional full program AST
            runner_id: Optional runner ID

        Returns:
            ExecutionResult
        """
        logger.info(
            "Workflow resume_step: workflow_id=%s step_id=%s",
            workflow_id_val,
            step_id,
        )

        defaults = self._extract_defaults(workflow_ast, {})

        context = ExecutionContext(
            persistence=self.persistence,
            telemetry=self.telemetry,
            changes=IterationChanges(),
            workflow_id=workflow_id_val,
            workflow_ast=workflow_ast,
            workflow_defaults=defaults,
            program_ast=program_ast,
            runner_id=runner_id,
            _dirty_blocks=set(),
        )

        try:
            max_iterations = 50
            total_iterations = 0

            # Start with the continued step as the initial work item
            work_queue: list[str] = [step_id]

            for _iteration in range(1, max_iterations + 1):
                if not work_queue:
                    break

                context.changes = IterationChanges()
                # Reset dirty set — will be populated by _process_step
                # when steps progress and notify their parents
                context._dirty_blocks = set()
                processed_ids: set[str] = set()

                # Process all steps in the current notification batch
                for current_id in work_queue:
                    if current_id in processed_ids:
                        continue
                    step = self.persistence.get_step(current_id)
                    if step is None or step.is_terminal:
                        continue
                    processed_ids.add(current_id)
                    self._process_step(step, context)

                # Process any newly created steps (e.g. block children
                # from StatementBlocksBeginHandler). These may in turn
                # notify their parents via _process_step → mark_block_dirty.
                while True:
                    unprocessed = [
                        s for s in context.changes.created_steps if s.id not in processed_ids
                    ]
                    if not unprocessed:
                        break
                    for ns in unprocessed:
                        processed_ids.add(ns.id)
                        self._process_step(ns, context)

                if not context.changes.has_changes:
                    break

                self._commit_iteration(context)
                total_iterations += 1

                # Build next round's work queue from notified parents.
                # _process_step marked block_id/container_id dirty for
                # every step that progressed — those are the parents that
                # need to re-evaluate their children.
                #
                # Note: do NOT filter by processed_ids here.  A block may
                # have been processed earlier in this iteration (before a
                # child completed) and now needs re-evaluation because a
                # sibling or descendant progressed.  Excluding it would
                # prevent the block from creating newly-ready sibling
                # steps (e.g. MergeSummaries after SummarizeChunk foreach
                # completes).
                work_queue = list(context._dirty_blocks)

            logger.info(
                "resume_step done: workflow_id=%s iterations=%d",
                workflow_id_val,
                total_iterations,
            )

            # Check if the workflow root reached a terminal state
            root = self.persistence.get_workflow_root(workflow_id_val)
            if root and root.is_complete:
                outputs = {name: attr.value for name, attr in root.attributes.returns.items()}
                self.telemetry.log_workflow_complete(workflow_id_val, outputs)
                return ExecutionResult(
                    success=True,
                    workflow_id=workflow_id_val,
                    outputs=outputs,
                    iterations=total_iterations,
                    status=ExecutionStatus.COMPLETED,
                )
            elif root and root.is_error:
                error = root.transition.error or Exception("Workflow error")
                self.telemetry.log_workflow_error(workflow_id_val, error)
                return ExecutionResult(
                    success=False,
                    workflow_id=workflow_id_val,
                    error=error,
                    iterations=total_iterations,
                    status=ExecutionStatus.ERROR,
                )

            return ExecutionResult(
                success=True,
                workflow_id=workflow_id_val,
                iterations=total_iterations,
                status=ExecutionStatus.PAUSED,
            )

        except Exception as e:
            logger.error(
                "resume_step failed: workflow_id=%s step_id=%s error=%s",
                workflow_id_val,
                step_id,
                e,
            )
            return ExecutionResult(
                success=False,
                workflow_id=workflow_id_val,
                error=e,
                status=ExecutionStatus.ERROR,
            )

    def process_single_step(
        self,
        step_id: str,
        workflow_ast: dict,
        program_ast: dict | None = None,
        runner_id: str = "",
        dispatcher: "HandlerDispatcher | None" = None,
    ) -> ExecutionResult:
        """Process a single step and generate continuation events.

        Event-driven per-step processing suitable for multi-server
        execution.  Processes the target step (and any children it
        creates), then generates continuation tasks for parent blocks
        so they can be picked up by any server.

        All changes (step updates, new steps, handler tasks, and
        continuation tasks) are committed atomically.

        Args:
            step_id: The step to process
            workflow_ast: The workflow AST
            program_ast: Optional full program AST
            runner_id: Optional runner ID
            dispatcher: Optional handler dispatcher for inline execution

        Returns:
            ExecutionResult
        """
        from .continuation import generate_continuation_events

        step = self.persistence.get_step(step_id)
        if step is None:
            logger.warning("process_single_step: step %s not found", step_id)
            return ExecutionResult(
                success=False,
                workflow_id="",
                error=ValueError(f"Step {step_id} not found"),
                status=ExecutionStatus.ERROR,
            )

        workflow_id_val = step.workflow_id
        logger.info(
            "process_single_step: step_id=%s workflow_id=%s state=%s",
            step_id,
            workflow_id_val,
            step.state,
        )

        defaults = self._extract_defaults(workflow_ast, {})

        context = ExecutionContext(
            persistence=self.persistence,
            telemetry=self.telemetry,
            changes=IterationChanges(),
            workflow_id=workflow_id_val,
            workflow_ast=workflow_ast,
            workflow_defaults=defaults,
            program_ast=program_ast,
            runner_id=runner_id,
            dispatcher=dispatcher,
            _dirty_blocks=set(),
        )

        try:
            max_rounds = 50
            total_rounds = 0

            # Start with the target step
            work_queue: list[str] = [step_id]

            for _round in range(max_rounds):
                if not work_queue:
                    break

                context.changes = IterationChanges()
                context._dirty_blocks = set()
                processed_ids: set[str] = set()

                # Process all steps in the current batch
                for current_id in work_queue:
                    if current_id in processed_ids:
                        continue
                    s = self.persistence.get_step(current_id)
                    if s is None or s.is_terminal:
                        continue
                    processed_ids.add(s.id)
                    self._process_step(s, context)

                # Process any newly created children (cascading creation)
                while True:
                    unprocessed = [
                        s for s in context.changes.created_steps if s.id not in processed_ids
                    ]
                    if not unprocessed:
                        break
                    for ns in unprocessed:
                        processed_ids.add(ns.id)
                        self._process_step(ns, context)

                if not context.changes.has_changes:
                    break

                # Bump version on updated steps for optimistic concurrency
                for s in context.changes.updated_steps:
                    s.version.increment()

                self._commit_iteration(context)
                total_rounds += 1

                # Build next round's work queue from notified parents.
                # This follows the parent chain up to the root within
                # the same call — no separate continuation tasks needed
                # for intra-process cascading.
                work_queue = [bid for bid in context._dirty_blocks if bid not in processed_ids]

            # Generate continuation events only for remaining dirty
            # blocks that weren't processed (e.g., steps on other
            # servers' workflows, or if we hit the round limit).
            remaining_dirty = {
                bid for bid in (context._dirty_blocks or set()) if bid not in processed_ids
            }
            if remaining_dirty:
                context.changes = IterationChanges()
                generate_continuation_events(
                    context.changes,
                    dirty_blocks=remaining_dirty,
                )
                if context.changes.has_changes:
                    # Need workflow_id for the continuation tasks
                    for t in context.changes.continuation_tasks:
                        t.workflow_id = workflow_id_val
                    self._commit_iteration(context)

            # Check if workflow completed
            root = self.persistence.get_workflow_root(workflow_id_val)
            if root and root.is_complete:
                outputs = {name: attr.value for name, attr in root.attributes.returns.items()}
                return ExecutionResult(
                    success=True,
                    workflow_id=workflow_id_val,
                    outputs=outputs,
                    iterations=1,
                    status=ExecutionStatus.COMPLETED,
                )
            elif root and root.is_error:
                return ExecutionResult(
                    success=False,
                    workflow_id=workflow_id_val,
                    error=root.transition.error or Exception("Workflow error"),
                    iterations=1,
                    status=ExecutionStatus.ERROR,
                )

            return ExecutionResult(
                success=True,
                workflow_id=workflow_id_val,
                iterations=1,
                status=ExecutionStatus.PAUSED,
            )

        except Exception as e:
            logger.error(
                "process_single_step failed: step_id=%s error=%s",
                step_id,
                e,
                exc_info=True,
            )
            return ExecutionResult(
                success=False,
                workflow_id=workflow_id_val,
                error=e,
                status=ExecutionStatus.ERROR,
            )

    def _commit_iteration(self, context: ExecutionContext) -> None:
        """Commit all changes from an iteration.

        Args:
            context: Execution context
        """
        if context.changes.has_changes:
            logger.info(
                "Iteration commit: workflow_id=%s created=%d updated=%d tasks=%d continuations=%d",
                context.workflow_id,
                len(context.changes.created_steps),
                len(context.changes.updated_steps),
                len(context.changes.created_tasks),
                len(context.changes.continuation_tasks),
            )
            self.persistence.commit(context.changes)
            context.changes.clear()

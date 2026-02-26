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

"""Block execution handlers.

Handles the BlockExecutionBegin/Continue/End states for AndThen blocks.
This is the core of block execution - creating child steps and
monitoring their completion.
"""

import logging
from typing import TYPE_CHECKING

logger = logging.getLogger(__name__)

from ..block import StepAnalysis
from ..changers.base import StateChangeResult
from ..dependency import DependencyGraph
from ..script_executor import ScriptExecutor
from .base import StateHandler

if TYPE_CHECKING:
    pass


class BlockExecutionBeginHandler(StateHandler):
    """Handler for state.block.execution.Begin.

    Initializes block execution by analyzing the block structure
    and creating steps for any statements that are ready.
    """

    def process_state(self) -> StateChangeResult:
        """Begin block execution."""
        # Get the block AST
        block_ast = self.context.get_block_ast(self.step)
        if block_ast is None:
            # No block definition, complete immediately
            self.step.request_state_change(True)
            return StateChangeResult(step=self.step)

        # Check for andThen script block
        if "script" in block_ast:
            return self._execute_script_block(block_ast["script"])

        # Check for foreach clause
        if "foreach" in block_ast:
            return self._process_foreach(block_ast)

        # Build dependency graph
        workflow_inputs = self._get_workflow_inputs()
        graph = DependencyGraph.from_ast(
            block_ast, workflow_inputs, program_ast=self.context.program_ast
        )

        # Store graph for continue phase
        self.context.set_block_graph(self.step.id, graph)

        all_stmts = graph.get_all_statements()
        ready = graph.get_ready_statements(set())
        logger.debug(
            "Block execution begin: block_id=%s statements=%d ready=%d",
            self.step.id,
            len(all_stmts),
            len(ready),
        )

        # Create steps for statements with no dependencies
        self._create_ready_steps(graph, set())

        self.step.request_state_change(True)
        return StateChangeResult(step=self.step)

    def _process_foreach(self, block_ast: dict) -> StateChangeResult:
        """Process a foreach block by creating sub-blocks per element.

        Args:
            block_ast: The block AST with a "foreach" key

        Returns:
            StateChangeResult
        """
        from ..expression import EvaluationContext, ExpressionEvaluator
        from ..step import StepDefinition
        from ..types import ObjectType

        foreach = block_ast["foreach"]
        variable = foreach.get("variable", "")
        iterable_expr = foreach.get("iterable")

        # Build body AST (block_ast without foreach)
        body_ast = {k: v for k, v in block_ast.items() if k != "foreach"}

        # Evaluate the iterable expression
        inputs = self._build_foreach_eval_inputs()
        eval_ctx = EvaluationContext(
            inputs=inputs,
            get_step_output=lambda s, a: None,
            step_id=self.step.id,
        )
        evaluator = ExpressionEvaluator()
        iterable = evaluator.evaluate(iterable_expr, eval_ctx)

        if not iterable:
            # Empty iterable — no sub-blocks to create, complete immediately
            self.step.request_state_change(True)
            return StateChangeResult(step=self.step)

        # Create a sub-block for each element
        for i, element in enumerate(iterable):
            foreach_stmt_id = f"foreach-{i}"

            # Idempotency: skip if sub-block already exists in DB
            if self.context.persistence.step_exists(foreach_stmt_id, self.step.id):
                continue

            # Also check pending creates in current iteration
            already_pending = any(
                str(p.statement_id) == foreach_stmt_id and p.block_id == self.step.id
                for p in self.context.changes.created_steps
            )
            if already_pending:
                continue

            sub_block = StepDefinition.create(
                workflow_id=self.step.workflow_id,
                object_type=ObjectType.AND_THEN,
                facet_name="",
                statement_id=foreach_stmt_id,
                container_id=self.step.container_id,
                block_id=self.step.id,
                root_id=self.step.root_id or self.step.container_id,
            )
            sub_block.foreach_var = variable
            sub_block.foreach_value = element

            # Cache the body AST for this sub-block
            self.context.set_block_ast_cache(sub_block.id, body_ast)

            logger.debug(
                "Foreach sub-block created: block_id=%s index=%d var=%s value=%s",
                sub_block.id,
                i,
                variable,
                element,
            )
            self.context.changes.add_created_step(sub_block)

        self.step.request_state_change(True)
        return StateChangeResult(step=self.step)

    def _build_foreach_eval_inputs(self) -> dict:
        """Build input dict for evaluating the foreach iterable expression."""
        # Get workflow root params
        workflow_root = self.context.get_workflow_root()
        inputs = {}
        if workflow_root:
            for name, attr in workflow_root.attributes.params.items():
                inputs[name] = attr.value
        return inputs

    def _get_workflow_inputs(self) -> set[str]:
        """Get valid input parameter names for this block's scope.

        For the workflow root block, returns workflow params.
        For nested blocks, returns the container's facet params.
        """
        # If this block's container is the workflow root (no container_id on container),
        # use workflow params
        if self.step.container_id:
            container = self.context._find_step(self.step.container_id)
            if container and container.facet_name:
                facet_def = self.context.get_facet_definition(container.facet_name)
                if facet_def:
                    params = facet_def.get("params", [])
                    return {p.get("name", "") for p in params}

        # Fall back to workflow params
        workflow_ast = self.context.get_workflow_ast()
        if workflow_ast:
            params = workflow_ast.get("params", [])
            return {p.get("name", "") for p in params}
        return set()

    def _create_ready_steps(
        self,
        graph: DependencyGraph,
        completed: set[str],
    ) -> None:
        """Create steps for statements that are ready.

        Args:
            graph: The dependency graph
            completed: Set of completed statement IDs
        """
        from ..step import StepDefinition

        ready = graph.get_ready_statements(completed)
        for stmt in ready:
            # Check if step already exists (idempotency)
            if self.context.persistence.step_exists(stmt.id, self.step.id):
                continue

            # Create the step
            step = StepDefinition.create(
                workflow_id=self.step.workflow_id,
                object_type=stmt.object_type,
                facet_name=stmt.facet_name,
                statement_id=stmt.id,
                statement_name=stmt.name,
                block_id=self.step.id,
                container_id=self.step.container_id,
                root_id=self.step.root_id or self.step.container_id,
            )

            logger.debug(
                "Step created: statement_id=%s facet_name=%s block_id=%s",
                stmt.id,
                stmt.facet_name,
                self.step.id,
            )
            self.context.changes.add_created_step(step)

    def _execute_script_block(self, script_ast: dict) -> StateChangeResult:
        """Execute an andThen script block.

        Runs the script with the container step's params as input and
        stores results as returns on this block step.

        Args:
            script_ast: ScriptBlock AST dict with "code" and "language" keys.

        Returns:
            StateChangeResult
        """
        # Build params from the container step's attributes
        params: dict = {}
        container = self.context._find_step(self.step.container_id) if self.step.container_id else None
        if container:
            for name, attr in container.attributes.params.items():
                params[name] = attr.value

        code = script_ast.get("code", "")
        language = script_ast.get("language", "python")
        executor = ScriptExecutor()
        result = executor.execute(code, params, language)

        if not result.success:
            return self.error(RuntimeError(result.error or "andThen script execution failed"))

        # Store results as returns on this block step
        for name, value in result.result.items():
            self.step.set_attribute(name, value, is_return=True)

        self.step.request_state_change(True)
        return StateChangeResult(step=self.step)


class BlockExecutionContinueHandler(StateHandler):
    """Handler for state.block.execution.Continue.

    Polls block progress, creates newly eligible steps,
    and determines when block is complete.
    """

    def process_state(self) -> StateChangeResult:
        """Continue block execution."""
        # Check if this is a foreach block — use sub-block tracking instead
        block_ast = self.context.get_block_ast(self.step)
        if block_ast and "foreach" in block_ast:
            return self._continue_foreach()

        # Get the dependency graph (may need to rebuild after resume)
        graph = self.context.get_block_graph(self.step.id)
        if graph is None:
            # Try to rebuild the graph (needed after evaluator resume)
            graph = self._rebuild_graph()
            if graph is None:
                # Truly no statements, complete
                self.step.request_state_change(True)
                return StateChangeResult(step=self.step)

        # Load current steps in this block
        steps = list(self.context.persistence.get_steps_by_block(self.step.id))

        # Include pending created steps
        for pending in self.context.changes.created_steps:
            if pending.block_id == self.step.id and pending not in steps:
                steps.append(pending)

        # Include pending updated steps
        for pending in self.context.changes.updated_steps:
            for i, s in enumerate(steps):
                if s.id == pending.id:
                    steps[i] = pending

        # Build analysis
        analysis = StepAnalysis.load(
            block=self.step,
            statements=graph.get_all_statements(),
            steps=steps,
        )

        completed, total = analysis.completion_progress
        logger.debug(
            "Block execution continue: block_id=%s progress=%d/%d done=%s blocked=%s",
            self.step.id,
            completed,
            total,
            analysis.done,
            analysis.is_blocked(),
        )

        if analysis.done:
            # All statements terminal (complete or error)
            if analysis.has_errors:
                errors = [s.transition.error for s in analysis.errored if s.transition.error]
                msg = f"Block has {len(analysis.errored)} errored step(s)"
                if errors:
                    msg += f": {errors[0]}"
                self.step.mark_error(RuntimeError(msg))
                return StateChangeResult(step=self.step)
            self.step.request_state_change(True)
            return StateChangeResult(step=self.step)

        # Create steps for newly ready statements (errored deps also satisfy)
        terminal_ids = {str(s.statement_id) for s in analysis.completed if s.statement_id}
        terminal_ids |= {str(s.statement_id) for s in analysis.errored if s.statement_id}
        self._create_ready_steps(graph, terminal_ids)

        # Check if we made progress
        if analysis.has_pending_work():
            # More work to do, push for retry
            return self.stay(push=True)
        elif analysis.is_blocked():
            # Blocked waiting on dependencies, push for later
            return self.stay(push=True)
        else:
            # Waiting for steps to complete
            return self.stay(push=True)

    def _continue_foreach(self) -> StateChangeResult:
        """Continue a foreach block by checking sub-block completion.

        For foreach blocks, we track sub-blocks (children with block_id=self.step.id)
        instead of using a DependencyGraph.

        Returns:
            StateChangeResult
        """
        # Get all sub-blocks
        sub_blocks = list(self.context.persistence.get_steps_by_block(self.step.id))

        # Include pending created/updated sub-blocks
        for pending in self.context.changes.created_steps:
            if pending.block_id == self.step.id and pending not in sub_blocks:
                sub_blocks.append(pending)
        for pending in self.context.changes.updated_steps:
            for i, s in enumerate(sub_blocks):
                if s.id == pending.id:
                    sub_blocks[i] = pending

        if not sub_blocks:
            # No sub-blocks (empty iterable), complete
            self.step.request_state_change(True)
            return StateChangeResult(step=self.step)

        completed = [s for s in sub_blocks if s.is_complete]
        errored = [s for s in sub_blocks if s.is_error]
        terminal = len(completed) + len(errored)
        total = len(sub_blocks)

        logger.debug(
            "Foreach block continue: block_id=%s progress=%d/%d errored=%d",
            self.step.id,
            terminal,
            total,
            len(errored),
        )

        if terminal == total:
            if errored:
                msg = f"Foreach block has {len(errored)} errored sub-block(s)"
                self.step.mark_error(RuntimeError(msg))
                return StateChangeResult(step=self.step)
            self.step.request_state_change(True)
            return StateChangeResult(step=self.step)

        return self.stay(push=True)

    def _rebuild_graph(self):
        """Rebuild the dependency graph for this block.

        This is needed when the evaluator resumes after a pause,
        as the cached graphs are lost.

        Returns:
            DependencyGraph or None
        """
        block_ast = self.context.get_block_ast(self.step)
        if block_ast is None:
            return None

        workflow_inputs = self._get_workflow_inputs()
        graph = DependencyGraph.from_ast(
            block_ast, workflow_inputs, program_ast=self.context.program_ast
        )
        self.context.set_block_graph(self.step.id, graph)
        return graph

    def _get_workflow_inputs(self) -> set[str]:
        """Get valid input parameter names for this block's scope."""
        if self.step.container_id:
            container = self.context._find_step(self.step.container_id)
            if container and container.facet_name:
                facet_def = self.context.get_facet_definition(container.facet_name)
                if facet_def:
                    params = facet_def.get("params", [])
                    return {p.get("name", "") for p in params}

        workflow_ast = self.context.get_workflow_ast()
        if workflow_ast:
            params = workflow_ast.get("params", [])
            return {p.get("name", "") for p in params}
        return set()

    def _create_ready_steps(
        self,
        graph: DependencyGraph,
        completed: set[str],
    ) -> None:
        """Create steps for statements that are ready."""
        from ..step import StepDefinition

        ready = graph.get_ready_statements(completed)
        for stmt in ready:
            # Check if step already exists (idempotency)
            if self.context.persistence.step_exists(stmt.id, self.step.id):
                continue

            # Check if already in pending creates
            already_pending = False
            for pending in self.context.changes.created_steps:
                if str(pending.statement_id) == stmt.id and pending.block_id == self.step.id:
                    already_pending = True
                    break

            if already_pending:
                continue

            # Create the step
            step = StepDefinition.create(
                workflow_id=self.step.workflow_id,
                object_type=stmt.object_type,
                facet_name=stmt.facet_name,
                statement_id=stmt.id,
                statement_name=stmt.name,
                block_id=self.step.id,
                container_id=self.step.container_id,
                root_id=self.step.root_id or self.step.container_id,
            )

            logger.debug(
                "Step created: statement_id=%s facet_name=%s block_id=%s",
                stmt.id,
                stmt.facet_name,
                self.step.id,
            )
            self.context.changes.add_created_step(step)


class BlockExecutionEndHandler(StateHandler):
    """Handler for state.block.execution.End.

    Completes block execution.
    """

    def process_state(self) -> StateChangeResult:
        """End block execution."""
        self.step.request_state_change(True)
        return StateChangeResult(step=self.step)

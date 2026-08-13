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
from typing import TYPE_CHECKING, Any

logger = logging.getLogger(__name__)

from ..block import StepAnalysis
from ..changers.base import StateChangeResult
from ..dependency import DependencyGraph
from ..script_executor import ScriptExecutor
from .base import StateHandler

if TYPE_CHECKING:
    pass

# Window state for a `foreach … limit N` fan-out, persisted on the block step.
# Underscore-prefixed so it cannot collide with a user-declared attribute.
_FOREACH_VALUES_ATTR = "_foreach_values"
_FOREACH_LIMIT_ATTR = "_foreach_limit"

# How long a capped foreach's sub-block may sit non-terminal with no progress
# before the window nudges it with a continuation.
#
# Sized against what it competes with, not picked for feel: the stuck-step sweep
# is the only other rescue, and it runs every 5 min, capped at 25 steps and
# 1.5 s per pass — and `get_pending_resume_workflow_ids` deliberately excludes
# block-Continue states, so a wedged fan-out may not even be selected. A window
# needs its slots back in seconds, so this must be far below that. 60 s is long
# enough that a live cascade (sub-second) is never nudged.
_FOREACH_STALL_GRACE_MS = 60_000


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

        # Check for when block
        if "when" in block_ast:
            return self._process_when(block_ast)

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
        from ..relative_scope import build_scope_stack, relative_scoping_enabled
        from ..step import StepDefinition
        from ..types import ObjectType, deterministic_step_id

        foreach = block_ast["foreach"]
        variable = foreach.get("variable", "")
        iterable_expr = foreach.get("iterable")

        # Build body AST (block_ast without foreach)
        body_ast = {k: v for k, v in block_ast.items() if k != "foreach"}

        # Evaluate the iterable expression
        inputs = self._build_foreach_eval_inputs()

        # Step ref resolver — sets a flag when a referenced step is not
        # yet complete, signalling the foreach block to defer.
        _step_not_ready = False

        def get_step_output(step_name: str, attr_name: str):
            nonlocal _step_not_ready
            all_steps = list(self.context.persistence.get_steps_by_workflow(self.step.workflow_id))
            for s in all_steps:
                if s.statement_name == step_name:
                    if s.is_complete:
                        ret = s.attributes.returns.get(attr_name)
                        if ret:
                            return ret.value
                    # Accept steps in blocks.Continue — they have event
                    # returns available even though step_body children
                    # are still running.
                    elif s.attributes.returns.get(attr_name):
                        return s.attributes.returns[attr_name].value
            _step_not_ready = True
            raise ValueError(f"{step_name}.{attr_name} not ready")

        def get_step_by_name(step_name: str):
            nonlocal _step_not_ready
            for s in self.context.persistence.get_steps_by_workflow(self.step.workflow_id):
                if s.statement_name == step_name and s.is_complete:
                    return s
            _step_not_ready = True
            raise ValueError(f"{step_name} not ready")

        def get_step_by_id(step_id: str):
            return self.context.persistence.get_step(step_id)

        def get_mixin_step_by_alias(parent_step_id: str, alias: str):
            from ..mixin_alias import resolve_mixin_step_by_alias

            return resolve_mixin_step_by_alias(
                parent_step_id,
                alias,
                self.context.persistence,
                self.context.get_facet_definition,
            )

        scope_stack = (
            build_scope_stack(self.context, self.step) if relative_scoping_enabled() else None
        )
        eval_ctx = EvaluationContext(
            inputs=inputs,
            get_step_output=get_step_output,
            get_step_by_name=get_step_by_name,
            get_step_by_id=get_step_by_id,
            get_mixin_step_by_alias=get_mixin_step_by_alias,
            step_id=self.step.id,
            scope_stack=scope_stack,
        )
        evaluator = ExpressionEvaluator()
        try:
            iterable = evaluator.evaluate(iterable_expr, eval_ctx)
        except Exception:
            if _step_not_ready:
                logger.debug(
                    "Foreach block deferred: step dependency not ready (block=%s)",
                    self.step.id,
                )
                return self.stay(push=True)
            raise

        if not iterable:
            # Empty iterable — no sub-blocks to create, complete immediately.
            # But the containing facet's declared ARRAY returns must still
            # materialize (as []): with zero iterations no yield ever fires
            # to seed the aggregate, and a downstream `step.attr` reference
            # would otherwise fail with "Attribute not found" far from the
            # cause (found live: a city with zero facilities of a category
            # in the emergency-access pilot).
            container = (
                self.context.persistence.get_step(self.step.container_id)
                if self.step.container_id
                else None
            )
            if container is not None and container.facet_name:
                fdef = self.context.get_facet_definition(container.facet_name) or {}
                changed = False
                for ret in fdef.get("returns", []):
                    name = ret.get("name", "")
                    rtype = ret.get("type") or {}
                    is_array = isinstance(rtype, dict) and rtype.get("type") == "ArrayType"
                    if name and is_array and name not in container.attributes.returns:
                        container.attributes.set_return(name, [], "List")
                        changed = True
                if changed:
                    self.context.changes.add_updated_step(container)
            self.step.request_state_change(True)
            return StateChangeResult(step=self.step)

        # `limit N` — cap how many iterations are in flight at once. The same
        # elements always run; only the width changes. Without a limit nothing
        # below alters the historical behaviour (create every sub-block now).
        limit = self._resolve_foreach_limit(foreach, evaluator, eval_ctx)
        if limit is not None:
            iterable = list(iterable)
            # The refill in _continue_foreach needs the elements again. Re-deriving
            # them there would re-scan every step in the workflow on each poll —
            # ruinous on exactly the wide fan-outs this feature exists for — so
            # stash them on the block step. Only paid when a limit is used.
            self.step.set_attribute(_FOREACH_VALUES_ATTR, iterable)
            self.step.set_attribute(_FOREACH_LIMIT_ATTR, limit)
            logger.info(
                "Foreach fan-out capped: block_id=%s total=%d limit=%d",
                self.step.id,
                len(iterable),
                limit,
            )
            iterable = list(iterable)[:limit]

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
                step_uuid=deterministic_step_id(
                    self.step.workflow_id,
                    self.step.id,
                    foreach_stmt_id,
                    self.step.container_id,
                ),
            )
            sub_block.foreach_var = variable
            sub_block.foreach_value = element
            sub_block.foreach_index = i

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

    def _resolve_foreach_limit(self, foreach: dict, evaluator, eval_ctx) -> int | None:
        """Resolve `foreach … limit N` to a positive int, or None if uncapped.

        The cap may be a literal or a reference (so it can come from a workflow
        parameter), hence the full expression evaluation.

        A limit that cannot be resolved to an integer is a hard error, not a
        silent fallback to unlimited: the whole point of the clause is to keep a
        3,000-wide fan-out from stampeding, and quietly ignoring it would
        reintroduce exactly the failure it prevents.

        **A PARAMETERISED cap of 0 means unbounded.** A literal is a decision the
        author already made, so `limit 0` stays a compile error (write no clause
        instead). But a cap that comes from a parameter is a knob the CALLER
        turns, and "no cap" is a legitimate setting for it — without a way to say
        so, a facet that wants both behaviours has to be written twice, once with
        the clause and once without. Note the difference from an UNRESOLVABLE
        cap, which still fails: 0 is the author supplying an answer, not the
        runtime failing to get one.

        Negative values remain an error at any depth — that is a mistake, not a
        considered setting.
        """
        if "limit" not in foreach:
            return None
        node = foreach["limit"]
        raw = evaluator.evaluate(node, eval_ctx)
        try:
            limit = int(raw)
        except (TypeError, ValueError):
            raise RuntimeError(f"foreach limit must be an integer, got {raw!r}") from None
        # A bare integer in the source is a fixed decision; anything else (a
        # parameter reference, an expression over one) is a knob.
        is_literal = isinstance(node, dict) and node.get("type") in ("Int", "Integer")
        if limit == 0 and not is_literal:
            logger.info("Foreach fan-out uncapped: limit parameter resolved to 0")
            return None
        if limit < 1:
            raise RuntimeError(
                f"foreach limit must be >= 1, got {limit}"
                + ("" if is_literal else " (0 means unbounded for a parameterised cap)")
            )
        return limit

    def _process_when(self, block_ast: dict) -> StateChangeResult:
        """Process a when block by evaluating conditions and creating sub-blocks.

        Semantics:
        - Non-exclusive: ALL matching cases execute concurrently
        - Default case: executes ONLY if no other case matched

        When blocks may reference steps from prior andThen blocks (e.g.
        ``case qc.passed == false``). If a referenced step is not yet
        complete, the when-block defers and will be re-evaluated on a
        later iteration.

        Args:
            block_ast: The block AST with a "when" key

        Returns:
            StateChangeResult
        """
        from ..expression import EvaluationContext, ExpressionEvaluator
        from ..relative_scope import build_scope_stack, relative_scoping_enabled
        from ..step import StepDefinition
        from ..types import ObjectType, deterministic_step_id

        match_ast = block_ast["when"]
        cases = match_ast.get("cases", [])

        # Build evaluation context from workflow inputs + parent step outputs
        inputs = self._build_foreach_eval_inputs()

        # Step ref resolver — sets a flag when a referenced step is not
        # yet complete, signalling the when-block to defer.
        _step_not_ready = False

        def get_step_output(step_name: str, attr_name: str):
            nonlocal _step_not_ready
            all_steps = list(self.context.persistence.get_steps_by_workflow(self.step.workflow_id))
            for s in all_steps:
                if s.statement_name == step_name:
                    if s.is_complete:
                        ret = s.attributes.returns.get(attr_name)
                        if ret:
                            return ret.value
                    # Accept steps in blocks.Continue — they have event
                    # returns available even though step_body children
                    # are still running.
                    elif s.attributes.returns.get(attr_name):
                        return s.attributes.returns[attr_name].value
            _step_not_ready = True
            raise ValueError(f"{step_name}.{attr_name} not ready")

        def get_step_by_name(step_name: str):
            nonlocal _step_not_ready
            for s in self.context.persistence.get_steps_by_workflow(self.step.workflow_id):
                if s.statement_name == step_name and s.is_complete:
                    return s
            _step_not_ready = True
            raise ValueError(f"{step_name} not ready")

        def get_step_by_id(step_id: str):
            return self.context.persistence.get_step(step_id)

        def get_mixin_step_by_alias(parent_step_id: str, alias: str):
            from ..mixin_alias import resolve_mixin_step_by_alias

            return resolve_mixin_step_by_alias(
                parent_step_id,
                alias,
                self.context.persistence,
                self.context.get_facet_definition,
            )

        scope_stack = (
            build_scope_stack(self.context, self.step) if relative_scoping_enabled() else None
        )
        eval_ctx = EvaluationContext(
            inputs=inputs,
            get_step_output=get_step_output,
            get_step_by_name=get_step_by_name,
            get_step_by_id=get_step_by_id,
            get_mixin_step_by_alias=get_mixin_step_by_alias,
            step_id=self.step.id,
            scope_stack=scope_stack,
        )
        evaluator = ExpressionEvaluator()

        any_matched = False
        has_default = any(case.get("default", False) for case in cases)
        for i, case in enumerate(cases):
            is_default = case.get("default", False)

            if is_default:
                # Default case: only create if no other case matched
                if any_matched:
                    continue
            else:
                # Evaluate condition — defer the entire when-block if a
                # step dependency is not ready yet.
                condition = case.get("condition")
                if condition is None:
                    continue
                try:
                    result = evaluator.evaluate(condition, eval_ctx)
                    if not result:
                        continue
                except Exception as e:
                    if _step_not_ready:
                        logger.debug(
                            "When block deferred: step dependency not ready (block=%s)",
                            self.step.id,
                        )
                        return self.stay(push=True)
                    logger.warning("When case %d condition evaluation failed: %s", i, e)
                    continue
                any_matched = True

            # Create sub-block for this case
            match_stmt_id = f"when-case-{i}"

            # Idempotency: skip if sub-block already exists
            if self.context.persistence.step_exists(match_stmt_id, self.step.id):
                continue

            already_pending = any(
                str(p.statement_id) == match_stmt_id and p.block_id == self.step.id
                for p in self.context.changes.created_steps
            )
            if already_pending:
                continue

            # Build body AST from case's steps/yield
            case_body: dict = {"type": "AndThenBlock"}
            if "steps" in case:
                case_body["steps"] = case["steps"]
            if "yield" in case:
                case_body["yield"] = case["yield"]
            if "yields" in case:
                case_body["yields"] = case["yields"]

            sub_block = StepDefinition.create(
                workflow_id=self.step.workflow_id,
                object_type=ObjectType.AND_THEN,
                facet_name="",
                statement_id=match_stmt_id,
                container_id=self.step.container_id,
                block_id=self.step.id,
                root_id=self.step.root_id or self.step.container_id,
                step_uuid=deterministic_step_id(
                    self.step.workflow_id,
                    self.step.id,
                    match_stmt_id,
                    self.step.container_id,
                ),
            )

            # Cache the body AST for this sub-block
            self.context.set_block_ast_cache(sub_block.id, case_body)

            logger.debug(
                "When case sub-block created: block_id=%s case=%d default=%s",
                sub_block.id,
                i,
                is_default,
            )
            self.context.changes.add_created_step(sub_block)

        # Error if no case matched and no default case exists
        if not any_matched and not has_default:
            return self.error(
                RuntimeError("No when case matched and no default case (case _ =>) provided")
            )

        self.step.request_state_change(True)
        return StateChangeResult(step=self.step)

    def _build_foreach_eval_inputs(self) -> dict:
        """Build input dict for evaluating the foreach iterable expression.

        For nested workflow/facet calls, the container step's params take
        precedence over the workflow root's params.  A nested workflow's
        pre_script results live on the container step, not the top-level
        workflow root.
        """
        inputs = {}

        # Start with workflow root params
        workflow_root = self.context.get_workflow_root()
        if workflow_root:
            for name, attr in workflow_root.attributes.params.items():
                inputs[name] = attr.value

        # Override with container step params (for nested workflow/facet calls)
        if self.step.container_id:
            container = self.context._find_step(self.step.container_id)
            if container:
                for name, attr in container.attributes.params.items():
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
        from ..types import deterministic_step_id

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
                step_uuid=deterministic_step_id(
                    self.step.workflow_id,
                    self.step.id,
                    stmt.id,
                    self.step.container_id,
                ),
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
        container = (
            self.context._find_step(self.step.container_id) if self.step.container_id else None
        )
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
        # Check if this is a foreach or when block — use sub-block tracking
        block_ast = self.context.get_block_ast(self.step)
        if block_ast and "foreach" in block_ast:
            return self._continue_foreach()
        if block_ast and "when" in block_ast:
            return self._continue_when()

        # Get the dependency graph (may need to rebuild after resume)
        graph = self.context.get_block_graph(self.step.id)
        if graph is None:
            # Try to rebuild the graph (needed after evaluator resume)
            graph = self._rebuild_graph()
            if graph is None:
                # Truly no statements, complete
                self.step.request_state_change(True)
                return StateChangeResult(step=self.step)

        # Load current steps in this block. Cached per iteration: the block
        # state machine re-reads the same block on every pass, and on a wide
        # fan-out that was ~3.9k queries in one run.
        steps = self.context.get_steps_by_block_cached(self.step.id)

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

        if analysis.has_errors:
            # Stop the block as soon as any step errors — do not let
            # downstream steps (e.g. yield) run against errored deps.
            # Wait for any still-running siblings to finish first.
            running = [s for s in analysis.steps if not s.is_terminal]
            if not running:
                errors = [s.transition.error for s in analysis.errored if s.transition.error]
                msg = f"Block has {len(analysis.errored)} errored step(s)"
                if errors:
                    msg += f": {errors[0]}"
                self.step.mark_error(RuntimeError(msg))
                return StateChangeResult(step=self.step)
            # Still have running steps — wait for them before erroring
            return self.stay(push=True)

        if analysis.done:
            # All statements completed successfully
            self.step.request_state_change(True)
            return StateChangeResult(step=self.step)

        # Create steps for newly ready statements (only completed deps satisfy)
        terminal_ids = {str(s.statement_id) for s in analysis.completed if s.statement_id}
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

        # `limit N`: refill the window as iterations finish. Absent a limit,
        # `remaining` is 0 and every branch below behaves exactly as before.
        remaining = self._refill_foreach_window(sub_blocks)

        completed = [s for s in sub_blocks if s.is_complete]
        errored = [s for s in sub_blocks if s.is_error]
        terminal = len(completed) + len(errored)
        total = len(sub_blocks)

        logger.debug(
            "Foreach block continue: block_id=%s progress=%d/%d errored=%d unstarted=%d",
            self.step.id,
            terminal,
            total,
            len(errored),
            remaining,
        )

        # Not done until every element has had its turn, not just the ones
        # currently materialized.
        if terminal == total and remaining == 0:
            if errored:
                errors = [s.transition.error for s in errored if s.transition.error]
                msg = f"Foreach block has {len(errored)} errored sub-block(s)"
                if errors:
                    msg += f": {errors[0]}"
                self.step.mark_error(RuntimeError(msg))
                return StateChangeResult(step=self.step)
            self.step.request_state_change(True)
            return StateChangeResult(step=self.step)

        return self.stay(push=True)

    def _refill_foreach_window(self, sub_blocks: list) -> int:
        """Top the `foreach … limit N` window back up to N in-flight iterations.

        Returns how many elements still have no sub-block after this pass, so
        the caller knows the fan-out is not finished merely because everything
        materialized so far is terminal. Returns 0 for an uncapped foreach,
        which leaves the historical behaviour untouched.

        Errored iterations still free their slot and refilling continues, so a
        capped foreach runs the same set of elements — and reports the same
        aggregate error — as an uncapped one. Only the width differs.
        """
        # get_param returns object; these are a resolved int cap + a list of
        # loop values at runtime — annotate Any so len()/int()/indexing type-check.
        limit: Any = self.step.attributes.get_param(_FOREACH_LIMIT_ATTR)
        values: Any = self.step.attributes.get_param(_FOREACH_VALUES_ATTR)
        if not limit or values is None:
            return 0

        created = len(sub_blocks)
        total = len(values)
        if created >= total:
            # Everything is admitted; the block is now just waiting for the last
            # iterations. A stalled straggler here blocks COMPLETION rather than
            # admission, so it needs the same nudge — otherwise the fan-out sits
            # at 3166/3167 forever.
            self._nudge_stalled_sub_blocks(sub_blocks)
            return 0

        in_flight = sum(1 for s in sub_blocks if not s.is_terminal)
        slots = int(limit) - in_flight
        if slots <= 0:
            # Every slot is occupied. If some occupants are stalled rather than
            # working, the fan-out is wedged: a sub-block that finished its work
            # but never cascaded holds its slot forever, and N of those stop the
            # whole run. Nudge them instead of waiting on the 5-minute sweep.
            self._nudge_stalled_sub_blocks(sub_blocks)
            return total - created

        block_ast = self.context.get_block_ast(self.step) or {}
        foreach = block_ast.get("foreach") or {}
        variable = foreach.get("variable", "")
        body_ast = {k: v for k, v in block_ast.items() if k != "foreach"}

        from ..step import StepDefinition
        from ..types import ObjectType, deterministic_step_id

        # Sub-blocks are always created as a contiguous prefix (foreach-0..N-1),
        # so the count of existing ones is the next index.
        for i in range(created, min(created + slots, total)):
            foreach_stmt_id = f"foreach-{i}"
            if self.context.persistence.step_exists(foreach_stmt_id, self.step.id):
                continue
            if any(
                str(p.statement_id) == foreach_stmt_id and p.block_id == self.step.id
                for p in self.context.changes.created_steps
            ):
                continue

            sub_block = StepDefinition.create(
                workflow_id=self.step.workflow_id,
                object_type=ObjectType.AND_THEN,
                facet_name="",
                statement_id=foreach_stmt_id,
                container_id=self.step.container_id,
                block_id=self.step.id,
                root_id=self.step.root_id or self.step.container_id,
                step_uuid=deterministic_step_id(
                    self.step.workflow_id,
                    self.step.id,
                    foreach_stmt_id,
                    self.step.container_id,
                ),
            )
            sub_block.foreach_var = variable
            sub_block.foreach_value = values[i]
            sub_block.foreach_index = i
            self.context.set_block_ast_cache(sub_block.id, body_ast)
            self.context.changes.add_created_step(sub_block)
            sub_blocks.append(sub_block)

        newly_created = len(sub_blocks)
        logger.info(
            "Foreach window refilled: block_id=%s in_flight=%d limit=%d created=%d/%d",
            self.step.id,
            in_flight,
            int(limit),
            newly_created,
            total,
        )
        return total - newly_created

    def _nudge_stalled_sub_blocks(self, sub_blocks: list) -> int:
        """Re-notify capped-foreach sub-blocks that are holding a slot doing nothing.

        A sub-block whose work finished but whose block never cascaded to
        Complete stays non-terminal forever. Uncapped that is a slow tail — the
        other iterations still run. Capped it is fatal: N stalled sub-blocks
        occupy the entire window and no further element is ever admitted.
        Observed live on the 3,167-county atlas fan-out, which stopped at 628
        with 31 of 32 slots held by sub-blocks that had no live task.

        The nudge is a continuation task, the same signal a completing child
        sends, so the step re-enters the normal state machine on whichever
        runner claims it — no direct state surgery, and any runner can service
        it. Continuations dedupe by target step id, so re-nudging on a later
        poll cannot pile up.

        Only reached when the window is already full, so the happy path pays
        nothing. Returns how many were nudged.
        """
        import time as _time

        from ..continuation import CONTINUATION_TASK_LIST, CONTINUATION_TASK_NAME
        from ..entities import TaskDefinition, TaskState
        from ..types import generate_id

        now = int(_time.time() * 1000)
        stalled = [
            s
            for s in sub_blocks
            if not s.is_terminal
            and s.last_modified
            and (now - s.last_modified) > _FOREACH_STALL_GRACE_MS
        ]
        if not stalled:
            return 0

        for s in stalled:
            self.context.changes.add_continuation_task(
                TaskDefinition(
                    uuid=generate_id(),
                    name=CONTINUATION_TASK_NAME,
                    runner_id="",
                    workflow_id=self.step.workflow_id,
                    flow_id="",
                    step_id=str(s.id),
                    state=TaskState.PENDING,
                    created=now,
                    updated=now,
                    task_list_name=CONTINUATION_TASK_LIST,
                    data={"step_id": str(s.id), "reason": "foreach_window_stalled"},
                )
            )

        logger.warning(
            "Foreach window stalled: block_id=%s nudged %d sub-block(s) idle > %ds "
            "(they hold slots but have no progress)",
            self.step.id,
            len(stalled),
            _FOREACH_STALL_GRACE_MS // 1000,
        )
        return len(stalled)

    def _continue_when(self) -> StateChangeResult:
        """Continue a when block by checking sub-block completion.

        For when blocks, we track sub-blocks (children with block_id=self.step.id)
        just like foreach blocks.

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
            # No sub-blocks (no case matched and no default), complete
            self.step.request_state_change(True)
            return StateChangeResult(step=self.step)

        completed = [s for s in sub_blocks if s.is_complete]
        errored = [s for s in sub_blocks if s.is_error]
        terminal = len(completed) + len(errored)
        total = len(sub_blocks)

        logger.debug(
            "When block continue: block_id=%s progress=%d/%d errored=%d",
            self.step.id,
            terminal,
            total,
            len(errored),
        )

        if terminal == total:
            if errored:
                errors = [s.transition.error for s in errored if s.transition.error]
                msg = f"When block has {len(errored)} errored sub-block(s)"
                if errors:
                    msg += f": {errors[0]}"
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
        from ..types import deterministic_step_id

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
                step_uuid=deterministic_step_id(
                    self.step.workflow_id,
                    self.step.id,
                    stmt.id,
                    self.step.container_id,
                ),
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

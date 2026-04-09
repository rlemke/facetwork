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

"""Catch block execution handlers.

Handles the CatchBegin/Continue/End states for error recovery.
When a step errors and has a catch clause, execution enters CATCH_BEGIN
instead of STATEMENT_ERROR, allowing recovery.
"""

import logging
from typing import TYPE_CHECKING

logger = logging.getLogger(__name__)

from ..changers.base import StateChangeResult
from .base import StateHandler

if TYPE_CHECKING:
    pass


class CatchBeginHandler(StateHandler):
    """Handler for state.statement.catch.Begin.

    Initializes catch block execution:
    - Stores error info as pseudo-returns (s.error, s.error_type)
    - Creates catch sub-block(s) from the catch clause AST
    """

    def process_state(self) -> StateChangeResult:
        """Begin catch execution."""

        # Get the catch clause AST
        catch_ast = self.context._find_statement_catch(self.step)
        if catch_ast is None:
            # No catch clause — shouldn't happen, but complete normally
            self.step.request_state_change(True)
            return StateChangeResult(step=self.step)

        # Store error info as pseudo-returns for s.error / s.error_type access
        error = self.step.transition.error
        error_msg = str(error) if error else "Unknown error"
        error_type = type(error).__name__ if error else "RuntimeError"
        self.step.set_attribute("error", error_msg, is_return=True)
        self.step.set_attribute("error_type", error_type, is_return=True)

        if "when" in catch_ast:
            # Conditional catch — evaluate conditions using same pattern as when blocks
            return self._process_catch_when(catch_ast["when"])
        else:
            # Simple catch — create single sub-block
            return self._process_catch_simple(catch_ast)

    def _process_catch_simple(self, catch_ast: dict) -> StateChangeResult:
        """Create a single catch sub-block."""
        from ..step import StepDefinition
        from ..types import ObjectType

        statement_id = "catch-block-0"

        # Idempotency check
        if self.context.persistence.block_step_exists(statement_id, self.step.id):
            self.step.request_state_change(True)
            return StateChangeResult(step=self.step)

        already_pending = any(
            str(p.statement_id) == statement_id and p.container_id == self.step.id
            for p in self.context.changes.created_steps
        )
        if already_pending:
            self.step.request_state_change(True)
            return StateChangeResult(step=self.step)

        # Build body AST from catch clause
        body_ast: dict = {"type": "AndThenBlock"}
        if "steps" in catch_ast:
            body_ast["steps"] = catch_ast["steps"]
        if "yield" in catch_ast:
            body_ast["yield"] = catch_ast["yield"]
        if "yields" in catch_ast:
            body_ast["yields"] = catch_ast["yields"]

        block_step = StepDefinition.create(
            workflow_id=self.step.workflow_id,
            object_type=ObjectType.AND_CATCH,
            facet_name="",
            statement_id=statement_id,
            container_id=self.step.id,
            container_type=self.step.object_type,
            root_id=self.step.root_id or self.step.id,
        )

        # Cache the body AST for this sub-block
        self.context.set_block_ast_cache(block_step.id, body_ast)
        self.context.changes.add_created_step(block_step)

        logger.debug(
            "Catch sub-block created: block_id=%s for step=%s",
            block_step.id,
            self.step.id,
        )

        self.step.request_state_change(True)
        return StateChangeResult(step=self.step)

    def _process_catch_when(self, when_ast: dict) -> StateChangeResult:
        """Process catch when block — evaluate conditions and create sub-blocks."""
        from ..expression import EvaluationContext, ExpressionEvaluator
        from ..step import StepDefinition
        from ..types import ObjectType

        cases = when_ast.get("cases", [])

        # Build evaluation context
        workflow_root = self.context.get_workflow_root()
        inputs = {}
        if workflow_root:
            for name, attr in workflow_root.attributes.params.items():
                inputs[name] = attr.value

        # Add error info to inputs for $.error / $.error_type access
        error = self.step.transition.error
        inputs["error"] = str(error) if error else "Unknown error"
        inputs["error_type"] = type(error).__name__ if error else "RuntimeError"

        def get_step_output(step_name: str, attr_name: str):
            # Allow accessing the errored step's attributes
            if step_name == str(self.step.statement_name or self.step.statement_id):
                ret = self.step.attributes.returns.get(attr_name)
                if ret:
                    return ret.value
            raise ValueError(f"Not found: {step_name}.{attr_name}")

        eval_ctx = EvaluationContext(
            inputs=inputs,
            get_step_output=get_step_output,
            step_id=self.step.id,
        )
        evaluator = ExpressionEvaluator()

        any_matched = False
        for i, case in enumerate(cases):
            is_default = case.get("default", False)

            if is_default:
                if any_matched:
                    continue
            else:
                condition = case.get("condition")
                if condition is None:
                    continue
                try:
                    result = evaluator.evaluate(condition, eval_ctx)
                    if not result:
                        continue
                except Exception as e:
                    logger.warning("Catch when case %d condition evaluation failed: %s", i, e)
                    continue
                any_matched = True

            # Create sub-block for this case
            catch_stmt_id = f"catch-case-{i}"

            if self.context.persistence.step_exists(catch_stmt_id, self.step.id):
                continue

            already_pending = any(
                str(p.statement_id) == catch_stmt_id and p.block_id == self.step.id
                for p in self.context.changes.created_steps
            )
            if already_pending:
                continue

            case_body: dict = {"type": "AndThenBlock"}
            if "steps" in case:
                case_body["steps"] = case["steps"]
            if "yield" in case:
                case_body["yield"] = case["yield"]
            if "yields" in case:
                case_body["yields"] = case["yields"]

            sub_block = StepDefinition.create(
                workflow_id=self.step.workflow_id,
                object_type=ObjectType.AND_CATCH,
                facet_name="",
                statement_id=catch_stmt_id,
                container_id=self.step.container_id,
                block_id=self.step.id,
                root_id=self.step.root_id or self.step.container_id,
            )

            self.context.set_block_ast_cache(sub_block.id, case_body)
            self.context.changes.add_created_step(sub_block)

            logger.debug(
                "Catch when sub-block created: block_id=%s case=%d default=%s",
                sub_block.id,
                i,
                is_default,
            )

        self.step.request_state_change(True)
        return StateChangeResult(step=self.step)


class CatchContinueHandler(StateHandler):
    """Handler for state.statement.catch.Continue.

    Polls catch sub-blocks until all are complete.
    """

    def process_state(self) -> StateChangeResult:
        """Continue catch execution."""
        # Get all catch sub-blocks (container_id=self.step.id for simple,
        # block_id=self.step.id for when cases)
        blocks = list(self.context.persistence.get_blocks_by_step(self.step.id))

        # Also check for newly created blocks in current iteration
        for pending_step in self.context.changes.created_steps:
            if pending_step.container_id == self.step.id and pending_step.is_block:
                if pending_step not in blocks:
                    blocks.append(pending_step)

        # Also check blocks by block_id (for catch when sub-blocks)
        sub_blocks = list(self.context.persistence.get_steps_by_block(self.step.id))
        for pending_step in self.context.changes.created_steps:
            if pending_step.block_id == self.step.id and pending_step not in sub_blocks:
                sub_blocks.append(pending_step)

        all_blocks = blocks + [s for s in sub_blocks if s not in blocks]

        if not all_blocks:
            # No catch blocks to wait for
            self.step.request_state_change(True)
            return StateChangeResult(step=self.step)

        completed = [s for s in all_blocks if s.is_complete]
        errored = [s for s in all_blocks if s.is_error]
        terminal = len(completed) + len(errored)
        total = len(all_blocks)

        logger.debug(
            "Catch continue: step_id=%s progress=%d/%d errored=%d",
            self.step.id,
            terminal,
            total,
            len(errored),
        )

        if terminal == total:
            if errored:
                # Catch itself failed — propagate error
                msg = f"Catch block has {len(errored)} errored sub-block(s)"
                self.step.mark_error(RuntimeError(msg))
                return StateChangeResult(step=self.step)
            # All catch blocks complete — transition to CATCH_END
            self.step.request_state_change(True)
            return StateChangeResult(step=self.step)

        return self.stay(push=True)


class CatchEndHandler(StateHandler):
    """Handler for state.statement.catch.End.

    Pass-through: transitions to STATEMENT_CAPTURE_BEGIN to resume normal flow.
    """

    def process_state(self) -> StateChangeResult:
        """End catch execution."""
        self.step.request_state_change(True)
        return StateChangeResult(step=self.step)

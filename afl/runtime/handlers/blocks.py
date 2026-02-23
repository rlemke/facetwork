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

"""Block execution phase handlers.

Handles mixin blocks and statement blocks phases.
These manage the creation and monitoring of andThen blocks.
"""

from typing import TYPE_CHECKING

from ..block import BlockAnalysis
from ..changers.base import StateChangeResult
from .base import StateHandler

if TYPE_CHECKING:
    pass


class MixinBlocksBeginHandler(StateHandler):
    """Handler for state.mixin.blocks.Begin.

    Creates block steps for mixin (facet-level) blocks.
    """

    def process_state(self) -> StateChangeResult:
        """Begin mixin blocks execution."""
        # Get mixin blocks for this step's facet
        # In the current implementation, we don't have mixin blocks yet
        # This would create BlockStep instances for each mixin's andThen blocks

        self.step.request_state_change(True)
        return StateChangeResult(step=self.step)


class MixinBlocksContinueHandler(StateHandler):
    """Handler for state.mixin.blocks.Continue.

    Polls until all mixin blocks are complete.
    """

    def process_state(self) -> StateChangeResult:
        """Continue mixin blocks execution."""
        # Load block analysis for mixin blocks
        blocks = self.context.persistence.get_blocks_by_step(self.step.id)
        mixin_blocks = [b for b in blocks if b.container_type == "Facet"]

        if not mixin_blocks:
            # No mixin blocks to wait for
            self.step.request_state_change(True)
            return StateChangeResult(step=self.step)

        analysis = BlockAnalysis.load(self.step, mixin_blocks, mixins=True)

        if analysis.done:
            if analysis.has_errors:
                msg = f"{len(analysis.errored)} mixin block(s) errored"
                self.step.mark_error(RuntimeError(msg))
                return StateChangeResult(step=self.step)
            self.step.request_state_change(True)
            return StateChangeResult(step=self.step)
        else:
            # Still waiting, push for retry
            return self.stay(push=True)


class MixinBlocksEndHandler(StateHandler):
    """Handler for state.mixin.blocks.End.

    Completes mixin blocks phase.
    """

    def process_state(self) -> StateChangeResult:
        """End mixin blocks execution."""
        self.step.request_state_change(True)
        return StateChangeResult(step=self.step)


class StatementBlocksBeginHandler(StateHandler):
    """Handler for state.statement.blocks.Begin.

    Creates block steps for statement-level andThen blocks.
    """

    def process_state(self) -> StateChangeResult:
        """Begin statement blocks execution.

        Checks three sources for an andThen body:
        1. Workflow root → workflow_ast body
        2. Statement-level inline body → step's statement has a body key
        3. Facet-level body → facet definition has a body key
        """
        body = self._get_step_body()
        if body:
            self._create_block_steps(body)

        self.step.request_state_change(True)
        return StateChangeResult(step=self.step)

    def _get_step_body(self):
        """Get the andThen body for this step, if any.

        Returns:
            The body dict, or None
        """
        # 1. Workflow root step
        if self.step.container_id is None:
            workflow_ast = self.context.get_workflow_ast()
            if workflow_ast:
                return workflow_ast.get("body")
            return None

        # 2. Statement-level inline body
        inline_body = self.context._find_statement_body(self.step)
        if inline_body:
            return inline_body

        # 3. Facet-level body
        if self.step.facet_name:
            facet_def = self.context.get_facet_definition(self.step.facet_name)
            if facet_def and "body" in facet_def:
                return facet_def["body"]

        return None

    def _create_block_steps(self, body) -> None:
        """Create block steps for andThen blocks in body."""
        from ..step import StepDefinition
        from ..types import ObjectType

        # The body could be a single andThen block or a list of blocks
        bodies = body if isinstance(body, list) else [body]
        for i, _block_body in enumerate(bodies):
            statement_id = f"block-{i}"

            # Idempotency: skip if block step already exists in DB
            if self.context.persistence.block_step_exists(statement_id, self.step.id):
                continue

            # Also check pending creates in current iteration
            already_pending = any(
                str(p.statement_id) == statement_id and p.container_id == self.step.id
                for p in self.context.changes.created_steps
            )
            if already_pending:
                continue

            block_step = StepDefinition.create(
                workflow_id=self.step.workflow_id,
                object_type=ObjectType.AND_THEN,
                facet_name="",
                statement_id=statement_id,
                container_id=self.step.id,
                container_type=self.step.object_type,
                root_id=self.step.root_id or self.step.id,
            )

            # Add to pending changes
            self.context.changes.add_created_step(block_step)


class StatementBlocksContinueHandler(StateHandler):
    """Handler for state.statement.blocks.Continue.

    Polls until all statement blocks are complete.
    """

    def process_state(self) -> StateChangeResult:
        """Continue statement blocks execution."""
        # Load all blocks for this step
        blocks = list(self.context.persistence.get_blocks_by_step(self.step.id))

        # Also check for newly created blocks in current iteration
        for pending_step in self.context.changes.created_steps:
            if pending_step.container_id == self.step.id and pending_step.is_block:
                if pending_step not in blocks:
                    blocks.append(pending_step)

        if not blocks:
            # No blocks to wait for
            self.step.request_state_change(True)
            return StateChangeResult(step=self.step)

        analysis = BlockAnalysis.load(self.step, blocks, mixins=False)

        if analysis.done:
            if analysis.has_errors:
                msg = f"{len(analysis.errored)} block(s) errored"
                self.step.mark_error(RuntimeError(msg))
                return StateChangeResult(step=self.step)
            self.step.request_state_change(True)
            return StateChangeResult(step=self.step)
        else:
            # Still waiting, push for retry
            return self.stay(push=True)


class StatementBlocksEndHandler(StateHandler):
    """Handler for state.statement.blocks.End.

    Completes statement blocks phase.
    """

    def process_state(self) -> StateChangeResult:
        """End statement blocks execution."""
        self.step.request_state_change(True)
        return StateChangeResult(step=self.step)

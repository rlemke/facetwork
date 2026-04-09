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

"""AFL block execution analysis.

StepAnalysis tracks the execution state of steps within a block.
Used by BlockExecutionContinue to determine when a block is complete.
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

from .states import StepState
from .step import StepDefinition


@dataclass
class StatementDefinition:
    """Definition of a statement from compiled AST.

    Represents the static structure of a statement before
    it becomes a runtime step.
    """

    id: str  # Statement ID from AST
    name: str  # Step name
    object_type: str
    facet_name: str
    dependencies: set[str] = field(default_factory=set)  # Statement IDs this depends on
    args: list[dict] = field(default_factory=list)  # Named arguments
    mixins: list[dict] = field(default_factory=list)  # Call-site mixin calls
    is_yield: bool = False


@dataclass
class StepAnalysis:
    """Analysis of step execution state within a block.

    Tracks which statements have been created as steps, which are complete,
    and which are ready to be created based on dependency satisfaction.
    """

    block: StepDefinition  # The block step being analyzed
    statements: Sequence[StatementDefinition]  # All statements in the block

    # Step collections
    missing: list[StatementDefinition] = field(default_factory=list)  # Not yet created
    steps: list[StepDefinition] = field(default_factory=list)  # All created steps
    completed: list[StepDefinition] = field(default_factory=list)  # Complete steps
    errored: list[StepDefinition] = field(default_factory=list)  # Error steps
    requesting_push: list[StepDefinition] = field(default_factory=list)  # Need re-queue
    requesting_transition: list[StepDefinition] = field(default_factory=list)  # Need state change
    pending_event: list[StepDefinition] = field(default_factory=list)  # Waiting on events
    pending_mixin: list[StepDefinition] = field(default_factory=list)  # In mixin blocks
    pending_blocks: list[StepDefinition] = field(default_factory=list)  # In block execution

    done: bool = False  # True when all statements are terminal (complete or error)
    has_errors: bool = False  # True when any statement has errored

    @classmethod
    def load(
        cls,
        block: StepDefinition,
        statements: Sequence[StatementDefinition],
        steps: Sequence[StepDefinition],
    ) -> "StepAnalysis":
        """Load analysis from persisted steps.

        Args:
            block: The block step being analyzed
            statements: All statement definitions in the block
            steps: All persisted steps in the block

        Returns:
            StepAnalysis with categorized steps
        """
        analysis = cls(block=block, statements=statements)

        # Map statement IDs to steps
        stmt_to_step: dict[str, StepDefinition] = {}
        for step in steps:
            if step.statement_id:
                stmt_to_step[str(step.statement_id)] = step

        # Categorize each statement/step
        for stmt in statements:
            matched_step = stmt_to_step.get(stmt.id)
            if matched_step is None:
                analysis.missing.append(stmt)
            else:
                analysis.steps.append(matched_step)
                analysis._categorize_step(matched_step)

        # Done when all statements have terminal steps (complete or error)
        terminal_count = len(analysis.completed) + len(analysis.errored)
        analysis.done = len(analysis.missing) == 0 and terminal_count == len(analysis.statements)
        analysis.has_errors = len(analysis.errored) > 0

        logger.debug(
            "StepAnalysis loaded: block_id=%s total=%d missing=%d completed=%d errored=%d pending_event=%d pending_blocks=%d",
            block.id,
            len(statements),
            len(analysis.missing),
            len(analysis.completed),
            len(analysis.errored),
            len(analysis.pending_event),
            len(analysis.pending_blocks),
        )

        return analysis

    def _categorize_step(self, step: StepDefinition) -> None:
        """Categorize a step by its current state."""
        if step.is_complete:
            self.completed.append(step)
        elif step.is_error:
            self.errored.append(step)
        elif step.transition.is_requesting_push:
            self.requesting_push.append(step)
        elif step.transition.is_requesting_state_change:
            self.requesting_transition.append(step)
        elif step.state == StepState.EVENT_TRANSMIT:
            self.pending_event.append(step)
        elif step.state == StepState.MIXIN_BLOCKS_CONTINUE:
            self.pending_mixin.append(step)
        elif step.state in (
            StepState.BLOCK_EXECUTION_CONTINUE,
            StepState.STATEMENT_BLOCKS_CONTINUE,
        ):
            self.pending_blocks.append(step)

    def can_be_created(self) -> Sequence[StatementDefinition]:
        """Get statements that can have steps created.

        A statement can be created if all its dependencies are terminal
        (complete or error).

        Returns:
            Statements ready for step creation
        """
        terminal_ids = {str(s.statement_id) for s in self.completed if s.statement_id}
        terminal_ids |= {str(s.statement_id) for s in self.errored if s.statement_id}

        ready = []
        for stmt in self.missing:
            if stmt.dependencies.issubset(terminal_ids):
                ready.append(stmt)

        return ready

    def is_blocked(self) -> bool:
        """Check if execution is blocked waiting on dependencies."""
        return (
            len(self.missing) > 0
            and len(self.can_be_created()) == 0
            and len(self.requesting_transition) == 0
            and len(self.requesting_push) == 0
        )

    def has_pending_work(self) -> bool:
        """Check if there is pending work to do."""
        return (
            len(self.requesting_transition) > 0
            or len(self.requesting_push) > 0
            or len(self.can_be_created()) > 0
        )

    @property
    def completion_progress(self) -> tuple[int, int]:
        """Get completion progress as (completed, total)."""
        return len(self.completed) + len(self.errored), len(self.statements)


@dataclass
class BlockAnalysis:
    """Analysis of all blocks for a step.

    Used by MixinBlocksContinue and StatementBlocksContinue
    to track block completion.
    """

    step: StepDefinition  # The containing step
    blocks: list[StepDefinition]  # All block steps

    completed: list[StepDefinition] = field(default_factory=list)
    errored: list[StepDefinition] = field(default_factory=list)
    pending: list[StepDefinition] = field(default_factory=list)

    done: bool = False
    has_errors: bool = False

    @classmethod
    def load(
        cls,
        step: StepDefinition,
        blocks: Sequence[StepDefinition],
        mixins: bool = False,
    ) -> "BlockAnalysis":
        """Load analysis from persisted blocks.

        Args:
            step: The containing step
            blocks: All block steps for this step
            mixins: If True, filter to mixin blocks only

        Returns:
            BlockAnalysis with categorized blocks
        """
        analysis = cls(step=step, blocks=list(blocks))

        for block in blocks:
            if block.is_complete:
                analysis.completed.append(block)
            elif block.is_error:
                analysis.errored.append(block)
            else:
                analysis.pending.append(block)

        analysis.done = len(analysis.pending) == 0
        analysis.has_errors = len(analysis.errored) > 0

        logger.debug(
            "BlockAnalysis loaded: step_id=%s total=%d completed=%d errored=%d pending=%d",
            step.id,
            len(list(blocks)),
            len(analysis.completed),
            len(analysis.errored),
            len(analysis.pending),
        )

        return analysis

    @property
    def completion_progress(self) -> tuple[int, int]:
        """Get completion progress as (completed, total)."""
        return len(self.completed) + len(self.errored), len(self.blocks)

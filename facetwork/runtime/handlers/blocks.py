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

    Creates one sub-step per aliased mixin declared on this step's
    facet signature.  The sub-step is born in ``CREATED`` state and
    walks the full ``STEP_TRANSITIONS`` lifecycle: facet init (with
    pre-bound params), mixin blocks (typically a no-op since mixin
    facets rarely nest mixins), the mixin facet's own body, capture,
    and finally ``STATEMENT_COMPLETE``.  Parent waits on these at
    ``MixinBlocksContinueHandler``.

    Sub-step params are seeded from the parent's already-evaluated
    ``attributes.params[alias]`` (a nested dict produced by
    ``FacetInitializationBeginHandler`` when it evaluated the mixin's
    sig-args in the parent's scope).  ``FacetInitializationBeginHandler``
    on the sub-step then detects the pre-populated params and skips
    its own arg-evaluation pass.

    Un-aliased mixins are intentionally skipped here — they remain
    purely config (their sig-args flat-merge into parent.params via
    the v0.21.0 contract) and never execute their bodies.  An
    un-aliased mixin has no consumer-visible identity, so spinning up
    a sub-step whose results nobody can read would be wasteful.
    """

    def process_state(self) -> StateChangeResult:
        """Begin mixin blocks execution."""
        from ..step import StepDefinition
        from ..types import AttributeValue, FacetAttributes, ObjectType

        facet_def = (
            self.context.get_facet_definition(self.step.facet_name)
            if self.step.facet_name
            else None
        )
        if facet_def:
            existing_aliases = {
                child.statement_name
                for child in self.context.persistence.get_steps_by_container(self.step.id)
                if child.statement_name
            }
            existing_aliases |= {
                p.statement_name
                for p in self.context.changes.created_steps
                if p.container_id == self.step.id and p.statement_name
            }
            for mixin in facet_def.get("mixins", []) or []:
                alias = mixin.get("alias")
                target = mixin.get("target") or ""
                if not alias or not target:
                    continue
                if alias in existing_aliases:
                    continue

                # Pull the mixin's already-evaluated sig-args off the
                # parent.  FACET_INIT_BEGIN put them under params[alias]
                # as a nested dict (v0.21.0 contract) — those values
                # become the sub-step's bound params, one entry per
                # sig-arg.  Empty dict if the parent didn't bind any.
                parent_bag = self.step.attributes.params.get(alias)
                seed_params: dict[str, AttributeValue] = {}
                if parent_bag is not None and isinstance(parent_bag.value, dict):
                    for k, v in parent_bag.value.items():
                        seed_params[k] = AttributeValue(k, v)

                sub_step = StepDefinition.create(
                    workflow_id=self.step.workflow_id,
                    object_type=ObjectType.VARIABLE_ASSIGNMENT,
                    facet_name=target,
                    statement_name=alias,
                    container_id=self.step.id,
                    container_type=self.step.object_type,
                    root_id=self.step.root_id or self.step.id,
                )
                if seed_params:
                    sub_step.attributes = FacetAttributes(params=seed_params)
                # State stays at the StepDefinition.create default
                # (``CREATED``) so the sub-step picks up at the next
                # iteration and walks its full lifecycle.
                self.context.changes.add_created_step(sub_step)
                existing_aliases.add(alias)

        self.step.request_state_change(True)
        return StateChangeResult(step=self.step)


class MixinBlocksContinueHandler(StateHandler):
    """Handler for state.mixin.blocks.Continue.

    Waits for every aliased mixin sub-step under this parent to reach
    a terminal state.  Sub-steps are children of the parent whose
    ``statement_name`` is one of the aliases declared on the parent's
    facet signature.  Un-aliased mixins are pure config (no sub-step
    created by ``MixinBlocksBeginHandler``) and are not waited on.

    Error coupling is strict: if any mixin sub-step errored, the
    parent step also errors with the first sub-step's error message.
    """

    def process_state(self) -> StateChangeResult:
        """Continue mixin blocks execution."""
        aliases = self._declared_aliases()
        if not aliases:
            # Facet has no aliased mixins — nothing to wait for.
            self.step.request_state_change(True)
            return StateChangeResult(step=self.step)

        sub_steps = self._mixin_sub_steps(aliases)
        if not sub_steps:
            # MIXIN_BLOCKS_BEGIN didn't run yet, or sub-step rows are
            # still pending the next iteration's commit — defer.
            return self.stay(push=True)

        errored = [s for s in sub_steps if s.is_error]
        if errored:
            err = errored[0].transition.error
            msg = f"{len(errored)} mixin sub-step(s) errored" + (f": {err}" if err else "")
            self.step.mark_error(RuntimeError(msg))
            return StateChangeResult(step=self.step)

        not_done = [s for s in sub_steps if not s.is_terminal]
        if not_done:
            return self.stay(push=True)

        # All mixin sub-steps reached STATEMENT_COMPLETE.
        self.step.request_state_change(True)
        return StateChangeResult(step=self.step)

    def _declared_aliases(self) -> set[str]:
        if not self.step.facet_name:
            return set()
        facet_def = self.context.get_facet_definition(self.step.facet_name)
        if not facet_def:
            return set()
        return {m.get("alias") for m in (facet_def.get("mixins", []) or []) if m.get("alias")}

    def _mixin_sub_steps(self, aliases: set[str]) -> list:
        """Return persisted+pending sub-steps under this parent whose
        ``statement_name`` is one of the declared aliases."""
        seen_ids: set[str] = set()
        result = []
        for child in self.context.persistence.get_steps_by_container(self.step.id):
            if child.statement_name in aliases and child.id not in seen_ids:
                seen_ids.add(child.id)
                result.append(child)
        # Pending creates from this iteration aren't yet in persistence.
        for pending in self.context.changes.created_steps:
            if (
                pending.container_id == self.step.id
                and pending.statement_name in aliases
                and pending.id not in seen_ids
            ):
                seen_ids.add(pending.id)
                result.append(pending)
        # Pending updates supersede prior persisted copies.
        for pending in self.context.changes.updated_steps:
            if pending.container_id == self.step.id and pending.statement_name in aliases:
                for i, s in enumerate(result):
                    if s.id == pending.id:
                        result[i] = pending
                        break
        return result


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
        from ..types import ObjectType, deterministic_step_id

        # The body could be a single andThen block or a list of blocks
        bodies = body if isinstance(body, list) else [body]
        for i, block_body in enumerate(bodies):
            statement_id = f"block-{i}"

            # Determine block type: when blocks use AND_WHEN
            block_type = ObjectType.AND_THEN
            if isinstance(block_body, dict) and "when" in block_body:
                block_type = ObjectType.AND_WHEN

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
                object_type=block_type,
                facet_name="",
                statement_id=statement_id,
                container_id=self.step.id,
                container_type=self.step.object_type,
                root_id=self.step.root_id or self.step.id,
                step_uuid=deterministic_step_id(
                    self.step.workflow_id,
                    None,
                    statement_id,
                    self.step.id,
                ),
            )

            # Add to pending changes
            self.context.changes.add_created_step(block_step)


class StatementBlocksContinueHandler(StateHandler):
    """Handler for state.statement.blocks.Continue.

    Polls until all statement blocks are complete.
    """

    def process_state(self) -> StateChangeResult:
        """Continue statement blocks execution."""
        from ..states import StepState

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
                # Check for catch clause before erroring
                catch_ast = self.context._find_statement_catch(self.step)
                if catch_ast:
                    errors = [b.transition.error for b in analysis.errored if b.transition.error]
                    error = (
                        errors[0]
                        if errors
                        else RuntimeError(f"{len(analysis.errored)} block(s) errored")
                    )
                    self.step.transition.error = error
                    self.step.change_state(StepState.CATCH_BEGIN)
                    # Do NOT request a state change: the changer loop advances
                    # BEFORE executing, so a pending request would skip
                    # CatchBeginHandler (which creates the catch sub-block) and
                    # land in CATCH_CONTINUE with only the errored body block
                    # in sight. Stay + push so CATCH_BEGIN executes next.
                    return self.stay(push=True)
                errors = [b.transition.error for b in analysis.errored if b.transition.error]
                msg = f"{len(analysis.errored)} block(s) errored"
                if errors:
                    msg += f": {errors[0]}"
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

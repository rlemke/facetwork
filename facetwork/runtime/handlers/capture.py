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

"""Capture phase handlers.

Handles yield/capture merging from blocks into containing step.
"""

from typing import TYPE_CHECKING

from ..changers.base import StateChangeResult
from .base import StateHandler

if TYPE_CHECKING:
    from ..step import StepDefinition


def _names_match(a: str | None, b: str | None) -> bool:
    """Check if two facet names match, handling qualified vs short names."""
    if not a or not b:
        return a == b
    if a == b:
        return True
    return a.endswith("." + b) or b.endswith("." + a)


def _merge_yield_value(existing: object, new: object) -> object:
    """Combine a prior yield value with a fresh one.

    Yield merge semantics are type-driven:
      - ``list``   → concat (``existing + new``). Order is yield order; for
        ``andThen foreach`` blocks where sub-blocks complete in parallel,
        that is dispatch-completion order, not iteration order.
      - ``set`` / ``frozenset`` → union (``existing | new``).
      - everything else (scalars, dicts, schema instances) → ``new``
        overwrites ``existing``. This preserves the historical contract
        for scalar yields (``yield F(count = $.x)`` reports the last
        iteration's value, dispatch-order-dependent) and for schema
        results (a yield of a single ``OSMCache`` replaces the prior one
        rather than smashing fields together).

    A ``foreach`` body that wants its outputs collected should yield a
    *list* containing the new element — ``yield F(items = [item])`` —
    so each iteration contributes one entry to the aggregate.

    Args:
        existing: The current return value already on the parent step.
        new: The value carried by the yield being merged.

    Returns:
        The combined value to store on the parent step.
    """
    if isinstance(existing, list) and isinstance(new, list):
        return existing + new
    if isinstance(existing, set) and isinstance(new, set):
        return existing | new
    if isinstance(existing, frozenset) and isinstance(new, frozenset):
        return existing | new
    return new


class MixinCaptureBeginHandler(StateHandler):
    """Handler for state.mixin.capture.Begin.

    Snapshots each aliased mixin sub-step's full attributes
    (params + returns, with returns shadowing params on collision)
    into ``parent.attributes.params[alias]`` as a single dict.  This
    replaces the v0.21.0 sig-args nested dict that
    ``FacetInitializationBeginHandler`` wrote there at parent init —
    after Scope B the mixin sub-step has actually executed, so the
    parent's "view" of the alias gets refreshed with the sub-step's
    computed returns.

    The parent's andThen body then reads ``$.<alias>.<field>`` against
    this merged dict.  Mixin sub-steps are the live source of truth
    for FacetRef consumers (``$.<fref>.<alias>.<field>`` still goes
    through the persistence resolver); this snapshot exists purely as
    a parent-internal convenience for in-handler / in-andThen reads.
    """

    def process_state(self) -> StateChangeResult:
        """Snapshot each mixin sub-step's attributes onto the parent."""
        aliases = self._declared_aliases()
        if not aliases:
            self.step.request_state_change(True)
            return StateChangeResult(step=self.step)

        sub_step_by_alias = self._sub_steps_by_alias(aliases)
        for alias, sub_step in sub_step_by_alias.items():
            snapshot: dict = {}
            for name, attr in sub_step.attributes.params.items():
                snapshot[name] = attr.value
            for name, attr in sub_step.attributes.returns.items():
                snapshot[name] = attr.value
            self.step.attributes.set_param(alias, snapshot)

        self.step.request_state_change(True)
        return StateChangeResult(step=self.step)

    def _declared_aliases(self) -> set[str]:
        if not self.step.facet_name:
            return set()
        facet_def = self.context.get_facet_definition(self.step.facet_name)
        if not facet_def:
            return set()
        return {m.get("alias") for m in (facet_def.get("mixins", []) or []) if m.get("alias")}

    def _sub_steps_by_alias(self, aliases: set[str]) -> dict:
        """Return ``{alias: sub_step}`` for the parent's mixin sub-steps,
        accounting for pending creates/updates so we see the freshest
        copy this iteration."""
        result: dict = {}
        for child in self.context.persistence.get_steps_by_container(self.step.id):
            if child.statement_name in aliases:
                result[child.statement_name] = child
        for pending in self.context.changes.created_steps:
            if pending.container_id == self.step.id and pending.statement_name in aliases:
                result[pending.statement_name] = pending
        for pending in self.context.changes.updated_steps:
            if pending.container_id == self.step.id and pending.statement_name in aliases:
                result[pending.statement_name] = pending
        return result


class MixinCaptureEndHandler(StateHandler):
    """Handler for state.mixin.capture.End.

    Completes mixin capture phase.
    """

    def process_state(self) -> StateChangeResult:
        """End mixin capture."""
        self.step.request_state_change(True)
        return StateChangeResult(step=self.step)


class StatementCaptureBeginHandler(StateHandler):
    """Handler for state.statement.capture.Begin.

    Merges yield results from statement blocks (andThen) into either
    the parent step's returns (the typical case) or into an aliased
    mixin sub-step's returns when the yield targets the mixin by
    alias name (or by the mixin facet's name when that target is
    used by exactly one alias on the parent facet).

    Routing rules per yield (target = ``yield <target>(...)``):

    1. ``target`` matches the parent's facet name → merge into the
       parent step's returns (this is the existing primary case).
    2. ``target`` matches a declared mixin alias on the parent's facet
       → merge into the matching mixin sub-step's returns.  The
       sub-step was created in ``STATEMENT_COMPLETE`` by
       ``MixinBlocksBeginHandler``; we update it in place via
       ``add_updated_step`` so its returns are visible to FacetRef
       consumers reading ``$.<fref>.<alias>.<field>``.
    3. ``target`` is the **facet name** of a mixin used **exactly once**
       on the parent and that one usage has an alias → route to that
       sub-step.  Preserves the historical ``yield F(...) with M(out=...)``
       pattern (which the parser splits into two YieldStmts, one per
       target) when there is no ambiguity.  When two or more aliases
       point at the same target, the validator's ``YIELD_TARGET_AMBIGUOUS``
       rule forces the author to use the alias name explicitly, so this
       branch never has to disambiguate at runtime.
    4. Otherwise → ignored at this capture scope (inner-facet-body
       yields handled by their own capture).
    """

    def process_state(self) -> StateChangeResult:
        """Begin statement capture."""
        # Get completed statement blocks
        blocks = self.context.persistence.get_blocks_by_step(self.step.id)
        statement_blocks = [b for b in blocks if b.is_complete]

        # Also check pending changes for blocks that just completed
        for pending_step in self.context.changes.updated_steps:
            if (
                pending_step.container_id == self.step.id
                and pending_step.is_block
                and pending_step.is_complete
                and pending_step not in statement_blocks
            ):
                statement_blocks.append(pending_step)

        # Per-process routing scratch state.  ``_seen_yield_ids`` dedups
        # yields seen across foreach/when sub-blocks (a foreach yield's
        # step is reachable from both its direct foreach sub-block and
        # the parent's container walk).  ``_mixin_substep_working`` is a
        # working-copy map alias → mutable StepDefinition so multiple
        # yields targeting the same alias accumulate before we persist
        # the sub-step once via ``add_updated_step``.
        self._seen_yield_ids: set[str] = set()
        self._mixin_substep_working: dict[str, StepDefinition] = {}

        # Cache parent's mixin metadata once.  ``mixin_aliases`` maps
        # alias → target facet name; ``target_to_aliases`` is the inverse
        # used to resolve the unique-target back-compat case.  Computed
        # from the same facet def the validator reads, so any disambiguation
        # rule in the validator also applies at routing time.
        self._mixin_aliases: dict[str, str] = {}
        self._target_to_aliases: dict[str, list[str]] = {}
        if self.step.facet_name:
            facet_def = self.context.get_facet_definition(self.step.facet_name)
            if facet_def:
                for mixin in facet_def.get("mixins", []) or []:
                    target = mixin.get("target") or ""
                    alias = mixin.get("alias")
                    if alias:
                        self._mixin_aliases[alias] = target
                    if target:
                        self._target_to_aliases.setdefault(target, []).append(alias or "")

        for block in statement_blocks:
            self._merge_yields_from_block(block)

        self.step.request_state_change(True)
        return StateChangeResult(step=self.step)

    def _merge_yields_from_block(self, block: "StepDefinition") -> None:
        """Merge yield results from a block into the right destination.

        For andThen script blocks, the block step's own ``attributes.returns``
        are copied directly onto the parent (these are populated by
        ``ScriptExecutor`` in ``_execute_script_block`` and never need
        mixin routing).

        Recursively walks descendant blocks (andWhen cases, nested andThen
        blocks) to find every completed yield step.  Yields are then
        routed per the rules in this handler's class docstring.
        Deduplication uses ``self._seen_yield_ids``, seeded by
        ``process_state``.
        """

        # Check if block itself has returns (andThen script blocks)
        if block.attributes.returns:
            for name, attr in block.attributes.returns.items():
                self.step.attributes.set_return(name, attr.value, attr.type_hint)

        # Collect every completed yield under this block — filtering now
        # happens during routing instead of during collection.  Inner-facet-
        # body yields (whose target is some inner facet on a nested step's
        # own body) are dropped by the routing step because their target
        # name matches neither the parent nor any of the parent's mixins.
        yield_steps = self._collect_yields_recursive(block.id)

        seen = getattr(self, "_seen_yield_ids", None)
        for yield_step in yield_steps:
            if seen is not None:
                if yield_step.id in seen:
                    continue
                seen.add(yield_step.id)
            self._route_yield(yield_step)

    def _route_yield(self, yield_step: "StepDefinition") -> None:
        """Decide where a yield's params go and merge them there."""
        target = yield_step.facet_name or ""
        short = target.split(".")[-1]
        parent_short = self.step.facet_name.split(".")[-1] if self.step.facet_name else None

        # Case 1: yield to the parent facet itself.
        if parent_short is not None and _names_match(target, self.step.facet_name):
            self._merge_yield_into_parent(yield_step)
            return

        # Case 2: yield uses an alias name directly.
        if short in self._mixin_aliases:
            self._merge_yield_into_mixin_substep(yield_step, alias=short)
            return

        # Case 3: yield uses a mixin's target facet name and that target
        # has exactly one alias on the parent.  The validator's
        # YIELD_TARGET_AMBIGUOUS rule rejects multi-alias cases at compile
        # time, so we only need the unique-alias branch here.
        aliases = [a for a in self._target_to_aliases.get(short, []) if a]
        if len(aliases) == 1:
            self._merge_yield_into_mixin_substep(yield_step, alias=aliases[0])
            return

        # Otherwise: not for this capture scope (inner facet body, etc.).

    def _collect_yields_recursive(self, block_id) -> list["StepDefinition"]:
        """Recursively collect yield steps from a block and its descendants.

        Follows both block children (andWhen cases, nested andThen) and
        step_body blocks (andThen when/foreach attached to a step).
        Routing decides which collected yield belongs to which destination;
        unmatched yields are silently dropped, which is also how inner
        facet body yields (e.g. ``yield IntValueAdd(...)`` inside Adder's
        body) avoid leaking into an outer capture scope.

        Args:
            block_id: The block to search.
        """
        from ..types import ObjectType

        steps = list(self.context.persistence.get_steps_by_block(block_id))

        # Also check pending changes
        for pending_step in self.context.changes.created_steps:
            if pending_step.block_id == block_id and pending_step not in steps:
                steps.append(pending_step)
        for pending_step in self.context.changes.updated_steps:
            for i, s in enumerate(steps):
                if s.id == pending_step.id:
                    steps[i] = pending_step

        yields = [
            s for s in steps if s.object_type == ObjectType.YIELD_ASSIGNMENT and s.is_complete
        ]

        # Recurse into sub-blocks (andWhen cases, nested andThen blocks)
        for s in steps:
            if s.is_block and s.is_complete:
                yields.extend(self._collect_yields_recursive(s.id))

        # Also follow step_body blocks: for each non-block step, check if
        # it has block children (from andThen when/foreach step_body).
        for s in steps:
            if not s.is_block and s.is_complete:
                child_blocks = self.context.persistence.get_blocks_by_step(s.id)
                for cb in child_blocks:
                    if cb.is_complete:
                        yields.extend(self._collect_yields_recursive(cb.id))

        return yields

    def _merge_yield_into_parent(self, yield_step: "StepDefinition") -> None:
        """Merge a yield into the parent step's returns.

        Yield attributes become return values on the containing step.
        Collection-typed values (``list``, ``set``, ``frozenset``)
        combine with the prior return via concat/union so multiple
        yields — typically from ``andThen foreach`` sub-blocks —
        aggregate into one collection.  Other types (scalars, dicts,
        schema instances) overwrite — see ``_merge_yield_value``.

        This is what lets a foreach body collect its outputs:

            } andThen foreach r in batch.regions {
                c = Download(region = $.r)
                yield Workflow(caches = [c.cache])  // 1-element list per iter
            }                                       // parent caches: [N elements]
        """
        for name, attr in yield_step.attributes.params.items():
            existing = self.step.attributes.returns.get(name)
            merged_value = (
                _merge_yield_value(existing.value, attr.value)
                if existing is not None
                else attr.value
            )
            self.step.attributes.set_return(name, merged_value, attr.type_hint)

    def _merge_yield_into_mixin_substep(self, yield_step: "StepDefinition", alias: str) -> None:
        """Merge a yield into the aliased mixin sub-step's returns.

        Looks up (or creates a working copy of) the persisted sub-step
        under the parent, merges the yield's params into its
        ``attributes.returns`` using the same merge rules as parent-side
        capture, and registers it via ``add_updated_step`` so the iteration
        commits the change.  Multiple yields targeting the same alias
        accumulate in the working copy before a single update is committed.
        """
        sub_step = self._get_mixin_substep(alias)
        if sub_step is None:
            # Placeholder wasn't created for this alias (e.g. handler skipped
            # because the facet definition is missing).  Without a sub-step
            # to write to, the yield has no consumer-visible home — silently
            # drop, matching the pre-routing behavior for unknown targets.
            return
        for name, attr in yield_step.attributes.params.items():
            existing = sub_step.attributes.returns.get(name)
            merged_value = (
                _merge_yield_value(existing.value, attr.value)
                if existing is not None
                else attr.value
            )
            sub_step.attributes.set_return(name, merged_value, attr.type_hint)
        self.context.changes.add_updated_step(sub_step)

    def _get_mixin_substep(self, alias: str) -> "StepDefinition | None":
        """Return a mutable working copy of the aliased mixin sub-step.

        Search order:

        1. The handler's per-process working-copy cache, so multiple yields
           into the same alias share state.
        2. ``changes.created_steps`` (the placeholder this iteration's own
           ``MixinBlocksBeginHandler`` just wrote), since persistence may
           not yet have committed it.
        3. ``changes.updated_steps`` (an earlier routing call this iteration).
        4. Persistence — clone before mutating to avoid leaking into the
           store's internal cache.
        """
        working = self._mixin_substep_working.get(alias)
        if working is not None:
            return working

        for pending in self.context.changes.created_steps:
            if pending.container_id == self.step.id and pending.statement_name == alias:
                self._mixin_substep_working[alias] = pending
                return pending

        for pending in self.context.changes.updated_steps:
            if pending.container_id == self.step.id and pending.statement_name == alias:
                self._mixin_substep_working[alias] = pending
                return pending

        for child in self.context.persistence.get_steps_by_container(self.step.id):
            if child.statement_name == alias:
                cloned = child.clone()
                self._mixin_substep_working[alias] = cloned
                return cloned

        return None


class StatementCaptureEndHandler(StateHandler):
    """Handler for state.statement.capture.End.

    Completes statement capture phase.
    """

    def process_state(self) -> StateChangeResult:
        """End statement capture."""
        self.step.request_state_change(True)
        return StateChangeResult(step=self.step)

"""Mixin-alias resolution for FacetRef consumers.

A consumer that holds a FacetRef can address an aliased mixin's
attributes through ``$.fref.<alias>.<field>``.  The aliased mixin
lives as a real persisted sub-step under the parent (written by
``MixinBlocksBeginHandler`` during the parent's ``MIXIN_BLOCKS_BEGIN``
phase), and the parent's ``StatementCaptureBeginHandler`` routes
yields whose target is the alias (or the mixin's facet name, when
unambiguous) into the sub-step's ``attributes.returns``.  By the time
a FacetRef consumer evaluates ``$.fref.<alias>.<field>``, the parent
step is fully complete (dependency tracking enforces this), so the
sub-step's returns are populated.

This module used to *synthesize* a sub-step on demand by walking the
parent's persisted yields.  That synthesis was a workaround for the
placeholder shape, retired when yield routing went through the
``StatementCaptureBeginHandler``.  The resolver is now a direct
persistence lookup keyed by ``statement_name == alias``.
"""

from __future__ import annotations

from typing import Any

from .step import StepDefinition


def is_mixin_sub_step(step: StepDefinition | None) -> bool:
    """Return True iff ``step`` is an aliased mixin sub-step.

    Mixin sub-steps land in persistence with a distinct shape:
    ``container_id`` set (their parent step), ``block_id`` *unset*
    (they don't live inside any andThen block), and ``statement_name``
    set to the alias name (e.g. ``m1`` for ``with M() as m1``).  This
    is exactly what ``MixinBlocksBeginHandler`` writes when it creates
    a sub-step for each aliased mixin on a facet sig.

    Sibling body steps inside a parent's andThen always have
    ``block_id`` populated, so the ``not block_id`` check is what
    distinguishes a mixin sub-step from a regular body step.

    Callers (dashboard recovery endpoints, evaluator retry/rerun
    helpers, repair-workflow, MCP fw_retry_step) share this helper so
    every recovery path agrees on what a mixin sub-step looks like.
    """
    return bool(step and step.statement_name and step.container_id and not step.block_id)


def resolve_mixin_step_by_alias(
    parent_step_id: str,
    alias: str,
    persistence: Any,
    get_facet_definition: Any = None,
) -> StepDefinition | None:
    """Return the persisted aliased-mixin sub-step under ``parent_step_id``.

    Looks up every child step of the parent via
    ``persistence.get_steps_by_container`` and returns the one whose
    ``statement_name`` matches ``alias`` (the alias is written into
    ``statement_name`` by ``MixinBlocksBeginHandler`` when the
    placeholder is created).  Returns ``None`` if no such sub-step
    exists — which happens only if the parent's facet definition is
    missing/the parent never reached MIXIN_BLOCKS_BEGIN, since aliased
    mixins always get a placeholder there.

    Args:
        parent_step_id: The persisted step holding the parent facet.
        alias: The mixin alias the consumer is reaching for.
        persistence: PersistenceAPI providing ``get_steps_by_container``.
        get_facet_definition: Unused (kept for source-level
            backward-compatibility with callers that still pass it).
    """
    del get_facet_definition  # legacy positional arg
    for child in persistence.get_steps_by_container(parent_step_id):
        if child.statement_name == alias:
            return child
    return None

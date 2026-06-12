"""Repair-workflow recognises aliased mixin sub-steps.

The `_reset_failed_step_and_ancestors` helper used by every repair
branch resets a single errored step plus its ancestor chain.
Regular steps go to ``EVENT_TRANSMIT``; aliased mixin sub-steps
go to ``CREATED`` with cleared returns, and the parent container
resumes at ``MIXIN_BLOCKS_CONTINUE`` instead of
``STATEMENT_BLOCKS_CONTINUE`` so the parent re-waits on the mixin
instead of skipping the mixin phase.

This is the same mixin-aware semantic the dashboard's `/retry`
endpoint and the MCP `fw_retry_step` tool implement.
"""

from __future__ import annotations

import pytest

try:
    import mongomock

    MONGOMOCK_AVAILABLE = True
except ImportError:
    MONGOMOCK_AVAILABLE = False

needs_mongomock = pytest.mark.skipif(
    not MONGOMOCK_AVAILABLE, reason="mongomock not installed"
)


@pytest.fixture
def store():
    if not MONGOMOCK_AVAILABLE:
        pytest.skip("mongomock not installed")
    from facetwork.runtime.mongo_store import MongoStore

    s = MongoStore(database_name="afl_test_repair_mixin", client=mongomock.MongoClient())
    yield s
    s.drop_database()
    s.close()


@needs_mongomock
class TestRepairResetMixinSubStep:
    """Pin the mixin-aware reset behavior in
    ``_reset_failed_step_and_ancestors``."""

    def _make_parent_with_errored_mixin(self, store):
        from facetwork.runtime.states import StepState
        from facetwork.runtime.step import StepDefinition
        from facetwork.runtime.types import (
            AttributeValue,
            FacetAttributes,
            ObjectType,
        )

        parent = StepDefinition.create(
            workflow_id="wf-repair",
            object_type=ObjectType.VARIABLE_ASSIGNMENT,
            facet_name="Parent",
        )
        parent.state = StepState.STATEMENT_ERROR
        parent.transition.current_state = StepState.STATEMENT_ERROR
        store.save_step(parent)

        mixin_sub = StepDefinition.create(
            workflow_id="wf-repair",
            object_type=ObjectType.VARIABLE_ASSIGNMENT,
            facet_name="M",
            statement_name="m",
            container_id=parent.id,
        )
        mixin_sub.attributes = FacetAttributes(
            returns={"output": AttributeValue("output", "stale")}
        )
        mixin_sub.mark_error(RuntimeError("oops"))
        store.save_step(mixin_sub)
        return parent, mixin_sub

    def test_mixin_sub_step_resets_to_created_clears_returns(self, store):
        from facetwork.runtime.states import StepState

        parent, mixin_sub = self._make_parent_with_errored_mixin(store)
        step_by_id = {parent.id: parent, mixin_sub.id: mixin_sub}
        ancestors_reset: list[str] = []

        store._reset_failed_step_and_ancestors(
            mixin_sub, step_by_id, ancestors_reset
        )

        reloaded = store.get_step(mixin_sub.id)
        assert reloaded.state == StepState.CREATED
        assert reloaded.attributes.returns == {}

    def test_parent_container_resumes_at_mixin_blocks_continue(self, store):
        from facetwork.runtime.states import StepState

        parent, mixin_sub = self._make_parent_with_errored_mixin(store)
        step_by_id = {parent.id: parent, mixin_sub.id: mixin_sub}
        ancestors_reset: list[str] = []

        store._reset_failed_step_and_ancestors(
            mixin_sub, step_by_id, ancestors_reset
        )

        reloaded_parent = store.get_step(parent.id)
        assert reloaded_parent.state == StepState.MIXIN_BLOCKS_CONTINUE, (
            f"parent must resume at MIXIN_BLOCKS_CONTINUE so it re-waits on "
            f"the mixin; got {reloaded_parent.state}"
        )
        assert parent.id in ancestors_reset

    def test_non_mixin_step_resets_to_event_transmit(self, store):
        """A regular (non-mixin) errored step still resets to
        EVENT_TRANSMIT, matching the pre-Scope-C behavior."""
        from facetwork.runtime.states import StepState
        from facetwork.runtime.step import StepDefinition
        from facetwork.runtime.types import ObjectType

        regular = StepDefinition.create(
            workflow_id="wf-repair",
            object_type=ObjectType.VARIABLE_ASSIGNMENT,
            facet_name="Download",
        )
        regular.mark_error(RuntimeError("transient"))
        store.save_step(regular)
        step_by_id = {regular.id: regular}

        store._reset_failed_step_and_ancestors(regular, step_by_id, [])

        reloaded = store.get_step(regular.id)
        assert reloaded.state == StepState.EVENT_TRANSMIT


@needs_mongomock
class TestRepairResetContainerStepNotEventTransmit:
    """A Workflow / Block step that errored only because a descendant
    errored must NOT be reset to EVENT_TRANSMIT.

    EVENT_TRANSMIT on a container step makes the runtime spawn a bogus
    event task named after the workflow (via ``_create_event_task``) —
    one that no runner can service (the workflow facet has no handler),
    leaving the run wedged. Container steps must instead re-drive their
    block continuation. Regression for the repair-workflow continuation
    mis-routing bug.
    """

    def test_workflow_step_resets_to_statement_blocks_continue(self, store):
        from facetwork.runtime.states import StepState
        from facetwork.runtime.step import StepDefinition
        from facetwork.runtime.types import ObjectType

        wf = StepDefinition.create(
            workflow_id="wf-repair-container",
            object_type=ObjectType.WORKFLOW,
            facet_name="CitiesByZoomTiledMapFanout",
        )
        # The workflow step is in STATEMENT_ERROR only because a
        # descendant (a tile build) errored; the propagated message even
        # matches a transient pattern, so the transient-retry loop picks
        # it up.
        wf.mark_error(
            RuntimeError("Block has 2 errored step(s): [Errno 2] "
                         "No such file or directory: 'tippecanoe'")
        )
        store.save_step(wf)

        store._reset_failed_step_and_ancestors(wf, {wf.id: wf}, [])

        reloaded = store.get_step(wf.id)
        assert reloaded.state != StepState.EVENT_TRANSMIT, (
            "a Workflow step reset to EVENT_TRANSMIT spawns a bogus, "
            "unclaimable event task named after the workflow"
        )
        assert reloaded.state == StepState.STATEMENT_BLOCKS_CONTINUE

    def test_block_step_resets_to_block_execution_continue(self, store):
        from facetwork.runtime.states import StepState
        from facetwork.runtime.step import StepDefinition
        from facetwork.runtime.types import ObjectType

        block = StepDefinition.create(
            workflow_id="wf-repair-container",
            object_type=ObjectType.AND_THEN,
            facet_name="",
        )
        block.mark_error(RuntimeError("Block has errored step(s)"))
        store.save_step(block)

        store._reset_failed_step_and_ancestors(block, {block.id: block}, [])

        reloaded = store.get_step(block.id)
        assert reloaded.state != StepState.EVENT_TRANSMIT
        assert reloaded.state == StepState.BLOCK_EXECUTION_CONTINUE

"""Recovery actions for mixin sub-steps (Scope C).

Mixin sub-steps live as direct children of a parent step
(``container_id`` set, ``block_id`` unset, ``statement_name`` = alias).
The dashboard recovery endpoints recognize them and:

  * **retry** resets the sub-step to ``CREATED`` (so it walks the full
    STEP_TRANSITIONS lifecycle again, re-binding params and re-running
    the mixin facet's body), and resets the parent to
    ``MIXIN_BLOCKS_CONTINUE`` (so it re-waits on the aliased mixin,
    not at ``STATEMENT_BLOCKS_CONTINUE`` which would skip the mixin
    phase entirely).
  * **rerun** does the same target reset, but also (a) deletes the
    parent's body's child block (so the body re-executes against
    fresh mixin returns) and (b) clears the parent's own returns and
    walks any downstream dependents of the parent.

This test file pins both behaviors against an in-memory mongomock
store.
"""

from __future__ import annotations

import time as _time

import pytest

try:
    from fastapi.testclient import TestClient

    FASTAPI_AVAILABLE = True
except ImportError:
    FASTAPI_AVAILABLE = False

try:
    import mongomock

    MONGOMOCK_AVAILABLE = True
except ImportError:
    MONGOMOCK_AVAILABLE = False

from facetwork.runtime.states import StepState
from facetwork.runtime.step import StepDefinition
from facetwork.runtime.types import AttributeValue, FacetAttributes, ObjectType, step_id


needs_fastapi = pytest.mark.skipif(
    not FASTAPI_AVAILABLE or not MONGOMOCK_AVAILABLE,
    reason="fastapi or mongomock not installed",
)


def _step(
    *,
    id: str | None = None,
    object_type: str = ObjectType.VARIABLE_ASSIGNMENT,
    workflow_id: str = "wf-1",
    facet_name: str = "",
    statement_name: str = "",
    container_id: str | None = None,
    block_id: str | None = None,
    root_id: str | None = None,
    state: str = StepState.STATEMENT_ERROR,
    params: dict | None = None,
    returns: dict | None = None,
) -> StepDefinition:
    attrs = FacetAttributes(
        params={k: AttributeValue(k, v) for k, v in (params or {}).items()},
        returns={k: AttributeValue(k, v) for k, v in (returns or {}).items()},
    )
    return StepDefinition(
        id=id or step_id(),
        object_type=object_type,
        workflow_id=workflow_id,
        facet_name=facet_name,
        statement_name=statement_name,
        container_id=container_id,
        block_id=block_id,
        root_id=root_id,
        state=state,
        attributes=attrs,
    )


@pytest.fixture
def client():
    if not (FASTAPI_AVAILABLE and MONGOMOCK_AVAILABLE):
        pytest.skip("fastapi or mongomock not installed")
    from facetwork.dashboard import dependencies as deps
    from facetwork.dashboard.app import create_app
    from facetwork.runtime.mongo_store import MongoStore

    store = MongoStore(database_name="afl_test_recovery_mixin", client=mongomock.MongoClient())
    app = create_app()
    app.dependency_overrides[deps.get_store] = lambda: store
    with TestClient(app, follow_redirects=False) as tc:
        yield tc, store
    store.drop_database()
    store.close()


# ---------------------------------------------------------------------------
# Retry on a mixin sub-step
# ---------------------------------------------------------------------------


@needs_fastapi
class TestRetryMixinSubStep:
    """Errored mixin sub-step → retry resets to CREATED and parent to
    MIXIN_BLOCKS_CONTINUE."""

    def _seed(self, store):
        # Parent in STATEMENT_ERROR (its MIXIN_BLOCKS_CONTINUE raised
        # because the aliased mixin sub-step errored).
        parent = _step(
            id="parent-1",
            facet_name="Parent",
            state=StepState.STATEMENT_ERROR,
            params={"input": "alpha", "m": {"input": "alpha"}},
        )
        store.save_step(parent)
        # Aliased mixin sub-step: direct child of parent, no block_id,
        # statement_name = "m" (the alias).
        mixin = _step(
            id="mixin-1",
            facet_name="M",
            statement_name="m",
            container_id="parent-1",
            block_id=None,
            state=StepState.STATEMENT_ERROR,
            params={"input": "alpha"},
        )
        store.save_step(mixin)
        return parent, mixin

    def test_retry_resets_mixin_to_created_and_parent_to_mixin_blocks_continue(self, client):
        tc, store = client
        parent, mixin = self._seed(store)

        resp = tc.post(f"/steps/{mixin.id}/retry")
        assert resp.status_code == 303  # redirect to step detail

        mixin_after = store.get_step(mixin.id)
        assert mixin_after.state == StepState.CREATED
        assert mixin_after.transition.error is None

        parent_after = store.get_step(parent.id)
        assert parent_after.state == StepState.MIXIN_BLOCKS_CONTINUE, (
            f"parent should resume at MIXIN_BLOCKS_CONTINUE so it re-waits on "
            f"the mixin, got {parent_after.state}"
        )

    def test_retry_clears_mixin_returns(self, client):
        """The mixin's prior (partial / wrong) returns are wiped so the
        re-run produces fresh output rather than merging with stale data."""
        tc, store = client
        parent = _step(id="parent-2", facet_name="Parent")
        store.save_step(parent)
        mixin = _step(
            id="mixin-2",
            facet_name="M",
            statement_name="m",
            container_id="parent-2",
            state=StepState.STATEMENT_ERROR,
            returns={"output": "stale-value"},
        )
        store.save_step(mixin)

        tc.post(f"/steps/{mixin.id}/retry")
        mixin_after = store.get_step(mixin.id)
        assert mixin_after.attributes.returns == {}

    def test_non_mixin_sibling_uses_default_event_transmit_reset(self, client):
        """A non-mixin child (regular body step with block_id set) should
        still reset to EVENT_TRANSMIT, not CREATED — the new mixin path
        must not change the existing behavior for body steps."""
        tc, store = client
        parent = _step(id="parent-3", facet_name="Parent")
        store.save_step(parent)
        body_block = _step(
            id="body-block",
            object_type=ObjectType.AND_THEN,
            container_id="parent-3",
            state=StepState.BLOCK_EXECUTION_CONTINUE,
        )
        store.save_step(body_block)
        body_step = _step(
            id="body-step",
            facet_name="Inner",
            statement_name="s",
            container_id="parent-3",
            block_id="body-block",  # ← in a block, not a mixin sub-step
            state=StepState.STATEMENT_ERROR,
        )
        store.save_step(body_step)

        tc.post(f"/steps/{body_step.id}/retry")
        body_after = store.get_step(body_step.id)
        assert body_after.state == StepState.EVENT_TRANSMIT


# ---------------------------------------------------------------------------
# Rerun on a mixin sub-step
# ---------------------------------------------------------------------------


@needs_fastapi
class TestRerunMixinSubStep:
    """Re-run on a completed mixin sub-step: resets the mixin, deletes the
    parent's body block (so it re-executes), and clears the parent's
    returns."""

    def _seed(self, store):
        parent = _step(
            id="parent-rr",
            facet_name="Parent",
            state=StepState.STATEMENT_COMPLETE,
            params={"input": "alpha", "m": {"input": "alpha", "output": "result"}},
            returns={"out": "primary-result"},
        )
        store.save_step(parent)
        mixin = _step(
            id="mixin-rr",
            facet_name="M",
            statement_name="m",
            container_id="parent-rr",
            block_id=None,
            state=StepState.STATEMENT_COMPLETE,
            params={"input": "alpha"},
            returns={"output": "result"},
        )
        store.save_step(mixin)
        # Parent's body block (regular andThen block under parent)
        body_block = _step(
            id="body-block-rr",
            object_type=ObjectType.AND_THEN,
            container_id="parent-rr",
            state=StepState.STATEMENT_COMPLETE,
        )
        store.save_step(body_block)
        # A statement step inside the body block
        body_step = _step(
            id="body-step-rr",
            facet_name="Inner",
            statement_name="x",
            container_id="parent-rr",
            block_id="body-block-rr",
            state=StepState.STATEMENT_COMPLETE,
        )
        store.save_step(body_step)
        return parent, mixin, body_block, body_step

    def test_rerun_resets_mixin_clears_parent_returns_deletes_body(self, client):
        tc, store = client
        parent, mixin, body_block, body_step = self._seed(store)

        resp = tc.post(f"/steps/{mixin.id}/rerun")
        assert resp.status_code == 303

        mixin_after = store.get_step(mixin.id)
        assert mixin_after.state == StepState.CREATED
        assert mixin_after.attributes.returns == {}

        parent_after = store.get_step(parent.id)
        assert parent_after.attributes.returns == {}
        assert parent_after.state == StepState.MIXIN_BLOCKS_CONTINUE

        # Body block + its descendants should be gone — they'll be
        # re-created when the parent re-walks STATEMENT_BLOCKS.
        assert store.get_step(body_block.id) is None
        assert store.get_step(body_step.id) is None

    def test_rerun_preserves_sibling_mixin_sub_steps(self, client):
        """If the parent has another aliased mixin, the rerun must NOT
        delete it — only the target mixin and the parent's body."""
        tc, store = client
        parent = _step(id="parent-mm", facet_name="Parent")
        store.save_step(parent)
        m1 = _step(
            id="m1",
            facet_name="M",
            statement_name="m1",
            container_id="parent-mm",
            state=StepState.STATEMENT_COMPLETE,
            returns={"output": "v1"},
        )
        m2 = _step(
            id="m2",
            facet_name="M",
            statement_name="m2",
            container_id="parent-mm",
            state=StepState.STATEMENT_COMPLETE,
            returns={"output": "v2"},
        )
        store.save_step(m1)
        store.save_step(m2)

        tc.post(f"/steps/{m1.id}/rerun")

        # m1 reset, m2 untouched.
        m1_after = store.get_step(m1.id)
        m2_after = store.get_step(m2.id)
        assert m1_after.state == StepState.CREATED
        assert m1_after.attributes.returns == {}
        assert m2_after.state == StepState.STATEMENT_COMPLETE
        assert m2_after.attributes.returns["output"].value == "v2"


# ---------------------------------------------------------------------------
# retry-block on a parent that has errored mixin sub-steps in its tree
# ---------------------------------------------------------------------------


@needs_fastapi
class TestRetryBlockMixinSubStep:
    """Retry All Errors clicked on a parent whose mixin sub-step
    errored should treat the mixin sub-step as a leaf and reset it
    to CREATED (not EVENT_TRANSMIT); the parent container ancestor
    resumes at MIXIN_BLOCKS_CONTINUE."""

    def test_retry_block_resets_errored_mixin_leaf_to_created(self, client):
        tc, store = client
        parent = _step(
            id="parent-rb",
            facet_name="Parent",
            state=StepState.STATEMENT_ERROR,
        )
        store.save_step(parent)
        mixin = _step(
            id="mixin-rb",
            facet_name="M",
            statement_name="m",
            container_id="parent-rb",
            state=StepState.STATEMENT_ERROR,
            returns={"output": "stale"},
        )
        store.save_step(mixin)

        resp = tc.post(f"/steps/{parent.id}/retry-block")
        assert resp.status_code == 303

        mixin_after = store.get_step(mixin.id)
        assert mixin_after.state == StepState.CREATED, (
            f"errored mixin leaf must reset to CREATED, not EVENT_TRANSMIT; got {mixin_after.state}"
        )
        assert mixin_after.attributes.returns == {}

        parent_after = store.get_step(parent.id)
        assert parent_after.state == StepState.MIXIN_BLOCKS_CONTINUE


# ---------------------------------------------------------------------------
# reset-block deletes mixin sub-steps as descendants — they regenerate.
# ---------------------------------------------------------------------------


@needs_fastapi
class TestResetBlockMixinSubStep:
    def test_reset_block_on_parent_deletes_mixin_sub_steps(self, client):
        """Resetting a parent step's "block" wipes everything beneath
        it — mixin sub-steps included — so the next pass through
        MixinBlocksBeginHandler recreates them."""
        tc, store = client
        parent = _step(id="parent-rsb", facet_name="Parent")
        store.save_step(parent)
        mixin = _step(
            id="mixin-rsb",
            facet_name="M",
            statement_name="m",
            container_id="parent-rsb",
            state=StepState.STATEMENT_COMPLETE,
        )
        store.save_step(mixin)

        resp = tc.post(f"/steps/{parent.id}/reset-block")
        assert resp.status_code == 303

        # Mixin sub-step deleted as a descendant — it will be re-created
        # by MixinBlocksBeginHandler when the parent walks the mixin
        # phase again.
        assert store.get_step(mixin.id) is None

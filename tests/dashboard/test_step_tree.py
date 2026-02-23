"""Tests for the hierarchical step tree view."""

from __future__ import annotations

import pytest

from afl.dashboard.tree import StepNode, build_step_tree
from afl.runtime.step import StepDefinition
from afl.runtime.types import step_id

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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _step(
    *,
    id: str | None = None,
    object_type: str = "VariableAssignment",
    workflow_id: str = "wf-1",
    facet_name: str = "",
    statement_name: str = "",
    container_id: str | None = None,
    block_id: str | None = None,
    root_id: str | None = None,
    state: str = "state.facet.initialization.Begin",
) -> StepDefinition:
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
    )


# ---------------------------------------------------------------------------
# build_step_tree unit tests
# ---------------------------------------------------------------------------


class TestBuildStepTree:
    def test_empty_list(self):
        assert build_step_tree([]) == []

    def test_single_root(self):
        root = _step(id="r1", statement_name="root")
        tree = build_step_tree([root])
        assert len(tree) == 1
        assert tree[0].step is root
        assert tree[0].children == []
        assert tree[0].depth == 0

    def test_root_with_block_and_statements(self):
        root = _step(id="r1", object_type="Workflow", statement_name="main")
        block = _step(id="b1", object_type="AndThen", container_id="r1", root_id="r1")
        s1 = _step(id="s1", statement_name="step1", block_id="b1", root_id="r1")
        s2 = _step(id="s2", statement_name="step2", block_id="b1", root_id="r1")

        tree = build_step_tree([root, block, s1, s2])
        assert len(tree) == 1
        root_node = tree[0]
        assert root_node.depth == 0
        assert len(root_node.children) == 1  # the block

        block_node = root_node.children[0]
        assert block_node.step is block
        assert block_node.depth == 1
        assert len(block_node.children) == 2  # two statements

        assert block_node.children[0].step is s1
        assert block_node.children[0].depth == 2
        assert block_node.children[1].step is s2
        assert block_node.children[1].depth == 2

    def test_deep_nesting(self):
        root = _step(id="r1", object_type="Workflow")
        block1 = _step(id="b1", object_type="AndThen", container_id="r1", root_id="r1")
        stmt = _step(id="s1", statement_name="outer", block_id="b1", root_id="r1")
        block2 = _step(id="b2", object_type="AndThen", container_id="s1", root_id="r1")
        nested_stmt = _step(id="s2", statement_name="inner", block_id="b2", root_id="r1")

        tree = build_step_tree([root, block1, stmt, block2, nested_stmt])
        assert len(tree) == 1

        # root -> block1 -> stmt -> block2 -> nested_stmt
        root_node = tree[0]
        block1_node = root_node.children[0]
        stmt_node = block1_node.children[0]
        assert stmt_node.step is stmt
        assert stmt_node.depth == 2
        assert len(stmt_node.children) == 1

        block2_node = stmt_node.children[0]
        assert block2_node.step is block2
        assert block2_node.depth == 3

        nested_node = block2_node.children[0]
        assert nested_node.step is nested_stmt
        assert nested_node.depth == 4

    def test_multiple_roots(self):
        r1 = _step(id="r1", object_type="Workflow", statement_name="wf1")
        r2 = _step(id="r2", object_type="Workflow", statement_name="wf2")

        tree = build_step_tree([r1, r2])
        assert len(tree) == 2
        assert tree[0].step is r1
        assert tree[1].step is r2

    def test_preserves_step_order(self):
        root = _step(id="r1", object_type="Workflow")
        block = _step(id="b1", object_type="AndThen", container_id="r1", root_id="r1")
        s1 = _step(id="s1", statement_name="first", block_id="b1", root_id="r1")
        s2 = _step(id="s2", statement_name="second", block_id="b1", root_id="r1")
        s3 = _step(id="s3", statement_name="third", block_id="b1", root_id="r1")

        tree = build_step_tree([root, block, s1, s2, s3])
        block_node = tree[0].children[0]
        names = [n.step.statement_name for n in block_node.children]
        assert names == ["first", "second", "third"]

    def test_orphan_steps_not_in_tree(self):
        """Steps that have root_id set but no matching container/block parent are not roots."""
        root = _step(id="r1", object_type="Workflow")
        orphan = _step(id="o1", block_id="missing-block", root_id="r1")

        tree = build_step_tree([root, orphan])
        # Only root shows up as a root node
        assert len(tree) == 1
        assert tree[0].step is root
        # Orphan's block_id doesn't match any step's id, so it won't appear as a child
        assert tree[0].children == []


# ---------------------------------------------------------------------------
# Integration tests (require fastapi + mongomock)
# ---------------------------------------------------------------------------

needs_fastapi = pytest.mark.skipif(
    not FASTAPI_AVAILABLE or not MONGOMOCK_AVAILABLE,
    reason="fastapi or mongomock not installed",
)


@needs_fastapi
class TestTreeIntegration:
    @pytest.fixture
    def client(self):
        from afl.dashboard import dependencies as deps
        from afl.dashboard.app import create_app
        from afl.runtime.mongo_store import MongoStore

        mock_client = mongomock.MongoClient()
        store = MongoStore(database_name="afl_test_tree", client=mock_client)

        app = create_app()
        app.dependency_overrides[deps.get_store] = lambda: store

        with TestClient(app) as tc:
            yield tc, store

        store.drop_database()
        store.close()

    def _seed(self, store):
        from afl.runtime.entities import RunnerDefinition, WorkflowDefinition

        wf = WorkflowDefinition(
            uuid="wf-1",
            name="TestWF",
            namespace_id="ns-1",
            facet_id="f-1",
            flow_id="flow-1",
            starting_step="s-1",
            version="1.0",
        )
        runner = RunnerDefinition(
            uuid="r-1", workflow_id=wf.uuid, workflow=wf, state="running"
        )
        store.save_runner(runner)

        root = _step(id="root-1", object_type="Workflow", facet_name="TestWF")
        block = _step(
            id="block-1", object_type="AndThen", container_id="root-1", root_id="root-1"
        )
        stmt = _step(
            id="stmt-1",
            statement_name="doWork",
            facet_name="DoWork",
            block_id="block-1",
            root_id="root-1",
        )

        for s in [root, block, stmt]:
            s.workflow_id = wf.uuid
            store.save_step(s)

    def test_tree_partial_returns_details_html(self, client):
        tc, store = client
        self._seed(store)

        resp = tc.get("/api/runners/r-1/steps?partial=true&view=tree")
        assert resp.status_code == 200
        assert "<details" in resp.text
        assert "step-tree-node" in resp.text

    def test_flat_partial_unchanged(self, client):
        tc, store = client
        self._seed(store)

        resp = tc.get("/api/runners/r-1/steps?partial=true")
        assert resp.status_code == 200
        assert "<tr data-state-category=" in resp.text
        # Flat view should not contain tree markup
        assert "step-tree-node" not in resp.text

    def test_runner_detail_has_toggle(self, client):
        tc, store = client
        self._seed(store)

        resp = tc.get("/runners/r-1")
        assert resp.status_code == 200
        assert "view-toggle" in resp.text
        assert 'id="step-flat"' in resp.text
        assert 'id="step-tree"' in resp.text

    def test_runner_steps_page_has_toggle(self, client):
        tc, store = client
        self._seed(store)

        resp = tc.get("/runners/r-1/steps")
        assert resp.status_code == 200
        assert "view-toggle" in resp.text
        assert 'id="step-flat"' in resp.text
        assert 'id="step-tree"' in resp.text

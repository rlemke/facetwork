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

"""Tests for catch block execution handlers.

Tests CatchBeginHandler, CatchContinueHandler, and CatchEndHandler
which implement error recovery for steps with catch clauses.
"""

from unittest.mock import MagicMock

from facetwork.runtime.changers.base import StateChangeResult
from facetwork.runtime.handlers.catch_execution import (
    CatchBeginHandler,
    CatchContinueHandler,
    CatchEndHandler,
)
from facetwork.runtime.persistence import IterationChanges
from facetwork.runtime.states import StepState
from facetwork.runtime.step import StepDefinition
from facetwork.runtime.types import ObjectType


def _make_step(
    facet_name: str = "TestFacet",
    error: Exception | None = None,
    statement_id: str | None = None,
    statement_name: str = "",
    container_id: str | None = None,
    object_type: str = ObjectType.VARIABLE_ASSIGNMENT,
    workflow_id: str = "wf-test",
) -> StepDefinition:
    """Create a test step, optionally with an error on its transition."""
    step = StepDefinition.create(
        workflow_id=workflow_id,
        object_type=object_type,
        facet_name=facet_name,
        statement_id=statement_id,
        statement_name=statement_name,
        container_id=container_id,
    )
    if error:
        step.transition.error = error
    return step


def _make_context(
    catch_ast: dict | None = None,
    workflow_root: StepDefinition | None = None,
) -> MagicMock:
    """Create a mock ExecutionContext for catch handlers."""
    context = MagicMock()
    context._find_statement_catch.return_value = catch_ast
    context.telemetry = MagicMock()
    context.changes = IterationChanges()
    context.persistence = MagicMock()
    context.persistence.block_step_exists.return_value = False
    context.persistence.step_exists.return_value = False
    context.persistence.get_blocks_by_step.return_value = []
    context.persistence.get_steps_by_block.return_value = []

    if workflow_root:
        context.get_workflow_root.return_value = workflow_root
    else:
        root = _make_step(object_type=ObjectType.WORKFLOW)
        context.get_workflow_root.return_value = root

    return context


# ---------------------------------------------------------------------------
# CatchBeginHandler tests
# ---------------------------------------------------------------------------


class TestCatchBeginHandler:
    """Tests for CatchBeginHandler."""

    def test_no_catch_clause_transitions(self):
        """When no catch clause is found, handler transitions immediately."""
        step = _make_step(error=ValueError("boom"))
        context = _make_context(catch_ast=None)

        handler = CatchBeginHandler(step, context)
        result = handler.process_state()

        assert isinstance(result, StateChangeResult)
        assert step.transition.request_transition is True

    def test_simple_catch_stores_error_info(self):
        """Simple catch stores error and error_type as pseudo-returns."""
        error = ValueError("something went wrong")
        step = _make_step(error=error)
        catch_ast = {
            "steps": [
                {
                    "type": "StepStmt",
                    "name": "r",
                    "call": {"type": "CallExpr", "target": "Recover"},
                },
            ],
        }
        context = _make_context(catch_ast=catch_ast)

        handler = CatchBeginHandler(step, context)
        handler.process_state()

        assert step.get_attribute("error") == "something went wrong"
        assert step.get_attribute("error_type") == "ValueError"

    def test_simple_catch_creates_sub_block(self):
        """Simple catch creates a single AND_CATCH sub-block step."""
        error = RuntimeError("fail")
        step = _make_step(error=error)
        catch_ast = {
            "steps": [
                {
                    "type": "StepStmt",
                    "name": "r",
                    "call": {"type": "CallExpr", "target": "Recover"},
                },
            ],
        }
        context = _make_context(catch_ast=catch_ast)

        handler = CatchBeginHandler(step, context)
        result = handler.process_state()

        assert isinstance(result, StateChangeResult)
        assert step.transition.request_transition is True
        assert len(context.changes.created_steps) == 1

        block = context.changes.created_steps[0]
        assert block.object_type == ObjectType.AND_CATCH
        assert block.container_id == step.id
        assert block.workflow_id == step.workflow_id

    def test_simple_catch_with_yield(self):
        """Simple catch with yield clause propagates yield to sub-block AST."""
        step = _make_step(error=RuntimeError("err"))
        catch_ast = {
            "steps": [
                {
                    "type": "StepStmt",
                    "name": "r",
                    "call": {"type": "CallExpr", "target": "Recover"},
                },
            ],
            "yield": {"type": "YieldExpr", "value": "r.output"},
        }
        context = _make_context(catch_ast=catch_ast)

        handler = CatchBeginHandler(step, context)
        handler.process_state()

        assert len(context.changes.created_steps) == 1
        # Verify the AST was cached with the yield
        context.set_block_ast_cache.assert_called_once()
        cached_ast = context.set_block_ast_cache.call_args[0][1]
        assert "yield" in cached_ast
        assert "steps" in cached_ast

    def test_simple_catch_with_yields(self):
        """Simple catch with yields clause propagates yields to sub-block AST."""
        step = _make_step(error=RuntimeError("err"))
        catch_ast = {
            "yields": [{"name": "out", "value": "r.output"}],
        }
        context = _make_context(catch_ast=catch_ast)

        handler = CatchBeginHandler(step, context)
        handler.process_state()

        assert len(context.changes.created_steps) == 1
        cached_ast = context.set_block_ast_cache.call_args[0][1]
        assert "yields" in cached_ast

    def test_simple_catch_idempotency_persisted(self):
        """If block already persisted, handler transitions without creating."""
        step = _make_step(error=RuntimeError("err"))
        catch_ast = {"steps": []}
        context = _make_context(catch_ast=catch_ast)
        context.persistence.block_step_exists.return_value = True

        handler = CatchBeginHandler(step, context)
        handler.process_state()

        assert step.transition.request_transition is True
        assert len(context.changes.created_steps) == 0

    def test_simple_catch_idempotency_pending(self):
        """If block already pending in changes, handler transitions without duplication."""
        step = _make_step(error=RuntimeError("err"))
        catch_ast = {"steps": []}
        context = _make_context(catch_ast=catch_ast)

        # Pre-populate a pending step matching the catch block
        pending = StepDefinition.create(
            workflow_id=step.workflow_id,
            object_type=ObjectType.AND_CATCH,
            facet_name="",
            statement_id="catch-block-0",
            container_id=step.id,
        )
        context.changes.add_created_step(pending)

        handler = CatchBeginHandler(step, context)
        handler.process_state()

        # Should not add another step
        assert len(context.changes.created_steps) == 1

    def test_simple_catch_unknown_error(self):
        """When error is None, defaults to 'Unknown error' / 'RuntimeError'."""
        step = _make_step()
        step.transition.error = None  # No error object
        catch_ast = {"steps": []}
        context = _make_context(catch_ast=catch_ast)

        handler = CatchBeginHandler(step, context)
        handler.process_state()

        assert step.get_attribute("error") == "Unknown error"
        assert step.get_attribute("error_type") == "RuntimeError"

    # --- catch when tests ---

    def test_catch_when_matching_case(self):
        """catch when creates sub-block for matching condition."""
        error = ValueError("bad input")
        step = _make_step(error=error, statement_name="s1")
        catch_ast = {
            "when": {
                "cases": [
                    {
                        "condition": {
                            "type": "BinaryExpr",
                            "operator": "==",
                            "left": {"type": "InputRef", "path": ["error_type"]},
                            "right": {"type": "String", "value": "ValueError"},
                        },
                        "steps": [
                            {
                                "type": "StepStmt",
                                "name": "r",
                                "call": {"type": "CallExpr", "target": "HandleValueError"},
                            },
                        ],
                    },
                    {
                        "default": True,
                        "steps": [],
                    },
                ],
            },
        }
        context = _make_context(catch_ast=catch_ast)

        handler = CatchBeginHandler(step, context)
        handler.process_state()

        # Should create sub-block for case 0 (matched), NOT for default (case 1)
        assert len(context.changes.created_steps) == 1
        block = context.changes.created_steps[0]
        assert block.statement_id == "catch-case-0"

    def test_catch_when_default_case_fires_when_no_match(self):
        """Default case fires when no other case matches."""
        error = TypeError("wrong type")
        step = _make_step(error=error, statement_name="s1")
        catch_ast = {
            "when": {
                "cases": [
                    {
                        "condition": {
                            "type": "BinaryExpr",
                            "operator": "==",
                            "left": {"type": "InputRef", "path": ["error_type"]},
                            "right": {"type": "String", "value": "ValueError"},
                        },
                        "steps": [],
                    },
                    {
                        "default": True,
                        "steps": [
                            {
                                "type": "StepStmt",
                                "name": "fallback",
                                "call": {"type": "CallExpr", "target": "DefaultRecover"},
                            },
                        ],
                    },
                ],
            },
        }
        context = _make_context(catch_ast=catch_ast)

        handler = CatchBeginHandler(step, context)
        handler.process_state()

        # Only default case should fire
        assert len(context.changes.created_steps) == 1
        block = context.changes.created_steps[0]
        assert block.statement_id == "catch-case-1"

    def test_catch_when_no_condition_skips_case(self):
        """Case with no condition is skipped."""
        step = _make_step(error=RuntimeError("err"))
        catch_ast = {
            "when": {
                "cases": [
                    {
                        # No condition and not default — skip
                        "steps": [],
                    },
                    {
                        "default": True,
                        "steps": [],
                    },
                ],
            },
        }
        context = _make_context(catch_ast=catch_ast)

        handler = CatchBeginHandler(step, context)
        handler.process_state()

        # Case 0 has no condition (skipped), default fires
        assert len(context.changes.created_steps) == 1
        assert context.changes.created_steps[0].statement_id == "catch-case-1"

    def test_catch_when_condition_evaluation_failure_skips_case(self):
        """If condition evaluation raises, that case is skipped."""
        step = _make_step(error=RuntimeError("err"))
        catch_ast = {
            "when": {
                "cases": [
                    {
                        "condition": {
                            # Deliberately broken expression
                            "type": "Ref",
                            "name": "nonexistent_var",
                        },
                        "steps": [],
                    },
                    {
                        "default": True,
                        "steps": [],
                    },
                ],
            },
        }
        context = _make_context(catch_ast=catch_ast)

        handler = CatchBeginHandler(step, context)
        handler.process_state()

        # Broken condition is skipped, default fires
        assert len(context.changes.created_steps) == 1
        assert context.changes.created_steps[0].statement_id == "catch-case-1"

    def test_catch_when_condition_evaluates_false(self):
        """Condition that evaluates to false skips the case."""
        step = _make_step(error=RuntimeError("err"))
        catch_ast = {
            "when": {
                "cases": [
                    {
                        "condition": {
                            "type": "Boolean",
                            "value": False,
                        },
                        "steps": [],
                    },
                    {
                        "default": True,
                        "steps": [],
                    },
                ],
            },
        }
        context = _make_context(catch_ast=catch_ast)

        handler = CatchBeginHandler(step, context)
        handler.process_state()

        # False condition skipped, default fires
        assert len(context.changes.created_steps) == 1
        assert context.changes.created_steps[0].statement_id == "catch-case-1"

    def test_catch_when_idempotency_persisted(self):
        """If catch-case step already persisted, it is not re-created."""
        step = _make_step(error=RuntimeError("err"))
        catch_ast = {
            "when": {
                "cases": [
                    {
                        "condition": {"type": "Boolean", "value": True},
                        "steps": [],
                    },
                    {"default": True, "steps": []},
                ],
            },
        }
        context = _make_context(catch_ast=catch_ast)
        # Pretend case-0 already exists
        context.persistence.step_exists.side_effect = lambda sid, bid: sid == "catch-case-0"

        handler = CatchBeginHandler(step, context)
        handler.process_state()

        # Case 0 skipped (persisted), default skipped (any_matched=True from case 0)
        assert len(context.changes.created_steps) == 0

    def test_catch_when_idempotency_pending(self):
        """If catch-case step already pending in changes, it is not duplicated."""
        step = _make_step(error=RuntimeError("err"))
        catch_ast = {
            "when": {
                "cases": [
                    {
                        "condition": {"type": "Boolean", "value": True},
                        "steps": [],
                    },
                    {"default": True, "steps": []},
                ],
            },
        }
        context = _make_context(catch_ast=catch_ast)

        # Pre-populate a pending step for catch-case-0
        pending = StepDefinition.create(
            workflow_id=step.workflow_id,
            object_type=ObjectType.AND_CATCH,
            facet_name="",
            statement_id="catch-case-0",
            block_id=step.id,
        )
        context.changes.add_created_step(pending)

        handler = CatchBeginHandler(step, context)
        handler.process_state()

        # Case 0 skipped (pending), default skipped (any_matched=True)
        assert len(context.changes.created_steps) == 1  # only the pre-existing one

    def test_catch_when_empty_cases(self):
        """Empty cases list creates no sub-blocks."""
        step = _make_step(error=RuntimeError("err"))
        catch_ast = {"when": {"cases": []}}
        context = _make_context(catch_ast=catch_ast)

        handler = CatchBeginHandler(step, context)
        handler.process_state()

        assert len(context.changes.created_steps) == 0
        assert step.transition.request_transition is True

    def test_catch_when_with_yield_and_yields(self):
        """catch when case with yield/yields propagates to sub-block AST."""
        step = _make_step(error=RuntimeError("err"))
        catch_ast = {
            "when": {
                "cases": [
                    {
                        "condition": {"type": "Boolean", "value": True},
                        "steps": [],
                        "yield": {"type": "YieldExpr"},
                        "yields": [{"name": "x"}],
                    },
                    {"default": True, "steps": []},
                ],
            },
        }
        context = _make_context(catch_ast=catch_ast)

        handler = CatchBeginHandler(step, context)
        handler.process_state()

        assert len(context.changes.created_steps) == 1
        cached_ast = context.set_block_ast_cache.call_args[0][1]
        assert "yield" in cached_ast
        assert "yields" in cached_ast

    def test_catch_when_workflow_root_params_in_eval_context(self):
        """Workflow root params are available in condition evaluation context."""
        root = _make_step(object_type=ObjectType.WORKFLOW)
        root.set_attribute("threshold", 5, is_return=False)

        step = _make_step(error=RuntimeError("err"))
        catch_ast = {
            "when": {
                "cases": [
                    {
                        "condition": {
                            "type": "BinaryExpr",
                            "operator": "==",
                            "left": {"type": "InputRef", "path": ["threshold"]},
                            "right": {"type": "Int", "value": 5},
                        },
                        "steps": [],
                    },
                    {"default": True, "steps": []},
                ],
            },
        }
        context = _make_context(catch_ast=catch_ast, workflow_root=root)

        handler = CatchBeginHandler(step, context)
        handler.process_state()

        # Condition uses threshold=5, should match
        assert len(context.changes.created_steps) == 1
        assert context.changes.created_steps[0].statement_id == "catch-case-0"

    def test_catch_when_no_workflow_root(self):
        """Handler works even when get_workflow_root returns None."""
        step = _make_step(error=RuntimeError("err"))
        catch_ast = {
            "when": {
                "cases": [
                    {
                        "condition": {"type": "Boolean", "value": True},
                        "steps": [],
                    },
                    {"default": True, "steps": []},
                ],
            },
        }
        context = _make_context(catch_ast=catch_ast)
        context.get_workflow_root.return_value = None

        handler = CatchBeginHandler(step, context)
        handler.process_state()

        assert len(context.changes.created_steps) == 1


# ---------------------------------------------------------------------------
# CatchContinueHandler tests
# ---------------------------------------------------------------------------


class TestCatchContinueHandler:
    """Tests for CatchContinueHandler."""

    def _make_block(
        self,
        container_id: str,
        complete: bool = False,
        error: bool = False,
    ) -> StepDefinition:
        """Create a sub-block step in the desired state."""
        block = StepDefinition.create(
            workflow_id="wf-test",
            object_type=ObjectType.AND_CATCH,
            facet_name="",
            container_id=container_id,
        )
        if complete:
            block.state = StepState.STATEMENT_COMPLETE
        elif error:
            block.state = StepState.STATEMENT_ERROR
        return block

    def test_no_blocks_transitions(self):
        """No catch blocks to wait for — transitions immediately."""
        step = _make_step()
        context = _make_context()

        handler = CatchContinueHandler(step, context)
        result = handler.process_state()

        assert isinstance(result, StateChangeResult)
        assert step.transition.request_transition is True

    def test_all_blocks_complete(self):
        """All sub-blocks complete — transitions to CATCH_END."""
        step = _make_step()
        context = _make_context()
        b1 = self._make_block(step.id, complete=True)
        b2 = self._make_block(step.id, complete=True)
        context.persistence.get_blocks_by_step.return_value = [b1, b2]

        handler = CatchContinueHandler(step, context)
        result = handler.process_state()

        assert step.transition.request_transition is True
        assert result.step == step

    def test_sub_block_errored_propagates(self):
        """Errored sub-block propagates error to parent step."""
        step = _make_step()
        context = _make_context()
        b1 = self._make_block(step.id, error=True)
        context.persistence.get_blocks_by_step.return_value = [b1]

        handler = CatchContinueHandler(step, context)
        handler.process_state()

        assert step.is_error is True
        assert step.state == StepState.STATEMENT_ERROR

    def test_blocks_still_in_progress(self):
        """In-progress sub-blocks cause handler to stay and push."""
        step = _make_step()
        context = _make_context()
        b1 = self._make_block(step.id, complete=True)
        b2 = self._make_block(step.id)  # still in progress
        context.persistence.get_blocks_by_step.return_value = [b1, b2]

        handler = CatchContinueHandler(step, context)
        result = handler.process_state()

        # Should stay in current state and request push
        assert step.transition.request_transition is False
        assert result.continue_processing is True

    def test_pending_blocks_in_changes(self):
        """Newly created blocks in changes are considered."""
        step = _make_step()
        context = _make_context()
        # No persisted blocks, but one pending
        pending = StepDefinition.create(
            workflow_id="wf-test",
            object_type=ObjectType.AND_CATCH,
            facet_name="",
            container_id=step.id,
        )
        context.changes.add_created_step(pending)

        handler = CatchContinueHandler(step, context)
        result = handler.process_state()

        # Pending block is in progress — should stay
        assert step.transition.request_transition is False
        assert result.continue_processing is True

    def test_pending_sub_blocks_by_block_id(self):
        """Pending sub-blocks with block_id are also tracked."""
        step = _make_step()
        context = _make_context()
        # Sub-block uses block_id (catch when pattern)
        pending = StepDefinition.create(
            workflow_id="wf-test",
            object_type=ObjectType.AND_CATCH,
            facet_name="",
            block_id=step.id,
        )
        context.changes.add_created_step(pending)

        handler = CatchContinueHandler(step, context)
        result = handler.process_state()

        # Pending block by block_id — should stay
        assert step.transition.request_transition is False
        assert result.continue_processing is True

    def test_mixed_complete_and_errored(self):
        """Mix of complete and errored sub-blocks reports error."""
        step = _make_step()
        context = _make_context()
        b1 = self._make_block(step.id, complete=True)
        b2 = self._make_block(step.id, error=True)
        context.persistence.get_blocks_by_step.return_value = [b1, b2]

        handler = CatchContinueHandler(step, context)
        handler.process_state()

        assert step.is_error is True

    def test_sub_blocks_from_persistence(self):
        """Sub-blocks fetched via get_steps_by_block are included."""
        step = _make_step()
        context = _make_context()
        sub = self._make_block(step.id, complete=True)
        sub.block_id = step.id
        context.persistence.get_steps_by_block.return_value = [sub]

        handler = CatchContinueHandler(step, context)
        handler.process_state()

        assert step.transition.request_transition is True


# ---------------------------------------------------------------------------
# CatchEndHandler tests
# ---------------------------------------------------------------------------


class TestCatchEndHandler:
    """Tests for CatchEndHandler."""

    def test_passthrough_transitions(self):
        """CatchEndHandler is a pass-through that just transitions."""
        step = _make_step()
        context = _make_context()

        handler = CatchEndHandler(step, context)
        result = handler.process_state()

        assert isinstance(result, StateChangeResult)
        assert step.transition.request_transition is True
        assert result.step == step

    def test_end_handler_returns_same_step(self):
        """Result contains the same step object."""
        step = _make_step(error=RuntimeError("recovered"))
        context = _make_context()

        handler = CatchEndHandler(step, context)
        result = handler.process_state()

        assert result.step is step

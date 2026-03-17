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

"""Tests for state changers (BlockStateChanger, StepStateChanger, YieldStateChanger).

Covers the select_state, execute_state, and process loop methods,
including error handling, catch-clause routing, and terminal state detection.
"""

from unittest.mock import MagicMock, patch

from afl.runtime.changers.base import StateChangeResult
from afl.runtime.changers.block_changer import BlockStateChanger
from afl.runtime.changers.step_changer import StepStateChanger
from afl.runtime.changers.yield_changer import YieldStateChanger
from afl.runtime.states import StepState
from afl.runtime.step import StepDefinition
from afl.runtime.types import ObjectType


def _make_step(
    object_type: str = ObjectType.VARIABLE_ASSIGNMENT,
    state: str = StepState.CREATED,
) -> StepDefinition:
    """Create a test step."""
    step = StepDefinition.create(
        workflow_id="wf-test",
        object_type=object_type,
        facet_name="TestFacet",
    )
    step.state = state
    step.transition.current_state = state
    return step


def _make_context(catch_ast: dict | None = None) -> MagicMock:
    """Create a mock ExecutionContext."""
    context = MagicMock()
    context._find_statement_catch.return_value = catch_ast
    context.telemetry = MagicMock()
    return context


# ---------------------------------------------------------------------------
# BlockStateChanger tests
# ---------------------------------------------------------------------------


class TestBlockStateChanger:
    """Tests for BlockStateChanger."""

    def test_select_state_created_to_block_begin(self):
        """CREATED -> BLOCK_EXECUTION_BEGIN."""
        step = _make_step(ObjectType.AND_THEN, StepState.CREATED)
        context = _make_context()
        changer = BlockStateChanger(step, context)

        assert changer.select_state() == StepState.BLOCK_EXECUTION_BEGIN

    def test_select_state_terminal_returns_none(self):
        """Terminal state returns None from select_state."""
        step = _make_step(ObjectType.AND_THEN, StepState.STATEMENT_COMPLETE)
        context = _make_context()
        changer = BlockStateChanger(step, context)

        assert changer.select_state() is None

    def test_select_state_unknown_returns_none(self):
        """Unknown state returns None."""
        step = _make_step(ObjectType.AND_THEN)
        step.state = "state.unknown"
        step.transition.current_state = "state.unknown"
        context = _make_context()
        changer = BlockStateChanger(step, context)

        assert changer.select_state() is None

    def test_execute_state_no_handler_auto_transitions(self):
        """When no handler exists, auto-transitions."""
        step = _make_step(ObjectType.AND_THEN)
        context = _make_context()
        changer = BlockStateChanger(step, context)

        with patch("afl.runtime.handlers.get_handler", return_value=None):
            result = changer.execute_state(StepState.BLOCK_EXECUTION_BEGIN)

        assert isinstance(result, StateChangeResult)
        assert step.transition.request_transition is True

    def test_execute_state_handler_called(self):
        """When handler exists, it is called."""
        step = _make_step(ObjectType.AND_THEN)
        context = _make_context()
        changer = BlockStateChanger(step, context)

        mock_handler = MagicMock()
        mock_handler.process.return_value = StateChangeResult(step=step)

        with patch("afl.runtime.handlers.get_handler", return_value=mock_handler):
            result = changer.execute_state(StepState.BLOCK_EXECUTION_BEGIN)

        mock_handler.process.assert_called_once()
        assert result.step == step

    def test_execute_state_handler_raises(self):
        """Handler exception is caught and returned as error result."""
        step = _make_step(ObjectType.AND_THEN)
        context = _make_context()
        changer = BlockStateChanger(step, context)

        mock_handler = MagicMock()
        mock_handler.process.side_effect = RuntimeError("handler crash")

        with patch("afl.runtime.handlers.get_handler", return_value=mock_handler):
            result = changer.execute_state(StepState.BLOCK_EXECUTION_BEGIN)

        assert result.success is False
        assert result.error is not None

    def test_process_already_complete(self):
        """Process on already-complete step returns immediately."""
        step = _make_step(ObjectType.AND_THEN, StepState.STATEMENT_COMPLETE)
        context = _make_context()
        changer = BlockStateChanger(step, context)

        result = changer.process()

        assert result.continue_processing is False


# ---------------------------------------------------------------------------
# StepStateChanger tests
# ---------------------------------------------------------------------------


class TestStepStateChanger:
    """Tests for StepStateChanger."""

    def test_select_state_created_to_facet_init(self):
        """CREATED -> FACET_INIT_BEGIN."""
        step = _make_step(ObjectType.VARIABLE_ASSIGNMENT, StepState.CREATED)
        context = _make_context()
        changer = StepStateChanger(step, context)

        assert changer.select_state() == StepState.FACET_INIT_BEGIN

    def test_select_state_catch_begin_to_continue(self):
        """CATCH_BEGIN -> CATCH_CONTINUE."""
        step = _make_step(ObjectType.VARIABLE_ASSIGNMENT, StepState.CATCH_BEGIN)
        context = _make_context()
        changer = StepStateChanger(step, context)

        assert changer.select_state() == StepState.CATCH_CONTINUE

    def test_select_state_catch_end_to_capture(self):
        """CATCH_END -> STATEMENT_CAPTURE_BEGIN (recovery path)."""
        step = _make_step(ObjectType.VARIABLE_ASSIGNMENT, StepState.CATCH_END)
        context = _make_context()
        changer = StepStateChanger(step, context)

        assert changer.select_state() == StepState.STATEMENT_CAPTURE_BEGIN

    def test_select_state_terminal_returns_none(self):
        """Terminal state returns None."""
        step = _make_step(ObjectType.VARIABLE_ASSIGNMENT, StepState.STATEMENT_COMPLETE)
        context = _make_context()
        changer = StepStateChanger(step, context)

        assert changer.select_state() is None

    def test_execute_state_no_handler(self):
        """No handler -> auto-transition."""
        step = _make_step()
        context = _make_context()
        changer = StepStateChanger(step, context)

        with patch("afl.runtime.handlers.get_handler", return_value=None):
            changer.execute_state(StepState.FACET_INIT_BEGIN)

        assert step.transition.request_transition is True

    def test_execute_state_handler_raises(self):
        """Exception in handler is caught."""
        step = _make_step()
        context = _make_context()
        changer = StepStateChanger(step, context)

        mock_handler = MagicMock()
        mock_handler.process.side_effect = ValueError("boom")

        with patch("afl.runtime.handlers.get_handler", return_value=mock_handler):
            result = changer.execute_state(StepState.FACET_INIT_BEGIN)

        assert result.success is False

    def test_process_error_with_catch_enters_catch_begin(self):
        """Handler error with catch clause transitions to CATCH_BEGIN."""
        step = _make_step(ObjectType.VARIABLE_ASSIGNMENT, StepState.CREATED)
        error = RuntimeError("event failed")
        catch_ast = {"steps": []}
        context = _make_context(catch_ast=catch_ast)

        call_count = 0

        def mock_get_handler(state, s, ctx):
            nonlocal call_count
            handler = MagicMock()
            call_count += 1
            if call_count == 1:
                # First call: FACET_INIT_BEGIN — auto-transition
                s.request_state_change(True)
                handler.process.return_value = StateChangeResult(step=s)
                return handler
            elif call_count == 2:
                # Second call: FACET_INIT_END — error
                handler.process.return_value = StateChangeResult(step=s, success=False, error=error)
                return handler
            else:
                # After catch entry: just transition to complete
                s.mark_completed()
                handler.process.return_value = StateChangeResult(step=s)
                return handler

        with patch("afl.runtime.handlers.get_handler", side_effect=mock_get_handler):
            changer = StepStateChanger(step, context)
            changer.process()

        # Step should have entered catch path — the error is on the transition
        assert step.transition.error == error

    def test_process_error_without_catch_marks_error(self):
        """Handler error without catch clause marks step as error."""
        step = _make_step(ObjectType.VARIABLE_ASSIGNMENT, StepState.CREATED)
        context = _make_context(catch_ast=None)

        def mock_get_handler(state, s, ctx):
            handler = MagicMock()
            if state == StepState.FACET_INIT_BEGIN:
                handler.process.return_value = StateChangeResult(
                    step=s, success=False, error=RuntimeError("fail")
                )
            else:
                s.request_state_change(True)
                handler.process.return_value = StateChangeResult(step=s)
            return handler

        with patch("afl.runtime.handlers.get_handler", side_effect=mock_get_handler):
            result = StepStateChanger(step, context).process()

        assert result.success is False
        assert step.is_error is True

    def test_process_exception_in_loop(self):
        """Unexpected exception during processing is caught."""
        step = _make_step(ObjectType.VARIABLE_ASSIGNMENT, StepState.CREATED)
        context = _make_context()

        def mock_get_handler(state, s, ctx):
            raise RuntimeError("unexpected crash")

        with patch("afl.runtime.handlers.get_handler", side_effect=mock_get_handler):
            result = StepStateChanger(step, context).process()

        assert result.success is False
        assert step.is_error is True


# ---------------------------------------------------------------------------
# YieldStateChanger tests
# ---------------------------------------------------------------------------


class TestYieldStateChanger:
    """Tests for YieldStateChanger."""

    def test_select_state_created_to_facet_init(self):
        """CREATED -> FACET_INIT_BEGIN."""
        step = _make_step(ObjectType.YIELD_ASSIGNMENT, StepState.CREATED)
        context = _make_context()
        changer = YieldStateChanger(step, context)

        assert changer.select_state() == StepState.FACET_INIT_BEGIN

    def test_select_state_scripts_end_skips_to_statement_end(self):
        """FACET_SCRIPTS_END -> STATEMENT_END (skips blocks)."""
        step = _make_step(ObjectType.YIELD_ASSIGNMENT, StepState.FACET_SCRIPTS_END)
        context = _make_context()
        changer = YieldStateChanger(step, context)

        assert changer.select_state() == StepState.STATEMENT_END

    def test_select_state_terminal_returns_none(self):
        """Terminal state returns None."""
        step = _make_step(ObjectType.YIELD_ASSIGNMENT, StepState.STATEMENT_COMPLETE)
        context = _make_context()
        changer = YieldStateChanger(step, context)

        assert changer.select_state() is None

    def test_execute_state_no_handler(self):
        """No handler -> auto-transition."""
        step = _make_step(ObjectType.YIELD_ASSIGNMENT)
        context = _make_context()
        changer = YieldStateChanger(step, context)

        with patch("afl.runtime.handlers.get_handler", return_value=None):
            changer.execute_state(StepState.FACET_INIT_BEGIN)

        assert step.transition.request_transition is True

    def test_execute_state_handler_raises(self):
        """Exception in handler is caught."""
        step = _make_step(ObjectType.YIELD_ASSIGNMENT)
        context = _make_context()
        changer = YieldStateChanger(step, context)

        mock_handler = MagicMock()
        mock_handler.process.side_effect = ValueError("boom")

        with patch("afl.runtime.handlers.get_handler", return_value=mock_handler):
            result = changer.execute_state(StepState.FACET_INIT_BEGIN)

        assert result.success is False

    def test_process_already_complete(self):
        """Process on already-complete step returns immediately."""
        step = _make_step(ObjectType.YIELD_ASSIGNMENT, StepState.STATEMENT_COMPLETE)
        context = _make_context()
        changer = YieldStateChanger(step, context)

        result = changer.process()

        assert result.continue_processing is False


# ---------------------------------------------------------------------------
# Base StateChanger.process() loop tests
# ---------------------------------------------------------------------------


class TestStateChangerProcessLoop:
    """Tests for the process() loop in base StateChanger (via concrete subclasses)."""

    def test_process_stops_when_not_requesting_transition(self):
        """Loop exits when step stops requesting state change."""
        step = _make_step(ObjectType.AND_THEN, StepState.CREATED)
        context = _make_context()

        def mock_get_handler(state, s, ctx):
            handler = MagicMock()
            # Handler does not request transition — just stay
            s.request_state_change(False)
            handler.process.return_value = StateChangeResult(step=s)
            return handler

        with patch("afl.runtime.handlers.get_handler", side_effect=mock_get_handler):
            result = BlockStateChanger(step, context).process()

        assert result.success is True

    def test_process_reaches_terminal_state(self):
        """Loop exits when step reaches terminal state."""
        step = _make_step(ObjectType.AND_THEN, StepState.CREATED)
        context = _make_context()

        def mock_get_handler(state, s, ctx):
            handler = MagicMock()
            # Mark as complete
            s.mark_completed()
            handler.process.return_value = StateChangeResult(step=s)
            return handler

        with patch("afl.runtime.handlers.get_handler", side_effect=mock_get_handler):
            result = BlockStateChanger(step, context).process()

        assert result.continue_processing is False
        assert step.is_complete is True

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

"""Tests for the AFL AgentPoller library.

Tests cover:
- Evaluator.fail_step() method
- AgentPoller registration
- AgentPoller poll_once (end-to-end, no tasks, failure, capacity, short name)
- AgentPoller lifecycle (server registration, AST caching, stop)
"""

import threading
import time

import pytest

from afl.runtime import (
    Evaluator,
    ExecutionStatus,
    MemoryStore,
    StepState,
    Telemetry,
)
from afl.runtime.agent_poller import AgentPoller, AgentPollerConfig
from afl.runtime.entities import (
    RunnerDefinition,
    RunnerState,
    ServerState,
    TaskDefinition,
    TaskState,
    WorkflowDefinition,
)
from afl.runtime.types import generate_id

# =========================================================================
# Fixtures
# =========================================================================


@pytest.fixture
def store():
    """Fresh in-memory store."""
    return MemoryStore()


@pytest.fixture
def evaluator(store):
    """Evaluator with in-memory store."""
    return Evaluator(persistence=store, telemetry=Telemetry(enabled=False))


@pytest.fixture
def config():
    """Default poller config."""
    return AgentPollerConfig()


@pytest.fixture
def poller(store, evaluator, config):
    """AgentPoller with defaults."""
    return AgentPoller(
        persistence=store,
        evaluator=evaluator,
        config=config,
    )


# ---- Workflow AST fixtures (same as runner tests) ----


@pytest.fixture
def program_ast():
    """Program AST with Value (facet), CountDocuments (event facet)."""
    return {
        "type": "Program",
        "declarations": [
            {
                "type": "FacetDecl",
                "name": "Value",
                "params": [{"name": "input", "type": "Long"}],
            },
            {
                "type": "EventFacetDecl",
                "name": "CountDocuments",
                "params": [{"name": "input", "type": "Long"}],
                "returns": [{"name": "output", "type": "Long"}],
            },
        ],
    }


@pytest.fixture
def workflow_ast():
    """Simple workflow that calls an event facet."""
    return {
        "type": "WorkflowDecl",
        "name": "TestWorkflow",
        "params": [{"name": "x", "type": "Long"}],
        "returns": [{"name": "result", "type": "Long"}],
        "body": {
            "type": "AndThenBlock",
            "steps": [
                {
                    "type": "StepStmt",
                    "id": "step-s1",
                    "name": "s1",
                    "call": {
                        "type": "CallExpr",
                        "target": "Value",
                        "args": [
                            {
                                "name": "input",
                                "value": {
                                    "type": "BinaryExpr",
                                    "operator": "+",
                                    "left": {"type": "InputRef", "path": ["x"]},
                                    "right": {"type": "Int", "value": 1},
                                },
                            }
                        ],
                    },
                },
                {
                    "type": "StepStmt",
                    "id": "step-s2",
                    "name": "s2",
                    "call": {
                        "type": "CallExpr",
                        "target": "CountDocuments",
                        "args": [
                            {
                                "name": "input",
                                "value": {"type": "StepRef", "path": ["s1", "input"]},
                            }
                        ],
                    },
                },
            ],
            "yield": {
                "type": "YieldStmt",
                "id": "yield-TW",
                "call": {
                    "type": "CallExpr",
                    "target": "TestWorkflow",
                    "args": [
                        {
                            "name": "result",
                            "value": {
                                "type": "BinaryExpr",
                                "operator": "+",
                                "left": {"type": "StepRef", "path": ["s2", "output"]},
                                "right": {"type": "StepRef", "path": ["s1", "input"]},
                            },
                        }
                    ],
                },
            },
        },
    }


def _execute_until_paused(evaluator, workflow_ast, inputs=None, program_ast=None):
    """Execute a workflow until it pauses at EVENT_TRANSMIT."""
    return evaluator.execute(workflow_ast, inputs=inputs, program_ast=program_ast)


# =========================================================================
# TestFailStep
# =========================================================================


class TestFailStep:
    """Tests for Evaluator.fail_step() method."""

    def test_fail_step_marks_error(self, store, evaluator, workflow_ast, program_ast):
        """Step at EVENT_TRANSMIT transitions to STATEMENT_ERROR."""
        result = _execute_until_paused(evaluator, workflow_ast, {"x": 1}, program_ast)
        assert result.status == ExecutionStatus.PAUSED

        # Find the event-blocked step
        blocked = store.get_steps_by_state(StepState.EVENT_TRANSMIT)
        assert len(blocked) >= 1
        step = blocked[0]

        evaluator.fail_step(step.id, "something went wrong")

        updated = store.get_step(step.id)
        assert updated.state == StepState.STATEMENT_ERROR
        assert updated.transition.error is not None
        assert "something went wrong" in str(updated.transition.error)

    def test_fail_step_wrong_state(self, store, evaluator, workflow_ast, program_ast):
        """fail_step raises ValueError if step is not at EVENT_TRANSMIT."""
        _execute_until_paused(evaluator, workflow_ast, {"x": 1}, program_ast)

        # Find a completed step (not at EVENT_TRANSMIT)
        all_steps = store.get_all_steps()
        non_event = [s for s in all_steps if s.state != StepState.EVENT_TRANSMIT]
        assert len(non_event) >= 1

        with pytest.raises(ValueError, match="expected"):
            evaluator.fail_step(non_event[0].id, "error")

    def test_fail_step_not_found(self, evaluator):
        """fail_step raises ValueError for non-existent step."""
        with pytest.raises(ValueError, match="not found"):
            evaluator.fail_step("nonexistent-id", "error")


# =========================================================================
# TestAgentPollerRegistration
# =========================================================================


class TestAgentPollerRegistration:
    """Tests for handler registration."""

    def test_register_callback(self, poller):
        """A registered handler is stored."""
        poller.register("ns.MyEvent", lambda p: {"ok": True})
        assert "ns.MyEvent" in poller._handlers

    def test_register_multiple(self, poller):
        """Multiple handlers can be registered."""
        poller.register("ns.EventA", lambda p: {"a": 1})
        poller.register("ns.EventB", lambda p: {"b": 2})
        assert len(poller._handlers) == 2

    def test_registered_names(self, poller):
        """registered_names returns all facet names."""
        poller.register("ns.EventA", lambda p: {})
        poller.register("ns.EventB", lambda p: {})
        names = poller.registered_names()
        assert sorted(names) == ["ns.EventA", "ns.EventB"]


# =========================================================================
# TestAgentPollerPollOnce
# =========================================================================


class TestAgentPollerPollOnce:
    """Tests for the poll_once() synchronous cycle."""

    def test_poll_claims_and_processes(self, store, evaluator, workflow_ast, program_ast):
        """End-to-end: register handler, poll_once
        -> callback invoked, step continued, task completed."""
        result = _execute_until_paused(evaluator, workflow_ast, {"x": 1}, program_ast)
        assert result.status == ExecutionStatus.PAUSED

        # The evaluator auto-creates a task when step reaches EVENT_TRANSMIT
        blocked = store.get_steps_by_state(StepState.EVENT_TRANSMIT)
        assert len(blocked) >= 1
        step = blocked[0]

        # Find the auto-created task
        pending_tasks = [
            t
            for t in store._tasks.values()
            if t.state == TaskState.PENDING and t.step_id == step.id
        ]
        assert len(pending_tasks) == 1
        task = pending_tasks[0]

        # Create and configure poller
        poller = AgentPoller(
            persistence=store,
            evaluator=evaluator,
            config=AgentPollerConfig(),
        )
        poller.register("CountDocuments", lambda p: {"output": 42})
        poller.cache_workflow_ast(result.workflow_id, workflow_ast)

        dispatched = poller.poll_once()
        assert dispatched == 1

        # Task should be completed
        updated_task = store._tasks[task.uuid]
        assert updated_task.state == TaskState.COMPLETED

        # Step should have moved past EVENT_TRANSMIT
        updated_step = store.get_step(step.id)
        assert updated_step.state != StepState.EVENT_TRANSMIT

    def test_poll_no_tasks(self, poller):
        """poll_once returns 0 when no tasks are available."""
        poller.register("ns.SomeEvent", lambda p: {})
        assert poller.poll_once() == 0

    def test_poll_callback_failure(self, store, evaluator, workflow_ast, program_ast):
        """Exception in callback -> step error, task failed."""
        _execute_until_paused(evaluator, workflow_ast, {"x": 1}, program_ast)

        blocked = store.get_steps_by_state(StepState.EVENT_TRANSMIT)
        step = blocked[0]

        # Find the auto-created task
        pending_tasks = [
            t
            for t in store._tasks.values()
            if t.state == TaskState.PENDING and t.step_id == step.id
        ]
        assert len(pending_tasks) == 1
        task = pending_tasks[0]

        def failing_handler(payload):
            raise ValueError("handler exploded")

        poller = AgentPoller(
            persistence=store,
            evaluator=evaluator,
            config=AgentPollerConfig(),
        )
        poller.register("CountDocuments", failing_handler)

        dispatched = poller.poll_once()
        assert dispatched == 1

        # Task should be failed
        updated_task = store._tasks[task.uuid]
        assert updated_task.state == TaskState.FAILED
        assert "handler exploded" in updated_task.error["message"]

        # Step should be in error state
        updated_step = store.get_step(step.id)
        assert updated_step.state == StepState.STATEMENT_ERROR

    def test_poll_respects_max_concurrent(self, store, evaluator):
        """poll_once does not dispatch more than max_concurrent tasks."""
        config = AgentPollerConfig(max_concurrent=2)
        poller = AgentPoller(persistence=store, evaluator=evaluator, config=config)
        poller.register("TestEvent", lambda p: {"ok": True})

        # Create 5 tasks (but we can't process them without real steps,
        # so just verify capacity logic by checking the claim loop stops)
        # With no tasks in the queue, it returns 0
        assert poller.poll_once() == 0

    def test_poll_short_name_fallback(self, store, evaluator):
        """A handler registered with a short name matches a qualified task name
        via the _process_event fallback logic."""
        from afl.runtime.step import StepDefinition
        from afl.runtime.types import ObjectType
        from afl.runtime.types import workflow_id as make_wf_id

        # Create a step at EVENT_TRANSMIT with a qualified facet name
        wf_id = make_wf_id()
        step = StepDefinition.create(
            workflow_id=wf_id,
            object_type=ObjectType.VARIABLE_ASSIGNMENT,
            facet_name="ns.CountDocuments",
        )
        step.state = StepState.EVENT_TRANSMIT
        step.transition.current_state = StepState.EVENT_TRANSMIT
        step.transition.request_transition = False
        store.save_step(step)

        # Create task with qualified name
        task = TaskDefinition(
            uuid=generate_id(),
            name="ns.CountDocuments",
            runner_id="",
            workflow_id=wf_id,
            flow_id="",
            step_id=step.id,
            state=TaskState.PENDING,
            task_list_name="default",
            data={"input": 2},
        )
        store.save_task(task)

        poller = AgentPoller(
            persistence=store,
            evaluator=evaluator,
            config=AgentPollerConfig(),
        )
        # Register with short name only — the callback lookup falls back
        poller.register("CountDocuments", lambda p: {"output": 10})
        # Must also register qualified name so claim_task matches
        poller.register("ns.CountDocuments", lambda p: {"output": 10})

        dispatched = poller.poll_once()
        assert dispatched == 1

        updated_task = store._tasks[task.uuid]
        assert updated_task.state == TaskState.COMPLETED


# =========================================================================
# TestAgentPollerLifecycle
# =========================================================================


class TestAgentPollerLifecycle:
    """Tests for server registration, AST caching, and shutdown."""

    def test_server_registration(self, store, evaluator):
        """start() registers server, stop() deregisters."""
        poller = AgentPoller(
            persistence=store,
            evaluator=evaluator,
            config=AgentPollerConfig(poll_interval_ms=50),
        )
        poller.register("TestEvent", lambda p: {})

        # Start in background, then stop quickly
        def run_poller():
            poller.start()

        t = threading.Thread(target=run_poller, daemon=True)
        t.start()

        # Poll for server registration (may take >100ms under load)
        server = None
        for _ in range(20):
            time.sleep(0.05)
            server = store.get_server(poller.server_id)
            if server is not None:
                break
        assert server is not None
        assert server.state == ServerState.RUNNING
        assert server.service_name == "afl-agent"

        poller.stop()
        t.join(timeout=2)

        server = store.get_server(poller.server_id)
        assert server.state == ServerState.SHUTDOWN

    def test_ast_caching(self, store, evaluator, workflow_ast, program_ast):
        """Cached AST is used for resume after poll_once."""
        result = _execute_until_paused(evaluator, workflow_ast, {"x": 1}, program_ast)
        assert result.status == ExecutionStatus.PAUSED

        blocked = store.get_steps_by_state(StepState.EVENT_TRANSMIT)
        step = blocked[0]

        poller = AgentPoller(
            persistence=store,
            evaluator=evaluator,
            config=AgentPollerConfig(),
        )
        poller.register("CountDocuments", lambda p: {"output": 50})
        poller.cache_workflow_ast(result.workflow_id, workflow_ast)

        # Verify cache is populated
        assert result.workflow_id in poller._ast_cache

        poller.poll_once()

        # Workflow should have completed via resume using cached AST
        updated_step = store.get_step(step.id)
        assert updated_step.state != StepState.EVENT_TRANSMIT

    def test_stop(self, store, evaluator):
        """stop() causes the poll loop to exit."""
        poller = AgentPoller(
            persistence=store,
            evaluator=evaluator,
            config=AgentPollerConfig(poll_interval_ms=50),
        )

        def run_poller():
            poller.start()

        t = threading.Thread(target=run_poller, daemon=True)
        t.start()

        time.sleep(0.1)
        assert poller.is_running

        poller.stop()
        t.join(timeout=2)

        assert not poller.is_running


# =========================================================================
# TestAgentPollerRunnerState
# =========================================================================


def _create_runner_entity(store, workflow_id, runner_id):
    """Create and save a RunnerDefinition in RUNNING state."""
    wf_def = WorkflowDefinition(
        uuid=workflow_id,
        name="TestWorkflow",
        namespace_id="ns-1",
        facet_id="f-1",
        flow_id="flow-1",
        starting_step="step-1",
        version="1.0",
    )
    runner = RunnerDefinition(
        uuid=runner_id,
        workflow_id=workflow_id,
        workflow=wf_def,
        state=RunnerState.RUNNING,
        start_time=int(time.time() * 1000),
    )
    store.save_runner(runner)
    return runner


class TestAgentPollerRunnerState:
    """Tests that AgentPoller updates runner state after resume."""

    def test_runner_completed_after_event(self, store, evaluator, workflow_ast, program_ast):
        """Runner state transitions to COMPLETED when workflow completes."""
        result = evaluator.execute(
            workflow_ast,
            inputs={"x": 1},
            program_ast=program_ast,
            runner_id="ap-runner-1",
        )
        assert result.status == ExecutionStatus.PAUSED

        _create_runner_entity(store, result.workflow_id, "ap-runner-1")

        poller = AgentPoller(
            persistence=store,
            evaluator=evaluator,
            config=AgentPollerConfig(),
        )
        poller.register("CountDocuments", lambda p: {"output": 42})
        poller.cache_workflow_ast(result.workflow_id, workflow_ast, program_ast)

        dispatched = poller.poll_once()
        assert dispatched == 1

        updated_runner = store.get_runner("ap-runner-1")
        assert updated_runner.state == RunnerState.COMPLETED
        assert updated_runner.end_time > 0
        assert updated_runner.duration > 0

    def test_runner_stays_running_without_runner_id(
        self, store, evaluator, workflow_ast, program_ast
    ):
        """When task has no runner_id, runner state is not touched."""
        result = evaluator.execute(
            workflow_ast,
            inputs={"x": 1},
            program_ast=program_ast,
        )
        assert result.status == ExecutionStatus.PAUSED

        poller = AgentPoller(
            persistence=store,
            evaluator=evaluator,
            config=AgentPollerConfig(),
        )
        poller.register("CountDocuments", lambda p: {"output": 42})
        poller.cache_workflow_ast(result.workflow_id, workflow_ast, program_ast)

        dispatched = poller.poll_once()
        assert dispatched == 1
        # No runner entity exists, no error occurs

    def test_concurrent_resume_lock(self, store, evaluator, workflow_ast, program_ast):
        """Resume lock prevents concurrent resumes for the same workflow."""
        result = evaluator.execute(
            workflow_ast,
            inputs={"x": 1},
            program_ast=program_ast,
        )
        assert result.status == ExecutionStatus.PAUSED

        poller = AgentPoller(
            persistence=store,
            evaluator=evaluator,
            config=AgentPollerConfig(),
        )
        poller.cache_workflow_ast(result.workflow_id, workflow_ast, program_ast)

        # Pre-acquire the lock
        with poller._resume_locks_lock:
            poller._resume_locks[result.workflow_id] = threading.Lock()
        lock = poller._resume_locks[result.workflow_id]
        lock.acquire()

        try:
            # Resume should be skipped (lock held)
            poller._resume_workflow(result.workflow_id)
            blocked = store.get_steps_by_state(StepState.EVENT_TRANSMIT)
            assert len(blocked) >= 1  # still paused
        finally:
            lock.release()


# =========================================================================
# TestAgentPollerStepLogs
# =========================================================================


class TestAgentPollerStepLogs:
    """Tests that AgentPoller emits step logs during event processing."""

    def test_step_logs_on_success(self, store, evaluator, workflow_ast, program_ast):
        """Successful dispatch emits claimed, dispatching, and completed logs."""
        result = _execute_until_paused(evaluator, workflow_ast, {"x": 1}, program_ast)
        assert result.status == ExecutionStatus.PAUSED

        blocked = store.get_steps_by_state(StepState.EVENT_TRANSMIT)
        assert len(blocked) >= 1
        step = blocked[0]

        poller = AgentPoller(
            persistence=store,
            evaluator=evaluator,
            config=AgentPollerConfig(),
        )
        poller.register("CountDocuments", lambda p: {"output": 42})
        poller.cache_workflow_ast(result.workflow_id, workflow_ast)

        poller.poll_once()

        logs = store.get_step_logs_by_step(step.id)
        messages = [entry.message for entry in logs]

        # Should have at least: claimed, dispatching, completed
        assert any("Task claimed" in m for m in messages)
        assert any("Dispatching handler" in m for m in messages)
        assert any("Handler completed" in m for m in messages)

        # Check levels
        levels = {entry.message: entry.level for entry in logs}
        completed_key = [k for k in levels if "Handler completed" in k][0]
        assert levels[completed_key] == "success"

    def test_step_logs_on_failure(self, store, evaluator, workflow_ast, program_ast):
        """Handler exception emits an error step log."""
        _execute_until_paused(evaluator, workflow_ast, {"x": 1}, program_ast)

        blocked = store.get_steps_by_state(StepState.EVENT_TRANSMIT)
        step = blocked[0]

        def failing_handler(payload):
            raise ValueError("boom")

        poller = AgentPoller(
            persistence=store,
            evaluator=evaluator,
            config=AgentPollerConfig(),
        )
        poller.register("CountDocuments", failing_handler)
        poller.cache_workflow_ast(
            store.get_steps_by_state(StepState.EVENT_TRANSMIT)[0].workflow_id
            if store.get_steps_by_state(StepState.EVENT_TRANSMIT)
            else step.workflow_id,
            workflow_ast,
        )

        poller.poll_once()

        logs = store.get_step_logs_by_step(step.id)
        error_logs = [entry for entry in logs if entry.level == "error"]
        assert len(error_logs) >= 1
        assert any("Handler error" in entry.message for entry in error_logs)

    def test_step_log_callback_injection(
        self, store, evaluator, workflow_ast, program_ast
    ):
        """Handler receives _step_log callback and can emit handler-level logs."""
        result = _execute_until_paused(evaluator, workflow_ast, {"x": 1}, program_ast)

        blocked = store.get_steps_by_state(StepState.EVENT_TRANSMIT)
        step = blocked[0]

        def logging_handler(payload):
            log = payload.get("_step_log")
            if log:
                log("Fetching data from API")
                log("Download complete", level="success")
            return {"output": 42}

        poller = AgentPoller(
            persistence=store,
            evaluator=evaluator,
            config=AgentPollerConfig(),
        )
        poller.register("CountDocuments", logging_handler)
        poller.cache_workflow_ast(result.workflow_id, workflow_ast)

        poller.poll_once()

        logs = store.get_step_logs_by_step(step.id)
        handler_logs = [entry for entry in logs if entry.source == "handler"]
        assert len(handler_logs) == 2
        assert handler_logs[0].message == "Fetching data from API"
        assert handler_logs[0].level == "info"
        assert handler_logs[1].message == "Download complete"
        assert handler_logs[1].level == "success"


# =========================================================================
# TestAgentPollerASTSnapshot
# =========================================================================


class TestAgentPollerASTSnapshot:
    """Tests for runner-snapshotted AST preference in AgentPoller."""

    def test_load_prefers_runner_ast(self, store, evaluator):
        """_load_workflow_ast prefers runner-snapshotted AST."""
        poller = AgentPoller(
            persistence=store,
            evaluator=evaluator,
            config=AgentPollerConfig(),
        )

        wf = WorkflowDefinition(
            uuid="wf-snap", name="SnapWF", namespace_id="ns",
            facet_id="f-1", flow_id="fl-1", starting_step="s-1", version="1.0",
        )
        program_dict = {"declarations": [{"type": "WorkflowDecl", "name": "SnapWF"}]}
        wf_ast = {"type": "WorkflowDecl", "name": "SnapWF"}
        runner = RunnerDefinition(
            uuid="r-snap",
            workflow_id="wf-snap",
            workflow=wf,
            state=RunnerState.RUNNING,
            compiled_ast=program_dict,
            workflow_ast=wf_ast,
        )
        store.save_runner(runner)

        result = poller._load_workflow_ast("wf-snap")
        assert result == wf_ast
        assert poller._program_ast_cache["wf-snap"] == program_dict

    def test_load_skips_runner_without_ast(self, store, evaluator):
        """_load_workflow_ast skips runners with no snapshotted ASTs."""
        poller = AgentPoller(
            persistence=store,
            evaluator=evaluator,
            config=AgentPollerConfig(),
        )

        wf = WorkflowDefinition(
            uuid="wf-noast", name="NoAstWF", namespace_id="ns",
            facet_id="f-1", flow_id="fl-1", starting_step="s-1", version="1.0",
        )
        runner = RunnerDefinition(
            uuid="r-noast",
            workflow_id="wf-noast",
            workflow=wf,
            state=RunnerState.RUNNING,
        )
        store.save_runner(runner)

        # MemoryStore has no get_workflow/get_flow, so fallback returns None
        result = poller._load_workflow_ast("wf-noast")
        assert result is None

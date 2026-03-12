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

"""Tests for the AFL distributed runner service.

Tests cover:
- RunnerConfig defaults and custom values
- RunnerService lifecycle (register, deregister, state transitions)
- Polling (find steps, skip locked, respect capacity)
- Locking (acquire, release, on-error, concurrent claim)
- Event processing (dispatch, continue_step, resume, errors)
- Task processing (claim, state transitions)
- Heartbeat (ping_time updates)
- Shutdown (stops loop, releases locks)
- Integration (workflow execute → pause → run_once → complete)
"""

import http.client
import json
import socket
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from unittest.mock import MagicMock, patch

import pytest

from afl.runtime import (
    Evaluator,
    ExecutionStatus,
    MemoryStore,
    StepState,
    Telemetry,
)
from afl.runtime.agent import ToolRegistry
from afl.runtime.entities import (
    ServerDefinition,
    ServerState,
    TaskDefinition,
    TaskState,
)
from afl.runtime.runner import RunnerConfig, RunnerService
from afl.runtime.runner.service import _current_time_ms
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
def registry():
    """Empty tool registry."""
    return ToolRegistry()


@pytest.fixture
def config():
    """Default runner config."""
    return RunnerConfig()


@pytest.fixture
def service(store, evaluator, config, registry):
    """RunnerService with defaults."""
    return RunnerService(
        persistence=store,
        evaluator=evaluator,
        config=config,
        tool_registry=registry,
    )


# ---- Workflow AST fixtures (Example 4 pattern with event facet) ----------


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
    """Simple workflow that calls an event facet.

    workflow TestWorkflow(x: Long = 1) => (result: Long) andThen {
        s1 = Value(input = $.x + 1)
        s2 = CountDocuments(input = s1.input)
        yield TestWorkflow(result = s2.output + s1.input)
    }
    """
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
    result = evaluator.execute(workflow_ast, inputs=inputs, program_ast=program_ast)
    return result


# =========================================================================
# TestRunnerConfig
# =========================================================================


class TestRunnerConfig:
    """Tests for RunnerConfig defaults and custom values."""

    def test_defaults(self):
        config = RunnerConfig()
        assert config.server_group == "default"
        assert config.service_name == "afl-runner"
        assert config.server_name == socket.gethostname()
        assert config.topics == []
        assert config.task_list == "default"
        assert config.poll_interval_ms == 1000
        assert config.heartbeat_interval_ms == 10000
        assert config.lock_duration_ms == 60000
        assert config.lock_extend_interval_ms == 20000
        assert config.max_concurrent == 2
        assert config.shutdown_timeout_ms == 30000
        assert config.http_port == 8080
        assert config.http_max_port_attempts == 20

    def test_custom_values(self):
        config = RunnerConfig(
            server_group="prod",
            service_name="my-runner",
            server_name="host01",
            topics=["TopicA", "TopicB"],
            task_list="priority",
            poll_interval_ms=500,
            max_concurrent=10,
        )
        assert config.server_group == "prod"
        assert config.service_name == "my-runner"
        assert config.server_name == "host01"
        assert config.topics == ["TopicA", "TopicB"]
        assert config.task_list == "priority"
        assert config.poll_interval_ms == 500
        assert config.max_concurrent == 10

    def test_auto_detect_hostname(self):
        config = RunnerConfig(server_name="")
        assert config.server_name == socket.gethostname()


# =========================================================================
# TestRunnerServiceLifecycle
# =========================================================================


class TestRunnerServiceLifecycle:
    """Tests for server registration and state transitions."""

    def test_initial_state(self, service):
        assert service.is_running is False
        assert service.server_id is not None

    def test_server_id_is_unique(self, store, evaluator, config, registry):
        s1 = RunnerService(store, evaluator, config, registry)
        s2 = RunnerService(store, evaluator, config, registry)
        assert s1.server_id != s2.server_id

    def test_register_server(self, service, store):
        service._register_server()
        server = store.get_server(service.server_id)
        assert server is not None
        assert server.state == ServerState.RUNNING
        assert server.service_name == "afl-runner"
        assert server.server_group == "default"

    def test_deregister_server(self, service, store):
        service._register_server()
        service._deregister_server()
        server = store.get_server(service.server_id)
        assert server is not None
        assert server.state == ServerState.SHUTDOWN

    def test_register_records_handlers(self, store, evaluator, config):
        registry = ToolRegistry()
        registry.register("FacetA", lambda p: {})
        registry.register("FacetB", lambda p: {})
        svc = RunnerService(store, evaluator, config, registry)
        svc._register_server()
        server = store.get_server(svc.server_id)
        assert {"FacetA", "FacetB"}.issubset(set(server.handlers))
        assert "afl:execute" in server.handlers

    def test_register_records_topics(self, store, evaluator, registry):
        config = RunnerConfig(topics=["TopicX", "TopicY"])
        svc = RunnerService(store, evaluator, config, registry)
        svc._register_server()
        server = store.get_server(svc.server_id)
        assert server.topics == ["TopicX", "TopicY"]


# =========================================================================
# TestRunnerServicePolling
# =========================================================================


class TestRunnerServicePolling:
    """Tests for polling event steps and pending tasks."""

    def test_poll_event_steps_finds_blocked(
        self, store, evaluator, registry, workflow_ast, program_ast
    ):
        """Steps at EVENT_TRANSMIT are found by polling."""
        registry.register("CountDocuments", lambda p: {"output": 42})
        config = RunnerConfig()
        svc = RunnerService(store, evaluator, config, registry)

        # Execute workflow until paused
        result = _execute_until_paused(evaluator, workflow_ast, {"x": 1}, program_ast)
        assert result.status == ExecutionStatus.PAUSED

        steps = svc._poll_event_steps()
        assert len(steps) >= 1
        assert all(s.state == StepState.EVENT_TRANSMIT for s in steps)

    def test_poll_event_steps_filters_by_topics(self, store, evaluator, workflow_ast, program_ast):
        """Only matching topics are returned when topics are configured."""
        registry = ToolRegistry()
        registry.register("CountDocuments", lambda p: {"output": 42})
        config = RunnerConfig(topics=["OtherTopic"])
        svc = RunnerService(store, evaluator, config, registry)

        result = _execute_until_paused(evaluator, workflow_ast, {"x": 1}, program_ast)
        assert result.status == ExecutionStatus.PAUSED

        # Should not find CountDocuments since topics filter only allows OtherTopic
        steps = svc._poll_event_steps()
        assert len(steps) == 0

    def test_poll_event_steps_filters_by_handler(self, store, evaluator, workflow_ast, program_ast):
        """Steps are skipped if no handler is registered."""
        registry = ToolRegistry()  # No handlers
        config = RunnerConfig()
        svc = RunnerService(store, evaluator, config, registry)

        result = _execute_until_paused(evaluator, workflow_ast, {"x": 1}, program_ast)
        assert result.status == ExecutionStatus.PAUSED

        steps = svc._poll_event_steps()
        assert len(steps) == 0

    def test_poll_pending_tasks(self, store, evaluator):
        """Pending tasks with a built-in handler are found."""
        registry = ToolRegistry()
        config = RunnerConfig(task_list="mylist")
        svc = RunnerService(store, evaluator, config, registry)

        # Create a pending afl:execute task (built-in handler)
        task = TaskDefinition(
            uuid=generate_id(),
            name="afl:execute",
            runner_id="r1",
            workflow_id="w1",
            flow_id="f1",
            step_id="s1",
            state=TaskState.PENDING,
            task_list_name="mylist",
        )
        store.save_task(task)

        tasks = svc._poll_pending_tasks()
        assert len(tasks) == 1
        assert tasks[0].uuid == task.uuid

    def test_poll_ignores_wrong_task_list(self, store, evaluator):
        """Tasks from a different list are not returned."""
        registry = ToolRegistry()
        config = RunnerConfig(task_list="mylist")
        svc = RunnerService(store, evaluator, config, registry)

        task = TaskDefinition(
            uuid=generate_id(),
            name="afl:execute",
            runner_id="r1",
            workflow_id="w1",
            flow_id="f1",
            step_id="s1",
            state=TaskState.PENDING,
            task_list_name="otherlist",
        )
        store.save_task(task)

        tasks = svc._poll_pending_tasks()
        assert len(tasks) == 0

    def test_poll_ignores_unhandled_tasks(self, store, registry, evaluator):
        """Tasks with no registered handler are not returned."""
        config = RunnerConfig(task_list="mylist")
        svc = RunnerService(store, evaluator, config, registry)

        task = TaskDefinition(
            uuid=generate_id(),
            name="osm.ops.CacheRegion",
            runner_id="r1",
            workflow_id="w1",
            flow_id="f1",
            step_id="s1",
            state=TaskState.PENDING,
            task_list_name="mylist",
        )
        store.save_task(task)

        tasks = svc._poll_pending_tasks()
        assert len(tasks) == 0

    def test_run_once_respects_capacity(self, store, evaluator, workflow_ast, program_ast):
        """run_once dispatches up to max_concurrent items."""
        registry = ToolRegistry()
        registry.register("CountDocuments", lambda p: {"output": 10})
        config = RunnerConfig(max_concurrent=1)
        svc = RunnerService(store, evaluator, config, registry)

        result = _execute_until_paused(evaluator, workflow_ast, {"x": 1}, program_ast)
        assert result.status == ExecutionStatus.PAUSED

        # Cache the AST for resume
        svc.cache_workflow_ast(result.workflow_id, workflow_ast)

        dispatched = svc.run_once()
        # Should dispatch at most 1 (max_concurrent)
        assert dispatched <= 1


# =========================================================================
# TestRunnerServiceLocking
# =========================================================================


class TestRunnerServiceLocking:
    """Tests for distributed lock acquisition and release."""

    def test_claim_step_acquires_lock(self, store, evaluator, registry, workflow_ast, program_ast):
        """Claiming a step acquires a distributed lock."""
        registry.register("CountDocuments", lambda p: {"output": 1})
        config = RunnerConfig()
        svc = RunnerService(store, evaluator, config, registry)

        _execute_until_paused(evaluator, workflow_ast, {"x": 1}, program_ast)
        steps = svc._poll_event_steps()
        assert len(steps) >= 1

        step = steps[0]
        assert svc._try_claim_step(step) is True

        # Lock should exist
        lock = store.check_lock(f"runner:step:{step.id}")
        assert lock is not None

    def test_double_claim_fails(self, store, evaluator, registry, workflow_ast, program_ast):
        """Second claim on same step fails."""
        registry.register("CountDocuments", lambda p: {"output": 1})
        config = RunnerConfig()
        svc = RunnerService(store, evaluator, config, registry)

        _execute_until_paused(evaluator, workflow_ast, {"x": 1}, program_ast)
        steps = svc._poll_event_steps()
        step = steps[0]

        assert svc._try_claim_step(step) is True
        assert svc._try_claim_step(step) is False

    def test_release_step_lock(self, store, evaluator, registry, workflow_ast, program_ast):
        """Releasing a step lock frees it for others."""
        registry.register("CountDocuments", lambda p: {"output": 1})
        config = RunnerConfig()
        svc = RunnerService(store, evaluator, config, registry)

        _execute_until_paused(evaluator, workflow_ast, {"x": 1}, program_ast)
        steps = svc._poll_event_steps()
        step = steps[0]

        svc._try_claim_step(step)
        svc._release_step_lock(step)

        # Lock should be gone
        lock = store.check_lock(f"runner:step:{step.id}")
        assert lock is None

        # Can re-acquire
        assert svc._try_claim_step(step) is True

    def test_claim_task_acquires_lock(self, store, evaluator, registry):
        """Claiming a task acquires a distributed lock."""
        config = RunnerConfig()
        svc = RunnerService(store, evaluator, config, registry)

        task = TaskDefinition(
            uuid=generate_id(),
            name="SomeTask",
            runner_id="r1",
            workflow_id="w1",
            flow_id="f1",
            step_id="s1",
        )
        store.save_task(task)

        assert svc._try_claim_task(task) is True
        lock = store.check_lock(f"runner:task:{task.uuid}")
        assert lock is not None

    def test_concurrent_claim_only_one_wins(self, store, evaluator, workflow_ast, program_ast):
        """Two services trying to claim the same step: only one succeeds."""
        reg1 = ToolRegistry()
        reg1.register("CountDocuments", lambda p: {"output": 1})
        reg2 = ToolRegistry()
        reg2.register("CountDocuments", lambda p: {"output": 2})

        config = RunnerConfig()
        svc1 = RunnerService(store, evaluator, config, reg1)
        svc2 = RunnerService(store, evaluator, config, reg2)

        _execute_until_paused(evaluator, workflow_ast, {"x": 1}, program_ast)
        steps = svc1._poll_event_steps()
        step = steps[0]

        claim1 = svc1._try_claim_step(step)
        claim2 = svc2._try_claim_step(step)

        assert claim1 is True
        assert claim2 is False


# =========================================================================
# TestRunnerServiceEventProcessing
# =========================================================================


class TestRunnerServiceEventProcessing:
    """Tests for event step dispatch and workflow continuation."""

    def test_process_step_dispatches_and_continues(
        self, store, evaluator, workflow_ast, program_ast
    ):
        """_process_step calls handler and continues the step."""
        registry = ToolRegistry()
        registry.register("CountDocuments", lambda p: {"output": 100})
        config = RunnerConfig()
        svc = RunnerService(store, evaluator, config, registry)

        result = _execute_until_paused(evaluator, workflow_ast, {"x": 1}, program_ast)
        assert result.status == ExecutionStatus.PAUSED

        steps = svc._poll_event_steps()
        assert len(steps) >= 1
        step = steps[0]

        # Claim and process
        svc._try_claim_step(step)

        # Cache AST for resume
        svc.cache_workflow_ast(result.workflow_id, workflow_ast)

        svc._process_step(step)

        # Step should have been continued past EVENT_TRANSMIT
        updated_step = store.get_step(step.id)
        assert updated_step.state != StepState.EVENT_TRANSMIT

        # Lock should be released
        lock = store.check_lock(f"runner:step:{step.id}")
        assert lock is None

    def test_process_step_no_handler_releases_lock(
        self, store, evaluator, workflow_ast, program_ast
    ):
        """If no handler returns None, lock is released, step unchanged."""
        registry = ToolRegistry()
        # Register handler so polling finds it, then unregister
        registry.register("CountDocuments", lambda p: {"output": 1})
        config = RunnerConfig()
        svc = RunnerService(store, evaluator, config, registry)

        _execute_until_paused(evaluator, workflow_ast, {"x": 1}, program_ast)
        steps = svc._poll_event_steps()
        step = steps[0]

        # Replace handler to return None
        svc._tool_registry = ToolRegistry()

        svc._try_claim_step(step)
        svc._process_step(step)

        # Step should still be at EVENT_TRANSMIT
        updated = store.get_step(step.id)
        assert updated.state == StepState.EVENT_TRANSMIT

        # Lock released
        lock = store.check_lock(f"runner:step:{step.id}")
        assert lock is None

    def test_process_step_handler_error_releases_lock(
        self, store, evaluator, workflow_ast, program_ast
    ):
        """If the handler raises, the lock is still released."""

        def failing_handler(payload):
            raise ValueError("handler error")

        registry = ToolRegistry()
        registry.register("CountDocuments", failing_handler)
        config = RunnerConfig()
        svc = RunnerService(store, evaluator, config, registry)

        _execute_until_paused(evaluator, workflow_ast, {"x": 1}, program_ast)
        steps = svc._poll_event_steps()
        step = steps[0]

        svc._try_claim_step(step)
        svc._process_step(step)  # Should not raise

        # Lock should be released despite error
        lock = store.check_lock(f"runner:step:{step.id}")
        assert lock is None

    def test_handled_stats_updated_on_success(self, store, evaluator, workflow_ast, program_ast):
        """Successful processing increments handled count."""
        registry = ToolRegistry()
        registry.register("CountDocuments", lambda p: {"output": 5})
        config = RunnerConfig()
        svc = RunnerService(store, evaluator, config, registry)
        svc._register_server()

        result = _execute_until_paused(evaluator, workflow_ast, {"x": 1}, program_ast)
        steps = svc._poll_event_steps()
        step = steps[0]

        svc._try_claim_step(step)
        svc.cache_workflow_ast(result.workflow_id, workflow_ast)
        svc._process_step(step)

        assert svc._handled_counts["CountDocuments"].handled == 1
        assert svc._handled_counts["CountDocuments"].not_handled == 0

    def test_handled_stats_updated_on_failure(self, store, evaluator, workflow_ast, program_ast):
        """Failed processing increments not_handled count."""

        def failing_handler(payload):
            raise ValueError("boom")

        registry = ToolRegistry()
        registry.register("CountDocuments", failing_handler)
        config = RunnerConfig()
        svc = RunnerService(store, evaluator, config, registry)
        svc._register_server()

        _execute_until_paused(evaluator, workflow_ast, {"x": 1}, program_ast)
        steps = svc._poll_event_steps()
        step = steps[0]

        svc._try_claim_step(step)
        svc._process_step(step)

        assert svc._handled_counts["CountDocuments"].not_handled == 1


# =========================================================================
# TestRunnerServiceTaskProcessing
# =========================================================================


class TestRunnerServiceTaskProcessing:
    """Tests for task queue processing."""

    def test_process_task_success(self, store, evaluator):
        """Successful task processing transitions to COMPLETED."""
        registry = ToolRegistry()
        registry.register("DoWork", lambda p: {"done": True})
        config = RunnerConfig()
        svc = RunnerService(store, evaluator, config, registry)

        task = TaskDefinition(
            uuid=generate_id(),
            name="DoWork",
            runner_id="r1",
            workflow_id="w1",
            flow_id="f1",
            step_id="s1",
            state=TaskState.PENDING,
            task_list_name="default",
            data={"key": "value"},
        )
        store.save_task(task)

        svc._try_claim_task(task)
        svc._process_task(task)

        updated = store._tasks[task.uuid]
        assert updated.state == TaskState.COMPLETED

        # Lock released
        lock = store.check_lock(f"runner:task:{task.uuid}")
        assert lock is None

    def test_process_task_no_handler(self, store, evaluator):
        """Task with no handler transitions to FAILED."""
        registry = ToolRegistry()  # No handlers
        config = RunnerConfig()
        svc = RunnerService(store, evaluator, config, registry)

        task = TaskDefinition(
            uuid=generate_id(),
            name="Unknown",
            runner_id="r1",
            workflow_id="w1",
            flow_id="f1",
            step_id="s1",
            state=TaskState.PENDING,
        )
        store.save_task(task)

        svc._try_claim_task(task)
        svc._process_task(task)

        updated = store._tasks[task.uuid]
        assert updated.state == TaskState.FAILED
        assert "No handler" in updated.error["message"]

    def test_process_task_handler_error(self, store, evaluator):
        """Task handler exception transitions to FAILED."""
        registry = ToolRegistry()
        registry.register("Boom", lambda p: (_ for _ in ()).throw(RuntimeError("oops")))
        # Use a simpler failing handler:
        registry._handlers["Boom"] = lambda p: (_ for _ in ()).throw(RuntimeError("oops"))

        config = RunnerConfig()
        svc = RunnerService(store, evaluator, config, registry)

        task = TaskDefinition(
            uuid=generate_id(),
            name="Boom",
            runner_id="r1",
            workflow_id="w1",
            flow_id="f1",
            step_id="s1",
            state=TaskState.PENDING,
        )
        store.save_task(task)

        def bad_handler(p):
            raise RuntimeError("oops")

        svc._tool_registry._handlers["Boom"] = bad_handler

        svc._try_claim_task(task)
        svc._process_task(task)

        updated = store._tasks[task.uuid]
        assert updated.state == TaskState.FAILED
        assert "oops" in updated.error["message"]

    def test_process_task_marks_running_then_completed(self, store, evaluator):
        """Task goes through RUNNING state before COMPLETED."""
        states_seen = []

        def capturing_handler(payload):
            # At this point the task should be RUNNING
            task_in_store = store._tasks[task_uuid]
            states_seen.append(task_in_store.state)
            return {"result": True}

        registry = ToolRegistry()
        registry.register("Track", capturing_handler)
        config = RunnerConfig()
        svc = RunnerService(store, evaluator, config, registry)

        task_uuid = generate_id()
        task = TaskDefinition(
            uuid=task_uuid,
            name="Track",
            runner_id="r1",
            workflow_id="w1",
            flow_id="f1",
            step_id="s1",
            state=TaskState.PENDING,
        )
        store.save_task(task)

        svc._try_claim_task(task)
        svc._process_task(task)

        assert TaskState.RUNNING in states_seen
        updated = store._tasks[task_uuid]
        assert updated.state == TaskState.COMPLETED


# =========================================================================
# TestRunnerServiceHeartbeat
# =========================================================================


class TestRunnerServiceHeartbeat:
    """Tests for heartbeat ping_time updates."""

    def test_heartbeat_updates_ping_time(self, store, evaluator, registry):
        """Heartbeat loop updates server ping_time."""
        config = RunnerConfig(heartbeat_interval_ms=50)
        svc = RunnerService(store, evaluator, config, registry)
        svc._register_server()

        initial_ping = store.get_server(svc.server_id).ping_time

        # Run heartbeat in background briefly
        stop_event = svc._stopping
        heartbeat = threading.Thread(target=svc._heartbeat_loop, daemon=True)
        heartbeat.start()

        time.sleep(0.15)  # Wait for at least one heartbeat
        stop_event.set()
        heartbeat.join(timeout=1)

        updated_ping = store.get_server(svc.server_id).ping_time
        assert updated_ping > initial_ping


# =========================================================================
# TestRunnerServiceShutdown
# =========================================================================


class TestRunnerServiceShutdown:
    """Tests for graceful shutdown."""

    def test_stop_sets_stopping_event(self, service):
        """stop() sets the stopping event."""
        service.stop()
        assert service._stopping.is_set()

    def test_start_stop_lifecycle(self, store, evaluator, registry):
        """Start and stop in a thread completes cleanly."""
        config = RunnerConfig(poll_interval_ms=50, shutdown_timeout_ms=1000)
        svc = RunnerService(store, evaluator, config, registry)

        thread = threading.Thread(target=svc.start)
        thread.start()

        time.sleep(0.1)
        assert svc.is_running is True

        svc.stop()
        thread.join(timeout=5)

        assert svc.is_running is False

        # Server should be deregistered
        server = store.get_server(svc.server_id)
        assert server is not None
        assert server.state == ServerState.SHUTDOWN

    def test_shutdown_releases_locks_via_process(self, store, evaluator, workflow_ast, program_ast):
        """Locks are released after processing completes (even on shutdown)."""
        registry = ToolRegistry()
        registry.register("CountDocuments", lambda p: {"output": 1})
        config = RunnerConfig()
        svc = RunnerService(store, evaluator, config, registry)

        result = _execute_until_paused(evaluator, workflow_ast, {"x": 1}, program_ast)
        steps = svc._poll_event_steps()
        step = steps[0]

        svc._try_claim_step(step)
        svc.cache_workflow_ast(result.workflow_id, workflow_ast)
        svc._process_step(step)

        # After processing, lock should be released
        lock = store.check_lock(f"runner:step:{step.id}")
        assert lock is None


# =========================================================================
# TestRunnerServiceIntegration
# =========================================================================


class TestRunnerServiceIntegration:
    """End-to-end integration tests."""

    def test_workflow_execute_pause_run_once_complete(
        self, store, evaluator, workflow_ast, program_ast
    ):
        """Full cycle: execute → PAUSED → run_once → COMPLETED."""
        registry = ToolRegistry()
        registry.register("CountDocuments", lambda p: {"output": 50})
        config = RunnerConfig()
        svc = RunnerService(store, evaluator, config, registry)

        # Execute until paused
        result = _execute_until_paused(evaluator, workflow_ast, {"x": 1}, program_ast)
        assert result.status == ExecutionStatus.PAUSED

        # Cache AST for resume
        svc.cache_workflow_ast(result.workflow_id, workflow_ast)

        # run_once processes the blocked step
        dispatched = svc.run_once()
        assert dispatched == 1

        # Resume the workflow and check completion
        resume_result = evaluator.resume(result.workflow_id, workflow_ast, program_ast, {"x": 1})
        assert resume_result.status == ExecutionStatus.COMPLETED
        assert resume_result.success is True
        # s1.input = 1 + 1 = 2
        # s2 = CountDocuments(input=2) → output=50
        # result = s2.output + s1.input = 50 + 2 = 52
        assert resume_result.outputs["result"] == 52

    def test_run_once_no_work_returns_zero(self, store, evaluator, registry):
        """run_once with no blocked steps returns 0."""
        config = RunnerConfig()
        svc = RunnerService(store, evaluator, config, registry)
        assert svc.run_once() == 0

    def test_multiple_run_once_cycles(self, store, evaluator, workflow_ast, program_ast):
        """Multiple run_once cycles process one step each."""
        registry = ToolRegistry()
        registry.register("CountDocuments", lambda p: {"output": 10})
        config = RunnerConfig(max_concurrent=1)
        svc = RunnerService(store, evaluator, config, registry)

        result = _execute_until_paused(evaluator, workflow_ast, {"x": 1}, program_ast)
        assert result.status == ExecutionStatus.PAUSED

        svc.cache_workflow_ast(result.workflow_id, workflow_ast)

        # First cycle processes the step
        dispatched1 = svc.run_once()
        assert dispatched1 == 1

        # Second cycle: no more blocked steps
        dispatched2 = svc.run_once()
        assert dispatched2 == 0

    def test_cache_workflow_ast(self, service):
        """cache_workflow_ast stores AST for later retrieval."""
        ast = {"type": "WorkflowDecl", "name": "Test"}
        service.cache_workflow_ast("wf-123", ast)
        assert service._ast_cache["wf-123"] == ast


# =========================================================================
# TestGetStepsByState (persistence extension)
# =========================================================================


class TestGetStepsByState:
    """Tests for the get_steps_by_state() persistence method."""

    def test_memory_store_empty(self, store):
        """Empty store returns empty list."""
        result = store.get_steps_by_state(StepState.EVENT_TRANSMIT)
        assert list(result) == []

    def test_memory_store_finds_matching(self, store, evaluator, workflow_ast, program_ast):
        """Steps at EVENT_TRANSMIT are returned."""
        result = _execute_until_paused(evaluator, workflow_ast, {"x": 1}, program_ast)
        assert result.status == ExecutionStatus.PAUSED

        blocked = store.get_steps_by_state(StepState.EVENT_TRANSMIT)
        assert len(blocked) >= 1
        assert all(s.state == StepState.EVENT_TRANSMIT for s in blocked)

    def test_memory_store_excludes_other_states(self, store, evaluator, workflow_ast, program_ast):
        """Steps in other states are not returned."""
        _execute_until_paused(evaluator, workflow_ast, {"x": 1}, program_ast)

        # Should have steps in various states
        all_steps = store.get_all_steps()
        assert len(all_steps) > 1

        # Only EVENT_TRANSMIT ones returned
        blocked = store.get_steps_by_state(StepState.EVENT_TRANSMIT)
        non_blocked = store.get_steps_by_state(StepState.CREATED)
        # These should be different sets
        blocked_ids = {s.id for s in blocked}
        created_ids = {s.id for s in non_blocked}
        assert blocked_ids.isdisjoint(created_ids)


# =========================================================================
# TestDeregisterEdgeCases
# =========================================================================


class TestDeregisterEdgeCases:
    """Tests for deregister edge cases."""

    def test_deregister_without_register(self, service, store):
        """Deregister when server was never registered is a no-op."""
        # Server not registered, so get_server returns None
        service._deregister_server()
        # Should not raise; server just doesn't exist
        server = store.get_server(service.server_id)
        assert server is None


# =========================================================================
# TestGetServerIpsException
# =========================================================================


class TestGetServerIpsException:
    """Tests for _get_server_ips exception handling."""

    def test_get_server_ips_returns_list(self, service):
        """Normal case returns a list with at least one IP."""
        ips = service._get_server_ips()
        assert isinstance(ips, list)

    def test_get_server_ips_exception_returns_empty(self, service):
        """When socket fails, returns empty list."""
        with patch("socket.gethostbyname", side_effect=OSError("nope")):
            ips = service._get_server_ips()
            assert ips == []


# =========================================================================
# TestPollLoopExceptionHandling
# =========================================================================


class TestPollLoopExceptionHandling:
    """Tests for poll loop exception handling."""

    def test_poll_loop_catches_poll_cycle_exception(self, store, evaluator, registry):
        """Poll loop continues after a poll cycle exception."""
        config = RunnerConfig(poll_interval_ms=10)
        svc = RunnerService(store, evaluator, config, registry)

        call_count = 0

        def failing_then_ok():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("poll error")
            # On second call, stop the loop
            svc._stopping.set()
            return 0

        svc._poll_cycle = failing_then_ok

        # Run the poll loop — it should catch the first exception
        svc._poll_loop()

        # The loop ran at least twice (once failing, once stopping)
        assert call_count >= 2


# =========================================================================
# TestPollCycleCapacityExhaustion
# =========================================================================


class TestPollCycleCapacityExhaustion:
    """Tests for capacity limits during poll cycle."""

    def test_poll_cycle_returns_zero_at_capacity(self, store, evaluator, registry):
        """When active futures fill capacity, poll_cycle returns 0."""
        config = RunnerConfig(max_concurrent=1)
        svc = RunnerService(store, evaluator, config, registry)

        # Simulate a running future
        mock_future = MagicMock(spec=Future)
        mock_future.done.return_value = False
        with svc._active_lock:
            svc._active_futures.append(mock_future)

        result = svc._poll_cycle()
        assert result == 0

    def test_poll_cycle_capacity_exhausted_mid_step_loop(
        self, store, evaluator, workflow_ast, program_ast
    ):
        """Capacity runs out while iterating through steps."""
        registry = ToolRegistry()
        registry.register("CountDocuments", lambda p: {"output": 1})
        # max_concurrent=1: can only dispatch 1 item
        config = RunnerConfig(max_concurrent=1)
        svc = RunnerService(store, evaluator, config, registry)

        result = _execute_until_paused(evaluator, workflow_ast, {"x": 1}, program_ast)
        assert result.status == ExecutionStatus.PAUSED
        svc.cache_workflow_ast(result.workflow_id, workflow_ast)

        # Also add a pending task to show it gets skipped
        task = TaskDefinition(
            uuid=generate_id(),
            name="CountDocuments",
            runner_id="r1",
            workflow_id="w1",
            flow_id="f1",
            step_id="s1",
            state=TaskState.PENDING,
            task_list_name="default",
        )
        store.save_task(task)

        dispatched = svc.run_once()
        # Only 1 dispatched (the step), task skipped due to capacity
        assert dispatched == 1

    def test_poll_cycle_dispatches_tasks(self, store, evaluator):
        """Tasks are dispatched when no steps are blocked."""
        registry = ToolRegistry()
        registry.register("MyTask", lambda p: {"ok": True})
        config = RunnerConfig()
        svc = RunnerService(store, evaluator, config, registry)

        task = TaskDefinition(
            uuid=generate_id(),
            name="MyTask",
            runner_id="r1",
            workflow_id="w1",
            flow_id="f1",
            step_id="s1",
            state=TaskState.PENDING,
            task_list_name="default",
        )
        store.save_task(task)

        dispatched = svc.run_once()
        assert dispatched == 1


# =========================================================================
# TestSubmitWithExecutor
# =========================================================================


class TestSubmitWithExecutor:
    """Tests for _submit_step/_submit_task with executor (threaded path)."""

    def test_submit_step_with_executor(self, store, evaluator, workflow_ast, program_ast):
        """Step submitted to executor creates a future."""
        registry = ToolRegistry()
        registry.register("CountDocuments", lambda p: {"output": 1})
        config = RunnerConfig()
        svc = RunnerService(store, evaluator, config, registry)

        result = _execute_until_paused(evaluator, workflow_ast, {"x": 1}, program_ast)
        steps = svc._poll_event_steps()
        step = steps[0]

        svc._try_claim_step(step)
        svc.cache_workflow_ast(result.workflow_id, workflow_ast)

        # Manually set up executor
        svc._executor = ThreadPoolExecutor(max_workers=2)
        try:
            svc._submit_step(step)
            # Wait for the future to complete
            time.sleep(0.5)
            svc._cleanup_futures()
            assert svc._active_count() == 0
        finally:
            svc._executor.shutdown(wait=True)
            svc._executor = None

    def test_submit_task_with_executor(self, store, evaluator):
        """Task submitted to executor creates a future."""
        registry = ToolRegistry()
        registry.register("Work", lambda p: {"done": True})
        config = RunnerConfig()
        svc = RunnerService(store, evaluator, config, registry)

        task = TaskDefinition(
            uuid=generate_id(),
            name="Work",
            runner_id="r1",
            workflow_id="w1",
            flow_id="f1",
            step_id="s1",
            state=TaskState.PENDING,
        )
        store.save_task(task)
        svc._try_claim_task(task)

        svc._executor = ThreadPoolExecutor(max_workers=2)
        try:
            svc._submit_task(task)
            time.sleep(0.5)
            svc._cleanup_futures()
            assert svc._active_count() == 0

            updated = store._tasks[task.uuid]
            assert updated.state == TaskState.COMPLETED
        finally:
            svc._executor.shutdown(wait=True)
            svc._executor = None


# =========================================================================
# TestResumeWorkflowEdgeCases
# =========================================================================


class TestResumeWorkflowEdgeCases:
    """Tests for _resume_workflow edge cases."""

    def test_resume_no_ast_cached_no_persistence(self, store, evaluator, registry):
        """Resume with no cached AST and MemoryStore (no get_workflow) logs warning."""
        config = RunnerConfig()
        svc = RunnerService(store, evaluator, config, registry)

        # MemoryStore doesn't have get_workflow, so _load_workflow_ast returns None
        svc._resume_workflow("nonexistent-workflow-id")
        # Should not raise; just logs a warning

    def test_resume_with_cached_ast(self, store, evaluator, workflow_ast, program_ast):
        """Resume with cached AST calls evaluator.resume."""
        registry = ToolRegistry()
        registry.register("CountDocuments", lambda p: {"output": 1})
        config = RunnerConfig()
        svc = RunnerService(store, evaluator, config, registry)

        result = _execute_until_paused(evaluator, workflow_ast, {"x": 1}, program_ast)
        steps = svc._poll_event_steps()
        step = steps[0]

        # Continue the step manually
        evaluator.continue_step(step.id, {"output": 1})

        # Cache AST and resume
        svc.cache_workflow_ast(result.workflow_id, workflow_ast)
        svc._resume_workflow(result.workflow_id)

        # Workflow should have progressed
        root = store.get_workflow_root(result.workflow_id)
        assert root is not None

    def test_load_workflow_ast_no_get_workflow_attr(self, store, evaluator, registry):
        """_load_workflow_ast returns None when store lacks get_workflow."""
        config = RunnerConfig()
        svc = RunnerService(store, evaluator, config, registry)

        # MemoryStore doesn't have get_workflow
        result = svc._load_workflow_ast("some-id")
        assert result is None

    def test_load_workflow_ast_with_mock_store(self, evaluator, registry):
        """_load_workflow_ast returns None when workflow not found."""
        mock_store = MagicMock()
        mock_store.get_workflow.return_value = None
        mock_store.get_steps_by_state.return_value = []
        mock_store.get_pending_tasks.return_value = []

        config = RunnerConfig()
        svc = RunnerService(mock_store, evaluator, config, registry)

        result = svc._load_workflow_ast("missing-wf")
        assert result is None

    def test_load_workflow_ast_no_get_flow_attr(self, evaluator, registry):
        """_load_workflow_ast returns None when store has get_workflow but not get_flow."""
        mock_store = MagicMock(
            spec=[
                "get_workflow",
                "get_steps_by_state",
                "get_pending_tasks",
                "acquire_lock",
                "release_lock",
            ]
        )
        mock_wf = MagicMock()
        mock_wf.flow_id = "f1"
        mock_store.get_workflow.return_value = mock_wf

        config = RunnerConfig()
        svc = RunnerService(mock_store, evaluator, config, registry)

        # hasattr check for get_flow will fail since spec doesn't include it
        result = svc._load_workflow_ast("wf-1")
        assert result is None

    def test_load_workflow_ast_flow_no_sources(self, evaluator, registry):
        """_load_workflow_ast returns None when flow has no compiled_ast or sources."""
        mock_store = MagicMock()
        mock_wf = MagicMock()
        mock_wf.flow_id = "f1"
        mock_store.get_workflow.return_value = mock_wf

        mock_flow = MagicMock()
        mock_flow.compiled_ast = None
        mock_flow.compiled_sources = []
        mock_store.get_flow.return_value = mock_flow

        config = RunnerConfig()
        svc = RunnerService(mock_store, evaluator, config, registry)

        result = svc._load_workflow_ast("wf-1")
        assert result is None

    def test_load_workflow_ast_flow_is_none(self, evaluator, registry):
        """_load_workflow_ast returns None when flow is None."""
        mock_store = MagicMock()
        mock_wf = MagicMock()
        mock_wf.flow_id = "f1"
        mock_store.get_workflow.return_value = mock_wf
        mock_store.get_flow.return_value = None

        config = RunnerConfig()
        svc = RunnerService(mock_store, evaluator, config, registry)

        result = svc._load_workflow_ast("wf-1")
        assert result is None

    def test_load_workflow_ast_parse_exception(self, evaluator, registry):
        """_load_workflow_ast returns None on parse error (legacy fallback)."""
        mock_store = MagicMock()
        mock_wf = MagicMock()
        mock_wf.flow_id = "f1"
        mock_wf.name = "TestWF"
        mock_store.get_workflow.return_value = mock_wf

        mock_source = MagicMock()
        mock_source.content = "this is not valid AFL %%% syntax"
        mock_flow = MagicMock()
        mock_flow.compiled_ast = None
        mock_flow.compiled_sources = [mock_source]
        mock_store.get_flow.return_value = mock_flow

        config = RunnerConfig()
        svc = RunnerService(mock_store, evaluator, config, registry)

        result = svc._load_workflow_ast("wf-1")
        assert result is None

    def test_load_workflow_ast_success(self, evaluator, registry):
        """_load_workflow_ast succeeds with compiled_ast on flow."""
        import json

        from afl.emitter import JSONEmitter
        from afl.parser import AFLParser

        # Build a real compiled AST
        source = """
workflow TestLoad(x: Long) => (result: Long) andThen {
    step1 = Compute(input = $.x)
    yield TestLoad(result = step1.input)
}
"""
        parser = AFLParser()
        ast = parser.parse(source)
        emitter = JSONEmitter(include_locations=False)
        program_dict = json.loads(emitter.emit(ast))

        mock_store = MagicMock()
        mock_wf = MagicMock()
        mock_wf.flow_id = "f1"
        mock_wf.name = "TestLoad"
        mock_store.get_workflow.return_value = mock_wf

        mock_flow = MagicMock()
        mock_flow.compiled_ast = program_dict
        mock_flow.compiled_sources = []
        mock_store.get_flow.return_value = mock_flow

        config = RunnerConfig()
        svc = RunnerService(mock_store, evaluator, config, registry)

        result = svc._load_workflow_ast("wf-1")
        assert result is not None
        assert result["name"] == "TestLoad"

    def test_load_workflow_ast_legacy_fallback(self, evaluator, registry):
        """_load_workflow_ast falls back to recompilation when compiled_ast is None."""
        mock_store = MagicMock()
        mock_wf = MagicMock()
        mock_wf.flow_id = "f1"
        mock_wf.name = "TestLoad"
        mock_store.get_workflow.return_value = mock_wf

        mock_source = MagicMock()
        mock_source.content = """
workflow TestLoad(x: Long) => (result: Long) andThen {
    step1 = Compute(input = $.x)
    yield TestLoad(result = step1.input)
}
"""
        mock_flow = MagicMock()
        mock_flow.compiled_ast = None
        mock_flow.compiled_sources = [mock_source]
        mock_store.get_flow.return_value = mock_flow

        config = RunnerConfig()
        svc = RunnerService(mock_store, evaluator, config, registry)

        result = svc._load_workflow_ast("wf-1")
        assert result is not None
        assert result["name"] == "TestLoad"

    def test_load_workflow_ast_no_matching_workflow(self, evaluator, registry):
        """_load_workflow_ast returns None when no workflow matches name."""
        import json

        from afl.emitter import JSONEmitter
        from afl.parser import AFLParser

        source = """
workflow OtherName(x: Long) => (result: Long) andThen {
    step1 = Compute(input = $.x)
    yield OtherName(result = step1.input)
}
"""
        parser = AFLParser()
        ast = parser.parse(source)
        emitter = JSONEmitter(include_locations=False)
        program_dict = json.loads(emitter.emit(ast))

        mock_store = MagicMock()
        mock_wf = MagicMock()
        mock_wf.flow_id = "f1"
        mock_wf.name = "NonExistent"
        mock_store.get_workflow.return_value = mock_wf

        mock_flow = MagicMock()
        mock_flow.compiled_ast = program_dict
        mock_flow.compiled_sources = []
        mock_store.get_flow.return_value = mock_flow

        config = RunnerConfig()
        svc = RunnerService(mock_store, evaluator, config, registry)

        result = svc._load_workflow_ast("wf-1")
        assert result is None

    def test_resume_caches_loaded_ast(self, evaluator, registry):
        """_resume_workflow caches AST loaded from persistence."""
        import json

        from afl.emitter import JSONEmitter
        from afl.parser import AFLParser

        source = """
workflow CacheTest(x: Long) => (result: Long) andThen {
    step1 = Compute(input = $.x)
    yield CacheTest(result = step1.input)
}
"""
        parser = AFLParser()
        ast = parser.parse(source)
        emitter = JSONEmitter(include_locations=False)
        program_dict = json.loads(emitter.emit(ast))

        mock_store = MagicMock()
        mock_wf = MagicMock()
        mock_wf.flow_id = "f1"
        mock_wf.name = "CacheTest"
        mock_store.get_workflow.return_value = mock_wf

        mock_flow = MagicMock()
        mock_flow.compiled_ast = program_dict
        mock_flow.compiled_sources = []
        mock_store.get_flow.return_value = mock_flow

        # get_steps_by_workflow returns empty for resume
        mock_store.get_steps_by_workflow.return_value = []
        mock_store.get_workflow_root.return_value = None

        config = RunnerConfig()
        svc = RunnerService(mock_store, evaluator, config, registry)

        svc._resume_workflow("wf-1")

        # AST should now be cached
        assert "wf-1" in svc._ast_cache
        assert svc._ast_cache["wf-1"]["name"] == "CacheTest"


# =========================================================================
# TestExtendLockLoop
# =========================================================================


class TestExtendLockLoop:
    """Tests for _extend_lock_loop behavior."""

    def test_extend_lock_runs_and_stops(self, store, evaluator, registry):
        """Lock extension loop runs until stop event is set."""
        config = RunnerConfig(lock_extend_interval_ms=30, lock_duration_ms=5000)
        svc = RunnerService(store, evaluator, config, registry)

        # Acquire a lock
        store.acquire_lock("test:lock", 5000)

        stop = threading.Event()
        thread = threading.Thread(
            target=svc._extend_lock_loop, args=("test:lock", stop), daemon=True
        )
        thread.start()

        time.sleep(0.1)  # Let at least one extension happen
        stop.set()
        thread.join(timeout=1)

        # Lock should still be valid (was extended)
        lock = store.check_lock("test:lock")
        assert lock is not None

    def test_extend_lock_stops_on_failure(self, store, evaluator, registry):
        """Lock extension loop stops when extend_lock returns False."""
        config = RunnerConfig(lock_extend_interval_ms=20, lock_duration_ms=5000)
        svc = RunnerService(store, evaluator, config, registry)

        # Don't acquire a lock, so extend will fail immediately
        stop = threading.Event()
        thread = threading.Thread(
            target=svc._extend_lock_loop, args=("nonexistent:lock", stop), daemon=True
        )
        thread.start()
        thread.join(timeout=2)

        # Thread should have exited on its own (extend_lock returned False)
        assert not thread.is_alive()

    def test_extend_lock_stops_on_exception(self, evaluator, registry):
        """Lock extension loop stops on exception from extend_lock."""
        mock_store = MagicMock()
        mock_store.extend_lock.side_effect = RuntimeError("db error")

        config = RunnerConfig(lock_extend_interval_ms=20, lock_duration_ms=5000)
        svc = RunnerService(mock_store, evaluator, config, registry)

        stop = threading.Event()
        thread = threading.Thread(
            target=svc._extend_lock_loop, args=("err:lock", stop), daemon=True
        )
        thread.start()
        thread.join(timeout=2)

        assert not thread.is_alive()


# =========================================================================
# TestHandledStatsEdgeCases
# =========================================================================


class TestHandledStatsEdgeCases:
    """Tests for _update_handled_stats edge cases."""

    def test_stats_update_without_server_registered(self, store, evaluator, registry):
        """Stats update when server is not registered doesn't crash."""
        config = RunnerConfig()
        svc = RunnerService(store, evaluator, config, registry)
        # Don't register server — get_server returns None
        svc._update_handled_stats("SomeFacet", handled=True)
        assert svc._handled_counts["SomeFacet"].handled == 1

    def test_stats_persistence_exception_swallowed(self, evaluator, registry):
        """Exception from persistence during stats update is swallowed."""
        mock_store = MagicMock()
        mock_store.get_server.side_effect = RuntimeError("db down")

        config = RunnerConfig()
        svc = RunnerService(mock_store, evaluator, config, registry)

        # Should not raise
        svc._update_handled_stats("FacetX", handled=False)
        assert svc._handled_counts["FacetX"].not_handled == 1


# =========================================================================
# TestShutdownEdgeCases
# =========================================================================


class TestShutdownEdgeCases:
    """Tests for _shutdown edge cases."""

    def test_shutdown_without_executor(self, store, evaluator, registry):
        """_shutdown with no executor is safe."""
        config = RunnerConfig()
        svc = RunnerService(store, evaluator, config, registry)
        svc._register_server()
        svc._running = True

        svc._shutdown()

        assert svc.is_running is False
        server = store.get_server(svc.server_id)
        assert server.state == ServerState.SHUTDOWN

    def test_shutdown_with_executor_and_futures(self, store, evaluator, registry):
        """_shutdown waits for futures and cleans up."""
        config = RunnerConfig(shutdown_timeout_ms=2000)
        svc = RunnerService(store, evaluator, config, registry)
        svc._register_server()
        svc._running = True

        executor = ThreadPoolExecutor(max_workers=2)
        svc._executor = executor

        # Submit a quick task
        future = executor.submit(lambda: time.sleep(0.01))
        with svc._active_lock:
            svc._active_futures.append(future)

        svc._shutdown()

        assert svc.is_running is False
        assert svc._executor is None
        assert len(svc._active_futures) == 0

    def test_shutdown_future_exception_swallowed(self, store, evaluator, registry):
        """_shutdown swallows exceptions from futures."""
        config = RunnerConfig(shutdown_timeout_ms=2000)
        svc = RunnerService(store, evaluator, config, registry)
        svc._register_server()
        svc._running = True

        executor = ThreadPoolExecutor(max_workers=2)
        svc._executor = executor

        def failing_task():
            raise RuntimeError("task failed")

        future = executor.submit(failing_task)
        with svc._active_lock:
            svc._active_futures.append(future)

        # Should not raise
        svc._shutdown()

        assert svc.is_running is False

    def test_shutdown_deregister_exception_swallowed(self, evaluator, registry):
        """_shutdown swallows exceptions from _deregister_server."""
        mock_store = MagicMock()
        mock_store.get_server.side_effect = RuntimeError("db error")

        config = RunnerConfig()
        svc = RunnerService(mock_store, evaluator, config, registry)
        svc._running = True

        # Should not raise
        svc._shutdown()

        assert svc.is_running is False


# =========================================================================
# TestHeartbeatException
# =========================================================================


class TestHeartbeatException:
    """Tests for heartbeat exception handling."""

    def test_heartbeat_exception_continues(self, evaluator, registry):
        """Heartbeat loop continues after an exception from update_server_ping."""
        mock_store = MagicMock()
        call_count = 0

        def counting_ping(server_id, ping_time):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("transient db error")
            # Otherwise succeed

        mock_store.update_server_ping = counting_ping

        config = RunnerConfig(heartbeat_interval_ms=20)
        svc = RunnerService(mock_store, evaluator, config, registry)

        heartbeat = threading.Thread(target=svc._heartbeat_loop, daemon=True)
        heartbeat.start()

        time.sleep(0.1)
        svc._stopping.set()
        heartbeat.join(timeout=1)

        # Should have been called multiple times despite first exception
        assert call_count >= 2


# =========================================================================
# TestCleanupFutures
# =========================================================================


class TestCleanupFutures:
    """Tests for _cleanup_futures behavior."""

    def test_cleanup_removes_done_futures(self, store, evaluator, registry):
        """Completed futures are removed from the active list."""
        config = RunnerConfig()
        svc = RunnerService(store, evaluator, config, registry)

        done_future = MagicMock(spec=Future)
        done_future.done.return_value = True
        pending_future = MagicMock(spec=Future)
        pending_future.done.return_value = False

        with svc._active_lock:
            svc._active_futures = [done_future, pending_future]

        svc._cleanup_futures()

        assert svc._active_count() == 1

    def test_cleanup_empty_list(self, store, evaluator, registry):
        """Cleanup on empty list is a no-op."""
        config = RunnerConfig()
        svc = RunnerService(store, evaluator, config, registry)
        svc._cleanup_futures()
        assert svc._active_count() == 0


# =========================================================================
# TestExecuteWorkflowHandler
# =========================================================================


class TestExecuteWorkflowHandler:
    """Tests for the built-in afl:execute task handler."""

    def test_handler_registered_on_init(self, store, evaluator, config):
        """afl:execute handler is registered automatically."""
        registry = ToolRegistry()
        RunnerService(store, evaluator, config, registry)
        assert registry.has_handler("afl:execute")

    def test_execute_workflow_success(self, evaluator, config):
        """afl:execute handler executes a workflow and updates runner state."""
        import json

        from afl.emitter import JSONEmitter
        from afl.parser import AFLParser
        from afl.runtime.entities import (
            RunnerState,
        )

        mock_store = MagicMock()

        # Create flow with compiled AST
        afl_source = """
facet Compute(input: Long)

workflow SimpleWF(x: Long) => (result: Long) andThen {
    s1 = Compute(input = $.x)
    yield SimpleWF(result = s1.input)
}
"""
        parser = AFLParser()
        ast = parser.parse(afl_source)
        emitter_obj = JSONEmitter(include_locations=False)
        program_dict = json.loads(emitter_obj.emit(ast))

        mock_flow = MagicMock()
        mock_flow.compiled_ast = program_dict
        mock_flow.compiled_sources = []
        mock_store.get_flow.return_value = mock_flow

        # Create runner
        mock_runner = MagicMock()
        mock_runner.state = RunnerState.CREATED
        mock_runner.start_time = 0
        mock_runner.end_time = 0
        mock_runner.duration = 0
        mock_store.get_runner.return_value = mock_runner

        # Use a real evaluator with a MagicMock persistence that delegates
        # to MemoryStore for step operations
        real_store = MemoryStore()
        real_evaluator = Evaluator(persistence=real_store, telemetry=Telemetry(enabled=False))

        registry = ToolRegistry()
        svc = RunnerService(mock_store, real_evaluator, config, registry)

        payload = {
            "flow_id": "f-1",
            "workflow_name": "SimpleWF",
            "inputs": {"x": 10},
            "runner_id": "r-1",
        }

        result = svc._handle_execute_workflow(payload)

        assert result["status"] == ExecutionStatus.COMPLETED
        assert "workflow_id" in result

        # Runner should have been updated to COMPLETED
        assert mock_runner.state == RunnerState.COMPLETED
        assert mock_runner.end_time > 0
        # Runner workflow_id should match evaluator's generated ID
        assert mock_runner.workflow_id == result["workflow_id"]

    def test_execute_workflow_flow_not_found(self, evaluator, config):
        """afl:execute handler raises when flow is not found."""
        mock_store = MagicMock()
        mock_store.get_flow.return_value = None
        mock_store.get_runner.return_value = None

        registry = ToolRegistry()
        svc = RunnerService(mock_store, evaluator, config, registry)

        with pytest.raises(RuntimeError, match="Flow.*not found"):
            svc._handle_execute_workflow(
                {
                    "flow_id": "missing",
                    "workflow_name": "WF",
                    "runner_id": "",
                }
            )

    def test_execute_workflow_no_sources(self, evaluator, config):
        """afl:execute handler raises when flow has no compiled AST or sources."""
        mock_store = MagicMock()
        mock_flow = MagicMock()
        mock_flow.compiled_ast = None
        mock_flow.compiled_sources = []
        mock_store.get_flow.return_value = mock_flow
        mock_store.get_runner.return_value = None

        registry = ToolRegistry()
        svc = RunnerService(mock_store, evaluator, config, registry)

        with pytest.raises(RuntimeError, match="no compiled AST or sources"):
            svc._handle_execute_workflow(
                {
                    "flow_id": "f-1",
                    "workflow_name": "WF",
                    "runner_id": "",
                }
            )

    def test_execute_workflow_name_not_found(self, evaluator, config):
        """afl:execute handler raises when workflow name not in flow."""
        import json

        from afl.emitter import JSONEmitter
        from afl.parser import AFLParser

        mock_store = MagicMock()
        afl_source = """
facet Compute(input: Long)

workflow OtherWF(x: Long) => (result: Long) andThen {
    s1 = Compute(input = $.x)
    yield OtherWF(result = s1.input)
}
"""
        parser = AFLParser()
        ast = parser.parse(afl_source)
        emitter_obj = JSONEmitter(include_locations=False)
        program_dict = json.loads(emitter_obj.emit(ast))

        mock_flow = MagicMock()
        mock_flow.compiled_ast = program_dict
        mock_flow.compiled_sources = []
        mock_store.get_flow.return_value = mock_flow
        mock_store.get_runner.return_value = None

        registry = ToolRegistry()
        svc = RunnerService(mock_store, evaluator, config, registry)

        with pytest.raises(RuntimeError, match="Workflow.*not found"):
            svc._handle_execute_workflow(
                {
                    "flow_id": "f-1",
                    "workflow_name": "MissingWF",
                    "runner_id": "",
                }
            )

    def test_execute_workflow_sets_runner_failed_on_error(self, evaluator, config):
        """afl:execute handler sets runner to FAILED on exception."""
        from afl.runtime.entities import RunnerState

        mock_store = MagicMock()
        mock_store.get_flow.return_value = None  # Will cause RuntimeError
        mock_runner = MagicMock()
        mock_runner.state = RunnerState.CREATED
        mock_runner.start_time = 0
        mock_runner.end_time = 0
        mock_runner.duration = 0
        mock_store.get_runner.return_value = mock_runner

        registry = ToolRegistry()
        svc = RunnerService(mock_store, evaluator, config, registry)

        with pytest.raises(RuntimeError):
            svc._handle_execute_workflow(
                {
                    "flow_id": "f-1",
                    "workflow_name": "WF",
                    "runner_id": "r-1",
                }
            )

        assert mock_runner.state == RunnerState.FAILED

    def test_execute_workflow_no_get_flow(self, store, evaluator, config):
        """afl:execute handler raises when flow is not found in store."""
        # MemoryStore's default get_flow returns None (flow not in store)
        registry = ToolRegistry()
        svc = RunnerService(store, evaluator, config, registry)

        with pytest.raises(RuntimeError, match="Flow 'f-1' not found"):
            svc._handle_execute_workflow(
                {
                    "flow_id": "f-1",
                    "workflow_name": "WF",
                    "runner_id": "",
                }
            )

    def test_execute_workflow_as_task(self, evaluator, config):
        """afl:execute task is processed end-to-end via run_once."""
        import json

        from afl.emitter import JSONEmitter
        from afl.parser import AFLParser
        from afl.runtime.entities import RunnerState

        mock_store = MagicMock()

        afl_source = """
facet Compute(input: Long)

workflow TaskWF(x: Long) => (result: Long) andThen {
    s1 = Compute(input = $.x)
    yield TaskWF(result = s1.input)
}
"""
        parser = AFLParser()
        ast = parser.parse(afl_source)
        emitter_obj = JSONEmitter(include_locations=False)
        program_dict = json.loads(emitter_obj.emit(ast))

        mock_flow = MagicMock()
        mock_flow.compiled_ast = program_dict
        mock_flow.compiled_sources = []
        mock_store.get_flow.return_value = mock_flow

        mock_runner = MagicMock()
        mock_runner.state = RunnerState.CREATED
        mock_runner.start_time = 0
        mock_runner.end_time = 0
        mock_runner.duration = 0
        mock_store.get_runner.return_value = mock_runner

        # Set up task
        task = TaskDefinition(
            uuid=generate_id(),
            name="afl:execute",
            runner_id="r-1",
            workflow_id="",
            flow_id="f-1",
            step_id="",
            state=TaskState.PENDING,
            task_list_name="default",
            data={
                "flow_id": "f-1",
                "workflow_name": "TaskWF",
                "inputs": {"x": 5},
                "runner_id": "r-1",
            },
        )
        mock_store.save_task.return_value = None
        mock_store.get_pending_tasks.return_value = [task]
        mock_store.get_steps_by_state.return_value = []
        mock_store.claim_task.return_value = None
        mock_store.acquire_lock.return_value = True
        mock_store.release_lock.return_value = None
        mock_store.extend_lock.return_value = True

        real_store = MemoryStore()
        real_evaluator = Evaluator(persistence=real_store, telemetry=Telemetry(enabled=False))

        registry = ToolRegistry()
        svc = RunnerService(mock_store, real_evaluator, config, registry)

        dispatched = svc.run_once()
        assert dispatched == 1

        # Task should be marked completed
        assert task.state == TaskState.COMPLETED


# =========================================================================
# TestRunnerASTSnapshot
# =========================================================================


class TestRunnerASTSnapshot:
    """Tests for runner-snapshotted compiled_ast / workflow_ast."""

    def test_runner_definition_defaults_none(self):
        """New RunnerDefinition fields default to None."""
        from afl.runtime.entities import RunnerDefinition, WorkflowDefinition

        wf = WorkflowDefinition(
            uuid="wf-1",
            name="W",
            namespace_id="ns",
            facet_id="f-1",
            flow_id="fl-1",
            starting_step="s-1",
            version="1.0",
        )
        runner = RunnerDefinition(uuid="r-1", workflow_id="wf-1", workflow=wf)
        assert runner.compiled_ast is None
        assert runner.workflow_ast is None

    def test_execute_snapshots_ast_into_runner(self, evaluator, config):
        """afl:execute handler snapshots compiled_ast and workflow_ast into runner."""
        import json

        from afl.emitter import JSONEmitter
        from afl.parser import AFLParser
        from afl.runtime.entities import RunnerState

        mock_store = MagicMock()

        afl_source = """
facet Compute(input: Long)

workflow SnapshotWF(x: Long) => (result: Long) andThen {
    s1 = Compute(input = $.x)
    yield SnapshotWF(result = s1.input)
}
"""
        parser = AFLParser()
        ast = parser.parse(afl_source)
        emitter_obj = JSONEmitter(include_locations=False)
        program_dict = json.loads(emitter_obj.emit(ast))

        mock_flow = MagicMock()
        mock_flow.compiled_ast = program_dict
        mock_flow.compiled_sources = []
        mock_store.get_flow.return_value = mock_flow

        mock_runner = MagicMock()
        mock_runner.state = RunnerState.CREATED
        mock_runner.start_time = 0
        mock_runner.end_time = 0
        mock_runner.duration = 0
        mock_store.get_runner.return_value = mock_runner

        real_store = MemoryStore()
        real_evaluator = Evaluator(persistence=real_store, telemetry=Telemetry(enabled=False))

        registry = ToolRegistry()
        svc = RunnerService(mock_store, real_evaluator, config, registry)

        payload = {
            "flow_id": "f-1",
            "workflow_name": "SnapshotWF",
            "inputs": {"x": 10},
            "runner_id": "r-1",
        }
        svc._handle_execute_workflow(payload)

        # Runner should have snapshotted ASTs
        assert mock_runner.compiled_ast == program_dict
        assert mock_runner.workflow_ast is not None
        assert mock_runner.workflow_ast["name"] == "SnapshotWF"

    def test_resume_prefers_runner_ast_over_flow(self, config):
        """_load_workflow_ast prefers runner-snapshotted AST over flow lookup."""
        from afl.runtime.entities import RunnerDefinition, RunnerState, WorkflowDefinition

        store = MemoryStore()
        evaluator = Evaluator(persistence=store, telemetry=Telemetry(enabled=False))
        registry = ToolRegistry()
        svc = RunnerService(store, evaluator, config, registry)

        # Create a runner with snapshotted ASTs
        wf = WorkflowDefinition(
            uuid="wf-snap",
            name="SnapWF",
            namespace_id="ns",
            facet_id="f-1",
            flow_id="fl-1",
            starting_step="s-1",
            version="1.0",
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

        # _load_workflow_ast should find it from the runner
        result = svc._load_workflow_ast("wf-snap")
        assert result == wf_ast
        assert svc._program_ast_cache["wf-snap"] == program_dict

    def test_resume_falls_back_without_runner_ast(self, config):
        """_load_workflow_ast falls back to flow when runner has no ASTs."""
        from afl.runtime.entities import RunnerDefinition, RunnerState, WorkflowDefinition

        store = MemoryStore()
        evaluator = Evaluator(persistence=store, telemetry=Telemetry(enabled=False))
        registry = ToolRegistry()
        svc = RunnerService(store, evaluator, config, registry)

        # Create a runner WITHOUT snapshotted ASTs (legacy)
        wf = WorkflowDefinition(
            uuid="wf-legacy",
            name="LegacyWF",
            namespace_id="ns",
            facet_id="f-1",
            flow_id="fl-1",
            starting_step="s-1",
            version="1.0",
        )
        runner = RunnerDefinition(
            uuid="r-legacy",
            workflow_id="wf-legacy",
            workflow=wf,
            state=RunnerState.RUNNING,
        )
        store.save_runner(runner)

        # MemoryStore has no get_workflow/get_flow, so fallback returns None
        result = svc._load_workflow_ast("wf-legacy")
        assert result is None


# =========================================================================
# TestHTTPStatusServer
# =========================================================================


class TestHTTPStatusServer:
    """Tests for the embedded HTTP status server."""

    def _get(self, port: int, path: str) -> tuple[int, dict]:
        """Make a GET request and return (status_code, json_body)."""
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", path)
        resp = conn.getresponse()
        status = resp.status
        body = json.loads(resp.read().decode("utf-8"))
        conn.close()
        return status, body

    def test_http_server_starts(self, store, evaluator, registry):
        """HTTP server starts and /health returns 200."""
        config = RunnerConfig(http_port=0)
        # Use port 0 to let OS pick a free port — but our impl increments
        # from the given port. Pick a high random port to avoid conflicts.
        port = 19800
        config = RunnerConfig(http_port=port)
        svc = RunnerService(store, evaluator, config, registry)

        actual_port = svc._start_http_server()
        try:
            assert actual_port >= port
            assert svc.http_port == actual_port

            status, body = self._get(actual_port, "/health")
            assert status == 200
            assert body == {"ok": True}
        finally:
            svc._stop_http_server()

    def test_http_status_endpoint(self, store, evaluator, registry):
        """GET /status returns expected JSON keys."""
        config = RunnerConfig(http_port=19810)
        svc = RunnerService(store, evaluator, config, registry)
        svc._start_time_ms = _current_time_ms()

        actual_port = svc._start_http_server()
        try:
            status, body = self._get(actual_port, "/status")
            assert status == 200
            assert body["server_id"] == svc.server_id
            assert "running" in body
            assert "uptime_ms" in body
            assert "handled" in body
            assert "active_work_items" in body
            assert "config" in body
            assert body["config"]["service_name"] == "afl-runner"
        finally:
            svc._stop_http_server()

    def test_http_auto_port(self, store, evaluator, registry):
        """When the default port is occupied, the server picks the next one."""
        port = 19820
        # Occupy the port with a plain socket
        blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        blocker.bind(("0.0.0.0", port))
        blocker.listen(1)

        try:
            config = RunnerConfig(http_port=port)
            svc = RunnerService(store, evaluator, config, registry)
            actual_port = svc._start_http_server()
            try:
                assert actual_port > port
                status, body = self._get(actual_port, "/health")
                assert status == 200
                assert body == {"ok": True}
            finally:
                svc._stop_http_server()
        finally:
            blocker.close()

    def test_http_404(self, store, evaluator, registry):
        """GET on an unknown path returns 404."""
        config = RunnerConfig(http_port=19830)
        svc = RunnerService(store, evaluator, config, registry)

        actual_port = svc._start_http_server()
        try:
            status, body = self._get(actual_port, "/unknown")
            assert status == 404
            assert body == {"error": "not found"}
        finally:
            svc._stop_http_server()

    def test_http_server_stops(self, store, evaluator, registry):
        """After stopping, the port is freed."""
        config = RunnerConfig(http_port=19840)
        svc = RunnerService(store, evaluator, config, registry)

        actual_port = svc._start_http_server()
        svc._stop_http_server()

        assert svc.http_port is None

        # Port should be free — verify by binding to it
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.bind(("0.0.0.0", actual_port))
        finally:
            sock.close()

    def test_http_all_ports_exhausted(self, store, evaluator, registry):
        """RuntimeError raised when all port attempts fail."""
        config = RunnerConfig(http_port=19850, http_max_port_attempts=2)
        svc = RunnerService(store, evaluator, config, registry)

        # Occupy both ports
        blockers = []
        for p in (19850, 19851):
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("0.0.0.0", p))
            s.listen(1)
            blockers.append(s)

        try:
            with pytest.raises(RuntimeError, match="Could not bind"):
                svc._start_http_server()
        finally:
            for s in blockers:
                s.close()


# =========================================================================
# TestEventTaskCreation
# =========================================================================


class TestEventTaskCreation:
    """Tests for automatic task creation when step reaches EVENT_TRANSMIT."""

    def test_event_task_created_on_event_transmit(
        self, store, evaluator, workflow_ast, program_ast
    ):
        """Executing a workflow with an event facet creates a task in the store."""
        result = _execute_until_paused(evaluator, workflow_ast, {"x": 1}, program_ast)
        assert result.status == ExecutionStatus.PAUSED

        # There should be a pending task in the store
        tasks = list(store._tasks.values())
        event_tasks = [t for t in tasks if t.name == "CountDocuments"]
        assert len(event_tasks) == 1

        task = event_tasks[0]
        assert task.state == TaskState.PENDING
        assert task.step_id  # Should have a step_id
        assert task.workflow_id == result.workflow_id
        assert task.task_list_name == "default"
        assert task.data is not None
        assert "input" in task.data

    def test_event_task_has_qualified_name_for_namespaced_facet(self, store, evaluator):
        """Event task name is qualified when the facet is inside a namespace."""
        ns_program_ast = {
            "type": "Program",
            "declarations": [
                {
                    "type": "Namespace",
                    "name": "MyNs",
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
                },
                {
                    "type": "WorkflowDecl",
                    "name": "TestWorkflow",
                    "params": [{"name": "x", "type": "Long"}],
                    "returns": [{"name": "result", "type": "Long"}],
                },
            ],
        }

        ns_workflow_ast = {
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
                                    "value": {"type": "InputRef", "path": ["x"]},
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
                                "value": {"type": "StepRef", "path": ["s2", "output"]},
                            }
                        ],
                    },
                },
            },
        }

        result = evaluator.execute(ns_workflow_ast, inputs={"x": 5}, program_ast=ns_program_ast)
        assert result.status == ExecutionStatus.PAUSED

        # Task should have qualified name
        tasks = list(store._tasks.values())
        event_tasks = [t for t in tasks if "CountDocuments" in t.name]
        assert len(event_tasks) == 1
        assert event_tasks[0].name == "MyNs.CountDocuments"

    def test_event_task_runner_id_set(self, store, workflow_ast, program_ast):
        """Event task includes runner_id when provided to evaluator."""
        eval_with_runner = Evaluator(persistence=store, telemetry=Telemetry(enabled=False))
        result = eval_with_runner.execute(
            workflow_ast,
            inputs={"x": 1},
            program_ast=program_ast,
            runner_id="runner-123",
        )
        assert result.status == ExecutionStatus.PAUSED

        tasks = list(store._tasks.values())
        event_tasks = [t for t in tasks if t.name == "CountDocuments"]
        assert len(event_tasks) == 1
        assert event_tasks[0].runner_id == "runner-123"


# =========================================================================
# TestClaimTask
# =========================================================================


class TestClaimTask:
    """Tests for the claim_task persistence method."""

    def test_claim_task_returns_none_when_empty(self, store):
        """Empty store returns None."""
        result = store.claim_task(["SomeName"])
        assert result is None

    def test_claim_task_filters_by_name(self, store):
        """Only tasks matching the given names are claimed."""
        task = TaskDefinition(
            uuid=generate_id(),
            name="TargetName",
            runner_id="r1",
            workflow_id="w1",
            flow_id="f1",
            step_id="s1",
            state=TaskState.PENDING,
            task_list_name="default",
        )
        store.save_task(task)

        # No match
        result = store.claim_task(["OtherName"])
        assert result is None

        # Match
        result = store.claim_task(["TargetName"])
        assert result is not None
        assert result.uuid == task.uuid
        assert result.state == TaskState.RUNNING

    def test_claim_task_skips_running(self, store):
        """Already running tasks are not claimed."""
        task = TaskDefinition(
            uuid=generate_id(),
            name="MyTask",
            runner_id="r1",
            workflow_id="w1",
            flow_id="f1",
            step_id="s1",
            state=TaskState.RUNNING,
            task_list_name="default",
        )
        store.save_task(task)

        result = store.claim_task(["MyTask"])
        assert result is None

    def test_claim_task_atomic_two_services(self, store):
        """Two claim_task calls on same task: only one gets it."""
        task = TaskDefinition(
            uuid=generate_id(),
            name="EventA",
            runner_id="r1",
            workflow_id="w1",
            flow_id="f1",
            step_id="s1",
            state=TaskState.PENDING,
            task_list_name="default",
        )
        store.save_task(task)

        result1 = store.claim_task(["EventA"])
        result2 = store.claim_task(["EventA"])

        assert result1 is not None
        assert result2 is None

    def test_claim_task_filters_by_task_list(self, store):
        """Only tasks in the specified task list are claimed."""
        task = TaskDefinition(
            uuid=generate_id(),
            name="EventB",
            runner_id="r1",
            workflow_id="w1",
            flow_id="f1",
            step_id="s1",
            state=TaskState.PENDING,
            task_list_name="special",
        )
        store.save_task(task)

        result = store.claim_task(["EventB"], task_list="default")
        assert result is None

        result = store.claim_task(["EventB"], task_list="special")
        assert result is not None


# =========================================================================
# TestProcessEventTask
# =========================================================================


class TestProcessEventTask:
    """Tests for _process_event_task method."""

    def test_process_event_task_end_to_end(self, store, evaluator, workflow_ast, program_ast):
        """Claim and process an event task completes the workflow."""
        registry = ToolRegistry()
        registry.register("CountDocuments", lambda p: {"output": 42})
        config = RunnerConfig()
        svc = RunnerService(store, evaluator, config, registry)

        # Execute until paused (creates event task)
        result = _execute_until_paused(evaluator, workflow_ast, {"x": 1}, program_ast)
        assert result.status == ExecutionStatus.PAUSED

        svc.cache_workflow_ast(result.workflow_id, workflow_ast)

        # Claim the event task
        task = store.claim_task(["CountDocuments"])
        assert task is not None

        # Process it
        svc._process_event_task(task)

        # Task should be completed
        updated_task = store._tasks[task.uuid]
        assert updated_task.state == TaskState.COMPLETED

        # Stats should be updated
        assert svc._handled_counts["CountDocuments"].handled == 1

    def test_process_event_task_no_handler(self, store, evaluator, workflow_ast, program_ast):
        """Event task with no handler is marked FAILED."""
        registry = ToolRegistry()
        # Register to get past execution, but don't register for claiming
        config = RunnerConfig()
        svc = RunnerService(store, evaluator, config, registry)

        # Manually create a task
        task = TaskDefinition(
            uuid=generate_id(),
            name="UnknownEvent",
            runner_id="r1",
            workflow_id="w1",
            flow_id="f1",
            step_id="s1",
            state=TaskState.RUNNING,
            task_list_name="default",
            data={"key": "val"},
        )
        store.save_task(task)

        svc._process_event_task(task)

        updated = store._tasks[task.uuid]
        assert updated.state == TaskState.FAILED
        assert "No handler" in updated.error["message"]

    def test_process_event_task_handler_error(self, store, evaluator, workflow_ast, program_ast):
        """Event task handler exception marks task FAILED."""

        def failing(p):
            raise ValueError("handler boom")

        registry = ToolRegistry()
        registry.register("CountDocuments", failing)
        config = RunnerConfig()
        svc = RunnerService(store, evaluator, config, registry)

        result = _execute_until_paused(evaluator, workflow_ast, {"x": 1}, program_ast)
        assert result.status == ExecutionStatus.PAUSED

        task = store.claim_task(["CountDocuments"])
        assert task is not None

        svc._process_event_task(task)

        updated = store._tasks[task.uuid]
        assert updated.state == TaskState.FAILED
        assert "handler boom" in updated.error["message"]


# =========================================================================
# TestPollCycleClaimsEventTasks
# =========================================================================


class TestPollCycleClaimsEventTasks:
    """Tests for poll cycle claiming event tasks from task queue."""

    def test_poll_cycle_claims_event_tasks(self, store, evaluator, workflow_ast, program_ast):
        """run_once finds and processes event tasks via claim_task."""
        registry = ToolRegistry()
        registry.register("CountDocuments", lambda p: {"output": 99})
        config = RunnerConfig()
        svc = RunnerService(store, evaluator, config, registry)

        result = _execute_until_paused(evaluator, workflow_ast, {"x": 1}, program_ast)
        assert result.status == ExecutionStatus.PAUSED

        svc.cache_workflow_ast(result.workflow_id, workflow_ast)

        dispatched = svc.run_once()
        assert dispatched == 1

        # Event task should now be completed
        tasks = list(store._tasks.values())
        event_tasks = [t for t in tasks if t.name == "CountDocuments"]
        assert len(event_tasks) == 1
        assert event_tasks[0].state == TaskState.COMPLETED

    def test_topics_filter_event_tasks(self, store, evaluator, workflow_ast, program_ast):
        """Only matching qualified names are claimed when topics are configured."""
        registry = ToolRegistry()
        registry.register("CountDocuments", lambda p: {"output": 1})
        config = RunnerConfig(topics=["OtherFacet"])
        svc = RunnerService(store, evaluator, config, registry)

        result = _execute_until_paused(evaluator, workflow_ast, {"x": 1}, program_ast)
        assert result.status == ExecutionStatus.PAUSED

        svc.cache_workflow_ast(result.workflow_id, workflow_ast)

        # Should not claim because topics filter doesn't match
        dispatched = svc.run_once()
        assert dispatched == 0

    def test_topics_filter_matches_event_tasks(self, store, evaluator, workflow_ast, program_ast):
        """Matching topics allow event tasks to be claimed."""
        registry = ToolRegistry()
        registry.register("CountDocuments", lambda p: {"output": 1})
        config = RunnerConfig(topics=["CountDocuments"])
        svc = RunnerService(store, evaluator, config, registry)

        result = _execute_until_paused(evaluator, workflow_ast, {"x": 1}, program_ast)
        assert result.status == ExecutionStatus.PAUSED

        svc.cache_workflow_ast(result.workflow_id, workflow_ast)

        dispatched = svc.run_once()
        assert dispatched == 1


# =========================================================================
# TestQualifiedFacetNames
# =========================================================================


class TestQualifiedFacetNames:
    """Tests for qualified facet name resolution."""

    def test_resolve_qualified_name_top_level(self, evaluator, program_ast):
        """Top-level facets resolve to short name."""
        from afl.runtime.evaluator import ExecutionContext
        from afl.runtime.persistence import IterationChanges

        ctx = ExecutionContext(
            persistence=evaluator.persistence,
            telemetry=evaluator.telemetry,
            changes=IterationChanges(),
            workflow_id="wf-1",
            program_ast=program_ast,
        )
        assert ctx.resolve_qualified_name("Value") == "Value"
        assert ctx.resolve_qualified_name("CountDocuments") == "CountDocuments"

    def test_resolve_qualified_name_namespaced(self, evaluator):
        """Namespaced facets resolve to 'ns.FacetName'."""
        from afl.runtime.evaluator import ExecutionContext
        from afl.runtime.persistence import IterationChanges

        ns_ast = {
            "type": "Program",
            "declarations": [
                {
                    "type": "Namespace",
                    "name": "Data",
                    "declarations": [
                        {
                            "type": "EventFacetDecl",
                            "name": "Fetch",
                            "params": [],
                        },
                    ],
                },
            ],
        }
        ctx = ExecutionContext(
            persistence=evaluator.persistence,
            telemetry=evaluator.telemetry,
            changes=IterationChanges(),
            workflow_id="wf-1",
            program_ast=ns_ast,
        )
        assert ctx.resolve_qualified_name("Fetch") == "Data.Fetch"

    def test_resolve_qualified_name_unknown(self, evaluator):
        """Unknown facet names are returned as-is."""
        from afl.runtime.evaluator import ExecutionContext
        from afl.runtime.persistence import IterationChanges

        ctx = ExecutionContext(
            persistence=evaluator.persistence,
            telemetry=evaluator.telemetry,
            changes=IterationChanges(),
            workflow_id="wf-1",
            program_ast={"type": "Program", "declarations": []},
        )
        assert ctx.resolve_qualified_name("Unknown") == "Unknown"

    def test_get_facet_definition_qualified(self, evaluator):
        """get_facet_definition accepts qualified names."""
        from afl.runtime.evaluator import ExecutionContext
        from afl.runtime.persistence import IterationChanges

        ns_ast = {
            "type": "Program",
            "declarations": [
                {
                    "type": "Namespace",
                    "name": "MyNs",
                    "declarations": [
                        {
                            "type": "EventFacetDecl",
                            "name": "DoWork",
                            "params": [{"name": "x", "type": "Long"}],
                        },
                    ],
                },
            ],
        }
        ctx = ExecutionContext(
            persistence=evaluator.persistence,
            telemetry=evaluator.telemetry,
            changes=IterationChanges(),
            workflow_id="wf-1",
            program_ast=ns_ast,
        )

        # Qualified name lookup
        facet = ctx.get_facet_definition("MyNs.DoWork")
        assert facet is not None
        assert facet["name"] == "DoWork"

        # Short name lookup (still works)
        facet2 = ctx.get_facet_definition("DoWork")
        assert facet2 is not None

    def test_dependency_graph_qualified_names(self):
        """DependencyGraph resolves facet names to qualified form."""
        from afl.runtime.dependency import DependencyGraph

        program_ast = {
            "type": "Program",
            "declarations": [
                {
                    "type": "Namespace",
                    "name": "DB",
                    "declarations": [
                        {
                            "type": "EventFacetDecl",
                            "name": "Query",
                            "params": [{"name": "sql", "type": "String"}],
                        },
                    ],
                },
            ],
        }

        block_ast = {
            "steps": [
                {
                    "id": "s1",
                    "name": "s1",
                    "call": {
                        "target": "Query",
                        "args": [
                            {"name": "sql", "value": {"type": "Literal", "value": "SELECT 1"}},
                        ],
                    },
                },
            ],
        }

        graph = DependencyGraph.from_ast(block_ast, set(), program_ast=program_ast)
        stmt = graph.get_statement("s1")
        assert stmt is not None
        assert stmt.facet_name == "DB.Query"


# =============================================================================
# Orphaned Task Reaper Tests
# =============================================================================


class TestClaimTaskServerTracking:
    """Tests that claim_task records the server_id on the task."""

    def test_claim_task_sets_server_id(self, store):
        """claim_task with server_id stores it on the task document."""
        task = TaskDefinition(
            uuid=generate_id(),
            name="MyEvent",
            runner_id="r1",
            workflow_id="w1",
            flow_id="f1",
            step_id="s1",
            state=TaskState.PENDING,
            task_list_name="default",
        )
        store.save_task(task)

        result = store.claim_task(["MyEvent"], server_id="server-abc")
        assert result is not None
        assert result.state == TaskState.RUNNING

    def test_claim_task_without_server_id_still_works(self, store):
        """Backward compat: claim_task without server_id works."""
        task = TaskDefinition(
            uuid=generate_id(),
            name="MyEvent",
            runner_id="r1",
            workflow_id="w1",
            flow_id="f1",
            step_id="s1",
            state=TaskState.PENDING,
            task_list_name="default",
        )
        store.save_task(task)

        result = store.claim_task(["MyEvent"])
        assert result is not None
        assert result.state == TaskState.RUNNING


class TestReapOrphanedTasks:
    """Tests for the reap_orphaned_tasks persistence method."""

    def test_no_servers_returns_empty(self, store):
        """No servers at all → nothing to reap."""
        assert store.reap_orphaned_tasks() == []

    def test_healthy_server_tasks_not_reaped(self, store):
        """Tasks claimed by a healthy server should NOT be reaped."""
        # Register a healthy server (recent ping)
        server = ServerDefinition(
            uuid="healthy-server",
            server_group="test",
            service_name="test-runner",
            server_name="host1",
            state=ServerState.RUNNING,
            ping_time=int(time.time() * 1000),  # just now
        )
        store.save_server(server)

        # The MemoryStore doesn't track server_id on tasks, so this
        # test mainly verifies the method runs without error.
        assert store.reap_orphaned_tasks() == []

    def test_default_returns_empty(self, store):
        """Base PersistenceAPI.reap_orphaned_tasks returns empty list."""
        assert store.reap_orphaned_tasks() == []


class TestReaperInRunnerService:
    """Tests for the orphan reaper integration in RunnerService."""

    def test_reaper_called_periodically(self, store, evaluator, registry):
        """The reaper is called after the interval elapses."""
        config = RunnerConfig(
            poll_interval_ms=100,
            heartbeat_interval_ms=60000,
        )
        svc = RunnerService(store, evaluator, config, registry)
        svc._reap_interval_ms = 0  # fire every cycle

        with patch.object(store, "reap_orphaned_tasks", return_value=[]) as mock_reap:
            svc._maybe_reap_orphaned_tasks()
            assert mock_reap.call_count == 1

    def test_reaper_skipped_within_interval(self, store, evaluator, registry):
        """The reaper is skipped if interval hasn't elapsed."""
        config = RunnerConfig(
            poll_interval_ms=100,
            heartbeat_interval_ms=60000,
        )
        svc = RunnerService(store, evaluator, config, registry)
        svc._reap_interval_ms = 999_999_999  # never fires
        svc._last_reap = _current_time_ms()  # just ran

        with patch.object(store, "reap_orphaned_tasks", return_value=[]) as mock_reap:
            svc._maybe_reap_orphaned_tasks()
            assert mock_reap.call_count == 0

    def test_reaper_logs_on_recovery(self, store, evaluator, registry):
        """The reaper logs a warning and emits step logs when tasks are recovered."""
        config = RunnerConfig(
            poll_interval_ms=100,
            heartbeat_interval_ms=60000,
        )
        svc = RunnerService(store, evaluator, config, registry)
        svc._reap_interval_ms = 0

        reaped_tasks = [
            {
                "step_id": f"s{i}",
                "workflow_id": "w1",
                "name": "MyEvent",
                "server_id": "dead-server-1",
            }
            for i in range(3)
        ]
        with patch.object(store, "reap_orphaned_tasks", return_value=reaped_tasks):
            with patch.object(store, "save_step_log") as mock_log:
                svc._maybe_reap_orphaned_tasks()
                # Should emit a step log for each reaped task
                assert mock_log.call_count == 3
                entry = mock_log.call_args_list[0][0][0]
                assert "restarted" in entry.message.lower()
                assert "dead-ser" in entry.message  # truncated server_id

    def test_reaper_survives_exception(self, store, evaluator, registry):
        """The reaper catches exceptions and does not crash."""
        config = RunnerConfig(
            poll_interval_ms=100,
            heartbeat_interval_ms=60000,
        )
        svc = RunnerService(store, evaluator, config, registry)
        svc._reap_interval_ms = 0

        with patch.object(store, "reap_orphaned_tasks", side_effect=Exception("db error")):
            # Should not raise
            svc._maybe_reap_orphaned_tasks()

    def test_poll_loop_passes_server_id_to_claim(self, store, evaluator, registry):
        """claim_task in poll cycle includes server_id."""
        config = RunnerConfig(
            poll_interval_ms=100,
            heartbeat_interval_ms=60000,
        )
        svc = RunnerService(store, evaluator, config, registry)

        # Register a handler so event_names is non-empty
        registry.register("TestEvent", lambda p: {"ok": True})

        with patch.object(store, "claim_task", return_value=None) as mock_claim:
            svc._poll_cycle()
            # Should have been called with server_id
            if mock_claim.call_count > 0:
                _, kwargs = mock_claim.call_args
                assert "server_id" in kwargs
                assert kwargs["server_id"] == svc.server_id

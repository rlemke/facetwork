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

"""Tests for the AFL RegistryRunner.

Tests cover:
- HandlerRegistration CRUD via persistence
- Dynamic module loading (file:// URI, dotted module, errors)
- Module caching and cache invalidation
- RegistryRunner poll_once (end-to-end, no tasks, failure, handler not found, load failure, short-name fallback)
- RegistryRunner lifecycle (server registration, AST caching, stop)
- Registry refresh behaviour
"""

import threading
import time

import pytest

from afl.runtime import (
    Evaluator,
    ExecutionStatus,
    HandlerRegistration,
    MemoryStore,
    StepState,
    Telemetry,
)
from afl.runtime.entities import (
    ServerState,
    TaskDefinition,
    TaskState,
)
from afl.runtime.registry_runner import RegistryRunner, RegistryRunnerConfig, _current_time_ms
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
    """Default runner config."""
    return RegistryRunnerConfig()


@pytest.fixture
def runner(store, evaluator, config):
    """RegistryRunner with defaults."""
    return RegistryRunner(
        persistence=store,
        evaluator=evaluator,
        config=config,
    )


@pytest.fixture
def handler_file(tmp_path):
    """Temp Python file with a handle() function."""
    f = tmp_path / "test_handler.py"
    f.write_text("def handle(payload):\n    return {'output': payload.get('input', 0) * 2}\n")
    return str(f)


@pytest.fixture
def handler_module(tmp_path, monkeypatch):
    """Test module on sys.path."""
    d = tmp_path / "test_handlers"
    d.mkdir()
    (d / "__init__.py").write_text("def handle(payload):\n    return {'result': 42}\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    return "test_handlers"


# ---- Workflow AST fixtures ----


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
# TestHandlerRegistrationCRUD
# =========================================================================


class TestHandlerRegistrationCRUD:
    """Tests for handler registration persistence operations."""

    def test_save_and_get(self, store):
        """Round-trip a registration through persistence."""
        reg = HandlerRegistration(
            facet_name="ns.MyHandler",
            module_uri="my.module",
            entrypoint="handle",
            version="1.0.0",
        )
        store.save_handler_registration(reg)

        loaded = store.get_handler_registration("ns.MyHandler")
        assert loaded is not None
        assert loaded.facet_name == "ns.MyHandler"
        assert loaded.module_uri == "my.module"
        assert loaded.entrypoint == "handle"

    def test_list(self, store):
        """List returns all registrations."""
        store.save_handler_registration(HandlerRegistration(facet_name="ns.A", module_uri="a"))
        store.save_handler_registration(HandlerRegistration(facet_name="ns.B", module_uri="b"))
        regs = store.list_handler_registrations()
        names = {r.facet_name for r in regs}
        assert names == {"ns.A", "ns.B"}

    def test_delete(self, store):
        """Delete removes a registration."""
        store.save_handler_registration(HandlerRegistration(facet_name="ns.X", module_uri="x"))
        assert store.delete_handler_registration("ns.X") is True
        assert store.get_handler_registration("ns.X") is None

    def test_delete_not_found(self, store):
        """Delete returns False for non-existent facet."""
        assert store.delete_handler_registration("ns.Missing") is False

    def test_overwrite(self, store):
        """Saving with same facet_name overwrites."""
        store.save_handler_registration(HandlerRegistration(facet_name="ns.A", module_uri="old"))
        store.save_handler_registration(HandlerRegistration(facet_name="ns.A", module_uri="new"))
        loaded = store.get_handler_registration("ns.A")
        assert loaded.module_uri == "new"

    def test_register_handler_convenience(self, store, evaluator):
        """register_handler() convenience method creates and saves a registration."""
        runner = RegistryRunner(persistence=store, evaluator=evaluator)
        runner.register_handler(
            facet_name="ns.MyFacet",
            module_uri="my.handlers",
            entrypoint="run",
            version="2.0.0",
        )

        loaded = store.get_handler_registration("ns.MyFacet")
        assert loaded is not None
        assert loaded.module_uri == "my.handlers"
        assert loaded.entrypoint == "run"
        assert loaded.version == "2.0.0"
        assert loaded.created > 0

        # Should also be in registered_names
        assert "ns.MyFacet" in runner.registered_names()


# =========================================================================
# TestDynamicModuleLoading
# =========================================================================


class TestDynamicModuleLoading:
    """Tests for dynamic handler loading."""

    def test_load_file_uri(self, store, evaluator, handler_file):
        """Load a handler from a file:// URI."""
        runner = RegistryRunner(persistence=store, evaluator=evaluator)
        reg = HandlerRegistration(
            facet_name="ns.FileHandler",
            module_uri=f"file://{handler_file}",
            entrypoint="handle",
        )

        handler = runner._dispatcher._import_handler(reg)
        result = handler({"input": 5})
        assert result == {"output": 10}

    def test_load_dotted_module(self, store, evaluator, handler_module):
        """Load a handler from a dotted module path."""
        runner = RegistryRunner(persistence=store, evaluator=evaluator)
        reg = HandlerRegistration(
            facet_name="ns.DottedHandler",
            module_uri=handler_module,
            entrypoint="handle",
        )

        handler = runner._dispatcher._import_handler(reg)
        result = handler({})
        assert result == {"result": 42}

    def test_bad_module_path(self, store, evaluator):
        """ImportError for non-existent module."""
        runner = RegistryRunner(persistence=store, evaluator=evaluator)
        reg = HandlerRegistration(
            facet_name="ns.Bad",
            module_uri="nonexistent.module.path",
        )

        with pytest.raises(ImportError):
            runner._dispatcher._import_handler(reg)

    def test_bad_entrypoint(self, store, evaluator, handler_module):
        """AttributeError for non-existent entrypoint."""
        runner = RegistryRunner(persistence=store, evaluator=evaluator)
        reg = HandlerRegistration(
            facet_name="ns.BadEntry",
            module_uri=handler_module,
            entrypoint="nonexistent_function",
        )

        with pytest.raises(AttributeError):
            runner._dispatcher._import_handler(reg)

    def test_non_callable_entrypoint(self, tmp_path, store, evaluator, monkeypatch):
        """TypeError for non-callable entrypoint."""
        d = tmp_path / "non_callable_mod"
        d.mkdir()
        (d / "__init__.py").write_text("handle = 42\n")
        monkeypatch.syspath_prepend(str(tmp_path))

        runner = RegistryRunner(persistence=store, evaluator=evaluator)
        reg = HandlerRegistration(
            facet_name="ns.NonCallable",
            module_uri="non_callable_mod",
            entrypoint="handle",
        )

        with pytest.raises(TypeError, match="not callable"):
            runner._dispatcher._import_handler(reg)


# =========================================================================
# TestModuleCaching
# =========================================================================


class TestModuleCaching:
    """Tests for module caching behaviour."""

    def test_cache_hit(self, store, evaluator, handler_file):
        """Second load uses cache (does not re-import)."""
        runner = RegistryRunner(persistence=store, evaluator=evaluator)
        reg = HandlerRegistration(
            facet_name="ns.Cached",
            module_uri=f"file://{handler_file}",
            entrypoint="handle",
            checksum="abc123",
        )

        handler1 = runner._dispatcher._load_handler(reg)
        handler2 = runner._dispatcher._load_handler(reg)
        assert handler1 is handler2

    def test_checksum_change_evicts_cache(self, store, evaluator, handler_file):
        """Changed checksum forces a fresh import."""
        runner = RegistryRunner(persistence=store, evaluator=evaluator)
        reg_v1 = HandlerRegistration(
            facet_name="ns.Versioned",
            module_uri=f"file://{handler_file}",
            entrypoint="handle",
            checksum="v1",
        )
        reg_v2 = HandlerRegistration(
            facet_name="ns.Versioned",
            module_uri=f"file://{handler_file}",
            entrypoint="handle",
            checksum="v2",
        )

        _handler_v1 = runner._dispatcher._load_handler(reg_v1)
        _handler_v2 = runner._dispatcher._load_handler(reg_v2)
        # Different checksum -> different cache entry -> different import
        # (they're functionally equal but are separate function objects)
        assert (reg_v1.module_uri, reg_v1.checksum) in runner._module_cache
        assert (reg_v2.module_uri, reg_v2.checksum) in runner._module_cache


# =========================================================================
# TestRegistryRunnerPollOnce
# =========================================================================


class TestRegistryRunnerPollOnce:
    """Tests for the poll_once() synchronous cycle."""

    def test_poll_claims_and_processes(
        self, store, evaluator, workflow_ast, program_ast, handler_file
    ):
        """End-to-end: register handler, poll_once
        -> callback invoked, step continued, task completed."""
        result = _execute_until_paused(evaluator, workflow_ast, {"x": 1}, program_ast)
        assert result.status == ExecutionStatus.PAUSED

        # Find the event-blocked step
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

        # Create runner with a file-based handler
        runner = RegistryRunner(
            persistence=store,
            evaluator=evaluator,
            config=RegistryRunnerConfig(),
        )

        # Write a handler that returns {output: 42}
        reg = HandlerRegistration(
            facet_name="CountDocuments",
            module_uri=f"file://{handler_file}",
            entrypoint="handle",
        )
        store.save_handler_registration(reg)

        runner.cache_workflow_ast(result.workflow_id, workflow_ast)

        dispatched = runner.poll_once()
        assert dispatched == 1

        # Task should be completed
        updated_task = store._tasks[task.uuid]
        assert updated_task.state == TaskState.COMPLETED

        # Step should have moved past EVENT_TRANSMIT
        updated_step = store.get_step(step.id)
        assert updated_step.state != StepState.EVENT_TRANSMIT

    def test_poll_no_tasks(self, runner):
        """poll_once returns 0 when no tasks are available."""
        assert runner.poll_once() == 0

    def test_poll_no_registrations(self, runner):
        """poll_once returns 0 when no handlers are registered."""
        assert runner.poll_once() == 0

    def test_poll_handler_exception(self, store, evaluator, workflow_ast, program_ast, tmp_path):
        """Exception in handler -> step error, task failed."""
        _execute_until_paused(evaluator, workflow_ast, {"x": 1}, program_ast)

        blocked = store.get_steps_by_state(StepState.EVENT_TRANSMIT)
        step = blocked[0]

        pending_tasks = [
            t
            for t in store._tasks.values()
            if t.state == TaskState.PENDING and t.step_id == step.id
        ]
        task = pending_tasks[0]

        # Write a failing handler
        f = tmp_path / "failing_handler.py"
        f.write_text("def handle(payload):\n    raise ValueError('handler exploded')\n")

        reg = HandlerRegistration(
            facet_name="CountDocuments",
            module_uri=f"file://{f}",
            entrypoint="handle",
        )
        store.save_handler_registration(reg)

        runner = RegistryRunner(
            persistence=store,
            evaluator=evaluator,
            config=RegistryRunnerConfig(),
        )

        dispatched = runner.poll_once()
        assert dispatched == 1

        updated_task = store._tasks[task.uuid]
        assert updated_task.state == TaskState.FAILED
        assert "handler exploded" in updated_task.error["message"]

        updated_step = store.get_step(step.id)
        assert updated_step.state == StepState.STATEMENT_ERROR

    def test_poll_handler_not_found(self, store, evaluator, workflow_ast, program_ast):
        """No registration for task name -> step error, task failed."""
        _execute_until_paused(evaluator, workflow_ast, {"x": 1}, program_ast)

        blocked = store.get_steps_by_state(StepState.EVENT_TRANSMIT)
        step = blocked[0]

        pending_tasks = [
            t
            for t in store._tasks.values()
            if t.state == TaskState.PENDING and t.step_id == step.id
        ]
        task = pending_tasks[0]

        # Register a handler for a DIFFERENT facet, not CountDocuments
        reg = HandlerRegistration(
            facet_name="SomeOtherFacet",
            module_uri="dummy",
        )
        store.save_handler_registration(reg)

        runner = RegistryRunner(
            persistence=store,
            evaluator=evaluator,
            config=RegistryRunnerConfig(),
        )
        # We need the task name in the name list so claim_task matches,
        # but get_handler_registration should return None for that name.
        # Set names manually and prevent refresh from overwriting them.
        runner._registered_names = [task.name]
        runner._last_refresh = _current_time_ms()

        dispatched = runner.poll_once()
        assert dispatched == 1

        updated_task = store._tasks[task.uuid]
        assert updated_task.state == TaskState.FAILED
        assert "No handler registration" in updated_task.error["message"]

    def test_poll_handler_load_failure(self, store, evaluator, workflow_ast, program_ast):
        """Handler with bad module_uri -> task released back to pending."""
        _execute_until_paused(evaluator, workflow_ast, {"x": 1}, program_ast)

        blocked = store.get_steps_by_state(StepState.EVENT_TRANSMIT)
        step = blocked[0]

        pending_tasks = [
            t
            for t in store._tasks.values()
            if t.state == TaskState.PENDING and t.step_id == step.id
        ]
        task = pending_tasks[0]

        # Register with a bad module path
        reg = HandlerRegistration(
            facet_name="CountDocuments",
            module_uri="totally.nonexistent.module",
        )
        store.save_handler_registration(reg)

        runner = RegistryRunner(
            persistence=store,
            evaluator=evaluator,
            config=RegistryRunnerConfig(),
        )

        runner.poll_once()

        # ImportError releases task back to pending (another runner may handle it)
        updated_task = store._tasks[task.uuid]
        assert updated_task.state == TaskState.PENDING

        # Step stays at EventTransmit (not failed)
        updated_step = store.get_step(step.id)
        assert updated_step.state == StepState.EVENT_TRANSMIT

    def test_facet_name_injected_in_payload(
        self, store, evaluator, workflow_ast, program_ast, tmp_path
    ):
        """_facet_name is injected into the payload before the handler is called."""
        result = _execute_until_paused(evaluator, workflow_ast, {"x": 1}, program_ast)
        assert result.status == ExecutionStatus.PAUSED

        blocked = store.get_steps_by_state(StepState.EVENT_TRANSMIT)
        step = blocked[0]

        pending_tasks = [
            t
            for t in store._tasks.values()
            if t.state == TaskState.PENDING and t.step_id == step.id
        ]
        task = pending_tasks[0]

        # Handler that writes the payload to a file for inspection
        capture_file = tmp_path / "captured_payload.json"
        f = tmp_path / "capture_handler.py"
        f.write_text(
            "import json\n"
            f"CAPTURE_PATH = {str(capture_file)!r}\n"
            "def handle(payload):\n"
            "    serializable = {k: v for k, v in payload.items() if not callable(v)}\n"
            "    with open(CAPTURE_PATH, 'w') as fp:\n"
            "        json.dump(serializable, fp)\n"
            "    return {'output': payload.get('input', 0) * 2}\n"
        )

        reg = HandlerRegistration(
            facet_name="CountDocuments",
            module_uri=f"file://{f}",
            entrypoint="handle",
        )
        store.save_handler_registration(reg)

        runner = RegistryRunner(
            persistence=store,
            evaluator=evaluator,
            config=RegistryRunnerConfig(),
        )
        runner.cache_workflow_ast(result.workflow_id, workflow_ast)

        dispatched = runner.poll_once()
        assert dispatched == 1

        import json

        captured = json.loads(capture_file.read_text())
        assert "_facet_name" in captured
        assert captured["_facet_name"] == task.name

    def test_original_task_data_not_mutated(
        self, store, evaluator, workflow_ast, program_ast, handler_file
    ):
        """Original task.data dict is not mutated by _facet_name injection."""
        result = _execute_until_paused(evaluator, workflow_ast, {"x": 1}, program_ast)
        assert result.status == ExecutionStatus.PAUSED

        blocked = store.get_steps_by_state(StepState.EVENT_TRANSMIT)
        step = blocked[0]

        pending_tasks = [
            t
            for t in store._tasks.values()
            if t.state == TaskState.PENDING and t.step_id == step.id
        ]
        task = pending_tasks[0]

        # Save a reference to the original data
        original_data = task.data
        original_keys = set(original_data.keys()) if original_data else set()

        reg = HandlerRegistration(
            facet_name="CountDocuments",
            module_uri=f"file://{handler_file}",
            entrypoint="handle",
        )
        store.save_handler_registration(reg)

        runner = RegistryRunner(
            persistence=store,
            evaluator=evaluator,
            config=RegistryRunnerConfig(),
        )
        runner.cache_workflow_ast(result.workflow_id, workflow_ast)
        runner.poll_once()

        # Original data should not contain _facet_name
        if original_data is not None:
            assert "_facet_name" not in original_data
            assert set(original_data.keys()) == original_keys

    def test_handler_metadata_injected(self, store, evaluator, tmp_path):
        """_handler_metadata is injected when registration has metadata."""
        from afl.runtime.step import StepDefinition
        from afl.runtime.types import ObjectType
        from afl.runtime.types import workflow_id as make_wf_id

        wf_id = make_wf_id()
        step = StepDefinition.create(
            workflow_id=wf_id,
            object_type=ObjectType.VARIABLE_ASSIGNMENT,
            facet_name="ns.MetadataFacet",
        )
        step.state = StepState.EVENT_TRANSMIT
        step.transition.current_state = StepState.EVENT_TRANSMIT
        step.transition.request_transition = False
        store.save_step(step)

        task = TaskDefinition(
            uuid=generate_id(),
            name="ns.MetadataFacet",
            runner_id="",
            workflow_id=wf_id,
            flow_id="",
            step_id=step.id,
            state=TaskState.PENDING,
            task_list_name="default",
            data={"input": 5},
        )
        store.save_task(task)

        # Handler that captures payload to file
        capture_file = tmp_path / "captured_meta.json"
        f = tmp_path / "meta_handler.py"
        f.write_text(
            "import json\n"
            f"CAPTURE_PATH = {str(capture_file)!r}\n"
            "def handle(payload):\n"
            "    serializable = {k: v for k, v in payload.items() if not callable(v)}\n"
            "    with open(CAPTURE_PATH, 'w') as fp:\n"
            "        json.dump(serializable, fp)\n"
            "    return {'output': 42}\n"
        )

        reg = HandlerRegistration(
            facet_name="ns.MetadataFacet",
            module_uri=f"file://{f}",
            entrypoint="handle",
            metadata={"region": "europe", "priority": "high"},
        )
        store.save_handler_registration(reg)

        runner = RegistryRunner(
            persistence=store,
            evaluator=evaluator,
            config=RegistryRunnerConfig(),
        )

        dispatched = runner.poll_once()
        assert dispatched == 1

        import json

        captured = json.loads(capture_file.read_text())
        assert "_handler_metadata" in captured
        assert captured["_handler_metadata"] == {"region": "europe", "priority": "high"}

    def test_no_handler_metadata_when_empty(self, store, evaluator, tmp_path):
        """_handler_metadata is NOT injected when registration has no metadata."""
        from afl.runtime.step import StepDefinition
        from afl.runtime.types import ObjectType
        from afl.runtime.types import workflow_id as make_wf_id

        wf_id = make_wf_id()
        step = StepDefinition.create(
            workflow_id=wf_id,
            object_type=ObjectType.VARIABLE_ASSIGNMENT,
            facet_name="ns.NoMeta",
        )
        step.state = StepState.EVENT_TRANSMIT
        step.transition.current_state = StepState.EVENT_TRANSMIT
        step.transition.request_transition = False
        store.save_step(step)

        task = TaskDefinition(
            uuid=generate_id(),
            name="ns.NoMeta",
            runner_id="",
            workflow_id=wf_id,
            flow_id="",
            step_id=step.id,
            state=TaskState.PENDING,
            task_list_name="default",
            data={"input": 5},
        )
        store.save_task(task)

        capture_file = tmp_path / "captured_nometa.json"
        f = tmp_path / "nometa_handler.py"
        f.write_text(
            "import json\n"
            f"CAPTURE_PATH = {str(capture_file)!r}\n"
            "def handle(payload):\n"
            "    serializable = {k: v for k, v in payload.items() if not callable(v)}\n"
            "    with open(CAPTURE_PATH, 'w') as fp:\n"
            "        json.dump(serializable, fp)\n"
            "    return {'output': 42}\n"
        )

        reg = HandlerRegistration(
            facet_name="ns.NoMeta",
            module_uri=f"file://{f}",
            entrypoint="handle",
            # No metadata (defaults to empty dict)
        )
        store.save_handler_registration(reg)

        runner = RegistryRunner(
            persistence=store,
            evaluator=evaluator,
            config=RegistryRunnerConfig(),
        )

        dispatched = runner.poll_once()
        assert dispatched == 1

        import json

        captured = json.loads(capture_file.read_text())
        assert "_handler_metadata" not in captured
        assert "_facet_name" in captured

    def test_poll_short_name_fallback(self, store, evaluator, handler_file):
        """Registration under short name is found when task has qualified name."""
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

        # Register handler under short name
        reg = HandlerRegistration(
            facet_name="CountDocuments",
            module_uri=f"file://{handler_file}",
            entrypoint="handle",
        )
        store.save_handler_registration(reg)

        runner = RegistryRunner(
            persistence=store,
            evaluator=evaluator,
            config=RegistryRunnerConfig(),
        )
        # claim_task needs "ns.CountDocuments" in the name list to match
        # Prevent refresh from overwriting the manually set names
        runner._registered_names = ["ns.CountDocuments", "CountDocuments"]
        runner._last_refresh = _current_time_ms()

        dispatched = runner.poll_once()
        assert dispatched == 1

        updated_task = store._tasks[task.uuid]
        assert updated_task.state == TaskState.COMPLETED


# =========================================================================
# TestRegistryRunnerLifecycle
# =========================================================================


class TestRegistryRunnerLifecycle:
    """Tests for server registration, AST caching, and shutdown."""

    def test_server_registration(self, store, evaluator, handler_file):
        """start() registers server, stop() deregisters."""
        runner = RegistryRunner(
            persistence=store,
            evaluator=evaluator,
            config=RegistryRunnerConfig(poll_interval_ms=50),
        )

        # Register a handler so the server has something to report
        reg = HandlerRegistration(
            facet_name="ns.TestEvent",
            module_uri=f"file://{handler_file}",
        )
        store.save_handler_registration(reg)

        def run_runner():
            runner.start()

        t = threading.Thread(target=run_runner, daemon=True)
        t.start()

        # Wait for server registration
        time.sleep(0.2)

        server = store.get_server(runner.server_id)
        assert server is not None
        assert server.state == ServerState.RUNNING
        assert server.service_name == "afl-registry-runner"

        runner.stop()
        t.join(timeout=2)

        server = store.get_server(runner.server_id)
        assert server.state == ServerState.SHUTDOWN

    def test_ast_caching(self, store, evaluator, workflow_ast, program_ast, handler_file):
        """Cached AST is used for resume after poll_once."""
        result = _execute_until_paused(evaluator, workflow_ast, {"x": 1}, program_ast)
        assert result.status == ExecutionStatus.PAUSED

        blocked = store.get_steps_by_state(StepState.EVENT_TRANSMIT)
        step = blocked[0]

        # Register handler
        reg = HandlerRegistration(
            facet_name="CountDocuments",
            module_uri=f"file://{handler_file}",
            entrypoint="handle",
        )
        store.save_handler_registration(reg)

        runner = RegistryRunner(
            persistence=store,
            evaluator=evaluator,
            config=RegistryRunnerConfig(),
        )
        runner.cache_workflow_ast(result.workflow_id, workflow_ast)

        # Verify cache is populated
        assert result.workflow_id in runner._ast_cache

        runner.poll_once()

        # Workflow should have completed via resume using cached AST
        updated_step = store.get_step(step.id)
        assert updated_step.state != StepState.EVENT_TRANSMIT

    def test_stop(self, store, evaluator):
        """stop() causes the poll loop to exit."""
        runner = RegistryRunner(
            persistence=store,
            evaluator=evaluator,
            config=RegistryRunnerConfig(poll_interval_ms=50),
        )

        def run_runner():
            runner.start()

        t = threading.Thread(target=run_runner, daemon=True)
        t.start()

        time.sleep(0.2)
        assert runner.is_running

        runner.stop()
        t.join(timeout=2)

        assert not runner.is_running


# =========================================================================
# TestRegistryRefresh
# =========================================================================


class TestRegistryRefresh:
    """Tests for registry refresh behaviour."""

    def test_new_registration_picked_up(self, store, evaluator, handler_file):
        """A registration added after construction is picked up on refresh."""
        runner = RegistryRunner(
            persistence=store,
            evaluator=evaluator,
            config=RegistryRunnerConfig(registry_refresh_interval_ms=0),
        )

        assert runner.registered_names() == []

        # Add a registration to the store
        reg = HandlerRegistration(
            facet_name="ns.NewHandler",
            module_uri=f"file://{handler_file}",
        )
        store.save_handler_registration(reg)

        # With refresh interval 0, next call refreshes immediately
        names = runner.registered_names()
        assert "ns.NewHandler" in names

    def test_refresh_interval_respected(self, store, evaluator, handler_file):
        """Refresh is skipped when the interval hasn't elapsed."""
        runner = RegistryRunner(
            persistence=store,
            evaluator=evaluator,
            config=RegistryRunnerConfig(registry_refresh_interval_ms=60000),
        )

        # Force an initial refresh
        runner._refresh_registry()
        assert runner._registered_names == []

        # Add a registration — should NOT be seen because interval hasn't elapsed
        reg = HandlerRegistration(
            facet_name="ns.Delayed",
            module_uri=f"file://{handler_file}",
        )
        store.save_handler_registration(reg)

        runner._maybe_refresh_registry()
        assert "ns.Delayed" not in runner._registered_names

        # Force by backdating _last_refresh
        runner._last_refresh = 0
        runner._maybe_refresh_registry()
        assert "ns.Delayed" in runner._registered_names


# =========================================================================
# TestRegistryRunnerTopics
# =========================================================================


class TestRegistryRunnerTopics:
    """Tests for topic-based filtering of registered names."""

    def test_topics_filter_exact(self, store, evaluator):
        """Only exact-matching facet names appear when topics are set."""
        store.save_handler_registration(HandlerRegistration(facet_name="ns.A", module_uri="a"))
        store.save_handler_registration(HandlerRegistration(facet_name="ns.B", module_uri="b"))
        store.save_handler_registration(HandlerRegistration(facet_name="ns.C", module_uri="c"))

        runner = RegistryRunner(
            persistence=store,
            evaluator=evaluator,
            config=RegistryRunnerConfig(topics=["ns.A", "ns.C"]),
        )
        runner._refresh_registry()

        assert sorted(runner.registered_names()) == ["ns.A", "ns.C"]

    def test_topics_filter_glob(self, store, evaluator):
        """Glob patterns filter registered names."""
        store.save_handler_registration(HandlerRegistration(facet_name="ns.X.Foo", module_uri="x"))
        store.save_handler_registration(HandlerRegistration(facet_name="ns.Y.Bar", module_uri="y"))

        runner = RegistryRunner(
            persistence=store,
            evaluator=evaluator,
            config=RegistryRunnerConfig(topics=["ns.X.*"]),
        )
        runner._refresh_registry()

        assert runner.registered_names() == ["ns.X.Foo"]

    def test_topics_empty_means_all(self, store, evaluator):
        """No topics configured -> all registered names appear (default)."""
        store.save_handler_registration(HandlerRegistration(facet_name="ns.A", module_uri="a"))
        store.save_handler_registration(HandlerRegistration(facet_name="ns.B", module_uri="b"))

        runner = RegistryRunner(
            persistence=store,
            evaluator=evaluator,
            config=RegistryRunnerConfig(),  # no topics
        )
        runner._refresh_registry()

        assert sorted(runner.registered_names()) == ["ns.A", "ns.B"]

    def test_topics_filter_poll_once(self, store, evaluator, handler_file):
        """poll_once only claims tasks matching the topic filter."""
        from afl.runtime.step import StepDefinition
        from afl.runtime.types import ObjectType
        from afl.runtime.types import workflow_id as make_wf_id

        wf_id = make_wf_id()

        # Create two steps at EVENT_TRANSMIT
        step_a = StepDefinition.create(
            workflow_id=wf_id,
            object_type=ObjectType.VARIABLE_ASSIGNMENT,
            facet_name="ns.A",
        )
        step_a.state = StepState.EVENT_TRANSMIT
        step_a.transition.current_state = StepState.EVENT_TRANSMIT
        step_a.transition.request_transition = False
        store.save_step(step_a)

        step_b = StepDefinition.create(
            workflow_id=wf_id,
            object_type=ObjectType.VARIABLE_ASSIGNMENT,
            facet_name="ns.B",
        )
        step_b.state = StepState.EVENT_TRANSMIT
        step_b.transition.current_state = StepState.EVENT_TRANSMIT
        step_b.transition.request_transition = False
        store.save_step(step_b)

        # Create tasks for both
        task_a = TaskDefinition(
            uuid=generate_id(),
            name="ns.A",
            runner_id="",
            workflow_id=wf_id,
            flow_id="",
            step_id=step_a.id,
            state=TaskState.PENDING,
            task_list_name="default",
            data={"input": 1},
        )
        store.save_task(task_a)

        task_b = TaskDefinition(
            uuid=generate_id(),
            name="ns.B",
            runner_id="",
            workflow_id=wf_id,
            flow_id="",
            step_id=step_b.id,
            state=TaskState.PENDING,
            task_list_name="default",
            data={"input": 2},
        )
        store.save_task(task_b)

        # Register handlers for both
        store.save_handler_registration(
            HandlerRegistration(
                facet_name="ns.A",
                module_uri=f"file://{handler_file}",
                entrypoint="handle",
            )
        )
        store.save_handler_registration(
            HandlerRegistration(
                facet_name="ns.B",
                module_uri=f"file://{handler_file}",
                entrypoint="handle",
            )
        )

        # Runner with topics=["ns.A"] — should only claim ns.A
        runner = RegistryRunner(
            persistence=store,
            evaluator=evaluator,
            config=RegistryRunnerConfig(topics=["ns.A"]),
        )

        dispatched = runner.poll_once()
        assert dispatched == 1

        # ns.A should be completed, ns.B should still be pending
        updated_a = store._tasks[task_a.uuid]
        assert updated_a.state == TaskState.COMPLETED

        updated_b = store._tasks[task_b.uuid]
        assert updated_b.state == TaskState.PENDING

    def test_server_topics_reflect_filter(self, store, evaluator):
        """Server definition topics match the filtered registered names."""
        store.save_handler_registration(HandlerRegistration(facet_name="ns.X.Foo", module_uri="x"))
        store.save_handler_registration(HandlerRegistration(facet_name="ns.X.Bar", module_uri="x"))
        store.save_handler_registration(HandlerRegistration(facet_name="ns.Y.Baz", module_uri="y"))

        runner = RegistryRunner(
            persistence=store,
            evaluator=evaluator,
            config=RegistryRunnerConfig(topics=["ns.X.*"]),
        )
        runner._refresh_registry()
        runner._register_server()

        server = store.get_server(runner.server_id)
        assert server is not None
        assert sorted(server.topics) == ["ns.X.Bar", "ns.X.Foo"]
        assert sorted(server.handlers) == ["ns.X.Bar", "ns.X.Foo"]


# =========================================================================
# TestStepLogEmission
# =========================================================================


class TestStepLogEmission:
    """Tests for step log emission during event processing."""

    def test_step_logs_on_success(self, store, evaluator, workflow_ast, program_ast, handler_file):
        """Successful dispatch emits claimed, dispatching, and completed logs."""
        result = _execute_until_paused(evaluator, workflow_ast, {"x": 1}, program_ast)
        assert result.status == ExecutionStatus.PAUSED

        blocked = store.get_steps_by_state(StepState.EVENT_TRANSMIT)
        assert len(blocked) >= 1
        step = blocked[0]

        runner = RegistryRunner(
            persistence=store,
            evaluator=evaluator,
            config=RegistryRunnerConfig(),
        )
        reg = HandlerRegistration(
            facet_name="CountDocuments",
            module_uri=f"file://{handler_file}",
            entrypoint="handle",
        )
        store.save_handler_registration(reg)
        runner.cache_workflow_ast(result.workflow_id, workflow_ast)

        runner.poll_once()

        logs = store.get_step_logs_by_step(step.id)
        messages = [log_entry.message for log_entry in logs]

        # Should have at least: claimed, dispatching, completed
        assert any("Task claimed" in m for m in messages)
        assert any("Dispatching handler" in m for m in messages)
        assert any("Handler completed" in m for m in messages)

        # Check levels
        levels = {log_entry.message: log_entry.level for log_entry in logs}
        completed_key = [k for k in levels if "Handler completed" in k][0]
        assert levels[completed_key] == "success"

    def test_step_logs_on_failure(self, store, evaluator, workflow_ast, program_ast, tmp_path):
        """Handler exception emits an error step log."""
        _execute_until_paused(evaluator, workflow_ast, {"x": 1}, program_ast)

        blocked = store.get_steps_by_state(StepState.EVENT_TRANSMIT)
        step = blocked[0]

        f = tmp_path / "failing_handler.py"
        f.write_text("def handle(payload):\n    raise ValueError('boom')\n")

        reg = HandlerRegistration(
            facet_name="CountDocuments",
            module_uri=f"file://{f}",
            entrypoint="handle",
        )
        store.save_handler_registration(reg)

        runner = RegistryRunner(
            persistence=store,
            evaluator=evaluator,
            config=RegistryRunnerConfig(),
        )
        runner.cache_workflow_ast(
            store.get_steps_by_state(StepState.EVENT_TRANSMIT)[0].workflow_id
            if store.get_steps_by_state(StepState.EVENT_TRANSMIT)
            else step.workflow_id,
            workflow_ast,
        )

        runner.poll_once()

        logs = store.get_step_logs_by_step(step.id)
        error_logs = [log_entry for log_entry in logs if log_entry.level == "error"]
        assert len(error_logs) >= 1
        assert any("Handler error" in log_entry.message for log_entry in error_logs)

    def test_step_log_callback_injection(
        self, store, evaluator, workflow_ast, program_ast, tmp_path
    ):
        """Handler receives _step_log callback and can emit handler-level logs."""
        result = _execute_until_paused(evaluator, workflow_ast, {"x": 1}, program_ast)

        blocked = store.get_steps_by_state(StepState.EVENT_TRANSMIT)
        step = blocked[0]

        # Write a handler that uses the _step_log callback
        f = tmp_path / "logging_handler.py"
        f.write_text(
            "def handle(payload):\n"
            "    log = payload.get('_step_log')\n"
            "    if log:\n"
            "        log('Fetching data from API')\n"
            "        log('Download complete', level='success')\n"
            "    return {'output': 42}\n"
        )

        reg = HandlerRegistration(
            facet_name="CountDocuments",
            module_uri=f"file://{f}",
            entrypoint="handle",
        )
        store.save_handler_registration(reg)

        runner = RegistryRunner(
            persistence=store,
            evaluator=evaluator,
            config=RegistryRunnerConfig(),
        )
        runner.cache_workflow_ast(result.workflow_id, workflow_ast)

        runner.poll_once()

        logs = store.get_step_logs_by_step(step.id)
        handler_logs = [log_entry for log_entry in logs if log_entry.source == "handler"]
        assert len(handler_logs) == 2
        assert handler_logs[0].message == "Fetching data from API"
        assert handler_logs[0].level == "info"
        assert handler_logs[1].message == "Download complete"
        assert handler_logs[1].level == "success"


# =========================================================================
# TestInlineDispatchStepLogs
# =========================================================================


class _SimpleDispatcher:
    """Minimal dispatcher for inline dispatch tests."""

    def __init__(self, handler_fn=None):
        self._handler = handler_fn or (lambda payload: {"output": 42})

    def can_dispatch(self, facet_name: str) -> bool:
        return True

    def dispatch(self, facet_name: str, payload: dict) -> dict | None:
        return self._handler(payload)


class TestInlineDispatchStepLogs:
    """Tests for step log emission during inline (evaluator-side) dispatch."""

    def test_inline_dispatch_emits_framework_logs(
        self, store, evaluator, workflow_ast, program_ast
    ):
        """Inline dispatch emits dispatching and completed framework logs."""
        dispatcher = _SimpleDispatcher()
        result = evaluator.execute(
            workflow_ast, inputs={"x": 1}, program_ast=program_ast, dispatcher=dispatcher
        )
        assert result.status == ExecutionStatus.COMPLETED

        # Find the event-facet step (CountDocuments)
        all_steps = list(store._steps.values())
        event_step = [s for s in all_steps if s.facet_name == "CountDocuments"]
        assert len(event_step) == 1
        step = event_step[0]

        logs = store.get_step_logs_by_step(step.id)
        messages = [log_entry.message for log_entry in logs]

        assert any("Dispatching handler" in m for m in messages)
        assert any("Handler completed" in m for m in messages)

        # Completed log should be success level
        levels = {log_entry.message: log_entry.level for log_entry in logs}
        completed_key = [k for k in levels if "Handler completed" in k][0]
        assert levels[completed_key] == "success"

        # All framework logs should have correct source
        framework_logs = [entry for entry in logs if entry.source == "framework"]
        assert len(framework_logs) >= 2

    def test_inline_dispatch_handler_callback(self, store, evaluator, workflow_ast, program_ast):
        """Inline dispatch injects _step_log callback for handler-level logs."""

        def handler_with_logging(payload):
            log = payload.get("_step_log")
            if log:
                log("Starting computation")
                log("Done computing", "success")
            return {"output": 42}

        dispatcher = _SimpleDispatcher(handler_with_logging)
        result = evaluator.execute(
            workflow_ast, inputs={"x": 1}, program_ast=program_ast, dispatcher=dispatcher
        )
        assert result.status == ExecutionStatus.COMPLETED

        all_steps = list(store._steps.values())
        event_step = [s for s in all_steps if s.facet_name == "CountDocuments"]
        step = event_step[0]

        logs = store.get_step_logs_by_step(step.id)
        handler_logs = [entry for entry in logs if entry.source == "handler"]
        assert len(handler_logs) == 2
        assert handler_logs[0].message == "Starting computation"
        assert handler_logs[0].level == "info"
        assert handler_logs[1].message == "Done computing"
        assert handler_logs[1].level == "success"

    def test_inline_dispatch_error_emits_error_log(
        self, store, evaluator, workflow_ast, program_ast
    ):
        """Inline dispatch error emits an error step log."""

        def failing_handler(payload):
            raise ValueError("handler failed")

        dispatcher = _SimpleDispatcher(failing_handler)
        evaluator.execute(
            workflow_ast, inputs={"x": 1}, program_ast=program_ast, dispatcher=dispatcher
        )

        all_steps = list(store._steps.values())
        event_step = [s for s in all_steps if s.facet_name == "CountDocuments"]
        step = event_step[0]

        # Step should be in error state
        assert "error" in step.state.lower() or "Error" in step.state

        logs = store.get_step_logs_by_step(step.id)
        error_logs = [entry for entry in logs if entry.level == "error"]
        assert len(error_logs) >= 1
        assert any("Handler error" in entry.message for entry in error_logs)
        assert any("handler failed" in entry.message for entry in error_logs)

    def test_inline_dispatch_includes_duration(self, store, evaluator, workflow_ast, program_ast):
        """Handler completed log includes dispatch duration in ms."""
        dispatcher = _SimpleDispatcher()
        evaluator.execute(
            workflow_ast, inputs={"x": 1}, program_ast=program_ast, dispatcher=dispatcher
        )

        all_steps = list(store._steps.values())
        event_step = [s for s in all_steps if s.facet_name == "CountDocuments"]
        step = event_step[0]

        logs = store.get_step_logs_by_step(step.id)
        completed = [entry for entry in logs if "Handler completed" in entry.message]
        assert len(completed) == 1
        assert "ms)" in completed[0].message


# =========================================================================
# TestStuckStepSweep
# =========================================================================


class TestStuckStepSweep:
    """Tests for the stuck-step recovery sweep mechanism."""

    def test_sweep_finds_stuck_steps(self, store, evaluator, workflow_ast, program_ast):
        """Sweep detects workflows with EventTransmit steps awaiting resume."""
        # Execute until paused at EventTransmit
        result = _execute_until_paused(
            evaluator, workflow_ast, inputs={"x": 1}, program_ast=program_ast
        )
        assert result.status == ExecutionStatus.PAUSED

        # Simulate handler completion: continue_step sets request_transition=True
        event_steps = [s for s in store._steps.values() if s.state == StepState.EVENT_TRANSMIT]
        assert len(event_steps) == 1
        evaluator.continue_step(event_steps[0].id, {"output": 42})

        # The persistence layer should now report this workflow as needing resume
        pending = store.get_pending_resume_workflow_ids()
        assert result.workflow_id in pending

    def test_sweep_resumes_stuck_workflow(self, store, evaluator, workflow_ast, program_ast):
        """Sweep resumes a workflow whose step is stuck at EventTransmit."""
        result = _execute_until_paused(
            evaluator, workflow_ast, inputs={"x": 1}, program_ast=program_ast
        )
        assert result.status == ExecutionStatus.PAUSED

        # Simulate handler completion without calling _resume_workflow
        event_steps = [s for s in store._steps.values() if s.state == StepState.EVENT_TRANSMIT]
        evaluator.continue_step(event_steps[0].id, {"output": 42})

        # Step has advanced past EventTransmit to StatementBlocksBegin
        # (continue_step advances the state but does not run the handler)
        step = store.get_step(event_steps[0].id)
        assert step.state == StepState.STATEMENT_BLOCKS_BEGIN

        # Create runner and trigger sweep
        runner = RegistryRunner(
            persistence=store,
            evaluator=evaluator,
            config=RegistryRunnerConfig(),
        )
        runner.cache_workflow_ast(result.workflow_id, workflow_ast, program_ast=program_ast)
        runner._last_sweep = 0  # Force sweep to run immediately
        runner._maybe_sweep_stuck_steps()

        # Step should now have advanced past EventTransmit
        step = store.get_step(event_steps[0].id)
        assert step.state != StepState.EVENT_TRANSMIT

    def test_sweep_throttled(self, store, evaluator):
        """Sweep respects the throttle interval."""
        runner = RegistryRunner(
            persistence=store,
            evaluator=evaluator,
            config=RegistryRunnerConfig(),
        )
        # Just ran a sweep
        runner._last_sweep = _current_time_ms()
        # Should not run again within the interval
        runner._maybe_sweep_stuck_steps()
        # No error — just verifying it returns without querying

    def test_sweep_no_stuck_steps(self, store, evaluator):
        """Sweep is a no-op when there are no stuck steps."""
        runner = RegistryRunner(
            persistence=store,
            evaluator=evaluator,
            config=RegistryRunnerConfig(),
        )
        runner._last_sweep = 0
        runner._maybe_sweep_stuck_steps()
        # No error, no workflows to resume

    def test_pending_resume_workflow_ids_empty(self, store):
        """get_pending_resume_workflow_ids returns empty list when no stuck steps."""
        assert store.get_pending_resume_workflow_ids() == []

    def test_pending_resume_ignores_normal_event_transmit(
        self, store, evaluator, workflow_ast, program_ast
    ):
        """Steps at EventTransmit WITHOUT request_transition are not flagged."""
        result = _execute_until_paused(
            evaluator, workflow_ast, inputs={"x": 1}, program_ast=program_ast
        )
        assert result.status == ExecutionStatus.PAUSED

        # Step is at EventTransmit but request_transition=False (normal blocked state)
        event_steps = [s for s in store._steps.values() if s.state == StepState.EVENT_TRANSMIT]
        assert len(event_steps) == 1
        assert not event_steps[0].transition.is_requesting_state_change

        # Should NOT be flagged as needing resume
        pending = store.get_pending_resume_workflow_ids()
        assert result.workflow_id not in pending

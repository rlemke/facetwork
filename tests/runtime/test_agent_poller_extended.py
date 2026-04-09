"""Extended tests for AgentPoller — AST loading, resume, handler-not-found, shutdown."""

from dataclasses import dataclass
from unittest.mock import MagicMock, patch

import pytest

from facetwork.runtime.agent_poller import AgentPoller, AgentPollerConfig
from facetwork.runtime.entities import TaskState


@dataclass
class FakeTask:
    uuid: str = "t-1"
    name: str = "ns.DoWork"
    step_id: str = "s-1"
    workflow_id: str = "wf-1"
    runner_id: str = ""
    data: dict = None
    state: str = "running"
    error: dict = None
    updated: int = 0

    def __post_init__(self):
        if self.data is None:
            self.data = {"input": "test"}


@pytest.fixture
def persistence():
    store = MagicMock()
    store.claim_task.return_value = None
    store.save_server = MagicMock()
    store.get_server = MagicMock(return_value=MagicMock())
    store.save_task = MagicMock()
    return store


@pytest.fixture
def evaluator():
    return MagicMock()


@pytest.fixture
def poller(persistence, evaluator):
    config = AgentPollerConfig(
        service_name="test-agent",
        poll_interval_ms=100,
        max_concurrent=2,
    )
    return AgentPoller(persistence=persistence, evaluator=evaluator, config=config)


class TestLoadWorkflowAST:
    """Test _load_workflow_ast branches."""

    def test_no_get_workflow_method(self, poller):
        del poller._persistence.get_workflow
        result = poller._load_workflow_ast("wf-1")
        assert result is None

    def test_workflow_not_found(self, poller):
        poller._persistence.get_workflow.return_value = None
        result = poller._load_workflow_ast("wf-1")
        assert result is None

    def test_no_get_flow_method(self, poller):
        wf = MagicMock()
        wf.flow_id = "f-1"
        poller._persistence.get_workflow.return_value = wf
        del poller._persistence.get_flow
        result = poller._load_workflow_ast("wf-1")
        assert result is None

    def test_flow_not_found(self, poller):
        wf = MagicMock()
        wf.flow_id = "f-1"
        poller._persistence.get_workflow.return_value = wf
        poller._persistence.get_flow.return_value = None
        result = poller._load_workflow_ast("wf-1")
        assert result is None

    def test_flow_no_compiled_sources(self, poller):
        wf = MagicMock()
        wf.flow_id = "f-1"
        poller._persistence.get_workflow.return_value = wf
        flow = MagicMock()
        flow.compiled_ast = None
        flow.compiled_sources = []
        poller._persistence.get_flow.return_value = flow
        result = poller._load_workflow_ast("wf-1")
        assert result is None

    def test_exception_returns_none(self, poller):
        poller._persistence.get_workflow.side_effect = Exception("db error")
        result = poller._load_workflow_ast("wf-1")
        assert result is None

    def test_success_returns_workflow_ast(self, poller):
        import json

        from facetwork.emitter import JSONEmitter
        from facetwork.parser import FFLParser

        wf = MagicMock()
        wf.flow_id = "f-1"
        wf.name = "TestWF"
        poller._persistence.get_workflow.return_value = wf

        # Build a real compiled AST
        source = "workflow TestWF(x: String) => (output: String)"
        parser = FFLParser()
        ast = parser.parse(source)
        emitter = JSONEmitter(include_locations=False)
        program_dict = json.loads(emitter.emit(ast))

        flow = MagicMock()
        flow.compiled_ast = program_dict
        flow.compiled_sources = []
        poller._persistence.get_flow.return_value = flow

        result = poller._load_workflow_ast("wf-1")
        assert result is not None
        assert result["name"] == "TestWF"


class TestResumeWorkflow:
    """Test _resume_workflow with various AST states."""

    def test_resume_with_cached_ast(self, poller, evaluator):
        ast = {"name": "TestWF", "body": {}}
        poller.cache_workflow_ast("wf-1", ast)

        poller._resume_workflow("wf-1")
        evaluator.resume.assert_called_once_with("wf-1", ast, program_ast=None, runner_id="")

    def test_resume_with_cached_program_ast(self, poller, evaluator):
        ast = {"name": "TestWF", "body": {}}
        prog = {"type": "Program", "declarations": []}
        poller.cache_workflow_ast("wf-1", ast, program_ast=prog)

        poller._resume_workflow("wf-1")
        evaluator.resume.assert_called_once_with("wf-1", ast, program_ast=prog, runner_id="")

    def test_resume_no_ast_logs_warning(self, poller, evaluator):
        # No cached AST and _load_workflow_ast returns None
        with patch.object(poller, "_load_workflow_ast", return_value=None):
            poller._resume_workflow("wf-1")

        evaluator.resume.assert_not_called()

    def test_resume_loads_and_caches_ast(self, poller, evaluator):
        ast = {"name": "WF", "body": {}}
        with patch.object(poller, "_load_workflow_ast", return_value=ast):
            poller._resume_workflow("wf-1")

        evaluator.resume.assert_called_once_with("wf-1", ast, program_ast=None, runner_id="")
        assert poller._ast_cache["wf-1"] is ast


class TestProcessEventHandlerNotFound:
    """Test _process_event when no handler matches."""

    def test_no_handler_fails_step(self, poller, evaluator, persistence):
        task = FakeTask(name="ns.Unknown")

        poller._process_event(task)

        evaluator.fail_step.assert_called_once_with("s-1", "No handler for event task 'ns.Unknown'")
        persistence.save_task.assert_called_once()
        assert task.state == TaskState.FAILED
        assert "No handler" in task.error["message"]

    def test_no_handler_short_name_fallback(self, poller, evaluator, persistence):
        """Short name fallback should be tried before failing."""

        def handler(payload):
            return {"ok": True}

        poller.register("DoWork", handler)

        task = FakeTask(name="ns.DoWork")

        with patch.object(poller, "_resume_workflow"):
            poller._process_event(task)

        evaluator.continue_step.assert_called_once_with("s-1", {"ok": True})
        assert task.state == TaskState.COMPLETED


class TestProcessEventCallbackError:
    """Test _process_event when callback raises."""

    def test_callback_exception_fails_step(self, poller, evaluator, persistence):
        def bad_handler(payload):
            raise RuntimeError("processing failed")

        poller.register("ns.DoWork", bad_handler)
        task = FakeTask()

        poller._process_event(task)

        evaluator.fail_step.assert_called_once_with("s-1", "processing failed")
        assert task.state == TaskState.FAILED
        assert "processing failed" in task.error["message"]

    def test_callback_error_and_fail_step_error(self, poller, evaluator, persistence):
        """If both callback and fail_step raise, task should still be marked failed."""

        def bad_handler(payload):
            raise RuntimeError("callback error")

        poller.register("ns.DoWork", bad_handler)
        evaluator.fail_step.side_effect = Exception("fail_step also failed")

        task = FakeTask()
        poller._process_event(task)

        assert task.state == TaskState.FAILED
        persistence.save_task.assert_called()


class TestRegistration:
    """Test handler registration."""

    def test_register_and_list(self, poller):
        poller.register("ns.A", lambda p: {})
        poller.register("ns.B", lambda p: {})

        names = poller.registered_names()
        assert "ns.A" in names
        assert "ns.B" in names

    def test_poll_once_no_handlers(self, poller):
        result = poller.poll_once()
        assert result == 0


class TestCacheWorkflowAST:
    """Test AST caching."""

    def test_cache_and_retrieve(self, poller):
        ast = {"name": "WF"}
        poller.cache_workflow_ast("wf-1", ast)
        assert poller._ast_cache["wf-1"] is ast


class TestShutdown:
    """Test _shutdown."""

    def test_shutdown_deregisters_server(self, poller, persistence):
        poller._running = True
        poller._shutdown()

        assert not poller._running
        persistence.get_server.assert_called_once()

    def test_shutdown_handles_deregister_error(self, poller, persistence):
        poller._running = True
        persistence.get_server.side_effect = Exception("db error")

        # Should not raise
        poller._shutdown()
        assert not poller._running

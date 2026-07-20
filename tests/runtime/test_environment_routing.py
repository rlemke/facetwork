"""Environment claim routing — IDENTICAL assertions against BOTH stores.

lessons-learned §23: memory-vs-mongo parity gaps hide distributed bugs.
Every behavior here runs against MemoryStore and MongoStore via one
parametrized fixture; divergence is a test failure, not a fleet incident.
"""

from __future__ import annotations

import pytest

from facetwork.runtime.entities import TaskDefinition, TaskState
from facetwork.runtime.memory_store import MemoryStore

try:
    import mongomock

    MONGOMOCK = True
except ImportError:
    MONGOMOCK = False


@pytest.fixture(params=["memory", "mongo"])
def store(request):
    if request.param == "memory":
        yield MemoryStore()
        return
    if not MONGOMOCK:
        pytest.skip("mongomock not installed")
    from facetwork.runtime.mongo_store import MongoStore

    s = MongoStore(database_name="afl_test_envs", client=mongomock.MongoClient())
    yield s
    s.drop_database()
    s.close()


def _task(uuid, name="geo.Cluster", env="", kind="", task_list="geo"):
    return TaskDefinition(
        uuid=uuid,
        name=name,
        runner_id="r1",
        workflow_id="wf1",
        flow_id="",
        step_id=f"step-{uuid}",
        state=TaskState.PENDING,
        task_list_name=task_list,
        environment_hash=env,
        kind=kind,
    )


class TestEnvironmentClaimRouting:
    def test_untagged_task_claimable_by_anyone(self, store):
        store.save_task(_task("t1"))
        got = store.claim_task(["geo.Cluster"], task_list="geo")
        assert got is not None and got.uuid == "t1"

    def test_tagged_task_invisible_without_env(self, store):
        store.save_task(_task("t2", env="abc123"))
        assert store.claim_task(["geo.Cluster"], task_list="geo") is None

    def test_tagged_task_claimable_with_matching_env(self, store):
        store.save_task(_task("t3", env="abc123"))
        got = store.claim_task(
            ["geo.Cluster"], task_list="geo", provided_environments=["abc123"]
        )
        assert got is not None and got.uuid == "t3"

    def test_tagged_task_invisible_with_wrong_env(self, store):
        store.save_task(_task("t4", env="abc123"))
        assert (
            store.claim_task(
                ["geo.Cluster"], task_list="geo", provided_environments=["other"]
            )
            is None
        )

    def test_untagged_still_claimable_when_envs_provided(self, store):
        store.save_task(_task("t5"))
        got = store.claim_task(
            ["geo.Cluster"], task_list="geo", provided_environments=["abc123"]
        )
        assert got is not None and got.uuid == "t5"

    def test_environment_hash_roundtrips(self, store):
        store.save_task(_task("t6", env="abc123", kind="script"))
        got = store.get_task("t6")
        assert got.environment_hash == "abc123"
        assert got.kind == "script"


class TestScriptTaskClaiming:
    def test_script_task_claimed_by_env_not_name(self, store):
        # Script tasks are named by facet — in NO runner's handler set. The
        # dedicated claim ignores names and lists: environment IS capability.
        store.save_task(_task("s1", name="geo.Cluster", env="abc123", kind="script"))
        assert store.claim_script_task(["other"]) is None
        got = store.claim_script_task(["abc123"], server_id="srv-9")
        assert got is not None and got.uuid == "s1"
        assert got.state == TaskState.RUNNING
        assert got.server_id == "srv-9"

    def test_script_claim_ignores_handler_tasks(self, store):
        store.save_task(_task("s2", env="abc123", kind=""))  # env-tagged handler task
        assert store.claim_script_task(["abc123"]) is None

    def test_script_claim_with_no_envs_returns_none(self, store):
        store.save_task(_task("s3", env="abc123", kind="script"))
        assert store.claim_script_task([]) is None

    def test_script_claim_respects_retry_backoff(self, store):
        t = _task("s4", env="abc123", kind="script")
        t.next_retry_after = 32503680000000  # year 3000
        store.save_task(t)
        assert store.claim_script_task(["abc123"]) is None


class TestEnvScriptExecution:
    """End-to-end: an env-bound script facet defers past inline execution,
    lands as an env-routed script task, and is executed by a runner that
    provides the environment — under that environment's interpreter."""

    def test_env_script_executes_on_providing_runner(self, tmp_path, monkeypatch):
        import json
        import os
        import sys

        from facetwork import parse
        from facetwork.ast_utils import find_workflow
        from facetwork.emitter import JSONEmitter
        from facetwork.environments import annotate_program
        from facetwork.runtime import Evaluator, Telemetry
        from facetwork.runtime.agent import ToolRegistry
        from facetwork.runtime.evaluator import ExecutionStatus
        from facetwork.runtime.runner import RunnerConfig, RunnerService

        src = """
        namespace demo {
            environment PyLocal {
                language = "python"
            }
            facet Doubler(x: Long) => (y: Long)
                in environment PyLocal
                script { result['y'] = params['x'] * 2 }
            workflow W(x: Long) => (y: Long) andThen {
                s = Doubler(x = $.x)
                yield W(y = s.y)
            }
        }
        """
        program = json.loads(JSONEmitter(include_locations=False).emit(parse(src)))
        hashes = annotate_program(program)
        env_hash = hashes["demo.PyLocal"]

        # "Materialize" the environment: its interpreter is this python.
        bin_dir = tmp_path / env_hash / "bin"
        bin_dir.mkdir(parents=True)
        os.symlink(sys.executable, bin_dir / "python")
        monkeypatch.setenv("FW_ENV_ROOT", str(tmp_path))

        store = MemoryStore()
        evaluator = Evaluator(persistence=store, telemetry=Telemetry(enabled=False))
        workflow_ast = find_workflow(program, "demo.W")
        result = evaluator.execute(
            workflow_ast, inputs={"x": 21}, program_ast=program, runner_id="run-1"
        )
        assert result.status == ExecutionStatus.PAUSED  # blocked on the script task

        # Runner snapshot (as fw ffl run / fw:execute create in production) —
        # the claiming runner loads program_ast from it.
        from facetwork.runtime.entities import RunnerDefinition, WorkflowDefinition

        store.save_runner(
            RunnerDefinition(
                uuid="run-1",
                workflow_id=result.workflow_id,
                workflow=WorkflowDefinition(uuid="wfdef-1", name="demo.W", namespace_id="", facet_id="", flow_id="", starting_step="", version="1"),
                compiled_ast=program,
                workflow_ast=workflow_ast,
            )
        )

        tasks = [t for t in store._tasks.values() if t.kind == "script"]
        assert len(tasks) == 1
        assert tasks[0].environment_hash == env_hash
        assert tasks[0].name == "demo.Doubler"

        svc = RunnerService(store, evaluator, RunnerConfig(), ToolRegistry())
        assert env_hash in svc._provided_environments()  # discovered from FW_ENV_ROOT
        for _ in range(20):
            svc.run_once()
            root = next(
                s for s in store._steps.values()
                if s.object_type == "Workflow"
            )
            if root.state.endswith("Complete"):
                break
        assert root.state.endswith("Complete"), root.state
        assert root.get_attribute("y") == 42
        done = store.get_task(tasks[0].uuid)
        assert done.state == TaskState.COMPLETED

    def test_env_script_not_executed_by_non_provider(self, tmp_path, monkeypatch):
        import json

        from facetwork import parse
        from facetwork.ast_utils import find_workflow
        from facetwork.emitter import JSONEmitter
        from facetwork.environments import annotate_program
        from facetwork.runtime import Evaluator, Telemetry
        from facetwork.runtime.agent import ToolRegistry
        from facetwork.runtime.runner import RunnerConfig, RunnerService

        monkeypatch.setenv("FW_ENV_ROOT", str(tmp_path / "empty"))
        src = """
        namespace demo {
            environment PyLocal { language = "python" }
            facet Doubler(x: Long) => (y: Long)
                in environment PyLocal
                script { result['y'] = params['x'] * 2 }
            workflow W(x: Long) => (y: Long) andThen {
                s = Doubler(x = $.x)
                yield W(y = s.y)
            }
        }
        """
        program = json.loads(JSONEmitter(include_locations=False).emit(parse(src)))
        annotate_program(program)
        store = MemoryStore()
        evaluator = Evaluator(persistence=store, telemetry=Telemetry(enabled=False))
        evaluator.execute(
            find_workflow(program, "demo.W"), inputs={"x": 1},
            program_ast=program, runner_id="run-1",
        )
        svc = RunnerService(store, evaluator, RunnerConfig(), ToolRegistry())
        assert svc._provided_environments() == []
        dispatched = svc.run_once()
        # The script task stays pending — this runner cannot claim it.
        task = next(t for t in store._tasks.values() if t.kind == "script")
        assert task.state == TaskState.PENDING

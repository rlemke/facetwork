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
        got = store.claim_task(["geo.Cluster"], task_list="geo", provided_environments=["abc123"])
        assert got is not None and got.uuid == "t3"

    def test_tagged_task_invisible_with_wrong_env(self, store):
        store.save_task(_task("t4", env="abc123"))
        assert (
            store.claim_task(["geo.Cluster"], task_list="geo", provided_environments=["other"])
            is None
        )

    def test_untagged_still_claimable_when_envs_provided(self, store):
        store.save_task(_task("t5"))
        got = store.claim_task(["geo.Cluster"], task_list="geo", provided_environments=["abc123"])
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
                workflow=WorkflowDefinition(
                    uuid="wfdef-1",
                    name="demo.W",
                    namespace_id="",
                    facet_id="",
                    flow_id="",
                    starting_step="",
                    version="1",
                ),
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
            root = next(s for s in store._steps.values() if s.object_type == "Workflow")
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
            find_workflow(program, "demo.W"),
            inputs={"x": 1},
            program_ast=program,
            runner_id="run-1",
        )
        svc = RunnerService(store, evaluator, RunnerConfig(), ToolRegistry())
        assert svc._provided_environments() == []
        svc.run_once()
        # The script task stays pending — this runner cannot claim it.
        task = next(t for t in store._tasks.values() if t.kind == "script")
        assert task.state == TaskState.PENDING


class TestEnvImportSurface:
    def test_env_script_imports_manifest_package(self, tmp_path, monkeypatch):
        """A script in a non-default environment may import the environment's
        declared packages — the declaration is the review gate. Verified with
        a stdlib stand-in on the allow-extension path (extra_import_modules)."""
        from facetwork.runtime.script_executor import ScriptExecutor

        # 'uuid' is NOT in the base allowlist — blocked by default…
        base = ScriptExecutor().execute("import uuid\nresult['ok'] = 1", {})
        assert not base.success
        # …but allowed when the environment's manifest brings it.
        extended = ScriptExecutor().execute(
            "import uuid\nresult['ok'] = len(str(uuid.uuid4())) > 0",
            {},
            extra_import_modules=["uuid"],
        )
        assert extended.success and extended.result == {"ok": True}


class TestEnvironmentDemandQuery:
    """Parity: pending-script-demand scan against BOTH stores."""

    def test_demand_lists_unclaimed_env_hashes(self, store):
        store.save_task(_task("d1", env="hashA", kind="script"))
        store.save_task(_task("d2", env="hashA", kind="script"))
        store.save_task(_task("d3", env="hashB", kind="script"))
        store.save_task(_task("d4", env="hashC", kind=""))  # handler task: no
        store.save_task(_task("d5", env="", kind="script"))  # untagged: no
        t = _task("d6", env="hashD", kind="script")
        t.state = "running"  # claimed: no
        store.save_task(t)
        demand = store.get_pending_script_environment_demand()
        assert [h for h, _ in demand] == ["hashA", "hashB"]
        assert all(wf == "wf1" for _, wf in demand)

    def test_demand_empty(self, store):
        assert store.get_pending_script_environment_demand() == []


class TestLazyMaterialization:
    """End-to-end §4.2: a runner that lacks a demanded environment builds the
    venv from the workflow's frozen manifest, advertises it, and completes the
    waiting workflow — no bake, no operator."""

    def _setup(self, tmp_path, monkeypatch, requires="requires = []"):
        import json

        from facetwork import parse
        from facetwork.ast_utils import find_workflow
        from facetwork.emitter import JSONEmitter
        from facetwork.environments import annotate_program
        from facetwork.runtime import Evaluator, Telemetry
        from facetwork.runtime.entities import RunnerDefinition, WorkflowDefinition

        monkeypatch.setenv("FW_ENV_ROOT", str(tmp_path / "envs"))
        src = """
        namespace lazy {
            environment PyBare { language = "python" }
            facet Tag(x: Long) => (y: Long)
                in environment PyBare
                script { result['y'] = params['x'] + 1 }
            workflow W(x: Long) => (y: Long) andThen {
                s = Tag(x = $.x)
                yield W(y = s.y)
            }
        }
        """
        program = json.loads(JSONEmitter(include_locations=False).emit(parse(src)))
        env_hash = annotate_program(program)["lazy.PyBare"]
        store = MemoryStore()
        ev = Evaluator(persistence=store, telemetry=Telemetry(enabled=False))
        wf = find_workflow(program, "lazy.W")
        res = ev.execute(wf, inputs={"x": 41}, program_ast=program, runner_id="run-1")
        store.save_runner(
            RunnerDefinition(
                uuid="run-1",
                workflow_id=res.workflow_id,
                workflow=WorkflowDefinition(
                    uuid="w1",
                    name="lazy.W",
                    namespace_id="",
                    facet_id="",
                    flow_id="",
                    starting_step="",
                    version="1",
                ),
                compiled_ast=program,
                workflow_ast=wf,
            )
        )
        return store, ev, env_hash

    def test_hook_materializes_advertises_and_completes(self, tmp_path, monkeypatch):
        from facetwork.runtime.agent import ToolRegistry
        from facetwork.runtime.runner import RunnerConfig, RunnerService

        store, ev, env_hash = self._setup(tmp_path, monkeypatch)
        svc = RunnerService(store, ev, RunnerConfig(), ToolRegistry())
        svc._register_server()
        assert svc._provided_environments() == []  # nothing baked

        svc._maybe_materialize_environments()
        assert env_hash in svc._provided_environments()
        server = store.get_server(svc.server_id)
        assert env_hash in server.provided_environments  # re-registered

        for _ in range(20):
            svc.run_once()
            root = next(s for s in store._steps.values() if s.object_type == "Workflow")
            if root.state.endswith("Complete"):
                break
        assert root.state.endswith("Complete"), root.state
        assert root.get_attribute("y") == 42

    def test_hook_respects_kill_switch(self, tmp_path, monkeypatch):
        from facetwork.runtime.agent import ToolRegistry
        from facetwork.runtime.runner import RunnerConfig, RunnerService

        store, ev, env_hash = self._setup(tmp_path, monkeypatch)
        monkeypatch.setenv("FW_ENV_LAZY", "off")
        svc = RunnerService(store, ev, RunnerConfig(), ToolRegistry())
        svc._maybe_materialize_environments()
        assert svc._provided_environments() == []

    def test_failure_negative_caches(self, tmp_path, monkeypatch):
        from facetwork.runtime.agent import ToolRegistry
        from facetwork.runtime.runner import RunnerConfig, RunnerService

        store, ev, env_hash = self._setup(tmp_path, monkeypatch)
        svc = RunnerService(store, ev, RunnerConfig(), ToolRegistry())
        calls = []

        def boom(manifest, h):
            calls.append(h)
            raise RuntimeError("no network")

        import facetwork.environments as fe

        monkeypatch.setattr(fe, "materialize_environment", boom)
        svc._maybe_materialize_environments()
        svc._env_matz_last_scan = 0  # force a fresh scan window
        svc._maybe_materialize_environments()
        assert calls == [env_hash]  # second scan skipped via negative cache
        assert svc._provided_environments() == []

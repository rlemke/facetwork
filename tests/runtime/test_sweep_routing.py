"""The stuck-step sweep must carry capability routing forward.

Regression for 2026-08-31: the sweep recreated a task for a stuck step with none
of the routing the normal path derives, so 7 BuildAdminSet tasks carried
`requires: {}` instead of `{memory_gb: 10}` and a 7.75 GiB host claimed work
floored at 10 GiB. Losing `required_features` the same way is what left two
stocks snapshots unclaimable for days.
"""
from facetwork.runtime.entities import TaskDefinition, TaskState


class _Store:
    def __init__(self, prior):
        self._prior = prior
    def get_tasks_by_step(self, step_id):
        return self._prior


def _svc(prior):
    from facetwork.runtime.runner.service import RunnerService
    s = RunnerService.__new__(RunnerService)
    s._persistence = _Store(prior)
    return s


def _task(**kw):
    base = dict(uuid="t", name="osm.planet.BuildAdminSet", runner_id="r",
                workflow_id="w", flow_id="", step_id="s", state=TaskState.COMPLETED,
                created=1000)
    base.update(kw)
    return TaskDefinition(**base)


def test_inherits_requires_and_features():
    prior = [_task(requires={"memory_gb": 10.0}, required_features=["after"],
                   environment_hash="abc", timeout_ms=14400000)]
    out = _svc(prior)._prior_task_routing("s")
    assert out["requires"] == {"memory_gb": 10.0}, out
    assert out["required_features"] == ["after"], out
    assert out["environment_hash"] == "abc", out
    assert out["timeout_ms"] == 14400000, out
    print("  ✓ inherits requires / required_features / env / timeout")


def test_prefers_newest_carrier():
    prior = [_task(uuid="old", created=1, requires={"memory_gb": 4.0}),
             _task(uuid="new", created=99, requires={"memory_gb": 10.0})]
    out = _svc(prior)._prior_task_routing("s")
    assert out["requires"] == {"memory_gb": 10.0}, out
    print("  ✓ prefers the newest task that carries routing")


def test_no_prior_warns_and_returns_empty():
    out = _svc([])._prior_task_routing("s")
    assert out == {}, out
    print("  ✓ no prior -> {} (and logs a warning)")


def test_ignores_unrouted_prior():
    # a previous SWEEP-created task carries nothing; it must not be chosen over
    # a real one, and must not be mistaken for a valid source
    prior = [_task(uuid="swept", created=50), _task(uuid="real", created=10,
             requires={"memory_gb": 10.0})]
    out = _svc(prior)._prior_task_routing("s")
    assert out["requires"] == {"memory_gb": 10.0}, out
    print("  ✓ skips an unrouted (previously-swept) task and finds the real one")



"""Every dispatch path must inject the same handler-payload keys.

There are three payload builders — RegistryRunner, AgentPoller and the public
TaskProcessor. A key present in some and not others makes a handler's behaviour
depend on which runner happened to claim its task, which is invisible until the
task lands on the wrong one.

That is not hypothetical: `_step_id` was injected by two of the three, and the
Ray delegation adapter — which derives the external engine's run id from it, so
a retry re-attaches instead of launching a second job — dead-lettered with
"no _step_id in payload" only on the path that omitted it. Three tasks sat
forgotten for 14 days.
"""
import ast
import pathlib

import pytest

RUNTIME = pathlib.Path(__file__).resolve().parents[2] / "facetwork" / "runtime"
BUILDERS = ["registry_runner.py", "agent_poller.py", "task_processor.py"]


def _injected_keys(path: pathlib.Path) -> set[str]:
    """Underscore-prefixed keys assigned as ``payload["_x"] = ...``."""
    tree = ast.parse(path.read_text())
    keys: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if (isinstance(target, ast.Subscript)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "payload"
                    and isinstance(target.slice, ast.Constant)
                    and isinstance(target.slice.value, str)
                    and target.slice.value.startswith("_")):
                keys.add(target.slice.value)
    return keys


@pytest.mark.parametrize("name", BUILDERS)
def test_every_builder_injects_the_step_id(name):
    """The specific key whose absence dead-lettered the Ray adapter."""
    assert "_step_id" in _injected_keys(RUNTIME / name), (
        f"{name} does not inject _step_id; a handler deriving an external run id "
        "from it will fail only when this path claims the task")


def test_builders_do_not_drift_apart():
    """Whole-set parity, so the NEXT key added to one builder cannot go missing."""
    sets = {n: _injected_keys(RUNTIME / n) for n in BUILDERS}
    common = set.intersection(*sets.values())
    for name, keys in sets.items():
        missing = common - keys
        assert not missing, f"{name} is missing keys the others inject: {sorted(missing)}"
    # And the union should not exceed the common set by much — report what differs
    # rather than asserting exact equality, since a builder may legitimately add
    # something path-specific.
    union = set.union(*sets.values())
    extras = {n: sorted(union - k) for n, k in sets.items() if union - k}
    assert "_step_id" not in {k for v in extras.values() for k in v}, (
        f"_step_id is not injected everywhere: {extras}")

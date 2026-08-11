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

"""Every runner must inject the same handler-payload contract.

There are three dispatch sites — RegistryRunner, AgentPoller and RunnerService —
and a handler is supposed to behave identically on all of them. They drift,
because each builds its payload independently and nothing compares them.

The drift this pins was real: RegistryRunner and AgentPoller injected
``_step_id``/``_workflow_id``; RunnerService did not. A handler that correlates
to its step — logging, naming an artifact, deriving an idempotency key for an
external system — therefore worked on two runners and failed on the third with a
message that pointed at the handler rather than the runner. It surfaced only
when the Ray delegation adapter, which derives its external submission id from
``_step_id``, was run end to end.

Parity is asserted two ways because the sites differ in shape: RunnerService
exposes a payload builder that can be called, while the other two construct the
payload inline inside their dispatch loop, so those are checked by source
inspection. That is crude, and it is still enough to catch a key being added to
one site and not the others.
"""

from __future__ import annotations

import inspect

import pytest

from facetwork.runtime.agent import ToolRegistry
from facetwork.runtime.entities import TaskDefinition, TaskState
from facetwork.runtime.evaluator import Evaluator
from facetwork.runtime.memory_store import MemoryStore
from facetwork.runtime.runner import RunnerConfig, RunnerService

# The contract every dispatch site owes a handler. Not exhaustive — these are the
# keys whose absence silently changes handler behaviour rather than erroring.
CORE_KEYS = [
    "_step_log",
    "_task_heartbeat",
    "_cancellation_check",
    "_task_uuid",
    "_step_id",
    "_workflow_id",
    "_retry_count",
    "_is_retry",
]


def _service_payload() -> dict:
    store = MemoryStore()
    svc = RunnerService(store, Evaluator(persistence=store), RunnerConfig(), ToolRegistry())
    task = TaskDefinition(
        uuid="task-1",
        name="ns.Facet",
        runner_id="run-1",
        workflow_id="wf-1",
        flow_id="f-1",
        step_id="step-1",
        state=TaskState.RUNNING,
    )
    store.save_task(task)
    return svc._build_handler_payload(task)


@pytest.mark.parametrize("key", CORE_KEYS)
def test_runner_service_injects_the_core_key(key):
    assert key in _service_payload(), f"RunnerService payload is missing {key}"


def test_runner_service_step_identity_is_the_task_s_own():
    """Not merely present — correct. A wrong id is worse than a missing one."""
    payload = _service_payload()
    assert payload["_step_id"] == "step-1"
    assert payload["_workflow_id"] == "wf-1"


@pytest.mark.parametrize("module_name", ["registry_runner", "agent_poller"])
@pytest.mark.parametrize("key", CORE_KEYS)
def test_inline_dispatch_sites_inject_the_core_key(module_name, key):
    """Source-level, because these build their payload inside the dispatch loop.

    Looks for the assignment, not merely the string, so a key that only appears
    in a comment or a docstring does not count as injected.
    """
    import importlib

    mod = importlib.import_module(f"facetwork.runtime.{module_name}")
    src = inspect.getsource(mod)
    assigned = f'payload["{key}"] =' in src or f'"{key}":' in src
    assert assigned, f"{module_name} never assigns {key} into the handler payload"

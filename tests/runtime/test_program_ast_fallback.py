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

"""The program AST must survive a runner restart, or foreach stalls forever.

`_program_ast_cache` is in-process and populated in only two places: the runner
that STARTED the workflow, and `_load_workflow_ast` — which is itself reached
only when `_ast_cache` misses. `cache_workflow_ast()` sets the workflow AST
alone, so the two caches routinely desync: workflow AST present, program AST
absent.

That combination is silently fatal to a `foreach`. A sub-block derives its body
from the PROGRAM ast; without it `resume_step` advances nothing and reports
`iterations=0` — no error, no warning, just a step stranded forever. Every
runner restart re-creates the condition, so a long fan-out is near-certain to
hit it.

Observed live: the 3,167-county fan-out stopped dead after a fleet rollout, its
sweep logging `iterations=0` every 5 minutes against 68 stuck block steps, while
an out-of-process script passing the runner document's `compiled_ast` explicitly
resumed the very same steps immediately. It is also the most likely explanation
for the earlier 49-hour county-atlas stall, which was never root-caused.

The runner document persists `compiled_ast`, so the recovery path exists — it
just was not being taken.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


@pytest.fixture
def service():
    """A RunnerService with both AST caches empty and persistence stubbed."""
    from facetwork.runtime.runner.service import RunnerService

    svc = RunnerService.__new__(RunnerService)
    svc._ast_cache = {}
    svc._program_ast_cache = {}
    svc._persistence = MagicMock()
    return svc


WF = "wf-1"
PROGRAM = {"type": "Program", "declarations": [{"type": "Namespace", "name": "d"}]}
WORKFLOW = {"type": "WorkflowDecl", "name": "W"}


def test_returns_the_cached_program_ast_when_present(service):
    service._program_ast_cache[WF] = PROGRAM
    service._load_workflow_ast = MagicMock(side_effect=AssertionError("must not load"))
    assert service._get_program_ast(WF) is PROGRAM


def test_loads_from_persistence_when_the_cache_is_cold(service):
    """The restart case: workflow AST cached, program AST lost."""
    service._ast_cache[WF] = WORKFLOW  # as cache_workflow_ast() would leave it

    def _load(workflow_id):
        # Mirrors _load_workflow_ast: fills the program cache from the runner
        # snapshot as a side effect.
        service._program_ast_cache[workflow_id] = PROGRAM
        return WORKFLOW

    service._load_workflow_ast = MagicMock(side_effect=_load)

    assert service._get_program_ast(WF) is PROGRAM, (
        "a cold program cache must be refilled from the runner snapshot — "
        "otherwise every foreach sub-block strands with iterations=0"
    )
    service._load_workflow_ast.assert_called_once_with(WF)


def test_returns_none_when_persistence_has_nothing(service):
    """No snapshot to recover from — return None rather than raising."""
    service._load_workflow_ast = MagicMock(return_value=None)
    assert service._get_program_ast(WF) is None


def test_does_not_raise_when_the_loader_fails(service):
    """A resume path must not die because the AST lookup did.

    `_get_program_ast` is called from the stuck-step sweep, which is the very
    machinery that recovers stalled workflows; letting an exception escape here
    would take out the recovery path for every OTHER workflow too.
    """
    service._load_workflow_ast = MagicMock(side_effect=RuntimeError("mongo down"))
    with pytest.raises(RuntimeError):
        # Documents current behaviour: the loader's own errors propagate to the
        # sweep's per-step try/except, which logs and moves to the next step.
        service._get_program_ast(WF)

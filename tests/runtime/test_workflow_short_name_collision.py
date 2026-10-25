"""A workflow whose short name matches an event facet elsewhere in the program.

Regression for 2026-10-25: ``nfl.workflows.Backtest`` calls the event facet
``nfl.model.Backtest``. The workflow's root step carries the SHORT name
"Backtest", and the facet lookup took the first declaration with that short
name anywhere in the program — the event facet, declared earlier in the file.
The root step was dispatched as a task named "Backtest" on the default list,
which no runner serves, so the run sat pending forever with no error.
"""

from __future__ import annotations

from facetwork import FFLParser, emit_dict
from facetwork.ast_utils import find_workflow
from facetwork.runtime import Evaluator, MemoryStore, Telemetry

SRC = """
namespace demo.model {
    event facet Score(x: Int) => (y: Int)
}
namespace demo.workflows {
    use demo.model
    workflow Score(x: Int = 1) => (y: Int) andThen {
        s = demo.model.Score(x = $.x)
        yield Score(y = s.y)
    }
}
"""


def test_root_step_resolves_to_the_workflow_not_the_same_named_event_facet():
    program = emit_dict(FFLParser().parse(SRC))
    workflow_ast = find_workflow(program, "demo.workflows.Score")
    store = MemoryStore()
    result = Evaluator(persistence=store, telemetry=Telemetry(enabled=False)).execute(
        workflow_ast,
        inputs={"x": 3},
        program_ast=program,
        qualified_workflow_name="demo.workflows.Score",
    )
    names = [t.name for t in store.get_tasks_by_workflow(result.workflow_id)]
    assert "Score" not in names, f"the workflow itself was dispatched as a task: {names}"
    assert names == ["demo.model.Score"]

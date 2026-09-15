"""A stepless task must record its handler's result on the task.

⚠️ For a normal event task the handler's return value flows to the STEP via
continue_step(). fw:execute and fw:sys have no step, so the value was discarded:
`fw runner sys pause` completed with result None, telling you THAT the command
ran but not WHAT it reported — for a control channel, most of the point.
"""
import inspect

from facetwork.runtime.runner import service as svc


def test_stepless_result_is_written_to_task_data():
    src = inspect.getsource(svc.RunnerService._process_event_task)
    assert 'task.data["result"] = result' in src, (
        "a stepless task must persist its handler result; otherwise a control "
        "command reports completion with nothing to show for it"
    )


def test_result_is_only_recorded_when_there_is_no_step():
    """A stepped task's result belongs on the step, via continue_step — writing
    it to the task too would duplicate state that can then disagree."""
    src = inspect.getsource(svc.RunnerService._process_event_task)
    i = src.index('task.data["result"] = result')
    guard = src[:i]
    assert "if not task.step_id" in guard.split("task.state = TaskState.COMPLETED")[-1], (
        "the result write must be guarded on the task having no step"
    )

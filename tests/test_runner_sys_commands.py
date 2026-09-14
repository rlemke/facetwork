"""Unit tests for the fw:sys control channel."""
import pytest
from facetwork.runtime.runner.service import RunnerService


class _Svc:
    """Minimal stand-in: exercises the handler without booting a runner."""
    _handle_sys_command = RunnerService._handle_sys_command
    def __init__(self):
        self._paused = False
        self.server_id = "uuid-1234"
        self.is_running = True
        self._start_time_ms = 0
        self._version = "test"
        class _C: server_name="h1"; task_list="default"; server_group="runner"
        self._config = _C()
    def _active_count(self): return 3


def test_pause_then_resume_is_absolute_not_a_toggle():
    s = _Svc()
    assert s._handle_sys_command({"command": "pause"})["paused"] is True
    # delivered TWICE — at-least-once delivery, no fencing token
    assert s._handle_sys_command({"command": "pause"})["paused"] is True
    assert s._handle_sys_command({"command": "resume"})["paused"] is False
    assert s._handle_sys_command({"command": "resume"})["paused"] is False


def test_status_is_read_only():
    s = _Svc()
    s._paused = True
    r = s._handle_sys_command({"command": "status"})
    assert r["paused"] is True and r["server_id"] == "uuid-1234"
    assert s._paused is True, "status must not mutate"


def test_unknown_command_is_refused_not_ignored():
    s = _Svc()
    with pytest.raises(ValueError, match="unknown command"):
        s._handle_sys_command({"command": "reboot"})
    assert s._paused is False


def test_pause_reports_in_flight_work():
    """pause means 'no NEW work' — the caller must be able to see what is still running."""
    r = _Svc()._handle_sys_command({"command": "pause"})
    assert r["active_work_items"] == 3

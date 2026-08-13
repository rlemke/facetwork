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

"""The built-in ``fw.exec`` facets.

Real processes, not a mocked ``subprocess`` — the behaviours that matter here
(a full pipe not deadlocking, a process group actually dying, an inherited
credential actually being absent) are precisely the ones a mock cannot show.

The gate is the reason this facet is allowed to exist at all: Facetwork already
decided the FFL author is not fully trusted, so the tests that pin ``FW_EXEC_ALLOW``
behaviour are load-bearing, not paperwork.
"""

from __future__ import annotations

import os
import sys

import pytest

from facetwork.handlers import exec_handlers as eh


@pytest.fixture
def allow_python(monkeypatch):
    """Allow the running interpreter's basename, so the tests can exec it.

    PATH is pinned to this interpreter's directory first, because the gate
    resolves a bare name on PATH — without this, `python` found a *different*
    interpreter on the box and the tests silently ran under it (caught when one
    rejected an underscore in a numeric literal).
    """
    name = os.path.basename(sys.executable)
    monkeypatch.setenv("PATH", os.path.dirname(sys.executable) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv(eh.ALLOW_VAR, name)
    return name


def _run(command, **params):
    return eh.handle({"_facet_name": "fw.exec.Run", "command": command, **params})


def _py(code: str, **params):
    return _run(os.path.basename(sys.executable), args=["-c", code], **params)


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


def test_unset_allowlist_refuses_and_says_how_to_enable(monkeypatch):
    monkeypatch.delenv(eh.ALLOW_VAR, raising=False)
    with pytest.raises(eh.ExecNotAllowed) as exc:
        _run("echo", args=["hi"])
    message = str(exc.value)
    assert eh.ALLOW_VAR in message, "the operator is not told which variable to set"
    assert "=osmium" in message, "no example of what to set it to"


def test_a_refusal_is_permanent_not_retried(monkeypatch):
    """Observed on the first end-to-end run: the refusal rode the full retry
    budget — 1/5, 2/5, 3/5 — each attempt re-deriving the same answer and
    pushing the real diagnosis minutes out. A policy decision cannot change
    between attempts."""
    from facetwork.runtime.errors import PermanentError

    monkeypatch.setenv(eh.ALLOW_VAR, "osmium")
    with pytest.raises(PermanentError):
        _run("sh", args=["-c", "echo pwned"])


def test_a_command_not_on_the_list_is_refused(monkeypatch):
    monkeypatch.setenv(eh.ALLOW_VAR, "osmium,tippecanoe")
    with pytest.raises(eh.ExecNotAllowed, match="not in FW_EXEC_ALLOW"):
        _run("sh", args=["-c", "echo pwned"])


def test_a_path_is_refused_even_if_its_basename_is_allowed(monkeypatch, tmp_path):
    """The hole this closes: fw.file.WriteText plants a file, Run executes it,
    and the allowlist becomes decorative. Bare names only."""
    monkeypatch.setenv(eh.ALLOW_VAR, "osmium")
    planted = tmp_path / "osmium"
    planted.write_text("#!/bin/sh\necho pwned\n")
    planted.chmod(0o755)

    with pytest.raises(eh.ExecNotAllowed, match="is a path"):
        _run(str(planted))


def test_an_allowed_command_missing_from_path_is_a_host_problem(monkeypatch):
    """"Advertised but absent" is a provisioning fault, and saying so beats a
    bare FileNotFoundError with no context about which host."""
    monkeypatch.setenv(eh.ALLOW_VAR, "definitely-not-a-real-binary-xyz")
    with pytest.raises(FileNotFoundError, match="advertises a capability"):
        _run("definitely-not-a-real-binary-xyz")


def test_which_reports_the_gate_without_running_anything(monkeypatch):
    monkeypatch.setenv(eh.ALLOW_VAR, "osmium")
    out = eh.handle({"_facet_name": "fw.exec.Which", "command": "osmium"})
    assert out["allowed"] is True
    sh = eh.handle({"_facet_name": "fw.exec.Which", "command": "sh"})
    assert sh["allowed"] is False
    assert sh["found"] is True, "sh exists; it is just not permitted here"


# ---------------------------------------------------------------------------
# Running
# ---------------------------------------------------------------------------


def test_stdout_and_exit_code_come_back(allow_python):
    out = _py("print('hello')")
    assert out["stdout"].strip() == "hello"
    assert out["exit_code"] == 0 and out["truncated"] is False
    assert out["duration_ms"] >= 0


def test_a_nonzero_exit_fails_with_stderr_in_the_message(allow_python):
    """"exit status 1" is not a diagnosis; the tail of stderr usually is."""
    with pytest.raises(RuntimeError) as exc:
        _py("import sys; sys.stderr.write('boom: bad input file\\n'); sys.exit(3)")
    assert "exited 3" in str(exc.value)
    assert "boom: bad input file" in str(exc.value)


def test_an_expected_nonzero_exit_can_be_allowed(allow_python):
    """grep's 1 means "no match", not failure."""
    out = _py("import sys; sys.exit(1)", allow_exit_codes=[1])
    assert out["exit_code"] == 1


def test_args_are_never_reparsed_by_a_shell(allow_python, tmp_path):
    """A path containing a space, a quote or $(...) is data, not syntax. This
    is the bug class Snakemake's shell: string interpolation creates."""
    nasty = tmp_path / "a file; rm -rf $(pwd) '.txt"
    nasty.write_text("safe\n")
    out = _run(
        os.path.basename(sys.executable),
        args=["-c", "import sys; print(open(sys.argv[1]).read().strip())", str(nasty)],
    )
    assert out["stdout"].strip() == "safe"
    assert nasty.exists(), "the shell got hold of the argument"


def test_stdin_is_delivered(allow_python):
    out = _py("import sys; print(sys.stdin.read().upper())", stdin="quiet")
    assert "QUIET" in out["stdout"]


def test_cwd_is_honoured(allow_python, tmp_path):
    out = _py("import os; print(os.getcwd())", cwd=str(tmp_path))
    assert os.path.realpath(out["stdout"].strip()) == os.path.realpath(str(tmp_path))


# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------


def test_the_runners_credentials_do_not_reach_the_child(allow_python, monkeypatch):
    """A runner process holds real secrets. Inheriting the environment would
    hand every one of them to any wrapped tool."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-not-leak")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp-should-not-leak")
    out = _py("import os; print(sorted(os.environ))")
    assert "ANTHROPIC_API_KEY" not in out["stdout"]
    assert "GITHUB_TOKEN" not in out["stdout"]
    assert "PATH" in out["stdout"], "PATH must survive or nothing resolves"


def test_env_passes_exactly_what_was_asked_for(allow_python):
    out = _py("import os; print(os.environ.get('MY_VAR'))", env={"MY_VAR": "set-on-purpose"})
    assert out["stdout"].strip() == "set-on-purpose"


# ---------------------------------------------------------------------------
# Bounds
# ---------------------------------------------------------------------------


def test_a_chatty_program_cannot_exhaust_memory_or_deadlock(allow_python):
    """Reading everything buffers a runner to death; not reading fills the pipe
    and hangs forever. Keep a bounded prefix, keep draining."""
    out = _py("print('x' * 5_000_000)", max_output_bytes=1000)
    assert len(out["stdout"]) == 1000
    assert out["truncated"] is True


def test_a_timeout_kills_the_whole_process_group(allow_python, tmp_path):
    """A tool that spawned helpers must not leave them behind holding the
    scratch disk — so the group is killed, not just the parent."""
    marker = tmp_path / "child-still-running"
    code = (
        "import subprocess, sys, time\n"
        f"subprocess.Popen([sys.executable, '-c', "
        f"\"import time; time.sleep(30); open(r'{marker}', 'w').write('alive')\"])\n"
        "time.sleep(30)\n"
    )
    with pytest.raises(TimeoutError, match="timeout_seconds=1"):
        _py(code, timeout_seconds=1)

    import time as _t

    _t.sleep(2)
    assert not marker.exists(), "the child outlived the timeout"


def test_the_command_line_is_reported_for_reading(allow_python):
    out = _py("pass")
    assert out["command_line"].startswith(os.path.basename(sys.executable))


def test_unknown_facet_is_a_clear_error():
    with pytest.raises(KeyError):
        eh.handle({"_facet_name": "fw.exec.Nope"})


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_exec_registers_with_the_execution_timeout_disabled():
    """A wrapped tool legitimately runs for hours (osmium on a continental PBF).
    The 10-minute default would reset the task under a healthy process."""
    from facetwork.handlers import _builtin_entries

    _uri, timeout = _builtin_entries()["fw.exec.Run"]
    assert timeout == 0
    _uri2, other = _builtin_entries()["fw.file.List"]
    assert other > 0, "the override must not have leaked to the other built-ins"

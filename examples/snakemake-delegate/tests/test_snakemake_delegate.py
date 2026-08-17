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

"""Tests for the Snakemake delegation adapter.

Most of these need no Snakemake: the parts worth pinning are the refusals and
the parsing, and those are pure. The one test that runs the real binary skips
itself when it is absent, so a runner host without it still gets a green suite.
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_HANDLER = _ROOT / "handlers" / "snakemake_handlers.py"
SNAKEFILE = _ROOT / "workflow" / "Snakefile"


def _load():
    spec = importlib.util.spec_from_file_location("snakemake_handlers_under_test", _HANDLER)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


sh = _load()

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


# --- parsing: the two answers Snakemake gives ------------------------------


def test_nothing_to_be_done_is_recognised():
    """The whole idempotency story rests on reading this correctly: a
    redelivered task re-invokes Snakemake and must be able to tell that the
    engine did nothing, rather than assuming it."""
    out = "Building DAG of jobs...\nNothing to be done (all requested files are present and up to date)."
    assert sh._NOTHING_TO_DO.search(out)
    assert sh._parse_jobs(out) == 0


def test_job_count_comes_from_the_summary_table():
    out = "Job stats:\nshard       12\nmerge        1\nall          1\ntotal       14\n"
    assert sh._parse_jobs(out) == 14
    assert not sh._NOTHING_TO_DO.search(out)


def test_job_count_is_zero_when_there_is_no_table():
    assert sh._parse_jobs("some unrelated output") == 0


def test_lock_message_is_recognised():
    assert sh._LOCKED.search("Error: Directory cannot be locked. ...")


# --- refusals: what must NOT burn the retry budget --------------------------


@pytest.mark.parametrize("missing", ["snakefile", "workdir"])
def test_missing_required_parameter_is_permanent(missing, tmp_path):
    from facetwork.runtime.errors import PermanentError

    payload = {
        "_facet_name": "snakemake.delegate.RunWorkflow",
        "snakefile": str(SNAKEFILE),
        "workdir": str(tmp_path),
    }
    payload[missing] = ""
    with pytest.raises(PermanentError, match="required"):
        sh.handle(payload)


def test_a_nonexistent_path_is_permanent(tmp_path):
    from facetwork.runtime.errors import PermanentError

    with pytest.raises(PermanentError, match="does not exist"):
        sh.handle({
            "_facet_name": "snakemake.delegate.RunWorkflow",
            "snakefile": str(tmp_path / "nope"),
            "workdir": str(tmp_path),
        })


def test_a_bad_timeout_is_permanent_not_a_two_hour_wait(tmp_path):
    """`x or 120` would rewrite a caller's 0 into the default and wait two
    hours for a workflow that asked not to."""
    from facetwork.runtime.errors import PermanentError

    with pytest.raises(PermanentError, match="run_timeout_minutes must be positive"):
        sh.handle({
            "_facet_name": "snakemake.delegate.RunWorkflow",
            "snakefile": str(SNAKEFILE),
            "workdir": str(tmp_path),
            "run_timeout_minutes": 0,
        })


def test_unknown_facet_is_rejected():
    with pytest.raises(ValueError, match="Unknown facet"):
        sh.handle({"_facet_name": "snakemake.delegate.Nope"})


# --- the command: argv, never a shell string --------------------------------


def test_command_is_argv_and_carries_the_knobs(tmp_path):
    cmd = sh._command("/bin/snakemake", {"cores": 4, "targets": "all", "extra_args": "--quiet"},
                      str(tmp_path), str(SNAKEFILE))
    assert cmd[0] == "/bin/snakemake"
    assert "--cores" in cmd and cmd[cmd.index("--cores") + 1] == "4"
    assert cmd[-2:] == ["--quiet", "all"]
    # A path with a space must arrive as ONE argv element, not two words.
    spaced = sh._command("/bin/snakemake", {"targets": "'a file.txt'"}, str(tmp_path), str(SNAKEFILE))
    assert "a file.txt" in spaced


def test_nonpositive_cores_is_permanent(tmp_path):
    from facetwork.runtime.errors import PermanentError

    with pytest.raises(PermanentError, match="cores must be positive"):
        sh._command("/bin/snakemake", {"cores": 0}, str(tmp_path), str(SNAKEFILE))


# --- with the real binary ---------------------------------------------------

_BIN = shutil.which(sh.SNAKEMAKE_BIN)


@pytest.mark.skipif(not _BIN, reason="snakemake not installed on this host")
def test_end_to_end_runs_then_does_nothing(tmp_path, monkeypatch):
    """The two answers, in order: a real fan-out, then the no-op that makes a
    redelivered task cheap."""
    payload = {
        "_facet_name": "snakemake.delegate.RunWorkflow",
        "_step_id": "test-step",
        "snakefile": str(SNAKEFILE),
        "workdir": str(tmp_path),
        "cores": 2,
    }
    first = sh.handle(dict(payload))
    assert first["status"] == "success"
    assert first["jobs_run"] == 14 and first["nothing_to_be_done"] is False
    assert (tmp_path / "out" / "merged.txt").exists()

    second = sh.handle(dict(payload))
    assert second["nothing_to_be_done"] is True
    assert second["jobs_run"] == 0
    assert second["duration_seconds"] < first["duration_seconds"]


@pytest.mark.skipif(not _BIN, reason="snakemake not installed on this host")
def test_a_stale_lock_refuses_unless_told_otherwise(tmp_path):
    """A lock means *some process may be running here* and, from outside, a
    crashed owner is indistinguishable from a live one — the same ambiguity the
    orphan reaper faces. So the adapter refuses by default rather than guessing."""
    from facetwork.runtime.errors import PermanentError

    # A lock file must NAME the paths it guards — Snakemake compares them
    # against what the new run would touch, so an empty one locks nothing and
    # the run sails straight past it. (Found the hard way: the first version of
    # this test wrote empty files and asserted a refusal that never came.)
    lock_dir = tmp_path / ".snakemake" / "locks"
    lock_dir.mkdir(parents=True)
    guarded = [f"out/{i:02d}.txt" for i in range(1, 13)] + ["out/merged.txt"]
    (lock_dir / "0.output.lock").write_text("\n".join(guarded) + "\n")

    payload = {
        "_facet_name": "snakemake.delegate.RunWorkflow",
        "_step_id": "test-step",
        "snakefile": str(SNAKEFILE),
        "workdir": str(tmp_path),
        "cores": 1,
    }
    with pytest.raises(PermanentError, match="locked"):
        sh.handle(dict(payload))

    # Opt in, and it clears the lock and runs.
    ok = sh.handle(dict(payload, unlock_stale=True))
    assert ok["status"] == "success"

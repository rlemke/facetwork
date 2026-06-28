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

"""Smoke tests for the `fw` front-door CLI and its command files.

Hermetic: only exercises help/listing/dispatch + `bash -n` syntax of every
command file. Nothing here touches Mongo, Docker, or the network, so it runs
anywhere the suite runs. Guards the large `scripts/lib/` surface against the
most common regressions: a syntax error in a command file, a broken dispatcher,
or a renamed/removed group.
"""

from __future__ import annotations

import os
import shutil
import subprocess

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FW = os.path.join(REPO_ROOT, "fw")
LIB = os.path.join(REPO_ROOT, "scripts", "lib")

EXPECTED_GROUPS = [
    "install",
    "single",
    "db",
    "runner",
    "fleet",
    "ffl",
    "maint",
    "svc",
    "util",
]

pytestmark = pytest.mark.skipif(
    not (os.path.isfile(FW) and shutil.which("bash")),
    reason="fw CLI or bash not available",
)


def _run(*args, **kw):
    return subprocess.run(["bash", FW, *args], cwd=REPO_ROOT, capture_output=True, text=True, **kw)


def _command_files():
    """Every executable command file under scripts/lib (excludes _helpers/_*)."""
    out = []
    for dp, _, files in os.walk(LIB):
        if os.sep + "_helpers" in dp or "__pycache__" in dp:
            continue
        for f in files:
            if f.startswith("_"):
                continue
            out.append(os.path.join(dp, f))
    return out


def _shebang(path):
    with open(path, "rb") as fh:
        return fh.readline()


def _bash_files():
    fw_and_lib = [FW]
    for p in _command_files():
        if b"bash" in _shebang(p):
            fw_and_lib.append(p)
    # helpers are sourced bash too
    for f in ("_bootstrap.sh", "_env.sh", "_remote.sh"):
        fp = os.path.join(LIB, "_helpers", f)
        if os.path.isfile(fp):
            fw_and_lib.append(fp)
    return fw_and_lib


def _python_command_files():
    return [p for p in _command_files() if b"python" in _shebang(p)]


def test_fw_help_lists_all_groups():
    r = _run("help")
    assert r.returncode == 0, r.stderr
    for group in EXPECTED_GROUPS:
        assert group in r.stdout, f"group {group} missing from `fw help`"


def test_bare_fw_lists_groups():
    r = _run()
    assert r.returncode == 0
    assert "Groups:" in r.stdout


@pytest.mark.parametrize("group", EXPECTED_GROUPS)
def test_group_listing(group):
    r = _run(group)
    assert r.returncode == 0, f"`fw {group}` failed: {r.stderr}"
    assert f"fw {group}" in r.stdout


def test_nested_subgroup_listing():
    r = _run("db", "postgis")
    assert r.returncode == 0, r.stderr
    assert "vacuum" in r.stdout


def test_unknown_group_exits_2():
    r = _run("definitely-not-a-group")
    assert r.returncode == 2
    assert "unknown group" in r.stderr.lower()


def test_unknown_command_suggests():
    r = _run("fleet", "staus")  # typo for a fleet subcommand
    assert r.returncode == 2
    assert "unknown command" in r.stderr.lower()


def test_completion_path():
    r = _run("--completion-path")
    assert r.returncode == 0
    assert r.stdout.strip().endswith("fw-completion.bash")


# The gap-filling commands take destructive/networked actions, so their --help
# must short-circuit cleanly (exit 0, print usage) *before* touching Mongo/Docker.
NEW_COMMANDS = [
    ("maint", "purge-servers"),
    ("runner", "scale"),
    ("fleet", "scale"),
    ("fleet", "registry-setup"),
    ("fleet", "rollout"),
]


@pytest.mark.parametrize("cmd", NEW_COMMANDS, ids=[" ".join(c) for c in NEW_COMMANDS])
def test_new_command_help_is_hermetic(cmd):
    r = _run(*cmd, "--help")
    assert r.returncode == 0, f"`fw {' '.join(cmd)} --help` exited {r.returncode}: {r.stderr}"
    assert "usage" in (r.stdout + r.stderr).lower()


def test_all_command_files_parse():
    """`bash -n` every command file + helper — catches syntax errors anywhere."""
    bad = []
    for f in _bash_files():
        r = subprocess.run(["bash", "-n", f], capture_output=True, text=True)
        if r.returncode != 0:
            bad.append(f"{os.path.relpath(f, REPO_ROOT)}: {r.stderr.strip()}")
    assert not bad, "bash syntax errors:\n" + "\n".join(bad)


def test_every_command_is_executable():
    not_exec = [
        os.path.relpath(p, REPO_ROOT) for p in _command_files() if not os.access(p, os.X_OK)
    ]
    assert not not_exec, f"command files missing +x: {not_exec}"


def test_every_command_has_shebang():
    """`fw` exec()s each command, so a file without a shebang can't run even with
    +x (this is exactly how the cache-index regression manifested)."""
    no_shebang = [
        os.path.relpath(p, REPO_ROOT) for p in _command_files() if not _shebang(p).startswith(b"#!")
    ]
    assert not no_shebang, f"command files missing a shebang: {no_shebang}"


def test_python_command_files_compile():
    import sys

    bad = []
    for f in _python_command_files():
        r = subprocess.run([sys.executable, "-m", "py_compile", f], capture_output=True, text=True)
        if r.returncode != 0:
            bad.append(f"{os.path.relpath(f, REPO_ROOT)}: {r.stderr.strip()}")
    assert not bad, "python command files with syntax errors:\n" + "\n".join(bad)

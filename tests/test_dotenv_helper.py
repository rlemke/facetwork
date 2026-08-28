"""Tests for scripts/lib/_helpers/_dotenv.py.

⚠️ Why this matters more than a dotenv parser usually would: `fw` execs python
commands directly, so they never source _env.sh. Without .env a tool falls back
to `mongodb://localhost:27017`, which on a fleet host is a DIFFERENT MongoDB
from the fleet's — measured 2026-08-28, one container had 2 running runners and
the other had 0. A diagnostic reading the wrong database reports confidently
about the wrong world.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

_HELPERS = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "lib" / "_helpers"
sys.path.insert(0, str(_HELPERS))
from _dotenv import load_env  # noqa: E402


def _repo(tmp_path: pathlib.Path, body: str) -> pathlib.Path:
    (tmp_path / "fw").write_text("#!/bin/sh\n")
    (tmp_path / ".env").write_text(body)
    deep = tmp_path / "scripts" / "lib" / "maint"
    deep.mkdir(parents=True)
    (deep / "cmd").write_text("")
    return deep / "cmd"


def test_loads_values_from_the_repo_root(tmp_path, monkeypatch):
    cmd = _repo(tmp_path, "FW_MONGODB_URL=mongodb://afl-mongodb:27017\nOTHER=x\n")
    monkeypatch.delenv("FW_MONGODB_URL", raising=False)
    monkeypatch.delenv("OTHER", raising=False)
    root = load_env(cmd)
    assert root == tmp_path
    import os
    assert os.environ["FW_MONGODB_URL"] == "mongodb://afl-mongodb:27017"
    assert os.environ["OTHER"] == "x"


def test_an_explicit_environment_value_wins(tmp_path, monkeypatch):
    """Matches _env.sh: `FW_MONGODB_URL=... fw maint ...` must still override."""
    cmd = _repo(tmp_path, "FW_MONGODB_URL=mongodb://from-dotenv:27017\n")
    monkeypatch.setenv("FW_MONGODB_URL", "mongodb://explicit:27017")
    load_env(cmd)
    import os
    assert os.environ["FW_MONGODB_URL"] == "mongodb://explicit:27017"


def test_comments_blanks_quotes_and_export_are_handled(tmp_path, monkeypatch):
    cmd = _repo(tmp_path, '# a comment\n\nexport A="quoted"\nB=\'single\'\nC=plain\nnot_a_pair\n')
    for k in "ABC":
        monkeypatch.delenv(k, raising=False)
    load_env(cmd)
    import os
    assert os.environ["A"] == "quoted"
    assert os.environ["B"] == "single"
    assert os.environ["C"] == "plain"


def test_returns_none_outside_a_repo(tmp_path):
    """No .env + fw marker anywhere above: report it rather than guessing."""
    stray = tmp_path / "nowhere" / "cmd"
    stray.parent.mkdir(parents=True)
    stray.write_text("")
    assert load_env(stray) is None


def test_the_python_maint_commands_actually_call_it():
    """These two are exec'd directly by `fw`; if they stop loading .env they
    silently read localhost again. Discovering them by scan rather than trusting
    a hardcoded list — the same lesson as the claim-site guard."""
    maint = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "lib" / "maint"
    python_cmds = [p for p in maint.iterdir()
                   if p.is_file() and p.read_text(errors="replace").startswith("#!/usr/bin/env python")]
    assert python_cmds, "expected python maint commands"
    missing = [p.name for p in python_cmds
               if "load_env" not in p.read_text(errors="replace")]
    assert not missing, f"python maint commands not loading .env: {missing}"

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

"""Tests for the retired AFL_ prefix — FW_ is the only accepted prefix now."""

from __future__ import annotations

import logging

from facetwork import envcompat as ec


def test_fw_getenv_reads_fw_only(monkeypatch):
    monkeypatch.delenv("FW_PTEST", raising=False)
    monkeypatch.delenv("AFL_PTEST", raising=False)
    # A lone legacy AFL_ var is NOT consulted — it is retired.
    monkeypatch.setenv("AFL_PTEST", "legacy")
    assert ec.fw_getenv("PTEST") is None
    monkeypatch.setenv("FW_PTEST", "canon")
    assert ec.fw_getenv("PTEST") == "canon"
    assert ec.fw_getenv("PTEST_MISSING", "dflt") == "dflt"


def test_legacy_vars_detects_afl_only():
    e = {"FW_X": "new", "AFL_X": "old", "AFL_Y": "y", "PATH": "/bin"}
    assert ec.legacy_vars(e) == ["AFL_X", "AFL_Y"]


def test_no_mirroring_environ_unchanged():
    # The mirror is gone: detection must NOT mutate the env (no FW_ created,
    # no AFL_ promoted).
    e = {"AFL_STORAGE": "s3", "FW_DATA_ROOT": "x"}
    before = dict(e)
    ec.legacy_vars(e)
    assert e == before


def test_install_logs_error_on_legacy(monkeypatch, caplog):
    monkeypatch.setenv("AFL_SOME_LEGACY", "v")
    # install() is guarded by a module flag after first import; exercise the
    # reporting path directly so the guard doesn't swallow it.
    ec._installed = False
    with caplog.at_level(logging.ERROR, logger="facetwork.env"):
        ec.install()
    assert "AFL_SOME_LEGACY" in caplog.text
    monkeypatch.delenv("AFL_SOME_LEGACY", raising=False)

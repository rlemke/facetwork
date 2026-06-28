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

"""Tests for the AFL_ -> FW_ env-var compatibility shim."""

from __future__ import annotations

import os

from facetwork import envcompat as ec


def test_fw_wins_collision():
    e = {"FW_X": "new", "AFL_X": "old"}
    ec.normalize_environ(e)
    assert e["FW_X"] == "new"
    assert e["AFL_X"] == "new"  # FW_ is canonical, pushed down


def test_lone_legacy_mirrored_up():
    e = {"AFL_STORAGE": "s3"}
    ec.normalize_environ(e)
    assert e["FW_STORAGE"] == "s3"


def test_lone_canonical_mirrored_down():
    e = {"FW_DATA_ROOT": "s3://fw-cache"}
    ec.normalize_environ(e)
    assert e["AFL_DATA_ROOT"] == "s3://fw-cache"


def test_legacy_count_reflects_operator_set_only():
    # The newly-mirrored AFL_DATA_ROOT must NOT inflate the legacy count.
    e = {"FW_DATA_ROOT": "x", "AFL_STORAGE": "s3"}
    assert ec.normalize_environ(e) == 1


def test_idempotent_values_stable():
    e = {"FW_X": "new", "AFL_X": "old", "AFL_Y": "y"}
    ec.normalize_environ(e)
    snapshot = dict(e)
    ec.normalize_environ(e)
    assert e == snapshot


def test_fw_getenv_precedence(monkeypatch):
    monkeypatch.delenv("FW_PTEST", raising=False)
    monkeypatch.delenv("AFL_PTEST", raising=False)
    monkeypatch.setenv("AFL_PTEST", "legacy")
    assert ec.fw_getenv("PTEST") == "legacy"
    monkeypatch.setenv("FW_PTEST", "canon")
    assert ec.fw_getenv("PTEST") == "canon"
    assert ec.fw_getenv("PTEST_MISSING", "dflt") == "dflt"


def test_install_mirrors_real_environ_for_legacy_readers(monkeypatch):
    """A FW_ var set before package import must be visible to code that reads the
    legacy AFL_ name — the whole point of the shim."""
    monkeypatch.setenv("FW_INTEGRATION_PROBE", "value")
    monkeypatch.delenv("AFL_INTEGRATION_PROBE", raising=False)
    # install() is guarded by a module flag after first import, so exercise the
    # underlying normalize directly against the real environ.
    ec.normalize_environ()
    assert os.environ["AFL_INTEGRATION_PROBE"] == "value"

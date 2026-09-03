"""`facetwork.domains.toolstorage` — the shared tools/ storage layer.

fwh_groupphoto and fwh_sentinel2 shipped this BYTE-IDENTICALLY apart from their
docstring and NAMESPACE. It is deliberately NOT folded into DomainStorage: the
semantics differ in four ways at once (path-scheme dispatch, boto3 directly, a
shared cache root with no domain segment, and recursive-relative listing), so
merging them would be a rewrite wearing a refactor's clothes.
"""
from __future__ import annotations

import os

import pytest

from facetwork.domains.toolstorage import ToolStorage, tool_storage


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for k in list(os.environ):
        if k.startswith("FW_"):
            monkeypatch.delenv(k, raising=False)


def test_backend_returns_the_name_not_an_object():
    """⚠️ Public API: a STRING. DomainStorage.backend() returns a backend
    object — same name, different contract, which is one reason these two
    classes stay separate."""
    assert tool_storage("gp").backend() == "local"


def test_defaults_differ_by_backend(monkeypatch):
    s = tool_storage("gp")
    assert s.data_root() == ToolStorage.LOCAL_DEFAULT_ROOT
    monkeypatch.setenv("FW_STORAGE", "s3")
    assert s.data_root() == ToolStorage.S3_DEFAULT_ROOT


def test_cache_root_has_no_domain_segment(monkeypatch):
    monkeypatch.setenv("FW_DATA_ROOT", "s3://afl-cache")
    assert tool_storage("gp").cache_root() == "s3://afl-cache/cache"


def test_local_output_base_is_ignored_on_s3(monkeypatch):
    """⚠️ The subtle one. Under the runtime's staging model FW_OUTPUT_BASE is
    usually a LOCAL scratch dir; honouring it on s3 would leave the published
    bundle on one runner's disk instead of in the object store."""
    monkeypatch.setenv("FW_STORAGE", "s3")
    monkeypatch.setenv("FW_DATA_ROOT", "s3://afl-cache")
    monkeypatch.setenv("FW_OUTPUT_BASE", "/tmp/scratch")
    assert tool_storage("gp").output_root() == "s3://afl-cache/output"
    monkeypatch.setenv("FW_OUTPUT_BASE", "s3://elsewhere/out")
    assert tool_storage("gp").output_root() == "s3://elsewhere/out"


def test_output_base_env_is_not_derived_from_the_namespace():
    """fwh_sentinel2 reads FW_S2_OUTPUT_BASE while its package is 'sentinel2';
    guessing would silently ignore an operator's setting."""
    assert tool_storage("s2", output_base_env="FW_S2_OUTPUT_BASE").output_base_env == \
        "FW_S2_OUTPUT_BASE"


def test_join_is_os_path_for_local_and_posix_for_s3():
    assert ToolStorage.join("s3://b", "x", "y") == "s3://b/x/y"
    assert ToolStorage.join("/t", "a", "b") == os.path.join("/t", "a", "b")
    assert ToolStorage.join() == ""


def test_list_files_is_recursive_and_relative(tmp_path):
    """⚠️ Different from DomainStorage.list_dir, which is immediate entries as
    full paths. Confusing the two silently returns the wrong strings."""
    (tmp_path / "a" / "deep").mkdir(parents=True)
    (tmp_path / "a" / "deep" / "f.txt").write_text("x")
    (tmp_path / "top.txt").write_text("y")
    got = sorted(tool_storage("gp").list_files(str(tmp_path)))
    assert got == ["a/deep/f.txt", "top.txt"]


def test_list_files_is_empty_for_a_missing_dir(tmp_path):
    assert tool_storage("gp").list_files(str(tmp_path / "nope")) == []


def test_local_write_is_atomic(tmp_path):
    """A reader never sees a half-written file; a crash leaves the previous
    version intact rather than a truncated one."""
    s = tool_storage("gp")
    p = str(tmp_path / "sub" / "f.bin")
    s.write_bytes(p, b"first")
    assert s.read_bytes(p) == b"first"
    s.write_bytes(p, b"second")
    assert s.read_bytes(p) == b"second"
    assert not (tmp_path / "sub" / "f.bin.tmp").exists()


def test_text_round_trip(tmp_path):
    s = tool_storage("gp")
    p = str(tmp_path / "t.txt")
    s.write_text(p, "héllo")
    assert s.read_text(p) == "héllo" and s.exists(p)

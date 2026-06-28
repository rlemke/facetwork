"""Tests for the FileSystem facade — init/config-driven backend selection."""

import json
from unittest.mock import MagicMock, patch

import pytest

from facetwork.runtime.storage import (
    FileSystem,
    _detect_backend_and_root,
    configure_fs,
    get_fs,
    reset_fs,
)


@pytest.fixture(autouse=True)
def _clean_fs_env(monkeypatch):
    """Strip every env var that influences backend selection before each test."""
    for var in (
        "FW_FS_BACKEND",
        "FW_FS_ROOT",
        "FW_STORAGE",
        "FW_HDFS_HOST",
        "FW_HDFS_BASE",
        "FW_S3_BUCKET",
        "FW_S3_PREFIX",
    ):
        monkeypatch.delenv(var, raising=False)
    reset_fs()
    yield
    reset_fs()


# ---------------------------------------------------------------------------
# Backend selection precedence (Hadoop → MinIO/S3 → local file)
# ---------------------------------------------------------------------------


class TestBackendSelection:
    def test_default_is_local(self):
        kind, root = _detect_backend_and_root("auto", "")
        assert kind == "local"
        assert root == ""

    def test_root_scheme_pins_hdfs(self):
        kind, root = _detect_backend_and_root("auto", "hdfs://nn/user/afl")
        assert kind == "hdfs"
        assert root == "hdfs://nn/user/afl"

    def test_root_scheme_pins_s3(self):
        kind, root = _detect_backend_and_root("auto", "s3://bucket/cache")
        assert kind == "s3"
        assert root == "s3://bucket/cache"

    def test_root_scheme_file_is_local(self):
        kind, root = _detect_backend_and_root("auto", "file:///data")
        assert kind == "local"

    def test_explicit_backend_overrides_auto(self, monkeypatch):
        monkeypatch.setenv("FW_HDFS_HOST", "namenode")
        kind, root = _detect_backend_and_root("hdfs", "")
        assert kind == "hdfs"
        assert root == "hdfs://namenode"

    def test_hadoop_signal_via_afl_storage(self, monkeypatch):
        monkeypatch.setenv("FW_STORAGE", "hdfs")
        monkeypatch.setenv("FW_HDFS_HOST", "nn1")
        monkeypatch.setenv("FW_HDFS_BASE", "/user/afl")
        kind, root = _detect_backend_and_root("auto", "")
        assert kind == "hdfs"
        assert root == "hdfs://nn1/user/afl"

    def test_hadoop_precedence_over_s3(self, monkeypatch):
        # Both signals present — Hadoop must win (stated precedence).
        monkeypatch.setenv("FW_STORAGE", "hdfs")
        monkeypatch.setenv("FW_HDFS_HOST", "nn1")
        monkeypatch.setenv("FW_S3_BUCKET", "b")
        kind, _ = _detect_backend_and_root("auto", "")
        assert kind == "hdfs"

    def test_s3_signal_via_bucket(self, monkeypatch):
        monkeypatch.setenv("FW_S3_BUCKET", "my-bucket")
        monkeypatch.setenv("FW_S3_PREFIX", "cache/v1")
        kind, root = _detect_backend_and_root("auto", "")
        assert kind == "s3"
        assert root == "s3://my-bucket/cache/v1"

    def test_s3_signal_via_afl_storage(self, monkeypatch):
        monkeypatch.setenv("FW_STORAGE", "s3")
        monkeypatch.setenv("FW_S3_BUCKET", "b")
        kind, root = _detect_backend_and_root("auto", "")
        assert kind == "s3"
        assert root == "s3://b"

    def test_explicit_backend_conflicts_with_scheme_warns(self, caplog):
        # Scheme on root is authoritative; explicit mismatch is downgraded.
        kind, _ = _detect_backend_and_root("local", "s3://b/k")
        assert kind == "s3"
        assert any("conflicts" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Bare-path resolution
# ---------------------------------------------------------------------------


class TestResolve:
    def test_local_relative_under_root(self):
        fs = FileSystem(backend="local", root="/data")
        assert fs.resolve("regions/ca.pbf") == "/data/regions/ca.pbf"

    def test_local_absolute_passthrough(self):
        fs = FileSystem(backend="local", root="/data")
        assert fs.resolve("/etc/hosts") == "/etc/hosts"

    def test_local_no_root_passthrough(self):
        fs = FileSystem(backend="local", root="")
        assert fs.resolve("relative/x.txt") == "relative/x.txt"

    def test_explicit_uri_overrides_local_default(self):
        fs = FileSystem(backend="local", root="/data")
        assert fs.resolve("s3://b/k") == "s3://b/k"
        assert fs.resolve("hdfs://nn/p") == "hdfs://nn/p"

    def test_s3_root_join(self):
        fs = FileSystem(backend="s3", root="s3://bucket/cache")
        assert fs.resolve("regions/ca.pbf") == "s3://bucket/cache/regions/ca.pbf"

    def test_s3_leading_slash_stripped(self):
        fs = FileSystem(backend="s3", root="s3://bucket/cache")
        assert fs.resolve("/regions/ca.pbf") == "s3://bucket/cache/regions/ca.pbf"

    def test_hdfs_root_join(self):
        fs = FileSystem(backend="hdfs", root="hdfs://nn/user/afl")
        assert fs.resolve("data/x.json") == "hdfs://nn/user/afl/data/x.json"

    def test_remote_uri_override_on_s3_default(self):
        fs = FileSystem(backend="s3", root="s3://bucket/cache")
        assert fs.resolve("hdfs://nn/p") == "hdfs://nn/p"


# ---------------------------------------------------------------------------
# Read/write round-trips on the local backend
# ---------------------------------------------------------------------------


class TestLocalReadWrite:
    def test_write_then_read_bytes(self, tmp_path):
        fs = FileSystem(backend="local", root=str(tmp_path))
        fs.write_bytes("a/b/data.bin", b"\x00\x01\x02")
        assert fs.read_bytes("a/b/data.bin") == b"\x00\x01\x02"

    def test_write_creates_parent_dirs(self, tmp_path):
        fs = FileSystem(backend="local", root=str(tmp_path))
        fs.write_text("deep/nested/dir/x.txt", "hi")
        assert (tmp_path / "deep" / "nested" / "dir" / "x.txt").read_text() == "hi"

    def test_text_round_trip(self, tmp_path):
        fs = FileSystem(backend="local", root=str(tmp_path))
        fs.write_text("note.txt", "héllo")
        assert fs.read_text("note.txt") == "héllo"

    def test_json_round_trip(self, tmp_path):
        fs = FileSystem(backend="local", root=str(tmp_path))
        obj = {"count": 42, "names": ["a", "b"]}
        fs.write_json("out/summary.json", obj)
        assert fs.read_json("out/summary.json") == obj
        # also readable as raw text / parseable independently
        assert json.loads((tmp_path / "out" / "summary.json").read_text()) == obj

    def test_exists_isfile_isdir(self, tmp_path):
        fs = FileSystem(backend="local", root=str(tmp_path))
        fs.write_text("dir/f.txt", "x")
        assert fs.exists("dir/f.txt")
        assert fs.isfile("dir/f.txt")
        assert fs.isdir("dir")
        assert not fs.exists("nope.txt")

    def test_open_context_manager(self, tmp_path):
        fs = FileSystem(backend="local", root=str(tmp_path))
        with fs.open("c.txt", "w") as fh:
            fh.write("written")
        with fs.open("c.txt", "r") as fh:
            assert fh.read() == "written"

    def test_listdir_and_getsize(self, tmp_path):
        fs = FileSystem(backend="local", root=str(tmp_path))
        fs.write_bytes("d/one.bin", b"abc")
        fs.write_bytes("d/two.bin", b"de")
        assert sorted(fs.listdir("d")) == ["one.bin", "two.bin"]
        assert fs.getsize("d/one.bin") == 3


# ---------------------------------------------------------------------------
# Global facade + configure/reset
# ---------------------------------------------------------------------------


class TestGlobalFacade:
    def test_configure_fs_installs_global(self, tmp_path):
        configure_fs(backend="local", root=str(tmp_path))
        fs = get_fs()
        assert fs.kind == "local"
        assert fs.root == str(tmp_path)

    def test_get_fs_is_singleton(self):
        first = get_fs()
        assert get_fs() is first

    def test_reset_fs_rebuilds(self):
        first = get_fs()
        reset_fs()
        assert get_fs() is not first

    def test_module_helpers_use_global(self, tmp_path):
        from facetwork.runtime import storage as st

        configure_fs(backend="local", root=str(tmp_path))
        st.write_text("h.txt", "via helper")
        assert st.read_text("h.txt") == "via helper"
        assert st.fs_exists("h.txt")
        assert not st.fs_exists("missing.txt")


# ---------------------------------------------------------------------------
# Remote backends route through the right StorageBackend (mocked transport)
# ---------------------------------------------------------------------------


class TestRemoteRouting:
    def test_s3_default_routes_to_s3_backend(self):
        fs = FileSystem(backend="s3", root="s3://bucket/cache")
        fake = MagicMock()
        fake.open.return_value.__enter__ = lambda s: s
        with patch("facetwork.runtime.storage.get_storage_backend", return_value=fake) as gsb:
            fs.exists("regions/ca.pbf")
        gsb.assert_called_with("s3://bucket/cache/regions/ca.pbf")
        fake.exists.assert_called_once_with("s3://bucket/cache/regions/ca.pbf")

    def test_uri_override_routes_to_hdfs(self):
        fs = FileSystem(backend="s3", root="s3://bucket/cache")
        fake = MagicMock()
        with patch("facetwork.runtime.storage.get_storage_backend", return_value=fake) as gsb:
            fs.getsize("hdfs://nn/user/afl/x")
        gsb.assert_called_with("hdfs://nn/user/afl/x")

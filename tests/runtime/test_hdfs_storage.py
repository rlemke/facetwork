"""Integration tests for HDFS storage against live HDFS containers.

Requires the HDFS Docker services to be running:

    docker compose --profile hdfs up -d

Run with:

    pytest tests/runtime/test_hdfs_storage.py --hdfs -v

Uses the WebHDFS REST API (port 9870) to test HDFS operations directly.
pyarrow's HadoopFileSystem requires the native libhdfs JNI library which
is typically only available in full Hadoop installations, so these tests
exercise the cluster through WebHDFS instead.
"""

import pytest

from tests.hdfs_helpers import WebHDFSClient, hdfs, workdir  # noqa: F401, F811

# Skip entire module unless --hdfs is passed
pytestmark = pytest.mark.skipif("not config.getoption('--hdfs')")


# ---------------------------------------------------------------------------
# Cluster health
# ---------------------------------------------------------------------------


class TestHDFSClusterHealth:
    """Verify the HDFS cluster is operational before running storage tests."""

    def test_namenode_reachable(self, hdfs):
        """Namenode WebHDFS API responds."""
        s = hdfs.status("/")
        assert s is not None
        assert s["type"] == "DIRECTORY"

    def test_root_listing(self, hdfs):
        """Can list the root directory."""
        entries = hdfs.listdir("/")
        assert isinstance(entries, list)


# ---------------------------------------------------------------------------
# File operations
# ---------------------------------------------------------------------------


class TestHDFSFileOperations:
    """Test CRUD file operations via WebHDFS."""

    def test_mkdirs(self, hdfs, workdir):
        assert hdfs.isdir(workdir) is True
        nested = f"{workdir}/a/b/c"
        hdfs.mkdirs(nested)
        assert hdfs.isdir(nested) is True

    def test_create_and_read(self, hdfs, workdir):
        path = f"{workdir}/hello.txt"
        payload = b"hello hdfs"
        hdfs.create(path, payload)
        assert hdfs.read(path) == payload

    def test_exists(self, hdfs, workdir):
        assert hdfs.exists(workdir) is True
        assert hdfs.exists(f"{workdir}/nonexistent.txt") is False

    def test_isfile(self, hdfs, workdir):
        path = f"{workdir}/file.txt"
        assert hdfs.isfile(path) is False
        hdfs.create(path, b"x")
        assert hdfs.isfile(path) is True
        assert hdfs.isfile(workdir) is False

    def test_isdir(self, hdfs, workdir):
        assert hdfs.isdir(workdir) is True
        path = f"{workdir}/file.txt"
        hdfs.create(path, b"x")
        assert hdfs.isdir(path) is False

    def test_file_size(self, hdfs, workdir):
        path = f"{workdir}/sized.txt"
        payload = b"12345"
        hdfs.create(path, payload)
        s = hdfs.status(path)
        assert s["length"] == len(payload)

    def test_modification_time(self, hdfs, workdir):
        path = f"{workdir}/mtime.txt"
        hdfs.create(path, b"x")
        s = hdfs.status(path)
        assert s["modificationTime"] > 0

    def test_overwrite(self, hdfs, workdir):
        path = f"{workdir}/overwrite.txt"
        hdfs.create(path, b"first")
        hdfs.create(path, b"second")
        assert hdfs.read(path) == b"second"


# ---------------------------------------------------------------------------
# Directory operations
# ---------------------------------------------------------------------------


class TestHDFSDirectoryOperations:
    """Test directory listing and traversal."""

    def test_listdir_files(self, hdfs, workdir):
        for name in ("a.txt", "b.txt", "c.txt"):
            hdfs.create(f"{workdir}/{name}", name.encode())
        entries = hdfs.listdir(workdir)
        names = sorted(e["pathSuffix"] for e in entries)
        assert names == ["a.txt", "b.txt", "c.txt"]

    def test_listdir_mixed(self, hdfs, workdir):
        hdfs.mkdirs(f"{workdir}/subdir")
        hdfs.create(f"{workdir}/file.txt", b"x")
        entries = hdfs.listdir(workdir)
        names = sorted(e["pathSuffix"] for e in entries)
        assert names == ["file.txt", "subdir"]
        types = {e["pathSuffix"]: e["type"] for e in entries}
        assert types["file.txt"] == "FILE"
        assert types["subdir"] == "DIRECTORY"

    def test_listdir_empty(self, hdfs, workdir):
        entries = hdfs.listdir(workdir)
        assert entries == []

    def test_nested_directories(self, hdfs, workdir):
        hdfs.mkdirs(f"{workdir}/a/b/c")
        assert hdfs.isdir(f"{workdir}/a") is True
        assert hdfs.isdir(f"{workdir}/a/b") is True
        assert hdfs.isdir(f"{workdir}/a/b/c") is True


# ---------------------------------------------------------------------------
# Delete operations
# ---------------------------------------------------------------------------


class TestHDFSDeleteOperations:
    """Test delete and recursive delete."""

    def test_delete_file(self, hdfs, workdir):
        path = f"{workdir}/to_delete.txt"
        hdfs.create(path, b"gone")
        assert hdfs.delete(path) is True
        assert hdfs.exists(path) is False

    def test_delete_nonexistent(self, hdfs, workdir):
        result = hdfs.delete(f"{workdir}/no_such_file.txt")
        assert result is False

    def test_delete_recursive(self, hdfs, workdir):
        sub = f"{workdir}/subtree"
        hdfs.mkdirs(f"{sub}/deep/nested")
        hdfs.create(f"{sub}/deep/nested/file.txt", b"data")
        hdfs.create(f"{sub}/root.txt", b"data")
        assert hdfs.delete(sub, recursive=True) is True
        assert hdfs.exists(sub) is False


# ---------------------------------------------------------------------------
# Large(r) payloads
# ---------------------------------------------------------------------------


class TestHDFSPayloads:
    """Test with non-trivial data sizes."""

    def test_medium_payload(self, hdfs, workdir):
        """Write and read back a 1 MB file."""
        path = f"{workdir}/medium.bin"
        payload = b"A" * (1024 * 1024)
        hdfs.create(path, payload)
        data = hdfs.read(path)
        assert len(data) == len(payload)
        assert data == payload

    def test_binary_payload(self, hdfs, workdir):
        """Write and read back binary data with all byte values."""
        path = f"{workdir}/binary.bin"
        payload = bytes(range(256)) * 100
        hdfs.create(path, payload)
        assert hdfs.read(path) == payload


# ---------------------------------------------------------------------------
# FileSystem facade, end-to-end against HDFS
# ---------------------------------------------------------------------------


class TestHDFSFileSystemFacade:
    """Drive the init/config-driven facade against HDFS with bare paths, then
    confirm independently (via the WebHDFS helper) that bytes land in HDFS.

    Note: WebHDFS redirects the client to the datanode at its registered
    hostname. From the Docker host that must resolve to a reachable address —
    run the single-node cluster with ``--hostname localhost`` (and publish the
    datanode HTTP port) or run in-cluster. If the datanode is unreachable the
    facade write's redirect hop raises ConnectionError and the test skips
    rather than fails.
    """

    def _facade(self, root):
        from facetwork.runtime.storage import configure_fs

        return configure_fs(backend="hdfs", root=root)

    def test_live_facade_roundtrip(self, hdfs, workdir):
        import json

        import requests as _rq

        from facetwork.runtime.storage import reset_fs

        root = f"hdfs://localhost{workdir}"
        try:
            fs = self._facade(root)
            assert fs.kind == "hdfs"
            assert fs.resolve("a/b.json") == f"{root}/a/b.json"

            obj = {"count": 42, "names": ["café", "二"], "nested": {"x": [1, 2, 3]}}
            try:
                fs.write_json("regions/summary.json", obj)
            except _rq.exceptions.ConnectionError as exc:
                pytest.skip(f"HDFS datanode not reachable from host (redirect): {exc}")
            assert fs.read_json("regions/summary.json") == obj

            blob = bytes(range(256)) * 4
            fs.write_bytes("data/blob.bin", blob)
            assert fs.read_bytes("data/blob.bin") == blob
            assert fs.getsize("data/blob.bin") == len(blob)

            fs.write_text("notes/u.txt", "héllo 世界")
            assert fs.read_text("notes/u.txt") == "héllo 世界"

            assert fs.exists("regions/summary.json") is True
            assert fs.exists("regions/nope.json") is False
            assert fs.isfile("data/blob.bin") is True
            assert fs.isdir("regions") is True
            assert "summary.json" in fs.listdir("regions")

            # explicit hdfs:// URI overrides the configured default
            fs.write_text(f"{root}/explicit/x.txt", "via explicit uri")
            assert fs.read_text(f"{root}/explicit/x.txt") == "via explicit uri"

            # independence: read via the WebHDFS helper (localizes redirects)
            assert json.loads(hdfs.read(f"{workdir}/regions/summary.json")) == obj
            assert hdfs.read(f"{workdir}/data/blob.bin") == blob
        finally:
            reset_fs()

    def test_live_facade_autodetect(self, hdfs, workdir, monkeypatch):
        import requests as _rq

        from facetwork.runtime.storage import FileSystem

        base = f"{workdir}/auto"
        monkeypatch.setenv("FW_STORAGE", "hdfs")
        monkeypatch.setenv("FW_HDFS_HOST", "localhost")
        monkeypatch.setenv("FW_HDFS_BASE", base)

        fs = FileSystem(backend="auto", root="")
        assert fs.kind == "hdfs"
        assert fs.root == f"hdfs://localhost{base}"
        try:
            fs.write_text("hello.txt", "auto wrote me")
        except _rq.exceptions.ConnectionError as exc:
            pytest.skip(f"HDFS datanode not reachable from host (redirect): {exc}")
        assert hdfs.read(f"{base}/hello.txt") == b"auto wrote me"

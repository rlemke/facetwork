"""Tests for afl.runtime.storage — StorageBackend abstraction layer."""

import os
from unittest.mock import MagicMock, patch

import pytest

from facetwork.runtime.storage import (
    HDFSStorageBackend,
    LocalStorageBackend,
    _hdfs_retry,
    _should_localize_mount,
    get_storage_backend,
    localize,
)

# ---------------------------------------------------------------------------
# TestLocalStorageBackend
# ---------------------------------------------------------------------------


class TestLocalStorageBackend:
    """Tests for LocalStorageBackend (wraps os / builtins)."""

    def test_exists_true(self, tmp_path):
        backend = LocalStorageBackend()
        f = tmp_path / "hello.txt"
        f.write_text("hi")
        assert backend.exists(str(f)) is True

    def test_exists_false(self, tmp_path):
        backend = LocalStorageBackend()
        assert backend.exists(str(tmp_path / "nope.txt")) is False

    def test_open_write_and_read(self, tmp_path):
        backend = LocalStorageBackend()
        path = str(tmp_path / "data.txt")
        with backend.open(path, "w") as fh:
            fh.write("hello world")
        with backend.open(path, "r") as fh:
            assert fh.read() == "hello world"

    def test_makedirs(self, tmp_path):
        backend = LocalStorageBackend()
        nested = str(tmp_path / "a" / "b" / "c")
        backend.makedirs(nested)
        assert os.path.isdir(nested)

    def test_makedirs_exist_ok(self, tmp_path):
        backend = LocalStorageBackend()
        nested = str(tmp_path / "x")
        backend.makedirs(nested)
        backend.makedirs(nested, exist_ok=True)  # should not raise

    def test_getsize(self, tmp_path):
        backend = LocalStorageBackend()
        f = tmp_path / "size.txt"
        f.write_text("12345")
        assert backend.getsize(str(f)) == 5

    def test_getmtime(self, tmp_path):
        backend = LocalStorageBackend()
        f = tmp_path / "mtime.txt"
        f.write_text("x")
        mtime = backend.getmtime(str(f))
        assert isinstance(mtime, float)
        assert mtime > 0

    def test_isfile(self, tmp_path):
        backend = LocalStorageBackend()
        f = tmp_path / "f.txt"
        f.write_text("x")
        assert backend.isfile(str(f)) is True
        assert backend.isfile(str(tmp_path)) is False

    def test_isdir(self, tmp_path):
        backend = LocalStorageBackend()
        assert backend.isdir(str(tmp_path)) is True
        f = tmp_path / "f.txt"
        f.write_text("x")
        assert backend.isdir(str(f)) is False

    def test_listdir(self, tmp_path):
        backend = LocalStorageBackend()
        (tmp_path / "a.txt").write_text("a")
        (tmp_path / "b.txt").write_text("b")
        entries = backend.listdir(str(tmp_path))
        assert sorted(entries) == ["a.txt", "b.txt"]

    def test_walk(self, tmp_path):
        backend = LocalStorageBackend()
        sub = tmp_path / "sub"
        sub.mkdir()
        (tmp_path / "root.txt").write_text("r")
        (sub / "child.txt").write_text("c")

        walked = list(backend.walk(str(tmp_path)))
        assert len(walked) == 2

        root_dir, root_dirs, root_files = walked[0]
        assert root_dir == str(tmp_path)
        assert "sub" in root_dirs
        assert "root.txt" in root_files

    def test_rmtree(self, tmp_path):
        backend = LocalStorageBackend()
        d = tmp_path / "todelete"
        d.mkdir()
        (d / "inside.txt").write_text("x")
        backend.rmtree(str(d))
        assert not d.exists()

    def test_join(self):
        backend = LocalStorageBackend()
        result = backend.join("/a", "b", "c.txt")
        assert result == os.path.join("/a", "b", "c.txt")

    def test_dirname(self):
        backend = LocalStorageBackend()
        assert backend.dirname("/a/b/c.txt") == "/a/b"

    def test_basename(self):
        backend = LocalStorageBackend()
        assert backend.basename("/a/b/c.txt") == "c.txt"

    def test_open_context_manager(self, tmp_path):
        backend = LocalStorageBackend()
        path = str(tmp_path / "ctx.txt")
        with backend.open(path, "w") as fh:
            fh.write("test")
        # File should be closed after context manager exits
        with backend.open(path, "r") as fh:
            assert fh.read() == "test"


# ---------------------------------------------------------------------------
# TestHDFSStorageBackend
# ---------------------------------------------------------------------------


class TestHDFSStorageBackend:
    """Tests for HDFSStorageBackend (WebHDFS via mocked requests)."""

    def _make_backend(self, **kwargs):
        """Create an HDFSStorageBackend with requests available."""
        import afl.runtime.storage as mod

        orig = mod.HAS_REQUESTS
        mod.HAS_REQUESTS = True
        try:
            backend = HDFSStorageBackend(**kwargs)
        finally:
            mod.HAS_REQUESTS = orig
        return backend

    def test_missing_requests_raises(self):
        with patch("facetwork.runtime.storage.HAS_REQUESTS", False):
            with pytest.raises(RuntimeError, match="requests is required"):
                HDFSStorageBackend()

    def test_init(self):
        backend = self._make_backend(host="namenode", port=8020, user="hdfs")
        assert "namenode" in backend._base_url
        assert backend._user == "hdfs"

    def test_strip_uri(self):
        backend = self._make_backend()
        assert backend._strip_uri("hdfs://namenode:8020/data/file.txt") == "/data/file.txt"
        assert backend._strip_uri("/local/path") == "/local/path"

    @patch("facetwork.runtime.storage._requests")
    def test_exists(self, mock_req):
        backend = self._make_backend()
        mock_req.get.return_value = MagicMock(status_code=200)
        assert backend.exists("hdfs://host:8020/data/file.txt") is True

    @patch("facetwork.runtime.storage._requests")
    def test_exists_not_found(self, mock_req):
        backend = self._make_backend()
        mock_req.get.return_value = MagicMock(status_code=404)
        assert backend.exists("hdfs://host:8020/missing.txt") is False

    @patch("facetwork.runtime.storage._requests")
    def test_open_read(self, mock_req):
        backend = self._make_backend()
        mock_resp = MagicMock()
        mock_resp.text = "hello"
        mock_resp.raise_for_status = MagicMock()
        mock_req.get.return_value = mock_resp
        result = backend.open("hdfs://host:8020/data/file.txt", "r")
        assert result.read() == "hello"

    @patch("facetwork.runtime.storage._requests")
    def test_open_read_binary(self, mock_req):
        backend = self._make_backend()
        mock_resp = MagicMock()
        mock_resp.content = b"binary data"
        mock_resp.raise_for_status = MagicMock()
        mock_req.get.return_value = mock_resp
        result = backend.open("hdfs://host:8020/data/file.bin", "rb")
        assert result.read() == b"binary data"

    def test_open_write(self):
        backend = self._make_backend()
        stream = backend.open("hdfs://host:8020/data/file.txt", "w")
        from facetwork.runtime.storage import _WebHDFSWriteStream

        assert isinstance(stream, _WebHDFSWriteStream)

    @patch("facetwork.runtime.storage._requests")
    def test_makedirs(self, mock_req):
        backend = self._make_backend()
        mock_req.put.return_value = MagicMock(status_code=200, raise_for_status=MagicMock())
        backend.makedirs("hdfs://host:8020/data/newdir")
        mock_req.put.assert_called_once()
        call_args = mock_req.put.call_args
        assert "MKDIRS" in str(call_args)

    @patch("facetwork.runtime.storage._requests")
    def test_getsize(self, mock_req):
        backend = self._make_backend()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"FileStatus": {"length": 42}}
        mock_resp.raise_for_status = MagicMock()
        mock_req.get.return_value = mock_resp
        assert backend.getsize("/data/file.txt") == 42

    @patch("facetwork.runtime.storage._requests")
    def test_rmtree(self, mock_req):
        backend = self._make_backend()
        mock_req.delete.return_value = MagicMock(status_code=200, raise_for_status=MagicMock())
        backend.rmtree("hdfs://host:8020/data/dir")
        mock_req.delete.assert_called_once()
        call_args = mock_req.delete.call_args
        assert "DELETE" in str(call_args)

    def test_join(self):
        backend = self._make_backend()
        assert backend.join("/data", "sub", "file.txt") == "/data/sub/file.txt"

    def test_dirname(self):
        backend = self._make_backend()
        assert backend.dirname("/data/sub/file.txt") == "/data/sub"

    def test_basename(self):
        backend = self._make_backend()
        assert backend.basename("/data/sub/file.txt") == "file.txt"

    @patch("facetwork.runtime.storage._requests")
    def test_getmtime(self, mock_req):
        backend = self._make_backend()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"FileStatus": {"modificationTime": 1700000000000}}
        mock_resp.raise_for_status = MagicMock()
        mock_req.get.return_value = mock_resp
        assert backend.getmtime("/data/file.txt") == 1700000000.0

    @patch("facetwork.runtime.storage._requests")
    def test_isfile(self, mock_req):
        backend = self._make_backend()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"FileStatus": {"type": "FILE"}}
        mock_resp.raise_for_status = MagicMock()
        mock_req.get.return_value = mock_resp
        assert backend.isfile("/data/file.txt") is True

    @patch("facetwork.runtime.storage._requests")
    def test_isdir(self, mock_req):
        backend = self._make_backend()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"FileStatus": {"type": "DIRECTORY"}}
        mock_resp.raise_for_status = MagicMock()
        mock_req.get.return_value = mock_resp
        assert backend.isdir("/data/dir") is True

    @patch("facetwork.runtime.storage._requests")
    def test_listdir(self, mock_req):
        backend = self._make_backend()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "FileStatuses": {
                "FileStatus": [
                    {"pathSuffix": "a.txt"},
                    {"pathSuffix": "b.txt"},
                ]
            }
        }
        mock_resp.raise_for_status = MagicMock()
        mock_req.get.return_value = mock_resp
        assert backend.listdir("/data") == ["a.txt", "b.txt"]


# ---------------------------------------------------------------------------
# TestHDFSRetry
# ---------------------------------------------------------------------------


class TestHDFSRetry:
    """Tests for the _hdfs_retry helper."""

    def test_success_no_retry(self):
        calls = []

        def fn():
            calls.append(1)
            return "ok"

        assert _hdfs_retry(fn, max_retries=3, base_delay=0) == "ok"
        assert len(calls) == 1

    def test_retries_on_404(self):
        import requests

        calls = []

        def fn():
            calls.append(1)
            if len(calls) < 3:
                resp = MagicMock(status_code=404)
                raise requests.exceptions.HTTPError(response=resp)
            return "recovered"

        assert _hdfs_retry(fn, max_retries=3, base_delay=0) == "recovered"
        assert len(calls) == 3

    def test_retries_on_connection_error(self):
        import requests

        calls = []

        def fn():
            calls.append(1)
            if len(calls) < 2:
                raise requests.exceptions.ConnectionError("refused")
            return "ok"

        assert _hdfs_retry(fn, max_retries=2, base_delay=0) == "ok"
        assert len(calls) == 2

    def test_raises_after_max_retries(self):
        import requests

        def fn():
            resp = MagicMock(status_code=404)
            raise requests.exceptions.HTTPError(response=resp)

        with pytest.raises(requests.exceptions.HTTPError):
            _hdfs_retry(fn, max_retries=2, base_delay=0)

    def test_no_retry_on_non_retryable_status(self):
        import requests

        calls = []

        def fn():
            calls.append(1)
            resp = MagicMock(status_code=403)
            raise requests.exceptions.HTTPError(response=resp)

        with pytest.raises(requests.exceptions.HTTPError):
            _hdfs_retry(fn, max_retries=3, base_delay=0)
        assert len(calls) == 1

    def test_no_retry_on_value_error(self):
        calls = []

        def fn():
            calls.append(1)
            raise ValueError("not retryable")

        with pytest.raises(ValueError):
            _hdfs_retry(fn, max_retries=3, base_delay=0)
        assert len(calls) == 1

    @patch("facetwork.runtime.storage._requests")
    def test_write_stream_retries(self, mock_req):
        """_WebHDFSWriteStream.close() retries on transient datanode 404."""
        import requests

        import afl.runtime.storage as mod

        orig = mod.HAS_REQUESTS
        mod.HAS_REQUESTS = True
        try:
            backend = HDFSStorageBackend(host="namenode", port=8020)
        finally:
            mod.HAS_REQUESTS = orig

        stream = backend.open("hdfs://namenode:8020/data/file.bin", "wb")
        stream.write(b"test data")

        # First call: namenode returns 307 redirect
        redirect_resp = MagicMock(
            status_code=307,
            headers={"Location": "http://datanode:9864/webhdfs/v1/data/file.bin?op=CREATE"},
        )
        redirect_resp.raise_for_status = MagicMock()

        # First attempt: datanode returns 404
        error_resp = MagicMock(status_code=404)
        error_404 = requests.exceptions.HTTPError(response=error_resp)

        # Second attempt: success
        success_resp = MagicMock(status_code=201)
        success_resp.raise_for_status = MagicMock()

        mock_req.put.side_effect = [
            redirect_resp,
            error_404,  # attempt 1: namenode OK, datanode 404
            redirect_resp,
            success_resp,  # attempt 2: both OK
        ]

        stream.close()  # should succeed after retry
        assert mock_req.put.call_count == 4


# ---------------------------------------------------------------------------
# TestGetStorageBackend
# ---------------------------------------------------------------------------


class TestGetStorageBackend:
    """Tests for the get_storage_backend factory function."""

    def setup_method(self):
        """Reset cached backends between tests."""
        import afl.runtime.storage as mod

        mod._local_backend = None
        mod._hdfs_backends.clear()

    def test_local_default(self):
        backend = get_storage_backend()
        assert isinstance(backend, LocalStorageBackend)

    def test_local_path(self):
        backend = get_storage_backend("/tmp/somepath")
        assert isinstance(backend, LocalStorageBackend)

    def test_local_singleton(self):
        b1 = get_storage_backend()
        b2 = get_storage_backend("/tmp/other")
        assert b1 is b2

    @patch("facetwork.runtime.storage.HDFSStorageBackend")
    def test_hdfs_uri(self, mock_hdfs_cls):
        mock_instance = MagicMock()
        mock_hdfs_cls.return_value = mock_instance
        backend = get_storage_backend("hdfs://namenode:8020/data")
        mock_hdfs_cls.assert_called_once_with(host="namenode", port=8020)
        assert backend is mock_instance

    @patch("facetwork.runtime.storage.HDFSStorageBackend")
    def test_hdfs_caching(self, mock_hdfs_cls):
        mock_instance = MagicMock()
        mock_hdfs_cls.return_value = mock_instance
        b1 = get_storage_backend("hdfs://namenode:8020/data/a")
        b2 = get_storage_backend("hdfs://namenode:8020/data/b")
        assert b1 is b2
        assert mock_hdfs_cls.call_count == 1


# ---------------------------------------------------------------------------
# TestLocalize — mount path localization
# ---------------------------------------------------------------------------


class TestLocalizeMounts:
    """Tests for localize() with AFL_LOCALIZE_MOUNTS."""

    def test_should_localize_mount_unset(self, monkeypatch):
        monkeypatch.delenv("AFL_LOCALIZE_MOUNTS", raising=False)
        assert _should_localize_mount("/data/osm-mirror/file.pbf") is False

    def test_should_localize_mount_empty(self, monkeypatch):
        monkeypatch.setenv("AFL_LOCALIZE_MOUNTS", "")
        assert _should_localize_mount("/data/osm-mirror/file.pbf") is False

    def test_should_localize_mount_match(self, monkeypatch):
        monkeypatch.setenv("AFL_LOCALIZE_MOUNTS", "/data/osm-mirror")
        assert _should_localize_mount("/data/osm-mirror/file.pbf") is True

    def test_should_localize_mount_no_match(self, monkeypatch):
        monkeypatch.setenv("AFL_LOCALIZE_MOUNTS", "/data/osm-mirror")
        assert _should_localize_mount("/tmp/local/file.pbf") is False

    def test_should_localize_mount_multiple(self, monkeypatch):
        monkeypatch.setenv("AFL_LOCALIZE_MOUNTS", "/mnt/a,/mnt/b")
        assert _should_localize_mount("/mnt/b/file.txt") is True
        assert _should_localize_mount("/mnt/c/file.txt") is False

    def test_localize_local_path_no_env(self, monkeypatch):
        """Local paths returned unchanged when AFL_LOCALIZE_MOUNTS is unset."""
        monkeypatch.delenv("AFL_LOCALIZE_MOUNTS", raising=False)
        assert localize("/data/osm-mirror/file.pbf") == "/data/osm-mirror/file.pbf"

    def test_localize_copies_mount_file(self, tmp_path, monkeypatch):
        """Mount-path files are copied to local cache."""
        # Create a fake "mount" file
        mount_dir = tmp_path / "mount"
        mount_dir.mkdir()
        src = mount_dir / "test.pbf"
        src.write_bytes(b"fake pbf data")

        monkeypatch.setenv("AFL_LOCALIZE_MOUNTS", str(mount_dir))

        cache_dir = tmp_path / "cache"
        result = localize(str(src), target_dir=str(cache_dir))

        assert result != str(src)
        assert result.startswith(str(cache_dir))
        assert os.path.isfile(result)
        assert open(result, "rb").read() == b"fake pbf data"

    def test_localize_mount_cache_hit(self, tmp_path, monkeypatch):
        """Cached copy is reused when sizes match."""
        mount_dir = tmp_path / "mount"
        mount_dir.mkdir()
        src = mount_dir / "test.pbf"
        src.write_bytes(b"fake pbf data")

        monkeypatch.setenv("AFL_LOCALIZE_MOUNTS", str(mount_dir))
        cache_dir = tmp_path / "cache"

        # First call copies
        result1 = localize(str(src), target_dir=str(cache_dir))
        mtime1 = os.path.getmtime(result1)

        # Second call should hit cache (same path returned, file not re-copied)
        result2 = localize(str(src), target_dir=str(cache_dir))
        assert result1 == result2
        assert os.path.getmtime(result2) == mtime1

    def test_localize_mount_cache_stale(self, tmp_path, monkeypatch):
        """Stale 0-byte cache file triggers re-copy."""
        mount_dir = tmp_path / "mount"
        mount_dir.mkdir()
        src = mount_dir / "test.pbf"
        src.write_bytes(b"real data")

        monkeypatch.setenv("AFL_LOCALIZE_MOUNTS", str(mount_dir))
        cache_dir = tmp_path / "cache"

        # Simulate a stale 0-byte file from a previous failed copy
        local_path = os.path.join(str(cache_dir), str(src).lstrip("/"))
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        open(local_path, "w").close()  # 0-byte file
        assert os.path.getsize(local_path) == 0

        result = localize(str(src), target_dir=str(cache_dir))
        assert open(result, "rb").read() == b"real data"

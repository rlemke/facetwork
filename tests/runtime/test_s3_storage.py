"""Tests for the S3 / MinIO storage backend.

Two tiers:

* **Path helpers + dispatch** — always run (boto3 client creation is lazy and
  makes no network call), so ``s3://`` URI parsing/joining and backend
  selection are covered without any server.
* **Live round-trip** — opt-in, gated on ``FW_S3_ENDPOINT`` pointing at a
  reachable S3/MinIO. Start one with::

      docker run -d -p 9000:9000 -e MINIO_ROOT_USER=minioadmin \\
          -e MINIO_ROOT_PASSWORD=minioadmin minio/minio server /data
      export FW_S3_ENDPOINT=http://localhost:9000 \\
          FW_S3_ACCESS_KEY=minioadmin FW_S3_SECRET_KEY=minioadmin
"""

from __future__ import annotations

import os
import uuid

import pytest

boto3 = pytest.importorskip("boto3")

from unittest.mock import MagicMock  # noqa: E402

from facetwork.runtime.storage import (  # noqa: E402
    S3StorageBackend,
    _BotoClientError,
    _s3_is_not_found,
    _S3WriteStream,
    _WebHDFSWriteStream,
    get_storage_backend,
    localize,
)


def _client_error(code: str, status: int) -> Exception:
    """Build a botocore-compatible ClientError with the given code/status."""
    resp = {"Error": {"Code": code}, "ResponseMetadata": {"HTTPStatusCode": status}}
    try:
        err = _BotoClientError(resp, "HeadObject")
    except TypeError:  # fallback when botocore is absent (_BotoClientError is Exception)
        err = _BotoClientError(resp)
    if not getattr(err, "response", None):
        err.response = resp
    return err


_LIVE = bool(os.environ.get("FW_S3_ENDPOINT"))
_live_only = pytest.mark.skipif(not _LIVE, reason="set FW_S3_ENDPOINT to run live S3/MinIO tests")
_BUCKET = os.environ.get("FW_S3_TEST_BUCKET", "afl-cache")


# --- path helpers + dispatch (no network) -------------------------------------


def test_dispatch_returns_s3_backend():
    assert isinstance(get_storage_backend("s3://bucket/key"), S3StorageBackend)


def test_path_helpers():
    b = S3StorageBackend()
    assert b._split("s3://bkt/a/b/c.json") == ("bkt", "a/b/c.json")
    assert b.join("s3://bkt/a", "b", "c.json") == "s3://bkt/a/b/c.json"
    assert b.join("s3://bkt/a/", "/b/", "c") == "s3://bkt/a/b/c"
    assert b.dirname("s3://bkt/a/b/c.json") == "s3://bkt/a/b"
    assert b.dirname("s3://bkt/top") == "s3://bkt"
    assert b.basename("s3://bkt/a/b/c.json") == "c.json"


# --- write-stream abort + error classification (no network) -------------------


def test_s3_write_stream_aborts_partial_object_on_exception():
    """Finding #13: if the writer body raises, the stream must NOT finalize a
    partial object — object stores have no partial-write semantics, so
    close()'ing would PUT a truncated object that consumers see as complete."""
    backend = MagicMock()
    with pytest.raises(RuntimeError):
        with _S3WriteStream(backend, "bkt", "key") as w:
            w.write(b"partial data")
            raise RuntimeError("handler died mid-write")
    backend._client.put_object.assert_not_called()


def test_s3_write_stream_finalizes_on_clean_exit():
    """A clean exit still uploads the buffered object exactly once."""
    backend = MagicMock()
    with _S3WriteStream(backend, "bkt", "key") as w:
        w.write(b"complete data")
    backend._client.put_object.assert_called_once()


def test_hdfs_write_stream_aborts_partial_object_on_exception(monkeypatch):
    """Same abort-on-exception contract for the WebHDFS write stream."""
    import facetwork.runtime.storage as storage_mod

    put = MagicMock()
    monkeypatch.setattr(storage_mod, "_requests", MagicMock(put=put))
    with pytest.raises(RuntimeError):
        with _WebHDFSWriteStream(MagicMock(), "/tmp/out.bin") as w:
            w.write(b"partial")
            raise RuntimeError("boom")
    put.assert_not_called()


def test_s3_is_not_found_only_true_for_404():
    assert _s3_is_not_found(_client_error("404", 404)) is True
    assert _s3_is_not_found(_client_error("NoSuchKey", 404)) is True
    # Auth / throttle / unrelated errors must NOT read as "absent".
    assert _s3_is_not_found(_client_error("403", 403)) is False
    assert _s3_is_not_found(_client_error("SlowDown", 503)) is False
    assert _s3_is_not_found(ValueError("not a client error")) is False


def test_isfile_reraises_non_404_error():
    """An auth/throttle error must not be masked as 'file absent'."""
    backend = S3StorageBackend.__new__(S3StorageBackend)
    backend._client = MagicMock()
    backend._client.head_object.side_effect = _client_error("403", 403)
    with pytest.raises(_BotoClientError):
        backend.isfile("s3://bkt/key")
    # A genuine 404 is still reported as absent.
    backend._client.head_object.side_effect = _client_error("404", 404)
    assert backend.isfile("s3://bkt/key") is False


# --- live round-trip (MinIO) --------------------------------------------------


@_live_only
def test_live_roundtrip(tmp_path):
    b = get_storage_backend("s3://x")
    prefix = f"s3://{_BUCKET}/_pytest/{uuid.uuid4().hex}"
    try:
        with b.open(f"{prefix}/dir/a.txt", "w") as f:
            f.write("hello-s3")
        with b.open(f"{prefix}/dir/sub/b.txt", "w") as f:
            f.write("nested")

        assert b.exists(f"{prefix}/dir/a.txt") is True
        assert b.isfile(f"{prefix}/dir/a.txt") is True
        assert b.isdir(f"{prefix}/dir") is True
        assert b.isfile(f"{prefix}/dir") is False
        assert b.open(f"{prefix}/dir/a.txt").read() == "hello-s3"
        assert b.getsize(f"{prefix}/dir/a.txt") == len("hello-s3")
        assert set(b.listdir(f"{prefix}/dir")) == {"a.txt", "sub"}

        local = localize(f"{prefix}/dir/a.txt", target_dir=str(tmp_path))
        assert open(local).read() == "hello-s3"
    finally:
        b.rmtree(prefix)
        assert b.isdir(prefix) is False


# --- FileSystem facade, end-to-end against MinIO ------------------------------
#
# Exercises the init/config-driven facade exactly as handler code would (bare,
# scheme-less paths under an s3:// root), and independently confirms via the raw
# backend that the bytes really land in the object store.


@_live_only
def test_live_facade_roundtrip():
    import json

    from facetwork.runtime.storage import configure_fs, get_storage_backend, reset_fs

    root = f"s3://{_BUCKET}/_pytest/{uuid.uuid4().hex}"
    try:
        fs = configure_fs(backend="s3", root=root)
        assert fs.kind == "s3"
        assert fs.resolve("a/b.json") == f"{root}/a/b.json"

        obj = {"count": 42, "names": ["café", "二"], "nested": {"x": [1, 2, 3]}}
        fs.write_json("regions/summary.json", obj)
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
        assert "summary.json" in fs.listdir("regions")

        # explicit s3:// URI overrides the configured default
        fs.write_text(f"{root}/explicit/x.txt", "via explicit uri")
        assert fs.read_text(f"{root}/explicit/x.txt") == "via explicit uri"

        # independence: the object is really in MinIO (raw backend, not the facade)
        raw = get_storage_backend(f"s3://{_BUCKET}/x")
        with raw.open(f"{root}/regions/summary.json", "rb") as f:
            assert json.loads(f.read()) == obj
    finally:
        reset_fs()
        get_storage_backend(f"s3://{_BUCKET}/x").rmtree(root)


@_live_only
def test_live_facade_autodetect(monkeypatch):
    from facetwork.runtime.storage import FileSystem, get_storage_backend

    prefix = uuid.uuid4().hex
    monkeypatch.setenv("FW_STORAGE", "s3")
    monkeypatch.setenv("FW_S3_BUCKET", _BUCKET)
    monkeypatch.setenv("FW_S3_PREFIX", f"_pytest/{prefix}")

    fs = FileSystem(backend="auto", root="")
    assert fs.kind == "s3"
    assert fs.root == f"s3://{_BUCKET}/_pytest/{prefix}"
    try:
        fs.write_text("hello.txt", "auto wrote me")
        raw = get_storage_backend(f"s3://{_BUCKET}/x")
        with raw.open(f"s3://{_BUCKET}/_pytest/{prefix}/hello.txt", "rb") as f:
            assert f.read() == b"auto wrote me"
    finally:
        get_storage_backend(f"s3://{_BUCKET}/x").rmtree(f"s3://{_BUCKET}/_pytest/{prefix}")

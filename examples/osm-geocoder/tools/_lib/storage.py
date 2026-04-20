"""Storage backend abstraction for tools.

Two backends are supported:

- ``local`` (default): standard POSIX filesystem via the Python stdlib.
- ``hdfs``: delegates to ``facetwork.runtime.storage.HDFSStorageBackend``
  (soft-imported; only loaded when the backend is selected). HDFS uses
  WebHDFS over HTTP, so no Hadoop native libraries are required.

The backend is chosen by ``AFL_OSM_STORAGE`` or the tool's ``--backend``
flag. Cache root comes from ``AFL_OSM_CACHE_ROOT`` (overrides everything)
or a backend-specific default:

- local: ``/Volumes/afl_data/osm``
- hdfs:  ``/user/afl/osm``

HDFS limitations (documented; acceptable for the current use case):

- **No advisory locking.** ``lock()`` is a no-op on HDFS. HDFS caches are
  assumed to be written by a single coordinated process (typically a batch
  job), not by ad-hoc concurrent invocations. The local backend still uses
  ``fcntl.flock`` for full read-modify-write safety.
- **In-memory write buffer.** The WebHDFS ``CREATE`` op used by the
  underlying backend buffers the entire file in RAM before uploading. This
  is fine for typical continent/country PBFs under a few GB but will not
  scale to the full planet. The local backend streams to disk normally.
- **Atomic rename is metadata-level.** WebHDFS ``RENAME`` is atomic at the
  namenode; if the destination exists it is removed first (not atomic
  across the pair of operations, but single-writer semantics make this
  safe).
"""

from __future__ import annotations

import abc
import fcntl
import os
import tempfile
from contextlib import contextmanager
from typing import IO, Iterator

LOCAL_DEFAULT_ROOT = "/Volumes/afl_data/osm"
HDFS_DEFAULT_ROOT = "/user/afl/osm"


class Storage(abc.ABC):
    """Minimal storage interface used by the OSM tools."""

    name: str  # "local" | "hdfs"

    @abc.abstractmethod
    def exists(self, path: str) -> bool: ...

    @abc.abstractmethod
    def size(self, path: str) -> int: ...

    @abc.abstractmethod
    def mkdir_p(self, path: str) -> None: ...

    @abc.abstractmethod
    def unlink(self, path: str) -> None:
        """Delete a file. No-op if missing."""

    @abc.abstractmethod
    def rename(self, src: str, dst: str) -> None:
        """Rename ``src`` to ``dst``, replacing any pre-existing destination."""

    @abc.abstractmethod
    def read_text(self, path: str) -> str: ...

    @abc.abstractmethod
    def write_text_atomic(self, path: str, text: str) -> None:
        """Write ``text`` to ``path``. Atomic where the backend supports it."""

    @abc.abstractmethod
    def open_write_binary(self, path: str) -> IO[bytes]:
        """Open a file for streaming binary writes. Caller must close."""

    @abc.abstractmethod
    @contextmanager
    def lock(self, path: str, *, exclusive: bool) -> Iterator[None]:
        """Advisory lock on ``path``. May be a no-op on some backends."""

    @property
    @abc.abstractmethod
    def supports_locking(self) -> bool: ...

    # Path arithmetic helpers — both backends use POSIX-style paths, so
    # string-level operations work uniformly.

    @staticmethod
    def join(*parts: str) -> str:
        out: list[str] = []
        for i, p in enumerate(parts):
            if not p:
                continue
            if i == 0:
                out.append(p.rstrip("/"))
            else:
                out.append(p.strip("/"))
        return "/".join(out) if out else ""

    @staticmethod
    def dirname(path: str) -> str:
        if "/" not in path:
            return ""
        return path.rsplit("/", 1)[0]


class LocalStorage(Storage):
    name = "local"

    @property
    def supports_locking(self) -> bool:
        return True

    def exists(self, path: str) -> bool:
        return os.path.exists(path)

    def size(self, path: str) -> int:
        return os.path.getsize(path)

    def mkdir_p(self, path: str) -> None:
        if path:
            os.makedirs(path, exist_ok=True)

    def unlink(self, path: str) -> None:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass

    def rename(self, src: str, dst: str) -> None:
        os.replace(src, dst)

    def read_text(self, path: str) -> str:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()

    def write_text_atomic(self, path: str, text: str) -> None:
        parent = os.path.dirname(path) or "."
        self.mkdir_p(parent)
        fd, tmp = tempfile.mkstemp(dir=parent, prefix=".tmp.", suffix=".swap")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def open_write_binary(self, path: str) -> IO[bytes]:
        parent = os.path.dirname(path)
        if parent:
            self.mkdir_p(parent)
        return open(path, "wb")

    @contextmanager
    def lock(self, path: str, *, exclusive: bool) -> Iterator[None]:
        parent = os.path.dirname(path)
        if parent:
            self.mkdir_p(parent)
        with open(path, "a+", encoding="utf-8") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
            try:
                yield
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)


class HdfsStorage(Storage):
    name = "hdfs"

    def __init__(self) -> None:
        try:
            from facetwork.runtime.storage import HDFSStorageBackend
        except ImportError as exc:
            raise RuntimeError(
                "HDFS backend unavailable: could not import "
                "facetwork.runtime.storage (requires the Facetwork runtime "
                f"package). Underlying error: {exc}"
            ) from exc
        self._backend = HDFSStorageBackend()

    @property
    def supports_locking(self) -> bool:
        return False

    def exists(self, path: str) -> bool:
        return self._backend.exists(path)

    def size(self, path: str) -> int:
        return self._backend.getsize(path)

    def mkdir_p(self, path: str) -> None:
        if path:
            self._backend.makedirs(path, exist_ok=True)

    def unlink(self, path: str) -> None:
        try:
            self._backend.remove(path)
        except FileNotFoundError:
            pass
        except Exception:
            # WebHDFS returns a generic HTTP error for missing paths; treat
            # as already-gone (matches POSIX ``os.unlink(missing_ok=True)``
            # semantics). If the path actually exists and removal failed
            # for another reason, the next operation on it will surface
            # that error.
            if not self._backend.exists(path):
                return
            raise

    def rename(self, src: str, dst: str) -> None:
        """WebHDFS RENAME. Removes any pre-existing destination first."""
        import requests as _requests

        if self._backend.exists(dst):
            self._backend.remove(dst)
        url = self._backend._url(self._backend._strip_uri(src))
        params = self._backend._params(
            op="RENAME", destination=self._backend._strip_uri(dst)
        )
        response = _requests.put(
            url, params=params, allow_redirects=True, timeout=30
        )
        response.raise_for_status()
        result = response.json()
        if not result.get("boolean"):
            raise RuntimeError(f"HDFS rename failed: {src} -> {dst}")

    def read_text(self, path: str) -> str:
        with self._backend.open(path, "r") as f:
            return f.read()

    def write_text_atomic(self, path: str, text: str) -> None:
        # HDFS single-writer semantics: no tmp+rename, just overwrite.
        parent = self.dirname(path)
        if parent:
            self.mkdir_p(parent)
        with self._backend.open(path, "w") as f:
            f.write(text)

    def open_write_binary(self, path: str) -> IO[bytes]:
        parent = self.dirname(path)
        if parent:
            self.mkdir_p(parent)
        return self._backend.open(path, "wb")

    @contextmanager
    def lock(self, path: str, *, exclusive: bool) -> Iterator[None]:
        # No-op: HDFS does not support advisory locking. See module docstring.
        yield


def default_backend() -> str:
    return (os.environ.get("AFL_OSM_STORAGE") or "local").lower()


def default_cache_root(backend: str) -> str:
    env = os.environ.get("AFL_OSM_CACHE_ROOT")
    if env:
        return env
    return HDFS_DEFAULT_ROOT if backend == "hdfs" else LOCAL_DEFAULT_ROOT


def get_storage(backend: str | None = None) -> Storage:
    name = (backend or default_backend()).lower()
    if name == "local":
        return LocalStorage()
    if name == "hdfs":
        return HdfsStorage()
    raise ValueError(
        f"Unknown storage backend: {name!r} (expected 'local' or 'hdfs')"
    )

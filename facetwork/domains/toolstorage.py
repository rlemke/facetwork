"""Local-or-S3 storage for a domain's ``tools/`` package.

⚠️ A DIFFERENT shape from ``facetwork.domains.storage.DomainStorage``, and
deliberately not folded into it. This one:

* dispatches on the PATH's scheme rather than a configured backend object,
* uses a SHARED cache root with no per-domain segment,
* lists files RECURSIVELY and returns paths RELATIVE to the directory,
* writes local files atomically via ``.tmp`` + ``os.replace`` + ``fsync``.

fwh_groupphoto and fwh_sentinel2 shipped this module byte-identically apart from
their docstring and ``NAMESPACE``. This is that code, moved once, parameterised
by the two names that varied. Nothing else changed: mapping it onto
``DomainStorage`` would have altered listing semantics, local atomicity and the
root defaults all at once, which is a rewrite wearing a refactor's clothes.

S3 goes through ``facetwork.runtime.storage`` — ONE S3 client in the codebase,
built from the same ``FW_S3_*`` env this module used to read itself (verified
identical before the switch).

⚠️ The LOCAL write stays here and stays atomic (``.tmp`` + ``fsync`` +
``os.replace``). Core's ``write_bytes`` does none of that — no tmp file, no
rename — so routing the local path through it as well would have silently traded
a crash-safety guarantee for tidiness. One S3 client was the goal; losing atomic
writes was not.
"""
from __future__ import annotations

import os


class ToolStorage:
    """One backend, selected by ``FW_STORAGE`` (``local`` default, or ``s3``)."""

    LOCAL_DEFAULT_ROOT = os.path.expanduser("~/afl_data")
    S3_DEFAULT_ROOT = "s3://afl-cache"

    def __init__(self, namespace: str, *, output_base_env: str | None = None) -> None:
        if not namespace:
            raise ValueError("namespace is required")
        self.namespace = namespace
        #: ⚠️ Not derived from the namespace. fwh_sentinel2 reads
        #: FW_S2_OUTPUT_BASE while its namespace is "s2" and its package is
        #: "sentinel2"; guessing would silently ignore an operator's setting.
        self.output_base_env = output_base_env or f"FW_{namespace.upper()}_OUTPUT_BASE"

    # ── backend ──────────────────────────────────────────────────────────────

    def backend(self) -> str:
        """The configured backend NAME (a string), not a backend object."""
        return (os.environ.get("FW_STORAGE") or "local").lower()

    @staticmethod
    def is_s3(path: str) -> bool:
        return isinstance(path, str) and path.startswith("s3://")

    # ── roots ────────────────────────────────────────────────────────────────

    def data_root(self) -> str:
        env = os.environ.get("FW_DATA_ROOT")
        if env:
            return env
        return self.S3_DEFAULT_ROOT if self.backend() == "s3" else self.LOCAL_DEFAULT_ROOT

    def cache_root(self) -> str:
        return os.environ.get("FW_CACHE_ROOT") or self.join(self.data_root(), "cache")

    def output_root(self) -> str:
        """Where a rendered bundle is written.

        ⚠️ On ``s3``, ``FW_OUTPUT_BASE`` is honoured ONLY when it is itself an
        ``s3://`` URI — under the runtime's staging model it is usually a LOCAL
        scratch dir, and writing the published bundle there would leave it on one
        runner's disk instead of in the object store.
        """
        if self.backend() == "s3":
            ob = os.environ.get(self.output_base_env) or os.environ.get("FW_OUTPUT_BASE")
            if ob and self.is_s3(ob):
                return ob
            return self.join(self.data_root(), "output")
        return os.environ.get("FW_OUTPUT_BASE") or self.join(self.data_root(), "output")

    @classmethod
    def join(cls, *parts: str) -> str:
        """⚠️ ``os.path.join`` for local paths, POSIX for ``s3://``. Not the
        module-level ``join`` in domains/storage.py, which is POSIX always."""
        parts = [p for p in parts if p]
        if not parts:
            return ""
        if cls.is_s3(parts[0]):
            head = parts[0].rstrip("/")
            tail = "/".join(p.strip("/") for p in parts[1:])
            return head + ("/" + tail if tail else "")
        return os.path.join(*parts)

    # ── S3, through the one client ───────────────────────────────────────────

    @staticmethod
    def _backend(path: str):
        """The runtime's backend for `path`.

        Previously this module built its own boto3 client. That client read
        exactly the same env (FW_S3_ENDPOINT / FW_S3_REGION / FW_S3_ACCESS_KEY /
        FW_S3_SECRET_KEY, falling back to AWS_*) with the same precedence and the
        same us-east-1 default as ``S3StorageBackend`` — verified line by line
        before removing it — so this is the same connection, made once.
        """
        from facetwork.runtime.storage import get_storage_backend

        return get_storage_backend(path)

    # ── ops, dispatched on the path ──────────────────────────────────────────

    def exists(self, path: str) -> bool:
        if self.is_s3(path):
            return bool(self._backend(path).exists(path))
        return os.path.exists(path)

    def read_bytes(self, path: str) -> bytes:
        if self.is_s3(path):
            with self._backend(path).open(path, "rb") as fh:
                return fh.read()
        with open(path, "rb") as f:
            return f.read()

    def write_bytes(self, path: str, data: bytes) -> None:
        if self.is_s3(path):
            with self._backend(path).open(path, "wb") as fh:
                fh.write(data)
            return
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        # ⚠️ Atomic: a reader never sees a half-written file, and a crash leaves
        # the previous version intact rather than a truncated one.
        tmp = path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)

    def read_text(self, path: str) -> str:
        return self.read_bytes(path).decode("utf-8")

    def write_text(self, path: str, text: str) -> None:
        self.write_bytes(path, text.encode("utf-8"))

    def list_files(self, dir_path: str) -> list[str]:
        """File paths RELATIVE to `dir_path`, RECURSIVE. Local dir or S3 prefix."""
        if self.is_s3(dir_path):
            root = dir_path.rstrip("/")
            out: list[str] = []
            for cur, _dirs, files in self._backend(root).walk(root):
                base = cur.rstrip("/")
                rel = base[len(root):].lstrip("/")
                out.extend(f"{rel}/{fn}" if rel else fn for fn in files)
            return out
        if not os.path.isdir(dir_path):
            return []
        out = []
        for root, _dirs, files in os.walk(dir_path):
            for fn in files:
                out.append(os.path.relpath(os.path.join(root, fn), dir_path))
        return out


def tool_storage(namespace: str, **kw) -> ToolStorage:
    return ToolStorage(namespace, **kw)

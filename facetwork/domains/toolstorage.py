"""Local-or-S3 storage for a domain's ``tools/`` package.

⚠️ A DIFFERENT shape from ``facetwork.domains.storage.DomainStorage``, and
deliberately not folded into it. This one:

* dispatches on the PATH's scheme rather than a configured backend object,
* talks to boto3 directly (so ``FW_STORAGE=s3`` works in a plain CLI with no
  runtime configured),
* uses a SHARED cache root with no per-domain segment,
* lists files RECURSIVELY and returns paths RELATIVE to the directory,
* writes local files atomically via ``.tmp`` + ``os.replace`` + ``fsync``.

fwh_groupphoto and fwh_sentinel2 shipped this module byte-identically apart from
their docstring and ``NAMESPACE``. This is that code, moved once, parameterised
by the two names that varied. Nothing else changed: mapping it onto
``DomainStorage`` would have altered listing semantics, local atomicity and the
root defaults all at once, which is a rewrite wearing a refactor's clothes.

⚠️ The boto3 dependency is the honest state of things, not an endorsement. A
later change can route this through ``facetwork.runtime.storage`` so there is one
S3 client in the codebase — but that is a MECHANISM change and belongs in its own
step, verified separately.
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

    # ── S3 client ────────────────────────────────────────────────────────────

    @staticmethod
    def _s3():
        import boto3

        return boto3.client(
            "s3",
            endpoint_url=os.environ.get("FW_S3_ENDPOINT") or None,
            region_name=os.environ.get("FW_S3_REGION", "us-east-1"),
            aws_access_key_id=(os.environ.get("FW_S3_ACCESS_KEY")
                               or os.environ.get("AWS_ACCESS_KEY_ID")),
            aws_secret_access_key=(os.environ.get("FW_S3_SECRET_KEY")
                                   or os.environ.get("AWS_SECRET_ACCESS_KEY")),
        )

    @staticmethod
    def _split(uri: str) -> tuple[str, str]:
        bucket, _, key = uri[len("s3://"):].partition("/")
        return bucket, key

    # ── ops, dispatched on the path ──────────────────────────────────────────

    def exists(self, path: str) -> bool:
        if self.is_s3(path):
            from botocore.exceptions import ClientError

            b, k = self._split(path)
            try:
                self._s3().head_object(Bucket=b, Key=k)
                return True
            except ClientError:
                return False
        return os.path.exists(path)

    def read_bytes(self, path: str) -> bytes:
        if self.is_s3(path):
            b, k = self._split(path)
            return self._s3().get_object(Bucket=b, Key=k)["Body"].read()
        with open(path, "rb") as f:
            return f.read()

    def write_bytes(self, path: str, data: bytes) -> None:
        if self.is_s3(path):
            b, k = self._split(path)
            self._s3().put_object(Bucket=b, Key=k, Body=data)
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
            b, prefix = self._split(dir_path.rstrip("/") + "/")
            out: list[str] = []
            for page in self._s3().get_paginator("list_objects_v2").paginate(
                    Bucket=b, Prefix=prefix):
                for obj in page.get("Contents", []):
                    out.append(obj["Key"][len(prefix):])
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

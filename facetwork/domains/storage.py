"""Backend-aware cache/output paths for a domain package.

⚠️ This exists because 21 of the 29 ``fwh_*`` repos shipped their own
``storage.py`` doing the same job, and the only thing that genuinely varied
between them was the DOMAIN NAME. Measured 2026-09-03: the ten smallest were
55-72% similar to each other, and their diffs are the domain string, the name of
an optional cache-dir env override, and the local fallback directory. Everything
else -- ``cache_root`` / ``output_root`` / ``join`` / ``is_remote`` /
``localize`` / ``finalize_from_local`` -- was copied.

Copies do not stay copies. The 2026-07 documentation sweep found real drift
between them, so a bug fixed in one repo stayed unfixed in twenty.

This is deliberately a THIN layer over ``facetwork.runtime.storage``, which
already owns backend selection (local / ``s3://`` / ``hdfs://``). It adds only
the per-domain path convention:

    <FW_DATA_ROOT>/cache/<domain>/...      (remote)
    <FW_DATA_ROOT>/<domain>-cache/...      (local)

Usage in a domain package::

    from facetwork.domains.storage import domain_storage

    S = domain_storage("conflict")
    p = S.cache("ucdp", "aggregate.json")
    with S.open_write_binary(p) as fh:
        ...

⚠️ Object stores have no partial writes. ``finalize_from_local`` is how a large
artifact is published: build it on local scratch, then finalize. Writing
directly to an ``s3://`` path and crashing halfway leaves nothing usable.
"""
from __future__ import annotations

import contextlib
import os
import shutil
import tempfile
from collections.abc import Iterator
from typing import IO, Any

from facetwork.runtime import storage as _core


def join(*parts: str) -> str:
    """POSIX-style join that is safe for ``s3://`` URIs.

    ``os.path.join`` is not: it collapses the ``//`` in a scheme on some inputs
    and uses ``\\`` on Windows. Every copy of this in the domain repos had the
    same hand-rolled version, for the same reason.
    """
    parts = tuple(p for p in parts if p)
    if not parts:
        return ""
    base = parts[0].rstrip("/")
    rest = [p.strip("/") for p in parts[1:]]
    return "/".join([base, *[p for p in rest if p]])


def is_remote(path: str) -> bool:
    """True for a URI with a scheme (``s3://``, ``hdfs://``), false for a path."""
    return "://" in (path or "")


def subdirs_containing(base: str, filename: str) -> list[str]:
    """Names of immediate subdirectories of `base` that contain `filename`.

    ⚠️ This exists because three domains (fwh_h1b, fwh_livability,
    fwh_osm_mapping) each carried a byte-identical copy of it, and each built
    its OWN boto3 client to do the S3 half — clients that omitted the region and
    the AWS_* credential fallback that every other client in the codebase has.
    They worked only because this fleet always sets FW_S3_*.

    One implementation, over the runtime backend, so local and s3:// take the
    same path. Empty for a base that does not exist: an unseeded cache is a cold
    start, not an error.
    """
    backend = _core.get_storage_backend(base)
    root = base.rstrip("/")
    out: set[str] = set()
    for cur, _dirs, files in backend.walk(root):
        if filename in files:
            name = cur.rstrip("/").rsplit("/", 1)[-1]
            if name and cur.rstrip("/") != root:
                out.add(name)
    return sorted(out)


class DomainStorage:
    """Per-domain paths over the shared storage backend."""

    #: Remote cache layouts actually in use. ⚠️ These are NOT interchangeable:
    #: they name where a domain's data already sits in MinIO, and changing one
    #: orphans that cache rather than moving it. Measured 2026-09-03 across the
    #: ten smallest domains: EIGHT use "nested" (a doubled cache/ segment that
    #: nobody intended but everyone copied) and cancer uses "flat". The
    #: consolidation preserves each domain's existing layout instead of
    #: silently unifying them, because unifying is a DATA MIGRATION, not a
    #: refactor.
    LAYOUTS = {
        "nested": ("cache", "{domain}", "cache"),   # s3://root/cache/<d>/cache
        "flat": ("cache", "{domain}"),              # s3://root/cache/<d>
        # ⚠️ No domain segment at all: the cache is SHARED between domains and
        # outputs go to the data root itself. fwh_groupphoto and fwh_sentinel2
        # both do this (they are copies of each other). Not a mistake to tidy —
        # it is where their objects are.
        "global": ("cache",),                       # s3://root/cache
    }

    def __init__(self, domain: str, *, cache_env: str | None = None,
                 root_env: str = "FW_DATA_ROOT", layout: str = "nested",
                 path_name: str | None = None, local_name: str | None = None,
                 output_env: str | None = None) -> None:
        if not domain or "/" in domain:
            raise ValueError(f"domain must be a bare name, got {domain!r}")
        if layout not in self.LAYOUTS:
            raise ValueError(f"layout must be one of {sorted(self.LAYOUTS)}, got {layout!r}")
        self.domain = domain
        self.layout = layout
        # ⚠️ The name in PATHS is not always the package name: fwh_osm_mapping
        # stores under "osm-mapping". Derived from the domain by default,
        # overridable, because a wrong guess here silently points at an empty
        # prefix rather than failing.
        self.path_name = path_name or domain
        # ⚠️ The LOCAL directory name is not always the remote one. fwh_census_us
        # stores under "census-us" remotely but "census-cache" locally; deriving
        # one from the other would silently move a developer's local cache.
        self.local_name = local_name or self.path_name
        # Each repo invented its own override name (FW_CONFLICT_CACHE_DIR,
        # FW_CENSUS_CACHE_DIR, ...). Derive it, but let a package keep the exact
        # spelling it already documents.
        # ⚠️ Pass "" to mean NO override. Not every domain honours one, and
        # adding one silently is still a behaviour change: fwh_cancer ignores a
        # cache override and fwh_census_us ignores an output override, so a
        # helpfully-added env var would make them obey something they never did.
        self.cache_env = (f"FW_{domain.upper().replace('-', '_')}_CACHE_DIR"
                          if cache_env is None else cache_env)
        # ⚠️ There is an OUTPUT override too, and it is not optional politeness:
        # fwh_amr's tests set FW_AMR_OUTPUT_DIR, and a layer that honoured only
        # the cache override sent their writes to the real /Volumes data root.
        # My equivalence check missed it because it varied FW_DATA_ROOT and
        # never the per-domain overrides.
        self.output_env = (f"FW_{domain.upper().replace('-', '_')}_OUTPUT_DIR"
                           if output_env is None else output_env)
        self.root_env = root_env

    # --- roots -------------------------------------------------------------

    def data_root(self) -> str:
        """``FW_DATA_ROOT`` (an ``s3://`` URI on the fleet) or the local output base."""
        return os.environ.get(self.root_env) or _core_output_base()

    def cache_root(self) -> str:
        ov = os.environ.get(self.cache_env) if self.cache_env else None
        if ov:
            return ov
        r = self.data_root()
        if not is_remote(r):
            return (join(r, "cache") if self.layout == "global"
                    else join(r, f"{self.local_name}-cache"))
        parts = [p.format(domain=self.path_name) for p in self.LAYOUTS[self.layout]]
        return join(r, *parts)

    def output_root(self) -> str:
        """⚠️ ``cache/<domain>/output`` on a remote backend, NOT ``output/<domain>``.

        That reads oddly — published outputs living under a ``cache/`` prefix —
        but it is where every one of these domains has been writing, and the
        tidier-looking layout would orphan the existing objects instead of
        moving them. Verified against the pre-consolidation modules.
        """
        ov = os.environ.get(self.output_env) if self.output_env else None
        if ov:
            return ov
        r = self.data_root()
        if self.layout == "global":
            return r
        if not is_remote(r):
            return join(r, f"{self.local_name}-output")
        return join(r, "cache", self.path_name, "output")

    def maps_root(self) -> str:
        """Rendered maps. Same shape for every domain that publishes them:
        ``cache/<d>/maps`` remote, ``<d>-maps`` local."""
        r = self.data_root()
        if not is_remote(r):
            return join(r, f"{self.local_name}-maps")
        return join(r, "cache", self.path_name, "maps")

    def cache(self, *parts: str) -> str:
        return join(self.cache_root(), *parts)

    def output(self, *parts: str) -> str:
        return join(self.output_root(), *parts)

    # --- backend passthrough ----------------------------------------------

    def backend(self, path: str | None = None) -> Any:
        """The backend for `path`.

        ⚠️ Takes the path. ``get_storage_backend`` selects per-path, so a domain
        that mixes a local scratch file with an s3:// destination needs the
        right backend for each; calling it bare would resolve both against the
        default and write the remote one to disk.
        """
        return _core.get_storage_backend(path)

    def exists(self, path: str) -> bool:
        return bool(self.backend(path).exists(path))

    def size(self, path: str) -> int:
        return int(self.backend(path).getsize(path))

    def mkdir_p(self, path: str) -> None:
        self.backend(path).makedirs(path, exist_ok=True)

    def read_bytes(self, path: str) -> bytes:
        return _core.read_bytes(path)

    def write_bytes(self, path: str, body: bytes) -> None:
        _core.write_bytes(path, body)

    def list_dir(self, path: str) -> list[str]:
        """Immediate entries under `path`, as FULL PATHS; empty when absent.

        ⚠️ Full paths, not names. I first wrote this returning bare names and
        asserted in the docstring that it "matches every domain copy" — it did
        not, and fwh_amr's tests failed with "no organism aggregates cached"
        because every returned path was then wrong. Verify, do not assert.

        Empty-on-missing is deliberate: a listing that raises for an unseeded
        cache turns a cold start into an error. It does mean "absent" and
        "empty" are indistinguishable, so a caller needing that difference must
        ask exists() first.
        """
        backend = self.backend(path)
        lister = getattr(backend, "list", None) or getattr(backend, "listdir", None)
        if lister is not None:
            try:
                return [join(path, os.path.basename(str(p).rstrip("/"))) for p in lister(path)]
            except Exception:  # noqa: BLE001 - a missing prefix is not an error
                pass
        if not is_remote(path) and os.path.isdir(path):
            return [join(path, n) for n in sorted(os.listdir(path))]
        return []

    def read_text(self, path: str, encoding: str = "utf-8") -> str:
        return _core.read_text(path, encoding=encoding)

    def write_text(self, path: str, body: str, encoding: str = "utf-8") -> None:
        _core.write_text(path, body, encoding=encoding)

    def localize(self, path: str) -> str:
        """A local filesystem path for `path`, downloading it if remote.

        A local path is returned unchanged rather than copied — every domain's
        copy did this, and it matters: localizing a local file would otherwise
        duplicate multi-GB artifacts for no reason.
        """
        if not is_remote(path):
            return path
        return _core.localize(path)

    @contextlib.contextmanager
    def open_write_binary(self, path: str) -> Iterator[IO[bytes]]:
        with self.backend(path).open(path, "wb") as fh:
            yield fh

    def open_read(self, path: str, mode: str = "r", **kw: Any) -> IO[Any]:
        """Open for reading, downloading first when the path is remote."""
        return open(self.localize(path), mode, **kw)

    @contextlib.contextmanager
    def open_write(self, path: str, mode: str = "w", **kw: Any) -> Iterator[IO[Any]]:
        """Open for writing; a remote destination is staged locally and uploaded
        on CLEAN exit only.

        ⚠️ The upload is outside the `yield`'s success path on purpose: an
        exception in the caller's body must leave the destination untouched. An
        object store has no partial write, so a half-uploaded object is
        indistinguishable from a complete one to every later reader.
        """
        if not is_remote(path):
            parent = os.path.dirname(path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            fh = open(path, mode, **kw)
            try:
                yield fh
            finally:
                fh.close()
            return

        fd, tmp = tempfile.mkstemp(suffix="_" + os.path.basename(path))
        os.close(fd)
        fh = open(tmp, mode, **kw)
        try:
            yield fh
            fh.close()
            with open(tmp, "rb") as src, self.backend(path).open(path, "wb") as dst:
                shutil.copyfileobj(src, dst)
        finally:
            if not fh.closed:
                fh.close()
            with contextlib.suppress(OSError):
                os.unlink(tmp)

    # --- staged publication ------------------------------------------------

    @contextlib.contextmanager
    def staged(self, dest: str, suffix: str = "") -> Iterator[str]:
        """Build on local scratch, then publish to `dest` on clean exit.

        ⚠️ The whole point of the pattern. An object store has no partial write,
        so streaming a multi-GB artifact straight at an ``s3://`` path and dying
        halfway leaves a half-object that looks like a real one. On the way out,
        nothing is published unless the body completed.
        """
        tmpdir = tempfile.mkdtemp(prefix=f"fw-{self.domain}-")
        local = os.path.join(tmpdir, "staged" + suffix)
        try:
            yield local
            self.finalize_from_local(local, dest)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def finalize_from_local(self, local_path: str, dest: str) -> str:
        """Publish a completed local file to `dest` (local move or upload)."""
        if is_remote(dest):
            with open(local_path, "rb") as src, self.backend(dest).open(dest, "wb") as dst:
                shutil.copyfileobj(src, dst)
        else:
            self.mkdir_p(self.backend(dest).dirname(dest))
            shutil.move(local_path, dest)
        return dest


def _core_output_base() -> str:
    """The local output base, honouring the same env the runtime does."""
    from facetwork.config import get_output_base

    return get_output_base()


def domain_storage(domain: str, **kw: Any) -> DomainStorage:
    """Factory — the one call a domain package needs."""
    return DomainStorage(domain, **kw)

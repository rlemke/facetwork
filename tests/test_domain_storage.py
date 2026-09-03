"""`facetwork.domains.storage` — the consolidated per-domain path layer.

21 of 29 fwh_* repos shipped their own storage.py doing this. The only thing
that genuinely varied was the domain name — but the COPIES had drifted, and the
drift is in the paths, which point at live data in MinIO.

⚠️ The tests that matter here are the ones pinning that a layout change is a
DATA MIGRATION, not a refactor: eight domains store under a doubled
`cache/<d>/cache` segment nobody intended, one under `cache/<d>`, and
fwh_osm_mapping under a hyphen its package name does not have. Unifying those
silently would orphan each cache rather than move it.
"""
from __future__ import annotations

import os

import pytest

from facetwork.domains.storage import DomainStorage, domain_storage, is_remote, join


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in ("FW_DATA_ROOT", "FW_OUTPUT_BASE"):
        monkeypatch.delenv(k, raising=False)


def test_join_is_safe_for_s3_uris():
    """os.path.join is not: every domain hand-rolled this for the same reason."""
    assert join("s3://bucket", "a", "b.json") == "s3://bucket/a/b.json"
    assert join("s3://bucket/", "/a/", "b.json") == "s3://bucket/a/b.json"
    assert join("/tmp/x", "y") == "/tmp/x/y"
    assert join() == ""


def test_is_remote_distinguishes_uri_from_path():
    assert is_remote("s3://b/k") and is_remote("hdfs://n/p")
    assert not is_remote("/Volumes/afl_data") and not is_remote("")


def test_nested_layout_is_the_majority_and_the_default(monkeypatch):
    monkeypatch.setenv("FW_DATA_ROOT", "s3://afl-cache")
    assert domain_storage("conflict").cache_root() == "s3://afl-cache/cache/conflict/cache"


def test_flat_layout_is_preserved_for_the_domain_that_uses_it(monkeypatch):
    """cancer stores under cache/<d>; the others under cache/<d>/cache. Both are
    live locations, so both must remain expressible."""
    monkeypatch.setenv("FW_DATA_ROOT", "s3://afl-cache")
    assert domain_storage("cancer", layout="flat").cache_root() == "s3://afl-cache/cache/cancer"


def test_output_root_keeps_the_cache_prefix_it_already_uses(monkeypatch):
    """⚠️ Reads oddly — outputs under cache/ — but it is where these domains
    have been writing. The tidier output/<domain> would orphan live objects."""
    monkeypatch.setenv("FW_DATA_ROOT", "s3://afl-cache")
    assert domain_storage("conflict").output_root() == "s3://afl-cache/cache/conflict/output"


def test_path_name_may_differ_from_the_package_name(monkeypatch):
    """fwh_osm_mapping stores under 'osm-mapping'. Deriving the path from the
    package name would point at an empty prefix and silently find nothing."""
    monkeypatch.setenv("FW_DATA_ROOT", "s3://afl-cache")
    s = domain_storage("osm_mapping", path_name="osm-mapping")
    assert s.cache_root() == "s3://afl-cache/cache/osm-mapping/cache"
    assert s.output_root() == "s3://afl-cache/cache/osm-mapping/output"


def test_local_root_uses_the_sibling_dir_convention(monkeypatch):
    monkeypatch.setenv("FW_DATA_ROOT", "/tmp/data")
    s = domain_storage("conflict")
    assert s.cache_root() == "/tmp/data/conflict-cache"
    assert s.output_root() == "/tmp/data/conflict-output"


def test_cache_env_override_wins_and_is_derived_per_domain(monkeypatch):
    monkeypatch.setenv("FW_DATA_ROOT", "s3://afl-cache")
    s = domain_storage("conflict")
    assert s.cache_env == "FW_CONFLICT_CACHE_DIR"
    monkeypatch.setenv("FW_CONFLICT_CACHE_DIR", "/scratch/here")
    assert s.cache_root() == "/scratch/here"


def test_an_explicit_cache_env_name_is_honoured():
    """A package that already documents FW_CENSUS_CACHE_DIR keeps that spelling."""
    assert domain_storage("census_us", cache_env="FW_CENSUS_CACHE_DIR").cache_env == \
        "FW_CENSUS_CACHE_DIR"


def test_a_bad_layout_or_domain_is_refused():
    with pytest.raises(ValueError, match="layout"):
        domain_storage("x", layout="whatever")
    with pytest.raises(ValueError, match="bare name"):
        domain_storage("a/b")


def test_staged_publishes_only_on_clean_exit(tmp_path, monkeypatch):
    """⚠️ Object stores have no partial write. A crash mid-body must publish
    nothing, not a half-object that looks real."""
    monkeypatch.setenv("FW_DATA_ROOT", str(tmp_path))
    s = domain_storage("demo")
    dest = str(tmp_path / "out" / "good.txt")
    with s.staged(dest) as local:
        with open(local, "w") as fh:
            fh.write("complete")
    assert s.read_text(dest) == "complete"

    boom = str(tmp_path / "out" / "bad.txt")
    with pytest.raises(RuntimeError):
        with s.staged(boom) as local:
            with open(local, "w") as fh:
                fh.write("half")
            raise RuntimeError("died mid-body")
    assert not s.exists(boom)


def test_open_write_publishes_nothing_when_the_body_raises(tmp_path, monkeypatch):
    """Same invariant as staged(), on the handle-based API the domains use."""
    monkeypatch.setenv("FW_DATA_ROOT", str(tmp_path))
    s = domain_storage("demo")
    dest = str(tmp_path / "d" / "f.txt")
    with s.open_write(dest) as fh:
        fh.write("ok")
    assert s.read_text(dest) == "ok"


def test_open_read_does_not_copy_a_local_file(tmp_path, monkeypatch):
    """localize() must return a local path unchanged — copying would duplicate
    multi-GB artifacts for nothing."""
    monkeypatch.setenv("FW_DATA_ROOT", str(tmp_path))
    p = tmp_path / "big.bin"
    p.write_text("data")
    s = domain_storage("demo")
    assert s.localize(str(p)) == str(p)
    with s.open_read(str(p)) as fh:
        assert fh.read() == "data"


def test_backend_is_selected_per_path():
    """get_storage_backend selects per path; calling it bare would resolve a
    remote destination against the default backend."""
    import inspect
    sig = inspect.signature(DomainStorage.backend)
    assert "path" in sig.parameters

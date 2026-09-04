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


def test_list_dir_returns_full_paths(tmp_path, monkeypatch):
    """⚠️ Full paths, not names. I first wrote this returning bare names and
    asserted in the docstring that it matched every domain copy — it did not,
    and fwh_amr's tests failed because every returned path was wrong."""
    monkeypatch.setenv("FW_DATA_ROOT", str(tmp_path))
    d = tmp_path / "things"
    d.mkdir()
    (d / "a.json").write_text("{}")
    got = domain_storage("demo").list_dir(str(d))
    assert got == [str(d / "a.json")]


def test_list_dir_is_empty_for_a_missing_prefix(tmp_path, monkeypatch):
    monkeypatch.setenv("FW_DATA_ROOT", str(tmp_path))
    assert domain_storage("demo").list_dir(str(tmp_path / "nope")) == []


def test_output_env_override_is_honoured(monkeypatch):
    """fwh_amr's tests set FW_AMR_OUTPUT_DIR; a layer honouring only the cache
    override sent their writes to the real /Volumes data root."""
    monkeypatch.setenv("FW_DATA_ROOT", "s3://afl-cache")
    monkeypatch.setenv("FW_AMR_OUTPUT_DIR", "/scratch/out")
    assert domain_storage("amr").output_root() == "/scratch/out"


def test_an_override_can_be_disabled_explicitly(monkeypatch):
    """⚠️ Not every domain honours one, and adding one silently is still a
    behaviour change: fwh_cancer ignores a cache override, fwh_census_us
    ignores an output override."""
    monkeypatch.setenv("FW_DATA_ROOT", "s3://afl-cache")
    monkeypatch.setenv("FW_CANCER_CACHE_DIR", "/should/be/ignored")
    s = domain_storage("cancer", layout="flat", cache_env="")
    assert s.cache_root() == "s3://afl-cache/cache/cancer"


def test_global_layout_has_no_domain_segment(monkeypatch):
    """fwh_groupphoto / fwh_sentinel2 share one cache and write outputs at the
    data root itself."""
    monkeypatch.setenv("FW_DATA_ROOT", "s3://afl-cache")
    s = domain_storage("groupphoto", layout="global")
    assert s.cache_root() == "s3://afl-cache/cache"
    assert s.output_root() == "s3://afl-cache"


def test_there_is_one_s3_client_construction():
    """⚠️ The whole point. Four slightly different credential chains had grown:
    the backend's, toolstorage's, and two in fwh_osm — one pair omitting the
    region and the AWS_* fallback, the other hardcoding minioadmin."""
    import inspect

    import facetwork.runtime.storage as S
    src = inspect.getsource(S)
    assert src.count("_b.client(") + src.count("_boto3.client(") == 1
    assert "def s3_client(" in src


def test_the_shared_client_carries_the_minio_config():
    """MinIO needs s3v4 + PATH-style addressing. The ad-hoc clients omitted it
    and worked only because boto3 happens to choose path-style for a custom
    endpoint_url — making it explicit is what one client is for."""
    import inspect

    import facetwork.runtime.storage as S
    body = inspect.getsource(S.s3_client)
    assert "s3v4" in body and "addressing_style" in body


def test_client_defaults_can_be_pinned_by_a_caller():
    """fwh_osm passes its historical localhost/minioadmin defaults explicitly
    rather than reintroducing its own constructor. The default is a smell —
    it silently targets a local store when the env is unset — but removing it
    changes behaviour for the live planet pipeline."""
    import inspect

    import facetwork.runtime.storage as S
    sig = inspect.signature(S.s3_client)
    assert {"endpoint", "access_key", "secret_key", "region"} <= set(sig.parameters)


def test_client_config_tuning_is_passed_through_not_dropped():
    """⚠️ fwh_county_atlas tuned read_timeout=300 / max_attempts=5 for multi-GB
    county PBF downloads. Consolidating without carrying those would turn a slow
    object into a timeout rather than a slow success — the kind of regression a
    refactor hides because nothing fails until the object is big enough."""
    from facetwork.runtime.storage import s3_client
    c = s3_client("http://x:9000", access_key="k", secret_key="s",
                  read_timeout=300, max_attempts=5)
    assert c.meta.config.read_timeout == 300
    # botocore normalises max_attempts=5 to total_max_attempts=6 (5 retries + 1)
    assert c.meta.config.retries["total_max_attempts"] == 6


def test_config_tuning_is_optional():
    from facetwork.runtime.storage import s3_client
    c = s3_client("http://x:9000", access_key="k", secret_key="s")
    assert c.meta.config.signature_version == "s3v4"
    assert c.meta.config.s3["addressing_style"] == "path"

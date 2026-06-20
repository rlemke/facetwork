"""Tests for the domain/example catalog loader + layered override resolution."""

from __future__ import annotations

import json

import pytest

from facetwork.domains import catalog


def _write(p, obj):
    p.write_text(json.dumps(obj), encoding="utf-8")


@pytest.fixture
def base(tmp_path, monkeypatch):
    """Point the loader at a tmp DEFAULT_CATALOG/LOCAL_OVERRIDE, no env file."""
    default = tmp_path / "domains.json"
    local = tmp_path / "domains.local.json"
    _write(default, {
        "version": 1,
        "domains": {"osm-geocoder": {"repo": "fwh_osm", "service": "runner-osm-geocoder"},
                    "jenkins": {"repo": "fwh_jenkins"}},
        "examples": {"hello-agent": {"dir": "examples/hello-agent"}},
    })
    monkeypatch.setattr(catalog, "DEFAULT_CATALOG", default)
    monkeypatch.setattr(catalog, "LOCAL_OVERRIDE", local)
    monkeypatch.delenv("AFL_DOMAINS_FILE", raising=False)
    return tmp_path, default, local


def test_defaults_when_no_override(base):
    _tmp, default, _local = base
    assert catalog.catalog_source() == default
    assert catalog.domain_names() == ["jenkins", "osm-geocoder"]
    assert set(catalog.compose_domains()) == {"osm-geocoder"}  # only the one with a service


def test_local_override_merges_add_and_remove(base):
    _tmp, _default, local = base
    _write(local, {"domains": {"acme": {"repo": "fwh_acme"}}, "_remove": ["jenkins"]})
    assert catalog.catalog_source() == local
    assert catalog.domain_names() == ["acme", "osm-geocoder"]  # jenkins removed, acme added
    assert catalog.get_domain("acme")["repo"] == "fwh_acme"
    assert catalog.get_domain("osm-geocoder")["repo"] == "fwh_osm"  # base entry survives


def test_local_override_replaces_entry(base):
    _tmp, _default, local = base
    _write(local, {"domains": {"osm-geocoder": {"repo": "fwh_osm_fork"}}})
    assert catalog.get_domain("osm-geocoder")["repo"] == "fwh_osm_fork"


def test_scaled_split_and_compose_services(base):
    _tmp, default, _local = base
    _write(default, {"domains": {
        "osm": {"repo": "fwh_osm", "service": "runner-osm", "scaled": True},
        "anthropic": {"repo": "fwh_anthropic", "service": "runner-anthropic"},
        "save": {"repo": "fwh_save"},  # no service -> not a compose domain at all
    }, "examples": {}})
    assert catalog.scaled_domain_suffixes() == ["osm"]
    assert catalog.unscaled_domain_suffixes() == ["anthropic"]  # 'save' excluded (no service)
    assert catalog.compose_services() == ["runner-anthropic", "runner-osm"]


def test_replica_defaults(base):
    _tmp, default, _local = base
    _write(default, {"defaults": {"replicas": 2, "scaled_replicas": 5}, "domains": {}, "examples": {}})
    assert catalog.default_replicas() == 2
    assert catalog.scaled_replicas() == 5


def test_replica_defaults_fallbacks(base):
    _tmp, default, _local = base
    _write(default, {"domains": {}, "examples": {}})  # no "defaults" block
    assert catalog.default_replicas() == 1            # fallback
    assert catalog.scaled_replicas() == 1             # falls back to default_replicas


def test_replica_defaults_overridable(base):
    _tmp, _default, local = base
    _write(local, {"defaults": {"scaled_replicas": 8}})
    assert catalog.scaled_replicas() == 8  # deployment bumps the scaled default


def test_env_file_is_full_replace(base, tmp_path, monkeypatch):
    _tmp, _default, local = base
    _write(local, {"domains": {"acme": {"repo": "fwh_acme"}}})  # should be IGNORED
    envf = tmp_path / "custom.json"
    _write(envf, {"domains": {"only": {"repo": "fwh_only"}}, "examples": {}})
    monkeypatch.setenv("AFL_DOMAINS_FILE", str(envf))
    assert catalog.catalog_source() == envf
    assert catalog.domain_names() == ["only"]  # neither base nor local merged in

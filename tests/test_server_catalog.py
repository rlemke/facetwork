"""Tests for facetwork.servers.catalog (no network — resolution is mocked/pinned)."""

from __future__ import annotations

import json

import pytest

from facetwork.servers import catalog


@pytest.fixture()
def cat(tmp_path, monkeypatch):
    """Point the catalog at a temp servers.json; return its path for overrides."""
    base = {
        "version": 1,
        "servers": [
            {
                "name": "infra.local",
                "aliases": ["afl-mongodb", "afl-minio"],
                "purpose": "infra",
                "group": "heavy",
                "infra": True,
                "ip_pin": None,
            },
            {
                "name": "worker.local",
                "aliases": [],
                "purpose": "runner",
                "group": "runner",
                "infra": False,
                "ip_pin": "10.9.8.7",
            },
        ],
    }
    p = tmp_path / "servers.json"
    p.write_text(json.dumps(base))
    monkeypatch.setenv("FW_SERVERS_FILE", str(p))
    return tmp_path


def test_servers_and_find(cat):
    names = [s["name"] for s in catalog.servers()]
    assert names == ["infra.local", "worker.local"]
    assert catalog.find("afl-mongodb")["name"] == "infra.local"
    assert catalog.find("worker.local")["purpose"] == "runner"
    assert catalog.find("nope") is None


def test_infra_and_alias_map(cat):
    assert catalog.infra()["name"] == "infra.local"
    assert catalog.alias_map() == {"afl-mongodb": "infra.local", "afl-minio": "infra.local"}


def test_resolve_ip_pin_wins_and_unknown_none(cat):
    assert catalog.resolve_ip("worker.local") == "10.9.8.7"
    assert catalog.resolve_ip("unknown-host") is None


def test_resolve_ip_live_resolution(cat, monkeypatch):
    monkeypatch.setattr(catalog.socket, "gethostbyname", lambda n: "192.0.2.5")
    assert catalog.resolve_ip("afl-minio") == "192.0.2.5"


def test_resolve_ip_failure_returns_none(cat, monkeypatch):
    def boom(_):
        raise OSError("no dns")

    monkeypatch.setattr(catalog.socket, "gethostbyname", boom)
    assert catalog.resolve_ip("infra.local") is None


def test_local_override_merge(cat, monkeypatch):
    monkeypatch.delenv("FW_SERVERS_FILE")
    base = cat / "servers.json"
    monkeypatch.setattr(catalog, "DEFAULT_CATALOG", base)
    override = cat / "servers.local.json"
    override.write_text(
        json.dumps(
            {
                "servers": [
                    {
                        "name": "worker.local",
                        "aliases": ["w2"],
                        "purpose": "renamed",
                        "group": "runner",
                        "infra": False,
                        "ip_pin": None,
                    },
                    {
                        "name": "extra.local",
                        "aliases": [],
                        "purpose": "added",
                        "group": "runner",
                        "infra": False,
                        "ip_pin": None,
                    },
                ],
                "_remove": ["infra.local"],
            }
        )
    )
    monkeypatch.setattr(catalog, "LOCAL_OVERRIDE", override)
    names = {s["name"] for s in catalog.servers()}
    assert names == {"worker.local", "extra.local"}
    assert catalog.find("worker.local")["purpose"] == "renamed"
    assert catalog.infra() is None


# ---------------------------------------------------------------------------
# container_ip — the address that lands in compose extra_hosts
# ---------------------------------------------------------------------------


def test_container_ip_remote_infra_is_the_resolved_address(cat, monkeypatch):
    """Infra on another machine: containers still get its live address."""
    monkeypatch.setattr(catalog.socket, "gethostbyname", lambda n: "192.0.2.5")
    monkeypatch.setattr(catalog, "_local_addresses", lambda: {"127.0.0.1", "10.0.0.9"})
    monkeypatch.setattr(catalog.socket, "gethostname", lambda: "someone-else")
    assert catalog.container_ip() == "192.0.2.5"


def test_container_ip_self_infra_is_the_gateway_alias(cat, monkeypatch):
    """Infra on THIS machine: never an address — a reboot onto a new DHCP lease
    must not strand the containers on an IP that no longer exists."""
    monkeypatch.setattr(catalog.socket, "gethostbyname", lambda n: "10.0.0.9")
    monkeypatch.setattr(catalog, "_local_addresses", lambda: {"127.0.0.1", "10.0.0.9"})
    monkeypatch.setattr(catalog.socket, "gethostname", lambda: "someone-else")
    assert catalog.container_ip() == catalog.HOST_GATEWAY


def test_container_ip_self_by_hostname_without_dns(cat, monkeypatch):
    """Hostname match alone is enough — the laptop with no network at all still
    gets a usable mapping, where resolution would give nothing."""
    def boom(_):
        raise OSError("no dns")

    monkeypatch.setattr(catalog.socket, "gethostbyname", boom)
    monkeypatch.setattr(catalog.socket, "gethostname", lambda: "infra")
    assert catalog.container_ip() == catalog.HOST_GATEWAY


def test_container_ip_pin_wins_over_gateway(cat, monkeypatch):
    """An explicit ip_pin is a deliberate choice — it outranks the alias."""
    monkeypatch.setattr(catalog.socket, "gethostname", lambda: "worker")
    assert catalog.container_ip("worker.local") == "10.9.8.7"


def test_container_ip_uncatalogued_name_still_resolves(cat, monkeypatch):
    """FW_INFRA_HOST may name a host nobody catalogued; that must not become
    'unresolved' (it was plain gethostbyname before this call replaced it)."""
    monkeypatch.setattr(catalog.socket, "gethostbyname", lambda n: "192.0.2.9")
    monkeypatch.setattr(catalog, "_local_addresses", lambda: {"127.0.0.1"})
    monkeypatch.setattr(catalog.socket, "gethostname", lambda: "someone-else")
    assert catalog.container_ip("not-in-catalog.local") == "192.0.2.9"

"""Tests for facetwork.servers.catalog (no network — resolution is mocked/pinned)."""

from __future__ import annotations

import json

import pytest

from facetwork.servers import catalog
from tests import _site

# No host name or address is spelled here -- each is a ROLE from tests/_site.py.
INFRA = _site.host("infra")
WORKER = _site.host("worker")
EXTRA = _site.host("extra")
UNCATALOGUED = _site.host("not-in-catalog")
PINNED_IP = _site.ip("pinned")
RESOLVED_IP = _site.ip("resolved")
SELF_IP = _site.ip("this-host")
OTHER_IP = _site.ip("other-host")
LOOPBACK = _site.loopback()


@pytest.fixture()
def cat(tmp_path, monkeypatch):
    """Point the catalog at a temp servers.json; return its path for overrides."""
    base = {
        "version": 1,
        "servers": [
            {
                "name": INFRA,
                "aliases": ["afl-mongodb", "afl-minio"],
                "purpose": "infra",
                "group": "heavy",
                "infra": True,
                "ip_pin": None,
            },
            {
                "name": WORKER,
                "aliases": [],
                "purpose": "runner",
                "group": "runner",
                "infra": False,
                "ip_pin": PINNED_IP,
            },
        ],
    }
    p = tmp_path / "servers.json"
    p.write_text(json.dumps(base))
    monkeypatch.setenv("FW_SERVERS_FILE", str(p))
    return tmp_path


def test_servers_and_find(cat):
    names = [s["name"] for s in catalog.servers()]
    assert names == [INFRA, WORKER]
    assert catalog.find("afl-mongodb")["name"] == INFRA
    assert catalog.find(WORKER)["purpose"] == "runner"
    assert catalog.find("nope") is None


def test_infra_and_alias_map(cat):
    assert catalog.infra()["name"] == INFRA
    assert catalog.alias_map() == {"afl-mongodb": INFRA, "afl-minio": INFRA}


def test_resolve_ip_pin_wins_and_unknown_none(cat):
    assert catalog.resolve_ip(WORKER) == PINNED_IP
    assert catalog.resolve_ip("unknown-host") is None


def test_resolve_ip_live_resolution(cat, monkeypatch):
    monkeypatch.setattr(catalog.socket, "gethostbyname", lambda n: RESOLVED_IP)
    assert catalog.resolve_ip("afl-minio") == RESOLVED_IP


def test_resolve_ip_failure_returns_none(cat, monkeypatch):
    def boom(_):
        raise OSError("no dns")

    monkeypatch.setattr(catalog.socket, "gethostbyname", boom)
    assert catalog.resolve_ip(INFRA) is None


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
                        "name": WORKER,
                        "aliases": ["w2"],
                        "purpose": "renamed",
                        "group": "runner",
                        "infra": False,
                        "ip_pin": None,
                    },
                    {
                        "name": EXTRA,
                        "aliases": [],
                        "purpose": "added",
                        "group": "runner",
                        "infra": False,
                        "ip_pin": None,
                    },
                ],
                "_remove": [INFRA],
            }
        )
    )
    monkeypatch.setattr(catalog, "LOCAL_OVERRIDE", override)
    names = {s["name"] for s in catalog.servers()}
    assert names == {WORKER, EXTRA}
    assert catalog.find(WORKER)["purpose"] == "renamed"
    assert catalog.infra() is None


# ---------------------------------------------------------------------------
# container_ip — the address that lands in compose extra_hosts
# ---------------------------------------------------------------------------


def test_container_ip_remote_infra_is_the_resolved_address(cat, monkeypatch):
    """Infra on another machine: containers still get its live address."""
    monkeypatch.setattr(catalog.socket, "gethostbyname", lambda n: RESOLVED_IP)
    monkeypatch.setattr(catalog, "_local_addresses", lambda: {LOOPBACK, SELF_IP})
    monkeypatch.setattr(catalog.socket, "gethostname", lambda: "someone-else")
    assert catalog.container_ip() == RESOLVED_IP


def test_container_ip_self_infra_is_the_gateway_alias(cat, monkeypatch):
    """Infra on THIS machine: never an address — a reboot onto a new DHCP lease
    must not strand the containers on an IP that no longer exists."""
    monkeypatch.setattr(catalog.socket, "gethostbyname", lambda n: SELF_IP)
    monkeypatch.setattr(catalog, "_local_addresses", lambda: {LOOPBACK, SELF_IP})
    monkeypatch.setattr(catalog.socket, "gethostname", lambda: "someone-else")
    assert catalog.container_ip() == catalog.HOST_GATEWAY


def test_container_ip_self_by_hostname_without_dns(cat, monkeypatch):
    """Hostname match alone is enough — the laptop with no network at all still
    gets a usable mapping, where resolution would give nothing."""

    def boom(_):
        raise OSError("no dns")

    monkeypatch.setattr(catalog.socket, "gethostbyname", boom)
    monkeypatch.setattr(catalog.socket, "gethostname", lambda: INFRA.split(".")[0])
    assert catalog.container_ip() == catalog.HOST_GATEWAY


def test_container_ip_pin_wins_over_gateway(cat, monkeypatch):
    """An explicit ip_pin is a deliberate choice — it outranks the alias."""
    monkeypatch.setattr(catalog.socket, "gethostname", lambda: WORKER.split(".")[0])
    assert catalog.container_ip(WORKER) == PINNED_IP


def test_container_ip_uncatalogued_name_still_resolves(cat, monkeypatch):
    """FW_INFRA_HOST may name a host nobody catalogued; that must not become
    'unresolved' (it was plain gethostbyname before this call replaced it)."""
    monkeypatch.setattr(catalog.socket, "gethostbyname", lambda n: OTHER_IP)
    monkeypatch.setattr(catalog, "_local_addresses", lambda: {LOOPBACK})
    monkeypatch.setattr(catalog.socket, "gethostname", lambda: "someone-else")
    assert catalog.container_ip(UNCATALOGUED) == OTHER_IP


def test_host_list_falls_back_to_the_catalog():
    """⚠️ A hand-maintained FW_RUNNER_HOSTS is what goes stale. Ours named two
    powered-off machines and omitted two live ones, so `fleet rollout --stagger`
    refused to flip — it pre-pulls to exactly those hosts — while the hosts it did
    not know about pulled ~1GB unstaggered on reconcile.

    The catalog already tracks the fleet by stable name and is updated on join.
    """
    import pathlib

    src = (
        pathlib.Path(__file__).resolve().parents[1] / "scripts/lib/_helpers/_remote.sh"
    ).read_text()
    assert "from facetwork.servers import catalog" in src
    # An explicit setting must still win: this is a fallback, not a takeover.
    i = src.index("_afl_resolve_hosts()")
    body = src[i : i + 2600]
    assert body.index("FW_RUNNER_HOSTS") < body.index("catalog")


def test_the_fallback_skips_unreachable_and_self():
    """A catalogued machine that is simply POWERED OFF is not an error — treating
    it as one turns 'one host is down' into a failed rollout. And every caller acts
    on this host locally, not over ssh."""
    import pathlib

    src = (
        pathlib.Path(__file__).resolve().parents[1] / "scripts/lib/_helpers/_remote.sh"
    ).read_text()
    assert "catalog.resolve_ip(name)" in src, "must skip machines that do not resolve"
    assert "skip machines that are simply off" in src
    assert 'split(".")[0].lower() == me' in src, "must exclude this host"


def test_this_machine_resolves_itself_by_routing_not_mdns(cat, monkeypatch):
    """avahi advertises a host's name on every interface, Docker bridges included,
    so resolving OUR OWN name can answer a container-network address. Measured
    2026-09-30: the fleet-agent then judged every correctly-pinned runner
    "drifted" and recreated all 23, killing a planet rewrite in its last 2%."""
    monkeypatch.setattr(catalog.socket, "gethostname", lambda: INFRA.split(".")[0])
    monkeypatch.setattr(catalog.socket, "gethostbyname", lambda n: _site.container_net_ip())
    monkeypatch.setattr(catalog, "_primary_lan_ip", lambda: SELF_IP)
    assert catalog.resolve_ip(INFRA) == SELF_IP


def test_other_machines_still_resolve_by_name(cat, monkeypatch):
    monkeypatch.setattr(catalog.socket, "gethostname", lambda: "somewhere-else")
    monkeypatch.setattr(catalog.socket, "gethostbyname", lambda n: RESOLVED_IP)
    monkeypatch.setattr(catalog, "_primary_lan_ip", lambda: SELF_IP)
    assert catalog.resolve_ip(INFRA) == RESOLVED_IP

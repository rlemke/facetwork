"""The fleet-agent's detection of a stale infra IP pinned in a container's ENV.

⚠️ WRITTEN AFTER A REAL OUTAGE, 2026-09-08. The infra host's DHCP lease moved it
to a new address and the old one was reassigned to a different machine. Every
runner on that host carried ``FW_MONGODB_URL=mongodb://<old address>:27017`` in
its ENVIRONMENT, baked in at container creation. 22 of 23 runners stayed "Up" for
days, logged "Heartbeat failed" every 30 seconds, and never registered.

Three separate signals said everything was fine:

  docker ps                     "Up 3 days", all 23
  the fleet agent               "up to date (v184)" — it compares the IMAGE TAG
  fleet-agent refresh-hosts     "no drift" — TRUTHFULLY, because the /etc/hosts
                                entries it owns were correct

An address can hide in two places and the self-heal covered only one. These
tests pin the second.
"""

import importlib.util

import pytest
from pathlib import Path

from tests import _site

_FLEET_LIB = (
    Path(__file__).resolve().parent.parent / "scripts" / "lib" / "_helpers" / "_fleet_lib.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("_fleet_lib_env_under_test", _FLEET_LIB)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


fl = _load()
_REAL_ANSWERS = fl._endpoint_answers


@pytest.fixture(autouse=True)
def _no_probes(monkeypatch):
    """A stale address in these tests is one that does NOT answer; no real
    connection is attempted."""
    monkeypatch.setattr(fl, "_endpoint_answers", lambda value, ip: False)


INFRA = _site.ip("infra")
STALE = _site.ip("stale-infra")
OTHER_STALE = _site.ip("another-stale-host")


def _with_env(monkeypatch, containers: dict):
    monkeypatch.setattr(fl, "_runner_containers", lambda: list(containers))
    monkeypatch.setattr(fl, "_container_env", lambda name: containers[name])


def test_the_exact_outage_value_is_detected(monkeypatch):
    """The literal string that took the fleet down."""
    _with_env(
        monkeypatch,
        {
            "runner-a": {"FW_MONGODB_URL": f"mongodb://{STALE}:27017"},
        },
    )
    got = fl.stale_env_containers(INFRA)
    assert got == [("runner-a", "FW_MONGODB_URL", STALE)]


def test_a_hostname_is_not_drift(monkeypatch):
    """⚠️ The correct configuration. Flagging it would recreate every container
    on every poll forever — the failure mode the /etc/hosts probe already had
    once, for the same reason."""
    _with_env(
        monkeypatch,
        {
            "runner-a": {
                "FW_MONGODB_URL": "mongodb://afl-mongodb:27017",
                "FW_S3_ENDPOINT": "http://afl-minio:9000",
            },
        },
    )
    assert fl.stale_env_containers(INFRA) == []


def test_loopback_and_container_network_addresses_are_deliberate(monkeypatch):
    """⚠️ On the infra host itself the endpoint is legitimately loopback, and a
    compose-network address is assigned by Docker. Neither is drift, and
    recreating on them would make the infra host unable to run runners at all."""
    _with_env(
        monkeypatch,
        {
            "runner-a": {"FW_MONGODB_URL": f"mongodb://{_site.loopback()}:27017"},
            "runner-b": {"FW_S3_ENDPOINT": f"http://{_site.container_net_ip()}:9000"},
        },
    )
    assert fl.stale_env_containers(INFRA) == []


def test_the_current_infra_ip_is_not_drift(monkeypatch):
    """A literal IP is not itself wrong — the infra host's own runners are
    pinned to one by construction. Only a STALE one is."""
    _with_env(
        monkeypatch,
        {
            "runner-a": {"FW_MONGODB_URL": f"mongodb://{INFRA}:27017"},
        },
    )
    assert fl.stale_env_containers(INFRA) == []


def test_every_endpoint_variable_is_checked(monkeypatch):
    """Mongo is the one that caused the outage, but MinIO, the dashboard, PostGIS
    and the self-hosted extracts server can each pin an address the same way."""
    stale = STALE
    _with_env(
        monkeypatch,
        {
            "runner-a": dict.fromkeys(fl._ENDPOINT_VARS, f"http://{stale}:9000"),
        },
    )
    got = fl.stale_env_containers(INFRA)
    assert {v for _c, v, _o in got} == set(fl._ENDPOINT_VARS)


def test_findings_name_the_container_variable_and_value(monkeypatch):
    """The report has to be actionable without a second investigation: which
    container, which variable, what it pins."""
    _with_env(
        monkeypatch,
        {
            "runner-a": {"FW_MONGODB_URL": f"mongodb://{OTHER_STALE}:27017"},
            "runner-b": {"FW_MONGODB_URL": "mongodb://afl-mongodb:27017"},
        },
    )
    got = fl.stale_env_containers(INFRA)
    assert len(got) == 1
    c, var, old = got[0]
    assert (c, var, old) == ("runner-a", "FW_MONGODB_URL", OTHER_STALE)


def test_a_container_that_cannot_be_inspected_is_skipped_not_fatal(monkeypatch):
    """One unreadable container must not stop the sweep — the same contract the
    /etc/hosts repair holds."""
    monkeypatch.setattr(fl, "_runner_containers", lambda: ["good", "bad"])

    def env(name):
        if name == "bad":
            raise RuntimeError("docker inspect failed")
        return {"FW_MONGODB_URL": f"mongodb://{STALE}:27017"}

    monkeypatch.setattr(fl, "_container_env", env)
    try:
        got = fl.stale_env_containers(INFRA)
    except RuntimeError as exc:
        raise AssertionError("one bad container aborted the sweep") from exc
    assert ("good", "FW_MONGODB_URL", STALE) in got


# ---------------------------------------------------------------------------
# 2026-10-14: a FAILED lookup read as drift. The Mongo host's catalog entry did
# not resolve for a moment, service_ips() filled the gap with the infra IP, and
# every correct FW_MONGODB_URL was flagged -- the agent recreated all 24 runners
# on a host 8 times and restarted a 3-hour build each time.
# ---------------------------------------------------------------------------

MONGO_HOST = _site.ip("database-host")


def test_a_service_missing_from_the_map_is_not_judged(monkeypatch):
    _with_env(monkeypatch, {"runner-a": {"FW_MONGODB_URL": f"mongodb://{MONGO_HOST}:27017",
                                         "FW_S3_ENDPOINT": f"http://{INFRA}:9000"}})
    # afl-mongodb did not resolve, so it is absent; MinIO is on the infra host.
    assert fl.stale_env_containers({"afl-minio": INFRA}) == []


def test_a_resolved_service_is_still_judged(monkeypatch):
    _with_env(monkeypatch, {"runner-a": {"FW_MONGODB_URL": f"mongodb://{STALE}:27017"}})
    got = fl.stale_env_containers({"afl-minio": INFRA, "afl-mongodb": MONGO_HOST})
    assert got == [("runner-a", "FW_MONGODB_URL", STALE)]


class _Catalog:
    """A catalog whose afl-mongodb entry exists but does not resolve."""

    def __init__(self, resolved):
        self.resolved = resolved

    def find(self, name):
        return {"name": name} if name in ("afl-mongodb", "afl-minio") else None

    def resolve_ip(self, entry):
        return self.resolved.get(entry["name"])


def _with_catalog(monkeypatch, resolved):
    import sys
    import types

    mod = types.ModuleType("facetwork.servers.catalog")
    cat = _Catalog(resolved)
    mod.find, mod.resolve_ip = cat.find, cat.resolve_ip
    pkg = types.ModuleType("facetwork.servers")
    pkg.catalog = mod
    monkeypatch.setitem(sys.modules, "facetwork.servers", pkg)
    monkeypatch.setitem(sys.modules, "facetwork.servers.catalog", mod)
    monkeypatch.setattr(fl, "resolve_infra_ip", lambda log=None: INFRA)


def test_strict_leaves_out_an_entry_that_did_not_resolve(monkeypatch):
    _with_catalog(monkeypatch, {"afl-minio": INFRA})
    svc = fl.service_ips(strict=True)
    assert "afl-mongodb" not in svc and svc["afl-minio"] == INFRA


def test_non_strict_keeps_the_infra_fallback(monkeypatch):
    _with_catalog(monkeypatch, {"afl-minio": INFRA})
    assert fl.service_ips()["afl-mongodb"] == INFRA


def test_strict_and_a_resolved_entry_agree(monkeypatch):
    _with_catalog(monkeypatch, {"afl-minio": INFRA, "afl-mongodb": MONGO_HOST})
    assert fl.service_ips(strict=True)["afl-mongodb"] == MONGO_HOST


def test_the_agent_recreates_only_on_two_agreeing_polls():
    src = (Path(__file__).resolve().parent.parent / "scripts/lib/fleet/agent").read_text()
    assert "service_ips(strict=True)" in src
    assert "confirmed = set(stale) & prev_stale" in src



# 2026-10-15: the Mongo host gained a second address and its name resolved to
# either; every flip recreated all 24 runners on a host, killing a build.

def test_a_pinned_address_that_still_answers_is_not_drift(monkeypatch):
    other = _site.ip("database-host-second-address")
    _with_env(monkeypatch, {"runner-a": {"FW_MONGODB_URL": f"mongodb://{other}:27017"}})
    monkeypatch.setattr(fl, "_endpoint_answers", lambda value, ip: ip == other)
    assert fl.stale_env_containers({"afl-minio": INFRA, "afl-mongodb": MONGO_HOST}) == []


def test_the_probe_uses_the_port_the_url_names(monkeypatch):
    import socket

    seen = []

    class Conn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def connect(addr, timeout):
        seen.append(addr)
        return Conn()

    monkeypatch.setattr(socket, "create_connection", connect)
    fl._answer_cache.clear()
    ip = _site.ip("probe-target")
    assert _REAL_ANSWERS(f"mongodb://{ip}:27017", ip) is True
    assert _REAL_ANSWERS(f"http://{ip}:9000", ip) is True
    assert _REAL_ANSWERS(f"postgresql://u:p@{ip}/db", ip) is True
    assert seen == [(ip, 27017), (ip, 9000), (ip, 5432)]

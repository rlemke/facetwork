"""The fleet-agent's detection of a stale infra IP pinned in a container's ENV.

⚠️ WRITTEN AFTER A REAL OUTAGE, 2026-09-08. The infra host's DHCP lease moved it
from .114 to .67 and .114 was reassigned to a different machine. Every runner on
that host carried ``FW_MONGODB_URL=mongodb://192.168.68.114:27017`` in its
ENVIRONMENT, baked in at container creation. 22 of 23 runners stayed "Up" for
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
from pathlib import Path

_FLEET_LIB = (
    Path(__file__).resolve().parent.parent / "scripts" / "lib" / "_helpers" / "_fleet_lib.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("_fleet_lib_env_under_test", _FLEET_LIB)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


fl = _load()
INFRA = "192.168.68.67"


def _with_env(monkeypatch, containers: dict):
    monkeypatch.setattr(fl, "_runner_containers", lambda: list(containers))
    monkeypatch.setattr(fl, "_container_env", lambda name: containers[name])


def test_the_exact_outage_value_is_detected(monkeypatch):
    """The literal string that took the fleet down."""
    _with_env(monkeypatch, {
        "runner-a": {"FW_MONGODB_URL": "mongodb://192.168.68.114:27017"},
    })
    got = fl.stale_env_containers(INFRA)
    assert got == [("runner-a", "FW_MONGODB_URL", "192.168.68.114")]


def test_a_hostname_is_not_drift(monkeypatch):
    """⚠️ The correct configuration. Flagging it would recreate every container
    on every poll forever — the failure mode the /etc/hosts probe already had
    once, for the same reason."""
    _with_env(monkeypatch, {
        "runner-a": {"FW_MONGODB_URL": "mongodb://afl-mongodb:27017",
                     "FW_S3_ENDPOINT": "http://afl-minio:9000"},
    })
    assert fl.stale_env_containers(INFRA) == []


def test_loopback_and_container_network_addresses_are_deliberate(monkeypatch):
    """⚠️ On the infra host itself the endpoint is legitimately 127.0.0.1, and a
    compose-network address is assigned by Docker. Neither is drift, and
    recreating on them would make the infra host unable to run runners at all."""
    _with_env(monkeypatch, {
        "runner-a": {"FW_MONGODB_URL": "mongodb://127.0.0.1:27017"},
        "runner-b": {"FW_S3_ENDPOINT": "http://172.19.0.2:9000"},
    })
    assert fl.stale_env_containers(INFRA) == []


def test_the_current_infra_ip_is_not_drift(monkeypatch):
    """A literal IP is not itself wrong — server3's own runners are pinned to
    one by construction. Only a STALE one is."""
    _with_env(monkeypatch, {
        "runner-a": {"FW_MONGODB_URL": f"mongodb://{INFRA}:27017"},
    })
    assert fl.stale_env_containers(INFRA) == []


def test_every_endpoint_variable_is_checked(monkeypatch):
    """Mongo is the one that caused the outage, but MinIO, the dashboard, PostGIS
    and the self-hosted extracts server can each pin an address the same way."""
    stale = "192.168.68.114"
    _with_env(monkeypatch, {
        "runner-a": {v: f"http://{stale}:9000" for v in fl._ENDPOINT_VARS},
    })
    got = fl.stale_env_containers(INFRA)
    assert {v for _c, v, _o in got} == set(fl._ENDPOINT_VARS)


def test_findings_name_the_container_variable_and_value(monkeypatch):
    """The report has to be actionable without a second investigation: which
    container, which variable, what it pins."""
    _with_env(monkeypatch, {
        "runner-a": {"FW_MONGODB_URL": "mongodb://10.1.2.3:27017"},
        "runner-b": {"FW_MONGODB_URL": "mongodb://afl-mongodb:27017"},
    })
    got = fl.stale_env_containers(INFRA)
    assert len(got) == 1
    c, var, old = got[0]
    assert (c, var, old) == ("runner-a", "FW_MONGODB_URL", "10.1.2.3")


def test_a_container_that_cannot_be_inspected_is_skipped_not_fatal(monkeypatch):
    """One unreadable container must not stop the sweep — the same contract the
    /etc/hosts repair holds."""
    monkeypatch.setattr(fl, "_runner_containers", lambda: ["good", "bad"])

    def env(name):
        if name == "bad":
            raise RuntimeError("docker inspect failed")
        return {"FW_MONGODB_URL": "mongodb://192.168.68.114:27017"}

    monkeypatch.setattr(fl, "_container_env", env)
    try:
        got = fl.stale_env_containers(INFRA)
    except RuntimeError:
        raise AssertionError("one bad container aborted the sweep")
    assert ("good", "FW_MONGODB_URL", "192.168.68.114") in got

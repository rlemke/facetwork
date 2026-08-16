"""Unit tests for the fleet-agent's container /etc/hosts drift repair.

Two behaviours are pinned here, both regressions this file was written for:

- the probe must ask for the **IPv4** answer. Plain ``getent hosts`` is
  AF_UNSPEC and prefers AAAA, so on an IPv6-enabled compose network it returns
  the infra *container's* address from Docker's embedded DNS and never sees the
  IPv4 ``/etc/hosts`` entry the agent writes. Comparing against that made every
  poll report drift and rewrite an already-correct file — 16 containers, every
  30s, each with a misleading ``drift:`` log line.
- a container mapped to Docker's ``host-gateway`` (infra is this machine) must
  be left alone. That mapping cannot go stale, so "differs from the LAN IP" is
  not drift; rewriting it would downgrade it to an address that dies on this
  machine's next DHCP lease.
"""

import importlib.util
from pathlib import Path

_FLEET_LIB = (
    Path(__file__).resolve().parent.parent / "scripts" / "lib" / "_helpers" / "_fleet_lib.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("_fleet_lib_hosts_under_test", _FLEET_LIB)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


fl = _load()

GATEWAY = "192.168.65.254"


class _Recorder:
    """Stands in for subprocess.run over `docker exec`, answering by argv."""

    def __init__(self, answers, hosts_file="127.0.0.1\tlocalhost\n10.0.0.1\tafl-mongodb\n"):
        self.answers = answers  # {name: ipv4 the container resolves it to}
        self.hosts_file = hosts_file
        self.calls = []
        self.written = []

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        out = ""
        if "getent" in cmd:
            name = cmd[-1]
            ip = self.answers.get(name)
            out = f"{ip}    STREAM {name}\n" if ip else ""
        elif "cat" in cmd and cmd[-1] == "/etc/hosts":
            out = self.hosts_file
        elif kw.get("input") is not None:
            self.written.append(kw["input"])

        class R:
            returncode = 0
            stdout = out
            stderr = ""

        return R()


def _patch(monkeypatch, rec, containers=("runner-a",)):
    monkeypatch.setattr(fl.subprocess, "run", rec)
    monkeypatch.setattr(fl, "_runner_containers", lambda: list(containers))


def test_probe_asks_for_the_ipv4_answer(monkeypatch):
    rec = _Recorder({"afl-mongodb": "10.0.0.1"})
    _patch(monkeypatch, rec)
    assert fl._container_resolves("runner-a", "afl-mongodb") == "10.0.0.1"
    assert rec.calls[0][3:] == ["getent", "ahostsv4", "afl-mongodb"], (
        "must not use plain `getent hosts` — it returns the AAAA answer"
    )


def test_no_rewrite_when_the_container_already_has_the_current_ip(monkeypatch):
    rec = _Recorder({"afl-mongodb": "10.0.0.1"})
    _patch(monkeypatch, rec)
    assert fl.refresh_container_hosts("10.0.0.1") == []
    assert rec.written == []


def test_rewrite_on_real_drift(monkeypatch):
    rec = _Recorder({"afl-mongodb": "10.0.0.1", "host.docker.internal": GATEWAY})
    _patch(monkeypatch, rec)
    assert fl.refresh_container_hosts("10.0.0.99") == ["runner-a"]
    assert "10.0.0.99\tafl-mongodb" in rec.written[0]


def test_host_gateway_mapping_is_left_alone(monkeypatch):
    """Infra is this machine: the container resolves afl-* through Docker's
    gateway, which never goes stale — patching it to the LAN IP would be a
    downgrade, and it would happen again on every single poll."""
    rec = _Recorder({"afl-mongodb": GATEWAY, "host.docker.internal": GATEWAY})
    _patch(monkeypatch, rec)
    assert fl.refresh_container_hosts("10.0.0.99") == []
    assert rec.written == []

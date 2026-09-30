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

Since 2026-09-13 the repair is also **per name**. MongoDB moved off the infra
host while MinIO stayed behind, so the afl-*
names no longer share an address. The old code checked drift on ``afl-mongodb``
alone and returned early if it matched — under which a MinIO-only move would
have been invisible. These tests therefore answer for EVERY name, and pin that
a split mapping is honoured.
"""

import importlib.util
from pathlib import Path

from tests import _site

_FLEET_LIB = (
    Path(__file__).resolve().parent.parent / "scripts" / "lib" / "_helpers" / "_fleet_lib.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("_fleet_lib_hosts_under_test", _FLEET_LIB)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


fl = _load()

# Every address is a ROLE from tests/_site.py -- never a literal (see that module).
GATEWAY = _site.ip("docker-gateway")  # what Docker's host-gateway resolves to in a container
INFRA = _site.ip("infra")
INFRA_NEW = _site.ip("infra-after-move")
MONGO_HOST = _site.ip("mongo-host")
HERE = _site.ip("this-host")
EXTRACTS_HOST = _site.ip("extracts-host")


class _Recorder:
    """Stands in for subprocess.run over `docker exec`, answering by argv."""

    def __init__(self, answers, hosts_file=None):
        self.answers = answers  # {name: ipv4 the container resolves it to}
        self.hosts_file = hosts_file or f"{_site.loopback()}\tlocalhost\n{INFRA}\tafl-mongodb\n"
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
    rec = _Recorder({"afl-mongodb": INFRA})
    _patch(monkeypatch, rec)
    assert fl._container_resolves("runner-a", "afl-mongodb") == INFRA
    assert rec.calls[0][3:] == ["getent", "ahostsv4", "afl-mongodb"], (
        "must not use plain `getent hosts` — it returns the AAAA answer"
    )


def _all_names_at(ip, **extra):
    """Every afl-* name already resolving to ``ip`` — i.e. nothing drifted."""
    answers = dict.fromkeys(fl.INFRA_HOST_NAMES, ip)
    answers.update(extra)
    return answers


def test_no_rewrite_when_the_container_already_has_the_current_ip(monkeypatch):
    rec = _Recorder(_all_names_at(INFRA))
    _patch(monkeypatch, rec)
    assert fl.refresh_container_hosts(INFRA) == []
    assert rec.written == []


def test_a_name_the_container_is_missing_entirely_is_healed(monkeypatch):
    """Only afl-mongodb is present. The others resolve to nothing, which is
    drift — the old single-name check returned early here and left a container
    that could never reach MinIO."""
    rec = _Recorder({"afl-mongodb": INFRA, "host.docker.internal": GATEWAY})
    _patch(monkeypatch, rec)
    assert fl.refresh_container_hosts(INFRA) == ["runner-a"]
    assert f"{INFRA}\tafl-minio" in rec.written[0]


def test_split_mapping_sends_each_name_to_its_own_host(monkeypatch):
    """The reason this function takes a mapping: Mongo on one box, MinIO on
    another. Pointing them at one address would take the object store out."""
    rec = _Recorder(_all_names_at(INFRA, **{"host.docker.internal": GATEWAY}))
    _patch(monkeypatch, rec)
    split = dict.fromkeys(fl.INFRA_HOST_NAMES, INFRA)
    split["afl-mongodb"] = MONGO_HOST
    assert fl.refresh_container_hosts(split) == ["runner-a"]
    written = rec.written[0]
    assert f"{MONGO_HOST}\tafl-mongodb" in written, "Mongo must move"
    assert f"{INFRA}\tafl-mongodb" not in written
    # ...and MinIO must NOT have followed it.
    assert f"{MONGO_HOST}\tafl-minio" not in written


def test_rewrite_on_real_drift(monkeypatch):
    rec = _Recorder({"afl-mongodb": INFRA, "host.docker.internal": GATEWAY})
    _patch(monkeypatch, rec)
    assert fl.refresh_container_hosts(INFRA_NEW) == ["runner-a"]
    assert f"{INFRA_NEW}\tafl-mongodb" in rec.written[0]


def test_host_gateway_mapping_is_left_alone(monkeypatch):
    """Infra is this machine: the container resolves afl-* through Docker's
    gateway, which never goes stale — patching it to the LAN IP would be a
    downgrade, and it would happen again on every single poll."""
    rec = _Recorder(_all_names_at(GATEWAY, **{"host.docker.internal": GATEWAY}))
    _patch(monkeypatch, rec)
    monkeypatch.setattr(fl, "_this_host_addresses", lambda: {INFRA_NEW})
    assert fl.refresh_container_hosts(INFRA_NEW) == []
    assert rec.written == []


def test_gateway_entry_for_a_service_that_moved_away_is_healed(monkeypatch):
    """The gateway skip holds only while the service still runs HERE.

    2026-09-29: the OSM extract server moved from the infra host to another
    machine. The infra host's runners were created with
    `afl-extracts:host-gateway`, and the skip left them resolving to the infra
    host's own, now-empty extract server for two weeks, while every other host
    was healed to the new one."""
    rec = _Recorder(_all_names_at(GATEWAY, **{"host.docker.internal": GATEWAY}))
    _patch(monkeypatch, rec)
    monkeypatch.setattr(fl, "_this_host_addresses", lambda: {HERE})
    mapping = dict.fromkeys(fl.INFRA_HOST_NAMES, HERE)  # MinIO etc. still here
    mapping["afl-extracts"] = EXTRACTS_HOST  # ...the extracts moved
    assert fl.refresh_container_hosts(mapping) == ["runner-a"]
    written = rec.written[0]
    assert f"{EXTRACTS_HOST}\tafl-extracts" in written
    # The names whose service is still local keep Docker's gateway mapping.
    assert f"{HERE}\tafl-minio" not in written

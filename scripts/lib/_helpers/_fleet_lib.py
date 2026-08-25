"""Shared helpers for the fleet controller (Phase 3): MongoDB discovery and an
encrypted secret store. Imported by `fleet`, `fleet-agent`, `fleet-advertise`.

Discovery: a server should find MongoDB with no explicit endpoint. resolve_mongo
tries, in order, explicit → FW_MONGODB_URL → mDNS (if `zeroconf` is installed) →
the conventional `afl-mongodb` hostname (DNS/`/etc/hosts`), returning the first
that actually answers a ping. MinIO is then learned from `fleet_config`, with a
conventional `afl-minio` fallback.

Secret store: MinIO credentials live encrypted in the `fleet_secrets` collection
(Fernet symmetric encryption). Each host needs only the fleet key (FW_FLEET_KEY)
— one bootstrap secret, rotatable — not the actual creds. Set once centrally.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
from urllib.parse import urlparse

CONVENTIONAL_MONGO = "mongodb://afl-mongodb:27017"
CONVENTIONAL_MINIO = "http://afl-minio:9000"


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def _mongo_ok(url: str, timeout_ms: int = 5000) -> bool:
    try:
        from pymongo import MongoClient

        MongoClient(url, serverSelectionTimeoutMS=timeout_ms).admin.command("ping")
        return True
    except Exception:
        return False


def lan_ip() -> str | None:
    """This host's primary LAN IPv4 (UDP-connect trick — no packet is sent).
    Used to replace a loopback self-resolution with an address that is valid
    fleet-wide and reachable from containers."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("192.0.2.1", 9))  # TEST-NET; routing lookup only
            return s.getsockname()[0]
        finally:
            s.close()
    except OSError:
        return None


def _tcp_ok(endpoint: str, timeout: float = 2.0) -> bool:
    try:
        u = urlparse(endpoint)
        host = u.hostname or endpoint
        port = u.port or (443 if u.scheme == "https" else 80)
        socket.create_connection((host, port), timeout=timeout).close()
        return True
    except Exception:
        return False


def mdns_lookup(service_type: str, timeout: float = 2.0) -> str | None:
    """Return host:port for the first instance of an mDNS service, or None if
    zeroconf isn't installed or nothing answers."""
    try:
        from zeroconf import ServiceBrowser, Zeroconf
    except Exception:
        return None
    found: list[str] = []

    class _L:
        def add_service(self, zc, type_, name):
            info = zc.get_service_info(type_, name, timeout=int(timeout * 1000))
            if info and info.addresses:
                ip = socket.inet_ntoa(info.addresses[0])
                found.append(f"{ip}:{info.port}")

        def update_service(self, *a):
            pass

        def remove_service(self, *a):
            pass

    zc = Zeroconf()
    try:
        ServiceBrowser(zc, service_type, _L())
        import time

        time.sleep(timeout)
    finally:
        zc.close()
    return found[0] if found else None


#: Hosts that mean "this machine" — correct for the process doing the lookup,
#: and WRONG for every container it launches (loopback inside a container is the
#: container). A literal one of these must never reach a runner's environment.
_LOOPBACK_HOSTS = ("127.", "localhost", "::1", "[::1]")


def _is_loopback_host(host: str) -> bool:
    h = host.strip().lower()
    return h == "localhost" or h == "::1" or h == "[::1]" or h.startswith("127.")


def _deloop(url: str, *, say=None) -> str:
    """Rewrite a LITERAL loopback host in *url* to this host's LAN IP.

    Why this is unconditional rather than a caller's job: the discovered URL is
    handed to runner containers, where loopback resolves to the container
    itself. A runner then starts, fails to reach Mongo, exits 0, and restarts —
    forever, while the host's own checks all pass because loopback IS correct
    for the host. That failure took 21 of 22 runners off an infra host for 17
    hours, invisible to `fleet status`, because the agent resolved once at
    startup and cached the poisoned value for its lifetime.

    Only *literal* loopback is rewritten. A stable NAME (``afl-mongodb``) is
    left alone on purpose: containers map those via ``extra_hosts`` to the infra
    IP, so the name is the more drift-proof form.

    If the LAN address can't be determined or isn't reachable, the original URL
    is returned — a working host-local URL beats a broken guess, and the caller
    still logs what it chose.
    """
    try:
        from urllib.parse import urlsplit, urlunsplit

        parts = urlsplit(url)
        host = parts.hostname or ""
        if not _is_loopback_host(host):
            return url
        lan = lan_ip()
        if not lan or _is_loopback_host(lan):
            if say:
                say(f"  (loopback {host} in {url} but no LAN IP to substitute — leaving as-is)")
            return url
        netloc = f"{lan}:{parts.port}" if parts.port else lan
        if parts.username:
            cred = parts.username + (f":{parts.password}" if parts.password else "")
            netloc = f"{cred}@{netloc}"
        rewritten = urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
        if not _mongo_ok(rewritten):
            if say:
                say(f"  (loopback→{lan} rewrite of {url} not reachable — leaving as-is)")
            return url
        if say:
            say(f"  loopback {host} → {lan} (a container cannot reach loopback)")
        return rewritten
    except Exception:  # never let the guard break discovery
        return url


def resolve_mongo(explicit: str | None = None, *, log=None) -> str:
    """Discover a reachable MongoDB URL. Order: explicit → FW_MONGODB_URL →
    mDNS (_afl-mongo._tcp) → conventional afl-mongodb. Raises if none answer."""

    def _say(m):
        if log:
            log(m)

    candidates: list[tuple[str, str]] = []
    if explicit:
        candidates.append(("explicit", explicit))
    env = os.environ.get("FW_MONGODB_URL")
    if env and env != explicit:
        candidates.append(("FW_MONGODB_URL", env))
    # Server catalog (servers.json): resolve the infra entry's stable NAME to
    # its current IP — survives DHCP drift without /etc/hosts edits. Sits
    # before the conventional alias, which depends on a hand-maintained (and
    # historically stale) /etc/hosts mapping.
    # LOOPBACK GUARD: on the infra host itself the name resolves to 127.0.0.1,
    # which is host-reachable but POISON for anything that hands the URL to a
    # container (loopback inside a container is the container). A loopback
    # resolution means WE ARE the infra host, so substitute this host's LAN IP
    # — valid fleet-wide and reachable from containers.
    try:
        from facetwork.servers import catalog as _srv_catalog

        _infra = _srv_catalog.infra()
        _ip = _srv_catalog.resolve_ip(_infra) if _infra else None
        if _ip and (_ip.startswith("127.") or _ip == "::1"):
            _ip = lan_ip() or _ip
        if _ip:
            candidates.append(("server catalog (infra)", f"mongodb://{_ip}:27017"))
    except Exception:
        pass
    hp = mdns_lookup("_afl-mongo._tcp.local.")
    if hp:
        candidates.append(("mDNS", f"mongodb://{hp}"))
    candidates.append(("afl-mongodb hostname", CONVENTIONAL_MONGO))

    for how, url in candidates:
        if _mongo_ok(url):
            url = _deloop(url, say=_say)
            _say(f"discovered MongoDB via {how}: {url}")
            return url
        _say(f"  (no MongoDB via {how}: {url})")
    raise RuntimeError(
        "could not discover MongoDB — tried explicit/--mongo, FW_MONGODB_URL, "
        "mDNS, and the afl-mongodb hostname. Pass --mongo or add an /etc/hosts entry."
    )


def resolve_minio(from_config: str | None) -> str | None:
    """MinIO endpoint: the fleet_config value if reachable, else the conventional
    afl-minio hostname if reachable, else the config value (let preflight report)."""
    if from_config and _tcp_ok(from_config):
        return from_config
    if _tcp_ok(CONVENTIONAL_MINIO):
        return CONVENTIONAL_MINIO
    return from_config


# ---------------------------------------------------------------------------
# Infra-IP drift self-heal — keep runner containers' afl-* /etc/hosts current
# ---------------------------------------------------------------------------
#
# Runner containers reach Mongo/MinIO by the stable names afl-mongodb / afl-minio,
# mapped to the infra host's IP by docker `extra_hosts` — baked into /etc/hosts at
# container creation and NEVER refreshed. When the infra host's DHCP lease drifts,
# that entry goes stale and every runner loses Mongo (can't claim tasks) and MinIO.
# pymongo/boto3 already re-resolve the NAME on each reconnect, so the only stale
# thing is /etc/hosts. The fleet-agent runs on the HOST (it can resolve the server
# catalog / mDNS even while the container is DB-blind), so it patches the entry and
# the existing clients auto-reconnect — no runner code change, no recreation.

INFRA_HOST_NAMES = ("afl-mongodb", "afl-minio", "afl-postgres", "afl-extracts")


def resolve_infra_ip(*, log=None) -> str | None:
    """Current NUMERIC IP of the infra host from the server catalog, with the
    loopback→LAN guard (on the infra host the stable name resolves to 127.0.0.1,
    which is poison inside a container).

    This is the address to write into a RUNNING container's /etc/hosts. For the
    value compose bakes in at create time use :func:`container_infra_ip`, which
    prefers Docker's gateway alias when infra is this machine."""
    try:
        from facetwork.servers import catalog as _srv

        infra = _srv.infra()
        ip = _srv.resolve_ip(infra) if infra else None
        if ip and (ip.startswith("127.") or ip == "::1"):
            ip = lan_ip() or ip
        return ip
    except Exception as exc:  # noqa: BLE001
        if log:
            log(f"infra-IP resolve failed: {exc}")
        return None


def container_infra_ip(*, log=None) -> str | None:
    """What container ``extra_hosts`` should map the afl-* names to: Docker's
    ``host-gateway`` alias when infra is this machine (immune to this host's own
    DHCP drift), else the resolved infra IP. See servers.catalog.container_ip."""
    try:
        from facetwork.servers import catalog as _srv

        return _srv.container_ip()
    except Exception as exc:  # noqa: BLE001
        if log:
            log(f"container-IP resolve failed: {exc}")
        return resolve_infra_ip(log=log)


def _container_resolves(container: str, name: str) -> str | None:
    """The IPv4 ``name`` resolves to INSIDE ``container``, or None.

    ``ahostsv4``, NOT ``hosts``: plain ``getent hosts`` is AF_UNSPEC and prefers
    the AAAA answer, so on an IPv6-enabled compose network it returns the
    service's container address from Docker's embedded DNS and never sees the
    IPv4 /etc/hosts entry we write. Comparing against that made every poll
    report drift and rewrite a file that was already correct — 16 containers,
    forever, with a misleading "drift:" line each time."""
    try:
        out = subprocess.run(
            ["docker", "exec", container, "getent", "ahostsv4", name],
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.split()
        return out[0] if out else None
    except Exception:  # noqa: BLE001
        return None


def _container_gateway_ip(container: str) -> str | None:
    """The address ``host-gateway`` resolves to INSIDE ``container`` (Docker
    publishes it as host.docker.internal). ``None`` if it doesn't resolve."""
    return _container_resolves(container, "host.docker.internal")


def _rewrite_hosts(content: str, ip: str, names=INFRA_HOST_NAMES) -> str:
    """Return /etc/hosts content with every ``names`` entry pointed at ``ip``
    (adding any that are missing). Order preserved; other lines untouched."""
    nameset = set(names)
    seen: set[str] = set()
    out: list[str] = []
    for ln in content.splitlines():
        parts = ln.split()
        if len(parts) >= 2 and parts[1] in nameset:
            out.append(f"{ip}\t{parts[1]}")
            seen.add(parts[1])
        else:
            out.append(ln)
    for n in nameset - seen:
        out.append(f"{ip}\t{n}")
    return "\n".join(out) + "\n"


def _runner_containers() -> list[str]:
    try:
        out = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}", "--filter", "name=facetwork-runner-"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        return [n for n in out.stdout.split() if n]
    except Exception:
        return []


def refresh_container_hosts(ip: str, *, log=None) -> list[str]:
    """Patch each running runner container whose afl-mongodb entry ≠ ``ip`` so the
    afl-* names resolve to the current infra IP. Rewrites /etc/hosts in place via
    ``cat > /etc/hosts`` (truncate-in-place works on docker's bind-mounted file;
    ``sed -i`` / rename does not). Returns the names of containers actually patched.
    A no-op when nothing drifted, so it's cheap to call every poll."""
    say = log or (lambda _m: None)
    patched: list[str] = []
    for c in _runner_containers():
        try:
            cur_ip = _container_resolves(c, "afl-mongodb")
            if cur_ip == ip:
                continue
            # Created with `afl-mongodb:host-gateway` (infra is this machine):
            # that mapping is maintained by Docker and can never go stale, so
            # "differs from the LAN IP" is not drift — leave it alone, or we
            # would rewrite it back every poll and downgrade it to an address
            # that dies on the next DHCP lease.
            if cur_ip and cur_ip == _container_gateway_ip(c):
                continue
            content = subprocess.run(
                ["docker", "exec", c, "cat", "/etc/hosts"],
                capture_output=True,
                text=True,
                timeout=10,
            ).stdout
            subprocess.run(
                ["docker", "exec", "-i", c, "sh", "-c", "cat > /etc/hosts"],
                input=_rewrite_hosts(content, ip),
                text=True,
                check=True,
                timeout=15,
                capture_output=True,
            )
            patched.append(c)
            say(f"drift: {c} afl-* {cur_ip} → {ip} (runner auto-reconnects)")
        except Exception as exc:  # noqa: BLE001 - one bad container shouldn't stop the rest
            say(f"could not refresh {c}: {exc}")
    return patched


# ---------------------------------------------------------------------------
# Server groups — restrict which roles a host brings up
# ---------------------------------------------------------------------------

DEFAULT_SERVER_GROUP = "runner"


def host_server_group() -> str:
    """This host's server-group label (FW_SERVER_GROUP, default 'runner').

    The group is a deployment-chosen tag; it decides which central roles this
    host actually brings up (see :func:`role_in_group`)."""
    return os.environ.get("FW_SERVER_GROUP") or DEFAULT_SERVER_GROUP


def role_in_group(spec: dict, host_group: str) -> bool:
    """Whether a role with this central-config spec should run on `host_group`.

    A role MAY carry ``server_groups: [name, ...]``. When present and non-empty
    the role runs ONLY on hosts whose group is in the list; when absent or empty
    it runs everywhere. The empty-means-everywhere default is what makes the
    feature backward-compatible — a fleet that sets no ``server_groups`` anywhere
    behaves exactly as it did before groups existed. Routing correctness does not
    depend on this (a runner only ever claims tasks it has a handler for); the
    gate only decides which runners a host bothers to START, so heavy domains can
    be kept off under-provisioned machines."""
    groups = spec.get("server_groups") or []
    return (not groups) or (host_group in groups)


def parse_role_groups(value: str) -> tuple[str, list[str]]:
    """Parse a ``ROLE:group1,group2`` CLI value (``ROLE:`` clears the restriction).

    Returns ``(role, groups)``; an empty ``groups`` list means "run everywhere"."""
    role, sep, rest = value.partition(":")
    role = role.strip()
    if not role or not sep:
        raise ValueError(f"--role-groups expects ROLE:group1,group2 (got {value!r})")
    groups = [g.strip() for g in rest.split(",") if g.strip()]
    return role, groups


# ---------------------------------------------------------------------------
# Secret store (Fernet)
# ---------------------------------------------------------------------------


def gen_key() -> str:
    from cryptography.fernet import Fernet

    return Fernet.generate_key().decode()


def _fernet():
    key = os.environ.get("FW_FLEET_KEY")
    if not key:
        raise RuntimeError(
            "FW_FLEET_KEY is not set. Generate one with `fleet secret gen-key`, "
            "then export it on each host (and the admin) to use the secret store."
        )
    try:
        from cryptography.fernet import Fernet

        return Fernet(key.encode() if isinstance(key, str) else key)
    except Exception as exc:
        raise RuntimeError(f"invalid FW_FLEET_KEY: {exc}") from exc


def set_minio_secret(db, access_key: str, secret_key: str) -> None:
    token = _fernet().encrypt(
        json.dumps({"access_key": access_key, "secret_key": secret_key}).encode()
    )
    db.fleet_secrets.update_one(
        {"_id": "default"}, {"$set": {"minio_enc": token.decode()}}, upsert=True
    )


def get_minio_secret(db) -> dict | None:
    """Decrypt the stored MinIO creds, or None if no secret is stored. Raises if
    FW_FLEET_KEY is missing/wrong while a secret exists."""
    doc = db.fleet_secrets.find_one({"_id": "default"})
    if not doc or not doc.get("minio_enc"):
        return None
    return json.loads(_fernet().decrypt(doc["minio_enc"].encode()))

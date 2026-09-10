"""Server and handler registration entity definitions."""

import os
import re
from dataclasses import dataclass, field
from functools import lru_cache

_CID = re.compile(r"\b([0-9a-f]{64})\b")


@lru_cache(maxsize=1)
def container_id() -> str:
    """This process's container ID, or "" when not containerised.

    ⚠️ Exists because a count mismatch on a host was UNDIAGNOSABLE. Every runner
    on a host registers with the same ``server_name`` and the same
    ``service_name`` ("afl-runner"), so "23 containers but 22 records" could not
    be resolved to a NAME from the data — only to a number. Three hosts showed it
    simultaneously and none could be investigated.

    Read from /proc rather than from the hostname: compose may set a hostname, and
    an operator certainly may, at which point the hostname stops being the
    container ID and the mapping silently breaks. cgroup v1 puts the ID in
    /proc/self/cgroup; under cgroup v2 that file often carries only "0::/", so
    mountinfo is checked too. Returns "" rather than guessing — a bare-metal
    runner has no container, and inventing one would make it look like a
    container nobody can find.
    """
    for path in ("/proc/self/cgroup", "/proc/self/mountinfo"):
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                m = _CID.search(fh.read())
        except OSError:
            continue
        if m:
            return m.group(1)[:12]
    # Podman and some runtimes expose it directly; cheap to honour.
    for var in ("HOSTNAME_CONTAINER_ID", "CONTAINER_ID"):
        v = (os.environ.get(var) or "").strip()
        if re.fullmatch(r"[0-9a-f]{12,64}", v):
            return v[:12]
    return ""


class ServerState:
    """Server state constants."""

    STARTUP = "startup"
    RUNNING = "running"
    SHUTDOWN = "shutdown"
    ERROR = "error"
    QUARANTINE = "quarantine"


@dataclass
class HandledCount:
    """Event handling statistics."""

    handler: str
    handled: int = 0
    not_handled: int = 0


@dataclass
class ServerDefinition:
    """Agent/server registration.

    Stored in the `servers` collection.
    """

    uuid: str
    server_group: str
    service_name: str
    server_name: str
    server_ips: list[str] = field(default_factory=list)
    start_time: int = 0  # Server start timestamp (ms)
    ping_time: int = 0  # Last ping timestamp (ms)
    topics: list[str] = field(default_factory=list)
    handlers: list[str] = field(default_factory=list)
    handled: list[HandledCount] = field(default_factory=list)
    state: str = ServerState.STARTUP
    http_port: int = 0
    version: str = ""
    manager: str = ""
    error: dict | None = None
    # Task list this runner polls (set from --task-list / RunnerConfig.task_list).
    # Operators query by this to see how their workload is partitioned, and the
    # dashboard surfaces it on /tasks to flag under/over-provisioning.
    task_list: str = "default"
    provided_environments: list = field(default_factory=list)  # manifest hashes this runner can run
    # AST features this runner can execute faithfully (ast_features.py). Makes
    # version skew VISIBLE: `fw fleet status` can show which runners would
    # decline a workflow instead of silently mis-running it.
    ast_features: list = field(default_factory=list)
    # MEASURED capacity of this host, e.g. {"memory_gb": 13.6, "cpus": 12,
    # "scratch_gb": 3400}. Measured rather than configured: a config file saying
    # "16 GB" lies, and the OOM this exists to prevent was against a real
    # 7.75 GiB Docker VM.
    resources: dict = field(default_factory=dict)
    # Short container ID of the process that registered this record, or "" on a
    # bare-metal runner. A default_factory ON PURPOSE: three separate subclasses
    # build ServerDefinition independently (RegistryRunner, AgentPoller,
    # RunnerService), and a field set at the call sites would be added to one and
    # missed by the others — the recurring shape of bugs in this codebase. Computed
    # here, no caller can omit it.
    container: str = field(default_factory=container_id)


@dataclass
class HandlerRegistration:
    """Handler registration for the RegistryRunner.

    Maps a qualified facet name to a Python module + entrypoint
    so the RegistryRunner can dynamically load and dispatch handlers.
    """

    facet_name: str  # Qualified name: "ns.FacetName" (primary key)
    module_uri: str  # Python module path ("my.handlers") or "file:///path/to.py"
    entrypoint: str = "handle"  # Function name within module
    version: str = "1.0.0"
    checksum: str = ""  # For cache invalidation
    timeout_ms: int = 30000
    requirements: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    created: int = 0  # Timestamp (ms)
    updated: int = 0  # Timestamp (ms)


@dataclass
class PublishedSource:
    """Published FFL source for namespace-based lookup.

    Stored in the ``afl_sources`` collection.
    """

    uuid: str
    namespace_name: str
    source_text: str
    namespaces_defined: list[str] = field(default_factory=list)
    version: str = "latest"
    published_at: int = 0
    origin: str = ""
    checksum: str = ""

"""Server and handler registration entity definitions."""

from dataclasses import dataclass, field


class ServerState:
    """Server state constants."""

    STARTUP = "startup"
    RUNNING = "running"
    SHUTDOWN = "shutdown"
    ERROR = "error"


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

"""Base class for declarative handler modules.

Eliminates the boilerplate dispatch/registration pattern that every
handler module repeats. Instead of manually writing ``_DISPATCH``,
``handle()``, ``register_handlers()``, and ``register_xxx_handlers()``,
subclass ``HandlerModule`` and declare handlers declaratively::

    class PostGISHandlers(HandlerModule):
        namespace = "osm.ops"
        timeout_ms = 0  # long-running imports

        @handler("PostGisImport")
        def _postgis_import(self, payload: dict) -> dict:
            ...

        @handler("PostGisImportBatch")
        def _postgis_import_batch(self, payload: dict) -> dict:
            ...

    # Module-level entrypoints (generated automatically)
    _module = PostGISHandlers()
    handle = _module.handle
    register_handlers = _module.register_handlers
    register_poller_handlers = _module.register_poller_handlers
    DISPATCH = _module.dispatch_table

Or for simpler cases, use the class directly without subclassing::

    handlers = HandlerModule(
        namespace="osm.ops",
        dispatch={
            "PostGisImport": _postgis_import_handler,
            "PostGisImportBatch": _postgis_import_batch_handler,
        },
        timeout_ms=0,
    )
    handle = handlers.handle
"""

from __future__ import annotations

import logging
import os
from typing import Callable

log = logging.getLogger(__name__)


def handler(facet_short_name: str) -> Callable:
    """Decorator to register a method as a handler for a facet.

    Usage::

        class MyHandlers(HandlerModule):
            namespace = "my.ns"

            @handler("DoWork")
            def _do_work(self, payload: dict) -> dict:
                return {"result": "done"}
    """

    def decorator(fn: Callable) -> Callable:
        fn._handler_facet = facet_short_name
        return fn

    return decorator


class HandlerModule:
    """Declarative handler module base.

    Provides ``handle()``, ``register_handlers()``, and
    ``register_poller_handlers()`` from either:

    - A ``dispatch`` dict passed to ``__init__``
    - Methods decorated with ``@handler("FacetName")``
    - A ``namespace`` class attribute combined with either approach

    Args:
        namespace: Qualified namespace prefix (e.g. ``"osm.ops"``).
        dispatch: Mapping of short facet names to handler callables.
        timeout_ms: Registration timeout (0 = no per-handler timeout).
        module_file: ``__file__`` of the handler module (for file:// URI).
    """

    namespace: str = ""
    timeout_ms: int = 30000

    def __init__(
        self,
        namespace: str = "",
        dispatch: dict[str, Callable] | None = None,
        timeout_ms: int | None = None,
        module_file: str = "",
    ) -> None:
        if namespace:
            self.namespace = namespace
        if timeout_ms is not None:
            self.timeout_ms = timeout_ms
        self._module_file = module_file

        # Build dispatch table from explicit dict and/or decorated methods
        self._dispatch: dict[str, Callable] = {}

        if dispatch:
            for short_name, fn in dispatch.items():
                qualified = f"{self.namespace}.{short_name}" if self.namespace else short_name
                self._dispatch[qualified] = fn

        # Discover @handler-decorated methods
        for attr_name in dir(self):
            if attr_name.startswith("_") and attr_name != "__init__":
                attr = getattr(self, attr_name, None)
                if callable(attr) and hasattr(attr, "_handler_facet"):
                    short_name = attr._handler_facet
                    qualified = f"{self.namespace}.{short_name}" if self.namespace else short_name
                    self._dispatch[qualified] = attr

    @property
    def dispatch_table(self) -> dict[str, Callable]:
        """The fully-qualified dispatch table."""
        return dict(self._dispatch)

    def handle(self, payload: dict) -> dict:
        """Dispatch entrypoint for RegistryRunner.

        Reads ``_facet_name`` from the payload and routes to the
        registered handler.
        """
        facet_name = payload.get("_facet_name", "")
        fn = self._dispatch.get(facet_name)
        if fn is None:
            raise ValueError(
                f"Unknown facet: {facet_name} "
                f"(registered: {', '.join(sorted(self._dispatch))})"
            )
        return fn(payload)

    def register_handlers(self, runner) -> None:
        """Register all facets with a RegistryRunner."""
        module_uri = ""
        if self._module_file:
            module_uri = f"file://{os.path.abspath(self._module_file)}"

        for facet_name in self._dispatch:
            runner.register_handler(
                facet_name=facet_name,
                module_uri=module_uri,
                entrypoint="handle",
                timeout_ms=self.timeout_ms,
            )

    def register_poller_handlers(self, poller) -> None:
        """Register all facets with an AgentPoller."""
        for facet_name, fn in self._dispatch.items():
            poller.register(facet_name, fn)
            log.debug("Registered handler: %s", facet_name)

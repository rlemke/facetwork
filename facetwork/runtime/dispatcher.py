# Copyright 2025 Ralph Lemke
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Handler dispatchers for inline event execution.

Provides a protocol and implementations for dispatching event facet
handlers inline during evaluation, bypassing the task queue when
a local handler is available.
"""

import asyncio
import importlib
import importlib.util
import inspect
import logging
import os
import threading
from collections.abc import Callable
from typing import Any, Protocol, runtime_checkable

from .persistence import PersistenceAPI

logger = logging.getLogger(__name__)


def registration_module_available(module_uri: str) -> bool:
    """Return True if ``module_uri`` is importable in this process.

    A cheap, side-effect-light check used to decide whether a runner should
    advertise (and therefore claim) tasks for a given handler registration:
    a runner must never accept a task whose handler it cannot load. For
    ``file://`` URIs this is just a path-existence check (the common case —
    each example runner only bind-mounts its own handler source); for dotted
    module names it uses :func:`importlib.util.find_spec`.
    """
    if module_uri.startswith("file://"):
        return os.path.exists(module_uri[len("file://") :])
    try:
        return importlib.util.find_spec(module_uri) is not None
    except (ImportError, ValueError, ModuleNotFoundError):
        return False


@runtime_checkable
class HandlerDispatcher(Protocol):
    """Protocol for inline handler dispatch.

    Implementations check whether a handler is available for a given
    facet name and, if so, invoke it synchronously and return the result.
    """

    def can_dispatch(self, facet_name: str) -> bool:
        """Check if a handler is available for the given facet.

        Args:
            facet_name: Qualified or short facet name

        Returns:
            True if a handler can process this facet
        """
        ...

    def dispatch(self, facet_name: str, payload: dict) -> dict | None:
        """Dispatch a handler for the given facet.

        Args:
            facet_name: Qualified or short facet name
            payload: Parameter values for the handler

        Returns:
            Result dict from the handler, or None

        Raises:
            Exception: If the handler raises
        """
        ...


class RegistryDispatcher:
    """Persistence-backed dispatcher extracted from RegistryRunner.

    Loads handler registrations from the persistence store, dynamically
    imports modules, caches them, and invokes handlers inline.

    Registrations are cached in memory on first load via ``preload()``
    so that a running dispatcher is resilient to database restarts or
    collection wipes.
    """

    def __init__(
        self,
        persistence: PersistenceAPI,
        topics: list[str] | None = None,
    ) -> None:
        self._persistence = persistence
        self._topics = topics or []
        self._module_cache: dict[tuple[str, str], Callable] = {}
        self._import_lock = threading.Lock()
        # In-memory registration cache: facet_name -> registration
        self._reg_cache: dict[str, Any] = {}
        self._reg_cache_loaded = False

    def preload(self, *, verify: bool = False) -> int:
        """Load all handler registrations from persistence into memory.

        When ``verify`` is True, registrations whose handler module is not
        importable in this process are dropped — so ``can_dispatch`` (and a
        runner built on top of this dispatcher) only advertises and claims
        tasks it can actually run. Use this in standalone runners that host a
        subset of the handler universe (e.g. one example's handlers each).

        Returns the number of registrations kept.  Subsequent ``can_dispatch``
        / ``dispatch`` calls use the in-memory cache and never hit the database.
        """
        import fnmatch

        registrations = self._persistence.list_handler_registrations()
        self._reg_cache.clear()
        skipped: list[str] = []
        for reg in registrations:
            # Scope to --topics BEFORE the import-check: a topic-scoped runner
            # must not pay to `find_spec` (and, for cross-domain deps, import)
            # the whole 500+-registration handler universe just to keep its own
            # ~few dozen. Non-matching registrations are simply not ours.
            if self._topics and not any(
                fnmatch.fnmatch(reg.facet_name, t) for t in self._topics
            ):
                continue
            if verify and not registration_module_available(reg.module_uri):
                skipped.append(reg.facet_name)
                continue
            self._reg_cache[reg.facet_name] = reg
        self._reg_cache_loaded = True
        kept = len(self._reg_cache)
        if skipped:
            preview = ", ".join(skipped[:8]) + (", …" if len(skipped) > 8 else "")
            logger.info(
                "RegistryDispatcher: preloaded %d handler(s); skipped %d not loadable here (%s)",
                kept,
                len(skipped),
                preview,
            )
        else:
            logger.info("RegistryDispatcher: preloaded %d registrations", kept)
        return kept

    def dispatchable_facets(self) -> list[str]:
        """Facet names this dispatcher can serve. Only meaningful after ``preload()``."""
        return list(self._reg_cache.keys())

    def can_dispatch(self, facet_name: str) -> bool:
        """Check if a handler registration exists for the facet."""
        reg = self._find_registration(facet_name)
        return reg is not None

    def check_loadable(self, facet_name: str) -> str | None:
        """Return ``None`` if the facet has a registered handler whose module
        imports and whose entrypoint is callable; otherwise a short reason
        string. Stronger than :meth:`can_dispatch` (which only checks that a
        registration exists): it actually imports the handler module and
        resolves the entrypoint, catching a missing registration, a module that
        won't import, or a missing/non-callable entrypoint.

        It does NOT trace *lazy* imports inside dispatched handler bodies: a
        handler module commonly hosts many facets' handlers behind one dispatch
        entrypoint, so a module-level import scan cannot attribute a sibling
        handler's broken lazy import to this facet (it would false-positive).
        A broken lazy import therefore surfaces at dispatch, not here.
        """
        reg = self._find_registration(facet_name)
        if reg is None:
            return "no handler registered"
        try:
            module = self._import_module(reg)
            if not callable(getattr(module, reg.entrypoint, None)):
                return f"entrypoint '{reg.entrypoint}' is not callable"
        except Exception as e:  # module won't import
            return f"{type(e).__name__}: {str(e)[:140]}"
        return None

    def get_timeout_ms(self, facet_name: str) -> int:
        """Return timeout_ms for the handler, or 0 if not found."""
        reg = self._find_registration(facet_name)
        return getattr(reg, "timeout_ms", 0) if reg else 0

    def dispatch(self, facet_name: str, payload: dict) -> dict | None:
        """Load and invoke the registered handler.

        Args:
            facet_name: The event facet name
            payload: Parameter values

        Returns:
            Result dict from the handler

        Raises:
            ImportError: If the module cannot be loaded
            AttributeError: If the entrypoint is not found
            TypeError: If the entrypoint is not callable
            Exception: Any exception from the handler itself
        """
        reg = self._find_registration(facet_name)
        if reg is None:
            return None

        callback = self._load_handler(reg)

        # Inject dispatch metadata
        payload = dict(payload)  # shallow copy
        payload["_facet_name"] = facet_name
        if reg.metadata:
            payload["_handler_metadata"] = reg.metadata

        if inspect.iscoroutinefunction(callback):
            return asyncio.run(callback(payload))
        return callback(payload)

    def _find_registration(self, facet_name: str) -> Any:
        """Find a handler registration by exact or short name.

        Uses the in-memory cache when preloaded, otherwise falls back
        to the persistence store.
        """
        if self._reg_cache_loaded:
            reg = self._reg_cache.get(facet_name)
            if reg is None and "." in facet_name:
                short_name = facet_name.rsplit(".", 1)[-1]
                reg = self._reg_cache.get(short_name)
            return reg

        reg = self._persistence.get_handler_registration(facet_name)
        if reg is None and "." in facet_name:
            short_name = facet_name.rsplit(".", 1)[-1]
            reg = self._persistence.get_handler_registration(short_name)
        return reg

    def _load_handler(self, reg: Any) -> Callable:
        """Load a handler callable, using cache when possible."""
        cache_key = (reg.module_uri, reg.checksum)
        if cache_key in self._module_cache:
            return self._module_cache[cache_key]

        with self._import_lock:
            # Double-check after acquiring lock
            if cache_key in self._module_cache:
                return self._module_cache[cache_key]
            handler = self._import_handler(reg)
            self._module_cache[cache_key] = handler
            return handler

    def _import_module(self, reg: Any) -> Any:
        """Import and return the handler's *module*.

        ``file:///path/to/module.py`` is loaded as a proper package import so
        relative imports work; a dotted ``my.package.module`` via
        ``importlib.import_module``.
        """
        if reg.module_uri.startswith("file://"):
            return self._import_from_file(reg.module_uri[7:])
        return importlib.import_module(reg.module_uri)

    def _import_handler(self, reg: Any) -> Callable:
        """Import and return the handler callable from a registration."""
        module = self._import_module(reg)
        attr = getattr(module, reg.entrypoint)
        if not callable(attr):
            raise TypeError(f"Entrypoint '{reg.entrypoint}' in '{reg.module_uri}' is not callable")
        return attr

    @staticmethod
    def _import_from_file(file_path: str) -> Any:
        """Import a module from a file path with proper package context.

        Walks up from the file to find the package root (furthest ancestor
        directory containing ``__init__.py``), adds the root's parent to
        ``sys.path``, computes the dotted module name, and uses
        ``importlib.import_module`` so that relative imports work.
        """
        import os
        import sys

        file_path = os.path.abspath(file_path)
        parts: list[str] = []
        current = file_path

        # Strip .py extension for module name
        stem = os.path.splitext(os.path.basename(current))[0]
        parts.append(stem)
        current = os.path.dirname(current)

        # Walk up while __init__.py exists
        while os.path.isfile(os.path.join(current, "__init__.py")):
            parts.append(os.path.basename(current))
            current = os.path.dirname(current)

        # current is now the package root's parent
        parts.reverse()
        dotted_name = ".".join(parts)

        # Ensure the correct example directory is at the front of sys.path.
        if current in sys.path:
            sys.path.remove(current)
        sys.path.insert(0, current)

        # Multiple examples share the same top-level "handlers" package.
        # Check if we already have the exact module cached from the right
        # file.  If not, evict the entire package tree so importlib
        # re-discovers from the correct sys.path entry.
        top_package = parts[0]
        cached = sys.modules.get(dotted_name)
        if cached is not None:
            cached_file = os.path.abspath(getattr(cached, "__file__", "") or "")
            if cached_file == os.path.abspath(file_path):
                return cached  # exact match — skip re-import
        # Evict if any cached module under top_package is from a different root
        expected_root = os.path.abspath(os.path.join(current, top_package))
        for key in list(sys.modules):
            if key == top_package or key.startswith(top_package + "."):
                mod = sys.modules[key]
                mod_file = os.path.abspath(getattr(mod, "__file__", "") or "")
                if mod_file and not mod_file.startswith(expected_root):
                    # Found a module from a different example — evict all
                    stale = [
                        k
                        for k in list(sys.modules)
                        if k == top_package or k.startswith(top_package + ".")
                    ]
                    for k in stale:
                        del sys.modules[k]
                    importlib.invalidate_caches()
                    break

        logger.debug(
            "_import_from_file: importing %s from %s (package_root_parent=%s)",
            dotted_name,
            file_path,
            current,
        )
        try:
            return importlib.import_module(dotted_name)
        except ImportError:
            logger.exception(
                "Failed to import %s (sys.path[:5]=%s, handlers_in_modules=%s)",
                dotted_name,
                sys.path[:5],
                "handlers" in sys.modules,
            )
            raise

    @property
    def module_cache(self) -> dict[tuple[str, str], Callable]:
        """Expose cache for testing."""
        return self._module_cache


class InMemoryDispatcher:
    """In-memory dispatcher wrapping a dict of callbacks.

    Useful for tests and lightweight setups where handlers are
    registered directly in Python code.
    """

    def __init__(self) -> None:
        self._handlers: dict[str, Callable] = {}

    def register(self, facet_name: str, callback: Callable) -> None:
        """Register a synchronous callback for a facet.

        Args:
            facet_name: The event facet name
            callback: Function (payload) -> result dict
        """
        self._handlers[facet_name] = callback

    def register_async(self, facet_name: str, callback: Callable) -> None:
        """Register an async callback for a facet.

        Args:
            facet_name: The event facet name
            callback: Async function (payload) -> result dict
        """
        self._handlers[facet_name] = callback

    def can_dispatch(self, facet_name: str) -> bool:
        """Check if a handler is registered for the facet."""
        return self._find_handler(facet_name) is not None

    def dispatch(self, facet_name: str, payload: dict) -> dict | None:
        """Invoke the registered callback."""
        callback = self._find_handler(facet_name)
        if callback is None:
            return None

        if inspect.iscoroutinefunction(callback):
            return asyncio.run(callback(payload))
        return callback(payload)

    def _find_handler(self, facet_name: str) -> Callable | None:
        """Find handler by exact name or short-name fallback."""
        handler = self._handlers.get(facet_name)
        if handler is not None:
            return handler
        # Short-name fallback: "ns.Sub.FacetName" -> "FacetName"
        if "." in facet_name:
            short_name = facet_name.rsplit(".", 1)[-1]
            return self._handlers.get(short_name)
        return None


class ToolRegistryDispatcher:
    """Adapter that wraps an existing ToolRegistry as a HandlerDispatcher.

    Bridges the ClaudeAgentRunner's ToolRegistry to the dispatcher protocol.
    """

    def __init__(self, tool_registry: Any) -> None:
        self._registry = tool_registry

    def can_dispatch(self, facet_name: str) -> bool:
        """Delegate to ToolRegistry.has_handler()."""
        return self._registry.has_handler(facet_name)

    def dispatch(self, facet_name: str, payload: dict) -> dict | None:
        """Delegate to ToolRegistry.handle()."""
        return self._registry.handle(facet_name, payload)


class CompositeDispatcher:
    """Chains multiple dispatchers with priority ordering.

    The first dispatcher that ``can_dispatch`` a facet wins.
    """

    def __init__(self, *dispatchers: HandlerDispatcher) -> None:
        self._dispatchers = list(dispatchers)

    def can_dispatch(self, facet_name: str) -> bool:
        """Check if any child dispatcher can handle the facet."""
        return any(d.can_dispatch(facet_name) for d in self._dispatchers)

    def dispatch(self, facet_name: str, payload: dict) -> dict | None:
        """Dispatch to the first child that can handle the facet."""
        for d in self._dispatchers:
            if d.can_dispatch(facet_name):
                return d.dispatch(facet_name, payload)
        return None

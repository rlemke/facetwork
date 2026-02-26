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
from collections.abc import Callable
from typing import Any, Protocol, runtime_checkable

from .persistence import PersistenceAPI

logger = logging.getLogger(__name__)


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
    """

    def __init__(
        self,
        persistence: PersistenceAPI,
        topics: list[str] | None = None,
    ) -> None:
        self._persistence = persistence
        self._topics = topics or []
        self._module_cache: dict[tuple[str, str], Callable] = {}

    def can_dispatch(self, facet_name: str) -> bool:
        """Check if a handler registration exists for the facet."""
        reg = self._find_registration(facet_name)
        return reg is not None

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
        """Find a handler registration by exact or short name."""
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

        handler = self._import_handler(reg)
        self._module_cache[cache_key] = handler
        return handler

    def _import_handler(self, reg: Any) -> Callable:
        """Import and return the handler callable from a registration.

        Supports two URI formats:
        - ``file:///path/to/module.py`` — loaded as a proper package import
          so that relative imports work. Walks up from the file to find
          the package root (furthest ancestor with ``__init__.py``), adds
          the root's parent to ``sys.path``, and uses ``import_module``.
        - ``my.package.module`` — loaded via ``importlib.import_module``
        """
        if reg.module_uri.startswith("file://"):
            module = self._import_from_file(reg.module_uri[7:])
        else:
            module = importlib.import_module(reg.module_uri)

        attr = getattr(module, reg.entrypoint)
        if not callable(attr):
            raise TypeError(
                f"Entrypoint '{reg.entrypoint}' in '{reg.module_uri}' is not callable"
            )
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

        if current not in sys.path:
            sys.path.insert(0, current)

        return importlib.import_module(dotted_name)

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

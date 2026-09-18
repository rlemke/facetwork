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


#: Per-process memo of the on-disk verification cache, keyed by module_uri.
_IMPORT_VERIFY: dict[str, bool] | None = None
_IMPORT_VERIFY_PATH: str | None = None
_IMPORT_VERIFY_LOCK = threading.Lock()


def _import_verify_cache_path() -> str:
    """Where this host records which handler modules really import.

    ⚠️ Keyed by IMAGE TAG and stored ON THE HOST, and both halves matter. The
    whole reason this verification exists is that the same image behaves
    differently on different CPUs — macmini01's 2009 processor cannot run the
    image's numpy while every other host can — so a cache shared between hosts
    would be actively wrong. A file on the host is implicitly host-scoped; the
    tag in its name keeps a rollout from inheriting the previous image's answers.
    """
    tag = (os.environ.get("FW_RUNNER_IMAGE") or "").rsplit(":", 1)[-1] or "untagged"
    base = os.environ.get("FW_LOCAL_SCRATCH") or "/tmp"
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in tag)
    return os.path.join(base, f"fw-import-verify-{safe}.json")


def _load_import_verify() -> dict[str, bool]:
    global _IMPORT_VERIFY, _IMPORT_VERIFY_PATH
    if _IMPORT_VERIFY is not None:
        return _IMPORT_VERIFY
    with _IMPORT_VERIFY_LOCK:
        if _IMPORT_VERIFY is None:
            _IMPORT_VERIFY_PATH = _import_verify_cache_path()
            try:
                import json

                with open(_IMPORT_VERIFY_PATH, encoding="utf-8") as fh:
                    loaded = json.load(fh)
                _IMPORT_VERIFY = {k: bool(v) for k, v in loaded.items()}
            except (OSError, ValueError):
                _IMPORT_VERIFY = {}
    return _IMPORT_VERIFY


def _save_import_verify() -> None:
    """Best effort. A read-only scratch dir costs a re-verify, never a wrong answer."""
    if _IMPORT_VERIFY is None or not _IMPORT_VERIFY_PATH:
        return
    try:
        import json

        tmp = f"{_IMPORT_VERIFY_PATH}.{os.getpid()}"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(_IMPORT_VERIFY, fh)
        os.replace(tmp, _IMPORT_VERIFY_PATH)
    except OSError:
        pass


_CORE_STACK_LOCK = threading.Lock()
_CORE_STACK_VERDICT: tuple[bool, str] | None = None


def core_stack_unusable() -> str | None:
    """Reason the image's core stack cannot run HERE, or None if it can.

    Host-level admission. :func:`registration_module_available` imports a handler
    MODULE, which is necessary but not sufficient: a dependency imported inside a
    function is never exercised by importing the module, so a handler whose body
    does ``import numpy`` passes the per-registration check and then dies at
    dispatch. Measured on macmini01 (2009 Core 2 Duo, no SSE4.2/POPCNT):

        osm_geocoder import: OK          <- the handler module loads
        numpy import:        ImportError <- its dependency does not
        still advertised:    265 handlers, 122 numpy-dependent

    No per-handler declaration can fix that, because it depends on where an
    author happened to put an import statement. What the failure actually is, is
    an IMAGE that cannot run on this CPU — a property of the host, checkable once.

    ⚠️ **Absent is not broken.** A deployment that simply has no numpy (someone
    running only their own handlers) must keep advertising normally, so:

    * ``find_spec`` returns None  -> NOT installed -> not a reason to refuse.
      Handlers that need it fail their own per-registration import check.
    * installed but raises        -> present and unusable -> refuse.

    That asymmetry is the whole point: refusing on absence would silence every
    minimal deployment, which is a worse failure than the one being fixed.

    The sentinel set is ``FW_CORE_IMPORTS`` (comma-separated, default ``numpy``)
    so a deployment whose core stack is something else can say so. Verdict is
    cached per process — a CPU does not gain instructions at runtime.
    """
    global _CORE_STACK_VERDICT
    if _CORE_STACK_VERDICT is not None:
        return _CORE_STACK_VERDICT[1] or None
    with _CORE_STACK_LOCK:
        if _CORE_STACK_VERDICT is None:
            _CORE_STACK_VERDICT = (True, _probe_core_stack())
    return _CORE_STACK_VERDICT[1] or None


def _probe_core_stack() -> str:
    names = [
        n.strip()
        for n in os.environ.get("FW_CORE_IMPORTS", "numpy").split(",")
        if n.strip()
    ]
    for name in names:
        try:
            if importlib.util.find_spec(name) is None:
                continue          # not installed here — see the docstring
        except Exception:
            continue              # cannot even ask; not evidence of breakage
        try:
            importlib.import_module(name)
        except Exception as exc:
            # Deliberately broad. numpy reports a CPU-baseline mismatch as
            # ImportError in some versions and RuntimeError in others, and the
            # verdict must not depend on which. It is still narrow in effect:
            # this line is reached only for a module that IS installed and does
            # NOT import, which is precisely the unusable-image case.
            return f"{name} is installed but does not import here: {type(exc).__name__}: {exc}"
    return ""


def registration_module_available(module_uri: str) -> bool:
    """Return True if ``module_uri`` can actually be IMPORTED in this process.

    Used to decide whether a runner should advertise (and therefore claim) tasks
    for a handler registration: a runner must never accept a task whose handler
    it cannot load.

    ⚠️ This used to be ``find_spec`` alone, which only LOCATES a module and never
    executes it — so a module whose first line was an impossible import passed,
    and the function did not test the thing its own docstring promised. That is
    how one host advertised 265 facets it could not execute: its 2009 CPU cannot
    run the image's numpy, every handler module that imports numpy fails, and
    every one of them was located successfully.

    Now two stages:

    1. ``find_spec`` as a cheap pre-filter. Cannot be located → cannot be
       imported, decided without executing anything.
    2. A real import, **cached per image tag on this host** (see
       :func:`_import_verify_cache_path`). A generalist runner carries ~265
       registrations, so the import pass is paid once per image per host rather
       than on every start.

    Failures are cached too: an ImportError under a given image on a given host
    is a stable fact, and re-deriving it every start is pure cost.
    """
    if module_uri.startswith("file://"):
        return os.path.exists(module_uri[len("file://") :])

    # Stage 1 — cheap, no execution.
    try:
        if importlib.util.find_spec(module_uri) is None:
            return False
    except (ImportError, ValueError, ModuleNotFoundError):
        return False

    # Stage 2 — the truthful one.
    cache = _load_import_verify()
    if module_uri in cache:
        return cache[module_uri]
    try:
        importlib.import_module(module_uri)
        ok = True
    except Exception as exc:  # noqa: BLE001 — any failure means "cannot run it here"
        ok = False
        logger.warning(
            "handler module %s locates but does NOT import here (%s: %s) — "
            "not advertising its facets",
            module_uri,
            type(exc).__name__,
            str(exc)[:200],
        )
    cache[module_uri] = ok
    _save_import_verify()
    return ok


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
        # keyed by (module_uri, checksum, entrypoint) — see _load_handler
        self._module_cache: dict[tuple[str, str, str], Callable] = {}
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

        from .task_list_routing import is_ambient as _is_ambient

        registrations = self._persistence.list_handler_registrations()
        self._reg_cache.clear()
        skipped: list[str] = []

        # Host-level admission, BEFORE the per-registration check. If the image's
        # core stack cannot run on this CPU, no amount of per-handler verification
        # helps: a dependency imported inside a function passes the module import
        # and dies at dispatch. Refuse the whole domain surface and keep only the
        # ambient `fw.*` facets, which are stdlib-only and genuinely do work here.
        core_reason = core_stack_unusable() if verify else None
        if core_reason:
            logger.warning(
                "RegistryDispatcher: NOT advertising domain handlers on this host "
                "— %s. Only the built-in fw.* facets are offered. This host cannot "
                "run this image's handlers, so advertising them would claim work it "
                "would then fail.",
                core_reason,
            )

        for reg in registrations:
            # Scope to --topics BEFORE the import-check: a topic-scoped runner
            # must not pay to `find_spec` (and, for cross-domain deps, import)
            # the whole 500+-registration handler universe just to keep its own
            # ~few dozen. Non-matching registrations are simply not ours.
            #
            # EXCEPT the framework's own `fw.*` facets, which are ambient: every
            # runner registers them and any runner can execute them, like the
            # fw:execute / fw:resume protocol tasks. Dropping them here is what
            # made a topic-scoped fleet leave `fw.http.Fetch` pending with no
            # claimer — they never reached the advertise step, so exempting them
            # only there was not enough. Their modules ship in the wheel, so the
            # import-check they still go through costs nothing.
            ambient = _is_ambient(reg.facet_name)
            if (
                self._topics
                and not ambient
                and not any(fnmatch.fnmatch(reg.facet_name, t) for t in self._topics)
            ):
                continue
            if core_reason and not ambient:
                skipped.append(reg.facet_name)
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
        # The cache stores the RESOLVED callable, so the entrypoint MUST be part
        # of the key: two facets registered from the same module (same module_uri
        # + checksum) but with different entrypoints would otherwise both get the
        # first-cached callable, silently mis-routing every facet after the first
        # to one handler. (Domain packages use a single dispatch entrypoint, which
        # masked this — but a module with N distinct entrypoints hit it.)
        cache_key = (reg.module_uri, reg.checksum, reg.entrypoint)
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
    def module_cache(self) -> dict[tuple[str, str, str], Callable]:
        """Expose cache for testing. Keyed by (module_uri, checksum, entrypoint)."""
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

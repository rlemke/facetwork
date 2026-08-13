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

"""Unit tests for handler dispatcher implementations."""

import pytest

from facetwork.runtime import (
    HandlerRegistration,
    MemoryStore,
    ToolRegistry,
)
from facetwork.runtime.dispatcher import (
    CompositeDispatcher,
    InMemoryDispatcher,
    RegistryDispatcher,
    ToolRegistryDispatcher,
)

# =========================================================================
# Fixtures
# =========================================================================


@pytest.fixture
def store():
    """Fresh in-memory store."""
    return MemoryStore()


@pytest.fixture
def handler_file(tmp_path):
    """Create a temporary handler module file."""
    f = tmp_path / "test_handler.py"
    f.write_text("def handle(payload):\n    return {'output': payload['input'] * 2}\n")
    return f


# =========================================================================
# TestRegistryDispatcher
# =========================================================================


class TestRegistryDispatcher:
    """Tests for persistence-backed dispatcher."""

    def test_can_dispatch_registered_handler(self, store, handler_file):
        """Registered facet returns True."""
        store.save_handler_registration(
            HandlerRegistration(
                facet_name="ns.Double",
                module_uri=f"file://{handler_file}",
                entrypoint="handle",
            )
        )
        dispatcher = RegistryDispatcher(persistence=store)
        assert dispatcher.can_dispatch("ns.Double") is True

    def test_can_dispatch_unregistered(self, store):
        """Unregistered facet returns False."""
        dispatcher = RegistryDispatcher(persistence=store)
        assert dispatcher.can_dispatch("ns.Unknown") is False

    def test_can_dispatch_short_name_fallback(self, store, handler_file):
        """ns.Facet found via short name 'Facet' when registered as 'Facet'."""
        store.save_handler_registration(
            HandlerRegistration(
                facet_name="Double",
                module_uri=f"file://{handler_file}",
                entrypoint="handle",
            )
        )
        dispatcher = RegistryDispatcher(persistence=store)
        # Exact match fails, but short-name fallback succeeds
        assert dispatcher.can_dispatch("ns.Double") is True

    def test_dispatch_sync_handler(self, store, handler_file):
        """Sync handler invoked and result returned."""
        store.save_handler_registration(
            HandlerRegistration(
                facet_name="ns.Double",
                module_uri=f"file://{handler_file}",
                entrypoint="handle",
            )
        )
        dispatcher = RegistryDispatcher(persistence=store)
        result = dispatcher.dispatch("ns.Double", {"input": 5})
        assert result == {"output": 10}

    def test_distinct_entrypoints_same_module_do_not_collide(self, store, tmp_path):
        """Two facets from ONE module with DIFFERENT entrypoints must each
        dispatch to their own handler.

        Regression: the handler cache was keyed by ``(module_uri, checksum)``
        without the entrypoint, so the first-dispatched facet's callable was
        cached and returned for every other facet sharing that module —
        silently mis-routing every facet after the first. (Domain packages use
        a single dispatch entrypoint, which masked it; a module with N distinct
        entrypoints hit it.)
        """
        f = tmp_path / "multi_handler.py"
        f.write_text(
            "def alpha(payload):\n    return {'who': 'alpha'}\n\n"
            "def beta(payload):\n    return {'who': 'beta'}\n"
        )
        mod = f"file://{f}"
        # same module_uri + (default) checksum, different entrypoints
        store.save_handler_registration(
            HandlerRegistration(facet_name="ns.Alpha", module_uri=mod, entrypoint="alpha")
        )
        store.save_handler_registration(
            HandlerRegistration(facet_name="ns.Beta", module_uri=mod, entrypoint="beta")
        )
        dispatcher = RegistryDispatcher(persistence=store)
        # Dispatch alpha first (populates the cache), then beta — and again in
        # the reverse order to prove the cache is genuinely per-entrypoint.
        assert dispatcher.dispatch("ns.Alpha", {}) == {"who": "alpha"}
        assert dispatcher.dispatch("ns.Beta", {}) == {"who": "beta"}
        assert dispatcher.dispatch("ns.Beta", {}) == {"who": "beta"}
        assert dispatcher.dispatch("ns.Alpha", {}) == {"who": "alpha"}

    def test_dispatch_async_handler(self, store, tmp_path):
        """Async handler detected and invoked via asyncio.run()."""
        f = tmp_path / "async_handler.py"
        f.write_text(
            "import asyncio\n"
            "async def handle(payload):\n"
            "    await asyncio.sleep(0)\n"
            "    return {'output': payload['input'] + 100}\n"
        )
        store.save_handler_registration(
            HandlerRegistration(
                facet_name="ns.AsyncOp",
                module_uri=f"file://{f}",
                entrypoint="handle",
            )
        )
        dispatcher = RegistryDispatcher(persistence=store)
        result = dispatcher.dispatch("ns.AsyncOp", {"input": 7})
        assert result["output"] == 107

    def test_dispatch_handler_exception(self, store, tmp_path):
        """Exception from handler propagated."""
        f = tmp_path / "error_handler.py"
        f.write_text("def handle(payload):\n    raise ValueError('handler failed')\n")
        store.save_handler_registration(
            HandlerRegistration(
                facet_name="ns.Fail",
                module_uri=f"file://{f}",
                entrypoint="handle",
            )
        )
        dispatcher = RegistryDispatcher(persistence=store)
        with pytest.raises(ValueError, match="handler failed"):
            dispatcher.dispatch("ns.Fail", {"input": 1})

    def test_module_cache_hit(self, store, handler_file):
        """Second dispatch uses cached module."""
        store.save_handler_registration(
            HandlerRegistration(
                facet_name="ns.Double",
                module_uri=f"file://{handler_file}",
                entrypoint="handle",
                checksum="abc",
            )
        )
        dispatcher = RegistryDispatcher(persistence=store)
        dispatcher.dispatch("ns.Double", {"input": 1})
        dispatcher.dispatch("ns.Double", {"input": 2})
        # Only one entry in cache
        assert len(dispatcher.module_cache) == 1

    def test_module_cache_invalidation(self, store, handler_file):
        """Changed checksum reloads module."""
        store.save_handler_registration(
            HandlerRegistration(
                facet_name="ns.Double",
                module_uri=f"file://{handler_file}",
                entrypoint="handle",
                checksum="v1",
            )
        )
        dispatcher = RegistryDispatcher(persistence=store)
        dispatcher.dispatch("ns.Double", {"input": 1})

        # Update registration with new checksum
        store.save_handler_registration(
            HandlerRegistration(
                facet_name="ns.Double",
                module_uri=f"file://{handler_file}",
                entrypoint="handle",
                checksum="v2",
            )
        )
        dispatcher.dispatch("ns.Double", {"input": 2})
        # Two cache entries: (uri, v1) and (uri, v2)
        assert len(dispatcher.module_cache) == 2

    def test_file_uri_handler(self, store, tmp_path):
        """file:// URI loading works."""
        f = tmp_path / "file_handler.py"
        f.write_text("def process(payload):\n    return {'result': payload.get('x', 0) + 42}\n")
        store.save_handler_registration(
            HandlerRegistration(
                facet_name="ns.FileOp",
                module_uri=f"file://{f}",
                entrypoint="process",
            )
        )
        dispatcher = RegistryDispatcher(persistence=store)
        result = dispatcher.dispatch("ns.FileOp", {"x": 8})
        assert result["result"] == 50

    def test_dispatch_unregistered_returns_none(self, store):
        """Dispatching unregistered facet returns None."""
        dispatcher = RegistryDispatcher(persistence=store)
        result = dispatcher.dispatch("ns.Unknown", {"input": 1})
        assert result is None

    def test_get_timeout_ms(self, store, handler_file):
        """get_timeout_ms returns the registration's timeout_ms."""
        store.save_handler_registration(
            HandlerRegistration(
                facet_name="ns.Slow",
                module_uri=f"file://{handler_file}",
                entrypoint="handle",
                timeout_ms=120000,
            )
        )
        dispatcher = RegistryDispatcher(persistence=store)
        assert dispatcher.get_timeout_ms("ns.Slow") == 120000

    def test_get_timeout_ms_unregistered(self, store):
        """get_timeout_ms returns 0 for unregistered facet."""
        dispatcher = RegistryDispatcher(persistence=store)
        assert dispatcher.get_timeout_ms("ns.Unknown") == 0

    def test_preload_verify_drops_unloadable_registrations(self, store, handler_file):
        """preload(verify=True) keeps only registrations whose module is importable here."""
        store.save_handler_registration(
            HandlerRegistration(
                facet_name="ns.Here", module_uri=f"file://{handler_file}", entrypoint="handle"
            )
        )
        store.save_handler_registration(
            HandlerRegistration(
                facet_name="ns.Gone",
                module_uri="file:///definitely/not/here/handler.py",
                entrypoint="handle",
            )
        )
        dispatcher = RegistryDispatcher(persistence=store)

        # Without verify: both are advertised (legacy behaviour).
        assert dispatcher.preload() == 2
        assert set(dispatcher.dispatchable_facets()) == {"ns.Here", "ns.Gone"}

        # With verify: the missing-module one is dropped — the runner won't claim it.
        assert dispatcher.preload(verify=True) == 1
        assert dispatcher.dispatchable_facets() == ["ns.Here"]
        assert dispatcher.can_dispatch("ns.Here") is True
        assert dispatcher.can_dispatch("ns.Gone") is False

    def test_topics_scope_registrations_but_never_the_ambient_builtins(self, store, handler_file):
        """Topic scoping keeps a runner to its own domain — except for `fw.*`.

        The framework's own facets are ambient: every runner registers them and
        any runner can execute them, like the fw:execute / fw:resume protocol
        tasks. On a fleet where every runner is scoped to its domain
        (`--topics census.*`), dropping them here meant NO runner advertised
        `fw.http.Fetch`, so its task sat pending with server_id=None while all
        thirteen runners logged "Registered 21 built-in handler(s)" and looked
        healthy.
        """
        for name in ("census.Acs.Fetch", "osm.cache.Download", "fw.http.Fetch", "fw.file.List"):
            store.save_handler_registration(
                HandlerRegistration(
                    facet_name=name, module_uri=f"file://{handler_file}", entrypoint="handle"
                )
            )

        dispatcher = RegistryDispatcher(persistence=store, topics=["census.*"])
        dispatcher.preload(verify=True)
        advertised = set(dispatcher.dispatchable_facets())

        assert "census.Acs.Fetch" in advertised, "topic scoping broke"
        assert "osm.cache.Download" not in advertised, "scoping must still exclude other domains"
        assert {"fw.http.Fetch", "fw.file.List"} <= advertised, (
            "built-ins hidden by --topics; no runner in a scoped fleet would claim their tasks"
        )

    def test_registration_module_available(self, handler_file):
        """registration_module_available: True for an existing file:// path, False otherwise."""
        from facetwork.runtime.dispatcher import registration_module_available

        assert registration_module_available(f"file://{handler_file}") is True
        assert registration_module_available("file:///no/such/file.py") is False
        assert registration_module_available("os.path") is True  # importable dotted module
        assert registration_module_available("totally.not.a.module") is False


# =========================================================================
# TestInMemoryDispatcher
# =========================================================================


class TestInMemoryDispatcher:
    """Tests for in-memory callback dispatcher."""

    def test_register_and_dispatch(self):
        """Register callback, dispatch succeeds."""
        dispatcher = InMemoryDispatcher()
        dispatcher.register("ns.AddOne", lambda p: {"output": p["input"] + 1})
        result = dispatcher.dispatch("ns.AddOne", {"input": 5})
        assert result == {"output": 6}

    def test_can_dispatch_false_when_empty(self):
        """No handlers registered returns False."""
        dispatcher = InMemoryDispatcher()
        assert dispatcher.can_dispatch("ns.Anything") is False

    def test_short_name_fallback(self):
        """Qualified name falls back to short name."""
        dispatcher = InMemoryDispatcher()
        dispatcher.register("AddOne", lambda p: {"output": p["input"] + 1})
        # "ns.AddOne" should fall back to "AddOne"
        assert dispatcher.can_dispatch("ns.AddOne") is True
        result = dispatcher.dispatch("ns.AddOne", {"input": 10})
        assert result == {"output": 11}

    def test_async_callback(self):
        """Async callback detected and invoked."""
        dispatcher = InMemoryDispatcher()

        async def async_handler(payload):
            return {"output": payload["input"] * 3}

        dispatcher.register_async("ns.Triple", async_handler)
        result = dispatcher.dispatch("ns.Triple", {"input": 4})
        assert result == {"output": 12}

    def test_dispatch_unregistered_returns_none(self):
        """Dispatching unregistered facet returns None."""
        dispatcher = InMemoryDispatcher()
        result = dispatcher.dispatch("ns.Unknown", {"input": 1})
        assert result is None

    def test_exact_match_preferred_over_short(self):
        """Exact match is preferred over short-name fallback."""
        dispatcher = InMemoryDispatcher()
        dispatcher.register("AddOne", lambda p: {"output": "short"})
        dispatcher.register("ns.AddOne", lambda p: {"output": "exact"})
        result = dispatcher.dispatch("ns.AddOne", {"input": 1})
        assert result == {"output": "exact"}


# =========================================================================
# TestToolRegistryDispatcher
# =========================================================================


class TestToolRegistryDispatcher:
    """Tests for ToolRegistry adapter."""

    def test_delegates_to_tool_registry(self):
        """Wraps existing ToolRegistry."""
        reg = ToolRegistry()
        reg.register("ns.Op", lambda p: {"result": p["x"] * 2})

        dispatcher = ToolRegistryDispatcher(reg)
        result = dispatcher.dispatch("ns.Op", {"x": 5})
        assert result == {"result": 10}

    def test_can_dispatch_delegates(self):
        """Delegates to has_handler."""
        reg = ToolRegistry()
        reg.register("ns.Op", lambda p: {"result": 1})

        dispatcher = ToolRegistryDispatcher(reg)
        assert dispatcher.can_dispatch("ns.Op") is True
        assert dispatcher.can_dispatch("ns.Unknown") is False

    def test_default_handler_support(self):
        """ToolRegistry default handler is accessible."""
        reg = ToolRegistry()
        reg.set_default_handler(lambda event_type, p: {"handled": event_type})

        dispatcher = ToolRegistryDispatcher(reg)
        # Default handler makes has_handler return True for any facet
        assert dispatcher.can_dispatch("ns.Anything") is True
        result = dispatcher.dispatch("ns.Anything", {})
        assert result == {"handled": "ns.Anything"}


# =========================================================================
# TestCompositeDispatcher
# =========================================================================


class TestCompositeDispatcher:
    """Tests for composite dispatcher chaining."""

    def test_first_match_wins(self):
        """Priority ordering respected — first dispatcher wins."""
        d1 = InMemoryDispatcher()
        d1.register("ns.Op", lambda p: {"source": "first"})
        d2 = InMemoryDispatcher()
        d2.register("ns.Op", lambda p: {"source": "second"})

        composite = CompositeDispatcher(d1, d2)
        result = composite.dispatch("ns.Op", {})
        assert result == {"source": "first"}

    def test_fallthrough(self):
        """First dispatcher declines, second handles."""
        d1 = InMemoryDispatcher()
        # d1 has no handler for ns.Op
        d2 = InMemoryDispatcher()
        d2.register("ns.Op", lambda p: {"source": "second"})

        composite = CompositeDispatcher(d1, d2)
        assert composite.can_dispatch("ns.Op") is True
        result = composite.dispatch("ns.Op", {})
        assert result == {"source": "second"}

    def test_none_can_dispatch(self):
        """Returns False when no dispatcher matches."""
        d1 = InMemoryDispatcher()
        d2 = InMemoryDispatcher()

        composite = CompositeDispatcher(d1, d2)
        assert composite.can_dispatch("ns.Unknown") is False

    def test_dispatch_no_match_returns_none(self):
        """Dispatch returns None when no handler matches."""
        d1 = InMemoryDispatcher()
        composite = CompositeDispatcher(d1)
        result = composite.dispatch("ns.Unknown", {"x": 1})
        assert result is None

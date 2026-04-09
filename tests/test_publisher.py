"""Tests for FFL source publisher."""

import pytest

try:
    import mongomock

    MONGOMOCK_AVAILABLE = True
except ImportError:
    MONGOMOCK_AVAILABLE = False

from facetwork.publisher import PublishError, SourcePublisher

pytestmark = pytest.mark.skipif(
    not MONGOMOCK_AVAILABLE,
    reason="mongomock is required for publisher tests",
)


@pytest.fixture
def store():
    """Create a MongoStore backed by mongomock."""
    from facetwork.runtime.mongo_store import MongoStore

    client = mongomock.MongoClient()
    s = MongoStore(database_name="afl_test_publisher", client=client)
    yield s
    s.drop_database()
    s.close()


@pytest.fixture
def publisher(store):
    return SourcePublisher(store)


class TestPublish:
    """Tests for SourcePublisher.publish."""

    def test_publish_single_namespace(self, publisher, store):
        """Publish a file with one namespace."""
        source = "namespace my.types { facet Addr() }"
        published = publisher.publish(source)

        assert len(published) == 1
        assert published[0].namespace_name == "my.types"
        assert published[0].version == "latest"
        assert published[0].checksum != ""

        # Verify in store
        found = store.get_source_by_namespace("my.types")
        assert found is not None
        assert found.source_text == source

    def test_publish_multi_namespace_file(self, publisher, store):
        """A file with multiple namespaces creates one doc per namespace."""
        source = "namespace ns.a { facet A() }\nnamespace ns.b { facet B() }\n"
        published = publisher.publish(source)

        assert len(published) == 2
        names = {p.namespace_name for p in published}
        assert names == {"ns.a", "ns.b"}

        # Both should record all namespaces defined
        for p in published:
            assert set(p.namespaces_defined) == {"ns.a", "ns.b"}

    def test_publish_with_version(self, publisher, store):
        """Publish with explicit version."""
        source = "namespace v.ns { facet V() }"
        published = publisher.publish(source, version="1.0.0")

        assert published[0].version == "1.0.0"
        assert store.get_source_by_namespace("v.ns", version="1.0.0") is not None
        assert store.get_source_by_namespace("v.ns", version="latest") is None

    def test_publish_duplicate_error(self, publisher):
        """Publishing same namespace with different content raises error."""
        publisher.publish("namespace dup.ns { facet A() }")

        with pytest.raises(PublishError, match="already published"):
            publisher.publish("namespace dup.ns { facet B() }")

    def test_publish_duplicate_same_content_ok(self, publisher):
        """Re-publishing the same content is idempotent (no error)."""
        source = "namespace same.ns { facet Same() }"
        publisher.publish(source)
        # Should not raise
        published = publisher.publish(source)
        assert len(published) == 1

    def test_publish_force_overwrite(self, publisher, store):
        """Force flag overwrites existing content."""
        publisher.publish("namespace force.ns { facet A() }")
        new_source = "namespace force.ns { facet B() }"
        published = publisher.publish(new_source, force=True)

        assert len(published) == 1
        found = store.get_source_by_namespace("force.ns")
        assert "facet B()" in found.source_text

    def test_publish_invalid_source(self, publisher):
        """Cannot publish source that doesn't parse."""
        with pytest.raises(PublishError, match="Cannot parse"):
            publisher.publish("@@@ invalid syntax")

    def test_publish_no_namespaces(self, publisher):
        """Cannot publish source with no namespaces."""
        with pytest.raises(PublishError, match="no namespaces"):
            publisher.publish("facet TopLevel()")

    def test_publish_origin_tracked(self, publisher, store):
        """Origin is stored on the published document."""
        source = "namespace orig.ns { facet O() }"
        _published = publisher.publish(source, origin="test-origin")

        found = store.get_source_by_namespace("orig.ns")
        assert found.origin == "test-origin"


class TestUnpublish:
    """Tests for SourcePublisher.unpublish."""

    def test_unpublish_existing(self, publisher):
        """Unpublish removes a published namespace."""
        publisher.publish("namespace rm.ns { facet R() }")
        assert publisher.unpublish("rm.ns") is True

    def test_unpublish_nonexistent(self, publisher):
        """Unpublishing a non-existent namespace returns False."""
        assert publisher.unpublish("nonexistent.ns") is False


class TestListPublished:
    """Tests for SourcePublisher.list_published."""

    def test_list_empty(self, publisher):
        """List returns empty when nothing published."""
        assert publisher.list_published() == []

    def test_list_after_publish(self, publisher):
        """List returns all published namespaces."""
        publisher.publish("namespace list.a { facet A() }")
        publisher.publish("namespace list.b { facet B() }")

        sources = publisher.list_published()
        names = {s.namespace_name for s in sources}
        assert names == {"list.a", "list.b"}


class TestRoundTrip:
    """Test publish → resolve roundtrip."""

    def test_publish_then_resolve(self, store):
        """Published source can be resolved by DependencyResolver."""
        from facetwork.parser import FFLParser
        from facetwork.resolver import DependencyResolver, MongoDBNamespaceResolver
        from facetwork.source import CompilerInput, FileOrigin, SourceEntry

        # Publish a library namespace
        publisher = SourcePublisher(store)
        publisher.publish("namespace lib.types { facet TypeDef() }")

        # Create a mock MongoDB resolver that uses our store
        mongo_resolver = MongoDBNamespaceResolver.__new__(MongoDBNamespaceResolver)
        mongo_resolver._store = store
        mongo_resolver._config = None

        # Parse a source that depends on the published namespace
        parser = FFLParser()
        entry = SourceEntry(
            text="namespace app { use lib.types\n facet App() }",
            origin=FileOrigin(path="<test>"),
            is_library=False,
        )
        ci = CompilerInput(primary_sources=[entry])
        program, registry = parser.parse_sources(ci)

        # Resolve
        resolver = DependencyResolver(mongodb_resolver=mongo_resolver)
        result_prog, _, _ = resolver.resolve(program, registry, ci)

        ns_names = {ns.name for ns in result_prog.namespaces}
        assert ns_names == {"app", "lib.types"}

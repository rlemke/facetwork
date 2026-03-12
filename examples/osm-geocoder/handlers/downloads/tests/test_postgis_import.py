"""Tests for PostGIS import engine and handler dispatch.

Unit tests (always run):
- Module-level constants and utility functions
- Handler dispatch pattern

Integration tests (gated by --postgis):
- Live PostGIS schema creation and import

Requires the PostGIS Docker service to be running for live tests:

    docker compose --profile postgis up -d

Run with:

    pytest tests/test_postgis_import.py --postgis -v
"""

import importlib
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

OSM_DIR = str(Path(__file__).resolve().parent.parent.parent.parent)


def _osm_import(module_name: str):
    """Import an OSM handlers submodule, ensuring correct sys.path."""
    if OSM_DIR in sys.path:
        sys.path.remove(OSM_DIR)
    sys.path.insert(0, OSM_DIR)

    full_name = f"handlers.{module_name}"

    if full_name in sys.modules:
        mod = sys.modules[full_name]
        mod_file = getattr(mod, "__file__", "")
        if mod_file and "osm-geocoder" in mod_file:
            return mod
        del sys.modules[full_name]

    if "handlers" in sys.modules:
        pkg = sys.modules["handlers"]
        pkg_file = getattr(pkg, "__file__", "")
        if pkg_file and "osm-geocoder" not in pkg_file:
            stale = [k for k in sys.modules if k == "handlers" or k.startswith("handlers.")]
            for k in stale:
                del sys.modules[k]

    return importlib.import_module(full_name)


# ---------------------------------------------------------------------------
# Unit tests — always run
# ---------------------------------------------------------------------------


class TestPostgisImporterModule:
    """Test postgis_importer module constants and utilities."""

    def test_has_osmium_is_boolean(self):
        mod = _osm_import("postgis_importer")
        assert isinstance(mod.HAS_OSMIUM, bool)

    def test_has_psycopg2_is_boolean(self):
        mod = _osm_import("postgis_importer")
        assert isinstance(mod.HAS_PSYCOPG2, bool)

    def test_get_postgis_url_default(self):
        mod = _osm_import("postgis_importer")
        with patch.dict(os.environ, {}, clear=True):
            # Remove AFL_POSTGIS_URL if set
            env = os.environ.copy()
            env.pop("AFL_POSTGIS_URL", None)
            with patch.dict(os.environ, env, clear=True):
                url = mod.get_postgis_url()
                assert url == mod.DEFAULT_POSTGIS_URL

    def test_get_postgis_url_env_override(self):
        mod = _osm_import("postgis_importer")
        custom_url = "postgresql://user:pass@dbhost:5433/mydb"
        with patch.dict(os.environ, {"AFL_POSTGIS_URL": custom_url}):
            url = mod.get_postgis_url()
            assert url == custom_url

    def test_sanitize_url_strips_password(self):
        mod = _osm_import("postgis_importer")
        url = "postgresql://osm:secretpass@localhost:5432/osm"
        sanitized = mod.sanitize_url(url)
        assert "secretpass" not in sanitized
        assert "***" in sanitized
        assert "osm" in sanitized  # username preserved
        assert "localhost:5432" in sanitized

    def test_sanitize_url_no_password(self):
        mod = _osm_import("postgis_importer")
        url = "postgresql://localhost:5432/osm"
        sanitized = mod.sanitize_url(url)
        assert sanitized == url

    def test_ddl_contains_expected_keywords(self):
        mod = _osm_import("postgis_importer")
        assert "postgis" in mod.CREATE_POSTGIS_EXT.lower()
        assert "osm_nodes" in mod.CREATE_NODES_TABLE
        assert "osm_ways" in mod.CREATE_WAYS_TABLE
        assert "osm_import_log" in mod.CREATE_IMPORT_LOG_TABLE
        assert "geometry" in mod.CREATE_NODES_TABLE.lower()
        assert "geometry" in mod.CREATE_WAYS_TABLE.lower()
        assert "jsonb" in mod.CREATE_NODES_TABLE.lower()
        assert "gist" in mod.CREATE_NODES_GEOM_IDX.lower()
        assert "gin" in mod.CREATE_NODES_TAGS_IDX.lower()


class TestPostgisHandlerDispatch:
    """Test postgis_handlers dispatch adapter pattern."""

    def test_dispatch_key_present(self):
        mod = _osm_import("postgis_handlers")
        assert "osm.ops.PostGisImport" in mod._DISPATCH

    def test_dispatch_count_is_1(self):
        mod = _osm_import("postgis_handlers")
        assert len(mod._DISPATCH) == 1

    def test_handle_returns_dict_with_stats(self):
        mod = _osm_import("postgis_handlers")
        result = mod.handle(
            {
                "_facet_name": "osm.ops.PostGisImport",
                "cache": {"url": "http://example.com/test.pbf", "path": "", "date": "", "size": 0},
            }
        )
        assert isinstance(result, dict)
        assert "stats" in result

    def test_handle_unknown_facet_raises(self):
        mod = _osm_import("postgis_handlers")
        with pytest.raises(ValueError, match="Unknown facet"):
            mod.handle({"_facet_name": "osm.ops.NonExistent"})

    def test_register_handlers_call_count(self):
        mod = _osm_import("postgis_handlers")
        runner = MagicMock()
        mod.register_handlers(runner)
        assert runner.register_handler.call_count == 1


# ---------------------------------------------------------------------------
# Integration tests — gated by --postgis
# ---------------------------------------------------------------------------


class TestPostgisImportLive:
    """Live PostGIS integration tests.

    Requires:
        docker compose --profile postgis up -d
        pytest tests/test_postgis_import.py --postgis -v
    """

    pytestmark = pytest.mark.skipif("not config.getoption('--postgis')")

    def _get_conn(self):
        mod = _osm_import("postgis_importer")
        if not mod.HAS_PSYCOPG2:
            pytest.skip("psycopg2 not installed")
        import psycopg2

        url = mod.get_postgis_url()
        return psycopg2.connect(url), mod

    def test_ensure_schema_creates_tables(self):
        conn, mod = self._get_conn()
        try:
            mod.ensure_schema(conn)
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public' AND table_name IN "
                    "('osm_nodes', 'osm_ways', 'osm_import_log') "
                    "ORDER BY table_name"
                )
                tables = [row[0] for row in cur.fetchall()]
            assert "osm_nodes" in tables
            assert "osm_ways" in tables
            assert "osm_import_log" in tables
        finally:
            conn.close()

    def test_spatial_indexes_exist(self):
        conn, mod = self._get_conn()
        try:
            mod.ensure_schema(conn)
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT indexname FROM pg_indexes "
                    "WHERE tablename IN ('osm_nodes', 'osm_ways') "
                    "ORDER BY indexname"
                )
                indexes = [row[0] for row in cur.fetchall()]
            assert "idx_osm_nodes_geom" in indexes
            assert "idx_osm_nodes_tags" in indexes
            assert "idx_osm_ways_geom" in indexes
            assert "idx_osm_ways_tags" in indexes
        finally:
            conn.close()

    def test_import_log_entry_written(self):
        conn, mod = self._get_conn()
        try:
            mod.ensure_schema(conn)
            test_url = "http://test.example.com/test-log-entry.osm.pbf"
            with conn.cursor() as cur:
                cur.execute(mod.INSERT_LOG_SQL, (test_url, "/tmp/test.pbf", 42, 7))
            conn.commit()
            with conn.cursor() as cur:
                cur.execute(mod.CHECK_PRIOR_IMPORT_SQL, (test_url,))
                row = cur.fetchone()
            assert row is not None
            assert row[1] == 42  # node_count
            assert row[2] == 7  # way_count
        finally:
            # Clean up test data
            with conn.cursor() as cur:
                cur.execute("DELETE FROM osm_import_log WHERE url = %s", (test_url,))
            conn.commit()
            conn.close()

    def test_reimport_detects_prior_import(self):
        conn, mod = self._get_conn()
        try:
            mod.ensure_schema(conn)
            test_url = "http://test.example.com/reimport-test.osm.pbf"
            # Insert a prior import record
            with conn.cursor() as cur:
                cur.execute(mod.INSERT_LOG_SQL, (test_url, "/tmp/test.pbf", 10, 5))
            conn.commit()
            # Check that prior import is detected
            with conn.cursor() as cur:
                cur.execute(mod.CHECK_PRIOR_IMPORT_SQL, (test_url,))
                row = cur.fetchone()
            assert row is not None
        finally:
            # Clean up test data
            with conn.cursor() as cur:
                cur.execute("DELETE FROM osm_import_log WHERE url = %s", (test_url,))
            conn.commit()
            conn.close()

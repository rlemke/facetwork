"""Tests for pgRouting topology handlers and dispatch.

Unit tests (always run):
- Module-level constants and utility functions
- Handler dispatch pattern
- SQL constants

Integration tests (gated by --postgis):
- Live pgRouting extension and topology log creation

Requires the PostGIS Docker service (with pgRouting) to be running for live tests:

    docker compose --profile postgis up -d

Run with:

    pytest tests/test_pgrouting_handlers.py --postgis -v
"""

import importlib
import sys
from pathlib import Path
from unittest.mock import MagicMock

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


class TestPgroutingModule:
    """Test pgrouting_handlers module constants and utilities."""

    def test_namespace(self):
        mod = _osm_import("pgrouting_handlers")
        assert mod.NAMESPACE == "osm.ops.PgRouting"

    def test_profile_configs_has_car(self):
        mod = _osm_import("pgrouting_handlers")
        assert "car" in mod.PROFILE_CONFIGS

    def test_profile_configs_has_bike(self):
        mod = _osm_import("pgrouting_handlers")
        assert "bike" in mod.PROFILE_CONFIGS

    def test_profile_configs_has_foot(self):
        mod = _osm_import("pgrouting_handlers")
        assert "foot" in mod.PROFILE_CONFIGS

    def test_create_pgrouting_ext_sql(self):
        mod = _osm_import("pgrouting_handlers")
        assert "pgrouting" in mod.CREATE_PGROUTING_EXT.lower()

    def test_topology_log_ddl_has_region(self):
        mod = _osm_import("pgrouting_handlers")
        assert "region" in mod.CREATE_TOPOLOGY_LOG

    def test_topology_log_ddl_has_profile(self):
        mod = _osm_import("pgrouting_handlers")
        assert "profile" in mod.CREATE_TOPOLOGY_LOG

    def test_prefix_normalizes_name(self):
        mod = _osm_import("pgrouting_handlers")
        assert mod._prefix("North America") == "north_america"
        assert mod._prefix("Germany") == "germany"
        assert mod._prefix("île-de-france") == "_le_de_france"

    def test_parse_dsn(self):
        mod = _osm_import("pgrouting_handlers")
        dsn = mod._parse_dsn("postgresql://afl_osm:pass@localhost:5432/osm")
        assert dsn["dbname"] == "osm"
        assert dsn["user"] == "afl_osm"
        assert dsn["host"] == "localhost"
        assert dsn["port"] == "5432"

    def test_parse_dsn_default_port(self):
        mod = _osm_import("pgrouting_handlers")
        dsn = mod._parse_dsn("postgresql://user@host/db")
        assert dsn["port"] == "5432"


class TestPgroutingDispatch:
    """Test pgrouting_handlers dispatch adapter pattern."""

    def test_dispatch_has_build(self):
        mod = _osm_import("pgrouting_handlers")
        assert "osm.ops.PgRouting.BuildRoutingTopology" in mod._DISPATCH

    def test_dispatch_has_validate(self):
        mod = _osm_import("pgrouting_handlers")
        assert "osm.ops.PgRouting.ValidateTopology" in mod._DISPATCH

    def test_dispatch_has_clean(self):
        mod = _osm_import("pgrouting_handlers")
        assert "osm.ops.PgRouting.CleanTopology" in mod._DISPATCH

    def test_dispatch_count_is_3(self):
        mod = _osm_import("pgrouting_handlers")
        assert len(mod._DISPATCH) == 3

    def test_handle_unknown_facet_raises(self):
        mod = _osm_import("pgrouting_handlers")
        with pytest.raises(ValueError, match="Unknown facet"):
            mod.handle({"_facet_name": "osm.ops.PgRouting.NonExistent"})

    def test_register_handlers_call_count(self):
        mod = _osm_import("pgrouting_handlers")
        runner = MagicMock()
        mod.register_handlers(runner)
        assert runner.register_handler.call_count == 3

    def test_register_pgrouting_handlers_poller(self):
        mod = _osm_import("pgrouting_handlers")
        poller = MagicMock()
        mod.register_pgrouting_handlers(poller)
        assert poller.register.call_count == 3

    def test_build_requires_path(self):
        mod = _osm_import("pgrouting_handlers")
        with pytest.raises(ValueError, match="No PBF path"):
            mod.handle(
                {
                    "_facet_name": "osm.ops.PgRouting.BuildRoutingTopology",
                    "cache": {"path": ""},
                    "region": "test",
                }
            )

    def test_build_requires_region(self):
        mod = _osm_import("pgrouting_handlers")
        with pytest.raises(ValueError, match="Region is required"):
            mod.handle(
                {
                    "_facet_name": "osm.ops.PgRouting.BuildRoutingTopology",
                    "cache": {"path": "/tmp/test.pbf"},
                    "region": "",
                }
            )

    def test_validate_returns_invalid_for_empty(self):
        mod = _osm_import("pgrouting_handlers")
        result = mod.handle(
            {
                "_facet_name": "osm.ops.PgRouting.ValidateTopology",
                "topology": {"region": ""},
            }
        )
        assert result["valid"] is False
        assert result["nodeCount"] == 0

    def test_clean_returns_false_for_empty(self):
        mod = _osm_import("pgrouting_handlers")
        result = mod.handle(
            {
                "_facet_name": "osm.ops.PgRouting.CleanTopology",
                "region": "",
            }
        )
        assert result["deleted"] is False


# ---------------------------------------------------------------------------
# Integration tests — gated by --postgis
# ---------------------------------------------------------------------------


class TestPgroutingLive:
    """Live pgRouting integration tests.

    Requires:
        docker compose --profile postgis up -d
        pytest tests/test_pgrouting_handlers.py --postgis -v
    """

    pytestmark = pytest.mark.skipif("not config.getoption('--postgis')")

    def _get_conn(self):
        mod = _osm_import("postgis_importer")
        if not mod.HAS_PSYCOPG2:
            pytest.skip("psycopg2 not installed")
        import psycopg2

        url = mod.get_postgis_url()
        return psycopg2.connect(url), mod

    def test_pgrouting_extension_creates(self):
        conn, _ = self._get_conn()
        pgr_mod = _osm_import("pgrouting_handlers")
        try:
            pgr_mod._ensure_pgrouting(conn)
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM pg_extension WHERE extname = 'pgrouting'")
                assert cur.fetchone() is not None
        finally:
            conn.close()

    def test_topology_log_table_creates(self):
        conn, _ = self._get_conn()
        pgr_mod = _osm_import("pgrouting_handlers")
        try:
            pgr_mod._ensure_pgrouting(conn)
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema = 'public' AND table_name = 'routing_topology_log'"
                )
                assert cur.fetchone() is not None
        finally:
            conn.close()

    def test_topology_log_unique_constraint(self):
        conn, _ = self._get_conn()
        pgr_mod = _osm_import("pgrouting_handlers")
        try:
            pgr_mod._ensure_pgrouting(conn)
            with conn.cursor() as cur:
                # Insert first record
                cur.execute(pgr_mod.UPSERT_TOPOLOGY_LOG, ("test-region", "car", 100, 50))
                conn.commit()
                # Upsert same region+profile — should update, not duplicate
                cur.execute(pgr_mod.UPSERT_TOPOLOGY_LOG, ("test-region", "car", 200, 100))
                conn.commit()
                cur.execute(pgr_mod.CHECK_PRIOR_TOPOLOGY, ("test-region", "car"))
                row = cur.fetchone()
                assert row is not None
                assert row[1] == 200  # updated edge count
        finally:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM routing_topology_log WHERE region = 'test-region'")
            conn.commit()
            conn.close()

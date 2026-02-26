"""Tests for MongoDB ingestion handlers.

Verifies OutputStore index creation, GeoJSON/CSV/JSON ingestion with
upsert idempotency, and the ingestion handler dispatch pattern.
"""

import importlib
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

CENSUS_DIR = str(Path(__file__).resolve().parent.parent)


def _census_import(module_name: str):
    """Import a census-us handlers submodule, ensuring correct sys.path."""
    if CENSUS_DIR in sys.path:
        sys.path.remove(CENSUS_DIR)
    sys.path.insert(0, CENSUS_DIR)

    full_name = f"handlers.{module_name}"

    if full_name in sys.modules:
        mod = sys.modules[full_name]
        mod_file = getattr(mod, "__file__", "")
        if mod_file and "census-us" in mod_file:
            return mod
        del sys.modules[full_name]

    if "handlers" in sys.modules:
        pkg = sys.modules["handlers"]
        pkg_file = getattr(pkg, "__file__", "")
        if pkg_file and "census-us" not in pkg_file:
            stale = [k for k in sys.modules
                     if k == "handlers" or k.startswith("handlers.")]
            for k in stale:
                del sys.modules[k]

    return importlib.import_module(full_name)


# ------------------------------------------------------------------
# OutputStore tests (mongomock)
# ------------------------------------------------------------------

class TestOutputStore:
    """Test OutputStore with mocked MongoDB collections."""

    @pytest.fixture()
    def mock_db(self):
        db = MagicMock()
        db.handler_output = MagicMock()
        db.handler_output_meta = MagicMock()
        return db

    @pytest.fixture()
    def store(self, mock_db):
        mod = _census_import("ingestion.db_ingest")
        return mod.OutputStore(mock_db), mock_db

    def test_indexes_created(self, store):
        _, db = store
        output_calls = db.handler_output.create_index.call_args_list
        assert len(output_calls) == 2
        # Compound unique index
        assert output_calls[0][1]["name"] == "output_upsert_key"
        assert output_calls[0][1]["unique"] is True
        # 2dsphere index
        assert output_calls[1][1]["name"] == "output_geo_2dsphere"
        assert output_calls[1][1]["sparse"] is True
        # Meta index
        meta_calls = db.handler_output_meta.create_index.call_args_list
        assert len(meta_calls) == 1
        assert meta_calls[0][1]["name"] == "meta_upsert_key"

    def test_ingest_geojson(self, store, tmp_path):
        output_store, db = store
        geojson_file = tmp_path / "counties.geojson"
        geojson_file.write_text(json.dumps({
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"GEOID": "01001", "NAME": "Autauga"},
                    "geometry": {"type": "Point", "coordinates": [-86.6, 32.5]},
                },
                {
                    "type": "Feature",
                    "properties": {"GEOID": "01003", "NAME": "Baldwin"},
                    "geometry": {"type": "Point", "coordinates": [-87.7, 30.7]},
                },
            ],
        }))

        count = output_store.ingest_geojson(
            path=str(geojson_file),
            dataset_key="census.tiger.county.01",
            feature_key_field="GEOID",
            facet_name="census.Ingestion.CountiesToDB",
        )

        assert count == 2
        db.handler_output.bulk_write.assert_called_once()
        ops = db.handler_output.bulk_write.call_args[0][0]
        assert len(ops) == 2
        # Verify meta was updated
        db.handler_output_meta.replace_one.assert_called_once()
        meta_doc = db.handler_output_meta.replace_one.call_args[0][1]
        assert meta_doc["record_count"] == 2
        assert meta_doc["data_type"] == "geojson_feature"

    def test_ingest_geojson_ops_use_upsert(self, store, tmp_path):
        """bulk_write operations use upsert=True for idempotency."""
        output_store, db = store
        geojson_file = tmp_path / "counties.geojson"
        geojson_file.write_text(json.dumps({
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "properties": {"GEOID": "01001", "NAME": "Autauga"},
                "geometry": {"type": "Point", "coordinates": [-86.6, 32.5]},
            }],
        }))

        output_store.ingest_geojson(
            path=str(geojson_file),
            dataset_key="census.tiger.county.01",
            feature_key_field="GEOID",
            facet_name="census.Ingestion.CountiesToDB",
        )

        ops = db.handler_output.bulk_write.call_args[0][0]
        op = ops[0]
        # ReplaceOne with upsert=True
        assert op._upsert is True
        assert op._filter == {
            "dataset_key": "census.tiger.county.01",
            "feature_key": "01001",
        }

    def test_ingest_csv(self, store, tmp_path):
        output_store, db = store
        csv_file = tmp_path / "population.csv"
        csv_file.write_text(
            "GEOID,NAME,B01003_001E\n"
            "0500000US01001,Autauga,59285\n"
            "0500000US01003,Baldwin,231767\n"
        )

        count = output_store.ingest_csv(
            path=str(csv_file),
            dataset_key="census.acs.b01003.01",
            feature_key_field="GEOID",
            facet_name="census.Ingestion.PopulationToDB",
        )

        assert count == 2
        db.handler_output.bulk_write.assert_called_once()
        ops = db.handler_output.bulk_write.call_args[0][0]
        assert len(ops) == 2
        meta_doc = db.handler_output_meta.replace_one.call_args[0][1]
        assert meta_doc["record_count"] == 2
        assert meta_doc["data_type"] == "csv_record"

    def test_ingest_csv_ops_use_upsert(self, store, tmp_path):
        """CSV bulk_write operations use upsert=True."""
        output_store, db = store
        csv_file = tmp_path / "pop.csv"
        csv_file.write_text("GEOID,B01003_001E\n0500000US01001,59285\n")

        output_store.ingest_csv(
            path=str(csv_file),
            dataset_key="census.acs.b01003.01",
            feature_key_field="GEOID",
            facet_name="census.Ingestion.PopulationToDB",
        )

        ops = db.handler_output.bulk_write.call_args[0][0]
        assert ops[0]._upsert is True
        assert ops[0]._filter["feature_key"] == "0500000US01001"

    def test_ingest_json(self, store, tmp_path):
        output_store, db = store
        json_file = tmp_path / "summary.json"
        json_file.write_text(json.dumps({
            "state_fips": "01",
            "state_name": "Alabama",
            "tables_joined": 5,
            "record_count": 67,
        }))

        count = output_store.ingest_json(
            path=str(json_file),
            dataset_key="census.summary.01",
            key_field="state_fips",
            facet_name="census.Ingestion.SummaryToDB",
        )

        assert count == 1
        db.handler_output.replace_one.assert_called_once()
        replace_args = db.handler_output.replace_one.call_args
        assert replace_args[0][0] == {
            "dataset_key": "census.summary.01",
            "feature_key": "01",
        }
        doc = replace_args[0][1]
        assert doc["properties"]["state_name"] == "Alabama"
        assert replace_args[1]["upsert"] is True


# ------------------------------------------------------------------
# Ingestion handler dispatch tests
# ------------------------------------------------------------------

class TestIngestionHandlers:
    def test_dispatch_keys(self):
        mod = _census_import("ingestion.ingestion_handlers")
        assert len(mod._DISPATCH) == 15
        for key in mod._DISPATCH:
            assert key.startswith("census.Ingestion.")

    def test_dispatch_key_names(self):
        mod = _census_import("ingestion.ingestion_handlers")
        expected = {
            "census.Ingestion.PopulationToDB",
            "census.Ingestion.IncomeToDB",
            "census.Ingestion.HousingToDB",
            "census.Ingestion.EducationToDB",
            "census.Ingestion.CommutingToDB",
            "census.Ingestion.TenureToDB",
            "census.Ingestion.HouseholdsToDB",
            "census.Ingestion.AgeToDB",
            "census.Ingestion.VehiclesToDB",
            "census.Ingestion.RaceToDB",
            "census.Ingestion.PovertyToDB",
            "census.Ingestion.EmploymentToDB",
            "census.Ingestion.CountiesToDB",
            "census.Ingestion.JoinedToDB",
            "census.Ingestion.SummaryToDB",
        }
        assert set(mod._DISPATCH.keys()) == expected

    def test_handle_unknown_facet(self):
        mod = _census_import("ingestion.ingestion_handlers")
        with pytest.raises(ValueError, match="Unknown facet"):
            mod.handle({"_facet_name": "census.Ingestion.NonExistent"})

    def test_register_handlers(self):
        mod = _census_import("ingestion.ingestion_handlers")
        runner = MagicMock()
        mod.register_handlers(runner)
        assert runner.register_handler.call_count == 15

    def test_register_ingestion_handlers_poller(self):
        mod = _census_import("ingestion.ingestion_handlers")
        poller = MagicMock()
        mod.register_ingestion_handlers(poller)
        assert poller.register.call_count == 15

    def test_handle_counties_to_db(self, tmp_path):
        """CountiesToDB reads GeoJSON and calls OutputStore.ingest_geojson."""
        mod = _census_import("ingestion.ingestion_handlers")
        geojson_file = tmp_path / "counties.geojson"
        geojson_file.write_text(json.dumps({
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"GEOID": "01001", "NAME": "Autauga"},
                    "geometry": {"type": "Point", "coordinates": [-86.6, 32.5]},
                },
            ],
        }))

        mock_store = MagicMock()
        mock_store.ingest_geojson.return_value = 1

        with patch.object(mod, "get_mongo_db"):
            with patch.object(mod, "OutputStore", return_value=mock_store):
                result = mod.handle({
                    "_facet_name": "census.Ingestion.CountiesToDB",
                    "result": {
                        "output_path": str(geojson_file),
                        "feature_count": 1,
                    },
                    "state_fips": "01",
                })

        assert "ingestion" in result
        assert result["ingestion"]["dataset_key"] == "census.tiger.county.01"
        assert result["ingestion"]["record_count"] == 1
        mock_store.ingest_geojson.assert_called_once()

    def test_handle_population_to_db(self, tmp_path):
        """PopulationToDB reads CSV and calls OutputStore.ingest_csv."""
        mod = _census_import("ingestion.ingestion_handlers")
        csv_file = tmp_path / "pop.csv"
        csv_file.write_text("GEOID,B01003_001E\n01001,59285\n")

        mock_store = MagicMock()
        mock_store.ingest_csv.return_value = 1

        with patch.object(mod, "get_mongo_db"):
            with patch.object(mod, "OutputStore", return_value=mock_store):
                result = mod.handle({
                    "_facet_name": "census.Ingestion.PopulationToDB",
                    "result": {
                        "output_path": str(csv_file),
                        "table_id": "B01003",
                        "record_count": 1,
                    },
                    "state_fips": "01",
                })

        assert "ingestion" in result
        assert result["ingestion"]["dataset_key"] == "census.acs.b01003.01"
        mock_store.ingest_csv.assert_called_once()

    def test_handle_summary_to_db(self, tmp_path):
        """SummaryToDB reads JSON and calls OutputStore.ingest_json."""
        mod = _census_import("ingestion.ingestion_handlers")
        json_file = tmp_path / "summary.json"
        json_file.write_text(json.dumps({
            "state_fips": "01", "state_name": "Alabama",
            "tables_joined": 5, "record_count": 67,
        }))

        mock_store = MagicMock()
        mock_store.ingest_json.return_value = 1

        with patch.object(mod, "get_mongo_db"):
            with patch.object(mod, "OutputStore", return_value=mock_store):
                result = mod.handle({
                    "_facet_name": "census.Ingestion.SummaryToDB",
                    "result": {
                        "output_path": str(json_file),
                        "state_fips": "01",
                        "state_name": "Alabama",
                    },
                    "state_fips": "01",
                })

        assert "ingestion" in result
        assert result["ingestion"]["dataset_key"] == "census.summary.01"
        mock_store.ingest_json.assert_called_once()

    def test_handle_joined_to_db_empty_path(self):
        """JoinedToDB with empty output_path returns 0 records."""
        mod = _census_import("ingestion.ingestion_handlers")

        mock_store = MagicMock()

        with patch.object(mod, "get_mongo_db"):
            with patch.object(mod, "OutputStore", return_value=mock_store):
                result = mod.handle({
                    "_facet_name": "census.Ingestion.JoinedToDB",
                    "result": {"output_path": ""},
                    "state_fips": "01",
                })

        assert result["ingestion"]["record_count"] == 0
        mock_store.ingest_geojson.assert_not_called()

    def test_error_step_log(self):
        """Error is logged to _step_log before re-raising."""
        mod = _census_import("ingestion.ingestion_handlers")
        step_log = MagicMock()

        with patch.object(mod, "get_mongo_db",
                          side_effect=RuntimeError("connection refused")):
            with pytest.raises(RuntimeError, match="connection refused"):
                mod.handle({
                    "_facet_name": "census.Ingestion.CountiesToDB",
                    "result": {"output_path": "/tmp/test.geojson"},
                    "state_fips": "01",
                    "_step_log": step_log,
                })

        step_log.assert_called_once()
        assert "connection refused" in step_log.call_args[0][0]
        assert step_log.call_args[1]["level"] == "error"


# ------------------------------------------------------------------
# Registration count tests (updated totals)
# ------------------------------------------------------------------

class TestInitRegistryHandlersWithIngestion:
    def test_register_all_registry_handlers(self):
        mod = _census_import("__init__")
        runner = MagicMock()
        mod.register_all_registry_handlers(runner)
        # 3 downloads + 12 ACS + 4 TIGER + 2 summary + 15 ingestion = 36
        assert runner.register_handler.call_count == 36

    def test_register_all_handlers(self):
        mod = _census_import("__init__")
        poller = MagicMock()
        mod.register_all_handlers(poller)
        assert poller.register.call_count == 36

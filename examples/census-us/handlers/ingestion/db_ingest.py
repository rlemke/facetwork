"""Shared utility for upserting handler output data into MongoDB.

Reads GeoJSON, CSV, or JSON files produced by upstream handlers and
bulk-upserts them into ``handler_output`` / ``handler_output_meta``
collections.  The compound unique index on ``(dataset_key, feature_key)``
ensures re-runs replace data without creating duplicates.
"""

import csv
import json
import os
import time
from typing import Any

from pymongo import MongoClient, ReplaceOne
from pymongo.collection import Collection
from pymongo.database import Database


def get_mongo_db() -> Database:
    """Connect to MongoDB using the same env vars as the AFL runtime."""
    url = os.environ.get("AFL_MONGODB_URL", "mongodb://localhost:27017")
    db_name = os.environ.get("AFL_MONGODB_DATABASE", "afl")
    return MongoClient(url)[db_name]


class OutputStore:
    """Upserts handler output data into MongoDB."""

    BATCH_SIZE = 1000

    def __init__(self, db: Database) -> None:
        self._output: Collection = db.handler_output
        self._meta: Collection = db.handler_output_meta
        self._ensure_indexes()

    def _ensure_indexes(self) -> None:
        self._output.create_index(
            [("dataset_key", 1), ("feature_key", 1)],
            unique=True,
            name="output_upsert_key",
        )
        self._output.create_index(
            [("geometry", "2dsphere")],
            sparse=True,
            name="output_geo_2dsphere",
        )
        self._meta.create_index(
            "dataset_key",
            unique=True,
            name="meta_upsert_key",
        )

    # ------------------------------------------------------------------
    # GeoJSON ingestion
    # ------------------------------------------------------------------

    def ingest_geojson(
        self,
        path: str,
        dataset_key: str,
        feature_key_field: str,
        facet_name: str,
        data_type: str = "geojson_feature",
    ) -> int:
        """Read a GeoJSON FeatureCollection and bulk-upsert features.

        Returns the number of features processed.
        """
        with open(path) as f:
            data = json.load(f)

        features = data.get("features", [])
        now = int(time.time() * 1000)
        ops: list[ReplaceOne] = []

        for feat in features:
            props = feat.get("properties", {})
            feature_key = str(props.get(feature_key_field, ""))
            doc: dict[str, Any] = {
                "dataset_key": dataset_key,
                "feature_key": feature_key,
                "facet_name": facet_name,
                "data_type": data_type,
                "properties": props,
                "geometry": feat.get("geometry"),
                "imported_at": now,
            }
            ops.append(
                ReplaceOne(
                    {"dataset_key": dataset_key, "feature_key": feature_key},
                    doc,
                    upsert=True,
                )
            )
            if len(ops) >= self.BATCH_SIZE:
                self._output.bulk_write(ops, ordered=False)
                ops = []

        if ops:
            self._output.bulk_write(ops, ordered=False)

        self._update_meta(dataset_key, facet_name, len(features),
                          data_type, path, now)
        return len(features)

    # ------------------------------------------------------------------
    # CSV ingestion
    # ------------------------------------------------------------------

    def ingest_csv(
        self,
        path: str,
        dataset_key: str,
        feature_key_field: str,
        facet_name: str,
        data_type: str = "csv_record",
    ) -> int:
        """Read a CSV file and bulk-upsert rows.

        Returns the number of rows processed.
        """
        now = int(time.time() * 1000)
        ops: list[ReplaceOne] = []
        count = 0

        with open(path, newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                feature_key = str(row.get(feature_key_field, ""))
                doc: dict[str, Any] = {
                    "dataset_key": dataset_key,
                    "feature_key": feature_key,
                    "facet_name": facet_name,
                    "data_type": data_type,
                    "properties": dict(row),
                    "imported_at": now,
                }
                ops.append(
                    ReplaceOne(
                        {"dataset_key": dataset_key, "feature_key": feature_key},
                        doc,
                        upsert=True,
                    )
                )
                count += 1
                if len(ops) >= self.BATCH_SIZE:
                    self._output.bulk_write(ops, ordered=False)
                    ops = []

        if ops:
            self._output.bulk_write(ops, ordered=False)

        self._update_meta(dataset_key, facet_name, count, data_type, path, now)
        return count

    # ------------------------------------------------------------------
    # JSON ingestion (single document)
    # ------------------------------------------------------------------

    def ingest_json(
        self,
        path: str,
        dataset_key: str,
        key_field: str,
        facet_name: str,
        data_type: str = "json_object",
    ) -> int:
        """Read a JSON file and upsert a single document.

        Returns 1 on success.
        """
        with open(path) as f:
            data = json.load(f)

        now = int(time.time() * 1000)
        feature_key = str(data.get(key_field, dataset_key))
        doc: dict[str, Any] = {
            "dataset_key": dataset_key,
            "feature_key": feature_key,
            "facet_name": facet_name,
            "data_type": data_type,
            "properties": data,
            "imported_at": now,
        }
        self._output.replace_one(
            {"dataset_key": dataset_key, "feature_key": feature_key},
            doc,
            upsert=True,
        )
        self._update_meta(dataset_key, facet_name, 1, data_type, path, now)
        return 1

    # ------------------------------------------------------------------
    # Meta helper
    # ------------------------------------------------------------------

    def _update_meta(
        self,
        dataset_key: str,
        facet_name: str,
        record_count: int,
        data_type: str,
        source_path: str,
        imported_at: int,
    ) -> None:
        self._meta.replace_one(
            {"dataset_key": dataset_key},
            {
                "dataset_key": dataset_key,
                "facet_name": facet_name,
                "record_count": record_count,
                "data_type": data_type,
                "imported_at": imported_at,
                "source_path": source_path,
            },
            upsert=True,
        )

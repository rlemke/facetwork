"""ACS data extraction from Census Bureau API downloads.

Reads CSV data produced by the Census API downloader and extracts columns
for specific tables (e.g. B01003 for population, B19013 for income).
"""

import csv
import logging
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ACS table ID -> human-readable label and estimate column suffix
ACS_TABLES: dict[str, dict[str, str]] = {
    "B01003": {"label": "Total Population", "column": "B01003_001E"},
    "B19013": {"label": "Median Household Income", "column": "B19013_001E"},
    "B25001": {"label": "Housing Units", "column": "B25001_001E"},
    "B15003": {"label": "Educational Attainment", "column": "B15003_001E"},
    "B08301": {"label": "Means of Transportation", "column": "B08301_001E"},
}

_LOCAL_OUTPUT = os.environ.get("AFL_LOCAL_OUTPUT_DIR", "/tmp")
_OUTPUT_DIR = os.environ.get("AFL_CENSUS_OUTPUT_DIR",
                             os.path.join(_LOCAL_OUTPUT, "census-output"))


@dataclass
class ACSExtractionResult:
    """Result of an ACS table extraction."""
    table_id: str
    output_path: str
    record_count: int
    geography_level: str
    year: str
    extraction_date: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat()
    )


def extract_acs_table(csv_path: str, table_id: str, state_fips: str,
                      geo_level: str = "county",
                      year: str = "2023") -> ACSExtractionResult:
    """Extract a specific ACS table from a downloaded CSV file.

    The CSV is produced by the Census API downloader with columns:
    GEOID, NAME, B01003_001E, B19013_001E, etc.

    Args:
        csv_path: Path to downloaded ACS CSV file.
        table_id: ACS table ID (e.g. "B01003").
        state_fips: Two-digit state FIPS code.
        geo_level: Geography level (county, tract, etc.).
        year: Survey year.

    Returns:
        ACSExtractionResult with output path and record count.
    """
    table_info = ACS_TABLES.get(table_id)
    if table_info is None:
        raise ValueError(f"Unknown ACS table: {table_id}. "
                         f"Supported: {list(ACS_TABLES.keys())}")

    target_col = table_info["column"]
    output_dir = os.path.join(_OUTPUT_DIR, "acs", table_id.lower())
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    output_path = os.path.join(output_dir,
                               f"{state_fips}_{geo_level}_{table_id}.csv")

    records: list[dict[str, Any]] = []

    if os.path.exists(csv_path):
        try:
            with open(csv_path, newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    geoid = row.get("GEOID", "")
                    if not geoid.startswith(f"0500000US{state_fips}"):
                        continue
                    value = row.get(target_col, "")
                    if value:
                        records.append({
                            "GEOID": geoid,
                            target_col: value,
                            "NAME": row.get("NAME", ""),
                        })
        except (OSError, csv.Error) as exc:
            logger.warning("Failed to read ACS CSV %s: %s", csv_path, exc)

    # Write output CSV
    with open(output_path, "w", newline="") as f:
        fieldnames = ["GEOID", target_col, "NAME"]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(records)

    logger.info("Extracted %d records for %s (state=%s, level=%s)",
                len(records), table_id, state_fips, geo_level)

    return ACSExtractionResult(
        table_id=table_id,
        output_path=output_path,
        record_count=len(records),
        geography_level=geo_level,
        year=year,
    )

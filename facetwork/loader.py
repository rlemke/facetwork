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

"""AFL source loaders for different origin types.

Provides loading functionality for:
- File system sources
- MongoDB sources
- Maven artifacts
"""

from __future__ import annotations

import io
import urllib.request
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .source import (
    FileOrigin,
    MavenOrigin,
    MongoDBOrigin,
    SourceEntry,
)

if TYPE_CHECKING:
    from .config import FFLConfig


class SourceLoader:
    """Loads FFL source from various origins."""

    @staticmethod
    def load_file(path: str | Path, is_library: bool = False) -> SourceEntry:
        """Load source from a file.

        Args:
            path: Path to the FFL source file
            is_library: Whether this is a library source

        Returns:
            SourceEntry with file content and provenance

        Raises:
            FileNotFoundError: If the file doesn't exist
            IOError: If the file can't be read
        """
        file_path = Path(path)
        text = file_path.read_text()
        return SourceEntry(
            text=text,
            origin=FileOrigin(path=str(file_path)),
            is_library=is_library,
        )

    @staticmethod
    def load_mongodb(
        collection_id: str,
        display_name: str,
        is_library: bool = True,
        config: FFLConfig | None = None,
    ) -> SourceEntry:
        """Load source from MongoDB flows collection.

        Queries the flows collection for a document with the given UUID,
        then assembles source text from its sources array (filtering for AFL).

        Args:
            collection_id: MongoDB document UUID (flows collection)
            display_name: Human-readable name for provenance
            is_library: Whether this is a library source
            config: FFL configuration (uses default if not provided)

        Returns:
            SourceEntry with assembled content and provenance

        Raises:
            ValueError: If flow not found or contains no FFL sources
            ImportError: If pymongo is not installed
        """
        try:
            from pymongo import MongoClient
        except ImportError:
            raise ImportError(
                "pymongo is required for MongoDB source loading. Install with: pip install pymongo"
            ) from None

        from .config import load_config

        cfg = config if config is not None else load_config()
        client: Any = MongoClient(cfg.mongodb.url)
        db = client[cfg.mongodb.database]

        # Query flows collection by UUID
        flow = db.flows.find_one({"uuid": collection_id})
        if flow is None:
            raise ValueError(f"Flow not found: {collection_id}")

        # Assemble source text from sources array
        sources = flow.get("sources", [])
        afl_sources = [s.get("content", "") for s in sources if s.get("language") == "afl"]

        if not afl_sources:
            raise ValueError(f"Flow '{collection_id}' contains no FFL sources")

        text = "\n".join(afl_sources)
        client.close()

        return SourceEntry(
            text=text,
            origin=MongoDBOrigin(collection_id=collection_id, display_name=display_name),
            is_library=is_library,
        )

    @staticmethod
    def load_maven(
        group_id: str,
        artifact_id: str,
        version: str,
        classifier: str = "sources",
        is_library: bool = True,
        repository_url: str = "https://repo1.maven.org/maven2",
    ) -> SourceEntry:
        """Load source from Maven repository.

        Downloads the artifact JAR from Maven Central (or specified repo),
        extracts all .afl files from it, and assembles them into a single source.

        Args:
            group_id: Maven group ID (e.g., "com.example")
            artifact_id: Maven artifact ID (e.g., "my-lib")
            version: Maven version (e.g., "1.0.0")
            classifier: Classifier (default: "sources")
            is_library: Whether this is a library source
            repository_url: Maven repository URL

        Returns:
            SourceEntry with assembled content and provenance

        Raises:
            ValueError: If artifact not found or contains no FFL sources
            urllib.error.URLError: If download fails
        """
        # Build Maven Central URL
        group_path = group_id.replace(".", "/")
        jar_name = f"{artifact_id}-{version}"
        if classifier:
            jar_name += f"-{classifier}"
        url = f"{repository_url}/{group_path}/{artifact_id}/{version}/{jar_name}.jar"

        # Download JAR
        try:
            with urllib.request.urlopen(url, timeout=60) as response:
                jar_bytes = response.read()
        except urllib.error.HTTPError as e:
            coords = f"{group_id}:{artifact_id}:{version}"
            if classifier:
                coords += f":{classifier}"
            raise ValueError(f"Failed to download artifact '{coords}': HTTP {e.code}") from e

        # Extract FFL sources from JAR
        afl_sources: list[str] = []
        try:
            with zipfile.ZipFile(io.BytesIO(jar_bytes)) as zf:
                for name in sorted(zf.namelist()):  # sorted for deterministic order
                    if name.endswith(".ffl"):
                        content = zf.read(name).decode("utf-8")
                        afl_sources.append(content)
        except zipfile.BadZipFile as e:
            raise ValueError(f"Invalid JAR file from {url}") from e

        if not afl_sources:
            coords = f"{group_id}:{artifact_id}:{version}"
            if classifier:
                coords += f":{classifier}"
            raise ValueError(f"No .afl files found in artifact '{coords}'")

        text = "\n".join(afl_sources)

        return SourceEntry(
            text=text,
            origin=MavenOrigin(
                group_id=group_id,
                artifact_id=artifact_id,
                version=version,
                classifier=classifier,
            ),
            is_library=is_library,
        )

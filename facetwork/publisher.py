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

"""AFL source publisher for MongoDB-backed namespace sharing.

Publishes FFL source files to the ``afl_sources`` MongoDB collection
so that other compilations can resolve namespace dependencies automatically.
"""

from __future__ import annotations

import hashlib
import time
import uuid
from typing import TYPE_CHECKING

from .parser import FFLParser, ParseError
from .runtime.entities import PublishedSource

if TYPE_CHECKING:
    from .runtime.mongo_store import MongoStore


class PublishError(Exception):
    """Error during source publishing."""


class SourcePublisher:
    """Publishes FFL source files to MongoDB for namespace-based lookup."""

    def __init__(self, store: MongoStore) -> None:
        self._store = store
        self._parser = FFLParser()

    def publish(
        self,
        source_text: str,
        version: str = "latest",
        origin: str = "cli",
        force: bool = False,
    ) -> list[PublishedSource]:
        """Publish an FFL source file to MongoDB.

        Parses the source to extract namespace names, then creates a
        ``PublishedSource`` document for each namespace found. All
        documents share the same source_text (the full file content).

        Args:
            source_text: FFL source code to publish
            version: Version tag (default: "latest")
            origin: Origin identifier (e.g. "cli", "api")
            force: If True, overwrite existing entries at the same version

        Returns:
            List of PublishedSource documents created

        Raises:
            PublishError: If source cannot be parsed or has no namespaces
        """
        try:
            program = self._parser.parse(source_text, filename="<publish>")
        except ParseError as e:
            raise PublishError(f"Cannot parse source: {e}") from e

        if not program.namespaces:
            raise PublishError("Source contains no namespaces")

        checksum = hashlib.sha256(source_text.encode()).hexdigest()
        now_ms = int(time.time() * 1000)
        all_ns_names = [ns.name for ns in program.namespaces]

        published: list[PublishedSource] = []
        for ns in program.namespaces:
            if not force:
                existing = self._store.get_source_by_namespace(ns.name, version)
                if existing and existing.checksum != checksum:
                    raise PublishError(
                        f"Namespace '{ns.name}' already published at version '{version}' "
                        f"with different content. Use force=True to overwrite."
                    )

            doc = PublishedSource(
                uuid=str(uuid.uuid4()),
                namespace_name=ns.name,
                source_text=source_text,
                namespaces_defined=all_ns_names,
                version=version,
                published_at=now_ms,
                origin=origin,
                checksum=checksum,
            )
            self._store.save_published_source(doc)
            published.append(doc)

        return published

    def unpublish(self, namespace_name: str, version: str = "latest") -> bool:
        """Remove a published source by namespace name and version.

        Returns:
            True if a document was deleted, False if not found.
        """
        return self._store.delete_published_source(namespace_name, version)

    def list_published(self) -> list[PublishedSource]:
        """List all published sources."""
        return self._store.list_published_sources()

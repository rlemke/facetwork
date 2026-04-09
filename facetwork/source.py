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

"""AFL source input and provenance tracking.

This module provides data structures for compiler input with source
provenance tracking, supporting multiple source origins:
- File system
- MongoDB source collection
- Maven artifacts
"""

from dataclasses import dataclass, field


@dataclass
class FileOrigin:
    """Provenance for file-based source."""

    path: str

    def to_source_id(self) -> str:
        """Generate unique source ID."""
        return f"file://{self.path}"


@dataclass
class MongoDBOrigin:
    """Provenance for MongoDB-stored source."""

    collection_id: str
    display_name: str

    def to_source_id(self) -> str:
        """Generate unique source ID."""
        return f"mongodb://{self.collection_id}/{self.display_name}"


@dataclass
class MavenOrigin:
    """Provenance for Maven-sourced libraries."""

    group_id: str
    artifact_id: str
    version: str
    classifier: str = ""

    def to_source_id(self) -> str:
        """Generate unique source ID."""
        base = f"maven://{self.group_id}/{self.artifact_id}/{self.version}"
        if self.classifier:
            return f"{base}/{self.classifier}"
        return base


# Union type for all source origins
SourceOrigin = FileOrigin | MongoDBOrigin | MavenOrigin


@dataclass
class SourceEntry:
    """A single source input with provenance metadata."""

    text: str
    origin: SourceOrigin
    is_library: bool = False

    @property
    def source_id(self) -> str:
        """Get the unique source ID for this entry."""
        return self.origin.to_source_id()


@dataclass
class CompilerInput:
    """Input to the FFL compiler with primary and library sources.

    Attributes:
        primary_sources: List of primary source entries (agent code)
        library_sources: List of library/dependency sources
    """

    primary_sources: list[SourceEntry] = field(default_factory=list)
    library_sources: list[SourceEntry] = field(default_factory=list)

    @property
    def all_sources(self) -> list[SourceEntry]:
        """Get all sources (primary + library)."""
        return self.primary_sources + self.library_sources


@dataclass
class SourceRegistry:
    """Maps source_id to SourceOrigin for provenance lookup.

    Used by the emitter to include provenance metadata in JSON output.
    """

    sources: dict[str, SourceOrigin] = field(default_factory=dict)

    def register(self, source_id: str, origin: SourceOrigin) -> None:
        """Register a source origin."""
        self.sources[source_id] = origin

    def get(self, source_id: str) -> SourceOrigin | None:
        """Get origin for a source ID, or None if not found."""
        return self.sources.get(source_id)

    def register_entry(self, entry: SourceEntry) -> str:
        """Register a source entry and return its source_id."""
        source_id = entry.source_id
        self.sources[source_id] = entry.origin
        return source_id

    @classmethod
    def from_compiler_input(cls, compiler_input: CompilerInput) -> "SourceRegistry":
        """Create a registry from compiler input."""
        registry = cls()
        for entry in compiler_input.all_sources:
            registry.register_entry(entry)
        return registry

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

"""Persistence for the Claude workflow catalog.

``CatalogStore`` is the storage protocol the service depends on. Two
implementations:

- ``InMemoryCatalogStore`` — dict-backed, deep-copying (used in tests and any
  store-less context); enforces revision immutability by copying on the way in
  and out.
- ``MongoCatalogStore`` — backed by the ``claude_workflows`` and
  ``claude_workflow_revisions`` collections of a pymongo ``Database`` (the same
  ``_db`` a ``MongoStore`` holds).
"""

from __future__ import annotations

import copy
from dataclasses import asdict
from typing import Any, Protocol

from .entities import CatalogEntry, CatalogRevision, DependencyPin


class CatalogStore(Protocol):
    """Storage interface for catalog entries and their immutable revisions."""

    def get_entry(self, slug: str) -> CatalogEntry | None: ...
    def save_entry(self, entry: CatalogEntry) -> None: ...
    def list_entries(self) -> list[CatalogEntry]: ...
    def get_revision(self, revision_id: str) -> CatalogRevision | None: ...
    def get_revisions_for_slug(self, slug: str) -> list[CatalogRevision]: ...
    def get_revision_by_version(self, slug: str, version: int) -> CatalogRevision | None: ...
    def find_revision_by_hash(self, slug: str, content_hash: str) -> CatalogRevision | None: ...
    def save_revision(self, revision: CatalogRevision) -> None: ...


# ---------------------------------------------------------------------------
# Serialization helpers (shared by the Mongo store)
# ---------------------------------------------------------------------------


def _revision_to_doc(rev: CatalogRevision) -> dict:
    doc = asdict(rev)  # asdict recurses into DependencyPin
    return doc


def _doc_to_revision(doc: dict) -> CatalogRevision:
    deps = [DependencyPin(**d) for d in doc.get("depends_on", [])]
    return CatalogRevision(
        revision_id=doc["revision_id"],
        slug=doc["slug"],
        version=doc["version"],
        content_hash=doc["content_hash"],
        ffl_source=doc["ffl_source"],
        flow_id=doc["flow_id"],
        entry_workflow=doc["entry_workflow"],
        workflow_id=doc["workflow_id"],
        workflow_names=doc.get("workflow_names", []),
        param_schema=doc.get("param_schema", []),
        returns_schema=doc.get("returns_schema", []),
        facets_used=doc.get("facets_used", []),
        depends_on=deps,
        status=doc.get("status", "draft"),
        is_valid=doc.get("is_valid", True),
        warnings=doc.get("warnings", []),
        author=doc.get("author", "claude"),
        teams=doc.get("teams", []),
        note=doc.get("note", ""),
        summary=doc.get("summary", ""),
        created_at=doc.get("created_at", 0),
    )


def _entry_to_doc(entry: CatalogEntry) -> dict:
    return asdict(entry)


def _doc_to_entry(doc: dict) -> CatalogEntry:
    return CatalogEntry(
        slug=doc["slug"],
        kind=doc.get("kind", "workflow"),
        title=doc.get("title", ""),
        description=doc.get("description", ""),
        tags=doc.get("tags", []),
        teams=doc.get("teams", []),
        latest_version=doc.get("latest_version", 0),
        published_version=doc.get("published_version"),
        author=doc.get("author", "claude"),
        created_at=doc.get("created_at", 0),
        updated_at=doc.get("updated_at", 0),
    )


# ---------------------------------------------------------------------------
# In-memory implementation
# ---------------------------------------------------------------------------


class InMemoryCatalogStore:
    """Dict-backed catalog store. Deep-copies on read/write so callers cannot
    mutate stored revisions in place (immutability is a contract, not a hope)."""

    def __init__(self) -> None:
        self._entries: dict[str, CatalogEntry] = {}
        self._revisions: dict[str, CatalogRevision] = {}

    def get_entry(self, slug: str) -> CatalogEntry | None:
        e = self._entries.get(slug)
        return copy.deepcopy(e) if e else None

    def save_entry(self, entry: CatalogEntry) -> None:
        self._entries[entry.slug] = copy.deepcopy(entry)

    def list_entries(self) -> list[CatalogEntry]:
        return [copy.deepcopy(e) for e in self._entries.values()]

    def get_revision(self, revision_id: str) -> CatalogRevision | None:
        r = self._revisions.get(revision_id)
        return copy.deepcopy(r) if r else None

    def get_revisions_for_slug(self, slug: str) -> list[CatalogRevision]:
        revs = [r for r in self._revisions.values() if r.slug == slug]
        revs.sort(key=lambda r: r.version)
        return [copy.deepcopy(r) for r in revs]

    def get_revision_by_version(self, slug: str, version: int) -> CatalogRevision | None:
        for r in self._revisions.values():
            if r.slug == slug and r.version == version:
                return copy.deepcopy(r)
        return None

    def find_revision_by_hash(self, slug: str, content_hash: str) -> CatalogRevision | None:
        for r in self._revisions.values():
            if r.slug == slug and r.content_hash == content_hash:
                return copy.deepcopy(r)
        return None

    def save_revision(self, revision: CatalogRevision) -> None:
        self._revisions[revision.revision_id] = copy.deepcopy(revision)


# ---------------------------------------------------------------------------
# MongoDB implementation
# ---------------------------------------------------------------------------


class MongoCatalogStore:
    """Catalog store backed by two collections on a pymongo ``Database``.

    Pass a ``MongoStore``'s ``_db`` (or any pymongo ``Database``). Indexes are
    created lazily on first construction.
    """

    ENTRIES = "claude_workflows"
    REVISIONS = "claude_workflow_revisions"

    def __init__(self, db: Any) -> None:
        self._db = db
        self._db[self.ENTRIES].create_index("slug", unique=True)
        self._db[self.REVISIONS].create_index("revision_id", unique=True)
        self._db[self.REVISIONS].create_index([("slug", 1), ("version", 1)], unique=True)
        self._db[self.REVISIONS].create_index([("slug", 1), ("content_hash", 1)])

    def get_entry(self, slug: str) -> CatalogEntry | None:
        doc = self._db[self.ENTRIES].find_one({"slug": slug})
        return _doc_to_entry(doc) if doc else None

    def save_entry(self, entry: CatalogEntry) -> None:
        self._db[self.ENTRIES].replace_one(
            {"slug": entry.slug}, _entry_to_doc(entry), upsert=True
        )

    def list_entries(self) -> list[CatalogEntry]:
        return [_doc_to_entry(d) for d in self._db[self.ENTRIES].find()]

    def get_revision(self, revision_id: str) -> CatalogRevision | None:
        doc = self._db[self.REVISIONS].find_one({"revision_id": revision_id})
        return _doc_to_revision(doc) if doc else None

    def get_revisions_for_slug(self, slug: str) -> list[CatalogRevision]:
        docs = self._db[self.REVISIONS].find({"slug": slug}).sort("version", 1)
        return [_doc_to_revision(d) for d in docs]

    def get_revision_by_version(self, slug: str, version: int) -> CatalogRevision | None:
        doc = self._db[self.REVISIONS].find_one({"slug": slug, "version": version})
        return _doc_to_revision(doc) if doc else None

    def find_revision_by_hash(self, slug: str, content_hash: str) -> CatalogRevision | None:
        doc = self._db[self.REVISIONS].find_one({"slug": slug, "content_hash": content_hash})
        return _doc_to_revision(doc) if doc else None

    def save_revision(self, revision: CatalogRevision) -> None:
        self._db[self.REVISIONS].replace_one(
            {"revision_id": revision.revision_id}, _revision_to_doc(revision), upsert=True
        )

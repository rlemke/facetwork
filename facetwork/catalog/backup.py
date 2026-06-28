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

"""Backup, restore, and file-import for the Claude workflow catalog.

The catalog is self-describing — each revision carries its FFL source, content
hash, version, status, and pinned dependencies — so a backup is just the
entries + revisions as JSON. The materialized ``FlowDefinition``s are NOT backed
up; restore re-materializes them by recompiling the FFL (the FFL is the source
of truth), which keeps backups small, readable, and git-friendly.

- ``export_to_file`` / ``export`` — dump the catalog to a JSON file/dict.
- ``restore_from_file`` / ``restore`` — load a backup, preserving revision
  identity (id, version, content_hash, status, pins) and rebuilding runnable
  flows in the target database.
- ``import_files`` / ``import_ffl`` — register file-based ``.ffl`` workflows
  into the catalog so Claude can discover and run them.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .store import _doc_to_entry, _doc_to_revision

BACKUP_FORMAT = "facetwork-catalog-backup"
BACKUP_VERSION = 1


# ---------------------------------------------------------------------------
# Backup
# ---------------------------------------------------------------------------


def export(service: Any) -> dict:
    """Return a JSON-serializable snapshot of the catalog (entries + revisions)."""
    catalog = service._catalog
    entries = catalog.list_entries()
    revisions: list = []
    for e in entries:
        revisions.extend(catalog.get_revisions_for_slug(e.slug))
    return {
        "format": BACKUP_FORMAT,
        "version": BACKUP_VERSION,
        "exported_at": int(time.time() * 1000),
        "entries": [asdict(e) for e in entries],
        "revisions": [asdict(r) for r in revisions],  # asdict recurses DependencyPin
    }


def export_to_file(service: Any, path: str | Path) -> dict:
    """Write ``export(service)`` to ``path`` as indented JSON. Returns a summary."""
    from facetwork.runtime.storage import get_fs

    data = export(service)
    get_fs().write_text(str(path), json.dumps(data, indent=2, default=str))
    return {
        "path": str(path),
        "entries": len(data["entries"]),
        "revisions": len(data["revisions"]),
    }


# ---------------------------------------------------------------------------
# Restore
# ---------------------------------------------------------------------------


def restore(data: dict, service: Any, *, rematerialize: bool = True) -> dict:
    """Load a backup into ``service``'s store.

    Writes entries + revisions verbatim (preserving revision_id, version,
    content_hash, status, and pinned deps), then — unless ``rematerialize`` is
    false — recompiles each revision to rebuild a runnable ``FlowDefinition`` in
    the target database. Idempotent. A revision whose FFL no longer compiles is
    restored as a non-runnable record (flow_id="" , is_valid=False) and reported.
    """
    fmt = data.get("format")
    if fmt != BACKUP_FORMAT:
        raise ValueError(f"not a catalog backup (format={fmt!r})")
    catalog = service._catalog

    for ed in data.get("entries", []):
        catalog.save_entry(_doc_to_entry(ed))
    for rd in data.get("revisions", []):
        catalog.save_revision(_doc_to_revision(rd))

    result: dict = {
        "entries": len(data.get("entries", [])),
        "revisions": len(data.get("revisions", [])),
        "rematerialized": 0,
        "failed": [],
    }
    if not rematerialize:
        return result

    # All revisions are now in the store, so pinned deps resolve regardless of
    # order; rebuild dependency-free revisions (libraries) first so thin
    # per-workflow entries reuse the library's freshly-built shared flow.
    revision_docs = sorted(data.get("revisions", []), key=lambda rd: len(rd.get("depends_on", [])))
    for rd in revision_docs:
        rev = catalog.get_revision(rd["revision_id"])
        rebuilt = service.rematerialize(rev)
        catalog.save_revision(rebuilt)
        if rebuilt.flow_id:
            result["rematerialized"] += 1
        else:
            result["failed"].append(
                {"slug": rebuilt.slug, "version": rebuilt.version, "warnings": rebuilt.warnings}
            )
    return result


def restore_from_file(path: str | Path, service: Any, *, rematerialize: bool = True) -> dict:
    """Read a backup JSON file and ``restore`` it."""
    from facetwork.runtime.storage import get_fs

    data = get_fs().read_json(str(path))
    return restore(data, service, rematerialize=rematerialize)


# ---------------------------------------------------------------------------
# Import file-based FFL workflows
# ---------------------------------------------------------------------------


def import_ffl(service: Any, ffl_source: str, slug: str, *, publish: bool = False, **meta: Any):
    """Register one FFL string in the catalog (a thin wrapper over ``save`` that
    optionally publishes). ``meta`` accepts kind/title/description/tags/
    depends_on/entry_workflow/author/note."""
    res = service.save(slug, ffl_source=ffl_source, **meta)
    if publish and res.ok and res.is_valid:
        service.publish(slug, res.version)
        res.status = "published"
    return res


def import_package(
    service: Any,
    *,
    name: str | None = None,
    ffl_dir: str | Path | None = None,
    lib_slug: str | None = None,
    also: list[str] | None = None,
    prefix: str = "",
    tags: list[str] | None = None,
    publish: bool = True,
    repo_root: str | Path | None = None,
) -> list[tuple[str, Any]]:
    """Import a whole multi-file FFL package as ONE shared library plus ONE
    thin catalog entry per workflow it defines.

    A package (e.g. ``osm-geocoder``) is dozens of ``.ffl`` files that only
    compile together (cross-file ``use``). Importing each file standalone fails;
    materializing the merged program once per workflow would store the (often
    multi-MB) compiled program N times. Instead this:

    1. Merges every ``.ffl`` in the package (resolving all ``use`` deps) into one
       ``kind="library"`` entry whose single ``FlowDefinition`` holds *all* the
       workflows — the one shared flow.
    2. Creates one ``kind="workflow"`` entry per workflow, each with empty own
       FFL and a single pinned dependency on the library, pointing at its own
       workflow within the shared flow. ``rematerialize`` keeps these bound to
       the library's flow, so backup/restore stays at one flow, not N.

    ``name`` resolves a registered domain (or teaching example) package;
    ``ffl_dir`` imports a directory of ``.ffl`` instead; ``also`` merges extra
    packages' FFL for cross-package ``use`` deps. Returns ``[("(library) <slug>",
    SaveResult), (workflow_slug, CatalogRevision), ...]``.
    """
    import hashlib

    from facetwork._pkg_discovery import collect_ffl_files
    from facetwork.domains import get_domain
    from facetwork.examples import get_example
    from facetwork.runtime.types import generate_id

    def _resolve(n):
        return get_domain(n, repo_root) or get_example(n, repo_root)

    from .entities import (
        KIND_LIBRARY,
        KIND_WORKFLOW,
        STATUS_DRAFT,
        STATUS_PUBLISHED,
        CatalogEntry,
        CatalogRevision,
        DependencyPin,
    )
    from .service import CatalogError, _param_schema, _returns_schema

    # 1. Gather all FFL sources (the package + any cross-package merges).
    sources: list[str] = []
    for n in ([name] if name else []) + list(also or []):
        pkg = _resolve(n)
        if pkg is None:
            raise CatalogError(f"package not discoverable as a facetwork domain or example: {n!r}")
        for f in collect_ffl_files(pkg):
            sources.append(Path(f).read_text())
    if ffl_dir:
        for f in sorted(Path(ffl_dir).rglob("*.ffl")):
            sources.append(f.read_text())
    if not sources:
        raise CatalogError("no FFL sources found to import")
    combined = "\n\n".join(sources)

    lib = lib_slug or name or (Path(ffl_dir).name if ffl_dir else "package")
    tags = list(tags or [])

    # 2. Library entry — its single flow holds every workflow (the shared flow).
    libres = service.save(
        lib,
        kind=KIND_LIBRARY,
        ffl_source=combined,
        title=f"{lib} (package FFL)",
        tags=tags,
        author="import",
        note=f"package import: {name or ffl_dir}",
    )
    if not libres.ok:
        raise CatalogError(f"library compile failed for {lib}: {libres.error}")
    if publish and libres.is_valid:
        service.publish(lib, libres.version)
    lib_rev = service._catalog.get_revision_by_version(lib, libres.version)

    # 3. One thin entry per workflow, all sharing the library's flow.
    wf_defs = sorted(service._flows.get_workflows_by_flow(lib_rev.flow_id), key=lambda w: w.name)
    flow = service._flows.get_flow(lib_rev.flow_id)
    program = flow.compiled_ast if flow else {}
    all_names = [w.name for w in wf_defs]
    lib_pin = DependencyPin(
        slug=lib,
        revision_id=lib_rev.revision_id,
        version=lib_rev.version,
        content_hash=lib_rev.content_hash,
    )
    status = STATUS_PUBLISHED if (publish and lib_rev.is_valid) else STATUS_DRAFT
    now = int(time.time() * 1000)

    results: list[tuple[str, Any]] = [(f"(library) {lib}", libres)]
    for w in wf_defs:
        wf_name = w.name
        slug = f"{prefix}{wf_name}" if prefix else wf_name
        wf_ast = service._find_wf(program, wf_name)
        content_hash = (
            "sha256:" + hashlib.sha256(f"{wf_name}@{lib_rev.content_hash}".encode()).hexdigest()
        )
        # Idempotent re-import: reuse the existing v1 revision_id for this slug.
        existing = service._catalog.get_revision_by_version(slug, 1)
        rev = CatalogRevision(
            revision_id=existing.revision_id if existing else generate_id(),
            slug=slug,
            version=1,
            content_hash=content_hash,
            ffl_source="",  # thin: the body lives in the pinned library
            flow_id=lib_rev.flow_id,
            entry_workflow=wf_name,
            workflow_id=w.uuid,
            workflow_names=all_names,
            param_schema=_param_schema(wf_ast) if wf_ast else [],
            returns_schema=_returns_schema(wf_ast) if wf_ast else [],
            facets_used=[],
            depends_on=[lib_pin],
            status=status,
            is_valid=lib_rev.is_valid,
            author="import",
            note=f"from the {lib} package",
            created_at=now,
        )
        entry = service._catalog.get_entry(slug) or CatalogEntry(slug=slug, created_at=now)
        entry.kind = KIND_WORKFLOW
        entry.title = wf_name
        entry.tags = sorted(set(tags) | ({name} if name else set()))
        entry.latest_version = max(entry.latest_version, 1)
        if status == STATUS_PUBLISHED:
            entry.published_version = 1
        entry.author = "import"
        entry.updated_at = now
        service._catalog.save_entry(entry)
        service._catalog.save_revision(rev)
        results.append((slug, rev))
    return results


def import_files(
    service: Any,
    paths: list[str | Path],
    *,
    slug: str | None = None,
    publish: bool = False,
    **meta: Any,
) -> list[tuple[str, Any]]:
    """Import file-based ``.ffl`` workflows into the catalog.

    Each file becomes one catalog entry; directories are searched recursively
    for ``*.ffl``. The slug defaults to the file stem (``_`` → ``-``); an
    explicit ``--slug`` is only valid for a single file. Returns
    ``[(path, SaveResult), ...]``.
    """
    files: list[Path] = []
    for p in paths:
        pp = Path(p)
        if pp.is_dir():
            files.extend(sorted(pp.rglob("*.ffl")))
        elif pp.exists():
            files.append(pp)
        else:
            raise FileNotFoundError(str(pp))
    if slug and len(files) != 1:
        raise ValueError("--slug is only valid with a single .ffl file")

    out: list[tuple[str, Any]] = []
    for f in files:
        file_meta = dict(meta)
        file_meta.setdefault("title", f.stem)
        s = slug or f.stem.replace("_", "-")
        res = import_ffl(service, f.read_text(), s, publish=publish, **file_meta)
        out.append((str(f), res))
    return out

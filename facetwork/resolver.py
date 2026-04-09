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

"""Automatic dependency resolution for FFL sources.

Provides filesystem scanning and MongoDB-backed namespace lookup
to automatically discover and load missing namespace dependencies.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from .ast import (
    AndThenBlock,
    CallExpr,
    MixinSig,
    Program,
    PromptBlock,
)
from .source import CompilerInput, FileOrigin, SourceEntry, SourceRegistry

if TYPE_CHECKING:
    from .config import MongoDBConfig
    from .runtime.mongo_store import MongoStore

logger = logging.getLogger(__name__)

_MAX_RESOLVE_ITERATIONS = 100


def _namespace_candidates_from_qualified(name: str) -> list[str]:
    """Extract candidate namespace names from a qualified call expression.

    For ``"a.b.c.Facet"``, returns ``["a.b.c", "a.b", "a"]`` — longest
    prefix first.  Simple unqualified names (no dots) return an empty list.
    """
    parts = name.split(".")
    if len(parts) <= 1:
        return []
    # The last component is the facet name; everything before is a potential
    # namespace prefix (progressively shorter).
    candidates: list[str] = []
    for i in range(len(parts) - 1, 0, -1):
        candidates.append(".".join(parts[:i]))
    return candidates


def _collect_calls_from_block(block: AndThenBlock) -> list[CallExpr]:
    """Recursively collect all CallExpr nodes from an andThen block."""
    calls: list[CallExpr] = []
    if block.block is None:
        return calls
    for step in block.block.steps:
        calls.append(step.call)
        for mixin in step.call.mixins:
            calls.append(CallExpr(name=mixin.name, args=mixin.args))
        if step.body is not None:
            calls.extend(_collect_calls_from_block(step.body))
    for ys in block.block.yield_stmts:
        calls.append(ys.call)
        for mixin in ys.call.mixins:
            calls.append(CallExpr(name=mixin.name, args=mixin.args))
    return calls


def _collect_qualified_namespace_refs(program: Program) -> set[str]:
    """Scan all call expressions in *program* for qualified namespace references.

    Returns a set of candidate namespace names extracted from dotted call names
    (e.g., ``osm.ops.CacheRegion`` → ``osm.ops``).
    """
    candidates: set[str] = set()

    def _process_calls(calls: list[CallExpr]) -> None:
        for call in calls:
            for cand in _namespace_candidates_from_qualified(call.name):
                candidates.add(cand)

    def _process_body(body: list[AndThenBlock] | AndThenBlock | PromptBlock | None) -> None:
        if body is None or isinstance(body, PromptBlock):
            return
        blocks = body if isinstance(body, list) else [body]
        for block in blocks:
            _process_calls(_collect_calls_from_block(block))

    def _process_mixins(mixins: list[MixinSig]) -> None:
        for m in mixins:
            for cand in _namespace_candidates_from_qualified(m.name):
                candidates.add(cand)

    for ns in program.namespaces:
        for facet in ns.facets:
            _process_body(facet.body)
            _process_mixins(facet.sig.mixins)
        for ef in ns.event_facets:
            _process_body(ef.body)
            _process_mixins(ef.sig.mixins)
        for wf in ns.workflows:
            _process_body(wf.body)
            _process_mixins(wf.sig.mixins)
        for imp in ns.implicits:
            for cand in _namespace_candidates_from_qualified(imp.call.name):
                candidates.add(cand)

    # Also check top-level declarations (outside namespaces)
    for facet in program.facets:
        _process_body(facet.body)
        _process_mixins(facet.sig.mixins)
    for ef in program.event_facets:
        _process_body(ef.body)
        _process_mixins(ef.sig.mixins)
    for wf in program.workflows:
        _process_body(wf.body)
        _process_mixins(wf.sig.mixins)

    return candidates


class NamespaceIndex:
    """Filesystem scanner that maps namespace names to FFL source files.

    Lazily scans directories for ``.afl`` files, parses each to extract
    namespace declarations, and builds a lookup table.
    """

    def __init__(self, search_paths: list[Path]) -> None:
        self._search_paths = search_paths
        self._index: dict[str, Path] | None = None

    def _build_index(self) -> dict[str, Path]:
        """Walk all search paths and parse ``.afl`` files for namespace names."""
        from .parser import FFLParser, ParseError

        index: dict[str, Path] = {}
        parser = FFLParser()

        seen_files: set[Path] = set()
        for search_dir in self._search_paths:
            if not search_dir.is_dir():
                logger.debug("Skipping non-directory search path: %s", search_dir)
                continue
            for afl_file in sorted(search_dir.glob("**/*.ffl")):
                resolved = afl_file.resolve()
                if resolved in seen_files:
                    continue
                seen_files.add(resolved)
                try:
                    program = parser.parse(
                        afl_file.read_text(),
                        filename=str(afl_file),
                    )
                    for ns in program.namespaces:
                        if ns.name in index and index[ns.name] != resolved:
                            logger.warning(
                                "Duplicate namespace '%s' found in %s and %s",
                                ns.name,
                                index[ns.name],
                                resolved,
                            )
                        index[ns.name] = resolved
                except ParseError as e:
                    logger.debug("Skipping unparseable file %s: %s", afl_file, e)
        return index

    def find_namespace(self, name: str) -> Path | None:
        """Find the file that defines the given namespace.

        Returns:
            Path to the ``.afl`` file, or ``None`` if not found.
        """
        if self._index is None:
            self._index = self._build_index()
        return self._index.get(name)

    def all_namespaces(self) -> dict[str, Path]:
        """Return the full namespace → path mapping."""
        if self._index is None:
            self._index = self._build_index()
        return dict(self._index)


class MongoDBNamespaceResolver:
    """Resolves namespace names to source text via MongoDB ``afl_sources`` collection."""

    def __init__(self, config: MongoDBConfig) -> None:
        self._config = config
        self._store: MongoStore | None = None

    def _get_store(self) -> MongoStore:
        """Lazily create MongoStore."""
        if self._store is None:
            from .runtime.mongo_store import MongoStore

            self._store = MongoStore.from_config(self._config, create_indexes=False)
        return self._store

    def find_namespace(self, name: str) -> str | None:
        """Look up a namespace in MongoDB.

        Returns:
            Source text if found, or ``None``.
        """
        try:
            store = self._get_store()
            source = store.get_source_by_namespace(name)
            return source.source_text if source else None
        except Exception as e:
            logger.debug("MongoDB namespace lookup failed for '%s': %s", name, e)
            return None

    def batch_find(self, names: set[str]) -> dict[str, str]:
        """Batch-fetch multiple namespaces from MongoDB.

        Returns:
            Dict mapping namespace_name → source_text for found namespaces.
        """
        if not names:
            return {}
        try:
            store = self._get_store()
            sources = store.get_sources_by_namespaces(names)
            return {name: ps.source_text for name, ps in sources.items()}
        except Exception as e:
            logger.debug("MongoDB batch namespace lookup failed: %s", e)
            return {}


class DependencyResolver:
    """Iterative fixpoint resolver for FFL namespace dependencies.

    Given a parsed ``Program``, finds missing namespaces (referenced
    via ``use`` statements but not yet defined), loads them from the
    filesystem or MongoDB, parses them, and merges into the program.
    Repeats until no new namespaces are discovered.
    """

    def __init__(
        self,
        filesystem_index: NamespaceIndex | None = None,
        mongodb_resolver: MongoDBNamespaceResolver | None = None,
    ) -> None:
        self._fs_index = filesystem_index
        self._mongo_resolver = mongodb_resolver
        self._loaded_sources: set[str] = set()

    def resolve(
        self,
        program: Program,
        registry: SourceRegistry,
        compiler_input: CompilerInput,
    ) -> tuple[Program, SourceRegistry, CompilerInput]:
        """Resolve all missing namespace dependencies.

        Returns:
            Updated (program, registry, compiler_input) tuple with
            all discovered dependencies merged in.
        """
        from .parser import FFLParser, ParseError

        parser = FFLParser()

        for iteration in range(_MAX_RESOLVE_ITERATIONS):
            defined = {ns.name for ns in program.namespaces}

            # Collect needed namespaces from both `use` statements AND
            # qualified call expressions (e.g. osm.ops.CacheRegion).
            needed: set[str] = set()
            for ns in program.namespaces:
                for use in ns.uses:
                    needed.add(use.name)

            # Extract namespace candidates from qualified call names
            qualified_refs = _collect_qualified_namespace_refs(program)
            # Only keep candidates that exist in the filesystem/mongo index
            # (otherwise we'd try to resolve every possible prefix).
            if self._fs_index is not None:
                all_known = set(self._fs_index.all_namespaces().keys())
                needed.update(qualified_refs & all_known)
            else:
                # Without a filesystem index, add all candidates — the
                # MongoDB resolver will filter non-existent ones.
                needed.update(qualified_refs)

            missing = needed - defined
            if not missing:
                logger.debug("Dependency resolution complete after %d iteration(s)", iteration + 1)
                return program, registry, compiler_input

            logger.debug("Iteration %d: missing namespaces: %s", iteration + 1, missing)

            new_programs: list[Program] = []
            resolved_any = False

            # Try filesystem first
            if self._fs_index is not None:
                for name in list(missing):
                    file_path = self._fs_index.find_namespace(name)
                    if file_path is None:
                        continue
                    source_key = f"file://{file_path.resolve()}"
                    if source_key in self._loaded_sources:
                        continue
                    self._loaded_sources.add(source_key)

                    try:
                        text = file_path.read_text()
                        entry = SourceEntry(
                            text=text,
                            origin=FileOrigin(path=str(file_path)),
                            is_library=True,
                        )
                        compiler_input.library_sources.append(entry)
                        registry.register_entry(entry)

                        sub_program = parser.parse(
                            text,
                            filename=str(file_path),
                            source_id=entry.source_id,
                        )
                        new_programs.append(sub_program)
                        resolved_any = True
                        logger.debug("Resolved '%s' from filesystem: %s", name, file_path)
                    except (ParseError, OSError) as e:
                        logger.warning("Failed to load '%s' from %s: %s", name, file_path, e)

            # Then try MongoDB for still-missing namespaces
            if self._mongo_resolver is not None:
                # Recalculate what's still missing after filesystem resolution
                newly_defined = {ns.name for p in new_programs for ns in p.namespaces}
                still_missing = missing - newly_defined - defined
                if still_missing:
                    mongo_results = self._mongo_resolver.batch_find(still_missing)
                    for name, source_text in mongo_results.items():
                        source_key = f"mongodb://{name}"
                        if source_key in self._loaded_sources:
                            continue
                        self._loaded_sources.add(source_key)

                        try:
                            from .source import MongoDBOrigin

                            entry = SourceEntry(
                                text=source_text,
                                origin=MongoDBOrigin(
                                    collection_id=name,
                                    display_name=name,
                                ),
                                is_library=True,
                            )
                            compiler_input.library_sources.append(entry)
                            registry.register_entry(entry)

                            sub_program = parser.parse(
                                source_text,
                                filename=f"mongodb://{name}",
                                source_id=entry.source_id,
                            )
                            new_programs.append(sub_program)
                            resolved_any = True
                            logger.debug("Resolved '%s' from MongoDB", name)
                        except (ParseError, Exception) as e:
                            logger.warning("Failed to parse MongoDB source for '%s': %s", name, e)

            if not resolved_any:
                logger.debug(
                    "No new namespaces resolved; remaining missing: %s",
                    missing - {ns.name for p in new_programs for ns in p.namespaces},
                )
                return program, registry, compiler_input

            # Merge new programs into the main program
            for p in new_programs:
                program.namespaces.extend(p.namespaces)
                program.facets.extend(p.facets)
                program.event_facets.extend(p.event_facets)
                program.workflows.extend(p.workflows)
                program.implicits.extend(p.implicits)
                program.schemas.extend(p.schemas)

        logger.warning("Dependency resolution hit max iterations (%d)", _MAX_RESOLVE_ITERATIONS)
        return program, registry, compiler_input

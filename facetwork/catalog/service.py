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

"""Catalog service — the logic for storing, versioning, and running Claude
workflows. Storage-agnostic: it depends on a ``CatalogStore`` (catalog +
revisions) and a ``flow_store`` (a ``PersistenceAPI`` such as ``MongoStore`` or
``MemoryStore``) that holds the materialized ``FlowDefinition``/``WorkflowDefinition``
rows and the bootstrap task that runs them.
"""

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass, field
from typing import Any

from .entities import (
    KIND_LIBRARY,
    KIND_WORKFLOW,
    STATUS_DRAFT,
    STATUS_PUBLISHED,
    CatalogEntry,
    CatalogRevision,
    DependencyPin,
)
from .store import CatalogStore


class CatalogError(Exception):
    """A catalog operation could not be completed."""


class CatalogRunBlocked(CatalogError):
    """A run was refused by the review gate (revision not published)."""


@dataclass
class SaveResult:
    """Outcome of ``CatalogService.save``."""

    ok: bool
    slug: str
    version: int | None = None
    revision_id: str | None = None
    flow_id: str | None = None
    deduped: bool = False  # identical content already existed; no new version
    is_valid: bool = True
    status: str = STATUS_DRAFT
    warnings: list[str] = field(default_factory=list)
    error: str | None = None


def _now_ms() -> int:
    return int(time.time() * 1000)


class CatalogService:
    """Store, version, discover, and run Claude-authored FFL workflows."""

    def __init__(self, catalog: CatalogStore, flow_store: Any) -> None:
        self._catalog = catalog
        self._flows = flow_store

    # =====================================================================
    # save — validate, merge pinned deps, compile, content-hash, version
    # =====================================================================

    def save(
        self,
        slug: str,
        *,
        ffl_source: str,
        kind: str = KIND_WORKFLOW,
        title: str = "",
        description: str = "",
        tags: list[str] | None = None,
        depends_on: list[dict] | None = None,
        entry_workflow: str | None = None,
        author: str = "claude",
        note: str = "",
        summary: str = "",
    ) -> SaveResult:
        """Create (or dedup to) an immutable revision of ``slug``.

        Resolves and pins the requested library dependencies, hashes the FFL +
        pins, and — if that exact content does not already exist — compiles the
        merged program, materializes a ``FlowDefinition`` + ``WorkflowDefinition``s,
        and records a new draft revision (version = previous + 1). Identical
        content returns the existing revision without bumping the version.
        """
        tags = tags or []
        try:
            direct_pins, dep_sources = self._resolve_deps(depends_on or [])
        except CatalogError as e:
            return SaveResult(ok=False, slug=slug, error=str(e))

        content_hash = self._content_hash(ffl_source, direct_pins)

        existing = self._catalog.find_revision_by_hash(slug, content_hash)
        if existing is not None:
            # Same body + same pinned deps — reuse it; refresh mutable metadata.
            self._upsert_entry(slug, kind, title, description, tags, author, existing.version)
            # The summary is descriptive (not part of the hash); let a re-save
            # with a better narrative update it without churning the version.
            if summary and existing.summary != summary:
                existing.summary = summary
                self._catalog.save_revision(existing)
            return SaveResult(
                ok=True,
                slug=slug,
                version=existing.version,
                revision_id=existing.revision_id,
                flow_id=existing.flow_id,
                deduped=True,
                is_valid=existing.is_valid,
                status=existing.status,
                warnings=existing.warnings,
            )

        # Compile the merged program (deepest deps first, then this source).
        try:
            program_dict, combined, is_valid, warnings = self._compile(dep_sources + [ffl_source])
        except Exception as e:  # parse error — cannot build an AST at all
            return SaveResult(ok=False, slug=slug, is_valid=False, error=f"parse error: {e}")

        from facetwork._pkg_discovery import _collect_workflow_names

        workflow_names = _collect_workflow_names(program_dict)
        entry_name, perr = self._resolve_entry(kind, workflow_names, entry_workflow)
        if perr:
            return SaveResult(ok=False, slug=slug, error=perr)

        prev = self._catalog.get_entry(slug)
        version = (prev.latest_version if prev else 0) + 1

        flow_id, workflow_id = self._materialize(
            slug, version, combined, program_dict, workflow_names, entry_name
        )

        wf_ast = self._find_wf(program_dict, entry_name) if entry_name else None
        from facetwork.capabilities import author_and_teams
        from facetwork.runtime.types import generate_id

        # Ownership from the entry workflow's `with Author(email=…)` /
        # `with Teams(names=[…])` mixins. The FFL-declared author wins over the
        # caller-supplied default.
        mixin_author, mixin_teams = author_and_teams(wf_ast.get("mixins")) if wf_ast else ("", [])
        effective_author = mixin_author or author

        rev = CatalogRevision(
            revision_id=generate_id(),
            slug=slug,
            version=version,
            content_hash=content_hash,
            ffl_source=ffl_source,
            flow_id=flow_id,
            entry_workflow=entry_name or "",
            workflow_id=workflow_id,
            workflow_names=workflow_names,
            param_schema=_param_schema(wf_ast) if wf_ast else [],
            returns_schema=_returns_schema(wf_ast) if wf_ast else [],
            facets_used=_collect_call_targets(program_dict),
            depends_on=direct_pins,
            status=STATUS_DRAFT,
            is_valid=is_valid,
            warnings=warnings,
            author=effective_author,
            teams=mixin_teams,
            note=note,
            summary=summary,
            created_at=_now_ms(),
        )
        self._catalog.save_revision(rev)
        self._upsert_entry(
            slug, kind, title, description, tags, effective_author, version, teams=mixin_teams
        )

        return SaveResult(
            ok=True,
            slug=slug,
            version=version,
            revision_id=rev.revision_id,
            flow_id=flow_id,
            is_valid=is_valid,
            status=STATUS_DRAFT,
            warnings=warnings,
        )

    # =====================================================================
    # publish — the review gate
    # =====================================================================

    def publish(self, slug: str, version: int | None = None) -> CatalogRevision:
        """Mark a revision published so it may run unattended. Refuses an
        invalid revision."""
        rev = self._require_revision(slug, version)
        if not rev.is_valid:
            raise CatalogError(
                f"{slug} v{rev.version} failed validation and cannot be published: "
                f"{'; '.join(rev.warnings) or 'invalid FFL'}"
            )
        rev.status = STATUS_PUBLISHED
        self._catalog.save_revision(rev)
        entry = self._catalog.get_entry(slug)
        if entry is not None:
            entry.published_version = max(entry.published_version or 0, rev.version)
            entry.updated_at = _now_ms()
            self._catalog.save_entry(entry)
        return rev

    # =====================================================================
    # search / get
    # =====================================================================

    def search(
        self,
        query: str = "",
        *,
        tags: list[str] | None = None,
        facet: str | None = None,
        kind: str | None = None,
        include_drafts: bool = True,
    ) -> list[dict]:
        """Rank catalog entries against a free-text query + filters. Returns
        lightweight summaries (slug, title, description, tags, versions, param
        schema of the resolved revision) so Claude can decide reuse vs. author."""
        q = query.lower().strip()
        want_tags = {t.lower() for t in (tags or [])}
        out: list[tuple[int, dict]] = []
        for entry in self._catalog.list_entries():
            if kind and entry.kind != kind:
                continue
            rev = self._resolve_revision(
                entry.slug, None, prefer_published=not include_drafts
            )
            if rev is None:
                continue
            if facet and facet not in rev.facets_used:
                continue
            if want_tags and not want_tags.issubset({t.lower() for t in entry.tags}):
                continue
            score = self._score(q, entry, rev)
            if q and score == 0:
                continue
            out.append((score, self._summary(entry, rev)))
        out.sort(key=lambda x: (-x[0], x[1]["slug"]))
        return [s for _, s in out]

    def match(
        self,
        request: str,
        *,
        kind: str | None = None,
        include_drafts: bool = True,
        limit: int = 5,
    ) -> dict:
        """Reuse-first matching: given a natural-language *request*, find the
        catalog workflow that already satisfies that request *family* — matched
        primarily by **intent** (the revision's authoring ``summary``), not just
        keywords.

        Returns ``{request, terms, verdict, best, candidates, count}`` where
        ``verdict`` is ``reuse`` (a strong match — fill ``best.param_schema`` and
        run via :meth:`run` instead of authoring), ``review`` (candidates exist;
        inspect before deciding), or ``author_new`` (no good match — author a new
        workflow, which then *joins* the catalog). Each candidate carries its
        ``summary`` (recorded intent), ``param_schema``, ``entry_workflow``, and a
        0–1 ``confidence`` (the share of request terms it covers). The
        workflow-level analogue of the facet capability index.
        """
        terms = _tokenize_request(request)
        ranked: list[tuple[int, dict]] = []
        for entry in self._catalog.list_entries():
            if kind and entry.kind != kind:
                continue
            rev = self._resolve_revision(entry.slug, None, prefer_published=not include_drafts)
            if rev is None:
                continue
            score, hit = _match_score(terms, entry, rev)
            if terms and score == 0:
                continue
            summary = self._summary(entry, rev)
            summary["summary"] = rev.summary
            summary["confidence"] = round(len(hit) / len(terms), 2) if terms else 0.0
            summary["matched_terms"] = sorted(hit)
            ranked.append((score, summary))

        ranked.sort(key=lambda x: (-x[0], x[1]["slug"]))
        candidates = [s for _, s in ranked]
        best = candidates[0] if candidates else None
        best_conf = best["confidence"] if best else 0.0
        verdict = _match_verdict(best_conf) if terms else "author_new"
        return {
            "request": request,
            "terms": terms,
            "verdict": verdict,
            "best": best,
            "candidates": candidates[: max(1, limit)],
            "count": len(candidates),
        }

    def list_all(self) -> list[dict]:
        """Every catalog entry as a summary, annotated for grouping:
        ``depends_on`` (dep slugs of the resolved revision), ``package`` (the
        first library dep, if any — i.e. the package a thin workflow belongs to),
        and ``member_count`` (workflows depending on this entry — non-zero for
        package libraries). Used by ``fw ffl catalog list`` and any UI."""
        entries = self._catalog.list_entries()
        resolved: list[tuple] = []
        members: dict[str, int] = {}
        for e in entries:
            rev = self._resolve_revision(e.slug, None, prefer_published=False)
            deps = [p.slug for p in rev.depends_on] if rev else []
            for d in deps:
                members[d] = members.get(d, 0) + 1
            resolved.append((e, rev, deps))
        out: list[dict] = []
        for e, rev, deps in resolved:
            s = self._summary(e, rev)
            s["depends_on"] = deps
            s["package"] = deps[0] if deps else None
            s["member_count"] = members.get(e.slug, 0)
            out.append(s)
        out.sort(key=lambda s: (s["kind"] != KIND_LIBRARY, s["slug"]))
        return out

    def get(self, slug: str, version: int | None = None) -> dict | None:
        """Full detail for one entry + a specific (or latest) revision."""
        entry = self._catalog.get_entry(slug)
        if entry is None:
            return None
        rev = self._resolve_revision(slug, version, prefer_published=False)
        detail = self._summary(entry, rev) if rev else {"slug": slug}
        if rev is not None:
            detail["ffl_source"] = rev.ffl_source
            detail["depends_on"] = [
                {"slug": p.slug, "version": p.version, "revision_id": p.revision_id}
                for p in rev.depends_on
            ]
            detail["all_versions"] = [
                {"version": r.version, "status": r.status, "is_valid": r.is_valid}
                for r in self._catalog.get_revisions_for_slug(slug)
            ]
            detail["flow_id"] = rev.flow_id
            detail["workflow_id"] = rev.workflow_id
            detail["author"] = entry.author
            detail["note"] = rev.note
            detail["summary"] = rev.summary
            detail["warnings"] = rev.warnings
        return detail

    # =====================================================================
    # run — gated bootstrap submission
    # =====================================================================

    def run(
        self,
        slug: str,
        *,
        version: int | None = None,
        inputs: dict | None = None,
        allow_unpublished: bool = False,
        task_list: str | None = None,
    ) -> dict:
        """Submit a bootstrap ``fw:execute`` task pinned to a revision.

        The review gate: unless ``allow_unpublished`` is set (an explicit
        opt-in for interactive testing), the resolved revision MUST be
        published — so unattended runs only ever execute reviewed workflows.
        Re-running with different ``inputs`` re-uses the identical pinned
        revision; the workflow body cannot change underneath it.
        """
        revs = self._catalog.get_revisions_for_slug(slug)
        if not revs:
            raise CatalogError(f"no such workflow: {slug}")
        if version is not None:
            rev = self._catalog.get_revision_by_version(slug, version)
            if rev is None:
                raise CatalogError(f"no such version: {slug} v{version}")
        elif allow_unpublished:
            rev = revs[-1]
        else:
            published = [r for r in revs if r.status == STATUS_PUBLISHED]
            if not published:
                raise CatalogRunBlocked(
                    f"{slug} has no published revision (latest is v{revs[-1].version}, "
                    f"{revs[-1].status}); publish it or pass allow_unpublished=True "
                    f"for an attended test run."
                )
            rev = published[-1]
        # Per-version gate: a pinned draft still requires the opt-in.
        if rev.status != STATUS_PUBLISHED and not allow_unpublished:
            raise CatalogRunBlocked(
                f"{slug} v{rev.version} is a {rev.status} revision; publish it "
                f"(or pass allow_unpublished=True for an attended test run) before running."
            )
        if not rev.is_valid:
            raise CatalogError(f"{slug} v{rev.version} is invalid and cannot run")
        entry = self._catalog.get_entry(slug)
        if entry and entry.kind == KIND_LIBRARY:
            raise CatalogError(f"{slug} is a library, not a runnable workflow")

        # Handler preflight: every event facet the workflow calls must have a
        # loadable handler advertised by the fleet, else the run dead-letters
        # mid-flight (e.g. a declared-but-unimplemented facet). Refuse up front
        # with the offending facet names. Skipped when no registry is populated.
        missing = self._missing_handlers(rev)
        if missing:
            raise CatalogRunBlocked(
                f"{slug} v{rev.version} cannot run — no loadable handler advertised "
                f"for: {', '.join(missing)}. Start a runner that registers "
                f"{'these facets' if len(missing) > 1 else 'this facet'} "
                f"(or fix the handler), then retry."
            )

        import copy

        from facetwork.runtime.entities import (
            RunnerDefinition,
            RunnerState,
            TaskDefinition,
            TaskState,
            WorkflowDefinition,
        )
        from facetwork.runtime.types import generate_id

        try:
            from facetwork.runtime.task_list_routing import namespace_of

            resolved_list = task_list or namespace_of(rev.entry_workflow)
        except Exception:
            resolved_list = task_list or "default"

        runner_id = generate_id()
        task_id = generate_id()
        # Fresh EXECUTION workflow id per run. rev.workflow_id is the immutable
        # *definition* id (shared by every run of this revision); reusing it as
        # the execution scope let a prior run's steps/terminal state collide with
        # a new run. Mint a per-run WorkflowDefinition pointing at the same flow +
        # entry workflow — exactly what the CLI submit path does (a fresh wf_id
        # each run).
        exec_workflow_id = generate_id()
        now = _now_ms()

        definition = self._flows.get_workflow(rev.workflow_id)
        if definition is not None:
            run_workflow = copy.deepcopy(definition)
            run_workflow.uuid = exec_workflow_id
            run_workflow.facet_id = exec_workflow_id
        else:
            run_workflow = WorkflowDefinition(
                uuid=exec_workflow_id,
                name=rev.entry_workflow,
                namespace_id=f"claude:{slug}",
                facet_id=exec_workflow_id,
                flow_id=rev.flow_id,
                starting_step="",
                version=str(rev.version),
                date=now,
            )
        self._flows.save_workflow(run_workflow)

        self._flows.save_runner(
            RunnerDefinition(
                uuid=runner_id,
                workflow_id=exec_workflow_id,
                workflow=run_workflow,
                state=RunnerState.CREATED,
            )
        )
        self._flows.save_task(
            TaskDefinition(
                uuid=task_id,
                name=f"fw:execute:{rev.entry_workflow}",
                runner_id=runner_id,
                workflow_id=exec_workflow_id,
                flow_id=rev.flow_id,
                step_id="",
                state=TaskState.PENDING,
                created=now,
                updated=now,
                task_list_name=resolved_list,
                data={
                    "flow_id": rev.flow_id,
                    "workflow_id": exec_workflow_id,
                    "workflow_name": rev.entry_workflow,
                    "inputs": inputs or {},
                    "runner_id": runner_id,
                },
            )
        )
        return {
            "runner_id": runner_id,
            "task_id": task_id,
            "workflow_id": exec_workflow_id,
            "slug": slug,
            "version": rev.version,
            "workflow": rev.entry_workflow,
            "task_list": resolved_list,
        }

    # =====================================================================
    # rematerialize — rebuild a revision's runnable flow (used by restore)
    # =====================================================================

    def rematerialize(self, rev: CatalogRevision) -> CatalogRevision:
        """Recompile ``rev`` (own FFL + its pinned dep sources) and materialize
        a fresh FlowDefinition, returning a copy with new flow_id/workflow_id.

        Preserves identity (revision_id, version, content_hash, status,
        depends_on); only the runnable flow is regenerated. Used by restore to
        rebuild flows in a fresh database from the FFL alone. The pinned
        dependency revisions must already exist in the catalog store.

        A *thin* revision — no own FFL, exactly one ``library`` dependency — is
        a per-workflow handle onto a package library (see ``import_package``).
        It does NOT materialize its own flow; it reuses the library's single
        compiled flow and points at its own workflow within it. This keeps a
        restored N-workflow package at one shared flow instead of N copies of
        the (large) compiled program.
        """
        import copy

        thin = self._reuse_library_flow(rev)
        if thin is not None:
            return thin

        ordered: list[str] = []
        try:
            self._gather_sources(rev.depends_on, set(), ordered, set())
            program_dict, combined, is_valid, warnings = self._compile(ordered + [rev.ffl_source])
        except Exception as e:
            broken = copy.deepcopy(rev)
            broken.flow_id = ""
            broken.workflow_id = ""
            broken.is_valid = False
            broken.warnings = [f"recompile failed: {e}"]
            return broken

        from facetwork._pkg_discovery import _collect_workflow_names

        workflow_names = _collect_workflow_names(program_dict)
        entry_name = rev.entry_workflow or (workflow_names[0] if workflow_names else None)
        flow_id, workflow_id = self._materialize(
            rev.slug, rev.version, combined, program_dict, workflow_names, entry_name
        )
        out = copy.deepcopy(rev)
        out.flow_id = flow_id
        out.workflow_id = workflow_id
        out.is_valid = is_valid
        out.warnings = warnings
        return out

    def _missing_handlers(self, rev: CatalogRevision) -> list[str]:
        """Event facets this revision calls that have no loadable handler in the
        fleet's registry — calling them would dead-letter the run.

        Returns the qualified names of unservable event facets. Returns ``[]``
        (skips the preflight) when the flow store has no handler registry, the
        registry is empty (no runner has registered yet — can't assess), or the
        runtime dispatcher is unavailable.
        """
        if not hasattr(self._flows, "list_handler_registrations"):
            return []
        flow = self._flows.get_flow(rev.flow_id)
        if flow is None or not getattr(flow, "compiled_ast", None):
            return []
        # Only the event facets the ENTRY workflow transitively reaches — not
        # every facet in the (possibly large) pinned library, which other
        # workflows there might call.
        needed = _entry_event_facets(flow.compiled_ast, rev.entry_workflow)
        if not needed:
            return []
        try:
            from facetwork.runtime.dispatcher import RegistryDispatcher

            disp = RegistryDispatcher(self._flows)
            disp.preload()  # populate the registration cache (keep all)
        except Exception:
            return []
        if not disp.dispatchable_facets():
            return []  # empty registry — no runner up yet; don't false-positive
        # check_loadable imports each handler module + resolves its entrypoint,
        # catching a missing or unimportable handler before launch. (A broken
        # lazy import inside a dispatched handler body still surfaces at
        # dispatch — it can't be attributed to one facet in a shared module.)
        problems: list[str] = []
        for q in needed:
            reason = disp.check_loadable(q)
            if reason:
                problems.append(f"{q} ({reason})")
        return sorted(problems)

    def _reuse_library_flow(self, rev: CatalogRevision) -> CatalogRevision | None:
        """If ``rev`` is a thin per-workflow handle onto a package library
        (empty own FFL + exactly one library dep), return a copy bound to the
        library's already-materialized flow instead of building a new one;
        otherwise ``None`` (caller falls through to normal materialization).

        The library flow is rematerialized on demand if missing, so restore
        order does not matter.
        """
        import copy

        if rev.ffl_source.strip() or len(rev.depends_on) != 1:
            return None
        pin = rev.depends_on[0]
        dep_entry = self._catalog.get_entry(pin.slug)
        if dep_entry is None or dep_entry.kind != KIND_LIBRARY:
            return None
        lib_rev = self._catalog.get_revision(pin.revision_id)
        if lib_rev is None:
            return None
        if not lib_rev.flow_id or self._flows.get_flow(lib_rev.flow_id) is None:
            lib_rev = self.rematerialize(lib_rev)
            self._catalog.save_revision(lib_rev)
        wf_id = next(
            (w.uuid for w in self._flows.get_workflows_by_flow(lib_rev.flow_id)
             if w.name == rev.entry_workflow),
            "",
        )
        out = copy.deepcopy(rev)
        out.flow_id = lib_rev.flow_id
        out.workflow_id = wf_id
        out.is_valid = lib_rev.is_valid and bool(wf_id)
        if not wf_id:
            out.warnings = [
                f"workflow {rev.entry_workflow!r} not found in library {pin.slug}"
            ]
        return out

    # =====================================================================
    # internals
    # =====================================================================

    def _resolve_deps(
        self, depends_on: list[dict]
    ) -> tuple[list[DependencyPin], list[str]]:
        """Pin each requested dependency to a concrete revision and gather the
        transitive FFL sources (deepest first, deduped by slug)."""
        direct: list[DependencyPin] = []
        for spec in depends_on:
            dep_slug = spec["slug"]
            dep_rev = self._resolve_revision(
                dep_slug, spec.get("version"), prefer_published=True
            )
            if dep_rev is None:
                raise CatalogError(f"dependency not found: {dep_slug} v{spec.get('version')}")
            direct.append(
                DependencyPin(
                    slug=dep_slug,
                    revision_id=dep_rev.revision_id,
                    version=dep_rev.version,
                    content_hash=dep_rev.content_hash,
                )
            )
        ordered: list[str] = []
        seen: set[str] = set()
        self._gather_sources(direct, seen, ordered, set())
        return direct, ordered

    def _gather_sources(
        self,
        pins: list[DependencyPin],
        seen: set[str],
        ordered: list[str],
        in_progress: set[str],
    ) -> None:
        for pin in pins:
            if pin.slug in seen:
                continue
            if pin.slug in in_progress:
                raise CatalogError(f"dependency cycle through {pin.slug}")
            rev = self._catalog.get_revision(pin.revision_id)
            if rev is None:
                raise CatalogError(f"pinned dependency revision missing: {pin.revision_id}")
            in_progress.add(pin.slug)
            self._gather_sources(rev.depends_on, seen, ordered, in_progress)
            in_progress.discard(pin.slug)
            seen.add(pin.slug)
            ordered.append(rev.ffl_source)

    @staticmethod
    def _content_hash(ffl_source: str, pins: list[DependencyPin]) -> str:
        key = ffl_source.strip() + "\n--deps--\n" + "\n".join(
            f"{p.slug}:{p.content_hash}" for p in sorted(pins, key=lambda p: p.slug)
        )
        return "sha256:" + hashlib.sha256(key.encode("utf-8")).hexdigest()

    @staticmethod
    def _compile(sources: list[str]) -> tuple[dict, str, bool, list[str]]:
        import json

        from facetwork.ast import Program
        from facetwork.emitter import JSONEmitter
        from facetwork.parser import FFLParser
        from facetwork.validator import validate

        parser = FFLParser()
        programs = [parser.parse(s) for s in sources]
        merged = Program.merge(programs)
        result = validate(merged)
        warnings = [] if result.is_valid else [str(e) for e in result.errors]
        program_dict = json.loads(JSONEmitter(include_locations=False).emit(merged))
        return program_dict, "\n\n".join(sources), result.is_valid, warnings

    @staticmethod
    def _resolve_entry(
        kind: str, workflow_names: list[str], entry_workflow: str | None
    ) -> tuple[str | None, str | None]:
        if kind == KIND_LIBRARY:
            return (entry_workflow or None), None
        if not workflow_names:
            return None, "no workflow defined in the FFL (use kind='library' for facet-only libs)"
        if entry_workflow:
            for q in workflow_names:
                if q == entry_workflow or q.split(".")[-1] == entry_workflow:
                    return q, None
            return None, f"entry_workflow {entry_workflow!r} not found in {workflow_names}"
        if len(workflow_names) == 1:
            return workflow_names[0], None
        return None, f"multiple workflows {workflow_names}; specify entry_workflow"

    def _materialize(
        self,
        slug: str,
        version: int,
        combined_source: str,
        program_dict: dict,
        workflow_names: list[str],
        entry_name: str | None,
    ) -> tuple[str, str]:
        """Create the runnable FlowDefinition + WorkflowDefinitions (one
        immutable flow per revision). Returns (flow_id, entry workflow_id)."""
        from facetwork.runtime.entities import (
            FlowDefinition,
            FlowIdentity,
            SourceText,
            WorkflowDefinition,
        )
        from facetwork.runtime.types import generate_id

        flow_id = generate_id()
        now = _now_ms()
        self._flows.save_flow(
            FlowDefinition(
                uuid=flow_id,
                name=FlowIdentity(name=slug, path=f"claude:{slug}:v{version}", uuid=flow_id),
                compiled_sources=[SourceText(name="source.ffl", content=combined_source)],
                compiled_ast=program_dict,
            )
        )
        entry_workflow_id = ""
        for qname in workflow_names:
            wf_id = generate_id()
            if qname == entry_name:
                entry_workflow_id = wf_id
            self._flows.save_workflow(
                WorkflowDefinition(
                    uuid=wf_id,
                    name=qname,
                    namespace_id=f"claude:{slug}",
                    facet_id=wf_id,
                    flow_id=flow_id,
                    starting_step="",
                    version=str(version),
                    date=now,
                )
            )
        return flow_id, entry_workflow_id

    @staticmethod
    def _find_wf(program_dict: dict, name: str) -> dict | None:
        from facetwork.ast_utils import find_workflow

        return find_workflow(program_dict, name)

    def _resolve_revision(
        self, slug: str, version: int | None, *, prefer_published: bool
    ) -> CatalogRevision | None:
        if version is not None:
            return self._catalog.get_revision_by_version(slug, version)
        revs = self._catalog.get_revisions_for_slug(slug)
        if not revs:
            return None
        if prefer_published:
            pub = [r for r in revs if r.status == STATUS_PUBLISHED]
            if pub:
                return pub[-1]
            return None  # gate: nothing published
        return revs[-1]

    def _require_revision(self, slug: str, version: int | None) -> CatalogRevision:
        rev = self._catalog.get_revision_by_version(slug, version) if version is not None else None
        if rev is None and version is None:
            revs = self._catalog.get_revisions_for_slug(slug)
            rev = revs[-1] if revs else None
        if rev is None:
            raise CatalogError(f"no such workflow/version: {slug} v{version}")
        return rev

    def _upsert_entry(
        self,
        slug: str,
        kind: str,
        title: str,
        description: str,
        tags: list[str],
        author: str,
        version: int,
        teams: list[str] | None = None,
    ) -> None:
        entry = self._catalog.get_entry(slug)
        now = _now_ms()
        if entry is None:
            entry = CatalogEntry(
                slug=slug, kind=kind, title=title, description=description, tags=tags,
                teams=teams or [], latest_version=version, author=author,
                created_at=now, updated_at=now,
            )
        else:
            if title:
                entry.title = title
            if description:
                entry.description = description
            if tags:
                entry.tags = tags
            if teams:
                entry.teams = teams
            entry.latest_version = max(entry.latest_version, version)
            entry.updated_at = now
        self._catalog.save_entry(entry)

    @staticmethod
    def _score(q: str, entry: CatalogEntry, rev: CatalogRevision) -> int:
        if not q:
            return 1
        score = 0
        if q in entry.slug.lower():
            score += 5
        if q in entry.title.lower():
            score += 3
        if q in entry.description.lower():
            score += 2
        for t in entry.tags:
            if q in t.lower():
                score += 2
        for f in rev.facets_used:
            if q in f.lower():
                score += 1
        return score

    @staticmethod
    def _summary(entry: CatalogEntry, rev: CatalogRevision | None) -> dict:
        s = {
            "slug": entry.slug,
            "kind": entry.kind,
            "title": entry.title,
            "description": entry.description,
            "tags": entry.tags,
            "latest_version": entry.latest_version,
            "published_version": entry.published_version,
        }
        if rev is not None:
            s.update(
                {
                    "version": rev.version,
                    "status": rev.status,
                    "is_valid": rev.is_valid,
                    "entry_workflow": rev.entry_workflow,
                    "param_schema": rev.param_schema,
                    "facets_used": rev.facets_used,
                }
            )
        return s


# ---------------------------------------------------------------------------
# Reuse-first matching helpers
# ---------------------------------------------------------------------------

# Generic request words that carry no domain signal (so they don't inflate a
# match). Domain verbs like "route"/"map"-as-noun stay — only true filler drops.
_MATCH_STOPWORDS = frozenset({
    "the", "a", "an", "of", "in", "on", "for", "to", "and", "or", "with", "by",
    "me", "my", "is", "are", "that", "this", "show", "give", "get", "all", "any",
    "please", "want", "need", "from", "at", "as", "it", "its",
})

# Field weights for intent matching — the authoring summary (recorded intent)
# dominates, then title/tags, then description/slug, then the facets used.
_MATCH_WEIGHTS = (("summary", 6), ("title", 5), ("tags", 4),
                  ("description", 3), ("slug", 3), ("facets", 2))


def _tokenize_request(text: str) -> list[str]:
    """Lowercase, split on non-alphanumerics, drop short/filler words; de-duped."""
    seen: list[str] = []
    for tok in re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).split():
        if len(tok) > 2 and tok not in _MATCH_STOPWORDS and tok not in seen:
            seen.append(tok)
    return seen


def _match_score(terms: list[str], entry: CatalogEntry, rev: CatalogRevision) -> tuple[int, set[str]]:
    """Weighted intent score + the set of request terms that hit any field."""
    text = {
        "summary": (rev.summary or "").lower(),
        "title": (entry.title or "").lower(),
        "tags": " ".join(entry.tags or []).lower(),
        "description": (entry.description or "").lower(),
        "slug": (entry.slug or "").replace("-", " ").replace("_", " ").lower(),
        "facets": " ".join(rev.facets_used or []).lower(),
    }
    score = 0
    hit: set[str] = set()
    for term in terms:
        for field_name, weight in _MATCH_WEIGHTS:
            if term in text[field_name]:
                score += weight
                hit.add(term)
    return score, hit


def _match_verdict(best_confidence: float) -> str:
    """Map the best candidate's term coverage to a reuse recommendation."""
    if best_confidence >= 0.6:
        return "reuse"
    if best_confidence >= 0.3:
        return "review"
    return "author_new"


# ---------------------------------------------------------------------------
# AST extraction helpers
# ---------------------------------------------------------------------------


def _param_schema(wf_ast: dict) -> list[dict]:
    out = []
    for p in wf_ast.get("params", []) or []:
        d = p.get("default")
        if isinstance(d, dict) and "value" in d:
            d = d["value"]
        out.append({"name": p.get("name"), "type": p.get("type", "Any"), "default": d})
    return out


def _returns_schema(wf_ast: dict) -> list[dict]:
    out = []
    rets = wf_ast.get("returns") or wf_ast.get("return") or []
    if isinstance(rets, dict):
        rets = rets.get("fields", []) or rets.get("params", [])
    for r in rets or []:
        if isinstance(r, dict):
            out.append({"name": r.get("name"), "type": r.get("type", "Any")})
    return out


_DECL_KIND = {"EventFacetDecl": "event", "FacetDecl": "facet", "WorkflowDecl": "workflow"}


def _build_call_graph(node: dict, prefix: str, graph: dict, kind: dict) -> None:
    """Populate ``graph`` (qualified facet/workflow name -> call targets in its
    body) and ``kind`` (qualified name -> 'event' | 'facet' | 'workflow') from a
    compiled program dict. Handles nested and flat emitter shapes."""

    def _name(d: dict) -> str | None:
        return d.get("name") or (d.get("sig") or {}).get("name")

    for key, k in (("event_facets", "event"), ("facets", "facet"), ("workflows", "workflow")):
        for d in node.get(key, []):
            n = _name(d)
            if n:
                q = f"{prefix}{n}" if prefix else n
                graph[q] = _collect_call_targets(d)
                kind[q] = k
    for decl in node.get("declarations", []):
        t = decl.get("type")
        if t in _DECL_KIND:
            n = _name(decl)
            if n:
                q = f"{prefix}{n}" if prefix else n
                graph[q] = _collect_call_targets(decl)
                kind[q] = _DECL_KIND[t]
        elif t == "Namespace":
            _build_call_graph(decl, f"{prefix}{decl['name']}.", graph, kind)
    for ns in node.get("namespaces", []):
        _build_call_graph(ns, f"{prefix}{ns['name']}.", graph, kind)


def _entry_event_facets(program: dict, entry: str) -> set[str]:
    """Qualified names of event facets transitively reachable from the entry
    workflow's body — the handlers a run of ``entry`` will actually need."""
    graph: dict[str, list[str]] = {}
    kind: dict[str, str] = {}
    _build_call_graph(program, "", graph, kind)
    short: dict[str, str] = {}
    for q in graph:  # last-wins; ambiguity is rare and the registry resolves shorts too
        short[q.rsplit(".", 1)[-1]] = q

    def resolve(target: str) -> str | None:
        return target if target in graph else short.get(target.rsplit(".", 1)[-1])

    start = resolve(entry)
    if start is None:
        return set()
    seen: set[str] = set()
    queue = [start]
    events: set[str] = set()
    while queue:
        cur = queue.pop()
        if cur in seen:
            continue
        seen.add(cur)
        for target in graph.get(cur, []):
            q = resolve(target)
            if q is None:
                continue
            if kind.get(q) == "event":
                events.add(q)
            elif q not in seen:
                queue.append(q)
    return events


def _collect_call_targets(node: Any, acc: set[str] | None = None) -> list[str]:
    """Best-effort set of facet/workflow names this program calls (for
    discovery + a 'facets this needs' hint)."""
    if acc is None:
        acc = set()
    if isinstance(node, dict):
        if node.get("type") == "CallExpr" and isinstance(node.get("target"), str):
            acc.add(node["target"])
        for v in node.values():
            _collect_call_targets(v, acc)
    elif isinstance(node, list):
        for v in node:
            _collect_call_targets(v, acc)
    return sorted(acc)

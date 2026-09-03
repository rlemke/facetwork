"""`fw util check-doc-links` must catch stale code paths and protocol names.

The drift it exists for, measured 2026-09-03: after the afl/ -> facetwork/
rename, 56 references across 14 documents pointed at a package that no longer
existed -- and facetwork/runtime/continuation.py documented the continuation
queue as `_afl_continue` while the runtime polls `_fw_continue`. Markdown-link
checking could not see any of it, because none of them were links.
"""
import importlib.util
import pathlib

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
MOD = REPO / "scripts" / "lib" / "_helpers" / "_doc_coderefs.py"


def _load():
    spec = importlib.util.spec_from_file_location("_doc_coderefs", MOD)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


dc = _load()


def test_reserved_names_map_to_current_spelling():
    assert dc.RESERVED["_afl_continue"] == "_fw_continue"
    assert dc.RESERVED["afl:execute"] == "fw:execute"
    assert dc.RESERVED["afl:resume"] == "fw:resume"


def test_only_path_shaped_refs_are_checked():
    """A bare filename in prose is illustrative or lives in another repo;
    checking those produced 200+ false positives, and a linter nobody can run
    clean is a linter nobody runs."""
    assert dc.CODE_REF.search("see `facetwork/runtime/step.py` now")
    assert not dc.CODE_REF.search("see `migrate.py` now")


def test_changelog_is_exempt():
    """A changelog entry naming afl/runtime/x.py was TRUE when written;
    rewriting it falsifies history rather than fixing drift."""
    assert "docs/contributing/changelog.md" in dc.SKIP_FILES


def test_third_party_trees_are_skipped():
    for d in (".venv", "site-packages", "node_modules"):
        assert d in dc.SKIP_DIRS


def test_resolves_accepts_a_path_relative_to_a_package_root():
    """Docs write `catalog/service.py` for facetwork/catalog/service.py, which
    is correct in context."""
    assert dc.resolves(REPO, "facetwork/runtime/step.py")
    assert dc.resolves(REPO, "runtime/step.py")
    assert not dc.resolves(REPO, "afl/runtime/step.py")


def test_the_repo_is_currently_clean():
    """Guards the fix itself: 0 stale refs and 0 stale protocol names."""
    import io, contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = dc.main.__wrapped__() if hasattr(dc.main, "__wrapped__") else None
    # main() parses argv; call the pieces directly instead
    bad = []
    for md in (REPO / "docs").rglob("*.md"):
        rel = str(md.relative_to(REPO))
        if rel in dc.SKIP_FILES or dc.SKIP_DIRS & set(md.parts):
            continue
        text = md.read_text(errors="replace")
        for stale in dc.RESERVED:
            assert stale not in text, f"{rel} still contains {stale}"
        for m in dc.CODE_REF.finditer(text):
            p = m.group(1)
            if dc.ALLOW_MISSING.match(p) or p.split("/", 1)[0] not in dc.OURS:
                continue
            if not dc.resolves(REPO, p):
                bad.append((rel, p))
    assert not bad, f"stale code refs: {bad[:5]}"


def test_the_runtime_owns_its_protocol_names():
    """The bug that mattered was in CODE: the module implementing the
    continuation protocol documented the wrong queue name."""
    for f in ("facetwork/runtime/continuation.py", "facetwork/runtime/runner/service.py"):
        text = (REPO / f).read_text()
        for stale in dc.RESERVED:
            assert stale not in text, f"{f} still names {stale}"

#!/usr/bin/env python3
"""Validate that docs point at code paths and protocol names that EXIST.

`fw util check-doc-links` verified markdown-to-markdown links. It could not
catch the larger drift: prose that names a source file or a reserved protocol
name that the codebase no longer has. Measured 2026-09-03, after the
afl/ -> facetwork/ rename: **56 stale references across 14 documents**, and one
in code — facetwork/runtime/continuation.py documented the continuation queue as
``_afl_continue`` while the runtime polls ``_fw_continue``. An agent author
following the module that OWNS the protocol would have written to a queue
nothing reads.

Two classes of check, because they fail differently:

  paths  A `backticked/path.py` that does not exist. Usually a rename, but not
         always a rename that a global search-and-replace can perform: of the
         77 stale paths, 14 needed a decision - entities.py and mongo_store.py
         had become PACKAGES, claude_agent.py's class moved to agent.py, and
         events.py named two classes (EventManager, EventDispatcher) that had
         been deleted outright. A blind sed would have produced 14 confident,
         wrong references.

  names  Reserved protocol strings in a stale spelling. These are worse than a
         bad path: a broken path is a dead link, a wrong queue name is a silent
         runtime no-op.

⚠️ Two deliberate exemptions, both real:
  - docs/contributing/changelog.md is a HISTORICAL record. An entry that said
    `afl/runtime/x.py` was true when written; rewriting it falsifies history.
  - `afl.agent` is the genuine JAVA package name
    (agents/java/fw-agent/src/main/java/afl/agent/AgentPoller.java). The docs
    that import it are correct.
"""
import argparse
import os
import pathlib
import re
import sys

# A backticked path that looks like a source file we ship. ⚠️ It must contain a
# SLASH: the drift worth catching is path-shaped (`afl/runtime/evaluator.py`),
# while a bare filename in prose is usually illustrative or lives in another
# repo (`publish-replication.sh` belongs to fwh_osm). Checking bare names
# produced 200+ false positives, and a linter nobody can run clean is a linter
# nobody runs.
CODE_REF = re.compile(r"`([A-Za-z0-9_][A-Za-z0-9_.-]*(?:/[A-Za-z0-9_.-]+)+\.(?:py|lark|ffl|java|sh))`")
# Reserved protocol names, in their ONLY correct spelling.
RESERVED = {
    "_afl_continue": "_fw_continue",
    "afl:execute": "fw:execute",
    "afl:resume": "fw:resume",
}
SKIP_FILES = {"docs/contributing/changelog.md"}
# Third-party trees carry their own READMEs about their own layout.
SKIP_DIRS = {".git", ".venv", "node_modules", "site-packages", "__pycache__", "build", "dist"}
# Top-level trees this repo owns and can therefore be authoritative about.
OURS = {"facetwork", "scripts", "docs", "examples", "tests", "agents",
        "domain-template", "afl"}


def resolves(repo, p):
    """Exact from the repo root, or as a suffix of a real path.

    Docs often write a path relative to a package root named in the surrounding
    prose (`catalog/service.py` for `facetwork/catalog/service.py`). That is
    readable and correct in context, so a root-anchored check alone would flag
    ~50 references that are not drift at all.
    """
    if (repo / p).exists():
        return True
    tail = "/" + p
    for cand in repo.rglob("*" + pathlib.Path(p).name):
        if SKIP_DIRS & set(cand.parts):
            continue
        if str(cand).endswith(tail):
            return True
    return False
# Paths that legitimately do not exist in this repo.
ALLOW_MISSING = re.compile(
    r"^(?:"
    r"path/|your/|some/|example|foo|bar|my_|<|\.\.\.|"          # illustrative
    r"[A-Za-z0-9_.-]+\.(?:json|yml|yaml)$|"                      # bare filenames
    r"afl/agent/"                                                # the Java package
    r")"
)


def main() -> int:
    ap = argparse.ArgumentParser(prog="fw util check-doc-links --code")
    ap.add_argument("root", nargs="?", default=".")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()
    root = pathlib.Path(a.root).resolve()
    repo = pathlib.Path(__file__).resolve().parents[3]

    bad_paths, bad_names, foreign, checked = [], [], [], 0
    for md in sorted(root.rglob("*.md")):
        try:
            rel = md.relative_to(repo)
        except ValueError:
            rel = md
        if str(rel) in SKIP_FILES or SKIP_DIRS & set(md.parts):
            continue
        try:
            text = md.read_text(errors="replace")
        except OSError:
            continue
        for n, line in enumerate(text.split("\n"), 1):
            for stale, good in RESERVED.items():
                if stale in line:
                    bad_names.append((rel, n, stale, good))
            for m in CODE_REF.finditer(line):
                p = m.group(1)
                checked += 1
                if ALLOW_MISSING.match(p):
                    continue
                if resolves(repo, p):
                    continue
                # Only ERROR for a path under a tree we own. A doc may legitimately
                # name a file in a DOMAIN repo (fwh_osm's `handlers/shared/osm_utils.py`),
                # and this repo cannot adjudicate that - reporting it as broken would
                # make the check unfixable here, which is how checks get ignored.
                if p.split("/", 1)[0] in OURS:
                    bad_paths.append((rel, n, p))
                else:
                    foreign.append((rel, n, p))

    # The same reserved-name rule, applied to the code itself - that is where it
    # actually mattered.
    for py in sorted((repo / "facetwork").rglob("*.py")):
        if "__pycache__" in py.parts:
            continue
        try:
            text = py.read_text(errors="replace")
        except OSError:
            continue
        for n, line in enumerate(text.split("\n"), 1):
            for stale, good in RESERVED.items():
                if stale in line:
                    bad_names.append((py.relative_to(repo), n, stale, good))

    if a.quiet:
        print(f"code refs: {len(bad_paths)} broken paths, {len(bad_names)} stale protocol names "
              f"({checked} checked)")
        return 1 if (bad_paths or bad_names) else 0

    if bad_names:
        print(f"✗ {len(bad_names)} stale RESERVED PROTOCOL NAME(s) — a wrong queue name is a")
        print("  silent no-op, not a dead link:")
        for f, n, stale, good in bad_names:
            print(f"    {f}:{n}  {stale}  ->  {good}")
        print()
    if bad_paths:
        print(f"✗ {len(bad_paths)} referenced code path(s) that do not exist:")
        for f, n, p in bad_paths:
            print(f"    {f}:{n}  {p}")
        print()
        print("  ⚠️ Do NOT fix these with a global search-and-replace. After the")
        print("  afl/ -> facetwork/ rename, 14 of 77 needed a decision: two modules had")
        print("  become packages, one class had moved, and two named classes had been")
        print("  deleted. Resolve by SYMBOL (where is the class now?), not by filename.")
        return 1
    if foreign:
        print(f"note: {len(foreign)} reference(s) name files outside this repo "
              f"(domain repos); reported, not failed:")
        for f, n, p in foreign[:6]:
            print(f"    {f}:{n}  {p}")
        print()
    if not bad_names:
        print(f"✓ all {checked} in-repo code references resolve; no stale protocol names")
    return 1 if bad_names else 0


if __name__ == "__main__":
    sys.exit(main())

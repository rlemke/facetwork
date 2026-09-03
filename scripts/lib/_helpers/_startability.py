#!/usr/bin/env python3
"""Report which `fw` commands cannot START on this host.

⚠️ This deliberately does NOT run the commands. `fw` execs a command file
directly, so the only "safe" probe would be `--help` — and 44 of this repo's 99
commands have no --help handling at all (`fw mode leave`, `fw install rebuild`,
`fw ffl run`, `fw single reset`). Probing by execution would leave the fleet,
rebuild an image and submit a workflow. So startability is derived STATICALLY:
resolve the interpreter `fw` will hand each file to, then ask THAT interpreter
whether the file's hard imports resolve.

The defect this exists for, measured 2026-09-02: `fw` execs a
`#!/usr/bin/env python3` file under the SYSTEM interpreter, never the repo venv.
On three of this fleet's four hosts the system python has no pymongo, so
`fw maint unsatisfiable` exited 2 ("could not verify") from the day it shipped —
on every host except the one it was written on. Bash commands dodge this by
assigning `PYTHON="$FW_ROOT/.venv/bin/python3"` first; a bare .py command has
nobody to choose an interpreter for it, so it must re-exec itself.

It stayed invisible because exit 2 is the *correct* could-not-verify code and
does not alarm. That is right for a one-off run and wrong as a standing state:
"I cannot check" and "I checked, all clear" are indistinguishable from outside,
and the thing `unsatisfiable` watches for (a task no runner can ever claim) also
never errors and never dead-letters. A check that can never run is not a check.

Verdicts
  BROKEN  — a hard import is missing under the interpreter fw will use. The
            command cannot start here.
  RISK    — starts, but reaches a third-party module through a bare `python3`
            rather than the venv-preferring idiom, so it is one host away from
            BROKEN.
  OK      — imports resolve, or the command needs no python at all.
"""
import argparse
import ast
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

# A command, exactly as fw dispatches one: executable, basename not starting
# with '_', at group/<cmd> or group/<subgroup>/<cmd>. Mirrors _fw_is_listable.
SKIP_DIRS = {"__pycache__", "_helpers"}
VENV_PY = re.compile(r"\.venv/bin/python")
REEXEC = re.compile(r"os\.exec[vl]p?e?\(")
# A bare python invocation in shell: start of line/pipe/subshell, not $PYTHON.
BASH_BARE_PY = re.compile(r"(?:^|[|;&(`]|\$\()\s*(?:command\s+)?python3?\b", re.M)
# Imports inside an embedded python payload, in the two shapes shell uses: at
# line start (a heredoc body) and after a quote or semicolon (`python3 -c
# "import pymongo"`). Comment lines are stripped first, so prose like
# "# will import the catalog" cannot manufacture a dependency named "the" —
# these files carry usage examples in their headers, and a check that invents
# missing modules is one nobody reads twice.
BASH_IMPORT = re.compile(
    r"""(?:^|["';])\s*(?:import\s+([A-Za-z_][\w.]*)|from\s+([A-Za-z_][\w.]*)\s+import)""",
    re.M,
)
SHELL_COMMENT = re.compile(r"^\s*#.*$", re.M)
OPTIONAL_EXC = {"ImportError", "ModuleNotFoundError"}

try:
    STDLIB = set(sys.stdlib_module_names)  # 3.10+
except AttributeError:  # pragma: no cover - ancient interpreter
    STDLIB = set(sys.builtin_module_names)
STDLIB |= {"__future__"}


def iter_commands(lib: Path):
    for group in sorted(lib.iterdir()):
        if not group.is_dir() or group.name.startswith("_") or group.name in SKIP_DIRS:
            continue
        for entry in sorted(group.iterdir()):
            if entry.name.startswith("_") or entry.name in SKIP_DIRS:
                continue
            if entry.is_file() and os.access(entry, os.X_OK):
                yield f"{group.name} {entry.name}", entry
            elif entry.is_dir():
                for sub in sorted(entry.iterdir()):
                    if sub.name.startswith("_") or not sub.is_file():
                        continue
                    if os.access(sub, os.X_OK):
                        yield f"{group.name} {entry.name} {sub.name}", sub


def _optional_nodes(tree):
    """ids of nodes inside a try: that catches ImportError — optional by design."""
    out = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        guarded = any(
            h.type is None
            or (isinstance(h.type, ast.Name) and h.type.id in OPTIONAL_EXC)
            or (
                isinstance(h.type, ast.Tuple)
                and any(
                    isinstance(e, ast.Name) and e.id in OPTIONAL_EXC for e in h.type.elts
                )
            )
            for h in node.handlers
        )
        if guarded:
            for stmt in node.body:
                for n in ast.walk(stmt):
                    out.add(id(n))
    return out


def python_imports(text):
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return set()
    optional = _optional_nodes(tree)
    mods = set()
    for node in ast.walk(tree):
        if id(node) in optional:
            continue
        if isinstance(node, ast.Import):
            mods.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
            mods.add(node.module.split(".")[0])
    return mods


def shell_imports(text):
    mods = set()
    text = SHELL_COMMENT.sub("", text)
    for a, b in BASH_IMPORT.findall(text):
        mods.add((a or b).split(".")[0])
    return mods


def classify(path: Path):
    """-> (lang, interpreter_key, modules) for one command file."""
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return "unreadable", "none", set()
    first = text.split("\n", 1)[0]
    if "python" in first:
        # fw execs the file, so `env python3` = the SYSTEM interpreter, unless
        # the file re-execs itself under the venv (which is the fix, not a
        # style choice — see the module docstring).
        key = "venv" if (VENV_PY.search(text) and REEXEC.search(text)) else "system"
        return "python", key, python_imports(text)
    mods = shell_imports(text)
    if VENV_PY.search(text):
        return "shell", "venv", mods
    if mods and BASH_BARE_PY.search(text):
        return "shell", "system", mods
    return "shell", "none", mods


def local_modules(root: Path, extra_roots=()):
    """Top-level module names that exist as FILES we ship.

    A command that imports one of these reaches it through a sys.path.insert
    (`from fwroot import fw_root` after inserting _helpers/), so it is not a
    missing dependency. Deciding this by "does the file do a path dance?" was
    too crude — `fw maint unsatisfiable` does one AND needs pymongo, so that
    rule excused the very bug this check exists for. Asking whether the module
    is something we ship separates the two exactly.
    """
    names = set()
    for base in (root, *extra_roots):
        if not base or not Path(base).is_dir():
            continue
        for depth in ("*", "*/*", "*/*/*"):
            for hit in Path(base).glob(f"{depth}.py"):
                if ".venv" not in hit.parts and "site-packages" not in hit.parts:
                    names.add(hit.stem)
            for hit in Path(base).glob(f"{depth}/__init__.py"):
                if ".venv" not in hit.parts and "site-packages" not in hit.parts:
                    names.add(hit.parent.name)
    return names


def probe(interp, mods):
    """Which of `mods` are not importable under `interp`."""
    if not mods or not interp:
        return set()
    code = (
        "import importlib.util,sys,json\n"
        "def miss(m):\n"
        " try: return importlib.util.find_spec(m) is None\n"
        " except Exception: return True\n"
        "print(json.dumps([m for m in sys.argv[1:] if miss(m)]))\n"
    )

    try:
        r = subprocess.run(
            [interp, "-c", code, *sorted(mods)],
            capture_output=True,
            text=True,
            timeout=90,
            env={**os.environ, "PYTHONPATH": ""},
        )
        return set(json.loads(r.stdout.strip().splitlines()[-1]))
    except Exception:
        return set(mods)


def main():
    ap = argparse.ArgumentParser(prog="fw install check --commands")
    ap.add_argument("--lib", default=None, help="scripts/lib to scan")
    ap.add_argument("--root", default=None, help="repo root")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--quiet", action="store_true", help="one summary line only")
    args = ap.parse_args()

    root = Path(args.root or Path(__file__).resolve().parents[3])
    lib = Path(args.lib or root / "scripts" / "lib")
    if not lib.is_dir():
        print(f"could not verify: no scripts/lib at {lib}", file=sys.stderr)
        return 2

    interps = {
        "system": shutil.which("python3") or shutil.which("python"),
        "venv": str(root / ".venv/bin/python3")
        if (root / ".venv/bin/python3").exists()
        else None,
        "none": None,
    }

    cmds = list(iter_commands(lib))
    if not cmds:
        print(f"could not verify: no commands found under {lib}", file=sys.stderr)
        return 2

    # _helpers is named explicitly because every command bootstraps by
    # inserting it on sys.path, so `from fwroot import fw_root` always
    # resolves there and is not a missing dependency.
    local = local_modules(root, [
        lib / "_helpers",
        os.environ.get("FWH_HANDLERS_ROOT") or Path.home() / "fw_handlers",
    ])

    # One probe per interpreter over the union of what it is asked for.
    wanted = {"system": set(), "venv": set()}
    meta = {}
    for name, path in cmds:
        lang, key, mods = classify(path)
        mods = {
            m for m in mods
            if m not in STDLIB and not m.startswith("_") and m not in local
        }
        meta[name] = (lang, key, mods)
        if key in wanted:
            wanted[key] |= mods
    missing = {key: probe(interps[key], wanted[key]) for key in ("system", "venv")}

    broken, risk, ok = [], [], []
    for name, path in cmds:
        lang, key, mods = meta[name]
        gone = sorted(m for m in mods if m in missing.get(key, set()))
        if gone:
            broken.append((name, key, gone))
        elif key == "system" and mods:
            risk.append((name, sorted(mods)))
        else:
            ok.append(name)

    if args.json:
        print(json.dumps(
            {"broken": [{"cmd": c, "interpreter": k, "missing": m} for c, k, m in broken],
             "risk": [{"cmd": c, "modules": m} for c, m in risk],
             "ok": len(ok), "total": len(cmds),
             "interpreters": {k: v for k, v in interps.items() if v}}, indent=2))
        return 1 if broken else 0

    host = os.uname().nodename.split(".")[0]
    if args.quiet:
        print(f"{host}: {len(broken)} BROKEN, {len(risk)} RISK, {len(ok)} OK of {len(cmds)}")
        return 1 if broken else 0

    print(f"[4] fw command startability on {host} — {len(cmds)} commands")
    sysi, venvi = interps["system"], interps["venv"]
    print(f"    fw execs a python command under : {sysi or '(none on PATH)'}")
    print(f"    the repo venv is               : {venvi or '(absent)'}")
    print()
    if broken:
        print(f"  ✗ {len(broken)} command(s) CANNOT START here:")
        for name, key, gone in broken:
            print(f"      fw {name:<28} {key:<6} missing: {', '.join(gone)}")
        print()
        print("    Fix: have the command re-exec itself under the repo venv (see")
        print("    scripts/lib/maint/unsatisfiable), or install the module for the")
        print("    system interpreter. `fw install repo` creates the venv if absent.")
        print()
    if risk:
        print(f"  ⚠ {len(risk)} command(s) reach a third-party module through the SYSTEM python:")
        for name, mods in risk:
            print(f"      fw {name:<28} needs: {', '.join(mods)}")
        print("    They start here, but are one host away from the failure above.")
        print()
    if not broken and not risk:
        print(f"  ✓ all {len(cmds)} commands resolve their imports on this host")
    return 1 if broken else 0


if __name__ == "__main__":
    sys.exit(main())

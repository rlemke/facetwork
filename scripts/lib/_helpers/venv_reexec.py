"""Re-exec a bare-python `fw` command under the repo venv.

⚠️ `fw` EXECS a command file directly, so `#!/usr/bin/env python3` resolves to
whatever python3 is on PATH — the SYSTEM interpreter, never the repo venv. That
PATH differs by context: on this fleet a login shell finds
/usr/local/bin/python3 (pymongo present) while a launchd job finds
/usr/bin/python3 (3.9, absent). Bash commands sidestep the whole problem by
assigning PYTHON="$FW_ROOT/.venv/bin/python3" first; a .py command has nobody to
choose an interpreter for it.

Cost of not doing this, measured: `fw maint unsatisfiable` exited 2 ("could not
verify") on three of four hosts for its entire life — on every machine except
the one it was written on. Exit 2 is the correct could-not-verify code and does
not alarm, so nothing ever looked wrong; the check simply never ran.

`fw install check --commands` finds commands still missing this, and recognises
the call below as the fix.
"""
import os
import sys
from pathlib import Path

_GUARD = "_FW_VENV_REEXEC"


def ensure_venv(script: str, *modules: str) -> None:
    """Re-exec `script` under <repo>/.venv if `modules` are not importable here.

    A no-op when they already import (so the launchd wrappers that invoke
    `.venv/bin/python scripts/lib/... ` directly are unaffected), and a no-op
    when there is no venv — the caller then fails with its own honest
    ImportError rather than a confusing exec error. Guarded by an env var so a
    venv that is itself missing the module cannot loop.
    """
    if os.environ.get(_GUARD):
        return
    missing = []
    for mod in modules:
        try:
            __import__(mod)
        except ImportError:
            missing.append(mod)
    if not missing:
        return
    here = Path(script).resolve()
    for parent in here.parents:
        for cand in (parent / ".venv/bin/python3", parent / ".venv/bin/python"):
            if cand.exists():
                os.environ[_GUARD] = "1"
                os.execv(str(cand), [str(cand), str(here), *sys.argv[1:]])
    return

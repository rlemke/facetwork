# Shared path-resolver for all fw command implementations and the `fw` dispatcher.
# SOURCED, never executed. Sets FW_ROOT (repo root), FW_LIB, and REPO_ROOT (a
# back-compat alias so existing script bodies that use $REPO_ROOT keep working).
#
# Resolution order — must work for: a dev git checkout, a runner image that may
# ship without a .git dir, and invocation via a symlink or from any cwd:
#   1. FW_ROOT_OVERRIDE   (explicit; CI / fixed-layout containers)
#   2. git rev-parse --show-toplevel   (normal dev + fleet hosts)
#   3. walk up to the pyproject.toml sentinel   (no-.git fallback)
#
# Depth-independent: every command lives at scripts/lib/<group>/<cmd> (or one
# level deeper under a nested group), so callers source this via a relative
# path, but the resolution below never relies on that depth.

if [ -z "${FW_ROOT:-}" ]; then
    if [ -n "${FW_ROOT_OVERRIDE:-}" ]; then
        FW_ROOT="$FW_ROOT_OVERRIDE"
    else
        _fw_here="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
        if FW_ROOT="$(git -C "$_fw_here" rev-parse --show-toplevel 2>/dev/null)"; then
            :
        else
            _fw_d="$_fw_here"
            while [ "$_fw_d" != "/" ] && [ ! -f "$_fw_d/pyproject.toml" ]; do
                _fw_d="$(dirname "$_fw_d")"
            done
            FW_ROOT="$_fw_d"
        fi
        unset _fw_here _fw_d
    fi
fi

export FW_ROOT
FW_LIB="$FW_ROOT/scripts/lib"
REPO_ROOT="$FW_ROOT"   # back-compat alias for existing script bodies
export FW_LIB REPO_ROOT

# --- interpreter selection -------------------------------------------------
# Every python-based command file starts with `#!/usr/bin/env python3`, so the
# interpreter is whatever PATH resolves — historically the SYSTEM/Homebrew
# python3, not this repo's venv. That has bitten twice:
#   1. `facetwork`/`pymongo` missing there -> the command dies at import.
#   2. macOS Local Network Privacy is granted PER BINARY. Homebrew moving its
#      `python3` symlink to a new minor (3.12 -> 3.14) produced an UNGRANTED
#      binary, and every LAN connection from it failed with "No route to host"
#      / a silent timeout while the same call from .venv/bin/python succeeded.
#      A CLI cannot raise the permission prompt, so nothing ever re-grants it.
#      Symptom: `fw fleet status` reporting "could not discover MongoDB" against
#      an infra host that is demonstrably up and serving every other client.
# Putting the venv first makes `env python3` resolve to the interpreter the repo
# is actually installed into. Skipped when there is no venv (runner images run
# python from the image), and never clobbers an explicit FW_PYTHON_NO_VENV=1.
if [ -z "${FW_PYTHON_NO_VENV:-}" ] && [ -x "$FW_ROOT/.venv/bin/python3" ]; then
    case ":$PATH:" in
        *":$FW_ROOT/.venv/bin:"*) ;;
        *) PATH="$FW_ROOT/.venv/bin:$PATH"; export PATH ;;
    esac
fi

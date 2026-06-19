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

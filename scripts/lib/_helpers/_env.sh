# Shared environment helper for AgentFlow scripts.
# Source this AFTER _bootstrap.sh (which sets FW_ROOT):
#   source "$(dirname "${BASH_SOURCE[0]}")/../_helpers/_bootstrap.sh"
#   source "$FW_LIB/_helpers/_env.sh"
#
# Loads .env (without overriding already-set vars) and exports
# _compute_compose_args which populates FW_COMPOSE_FILES and FW_PROFILE_ARGS.

# Ensure FW_ROOT is set even if a caller sources us directly (idempotent).
[ -z "${FW_ROOT:-}" ] && source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_bootstrap.sh"
_ENV_PROJECT_DIR="$FW_ROOT"

# Load .env from project root (set only vars that are not already set)
if [ -f "$_ENV_PROJECT_DIR/.env" ]; then
    while IFS='=' read -r _key _value; do
        # Skip comments and blank lines
        [[ -z "$_key" || "$_key" == \#* ]] && continue
        # Strip leading/trailing whitespace from key
        _key="$(echo "$_key" | xargs)"
        # Only set if not already in environment
        if [ -z "${!_key+x}" ]; then
            export "$_key=$_value"
        fi
    done < "$_ENV_PROJECT_DIR/.env"
fi

# Env-var prefix: FW_ is the ONLY accepted prefix (the AFL_ -> FW_ migration is
# done; the mirror shim is retired). A lingering AFL_* var is unsupported and its
# value is IGNORED — warn loudly so it gets renamed. Mirrors the Python detector in
# facetwork/envcompat.py. bash 3.2-safe (macOS).
_fw_warn_legacy_env() {
    command -v compgen >/dev/null 2>&1 || return 0
    local legacy
    # `|| true`: with no AFL_* set, grep exits 1 — under a caller's `set -o pipefail`
    # + `set -e` (sourced into their shell) that would abort the whole command.
    legacy="$(compgen -v 2>/dev/null | grep '^AFL_' | tr '\n' ' ' || true)"
    [ -n "$legacy" ] && echo "WARNING: unsupported legacy AFL_* env var(s) set (rename to FW_*; ignored): $legacy" >&2
    return 0
}
_fw_warn_legacy_env

# Auto-fallback: if FW_MONGODB_URL is unreachable, try localhost.
# Only runs the check if a Python interpreter is available.
_PYTHON="${_ENV_PROJECT_DIR}/.venv/bin/python3"
[[ -x "$_PYTHON" ]] || _PYTHON=python3
if command -v "$_PYTHON" &>/dev/null 2>&1 && "$_PYTHON" -c "import pymongo" 2>/dev/null; then
    _mongo_ok() {
        "$_PYTHON" -c "
from pymongo import MongoClient; import sys, os
try:
    MongoClient(os.environ.get('FW_MONGODB_URL','mongodb://localhost:27017'), serverSelectionTimeoutMS=2000).server_info()
except Exception:
    sys.exit(1)
" 2>/dev/null
    }
    if ! _mongo_ok; then
        _FW_ORIG_URL="${FW_MONGODB_URL:-}"
        export FW_MONGODB_URL="mongodb://localhost:27017"
        if _mongo_ok; then
            echo "MongoDB at ${_FW_ORIG_URL:-<unset>} unreachable, using localhost" >&2
        else
            # Restore original — let downstream scripts handle the error
            if [ -n "$_FW_ORIG_URL" ]; then
                export FW_MONGODB_URL="$_FW_ORIG_URL"
            fi
        fi
    fi
fi

# Compute compose file args and profile args from active overlay state.
# Sets: FW_COMPOSE_FILES, FW_PROFILE_ARGS
_compute_compose_args() {
    FW_COMPOSE_FILES="-f docker-compose.yml"
    FW_PROFILE_ARGS=""

    if [ "${FW_HDFS:-false}" = true ]; then
        FW_COMPOSE_FILES="$FW_COMPOSE_FILES -f docker-compose.hdfs.yml"
        FW_PROFILE_ARGS="$FW_PROFILE_ARGS --profile hdfs"
    fi
    if [ "${FW_JENKINS:-false}" = true ]; then
        FW_PROFILE_ARGS="$FW_PROFILE_ARGS --profile jenkins"
    fi
}

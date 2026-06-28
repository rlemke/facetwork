# Shared environment helper for AgentFlow scripts.
# Source this AFTER _bootstrap.sh (which sets FW_ROOT):
#   source "$(dirname "${BASH_SOURCE[0]}")/../_helpers/_bootstrap.sh"
#   source "$FW_LIB/_helpers/_env.sh"
#
# Loads .env (without overriding already-set vars) and exports
# _compute_compose_args which populates AFL_COMPOSE_FILES and AFL_PROFILE_ARGS.

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

# Env-var prefix compat: the platform is migrating AFL_* -> FW_* (matching the
# `fw` CLI / `fw_*` MCP tools / `fw:` tasks). Mirror the two prefixes so either
# works during the transition; FW_ is canonical and wins on a collision. Mirrors
# the Python shim in facetwork/envcompat.py. bash 3.2-safe (macOS).
_fw_normalize_env() {
    command -v compgen >/dev/null 2>&1 || return 0
    local v base canon canon_set
    # FW_ wins: push every FW_X down onto AFL_X (skip internal bootstrap vars).
    for v in $(compgen -v 2>/dev/null | grep '^FW_'); do
        case "$v" in FW_ROOT|FW_LIB) continue;; esac
        base="${v#FW_}"
        export "AFL_${base}=${!v}"
    done
    # Fill gaps: mirror any lone AFL_X up to FW_X.
    for v in $(compgen -v 2>/dev/null | grep '^AFL_'); do
        base="${v#AFL_}"
        canon="FW_${base}"
        eval "canon_set=\${${canon}+x}"
        [ -z "$canon_set" ] && export "${canon}=${!v}"
    done
}
_fw_normalize_env

# Auto-fallback: if AFL_MONGODB_URL is unreachable, try localhost.
# Only runs the check if a Python interpreter is available.
_PYTHON="${_ENV_PROJECT_DIR}/.venv/bin/python3"
[[ -x "$_PYTHON" ]] || _PYTHON=python3
if command -v "$_PYTHON" &>/dev/null 2>&1 && "$_PYTHON" -c "import pymongo" 2>/dev/null; then
    _mongo_ok() {
        "$_PYTHON" -c "
from pymongo import MongoClient; import sys, os
try:
    MongoClient(os.environ.get('AFL_MONGODB_URL','mongodb://localhost:27017'), serverSelectionTimeoutMS=2000).server_info()
except Exception:
    sys.exit(1)
" 2>/dev/null
    }
    if ! _mongo_ok; then
        _AFL_ORIG_URL="${AFL_MONGODB_URL:-}"
        export AFL_MONGODB_URL="mongodb://localhost:27017"
        if _mongo_ok; then
            echo "MongoDB at ${_AFL_ORIG_URL:-<unset>} unreachable, using localhost" >&2
        else
            # Restore original — let downstream scripts handle the error
            if [ -n "$_AFL_ORIG_URL" ]; then
                export AFL_MONGODB_URL="$_AFL_ORIG_URL"
            fi
        fi
    fi
fi

# Compute compose file args and profile args from active overlay state.
# Sets: AFL_COMPOSE_FILES, AFL_PROFILE_ARGS
_compute_compose_args() {
    AFL_COMPOSE_FILES="-f docker-compose.yml"
    AFL_PROFILE_ARGS=""

    if [ "${AFL_HDFS:-false}" = true ]; then
        AFL_COMPOSE_FILES="$AFL_COMPOSE_FILES -f docker-compose.hdfs.yml"
        AFL_PROFILE_ARGS="$AFL_PROFILE_ARGS --profile hdfs"
    fi
    if [ "${AFL_JENKINS:-false}" = true ]; then
        AFL_PROFILE_ARGS="$AFL_PROFILE_ARGS --profile jenkins"
    fi
}

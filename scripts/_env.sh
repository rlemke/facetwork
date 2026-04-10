# Shared environment helper for AgentFlow scripts.
# Source this at the top of every script:
#   source "$(dirname "$0")/_env.sh"
#
# Loads .env (without overriding already-set vars) and exports
# _compute_compose_args which populates AFL_COMPOSE_FILES and AFL_PROFILE_ARGS.

_ENV_PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

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
    if [ "${AFL_POSTGIS:-false}" = true ]; then
        AFL_COMPOSE_FILES="$AFL_COMPOSE_FILES -f docker-compose.postgis.yml"
        AFL_PROFILE_ARGS="$AFL_PROFILE_ARGS --profile postgis"
    fi
    if [ "${AFL_JENKINS:-false}" = true ]; then
        AFL_PROFILE_ARGS="$AFL_PROFILE_ARGS --profile jenkins"
    fi
    if [ -n "${AFL_GEOFABRIK_MIRROR:-}" ]; then
        AFL_COMPOSE_FILES="$AFL_COMPOSE_FILES -f docker-compose.mirror.yml"
    fi
}

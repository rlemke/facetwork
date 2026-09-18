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

        # 1. THE SERVER CATALOG FIRST. A name like afl-mongodb failing to resolve
        #    almost always means a stale /etc/hosts, not a missing database: the
        #    catalog tracks the current IP so DHCP drift self-heals without root.
        #    Measured on MaxPro 2026-09-14 — /etc/hosts still pointed afl-mongodb
        #    at a host that had not held it since the move, while the catalog had
        #    the right address all along.
        # URL passed as argv, NOT via the environment: _FW_ORIG_URL is a plain
        # shell variable here, and reading it with os.environ silently yielded
        # None -- the lookup "ran" and always declined.
        # ⚠️ TWO defects lived in this one assignment, and together they cost
        # 94 silent reconcile failures on macmini02 (2026-09-18).
        #
        # 1. `|| _FW_CAT_URL=""`. The snippet exits 1 to mean "I could not
        #    resolve this" -- a normal, expected outcome. But a command
        #    substitution ASSIGNMENT that fails is a failing command, so under
        #    the caller's `set -e` a declined lookup KILLED THE CALLER. It did so
        #    before printing anything, which is why the fleet agent reported only
        #    "start-worker failed (exit 1)" with both streams empty.
        # 2. Stdlib only -- do NOT import facetwork here. This runs under the
        #    fleet agent's minimal environment where $_PYTHON falls back to the
        #    system interpreter, and `from facetwork.servers import catalog` then
        #    fails on a TRANSITIVE dependency (lark), so PYTHONPATH does not
        #    rescue it. Same lesson as _catalog_ip() in scripts/lib/runner/start:
        #    a diagnostic must have fewer dependencies than what it diagnoses.
        _FW_CAT_URL="$("$_PYTHON" -c '
import json, os, re, socket, sys

m = re.match(r"^(mongodb://)([^/:,?]+)(.*)$", sys.argv[1] if len(sys.argv) > 1 else "")
if not m:
    sys.exit(1)
host = m.group(2)
root = os.environ.get("FW_ROOT") or os.getcwd()
path = os.environ.get("FW_SERVERS_FILE") or os.path.join(root, "servers.json")
try:
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
except Exception:
    sys.exit(1)
entries = doc.get("servers") or doc
rows = entries.values() if isinstance(entries, dict) else entries
ip = None
for e in rows:
    if not isinstance(e, dict) or e.get("joined") is False:
        continue          # a host deliberately out of the fleet is not an answer
    if host == e.get("name") or host in (e.get("aliases") or []):
        try:
            ip = e.get("ip_pin") or socket.gethostbyname(e["name"])
        except Exception:
            ip = e.get("ip_pin")
        break
if not ip or ip == host:
    sys.exit(1)
print(m.group(1) + ip + m.group(3))
' "$_FW_ORIG_URL" 2>/dev/null)" || _FW_CAT_URL=""
        if [ -n "$_FW_CAT_URL" ]; then
            export FW_MONGODB_URL="$_FW_CAT_URL"
            if _mongo_ok; then
                echo "MongoDB: ${_FW_ORIG_URL} did not resolve; using the server catalog -> ${_FW_CAT_URL}" >&2
                _FW_ORIG_URL=""            # resolved; no further fallback
            else
                export FW_MONGODB_URL="$_FW_ORIG_URL"
            fi
        fi

        # 2. Only then localhost — and LOUDLY. This does not reconnect you to the
        #    same cluster, it points you at a DIFFERENT WORLD: a host running its
        #    own standalone mongod has separate workflows, runs and tasks. That
        #    has already caused a dashboard to serve a 4-day-stale universe while
        #    looking perfectly healthy, so it must never read as a mere retry.
        if [ -n "$_FW_ORIG_URL" ] || [ -z "${FW_MONGODB_URL:-}" ]; then
            if ! _mongo_ok; then
                _FW_SAVED="${FW_MONGODB_URL:-}"
                export FW_MONGODB_URL="mongodb://localhost:27017"
                if _mongo_ok; then
                    echo "WARNING: MongoDB at ${_FW_ORIG_URL:-<unset>} is unreachable and the server" >&2
                    echo "         catalog could not resolve it either. Falling back to LOCALHOST," >&2
                    echo "         which is a SEPARATE database — not the fleet's. Anything you" >&2
                    echo "         submit or read here is invisible to the fleet." >&2
                elif [ -n "$_FW_SAVED" ]; then
                    # Restore — let downstream scripts report the real endpoint.
                    export FW_MONGODB_URL="$_FW_SAVED"
                else
                    # ⚠️ Nothing to restore: FW_MONGODB_URL was never set (a fleet
                    # host carries .env.fleet, not .env, so this is NORMAL there).
                    # The old code left the probe's own localhost value in place
                    # and said nothing, so every downstream script silently
                    # addressed a CLOSED local port and reported it as the fleet's
                    # database. Measured on macmini02 2026-09-18. Unset it and say
                    # so: no endpoint is an answerable state, a wrong one is not.
                    unset FW_MONGODB_URL
                    echo "MongoDB: no endpoint configured (no .env here) and localhost is closed." >&2
                    echo "         Leaving FW_MONGODB_URL UNSET rather than pointing at a dead port." >&2
                    echo "         Fleet hosts get theirs from fleet_config; pass --mongo, or set it in .env." >&2
                fi
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

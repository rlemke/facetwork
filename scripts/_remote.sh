# Shared helpers for remote runner management.
# Source this after _env.sh:
#   source "$(dirname "$0")/_remote.sh"
#
# Provides:
#   _afl_resolve_remote_env    — resolve AFL_RUNNER_HOSTS, AFL_REMOTE_PATH, etc.
#   _afl_query_running_servers — query MongoDB for running servers
#   _afl_ssh <host> <cmd>      — SSH wrapper with standard options
#   _afl_poll_server_state     — poll MongoDB until server reaches expected state
#   _afl_poll_new_server       — poll until a new server appears on a hostname

_REMOTE_SH_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_REMOTE_REPO_ROOT="$(cd "$_REMOTE_SH_DIR/.." && pwd)"
_REMOTE_PYTHON="${_REMOTE_REPO_ROOT}/.venv/bin/python3"
[[ -x "$_REMOTE_PYTHON" ]] || _REMOTE_PYTHON=python3

# ---------------------------------------------------------------------------
# Environment resolution
# ---------------------------------------------------------------------------

_afl_resolve_remote_env() {
    # AFL_RUNNER_HOSTS: space-separated list of remote hostnames
    AFL_RUNNER_HOSTS="${AFL_RUNNER_HOSTS:-}"

    # AFL_REMOTE_PATH: repo path on remote hosts (default: same as local)
    AFL_REMOTE_PATH="${AFL_REMOTE_PATH:-$_REMOTE_REPO_ROOT}"

    # AFL_SSH_OPTS: extra SSH options
    AFL_SSH_OPTS="${AFL_SSH_OPTS:-}"
}

# ---------------------------------------------------------------------------
# SSH wrapper
# ---------------------------------------------------------------------------

_afl_ssh() {
    local host="$1"; shift
    # shellcheck disable=SC2086
    ssh -o BatchMode=yes -o ConnectTimeout=5 -o StrictHostKeyChecking=accept-new \
        $AFL_SSH_OPTS "$host" "$@"
}

# ---------------------------------------------------------------------------
# MongoDB queries (inline Python, outputs to stdout)
# ---------------------------------------------------------------------------

# Query running servers. Output: one line per server:
#   server_name http_port uuid
_afl_query_running_servers() {
    env PYTHONPATH="$_REMOTE_REPO_ROOT" "$_REMOTE_PYTHON" -c "
import os
from facetwork.runtime.mongo_store import MongoStore
mongo_url = os.environ.get('AFL_MONGODB_URL', 'mongodb://afl-mongodb:27017')
store = MongoStore(mongo_url)
for s in store.get_servers_by_state('running'):
    print(f'{s.server_name} {s.http_port} {s.uuid}')
"
}

# Get state of a specific server by UUID. Output: state string or empty.
_afl_get_server_state() {
    local server_uuid="$1"
    env PYTHONPATH="$_REMOTE_REPO_ROOT" "$_REMOTE_PYTHON" -c "
import os
from facetwork.runtime.mongo_store import MongoStore
mongo_url = os.environ.get('AFL_MONGODB_URL', 'mongodb://afl-mongodb:27017')
store = MongoStore(mongo_url)
s = store.get_server('$server_uuid')
if s:
    print(s.state)
"
}

# ---------------------------------------------------------------------------
# Polling helpers
# ---------------------------------------------------------------------------

# Poll until a server reaches the expected state (or timeout).
# Usage: _afl_poll_server_state <uuid> <expected_state> <timeout_seconds>
# Returns 0 on success, 1 on timeout.
_afl_poll_server_state() {
    local uuid="$1"
    local expected="$2"
    local timeout="${3:-30}"
    local elapsed=0

    while [[ $elapsed -lt $timeout ]]; do
        local state
        state=$(_afl_get_server_state "$uuid")
        if [[ "$state" == "$expected" ]]; then
            return 0
        fi
        sleep 2
        elapsed=$((elapsed + 2))
    done
    return 1
}

# Poll until a NEW server appears on a given hostname with expected state.
# Ignores servers whose UUIDs are in the exclude list.
# Usage: _afl_poll_new_server <hostname> <expected_state> <timeout_seconds> [exclude_uuids...]
# On success, prints the new server's "server_name http_port uuid" line.
# Returns 0 on success, 1 on timeout.
_afl_poll_new_server() {
    local hostname="$1"
    local expected="$2"
    local timeout="${3:-60}"
    shift 3
    local -a exclude=("$@")
    local elapsed=0

    while [[ $elapsed -lt $timeout ]]; do
        local line
        while IFS= read -r line; do
            local srv_name srv_port srv_uuid
            read -r srv_name srv_port srv_uuid <<< "$line"
            if [[ "$srv_name" == "$hostname" ]]; then
                # Check it's not in the exclude list
                local found=false
                for ex in "${exclude[@]+"${exclude[@]}"}"; do
                    if [[ "$ex" == "$srv_uuid" ]]; then
                        found=true
                        break
                    fi
                done
                if [[ "$found" == "false" ]]; then
                    echo "$line"
                    return 0
                fi
            fi
        done <<< "$(_afl_query_running_servers)"

        sleep 2
        elapsed=$((elapsed + 2))
    done
    return 1
}

# ---------------------------------------------------------------------------
# Host list helpers
# ---------------------------------------------------------------------------

# Build the list of target hosts from --host flags or AFL_RUNNER_HOSTS.
# Usage: _afl_resolve_hosts "${HOST_ARGS[@]}"
# Prints one hostname per line.
_afl_resolve_hosts() {
    if [[ $# -gt 0 ]]; then
        printf '%s\n' "$@"
    elif [[ -n "$AFL_RUNNER_HOSTS" ]]; then
        # shellcheck disable=SC2086
        printf '%s\n' $AFL_RUNNER_HOSTS
    else
        echo "Error: no hosts specified. Use --host or set AFL_RUNNER_HOSTS." >&2
        return 1
    fi
}

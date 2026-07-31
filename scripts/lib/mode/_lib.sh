# Shared helpers for the `fw mode` group (day-cluster / night-local switch).
# SOURCED by mode/* commands AFTER _bootstrap.sh sets FW_ROOT.
#
# Two independent switches, deliberately separated (see docs/operations/fw-mode.md):
#   Model A  join / leave     — contribute this machine to the cluster as a runner,
#                               or stop; NO infra change (drain + reaper re-claim).
#   Model B  local / cluster  — flip WHERE infra (Mongo/MinIO/registry) lives and
#                               recreate the runners against it. Needs a local
#                               deployment for `local`.
#
# ⚠️ Model B does NOT merge state: local and cluster are separate Mongo databases
# and separate object stores. Switching gives you that world's runs, not both.

_MODE_MARKER="$FW_ROOT/.fw-mode"
_MODE_PY="${FW_ROOT}/.venv/bin/python3"; [ -x "$_MODE_PY" ] || _MODE_PY=python3

_mode_active() { [ -f "$_MODE_MARKER" ] && cat "$_MODE_MARKER" || echo "unknown"; }
_mode_profile_path() { echo "$FW_ROOT/mode.$1.json"; }

# _mode_pget <profile> <key> — read a scalar key from mode.<profile>.json (empty if absent/null)
_mode_pget() {
    local f; f="$(_mode_profile_path "$1")"
    [ -f "$f" ] || { echo ""; return 0; }
    "$_MODE_PY" - "$f" "$2" <<'PY'
import json, sys
try:
    d = json.load(open(sys.argv[1]))
    v = d.get(sys.argv[2])
    print("" if v is None else v)
except Exception:
    print("")
PY
}

# _env_upsert <file> <KEY> <VALUE> — set KEY=VALUE (replace the last uncommented
# assignment, else append). Python, not sed: BSD sed's missing GNU features have
# silently no-op'd env edits before (maxpro-standalone §3.6).
_env_upsert() {
    local file="$1" key="$2" val="$3"
    [ -f "$file" ] || : > "$file"
    "$_MODE_PY" - "$file" "$key" "$val" <<'PY'
import sys
file, key, val = sys.argv[1], sys.argv[2], sys.argv[3]
lines = open(file).read().splitlines()
out, done = [], False
for ln in reversed(lines):                       # replace the LAST assignment
    s = ln.lstrip()
    if not done and s.startswith(key + "=") and not s.startswith("#"):
        out.append(f"{key}={val}"); done = True
    else:
        out.append(ln)
out.reverse()
if not done:
    if out and out[-1].strip() != "": out.append("")
    out.append(f"{key}={val}")
open(file, "w").write("\n".join(out) + "\n")
PY
}

# _mode_resolve_ip <host> — current IPv4 for a hostname (mDNS/DNS), empty on failure.
_mode_resolve_ip() {
    local host="$1" ip=""
    ip="$(dscacheutil -q host -a name "$host" 2>/dev/null | awk '/^ip_address:/{print $2; exit}')"
    [ -z "$ip" ] && ip="$(ping -c1 -t1 "$host" 2>/dev/null | awk -F'[()]' '/PING/{print $2; exit}')"
    echo "$ip"
}

# _mode_mongo_reachable <ip_or_host> — true if TCP :27017 answers within 3s.
_mode_mongo_reachable() { nc -z -G 3 "$1" 27017 >/dev/null 2>&1; }

# _mode_runner_containers — names of this host's runner containers.
_mode_runner_containers() { docker ps -a --format '{{.Names}}' 2>/dev/null | grep -E '^facetwork-runner-' || true; }

# _mode_set_hosts <ip> — point afl-mongodb/afl-minio at <ip> in /etc/hosts. Host-side
# only (the fw CLI + `mc`); runner CONTAINERS get afl-* from compose extra_hosts, so
# this is a convenience, not load-bearing. Needs sudo; afl-postgres is left ALONE
# (it lives on a different machine — maxpro-standalone §3.6). Python, never sed.
_mode_set_hosts() {
    local ip="$1"; [ -z "$ip" ] && return 0
    read -r -d '' _hp <<'PY' || true
import sys
ip = sys.argv[1]
p = "/etc/hosts"
lines = open(p).read().splitlines()
out, done = [], False
for ln in lines:
    toks = ln.split()
    names = toks[1:] if toks and not toks[0].startswith("#") else []
    if ("afl-mongodb" in names or "afl-minio" in names) and "afl-postgres" not in names:
        if not done:
            out.append(f"{ip}\tafl-mongodb afl-minio"); done = True
        # drop any other afl-mongodb/afl-minio lines
    else:
        out.append(ln)
if not done:
    out.append(f"{ip}\tafl-mongodb afl-minio")
open(p, "w").write("\n".join(out) + "\n")
PY
    if sudo -n true 2>/dev/null; then
        printf '%s' "$_hp" | sudo "$_MODE_PY" - "$ip" && echo "  /etc/hosts: afl-mongodb/afl-minio -> $ip"
    else
        echo "  /etc/hosts needs sudo (skipped) — for host-side 'mc'/CLI, set manually:"
        echo "      $ip  afl-mongodb afl-minio   (leave afl-postgres alone)"
    fi
}

# _mode_apply <target> [dry] — the Model B switch: resolve+guard infra, rewrite env,
# (dis)able the local server catalog, set /etc/hosts, recreate runners, stamp marker.
_mode_apply() {
    local target="$1" dry="${2:-0}"
    [ -f "$(_mode_profile_path "$target")" ] || { echo "ERROR: no profile mode.$target.json" >&2; return 1; }

    local infra_host mongo s3 reg data_dir data_root hosts_ip server_catalog require infra_ip
    infra_host="$(_mode_pget "$target" infra_host)"
    mongo="$(_mode_pget "$target" mongodb_url)"
    s3="$(_mode_pget "$target" s3_endpoint)"
    reg="$(_mode_pget "$target" fleet_registry)"
    data_dir="$(_mode_pget "$target" data_dir)"
    data_root="$(_mode_pget "$target" data_root)"
    hosts_ip="$(_mode_pget "$target" hosts_ip)"
    server_catalog="$(_mode_pget "$target" server_catalog)"
    require="$(_mode_pget "$target" require_reachable)"
    infra_ip="$(_mode_pget "$target" infra_ip)"

    [ -z "$infra_ip" ] && infra_ip="$(_mode_resolve_ip "$infra_host")"
    [ "$hosts_ip" = "@resolve" ] && hosts_ip="$infra_ip"

    local reachable=0
    { [ -n "$infra_ip" ] && _mode_mongo_reachable "$infra_ip"; } && reachable=1

    echo "Switch to mode '$target':"
    printf '  %-14s %s\n' infra "$infra_host (${infra_ip:-unresolved})  Mongo:27017 $([ "$reachable" = 1 ] && echo 'reachable ✓' || echo 'UNREACHABLE ✗')"
    printf '  %-14s %s\n' registry "$reg"
    printf '  %-14s %s\n' mongo "$mongo"
    printf '  %-14s %s\n' data_dir "$data_dir"
    printf '  %-14s %s\n' catalog "$server_catalog"

    if [ "$dry" = 1 ]; then
        echo "[dry-run] would: rewrite FW_INFRA_*/FW_MONGODB_URL/FW_S3_ENDPOINT/FW_DATA_*/FW_FLEET_REGISTRY,"
        echo "          $([ "$server_catalog" = none ] && echo 'disable' || echo 'enable') servers.local.json, set /etc/hosts afl-* -> $hosts_ip,"
        echo "          then 'fw fleet agent apply --data-dir $data_dir'. No changes made."
        { [ "$require" = "True" ] || [ "$require" = "true" ]; } && [ "$reachable" != 1 ] && \
            echo "          NOTE: a real switch would REFUSE right now — infra is unreachable."
        return 0
    fi

    # Safety: never switch to an unreachable infra — it would strand this box.
    if { [ "$require" = "True" ] || [ "$require" = "true" ]; } && [ "$reachable" != 1 ]; then
        echo "" >&2
        echo "REFUSING to switch to '$target': infra '$infra_host' is unreachable" >&2
        echo "  (resolved IP: ${infra_ip:-<none>}; Mongo :27017 did not answer within 3s)." >&2
        echo "  Power the cluster on / fix $infra_host, then retry. Nothing changed —" >&2
        echo "  still on '$(_mode_active)'." >&2
        return 2
    fi

    _env_upsert "$FW_ROOT/.env.fleet" FW_INFRA_HOST   "$infra_host"
    _env_upsert "$FW_ROOT/.env.fleet" FW_INFRA_IP     "$infra_ip"
    _env_upsert "$FW_ROOT/.env.fleet" FW_MONGODB_URL  "$mongo"
    _env_upsert "$FW_ROOT/.env.fleet" FW_S3_ENDPOINT  "$s3"
    _env_upsert "$FW_ROOT/.env.fleet" FW_DATA_DIR     "$data_dir"
    _env_upsert "$FW_ROOT/.env.fleet" FW_DATA_ROOT    "$data_root"
    _env_upsert "$FW_ROOT/.env.fleet" FW_FLEET_REGISTRY "$reg"
    _env_upsert "$FW_ROOT/.env"       FW_FLEET_REGISTRY "$reg"

    if [ "$server_catalog" = "none" ]; then
        [ -f "$FW_ROOT/servers.local.json" ] && mv "$FW_ROOT/servers.local.json" "$FW_ROOT/servers.local.json.disabled" \
            && echo "  server catalog: servers.local.json -> .disabled (committed defaults govern)"
    else
        [ -f "$FW_ROOT/servers.local.json.disabled" ] && mv "$FW_ROOT/servers.local.json.disabled" "$FW_ROOT/servers.local.json" \
            && echo "  server catalog: restored servers.local.json"
        [ -f "$FW_ROOT/servers.local.json" ] || echo "  ⚠️ servers.local.json missing — local infra resolution needs it"
    fi

    _mode_set_hosts "$hosts_ip"

    echo "=== reconciling runners against '$target' infra (fleet agent apply) ==="
    FW_DATA_DIR="$data_dir" "$FW_LIB/fleet/agent" apply --data-dir "$data_dir" || {
        echo "ERROR: fleet agent apply failed — env is set but runners were not recreated." >&2
        echo "       Fix the cause and re-run 'fw mode $target', or 'fw mode $(_mode_active)' to revert." >&2
        return 1
    }

    echo "$target" > "$_MODE_MARKER"
    echo
    echo "Now in mode: $target"
    echo "⚠️  '$target' has its OWN Mongo + object store — runs/catalog do NOT merge"
    echo "    with the other mode. You see this world's runs only."
}

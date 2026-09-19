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
    # Resolve a host to an address CONTAINERS can use to reach it.
    #
    # Must skip loopback. A machine's own .local name resolves to both 127.0.0.1
    # and its LAN address, and dscacheutil lists loopback first — so taking the
    # first answer yields 127.0.0.1, which inside a container points at the
    # container itself, not at the host. Every runner then fails to reach Mongo.
    # That is why this host had an IP pinned by hand, and why the pin silently
    # went stale when the machine changed subnet.
    local host="$1" ip=""
    # An afl-* name is a CATALOG ALIAS, not a DNS name -- since the /etc/hosts pins
    # were removed fleet-wide (the catalog resolves each service independently)
    # there is nothing for dscacheutil to answer with, and this used to return
    # empty and report a healthy cluster UNREACHABLE. Ask the catalog first.
    case "$host" in
        afl-*)
            ip="$("$FW_ROOT/.venv/bin/python" -m facetwork.servers --resolve "$host" 2>/dev/null || true)"
            if [ -n "$ip" ]; then echo "$ip"; return 0; fi
            ;;
    esac
    ip="$(dscacheutil -q host -a name "$host" 2>/dev/null \
          | awk '/^ip_address:/ && $2 !~ /^127\./ {print $2; exit}')"
    [ -z "$ip" ] && ip="$(ping -c1 -t1 "$host" 2>/dev/null \
          | awk -F'[()]' '/PING/{print $2; exit}' | grep -v '^127\.' )"
    echo "$ip"
}

# _mode_mongo_reachable <ip_or_host> — true if TCP :27017 answers within 3s.
_mode_mongo_reachable() { nc -z -G 3 "$1" 27017 >/dev/null 2>&1; }

# _mode_container_ip <host> — what CONTAINERS should map afl-* to for <host>:
# Docker's `host-gateway` alias when <host> is this machine (Docker maintains it,
# so it survives this box rebooting onto a new DHCP lease — the recurring staleness
# a pinned IP causes), else the resolved address. Falls back to plain resolution.
_mode_container_ip() {
    local host="$1" v=""
    v="$(cd "$FW_ROOT" && "$_MODE_PY" -m facetwork.servers --container-ip "$host" 2>/dev/null || true)"
    [ -z "$v" ] && v="$(_mode_resolve_ip "$host")"
    echo "$v"
}

# _mode_runner_containers — names of this host's runner containers.
_mode_runner_containers() { docker ps -a --format '{{.Names}}' 2>/dev/null | grep -E '^facetwork-runner-' || true; }

# _mode_set_hosts <ip> — point afl-mongodb/afl-minio at <ip> in /etc/hosts. Host-side
# only (the fw CLI + `mc`); runner CONTAINERS get afl-* from compose extra_hosts, so
# this is a convenience, not load-bearing. Needs sudo; afl-postgres is left ALONE
# (it lives on a different machine — maxpro-standalone §3.6). Python, never sed.
_mode_set_hosts() {
    local ip="$1"; [ -z "$ip" ] && return 0
    # ⚠️ "none" means DO NOT PIN. Writing both names to one IP was correct when a
    # single machine held every service and is now actively wrong -- it would point
    # afl-mongodb at the host that stopped serving Mongo on 2026-09-13. In cluster
    # mode the SERVER CATALOG resolves each name independently, and a pinned entry
    # WINS over it, so pinning re-creates the drift the catalog removed. Local mode
    # still pins 127.0.0.1, where it is genuinely load-bearing (it is what points
    # the host-side CLI at this machine's own Mongo/MinIO instead of the cluster's).
    if [ "$ip" = "none" ]; then
        echo "  /etc/hosts: not pinned (server catalog resolves afl-* per service)"
        return 0
    fi
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

    # What CONTAINERS get (compose extra_hosts) is NOT always what the host
    # probes: when infra is this machine it is Docker's `host-gateway` alias, so
    # the value written to .env.fleet stays correct across reboots onto a new IP.
    local container_ip; container_ip="$(_mode_container_ip "$infra_host")"

    # ⚠️ Probe the MONGO host, not the infra host. Since 2026-09-13 there is no
    # single infra machine: MongoDB moved off it and MinIO stayed, so probing
    # $infra_ip for :27017 asks the wrong box. Measured 2026-09-18: this made
    # `fw mode status` report Mongo UNREACHABLE while the cluster was healthy, and
    # would have made `fw mode cluster` REFUSE on return from a trip -- the exact
    # stranding the guard exists to prevent, caused by the guard itself.
    local mongo_host mongo_ip reachable=0
    mongo_host="$(printf '%s' "$mongo" | sed -E 's|^mongodb://||; s|/.*$||; s|:[0-9]+$||; s|^.*@||; s|,.*$||')"
    # ⚠️ Resolve with the catalog of the mode we are switching INTO, not the one
    # currently active. Measured 2026-09-18 while in local mode: previewing a
    # switch to cluster resolved afl-mongodb through servers.local.json and got
    # THIS MACHINE, so the guard passed by probing MaxPro's own Mongo. On return
    # from a trip with the cluster still powered off it would have passed again and
    # switched anyway -- stranding the box, which is the one thing this guard
    # exists to prevent. A guard that consults the wrong world does not guard.
    local _saved_sf="${FW_SERVERS_FILE:-}"
    case "$server_catalog" in
        none)  export FW_SERVERS_FILE="$FW_ROOT/servers.json" ;;
        local) [ -f "$FW_ROOT/servers.local.json" ] \
                 && export FW_SERVERS_FILE="$FW_ROOT/servers.local.json" \
                 || export FW_SERVERS_FILE="$FW_ROOT/servers.local.json.disabled" ;;
    esac
    mongo_ip="$(_mode_resolve_ip "$mongo_host" 2>/dev/null || true)"
    if [ -n "$_saved_sf" ]; then export FW_SERVERS_FILE="$_saved_sf"; else unset FW_SERVERS_FILE; fi
    # A catalog name resolves through the catalog; an IP resolves to itself.
    [ -z "$mongo_ip" ] && mongo_ip="$mongo_host"
    { [ -n "$mongo_ip" ] && _mode_mongo_reachable "$mongo_ip"; } && reachable=1

    echo "Switch to mode '$target':"
    printf '  %-14s %s\n' infra "$infra_host (${infra_ip:-unresolved})"
    printf '  %-14s %s\n' mongo-probe "$mongo_host (${mongo_ip:-unresolved}):27017 $([ "$reachable" = 1 ] && echo 'reachable ✓' || echo 'UNREACHABLE ✗')"
    printf '  %-14s %s\n' containers "afl-* -> ${container_ip:-unresolved}"
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
    _env_upsert "$FW_ROOT/.env.fleet" FW_INFRA_IP     "${container_ip:-$infra_ip}"
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

    # Source per-host API-key secrets so recreated runners keep them. The launchd
    # fleet-agent wrapper sources this before applying; a `fw mode` switch must too,
    # or the reconcile silently strips CENSUS_API_KEY / ANTHROPIC_API_KEY etc. from
    # every runner (which broke census/h1b maps until re-applied).
    if [ -f "$HOME/.facetwork/fleet-secrets.env" ]; then
        set -a; . "$HOME/.facetwork/fleet-secrets.env"; set +a
        echo "  sourced fleet-secrets.env (API keys → runners)"
    fi

    # ⚠️ RESTART THE LONG-RUNNING AGENT FIRST, or it undoes this switch.
    # The agent resolves its Mongo ONCE at start and then builds every runner's
    # compose environment from `fleet_config` in THAT database -- it never reads
    # .env.fleet (see CLAUDE.md). So a mode switch that only rewrites .env.fleet
    # leaves a daemon still pointed at the old world, which re-reconciles the
    # runners straight back. Measured 2026-09-18: after `fw mode local`,
    # **19 of 23 runners still had FW_MONGODB_URL pointing at the CLUSTER**, which
    # was about to be powered off for a week. The one-shot `agent apply` below
    # cannot fix that on its own; the daemon has to be re-exec'd so it re-resolves
    # afl-mongodb through the newly-active catalog.
    echo "=== restarting the fleet-agent so it re-resolves infra for '$target' ==="
    if [ "$(uname -s)" = "Darwin" ]; then
        if launchctl kickstart -k "gui/$(id -u)/com.facetwork.fleet-agent" 2>/dev/null; then
            echo "  fleet-agent restarted (launchd)"
        else
            echo "  NOTE: no launchd fleet-agent to restart (or it is not loaded)."
        fi
    else
        if systemctl restart facetwork-fleet-agent 2>/dev/null; then
            echo "  fleet-agent restarted (systemd)"
        else
            echo "  NOTE: could not restart facetwork-fleet-agent — it may re-apply the" >&2
            echo "        PREVIOUS mode. Run: sudo systemctl restart facetwork-fleet-agent" >&2
        fi
    fi

    echo "=== reconciling runners against '$target' infra (fleet agent apply) ==="
    FW_DATA_DIR="$data_dir" "$FW_LIB/fleet/agent" apply --data-dir "$data_dir" || {
        echo "ERROR: fleet agent apply failed — env is set but runners were not recreated." >&2
        echo "       Fix the cause and re-run 'fw mode $target', or 'fw mode $(_mode_active)' to revert." >&2
        return 1
    }

    # Verify the OUTCOME, not the intent: count runners whose compose env actually
    # points at this mode's Mongo. A switch that reports success while most runners
    # address the other world is the failure this check exists to catch.
    local want_ok=0 want_bad=0 _u
    for _c in $(_mode_runner_containers); do
        _u="$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$_c" 2>/dev/null \
              | grep -E '^FW_MONGODB_URL=' | cut -d= -f2-)"
        case "$_u" in
            *"${mongo_ip:-@@none@@}"*|*"$mongo_host"*|*host-gateway*|*host.docker.internal*)
                want_ok=$((want_ok+1)) ;;
            "") : ;;
            *) want_bad=$((want_bad+1)) ;;
        esac
    done
    printf '  runners addressing %s: %d ok' "$target" "$want_ok"
    [ "$want_bad" -gt 0 ] && printf ', %d STILL ON THE OTHER WORLD' "$want_bad"
    echo
    if [ "$want_bad" -gt 0 ]; then
        echo "  ⚠️ $want_bad runner(s) still point elsewhere. The fleet-agent may have" >&2
        echo "     re-applied the previous mode. Re-run 'fw mode $target' once the agent" >&2
        echo "     has restarted, and check: fw mode status" >&2
    fi

    echo "$target" > "$_MODE_MARKER"
    echo
    echo "Now in mode: $target"
    echo "⚠️  '$target' has its OWN Mongo + object store — runs/catalog do NOT merge"
    echo "    with the other mode. You see this world's runs only."
}

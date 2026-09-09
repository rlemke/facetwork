#!/usr/bin/env bash
# Facetwork fleet-agent self-heal wrapper (atopnuc01 - light tier, group "runner").
#
# Linux/systemd counterpart of the launchd wrappers on MaxPro and server3. Same
# contract: export this host's identity, then exec the watch loop, which polls
# fleet_config.version and reconciles on every change.
set -u
export PYTHONUNBUFFERED=1
export FW_FLEET_AGENT_DOCKER_TIMEOUT=1800
export FW_FLEET_AGENT_WATCHDOG_SECONDS=2400

REPO="$HOME/facetwork"
cd "$REPO" || exit 1

# Per-host identity, read from .env.fleet.override rather than duplicated here,
# so there is ONE place per host that says what this machine is. FW_SERVER_GROUP
# is what keeps the heavy OSM tier off this box (2 cores / 6.7 GiB): a europe cut
# has peaked at 18.9 GB, so a mis-set group is an OOM kill, not a slow run.
_get() { sed -n "s/^$1=//p" "$REPO/.env.fleet.override" 2>/dev/null | tail -1; }
FW_SERVER_GROUP="$(_get FW_SERVER_GROUP)"; : "${FW_SERVER_GROUP:=runner}"
FW_DATA_DIR="$(_get FW_DATA_DIR)";         : "${FW_DATA_DIR:=$HOME/fw_data}"
export FW_SERVER_GROUP FW_DATA_DIR

# Pin --mongo to a NAME the CONTAINERS can resolve. afl-mongodb is mapped into
# every runner by extra_hosts, so it survives an infra DHCP change without
# recreating anything; an IP here would have to be rebuilt on every lease.
# See the loopback guard in scripts/lib/fleet/agent for why this is pinned at all.
FW_MONGO_URL="$(sed -n "s/^FW_MONGODB_URL=//p" "$REPO/.env.fleet" 2>/dev/null | tail -1)"
: "${FW_MONGO_URL:=mongodb://afl-mongodb:27017}"
export FW_MONGO_URL

# That name resolves through THIS HOST'S /etc/hosts, which nothing here can
# maintain (it needs root). When the infra host's DHCP lease moves, the entry
# goes stale and every reconcile fails against an address nobody answers --
# after a power outage that is the normal case, not the exception. Say so
# loudly and name the fix, rather than retrying into a dead address in silence.
_infra="$(.venv/bin/python -m facetwork.servers --infra-name 2>/dev/null)"
_want="$(.venv/bin/python -m facetwork.servers --resolve "$_infra" 2>/dev/null)"
_have="$(getent hosts afl-mongodb 2>/dev/null | awk '{print $1; exit}')"
if [ -n "$_want" ] && [ -n "$_have" ] && [ "$_want" != "$_have" ]; then
  echo "fleet-agent-watch: WARNING /etc/hosts maps afl-mongodb to $_have but the"
  echo "fleet-agent-watch: server catalog resolves $_infra to $_want."
  echo "fleet-agent-watch: reconciles will fail until that is fixed. Run:"
  echo "fleet-agent-watch:   sudo sed -i \"s/\\b$_have\\b/$_want/g\" /etc/hosts"
fi

# Wait for Docker rather than failing the unit at boot: systemd can start us
# before dockerd accepts connections even with After=docker.service.
for _ in $(seq 1 60); do docker info >/dev/null 2>&1 && break; sleep 3; done

[ -f "$HOME/.facetwork/fleet-secrets.env" ] && . "$HOME/.facetwork/fleet-secrets.env"
echo "fleet-agent-watch: group=$FW_SERVER_GROUP mongo=$FW_MONGO_URL data-dir=$FW_DATA_DIR"
exec ./fw fleet agent watch --mongo "$FW_MONGO_URL" --data-dir "$FW_DATA_DIR" --interval 30

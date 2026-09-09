#!/usr/bin/env bash
#
# Set up a fresh Ubuntu machine as a Facetwork fleet host.
#
#   curl -fsSLO https://raw.githubusercontent.com/rlemke/facetwork/main/docs/operations/fleet-agent/setup-ubuntu-fleet-host.sh
#   bash setup-ubuntu-fleet-host.sh --dry-run        # review first
#   bash setup-ubuntu-fleet-host.sh
#
# Every non-obvious step below is here because it FAILED somewhere on this fleet.
# The comments say which; do not "simplify" one away without reading it.
#
# Validated against atopnuc01 (Ubuntu 26.04.1 LTS, python 3.14.4, docker.io
# 29.1.3, docker-compose-v2 2.40.3), which joined and survived a reboot unattended.
#
set -euo pipefail

# ---------------------------------------------------------------- parameters
INFRA_HOST="${FW_INFRA_HOST:-server3.local}"   # stable NAME; resolved, never pinned
REGISTRY_PORT="${FW_REGISTRY_PORT:-5050}"
REPO_URL="${FW_REPO_URL:-https://github.com/rlemke/facetwork.git}"
SERVER_GROUP="${FW_SERVER_GROUP:-runner}"      # 'runner' = light tier. See below.
DATA_DIR="${FW_DATA_DIR:-$HOME/fw_data}"
RDP_MODE="${FW_RDP_MODE:-remote-login}"        # remote-login | xrdp | none
DRY=0; JOIN=1; SSH_KEY=""

usage() {
    cat <<USAGE
usage: $0 [options]

  --dry-run              print what would happen, change nothing
  --group NAME           fleet server group (default: runner)
                         'runner' = light tier. 'heavy' opts this host INTO the
                         OSM tier, where a single europe cut has peaked at
                         18.9 GB RSS. Do not set it on a machine that cannot
                         take that; a mis-set group is an OOM kill, not a slow run.
  --data-dir PATH        large LOCAL scratch dir (default: \$HOME/fw_data)
  --infra-host NAME      infra host's stable name (default: server3.local)
  --rdp MODE             remote-login | xrdp | none   (default: remote-login)
  --ssh-key 'ssh-ed25519 AAAA...'   append to authorized_keys
  --no-join              set everything up but do not start the fleet agent
USAGE
}
while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run) DRY=1; shift ;;
        --group) SERVER_GROUP="$2"; shift 2 ;;
        --data-dir) DATA_DIR="$2"; shift 2 ;;
        --infra-host) INFRA_HOST="$2"; shift 2 ;;
        --rdp) RDP_MODE="$2"; shift 2 ;;
        --ssh-key) SSH_KEY="$2"; shift 2 ;;
        --no-join) JOIN=0; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "unknown option: $1" >&2; usage; exit 2 ;;
    esac
done

REPO_DIR="$HOME/facetwork"
say()  { printf '\n=== %s\n' "$*"; }
info() { printf '    %s\n' "$*"; }
warn() { printf '    WARNING: %s\n' "$*" >&2; }
die()  { printf '\nERROR: %s\n' "$*" >&2; exit 1; }
run()  { if [ "$DRY" = 1 ]; then printf '    [dry] %s\n' "$*"; else "$@"; fi; }
# For pipelines/redirection that `run` cannot take verbatim.
sh_run() { if [ "$DRY" = 1 ]; then printf '    [dry] %s\n' "$1"; else bash -c "$1"; fi; }

# ---------------------------------------------------------------- 0. preflight
say "0/10  preflight"
[ -r /etc/os-release ] || die "not a Linux with /etc/os-release"
. /etc/os-release
[ "${ID:-}" = "ubuntu" ] || warn "expected Ubuntu, found ${PRETTY_NAME:-unknown} — continuing"
info "os:   ${PRETTY_NAME:-unknown}"
info "arch: $(uname -m)"
info "host: $(hostname)"
[ "$(id -u)" = 0 ] && die "run as your normal user, not root — the fleet agent runs as you, and \$HOME must be yours"
# A dry run must not need sudo: its whole purpose is letting someone review the
# plan before granting anything, and requiring credentials to print a plan makes
# it unreviewable over a non-interactive ssh session (which is how it was first run).
if [ "$DRY" = 0 ]; then
    sudo -v || die "this script needs sudo"
else
    sudo -n true 2>/dev/null && info "sudo: available" || info "sudo: will be requested on the real run"
fi

# The fleet image is multi-arch (linux/amd64 + linux/arm64). A Mac mini may be
# either: Intel minis are amd64; Apple Silicon under Asahi is arm64. Both are
# supported, but say which so a surprise is visible now rather than at first pull.
case "$(uname -m)" in
    x86_64)  info "will pull linux/amd64 images" ;;
    aarch64) info "will pull linux/arm64 images" ;;
    *) die "unsupported architecture $(uname -m) — the fleet image has amd64 and arm64 only" ;;
esac

# T2 Macs (2018-2020 Intel) need the t2linux kernel for internal disk/keyboard/
# audio. If you are reading this on a machine that installed fine, it is not a T2
# or you already handled it — this only prints a pointer.
if [ -d /sys/class/apple_bce ] || dmesg 2>/dev/null | grep -qi "apple-bce"; then
    info "Apple T2 hardware detected — see t2linux.org if wifi/audio misbehave"
fi

# ---------------------------------------------------------------- 1. packages
say "1/10  base packages"
# python3.N-venv is version-matched and NOT implied by python3: Ubuntu ships
# python3 without ensurepip, so `python3 -m venv` fails with a message about
# apt-installing a package it does not name. Derive N rather than hardcode it.
VENV_PKG="python3-venv"
if command -v python3 >/dev/null 2>&1; then
    VENV_PKG="python3.$(python3 -c 'import sys; print(sys.version_info.minor)')-venv"
fi
info "venv package: $VENV_PKG"
# ⚠️ docker-compose-v2 is a SEPARATE package. docker.io does NOT bring it, and its
# absence presents as "no compose service 'runner-<name>'" for every domain —
# which reads as compose-file drift, not a missing package. This cost real time.
# No python3-pip: it pulls python3-dev/libpython3-dev/zlib1g-dev and ~42 MB of
# build headers that nothing here needs — `python3 -m venv` provides pip INSIDE
# the venv, which is the only pip this host uses.
PKGS=(openssh-server docker.io docker-compose-v2 "$VENV_PKG"
      git curl ca-certificates avahi-daemon libnss-mdns chrony)
run sudo apt-get update -qq
run sudo apt-get install -y "${PKGS[@]}"

# ⚠️ Do NOT install Docker via Homebrew on Linux. Two daemons then fight over
# /var/run/docker.pid and the service fails to start with an opaque
# "control process exited with error code". Seen on atopnuc01.
if command -v brew >/dev/null 2>&1 && brew list docker >/dev/null 2>&1; then
    warn "Homebrew 'docker' is installed and will conflict with docker.io."
    warn "Remove it:  brew uninstall docker    (then re-run this script)"
fi

# ---------------------------------------------------------------- 2. clocks
say "2/10  time synchronisation"
# Task leases, heartbeats and the dead-server reaper are all wall-clock
# comparisons against timestamps written by OTHER hosts. A host with a skewed
# clock gets its live tasks reaped, or holds tasks past their lease — neither
# reports itself as a clock problem.
run sudo systemctl enable --now chrony
if [ "$DRY" = 0 ]; then
    chronyc tracking >/dev/null 2>&1 && info "chrony is tracking a time source" \
        || warn "chrony not yet synchronised — recheck with 'chronyc tracking'"
fi

# ---------------------------------------------------------------- 3. no sleep
say "3/10  disable sleep/suspend"
# ⚠️ A suspended host is INDISTINGUISHABLE FROM A STALLED WORKFLOW. A sleeping
# laptop on this fleet once made a nightly job silently never run, and a 7.4h
# hibernation mid-run looked exactly like a frozen fan-out. Desktop Ubuntu
# suspends on idle by default; a server must not.
run sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target
# The GNOME session has its own idle-suspend setting that ignores the targets above.
if command -v gsettings >/dev/null 2>&1 && [ "$DRY" = 0 ]; then
    gsettings set org.gnome.settings-daemon.plugins.power sleep-inactive-ac-type 'nothing' 2>/dev/null || true
    gsettings set org.gnome.settings-daemon.plugins.power sleep-inactive-battery-type 'nothing' 2>/dev/null || true
    info "GNOME idle-suspend disabled (if a desktop session exists)"
fi

# ---------------------------------------------------------------- 4. ssh
say "4/10  SSH"
run sudo systemctl enable --now ssh
if [ -n "$SSH_KEY" ]; then
    run mkdir -p "$HOME/.ssh"
    run chmod 700 "$HOME/.ssh"
    if [ "$DRY" = 0 ]; then
        touch "$HOME/.ssh/authorized_keys"; chmod 600 "$HOME/.ssh/authorized_keys"
        grep -qxF "$SSH_KEY" "$HOME/.ssh/authorized_keys" \
            || printf '%s\n' "$SSH_KEY" >> "$HOME/.ssh/authorized_keys"
        info "authorized_keys updated"
    else
        info "[dry] would append the given key to ~/.ssh/authorized_keys"
    fi
else
    info "no --ssh-key given; add one later or password auth will be required"
fi
info "reachable as: ssh $USER@$(hostname).local"

# ---------------------------------------------------------------- 5. remote desktop
say "5/10  remote desktop (mode: $RDP_MODE)"
# ⚠️ GNOME ships TWO different RDP services and they are not interchangeable:
#
#   Desktop Sharing (user unit)  — shares the EXISTING session, on its OWN
#       RDP username/password (not your login password). Needs someone logged in
#       locally, and stores its credential in the GNOME keyring, which autologin
#       typically leaves LOCKED. Serves 3389 directly: no redirection.
#
#   Remote Login (system unit)   — authenticates via PAM with your REAL system
#       password, then starts a NEW session and hands the client over using RDP
#       SERVER REDIRECTION. Needs no local session, so it suits a headless
#       server — but the Mac "Windows App" client does not follow that handover
#       and reports "the credentials did not work" AFTER authentication has
#       already succeeded. Measured here: the journal shows "Sending server
#       redirection" then ERRINFO_LOGOFF_BY_USER. It is not a password problem.
#
# So: remote-login is correct for a headless box, PROVIDED you use a client that
# follows redirection (FreeRDP does). Choose xrdp instead if you must use the
# Microsoft/Windows App client, since xrdp serves its own session with no handover.
case "$RDP_MODE" in
  remote-login)
    run sudo apt-get install -y gnome-remote-desktop freerdp3-x11
    run sudo systemctl enable --now gnome-remote-desktop
    info "enabled GNOME Remote Login (system unit) on 3389"
    info "connect with a redirection-capable client, e.g. from a Mac:"
    info "    brew install freerdp"
    info "    sdl-freerdp /v:$(hostname).local /u:$USER /dynamic-resolution +clipboard"
    warn "the Mac 'Windows App' client will FAIL against this mode (see comments)"
    # Remote Login refuses to open a second graphical session for a user who is
    # already logged in locally, and reports that as a credentials failure too.
    if [ "$DRY" = 0 ] && grep -qs "^AutomaticLoginEnable=[Tt]rue" /etc/gdm3/custom.conf; then
        warn "GDM autologin is ON — it creates a local session that BLOCKS Remote Login."
        warn "disable it:  sudo sed -i 's/^AutomaticLoginEnable=True/AutomaticLoginEnable=False/' /etc/gdm3/custom.conf"
    fi
    ;;
  xrdp)
    run sudo apt-get install -y xrdp freerdp3-x11
    run sudo systemctl enable --now xrdp
    run sudo adduser xrdp ssl-cert
    info "enabled xrdp on 3389 — works with the Microsoft/Windows App client"
    warn "xrdp starts its OWN session; it does not show the console session"
    ;;
  none) info "skipped (no RDP server installed)" ;;
  *) die "--rdp must be remote-login, xrdp, or none" ;;
esac

# ---------------------------------------------------------------- 6. infra addresses
say "6/10  infra host + /etc/hosts"
INFRA_IP="$(getent hosts "$INFRA_HOST" 2>/dev/null | awk '{print $1; exit}' || true)"
[ -z "$INFRA_IP" ] && die "cannot resolve $INFRA_HOST — is it powered on and on this LAN?
       (mDNS is provided by avahi-daemon/libnss-mdns, installed above; a fresh
        install may need a moment, or the host may simply be down)"
info "$INFRA_HOST -> $INFRA_IP"
# ⚠️ Containers do NOT read the host's /etc/hosts and Docker's DNS does not
# resolve .local names — the runners reach the infra host through `extra_hosts`
# entries the agent generates from this name. But the AGENT itself (a host
# process) resolves afl-mongodb through /etc/hosts, so both must exist.
for name in afl-mongodb afl-minio afl-postgres afl-extracts; do
    if grep -qE "^[0-9.]+[[:space:]].*\b${name}\b" /etc/hosts 2>/dev/null; then
        cur="$(awk -v n="$name" '$0 !~ /^#/ && $0 ~ n {print $1; exit}' /etc/hosts)"
        if [ "$cur" != "$INFRA_IP" ]; then
            info "updating $name: $cur -> $INFRA_IP"
            # Escape the dots: unescaped they are regex wildcards, so
            # 192.168.68.115 would also match 192.168.68x115. Harmless here by
            # luck, wrong in principle, and this file is a template others copy.
            cur_re="$(printf '%s' "$cur" | sed 's/\./\\./g')"
            sh_run "sudo sed -i 's/^${cur_re}\\([[:space:]]\\)/${INFRA_IP}\\1/' /etc/hosts"
        fi
    else
        sh_run "printf '%s\\t%s\\n' '$INFRA_IP' '$name' | sudo tee -a /etc/hosts >/dev/null"
    fi
done
info "afl-* now point at $INFRA_IP"

# ---------------------------------------------------------------- 7. docker
say "7/10  Docker"
# ⚠️ The image registry is plain HTTP. Without this the pull fails with
# "http: server gave HTTP response to HTTPS client", ZERO runners start, and the
# preflight still passes — so the host looks joined and does nothing.
DAEMON_JSON=/etc/docker/daemon.json
REG="${INFRA_HOST}:${REGISTRY_PORT}"
if [ "$DRY" = 0 ]; then
    sudo mkdir -p /etc/docker
    # Merge rather than clobber: this file may hold log/storage settings.
    sudo python3 - "$DAEMON_JSON" "$REG" "${INFRA_IP}:${REGISTRY_PORT}" "$REGISTRY_PORT" <<'PYEOF'
import json, re, sys, pathlib
p, name_reg, ip_reg, port = sys.argv[1:]
path = pathlib.Path(p)
try:
    cfg = json.loads(path.read_text())
except Exception:
    cfg = {}
cur = list(cfg.get("insecure-registries") or [])

# ⚠️ PRUNE stale IP entries for this registry port before adding the current one.
# Naively appending accumulates one address per DHCP lease the infra host has
# ever had (measured: server3.local:5050 + .67:5050 + .112:5050 on one host).
# That is not merely untidy -- each entry grants plain-HTTP trust to whatever
# machine holds that address TODAY, and a released lease gets reassigned. Keep
# the NAME (which follows the host) plus exactly the current IP.
stale = [r for r in cur
         if re.fullmatch(rf"\d{{1,3}}(?:\.\d{{1,3}}){{3}}:{re.escape(port)}", r)
         and r != ip_reg]
if stale:
    print(f"    pruning stale registry addresses: {stale}")
cur = [r for r in cur if r not in stale]
for r in (name_reg, ip_reg):
    if r not in cur:
        cur.append(r)
cfg["insecure-registries"] = cur
path.write_text(json.dumps(cfg, indent=2) + "\n")
print(f"    insecure-registries: {cur}")
PYEOF
    sudo systemctl restart docker
else
    info "[dry] would add $REG to $DAEMON_JSON and restart docker"
fi
# Socket access comes from the docker group; without it every reconcile fails
# with a permission error that reads like a missing binary.
if ! id -nG "$USER" | tr ' ' '\n' | grep -qx docker; then
    run sudo usermod -aG docker "$USER"
    warn "added $USER to the 'docker' group — you must LOG OUT AND BACK IN"
    warn "(or run 'newgrp docker') before the rest of this script can use docker"
fi

# ---------------------------------------------------------------- 8. repo + venv
say "8/10  repo + python environment"
if [ -d "$REPO_DIR/.git" ]; then
    info "$REPO_DIR exists — updating"
    run git -C "$REPO_DIR" fetch -q origin
    run git -C "$REPO_DIR" reset -q --hard origin/main
else
    run git clone -q "$REPO_URL" "$REPO_DIR"
fi
run mkdir -p "$DATA_DIR"
if [ "$DRY" = 0 ]; then
    cd "$REPO_DIR"
    [ -d .venv ] || python3 -m venv .venv
    ./.venv/bin/python -m pip install -q --upgrade pip
    ./.venv/bin/python -m pip install -q -e ".[dashboard,mcp,s3]" 2>/dev/null \
        || ./.venv/bin/python -m pip install -q -e .
    info "venv: $(./.venv/bin/python -V)"
fi

# ---------------------------------------------------------------- 9. fleet config
say "9/10  fleet configuration"
# .env.fleet is the SHARED preset, copied verbatim and never hand-edited, so a
# later `git pull && cp .env.fleet.preset .env.fleet` cannot clobber this host's
# values. Per-host values go in the gitignored override, which wins on top.
if [ "$DRY" = 0 ]; then
    cd "$REPO_DIR"
    [ -f .env.fleet.preset ] && cp .env.fleet.preset .env.fleet
    cat > .env.fleet.override <<OVERRIDE
# Per-server values for $(hostname). Gitignored; wins over .env.fleet.
# FW_SERVER_GROUP decides which ROLES this host starts. 'runner' is the light
# tier; 'heavy' opts into the OSM tier, where one europe cut has peaked at
# 18.9 GB RSS. A mis-set group is an OOM kill, not a slow run.
FW_SERVER_GROUP=$SERVER_GROUP
FW_DATA_DIR=$DATA_DIR
FW_OSM_REPLICAS=1
FW_FLEET_HOST=$(hostname -s)
OVERRIDE
    info "group=$SERVER_GROUP  data-dir=$DATA_DIR"
else
    info "[dry] would write .env.fleet + .env.fleet.override (group=$SERVER_GROUP)"
fi

# ---------------------------------------------------------------- 10. agent
say "10/10  fleet agent (systemd)"
TMPL="$REPO_DIR/docs/operations/fleet-agent"
if [ "$DRY" = 0 ]; then
    [ -f "$TMPL/fleet-agent-watch.sh" ] || TMPL_SH="$TMPL/fleet-agent-watch.linux.sh"
    TMPL_SH="${TMPL_SH:-$TMPL/fleet-agent-watch.linux.sh}"
    [ -f "$TMPL_SH" ] || die "wrapper template missing at $TMPL_SH"
    mkdir -p "$HOME/.facetwork"
    install -m 755 "$TMPL_SH" "$HOME/.facetwork/fleet-agent-watch.sh"
    # The unit template hardcodes a user; rewrite it for whoever runs this.
    sed -e "s|^User=.*|User=$USER|" \
        -e "s|^Group=.*|Group=$(id -gn)|" \
        -e "s|/home/[^/]*/|$HOME/|g" \
        "$TMPL/facetwork-fleet-agent.service" > /tmp/facetwork-fleet-agent.service
    sudo install -m 644 /tmp/facetwork-fleet-agent.service /etc/systemd/system/
    rm -f /tmp/facetwork-fleet-agent.service
    sudo systemctl daemon-reload
    info "unit installed as facetwork-fleet-agent.service"
else
    info "[dry] would install the wrapper + systemd unit"
fi

if [ "$JOIN" = 1 ]; then
    if [ "$DRY" = 0 ]; then
        if docker info >/dev/null 2>&1; then
            sudo systemctl enable --now facetwork-fleet-agent
            info "agent started — it reconciles this host to fleet_config every 30s"
        else
            warn "docker is not usable by $USER yet (group change needs a re-login)."
            warn "after logging back in:  sudo systemctl enable --now facetwork-fleet-agent"
        fi
    fi
else
    info "--no-join: start it yourself with"
    info "    sudo systemctl enable --now facetwork-fleet-agent"
fi

# ---------------------------------------------------------------- verify
say "verification"
if [ "$DRY" = 1 ]; then
    info "dry run — nothing was changed"
    exit 0
fi
fail=0
chk() { if eval "$2" >/dev/null 2>&1; then info "OK    $1"; else info "FAIL  $1"; fail=1; fi; }
chk "ssh enabled"                 "systemctl is-active --quiet ssh"
chk "docker daemon reachable"     "docker info"
chk "docker compose v2 present"   "docker compose version"
chk "infra host resolves"         "getent hosts $INFRA_HOST"
chk "afl-mongodb resolves"        "getent hosts afl-mongodb"
chk "registry reachable"          "curl -sf http://$REG/v2/ -o /dev/null"
# ⚠️ Compare the OUTPUT, not the exit code, and never through a pipe here:
# `systemctl is-enabled` exits 1 for a masked unit while correctly printing
# "masked", and `set -o pipefail` then makes `... | grep -q masked` fail even
# though grep matched. That reported a FALSE FAILURE on a correctly configured
# host — worse than a false pass, in a script whose non-zero exit says
# "do not trust this host".
chk "sleep masked"                '[ "$(systemctl is-enabled sleep.target 2>/dev/null || true)" = masked ]'
chk "repo present"                "test -x $REPO_DIR/fw"
chk "venv present"                "test -x $REPO_DIR/.venv/bin/python"
[ "$RDP_MODE" != none ] && chk "rdp listening on 3389" "ss -tln | grep -q ':3389'"
chk "agent unit installed"        "systemctl cat facetwork-fleet-agent"
[ "$JOIN" = 1 ] && chk "agent running" "systemctl is-active --quiet facetwork-fleet-agent"

echo
if [ "$fail" = 0 ]; then
    echo "Setup complete. This host is group '$SERVER_GROUP'."
    echo "Check it registered (from any fleet host):   fw fleet status"
    echo "Then add it to the catalog so DHCP drift self-heals: edit servers.json"
else
    echo "Setup finished WITH FAILURES above — fix those before trusting this host." >&2
    echo "A half-provisioned host does not error: it silently never claims work." >&2
    exit 1
fi

#!/usr/bin/env bash
# Entrypoint for the per-domain Facetwork runner container.
#
# Required env:
#   AFL_DOMAIN_NAME      short name (e.g. `anthropic`, `osm-geocoder`)
#   AFL_DOMAIN_REPO      fwh_* directory name (e.g. `fwh_anthropic`)
#   AFL_MONGODB_URL      mongodb://mongodb:27017
#
# Optional env:
#   AFL_HANDLERS_ROOT    default /handlers — bind-mount root for fwh_* repos
#   AFL_DOMAIN_EXTRAS    comma-separated pip extras (e.g. "agent_sdk,mcp")
#   AFL_REGISTRY_RUNNER_ARGS  extra args to forward to the registry runner
#
# Transitional: the legacy AFL_EXAMPLE_* vars are honored as a fallback so a
# compose file not yet flipped during the examples->domains rollout still boots.
# Removed in the cleanup commit once every host runs the new compose.

set -euo pipefail

# Prefer AFL_DOMAIN_*; fall back to the legacy AFL_EXAMPLE_* during the cutover.
AFL_DOMAIN_NAME="${AFL_DOMAIN_NAME:-${AFL_EXAMPLE_NAME:-}}"
AFL_DOMAIN_REPO="${AFL_DOMAIN_REPO:-${AFL_EXAMPLE_REPO:-}}"
AFL_DOMAIN_EXTRAS="${AFL_DOMAIN_EXTRAS:-${AFL_EXAMPLE_EXTRAS:-}}"
AFL_DOMAIN_FORCE_MOUNT="${AFL_DOMAIN_FORCE_MOUNT:-${AFL_EXAMPLE_FORCE_MOUNT:-0}}"

: "${AFL_DOMAIN_NAME:?must be set (AFL_DOMAIN_NAME)}"
: "${AFL_DOMAIN_REPO:?must be set (AFL_DOMAIN_REPO)}"
: "${AFL_MONGODB_URL:?must be set}"

HANDLERS_ROOT="${AFL_HANDLERS_ROOT:-/handlers}"
DOMAIN_DIR="$HANDLERS_ROOT/$AFL_DOMAIN_REPO"

echo "==> domain=$AFL_DOMAIN_NAME repo=$AFL_DOMAIN_REPO"
echo "    mongodb=$AFL_MONGODB_URL"
echo "    handlers=$DOMAIN_DIR"

# Resolve the domain's source. Precedence:
#   1. AFL_DOMAIN_FORCE_MOUNT=1 + a bind-mount  -> install the mount (hot-reload).
#   2. baked into the image (listed in the baked list) -> use baked, IGNORE any
#      bind-mount. The fleet default: handler code ships in the image.
#   3. a bind-mount is present -> install it (local dev / non-baked domains).
#   4. neither -> error.
# Check both the new and (transitionally) the legacy baked-list filenames.
is_baked() {
    for f in /etc/afl-baked-domains /etc/afl-baked-examples; do
        [[ -f "$f" ]] && grep -qxF "$AFL_DOMAIN_NAME" "$f" && return 0
    done
    return 1
}

install_mount() {
    if [[ -n "${AFL_DOMAIN_EXTRAS:-}" ]]; then
        echo "    pip install -e $DOMAIN_DIR[$AFL_DOMAIN_EXTRAS]"
        pip install --quiet --no-cache-dir -e "$DOMAIN_DIR[$AFL_DOMAIN_EXTRAS]"
    else
        echo "    pip install -e $DOMAIN_DIR"
        pip install --quiet --no-cache-dir -e "$DOMAIN_DIR"
    fi
}

if [[ "${AFL_DOMAIN_FORCE_MOUNT:-0}" == "1" && -d "$DOMAIN_DIR" ]]; then
    echo "    AFL_DOMAIN_FORCE_MOUNT=1 -> using bind-mounted source at $DOMAIN_DIR"
    install_mount
elif is_baked; then
    echo "    using baked-in $AFL_DOMAIN_NAME (image is source of truth; bind-mount ignored)"
    [[ -f /opt/fwh_osm.commit ]] && echo "    fwh_osm @ $(cat /opt/fwh_osm.commit)"
elif [[ -d "$DOMAIN_DIR" ]]; then
    install_mount
else
    echo "ERROR: $AFL_DOMAIN_NAME is neither baked into the image nor bind-mounted at $DOMAIN_DIR." >&2
    echo "       Bake it (add to /etc/afl-baked-domains) or bind-mount ~/fw_handlers/$AFL_DOMAIN_REPO." >&2
    exit 1
fi

# Register handler routing in MongoDB AND seed the domain's FFL workflows so they
# appear in the dashboard's Flows tab. `--seed` is idempotent across restarts.
echo "    Registering handlers + seeding workflows for $AFL_DOMAIN_NAME"
python -m facetwork.domains --seed "$AFL_DOMAIN_NAME"

# Hand off to the runner service in registry mode.
echo "    Starting runner (registry mode)"
exec python -m facetwork.runtime.runner --registry ${AFL_REGISTRY_RUNNER_ARGS:-}

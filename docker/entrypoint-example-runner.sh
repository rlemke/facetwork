#!/usr/bin/env bash
# Entrypoint for the per-example Facetwork runner container.
#
# Required env:
#   AFL_EXAMPLE_NAME     short name (e.g. `anthropic`, `osm-geocoder`)
#   AFL_EXAMPLE_REPO     fwh_* directory name (e.g. `fwh_anthropic`)
#   AFL_MONGODB_URL      mongodb://mongodb:27017
#
# Optional env:
#   AFL_HANDLERS_ROOT    default /handlers — bind-mount root for fwh_* repos
#   AFL_EXAMPLE_EXTRAS   comma-separated pip extras
#                        (e.g. "agent_sdk,mcp" for anthropic)
#   AFL_REGISTRY_RUNNER_ARGS  extra args to forward to the registry runner

set -euo pipefail

: "${AFL_EXAMPLE_NAME:?must be set}"
: "${AFL_EXAMPLE_REPO:?must be set}"
: "${AFL_MONGODB_URL:?must be set}"

HANDLERS_ROOT="${AFL_HANDLERS_ROOT:-/handlers}"
EXAMPLE_DIR="$HANDLERS_ROOT/$AFL_EXAMPLE_REPO"

echo "==> example=$AFL_EXAMPLE_NAME repo=$AFL_EXAMPLE_REPO"
echo "    mongodb=$AFL_MONGODB_URL"
echo "    handlers=$EXAMPLE_DIR"

# Resolve the example's source. Precedence:
#   1. AFL_EXAMPLE_FORCE_MOUNT=1 + a bind-mount  -> install the mount (hot-reload
#      local edits, even for a baked example).
#   2. baked into the image (listed in /etc/afl-baked-examples) -> use baked,
#      IGNORE any bind-mount. This is the fleet default: handler code ships in
#      the image, so a `fleet set --image` rollout updates every server with no
#      git-pull — a (possibly stale) bind-mount on a fleet host is bypassed.
#   3. a bind-mount is present -> install it (local dev / non-baked examples).
#   4. neither -> error.
BAKED_LIST="/etc/afl-baked-examples"
is_baked() { [[ -f "$BAKED_LIST" ]] && grep -qxF "$AFL_EXAMPLE_NAME" "$BAKED_LIST"; }

install_mount() {
    # Editable so handlers stay in lockstep with the bind-mounted source.
    if [[ -n "${AFL_EXAMPLE_EXTRAS:-}" ]]; then
        echo "    pip install -e $EXAMPLE_DIR[$AFL_EXAMPLE_EXTRAS]"
        pip install --quiet --no-cache-dir -e "$EXAMPLE_DIR[$AFL_EXAMPLE_EXTRAS]"
    else
        echo "    pip install -e $EXAMPLE_DIR"
        pip install --quiet --no-cache-dir -e "$EXAMPLE_DIR"
    fi
}

if [[ "${AFL_EXAMPLE_FORCE_MOUNT:-0}" == "1" && -d "$EXAMPLE_DIR" ]]; then
    echo "    AFL_EXAMPLE_FORCE_MOUNT=1 -> using bind-mounted source at $EXAMPLE_DIR"
    install_mount
elif is_baked; then
    echo "    using baked-in $AFL_EXAMPLE_NAME (image is source of truth; bind-mount ignored)"
    [[ -f /opt/fwh_osm.commit ]] && echo "    fwh_osm @ $(cat /opt/fwh_osm.commit)"
elif [[ -d "$EXAMPLE_DIR" ]]; then
    install_mount
else
    echo "ERROR: $AFL_EXAMPLE_NAME is neither baked into the image nor bind-mounted at $EXAMPLE_DIR." >&2
    echo "       Bake it (add to /etc/afl-baked-examples) or bind-mount ~/fw_handlers/$AFL_EXAMPLE_REPO." >&2
    exit 1
fi

# Register handler routing in MongoDB so the registry runner knows which
# facets map to this container's module/entrypoint, AND seed the example's
# FFL workflows so they appear in the dashboard's Flows tab. `--seed` is
# idempotent: re-running replaces this example's prior seed (no duplicates
# across container restarts).
echo "    Registering handlers + seeding workflows for $AFL_EXAMPLE_NAME"
python -m facetwork.examples --seed "$AFL_EXAMPLE_NAME"

# Hand off to the runner service in registry mode.
echo "    Starting runner (registry mode)"
exec python -m facetwork.runtime.runner --registry ${AFL_REGISTRY_RUNNER_ARGS:-}

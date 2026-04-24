#!/usr/bin/env bash
# Download every Geofabrik PBF continent-by-continent, then run the full
# downstream pipeline (geojson / extract / vector-tiles / routing graphs /
# HTML maps) on the cached PBFs.
#
# Two phases:
#   1. Continent-by-continent pbf downloads (one `download-pbf --all-under`
#      per top-level continent, --include-parents so continent- and
#      country-level PBFs come down too). This is the slow, bandwidth-
#      heavy phase — expect hours and tens of GB per continent.
#
#   2. `update-all.sh` on the fully-populated pbf cache. Each sub-tool's
#      `--update-all` sweeps over every newly-cached region and derives
#      its geojson / extract / vector tiles / routing graphs / html.
#      Per-region re-runs are no-ops once the sidecar confirms current.
#
# Failures in one continent don't stop the others — we print a message
# and move on. `update-all.sh` is run unconditionally at the end.
#
# Sibling to examples/noaa-weather/tools/all.sh in shape and spirit:
# iterate the nine top-level Geofabrik slugs, invoke the domain's
# primary tool with --all-under + --include-parents per slug.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ -t 1 ]; then
    BOLD=$'\033[1m'
    GREEN=$'\033[32m'
    YELLOW=$'\033[33m'
    RED=$'\033[31m'
    RESET=$'\033[0m'
else
    BOLD="" GREEN="" YELLOW="" RED="" RESET=""
fi

# Flags shared by every download-pbf invocation. As a bash array so
# whitespace and dashes pass through untouched (the classic mistake
# was unquoted `PARAM=--x 1 --y 2`, where export parses each token
# as a separate KEY=VALUE assignment — see noaa-weather's all.sh for
# the same fix).
DOWNLOAD_PARAM=(
    --include-parents
    --delay 2              # be nice to Geofabrik's servers
)

# Geofabrik's top-level region slugs. Russia and antarctica sit at
# the top level rather than under Europe/Asia. australia-oceania
# covers Australia, NZ, and the Pacific island sets.
CONTINENTS=(
    north-america
    central-america
    south-america
    europe
    asia
    africa
    australia-oceania
    russia
    antarctica
)

FAILED_CONTINENTS=()
TOTAL=${#CONTINENTS[@]}
IDX=0
START_EPOCH=$(date +%s)

for continent in "${CONTINENTS[@]}"; do
    IDX=$((IDX + 1))
    printf '\n%s=== [%d/%d] download-pbf %s ===%s\n' \
        "$BOLD" "$IDX" "$TOTAL" "$continent" "$RESET"
    if "${SCRIPT_DIR}/download-pbf.sh" \
        --all-under "$continent" "${DOWNLOAD_PARAM[@]}"; then
        printf '%s[ok]%s download-pbf %s\n' "$GREEN" "$RESET" "$continent"
    else
        rc=$?
        printf '%s[fail]%s download-pbf %s (exit %d) — moving on\n' \
            "$RED" "$RESET" "$continent" "$rc"
        FAILED_CONTINENTS+=("$continent")
    fi
done

DOWNLOAD_ELAPSED=$(( $(date +%s) - START_EPOCH ))
echo
printf '%s=== pbf download phase complete ===%s\n' "$BOLD" "$RESET"
printf 'continents: %d ok, %d failed (%s)  ·  elapsed: %ds\n' \
    "$((TOTAL - ${#FAILED_CONTINENTS[@]}))" \
    "${#FAILED_CONTINENTS[@]}" \
    "${FAILED_CONTINENTS[*]:-none}" \
    "$DOWNLOAD_ELAPSED"

# Phase 2 — run every downstream stage on everything we just cached.
# update-all.sh sweeps each sub-tool's --update-all in dependency
# order and is a no-op for anything already current, so this is safe
# to re-run after partial progress.
echo
printf '%s=== running update-all on every cached pbf ===%s\n' "$BOLD" "$RESET"
"${SCRIPT_DIR}/update-all.sh"

# Fill the FW_OSM_SELFHOST_* settings the OSM publish/watchdog jobs need from
# this host's nightly re-split config, when the environment does not set them.
#
# Why: these values used to come from a `.env` on the host that generated the
# split. When that work moved hosts, nothing carried them over, and every job
# that needs them failed on every run -- the replication publisher (17 nights,
# 2026-09-16..10-02) and the watchdog meant to notice exactly that. The
# re-split's config.env already describes this host's tree, so it is the one
# source of truth; an explicit environment value still wins.
#
# Sourced, not executed. Safe under `set -u`.
_fw_osm_cfg="${FW_OSM_SELFHOST_CONFIG:-$HOME/.facetwork/osm-selfhost/config.env}"
if [ -f "$_fw_osm_cfg" ]; then
    _fw_osm_vals="$( ( set +u; . "$_fw_osm_cfg" >/dev/null 2>&1
                       printf '%s\n%s\n' "${WWW:-}" "${BASE_URL:-}" ) )"
    _fw_osm_www="$(printf '%s\n' "$_fw_osm_vals" | sed -n 1p)"
    _fw_osm_base="$(printf '%s\n' "$_fw_osm_vals" | sed -n 2p)"
    if [ -z "${FW_OSM_SELFHOST_WWW:-}" ] && [ -n "$_fw_osm_www" ]; then
        export FW_OSM_SELFHOST_WWW="$_fw_osm_www"
    fi
    if [ -z "${FW_OSM_SELFHOST_BASE_URL:-}" ] && [ -n "$_fw_osm_base" ]; then
        export FW_OSM_SELFHOST_BASE_URL="$_fw_osm_base"
    fi
fi
if [ -n "${FW_OSM_SELFHOST_WWW:-}" ]; then
    _fw_osm_root="$(dirname "$FW_OSM_SELFHOST_WWW")"
    if [ -z "${FW_OSM_SELFHOST_POLYS:-}" ] && [ -d "$_fw_osm_root/polys" ]; then
        export FW_OSM_SELFHOST_POLYS="$_fw_osm_root/polys"
    fi
    # Where those indexes live. Unset, tag_index falls back to /tmp: the
    # publisher then opened a NEW empty index there every night and failed with
    # "no expression -- build it first" while the real one (151,819 rows) sat
    # untouched beside the tree.
    if [ -z "${FW_OSM_INDEX_ROOT:-}" ] && [ -d "$_fw_osm_root/indexes" ]; then
        export FW_OSM_INDEX_ROOT="$_fw_osm_root/indexes"
    fi
    # The tag indexes kept beside the tree. An index that exists here exists to
    # be kept current; leaving it out of the nightly list is how alpr stopped
    # advancing on 2026-09-14 with nothing reporting it. Set the variable
    # (even to empty) to choose explicitly.
    if [ -z "${FW_OSM_NIGHTLY_INDEXES+set}" ] && [ -d "$_fw_osm_root/indexes" ]; then
        FW_OSM_NIGHTLY_INDEXES="$(find "$_fw_osm_root/indexes" -maxdepth 1 -name '*.sqlite' \
            -exec basename {} .sqlite \; 2>/dev/null | sort | paste -sd, -)"
        export FW_OSM_NIGHTLY_INDEXES
    fi
fi
unset _fw_osm_cfg _fw_osm_vals _fw_osm_www _fw_osm_base _fw_osm_root

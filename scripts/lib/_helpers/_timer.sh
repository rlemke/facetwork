# fw timer helper — install a periodic job on macOS (launchd) or Linux (cron).
#
# ⚠️ Every `fw svc …--install` was launchd-only and EXITED 1 on Linux. That was
# invisible while the fleet was two Macs; it is now majority Linux (beelink01,
# three macminis, two atopnucs), so these services were installable on 2 hosts
# of 7 — and the OSM role could not move to the box with the RAM to run it.
#
# cron rather than systemd, deliberately: `loginctl show-user … Linger` is `no`
# on these hosts, so a --user timer would die at logout, and system units need
# sudo for every install. cron needs neither, survives logout, and the wrappers
# already defend against the one thing systemd would add here — osm-replicate's
# wrapper waits 60x5s for the external tree before doing anything.
#
# A cron line is tagged `# fw-timer:<label>` so uninstall removes exactly its
# own line and never rewrites somebody else's crontab entry.

fw_timer_is_darwin() { [ "$(uname)" = "Darwin" ]; }

# fw_timer_cron_spec <at HH:MM|""> <every_hours|"">  -> "MM HH * * *" | "0 */N * * *"
fw_timer_cron_spec() {
    local at="${1:-}" every="${2:-}"
    if [ -n "$at" ]; then
        case "$at" in
            [0-9][0-9]:[0-9][0-9]) : ;;
            *) echo "fw_timer: --at wants HH:MM (24h), got '$at'" >&2; return 2 ;;
        esac
        # 10# so 08 is not parsed as octal — a real bug class in these scripts.
        printf '%d %d * * *' "$((10#${at##*:}))" "$((10#${at%%:*}))"
    else
        [ -n "$every" ] || every=6
        printf '0 */%d * * *' "$every"
    fi
}

# fw_timer_cron_install <label> <wrapper> <spec> <log>
fw_timer_cron_install() {
    local label="$1" wrapper="$2" spec="$3" log="$4"
    local tag="# fw-timer:$label"
    local line="$spec $wrapper >> $log 2>&1  $tag"
    local cur; cur="$(crontab -l 2>/dev/null || true)"
    # Drop any previous line for this label, then append the new one.
    printf '%s\n' "$cur" | grep -vF "$tag" | grep -v '^$' > /tmp/.fw_cron.$$ || true
    printf '%s\n' "$line" >> /tmp/.fw_cron.$$
    crontab /tmp/.fw_cron.$$ && rm -f /tmp/.fw_cron.$$
}

# fw_timer_cron_uninstall <label>
fw_timer_cron_uninstall() {
    local label="$1" tag="# fw-timer:$1"
    local cur; cur="$(crontab -l 2>/dev/null || true)"
    [ -z "$cur" ] && return 0
    printf '%s\n' "$cur" | grep -vF "$tag" | grep -v '^$' > /tmp/.fw_cron.$$ || true
    if [ -s /tmp/.fw_cron.$$ ]; then crontab /tmp/.fw_cron.$$; else crontab -r 2>/dev/null || true; fi
    rm -f /tmp/.fw_cron.$$
}

# fw_timer_cron_line <label> -> prints the installed line, empty if none
fw_timer_cron_line() {
    crontab -l 2>/dev/null | grep -F "# fw-timer:$1" || true
}

# fw_timer_loaded <label> -> 0 if installed (either platform)
fw_timer_loaded() {
    if fw_timer_is_darwin; then
        launchctl list 2>/dev/null | grep -q "$1"
    else
        [ -n "$(fw_timer_cron_line "$1")" ]
    fi
}

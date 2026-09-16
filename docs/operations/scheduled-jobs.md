# Scheduled jobs: how `--install` timers work

Some things in this system are not workflows and cannot be. A workflow runs when
something submits it; these run because *time passed*, and several exist
precisely to notice that something else **stopped** running. A process cannot
observe its own absence, so the schedule has to live outside it.

This page covers where those timers are implemented, how to add one, and the
traps that have actually bitten.

---

## 1. There is no scheduler

There is no central scheduler, no daemon, and no scheduling state in MongoDB.
Each command installs a job with **the host's own scheduler**:

| Host OS | Mechanism | Where it lives |
|---|---|---|
| macOS | **launchd** | `~/Library/LaunchAgents/com.facetwork.<name>.plist` |
| Linux | **cron** | the user crontab, one tagged line per job |

Both run the *same* wrapper script, written to `~/.facetwork/<name>.sh`. Only the
registration differs, which is why the shared helper is under 70 lines.

This is deliberate. A scheduler of our own would be one more thing that can stop
running, and it would need its own watchdog. The host's scheduler is already
supervised by the host.

## 2. The shared half — `scripts/lib/_helpers/_timer.sh`

```
fw_timer_is_darwin        which scheduler this host uses
fw_timer_cron_spec        "02:40" or every-N-hours  ->  a crontab spec
fw_timer_cron_install     replace-or-add our tagged line
fw_timer_cron_uninstall   remove exactly our line
fw_timer_cron_line        read back what is installed
fw_timer_loaded           is it scheduled? (either platform)
```

Two properties are load-bearing:

⚠️ **A cron line is tagged `# fw-timer:<label>`** so uninstall removes exactly its
own entry. This edits the operator's real crontab, and silently rewriting an
unrelated line would be a poor way to discover the bug.

⚠️ **Re-install replaces rather than appends.** Otherwise the job runs twice per
tick and every re-install makes it worse.

## 3. Why cron on Linux, and not systemd

Measured, not preferred:

- `loginctl show-user <user> -p Linger` is **`no`** on these hosts, so a
  `systemd --user` timer dies at logout — unusable on a headless box.
- System units need **sudo for every install and uninstall**, which puts a human
  in the loop for something that should be one command.
- The one thing systemd would genuinely add — ordering against an external mount
  (`RequiresMountsFor`) — **the wrappers already do themselves**: the
  `osm-replicate` wrapper waits 60×5 s for the tree and refuses rather than
  publishing a day whose diff was never cut.

If lingering is ever enabled fleet-wide, a systemd back end is a drop-in third
branch in `_timer.sh`.

## 4. Writing the wrapper

The wrapper is the part that survives contact with reality. It runs under a
**minimal environment** — launchd gives a job almost nothing, and cron only a
little more — so:

- **Never rely on `PATH`.** `osm-replicate` resolves the osmium binary
  explicitly, because a bare `python3` under launchd resolves to Xcode's
  sandboxed 3.9, which raises *"Operation not permitted"* on an external volume
  and has none of the dependencies.
- **Never rely on the working directory.** launchd's `WorkingDirectory` on an
  external volume fails with *"getcwd: Operation not permitted"*; the wrapper
  `cd`s itself, after waiting for the mount.
- **Check your preconditions and refuse.** A job that runs against a
  half-mounted tree and writes a state file is worse than one that skips the
  night, because it advertises a result that was never produced.

## 5. Staleness is the failure that matters

Every scheduled job needs a companion answer to *"is this still running?"*,
because the failure mode is **silence**. The pattern used here:

- The job writes a health file or log on each run.
- A `--check` mode reads it and exits **0** healthy / **1** stale-or-broken /
  **2** could-not-verify.
- Something independent runs that check.

⚠️ **The third exit code is not a courtesy.** A check that alarms because a
remote host is merely offline teaches the operator to dismiss it — which is
exactly how the gap it was built to catch survives. `osm-watchdog` and
`backup-mongo` both follow this.

⚠️ **A watchdog must be independent of the thing it watches.** `osm-replicate`'s
own post-publish check catches a run that happens *and* finds a problem. It
cannot catch a run that never happened, and that failure has occurred here — a
fixed nightly time never fired on a sleeping laptop.

⚠️ **On a machine that sleeps, prefer `StartInterval` to a fixed hour.** launchd
does **not** run a missed `StartCalendarInterval` on wake; `StartInterval` fires
once its period has elapsed.

## 6. Adding a timer to a command

1. Source the helper: `. "$(dirname "${BASH_SOURCE[0]}")/../_helpers/_timer.sh"`
2. Write the wrapper to `~/.facetwork/<name>.sh` — platform-neutral, defensive
   about PATH, cwd and preconditions.
3. Branch once at the end: `fw_timer_is_darwin` → write a plist and
   `launchctl load`; otherwise `fw_timer_cron_spec` + `fw_timer_cron_install`.
4. Handle `--uninstall` and `--status` on **both** platforms. A job that can only
   be removed on macOS is a job nobody removes.
5. Give it a `--check` with the three exit codes above.

`scripts/lib/maint/backup-mongo` is the cleanest current example; `osm-replicate`
shows the hardened wrapper.

## 7. What is scheduled now

Singleton jobs, deliberately on one host each — not per-host.

| Host | When | Job |
|---|---|---|
| beelink01 | 02:40 daily | `backup-mongo` — control-plane dump to the archive host |
| beelink01 | 03:15 daily | `osm-replicate` — publish new per-region diffs |
| beelink01 | every 12 h | `osm-watchdog` — independent alarm on a stalled stream |

Read the truth with `crontab -l` (Linux) or `launchctl list | grep com.facetwork`
(macOS), and per-command with `fw <group> <name> --status`. This table is a
snapshot; the host is not.

⚠️ **Timers do not move with a role by themselves**, and this is not a caution
— it happened, to the person writing this page, hours after writing it.

When the OSM role moved hosts on 2026-09-15, five timers were moved with it and
one was not. `osm-maintain` — the nightly re-split — is not a `fw` command; it
lives in `fwh_osm/deploy/selfhost/install.sh`, which was launchd-only, so it
could not have been installed on the new Linux host even if it had been
remembered. It kept running on the old machine against a planet that had been
deleted:

```
2026-09-15 06:23  osm-maintain done     8 region(s) re-extracted
2026-09-16 03:30  osm-maintain: master PBF missing: …/planet-latest.osm.pbf
```

Three lessons worth more than the incident:

1. **Inventory timers by HOST, not by repository.** The five that moved were the
   ones `fw` knows about. The one that did not was in another repo, and no
   audit of this repo would have listed it. `crontab -l` and
   `launchctl list | grep com.facetwork` on each host are the only complete
   inventory.
2. **Moving a data tree is not moving its siblings.** The migration synced
   `www`, `bucket-tier` and `work`. It did not sync the ROOT-LEVEL files beside
   them, so `regions.json` — 695 bytes, and a required input — never arrived.
   A directory-by-directory parity check passed, because the files that were
   missing were in no directory that was compared.
3. **It was visible only because something independent was watching.**
   `osm-watchdog` alarms when the re-split goes stale. Without it, a scheduled
   job with nowhere to run is silence, and the extracts just stop advancing —
   the failure that took 39 days to notice the first time.

## 8. History

Until 2026-09-15 **every** `--install` guarded on `uname != Darwin` and exited 1.
That was invisible while the fleet was two Macs. It is now majority Linux, so
these services were installable on **2 hosts of 7** — and it surfaced as a hard
blocker: the OSM role could not move to the only box with the memory to run it,
because its extract server and nightly publisher could not be installed there.
`_timer.sh` exists to make that impossible to repeat.

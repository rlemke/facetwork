# Diagnosis field notes

Traps that have actually cost time on this fleet, and the rules that come out of
them. Read this **before** a debugging session, not after.

Organised by the mistake, not by the component — the same mistake recurs in
unrelated places, which is the point.

---

## 1. The probe that cannot fail

**The single most expensive class of bug here.** Not a monitoring *gap* — those
are an absence you eventually notice — but a check that returns a *positive
assertion of health* about something broken, which terminates investigation.

Nine instances inside three weeks:

| Probe | Said | Truth | Mechanism |
|---|---|---|---|
| `timeout 120 docker kill …` | "Docker wedged" | Docker fine | **`timeout` does not exist on macOS** — `command not found`, never ran |
| `pgrep -f "curl.*X"` | transfer running | dead 45 min | matched the probe's **own ssh command line** |
| `pkill -f X.sh` | cleanup done | nothing happened | killed **its own shell**, same self-match |
| `rsync --partial` + retry loop | healthy | 22 min at 0 bytes | rsync has **no default I/O timeout**; a hung socket never errors, so the retry never fires |
| stall detector v1 | no stall | one transfer stalled | required **both** watched transfers to stall |
| `du -sb` progress | 90% done | 76% | counted a **duplicate the operator created** |
| convergence check | "23/23 up-to-date" | 22h on a stale image | counted *registered runners*, not the **image they ran** |
| `stat -f %z f \|\| stat -c %s f` | a byte count | a **filesystem block report** | `-f` is BSD *format*, GNU *filesystem status*: on Linux the first form SUCCEEDS |
| `fleet status` → `applied_version` | agents current | **3-day-old code running** | reports what the agent **recorded**, not what it **executes** |

### Rules

1. **Before trusting a check, construct its failing case and run it.** If you
   cannot make it go red, it is not evidence. This is routine for code; it is
   almost never done for operational probes.
2. **Measure the artifact, not a proxy.** Bytes on disk beat "is the process
   alive". A `COMPLETE` marker written by the job beats inferring completion from
   progress output.
3. **A diagnostic should have the fewest dependencies in the system.** It runs
   when things are broken, often in a degraded environment. A check that needs a
   heavy import chain can fail for reasons unrelated to its subject and then
   misattribute — one here blamed the *network* for a missing Python module
   across 79 retries.
4. **Never silence a check's stderr.** `2>/dev/null` on a probe removes its
   ability to explain itself; the loud diagnostic you added is discarded.
5. **A silent fallback is a wrong answer waiting.** `except Exception: x = None`
   in a resolver turns "I could not look this up" into "the thing is absent".

---

## 2. Stale artifacts read as current

Three different artifacts lied about being current, in one week.

| Artifact | How it lied |
|---|---|
| `planet.md5.actual` | 8 weeks old, hand-made, never maintained. Verification against it failed and read as corruption. |
| `fleet-agent.err.log` | **4 days stale** while the agent actively failed. I diagnosed from it twice and reached a wrong conclusion both times. |
| the agent's own **code** | `git pull` updates files; a running interpreter keeps what it loaded. Four agents ran 3-day-old code while every fix sat on disk. |

### Rules

6. **Check the mtime of anything you are about to reason from.** Especially logs
   and checksums. `stat -c %y` costs nothing.
7. **A checksum that cannot be maintained should be deleted, not refreshed.** One
   guaranteed to fail trains you to dismiss the alarm. There is no valid external
   checksum for a file advanced by incremental updates — say so instead.
8. **A long-lived daemon does not follow its own source.** Nothing reports
   "running stale code": convergence views show what was *recorded*, liveness
   watchdogs check *alive*, not *current*. Either make it re-exec on source
   change, or restart it as part of every deploy.

---

## 3. Cross-platform divergence

A fleet mixing BSD (macOS) and GNU (Linux) userland. Five of the nine probe
failures above trace here. The dangerous ones do not error — they return
something plausible.

| Trap | Note |
|---|---|
| `timeout` | GNU only. On macOS: `command not found` — reads as the command failing |
| `stat -f` | BSD *format* vs GNU *filesystem status*. **The usual `stat -f … \|\| stat -c …` fallback is portable in one direction only** |
| `du -b` | GNU only; use `wc -c` for bytes |
| `ps -eo` | GNU; BSD wants `ps -axo` |
| `pgrep -c` | not on macOS |
| `sed -i` | GNU takes no arg, BSD requires one (`sed -i ''`) |
| `rsync` | macOS ships **openrsync**, which rejects many options. Use `--rsync-path=/opt/homebrew/bin/rsync` |

### Rules

9. **Prefer primitives with no dialect** — `wc -c` over `stat`, python over
   coreutils flags, when a script must run on both.
10. ⚠️ **A defensive fallback that defeats itself is worse than none**, because it
    is evidence somebody thought about the problem. `stat -f … || stat -c …` is
    the canonical example.

---

## 4. Migrations are never just the bytes

Moving 298 GB between hosts had **four** distinct incompleteness modes, each
invisible to the check before it:

| Moved | Did not move | Why the check missed it |
|---|---|---|
| the data | the **timers** | a schedule in another repo appears in no audit of this one |
| the directories | the **root-level files** beside them | directory-by-directory parity compared only directories |
| the files | the **absolute paths inside them** | a byte-exact copy is exactly the wrong outcome |
| the data | its **consumers' config** | nothing links a data path to what references it |

### Rules

11. **Run `fw maint path-check` on the destination.** A config embedding absolute
    paths transfers perfectly and is then wrong.
12. **Never put migration scratch inside the tree being migrated.** A hardlink
    created to serve a file over HTTP became a 92 GB duplicate *and* inflated the
    progress metric.
13. **Inventory timers by HOST, not by repository** — `crontab -l` and
    `launchctl list | grep com.facetwork` on every host.
14. **Directory parity is not migration completeness.** Compare the parent, and
    list what sits beside the directories you synced.

---

## 5. Docker, mounts and bind sources

15. **A bind whose source is missing does not fail — it gets invented, as root.**
    23 such binds created `root:root` directories on four hosts, breaking domain
    installs weeks later. `create_host_path: false` is right for binds that
    *must* exist and wrong for optional ones (it would stop every runner on a
    host with no checkouts); pre-create them as the user instead.
16. **Name filesystems by UUID.** Device names are context-dependent — measured:
    the same filesystem was `nvme1n1p4` before a reboot and `nvme0n1p4` after.
17. **Order the container runtime after the mount** (`RequiresMountsFor`), and
    verify by *rebooting*: `mount -a` tests parsing, not ordering. A passing
    reboot shows the mount timestamp **before** the daemon's.
18. **On macOS there is no such ordering.** A volume reattached under a running
    Docker leaves its file-sharing layer stale: `test -d` succeeds from the shell
    while the daemon reports `not a directory`. Only a daemon restart clears it.
19. **`systemctl disable` does not keep a unit out.** It only prevents starting at
    *boot*; a `restart` sweep starts it anyway. Use `stop` plus `mask` — and note
    `mask` fails if a real unit file occupies the path, so rename it instead.

---

## 6. Distributed execution

20. **Reclaim does not stop the original execution.** At-least-once plus an
    uncancelled original turns recovery into amplification: four concurrent 92 GB
    copies once ran here, each making the others slower. Handlers must be
    idempotent in the **cost** sense, not only the correctness sense.
21. **A timeout read from the observer's environment cannot be tuned by the
    observed.** A domain raised its own execution budget to 8h; the reaper
    timeout lives in whichever runner is *doing the reaping*, so ~130 other
    runners still reclaimed it at 120s.
22. **A wedged host is worse than a dead one.** It holds memory, keeps its
    containers `Up`, and every reclaim adds load to the host least able to bear
    it.
23. **Changing shared state that older code validates needs
    expand/migrate/contract.** Deploy tolerant code everywhere *first*. An index
    filter changed live took this fleet from 108 runners to 23.

---

## 7. Shell semantics that produce a silent non-zero exit

The macmini02 outage (94 failed reconciles, **zero bytes of output**) was one line:

```bash
VAR="$(python -c '... sys.exit(1) ...')"     # exits 1 to mean "declined"
```

Under the caller's `set -e` a failing command substitution **is** a failing
command, so a *deliberate* "I could not resolve this" aborted the caller — before
anything was printed. The fleet agent shows the subprocess's streams only on
failure, and both were empty, so the host could not be triaged remotely at all.

```bash
VAR="$(python -c '…')" || VAR=""            # declining is now an answer, not an abort
```

⚠️ **Three silent-failure modes lived in this one helper**, which every `fw`
command sources — so each was reachable from ~40 entry points:

| # | Defect | Symptom |
|---|---|---|
| 1 | declining `$( )` under the caller's `set -e` | caller aborts, **0 bytes** of output |
| 2 | resolver imported `facetwork` in a minimal env | transitive `lark` ImportError, swallowed, reported as *network unreachable* |
| 3 | restore guarded by `[ -n "$_SAVED" ]` | nothing to restore → the probe's **own** localhost value was left in place, silently, so every downstream script addressed a **closed port** and called it the fleet's database |

Defect 3 is the subtlest: the guard looks defensive. It protects against
*overwriting with an empty value*, and in doing so it preserves a value the probe
itself invented. **No endpoint is an answerable state; a wrong one is not** — unset
it and say so.

### Rules

24. **`||` every command substitution whose command may legitimately decline.**
    Exit 1 meaning "no result" and exit 1 meaning "I broke" are indistinguishable
    to `set -e`; the guard is what separates them.
25. ⚠️ **This bug class defeats the obvious audit.** The file holding the
    assignment does not itself `set -e` — the *caller* does. A sweep over "files
    with `set -e`" cannot see it. Audit by the shape of the assignment, across all
    files. (Done here: 6 such assignments under `scripts/lib`; exactly one could
    decline, and the other five *should* abort.)
26. **A script that can exit non-zero must not be able to do so silently.** An
    `ERR` trap (with `set -o errtrace`, so it also fires inside functions) that
    names the exit code, line and `$BASH_COMMAND` costs six lines and converts an
    untriageable host into a one-line diagnosis. `runner/start` now carries one —
    it named this bug on the **first** reconcile after deployment.
27. **A "restore the previous value" branch must handle there being none.** Guarding the restore leaves whatever the probe set — which is the probe's guess, not the system's state.
28. **Write the test that strips the fix.** A regression test asserting the good
    path passes is compatible with the fix having done nothing; the companion case
    removes the guard and asserts the caller dies *and that stderr is empty*. Same
    rule as §1.1, applied to tests.

## 8. Practical habits that paid off

- **Capture a baseline before changing anything.** "4 advertisers here, 0 there"
  before a cutover made the after-state provable rather than plausible.
- **Verify the surviving copy immediately before deleting the other.** Three
  deletions totalling 297 GB, each gated on re-checking what remained — and in
  one case the survivor was *newer*, which is the reassuring answer.
- **Write down why a thing is retired.** `planet-latest.osm.pbf.retired-<date>`
  and a `joined: false` catalog entry with the reason stop the next person
  restoring it.
- **Report what was measured, not just the conclusion.** Most wrong turns here
  came from a conclusion whose evidence nobody restated.

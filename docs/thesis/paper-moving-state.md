# Moving State: Relocating Stateful Services and Bulk Data in a Leaderless Commodity Fleet — an Experience Report

*Research companion to the Facetwork thesis (`thesis.md`). Empirical basis: the
operating record of a seven-host fleet between 2026-08-23 and 2026-09-15,
covering a whole-site physical relocation, the decommissioning of two hosts and
the addition of four, the migration of MongoDB between machines, and the
migration of a 92 GB OpenStreetMap planet plus a 206 GB derived tree — together
with the incident record in project memory and the commits referenced inline.*

---

## Abstract

A leaderless workflow runtime earns its resilience by making runners stateless
and disposable. We report on what happens when the *state* has to move. Over
three weeks we relocated a fleet to a new site, replaced half its hosts,
migrated the control-plane database between machines, and moved 298 GB of bulk
scientific data — a 92 GB OSM planet and its derived extract tree — from the
host that had always held it to one with four times the usable memory. The
runtime's own abstractions handled the compute side almost invisibly: work
re-routed by capability, runners re-registered, and no workflow definition
changed. Every serious incident came from the storage and observation layers
instead.

We catalogue eleven distinct failures and find they fall into two families. The
first is **storage identity**: a filesystem's device name is not stable
(`/dev/nvme1n1p4` became `/dev/nvme0n1p4` across a single reboot, same UUID),
container runtimes silently invent missing bind-mount sources as root, and a
volume reattached beneath a running Docker daemon stays invisible to it. The
second, and our main contribution, is **probes that cannot return the failing
answer**: seven independent monitoring or verification mechanisms reported
health while the thing they watched was broken, each for a different mechanical
reason, and in four cases the false reading directly caused wasted or harmful
work. We argue this family is systematically under-treated: the literature on
observability assumes the probe is correct and asks what to measure, whereas in
practice the probe's own failure modes dominated our incident record.

We also report a measurement that only appears under load — a wired 2.5 GbE link
that loses 34–55% of packets while transferring and 0% when idle, with every
interface error counter reading zero — and a capability class that no resource
model in the literature expresses: a CPU whose instruction set cannot run a
dependency that installs successfully.

---

## 1. Statelessness ends at the disk

Facetwork's operating model is deliberate about hosts: runners hold no
authoritative state, coordination is a shared MongoDB and object store, and any
runner may disappear at any time (`paper-informal-fleet.md`). That model was
tested hard here and it held. During the site move every host's address changed
and nothing needed reconfiguring, because hosts are named in a catalog and
resolved at startup. During the OSM migration, workflow definitions did not
change at all: the work followed the data because the data is a *routing
dimension*, not a configuration value (§7).

But the fleet is not stateless in aggregate. It has exactly three pieces of
irreplaceable or expensive state:

| State | Size | Replaceable? |
|---|---|---|
| MongoDB (workflows, runs, steps, tasks, fleet config, catalogs) | 126 MB | **No** — no backup existed (see §10) |
| OSM planet, advanced by replication diffs | 92 GB | Only by re-download + weeks of diffs |
| Derived extract tree (`www`, `bucket-tier`) | 206 GB | Only by re-running the pipeline |

Moving these is the subject of this paper. Our headline finding is that the
difficulty was not in the runtime and not in the network throughput. It was in
*knowing whether each step had actually worked*.

---

## 2. Storage identity is not what the operator thinks it is

### 2.1 Device names are not stable, and the failure is silent

The host we were migrating to has two identical 931 GB NVMe drives. Its data
partition was `/dev/nvme1n1p4`. Two earlier attempts to add an `/etc/fstab`
entry had been made by the operator and both had to be backed out, because the
mount failed on the next boot with no useful diagnostic.

The boot journals contained the explanation. The *root* filesystem — UUID
`fad5c9ab…` — is `nvme0n1p2` in the booted system and `nvme1n1p2` in the
initramfs, on every boot. The two drives swap names depending on which context
is asking.

We rewrote the entry to name the filesystem by UUID, with `nofail` and
`x-systemd.device-timeout=30`, and later verified it across a real reboot:

```
before reboot:  /dev/nvme1n1p4
after  reboot:  /dev/nvme0n1p4      # same UUID, same filesystem, nothing changed
```

The rename we had inferred from the initramfs actually occurred. A device-path
entry would have mounted the wrong disk or nothing. This is well-known advice;
what is less appreciated is that the failure is **silent and delayed** — the
entry works when you write it, works under `mount -a`, and fails at the next
unplanned reboot, by which time the change is no longer suspected.

### 2.2 `mount -a` cannot test the half that matters

Both failure modes above are *startup ordering* faults. `mount -a` proves the
entry parses. It cannot prove the mount happens before the consumers of the
mount. We adopted the rule that **only a deliberate reboot verifies a mount**,
and recorded the passing signature:

```
mount unit active:  19:54:46
docker started:     19:54:55      # 9s later, via RequiresMountsFor
```

If those timestamps invert, a bind-mounted container is holding the empty
directory *underneath* the mountpoint — and it will look healthy.

### 2.3 Container runtimes invent missing bind sources, as root

Docker creates a missing bind-mount source directory rather than failing. The
compose files carry 23 such binds (`~/fw_handlers/fwh_*`). They did not exist on
any Linux host, so Docker created all of them `root:root`, on **four hosts
simultaneously**. Nothing failed at the time: the runners log *"using baked-in
domain (image is source of truth; bind-mount ignored)"*, so the binds are a
development convenience the fleet never reads. The damage surfaced weeks later,
when installing a domain checkout failed with `Permission denied` on every Linux
host at once.

The obvious guard is wrong here. `create_host_path: false` is correct for the
Mongo and MinIO binds, whose sources *must* exist — but applying it to these
optional binds would stop every runner on a host with no checkouts, which is the
normal fleet case. The fix (`c2bd32ef`) was to have the fleet agent pre-create
the directories as the invoking user before compose runs, so the runtime finds
them present and leaves ownership alone.

**Generalisable form:** *a bind whose source is missing does not fail, it gets
invented* — and what invents it is privileged. The same mechanism silently
produced an empty root-owned directory where a mounted volume was expected,
which is the `/Volumes` class of failure we hit twice more.

### 2.4 A volume reattached under a running daemon stays invisible

On macOS there is no `RequiresMountsFor`. When an external volume was reattached
beneath an already-running Docker Desktop, container creation failed with:

```
open /host_mnt/Volumes/afl_data_local/scratch/osm-selfhost: not a directory
```

on a path where `test -d` succeeded from the shell. The daemon's file-sharing
layer held a stale view. The only remedy we found is restarting the daemon. We
note this asymmetry because it is architectural, not incidental: Linux hosts can
*express* the dependency between a mount and the container runtime; macOS hosts
cannot, and must instead be sequenced by hand.

---

## 3. Moving the control-plane database

MongoDB moved between hosts with the data intact. Three findings:

**Live client pools do not re-resolve.** After the new instance was serving, some
processes continued writing to the old one because their connection pools had
already resolved the name. The split persisted until the old `mongod` was
stopped. Name-based addressing does not make a running client follow the name.

**File-descriptor limits are a migration-time landmine.** The new host's `mongod`
hit `TooManyFilesOpen (errno 24)` under normal fleet load at the default
`nofile` of 1024 — a limit that had never been reached on the previous host
because it ran under a different supervisor.

**"The infra host" stopped existing, and the docs did not notice.** The fleet had
always had one machine serving both MongoDB and the object store, and the
documentation, defaults and mental model all said *the infra host*. After the
move, MongoDB was on one machine and MinIO on another. The catalog already
supported this — each `afl-*` name resolves independently from whichever entry
claims it — but a documentation table continued to assert the old topology.

We later found a sharper instance of the same drift: the `afl-extracts` alias was
*documented* as pointing at a host, but **no entry claimed it**, so
`catalog.resolve_ip("afl-extracts")` returned `None`. The documentation and the
resolver had diverged, and only the documentation was ever read by a human. We
now treat generated output (`fw fleet servers`) as the source of truth and
document the *shape* rather than the assignments.

---

## 4. Moving 298 GB, and the cost of not knowing

The bulk migration took roughly six hours of wall time and about eleven hours of
attention. Almost none of that was throughput.

### 4.1 Verification without a reference

The planet carried a checksum file, `planet.md5.actual`. The transferred copy did
not match it, which we initially read as corruption.

The checksum was **eight weeks old**. `UpdatePlanet` had rewritten the file many
times since by applying replication diffs — its mtime was that morning — and
nothing ever updated the checksum. Worse, nothing *could*: the artifact was
hand-made (a zero-length `planet.md5.err` beside it betrays a shell redirect),
and **no external reference checksum can exist for a planet advanced by diffs**,
because the result is unique to one host's particular sequence of updates.

The only sound verification was to compute the source's checksum at migration
time and compare — 92 GB read on both sides, 87 minutes on the source's
contended external disk, for a comparison that took milliseconds. It matched.

We then deleted the stale file rather than refreshing it. **A checksum that is
guaranteed to fail is worse than none**: it trains the operator to dismiss the
alarm, which is precisely what we nearly did.

### 4.2 Transfer artifacts become real data

To serve the planet over HTTP during the migration we hardlinked it into the
served tree. Hardlinks did not survive `rsync` to the destination filesystem, so
the link was transferred **as a separate 92 GB file**. We caught it at 21.7 GB,
removed both ends — and it reappeared, because the running `rsync` had built its
file list before the deletion and continued transferring a file that no longer
existed at the source. It reached 51 GB across a completed copy and a temp before
we stopped the pass and rebuilt the list.

That artifact also inflated the destination's apparent size, which is how we came
to believe the transfer was 90% done when 41 GB was still missing. **A migration
scratch artifact placed inside the migrated tree is indistinguishable from
payload**, both to the tool and to the operator watching byte counts.

### 4.3 The link only fails while you use it

Three long-lived connections dropped mid-transfer with different errors. Pinging
the host showed 0% loss, so we looked elsewhere — twice.

Measuring *during* a transfer told a different story:

| condition | ICMP loss | throughput |
|---|---|---|
| idle | **0%** | — |
| under load | **34–55%** | ~124 MB/s ≈ 1 Gb/s on a 2.5 GbE link |

Every interface error counter on both hosts read zero, and the loss is specific
to one host: the same source measured 0% to three other machines under the same
conditions. The signature — trains at 2.5 G, collapses under sustained load,
counters clean because the frames never reach the MAC — is consistent with a
marginal cable or the `energy-efficient-ethernet` flag the interface carries.

The methodological point is the one we want to keep: **a link fault that is
load-correlated is invisible to every idle check**, including the one an operator
reaches for first. A 20-packet ping reported 0% on a link that was losing half
its packets minutes earlier.

**Resolved, 2026-09-16: it was the cable.** Cables were replaced and the switch
deliberately kept, isolating the variable. Re-measured by the same method — 8 GB
over `nc`, 120 pings *during* the copy:

| | old cable | new cable |
|---|---|---|
| loss under load | 34–55% | **0%** |
| throughput | ~124 MB/s | **261 MB/s** (~84% of 2.5GbE line rate) |

Two things we get wrong-way-round credit for. The `energy-efficient-ethernet`
flag, which we listed as a prime suspect, is **still set** and was irrelevant —
the cheapest hypothesis was the correct one, and the more interesting one was a
distraction. And the resolution obliges us to **re-weight §6**: we attributed the
reclaim storm to memory starvation, and memory pressure was real, but the reaper
fired because the host's heartbeat did not reach the database for 121 seconds.
A link losing half its packets under precisely the load a 92 GB copy generates
is a better explanation of that silence than memory alone. We leave §6 as
written — it reports what we believed with the evidence we had — and record the
correction here, which is the honest form.

---

## 5. Probes that cannot return the failing answer

This is our principal contribution, and it emerged only because we kept a
per-incident record. Nine mechanisms reported health, or a plausible wrong answer, while the thing
they observed was broken or absent. They are mechanically unrelated; what they share is that
**the failing state was outside the probe's expressible range**.

| # | Probe | Reported | Reality | Mechanism |
|---|---|---|---|---|
| 1 | `timeout 120 docker kill …` | "Docker wedged" | Docker fine | **`timeout` does not exist on macOS** — the shell returned `command not found` and the command never ran |
| 2 | `pgrep -f "curl.*_transfer"` | transfer running | dead **45 min** | The pattern matched the probe's **own ssh command line** |
| 3 | `pkill -f osm-sync-retry.sh` | cleanup done | nothing happened | Killed **its own shell** by the same self-match |
| 4 | `rsync --partial` + retry loop | transfer healthy | **22 min at 0 bytes** | rsync has **no I/O timeout by default**; a hung connection never errors, so the retry loop never fires |
| 5 | Stall detector v1 | no stall | one transfer stalled | Required **both** transfers stalled to report one |
| 6 | `du -sb` progress | 90% complete | 76%, 41 GB missing | Counting a **duplicate the operator had created** |
| 7 | Image-convergence check | "23/23 up-to-date" | 22 hours on a stale image | Counted *registered runners*, not the **image they ran** |
| 8 | `stat -f %z f \|\| stat -c %s f` | a byte count | a **filesystem block report** | `-f` is BSD's *format* but GNU's *filesystem status*: on Linux the first form SUCCEEDS, so the fallback never fires |
| 9 | `fleet status` → `applied_version` | agents current | four agents running **3-day-old code** | Reports what the agent last **recorded**, not what it is **executing**; `git pull` updates files, not a running interpreter |

Four caused real harm. #1 nearly triggered an unnecessary Docker restart on the
object-store host. #2 hid a dead transfer for 45 minutes. #3 made a cleanup
silently no-op, letting 51 GB of junk accumulate. #4 wasted 22 minutes and, more
seriously, meant the resilience mechanism we had *just added for this exact
purpose* was inert.

Three observations follow.

**Probe failure is not the same as monitored-system failure, and is more
insidious.** A monitoring gap is an absence you can notice. A probe that cannot
express the failing state produces a *positive assertion of health*, which
terminates investigation.

**Cross-platform heterogeneity is a silent generator of this class.** Five of the
eight (#1 and #8, plus `ps -eo`, `du -sb`, and `pgrep -c`) arise from a fleet
mixing GNU and BSD userland. A probe written on one and run on the other does not
usually error — it returns something plausible.

⚠️ **#8 is the worst shape in this table, and it appeared last.** The others
fail loudly enough to be noticed once you look. This one is a *defensive
fallback that defeats itself*: the idiom `stat -f … || stat -c …` is written
precisely to be portable, and it is portable in one direction only, because the
same flag means different things rather than being absent. On Linux the BSD form
does not fail over — it succeeds and returns filesystem statistics where a byte
count was expected. A portability guard that silently returns the wrong kind of
answer is worse than no guard, because it is *evidence of having thought about
the problem*. The fix is to use primitives with no dialect (`wc -c`), not to
order the fallbacks better.

**Self-matching is endemic to pattern-based process probes.** `pgrep -f` and
`pkill -f` match against full command lines, and a probe dispatched over ssh puts
its own pattern on a command line on the target host. This is a general hazard of
remote administration, not a quirk of these tools.

⚠️ **#9 is the one we would least have predicted, and it invalidated three
diagnoses.** A long-lived daemon does not follow its own source: `git pull`
rewrites files while the interpreter keeps executing what it loaded at start.
Four fleet-agents had been running since 14–15 September, so every fix of that
week sat on disk unused — and *nothing anywhere reports it*. The convergence
view shows `applied_version`, which is what the agent last **recorded**; the
agent's own liveness watchdog checks that it is **alive**, not that it is
**current**. Three times that week we diagnosed a second bug when the real
answer was that the first fix had never loaded. The repair was to make the
daemon re-exec when its own source changes, which also removes the root access
that a restart-per-change otherwise demands on every host.

**Mitigations we adopted.** Measure the *artifact*, not a proxy for it — byte
counts on disk rather than process liveness. Prefer probes that can only be
satisfied by the thing actually working (a `COMPLETE` marker written by the job,
not an inference from progress). Always ask, before trusting a green check,
*what would this return if the system were broken in the way I am worried
about?* Where a probe's negative result is load-bearing, test it against a known
failure — we now do this routinely for code, and it is exactly what we had not
done for the operational probes.

---

## 6. When the recovery mechanism is the amplifier

The most expensive single incident combined a resource shortage with a recovery
mechanism that assumed the opposite failure.

On the old host, a planet update ran while the machine had **60 MB of free
memory** (32 GB physical, a 24 GiB container VM, an object store, a registry and
23 runners). The runner process wedged — container `Up`, `restarts=0`, last log
line 26 minutes old, heartbeat thread stopped. The dead-server reaper's 120-second
window elapsed, the task was reclaimed, and the reclaimed execution **began a
fresh 92 GB copy while the original continued**. Four ran concurrently, each at
~2.7 MB/s, competing for exactly the memory whose exhaustion caused the wedge.
Every recovery attempt made recovery less likely, and the run reported progress
throughout.

Three lessons.

**At-least-once delivery without cancellation makes reclaim an amplifier.** The
platform's contract permits duplicate execution and requires handler
idempotency. This handler was *safe* (a uuid-named temp file meant the output
was never corrupted) but not *cheap* — each duplicate cost a full 92 GB copy.
Idempotency is usually discussed as a correctness property; here the gap was
economic, and the correctness argument concealed it.

**A domain cannot protect itself by configuration.** The domain had carefully
raised its execution and stuck-task timeouts to 8 hours. But
`FW_REAPER_TIMEOUT_MS` is read from the environment of the **reaping** runner,
so raising it in one domain only makes that domain lenient toward others, while
~130 other runners continue to reap it at 120 seconds. The fix had to be
handler-side: refuse to start a second concurrent update, and clean up abandoned
temp files (`9d9c16c`).

**A wedged host is worse than a dead one.** It holds memory, keeps its container
`Up`, and each reclaim adds load to the host least able to bear it.

---

## 7. What the routing model did, and what it could not express

The migration's compute side was close to free, and the reason is worth stating
precisely. Both the old and new hosts are in the same capability tier
(`heavy`), so the *group* mechanism could not distinguish them. What moved the
work was an intrinsic fact: the 92 GB planet file exists on exactly one host, so
`Requires(dataset_planet_gb = 80)` resolves there and nowhere else. Verified
against a pre-move baseline: 4 advertising runners on the old host → 0; 0 on the
new → 4. No workflow definition changed.

This is the thesis's intrinsic-routing claim (`paper-intrinsic-routing.md`)
operating on a dimension we had not previously exercised at this scale, and it
is the strongest positive result in this report.

Two things the model could **not** express:

**Co-location.** One step extracts to local files and the next publishes them.
No value flows that the compiler can see — only a path string, which means
nothing on another host. An unpinned publish step was claimed by a host with an
empty output directory, uploaded nothing, and **reported success**: the tier came
back 36% refreshed with every step green. We repaired it twice: the handler now
fails when it publishes none of what it was asked for (`bace3c5`), and the call
site carries a matching resource pin so both steps route identically
(`c74ed50`). Neither is a real co-location primitive, and we consider the
underlying gap open — the sound fix is the one the codebase already uses
elsewhere, namely doing extract-and-publish in a single task.

**Instruction set.** One host is a 2009 CPU lacking SSE4.2 and POPCNT, so it does
not meet `x86-64-v2`. The image's numpy is built to that baseline: `pip install`
**succeeds** and only `import` raises. Because handler modules import numpy
*inside functions*, the registry's importability check passed and the host
advertised **265 facets** it could not execute. It reported healthy and would
have failed at dispatch. No group, no memory floor and no dataset dimension
expresses this; all three are about capacity, and this is about the instruction
set. We removed the host. The residual insight is that **a host that silently
fails a subset of tasks is more dangerous than one that cannot join**, and our
capability model — like every resource model we know of — has no vocabulary for
it.

---

## 8. Recommendations

For operators of small heterogeneous fleets:

1. **Name filesystems by UUID, always.** Device names are context-dependent, and
   the failure appears at the next unplanned reboot.
2. **Verify mounts by rebooting.** `mount -a` tests parsing, not ordering. Record
   the mount and daemon timestamps; inverted, they mean a container is holding an
   empty directory.
3. **Order the container runtime after the mount** where the init system allows
   it, and sequence it by hand where it does not.
4. **Never place migration scratch inside the tree being migrated.** It becomes
   payload to the tool and to your progress metrics.
5. **Compute checksums at migration time**; treat a stored checksum with an mtime
   older than its subject as absent. Delete checksums that cannot be maintained.
6. **Measure links under load.** An idle ping cannot see a load-correlated fault,
   and error counters will not either.
7. **Test your probes against a known failure**, exactly as you would test code.
   Ask what the probe returns when the system is broken in the way you fear.

For designers of distributed runtimes:

8. **Reclaim must consider cancellation.** At-least-once plus an uncancelled
   original turns recovery into amplification when the work is expensive.
9. **A timeout read from the observer's environment cannot be tuned by the
   observed.** Either make it a property of the task or document loudly that it
   is global.
10. **Capability models need a vocabulary for "can run" as distinct from "has
    capacity".** Instruction set, kernel features and driver availability are
    admission criteria that memory and disk floors cannot express.

---

## 9. Closing the gap the report opened

§1 recorded that the only irreplaceable state in this fleet had no backup, and
that nothing in the repository did. That is now addressed, and the shape of the
solution follows this paper's own argument.

`mongodump` runs inside the database's own container, so no host carries the
tooling and the dump version cannot drift from the server. The output is a
single `--archive --gzip` stream: one file to verify, ship and restore.

Every run verifies the **artifact**, not the exit status — valid gzip before
sending, the expected collection names present (a large, valid, *empty* dump
means the tool is pointed at the wrong server), byte-count equality after
transfer, and a second gzip test on the copy at rest. `--check` answers the
standing question, with the same load-bearing exit codes as the OSM watchdog:
**0** healthy, **1** stale or broken, **2** could-not-verify. The third is not a
courtesy. A check that alarms because the archive host is merely offline teaches
the operator to dismiss it, which is the mechanism by which the original gap
survived unnoticed for the fleet's whole life.

Three defects surfaced only by running it, all of a piece with §5: it resolved
the database host to an IP and failed host-key verification; it took an ssh hop
to itself when run on the very host `--install` places it; and it counted bytes
with `stat -f` (#8 above). None would have appeared in review.

**And the backup was then restored.** Into a throwaway container, with every
collection compared against live:

| collection | live | restored |
|---|---|---|
| workflows | 2045 | 2045 |
| steps | 2362 | 2362 |
| tasks | 1412 | 1412 |
| handler_registrations | 700 | 700 |
| runners / servers / afl_sources / fleet_config | 151 / 106 / 1 / 1 | identical |

We record this because the distinction is the whole point of the section: a
backup that is valid gzip and contains plausible collection names is *readable*.
Only a restore with matching counts shows it is a **backup**. The archive host is
a machine dropped from the fleet for being unable to run the runtime — a backup
target needs sshd and disk, so the defect that disqualified it is irrelevant to
the role.

---

## 10. Limits

This is a single-fleet experience report over three weeks, one operator, seven
hosts, one workload family. The incident frequencies are not rates and should not
be read as such. The probe-failure catalogue is the part we expect to generalise;
the specific hardware faults (one marginal link, one 2009 CPU) are anecdotes
whose value is illustrative. We did not attempt a controlled comparison against
an alternative runtime, and the positive routing result would look different on a
fleet whose hosts were genuinely interchangeable — the dimension did work
*because* the data was expensive and unique, which is the case where it matters
and also the case most favourable to it.

---

*Companion papers: `paper-informal-fleet.md` (operating the fleet),
`paper-intrinsic-routing.md` (routing from intrinsic facts),
`paper-timeout-interactions.md` (the timeout hierarchy),
`paper-geofabrik-replacement.md` (the OSM pipeline this data serves).*

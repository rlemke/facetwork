# Prompts for working with Facetwork

Ready-to-use prompts for Claude Code, grouped by what you are trying to do.
Copy one, edit the bracketed parts, paste.

**Why prompts and not just docs.** Most of these ask Claude to *inspect your
actual system before recommending anything* — which host has the memory, what
the catalog says, what is already installed. A prompt that starts "read X, then
tell me" produces grounded answers; one that starts "how do I…" produces
plausible ones.

**A note on how to phrase them.** Every prompt below ends by asking for what was
*measured*, not just a conclusion. That is deliberate: this system's most
expensive failures have been confident answers from checks that could not return
the failing result. Ask for the evidence.

---

## 1. "What do I actually need?"

### 1.1 First contact — what is this and what do I install

```
Read CLAUDE.md and docs/getting-started/install.md. I have [describe your
machines: how many, OS, RAM, disk]. I want to [what you want to do].

Tell me: which repos I need to clone, what has to be installed on each machine,
and what I can skip. Be explicit about what is OPTIONAL — I don't want the OSM
pipeline unless I need it. Show me the smallest thing that runs end to end.
```

### 1.2 I only want to write my own handlers — no existing domains

```
I want to run Facetwork with NONE of the fwh_* domains — only handlers I write
myself. Read domains.json and docs/reference/domain-catalog.md.

Tell me: what the minimum install is, how to keep the domain catalog from
pulling in anything, and whether the built-in fw.* facets (file/http/archive/
exec/compare/provenance) still work without any domain installed. Show me a
hello-world workflow with one handler of my own.
```

### 1.3 I want a subset of the existing domains

```
I want these domains only: [list, e.g. census-us, noaa-weather]. Read
domains.json and docs/reference/domain-catalog.md.

Tell me how to restrict the catalog to exactly those — I understand there is a
domains.local.json override and an FW_DOMAINS_FILE. Show me which one fits, what
each chosen domain drags in (deps, credentials, disk), and confirm the runners
will not try to start roles for domains I excluded.
```

---

## 2. Deployment shapes

### 2.1 A few machines at home, varying capability

```
I have [N] machines: [list each with cores/RAM/disk/OS]. Read
docs/operations/informal-fleet.md and docs/architecture/server-groups.md.

Propose a layout: which host runs the shared MongoDB and object store, which
hosts are `heavy` / `medium` / `runner`, and why. Base the tiers on MEASURED
capacity, not the spec sheet — container memory is what matters, not host RAM.
Then give me the exact commands per host, in order.
```

### 2.2 A small company cloud with no dedicated ops

```
We have [describe: e.g. 3 VMs on a company OpenStack, no ops team]. Nobody here
administers clusters. Read docs/operations/deployment.md and
docs/operations/informal-fleet.md.

I want the least operational surface that is still honest about durability. Tell
me what MUST be reliable (and therefore backed up) versus what can be
disposable, and what breaks if a runner VM disappears. Include the backup story:
what is irreplaceable here?
```

### 2.3 A large company cloud with real IT engineers

```
We have a platform team, Kubernetes, and managed databases. Read
docs/operations/informal-fleet.md — particularly what it says about data-centre
operation being "architecturally possible but untested".

Give me an honest assessment: what would we be signing up to build ourselves
(scheduling, observability, hardening), what the runtime already gives us, and
where the genuine gaps are. Do not sell it to me — I need to brief engineers who
will find the gaps anyway.
```

### 2.4 Managed cloud (GCP / Azure / AWS)

```
I want to run this on [GCP|Azure|AWS] using managed services where possible.
Read docs/operations/deployment.md and facetwork/runtime/storage.py.

Tell me which pieces map to managed services cleanly (object store almost
certainly; MongoDB maybe), which do not, and what the runner hosts need to be.
Flag anything where the mapping is NOT clean — I would rather know now.
```

---

## 3. Swapping infrastructure — and what that really costs

> ⚠️ **Read this before promising yourself a swap.** The two boundaries are very
> different, and the difference is not obvious from the code.

### 3.1 Object store — genuinely pluggable

`facetwork/runtime/storage.py` selects a backend from the environment:
`local` (a path), `hdfs://`, or `s3://` (AWS S3 or a self-hosted MinIO). Three
live implementations, chosen by `FW_STORAGE` / `FW_S3_*` / `FW_HDFS_*`. Handlers
receive portable URIs, so any runner on any host can resolve them.

```
I want to use [S3 | an existing company MinIO | HDFS | a local path] as the
object store instead of a bundled MinIO. Read facetwork/runtime/storage.py and
the S3/MinIO section of docs/operations/deployment.md.

Show me the exact environment for the runners, what changes in the compose
files, and how to verify a handler actually reads and writes through it — not
just that the endpoint answers.
```

### 3.2 Database — the boundary exists, but it is wide, and passing tests will not prove it works

`PersistenceAPI` is an explicit Protocol and **all** database access goes
through it. There are two implementations: `MongoStore` and an in-memory
`MemoryStore`. So a third is *possible*.

Two things to know before you start:

- **It is 68 methods.** That is the implementation cost, and it includes the
  atomic claim, leases, and the partial-index semantics the scheduler depends
  on — not just CRUD.
- ⚠️ **Passing the test suite would not prove it correct.** `MemoryStore`
  implements the same protocol and roughly 1,200 tests run against it — and
  `paper-parity-gaps.md` documents how the `catch` clause passed all of them
  while being completely broken in distributed execution, because a
  single-address-space implementation *structurally cannot* exhibit the
  coordination bugs that matter. A new backend needs multi-process testing
  against real concurrency, not a green suite.

```
I want to replace MongoDB with [Postgres | DynamoDB | …]. Read
facetwork/runtime/persistence.py, facetwork/runtime/mongo_store/, and
docs/thesis/paper-parity-gaps.md.

Tell me honestly: how many methods, which ones carry real semantics (atomic
claim, leases, partial unique indexes) versus plain storage, and what a test
plan would have to look like given that a single-process double passed 1,200
tests while `catch` was broken. I want the cost, not encouragement.
```

### 3.3 Using an existing company MongoDB

```
We already run MongoDB at [host/URI]. Read docs/operations/deployment.md and
docs/reference/server-catalog.md.

Show me how to point the fleet at it instead of a bundled instance, what the
runners need (containers do not read the host's /etc/hosts), and which indexes
and permissions it requires. Also: what happens to running clients when that URI
changes — I gather pools do not re-resolve.
```

---

## 4. Writing your own facets and handlers

### 4.1 A new domain repo from scratch

```
I want a new domain package called [name] in its own repo. Read
domain-template/, agent-spec/tools-pattern.agent-spec.yaml, and
docs/architecture/extending-with-new-handlers.md.

Scaffold it: FFL for the facets, Python handlers, tests, and the
facetwork.domains entry point. My first facet should [describe]. Follow the
existing conventions rather than inventing new ones, and tell me which
conventions you followed.
```

### 4.2 Add one facet to an existing domain

```
Add a facet [ns.Name] that takes [params] and returns [returns], with a handler
that [does what]. Use `fw ffl scaffold` if it fits.

Then validate with `fw ffl compile --check` and show me the validator output. If
it emits a rule_id I do not understand, fetch fw://docs/rules/{rule_id} and
explain it rather than guessing at a fix.
```

### 4.3 Reuse before you author

```
I want a workflow that [describe the outcome in plain language]. Before writing
any FFL, use fw_catalog_match to see whether something already does this, and
fw_capabilities to find facets I can compose.

Only author something new if the verdict is author_new — and if you do, tell me
what you searched for and why nothing matched.
```

---

## 5. Environments and dependencies

### 5.1 Declare an environment for a script facet

```
I have a script facet that needs [packages]. Read
docs/architecture/script-environments.md and
examples/canonical/11-environment-script.ffl.

Write the `environment` declaration and the `in environment` binding, then tell
me how the manifest hash reaches the runners and what happens on a host that has
not materialised it. I want to understand the failure mode BEFORE I deploy it.
```

### 5.2 Make sure a host can actually run it

```
Check whether [host] can run the environments and handlers it advertises. Run
`fw install check` and `fw maint unsatisfiable`.

⚠️ Note that "installed" is not "runnable": materialization now smoke-imports
every declared pin, because pip can succeed on a host whose CPU cannot execute
the result. Tell me what was actually imported, not just what is present.
```

### 5.3 A task is pending and nothing claims it

```
Task [id/facet] is pending and no runner takes it. Run `fw maint unsatisfiable`
and check all THREE routing dimensions: resource floor (`requires`),
`environment_hash`, and `required_features`.

Absent means DECLINE on every one of them, so tell me which dimension nobody
advertises — and whether the answer is "no host can" or "the check cannot tell".
```

---

## 6. Docker and day-to-day operations

### 6.1 What is the fleet doing right now

```
Give me fleet status: `fw fleet status`, then per-host `docker ps` for anything
that looks wrong. For each discrepancy tell me whether it is a real fault or a
known exemption — the Java gh-router runs a different image by design, and a
container younger than ~2 minutes has not registered yet.
```

### 6.2 A host shows no runners

```
[host] shows NO RUNNERS ALIVE. Work through, in order: are containers running or
crash-looping (`docker ps -a`, RestartCount); what the logs say; whether the
host can reach MongoDB and the object store; whether a mount the containers bind
is missing.

⚠️ Check the seed step specifically — entrypoint.sh captures its stderr into a
variable under `set -e`, so a failure there exits before anything is printed.
```

### 6.3 Restart / recreate something safely

```
I need to restart [container/role] on [host]. Tell me first what it will
interrupt: running tasks, and whether the reaper will reclaim them.

⚠️ If it binds an external volume, check the volume is mounted BEFORE recreating
— Docker will invent a missing bind source as an empty directory and the
container will look healthy while writing to the wrong disk.
```

### 6.4 Roll out a change

```
Roll out the current HEAD: `fw fleet rollout --stagger-best-effort`. Before
starting, tell me what is in it and whether anything changes shared state that
OLDER images validate at startup — an index, a schema, a required field.

⚠️ Those need expand/migrate/contract: deploy tolerant code everywhere FIRST,
migrate second. Doing it the other way took this fleet from 108 runners to 23.
```

### 6.5 Is anything quietly broken

```
Run the standing checks and report only what is actionable: `fw maint
dead-letters`, `fw maint unsatisfiable`, `fw fleet unregistered` on each host,
and `fw svc osm-watchdog --check` if OSM is deployed.

For each, tell me what its exit code means — several use 0/1/2 where 2 is
"could not verify" and deliberately does NOT alarm.
```

---

## 7. Diagnosis prompts worth having

These come from failures that actually happened here.

### 7.1 Check the checker

```
I am about to trust [check/monitor/probe] to tell me whether [thing] is working.
Before I do: what would it return if [thing] were broken in the way I am worried
about? Construct the failing case and run it.

Context: several probes here have reported health while the subject was broken —
a pattern-based process check matching its own command line, a retry loop that
never fired because rsync has no default I/O timeout, a stall detector that
required BOTH watched transfers to stall. A green check is not evidence until it
has been shown capable of going red.
```

### 7.2 After moving data between hosts

```
I moved [data] from [host A] to [host B]. Verify the move is COMPLETE, not just
that the bytes match:

1. `fw maint path-check` on the destination — configs that embed absolute paths
   transfer byte-for-byte and are then wrong.
2. Root-level files beside the directories I synced, which a
   directory-by-directory comparison does not cover.
3. Timers: `crontab -l` and `launchctl list | grep com.facetwork` on BOTH hosts.
   Schedules do not move with a role, and one living in another repo will not
   appear in any audit of this one.
4. Anything that references the old path — consumers, not just the data.
```

### 7.3 A long-running step keeps restarting

```
[facet] keeps being reclaimed and restarted. Check whether the runner's HEARTBEAT
is reaching Mongo, not just whether the handler is alive — the dead-server reaper
(120s) is the real duplicate-execution window, and it fires on host silence.

⚠️ Reclaim does not stop the original execution. If the handler is expensive,
confirm it is idempotent in the COST sense, not only the correctness sense: four
concurrent 92 GB copies once ran here, each making the others slower.
```

### 7.4 Is this result trustworthy

```
[workflow] reported success. Before I believe it, check that it produced what it
claimed: counts against expectations, outputs actually written where they should
be, and whether any step "succeeded" with an empty result.

Context: a publish step once uploaded nothing from a host with an empty
directory and reported success — the tier came back 36% complete with every step
green.
```

### 7.5 Cross-platform gotcha check

```
I am writing a script that will run on both macOS and Linux hosts in this fleet.
Review it for BSD/GNU divergence before I deploy it.

Known traps here: `timeout` does not exist on macOS; `stat -f` is BSD's *format*
but GNU's *filesystem status* (so the usual fallback silently returns the wrong
kind of answer on Linux); `du -b` is GNU-only; `ps -eo` is GNU; `pgrep -f`
matches the probe's own ssh command line.
```

---

## 8. Prompts for understanding the system

### 8.1 Explain a failure honestly

```
[Paste error/symptom]. Diagnose it, but tell me explicitly which parts are
MEASURED and which are inference. If a check you ran cannot distinguish two
causes, say so rather than picking the likelier one.
```

### 8.2 Review a design before I build it

```
I am about to [describe]. Review it against docs/architecture/lessons-learned.md
and the execution semantics in docs/reference/execution-semantics.md.

Specifically: what happens if this crashes halfway; is it idempotent given
at-least-once delivery with no fencing token; and what does it look like when it
fails SILENTLY rather than loudly.
```

### 8.3 What is outstanding

```
Read docs/operations/open-items.md and tell me what is worth doing next given
[my constraint: time / risk appetite / what I care about]. Distinguish items
that are mechanical from ones that need a decision from me.
```

---

## Prompts that do not work well

Worth saying, so you do not waste a round:

- **"Is everything OK?"** — produces reassurance. Ask for specific checks and
  their exit codes.
- **"Fix the fleet."** — too broad; the first step is always to find out which
  of several unrelated things is wrong.
- **"Make it faster."** — without a measurement, this yields plausible-sounding
  changes. Ask for a measurement first.
- **Anything asking to apply one fix to N similar-looking items.** Four
  "launchd-only installers" here turned out to be two timers and two supervised
  daemons; a uniform pass would have created servers that multiply every tick.

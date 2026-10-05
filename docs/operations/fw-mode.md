# `fw mode` — day-cluster / night-local switch

Your work machine is part of the shared fleet during the day; at night you want it
local. `fw mode` makes that switch one reversible command. It covers **two
different needs** that "go local at night" conflates — keep them separate:

| | Model A — join / leave | Model B — local / cluster |
|---|---|---|
| **You want** | to stop *lending* this machine to the cluster overnight | to keep *working* on Facetwork while disconnected |
| **Infra** | unchanged — still the shared Mongo/MinIO | flips to this machine's own Mongo/MinIO/registry |
| **Command** | `fw mode leave` / `fw mode join` | `fw mode local` / `fw mode cluster` |
| **Cost** | near-free — drain + stop containers; reaper re-claims | a local deployment must exist; recreates runners |
| **Most people want** | **this one** | only if you truly work offline |

```
fw mode status                 # active mode + where infra resolves + runner state
fw mode leave [--dry]          # Model A: stop serving the cluster (infra untouched)
fw mode join  [--dry]          # Model A: start serving it again
fw mode local   [--dry]        # Model B: run the whole stack on this machine
fw mode cluster [--dry]        # Model B: rejoin the shared fleet as a runner
```

**Going away with only this machine?** Follow the
[runbook — going standalone](#runbook--going-standalone-for-travel-cluster--one-machine)
and, on return, [coming back](#runbook--coming-back-one-machine--cluster). `fw mode`
is the core of both, but not all of either: drivers to pause, a backup to take, the
local image and dashboard to bring current, and a verification table.

## Model A — join / leave (the common case)

Facetwork infra is URL-addressed and every runner is stateless and leaderless (the
[informal fleet](informal-fleet.md) model), so a machine can come and go freely:

- **`fw mode leave`** resets this host's in-flight tasks to `pending` (the reaper /
  other hosts re-claim them within `FW_REAPER_TIMEOUT_MS`) and stops its runner
  containers. Mongo/MinIO/dashboard keep running; you're still pointed at the same
  cluster, just not lending compute.
- **`fw mode join`** starts the runner containers again; they re-register and claim.

No data moves, no config changes. This is what "take my laptop home at night"
usually means.

## Model B — local / cluster (work offline)

This flips **where infra lives** and recreates the runners against it. Use it only
on a machine that has its own local deployment (Mongo + MinIO + registry + data),
such as the laptop ([standalone-laptop.md](standalone-laptop.md)).

Each switch is driven by a gitignored, host-local profile — `mode.local.json` and
`mode.cluster.json` — holding the handful of values that differ:

| key | `local` (this machine) | `cluster` (shared host) |
|---|---|---|
| `infra_host` / `infra_ip` | this machine | the infra host (IP **re-resolved live**) |
| `fleet_registry` | `host.docker.internal:5050` (local `registry:2`) | `<registry-host>:5050` (from the server catalog) |
| `mongodb_url` / `s3_endpoint` | `afl-mongodb` / `afl-minio` → localhost | → the infra host |
| `data_dir` / `data_root` | local scratch + `s3://afl-cache` | same names, remote |
| `server_catalog` | `local` (writes `servers.local.json`) | `none` (committed defaults govern) |
| `require_reachable` | `false` | `true` |


**Leave `infra_ip` empty.** It is re-resolved from `infra_host` on every apply, so a DHCP or subnet change self-heals; pinning a value defeats that and goes stale silently — the containers keep the address they were created with, while `fw mode status` reports the infra host unreachable. Resolution deliberately skips loopback: a machine's own `.local` name resolves to both `127.0.0.1` and its LAN address, and `127.0.0.1` inside a container points at the container itself, not the host.

**In `local` mode the containers get no address at all.** Since infra *is* this machine, `FW_INFRA_IP` is written as Docker's **`host-gateway`** alias (`catalog.container_ip()`), which Docker maintains — so the mapping survives a reboot onto a new DHCP lease, a subnet change, or no network at all, with nothing to re-resolve and no per-reboot edit. Only `cluster` mode carries a real address, because there infra is a different machine. `fw mode status` shows both: the live-resolved IP it probes, and what the containers actually use. See [server-catalog.md](../reference/server-catalog.md#when-infra-is-this-machine-host-gateway-not-an-address).

Switching `fw mode local|cluster`:
1. resolves the target infra IP (live, so DHCP drift self-heals),
2. **refuses if `require_reachable` and the target Mongo doesn't answer** — so you
   can't strand the box pointing at a powered-off cluster (`--dry` still previews
   and just *notes* the refusal),
3. rewrites `FW_INFRA_*` / `FW_MONGODB_URL` / `FW_S3_ENDPOINT` / `FW_DATA_*` /
   `FW_FLEET_REGISTRY` in `.env` + `.env.fleet`,
4. enables/disables `servers.local.json` (moved to `.disabled` for `cluster`),
5. points `/etc/hosts` `afl-mongodb`/`afl-minio` at the target (needs `sudo`;
   prints the line if unavailable — containers use compose `extra_hosts`, so this
   is only for the host-side CLI/`mc`; **`afl-postgres` is never touched**),
6. **re-resolves `FW_MONGODB_URL` / `FW_S3_ENDPOINT` through the catalog it just
   activated**, then restarts the long-running fleet-agent and runs
   `fw fleet agent apply` to recreate the runners (the `fw` dispatcher resolves
   `afl-*` names when the command *starts* — before step 4 — so without this the
   apply inherited the OLD world's address; see the traps below),
7. **counts the result** — `runners addressing <mode>: N ok, M STILL ON THE OTHER
   WORLD` — and stamps `.fw-mode`. Treat any `M > 0` as unfinished.

### ⚠️ The honest boundary — state does not merge

Config flips cleanly; **runs and data do not.** `local` and `cluster` are separate
Mongo databases and separate object stores. In `local` you see your local runs; in
`cluster` you see the fleet's. Switching does **not** sync workflow state between
them — full bidirectional sync (with conflict resolution on in-flight step state)
is a hard problem and deliberately out of scope. On a laptop, run the local MinIO
as a warm read-through cache that fills on demand rather than mirroring the whole
fleet; the first offline run of each domain is just slower.

### Standing up a machine for Model B

See [standalone-laptop.md](standalone-laptop.md) for the full one-time setup (local
Mongo restore, MinIO, a local `registry:2` for rebuild independence, and data). Note
in particular: **create every bucket the fleet writes to** (e.g. `afl-cache`), even
the ones you skip mirroring, or the first output write fails `NoSuchBucket`.

---

## Runbook — going standalone for travel (cluster → one machine)

The whole procedure, in order, as run on 2026-10-04. Every step ends in something
you can check. Machines are named by role: **the database host** (cluster
MongoDB), **the infra host** (MinIO, image registry, extracts server), **this
machine** (the one that will run alone).

**1. Stop long-running drivers on the cluster, and anything that auto-starts.**
Work in flight when the cluster powers off is not lost — tasks are re-claimed and
resumable drivers pick up from their ledgers — but a driver that restarts itself
on boot will resume *unattended* the moment the cluster comes back. Comment out
its `@reboot` cron line (or stop its timer) and note where it stopped. Kill a
process by a pattern anchored at its start (`pkill -f '^/path/python tools/x.py'`):
an unanchored pattern also matches the `ssh … 'pkill …'` shell that runs it.

**2. Back up the cluster database onto this machine** — it is the only copy you
will be able to reach:

```bash
ssh <database-host> 'docker exec facetwork-mongodb mongodump --quiet --archive --gzip' \
  > ~/fw_backups/mongo/mongo-$(date +%Y%m%d-%H%M).archive.gz
gzip -t ~/fw_backups/mongo/mongo-*.archive.gz && echo ok
```

**3. Start this machine's own infra and probe EVERY bind mount.** The local
MongoDB / MinIO / registry containers are stopped while in cluster mode.

```bash
docker start facetwork-mongodb facetwork-minio facetwork-registry
for c in facetwork-mongodb facetwork-minio facetwork-registry; do
  for d in $(docker inspect $c --format '{{range .Mounts}}{{.Destination}} {{end}}'); do
    docker exec $c ls "$d" >/dev/null 2>&1 && echo "$c $d ok" || echo "$c $d DEAD"; done; done
```

A container that started before its external disk mounted holds a **dead** file
descriptor while reporting healthy; `docker ps` and the MinIO health endpoint both
say fine. Recreate any `DEAD` one.

**4. Preview, then switch.**

```bash
fw mode local --dry      # must show "mongo-probe … reachable" for THIS machine
fw mode local
```

Read the last lines: `runners addressing local: 24 ok, 0 STILL ON THE OTHER
WORLD`. If the count is not zero, find the stragglers —

```bash
for c in $(docker ps -a --format '{{.Names}}' | grep facetwork-runner); do
  echo "$c $(docker inspect -f '{{.State.Status}}' $c) \
    $(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' $c | grep ^FW_MONGODB_URL=)"; done
```

— a container left `created` (never started) by a failed earlier attempt still
carries the old address: `docker rm` it.

**5. Put the local fleet on the CURRENT image.** The local `fleet_config` keeps
whatever image it had when this machine last ran alone — possibly weeks of fixes
behind. The current image is already in this machine's Docker from cluster mode:

```bash
IMG=facetwork-runner:<current-tag>
docker tag <cluster-registry>:5050/$IMG localhost:5050/$IMG && docker push localhost:5050/$IMG
docker tag localhost:5050/$IMG host.docker.internal:5050/$IMG   # see trap 3
fw fleet set --image host.docker.internal:5050/$IMG             # local fleet_config
launchctl kickstart -k gui/$(id -u)/com.facetwork.fleet-agent   # macOS
docker ps --format '{{.Image}}' | grep runner | sort | uniq -c  # wait for all on $IMG
```

**6. Recreate the dashboard against the local database.** It is a compose service
whose MongoDB alias is fixed at container creation; an old container can carry a
stale address and show another world (or nothing):

```bash
FW_INFRA_IP=host-gateway FW_DASHBOARD_MONGODB_URL=mongodb://afl-fleet-mongodb:27017 \
  docker compose -p facetwork -f docker-compose.full-stack.yml up -d --no-deps --no-build dashboard
```

**7. Verify the outcome, not the intent.**

| check | expect |
|---|---|
| `fw mode status` | `Active mode: local`, MongoDB and MinIO reachable on this machine |
| `FW_MONGODB_URL` of every runner container (step 4 loop) | all this machine |
| live runner records in the LOCAL `servers` collection | one per runner, pinging |
| runner images (`docker ps --format '{{.Image}}'`) | all the current tag |
| `curl -s localhost:8080/health` | 200, and the fleet page lists only this machine |

**8. Power off the cluster** — runner hosts first, the database host and the infra
host last (`sudo shutdown -h now`; MongoDB journals, so order is tidiness, not
safety).

## Runbook — coming back (one machine → cluster)

**1. Power the cluster on**, database host and infra host first. Confirm the
database host is on the address the catalog expects — a host can come back on a
NEW DHCP lease while another device takes the old one, so "ping answers" proves
nothing; probe the service (`nc -z <host> 27017`). Give the database host a DHCP
reservation to make this permanent.

**2. Restart every fleet-agent.** Each agent resolves MongoDB ONCE at start, and
containers carry the address they were created with. On Linux
`systemctl restart facetwork-fleet-agent` (no sudo with the polkit rule, see
[zero-sudo-operations.md](zero-sudo-operations.md)); on macOS the `launchctl
kickstart` above.

**3. Switch this machine back.**

```bash
fw mode status
fw mode cluster --dry    # REFUSES (and says so) if the cluster MongoDB does not answer
fw mode cluster          # expect "… 0 STILL ON THE OTHER WORLD"
```

**4. Stop this machine's local infra** (`docker stop facetwork-mongodb
facetwork-minio facetwork-dashboard`). A leftover local MinIO answering on
`localhost:9000` has been mistaken for the fleet's store by tools that fell back
to localhost, which then reported a stale parallel world authoritatively.

**5. Resume what step 1 paused** — uncomment the `@reboot` line or start the
driver; resumable drivers adopt or redo the work that was in flight.

**Nothing merges.** Runs made while standalone stay in this machine's database and
object store; the cluster's work (and its outputs, such as maps in the cluster
object store) is not visible from the standalone side. See the boundary above.

## Traps — each one measured

| | what happened | check / fix |
|---|---|---|
| 1 | `fw mode local` reconciled the agent against the **cluster** MongoDB: the `fw` dispatcher had resolved `afl-mongodb` through the old catalog before the switch flipped it, and 25 of 25 runners stayed on a cluster about to power off (2026-10-04) | fixed in `fw mode` (re-resolves after the flip); the `STILL ON THE OTHER WORLD` count catches any recurrence |
| 2 | a runner's bind defaulted to a path that exists only on the infra host; Docker Desktop failed the whole runner with `mkdir /host_mnt/…: permission denied` | binds are relative to `FW_DATA_DIR` now; a failed `agent apply` names the service |
| 3 | `docker compose` **hung** pulling from `host.docker.internal:5050` on macOS (600 s timeout) though `docker push`/`pull` to `localhost:5050` worked | tag the image locally under the `host.docker.internal` name so compose finds it present |
| 4 | a long-running agent kept the old world's MongoDB and re-reconciled runners back (2026-09-18) | `fw mode` restarts the agent itself; on return, restart EVERY host's agent |
| 5 | the database host returned on a new DHCP address while an unrelated device answered ping on the old one (2026-09-29) | probe the service port, not ping; DHCP reservation |
| 6 | the dashboard container kept an `afl-fleet-mongodb` mapping from an old configuration | recreate it (step 6) |

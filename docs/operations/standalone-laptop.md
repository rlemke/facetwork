# MaxPro standalone — running the whole stack on one machine

**Goal:** MaxPro runs Facetwork end to end with **zero dependency on server3** — its own
MongoDB, its own MinIO, and OSM data on the locally attached `afl_data_local`. server3 can
then be powered off, rebooted, or left alone without affecting development.

**Status: EXECUTED 2026-07-30.** Data copied and verified, MongoDB restored, and MaxPro cut over to standalone (15/15 runners live against its own Mongo+MinIO). Phases 1-3 below are corrected to as-built — **the original Phase 3 was wrong** and would not have produced a working machine. **Rebuild independence added 2026-07-30 (Phase 3a): a local `registry:2` + one real `fw fleet rollout` verified — server3 is out of the registry path.** **§4 checklist re-run 2026-07-31 with server3 (and server1/2) PHYSICALLY OFF — all pass** (caught + fixed a missing `afl-cache` bucket). The standalone box is fully server3-independent.

---

## 1. Where things stand (measured 2026-07-30, not assumed)

### MaxPro already has most of the pieces

| | |
|---|---|
| Hardware | Mac15,9 · 16 CPU · 64 GB RAM · macOS 26.4.1 |
| Internal disk | 1.8 TB, **only 237 GB free** (Data volume 87% full) |
| `/Volumes/afl_data_local` | `/dev/disk5s1` · 3.6 TB · **3.6 TB free** · already mounted ✅ |
| Docker | 29.4.1, 18 containers running |
| `facetwork-mongodb` | **already running** (`mongo:7`) |
| `facetwork-minio` | container **exists but is stopped** |
| `facetwork-dashboard` | already running |
| Runner containers | 15, all on v137 (`6168c9f-dd8aff42f4015`) |
| Buildx builder | `buildx_buildkit_fleetbuilder0` present — can build images locally |

The internal disk having only 237 GB free is the reason everything below targets
`afl_data_local`, not `~`.

### What ties MaxPro to server3 today

1. **`/etc/hosts`** — `<server3-ip>  afl-mongodb afl-minio server3`
   Every Mongo and S3 call resolves to server3.
2. **SMB mounts** — `/Volumes/afl_data` (15 TB, 11 TB used) and `/Volumes/bigdata`
   (3.6 TB) are network mounts **served by server3**. These vanish when server3 does.
3. **Fleet config** — `mongodb://afl-mongodb:27017`, `http://afl-minio:9000`,
   dashboard `http://afl-mongodb:8080`.
4. **Image registry** — roles pull `server3.local:5050/facetwork-runner:…`.
5. `.env` / `.env.fleet` — `FW_MONGODB_URL`, `FW_S3_ENDPOINT`, `FW_INFRA_HOST=server3.local`.

Note `afl-postgres` is **<postgis-host-ip> — a different machine**, not server3. PostGIS is
only needed for the OSM PostGIS import path; if you want that offline too it is a separate
piece of work, called out in §6.

### Data that must move

| What | Size | Notes |
|---|---:|---|
| MongoDB `facetwork` | **1.3 GB** on disk (0.33 GB logical, 89,639 docs) | Trivial |
| MongoDB `facetwork_examples` | ~0 | 478 docs |
| MinIO `afl-cache` | **363 GB** | All domain caches + published outputs |
| MinIO `osm-extracts/north-america` | **49 GB** | Per-county PBF tree |
| MinIO `osm-extracts` (all continents) | **217 GB** | 8 continents + `-updates` dirs |
| **`osm-selfhost/` (planet + split tree)** | **182 GB** | Plain files, NOT MinIO — see below |
| `cache/` (noaa-weather, osm) | **16 GB** | Plain files — legacy/local domain caches |
| `output/` (published outputs, census-output, maps, osm) | **96 GB** | Plain files — `output/cache` is 87 GB of it |

**Total to move: ~874 GB** — **24% of the 3.6 TB disk.** Nothing needs re-downloading:
every byte already exists on server3.

| Tree | Size | How |
|---|---:|---|
| MinIO `afl-cache` | 363 GB | `mc mirror` (S3) |
| MinIO `osm-extracts` | 217 GB | `mc mirror` (S3) |
| `osm-selfhost/` (planet + continents) | 182 GB | `rsync` |
| `output/` | 96 GB | `rsync` |
| `cache/` | 16 GB | `rsync` |
| MongoDB | 1.3 GB | `mongodump`/`mongorestore` |

**Optional — regenerable or not Facetwork data** (copy only if you want them;
all of it still fits, ~1.2 TB total / 34% of the disk):

| Tree | Size | Verdict |
|---|---:|---|
| `local_servers/` | 249 GB | Identify before copying — not part of the runtime path |
| `osm-scratch/` | 96 GB | **Skip** — transient scratch, regenerated on demand |
| `hadoop/` | 3.1 GB | **Skip** — HDFS is not deployed (`reference_hdfs_not_deployed`) |
| `sim-srv1…6/`, `sim-dbg/` | small | **Skip** — fleet-simulation artifacts |
| `photos*/` | the bulk of the 11 TB | **Skip** — not Facetwork data |

So the answer to "can we just copy rather than re-download": **yes, entirely.**
The weather (`cache/noaa-weather`), census (`output/census-output`), map outputs
(`output/maps`) and every domain cache in MinIO `afl-cache` all come across as
data. No domain has to re-fetch from its upstream API, and no OSM data has to be
re-downloaded.

✅ **The planet already exists — do NOT re-download it.** It is not in the MinIO
bucket, which is why a first pass missed it. It lives on the plain filesystem at
`/Volumes/afl_data/osm-selfhost/` — the self-hosted Geofabrik replacement
(`project_selfhosted_planet_split`):

| Path | Size |
|---|---:|
| `osm-selfhost/planet-latest.osm.pbf` | **87 GB** |
| `osm-selfhost/www/` (8 continent extracts split from planet) | **~93 GB** |
| `osm-selfhost/` total (incl. `polys/`, `regions.json`, `*-updates/`) | **182 GB** |

`www/` is the served tree that replaces Geofabrik — `north-america-latest.osm.pbf` (20 GB),
`europe` (37 GB), `asia` (17 GB), and so on, plus `_poly`, `extract-config.json` and the
per-continent `-updates` dirs for the delta path. `process_continent.py` and `extract.log`
are the split machinery. Copy this tree; it is the whole point of not depending on
Geofabrik.

⚠️ **MinIO objects are erasure-coded directories**, not files — `north-america-latest.osm.pbf`
is a *directory* containing `xl.meta` and part files. **Never `cp`/`rsync` them between
MinIO instances.** Use `mc mirror` (S3 to S3), which is what §3 does.

---

## 2. Target architecture

```
MaxPro (standalone)
  ├── facetwork-mongodb   :27017   → docker volume (1.3 GB, keep on internal disk)
  ├── facetwork-minio     :9000    → /Volumes/afl_data_local/minio
  ├── facetwork-dashboard :8080
  └── 15 runner containers         → FW_S3_ENDPOINT=http://localhost:9000
                                     FW_MONGODB_URL=mongodb://localhost:27017
  /Volumes/afl_data_local/
      minio/           ← afl-cache + osm-extracts        (~580 GB)
      osm-selfhost/    ← planet 87 GB + split www/ tree  (182 GB, plain files)
      scratch/         ← FW_LOCAL_SCRATCH, FW_OUTPUT_BASE (must stay local, never S3)
```

`afl-mongodb` / `afl-minio` are **re-pointed at MaxPro itself**, so no config that
references those names has to change — only what they resolve to.

---

## 3. Migration steps

### Phase 0 — make server3 safe first (do this before touching anything)

The instruction was "make sure everything is safe." Concretely:

1. **Let the county-atlas fan-out finish** (in progress; watchdog running). Do not migrate
   mid-run — the runner state lives in server3's Mongo.
2. **Verify no other workflow is mid-flight:**
   `fw fleet status` and check for runners in `running` state.
3. **Take a Mongo dump on server3** and keep it *on server3* as the rollback copy:
   `docker exec facetwork-mongodb mongodump --archive=/data/db/pre-maxpro.gz --gzip`
   then copy it off the container.
4. **Do not delete anything on server3.** This migration is a *copy*, so server3 stays a
   complete, working fallback. Rollback = revert `/etc/hosts` on MaxPro.

### Phase 1 — MongoDB onto MaxPro

Mongo is 1.3 GB, so this is minutes, not hours.

1. On server3, dump **all databases** — pass NO `--db`:
   `mongodump --gzip --archive=/tmp/fw.gz`

   ⚠️ **`mongodump` silently keeps only the LAST `--db`.** Writing
   `--db facetwork --db facetwork_examples` dumps *only* the examples DB, skips
   everything that matters, and **exits 0**. That produced a 20 KB archive that
   looked fine; the correct all-databases dump was 56 MB.
2. `scp` to MaxPro.
3. On MaxPro, restore into the running `facetwork-mongodb`:
   `mongorestore --gzip --archive=/tmp/fw.gz --drop`
   (`--drop` is required — MaxPro's local Mongo had its own stale data,
   1,752 steps vs server3's 24,575.)
4. Verify counts match per collection: `servers`, `runners`, `steps`, `tasks`,
   `handler_registrations`, `flows`, `fleet_config`. As-built: 124,521 documents,
   0 failures, every collection equal.

⚠️ **MaxPro's Mongo may not publish its port.** Its container had a malformed
binding (`map[27017/tcp:[{invalid IP 27017}]]`) so nothing listened on 27017 and
`fw` could not reach it. Recreate with an explicit `-p 27017:27017`, reusing the
named volume `facetwork_mongodb_data` so the restored data survives.

### Phase 2 — MinIO onto `afl_data_local`

1. Point MaxPro's MinIO at the local disk — `/Volumes/afl_data_local/minio:/data` — and
   start the (currently stopped) `facetwork-minio` container.
2. Mirror **bucket by bucket, over S3**, from server3's MinIO to MaxPro's:
   ```
   mc alias set src http://<server3-ip>:9000 minioadmin minioadmin
   mc alias set dst http://localhost:9000     minioadmin minioadmin
   mc mb dst/afl-cache dst/osm-extracts
   mc mirror --watch=false src/afl-cache    dst/afl-cache
   mc mirror --watch=false src/osm-extracts dst/osm-extracts
   ```
   `mc mirror` is resumable and idempotent — safe to re-run after an interruption.
3. Verify with `mc ls --recursive --summarize` object counts per bucket, not just sizes.

Over gigabit this is roughly **1.5–2 hours for the 580 GB of MinIO buckets**, plus ~1 hour for the 294 GB of rsync trees (`osm-selfhost` + `output` + `cache`); run it before you leave.

### Phase 3 — cut the cord

⚠️ **The original version of this phase was WRONG and would not have produced a
working standalone machine.** It listed `/etc/hosts` as "the single
highest-leverage change". In reality there are **five** places pointing at
server3, and `/etc/hosts` is the least important of them — containers do not
even read it. All five are corrected below, as-built.

1. **`servers.local.json` — the one that actually matters.** `fw` resolves the
   infra host from the **server catalog**, not from `/etc/hosts` or
   `FW_MONGODB_URL`. Until this is changed the CLI keeps reading server3's
   database (`discovered MongoDB via server catalog (infra):
   mongodb://<server3-ip>:27017`) while the containers read MaxPro's — a
   split-brain that is confusing to debug remotely.

   Create `servers.local.json` (gitignored, merged over `servers.json`; the
   schema is an **array** under `"servers"`, with `name`/`aliases`/`infra`):
   move `afl-mongodb` + `afl-minio` and `"infra": true` onto `MaxPro.local`, and
   leave `afl-postgres` on server3.local (PostGIS is on a *different* machine).

2. **`FW_INFRA_IP` → MaxPro's LAN IP (`<maxpro-lan-ip>`), NOT `127.0.0.1`.**
   Containers get `afl-*` from compose `extra_hosts`, driven by this variable.
   Inside a container `127.0.0.1` is the container itself, so loopback here
   silently breaks every runner. As found, it was pinned at server3
   (`afl-minio:<server3-ip>`).

3. **`FW_MONGODB_URL`** named `server3.local` *directly*
   (`mongodb://server3.local:27017`), so no hosts-file change could redirect it.
   Set it to `mongodb://afl-mongodb:27017` so it follows the catalog.

4. **`FW_DATA_DIR`** pointed at the SMB share and breaks the moment it is
   unmounted — `fleet-agent` refuses to start without it. Set it to a **local**
   path on the attached disk: `/Volumes/afl_data_local/scratch`. The env files
   were not picked up in practice; pass `--data-dir` explicitly to
   `fw fleet agent apply`.

5. **MinIO's bind mount** on MaxPro was `/Volumes/afl_data/minio` — the SMB share
   *from server3*, not the local disk. Recreate the container against
   `/Volumes/afl_data_local/minio`.

6. **`/etc/hosts`** — host-side only (the `fw` CLI, `mc`), *after* the above:
   `127.0.0.1  afl-mongodb afl-minio`. Needs `sudo`; `sudo -n` was not available.
   ⚠️ Do **not** redirect `afl-postgres` — PostGIS is on **<postgis-host-ip>**, an
   unrelated machine, and pointing it at loopback breaks the PostGIS import path.
   ⚠️ Write the edit in Python, not `sed`: a `sed` using `\+` (a GNU extension
   BSD `sed` does not support) matched nothing and **exited 0**, so the change
   silently did not apply.

7. **Unmount the SMB shares** — `umount /Volumes/afl_data /Volumes/bigdata` — and
   remove any auto-mount/login item. Stop MinIO first if it still binds them.

8. **`FW_RUNNER_HOSTS=`** (empty) so `fw fleet rollout --stagger` stops trying to
   SSH to server1/2/3.

9. **Recreate the runners** so they pick up the new `extra_hosts`:
   `FW_DATA_DIR=... fw fleet agent apply --data-dir /Volumes/afl_data_local/scratch`.
   Preflight should print all three ✓ against MaxPro. As-built result: 15/15
   runners live, containers resolving `afl-mongodb`/`afl-minio` to
   <maxpro-lan-ip>, and server1/2/3 correctly showing as stale records.

**Images**: MaxPro already has `facetwork-runner:4c37bc7-d3456ea5` cached, so
*running* existing workflows is fully server3-independent. But *rebuilds* were
not — see Phase 3a.

### Phase 3a — local image registry (rebuild independence) — DONE 2026-07-30

Running cached workflows never touches the registry, but `fw fleet rollout`
(build → push → point fleet_config → converge) did: `FW_FLEET_REGISTRY` defaulted
to `server3.local:5050` for both push and pull. With server3 off, a rebuild had
nowhere to push and the runners had nowhere to pull from. Fixed by running a local
`registry:2` on MaxPro and pointing the whole rollout loop at it.

The one subtlety: **two different clients reach the registry, and they need a name
that resolves the same from both.** The off-host buildkit builder (a container)
pushes; the host Docker daemon pulls. `host.docker.internal:5050` is the name that
works from both (the builder reaches the host gateway; the daemon resolves it in
the Docker Desktop VM), which is why it's used for push, pull, and the
`fleet_config` image ref alike — self-consistent on a single machine.

```bash
# 1. Local registry, data on the attached disk, survives reboots
docker run -d --name facetwork-registry --restart always \
  -p 5050:5000 -v /Volumes/afl_data_local/registry:/var/lib/registry registry:2

# 2. Teach the fleetbuilder to trust it (recreate with a buildkitd.toml that lists
#    host.docker.internal:5050 + localhost:5050 as http/insecure). Config lives at
#    /Volumes/afl_data_local/registry-buildkitd.toml.
docker buildx rm fleetbuilder
docker buildx create --name fleetbuilder --driver docker-container \
  --config /Volumes/afl_data_local/registry-buildkitd.toml --bootstrap

# 3. Teach the host DAEMON to trust it, then restart Docker (bounces the fleet;
#    unless-stopped containers + the fleet-agent launchd job bring it all back)
fw fleet registry-setup --registry host.docker.internal:5050
docker desktop restart

# 4. Point rollout at it durably (gitignored .env, host-local — the CLI reads .env)
echo 'FW_FLEET_REGISTRY=host.docker.internal:5050' >> .env   # also in .env.fleet, documented

# 5. Seed the current image so cache-from hits, then do one real rollout
docker tag  server3.local:5050/facetwork-runner:4c37bc7-d3456ea5 \
            host.docker.internal:5050/facetwork-runner:4c37bc7-d3456ea5
docker push host.docker.internal:5050/facetwork-runner:4c37bc7-d3456ea5
fw fleet rollout          # NOT --stagger — it aborts with no remote hosts
```

⚠️ **Gotchas hit doing this:**
- `fw fleet rollout` sources only `_bootstrap.sh` + `_remote.sh`, **not** `_env.sh`,
  so a `FW_FLEET_REGISTRY` set only in `.env` was silently ignored and it defaulted
  back to `server3.local:5050`. Fixed by teaching `_afl_resolve_remote_env` to read
  `FW_FLEET_REGISTRY` from `.env` the same way it already reads `FW_RUNNER_HOSTS`.
  Confirm with `fw fleet rollout --dry` — `image:`/`registry:` must both say
  `host.docker.internal:5050`, not just `cache-from`.
- A push **~12 s after a Docker restart** failed with `proxyconnect … 3128: i/o
  timeout` — Docker Desktop's built-in transparent proxy wasn't ready yet. It is
  NOT a name/insecure-registry problem; just retry once the daemon has settled.
- Recreating the fleetbuilder wipes its build cache, so the first rebuild is
  slower until cache-from (the seeded image) and the layer cache repopulate.

✅ **Verified 2026-07-30**: a full `fw fleet rollout` built HEAD (`582efbe`),
re-cloned + re-baked every `fwh_*` domain **from GitHub** (a rebuild needs source
somewhere — but that is GitHub, not server3), pushed to the local registry, and
MaxPro's **15/15 runners pulled `host.docker.internal:5050/facetwork-runner:582efbe`
and went `[up-to-date]`**. server3 is out of the registry path entirely.

⚠️ **The rollout still exits non-zero and prints "NOT converged (1/4 host(s))".**
That is **cosmetic**: `fleet_config` still lists the three offline hosts
(server1/2/3) as expected members, so the converge check counts 4 hosts while only
MaxPro is live. MaxPro itself converges — check the per-host line
(`MaxPro 15/15 [up-to-date]`), not the exit code. The clean fix is to trim
`fleet_config` to the local host, which belongs to the planned `fw mode local`
toggle (local profile = just this machine), not a hand-edit here.

⚠️ **`gh-router` residual**: its `fleet_config` image is still
`server3.local:5050/osm-gh-router:f2ee20c`, but it runs with `replicas=-` (not
scheduled on MaxPro), so it is inert — like the `afl-postgres` line in §3.6. It
would only bite if OSM routing (embedded GraphHopper) were scheduled here; rebuild
its image to the local registry first if so.

### Phase 4 — OSM data

Two separate trees, moved two different ways.

**a) MinIO `osm-extracts` (217 GB)** — includes the per-county `north-america` tree
(49 GB). Comes across in Phase 2 via `mc mirror`, because these are MinIO objects.

**b) `osm-selfhost/` (182 GB) — plain files, so `rsync` is correct here** (unlike the
MinIO buckets). This is the planet plus the continent extracts split from it:

```
for tree in osm-selfhost output cache; do
  rsync -aP --info=progress2 \
    /Volumes/afl_data/$tree/ \
    /Volumes/afl_data_local/$tree/
done
```

`output/` and `cache/` carry the plain-filesystem side of the domain data —
`cache/noaa-weather`, `output/census-output`, `output/maps`, `output/osm`. The
MinIO `afl-cache` bucket carries the rest. Copy both; nothing is re-fetched.

`rsync` is resumable — re-run after any interruption. Verify the planet afterwards against
its checksum: `osm-selfhost/planet-latest.osm.pbf.md5` is already there, and
`planet.md5.actual` records the last verification.

**Do not re-download the planet.** It is 87 GB already on disk, and Geofabrik has IP-banned
the fleet anyway (`reference_osm_france_provider`) — the self-host tree exists precisely so
that ban does not matter.

Once copied, point the extract path at the local tree (`FW_GEOFABRIK_BASE_URL` /
`FW_OSM_EXTRACT_PROVIDER`) so nothing reaches for the network. If you want the `www/` tree
served over HTTP on MaxPro the way it was on server3, `server.nohup.log` in that directory
shows how it was run.

Budget: 762 GB total ≈ **21% of the 3.6 TB disk.** No download needed.

---

## 4. Pre-departure verification checklist

✅ **All items verified 2026-07-31 with server3 (and server1/2) PHYSICALLY POWERED OFF** — the fully honest test:

- [x] `fw fleet status` — MaxPro 15/15, Mongo via `afl-mongodb`; completed in ~6.7 s, **did not hang** reaching for server3
- [x] Dashboard loads at `http://localhost:8080` (HTTP 302 → v3)
- [x] `fw ffl run` a live-domain workflow → completes — `save_earth.workflows.BuildSeismicMap` reached `completed` (county-atlas domain isn't on the fleet; used save-earth instead)
- [x] A workflow writes to local MinIO — the seismic run fetched USGS quakes + Bird-2002 faults over the network and wrote `cache/save-earth/{earthquakes,faults}/*.geojson` + `cache/save-earth/maps/seismic/index.html` (608 KiB) to `afl-cache`
- [x] `fw fleet rollout --dry` resolves to the **local** registry (`host.docker.internal:5050`), not `server3.local:5050`
- [x] One **real** rebuild + rollout end to end — built `582efbe`, pushed to local registry, MaxPro 15/15 `[up-to-date]` (Phase 3a)
- [x] `mongodump` on MaxPro succeeds — 58 MB local backup written

⚠️ **Gap the honest test caught — the `afl-cache` bucket did not exist.** Phase 2
mirrored `osm-extracts` but deliberately skipped the 363 GB `afl-cache` cache — and
never created the *empty* bucket. Local MinIO had only `osm-extracts`, so the first
output-writing workflow would have failed `NoSuchBucket` on `s3://afl-cache`. Fixed
by creating it: `mc mb --ignore-existing loc/afl-cache`. It repopulates on demand
(the seismic run's 6 objects were the first). **When standing up any local-standalone
box, create every bucket the fleet writes to, even the ones you skip mirroring.**

---

## 5. Risks and things that will bite

| Risk | Why | Mitigation |
|---|---|---|
| **`mc mirror` interrupted** | 580 GB over the network |
| **Re-downloading the planet** | Easy to assume it is missing — it is not in MinIO | It is at `osm-selfhost/planet-latest.osm.pbf` (87 GB); rsync it | Resumable — just re-run; verify with object counts |
| **Copying MinIO dirs with `cp`** | Objects are erasure-coded dirs | Always `mc mirror`; never filesystem copy between instances |
| **SMB mounts silently reappear** | macOS remembers shares | Remove login items; verify after a reboot |
| **Registry unreachable** | Roles reference `server3.local:5050` | Local registry, tested *before* departure |
| **PostGIS** is on .76, not server3 | Survives server3 going down, but not a full-offline setup | Only matters for PostGIS import; run a local PostGIS if needed |
| **`foreach … limit` can stall a fan-out** | Stranded sub-blocks hold window slots (§6) | Fix before departure, or don't use `limit` on long runs |
| **Internal disk 87% full** | 237 GB free | Keep all data on `afl_data_local`; watch Docker VM growth |
| **Commands that exit 0 having done nothing** | `mongodump` keeping only the last `--db`; BSD `sed` ignoring `\+`; a trailing `echo` masking a failed mirror | Verify the *effect* (counts, file contents), never the exit code |
| **`afl-postgres` redirected to loopback** | PostGIS is on <postgis-host-ip>, unrelated to server3 | Leave that hosts line alone; restore `<postgis-host-ip>` if the import path is needed |

---

## 6. Must-fix before vacation

**`foreach … limit N` can wedge a fan-out.** Observed live on the 3,167-county run: 31 of
32 window slots were held by sub-blocks that had finished their work but never cascaded to
`Complete`, so no new counties were admitted and the run stalled at 628/3,167.

This is the pre-existing stranded-block defect (same as the 49-hour stall), but the cap
converts it from a slow tail into a **hard stop**. Repair check 7 does not catch it — it
requires *every* task terminal, and here one was still running.

Current mitigation is a watchdog script that resumes stranded sub-blocks every 45 s. That
must not be the state of things while you are away. The fix belongs in
`_refill_foreach_window`: a sub-block that is non-terminal, has no live task, and has not
progressed within a grace period should be resumed inline rather than holding a slot
forever — which also helps uncapped runs.

**Recommendation:** land that fix, rebake, and re-run the fan-out clean *before* the
migration, so MaxPro starts from a known-good image.

---

## 7. Post-vacation experiment: OrbStack (or colima) vs Docker Desktop

**Not a recommendation yet — a benchmark to run.** Do this *after* the migration
settles, on MaxPro alone, never mid-trip.

### Why it is worth measuring

Docker Desktop on macOS runs a Linux VM and bridges host directories through
**virtiofs**, so every bind-mounted file operation crosses that boundary. This
fleet is almost entirely bind-mount-driven — MinIO's backend, the county PBFs,
the baked domain trees — and the migration surfaced the cost directly:

* the temp MinIO's `df /data` reported **1.9 T / 902 G used**, which is the
  host's `/Volumes` mount point, not the 3.6 T USB volume — misleading enough
  that the bind had to be verified by comparing file contents;
* `iostat` showed **~17,500 tps of 4 KB I/O** during the copies, the exact
  small-file pattern virtiofs handles worst — and MinIO stores every object as
  a directory of small files;
* a filesystem copy of the MinIO backend managed **~1 file/sec** (60 files in
  61 s), while `mc` over HTTP against the same data ran at 39–58 MiB/s.

With 15–22 containers per host all reading through bind mounts, a faster
file-sharing path compounds.

### What is NOT established

OrbStack's reputation is "several times faster on bind-mount-heavy small-file
workloads", **but that figure has not been measured on this hardware.** Treat it
as a hypothesis. (This document already carries one cautionary example: a local
disk-to-disk copy was predicted at ~200 MB/s and actually ran at 38.)

### Benchmark procedure

Run each under Docker Desktop, then under OrbStack, on the same host and data:

1. **Small-file bind-mount read** — `time` a recursive checksum of a fixed
   MinIO prefix (e.g. `osm-extracts/county-atlas`, ~6.8 GB across thousands of
   object dirs) from inside a container with the backend bind-mounted.
2. **Large-file bind-mount write** — `time` copying one continent PBF
   (`europe-latest.osm.pbf`, 37 GB) into a bind-mounted volume.
3. **Container start-up** — `time` `fw fleet rollout` reconcile on one host, or
   simply `docker compose up` for the 15 runner services.
4. **Idle overhead** — CPU and RSS of the VM with the fleet running but idle.
5. **Correctness gate** — `fw ffl run` a one-county county-atlas build end to
   end, and confirm `fw fleet status` reports the expected runner count.

Record the numbers in this section. Switch only if (1) or (3) improves
materially **and** (5) passes.

### Caveats before switching

* It is a **different runtime**, not a Docker Desktop setting. Test on one host
  first; never roll it across the fleet in one step.
* `colima` is the free/open alternative; OrbStack is free for personal use and
  paid commercially.
* The registry, buildx builder, and `extra_hosts` mappings in
  `docker-compose.fleet.yml` all need re-verification under a new runtime —
  they are the parts most likely to differ quietly.


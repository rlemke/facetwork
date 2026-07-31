# MaxPro standalone — running the whole stack on one machine

**Goal:** MaxPro runs Facetwork end to end with **zero dependency on server3** — its own
MongoDB, its own MinIO, and OSM data on the locally attached `afl_data_local`. server3 can
then be powered off, rebooted, or left alone without affecting development.

**Status:** plan only. Nothing here has been executed.

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

1. **`/etc/hosts`** — `192.168.68.114  afl-mongodb afl-minio server3`
   Every Mongo and S3 call resolves to server3.
2. **SMB mounts** — `/Volumes/afl_data` (15 TB, 11 TB used) and `/Volumes/bigdata`
   (3.6 TB) are network mounts **served by server3**. These vanish when server3 does.
3. **Fleet config** — `mongodb://afl-mongodb:27017`, `http://afl-minio:9000`,
   dashboard `http://afl-mongodb:8080`.
4. **Image registry** — roles pull `server3.local:5050/facetwork-runner:…`.
5. `.env` / `.env.fleet` — `FW_MONGODB_URL`, `FW_S3_ENDPOINT`, `FW_INFRA_HOST=server3.local`.

Note `afl-postgres` is **192.168.68.76 — a different machine**, not server3. PostGIS is
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

1. On server3: `mongodump --gzip --archive=/tmp/fw.gz` for `facetwork` + `facetwork_examples`.
2. `scp` to MaxPro.
3. On MaxPro, restore into the **already-running** `facetwork-mongodb`:
   `mongorestore --gzip --archive=/tmp/fw.gz --drop`
4. Verify counts match: `servers`, `runners`, `steps`, `tasks`, `handler_registrations`,
   `flows`, `fleet_config`.

### Phase 2 — MinIO onto `afl_data_local`

1. Point MaxPro's MinIO at the local disk — `/Volumes/afl_data_local/minio:/data` — and
   start the (currently stopped) `facetwork-minio` container.
2. Mirror **bucket by bucket, over S3**, from server3's MinIO to MaxPro's:
   ```
   mc alias set src http://192.168.68.114:9000 minioadmin minioadmin
   mc alias set dst http://localhost:9000     minioadmin minioadmin
   mc mb dst/afl-cache dst/osm-extracts
   mc mirror --watch=false src/afl-cache    dst/afl-cache
   mc mirror --watch=false src/osm-extracts dst/osm-extracts
   ```
   `mc mirror` is resumable and idempotent — safe to re-run after an interruption.
3. Verify with `mc ls --recursive --summarize` object counts per bucket, not just sizes.

Over gigabit this is roughly **1.5–2 hours for the 580 GB of MinIO buckets**, plus ~1 hour for the 294 GB of rsync trees (`osm-selfhost` + `output` + `cache`); run it before you leave.

### Phase 3 — cut the cord

1. **`/etc/hosts` on MaxPro** — the single highest-leverage change:
   ```
   127.0.0.1   afl-mongodb afl-minio
   ```
   (remove the `192.168.68.114` line). Everything that names `afl-*` now stays local.
2. **Unmount the SMB shares** so nothing silently reads server3:
   `umount /Volumes/afl_data /Volumes/bigdata` — and remove any auto-mount/login item.
3. **Fleet config** → point at MaxPro and drop the remote roles:
   `fw fleet set --mongo mongodb://localhost:27017 --minio http://localhost:9000`
4. **Fleet hosts** — set `FW_RUNNER_HOSTS` to MaxPro only, so `fw fleet rollout --stagger`
   does not try to SSH to server1/2/3.
5. **Images** — MaxPro already has `6168c9f-dd8aff42f4015` cached. For *new* builds while
   away, either run a local registry on MaxPro (`fw fleet registry-setup`) or build and
   `docker tag` locally. **Test one rebuild before you leave** (§4).

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

Run each of these **with server3 powered off** — that is the only honest test:

- [ ] `fw fleet status` — MaxPro reports its runners, no attempt to reach server3
- [ ] Dashboard loads at `http://localhost:8080`
- [ ] `fw ffl run` a one-county county-atlas build → completes
- [ ] A map-publishing workflow writes to local MinIO (`mc ls dst/afl-cache/...`)
- [ ] `fw fleet rollout --dry` resolves to a MaxPro-local registry, not `server3.local:5050`
- [ ] One **real** rebuild + rollout end to end (this is the step most likely to surprise)
- [ ] `mongodump` on MaxPro succeeds — you have a local backup path while away

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


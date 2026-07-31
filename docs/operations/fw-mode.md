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
such as MaxPro ([maxpro-standalone.md](maxpro-standalone.md)).

Each switch is driven by a gitignored, host-local profile — `mode.local.json` and
`mode.cluster.json` — holding the handful of values that differ:

| key | `local` (this machine) | `cluster` (shared host) |
|---|---|---|
| `infra_host` / `infra_ip` | this machine | the infra host (IP **re-resolved live**) |
| `fleet_registry` | `host.docker.internal:5050` (local `registry:2`) | `server3.local:5050` |
| `mongodb_url` / `s3_endpoint` | `afl-mongodb` / `afl-minio` → localhost | → the infra host |
| `data_dir` / `data_root` | local scratch + `s3://afl-cache` | same names, remote |
| `server_catalog` | `local` (writes `servers.local.json`) | `none` (committed defaults govern) |
| `require_reachable` | `false` | `true` |

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
6. `fw fleet agent apply` to recreate the runners,
7. stamps `.fw-mode`.

### ⚠️ The honest boundary — state does not merge

Config flips cleanly; **runs and data do not.** `local` and `cluster` are separate
Mongo databases and separate object stores. In `local` you see your local runs; in
`cluster` you see the fleet's. Switching does **not** sync workflow state between
them — full bidirectional sync (with conflict resolution on in-flight step state)
is a hard problem and deliberately out of scope. On a laptop, run the local MinIO
as a warm read-through cache that fills on demand rather than mirroring the whole
fleet; the first offline run of each domain is just slower.

### Standing up a machine for Model B

See [maxpro-standalone.md](maxpro-standalone.md) for the full one-time setup (local
Mongo restore, MinIO, a local `registry:2` for rebuild independence, and data). Note
in particular: **create every bucket the fleet writes to** (e.g. `afl-cache`), even
the ones you skip mirroring, or the first output write fails `NoSuchBucket`.

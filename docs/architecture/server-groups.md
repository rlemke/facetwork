# Capability-tiered server groups (heterogeneous fleet)

**Status:** **Implemented.** Per-role `server_groups` gating lands in the central
fleet config (`fw fleet set --role-groups ROLE:g1,g2` / `--server-groups`) and the
per-host reconcile (`fleet-agent`). It is a **no-op by default** — a fleet that
sets no `server_groups` anywhere runs every role on every host exactly as before,
so existing deployments are unaffected until they opt in.

**Related:** [task_list_routing.py](../../facetwork/runtime/task_list_routing.py)
(namespace-derived routing — the correctness guarantee this builds on),
[ffl-runner-orchestration-tier.md](ffl-runner-orchestration-tier.md) (the prior
role-split this generalizes), [informal-fleet.md](../operations/informal-fleet.md),
[deployment.md](../operations/deployment.md) (central fleet config / adding a server).

## 1. Problem

The fleet is **homogeneous**: every runner server brings up the same set of
roles, so every host bears the resource profile of the *heaviest* domain it runs.
In practice that means a laptop or a modest desktop in the fleet is asked to run
the same osm-geocoder tier as a well-provisioned box — whole-region PBF imports,
multi-GB `osmium` node-location indexes, and wide `andThen foreach` fan-outs that
stage large artifacts to local disk before finalizing to the object store.

This is exactly what produced a real incident: concurrent 51-state fan-outs
filled an under-provisioned host's Docker-VM disk, which crashed its MongoDB and
took out the box. The fix at the time was operational (prune, serialize, the
disk-guard). But the structural question the incident raised is: **can a heavy
domain be kept off the machines that can't carry it**, so the same workload runs
only where there is disk and memory headroom — while lighter domains (NOAA,
census, H-1B, conflict) still spread across the whole fleet?

The routing layer already supports the *correctness* half of this for free (§2);
what was missing was a way to express, in the **central** config, that a given
role should only be *started* on a subset of hosts.

## 2. What already worked: routing is capability-scoped

A runner only ever **claims tasks for facets it has a handler for**. Task lists
are derived from the facet's top-level namespace (`task_list_routing.py`): a task
for `osm.cache.Download` is tagged list `osm`, and a runner polls only the
namespaces of the handlers it actually loaded, plus the protocol lists. The claim
itself (`claim_task`, a single atomic `find_one_and_update`) filters on both the
task name and the task list, server-side. Two consequences:

- A host running only the NOAA runner **cannot** pick up an osm PBF task — it
  doesn't poll the `osm` list and doesn't have the handler. Disjoint filters,
  disjoint pools; no contention.
- Therefore heterogeneity is already *safe*: putting different domains on
  different hosts never mis-routes work. A task always reaches a host that can
  serve it, or waits (pending) until one is up.

So "server groups handling a subset of handlers" was achievable **today,
decentralized**, just by choosing which `fw runner start --domain X` you run on
each host. The gap was only that the **central** fleet config (`fleet set` →
`fleet agent`) applied one uniform role set to every server.

## 3. Proposal: `server_groups` on a role + an agent gate

Add an optional `server_groups: [name, …]` list to any role in the central
`fleet_config.roles`. A host carries a group label (`FW_SERVER_GROUP`, default
`runner` — already recorded on the heartbeat for dashboard/`fleet status`). The
per-host `fleet-agent` brings a role up **only when** the host's group is in the
role's `server_groups`; a role with no list runs everywhere.

```
role runs on this host  ⇔  role.server_groups is empty  ∨  host_group ∈ role.server_groups
```

This is *role* targeting, not a new scheduler and not a change to claiming. It
decides which runners a host bothers to **start**; routing (§2) still guarantees
correctness regardless. A skipped role is simply not started here — another host
in its group serves those tasks, and if none is up the tasks wait pending (the
same liveness model the informal fleet already relies on).

### 3.1 Gated roles

The gate applies uniformly to every role the agent brings up: the **osm tier**
(osm-geocoder + osm-lz, the `fleet_default` base), **gh-router**, **ffl-runner**,
and every per-domain `kind="domain"` runner. The osm tier is the heaviest, so it
is gated too: when the host's group isn't in `osm-geocoder.server_groups` the
agent skips the base bring-up entirely (it does not *stop* already-running
containers — a skip is "don't start", consistent with the rest of reconcile).

### 3.2 Config surface

```bash
# Tag hosts by editing each host's FW_SERVER_GROUP (default "runner"):
#   heavy box  → FW_SERVER_GROUP=heavy
#   laptop     → FW_SERVER_GROUP=light

# Pin the heavy domains to the 'heavy' group:
fw fleet set --role-groups osm-geocoder:heavy --role-groups osm-mapping:heavy

# Add a light domain to the 'light' group in one call (convenience form):
fw fleet set --domain-runner census-us --server-groups light,heavy

# General form for any role; empty clears (runs everywhere again):
fw fleet set --role-groups ffl-runner:heavy
fw fleet set --role-groups osm-geocoder:        # clear → osm runs everywhere
```

`fw fleet get` / `fw fleet status` show the `groups=` per role; `fleet agent
apply --dry-run` prints exactly which roles a host would start and which it would
skip ("not in group"), so the assignment is auditable before it takes effect.

### 3.3 Backward compatibility

The empty-means-everywhere default is the whole compatibility story: every
existing config has no `server_groups`, so `role_in_group()` returns `True` for
every role on every host, and behavior is byte-for-byte the old behavior. The
feature is purely additive and opt-in.

## 4. Trade-offs and non-goals

- **Not a bin-packer.** `server_groups` is static operator intent ("these
  domains belong on these tiers"), not a dynamic scheduler that places work by
  live load. That is a deliberate non-goal — it keeps the fleet leaderless and
  the model legible. Dynamic, load-aware placement would be a much larger change
  and is not proposed here.
- **Liveness is the operator's responsibility.** If a role is pinned to a group
  with no live host, its tasks sit pending. `fleet status` shows hosts by group;
  the dashboard shows pending depth. This is the same "runner machines are
  disposable, infra must be stable" contract the informal-fleet model already
  states — groups just narrow *which* hosts can serve a given role.
- **`server_groups` is a start-time gate, not a claim filter.** It is
  intentionally *not* wired into `claim_task`. Routing already enforces
  correctness; duplicating the restriction in the claim query would add a second
  source of truth that could disagree with which handlers a runner actually
  loaded. The label stays advisory at the data layer and load-bearing only in the
  agent's bring-up decision.

## 5. Relation to the `ffl-runner` tier

This generalizes the precedent set by the [`ffl-runner`
tier](ffl-runner-orchestration-tier.md). That work already added a *separate
role* to the central config, brought up conditionally per host, with its own
group label and behavior gating (`continuation_mode`). Server groups take the
last step: instead of "this role exists and every host runs it if replicas>0,"
any role can declare *which* hosts run it. The ffl-runner tier and a heavy-domain
tier are now expressible as ordinary group assignments
(`--role-groups ffl-runner:orchestrators`, `--role-groups osm-geocoder:heavy`).

## 6. Assigning handlers to servers by capability (capacity-aware placement)

§3 gives the *mechanism* (a role → group gate). This section is the *policy*: how
to decide **which handler roles a given server should run**, so a role's resource
demand never exceeds the host's CPU, memory, disk, and special abilities. The
assignment is operator intent expressed once in the central config; it is not a
dynamic scheduler (§4). Do it in three steps.

### 6.1 Profile each server (its capacity envelope)

Record, per host, the axes a handler can exhaust:

| Axis | How to read it | Why a handler cares |
|------|----------------|---------------------|
| **CPU cores** | `sysctl -n hw.ncpu` / `nproc` | osmium PBF parse, tippecanoe tiling, scipy/lifelines are CPU-bound; a wide `foreach` fans `min(16, cores-2)` concurrent tasks. |
| **RAM** | `sysctl -n hw.memsize` / `free -g` | osmium node-location index and the GraphHopper in-memory graph are multi-GB; pandas frames scale with input. |
| **Local scratch disk** | free space on `FW_DATA_DIR`/`FW_LOCAL_SCRATCH` | osm stages multi-GB PBF/continent extracts to scratch **before** finalizing to MinIO. Under-provisioned scratch is what crashed a host's Mongo (§1). Keep scratch on a LARGE disk, never the small internal volume. |
| **Architecture / emulation** | `uname -m` vs the image arch | The fleet image is **arm64**; an **x86 host runs it under qemu** (~5–10× slower). Emulation multiplies every heavy CPU/RAM cost — reason enough to keep heavy roles off emulated hosts. This is an *ability*, not just speed. |
| **Installed binaries** | in-image vs host | osm needs `osmium`/`tippecanoe`/`pmtiles`/a JRE+GraphHopper and a reachable **PostGIS**; sentinel2 needs GDAL/rasterio. All are baked into the image, but PostGIS is an external dependency the host must reach. |
| **Credentials** | per-host `~/.facetwork/fleet-secrets.env` | census needs `CENSUS_API_KEY`, anthropic needs `ANTHROPIC_API_KEY`. A host without the key can't serve that domain even if it has the CPU — a credential is a capability. |

### 6.2 Handler resource matrix (demand per role)

Each domain's demand, drawn from its `domains.json` entry (`compose` env/volumes/
notes) and handler behavior. **Tier** is the recommended placement class.

| Role (task_list) | CPU | RAM | Scratch disk | Special abilities needed | Tier |
|------------------|-----|-----|--------------|--------------------------|------|
| **osm-geocoder** (`osm`) | high (osmium parse, tippecanoe) | **high** (multi-GB node index) | **very high** (multi-GB PBF staging; 4h timeouts) | osmium, tippecanoe, pmtiles, JRE+GraphHopper, **PostGIS**, LARGE scratch | **heavy** |
| **osm-lz** (`osm`) | — (pure-FFL over osm facets) | — | — | rides osm-geocoder | **heavy** |
| **gh-router** (`osm` RouteBatch) | high (JVM routing) | **high** (in-memory graph) | low | JRE + GraphHopper jar | **heavy** |
| **osm-mapping** (`osm_mapping`) | medium (shapely joins) | medium | medium | Overpass egress (rate-limited → do NOT fan out) | medium |
| **sentinel2-landchange** (`s2`) | medium-high (raster) | high (COG/rasterio) | medium | GDAL/rasterio (`[geo]` extra) | medium |
| **cancer** (`cancer`) | high (scipy/lifelines) | high (pandas/parquet) | low | MinIO parquet; open genomics APIs | medium |
| **census-us** (`census`) | medium (TIGER geometry) | medium | low | **CENSUS_API_KEY** | medium |
| **genomics** (`genomics`) | medium (foreach) | medium | medium | — | medium |
| **h1b** (`h1b`) / **health** (`health`) | low-medium (CSV+join) | medium (pandas) | low | open gov APIs | light |
| **noaa-weather, conflict, migration, jenkins, sensor-monitoring** | low | low | low | open APIs / zips | light |
| **anthropic** (`anthropic`) | low | low | low | **ANTHROPIC_API_KEY** | light |

### 6.3 Placement procedure

1. **Tier the servers** by 6.1 into groups (`FW_SERVER_GROUP`): e.g. `heavy` for a
   big, native-arch box with large scratch + PostGIS reach; `runner` (default) for
   everything else; add more tiers (`light`, `orchestrators`) as the fleet grows.
2. **Gate each heavy/medium role** to the group(s) that satisfy its 6.2 demand,
   via `fw fleet set --role-groups ROLE:group[,group]`. Light roles get **no**
   group — they run everywhere (§3's empty-means-everywhere).
3. **Ensure every gated role has a live host in its group** (§4 liveness) and that
   host carries the role's credentials (6.1). Otherwise its tasks sit pending.

The invariant to preserve: **for every role, `role.server_groups` ⊆ {groups whose
hosts meet 6.2's demand}**. Routing (§2) still guarantees correctness if you get
it wrong — a mis-placed heavy task just won't be claimed by a host that can't
serve it — but honoring the invariant is what keeps heavy work off the boxes that
would thrash or crash on it.

### 6.4 Current fleet assignment (worked example, 2026-07-10)

The live fleet has one native-arm64 heavy box and two emulated x86 minis:

| Host | Group | Why | Runs |
|------|-------|-----|------|
| **MaxPro** | `heavy` | native arm64 (no emulation), large scratch, reaches PostGIS | osm-geocoder + osm-lz + gh-router (all `heavy`-gated) **plus** all light/medium domains |
| **server1, server2** | `runner` | x86 running the arm64 image under **qemu** (heavy work is doubly slow here); modest scratch | the light/medium data domains only — **no osm** |
| **server3** | — | infra host (Mongo/MinIO/registry/dashboard) | no runners |

Set with:
```bash
fw fleet set --role-groups osm-geocoder:heavy   # implies osm-lz (rides osm)
fw fleet set --role-groups gh-router:heavy       # GraphHopper routing follows osm
# light/medium domains: no --role-groups → run on every host
```

Verified end-to-end 2026-07-10: an `osm.heatmap.ContinentHeatmap` fan-out over 3
leaves ran **all 13 osm tasks on MaxPro** and **zero on the emulated minis** — the
gate kept the heavy PBF/tiling work on the only box provisioned for it, while the
minis kept serving their light domains. Emulation lesson worth carrying: baking
every domain into the image (so containers skip the per-start `pip install`)
removed the dominant cold-start cost, but qemu still penalizes heavy library
imports on the x86 minis — an independent capability reason to keep the heavy
tier off them.

### 6.5 Auditing the assignment

- `fw fleet get` / `fw fleet status` — the `groups=` shown per role is the live
  assignment; `status` lists hosts by group so you can check each gated role has a
  member.
- `fw fleet agent apply --dry-run` on a host — prints exactly which roles it would
  **start** and which it would **skip ("not in group")**, so placement is
  auditable before it takes effect.
- Confirm at runtime by which host actually ran a role's tasks (the osm-on-MaxPro
  check above): a mis-tier shows up as tasks pending (no capable host) or as heavy
  work landing on a host that thrashes.

## 7. Thesis note

The thesis (§14.3, §15.3) describes a homogeneous fleet ("every Facetwork server
is a homogeneous, stateless runner"). Server groups sharpen that into an honest,
specific extension: because routing is already capability-scoped, a
**heterogeneous, capability-tiered** fleet needs *no change to the runtime,
claim protocol, or recovery machinery* — only the central config gains a per-role
group binding, and the per-host agent honors it. It is the cheapest possible
answer to "what would have contained the disk incident": keep the heavy domain
off the machines that can't carry it, in one central setting, with routing still
guaranteeing every task reaches a host that can serve it.

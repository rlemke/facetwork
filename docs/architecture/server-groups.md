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

## 6. Thesis note

The thesis (§14.3, §15.3) describes a homogeneous fleet ("every Facetwork server
is a homogeneous, stateless runner"). Server groups sharpen that into an honest,
specific extension: because routing is already capability-scoped, a
**heterogeneous, capability-tiered** fleet needs *no change to the runtime,
claim protocol, or recovery machinery* — only the central config gains a per-role
group binding, and the per-host agent honors it. It is the cheapest possible
answer to "what would have contained the disk incident": keep the heavy domain
off the machines that can't carry it, in one central setting, with routing still
guaranteeing every task reaches a host that can serve it.

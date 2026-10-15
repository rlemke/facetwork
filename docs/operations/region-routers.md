# Per-region GraphHopper routers

graphhopper-web serves one graph per process. The fleet used to run ONE router
region everywhere (`fw fleet set --graphhopper-region R`), so a low-zoom build of
another region first repointed every host's router and waited minutes for it,
and two regions could never be built at the same time.

`fw fleet router` runs one router **per region**, each on whichever host claims
it, and handlers find the right one by the region in their graph path.

```bash
fw fleet router add north-america/us-major-roads --heap 6g --groups heavy
fw fleet router add europe/germany --replicas 2
fw fleet router wait north-america/us-major-roads   # exit 0 once a replica is ready
fw fleet router list
fw fleet router remove europe/germany
```

The graph must already be built in the object-store cache
(`cache/osm/graphhopper/<region>-latest/<profile>/`); a router without one exits
at start and its slot reads `failed`.

## How placement works

Two collections, no leader:

| Collection | Written by | Holds |
|---|---|---|
| `fleet_routers` | `fw fleet router add/remove` | desired: region, profile, replicas, heap, server groups |
| `fleet_router_slots` | fleet-agents | one doc per replica: host, port, url, state, lease |

Every fleet-agent, every cycle (`_routers.py`):

1. renews the slots it holds and checks its router answers `/health` + `/info`
   (`starting` → `ready`; a crash-looping container reads `failed`);
2. releases a slot whose region was removed or scaled down, stopping its container;
3. stops any `fw-gh-*` container it holds no slot for (two hosts must never serve one slot);
4. claims **at most one** free slot -- unheld, or whose lease (5 min) lapsed.

One claim per cycle is what spreads regions across hosts instead of letting the
first agent awake take them all. A host never holds two replicas of one region.
Limits: `--groups` (default `roles.graphhopper.server_groups`) and
`FW_GH_MAX_ROUTERS` / `roles.graphhopper.max_per_host` per host (default 2).

Each router is its own compose project (`fw-gh-<region>-<profile>`) on the plain
bridge network (`docker/compose.router-region.yml`), published on a port from
18990 up that the agent first checks is free on the host.

## How handlers find a router

`fwh_osm` `zoom_router.router_url(graph_dir, profile)` takes the region from the
graph path, picks a random `ready` replica with a fresh lease, and keeps it only
if **this process** can reach `/health`. With none, it falls back to
`GRAPHHOPPER_API_URL` -- the fleet-wide router -- so a deployment that adds no
region router behaves exactly as before.

⚠️ "Ready" means reachable from the router's OWN host. Measured 2026-10-14: a
router published by Docker Desktop on a Mac answered 200 there and from that
Mac's containers, and reset every connection from the other hosts (that Mac's
fleet-wide router on 8989 did the same). Granting Docker Desktop the macOS Local
Network permission is the fix on such a host; the reachability check is what
keeps a run from failing on one meanwhile.

## Known limits

- A router keeps the image it started with until it is restarted; it is not
  recreated on a runner-image rollout (the GraphHopper jar is pinned and the
  entrypoint is bind-mounted, so this is rarely a difference).
- The `/etc/hosts` drift self-heal does not cover routers. A router reaches
  MinIO only while it downloads its graph at start.

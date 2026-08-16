# Server catalog (`servers.json`)

One entry per machine in the deployment, keyed by a **stable resolvable name**
(mDNS `.local` on a LAN, or a DNS name) — never by IP. Anything that needs a
fleet machine resolves the name to its *current* address at startup or
reconcile time via `facetwork/servers/catalog.py`, so a DHCP-drifted address
heals on the next resolution instead of requiring `sudo /etc/hosts` edits on
every host (the recurring infra-host failure this file removes).

## Entry fields

| Field | Meaning |
|-------|---------|
| `name` | Stable resolvable name (`server3.local`). The authoritative key. |
| `aliases` | Conventional service names that map to this machine (`afl-mongodb`, `afl-minio`, `afl-postgres`). Replaces the `/etc/hosts` role. |
| `purpose` | Human description shown by `fw fleet servers`. |
| `group` | The machine's `FW_SERVER_GROUP` role tag ([server groups](../architecture/server-groups.md)). |
| `infra` | Exactly one entry `true` — the MongoDB/MinIO/dashboard host; tools use it as the default infra target. |
| `ip_pin` | **Last resort** for hosts name-resolution can't reach (cross-subnet, no mDNS). A pinned IP goes stale on lease change — leave `null` when you can. |

## Resolution & override

Same contract as the domain catalog: `FW_SERVERS_FILE=<path>` (full replace) →
`servers.local.json` (gitignored, merged over by `name`; top-level
`"_remove": [names]` drops entries) → committed `servers.json`. Template for
new deployments: [`servers.example.json`](../../servers.example.json).

## Consumers

- **`fw fleet servers`** — table with live-resolved IPs (`--env` prints
  `FW_INFRA_HOST`/`FW_INFRA_IP` exports; `--infra-name`; `--resolve NAME`;
  `--container-ip [NAME]`).
- **`runner/start --fleet`** (also invoked by the fleet-agent every reconcile)
  defaults `FW_INFRA_HOST` from the catalog's infra entry, resolves it on the
  host, and exports `FW_INFRA_IP` for the compose `extra_hosts` mapping —
  containers can't resolve mDNS themselves, so the host materializes the
  current IP for them at every (re)create. This is what makes drift self-heal
  within one reconcile cycle.

## When infra is THIS machine: `host-gateway`, not an address

`catalog.container_ip()` — the single source of the value that lands in
`extra_hosts` — returns Docker's **`host-gateway`** alias whenever the infra
entry names the local machine (standalone / `fw mode local`), and the resolved
address otherwise. An explicit `ip_pin` still wins.

The distinction matters on a machine that reboots onto a new DHCP lease. A
resolved address is only correct *at create time*: the containers keep whatever
they were given, so after the lease changes they point at an address that no
longer exists, and every path that cached it (`.env.fleet`'s `FW_INFRA_IP`,
`fw mode status`) reports a fleet that looks unreachable. `host-gateway` is
maintained by Docker itself — it survives the reboot, the new subnet, and having
no network at all, so a laptop or desk machine that hosts its own infra needs no
re-resolution and no per-reboot edit.

Two rules follow:

- **Host-side probes must not use it.** `host-gateway` means nothing outside a
  container; use `catalog.resolve_ip()` (or `127.0.0.1` — this machine publishes
  the ports). `runner/start` keeps a separate `_infra_probe_ip` for its
  preflight, and `--env` prints the numeric address as `FW_INFRA_HOST_IP`.
- **The drift repair must not "fix" it.** `_fleet_lib.refresh_container_hosts`
  leaves a container whose `afl-*` entry already resolves to the gateway alone;
  rewriting it to the LAN IP would be a downgrade that recurs every poll.
- **Mongo discovery** (`_fleet_lib.resolve_mongo`) tries the catalog's infra
  entry after `--mongo`/`FW_MONGODB_URL` and before mDNS + the `/etc/hosts`
  `afl-mongodb` convention.

`/etc/hosts` entries for `afl-*` names remain honored (they're just the last
candidates) but are no longer required on catalog-aware paths.

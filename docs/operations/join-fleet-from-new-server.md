# Join the fleet from a new server

**Audience: a Claude (or operator) bringing up an ADDITIONAL server that joins an
existing Facetwork fleet.** This server runs *runners only* and connects to the
fleet's shared MongoDB + MinIO (which run on the **infra host**). Nothing here
starts a database or object store locally.

## The fleet's shared infra (fill in / verify before you start)

| What | Value (as of 2026-06-11) | How to re-derive |
|------|--------------------------|------------------|
| Infra host | `server3` @ **`192.168.68.75`** | on the infra host: `ipconfig getifaddr en0` (macOS) / `hostname -I` (Linux) |
| MongoDB | `mongodb://192.168.68.75:27017` | bundled, `--bind_ip_all`, published `0.0.0.0:27017` |
| MinIO | `http://192.168.68.75:9000` | bundled, published `0.0.0.0:9000`; bucket `afl-cache` |
| MinIO creds | `minioadmin` / `minioadmin` | bundled dev default (use the secret store for prod — see below) |

> The IP is the infra host's LAN address; it can change on DHCP. If it has,
> update it (one place: `.env.worker`), or — more robust — add to **every server's**
> `/etc/hosts`: `192.168.68.75  afl-mongodb afl-minio` and use
> `mongodb://afl-mongodb:27017` + `http://afl-minio:9000` (survives IP changes; the
> fleet tooling also auto-discovers these names).

## Prerequisites on the new server
- Docker + Docker Compose, `git`, Python 3.11+.
- Network: the new server must reach the infra host on **27017** and **9000**
  (same LAN; open the firewall if needed).
- A **large local disk** for scratch (multi-GB staging stays local; only outputs
  go to the shared MinIO).

## Steps

```bash
# 1. Clone this repo + the OSM handler package (the runner mounts the handlers).
git clone <facetwork repo url> ~/facetwork && cd ~/facetwork
git clone https://github.com/rlemke/fwh_osm ~/fw_handlers/fwh_osm
#    (the worker runner mounts ${FWH_HANDLERS_ROOT:-$HOME/fw_handlers}/fwh_osm)

# 2. Python venv with the deps the fleet tooling needs.
python3 -m venv .venv
.venv/bin/pip install -e ".[mongodb,s3]" cryptography   # cryptography = secret store; zeroconf optional (mDNS)

# 3. Point at the shared infra — the committed template is pre-filled.
cp .env.worker.fleet .env.worker
#    Edit .env.worker if the infra IP changed, and set AFL_DATA_DIR to a big LOCAL
#    path on THIS server (macOS: /Volumes/afl_data ; Linux: e.g. /var/lib/afl_data).

# 4. Preflight: confirm both shared services are reachable from here.
scripts/start-worker --check          # ✓ MongoDB reachable  ✓ MinIO reachable

# 5. Start the runners (first run BUILDS the runner image locally — a few minutes).
scripts/start-worker                  # uses AFL_OSM_REPLICAS from .env.worker
```

That's it — the runners register in the shared MongoDB and immediately start
claiming tasks from the shared queue, writing outputs to the shared MinIO.

### Option B — central-config daemon (recommended for a managed fleet)

Instead of `.env.worker`, let the server pull its config (MinIO endpoint, replica
count, image) from the central `fleet_config` in Mongo, and auto-reconcile:

```bash
scripts/fleet-agent watch --mongo mongodb://192.168.68.75:27017
#   (with the /etc/hosts entry above, just: scripts/fleet-agent watch)
```

Then drive the whole fleet from one place (run on any machine):
`scripts/fleet set --osm-replicas N`, `scripts/fleet set --image <tag>`,
`scripts/fleet status` (shows every host + whether it's up to date).

## Verify (from the infra host, or anywhere with Mongo access)

```bash
scripts/fleet status --mongo mongodb://192.168.68.75:27017
#   the new server appears in "live runners … across N host(s)"
```

Then submit a fan-out and watch it spread across servers (see
[deployment.md → Adding a server to the fleet](deployment.md) and the
`osm.heatmap.ContinentHeatmap` workflow). A local rehearsal of all of this on one
box is `scripts/simulate-fleet` (see CLAUDE.md → *Multi-server runner fleet*).

## Notes
- **Secrets (prod).** For real deployments, don't keep MinIO creds in each
  `.env.worker`. On the infra host: `scripts/fleet secret gen-key` (share
  `AFL_FLEET_KEY` out-of-band), `scripts/fleet secret set --minio-access … --minio-secret …`.
  Each server then needs only `AFL_FLEET_KEY` (one rotatable secret); `fleet-agent`
  decrypts the creds at apply.
- **Endpoint reachability.** The MinIO/Mongo addresses must resolve from both the
  host (preflight) and the runner *containers* — a LAN IP or DNS name, never
  `localhost`.
- **Images.** Without a registry, each server builds the runner image locally on
  first `start-worker`. To skip per-server builds, push the image to a registry
  and set it fleet-wide: `scripts/fleet set --image <registry>/<tag>` (agents pull).

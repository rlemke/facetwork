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

> **Use the infra host's IP in `.env.fleet`, not a `.local`/mDNS name.** The runners
> run in Docker containers, and Docker's DNS does **not** resolve mDNS/`.local`
> names, and containers don't read the host's `/etc/hosts`. So `mongodb://server3.local:27017`
> works for `fleet status` (which runs on the host) but the **runner container
> can't reach it → the runner never registers** (the #1 "my runner doesn't show
> up" cause). If the IP changes, update it in `.env.fleet` (one place). To use a
> stable **name** instead: set `AFL_INFRA_IP=<infra host IP>` in `.env.fleet` and use
> `mongodb://afl-mongodb:27017` + `http://afl-minio:9000` — `docker-compose.fleet.yml`
> maps those names *inside the containers* via `extra_hosts` (a host `/etc/hosts`
> entry alone does **not** reach the container).

## Prerequisites on the new server
- Docker + Docker Compose, `git`, Python 3.11+.
- Network: the new server must reach the infra host on **27017** (MongoDB),
  **9000** (MinIO), and **5050** (the runner image registry) — same LAN; open
  the firewall if needed.
- **The Docker daemon must trust the plain-HTTP registry** at
  `<infra-host-ip>:5050` (see step 3b). Throughout this guide, **`<infra-host-ip>`
  is your fleet's infra host** — the single machine running MongoDB + MinIO + the
  registry, i.e. the `AFL_INFRA_IP` you set in `.env.fleet`. Substitute your
  fleet's value (e.g. find it with `grep AFL_INFRA_IP .env.fleet`). Without this
  registry trust, the runner image pull fails with `http: server gave HTTP
  response to HTTPS client` and **no runners start** — even though MongoDB/MinIO
  preflight passes. This is host-level config, not in the repo, so it's easy to
  miss on a new or freshly-reinstalled host.
- A **large local disk** for scratch (multi-GB staging stays local; only outputs
  go to the shared MinIO).

## Steps

```bash
# 1. Clone this repo + the OSM handler package (the runner mounts the handlers).
git clone <facetwork repo url> ~/facetwork && cd ~/facetwork
git clone https://github.com/rlemke/fwh_osm ~/fw_handlers/fwh_osm
#    (the runner mounts ${FWH_HANDLERS_ROOT:-$HOME/fw_handlers}/fwh_osm)

# 2. Python venv with the deps the fleet tooling needs.
python3 -m venv .venv
.venv/bin/pip install -e ".[mongodb,s3]" cryptography   # cryptography = secret store; zeroconf optional (mDNS)

# 3. Point at the shared infra. The shared base is the committed, pre-filled
#    template — copy it as-is, never hand-edit it:
cp .env.fleet.preset .env.fleet
#    Put THIS server's specifics in a separate per-server override file instead,
#    so future `cp .env.fleet.preset .env.fleet` updates never clobber them:
cp .env.fleet.override.example .env.fleet.override
#    REQUIRED in .env.fleet.override: set AFL_DATA_DIR to a big LOCAL path on THIS
#    server (it ships empty — do not skip). macOS: e.g. $HOME/afl_data ; Linux:
#    e.g. /var/lib/afl_data. Create it first — `mkdir -p "$AFL_DATA_DIR"`. Do NOT
#    use /Volumes/afl_data (that's the infra host's disk; on macOS only root can
#    mkdir under /Volumes, so the runner bind-mount fails). Optionally set
#    AFL_OSM_REPLICAS / AFL_INFRA_IP there too. start-runner loads .env.fleet then
#    .env.fleet.override ON TOP (override wins); .env.fleet.override is gitignored.
#
#    Updating later = `git pull && cp .env.fleet.preset .env.fleet` — your
#    .env.fleet.override survives untouched.

# 3b. Trust the plain-HTTP image registry (REQUIRED — the runners pull from it).
#     Add <infra-host-ip>:5050 to the daemon's insecure-registries, then RESTART
#     the Docker engine. Skipping this → "http: server gave HTTP response to
#     HTTPS client" on pull and zero runners start (preflight still passes).
#
#   Linux:  add to /etc/docker/daemon.json then restart dockerd
#     {"insecure-registries": ["<infra-host-ip>:5050"]}
#     sudo systemctl restart docker
#
#   macOS (Docker Desktop): add the same key to ~/.docker/daemon.json, then
#   FORCE-restart Docker Desktop — a graceful `quit` can wedge the VM
#   ("no route to host", engine never returns):
#     osascript -e 'quit app "Docker"'; sleep 3
#     pkill -f "Docker Desktop.app"; pkill -f "com.docker.backend"; sleep 4
#     open -a Docker        # wait ~1-2 min for the engine
#   NOTE: on the INFRA host this also bounces the bundled MongoDB/MinIO/registry
#   (a brief fleet-wide blip) — do it deliberately. Runner-only hosts are safe.
#
#   Verify before continuing:
docker pull <infra-host-ip>:5050/facetwork-runner:latest   # must succeed (no HTTPS error)

# 4. Preflight: confirm shared services + local scratch are usable from here.
scripts/start-runner --fleet --check          # ✓ MongoDB  ✓ MinIO  ✓ Local scratch writable

# 5. Start the runners (first run BUILDS the runner image locally — a few minutes).
scripts/start-runner --fleet                  # AFL_OSM_REPLICAS from .env.fleet(.override)
#    default runners are osm-geocoder + osm-lz; add --example NAME for others.
#    (scripts/start-worker still works — it's a back-compat shim for this.)
```

That's it — the runners register in the shared MongoDB and immediately start
claiming tasks from the shared queue, writing outputs to the shared MinIO.

### Migrating an EXISTING server to the base + override split

A server set up before the `.env.fleet.override` split (or cloned from another
host) may carry per-server values — and drifted *shared* values — inside its
`.env.fleet`. The classic symptom is a host whose config was copied from another:
its `AFL_MONGODB_URL`/name still point at the *source* host, so its containers
can't reach Mongo (a host-only name doesn't resolve inside a container) or it
registers under the wrong name. Fix it in one step:

```bash
git pull
scripts/fleet-env-migrate            # moves AFL_DATA_DIR / AFL_OSM_REPLICAS /
                                     # AFL_RUNNER_NAME(/FLEET_HOST) into
                                     # .env.fleet.override; resets .env.fleet to
                                     # the preset (drifted SHARED values dropped).
                                     # --dry-run to preview; backs up what it replaces.
scripts/start-runner --fleet --recreate
```

After it, `.env.fleet` is a clean copy of the preset and this host's specifics
live in `.env.fleet.override` — so the next `git pull && cp .env.fleet.preset
.env.fleet` can never reintroduce the drift.

### Option B — central-config daemon (recommended for a managed fleet)

Instead of `.env.fleet`, let the server pull its config (MinIO endpoint, replica
count, image) from the central `fleet_config` in Mongo, and auto-reconcile:

```bash
mkdir -p "$HOME/afl_data"                                     # big LOCAL scratch on THIS server
scripts/fleet-agent watch --mongo mongodb://192.168.68.75:27017 --data-dir "$HOME/afl_data"
#   (with the /etc/hosts entry above, just: scripts/fleet-agent watch --data-dir "$HOME/afl_data")
```

> **Pass `--data-dir` (don't rely on `export AFL_DATA_DIR`).** The agent needs a
> large LOCAL scratch path on this server, and it's REQUIRED — there is no
> `/Volumes/afl_data` default (that's the infra host's disk). An exported
> `AFL_DATA_DIR` is fragile: it isn't inherited by an already-running `watch`
> daemon, or under `sudo`/`launchd`/`systemd`. `--data-dir` always wins. Do NOT
> use `/Volumes/afl_data` (on macOS only root can mkdir under `/Volumes`).

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
  `.env.fleet`. On the infra host: `scripts/fleet secret gen-key` (share
  `AFL_FLEET_KEY` out-of-band), `scripts/fleet secret set --minio-access … --minio-secret …`.
  Each server then needs only `AFL_FLEET_KEY` (one rotatable secret); `fleet-agent`
  decrypts the creds at apply.
- **Endpoint reachability.** The MinIO/Mongo addresses must resolve from both the
  host (preflight) and the runner *containers* — a **LAN IP** (simplest) or a real
  **DNS A-record**, never `localhost` and never a `.local`/mDNS name (containers
  can't resolve those). For a container-resolvable alias, set `AFL_INFRA_IP` and
  use `afl-mongodb`/`afl-minio` (mapped via `extra_hosts`).
- **Images.** Without a registry, each server builds the runner image locally on
  the first `start-runner --fleet`. To skip per-server builds, push the image to a registry
  and set it fleet-wide: `scripts/fleet set --image <registry>/<tag>` (agents pull).

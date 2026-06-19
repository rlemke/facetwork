# First-time install — from nothing to a running Facetwork

This guide assumes you have **no tools installed**, **no prior knowledge** of
Facetwork or its dependencies, and **haven't cloned anything**. It walks you all
the way from a blank machine to running a workflow, for four common setups.

Read **"What the pieces are"** first (2 minutes), then jump to your scenario.

---

## What the pieces are (plain language)

Facetwork runs **workflows** — pipelines you write in a small language called
**FFL**. To run them, a few cooperating parts are involved:

| Piece | What it is | Do I need to understand it? |
|-------|-----------|------------------------------|
| **MongoDB** | A database. It's the shared "to-do list" + memory: every workflow step (a *task*) and its state lives here. Everything coordinates through it. | Just know it must be running and reachable. |
| **MinIO** | An S3-compatible **object store** — a shared file drawer for the cache and outputs (maps, rasters). Lets any machine read/write the same files. | Needed only when more than one machine is involved (or you want shared outputs). One machine can use local disk instead. |
| **Runner** | A program that pulls tasks from MongoDB and does the work (runs the Python that implements each step). You can run **many**, on **many machines**. | This is the workhorse. Runners are disposable — start/stop freely. |
| **Dashboard** | The web UI (browser, port 8080) to launch workflows and watch them run. | This is how you'll actually use it. |
| **Example / handler package** | The domain code that implements a workflow's steps (e.g. `fwh_sentinel2` for satellite water maps). Installed into a runner's environment. | Install the one(s) you want to run. |

**The one rule that explains every setup:** only **MongoDB + MinIO** need to be
stable and reachable. Everything else — dashboard, runners — is stateless and
can come, go, or move. The four scenarios below differ only in *where those two
services live* and *who runs runners*.

You drive everything from a clone of the main repo:

```
https://github.com/rlemke/facetwork.git
```

---

## Step 0 — Install the prerequisites (once per machine)

You need **git** and **Docker** on every machine. (Python is only needed if you
run runners outside Docker — Scenarios 3/4 note where.)

- **git** — version control, used to clone the repo.
- **Docker** (Docker Desktop on Mac/Windows; Docker Engine + the Compose plugin
  on Linux) — runs MongoDB, MinIO, the dashboard, and runners as containers, so
  you don't install those by hand.

| OS | Install Docker | Install git |
|----|----------------|-------------|
| **macOS** | [Docker Desktop](https://www.docker.com/products/docker-desktop/) (.dmg) | `xcode-select --install` (or it comes with Docker Desktop's tools) |
| **Windows** | [Docker Desktop](https://www.docker.com/products/docker-desktop/) — it will enable WSL2 | [git-scm.com](https://git-scm.com/download/win) |
| **Linux** | [Docker Engine + Compose plugin](https://docs.docker.com/engine/install/) | `sudo apt install git` (Debian/Ubuntu) |

Verify (any OS):

```bash
git --version          # any recent version
docker --version       # 24+ recommended
docker compose version # the Compose plugin (note: "docker compose", not "docker-compose")
```

Start Docker Desktop (Mac/Windows) before continuing — the whale icon should say
"running".

---

## Step 1 — Clone the repo

```bash
git clone https://github.com/rlemke/facetwork.git
cd facetwork
```

That's the only repo you need to get started. **Example workflows live in their
own repos** (so they version independently); you install the one(s) you want
later with a helper — you do **not** clone them by hand:

```bash
fw install example --list          # see what's available
fw install example sentinel2-landchange   # clone + install one
```

(`fwh_sentinel2`, `fwh_osm`, `fwh_noaa_weather`, … — the helper clones them into
`~/fw_handlers/` and installs them for you.)

---

## Step 2 — Pick your scenario

| # | You want… | MongoDB + MinIO live on… | Runners on… | Go to |
|---|-----------|--------------------------|-------------|-------|
| **1** | Everything on one computer (try it / solo work) | your one machine (containers) | your one machine | [Scenario 1](#scenario-1--all-on-one-machine) |
| **2** | Your machine as the hub; teammates join as runners that come & go | **your machine** (containers) | your machine + teammates' laptops | [Scenario 2](#scenario-2--your-machine-is-the-hub-teammates-are-runners) |
| **3** | Small team, but the database & store are on shared/company servers | dedicated/shared servers | team machines or servers | [Scenario 3](#scenario-3--shared-infra-on-dedicated-servers) |
| **4** | A production company/cloud deployment | managed/cloud (replica set, S3/MinIO) | a managed runner fleet | [Scenario 4](#scenario-4--company--cloud-deployment) |
| **5** | **Just to *use* Claude to run workflows** — a teammate already set up a shared server | a shared server (someone else's) | the shared server's fleet | [Scenario 5](#scenario-5--just-claude--a-shared-mcp-server-no-clone) |

> Scenario 5 is the **thin-client** path: you **skip Step 1 (no clone)** and install
> nothing but your editor — everything runs on a server someone else set up.

---

## Scenario 1 — All on one machine

**For:** trying Facetwork, or solo work on a desktop/laptop. Simplest path —
**Docker only, no Python, no MinIO setup.** Everything runs in containers on your
machine; the cache/outputs use local storage.

```bash
# from the facetwork clone:
docker compose up -d                          # MongoDB + dashboard + a runner + sample agent
docker compose --profile seed run --rm seed   # load the example workflows
```

Open **http://localhost:8080** → you should see the dashboard (it lands on
**Runs**). Click **New run**, pick a workflow (e.g. the sample, or
`AnalyzeAOI` with `use_mock: true` after installing an example), and **Run**.

Stop everything with `docker compose down` (add `-v` to also wipe the local
data volumes).

> Want to run real example workflows (e.g. Sentinel-2 maps)? Install one with
> `fw install example sentinel2-landchange`, then start a runner for it —
> see the example's own README, and [beginners-guide.md](beginners-guide.md).

That's the whole setup for one machine. The scenarios below only add **other
machines** and **shared infra**.

---

## Scenario 2 — Your machine is the hub; teammates are runners

**For:** a small team where **your machine hosts the shared services** (MongoDB +
MinIO + dashboard) and one runner, and teammates point their laptops at it as
**extra runners that can come and go**. This is the "informal fleet" — teammates'
machines are disposable; if one closes its laptop, its in-flight work is picked
up by another.

**On your machine (the hub):** bring up the bundled full stack — it includes
MinIO (so files are shared across machines), MongoDB, the dashboard, and runners:

```bash
docker compose -f docker-compose.full-stack.yml up -d
docker compose --profile seed run --rm seed
```

Find your machine's LAN IP (e.g. `192.168.1.50`) — teammates will point at it.
Make sure MongoDB (27017) and MinIO (9000) are reachable on your network.

**On each teammate's machine:** install Docker + git (Step 0), clone the repo
(Step 1), then point at the hub and start a runner:

```bash
cp .env.fleet.preset .env.fleet     # then edit: set the hub's IP for Mongo + MinIO
fw runner start --fleet        # starts runner container(s) against the hub
```

Teammates can stop/close anytime; a background **reaper** reclaims their in-flight
tasks so another runner finishes them.

**Full walkthrough (the exact `.env.fleet` fields, adding/removing machines, the
central `fleet` config):**
- [docs/operations/informal-fleet.md](../operations/informal-fleet.md) — the model + who it's for
- [docs/operations/join-fleet-from-new-server.md](../operations/join-fleet-from-new-server.md) — a teammate joining, step by step

---

## Scenario 3 — Shared infra on dedicated servers

**For:** a small team where **MongoDB and MinIO run on shared/company servers**
(not a laptop) — more stable and always-on — while runners run on team machines
or a few servers.

The only change from Scenario 2 is **where the two services live**: instead of
the bundled containers, you point everything at the standalone MongoDB + MinIO.

1. **Stand up the two services** on the dedicated server(s):
   - MongoDB, bound so other machines can reach it (`--bind_ip 0.0.0.0`; see
     `fw db mongo-start`), or a managed MongoDB.
   - MinIO (or any S3-compatible store), reachable on the network.
2. **Record them centrally once** (so every runner self-configures):

   ```bash
   fw fleet set --mongo-url mongodb://<server>:27017 \
                     --minio http://<server>:9000 --dashboard-url http://<server>:8080
   ```
3. **On each runner machine:** clone, set the Mongo URL, and join:

   ```bash
   export AFL_FLEET_KEY=<shared-key>     # decrypts the central MinIO creds
   fw fleet agent watch             # reads central config, brings runners up, keeps them current
   ```

Reference: [docs/operations/deployment.md](../operations/deployment.md) →
**"Central fleet config"** and **"Adding a server to the fleet"**, and the
**Multi-server database access** notes in the project README/CLAUDE.md (binding
`0.0.0.0`, `/etc/hosts` entries for `afl-mongodb` / `afl-minio`).

---

## Scenario 4 — Company / cloud deployment

**For:** a production deployment owned by a company. Same architecture, hardened:

- **MongoDB:** a managed cluster / replica set (e.g. Atlas or self-managed HA) —
  not a single container. Facetwork only needs its **URL**.
- **MinIO / S3:** a managed S3 bucket or an HA MinIO. Set `AFL_STORAGE=s3` and the
  `AFL_S3_*` endpoint/credentials so portable `s3://` URIs work fleet-wide (no
  shared disk needed).
- **Dashboard:** deployed as a long-running service (its own container/host).
- **Runners:** a managed **fleet** with **image-based rollouts** — build a runner
  image, push to a registry, and roll it out centrally (`fleet set --image …`);
  a graceful drain + reaper + retry/dead-letter handles in-flight work during a
  rollout.

References:
- [docs/operations/deployment.md](../operations/deployment.md) — the full deployment guide (storage backends, S3/MinIO, fleet config, secrets)
- [docs/operations/fleet-rollouts.md](../operations/fleet-rollouts.md) — image-based change deployment + runner lifecycle, and how this compares to Kubernetes/Temporal-grade pipelines
- [docs/operations/informal-fleet.md](../operations/informal-fleet.md) — note on what's required to go from a small team to data-center scale (a real engineering investment: hardening / scheduling / observability)

---

## Scenario 5 — Just Claude + a shared MCP server (no clone)

**For:** someone who only wants to *use* Claude to run (or author) workflows, where
a teammate has **already set up a shared server** running the whole stack — MongoDB,
MinIO, the runner fleet, the catalog, **and the MCP server**. You **don't clone the
repo, don't install Python/Docker, and don't run a runner.** You connect your AI
editor to the shared MCP server and drive everything in plain language. The
server's fleet does the work.

**The one prerequisite the server side must satisfy:** that shared server is itself
a Scenario 3/4 setup *plus* it runs the MCP (`python -m facetwork.mcp`) pointed at
the shared MongoDB, and its runners have the example handler packages installed.
(Whoever owns the server does this once.)

**What you (the thin client) need:**
- An MCP client — e.g. **Claude Code** — on your laptop.
- **SSH access** to the shared server (Facetwork's MCP speaks stdio, so the standard
  way to reach a remote one is to launch it over SSH).

**Setup — a tiny `.mcp.json` in your working folder (no repo):**

```json
{
  "mcpServers": {
    "facetwork": {
      "command": "ssh",
      "args": [
        "you@fw-server",
        "cd /opt/facetwork && .venv/bin/python -m facetwork.mcp --log-level WARNING"
      ]
    }
  }
}
```

Replace `you@fw-server` and the server path. That's the whole install — Claude Code
auto-detects the config and connects; the MCP runs **on the server**, already wired
to the team's MongoDB/MinIO/catalog. (If the team instead exposes the MCP over a
network transport, point your client at that URL — ask whoever set it up.)

**Then just talk to Claude** (each maps to the catalog/runtime MCP tools):

- *"List the workflows in the catalog."* → `fw_catalog_search`
- *"Run `s2.water-level-vs-extent` on Lake Tahoe."* → `fw_catalog_match` → `fw_catalog_run`
- *"How did the Great Salt Lake's water change 2004–2024? Use a cataloged workflow."* → match → run
- *"Validate this FFL / save it to the catalog and publish."* → `fw_validate`, `fw_catalog_save/publish`

You never clone, seed, or start a runner — the shared server's fleet executes, and
you watch progress on its dashboard URL. **Limit:** you can only run what the
server's runners have handlers for (the installed example packages); ask the server
owner to add a package if you need one it doesn't have.

---

## Step 3 — Verify it's working (any scenario)

1. Open the dashboard (**http://localhost:8080** locally, or the dashboard URL
   for shared infra). It lands on **Runs**.
2. `fw runner list` — confirm at least one runner is **running**.
3. Click **New run**, pick a seeded workflow, fill parameters, **Run**, and watch
   the live execution graph + step logs.

If a run sits **pending** forever, it usually means **no runner has a handler for
that workflow** — install/start the matching example
(`fw install example <name>` + `fw runner start --example <name>`).

## Where to go next

- [beginners-guide.md](beginners-guide.md) — your first workflow + basic FFL
- [tutorial.md](tutorial.md) — a deeper, hands-on tour
- [../reference/dashboard.md](../reference/dashboard.md) — the dashboard (v3) reference
- The main **README** / **CLAUDE.md** — full command reference, the catalog, the fleet

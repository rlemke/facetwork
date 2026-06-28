# Fleet rollouts & runner lifecycle

How code reaches the multi-server runner fleet, what happens to in-flight work
during a rollout, the command-line surface for starting/stopping runners, and
how this design compares to the deployment systems used at large companies.

> Prerequisite reading: the **Multi-server fleet** section of
> [deployment.md](deployment.md) and the role model
> ([`project_fleet_role_model`]). This doc assumes you already have a fleet:
> one shared MongoDB + one shared MinIO, a central `fleet_config` record, and
> `fleet-agent watch` running on each runner server.

---

## 1. Mental model

The fleet has no master. Two participant kinds:

- **Infra services** — MongoDB, MinIO, Dashboard — addressed by URL only.
- **Runner servers** — homogeneous, stateless, leaderless. They hold nothing
  but local scratch and coordinate solely through the atomic `claim_task()` in
  Mongo.

Deployment is **declarative and pull-based**. A single central record drives the
whole fleet:

```
fleet_config (Mongo, _id="default")
  ├─ version: N                      # bumped on every `fleet set`
  ├─ roles.osm-geocoder.replicas: 2
  ├─ roles.osm-geocoder.image: <registry>/facetwork-runner:<tag>
  └─ endpoints / minio / bucket
```

Each server runs `fleet-agent watch`, a daemon that polls `fleet_config.version`
every 30s. When the version changes, the agent reconciles **its own** host to the
desired state. Nothing is pushed; every server pulls. See
[`fw fleet agent`](../../scripts/lib/fleet/agent) lines 174-202.

There are two reconcile paths, chosen by the agent:

| Change in `fleet set` | Agent action | Disruption |
|-----------------------|--------------|------------|
| **replica / version only** (image unchanged) | `docker compose up -d` (no `--recreate`) — idempotent rescale | none — running runners untouched |
| **image tag changed** | force-recreate: `compose up -d --recreate` (pull + replace containers) | runners are stopped & replaced |

---

## 2. Change deployments (image-based rollout)

A real code rollout means: build a new runner image, push it to the registry
every server can pull from, then point the central config at the new tag.

> **TL;DR — `fw fleet rollout`** does all of §2.2–§2.4 in one shot: builds the
> image at `HEAD` (tagged by git short SHA, cache-from the current image), pushes
> it, runs `fw fleet set --image`, and polls until every host converges. Use
> `--dry` to preview. The manual steps below are the underlying procedure (useful
> when you need a custom tag, a different builder/arch, or to debug a step).

### 2.1 Tag convention

Images are tagged by **git short SHA** and pushed to the private registry:

```
<registry>/facetwork-runner:<git-short-sha>
# e.g. 192.168.68.96:5050/facetwork-runner:65b54e0
```

All per-example runners build from the **same** `docker/Dockerfile.example-runner`
(the example is chosen at runtime via `FW_EXAMPLE_NAME`), so one tag serves
every example. Build the **same architecture** the fleet runs (the current fleet
is all Apple Silicon → `linux/arm64`).

### 2.2 Build + push

The registry is plain HTTP. If the local Docker daemon is not configured for that
insecure registry, do **not** reconfigure + restart the engine (it bounces every
container on the box). Instead use a throwaway **buildx container builder** with
an insecure-registry config — it pushes straight to the registry without touching
the main daemon, and can pull the previous image as a layer cache so only the
changed code layer rebuilds.

```bash
# 0. clean tree at the commit you want to ship
git rev-parse --short HEAD              # -> the new tag, e.g. 65b54e0

# 1. one-time: a builder that may speak HTTP to the registry
cat > /tmp/buildkitd-fleet.toml <<'EOF'
[registry."192.168.68.96:5050"]
  http = true
  insecure = true
EOF
docker buildx create --name fleetbuilder --driver docker-container \
  --config /tmp/buildkitd-fleet.toml --bootstrap

# 2. build the runner image for the fleet's arch, push to the registry,
#    reusing the previous image as cache (only the code layer rebuilds)
docker buildx build \
  --builder fleetbuilder \
  --platform linux/arm64 \
  -f docker/Dockerfile.example-runner \
  -t 192.168.68.96:5050/facetwork-runner:$(git rev-parse --short HEAD) \
  --cache-from type=registry,ref=192.168.68.96:5050/facetwork-runner:<previous-tag> \
  --provenance=false \
  --push .

# 3. (optional) verify the tag landed
curl -s http://192.168.68.96:5050/v2/facetwork-runner/tags/list

# 4. tidy up the temporary builder
docker buildx rm fleetbuilder
```

### 2.3 Trigger the rollout

```bash
fw fleet set --image 192.168.68.96:5050/facetwork-runner:<new-tag>
```

This bumps `fleet_config.version` and changes the image. Within one ~30s poll
cycle every server's `fleet-agent watch` sees the new version, sees the image
changed, takes the **force-recreate** path (pull + recreate its osm runners),
and records its `applied_version` / `applied_image`.

### 2.4 Verify

```bash
fw fleet status          # config version + per-host applied version/image
```

Watch the per-host reconcile records converge to the new version and image. On
any one host you can confirm the containers were actually replaced:

```bash
docker ps --filter name=runner-osm-geocoder \
  --format '{{.Names}}  up {{.RunningFor}}  {{.Image}}'
# fresh "up About a minute" + the new tag == real recreate
```

### 2.5 Rollback

Both tags remain in the registry, so rollback is just another `fleet set`:

```bash
fw fleet set --image 192.168.68.96:5050/facetwork-runner:<previous-tag>
```

### 2.6 What `fleet set` cannot deploy

`fleet set` deploys the **runner image** only. Two things ride along the **git
checkout** on each server, not the registry image, and therefore need a
`git pull` on each host (plus a recreate) to take effect:

- **`docker-compose.fleet.yml`** itself (e.g. `stop_grace_period`, env wiring) —
  the fleet-agent runs compose from each server's *local* copy.
- The **dashboard** image is built and deployed separately (see
  [deployment.md](deployment.md) → S3/MinIO Integration and the dashboard
  service in `docker-compose.full-stack.yml`); it is not part of the fleet
  runner roll.

---

## 3. What happens to running tasks during a rollout

A `fleet set --image` rollout force-recreates runner containers. It does **not**
drain them first. A running task's fate depends on whether it finishes inside the
container stop-grace window.

### 3.1 Graceful path (task finishes within the grace window)

The runner runs as PID 1 (`docker/entrypoint-example-runner.sh` uses `exec`), so
it receives SIGTERM directly and installs a handler
(`facetwork/runtime/runner/__main__.py:316`) that calls `service.stop()`:

1. stops claiming **new** tasks,
2. waits up to `shutdown_timeout_ms` (default **30s**) for in-flight tasks to
   finish (`facetwork/runtime/runner/service.py` `_shutdown`,
   `executor.shutdown(wait=True, cancel_futures=False)`),
3. deregisters its server record.

If the task completes in time, it commits its result normally — no recovery
needed. **This is why `stop_grace_period: 35s` is set on the fleet runners**
(in `docker-compose.fleet.yml`): Docker's *default* grace is only 10s, which
would truncate the 30s drain. 35s (just over the drain) lets the runner exit
cleanly first.

### 3.2 Ungraceful path (task still running at the grace deadline)

If a task is still running when the grace period elapses, Docker sends SIGKILL.
The process dies mid-task; the task stays `state=running` in Mongo with
`server_id` still pointing at the dead runner. Recovery is automatic, via the
**reaper**:

- Every surviving runner runs a reaper every 60s
  (`service.py` `_maybe_reap_orphaned_tasks`).
- When a server's `ping_time` is stale by more than `FW_REAPER_TIMEOUT_MS`
  (**default 120s**), the reaper
  (`facetwork/runtime/mongo_store/tasks.py` `reap_orphaned_tasks`) resets that
  server's `running` tasks to `pending`, clears `server_id`, and
  `$inc: {retry_count: 1}`.
- A healthy runner then claims the task from `pending` and **re-runs the handler
  from scratch** (no checkpoint of handler progress is kept).

**Realistic recovery window: ~2–3 minutes** (≤grace to die + up to 120s
stale-detection + next 60s reaper tick + reclaim).

Recovery is driven by **server-heartbeat staleness**, not the per-task lease
(`FW_LEASE_DURATION_MS`, default 5min). The lease is the slower fallback that
lets another runner reclaim a stale `running` task directly on `claim_task`.

### 3.3 Retry ceiling & idempotency

- Each reaper reset increments `retry_count`. When `retry_count >= max_retries`,
  the task is moved to `dead_letter` instead of retried
  (`tasks.py` `_dead_letter_overdue`).
- Because the handler re-runs from the beginning, **handlers must be
  idempotent**. `retry_count` is exposed in the payload so a handler can detect a
  retry. The osm handlers are idempotent (e.g. `osm.cache.Download` checks the
  sidecar cache and skips/re-downloads), so the reaper path is safe for them.

### 3.4 The two watchdogs (don't confuse them)

| Mechanism | Triggers on | Default | Recovers a rollout kill? |
|-----------|-------------|---------|--------------------------|
| **Reaper** | dead **server** (stale `ping_time`) | `FW_REAPER_TIMEOUT_MS` = 120s | **Yes** |
| **Stuck-task watchdog** | stalled **task** on a *live* server | `FW_STUCK_TIMEOUT_MS` = 30min | no — different failure mode |
| Per-task execution timeout | task exceeds its own limit | `FW_TASK_EXECUTION_TIMEOUT_MS` = 15min | safety net |
| Task lease | stale `running` task reclaim on claim | `FW_LEASE_DURATION_MS` = 5min | slow fallback |

### 3.5 Zero-interruption rollouts

For no in-flight interruption at all, **drain first** so tasks are cleanly
re-queued before any container stops:

```bash
fw runner drain --all        # reset running -> pending, then stop
# ... then fleet set --image ...
```

The fleet-agent does **not** drain automatically. For idempotent handlers the
rollout-then-reaper path is usually acceptable (worst case: a couple of tasks
redo ~2–3 min of work).

### 3.6 Server records left behind by a rollout

A `fleet set --image` rollout force-recreates the **fleet-managed role
runners** — osm-geocoder, plus ffl/gh-router when their image changes (it does
**not** touch the per-example runners; see §2.6). Each recreated container
deregisters (or is reaped) and its `servers` row goes to `state="shutdown"`.

**The cleanup that follows is not specific to osm or to rollouts.** Any runner
of any role (osm, ffl, gh-router, or an example runner like anthropic/census)
leaves a `state="shutdown"` row whenever it stops — a rollout recreate, a manual
`docker compose --scale` down, a `drain-runners`, a plain graceful stop, or a
crash the reaper later marks. All of them are cleaned up the same way, by the
reaper loop on every live runner (`service.py` `_maybe_reap_orphaned_tasks` →
`prune_stale_servers`), using **two windows keyed only on `state`** (never on
role or facet name):

| Server state | Prune window | Why |
|--------------|--------------|-----|
| `running` / `startup` (live) | `max(10×FW_REAPER_TIMEOUT_MS, 10min)` ≈ **20 min** | A briefly-quiet live runner (GC pause, slow Mongo) must never be deleted out from under itself. |
| `shutdown` (terminal) | `FW_REAPER_TIMEOUT_MS` ≈ **2 min** | Explicitly dead — a graceful deregister or the reaper marked it. Nothing to protect, so it clears fast instead of inflating counts. |

So any `shutdown` row — post-rollout or otherwise — self-clears within ~2 min;
no manual cleanup is needed. Until then, **`fw runner list` (no filter)
counts them** — use `fw runner list --state running` for the true live
count during the window, or just wait. The prune runs from any live runner and
acts on the whole `servers` collection, so a single up-to-date runner keeps the
collection clean fleet-wide (one reason a lagging host is rarely urgent).

---

## 4. Updating & starting runners from the command line

There are three operating modes. Pick by where the runners run.

### 4.1 Local processes (dev / single box)

```bash
# register handlers from examples + start dashboard + start the runner service
fw runner start                                   # ALL examples
fw runner start --example osm-geocoder            # one example
fw runner start --example osm-geocoder -- --log-format text
fw runner start --instances 3                     # 3 concurrent runner processes
fw runner start --no-dashboard                    # skip the dashboard
```

### 4.2 Containers (this host)

```bash
# runners as containers against the LOCAL bundled infra
fw runner start --docker --example osm-geocoder

# runners as containers joining the EXTERNAL shared Mongo+MinIO (the fleet);
# preflights both, reads .env.fleet. Default services: osm-geocoder + osm-lz.
fw runner start --fleet
fw runner start --fleet --example noaa-weather
fw runner start --fleet --example census-us --replicas 6
fw runner start --fleet --recreate                # force-recreate containers
```

`fw runner start --fleet` is a back-compat shim for `start-runner --fleet`.

### 4.3 The fleet way (config-driven, all servers at once)

This is how you "update and start" the whole fleet without touching individual
boxes. Edit the central config once; every server's agent reconciles.

```bash
# change desired state centrally (each bumps fleet_config.version)
fw fleet set --osm-replicas 8                     # every host rescales to 8
fw fleet set --image <registry>/facetwork-runner:<tag>   # rolling image deploy
fw fleet status                                   # config + per-host drift

# on each runner server (one-time join): a daemon that auto-applies changes
export FW_FLEET_KEY=<key>
fw fleet agent watch                              # poll + reconcile forever
fw fleet agent apply                              # one-shot reconcile (no daemon)
fw fleet agent apply --dry-run                    # preview
```

A brand-new server joins with one command (`fleet-agent watch`) and self-configures
from the central config + encrypted secret store. See
[join-fleet-from-new-server.md](join-fleet-from-new-server.md).

### 4.4 Remote process mode & rolling restart (SSH)

For process-mode runners on remote hosts (requires `FW_RUNNER_HOSTS` or `--host`):

```bash
fw runner start --all --example osm-geocoder      # all FW_RUNNER_HOSTS
fw runner start --host h1 --host h2 --example osm-geocoder

# zero-downtime SERIAL restart: drain -> wait SHUTDOWN -> start -> wait RUNNING,
# one host at a time. Re-registers handlers once before the loop.
fw fleet rolling-deploy --example osm-geocoder
fw fleet rolling-deploy --host h1 --host h2 --drain-timeout 90
```

---

## 5. Shutting down runners from the command line

### 5.1 Stop (may leave tasks `running` until the reaper)

```bash
fw runner stop                       # local: SIGTERM, wait 5s, then SIGKILL
fw runner stop --all                 # all remote servers found in Mongo
fw runner stop --host h1 --host h2
fw runner stop --all --drain-timeout 30
```

Remote mode SSHs to each host, sends SIGTERM, polls Mongo for `SHUTDOWN`, and
force-kills if needed.

### 5.2 Drain (graceful — resets running tasks to pending first)

Prefer this when you care about in-flight work. Each reset task gets a step-log
entry for audit.

```bash
fw runner drain                      # stop local runners + reset running->pending
fw runner drain --tasks-only         # reset tasks, leave processes running
fw runner drain --dry                # preview, change nothing
```

### 5.3 Fleet-wide scale-to-zero

To stop fleet **containers** centrally, set the role replicas to 0 and let the
agents reconcile (or stop containers on each host directly):

```bash
fw fleet set --osm-replicas 0         # agents scale osm runners to 0
# or, per host:
docker compose -f docker-compose.full-stack.yml -f docker-compose.fleet.yml \
  stop runner-osm-geocoder
```

### 5.4 Which to use

| Goal | Command |
|------|---------|
| Quick local stop | `stop-runners` |
| Stop without orphaned `running` tasks | `drain-runners` |
| Stop all remote process runners | `stop-runners --all` |
| Zero-downtime code restart (process mode) | `rolling-deploy` |
| Stop/scale the fleet centrally | `fleet set --osm-replicas 0` |

---

## 6. How this compares to production autodeploy (Apple / Microsoft scale)

This fleet is, structurally, a **pull-based work-queue worker pool with a
declarative desired-state controller** — the same family as Temporal/SQS workers
behind a Kubernetes Deployment. The pieces map cleanly onto what large
orgs run, but several production guarantees are intentionally simplified.

### 6.1 What maps directly

| This fleet | Industry equivalent |
|------------|---------------------|
| `fleet_config` (desired state in Mongo) + `fleet-agent watch` reconcile loop | Kubernetes **Deployment** spec + the Deployment/ReplicaSet **controller** reconcile loop |
| `fleet set --image <tag>` → agents pull + recreate | `kubectl set image` / updating a Deployment's `image:` → rolling update |
| `fleet set --osm-replicas N` | `kubectl scale` / `replicas: N` |
| Per-host `applied_version` vs desired `version` | Deployment `observedGeneration` / `kubectl rollout status` |
| Image tag = git SHA in a registry | Immutable, content-addressed images in a registry (the universal norm) |
| `claim_task()` atomic pull from Mongo | SQS/Kafka/Temporal task queue; workers pull, no central dispatcher |
| Reaper resets dead-server tasks → re-run | At-least-once delivery: visibility-timeout / lease expiry re-queues (SQS, Temporal activity timeouts) |
| `retry_count` → `dead_letter` | Max-receive-count → **dead-letter queue** |
| `drain-runners`, `rolling-deploy` | Pod **graceful termination** + `PreStop` hooks; serial `RollingUpdate` |
| `stop_grace_period: 35s` | Pod `terminationGracePeriodSeconds` |

So the *shape* is industry-standard: immutable images, a registry, a declarative
desired state, a reconcile loop, pull-based work distribution, and at-least-once
recovery with dead-lettering.

### 6.2 What a Big-Tech pipeline adds that this does not

1. **Health-gated rollout.** Kubernetes won't send traffic to a new pod until its
   **readiness probe** passes, and a rolling update advances only as new pods go
   Ready (`maxSurge`/`maxUnavailable`). Here, `fleet-agent` recreates containers
   and records `applied_version` immediately — there is **no readiness gate**. A
   broken image is "deployed" the moment the container starts; nothing blocks the
   roll. (Mitigation today: it's a pull-based pool, so a crash-looping runner
   simply stops claiming work rather than dropping requests — failure is quieter
   than for a request-serving service, but still undetected by the roller.)

2. **Automated rollback.** `kubectl rollout undo`, Argo Rollouts, and Flagger
   **auto-revert** when health/metrics regress. Here rollback is a manual
   `fleet set --image <previous-tag>` — correct and fast, but human-triggered.

3. **Progressive delivery (canary / blue-green).** Apple/Microsoft typically ship
   to 1% → 5% → 25% → 100% with automated metric analysis (Argo Rollouts,
   Flagger, internal systems), or stand up a parallel "blue" fleet and flip
   traffic. Here every server flips on the **same** config version within one
   ~30s poll — effectively an **all-at-once rolling deploy**, not a canary. You
   *can* approximate a canary by giving one role/host a different image, but
   there's no built-in traffic-percentage or metric-gated promotion.

4. **GitOps / signed supply chain.** Mature shops drive desired state from a git
   repo via **Argo CD / Flux** (the cluster syncs to git; every change is a
   reviewed PR with history), build via CI, and enforce **signed images**
   (cosign/Notary) + admission control. Here desired state lives in a Mongo
   document edited by `fleet set`, the image build/push is a manual out-of-band
   step, and `docker-compose.fleet.yml` propagates by `git pull` per host. There
   is no signing, no admission policy, no single audited change log for
   deployments.

5. **Graceful, lease-aware handoff.** This fleet recovers killed work
   **reactively** via a ~2–3 min reaper after a heartbeat goes stale — at-least-once
   with possible full re-execution. Temporal/Cadence (used heavily at large orgs)
   persist **workflow/activity state** so work resumes mid-execution with
   exactly-once *effects*; Kubernetes pairs `PreStop` + `terminationGracePeriod`
   with **PodDisruptionBudgets** so voluntary disruptions (a rollout) never kill
   more than a budgeted number at once. Here a rollout that recreates all replicas
   at once *can* interrupt every in-flight task simultaneously unless you
   `drain-runners` first; there is no PDB-equivalent and no mid-task checkpoint.

6. **Observability & SLO gating.** Production rollouts are gated on dashboards,
   distributed tracing, and SLO burn-rate alerts, often auto-pausing a roll on
   regression. Here you get `fleet status` drift + the dashboard's per-task view;
   gating/pausing is manual.

### 6.3 Honest summary

The fleet gets the **fundamentals** right and they're the same fundamentals the
big systems use: immutable registry images, a declarative central desired state,
a per-node reconcile loop, leaderless pull-based work distribution, and
at-least-once recovery with dead-lettering. Where it diverges from an Apple- or
Microsoft-grade pipeline is the **safety envelope around the roll**: no
readiness-gated progression, no canary/blue-green, no automated metric-based
rollback, no GitOps audit trail or image signing, and reaper-based (reactive,
re-from-scratch) recovery instead of budgeted disruption + checkpointed handoff.
For a homogeneous pool of **idempotent** batch workers — which is exactly what
this is — those simplifications are reasonable; the gaps matter most if the
handlers become non-idempotent or the work becomes latency-sensitive request
serving rather than batch tasks.

**Who it's for, in one line:** this is built for **small/trusted teams and
research groups** turning the machines they already have into a resilient batch
fleet — *not* multi-team, SLO-bound production at scale. The runner machines can
literally be your team's desktops and laptops coming and going; see
[informal-fleet.md](informal-fleet.md).

**Large-scale / data-center operation is possible but untested.** The
fundamentals scale horizontally, so a shared Mongo + MinIO fronting hundreds of
nodes is architecturally plausible — but it has **not** been validated at that
scale (all testing to date is small-team / research scale). Treat large-scale
data-center data processing as a real engineering investment: budget time to
prove it out and to add the hardening/scheduling/observability that mature
large-scale data-processing deployments require (the §6.2 gaps above). See
[informal-fleet.md → "Could it run in a real data center, at large scale?"](informal-fleet.md).

---

## See also

- [informal-fleet.md](informal-fleet.md) — your team's own machines as the
  cluster, who this model is for, and where it stops
- [multi-language-handlers.md](../guides/multi-language-handlers.md) — Java / Go
  / TS handlers alongside the Python runners
- [deployment.md](deployment.md) — full fleet setup, central config, secret store
- [join-fleet-from-new-server.md](join-fleet-from-new-server.md) — adding a server
- [full-stack-compose.md](full-stack-compose.md) — one runner per `fwh_*` example
- Runner resilience env vars: [../../CLAUDE.md](../../CLAUDE.md) → *Runner resilience tuning*
- Workflow repair / terminate: [deployment.md](deployment.md) and the dashboard

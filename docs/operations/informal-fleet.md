# An informal fleet: your team's own machines as the cluster

The single most useful thing to understand about Facetwork's deployment model:

> **Only the central MongoDB + MinIO need to be stable. The runner machines are
> stateless, leaderless, and disposable — they can come and go at any time. So
> your "cluster" can just be your team's desktops and laptops.**

You do **not** need a data center, Kubernetes, a scheduler, or a formal cluster
to get a real distributed data-processing framework. Point a few personal
machines at one shared Mongo + one shared MinIO and you have one.

## Why the runner machines can be disposable

Every runner is a homogeneous, stateless process that holds nothing but local
scratch; all coordination goes through MongoDB and all durable output goes to
MinIO. Concretely:

- **Joining is one command.** A machine becomes a runner by starting a runner
  pointed at the shared Mongo + MinIO (`scripts/start-runner --fleet`, or the
  `fleet-agent` daemon). No registration ceremony, no leader election.
- **Leaving is safe.** If a machine disappears mid-task — someone closes their
  laptop, a desktop reboots, the Wi-Fi drops — the **reaper** notices the dead
  server (default ~2 min, `AFL_REAPER_TIMEOUT_MS`), resets its in-flight tasks
  to `pending`, and another machine claims them. Work is **retried with
  backoff**, not lost.
- **Rejoining is automatic.** The laptop comes back, starts its runner, and
  immediately starts claiming pending work again.
- **Outputs survive the machine.** Caches and results are written to MinIO with
  portable `s3://` URIs that *any* machine on *any* host can resolve, so a leaf
  produced on one laptop and the merge that consumes it on another Just Work —
  no shared disk, no NFS.

So a machine flickering in and out of the fleet is a normal, expected event the
runtime is built to absorb — not an outage.

## What this looks like in practice

A research group with four people and some idle compute:

```
  ┌─────────────────────────────────────────────┐
  │  one reasonably-stable host (or small VM):   │
  │     MongoDB   +   MinIO   (+ dashboard)      │   ← the only things that must stay up
  └─────────────────────────────────────────────┘
        ▲          ▲          ▲          ▲
        │          │          │          │   (each points at the shared Mongo+MinIO)
   Alice's     Bob's      lab desktop   Carol's
   laptop      desktop    (always on)   laptop
   (9–5)       (always)   (always)      (evenings)
```

- The **stable host** runs MongoDB + MinIO. It can itself be a spare desktop or
  a tiny cloud VM — it just needs to stay reachable. (See
  [deployment.md](deployment.md) for binding it to `0.0.0.0` and the env
  contract; [join-fleet-from-new-server.md](join-fleet-from-new-server.md) for
  the join steps.)
- Everyone else's machine is a **runner**. Alice's laptop contributes during the
  workday and drains cleanly when she shuts it; the lab desktop is always-on and
  picks up the slack; Carol's joins in the evening. The fleet's capacity simply
  rises and falls with how many machines are currently on.
- You submit a fan-out workflow (e.g. a 50-region build) and it spreads across
  whatever machines happen to be up, finishing faster the more compute is
  online — with no machine being special.

This turns *idle compute you already own* into a parallel data-processing
cluster, for the cost of running two services.

## Who this is for

This model is a strong fit for:

- **Small teams, research labs, and students** with spare desktops/lab machines
  and heavy but bursty data work (geospatial pipelines, genomics fan-outs,
  climate/census crunching, batch LLM jobs).
- **Side projects and internal tooling** where standing up real cluster infra
  would cost more than the work itself.
- **"I have five computers and a big job"** situations — exactly the case the
  runtime's resilience (atomic claim, reaper, retries, durable MinIO output) was
  designed to make painless.

What you need is modest: one stable-ish host for Mongo + MinIO, network
reachability from the runner machines, and the runner image (or a venv) on each.

## Where it stops: not large-scale production

Be honest about the ceiling. This is a deliberately simple, config-driven fleet
(`fleet set` → every server's `fleet-agent` reconciles). It is **not** a
production orchestration platform, and deploying it as one would hurt. It lacks,
by design:

- per-host **autoscaling** and **capacity-/locality-aware scheduling**;
- **health-gated / canary / staged rollouts** with automatic rollback on error
  budgets (a `fleet set --image` is a blunt fleet-wide recreate);
- **multi-tenancy / isolation / quotas**, RBAC, audited change control;
- **SLOs, alerting, and on-call-grade observability**.

For that scale you'd reach for Kubernetes, Temporal, a real scheduler, etc. The
full, candid comparison — what maps directly to a Big-Tech autodeploy pipeline
and what it deliberately omits — is in
[fleet-rollouts.md §6](fleet-rollouts.md). The sweet spot here is **small,
trusted teams turning the machines they already have into a resilient batch
fleet** — not multi-team, SLO-bound production at thousands of nodes.

### Could it run in a real data center, at large scale?

**Possibly — the architecture doesn't forbid it.** The fundamentals it's built
on (leaderless, pull-based atomic claiming; stateless, disposable runners; a
durable object store with portable URIs; at-least-once recovery) are the *same*
fundamentals large-scale data-processing systems use, and they scale
horizontally: a shared Mongo + MinIO (each itself a cluster / managed service)
fronting hundreds of runner nodes is structurally the same picture as the
laptop fleet above, just bigger.

**But this has not been tested at data-center scale.** All current testing is at
small-team / research scale (a handful of machines, thousands of tasks). Nothing
here has been exercised against the failure modes, contention, and operational
load of a large production cluster.

So if you're considering large-scale, data-center data processing on this,
treat it as a **real engineering investment, not a config change** — budget the
time to validate it at scale, and plan to add the capabilities that running a
large-scale data-processing operation actually requires (and that this does not
yet provide), for example:

- Mongo/MinIO sized and tuned as the coordination + storage tier under heavy
  concurrent claim/finalize load (sharding, replica sets, connection limits,
  the claim index's contention behavior at scale).
- per-host **autoscaling** and **capacity-/locality-aware scheduling**;
- **health-gated / canary / staged rollouts** with automated, metric-based
  rollback;
- **multi-tenancy, isolation, quotas**, RBAC, and audited change control;
- **SLOs, alerting, and on-call-grade observability** across the fleet.

In short: large scale is a plausible *destination*, but reaching it means
investing the same hardening, scheduling, and operational machinery that mature
large-scale data-processing deployments are built from — not assuming the
small-team fleet will simply stretch to fill a data center untouched.

## See also

- [deployment.md](deployment.md) — shared Mongo + MinIO, the env contract.
- [join-fleet-from-new-server.md](join-fleet-from-new-server.md) — add a machine.
- [fleet-rollouts.md](fleet-rollouts.md) — rollouts, runner lifecycle, the
  honest production-scale comparison.
- [multi-language-handlers.md](../guides/multi-language-handlers.md) — any of
  these machines can also run a Java/Go/TS handler.

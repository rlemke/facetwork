# How our deployment compares to Hadoop, Spark and Kubernetes

An honest assessment of Facetwork's install/setup/deploy machinery against
platforms built for the same *shape* of problem (distribute work across many
machines) at a different *scale* of assurance.

Written 2026-09-18 from measured facts about this fleet, not impressions. Where a
claim is a measurement, it says so.

---

## 1. Framing: not the same problem

Before any comparison is useful, the difference in target has to be stated, or
every gap reads as a defect:

| | Hadoop / Spark / Kubernetes | Facetwork here |
|---|---|---|
| tenancy | **multi-tenant**, mutually untrusting users | single tenant, one operator |
| trust boundary | inside the cluster | at the LAN edge |
| scale | 100s–10,000s of nodes | **8 hosts, 113 runners** |
| operators | a platform team | one person, part-time |
| hardening | 10–20 years, adversarial | 2 years, cooperative |

Facetwork is explicitly an *informal fleet* — machines you own, lent to a cluster,
stateless and disposable. Several things below are missing *because that model
does not need them*, and several are missing because nobody has done them yet.
This document tries hard to keep those two categories apart.

---

## 2. Where the architecture is genuinely modern

These are not catching-up items; they are the same patterns the mature systems
converged on, and in two cases we are ahead of classic Hadoop.

### Desired state in a database + a per-host reconcile loop

`fleet_config` in MongoDB is the single source of desired state; a `fleet-agent`
on each host polls it every 30 s and reconciles local containers to match. That is
structurally **kubelet**, and it is strictly better than classic Hadoop's model of
pushing `core-site.xml` / `hdfs-site.xml` / `yarn-site.xml` to every node and
restarting services — the approach that made Ambari and Cloudera Manager
necessary products rather than conveniences.

### Leaderless by construction

There is no NameNode, no ResourceManager, no scheduler process. Every runner is
identical and stateless; coordination is a single atomic
`find_one_and_update` on a Mongo collection. Hadoop needed ZooKeeper, a standby
master and JournalNodes to retrofit HA onto a design with a singleton master.
We have no master to lose. (The cost is paid elsewhere — see §3.4.)

### Task-queue polling with capability filters

Runners claim only work they can run, filtered server-side on facet name,
`environment_hash`, `required_features` and a resource floor. This is the
**Temporal** worker model, which is the most mature version of this pattern, and
it is a better fit for heterogeneous hardware than YARN's uniform-container view.

### Image-based deploys through a registry

`fw fleet rollout` builds a multi-arch image tagged with the git SHA, pushes it,
flips `fleet_config`, and waits for every host to converge. Domains are **baked
in**, so "deploy" means "change the image tag" — the same immutable-artifact
discipline as a Kubernetes Deployment, and notably stricter than Airflow's usual
DAG distribution by shared volume or `git-sync`.

### Drain before stop

`fw runner drain` resets in-flight tasks to `pending` with an audit log entry
before shutdown — the equivalent of `kubectl drain` / YARN's graceful
decommission. Many small systems skip this entirely.

---

## 3. Where we are behind, ordered by consequence

### 3.1 ⚠️ The control plane is unauthenticated — the largest gap by far

Measured 2026-09-18:

```
mongodb://<host>:27017   connectionStatus -> authenticatedUsers: []
MinIO                    minioadmin / minioadmin   (documented default)
image registry           plain HTTP, via docker insecure-registries, no auth
between components       no TLS anywhere
```

Anyone who can reach the LAN can read every workflow, rewrite `fleet_config`, and
therefore **choose the image that every host in the fleet will run**. That is
remote code execution on eight machines, by design, gated only on network
position.

The comparison is stark. Hadoop ships Kerberos for exactly this and the
"simple auth" mode we are effectively in is widely treated as
development-only. Kubernetes has RBAC, service accounts and mTLS between control
plane and kubelets. Temporal has TLS plus namespace authorization.

**What it would cost:** Mongo auth with per-role users is an afternoon plus a
careful credential rollout (every runner, every `fw` invocation, the dashboard,
the MCP server). MinIO credentials likewise. Registry auth needs `htpasswd` plus
TLS, or the registry moved behind something that terminates it. None of it is
research work; it is a day or two of unglamorous plumbing. The reason to do it is
not compliance — it is that **the blast radius of a mistake is currently the whole
fleet**, and we have already proved a single bad write to shared state can take it
from 108 runners to 23.

⚠️ Do not read "it is on a trusted LAN" as a mitigation without also noting what
that assumes: every device on that LAN, including a guest phone and anything with
a browser reaching `:27017`, is inside the trust boundary.

### 3.2 No health gating and no rollback on deploy

`fw fleet rollout` has **zero** mentions of rollback (measured). It flips the
config, waits for convergence, and reports. If the new image is broken, the fleet
converges *onto the broken image* and stays there until a human builds another.

What the mature systems do instead:

- **Kubernetes** Deployments: `maxUnavailable`/`maxSurge`, readiness probes gate
  whether a new pod counts as available, and `kubectl rollout undo` restores the
  previous ReplicaSet.
- **Nomad**: canary deployments with automatic revert on failed health checks.
- **Hadoop/Spark rolling upgrades**: staged, with explicit version compatibility
  rules.

We have the ingredients — staggered pre-pull already serialises the risky step,
and the previous image tag is recorded — but nothing closes the loop. **The
cheapest useful version:** after converge, if the fraction of live runners drops
below a threshold, flip `fleet_config` back to the prior tag automatically and say
so loudly. That single addition converts our worst deploy outcome from "the fleet
is down until someone notices" into "the deploy failed and reverted".

Related: there is **no readiness concept** at all. A runner registers, and that is
taken as ready. `/healthz` exists only in the map server, not in runners. This is
why image uniformity had to be added as a separate check — the fleet view could
report "23/23 runners" about 23 containers running last week's code.

### 3.3 No metrics pipeline

Measured: **no `prometheus_client`, no `/metrics` endpoint** anywhere in the
runtime. Observability is structured logs, the Mongo collections, a dashboard, and
ad-hoc queries.

Everything comparable exposes numbers as a first-class interface: Hadoop and Spark
publish Dropwizard/JMX metrics, Kubernetes has cAdvisor and kube-state-metrics,
Temporal ships Prometheus endpoints. The practical consequence for us is already
documented in `fw maint workflow-stats`: **peak memory has to be parsed out of
handler log text** where a handler chose to log it, because nothing samples RSS.
We report advertised capacity and say plainly that usage is not measured — which
is honest, and also a confession that the data does not exist.

This is the gap that most limits the *next* thing: capacity planning, autoscaling
and regression detection all need a time series we do not collect.

### 3.4 Scheduling is a floor, not a scheduler

Documented already and worth repeating in this context: a task may carry a
resource floor and a runner advertises measured capacity, but **advertised
capacity is static per process and ignores what the runner is already doing**. It
prevents catastrophic placement; it does not pack work.

YARN and Kubernetes both do real bin-packing against requests and limits, with
preemption and queues. This is the price of having no scheduler process — the
leaderless design in §2 buys simplicity and gives up global placement decisions.
It is a legitimate trade at 8 hosts and would not be at 800.

### 3.5 Desired state is mutable state, not a versioned spec

`fleet_config` is edited by CLI (`fw fleet set`) and lives only in Mongo. There is
a version counter, but no history you can diff, no review, and no way to say "make
the cluster match this file". Kubernetes' entire operational culture — YAML in
git, reviewed, applied, GitOps — exists because that property is what makes a
cluster's state auditable and reproducible.

We have `servers.json` and `domains.json` in git, which is the right instinct;
`fleet_config` is the piece that escaped it. **The gap is felt as:** nobody can
answer "what changed between v217 and v218?" from the repo.

### 3.6 Secrets have no lifecycle

Per-host `~/.facetwork/fleet-secrets.env` plus encrypted documents in Mongo. No
rotation mechanism exists (measured: no match for `rotat*` in the secret tooling),
no expiry, no audit of use. Vault, KMS and sealed-secrets all exist because
long-lived plaintext credentials on disk is the failure mode everyone eventually
has. Ours are also, per §3.1, sitting next to an unauthenticated database.

### 3.7 Provisioning has no inventory or drift reporting

Our provisioning is one idempotent, non-interactive bash script over ssh — which
is the *property* that matters (see
[zero-sudo-operations.md](zero-sudo-operations.md)), and it is genuinely better
than a wiki page of steps. What it lacks is everything around it: no inventory of
hosts to converge, no report of which hosts are at which state, no scheduled
re-convergence. Ansible/Salt/Puppet give that for free, and cloud-init plus an
image gives it at the next tier up.

The cost showed up concretely this week: a `/etc/hosts` cleanup was applied to
*live* hosts, so the two machines that were parked at that moment kept their stale
configuration and carried it back when they rejoined. A converging inventory
cannot make that mistake.

---

## 4. Where the comparison flatters us

Worth stating, because a list of gaps invites the wrong conclusion:

- **Component count.** A Hadoop cluster is NameNode + DataNodes + ResourceManager
  + NodeManagers + JournalNodes + ZooKeeper, each with its own configuration and
  failure modes. Ours is: one agent per host, N identical runners, Mongo, MinIO.
  A new machine joins with one script and starts claiming work.
- **No cluster-wide restart to change configuration.** The reconcile loop means a
  config change propagates in ~30 s without touching any host.
- **Disposable workers are real, not aspirational.** A host can vanish mid-task
  and the reaper re-queues its work. Many small deployments claim this and have
  never tested it; we have measured it repeatedly, including a 7.4-hour
  hibernation mid-fan-out.
- **Honest capability routing.** A runner that cannot run something does not claim
  it. As of v218 that extends to whole-host admission: a machine whose core stack
  cannot load advertises only the handler-free facets rather than 265 it would
  fail. YARN has no equivalent notion of "this node cannot run this class of work
  for a *software* reason".

---

## 5. What I would actually do, in order

Ranked by consequence-per-effort, not by how interesting it is:

1. **Authenticate Mongo and MinIO** (§3.1). Biggest blast-radius reduction
   available, and it is plumbing rather than design.
2. **Automatic revert on a failed rollout** (§3.2). Converts the worst deploy
   outcome from an outage into a failed deploy.
3. **Put `fleet_config` in git and apply it** (§3.5) — even as a file the CLI
   reads and records. Makes cluster change reviewable.
4. **A `/metrics` endpoint per runner** (§3.3). Unblocks capacity planning and
   regression detection; everything else in observability follows.
5. **A converging inventory sweep** (§3.7) — even a loop over the catalog that
   runs the provisioning script in check mode and reports drift.
6. Secret rotation (§3.6), then readiness probes (§3.2).

Explicitly **not** on this list: a real scheduler (§3.4), multi-tenancy, mTLS
between runners. Those are correct for a platform serving untrusting users and
would be substantial engineering for no benefit at eight hosts. The measure of
whether this system is well-built is not how much of Kubernetes it reimplements —
it is whether the properties it *claims* are the ones it actually has.

# Research papers

Companions to [`thesis.md`](thesis.md). Each is a standalone write-up of one
finding, grounded in this fleet's operating record rather than in simulation —
the empirical basis is stated in the preamble of every paper.

They are listed here because nine papers with no index is how a paper stops
being read.

## Experience reports

Long-form accounts of operating the system, with incident catalogues.

| Paper | What it argues |
|---|---|
| [An Informal Fleet](paper-informal-fleet.md) | Consumer desktops are a viable execution tier for small teams *if* runners are stateless, claims are atomic, and routing is derived. Quantifies a 72-hour window absorbing nine rollouts. |
| [Moving State](paper-moving-state.md) | Statelessness ends at the disk. Relocating a database and 298 GB of bulk data across a fleet; storage-identity failures, and **seven probes that reported health while the system was broken**. |
| [Green Is Not Done](paper-completion-and-portability.md) | Nine silent failures between "the run succeeded" and "the data is complete", plus the capacity recovered by multi-architecture images. |

## Coordination and routing

| Paper | What it argues |
|---|---|
| [Intrinsic Facts as Routing Keys](paper-intrinsic-routing.md) | Derive routing from facts the system already holds rather than configuring it — and two bugs from the boundary where derivation stops. |
| [Ordering, Not Duration](paper-timeout-interactions.md) | Timeout *interaction* hazards in leaderless claiming: the relative order of expiries matters more than any individual value. |
| [The Safety Net With a Hole](paper-liveness-coverage-drift.md) | Self-healing mechanisms drift out of coverage with the failure states they were built for, and the gap is invisible while everything is green. |

## Testing and correctness

| Paper | What it argues |
|---|---|
| [Passing Every Test While Completely Broken](paper-parity-gaps.md) | Single-address-space test doubles structurally cannot expose distributed coordination bugs — a whole class of defect passes every test by construction. |

## Data substrate

| Paper | What it argues |
|---|---|
| [Become Your Own Geofabrik](paper-geofabrik-replacement.md) | Self-hosting OSM regional extracts as a fan-out-native substrate, and what it costs to own the data path. |
| [Resolve Once, Route Always](paper-environment-provisioning.md) | Content-addressed script environments versus per-job dependency resolution. ⚠️ Reports our own lazy tier *losing* on the metric it was built for. |

## Composition

| Paper | What it argues |
|---|---|
| [Lookup, Don't Generate](paper-llm-composition.md) | A capability catalog as the substrate for LLM workflow composition — turning authoring into lookup-then-compose. |

---

## Conventions

- **Empirical basis is stated up front.** If a number came from one run on one
  fleet, the paper says so. Several papers include a *Limits* section that
  names what would not generalise.
- **Negative results are kept.** `paper-environment-provisioning.md` reports a
  design of ours performing worse than the alternative; that is the point of
  writing them down.
- **Papers are historical records.** They describe the system at the time of
  writing and are not retrofitted when the system changes — a paper that
  silently tracks the present cannot be cited. Where a later paper supersedes an
  earlier claim, it says so explicitly and the earlier text stands.

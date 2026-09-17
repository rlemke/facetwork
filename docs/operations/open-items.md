# Open items

What is outstanding, why it matters, and what the fix looks like. Kept here
because the architectural roadmap in
[lessons-learned.md](../architecture/lessons-learned.md) tracks *design* work,
and most of what accumulates in practice is not design work.

Ordered by consequence, not effort. Last reviewed **2026-09-16**.

---

## 1. Known defects with no fix yet

### 1.1 No co-location primitive — steps that must share a host cannot say so

`ExtractRegions` writes local files and `PublishExtracts` uploads them. No value
flows that the compiler can see — only a path string, which means nothing on
another host. An unpinned publish step was claimed by a host with an empty
output directory, uploaded nothing, and **reported success**: the us-states tier
came back **36% refreshed with every step green**.

Two repairs shipped, neither of which is the real fix: the handler now fails
when it publishes none of what it was asked for (`bace3c5`), and the call site
carries a matching `Requires` pin so both steps route identically (`c74ed50`).
The pin only works because exactly one host holds the dataset; it is not a
co-location primitive and does not generalise.

**The sound fix** is the one this codebase already uses elsewhere:
`BuildAdminSet` does extract-and-publish in **one task**, explicitly "so there's
no cross-host local-file handoff". The planet workflows should do the same.

### 1.2 Capability advertisement never imports the handler — ✅ FIXED 2026-09-16

`registration_module_available()` used `importlib.util.find_spec`, which only
**locates** a module and never executes it — so a module whose first line was an
impossible import passed, and the function did not test the thing its docstring
promised. That is how macmini01 advertised **265 facets** it could not execute.

Now two stages: `find_spec` as a cheap pre-filter (cannot be located → cannot be
imported, decided without executing anything), then a **real import, cached per
image tag on this host**. A generalist runner carries ~265 registrations, so the
import pass is paid once per image per host rather than on every start. Failures
are cached too — an ImportError under a given image on a given host is a stable
fact.

⚠️ The cache is keyed by image tag AND is host-local, and both halves matter:
the premise of the whole check is that the *same image* behaves differently on
different CPUs, so a cache shared across hosts would be actively wrong. A file
on the host is implicitly host-scoped; the tag stops a rollout inheriting the
previous image's answers.

Eight tests, verified to fail against the `find_spec`-only version.

⚠️ Residual: `HandlerRegistration.requirements` is still unusable — **1 of 700**
rows populates it and that row is corrupt (a string iterated character-wise, so
its entries are `[` and `]`). Nothing depends on it; worth either populating or
removing.

### 1.3 Nothing rewrites absolute paths after a migration

`regions.json` transferred byte-for-byte and broke the nightly re-split, because
it hardcodes absolute poly paths from the host that generated it. **A faithful
copy was exactly the wrong outcome.** Found only because `osm-watchdog` alarms
on a stale re-split.

**Partly addressed** by `fw maint path-check` — it flags absolute paths that do
not resolve on this host, paths impossible for the platform (`/Volumes` on
Linux), and anything under a `--stale-prefix`. Verified against the original
`regions.json`: it catches all 8 bad paths in seconds, with no knowledge of the
migration.

⚠️ **Still open: nothing rewrites them, and nothing distinguishes live config
from a historical artifact.** The first fleet-wide run returned 44 findings, all
true and all artifacts (a pre-move `extract-config.json`, a July scratch
`cfg.json`). Judging severity still needs a human reading mtimes. The checker
should be run as part of a migration, where "this file arrived today and does
not resolve" is unambiguous.

### 1.4 53 country extracts are permanently stale

Measured twice from opposite directions. Every stale region is also
structurally unreproducible at `admin_level=2` — under a `country_prefix`,
levels ≤ 4 are subdivisions and must carry ISO 3166-2, so countries are dropped.
**RefreshChain cannot keep the country tier current**; do not add compute
expecting it to. Open question: are they reproducible from a *country* source at
`admin_level=4` (`BuildAdminFanout`) rather than a continent source at 2? That is
a keying-rule change, not a parameter.

---

## 2. Deployment and hygiene

| Item | Detail |
|---|---|
| **server3 `bucket-tier` (94 GB)** | Duplicate of what beelink01 holds since the OSM move. `www` was deleted 2026-09-16; this is the remainder. Verify beelink01's copy, then remove. |
| **`country_width: 4` not live** | Committed (`7b88e9d`) but the FFL is baked into the image, so it needs a rollout. Will show its effect on the first run with real work — a run with nothing to rebuild leaves the slots idle regardless. |
| **Four installers still launchd-only** | `maps-install`, `selfcheck`, `stocks-snapshot`, and `osm-extracts`' native (non-container) path. None block anything today; the fleet is majority Linux, so they are installable on 2 hosts of 7. `_timer.sh` makes each a small change. |
| **server3 selfhost leftovers** | A 1 MB stand-in `master.osm.pbf` and assorted logs in `~/.facetwork/osm-selfhost/`, now that the role has moved. |

---

## 3. Parked

### `fw:sys` control channel

Runner lifecycle control (pause/resume/status) was built on tasks carrying
`step_id: ""`. That sentinel collided with a partial unique index, and widening
the index live took the fleet from ~108 runners to 23.

The control-plane separation is right — `resume` must reach a runner that is by
definition not claiming workflow work, and control must not depend on the data
plane it may need to repair. The **encoding** was wrong. Redo it against its own
`fw_control` collection rather than borrowing `tasks`, and keep the split:
lifecycle control outside workflows, administrative *work* (cache cleaning,
diagnostics) as ordinary FFL with a per-host routing dimension.

---

## 4. Queued ideas

**NEVI corridor-gap analysis.** Not "rest stops with charging" — that is well
served by PlugShare/ABRP and goes stale fast. The underserved question is *where
the interstate network fails the federal 50-mile standard, and how many rest
areas sit inside those gaps* — unusable for the purpose because 23 U.S.C. § 111
has banned commercial activity at interstate rest areas since 1956. Measurable
against a published requirement, which suits `fw.compare`. Use AFDC for chargers
(OSM's charger coverage lags), OSM for rest areas and roads, FHWA for corridors.

**CERN/ROOT delegation.** Fits the D3 delegation pattern; start at "rung 1" — one
machine, `service: none`, local checkout, as `peloton` does — not fleet
deployment. The compelling target is the CMS dimuon spectrum compared against
ROOT's own tutorial output and PDG masses: a re-analysis that reports *how
closely* it reproduced a published result.

---

## 5. Hardware / environment

| Item | Status |
|---|---|
| server3 link | ✅ **Resolved 2026-09-16** — cable. 34–55% loss under load → 0%; 124 → 261 MB/s, old switch kept. |
| macmini01 | Dropped from the fleet (2009 CPU, no SSE4.2/POPCNT). Repurposed as the **backup archive host** — a role needing only sshd and disk. |
| atopnuc02 | In the catalog, `joined: false`, never provisioned. |

# What a change costs — PyPSA-Eur's retrieve layer, measured against its own history

[`fwh_pypsa_data`](https://github.com/rlemke/fwh_pypsa_data) ports PyPSA-Eur's
`rules/retrieve.smk` — 1,631 lines and 73 rules — to a single `foreach` over
`data/versions.csv`. The collapse ratio is the eye-catching number, and it is
also the least interesting one: nobody rewrites a retrieval layer, they *change*
it. So the question this experiment asks is the one that recurs:

> When a dataset is added, updated or moved, what does it cost in each system?

Both halves are measured rather than argued. The upstream half comes from the
23 commits in PyPSA-Eur's history that touch `data/versions.csv`; the port's
half comes from adding a dataset and running it.

## Method

```bash
gh api "repos/PyPSA/pypsa-eur/commits?path=data/versions.csv&per_page=100"
# then, per commit, the additions/deletions in versions.csv, retrieve.smk and
# config.default.yaml, and what the added rule body actually does
```

Line counts are upstream's own diffs, not a reconstruction.

## Upstream: it depends entirely on the kind of change

| kind of change | commits | `retrieve.smk` |
|---|---|---|
| data only — new URL, new version, moved mirror | 5 | **untouched** |
| config + data | 4 | untouched |
| needed rule code | 14 | +1 … +784 lines, median **+13** |

The +784 outlier is `151f6038`, which *introduced* the versions.csv layer — it
is the infrastructure commit, not a dataset change, and is excluded from the
median above.

The first row is the finding that matters most, because it is the one that
cuts against the port: **upstream is already data-driven for the common case.**
Bumping `technology-data` to v0.13.4 (`1e3e24b5`), fixing the SCIGRID gas
archive URL (`666fdf17`), fixing the WDPA links for Windows (`547e8e81`) — all
of these are edits to `versions.csv` and nothing else. The FFL port offers no
advantage here whatsoever. Any claim that it makes *change* cheap in general
would be false, and the history says so plainly.

## Where the 73 rules actually cost something

Of the 14 commits that did need rule code, what the added code *does* separates
them cleanly:

| what the added rule body does | commits | in the FFL port |
|---|---|---|
| a plain `storage(...)` + `copy2` rule for a new dataset | 3 | **0 lines** — add the CSV row |
| a bespoke request: `requests.get(params=…)`, `entsoe-py`, unpacking | 5 | **also needs code** |
| edits to existing rules / one-line touches | 6 | varies |

So the collapse pays off in exactly one place — a *new plain download* — where
upstream writes a rule (median +13 lines, e.g. `1fa9a4fb`, `5664e141`,
`5352136d` each adding one `storage()`+`copy2` rule) and the port writes
nothing. It does not pay off when the new dataset needs a request shape the CSV
has no column for, and there the port is currently *worse*: it cannot express
one at all, and its guard falls back to the archive rather than fetching the
wrong thing (see the domain README).

## The port's half: adding a dataset, measured

A row was appended to a copy of upstream's catalogue —

```csv
demo_new_dataset,v1.0,primary,latest supported,2026-08-16,…,https://…/versions.csv
```

— and run with no other change:

```bash
fw ffl run …/pypsa_data.ffl --workflow pypsa.data.RetrieveDatasets \
  --inputs '{"csv_path": "versions.plus.csv", "names": ["demo_new_dataset"], …}'
```

Result: `state: completed`, tasks `fw:execute` → `Catalog` → `fw.http.Fetch` →
`fw.file.WriteJson`, the file in the object store with its provenance sidecar,
and `git status` in the port reporting **0 files changed**. The fan-out picked
up a dataset that did not exist when the workflow was written, because the work
list is read at run time rather than compiled into rules.

## What this supports, and what it does not

**Supported.** For a new plain download, the port's marginal cost is zero and
upstream's is a rule — which is the same static-outputs property §13 describes,
now priced from upstream's own commits rather than asserted.

**Not supported.** "Change is cheaper in FFL" as a general claim. Five of 23
catalogue commits cost upstream nothing either, and five needed request logic
that costs the port at least as much. The honest summary is narrower and more
useful: *the rule-per-output tax is real but it is charged only on new
downloads, and a workflow whose work list is already a table has largely paid it
off already.*

The deeper point is the same one §13.3 makes about staleness: what a system can
express as data, it can change as data. PyPSA-Eur got there first for URLs and
versions, and its remaining 73 rules are what is left over — the fetches whose
*shape*, not whose address, varies.

## The prediction, tested on a different workflow

The table above says the collapse pays off for a *new plain download* and not
for a rule whose body is a bespoke script. That was a claim about shape, made
from one repository's history, so it was worth testing where it predicts a
*negative* result.

[`fwh_pypsa_earth`](https://github.com/rlemke/fwh_pypsa_earth) ports PyPSA-Earth's
OSM stage — `build_shapes` → `download_osm_data` → `clean_osm_data` →
`build_osm_network`, three rules of about sixty. Every one is `script:`-shaped.

| | `fwh_pypsa_data` (retrieve.smk) | `fwh_pypsa_earth` (OSM stage) |
|---|---|---|
| upstream rules ported | 73 | 3 |
| what the rules are | near-copies of one shape | three distinct algorithms |
| upstream Python behind them | none (a CSV table) | 2,168 lines |
| result | **73 rules → one `foreach`** | **~1 facet per rule** |

So the prediction holds in both directions: where the work list was already a
table, the rules collapsed; where the rule body is an algorithm, nothing
collapses and the port is a wrapper. The FFL gain in the second case is
different in kind — durable steps, a per-country fan-out across a fleet, local
sourcing — and explicitly **not** brevity.

Two things the port added that the pricing exercise could not have found:

- **Wrapping is only possible if the code is callable, and theirs is not.**
  `clean_data` and `built_network` look like path-in/path-out functions and are
  not: two of their helpers read module-level globals that only `__main__`
  assigns, and one indexes `inputs` as an attribute. All three are invisible
  under Snakemake, where the parameter and the global are the same object. A
  *delegation* would have run first time and taught none of this.
- **The dependency tax is paid in full regardless.** Importing three scripts
  pulled ~178 MiB (pypsa and 27 deps, then sklearn, fiona, numba/llvmlite),
  most of it for code these rules never execute. Delegation would pay the same,
  because it needs the same environment.

## Appendix: killing a runner mid-fan-out, twice

§13's interruption row was measured on the state-capitals workflow, which I
wrote. Repeating it here needed no new data: the six datasets are already in the
object store, so a probe workflow with `max_age_hours = 0` forces a conditional
GET per dataset — real network round-trips, 304 responses, no bodies. Eight
fetches, ~0.4s each, 4.5s serialised at `limit 1`. Long enough to interrupt.

The first attempt tested something other than what it set out to.

**Run 1 — one of seventeen.** `SIGKILL` to a runner mid-fan-out, and the
workflow finished **1.5 seconds later**. Not durability: `fw.http.Fetch` is an
ambient built-in, so all 16 fleet containers could serve it, and seven distinct
servers claimed tasks during this single four-second run. The peers simply took
the rest. `retry_count` was **0 on all 11 tasks** — nothing was reclaimed, the
reaper never ran, no work repeated. That is the informal-fleet property rather
than the durability one, and it is worth recording as its own result: with
interchangeable participants, losing one mid-run is a non-event, and the
distribution across seven of them was the outcome of racing claims, not of a
scheduler.

**Run 2 — the last one standing.** To test what was intended, this host left the
cluster (`fw mode leave`, 16 containers stopped) so exactly one runner could
serve the work:

| | |
|---|---|
| `SIGKILL` | 22:35:18.9, with 4 tasks complete and **1 `fw.http.Fetch` orphaned in `running`** on the dead server |
| live runners after the kill | **0** |
| replacement process up | 22:36:03 (a different `server_id`) |
| orphan reaper fires | 22:38:03 — *"reset 1 task(s) from crashed server(s)"* |
| orphaned task re-claimed | 22:38:05, `retry_count` 1 |
| run complete | 22:38:08 — **169s after the kill** |

Two details matter more than the total. First, the four completed tasks **did
not re-run**: they keep the dead server's id and `retry_count` 0, and their
timestamps never move. Exactly the remaining work was resumed, on a workflow
this thesis did not write. Second, the replacement runner was alive for two
minutes before anything happened, and *should* have been: it cannot distinguish
a crashed owner from a slow one, so it waits out `FW_REAPER_TIMEOUT_MS` (120s)
rather than reclaiming on sight. The 169s is that safety interval, not latency
to be optimised away — the alternative is running a task twice.

No repair command was needed in either run.

## Reproducing

```bash
# upstream side (needs gh auth)
gh api "repos/PyPSA/pypsa-eur/commits?path=data/versions.csv&per_page=100" --jq '.[].sha'
gh api repos/PyPSA/pypsa-eur/commits/<sha> \
  --jq '.files[] | select(.filename|test("versions.csv|retrieve.smk|config.default.yaml"))
        | "\(.filename) +\(.additions)/-\(.deletions)"'

# port side
cp versions.csv versions.plus.csv && echo '<new row>' >> versions.plus.csv
fw runner start --domain pypsa-data --no-dashboard
fw ffl run src/pypsa_data/ffl/pypsa_data.ffl --workflow pypsa.data.RetrieveDatasets \
  --inputs '{"csv_path":"versions.plus.csv","out_dir":"…","names":["demo_new_dataset"]}'
git status --short   # expect: empty

# interruption (appendix). `interrupt_probe.ffl` here is the max_age_hours=0
# variant; leaving the cluster is what makes the second run test the reaper
# rather than the fleet's redundancy.
fw mode leave
fw ffl run --primary interrupt_probe.ffl --library …/pypsa_data.ffl \
  --workflow probe.RevalidateAll --inputs '{…,"concurrency":1}'
sleep 2 && kill -9 $(pgrep -f facetwork.runtime.runner | head -1)
fw runner start --domain pypsa-data --no-dashboard    # a different process
fw mode join
```

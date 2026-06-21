# The domain/example catalog — `domains.json`

`domains.json` (repo root) is the **single source of truth** for the set of
pluggable **domains** (the `fwh_*` production pipelines) and in-repo **examples**
(teaching demos), plus each domain's attributes. It replaces the lists that used
to be hardcoded in `scripts/lib/install/domain`, `facetwork/domains/migrate.py`,
and `docker-compose.full-stack.yml`.

A deployment customizes the entire domain footprint — which packages, their
repos/extras/task-lists, which run scaled vs single, replica counts — by editing
**one JSON file**, with **no changes to any `fw` command**.

---

## 1. Who reads the catalog

| Consumer | Uses |
|----------|------|
| `fw install domain` (`--list` / `--all` / lookup) | `repo`, `extras`, `description` |
| `fw fleet … migrate-seed-prefix` (`migrate.py`) | the domain names |
| `fw util gen-compose` | `service`, `task_list`, `repo`, `compose_extras`/`extras_var`, `server_group_arg` → writes the per-domain env into `docker-compose.full-stack.yml` |
| `fw runner start` (default `--fleet`/`--docker` set) | `fleet_default` + `service` |
| `fw runner start-all` (replica tiers) | `scaled` + `service`, `defaults.replicas`, `defaults.scaled_replicas` |
| `fw single rebuild-runners` | `service` (all compose domains) |
| `fw fleet set --domain-runner` | validates the name against `service` suffixes |

Everything goes through `facetwork/domains/catalog.py` (Python) and
`scripts/lib/_helpers/_catalog.sh` (bash shim), so shell and Python consumers see
the **same** resolved catalog.

---

## 2. File resolution & per-deployment override

The effective catalog is resolved in this order:

1. **`$AFL_DOMAINS_FILE`** — if set, that file *is* the catalog (full replace).
2. **`domains.local.json`** (repo root, gitignored) — merged *over* `domains.json`.
3. **`domains.json`** — the committed standard catalog.

**Merge semantics** (option 2): entries in the overlay's `domains`/`examples`
**add or replace** the base entry *by key* (a replaced entry is taken wholesale,
not deep-merged); a top-level **`"_remove": ["name", …]`** drops standard
entries. Other top-level keys (e.g. `defaults`) in the overlay replace the base's.

Inspect what's in effect:

```bash
python -c 'from facetwork.domains.catalog import catalog_source; print(catalog_source())'
fw install domain --list          # the domains currently in the catalog
```

---

## 3. Top-level structure

```jsonc
{
  "version": 1,
  "defaults": { "replicas": 1, "scaled_replicas": 3 },
  "domains":  { "<name>": { … } },
  "examples": { "<name>": { … } }
}
```

### `defaults` (optional)

| Key | Default | Meaning |
|-----|---------|---------|
| `replicas` | `1` | Replica count for a single-replica (non-`scaled`) domain runner. |
| `scaled_replicas` | falls back to `replicas` | Replica count for the `scaled` tier. The env `AFL_OSM_REPLICAS` still **overrides** this per host. |

---

## 4. Domain entry — field reference

Keyed by the domain **short-name** (e.g. `osm-geocoder`). Only `repo` is strictly
required for an installable domain; everything else is optional and unlocks a
capability.

| Field | Type | Required | Used by | Meaning |
|-------|------|----------|---------|---------|
| `repo` | string | **yes** | install, gen-compose | The `fwh_*` GitHub repo / clone dir name. |
| `extras` | string[] | no (default `[]`) | install | pip extras added on `fw install domain` unless `--no-default-extras`. |
| `description` | string | no | install `--list` | One-line blurb. |
| `service` | string | no | gen-compose, runner/start, rebuild, fleet | The compose service (`runner-<x>`). **Presence makes it a "compose domain"** (it has a runner service). Absent → install-only (e.g. `save-earth`, `sentinel2-landchange`). The `--domain` suffix is `service` minus `runner-` (so `jenkins` → `runner-jenkins-example` → suffix `jenkins-example`). |
| `task_list` | string | no¹ | gen-compose | Emitted as `AFL_REGISTRY_RUNNER_ARGS: "--task-list <task_list>"`. |
| `compose_extras` | string | no | gen-compose | Default value for the compose `AFL_DOMAIN_EXTRAS` env (distinct from install `extras`). |
| `extras_var` | string | no | gen-compose | If set, `AFL_DOMAIN_EXTRAS` becomes `${<extras_var>:-<compose_extras>}` (env-overridable). |
| `server_group_arg` | bool | no | gen-compose | Append ` --server-group ${AFL_SERVER_GROUP:-default}` to the runner args (used by osm). |
| `fleet_default` | bool | no | runner/start | In the default set a bare `fw runner start --fleet`/fleet-agent brings up. |
| `scaled` | bool | no | start-all | Runs at the throughput knob (`scaled_replicas` / `AFL_OSM_REPLICAS`) instead of one replica. |

¹ Required only for a compose domain (one with a `service`).

### Example entry (compose domain)

```jsonc
"osm-geocoder": {
  "repo": "fwh_osm",
  "extras": [],
  "description": "OSM PBF → PostGIS → routing/tiles (production-scale)",
  "service": "runner-osm-geocoder",
  "task_list": "osm",
  "server_group_arg": true,
  "fleet_default": true,
  "scaled": true
}
```

### Example entry (install-only domain — no runner service)

```jsonc
"sentinel2-landchange": {
  "repo": "fwh_sentinel2",
  "extras": ["geo"],
  "description": "Sentinel-2 land-cover change → MapLibre tiled map"
}
```

---

## 5. Example entry — field reference

The `examples` section is **informational** today (in-repo teaching demos are
auto-discovered from `examples/<name>/`; no command reads these fields). List
them for reference:

| Field | Meaning |
|-------|---------|
| `dir` | In-repo path, e.g. `examples/hello-agent`. |
| `description` | One-line blurb. |

---

## 6. Walkthrough — add a new domain

You have a standalone `fwh_acme` package (declares the `facetwork.domains` entry
point — see `domain-template/`).

**A. Add it to the catalog.** Edit `domains.json` (to ship it as standard) or
drop a `domains.local.json` (deployment-only):

```jsonc
// domains.local.json
{
  "domains": {
    "acme": {
      "repo": "fwh_acme",
      "extras": ["s3"],
      "description": "ACME ingestion pipeline",
      "service": "runner-acme",
      "task_list": "acme"
    }
  }
}
```

**B. Install it** — `fw install domain acme` (clones + `pip install -e`). It now
shows in `fw install domain --list`, and `migrate.py` knows its name.

**C. (If it needs a compose runner)** add a `runner-acme` service block to
`docker-compose.full-stack.yml` by copying an existing standard domain's block
(keep the structural bits: `build`, `depends_on`, the `<<: *s3-storage` anchor,
`volumes`), then let the catalog fill its env:

```bash
fw util gen-compose          # writes AFL_DOMAIN_NAME/REPO/AFL_REGISTRY_RUNNER_ARGS into the block
fw util gen-compose --check  # CI guard: exits non-zero if compose drifts from the catalog
```

> `gen-compose` only *replaces env keys that already exist* in the block — it
> never invents structure. A brand-new service block must exist first (step C);
> the generator then keeps its env in sync with the catalog.

**D. Run it** — `fw runner start --domain acme` (or add `runner-acme` to
`fleet set --domain-runner acme` so fleet-agent manages it fleet-wide).

---

## 7. Walkthrough — override per deployment

A deployment that wants **only** OSM + its own domains, at higher scale, ships a
`domains.local.json` (no edits to committed files):

```jsonc
{
  "defaults": { "replicas": 2, "scaled_replicas": 6 },
  "domains": {
    "acme":  { "repo": "fwh_acme", "service": "runner-acme", "task_list": "acme", "scaled": true }
  },
  "_remove": ["jenkins", "genomics", "census-us", "save-earth",
              "sentinel2-landchange", "noaa-weather", "sensor-monitoring", "anthropic"]
}
```

Or replace the catalog entirely with an unrelated file: `export
AFL_DOMAINS_FILE=/etc/facetwork/our-domains.json`. Either way, every `fw`
command (`install domain`, `gen-compose`, `runner start`, `start-all`, fleet
validation) follows the deployment's catalog with no source changes.

After any catalog edit that affects compose env, run `fw util gen-compose`.

---

## 8. Validation

```bash
fw util gen-compose --check      # compose env matches the catalog (CI guard)
fw install domain --list         # the resolved domain set + descriptions
python -m pytest tests/test_domain_catalog.py tests/test_compose_gen.py
```

See also: [`facetwork/domains/catalog.py`](../../facetwork/domains/catalog.py)
(loader API), the `_comment` field in [`domains.json`](../../domains.json), and
**Standalone domain packages** in [CLAUDE.md](../../CLAUDE.md).

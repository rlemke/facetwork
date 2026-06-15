# Dashboard (v3 UI)

The Facetwork dashboard is a FastAPI + Jinja server-rendered web app for running
and monitoring workflows. It runs on **http://localhost:8080** (`python -m
facetwork.dashboard`).

The current UI is **v3** and it is the default — opening `/` redirects to
`/v3/workflows`. Every page lives under `/v3/…` and shares one shell
(`templates/v3/base.html` + `static/v3.css`): a left sidebar (navigation + app
switcher + acting-as footer), a top bar (breadcrumb, page actions, density and
theme toggles), and the page body. Theme (dark/light) and density are persisted
in the browser.

> The older **v2** UI and the original non-prefixed page routes have been
> removed. v3 fully supersedes them. The only pages still served outside `/v3`
> are `/namespaces` and `/sources` (no v3 equivalent yet); the JSON `/api/*`
> endpoints, the SSE log streams, and all the POST action endpoints
> (retry/rerun/cancel/…) remain unchanged and are what v3 calls under the hood.

## Navigation

The sidebar is grouped:

| Group | Item | URL | What it shows |
|-------|------|-----|---------------|
| — | **Runs** | `/v3/workflows` | All workflow runs grouped by namespace, with **Running / Completed / Failed** tabs and a name filter |
| — | **Library** | `/v3/flows` | Compiled FFL flows (namespaces/facets/workflows); each row shows when the flow was created |
| — | **Catalog** | `/v3/catalog` | Reusable, versioned workflows/libraries you can run |
| — | **Filters** | `/v3/filters` | The global Flow/Workflow filter page (see below) |
| Infrastructure | **Servers** | `/v3/servers` | Runner processes, health, what they're handling |
| Infrastructure | **Handlers** | `/v3/handlers` | Registered event-facet handlers |
| Infrastructure | **Fleet** | `/v3/fleet` | Infra services (MongoDB/MinIO/Dashboard, by URL) + runner roles — there is no master |
| Infrastructure | **Tasks** | `/v3/tasks` | The task queue across runners |
| Infrastructure | **Events** | `/v3/events` | Event-facet work (tasks dispatched to agents) |
| Data | **Output** | `/v3/output` | Browse handler output / cached artifacts |
| Data | **PostGIS** | `/v3/postgis` | PostGIS database summary |
| Access | **Users** | `/v3/users` | User management + the **acting-as** selector |
| Access | **Teams** | `/v3/teams` | Team management and membership |

Deep pages: a run's detail (`/v3/workflows/{id}`) shows a live execution graph,
step logs (streamed via SSE), and recovery actions (Pause/Resume/Cancel/Repair,
and per-step Retry/Re-run/Reset). A single step is at `/v3/steps/{id}`. New runs
start at `/v3/workflows/new`.

### App switcher (domain dashboards)

The sidebar's app switcher (top, under the brand) flips between **Platform**
(the table above) and the domain "apps", each on the same v3 shell:

- **Census Maps** — `/census/maps`
- **Site Selection** — `/site-selection/`
- **Climate Trends** — `/climate-trends/`

## Global filters

The **Filters** page (`/v3/filters`) sets filters that **persist** (in an
`afl_filters` browser cookie) and are applied automatically whenever you view
**Library** (Flows) and **Runs** (Workflows). It has two sections:

**Flows** (applies to `/v3/flows`)
- **Teams** — multi-select; pick several. None selected = **Any team**.
- **Author** — single-select, with **Any author**.
- **Created** — date range (from → to); blank = **Any date**. Backed by the
  flow's real `created_at` (stamped at publish time; legacy flows fall back to
  their earliest workflow date).
- **Tag** — single-select, with **Any**.

**Workflows** (applies to `/v3/workflows`)
- **Teams** — multi-select.
- **Workflows** — the workflows belonging to the selected teams (the list
  filters client-side to the chosen teams); none = **Any workflow**.
- **Ran by** — the runner user who ran it, single-select with **Any user**.
- **Run date** — date range for when the workflow was run.
- **State** — Any / Running / Completed / Failed.
- **Purpose** — Any / production / beta / test / …

Every single-select offers **Any**, and an empty multi-select or blank date
means Any — so with no filters set, both lists show everything. **Apply
filters** saves the cookie; **Clear all** resets it. When a filter is active,
the Library and Runs pages show a banner summarizing it with **Edit** / **Clear**
links, and the Runs tab counts reflect the filtered set.

Filters are evaluated server-side in
[`facetwork/dashboard/viewfilters.py`](../../facetwork/dashboard/viewfilters.py)
(cookie parse, flow/runner matchers, option-list builder) and applied in the
`flows_v3` and `workflow_list_v3` routes.

## Users, teams, and "acting as"

The **Access** group manages identities. Each user has an email, name, teams,
and a default team; teams have a leader and members. The acting-as footer link
(and the **Act as** button on the Users list) sets the current user cookie —
runs you submit are attributed to that user. These are the same identities used
by the run form's team tagging and by the global filters' author/runner-user
selects.

## Implementation notes

- v3 routes: `facetwork/dashboard/routes/v3/` (registered in
  `routes/__init__.py`). They reuse shared view-data builders in
  `facetwork/dashboard/viewdata.py` and helpers in `helpers.py`.
- The dashboard image is a local build (`docker/Dockerfile.dashboard`); redeploy
  with `docker compose -f docker-compose.full-stack.yml build dashboard` then
  `up -d --no-deps --force-recreate dashboard`.
- Flow `created_at` is stamped once at publish time by `MongoStore.save_flow`
  and preserved on later saves; all publish paths (catalog publish, CLI submit,
  example seeding) go through it.

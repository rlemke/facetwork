# Local maps gallery — `fw svc maps`

Browse every map your fleet has produced **straight from MinIO**, with nothing
copied to a git repo. This decouples *keeping* maps from *publishing* them: big
maps, tiled maps, and their data can live only in the object store, and you push
just the ones you pick to the public site ([facetwork-maps](https://github.com/rlemke/facetwork-maps))
separately.

```bash
fw svc maps                 # → http://localhost:8090/
fw svc maps --port 9095     # custom port
```

## How it works

Every domain already writes its maps to the durable store under
`<prefix>/<domain>/maps/<name…>/index.html` (+ a `.meta.json` sidecar) — e.g.
`cache/save-earth/maps/seismic/index.html` in the `afl-cache` bucket. `fw svc maps`:

1. **Lists** those objects and reads each sidecar for title/size/date/layers.
2. **Renders** a gallery at `/`, grouped by domain. Each card has a **View map ↗**
   button (opens the map locally) and, when a token is available, a **Publish**
   button (below).
3. **Streams** each map from MinIO on request at `/m/<domain>/maps/<name>/…` — a
   read-only proxy, so **no bucket policy change, no anonymous access, and
   sibling assets/tiles resolve** (relative links keep working). Nothing is
   written to disk or to git.

It always reflects the current bucket contents — run a map workflow and refresh.

## Keep it always up (launchd, macOS)

```bash
fw svc maps-install                 # write + load a LaunchAgent (port 8090)
fw svc maps-install --port 9095     # custom port
fw svc maps-install --uninstall     # unload + remove
```

Installs `com.facetwork.maps` (RunAtLoad + KeepAlive) via a login-shell wrapper at
`~/.facetwork/maps-gallery.sh`. It waits for Docker + local MinIO before starting
(so it doesn't crash-loop on boot) and is restarted automatically if it dies —
surviving logout/reboot. Logs: `~/.facetwork/maps-gallery.log`. On Linux, run
`fw svc maps` from a systemd user unit instead.

### ⚠️ Publishing under launchd — the keychain gotcha

A LaunchAgent runs as a background process that **cannot read `gh`'s token from the
macOS keychain** (`gh auth status` shows `(keyring)`, and there's no plaintext token
in `~/.config/gh/hosts.yml`). So the always-up gallery starts with **publish
disabled** — its log says `publish → …: disabled (no GITHUB_TOKEN / gh auth)` — even
though `gh auth token` works fine in your Terminal. Browsing/viewing is unaffected.

Two ways to publish:

- **Interactive (no token on disk):** run `fw svc maps --port 8091` in a terminal
  (a login shell *can* read the keychain, so publish is enabled there) and use that
  instance's Publish buttons. The launchd one keeps serving :8090 for browsing.
- **Enable publish under launchd:** drop `GITHUB_TOKEN=<token>` into
  `~/.facetwork/maps.env` (mode 0600) — the wrapper sources it. Use a **fine-grained
  PAT scoped to only the Pages repo with Contents: read-write** (least privilege),
  not your full gh token. Then `launchctl kickstart -k gui/$(id -u)/com.facetwork.maps`
  (or `fw svc maps-install`) to restart.

## Config

All optional; defaults suit the MaxPro standalone box (local MinIO). Read from the
environment / `.env` / `.env.fleet`:

| var | default | meaning |
|---|---|---|
| `FW_S3_ENDPOINT` | `http://localhost:9000` | MinIO/S3 endpoint (`afl-minio` is rewritten to localhost) |
| `FW_S3_ACCESS_KEY` / `FW_S3_SECRET_KEY` | `minioadmin` | credentials |
| `FW_MAPS_BUCKET` | `$FW_S3_BUCKET` or `afl-cache` | bucket to browse |
| `FW_MAPS_PREFIX` | `cache/` | key prefix maps live under |
| `FW_MAPS_PORT` | `8090` | listen port (or `--port`) |

Point it at any store — e.g. `FW_S3_ENDPOINT=http://afl-minio:9000 fw svc maps` on a
host that reaches the shared fleet MinIO.

## Publishing a selected map to the public site

Each card has a **"Publish to facetwork-maps →"** button. It reveals a destination
field (prefilled `<domain>/<name>`, e.g. `save-earth/seismic`; the first path
segment is the public-site section — edit to `world/seismic` etc.), and on
**Publish** (after a confirm) pushes **just that map's `index.html`** to the public
GitHub Pages repo and returns the live URL.

- Reuses the existing `census_us.publish_bundles`, which is **incremental** — it
  harvests the maps already on the site and merges, so publishing one **never
  wipes** the rest of the public gallery.
- `include=[".html"]` — only the self-contained HTML goes public; the big
  GeoJSON/data/tiles stay local in MinIO. (Tiled maps that need sibling assets are
  not a fit for HTML-only publish.)
- **Token**: needs push access to the Pages repo — `GITHUB_TOKEN`/`GH_TOKEN`, else
  the `gh auth` session (`gh auth token`). If none is available the button is
  replaced by a "publishing disabled" note. Config the repo with
  `FW_MAPS_PUBLISH_REPO` (default `rlemke/facetwork-maps`) / `FW_MAPS_PUBLISH_BRANCH`.
- Only maps that exist in the store can be published (the endpoint validates the
  map id and rejects path traversal), so exposing the gallery on your LAN can't be
  used to push arbitrary content. Pages typically lags ~1 min before the map is live.

This keeps the split you want: **everything is browsable locally; only what you
click Publish on reaches the public site.**

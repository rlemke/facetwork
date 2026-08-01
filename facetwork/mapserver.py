"""Local maps gallery — browse the map outputs sitting in MinIO/S3 without ever
copying them to a git repo.

Domains write their maps to the durable object store under either
``<prefix>/<domain>/maps/<name…>/index.html`` (save-earth, health) or
``<prefix>/<domain>/output/…/index.html`` (h1b, census-us) — both with a
``.meta.json`` sidecar. This service lists those objects, renders a gallery grouped
by domain, and STREAMS each map straight from the bucket on request — so big
maps/tiles/data live only in MinIO, and you publish just the ones you pick to the
public site separately.

Run it via ``fw svc maps`` (see scripts/lib/svc/maps). Read-only; no bucket policy
change, no anonymous access, nothing written back to the store.

Config (env, all optional — defaults suit the MaxPro standalone box):
  FW_S3_ENDPOINT   S3/MinIO endpoint       (default http://localhost:9000)
  FW_S3_ACCESS_KEY / FW_S3_SECRET_KEY      (default minioadmin / minioadmin)
  FW_MAPS_BUCKET   bucket to browse        (default FW_S3_BUCKET or 'afl-cache')
  FW_MAPS_PREFIX   key prefix maps live under (default 'cache/')
"""
from __future__ import annotations

import json
import mimetypes
import os
import socket
import subprocess
import threading
import time
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote

import boto3
from botocore.config import Config as _BotoConfig

# --- config ---------------------------------------------------------------

ENDPOINT = os.environ.get("FW_S3_ENDPOINT", "http://localhost:9000")
# afl-minio only resolves via /etc/hosts (→ localhost on a standalone box); prefer
# an explicit localhost so the host process always reaches the local MinIO.
if "afl-minio" in ENDPOINT:
    ENDPOINT = "http://localhost:9000"
ACCESS = os.environ.get("FW_S3_ACCESS_KEY", "minioadmin")
SECRET = os.environ.get("FW_S3_SECRET_KEY", "minioadmin")
BUCKET = os.environ.get("FW_MAPS_BUCKET") or os.environ.get("FW_S3_BUCKET", "afl-cache")
PREFIX = os.environ.get("FW_MAPS_PREFIX", "cache/").lstrip("/")

# Selected-map publishing → the public GitHub Pages site. Off unless a token is
# obtainable (env GITHUB_TOKEN/GH_TOKEN, else `gh auth token`).
PUBLISH_REPO = os.environ.get("FW_MAPS_PUBLISH_REPO", "rlemke/facetwork-maps")
PUBLISH_BRANCH = os.environ.get("FW_MAPS_PUBLISH_BRANCH", "main")
_publish_lock = threading.Lock()  # serialize pushes to the one Pages repo

# A map = a key ".../maps/<name…>/index.html" under PREFIX. The domain is the first
# segment after PREFIX; the name is everything between "maps/" and "/index.html".
# Domains write map HTML under two conventions: <domain>/maps/<name>/index.html
# (save-earth, health) and <domain>/output/…/index.html (h1b, census-us). Match both.
def _classify(key: str):
    """(domain, name, map_rel) for a map index.html, else None. map_rel is the key
    minus PREFIX and the trailing /index.html (used for the view URL + publish)."""
    if not key.startswith(PREFIX) or not key.endswith("/index.html"):
        return None
    segs = key[len(PREFIX):-len("/index.html")].split("/")
    if len(segs) < 2:
        return None
    domain, sub = segs[0], segs[1:]
    if sub[0] == "maps" and len(sub) >= 2:
        name = "/".join(sub[1:])
    elif sub[0] == "output":
        tail = [s for s in sub[1:] if s != "metrics"]  # 'metrics' is a noise segment
        name = "/".join(tail) if tail else domain
    else:
        return None
    return domain, name, "/".join(segs)


def _client():
    return boto3.client(
        "s3", endpoint_url=ENDPOINT, aws_access_key_id=ACCESS,
        aws_secret_access_key=SECRET,
        # Fail fast rather than hang: under heavy MinIO load (e.g. a big concurrent
        # upload) an un-bounded list/get would block the request thread for minutes.
        config=_BotoConfig(signature_version="s3v4", retries={"max_attempts": 2},
                           connect_timeout=4, read_timeout=12),
    )


# Cache the map listing so the gallery stays responsive when MinIO is busy: serve
# the last good list (stale) rather than re-listing on every request / hanging.
_maps_cache: dict = {"maps": None, "ts": 0.0}
_maps_lock = threading.Lock()


def cached_maps(s3, ttl: float = 20.0) -> list[dict]:
    now = time.time()
    with _maps_lock:
        if _maps_cache["maps"] is not None and now - _maps_cache["ts"] < ttl:
            return _maps_cache["maps"]
    try:
        m = list_maps(s3)
    except Exception:
        with _maps_lock:
            if _maps_cache["maps"] is not None:
                return _maps_cache["maps"]  # serve stale rather than error
        raise
    with _maps_lock:
        _maps_cache["maps"], _maps_cache["ts"] = m, now
    return m


# --- publish (opt-in) ------------------------------------------------------

def _github_token() -> str | None:
    """Token for pushing to the Pages repo: env first, else the gh CLI session."""
    tok = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if tok:
        return tok
    try:
        out = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=8)
        tok = out.stdout.strip()
        return tok or None
    except Exception:
        return None


def publish_available() -> bool:
    return _github_token() is not None


def publish_map(map_rel: str, dest: str, label: str = "") -> dict:
    """Publish ONE map (its self-contained index.html) to the public Pages site.

    map_rel: the map's key path minus PREFIX, e.g. 'save-earth/maps/seismic'.
    dest:    path within the repo, e.g. 'world/seismic' (first segment = section).
    Reuses census_us.publish_bundles, which is INCREMENTAL — it harvests the maps
    already on the site and merges, so this never wipes the rest of the gallery.
    include=[".html"] keeps the public site to the self-contained HTML (the big
    GeoJSON/data stays local in MinIO). Serialized against concurrent publishes.
    """
    token = _github_token()
    if not token:
        return {"ok": False, "error": "no GitHub token (set GITHUB_TOKEN or run `gh auth login`)"}
    dest = (dest or "").strip().strip("/")
    if not dest or ".." in dest.split("/"):
        return {"ok": False, "error": f"invalid destination {dest!r}"}
    # Import lazily so the gallery works even without the domain package installed.
    try:
        from census_us.tools._lib.publish import publish_bundles
    except Exception as e:
        return {"ok": False, "error": f"publisher unavailable: {e}"}
    prefix = f"s3://{BUCKET}/{PREFIX}{map_rel}/"
    try:
        with _publish_lock:
            res = publish_bundles(
                PUBLISH_REPO, [prefix], [dest],
                branch=PUBLISH_BRANCH, include=[".html"],
                labels=[label or None], token=token,
            )
        pages = getattr(res, "pages_url", None) or f"https://{PUBLISH_REPO.split('/')[0]}.github.io/{PUBLISH_REPO.split('/')[1]}/{dest}/"
        return {"ok": True, "pages_url": pages, "dest": dest,
                "files": getattr(res, "files", None), "sha": getattr(res, "sha", None)}
    except Exception as e:
        return {"ok": False, "error": str(e)[:400]}


# --- harvest --------------------------------------------------------------

def list_maps(s3) -> list[dict]:
    """Every map index.html under PREFIX, newest first, with its meta.json."""
    maps: list[dict] = []
    paginator = s3.get_paginator("list_objects_v2")
    metas: dict[str, dict] = {}
    index_keys: list[tuple[str, int, str]] = []
    for page in paginator.paginate(Bucket=BUCKET, Prefix=PREFIX):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if key.endswith("/index.html.meta.json"):
                metas[key] = obj  # fetched lazily below
            elif _classify(key):
                index_keys.append((key, obj.get("Size", 0),
                                   obj.get("LastModified").isoformat() if obj.get("LastModified") else ""))
    # Render straight from the LIST results — size + last-modified are already
    # there. We deliberately do NOT fetch each map's meta.json: that was an N+1 of
    # sequential S3 GETs that made the page crawl (and hang under heavy MinIO load,
    # e.g. a big concurrent upload). Layer counts came from meta.json; dropping them
    # keeps the gallery instant. `metas` is unused now but the listing pass is free.
    for key, size, mtime in index_keys:
        domain, name, map_rel = _classify(key)
        maps.append({
            "domain": domain,
            "name": name,
            "url": "/m/" + key[len(PREFIX):],                      # key sans PREFIX
            "map_rel": map_rel,                                     # for view/publish
            "size": size,
            "generated_at": mtime,
            "layers": [],
            "layer_counts": {},
        })
    maps.sort(key=lambda x: x["generated_at"], reverse=True)
    return maps


# --- rendering ------------------------------------------------------------

def _pretty(seg: str) -> str:
    return seg.replace("-", " ").replace("_", " ").replace("/", " · ").title()


def _human_size(n: int) -> str:
    f = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if f < 1024 or unit == "GB":
            return f"{f:.0f} {unit}" if unit == "B" else f"{f:.1f} {unit}"
        f /= 1024
    return f"{f:.1f} GB"


_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Facetwork maps (local)</title>
<style>
 :root{{color-scheme:light dark}}
 body{{font:15px/1.5 system-ui,sans-serif;margin:0;background:#0f1115;color:#e6e6e6}}
 header{{padding:20px 28px;border-bottom:1px solid #262a33;background:#151821}}
 h1{{margin:0;font-size:19px}} .sub{{color:#8a93a6;font-size:13px;margin-top:4px}}
 main{{padding:20px 28px;max-width:1200px;margin:0 auto}}
 h2{{font-size:14px;text-transform:uppercase;letter-spacing:.06em;color:#8a93a6;
     border-bottom:1px solid #262a33;padding-bottom:6px;margin:28px 0 14px}}
 .grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:14px}}
 .card{{background:#171a22;border:1px solid #262a33;border-radius:10px;
        padding:14px 16px;color:inherit;transition:border-color .15s}}
 .card:hover{{border-color:#3d6be5}}
 .card .t{{font-weight:600;font-size:15px;color:#e6e6e6}}
 .card .d{{color:#8a93a6;font-size:12.5px;margin-top:6px}}
 .meta{{color:#6f7788;font-size:11.5px;margin-top:8px}}
 .actions{{margin-top:12px;border-top:1px solid #262a33;padding-top:10px;
           display:flex;gap:8px;align-items:center;flex-wrap:wrap}}
 button,.btn{{font:inherit;font-size:12px;background:#222836;color:#cbd5e8;border:1px solid #333b4d;
         border-radius:6px;padding:5px 11px;cursor:pointer;text-decoration:none;display:inline-block}}
 button:hover,.btn:hover{{border-color:#3d6be5}} button:disabled{{opacity:.5;cursor:default}}
 .btn.view{{background:#1c3a63;border-color:#2f5691;color:#dce9ff;font-weight:600}}
 .btn.view:hover{{background:#22467a}}
 .pubform{{margin-top:8px;display:flex;gap:6px;align-items:center;flex-wrap:wrap}}
 .pubform input{{font:inherit;font-size:12px;background:#0f1115;color:#e6e6e6;
                 border:1px solid #333b4d;border-radius:6px;padding:4px 8px;flex:1;min-width:120px}}
 .status{{font-size:11.5px;color:#8a93a6;width:100%}}
 .status a{{color:#6ea8ff}} .status.err{{color:#ff8a8a}} .status.ok{{color:#6ee7a8}}
 .empty{{color:#8a93a6;padding:40px 0}}
 code{{background:#1e222b;padding:1px 5px;border-radius:4px}}
</style></head><body>
<header><h1>Facetwork maps — local gallery</h1>
<div class="sub">Served from MinIO <code>{bucket}</code> · {count} map(s) · nothing here is on the public site until you publish it</div>
</header><main>{body}</main>
<script>
function togglePub(btn){{
  const f = btn.closest('.card').querySelector('.pubform');
  f.hidden = !f.hidden; if(!f.hidden) f.querySelector('input').focus();
}}
async function doPublish(btn, mapRel){{
  const form = btn.parentElement, dest = form.querySelector('input').value.trim();
  const status = form.querySelector('.status');
  if(!dest){{ status.className='status err'; status.textContent='enter a destination path'; return; }}
  if(!confirm('Publish this map to the PUBLIC site at "'+dest+'"?')) return;
  btn.disabled = true; status.className='status'; status.textContent='publishing…';
  try{{
    const r = await fetch('/publish', {{method:'POST', headers:{{'Content-Type':'application/json'}},
      body: JSON.stringify({{map: mapRel, dest: dest}})}});
    const j = await r.json();
    if(j.ok){{ status.className='status ok';
      status.innerHTML='published → <a href="'+j.pages_url+'" target="_blank" rel="noopener">'+j.pages_url+'</a> (Pages may lag ~1 min)'; }}
    else{{ status.className='status err'; status.textContent='failed: '+j.error; }}
  }}catch(e){{ status.className='status err'; status.textContent='error: '+e; }}
  btn.disabled = false;
}}
</script>
</body></html>"""


def _actions(m: dict, can_publish: bool) -> str:
    """Actions row for a card: always a prominent View button; Publish when a token
    is available. The publish form (hidden) sits below the row, inside the card."""
    view = (f'<a class="btn view" href="{escape(m["url"])}" target="_blank" '
            f'rel="noopener">View map ↗</a>')
    if not can_publish:
        note = ('<span class="status" style="width:auto">publishing disabled — no '
                'GitHub token (<code>gh auth login</code>)</span>')
        return f'<div class="actions">{view}{note}</div>'
    default_dest = f'{m["domain"]}/{m["name"]}'
    toggle = (f'<button onclick="togglePub(this)">Publish to '
              f'{escape(PUBLISH_REPO.split("/")[-1])} →</button>')
    form = ('<div class="pubform" hidden>'
            f'<input value="{escape(default_dest)}" spellcheck="false" aria-label="destination path">'
            f'<button onclick="doPublish(this,{json.dumps(m["map_rel"])})">Publish</button>'
            '<span class="status"></span></div>')
    return f'<div class="actions">{view}{toggle}</div>{form}'


def render_gallery(maps: list[dict], can_publish: bool = False) -> str:
    if not maps:
        body = ('<div class="empty">No maps in the store yet. Run a map workflow '
                '(its output lands in MinIO under <code>%s&lt;domain&gt;/maps/…</code>) '
                'and refresh.</div>' % escape(PREFIX))
        return _PAGE.format(bucket=escape(BUCKET), count=0, body=body)
    by_domain: dict[str, list[dict]] = {}
    for m in maps:
        by_domain.setdefault(m["domain"], []).append(m)
    blocks = []
    for domain in sorted(by_domain):
        cards = []
        for m in by_domain[domain]:
            nlayers = len(m["layers"])
            layer_bit = f'{nlayers} layer(s) · ' if nlayers else ""
            meta = f'{layer_bit}{_human_size(int(m["size"] or 0))} · {escape(m["generated_at"][:10])}'
            cards.append(
                '<div class="card">'
                f'<div class="t">{escape(_pretty(m["name"]))}</div>'
                f'<div class="d">{escape(domain)}</div>'
                f'<div class="meta">{meta}</div>'
                f'{_actions(m, can_publish)}</div>')
        blocks.append(f'<h2>{escape(_pretty(domain))}</h2><div class="grid">{"".join(cards)}</div>')
    return _PAGE.format(bucket=escape(BUCKET), count=len(maps), body="".join(blocks))


# --- HTTP -----------------------------------------------------------------

class _Handler(BaseHTTPRequestHandler):
    server_version = "fw-mapserver/1.0"

    def _send(self, code, body, ctype="text/html; charset=utf-8", extra=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        path = unquote(self.path.split("?", 1)[0])
        s3 = self.server.s3  # type: ignore[attr-defined]
        if path in ("/", "/index.html"):
            try:
                self._send(200, render_gallery(cached_maps(s3), can_publish=self.server.can_publish))
            except Exception as e:  # pragma: no cover - surfaced to the browser
                self._send(503, f"<h1>maps store busy</h1><p>Could not list the object "
                                f"store (it may be under heavy load): <code>{escape(str(e))}</code>."
                                f"<br>Retry in a moment.</p>")
            return
        if path == "/healthz":
            self._send(200, "ok", "text/plain")
            return
        if path.startswith("/m/"):
            rel = path[len("/m/"):]
            if rel.endswith("/") or rel == "":
                rel += "index.html"
            key = PREFIX + rel
            try:
                obj = s3.get_object(Bucket=BUCKET, Key=key)
            except Exception:
                self._send(404, f"<h1>404</h1><p>not in store: <code>{escape(key)}</code></p>")
                return
            ctype = mimetypes.guess_type(key)[0] or "application/octet-stream"
            self._send(200, obj["Body"].read(), ctype)
            return
        self._send(404, "<h1>404</h1>")

    do_HEAD = do_GET

    def do_POST(self):  # noqa: N802
        if self.path.split("?", 1)[0] != "/publish":
            self._send(404, json.dumps({"ok": False, "error": "not found"}), "application/json")
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            data = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            self._send(400, json.dumps({"ok": False, "error": "bad request body"}), "application/json")
            return
        map_rel = (data.get("map") or "").strip().strip("/")
        dest = (data.get("dest") or "").strip()
        label = (data.get("label") or "").strip()
        # Only allow publishing a map that actually exists in the store (never an
        # arbitrary prefix — the server may be reachable on the LAN).
        if not map_rel or not any(m["map_rel"] == map_rel for m in list_maps(self.server.s3)):
            self._send(400, json.dumps({"ok": False, "error": f"unknown map {map_rel!r}"}), "application/json")
            return
        result = publish_map(map_rel, dest, label)
        self._send(200 if result.get("ok") else 400, json.dumps(result), "application/json")

    def log_message(self, fmt, *args):  # quieter logs
        pass


def _lan_ip() -> str | None:
    """This host's primary LAN IP (so the gallery URL works from other machines).
    Uses a UDP socket with no data sent; returns None if only loopback resolves."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
        finally:
            s.close()
        return ip if ip and not ip.startswith("127.") else None
    except Exception:
        return None


def serve(port: int, host: str = "0.0.0.0") -> None:
    httpd = ThreadingHTTPServer((host, port), _Handler)
    httpd.s3 = _client()  # type: ignore[attr-defined]
    # Fail fast + informatively if the store is unreachable.
    try:
        httpd.s3.head_bucket(Bucket=BUCKET)
    except Exception as e:
        raise SystemExit(f"cannot reach bucket '{BUCKET}' at {ENDPOINT}: {e}\n"
                         f"  set FW_S3_ENDPOINT / FW_MAPS_BUCKET, or start MinIO.")
    httpd.can_publish = publish_available()  # type: ignore[attr-defined]
    lan = _lan_ip()
    print("Facetwork maps gallery is up — view it at:")
    print(f"    http://localhost:{port}/")
    if lan:
        print(f"    http://{lan}:{port}/   (from other machines on your network)")
    print(f"  serving {BUCKET} @ {ENDPOINT} (prefix {PREFIX!r})")
    print("  publish → %s: %s" % (
        PUBLISH_REPO,
        "enabled (GitHub token found)" if httpd.can_publish
        else "disabled (no GITHUB_TOKEN / gh auth)"))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.")


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description="Local maps gallery over MinIO/S3.")
    ap.add_argument("--port", type=int, default=int(os.environ.get("FW_MAPS_PORT", "8090")))
    ap.add_argument("--host", default="0.0.0.0")
    args = ap.parse_args()
    serve(args.port, args.host)


if __name__ == "__main__":
    main()

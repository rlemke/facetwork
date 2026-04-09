# Downloads and Operations

The operations layer handles data processing tasks that act on cached OSM data. Operations receive an `OSMCache` struct from a cache facet and perform work such as downloading files, generating map tiles, building routing graphs, or importing data into PostGIS.

## FFL facets

All operations are defined in `afl/osmoperations.ffl` under two namespaces.

### Data facets

The `osm.ops.Data` namespace defines metadata facets used as parameters:

```afl
namespace osm.ops.Data {
    facet OsmDate(date: String = "latest")
    facet OsmDownloadUrl(url: String = null)
    facet OsmCachedLocation(location: String = null)
    facet OsmForce(force: Boolean = false)
}
```

These are plain facets (not event facets) — they carry configuration values but do not trigger agent execution.

### Event facets

The `osm.ops` namespace defines 13 event facets:

```afl
namespace osm.ops {
    event facet Download(cache:OSMCache) => ()
    event facet Tile(cache:OSMCache) => (tiles:OSMCache)
    event facet RoutingGraph(cache:OSMCache) => (graph:OSMCache)
    event facet Status(cache:OSMCache) => (stats:OSMCache)
    event facet GeoOSMCache(cache:OSMCache) => (graph:OSMCache)
    event facet PostGisImport(cache:OSMCache) => (stats:OSMCache)
    event facet DownloadBatch(cache:OSMCache) => ()
    event facet TileBatch(cache:OSMCache) => (tiles:OSMCache)
    event facet RoutingGraphBatch(cache:OSMCache) => (graph:OSMCache)
    event facet StatusBatch(cache:OSMCache) => (stats:OSMCache)
    event facet GeoOSMCacheBatch(cache:OSMCache) => (graph:OSMCache)
    event facet DownloadShapefile(cache:OSMCache) => ()
    event facet DownloadShapefileBatch(cache:OSMCache) => ()
}
```

### Facet reference

| Facet | Return | Description |
|-------|--------|-------------|
| `Download` | `()` | Download a PBF file for a single region |
| `DownloadBatch` | `()` | Download a PBF file for a bulk/aggregate region |
| `DownloadShapefile` | `()` | Download a Geofabrik free shapefile for a single region |
| `DownloadShapefileBatch` | `()` | Download a shapefile for a bulk/aggregate region |
| `Tile` | `tiles:OSMCache` | Generate map tiles from a single region's PBF |
| `TileBatch` | `tiles:OSMCache` | Generate map tiles from a bulk region |
| `RoutingGraph` | `graph:OSMCache` | Build a routing graph from a single region's PBF |
| `RoutingGraphBatch` | `graph:OSMCache` | Build a routing graph from a bulk region |
| `Status` | `stats:OSMCache` | Report processing status for a single region |
| `StatusBatch` | `stats:OSMCache` | Report processing status for a bulk region |
| `GeoOSMCache` | `graph:OSMCache` | Generate GeoJSON cache from a single region |
| `GeoOSMCacheBatch` | `graph:OSMCache` | Generate GeoJSON cache from a bulk region |
| `PostGisImport` | `stats:OSMCache` | Import a region into PostGIS |

The `*All` variants are identical in signature to their single-region counterparts. The distinction is semantic — workflows use them to signal that the input is a continent or aggregate extract rather than a single country.

## Python handlers

Operations handlers are registered in `handlers/operations_handlers.py`. The module defines two handler factories:

### Standard operation handler

For most facets, `_make_operation_handler` creates a pass-through handler that logs the operation and returns the cache data under the appropriate return parameter name:

```python
def _make_operation_handler(facet_name: str, return_param: str | None):
    def handler(payload: dict) -> dict:
        cache = payload.get("cache", {})
        if return_param is None:
            return {}
        return {return_param: {
            "url": cache.get("url", ""),
            "path": cache.get("path", ""),
            # ...
        }}
    return handler
```

Facets with `=> ()` (no return) use `return_param=None` and return an empty dict.

### Shapefile handler

`DownloadShapefile` and `DownloadShapefileBatch` use a specialized handler that extracts the region path from the cache URL and downloads in shapefile format. See [shapefiles README](../shapefiles/README.md) for details.

### Handler dispatch

Registration routes each facet to the correct factory:

```python
OPERATIONS_FACETS = {
    "Download": None,        # => ()
    "Tile": "tiles",
    "RoutingGraph": "graph",
    "Status": "stats",
    "GeoOSMCache": "graph",
    "PostGisImport": "stats",
    "DownloadBatch": None,
    "TileBatch": "tiles",
    "RoutingGraphBatch": "graph",
    "StatusBatch": "stats",
    "GeoOSMCacheBatch": "graph",
    "DownloadShapefile": None,
    "DownloadShapefileBatch": None,
}
```

All handlers are registered under the qualified name `osm.ops.<FacetName>`.

## Downloader module

The `handlers/downloader.py` module is a standalone HTTP client with filesystem caching. It has no Facetwork dependencies and can be used independently.

### API

```python
from handlers.downloader import download, geofabrik_url, cache_path

# Build a Geofabrik URL
geofabrik_url("europe/albania")              # https://download.geofabrik.de/europe/albania-latest.osm.pbf
geofabrik_url("europe/albania", fmt="shp")   # https://download.geofabrik.de/europe/albania-latest.free.shp.zip

# Get the local cache path
cache_path("europe/albania")                 # /tmp/osm-cache/europe/albania-latest.osm.pbf

# Download (returns OSMCache dict)
result = download("europe/albania")
result = download("europe/albania", fmt="shp")
```

### Supported formats

| Format key | File extension | Description |
|-----------|---------------|-------------|
| `"pbf"` | `osm.pbf` | Protocol Buffer Format — compact binary OSM data (default) |
| `"shp"` | `free.shp.zip` | Geofabrik free shapefile extract (zipped) |

### Caching behavior

- Files are cached under `/tmp/osm-cache/` mirroring the Geofabrik directory structure
- PBF and shapefile formats are cached independently (different file extensions)
- A file is considered cached if it exists on disk — no TTL or staleness check
- Downloads use streaming (`iter_content`) with an 8 KB chunk size
- The `User-Agent` header is set to `Facetwork-OSM-Example/1.0`
- HTTP errors propagate as `requests.HTTPError` exceptions
- Timeout is 300 seconds per download

### Return value

The `download()` function returns an `OSMCache`-compatible dict:

```python
{
    "url": "https://download.geofabrik.de/europe/albania-latest.osm.pbf",
    "path": "/tmp/osm-cache/europe/albania-latest.osm.pbf",
    "date": "2026-02-01T12:00:00+00:00",
    "size": 14523648,
    "wasInCache": False
}
```

## Local Geofabrik Mirror

In CI, test, or air-gapped environments you may want to avoid hitting `download.geofabrik.de` on every run. The downloader supports a **local mirror** that is checked before any network request.

### Setting up the mirror

Use the `scripts/osm-prefetch` script to populate a mirror directory:

```bash
# Download all ~250 region PBFs (be patient — this is ~50 GB)
scripts/osm-prefetch --mirror-dir /data/osm-mirror

# Preview what would be downloaded
scripts/osm-prefetch --dry-run

# Download only European regions
scripts/osm-prefetch --include "europe/" --mirror-dir /data/osm-mirror

# Skip regions you don't need
scripts/osm-prefetch --exclude "planet|antarctica" --mirror-dir /data/osm-mirror

# Resume an interrupted download (skips existing files)
scripts/osm-prefetch --resume --mirror-dir /data/osm-mirror

# Download shapefiles instead of PBFs
scripts/osm-prefetch --fmt shp --mirror-dir /data/osm-mirror
```

Key options:

| Option | Default | Description |
|--------|---------|-------------|
| `--mirror-dir` | `./osm-mirror` | Target directory for downloaded files |
| `--dry-run` | — | List files without downloading |
| `--include` | — | Only download paths matching this regex |
| `--exclude` | — | Skip paths matching this regex |
| `--resume` | — | Skip files that already exist in the mirror |
| `--fmt` | `pbf` | Format: `pbf`, `shp`, or `all` |
| `--delay` | `2.0` | Seconds to wait between downloads (rate limiting) |

The script writes a `manifest.json` into the mirror directory listing every downloaded file.

### Activating the mirror

Set the `AFL_GEOFABRIK_MIRROR` environment variable to the mirror directory:

```bash
export AFL_GEOFABRIK_MIRROR=/data/osm-mirror
```

### Check order

When `download()` is called, the downloader checks in this order:

1. **Cache** — if the file exists under `/tmp/osm-cache/`, return immediately (no lock)
2. **Mirror** — if `AFL_GEOFABRIK_MIRROR` is set and the file exists there, return immediately (no lock, read-only)
3. **Lock + download** — acquire a per-path lock, re-check the cache, then download from Geofabrik

Mirror reads are lock-free because the mirror directory is treated as read-only — the downloader never writes to it.

### Mirror directory structure

The mirror layout matches Geofabrik's path structure:

```
/data/osm-mirror/
├── africa/
│   ├── algeria-latest.osm.pbf
│   └── angola-latest.osm.pbf
├── europe/
│   ├── albania-latest.osm.pbf
│   └── austria-latest.osm.pbf
├── manifest.json
└── ...
```

## Concurrency Control

The downloader is safe for concurrent use from multiple threads. It uses per-path locks with a double-checked locking pattern and atomic file writes.

### Per-path locks

Each cache file path gets its own `threading.Lock`. The `_get_path_lock()` function uses a double-checked pattern to create locks without races:

```python
_path_locks: dict[str, threading.Lock] = {}
_path_locks_guard = threading.Lock()

def _get_path_lock(path: str) -> threading.Lock:
    if path not in _path_locks:          # fast check (no lock)
        with _path_locks_guard:          # global guard for creation
            if path not in _path_locks:  # re-check under guard
                _path_locks[path] = threading.Lock()
    return _path_locks[path]
```

Different region paths are downloaded in parallel without contention. Only requests for the *same* path serialize.

### Double-checked download

The `download()` function checks the cache twice — once before acquiring the lock (fast path) and once after (to avoid redundant downloads when another thread finished first):

1. Check cache — if file exists, return immediately (no lock)
2. Check mirror — if mirror file exists, return immediately (no lock)
3. Acquire per-path lock
4. Re-check cache — if file appeared while waiting, return (another thread downloaded it)
5. Download from Geofabrik

### Atomic writes

Downloads go to a temporary file first, then are atomically moved into place:

```python
tmp_path = local_path + f".tmp.{os.getpid()}.{threading.get_ident()}"
try:
    _stream_to_file(url, tmp_path, _storage)
    os.replace(tmp_path, local_path)       # atomic on POSIX
except BaseException:
    _storage.remove(tmp_path)              # clean up partial file
    raise
```

The temp file name includes the PID and thread ID to avoid collisions. `os.replace()` is atomic on POSIX systems, so the cache file is always either absent or complete — never a partial download.

### HDFS path

The `download_url()` function handles HDFS paths differently: it streams directly to the destination without atomic rename, because HDFS does not support `os.replace()`. Local paths use the same atomic temp-file pattern.

## How workflows use operations

Regional workflows compose cache lookups with operations in an `andThen` block:

```afl
facet AfricaIndividually() => (cache: [OSMCache]) andThen {
    algeria = Algeria()                            // cache lookup
    dl_algeria = Download(cache = algeria.cache)   // PBF download

    // ... more countries

    yield AfricaIndividually(cache = dl_algeria.cache ++ ...)
}
```

Steps within an `andThen` block execute based on data dependencies. All cache lookups run in parallel, and each download runs as soon as its cache step completes.

## Tests

```bash
# Downloader unit tests (PBF and shapefile)
PYTHONPATH=. python -m pytest examples/osm-geocoder/test_downloader.py -v

# All OSM geocoder tests
PYTHONPATH=. python -m pytest examples/osm-geocoder/ -v
```

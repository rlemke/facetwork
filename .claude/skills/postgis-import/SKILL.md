---
name: postgis-import
description: PostGIS data management and the local-first import flow for large OSM/PBF imports — vacuum/tune/kill-vacuum, the disposable Docker import instance (FW_IMPORT_POSTGIS_URL), and its staged COPY-transfer pipeline.
---

# PostGIS data management

PostGIS data directory: `/Volumes/afl_data/local_servers/postgis/data`. Start with `fw db pg-start`, tune with `fw db postgis tune`. After large import batches, run `fw db postgis vacuum` to reclaim space and update statistics. During bulk imports, autovacuum may compete for I/O — kill it with `fw db postgis kill-vacuum`. Tables have `autovacuum_analyze_threshold = 1,000,000` to reduce frequency during imports.

# Local-first PostGIS import

For large imports, a disposable Docker-based PostgreSQL instance can absorb the hours of PBF parsing I/O, then bulk-transfer the finished data to the main server. This isolates the main server from sustained write pressure during imports.

```bash
# Start the local import instance (Docker, port 5433)
fw db import-pg
fw db import-pg --status       # check if running
fw db import-pg --stop         # stop and remove

# Enable local-first import (add to .env or runner.env)
FW_IMPORT_POSTGIS_URL=postgresql://afl_osm:afl_osm_2024@localhost:5433/osm
```

When `FW_IMPORT_POSTGIS_URL` is set, `import_to_postgis()` follows this flow:
1. Check prior-import log on the **main** server (skip if already imported)
2. Parse PBF and stage data on the **local** instance (fast — disposable, no WAL)
3. Merge staging into local main tables (no index contention with readers)
4. Transfer via `COPY` binary stream from local to main server staging tables
5. Batched merge into main server tables (upsert or plain insert)
6. Write audit log on the **main** server

The local instance is tuned with `fsync=off`, `synchronous_commit=off`, and `autovacuum=off` — it's disposable, so crash recovery is simply re-importing from PBF. Works on the same host as the main PostgreSQL (different port) or on a separate machine.

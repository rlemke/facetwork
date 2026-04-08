# Long-Running Handlers: Database Imports, Heartbeats, and Timeouts

This guide covers how to write handlers that perform long-running operations
(bulk database imports, large file processing, multi-hour computations) without
being killed by the runtime's timeout and stuck-task detection systems.

## The Problem

The AgentFlow runtime has two watchdog systems that detect and reclaim stuck tasks:

| Watchdog | Env Variable | Default | What It Checks |
|----------|-------------|---------|----------------|
| **Execution timeout** | `AFL_TASK_EXECUTION_TIMEOUT_MS` | 900,000 (15 min) | Time since last heartbeat on the runner's thread pool |
| **Stuck task reaper** | `AFL_STUCK_TIMEOUT_MS` | 1,800,000 (30 min) | `max(task_heartbeat, updated)` in MongoDB |

If your handler blocks for longer than these thresholds without signaling
progress, the runtime will:

1. Reset the task to `pending` (so another runner can claim it)
2. Increment `retry_count`
3. After 5 resets (default `max_retries`): move to `dead_letter`

The handler itself may still be running — it just loses ownership of the task.
When it eventually tries to complete the step, the write fails silently.

## Key Concepts

### Task Heartbeat vs Step Log

Handlers receive two callbacks in the payload:

| Callback | Purpose | Updates |
|----------|---------|---------|
| `_step_log` | Write a log message visible in the dashboard | `step_logs` collection only |
| `_task_heartbeat` | Signal liveness to the watchdogs | `task_heartbeat` field on the task document |

**Writing step logs does NOT keep your task alive.** Only `_task_heartbeat`
updates the timestamp that the stuck task reaper checks. You must call both.

### What Blocks Heartbeats

The handler runs on a single thread. Any blocking call prevents heartbeats
from firing:

- Large SQL `INSERT`/`UPSERT` statements
- `COPY` operations
- Network calls to slow APIs
- File I/O on large datasets
- `apply_file()` in pyosmium (callbacks run on the same thread)

If a single operation blocks for longer than the stuck timeout, the task
will be reaped even though the work is still in progress.

## Pattern: Batched Operations with Heartbeats

Break long-running operations into batches. Between each batch, call
`_task_heartbeat` and optionally `_step_log`.

### Basic Pattern

```python
def handle(payload: dict) -> dict:
    step_log = payload.get("_step_log")
    heartbeat = payload.get("_task_heartbeat")
    
    items = get_work_items()
    batch_size = 10000
    processed = 0
    
    for i in range(0, len(items), batch_size):
        batch = items[i:i + batch_size]
        process_batch(batch)  # blocking call
        processed += len(batch)
        
        # Signal liveness after each batch
        if heartbeat:
            heartbeat(progress_message=f"Processed {processed:,}/{len(items):,}")
        if step_log and processed % (batch_size * 10) == 0:
            step_log(f"Progress: {processed:,}/{len(items):,}")
    
    return {"processed": processed}
```

### Database Import Pattern (Staging Tables)

For bulk database imports, use **staging tables** to isolate the import
from the main tables, then merge in batches:

```python
def handle(payload: dict) -> dict:
    heartbeat = payload.get("_task_heartbeat")
    step_log = payload.get("_step_log")
    conn = get_connection()
    
    # 1. Create an unlogged staging table (no indexes, no WAL)
    staging = f"_staging_{region}_{os.getpid()}"
    cur = conn.cursor()
    cur.execute(f"""
        CREATE UNLOGGED TABLE {staging} (
            id BIGINT, data JSONB, geom geometry(Point, 4326)
        )
    """)
    conn.commit()
    
    try:
        # 2. Bulk load into staging (plain INSERT, no conflicts)
        for batch in read_source_in_batches():
            insert_batch(conn, staging, batch)
            if heartbeat:
                heartbeat(progress_message=f"Loading: {count:,} rows")
        
        # 3. Merge into main table in batches
        #    Add a serial column for windowed iteration
        cur.execute(f"ALTER TABLE {staging} ADD COLUMN _row_id SERIAL")
        conn.commit()
        
        offset = 0
        batch_size = 200_000
        while True:
            cur.execute(f"""
                INSERT INTO main_table (id, data, geom)
                SELECT id, data, geom FROM {staging}
                WHERE _row_id > %s AND _row_id <= %s
                ON CONFLICT (id) DO UPDATE
                SET data = EXCLUDED.data, geom = EXCLUDED.geom
            """, (offset, offset + batch_size))
            
            if cur.rowcount == 0:
                break
            conn.commit()
            offset += batch_size
            
            # Heartbeat between merge batches
            if heartbeat:
                heartbeat(progress_message=f"Merged {offset:,} rows")
        
        # 4. Drop staging table
        cur.execute(f"DROP TABLE {staging}")
        conn.commit()
    except Exception:
        cur.execute(f"DROP TABLE IF EXISTS {staging}")
        conn.commit()
        raise
```

**Why staging tables help:**

| Direct import | Staging + merge |
|--------------|----------------|
| UPSERT checks conflicts on every row | Plain INSERT, no conflict checks |
| Maintains GIST/GIN indexes on every insert | No indexes during load |
| WAL writes for every row | UNLOGGED — no WAL |
| Lock contention with concurrent imports | Each import has its own table |
| Single blocked SQL = no heartbeats | Batched merge = heartbeats between batches |

### Handler Registration for Long-Running Tasks

Register handlers with `timeout_ms=0` so the per-handler timeout doesn't
apply. The global stuck-task reaper becomes the safety net:

```python
def register_handlers(runner) -> None:
    runner.register_handler(
        facet_name="myns.BulkImport",
        timeout_ms=0,  # no per-handler timeout
        module_uri=f"file://{os.path.abspath(__file__)}",
        entrypoint="handle",
    )
```

## Runner Environment Configuration

For examples with long-running handlers, create a `runner.env` file that
overrides the default timeouts. The `start-runner` script sources this
automatically.

```bash
# runner.env — placed in the example directory
AFL_TASK_EXECUTION_TIMEOUT_MS=14400000   # 4 hours
AFL_STUCK_TIMEOUT_MS=14400000            # 4 hours (must match execution timeout)
```

**Both variables must be set.** If you only set `AFL_TASK_EXECUTION_TIMEOUT_MS`,
the stuck task reaper still uses its default 30-minute threshold and will
kill tasks that block for longer than that between heartbeats.

| Variable | Controls | Default |
|----------|----------|---------|
| `AFL_TASK_EXECUTION_TIMEOUT_MS` | Runner's per-task timeout (thread pool) | 900,000 (15 min) |
| `AFL_STUCK_TIMEOUT_MS` | Stuck reaper's last-activity threshold | 1,800,000 (30 min) |
| `AFL_REAPER_TIMEOUT_MS` | Dead-server detection (server heartbeat) | 120,000 (2 min) |
| `AFL_LEASE_DURATION_MS` | Task lease renewed by heartbeat | 300,000 (5 min) |

## Monitoring

The dashboard step detail page shows:

- **Last Heartbeat**: Live counter showing time since the task's last
  `_task_heartbeat` call. This updates in real-time via JavaScript.
- **Duration**: Total time from the first step log to the most recent.

If "Last Heartbeat" grows beyond a few minutes, the handler is in a
blocking operation. If it approaches the stuck timeout threshold, the
task is at risk of being reaped.

## Checklist for Long-Running Handlers

1. **Call `_task_heartbeat`** — not just `_step_log` — between blocking operations
2. **Batch all database writes** — never issue a single SQL statement that runs for more than a few minutes
3. **Use staging tables** for bulk imports to avoid index and lock contention
4. **Set `timeout_ms=0`** on handler registration to disable per-handler timeout
5. **Set both timeout env vars** in `runner.env` to accommodate your longest expected operation
6. **Test with concurrent imports** — contention issues only appear under parallel load
7. **Clean up on failure** — drop staging tables in exception handlers

## Reference

- [Agent SDK Specification](../reference/agent-sdk.md) — full handler API
- [Runtime Reference](../reference/runtime.md) — task lifecycle and timeouts
- [PostGIS importer](../../examples/osm-geocoder/handlers/downloads/postgis_importer.py) — production example of staging tables + batched merge

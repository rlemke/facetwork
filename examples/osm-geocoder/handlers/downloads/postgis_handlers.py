"""PostGIS import event facet handler for OSM data.

Handles the PostGisImport and PostGisImportBatch event facets defined
in osmoperations.afl under the osm.ops namespace.
"""

import logging
import os
import traceback

from .postgis_importer import HAS_OSMIUM, HAS_PSYCOPG2


def _format_exc_for_step_log(exc: BaseException) -> str:
    """Render an exception as a compact multi-line string for the step log.

    Includes the fully-qualified exception type, the message, and a short
    traceback. `str(exc)` alone can be empty or ambiguous for some driver
    errors — this guarantees the step log always has actionable detail.
    """
    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)).rstrip()
    return f"{type(exc).__module__}.{type(exc).__qualname__}: {exc}\n{tb}"

log = logging.getLogger(__name__)

NAMESPACE = "osm.ops"


def _postgis_import_handler(payload: dict) -> dict:
    """Handle the PostGisImport event facet.

    Extracts cache metadata from the payload, imports the PBF file
    into PostGIS for a specific region, and returns stats in OSMCache-shaped format.
    """
    cache = payload.get("cache", {})
    pbf_path = cache.get("path", "")
    source_url = cache.get("url", "")
    region = payload.get("region", "")
    force_raw = payload.get("force", False)
    force = force_raw if isinstance(force_raw, bool) else str(force_raw).lower() in ("true", "1", "yes")
    step_log = payload.get("_step_log")
    task_heartbeat = payload.get("_task_heartbeat")

    # Signal liveness immediately so heartbeat is established early
    if task_heartbeat:
        try:
            task_heartbeat(progress_message=f"PostGisImport starting: {region or 'unknown'}")
        except Exception:
            log.warning("PostGisImport: initial heartbeat failed", exc_info=True)

    if step_log:
        step_log(f"PostGisImport: processing {region or 'unknown'} from {source_url or pbf_path}")
    log.info("PostGisImport processing region=%s cache=%s", region, source_url or pbf_path)

    if not HAS_OSMIUM or not HAS_PSYCOPG2 or not pbf_path:
        msg = (
            f"PostGisImport: skipping import — missing dependencies "
            f"(osmium={HAS_OSMIUM}, psycopg2={HAS_PSYCOPG2}, path={bool(pbf_path)})"
        )
        log.warning(msg)
        if step_log:
            step_log(msg, level="warning")
        return {
            "stats": {
                "url": source_url,
                "path": "",
                "date": cache.get("date", ""),
                "size": 0,
                "wasInCache": False,
            }
        }

    try:
        from .postgis_importer import import_to_postgis

        result = import_to_postgis(
            pbf_path,
            source_url=source_url,
            region=region,
            force=force,
            step_log=step_log,
            task_heartbeat=task_heartbeat,
        )
        if step_log:
            if result.was_prior_import and not force:
                step_log(
                    f"PostGisImport: region '{region}' already imported "
                    f"({result.node_count} nodes, {result.way_count} ways), skipped",
                )
            else:
                step_log(
                    f"PostGisImport: imported {result.node_count + result.way_count} elements "
                    f"(nodes={result.node_count}, ways={result.way_count}, region={region})",
                    level="success",
                )
        return {
            "stats": {
                "url": source_url,
                "path": result.postgis_url,
                "date": result.imported_at,
                "size": result.node_count + result.way_count,
                "wasInCache": result.was_prior_import,
            }
        }
    except Exception as exc:
        log.exception("PostGisImport failed for region=%s path=%s", region, pbf_path)
        if step_log:
            step_log(
                f"PostGisImport: FAILED for region '{region}' (pbf={pbf_path}):\n"
                f"{_format_exc_for_step_log(exc)}",
                level="error",
            )
        raise


def _postgis_import_batch_handler(payload: dict) -> dict:
    """Handle the PostGisImportBatch event facet.

    Imports multiple regions from their cached PBF files into PostGIS
    sequentially. Each region is cached and imported independently.
    """
    regions = payload.get("regions", [])
    force_raw = payload.get("force", False)
    force = force_raw if isinstance(force_raw, bool) else str(force_raw).lower() in ("true", "1", "yes")
    step_log = payload.get("_step_log")
    task_heartbeat = payload.get("_task_heartbeat")

    if step_log:
        step_log(f"PostGisImportBatch: importing {len(regions)} regions")
    log.info("PostGisImportBatch: %d regions, force=%s", len(regions), force)

    if not HAS_OSMIUM or not HAS_PSYCOPG2:
        log.warning(
            "PostGisImportBatch: skipping (osmium=%s, psycopg2=%s)",
            HAS_OSMIUM,
            HAS_PSYCOPG2,
        )
        return {"stats": {"url": "", "path": "", "date": "", "size": 0, "wasInCache": False}}

    from ..shared.downloader import download
    from .postgis_importer import import_to_postgis

    total_nodes = 0
    total_ways = 0
    imported = 0
    skipped = 0

    for region_name in regions:
        try:
            cache = download(region_name)
            pbf_path = cache.get("path", "")
            source_url = cache.get("url", "")
            if not pbf_path:
                log.warning("PostGisImportBatch: no PBF for region '%s', skipping", region_name)
                continue

            result = import_to_postgis(
                pbf_path,
                source_url=source_url,
                region=region_name,
                force=force,
                step_log=step_log,
                task_heartbeat=task_heartbeat,
            )
            total_nodes += result.node_count
            total_ways += result.way_count
            if result.was_prior_import and not force:
                skipped += 1
            else:
                imported += 1

            if step_log:
                step_log(
                    f"PostGisImportBatch: {region_name} done "
                    f"({result.node_count} nodes, {result.way_count} ways)"
                )
        except Exception as exc:
            log.exception("PostGisImportBatch: failed for region '%s'", region_name)
            if step_log:
                step_log(
                    f"PostGisImportBatch: FAILED for region '{region_name}':\n"
                    f"{_format_exc_for_step_log(exc)}",
                    level="error",
                )
            raise

    if step_log:
        step_log(
            f"PostGisImportBatch: complete — {imported} imported, {skipped} skipped, "
            f"{total_nodes + total_ways} total elements",
            level="success",
        )

    return {
        "stats": {
            "url": "",
            "path": "",
            "date": "",
            "size": total_nodes + total_ways,
            "wasInCache": skipped > 0,
        }
    }


_DISPATCH: dict[str, callable] = {
    f"{NAMESPACE}.PostGisImport": _postgis_import_handler,
    f"{NAMESPACE}.PostGisImportBatch": _postgis_import_batch_handler,
}


def handle(payload: dict) -> dict:
    """RegistryRunner dispatch entrypoint."""
    facet_name = payload["_facet_name"]
    handler = _DISPATCH.get(facet_name)
    if handler is None:
        raise ValueError(f"Unknown facet: {facet_name}")
    return handler(payload)


def register_postgis_handlers(poller) -> None:
    """Register PostGIS event facet handlers with the poller."""
    if not HAS_OSMIUM:
        return
    for facet_name, handler in _DISPATCH.items():
        poller.register(facet_name, handler)


def register_handlers(runner) -> None:
    """Register all facets with a RegistryRunner."""
    # PostGIS imports can take hours for large regions — use 0 (no per-handler
    # timeout) and rely on heartbeat + the global stuck-task watchdog instead.
    for facet_name in _DISPATCH:
        runner.register_handler(
            facet_name=facet_name,
            timeout_ms=0,
            module_uri=f"file://{os.path.abspath(__file__)}",
            entrypoint="handle",
        )

"""Server catalog (``servers.json``) — the fleet's machines by stable name.

See :mod:`facetwork.servers.catalog`.
"""

from .catalog import (  # noqa: F401
    alias_map,
    catalog_source,
    find,
    infra,
    load_catalog,
    resolve_ip,
    servers,
)

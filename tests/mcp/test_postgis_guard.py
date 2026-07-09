"""Guard for the read-only PostGIS query tool (`_reject_reason`).

Regression tests for the bypasses the old `_FORBIDDEN_SQL`-only guard let
through: `set_config(...)`, multi-statement payloads, `SELECT ... INTO`,
writable CTEs, and `pg_sleep` DoS — while keeping legitimate SELECTs (incl.
semicolons/keywords inside string literals) allowed.
"""
import pytest

from facetwork.mcp.server import _reject_reason


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM osm_nodes LIMIT 10",
        "select osm_id, tags->>'name' FROM osm_nodes WHERE tags ? 'amenity'",
        "WITH r AS (SELECT osm_id FROM osm_ways) SELECT count(*) FROM r",
        # semicolons / keywords INSIDE string literals must not trip the checks
        "SELECT * FROM osm_nodes WHERE tags->>'name' = 'a;b'",
        "SELECT * FROM osm_nodes WHERE tags->>'name' = 'St. John''s'",
        "SELECT * FROM osm_nodes WHERE tags->>'note' = 'delete this later'",
        "SELECT * FROM osm_nodes;",  # single trailing semicolon is fine
        "/* comment */ SELECT 1",
    ],
)
def test_allows_read_queries(sql):
    assert _reject_reason(sql) is None, sql


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT set_config('transaction_read_only','off',false)",          # setting escape
        "SELECT 1; DROP TABLE osm_nodes",                                   # multi-statement
        "SELECT 1 INTO public.pwned",                                       # SELECT INTO create
        "SELECT * INTO pwned FROM osm_nodes",
        "WITH x AS (DELETE FROM osm_nodes RETURNING *) SELECT * FROM x",    # writable CTE
        "DELETE FROM osm_nodes",                                            # not SELECT/WITH
        "UPDATE osm_nodes SET tags = '{}'",
        "SET default_transaction_read_only = off",                         # top-level SET
        "SELECT pg_sleep(60)",                                             # DoS
        "SELECT pg_read_file('/etc/passwd')",                              # file read
        "COPY osm_nodes TO '/tmp/x'",
        "  ",                                                              # empty-ish → not a read query
    ],
)
def test_rejects_writes_and_bypasses(sql):
    assert _reject_reason(sql) is not None, sql

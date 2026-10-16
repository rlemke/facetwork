"""Tests for catalog -> compose block generation (facetwork.domains.compose_gen)."""

from __future__ import annotations

import pytest

from facetwork.domains import compose_gen
from facetwork.domains.compose_gen import BEGIN, END

_COMPOSE = f"""\
services:
  mongodb:
    image: mongo:7
{BEGIN}{END}
  runner-ffl:
    image: x
"""


@pytest.fixture
def compose(tmp_path, monkeypatch):
    f = tmp_path / "docker-compose.full-stack.yml"
    f.write_text(_COMPOSE, encoding="utf-8")
    monkeypatch.setattr(compose_gen, "COMPOSE", f)
    return f


def _catalog(monkeypatch, mapping):
    monkeypatch.setattr(compose_gen, "compose_domains", lambda: mapping)


def test_render_standard_block():
    blk = compose_gen.render_block(
        "noaa-weather",
        {
            "service": "runner-noaa-weather",
            "repo": "fwh_noaa_weather",
            "task_list": "weather",
        },
    )
    assert "  runner-noaa-weather:\n" in blk
    assert "      <<: *s3-storage\n" in blk
    assert "      FW_DOMAIN_NAME: noaa-weather\n" in blk
    assert "      FW_DOMAIN_REPO: fwh_noaa_weather\n" in blk
    assert '      FW_REGISTRY_RUNNER_ARGS: "--task-list weather"\n' in blk
    assert (
        "      - ${FWH_HANDLERS_ROOT:-$HOME/fw_handlers}/fwh_noaa_weather:/handlers/fwh_noaa_weather\n"
        in blk
    )
    assert "      - ${FW_DATA_DIR:-/Volumes/afl_data}:/Volumes/afl_data\n" in blk  # default volume
    assert "    restart: unless-stopped\n" in blk


def test_render_osm_block_structural_fields():
    blk = compose_gen.render_block(
        "osm-geocoder",
        {
            "service": "runner-osm-geocoder",
            "repo": "fwh_osm",
            "task_list": "osm",
            "server_group_arg": True,
            "compose": {
                "storage": "osm",
                "hostname": True,
                "postgis": True,
                "depends": [["postgis", "service_healthy"]],
                "env": [["FW_GEOFABRIK_MIRROR", "/geofabrik"]],
                "volumes": ["afl_output:/data/output"],
                "notes": ["scratch must stay on the big disk"],
            },
        },
    )
    assert "    hostname: ${FW_FLEET_HOST:-}\n" in blk
    assert "      <<: *osm-s3-env\n" in blk
    assert "      postgis:\n        condition: service_healthy\n" in blk
    assert "      FW_POSTGIS_URL: postgresql://" in blk
    assert "      FW_GEOFABRIK_MIRROR: /geofabrik\n" in blk
    assert (
        '      FW_REGISTRY_RUNNER_ARGS: "--task-list osm --server-group ${FW_SERVER_GROUP:-default}"\n'
        in blk
    )
    assert "      - afl_output:/data/output\n" in blk
    assert "    # scratch must stay on the big disk\n" in blk
    # default data-dir volume is NOT added when explicit volumes are given
    assert "/Volumes/afl_data:/Volumes/afl_data" not in blk


def test_extras_with_var():
    blk = compose_gen.render_block(
        "anthropic",
        {
            "service": "runner-anthropic",
            "repo": "fwh_anthropic",
            "task_list": "anthropic",
            "compose_extras": "agent_sdk,mcp",
            "extras_var": "ANTHROPIC_EXTRAS",
            "compose": {"env": [["ANTHROPIC_API_KEY", "${ANTHROPIC_API_KEY:-}"]]},
        },
    )
    assert "      FW_DOMAIN_EXTRAS: ${ANTHROPIC_EXTRAS:-agent_sdk,mcp}\n" in blk
    assert "      ANTHROPIC_API_KEY: ${ANTHROPIC_API_KEY:-}\n" in blk


def test_sync_fills_region_and_is_idempotent(compose, monkeypatch):
    _catalog(
        monkeypatch,
        {
            "noaa-weather": {
                "service": "runner-noaa-weather",
                "repo": "fwh_noaa_weather",
                "task_list": "weather",
            },
        },
    )
    changed, _ = compose_gen.sync(write=True)
    assert changed
    text = compose.read_text()
    assert "  runner-noaa-weather:\n" in text  # block landed in the region
    assert "  mongodb:\n" in text and "  runner-ffl:\n" in text  # outside-region untouched
    # idempotent: a second sync makes no change
    changed2, _ = compose_gen.sync(write=True)
    assert not changed2


def test_check_detects_drift(compose, monkeypatch):
    _catalog(monkeypatch, {"a": {"service": "runner-a", "repo": "fwh_a", "task_list": "a"}})
    compose_gen.sync(write=True)
    # change the catalog -> region now out of sync
    _catalog(monkeypatch, {"a": {"service": "runner-a", "repo": "fwh_a", "task_list": "a2"}})
    changed, _ = compose_gen.sync(write=False)
    assert changed  # --check would exit non-zero


def test_missing_markers_errors(tmp_path, monkeypatch):
    f = tmp_path / "docker-compose.full-stack.yml"
    f.write_text("services:\n  mongodb: {}\n", encoding="utf-8")
    monkeypatch.setattr(compose_gen, "COMPOSE", f)
    _catalog(monkeypatch, {})
    with pytest.raises(SystemExit):
        compose_gen.sync(write=False)


def test_generalist_carries_every_member_domains_env(monkeypatch):
    _catalog(
        monkeypatch,
        {
            "census-us": {"service": "runner-census-us", "repo": "r", "task_list": "census",
                          "compose": {"env": [["CENSUS_API_KEY", "${CENSUS_API_KEY:-}"]]}},
            "county-atlas": {"service": "runner-county-atlas", "repo": "r", "task_list": "county",
                             "compose": {"env": [["CENSUS_API_KEY", "${CENSUS_API_KEY:-}"],
                                                 ["FW_TASK_EXECUTION_TIMEOUT_MS", "1800000"]]}},
            "slow": {"service": "runner-slow", "repo": "r", "task_list": "slow",
                     "compose": {"env": [["FW_TASK_EXECUTION_TIMEOUT_MS", "900000"]]}},
            "osm-lz": {"service": "runner-osm-lz", "repo": "r", "task_list": "osm",
                       "compose": {"storage": "osm", "env": [["FW_PLANET_DIR", "/planet"]]}},
        },
    )
    blk = compose_gen._render_generalist(["census-us"])
    assert blk.count("      CENSUS_API_KEY: ") == 1
    assert "      FW_TASK_EXECUTION_TIMEOUT_MS: 1800000\n" in blk  # the longest wins
    assert "FW_PLANET_DIR" not in blk  # the osm tier is never a member


def test_generalist_refuses_conflicting_env(monkeypatch):
    _catalog(
        monkeypatch,
        {
            "a": {"service": "runner-a", "repo": "r", "task_list": "a",
                  "compose": {"env": [["MODE", "x"]]}},
            "b": {"service": "runner-b", "repo": "r", "task_list": "b",
                  "compose": {"env": [["MODE", "y"]]}},
        },
    )
    with pytest.raises(SystemExit):
        compose_gen._render_generalist(["a", "b"])

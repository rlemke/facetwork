"""Unit tests for fleet server-group role gating (`_fleet_lib` helpers).

`server_groups` lets a deployment restrict which central roles a host brings up
so heavy domains stay off under-provisioned machines. The gate decides only what
a host *starts*; task routing remains correct regardless (a runner claims only
tasks it has a handler for). These tests pin the pure logic:

- a role with no `server_groups` runs everywhere (backward compatible);
- a role with `server_groups` runs only on a host whose group is listed;
- the `ROLE:g1,g2` CLI parse (and `ROLE:` clear).
"""

import importlib.util
from pathlib import Path

import pytest

_FLEET_LIB = (
    Path(__file__).resolve().parent.parent / "scripts" / "lib" / "_helpers" / "_fleet_lib.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("_fleet_lib_under_test", _FLEET_LIB)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


fl = _load()


# --- role_in_group ---------------------------------------------------------


def test_role_with_no_groups_runs_everywhere():
    # The backward-compatible default: a fleet that never sets server_groups
    # behaves exactly as before — every role on every host.
    assert fl.role_in_group({}, "runner") is True
    assert fl.role_in_group({"replicas": 4}, "anything") is True
    assert fl.role_in_group({"server_groups": []}, "runner") is True


def test_role_runs_only_on_listed_groups():
    spec = {"server_groups": ["heavy", "gpu"]}
    assert fl.role_in_group(spec, "heavy") is True
    assert fl.role_in_group(spec, "gpu") is True
    assert fl.role_in_group(spec, "light") is False
    assert fl.role_in_group(spec, "runner") is False


def test_host_server_group_default_and_override(monkeypatch):
    monkeypatch.delenv("AFL_SERVER_GROUP", raising=False)
    assert fl.host_server_group() == "runner"
    monkeypatch.setenv("AFL_SERVER_GROUP", "heavy")
    assert fl.host_server_group() == "heavy"
    # An empty value falls back to the default rather than gating to "".
    monkeypatch.setenv("AFL_SERVER_GROUP", "")
    assert fl.host_server_group() == "runner"


def test_gate_end_to_end_for_a_host():
    # A 'light' host with osm restricted to 'heavy' skips osm but keeps an
    # ungrouped domain runner.
    roles = {
        "osm-geocoder": {"replicas": 4, "server_groups": ["heavy"]},
        "noaa-weather": {"kind": "domain", "replicas": 1},  # everywhere
        "census-us": {"kind": "domain", "replicas": 1, "server_groups": ["light"]},
    }
    here = {n: fl.role_in_group(s, "light") for n, s in roles.items()}
    assert here == {"osm-geocoder": False, "noaa-weather": True, "census-us": True}


# --- parse_role_groups -----------------------------------------------------


def test_parse_role_groups_basic():
    assert fl.parse_role_groups("osm-geocoder:heavy,gpu") == ("osm-geocoder", ["heavy", "gpu"])
    assert fl.parse_role_groups("census-us:light") == ("census-us", ["light"])


def test_parse_role_groups_clear():
    # `ROLE:` with no groups clears the restriction (empty list = everywhere).
    assert fl.parse_role_groups("osm-geocoder:") == ("osm-geocoder", [])


def test_parse_role_groups_trims_and_drops_blanks():
    assert fl.parse_role_groups("r: a , , b ") == ("r", ["a", "b"])


def test_parse_role_groups_rejects_missing_colon():
    with pytest.raises(ValueError):
        fl.parse_role_groups("osm-geocoder")
    with pytest.raises(ValueError):
        fl.parse_role_groups(":heavy")

"""_osm_admin_scope.py refuses a set the catalogue marks unschedulable.

russia-districts is kept as a RECORD (verified to produce nothing) with
"do not schedule" in its description -- prose nothing read, so it was submitted
anyway on 2026-09-30 and re-proved the result at the cost of ~3 host-hours.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location(
        "_osm_admin_scope_under_test", REPO / "scripts/lib/svc/_osm_admin_scope.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_an_unschedulable_set_is_refused_before_any_lookup(tmp_path, monkeypatch, capsys):
    sets = tmp_path / "sets.json"
    sets.write_text(
        json.dumps(
            {
                "sets": {
                    "never": {
                        "description": "verified to produce nothing",
                        "schedulable": False,
                        "workflow": "x.Y",
                        "inputs": {},
                        "requires_fresh": ["a-latest.osm.pbf"],
                    }
                }
            }
        )
    )
    monkeypatch.setenv("FW_OSM_REGEN_SETS_FILE", str(sets))
    mod = _load()
    monkeypatch.setattr(mod, "_s3", lambda: (_ for _ in ()).throw(AssertionError("no lookup")))
    monkeypatch.setattr(sys, "argv", ["scope", "--set", "never"])
    assert mod.main() == 1
    assert "unschedulable" in capsys.readouterr().err


def test_the_real_catalogue_marks_russia_districts_unschedulable():
    cfg = json.loads((REPO / "scripts/lib/svc/_osm-admin-sets.json").read_text())
    assert cfg["sets"]["russia-districts"]["schedulable"] is False

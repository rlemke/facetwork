"""Tests for the sentinel2-landchange example.

Covers: (1) the FFL parses + validates + compiles and exposes the expected
workflows/facets; (2) the offline mock chain (search -> per-scene index ->
composite x2 -> change -> render) runs end-to-end and produces coherent stats +
an HTML map; (3) the handlers dispatch with use_mock.

All cache/output writes go to a tmp dir via AFL_CACHE_ROOT/AFL_DATA_ROOT, so the
suite needs no network, GDAL, or external storage.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

_EXAMPLE_ROOT = Path(__file__).resolve().parents[1]
_FFL = _EXAMPLE_ROOT / "ffl" / "sentinel2_landchange.ffl"
_TOOLS = _EXAMPLE_ROOT / "tools"


# ── FFL compilation ───────────────────────────────────────────────────────────


def _compile() -> dict:
    from facetwork.emitter import emit_dict
    from facetwork.parser import FFLParser
    from facetwork.source import CompilerInput, FileOrigin, SourceEntry
    from facetwork.validator import validate

    entry = SourceEntry(text=_FFL.read_text(), origin=FileOrigin(path=str(_FFL)), is_library=False)
    program_ast, _registry = FFLParser().parse_sources(CompilerInput(primary_sources=[entry]))
    result = validate(program_ast)
    assert not result.errors, "; ".join(str(e) for e in result.errors)
    return emit_dict(program_ast, include_locations=False)


def test_ffl_compiles_and_validates():
    prog = _compile()
    assert prog  # non-empty


def test_ffl_defines_expected_workflows_and_facets():
    text = _FFL.read_text()
    for name in ["AnalyzeAOI", "ScanScenes", "SearchScenes", "FetchSceneIndex",
                 "Composite", "DetectChange", "ChangeMap"]:
        assert name in text, f"missing declaration {name}"


# ── offline mock chain through _s2_tools ───────────────────────────────────────


@pytest.fixture
def tools_env(tmp_path, monkeypatch):
    monkeypatch.setenv("AFL_DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("AFL_CACHE_ROOT", str(tmp_path / "cache"))
    monkeypatch.setenv("AFL_OUTPUT_BASE", str(tmp_path / "output"))
    if str(_TOOLS) not in sys.path:
        sys.path.insert(0, str(_TOOLS))
    # Re-import fresh so cached modules don't leak roots between tests.
    for mod in [m for m in sys.modules if m == "_s2_tools" or m.startswith("_s2_tools.")]:
        del sys.modules[mod]
    return importlib.import_module("_s2_tools")


def test_mock_chain_end_to_end(tools_env):
    from _s2_tools import map_render, raster, stac

    aoi = "-122.55,37.70,-122.35,37.85"
    # baseline epoch
    base_scenes = stac.search(aoi, "2018-06-01", "2018-09-30", use_mock=True)
    assert base_scenes
    for s in base_scenes:
        raster.fetch_scene_index(s["scene_id"], aoi, index="ndvi", use_mock=True)
    base = raster.composite(aoi, "2018-06-01", "2018-09-30", index="ndvi", use_mock=True)
    assert base["scene_count"] == len(base_scenes)

    # idempotent: a second fetch is a cache hit
    again = raster.fetch_scene_index(base_scenes[0]["scene_id"], aoi, index="ndvi", use_mock=True)
    assert again["was_cached"] is True

    # recent epoch
    recent_scenes = stac.search(aoi, "2024-06-01", "2024-09-30", use_mock=True)
    for s in recent_scenes:
        raster.fetch_scene_index(s["scene_id"], aoi, index="ndvi", use_mock=True)
    recent = raster.composite(aoi, "2024-06-01", "2024-09-30", index="ndvi", use_mock=True)

    change = raster.detect_change(base["relative_path"], recent["relative_path"],
                                  base["aoi_key"], method="difference", threshold=0.15, use_mock=True)
    assert change["total_pixels"] == 16 * 16
    assert change["changed_pixels"] == change["class_counts"]["loss"] + change["class_counts"]["gain"]
    assert 0.0 <= change["pct_loss"] <= 100.0

    bundle = map_render.render_change_map(change["relative_path"], change["aoi_key"], detail="test")
    assert Path(bundle["html_path"]).is_file()
    assert "land-cover change" in Path(bundle["html_path"]).read_text()


def test_unknown_index_rejected(tools_env):
    from _s2_tools import raster

    with pytest.raises(ValueError):
        raster.fetch_scene_index("S2_X", "-1,-1,1,1", index="bogus", use_mock=True)


def test_real_path_is_not_implemented(tools_env):
    from _s2_tools import stac

    with pytest.raises(NotImplementedError):
        stac.search("-1,-1,1,1", "2024-01-01", "2024-02-01", use_mock=False)


# ── handler dispatch ───────────────────────────────────────────────────────────


def test_handlers_dispatch_with_mock(tools_env):
    sys.path.insert(0, str(_EXAMPLE_ROOT))
    for mod in [m for m in sys.modules if m == "handlers" or m.startswith("handlers.")]:
        del sys.modules[mod]
    from handlers.analyze.analyze_handlers import handle_composite
    from handlers.source.source_handlers import handle_fetch_scene_index, handle_search_scenes

    aoi = "-122.55,37.70,-122.35,37.85"
    res = handle_search_scenes({"aoi": aoi, "date_from": "2018-06-01",
                                "date_to": "2018-09-30", "use_mock": True})
    assert res["count"] == len(res["scene_ids"]) > 0
    for sid in res["scene_ids"]:
        handle_fetch_scene_index({"scene_id": sid, "aoi": aoi, "index": "ndvi", "use_mock": True})
    comp = handle_composite({"aoi": aoi, "date_from": "2018-06-01",
                             "date_to": "2018-09-30", "index": "ndvi", "use_mock": True})
    assert comp["cache_type"] == "composite" and comp["scene_count"] == res["count"]

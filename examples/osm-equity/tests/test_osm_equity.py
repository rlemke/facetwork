"""Tests for the osm.equity mapping-equity study.

Covers three levels:
  1. the FFL compiles + validates (relative-scoping model);
  2. the computational utils are correct and deterministic;
  3. an END-TO-END run driving every handler in the workflow's data order
     recovers the built-in digital-divide signal (income correlates with
     OSM quality) and produces a report + data-desert list.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

_EXAMPLE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_REPO_ROOT = os.path.abspath(os.path.join(_EXAMPLE_ROOT, "..", ".."))
_FFL = os.path.join(_EXAMPLE_ROOT, "ffl", "osm_equity.ffl")


# ---------------------------------------------------------------------------
# 1. FFL compiles & validates
# ---------------------------------------------------------------------------
class TestFFL:
    def test_ffl_validates(self):
        r = subprocess.run(
            [sys.executable, "-m", "facetwork.cli", _FFL, "--check"],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
        )
        assert r.returncode == 0, r.stdout + r.stderr
        assert "OK" in (r.stdout + r.stderr)

    def test_ffl_emits_json(self, tmp_path):
        out = tmp_path / "equity.json"
        r = subprocess.run(
            [sys.executable, "-m", "facetwork.cli", _FFL, "-o", str(out)],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
        )
        assert r.returncode == 0, r.stdout + r.stderr
        assert out.exists()


# ---------------------------------------------------------------------------
# 2. Utility correctness / determinism
# ---------------------------------------------------------------------------
class TestUtils:
    def test_tracts_tile_the_grid(self):
        from handlers.shared import equity_utils as U

        tracts = U.make_tracts("Oakland, CA")
        n = U.grid_size()
        assert len(tracts) == n * n
        assert all("geometry_wkt" in t and t["area_km2"] > 0 for t in tracts)

    def test_income_gradient_is_spatial(self):
        from handlers.shared import equity_utils as U

        # NE corner richer than SW corner (smooth gradient => Moran signal)
        sw = U.tract_profile("06001" + "0000")["median_income"]
        ne = U.tract_profile(f"06001{U.grid_size() - 1:02d}{U.grid_size() - 1:02d}")[
            "median_income"
        ]
        assert ne > sw

    def test_census_equity_determinism(self):
        from handlers.shared import equity_utils as U

        a = U.fetch_census_equity("060010203", 2022)
        b = U.fetch_census_equity("060010203", 2022)
        assert a == b
        assert 0 <= a["pct_internet_subscription"] <= 1

    def test_update_gating_reuses_cache(self, tmp_path, monkeypatch):
        from handlers.shared import equity_utils as U

        monkeypatch.setenv("FW_CACHE_ROOT", str(tmp_path))
        tracts = U.make_tracts("Detroit, MI")
        p1, s1, cached1 = U.fetch_region_osm("Detroit, MI", update=False, tracts=tracts)
        assert cached1 is False  # first fetch builds it
        p2, s2, cached2 = U.fetch_region_osm("Detroit, MI", update=False, tracts=tracts)
        assert cached2 is True and s2 == s1  # reused, no re-fetch
        _, s3, cached3 = U.fetch_region_osm("Detroit, MI", update=True, tracts=tracts)
        assert cached3 is False  # update flag forces a re-fetch

    def test_extrinsic_gating_no_reference(self):
        from handlers.shared import equity_utils as U

        q = U.compute_extrinsic_quality("nonexistent", "POINT (0 0)", "", False, "060010000")
        assert q["has_reference"] is False
        assert q["footprint_completeness"] == -1.0  # explicit N/A, not silently 0

    def test_footprint_source_unknown_is_unavailable(self, tmp_path, monkeypatch):
        from handlers.shared import equity_utils as U

        monkeypatch.setenv("FW_CACHE_ROOT", str(tmp_path))
        tracts = U.make_tracts("Atlanta, GA")
        path, avail = U.fetch_region_footprints("Atlanta, GA", "no_such_source", False, tracts)
        assert avail is False and path == ""


# ---------------------------------------------------------------------------
# 2b. Tile analysis units (offline-pure: no network)
# ---------------------------------------------------------------------------
class TestTiling:
    def test_build_tiles_are_two_sqmi(self):
        from handlers.shared import sources_real as R

        tiles = R.build_tiles((-122.52, 37.70, -122.36, 37.83), sqmi=2.0)
        assert len(tiles) > 20
        full = [t["area_km2"] for t in tiles if t["area_km2"] > 5.0]
        assert full and all(abs(a - 2 * 2.589988) < 0.05 for a in full)  # ~5.18 km2
        assert len({t["geoid"] for t in tiles}) == len(tiles)  # unique ids

    def test_areal_interpolation_area_weights(self):
        from handlers.shared import sources_real as R

        # two side-by-side tracts, a tile straddling both 50/50 -> mean income
        tracts = [
            {"geometry_wkt": "POLYGON ((0 0, 1 0, 1 1, 0 1, 0 0))", "equity": _equity(100000)},
            {"geometry_wkt": "POLYGON ((1 0, 2 0, 2 1, 1 1, 1 0))", "equity": _equity(50000)},
        ]
        tile = {
            "geoid": "TILE_000_000",
            "geometry_wkt": "POLYGON ((0.5 0, 1.5 0, 1.5 1, 0.5 1, 0.5 0))",
        }
        out = R.areal_interpolate_equity([tile], tracts)
        assert abs(out["TILE_000_000"]["median_income"] - 75000) < 1  # 50/50 area weight

    def test_areal_interpolation_skips_no_income(self):
        from handlers.shared import sources_real as R

        tracts = [{"geometry_wkt": "POLYGON ((0 0, 1 0, 1 1, 0 1, 0 0))", "equity": _equity(0)}]
        tile = {"geoid": "T", "geometry_wkt": "POLYGON ((0 0, 1 0, 1 1, 0 1, 0 0))"}
        assert R.areal_interpolate_equity([tile], tracts) == {}  # no populated tract -> omitted


def _equity(income):
    return {
        "geoid": "x",
        "median_income": float(income),
        "pct_rent_burdened": 0.3,
        "pct_bipoc": 0.5,
        "pct_no_hs_diploma": 0.1,
        "pct_limited_english": 0.1,
        "pct_zero_vehicle": 0.2,
        "pct_internet_subscription": 0.8,
    }


# ---------------------------------------------------------------------------
# 2c. Atlas: fan out over cities, combine to ONE map (offline)
# ---------------------------------------------------------------------------
class TestAtlas:
    def test_atlas_fanout_and_combine(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FW_CACHE_ROOT", str(tmp_path))
        from handlers.atlas import atlas_handlers as A

        cities = A.handle_resolve_cities(
            {"regions": ["north-america"], "min_population": 500000, "half_deg": 0.05}
        )["cities"]
        assert len(cities) >= 30 and all(c["population"] >= 500000 for c in cities)

        # fan-out (offline synthetic tiles) over the first 3 cities, aggregate
        records = []
        for c in cities[:3]:
            recs = A.handle_city_tiles({"city": c, "tile_sqmi": 2.0, "acs_year": 2022})["records"]
            assert recs and all(r["geoid"].startswith(A.U._slug(c["name"])) for r in recs)
            records.extend(recs)

        # combine to ONE map
        out = A.handle_build_atlas_map(
            {
                "records": records,
                "stats": {"spearman_rho": 0.5},
                "metric": "attr_completeness",
                "title": "Atlas test",
            }
        )
        assert out["city_count"] == 3
        assert out["tile_count"] == len(records)
        assert os.path.exists(out["map_html"])
        assert "maplibre" in open(out["map_html"]).read().lower()

    def test_resolve_cities_population_floor(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FW_CACHE_ROOT", str(tmp_path))
        from handlers.atlas import atlas_handlers as A

        big = A.handle_resolve_cities({"regions": ["us"], "min_population": 1000000})["cities"]
        assert all(c["population"] >= 1000000 for c in big)
        assert len(big) < 15  # only the largest


# ---------------------------------------------------------------------------
# 3. End-to-end: drive every handler in the workflow's data order
# ---------------------------------------------------------------------------
class TestEndToEnd:
    def _run(
        self,
        tmp_path,
        monkeypatch,
        region="Oakland, CA",
        update=False,
        benchmark="microsoft_open_buildings",
    ):
        monkeypatch.setenv("FW_CACHE_ROOT", str(tmp_path))
        from handlers.acquisition import acquisition_handlers as ACQ
        from handlers.analysis import analysis_handlers as ANA
        from handlers.design import design_handlers as DES
        from handlers.metrics import metrics_handlers as MET
        from handlers.reporting import reporting_handlers as REP

        # Phase 1
        area = DES.handle_define_study_area(
            {"region": region, "scale": "census_tract", "hypothesis": "h", "acs_year": 2022}
        )["area"]
        # Phase 2
        tracts = ACQ.handle_resolve_study_tracts({"area": area})["tracts"]
        osm = ACQ.handle_fetch_region_osm({"region": region, "update": update})
        foot = ACQ.handle_fetch_region_footprints(
            {"region": region, "benchmark_source": benchmark, "update": update}
        )
        # Phases 2+3 fan-out
        records = []
        for t in tracts:
            clip = MET.handle_clip_tract_osm(
                {"region_osm_path": osm["osm_path"], "geometry_wkt": t["geometry_wkt"]}
            )
            eq = MET.handle_fetch_census_equity({"geoid": t["geoid"], "acs_year": 2022})["vars"]
            intr = MET.handle_compute_attribute_quality(
                {"osm_path": clip["osm_path"], "area_km2": t["area_km2"], "geoid": t["geoid"]}
            )["quality"]
            extr = MET.handle_compute_extrinsic_quality(
                {
                    "osm_path": clip["osm_path"],
                    "geometry_wkt": t["geometry_wkt"],
                    "footprints_path": foot["footprints_path"],
                    "reference_available": foot["available"],
                    "geoid": t["geoid"],
                }
            )["quality"]
            rec = MET.handle_assemble_tract_quality(
                {
                    "geoid": t["geoid"],
                    "geometry_wkt": t["geometry_wkt"],
                    "equity": eq,
                    "intrinsic": intr,
                    "extrinsic": extr,
                }
            )["record"]
            records.append(rec)
        # Phase 4
        corr = ANA.handle_spearman({"records": records})
        moran = ANA.handle_morans_i({"records": records})
        gwr = ANA.handle_gwr({"records": records})
        temporal = ANA.handle_temporal({"records": records, "snapshot": osm["snapshot"]})
        stats = ANA.handle_combine_stats(
            {
                "spearman_rho": corr["rho"],
                "spearman_p": corr["pvalue"],
                "moran_i": moran["i"],
                "moran_p": moran["pvalue"],
                "gwr_summary": gwr["summary_path"],
                "gwr_r2": gwr["mean_r2"],
                "temporal_trend": temporal["trend"],
                "temporal_gap_change": temporal["gap_change"],
            }
        )["results"]
        # Phase 5
        report = REP.handle_build_equity_report({"records": records, "stats": stats, "title": "T"})
        return records, stats, report

    def test_full_pipeline_runs_and_reports(self, tmp_path, monkeypatch):
        records, stats, report = self._run(tmp_path, monkeypatch)
        n = __import__("os")  # noqa
        assert len(records) >= 9
        assert os.path.exists(report["report_html"])
        assert os.path.exists(report["map_path"])
        assert "maplibre" in open(report["map_path"]).read().lower()  # interactive heat map
        html = open(report["report_html"]).read()
        assert "Mapping Equity" in html or "Digital Divide" in html or "data desert" in html.lower()
        assert isinstance(report["data_deserts"], list) and len(report["data_deserts"]) >= 1

    def test_digital_divide_signal_recovered(self, tmp_path, monkeypatch):
        # Income was built to drive POI diversity + completeness, so the study
        # must detect a positive, significant relationship and spatial clustering.
        records, stats, _ = self._run(tmp_path, monkeypatch)
        assert stats["spearman_rho"] > 0.3
        assert stats["spearman_pvalue"] < 0.05
        assert stats["morans_i"] > 0.0

    def test_data_deserts_are_low_income(self, tmp_path, monkeypatch):
        records, _, report = self._run(tmp_path, monkeypatch)
        incomes = {r["geoid"]: r["equity"]["median_income"] for r in records}
        overall_median = sorted(incomes.values())[len(incomes) // 2]
        desert_incomes = [incomes[g] for g in report["data_deserts"]]
        assert sum(desert_incomes) / len(desert_incomes) < overall_median

    def test_extrinsic_available_with_real_source(self, tmp_path, monkeypatch):
        records, _, _ = self._run(tmp_path, monkeypatch, benchmark="microsoft_open_buildings")
        assert all(r["extrinsic"]["has_reference"] for r in records)
        assert all(0.0 <= r["extrinsic"]["footprint_completeness"] <= 1.0 for r in records)

    def test_extrinsic_na_without_reference(self, tmp_path, monkeypatch):
        records, _, _ = self._run(tmp_path, monkeypatch, benchmark="no_such_source")
        assert all(r["extrinsic"]["has_reference"] is False for r in records)
        assert all(r["extrinsic"]["footprint_completeness"] == -1.0 for r in records)


# ---------------------------------------------------------------------------
# 4. Live real sources (opt-in; skipped unless FW_EQUITY_LIVE_TEST=1 + key)
# ---------------------------------------------------------------------------
@pytest.mark.skipif(
    os.environ.get("FW_EQUITY_LIVE_TEST") != "1" or not os.environ.get("CENSUS_API_KEY"),
    reason="live test needs FW_EQUITY_LIVE_TEST=1 and CENSUS_API_KEY (network)",
)
class TestRealSources:
    def test_tiger_and_acs_and_overpass(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FW_CACHE_ROOT", str(tmp_path))
        monkeypatch.setenv("FW_EQUITY_SOURCE", "real")
        from handlers.shared import equity_utils as U

        tracts = U.make_tracts("San Francisco, CA")  # TIGERweb
        assert len(tracts) > 50 and all(len(t["geoid"]) == 11 for t in tracts)
        eq = U.fetch_census_equity(tracts[0]["geoid"], 2022)  # ACS
        assert eq["median_income"] >= 0 and 0 <= eq["pct_bipoc"] <= 1
        path, snap, cached = U.fetch_region_osm("San Francisco, CA", False, tracts)  # Overpass
        feats = U._read_geojson(path)
        kinds = {f["properties"]["osm_kind"] for f in feats}
        assert {"building", "road", "poi"} & kinds and len(feats) > 1000

    def test_microsoft_open_buildings(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FW_CACHE_ROOT", str(tmp_path))
        monkeypatch.setenv("FW_EQUITY_SOURCE", "real")
        from handlers.shared import equity_utils as U

        path, available = U.fetch_region_footprints("San Francisco, CA", "microsoft", False, [])
        assert available and path
        refs = U._read_geojson(path)
        assert len(refs) > 1000 and all(r["properties"]["ref"] == "building" for r in refs[:5])

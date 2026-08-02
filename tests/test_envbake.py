"""Tests for bake-time environment materialization (facetwork/envbake.py)."""

from __future__ import annotations

import pytest

from facetwork.envbake import collect_environments, main

GOOD = """
namespace geo {
    environment PyGeo {
        language = "python",
        requires = ["shapely==2.0.4"]
    }
}
"""

SAME = """
namespace geo {
    environment PyGeo {
        language = "python",
        requires = ["shapely==2.0.4"]
    }
}
"""

CONFLICT = """
namespace geo {
    environment PyGeo {
        language = "python",
        requires = ["shapely==2.0.5"]
    }
}
"""


class TestCollect:
    def test_collects_and_freezes(self, tmp_path):
        (tmp_path / "a.ffl").write_text(GOOD)
        (tmp_path / "broken.ffl").write_text("this is not ffl {{{")  # skipped
        envs = collect_environments([str(tmp_path)])
        assert set(envs) == {"geo.PyGeo"}
        assert envs["geo.PyGeo"]["manifest"]["pins"] == ["shapely==2.0.4"]

    def test_duplicate_same_hash_collapses(self, tmp_path):
        (tmp_path / "a.ffl").write_text(GOOD)
        (tmp_path / "b.ffl").write_text(SAME)
        envs = collect_environments([str(tmp_path)])
        assert len(envs) == 1

    def test_conflicting_manifests_fail(self, tmp_path):
        (tmp_path / "a.ffl").write_text(GOOD)
        (tmp_path / "b.ffl").write_text(CONFLICT)
        with pytest.raises(SystemExit, match="conflicting environment"):
            collect_environments([str(tmp_path)])

    def test_missing_root_is_fine(self, tmp_path):
        assert collect_environments([str(tmp_path / "nope")]) == {}


class TestMain:
    def test_check_mode_lists_without_building(self, tmp_path, capsys, monkeypatch):
        monkeypatch.setenv("FW_ENV_ROOT", str(tmp_path / "envs"))
        (tmp_path / "a.ffl").write_text(GOOD)
        rc = main(["--root", str(tmp_path), "--check"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "geo.PyGeo@" in out and "[to build]" in out
        assert not (tmp_path / "envs").exists()

    def test_materializes_empty_pin_env(self, tmp_path, capsys, monkeypatch):
        """An env with no requires builds a bare venv — fast, no network —
        exercising the real materialization path end-to-end."""
        monkeypatch.setenv("FW_ENV_ROOT", str(tmp_path / "envs"))
        (tmp_path / "a.ffl").write_text(
            'namespace t {\n  environment Bare { language = "python" }\n}'
        )
        rc = main(["--root", str(tmp_path)])
        assert rc == 0
        from facetwork.environments import discover_provided_environments

        provided = discover_provided_environments()
        assert len(provided) == 1  # the bare env's hash, interpreter present

    def test_foreign_language_skipped(self, tmp_path, capsys, monkeypatch):
        monkeypatch.setenv("FW_ENV_ROOT", str(tmp_path / "envs"))
        (tmp_path / "a.ffl").write_text(
            'namespace t {\n  environment Jvm { language = "scala", requires = ["x:y:1"] }\n}'
        )
        rc = main(["--root", str(tmp_path)])
        out = capsys.readouterr().out
        assert rc == 0
        assert "SKIP t.Jvm@" in out and "provided-only" in out

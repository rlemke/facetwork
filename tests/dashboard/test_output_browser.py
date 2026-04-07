"""Tests for the output file browser route and helpers."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from afl.config import _reset_config_cache
from afl.dashboard.app import create_app
from afl.dashboard.filters import file_timestamp, filesizeformat
from afl.dashboard.routes.monitoring.output import (
    _build_breadcrumbs,
    _build_tree,
    _safe_path,
)


@pytest.fixture(autouse=True)
def _reset_config(monkeypatch):
    """Reset the config singleton before each test so env var changes take effect."""
    _reset_config_cache()
    yield
    _reset_config_cache()


# ---- Helper unit tests ----


class TestSafePath:
    """Test path traversal protection."""

    def test_empty_path_returns_base(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AFL_LOCAL_OUTPUT_DIR", str(tmp_path))
        result = _safe_path("")
        assert result == tmp_path.resolve()

    def test_normal_subpath(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AFL_LOCAL_OUTPUT_DIR", str(tmp_path))
        sub = tmp_path / "maps"
        sub.mkdir()
        result = _safe_path("maps")
        assert result == sub.resolve()

    def test_traversal_blocked(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AFL_LOCAL_OUTPUT_DIR", str(tmp_path))
        assert _safe_path("../../etc/passwd") is None

    def test_traversal_dot_dot_slash(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AFL_LOCAL_OUTPUT_DIR", str(tmp_path))
        assert _safe_path("../..") is None

    def test_nested_path(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AFL_LOCAL_OUTPUT_DIR", str(tmp_path))
        nested = tmp_path / "a" / "b" / "c"
        nested.mkdir(parents=True)
        result = _safe_path("a/b/c")
        assert result == nested.resolve()


class TestBuildTree:
    """Test directory listing."""

    def test_empty_dir(self, tmp_path):
        result = _build_tree(tmp_path)
        assert result == []

    def test_files_and_dirs_sorted(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AFL_LOCAL_OUTPUT_DIR", str(tmp_path))
        (tmp_path / "bravo.txt").write_text("hello")
        (tmp_path / "alpha").mkdir()
        (tmp_path / "charlie.html").write_text("<html></html>")
        result = _build_tree(tmp_path)
        names = [e["name"] for e in result]
        # Directories first, then files, both alphabetical
        assert names == ["alpha", "bravo.txt", "charlie.html"]

    def test_directory_entry_is_dir(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AFL_LOCAL_OUTPUT_DIR", str(tmp_path))
        (tmp_path / "subdir").mkdir()
        result = _build_tree(tmp_path)
        assert result[0]["is_dir"] is True
        assert result[0]["size"] == 0

    def test_file_metadata(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AFL_LOCAL_OUTPUT_DIR", str(tmp_path))
        f = tmp_path / "data.json"
        f.write_text('{"key": "value"}')
        result = _build_tree(tmp_path)
        assert len(result) == 1
        assert result[0]["name"] == "data.json"
        assert result[0]["size"] > 0
        assert result[0]["mtime"] > 0
        assert result[0]["is_dir"] is False

    def test_nonexistent_dir(self, tmp_path):
        result = _build_tree(tmp_path / "nonexistent")
        assert result == []


class TestBuildBreadcrumbs:
    """Test breadcrumb generation."""

    def test_root(self):
        crumbs = _build_breadcrumbs("")
        assert len(crumbs) == 1
        assert crumbs[0]["name"] == "Output"
        assert crumbs[0]["path"] == ""

    def test_nested_path(self):
        crumbs = _build_breadcrumbs("maps/alabama")
        assert len(crumbs) == 3
        assert crumbs[0] == {"name": "Output", "path": ""}
        assert crumbs[1] == {"name": "maps", "path": "maps"}
        assert crumbs[2] == {"name": "alabama", "path": "maps/alabama"}

    def test_single_level(self):
        crumbs = _build_breadcrumbs("stats")
        assert len(crumbs) == 2
        assert crumbs[1] == {"name": "stats", "path": "stats"}


# ---- Filter tests ----


class TestFilesizeformat:
    def test_bytes(self):
        assert filesizeformat(100) == "100 B"

    def test_zero(self):
        assert filesizeformat(0) == "0 B"

    def test_kilobytes(self):
        result = filesizeformat(2048)
        assert "KB" in result
        assert "2.0" in result

    def test_megabytes(self):
        result = filesizeformat(5 * 1024 * 1024)
        assert "MB" in result
        assert "5.0" in result

    def test_gigabytes(self):
        result = filesizeformat(2 * 1024 * 1024 * 1024)
        assert "GB" in result
        assert "2.0" in result

    def test_boundary_1024(self):
        # Exactly 1024 bytes -> 1.0 KB
        result = filesizeformat(1024)
        assert "1.0 KB" == result


class TestFileTimestamp:
    def test_zero_returns_dashes(self):
        assert file_timestamp(0) == "---"

    def test_valid_timestamp(self):
        # 2024-06-15 12:00:00 UTC
        ts = 1718452800.0
        result = file_timestamp(ts)
        assert "2024" in result
        assert "06" in result

    def test_format_includes_time(self):
        ts = 1718452800.0
        result = file_timestamp(ts)
        # Should have YYYY-MM-DD HH:MM:SS format
        assert len(result) == 19  # "YYYY-MM-DD HH:MM:SS"


# ---- Route integration tests ----


@pytest.fixture()
def output_dir(tmp_path, monkeypatch):
    """Create a temp output directory and point AFL_LOCAL_OUTPUT_DIR at it."""
    monkeypatch.setenv("AFL_LOCAL_OUTPUT_DIR", str(tmp_path))
    (tmp_path / "maps").mkdir()
    (tmp_path / "maps" / "alabama.html").write_text("<html><body>Map</body></html>")
    (tmp_path / "stats").mkdir()
    (tmp_path / "stats" / "report.txt").write_text("some stats")
    return tmp_path


@pytest.fixture()
def client(output_dir):
    """FastAPI test client with output dir configured."""
    app = create_app()
    return TestClient(app)


class TestOutputRoutes:
    def test_browser_root(self, client, output_dir):
        resp = client.get("/output")
        assert resp.status_code == 200
        assert "maps" in resp.text
        assert "stats" in resp.text

    def test_browser_subdir(self, client, output_dir):
        resp = client.get("/output?path=maps")
        assert resp.status_code == 200
        assert "alabama.html" in resp.text

    def test_view_html_file(self, client, output_dir):
        resp = client.get("/output/view?path=maps/alabama.html")
        assert resp.status_code == 200
        assert "Map" in resp.text

    def test_view_missing_file_returns_404(self, client, output_dir):
        resp = client.get("/output/view?path=maps/nonexistent.html")
        assert resp.status_code == 404

    def test_traversal_blocked_browser(self, client, output_dir):
        resp = client.get("/output?path=../../etc/passwd")
        assert resp.status_code == 400

    def test_traversal_blocked_view(self, client, output_dir):
        resp = client.get("/output/view?path=../../etc/passwd")
        assert resp.status_code == 400

    def test_nonexistent_dir_returns_404(self, client, output_dir):
        resp = client.get("/output?path=nonexistent")
        assert resp.status_code == 404

    def test_breadcrumbs_in_response(self, client, output_dir):
        resp = client.get("/output?path=maps")
        assert resp.status_code == 200
        assert "Output" in resp.text  # root breadcrumb

    def test_view_text_file(self, client, output_dir):
        resp = client.get("/output/view?path=stats/report.txt")
        assert resp.status_code == 200
        assert "some stats" in resp.text

    def test_viewable_file_name_is_link(self, client, output_dir):
        resp = client.get("/output?path=maps")
        assert resp.status_code == 200
        # The filename itself should be an <a> link to /output/view
        assert '<a href="/output/view?path=maps' in resp.text
        assert "alabama.html</a>" in resp.text

    def test_json_file_name_is_link(self, client, output_dir):
        (output_dir / "maps" / "data.json").write_text('{"a":1}')
        resp = client.get("/output?path=maps")
        assert resp.status_code == 200
        assert "data.json</a>" in resp.text

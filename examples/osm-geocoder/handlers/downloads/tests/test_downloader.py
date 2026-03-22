#!/usr/bin/env python3
"""Tests for the standalone OSM downloader module.

All tests are offline — HTTP requests and filesystem access are mocked.

    PYTHONPATH=. python -m pytest examples/osm-geocoder/tests/mocked/py/test_downloader.py -v
"""

import os
import sys
import threading
from unittest import mock

import pytest

# The example directory uses hyphens, so add it to sys.path for direct import.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from handlers import downloader as _downloader_mod  # noqa: E402
from handlers.shared.downloader import (  # noqa: E402
    CACHE_DIR,
    FORMAT_EXTENSIONS,
    GEOFABRIK_BASE,
    cache_path,
    download,
    download_url,
    geofabrik_url,
)

MODULE = "handlers.downloader"

# Minimal valid PBF header (32+ bytes with OSMHeader marker) for integrity checks
_FAKE_PBF = b"\x00\x00\x00\x0e\x0a\x09OSMHeader\x18" + b"\x00" * 19


@pytest.fixture(autouse=True)
def _disable_mirror():
    """Disable mirror by default so cache-miss tests hit HTTP, not the real mirror dir."""
    with mock.patch.object(_downloader_mod, "GEOFABRIK_MIRROR", None):
        yield


class TestGeofabrikUrl:
    def test_simple_region(self):
        assert geofabrik_url("africa/algeria") == (
            f"{GEOFABRIK_BASE}/africa/algeria-latest.osm.pbf"
        )

    def test_nested_region(self):
        assert geofabrik_url("north-america/us/california") == (
            f"{GEOFABRIK_BASE}/north-america/us/california-latest.osm.pbf"
        )

    def test_continent(self):
        assert geofabrik_url("antarctica") == (f"{GEOFABRIK_BASE}/antarctica-latest.osm.pbf")


class TestGeofabrikUrlShapefile:
    def test_simple_region_shp(self):
        assert geofabrik_url("africa/algeria", fmt="shp") == (
            f"{GEOFABRIK_BASE}/africa/algeria-latest.free.shp.zip"
        )

    def test_nested_region_shp(self):
        assert geofabrik_url("north-america/us/california", fmt="shp") == (
            f"{GEOFABRIK_BASE}/north-america/us/california-latest.free.shp.zip"
        )

    def test_continent_shp(self):
        assert geofabrik_url("antarctica", fmt="shp") == (
            f"{GEOFABRIK_BASE}/antarctica-latest.free.shp.zip"
        )


class TestCachePath:
    def test_simple_region(self):
        result = cache_path("africa/algeria")
        assert result == os.path.join(CACHE_DIR, "africa/algeria-latest.osm.pbf")

    def test_nested_region(self):
        result = cache_path("north-america/us/california")
        assert result == os.path.join(CACHE_DIR, "north-america/us/california-latest.osm.pbf")

    def test_continent(self):
        result = cache_path("antarctica")
        assert result == os.path.join(CACHE_DIR, "antarctica-latest.osm.pbf")


class TestCachePathShapefile:
    def test_simple_region_shp(self):
        result = cache_path("africa/algeria", fmt="shp")
        assert result == os.path.join(CACHE_DIR, "africa/algeria-latest.free.shp.zip")

    def test_nested_region_shp(self):
        result = cache_path("north-america/us/california", fmt="shp")
        assert result == os.path.join(CACHE_DIR, "north-america/us/california-latest.free.shp.zip")

    def test_continent_shp(self):
        result = cache_path("antarctica", fmt="shp")
        assert result == os.path.join(CACHE_DIR, "antarctica-latest.free.shp.zip")


class TestDownloadCacheHit:
    """When the file already exists locally, no HTTP request is made."""

    @mock.patch(f"{MODULE}.os.path.getsize", return_value=5000)
    @mock.patch(f"{MODULE}.os.path.exists", return_value=True)
    def test_returns_cache_hit(self, mock_exists, mock_getsize):
        result = download("africa/algeria")

        assert result["wasInCache"] is True
        assert result["size"] == 5000
        assert result["url"] == geofabrik_url("africa/algeria")
        assert result["path"] == cache_path("africa/algeria")
        assert "date" in result

    @mock.patch(f"{MODULE}.os.path.getsize", return_value=5000)
    @mock.patch(f"{MODULE}.os.path.exists", return_value=True)
    @mock.patch(f"{MODULE}.requests.get")
    def test_no_http_request(self, mock_get, mock_exists, mock_getsize):
        download("africa/algeria")
        mock_get.assert_not_called()


class TestDownloadCacheMiss:
    """When the file is not cached, it is downloaded via HTTP."""

    def _setup_mocks(self, mock_get):
        mock_response = mock.Mock()
        mock_response.iter_content.return_value = [_FAKE_PBF]
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response
        return mock_response

    @mock.patch(f"{MODULE}.os.replace")
    @mock.patch(f"{MODULE}.os.path.getsize", return_value=len(_FAKE_PBF))
    @mock.patch(f"{MODULE}.os.makedirs")
    @mock.patch("builtins.open", new_callable=lambda: mock.mock_open(read_data=_FAKE_PBF))
    @mock.patch(f"{MODULE}.requests.get")
    @mock.patch(f"{MODULE}.os.path.exists", return_value=False)
    def test_returns_cache_miss(
        self, mock_exists, mock_get, mock_open, mock_makedirs, mock_getsize, mock_replace
    ):
        self._setup_mocks(mock_get)

        result = download("africa/algeria")

        assert result["wasInCache"] is False
        assert result["size"] == len(_FAKE_PBF)
        assert result["url"] == geofabrik_url("africa/algeria")
        assert result["path"] == cache_path("africa/algeria")

    @mock.patch(f"{MODULE}.os.replace")
    @mock.patch(f"{MODULE}.os.path.getsize", return_value=len(_FAKE_PBF))
    @mock.patch(f"{MODULE}.os.makedirs")
    @mock.patch("builtins.open", new_callable=lambda: mock.mock_open(read_data=_FAKE_PBF))
    @mock.patch(f"{MODULE}.requests.get")
    @mock.patch(f"{MODULE}.os.path.exists", return_value=False)
    def test_streams_to_file(
        self, mock_exists, mock_get, mock_open, mock_makedirs, mock_getsize, mock_replace
    ):
        self._setup_mocks(mock_get)

        download("africa/algeria")

        mock_get.assert_called_once()
        assert mock_get.call_args.kwargs["stream"] is True
        mock_open().write.assert_called_once_with(_FAKE_PBF)

    @mock.patch(f"{MODULE}.os.replace")
    @mock.patch(f"{MODULE}.os.path.getsize", return_value=len(_FAKE_PBF))
    @mock.patch(f"{MODULE}.os.makedirs")
    @mock.patch("builtins.open", new_callable=lambda: mock.mock_open(read_data=_FAKE_PBF))
    @mock.patch(f"{MODULE}.requests.get")
    @mock.patch(f"{MODULE}.os.path.exists", return_value=False)
    def test_creates_parent_directories(
        self, mock_exists, mock_get, mock_open, mock_makedirs, mock_getsize, mock_replace
    ):
        self._setup_mocks(mock_get)

        download("north-america/us/california")

        expected_dir = os.path.dirname(cache_path("north-america/us/california"))
        mock_makedirs.assert_called_once_with(expected_dir, exist_ok=True)

    @mock.patch(f"{MODULE}.os.replace")
    @mock.patch(f"{MODULE}.os.path.getsize", return_value=len(_FAKE_PBF))
    @mock.patch(f"{MODULE}.os.makedirs")
    @mock.patch("builtins.open", new_callable=lambda: mock.mock_open(read_data=_FAKE_PBF))
    @mock.patch(f"{MODULE}.requests.get")
    @mock.patch(f"{MODULE}.os.path.exists", return_value=False)
    def test_sets_user_agent(
        self, mock_exists, mock_get, mock_open, mock_makedirs, mock_getsize, mock_replace
    ):
        self._setup_mocks(mock_get)

        download("africa/algeria")

        headers = mock_get.call_args.kwargs["headers"]
        assert headers["User-Agent"] == "AgentFlow-OSM-Example/1.0"


class TestDownloadHttpError:
    """HTTP errors propagate as exceptions."""

    @mock.patch(f"{MODULE}.os.makedirs")
    @mock.patch(f"{MODULE}.requests.get")
    @mock.patch(f"{MODULE}.os.path.exists", return_value=False)
    def test_raises_on_http_error(self, mock_exists, mock_get, mock_makedirs):
        import requests

        mock_response = mock.Mock()
        mock_response.raise_for_status.side_effect = requests.HTTPError("404 Not Found")
        mock_get.return_value = mock_response

        with pytest.raises(requests.HTTPError, match="404 Not Found"):
            download("nonexistent/region")


class TestDownloadMirror:
    """Tests for AFL_GEOFABRIK_MIRROR local mirror support."""

    def test_mirror_hit_uses_mirror_directly(self, tmp_path):
        """When mirror has the file and cache is local, returns mirror path directly."""
        mirror_dir = str(tmp_path / "mirror")
        region = "africa/algeria"
        ext = FORMAT_EXTENSIONS["pbf"]
        mirror_file = os.path.join(mirror_dir, f"{region}-latest.{ext}")
        os.makedirs(os.path.dirname(mirror_file), exist_ok=True)
        with open(mirror_file, "wb") as f:
            f.write(_FAKE_PBF)

        with (
            mock.patch.object(_downloader_mod, "GEOFABRIK_MIRROR", mirror_dir),
            mock.patch.object(_downloader_mod, "_storage") as mock_storage,
            mock.patch(f"{MODULE}.requests.get") as mock_get,
        ):
            mock_storage.exists.return_value = False  # cache miss
            result = download(region)

        assert result["wasInCache"] is True
        assert result["source"] == "mirror"
        assert result["path"] == mirror_file
        assert result["url"] == geofabrik_url(region)
        assert result["size"] == len(_FAKE_PBF)
        mock_get.assert_not_called()

    def test_mirror_hit_copies_to_hdfs_cache(self, tmp_path):
        """When mirror has the file and cache is HDFS, copies to HDFS cache."""
        mirror_dir = str(tmp_path / "mirror")
        region = "africa/algeria"
        ext = FORMAT_EXTENSIONS["pbf"]
        mirror_file = os.path.join(mirror_dir, f"{region}-latest.{ext}")
        os.makedirs(os.path.dirname(mirror_file), exist_ok=True)
        with open(mirror_file, "wb") as f:
            f.write(_FAKE_PBF)

        with (
            mock.patch.object(_downloader_mod, "GEOFABRIK_MIRROR", mirror_dir),
            mock.patch.object(_downloader_mod, "CACHE_DIR", "hdfs://namenode:8020/cache"),
            mock.patch.object(_downloader_mod, "_storage") as mock_storage,
            mock.patch(f"{MODULE}.requests.get") as mock_get,
        ):
            mock_storage.exists.return_value = False
            mock_storage.join.return_value = f"hdfs://namenode:8020/cache/{region}-latest.{ext}"
            mock_storage.dirname.return_value = "hdfs://namenode:8020/cache/africa"
            mock_storage.getsize.return_value = 13
            mock_storage.open.return_value.__enter__ = mock.Mock()
            mock_storage.open.return_value.__exit__ = mock.Mock(return_value=False)
            result = download(region)

        assert result["wasInCache"] is False
        assert result["source"] == "mirror"
        mock_get.assert_not_called()

    def test_mirror_miss_falls_through_to_download(self, tmp_path):
        """Mirror set but file missing — proceeds to HTTP download."""
        mirror_dir = str(tmp_path / "empty-mirror")
        os.makedirs(mirror_dir, exist_ok=True)

        mock_response = mock.Mock()
        mock_response.iter_content.return_value = [_FAKE_PBF]
        mock_response.raise_for_status.return_value = None

        with (
            mock.patch.object(_downloader_mod, "GEOFABRIK_MIRROR", mirror_dir),
            mock.patch(f"{MODULE}.os.path.exists", return_value=False),
            mock.patch(f"{MODULE}.os.makedirs"),
            mock.patch(f"{MODULE}.os.path.getsize", return_value=len(_FAKE_PBF)),
            mock.patch(f"{MODULE}.os.replace"),
            mock.patch("builtins.open", new_callable=lambda: mock.mock_open(read_data=_FAKE_PBF)),
            mock.patch(f"{MODULE}.requests.get", return_value=mock_response) as mock_get,
        ):
            result = download("africa/algeria")

        assert result["wasInCache"] is False
        mock_get.assert_called_once()

    def test_mirror_not_set_skips_check(self):
        """Without AFL_GEOFABRIK_MIRROR, goes straight to HTTP."""
        mock_response = mock.Mock()
        mock_response.iter_content.return_value = [_FAKE_PBF]
        mock_response.raise_for_status.return_value = None

        with (
            mock.patch.object(_downloader_mod, "GEOFABRIK_MIRROR", None),
            mock.patch(f"{MODULE}.os.path.exists", return_value=False),
            mock.patch(f"{MODULE}.os.path.isfile") as mock_isfile,
            mock.patch(f"{MODULE}.os.makedirs"),
            mock.patch(f"{MODULE}.os.path.getsize", return_value=len(_FAKE_PBF)),
            mock.patch(f"{MODULE}.os.replace"),
            mock.patch("builtins.open", new_callable=lambda: mock.mock_open(read_data=_FAKE_PBF)),
            mock.patch(f"{MODULE}.requests.get", return_value=mock_response),
        ):
            download("africa/algeria")

        mock_isfile.assert_not_called()

    def test_mirror_path_structure(self, tmp_path):
        """Mirror file is found — result path is the mirror path (local cache)."""
        mirror_dir = str(tmp_path / "mirror")
        for fmt, ext in FORMAT_EXTENSIONS.items():
            region = "north-america/us/california"
            mirror_file = os.path.join(mirror_dir, f"{region}-latest.{ext}")
            os.makedirs(os.path.dirname(mirror_file), exist_ok=True)
            with open(mirror_file, "wb") as f:
                f.write(_FAKE_PBF if fmt == "pbf" else b"x" * 7)

            with (
                mock.patch.object(_downloader_mod, "GEOFABRIK_MIRROR", mirror_dir),
                mock.patch.object(_downloader_mod, "_storage") as mock_storage,
            ):
                mock_storage.exists.return_value = False
                result = download(region, fmt=fmt)

            assert result["path"] == mirror_file
            assert result["wasInCache"] is True
            assert result["source"] == "mirror"

    def test_mirror_uses_path_directly_local_cache(self, tmp_path):
        """With local cache, mirror path is returned directly — no copy."""
        mirror_dir = str(tmp_path / "mirror")
        test_cache_dir = str(tmp_path / "cache")
        region = "africa/algeria"
        ext = FORMAT_EXTENSIONS["pbf"]

        # Create mirror file
        mirror_file = os.path.join(mirror_dir, f"{region}-latest.{ext}")
        os.makedirs(os.path.dirname(mirror_file), exist_ok=True)
        with open(mirror_file, "wb") as f:
            f.write(_FAKE_PBF)

        from afl.runtime.storage import get_storage_backend

        test_storage = get_storage_backend(test_cache_dir)

        with (
            mock.patch.object(_downloader_mod, "GEOFABRIK_MIRROR", mirror_dir),
            mock.patch.object(_downloader_mod, "CACHE_DIR", test_cache_dir),
            mock.patch.object(_downloader_mod, "_storage", test_storage),
            mock.patch(f"{MODULE}.requests.get") as mock_get,
        ):
            result = download(region)

        # Mirror path returned directly, no copy to cache
        assert result["path"] == mirror_file
        assert result["wasInCache"] is True
        assert result["source"] == "mirror"
        mock_get.assert_not_called()

    def test_mirror_skips_copy_when_cache_exists(self, tmp_path):
        """When cache already has the file, mirror copy is skipped."""
        from afl.runtime.storage import get_storage_backend

        mirror_dir = str(tmp_path / "mirror")
        test_cache_dir = str(tmp_path / "cache")
        region = "africa/algeria"
        ext = FORMAT_EXTENSIONS["pbf"]

        # Create mirror file
        mirror_file = os.path.join(mirror_dir, f"{region}-latest.{ext}")
        os.makedirs(os.path.dirname(mirror_file), exist_ok=True)
        with open(mirror_file, "wb") as f:
            f.write(_FAKE_PBF)

        # Create cache file (already exists)
        test_storage = get_storage_backend(test_cache_dir)
        cache_file = test_storage.join(test_cache_dir, f"{region}-latest.{ext}")
        os.makedirs(os.path.dirname(cache_file), exist_ok=True)
        with open(cache_file, "wb") as f:
            f.write(_FAKE_PBF)

        with (
            mock.patch.object(_downloader_mod, "GEOFABRIK_MIRROR", mirror_dir),
            mock.patch.object(_downloader_mod, "CACHE_DIR", test_cache_dir),
            mock.patch.object(_downloader_mod, "_storage", test_storage),
            mock.patch(f"{MODULE}.subprocess.run") as mock_cp,
            mock.patch(f"{MODULE}.requests.get") as mock_get,
        ):
            result = download(region)

        assert result["wasInCache"] is True
        assert result["path"] == cache_file
        mock_cp.assert_not_called()
        mock_get.assert_not_called()


class TestDownloadShapefileCacheHit:
    """Shapefile format: when the file already exists locally."""

    @mock.patch(f"{MODULE}.os.path.getsize", return_value=12000)
    @mock.patch(f"{MODULE}.os.path.exists", return_value=True)
    def test_returns_cache_hit_shp(self, mock_exists, mock_getsize):
        result = download("africa/algeria", fmt="shp")

        assert result["wasInCache"] is True
        assert result["size"] == 12000
        assert result["url"] == geofabrik_url("africa/algeria", fmt="shp")
        assert result["path"] == cache_path("africa/algeria", fmt="shp")
        assert "date" in result


class TestDownloadShapefileCacheMiss:
    """Shapefile format: when the file is not cached."""

    def _setup_mocks(self, mock_get):
        mock_response = mock.Mock()
        mock_response.iter_content.return_value = [b"fake-shp-data"]
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response
        return mock_response

    @mock.patch(f"{MODULE}.os.replace")
    @mock.patch(f"{MODULE}.os.path.getsize", return_value=len(_FAKE_PBF))
    @mock.patch(f"{MODULE}.os.makedirs")
    @mock.patch("builtins.open", new_callable=lambda: mock.mock_open(read_data=_FAKE_PBF))
    @mock.patch(f"{MODULE}.requests.get")
    @mock.patch(f"{MODULE}.os.path.exists", return_value=False)
    def test_returns_cache_miss_shp(
        self, mock_exists, mock_get, mock_open, mock_makedirs, mock_getsize, mock_replace
    ):
        self._setup_mocks(mock_get)

        result = download("africa/algeria", fmt="shp")

        assert result["wasInCache"] is False
        assert result["url"] == geofabrik_url("africa/algeria", fmt="shp")
        assert result["path"] == cache_path("africa/algeria", fmt="shp")

    @mock.patch(f"{MODULE}.os.replace")
    @mock.patch(f"{MODULE}.os.path.getsize", return_value=len(_FAKE_PBF))
    @mock.patch(f"{MODULE}.os.makedirs")
    @mock.patch("builtins.open", new_callable=lambda: mock.mock_open(read_data=_FAKE_PBF))
    @mock.patch(f"{MODULE}.requests.get")
    @mock.patch(f"{MODULE}.os.path.exists", return_value=False)
    def test_requests_shp_url(
        self, mock_exists, mock_get, mock_open, mock_makedirs, mock_getsize, mock_replace
    ):
        self._setup_mocks(mock_get)

        download("africa/algeria", fmt="shp")

        called_url = mock_get.call_args[0][0]
        assert called_url.endswith("-latest.free.shp.zip")


# ---------------------------------------------------------------------------
# download_url() — generic URL-to-path downloader
# ---------------------------------------------------------------------------


def _mock_storage():
    """Create a mock StorageBackend with context-manager-aware open()."""
    s = mock.MagicMock()
    s.dirname.side_effect = os.path.dirname
    s.join.side_effect = os.path.join
    return s


class TestDownloadUrlCacheHit:
    """When the destination file already exists and force=False."""

    @mock.patch.object(_downloader_mod, "get_storage_backend")
    @mock.patch.object(_downloader_mod.requests, "get")
    def test_returns_cache_hit(self, mock_get, mock_gsb):
        storage = _mock_storage()
        storage.exists.return_value = True
        storage.getsize.return_value = 9999
        mock_gsb.return_value = storage

        result = download_url("https://example.com/data.pbf", "/data/output.pbf")

        assert result["wasInCache"] is True
        assert result["size"] == 9999
        assert result["url"] == "https://example.com/data.pbf"
        assert result["path"] == "/data/output.pbf"
        assert "date" in result
        mock_get.assert_not_called()

    @mock.patch(f"{MODULE}.os.replace")
    @mock.patch.object(_downloader_mod, "get_storage_backend")
    @mock.patch.object(_downloader_mod.requests, "get")
    def test_force_redownloads(self, mock_get, mock_gsb, mock_replace):
        storage = _mock_storage()
        storage.exists.return_value = True
        storage.getsize.return_value = 42
        mock_gsb.return_value = storage

        mock_response = mock.Mock()
        mock_response.iter_content.return_value = [b"new-data"]
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        result = download_url("https://example.com/data.pbf", "/data/output.pbf", force=True)

        assert result["wasInCache"] is False
        mock_get.assert_called_once()


class TestDownloadUrlCacheMiss:
    """When the destination file does not exist."""

    def _setup_mocks(self, mock_get):
        mock_response = mock.Mock()
        mock_response.iter_content.return_value = [b"downloaded-data"]
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

    @mock.patch(f"{MODULE}.os.replace")
    @mock.patch.object(_downloader_mod, "get_storage_backend")
    @mock.patch.object(_downloader_mod.requests, "get")
    def test_downloads_and_returns(self, mock_get, mock_gsb, mock_replace):
        storage = _mock_storage()
        storage.exists.return_value = False
        storage.getsize.return_value = 15
        mock_gsb.return_value = storage
        self._setup_mocks(mock_get)

        result = download_url("https://example.com/file.zip", "/output/file.zip")

        assert result["wasInCache"] is False
        assert result["size"] == 15
        assert result["url"] == "https://example.com/file.zip"
        assert result["path"] == "/output/file.zip"
        mock_get.assert_called_once()
        storage.makedirs.assert_called_once()

    @mock.patch(f"{MODULE}.os.replace")
    @mock.patch.object(_downloader_mod, "get_storage_backend")
    @mock.patch.object(_downloader_mod.requests, "get")
    def test_streams_to_storage(self, mock_get, mock_gsb, mock_replace):
        storage = _mock_storage()
        storage.exists.return_value = False
        storage.getsize.return_value = 15
        mock_gsb.return_value = storage
        self._setup_mocks(mock_get)

        download_url("https://example.com/file.zip", "/output/file.zip")

        # File is written to a temp path (not final path) then atomically renamed
        assert storage.open.call_count == 1
        written_path = storage.open.call_args[0][0]
        assert written_path.startswith("/output/file.zip.tmp.")
        assert storage.open.call_args[0][1] == "wb"
        storage.open().__enter__().write.assert_called_once_with(b"downloaded-data")
        # os.replace moves temp → final
        mock_replace.assert_called_once()
        assert mock_replace.call_args[0][1] == "/output/file.zip"

    @mock.patch.object(_downloader_mod, "get_storage_backend")
    @mock.patch.object(_downloader_mod.requests, "get")
    def test_hdfs_path(self, mock_get, mock_gsb):
        storage = _mock_storage()
        storage.exists.return_value = False
        storage.getsize.return_value = 100
        mock_gsb.return_value = storage
        self._setup_mocks(mock_get)

        result = download_url(
            "https://example.com/data.csv",
            "hdfs://namenode:8020/data/output.csv",
        )

        assert result["path"] == "hdfs://namenode:8020/data/output.csv"
        assert result["wasInCache"] is False
        mock_gsb.assert_called_with("hdfs://namenode:8020/data/output.csv")

    def _setup_mocks(self, mock_get):
        mock_response = mock.Mock()
        mock_response.iter_content.return_value = [b"downloaded-data"]
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response


class TestDownloadUrlHttpError:
    """HTTP errors propagate from download_url."""

    @mock.patch.object(_downloader_mod, "get_storage_backend")
    @mock.patch.object(_downloader_mod.requests, "get")
    def test_raises_on_http_error(self, mock_get, mock_gsb):
        import requests

        storage = _mock_storage()
        storage.exists.return_value = False
        mock_gsb.return_value = storage

        mock_response = mock.Mock()
        mock_response.raise_for_status.side_effect = requests.HTTPError("403 Forbidden")
        mock_get.return_value = mock_response

        with pytest.raises(requests.HTTPError, match="403 Forbidden"):
            download_url("https://example.com/secret.dat", "/tmp/secret.dat")


# ---------------------------------------------------------------------------
# Lock deduplication and atomic write tests
# ---------------------------------------------------------------------------


class TestDownloadLockDeduplication:
    """Concurrent downloads to the same path should result in a single HTTP request."""

    def setup_method(self):
        _downloader_mod._path_locks.clear()

    def test_concurrent_downloads_single_fetch(self):
        """5 threads request same region — only 1 HTTP request is made."""
        downloaded = {"done": False}

        def exists_effect(path):
            return downloaded["done"]

        def replace_effect(src, dst):
            downloaded["done"] = True

        mock_response = mock.Mock()
        mock_response.iter_content.return_value = [_FAKE_PBF]
        mock_response.raise_for_status.return_value = None

        with (
            mock.patch(f"{MODULE}.os.path.exists", side_effect=exists_effect),
            mock.patch(f"{MODULE}.os.makedirs"),
            mock.patch(f"{MODULE}.os.path.getsize", return_value=len(_FAKE_PBF)),
            mock.patch(f"{MODULE}.os.replace", side_effect=replace_effect),
            mock.patch("builtins.open", new_callable=lambda: mock.mock_open(read_data=_FAKE_PBF)),
            mock.patch(f"{MODULE}.requests.get", return_value=mock_response) as mock_get,
        ):
            results = []
            errors = []
            barrier = threading.Barrier(5)

            def do_download():
                try:
                    barrier.wait(timeout=5)
                    results.append(download("africa/algeria"))
                except Exception as e:
                    errors.append(e)

            threads = [threading.Thread(target=do_download) for _ in range(5)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=10)

            assert not errors, f"Unexpected errors: {errors}"
            assert len(results) == 5
            # Only one HTTP request should have been made
            assert mock_get.call_count == 1

        cache_hits = [r for r in results if r["wasInCache"]]
        cache_misses = [r for r in results if not r["wasInCache"]]
        assert len(cache_misses) == 1
        assert len(cache_hits) == 4

    def test_lock_recheck_returns_cache_hit(self):
        """Thread that acquires the lock after another finishes gets wasInCache=True."""
        call_count = [0]

        def exists_effect(path):
            call_count[0] += 1
            # First call (fast-path) returns False; second call (re-check) returns True
            return call_count[0] > 1

        with (
            mock.patch(f"{MODULE}.os.path.exists", side_effect=exists_effect),
            mock.patch(f"{MODULE}.os.path.getsize", return_value=100),
        ):
            result = download("africa/algeria")

        assert result["wasInCache"] is True
        assert call_count[0] == 2

    def test_different_paths_not_blocked(self):
        """Concurrent downloads to different paths both proceed independently."""
        mock_response = mock.Mock()
        mock_response.iter_content.return_value = [_FAKE_PBF]
        mock_response.raise_for_status.return_value = None

        with (
            mock.patch(f"{MODULE}.os.path.exists", return_value=False),
            mock.patch(f"{MODULE}.os.makedirs"),
            mock.patch(f"{MODULE}.os.path.getsize", return_value=len(_FAKE_PBF)),
            mock.patch(f"{MODULE}.os.replace"),
            mock.patch("builtins.open", new_callable=lambda: mock.mock_open(read_data=_FAKE_PBF)),
            mock.patch(f"{MODULE}.requests.get", return_value=mock_response) as mock_get,
        ):
            results = []

            def dl(region):
                results.append(download(region))

            t1 = threading.Thread(target=dl, args=("africa/algeria",))
            t2 = threading.Thread(target=dl, args=("europe/germany",))
            t1.start()
            t2.start()
            t1.join(timeout=5)
            t2.join(timeout=5)

        assert len(results) == 2
        assert mock_get.call_count == 2


class TestDownloadUrlLockDeduplication:
    """Concurrent download_url() calls for the same path should deduplicate."""

    def setup_method(self):
        _downloader_mod._path_locks.clear()

    def test_concurrent_download_url_single_fetch(self):
        """Multiple concurrent download_url() calls — only 1 HTTP request."""
        downloaded = {"done": False}

        def exists_effect(path):
            return downloaded["done"]

        def replace_effect(src, dst):
            downloaded["done"] = True

        storage = _mock_storage()
        storage.exists.side_effect = exists_effect
        storage.getsize.return_value = 50

        mock_response = mock.Mock()
        mock_response.iter_content.return_value = [b"data"]
        mock_response.raise_for_status.return_value = None

        with (
            mock.patch.object(_downloader_mod, "get_storage_backend", return_value=storage),
            mock.patch.object(
                _downloader_mod.requests, "get", return_value=mock_response
            ) as mock_get,
            mock.patch(f"{MODULE}.os.replace", side_effect=replace_effect),
        ):
            results = []
            barrier = threading.Barrier(3)

            def dl():
                barrier.wait(timeout=5)
                results.append(download_url("https://example.com/f.bin", "/out/f.bin"))

            threads = [threading.Thread(target=dl) for _ in range(3)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=10)

            assert len(results) == 3
            assert mock_get.call_count == 1

        assert sum(1 for r in results if r["wasInCache"]) == 2
        assert sum(1 for r in results if not r["wasInCache"]) == 1


class TestDownloadAtomicWrite:
    """Atomic temp-file-then-rename pattern tests."""

    def setup_method(self):
        _downloader_mod._path_locks.clear()

    def test_partial_download_cleaned_up(self):
        """HTTP error mid-download triggers temp file cleanup."""
        import requests as req_lib

        mock_response = mock.Mock()
        mock_response.raise_for_status.side_effect = req_lib.HTTPError("500 Server Error")

        with (
            mock.patch(f"{MODULE}.os.path.exists", return_value=False),
            mock.patch(f"{MODULE}.os.makedirs"),
            mock.patch(f"{MODULE}.os.remove") as mock_os_remove,
            mock.patch(f"{MODULE}.requests.get", return_value=mock_response),
        ):
            with pytest.raises(req_lib.HTTPError, match="500"):
                download("africa/algeria")

            # Temp file cleanup was attempted
            mock_os_remove.assert_called_once()
            cleaned_path = mock_os_remove.call_args[0][0]
            assert ".tmp." in cleaned_path

    def test_temp_file_not_visible_as_cache_path(self):
        """Data is written to a temp path, then atomically renamed to the cache path."""
        local_path = cache_path("africa/algeria")

        mock_response = mock.Mock()
        mock_response.iter_content.return_value = [_FAKE_PBF]
        mock_response.raise_for_status.return_value = None

        paths_opened = []
        mock_file = mock.mock_open(read_data=_FAKE_PBF)

        def track_open(path, mode="r"):
            paths_opened.append(path)
            return mock_file()

        with (
            mock.patch(f"{MODULE}.os.path.exists", return_value=False),
            mock.patch(f"{MODULE}.os.makedirs"),
            mock.patch(f"{MODULE}.os.path.getsize", return_value=len(_FAKE_PBF)),
            mock.patch(f"{MODULE}.os.replace") as mock_replace,
            mock.patch("builtins.open", side_effect=track_open),
            mock.patch(f"{MODULE}.requests.get", return_value=mock_response),
        ):
            download("africa/algeria")

        # File was written to a temp path, not the final cache path
        assert len(paths_opened) >= 1
        assert all(".tmp." in p for p in paths_opened)
        assert local_path not in paths_opened
        # os.replace was called to atomically move temp → final
        mock_replace.assert_called_once()
        src, dst = mock_replace.call_args[0]
        assert ".tmp." in src
        assert dst == local_path

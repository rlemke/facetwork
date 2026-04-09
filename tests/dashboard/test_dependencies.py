"""Tests for dashboard dependency injection."""

from unittest.mock import MagicMock, patch

import pytest

try:
    from facetwork.dashboard.dependencies import _get_store, get_store

    DASHBOARD_AVAILABLE = True
except ImportError:
    DASHBOARD_AVAILABLE = False

pytestmark = pytest.mark.skipif(not DASHBOARD_AVAILABLE, reason="dashboard deps not installed")


@pytest.fixture(autouse=True)
def clear_cache():
    """Clear LRU cache between tests."""
    _get_store.cache_clear()
    yield
    _get_store.cache_clear()


class TestGetStoreInternal:
    """Test _get_store singleton factory."""

    @patch("facetwork.runtime.mongo_store.MongoStore")
    @patch("facetwork.config.load_config")
    def test_creates_store_from_config(self, mock_load, mock_mongo):
        mock_config = MagicMock()
        mock_load.return_value = mock_config
        mock_store = MagicMock()
        mock_mongo.from_config.return_value = mock_store

        result = _get_store(None)

        mock_load.assert_called_once_with(None)
        mock_mongo.from_config.assert_called_once_with(mock_config.mongodb)
        assert result is mock_store

    @patch("facetwork.runtime.mongo_store.MongoStore")
    @patch("facetwork.config.load_config")
    def test_caches_singleton(self, mock_load, mock_mongo):
        mock_config = MagicMock()
        mock_load.return_value = mock_config
        mock_mongo.from_config.return_value = MagicMock()

        r1 = _get_store(None)
        r2 = _get_store(None)

        assert r1 is r2
        mock_load.assert_called_once()

    @patch("facetwork.runtime.mongo_store.MongoStore")
    @patch("facetwork.config.load_config")
    def test_passes_config_path(self, mock_load, mock_mongo):
        mock_config = MagicMock()
        mock_load.return_value = mock_config
        mock_mongo.from_config.return_value = MagicMock()

        _get_store("/custom/path.json")
        mock_load.assert_called_once_with("/custom/path.json")


class TestGetStoreDependency:
    """Test FastAPI get_store dependency."""

    @patch("facetwork.runtime.mongo_store.MongoStore")
    @patch("facetwork.config.load_config")
    def test_extracts_config_from_request(self, mock_load, mock_mongo):
        mock_config = MagicMock()
        mock_load.return_value = mock_config
        mock_mongo.from_config.return_value = MagicMock()

        request = MagicMock()
        request.app.state.config_path = "/some/config.json"

        result = get_store(request)
        mock_load.assert_called_once_with("/some/config.json")
        assert result is not None

    @patch("facetwork.runtime.mongo_store.MongoStore")
    @patch("facetwork.config.load_config")
    def test_handles_missing_config_path(self, mock_load, mock_mongo):
        mock_config = MagicMock()
        mock_load.return_value = mock_config
        mock_mongo.from_config.return_value = MagicMock()

        request = MagicMock()
        # Simulate no config_path attribute
        del request.app.state.config_path

        result = get_store(request)
        mock_load.assert_called_once_with(None)
        assert result is not None

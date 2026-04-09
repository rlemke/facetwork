"""Tests for MCP store singleton factory."""

from unittest.mock import MagicMock, patch

import pytest

try:
    from facetwork.mcp.store import get_store

    MCP_AVAILABLE = True
except ImportError:
    MCP_AVAILABLE = False

pytestmark = pytest.mark.skipif(not MCP_AVAILABLE, reason="mcp not installed")


@pytest.fixture(autouse=True)
def clear_cache():
    """Clear LRU cache between tests."""
    get_store.cache_clear()
    yield
    get_store.cache_clear()


class TestGetStore:
    """Test get_store singleton factory."""

    @patch("facetwork.runtime.mongo_store.MongoStore")
    @patch("facetwork.config.load_config")
    def test_creates_store_from_config(self, mock_load, mock_mongo):
        mock_config = MagicMock()
        mock_load.return_value = mock_config
        mock_store = MagicMock()
        mock_mongo.from_config.return_value = mock_store

        result = get_store(None)

        mock_load.assert_called_once_with(None)
        mock_mongo.from_config.assert_called_once_with(mock_config.mongodb)
        assert result is mock_store

    @patch("facetwork.runtime.mongo_store.MongoStore")
    @patch("facetwork.config.load_config")
    def test_caches_store_singleton(self, mock_load, mock_mongo):
        mock_config = MagicMock()
        mock_load.return_value = mock_config
        mock_store = MagicMock()
        mock_mongo.from_config.return_value = mock_store

        result1 = get_store(None)
        result2 = get_store(None)

        assert result1 is result2
        # load_config should only be called once due to caching
        mock_load.assert_called_once()

    @patch("facetwork.runtime.mongo_store.MongoStore")
    @patch("facetwork.config.load_config")
    def test_passes_config_path(self, mock_load, mock_mongo):
        mock_config = MagicMock()
        mock_load.return_value = mock_config
        mock_mongo.from_config.return_value = MagicMock()

        get_store("/path/to/config.json")

        mock_load.assert_called_once_with("/path/to/config.json")

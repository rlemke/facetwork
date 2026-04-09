# Copyright 2025 Ralph Lemke
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Tests for FFL source loaders (MongoDB and Maven)."""

import io
import zipfile
from unittest.mock import MagicMock, patch

import pytest

from facetwork.loader import SourceLoader
from facetwork.source import MavenOrigin, MongoDBOrigin


class TestMongoDBLoader:
    """Tests for MongoDB source loader."""

    def test_load_mongodb_success(self, monkeypatch):
        """Successfully load FFL sources from MongoDB."""
        # Mock pymongo
        mock_db = MagicMock()
        mock_client = MagicMock()
        mock_client.__getitem__ = MagicMock(return_value=mock_db)

        # Mock flow document
        mock_flow = {
            "uuid": "test-uuid-123",
            "sources": [
                {"language": "afl", "content": "facet A()"},
                {"language": "afl", "content": "facet B()"},
                {"language": "json", "content": "{}"},  # Should be filtered
            ],
        }
        mock_db.flows.find_one.return_value = mock_flow

        mock_mongo_client = MagicMock(return_value=mock_client)
        monkeypatch.setattr("facetwork.loader.MongoClient", mock_mongo_client, raising=False)

        # Mock import
        with patch.dict("sys.modules", {"pymongo": MagicMock(MongoClient=mock_mongo_client)}):
            from facetwork.config import FFLConfig

            config = FFLConfig()
            entry = SourceLoader.load_mongodb("test-uuid-123", "Test Flow", config=config)

        assert "facet A()" in entry.text
        assert "facet B()" in entry.text
        assert isinstance(entry.origin, MongoDBOrigin)
        assert entry.origin.collection_id == "test-uuid-123"
        assert entry.origin.display_name == "Test Flow"

    def test_load_mongodb_flow_not_found(self, monkeypatch):
        """Raise ValueError when flow not found."""
        mock_db = MagicMock()
        mock_client = MagicMock()
        mock_client.__getitem__ = MagicMock(return_value=mock_db)
        mock_db.flows.find_one.return_value = None

        mock_mongo_client = MagicMock(return_value=mock_client)
        monkeypatch.setattr("facetwork.loader.MongoClient", mock_mongo_client, raising=False)

        with patch.dict("sys.modules", {"pymongo": MagicMock(MongoClient=mock_mongo_client)}):
            from facetwork.config import FFLConfig

            config = FFLConfig()
            with pytest.raises(ValueError, match="Flow not found"):
                SourceLoader.load_mongodb("nonexistent", "Missing", config=config)

    def test_load_mongodb_no_afl_sources(self, monkeypatch):
        """Raise ValueError when flow has no FFL sources."""
        mock_db = MagicMock()
        mock_client = MagicMock()
        mock_client.__getitem__ = MagicMock(return_value=mock_db)

        mock_flow = {
            "uuid": "test-uuid",
            "sources": [
                {"language": "json", "content": "{}"},
            ],
        }
        mock_db.flows.find_one.return_value = mock_flow

        mock_mongo_client = MagicMock(return_value=mock_client)
        monkeypatch.setattr("facetwork.loader.MongoClient", mock_mongo_client, raising=False)

        with patch.dict("sys.modules", {"pymongo": MagicMock(MongoClient=mock_mongo_client)}):
            from facetwork.config import FFLConfig

            config = FFLConfig()
            with pytest.raises(ValueError, match="no FFL sources"):
                SourceLoader.load_mongodb("test-uuid", "Test", config=config)

    def test_load_mongodb_as_library(self, monkeypatch):
        """Load MongoDB source as library."""
        mock_db = MagicMock()
        mock_client = MagicMock()
        mock_client.__getitem__ = MagicMock(return_value=mock_db)

        mock_flow = {"uuid": "test", "sources": [{"language": "afl", "content": "facet Lib()"}]}
        mock_db.flows.find_one.return_value = mock_flow

        mock_mongo_client = MagicMock(return_value=mock_client)
        monkeypatch.setattr("facetwork.loader.MongoClient", mock_mongo_client, raising=False)

        with patch.dict("sys.modules", {"pymongo": MagicMock(MongoClient=mock_mongo_client)}):
            from facetwork.config import FFLConfig

            config = FFLConfig()
            entry = SourceLoader.load_mongodb("test", "Test", is_library=True, config=config)

        assert entry.is_library is True


class TestMavenLoader:
    """Tests for Maven source loader."""

    def _create_mock_jar(self, files: dict[str, str]) -> bytes:
        """Create a mock JAR (ZIP) file in memory."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            for name, content in files.items():
                zf.writestr(name, content)
        return buf.getvalue()

    def test_load_maven_success(self, monkeypatch):
        """Successfully load FFL sources from Maven."""
        jar_content = self._create_mock_jar(
            {
                "facet-a.ffl": "facet A()",
                "facet-b.ffl": "facet B()",
                "README.md": "Not AFL",
            }
        )

        def mock_urlopen(url, **kwargs):
            response = MagicMock()
            response.read.return_value = jar_content
            response.__enter__ = MagicMock(return_value=response)
            response.__exit__ = MagicMock(return_value=False)
            return response

        monkeypatch.setattr("urllib.request.urlopen", mock_urlopen)

        entry = SourceLoader.load_maven("com.example", "mylib", "1.0.0")

        assert "facet A()" in entry.text
        assert "facet B()" in entry.text
        assert isinstance(entry.origin, MavenOrigin)
        assert entry.origin.group_id == "com.example"
        assert entry.origin.artifact_id == "mylib"
        assert entry.origin.version == "1.0.0"
        assert entry.origin.classifier == "sources"

    def test_load_maven_http_error(self, monkeypatch):
        """Raise ValueError on HTTP error."""
        import urllib.error

        def mock_urlopen(url, **kwargs):
            raise urllib.error.HTTPError(url=url, code=404, msg="Not Found", hdrs={}, fp=None)

        monkeypatch.setattr("urllib.request.urlopen", mock_urlopen)

        with pytest.raises(ValueError, match="HTTP 404"):
            SourceLoader.load_maven("com.example", "missing", "1.0.0")

    def test_load_maven_no_afl_files(self, monkeypatch):
        """Raise ValueError when JAR has no .afl files."""
        jar_content = self._create_mock_jar(
            {
                "Main.java": "class Main {}",
            }
        )

        def mock_urlopen(url, **kwargs):
            response = MagicMock()
            response.read.return_value = jar_content
            response.__enter__ = MagicMock(return_value=response)
            response.__exit__ = MagicMock(return_value=False)
            return response

        monkeypatch.setattr("urllib.request.urlopen", mock_urlopen)

        with pytest.raises(ValueError, match="No .afl files found"):
            SourceLoader.load_maven("com.example", "noafl", "1.0.0")

    def test_load_maven_custom_classifier(self, monkeypatch):
        """Load with custom classifier."""
        jar_content = self._create_mock_jar({"test.ffl": "facet Test()"})

        captured_url = []

        def mock_urlopen(url, **kwargs):
            captured_url.append(url)
            response = MagicMock()
            response.read.return_value = jar_content
            response.__enter__ = MagicMock(return_value=response)
            response.__exit__ = MagicMock(return_value=False)
            return response

        monkeypatch.setattr("urllib.request.urlopen", mock_urlopen)

        entry = SourceLoader.load_maven("com.example", "mylib", "1.0.0", classifier="afl")

        assert "mylib-1.0.0-afl.jar" in captured_url[0]
        assert entry.origin.classifier == "afl"

    def test_load_maven_custom_repository(self, monkeypatch):
        """Load from custom Maven repository."""
        jar_content = self._create_mock_jar({"test.ffl": "facet Test()"})

        captured_url = []

        def mock_urlopen(url, **kwargs):
            captured_url.append(url)
            response = MagicMock()
            response.read.return_value = jar_content
            response.__enter__ = MagicMock(return_value=response)
            response.__exit__ = MagicMock(return_value=False)
            return response

        monkeypatch.setattr("urllib.request.urlopen", mock_urlopen)

        SourceLoader.load_maven(
            "com.example",
            "mylib",
            "1.0.0",
            repository_url="https://private.repo/maven2",
        )

        assert captured_url[0].startswith("https://private.repo/maven2/")

    def test_load_maven_as_library(self, monkeypatch):
        """Load Maven artifact as library."""
        jar_content = self._create_mock_jar({"lib.ffl": "facet Lib()"})

        def mock_urlopen(url, **kwargs):
            response = MagicMock()
            response.read.return_value = jar_content
            response.__enter__ = MagicMock(return_value=response)
            response.__exit__ = MagicMock(return_value=False)
            return response

        monkeypatch.setattr("urllib.request.urlopen", mock_urlopen)

        entry = SourceLoader.load_maven("com.example", "mylib", "1.0.0", is_library=True)

        assert entry.is_library is True

    def test_load_maven_url_construction(self, monkeypatch):
        """Verify correct Maven URL construction."""
        jar_content = self._create_mock_jar({"test.ffl": "facet Test()"})

        captured_url = []

        def mock_urlopen(url, **kwargs):
            captured_url.append(url)
            response = MagicMock()
            response.read.return_value = jar_content
            response.__enter__ = MagicMock(return_value=response)
            response.__exit__ = MagicMock(return_value=False)
            return response

        monkeypatch.setattr("urllib.request.urlopen", mock_urlopen)

        SourceLoader.load_maven("org.example.sub", "my-artifact", "2.3.4")

        # Group ID dots should become path separators
        expected = "https://repo1.maven.org/maven2/org/example/sub/my-artifact/2.3.4/my-artifact-2.3.4-sources.jar"
        assert captured_url[0] == expected

    def test_load_maven_sorted_files(self, monkeypatch):
        """AFL files should be loaded in sorted order for determinism."""
        jar_content = self._create_mock_jar(
            {
                "z_last.ffl": "facet Z()",
                "a_first.ffl": "facet A()",
                "m_middle.ffl": "facet M()",
            }
        )

        def mock_urlopen(url, **kwargs):
            response = MagicMock()
            response.read.return_value = jar_content
            response.__enter__ = MagicMock(return_value=response)
            response.__exit__ = MagicMock(return_value=False)
            return response

        monkeypatch.setattr("urllib.request.urlopen", mock_urlopen)

        entry = SourceLoader.load_maven("com.example", "sorted", "1.0.0")

        # Should be sorted: a_first.afl, m_middle.afl, z_last.afl
        lines = entry.text.split("\n")
        assert "facet A()" in lines[0]
        assert "facet M()" in lines[1]
        assert "facet Z()" in lines[2]

    def test_load_maven_invalid_jar(self, monkeypatch):
        """Raise ValueError for invalid JAR file."""

        def mock_urlopen(url, **kwargs):
            response = MagicMock()
            response.read.return_value = b"not a zip file"
            response.__enter__ = MagicMock(return_value=response)
            response.__exit__ = MagicMock(return_value=False)
            return response

        monkeypatch.setattr("urllib.request.urlopen", mock_urlopen)

        with pytest.raises(ValueError, match="Invalid JAR file"):
            SourceLoader.load_maven("com.example", "invalid", "1.0.0")

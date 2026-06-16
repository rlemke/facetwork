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

"""Tests for the output artifact server (/output/raw) + html_path link detection."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from facetwork.dashboard.app import create_app
from facetwork.dashboard.routes.monitoring import output as out


@pytest.fixture
def out_root(tmp_path, monkeypatch):
    """A tmp output root with a rendered map bundle (index.html + a tile)."""
    monkeypatch.setenv("AFL_OUTPUT_BASE", str(tmp_path))
    bundle = tmp_path / "s2" / "aoi1"
    (bundle / "tiles" / "10" / "3").mkdir(parents=True)
    (bundle / "index.html").write_text("<html><body>maplibre-gl change map</body></html>")
    (bundle / "tiles" / "10" / "3" / "5.png").write_bytes(b"\x89PNG\r\n\x1a\n fake")
    return tmp_path, bundle


def test_artifact_url_detects_html(out_root):
    tmp_path, bundle = out_root
    url = out.artifact_url(str(bundle / "index.html"))
    assert url == "/output/raw/s2/aoi1/index.html"


def test_artifact_url_cross_mount(out_root):
    """A host absolute path resolves via its /output/ tail under a mounted root."""
    tmp_path, bundle = out_root  # AFL_OUTPUT_BASE=tmp_path holds s2/aoi1/index.html
    host_path = "/Users/someone/afl_data/output/s2/aoi1/index.html"  # doesn't exist here
    assert out.artifact_url(host_path) == "/output/raw/s2/aoi1/index.html"


def test_artifact_url_rejects_non_html_and_missing(out_root):
    tmp_path, bundle = out_root
    assert out.artifact_url(str(bundle / "change.tif")) is None      # wrong suffix
    assert out.artifact_url(str(bundle / "nope.html")) is None       # missing file
    assert out.artifact_url(12345) is None                           # non-string
    assert out.artifact_url("relative/x.html") is None               # not under a root


def test_raw_serves_html_and_assets(out_root):
    c = TestClient(create_app(), raise_server_exceptions=True)
    r = c.get("/output/raw/s2/aoi1/index.html")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    assert "maplibre-gl" in r.text
    # a sibling asset (relative tile) resolves under the same path base
    t = c.get("/output/raw/s2/aoi1/tiles/10/3/5.png")
    assert t.status_code == 200 and t.headers["content-type"] == "image/png"


def test_raw_blocks_traversal_and_missing(out_root):
    c = TestClient(create_app(), raise_server_exceptions=True)
    assert c.get("/output/raw/../../../../etc/passwd").status_code == 404
    assert c.get("/output/raw/s2/aoi1/missing.html").status_code == 404

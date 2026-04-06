"""Test fixtures for site-analyzer tests."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _patch_store_dirs(tmp_path, monkeypatch):
    """Redirect all store/report directories to tmp_path for every test."""
    from handlers.shared import site_utils

    monkeypatch.setattr(site_utils, "_PAGE_STORE_DIR", str(tmp_path / "page-store"))
    monkeypatch.setattr(site_utils, "_SITE_REPORTS_DIR", str(tmp_path / "site-reports"))

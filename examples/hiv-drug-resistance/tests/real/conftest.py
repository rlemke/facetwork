"""Shared fixtures for HIV drug resistance integration tests.

These tests use MemoryStore (no MongoDB required) and run in the
normal test suite. Tests that download reference data from NCBI
skip gracefully when there is no network connectivity.
"""

from __future__ import annotations

import os
import sys

import pytest

from afl.runtime import (
    AgentPoller,
    AgentPollerConfig,
    Evaluator,
    MemoryStore,
    Telemetry,
)

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------

# examples/hiv-drug-resistance/tests/real/ → examples/hiv-drug-resistance/
_EXAMPLE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))


def _ensure_resistance_handlers():
    """Purge non-resistance handler modules and put example root on sys.path."""
    for key in list(sys.modules.keys()):
        if key == "handlers" or key.startswith("handlers."):
            mod = sys.modules[key]
            mod_file = getattr(mod, "__file__", "") or ""
            if "hiv-drug-resistance" not in mod_file:
                del sys.modules[key]
    if _EXAMPLE_ROOT in sys.path:
        sys.path.remove(_EXAMPLE_ROOT)
    sys.path.insert(0, _EXAMPLE_ROOT)
    if _THIS_DIR not in sys.path:
        sys.path.insert(0, _THIS_DIR)


# Purge at collection time
_ensure_resistance_handlers()


@pytest.fixture(autouse=True)
def _resistance_handlers_on_path():
    """Ensure hiv-drug-resistance handlers are active before each test."""
    _ensure_resistance_handlers()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def memory_store():
    """Create an in-memory persistence store."""
    return MemoryStore()


@pytest.fixture
def evaluator(memory_store):
    """Create an Evaluator backed by MemoryStore."""
    return Evaluator(persistence=memory_store, telemetry=Telemetry(enabled=False))


@pytest.fixture
def poller(memory_store, evaluator):
    """Create an AgentPoller with no handlers registered."""
    return AgentPoller(
        persistence=memory_store,
        evaluator=evaluator,
        config=AgentPollerConfig(service_name="hiv-integration-test"),
    )


@pytest.fixture
def compiled_program():
    """Compile resistance.afl and return the program dict."""
    from hiv_helpers import compile_resistance_afl

    return compile_resistance_afl()


@pytest.fixture(scope="session")
def hxb2_fasta(tmp_path_factory):
    """Download the HIV-1 HXB2 reference genome FASTA from NCBI.

    Session-scoped so the download happens at most once per test run.
    Skips if the network is unreachable.
    """
    import urllib.error
    import urllib.request

    url = (
        "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
        "?db=nuccore&id=K03455.1&rettype=fasta&retmode=text"
    )

    cache_dir = tmp_path_factory.mktemp("hxb2")
    fasta_path = cache_dir / "HXB2.fasta"

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "AFL-test/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()
    except (urllib.error.URLError, OSError, TimeoutError):
        pytest.skip("NCBI unreachable — skipping HXB2 download test")

    if not data or not data.startswith(b">"):
        pytest.skip("NCBI returned unexpected data — skipping HXB2 download test")

    fasta_path.write_bytes(data)
    return str(fasta_path)

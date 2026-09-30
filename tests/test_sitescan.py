"""facetwork/sitescan.py -- the published-repo check for site identifiers.

Every probe string below is BUILT from parts at runtime, so this file does not
itself contain the identifiers it proves the scanner catches (and stays clean
under both the scanner and tests/test_no_site_identifiers.py).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from facetwork import sitescan

REPO = Path(__file__).resolve().parents[1]


def _dot(*parts: str) -> str:
    return ".".join(parts)


def _kinds(text: str, names: list[str] | None = None) -> list[str]:
    return [f.kind for f in sitescan.scan_text(text, "f", names or [])]


# --- what it must catch ---------------------------------------------------------


@pytest.mark.parametrize(
    "line,kind",
    [
        (f"url = 'http://{_dot('10', '0', '5', '17')}:9000'", "ip"),
        (f"host = '{_dot('buildbox', 'local')}'", "mdns"),
        (f"path = '/{'Users'}/alice/code'", "home"),
        (f"root = '/{'home'}/bob/data'", "home"),
        # The case that prompted the check: a personal address as a User-Agent.
        (f"UA = 'research {'someone' + '@' + _dot('hotmail', 'com')}'", "email"),
    ],
)
def test_it_catches_each_kind(line: str, kind: str) -> None:
    assert _kinds(line) == [kind]


def test_a_forbidden_name_is_caught_but_never_echoed() -> None:
    found = sitescan.scan_text("ssh alpha-bravo-7 uptime", "f", ["alpha-bravo-7"])
    assert [f.kind for f in found] == ["name"]
    assert "alpha-bravo-7" not in str(found[0]), "a CI log is published: never echo the name"


# --- what it must leave alone ---------------------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        f"bind = '{_dot('0', '0', '0', '0')}'",  # unspecified: a protocol constant
        f"lo = '{_dot('127', '0', '0', '1')}'",  # loopback
        f"doc = '{_dot('192', '0', '2', '7')}'",  # RFC 5737 documentation range
        f"probe = '{_dot('8', '8', '8', '8')}'",  # public resolver as a routing probe
        "version = '1.2.3.400'",  # not an address
        "_tls = threading.local()",  # a Python attribute, not a host
        "see servers.local.json",  # a FILE name
        f"alias = '{_dot('afl-mongodb', 'local')}'",  # product service naming
        f"email = 'system@{_dot('facetwork', 'local')}'",  # product identity
        "fixture = 'ada@x.com'",  # made-up fixture address, not a personal mailbox
        "url = 'https://gtexportal.org/home/gene/TP53'",  # a URL path, not a home dir
        "tmp = '/home/runner/work'",  # CI service account
        "x = '/Users/someone/afl_data'",  # placeholder
        f"ok = '{_dot('10', '9', '9', '9')}'  # sitescan: ok",  # explicit escape
    ],
)
def test_it_leaves_non_identifiers_alone(line: str) -> None:
    assert _kinds(line) == []


def test_ignore_file_and_binary_files(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    bad = f"h = '{_dot('buildbox', 'local')}'\n"
    (tmp_path / "a.py").write_text(bad)
    (tmp_path / "vendored.py").write_text(bad)
    (tmp_path / "blob.bin").write_bytes(b"\0\1" + bad.encode())
    (tmp_path / sitescan.IGNORE_FILE).write_text("vendored.py\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)
    found = sitescan.scan_root(tmp_path, [])
    assert [f.path for f in found] == ["a.py"]


def test_catalog_names_exclude_product_aliases(tmp_path: Path) -> None:
    cat = tmp_path / "servers.json"
    cat.write_text(
        '{"servers": [{"name": "'
        + _dot("alpha-bravo-7", "local")
        + '", "aliases": ["afl-minio", "zulu-9"]}]}'
    )
    names = sitescan.names_from_catalog(str(cat))
    assert "alpha-bravo-7" in names and "zulu-9" in names
    assert not any(n.startswith("afl-") for n in names)


def test_runs_as_a_plain_script_before_anything_is_installed() -> None:
    """CI runs it with no package installed; as a script its own directory
    shadowed the stdlib `ast` (the package has an ast.py) until that was fixed."""
    r = subprocess.run(
        [sys.executable, str(REPO / "facetwork" / "sitescan.py"), "--help"],
        capture_output=True,
        text=True,
        cwd=REPO,
    )
    assert r.returncode == 0, r.stderr


def test_this_repository_is_clean() -> None:
    found = sitescan.scan_root(REPO, [])
    assert not found, "\n".join(map(str, found[:20]))

"""No checked-in test may name a real server or spell an IP address.

Tests are published with the repo. They must not disclose the host names or
addresses of any deployment, and they must run unchanged on any cloud, network
or service configuration -- so a test asks tests/_site.py for a host or address
by ROLE and never writes one down. Real values, when a deployment wants them,
live in an UNCOMMITTED site file (see _site.py).

This guard fails on:

- an IPv4 literal (loopback 127/8 and 0.0.0.0 excepted: they are protocol
  constants the code under test checks for, not machines);
- a ``*.local`` (mDNS) host name -- ``*.local.json`` config FILE names and the
  product's built-in ``@facetwork.local`` identities are not hosts;
- any host name or alias in this checkout's server catalog, or listed as
  ``forbidden_names`` in the uncommitted site file. Read at RUNTIME, so this
  file names nothing either.

tests/_site.py itself is exempt: it holds the reserved documentation ranges that
generated values are drawn from, and nothing else.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests import _site

REPO = Path(__file__).resolve().parents[1]
EXEMPT = {Path(_site.__file__).resolve(), Path(__file__).resolve()}

_IPV4 = re.compile(r"(?<![\w.])(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})(?![\w.])")
_MDNS = re.compile(r"(?<![@\w.-])([A-Za-z0-9][\w-]*\.local)\b(?!\.json)")


def _test_files() -> list[Path]:
    files = list((REPO / "tests").rglob("*.py"))
    files += [p for p in (REPO / "examples").rglob("*.py") if "tests" in p.parts]
    return sorted(p for p in files if p.resolve() not in EXEMPT)


def _is_ipv4(groups: tuple[str, ...]) -> bool:
    return all(0 <= int(g) <= 255 for g in groups)


def _allowed_ip(groups: tuple[str, ...]) -> bool:
    return groups[0] == "127" or groups == ("0", "0", "0", "0")


def _findings(path: Path, names: list[str]) -> list[str]:
    text = path.read_text(encoding="utf-8", errors="replace")
    rel = path.relative_to(REPO)
    out = []
    for n, line in enumerate(text.splitlines(), 1):
        for m in _IPV4.finditer(line):
            g = m.groups()
            if _is_ipv4(g) and not _allowed_ip(g):
                out.append(f"{rel}:{n}: IP address {m.group(0)}")
        for m in _MDNS.finditer(line):
            out.append(f"{rel}:{n}: mDNS host name {m.group(1)}")
        low = line.lower()
        for name in names:
            if re.search(rf"(?<![\w-]){re.escape(name.lower())}(?![\w-])", low):
                out.append(f"{rel}:{n}: catalogued host name")   # not echoed: it is private
    return out


def test_no_test_file_names_a_server_or_spells_an_ip() -> None:
    names = _site.forbidden_names()
    found = [f for p in _test_files() for f in _findings(p, names)]
    assert not found, (
        "tests must take host names and addresses from tests/_site.py, never "
        "literals:\n  " + "\n  ".join(found[:40]))


@pytest.mark.parametrize("line", [
    "x = '{}'".format(".".join(["198", "51", "100", "7"])),
    "host = 'box.{}'".format("local"),
])
def test_the_guard_can_fail(tmp_path: Path, line: str) -> None:
    """A guard that cannot go red proves nothing. Built by joining parts, so this
    file does not itself contain the literals it forbids."""
    probe = REPO / "tests" / f"_probe_{tmp_path.name}.py"
    try:
        probe.write_text(line + "\n", encoding="utf-8")
        assert _findings(probe, []), f"guard missed: {line!r}"
    finally:
        probe.unlink(missing_ok=True)


def test_catalogued_names_are_caught_without_being_written_here(tmp_path: Path) -> None:
    probe = REPO / "tests" / f"_probe_{tmp_path.name}.py"
    try:
        probe.write_text("name = 'alpha-bravo-7'\n", encoding="utf-8")
        assert _findings(probe, ["alpha-bravo-7"])
        assert not _findings(probe, [])
    finally:
        probe.unlink(missing_ok=True)

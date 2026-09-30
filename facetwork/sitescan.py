"""Find site identifiers in a repository: host names, addresses, home paths, emails.

A published repository must not disclose the machines, network or people of the
deployment it came from, and must run unchanged anywhere else. Those values
belong in site configuration that is never committed (see
``servers.example.json`` and ``tests/_site.py``). This scanner is the check:
run over every tracked file, it reports

- ``ip``      an IPv4 literal -- loopback, 0.0.0.0 and the RFC 5737
              documentation ranges excepted (protocol constants and reserved
              examples, not machines);
- ``mdns``    a ``*.local`` host name (``*.local.json``-style FILE names and the
              product's ``@facetwork.local`` identities are not hosts);
- ``home``    a personal home path, ``/Users/<name>/`` or ``/home/<name>/``;
- ``email``   a PERSONAL email address (consumer mail providers: gmail,
              hotmail/outlook, yahoo, icloud, proton, ...);
- ``name``    a host name the CALLER forbids (``--names``, ``$FW_FORBIDDEN_NAMES``
              or ``--catalog``). Reported WITHOUT the name, because the output of
              a CI job is itself published.

The generic checks need no configuration, so they run in any CI for any
repository. A site's real host names cannot be listed in a published repo
without publishing them, which is why ``name`` takes them at run time.

Escapes, for the rare legitimate case: a line containing ``sitescan: ok``, or a
glob in a ``.sitescan-ignore`` file at the repository root.

Standard library only -- CI runs it before installing anything::

    python facetwork/sitescan.py [ROOT ...] [--names a,b] [--catalog servers.json]

Exit 0 clean, 1 findings, 2 could not scan.
"""

from __future__ import annotations

import sys as _sys

# Run as a plain script (`python facetwork/sitescan.py`), this file's directory
# is sys.path[0] -- and the package's own ast.py then shadows the standard
# library's `ast`, which dataclasses imports. Drop it before importing anything.
if __name__ == "__main__":
    _here = __file__.rsplit("/", 1)[0] or "."
    _sys.path = [p for p in _sys.path if p not in (_here, "")]

import argparse
import fnmatch
import ipaddress
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

MAX_BYTES = 20 * 1024 * 1024  # a larger file is data, not something a person wrote
ALLOW_MARK = "sitescan: ok"
IGNORE_FILE = ".sitescan-ignore"

_IPV4 = re.compile(r"(?<![\w.])(\d{1,3}(?:\.\d{1,3}){3})(?![\w.])")
_MDNS = re.compile(
    r"(?<![\w.@-])([A-Za-z0-9][\w-]*\.local)\b(?!\.(?:json|env|ya?ml|toml|py|sh|md))"
)
_HOME = re.compile(r"(?<![\w.])/(?:Users|home)/([A-Za-z][\w.-]*)/")
_EMAIL = re.compile(r"(?<![\w.+-])([\w.+-]+@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}))\b")

# Placeholder or service accounts, not a person's home directory.
_HOME_OK = {
    "someone",
    "user",
    "username",
    "you",
    "yourname",
    "runner",
    "fleet",
    "ubuntu",
    "ec2-user",
    "admin",
    "root",
    "example",
    "me",
    "shared",
    "Shared",
}
_DOC_NETS = tuple(
    ipaddress.ip_network(n) for n in ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24")
)


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    kind: str
    value: str  # empty for `name`: the forbidden name is private

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.kind}" + (f" {self.value}" if self.value else "")


# Well-known public resolvers: used as a routing-probe target ("which interface
# would reach the internet?"), never a site's machine.
_PUBLIC_RESOLVERS = {"8.8.8.8", "8.8.4.4", "1.1.1.1", "1.0.0.1", "9.9.9.9"}
# `<name>.local` that is not a host: Python's thread-local attribute, and the
# placeholder names templates and docs use.
_NOT_A_HOST = {
    "threading",
    "_thread",
    "self",
    "host",
    "example",
    "my-host",
    "myhost",
    "hostname",
    "your-host",
    "yourhost",
}


def _ip_ok(text: str) -> bool:
    if text in _PUBLIC_RESOLVERS:
        return True
    try:
        ip = ipaddress.ip_address(text)
    except ValueError:
        return True  # 999.1.2.3 is not an address (a version, a date, a coordinate)
    return ip.is_loopback or ip.is_unspecified or any(ip in n for n in _DOC_NETS)


def _mdns_ok(name: str) -> bool:
    label = name.rsplit(".", 1)[0].lower()
    return label in _NOT_A_HOST or label.endswith("-host") or label.startswith(("afl-", "example"))


# A PERSONAL mailbox is the disclosure risk (a contact address hard-coded as a
# User-Agent was the case that prompted this). Fixtures use made-up addresses
# on arbitrary domains -- ada@x.com -- which no rule can tell from a real work
# address, so the check targets consumer mail providers, where an address is
# almost always a real person's.
_PERSONAL_MAIL = (
    "gmail.com",
    "googlemail.com",
    "hotmail.com",
    "outlook.com",
    "live.com",
    "msn.com",
    "yahoo.com",
    "ymail.com",
    "icloud.com",
    "me.com",
    "mac.com",
    "aol.com",
    "proton.me",
    "protonmail.com",
    "gmx.com",
    "gmx.net",
    "fastmail.com",
    "zoho.com",
    "mail.com",
    "yandex.com",
    "hey.com",
)


def _email_ok(addr: str, domain: str) -> bool:
    d = domain.lower()
    return not any(d == p or d.endswith("." + p) for p in _PERSONAL_MAIL)


def scan_text(text: str, path: str, names: list[str]) -> list[Finding]:
    """Findings for one file's text. ``names`` are forbidden host names."""
    out: list[Finding] = []
    name_res = [re.compile(rf"(?<![\w-]){re.escape(n)}(?![\w-])", re.I) for n in names if n]
    for n, line in enumerate(text.splitlines(), 1):
        if ALLOW_MARK in line:
            continue
        for m in _IPV4.finditer(line):
            if not _ip_ok(m.group(1)):
                out.append(Finding(path, n, "ip", m.group(1)))
        for m in _MDNS.finditer(line):
            if not _mdns_ok(m.group(1)):
                out.append(Finding(path, n, "mdns", m.group(1)))
        for m in _HOME.finditer(line):
            if m.group(1) not in _HOME_OK and not m.group(1).startswith(("<", "$", "{")):
                out.append(Finding(path, n, "home", m.group(0)))
        for m in _EMAIL.finditer(line):
            if not _email_ok(m.group(1), m.group(2)):
                out.append(Finding(path, n, "email", m.group(1)))
        if any(r.search(line) for r in name_res):
            out.append(Finding(path, n, "name", ""))
    return out


def _tracked_files(root: Path) -> list[Path]:
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z"], capture_output=True, text=True, check=True
        ).stdout
        return [root / p for p in out.split("\0") if p]
    except (OSError, subprocess.CalledProcessError):
        return [p for p in root.rglob("*") if p.is_file() and ".git" not in p.parts]


def _ignores(root: Path) -> list[str]:
    try:
        return [
            ln.strip()
            for ln in (root / IGNORE_FILE).read_text().splitlines()
            if ln.strip() and not ln.startswith("#")
        ]
    except OSError:
        return []


def scan_root(root: Path, names: list[str]) -> list[Finding]:
    """Scan every tracked file under ``root`` (text only; binaries are skipped)."""
    ignores = _ignores(root)
    found: list[Finding] = []
    for p in _tracked_files(root):
        rel = p.relative_to(root).as_posix()
        if any(fnmatch.fnmatch(rel, g) for g in ignores) or rel == IGNORE_FILE:
            continue
        try:
            if p.is_symlink() or p.stat().st_size > MAX_BYTES:
                continue
            raw = p.read_bytes()
        except OSError:
            continue
        if b"\0" in raw[:8192]:
            continue  # binary
        found += scan_text(raw.decode("utf-8", "replace"), rel, names)
    return found


def names_from_catalog(path: str) -> list[str]:
    """Host names and short names from a server catalog (never its afl-* aliases,
    which are product service names, not machines)."""
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    rows = doc.get("servers") if isinstance(doc, dict) else doc
    out: set[str] = set()
    for s in rows or []:
        if not isinstance(s, dict):
            continue
        for n in [s.get("name"), *(s.get("aliases") or [])]:
            if n and not str(n).startswith("afl-"):
                out.update({str(n), str(n).split(".")[0]})
    return sorted(n for n in out if len(n) >= 4)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="sitescan", description=__doc__.split("\n\n")[0])
    ap.add_argument("roots", nargs="*", default=["."], help="repositories to scan (default .)")
    ap.add_argument("--names", default="", help="comma-separated host names to forbid")
    ap.add_argument("--catalog", help="forbid every host named in this server catalog")
    args = ap.parse_args(argv)

    names = [
        n.strip()
        for n in (args.names or os.environ.get("FW_FORBIDDEN_NAMES", "")).split(",")
        if n.strip()
    ]
    if args.catalog:
        names += names_from_catalog(args.catalog)

    total = 0
    for r in args.roots:
        root = Path(r).resolve()
        if not root.is_dir():
            print(f"sitescan: {r} is not a directory", file=sys.stderr)
            return 2
        found = scan_root(root, names)
        for f in found:
            print(f"{r.rstrip('/')}/{f}" if len(args.roots) > 1 else str(f))
        total += len(found)
    if total:
        print(
            f"\nsitescan: {total} site identifier(s). Move them to uncommitted site "
            f"configuration, use a role or placeholder, or mark a genuine exception "
            f"with '{ALLOW_MARK}'.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""CLI for the server catalog.

    python -m facetwork.servers                 # table: name, group, resolved IP, purpose
    python -m facetwork.servers --env           # shell exports (FW_INFRA_HOST/FW_INFRA_IP)
    python -m facetwork.servers --infra-name    # just the infra host's stable name
    python -m facetwork.servers --resolve NAME  # current IP for a name/alias

``--env`` is the docker/compose materialization hook: a start script (or the
fleet-agent via ``runner/start --fleet``) evals it so container ``extra_hosts``
get the infra host's CURRENT address each (re)start — containers can't resolve
mDNS themselves.
"""

from __future__ import annotations

import argparse
import sys

from . import catalog


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="facetwork.servers", description="Server catalog")
    p.add_argument("--env", action="store_true", help="print shell exports for the infra host")
    p.add_argument("--infra-name", action="store_true", help="print the infra host's stable name")
    p.add_argument("--resolve", metavar="NAME", help="print the current IP for a name/alias")
    args = p.parse_args(argv)

    if args.infra_name:
        entry = catalog.infra()
        if not entry:
            print("no infra entry in the server catalog", file=sys.stderr)
            return 1
        print(entry.get("name", ""))
        return 0

    if args.resolve:
        ip = catalog.resolve_ip(args.resolve)
        if not ip:
            print(f"cannot resolve '{args.resolve}' (unknown entry or resolution failed)",
                  file=sys.stderr)
            return 1
        print(ip)
        return 0

    if args.env:
        entry = catalog.infra()
        if not entry:
            print("no infra entry in the server catalog", file=sys.stderr)
            return 1
        ip = catalog.resolve_ip(entry)
        print(f"export FW_INFRA_HOST={entry.get('name', '')}")
        if ip:
            print(f"export FW_INFRA_IP={ip}")
        return 0

    src = catalog.catalog_source()
    entries = catalog.servers()
    if not entries:
        print(f"server catalog empty ({src})", file=sys.stderr)
        return 1
    print(f"server catalog: {src}")
    for s in entries:
        ip = catalog.resolve_ip(s) or "UNRESOLVED"
        tags = []
        if s.get("infra"):
            tags.append("infra")
        if s.get("ip_pin"):
            tags.append("pinned")
        alias = ",".join(s.get("aliases") or [])
        print(f"  {s.get('name', '?'):<18} {s.get('group', '-'):<8} {ip:<16}"
              f" {('[' + ' '.join(tags) + ']') if tags else '':<9}"
              f" {s.get('purpose', '')}" + (f"  (aliases: {alias})" if alias else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

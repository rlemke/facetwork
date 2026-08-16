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
    p.add_argument(
        "--container-ip",
        nargs="?",
        const="",
        metavar="NAME",
        help="print the address containers should map afl-* to (default: the infra "
        "host); 'host-gateway' when that host is this machine",
    )
    args = p.parse_args(argv)

    if args.container_ip is not None:
        val = catalog.container_ip(args.container_ip or None)
        if not val:
            print(
                f"cannot resolve '{args.container_ip or 'the infra host'}' "
                "(unknown entry or resolution failed)",
                file=sys.stderr,
            )
            return 1
        print(val)
        return 0

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
            print(
                f"cannot resolve '{args.resolve}' (unknown entry or resolution failed)",
                file=sys.stderr,
            )
            return 1
        print(ip)
        return 0

    if args.env:
        entry = catalog.infra()
        if not entry:
            print("no infra entry in the server catalog", file=sys.stderr)
            return 1
        # FW_INFRA_IP is CONTAINER-facing (it lands in compose extra_hosts), so
        # it is the gateway alias when infra is this machine — see
        # catalog.container_ip. FW_INFRA_HOST_IP carries the numeric address for
        # host-side probes, which cannot use that alias.
        ip = catalog.container_ip(entry)
        host_ip = catalog.resolve_ip(entry)
        print(f"export FW_INFRA_HOST={entry.get('name', '')}")
        if ip:
            print(f"export FW_INFRA_IP={ip}")
        if host_ip:
            print(f"export FW_INFRA_HOST_IP={host_ip}")
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
        print(
            f"  {s.get('name', '?'):<18} {s.get('group', '-'):<8} {ip:<16}"
            f" {('[' + ' '.join(tags) + ']') if tags else '':<9}"
            f" {s.get('purpose', '')}" + (f"  (aliases: {alias})" if alias else "")
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

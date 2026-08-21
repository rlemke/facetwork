"""Generate the per-domain ``runner-<service>`` compose blocks from the catalog.

`fw util gen-compose` regenerates every compose domain's service block in
`docker-compose.full-stack.yml`, in place between the markers:

    # >>> BEGIN generated domain runners (fw util gen-compose — do not edit) >>>
    …generated blocks…
    # <<< END generated domain runners <<<

Everything outside the markers (the `x-*` anchors, infra services, `runner`,
`runner-gh-router`, `runner-ffl`, volumes, networks) is hand-written and left
untouched. The block shape comes from each domain's catalog `compose` object
(`facetwork/domains/catalog.py`); structural notes that used to be inline YAML
comments now live in the catalog (`compose.notes`) and are re-emitted as comments.

`--check` writes nothing and exits non-zero if the marked region is out of sync
with the catalog (CI guard). Correctness gate for any change here: `docker compose
-f docker-compose.full-stack.yml config` must be byte-identical before and after.
"""

from __future__ import annotations

import sys
from pathlib import Path

from facetwork.domains.catalog import compose_domains

_REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE = _REPO_ROOT / "docker-compose.full-stack.yml"

BEGIN = "  # >>> BEGIN generated domain runners (fw util gen-compose — do not edit) >>>\n"
END = "  # <<< END generated domain runners <<<\n"

_POSTGIS_URL = (
    "postgresql://${POSTGRES_USER:-afl}:${POSTGRES_PASSWORD:-afl}"
    "@postgis:5432/${POSTGRES_DB:-afl_gis}"
)
_HANDLERS = "${FWH_HANDLERS_ROOT:-$HOME/fw_handlers}"
_DATA_DIR = "${FW_DATA_DIR:-/Volumes/afl_data}"


def _registry_args(spec: dict) -> str:
    args = f"--task-list {spec['task_list']}"
    if spec.get("server_group_arg"):
        args += " --server-group ${FW_SERVER_GROUP:-default}"
    return args


def _extras_value(spec: dict) -> str | None:
    ce = spec.get("compose_extras")
    if not ce:
        return None
    var = spec.get("extras_var")
    return f"${{{var}:-{ce}}}" if var else ce


def render_block(name: str, spec: dict) -> str:
    """Render one ``runner-<service>:`` YAML block from a catalog domain spec."""
    c = spec.get("compose", {}) or {}
    svc = spec["service"]
    L: list[str] = [f"  {svc}:\n"]
    for note in c.get("notes", []):
        L.append(f"    # {note}\n")
    if c.get("hostname"):
        L.append("    hostname: ${FW_FLEET_HOST:-}\n")
    L += [
        "    build:\n",
        "      context: .\n",
        "      dockerfile: docker/Dockerfile.domain-runner\n",
        "    depends_on:\n",
        "      mongodb:\n",
        "        condition: service_healthy\n",
    ]
    for dep, cond in c.get("depends", []):
        L.append(f"      {dep}:\n        condition: {cond}\n")
    L += [
        "      minio-setup:\n",
        "        condition: service_completed_successfully\n",
        "    environment:\n",
    ]
    anchor = "osm-s3-env" if c.get("storage") == "osm" else "s3-storage"
    L.append(f"      <<: *{anchor}\n")
    L.append("      FW_MONGODB_URL: mongodb://mongodb:27017\n")
    L.append("      FW_MONGODB_DATABASE: ${FW_MONGODB_DATABASE:-facetwork}\n")
    L.append(f"      FW_DOMAIN_NAME: {name}\n")
    L.append(f"      FW_DOMAIN_REPO: {spec['repo']}\n")
    if c.get("postgis"):
        L.append(f"      FW_POSTGIS_URL: {_POSTGIS_URL}\n")
    ev = _extras_value(spec)
    if ev is not None:
        L.append(f"      FW_DOMAIN_EXTRAS: {ev}\n")
    for k, v in c.get("env", []):
        L.append(f"      {k}: {v}\n")
    L.append(f'      FW_REGISTRY_RUNNER_ARGS: "{_registry_args(spec)}"\n')
    L.append("    volumes:\n")
    L.append(f"      - {_HANDLERS}/{spec['repo']}:/handlers/{spec['repo']}\n")
    vols = c.get("volumes") or [f"{_DATA_DIR}:/Volumes/afl_data"]
    for vol in vols:
        L.append(f"      - {vol}\n")
    L.append("    restart: unless-stopped\n")
    if spec.get("_consolidated"):
        # Behind a profile, so a plain `docker compose up -d` does NOT start it:
        # this domain's work is served by runner-generalist. Opt back in for one
        # host with `--profile per-domain` (or drop it from the catalog's
        # "consolidated" list) the moment the domain gets busy or slow enough to
        # deserve its own bulkhead again.
        L.append('    profiles: ["per-domain"]\n')
    return "".join(L)


def _render_generalist(names: list[str]) -> str:
    """The one runner that fronts every consolidated domain.

    Generated rather than hand-written, even though it is not itself a domain:
    its membership list and the `profiles:` keys that silence the per-domain
    blocks are the SAME fact. Two hand-maintained copies would drift, and the
    failure mode is silent — a domain in neither set is simply never claimed by
    anyone, which looks exactly like an idle queue.
    """
    from .catalog import index_dir

    # READ-ONLY on purpose: runners CONSUME indexes, only the host publish job
    # writes them. A runner cannot corrupt the thing every map depends on.
    idx = index_dir()
    idx_mount = f"      - {idx}:/opt/fw_osm_indexes:ro\n" if idx else ""
    idx_env = ("      FW_OSM_INDEX_ROOT: /opt/fw_osm_indexes\n" if idx else "")

    return (
        "  # Generalist runner: ONE container serving every domain listed in the\n"
        "  # catalog's `consolidated` set, whose own per-domain blocks are behind\n"
        "  # the `per-domain` profile. Claiming stays name-filtered server-side, so\n"
        "  # this runner claims exactly these namespaces and cannot reach a hot\n"
        "  # domain's queue.\n"
        "  runner-generalist:\n"
        "    build:\n"
        "      context: .\n"
        "      dockerfile: docker/Dockerfile.domain-runner\n"
        '    entrypoint: ["/usr/local/bin/entrypoint-generalist.sh"]\n'
        "    depends_on:\n"
        "      mongodb:\n"
        "        condition: service_healthy\n"
        "      minio-setup:\n"
        "        condition: service_completed_successfully\n"
        "    environment:\n"
        "      <<: *s3-storage\n"
        "      FW_MONGODB_URL: mongodb://mongodb:27017\n"
        "      FW_MONGODB_DATABASE: ${FW_MONGODB_DATABASE:-facetwork}\n"
        f"      FW_DOMAIN_NAMES: {','.join(names)}\n"
        "      # More workers than a single-domain runner: this one fronts several\n"
        "      # queues, so one slow handler should not block the rest.\n"
        '      FW_REGISTRY_RUNNER_ARGS: "--max-concurrent ${FW_GENERALIST_WORKERS:-4}"\n'
        + idx_env
        + "    volumes:\n"
        f"      - {_DATA_DIR}:/Volumes/afl_data\n"
        + idx_mount
        + "    restart: unless-stopped\n"
    )


def _render_region() -> str:
    from .catalog import consolidated_domains

    cold = consolidated_domains()
    specs = dict(compose_domains())
    for n in cold:
        if n in specs:
            specs[n] = {**specs[n], "_consolidated": True}
    blocks = [render_block(n, s) for n, s in sorted(specs.items())]
    if cold:
        blocks.append(_render_generalist(cold))
    return BEGIN + "\n".join(blocks) + END


def sync(*, write: bool) -> tuple[bool, str]:
    """Return (changed, message). With write=True, rewrite the marked region."""
    text = COMPOSE.read_text(encoding="utf-8")
    if BEGIN not in text or END not in text:
        raise SystemExit(
            f"markers not found in {COMPOSE.name} — expected the BEGIN/END "
            "generated-domain-runners markers around the runner blocks."
        )
    pre, rest = text.split(BEGIN, 1)
    _old, post = rest.split(END, 1)
    new_text = pre + _render_region() + post
    changed = new_text != text
    if changed and write:
        COMPOSE.write_text(new_text, encoding="utf-8")
    return changed, ("region regenerated" if changed else "already in sync")


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    check = "--check" in argv
    changed, msg = sync(write=not check)
    if check:
        if changed:
            print(
                "docker-compose.full-stack.yml is OUT OF SYNC with the catalog "
                "(generated domain-runner region differs). Run `fw util gen-compose`."
            )
            return 1
        print("docker-compose.full-stack.yml generated region is in sync with the catalog.")
        return 0
    print(f"gen-compose: {msg} ({len(compose_domains())} domain blocks).")
    return 0


if __name__ == "__main__":
    sys.exit(main())

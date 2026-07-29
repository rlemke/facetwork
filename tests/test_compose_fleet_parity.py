"""Every runner service in the full-stack compose file must have a fleet override.

A service defined only in docker-compose.full-stack.yml has `build:` and no
`image:`, so on a fleet host it runs a LOCALLY BUILT image that no
`fw fleet rollout` can ever update. That is not hypothetical: county-atlas sat 29h
behind on a stale local build, did not understand the `after` clause, rebuilt a
dependency graph without the ordering edge, and created a step while its producer
was still running -- a bug that looked like a runtime fault for hours.

`fw util gen-compose` generates the full-stack block for each catalog domain but
does NOT add the fleet override (that file is hand-maintained, because
`depends_on: !reset []` must sit inline per service and does not survive a `<<:`
merge). So adding a domain re-opens this gap silently. This test closes it.

Parsed with a regex rather than PyYAML: the fleet file uses the `!reset` tag,
which PyYAML rejects as an unknown tag.
"""

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
FULL_STACK = ROOT / "docker-compose.full-stack.yml"
FLEET = ROOT / "docker-compose.fleet.yml"

SERVICE = re.compile(r"^  (runner-[a-z0-9-]+):", re.M)


def _services(path: pathlib.Path) -> set[str]:
    return set(SERVICE.findall(path.read_text()))


def test_compose_files_exist():
    assert FULL_STACK.is_file() and FLEET.is_file()


def test_every_full_stack_runner_has_a_fleet_override():
    full_stack = _services(FULL_STACK)
    fleet = _services(FLEET)
    assert full_stack, "no runner-* services parsed from the full-stack file"

    missing = sorted(full_stack - fleet)
    assert not missing, (
        "these runner services have no docker-compose.fleet.yml override, so a "
        "fleet host would build them locally and no rollout could update them: "
        + ", ".join(missing)
        + "\nAdd a stanza mirroring the others:\n"
        "  <name>:\n"
        "    <<: *fleet-runner\n"
        "    depends_on: !reset []\n"
        "    environment:\n"
        "      <<: *fleet-env"
    )


def test_fleet_overrides_do_not_reference_unknown_services():
    """An override for a service that no longer exists is dead config."""
    stray = sorted(_services(FLEET) - _services(FULL_STACK))
    assert not stray, (
        "docker-compose.fleet.yml overrides services absent from the full-stack "
        "file (renamed or removed?): " + ", ".join(stray)
    )

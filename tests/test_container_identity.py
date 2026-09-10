"""Mapping a server record back to its container.

⚠️ A count mismatch on a host was UNDIAGNOSABLE. Every runner on a host registers
with the same `server_name` and the same `service_name` ("afl-runner"), so
"23 containers but 22 records" resolved to a number and nothing else. Three hosts
showed it simultaneously and none could be investigated.
"""
import pathlib

REPO = pathlib.Path(__file__).resolve().parents[1]


def test_container_id_returns_empty_off_container_rather_than_guessing():
    """A bare-metal runner has no container. Inventing one would make it look
    like a container nobody can find — worse than reporting nothing."""
    from facetwork.runtime.entities.server import container_id
    cid = container_id()
    assert cid == "" or (len(cid) == 12 and all(c in "0123456789abcdef" for c in cid))


def test_detection_reads_proc_not_the_hostname():
    """compose — or an operator — may set a hostname, at which point the hostname
    stops being the container ID and the mapping silently breaks."""
    src = (REPO / "facetwork/runtime/entities/server.py").read_text()
    i = src.index("def container_id()")
    body = src[i:i + 1800]
    assert "/proc/self/cgroup" in body
    assert "/proc/self/mountinfo" in body, "cgroup v2 often gives only '0::/'"
    assert "gethostname" not in body


def test_no_registration_site_can_omit_it():
    """⚠️ THREE subclasses build ServerDefinition independently (RegistryRunner,
    AgentPoller, RunnerService). A field set at the call sites gets added to one
    and missed by the others — the recurring defect shape in this codebase. A
    default_factory makes omission impossible.
    """
    import dataclasses
    from facetwork.runtime.entities.server import ServerDefinition, container_id
    f = {x.name: x for x in dataclasses.fields(ServerDefinition)}["container"]
    assert f.default_factory is container_id, "must be a default_factory, not a default"
    # And a construction that names nothing still carries it.
    s = ServerDefinition(uuid="u", server_group="g", service_name="afl-runner",
                         server_name="h")
    assert hasattr(s, "container")


def test_it_survives_a_persistence_round_trip():
    """A field written but not read back is invisible to every consumer."""
    src = (REPO / "facetwork/runtime/mongo_store/servers.py").read_text()
    assert '"container": getattr(server, "container", "") or "",' in src, "write path"
    assert 'container=doc.get("container", "") or "",' in src, "read path"


def test_an_old_record_without_the_field_still_loads():
    """Records written by an older image have no `container`; reading must not
    raise, or the whole roster becomes unreadable during a rollout."""
    src = (REPO / "facetwork/runtime/mongo_store/servers.py").read_text()
    assert 'doc.get("container"' in src and 'doc["container"]' not in src


def test_the_checker_separates_cannot_verify_from_all_clear():
    """⚠️ During a rollout, records predating the field carry nothing and EVERY
    container looks unregistered. That must be exit 2, never a silent pass and
    never a false alarm naming every container on the host."""
    src = (REPO / "scripts/lib/fleet/unregistered").read_text()
    assert "predating the field" in src
    assert "sys.exit(2)" in src and "sys.exit(1)" in src
    assert "0 all containers accounted for" in src


def test_the_checker_names_containers_and_gives_the_next_command():
    """The whole point is turning a number into a name you can act on."""
    src = (REPO / "scripts/lib/fleet/unregistered").read_text()
    assert "UNREGISTERED" in src
    assert "docker logs --tail 50" in src


def test_the_checker_resolves_infra_through_the_catalog():
    """afl-mongodb is a hand-maintained /etc/hosts mapping that has gone stale
    three times in two days, making a healthy fleet look dead."""
    src = (REPO / "scripts/lib/fleet/unregistered").read_text()
    assert "from facetwork.servers import catalog" in src
    assert ".venv/bin/python3" in src, "a bare python3 has no pymongo"

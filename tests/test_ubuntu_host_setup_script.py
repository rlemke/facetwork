"""The Ubuntu fleet-host setup script.

Not a style check. Every assertion here corresponds to something that actually
broke on this fleet, and the script exists so the next host does not rediscover
it. If one of these fails, the script has lost a hard-won fix.
"""
import pathlib

REPO = pathlib.Path(__file__).resolve().parents[1]
SH = REPO / "docs/operations/fleet-agent/setup-ubuntu-fleet-host.sh"
SRC = SH.read_text()


def test_script_exists_and_is_executable():
    import os
    assert SH.exists() and os.access(SH, os.X_OK)


def test_lives_outside_the_command_registry():
    """scripts/lib/<group>/ IS the `fw` command registry — anything dropped there
    becomes a command. A setup script also cannot live there for a second reason:
    it runs BEFORE the repo is cloned."""
    assert "scripts/lib" not in str(SH.relative_to(REPO))


def test_installs_docker_compose_v2_explicitly():
    """docker.io does NOT bring it. Its absence presents as
    "no compose service 'runner-<name>'" for every domain — which reads as
    compose-file drift, not a missing package."""
    assert "docker-compose-v2" in SRC


def test_venv_package_is_version_derived_not_hardcoded():
    """Ubuntu ships python3 without ensurepip; the venv package is version-matched
    (python3.14-venv on 26.04). Hardcoding a version breaks on the next release."""
    assert "sys.version_info.minor" in SRC
    assert "python3.14-venv" not in SRC.replace("# ", "")  # only in comments/output


def test_warns_about_homebrew_docker_conflict():
    """Two daemons fight over /var/run/docker.pid and the service fails with an
    opaque "control process exited with error code"."""
    assert "brew uninstall docker" in SRC


def test_disables_sleep():
    """⚠️ A suspended host is indistinguishable from a stalled workflow. Desktop
    Ubuntu suspends on idle by default; a 7.4h hibernation once looked exactly
    like a frozen fan-out."""
    assert "mask sleep.target" in SRC
    assert "sleep-inactive-ac-type" in SRC, "GNOME's own idle-suspend ignores the targets"


def test_configures_the_insecure_registry_and_merges_the_file():
    """Plain-HTTP registry: without this, pulls fail with "server gave HTTP
    response to HTTPS client", ZERO runners start, and preflight still passes."""
    assert "insecure-registries" in SRC
    assert "json.loads(path.read_text())" in SRC, "must merge, not clobber daemon.json"


def test_adds_the_user_to_the_docker_group_and_says_relogin_is_needed():
    assert "usermod -aG docker" in SRC
    assert "LOG OUT AND BACK IN" in SRC


def test_documents_both_gnome_rdp_services_and_the_redirection_trap():
    """The measured failure: Remote Login authenticates fine, then hands over with
    RDP server redirection, which the Mac 'Windows App' client does not follow —
    and reports it as a credentials error."""
    assert "Desktop Sharing" in SRC and "Remote Login" in SRC
    assert "redirection" in SRC.lower()
    assert "xrdp" in SRC, "the alternative for clients that cannot follow redirection"


def test_warns_that_autologin_blocks_remote_login():
    assert "AutomaticLoginEnable" in SRC


def test_sets_up_time_sync():
    """Leases, heartbeats and the dead-server reaper are wall-clock comparisons
    against timestamps written by OTHER hosts."""
    assert "chrony" in SRC


def test_default_group_is_the_light_tier():
    """'heavy' opts a host into the OSM tier, where one europe cut peaked at
    18.9 GB RSS. The default must be the safe one."""
    assert 'SERVER_GROUP="${FW_SERVER_GROUP:-runner}"' in SRC
    assert "18.9 GB" in SRC, "the reason must travel with the flag"


def test_dry_run_does_not_require_sudo():
    """A dry run exists so the plan can be reviewed BEFORE granting anything;
    requiring credentials to print it makes it unreviewable over ssh."""
    i = SRC.index('if [ "$DRY" = 0 ]; then\n    sudo -v')
    assert i > 0


def test_verifies_at_the_end_and_fails_loudly():
    """⚠️ A half-provisioned host does not error — it silently never claims work.
    So the script must not exit 0 on a partial setup."""
    assert "silently never claims work" in SRC
    assert "exit 1" in SRC.split("=== verify")[-1] or "exit 1" in SRC


def test_infra_host_is_a_name_resolved_at_run_time():
    """A pinned IP reintroduces exactly the DHCP drift the server catalog exists
    to remove — this fleet's infra host moved three times in two days."""
    assert 'INFRA_HOST="${FW_INFRA_HOST:-server3.local}"' in SRC
    assert "getent hosts" in SRC


# --------------------------------------------------------------------------
# bugs found by the first real run, on atopnuc01 (2026-09-09)
# --------------------------------------------------------------------------
def test_masked_check_does_not_depend_on_systemctl_exit_code():
    """⚠️ `systemctl is-enabled <unit>` exits 1 for a MASKED unit while correctly
    printing "masked". Under `set -o pipefail`, `... | grep -q masked` then fails
    even though grep matched — so a correctly configured host was reported as
    FAILING. In a script whose non-zero exit says "do not trust this host", a
    false failure is worse than a false pass.
    """
    assert 'systemctl is-enabled sleep.target 2>&1 | grep -q masked' not in SRC
    assert '= masked ]' in SRC, "compare the output, not the exit status"


def test_pipefail_is_on_so_the_above_matters():
    assert "set -euo pipefail" in SRC


def test_no_python3_pip_package():
    """It drags in python3-dev/libpython3-dev/zlib1g-dev — ~42 MB of build headers
    for nothing. `python3 -m venv` provides the only pip this host uses."""
    import re
    pkgs = SRC[SRC.index("PKGS=("):SRC.index(")", SRC.index("PKGS=("))]
    assert "python3-pip" not in pkgs


def test_stale_registry_addresses_are_pruned():
    """⚠️ Appending accumulates one address per DHCP lease the infra host has ever
    had — measured as server3.local:5050 + .67:5050 + .112:5050 on one machine.
    Each entry grants plain-HTTP trust to whatever holds that address TODAY, and a
    released lease gets reassigned to a different device.
    """
    assert "pruning stale registry addresses" in SRC
    assert "r != ip_reg" in SRC


def test_prune_keeps_the_name_and_other_ports():
    """The NAME follows the host across leases and must survive; an unrelated
    registry on a different port must not be collateral damage."""
    assert "re.escape(port)" in SRC, "prune must be scoped to this registry's port"
    assert "for r in (name_reg, ip_reg)" in SRC


# --------------------------------------------------------------------------
# bugs found by the first run on a FRESH host, macmini01 (2026-09-09)
# --------------------------------------------------------------------------
def test_ssh_key_is_validated_before_being_written():
    """⚠️ An unusable key is SILENT. A key pasted through a terminal or chat
    arrives wrapped or truncated more often than not; the script reported
    "authorized_keys updated" and login still failed — discoverable only at the
    next attempt, from a machine you may no longer be able to reach.
    ssh-keygen -l is the same parser sshd uses.
    """
    assert "ssh-keygen -lf -" in SRC
    assert "NOT a valid public key" in SRC
    assert "ssh-copy-id -i " in SRC, (
        "must name the reliable alternative WITH -i: without it ssh-copy-id picks "
        "its own identity and can install a key the client never offers")


def test_a_rejected_key_does_not_get_written():
    """Refusing must mean refusing — not warning and appending anyway."""
    # Structural, not distance-based: assert the ORDER of the three facts, so
    # adding explanatory comments between them cannot break the test.
    # Search FROM the rejection onward. A bare .index() finds the variable
    # declaration at the top of the script and the SSH phase's opening `if`,
    # both of which precede the rejection and make the ordering look inverted.
    reject = SRC.index("NOT a valid public key")
    clear = SRC.index('SSH_KEY=""', reject)
    guard = SRC.index('[ -n "$SSH_KEY" ]', clear)
    write = SRC.index("authorized_keys updated", guard)
    assert reject < clear < guard < write, (
        "the rejected key must be cleared BEFORE the guarded write, or the script "
        "warns and appends anyway")


def test_docker_group_relogin_is_pending_not_failure():
    """⚠️ A group change cannot apply to the shell that made it. Reporting that
    expected first-run state as a FAILURE, under a banner reading "do not trust
    this host", fires on every new machine — the same cry-wolf defect as the
    pipefail check, and it would teach you to ignore the verdict that matters.
    """
    assert "NEW_DOCKER_GROUP" in SRC
    assert "PEND  docker daemon reachable" in SRC
    assert "PEND  agent running" in SRC


def test_pending_exits_zero_with_the_next_step():
    """Expected-and-incomplete is not failure; it must still say what to do."""
    i = SRC.index("Setup complete except for one expected step")
    seg = SRC[i:i + 600]
    assert "systemctl enable --now facetwork-fleet-agent" in seg
    assert "exit 0" in seg
    assert "Nothing is wrong" in seg


def test_real_failures_still_exit_nonzero():
    assert "Setup finished WITH FAILURES" in SRC
    assert "exit 1" in SRC

"""`fw svc … --install` must work on Linux, not only macOS.

⚠️ Why: every service installer (osm-extracts, osm-replicate, osm-watchdog,
osm-admin-regen, stocks-snapshot) guarded on `uname != Darwin` and EXITED 1.
That was invisible while the fleet was two Macs. It is now majority Linux
(beelink01, three macminis, two atopnucs), so these services were installable on
2 hosts of 7 — and the OSM role could not move to the box with the RAM to run
it, which is what surfaced this.

cron rather than systemd: `loginctl show-user … Linger` is `no` on these hosts,
so a --user timer dies at logout, and system units need sudo per install.
"""
import os
import subprocess
import textwrap
from pathlib import Path

HELPER = Path(__file__).resolve().parents[1] / "scripts" / "lib" / "_helpers" / "_timer.sh"


def _run(script: str, tmp_path: Path) -> subprocess.CompletedProcess:
    """Run a bash snippet against the helper with `crontab` stubbed out.

    The stub keeps the 'crontab' in a file so install/uninstall round-trip for
    real, without touching the machine's actual crontab.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    store = tmp_path / "crontab.txt"
    (bin_dir / "crontab").write_text(textwrap.dedent(f"""\
        #!/bin/bash
        STORE="{store}"
        if [ "${{1:-}}" = "-l" ]; then cat "$STORE" 2>/dev/null; exit 0; fi
        if [ "${{1:-}}" = "-r" ]; then rm -f "$STORE"; exit 0; fi
        cat "$1" > "$STORE"
        """))
    (bin_dir / "crontab").chmod(0o755)
    env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}")
    return subprocess.run(["bash", "-c", f". {HELPER}\n{script}"],
                          capture_output=True, text=True, env=env)


def test_cron_spec_from_a_time():
    r = _run('fw_timer_cron_spec "03:15" ""', Path("/tmp"))
    assert r.stdout.strip() == "15 3 * * *"


def test_cron_spec_does_not_parse_a_leading_zero_as_octal():
    """⚠️ `08` and `09` are invalid octal; without 10# this errors out. The same
    class of bug the launchd path already guards with $((10#$HH))."""
    r = _run('fw_timer_cron_spec "08:09" ""', Path("/tmp"))
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "9 8 * * *"


def test_cron_spec_from_an_interval():
    r = _run('fw_timer_cron_spec "" 12', Path("/tmp"))
    assert r.stdout.strip() == "0 */12 * * *"


def test_install_then_uninstall_round_trips(tmp_path):
    r = _run(
        'fw_timer_cron_install demo /tmp/w.sh "15 3 * * *" /tmp/l.log\n'
        'echo "AFTER_INSTALL:$(fw_timer_cron_line demo)"\n'
        'fw_timer_cron_uninstall demo\n'
        'echo "AFTER_UNINSTALL:[$(fw_timer_cron_line demo)]"\n', tmp_path)
    assert "AFTER_INSTALL:15 3 * * * /tmp/w.sh" in r.stdout, r.stdout
    assert "AFTER_UNINSTALL:[]" in r.stdout, r.stdout


def test_reinstall_does_not_duplicate_the_line(tmp_path):
    """Installing twice must replace, not append — otherwise the job runs twice
    per tick and each re-install makes it worse."""
    r = _run(
        'fw_timer_cron_install demo /tmp/w.sh "15 3 * * *" /tmp/l.log\n'
        'fw_timer_cron_install demo /tmp/w.sh "30 4 * * *" /tmp/l.log\n'
        'fw_timer_cron_line demo | wc -l\n', tmp_path)
    assert r.stdout.strip().endswith("1"), r.stdout


def test_uninstall_leaves_other_entries_alone(tmp_path):
    """⚠️ This edits the user's real crontab. Removing our label must never
    rewrite an unrelated entry."""
    r = _run(
        'printf "0 5 * * * /usr/local/bin/backup.sh\\n" > /tmp/seed.$$ && crontab /tmp/seed.$$\n'
        'fw_timer_cron_install demo /tmp/w.sh "15 3 * * *" /tmp/l.log\n'
        'fw_timer_cron_uninstall demo\n'
        'crontab -l\n', tmp_path)
    assert "backup.sh" in r.stdout, r.stdout
    assert "fw-timer:demo" not in r.stdout, r.stdout

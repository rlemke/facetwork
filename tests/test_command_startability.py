"""`fw install check --commands` — which fw commands cannot start on this host.

The defect being regressed: `fw` execs a `#!/usr/bin/env python3` command under
the SYSTEM interpreter, never the repo venv. On three of four fleet hosts that
python has no pymongo, so `fw maint unsatisfiable` exited 2 ("could not verify")
on every host but the one it was written on — for its entire life, without ever
looking broken, because exit 2 correctly does not alarm.
"""
import importlib.util
import os
import stat
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
MOD = REPO / "scripts" / "lib" / "_helpers" / "_startability.py"


def _load():
    spec = importlib.util.spec_from_file_location("_startability", MOD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


sa = pytest.importorskip("importlib") and _load()


def _cmd(d: Path, name: str, body: str) -> Path:
    p = d / name
    p.write_text(body)
    p.chmod(p.stat().st_mode | stat.S_IXUSR)
    return p


# --- interpreter resolution ------------------------------------------------

def test_bare_python_command_resolves_to_system_interpreter(tmp_path):
    """The whole bug in one assertion: nothing points a bare .py at the venv."""
    p = _cmd(tmp_path, "unsat", "#!/usr/bin/env python3\nimport pymongo\n")
    lang, key, mods = sa.classify(p)
    assert (lang, key) == ("python", "system")
    assert "pymongo" in mods


def test_self_reexec_into_venv_is_recognised(tmp_path):
    """The fix (2c48d10c) must not still read as broken."""
    p = _cmd(tmp_path, "unsat", "#!/usr/bin/env python3\n"
                                "import os\n"
                                "os.execv('/x/.venv/bin/python3', [])\n"
                                "import pymongo\n")
    _, key, _ = sa.classify(p)
    assert key == "venv"


def test_shell_command_that_picks_the_venv_is_not_system(tmp_path):
    p = _cmd(tmp_path, "audit", '#!/usr/bin/env bash\n'
                                'PYTHON="$FW_ROOT/.venv/bin/python3"\n'
                                '$PYTHON -c "import pymongo"\n')
    lang, key, mods = sa.classify(p)
    assert (lang, key) == ("shell", "venv")
    assert mods == {"pymongo"}


def test_shell_command_with_no_python_needs_no_interpreter(tmp_path):
    p = _cmd(tmp_path, "stop", "#!/usr/bin/env bash\ndocker stop x\n")
    assert sa.classify(p)[1] == "none"


def test_shell_comment_is_not_read_as_an_import(tmp_path):
    """A noisy check gets ignored; prose must not manufacture a dependency."""
    p = _cmd(tmp_path, "c", "#!/usr/bin/env bash\n# import the catalog first\n")
    assert sa.classify(p)[2] == set()


# --- import extraction -----------------------------------------------------

def test_import_guarded_by_except_ImportError_is_optional(tmp_path):
    p = _cmd(tmp_path, "c", "#!/usr/bin/env python3\n"
                            "try:\n    import boto3\nexcept ImportError:\n    boto3 = None\n")
    assert "boto3" not in sa.classify(p)[2]


def test_relative_import_is_not_a_dependency(tmp_path):
    p = _cmd(tmp_path, "c", "#!/usr/bin/env python3\nfrom . import sibling\n")
    assert sa.classify(p)[2] == set()


# --- local modules ---------------------------------------------------------

def test_modules_we_ship_are_not_missing_dependencies():
    """`from fwroot import fw_root` resolves via the bootstrap's sys.path
    insert. Deciding this by 'does the file touch sys.path?' was too crude —
    unsatisfiable does BOTH, so that rule excused the very bug being caught."""
    local = sa.local_modules(REPO, [REPO / "scripts" / "lib" / "_helpers"])
    assert "fwroot" in local
    assert "facetwork" in local
    assert "pymongo" not in local, "a real third-party dep must stay reportable"


# --- enumeration matches fw's own dispatch ---------------------------------

def test_enumeration_skips_underscore_and_nonexecutable(tmp_path):
    g = tmp_path / "grp"
    g.mkdir()
    _cmd(g, "real", "#!/usr/bin/env bash\n")
    _cmd(g, "_helper", "#!/usr/bin/env bash\n")
    (g / "data.json").write_text("{}")
    names = {n for n, _ in sa.iter_commands(tmp_path)}
    assert names == {"grp real"}


def test_enumeration_finds_nested_subgroups(tmp_path):
    sub = tmp_path / "db" / "postgis"
    sub.mkdir(parents=True)
    _cmd(sub, "vacuum", "#!/usr/bin/env bash\n")
    assert "db postgis vacuum" in {n for n, _ in sa.iter_commands(tmp_path)}


def test_real_repo_enumeration_matches_fw(tmp_path):
    """Guard against the scan silently going empty (a check that finds nothing
    reports OK)."""
    names = [n for n, _ in sa.iter_commands(REPO / "scripts" / "lib")]
    assert len(names) > 50
    assert "maint unsatisfiable" in names
    assert not any(Path(n.split()[-1]).name.startswith("_") for n in names)


def test_a_file_that_does_not_parse_is_broken_not_ok(tmp_path):
    """A SyntaxError yields no imports, so the first version reported such a
    file as OK — caught live when a bad edit broke `fw maint unsatisfiable`
    and this check still said 'all 99 commands resolve their imports'."""
    p = _cmd(tmp_path, "c", "#!/usr/bin/env python3\ndef f() -> None: -> None:\n")
    assert sa.SYNTAX_ERROR in sa.classify(p)[2]


def test_shared_venv_helper_counts_as_the_fix(tmp_path):
    """The fix moved out of line into _helpers/venv_reexec.py; a detector that
    only looked for a literal os.execv would flag every adopter as broken."""
    p = _cmd(tmp_path, "c", "#!/usr/bin/env python3\n"
                            "from venv_reexec import ensure_venv\n"
                            "ensure_venv(__file__, 'pymongo')\n"
                            "import pymongo\n")
    assert sa.classify(p)[1] == "venv"

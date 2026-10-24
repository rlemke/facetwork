"""Script-block sandbox: known CPython escapes are refused, and secrets stay out.

The in-process restrictions are defence in depth (see script_executor's module
docstring); the scrubbed environment is what limits the damage of an escape
nobody has thought of. Both are pinned here.
"""

from __future__ import annotations

import pytest

from facetwork.runtime.script_executor import (
    ScriptExecutor,
    _script_env,
    check_script_source,
)


def _run(code: str):
    return ScriptExecutor(timeout=60).execute(code, {})


ESCAPES = {
    "subclass walk": "result['x'] = str(().__class__.__base__.__subclasses__())",
    "getattr by string": "result['x'] = str(getattr((), '__class__'))",
    "hasattr by string": "result['x'] = hasattr((), '__class__')",
    "generator frame": (
        "g = (i for i in [1])\nresult['x'] = str(g.gi_frame.f_back.f_globals)"
    ),
    "frame via getattr": (
        "g = (i for i in [1])\nresult['x'] = str(getattr(g, 'gi_frame'))"
    ),
    "str.format attribute path": "result['x'] = '{0.__class__}'.format(1)",
    "builtins name": "result['x'] = str(__builtins__)",
    "dunder import": "result['x'] = str(__import__('os'))",
    "module leak (statistics.sys)": "import statistics\nresult['x'] = statistics.sys.platform",
    "module leak via getattr": "import statistics\nresult['x'] = str(getattr(statistics, 'sys'))",
    "module leak (json.codecs)": "import json as j\nresult['x'] = str(j.codecs)",
    "injected json leak": "result['x'] = str(json.codecs)",
    "mro walk": "result['x'] = str(int.mro())",
    "star import": "from statistics import *\nresult['x'] = 1",
    "banned import": "import os\nresult['x'] = 1",
}


@pytest.mark.parametrize("code", ESCAPES.values(), ids=list(ESCAPES))
def test_known_escape_is_refused(code):
    r = _run(code)
    assert not r.success, f"escape succeeded: {r.result}"
    assert "not allowed" in (r.error or "")


def test_modules_are_read_only():
    r = _run("import math\nmath.pi = 3\nresult['x'] = 1")
    assert not r.success and "read-only" in (r.error or "")


@pytest.mark.parametrize(
    "code, want",
    [
        ("import statistics\nresult['x'] = statistics.mean([1, 2, 3])", 2),
        ("import json\nresult['x'] = json.decoder.JSONDecodeError.__name__", "JSONDecodeError"),
        ("from collections import Counter\nresult['x'] = Counter('aab')['a']", 2),
        ("v = 2.5\nresult['x'] = f'{v:.2f}'", "2.50"),
        ("result['x'] = json.dumps({'a': 1})", '{"a": 1}'),
        ("import re\nresult['x'] = re.sub('a', 'b', 'aa')", "bb"),
        ("import datetime\nresult['x'] = datetime.date(2026, 1, 2).isoformat()", "2026-01-02"),
        ("_tmp = [1, 2]\nresult['x'] = sum(_tmp)", 3),
    ],
)
def test_ordinary_scripts_still_work(code, want):
    r = _run(code)
    assert r.success, r.error
    assert r.result["x"] == want


SECRETS = {
    "FW_MONGODB_URL": "mongodb://db-host:27017",
    "FW_S3_ACCESS_KEY": "s3-key",
    "FW_S3_SECRET_KEY": "s3-secret",
    "ANTHROPIC_API_KEY": "sk-test",
    "GITHUB_TOKEN": "gh-test",
    "PATH": "/usr/bin",
    "HOME": "/home/runner",
    "LC_ALL": "C.UTF-8",
}


def test_a_plain_script_gets_no_secret():
    env = _script_env(set(), SECRETS)
    assert env == {"PATH": "/usr/bin", "HOME": "/home/runner", "LC_ALL": "C.UTF-8"}


def test_a_storage_import_earns_only_the_storage_credentials():
    env = _script_env({"fsspec"}, SECRETS)
    assert env["AWS_ACCESS_KEY_ID"] == "s3-key"
    assert env["AWS_SECRET_ACCESS_KEY"] == "s3-secret"
    for k in ("FW_MONGODB_URL", "ANTHROPIC_API_KEY", "GITHUB_TOKEN", "FW_S3_ACCESS_KEY"):
        assert k not in env


def test_an_anthropic_import_earns_only_the_api_key():
    env = _script_env({"anthropic"}, SECRETS)
    assert env["ANTHROPIC_API_KEY"] == "sk-test"
    assert not any(k.startswith("AWS_") for k in env)
    assert "FW_MONGODB_URL" not in env


def test_imports_are_read_from_the_syntax_tree():
    err, imports = check_script_source("import fsspec.core\nfrom anthropic import Anthropic\n")
    assert err is None and imports == {"fsspec", "anthropic"}


def test_the_subprocess_really_runs_with_the_scrubbed_environment(monkeypatch):
    import facetwork.runtime.script_executor as se

    monkeypatch.setenv("FW_MONGODB_URL", "mongodb://db-host:27017")
    seen = {}
    real = se.subprocess.run

    def spy(*a, **kw):
        seen.update(kw.get("env") or {})
        return real(*a, **kw)

    monkeypatch.setattr(se.subprocess, "run", spy)
    assert _run("result['x'] = 1").success
    assert seen and "FW_MONGODB_URL" not in seen

# Copyright 2025 Ralph Lemke
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""`fw install check` host-readiness reporting.

Two gaps this covers, both of the same shape — a coverage tool that reports
"nothing to see" while a real gap exists:

* **Script environments.** A declared ``environment`` is a claim-routing
  dimension: script tasks carry its manifest hash and a runner claims them only
  if it provides that hash. A host missing one never claims that work, so the
  check must report per-environment coverage against FW_ENV_ROOT.
* **Domain packages.** The registry moved into ``domains.json``; a regex over
  the old inline table in ``scripts/lib/install/domain`` silently listed ZERO
  domains afterwards.

The canonical environment pins an exact version, so freezing it needs no
network (see ``_EXACT_SPEC`` in facetwork.environments).
"""

from __future__ import annotations

import os
import shutil
import subprocess

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FW = os.path.join(REPO_ROOT, "fw")
CANONICAL_ENV = "canonical.envscript.PyHuman"

pytestmark = pytest.mark.skipif(
    not (os.path.isfile(FW) and shutil.which("bash")),
    reason="fw CLI or bash not available",
)


def _check(env_root, **extra):
    """Run `fw install check` (report-only) with FW_ENV_ROOT pointed at a tmpdir."""
    env = {**os.environ, "FW_ENV_ROOT": str(env_root), **extra}
    # Keep the sweep to the repo's own examples so a developer's ~/fw_handlers
    # checkouts can't drag loose specs (and the network) into the test.
    env["FWH_HANDLERS_ROOT"] = str(env_root / "no_domains_here")
    return subprocess.run(
        ["bash", FW, "install", "check"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env=env,
        timeout=600,
    )


def _canonical_hash():
    from facetwork.envbake import collect_environments

    envs = collect_environments([os.path.join(REPO_ROOT, "examples", "canonical")])
    assert CANONICAL_ENV in envs, f"canonical environment vanished from examples/: {sorted(envs)}"
    return envs[CANONICAL_ENV]["hash"]


def test_reports_declared_environment_as_missing(tmp_path):
    """An empty FW_ENV_ROOT must surface the declared environment as a GAP."""
    r = _check(tmp_path)
    assert r.returncode == 0, r.stderr
    assert "Script environments" in r.stdout
    assert CANONICAL_ENV in r.stdout
    assert "NOT materialized here" in r.stdout
    # and the summary must tell the operator what the consequence is
    assert "will not" in r.stdout and "claim their script tasks" in r.stdout


def test_reports_environment_as_provided_when_materialized(tmp_path):
    """A venv under FW_ENV_ROOT/<hash>/bin/python marks that environment covered.

    ⚠️ Scoped to the canonical hash on purpose. Asserting that the string
    "NOT materialized here" is absent ANYWHERE made the test depend on there
    being exactly one declared environment in the repo — adding a second (an
    astropy environment for examples/repro-asteroids) broke it, though nothing
    it covers had changed. Assert about the environment this test materialises.
    """
    h = _canonical_hash()
    interpreter = tmp_path / h / "bin" / "python"
    interpreter.parent.mkdir(parents=True)
    interpreter.write_text("#!/bin/sh\n")

    r = _check(tmp_path)
    assert r.returncode == 0, r.stderr
    assert "provided" in r.stdout
    canonical_lines = [l for l in r.stdout.splitlines() if h[:7] in l]
    assert canonical_lines, f"no line mentions the canonical env {h[:7]}"
    for line in canonical_lines:
        assert "NOT materialized here" not in line, line


def test_lists_domains_from_the_catalog(tmp_path):
    """Regression: the domain list is read from domains.json, not scraped.

    A regex over the (now removed) inline table in scripts/lib/install/domain
    matched nothing, so this section silently listed no domains at all.
    """
    from facetwork.domains import catalog

    expected = sorted(catalog.domains() or {})
    assert expected, "domain catalog is empty — domains.json missing?"

    r = _check(tmp_path)
    assert r.returncode == 0, r.stderr
    assert "Standalone domain packages" in r.stdout
    for name in expected:
        assert name in r.stdout, f"catalog domain {name} missing from `fw install check`"


def test_environment_section_survives_an_unreadable_env_root(tmp_path):
    """Coverage reporting is additive — it must never break the dependency check."""
    r = _check(tmp_path / "does" / "not" / "exist")
    assert r.returncode == 0, r.stderr
    assert "Test/example modules NOT importable here:" in r.stdout
    assert "Script environments" in r.stdout

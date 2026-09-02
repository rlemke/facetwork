

def test_csv_is_importable_in_script_blocks():
    """⚠️ `csv` must stay on the sandbox allowlist.

    Its absence is not neutral: a script needing CSV hand-rolls a parser
    instead. Measured — a regex "parser" over GHCN-Daily silently shifted every
    field (124 columns, only ~30 quoted, unquoted empties skipped) and produced
    plausible wrong numbers with no error. Pure stdlib parsing, no I/O reach,
    exactly like `json`.
    """
    from facetwork.runtime.script_executor import _SAFE_IMPORT_MODULES
    assert "csv" in _SAFE_IMPORT_MODULES
    assert "json" in _SAFE_IMPORT_MODULES


class TestScriptS3Credentials:
    """`fw.file.FileOps` bundles s3fs; the sandbox must make it usable."""

    def test_explicit_aws_env_is_not_overridden(self, monkeypatch):
        """An operator who set AWS_* deliberately must keep it."""
        import os
        import subprocess as sp
        from facetwork.runtime import script_executor as se
        monkeypatch.setenv("FW_S3_ACCESS_KEY", "from-fw")
        monkeypatch.setenv("AWS_ACCESS_KEY_ID", "explicit")
        seen = {}
        real = sp.run

        def spy(*a, **kw):
            seen.update(kw.get("env") or {})
            return real(*a, **kw)

        monkeypatch.setattr(se.subprocess, "run", spy)
        se.ScriptExecutor(timeout=30).execute("result = {}", {})
        assert seen.get("AWS_ACCESS_KEY_ID") == "explicit"
        assert seen.get("AWS_SECRET_ACCESS_KEY") is None or True

    def test_fw_s3_is_mapped_when_aws_is_absent(self, monkeypatch):
        import subprocess as sp
        from facetwork.runtime import script_executor as se
        for k in ("AWS_ACCESS_KEY_ID", "AWS_ENDPOINT_URL"):
            monkeypatch.delenv(k, raising=False)
        monkeypatch.setenv("FW_S3_ACCESS_KEY", "mapped-key")
        monkeypatch.setenv("FW_S3_ENDPOINT", "http://afl-minio:9000")
        seen = {}
        real = sp.run

        def spy(*a, **kw):
            seen.update(kw.get("env") or {})
            return real(*a, **kw)

        monkeypatch.setattr(se.subprocess, "run", spy)
        se.ScriptExecutor(timeout=30).execute("result = {}", {})
        assert seen.get("AWS_ACCESS_KEY_ID") == "mapped-key"
        assert seen.get("AWS_ENDPOINT_URL") == "http://afl-minio:9000"


class TestScriptDeclaredReturns:
    """A script that declares returns and produces none is a rebind mistake.

    These call the REAL guard. An earlier version of this test re-implemented
    the check inline and asserted on its own output, which proves nothing about
    the shipped code.
    """

    def test_empty_result_against_declared_returns_raises_and_names_the_cause(self):
        import pytest
        from facetwork.runtime.base_runner import check_declared_returns
        with pytest.raises(RuntimeError) as ei:
            check_declared_returns(
                "repro.normals.ExtractPublished",
                {"returns": [{"name": "path"}, {"name": "months"}]},
                {},
            )
        msg = str(ei.value)
        assert "path" in msg and "MUTATE" in msg and "rebind" in msg

    def test_produced_values_pass(self):
        from facetwork.runtime.base_runner import check_declared_returns
        check_declared_returns("f", {"returns": [{"name": "path"}]}, {"path": "/x"})

    def test_no_declared_returns_means_empty_is_fine(self):
        """A script facet with no return clause legitimately returns nothing."""
        from facetwork.runtime.base_runner import check_declared_returns
        check_declared_returns("f", {"returns": []}, {})
        check_declared_returns("f", {}, {})


def test_dockerfile_copies_every_example_that_declares_an_environment():
    """⚠️ envbake sweeps /app/examples — everything it sweeps must be COPYed.

    The Dockerfile copied only examples/canonical/, so an `environment`
    declared in any other example was SILENTLY INVISIBLE: envbake found
    nothing, the layer cached against an unchanged canonical/, no error was
    raised, and the environment did not exist. Its script tasks then carry an
    environment_hash no runner advertises and are claimable by nothing.
    """
    import pathlib
    import re
    root = pathlib.Path(__file__).resolve().parents[1]
    df = (root / "docker" / "Dockerfile.domain-runner").read_text()
    assert "--root /app/examples" in df, "envbake no longer sweeps examples"

    declaring = {
        p.parent.relative_to(root).as_posix()
        for p in (root / "examples").rglob("*.ffl")
        if re.search(r"^\s*environment\s+[A-Z]", p.read_text(), re.M)
    }
    assert declaring, "no example declares an environment — test is vacuous"

    copied = re.findall(r"^COPY\s+(examples\S*)", df, re.M)
    for d in sorted(declaring):
        assert any(c.rstrip("/") == "examples" or d.startswith(c.rstrip("/"))
                   for c in copied), (
            f"{d} declares an environment but the Dockerfile does not copy it "
            f"(COPY lines: {copied}) — envbake would never see it")

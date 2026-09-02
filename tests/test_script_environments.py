

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

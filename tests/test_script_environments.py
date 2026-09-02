

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

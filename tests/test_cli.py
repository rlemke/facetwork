"""Tests for FFL command-line interface."""

import json
from io import StringIO
from unittest.mock import patch

from facetwork.cli import main


class TestBasicParsing:
    """Test basic file parsing via CLI."""

    def test_parse_file(self, tmp_path):
        afl_file = tmp_path / "test.ffl"
        afl_file.write_text("facet Test()")
        output = tmp_path / "out.json"

        result = main([str(afl_file), "-o", str(output)])

        assert result == 0
        data = json.loads(output.read_text())
        assert data["type"] == "Program"
        assert len([d for d in data.get("declarations", []) if d.get("type") == "FacetDecl"]) == 1

    def test_parse_file_to_stdout(self, tmp_path, capsys):
        afl_file = tmp_path / "test.ffl"
        afl_file.write_text("facet Hello()")

        result = main([str(afl_file)])

        assert result == 0
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["type"] == "Program"

    def test_check_mode(self, tmp_path, capsys):
        afl_file = tmp_path / "test.ffl"
        afl_file.write_text("facet Valid()")

        result = main([str(afl_file), "--check"])

        assert result == 0
        captured = capsys.readouterr()
        assert "OK" in captured.err

    def test_compact_output(self, tmp_path, capsys):
        afl_file = tmp_path / "test.ffl"
        afl_file.write_text("facet Test()")

        result = main([str(afl_file), "--compact"])

        assert result == 0
        captured = capsys.readouterr()
        assert "\n" not in captured.out.strip()

    def test_no_locations(self, tmp_path, capsys):
        afl_file = tmp_path / "test.ffl"
        afl_file.write_text("facet Test()")

        result = main([str(afl_file), "--no-locations"])

        assert result == 0
        data = json.loads(capsys.readouterr().out)
        facets = [d for d in data.get("declarations", []) if d.get("type") == "FacetDecl"]
        assert "location" not in facets[0] if facets else True

    def test_no_validate(self, tmp_path, capsys):
        afl_file = tmp_path / "test.ffl"
        afl_file.write_text("facet Dup()\nfacet Dup()")

        result = main([str(afl_file), "--no-validate"])

        # Should succeed even with duplicate names
        assert result == 0


class TestMultiSource:
    """Test multi-source input options."""

    def test_primary_files(self, tmp_path, capsys):
        f1 = tmp_path / "a.ffl"
        f2 = tmp_path / "b.ffl"
        f1.write_text("facet A()")
        f2.write_text("facet B()")

        result = main(["--primary", str(f1), "--primary", str(f2)])

        assert result == 0

    def test_library_files(self, tmp_path, capsys):
        primary = tmp_path / "main.ffl"
        lib = tmp_path / "lib.ffl"
        primary.write_text("facet Main()")
        lib.write_text("facet Lib()")

        result = main(["--primary", str(primary), "--library", str(lib)])

        assert result == 0

    def test_conflict_positional_and_multi(self, tmp_path, capsys):
        f = tmp_path / "test.ffl"
        f.write_text("facet Test()")

        result = main([str(f), "--primary", str(f)])

        assert result == 1
        captured = capsys.readouterr()
        assert "Cannot use positional input" in captured.err


class TestErrorHandling:
    """Test error handling in CLI."""

    def test_file_not_found(self, capsys):
        result = main(["/nonexistent/file.ffl"])

        assert result == 1
        captured = capsys.readouterr()
        assert "File not found" in captured.err

    def test_primary_file_not_found(self, capsys):
        result = main(["--primary", "/nonexistent.ffl"])

        assert result == 1
        captured = capsys.readouterr()
        assert "File not found" in captured.err

    def test_library_file_not_found(self, tmp_path, capsys):
        primary = tmp_path / "main.ffl"
        primary.write_text("facet Main()")

        result = main(["--primary", str(primary), "--library", "/no.ffl"])

        assert result == 1
        captured = capsys.readouterr()
        assert "File not found" in captured.err

    def test_parse_error(self, tmp_path, capsys):
        afl_file = tmp_path / "bad.ffl"
        afl_file.write_text("@@@ invalid syntax")

        result = main([str(afl_file)])

        assert result == 1
        captured = capsys.readouterr()
        assert "Error" in captured.err

    def test_validation_error(self, tmp_path, capsys):
        afl_file = tmp_path / "dup.ffl"
        afl_file.write_text("facet Dup()\nfacet Dup()")

        result = main([str(afl_file)])

        assert result == 1
        captured = capsys.readouterr()
        assert "Duplicate" in captured.err

    def test_output_io_error(self, tmp_path, capsys):
        afl_file = tmp_path / "test.ffl"
        afl_file.write_text("facet Test()")

        result = main([str(afl_file), "-o", "/nonexistent/dir/out.json"])

        assert result == 1
        captured = capsys.readouterr()
        assert "Error writing output" in captured.err


class TestStdinParsing:
    """Test stdin parsing."""

    def test_stdin_input(self, capsys):
        with patch("sys.stdin", StringIO("facet FromStdin()")):
            result = main([])

        assert result == 0
        data = json.loads(capsys.readouterr().out)
        facets = [d for d in data.get("declarations", []) if d.get("type") == "FacetDecl"]
        assert facets[0]["name"] == "FromStdin"


class TestMongoMavenSpecs:
    """Test MongoDB and Maven source spec validation."""

    def test_invalid_mongo_spec(self, capsys):
        result = main(["--mongo", "no-colon"])

        assert result == 1
        captured = capsys.readouterr()
        assert "Invalid MongoDB spec" in captured.err

    def test_valid_mongo_spec_not_implemented(self, capsys):
        result = main(["--mongo", "id123:MySource"])

        assert result == 1
        captured = capsys.readouterr()
        # Should error because MongoDB loading is not yet implemented
        assert "Error" in captured.err

    def test_invalid_maven_spec(self, capsys):
        result = main(["--maven", "only-one-part"])

        assert result == 1
        captured = capsys.readouterr()
        assert "Invalid Maven spec" in captured.err

    def test_valid_maven_spec_not_implemented(self, capsys):
        result = main(["--maven", "com.example:artifact:1.0"])

        assert result == 1
        captured = capsys.readouterr()
        assert "Error" in captured.err

    def test_maven_with_classifier(self, capsys):
        result = main(["--maven", "com.example:artifact:1.0:tests"])

        assert result == 1
        captured = capsys.readouterr()
        assert "Error" in captured.err


class TestLogging:
    """Test logging configuration."""

    def test_log_level_option(self, tmp_path, capsys):
        afl_file = tmp_path / "test.ffl"
        afl_file.write_text("facet Test()")

        result = main([str(afl_file), "--log-level", "DEBUG"])
        assert result == 0

    def test_log_file_option(self, tmp_path, capsys):
        afl_file = tmp_path / "test.ffl"
        afl_file.write_text("facet Test()")
        log_file = tmp_path / "facetwork.log"

        result = main([str(afl_file), "--log-level", "DEBUG", "--log-file", str(log_file)])
        assert result == 0


class TestIncludeProvenance:
    """Test provenance inclusion."""

    def test_include_provenance(self, tmp_path, capsys):
        afl_file = tmp_path / "test.ffl"
        afl_file.write_text("facet Test()")

        result = main([str(afl_file), "--include-provenance"])
        assert result == 0
        data = json.loads(capsys.readouterr().out)
        # With provenance enabled, locations should include provenance info
        facet = [d for d in data.get("declarations", []) if d.get("type") == "FacetDecl"][0]
        assert "location" in facet
        loc = facet["location"]
        assert "provenance" in loc
        assert loc["provenance"]["type"] == "file"


class TestCheckModeCount:
    """Test check mode reports source count."""

    def test_check_reports_count(self, tmp_path, capsys):
        f1 = tmp_path / "a.ffl"
        f2 = tmp_path / "b.ffl"
        f1.write_text("facet A()")
        f2.write_text("facet B()")

        result = main(["--primary", str(f1), "--primary", str(f2), "--check"])

        assert result == 0
        captured = capsys.readouterr()
        assert "2 source(s)" in captured.err

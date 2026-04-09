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

"""AFL command-line interface."""

import argparse
import sys
from pathlib import Path

from .config import load_config
from .emitter import JSONEmitter
from .loader import SourceLoader
from .parser import FFLParser, ParseError
from .source import (
    CompilerInput,
    FileOrigin,
    SourceEntry,
)
from .validator import validate

# Known subcommands for routing
_SUBCOMMANDS = {"compile", "publish"}


def _build_compile_parser(parser: argparse.ArgumentParser) -> None:
    """Add compile-specific arguments to *parser*."""

    # Legacy single-file input (backward compatible)
    parser.add_argument(
        "input",
        nargs="?",
        help="Input FFL file (reads from stdin if not provided). "
        "Use --primary for multi-file input.",
    )

    # Multi-source input options
    parser.add_argument(
        "--primary",
        action="append",
        dest="primary_files",
        metavar="FILE",
        help="Primary FFL source file (repeatable)",
    )

    parser.add_argument(
        "--library",
        action="append",
        dest="library_files",
        metavar="FILE",
        help="Library/dependency source file (repeatable)",
    )

    parser.add_argument(
        "--mongo",
        action="append",
        dest="mongo_sources",
        metavar="ID:NAME",
        help="MongoDB source as ID:display_name (repeatable, not yet implemented)",
    )

    parser.add_argument(
        "--maven",
        action="append",
        dest="maven_sources",
        metavar="G:A:V",
        help="Maven artifact as group:artifact:version (repeatable, not yet implemented)",
    )

    # Output options
    parser.add_argument(
        "-o",
        "--output",
        help="Output file (writes to stdout if not provided)",
    )

    parser.add_argument(
        "--no-locations",
        action="store_true",
        help="Exclude source locations from output",
    )

    parser.add_argument(
        "--include-provenance",
        action="store_true",
        help="Include source provenance in locations",
    )

    parser.add_argument(
        "--compact",
        action="store_true",
        help="Output compact JSON (no indentation)",
    )

    parser.add_argument(
        "--check",
        action="store_true",
        help="Check syntax only, don't emit JSON",
    )

    parser.add_argument(
        "--no-validate",
        action="store_true",
        help="Skip semantic validation",
    )

    # Auto-resolve options
    parser.add_argument(
        "--auto-resolve",
        action="store_true",
        help="Automatically resolve missing namespace dependencies",
    )

    parser.add_argument(
        "--source-path",
        action="append",
        dest="source_paths",
        metavar="PATH",
        help="Additional directory to scan for FFL sources (repeatable)",
    )

    parser.add_argument(
        "--mongo-resolve",
        action="store_true",
        help="Enable MongoDB namespace lookup during auto-resolution",
    )


def _build_publish_parser(parser: argparse.ArgumentParser) -> None:
    """Add publish-specific arguments to *parser*."""

    parser.add_argument(
        "input",
        nargs="?",
        help="AFL source file to publish",
    )

    parser.add_argument(
        "--primary",
        action="append",
        dest="primary_files",
        metavar="FILE",
        help="Primary FFL source file (repeatable)",
    )

    parser.add_argument(
        "--library",
        action="append",
        dest="library_files",
        metavar="FILE",
        help="Library FFL source file (repeatable)",
    )

    parser.add_argument(
        "--version",
        default="latest",
        help="Version tag for published sources (default: latest)",
    )

    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing published sources",
    )

    parser.add_argument(
        "--list",
        action="store_true",
        dest="list_sources",
        help="List all published sources",
    )

    parser.add_argument(
        "--unpublish",
        metavar="NAMESPACE",
        help="Remove a published namespace",
    )


def _add_common_args(parser: argparse.ArgumentParser) -> None:
    """Add arguments shared by all subcommands."""
    parser.add_argument(
        "--config",
        metavar="FILE",
        help="Path to FFL config file (JSON). "
        "Defaults to afl.config.json in cwd, ~/.afl/, or /etc/ffl/",
    )

    parser.add_argument(
        "--log-level",
        default="WARNING",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Log level (default: WARNING)",
    )

    parser.add_argument(
        "--log-file",
        default=None,
        metavar="FILE",
        help="Log to file instead of stderr",
    )

    parser.add_argument(
        "--log-format",
        default="json",
        choices=["json", "text"],
        help="Log format (default: json)",
    )


def _configure_logging(parsed: argparse.Namespace) -> None:
    """Set up logging from parsed CLI args."""
    from .logging import configure_logging

    configure_logging(
        level=parsed.log_level,
        log_file=parsed.log_file,
        log_format=parsed.log_format,
    )


# =========================================================================
# Compile handler
# =========================================================================


def _handle_compile(parsed: argparse.Namespace) -> int:
    """Execute the compile subcommand."""
    config = load_config(parsed.config)

    # Build compiler input
    compiler_input = CompilerInput()

    # Check for conflicting input modes
    has_multi_source = (
        parsed.primary_files or parsed.library_files or parsed.mongo_sources or parsed.maven_sources
    )

    if parsed.input and has_multi_source:
        print(
            "Error: Cannot use positional input with --primary/--library/--mongo/--maven. "
            "Use --primary for the main source file.",
            file=sys.stderr,
        )
        return 1

    # Handle legacy single-file input
    if parsed.input:
        try:
            entry = SourceLoader.load_file(parsed.input, is_library=False)
            compiler_input.primary_sources.append(entry)
        except FileNotFoundError:
            print(f"Error: File not found: {parsed.input}", file=sys.stderr)
            return 1
        except OSError as e:
            print(f"Error reading input: {e}", file=sys.stderr)
            return 1

    # Handle stdin if no files specified
    elif not has_multi_source:
        source = sys.stdin.read()
        entry = SourceEntry(
            text=source,
            origin=FileOrigin(path="<stdin>"),
            is_library=False,
        )
        compiler_input.primary_sources.append(entry)

    # Handle --primary files
    for file_path in parsed.primary_files or []:
        try:
            entry = SourceLoader.load_file(file_path, is_library=False)
            compiler_input.primary_sources.append(entry)
        except FileNotFoundError:
            print(f"Error: File not found: {file_path}", file=sys.stderr)
            return 1
        except OSError as e:
            print(f"Error reading {file_path}: {e}", file=sys.stderr)
            return 1

    # Handle --library files
    for file_path in parsed.library_files or []:
        try:
            entry = SourceLoader.load_file(file_path, is_library=True)
            compiler_input.library_sources.append(entry)
        except FileNotFoundError:
            print(f"Error: File not found: {file_path}", file=sys.stderr)
            return 1
        except OSError as e:
            print(f"Error reading {file_path}: {e}", file=sys.stderr)
            return 1

    # Handle --mongo sources (stub)
    for mongo_spec in parsed.mongo_sources or []:
        try:
            if ":" not in mongo_spec:
                print(
                    f"Error: Invalid MongoDB spec '{mongo_spec}'. Expected format: ID:display_name",
                    file=sys.stderr,
                )
                return 1
            doc_id, display_name = mongo_spec.split(":", 1)
            entry = SourceLoader.load_mongodb(doc_id, display_name, is_library=True)
            compiler_input.library_sources.append(entry)
        except (NotImplementedError, ValueError, Exception) as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1

    # Handle --maven sources
    for maven_spec in parsed.maven_sources or []:
        try:
            parts = maven_spec.split(":")
            if len(parts) < 3:
                print(
                    f"Error: Invalid Maven spec '{maven_spec}'. "
                    "Expected format: group:artifact:version[:classifier]",
                    file=sys.stderr,
                )
                return 1
            group_id, artifact_id, version = parts[0], parts[1], parts[2]
            classifier = parts[3] if len(parts) > 3 else ""
            entry = SourceLoader.load_maven(
                group_id, artifact_id, version, classifier, is_library=True
            )
            compiler_input.library_sources.append(entry)
        except (NotImplementedError, ValueError, Exception) as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1

    # Parse (with optional auto-resolve)
    afl_parser = FFLParser()
    use_auto_resolve = parsed.auto_resolve or config.resolver.auto_resolve

    try:
        if use_auto_resolve:
            # Merge CLI flags into config
            if parsed.source_paths:
                config.resolver.source_paths.extend(parsed.source_paths)
            if parsed.mongo_resolve:
                config.resolver.mongodb_resolve = True
            config.resolver.auto_resolve = True

            ast, source_registry = afl_parser.parse_and_resolve(compiler_input, config)
        else:
            ast, source_registry = afl_parser.parse_sources(compiler_input)
    except ParseError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    # Validate (unless skipped)
    if not parsed.no_validate:
        result = validate(ast)
        if not result.is_valid:
            for error in result.errors:
                print(f"Error: {error}", file=sys.stderr)
            return 1

    # Check only mode
    if parsed.check:
        source_count = len(compiler_input.all_sources)
        print(f"OK: {source_count} source(s) parsed successfully", file=sys.stderr)
        return 0

    # Emit JSON
    indent = None if parsed.compact else 2
    include_locations = not parsed.no_locations
    include_provenance = parsed.include_provenance

    emitter = JSONEmitter(
        include_locations=include_locations,
        include_provenance=include_provenance,
        source_registry=source_registry if include_provenance else None,
        indent=indent,
    )
    output = emitter.emit(ast)

    # Write output
    try:
        if parsed.output:
            Path(parsed.output).write_text(output + "\n")
        else:
            print(output)
    except OSError as e:
        print(f"Error writing output: {e}", file=sys.stderr)
        return 1

    return 0


# =========================================================================
# Publish handler
# =========================================================================


def _handle_publish(parsed: argparse.Namespace) -> int:
    """Execute the publish subcommand."""
    from .publisher import PublishError, SourcePublisher
    from .runtime.mongo_store import MongoStore

    config = load_config(parsed.config)

    # Handle --list
    if parsed.list_sources:
        try:
            store = MongoStore.from_config(config.mongodb)
            publisher = SourcePublisher(store)
            sources = publisher.list_published()
            if not sources:
                print("No published sources found.", file=sys.stderr)
            else:
                for src in sources:
                    print(
                        f"  {src.namespace_name}  version={src.version}  "
                        f"checksum={src.checksum[:12]}...  origin={src.origin}"
                    )
            store.close()
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1
        return 0

    # Handle --unpublish
    if parsed.unpublish:
        try:
            store = MongoStore.from_config(config.mongodb)
            publisher = SourcePublisher(store)
            deleted = publisher.unpublish(parsed.unpublish, parsed.version)
            if deleted:
                print(
                    f"Unpublished '{parsed.unpublish}' version={parsed.version}",
                    file=sys.stderr,
                )
            else:
                print(
                    f"Not found: '{parsed.unpublish}' version={parsed.version}",
                    file=sys.stderr,
                )
            store.close()
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1
        return 0

    # Collect source files to publish
    files_to_publish: list[Path] = []

    if parsed.input:
        files_to_publish.append(Path(parsed.input))

    for fp in parsed.primary_files or []:
        files_to_publish.append(Path(fp))

    for fp in parsed.library_files or []:
        files_to_publish.append(Path(fp))

    if not files_to_publish:
        print("Error: No source files specified for publishing.", file=sys.stderr)
        return 1

    try:
        store = MongoStore.from_config(config.mongodb)
        publisher = SourcePublisher(store)

        total_published = 0
        for file_path in files_to_publish:
            if not file_path.exists():
                print(f"Error: File not found: {file_path}", file=sys.stderr)
                store.close()
                return 1

            source_text = file_path.read_text()
            published = publisher.publish(
                source_text,
                version=parsed.version,
                origin=f"cli:{file_path}",
                force=parsed.force,
            )
            for ps in published:
                print(
                    f"Published '{ps.namespace_name}' version={ps.version}",
                    file=sys.stderr,
                )
            total_published += len(published)

        print(f"OK: {total_published} namespace(s) published", file=sys.stderr)
        store.close()
    except PublishError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    return 0


# =========================================================================
# Main entry point
# =========================================================================


def main(args: list[str] | None = None) -> int:
    """Main entry point for FFL compiler CLI.

    Supports subcommands ``compile`` (default) and ``publish``.
    For backward compatibility, if the first argument is not a known
    subcommand, ``compile`` is assumed.

    Args:
        args: Command-line arguments (defaults to sys.argv[1:])

    Returns:
        Exit code (0 for success, non-zero for errors)
    """
    argv = args if args is not None else sys.argv[1:]

    # Determine subcommand: if first arg is a known subcommand, use it;
    # otherwise assume "compile" for backward compatibility.
    subcommand = "compile"
    remaining = list(argv)
    if remaining and remaining[0] in _SUBCOMMANDS:
        subcommand = remaining[0]
        remaining = remaining[1:]

    if subcommand == "compile":
        parser = argparse.ArgumentParser(
            prog="afl compile",
            description="AFL (Facetwork Flow Language) compiler",
        )
        _build_compile_parser(parser)
        _add_common_args(parser)
        parsed = parser.parse_args(remaining)
        _configure_logging(parsed)
        return _handle_compile(parsed)

    elif subcommand == "publish":
        parser = argparse.ArgumentParser(
            prog="afl publish",
            description="Publish FFL sources to MongoDB for namespace sharing",
        )
        _build_publish_parser(parser)
        _add_common_args(parser)
        parsed = parser.parse_args(remaining)
        _configure_logging(parsed)
        return _handle_publish(parsed)

    # Should not reach here
    print(f"Unknown subcommand: {subcommand}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())

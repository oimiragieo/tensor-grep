from typing import Any

from mcp.server.fastmcp import FastMCP

from tensor_grep.core.config import SearchConfig
from tensor_grep.core.pipeline import Pipeline
from tensor_grep.core.result import SearchResult
from tensor_grep.io.directory_scanner import DirectoryScanner

# Initialize the FastMCP server
mcp = FastMCP("tensor-grep")


@mcp.tool()
def tg_search(
    pattern: str,
    path: str = ".",
    case_sensitive: bool = False,
    ignore_case: bool = False,
    fixed_strings: bool = False,
    word_regexp: bool = False,
    context: int | None = None,
    max_count: int | None = None,
    count_matches: bool = False,
    glob: str | None = None,
    type_filter: str | None = None,
) -> str:
    """
    Search files for a regex pattern using tensor-grep's high-speed GPU or CPU engine.

    Args:
        pattern: A regular expression or exact string used for searching.
        path: A file or directory to search. Defaults to current directory.
        case_sensitive: Execute the search case sensitively.
        ignore_case: Search case insensitively (-i).
        fixed_strings: Treat pattern as a literal string instead of regex (-F).
        word_regexp: Only show matches surrounded by word boundaries (-w).
        context: Show NUM lines before and after each match (-C).
        max_count: Limit the number of matching lines per file (-m).
        count_matches: Just count the matches using ultra-fast Rust backend (-c).
        glob: Include/exclude files matching glob (e.g. '*.py').
        type_filter: Only search files matching TYPE (e.g. 'py', 'js').
    """
    config = SearchConfig(
        case_sensitive=case_sensitive,
        ignore_case=ignore_case,
        fixed_strings=fixed_strings,
        word_regexp=word_regexp,
        context=context,
        max_count=max_count,
        count=count_matches,
        glob=[glob] if glob else None,
        file_type=[type_filter] if type_filter else None,
        no_messages=True,
    )

    pipeline = Pipeline(config=config)
    backend = pipeline.get_backend()
    scanner = DirectoryScanner(config)

    all_results = SearchResult(matches=[], total_files=0, total_matches=0)

    try:
        for current_file in scanner.walk(path):
            result = backend.search(current_file, pattern, config=config)
            all_results.matches.extend(result.matches)
            all_results.total_matches += result.total_matches
            if result.total_matches > 0:
                all_results.total_files += 1

        if all_results.is_empty:
            return f"No matches found for '{pattern}' in {path}."

        if count_matches:
            return f"Found a total of {all_results.total_matches} matches across {all_results.total_files} files in {path}."

        # Format the results into a readable string for the LLM
        output = [
            f"Found {all_results.total_matches} matches across {all_results.total_files} files:"
        ]

        # Group by file
        by_file: dict[str, list[Any]] = {}
        for match in all_results.matches:
            if match.file not in by_file:
                by_file[match.file] = []
            by_file[match.file].append(match)

        for filepath, matches in list(by_file.items())[
            :15
        ]:  # Limit to first 15 files to prevent context explosion
            output.append(f"\n{filepath}:")
            for m in matches[:10]:  # Limit to 10 matches per file
                output.append(f"  {m.line_number}: {m.text.strip()}")

        if len(by_file) > 15:
            output.append(f"\n... and {len(by_file) - 15} more files.")

        return "\n".join(output)

    except Exception as e:
        return f"Search failed: {e!s}"


@mcp.tool()
def tg_ast_search(pattern: str, lang: str, path: str = ".") -> str:
    """
    Search source code structurally using PyTorch Geometric Graph Neural Networks.
    Ignores whitespace and formatting, searching the true AST structure.

    Args:
        pattern: AST pattern to search for (e.g. 'if ($A) { return $B; }').
        lang: Language to parse (e.g. 'python', 'javascript').
        path: Directory or file to search.
    """
    config = SearchConfig(ast=True, lang=lang, no_messages=True)
    pipeline = Pipeline(config=config)
    backend = pipeline.get_backend()

    if type(backend).__name__ != "AstBackend":
        return "Error: AstBackend is not available on this system. Requires torch_geometric and tree_sitter."

    scanner = DirectoryScanner(config)
    all_results = SearchResult(matches=[], total_files=0, total_matches=0)

    try:
        for current_file in scanner.walk(path):
            result = backend.search(current_file, pattern, config=config)
            all_results.matches.extend(result.matches)
            all_results.total_matches += result.total_matches
            if result.total_matches > 0:
                all_results.total_files += 1

        if all_results.is_empty:
            return f"No AST matches found for pattern in {path}."

        output = [
            f"Found {all_results.total_matches} structural AST matches across {all_results.total_files} files:"
        ]

        # Group by file
        by_file: dict[str, list[Any]] = {}
        for match in all_results.matches:
            if match.file not in by_file:
                by_file[match.file] = []
            by_file[match.file].append(match)

        for filepath, matches in list(by_file.items())[:15]:
            output.append(f"\n{filepath}:")
            for m in matches[:10]:
                output.append(f"  {m.line_number}: {m.text.strip()}")

        return "\n".join(output)

    except Exception as e:
        return f"AST Search failed: {e!s}"


@mcp.tool()
def tg_classify_logs(file_path: str) -> str:
    """
    Analyze a system log file using the CyBERT NLP model to automatically
    detect warnings, errors, and malicious payloads contextually.

    Args:
        file_path: The absolute path to the log file to classify.
    """
    try:
        from tensor_grep.backends.cybert_backend import CybertBackend
        from tensor_grep.io.reader_fallback import FallbackReader

        reader = FallbackReader()
        lines = list(reader.read_lines(file_path))
        if not lines:
            return f"Error: File {file_path} is empty or unreadable."

        backend = CybertBackend()
        results = backend.classify(lines)

        output = [f"Semantic Classification for {file_path} (Sample of {len(lines)} lines):"]

        warnings_or_errors = []
        for i, r in enumerate(results):
            if r["label"] in ("warn", "error") and r["confidence"] > 0.8:
                warnings_or_errors.append((lines[i].strip(), r["label"], r["confidence"]))

        if not warnings_or_errors:
            return f"No severe anomalies detected in {file_path}. All logs appear nominal."

        output.append(f"\nDetected {len(warnings_or_errors)} High-Confidence Anomalies:")
        for text, label, conf in warnings_or_errors[:20]:  # Limit output
            output.append(f"[{label.upper()}] ({conf:.2f}) {text}")

        return "\n".join(output)

    except Exception as e:
        return f"Log Classification failed: {e!s}"


def run_mcp_server() -> None:
    """Entry point for the MCP server."""
    mcp.run()

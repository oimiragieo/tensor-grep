from __future__ import annotations

import dataclasses
import os
import sys
from pathlib import Path

import typer

from tensor_grep.cli import _index_lock as _index_lock
from tensor_grep.cli import citations as _citations
from tensor_grep.cli import search_follow_options as _search_follow_options
from tensor_grep.cli._main_binding import _self

# perf (+10% campaign #6 / F2.4): import the 5 broad-scan-guard constants from the
# zero-dependency `tensor_grep.io.scan_limits` module directly, NOT from
# `tensor_grep.io.directory_scanner` (which does `from tensor_grep.core.config import
# SearchConfig` at its own module level -- SearchConfig transitively pulls in the stdlib
# `dataclasses`/`inspect` chain). This module-level import runs for every full-CLI command
# (--help, scan, test, ast-info, ...); these 5 names are plain frozensets/ints consumed only by
# the module-level broad-scan literals below, never SearchConfig itself, so there is no reason
# to pay for the heavier module merely to read 5 constants from it. `DirectoryScanner` itself is
# still imported lazily, function-local, at each call site that actually walks a tree (unchanged
# by this PR). See `tensor_grep.io.scan_limits`'s module docstring for the full rationale.
# Read this module object through late-bound
# attribute reads. A BARE call to a monkeypatched name resolves through THIS module's
# globals, welding the caller to this file -- move it and the test still passes while
# production runs the unpatched original. `_self.NAME(...)` resolves at CALL time.
# The two branches are load-bearing. At runtime only `sys.modules[__name__]` works (the
# module is mid-import, so importing itself by name would be circular), but that is typed
# `ModuleType`, whose `__getattr__` returns `Any` -- under a single-branch form every
# converted call returns Any and mypy raises `no-any-return` at each concrete-returning
# caller. The TYPE_CHECKING branch never executes; it exists so the checker resolves
# `_self` to this module and keeps every real signature.
# scripts/bare_call_ratchet.py pins the remaining bare-call count and fails if it grows.
# Re-exports from the modules split out of this file on 2026-08-20 (see
# cli/_main_binding.py). EVERY moved name is
# rebound here, not only the ones a scan can prove are read. The test suite patches many of
# them on `main` and the moved code reads them back through `_self`, so the patch target must
# not move with the code -- and tests reach them through local aliases, so 'nothing references
# it' would be a claim about the scan, not about the code. Rebinding all of them keeps
# `main.<name>` exactly the surface it was before the split. Plain assignment rather than
# `from ... import x as x` only because isort expands the aliased form to three lines each.
# --- from cli/ast_scan.py ----------------------------------------------------
# --- from cli/doctor_report.py -----------------------------------------------
# --- from cli/doctor_payload.py ----------------------------------------------
# --- from cli/native_frontdoor.py --------------------------------------------
# --- from cli/windows_launcher.py --------------------------------------------
# backlog #1 : kept numerically in sync with
# repo_map.DEFAULT_AGENT_REPO_MAP_LIMIT (raised 512 -> 2000 for routing accuracy -- a file past
# the old cap never entered the map, so edit-plan/agent/context-render/defs misrouted on repos
# >512 files). This is a SEPARATE literal (not an import) because it is this module's CLI-option
# default, shared across both ROUTING commands (edit-plan/agent/context-render/defs/source) and
# CALLER-SCAN commands (callers/refs/blast-radius/impact/blast-radius-plan). Raising it to 2000
# is safe for the caller-scan commands ONLY because repo_map.CALLER_SCAN_FILE_CEILING bounds
# their actual per-file scan work at 512 regardless of how large this default is -- the
# chokepoint, not a per-command repoint, is what keeps them fast.
from tensor_grep.cli.error_handling import (
    _combine_multi_patterns,
    _eligible_for_pcre2_inline_flag_fallback,
    _emit_broad_scan_refusal,
    _exit_invalid_regex,
    _exit_search_error,
    _format_broad_generated_scan_error,
    _format_broad_workspace_scan_error,
    _format_unbounded_large_root_scan_error,
    _format_unbounded_vendored_root_scan_error,
    _implicit_glob_search_walk_exceeds_ceiling,
    _is_inline_flag_regex_error,
    _is_invalid_regex_error,
    _pcre2_fallback_backend_available,
    _search_paths_include_guarded_broad_root,
    _should_refuse_unbounded_generated_scan,
    _should_refuse_unbounded_vendored_root_scan,
    _should_refuse_unbounded_workspace_root_scan,
    _validate_search_regex,
)
from tensor_grep.cli.investigation_options import (
    _note_late_rerank_degraded as _note_late_rerank_degraded,
)
from tensor_grep.cli.investigation_options import (
    _set_semantic_rank_fallback_reason as _set_semantic_rank_fallback_reason,
)
from tensor_grep.cli.output_envelope import _cli_package_version, _path_has_hidden_component
from tensor_grep.cli.prepare_service import _build_prepare_payload as _build_prepare_payload
from tensor_grep.cli.routers.search_exec import _execute_search_command
from tensor_grep.cli.routers.search_helpers import (
    _generate_shell_completion_script,
    _parse_gpu_device_ids_cli,
    _run_rg_compatible_info_action,
    _warn_unavailable_gpu_device_ids,
)
from tensor_grep.cli.runtime_paths import _native_tg_version as _native_tg_version
from tensor_grep.cli.runtime_paths import _native_tg_version_matches as _native_tg_version_matches
from tensor_grep.cli.runtime_paths import env_flag_enabled
from tensor_grep.cli.runtime_paths import inspect_native_tg_binary as inspect_native_tg_binary
from tensor_grep.cli.runtime_paths import (
    is_cross_domain_native_binary as is_cross_domain_native_binary,
)

# Re-exported, not used here: a front-door checksum test reads `main.native_frontdoor_metadata_path`.
from tensor_grep.cli.runtime_paths import (
    native_frontdoor_metadata_path as native_frontdoor_metadata_path,
)
from tensor_grep.cli.runtime_paths import resolve_native_tg_binary as resolve_native_tg_binary
from tensor_grep.cli.runtime_paths import resolve_ripgrep_binary as resolve_ripgrep_binary
from tensor_grep.cli.runtime_paths import (
    translate_path_for_windows_binary as translate_path_for_windows_binary,
)
from tensor_grep.io.scan_limits import (
    BROAD_WORKSPACE_MARKED_ROOT_CHILD_THRESHOLD,
    BROAD_WORKSPACE_PROJECT_CHILD_THRESHOLD,
    BROAD_WORKSPACE_PROJECT_MARKERS,
)

_DEFAULT_AGENT_REPO_SCAN_LIMIT = 2000
_DEFAULT_BLAST_RADIUS_JSON_MAX_CALLERS = 25
_DEFAULT_BLAST_RADIUS_JSON_MAX_FILES = 25
# audit #96 (answer-first payloads): defs/refs/callers/impact's own DEDICATED tests-cap, wired
# on-by-default like blast-radius's --max-callers/--max-files precedent above (not an opt-in-only
# flag -- the audit's "95% payload filler" bug needs a default that actually fixes it).
_DEFAULT_SYMBOL_MAX_TESTS = 25
_DOCTOR_LSP_PROBE_TIMEOUT_SECONDS = 15.0
_DOCTOR_LSP_WINDOWS_PROBE_TIMEOUT_SECONDS = 15.0
_DOCTOR_LSP_PROBE_TIMEOUT_ENV = "TG_DOCTOR_LSP_PROBE_TIMEOUT_SECONDS"
_DOCTOR_SCHEMA_VERSION = 3
_DOCTOR_LSP_SCHEMA_VERSION = 2
_GUARDED_BROAD_SEARCH_ROOTS = {".claude", ".claude/context"}
_BROAD_GENERATED_SCAN_DIR_NAMES = {
    "__pycache__",
    ".claude",
    ".cache",
    ".cargo",
    ".git",
    ".gradle",
    ".mypy_cache",
    ".npm",
    ".nuget",
    ".pytest_cache",
    ".ruff_cache",
    ".rustup",
    ".tox",
    ".venv",
    "AppData",
    "artifacts",
    "build",
    "coverage",
    "dist",
    "node_modules",
    "target",
    "venv",
}
# Single source of truth: `io/directory_scanner.py` (item #154) -- keeps this file and
# `cli/bootstrap.py`'s front-door mirror from drifting out of sync.
_BROAD_WORKSPACE_PROJECT_CHILD_THRESHOLD = BROAD_WORKSPACE_PROJECT_CHILD_THRESHOLD
_BROAD_WORKSPACE_MARKED_ROOT_CHILD_THRESHOLD = BROAD_WORKSPACE_MARKED_ROOT_CHILD_THRESHOLD
_BROAD_WORKSPACE_PROJECT_MARKERS = BROAD_WORKSPACE_PROJECT_MARKERS
_GUARDED_BROAD_ROOT_RG_GLOBS = (
    "!context/**",
    "!**/context/**",
    "!node_modules/**",
    "!**/node_modules/**",
    "!__pycache__/**",
    "!**/__pycache__/**",
    "!dist/**",
    "!**/dist/**",
    "!build/**",
    "!**/build/**",
)


def search_command(
    # POSITIONAL ARGUMENTS
    positionals: list[str] | None = typer.Argument(
        None,
        help="PATTERN followed by file paths, or just file paths when --files is set.",
    ),
    # INPUT OPTIONS
    regexp: list[str] | None = typer.Option(
        None, "-e", "--regexp", help="A pattern to search for. Can be provided multiple times."
    ),
    file: list[str] | None = typer.Option(
        None,
        "-f",
        "--file",
        help="Search for patterns from the given file, with one pattern per line.",
    ),
    pre: str | None = typer.Option(
        None, "--pre", help="For each input PATH, search standard output of COMMAND PATH."
    ),
    no_pre: bool = typer.Option(False, "--no-pre", help="Disable any configured --pre command."),
    pre_glob: list[str] | None = typer.Option(
        None, "--pre-glob", help="Only run --pre command on files matching this glob."
    ),
    search_zip: bool = typer.Option(
        False, "-z", "--search-zip", help="Search in compressed files (gzip, bzip2, xz, lz4, etc)."
    ),
    no_search_zip: bool = typer.Option(
        False, "--no-search-zip", help="Do not search compressed files."
    ),
    # SEARCH OPTIONS
    case_sensitive: bool = typer.Option(
        False, "-s", "--case-sensitive", help="Execute the search case sensitively."
    ),
    crlf: bool = typer.Option(
        False, "--crlf", help="Treat CRLF as a line terminator instead of just LF."
    ),
    no_crlf: bool = typer.Option(
        False, "--no-crlf", help="Do not treat CRLF specially; useful for config overrides."
    ),
    dfa_size_limit: str | None = typer.Option(
        None, "--dfa-size-limit", help="The upper size limit of the regex DFA."
    ),
    encoding: str = typer.Option(
        "auto", "-E", "--encoding", help="Specify the text encoding (e.g., auto, none, utf-8)."
    ),
    no_encoding: bool = typer.Option(
        False, "--no-encoding", help="Disable configured explicit encoding."
    ),
    engine: str = typer.Option(
        "default", "--engine", help="Regex engine to use: 'default', 'pcre2', or 'auto'."
    ),
    fixed_strings: bool = typer.Option(
        False, "-F", "--fixed-strings", help="Treat all patterns as literals instead of regex."
    ),
    no_fixed_strings: bool = typer.Option(
        False, "--no-fixed-strings", help="Disable fixed-string mode."
    ),
    ignore_case: bool = typer.Option(
        False, "-i", "--ignore-case", help="Search case insensitively."
    ),
    invert_match: bool = typer.Option(
        False, "-v", "--invert-match", help="Invert matching (print lines that don't match)."
    ),
    no_invert_match: bool = typer.Option(
        False, "--no-invert-match", help="Disable inverted matching."
    ),
    line_regexp: bool = typer.Option(
        False, "-x", "--line-regexp", help="Only show matches surrounded by line boundaries."
    ),
    max_count: int | None = typer.Option(
        None, "-m", "--max-count", help="Limit the number of matching lines per file."
    ),
    mmap: bool = typer.Option(
        True, "--mmap", help="Search using memory maps when possible (enabled by default)."
    ),
    no_mmap: bool = typer.Option(False, "--no-mmap", help="Do not use memory maps."),
    multiline: bool = typer.Option(
        False, "-U", "--multiline", help="Enable searching across multiple lines."
    ),
    no_multiline: bool = typer.Option(False, "--no-multiline", help="Disable multiline mode."),
    multiline_dotall: bool = typer.Option(
        False, "--multiline-dotall", help="Enable 'dot all' mode in multiline searches."
    ),
    no_multiline_dotall: bool = typer.Option(
        False, "--no-multiline-dotall", help="Disable multiline dot-all mode."
    ),
    auto_hybrid_regex: bool = typer.Option(
        False,
        "--auto-hybrid-regex",
        help="Use ripgrep's hybrid regex engine selection when rg passthrough is selected.",
    ),
    no_auto_hybrid_regex: bool = typer.Option(
        False,
        "--no-auto-hybrid-regex",
        help="Disable ripgrep's hybrid regex engine selection; useful for config overrides.",
    ),
    unicode: bool = typer.Option(
        False, "--unicode", help="Enable Unicode mode for regex. This is the default."
    ),
    pcre2_unicode: bool = typer.Option(
        False,
        "--pcre2-unicode",
        help="Enable PCRE2 Unicode mode. Alias of --unicode in ripgrep.",
    ),
    no_pcre2_unicode: bool = typer.Option(
        False, "--no-pcre2-unicode", help="Disable PCRE2 Unicode mode."
    ),
    no_unicode: bool = typer.Option(False, "--no-unicode", help="Disable Unicode mode for regex."),
    null_data: bool = typer.Option(
        False, "--null-data", help="Use NUL as a line terminator instead of \\n."
    ),
    pcre2: bool = typer.Option(False, "-P", "--pcre2", help="Use the PCRE2 regex engine."),
    no_pcre2: bool = typer.Option(False, "--no-pcre2", help="Disable PCRE2 regex mode."),
    regex_size_limit: str | None = typer.Option(
        None, "--regex-size-limit", help="Size limit of the compiled regex."
    ),
    smart_case: bool = typer.Option(
        False, "-S", "--smart-case", help="Search case insensitively if pattern is all lowercase."
    ),
    stop_on_nonmatch: bool = typer.Option(
        False,
        "--stop-on-nonmatch",
        help="Stop reading file once a non-matching line is encountered after a match.",
    ),
    text: bool = typer.Option(
        False, "-a", "--text", help="Search binary files as if they were text."
    ),
    no_text: bool = typer.Option(False, "--no-text", help="Do not search binary files as text."),
    threads: int = typer.Option(
        0, "-j", "--threads", help="Approximate number of threads to use (0 = auto)."
    ),
    word_regexp: bool = typer.Option(
        False, "-w", "--word-regexp", help="Only show matches surrounded by word boundaries."
    ),
    # FILTER OPTIONS
    binary: bool = typer.Option(
        False, "--binary", help="Search binary files (don't stop on NUL byte)."
    ),
    no_binary: bool = typer.Option(
        False, "--no-binary", help="Do not search binary files unless --text is set."
    ),
    follow: bool | None = _search_follow_options.follow_option(),
    glob: list[str] | None = typer.Option(
        None, "-g", "--glob", help="Include/exclude files matching glob."
    ),
    glob_case_insensitive: bool = typer.Option(
        False, "--glob-case-insensitive", help="Process glob patterns case insensitively."
    ),
    no_glob_case_insensitive: bool = typer.Option(
        False,
        "--no-glob-case-insensitive",
        help="Process glob patterns case sensitively; useful for config overrides.",
    ),
    hidden: bool = typer.Option(
        False, "-.", "--hidden", help="Search hidden files and directories."
    ),
    iglob: list[str] | None = typer.Option(
        None, "--iglob", help="Include/exclude files matching glob (case-insensitive)."
    ),
    ignore_file: list[str] | None = typer.Option(
        None, "--ignore-file", help="Path to gitignore formatted rules file."
    ),
    ignore_file_case_insensitive: bool = typer.Option(
        False, "--ignore-file-case-insensitive", help="Process ignore files case insensitively."
    ),
    no_ignore_file_case_insensitive: bool = typer.Option(
        False,
        "--no-ignore-file-case-insensitive",
        help="Process ignore files case sensitively; useful for config overrides.",
    ),
    max_depth: int | None = typer.Option(
        None, "-d", "--max-depth", "--maxdepth", help="Limit depth of directory traversal."
    ),
    max_filesize: str | None = typer.Option(
        None, "--max-filesize", help="Ignore files larger than this size."
    ),
    no_ignore: bool = typer.Option(
        False, "--no-ignore", help="Don't respect ignore files (.gitignore, .rgignore, etc)."
    ),
    ignore: bool = typer.Option(
        False, "--ignore", help="Respect ignore files; useful for overriding ripgrep config."
    ),
    no_ignore_dot: bool = typer.Option(
        False, "--no-ignore-dot", help="Don't respect .ignore or .rgignore files."
    ),
    ignore_dot: bool = typer.Option(
        False, "--ignore-dot", help="Respect .ignore and .rgignore files."
    ),
    no_ignore_exclude: bool = typer.Option(
        False, "--no-ignore-exclude", help="Don't respect .git/info/exclude."
    ),
    ignore_exclude: bool = typer.Option(
        False, "--ignore-exclude", help="Respect .git/info/exclude."
    ),
    no_ignore_files: bool = typer.Option(
        False, "--no-ignore-files", help="Ignore any --ignore-file flags."
    ),
    ignore_files: bool = typer.Option(False, "--ignore-files", help="Respect --ignore-file flags."),
    no_ignore_global: bool = typer.Option(
        False, "--no-ignore-global", help="Don't respect global gitignore."
    ),
    ignore_global: bool = typer.Option(
        False, "--ignore-global", help="Respect global gitignore files."
    ),
    ignore_messages: bool = typer.Option(
        False, "--ignore-messages", help="Show ignore file parsing errors."
    ),
    no_ignore_parent: bool = typer.Option(
        False, "--no-ignore-parent", help="Don't respect ignore files in parent directories."
    ),
    ignore_parent: bool = typer.Option(
        False, "--ignore-parent", help="Respect ignore files in parent directories."
    ),
    ignore_vcs: bool = typer.Option(
        False, "--ignore-vcs", help="Respect source control ignore files."
    ),
    no_ignore_vcs: bool = typer.Option(
        False, "--no-ignore-vcs", help="Don't respect source control ignore files (.gitignore)."
    ),
    no_require_git: bool = typer.Option(
        False, "--no-require-git", help="Respect .gitignore even outside of git repos."
    ),
    require_git: bool = typer.Option(
        False,
        "--require-git",
        help="Require a git repo before respecting git ignore rules.",
    ),
    no_hidden: bool = typer.Option(
        False, "--no-hidden", help="Do not search hidden files and directories."
    ),
    one_file_system: bool = typer.Option(
        False, "--one-file-system", help="Don't cross file system boundaries."
    ),
    no_one_file_system: bool = typer.Option(
        False, "--no-one-file-system", help="Allow crossing file system boundaries."
    ),
    type: list[str] | None = typer.Option(
        None, "-t", "--type", help="Only search files matching TYPE."
    ),
    type_not: list[str] | None = typer.Option(
        None, "-T", "--type-not", help="Do not search files matching TYPE."
    ),
    type_add: list[str] | None = typer.Option(
        None, "--type-add", help="Add a new glob for a file type."
    ),
    type_clear: str | None = typer.Option(None, "--type-clear", help="Clear globs for TYPE."),
    unrestricted: int = typer.Option(
        0, "-u", "--unrestricted", count=True, help="Reduce smart filtering (repeat up to 3 times)."
    ),
    # OUTPUT OPTIONS
    after_context: int | None = typer.Option(
        None, "-A", "--after-context", help="Show NUM lines after each match."
    ),
    before_context: int | None = typer.Option(
        None, "-B", "--before-context", help="Show NUM lines before each match."
    ),
    block_buffered: bool = typer.Option(False, "--block-buffered", help="Force block buffering."),
    no_block_buffered: bool = typer.Option(
        False, "--no-block-buffered", help="Disable forced block buffering."
    ),
    byte_offset: bool = typer.Option(
        False, "-b", "--byte-offset", help="Print 0-based byte offset before each output line."
    ),
    no_byte_offset: bool = typer.Option(
        False, "--no-byte-offset", help="Do not print byte offsets."
    ),
    color: str = typer.Option(
        "auto", "--color", help="When to use colors: never, auto, always, ansi."
    ),
    colors: list[str] | None = typer.Option(
        None, "--colors", help="Color settings for output (e.g. 'match:fg:magenta')."
    ),
    column: bool = typer.Option(False, "--column", help="Show column numbers (1-based)."),
    no_column: bool = typer.Option(False, "--no-column", help="Do not show column numbers."),
    context: int | None = typer.Option(
        None, "-C", "--context", help="Show NUM lines before and after each match."
    ),
    context_separator: str = typer.Option(
        "--", "--context-separator", help="String used to separate non-contiguous context lines."
    ),
    no_context_separator: bool = typer.Option(
        False, "--no-context-separator", help="Disable explicit context separators."
    ),
    field_context_separator: str = typer.Option(
        "-", "--field-context-separator", help="Set the field context separator."
    ),
    field_match_separator: str = typer.Option(
        ":", "--field-match-separator", help="Set the field match separator."
    ),
    heading: bool = typer.Option(
        True, "--heading", help="Print file path above clusters of matches."
    ),
    hostname_bin: str | None = typer.Option(
        None, "--hostname-bin", help="Executable to determine system hostname."
    ),
    hyperlink_format: str | None = typer.Option(
        None, "--hyperlink-format", help="Format of hyperlinks to use."
    ),
    include_zero: bool = typer.Option(
        False, "--include-zero", help="Print zero match counts with -c."
    ),
    no_include_zero: bool = typer.Option(
        False, "--no-include-zero", help="Do not print zero match counts with -c."
    ),
    line_buffered: bool = typer.Option(False, "--line-buffered", help="Force line buffering."),
    no_line_buffered: bool = typer.Option(
        False, "--no-line-buffered", help="Disable forced line buffering."
    ),
    line_number: bool | None = typer.Option(
        None, "-n", "--line-number", help="Show line numbers (1-based)."
    ),
    no_line_number: bool = typer.Option(
        False, "-N", "--no-line-number", help="Suppress line numbers."
    ),
    max_columns: int | None = typer.Option(
        None, "-M", "--max-columns", help="Omit lines longer than this limit."
    ),
    max_columns_preview: bool = typer.Option(
        False, "--max-columns-preview", help="Preview lines exceeding max column limit."
    ),
    no_max_columns_preview: bool = typer.Option(
        False, "--no-max-columns-preview", help="Do not preview lines exceeding max column limit."
    ),
    null: bool = typer.Option(False, "-0", "--null", help="Follow file paths with a NUL byte."),
    only_matching: bool = typer.Option(
        False, "-o", "--only-matching", help="Print only the matched parts of a line."
    ),
    path_separator: str | None = typer.Option(
        None, "--path-separator", help="Path separator to use."
    ),
    passthru: bool = typer.Option(
        False,
        "--passthru",
        "--passthrough",
        help="Print both matching and non-matching lines.",
    ),
    pretty: bool = typer.Option(
        False, "-p", "--pretty", help="Alias for --color=always --heading --line-number."
    ),
    quiet: bool = typer.Option(False, "-q", "--quiet", help="Do not print anything to stdout."),
    replace: str | None = typer.Option(
        None,
        "-r",
        "--replace",
        help="Replace every match with the given text. Supports capture groups (e.g., $1).",
    ),
    sort: str = typer.Option(
        "none", "--sort", help="Sort results (none, path, modified, accessed, created)."
    ),
    sortr: str = typer.Option("none", "--sortr", help="Sort results in reverse order."),
    sort_files: bool = typer.Option(
        False,
        "--sort-files",
        help="Deprecated ripgrep alias for --sort path; disables parallel traversal.",
    ),
    trim: bool = typer.Option(False, "--trim", help="Remove leading ASCII whitespace from output."),
    no_trim: bool = typer.Option(
        False, "--no-trim", help="Do not remove leading ASCII whitespace from output."
    ),
    vimgrep: bool = typer.Option(
        False,
        "--vimgrep",
        help="Print results with every match on its own line (line/column numbers).",
    ),
    with_filename: bool = typer.Option(
        False, "-H", "--with-filename", help="Print file path for each matching line."
    ),
    no_filename: bool = typer.Option(
        False, "-I", "--no-filename", help="Never print the file path."
    ),
    # OUTPUT MODES
    count: bool = typer.Option(
        False, "-c", "--count", help="Show only the number of matching lines per file."
    ),
    count_matches: bool = typer.Option(
        False, "--count-matches", help="Show only the total number of matches per file."
    ),
    files_with_matches: bool = typer.Option(
        False, "-l", "--files-with-matches", help="Print only paths with at least one match."
    ),
    files_without_match: bool = typer.Option(
        False, "--files-without-match", help="Print paths containing zero matches."
    ),
    json: bool = typer.Option(
        False,
        "--json",
        help=(
            "Print results as one tensor-grep aggregate JSON object, not rg JSON Lines. "
            "Use --format rg --json for ripgrep JSON Lines or --ndjson for tensor-grep streaming output."
        ),
    ),
    rank: bool = typer.Option(
        False,
        "--rank",
        "--bm25",
        help=(
            "Re-rank results by BM25 lexical relevance to the query terms instead of grep order "
            "(pure-CPU ranking; no API key, no model download)."
        ),
    ),
    semantic: bool = typer.Option(
        False,
        "--semantic",
        help=(
            "Re-rank results by a hybrid of BM25 + local CPU dense-embedding relevance (RRF "
            "fusion), instead of grep order. No API key, no GPU. Requires the `semantic` extra "
            "and a fetched model; falls back to BM25-only (visibly, never silently) when "
            "either is missing."
        ),
    ),
    no_json: bool = typer.Option(
        False, "--no-json", help="Disable ripgrep JSON Lines when overriding rg config."
    ),
    ndjson: bool = typer.Option(
        False,
        "--ndjson",
        help="Print tensor-grep newline-delimited JSON rows, not the rg event schema.",
    ),
    citations: bool = typer.Option(False, "--citations", help="Emit citation ranges."),
    # LOGGING OPTIONS
    debug: bool = typer.Option(False, "--debug", help="Show debug messages."),
    no_ignore_messages: bool = typer.Option(
        False, "--no-ignore-messages", help="Suppress ignore file parsing errors."
    ),
    no_messages: bool = typer.Option(
        False, "--no-messages", help="Suppress some error messages (like failed file opens)."
    ),
    messages: bool = typer.Option(
        False, "--messages", help="Show normal diagnostic messages; overrides ripgrep config."
    ),
    stats: bool = typer.Option(False, "--stats", help="Print aggregate statistics."),
    no_stats: bool = typer.Option(False, "--no-stats", help="Do not print aggregate statistics."),
    trace: bool = typer.Option(False, "--trace", help="Show exhaustive trace messages."),
    # OTHER BEHAVIORS
    files: bool = typer.Option(
        False, "--files", help="Print files that would be searched and exit."
    ),
    generate: str | None = typer.Option(
        None,
        "--generate",
        help=(
            "Generate shell completion output "
            "(complete-bash, complete-zsh, complete-fish, complete-powershell)."
        ),
    ),
    no_config: bool = typer.Option(False, "--no-config", help="Never read configuration files."),
    pcre2_version: bool = typer.Option(
        False, "--pcre2-version", help="Print PCRE2 version and exit."
    ),
    type_list: bool = typer.Option(
        False, "--type-list", help="Show all supported file types and exit."
    ),
    version: bool = typer.Option(False, "-V", "--version", help="Show tensor-grep version."),
    # TENSOR-GREP SPECIFIC
    cpu: bool = typer.Option(
        False,
        "--cpu",
        "--force-cpu",
        help="Force CPU fallback (tensor-grep specific).",
    ),
    format_type: str = typer.Option(
        "rg",
        "--format",
        help="Output format: rg, json, table, or csv. Use rg for exact ripgrep-style text output.",
    ),
    ast: bool = typer.Option(
        False,
        "--ast",
        help="Parse files into ASTs and search structurally using tree-sitter.",
    ),
    lang: str | None = typer.Option(
        None,
        "--lang",
        help="Explicitly define language grammar for --ast (e.g. python, javascript).",
    ),
    ltl: bool = typer.Option(
        False,
        "--ltl",
        help="Interpret PATTERN as a temporal query (supports: 'A -> eventually B').",
    ),
    gpu_device_ids: str | None = typer.Option(
        None,
        "--gpu-device-ids",
        help="Comma-separated GPU IDs to pin this search request to (e.g. 0,1).",
    ),
    allow_broad_generated_scan: bool = typer.Option(
        False,
        "--allow-broad-generated-scan",
        help=(
            "Permit unbounded file-list/search scans through generated, cache, dependency, "
            "or multi-project workspace roots. Prefer scoped paths, --glob, --type, or "
            "--max-depth for agent runs."
        ),
    ),
    enrich_ast: bool = typer.Option(
        False,
        "--enrich-ast",
        help="Enrich code search matches with the enclosing AST container (function/class) using tree-sitter.",
    ),
) -> None:
    """
    Search files for a regex pattern. GPU routing is experimental and opt-in via --gpu-device-ids; CPU/ripgrep is the default and the current speed baseline.
    The stable text-search contract is the validated rg-compatible surface documented in docs/CONTRACTS.md.
    """
    # Perf (#94 Part-B): lazy-imported here (not at module top) so non-search commands don't pay
    # for backends.base's transitive core.config/core.result chain. search_command is the sole
    # runtime user of BackendExecutionError in this module (except-clauses further below), and
    # this sits before any loop, so it costs exactly one import per invocation, not per iteration.

    # Just forward to CPU backend for now as a stub.
    # Note: Full flag wiring will require mapping these dozens of parameters into the Pipeline/Core components.
    args = positionals or []
    pattern = ""
    regexp_patterns = regexp or []
    if generate is not None:
        typer.echo(_generate_shell_completion_script(generator=generate))
        raise typer.Exit(0)
    if version:
        typer.echo(f"tensor-grep {_cli_package_version()}")
        raise typer.Exit(0)
    if pcre2_version:
        _run_rg_compatible_info_action(
            "--pcre2-version",
            "PCRE2 version unavailable: no native tg or ripgrep binary found.",
        )
    if type_list:
        _run_rg_compatible_info_action(
            "--type-list",
            "Type list unavailable: no native tg or ripgrep binary found.",
        )
    if files:
        paths_to_search = args or ["."]
        paths_defaulted = not args
    elif regexp_patterns:
        pattern = regexp_patterns[0]
        if pattern == "":
            _exit_search_error(
                "empty_pattern",
                "PATTERN must not be empty.",
                json_mode=json,
            )
        paths_to_search = args or ["."]
        paths_defaulted = not args
    elif file:
        pattern = ""
        paths_to_search = args or ["."]
        paths_defaulted = not args
    else:
        if not args:
            typer.echo("Error: Please provide a PATTERN to search.", err=True)
            sys.exit(1)
        pattern = args[0]
        if pattern == "":
            _exit_search_error(
                "empty_pattern",
                "PATTERN must not be empty.",
                json_mode=json,
            )
        paths_to_search = args[1:] or ["."]
        paths_defaulted = not args[1:]

    # `-f/--file` (patterns-from-file) and multiple `-e/--regexp` never build a real combined-pattern
    # regex -- `pattern` above is simply "" when the `elif file:` branch above actually ran (bool(file)
    # AND no regexp given, since `elif regexp_patterns:` takes priority over `elif file:` and would make
    # `-f` a dead flag), or regexp_patterns[0] (silently drops the rest) when multiple `-e` were given.
    # -o/-r/--rank/--semantic all operate on that single `pattern` string, so combining them previously
    # either silently returned zero matches (-o against pattern="") or reranked/replaced against the
    # wrong text. The multi-pattern combine feature was scoped OUT (#441 closed); reject the combo up
    # front instead, mirroring the plain-`--json` render-flag guard above (audit #5/#20). Excludes
    # `--files` mode (a distinct, unrelated file-listing path) and a single `-e` alongside an
    # otherwise-dead `-f` (regexp_patterns already wins there, so `pattern` is real).
    multi_pattern_source = not files and (
        (not regexp_patterns and bool(file)) or len(regexp_patterns) > 1
    )
    if multi_pattern_source:
        conflicting_flags = [
            spelling
            for present, spelling in (
                (only_matching, "-o/--only-matching"),
                (replace is not None, "-r/--replace"),
                (rank, "--rank/--bm25"),
                (semantic, "--semantic"),
            )
            if present
        ]
        if conflicting_flags:
            flag_list = " and ".join(conflicting_flags)
            source = "multiple -e/--regexp patterns" if len(regexp_patterns) > 1 else "-f/--file"
            _exit_search_error(
                "unsupported_flag",
                (
                    f"{flag_list} not supported with {source} (no single combined-pattern regex "
                    "is built from them); drop the flag(s), or provide a single -e/--regexp pattern."
                ),
                json_mode=json,
                exit_code=2,
            )

    if not files:
        missing_paths = [
            path for path in paths_to_search if path != "-" and not Path(path).exists()
        ]
        if missing_paths:
            if json:
                detail = "search path does not exist: " + ", ".join(missing_paths)
                _exit_search_error("path_not_found", detail, json_mode=True)
            else:
                for missing_path in missing_paths:
                    typer.echo(
                        f"Error: search path does not exist: {missing_path}",
                        err=True,
                    )
                sys.exit(2)

    # Capture whether the user explicitly chose a line-number mode BEFORE auto-resolving (so native
    # delegation can forward only an explicit -n/-N and leave the auto case to the native binary).
    line_number_explicit = bool(no_line_number) or line_number is True
    if no_line_number:
        line_number = False
    elif line_number is None:
        line_number = sys.stdout.isatty()
    if max_columns == 0:
        max_columns = None
    elif max_columns is None and not json and not ndjson and not vimgrep and _self._stdout_isatty():
        max_columns = 300

    from tensor_grep.core.config import SearchConfig

    follow, no_follow = _search_follow_options.resolve_flags(follow)

    from tensor_grep.cli.investigation_options import parse_gpu_device_ids_or_exit

    parsed_gpu_device_ids = parse_gpu_device_ids_or_exit(
        gpu_device_ids,
        parse_fn=_parse_gpu_device_ids_cli,
        json_mode=json,
        exit_error_fn=_exit_search_error,
    )
    _warn_unavailable_gpu_device_ids(parsed_gpu_device_ids)

    effective_force_cpu = cpu or env_flag_enabled("TG_FORCE_CPU")
    implicit_with_filename = (
        not no_filename
        and not effective_force_cpu
        and not json
        and not ndjson
        and not only_matching
        and not parsed_gpu_device_ids
        and replace is None
        and (
            len(paths_to_search) > 1
            or any(path != "-" and Path(path).is_dir() for path in paths_to_search)
        )
    )

    config = SearchConfig(
        rank_bm25=rank,
        semantic_rank=semantic,
        regexp=regexp,
        file_patterns=file,
        pre=pre,
        no_pre=no_pre,
        pre_glob=pre_glob,
        search_zip=search_zip,
        no_search_zip=no_search_zip,
        case_sensitive=case_sensitive,
        crlf=crlf,
        no_crlf=no_crlf,
        dfa_size_limit=dfa_size_limit,
        encoding=encoding,
        no_encoding=no_encoding,
        engine=engine,
        fixed_strings=fixed_strings,
        no_fixed_strings=no_fixed_strings,
        ignore_case=ignore_case,
        invert_match=invert_match,
        no_invert_match=no_invert_match,
        line_regexp=line_regexp,
        max_count=max_count,
        mmap=mmap,
        no_mmap=no_mmap,
        multiline=multiline,
        no_multiline=no_multiline,
        multiline_dotall=multiline_dotall,
        no_multiline_dotall=no_multiline_dotall,
        auto_hybrid_regex=auto_hybrid_regex,
        no_auto_hybrid_regex=no_auto_hybrid_regex,
        no_unicode=no_unicode,
        unicode=unicode,
        pcre2_unicode=pcre2_unicode,
        no_pcre2_unicode=no_pcre2_unicode,
        null_data=null_data,
        pcre2=pcre2,
        no_pcre2=no_pcre2,
        regex_size_limit=regex_size_limit,
        smart_case=smart_case,
        stop_on_nonmatch=stop_on_nonmatch,
        text=text,
        no_text=no_text,
        threads=threads,
        word_regexp=word_regexp,
        binary=binary,
        no_binary=no_binary,
        follow=follow,
        no_follow=no_follow,
        glob=glob,
        glob_case_insensitive=glob_case_insensitive,
        no_glob_case_insensitive=no_glob_case_insensitive,
        hidden=hidden,
        iglob=iglob,
        ignore_file=ignore_file,
        ignore_file_case_insensitive=ignore_file_case_insensitive,
        no_ignore_file_case_insensitive=no_ignore_file_case_insensitive,
        max_depth=max_depth,
        max_filesize=max_filesize,
        ignore=ignore,
        no_ignore=no_ignore,
        ignore_dot=ignore_dot,
        no_ignore_dot=no_ignore_dot,
        ignore_exclude=ignore_exclude,
        no_ignore_exclude=no_ignore_exclude,
        ignore_files=ignore_files,
        no_ignore_files=no_ignore_files,
        ignore_global=ignore_global,
        no_ignore_global=no_ignore_global,
        ignore_parent=ignore_parent,
        no_ignore_parent=no_ignore_parent,
        ignore_vcs=ignore_vcs,
        no_ignore_vcs=no_ignore_vcs,
        no_require_git=no_require_git,
        require_git=require_git,
        no_hidden=no_hidden,
        one_file_system=one_file_system,
        no_one_file_system=no_one_file_system,
        file_type=type,
        type_not=type_not,
        type_add=type_add,
        type_clear=type_clear,
        unrestricted=unrestricted,
        after_context=after_context,
        before_context=before_context,
        block_buffered=block_buffered,
        no_block_buffered=no_block_buffered,
        byte_offset=byte_offset,
        no_byte_offset=no_byte_offset,
        color=color,
        colors=colors,
        column=column,
        no_column=no_column,
        context=context,
        context_separator=context_separator,
        no_context_separator=no_context_separator,
        field_context_separator=field_context_separator,
        field_match_separator=field_match_separator,
        heading=heading,
        hostname_bin=hostname_bin,
        hyperlink_format=hyperlink_format,
        include_zero=include_zero,
        no_include_zero=no_include_zero,
        line_buffered=line_buffered,
        no_line_buffered=no_line_buffered,
        line_number=line_number,
        line_number_explicit=line_number_explicit,
        max_columns=max_columns,
        max_columns_preview=max_columns_preview,
        no_max_columns_preview=no_max_columns_preview,
        null=null,
        only_matching=only_matching,
        path_separator=path_separator,
        passthru=passthru,
        pretty=pretty,
        quiet=quiet,
        replace_str=replace,
        sort_by=sort,
        sort_by_reverse=sortr,
        sort_files=sort_files,
        trim=trim,
        no_trim=no_trim,
        vimgrep=vimgrep,
        with_filename=with_filename or implicit_with_filename,
        no_filename=no_filename,
        count=count,
        count_matches=count_matches,
        files_with_matches=files_with_matches,
        files_without_match=files_without_match,
        json_mode=json,
        no_json=no_json,
        debug=debug,
        ignore_messages=ignore_messages,
        no_ignore_messages=no_ignore_messages,
        no_messages=no_messages,
        messages=messages,
        stats=stats,
        no_stats=no_stats,
        trace=trace,
        list_files=files,
        generate=generate,
        no_config=no_config,
        pcre2_version=pcre2_version,
        type_list=type_list,
        force_cpu=effective_force_cpu,
        format_type=format_type,
        ast=ast,
        lang=lang,
        ltl=ltl,
        query_pattern=pattern,
        gpu_device_ids=parsed_gpu_device_ids,
    )
    if not files:
        # audit #69 (re-do of #441, this time with a Windows golden from the start):
        # `multi_pattern_source` already excludes -e-and-f-together (a single -e still makes
        # -f a dead flag, pinned by
        # test_search_single_regexp_with_unused_file_option_and_only_matching_still_works)
        # and excludes -o/-r/--rank/--semantic (rejected with exit 2 above), so this is
        # exactly the plain-search shape that used to silently drop every pattern but the
        # first (multiple -e) or never read the file at all (-f alone). The multiple-`-e`
        # sub-case combines EAGERLY here -- no I/O, and the rg-routed passthrough path is
        # untouched by it either way (see the combine step below). The `-f`-alone sub-case is
        # handled LATER, only once the search is confirmed to not be rg-passthrough
        # (deliberately deferred: an eager read here broke
        # test_python_search_treats_file_option_as_pattern_file_not_regex, where real `rg`
        # itself must read the `-f` file on the passthrough path, never tg).
        combined_multi_patterns: list[str] | None = (
            list(regexp_patterns) if multi_pattern_source and len(regexp_patterns) > 1 else None
        )
        try:
            patterns_to_validate = (
                combined_multi_patterns
                if combined_multi_patterns is not None
                else (regexp_patterns if regexp_patterns else [pattern])
            )
            for regex_pattern in patterns_to_validate:
                _validate_search_regex(regex_pattern, config)
        except Exception as exc:
            if _is_invalid_regex_error(exc):
                # M14b: a mid-pattern inline flag group (e.g. `start(?s).*end`) is rejected
                # by the default Rust/`re` engine but accepted by PCRE2. When the user did
                # not explicitly pick a non-PCRE2 engine, retry transparently under PCRE2
                # instead of erroring, and announce the switch on stderr so it is observable.
                if (
                    _is_inline_flag_regex_error(str(exc))
                    and _eligible_for_pcre2_inline_flag_fallback(config)
                    and _pcre2_fallback_backend_available()
                ):
                    config = dataclasses.replace(config, pcre2=True)
                    typer.echo(
                        "note: retried with PCRE2 (-P) for inline-flag pattern",
                        err=True,
                    )
                else:
                    _exit_invalid_regex(exc, json_mode=json)
            else:
                raise
        if combined_multi_patterns is not None:
            # Build one rg-parity OR-alternation and let 100% of the existing
            # single-pattern machinery (CPUBackend, the Rust FFI, native-binary delegation)
            # treat it exactly like a hand-typed `-e "foo|bar"` -- the rg-ROUTED passthrough
            # path is untouched by this (it reads `config.regexp`/`config.file_patterns`
            # directly and builds its own rg argv; see ripgrep_backend.py:788). `-F`
            # multi-literal is `re.escape`'d per branch, so `fixed_strings` must be cleared
            # here or the combined alternation string would be re-literal-matched whole.
            pattern = _combine_multi_patterns(
                combined_multi_patterns, fixed_strings=config.fixed_strings
            )
            config = dataclasses.replace(config, query_pattern=pattern, fixed_strings=False)
    guarded_broad_root = _search_paths_include_guarded_broad_root(paths_to_search)
    explicit_hidden_search_root = not config.hidden and any(
        _path_has_hidden_component(path) for path in paths_to_search
    )
    refuse_generated_scan, generated_scan_dirs = _should_refuse_unbounded_generated_scan(
        paths_to_search,
        config,
        allow_broad_generated_scan=allow_broad_generated_scan,
        files_mode=files,
    )
    if refuse_generated_scan:
        _emit_broad_scan_refusal(
            _format_broad_generated_scan_error(generated_scan_dirs),
            json_output=json,
            path=str(paths_to_search[0]) if paths_to_search else ".",
        )
        raise typer.Exit(2)
    refuse_workspace_scan, workspace_project_dirs = _should_refuse_unbounded_workspace_root_scan(
        paths_to_search,
        config,
        allow_broad_generated_scan=allow_broad_generated_scan,
        paths_defaulted=paths_defaulted,
    )
    if refuse_workspace_scan:
        _emit_broad_scan_refusal(
            _format_broad_workspace_scan_error(workspace_project_dirs),
            json_output=json,
            path=str(paths_to_search[0]) if paths_to_search else ".",
            incomplete_reason_class="workspace_root_refused",
            error_code="workspace_root_refused",
        )
        raise typer.Exit(2)
    refuse_vendored_scan, vendored_root_dirs = _should_refuse_unbounded_vendored_root_scan(
        paths_to_search,
        config,
        allow_broad_generated_scan=allow_broad_generated_scan,
        paths_defaulted=paths_defaulted,
    )
    if refuse_vendored_scan:
        _emit_broad_scan_refusal(
            _format_unbounded_vendored_root_scan_error(vendored_root_dirs),
            json_output=json,
            path=str(paths_to_search[0]) if paths_to_search else ".",
        )
        raise typer.Exit(2)

    # Bug #88 (dogfood v1.54.1 re-harvest): an implicit-path `--glob`/`--type` search that the
    # workspace/vendored guards above did not catch (a large single-project root whose top level
    # carries a project marker, e.g. a workspace dir with a package.json) would otherwise hand the
    # whole unbounded `.` walk to the rg passthrough / native delegation below. Mirror the native
    # binary's WALK-ceiling guard here so the full CLI refuses fast too. Gated on `paths_defaulted`
    # (an explicit, deliberately-scoped PATH still runs uninhibited -- Trap #3); `--max-depth` and
    # `--allow-broad-generated-scan` bypass it (a genuinely bounded walk / an opt-in override).
    #
    # P0-1 (dogfood + external audit 2026-07-11): fire for an unscoped search that carries NO
    # glob/type filter too, not just the glob/type combo. The plain fast-path search is already
    # bounded upstream -- the bootstrap front door delegates it to the native binary (whose own
    # walk-ceiling guard refuses) or to rg passthrough -- so a bare `tg search PATTERN` never
    # reaches this Python guard when a native/rg engine exists. The gap this closes is the
    # FULL-CLI path: a query that carries a TG-only flag (`--rank`/`--semantic`/`--cpu`, ...) is
    # forced to the full CLI where NO fast native/rg engine can serve it, and if no glob/type
    # rode along, the old gate let it fall through to the unbounded per-file Python loop and burn
    # the wall-clock deadline (dogfood-reproduced: `tg search PATTERN --rank` on a >1500-file
    # unscoped root did the full walk instead of refusing). The probe strips glob/type anyway, so
    # it counts every walked file (early-stopping at the ceiling) regardless of the filter flags.
    if (
        paths_defaulted
        and not allow_broad_generated_scan
        and config.max_depth is None
        and _implicit_glob_search_walk_exceeds_ceiling(
            paths_to_search, config, _self._LARGE_ROOT_SCAN_FILE_CEILING
        )
    ):
        _emit_broad_scan_refusal(
            _format_unbounded_large_root_scan_error(_self._LARGE_ROOT_SCAN_FILE_CEILING),
            json_output=json,
            path=str(paths_to_search[0]) if paths_to_search else ".",
        )
        raise typer.Exit(2)

    explicit_rg_format = _citations.validate_search_citation_from_locals(locals())
    # C3: plain `--json` emits one aggregate object and cannot render ripgrep's
    # text-shaping flags. Honoring them is impossible and silently dropping them is a
    # footgun that also lets the front-door launcher spawn an undrained text-render
    # child (-> deadlock). Fail fast and deterministically before any child is spawned.
    if json and not explicit_rg_format:
        # Detect from PARSED typer params (not sys.argv): reading sys.argv mis-fires when
        # the typer app is invoked in-process (e.g. CliRunner under pytest, whose argv
        # carries -p/--pretty-looking flags). The ambiguous-default flags (--heading and
        # the separators) are caught for the real CLI by the bootstrap launcher guard
        # (_json_aggregate_blocks_passthrough), so the secondary net here only needs the
        # unambiguously-set render flags (audit C3).
        incompatible_render_flags = [
            spelling
            for present, spelling in (
                (passthru, "--passthru"),
                (trim, "--trim"),
                (byte_offset, "-b"),
                (max_columns is not None, "-M"),
                (max_columns_preview, "--max-columns-preview"),
                (pretty, "-p"),
            )
            if present
        ]
        if incompatible_render_flags:
            flag_list = ", ".join(incompatible_render_flags)
            _exit_search_error(
                "unsupported_flag",
                (
                    f"flag(s) {flag_list} not supported with plain --json; "
                    "use --format rg --json for ripgrep JSON Lines that carry render "
                    "metadata, or drop the flag(s)."
                ),
                json_mode=True,
                exit_code=2,
            )
    native_tg_binary = _self.resolve_native_tg_binary()
    if os.environ.get("TG_REEXEC_GUARD"):
        # The native door spawned this process for a flag it routes to Python (python_sidecar.rs
        # `configure_python_child_environment`). Delegating back is the native<->python loop
        # (2026-10-05 P0). The bootstrap honours this guard and falls through to THIS site for
        # `--json`, so this site must honour it too: search runs in-process.
        native_tg_binary = None
    if (
        native_tg_binary is not None
        and not citations
        and not guarded_broad_root
        and not explicit_hidden_search_root
        and not (json and explicit_rg_format)
        and not enrich_ast
        and _self._can_delegate_to_native_tg_search(
            config,
            ndjson=ndjson,
            files_mode=files,
            files_with_matches=files_with_matches,
            files_without_match=files_without_match,
            format_type=format_type,
        )
    ):
        sys.exit(
            _self._delegate_to_native_tg_search(
                native_tg_binary,
                pattern=pattern,
                paths=paths_to_search,
                config=config,
                ndjson=ndjson,
            )
        )
    from tensor_grep.backends.ripgrep_backend import RipgrepBackend

    rg_backend = RipgrepBackend()
    rg_is_available = rg_backend.is_available()
    if config.count_matches and not rg_is_available:
        # Backend Fail-Closed Contract (AGENTS.md) / task #121: `--count-matches` reports
        # ripgrep's OCCURRENCE count (every match on a line counts separately), which is
        # semantically different from `-c`/`--count`'s LINE count (one per matching line,
        # regardless of how many times the pattern occurs on it). Every fallback engine
        # that can serve a query without rg -- RustCoreBackend AND CPUBackend -- is
        # LINE-granular only: neither ever emits more than one match per line, so a
        # `--count-matches` request routed to either would silently report a LINE count
        # mislabeled as an occurrence count (verified live: a 3-occurrence line undercounts
        # to 1). Unlike the default/--json search degrade (a like-for-like engine swap: the
        # native engine's match set IS what tg's own aggregate model already uses), there is
        # no fallback engine here that can serve the SAME semantics rg provides, so silently
        # "degrading" would be silent-wrong-output, not a graceful degrade. Refuse cleanly
        # instead -- mirroring the identical refusal the native `--index` fast path already
        # applies to `count_matches` for the same reason (`IndexFlagPolicy::Refuse` in
        # rust_core/src/main.rs) -- rather than let it through as a wrong number. `-c`/
        # `--count` is unaffected: its line-count contract is exactly what the fallback
        # engines already provide correctly (Pipeline's `count_rust_fast_path`).
        _exit_search_error(
            "count_matches_requires_ripgrep",
            (
                "--count-matches reports ripgrep's per-occurrence match count, which "
                "requires the 'rg' binary; rg was not found (checked TG_RG_PATH, PATH, "
                "and the bundled fallback). Install ripgrep "
                "(https://github.com/BurntSushi/ripgrep#installation), set TG_RG_PATH, "
                "or use --count (-c) for a line count that works without rg."
            ),
            json_mode=json,
        )
    # P5·H2 (audit verdict / codex Finding 1): `-l`/`--files-with-matches`/`--files-without-match`
    # are RAW PATH-OUTPUT modes. The tensor-grep aggregate `--json`/`--ndjson` envelope has no
    # place for a bare path list, and the files emitters below (main.py ~the `if files_with_matches:`
    # / `if files_without_match:` blocks) would print plain paths with exit 0 while the caller asked
    # for structured output -- the JSON contract silently dropped (verified live:
    # `tg search --json -l PAT DIR` emits the path, exit 0). Fail closed, mirroring the native
    # `exit_native_structured_flag_dropped` refusal (rust_core/src/main.rs) and the
    # `count_matches_requires_ripgrep` refusal above (same `_exit_search_error` contract).
    # `--format rg --json` (rg passthrough) is deliberately excluded: `_can_passthrough_rg` returns
    # False for files + rg_json_passthrough, so that route never reaches these emitters as an
    # additive same-JSON contract issue (its raw-paths behavior is pre-existing and tracked
    # separately). The exemption REQUIRES `not ndjson`: `--ndjson` has no rg-passthrough twin
    # (rg's `--json` events are not the tensor-grep ndjson schema), so `--json --ndjson --format rg
    # -l` must still refuse rather than drop the ndjson contract (codex round-3 finding). Plain
    # `-l`/`--files-with-matches` WITHOUT --json/--ndjson keeps working (it
    # routes to rg passthrough below; pinned by test_cli_modes.py::test_files_with_matches_*).
    if (
        (json or ndjson)
        and not (json and explicit_rg_format and not ndjson)
        and (files_with_matches or files_without_match)
    ):
        flag_list = ", ".join(
            spelling
            for present, spelling in (
                (files_with_matches, "-l/--files-with-matches"),
                (files_without_match, "--files-without-match"),
            )
            if present
        )
        _exit_search_error(
            "unsupported_flag",
            (
                f"{flag_list} is a raw path-output mode that cannot be expressed inside the "
                "tensor-grep aggregate --json/--ndjson envelope and would be silently dropped; "
                f"refusing rather than silently ignoring it. Drop --json/--ndjson (or {flag_list}) "
                "to get the raw path list."
            ),
            json_mode=json,
        )
    _execute_search_command(
        allow_broad_generated_scan=allow_broad_generated_scan,
        citations=citations,
        config=config,
        count=count,
        debug=debug,
        effective_force_cpu=effective_force_cpu,
        enrich_ast=enrich_ast,
        explicit_hidden_search_root=explicit_hidden_search_root,
        explicit_rg_format=explicit_rg_format,
        file=file,
        files=files,
        files_with_matches=files_with_matches,
        files_without_match=files_without_match,
        format_type=format_type,
        guarded_broad_root=guarded_broad_root,
        json=json,
        ltl=ltl,
        max_count=max_count,
        multi_pattern_source=multi_pattern_source,
        ndjson=ndjson,
        null=null,
        only_matching=only_matching,
        parsed_gpu_device_ids=parsed_gpu_device_ids,
        paths_defaulted=paths_defaulted,
        paths_to_search=paths_to_search,
        pattern=pattern,
        quiet=quiet,
        regexp_patterns=regexp_patterns,
        rg_backend=rg_backend,
        rg_is_available=rg_is_available,
        stats=stats,
    )


def register(app: typer.Typer) -> None:
    app.command(
        name="search",
        help="Search files for a regex pattern. GPU routing is experimental and opt-in via --gpu-device-ids; CPU/ripgrep is the default and the current speed baseline.\nThe stable text-search contract is the validated common rg-compatible subset documented in docs/CONTRACTS.md.\nUse --format rg --json when a tool needs ripgrep JSON Lines events; plain --json is tensor-grep aggregate JSON.\n\n**Other Available Subcommands:**\n- `tg calibrate`: Measure CPU vs GPU crossover thresholds\n- `tg devices`: Print routable GPU device IDs and VRAM inventory\n- `tg mcp`: Start the AI-assistant Model Context Protocol (MCP) server\n- `tg classify`: Run log classification with local heuristics by default, or CyBERT when explicitly enabled\n- `tg run`: Run a validated AST slice for structural search and guarded rewrites\n- `tg scan` / `tg test` / `tg lsp`: Auxiliary AST workflows\n- `tg upgrade` / `tg update`: Upgrade tensor-grep in place\n",
    )(search_command)

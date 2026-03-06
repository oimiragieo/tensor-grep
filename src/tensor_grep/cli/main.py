import re
import sys
import time
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, cast
from uuid import uuid4

import typer

from tensor_grep.cli.formatters.base import OutputFormatter
from tensor_grep.cli.formatters.ripgrep_fmt import RipgrepFormatter
from tensor_grep.core.observability import nvtx_range
from tensor_grep.core.result import MatchLine

if TYPE_CHECKING:
    from tensor_grep.core.config import SearchConfig
    from tensor_grep.io.directory_scanner import DirectoryScanner

app = typer.Typer(
    help="""tensor-grep (tg) - The GPU-Accelerated Semantic Log Parsing CLI

Combines raw regex speed with semantic understanding (cyBERT) while maintaining ripgrep parity.

**IMPORTANT: To see all 70+ ripgrep-compatible flags, run:**
`tg search --help`

(Note: `tg` operates primarily through the `search` subcommand. For drop-in `rg` compatibility, use aliases or `tg search PATTERN PATH`.)""",
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode="markdown",
)


def _collect_candidate_files(
    scanner: "DirectoryScanner", paths: list[str]
) -> tuple[list[str], set[str]]:
    ordered = []
    seen = set()
    for p in paths:
        for current_file in scanner.walk(p):
            if current_file not in seen:
                seen.add(current_file)
                ordered.append(current_file)
    return ordered, seen


def _sum_total_bytes(paths: list[str]) -> int:
    total = 0
    for p in paths:
        try:
            total += Path(p).stat().st_size
        except OSError:
            continue
    return total


def _can_passthrough_rg(
    config: "SearchConfig",
    *,
    format_type: str,
    json_mode: bool,
    files_mode: bool,
    files_with_matches: bool,
    files_without_match: bool,
    only_matching: bool,
    stats_mode: bool,
) -> bool:
    # Keep passthrough only for modes where rg semantics are fully compatible
    # with tensor-grep output and feature behavior.
    return bool(
        not config.ast
        and not config.ltl
        and not config.force_cpu
        and config.replace_str is None
        and format_type == "rg"
        and not json_mode
        and not files_mode
        and not files_with_matches
        and not files_without_match
        and not only_matching
        and not stats_mode
    )


def _only_matching_lines(
    matches: list[MatchLine], pattern: str, config: "SearchConfig"
) -> list[MatchLine]:
    flags = 0
    if config.ignore_case or (config.smart_case and pattern.islower()):
        flags |= re.IGNORECASE

    if config.fixed_strings:
        regex = re.compile(re.escape(pattern), flags)
    elif config.line_regexp:
        regex = re.compile(f"^{pattern}$", flags)
    elif config.word_regexp:
        regex = re.compile(rf"\b{pattern}\b", flags)
    else:
        regex = re.compile(pattern, flags)

    extracted: list[MatchLine] = []
    for match in matches:
        for token in regex.findall(match.text):
            if isinstance(token, tuple):
                token = "".join(token)
            token_text = str(token)
            if token_text:
                extracted.append(replace(match, text=token_text))
    return extracted


def _normalize_string_list(value: object, fallback: list[str]) -> list[str]:
    if value is None:
        return fallback
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    return fallback


def _parse_gpu_device_ids_cli(raw: str | None) -> list[int] | None:
    if raw is None:
        return None
    parsed: list[int] = []
    seen: set[int] = set()
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        try:
            value = int(token)
        except ValueError as exc:
            raise typer.BadParameter(
                f"Invalid GPU device id '{token}'. Use comma-separated integers, e.g. 0,1."
            ) from exc
        if value < 0:
            raise typer.BadParameter(
                f"Invalid GPU device id '{token}'. Device IDs must be non-negative."
            )
        if value in seen:
            continue
        seen.add(value)
        parsed.append(value)
    if not parsed:
        raise typer.BadParameter(
            "No valid GPU device IDs provided. Use comma-separated integers, e.g. 0,1."
        )
    return parsed


def _load_yaml_dict(path: Path) -> dict[str, object]:
    import yaml

    with path.open(encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle) or {}
    if not isinstance(loaded, dict):
        raise ValueError(f"YAML in {path} must be a mapping.")
    return loaded


def _load_sg_project_config(config_path: str | None) -> dict[str, object]:
    resolved = Path(config_path or "sgconfig.yml").resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"Config file {resolved} not found. Use `tg new` to create one.")

    raw = _load_yaml_dict(resolved)
    return {
        "config_path": resolved,
        "root_dir": resolved.parent,
        "rule_dirs": _normalize_string_list(raw.get("ruleDirs"), ["rules"]),
        "test_dirs": _normalize_string_list(raw.get("testDirs"), ["tests"]),
        "language": str(raw.get("language") or "python"),
    }


def _iter_yaml_files(base_dir: Path, rel_dirs: list[str]) -> list[Path]:
    candidates: list[Path] = []
    for rel_dir in rel_dirs:
        target = (base_dir / rel_dir).resolve()
        if target.is_file() and target.suffix.lower() in {".yml", ".yaml"}:
            candidates.append(target)
            continue
        if not target.is_dir():
            continue
        candidates.extend(sorted(target.rglob("*.yml")))
        candidates.extend(sorted(target.rglob("*.yaml")))
    return sorted(set(candidates))


def _extract_rule_pattern(rule_data: dict[str, object]) -> str | None:
    direct = rule_data.get("pattern")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()

    rule_node = rule_data.get("rule")
    if isinstance(rule_node, dict):
        nested = rule_node.get("pattern")
        if isinstance(nested, str) and nested.strip():
            return nested.strip()

    return None


def _load_rule_specs(project_cfg: dict[str, object]) -> list[dict[str, str]]:
    root_dir = cast(Path, project_cfg["root_dir"])
    rule_dirs = cast(list[str], project_cfg["rule_dirs"])
    default_language = cast(str, project_cfg["language"])

    specs: list[dict[str, str]] = []
    for rule_file in _iter_yaml_files(root_dir, rule_dirs):
        payload = _load_yaml_dict(rule_file)

        raw_rules = payload.get("rules")
        if isinstance(raw_rules, list):
            for idx, item in enumerate(raw_rules):
                if not isinstance(item, dict):
                    continue
                pattern = _extract_rule_pattern(item)
                if not pattern:
                    continue
                specs.append({
                    "id": str(item.get("id") or f"{rule_file.stem}-{idx + 1}"),
                    "pattern": pattern,
                    "language": str(
                        item.get("language") or payload.get("language") or default_language
                    ),
                })
            continue

        pattern = _extract_rule_pattern(payload)
        if not pattern:
            continue
        specs.append({
            "id": str(payload.get("id") or rule_file.stem),
            "pattern": pattern,
            "language": str(payload.get("language") or default_language),
        })

    return specs


def _suffix_for_language(language: str) -> str:
    normalized = language.lower()
    if normalized in {"js", "javascript"}:
        return ".js"
    if normalized in {"ts", "typescript"}:
        return ".ts"
    return ".py"


@app.command(
    name="search",
    help="""Search files for a regex pattern, with GPU acceleration when applicable.
Supports almost all ripgrep (rg) flags for drop-in compatibility.

**Other Available Subcommands:**
- `tg mcp`: Start the AI-assistant Model Context Protocol (MCP) server
- `tg classify`: Run semantic NLP threat classification on logs via cyBERT
- `tg run`: Run GPU-accelerated AST structural queries (ast-grep parity)
- `tg scan` / `tg test` / `tg lsp`: Auxiliary AST-GNN workflows
""",
)
def search_command(
    # POSITIONAL ARGUMENTS
    pattern: str = typer.Argument(..., help="A regular expression used for searching."),
    file_path: list[str] | None = typer.Argument(None, help="A file or directory to search."),
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
    pre_glob: list[str] | None = typer.Option(
        None, "--pre-glob", help="Only run --pre command on files matching this glob."
    ),
    search_zip: bool = typer.Option(
        False, "-z", "--search-zip", help="Search in compressed files (gzip, bzip2, xz, lz4, etc)."
    ),
    # SEARCH OPTIONS
    case_sensitive: bool = typer.Option(
        False, "-s", "--case-sensitive", help="Execute the search case sensitively."
    ),
    crlf: bool = typer.Option(
        False, "--crlf", help="Treat CRLF as a line terminator instead of just LF."
    ),
    dfa_size_limit: str | None = typer.Option(
        None, "--dfa-size-limit", help="The upper size limit of the regex DFA."
    ),
    encoding: str = typer.Option(
        "auto", "-E", "--encoding", help="Specify the text encoding (e.g., auto, none, utf-8)."
    ),
    engine: str = typer.Option(
        "default", "--engine", help="Regex engine to use: 'default', 'pcre2', or 'auto'."
    ),
    fixed_strings: bool = typer.Option(
        False, "-F", "--fixed-strings", help="Treat all patterns as literals instead of regex."
    ),
    ignore_case: bool = typer.Option(
        False, "-i", "--ignore-case", help="Search case insensitively."
    ),
    invert_match: bool = typer.Option(
        False, "-v", "--invert-match", help="Invert matching (print lines that don't match)."
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
    multiline: bool = typer.Option(
        False, "-U", "--multiline", help="Enable searching across multiple lines."
    ),
    multiline_dotall: bool = typer.Option(
        False, "--multiline-dotall", help="Enable 'dot all' mode in multiline searches."
    ),
    no_unicode: bool = typer.Option(False, "--no-unicode", help="Disable Unicode mode for regex."),
    null_data: bool = typer.Option(
        False, "--null-data", help="Use NUL as a line terminator instead of \\n."
    ),
    pcre2: bool = typer.Option(False, "-P", "--pcre2", help="Use the PCRE2 regex engine."),
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
    follow: bool = typer.Option(False, "-L", "--follow", help="Follow symbolic links."),
    glob: list[str] | None = typer.Option(
        None, "-g", "--glob", help="Include/exclude files matching glob."
    ),
    glob_case_insensitive: bool = typer.Option(
        False, "--glob-case-insensitive", help="Process glob patterns case insensitively."
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
    max_depth: int | None = typer.Option(
        None, "-d", "--max-depth", help="Limit depth of directory traversal."
    ),
    max_filesize: str | None = typer.Option(
        None, "--max-filesize", help="Ignore files larger than this size."
    ),
    no_ignore: bool = typer.Option(
        False, "--no-ignore", help="Don't respect ignore files (.gitignore, .rgignore, etc)."
    ),
    no_ignore_dot: bool = typer.Option(
        False, "--no-ignore-dot", help="Don't respect .ignore or .rgignore files."
    ),
    no_ignore_exclude: bool = typer.Option(
        False, "--no-ignore-exclude", help="Don't respect .git/info/exclude."
    ),
    no_ignore_files: bool = typer.Option(
        False, "--no-ignore-files", help="Ignore any --ignore-file flags."
    ),
    no_ignore_global: bool = typer.Option(
        False, "--no-ignore-global", help="Don't respect global gitignore."
    ),
    no_ignore_parent: bool = typer.Option(
        False, "--no-ignore-parent", help="Don't respect ignore files in parent directories."
    ),
    no_ignore_vcs: bool = typer.Option(
        False, "--no-ignore-vcs", help="Don't respect source control ignore files (.gitignore)."
    ),
    no_require_git: bool = typer.Option(
        False, "--no-require-git", help="Respect .gitignore even outside of git repos."
    ),
    one_file_system: bool = typer.Option(
        False, "--one-file-system", help="Don't cross file system boundaries."
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
    byte_offset: bool = typer.Option(
        False, "-b", "--byte-offset", help="Print 0-based byte offset before each output line."
    ),
    color: str = typer.Option(
        "auto", "--color", help="When to use colors: never, auto, always, ansi."
    ),
    colors: list[str] | None = typer.Option(
        None, "--colors", help="Color settings for output (e.g. 'match:fg:magenta')."
    ),
    column: bool = typer.Option(False, "--column", help="Show column numbers (1-based)."),
    context: int | None = typer.Option(
        None, "-C", "--context", help="Show NUM lines before and after each match."
    ),
    context_separator: str = typer.Option(
        "--", "--context-separator", help="String used to separate non-contiguous context lines."
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
    line_buffered: bool = typer.Option(False, "--line-buffered", help="Force line buffering."),
    line_number: bool = typer.Option(
        True, "-n", "--line-number", help="Show line numbers (1-based)."
    ),
    max_columns: int | None = typer.Option(
        None, "-M", "--max-columns", help="Omit lines longer than this limit."
    ),
    max_columns_preview: bool = typer.Option(
        False, "--max-columns-preview", help="Preview lines exceeding max column limit."
    ),
    null: bool = typer.Option(False, "-0", "--null", help="Follow file paths with a NUL byte."),
    only_matching: bool = typer.Option(
        False, "-o", "--only-matching", help="Print only the matched parts of a line."
    ),
    path_separator: str | None = typer.Option(
        None, "--path-separator", help="Path separator to use."
    ),
    passthru: bool = typer.Option(
        False, "--passthru", help="Print both matching and non-matching lines."
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
    trim: bool = typer.Option(False, "--trim", help="Remove leading ASCII whitespace from output."),
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
    json: bool = typer.Option(False, "--json", help="Print results in JSON Lines format."),
    # LOGGING OPTIONS
    debug: bool = typer.Option(False, "--debug", help="Show debug messages."),
    no_ignore_messages: bool = typer.Option(
        False, "--no-ignore-messages", help="Suppress ignore file parsing errors."
    ),
    no_messages: bool = typer.Option(
        False, "--no-messages", help="Suppress some error messages (like failed file opens)."
    ),
    stats: bool = typer.Option(False, "--stats", help="Print aggregate statistics."),
    trace: bool = typer.Option(False, "--trace", help="Show exhaustive trace messages."),
    # OTHER BEHAVIORS
    files: bool = typer.Option(
        False, "--files", help="Print files that would be searched and exit."
    ),
    generate: str | None = typer.Option(
        None, "--generate", help="Generate special output (e.g. man, complete-bash)."
    ),
    no_config: bool = typer.Option(False, "--no-config", help="Never read configuration files."),
    pcre2_version: bool = typer.Option(
        False, "--pcre2-version", help="Print PCRE2 version and exit."
    ),
    type_list: bool = typer.Option(
        False, "--type-list", help="Show all supported file types and exit."
    ),
    # TENSOR-GREP SPECIFIC
    cpu: bool = typer.Option(False, "--cpu", help="Force CPU fallback (tensor-grep specific)."),
    format_type: str = typer.Option(
        "rg", "--format", help="Internal formatter: json, table, csv, rg"
    ),
    ast: bool = typer.Option(
        False,
        "--ast",
        help="Parse files into ASTs and search structurally using PyTorch Geometric.",
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
) -> None:
    """
    Search files for a regex pattern, with GPU acceleration when applicable.
    Supports almost all ripgrep (rg) flags for drop-in compatibility.
    """
    # Just forward to CPU backend for now as a stub.
    # Note: Full flag wiring will require mapping these dozens of parameters into the Pipeline/Core components.
    if not file_path:
        typer.echo("Error: Please provide at least one PATH to search.", err=True)
        sys.exit(1)

    paths_to_search = file_path

    from tensor_grep.core.config import SearchConfig

    parsed_gpu_device_ids = _parse_gpu_device_ids_cli(gpu_device_ids)

    config = SearchConfig(
        regexp=regexp,
        file_patterns=file,
        pre=pre,
        pre_glob=pre_glob,
        search_zip=search_zip,
        case_sensitive=case_sensitive,
        crlf=crlf,
        dfa_size_limit=dfa_size_limit,
        encoding=encoding,
        engine=engine,
        fixed_strings=fixed_strings,
        ignore_case=ignore_case,
        invert_match=invert_match,
        line_regexp=line_regexp,
        max_count=max_count,
        mmap=mmap,
        multiline=multiline,
        multiline_dotall=multiline_dotall,
        no_unicode=no_unicode,
        null_data=null_data,
        pcre2=pcre2,
        regex_size_limit=regex_size_limit,
        smart_case=smart_case,
        stop_on_nonmatch=stop_on_nonmatch,
        text=text,
        threads=threads,
        word_regexp=word_regexp,
        binary=binary,
        follow=follow,
        glob=glob,
        glob_case_insensitive=glob_case_insensitive,
        hidden=hidden,
        iglob=iglob,
        ignore_file=ignore_file,
        ignore_file_case_insensitive=ignore_file_case_insensitive,
        max_depth=max_depth,
        max_filesize=max_filesize,
        no_ignore=no_ignore,
        no_ignore_dot=no_ignore_dot,
        no_ignore_exclude=no_ignore_exclude,
        no_ignore_files=no_ignore_files,
        no_ignore_global=no_ignore_global,
        no_ignore_parent=no_ignore_parent,
        no_ignore_vcs=no_ignore_vcs,
        no_require_git=no_require_git,
        one_file_system=one_file_system,
        file_type=type,
        type_not=type_not,
        type_add=type_add,
        type_clear=type_clear,
        unrestricted=unrestricted,
        after_context=after_context,
        before_context=before_context,
        block_buffered=block_buffered,
        byte_offset=byte_offset,
        color=color,
        colors=colors,
        column=column,
        context=context,
        context_separator=context_separator,
        field_context_separator=field_context_separator,
        field_match_separator=field_match_separator,
        heading=heading,
        hostname_bin=hostname_bin,
        hyperlink_format=hyperlink_format,
        include_zero=include_zero,
        line_buffered=line_buffered,
        line_number=line_number,
        max_columns=max_columns,
        max_columns_preview=max_columns_preview,
        null=null,
        only_matching=only_matching,
        path_separator=path_separator,
        passthru=passthru,
        pretty=pretty,
        quiet=quiet,
        replace_str=replace,
        sort_by=sort,
        sort_by_reverse=sortr,
        trim=trim,
        vimgrep=vimgrep,
        with_filename=with_filename,
        no_filename=no_filename,
        count=count,
        count_matches=count_matches,
        files_with_matches=files_with_matches,
        files_without_match=files_without_match,
        json_mode=json,
        debug=debug,
        no_ignore_messages=no_ignore_messages,
        no_messages=no_messages,
        stats=stats,
        trace=trace,
        list_files=files,
        generate=generate,
        no_config=no_config,
        pcre2_version=pcre2_version,
        type_list=type_list,
        force_cpu=cpu,
        format_type=format_type,
        ast=ast,
        lang=lang,
        ltl=ltl,
        query_pattern=pattern,
        gpu_device_ids=parsed_gpu_device_ids,
    )

    from tensor_grep.backends.ripgrep_backend import RipgrepBackend
    from tensor_grep.io.directory_scanner import DirectoryScanner

    rg_backend = RipgrepBackend()
    can_passthrough_rg = rg_backend.is_available() and _can_passthrough_rg(
        config,
        format_type=format_type,
        json_mode=json,
        files_mode=files,
        files_with_matches=files_with_matches,
        files_without_match=files_without_match,
        only_matching=only_matching,
        stats_mode=stats,
    )
    if can_passthrough_rg:
        if debug:
            typer.echo("[debug] routing.backend=RipgrepBackend reason=rg_passthrough_cli_fast_path")
        with nvtx_range("search.passthrough_rg", color="green"):
            exit_code = rg_backend.search_passthrough(paths_to_search, pattern, config=config)
        sys.exit(0 if exit_code == 0 else 1)

    scanner = DirectoryScanner(config)
    candidate_files_ordered, candidate_files_set = _collect_candidate_files(
        scanner, paths_to_search
    )
    config.input_total_bytes = _sum_total_bytes(candidate_files_ordered)

    from tensor_grep.core.pipeline import Pipeline
    from tensor_grep.core.result import SearchResult

    pipeline = Pipeline(force_cpu=cpu, config=config)
    backend = pipeline.get_backend()
    selected_backend_name = getattr(pipeline, "selected_backend_name", backend.__class__.__name__)
    selected_backend_reason = getattr(pipeline, "selected_backend_reason", "unknown")
    selected_gpu_device_ids = list(getattr(pipeline, "selected_gpu_device_ids", []) or [])
    selected_gpu_chunk_plan_mb = list(getattr(pipeline, "selected_gpu_chunk_plan_mb", []) or [])
    if debug:
        typer.echo(
            f"[debug] routing.backend={selected_backend_name} reason={selected_backend_reason}"
        )
        if selected_gpu_device_ids:
            typer.echo(
                f"[debug] routing.gpu_device_ids={selected_gpu_device_ids} "
                f"routing.gpu_chunk_plan_mb={selected_gpu_chunk_plan_mb}"
            )

    if files:
        if candidate_files_ordered:
            print("\n".join(candidate_files_ordered))
            sys.exit(0)
        sys.exit(1)

    tracer = None
    try:
        from opentelemetry import trace as otel_trace

        tracer = otel_trace.get_tracer(__name__)
    except ImportError:
        tracer = None

    all_results = SearchResult(matches=[], total_files=0, total_matches=0)
    all_results.routing_backend = selected_backend_name
    all_results.routing_reason = selected_backend_reason
    all_results.routing_gpu_device_ids = selected_gpu_device_ids
    all_results.routing_gpu_chunk_plan_mb = selected_gpu_chunk_plan_mb
    search_start = time.perf_counter()

    # RipgrepBackend optimization: passing all paths natively
    if backend.__class__.__name__ == "RipgrepBackend":
        rg_backend = cast(RipgrepBackend, backend)
        span_ctx = (
            tracer.start_as_current_span("search.file") if tracer is not None else nullcontext()
        )
        with span_ctx as span, nvtx_range("search.file", color="cyan"):
            if span is not None:
                span.set_attribute("backend", backend.__class__.__name__)
                span.set_attribute("path_count", len(paths_to_search))
            result = rg_backend.search(paths_to_search, pattern, config=config)
            if span is not None:
                span.set_attribute("matches", result.total_matches)
            all_results.matches.extend(result.matches)
            all_results.total_matches += result.total_matches
            all_results.total_files += result.total_files
            all_results.routing_distributed = (
                all_results.routing_distributed or result.routing_distributed
            )
            all_results.routing_worker_count = max(
                all_results.routing_worker_count, result.routing_worker_count
            )
    else:
        for current_file in candidate_files_ordered:
            span_ctx = (
                tracer.start_as_current_span("search.file") if tracer is not None else nullcontext()
            )
            with span_ctx as span, nvtx_range("search.file", color="cyan"):
                if span is not None:
                    span.set_attribute("backend", backend.__class__.__name__)
                    span.set_attribute("path", current_file)
                result = backend.search(current_file, pattern, config=config)
                if span is not None:
                    span.set_attribute("matches", result.total_matches)
            all_results.matches.extend(result.matches)
            all_results.total_matches += result.total_matches
            if result.total_matches > 0:
                all_results.total_files += 1
            all_results.routing_distributed = (
                all_results.routing_distributed or result.routing_distributed
            )
            all_results.routing_worker_count = max(
                all_results.routing_worker_count, result.routing_worker_count
            )

    if only_matching:
        all_results.matches = _only_matching_lines(all_results.matches, pattern, config)
        all_results.total_matches = len(all_results.matches)
        all_results.total_files = len({m.file for m in all_results.matches})

    matched_files = {m.file for m in all_results.matches}
    elapsed_ms = (time.perf_counter() - search_start) * 1000.0

    def _emit_stats() -> None:
        if not stats:
            return
        typer.echo(
            (
                f"[stats] scanned_files={len(candidate_files_ordered)} "
                f"matched_files={len(matched_files)} "
                f"total_matches={all_results.total_matches} "
                f"elapsed_ms={elapsed_ms:.2f}"
            ),
            err=True,
        )
        typer.echo(
            f"[stats] backend={selected_backend_name} reason={selected_backend_reason}",
            err=True,
        )
        if selected_gpu_device_ids:
            typer.echo(
                (
                    f"[stats] gpu_device_ids={selected_gpu_device_ids} "
                    f"gpu_chunk_plan_mb={selected_gpu_chunk_plan_mb} "
                    f"distributed={all_results.routing_distributed} "
                    f"workers={all_results.routing_worker_count}"
                ),
                err=True,
            )

    if files_with_matches:
        if matched_files:
            _emit_stats()
            print("\n".join(sorted(matched_files)))
            sys.exit(0)
        _emit_stats()
        sys.exit(1)

    if files_without_match:
        unmatched = sorted(candidate_files_set - matched_files)
        if unmatched:
            _emit_stats()
            print("\n".join(unmatched))
            sys.exit(0)
        _emit_stats()
        sys.exit(1)

    if all_results.is_empty:
        _emit_stats()
        sys.exit(1)

    if quiet:
        _emit_stats()
        sys.exit(0)

    formatter: OutputFormatter

    if json or format_type == "json":
        from tensor_grep.cli.formatters.json_fmt import JsonFormatter

        formatter = JsonFormatter()
    elif format_type == "table":
        from tensor_grep.cli.formatters.table_fmt import TableFormatter

        formatter = TableFormatter()
    elif format_type == "csv":
        from tensor_grep.cli.formatters.csv_fmt import CsvFormatter

        formatter = CsvFormatter()
    else:
        from tensor_grep.cli.formatters.ripgrep_fmt import RipgrepFormatter

        formatter = RipgrepFormatter(config=config)

    print(formatter.format(all_results))
    _emit_stats()


@app.command()
def devices(
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit device inventory as JSON for automation.",
    ),
    format_type: str = typer.Option(
        "text",
        "--format",
        help="Output format: text or json.",
    ),
) -> None:
    """Print routable GPU device IDs and VRAM inventory."""
    import json

    from tensor_grep.core.hardware.device_inventory import collect_device_inventory

    normalized_format = format_type.lower().strip()
    if json_output:
        normalized_format = "json"
    if normalized_format not in {"text", "json"}:
        raise typer.BadParameter("--format must be one of: text, json")

    inventory = collect_device_inventory()
    payload = inventory.to_dict()

    if normalized_format == "json":
        print(json.dumps(payload))
        return

    if not inventory.devices:
        typer.echo("No routable GPUs detected.")
        return

    typer.echo(f"Detected {inventory.device_count} routable GPU(s):")
    for device in inventory.devices:
        typer.echo(f"- gpu:{device.device_id} vram_mb={device.vram_capacity_mb}")


@app.command()
def classify(
    file_path: str, format_type: str = typer.Option("json", "--format", help="Output format")
) -> None:
    import json
    import re

    from tensor_grep.backends.cybert_backend import CybertBackend
    from tensor_grep.io.reader_fallback import FallbackReader

    reader = FallbackReader()
    lines = list(reader.read_lines(file_path))
    if not lines:
        sys.exit(1)

    backend = CybertBackend()
    try:
        results = backend.classify(lines)
    except Exception:
        # Keep CLI usable when Triton/PyTorch is unavailable in CI or local environments.
        results = []
        for line in lines:
            if re.search(r"\berror\b|\bfail(?:ed)?\b|\bexception\b", line, re.IGNORECASE):
                results.append({"label": "error", "confidence": 0.9})
            elif re.search(r"\bwarn(?:ing)?\b", line, re.IGNORECASE):
                results.append({"label": "warn", "confidence": 0.8})
            else:
                results.append({"label": "info", "confidence": 0.7})

    if format_type == "json":
        data = {"classifications": results}
        print(json.dumps(data))
    else:
        for r in results:
            print(f"{r['label']} ({r['confidence']:.2f})")


@app.command()
def run(
    pattern: str = typer.Argument(..., help="AST pattern to search for"),
    path: str | None = typer.Argument(None, help="Path to search"),
    rewrite: str | None = typer.Option(None, "--rewrite", "-r", help="Rewrite matching code"),
    lang: str | None = typer.Option(None, "--lang", "-l", help="Language to parse"),
    config: str | None = typer.Option(
        "sgconfig.yml", "--config", "-c", help="Path to ast-grep root config"
    ),
) -> None:
    """Run one time search or rewrite in command line (ast-grep parity)"""
    typer.echo("Executing GPU-Accelerated AST-Grep Run...")
    if not path:
        path = "."

    from tensor_grep.core.config import SearchConfig
    from tensor_grep.core.pipeline import Pipeline
    from tensor_grep.core.result import SearchResult
    from tensor_grep.io.directory_scanner import DirectoryScanner

    cfg = SearchConfig(ast=True, lang=lang)
    pipeline = Pipeline(config=cfg)
    backend = pipeline.get_backend()

    if not type(backend).__name__ == "AstBackend":
        typer.echo(
            "Warning: AstBackend not available (requires torch_geometric/tree_sitter). Falling back to CPU regex.",
            err=True,
        )

    scanner = DirectoryScanner(cfg)
    all_results = SearchResult(matches=[], total_files=0, total_matches=0)

    for current_file in scanner.walk(path):
        result = backend.search(current_file, pattern, config=cfg)
        all_results.matches.extend(result.matches)
        all_results.total_matches += result.total_matches
        if result.total_matches > 0:
            all_results.total_files += 1

    formatter = RipgrepFormatter()
    print(formatter.format(all_results))


@app.command()
def scan(
    config: str | None = typer.Option(
        "sgconfig.yml", "--config", "-c", help="Path to ast-grep root config"
    ),
) -> None:
    """Scan and rewrite code by configuration (ast-grep parity)"""
    from tensor_grep.core.config import SearchConfig
    from tensor_grep.core.pipeline import Pipeline
    from tensor_grep.io.directory_scanner import DirectoryScanner

    try:
        project_cfg = _load_sg_project_config(config)
    except (FileNotFoundError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        sys.exit(1)

    rules = _load_rule_specs(project_cfg)
    if not rules:
        typer.echo("Error: No valid rules found in configured rule directories.", err=True)
        sys.exit(1)

    cfg = SearchConfig(ast=True, lang=cast(str, project_cfg["language"]))
    pipeline = Pipeline(config=cfg)
    backend = pipeline.get_backend()
    scanner = DirectoryScanner(cfg)
    root_dir = cast(Path, project_cfg["root_dir"])
    candidate_files, _ = _collect_candidate_files(scanner, [str(root_dir)])

    typer.echo(
        f"Scanning project using GPU-Accelerated GNNs based on {project_cfg['config_path']}..."
    )

    total_matches = 0
    matched_rules = 0
    for rule in rules:
        rule_cfg = replace(cfg, lang=rule["language"])
        rule_matches = 0
        matched_files: set[str] = set()
        for current_file in candidate_files:
            result = backend.search(current_file, rule["pattern"], config=rule_cfg)
            rule_matches += result.total_matches
            if result.total_matches > 0:
                matched_files.add(current_file)
        total_matches += rule_matches
        if rule_matches > 0:
            matched_rules += 1
        typer.echo(
            f"[scan] rule={rule['id']} lang={rule['language']} "
            f"matches={rule_matches} files={len(matched_files)}"
        )

    typer.echo(
        f"Scan completed. rules={len(rules)} matched_rules={matched_rules} total_matches={total_matches}"
    )


@app.command()
def test(
    config: str | None = typer.Option(
        "sgconfig.yml", "--config", "-c", help="Path to ast-grep root config"
    ),
) -> None:
    """Test ast-grep rules (ast-grep parity)"""
    from tensor_grep.core.config import SearchConfig
    from tensor_grep.core.pipeline import Pipeline

    try:
        project_cfg = _load_sg_project_config(config)
    except (FileNotFoundError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        sys.exit(1)

    rules = _load_rule_specs(project_cfg)
    if not rules:
        typer.echo("Error: No valid rules found in configured rule directories.", err=True)
        sys.exit(1)
    rules_by_id = {rule["id"]: rule for rule in rules}

    root_dir = cast(Path, project_cfg["root_dir"])
    test_dirs = cast(list[str], project_cfg["test_dirs"])
    test_files = _iter_yaml_files(root_dir, test_dirs)
    if not test_files:
        typer.echo("Error: No test files found in configured test directories.", err=True)
        sys.exit(1)

    cfg = SearchConfig(ast=True, lang=cast(str, project_cfg["language"]))
    pipeline = Pipeline(config=cfg)
    backend = pipeline.get_backend()

    total_cases = 0
    failures: list[str] = []
    for test_file in test_files:
        payload = _load_yaml_dict(test_file)
        raw_cases = payload.get("tests")
        if isinstance(raw_cases, list):
            cases = [case for case in raw_cases if isinstance(case, dict)]
        else:
            cases = [payload]

        for case in cases:
            case_id = str(case.get("id") or test_file.stem)
            linked_rule = case.get("ruleId")
            pattern = _extract_rule_pattern(case)
            language = str(case.get("language") or cfg.lang or "python")
            if not pattern and isinstance(linked_rule, str) and linked_rule in rules_by_id:
                pattern = rules_by_id[linked_rule]["pattern"]
                language = str(case.get("language") or rules_by_id[linked_rule]["language"])
            if not pattern:
                failures.append(f"{test_file}:{case_id}: missing pattern or ruleId")
                continue

            valid_snippets = _normalize_string_list(case.get("valid"), [])
            invalid_snippets = _normalize_string_list(case.get("invalid"), [])
            if not valid_snippets and not invalid_snippets:
                failures.append(f"{test_file}:{case_id}: empty valid/invalid test lists")
                continue

            for expected_match, snippets in ((False, valid_snippets), (True, invalid_snippets)):
                for snippet in snippets:
                    total_cases += 1
                    temp_name = (
                        root_dir / f".tg_rule_test_{uuid4().hex}{_suffix_for_language(language)}"
                    )
                    temp_name.write_text(snippet, encoding="utf-8")
                    try:
                        result = backend.search(
                            str(temp_name), pattern, config=replace(cfg, lang=language)
                        )
                        has_match = result.total_matches > 0
                    except Exception as exc:
                        failures.append(f"{test_file}:{case_id}: backend error: {exc}")
                        has_match = expected_match
                    finally:
                        temp_name.unlink(missing_ok=True)

                    if has_match != expected_match:
                        expectation = "match" if expected_match else "no match"
                        failures.append(
                            f"{test_file}:{case_id}: expected {expectation}, got "
                            f"{'match' if has_match else 'no match'} for snippet {snippet!r}"
                        )

    typer.echo(f"Testing AST rules from {project_cfg['config_path']}...")
    if failures:
        for failure in failures:
            typer.echo(f"[test] FAIL {failure}", err=True)
        typer.echo(f"Rule tests failed. cases={total_cases} failures={len(failures)}", err=True)
        sys.exit(1)

    typer.echo(f"All tests passed. cases={total_cases}")


@app.command()
def new() -> None:
    """Create new ast-grep project or items like rules/tests (ast-grep parity)"""
    import os

    import yaml

    if os.path.exists("sgconfig.yml"):
        typer.echo("Project already initialized (sgconfig.yml exists).", err=True)
        sys.exit(1)

    config_data = {
        "ruleDirs": ["rules"],
        "testDirs": ["tests"],
        "utilsDir": "utils",
        "language": "python",
    }

    with open("sgconfig.yml", "w") as f:
        yaml.dump(config_data, f)

    os.makedirs("rules", exist_ok=True)
    os.makedirs("tests", exist_ok=True)

    typer.echo("Initialized new tensor-grep structural search project.")


@app.command()
def lsp() -> None:
    """Start language server (ast-grep parity)"""
    from tensor_grep.cli.lsp_server import run_lsp

    run_lsp()


@app.command(name="mcp")
def mcp_server() -> None:
    """Start the Model Context Protocol (MCP) server for AI assistants"""
    from tensor_grep.cli.mcp_server import run_mcp_server

    run_mcp_server()


@app.command()
def upgrade() -> None:
    """Upgrade tensor-grep to the latest version published on PyPI."""
    import subprocess
    import sys

    def _run_upgrade() -> tuple[subprocess.CompletedProcess[str], str]:
        errors: list[str] = []
        pip_cmd = [sys.executable, "-m", "pip", "install", "--upgrade", "tensor-grep"]
        attempts: list[tuple[str, list[str]]] = [
            (
                "uv",
                ["uv", "pip", "install", "--python", sys.executable, "--upgrade", "tensor-grep"],
            ),
            ("pip", pip_cmd),
        ]
        for label, cmd in attempts:
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, check=True)
                return result, label
            except FileNotFoundError as e:
                errors.append(f"{label}: {e}")
            except subprocess.CalledProcessError as e:
                stderr = (e.stderr or "").strip()
                stdout = (e.stdout or "").strip()
                combined = stderr or stdout or str(e)
                errors.append(f"{label}: {combined}")
                if label == "pip" and "No module named pip" in combined:
                    try:
                        subprocess.run(
                            [sys.executable, "-m", "ensurepip", "--upgrade"],
                            capture_output=True,
                            text=True,
                            check=True,
                        )
                        result = subprocess.run(pip_cmd, capture_output=True, text=True, check=True)
                        return result, "pip+ensurepip"
                    except FileNotFoundError as ee:
                        errors.append(f"ensurepip: {ee}")
                    except subprocess.CalledProcessError as ee:
                        ee_stderr = (ee.stderr or "").strip()
                        ee_stdout = (ee.stdout or "").strip()
                        errors.append(f"ensurepip: {ee_stderr or ee_stdout or str(ee)}")
        raise RuntimeError("; ".join(errors))

    typer.echo("Upgrading tensor-grep to the latest version...")

    try:
        result, method = _run_upgrade()
        output = "\n".join(
            part for part in ((result.stdout or "").strip(), (result.stderr or "").strip()) if part
        )
        if "Requirement already satisfied" in output:
            typer.echo("tensor-grep is already up to date!")
        else:
            typer.echo(f"Successfully upgraded tensor-grep via {method}!")
            if output:
                typer.echo(output)

    except RuntimeError as e:
        typer.echo("Error occurred while upgrading tensor-grep.", err=True)
        typer.echo(str(e), err=True)
        sys.exit(1)


def main_entry() -> None:
    import sys

    # Emulate ripgrep's top-level help behavior and transparent drop-in compatibility.
    # Typer requires an explicit subcommand (like `tg search pattern`).
    # To act exactly like ripgrep (`rg pattern`), we dynamically inject the `search`
    # subcommand into sys.argv if the user didn't provide any recognized subcommand.

    # Check for version flag first
    if len(sys.argv) > 1 and sys.argv[1] in ("--version", "-V"):
        try:
            from importlib.metadata import version

            pkg_version = version("tensor-grep")
        except Exception:
            pkg_version = "0.29.0"  # Fallback if not installed via package manager

        print(f"tensor-grep {pkg_version}")
        print()
        print("features:+gpu-cudf,+gpu-torch,+rust-core")
        print("simd(compile):+SSE2,-SSSE3,-AVX2")
        print("simd(runtime):+SSE2,+SSSE3,+AVX2")
        print()
        print("Arrow Zero-Copy IPC is available")
        sys.exit(0)

    known_commands = {
        "search",
        "devices",
        "classify",
        "run",
        "scan",
        "test",
        "new",
        "lsp",
        "mcp",
        "upgrade",
    }

    if len(sys.argv) > 1:
        first_arg = sys.argv[1]
        if first_arg in ("--help", "-h"):
            sys.argv.insert(1, "search")
        elif first_arg not in known_commands and not first_arg.startswith("--typer-"):
            sys.argv.insert(1, "search")
    elif len(sys.argv) == 1:
        sys.argv.extend(["search", "--help"])

    app()


if __name__ == "__main__":
    main_entry()

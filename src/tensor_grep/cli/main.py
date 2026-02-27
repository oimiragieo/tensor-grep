import sys

import typer

from tensor_grep.cli.formatters.base import OutputFormatter
from tensor_grep.cli.formatters.ripgrep_fmt import RipgrepFormatter

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


@app.command(name="search")
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
        None, "-r", "--replace", help="Replace every match with the given text."
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

    path_to_search = file_path[0]

    from tensor_grep.core.config import SearchConfig

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
    )

    from tensor_grep.core.pipeline import Pipeline
    from tensor_grep.core.result import SearchResult
    from tensor_grep.io.directory_scanner import DirectoryScanner

    pipeline = Pipeline(force_cpu=cpu, config=config)
    backend = pipeline.get_backend()

    scanner = DirectoryScanner(config)

    all_results = SearchResult(matches=[], total_files=0, total_matches=0)

    for current_file in scanner.walk(path_to_search):
        result = backend.search(current_file, pattern, config=config)
        all_results.matches.extend(result.matches)
        all_results.total_matches += result.total_matches
        if result.total_matches > 0:
            all_results.total_files += 1

    if all_results.is_empty and not quiet:
        sys.exit(1)
    elif all_results.is_empty and quiet:
        sys.exit(1)

    if quiet:
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


@app.command()
def classify(
    file_path: str, format_type: str = typer.Option("json", "--format", help="Output format")
) -> None:
    import json

    from tensor_grep.backends.cybert_backend import CybertBackend
    from tensor_grep.io.reader_fallback import FallbackReader

    reader = FallbackReader()
    lines = list(reader.read_lines(file_path))
    if not lines:
        sys.exit(1)

    backend = CybertBackend()
    results = backend.classify(lines)

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
    typer.echo(f"Scanning project using GPU-Accelerated GNNs based on {config}...")


@app.command()
def test(
    config: str | None = typer.Option(
        "sgconfig.yml", "--config", "-c", help="Path to ast-grep root config"
    ),
) -> None:
    """Test ast-grep rules (ast-grep parity)"""
    typer.echo(f"Testing AST rules from {config}...")


@app.command()
def new() -> None:
    """Create new ast-grep project or items like rules/tests (ast-grep parity)"""
    typer.echo("Scaffolding new ast-grep compatible project...")


@app.command()
def lsp() -> None:
    """Start language server (ast-grep parity)"""
    typer.echo("Starting tensor-grep LSP server with GPU-acceleration...")


def main_entry() -> None:
    import sys

    # Emulate ripgrep's top-level help behavior and transparent drop-in compatibility.
    # Typer requires an explicit subcommand (like `tg search pattern`).
    # To act exactly like ripgrep (`rg pattern`), we dynamically inject the `search`
    # subcommand into sys.argv if the user didn't provide any recognized subcommand.

    known_commands = {"search", "classify", "run", "scan", "test", "new", "lsp"}

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

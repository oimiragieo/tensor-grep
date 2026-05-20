from dataclasses import dataclass


@dataclass
class SearchConfig:
    # INPUT OPTIONS
    regexp: list[str] | None = None
    file_patterns: list[str] | None = None
    pre: str | None = None
    no_pre: bool = False
    pre_glob: list[str] | None = None
    search_zip: bool = False
    no_search_zip: bool = False

    # SEARCH OPTIONS
    case_sensitive: bool = False
    crlf: bool = False
    no_crlf: bool = False
    dfa_size_limit: str | None = None
    encoding: str = "auto"
    no_encoding: bool = False
    engine: str = "default"
    fixed_strings: bool = False
    no_fixed_strings: bool = False
    ignore_case: bool = False
    invert_match: bool = False
    no_invert_match: bool = False
    line_regexp: bool = False
    max_count: int | None = None
    mmap: bool = True
    no_mmap: bool = False
    multiline: bool = False
    no_multiline: bool = False
    multiline_dotall: bool = False
    no_multiline_dotall: bool = False
    auto_hybrid_regex: bool = False
    no_auto_hybrid_regex: bool = False
    no_unicode: bool = False
    unicode: bool = False
    pcre2_unicode: bool = False
    no_pcre2_unicode: bool = False
    null_data: bool = False
    pcre2: bool = False
    no_pcre2: bool = False
    regex_size_limit: str | None = None
    smart_case: bool = False
    stop_on_nonmatch: bool = False
    text: bool = False
    no_text: bool = False
    threads: int = 0
    word_regexp: bool = False

    # FILTER OPTIONS
    binary: bool = False
    no_binary: bool = False
    follow: bool = False
    no_follow: bool = False
    glob: list[str] | None = None
    glob_case_insensitive: bool = False
    no_glob_case_insensitive: bool = False
    hidden: bool = False
    iglob: list[str] | None = None
    ignore_file: list[str] | None = None
    ignore_file_case_insensitive: bool = False
    no_ignore_file_case_insensitive: bool = False
    max_depth: int | None = None
    max_filesize: str | None = None
    ignore: bool = False
    no_ignore: bool = False
    ignore_dot: bool = False
    no_ignore_dot: bool = False
    ignore_exclude: bool = False
    no_ignore_exclude: bool = False
    ignore_files: bool = False
    no_ignore_files: bool = False
    ignore_global: bool = False
    no_ignore_global: bool = False
    ignore_parent: bool = False
    no_ignore_parent: bool = False
    ignore_vcs: bool = False
    no_ignore_vcs: bool = False
    no_require_git: bool = False
    require_git: bool = False
    no_hidden: bool = False
    one_file_system: bool = False
    no_one_file_system: bool = False
    file_type: list[str] | None = None
    type_not: list[str] | None = None
    type_add: list[str] | None = None
    type_clear: str | None = None
    unrestricted: int = 0

    # OUTPUT OPTIONS
    after_context: int | None = None
    before_context: int | None = None
    block_buffered: bool = False
    no_block_buffered: bool = False
    byte_offset: bool = False
    no_byte_offset: bool = False
    color: str = "auto"
    colors: list[str] | None = None
    column: bool = False
    no_column: bool = False
    context: int | None = None
    context_separator: str = "--"
    no_context_separator: bool = False
    field_context_separator: str = "-"
    field_match_separator: str = ":"
    heading: bool = True
    hostname_bin: str | None = None
    hyperlink_format: str | None = None
    include_zero: bool = False
    no_include_zero: bool = False
    line_buffered: bool = False
    no_line_buffered: bool = False
    line_number: bool = True
    max_columns: int | None = None
    max_columns_preview: bool = False
    no_max_columns_preview: bool = False
    null: bool = False
    only_matching: bool = False
    path_separator: str | None = None
    passthru: bool = False
    pretty: bool = False
    quiet: bool = False
    replace_str: str | None = None
    sort_by: str = "none"
    sort_by_reverse: str = "none"
    sort_files: bool = False
    trim: bool = False
    no_trim: bool = False
    vimgrep: bool = False
    with_filename: bool = False
    no_filename: bool = False

    # OUTPUT MODES
    count: bool = False
    count_matches: bool = False
    files_with_matches: bool = False
    files_without_match: bool = False
    json_mode: bool = False
    no_json: bool = False

    # LOGGING OPTIONS
    debug: bool = False
    ignore_messages: bool = False
    no_ignore_messages: bool = False
    no_messages: bool = False
    messages: bool = False
    stats: bool = False
    no_stats: bool = False
    trace: bool = False

    # OTHER BEHAVIORS
    list_files: bool = False
    generate: str | None = None
    no_config: bool = False
    pcre2_version: bool = False
    type_list: bool = False

    # TENSOR-GREP SPECIFIC
    force_cpu: bool = False
    format_type: str = "rg"
    nlp_threshold: float = 0.0
    ast: bool = False
    ast_prefer_native: bool = False
    lang: str | None = None
    ast_selector: str | None = None
    ast_strictness: str | None = None
    ast_stdin: bool = False
    ast_stdin_input: str | None = None
    use_jit: bool = False
    ltl: bool = False
    query_pattern: str | None = None
    input_total_bytes: int = 0
    gpu_device_ids: list[int] | None = None

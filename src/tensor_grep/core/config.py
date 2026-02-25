from dataclasses import dataclass, field
from typing import Optional, List

@dataclass
class SearchConfig:
    # INPUT OPTIONS
    regexp: Optional[List[str]] = None
    file_patterns: Optional[List[str]] = None
    pre: Optional[str] = None
    pre_glob: Optional[List[str]] = None
    search_zip: bool = False
    
    # SEARCH OPTIONS
    case_sensitive: bool = False
    crlf: bool = False
    dfa_size_limit: Optional[str] = None
    encoding: str = "auto"
    engine: str = "default"
    fixed_strings: bool = False
    ignore_case: bool = False
    invert_match: bool = False
    line_regexp: bool = False
    max_count: Optional[int] = None
    mmap: bool = True
    multiline: bool = False
    multiline_dotall: bool = False
    no_unicode: bool = False
    null_data: bool = False
    pcre2: bool = False
    regex_size_limit: Optional[str] = None
    smart_case: bool = False
    stop_on_nonmatch: bool = False
    text: bool = False
    threads: int = 0
    word_regexp: bool = False
    
    # FILTER OPTIONS
    binary: bool = False
    follow: bool = False
    glob: Optional[List[str]] = None
    glob_case_insensitive: bool = False
    hidden: bool = False
    iglob: Optional[List[str]] = None
    ignore_file: Optional[List[str]] = None
    ignore_file_case_insensitive: bool = False
    max_depth: Optional[int] = None
    max_filesize: Optional[str] = None
    no_ignore: bool = False
    no_ignore_dot: bool = False
    no_ignore_exclude: bool = False
    no_ignore_files: bool = False
    no_ignore_global: bool = False
    no_ignore_parent: bool = False
    no_ignore_vcs: bool = False
    no_require_git: bool = False
    one_file_system: bool = False
    file_type: Optional[List[str]] = None
    type_not: Optional[List[str]] = None
    type_add: Optional[List[str]] = None
    type_clear: Optional[str] = None
    unrestricted: int = 0
    
    # OUTPUT OPTIONS
    after_context: Optional[int] = None
    before_context: Optional[int] = None
    block_buffered: bool = False
    byte_offset: bool = False
    color: str = "auto"
    colors: Optional[List[str]] = None
    column: bool = False
    context: Optional[int] = None
    context_separator: str = "--"
    field_context_separator: str = "-"
    field_match_separator: str = ":"
    heading: bool = True
    hostname_bin: Optional[str] = None
    hyperlink_format: Optional[str] = None
    include_zero: bool = False
    line_buffered: bool = False
    line_number: bool = True
    max_columns: Optional[int] = None
    max_columns_preview: bool = False
    null: bool = False
    only_matching: bool = False
    path_separator: Optional[str] = None
    passthru: bool = False
    pretty: bool = False
    quiet: bool = False
    replace_str: Optional[str] = None
    sort_by: str = "none"
    sort_by_reverse: str = "none"
    trim: bool = False
    vimgrep: bool = False
    with_filename: bool = False
    no_filename: bool = False
    
    # OUTPUT MODES
    count: bool = False
    count_matches: bool = False
    files_with_matches: bool = False
    files_without_match: bool = False
    json_mode: bool = False
    
    # LOGGING OPTIONS
    debug: bool = False
    no_ignore_messages: bool = False
    no_messages: bool = False
    stats: bool = False
    trace: bool = False
    
    # OTHER BEHAVIORS
    list_files: bool = False
    generate: Optional[str] = None
    no_config: bool = False
    pcre2_version: bool = False
    type_list: bool = False
    
    # TENSOR-GREP SPECIFIC
    force_cpu: bool = False
    format_type: str = "rg"

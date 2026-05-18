use anyhow::{bail, Context};
use std::env;
use std::ffi::OsString;
use tensor_grep_rs::rg_passthrough::{execute_ripgrep_search, RipgrepSearchArgs};

fn main() -> anyhow::Result<()> {
    let args = parse_args(env::args_os().skip(1).collect())?;
    let exit_code = execute_ripgrep_search(&args)?;
    if exit_code != 0 {
        std::process::exit(exit_code.max(1));
    }
    Ok(())
}

fn parse_args(tokens: Vec<OsString>) -> anyhow::Result<RipgrepSearchArgs> {
    if tokens.is_empty() {
        bail!("tg-search-fast requires a pattern and path")
    }

    let mut args = RipgrepSearchArgs {
        files: false,
        json: false,
        ignore_case: false,
        fixed_strings: false,
        invert_match: false,
        count: false,
        count_matches: false,
        line_number: false,
        no_line_number: false,
        column: false,
        only_matching: false,
        context: None,
        before_context: None,
        after_context: None,
        max_count: None,
        word_regexp: false,
        smart_case: false,
        globs: Vec::new(),
        ignore: false,
        no_ignore: false,
        no_ignore_dot: false,
        no_ignore_exclude: false,
        no_ignore_files: false,
        no_ignore_global: false,
        no_ignore_parent: false,
        require_git: false,
        hidden: false,
        no_hidden: false,
        follow: false,
        text: false,
        files_with_matches: false,
        files_without_match: false,
        file_types: Vec::new(),
        color: None,
        path_separator: None,
        replace: None,
        vimgrep: false,
        passthru: false,
        no_config: false,
        sort: None,
        sort_reverse: None,
        sort_files: false,
        max_depth: None,
        null: false,
        null_data: false,
        multiline: false,
        multiline_dotall: false,
        patterns: Vec::new(),
        paths: Vec::new(),
        no_ignore_vcs: false,
        pcre2: false,
        pcre2_unicode: false,
        no_pcre2_unicode: false,
        auto_hybrid_regex: false,
        no_auto_hybrid_regex: false,
        unicode: false,
        no_text: false,
        no_binary: false,
        no_follow: false,
        no_glob_case_insensitive: false,
        no_ignore_file_case_insensitive: false,
        ignore_dot: false,
        ignore_exclude: false,
        ignore_files: false,
        ignore_global: false,
        ignore_messages: false,
        ignore_parent: false,
        ignore_vcs: false,
        no_one_file_system: false,
        no_block_buffered: false,
        no_byte_offset: false,
        no_column: false,
        no_context_separator: false,
        no_include_zero: false,
        no_line_buffered: false,
        no_max_columns_preview: false,
        no_trim: false,
        no_json: false,
        messages: false,
        no_stats: false,
        max_filesize: None,
    };

    let mut positionals: Vec<String> = Vec::new();
    let tokens = tokens
        .into_iter()
        .map(|arg| arg.to_string_lossy().to_string())
        .collect::<Vec<_>>();

    let mut index = 0usize;
    while index < tokens.len() {
        let token = &tokens[index];
        match token.as_str() {
            "-i" | "--ignore-case" => args.ignore_case = true,
            "-F" | "--fixed-strings" => args.fixed_strings = true,
            "-v" | "--invert-match" => args.invert_match = true,
            "-c" | "--count" => args.count = true,
            "--count-matches" => args.count_matches = true,
            "-n" | "--line-number" => {
                args.line_number = true;
                args.no_line_number = false;
            }
            "-N" | "--no-line-number" => {
                args.line_number = false;
                args.no_line_number = true;
            }
            "--column" => args.column = true,
            "-w" | "--word-regexp" => args.word_regexp = true,
            "-0" | "--null" => args.null = true,
            "--null-data" => args.null_data = true,
            "-U" | "--multiline" => args.multiline = true,
            "--multiline-dotall" => args.multiline_dotall = true,
            "-S" | "--smart-case" => args.smart_case = true,
            "--ignore" => {
                args.ignore = true;
                args.no_ignore = false;
            }
            "--no-ignore" => {
                args.ignore = false;
                args.no_ignore = true;
            }
            "--no-ignore-dot" => args.no_ignore_dot = true,
            "--no-ignore-exclude" => args.no_ignore_exclude = true,
            "--no-ignore-files" => args.no_ignore_files = true,
            "--no-ignore-global" => args.no_ignore_global = true,
            "--no-ignore-parent" => args.no_ignore_parent = true,
            "--require-git" => args.require_git = true,
            "--no-config" => args.no_config = true,
            "--passthru" => args.passthru = true,
            "--passthrough" => args.passthru = true,
            "--auto-hybrid-regex" => args.auto_hybrid_regex = true,
            "--no-auto-hybrid-regex" => args.no_auto_hybrid_regex = true,
            "--pcre2-unicode" => {
                args.pcre2_unicode = true;
            }
            "--no-pcre2-unicode" => args.no_pcre2_unicode = true,
            "--unicode" => args.unicode = true,
            "--no-text" => args.no_text = true,
            "--no-binary" => args.no_binary = true,
            "--no-follow" => args.no_follow = true,
            "--no-glob-case-insensitive" => args.no_glob_case_insensitive = true,
            "--no-ignore-file-case-insensitive" => {
                args.no_ignore_file_case_insensitive = true;
            }
            "--ignore-dot" => args.ignore_dot = true,
            "--ignore-exclude" => args.ignore_exclude = true,
            "--ignore-files" => args.ignore_files = true,
            "--ignore-global" => args.ignore_global = true,
            "--ignore-messages" => args.ignore_messages = true,
            "--ignore-parent" => args.ignore_parent = true,
            "--ignore-vcs" => args.ignore_vcs = true,
            "--no-one-file-system" => args.no_one_file_system = true,
            "--no-block-buffered" => args.no_block_buffered = true,
            "--no-byte-offset" => args.no_byte_offset = true,
            "--no-column" => args.no_column = true,
            "--no-context-separator" => args.no_context_separator = true,
            "--no-include-zero" => args.no_include_zero = true,
            "--no-line-buffered" => args.no_line_buffered = true,
            "--no-max-columns-preview" => args.no_max_columns_preview = true,
            "--no-trim" => args.no_trim = true,
            "--no-json" => args.no_json = true,
            "--no-stats" => args.no_stats = true,
            "--messages" => args.messages = true,
            "--vimgrep" => args.vimgrep = true,
            "--no-hidden" => {
                args.hidden = false;
                args.no_hidden = true;
            }
            "--hidden" | "-." => {
                args.hidden = true;
                args.no_hidden = false;
            }
            "--follow" | "-L" => args.follow = true,
            "--text" | "-a" => args.text = true,
            "--files-with-matches" | "-l" => args.files_with_matches = true,
            "--files-without-match" => args.files_without_match = true,
            "--path-separator" => {
                index += 1;
                args.path_separator = Some(
                    tokens
                        .get(index)
                        .context("missing value for path-separator")?
                        .clone(),
                );
            }
            _ if token.starts_with("--path-separator=") => {
                let (_, value) = token
                    .split_once('=')
                    .context("invalid path-separator argument shape")?;
                args.path_separator = Some(value.to_string());
            }
            "-C" | "--context" => {
                index += 1;
                let value = tokens
                    .get(index)
                    .context("missing value for context")?
                    .parse::<usize>()
                    .context("invalid context value")?;
                args.context = Some(value);
            }
            "-A" | "--after-context" => {
                index += 1;
                let value = tokens
                    .get(index)
                    .context("missing value for after-context")?
                    .parse::<usize>()
                    .context("invalid after-context value")?;
                args.after_context = Some(value);
            }
            "-B" | "--before-context" => {
                index += 1;
                let value = tokens
                    .get(index)
                    .context("missing value for before-context")?
                    .parse::<usize>()
                    .context("invalid before-context value")?;
                args.before_context = Some(value);
            }
            "-m" | "--max-count" => {
                index += 1;
                let value = tokens
                    .get(index)
                    .context("missing value for max-count")?
                    .parse::<usize>()
                    .context("invalid max-count value")?;
                args.max_count = Some(value);
            }
            "-d" | "--max-depth" | "--maxdepth" => {
                index += 1;
                let value = tokens
                    .get(index)
                    .context("missing value for max-depth")?
                    .parse::<usize>()
                    .context("invalid max-depth value")?;
                args.max_depth = Some(value);
            }
            _ if token.starts_with("--maxdepth=") => {
                let (_, value) = token
                    .split_once('=')
                    .context("invalid maxdepth argument shape")?;
                args.max_depth = Some(value.parse::<usize>().context("invalid maxdepth value")?);
            }
            "-g" | "--glob" => {
                index += 1;
                args.globs
                    .push(tokens.get(index).context("missing value for glob")?.clone());
            }
            "-t" | "--type" => {
                index += 1;
                args.file_types
                    .push(tokens.get(index).context("missing value for type")?.clone());
            }
            "--sort" => {
                index += 1;
                args.sort = Some(tokens.get(index).context("missing value for sort")?.clone());
            }
            "--sortr" => {
                index += 1;
                args.sort_reverse = Some(
                    tokens
                        .get(index)
                        .context("missing value for sortr")?
                        .clone(),
                );
            }
            _ if token.starts_with("--sort=") => {
                let (_, value) = token
                    .split_once('=')
                    .context("invalid sort argument shape")?;
                args.sort = Some(value.to_string());
            }
            _ if token.starts_with("--sortr=") => {
                let (_, value) = token
                    .split_once('=')
                    .context("invalid sortr argument shape")?;
                args.sort_reverse = Some(value.to_string());
            }
            "--sort-files" => args.sort_files = true,
            "--no-sort-files" => args.sort_files = false,
            _ if token.starts_with("--glob=") => {
                let (_, value) = token
                    .split_once('=')
                    .context("invalid glob argument shape")?;
                args.globs.push(value.to_string());
            }
            _ if token.starts_with('-') => bail!("unsupported flag: {token}"),
            _ => positionals.push(token.clone()),
        }
        index += 1;
    }

    if positionals.len() < 2 {
        bail!("tg-search-fast requires a pattern and at least one path");
    }

    args.patterns.push(positionals[0].clone());
    args.paths = positionals[1..].to_vec();
    Ok(args)
}

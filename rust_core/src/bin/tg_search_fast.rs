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
        ignore_case: false,
        fixed_strings: false,
        invert_match: false,
        count: false,
        count_matches: false,
        line_number: false,
        column: false,
        only_matching: false,
        context: None,
        before_context: None,
        after_context: None,
        max_count: None,
        word_regexp: false,
        smart_case: false,
        globs: Vec::new(),
        no_ignore: false,
        no_ignore_dot: false,
        no_ignore_exclude: false,
        no_ignore_files: false,
        no_ignore_global: false,
        no_ignore_parent: false,
        hidden: false,
        follow: false,
        text: false,
        files_with_matches: false,
        files_without_match: false,
        file_types: Vec::new(),
        color: None,
        replace: None,
        passthru: false,
        no_config: false,
        sort: None,
        sort_reverse: None,
        max_depth: None,
        null: false,
        null_data: false,
        multiline: false,
        multiline_dotall: false,
        patterns: Vec::new(),
        paths: Vec::new(),
        no_ignore_vcs: false,
        pcre2: false,
        auto_hybrid_regex: false,
        unicode: false,
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
            "--column" => args.column = true,
            "-w" | "--word-regexp" => args.word_regexp = true,
            "-0" | "--null" => args.null = true,
            "--null-data" => args.null_data = true,
            "-U" | "--multiline" => args.multiline = true,
            "--multiline-dotall" => args.multiline_dotall = true,
            "-S" | "--smart-case" => args.smart_case = true,
            "--no-ignore" => args.no_ignore = true,
            "--no-ignore-dot" => args.no_ignore_dot = true,
            "--no-ignore-exclude" => args.no_ignore_exclude = true,
            "--no-ignore-files" => args.no_ignore_files = true,
            "--no-ignore-global" => args.no_ignore_global = true,
            "--no-ignore-parent" => args.no_ignore_parent = true,
            "--no-config" => args.no_config = true,
            "--passthru" => args.passthru = true,
            "--passthrough" => args.passthru = true,
            "--auto-hybrid-regex" => args.auto_hybrid_regex = true,
            "--unicode" => args.unicode = true,
            "--hidden" | "-." => args.hidden = true,
            "--follow" | "-L" => args.follow = true,
            "--text" | "-a" => args.text = true,
            "--files-with-matches" | "-l" => args.files_with_matches = true,
            "--files-without-match" => args.files_without_match = true,
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
            "-d" | "--max-depth" => {
                index += 1;
                let value = tokens
                    .get(index)
                    .context("missing value for max-depth")?
                    .parse::<usize>()
                    .context("invalid max-depth value")?;
                args.max_depth = Some(value);
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

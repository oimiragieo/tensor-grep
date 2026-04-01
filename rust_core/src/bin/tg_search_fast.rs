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
        ignore_case: false,
        fixed_strings: false,
        invert_match: false,
        count: false,
        line_number: false,
        context: None,
        max_count: None,
        word_regexp: false,
        globs: Vec::new(),
        no_ignore: false,
        patterns: Vec::new(),
        path: String::new(),
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
            "-w" | "--word-regexp" => args.word_regexp = true,
            "--no-ignore" => args.no_ignore = true,
            "-C" | "--context" => {
                index += 1;
                let value = tokens
                    .get(index)
                    .context("missing value for context")?
                    .parse::<usize>()
                    .context("invalid context value")?;
                args.context = Some(value);
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
            "-g" | "--glob" => {
                index += 1;
                args.globs
                    .push(tokens.get(index).context("missing value for glob")?.clone());
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

    if positionals.len() != 2 {
        bail!("tg-search-fast requires exactly a pattern and path");
    }

    args.patterns.push(positionals[0].clone());
    args.path = positionals[1].clone();
    Ok(args)
}

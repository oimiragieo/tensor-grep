use std::ffi::OsString;

use crate::search_flag_registry::{raw_args_contain_any_flag, search_args_contain_any_flag};

pub(crate) fn search_citations_flag_conflicts(
    raw_args: &[OsString],
    normalized_search_flag_tokens: Option<Vec<String>>,
) -> bool {
    let Some(args) = normalized_search_flag_tokens else {
        return false;
    };
    if !search_args_contain_any_flag(&args, &["--citations"]) {
        return false;
    }
    if args.iter().any(|arg| arg == "--ndjson") {
        return true;
    }
    explicit_rg_format_requested(&args)
}

pub(crate) fn search_invocation_requests_citations(
    raw_args: &[OsString],
    normalized_search_flag_tokens: Option<Vec<String>>,
) -> bool {
    if !raw_args_contain_any_flag(raw_args, &["--citations"]) {
        return false;
    }
    normalized_search_flag_tokens
        .is_some_and(|args| search_args_contain_any_flag(&args, &["--citations"]))
}

fn explicit_rg_format_requested(args: &[String]) -> bool {
    let mut index = 0usize;
    while index < args.len() {
        if args[index] == "--" {
            break;
        }
        if args[index] == "--format" {
            index += 1;
            return args.get(index).map(String::as_str) == Some("rg");
        }
        if args[index].starts_with("--format=") {
            return args[index].split_once('=').map(|(_, v)| v) == Some("rg");
        }
        index += 1;
    }
    false
}

use std::ffi::OsString;

pub(super) fn is_search_version_invocation(raw_args: &[OsString]) -> bool {
    raw_args.len() == 3
        && raw_args.get(1).and_then(|arg| arg.to_str()) == Some("search")
        && matches!(
            raw_args.get(2).and_then(|arg| arg.to_str()),
            Some("--version" | "-V")
        )
}

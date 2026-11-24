use std::env;
use std::ffi::OsString;

pub(crate) fn env_flag_enabled(name: &str) -> bool {
    env::var(name)
        .map(|value| {
            matches!(
                value.trim().to_ascii_lowercase().as_str(),
                "1" | "true" | "yes" | "on"
            )
        })
        .unwrap_or(false)
}

#[cfg(any(windows, test))]
fn is_native_version_invocation(args: &[OsString]) -> bool {
    args.len() == 2
        && args[1]
            .to_str()
            .is_some_and(|arg| matches!(arg, "--version" | "-V"))
}

#[cfg(windows)]
pub(crate) fn collect_main_args() -> anyhow::Result<(
    Vec<OsString>,
    Option<tensor_grep_rs::managed_runtime_resolver::ManagedRuntime>,
)> {
    let args: Vec<OsString> = env::args_os().collect();
    let runtime = match tensor_grep_rs::prepare_main_startup(&args[1..]) {
        Ok(runtime) => runtime,
        Err(err) if is_native_version_invocation(&args) => {
            eprintln!("warning: managed runtime unavailable ({err:#}); falling back to launcher");
            None
        }
        Err(err) => return Err(err),
    };
    Ok((args, runtime))
}

#[cfg(not(windows))]
pub(crate) fn collect_main_args() -> anyhow::Result<(Vec<OsString>, ())> {
    Ok((env::args_os().collect(), ()))
}

#[cfg(test)]
mod tests {
    use super::is_native_version_invocation;
    use std::ffi::OsString;

    fn os_args(items: &[&str]) -> Vec<OsString> {
        items.iter().map(OsString::from).collect()
    }

    #[test]
    fn native_version_fallback_matches_only_top_level_version_flags() {
        for flag in ["--version", "-V"] {
            assert!(
                is_native_version_invocation(&os_args(&["tg", flag])),
                "expected passthrough for {flag}"
            );
        }
        for args in [
            &["tg"][..],
            &["tg", "--help"],
            &["tg", "-h"],
            &["tg", "search"],
            &["tg", "--json"],
            &["tg", "search", "--version"],
            &["tg", "--version", "extra"],
        ] {
            assert!(
                !is_native_version_invocation(&os_args(args)),
                "did not expect passthrough for {args:?}"
            );
        }
    }
}

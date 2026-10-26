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

#[cfg(windows)]
pub(crate) fn collect_main_args() -> anyhow::Result<(
    Vec<OsString>,
    Option<tensor_grep_rs::managed_runtime_resolver::ManagedRuntime>,
)> {
    let args: Vec<OsString> = env::args_os().collect();
    let runtime = tensor_grep_rs::prepare_main_startup(&args[1..])?;
    Ok((args, runtime))
}

#[cfg(not(windows))]
pub(crate) fn collect_main_args() -> anyhow::Result<(Vec<OsString>, ())> {
    Ok((env::args_os().collect(), ()))
}

use crate::runtime_paths::{
    resolve_existing_relative_to_current_exe, resolve_explicit_file_override,
};
use anyhow::{anyhow, Context};
use std::env;
use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::sync::OnceLock;

const WINDOWS_RG_DIRNAME: &str = "ripgrep-14.1.0-x86_64-pc-windows-msvc";
const TG_RG_PATH_ENV: &str = "TG_RG_PATH";
const LEGACY_TG_RG_BINARY_ENV: &str = "TG_RG_BINARY";
const TG_DISABLE_RG_ENV: &str = "TG_DISABLE_RG";
static RG_BINARY_CACHE: OnceLock<Option<PathBuf>> = OnceLock::new();

#[derive(Debug, Clone)]
pub struct RipgrepSearchArgs {
    pub ignore_case: bool,
    pub fixed_strings: bool,
    pub invert_match: bool,
    pub count: bool,
    pub count_matches: bool,
    pub line_number: bool,
    pub column: bool,
    pub only_matching: bool,
    pub context: Option<usize>,
    pub before_context: Option<usize>,
    pub after_context: Option<usize>,
    pub max_count: Option<usize>,
    pub word_regexp: bool,
    pub smart_case: bool,
    pub globs: Vec<String>,
    pub no_ignore: bool,
    pub no_ignore_vcs: bool,
    pub hidden: bool,
    pub follow: bool,
    pub text: bool,
    pub files_with_matches: bool,
    pub files_without_match: bool,
    pub file_types: Vec<String>,
    pub color: Option<String>,
    pub replace: Option<String>,
    pub patterns: Vec<String>,
    pub path: String,
    pub pcre2: bool,
    pub max_filesize: Option<String>,
}

pub fn execute_ripgrep_search(args: &RipgrepSearchArgs) -> anyhow::Result<i32> {
    let rg_binary = resolve_ripgrep_binary().ok_or_else(|| {
        anyhow!(
            "ripgrep binary not found. Install `rg`, set {TG_RG_PATH_ENV}, or place a bundled ripgrep binary next to `tg`."
        )
    })?;

    let mut command = Command::new(rg_binary);
    command
        .stdin(Stdio::inherit())
        .stdout(Stdio::inherit())
        .stderr(Stdio::inherit());

    if args.pcre2 {
        command.arg("-P");
    }
    if let Some(size) = &args.max_filesize {
        command.arg("--max-filesize").arg(size);
    }
    if args.no_ignore_vcs {
        command.arg("--no-ignore-vcs");
    }
    if args.ignore_case {
        command.arg("-i");
    }
    if args.fixed_strings {
        command.arg("-F");
    }
    if args.invert_match {
        command.arg("-v");
    }
    if args.count_matches {
        command.arg("--count-matches");
    }
    if args.only_matching {
        command.arg("-o");
    }
    if args.column {
        command.arg("--column");
    }
    if args.count {
        command.arg("-c");
    } else if args.line_number {
        command.arg("-n");
    }
    if let Some(context) = args.context {
        command.arg("-C").arg(context.to_string());
    } else {
        if let Some(before_context) = args.before_context {
            command.arg("-B").arg(before_context.to_string());
        }
        if let Some(after_context) = args.after_context {
            command.arg("-A").arg(after_context.to_string());
        }
    }
    if let Some(max_count) = args.max_count {
        command.arg("-m").arg(max_count.to_string());
    }
    if args.word_regexp {
        command.arg("-w");
    }
    if args.smart_case {
        command.arg("-S");
    }
    if args.no_ignore {
        command.arg("--no-ignore");
    }
    if args.hidden {
        command.arg("--hidden");
    }
    if args.follow {
        command.arg("--follow");
    }
    if args.text {
        command.arg("--text");
    }
    if args.files_with_matches {
        command.arg("-l");
    }
    if args.files_without_match {
        command.arg("--files-without-match");
    }
    for file_type in &args.file_types {
        command.arg("-t").arg(file_type);
    }
    if let Some(color) = &args.color {
        command.arg("--color").arg(color);
    }
    if let Some(replacement) = &args.replace {
        command.arg("--replace").arg(replacement);
    }
    for glob in &args.globs {
        command.arg("-g").arg(glob);
    }

    for pattern in &args.patterns {
        command.arg("-e").arg(pattern);
    }

    command.arg(&args.path);

    let status = command.status().context("failed to execute ripgrep")?;
    Ok(status.code().unwrap_or(1))
}

pub fn ripgrep_is_available() -> bool {
    resolve_ripgrep_binary().is_some()
}

fn resolve_ripgrep_binary() -> Option<PathBuf> {
    resolve_ripgrep_binary_with_cache(&RG_BINARY_CACHE, resolve_ripgrep_binary_uncached)
}

fn resolve_ripgrep_binary_with_cache(
    cache: &OnceLock<Option<PathBuf>>,
    resolver: impl FnOnce() -> Option<PathBuf>,
) -> Option<PathBuf> {
    cache.get_or_init(resolver).clone()
}

fn resolve_ripgrep_binary_uncached() -> Option<PathBuf> {
    if env_flag_enabled(TG_DISABLE_RG_ENV) {
        return None;
    }

    if let Some(candidate) = resolve_explicit_file_override(TG_RG_PATH_ENV) {
        return Some(candidate);
    }

    if let Some(path) = env::var_os(LEGACY_TG_RG_BINARY_ENV) {
        let candidate = PathBuf::from(path);
        if candidate.is_file() {
            return Some(candidate);
        }
    }

    if let Some(runtime_relative_rg) = resolve_existing_relative_to_current_exe(&[
        &[if cfg!(windows) { "rg.exe" } else { "rg" }],
        &[
            "benchmarks",
            WINDOWS_RG_DIRNAME,
            if cfg!(windows) { "rg.exe" } else { "rg" },
        ],
        &["benchmarks", "rg"],
    ]) {
        return Some(runtime_relative_rg);
    }

    rg_path_candidates()
        .into_iter()
        .find(|candidate| candidate.is_file())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicUsize, Ordering};

    #[test]
    fn resolve_ripgrep_binary_uses_cached_value_after_first_lookup() {
        let cache = OnceLock::new();
        let calls = AtomicUsize::new(0);
        let expected = PathBuf::from("rg-a.exe");

        let first = resolve_ripgrep_binary_with_cache(&cache, || {
            calls.fetch_add(1, Ordering::SeqCst);
            Some(expected.clone())
        });
        let second = resolve_ripgrep_binary_with_cache(&cache, || {
            calls.fetch_add(1, Ordering::SeqCst);
            Some(PathBuf::from("rg-b.exe"))
        });

        assert_eq!(first, Some(expected.clone()));
        assert_eq!(second, Some(expected));
        assert_eq!(calls.load(Ordering::SeqCst), 1);
    }
}

fn env_flag_enabled(name: &str) -> bool {
    env::var(name)
        .map(|value| {
            matches!(
                value.trim().to_ascii_lowercase().as_str(),
                "1" | "true" | "yes" | "on"
            )
        })
        .unwrap_or(false)
}

fn rg_path_candidates() -> Vec<PathBuf> {
    let mut candidates = Vec::new();

    if cfg!(windows) {
        candidates.push(PathBuf::from("rg.exe"));
    }
    candidates.push(PathBuf::from("rg"));

    if let Some(path_var) = env::var_os("PATH") {
        for dir in env::split_paths(&path_var) {
            if cfg!(windows) {
                candidates.push(dir.join("rg.exe"));
            }
            candidates.push(dir.join("rg"));
        }
    }

    candidates
}

use crate::runtime_paths::{
    resolve_existing_relative_to_current_exe, resolve_explicit_file_override,
};
use anyhow::{anyhow, Context};
use std::env;
use std::path::PathBuf;
use std::process::{Command, Stdio};

const WINDOWS_RG_DIRNAME: &str = "ripgrep-14.1.0-x86_64-pc-windows-msvc";
const TG_RG_PATH_ENV: &str = "TG_RG_PATH";
const LEGACY_TG_RG_BINARY_ENV: &str = "TG_RG_BINARY";
const TG_DISABLE_RG_ENV: &str = "TG_DISABLE_RG";

#[derive(Debug, Clone)]
pub struct RipgrepSearchArgs {
    pub ignore_case: bool,
    pub fixed_strings: bool,
    pub invert_match: bool,
    pub count: bool,
    pub line_number: bool,
    pub context: Option<usize>,
    pub max_count: Option<usize>,
    pub word_regexp: bool,
    pub globs: Vec<String>,
    pub no_ignore: bool,
    pub patterns: Vec<String>,
    pub path: String,
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

    if args.ignore_case {
        command.arg("-i");
    }
    if args.fixed_strings {
        command.arg("-F");
    }
    if args.invert_match {
        command.arg("-v");
    }
    if args.count {
        command.arg("-c");
    } else if args.line_number {
        command.arg("-n");
    }
    if let Some(context) = args.context {
        command.arg("-C").arg(context.to_string());
    }
    if let Some(max_count) = args.max_count {
        command.arg("-m").arg(max_count.to_string());
    }
    if args.word_regexp {
        command.arg("-w");
    }
    if args.no_ignore {
        command.arg("--no-ignore");
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

fn env_flag_enabled(name: &str) -> bool {
    env::var(name)
        .map(|value| matches!(value.trim().to_ascii_lowercase().as_str(), "1" | "true" | "yes" | "on"))
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

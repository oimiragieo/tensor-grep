use anyhow::{anyhow, Context};
use std::env;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

const WINDOWS_RG_DIRNAME: &str = "ripgrep-14.1.0-x86_64-pc-windows-msvc";

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
    pub pattern: String,
    pub path: String,
}

pub fn execute_ripgrep_search(args: &RipgrepSearchArgs) -> anyhow::Result<i32> {
    let rg_binary = resolve_ripgrep_binary().ok_or_else(|| {
        anyhow!(
            "ripgrep binary not found. Install `rg` or use the bundled benchmark ripgrep binary."
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

    command.arg(&args.pattern).arg(&args.path);

    let status = command.status().context("failed to execute ripgrep")?;
    Ok(status.code().unwrap_or(1))
}

pub fn ripgrep_is_available() -> bool {
    resolve_ripgrep_binary().is_some()
}

fn resolve_ripgrep_binary() -> Option<PathBuf> {
    if let Some(path) = env::var_os("TG_RG_BINARY") {
        let candidate = PathBuf::from(path);
        if candidate.is_file() {
            return Some(candidate);
        }
    }

    rg_path_candidates()
        .into_iter()
        .find(|candidate| candidate.is_file())
}

fn rg_path_candidates() -> Vec<PathBuf> {
    let mut candidates = Vec::new();

    let repo_root = repo_root();
    if cfg!(windows) {
        candidates.push(
            repo_root
                .join("benchmarks")
                .join(WINDOWS_RG_DIRNAME)
                .join("rg.exe"),
        );
    } else {
        candidates.push(repo_root.join("benchmarks").join("rg"));
    }

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

fn repo_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap_or_else(|| Path::new(env!("CARGO_MANIFEST_DIR")))
        .to_path_buf()
}

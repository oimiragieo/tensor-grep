use crate::runtime_paths::{
    resolve_existing_relative_to_current_exe, resolve_explicit_file_override,
};
use anyhow::{anyhow, Context};
use std::env;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::OnceLock;

const WINDOWS_RG_DIRNAME: &str = "ripgrep-14.1.0-x86_64-pc-windows-msvc";
const TG_RG_PATH_ENV: &str = "TG_RG_PATH";
const LEGACY_TG_RG_BINARY_ENV: &str = "TG_RG_BINARY";
const TG_DISABLE_RG_ENV: &str = "TG_DISABLE_RG";
static RG_BINARY_CACHE: OnceLock<Option<PathBuf>> = OnceLock::new();

#[derive(Debug, Clone)]
pub struct RipgrepSearchArgs {
    pub files: bool,
    pub json: bool,
    pub ignore_case: bool,
    pub fixed_strings: bool,
    pub no_fixed_strings: bool,
    pub invert_match: bool,
    pub no_invert_match: bool,
    pub count: bool,
    pub count_matches: bool,
    pub line_number: bool,
    pub no_line_number: bool,
    pub column: bool,
    pub only_matching: bool,
    pub context: Option<usize>,
    pub before_context: Option<usize>,
    pub after_context: Option<usize>,
    pub max_count: Option<usize>,
    pub word_regexp: bool,
    pub smart_case: bool,
    pub globs: Vec<String>,
    pub ignore: bool,
    pub no_ignore: bool,
    pub no_ignore_dot: bool,
    pub no_ignore_exclude: bool,
    pub no_ignore_files: bool,
    pub no_ignore_global: bool,
    pub no_ignore_parent: bool,
    pub no_ignore_vcs: bool,
    pub require_git: bool,
    pub hidden: bool,
    pub no_hidden: bool,
    pub follow: bool,
    pub text: bool,
    pub files_with_matches: bool,
    pub files_without_match: bool,
    pub file_types: Vec<String>,
    pub color: Option<String>,
    pub path_separator: Option<String>,
    pub replace: Option<String>,
    pub vimgrep: bool,
    pub passthru: bool,
    pub no_config: bool,
    pub sort: Option<String>,
    pub sort_reverse: Option<String>,
    pub sort_files: bool,
    pub max_depth: Option<usize>,
    pub null: bool,
    pub null_data: bool,
    pub multiline: bool,
    pub no_multiline: bool,
    pub multiline_dotall: bool,
    pub no_multiline_dotall: bool,
    pub patterns: Vec<String>,
    pub paths: Vec<String>,
    pub pcre2: bool,
    pub no_pcre2: bool,
    pub pcre2_unicode: bool,
    pub no_pcre2_unicode: bool,
    pub no_crlf: bool,
    pub no_encoding: bool,
    pub no_mmap: bool,
    pub no_pre: bool,
    pub no_search_zip: bool,
    pub auto_hybrid_regex: bool,
    pub no_auto_hybrid_regex: bool,
    pub unicode: bool,
    pub no_text: bool,
    pub no_binary: bool,
    pub no_follow: bool,
    pub no_glob_case_insensitive: bool,
    pub no_ignore_file_case_insensitive: bool,
    pub ignore_dot: bool,
    pub ignore_exclude: bool,
    pub ignore_files: bool,
    pub ignore_global: bool,
    pub ignore_messages: bool,
    pub ignore_parent: bool,
    pub ignore_vcs: bool,
    pub no_one_file_system: bool,
    pub no_block_buffered: bool,
    pub no_byte_offset: bool,
    pub no_column: bool,
    pub no_context_separator: bool,
    pub no_include_zero: bool,
    pub no_line_buffered: bool,
    pub no_max_columns_preview: bool,
    pub no_trim: bool,
    pub no_json: bool,
    pub messages: bool,
    pub no_stats: bool,
    pub max_filesize: Option<String>,
}

pub fn execute_ripgrep_search(args: &RipgrepSearchArgs) -> anyhow::Result<i32> {
    let rg_binary = resolve_ripgrep_binary().ok_or_else(|| {
        anyhow!(
            "ripgrep binary not found. Install `rg`, set {TG_RG_PATH_ENV}, or place a bundled ripgrep binary next to `tg`."
        )
    })?;

    let mut command = command_for_executable(&rg_binary);
    command
        .stdin(Stdio::inherit())
        .stdout(Stdio::inherit())
        .stderr(Stdio::inherit());

    if args.pcre2 {
        command.arg("-P");
    }
    if args.no_pcre2 {
        command.arg("--no-pcre2");
    }
    if args.pcre2_unicode {
        command.arg("--pcre2-unicode");
    }
    if args.no_pcre2_unicode {
        command.arg("--no-pcre2-unicode");
    }
    if args.no_crlf {
        command.arg("--no-crlf");
    }
    if args.no_encoding {
        command.arg("--no-encoding");
    }
    if args.no_mmap {
        command.arg("--no-mmap");
    }
    if args.no_pre {
        command.arg("--no-pre");
    }
    if args.no_search_zip {
        command.arg("--no-search-zip");
    }
    if args.auto_hybrid_regex {
        command.arg("--auto-hybrid-regex");
    }
    if args.no_auto_hybrid_regex {
        command.arg("--no-auto-hybrid-regex");
    }
    if args.unicode {
        command.arg("--unicode");
    }
    if args.no_text {
        command.arg("--no-text");
    }
    if args.no_binary {
        command.arg("--no-binary");
    }
    if args.no_follow {
        command.arg("--no-follow");
    }
    if args.no_glob_case_insensitive {
        command.arg("--no-glob-case-insensitive");
    }
    if args.no_ignore_file_case_insensitive {
        command.arg("--no-ignore-file-case-insensitive");
    }
    if args.ignore_dot {
        command.arg("--ignore-dot");
    }
    if args.ignore_exclude {
        command.arg("--ignore-exclude");
    }
    if args.ignore_files {
        command.arg("--ignore-files");
    }
    if args.ignore_global {
        command.arg("--ignore-global");
    }
    if args.ignore_messages {
        command.arg("--ignore-messages");
    }
    if args.ignore_parent {
        command.arg("--ignore-parent");
    }
    if args.ignore_vcs {
        command.arg("--ignore-vcs");
    }
    if args.no_one_file_system {
        command.arg("--no-one-file-system");
    }
    if args.no_block_buffered {
        command.arg("--no-block-buffered");
    }
    if args.no_byte_offset {
        command.arg("--no-byte-offset");
    }
    if args.no_column {
        command.arg("--no-column");
    }
    if args.no_context_separator {
        command.arg("--no-context-separator");
    }
    if args.no_include_zero {
        command.arg("--no-include-zero");
    }
    if args.no_line_buffered {
        command.arg("--no-line-buffered");
    }
    if args.no_max_columns_preview {
        command.arg("--no-max-columns-preview");
    }
    if args.no_trim {
        command.arg("--no-trim");
    }
    if args.no_json {
        command.arg("--no-json");
    }
    if args.no_stats {
        command.arg("--no-stats");
    }
    if args.files {
        command.arg("--files");
    }
    if args.json {
        command.arg("--json");
    }
    if let Some(size) = &args.max_filesize {
        command.arg("--max-filesize").arg(size);
    }
    if let Some(max_depth) = args.max_depth {
        command.arg("--max-depth").arg(max_depth.to_string());
    }
    if args.null {
        command.arg("--null");
    }
    if args.null_data {
        command.arg("--null-data");
    }
    if args.multiline {
        command.arg("--multiline");
    }
    if args.no_multiline {
        command.arg("--no-multiline");
    }
    if args.multiline_dotall {
        command.arg("--multiline-dotall");
    }
    if args.no_multiline_dotall {
        command.arg("--no-multiline-dotall");
    }
    if args.no_ignore_vcs {
        command.arg("--no-ignore-vcs");
    }
    if args.require_git {
        command.arg("--require-git");
    }
    if args.ignore_case {
        command.arg("-i");
    }
    if args.fixed_strings {
        command.arg("-F");
    }
    if args.no_fixed_strings {
        command.arg("--no-fixed-strings");
    }
    if args.invert_match {
        command.arg("-v");
    }
    if args.no_invert_match {
        command.arg("--no-invert-match");
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
    } else if args.no_line_number {
        command.arg("-N");
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
    if args.ignore {
        command.arg("--ignore");
    } else if args.no_ignore {
        command.arg("--no-ignore");
    }
    if args.no_ignore_dot {
        command.arg("--no-ignore-dot");
    }
    if args.no_ignore_exclude {
        command.arg("--no-ignore-exclude");
    }
    if args.no_ignore_files {
        command.arg("--no-ignore-files");
    }
    if args.no_ignore_global {
        command.arg("--no-ignore-global");
    }
    if args.no_ignore_parent {
        command.arg("--no-ignore-parent");
    }
    if args.no_config {
        command.arg("--no-config");
    }
    if args.hidden {
        command.arg("--hidden");
    } else if args.no_hidden {
        command.arg("--no-hidden");
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
    if let Some(path_separator) = &args.path_separator {
        command.arg("--path-separator").arg(path_separator);
    }
    if let Some(replacement) = &args.replace {
        command.arg("--replace").arg(replacement);
    }
    if args.vimgrep {
        command.arg("--vimgrep");
    }
    if args.passthru {
        command.arg("--passthru");
    }
    if args.messages {
        command.arg("--messages");
    }
    if let Some(sort) = &args.sort {
        command.arg("--sort").arg(sort);
    }
    if let Some(sort_reverse) = &args.sort_reverse {
        command.arg("--sortr").arg(sort_reverse);
    }
    if args.sort_files {
        command.arg("--sort-files");
    }
    for glob in &args.globs {
        command.arg("-g").arg(glob);
    }

    if !args.files {
        for pattern in &args.patterns {
            command.arg("-e").arg(pattern);
        }
    }

    for path in &args.paths {
        command.arg(path);
    }

    let status = command.status().context("failed to execute ripgrep")?;
    Ok(status.code().unwrap_or(1))
}

pub fn execute_ripgrep_pcre2_version() -> anyhow::Result<i32> {
    let rg_binary = resolve_ripgrep_binary().ok_or_else(|| {
        anyhow!(
            "ripgrep binary not found. Install `rg`, set {TG_RG_PATH_ENV}, or place a bundled ripgrep binary next to `tg`."
        )
    })?;

    let mut command = command_for_executable(&rg_binary);
    let status = command
        .arg("--pcre2-version")
        .stdin(Stdio::inherit())
        .stdout(Stdio::inherit())
        .stderr(Stdio::inherit())
        .status()
        .context("failed to execute ripgrep")?;
    Ok(status.code().unwrap_or(1))
}

pub fn execute_ripgrep_type_list() -> anyhow::Result<i32> {
    let rg_binary = resolve_ripgrep_binary().ok_or_else(|| {
        anyhow!(
            "ripgrep binary not found. Install `rg`, set {TG_RG_PATH_ENV}, or place a bundled ripgrep binary next to `tg`."
        )
    })?;

    let mut command = command_for_executable(&rg_binary);
    let status = command
        .arg("--type-list")
        .stdin(Stdio::inherit())
        .stdout(Stdio::inherit())
        .stderr(Stdio::inherit())
        .status()
        .context("failed to execute ripgrep")?;
    Ok(status.code().unwrap_or(1))
}

pub fn ripgrep_is_available() -> bool {
    resolve_ripgrep_binary().is_some()
}

fn command_for_executable(program: &Path) -> Command {
    #[cfg(windows)]
    {
        if is_windows_batch_script(program) {
            let mut command = Command::new("cmd");
            command.arg("/d").arg("/c").arg(program);
            return command;
        }
    }
    Command::new(program)
}

#[cfg(windows)]
fn is_windows_batch_script(program: &Path) -> bool {
    program
        .extension()
        .and_then(|extension| extension.to_str())
        .is_some_and(|extension| {
            extension.eq_ignore_ascii_case("cmd") || extension.eq_ignore_ascii_case("bat")
        })
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

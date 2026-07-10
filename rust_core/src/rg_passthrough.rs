use crate::runtime_paths::{
    resolve_existing_relative_to_current_exe, resolve_explicit_file_override,
};
use anyhow::{anyhow, Context};
use ignore::WalkBuilder;
use std::env;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::OnceLock;

const WINDOWS_RG_DIRNAME: &str = "ripgrep-14.1.0-x86_64-pc-windows-msvc";
const TG_RG_PATH_ENV: &str = "TG_RG_PATH";
const LEGACY_TG_RG_BINARY_ENV: &str = "TG_RG_BINARY";
const TG_DISABLE_RG_ENV: &str = "TG_DISABLE_RG";
static RG_BINARY_CACHE: OnceLock<Option<PathBuf>> = OnceLock::new();

#[derive(Debug, Clone, Default)]
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
    /// Whether the caller omitted an explicit PATH positional (the search root defaulted to
    /// `.`/stdin instead of a user-supplied path). Gates the implicit-walk-ceiling refusal at
    /// the top of `execute_ripgrep_search` below -- an explicit, deliberately-scoped PATH must
    /// never be refused regardless of its size (Trap #3 parity). Every exhaustive construction
    /// site must set this correctly; `#[derive(Default)]` gives the fail-safe `false` to the one
    /// test helper that builds via `RipgrepSearchArgs::default()`.
    pub path_was_implicit: bool,
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

// Bug #88 (dogfood v1.54.0 -> v1.54.1 follow-up): a bare `tg search --glob X PATTERN` with NO
// explicit PATH used to hand the walk straight to real `rg` via `execute_ripgrep_search`
// (`search_prefers_ripgrep_passthrough`'s `!args.globs.is_empty()` branch in
// `handle_ripgrep_search`) with no ceiling check at all. `--glob`/`--type` narrow WHICH files
// MATCH, they do NOT bound how much of the tree is WALKED to find them -- only `--max-depth`
// (and gitignore/hidden pruning) do. On a large/workspace/vendored cwd this ran effectively
// unbounded (only the 60s `TG_RG_TIMEOUT_SECONDS` rg-subprocess timeout eventually killed it).
//
// The first fix attempt (296cc92) had two gaps the dogfood re-harvest caught:
//   (1) it counted post-GLOB matches, so a SELECTIVE glob (`*.rs` in a huge JS tree) counts
//       few matches, sails under the ceiling, and proceeds into the unbounded WALK; and worse,
//       a glob matching everything but slowly (`**/*`) made the probe itself walk the whole
//       tree. The hang is TREE-WALK cost, independent of match count -- so this probe now
//       counts every FILE the walker VISITS (ignoring the glob filter entirely) and early-exits
//       the instant the WALK exceeds the ceiling. That is both robust to glob selectivity and
//       self-bounded (never more than `ceiling + 1` files walked).
//   (2) `request.paths` is EMPTY (not `["."]`) whenever `grep_cli::is_readable_stdin()` is true
//       (`implicit_search_paths`), so the probe saw no root and skipped -- yet rg still walked
//       the cwd unbounded. The caller now normalizes an implicit empty-paths search to `["."]`
//       before probing (mirroring the Python CLI, which always guards `["."]`).
//
// Audit #100 (2026-07-10): that guard lived ONLY in `handle_ripgrep_search` (main.rs) -- ONE of
// several callers of `execute_ripgrep_search`. The native front door's default search fast path
// (`try_default_search_frontdoor_passthrough` -> `parse_early_ripgrep_args`'s `-e` arm) called
// `execute_ripgrep_search` directly and never passed through that gate at all: a complete
// regression of the pre-#480 walk-DoS, reachable via `tg search -e "TODO" --glob "*.py"` with no
// explicit path on the standalone native binary. HOISTED here (moved from `main.rs`, the binary
// crate root, into this library module -- `execute_ripgrep_search` cannot call back into a
// function defined only in the separate `tg` bin crate) as the first statement of
// `execute_ripgrep_search` itself, so every caller passes through one chokepoint: the frontdoor,
// `handle_ripgrep_search`, the positional CLI, tg-search-fast, and the PyO3 FFI bridge. Also
// drops the old `--glob`/`--type` requirement -- the hoisted gate fires on `path_was_implicit`
// alone, still bounded by the same 1500-file ceiling walk -- closing #105 (a bare, unfiltered
// implicit-path search on a huge root) too.
pub const IMPLICIT_SEARCH_WALK_FILE_CEILING: usize = 1500;

/// Bounded WALK probe: walks `paths` honoring the SAME max-depth / no-ignore / hidden traversal
/// settings the real search would use, counting every FILE the walker VISITS (NOT filtered by
/// the search glob -- a file glob does not prune the walk, so walk cost is glob-independent),
/// and returns `true` the instant the WALK exceeds the ceiling. Never enumerates more than
/// `ceiling + 1` files (that would just be the unbounded work this guard exists to avoid).
/// Only meaningful when the caller has confirmed `path_was_implicit` (no explicit PATH): an
/// explicit, deliberately-scoped PATH must still run uninhibited (the CLI's `paths_defaulted`
/// gate / Trap #3).
pub fn implicit_search_walk_exceeds_ceiling(
    paths: &[String],
    max_depth: Option<usize>,
    no_ignore: bool,
    hidden: bool,
    ceiling: usize,
) -> bool {
    let roots: Vec<PathBuf> = paths
        .iter()
        .map(PathBuf::from)
        .filter(|root| root.is_dir())
        .collect();
    let Some(first_root) = roots.first() else {
        return false;
    };

    let mut builder = WalkBuilder::new(first_root);
    for root in roots.iter().skip(1) {
        builder.add(root);
    }
    if let Some(depth) = max_depth {
        builder.max_depth(Some(depth));
    }
    // `hidden(true)` = SKIP hidden entries (ripgrep's default). Only descend hidden when the
    // real search was asked to (`--hidden`), so the probe's walk cost matches rg's.
    builder.hidden(!hidden);
    if no_ignore {
        builder.ignore(false);
        builder.git_ignore(false);
        builder.git_global(false);
        builder.git_exclude(false);
        builder.parents(false);
    }

    let mut file_count = 0usize;
    for entry in builder.build() {
        let Ok(entry) = entry else { continue };
        if entry
            .file_type()
            .map(|kind| kind.is_file())
            .unwrap_or(false)
        {
            file_count += 1;
            if file_count > ceiling {
                return true;
            }
        }
    }
    false
}

/// Substring marker present in every implicit-walk-ceiling refusal message, regardless of which
/// engine (rg-passthrough or native-CPU) produced it -- both reuse
/// `format_unbounded_implicit_search_walk_error` for the exact same text. Audit #105: a caller
/// that cannot rely on the generic native-search Err-to-exit-2 plumbing (the multi-pattern /
/// GPU-fallback call sites in main.rs, which otherwise let an `Err` propagate to `main()`'s
/// default exit-1) uses `is_unbounded_implicit_search_walk_refusal` to recognize ONLY this
/// specific refusal and normalize it to the shared exit-2 contract, without touching how any
/// other native-search error is handled.
const UNBOUNDED_IMPLICIT_SEARCH_WALK_REFUSAL_MARKER: &str =
    "broad root scan refused as a safety guard";

pub fn format_unbounded_implicit_search_walk_error(ceiling: usize) -> String {
    format!(
        "Error: {UNBOUNDED_IMPLICIT_SEARCH_WALK_REFUSAL_MARKER}, not a zero-match result: \
no PATH was given, so the search defaulted to the current directory, which is a large root \
(over {ceiling} files walked); --glob/--type only filter WHICH files match, they do not bound \
how much of the tree must be walked to find them. Scope the search to an explicit PATH, add \
--max-depth, or pass --allow-broad-generated-scan to opt in.\n\
For bounded output:\n\
tg search <pattern> <root> --glob \"*.py\"\n\
tg search <pattern> <root> --max-depth <N>\n\
For intentional broad scans:\n\
--allow-broad-generated-scan"
    )
}

/// See `UNBOUNDED_IMPLICIT_SEARCH_WALK_REFUSAL_MARKER`.
pub fn is_unbounded_implicit_search_walk_refusal(message: &str) -> bool {
    message.contains(UNBOUNDED_IMPLICIT_SEARCH_WALK_REFUSAL_MARKER)
}

/// First-statement chokepoint for `execute_ripgrep_search`: computes the implicit-walk-ceiling
/// refusal message (if any) before any rg subprocess is spawned. Mirrors the empty-paths ->
/// `["."]` substitution `handle_ripgrep_search` used to do at its own (now redundant) call site,
/// so a caller that leaves `paths` empty (e.g. the frontdoor's `-e` arm when stdin is being
/// read) still gets a `.`-rooted probe rather than silently skipping it.
fn check_implicit_walk_ceiling(args: &RipgrepSearchArgs) -> Option<String> {
    if !args.path_was_implicit {
        return None;
    }
    let dot_root = [".".to_string()];
    let probe_roots: &[String] = if args.paths.is_empty() {
        &dot_root
    } else {
        &args.paths
    };
    if implicit_search_walk_exceeds_ceiling(
        probe_roots,
        args.max_depth,
        args.no_ignore,
        args.hidden,
        IMPLICIT_SEARCH_WALK_FILE_CEILING,
    ) {
        Some(format_unbounded_implicit_search_walk_error(
            IMPLICIT_SEARCH_WALK_FILE_CEILING,
        ))
    } else {
        None
    }
}

pub fn execute_ripgrep_search(args: &RipgrepSearchArgs) -> anyhow::Result<i32> {
    // SECURITY (audit #100): must be the FIRST statement, before `resolve_ripgrep_binary()` or
    // any other work -- this is the single chokepoint every caller of `execute_ripgrep_search`
    // passes through. Returns `Ok(2)`, NEVER `process::exit`/`Err`/`bail!`: this function is
    // also called from inside the PyO3 FFI bridge (`lib.rs`), which embeds this crate into the
    // host Python process -- `process::exit` would hard-kill the Python host, and `Err`/`bail!`
    // reintroduces the exit-1-vs-exit-2 no-match ambiguity bug (audit #81 #7). Every CLI call
    // site already treats a non-zero `Ok(exit_code)` the same way it treats a real rg exit code
    // (`if exit_code != 0 { process::exit(exit_code.max(1)); }`), so returning `Ok(2)` here needs
    // zero call-site changes.
    if let Some(refusal) = check_implicit_walk_ceiling(args) {
        eprintln!("{refusal}");
        return Ok(2);
    }

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

    for operand in ripgrep_operand_args(args) {
        command.arg(operand);
    }

    let status = command.status().context("failed to execute ripgrep")?;
    Ok(status.code().unwrap_or(1))
}

/// Build ripgrep's operand args: search patterns (flag-safe via `-e`), an end-of-options `--`
/// sentinel, then the user paths. The `--` is load-bearing SECURITY (CWE-88): without it a user
/// path beginning with `-` — e.g. `--pre=CMD` — is parsed by rg's own option parser as a FLAG, not
/// a positional path, escalating toward arbitrary command execution via `--pre`. Extracted so the
/// sentinel placement is unit-testable without spawning rg (#326's helper was lost in a refactor;
/// the raw path loop had silently shipped since).
fn ripgrep_operand_args(args: &RipgrepSearchArgs) -> Vec<String> {
    let mut operands: Vec<String> = Vec::new();
    if !args.files {
        for pattern in &args.patterns {
            operands.push("-e".to_string());
            operands.push(pattern.clone());
        }
    }
    // Emit the sentinel ONLY when there are paths to protect: everything after `--` is a
    // positional path, never an option (even if it begins with `-`). With no user path there is
    // nothing to guard, and an unconditional trailing `--` would alter the no-path / piped-stdin
    // invocation (rg then reads stdin), which the parity tests correctly pin.
    if !args.paths.is_empty() {
        operands.push("--".to_string());
        for path in &args.paths {
            operands.push(path.clone());
        }
    }
    operands
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
    // Do NOT manually wrap a .cmd/.bat via `cmd /d /c <program> <args>`: that makes cmd.exe the
    // program, so std applies plain CreateProcess argv quoting and cmd.exe RE-PARSES the search
    // args -- a `&`/`|`/`%` in an (MCP-)caller-supplied pattern injects a command (CWE-88 / the
    // BatBadBut CVE-2024-24576 class) when rg resolves to a .cmd shim. Since Rust 1.77.2 (we pin
    // 1.96.0) std detects a .bat/.cmd program and spawns it through cmd.exe WITH the CVE-fixed
    // per-arg escaping, so plain Command::new(program) is both correct and injection-safe.
    Command::new(program)
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

    let path_candidates = rg_path_candidates();
    let runtime_relative_rg = resolve_existing_relative_to_current_exe(&[
        &[if cfg!(windows) { "rg.exe" } else { "rg" }],
        &[
            "benchmarks",
            WINDOWS_RG_DIRNAME,
            if cfg!(windows) { "rg.exe" } else { "rg" },
        ],
        &["benchmarks", "rg"],
    ]);

    select_ripgrep_binary_candidate(path_candidates, runtime_relative_rg)
}

fn select_ripgrep_binary_candidate(
    path_candidates: Vec<PathBuf>,
    bundled_fallback: Option<PathBuf>,
) -> Option<PathBuf> {
    if let Some(candidate) = path_candidates
        .into_iter()
        .find(|candidate| candidate.is_file())
    {
        return Some(candidate);
    }
    bundled_fallback
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

    #[test]
    fn resolve_ripgrep_binary_prefers_path_candidate_before_bundled_fallback() {
        let dir = tempfile::tempdir().unwrap();
        let path_rg = dir.path().join(if cfg!(windows) { "rg.exe" } else { "rg" });
        let bundled_rg =
            dir.path()
                .join("bundled")
                .join(if cfg!(windows) { "rg.exe" } else { "rg" });
        std::fs::write(&path_rg, "path").unwrap();
        std::fs::create_dir_all(bundled_rg.parent().unwrap()).unwrap();
        std::fs::write(&bundled_rg, "bundled").unwrap();

        let selected = select_ripgrep_binary_candidate(vec![path_rg.clone()], Some(bundled_rg));

        assert_eq!(selected, Some(path_rg));
    }

    fn args_with(patterns: Vec<&str>, paths: Vec<&str>, files: bool) -> RipgrepSearchArgs {
        let mut args = RipgrepSearchArgs::default();
        args.patterns = patterns.into_iter().map(String::from).collect();
        args.paths = paths.into_iter().map(String::from).collect();
        args.files = files;
        args
    }

    #[test]
    fn operand_args_insert_end_of_options_sentinel_before_paths() {
        // A path beginning with `-` must land AFTER `--` so rg treats it as a path, not a flag
        // (CWE-88: `--pre=CMD` would otherwise be an option -> RCE).
        let args = args_with(vec!["TODO"], vec!["--pre=/bin/sh", "src"], false);
        let operands = ripgrep_operand_args(&args);
        let sentinel = operands
            .iter()
            .position(|a| a == "--")
            .expect("`--` sentinel present");
        let evil = operands.iter().position(|a| a == "--pre=/bin/sh").unwrap();
        let path = operands.iter().position(|a| a == "src").unwrap();
        assert!(
            sentinel < evil,
            "the -- sentinel must precede the injected path"
        );
        assert!(sentinel < path);
        // Patterns stay flag-safe via -e, before the sentinel.
        assert_eq!(operands[0], "-e");
        assert_eq!(operands[1], "TODO");
    }

    #[test]
    fn operand_args_no_sentinel_when_no_paths() {
        // No user path -> nothing to protect -> no trailing `--` (preserves the piped-stdin /
        // no-default-path invocation the parity tests pin).
        let operands = ripgrep_operand_args(&args_with(vec!["x"], vec![], false));
        assert!(!operands.contains(&"--".to_string()));
        assert_eq!(operands, vec!["-e".to_string(), "x".to_string()]);
    }

    #[test]
    fn operand_args_files_mode_omits_patterns_but_keeps_sentinel() {
        // --files mode emits no -e patterns, but paths must still be sentinel-guarded.
        let operands = ripgrep_operand_args(&args_with(vec!["ignored"], vec!["-l"], true));
        assert!(!operands.iter().any(|a| a == "-e"));
        let sentinel = operands.iter().position(|a| a == "--").unwrap();
        let path = operands.iter().position(|a| a == "-l").unwrap();
        assert!(sentinel < path);
    }

    // --- Audit #100: implicit-walk-ceiling hoist -----------------------------------------

    fn _make_stub_file_dir(dir: &std::path::Path, file_count: usize) {
        for index in 0..file_count {
            std::fs::write(
                dir.join(format!("stub_{index}.py")),
                "nothing interesting\n",
            )
            .unwrap();
        }
    }

    #[test]
    fn check_implicit_walk_ceiling_refuses_oversized_implicit_walk_with_glob() {
        // RED-before-fix (audit #100): `tg search -e "TODO" --glob "*.py"` with no explicit PATH
        // on the native binary used to bypass the ceiling check entirely -- `parse_early_
        // ripgrep_args`'s `-e` arm defaulted `paths` to `["."]` with no `path_was_implicit`
        // record, so no caller could gate on it. This is the exact shape of that bypass.
        let dir = tempfile::tempdir().unwrap();
        _make_stub_file_dir(dir.path(), 1600);
        let dir_path = dir.path().to_string_lossy().to_string();
        let mut args = args_with(vec!["TODO"], vec![&dir_path], false);
        args.globs = vec!["*.py".to_string()];
        args.path_was_implicit = true;

        let refusal = check_implicit_walk_ceiling(&args);

        assert!(
            refusal.is_some(),
            "an oversized implicit-path walk with --glob must be refused"
        );
    }

    #[test]
    fn check_implicit_walk_ceiling_refuses_bare_oversized_implicit_walk_without_glob() {
        // #105 (bundled into this fix, per the design's LEAN-to-include decision): a BARE
        // unfiltered implicit-path search (no --glob/--type at all) on a huge root must also be
        // refused -- the hoisted gate fires on `path_was_implicit` alone now, not
        // `path_was_implicit && (glob-or-type)`. A normal <1500-file repo is unaffected.
        let dir = tempfile::tempdir().unwrap();
        _make_stub_file_dir(dir.path(), 1600);
        let dir_path = dir.path().to_string_lossy().to_string();
        let mut args = args_with(vec!["TODO"], vec![&dir_path], false);
        args.path_was_implicit = true;
        assert!(args.globs.is_empty() && args.file_types.is_empty());

        let refusal = check_implicit_walk_ceiling(&args);

        assert!(
            refusal.is_some(),
            "a bare (no glob/type) oversized implicit-path walk must also be refused (#105)"
        );
    }

    #[test]
    fn check_implicit_walk_ceiling_allows_explicit_path_even_when_oversized() {
        // Non-regression (Trap #3 parity): an EXPLICIT, deliberately-scoped PATH must never be
        // refused regardless of size -- only an IMPLICIT (defaulted) root is gated.
        let dir = tempfile::tempdir().unwrap();
        _make_stub_file_dir(dir.path(), 1600);
        let dir_path = dir.path().to_string_lossy().to_string();
        let mut args = args_with(vec!["TODO"], vec![&dir_path], false);
        args.globs = vec!["*.py".to_string()];
        args.path_was_implicit = false;

        let refusal = check_implicit_walk_ceiling(&args);

        assert!(
            refusal.is_none(),
            "an explicit path must run uninhibited even when the walk exceeds the ceiling"
        );
    }

    #[test]
    fn check_implicit_walk_ceiling_allows_implicit_path_under_ceiling() {
        // Normal-case non-regression: an implicit path under the ceiling is unaffected.
        let dir = tempfile::tempdir().unwrap();
        _make_stub_file_dir(dir.path(), 50);
        let dir_path = dir.path().to_string_lossy().to_string();
        let mut args = args_with(vec!["TODO"], vec![&dir_path], false);
        args.path_was_implicit = true;

        let refusal = check_implicit_walk_ceiling(&args);

        assert!(
            refusal.is_none(),
            "a 50-file implicit root must not be refused"
        );
    }

    #[test]
    fn check_implicit_walk_ceiling_substitutes_dot_root_for_empty_paths() {
        // Mirrors the (now-deleted) `handle_ripgrep_search` call site's own substitution: a
        // caller that leaves `paths` empty while `path_was_implicit` is true (e.g. the
        // frontdoor's `-e` arm when stdin is readable) still gets probed against `.` rather than
        // silently skipped. Uses the current directory implicitly via `["."]`, so just assert
        // this does not panic and returns a bool-shaped Option either way.
        let mut args = args_with(vec!["TODO"], vec![], false);
        args.path_was_implicit = true;
        assert!(args.paths.is_empty());

        // Must not panic; the concrete refuse/allow verdict depends on the test runner's cwd
        // file count, which this test does not control -- only the substitution's hermetic
        // safety (no panic on an empty roots list) is under test here.
        let _ = check_implicit_walk_ceiling(&args);
    }

    #[test]
    fn execute_ripgrep_search_refuses_oversized_implicit_walk_before_spawning_rg() {
        // The hermetic through-`execute_ripgrep_search` test the design calls for: now that the
        // check is the FIRST statement (before `resolve_ripgrep_binary()`), this is hermetic --
        // no real `rg` subprocess is ever spawned when the gate fires. Bounded per
        // anti-hang-test-protocol: run on a joined worker thread with an explicit timeout so a
        // regression (the check silently stops being first, or stops firing) that falls through
        // to a REAL `rg` subprocess with `Stdio::inherit()` and no timeout cannot hang the test
        // runner -- it fails fast with a clear panic message instead.
        let dir = tempfile::tempdir().unwrap();
        _make_stub_file_dir(dir.path(), 1600);
        let dir_path = dir.path().to_string_lossy().to_string();
        let mut args = args_with(vec!["TODO"], vec![&dir_path], false);
        args.globs = vec!["*.py".to_string()];
        args.path_was_implicit = true;

        let (tx, rx) = std::sync::mpsc::channel();
        std::thread::spawn(move || {
            let result = execute_ripgrep_search(&args).map_err(|error| error.to_string());
            let _ = tx.send(result);
        });
        let result = rx.recv_timeout(std::time::Duration::from_secs(10)).expect(
            "execute_ripgrep_search must return well within 10s -- a hang here means the \
             walk-ceiling check did not fire before an unbounded rg subprocess spawn",
        );

        assert_eq!(
            result.expect("must return Ok, not Err"),
            2,
            "an oversized implicit-path walk with --glob must be refused with exit code 2"
        );
    }

    // --- Audit #105: native-CPU implicit-walk-ceiling refusal recognition ------------------

    #[test]
    fn is_unbounded_implicit_search_walk_refusal_recognizes_the_shared_message() {
        // The native-CPU engine (native_search.rs) reuses `format_unbounded_implicit_search_walk_
        // error` verbatim for its own refusal (audit #105), and main.rs's multi-pattern call
        // sites use this predicate to normalize that refusal to exit code 2. Both directions must
        // hold: the real generated message is recognized, and an unrelated error is not.
        let refusal = format_unbounded_implicit_search_walk_error(IMPLICIT_SEARCH_WALK_FILE_CEILING);
        assert!(is_unbounded_implicit_search_walk_refusal(&refusal));
        assert!(!is_unbounded_implicit_search_walk_refusal(
            "native search path does not exist: /nope"
        ));
    }
}

#![allow(unused_imports, dead_code)]
use crate::native_multi_pattern::collect_native_multi_pattern_matches;
use crate::validation_rollback::{
    collect_validation_rollback_snapshots, emit_rollback_status,
    restore_validation_rollback_snapshots, retained_validation_targets, ValidationRollbackSummary,
};
use anyhow::Context;
#[cfg(feature = "cuda")]
use clap::ValueEnum;
use clap::{Args, CommandFactory, Parser, Subcommand};
use hmac::{Hmac, Mac};
use tensor_grep_rs::rg_passthrough::generated_scan_dir_names;
// Bug #88/#480/#100: `implicit_search_walk_exceeds_ceiling` (the WALK-ceiling probe) moved to
// `rg_passthrough.rs` (a library module, not this binary crate root) -- see the breadcrumb
// comment above `parse_early_ripgrep_args`. `WalkBuilder`'s only remaining consumer in THIS file
// is `count_search_corpus_bytes`, which is cuda-gated, so both imports are cuda-gated now.
use crate::main_env::env_flag_enabled;
use crate::search_files::format_broad_generated_scan_error;
use crate::search_flag_registry::{
    raw_args_contain_any_flag, search_args_contain_any_flag, SEARCH_OPTION_FIRST_FLAGS,
    SEARCH_PYTHON_PASSTHROUGH_FLAGS,
};
#[cfg(feature = "cuda")]
use ignore::overrides::OverrideBuilder;
#[cfg(feature = "cuda")]
use ignore::WalkBuilder;
use process_control::{ChildExt, Control};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet};
use std::env;
use std::ffi::{OsStr, OsString};
#[cfg(feature = "cuda")]
use std::fs;
use std::io::{self, Read, Write};
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::{Mutex, OnceLock};
use std::time::Instant;
use std::time::{Duration, SystemTime, UNIX_EPOCH};
use tensor_grep_rs::backend_ast::{
    AstBackend, AstMatch, AstMetaVariables, BatchRewritePlan, BatchRewriteRule,
};
use tensor_grep_rs::broken_pipe::error_chain_has_broken_pipe;
use tensor_grep_rs::crossover::{
    run_crossover_calibration, skip_signal_payload, write_crossover_config, NoCudaBuildError,
};
use tensor_grep_rs::exit_codes::exit_with_run_error;
#[cfg(feature = "cuda")]
use tensor_grep_rs::gpu_native::{
    benchmark_cuda_graph_search_paths, benchmark_pageable_transfer_throughput,
    benchmark_pinned_transfer_throughput, enumerate_cuda_devices, gpu_native_search_paths_multi,
    probe_device_allocation, GpuNativeSearchConfig, GpuNativeSearchStats, GpuPipelineStats,
};
use tensor_grep_rs::index::TrigramIndex;
use tensor_grep_rs::index_mmap::{mmap_index_path, LoadedIndex, MmapIndex};
use tensor_grep_rs::native_search::{
    defaulted_scope_fields, emit_index_binary_match_warning, native_json_text_fields,
    native_search_pattern_compiles, run_native_search, smart_case_pattern_is_case_insensitive,
    NativeSearchConfig, SearchStats,
};
use tensor_grep_rs::python_sidecar::{
    execute_python_passthrough_command, execute_python_passthrough_command_captured,
    execute_python_passthrough_command_with_stdin, execute_sidecar_command, SidecarError,
};
use tensor_grep_rs::rg_passthrough::{
    apply_last_ignore_wins, execute_ripgrep_pcre2_version, execute_ripgrep_search,
    execute_ripgrep_type_list, extract_search_tail_with_matches, implicit_search_paths,
    is_unbounded_implicit_search_walk_refusal, ripgrep_is_available, RipgrepSearchArgs,
};
use tensor_grep_rs::routing::{
    gpu_proof_fields, native_can_serve_plain_text, plain_text_native_cheap_checks_pass,
    plain_text_native_flag_token_is_allowed, route_search, BackendSelection, IndexRoutingState,
    PlainTextNativeRequest, RoutingDecision, SearchRoutingCalibration, SearchRoutingConfig,
};
use tensor_grep_rs::search_error::{
    self, exit_json_search_runtime_error_if_needed, exit_structured_search_error_if_needed,
    handle_search_runtime_error, refuse_structured_pcre2,
};

// Root identity also appears in positional help generated from this binary.
use crate::cli_app::*;
use crate::*;

pub(crate) const NATIVE_TOP_LEVEL_ABOUT: &str =
    "Search code, follow symbols, and prepare a bounded edit.";
pub(crate) const JSON_OUTPUT_VERSION: u32 = 1;
pub(crate) const TG_RUST_EARLY_RG_ENV: &str = "TG_RUST_EARLY_RG";
pub(crate) const TG_RUST_EARLY_POSITIONAL_RG_ENV: &str = "TG_RUST_EARLY_POSITIONAL_RG";
/// Default --lint-cmd/--test-cmd timeout, matching apply_policy.py's `_run_policy_command` default
/// (see apply_policy.py:256-260) so the Rust `tg run --apply` path never hangs longer than the
/// Python validation path does.
pub(crate) const DEFAULT_VALIDATION_TIMEOUT_MS: u64 = 120_000;
pub(crate) const TG_VALIDATION_TIMEOUT_MS_ENV: &str = "TG_VALIDATION_TIMEOUT_MS";
/// Default cap on the number of per-file validation targets spawned by a single `--lint-cmd`/
/// `--test-cmd` run (audit #34): an 800-edited-file batch-rewrite would otherwise fan out 800
/// serial subprocesses per command.
pub(crate) const DEFAULT_MAX_VALIDATION_TARGETS: usize = 50;
#[derive(Parser, Debug)]
#[command(name = "tg")]
#[command(bin_name = "tg")]
#[command(version)]
#[command(about = NATIVE_TOP_LEVEL_ABOUT)]
#[command(disable_help_subcommand = true)]
pub struct CommandCli {
    #[command(subcommand)]
    pub command: Commands,
}

#[derive(Parser, Debug)]
#[command(name = "tg")]
#[command(version)]
#[command(about = NATIVE_TOP_LEVEL_ABOUT)]
#[command(disable_help_subcommand = true)]
pub struct PositionalCli {
    /// The search pattern (regex or string)
    pub pattern: Option<String>,

    /// Paths to search
    #[arg(value_name = "PATH")]
    pub path: Vec<String>,

    /// Count matching lines
    #[arg(short = 'c', long)]
    pub count: bool,

    /// Show only the total number of matches per file
    #[arg(long = "count-matches")]
    pub count_matches: bool,

    /// Show line numbers
    #[arg(short = 'n', long)]
    pub line_number: bool,

    /// Suppress line numbers
    #[arg(short = 'N', long = "no-line-number")]
    pub no_line_number: bool,

    /// Show column numbers
    #[arg(long)]
    pub column: bool,

    /// Stop after NUM matching lines per file
    #[arg(short = 'm', long)]
    pub max_count: Option<usize>,

    /// Fixed string matching (disable regex)
    #[arg(short = 'F', long)]
    pub fixed_strings: bool,

    /// Invert match (select non-matching lines)
    #[arg(short = 'v', long)]
    pub invert_match: bool,

    /// Case insensitive search
    #[arg(short = 'i', long)]
    pub ignore_case: bool,

    /// Show matches with word boundaries
    #[arg(short = 'w', long)]
    pub word_regexp: bool,

    /// Replace matches in emitted output (ripgrep-style)
    #[arg(short = 'r', long)]
    pub replace: Option<String>,

    /// Force the native CPU engine
    #[arg(long = "cpu", alias = "force-cpu")]
    pub force_cpu: bool,

    /// Route search to GPU backends via Python sidecar (comma-separated device IDs)
    #[arg(long = "gpu-device-ids", value_delimiter = ',')]
    pub gpu_device_ids: Vec<i32>,

    /// Output coloring (auto, always, never)
    #[arg(long)]
    pub color: Option<String>,

    /// Path separator to use when printing file paths
    #[arg(long = "path-separator")]
    pub path_separator: Option<String>,

    /// Print only the matched parts of a line
    #[arg(short = 'o', long)]
    pub only_matching: bool,

    /// Print results in Vim quickfix format
    #[arg(long)]
    pub vimgrep: bool,

    /// Emit tensor-grep aggregate JSON (not rg JSON Lines)
    #[arg(long, conflicts_with = "ndjson")]
    pub json: bool,

    /// Emit tensor-grep newline-delimited JSON rows (not the rg event schema)
    #[arg(long, conflicts_with = "json")]
    pub ndjson: bool,

    /// Emit routing metadata on stderr before executing the search
    #[arg(long)]
    pub verbose: bool,

    /// Use PCRE2 regex engine
    #[arg(short = 'P', long)]
    pub pcre2: bool,

    /// Enable automatic hybrid regex routing when ripgrep is used
    #[arg(long = "auto-hybrid-regex")]
    pub auto_hybrid_regex: bool,

    /// Enable Unicode regex mode. This is the default; accepted for rg CLI compatibility.
    #[arg(long)]
    pub unicode: bool,

    /// Enable PCRE2 Unicode mode. Alias of --unicode in ripgrep; accepted for rg CLI compatibility.
    #[arg(long = "pcre2-unicode")]
    pub pcre2_unicode: bool,

    /// Ignore files larger than this size (e.g. 10MB)
    #[arg(long)]
    pub max_filesize: Option<String>,

    /// Ignore configured ignore files
    #[arg(long = "no-ignore")]
    pub no_ignore: bool,

    /// Respect configured ignore files. This is the default; accepted for rg CLI compatibility.
    #[arg(long = "ignore")]
    pub ignore: bool,

    /// Show normal diagnostic messages. This is the default; accepted for rg CLI compatibility.
    #[arg(long = "messages")]
    pub messages: bool,

    /// Require a git repository before respecting git ignore rules.
    #[arg(long = "require-git")]
    pub require_git: bool,

    /// Do not search hidden files and directories. This is the default; accepted for rg CLI compatibility.
    #[arg(long = "no-hidden")]
    pub no_hidden: bool,

    /// Don't respect source control ignore files
    #[arg(long)]
    pub no_ignore_vcs: bool,
}

#[derive(Args, Debug, Clone)]
pub struct SearchArgs {
    /// Case insensitive search
    #[arg(short = 'i', long)]
    pub ignore_case: bool,

    /// Fixed string matching (disable regex)
    #[arg(short = 'F', long)]
    pub fixed_strings: bool,

    /// Disable fixed-string mode; useful for rg config overrides.
    #[arg(long = "no-fixed-strings")]
    pub no_fixed_strings: bool,

    /// Invert match (select non-matching lines)
    #[arg(short = 'v', long)]
    pub invert_match: bool,

    /// Disable inverted matching; useful for rg config overrides.
    #[arg(long = "no-invert-match")]
    pub no_invert_match: bool,

    /// Count matching lines
    #[arg(short = 'c', long)]
    pub count: bool,

    /// Show only the total number of matches per file
    #[arg(long = "count-matches")]
    pub count_matches: bool,

    /// Show line numbers
    #[arg(short = 'n', long)]
    pub line_number: bool,

    /// Suppress line numbers
    #[arg(short = 'N', long = "no-line-number")]
    pub no_line_number: bool,

    /// Show column numbers
    #[arg(long)]
    pub column: bool,

    /// Do not show column numbers; useful for rg config overrides.
    #[arg(long = "no-column")]
    pub no_column: bool,

    /// Replace matches in emitted output (ripgrep-style)
    #[arg(short = 'r', long)]
    pub replace: Option<String>,

    /// Output format. `rg` is handled by the native front door; other formats stay on Python.
    #[arg(long = "format")]
    pub format: Option<String>,

    /// Sort results by field (for rg-compatible passthrough output)
    #[arg(long)]
    pub sort: Option<String>,

    /// Sort results in reverse by field (for rg-compatible passthrough output)
    #[arg(long = "sortr")]
    pub sort_reverse: Option<String>,

    /// Deprecated ripgrep alias for --sort path
    #[arg(long = "sort-files")]
    pub sort_files: bool,

    /// Follow file paths with a NUL byte
    #[arg(short = '0', long = "null")]
    pub null: bool,

    /// Use NUL as a line terminator instead of newline
    #[arg(long = "null-data")]
    pub null_data: bool,

    /// Enable searching across multiple lines
    #[arg(short = 'U', long = "multiline")]
    pub multiline: bool,

    /// Enable dot-all mode for multiline searches
    #[arg(long = "multiline-dotall")]
    pub multiline_dotall: bool,

    /// Show NUM context lines before and after each match
    #[arg(short = 'C', long)]
    pub context: Option<usize>,

    /// Show NUM context lines after each match
    #[arg(short = 'A', long = "after-context")]
    pub after_context: Option<usize>,

    /// Show NUM context lines before each match
    #[arg(short = 'B', long = "before-context")]
    pub before_context: Option<usize>,

    /// Stop after NUM matching lines per file
    #[arg(short = 'm', long)]
    pub max_count: Option<usize>,

    /// Limit depth of directory traversal
    #[arg(short = 'd', long = "max-depth", visible_alias = "maxdepth")]
    pub max_depth: Option<usize>,

    /// Show matches with word boundaries
    #[arg(short = 'w', long)]
    pub word_regexp: bool,

    /// Search case insensitively if the pattern is all lowercase
    #[arg(short = 'S', long = "smart-case")]
    pub smart_case: bool,

    /// Include/exclude files matching glob
    #[arg(short = 'g', long = "glob")]
    pub globs: Vec<String>,

    /// Ignore .gitignore / ignore files
    #[arg(long = "no-ignore")]
    pub no_ignore: bool,

    /// Respect .gitignore / ignore files. This is the default; accepted for rg CLI compatibility.
    #[arg(long = "ignore")]
    pub ignore: bool,

    /// Don't respect .ignore or .rgignore files
    #[arg(long = "no-ignore-dot")]
    pub no_ignore_dot: bool,

    /// Don't respect .git/info/exclude
    #[arg(long = "no-ignore-exclude")]
    pub no_ignore_exclude: bool,

    /// Ignore any --ignore-file flags
    #[arg(long = "no-ignore-files")]
    pub no_ignore_files: bool,

    /// Don't respect global gitignore
    #[arg(long = "no-ignore-global")]
    pub no_ignore_global: bool,

    /// Don't respect ignore files in parent directories
    #[arg(long = "no-ignore-parent")]
    pub no_ignore_parent: bool,

    /// Search hidden files and directories
    #[arg(short = '.', long)]
    pub hidden: bool,

    /// Do not search hidden files and directories. This is the default; accepted for rg CLI compatibility.
    #[arg(long = "no-hidden")]
    pub no_hidden: bool,

    /// Follow symbolic links
    #[arg(short = 'L', long)]
    pub follow: bool,

    /// Search binary files as if they were text
    #[arg(short = 'a', long)]
    pub text: bool,

    /// Print only paths with at least one match
    #[arg(short = 'l', long = "files-with-matches")]
    pub files_with_matches: bool,

    /// Print only paths containing zero matches
    #[arg(long = "files-without-match")]
    pub files_without_match: bool,

    /// Only search files matching TYPE
    #[arg(short = 't', long = "type")]
    pub file_type: Vec<String>,

    /// Use trigram index for accelerated repeated queries
    #[arg(long)]
    pub index: bool,

    /// Force the native CPU engine
    #[arg(long = "cpu", alias = "force-cpu")]
    pub force_cpu: bool,

    /// Route search to GPU backends via Python sidecar (comma-separated device IDs)
    #[arg(long = "gpu-device-ids", value_delimiter = ',')]
    pub gpu_device_ids: Vec<i32>,

    /// Output coloring (auto, always, never)
    #[arg(long)]
    pub color: Option<String>,

    /// Path separator to use when printing file paths
    #[arg(long = "path-separator")]
    pub path_separator: Option<String>,

    /// Print only the matched parts of a line
    #[arg(short = 'o', long)]
    pub only_matching: bool,

    /// Print results in Vim quickfix format
    #[arg(long)]
    pub vimgrep: bool,

    /// Print both matching and non-matching lines
    #[arg(long = "passthru", alias = "passthrough")]
    pub passthru: bool,

    /// Emit machine-readable routing metadata as JSON
    #[arg(long, conflicts_with = "ndjson")]
    pub json: bool,

    /// Emit one JSON object per matching line (newline-delimited)
    #[arg(long, conflicts_with = "json")]
    pub ndjson: bool,

    /// Emit routing metadata on stderr before executing the search
    #[arg(long)]
    pub verbose: bool,

    /// A pattern to search for. Can be provided multiple times.
    #[arg(short = 'e', long = "regexp", allow_hyphen_values = true)]
    pub regexp: Vec<String>,

    /// The search pattern (regex or string)
    #[arg(required_unless_present_any = ["regexp", "pcre2_version", "type_list", "version", "files"])]
    pub pattern: Option<String>,

    /// Print files that would be searched and exit
    #[arg(long)]
    pub files: bool,

    /// Allow unbounded scans across generated/dependency directories
    #[arg(long = "allow-broad-generated-scan")]
    pub allow_broad_generated_scan: bool,

    /// Paths to search
    #[arg(value_name = "PATH")]
    pub path: Vec<String>,

    /// Use PCRE2 regex engine
    #[arg(short = 'P', long)]
    pub pcre2: bool,

    /// Enable automatic hybrid regex routing when ripgrep is used
    #[arg(long = "auto-hybrid-regex")]
    pub auto_hybrid_regex: bool,

    /// Enable Unicode regex mode. This is the default; accepted for rg CLI compatibility.
    #[arg(long)]
    pub unicode: bool,

    /// Enable PCRE2 Unicode mode. Alias of --unicode in ripgrep; accepted for rg CLI compatibility.
    #[arg(long = "pcre2-unicode")]
    pub pcre2_unicode: bool,

    /// Ignore files larger than this size (e.g. 10MB)
    #[arg(long)]
    pub max_filesize: Option<String>,

    /// Don't respect source control ignore files
    #[arg(long)]
    pub no_ignore_vcs: bool,

    /// Require a git repository before respecting git ignore rules.
    #[arg(long = "require-git")]
    pub require_git: bool,

    /// Show normal diagnostic messages. This is the default; accepted for rg CLI compatibility.
    #[arg(long = "messages")]
    pub messages: bool,

    /// Never read configuration files
    #[arg(long = "no-config")]
    pub no_config: bool,

    /// Show the version of PCRE2 used
    #[arg(long)]
    pub pcre2_version: bool,

    /// Show all supported file types
    #[arg(long = "type-list")]
    pub type_list: bool,

    /// Show tensor-grep version
    #[arg(long = "version", short = 'V')]
    pub version: bool,
}

#[derive(Args, Debug, Clone)]
pub struct RunArgs {
    /// The AST language to use
    #[arg(long, default_value = "python")]
    pub lang: String,

    /// The structural pattern to match
    #[arg(short = 'p', long = "pattern")]
    pub pattern_option: Option<String>,

    /// Rewrite matched nodes with this replacement pattern (metavar substitution supported)
    #[arg(short = 'r', long, conflicts_with = "batch_rewrite")]
    pub rewrite: Option<String>,

    /// Apply multiple rewrite rules from a JSON config file
    #[arg(long = "batch-rewrite", conflicts_with = "rewrite")]
    pub batch_rewrite: Option<PathBuf>,

    /// Apply rewrite edits to files (requires --rewrite)
    #[arg(long)]
    pub apply: bool,

    /// ast-grep-compatible alias for applying all rewrite edits (requires --rewrite)
    #[arg(short = 'U', long = "update-all")]
    pub update_all: bool,

    /// Show unified diff preview of rewrites (requires --rewrite)
    #[arg(long)]
    pub diff: bool,

    /// Verify rewrites after apply by re-searching for replacement pattern
    #[arg(long)]
    pub verify: bool,

    /// Run this command after apply/verify and capture structured lint results
    #[arg(long = "lint-cmd")]
    pub lint_cmd: Option<String>,

    /// Run this command after apply/verify and capture structured test results
    #[arg(long = "test-cmd")]
    pub test_cmd: Option<String>,

    /// Kill a --lint-cmd/--test-cmd validation command that runs past this many milliseconds
    /// (env TG_VALIDATION_TIMEOUT_MS; default 120000, parity with the Python apply-policy path)
    #[arg(long = "validation-timeout-ms")]
    pub validation_timeout_ms: Option<u64>,

    /// Cap the number of per-file --lint-cmd/--test-cmd targets spawned in one run (0 disables the cap)
    #[arg(long = "max-validation-targets", default_value_t = DEFAULT_MAX_VALIDATION_TARGETS)]
    pub max_validation_targets: usize,

    /// Create a rollback checkpoint before applying rewrite edits
    #[arg(long)]
    pub checkpoint: bool,

    /// Write a deterministic rewrite audit manifest for applied edits
    #[arg(long = "audit-manifest")]
    pub audit_manifest: Option<PathBuf>,

    /// Sign the audit manifest using an HMAC-SHA256 key file
    #[arg(long = "audit-signing-key", requires = "audit_manifest")]
    pub audit_signing_key: Option<PathBuf>,

    /// Apply only the specified comma-delimited rewrite edit IDs
    #[arg(
        long = "apply-edit-ids",
        value_delimiter = ',',
        conflicts_with = "reject_edit_ids"
    )]
    pub apply_edit_ids: Vec<String>,

    /// Apply all planned rewrite edits except the specified comma-delimited edit IDs
    #[arg(
        long = "reject-edit-ids",
        value_delimiter = ',',
        conflicts_with = "apply_edit_ids"
    )]
    pub reject_edit_ids: Vec<String>,

    /// Emit machine-readable routing metadata as JSON
    #[arg(long)]
    pub json: bool,

    /// Emit routing metadata on stderr before executing the search
    #[arg(long)]
    pub verbose: bool,

    /// Print only paths with at least one AST match
    #[arg(long = "files-with-matches")]
    pub files_with_matches: bool,

    /// ast-grep matcher selector for read-only structural search
    #[arg(long)]
    pub selector: Option<String>,

    /// ast-grep strictness control for read-only structural search
    #[arg(long)]
    pub strictness: Option<String>,

    /// Read source code from stdin for read-only structural search
    #[arg(long = "stdin")]
    pub stdin_flag: bool,

    /// ast-grep include/exclude glob. Repeat for multiple globs; prefix with ! to exclude.
    #[arg(long = "globs")]
    pub globs: Vec<String>,

    /// Positional PATTERN and optional PATH, or just PATH when --pattern is used
    #[arg(value_name = "PATTERN_OR_PATH")]
    pub positional: Vec<String>,
}

#[derive(Args, Debug, Clone, Default)]
pub struct CalibrateArgs {
    /// Emit JSON, incl. `calibration_status: skipped_no_cuda_build` when GPU calibration cannot run
    /// (needs NVIDIA hardware + a CUDA-enabled binary). Exit code unchanged: still 2 on the no-cuda
    /// skip (backend-unavailable convention); the success path already emits JSON.
    #[arg(long)]
    pub json: bool,
}

#[derive(Args, Debug, Clone)]
pub struct AuditVerifyArgs {
    /// Path to the rewrite audit manifest JSON file
    pub manifest_path: PathBuf,

    /// Optional HMAC signing key path for signed manifests
    #[arg(long = "signing-key")]
    pub signing_key: Option<PathBuf>,

    /// Optional previous manifest path for validating manifest chaining
    #[arg(long = "previous-manifest")]
    pub previous_manifest: Option<PathBuf>,

    /// Emit structured JSON verification output
    #[arg(long)]
    pub json: bool,
}

#[derive(Args, Debug, Clone)]
pub struct ClassifyArgs {
    /// Output format
    #[arg(long = "format")]
    pub format: Option<String>,

    /// Emit JSON output
    #[arg(long = "json")]
    pub json: bool,

    /// Maximum input lines to emit in JSON output (0 disables the cap)
    #[arg(long = "max-lines", default_value_t = 500)]
    pub max_lines: usize,

    /// Read the text to classify from stdin instead of a file (mutually exclusive with --text)
    #[arg(long = "stdin")]
    pub stdin_flag: bool,

    /// Classify a literal string instead of a file or stdin (mutually exclusive with --stdin)
    #[arg(long = "text", value_name = "TEXT")]
    pub text: Option<String>,

    /// The log file to classify (omit when using --stdin or --text)
    pub file_path: Option<String>,
}

#[derive(Subcommand, Debug)]
pub enum Commands {
    /// Search for a regex pattern with the validated rg-compatible surface
    Search(SearchArgs),
    /// Measure CPU vs GPU crossover thresholds and persist smart-routing calibration
    Calibrate(CalibrateArgs),
    /// Upgrade tensor-grep via the managed Python package path
    #[command(name = "upgrade", bin_name = "tg upgrade")]
    Upgrade,
    /// Upgrade tensor-grep via the managed Python package path
    #[command(name = "update", bin_name = "tg update")]
    Update,
    /// Verify a rewrite audit manifest digest, chain, and optional signature
    #[command(name = "audit-verify")]
    AuditVerify(AuditVerifyArgs),
    /// Show audit command entry points
    #[command(name = "audit", disable_help_flag = true)]
    Audit {
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
        args: Vec<String>,
    },
    /// Start the AI-assistant Model Context Protocol (MCP) server
    Mcp,
    /// Run log classification with local heuristics; CyBERT/Triton is opt-in
    Classify(ClassifyArgs),
    /// Run a validated AST slice for structural search and guarded rewrites
    #[command(
        long_about = "Run a validated AST slice for structural search and guarded rewrites.\n\nPatterns match exact AST node shapes: a single-line 'def $NAME($$$ARGS): $$$BODY' does not match a multiline function block. Multiline examples -- Python: 'def $NAME($$$ARGS):\\n    $$$BODY'; TypeScript: 'function $NAME($$$ARGS) {\\n  $$$BODY\\n}'. Use `--selector function_definition` to match any function regardless of formatting."
    )]
    Run(RunArgs),
    /// Scan code by configuration
    Scan {
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
        args: Vec<String>,
    },
    /// Test AST rules
    Test {
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
        args: Vec<String>,
    },
    /// Create a new AST project configuration
    #[command(disable_help_flag = true)]
    New {
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
        args: Vec<String>,
    },
    /// Start a resident AST worker
    #[command(hide = true)]
    Worker {
        /// TCP port to listen on
        #[arg(long, default_value = "9999")]
        port: u16,
        /// Stop the running worker
        #[arg(long)]
        stop: bool,
    },
    /// Start the Language Server Protocol (LSP) server
    #[command(disable_help_flag = true)]
    Lsp {
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
        args: Vec<String>,
    },
    /// Provision managed external LSP providers for optional semantic modes
    #[command(name = "lsp-setup", disable_help_flag = true)]
    LspSetup {
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
        args: Vec<String>,
    },
    #[cfg(feature = "cuda")]
    #[command(hide = true, name = "__gpu-native-stats")]
    GpuNativeStats(GpuNativeStatsArgs),
    #[cfg(feature = "cuda")]
    #[command(hide = true, name = "__gpu-transfer-bench")]
    GpuTransferBench(GpuTransferBenchArgs),
    #[cfg(feature = "cuda")]
    #[command(hide = true, name = "__gpu-cuda-graphs")]
    GpuCudaGraphs(GpuCudaGraphArgs),
    #[cfg(feature = "cuda")]
    #[command(hide = true, name = "__gpu-oom-probe")]
    GpuOomProbe(GpuOomProbeArgs),

    // Editor-plane and Python passthrough commands:
    /// Build a bounded repository map for agent context selection
    #[command(name = "map", disable_help_flag = true)]
    Map {
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
        args: Vec<String>,
    },
    /// Emit a one-call codebase orientation capsule (central files, entry points, AST snippets)
    #[command(name = "orient", disable_help_flag = true)]
    Orient {
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
        args: Vec<String>,
    },
    /// Render a persisted, browsable folder->file->symbol code map (lean index + per-folder pages)
    #[command(name = "codemap", disable_help_flag = true)]
    Codemap {
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
        args: Vec<String>,
    },
    /// List every signature in one file, without the bodies (the file's API surface)
    #[command(name = "file-api", disable_help_flag = true)]
    FileApi {
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
        args: Vec<String>,
    },
    /// Report whether tg's persisted state has drifted from the code
    #[command(name = "freshness", disable_help_flag = true)]
    Freshness {
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
        args: Vec<String>,
    },
    /// Report indexed, stale, and unparsed repository files without modifying state
    #[command(name = "coverage", disable_help_flag = true)]
    Coverage {
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
        args: Vec<String>,
    },
    /// Emit a single-pass repository inventory (files, bytes, languages, categories)
    #[command(name = "inventory", disable_help_flag = true)]
    Inventory {
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
        args: Vec<String>,
    },
    /// List source files not referenced by any governing doc (CLAUDE.md/README/AGENTS.md)
    #[command(name = "docs-coverage", disable_help_flag = true)]
    DocsCoverage {
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
        args: Vec<String>,
    },
    /// Open, query, and manage cached edit-loop sessions
    #[command(name = "session", disable_help_flag = true)]
    Session {
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
        args: Vec<String>,
    },
    /// Diagnose launcher, GPU, cache, daemon, and LSP readiness
    #[command(name = "doctor", disable_help_flag = true)]
    Doctor {
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
        args: Vec<String>,
    },
    /// Repair Windows Python subprocess tg resolution when explicitly allowed
    #[command(name = "repair-launcher", disable_help_flag = true)]
    RepairLauncher {
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
        args: Vec<String>,
    },
    /// Create, list, and undo edit checkpoints
    #[command(name = "checkpoint", disable_help_flag = true)]
    Checkpoint {
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
        args: Vec<String>,
    },
    /// Run tensor-grep self-check and dogfood diagnostics
    #[command(name = "dogfood", disable_help_flag = true)]
    Dogfood {
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
        args: Vec<String>,
    },
    /// Print source snippets for a resolved symbol
    #[command(name = "source", disable_help_flag = true)]
    Source {
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
        args: Vec<String>,
    },
    /// Estimate files impacted by a symbol or query
    #[command(name = "impact", disable_help_flag = true)]
    Impact {
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
        args: Vec<String>,
    },
    /// Find direct callers of a symbol
    #[command(name = "callers", disable_help_flag = true)]
    Callers {
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
        args: Vec<String>,
    },
    /// Show what a file imports, resolved to target files
    #[command(name = "imports", disable_help_flag = true)]
    Imports {
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
        args: Vec<String>,
    },
    /// Find the files that import a given file
    #[command(name = "importers", disable_help_flag = true)]
    Importers {
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
        args: Vec<String>,
    },
    /// Whole-repo hybrid semantic search (BM25 + dense), ranked file:line results
    #[command(name = "find", disable_help_flag = true)]
    Find {
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
        args: Vec<String>,
    },
    /// Build a transitive blast-radius graph for a symbol
    #[command(name = "blast-radius", disable_help_flag = true)]
    BlastRadius {
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
        args: Vec<String>,
    },
    /// Render a human-readable symbol blast radius
    #[command(name = "blast-radius-render", disable_help_flag = true)]
    BlastRadiusRender {
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
        args: Vec<String>,
    },
    /// Build a machine-readable blast-radius plan
    #[command(name = "blast-radius-plan", disable_help_flag = true)]
    BlastRadiusPlan {
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
        args: Vec<String>,
    },
    /// Analyze blast radius and risk of git diff changes
    #[command(name = "diff-impact", disable_help_flag = true)]
    DiffImpact {
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
        args: Vec<String>,
    },
    /// Build a machine-readable edit plan without rendered source
    #[command(name = "edit-plan", disable_help_flag = true)]
    EditPlan {
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
        args: Vec<String>,
    },
    /// Emit an actionable context capsule for agents
    #[command(name = "agent", disable_help_flag = true)]
    Agent {
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
        args: Vec<String>,
    },
    /// Render bounded prompt-ready context for a task
    #[command(name = "context-render", disable_help_flag = true)]
    ContextRender {
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
        args: Vec<String>,
    },
    /// Inspect AST language and parser support
    #[command(name = "ast-info", disable_help_flag = true)]
    AstInfo {
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
        args: Vec<String>,
    },
    /// List and inspect bundled scanning rulesets
    #[command(name = "rulesets", disable_help_flag = true)]
    Rulesets {
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
        args: Vec<String>,
    },
    /// Show audit manifest history
    #[command(name = "audit-history", disable_help_flag = true)]
    AuditHistory {
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
        args: Vec<String>,
    },
    /// Compare audit manifests
    #[command(name = "audit-diff", disable_help_flag = true)]
    AuditDiff {
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
        args: Vec<String>,
    },
    /// Create and verify review bundles
    #[command(name = "review-bundle", disable_help_flag = true)]
    ReviewBundle {
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
        args: Vec<String>,
    },
    /// Emit a versioned EvidenceReceipt aggregating existing tg outputs
    #[command(name = "evidence", disable_help_flag = true)]
    Evidence {
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
        args: Vec<String>,
    },
    /// Advisory, code-scoped agent-to-agent coordination claims
    #[command(name = "ledger", disable_help_flag = true)]
    Ledger {
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
        args: Vec<String>,
    },
    /// List GPU devices and routing readiness
    #[command(name = "devices", disable_help_flag = true)]
    Devices {
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
        args: Vec<String>,
    },
    /// Find symbol definitions
    #[command(name = "defs", disable_help_flag = true)]
    Defs {
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
        args: Vec<String>,
    },
    /// Find symbol references
    #[command(name = "refs", disable_help_flag = true)]
    Refs {
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
        args: Vec<String>,
    },
    /// Build a ranked context pack for a task
    #[command(name = "context", disable_help_flag = true)]
    Context {
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
        args: Vec<String>,
    },
    /// Diagnose context-render vs edit-plan routing agreement for a query
    #[command(name = "route-test", disable_help_flag = true)]
    RouteTest {
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
        args: Vec<String>,
    },
    /// One-shot install of the `tg find` / `--semantic` dense-embedding leg
    #[command(name = "install-dense", disable_help_flag = true)]
    InstallDense {
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
        args: Vec<String>,
    },
    /// Configure AI coding agents to use tensor-grep MCP
    #[command(name = "install", disable_help_flag = true)]
    Install {
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
        args: Vec<String>,
    },
    /// Remove tensor-grep MCP integration from AI coding agents
    #[command(name = "uninstall", disable_help_flag = true)]
    Uninstall {
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
        args: Vec<String>,
    },
    /// One-call edit-readiness capsule: primary target, blast-radius floor, validation, claims
    #[command(name = "prepare", disable_help_flag = true)]
    Prepare {
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
        args: Vec<String>,
    },
    /// Repair editable package metadata desync
    #[command(name = "repair-env", disable_help_flag = true)]
    RepairEnv {
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
        args: Vec<String>,
    },
    /// Query AST symbols using structured SQL
    #[command(name = "sql", disable_help_flag = true)]
    Sql {
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
        args: Vec<String>,
    },
    #[command(external_subcommand)]
    PythonPassthrough(Vec<String>),
}

#[cfg(feature = "cuda")]
#[derive(Args, Debug, Clone)]
pub struct GpuNativeStatsArgs {
    #[arg(long = "pattern", required = true)]
    pub patterns: Vec<String>,

    #[arg(long)]
    pub path: PathBuf,

    #[arg(long = "gpu-device-ids", value_delimiter = ',')]
    pub gpu_device_ids: Vec<i32>,

    #[arg(long = "no-ignore")]
    pub no_ignore: bool,

    #[arg(short = 'g', long = "glob")]
    pub globs: Vec<String>,

    #[arg(long)]
    pub max_batch_bytes: Option<usize>,

    #[arg(long)]
    pub summary_only: bool,
}

#[cfg(feature = "cuda")]
#[derive(Clone, Copy, Debug, Eq, PartialEq, ValueEnum)]
pub enum GpuTransferMemoryKind {
    Pinned,
    Pageable,
}

#[cfg(feature = "cuda")]
#[derive(Args, Debug, Clone)]
pub struct GpuTransferBenchArgs {
    #[arg(long)]
    pub device_id: i32,

    #[arg(long)]
    pub total_bytes: usize,

    #[arg(long)]
    pub batch_bytes: usize,

    #[arg(long, value_enum, default_value_t = GpuTransferMemoryKind::Pinned)]
    pub memory_kind: GpuTransferMemoryKind,
}

#[cfg(feature = "cuda")]
#[derive(Args, Debug, Clone)]
pub struct GpuCudaGraphArgs {
    #[arg(long = "pattern", required = true)]
    pub patterns: Vec<String>,

    #[arg(long)]
    pub path: PathBuf,

    #[arg(long)]
    pub device_id: i32,

    #[arg(long = "no-ignore")]
    pub no_ignore: bool,

    #[arg(short = 'g', long = "glob")]
    pub globs: Vec<String>,

    #[arg(long)]
    pub max_batch_bytes: Option<usize>,
}

#[cfg(feature = "cuda")]
#[derive(Args, Debug, Clone)]
pub struct GpuOomProbeArgs {
    #[arg(long)]
    pub device_id: i32,

    #[arg(long)]
    pub bytes: usize,
}

#[derive(Debug, Clone)]
pub(crate) struct ResolvedSearchRequest {
    pub(crate) patterns: Vec<String>,
    pub(crate) paths: Vec<String>,
    pub(crate) path_was_implicit: bool,
}

impl ResolvedSearchRequest {
    pub(crate) fn query_display(&self) -> String {
        if self.patterns.len() == 1 {
            self.patterns[0].clone()
        } else {
            self.patterns.join(" | ")
        }
    }

    pub(crate) fn primary_path(&self) -> &str {
        self.paths.first().map(String::as_str).unwrap_or(".")
    }

    pub(crate) fn path_display(&self) -> String {
        if self.paths.is_empty() {
            ".".to_string()
        } else {
            self.paths.join(" ")
        }
    }

    #[cfg(feature = "cuda")]
    pub(crate) fn path_bufs(&self) -> Vec<PathBuf> {
        self.paths.iter().map(PathBuf::from).collect()
    }
}

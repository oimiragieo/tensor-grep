use anyhow::Context;
#[cfg(feature = "cuda")]
use clap::ValueEnum;
use clap::{Args, Parser, Subcommand};
use hmac::{Hmac, Mac};
// Bug #88/#480/#100: `implicit_search_walk_exceeds_ceiling` (the WALK-ceiling probe) moved to
// `rg_passthrough.rs` (a library module, not this binary crate root) -- see the breadcrumb
// comment above `parse_early_ripgrep_args`. `WalkBuilder`'s only remaining consumer in THIS file
// is `count_search_corpus_bytes`, which is cuda-gated, so both imports are cuda-gated now.
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
use std::sync::{Arc, Mutex, OnceLock};
use std::time::Instant;
use std::time::{Duration, SystemTime, UNIX_EPOCH};
use tensor_grep_rs::backend_ast::{
    AstBackend, AstMatch, AstMetaVariables, BatchRewritePlan, BatchRewriteRule,
};
use tensor_grep_rs::crossover::{
    run_crossover_calibration, skip_signal_payload, write_crossover_config, NoCudaBuildError,
};
#[cfg(feature = "cuda")]
use tensor_grep_rs::gpu_native::{
    benchmark_cuda_graph_search_paths, benchmark_pageable_transfer_throughput,
    benchmark_pinned_transfer_throughput, enumerate_cuda_devices, gpu_native_search_paths_multi,
    probe_device_allocation, GpuNativeSearchConfig, GpuNativeSearchStats, GpuPipelineStats,
};
use tensor_grep_rs::index::TrigramIndex;
use tensor_grep_rs::native_search::{
    defaulted_scope_fields, native_json_text_fields, native_search_pattern_compiles,
    run_native_fixed_multi_pattern_search, run_native_search,
    smart_case_pattern_is_case_insensitive, NativeOutputTarget, NativeSearchConfig, SearchStats,
};
use tensor_grep_rs::python_sidecar::{
    execute_python_passthrough_command, execute_python_passthrough_command_captured,
    execute_python_passthrough_command_with_stdin, execute_sidecar_command, SidecarError,
};
use tensor_grep_rs::rg_passthrough::{
    execute_ripgrep_pcre2_version, execute_ripgrep_search, execute_ripgrep_type_list,
    is_unbounded_implicit_search_walk_refusal, ripgrep_is_available, RipgrepSearchArgs,
};
use tensor_grep_rs::routing::{
    gpu_proof_fields, native_can_serve_plain_text, plain_text_native_cheap_checks_pass,
    plain_text_native_flag_token_is_allowed, route_search, BackendSelection, IndexRoutingState,
    PlainTextNativeRequest, RoutingDecision, SearchRoutingCalibration, SearchRoutingConfig,
};

// audit #97 item 1: shown by print_native_top_level_help() (the clap fallback rendered when the
// Python passthrough is unavailable or times out -- see resolve_help_probe_timeout()). Leads with
// a condensed, agent-oriented pointer to the flagship/moat commands (mirroring the Typer help's
// "AI workflows" section in src/tensor_grep/cli/main.py) so degrading to this fallback is not
// catastrophic for an agent that never sees the rich help. The full command roster (all ~40
// commands, moat and maintenance alike) still follows in clap's auto-generated Commands: list.
const NATIVE_TOP_LEVEL_ABOUT: &str = "tensor-grep: native search, rewrite, and repository analysis CLI\n\nAI agent moat commands (start here):\n  tg orient PATH                      One-call codebase orientation capsule (entry points, central files, AST snippets)\n  tg defs SYMBOL                      Find symbol definitions\n  tg refs SYMBOL                      Find symbol references\n  tg callers SYMBOL                   Find direct callers of a symbol\n  tg impact SYMBOL                    Estimate files impacted by a symbol or query\n  tg blast-radius PATH SYMBOL --json  Transitive caller blast-radius graph\n  tg map PATH                         Bounded repository map for agent context selection\n  tg agent PATH \"task\" --json         Actionable context capsule: targets, snippets, validation, rollback, confidence\n  tg search PATTERN [PATH]            Validated rg-compatible regex search\n  tg mcp                              Start the Model Context Protocol server for AI assistants\n\nThis native fallback renders when the richer Python help is unavailable; run `tg doctor` to diagnose.";
const ENVIRONMENT_OVERRIDES_HELP: &str = "Agent and GPU contracts:\n  tg agent --query TEXT --json        Emit an Actionable Context Capsule with validation, rollback, confidence, and optional gpu_acceleration evidence.\n  tg agent --gpu-device-ids 0,1       Run opt-in native GPU evidence probes; sidecar-routed GPU results are reported as unsupported.\n  --gpu-device-ids                    Pin selected GPUs for explicit search, benchmark, and agent evidence probes. GPU remains experimental until it beats rg and tg_cpu.\n\nSearch routing switches:\n  tg search                           Validated common rg-compatible subset, not a full ripgrep replacement.\n  tg -t js PATTERN PATH               Root shortcuts and option-first common search flags are treated as tg search.\n  tg --count-matches PATTERN PATH     Root shortcut for rg-compatible per-file match counts.\n  --format rg --json                  Emit ripgrep JSON Lines events; plain --json is tensor-grep aggregate JSON.\n  --smart-case                        CPU/sidecar honor lowercase-insensitive smart case; native GPU falls back when case-insensitive semantics are required.\n  --hidden, --max-depth N, --text      Structured CPU/sidecar search honors these switches; native GPU falls back when a requested switch changes unsupported semantics.\n\nLSP provider status:\n  tg lsp --provider hybrid            Optional experimental semantic provider mode; provider availability is not LSP proof.\n  tg doctor --with-lsp                Report provider availability plus health_status/health_check diagnostics.\n\nLauncher repair:\n  tg repair-launcher --allow-foreign-rename\n                                      Explicitly back up a foreign Windows tg.exe that blocks Python subprocess resolution and replace it with the verified tensor-grep front door.\n\nEnvironment overrides:\n  TG_SIDECAR_PYTHON                  Path to the Python executable used for sidecar-backed commands.\n  TG_NATIVE_TG_BINARY                Path to the native front door used by Python-backed commands.\n  TG_RG_PATH                         Path to the ripgrep executable used for text-search passthrough.\n  TG_FORCE_CPU                       Force CPU routing for search commands.\n  TG_SIDECAR_TIMEOUT_MS              Timeout for sidecar-backed commands.\n  TG_HELP_PROBE_TIMEOUT_MS           Timeout for the --help passthrough probe (default 3000ms).\n  TG_PASSTHROUGH_TIMEOUT_MS          Timeout for one-shot Python passthrough commands (default 600000ms). Does not apply to mcp/session serve/lsp server launches.\n  TG_LSP_PROVIDER                    Override the LSP semantic provider mode (default native; e.g. hybrid). Availability is not LSP proof.\n  TENSOR_GREP_DEVICE_IDS             Comma-separated GPU IDs available to tensor-grep.\n  TENSOR_GREP_CLASSIFY_PROVIDER      Set to cybert to opt into CyBERT/Triton classification.\n  TENSOR_GREP_TRITON_TIMEOUT_SECONDS Timeout for Triton-backed NLP probes.\n  TENSOR_GREP_LSP_OPERATION_BUDGET_SECONDS Total per-command budget for optional external LSP provider requests.";
const JSON_OUTPUT_VERSION: u32 = 1;
const TG_RUST_EARLY_RG_ENV: &str = "TG_RUST_EARLY_RG";
const TG_RUST_EARLY_POSITIONAL_RG_ENV: &str = "TG_RUST_EARLY_POSITIONAL_RG";
/// Default --lint-cmd/--test-cmd timeout, matching apply_policy.py's `_run_policy_command` default
/// (see apply_policy.py:256-260) so the Rust `tg run --apply` path never hangs longer than the
/// Python validation path does.
const DEFAULT_VALIDATION_TIMEOUT_MS: u64 = 120_000;
const TG_VALIDATION_TIMEOUT_MS_ENV: &str = "TG_VALIDATION_TIMEOUT_MS";
/// Default cap on the number of per-file validation targets spawned by a single `--lint-cmd`/
/// `--test-cmd` run (audit #34): an 800-edited-file batch-rewrite would otherwise fan out 800
/// serial subprocesses per command.
const DEFAULT_MAX_VALIDATION_TARGETS: usize = 50;
const BROAD_GENERATED_SCAN_DIR_NAMES: &[&str] = &[
    "__pycache__",
    ".claude",
    ".cache",
    ".cargo",
    ".git",
    ".gradle",
    ".mypy_cache",
    ".npm",
    ".nuget",
    ".pytest_cache",
    ".ruff_cache",
    ".rustup",
    ".tox",
    ".venv",
    "AppData",
    "artifacts",
    "build",
    "coverage",
    "dist",
    "node_modules",
    "target",
    "venv",
];
const SEARCH_OPTION_FIRST_FLAGS: &[&str] = &[
    "--count-matches",
    "--format",
    "--sort",
    "--sortr",
    "--sort-files",
    "--no-sort-files",
    "-H",
    "--with-filename",
    "-I",
    "--no-filename",
    "-q",
    "--quiet",
    "-n",
    "--line-number",
    "--engine",
    "-s",
    "--case-sensitive",
    "-x",
    "--line-regexp",
    "-j",
    "--threads",
    "-t",
    "--type",
    "--iglob",
    "-T",
    "--type-not",
    "-u",
    "--unrestricted",
    "--stats",
    "--debug",
    "--trace",
    "--pcre2-unicode",
    "--no-pcre2-unicode",
    "--no-auto-hybrid-regex",
    "--no-text",
    "--no-binary",
    "--no-follow",
    "--no-glob-case-insensitive",
    "--no-ignore-file-case-insensitive",
    "--ignore",
    "--no-ignore",
    "--ignore-dot",
    "--ignore-exclude",
    "--ignore-files",
    "--ignore-global",
    "--ignore-messages",
    "--ignore-parent",
    "--ignore-vcs",
    "--no-ignore-vcs",
    "--messages",
    "--require-git",
    "-C",
    "--context",
    "-A",
    "--after-context",
    "-B",
    "--before-context",
    "--no-hidden",
    "--no-one-file-system",
    "--no-block-buffered",
    "--no-byte-offset",
    "--no-column",
    "--no-crlf",
    "--no-encoding",
    "--no-fixed-strings",
    "--no-invert-match",
    "--no-mmap",
    "--no-multiline",
    "--no-multiline-dotall",
    "--no-pcre2",
    "--no-pre",
    "--no-search-zip",
    "--no-context-separator",
    "--no-include-zero",
    "--no-line-buffered",
    "--no-max-columns-preview",
    "--no-trim",
    "--no-json",
    "--no-stats",
];
/// Flags that route a search to the Python passthrough front door rather than being handled by
/// the native fast path. Exact token matches only; unrecognized flags are caught fail-closed by
/// `parse_early_ripgrep_args`'s catch-all arm returning `None`.
const SEARCH_PYTHON_PASSTHROUGH_FLAGS: &[&str] = &[
    "-H",
    "--with-filename",
    "-I",
    "--no-filename",
    "-q",
    "--quiet",
    "-N",
    "--no-line-number",
    "--engine",
    "-s",
    "--case-sensitive",
    "-x",
    "--line-regexp",
    "-j",
    "--threads",
    "--iglob",
    "-T",
    "--type-not",
    "-u",
    "--unrestricted",
    "--stats",
    "--debug",
    "--trace",
    "-f",
    "--file",
    "--pre",
    "--pre-glob",
    "-z",
    "--search-zip",
    "--crlf",
    "--dfa-size-limit",
    "-E",
    "--encoding",
    "--mmap",
    "--no-unicode",
    "--regex-size-limit",
    "--stop-on-nonmatch",
    "--binary",
    "--glob-case-insensitive",
    "--ignore-file",
    "--ignore-file-case-insensitive",
    "--no-ignore-file-case-insensitive",
    "--no-require-git",
    "--pcre2-unicode",
    "--no-pcre2-unicode",
    "--no-auto-hybrid-regex",
    "--no-text",
    "--no-binary",
    "--no-follow",
    "--no-glob-case-insensitive",
    "--ignore",
    "--ignore-dot",
    "--ignore-exclude",
    "--ignore-files",
    "--ignore-global",
    "--ignore-messages",
    "--ignore-parent",
    "--ignore-vcs",
    "--messages",
    "--require-git",
    "--no-hidden",
    "--one-file-system",
    "--no-one-file-system",
    "--type-add",
    "--type-clear",
    "--block-buffered",
    "--no-block-buffered",
    "-b",
    "--byte-offset",
    "--no-byte-offset",
    "--no-crlf",
    "--no-encoding",
    "--no-fixed-strings",
    "--no-invert-match",
    "--no-mmap",
    "--no-multiline",
    "--no-multiline-dotall",
    "--no-pcre2",
    "--no-pre",
    "--no-search-zip",
    "--colors",
    "--context-separator",
    "--no-context-separator",
    "--field-context-separator",
    "--field-match-separator",
    "--heading",
    "--no-heading",
    "--hostname-bin",
    "--hyperlink-format",
    "--include-zero",
    "--no-include-zero",
    "--line-buffered",
    "--no-line-buffered",
    "-M",
    "--max-columns",
    "--max-columns-preview",
    "--no-max-columns-preview",
    "-p",
    "--pretty",
    "--trim",
    "--no-trim",
    "--no-json",
    "--no-stats",
    "--no-ignore-messages",
    "--no-messages",
    "--generate",
    "--lang",
    // BM25 re-ranking is a Python-side post-process; route --rank/--bm25 searches to the sidecar
    // so the native front door does not clap-reject the unknown flag.
    "--rank",
    "--bm25",
    // Local hybrid semantic search (RRF fusion of BM25 + dense embeddings) is also a Python-side
    // post-process (roadmap #27, Path B Stage 1) -- same reasoning as --rank/--bm25 above.
    "--semantic",
    // --ltl is a Python-side temporal-query post-process (CPUBackend::_search_ltl); route it
    // to the sidecar so the native front door does not clap-reject the unknown flag. Paired
    // with bootstrap.py::_TG_ONLY_SEARCH_FLAGS (the 2-front-door law).
    "--ltl",
];

#[derive(Parser, Debug)]
#[command(name = "tg")]
#[command(version)]
#[command(about = NATIVE_TOP_LEVEL_ABOUT)]
#[command(after_help = ENVIRONMENT_OVERRIDES_HELP)]
#[command(disable_help_subcommand = true)]
pub struct CommandCli {
    #[command(subcommand)]
    pub command: Commands,
}

#[derive(Parser, Debug)]
#[command(name = "tg")]
#[command(version)]
#[command(about = NATIVE_TOP_LEVEL_ABOUT)]
#[command(after_help = ENVIRONMENT_OVERRIDES_HELP)]
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
    #[arg(required_unless_present_any = ["regexp", "pcre2_version", "type_list", "version"])]
    pub pattern: Option<String>,

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
    /// Emit a structured JSON result, including a machine-readable
    /// `calibration_status: skipped_no_cuda_build` signal when this build cannot run GPU
    /// calibration, instead of the default human-readable output. Does not change the exit
    /// code (still 2 on the no-cuda skip, per the backend-unavailable convention) or the
    /// success-path output (already JSON).
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
    #[arg(long = "format", default_value = "json")]
    pub format: String,

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
    #[command(visible_alias = "update")]
    Upgrade,
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
    /// One-shot install of the `tg find` / `--semantic` dense-embedding leg (CEO#7)
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
struct ResolvedSearchRequest {
    patterns: Vec<String>,
    paths: Vec<String>,
    path_was_implicit: bool,
}

impl ResolvedSearchRequest {
    fn query_display(&self) -> String {
        if self.patterns.len() == 1 {
            self.patterns[0].clone()
        } else {
            self.patterns.join(" | ")
        }
    }

    fn primary_path(&self) -> &str {
        self.paths.first().map(String::as_str).unwrap_or(".")
    }

    fn path_display(&self) -> String {
        if self.paths.is_empty() {
            ".".to_string()
        } else {
            self.paths.join(" ")
        }
    }

    #[cfg(feature = "cuda")]
    fn path_bufs(&self) -> Vec<PathBuf> {
        self.paths.iter().map(PathBuf::from).collect()
    }
}

fn main() -> anyhow::Result<()> {
    let handle = std::thread::Builder::new()
        .name("tg-main".to_string())
        .stack_size(16 * 1024 * 1024)
        .spawn(main_inner)?;
    handle
        .join()
        .unwrap_or_else(|panic| std::panic::resume_unwind(panic))
}

fn main_inner() -> anyhow::Result<()> {
    let raw_args: Vec<OsString> = std::env::args_os().collect();

    if raw_args.len() <= 1 {
        if let Some(exit_code) = try_public_help_passthrough(&raw_args)? {
            if exit_code != 0 {
                std::process::exit(exit_code.max(1));
            }
            return Ok(());
        }
        return print_native_top_level_help();
    }

    if top_level_unknown_command_refusal(&raw_args) {
        let first = &raw_args[1].to_string_lossy();
        let nearest = nearest_commands(first);
        let has_help = raw_args.iter().skip(2).any(|a| {
            let t = a.to_string_lossy();
            t == "--help" || t == "-h"
        });
        if has_help {
            if nearest.is_empty() {
                eprintln!("error: unknown command '{first}'");
            } else {
                eprintln!(
                    "error: unknown command '{first}' (did you mean {}?)",
                    nearest.join(", ")
                );
            }
        } else {
            let payload = serde_json::json!({
                "error": {
                    "code": "unknown_command",
                    "nearest": nearest,
                    "command": first,
                },
            });
            eprintln!("{payload}");
        }
        std::process::exit(2);
    }

    if is_top_level_version_invocation(&raw_args) || is_search_version_invocation(&raw_args) {
        println!("tg {}", env!("CARGO_PKG_VERSION"));
        return Ok(());
    }

    if is_top_level_pcre2_version_invocation(&raw_args)
        || is_search_pcre2_version_invocation(&raw_args)
    {
        require_ripgrep_or_exit(ripgrep_is_available(), "--pcre2-version");
        let exit_code = execute_ripgrep_pcre2_version()?;
        if exit_code != 0 {
            std::process::exit(exit_code.max(1));
        }
        return Ok(());
    }
    if is_top_level_type_list_invocation(&raw_args) || is_search_type_list_invocation(&raw_args) {
        require_ripgrep_or_exit(ripgrep_is_available(), "--type-list");
        let exit_code = execute_ripgrep_type_list()?;
        if exit_code != 0 {
            std::process::exit(exit_code.max(1));
        }
        return Ok(());
    }

    if let Some(exit_code) = try_public_help_passthrough(&raw_args)? {
        if exit_code != 0 {
            std::process::exit(exit_code.max(1));
        }
        return Ok(());
    }

    // C3: plain `--json` combined with a render-only flag (e.g. -b/--passthru/--heading)
    // cannot be honored by the aggregate JSON path and must be rejected by the native
    // binary itself — NEVER delegated to the Python sidecar, which deadlocks/fork-bombs
    // the native<->python re-exec chain when the resolved Python is a stale tensor-grep
    // lacking the launcher guard. Fail fast and deterministically before spawning anything.
    let json_render_conflicts = json_aggregate_render_flag_conflicts(&raw_args);
    if !json_render_conflicts.is_empty() {
        let detail = format!(
            "flag(s) {} not supported with plain --json; use --format rg --json for ripgrep \
             JSON Lines that carry render metadata, or drop the flag(s).",
            json_render_conflicts.join(", ")
        );
        let payload = serde_json::json!({
            "version": JSON_OUTPUT_VERSION,
            "schema_version": JSON_OUTPUT_VERSION,
            "ok": false,
            "error": "unsupported_flag",
            "detail": detail,
        });
        println!("{payload}");
        std::process::exit(2);
    }

    if let Some(exit_code) = try_early_ripgrep_passthrough(&raw_args)? {
        if exit_code != 0 {
            std::process::exit(exit_code.max(1));
        }
        return Ok(());
    }

    if let Some(exit_code) = try_default_search_frontdoor_passthrough(&raw_args)? {
        if exit_code != 0 {
            std::process::exit(exit_code.max(1));
        }
        return Ok(());
    }

    if let Some(search_args) = search_format_python_passthrough_args(&raw_args) {
        let exit_code = match execute_python_passthrough_command("search", search_args) {
            Ok(exit_code) => exit_code,
            Err(err) => return exit_with_sidecar_error(err),
        };
        if exit_code != 0 {
            std::process::exit(exit_code.max(1));
        }
        return Ok(());
    }

    if raw_args.get(1).and_then(|arg| arg.to_str()) != Some("search") {
        if let Some(search_args) = normalize_top_level_search_args(&raw_args) {
            let cli = CommandCli::parse_from(search_args);
            return run_command_cli(cli);
        }
    }

    if let Some(exit_code) = try_early_positional_ripgrep_passthrough(&raw_args)? {
        if exit_code != 0 {
            std::process::exit(exit_code.max(1));
        }
        return Ok(());
    }

    if should_use_positional_cli(&raw_args) {
        return run_positional_cli(PositionalCli::parse_from(raw_args));
    }

    let cli = CommandCli::parse_from(raw_args);

    run_command_cli(cli)
}

fn print_native_top_level_help() -> anyhow::Result<()> {
    use clap::CommandFactory;

    let mut cmd = CommandCli::command();
    cmd.print_help()?;
    Ok(())
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

/// Fail closed (exit 2, the "backend unavailable / incomplete" convention `handle_calibrate_command`
/// already uses) instead of letting a passthrough-required rg invocation bubble an `Err` through `?`
/// to `main()`'s default `Result` termination, which exits 1 -- indistinguishable from a genuine
/// "no match" (audit #81 #7). Also used to refuse a `--pcre2` request when no rg is present rather
/// than silently swapping to the native regex engine, which does not support PCRE2 syntax (#9).
fn require_ripgrep_or_exit(rg_available: bool, context: &str) {
    if !rg_available {
        eprintln!(
            "error: {context} requires the ripgrep (`rg`) backend, but rg is unavailable. \
             Install `rg`, set TG_RG_PATH, or place a bundled ripgrep binary next to `tg`."
        );
        std::process::exit(2);
    }
}

fn is_top_level_version_invocation(raw_args: &[OsString]) -> bool {
    raw_args.len() == 2
        && matches!(
            raw_args.get(1).and_then(|arg| arg.to_str()),
            Some("--version" | "-V")
        )
}

fn is_search_version_invocation(raw_args: &[OsString]) -> bool {
    raw_args.len() == 3
        && raw_args.get(1).and_then(|arg| arg.to_str()) == Some("search")
        && matches!(
            raw_args.get(2).and_then(|arg| arg.to_str()),
            Some("--version" | "-V")
        )
}

fn is_top_level_pcre2_version_invocation(raw_args: &[OsString]) -> bool {
    raw_args.len() == 2 && raw_args.get(1).and_then(|arg| arg.to_str()) == Some("--pcre2-version")
}

fn is_top_level_type_list_invocation(raw_args: &[OsString]) -> bool {
    raw_args.len() == 2 && raw_args.get(1).and_then(|arg| arg.to_str()) == Some("--type-list")
}

fn is_search_pcre2_version_invocation(raw_args: &[OsString]) -> bool {
    raw_args.len() == 3
        && raw_args.get(1).and_then(|arg| arg.to_str()) == Some("search")
        && raw_args.get(2).and_then(|arg| arg.to_str()) == Some("--pcre2-version")
}

fn is_search_type_list_invocation(raw_args: &[OsString]) -> bool {
    raw_args.len() == 3
        && raw_args.get(1).and_then(|arg| arg.to_str()) == Some("search")
        && raw_args.get(2).and_then(|arg| arg.to_str()) == Some("--type-list")
}

fn parse_public_help_passthrough(raw_args: &[OsString]) -> Option<(&str, Vec<String>)> {
    if raw_args.len() == 1 {
        return Some(("--help", Vec::new()));
    }

    let first = raw_args.get(1)?.to_str()?;
    match (
        first,
        raw_args.get(2).and_then(|arg| arg.to_str()),
        raw_args.len(),
    ) {
        ("--help" | "-h", None, 2) => Some((first, Vec::new())),
        ("search", Some("--help" | "-h"), 3) => {
            Some(("search", vec![raw_args[2].to_string_lossy().into_owned()]))
        }
        ("scan" | "test", Some("--help" | "-h"), 3) => {
            Some((first, vec![raw_args[2].to_string_lossy().into_owned()]))
        }
        _ => None,
    }
}

fn try_public_help_passthrough(raw_args: &[OsString]) -> anyhow::Result<Option<i32>> {
    let (command, args) = match parse_public_help_passthrough(raw_args) {
        Some(invocation) => invocation,
        None => return Ok(None),
    };

    match execute_python_passthrough_command_captured(command, args) {
        Ok(result) if result.exit_code == 0 => {
            if !result.stdout.is_empty() {
                print!("{}", result.stdout);
            }
            if !result.stderr.is_empty() {
                eprint!("{}", result.stderr);
            }
            Ok(Some(0))
        }
        Ok(_) => Ok(None),
        Err(_) => Ok(None),
    }
}

fn try_early_ripgrep_passthrough(raw_args: &[OsString]) -> anyhow::Result<Option<i32>> {
    if !env_flag_enabled(TG_RUST_EARLY_RG_ENV) {
        return Ok(None);
    }
    if !ripgrep_is_available() {
        return Ok(None);
    }

    if raw_args
        .get(1)
        .map(|arg| arg.to_string_lossy() != "search")
        .unwrap_or(true)
    {
        return Ok(None);
    }

    let rg_args = match parse_early_ripgrep_args(raw_args) {
        Some(args) => args,
        None => return Ok(None),
    };
    if !should_use_early_ripgrep_fast_path(&rg_args) {
        return Ok(None);
    }
    if ripgrep_args_need_broad_generated_guard(&rg_args) {
        let generated_dirs = generated_scan_dir_names(&rg_args.paths, rg_args.files);
        if !generated_dirs.is_empty() {
            eprintln!("{}", format_broad_generated_scan_error(&generated_dirs));
            return Ok(Some(2));
        }
    }

    let exit_code = execute_ripgrep_search(&rg_args)?;
    Ok(Some(exit_code))
}

fn try_default_search_frontdoor_passthrough(raw_args: &[OsString]) -> anyhow::Result<Option<i32>> {
    if !ripgrep_is_available() {
        return Ok(None);
    }
    let rg_args = match parse_default_search_frontdoor_args(raw_args) {
        Some(args) => args,
        None => return Ok(None),
    };

    let exit_code = execute_ripgrep_search(&rg_args)?;
    Ok(Some(exit_code))
}

fn try_early_positional_ripgrep_passthrough(raw_args: &[OsString]) -> anyhow::Result<Option<i32>> {
    if !env_flag_enabled(TG_RUST_EARLY_POSITIONAL_RG_ENV) {
        return Ok(None);
    }
    if !ripgrep_is_available() {
        return Ok(None);
    }

    if !should_use_positional_cli(raw_args) {
        return Ok(None);
    }

    let rg_args = match parse_early_positional_ripgrep_args(raw_args) {
        Some(args) => args,
        None => return Ok(None),
    };

    let exit_code = execute_ripgrep_search(&rg_args)?;
    Ok(Some(exit_code))
}

fn search_format_python_passthrough_args(raw_args: &[OsString]) -> Option<Vec<String>> {
    let search_args = normalize_top_level_search_args(raw_args)?;

    let args = search_args
        .iter()
        .skip(2)
        .map(|arg| arg.to_string_lossy().to_string())
        .collect::<Vec<_>>();
    // External audit #138/#140: `--index` selects the Rust-native trigram-index engine
    // (route_search -> handle_index_search -> index_flag_violations), which the Python sidecar
    // does not implement at all. Every check below exists to route some OTHER flag to Python --
    // none of them know about --index, so a request combining --index with any single one of
    // them (e.g. `tg search --index --no-hidden ...`) used to be forwarded here wholesale,
    // `--index` token and all, BEFORE clap ever parsed --index. Python has no `--index` option,
    // so that either crashes ("Error: No such option: --index") or -- if some future Python
    // passthrough ever tolerates unknown flags -- leaks the bare token into the constructed rg
    // argv. Short-circuit ahead of every passthrough check so an explicit --index always falls
    // through (returns None here) to clap/route_search/index_flag_violations below, which
    // already fail closed on any flag the index path cannot honor -- regardless of which other
    // (individually Python-passthrough-eligible) flags ride along with it. This does not change
    // routing for non-index invocations: the checks below are unreached only when --index is
    // literally present.
    if args.iter().any(|arg| arg == "--index") {
        return None;
    }
    if search_args_contain_any_flag(&args, SEARCH_PYTHON_PASSTHROUGH_FLAGS) {
        return Some(args);
    }
    if args.iter().any(|arg| {
        matches!(
            arg.as_str(),
            "--files" | "--allow-broad-generated-scan" | "--ast"
        )
    }) {
        return Some(args);
    }
    let structured_output = args
        .iter()
        .any(|arg| matches!(arg.as_str(), "--json" | "--ndjson"));
    if structured_output
        && args.iter().any(|arg| {
            matches!(
                arg.as_str(),
                "--passthru"
                    | "--passthrough"
                    | "--auto-hybrid-regex"
                    | "--no-ignore-dot"
                    | "--no-ignore-exclude"
                    | "--no-ignore-files"
                    | "--no-ignore-global"
                    | "--no-ignore-parent"
                    | "--no-config"
            )
        })
    {
        return Some(args);
    }
    if structured_output
        && args.iter().any(|arg| {
            matches!(
                arg.as_str(),
                "-U" | "--multiline" | "--multiline-dotall" | "--null-data"
            )
        })
    {
        return Some(args);
    }
    let mut index = 0usize;
    while index < args.len() {
        let token = &args[index];
        if token == "--format" {
            index += 1;
            if args.get(index).map(String::as_str) != Some("rg") {
                return Some(args);
            }
        } else if let Some((_, value)) = token.split_once('=') {
            if token.starts_with("--format=") && value != "rg" {
                return Some(args);
            }
        }
        index += 1;
    }
    None
}

/// Render-only flags the aggregate plain-`--json` path cannot honor. Mirrors
/// `_PLAIN_JSON_INCOMPATIBLE_RENDER_FLAGS` / `_JSON_INCOMPATIBLE_RENDER_FLAGS` in the
/// Python CLI/launcher (canonical spelling first in each group).
const JSON_INCOMPATIBLE_RENDER_FLAGS: &[&[&str]] = &[
    &["--passthru", "--passthrough"],
    &["--heading", "--no-heading"],
    &["--trim", "--no-trim"],
    &["-b", "--byte-offset", "--no-byte-offset"],
    &["-M", "--max-columns"],
    &["--max-columns-preview", "--no-max-columns-preview"],
    &["--context-separator", "--no-context-separator"],
    &["--field-context-separator"],
    &["--field-match-separator"],
    &["-p", "--pretty"],
];

/// Return the canonical spellings of render-only flags the user combined with plain
/// `--json` (not `--format rg`). Such a combination must be rejected by the NATIVE binary
/// directly — never delegated to the Python sidecar — because delegating to a stale/older
/// tensor-grep Python (one lacking the launcher guard) deadlocks and can fork-bomb the
/// native<->python re-exec chain (audit C3). Mirrors the Python guard so the native front
/// door is self-sufficient regardless of which Python it resolves.
fn json_aggregate_render_flag_conflicts(raw_args: &[OsString]) -> Vec<String> {
    let Some(search_args) = normalize_top_level_search_args(raw_args) else {
        return Vec::new();
    };
    let args = search_args
        .iter()
        .skip(2)
        .map(|arg| arg.to_string_lossy().to_string())
        .collect::<Vec<_>>();
    if !args.iter().any(|arg| arg == "--json") {
        return Vec::new();
    }
    // `--format rg` emits ripgrep JSON Lines, which carry render metadata — allowed.
    // Stop at the `--` end-of-options token (mirroring the conflict loop below): a
    // literal `--format rg` smuggled AFTER `--` is a search pattern, not the flag, and
    // must not suppress a genuine render-flag conflict that precedes `--` (audit MED).
    let mut index = 0usize;
    while index < args.len() {
        if args[index] == "--" {
            break;
        }
        if args[index] == "--format" {
            if args.get(index + 1).map(String::as_str) == Some("rg") {
                return Vec::new();
            }
        } else if args[index] == "--format=rg" {
            return Vec::new();
        }
        index += 1;
    }
    let mut flagged: Vec<String> = Vec::new();
    for arg in &args {
        if arg == "--" {
            break;
        }
        let base = arg.split('=').next().unwrap_or(arg);
        for group in JSON_INCOMPATIBLE_RENDER_FLAGS {
            let canonical = group[0].to_string();
            if group.contains(&base) && !flagged.contains(&canonical) {
                flagged.push(canonical);
            }
        }
    }
    flagged
}

fn normalize_top_level_search_args(raw_args: &[OsString]) -> Option<Vec<OsString>> {
    if raw_args.get(1).and_then(|arg| arg.to_str()) == Some("search") {
        return Some(raw_args.to_vec());
    }
    if !raw_args_contain_any_flag(raw_args, SEARCH_OPTION_FIRST_FLAGS)
        && !raw_args_contain_any_flag(raw_args, SEARCH_PYTHON_PASSTHROUGH_FLAGS)
    {
        return None;
    }
    if raw_args
        .get(1)
        .and_then(|arg| arg.to_str())
        .map(is_known_python_command)
        .unwrap_or(false)
    {
        return None;
    }

    let mut search_args = Vec::with_capacity(raw_args.len() + 1);
    search_args.push(raw_args.first()?.clone());
    search_args.push(OsString::from("search"));
    search_args.extend(raw_args.iter().skip(1).cloned());
    Some(search_args)
}

fn normalize_top_level_format_search_args(raw_args: &[OsString]) -> Option<Vec<OsString>> {
    normalize_top_level_search_args(raw_args)
}

fn raw_args_contain_any_flag(raw_args: &[OsString], flags: &[&str]) -> bool {
    raw_args.iter().skip(1).any(|arg| {
        let token = arg.to_string_lossy();
        token_matches_any_flag(&token, flags)
    })
}

fn search_args_contain_any_flag(args: &[String], flags: &[&str]) -> bool {
    args.iter()
        .any(|token| token_matches_any_flag(token.as_str(), flags))
}

fn token_matches_any_flag(token: &str, flags: &[&str]) -> bool {
    flags.iter().any(|flag| {
        token == *flag || (flag.starts_with("--") && token.starts_with(&format!("{flag}=")))
    })
}

fn requests_explicit_rg_format(raw_args: &[OsString]) -> bool {
    let tokens = raw_args
        .iter()
        .skip(2)
        .map(|arg| arg.to_string_lossy().to_string())
        .collect::<Vec<_>>();
    let mut index = 0usize;
    while index < tokens.len() {
        let token = &tokens[index];
        if token == "--format" {
            index += 1;
            return tokens.get(index).map(String::as_str) == Some("rg");
        }
        if token.starts_with("--format=") {
            return token.split_once('=').map(|(_, value)| value) == Some("rg");
        }
        index += 1;
    }
    false
}

fn should_use_early_ripgrep_fast_path(args: &RipgrepSearchArgs) -> bool {
    !args.word_regexp && !args.fixed_strings
}

/// Whether stdout is attached to a terminal. Load-bearing for
/// `native_can_serve_plain_text`: `execute_ripgrep_search` spawns `rg` with `Stdio::inherit()`,
/// so on a terminal `rg` renders its grouped/heading layout with color while the native engine
/// always renders flat uncolored `path:line:text`. Terminals therefore keep the subprocess.
fn stdout_is_terminal() -> bool {
    use std::io::IsTerminal;

    std::io::stdout().is_terminal()
}

/// ripgrep's config-file environment surface. `rg` reads this on startup, and
/// `execute_ripgrep_search` neither clears the environment nor passes `--no-config` (that is sent
/// only when the USER asks for it), so a plain-text search routed to `rg` today applies whatever
/// this points at. See `native_can_serve_plain_text` refusal note (8).
const RIPGREP_CONFIG_PATH_ENV: &str = "RIPGREP_CONFIG_PATH";

/// THE single computation of the environment clause. Called from exactly one place
/// (`finish_plain_text_native_request`), so the two eligibility adapters cannot drift on it.
fn rg_config_env_present() -> bool {
    rg_config_env_is_active(env::var_os(RIPGREP_CONFIG_PATH_ENV).as_deref())
}

/// Decision half of `rg_config_env_present`, split out so its semantics are unit-testable without
/// mutating the process environment (which would race every other test that computes eligibility).
/// `rg` IGNORES an empty value, so "set" alone is not the condition -- it must be set AND non-empty.
fn rg_config_env_is_active(value: Option<&OsStr>) -> bool {
    value.is_some_and(|value| !value.is_empty())
}

/// Size cap for `plain_text_native_file_renders_identically`: a file above this is REFUSED rather
/// than probed.
///
/// MEASURED, not assumed (median of 21-25 runs; "gain" is the subprocess round trip saved, "probe"
/// is the full-content read this route adds):
///
/// | file size | gain | probe | net | probe as % of gain |
/// |---|---|---|---|---|
/// | 4 KB | +10.6ms | 0.18ms | +10.4ms | 2% |
/// | 200 KB | +13.6ms | 0.25ms | +13.4ms | 2% |
/// | 1 MB | +9.7ms | 1.57ms | +8.1ms | 16% |
/// | 4 MB | +10.1ms | 4.35ms | +5.7ms | 43% |
/// | 8 MB | +11.1ms | 8.68ms | +2.5ms | 78% |
/// | 8 MB, match-dense | **-2.2ms** | 8.68ms | **-10.9ms** | REGRESSION |
///
/// An earlier revision of this comment claimed the probe "can never cost more than it saves". That
/// was FALSE at the top of the range: at 8 MB the probe ate 78% of the win, and on a match-dense
/// 8 MB file the native engine is itself SLOWER than `rg` (gain goes negative), so the probe turned
/// a small loss into a 10.9ms regression. The cap is the only thing standing between this route and
/// that tail.
///
/// 512 KiB is chosen deliberately conservatively inside the 256 KB-1 MB band the measurements
/// support: the probe costs ~2% of the gain there, essentially every source file fits, and it keeps
/// a wide margin from the size at which the ENGINE (not the probe) starts losing to `rg` on
/// match-dense input. Files above the cap keep spawning `rg` -- no regression tail, at the price of
/// giving up a win on large files that was already mostly eaten by the probe.
const PLAIN_TEXT_NATIVE_MAX_PROBE_BYTES: u64 = 512 * 1024;

/// FULL-CONTENT probe for `PlainTextNativeRequest::single_path_renders_identically`.
///
/// Three independently-verified emitter divergences make a file unsafe for the native plain-text
/// route, and none of them can be decided from the request alone -- they are properties of the
/// DATA (see `native_can_serve_plain_text` refusal note 5 for the measured byte diffs):
///   - any `\r` byte: the native plain sink strips a trailing `\r` (`trim_end_matches(['\n','\r'])`)
///     because nothing on this path sets a CRLF line terminator, so a CRLF file loses it while
///     `rg` keeps it;
///   - invalid UTF-8: the native plain sink is `grep_searcher::sinks::Lossy`, which substitutes
///     U+FFFD where `rg` writes raw bytes -- silent corruption;
///   - any NUL byte: the native binary-match notice spells `"/0"` where `rg` spells `"\0"`, and
///     that native spelling is a governed snapshot contract.
///
/// The check is deliberately WHOLE-FILE, not a prefix probe: an invalid byte at 1 MB diverges
/// exactly as hard as one at byte 0, so a prefix window cannot bound the risk. Fails CLOSED
/// (returns false = keep spawning `rg`) on any I/O error, oversize file, or non-file path.
///
/// MEMOIZED for the process lifetime. Both eligibility adapters run on an admitted request -- the
/// pre-clap front door decides whether to decline, then `handle_ripgrep_search` re-derives the same
/// verdict -- so without this cache an admitted request would read the file 3x (two probes plus the
/// search) where `rg` reads it once. With it: 2x. Removing the last extra read would mean threading
/// pre-read bytes into `run_native_search`, i.e. changing the engine shared with
/// `--json`/`--ndjson`/`--cpu` -- the wrong blast radius for this PR, and worth ~0.25ms at 200 KB
/// (about 2% of the win).
///
/// The cache is keyed by path. If the file is mutated between the probe and the search, a STALE
/// ADMISSION lets the native engine render content the probe would have refused -- CRLF whose
/// trailing `\r` is silently dropped, or invalid UTF-8 turned into U+FFFD. That is NOT the same
/// class as the ordinary read race `rg` already has (rg reads once and renders what it read); it
/// is a correctness window this route introduces. Severity is genuinely low -- single-shot
/// process, microseconds wide -- but it is a different failure than "you saw slightly older
/// bytes", and calling it a pre-existing race would understate it.
fn plain_text_native_file_renders_identically(path: &Path) -> bool {
    let cache = PLAIN_TEXT_NATIVE_PROBE_CACHE.get_or_init(|| Mutex::new(BTreeMap::new()));
    if let Ok(probed) = cache.lock() {
        if let Some(verdict) = probed.get(path) {
            return *verdict;
        }
    }

    let verdict = plain_text_native_probe_file(path);
    if let Ok(mut probed) = cache.lock() {
        probed.insert(path.to_path_buf(), verdict);
    }
    verdict
}

static PLAIN_TEXT_NATIVE_PROBE_CACHE: OnceLock<Mutex<BTreeMap<PathBuf, bool>>> = OnceLock::new();

fn plain_text_native_probe_file(path: &Path) -> bool {
    let Ok(metadata) = std::fs::metadata(path) else {
        return false;
    };
    if !metadata.is_file() || metadata.len() > PLAIN_TEXT_NATIVE_MAX_PROBE_BYTES {
        return false;
    }
    let Ok(bytes) = std::fs::read(path) else {
        return false;
    };
    // The bytes read MUST equal the size the OS reported. Measured on Linux (WSL):
    // `/proc/self/status` and `/proc/version` are S_ISREG with `st_size` 0 yet return 1460 and 166
    // bytes of clean UTF-8 -- so they passed every other clause and were ADMITTED. A file whose
    // reported size is a lie is precisely the shape where this probe cannot describe what the
    // SEARCH will later read: the content is generated per-open, so the memoized verdict is not
    // stale by a microsecond, it is unrelated by construction. This also fail-closes an ordinary
    // file that changes size between the `metadata` call and the read. Free -- both numbers are
    // already in hand.
    if bytes.len() as u64 != metadata.len() {
        return false;
    }
    if bytes.contains(&0u8) || bytes.contains(&b'\r') {
        return false;
    }
    std::str::from_utf8(&bytes).is_ok()
}

/// Refusal note (7): is this PATTERN safe for the native route?
///
/// Two independent gates, both fail-closed:
///
/// 1. TEXT SCAN for a line terminator or NUL, literal OR escaped. `rg` rejects these outright
///    (rc=2 plus a diagnostic); the native matcher accepts them and succeeds with zero matches
///    (rc=1, empty stderr), which is an exit-code REGRESSION an agent branching on 2-vs-1 would
///    misread as "no matches". The scan is deliberately over-broad -- it also refuses `\x..` and
///    `\u..` escapes wholesale rather than decoding them, and refuses `\n`-looking text even under
///    `-F` where it is a harmless literal -- because over-refusal costs only a `rg` spawn while
///    under-refusal costs correctness.
/// 2. COMPILE CHECK through `native_search::native_search_pattern_compiles`, which builds the
///    matcher with the very same `build_matcher` the search will use. A pattern that fails to
///    compile still exits 2, but only after the rg-fallback net prints a `warning: native CPU
///    search failed...` line `rg` never emits.
fn plain_text_native_pattern_is_renderable(
    pattern: &str,
    ignore_case: bool,
    fixed_strings: bool,
    word_boundary: bool,
) -> bool {
    let bytes = pattern.as_bytes();
    if bytes
        .iter()
        .any(|&byte| matches!(byte, b'\n' | b'\r' | b'\0'))
    {
        return false;
    }

    let mut index = 0usize;
    while index < bytes.len() {
        if bytes[index] != b'\\' {
            index += 1;
            continue;
        }
        if let Some(&escaped) = bytes.get(index + 1) {
            if matches!(escaped, b'n' | b'r' | b'0' | b'x' | b'u') {
                return false;
            }
        }
        // Skip the escaped byte too, so `\\n` (an escaped backslash followed by a plain `n`) is
        // not misread as the `\n` escape.
        index += 2;
    }

    // `smart_case` is never admitted, so it is always false on this route.
    //
    // TODO(perf): this builds a matcher and throws it away, and `run_native_search` then rebuilds
    // an identical one. Microseconds for an ordinary pattern, but it is duplicated work on the very
    // hot path this route exists to speed up, and a pathological pattern pays the regex-size-limit
    // walk twice. Threading the compiled matcher through to the search would remove both, at the
    // cost of widening the shared engine's entry signature -- deliberately out of this PR's blast
    // radius, and worth doing when the engine next changes shape.
    native_search_pattern_compiles(pattern, ignore_case, false, fixed_strings, word_boundary)
}

/// Builds the `PlainTextNativeRequest` for a resolved `tg search` invocation. THE production
/// computation -- `handle_ripgrep_search` calls it, and the adapter-agreement test drives it
/// rather than re-deriving anything, so a test can never pass against a mirror of the logic.
fn plain_text_native_request_for_search(
    args: &SearchArgs,
    request: &ResolvedSearchRequest,
    stdout_is_terminal: bool,
) -> PlainTextNativeRequest {
    let single_path = (request.paths.len() == 1).then(|| Path::new(&request.paths[0]));
    let facts = PlainTextNativeRequest {
        pattern_count: request.patterns.len(),
        pattern_is_empty: request.patterns.iter().any(String::is_empty),
        pattern_is_native_renderable: false,
        path_count: request.paths.len(),
        path_was_implicit: request.path_was_implicit,
        single_path_is_regular_file: single_path.is_some_and(Path::is_file),
        single_path_is_stdin_sentinel: request.paths.iter().any(|path| path == "-"),
        single_path_renders_identically: false,
        structured_output: args.json || args.ndjson,
        explicit_format: args.format.is_some(),
        stdout_is_terminal,
        // Overwritten by `finish_plain_text_native_request`, the single owner of these clauses.
        rg_config_env_present: false,
        only_allowed_flags: search_args_allow_plain_text_native(args),
    };
    finish_plain_text_native_request(
        facts,
        "clap",
        request.patterns.first().map(String::as_str),
        args.ignore_case,
        args.fixed_strings,
        args.word_regexp,
        single_path,
    )
}

/// Fills the EXPENSIVE tier of a `PlainTextNativeRequest` -- and ONLY if the cheap tier already
/// passed. See `plain_text_native_cheap_checks_pass` for why this ordering is a latency contract:
/// an interactive single-file search, a `--json` run, and an `-A`/`-B`/`-C` run all reach the
/// clap-side adapter and would otherwise each burn a full file read on their way to `rg` anyway.
/// The two gates are themselves ordered cheapest-first: a regex compile costs microseconds, the
/// file probe costs a read.
///
/// ONE thing is filled ABOVE the cheap-tier gate, and deliberately so: `rg_config_env_present`.
/// It is not an exception to the contract, it IS part of the cheap tier -- a single `env::var_os`
/// with no I/O, no allocation beyond the value, and no filesystem access -- and
/// `plain_text_native_cheap_checks_pass` consults it, so it must be populated before that call or
/// the clause would read `false` and be a dead guard. It lives here rather than in the two
/// constructors purely so there is exactly one call path and the adapters cannot drift on it.
fn finish_plain_text_native_request(
    request: PlainTextNativeRequest,
    stage: &'static str,
    pattern: Option<&str>,
    ignore_case: bool,
    fixed_strings: bool,
    word_boundary: bool,
    path: Option<&Path>,
) -> PlainTextNativeRequest {
    let finished = fill_plain_text_native_expensive_tier(
        request,
        pattern,
        ignore_case,
        fixed_strings,
        word_boundary,
        path,
    );
    record_plain_text_route_telemetry(stage, &finished);
    finished
}

/// The computation itself, split from `finish_plain_text_native_request` only so telemetry has a
/// single emission point covering every early-return path.
fn fill_plain_text_native_expensive_tier(
    mut request: PlainTextNativeRequest,
    pattern: Option<&str>,
    ignore_case: bool,
    fixed_strings: bool,
    word_boundary: bool,
    path: Option<&Path>,
) -> PlainTextNativeRequest {
    // The environment clause is filled HERE rather than by either constructor, so there is exactly
    // one call path to `rg_config_env_present()` and the two adapters cannot disagree about it.
    request.rg_config_env_present = rg_config_env_present();
    if !plain_text_native_cheap_checks_pass(&request) {
        return request;
    }
    let Some(pattern) = pattern else {
        return request;
    };
    request.pattern_is_native_renderable =
        plain_text_native_pattern_is_renderable(pattern, ignore_case, fixed_strings, word_boundary);
    if !request.pattern_is_native_renderable {
        return request;
    }
    if let Some(path) = path {
        request.single_path_renders_identically = plain_text_native_file_renders_identically(path);
    }
    request
}

/// Admission-rate telemetry, default-OFF. Answers "how often is this route ACTUALLY taken?", which
/// nothing else in the repo can: an independent review found 0 of 10 benchmark scenarios, 0 of 4
/// dogfood calls, and the entire MCP surface (which always builds `--json`) ineligible, so the
/// benchmark-regression gate can observe neither this route's benefit nor a future regression in it.
///
/// One JSON Lines record per eligibility evaluation, appended, carrying the stage and every clause,
/// so a consumer can report both the admit rate and a histogram of WHICH clause refused. Enable with
/// `TG_ROUTE_TELEMETRY=1`; the file defaults to the OS temp dir (never the workspace -- see the
/// file-placement rules) and is overridable with `TG_ROUTE_TELEMETRY_PATH`.
/// `scripts/summarize_route_telemetry.py` aggregates it.
const TG_ROUTE_TELEMETRY_ENV: &str = "TG_ROUTE_TELEMETRY";
const TG_ROUTE_TELEMETRY_PATH_ENV: &str = "TG_ROUTE_TELEMETRY_PATH";

fn route_telemetry_enabled() -> bool {
    static ENABLED: OnceLock<bool> = OnceLock::new();
    *ENABLED.get_or_init(|| env_flag_enabled(TG_ROUTE_TELEMETRY_ENV))
}

fn route_telemetry_path() -> PathBuf {
    env::var_os(TG_ROUTE_TELEMETRY_PATH_ENV)
        .map(PathBuf::from)
        .unwrap_or_else(|| env::temp_dir().join("tg-route-telemetry.jsonl"))
}

/// BEST-EFFORT and fail-silent by design: telemetry must never change what a search returns, so
/// every I/O error here is discarded rather than propagated. Gated behind a cached env read so a
/// disabled run pays one `OnceLock` load.
fn record_plain_text_route_telemetry(stage: &str, request: &PlainTextNativeRequest) {
    if !route_telemetry_enabled() {
        return;
    }
    let record = serde_json::json!({
        "stage": stage,
        "admitted": native_can_serve_plain_text(request),
        "cheap_checks_pass": plain_text_native_cheap_checks_pass(request),
        "only_allowed_flags": request.only_allowed_flags,
        "structured_output": request.structured_output,
        "explicit_format": request.explicit_format,
        "stdout_is_terminal": request.stdout_is_terminal,
        "rg_config_env_present": request.rg_config_env_present,
        "path_was_implicit": request.path_was_implicit,
        "pattern_count": request.pattern_count,
        "pattern_is_empty": request.pattern_is_empty,
        "path_count": request.path_count,
        "single_path_is_regular_file": request.single_path_is_regular_file,
        "single_path_is_stdin_sentinel": request.single_path_is_stdin_sentinel,
        "pattern_is_native_renderable": request.pattern_is_native_renderable,
        "single_path_renders_identically": request.single_path_renders_identically,
    });
    let _ = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(route_telemetry_path())
        .and_then(|mut file| writeln!(file, "{record}"));
}

/// Raw-argv adapter for `native_can_serve_plain_text`, used by the pre-clap default search front
/// door (`parse_default_search_frontdoor_args`). Works on tokens rather than a parsed struct
/// because that front door runs BEFORE clap.
///
/// AGREEMENT CONTRACT: this adapter and `plain_text_native_request_for_search` (the clap-side
/// computation) must return the SAME verdict for the same argv, and
/// `plain_text_native_adapters_agree_on_every_shape` drives both to prove it. That is not
/// cosmetic: the front door is only ONE of the paths into `route_search` -- anything
/// `parse_early_ripgrep_args` rejects (a combined short like `-in`, a `-w`/`-F` request, an
/// unknown token) reaches clap and `route_search` anyway, so the raw-argv list is NOT the
/// admission gate. `search_args_allow_plain_text_native` is. Mirroring clap's argv handling here
/// (combined short clusters, `-e`/`--regexp` patterns) is what keeps the two in step.
///
/// `search_args` is the normalized `["tg", "search", ...]` form, so operand scanning starts at 2.
fn frontdoor_search_is_native_plain_text_eligible(search_args: &[OsString]) -> bool {
    frontdoor_search_is_native_plain_text_eligible_with_terminal(search_args, stdout_is_terminal())
}

/// Terminal-state-injected core of `frontdoor_search_is_native_plain_text_eligible`, mirroring the
/// `resolve_search_request` / `resolve_search_request_with_stdin` split so tests never depend on
/// whether the harness captured stdout.
fn frontdoor_search_is_native_plain_text_eligible_with_terminal(
    search_args: &[OsString],
    stdout_is_terminal: bool,
) -> bool {
    let tokens = search_args
        .iter()
        .skip(2)
        .map(|token| token.to_string_lossy().to_string())
        .collect::<Vec<_>>();

    let mut explicit_patterns: Vec<String> = Vec::new();
    let mut positionals: Vec<String> = Vec::new();
    let mut ignore_case = false;
    let mut fixed_strings = false;
    let mut word_boundary = false;
    let mut end_of_flags = false;
    let mut index = 0usize;
    while index < tokens.len() {
        let token = &tokens[index];
        if end_of_flags {
            positionals.push(token.clone());
        } else if token == "--" {
            // clap's end-of-options sentinel: everything after it is a positional.
            end_of_flags = true;
        } else if token == "-e" || token == "--regexp" {
            index += 1;
            match tokens.get(index) {
                Some(value) => explicit_patterns.push(value.clone()),
                None => return false,
            }
        } else if let Some(value) = token.strip_prefix("--regexp=") {
            explicit_patterns.push(value.to_string());
        } else if token.starts_with('-') && token != "-" {
            if !plain_text_native_flag_token_is_allowed(token) {
                return false;
            }
            if let Some(long) = token.strip_prefix("--") {
                // A long form must be understood HERE, not merely allow-listed, because its value
                // feeds the matcher. Refuse an unrecognized one so a future addition to
                // `PLAIN_TEXT_NATIVE_ALLOWED_FLAGS` cannot silently reach `build_matcher` with the
                // wrong flags -- the fail-closed direction.
                match long {
                    "ignore-case" => ignore_case = true,
                    "fixed-strings" => fixed_strings = true,
                    "word-regexp" => word_boundary = true,
                    "line-number" | "verbose" => {}
                    _ => return false,
                }
            } else {
                // A combined short cluster; the guard above already proved every letter admitted.
                for letter in token.chars().skip(1) {
                    match letter {
                        'i' => ignore_case = true,
                        'F' => fixed_strings = true,
                        'w' => word_boundary = true,
                        _ => {}
                    }
                }
            }
        } else {
            positionals.push(token.clone());
        }
        index += 1;
    }

    // Mirrors `resolve_search_request_with_stdin`: with no `-e`, the FIRST positional is the
    // pattern and the rest are paths; with `-e`, every positional is a path.
    let (patterns, paths) = if explicit_patterns.is_empty() {
        match positionals.split_first() {
            Some((pattern, paths)) => (vec![pattern.clone()], paths.to_vec()),
            None => (Vec::new(), Vec::new()),
        }
    } else {
        (explicit_patterns, positionals)
    };

    let single_path = (paths.len() == 1).then(|| Path::new(&paths[0]));
    let facts = PlainTextNativeRequest {
        pattern_count: patterns.len(),
        pattern_is_empty: patterns.iter().any(String::is_empty),
        pattern_is_native_renderable: false,
        path_count: paths.len(),
        path_was_implicit: paths.is_empty(),
        single_path_is_regular_file: single_path.is_some_and(Path::is_file),
        single_path_is_stdin_sentinel: paths.iter().any(|path| path == "-"),
        single_path_renders_identically: false,
        structured_output: false,
        explicit_format: false,
        stdout_is_terminal,
        // Overwritten by `finish_plain_text_native_request`, the single owner of these clauses.
        rg_config_env_present: false,
        only_allowed_flags: true,
    };
    let facts = finish_plain_text_native_request(
        facts,
        "frontdoor",
        patterns.first().map(String::as_str),
        ignore_case,
        fixed_strings,
        word_boundary,
        single_path,
    );
    native_can_serve_plain_text(&facts)
}

/// Parsed-`SearchArgs` adapter for `native_can_serve_plain_text`, used by `handle_ripgrep_search`.
///
/// The destructure below names EVERY `SearchArgs` field with no `..` rest pattern, mirroring
/// `index_flag_violations`' compile-time ratchet: adding a field to `SearchArgs` fails this
/// function's compilation until a human classifies it. A new flag can therefore never become
/// silently "allowed" on the native plain-text route -- the fail-closed direction is to add it to
/// the excluded list below, which preserves today's `rg` behavior for it.
fn search_args_allow_plain_text_native(args: &SearchArgs) -> bool {
    let SearchArgs {
        // ADMITTED -- see PLAIN_TEXT_NATIVE_ALLOWED_FLAGS for the per-flag proof.
        ignore_case: _,
        fixed_strings: _,
        word_regexp: _,
        line_number: _,
        verbose: _,
        // QUERY-DEFINING -- cardinality is enforced by the predicate's pattern_count/path_count.
        regexp: _,
        pattern: _,
        path: _,
        // EXCLUDED -- every one of these must be at its default for the request to stay eligible.
        no_fixed_strings,
        invert_match,
        no_invert_match,
        count,
        count_matches,
        no_line_number,
        column,
        no_column,
        replace,
        format,
        sort,
        sort_reverse,
        sort_files,
        null,
        null_data,
        multiline,
        multiline_dotall,
        context,
        after_context,
        before_context,
        max_count,
        max_depth,
        smart_case,
        globs,
        no_ignore,
        ignore,
        no_ignore_dot,
        no_ignore_exclude,
        no_ignore_files,
        no_ignore_global,
        no_ignore_parent,
        hidden,
        no_hidden,
        follow,
        text,
        files_with_matches,
        files_without_match,
        file_type,
        index,
        force_cpu,
        gpu_device_ids,
        color,
        path_separator,
        only_matching,
        vimgrep,
        passthru,
        json,
        ndjson,
        pcre2,
        auto_hybrid_regex,
        unicode,
        pcre2_unicode,
        max_filesize,
        no_ignore_vcs,
        require_git,
        messages,
        no_config,
        pcre2_version,
        type_list,
        version,
    } = args;

    !*no_fixed_strings
        && !*invert_match
        && !*no_invert_match
        && !*count
        && !*count_matches
        && !*no_line_number
        && !*column
        && !*no_column
        && replace.is_none()
        && format.is_none()
        && sort.is_none()
        && sort_reverse.is_none()
        && !*sort_files
        && !*null
        && !*null_data
        && !*multiline
        && !*multiline_dotall
        && context.is_none()
        && after_context.is_none()
        && before_context.is_none()
        && max_count.is_none()
        && max_depth.is_none()
        && !*smart_case
        && globs.is_empty()
        && !*no_ignore
        && !*ignore
        && !*no_ignore_dot
        && !*no_ignore_exclude
        && !*no_ignore_files
        && !*no_ignore_global
        && !*no_ignore_parent
        && !*hidden
        && !*no_hidden
        && !*follow
        && !*text
        && !*files_with_matches
        && !*files_without_match
        && file_type.is_empty()
        && !*index
        && !*force_cpu
        && gpu_device_ids.is_empty()
        && color.is_none()
        && path_separator.is_none()
        && !*only_matching
        && !*vimgrep
        && !*passthru
        && !*json
        && !*ndjson
        && !*pcre2
        && !*auto_hybrid_regex
        && !*unicode
        && !*pcre2_unicode
        && max_filesize.is_none()
        && !*no_ignore_vcs
        && !*require_git
        && !*messages
        && !*no_config
        && !*pcre2_version
        && !*type_list
        && !*version
}

fn ripgrep_args_need_broad_generated_guard(args: &RipgrepSearchArgs) -> bool {
    let has_scan_bound =
        args.max_depth.is_some() || !args.globs.is_empty() || !args.file_types.is_empty();
    !has_scan_bound && (args.no_ignore || args.no_ignore_files || args.no_ignore_vcs)
}

fn search_args_have_generated_scan_bound(args: &SearchArgs) -> bool {
    args.max_depth.is_some() || !args.globs.is_empty() || !args.file_type.is_empty()
}

fn search_args_need_broad_generated_guard(args: &SearchArgs) -> bool {
    !search_args_have_generated_scan_bound(args)
        && (args.no_ignore || args.no_ignore_files || args.no_ignore_vcs)
}

fn is_broad_generated_scan_dir_name(name: &str) -> bool {
    BROAD_GENERATED_SCAN_DIR_NAMES
        .iter()
        .any(|candidate| candidate.eq_ignore_ascii_case(name))
}

fn generated_scan_dir_names(paths: &[String], include_child_dirs: bool) -> Vec<String> {
    let mut found = BTreeSet::new();
    for raw_path in paths {
        if raw_path.is_empty() || raw_path == "-" || raw_path.starts_with('-') {
            continue;
        }
        let path = Path::new(raw_path);
        if !path.is_dir() {
            continue;
        }
        if let Some(name) = path.file_name().and_then(|name| name.to_str()) {
            if is_broad_generated_scan_dir_name(name) {
                found.insert(name.to_string());
            }
        }
        if !include_child_dirs {
            continue;
        }
        let entries = match std::fs::read_dir(path) {
            Ok(entries) => entries,
            Err(_) => continue,
        };
        for entry in entries.flatten() {
            let is_dir = entry
                .file_type()
                .map(|file_type| file_type.is_dir())
                .unwrap_or(false);
            if !is_dir {
                continue;
            }
            let name = entry.file_name().to_string_lossy().to_string();
            if is_broad_generated_scan_dir_name(&name) {
                found.insert(name);
            }
        }
    }
    found.into_iter().collect()
}

fn format_broad_generated_scan_error(generated_dirs: &[String]) -> String {
    let mut visible_dirs = generated_dirs
        .iter()
        .take(8)
        .cloned()
        .collect::<Vec<_>>()
        .join(", ");
    if generated_dirs.len() > 8 {
        visible_dirs.push_str(", ...");
    }
    format!(
        "Error: broad generated-root scan refused as a safety guard, not a zero-match result: \
path contains generated, cache, \
or dependency directories ({visible_dirs}). Scope the path, add --glob, --type, \
or --max-depth, or pass --allow-broad-generated-scan to opt in.\n\
For bounded output:\n\
tg search --files <path> --hidden --max-depth <N>\n\
For intentional broad scans:\n\
--allow-broad-generated-scan"
    )
}

// Bug #88/#480/#100: `IMPLICIT_SEARCH_WALK_FILE_CEILING`, `implicit_search_walk_exceeds_ceiling`,
// and `format_unbounded_implicit_search_walk_error` used to live here. They are now HOISTED into
// `rg_passthrough.rs` (a library module) so `execute_ripgrep_search` -- which lives there, not
// here -- can call the probe as its own first statement, closing a native-frontdoor bypass this
// binary-crate-local copy could not reach (this `tg` binary and the `tensor_grep_rs` library are
// separate crate compilations; the library cannot call back into a function defined only in this
// bin crate). See `rg_passthrough.rs` for the full history and the current implementation; this
// file's existing tests re-import the moved items via `use tensor_grep_rs::rg_passthrough::{...}`
// inside `mod tests` below.

fn parse_early_ripgrep_args(raw_args: &[OsString]) -> Option<RipgrepSearchArgs> {
    let mut args = RipgrepSearchArgs {
        files: false,
        json: false,
        ignore_case: false,
        fixed_strings: false,
        no_fixed_strings: false,
        invert_match: false,
        no_invert_match: false,
        count: false,
        count_matches: false,
        line_number: false,
        no_line_number: false,
        column: false,
        only_matching: false,
        context: None,
        after_context: None,
        before_context: None,
        max_count: None,
        word_regexp: false,
        smart_case: false,
        globs: Vec::new(),
        ignore: false,
        no_ignore: false,
        no_ignore_dot: false,
        no_ignore_exclude: false,
        no_ignore_files: false,
        no_ignore_global: false,
        no_ignore_parent: false,
        require_git: false,
        hidden: false,
        no_hidden: false,
        follow: false,
        text: false,
        files_with_matches: false,
        files_without_match: false,
        file_types: Vec::new(),
        color: None,
        path_separator: None,
        replace: None,
        vimgrep: false,
        passthru: false,
        no_config: false,
        sort: None,
        sort_reverse: None,
        sort_files: false,
        max_depth: None,
        null: false,
        null_data: false,
        multiline: false,
        no_multiline: false,
        multiline_dotall: false,
        no_multiline_dotall: false,
        patterns: Vec::new(),
        paths: Vec::new(),
        // Placeholder -- overwritten below once we know whether the caller supplied an explicit
        // PATH positional (audit #100: this is THE FIX, see the `-e`-vs-positional branch below).
        path_was_implicit: false,
        no_ignore_vcs: false,
        pcre2: false,
        no_pcre2: false,
        pcre2_unicode: false,
        no_pcre2_unicode: false,
        no_crlf: false,
        no_encoding: false,
        no_mmap: false,
        no_pre: false,
        no_search_zip: false,
        auto_hybrid_regex: false,
        no_auto_hybrid_regex: false,
        unicode: false,
        no_text: false,
        no_binary: false,
        no_follow: false,
        no_glob_case_insensitive: false,
        no_ignore_file_case_insensitive: false,
        ignore_dot: false,
        ignore_exclude: false,
        ignore_files: false,
        ignore_global: false,
        ignore_messages: false,
        ignore_parent: false,
        ignore_vcs: false,
        no_one_file_system: false,
        no_block_buffered: false,
        no_byte_offset: false,
        no_column: false,
        no_context_separator: false,
        no_include_zero: false,
        no_line_buffered: false,
        no_max_columns_preview: false,
        no_trim: false,
        no_json: false,
        messages: false,
        no_stats: false,
        max_filesize: None,
    };

    let mut positionals: Vec<String> = Vec::new();
    let tokens = raw_args
        .iter()
        .skip(2)
        .map(|arg| arg.to_string_lossy().to_string())
        .collect::<Vec<_>>();
    let mut index = 0usize;
    while index < tokens.len() {
        let token = &tokens[index];
        match token.as_str() {
            "-i" | "--ignore-case" => args.ignore_case = true,
            "-F" | "--fixed-strings" => args.fixed_strings = true,
            "--no-fixed-strings" => args.no_fixed_strings = true,
            "-v" | "--invert-match" => args.invert_match = true,
            "--no-invert-match" => args.no_invert_match = true,
            "-c" | "--count" => args.count = true,
            "--count-matches" => args.count_matches = true,
            "--json" => args.json = true,
            "-n" | "--line-number" => {
                args.line_number = true;
                args.no_line_number = false;
            }
            "-N" | "--no-line-number" => {
                args.line_number = false;
                args.no_line_number = true;
            }
            "-o" | "--only-matching" => args.only_matching = true,
            "-w" | "--word-regexp" => args.word_regexp = true,
            "-0" | "--null" => args.null = true,
            "--null-data" => args.null_data = true,
            "-U" | "--multiline" => args.multiline = true,
            "--no-multiline" => args.no_multiline = true,
            "--multiline-dotall" => args.multiline_dotall = true,
            "--no-multiline-dotall" => args.no_multiline_dotall = true,
            "--ignore" => {
                args.ignore = true;
                args.no_ignore = false;
            }
            "--no-ignore" => {
                args.ignore = false;
                args.no_ignore = true;
            }
            "--no-ignore-dot" => args.no_ignore_dot = true,
            "--no-ignore-exclude" => args.no_ignore_exclude = true,
            "--no-ignore-files" => args.no_ignore_files = true,
            "--no-ignore-global" => args.no_ignore_global = true,
            "--no-ignore-parent" => args.no_ignore_parent = true,
            "--require-git" => args.require_git = true,
            "--no-config" => args.no_config = true,
            "--passthru" => args.passthru = true,
            "--passthrough" => args.passthru = true,
            "--auto-hybrid-regex" => args.auto_hybrid_regex = true,
            "--no-auto-hybrid-regex" => args.no_auto_hybrid_regex = true,
            "--pcre2-unicode" => {
                args.pcre2_unicode = true;
            }
            "--no-pcre2-unicode" => args.no_pcre2_unicode = true,
            "--no-crlf" => args.no_crlf = true,
            "--no-encoding" => args.no_encoding = true,
            "--no-mmap" => args.no_mmap = true,
            "--no-pcre2" => args.no_pcre2 = true,
            "--no-pre" => args.no_pre = true,
            "--no-search-zip" => args.no_search_zip = true,
            "--unicode" => args.unicode = true,
            "--no-text" => args.no_text = true,
            "--no-binary" => args.no_binary = true,
            "--no-follow" => args.no_follow = true,
            "--no-glob-case-insensitive" => args.no_glob_case_insensitive = true,
            "--no-ignore-file-case-insensitive" => {
                args.no_ignore_file_case_insensitive = true;
            }
            "--ignore-dot" => args.ignore_dot = true,
            "--ignore-exclude" => args.ignore_exclude = true,
            "--ignore-files" => args.ignore_files = true,
            "--ignore-global" => args.ignore_global = true,
            "--ignore-messages" => args.ignore_messages = true,
            "--ignore-parent" => args.ignore_parent = true,
            "--ignore-vcs" => args.ignore_vcs = true,
            "--no-one-file-system" => args.no_one_file_system = true,
            "--no-block-buffered" => args.no_block_buffered = true,
            "--no-byte-offset" => args.no_byte_offset = true,
            "--column" => {
                args.column = true;
                args.no_column = false;
            }
            "--no-column" => {
                args.column = false;
                args.no_column = true;
            }
            "--no-context-separator" => args.no_context_separator = true,
            "--no-include-zero" => args.no_include_zero = true,
            "--no-line-buffered" => args.no_line_buffered = true,
            "--no-max-columns-preview" => args.no_max_columns_preview = true,
            "--no-trim" => args.no_trim = true,
            "--no-json" => args.no_json = true,
            "--no-stats" => args.no_stats = true,
            "--messages" => args.messages = true,
            "-C" | "--context" => {
                index += 1;
                let value = tokens.get(index)?.parse::<usize>().ok()?;
                args.context = Some(value);
            }
            "-A" | "--after-context" => {
                index += 1;
                let value = tokens.get(index)?.parse::<usize>().ok()?;
                args.after_context = Some(value);
            }
            "-B" | "--before-context" => {
                index += 1;
                let value = tokens.get(index)?.parse::<usize>().ok()?;
                args.before_context = Some(value);
            }
            "-m" | "--max-count" => {
                index += 1;
                let value = tokens.get(index)?.parse::<usize>().ok()?;
                args.max_count = Some(value);
            }
            "-d" | "--max-depth" | "--maxdepth" => {
                index += 1;
                let value = tokens.get(index)?.parse::<usize>().ok()?;
                args.max_depth = Some(value);
            }
            _ if token.starts_with("--max-count=") => {
                let value = token
                    .split_once('=')
                    .and_then(|(_, value)| value.parse::<usize>().ok())?;
                args.max_count = Some(value);
            }
            _ if token.starts_with("--max-depth=") => {
                let value = token
                    .split_once('=')
                    .and_then(|(_, value)| value.parse::<usize>().ok())?;
                args.max_depth = Some(value);
            }
            _ if token.starts_with("--maxdepth=") => {
                let value = token
                    .split_once('=')
                    .and_then(|(_, value)| value.parse::<usize>().ok())?;
                args.max_depth = Some(value);
            }
            "--color" => {
                index += 1;
                args.color = Some(tokens.get(index)?.clone());
            }
            "--path-separator" => {
                index += 1;
                args.path_separator = Some(tokens.get(index)?.clone());
            }
            _ if token.starts_with("--path-separator=") => {
                args.path_separator =
                    Some(token.split_once('=').map(|(_, value)| value.to_string())?);
            }
            "--vimgrep" => args.vimgrep = true,
            "--no-hidden" => {
                args.hidden = false;
                args.no_hidden = true;
            }
            "--hidden" | "-." => {
                args.hidden = true;
                args.no_hidden = false;
            }
            "--format" => {
                index += 1;
                if tokens.get(index)? != "rg" {
                    return None;
                }
            }
            _ if token.starts_with("--format=") => {
                if token.split_once('=').map(|(_, value)| value) != Some("rg") {
                    return None;
                }
            }
            "--sort" => {
                index += 1;
                args.sort = Some(tokens.get(index)?.clone());
            }
            _ if token.starts_with("--sort=") => {
                args.sort = Some(token.split_once('=').map(|(_, value)| value.to_string())?);
            }
            "--sortr" => {
                index += 1;
                args.sort_reverse = Some(tokens.get(index)?.clone());
            }
            _ if token.starts_with("--sortr=") => {
                args.sort_reverse =
                    Some(token.split_once('=').map(|(_, value)| value.to_string())?);
            }
            "--sort-files" => args.sort_files = true,
            "--no-sort-files" => args.sort_files = false,
            "-e" | "--regexp" => {
                index += 1;
                args.patterns.push(tokens.get(index)?.clone());
            }
            _ if token.starts_with("--regexp=") => {
                args.patterns
                    .push(token.split_once('=').map(|(_, value)| value.to_string())?);
            }
            "-g" | "--glob" => {
                index += 1;
                args.globs.push(tokens.get(index)?.clone());
            }
            _ if token.starts_with("--glob=") => {
                args.globs
                    .push(token.split_once('=').map(|(_, value)| value.to_string())?);
            }
            "-t" | "--type" => {
                index += 1;
                args.file_types.push(tokens.get(index)?.clone());
            }
            _ if token.starts_with("--type=") => {
                args.file_types
                    .push(token.split_once('=').map(|(_, value)| value.to_string())?);
            }
            // LOAD-BEARING (task #271): this is the actual fail-closed guarantee that an
            // unrecognized flag never silently reaches the native fast path -- every arm above
            // is a finite, explicit allowlist, so any `-`-prefixed token this function does not
            // otherwise understand (including a ripgrep combined-short-flag cluster like `-uu`,
            // `-uuu`, or `-iu` that `SEARCH_PYTHON_PASSTHROUGH_FLAGS`'s exact-token matching
            // also does not recognize) falls through to here and returns `None`, forcing the
            // caller back to the full Python CLI rather than being silently misparsed or
            // dropped. Do not narrow this arm without an equally fail-closed replacement.
            _ if token.starts_with('-') => return None,
            _ => positionals.push(token.clone()),
        }
        index += 1;
    }

    if args.patterns.is_empty() {
        if positionals.len() < 2 {
            return None;
        }
        // Positional-pattern form always requires >= 2 positionals (pattern + >= 1 path) to even
        // reach this branch, so the path is always explicit here.
        args.patterns.push(positionals[0].clone());
        args.paths = positionals[1..].to_vec();
        args.path_was_implicit = false;
    } else {
        // THE FIX (audit #100): `-e`/`--regexp` form. `positionals` here is whatever the user
        // supplied as trailing PATH arguments (the pattern came via `-e`, not a positional) --
        // record `path_was_implicit` from whether that list is empty BEFORE the `["."]` default
        // substitution below, mirroring `resolve_search_request_with_stdin` (the full-CLI
        // equivalent, main.rs `path_was_implicit = true` set inside its own `paths.is_empty()`
        // branch). This is the exact gap that let `tg search -e "TODO" --glob "*.py"` with no
        // PATH bypass the walk-ceiling probe entirely: `paths` silently became `["."]` with no
        // record that the root was implicit, so no caller could gate on it.
        args.paths = positionals;
        args.path_was_implicit = args.paths.is_empty();
        if args.path_was_implicit && !stdin_should_search_implicit_path() {
            args.paths.push(".".to_string());
        }
    }
    Some(args)
}

fn parse_default_search_frontdoor_args(raw_args: &[OsString]) -> Option<RipgrepSearchArgs> {
    let search_args = normalize_top_level_format_search_args(raw_args)?;
    let explicit_rg_format = requests_explicit_rg_format(&search_args);
    let args = parse_early_ripgrep_args(&search_args)?;
    if args.json && !explicit_rg_format {
        return None;
    }
    if ripgrep_args_need_broad_generated_guard(&args) {
        return None;
    }
    // Perf: a provably-rg-identical plain-text search does NOT need the `rg` subprocess. Decline
    // the passthrough so the request falls through to clap -> `handle_ripgrep_search` ->
    // `route_search`, which re-derives the SAME predicate and selects the in-process native CPU
    // engine. Declining here is behavior-preserving by construction: every downstream front door
    // (`search_format_python_passthrough_args`, the positional CLI, clap) already handles this
    // shape today, and if the native engine errors, `allow_rg_fallback` still hands the request
    // to real `rg`. `--format rg` is exempted because it is an explicit demand for ripgrep's own
    // renderer.
    if !explicit_rg_format && frontdoor_search_is_native_plain_text_eligible(&search_args) {
        return None;
    }
    (explicit_rg_format || should_use_early_ripgrep_fast_path(&args)).then_some(args)
}

fn parse_early_positional_ripgrep_args(raw_args: &[OsString]) -> Option<RipgrepSearchArgs> {
    let cli = PositionalCli::try_parse_from(raw_args).ok()?;
    let pattern = cli.pattern.clone()?;
    let paths = implicit_search_paths(&cli.path, stdin_should_search_implicit_path());

    if cli.replace.is_some() || cli.force_cpu || !cli.gpu_device_ids.is_empty() {
        return None;
    }
    if cli.json || cli.ndjson || cli.verbose {
        return None;
    }

    Some(positional_ripgrep_args(&cli, &pattern, &paths))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;
    // Bug #88/#480/#100: these 3 items moved to `rg_passthrough.rs` (see the breadcrumb comment
    // above `parse_early_ripgrep_args`); re-imported here so this file's existing tests keep
    // compiling unqualified via `use super::*` above.
    use tensor_grep_rs::rg_passthrough::{
        implicit_search_walk_exceeds_ceiling, IMPLICIT_SEARCH_WALK_FILE_CEILING,
    };

    fn parse_run_args(tokens: &[&str]) -> RunArgs {
        use clap::Parser;
        let raw_args = tokens.iter().map(OsString::from).collect::<Vec<_>>();
        let cli = CommandCli::try_parse_from(&raw_args).expect("expected CLI args to parse");
        match cli.command {
            Commands::Run(args) => args,
            _ => panic!("expected run command"),
        }
    }

    fn parse_args(tokens: &[&str]) -> RipgrepSearchArgs {
        let raw_args = tokens.iter().map(OsString::from).collect::<Vec<_>>();
        parse_early_ripgrep_args(&raw_args).expect("expected early rg args to parse")
    }

    // Task #271: `SEARCH_PYTHON_PASSTHROUGH_FLAGS` is exact-token matching, so it recognizes the
    // literal spellings `-u`/`--unrestricted` but NOT a ripgrep combined-short-flag cluster like
    // `-uu`/`-iu` -- those are each a different literal token. The actual fail-closed guarantee
    // that such a cluster never silently reaches the native fast path is
    // `parse_early_ripgrep_args`'s own catch-all arm (`_ if token.starts_with('-') => return
    // None`), which forces a fall-through to the full Python CLI for any unrecognized
    // `-`-prefixed token. These tests assert that guarantee directly, at the function it actually
    // lives in, rather than only via the flags list this comment could otherwise be mistaken for
    // covering it.
    #[test]
    fn parse_early_ripgrep_args_rejects_combined_unrestricted_short_cluster() {
        let raw_args = ["tg", "search", "-uu", "needle", "."]
            .iter()
            .map(OsString::from)
            .collect::<Vec<_>>();
        assert!(
            parse_early_ripgrep_args(&raw_args).is_none(),
            "-uu must fall through to the Python CLI, not be silently misparsed by the native \
             fast-path parser"
        );
    }

    #[test]
    fn parse_early_ripgrep_args_rejects_combined_ignore_case_unrestricted_cluster() {
        let raw_args = ["tg", "search", "-iu", "needle", "."]
            .iter()
            .map(OsString::from)
            .collect::<Vec<_>>();
        assert!(
            parse_early_ripgrep_args(&raw_args).is_none(),
            "-iu must fall through to the Python CLI, not be silently misparsed by the native \
             fast-path parser"
        );
    }

    fn parse_default_frontdoor_args(tokens: &[&str]) -> RipgrepSearchArgs {
        let raw_args = tokens.iter().map(OsString::from).collect::<Vec<_>>();
        parse_default_search_frontdoor_args(&raw_args).expect("expected frontdoor args to parse")
    }

    fn parse_search_args(tokens: &[&str]) -> SearchArgs {
        use clap::Parser;
        let raw_args = tokens.iter().map(OsString::from).collect::<Vec<_>>();
        let cli = CommandCli::try_parse_from(&raw_args).expect("expected CLI args to parse");
        match cli.command {
            Commands::Search(args) => args,
            _ => panic!("expected search command"),
        }
    }

    fn parse_positional_cli(tokens: &[&str]) -> PositionalCli {
        use clap::Parser;
        let raw_args = tokens.iter().map(OsString::from).collect::<Vec<_>>();
        PositionalCli::try_parse_from(&raw_args).expect("expected CLI args to parse")
    }

    fn json_conflicts(tokens: &[&str]) -> Vec<String> {
        let raw_args = tokens.iter().map(OsString::from).collect::<Vec<_>>();
        json_aggregate_render_flag_conflicts(&raw_args)
    }

    #[test]
    fn json_aggregate_flags_incompatible_render_flags() {
        // audit C3: plain --json + a render-only flag must be flagged so the native binary
        // rejects it directly instead of delegating to (and deadlocking via) a stale Python
        // sidecar in the native<->python re-exec chain.
        assert_eq!(
            json_conflicts(&["tg", "search", "--json", "-b", "foo", "f.py"]),
            vec!["-b".to_string()]
        );
        assert_eq!(
            json_conflicts(&["tg", "search", "--json", "--heading", "foo", "f.py"]),
            vec!["--heading".to_string()]
        );
        // Option-first form (no explicit `search` subcommand) flags trigger flags too:
        // `-b` is in SEARCH_PYTHON_PASSTHROUGH_FLAGS so it is recognized as a search.
        assert_eq!(
            json_conflicts(&["tg", "--json", "-b", "foo", "f.py"]),
            vec!["-b".to_string()]
        );
        // `tg search --json --passthru` (explicit search) delegates via the --passthru gate,
        // so the native guard must reject it directly.
        assert_eq!(
            json_conflicts(&["tg", "search", "--json", "--passthru", "foo", "f.py"]),
            vec!["--passthru".to_string()]
        );
        // --byte-offset is an alias of -b and normalizes to the canonical spelling.
        assert_eq!(
            json_conflicts(&["tg", "search", "--json", "--byte-offset", "foo", "f.py"]),
            vec!["-b".to_string()]
        );
        assert_eq!(
            json_conflicts(&["tg", "search", "--json", "--passthru", "-b", "foo", "f.py"]),
            vec!["--passthru".to_string(), "-b".to_string()]
        );
    }

    #[test]
    fn json_aggregate_allows_plain_json_and_rg_format() {
        // plain --json (no render flag) is the native aggregate path — allowed.
        assert!(json_conflicts(&["tg", "search", "--json", "foo", "f.py"]).is_empty());
        // --format rg --json carries render metadata via ripgrep JSON Lines — allowed.
        assert!(
            json_conflicts(&["tg", "search", "--format", "rg", "--json", "-b", "foo", "f.py"])
                .is_empty()
        );
        // a literal render-flag-looking pattern after `--` is not a flag.
        assert!(json_conflicts(&["tg", "search", "--json", "--", "--passthru"]).is_empty());
    }

    #[test]
    fn json_aggregate_format_rg_after_double_dash_does_not_suppress_real_conflict() {
        // Regression (audit MED): `--format rg` / `--format=rg` smuggled AFTER `--` is a
        // literal search pattern, not the format flag, so it must NOT satisfy the rg-format
        // allowance. The genuine `-b` render-flag conflict BEFORE `--` must still be reported
        // (otherwise the native binary delegates the --json+render combo to the Python
        // sidecar, re-opening the C3 fork-bomb against a guard-less Python).
        assert_eq!(
            json_conflicts(&["tg", "search", "--json", "-b", "--", "--format", "rg"]),
            vec!["-b".to_string()]
        );
        assert_eq!(
            json_conflicts(&["tg", "search", "--json", "-b", "--", "--format=rg"]),
            vec!["-b".to_string()]
        );
    }

    // Not cuda-gated (unlike the tests that originally motivated it): task #131 F3 tests against
    // the non-cuda-gated `gpu_sidecar_search_payload`/`ripgrep_args_for_gpu_params`/
    // `gpu_cpu_fallback_unhonorable_flag` also need a `GpuSearchParams` fixture.
    fn gpu_params_for_patterns(patterns: &[String]) -> GpuSearchParams<'_> {
        GpuSearchParams {
            patterns,
            query: patterns.first().map(String::as_str).unwrap_or_default(),
            path: ".",
            line_number: false,
            ignore_case: false,
            smart_case: false,
            fixed_strings: true,
            invert_match: false,
            count: false,
            context: None,
            max_count: None,
            word_regexp: false,
            globs: Vec::new(),
            hidden: false,
            max_depth: None,
            text: false,
            no_ignore: true,
            gpu_device_ids: &[0],
            json: true,
            ndjson: false,
            verbose: false,
            replace: None,
            only_matching: false,
            max_filesize: None,
            color: None,
            no_ignore_vcs: false,
            path_was_implicit: false,
        }
    }

    #[cfg(feature = "cuda")]
    #[test]
    fn gpu_native_route_rejects_case_insensitive_smart_case_patterns() {
        let lowercase = vec!["warning".to_string()];
        let mut params = gpu_params_for_patterns(&lowercase);
        params.smart_case = true;
        assert_eq!(
            gpu_native_fallback_reason(&params),
            Some("case-insensitive searches are not yet supported by native GPU routing")
        );

        let uppercase = vec!["WARNING".to_string()];
        let mut params = gpu_params_for_patterns(&uppercase);
        params.smart_case = true;
        assert_eq!(gpu_native_fallback_reason(&params), None);
    }

    #[cfg(feature = "cuda")]
    #[test]
    fn gpu_native_route_rejects_line_terminator_patterns() {
        let patterns = vec!["foo\nbar".to_string()];
        let params = gpu_params_for_patterns(&patterns);

        assert_eq!(
            gpu_native_fallback_reason(&params),
            Some("line-terminator patterns require CPU or sidecar routing")
        );
    }

    #[cfg(feature = "cuda")]
    #[test]
    fn gpu_native_route_rejects_binary_as_text_searches() {
        let patterns = vec!["SECRET".to_string()];
        let mut params = gpu_params_for_patterns(&patterns);
        params.text = true;

        assert_eq!(
            gpu_native_fallback_reason(&params),
            Some("binary-as-text searches are not yet supported by native GPU routing")
        );
    }

    // -- task #131 F3 (Backend Fail-Closed Contract: GpuSearchParams flag completeness) --------
    //
    // Before this fix, `GpuSearchParams` had no field at all for `--replace`, `--only-matching`,
    // `--max-filesize`, `--color`, or `--no-ignore-vcs`, and `line_number` was hardcoded at every
    // construction site -- `tg PAT --gpu-device-ids 0 --replace X` printed "falling back to
    // native CPU" and then silently ran WITHOUT `--replace`, exit 0. These tests would not even
    // have COMPILED against that struct (the fields/functions they reference did not exist),
    // which is itself the clearest possible RED signal for a field-completeness bug.

    #[cfg(feature = "cuda")]
    #[test]
    fn gpu_native_route_rejects_previously_unrepresented_flags() {
        let patterns = vec!["needle".to_string()];

        let mut params = gpu_params_for_patterns(&patterns);
        params.replace = Some("REPLACED".to_string());
        assert_eq!(
            gpu_native_fallback_reason(&params),
            Some("--replace searches are not yet supported by native GPU routing")
        );

        let mut params = gpu_params_for_patterns(&patterns);
        params.only_matching = true;
        assert_eq!(
            gpu_native_fallback_reason(&params),
            Some("--only-matching searches are not yet supported by native GPU routing")
        );

        let mut params = gpu_params_for_patterns(&patterns);
        params.max_filesize = Some("10M".to_string());
        assert_eq!(
            gpu_native_fallback_reason(&params),
            Some("--max-filesize is not yet supported by native GPU routing")
        );

        let mut params = gpu_params_for_patterns(&patterns);
        params.color = Some("always".to_string());
        assert_eq!(
            gpu_native_fallback_reason(&params),
            Some("--color is not yet supported by native GPU routing")
        );

        let mut params = gpu_params_for_patterns(&patterns);
        params.no_ignore_vcs = true;
        assert_eq!(
            gpu_native_fallback_reason(&params),
            Some("--no-ignore-vcs is not yet supported by native GPU routing")
        );

        // Baseline: none of the 5 set (plus the pre-existing 8 conditions unset) must still fall
        // through to native-GPU routing, else every GPU search would now wrongly fall back.
        let params = gpu_params_for_patterns(&patterns);
        assert_eq!(gpu_native_fallback_reason(&params), None);
    }

    // GPU-P0-3 (#171): `validate_requested_cuda_device_ids` (gpu_native.rs) raises "invalid CUDA
    // device id {id}; available CUDA devices: {..}" for an out-of-range --gpu-device-ids request.
    // Before this fix, `classify_gpu_route_failure`'s catch-all arm relabeled that message as
    // "CUDA initialization failed: ..." -- true-sounding but wrong: nothing failed to initialize,
    // the id was simply invalid. These tests pin the classifier's OWN reason, verbatim, distinct
    // from the genuine-initialization-failure arms below.
    //
    // GPU Phase-0 gate-nit #172 NIT-4 / MF-1: these 3 tests used to ALSO carry their own
    // `#[cfg(feature = "cuda")]`, so a default `cargo test` (no --features cuda) never compiled
    // or ran them at all -- silently, since `mod tests` itself is only `#[cfg(test)]`-gated, so
    // nothing signaled the gap. `classify_gpu_route_failure` and its types are now gated
    // `any(feature = "cuda", test)` (see the definitions above), which is present under plain
    // `cargo test`, so the redundant per-test cuda gate is dropped here and these run by default.
    #[test]
    fn classify_gpu_route_failure_reports_invalid_device_id_as_its_own_fatal_reason() {
        let failure =
            classify_gpu_route_failure("invalid CUDA device id 99; available CUDA devices: 0, 1");

        assert_eq!(failure.kind, GpuRouteFailureKind::Fatal);
        assert_eq!(
            failure.message,
            "invalid CUDA device id 99; available CUDA devices: 0, 1"
        );
    }

    #[test]
    fn classify_gpu_route_failure_does_not_relabel_invalid_device_id_as_init_failure() {
        let failure =
            classify_gpu_route_failure("invalid CUDA device id 3; available CUDA devices: 0, 1, 2");

        assert!(
            !failure.message.starts_with("CUDA initialization failed"),
            "message={}",
            failure.message
        );
    }

    #[test]
    fn classify_gpu_route_failure_still_labels_genuine_init_failures_as_such() {
        // Baseline: the pre-existing "CUDA initialization failed:" arm must keep working --
        // the new invalid-device-id arm must not swallow unrelated Fatal messages.
        let failure = classify_gpu_route_failure("CUDA initialization failed: driver too old");

        assert_eq!(failure.kind, GpuRouteFailureKind::Fatal);
        assert_eq!(
            failure.message,
            "CUDA initialization failed: driver too old"
        );
    }

    // NIT-3 (#172): `gpu_fatal_native_error_kind` is the thin, pure, directly-testable string
    // check the two `exit_structured_search_error_if_needed` emission sites now call instead of
    // hardcoding "gpu_fatal" -- these pin its two branches directly, independent of the enum
    // `kind` (both messages below classify as `GpuRouteFailureKind::Fatal`; only the WIRE error
    // string differs).
    #[test]
    fn gpu_fatal_native_error_kind_distinguishes_invalid_device_id() {
        assert_eq!(
            gpu_fatal_native_error_kind("invalid CUDA device id 99; available CUDA devices: 0, 1"),
            "gpu_invalid_device_id"
        );
        assert_eq!(
            gpu_fatal_native_error_kind("CUDA initialization failed: driver too old"),
            "gpu_fatal"
        );
    }

    #[test]
    fn gpu_cpu_fallback_unhonorable_flag_detects_each_of_the_three() {
        let patterns = vec!["needle".to_string()];

        let params = gpu_params_for_patterns(&patterns);
        assert_eq!(gpu_cpu_fallback_unhonorable_flag(&params), None);

        let mut params = gpu_params_for_patterns(&patterns);
        params.max_filesize = Some("10M".to_string());
        assert_eq!(
            gpu_cpu_fallback_unhonorable_flag(&params),
            Some("--max-filesize")
        );

        let mut params = gpu_params_for_patterns(&patterns);
        params.color = Some("always".to_string());
        assert_eq!(gpu_cpu_fallback_unhonorable_flag(&params), Some("--color"));

        // N3: `--color never`/`auto` are honorable no-ops on the plain-text native engine (mirrors
        // `index_flag_violations`) -- they must NOT trigger the rg redirect. Only `always` (or an
        // unrecognized value) does.
        let mut params = gpu_params_for_patterns(&patterns);
        params.color = Some("never".to_string());
        assert_eq!(gpu_cpu_fallback_unhonorable_flag(&params), None);
        params.color = Some("auto".to_string());
        assert_eq!(gpu_cpu_fallback_unhonorable_flag(&params), None);
        params.color = Some("bogus".to_string());
        assert_eq!(
            gpu_cpu_fallback_unhonorable_flag(&params),
            Some("--color"),
            "an unrecognized --color value is still unhonorable (fail closed)"
        );

        let mut params = gpu_params_for_patterns(&patterns);
        params.no_ignore_vcs = true;
        assert_eq!(
            gpu_cpu_fallback_unhonorable_flag(&params),
            Some("--no-ignore-vcs")
        );

        // `replace`/`only_matching` ARE honorable by `NativeSearchConfig` (see
        // `native_search_config_for_gpu_params`) -- they must NOT trip this redirect-or-refuse
        // gate, or a plain CPU-fallback search would wrongly detour through rg / refuse.
        let mut params = gpu_params_for_patterns(&patterns);
        params.replace = Some("R".to_string());
        params.only_matching = true;
        assert_eq!(gpu_cpu_fallback_unhonorable_flag(&params), None);
    }

    #[test]
    fn ripgrep_args_for_gpu_params_carries_every_previously_dropped_flag() {
        let patterns = vec!["needle".to_string(), "second".to_string()];
        let mut params = gpu_params_for_patterns(&patterns);
        params.path = "src";
        params.path_was_implicit = false;
        params.line_number = false; // -N / --no-line-number
        params.max_filesize = Some("10M".to_string());
        params.color = Some("always".to_string());
        params.no_ignore_vcs = true;
        params.replace = Some("R".to_string());
        params.only_matching = true;
        params.context = Some(3); // M1: -C 3 must survive the rg redirect too

        let rg_args = ripgrep_args_for_gpu_params(&params);

        assert_eq!(
            rg_args.patterns,
            vec!["needle".to_string(), "second".to_string()]
        );
        assert_eq!(rg_args.paths, vec!["src".to_string()]);
        assert!(
            !rg_args.line_number,
            "-N must suppress line numbers on the redirect path too"
        );
        assert!(rg_args.no_line_number);
        assert_eq!(rg_args.max_filesize.as_deref(), Some("10M"));
        assert_eq!(rg_args.color.as_deref(), Some("always"));
        assert!(rg_args.no_ignore_vcs);
        assert_eq!(rg_args.replace.as_deref(), Some("R"));
        assert!(rg_args.only_matching);
        // M1 (gate must-fix): the rg redirect must not silently drop -C/-A/-B context. Pre-fix
        // this field defaulted to None => zero context lines, exit 0 = silent-wrong-output.
        assert_eq!(
            rg_args.context,
            Some(3),
            "-C 3 must survive the GPU-fallback rg redirect (M1)"
        );
    }

    #[test]
    fn positional_gpu_path_defaults_line_number_off_aligned_with_native() {
        // N2 (task #131 F3): `tg PAT --gpu-device-ids N` with no -n/-N derives line_number via
        // `cli.line_number && !cli.no_line_number` at both GpuSearchParams construction sites in
        // `run_positional_cli`, replacing the old hardcoded `line_number: true`. Because
        // `PositionalCli::line_number` is `#[arg(short='n')]` (default false), that is a
        // user-visible flip to line-numbers-OFF -- a DELIBERATE alignment with the sibling
        // `native_search_config_for_positional` (the CPU fallback it delegates to), which derives
        // the identical expression. This test locks both the default-off value and the alignment.
        let cli = parse_positional_cli(&["tg", "PATTERN"]);
        let derived_line_number = cli.line_number && !cli.no_line_number;
        assert!(
            !derived_line_number,
            "positional GPU path must default line_number OFF with no -n/-N (N2)"
        );

        // The alignment target: native_search_config_for_positional derives the same value from
        // the same cli. If a future edit diverges the two paths, this assert fails.
        let native = native_search_config_for_positional(
            &cli,
            "PATTERN",
            &[".".to_string()],
            RoutingDecision::native_cpu_gpu_fallback(false, false),
        );
        assert_eq!(
            native.line_number, derived_line_number,
            "positional GPU line_number must stay aligned with native_search_config_for_positional"
        );

        // -n flips it back on (both paths honor the explicit request).
        let cli_n = parse_positional_cli(&["tg", "-n", "PATTERN"]);
        assert!(
            cli_n.line_number && !cli_n.no_line_number,
            "explicit -n must turn line numbers back on"
        );
    }

    #[test]
    fn gpu_sidecar_search_payload_carries_every_previously_dropped_flag() {
        // Primary RED test (per the build spec): `handle_gpu_sidecar_search`'s JSON payload used
        // to omit all 6 of these keys entirely, because `GpuSearchParams` had nowhere to read
        // them from.
        let patterns = vec!["needle".to_string()];
        let mut params = gpu_params_for_patterns(&patterns);
        params.replace = Some("REPLACED".to_string());
        params.only_matching = true;
        params.max_filesize = Some("10M".to_string());
        params.color = Some("always".to_string());
        params.no_ignore_vcs = true;
        params.line_number = false; // -N

        let payload = gpu_sidecar_search_payload(&params);

        assert_eq!(payload["replace"], serde_json::json!("REPLACED"));
        assert_eq!(payload["only_matching"], serde_json::json!(true));
        assert_eq!(payload["max_filesize"], serde_json::json!("10M"));
        assert_eq!(payload["color"], serde_json::json!("always"));
        assert_eq!(payload["no_ignore_vcs"], serde_json::json!(true));
        assert_eq!(payload["line_number"], serde_json::json!(false));
    }

    /// Task #131 F3 recurrence guard. An EXHAUSTIVE destructure (no `..`) of every `SearchArgs`
    /// field: the moment a new field is added to that struct, THIS FUNCTION STOPS COMPILING until
    /// a human adds it here and consciously classifies it into one of:
    ///   - HONORED: threaded into `GpuSearchParams` (directly, or folded into a derived field
    ///     like `context`/`line_number`) and never silently dropped on any GPU-routed request.
    ///   - NO-OP: restates a default / has no observable effect on this or any other backend
    ///     (cross-checked against `index_flag_violations`'s own `PassthroughSafe` bucket for the
    ///     same flags, e.g. `ignore`/`no_hidden`/`unicode`/`messages`).
    ///   - MOOT-OR-UNREACHABLE: some earlier, verified gate (an unconditional early return, or
    ///     `route_search`'s fixed precedence order) makes this field either impossible to reach
    ///     the GPU branch with, or unable to change which branch is taken once there.
    ///   - OUT_OF_SCOPE_GAP (a REAL, pre-existing gap this PR does NOT fix): confirmed by reading
    ///     `search_requires_ripgrep_passthrough`/`search_prefers_ripgrep_passthrough` -- their
    ///     whole "hard list" is gated behind `!args.json && !args.ndjson`, so combined with
    ///     `--json`/`--ndjson` (and, for a few of them, combined with nothing at all -- see
    ///     `format`) the request CAN reach `NativeCpu`/`NativeGpu` today, and `NativeSearchConfig`
    ///     has no field for it either. This is a general native-engine limitation, not specific to
    ///     `--gpu-device-ids`, discovered while building #131 F3 and explicitly OUT OF SCOPE for
    ///     it -- flagged here (not silently left "safe") so it isn't lost, pending its own task.
    ///     P5·H2 (2026-08-08) CLOSED the count/files trio of OUT_OF_SCOPE_GAPs below
    ///     (`count_matches`/`files_with_matches`/`files_without_match`): the native-route
    ///     refusals (`validate_search_native_structured_refusals` /
    ///     `validate_positional_native_structured_refusals`) now HARD-REFUSE them (exit 2) before
    ///     the GPU/CPU branches are reached, so on the GPU branch they are re-classified
    ///     MOOT-OR-UNREACHABLE. Their OUT_OF_SCOPE_GAP siblings here keep that bucket.
    #[cfg(test)]
    fn assert_search_args_gpu_field_classification_is_exhaustive(args: &SearchArgs) {
        let SearchArgs {
            ignore_case: _,      // HONORED
            fixed_strings: _,    // HONORED
            no_fixed_strings: _, // NO-OP (hardcoded false in every rg-passthrough builder too)
            invert_match: _,     // HONORED
            no_invert_match: _,  // NO-OP (ditto)
            count: _,            // HONORED
            count_matches: _,    // MOOT-OR-UNREACHABLE (P5·H2: the native-route gate refuses
            // this before the GPU branch is reached)
            line_number: _, // HONORED (`line_number && !no_line_number`, task #131 F3)
            no_line_number: _, // HONORED (see line_number)
            column: _,      // OUT_OF_SCOPE_GAP
            no_column: _,   // NO-OP (moot: `column` itself is never emitted by this engine)
            replace: _,     // HONORED (task #131 F3)
            format: _,      // OUT_OF_SCOPE_GAP (only `format=="rg"` is independently
            // verified unreachable here -- forces rg passthrough
            // unconditionally; any OTHER non-`rg` value's routing was NOT
            // independently traced through the Python front door in this
            // pass, so it is bucketed as a gap rather than asserted safe)
            sort: _,                // OUT_OF_SCOPE_GAP
            sort_reverse: _,        // OUT_OF_SCOPE_GAP
            sort_files: _,          // OUT_OF_SCOPE_GAP
            null: _,                // OUT_OF_SCOPE_GAP
            null_data: _,           // OUT_OF_SCOPE_GAP
            multiline: _,           // OUT_OF_SCOPE_GAP
            multiline_dotall: _,    // OUT_OF_SCOPE_GAP
            context: _,             // HONORED (via search_effective_context)
            after_context: _,       // HONORED (folds into context, see search_effective_context)
            before_context: _,      // HONORED (ditto)
            max_count: _,           // HONORED
            max_depth: _,           // HONORED
            word_regexp: _,         // HONORED
            smart_case: _,          // HONORED
            globs: _,               // HONORED
            no_ignore: _,           // HONORED
            ignore: _,              // NO-OP (restates the no_ignore default)
            no_ignore_dot: _,       // OUT_OF_SCOPE_GAP
            no_ignore_exclude: _,   // OUT_OF_SCOPE_GAP
            no_ignore_files: _,     // OUT_OF_SCOPE_GAP
            no_ignore_global: _,    // OUT_OF_SCOPE_GAP
            no_ignore_parent: _,    // OUT_OF_SCOPE_GAP
            hidden: _,              // HONORED
            no_hidden: _,           // NO-OP (restates the default)
            follow: _,              // OUT_OF_SCOPE_GAP
            text: _,                // HONORED
            files_with_matches: _,  // MOOT-OR-UNREACHABLE (P5·H2: refused before this branch)
            files_without_match: _, // MOOT-OR-UNREACHABLE (P5·H2: refused before this branch)
            file_type: _,           // OUT_OF_SCOPE_GAP
            index: _,               // MOOT-OR-UNREACHABLE (route_search checks explicit_index
            // BEFORE explicit_gpu_device_ids -- TrigramIndex always wins)
            force_cpu: _, // MOOT-OR-UNREACHABLE (route_search checks
            // explicit_gpu_device_ids BEFORE force_cpu -- an explicit
            // --gpu-device-ids always wins the branch regardless of this
            // field's value, so there is nothing for it to affect here)
            gpu_device_ids: _, // HONORED (the field that selects this whole path)
            color: _,          // HONORED (task #131 F3)
            path_separator: _, // OUT_OF_SCOPE_GAP
            only_matching: _,  // HONORED (task #131 F3)
            vimgrep: _,        // OUT_OF_SCOPE_GAP
            passthru: _,       // OUT_OF_SCOPE_GAP
            json: _,           // HONORED
            ndjson: _,         // HONORED
            verbose: _,        // HONORED
            regexp: _,         // HONORED (folds into `request.patterns` -> `params.patterns`)
            pattern: _,        // HONORED (ditto)
            path: _,           // HONORED (-> params.path / params.path_was_implicit)
            pcre2: _,          // MOOT-OR-UNREACHABLE (rg available -> routes to
            // ripgrep_pcre2() before the gpu check in route_search; rg
            // unavailable -> require_ripgrep_or_exit hard-exits before
            // route_search is even called)
            auto_hybrid_regex: _, // OUT_OF_SCOPE_GAP
            unicode: _,           // NO-OP (restates the Unicode-mode default)
            pcre2_unicode: _,     // NO-OP (alias of unicode; same reasoning)
            max_filesize: _,      // HONORED (task #131 F3)
            no_ignore_vcs: _,     // HONORED (task #131 F3)
            require_git: _,       // OUT_OF_SCOPE_GAP
            messages: _,          // NO-OP (restates the default; no diagnostic-message mode here)
            no_config: _,         // NO-OP (this backend never reads an rg config file either)
            pcre2_version: _,     // MOOT-OR-UNREACHABLE (early return at the very top of
            // handle_ripgrep_search, before any routing)
            type_list: _, // MOOT-OR-UNREACHABLE (ditto)
            version: _,   // MOOT-OR-UNREACHABLE (ditto)
        } = args;
    }

    #[test]
    fn search_args_gpu_field_classification_covers_a_real_parsed_instance() {
        // Exercising the exhaustive destructure against a real clap-parsed value keeps this test
        // from being vacuous; the actual recurrence-guard value is the destructure compiling at
        // all (see the function's doc comment).
        let args = parse_search_args(&["tg", "search", "PATTERN"]);
        assert_search_args_gpu_field_classification_is_exhaustive(&args);
    }

    /// Sibling guard for `PositionalCli` (the `tg PATTERN` front door). Every one of its fields is
    /// name-identical to a `SearchArgs` field above (verified by inspection when this was written)
    /// with the same meaning, so the same classification applies; `run_positional_cli` has no
    /// `search_requires_ripgrep_passthrough`-equivalent gate at all, so its OUT_OF_SCOPE_GAP
    /// fields are reachable via `--gpu-device-ids` unconditionally (not only combined with
    /// `--json`/`--ndjson` as for `SearchArgs`) -- a strictly broader exposure of the same
    /// pre-existing, out-of-scope gap. P5·H2 (2026-08-08) closed the `count_matches` member of
    /// that gap: `validate_positional_native_structured_refusals` now HARD-REFUSES it (exit 2)
    /// at the top of both native positional arms (the structured doors and, unconditionally, the
    /// `--gpu-device-ids` door), so on the GPU branch it is MOOT-OR-UNREACHABLE.
    #[cfg(test)]
    fn assert_positional_cli_gpu_field_classification_is_exhaustive(cli: &PositionalCli) {
        let PositionalCli {
            pattern: _,           // HONORED
            path: _,              // HONORED
            count: _,             // HONORED
            count_matches: _,     // MOOT-OR-UNREACHABLE (P5·H2: refused before this branch)
            line_number: _,       // HONORED (task #131 F3)
            no_line_number: _,    // HONORED (task #131 F3)
            column: _,            // OUT_OF_SCOPE_GAP
            max_count: _,         // HONORED
            fixed_strings: _,     // HONORED
            invert_match: _,      // HONORED
            ignore_case: _,       // HONORED
            word_regexp: _,       // HONORED
            replace: _,           // HONORED (task #131 F3)
            force_cpu: _,         // MOOT-OR-UNREACHABLE (see the SearchArgs bucket above)
            gpu_device_ids: _,    // HONORED
            color: _,             // HONORED (task #131 F3)
            path_separator: _,    // OUT_OF_SCOPE_GAP
            only_matching: _,     // HONORED (task #131 F3)
            vimgrep: _,           // OUT_OF_SCOPE_GAP
            json: _,              // HONORED
            ndjson: _,            // HONORED
            verbose: _,           // HONORED
            pcre2: _,             // MOOT-OR-UNREACHABLE (see the SearchArgs bucket above)
            auto_hybrid_regex: _, // OUT_OF_SCOPE_GAP
            unicode: _,           // NO-OP
            pcre2_unicode: _,     // NO-OP
            max_filesize: _,      // HONORED (task #131 F3)
            no_ignore: _,         // HONORED
            ignore: _,            // NO-OP
            messages: _,          // NO-OP
            require_git: _,       // OUT_OF_SCOPE_GAP
            no_hidden: _,         // NO-OP
            no_ignore_vcs: _,     // HONORED (task #131 F3)
        } = cli;
    }

    #[test]
    fn positional_cli_gpu_field_classification_covers_a_real_parsed_instance() {
        let cli = parse_positional_cli(&["tg", "PATTERN"]);
        assert_positional_cli_gpu_field_classification_is_exhaustive(&cli);
    }

    /// P5·H2 coverage ratchet (modeled on `assert_search_args_gpu_field_classification_is_exhaustive`
    /// above). An EXHAUSTIVE destructure (no `..`) of every `SearchArgs` field: adding a field to
    /// that struct stops THIS function from compiling until a human classifies it for the NATIVE
    /// STRUCTURED route (`--json`/`--ndjson` -> `BackendSelection::NativeCpu`/`NativeGpu`, minus
    /// the `--format rg --json` passthrough that `search_requires_ripgrep_passthrough` honors).
    /// Disposition bucket per field, for that route specifically:
    ///   - HONORED: threaded into `NativeSearchConfig` / the structured emitter (or, for
    ///     `format`/`pcre2`-class fields, routed to an rg passthrough that carries them).
    ///   - HARD-REFUSED: THIS PR's class -- the native engine silently dropped it pre-fix
    ///     (`native_structed_dropped_search_flags` now lists it and the native arms exit 2).
    ///   - NO-OP: restates a default the native engine already behaves as on this route.
    ///   - MOOT-OR-UNREACHABLE: an earlier, verified gate (early return or routing precedence)
    ///     makes it impossible to reach a native structured branch, or impossible to matter once
    ///     there.
    ///   - IRRELEVANT-TO-THIS-CLASS: not a native-engine drop; its disposition is the GPU/plain-
    ///     text ratchets' business (cross-referenced, this function does not re-litigate it).
    #[cfg(test)]
    fn assert_search_args_native_structured_field_classification_is_exhaustive(args: &SearchArgs) {
        let SearchArgs {
            ignore_case: _,      // HONORED (NativeSearchConfig)
            fixed_strings: _,    // HONORED
            no_fixed_strings: _, // NO-OP (hardcoded false in every rg-passthrough builder too)
            invert_match: _,     // HONORED
            no_invert_match: _,  // NO-OP (ditto)
            count: _,            // HONORED (line-granular contract, credentialed for -c)
            count_matches: _,    // HARD-REFUSED (P5·H2 -- THIS PR)
            line_number: _,      // HONORED
            no_line_number: _,   // HONORED (see line_number)
            column: _,           // IRRELEVANT-TO-THIS-CLASS (neither a silent native drop fix nor
            // an honored native field; see the GPU ratchet's OUT_OF_SCOPE_GAP)
            no_column: _, // NO-OP (moot: `column` is never emitted by this engine)
            replace: _,   // HONORED (task #131 F3)
            format: _,    // ROUTE SELECTOR: `"rg"` => honored rg passthrough (carries the
            // refused flags through `command_ripgrep_args`, so it is excluded from the refusal
            // predicate); any other value reaches native structured only via the json gate and is
            // not a silent drop of THIS class (no native field for it to drop)
            sort: _,                // IRRELEVANT-TO-THIS-CLASS (GPU ratchet OUT_OF_SCOPE_GAP)
            sort_reverse: _,        // IRRELEVANT-TO-THIS-CLASS
            sort_files: _,          // IRRELEVANT-TO-THIS-CLASS
            null: _,                // IRRELEVANT-TO-THIS-CLASS
            null_data: _,           // IRRELEVANT-TO-THIS-CLASS
            multiline: _,           // IRRELEVANT-TO-THIS-CLASS
            multiline_dotall: _,    // IRRELEVANT-TO-THIS-CLASS
            context: _,             // HONORED
            after_context: _,       // HONORED
            before_context: _,      // HONORED
            max_count: _,           // HONORED
            max_depth: _,           // HONORED
            word_regexp: _,         // HONORED
            smart_case: _,          // HONORED
            globs: _,               // HONORED
            no_ignore: _,           // HONORED
            ignore: _,              // NO-OP (restates the no_ignore default)
            no_ignore_dot: _,       // IRRELEVANT-TO-THIS-CLASS
            no_ignore_exclude: _,   // IRRELEVANT-TO-THIS-CLASS
            no_ignore_files: _,     // IRRELEVANT-TO-THIS-CLASS
            no_ignore_global: _,    // IRRELEVANT-TO-THIS-CLASS
            no_ignore_parent: _,    // IRRELEVANT-TO-THIS-CLASS
            hidden: _,              // HONORED
            no_hidden: _,           // NO-OP
            follow: _,              // IRRELEVANT-TO-THIS-CLASS
            text: _,                // HONORED
            files_with_matches: _,  // HARD-REFUSED (P5·H2 -- THIS PR)
            files_without_match: _, // HARD-REFUSED (P5·H2 -- THIS PR)
            file_type: _,           // IRRELEVANT-TO-THIS-CLASS
            index: _,               // MOOT-OR-UNREACHABLE (route_search checks explicit_index
            // BEFORE json/ndjson -> the index path, which itself Refuses count/files flags
            // (IndexFlagPolicy::Refuse) with its own message; a structured index search never
            // reaches these native arms)
            force_cpu: _, // MOOT-OR-UNREACHABLE (structured output + force_cpu routes to
            // native_cpu_force, whose arms below refuse identically; a structured request can
            // never be served exclusively by rg)
            gpu_device_ids: _, // HONORED (the field that selects the whole path; refusal predicate
            // fires on the positional twin for it, see below)
            color: _,          // HONORED (task #131 F3)
            path_separator: _, // IRRELEVANT-TO-THIS-CLASS
            only_matching: _,  // HONORED (task #131 F3 -- do NOT extend the refusal to `-o`)
            vimgrep: _,        // IRRELEVANT-TO-THIS-CLASS
            passthru: _,       // IRRELEVANT-TO-THIS-CLASS
            json: _,           // ROUTE SELECTOR (native structured trigger; honored as the
            // emitter mode -- the refusal predicate makes the *combination* json + refused flag
            // exit 2, never both silently)
            ndjson: _,  // ROUTE SELECTOR (ditto)
            verbose: _, // HONORED
            regexp: _,  // HONORED
            pattern: _, // HONORED
            path: _,    // HONORED
            pcre2: _,   // MOOT-OR-UNREACHABLE (rg available -> routed to ripgrep_pcre2
            // before the native branches; rg unavailable -> require_ripgrep_or_exit hard-exits)
            auto_hybrid_regex: _, // IRRELEVANT-TO-THIS-CLASS
            unicode: _,           // NO-OP (restates the Unicode default)
            pcre2_unicode: _,     // NO-OP (alias of unicode)
            max_filesize: _,      // HONORED (task #131 F3)
            no_ignore_vcs: _,     // HONORED (task #131 F3)
            require_git: _,       // IRRELEVANT-TO-THIS-CLASS
            messages: _,          // NO-OP (this engine emits no diagnostic-message mode)
            no_config: _,         // NO-OP (this backend never reads an rg config file either)
            pcre2_version: _,     // MOOT-OR-UNREACHABLE (early return at the top of
            // handle_ripgrep_search)
            type_list: _, // MOOT-OR-UNREACHABLE (ditto)
            version: _,   // MOOT-OR-UNREACHABLE (ditto)
        } = args;
    }

    #[test]
    fn search_args_native_structured_field_classification_covers_a_real_parsed_instance() {
        // Exercising against real clap-parsed instances keeps the ratchet from being vacuous; the
        // recurrence guard is the destructure compiling at all.
        let args = parse_search_args(&["tg", "search", "PATTERN"]);
        assert_search_args_native_structured_field_classification_is_exhaustive(&args);
        let args = parse_search_args(&["tg", "search", "--json", "--count-matches", "PATTERN"]);
        assert_search_args_native_structured_field_classification_is_exhaustive(&args);
    }

    #[test]
    fn native_structured_route_hard_refuses_count_and_files_flags() {
        // P5·H2 behavioral arm (the refusal predicate): pre-fix, `search_requires_ripgrep_passthrough`'s
        // hard-flag list is gated behind !json&&!ndjson, so on `--json`/`--ndjson` these flags fell
        // through to the native engine and were SILENTLY DROPPED (No `NativeSearchConfig` field;
        // verified live on the shipped native front door: exit 0 with a plain match list). Each
        // combinator must now resolve to the EXACT refusal set, not an empty/green ok.
        for tokens in [
            ["tg", "search", "--json", "--count-matches", "needle", "."].as_slice(),
            ["tg", "search", "--count-matches", "--json", "needle", "."].as_slice(),
            ["tg", "search", "--ndjson", "--count-matches", "needle", "."].as_slice(),
        ] {
            let args = parse_search_args(tokens);
            assert_eq!(
                native_structured_dropped_search_flags(&args),
                vec!["--count-matches"],
                "structured native route must list --count-matches as refused: {tokens:?}"
            );
        }
        let args = parse_search_args(&[
            "tg",
            "search",
            "--json",
            "--files-with-matches",
            "--files-without-match",
            "needle",
            ".",
        ]);
        assert_eq!(
            native_structured_dropped_search_flags(&args),
            vec!["--files-with-matches", "--files-without-match"],
            "both files flags must be listed, in argv order"
        );
        let args = parse_search_args(&["tg", "search", "--json", "needle", "."]);
        assert!(
            native_structured_dropped_search_flags(&args).is_empty(),
            "a plain --json search must NOT be refused"
        );
    }

    #[test]
    fn format_rg_json_passthrough_stays_honored_not_refused() {
        // `--format rg --json` is an rg PASSTHROUGH (search_requires_ripgrep_passthrough returns
        // true), and `command_ripgrep_args` carries count/files flags into rg's own argv. The
        // refusal predicate must NOT fire there, or it would break a currently-honored route.
        let args = parse_search_args(&[
            "tg",
            "search",
            "--format",
            "rg",
            "--json",
            "--count-matches",
            "needle",
            ".",
        ]);
        assert!(
            search_requires_ripgrep_passthrough(&args),
            "--format rg --json must stay an rg passthrough (not native)"
        );
        assert!(
            native_structured_dropped_search_flags(&args).is_empty(),
            "--format rg --json + --count-matches is honored via rg passthrough -- never refused"
        );
    }

    #[test]
    fn non_json_count_matches_is_not_refused_by_the_structured_gate() {
        // The ALREADY-FINE path (locked by bootstrap test_rust_first_count_matches_refuses_via_native_self_guard):
        // without --json/--ndjson, `search_requires_ripgrep_passthrough`'s non-json hard list makes
        // count/files flags route to rg (honored) or self-refuse when rg is missing. The new refusal
        // predicate must keep its hands off, or it would double-refuse a route a later gate already
        // owns correctly.
        let args = parse_search_args(&["tg", "search", "--count-matches", "needle", "."]);
        assert!(
            search_requires_ripgrep_passthrough(&args),
            "non-json --count-matches still requires rg passthrough"
        );
        assert!(native_structured_dropped_search_flags(&args).is_empty());
    }

    #[test]
    fn positional_gpu_and_structured_doors_hard_refuse_count_matches() {
        // P5·H2 positional twin: `run_positional_cli` has no json/ndjson gate, so `--count-matches`
        // reaches the native engine via the structured doors AND, unconditionally, the explicit
        // `--gpu-device-ids` door (`native_search_config_for_positional` maps `count`, not
        // `count_matches`). The predicate must name it there.
        let cli = parse_positional_cli(&[
            "tg",
            "needle",
            ".",
            "--gpu-device-ids",
            "0",
            "--count-matches",
        ]);
        assert_eq!(
            positional_native_dropped_search_flags(&cli),
            vec!["--count-matches"],
            "positional --gpu-device-ids + --count-matches is a native silent drop -> refuse"
        );
        let cli = parse_positional_cli(&["tg", "needle", ".", "--json", "--count-matches"]);
        assert_eq!(
            positional_native_dropped_search_flags(&cli),
            vec!["--count-matches"],
            "positional --json + --count-matches is a native silent drop -> refuse"
        );
        let cli = parse_positional_cli(&["tg", "needle", ".", "--count-matches"]);
        assert!(
            positional_native_dropped_search_flags(&cli).is_empty(),
            "bare positional --count-matches routes to the Ripgrep arm (front-door-shielded); \
             not this gate's class"
        );
        let cli = parse_positional_cli(&["tg", "needle", ".", "--gpu-device-ids", "0"]);
        assert!(
            positional_native_dropped_search_flags(&cli).is_empty(),
            "no flag, no refuse"
        );
    }

    #[test]
    fn only_matching_and_count_stay_honored_not_refused() {
        // Scoped-out must stay honored: `-o`/`--only-matching` is carried by NativeSearchConfig
        // (task #131 F3) and `-c`/`--count` has the line-count contract the engine provides. They
        // must never join the refusal set.
        let args = parse_search_args(&["tg", "search", "--json", "-o", "-c", "needle", "."]);
        assert!(native_structured_dropped_search_flags(&args).is_empty());
    }

    #[test]
    fn native_structured_refusal_validator_returns_refusal_set() {
        // P5·H2 audit Finding 3: the PREDICATE tests above inspect `native_structured_dropped_search_flags`
        // directly, so deleting every validator CALL SITE would leave them green. These assert the
        // refusers themselves -- the functions the (source-wired) call sites run -- return the
        // exact refusal set for each newly-refused combo, and None for every honored one. The
        // validators are PURE (they return the set instead of exiting), so this runs in-process
        // without `exit(2)` killing the test binary; the exit is applied only at the call sites,
        // covered end-to-end by `rust_core/tests/test_h2_native_structured_refusal.rs` against
        // the real built `tg` binary (CARGO_BIN_EXE_tg) plus this in-process grid.
        let cases: &[(&[&str], Option<Vec<&'static str>>)] = &[
            // search-command structured doors
            (
                &["tg", "search", "--json", "--count-matches", "needle", "."],
                Some(vec!["--count-matches"]),
            ),
            (
                &["tg", "search", "--ndjson", "--count-matches", "needle", "."],
                Some(vec!["--count-matches"]),
            ),
            (
                &[
                    "tg",
                    "search",
                    "--json",
                    "--files-with-matches",
                    "--files-without-match",
                    "needle",
                    ".",
                ],
                Some(vec!["--files-with-matches", "--files-without-match"]),
            ),
            // honored search-command routes
            (&["tg", "search", "--json", "needle", "."], None),
            (&["tg", "search", "--count-matches", "needle", "."], None),
            // positional doors
            (
                &[
                    "tg",
                    "needle",
                    ".",
                    "--gpu-device-ids",
                    "0",
                    "--count-matches",
                ],
                Some(vec!["--count-matches"]),
            ),
            (
                &["tg", "needle", ".", "--json", "--count-matches"],
                Some(vec!["--count-matches"]),
            ),
            // positional honored routes
            (&["tg", "needle", ".", "--count-matches"], None),
            (&["tg", "needle", ".", "--gpu-device-ids", "0"], None),
        ];
        for (tokens, expected) in cases {
            if tokens[1..].contains(&"search") {
                let args = parse_search_args(tokens);
                assert_eq!(
                    validate_search_native_structured_refusals(&args).as_ref(),
                    expected.as_ref(),
                    "search validator mismatch for {tokens:?}"
                );
            } else {
                let cli = parse_positional_cli(tokens);
                assert_eq!(
                    validate_positional_native_structured_refusals(&cli).as_ref(),
                    expected.as_ref(),
                    "positional validator mismatch for {tokens:?}"
                );
            }
        }
    }

    #[test]
    fn rg_passthrough_gpu_dropped_search_flags_returns_count_files_only_with_gpu() {
        // P5·H2 extension predicate (audit/h2 follow-up): the rg-passthrough route honors the
        // count/files flags but has no `--gpu-device-ids` field, so the SEARCH-form refusal set is
        // non-empty ONLY when explicit GPU ids are present AND a count/files flag rides the
        // request. Pure `--count-matches`/`-l` (no gpu), gpu alone, and the native-mapped
        // `--count` stay EMPTY -- they keep their honored routes. This is the predicate the
        // source-wired call site in `handle_ripgrep_search` runs; the exit wiring is covered
        // end-to-end by `rust_core/tests/test_h2_native_structured_refusal.rs`.
        let cases: &[(&[&str], Vec<&'static str>)] = &[
            // refused: gpu + each count/files spelling, and a combined pair
            (
                &[
                    "tg",
                    "search",
                    "--gpu-device-ids",
                    "0",
                    "--count-matches",
                    "needle",
                    ".",
                ],
                vec!["--count-matches"],
            ),
            (
                &["tg", "search", "--gpu-device-ids", "0", "-l", "needle", "."],
                vec!["--files-with-matches"],
            ),
            (
                &[
                    "tg",
                    "search",
                    "--gpu-device-ids",
                    "0",
                    "--files-with-matches",
                    "needle",
                    ".",
                ],
                vec!["--files-with-matches"],
            ),
            (
                &[
                    "tg",
                    "search",
                    "--gpu-device-ids",
                    "0",
                    "--files-without-match",
                    "needle",
                    ".",
                ],
                vec!["--files-without-match"],
            ),
            (
                &[
                    "tg",
                    "search",
                    "--gpu-device-ids",
                    "0",
                    "--count-matches",
                    "--files-without-match",
                    "needle",
                    ".",
                ],
                vec!["--count-matches", "--files-without-match"],
            ),
            // honored: pure count/files without gpu, gpu without a count/files flag, and --count
            (
                &["tg", "search", "--count-matches", "needle", "."],
                Vec::new(),
            ),
            (&["tg", "search", "-l", "needle", "."], Vec::new()),
            (
                &["tg", "search", "--files-without-match", "needle", "."],
                Vec::new(),
            ),
            (
                &["tg", "search", "--gpu-device-ids", "0", "needle", "."],
                Vec::new(),
            ),
            (
                &[
                    "tg",
                    "search",
                    "--gpu-device-ids",
                    "0",
                    "--count",
                    "needle",
                    ".",
                ],
                Vec::new(),
            ),
        ];
        for (tokens, expected) in cases {
            let args = parse_search_args(tokens);
            assert_eq!(
                &rg_passthrough_gpu_dropped_search_flags(&args),
                expected,
                "predicate mismatch for {tokens:?}"
            );
        }
    }

    #[test]
    fn validation_command_argv_keeps_malicious_path_in_one_token_no_shell_injection() {
        // A maliciously named file with shell metacharacters must land in a SINGLE argv element so a
        // direct spawn cannot interpret it as a pipeline/command-substitution (SECURITY regression).
        let argv = validation_command_argv(
            r#"python -m py_compile "$file""#,
            Some("/repo/evil; rm -rf ~/`whoami`.py"),
        );
        assert_eq!(
            argv,
            vec![
                "python".to_string(),
                "-m".to_string(),
                "py_compile".to_string(),
                "/repo/evil; rm -rf ~/`whoami`.py".to_string(),
            ]
        );
    }

    #[test]
    fn validation_command_argv_preserves_quoted_path_with_spaces() {
        let argv =
            validation_command_argv(r#"python -m py_compile "C:\path with spaces\app.py""#, None);
        assert_eq!(
            argv,
            vec![
                "python".to_string(),
                "-m".to_string(),
                "py_compile".to_string(),
                r#"C:\path with spaces\app.py"#.to_string(),
            ]
        );
    }

    #[test]
    fn validation_command_argv_substitutes_brace_file_placeholder_safely() {
        // The {file} placeholder variant must also keep a malicious path in a single argv element.
        let argv = validation_command_argv(
            "ruff check {file}",
            Some("/repo/evil; rm -rf ~/`whoami`.py"),
        );
        assert_eq!(
            argv,
            vec![
                "ruff".to_string(),
                "check".to_string(),
                "/repo/evil; rm -rf ~/`whoami`.py".to_string(),
            ]
        );
    }

    #[test]
    fn split_validation_command_argv_rejects_unterminated_quote() {
        assert!(split_validation_command_argv("python \"foo").is_empty());
        assert!(split_validation_command_argv("python 'foo").is_empty());
    }

    #[test]
    fn run_validation_command_rejects_placeholder_in_program_position() {
        // A template whose only token is the placeholder would run the (attacker-named) file itself.
        let result = run_validation_command(
            "lint",
            "$file",
            Some("/repo/evil; rm -rf ~.py"),
            "$file",
            std::path::Path::new("."),
            DEFAULT_VALIDATION_TIMEOUT_MS,
        );
        assert!(!result.success);
        assert!(result.stderr.contains("must name a program"));
    }

    #[test]
    fn run_validation_command_rejects_unbalanced_quote_template() {
        let result = run_validation_command(
            "lint",
            "python \"foo",
            None,
            "python \"foo",
            std::path::Path::new("."),
            DEFAULT_VALIDATION_TIMEOUT_MS,
        );
        assert!(!result.success);
        assert!(result.stderr.contains("empty or has unbalanced quotes"));
    }

    // -- audit #10 (validation subprocess timeout) + #34 (validation fan-out cap) -------------

    /// Builds a validation-command TEMPLATE string from a program + argv, quoting any argument
    /// that contains whitespace so `split_validation_command_argv` round-trips it back into a
    /// single token (mirrors how a real `--test-cmd`/`--lint-cmd` value is authored).
    fn command_template(program: &str, args: &[String]) -> String {
        let mut parts = vec![program.to_string()];
        for arg in args {
            if arg.chars().any(char::is_whitespace) {
                assert!(
                    !arg.contains('"'),
                    "test helper does not support embedded double quotes"
                );
                parts.push(format!("\"{arg}\""));
            } else {
                parts.push(arg.clone());
            }
        }
        parts.join(" ")
    }

    /// Cross-platform "block forever" command as a SINGLE process (no shell/grandchild
    /// indirection), so a kill-on-timeout assertion only has to reason about one PID.
    fn platform_hang_forever_command() -> (&'static str, Vec<String>) {
        if cfg!(windows) {
            (
                "powershell",
                vec![
                    "-NoProfile".to_string(),
                    "-NonInteractive".to_string(),
                    "-Command".to_string(),
                    "Start-Sleep -Seconds 300".to_string(),
                ],
            )
        } else {
            ("sleep", vec!["300".to_string()])
        }
    }

    /// Cross-platform "exit 0 immediately" command.
    fn platform_fast_success_command() -> (&'static str, Vec<String>) {
        if cfg!(windows) {
            (
                "cmd",
                vec!["/C".to_string(), "exit".to_string(), "0".to_string()],
            )
        } else {
            ("true", Vec::new())
        }
    }

    /// Cross-platform "write ~2.5MB to stdout, fast, without reading anything" command: large
    /// enough to exceed a typical OS pipe buffer (commonly 4-64KB), so a successful capture here
    /// proves the wait path drains output concurrently instead of deadlocking against a full pipe.
    /// `reps` writes of 64KiB. `reps = 0` is the spawn-only BASELINE: same
    /// interpreter, same argv shape, no output -- so timing it isolates interpreter
    /// startup from drain cost.
    ///
    /// One parameterised producer rather than two literals, deliberately. A separate
    /// short baseline literal contained no spaces, so rust's windows argv quoting
    /// passed it unquoted and PowerShell stripped the inner single quotes
    /// (`Write('A')` arrived as `Write(A)`, a ParserError). Sharing the shape makes
    /// that unrepresentable instead of correctly quoted in two places.
    fn platform_stdout_command(reps: usize) -> (&'static str, Vec<String>) {
        if cfg!(windows) {
            (
                "powershell",
                vec![
                    "-NoProfile".to_string(),
                    "-NonInteractive".to_string(),
                    "-Command".to_string(),
                    format!(
                        "$s='A'*65536;for($i=0;$i -lt {reps};$i++){{[Console]::Out.Write($s)}}"
                    ),
                ],
            )
        } else {
            (
                "dd",
                vec![
                    "if=/dev/zero".to_string(),
                    "bs=65536".to_string(),
                    format!("count={reps}"),
                ],
            )
        }
    }

    #[test]
    fn run_validation_command_kills_a_hanging_process_within_the_timeout() {
        let (program, args) = platform_hang_forever_command();
        let template = command_template(program, &args);
        let timeout_ms = 300;

        let started = Instant::now();
        let result = run_validation_command(
            "test",
            &template,
            None,
            &template,
            std::path::Path::new("."),
            timeout_ms,
        );
        let elapsed = started.elapsed();

        assert!(!result.success, "a hung command must not report success");
        assert_eq!(result.exit_code, None, "a killed process has no exit code");
        assert!(
            result.stderr.contains("exceeded") && result.stderr.contains("timeout"),
            "expected a timeout message, got: {}",
            result.stderr
        );
        // Bounded, not the full 300s the command asked to sleep for: proves the child was
        // actually terminated at the timeout rather than the call blocking until natural exit
        // (the exact #400 hang class, applied to the validation subprocess path).
        assert!(
            elapsed < Duration::from_secs(10),
            "expected the timeout to bound the wait; took {elapsed:?}"
        );
    }

    #[test]
    fn run_validation_command_fast_command_still_succeeds_within_timeout() {
        let (program, args) = platform_fast_success_command();
        let template = command_template(program, &args);

        let result = run_validation_command(
            "lint",
            &template,
            None,
            &template,
            std::path::Path::new("."),
            DEFAULT_VALIDATION_TIMEOUT_MS,
        );

        assert!(result.success, "expected success, got: {result:?}");
        assert_eq!(result.exit_code, Some(0));
    }

    #[test]
    fn run_validation_command_captures_large_stdout_without_deadlock() {
        let (program, args) = platform_stdout_command(40);
        let template = command_template(program, &args);
        // Generous but bounded: if the pipe-fill deadlock footgun (rust-lang#45572) were
        // reintroduced (e.g. a hand-rolled spawn + wait_timeout + wait_with_output instead of
        // process_control's drain-while-timing-out wait), the child would block writing to a
        // full, undrained pipe and this call would hit the timeout and report failure instead of
        // completing quickly -- this is a regression guard, not just a happy-path check.
        let timeout_ms = 60_000;
        // The bound below is PAIRED, not absolute, and it is not what catches a deadlock --
        // a deadlock exhausts the limit, `terminate_for_timeout` kills the child, and the
        // FIRST assertion fires on `result.success`. What it catches is the weaker "drains,
        // but pathologically slowly" regression (a poll loop reading a tiny buffer at a low
        // duty cycle), which is why it is kept rather than deleted.
        //
        // It is paired because the absolute form measured the GENERATOR: windows spawns
        // PowerShell (startup dominates and swings with runner load), unix spawns `dd`
        // (~2ms). Every false red here has therefore been windows -- twice under #303 at
        // 15s/10s, then again at 35.1s after that was widened to 60s/30s. Subtracting a
        // spawn-only run of the SAME interpreter cancels startup and load; a real drain
        // regression still blows the allowance, because it inflates the large arm only.
        let drain_allowance = Duration::from_millis(timeout_ms / 4);

        let (baseline_program, baseline_args) = platform_stdout_command(0);
        let baseline_template = command_template(baseline_program, &baseline_args);
        let baseline_started = Instant::now();
        let baseline_result = run_validation_command(
            "test",
            &baseline_template,
            None,
            &baseline_template,
            std::path::Path::new("."),
            timeout_ms,
        );
        let baseline = baseline_started.elapsed();
        assert!(
            baseline_result.success,
            "the baseline spawn must succeed or it cannot be subtracted from anything; \
             a failed baseline means the interpreter is unavailable, not that drain is fast. \
             got: {baseline_result:?}"
        );

        let started = Instant::now();
        let result = run_validation_command(
            "test",
            &template,
            None,
            &template,
            std::path::Path::new("."),
            timeout_ms,
        );
        let elapsed = started.elapsed();

        assert!(
            result.success,
            "expected the large-output command to finish successfully, got: {result:?}"
        );
        assert!(
            result.stdout.len() > 1_000_000,
            "expected >1MB of captured stdout (pipe-buffer-exceeding), got {} bytes",
            result.stdout.len()
        );
        let drain_cost = elapsed.saturating_sub(baseline);
        assert!(
            drain_cost < drain_allowance,
            "drain is pathologically slow, though not deadlocked -- a deadlock would have failed \
             the success assertion above. Draining 2.6MB cost {drain_cost:?} after subtracting a \
             {baseline:?} baseline spawn of the same interpreter (total {elapsed:?}), against an \
             allowance of {drain_allowance:?}. Because the baseline is subtracted, a merely slow \
             or loaded runner does NOT reach this assertion; a real drain regression does."
        );
    }

    #[test]
    fn resolve_validation_timeout_ms_prefers_flag_over_env_over_default() {
        assert_eq!(
            resolve_validation_timeout_ms(Some(5_000), Some("9000".to_string())),
            5_000,
            "an explicit --validation-timeout-ms flag must win over the env var"
        );
        assert_eq!(
            resolve_validation_timeout_ms(None, Some("9000".to_string())),
            9_000,
            "TG_VALIDATION_TIMEOUT_MS must be honored when no flag is set"
        );
        assert_eq!(
            resolve_validation_timeout_ms(None, None),
            DEFAULT_VALIDATION_TIMEOUT_MS
        );
        assert_eq!(
            resolve_validation_timeout_ms(None, Some("not-a-number".to_string())),
            DEFAULT_VALIDATION_TIMEOUT_MS,
            "a malformed env value must fall back to the default, not panic or become 0"
        );
    }

    #[test]
    fn cap_validation_targets_truncates_and_reports_totals() {
        let targets: Vec<String> = (0..100).map(|i| format!("file_{i}.py")).collect();

        let (capped, truncated, total) = cap_validation_targets(targets.clone(), 50);
        assert_eq!(capped.len(), 50);
        assert!(truncated);
        assert_eq!(total, 100);
        assert_eq!(capped, targets[..50]);

        let (not_capped, truncated, total) = cap_validation_targets(targets.clone(), 200);
        assert_eq!(not_capped.len(), 100);
        assert!(!truncated);
        assert_eq!(total, 100);

        let (unlimited, truncated, total) = cap_validation_targets(targets, 0);
        assert_eq!(unlimited.len(), 100, "0 must disable the cap");
        assert!(!truncated);
        assert_eq!(total, 100);
    }

    #[test]
    fn run_post_apply_validation_caps_targets_and_reports_truncation() {
        let edits: Vec<tensor_grep_rs::backend_ast::RewriteEdit> = (0..100)
            .map(|i| tensor_grep_rs::backend_ast::RewriteEdit {
                id: format!("edit-{i}"),
                file: PathBuf::from(format!("validation_target_{i}.py")),
                planned_mtime_ns: 0,
                line: 1,
                byte_range: 0..0,
                original_text: String::new(),
                replacement_text: String::new(),
                metavar_env: HashMap::new(),
            })
            .collect();

        let (program, mut args) = platform_fast_success_command();
        args.push("{file}".to_string());
        let template = command_template(program, &args);

        let cli_args = parse_run_args(&[
            "tg",
            "run",
            "--test-cmd",
            &template,
            "--max-validation-targets",
            "50",
            ".",
        ]);

        let summary = run_post_apply_validation(&cli_args, ".", &edits)
            .expect("expected a validation summary when --test-cmd is set");

        assert_eq!(
            summary.commands.len(),
            50,
            "expected exactly 50 spawns, one per capped target"
        );
        assert!(summary.validation_targets_truncated);
        assert_eq!(summary.validation_targets_total, 100);
        assert!(
            summary.success,
            "all 50 spawned no-op commands should succeed: {summary:?}"
        );
    }

    #[test]
    fn run_post_apply_validation_does_not_truncate_when_under_the_cap() {
        let edits: Vec<tensor_grep_rs::backend_ast::RewriteEdit> = (0..5)
            .map(|i| tensor_grep_rs::backend_ast::RewriteEdit {
                id: format!("edit-{i}"),
                file: PathBuf::from(format!("validation_target_{i}.py")),
                planned_mtime_ns: 0,
                line: 1,
                byte_range: 0..0,
                original_text: String::new(),
                replacement_text: String::new(),
                metavar_env: HashMap::new(),
            })
            .collect();

        let (program, mut args) = platform_fast_success_command();
        args.push("{file}".to_string());
        let template = command_template(program, &args);

        let cli_args = parse_run_args(&["tg", "run", "--test-cmd", &template, "."]);

        let summary = run_post_apply_validation(&cli_args, ".", &edits)
            .expect("expected a validation summary when --test-cmd is set");

        assert_eq!(summary.commands.len(), 5);
        assert!(!summary.validation_targets_truncated);
        assert_eq!(summary.validation_targets_total, 5);
        assert!(summary.success);
    }

    #[test]
    fn search_request_preserves_multiple_path_roots_for_structured_output() {
        let args =
            parse_search_args(&["tg", "search", "ERROR", "src", "tests", "docs", "--ndjson"]);
        let request = resolve_search_request(&args).expect("expected search request");
        let decision = RoutingDecision::native_cpu_json(false);

        assert_eq!(request.patterns, vec!["ERROR".to_string()]);
        assert_eq!(
            request.paths,
            vec!["src".to_string(), "tests".to_string(), "docs".to_string()]
        );
        assert_eq!(request.path_display(), "src tests docs");
        assert_eq!(
            command_ripgrep_args(&args, &request).paths,
            vec!["src".to_string(), "tests".to_string(), "docs".to_string()]
        );
        assert_eq!(
            native_search_config_for_command(
                &args,
                "ERROR",
                &request.paths,
                request.path_was_implicit,
                decision
            )
            .paths,
            vec![
                PathBuf::from("src"),
                PathBuf::from("tests"),
                PathBuf::from("docs")
            ]
        );
    }

    // --- Audit #105: native-CPU implicit-walk-ceiling signal threading ---------------------
    // #100 hoisted a walk-ceiling gate into `execute_ripgrep_search` (rg-passthrough engine
    // only); the native-CPU engine (`run_native_search`, reached via `--json`, `--force-cpu`,
    // single-pattern `--fixed-strings`, and rg-unavailable routing) never received the
    // `path_was_implicit` signal at all -- `NativeSearchConfig` had no such field. These tests
    // pin that the signal is now correctly recorded end-to-end from real CLI parsing through
    // both `NativeSearchConfig` builders, mirroring
    // `frontdoor_args_record_path_was_implicit_for_e_flag_bypass`.

    #[test]
    fn native_search_config_for_command_records_path_was_implicit_for_json_route() {
        // `tg search -e "TODO" --json` (no explicit PATH) is exactly the #105 bypass shape: this
        // routes to `native_cpu_json` (reason "json_output"), never through
        // `execute_ripgrep_search`'s #100 gate at all.
        let implicit_args = parse_search_args(&["tg", "search", "-e", "TODO", "--json"]);
        let implicit_request =
            resolve_search_request(&implicit_args).expect("expected search request");
        assert!(
            implicit_request.path_was_implicit,
            "no PATH given must record path_was_implicit = true"
        );
        let implicit_config = native_search_config_for_command(
            &implicit_args,
            "TODO",
            &implicit_request.paths,
            implicit_request.path_was_implicit,
            RoutingDecision::native_cpu_json(false),
        );
        assert!(
            implicit_config.path_was_implicit,
            "NativeSearchConfig must record path_was_implicit = true for an implicit-path \
             --json search"
        );

        let explicit_args = parse_search_args(&["tg", "search", "-e", "TODO", "--json", "src"]);
        let explicit_request =
            resolve_search_request(&explicit_args).expect("expected search request");
        assert!(
            !explicit_request.path_was_implicit,
            "an explicit trailing PATH must record path_was_implicit = false"
        );
        let explicit_config = native_search_config_for_command(
            &explicit_args,
            "TODO",
            &explicit_request.paths,
            explicit_request.path_was_implicit,
            RoutingDecision::native_cpu_json(false),
        );
        assert!(
            !explicit_config.path_was_implicit,
            "NativeSearchConfig must record path_was_implicit = false for an explicit-path \
             search"
        );
    }

    #[test]
    fn native_search_config_for_positional_records_path_was_implicit() {
        // Sibling of the above for the bare positional fast-path CLI (`tg PATTERN [PATH]`).
        use clap::Parser;
        let implicit_raw_args = ["tg", "TODO", "--json"]
            .iter()
            .map(OsString::from)
            .collect::<Vec<_>>();
        let implicit_cli =
            PositionalCli::try_parse_from(&implicit_raw_args).expect("expected CLI to parse");
        let implicit_paths = implicit_search_paths(&implicit_cli.path, false);
        let implicit_config = native_search_config_for_positional(
            &implicit_cli,
            "TODO",
            &implicit_paths,
            RoutingDecision::native_cpu_json(false),
        );
        assert!(
            implicit_config.path_was_implicit,
            "no PATH given must record path_was_implicit = true"
        );

        let explicit_raw_args = ["tg", "TODO", "--json", "src"]
            .iter()
            .map(OsString::from)
            .collect::<Vec<_>>();
        let explicit_cli =
            PositionalCli::try_parse_from(&explicit_raw_args).expect("expected CLI to parse");
        let explicit_paths = implicit_search_paths(&explicit_cli.path, false);
        let explicit_config = native_search_config_for_positional(
            &explicit_cli,
            "TODO",
            &explicit_paths,
            RoutingDecision::native_cpu_json(false),
        );
        assert!(
            !explicit_config.path_was_implicit,
            "an explicit trailing PATH must record path_was_implicit = false"
        );
    }

    #[test]
    fn collect_native_multi_pattern_matches_exits_2_not_1_on_ceiling_refusal() {
        // Audit #105: `collect_native_multi_pattern_matches` (used by every multi-`-e` native-CPU
        // route) used to let an implicit-walk-ceiling refusal `Err` propagate via `?` all the way
        // to `main()`'s default exit-1 termination instead of the fast-bounded exit-2 refusal
        // every OTHER native-CPU route gets. `exit_on_native_multi_pattern_ceiling_refusal`
        // (the fix) calls `std::process::exit(2)` directly for this ONE error, which cannot be
        // observed in-process without exiting the test binary -- so this pins the OTHER half of
        // the contract instead: the recognizer used to gate that exit call correctly identifies
        // the shared refusal message and does not misfire on an unrelated native-search error.
        let refusal =
            tensor_grep_rs::rg_passthrough::format_unbounded_implicit_search_walk_error(1500);
        assert!(is_unbounded_implicit_search_walk_refusal(&refusal));
        assert!(!is_unbounded_implicit_search_walk_refusal(
            "native search path does not exist: /nope"
        ));
    }

    #[test]
    fn broad_scan_refusal_json_envelope_matches_python_field_for_field() {
        // Task #17: pins the exact vocabulary `cli/main.py`'s `_emit_broad_scan_refusal` uses
        // (`tests/unit/test_broad_scan_refusal_json_envelope.py` pins the Python side) -- the
        // whole point of this task is that BOTH front doors must use these same field names.
        let message = tensor_grep_rs::rg_passthrough::format_unbounded_implicit_search_walk_error(
            tensor_grep_rs::rg_passthrough::IMPLICIT_SEARCH_WALK_FILE_CEILING,
        );
        let payload = broad_scan_refusal_json_envelope(".", &message);
        assert_eq!(payload["version"], JSON_OUTPUT_VERSION);
        assert_eq!(payload["path"], ".");
        assert_eq!(payload["total_matches"], 0);
        assert_eq!(payload["total_files"], 0);
        assert_eq!(payload["matches"], serde_json::json!([]));
        assert_eq!(payload["truncated"], true);
        assert_eq!(payload["result_incomplete"], true);
        assert_eq!(payload["incomplete_reason"], message);
        assert_eq!(payload["incomplete_reason_class"], "scan_limit");
        assert_eq!(payload["error"]["code"], "broad_scan_refused");
        assert_eq!(payload["error"]["message"], message);
        assert_eq!(payload["error"]["retryable"], false);
    }

    #[test]
    fn exit_on_native_multi_pattern_ceiling_refusal_passes_through_other_errors_unchanged() {
        // CONTROL ARM for task #17's `json`/`path` parameters: an error that is NOT the shared
        // ceiling refusal must return completely unmodified -- no printing, no exit, and (unlike
        // the refusal path) this half CAN be observed in-process because it never reaches
        // `std::process::exit`. Regressing this would mean bad-pattern/bad-path errors under
        // `--json` start being misreported as `broad_scan_refused`.
        let original = "native search path does not exist: /nope";
        let err = anyhow::anyhow!(original);
        let returned = exit_on_native_multi_pattern_ceiling_refusal(err, true, ".");
        assert_eq!(returned.to_string(), original);
    }

    #[test]
    fn native_search_config_path_display_mirrors_resolved_search_request_convention() {
        // Same join-with-space-or-default-to-dot convention as `ResolvedSearchRequest::
        // path_display` (used for the SAME field on a successful result), so a refusal's `path`
        // never reads differently from what a successful run of the same invocation would show.
        assert_eq!(native_search_config_path_display(&[]), ".");
        assert_eq!(
            native_search_config_path_display(&[PathBuf::from("src")]),
            "src"
        );
        assert_eq!(
            native_search_config_path_display(&[PathBuf::from("src"), PathBuf::from("tests")]),
            "src tests"
        );
    }

    #[test]
    fn search_request_resolves_multiple_regexp_patterns_and_paths() {
        let args = parse_search_args(&[
            "tg",
            "search",
            "--fixed-strings",
            "-e",
            "TODO",
            "-e",
            "FIXME",
            "src",
            "tests",
        ]);
        let request = resolve_search_request(&args).expect("expected search request");

        assert_eq!(
            request.patterns,
            vec!["TODO".to_string(), "FIXME".to_string()]
        );
        assert_eq!(request.paths, vec!["src".to_string(), "tests".to_string()]);
        assert_eq!(request.query_display(), "TODO | FIXME");
        assert_eq!(request.path_display(), "src tests");
        assert_eq!(
            command_ripgrep_args(&args, &request).patterns,
            vec!["TODO".to_string(), "FIXME".to_string()]
        );
    }

    #[test]
    fn search_request_accepts_dash_leading_regexp_pattern() {
        let args = parse_search_args(&["tg", "search", "-e", "-needle", "--sort", "path", "."]);
        let request = resolve_search_request(&args).expect("expected search request");

        assert_eq!(request.patterns, vec!["-needle".to_string()]);
        assert_eq!(request.paths, vec![".".to_string()]);
        assert_eq!(
            command_ripgrep_args(&args, &request).patterns,
            vec!["-needle".to_string()]
        );
    }

    #[test]
    fn default_search_frontdoor_treats_format_rg_as_noop() {
        let args = parse_default_frontdoor_args(&[
            "tg",
            "search",
            "--format",
            "rg",
            "ERROR",
            "bench_data",
        ]);

        assert_eq!(args.patterns, vec!["ERROR".to_string()]);
        assert_eq!(args.paths, vec!["bench_data".to_string()]);
    }

    #[test]
    fn top_level_search_frontdoor_treats_format_rg_as_noop() {
        let args = parse_default_frontdoor_args(&["tg", "--format", "rg", "ERROR", "bench_data"]);

        assert_eq!(args.patterns, vec!["ERROR".to_string()]);
        assert_eq!(args.paths, vec!["bench_data".to_string()]);
    }

    #[test]
    fn top_level_search_frontdoor_accepts_format_rg_equals_form() {
        let args = parse_default_frontdoor_args(&["tg", "--format=rg", "ERROR", "bench_data"]);

        assert_eq!(args.patterns, vec!["ERROR".to_string()]);
        assert_eq!(args.paths, vec!["bench_data".to_string()]);
    }

    #[test]
    fn top_level_search_frontdoor_accepts_explicit_format_rg_fixed_string() {
        let args = parse_default_frontdoor_args(&[
            "tg",
            "--format",
            "rg",
            "--color",
            "never",
            "--sort",
            "path",
            "-n",
            "-F",
            "ERROR",
            "bench_data",
        ]);

        assert!(args.fixed_strings);
        assert!(args.line_number);
        assert_eq!(args.color.as_deref(), Some("never"));
        assert_eq!(args.sort.as_deref(), Some("path"));
        assert_eq!(args.patterns, vec!["ERROR".to_string()]);
        assert_eq!(args.paths, vec!["bench_data".to_string()]);
    }

    #[test]
    fn top_level_search_frontdoor_accepts_no_line_number() {
        let args = parse_default_frontdoor_args(&[
            "tg",
            "--format",
            "rg",
            "-n",
            "-N",
            "-F",
            "ERROR",
            "bench_data",
        ]);

        assert!(args.fixed_strings);
        assert!(!args.line_number);
        assert!(args.no_line_number);
        assert_eq!(args.patterns, vec!["ERROR".to_string()]);
        assert_eq!(args.paths, vec!["bench_data".to_string()]);
    }

    #[test]
    fn top_level_search_frontdoor_accepts_context_flags_option_first() {
        let args = parse_default_frontdoor_args(&["tg", "-n", "-C", "2", "ERROR", "bench_data"]);

        assert!(args.line_number);
        assert_eq!(args.context, Some(2));
        assert_eq!(args.patterns, vec!["ERROR".to_string()]);
        assert_eq!(args.paths, vec!["bench_data".to_string()]);
    }

    #[test]
    fn search_frontdoor_rejects_plain_json_without_explicit_rg_format() {
        for tokens in [
            vec!["tg", "search", "--json", "ERROR", "bench_data"],
            vec!["tg", "--json", "ERROR", "bench_data"],
        ] {
            let raw_args = tokens.iter().map(OsString::from).collect::<Vec<_>>();
            assert!(
                parse_default_search_frontdoor_args(&raw_args).is_none(),
                "plain --json must stay on tensor-grep aggregate JSON path for {tokens:?}"
            );
        }
    }

    #[test]
    fn search_frontdoor_accepts_json_when_rg_format_is_explicit() {
        for tokens in [
            vec![
                "tg",
                "search",
                "--format",
                "rg",
                "--json",
                "ERROR",
                "bench_data",
            ],
            vec!["tg", "--format", "rg", "--json", "ERROR", "bench_data"],
        ] {
            let raw_args = tokens.iter().map(OsString::from).collect::<Vec<_>>();
            let parsed = parse_default_search_frontdoor_args(&raw_args)
                .expect("explicit --format rg --json should use rg JSON Lines passthrough");
            assert!(parsed.json);
            assert_eq!(parsed.patterns, vec!["ERROR".to_string()]);
            assert_eq!(parsed.paths, vec!["bench_data".to_string()]);
        }
    }

    #[test]
    fn default_search_frontdoor_rejects_non_rg_format() {
        let raw_args = ["tg", "search", "--format=json", "ERROR", "bench_data"]
            .iter()
            .map(OsString::from)
            .collect::<Vec<_>>();

        assert!(parse_default_search_frontdoor_args(&raw_args).is_none());
    }

    #[test]
    fn default_search_frontdoor_accepts_sort_path_passthrough() {
        let args = parse_default_frontdoor_args(&[
            "tg",
            "search",
            "--sort",
            "path",
            "ERROR",
            "bench_data",
        ]);

        assert_eq!(args.sort.as_deref(), Some("path"));
        assert_eq!(args.patterns, vec!["ERROR".to_string()]);
        assert_eq!(args.paths, vec!["bench_data".to_string()]);
    }

    #[test]
    fn search_args_accept_format_rg_when_native_frontdoor_handles_richer_rg_modes() {
        let args = parse_search_args(&[
            "tg",
            "search",
            "--format",
            "rg",
            "--files-with-matches",
            "--sort",
            "path",
            "ERROR",
            "bench_data",
        ]);

        assert_eq!(args.format.as_deref(), Some("rg"));
        assert!(args.files_with_matches);
        assert_eq!(args.sort.as_deref(), Some("path"));
    }

    #[test]
    fn search_format_python_passthrough_args_detects_non_rg_formats() {
        let raw_args = ["tg", "search", "--format=json", "ERROR", "bench_data"]
            .iter()
            .map(OsString::from)
            .collect::<Vec<_>>();

        assert_eq!(
            search_format_python_passthrough_args(&raw_args),
            Some(vec![
                "--format=json".to_string(),
                "ERROR".to_string(),
                "bench_data".to_string()
            ])
        );
    }

    #[test]
    fn search_format_python_passthrough_args_keeps_rg_format_native() {
        let raw_args = ["tg", "search", "--format", "rg", "ERROR", "bench_data"]
            .iter()
            .map(OsString::from)
            .collect::<Vec<_>>();

        assert_eq!(search_format_python_passthrough_args(&raw_args), None);
    }

    #[test]
    fn search_format_python_passthrough_args_routes_rank_flag_to_python() {
        // `tg search --rank` must delegate to the Python sidecar (which owns the BM25 re-rank)
        // instead of being clap-rejected as an unknown flag by the native front door.
        let raw_args = ["tg", "search", "--rank", "invoice", "src"]
            .iter()
            .map(OsString::from)
            .collect::<Vec<_>>();

        assert_eq!(
            search_format_python_passthrough_args(&raw_args),
            Some(vec![
                "--rank".to_string(),
                "invoice".to_string(),
                "src".to_string()
            ])
        );
    }

    #[test]
    fn search_format_python_passthrough_args_routes_semantic_flag_to_python() {
        // `tg search --semantic` must delegate to the Python sidecar (which owns the dense/RRF
        // hybrid re-rank) instead of being clap-rejected as an unknown flag by the native front
        // door -- mirrors the --rank case above.
        let raw_args = ["tg", "search", "--semantic", "invoice", "src"]
            .iter()
            .map(OsString::from)
            .collect::<Vec<_>>();

        assert_eq!(
            search_format_python_passthrough_args(&raw_args),
            Some(vec![
                "--semantic".to_string(),
                "invoice".to_string(),
                "src".to_string()
            ])
        );
    }

    #[test]
    fn search_format_python_passthrough_args_routes_ltl_flag_to_python() {
        // `tg search --ltl` is a Python-side temporal query (CPUBackend::_search_ltl);
        // it must delegate to the Python sidecar instead of being clap-rejected as an
        // unknown flag by the native front door -- mirrors the --rank case above.
        // Registered on BOTH front doors per the 2-front-door law (the other door is
        // bootstrap.py::_TG_ONLY_SEARCH_FLAGS).
        let raw_args = ["tg", "search", "--ltl", "open -> eventually close", "src"]
            .iter()
            .map(OsString::from)
            .collect::<Vec<_>>();

        assert_eq!(
            search_format_python_passthrough_args(&raw_args),
            Some(vec![
                "--ltl".to_string(),
                "open -> eventually close".to_string(),
                "src".to_string()
            ])
        );
    }

    // -- Audit #138/#140: --index must short-circuit the Python-passthrough front door --------

    #[test]
    fn search_format_python_passthrough_args_short_circuits_index_with_json_gated_flag() {
        // `--no-hidden` is in SEARCH_PYTHON_PASSTHROUGH_FLAGS (honored unconditionally by that
        // allowlist). Before the fix, combining it with --index still forwarded the whole
        // invocation -- literal "--index" token included -- to the Python sidecar, which has no
        // such option.
        let raw_args = [
            "tg",
            "search",
            "--index",
            "--no-hidden",
            "--json",
            "ERROR",
            "bench_data",
        ]
        .iter()
        .map(OsString::from)
        .collect::<Vec<_>>();

        assert_eq!(search_format_python_passthrough_args(&raw_args), None);
    }

    #[test]
    fn search_format_python_passthrough_args_short_circuits_index_with_unconditional_allowlist_flag(
    ) {
        // `--require-git` is honored by SEARCH_PYTHON_PASSTHROUGH_FLAGS unconditionally (no
        // --json gate needed) -- a different branch than the --json-gated case above.
        let raw_args = [
            "tg",
            "search",
            "--index",
            "--require-git",
            "ERROR",
            "bench_data",
        ]
        .iter()
        .map(OsString::from)
        .collect::<Vec<_>>();

        assert_eq!(search_format_python_passthrough_args(&raw_args), None);
    }

    #[test]
    fn search_format_python_passthrough_args_short_circuits_index_with_structured_output_only_flag()
    {
        // `--passthru` only routes to Python when combined with --json/--ndjson (the function's
        // third check); confirm --index short-circuits that branch too.
        let raw_args = [
            "tg",
            "search",
            "--index",
            "--json",
            "--passthru",
            "ERROR",
            "bench_data",
        ]
        .iter()
        .map(OsString::from)
        .collect::<Vec<_>>();

        assert_eq!(search_format_python_passthrough_args(&raw_args), None);
    }

    #[test]
    fn search_format_python_passthrough_args_short_circuits_index_with_multiline_only_flag() {
        // `-U`/`--multiline` only routes to Python when combined with --json/--ndjson (the
        // function's fourth, final check); confirm --index short-circuits that branch too.
        let raw_args = [
            "tg",
            "search",
            "--index",
            "--json",
            "-U",
            "ERROR",
            "bench_data",
        ]
        .iter()
        .map(OsString::from)
        .collect::<Vec<_>>();

        assert_eq!(search_format_python_passthrough_args(&raw_args), None);
    }

    #[test]
    fn search_format_python_passthrough_args_still_routes_non_index_invocations_to_python() {
        // TRAP guard: the exact same flag combinations as the short-circuit tests above, MINUS
        // --index, must still route to the Python sidecar exactly as before the fix -- adding the
        // --index short-circuit must not change behavior for non-index invocations.
        let no_hidden = [
            "tg",
            "search",
            "--no-hidden",
            "--json",
            "ERROR",
            "bench_data",
        ]
        .iter()
        .map(OsString::from)
        .collect::<Vec<_>>();
        assert_eq!(
            search_format_python_passthrough_args(&no_hidden),
            Some(vec![
                "--no-hidden".to_string(),
                "--json".to_string(),
                "ERROR".to_string(),
                "bench_data".to_string(),
            ])
        );

        let require_git = ["tg", "search", "--require-git", "ERROR", "bench_data"]
            .iter()
            .map(OsString::from)
            .collect::<Vec<_>>();
        assert_eq!(
            search_format_python_passthrough_args(&require_git),
            Some(vec![
                "--require-git".to_string(),
                "ERROR".to_string(),
                "bench_data".to_string(),
            ])
        );

        let passthru = [
            "tg",
            "search",
            "--json",
            "--passthru",
            "ERROR",
            "bench_data",
        ]
        .iter()
        .map(OsString::from)
        .collect::<Vec<_>>();
        assert_eq!(
            search_format_python_passthrough_args(&passthru),
            Some(vec![
                "--json".to_string(),
                "--passthru".to_string(),
                "ERROR".to_string(),
                "bench_data".to_string(),
            ])
        );
    }

    #[test]
    fn orient_is_a_known_python_command_not_a_search_pattern() {
        // `tg orient PATH` must be recognized as a passthrough command so the native front door
        // delegates to the Python `orient` handler instead of treating "orient" as a ripgrep
        // pattern via run_positional_cli().
        assert!(is_known_python_command("orient"));
    }

    #[test]
    fn scoped_parser_keeps_known_and_reserved_disjoint() {
        // A90 lifecycle invariant: a reserved name must read `reserved == true` AND
        // `known == false` (the unscoped include_str parser leaked reserved names into
        // "known", which would have made the not-known gate never fire). Both directions:
        // known names must still read reserved == false.
        for reserved in ["edit-ready", "verify-edit", "workspace"] {
            assert!(
                is_reserved_python_command(reserved),
                "{reserved} must be reserved"
            );
            assert!(
                !is_known_python_command(reserved),
                "{reserved} must NOT be known (disjointness)"
            );
        }
        assert!(!is_reserved_python_command("orient"));
        assert!(is_known_python_command("orient"));
        assert!(is_known_python_command("search"));
        assert!(!is_reserved_python_command("search"));
    }

    #[test]
    fn scoped_parser_member_surface_is_robust() {
        // A90 codex R2 MEDIUM pins: the scoped set extractor must be quote/escape aware,
        // comment aware, brace-aware inside strings, and trailing-comma tolerant. Real
        // commands.py members are all plain identifiers; these pins protect the parser against
        // future edits (e.g. a member containing '#' or braces, or an escaped quote).
        let members = python_set_members("KNOWN_COMMANDS");
        // The authoritative set must parse completely and losslessly.
        assert!(members.contains(&"search".to_string()));
        assert!(members.contains(&"blast-radius".to_string()));
        assert!(members.contains(&"install-dense".to_string()));
        // Known members are exactly the quoted literals in the block, none come from comments
        // or the PYTHON_FULL_HELP_COMMANDS block (e.g. the docstring/comment lines mention
        // "ripgrep" and "Rust" in prose that must not parse as members).
        assert!(!members.contains(&"ripgrep".to_string()));
        assert!(!members.contains(&"Rust".to_string()));
        // Structure pin: every parsed member must be a CLEAN identifier — no backslash, no
        // quote, no trailing comma smuggled into the name. A leak here means the scanner's
        // escape/quote handling regressed (codex R2 MEDIUM).
        for member in &members {
            assert!(
                !member.contains('\\'),
                "member leaked a backslash: {member:?}"
            );
            assert!(!member.contains('"'), "member leaked a quote: {member:?}");
            assert!(
                !member.ends_with(','),
                "member leaked a trailing comma: {member:?}"
            );
        }
        // Internal hidden commands are legitimate members and must be recognized by membership
        // (so hidden-command dispatch still works); nearest hides them from suggestions.
        assert!(is_known_python_command("__gpu-native-stats"));
        assert!(members.contains(&"__gpu-native-stats".to_string()));
        assert!(!nearest_commands("__gpu-native-stats").contains(&"__gpu-native-stats".to_string()));

        // Reserved names are parsed from THEIR OWN block, not KNOWN_COMMANDS.
        let reserved = python_set_members("RESERVED_TOP_LEVEL_COMMANDS");
        assert!(reserved.contains(&"edit-ready".to_string()));
        assert!(!reserved.contains(&"orient".to_string()));
    }

    #[test]
    fn unknown_top_level_command_refusal_fires_on_reserved_flag_shapes() {
        // A90: `tg edit-ready --json` / `--help` refuse; unreserved pattern+flag stays search.
        let reserved_json = ["tg", "edit-ready", "--json"]
            .iter()
            .map(OsString::from)
            .collect::<Vec<_>>();
        let reserved_help = ["tg", "edit-ready", "--help"]
            .iter()
            .map(OsString::from)
            .collect::<Vec<_>>();
        let unknown_help = ["tg", "qqq", "--help"]
            .iter()
            .map(OsString::from)
            .collect::<Vec<_>>();
        let pattern_flag = ["tg", "hello", "--json"]
            .iter()
            .map(OsString::from)
            .collect::<Vec<_>>();
        let pattern_path = ["tg", "hello", "docs/"]
            .iter()
            .map(OsString::from)
            .collect::<Vec<_>>();
        let reserved_positional = ["tg", "edit-ready", "docs/"]
            .iter()
            .map(OsString::from)
            .collect::<Vec<_>>();
        let known = ["tg", "orient", "--json"]
            .iter()
            .map(OsString::from)
            .collect::<Vec<_>>();

        assert!(top_level_unknown_command_refusal(&reserved_json));
        assert!(top_level_unknown_command_refusal(&reserved_help));
        assert!(top_level_unknown_command_refusal(&unknown_help));
        assert!(!top_level_unknown_command_refusal(&pattern_flag));
        assert!(!top_level_unknown_command_refusal(&pattern_path));
        assert!(!top_level_unknown_command_refusal(&reserved_positional));
        assert!(!top_level_unknown_command_refusal(&known));
    }

    #[test]
    fn nearest_commands_is_bounded_and_deterministic() {
        let near = nearest_commands("searhc");
        assert!(near.contains(&"search".to_string()), "near={near:?}");
        assert!(near.len() <= 5);
        assert!(nearest_commands("qqqqzzzz").is_empty());
        assert_eq!(near, nearest_commands("searhc"));
    }

    #[test]
    fn top_level_search_format_python_passthrough_args_detects_non_rg_formats() {
        let raw_args = ["tg", "--format=json", "ERROR", "bench_data"]
            .iter()
            .map(OsString::from)
            .collect::<Vec<_>>();

        assert_eq!(
            search_format_python_passthrough_args(&raw_args),
            Some(vec![
                "--format=json".to_string(),
                "ERROR".to_string(),
                "bench_data".to_string()
            ])
        );
    }

    #[test]
    fn top_level_format_normalization_does_not_capture_known_commands() {
        let raw_args = ["tg", "classify", "--format", "json", "sample.log"]
            .iter()
            .map(OsString::from)
            .collect::<Vec<_>>();

        assert!(parse_default_search_frontdoor_args(&raw_args).is_none());
        assert_eq!(search_format_python_passthrough_args(&raw_args), None);
    }

    #[test]
    fn one_shot_apply_fast_path_is_only_enabled_for_safe_simple_apply() {
        let args = parse_run_args(&[
            "tg",
            "run",
            "--lang",
            "python",
            "--rewrite",
            "lambda $$$ARGS: $EXPR",
            "--apply",
            "def $F($$$ARGS): return $EXPR",
            "fixture.py",
        ]);
        assert!(can_use_one_shot_apply_fast_path(&args));

        let diff = parse_run_args(&[
            "tg",
            "run",
            "--lang",
            "python",
            "--rewrite",
            "lambda $$$ARGS: $EXPR",
            "--apply",
            "--diff",
            "def $F($$$ARGS): return $EXPR",
            "fixture.py",
        ]);
        assert!(!can_use_one_shot_apply_fast_path(&diff));

        let json = parse_run_args(&[
            "tg",
            "run",
            "--lang",
            "python",
            "--rewrite",
            "lambda $$$ARGS: $EXPR",
            "--apply",
            "--json",
            "def $F($$$ARGS): return $EXPR",
            "fixture.py",
        ]);
        assert!(!can_use_one_shot_apply_fast_path(&json));

        let verify = parse_run_args(&[
            "tg",
            "run",
            "--lang",
            "python",
            "--rewrite",
            "lambda $$$ARGS: $EXPR",
            "--apply",
            "--verify",
            "def $F($$$ARGS): return $EXPR",
            "fixture.py",
        ]);
        assert!(!can_use_one_shot_apply_fast_path(&verify));

        let checkpoint = parse_run_args(&[
            "tg",
            "run",
            "--lang",
            "python",
            "--rewrite",
            "lambda $$$ARGS: $EXPR",
            "--apply",
            "--checkpoint",
            "def $F($$$ARGS): return $EXPR",
            "fixture.py",
        ]);
        assert!(!can_use_one_shot_apply_fast_path(&checkpoint));

        let audit = parse_run_args(&[
            "tg",
            "run",
            "--lang",
            "python",
            "--rewrite",
            "lambda $$$ARGS: $EXPR",
            "--apply",
            "--audit-manifest",
            "audit.json",
            "def $F($$$ARGS): return $EXPR",
            "fixture.py",
        ]);
        assert!(!can_use_one_shot_apply_fast_path(&audit));

        let selector = parse_run_args(&[
            "tg",
            "run",
            "--lang",
            "python",
            "--rewrite",
            "lambda $$$ARGS: $EXPR",
            "--apply",
            "--apply-edit-ids",
            "e0000:fixture.py:0:1",
            "def $F($$$ARGS): return $EXPR",
            "fixture.py",
        ]);
        assert!(!can_use_one_shot_apply_fast_path(&selector));

        let reject_selector = parse_run_args(&[
            "tg",
            "run",
            "--lang",
            "python",
            "--rewrite",
            "lambda $$$ARGS: $EXPR",
            "--apply",
            "--reject-edit-ids",
            "e0000:fixture.py:0:1",
            "def $F($$$ARGS): return $EXPR",
            "fixture.py",
        ]);
        assert!(!can_use_one_shot_apply_fast_path(&reject_selector));

        let validation = parse_run_args(&[
            "tg",
            "run",
            "--lang",
            "python",
            "--rewrite",
            "lambda $$$ARGS: $EXPR",
            "--apply",
            "--lint-cmd",
            "echo lint",
            "--test-cmd",
            "echo test",
            "def $F($$$ARGS): return $EXPR",
            "fixture.py",
        ]);
        assert!(!can_use_one_shot_apply_fast_path(&validation));

        let batch = parse_run_args(&[
            "tg",
            "run",
            "--batch-rewrite",
            "batch-rewrite.json",
            "--apply",
            "fixture.py",
        ]);
        assert!(!can_use_one_shot_apply_fast_path(&batch));
    }

    #[test]
    fn simple_apply_selects_one_shot_apply_fast_path() {
        let args = parse_run_args(&[
            "tg",
            "run",
            "--lang",
            "python",
            "--rewrite",
            "lambda $$$ARGS: $EXPR",
            "--apply",
            "def $F($$$ARGS): return $EXPR",
            "fixture.py",
        ]);

        assert_eq!(
            select_rewrite_apply_mode(&args),
            RewriteApplyMode::OneShotFastPath
        );

        let json = parse_run_args(&[
            "tg",
            "run",
            "--lang",
            "python",
            "--rewrite",
            "lambda $$$ARGS: $EXPR",
            "--apply",
            "--json",
            "def $F($$$ARGS): return $EXPR",
            "fixture.py",
        ]);

        assert_eq!(
            select_rewrite_apply_mode(&json),
            RewriteApplyMode::PlanThenApply
        );
    }

    #[test]
    fn run_accepts_ast_grep_pattern_option() {
        let args = parse_run_args(&[
            "tg",
            "run",
            "--lang",
            "python",
            "--pattern",
            "class $NAME: $$$BODY",
            "fixture.py",
        ]);

        assert_eq!(run_pattern(&args).unwrap(), "class $NAME: $$$BODY");
        // Task #26: the pair, so this test also pins the ORIGIN half. An explicit trailing PATH
        // must report `false` -- the control arm for the scope note, which fires only when the
        // caller supplied nothing.
        assert_eq!(
            run_search_path_with_origin(&args),
            ("fixture.py", false),
            "an explicit trailing PATH must not be recorded as implicit"
        );
    }

    #[test]
    fn run_search_path_reports_the_default_as_implicit() {
        // CONTROL ARM for the test above, and the arm the scope note actually depends on. Without
        // it, hard-coding `false` for the origin would satisfy every other assertion in this file
        // while making the `--json` scope disclosure permanently silent on the AST route --
        // exactly the "fixed the route that happened to be reported" failure #26 exists to close.
        //
        // Both positional shapes, because `run_search_path_with_origin` selects a DIFFERENT
        // positional index depending on whether `--pattern` was used, and a test covering only
        // one of them would leave half the selection logic unguarded.
        let with_pattern_flag = parse_run_args(&[
            "tg",
            "run",
            "--lang",
            "python",
            "--pattern",
            "class $NAME: $$$BODY",
        ]);
        assert_eq!(
            run_search_path_with_origin(&with_pattern_flag),
            (".", true),
            "no PATH after --pattern must default to `.` AND report implicit"
        );

        let positional = parse_run_args(&["tg", "run", "--lang", "python", "class $NAME: $$$BODY"]);
        assert_eq!(
            run_search_path_with_origin(&positional),
            (".", true),
            "no PATH after a positional pattern must default to `.` AND report implicit"
        );
    }

    // --- The main.rs JSON envelope LITERALS ------------------------------------------------
    //
    // These three structs had NO direct test. The only envelope with one was `NativeJsonOutput`
    // over in `native_search.rs`, whose tests go through `emit_json_matches` because that emitter
    // writes to a configurable `output_target`. The three here `println!` instead, so capturing
    // their output means capturing process stdout -- which is why they went untested.
    //
    // Serialising the struct DIRECTLY tests the thing that actually matters: the omit-when-
    // inapplicable shape. Every `skip_serializing_if` here is a promise that a complete,
    // explicitly-scoped search stays BYTE-IDENTICAL for existing consumers, and nothing was
    // checking that promise.

    // Imported HERE rather than at module level: `DEFAULTED_SCOPE_NOTE` is referenced only by the
    // tests below, and a top-level `use` for it would be an unused import in every non-test build
    // -- which `cargo clippy -- -D warnings` turns into a CI failure.
    use tensor_grep_rs::native_search::DEFAULTED_SCOPE_NOTE;

    fn summary_value(summary: &SearchSummaryNdjson) -> serde_json::Value {
        serde_json::to_value(summary).expect("summary must serialize")
    }

    #[test]
    fn ndjson_summary_omits_every_optional_field_when_nothing_is_wrong() {
        // THE BYTE-IDENTICAL PROMISE. All five optionals must be ABSENT, not present-and-null:
        // a `null` is a NEW KEY on the happy path, which is a wire change wearing a default value.
        let value = summary_value(&SearchSummaryNdjson {
            record_type: "summary",
            version: JSON_OUTPUT_VERSION,
            total_matches: 3,
            result_incomplete: None,
            incomplete_reason_class: None,
            incomplete_paths_count: None,
            path_was_defaulted: None,
            scope_note: None,
        });

        assert_eq!(value["type"], serde_json::json!("summary"));
        assert_eq!(value["total_matches"], serde_json::json!(3));
        for key in [
            "result_incomplete",
            "incomplete_reason_class",
            "incomplete_paths_count",
            "path_was_defaulted",
            "scope_note",
        ] {
            assert!(
                value.get(key).is_none(),
                "a complete, explicitly-scoped summary must not carry `{key}`: {value}"
            );
        }
    }

    #[test]
    fn ndjson_summary_carries_both_disclosure_families_when_they_apply() {
        // TREATMENT, and deliberately BOTH families at once. They are independent -- an incomplete
        // walk and a defaulted scope can co-occur -- and a test that only ever sets one would pass
        // against an emitter that dropped the other.
        let value = summary_value(&SearchSummaryNdjson {
            record_type: "summary",
            version: JSON_OUTPUT_VERSION,
            total_matches: 0,
            result_incomplete: Some(true),
            incomplete_reason_class: Some("unreadable_path"),
            incomplete_paths_count: Some(2),
            path_was_defaulted: Some(true),
            scope_note: Some(DEFAULTED_SCOPE_NOTE),
        });

        assert_eq!(value["result_incomplete"], serde_json::json!(true));
        assert_eq!(
            value["incomplete_reason_class"],
            serde_json::json!("unreadable_path")
        );
        assert_eq!(value["incomplete_paths_count"], serde_json::json!(2));
        assert_eq!(value["path_was_defaulted"], serde_json::json!(true));
        assert_eq!(
            value["scope_note"],
            serde_json::json!(DEFAULTED_SCOPE_NOTE),
            "the summary must carry the SHARED note constant, not a local paraphrase"
        );
    }

    fn aggregate_value(
        result_incomplete: Option<bool>,
        incomplete_reason_class: Option<&'static str>,
        incomplete_paths_count: Option<usize>,
        path_was_defaulted: Option<bool>,
        scope_note: Option<&'static str>,
    ) -> serde_json::Value {
        let payload = SearchResultJson {
            version: JSON_OUTPUT_VERSION,
            routing_backend: "NativeCpuBackend",
            routing_reason: "native_cpu",
            sidecar_used: false,
            requested_gpu_device_ids: Vec::new(),
            routing_gpu_device_ids: Vec::new(),
            gpu_evidence_status: None,
            gpu_proof: None,
            native_gpu_unavailable: None,
            not_gpu_proof_reason: None,
            query: "needle",
            path: ".",
            total_files: 0,
            total_matches: 0,
            matched_file_paths: Vec::new(),
            match_counts_by_file: std::collections::BTreeMap::new(),
            matches: Vec::new(),
            result_incomplete,
            incomplete_reason_class,
            incomplete_paths_count,
            path_was_defaulted,
            scope_note,
        };
        serde_json::to_value(&payload).expect("aggregate envelope must serialize")
    }

    #[test]
    fn aggregate_envelope_omits_every_optional_field_when_nothing_is_wrong() {
        // CONTROL, and the load-bearing half: this is the shape every existing `--json` consumer
        // sees today. A key appearing here is a silent contract change.
        let value = aggregate_value(None, None, None, None, None);

        for key in [
            "result_incomplete",
            "incomplete_reason_class",
            "incomplete_paths_count",
            "path_was_defaulted",
            "scope_note",
            "gpu_evidence_status",
            "gpu_proof",
            "native_gpu_unavailable",
            "not_gpu_proof_reason",
        ] {
            assert!(
                value.get(key).is_none(),
                "a complete, explicitly-scoped, non-GPU search must not carry `{key}`: {value}"
            );
        }
        // The unconditional fields must still be there -- an "omits everything" assertion passes
        // trivially against an emitter that emits nothing at all.
        assert_eq!(
            value["routing_backend"],
            serde_json::json!("NativeCpuBackend")
        );
        assert_eq!(value["total_matches"], serde_json::json!(0));
        assert_eq!(value["query"], serde_json::json!("needle"));
    }

    #[test]
    fn aggregate_envelope_carries_both_disclosure_families_when_they_apply() {
        let value = aggregate_value(
            Some(true),
            Some("unreadable_path"),
            Some(1),
            Some(true),
            Some(DEFAULTED_SCOPE_NOTE),
        );

        assert_eq!(value["result_incomplete"], serde_json::json!(true));
        assert_eq!(
            value["incomplete_reason_class"],
            serde_json::json!("unreadable_path")
        );
        assert_eq!(value["incomplete_paths_count"], serde_json::json!(1));
        assert_eq!(value["path_was_defaulted"], serde_json::json!(true));
        assert_eq!(value["scope_note"], serde_json::json!(DEFAULTED_SCOPE_NOTE));
    }

    // THE THIRD LITERAL, `GpuNativeSearchResultJson`, IS DELIBERATELY NOT TESTED HERE, and this
    // comment is the honest record of why rather than a silent omission.
    //
    // It is `#[cfg(feature = "cuda")]`. The only job that compiles that feature is
    // `cuda-feature-check`, which runs `cargo check --features cuda --all-targets` -- CHECK, not
    // TEST. So a `#[cfg(feature = "cuda")] #[test]` added here would be type-checked and NEVER
    // EXECUTED by any job in CI, on any runner. Checking is not running, and a test that never
    // runs is worse than no test: it reports the surface as covered.
    //
    // Whether `cargo test --features cuda` can even LINK on a GPU-less runner is UNANSWERED (task
    // #279 territory) -- I did not verify it, and cargo is CPU-forbidden on the authoring machine,
    // so I am not going to claim either way. If someone establishes that it links, the two tests
    // above port to it directly: the struct differs only by the `pipeline` field and the GPU proof
    // quartet, and `incomplete_envelope_fields`/`defaulted_scope_fields` already feed it the same
    // way they feed these.

    #[test]
    fn run_rejects_duplicate_pattern_forms() {
        let args = parse_run_args(&[
            "tg",
            "run",
            "--lang",
            "python",
            "--pattern",
            "class $NAME: $$$BODY",
            "def $F(): $$$BODY",
            "fixture.py",
        ]);

        let error = run_pattern(&args).unwrap_err().to_string();
        assert!(error.contains("--pattern accepts at most one positional PATH"));
    }

    #[test]
    fn run_files_with_matches_is_read_only() {
        let args = parse_run_args(&[
            "tg",
            "run",
            "--lang",
            "python",
            "--pattern",
            "class $NAME: $$$BODY",
            "--files-with-matches",
            "fixture.py",
        ]);

        assert!(args.files_with_matches);
        assert!(validate_run_args(&args).is_ok());

        let rewrite = parse_run_args(&[
            "tg",
            "run",
            "--lang",
            "python",
            "--pattern",
            "class $NAME: $$$BODY",
            "--files-with-matches",
            "--rewrite",
            "class $NAME: pass",
            "fixture.py",
        ]);

        let error = validate_run_args(&rewrite).unwrap_err().to_string();
        assert!(error.contains("read-only search output mode"));
    }

    #[test]
    fn run_ast_grep_semantic_options_are_read_only_python_passthrough() {
        let args = parse_run_args(&[
            "tg",
            "run",
            "--lang",
            "python",
            "--pattern",
            "print($A)",
            "--selector",
            "call",
            "--strictness",
            "relaxed",
            "--globs",
            "*.py",
            "fixture.py",
        ]);

        assert!(validate_run_args(&args).is_ok());
        assert!(ast_run_requires_python_passthrough(&args));

        let rewrite = parse_run_args(&[
            "tg",
            "run",
            "--lang",
            "python",
            "--pattern",
            "print($A)",
            "--selector",
            "call",
            "--rewrite",
            "logger.info($A)",
            "fixture.py",
        ]);
        let error = validate_run_args(&rewrite).unwrap_err().to_string();
        assert!(error.contains("ast-grep semantic run options are read-only"));
    }

    #[test]
    fn run_stdin_rejects_files_with_matches() {
        let args = parse_run_args(&[
            "tg",
            "run",
            "--lang",
            "python",
            "--pattern",
            "print($A)",
            "--stdin",
            "--files-with-matches",
        ]);

        let error = validate_run_args(&args).unwrap_err().to_string();
        assert!(error.contains("--stdin cannot be combined with --files-with-matches"));
    }

    #[test]
    fn run_files_with_matches_rejects_json() {
        let args = parse_run_args(&[
            "tg",
            "run",
            "--lang",
            "python",
            "--pattern",
            "print($A)",
            "--files-with-matches",
            "--json",
            "fixture.py",
        ]);

        let error = validate_run_args(&args).unwrap_err().to_string();
        assert!(error.contains("--files-with-matches is a read-only text output mode"));
    }

    #[test]
    fn run_stdin_python_passthrough_omits_default_path() {
        let args = parse_run_args(&[
            "tg",
            "run",
            "--lang",
            "python",
            "--pattern",
            "print($A)",
            "--stdin",
            "--json",
        ]);

        let passthrough = ast_run_python_passthrough_args(&args).unwrap();
        assert!(passthrough.contains(&"--stdin".to_string()));
        assert!(!passthrough.contains(&".".to_string()));
    }

    #[test]
    fn run_semantic_options_reject_existing_path_without_pattern_option() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().to_string_lossy().to_string();
        let args = parse_run_args(&["tg", "run", "--selector", "call", &path]);

        let error = validate_run_args(&args).unwrap_err().to_string();

        assert!(
            error.contains("require --pattern <PATTERN> before PATH"),
            "{error}"
        );
    }

    #[test]
    fn early_ripgrep_fast_path_preserves_glob_cases() {
        let spaced = parse_args(&["tg", "search", "--glob", "*.log", "ERROR", "bench_data"]);
        let equals = parse_args(&["tg", "search", "--glob=*.log", "ERROR", "bench_data"]);

        assert!(should_use_early_ripgrep_fast_path(&spaced));
        assert!(should_use_early_ripgrep_fast_path(&equals));
        assert_eq!(spaced.globs, vec!["*.log".to_string()]);
        assert_eq!(equals.globs, vec!["*.log".to_string()]);

        let frontdoor =
            parse_default_frontdoor_args(&["tg", "search", "--glob=*.log", "ERROR", "bench_data"]);
        assert_eq!(frontdoor.globs, vec!["*.log".to_string()]);
    }

    #[test]
    fn frontdoor_args_record_path_was_implicit_for_e_flag_bypass() {
        // Audit #100 RED-before-fix shape: `tg search -e "TODO" --glob "*.py"` with NO explicit
        // PATH used to bypass the walk-ceiling probe entirely -- `parse_early_ripgrep_args`'s
        // `-e` arm defaulted `paths` to `["."]` with no `path_was_implicit` record at all, so no
        // caller (including this exact frontdoor) could gate on it. This pins the fix: the
        // frontdoor parser now records `path_was_implicit` correctly for the `-e` form.
        let implicit =
            parse_default_frontdoor_args(&["tg", "search", "-e", "TODO", "--glob", "*.py"]);
        assert!(
            implicit.path_was_implicit,
            "no PATH given via -e + --glob must record path_was_implicit = true"
        );

        let explicit = parse_default_frontdoor_args(&[
            "tg",
            "search",
            "-e",
            "TODO",
            "--glob",
            "*.py",
            "some/scoped/dir",
        ]);
        assert!(
            !explicit.path_was_implicit,
            "an explicit trailing PATH must record path_was_implicit = false"
        );

        // #105 extension: the SAME `-e` bypass shape without any --glob/--type at all must also
        // record path_was_implicit = true -- the hoisted gate no longer requires a glob/type to
        // fire, so this bare form must still surface the implicit-path signal.
        let bare_implicit = parse_default_frontdoor_args(&["tg", "search", "-e", "TODO"]);
        assert!(
            bare_implicit.path_was_implicit,
            "a bare -e with no PATH and no glob/type must still record path_was_implicit = true"
        );

        // Positional-pattern form (no -e) requires >= 2 positionals to parse at all (pattern +
        // >= 1 path), so it can never observe an implicit path through this parser.
        let positional = parse_default_frontdoor_args(&["tg", "search", "ERROR", "bench_data"]);
        assert!(
            !positional.path_was_implicit,
            "positional-pattern form always carries an explicit path when it parses at all"
        );
    }

    fn _make_stub_file_dir(dir: &std::path::Path, file_count: usize) {
        for index in 0..file_count {
            std::fs::write(dir.join(format!("stub_{index}.py")), "TODO placeholder\n").unwrap();
        }
    }

    #[test]
    fn implicit_search_walk_refuses_over_ceiling_file_count() {
        // Bug #88: a bare `--glob` search with NO explicit PATH on a root whose WALK exceeds the
        // ceiling must be refused -- the exact gap that let a bare `tg search --glob X PATTERN`
        // from a large/unscoped cwd walk/search unbounded via `execute_ripgrep_search`.
        let dir = tempfile::tempdir().unwrap();
        _make_stub_file_dir(dir.path(), 1600);

        let exceeds = implicit_search_walk_exceeds_ceiling(
            &[dir.path().to_string_lossy().to_string()],
            None,
            false,
            false,
            IMPLICIT_SEARCH_WALK_FILE_CEILING,
        );

        assert!(
            exceeds,
            "expected the 1600-file root's WALK to exceed the 1500 ceiling"
        );
    }

    #[test]
    fn implicit_search_walk_allows_count_under_ceiling() {
        let dir = tempfile::tempdir().unwrap();
        _make_stub_file_dir(dir.path(), 50);

        let exceeds = implicit_search_walk_exceeds_ceiling(
            &[dir.path().to_string_lossy().to_string()],
            None,
            false,
            false,
            IMPLICIT_SEARCH_WALK_FILE_CEILING,
        );

        assert!(!exceeds, "a 50-file root must not be refused");
    }

    #[test]
    fn implicit_search_walk_counts_the_walk_not_a_selective_glob_match() {
        // The dogfood re-harvest's core finding (hypothesis 3): the hang is TREE-WALK cost, NOT
        // post-glob MATCH count. This probe counts every FILE the walker VISITS (glob-independent),
        // so a huge tree that a SELECTIVE glob would narrow to a few matches is STILL refused --
        // because the real search must still WALK the whole tree to find those few matches. Here
        // 1600 `.py` files exist but the search glob is `*.txt` (0 matches); the walk is still
        // 1600 files, which must exceed the ceiling. (The old match-count probe returned false
        // here -> proceeded -> hung; this is the RED-before case for the walk-count fix.)
        let dir = tempfile::tempdir().unwrap();
        _make_stub_file_dir(dir.path(), 1600);

        let exceeds = implicit_search_walk_exceeds_ceiling(
            &[dir.path().to_string_lossy().to_string()],
            None,
            false,
            false,
            IMPLICIT_SEARCH_WALK_FILE_CEILING,
        );

        assert!(
            exceeds,
            "walk cost (1600 files) must be refused regardless of how selective the glob is"
        );
    }

    #[test]
    fn implicit_search_walk_respects_max_depth() {
        // `--max-depth` genuinely bounds the WALK (unlike a file glob): nest 1600 files one dir
        // deep and confirm `--max-depth 1` (which never descends into them) is not refused.
        let dir = tempfile::tempdir().unwrap();
        let nested = dir.path().join("nested");
        std::fs::create_dir(&nested).unwrap();
        _make_stub_file_dir(&nested, 1600);

        let exceeds = implicit_search_walk_exceeds_ceiling(
            &[dir.path().to_string_lossy().to_string()],
            Some(1),
            false,
            false,
            IMPLICIT_SEARCH_WALK_FILE_CEILING,
        );

        assert!(
            !exceeds,
            "max-depth 1 must not descend into the nested 1600-file dir"
        );
    }

    #[test]
    fn implicit_search_walk_empty_paths_probe_is_self_bounded_no_root() {
        // Regression for the SECOND gap the re-harvest caught: `request.paths` is EMPTY (not
        // `["."]`) when stdin is readable, so the probe saw no root and skipped. The probe itself
        // returns false on genuinely-empty roots (nothing to walk); the FIX lives at the call
        // site, which normalizes an implicit empty-paths search to `["."]` before calling this.
        // This test pins the probe's empty-roots contract so a future refactor cannot make it
        // panic or scan the process cwd unexpectedly.
        let exceeds = implicit_search_walk_exceeds_ceiling(
            &[],
            None,
            false,
            false,
            IMPLICIT_SEARCH_WALK_FILE_CEILING,
        );
        assert!(!exceeds, "no roots -> nothing to walk -> not refused");
    }

    #[test]
    fn implicit_search_walk_only_fires_when_path_implicit() {
        // Non-regression (Trap #3 parity): an EXPLICIT path combined with --glob over the ceiling
        // must NOT be refused -- callers gate the probe on `request.path_was_implicit`. Verified
        // directly here since `handle_ripgrep_search` spawns a real rg subprocess and is not
        // unit-testable in-process. The probe WOULD flag this root; the `path_was_implicit` gate
        // at the call site is the only thing that lets an explicit-path glob search proceed.
        let dir = tempfile::tempdir().unwrap();
        _make_stub_file_dir(dir.path(), 1600);
        let path_str = dir.path().to_string_lossy().to_string();

        let request = ResolvedSearchRequest {
            patterns: vec!["TODO".to_string()],
            paths: vec![path_str.clone()],
            path_was_implicit: false,
        };
        assert!(!request.path_was_implicit);

        let exceeds = implicit_search_walk_exceeds_ceiling(
            &request.paths,
            None,
            false,
            false,
            IMPLICIT_SEARCH_WALK_FILE_CEILING,
        );
        assert!(
            exceeds,
            "sanity: the fixture itself exceeds the ceiling, so only the path_was_implicit gate protects it"
        );
    }

    #[test]
    fn early_ripgrep_fast_path_rejects_fixed_and_word_cases() {
        let glob = parse_args(&["tg", "search", "--glob=*.log", "ERROR", "bench_data"]);
        let fixed = parse_args(&["tg", "search", "-F", "[ERROR]", "bench_data"]);
        let word = parse_args(&["tg", "search", "-w", "timeout", "bench_data"]);

        assert!(should_use_early_ripgrep_fast_path(&glob));
        assert!(!should_use_early_ripgrep_fast_path(&fixed));
        assert!(!should_use_early_ripgrep_fast_path(&word));
    }

    #[test]
    fn early_ripgrep_fast_path_keeps_plain_benchmark_shapes() {
        let simple = parse_args(&["tg", "search", "ERROR", "bench_data"]);
        let regex = parse_args(&["tg", "search", "ERROR.*timeout", "bench_data"]);
        let count = parse_args(&["tg", "search", "-c", "ERROR", "bench_data"]);
        let context = parse_args(&["tg", "search", "-C", "2", "CRITICAL", "bench_data"]);
        let max_count = parse_args(&["tg", "search", "-m", "10", "ERROR", "bench_data"]);
        assert!(should_use_early_ripgrep_fast_path(&simple));
        assert!(should_use_early_ripgrep_fast_path(&regex));
        assert!(should_use_early_ripgrep_fast_path(&count));
        assert!(should_use_early_ripgrep_fast_path(&context));
        assert!(should_use_early_ripgrep_fast_path(&max_count));
    }

    #[test]
    fn cold_rg_shaped_modes_prefer_ripgrep_passthrough() {
        let count_args = parse_search_args(&["tg", "search", "--count", "ERROR", "bench_data"]);
        let count_request = resolve_search_request(&count_args).unwrap();
        assert!(search_prefers_ripgrep_passthrough(
            &count_args,
            &count_request,
            true
        ));

        let glob_args =
            parse_search_args(&["tg", "search", "--glob", "*.log", "ERROR", "bench_data"]);
        let glob_request = resolve_search_request(&glob_args).unwrap();
        assert!(search_prefers_ripgrep_passthrough(
            &glob_args,
            &glob_request,
            true
        ));

        let no_ignore_args =
            parse_search_args(&["tg", "search", "--no-ignore", "ERROR", "bench_data"]);
        let no_ignore_request = resolve_search_request(&no_ignore_args).unwrap();
        assert!(!search_requires_ripgrep_passthrough(&no_ignore_args));
        assert!(search_prefers_ripgrep_passthrough(
            &no_ignore_args,
            &no_ignore_request,
            true
        ));
        let no_ignore_json_args = parse_search_args(&[
            "tg",
            "search",
            "--json",
            "--no-ignore",
            "ERROR",
            "bench_data",
        ]);
        let no_ignore_json_request = resolve_search_request(&no_ignore_json_args).unwrap();
        assert!(!search_requires_ripgrep_passthrough(&no_ignore_json_args));
        assert!(!search_prefers_ripgrep_passthrough(
            &no_ignore_json_args,
            &no_ignore_json_request,
            true
        ));
        let no_ignore_ndjson_args = parse_search_args(&[
            "tg",
            "search",
            "--ndjson",
            "--no-ignore",
            "ERROR",
            "bench_data",
        ]);
        let no_ignore_ndjson_request = resolve_search_request(&no_ignore_ndjson_args).unwrap();
        assert!(!search_requires_ripgrep_passthrough(&no_ignore_ndjson_args));
        assert!(!search_prefers_ripgrep_passthrough(
            &no_ignore_ndjson_args,
            &no_ignore_ndjson_request,
            true
        ));

        let no_ignore_vcs_args =
            parse_search_args(&["tg", "search", "--no-ignore-vcs", "ERROR", "bench_data"]);
        let no_ignore_vcs_request = resolve_search_request(&no_ignore_vcs_args).unwrap();
        assert!(!search_requires_ripgrep_passthrough(&no_ignore_vcs_args));
        assert!(search_prefers_ripgrep_passthrough(
            &no_ignore_vcs_args,
            &no_ignore_vcs_request,
            true
        ));
        let no_ignore_vcs_json_args = parse_search_args(&[
            "tg",
            "search",
            "--json",
            "--no-ignore-vcs",
            "ERROR",
            "bench_data",
        ]);
        let no_ignore_vcs_json_request = resolve_search_request(&no_ignore_vcs_json_args).unwrap();
        assert!(!search_requires_ripgrep_passthrough(
            &no_ignore_vcs_json_args
        ));
        assert!(!search_prefers_ripgrep_passthrough(
            &no_ignore_vcs_json_args,
            &no_ignore_vcs_json_request,
            true
        ));

        let many_fixed_args = parse_search_args(&[
            "tg",
            "search",
            "--fixed-strings",
            "-e",
            "ERROR",
            "-e",
            "WARN",
            "bench_data",
        ]);
        let many_fixed_request = resolve_search_request(&many_fixed_args).unwrap();
        assert!(search_prefers_ripgrep_passthrough(
            &many_fixed_args,
            &many_fixed_request,
            true
        ));
        assert!(!search_prefers_ripgrep_passthrough(
            &many_fixed_args,
            &many_fixed_request,
            false
        ));

        let explicit_index =
            parse_search_args(&["tg", "search", "--index", "--count", "ERROR", "bench_data"]);
        let explicit_index_request = resolve_search_request(&explicit_index).unwrap();
        assert!(!search_prefers_ripgrep_passthrough(
            &explicit_index,
            &explicit_index_request,
            true
        ));

        let warm_index_dir = tempfile::tempdir().unwrap();
        std::fs::write(warm_index_dir.path().join(".tg_index"), b"stale").unwrap();
        let warm_index_path = warm_index_dir.path().to_str().unwrap();
        let warm_index_count =
            parse_search_args(&["tg", "search", "--count", "ERROR", warm_index_path]);
        let warm_index_request = resolve_search_request(&warm_index_count).unwrap();
        assert!(!search_prefers_ripgrep_passthrough(
            &warm_index_count,
            &warm_index_request,
            true
        ));

        let forced_cpu = parse_search_args(&[
            "tg",
            "search",
            "--cpu",
            "--fixed-strings",
            "-e",
            "ERROR",
            "-e",
            "WARN",
            "bench_data",
        ]);
        let forced_cpu_request = resolve_search_request(&forced_cpu).unwrap();
        assert!(!search_prefers_ripgrep_passthrough(
            &forced_cpu,
            &forced_cpu_request,
            true
        ));
    }

    #[test]
    fn early_positional_ripgrep_args_parse_plain_shapes() {
        let raw_args = ["tg", "-i", "warning", "bench_data"]
            .into_iter()
            .map(OsString::from)
            .collect::<Vec<_>>();

        let parsed = parse_early_positional_ripgrep_args(&raw_args)
            .expect("expected early positional rg args to parse");

        assert!(parsed.ignore_case);
        assert_eq!(parsed.patterns, vec!["warning".to_string()]);
        assert_eq!(parsed.paths, vec!["bench_data".to_string()]);
    }

    #[test]
    fn implicit_search_paths_follow_rg_stdin_semantics() {
        assert_eq!(
            implicit_search_paths(&[], false),
            vec![".".to_string()],
            "without readable stdin, no-path searches should default to cwd"
        );
        assert!(
            implicit_search_paths(&[], true).is_empty(),
            "with readable stdin, no-path searches should let rg read stdin"
        );
        assert_eq!(
            implicit_search_paths(&["fixture.txt".to_string()], true),
            vec!["fixture.txt".to_string()],
            "explicit paths must beat piped stdin"
        );
    }

    #[test]
    fn early_positional_ripgrep_args_parse_max_count_shape() {
        let raw_args = ["tg", "-m", "1", "warning", "bench_data"]
            .into_iter()
            .map(OsString::from)
            .collect::<Vec<_>>();

        let parsed = parse_early_positional_ripgrep_args(&raw_args)
            .expect("expected early positional rg max-count args to parse");

        assert_eq!(parsed.max_count, Some(1));
        assert_eq!(parsed.patterns, vec!["warning".to_string()]);
        assert_eq!(parsed.paths, vec!["bench_data".to_string()]);
    }

    #[test]
    fn early_positional_ripgrep_args_parse_word_regexp_shape() {
        let short_args = ["tg", "-w", "word", "bench_data"]
            .into_iter()
            .map(OsString::from)
            .collect::<Vec<_>>();
        let long_args = ["tg", "--word-regexp", "word", "bench_data"]
            .into_iter()
            .map(OsString::from)
            .collect::<Vec<_>>();

        let short = parse_early_positional_ripgrep_args(&short_args)
            .expect("expected early positional rg word-regexp args to parse");
        let long = parse_early_positional_ripgrep_args(&long_args)
            .expect("expected early positional rg long word-regexp args to parse");

        for parsed in [short, long] {
            assert!(parsed.word_regexp);
            assert_eq!(parsed.patterns, vec!["word".to_string()]);
            assert_eq!(parsed.paths, vec!["bench_data".to_string()]);
        }
    }

    #[test]
    fn early_positional_ripgrep_args_reject_structured_and_force_cpu_shapes() {
        let structured = ["tg", "--json", "warning", "bench_data"]
            .into_iter()
            .map(OsString::from)
            .collect::<Vec<_>>();
        let force_cpu = ["tg", "--cpu", "warning", "bench_data"]
            .into_iter()
            .map(OsString::from)
            .collect::<Vec<_>>();

        assert!(parse_early_positional_ripgrep_args(&structured).is_none());
        assert!(parse_early_positional_ripgrep_args(&force_cpu).is_none());
    }

    #[test]
    fn default_search_frontdoor_accepts_plain_benchmark_shapes() {
        let raw_args = ["tg", "search", "ERROR", "bench_data"]
            .into_iter()
            .map(OsString::from)
            .collect::<Vec<_>>();

        let parsed = parse_default_search_frontdoor_args(&raw_args)
            .expect("expected default search frontdoor args to parse");

        assert_eq!(parsed.patterns, vec!["ERROR".to_string()]);
        assert_eq!(parsed.paths, vec!["bench_data".to_string()]);
        assert!(!parsed.line_number);
    }

    /// Thin wrapper so every assertion below stays a short, unambiguous line.
    fn frontdoor_admits(argv: &[OsString], stdout_is_terminal: bool) -> bool {
        frontdoor_search_is_native_plain_text_eligible_with_terminal(argv, stdout_is_terminal)
    }

    fn os_argv(tokens: &[&str]) -> Vec<OsString> {
        tokens.iter().map(OsString::from).collect()
    }

    /// Perf lever: an admitted plain-text search (single pattern, single explicit REGULAR FILE,
    /// only allow-listed flags, piped stdout) must be declined by the rg front door so it can be
    /// answered in-process by the native CPU engine instead of paying for an `rg` subprocess.
    #[test]
    fn frontdoor_plain_text_eligibility_admits_only_the_proven_subset() {
        let corpus = tempfile::tempdir().unwrap();
        let file = corpus.path().join("a.txt");
        std::fs::write(&file, "needle alpha\n").unwrap();
        let file_arg = file.display().to_string();
        let dir_arg = corpus.path().display().to_string();

        let with_flags = |extra: &[&str], path: &str| -> Vec<OsString> {
            let mut raw = vec!["tg".to_string(), "search".to_string()];
            raw.extend(extra.iter().map(|flag| (*flag).to_string()));
            raw.push("needle".to_string());
            raw.push(path.to_string());
            raw.iter().map(OsString::from).collect()
        };

        // ADMITTED: bare, each allow-listed flag on its own, and clap's COMBINED short clusters.
        // `-in` is admitted deliberately: clap expands it to `-i -n`, both admitted, so
        // `search_args_allow_plain_text_native` admits it and this adapter MUST agree. An earlier
        // revision of this test asserted `-in` kept spawning rg, which was FALSE at system level:
        // `parse_early_ripgrep_args` rejects `-in`, so it falls through to clap and reaches
        // `route_search` regardless of what this front door decides.
        let admitted: &[&[&str]] = &[
            &[],
            &["-i"],
            &["--ignore-case"],
            &["-F"],
            &["--fixed-strings"],
            &["-w"],
            &["--word-regexp"],
            &["-n"],
            &["--line-number"],
            &["--verbose"],
            &["-in"],
            &["-iw"],
            &["-Fn"],
            &["-i", "-n"],
        ];
        for extra in admitted {
            let argv = with_flags(extra, &file_arg);
            assert!(frontdoor_admits(&argv, false), "{extra:?} must stay native");
        }

        // EXCLUDED flags: each must keep today's rg passthrough. A cluster containing ONE
        // non-admitted letter refuses as a whole.
        let excluded: &[&[&str]] = &[
            &["-c"],
            &["--count"],
            &["-v"],
            &["-o"],
            &["-N"],
            &["-S"],
            &["--smart-case"],
            &["-a"],
            &["--hidden"],
            &["-L"],
            &["-l"],
            &["--count-matches"],
            &["--column"],
            &["--vimgrep"],
            &["--passthru"],
            &["-P"],
            &["--json"],
            &["--ndjson"],
            &["--cpu"],
            &["--index"],
            &["-ic"],
            &["-vn"],
        ];
        for extra in excluded {
            let argv = with_flags(extra, &file_arg);
            assert!(!frontdoor_admits(&argv, false), "{extra:?} must use rg");
        }

        // A terminal keeps rg (heading + color layout the native engine does not reproduce).
        let on_terminal = with_flags(&[], &file_arg);
        assert!(!frontdoor_admits(&on_terminal, true));

        // A DIRECTORY path keeps rg: walking diverges on binary-file messages and file order.
        let directory = with_flags(&[], &dir_arg);
        assert!(!frontdoor_admits(&directory, false));

        // A BINARY file keeps rg: `rg` spells the notice `"\0"` while the native engine's
        // GOVERNED output contract spells it `"/0"` (see `native_can_serve_plain_text` note 5c).
        let binary = corpus.path().join("binary.bin");
        std::fs::write(&binary, b"needle\0binary tail\n").unwrap();
        let binary_argv = with_flags(&[], &binary.display().to_string());
        assert!(!frontdoor_admits(&binary_argv, false));

        // A CRLF file keeps rg: the native plain sink strips the trailing `\r` while rg keeps it
        // (note 5a). This is the divergence that would have hit routine Windows searches.
        let crlf = corpus.path().join("crlf.txt");
        std::fs::write(&crlf, b"needle alpha\r\nplain\r\n").unwrap();
        let crlf_argv = with_flags(&[], &crlf.display().to_string());
        assert!(!frontdoor_admits(&crlf_argv, false));

        // A NON-UTF-8 file keeps rg: the native plain sink is `Lossy` and substitutes U+FFFD
        // where rg writes the raw byte (note 5b) -- silent corruption.
        let latin1 = corpus.path().join("latin1.txt");
        std::fs::write(&latin1, b"caf\xe9 needle here\n").unwrap();
        let latin1_argv = with_flags(&[], &latin1.display().to_string());
        assert!(!frontdoor_admits(&latin1_argv, false));

        // A missing path keeps rg (rg owns the error message).
        let absent = corpus.path().join("absent.txt").display().to_string();
        let absent_argv = with_flags(&[], &absent);
        assert!(!frontdoor_admits(&absent_argv, false));

        // An EMPTY PATTERN keeps rg: `run_native_search` rejects it and the rg fallback net then
        // prints a `warning: native CPU search failed...` line rg never emits (note 6).
        let empty_pattern = os_argv(&["tg", "search", "", file_arg.as_str()]);
        assert!(!frontdoor_admits(&empty_pattern, false));

        // PATTERN-level refusals (note 7). The file probe cannot see these -- they are properties
        // of the pattern on an otherwise fully admitted request. Rows 1-2 are an exit-code
        // regression (rg rc=2 -> native rc=1); row 3 fails to compile and trips the extra-stderr
        // fallback warning.
        for pattern in [
            "needle\\n",
            "\\n",
            "[\\n]",
            "needle\\r\\n",
            "\\x00",
            "\\0",
            "\\u{a}",
            "needle\n",
            "[",
            "(",
            "\\Qx\\E",
            "a{500}{500}{500}",
        ] {
            let argv = os_argv(&["tg", "search", pattern, file_arg.as_str()]);
            assert!(!frontdoor_admits(&argv, false), "{pattern:?} must use rg");
        }

        // ... while an ordinary regex with escapes the native matcher handles stays admitted.
        for pattern in ["needle", "need(le|ful)", "\\bneedle\\b", "\\d+", "needle$"] {
            let argv = os_argv(&["tg", "search", pattern, file_arg.as_str()]);
            assert!(
                frontdoor_admits(&argv, false),
                "{pattern:?} must stay native"
            );
        }

        // An IMPLICIT path keeps rg: `rg needle` prints `a.txt:...` while the native engine is
        // handed the literal "." default and would print `./a.txt:...`.
        let implicit = os_argv(&["tg", "search", "needle"]);
        assert!(!frontdoor_admits(&implicit, false));

        // TWO paths keep rg (the predicate admits exactly one).
        let two_paths = os_argv(&[
            "tg",
            "search",
            "needle",
            file_arg.as_str(),
            file_arg.as_str(),
        ]);
        assert!(!frontdoor_admits(&two_paths, false));

        // `-e PATTERN FILE` is the same admitted shape by another spelling and must agree.
        let dash_e = os_argv(&["tg", "search", "-e", "needle", file_arg.as_str()]);
        assert!(frontdoor_admits(&dash_e, false));

        // Two `-e` patterns refuse (the predicate admits exactly one).
        let two_e = os_argv(&["tg", "search", "-e", "a", "-e", "b", file_arg.as_str()]);
        assert!(!frontdoor_admits(&two_e, false));
    }

    /// The front door itself must decline the admitted shape (returning `None` hands the request
    /// on to clap -> `handle_ripgrep_search` -> `route_search`, which selects the native engine).
    #[test]
    fn default_search_frontdoor_declines_admitted_plain_text_file_search() {
        let corpus = tempfile::tempdir().unwrap();
        let file = corpus.path().join("a.txt");
        std::fs::write(&file, "needle alpha\n").unwrap();

        let file_arg = file.display().to_string();
        let raw_args = os_argv(&["tg", "search", "needle", file_arg.as_str()]);

        // Guard against a terminal-attached local run, where the predicate correctly refuses.
        if frontdoor_admits(&raw_args, false) && !stdout_is_terminal() {
            assert!(parse_default_search_frontdoor_args(&raw_args).is_none());
        }
    }

    /// `--format rg` is an explicit demand for ripgrep's own renderer and must never be diverted.
    #[test]
    fn default_search_frontdoor_still_passes_explicit_rg_format_through() {
        let corpus = tempfile::tempdir().unwrap();
        let file = corpus.path().join("a.txt");
        std::fs::write(&file, "needle alpha\n").unwrap();

        let file_arg = file.display().to_string();
        let raw_args = os_argv(&[
            "tg",
            "search",
            "--format",
            "rg",
            "needle",
            file_arg.as_str(),
        ]);

        let parsed = parse_default_search_frontdoor_args(&raw_args)
            .expect("--format rg must stay on the ripgrep passthrough");
        assert_eq!(parsed.patterns, vec!["needle".to_string()]);
    }

    /// THE adapter-agreement gate. The raw-argv front-door adapter and the clap-side computation
    /// (`plain_text_native_request_for_search`, the SAME function `handle_ripgrep_search` calls)
    /// must return an identical verdict for every shape listed here.
    ///
    /// Why this is load-bearing rather than tidiness: the front door is only ONE path into
    /// `route_search`. Anything `parse_early_ripgrep_args` rejects -- a combined short (`-in`), a
    /// `-w`/`-F` request (excluded by `should_use_early_ripgrep_fast_path`), an unknown token --
    /// falls through to clap and reaches `route_search` regardless of what the front door thinks.
    /// So the real admission surface is the clap side, and any shape the front door judges
    /// differently is a shape whose routing nobody actually reasoned about.
    ///
    /// SCOPE, stated precisely so this is not read as a stronger guarantee than it is: equality
    /// holds for the shapes below, NOT for every conceivable argv. Attached-value short spellings
    /// (`-ie needle f`, `-eneedle f`, `-e=needle f`) are known to disagree -- the front door
    /// refuses on the cluster letter `e` while clap admits. Those are all in the SAFE direction and
    /// are pinned separately by
    /// `plain_text_native_frontdoor_is_never_looser_than_the_clap_gate`.
    #[test]
    fn plain_text_native_adapters_agree_on_the_listed_shapes() {
        let corpus = tempfile::tempdir().unwrap();
        let file = corpus.path().join("a.txt");
        std::fs::write(&file, "needle alpha\n").unwrap();
        let crlf = corpus.path().join("crlf.txt");
        std::fs::write(&crlf, b"needle alpha\r\n").unwrap();
        let latin1 = corpus.path().join("latin1.txt");
        std::fs::write(&latin1, b"caf\xe9 needle here\n").unwrap();
        let binary = corpus.path().join("binary.bin");
        std::fs::write(&binary, b"needle\0tail\n").unwrap();

        let file_arg = file.display().to_string();
        let dir_arg = corpus.path().display().to_string();
        let crlf_arg = crlf.display().to_string();
        let latin1_arg = latin1.display().to_string();
        let binary_arg = binary.display().to_string();
        let absent_arg = corpus.path().join("absent.txt").display().to_string();

        let shapes: Vec<Vec<&str>> = vec![
            vec!["needle", file_arg.as_str()],
            vec!["-i", "needle", file_arg.as_str()],
            vec!["-n", "needle", file_arg.as_str()],
            vec!["-F", "needle", file_arg.as_str()],
            vec!["-w", "needle", file_arg.as_str()],
            vec!["--verbose", "needle", file_arg.as_str()],
            vec!["-in", "needle", file_arg.as_str()],
            vec!["-iw", "needle", file_arg.as_str()],
            vec!["-i", "-n", "needle", file_arg.as_str()],
            vec!["-ic", "needle", file_arg.as_str()],
            vec!["-e", "needle", file_arg.as_str()],
            vec!["-e", "needle", "-w", file_arg.as_str()],
            vec!["-e", "needle", "-e", "alpha", file_arg.as_str()],
            vec!["", file_arg.as_str()],
            vec!["needle", dir_arg.as_str()],
            vec!["needle", crlf_arg.as_str()],
            vec!["needle", latin1_arg.as_str()],
            vec!["needle", binary_arg.as_str()],
            vec!["needle", absent_arg.as_str()],
            vec!["needle", file_arg.as_str(), file_arg.as_str()],
            vec!["--", "needle", file_arg.as_str()],
            vec!["-i", "--", "needle", file_arg.as_str()],
            vec!["needle"],
            // Refusal note 7: patterns rg rejects outright, and patterns that do not compile.
            vec!["needle\\n", file_arg.as_str()],
            vec!["\\n", file_arg.as_str()],
            vec!["[\\n]", file_arg.as_str()],
            vec!["needle\\r\\n", file_arg.as_str()],
            vec!["\\x00", file_arg.as_str()],
            vec!["[", file_arg.as_str()],
            vec!["(", file_arg.as_str()],
            vec!["\\Qx\\E", file_arg.as_str()],
            vec!["a{500}{500}{500}", file_arg.as_str()],
            vec!["-F", "needle\\n", file_arg.as_str()],
            // A REAL newline byte in the pattern, not the two-character escape.
            vec!["needle\n", file_arg.as_str()],
            vec!["-c", "needle", file_arg.as_str()],
            vec!["-v", "needle", file_arg.as_str()],
            vec!["-N", "needle", file_arg.as_str()],
            vec!["-S", "needle", file_arg.as_str()],
            vec!["--json", "needle", file_arg.as_str()],
            vec!["--ndjson", "needle", file_arg.as_str()],
            vec!["--cpu", "needle", file_arg.as_str()],
        ];

        for shape in shapes {
            let mut argv = vec!["tg", "search"];
            argv.extend(shape.iter().copied());

            let raw_args = os_argv(&argv);
            let frontdoor = frontdoor_admits(&raw_args, false);

            let args = parse_search_args(&argv);
            let request = resolve_search_request_with_stdin(&args, false)
                .expect("expected the shape to resolve into a search request");
            let facts = plain_text_native_request_for_search(&args, &request, false);
            let clap_side = native_can_serve_plain_text(&facts);

            assert_eq!(
                frontdoor, clap_side,
                "adapters disagree on {shape:?}: frontdoor={frontdoor} clap={clap_side}"
            );
        }
    }

    /// The general invariant, weaker than equality but true for ALL argv: the front door may be
    /// STRICTER than the clap gate, never looser. A front-door refusal on an admitted shape costs
    /// at most one `rg` spawn (and for these shapes not even that -- `parse_early_ripgrep_args`
    /// rejects them first, so the request reaches clap and routes native anyway). A front-door
    /// ADMISSION on a shape the clap gate would refuse would be a correctness bug, because the
    /// front door's only action is to decline and hand off, and the clap gate then decides.
    ///
    /// These three shapes are the known, deliberate asymmetry: attached-value short spellings,
    /// where the front door refuses on the cluster letter `e` rather than reimplementing clap's
    /// attached-value argv parsing. The test asserts only what matters -- the front door refuses,
    /// and the invariant holds -- and deliberately does NOT assert the clap gate's verdict, which
    /// is not load-bearing here and would be an unverified claim dressed up as a test.
    #[test]
    fn plain_text_native_frontdoor_is_never_looser_than_the_clap_gate() {
        let corpus = tempfile::tempdir().unwrap();
        let file = corpus.path().join("a.txt");
        std::fs::write(&file, "needle alpha\n").unwrap();
        let file_arg = file.display().to_string();

        let asymmetric: Vec<Vec<&str>> = vec![
            vec!["-ie", "needle", file_arg.as_str()],
            vec!["-eneedle", file_arg.as_str()],
            vec!["-e=needle", file_arg.as_str()],
        ];

        for shape in asymmetric {
            let mut argv = vec!["tg", "search"];
            argv.extend(shape.iter().copied());

            let frontdoor = frontdoor_admits(&os_argv(&argv), false);
            assert!(!frontdoor, "front door is expected to refuse {shape:?}");

            // The safe direction: refusing here is allowed. Admitting where clap refuses is not.
            let args = parse_search_args(&argv);
            let request = resolve_search_request_with_stdin(&args, false)
                .expect("expected the shape to resolve into a search request");
            let facts = plain_text_native_request_for_search(&args, &request, false);
            let clap_side = native_can_serve_plain_text(&facts);
            assert!(
                !frontdoor || clap_side,
                "front door looser than clap on {shape:?}"
            );
        }
    }

    /// The environment clause's semantics, tested WITHOUT mutating the process environment --
    /// `env::set_var` is process-global and Rust tests run in parallel, so a test that set
    /// `RIPGREP_CONFIG_PATH` would silently flip the verdict of every concurrently-running
    /// eligibility test. The end-to-end proof (a real config file changing real results) lives in
    /// `tests/e2e/test_native_plain_text_parity.py`, where the variable is scoped to a subprocess.
    #[test]
    fn rg_config_env_is_active_requires_a_set_and_non_empty_value() {
        assert!(!rg_config_env_is_active(None));
        // `rg` IGNORES an empty value, so "set" alone must not disqualify.
        assert!(!rg_config_env_is_active(Some(OsStr::new(""))));
        assert!(rg_config_env_is_active(Some(OsStr::new("/etc/rgrc"))));
        // A DANGLING path still counts: rg emits a read-failure diagnostic the native route omits.
        assert!(rg_config_env_is_active(Some(OsStr::new(
            "/nonexistent/rgrc"
        ))));
        assert_eq!(RIPGREP_CONFIG_PATH_ENV, "RIPGREP_CONFIG_PATH");
    }

    /// Constant <-> predicate drift guard. `PLAIN_TEXT_NATIVE_ALLOWED_FLAGS` does not DRIVE
    /// `search_args_allow_plain_text_native` (that one destructures `SearchArgs`), so listing a
    /// flag in the constant while the destructure still rejects it -- or vice versa -- would be
    /// silent policy drift. This drives the real predicate with every constant entry and requires
    /// agreement, replacing an earlier test that merely restated the constant's contents.
    #[test]
    fn every_allow_listed_flag_is_actually_admitted_by_the_search_args_predicate() {
        let allowed = tensor_grep_rs::routing::PLAIN_TEXT_NATIVE_ALLOWED_FLAGS;
        assert!(!allowed.is_empty());

        for &flag in allowed {
            let args = parse_search_args(&["tg", "search", flag, "PATTERN", "file.txt"]);
            let admitted = search_args_allow_plain_text_native(&args);
            assert!(admitted, "{flag} is allow-listed but SearchArgs rejects it");
            let token_ok = plain_text_native_flag_token_is_allowed(flag);
            assert!(
                token_ok,
                "{flag} is allow-listed but the token matcher rejects it"
            );
        }

        // And the reverse direction: a flag NOT in the constant must be rejected by both.
        for flag in ["-c", "-v", "-N", "-S", "-o", "--hidden", "--json"] {
            assert!(!allowed.contains(&flag), "{flag} must not be allow-listed");
            let token_ok = plain_text_native_flag_token_is_allowed(flag);
            assert!(!token_ok, "{flag} must be rejected by the token matcher");
            let args = parse_search_args(&["tg", "search", flag, "PATTERN", "file.txt"]);
            let admitted = search_args_allow_plain_text_native(&args);
            assert!(
                !admitted,
                "{flag} must be rejected by the SearchArgs predicate"
            );
        }
    }

    /// Breadth check on the parsed-`SearchArgs` adapter: setting ANY excluded flag must refuse.
    /// (Cross-adapter equality is `plain_text_native_adapters_agree_on_every_shape`; constant
    /// drift is `every_allow_listed_flag_is_actually_admitted_by_the_search_args_predicate`.)
    #[test]
    fn search_args_plain_text_native_adapter_matches_the_allow_list() {
        let bare = parse_search_args(&["tg", "search", "PATTERN", "file.txt"]);
        assert!(search_args_allow_plain_text_native(&bare));

        for flag in ["-i", "-F", "-w", "-n", "--verbose"] {
            let args = parse_search_args(&["tg", "search", flag, "PATTERN", "file.txt"]);
            let admitted = search_args_allow_plain_text_native(&args);
            assert!(admitted, "{flag} is allow-listed and must stay eligible");
        }

        for flag in [
            "-c",
            "-v",
            "-o",
            "-N",
            "-S",
            "-a",
            "-L",
            "-l",
            "--hidden",
            "--column",
            "--vimgrep",
            "--passthru",
            "--count-matches",
            "--files-without-match",
            "--json",
            "--ndjson",
            "--cpu",
            "--index",
            "--pcre2",
            "--no-ignore",
            "--sort-files",
            "--null",
            "--multiline",
            "--unicode",
            "--messages",
            "--require-git",
            "--no-config",
        ] {
            let args = parse_search_args(&["tg", "search", flag, "PATTERN", "file.txt"]);
            let admitted = search_args_allow_plain_text_native(&args);
            assert!(!admitted, "{flag} must keep spawning rg");
        }

        for (flag, value) in [
            ("-C", "2"),
            ("-A", "2"),
            ("-B", "2"),
            ("-m", "5"),
            ("-d", "1"),
            ("-g", "*.py"),
            ("-t", "py"),
            ("-r", "hit"),
            ("--color", "always"),
            ("--sort", "path"),
            ("--format", "rg"),
            ("--max-filesize", "10M"),
            ("--path-separator", "/"),
        ] {
            let tokens = ["tg", "search", flag, value, "PATTERN", "file.txt"];
            let admitted = search_args_allow_plain_text_native(&parse_search_args(&tokens));
            assert!(!admitted, "{flag} must keep spawning rg");
        }
    }

    #[test]
    fn default_search_frontdoor_accepts_case_insensitive_and_max_count_shapes() {
        let ignore_case = ["tg", "search", "-i", "warning", "bench_data"]
            .into_iter()
            .map(OsString::from)
            .collect::<Vec<_>>();
        let max_count = ["tg", "search", "-m", "5", "ERROR", "bench_data"]
            .into_iter()
            .map(OsString::from)
            .collect::<Vec<_>>();

        let parsed_ignore_case = parse_default_search_frontdoor_args(&ignore_case)
            .expect("expected default search frontdoor case-insensitive args to parse");
        assert!(parsed_ignore_case.ignore_case);
        assert_eq!(parsed_ignore_case.patterns, vec!["warning".to_string()]);
        assert_eq!(parsed_ignore_case.paths, vec!["bench_data".to_string()]);

        let parsed_max_count = parse_default_search_frontdoor_args(&max_count)
            .expect("expected default search frontdoor max-count args to parse");
        assert_eq!(parsed_max_count.max_count, Some(5));
        assert_eq!(parsed_max_count.patterns, vec!["ERROR".to_string()]);
        assert_eq!(parsed_max_count.paths, vec!["bench_data".to_string()]);
    }

    #[test]
    fn default_search_frontdoor_accepts_equals_max_count_shape() {
        let raw_args = ["tg", "search", "--max-count=5", "ERROR", "bench_data"]
            .into_iter()
            .map(OsString::from)
            .collect::<Vec<_>>();

        let parsed = parse_default_search_frontdoor_args(&raw_args)
            .expect("expected default search frontdoor equals max-count args to parse");

        assert_eq!(parsed.max_count, Some(5));
        assert_eq!(parsed.patterns, vec!["ERROR".to_string()]);
        assert_eq!(parsed.paths, vec!["bench_data".to_string()]);
    }

    #[test]
    fn default_search_frontdoor_accepts_column_no_column_last_wins() {
        let raw_args = [
            "tg",
            "search",
            "--format",
            "rg",
            "--column",
            "--no-column",
            "-n",
            "-F",
            "ERROR",
            "bench_data",
        ]
        .into_iter()
        .map(OsString::from)
        .collect::<Vec<_>>();

        let parsed = parse_default_search_frontdoor_args(&raw_args)
            .expect("expected default search frontdoor column override args to parse");

        assert!(!parsed.column);
        assert!(parsed.no_column);
        assert!(parsed.line_number);
        assert!(parsed.fixed_strings);
        assert_eq!(parsed.patterns, vec!["ERROR".to_string()]);
        assert_eq!(parsed.paths, vec!["bench_data".to_string()]);
    }

    #[test]
    fn default_search_frontdoor_rejects_structured_and_advanced_shapes() {
        let structured = ["tg", "search", "--json", "ERROR", "bench_data"]
            .into_iter()
            .map(OsString::from)
            .collect::<Vec<_>>();

        assert!(parse_default_search_frontdoor_args(&structured).is_none());
    }

    #[test]
    fn default_search_frontdoor_rejects_positional_cli_shapes() {
        let positional = ["tg", "ERROR", "bench_data"]
            .into_iter()
            .map(OsString::from)
            .collect::<Vec<_>>();
        let positional_count = ["tg", "-c", "ERROR", "bench_data"]
            .into_iter()
            .map(OsString::from)
            .collect::<Vec<_>>();

        assert!(parse_default_search_frontdoor_args(&positional).is_none());
        assert!(parse_default_search_frontdoor_args(&positional_count).is_none());
    }

    #[test]
    fn test_search_args_parses_replace_flag() {
        use clap::Parser;
        let args = ["tg", "search", "-r", "REPLACEMENT", "PATTERN", "path"]
            .into_iter()
            .map(OsString::from)
            .collect::<Vec<_>>();
        let cli = CommandCli::try_parse_from(&args).expect("Failed to parse args");
        if let Commands::Search(search_args) = cli.command {
            assert_eq!(search_args.replace.as_deref(), Some("REPLACEMENT"));
        } else {
            panic!("Expected Search command");
        }

        let long_args = [
            "tg",
            "search",
            "--replace",
            "REPLACEMENT",
            "PATTERN",
            "path",
        ]
        .into_iter()
        .map(OsString::from)
        .collect::<Vec<_>>();
        let cli_long = CommandCli::try_parse_from(&long_args).expect("Failed to parse long args");
        if let Commands::Search(search_args) = cli_long.command {
            assert_eq!(search_args.replace.as_deref(), Some("REPLACEMENT"));
        } else {
            panic!("Expected Search command");
        }
    }

    #[test]
    fn early_positional_ripgrep_args_rejects_replace_flag() {
        let replace = ["tg", "-r", "REPLACEMENT", "PATTERN", "path"]
            .into_iter()
            .map(OsString::from)
            .collect::<Vec<_>>();
        assert!(parse_early_positional_ripgrep_args(&replace).is_none());
    }

    // -- Audit fix #1: --index capability validator (index_flag_violations) ------------------

    fn index_violations_for(tokens: &[&str]) -> Vec<&'static str> {
        let args = parse_search_args(tokens);
        let request = resolve_search_request(&args).expect("expected request to resolve");
        index_flag_violations(&args, &request)
    }

    /// Runtime backstop to the compile-time ratchet: `index_flag_violations` exhaustively
    /// destructures `SearchArgs` (no `..`), so a NEW field already fails compilation until it's
    /// added to that `match`/destructure. This test additionally guards the SEPARATE
    /// `INDEX_FLAG_POLICY` documentation table (used only by the tests below) from drifting out
    /// of sync with the real clap arg list -- e.g. someone satisfies the compiler by adding
    /// `newfield: _` to the destructure but forgets to record its classification here.
    #[test]
    fn index_flag_policy_table_is_exhaustive_over_search_args_clap_ids() {
        let command = <SearchArgs as clap::Args>::augment_args(clap::Command::new("t"));
        let clap_ids: Vec<String> = command
            .get_arguments()
            .map(|arg| arg.get_id().as_str().to_string())
            .filter(|id| id != "help")
            .collect();
        assert!(
            !clap_ids.is_empty(),
            "sanity: clap should report at least one SearchArgs argument"
        );

        let missing: Vec<&String> = clap_ids
            .iter()
            .filter(|id| {
                !INDEX_FLAG_POLICY
                    .iter()
                    .any(|(name, _)| *name == id.as_str())
            })
            .collect();
        assert!(
            missing.is_empty(),
            "SearchArgs has clap arg(s) not classified in INDEX_FLAG_POLICY for the --index \
             capability validator (index_flag_violations): {missing:?}"
        );

        let stale: Vec<&str> = INDEX_FLAG_POLICY
            .iter()
            .map(|(name, _)| *name)
            .filter(|name| !clap_ids.iter().any(|id| id == name))
            .collect();
        assert!(
            stale.is_empty(),
            "INDEX_FLAG_POLICY has stale entries no longer present on SearchArgs: {stale:?}"
        );

        let mut seen: Vec<&str> = Vec::new();
        for (name, _) in INDEX_FLAG_POLICY {
            assert!(
                !seen.contains(name),
                "INDEX_FLAG_POLICY has a duplicate entry for {name:?}"
            );
            seen.push(name);
        }
    }

    #[test]
    fn index_flag_violations_catches_flags_outside_the_original_six() {
        // None of these are in the original H1a 6-flag deny-list (invert_match/context/
        // max_count/word_regexp/globs/multi-pattern); before audit fix #1 they were silently
        // dropped by run_index_query the moment they reached it (combined with --json here so
        // they reach index_flag_violations at all -- see its doc comment on reachability).
        let cases: &[(&[&str], &str)] = &[
            (&["--hidden"], "-./--hidden"),
            (&["--max-depth", "2"], "-d/--max-depth"),
            (&["-t", "py"], "-t/--type"),
            (&["--sort", "path"], "--sort"),
            (&["--sortr", "path"], "--sortr"),
            (&["--sort-files"], "--sort-files"),
            (&["-o"], "-o/--only-matching"),
            (&["-r", "X"], "-r/--replace"),
            (&["--max-filesize", "10K"], "--max-filesize"),
            (&["--no-ignore-vcs"], "--no-ignore-vcs"),
            (&["--require-git"], "--require-git"),
            (&["-L"], "-L/--follow"),
            (&["-a"], "-a/--text"),
            (&["-l"], "-l/--files-with-matches"),
            (&["--files-without-match"], "--files-without-match"),
            (&["--column"], "--column"),
            (&["--count-matches"], "--count-matches"),
            (&["--vimgrep"], "--vimgrep"),
            (&["--passthru"], "--passthru"),
            (&["--null"], "-0/--null"),
            (&["--null-data"], "--null-data"),
            (&["-U"], "-U/--multiline"),
            (&["--multiline-dotall"], "--multiline-dotall"),
            (&["--path-separator", "/"], "--path-separator"),
            (&["--no-ignore-dot"], "--no-ignore-dot"),
            (&["--no-ignore-exclude"], "--no-ignore-exclude"),
            (&["--no-ignore-files"], "--no-ignore-files"),
            (&["--no-ignore-global"], "--no-ignore-global"),
            (&["--no-ignore-parent"], "--no-ignore-parent"),
            (&["--format", "text"], "--format"),
        ];
        for (extra, expected) in cases {
            let mut tokens = vec!["tg", "search", "--index", "--json"];
            tokens.extend_from_slice(extra);
            tokens.push("foo");
            tokens.push(".");
            let violations = index_violations_for(&tokens);
            assert!(
                violations.contains(expected),
                "flags {extra:?} should be refused (expected {expected:?}); got {violations:?}"
            );
        }
    }

    #[test]
    fn index_flag_violations_allows_passthrough_safe_bundle() {
        let tokens = [
            "tg",
            "search",
            "--index",
            "--json",
            "--no-fixed-strings",
            "--no-invert-match",
            "--ignore",
            "--no-hidden",
            "--no-column",
            "--unicode",
            "--pcre2-unicode",
            "--messages",
            "--no-config",
            "--auto-hybrid-regex",
            "--color",
            "auto",
            "foo",
            ".",
        ];
        assert_eq!(
            index_violations_for(&tokens),
            Vec::<&str>::new(),
            "PassthroughSafe flags must not be refused"
        );
    }

    #[test]
    fn index_flag_violations_color_never_and_auto_are_safe_but_always_is_refused() {
        assert!(
            index_violations_for(&["tg", "search", "--index", "--color", "never", "foo", "."])
                .is_empty()
        );
        assert!(
            index_violations_for(&["tg", "search", "--index", "--color", "auto", "foo", "."])
                .is_empty()
        );
        assert!(
            index_violations_for(&["tg", "search", "--index", "--color", "always", "foo", "."])
                .contains(&"--color"),
            "explicit --color always asks for output the index path cannot produce"
        );
    }

    #[test]
    fn index_flag_violations_refuses_contradictory_engine_selection() {
        // fold-in (c): --index combined with an explicit alternate engine is contradictory;
        // route_search currently checks explicit_index before force_cpu/explicit_gpu_device_ids,
        // so without this check the engine flag would be silently dropped, not honored.
        assert!(
            index_violations_for(&["tg", "search", "--index", "--cpu", "foo", "."])
                .contains(&"--cpu/--force-cpu")
        );
        assert!(index_violations_for(&[
            "tg",
            "search",
            "--index",
            "--gpu-device-ids",
            "0",
            "foo",
            "."
        ])
        .contains(&"--gpu-device-ids"));
    }

    #[test]
    fn index_flag_violations_honors_original_six_plus_no_line_number() {
        // The pre-existing H1a 6 must still be classified Refuse after the rewrite.
        assert!(
            index_violations_for(&["tg", "search", "--index", "-v", "foo", "."])
                .contains(&"-v/--invert-match")
        );
        assert!(
            index_violations_for(&["tg", "search", "--index", "-C", "2", "foo", "."])
                .contains(&"-C/-A/-B (context)")
        );
        assert!(
            index_violations_for(&["tg", "search", "--index", "-m", "1", "foo", "."])
                .contains(&"-m/--max-count")
        );
        assert!(
            index_violations_for(&["tg", "search", "--index", "-w", "foo", "."])
                .contains(&"-w/--word-regexp")
        );
        assert!(
            index_violations_for(&["tg", "search", "--index", "-g", "*.rs", "foo", "."])
                .contains(&"-g/--glob")
        );
        assert!(
            index_violations_for(&["tg", "search", "--index", "-e", "foo", "-e", "bar", "."])
                .contains(&"multiple patterns (-e)")
        );

        // Honor: -N/--no-line-number (and -n) must NOT be refused -- it's threaded into the
        // emit call (fold-in b) instead of being rejected.
        assert!(index_violations_for(&["tg", "search", "--index", "-N", "foo", "."]).is_empty());
        assert!(index_violations_for(&["tg", "search", "--index", "-n", "foo", "."]).is_empty());
        assert!(index_violations_for(&["tg", "search", "--index", "-S", "foo", "."]).is_empty());
        assert!(
            index_violations_for(&["tg", "search", "--index", "--no-ignore", "foo", "."])
                .is_empty()
        );
    }

    /// Creates a symlink at `link` pointing to `target`, dispatching to the platform-specific
    /// primitive (mirrors write_bytes_refuse_symlink's own unix/windows/other split). Tests
    /// call this and skip gracefully (matching the existing audit-manifest symlink tests) when
    /// the sandbox lacks symlink privilege instead of failing the whole suite.
    fn try_symlink_file(target: &Path, link: &Path) -> std::io::Result<()> {
        #[cfg(unix)]
        {
            std::os::unix::fs::symlink(target, link)
        }
        #[cfg(windows)]
        {
            std::os::windows::fs::symlink_file(target, link)
        }
        #[cfg(not(any(unix, windows)))]
        {
            let _ = (target, link);
            Err(std::io::Error::new(
                std::io::ErrorKind::Unsupported,
                "symlinks not supported on this platform",
            ))
        }
    }

    #[test]
    fn test_create_checkpoint_refuses_symlink_at_index_path() {
        // #115 Gap 2: checkpoint_index_path (checkpoint_storage_dir(root).join("index.json"))
        // is a SHARED, PREDICTABLE path across every checkpoint under a given root -- unlike
        // metadata.json (namespaced under a random checkpoint_id, unpredictable ahead of the
        // call), an attacker who can write into the checkpoint root can plant a symlink at
        // index.json in advance of any create_checkpoint call. Confirm create_checkpoint
        // refuses to follow it instead of silently overwriting whatever the symlink targets.
        let dir = tempfile::tempdir().unwrap();
        let file_path = dir.path().join("fixture.py");
        std::fs::write(&file_path, "def add(x, y): return x + y\n").unwrap();

        let outside_dir = tempfile::tempdir().unwrap();
        let outside_target = outside_dir.path().join("outside-target.json");
        // Valid (empty-array) JSON so the pre-write "does index.json already exist -> read
        // + parse it" branch succeeds and execution actually reaches the write call this
        // test targets, instead of failing earlier on a JSON-parse error.
        std::fs::write(&outside_target, b"[]").unwrap();

        // Reuse the production scope-detection so the storage dir we plant the symlink under
        // is byte-identical to the one create_checkpoint computes internally (both must agree
        // post-canonicalization, or the symlink would land at the wrong path).
        let path_str = file_path.to_str().unwrap();
        let scope = detect_checkpoint_scope(path_str);
        let storage_dir = checkpoint_storage_dir(&scope.root);
        std::fs::create_dir_all(&storage_dir).unwrap();
        let index_path = storage_dir.join("index.json");
        if let Err(err) = try_symlink_file(&outside_target, &index_path) {
            eprintln!(
                "skipping test_create_checkpoint_refuses_symlink_at_index_path: cannot create a symlink in this environment: {err}"
            );
            return;
        }

        let result = create_checkpoint(path_str);
        assert!(
            result.is_err(),
            "create_checkpoint must refuse to write the shared index through a symlink"
        );
        assert_eq!(
            std::fs::read(&outside_target).unwrap(),
            b"[]",
            "the symlink's target outside the checkpoint root must be left untouched"
        );
    }

    #[test]
    fn test_create_checkpoint_removes_orphaned_dir_on_index_parse_failure() {
        // NIT (v1.76 #602 gate, "NIT-3"): checkpoint_store.py::create_checkpoint (the Python
        // side) already wraps its copy-loop + metadata-write in `except BaseException: rmtree
        // (snapshot_dir.parent); raise` (audit #125a, shipped in #602) so a failure never
        // orphans the random-id snapshot dir -- audit #178 later widened that same Python guard
        // to also cover the index write (see checkpoint_store.py::create_checkpoint), closing
        // the one gap that earlier widening had not yet reached. The Rust `create_checkpoint`
        // above had NO equivalent at all: a failure in the copy loop, the metadata write, OR
        // the index write left the just-created checkpoint_id directory (snapshot/ +
        // metadata.json) behind forever.
        //
        // Force a deterministic, cross-platform failure at the LAST fallible step (the
        // pre-existing index.json fails to parse) so the copy loop and the metadata.json write
        // both succeed first -- proving the cleanup covers the whole write sequence, not just
        // the copy loop.
        let dir = tempfile::tempdir().unwrap();
        let file_path = dir.path().join("fixture.py");
        std::fs::write(&file_path, "def add(x, y): return x + y\n").unwrap();

        let path_str = file_path.to_str().unwrap();
        let scope = detect_checkpoint_scope(path_str);
        let storage_dir = checkpoint_storage_dir(&scope.root);
        std::fs::create_dir_all(&storage_dir).unwrap();
        let index_path = storage_dir.join("index.json");
        std::fs::write(&index_path, b"not valid json").unwrap();

        let result = create_checkpoint(path_str);
        assert!(
            result.is_err(),
            "a corrupt pre-existing index.json must fail create_checkpoint"
        );

        let remaining: Vec<std::ffi::OsString> = std::fs::read_dir(&storage_dir)
            .unwrap()
            .filter_map(|entry| entry.ok())
            .map(|entry| entry.file_name())
            .collect();
        assert_eq!(
            remaining,
            vec![std::ffi::OsString::from("index.json")],
            "a failed checkpoint must not leave an orphaned per-checkpoint directory behind: {remaining:?}"
        );
        assert_eq!(
            std::fs::read(&index_path).unwrap(),
            b"not valid json",
            "the pre-existing (corrupt) index.json itself must be left untouched"
        );
    }

    #[test]
    fn test_create_checkpoint_does_not_disclose_symlink_target() {
        // Item 1 (audit #178, surfaced by the #610 gate): `std::fs::copy` FOLLOWS symlinks, so
        // a source-tree symlink pointing OUTSIDE the checkpoint root previously had its
        // TARGET's content copied into the snapshot -- an out-of-root disclosure. Mirrors the
        // Python-side regression test `test_create_checkpoint_does_not_disclose_symlink_target`
        // in tests/unit/test_checkpoint_containment.py (checkpoint_store.py:853-855).
        let repo_parent = tempfile::tempdir().unwrap();
        let repo_path = repo_parent.path().join("repo");
        std::fs::create_dir_all(&repo_path).unwrap();
        std::fs::write(repo_path.join("real.py"), b"in-repo content\n").unwrap();

        let outside_dir = tempfile::tempdir().unwrap();
        let secret = outside_dir.path().join("secret.txt");
        std::fs::write(&secret, b"SECRET-OUT-OF-ROOT\n").unwrap();

        let link_path = repo_path.join("link.txt");
        if let Err(err) = try_symlink_file(&secret, &link_path) {
            eprintln!(
                "skipping test_create_checkpoint_does_not_disclose_symlink_target: cannot create a symlink in this environment: {err}"
            );
            return;
        }

        let path_str = repo_path.to_str().unwrap();
        let result = create_checkpoint(path_str).expect("checkpoint creation must still succeed");

        let scope = detect_checkpoint_scope(path_str);
        let snapshot_dir = checkpoint_snapshot_dir(&scope.root, &result.checkpoint_id);
        let snapshotted_link = snapshot_dir.join("link.txt");

        // The snapshot entry must itself be a symlink -- never a regular file holding the
        // target's bytes -- proving the target's content was never read/copied.
        let snapshotted_type = std::fs::symlink_metadata(&snapshotted_link)
            .expect("snapshotted symlink entry must exist")
            .file_type();
        assert!(
            snapshotted_type.is_symlink(),
            "checkpoint snapshot must store the symlink itself, not its resolved target content"
        );

        // Belt-and-suspenders: no regular file anywhere under the snapshot may contain the
        // out-of-root secret's content.
        for entry in walkdir::WalkDir::new(&snapshot_dir) {
            let entry = entry.unwrap();
            if entry.file_type().is_file() {
                let text = std::fs::read_to_string(entry.path()).unwrap_or_default();
                assert!(
                    !text.contains("SECRET-OUT-OF-ROOT"),
                    "checkpoint snapshot must not disclose the out-of-root secret's content: {}",
                    entry.path().display()
                );
            }
        }
    }

    #[test]
    fn test_create_checkpoint_still_copies_regular_files_byte_identical() {
        // Regression guard for the copy_checkpoint_entry symlink fix above: an ordinary
        // (non-symlink) source tree must still be captured with byte-identical content, and a
        // normal (no-symlink) checkpoint create must be entirely unaffected by the new
        // is_symlink() branch.
        let dir = tempfile::tempdir().unwrap();
        let repo_path = dir.path().join("repo");
        std::fs::create_dir_all(repo_path.join("pkg")).unwrap();
        std::fs::write(repo_path.join("a.py"), b"alpha\n").unwrap();
        std::fs::write(repo_path.join("pkg").join("b.py"), b"beta\n").unwrap();

        let path_str = repo_path.to_str().unwrap();
        let result = create_checkpoint(path_str).expect("checkpoint creation must succeed");
        assert_eq!(result.file_count, 2);

        let scope = detect_checkpoint_scope(path_str);
        let snapshot_dir = checkpoint_snapshot_dir(&scope.root, &result.checkpoint_id);
        assert_eq!(
            std::fs::read(snapshot_dir.join("a.py")).unwrap(),
            b"alpha\n"
        );
        assert_eq!(
            std::fs::read(snapshot_dir.join("pkg").join("b.py")).unwrap(),
            b"beta\n"
        );
    }

    #[test]
    fn test_restore_validation_rollback_snapshots_refuses_symlink_at_file_path() {
        // #115 Gap 3: between "apply the edit" and a failed-validation rollback, an edited
        // file could be swapped for a symlink pointing outside the project (e.g. via a
        // hostile --lint-cmd/--test-cmd running between apply and rollback).
        // restore_validation_rollback_snapshots must refuse to follow it instead of writing
        // the pre-edit snapshot bytes through the symlink.
        let dir = tempfile::tempdir().unwrap();
        let file_path = dir.path().join("fixture.py");
        let original_bytes = b"def add(x, y): return x + y\n".to_vec();
        std::fs::write(&file_path, &original_bytes).unwrap();

        let outside_dir = tempfile::tempdir().unwrap();
        let outside_target = outside_dir.path().join("outside-target.py");
        std::fs::write(&outside_target, b"UNTOUCHED").unwrap();

        let mut snapshots = BTreeMap::new();
        snapshots.insert(file_path.to_string_lossy().to_string(), original_bytes);

        // Simulate the swap: the tracked file is replaced by a symlink after the pre-apply
        // snapshot was captured.
        std::fs::remove_file(&file_path).unwrap();
        if let Err(err) = try_symlink_file(&outside_target, &file_path) {
            eprintln!(
                "skipping test_restore_validation_rollback_snapshots_refuses_symlink_at_file_path: cannot create a symlink in this environment: {err}"
            );
            return;
        }

        let summary = restore_validation_rollback_snapshots(&snapshots);

        assert!(
            !summary.success,
            "restore must report failure when a snapshot target is a symlink"
        );
        assert!(
            summary.files_restored.is_empty(),
            "a refused symlink write must not be counted as restored"
        );
        assert_eq!(summary.errors.len(), 1);
        assert_eq!(
            std::fs::read(&outside_target).unwrap(),
            b"UNTOUCHED",
            "the symlink's target outside the rollback root must be left untouched"
        );
    }

    /// Task 276 task 6: the DISCLOSURE and the EXIT CODE must read one predicate.
    ///
    /// `emit_multi_pattern_native_results` exits 2 on `walk_was_incomplete`; the `--json` and
    /// `--ndjson` envelopes stamp `result_incomplete` from `incomplete_envelope_fields`. Before
    /// task 6 the exit code was not derived from the count at all, which is how that route came
    /// to print `result_incomplete: true` and then exit 0 -- or, on a zero-match incomplete
    /// scan, exit 1, which reads as an authoritative "no matches exist".
    ///
    /// This asserts the two cannot disagree for ANY input. It fails if a later change
    /// re-derives either side independently, which is the specific way this family recurs.
    #[test]
    fn walk_incompleteness_predicate_agrees_with_the_envelope_it_stamps() {
        for incomplete_paths in [None, Some(0), Some(1), Some(2), Some(usize::MAX)] {
            let (result_incomplete, reason_class, count) =
                incomplete_envelope_fields(incomplete_paths);
            let exits_two = walk_was_incomplete(incomplete_paths);

            assert_eq!(
                result_incomplete == Some(true),
                exits_two,
                "envelope and exit code disagree for {incomplete_paths:?}: \
                 result_incomplete={result_incomplete:?}, walk_was_incomplete={exits_two}"
            );
            if exits_two {
                assert_eq!(reason_class, Some("unreadable_path"));
                assert_eq!(count, incomplete_paths);
            } else {
                assert_eq!(reason_class, None);
                assert_eq!(count, None);
            }
        }

        // CONTROL ARM. Both not-incomplete inputs are in the table above, so the loop cannot
        // pass by only ever exercising the positive case -- and a guard that answered `true`
        // unconditionally would satisfy the agreement assertion while exiting 2 on every clean
        // search. `None` means "this route observed no walk of its own"; `Some(0)` means "a walk
        // ran and hit no errors". Neither is an incompleteness claim.
        assert!(!walk_was_incomplete(None));
        assert!(!walk_was_incomplete(Some(0)));
        assert!(walk_was_incomplete(Some(1)));
    }
}

fn run_command_cli(cli: CommandCli) -> anyhow::Result<()> {
    match cli.command {
        Commands::Search(args) => handle_ripgrep_search(args),
        Commands::Calibrate(args) => handle_calibrate_command(args),
        Commands::Upgrade => handle_python_passthrough("upgrade", vec![]),
        Commands::AuditVerify(args) => handle_audit_verify_command(args),
        Commands::Audit { args } => handle_python_passthrough("audit", args),
        Commands::Mcp => handle_python_passthrough("mcp", vec![]),
        Commands::Classify(args) => handle_classify_command(args),
        Commands::Run(args) => handle_ast_run(args),
        Commands::Scan { args } => {
            if ast_scan_requires_python_passthrough(&args) {
                return handle_python_passthrough("scan", args);
            }

            use tensor_grep_rs::backend_ast_workflow::{handle_ast_scan, SessionRequest};
            let config_path =
                if !args.is_empty() && (args[0] == "--config" || args[0] == "-c") && args.len() > 1
                {
                    Some(args[1].clone())
                } else {
                    None
                };

            if let Some(exit_code) = try_resident_execution(SessionRequest::Scan {
                config_path: config_path.clone(),
            })? {
                std::process::exit(exit_code);
            }
            handle_ast_scan(config_path.as_deref())
        }
        Commands::Test { args } => {
            if ast_test_requires_python_passthrough(&args) {
                return handle_python_passthrough("test", args);
            }

            use tensor_grep_rs::backend_ast_workflow::{handle_ast_test, SessionRequest};
            let config_path =
                if !args.is_empty() && (args[0] == "--config" || args[0] == "-c") && args.len() > 1
                {
                    Some(args[1].clone())
                } else {
                    None
                };

            if let Some(exit_code) = try_resident_execution(SessionRequest::Test {
                config_path: config_path.clone(),
            })? {
                std::process::exit(exit_code);
            }
            handle_ast_test(config_path.as_deref())
        }
        Commands::New { args } => {
            use tensor_grep_rs::backend_ast_workflow::handle_ast_new;
            if ast_new_requires_python_passthrough(&args) {
                return handle_python_passthrough("new", args);
            }
            handle_ast_new(args)
        }
        Commands::Worker { port, stop } => {
            use tensor_grep_rs::backend_ast_workflow::{handle_ast_worker_tcp, SessionRequest};
            if stop {
                match try_resident_execution(SessionRequest::Stop)? {
                    Some(0) => println!("Stopped resident worker."),
                    _ => println!("No resident worker found or failed to stop."),
                }
                Ok(())
            } else {
                handle_ast_worker_tcp(port)
            }
        }
        Commands::Lsp { args } => handle_python_passthrough("lsp", args),
        Commands::LspSetup { args } => handle_python_passthrough("lsp-setup", args),
        #[cfg(feature = "cuda")]
        Commands::GpuNativeStats(args) => handle_gpu_native_stats_command(args),
        #[cfg(feature = "cuda")]
        Commands::GpuTransferBench(args) => handle_gpu_transfer_benchmark_command(args),
        #[cfg(feature = "cuda")]
        Commands::GpuCudaGraphs(args) => handle_gpu_cuda_graph_benchmark_command(args),
        #[cfg(feature = "cuda")]
        Commands::GpuOomProbe(args) => handle_gpu_oom_probe_command(args),
        Commands::Map { args } => handle_python_passthrough("map", args),
        Commands::Orient { args } => handle_python_passthrough("orient", args),
        Commands::Codemap { args } => handle_python_passthrough("codemap", args),
        Commands::Inventory { args } => handle_python_passthrough("inventory", args),
        Commands::DocsCoverage { args } => handle_python_passthrough("docs-coverage", args),
        Commands::Session { args } => handle_python_passthrough("session", args),
        Commands::Doctor { args } => handle_python_passthrough("doctor", args),
        Commands::RepairLauncher { args } => handle_python_passthrough("repair-launcher", args),
        Commands::Checkpoint { args } => handle_python_passthrough("checkpoint", args),
        Commands::Dogfood { args } => handle_python_passthrough("dogfood", args),
        Commands::Defs { args } => handle_python_passthrough("defs", args),
        Commands::Refs { args } => handle_python_passthrough("refs", args),
        Commands::Source { args } => handle_python_passthrough("source", args),
        Commands::Impact { args } => handle_python_passthrough("impact", args),
        Commands::Callers { args } => handle_python_passthrough("callers", args),
        Commands::Imports { args } => handle_python_passthrough("imports", args),
        Commands::Importers { args } => handle_python_passthrough("importers", args),
        Commands::Find { args } => handle_python_passthrough("find", args),
        Commands::BlastRadius { args } => handle_python_passthrough("blast-radius", args),
        Commands::BlastRadiusRender { args } => {
            handle_python_passthrough("blast-radius-render", args)
        }
        Commands::BlastRadiusPlan { args } => handle_python_passthrough("blast-radius-plan", args),
        Commands::DiffImpact { args } => handle_python_passthrough("diff-impact", args),
        Commands::EditPlan { args } => handle_python_passthrough("edit-plan", args),
        Commands::Agent { args } => handle_python_passthrough("agent", args),
        Commands::ContextRender { args } => handle_python_passthrough("context-render", args),
        Commands::AstInfo { args } => handle_python_passthrough("ast-info", args),
        Commands::Rulesets { args } => handle_python_passthrough("rulesets", args),
        Commands::AuditHistory { args } => handle_python_passthrough("audit-history", args),
        Commands::AuditDiff { args } => handle_python_passthrough("audit-diff", args),
        Commands::ReviewBundle { args } => handle_python_passthrough("review-bundle", args),
        Commands::Evidence { args } => handle_python_passthrough("evidence", args),
        Commands::Ledger { args } => handle_python_passthrough("ledger", args),
        Commands::Devices { args } => handle_python_passthrough("devices", args),
        Commands::Context { args } => handle_python_passthrough("context", args),
        Commands::RouteTest { args } => handle_python_passthrough("route-test", args),
        Commands::InstallDense { args } => handle_python_passthrough("install-dense", args),
        Commands::Install { args } => handle_python_passthrough("install", args),
        Commands::Uninstall { args } => handle_python_passthrough("uninstall", args),
        Commands::Prepare { args } => handle_python_passthrough("prepare", args),
        Commands::PythonPassthrough(args) => {
            let command = args[0].clone();
            let command_args = args[1..].to_vec();
            handle_python_passthrough(&command, command_args)
        }
    }
}

fn try_resident_execution(
    req: tensor_grep_rs::backend_ast_workflow::SessionRequest,
) -> anyhow::Result<Option<i32>> {
    use std::io::{BufRead, BufReader, Read, Write};
    use std::net::TcpStream;

    // Check if worker is requested or if we are stopping it
    let is_stop = matches!(
        req,
        tensor_grep_rs::backend_ast_workflow::SessionRequest::Stop
    );
    if !is_stop && std::env::var("TG_RESIDENT_AST").unwrap_or_default() != "1" {
        return Ok(None);
    }

    // Try to find the port
    let port_file = std::env::current_dir()?
        .join(".tg_cache")
        .join("ast")
        .join("worker_port.txt");
    if !port_file.exists() {
        return Ok(None);
    }

    let port_str = std::fs::read_to_string(&port_file)?;
    let port: u16 = port_str.trim().parse()?;

    // Connect
    let mut stream = match TcpStream::connect(format!("127.0.0.1:{}", port)) {
        Ok(s) => s,
        Err(_) => return Ok(None),
    };

    // Send request
    let req_json = serde_json::to_string(&req)?;
    stream.write_all(req_json.as_bytes())?;
    stream.flush()?;

    // Read response header
    let mut reader = BufReader::new(stream);
    let mut line = String::new();
    reader.read_line(&mut line)?;

    use tensor_grep_rs::backend_ast_workflow::SessionResponse;
    let resp: SessionResponse = match serde_json::from_str(&line) {
        Ok(r) => r,
        Err(_) => return Ok(None), // Protocol mismatch, fallback to cold
    };

    if !resp.success && resp.error.is_some() {
        if let Some(err) = resp.error {
            eprintln!("Worker error: {}", err);
        }
        return Ok(None); // Fallback to cold path for infrastructure/project errors
    }

    // Stream the rest of the output
    loop {
        let mut buf = [0; 4096];
        let n = reader.read(&mut buf)?;
        if n == 0 {
            break;
        }
        std::io::stdout().write_all(&buf[..n])?;
    }
    std::io::stdout().flush()?;

    if !resp.success {
        Ok(Some(1))
    } else {
        Ok(Some(0))
    }
}

fn ast_scan_requires_python_passthrough(args: &[String]) -> bool {
    let mut index = 0usize;
    while index < args.len() {
        match args[index].as_str() {
            "--config" | "-c" => index += 2,
            arg if arg.starts_with("--config=") => index += 1,
            _ => return true,
        }
    }
    false
}

fn ast_new_requires_python_passthrough(args: &[String]) -> bool {
    args.iter()
        .any(|arg| arg == "--config" || arg == "-c" || arg.starts_with("--config="))
}

fn ast_test_requires_python_passthrough(args: &[String]) -> bool {
    let mut index = 0usize;
    while index < args.len() {
        match args[index].as_str() {
            "--config" | "-c" => index += 2,
            arg if arg.starts_with("--config=") => index += 1,
            _ => return true,
        }
    }
    false
}

fn handle_calibrate_command(args: CalibrateArgs) -> anyhow::Result<()> {
    let executable = env::current_exe().context("failed to resolve current tg executable")?;
    match run_crossover_calibration(&executable) {
        Ok(config) => {
            write_crossover_config(&config, None)?;
            // --json is a no-op on the success path: the config was already JSON before this
            // flag existed, so there is nothing additive to do here.
            println!("{}", serde_json::to_string_pretty(&config)?);
            Ok(())
        }
        Err(err) => {
            // Fail closed (exit 2) either way -- see the module-level comment above
            // `require_ripgrep_or_exit`. `--json` only changes WHAT is printed for the one
            // error kind a harness needs to tell apart from a genuine failure: this build
            // structurally cannot run GPU calibration (no CUDA compiled in). Any other error
            // (a bad device id on a cuda-enabled build, I/O, a failed benchmark subprocess,
            // ...) is a real failure, not a skip, so it must NOT be relabeled -- it keeps the
            // original human-readable eprintln even under --json (fail-closed honesty, no
            // mislabeling a genuine failure as "skipped").
            if args.json {
                if let Some(no_cuda) = err.downcast_ref::<NoCudaBuildError>() {
                    let payload = skip_signal_payload(no_cuda);
                    println!("{payload}");
                    std::process::exit(2);
                }
            }
            eprintln!("{err}");
            std::process::exit(2);
        }
    }
}

fn handle_audit_verify_command(args: AuditVerifyArgs) -> anyhow::Result<()> {
    let payload = verify_audit_manifest_payload(&args)?;
    if args.json {
        println!("{}", serde_json::to_string_pretty(&payload)?);
    } else {
        println!("Manifest: {}", payload.manifest_path);
        println!("valid={}", payload.valid);
        println!(
            "checks=digest:{} chain:{} signature:{}",
            payload.checks.digest_valid, payload.checks.chain_valid, payload.checks.signature_valid
        );
        for error in &payload.errors {
            println!("- {error}");
        }
    }
    if payload.valid {
        Ok(())
    } else {
        anyhow::bail!("audit manifest verification failed")
    }
}

fn run_positional_cli(cli: PositionalCli) -> anyhow::Result<()> {
    if cli.pattern.is_none() {
        use clap::CommandFactory;
        let mut cmd = PositionalCli::command();
        cmd.print_help()?;
        return Ok(());
    }

    let pattern = cli.pattern.clone().unwrap();
    let paths = implicit_search_paths(&cli.path, stdin_should_search_implicit_path());
    exit_json_search_input_error_if_needed(
        cli.json,
        cli.ndjson,
        std::slice::from_ref(&pattern),
        &paths,
    );
    let primary_path = paths.first().map(String::as_str).unwrap_or(".");

    let rg_available = ripgrep_is_available();
    if cli.pcre2 {
        require_ripgrep_or_exit(rg_available, "--pcre2");
    }
    // P5·H2 (audit Finding 2 hoist): refuse the positional native count/files combos HERE, before
    // `count_search_corpus_bytes` walks the whole tree on CUDA builds. `PositionalCli` has no
    // `index` field, so there is no index-path refusal message to preserve -- a single,
    // unconditional choke point covering both the `--json`/`--ndjson` doors and the `--gpu-device-ids`
    // door (the in-arm calls in the NativeGpu/NativeCpu arms below were the original landing
    // spots; removed as redundant). The bare positional `--count-matches` (no json/ndjson/gpu)
    // routes to the Ripgrep arm and is NOT this gate's class (see
    // `positional_native_dropped_search_flags`).
    if let Some(dropped) = validate_positional_native_structured_refusals(&cli) {
        exit_native_structured_flag_dropped(&dropped, cli.json || cli.ndjson);
    }
    #[cfg_attr(not(feature = "cuda"), allow(unused_variables))]
    let structured_output = cli.json || cli.ndjson;
    let explicit_gpu = !cli.gpu_device_ids.is_empty();
    let auto_gpu_ids: [i32; 0] = [];
    if paths.len() != 1 && explicit_gpu {
        anyhow::bail!("GPU search currently supports exactly one path root");
    }

    #[cfg(feature = "cuda")]
    let (corpus_bytes, corpus_bytes_known) = match count_search_corpus_bytes(
        &paths.iter().map(PathBuf::from).collect::<Vec<_>>(),
        true,
        &[],
    ) {
        Ok(bytes) => (bytes, true),
        Err(err) => {
            eprintln!("warning: corpus size probe failed: {err}");
            (0, false)
        }
    };
    #[cfg(not(feature = "cuda"))]
    let (corpus_bytes, corpus_bytes_known) = (0u64, false);

    #[cfg(feature = "cuda")]
    let gpu_auto_supported = paths.len() == 1
        && gpu_native_fallback_reason(&GpuSearchParams {
            patterns: std::slice::from_ref(&pattern),
            query: &pattern,
            path: primary_path,
            line_number: cli.line_number && !cli.no_line_number,
            ignore_case: cli.ignore_case,
            smart_case: false,
            fixed_strings: cli.fixed_strings,
            invert_match: cli.invert_match,
            count: cli.count,
            context: None,
            max_count: cli.max_count,
            word_regexp: cli.word_regexp,
            globs: Vec::new(),
            hidden: false,
            max_depth: None,
            text: false,
            no_ignore: true,
            gpu_device_ids: &auto_gpu_ids,
            json: cli.json,
            ndjson: cli.ndjson,
            verbose: cli.verbose,
            replace: cli.replace.clone(),
            only_matching: cli.only_matching,
            max_filesize: cli.max_filesize.clone(),
            color: cli.color.clone(),
            no_ignore_vcs: cli.no_ignore_vcs,
            path_was_implicit: cli.path.is_empty(),
        })
        .is_none();

    #[cfg(not(feature = "cuda"))]
    let gpu_auto_supported = false;

    #[cfg(feature = "cuda")]
    let calibration = load_search_routing_calibration(Path::new(primary_path));
    #[cfg(not(feature = "cuda"))]
    let calibration: Option<SearchRoutingCalibration> = None;

    #[cfg(feature = "cuda")]
    let gpu_available = auto_gpu_available_for_routing();
    #[cfg(not(feature = "cuda"))]
    let gpu_available = false;

    let decision = route_search(
        &SearchRoutingConfig {
            explicit_index: false,
            explicit_gpu_device_ids: explicit_gpu,
            force_cpu: cli.force_cpu,
            ast_command: false,
            json: cli.json,
            ndjson: cli.ndjson,
            rg_available,
            corpus_bytes,
            corpus_bytes_known,
            gpu_auto_supported,
            prefer_rg_passthrough: false,
            pcre2: cli.pcre2,
            // The bare positional CLI (`tg PATTERN PATH` without the `search` subcommand) is a
            // separate front door with its own `PositionalCli` flag surface; this PR deliberately
            // does not change its routing. Explicitly false rather than derived, so the positional
            // path keeps today's behavior byte-for-byte.
            native_plain_text: false,
        },
        calibration.as_ref(),
        IndexRoutingState::default(),
        gpu_available,
    );

    match decision.selection {
        BackendSelection::NativeGpu => {
            let gpu_device_ids = if explicit_gpu {
                cli.gpu_device_ids.as_slice()
            } else {
                &auto_gpu_ids
            };
            let params = GpuSearchParams {
                patterns: std::slice::from_ref(&pattern),
                query: &pattern,
                path: primary_path,
                // N2 (task #131 F3): deliberately derive line_number the same way as the sibling
                // `native_search_config_for_positional` (its own `cli.line_number &&
                // !cli.no_line_number`), replacing the old hardcoded `line_number: true`. Since
                // `PositionalCli::line_number` is `#[arg(short='n')]` (default false), the positional
                // GPU path now defaults line numbers OFF, matching the native CPU fallback it
                // delegates to -- a user-visible alignment fix, locked by
                // `positional_gpu_path_defaults_line_number_off_aligned_with_native`.
                line_number: cli.line_number && !cli.no_line_number,
                ignore_case: cli.ignore_case,
                smart_case: false,
                fixed_strings: cli.fixed_strings,
                invert_match: cli.invert_match,
                count: cli.count,
                context: None,
                max_count: cli.max_count,
                word_regexp: cli.word_regexp,
                globs: Vec::new(),
                hidden: false,
                max_depth: None,
                text: false,
                no_ignore: true,
                gpu_device_ids,
                json: cli.json,
                ndjson: cli.ndjson,
                verbose: cli.verbose,
                replace: cli.replace.clone(),
                only_matching: cli.only_matching,
                max_filesize: cli.max_filesize.clone(),
                color: cli.color.clone(),
                no_ignore_vcs: cli.no_ignore_vcs,
                path_was_implicit: cli.path.is_empty(),
            };

            #[cfg(feature = "cuda")]
            if decision.reason == RoutingDecision::native_gpu_auto().reason {
                let fallback_decision =
                    RoutingDecision::native_cpu_gpu_fallback(rg_available, structured_output);
                let rg_fallback = fallback_decision
                    .allow_rg_fallback
                    .then(|| positional_ripgrep_args(&cli, &pattern, &paths));
                return handle_auto_gpu_search(
                    params,
                    native_search_config_for_positional(&cli, &pattern, &paths, fallback_decision),
                    rg_fallback,
                );
            }

            handle_gpu_search(params)
        }
        BackendSelection::NativeCpu => {
            if decision.reason
                == RoutingDecision::native_cpu_gpu_fallback(rg_available, structured_output).reason
            {
                eprintln!(
                    "warning: CUDA is unavailable: no usable GPU devices were found; falling back to native CPU search; this CPU fallback output is not GPU acceleration proof"
                );
            }
            if cli.verbose {
                emit_verbose_metadata(decision);
            }

            let rg_fallback = decision
                .allow_rg_fallback
                .then(|| positional_ripgrep_args(&cli, &pattern, &paths));

            run_native_search_with_optional_rg_fallback(
                native_search_config_for_positional(&cli, &pattern, &paths, decision),
                rg_fallback,
            )
        }
        BackendSelection::Ripgrep => {
            if cli.verbose {
                emit_verbose_metadata(decision);
            }

            let exit_code =
                execute_ripgrep_search(&positional_ripgrep_args(&cli, &pattern, &paths))?;
            if exit_code != 0 {
                std::process::exit(exit_code.max(1));
            }
            Ok(())
        }
        _ => anyhow::bail!(
            "unsupported positional routing decision: {}",
            decision.reason
        ),
    }
}

fn should_use_positional_cli(raw_args: &[OsString]) -> bool {
    for arg in raw_args.iter().skip(1) {
        let token = arg.to_string_lossy();
        if token == "--help" || token == "-h" || token == "--version" || token == "-V" {
            return false;
        }
        if token.starts_with('-') {
            continue;
        }
        return !is_known_python_command(&token);
    }

    false
}

/// A90: unknown-command-shaped refusal on the NATIVE door. Mirrors the Python
/// `_top_level_command_refusal` contract exactly (parity-pinned):
/// - first arg is a RESERVED (roadmap, not-yet-registered) name AND any later token starts with
///   '-'  -> refusal; or
/// - first arg is unknown (not known, not reserved) AND any later token is `--help`/`-h`
///   (a nonexistent command has no help) -> refusal.
///
/// Everything else (bare patterns, pattern+path, unreserved pattern+flag) stays search.
fn top_level_unknown_command_refusal(raw_args: &[OsString]) -> bool {
    let Some(first) = raw_args.get(1).map(|a| a.to_string_lossy()) else {
        return false;
    };
    if first == "search" || is_known_python_command(&first) || first.starts_with('-') {
        return false;
    }
    let rest: Vec<String> = raw_args
        .iter()
        .skip(2)
        .map(|a| a.to_string_lossy().into_owned())
        .collect();
    if rest.is_empty() {
        return false;
    }
    let has_flag = rest.iter().any(|t| t.starts_with('-'));
    if !has_flag {
        return false;
    }
    let is_reserved = is_reserved_python_command(&first);
    let has_help = rest.iter().any(|t| t == "--help" || t == "-h");
    is_reserved || has_help
}

fn is_known_python_command(token: &str) -> bool {
    python_set_members("KNOWN_COMMANDS")
        .iter()
        .any(|name| name == token)
}

/// A90: is `token` in the RESERVED (roadmap, not-yet-registered) top-level command set?
/// Parsed from the RESERVED_TOP_LEVEL_COMMANDS block of commands.py — SCOPED to that block,
/// never a bare quoted-literal scan (the unscoped `include_str!` match made every quoted
/// literal line look "known", which would have made reserved names pass the NOT-known gate).
fn is_reserved_python_command(token: &str) -> bool {
    python_set_members("RESERVED_TOP_LEVEL_COMMANDS")
        .iter()
        .any(|name| name == token)
}

/// Extract the members of one top-level set literal block from commands.py by its variable
/// name (`KNOWN_COMMANDS = { ... }` / `RESERVED_TOP_LEVEL_COMMANDS = { ... }`). Bracedepth
/// aware, quote/escape aware (a `#` inside a string is NOT a comment; braces inside strings do
/// not count), and trailing-comma tolerant — `"agent",` is a member named `agent`. Returns the
/// parsed string literals in block order, or an empty Vec if the block is absent.
fn python_set_members(set_name: &str) -> Vec<String> {
    const RAW_PY: &str = include_str!("../../src/tensor_grep/cli/commands.py");
    let needle_open = format!("{set_name} = {{");
    let mut members: Vec<String> = Vec::new();
    let mut depth: i32 = 0;
    let mut in_block = false;
    for line in RAW_PY.lines() {
        let t = line.trim();
        if !in_block {
            if t.starts_with(&needle_open) {
                in_block = true;
                depth += 1;
            }
            continue;
        }
        // Inside the target block: tokenize char-by-char with a small quote-aware scanner so a
        // '#' inside a string is data, not a comment, and braces inside strings are data too.
        let mut in_str = false;
        let mut in_str_esc = false;
        let mut current: String = String::new();
        let mut collected: Vec<String> = Vec::new();
        for c in t.chars() {
            if in_str {
                if in_str_esc {
                    // Decode the two escapes that can appear INSIDE a double-quoted Python
                    // string literal between the outer quotes: `\"` -> `"` and `\\` -> `\`.
                    // Any other escape is copied verbatim (commands.py set members are plain
                    // identifiers today; this only guards future edits — Python would decode
                    // e.g. `\n` to a newline, which cannot occur in a command name).
                    match c {
                        '"' => current.push('"'),
                        '\\' => current.push('\\'),
                        other => {
                            current.push('\\');
                            current.push(other);
                        }
                    }
                    in_str_esc = false;
                } else if c == '\\' {
                    in_str_esc = true;
                } else if c == '"' {
                    if depth >= 1 {
                        collected.push(current.clone());
                    }
                    current.clear();
                    in_str = false;
                } else {
                    current.push(c);
                }
                continue;
            }
            match c {
                '"' => {
                    in_str = true;
                    current.clear();
                }
                '#' => break, // comment to end of line
                '{' => depth += 1,
                '}' => {
                    depth -= 1;
                    if depth == 0 {
                        in_block = false;
                    }
                }
                _ => {}
            }
        }
        if !collected.is_empty() {
            members.extend(collected);
        }
        if !in_block {
            break;
        }
    }
    members
}

/// A90 nearest[]: normalized, max edit distance 3, excludes internal `__` names, cap 5, stable
/// (alphabetical) order, empty when nothing is close. Mirrors `_nearest_commands`. Uses the
/// same scoped member extractor as membership so nearest and known can never disagree.
fn nearest_commands(token: &str) -> Vec<String> {
    let candidates: Vec<String> = python_set_members("KNOWN_COMMANDS")
        .into_iter()
        .filter(|name| !name.starts_with("__"))
        .collect();
    let norm = token.to_lowercase();
    let mut matches: Vec<String> = Vec::new();
    for name in candidates {
        // Genuine Levenshtein edit distance — the honest "max distance 3" bound. Length-diff
        // alone is not edit distance (a 1-char substitution changes nothing about length); the
        // plan/council contract and the Python sibling both mean edit distance, so enforce it.
        if levenshtein_distance(&norm, &name) <= 3 {
            matches.push(name);
        }
    }
    matches.sort();
    matches.truncate(5);
    matches
}

/// Classic Wagner–Fischer Levenshtein distance over chars. Deterministic, bounded — the
/// command names and the input token are short, so the O(n*m) DP is trivial here.
fn levenshtein_distance(a: &str, b: &str) -> usize {
    let aa: Vec<char> = a.chars().collect();
    let bb: Vec<char> = b.chars().collect();
    if aa.is_empty() {
        return bb.len();
    }
    if bb.is_empty() {
        return aa.len();
    }
    let mut prev: Vec<usize> = (0..=bb.len()).collect();
    let mut curr: Vec<usize> = vec![0; bb.len() + 1];
    for (i, ca) in aa.iter().enumerate() {
        curr[0] = i + 1;
        for (j, cb) in bb.iter().enumerate() {
            let cost = if ca == cb { 0 } else { 1 };
            curr[j + 1] = (prev[j + 1] + 1).min(curr[j] + 1).min(prev[j] + cost);
        }
        std::mem::swap(&mut prev, &mut curr);
    }
    prev[bb.len()]
}

fn stdin_should_search_implicit_path() -> bool {
    grep_cli::is_readable_stdin()
}

fn implicit_search_paths(
    explicit_paths: &[String],
    stdin_searches_implicit_path: bool,
) -> Vec<String> {
    if !explicit_paths.is_empty() {
        return explicit_paths.to_vec();
    }
    if stdin_searches_implicit_path {
        Vec::new()
    } else {
        vec![".".to_string()]
    }
}

fn emit_search_error_json(error: &str, detail: &str) {
    println!(
        "{}",
        serde_json::json!({
            "version": JSON_OUTPUT_VERSION,
            "ok": false,
            "error": error,
            "detail": detail,
        })
    );
}

fn exit_search_error_json(error: &str, detail: impl Into<String>) -> ! {
    emit_search_error_json(error, &detail.into());
    std::process::exit(2);
}

fn exit_structured_search_error_if_needed(
    json: bool,
    ndjson: bool,
    error: &str,
    detail: impl Into<String>,
) -> ! {
    let detail = detail.into();
    if json && !ndjson {
        exit_search_error_json(error, detail);
    }
    if ndjson {
        println!(
            "{}",
            serde_json::json!({
                "version": JSON_OUTPUT_VERSION,
                "type": "error",
                "error": error,
                "detail": detail,
            })
        );
        std::process::exit(2);
    }
    eprintln!("{detail}");
    std::process::exit(2);
}

fn first_missing_search_path(paths: &[String]) -> Option<String> {
    paths
        .iter()
        .find(|path| path.as_str() != "-" && !Path::new(path).exists())
        .cloned()
}

fn exit_json_search_input_error_if_needed(
    json: bool,
    ndjson: bool,
    patterns: &[String],
    paths: &[String],
) {
    if !json && !ndjson {
        return;
    }
    if patterns.iter().any(|pattern| pattern.is_empty()) {
        exit_structured_search_error_if_needed(
            json,
            ndjson,
            "empty_pattern",
            "PATTERN must not be empty.",
        );
    }
    if let Some(missing_path) = first_missing_search_path(paths) {
        exit_structured_search_error_if_needed(
            json,
            ndjson,
            "path_not_found",
            format!("search path does not exist: {missing_path}"),
        );
    }
}

fn search_error_code_for_message(message: &str) -> Option<&'static str> {
    let lower = message.to_ascii_lowercase();
    if lower.contains("non-empty pattern") || lower.contains("pattern must not be empty") {
        Some("empty_pattern")
    } else if lower.contains("path does not exist") {
        Some("path_not_found")
    } else if lower.contains("failed to compile native search pattern")
        || lower.contains("regex parse error")
        || lower.contains("error parsing regex")
        || lower.contains("invalid regex")
    {
        Some("invalid_regex")
    } else {
        None
    }
}

fn normalize_search_error_detail(error: &str, detail: &str) -> String {
    if error == "invalid_regex" && !detail.to_ascii_lowercase().contains("invalid regex") {
        format!("invalid regex pattern: {detail}")
    } else {
        detail.to_string()
    }
}

fn exit_json_search_runtime_error_if_needed(json: bool, ndjson: bool, err: &anyhow::Error) {
    if !json && !ndjson {
        return;
    }
    let detail = err.to_string();
    if let Some(code) = search_error_code_for_message(&detail) {
        exit_structured_search_error_if_needed(
            json,
            ndjson,
            code,
            normalize_search_error_detail(code, &detail),
        );
    }
}

fn resolve_search_request(args: &SearchArgs) -> anyhow::Result<ResolvedSearchRequest> {
    resolve_search_request_with_stdin(args, stdin_should_search_implicit_path())
}

fn resolve_search_request_with_stdin(
    args: &SearchArgs,
    stdin_searches_implicit_path: bool,
) -> anyhow::Result<ResolvedSearchRequest> {
    let mut patterns = args.regexp.clone();
    let mut path_was_implicit = false;
    let paths = if args.regexp.is_empty() {
        if let Some(pattern) = args.pattern.as_ref() {
            patterns.push(pattern.clone());
        }
        if args.path.is_empty() {
            path_was_implicit = true;
            if stdin_searches_implicit_path {
                Vec::new()
            } else {
                vec![".".to_string()]
            }
        } else {
            args.path.clone()
        }
    } else {
        let mut paths = Vec::new();
        if let Some(path) = args.pattern.as_ref() {
            paths.push(path.clone());
        }
        paths.extend(args.path.clone());
        if paths.is_empty() {
            path_was_implicit = true;
            if stdin_searches_implicit_path {
                Vec::new()
            } else {
                vec![".".to_string()]
            }
        } else {
            paths
        }
    };

    if patterns.is_empty() {
        anyhow::bail!("search requires a pattern or at least one -e/--regexp pattern");
    }

    Ok(ResolvedSearchRequest {
        patterns,
        paths,
        path_was_implicit,
    })
}

/// Audit fix #1 (index capability validator, 2026-07-11): per-field classification of every
/// `SearchArgs` flag against the trigram index engine (`run_index_query` / `TrigramIndex`).
/// Three policy classes:
///   - `Honor`: the index path already correctly implements this flag (or the flag is one of
///     the query-defining fields -- `pattern`/`regexp`/`path` -- whose cardinality is enforced
///     separately, via `request.patterns.len() != 1` below and the `request.paths.len() != 1`
///     bail in `handle_index_search`).
///   - `PassthroughSafe`: the flag is a semantic no-op on this path -- it only restates a
///     default that already holds here (e.g. `--unicode`, `--no-hidden`, `--ignore`), or it only
///     changes behavior once ripgrep itself is invoked (e.g. `--auto-hybrid-regex`), which the
///     index path never does.
///   - `Refuse`: the flag changes the result set or output shape in a way the index cannot (yet)
///     reproduce. Silently dropping it would return wrong-but-plausible results with exit 0, so
///     the explicit `--index` path must fail closed and warm auto-routing must reroute past the
///     index instead (see the two call sites below and in `handle_index_search`).
///
/// Supersedes the original 6-flag ad-hoc deny-list (H1a, audit #79/#10/#14): that list was
/// correct as far as it went, but `run_index_query` only ever consulted
/// pattern/ignore_case/smart_case/fixed_strings/json/ndjson/count -- every OTHER flag (`--hidden`,
/// `--sort`, `--max-depth`, `-t`, `-o`, `-r`, `--max-filesize`, the `--no-ignore-*` family, ...)
/// was silently dropped once it reached this function instead of being honored or refused. (In
/// practice most of those flags are only non-json/non-ndjson-reachable via
/// `search_prefers_ripgrep_passthrough`'s early rg-passthrough branch in
/// `handle_ripgrep_search`, which diverts them to `rg` before `route_search` ever runs; combined
/// with `--json`/`--ndjson` that branch is skipped and they reach here directly -- see the H1e
/// smart-case tests below for the same reachability shape.)
///
/// The destructure below names EVERY `SearchArgs` field with no `..` rest pattern: adding a new
/// field to `SearchArgs` fails this function's compilation until it is explicitly classified
/// here (the compile-time ratchet). `INDEX_FLAG_POLICY` (test-only, defined just below) is a
/// second, independent listing used as a *runtime* backstop -- see
/// `index_flag_policy_table_is_exhaustive_over_search_args_clap_ids` in the test module -- so an
/// edit that adds a field here (satisfying the compiler) without ALSO updating that table still
/// fails a test instead of silently drifting out of sync.
fn index_flag_violations(args: &SearchArgs, request: &ResolvedSearchRequest) -> Vec<&'static str> {
    let mut violations = Vec::new();

    let SearchArgs {
        ignore_case: _,      // Honor: threaded into TrigramIndex::search.
        fixed_strings: _,    // Honor: threaded into TrigramIndex::search.
        no_fixed_strings: _, // PassthroughSafe: restates the `fixed_strings` default (false).
        invert_match,
        no_invert_match: _, // PassthroughSafe: restates the `invert_match` default (false).
        count: _,           // Honor: aggregate len(unique_line_matches).
        count_matches,
        line_number: _, // Honor: threaded as `line_number && !no_line_number` (fold-in b).
        no_line_number: _, // Honor: see line_number.
        column,
        no_column: _, // PassthroughSafe: the index path never emits column offsets.
        replace,
        format,
        sort,
        sort_reverse,
        sort_files,
        null,
        null_data,
        multiline,
        multiline_dotall,
        context: _,        // Refuse: covered by search_has_context() below (existing H1a).
        after_context: _,  // Refuse: see context.
        before_context: _, // Refuse: see context.
        max_count,
        max_depth,
        word_regexp,
        smart_case: _, // Honor: H1e, resolved per-pattern inside run_index_query.
        globs,
        no_ignore: _, // Honor: threaded as build/staleness mode (H1d).
        ignore: _,    // PassthroughSafe: restates the `no_ignore` default (respect ignore files).
        no_ignore_dot,
        no_ignore_exclude,
        no_ignore_files,
        no_ignore_global,
        no_ignore_parent,
        hidden,
        no_hidden: _, // PassthroughSafe: the index build walker hardcodes hidden-file exclusion
        // (`WalkBuilder::hidden(true)` in index.rs) regardless of query flags, so
        // this restates what already happens.
        follow,
        text,
        files_with_matches,
        files_without_match,
        file_type,
        index: _, // Honor: the field that selects this engine; not a compat flag itself.
        force_cpu,
        gpu_device_ids,
        color,
        path_separator,
        only_matching,
        vimgrep,
        passthru,
        json: _,    // Honor.
        ndjson: _,  // Honor.
        verbose: _, // Honor: emit_verbose_metadata is called from run_index_query.
        regexp: _,  // Honor: cardinality enforced via request.patterns.len() below.
        pattern: _, // Honor: the query itself.
        path: _,    // Honor: cardinality enforced by handle_index_search's paths.len()!=1 bail.
        pcre2,
        auto_hybrid_regex: _, // PassthroughSafe: only affects behavior once rg is actually invoked.
        unicode: _,           // PassthroughSafe: restates the Unicode-mode default (on).
        pcre2_unicode: _,     // PassthroughSafe: alias of `unicode`; same reasoning.
        max_filesize,
        no_ignore_vcs,
        require_git,
        messages: _, // PassthroughSafe: restates the default; index has no diagnostic-message mode.
        no_config: _, // PassthroughSafe: the index path never reads an rg config file.
        pcre2_version: _, // PassthroughSafe: early-exit flag (handle_ripgrep_search top), unreachable here.
        type_list: _,     // PassthroughSafe: early-exit flag, unreachable here.
        version: _,       // PassthroughSafe: early-exit flag, unreachable here.
    } = args;

    if *invert_match {
        violations.push("-v/--invert-match");
    }
    if search_has_context(args) {
        violations.push("-C/-A/-B (context)");
    }
    if max_count.is_some() {
        violations.push("-m/--max-count");
    }
    if *word_regexp {
        violations.push("-w/--word-regexp");
    }
    if !globs.is_empty() {
        violations.push("-g/--glob");
    }
    if request.patterns.len() != 1 {
        violations.push("multiple patterns (-e)");
    }
    if *count_matches {
        violations.push("--count-matches");
    }
    if *column {
        violations.push("--column");
    }
    if replace.is_some() {
        violations.push("-r/--replace");
    }
    if format.is_some() {
        violations.push("--format");
    }
    if sort.is_some() {
        violations.push("--sort");
    }
    if sort_reverse.is_some() {
        violations.push("--sortr");
    }
    if *sort_files {
        violations.push("--sort-files");
    }
    if *null {
        violations.push("-0/--null");
    }
    if *null_data {
        violations.push("--null-data");
    }
    if *multiline {
        violations.push("-U/--multiline");
    }
    if *multiline_dotall {
        violations.push("--multiline-dotall");
    }
    if max_depth.is_some() {
        violations.push("-d/--max-depth");
    }
    if *no_ignore_dot {
        violations.push("--no-ignore-dot");
    }
    if *no_ignore_exclude {
        violations.push("--no-ignore-exclude");
    }
    if *no_ignore_files {
        violations.push("--no-ignore-files");
    }
    if *no_ignore_global {
        violations.push("--no-ignore-global");
    }
    if *no_ignore_parent {
        violations.push("--no-ignore-parent");
    }
    if *hidden {
        violations.push("-./--hidden");
    }
    if *follow {
        violations.push("-L/--follow");
    }
    if *text {
        violations.push("-a/--text");
    }
    if *files_with_matches {
        violations.push("-l/--files-with-matches");
    }
    if *files_without_match {
        violations.push("--files-without-match");
    }
    if !file_type.is_empty() {
        violations.push("-t/--type");
    }
    if *force_cpu {
        // fold-in (c): --index and --cpu request contradictory engines; route_search currently
        // checks explicit_index before force_cpu, so without this --cpu would be silently
        // dropped rather than honored or refused.
        violations.push("--cpu/--force-cpu");
    }
    if !gpu_device_ids.is_empty() {
        // fold-in (c): same contradiction as force_cpu, for explicit --gpu-device-ids.
        violations.push("--gpu-device-ids");
    }
    if let Some(mode) = color {
        // `--color never`/`--color auto` restate a no-op default (the index's plain-text
        // emitter never writes ANSI escapes either way); only an explicit `always` (or any other
        // unrecognized value) asks for something this path cannot produce.
        if mode.as_str() != "never" && mode.as_str() != "auto" {
            violations.push("--color");
        }
    }
    if path_separator.is_some() {
        violations.push("--path-separator");
    }
    if *only_matching {
        violations.push("-o/--only-matching");
    }
    if *vimgrep {
        violations.push("--vimgrep");
    }
    if *passthru {
        violations.push("--passthru");
    }
    if *pcre2 {
        // fold-in (c): defense in depth only. route_search already sends --pcre2 to
        // ripgrep_pcre2() ahead of explicit_index when rg is available, and
        // handle_ripgrep_search's unconditional `require_ripgrep_or_exit(rg_available,
        // "--pcre2")` guard already fails closed before either is reached when rg is NOT
        // available -- so this arm should be unreachable in practice today. Kept so a future
        // routing-order change fails closed instead of silently running PCRE2 syntax through
        // the index's non-PCRE2 regex engine.
        violations.push("-P/--pcre2");
    }
    if max_filesize.is_some() {
        violations.push("--max-filesize");
    }
    if *no_ignore_vcs {
        violations.push("--no-ignore-vcs");
    }
    if *require_git {
        violations.push("--require-git");
    }

    violations
}

/// Test-only, independent-of-the-destructure classification table -- see
/// `index_flag_violations`'s doc comment for why this exists alongside the compile-time
/// exhaustive destructure. Keyed by clap arg id, which for a `#[derive(Args)]` struct field is
/// the Rust field name itself (not the `long = "..."` CLI spelling).
#[cfg(test)]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum IndexFlagPolicy {
    /// The index path already correctly implements this flag.
    Honor,
    /// The flag is a semantic no-op on the index path (restates a default, or only matters once
    /// ripgrep itself runs). `color` is listed here even though `--color always` is refused at
    /// runtime by value -- see `index_flag_violations`.
    PassthroughSafe,
    /// The flag changes the result set or output shape; `--index` must fail closed / reroute.
    Refuse,
}

#[cfg(test)]
const INDEX_FLAG_POLICY: &[(&str, IndexFlagPolicy)] = &[
    ("ignore_case", IndexFlagPolicy::Honor),
    ("fixed_strings", IndexFlagPolicy::Honor),
    ("no_fixed_strings", IndexFlagPolicy::PassthroughSafe),
    ("invert_match", IndexFlagPolicy::Refuse),
    ("no_invert_match", IndexFlagPolicy::PassthroughSafe),
    ("count", IndexFlagPolicy::Honor),
    ("count_matches", IndexFlagPolicy::Refuse),
    ("line_number", IndexFlagPolicy::Honor),
    ("no_line_number", IndexFlagPolicy::Honor),
    ("column", IndexFlagPolicy::Refuse),
    ("no_column", IndexFlagPolicy::PassthroughSafe),
    ("replace", IndexFlagPolicy::Refuse),
    ("format", IndexFlagPolicy::Refuse),
    ("sort", IndexFlagPolicy::Refuse),
    ("sort_reverse", IndexFlagPolicy::Refuse),
    ("sort_files", IndexFlagPolicy::Refuse),
    ("null", IndexFlagPolicy::Refuse),
    ("null_data", IndexFlagPolicy::Refuse),
    ("multiline", IndexFlagPolicy::Refuse),
    ("multiline_dotall", IndexFlagPolicy::Refuse),
    ("context", IndexFlagPolicy::Refuse),
    ("after_context", IndexFlagPolicy::Refuse),
    ("before_context", IndexFlagPolicy::Refuse),
    ("max_count", IndexFlagPolicy::Refuse),
    ("max_depth", IndexFlagPolicy::Refuse),
    ("word_regexp", IndexFlagPolicy::Refuse),
    ("smart_case", IndexFlagPolicy::Honor),
    ("globs", IndexFlagPolicy::Refuse),
    ("no_ignore", IndexFlagPolicy::Honor),
    ("ignore", IndexFlagPolicy::PassthroughSafe),
    ("no_ignore_dot", IndexFlagPolicy::Refuse),
    ("no_ignore_exclude", IndexFlagPolicy::Refuse),
    ("no_ignore_files", IndexFlagPolicy::Refuse),
    ("no_ignore_global", IndexFlagPolicy::Refuse),
    ("no_ignore_parent", IndexFlagPolicy::Refuse),
    ("hidden", IndexFlagPolicy::Refuse),
    ("no_hidden", IndexFlagPolicy::PassthroughSafe),
    ("follow", IndexFlagPolicy::Refuse),
    ("text", IndexFlagPolicy::Refuse),
    ("files_with_matches", IndexFlagPolicy::Refuse),
    ("files_without_match", IndexFlagPolicy::Refuse),
    ("file_type", IndexFlagPolicy::Refuse),
    ("index", IndexFlagPolicy::Honor),
    ("force_cpu", IndexFlagPolicy::Refuse),
    ("gpu_device_ids", IndexFlagPolicy::Refuse),
    ("color", IndexFlagPolicy::PassthroughSafe),
    ("path_separator", IndexFlagPolicy::Refuse),
    ("only_matching", IndexFlagPolicy::Refuse),
    ("vimgrep", IndexFlagPolicy::Refuse),
    ("passthru", IndexFlagPolicy::Refuse),
    ("json", IndexFlagPolicy::Honor),
    ("ndjson", IndexFlagPolicy::Honor),
    ("verbose", IndexFlagPolicy::Honor),
    ("regexp", IndexFlagPolicy::Honor),
    ("pattern", IndexFlagPolicy::Honor),
    ("path", IndexFlagPolicy::Honor),
    ("pcre2", IndexFlagPolicy::Refuse),
    ("auto_hybrid_regex", IndexFlagPolicy::PassthroughSafe),
    ("unicode", IndexFlagPolicy::PassthroughSafe),
    ("pcre2_unicode", IndexFlagPolicy::PassthroughSafe),
    ("max_filesize", IndexFlagPolicy::Refuse),
    ("no_ignore_vcs", IndexFlagPolicy::Refuse),
    ("require_git", IndexFlagPolicy::Refuse),
    ("messages", IndexFlagPolicy::PassthroughSafe),
    ("no_config", IndexFlagPolicy::PassthroughSafe),
    ("pcre2_version", IndexFlagPolicy::PassthroughSafe),
    ("type_list", IndexFlagPolicy::PassthroughSafe),
    ("version", IndexFlagPolicy::PassthroughSafe),
];

/// Detects warm-index routing state, loading the persisted index AT MOST ONCE (audit #138 item
/// #3): the previous version only returned `IndexRoutingState`, so the immediate warm-routing
/// caller (below) and `handle_index_search` each independently re-loaded + re-deserialized the
/// SAME `.tg_index` file for a single search invocation. Returning the loaded index lets the
/// caller reuse it directly instead of reading the file a second time. This is a pure READ -- it
/// never acquires the write lock (`IndexLockGuard` is only taken around a `save()`, never here);
/// see `save_index_locked` for where persistence is actually gated.
///
/// The returned `Option<TrigramIndex>` is `Some` only when a load actually succeeded; it is
/// always `None` when the guard clauses short-circuit (no `.tg_index` yet, explicit `--index`,
/// or an incompatible flag combination) or when the load itself failed (a corrupt/legacy index),
/// mirroring the `IndexRoutingState` this replaces field-for-field.
fn detect_warm_index_state(
    args: &SearchArgs,
    request: &ResolvedSearchRequest,
) -> (IndexRoutingState, Option<TrigramIndex>) {
    if args.index
        || request.paths.len() != 1
        || request.patterns.len() != 1
        || request.patterns[0].len() < 3
        || !index_flag_violations(args, request).is_empty()
    {
        return (IndexRoutingState::default(), None);
    }

    let index_path = resolve_index_path(request.primary_path());
    if !index_path.exists() {
        return (IndexRoutingState::default(), None);
    }

    match TrigramIndex::load(&index_path) {
        Ok(index) => {
            // M17 (audit-m17): the ROOT check runs FIRST, before any staleness work -- a
            // `.tg_index` reached from a DIFFERENT tree than it was built for must never be
            // warm-served (copied index, renamed tree, symlink alias). `is_stale` then also
            // covers the ordinary same-root staleness cases (H1d no_ignore mode flip,
            // per-file mtime/size, new files). A mismatch is disclosed via the same verbose
            // "[index]" channel as every other rebuild decision; routing then declines the
            // index (serving the query correctly through the fallback engine) and any actual
            // rebuild happens in `handle_index_search`, which repeats the check at its own
            // load sites.
            let root_reason = index.root_servability_reason(Path::new(request.primary_path()));
            if args.verbose {
                if let Some(reason) = &root_reason {
                    eprintln!("[index] refusing to serve cached index: {reason}");
                }
            }
            let is_stale = root_reason.is_some() || index.is_stale(args.no_ignore);
            (
                IndexRoutingState {
                    exists: true,
                    is_stale,
                    pattern_compatible: true,
                },
                Some(index),
            )
        }
        Err(_) => (
            IndexRoutingState {
                exists: true,
                is_stale: true,
                pattern_compatible: true,
            },
            None,
        ),
    }
}

#[cfg(feature = "cuda")]
fn count_search_corpus_bytes(
    paths: &[PathBuf],
    no_ignore: bool,
    globs: &[String],
) -> anyhow::Result<u64> {
    let mut total_bytes = 0u64;
    let mut roots = Vec::new();

    for path in paths {
        if path.is_file() {
            total_bytes = total_bytes.saturating_add(fs::metadata(path)?.len());
        } else {
            roots.push(path.clone());
        }
    }

    if roots.is_empty() {
        return Ok(total_bytes);
    }

    let mut builder = WalkBuilder::new(&roots[0]);
    for root in roots.iter().skip(1) {
        builder.add(root);
    }

    if no_ignore {
        builder.ignore(false);
        builder.git_ignore(false);
        builder.git_global(false);
        builder.git_exclude(false);
        builder.parents(false);
    } else {
        for root in &roots {
            for ignore_name in [".ignore", ".gitignore", ".rgignore"] {
                let ignore_path = root.join(ignore_name);
                if ignore_path.is_file() {
                    builder.add_ignore(ignore_path);
                }
            }
        }
    }

    if !globs.is_empty() {
        let mut overrides = OverrideBuilder::new(&roots[0]);
        for glob in globs {
            overrides
                .add(glob)
                .with_context(|| format!("failed to add glob override '{glob}'"))?;
        }
        builder.overrides(
            overrides
                .build()
                .context("failed to build glob override matcher")?,
        );
    }

    for entry in builder.build() {
        let entry = entry?;
        if entry
            .file_type()
            .map(|kind| kind.is_file())
            .unwrap_or(false)
        {
            total_bytes = total_bytes.saturating_add(entry.metadata()?.len());
        }
    }

    Ok(total_bytes)
}

#[cfg(feature = "cuda")]
fn load_search_routing_calibration(search_root: &Path) -> Option<SearchRoutingCalibration> {
    let now = tensor_grep_rs::crossover::current_timestamp();
    match tensor_grep_rs::crossover::load_fresh_crossover_config(Some(search_root), now) {
        Ok(Some((_, config))) => Some(SearchRoutingCalibration {
            threshold_bytes: config.corpus_size_breakpoint_bytes,
            gpu_positive: config.recommendation != "cpu_always",
        }),
        Ok(None) | Err(_) => None,
    }
}

#[cfg(feature = "cuda")]
fn auto_gpu_available_for_routing() -> bool {
    if env::var("TG_TEST_CUDA_BEHAVIOR")
        .ok()
        .map(|value| value.trim().eq_ignore_ascii_case("no-devices"))
        .unwrap_or(false)
    {
        return false;
    }

    enumerate_cuda_devices()
        .map(|devices| !devices.is_empty())
        .unwrap_or(false)
}

fn positional_ripgrep_args(
    cli: &PositionalCli,
    pattern: &str,
    paths: &[String],
) -> RipgrepSearchArgs {
    RipgrepSearchArgs {
        files: false,
        json: false,
        ignore_case: cli.ignore_case,
        fixed_strings: cli.fixed_strings,
        no_fixed_strings: false,
        invert_match: cli.invert_match,
        no_invert_match: false,
        count: cli.count,
        count_matches: false,
        line_number: cli.line_number && !cli.no_line_number,
        no_line_number: cli.no_line_number,
        column: false,
        only_matching: cli.only_matching,
        context: None,
        before_context: None,
        after_context: None,
        max_count: cli.max_count,
        word_regexp: cli.word_regexp,
        smart_case: false,
        globs: Vec::new(),
        ignore: cli.ignore,
        no_ignore: !cli.ignore,
        no_ignore_dot: false,
        no_ignore_exclude: false,
        no_ignore_files: false,
        no_ignore_global: false,
        no_ignore_parent: false,
        no_ignore_vcs: cli.no_ignore_vcs,
        require_git: cli.require_git,
        hidden: false,
        no_hidden: cli.no_hidden,
        follow: false,
        text: false,
        files_with_matches: false,
        files_without_match: false,
        file_types: Vec::new(),
        color: cli.color.clone(),
        path_separator: cli.path_separator.clone(),
        replace: cli.replace.clone(),
        vimgrep: cli.vimgrep,
        passthru: false,
        no_config: false,
        sort: None,
        sort_reverse: None,
        sort_files: false,
        max_depth: None,
        null: false,
        null_data: false,
        multiline: false,
        no_multiline: false,
        multiline_dotall: false,
        no_multiline_dotall: false,
        patterns: vec![pattern.to_string()],
        paths: paths.to_vec(),
        // `cli.path` is the RAW user-supplied PATH positionals before `implicit_search_paths`
        // substitutes stdin/"." -- empty means the caller gave no explicit path.
        path_was_implicit: cli.path.is_empty(),
        pcre2: cli.pcre2,
        no_pcre2: false,
        pcre2_unicode: cli.pcre2_unicode,
        no_pcre2_unicode: false,
        no_crlf: false,
        no_encoding: false,
        no_mmap: false,
        no_pre: false,
        no_search_zip: false,
        auto_hybrid_regex: cli.auto_hybrid_regex,
        no_auto_hybrid_regex: false,
        unicode: cli.unicode,
        no_text: false,
        no_binary: false,
        no_follow: false,
        no_glob_case_insensitive: false,
        no_ignore_file_case_insensitive: false,
        ignore_dot: false,
        ignore_exclude: false,
        ignore_files: false,
        ignore_global: false,
        ignore_messages: false,
        ignore_parent: false,
        ignore_vcs: false,
        no_one_file_system: false,
        no_block_buffered: false,
        no_byte_offset: false,
        no_column: false,
        no_context_separator: false,
        no_include_zero: false,
        no_line_buffered: false,
        no_max_columns_preview: false,
        no_trim: false,
        no_json: false,
        messages: cli.messages,
        no_stats: false,
        max_filesize: cli.max_filesize.clone(),
    }
}

fn command_ripgrep_args(args: &SearchArgs, request: &ResolvedSearchRequest) -> RipgrepSearchArgs {
    RipgrepSearchArgs {
        files: false,
        json: args.json && args.format.as_deref() == Some("rg"),
        ignore_case: args.ignore_case,
        fixed_strings: args.fixed_strings,
        no_fixed_strings: false,
        invert_match: args.invert_match,
        no_invert_match: false,
        count: args.count,
        count_matches: args.count_matches,
        line_number: args.line_number && !args.no_line_number,
        no_line_number: args.no_line_number,
        column: args.column && !args.no_column,
        only_matching: args.only_matching,
        context: args.context,
        before_context: args.before_context,
        after_context: args.after_context,
        max_count: args.max_count,
        word_regexp: args.word_regexp,
        smart_case: args.smart_case,
        globs: args.globs.clone(),
        ignore: args.ignore,
        no_ignore: args.no_ignore,
        no_ignore_dot: args.no_ignore_dot,
        no_ignore_exclude: args.no_ignore_exclude,
        no_ignore_files: args.no_ignore_files,
        no_ignore_global: args.no_ignore_global,
        no_ignore_parent: args.no_ignore_parent,
        no_ignore_vcs: args.no_ignore_vcs,
        require_git: args.require_git,
        hidden: args.hidden,
        no_hidden: args.no_hidden,
        follow: args.follow,
        text: args.text,
        files_with_matches: args.files_with_matches,
        files_without_match: args.files_without_match,
        file_types: args.file_type.clone(),
        color: args.color.clone(),
        path_separator: args.path_separator.clone(),
        replace: args.replace.clone(),
        vimgrep: args.vimgrep,
        passthru: args.passthru,
        no_config: args.no_config,
        sort: args.sort.clone(),
        sort_reverse: args.sort_reverse.clone(),
        sort_files: args.sort_files,
        max_depth: args.max_depth,
        null: args.null,
        null_data: args.null_data,
        multiline: args.multiline,
        no_multiline: false,
        multiline_dotall: args.multiline_dotall,
        no_multiline_dotall: false,
        patterns: request.patterns.clone(),
        paths: if request.path_was_implicit {
            Vec::new()
        } else {
            request.paths.clone()
        },
        path_was_implicit: request.path_was_implicit,
        pcre2: args.pcre2,
        no_pcre2: false,
        pcre2_unicode: args.pcre2_unicode,
        no_pcre2_unicode: false,
        no_crlf: false,
        no_encoding: false,
        no_mmap: false,
        no_pre: false,
        no_search_zip: false,
        auto_hybrid_regex: args.auto_hybrid_regex,
        no_auto_hybrid_regex: false,
        unicode: args.unicode,
        no_text: false,
        no_binary: false,
        no_follow: false,
        no_glob_case_insensitive: false,
        no_ignore_file_case_insensitive: false,
        ignore_dot: false,
        ignore_exclude: false,
        ignore_files: false,
        ignore_global: false,
        ignore_messages: false,
        ignore_parent: false,
        ignore_vcs: false,
        no_one_file_system: false,
        no_block_buffered: false,
        no_byte_offset: false,
        no_column: args.no_column,
        no_context_separator: false,
        no_include_zero: false,
        no_line_buffered: false,
        no_max_columns_preview: false,
        no_trim: false,
        no_json: false,
        messages: args.messages,
        no_stats: false,
        max_filesize: args.max_filesize.clone(),
    }
}

fn search_requires_ripgrep_passthrough(args: &SearchArgs) -> bool {
    (args.json && args.format.as_deref() == Some("rg"))
        || (!args.json
            && !args.ndjson
            && (args.count_matches
                || args.column
                || args.no_column
                || args.smart_case
                || args.hidden
                || args.follow
                || args.text
                || args.passthru
                || args.no_config
                || args.auto_hybrid_regex
                || args.pcre2_unicode
                || args.ignore
                || args.messages
                || args.require_git
                || args.no_hidden
                || args.path_separator.is_some()
                || args.vimgrep
                || args.no_ignore_dot
                || args.no_ignore_exclude
                || args.no_ignore_files
                || args.no_ignore_global
                || args.no_ignore_parent
                || args.files_with_matches
                || args.files_without_match
                || args.sort.is_some()
                || args.sort_reverse.is_some()
                || args.sort_files
                || args.max_depth.is_some()
                || args.null
                || args.null_data
                || args.multiline
                || args.multiline_dotall
                || !args.file_type.is_empty()))
}

/// P5·H2 (Backend Fail-Closed Contract): `search_requires_ripgrep_passthrough`'s whole hard-flag
/// list is gated behind `!args.json && !args.ndjson`, so on the STRUCTURED routes
/// (`--json`/`--ndjson`) the count/files flags fall through to the native engine -- and neither
/// `NativeSearchConfig` nor `GpuSearchParams` has a field for them
/// (`native_search_config_for_command` maps `count`/`only_matching`, not these), so they were
/// SILENTLY DROPPED (exit 0, wrong output; verified live on the shipped native front door:
/// `tg search ... --json --count-matches` prints a match list, not occurrence counts). This
/// predicate returns every such flag whose only ride to the native engine on this request would
/// be that drop -- the HARD-REFUSAL set. `--format rg --json` is DELIBERATELY excluded: that is
/// an rg PASSTHROUGH (`search_requires_ripgrep_passthrough` returns true), and
/// `command_ripgrep_args` threads all three flags into rg's own argv, so that route is honored,
/// not dropped -- refusing it would break working behavior.
fn native_structured_dropped_search_flags(args: &SearchArgs) -> Vec<&'static str> {
    let native_structured = args.ndjson || (args.json && args.format.as_deref() != Some("rg"));
    if !native_structured {
        return Vec::new();
    }
    let mut dropped = Vec::new();
    if args.count_matches {
        dropped.push("--count-matches");
    }
    if args.files_with_matches {
        dropped.push("--files-with-matches");
    }
    if args.files_without_match {
        dropped.push("--files-without-match");
    }
    dropped
}

/// P5·H2 positional twin. `run_positional_cli` has NO `search_requires_ripgrep_passthrough`-
/// equivalent gate at all, so `--count-matches` reaches the native engine through the `--json`/
/// `--ndjson` structured doors AND, unconditionally (no json/ndjson needed), the explicit
/// `--gpu-device-ids` door -- and `native_search_config_for_positional` maps `count`, not
/// `count_matches`. A NON--json/--ndjson/--gpu-device-ids positional `--count-matches` routes to
/// `BackendSelection::Ripgrep` instead; it is NOT refused here -- the Python front door excludes
/// it from native dispatch (bootstrap fast-path unsupported set + `count_matches` in
/// `_NATIVE_TG_DELEGATION_DEFAULT_REQUIRED_FIELDS`), so the published `tg PAT --count-matches`
/// path is served by Python->rg and stays honored (the already-fine case locked by
/// `test_cli_bootstrap.py::test_rust_first_count_matches_refuses_via_native_self_guard`).
fn positional_native_dropped_search_flags(cli: &PositionalCli) -> Vec<&'static str> {
    if !cli.count_matches {
        return Vec::new();
    }
    if cli.json || cli.ndjson || !cli.gpu_device_ids.is_empty() {
        vec!["--count-matches"]
    } else {
        Vec::new()
    }
}

/// Fail closed (exit 2, mirrors `exit_gpu_cpu_fallback_flag_unhonorable`) when a request the
/// native engine is about to serve carries a flag that engine silently drops. Names every dropped
/// flag and the remedy. The `structured` half makes the remedy text honest: only a
/// `--json`/`--ndjson` route can be redirected to `tg search ... --format rg --json` (rg's raw
/// passthrough output, described as raw paths/counts -- not "JSON Lines", which this native
/// refusal never emits); a bare `--gpu-device-ids` door gets the drop-the-flag/route-to-rg
/// remedy. Positional users have no `--format` flag, so the structured remedy always points at
/// the `tg search` command (which does have one) or at dropping the flags -- both work for both
/// front doors.
fn exit_native_structured_flag_dropped(flag_names: &[&'static str], structured: bool) {
    let flags = flag_names.join(", ");
    if structured {
        eprintln!(
            "error: {flags} is a raw path/count output mode that native structured \
             --json/--ndjson search output cannot express and would be silently dropped; \
             refusing rather than silently ignoring it. Drop --json/--ndjson (or {flags}), or \
             rerun as `tg search ... --format rg --json` to request ripgrep's raw passthrough \
             output, which carries {flags}."
        );
    } else {
        eprintln!(
            "error: {flags} is not supported by native GPU/CPU search output and would be \
             silently dropped; refusing rather than silently ignoring it. Drop {flags}, or drop \
             --gpu-device-ids/--json/--ndjson so the search is routed to the ripgrep backend, \
             which carries {flags}."
        );
    }
    std::process::exit(2);
}

/// Native-routing entrypoint gate for the `tg search` command (P5·H2). Pure: returns `Some(flags)`
/// when this request carries flags the native structured engine would silently drop (the caller
/// maps it to `exit_native_structured_flag_dropped`; keeping it pure makes it unit-testable
/// without the `exit(2)` killing the test binary -- see
/// `native_structured_refusal_validator_returns_refusal_set`). The `--format rg --json` passthrough
/// and the trigram index (which has its own `IndexFlagPolicy::Refuse` for these flags) never reach
/// it.
fn validate_search_native_structured_refusals(args: &SearchArgs) -> Option<Vec<&'static str>> {
    let dropped = native_structured_dropped_search_flags(args);
    if dropped.is_empty() {
        None
    } else {
        Some(dropped)
    }
}

/// Native-routing entrypoint gate for the positional CLI (P5·H2), same pure contract. The exit
/// wrapper is applied at the call site so both the `--json`/`--ndjson` doors and the
/// unconditional `--gpu-device-ids` door refuse identically.
fn validate_positional_native_structured_refusals(
    cli: &PositionalCli,
) -> Option<Vec<&'static str>> {
    let dropped = positional_native_dropped_search_flags(cli);
    if dropped.is_empty() {
        None
    } else {
        Some(dropped)
    }
}

/// P5·H2 extension (audit/h2 follow-up): the rg-passthrough route carries the count/files flags
/// (`search_requires_ripgrep_passthrough`'s hard-flag list; `command_ripgrep_args` threads them
/// into rg's argv) but `RipgrepSearchArgs` has NO `--gpu-device-ids` field (zero gpu refs), so a
/// gpu + count/files request silently drops the explicit GPU request (wrong output, exit 0). The
/// front-door rewrite makes this a SEARCH-FORM class, not just the positional one:
/// `SEARCH_OPTION_FIRST_FLAGS` includes `--count-matches`, so `tg PAT . --gpu-device-ids 0
/// --count-matches` normalizes into `tg search PAT . --gpu-device-ids 0 --count-matches` and
/// never reaches `validate_positional_native_structured_refusals`. This predicate returns every
/// count/files flag whose rg-passthrough would drop the GPU request; empty when no explicit GPU
/// ids are present (pure `--count-matches`/`-l` keep their HONORED rg passthrough).
/// P5·H2 "never silently drop" (Backend Fail-Closed Contract). Pure so the call site can map it
/// to the exit wrapper while in-process tests drive it directly.
fn rg_passthrough_gpu_dropped_search_flags(args: &SearchArgs) -> Vec<&'static str> {
    if args.gpu_device_ids.is_empty() {
        return Vec::new();
    }
    let mut dropped = Vec::new();
    if args.count_matches {
        dropped.push("--count-matches");
    }
    if args.files_with_matches {
        dropped.push("--files-with-matches");
    }
    if args.files_without_match {
        dropped.push("--files-without-match");
    }
    dropped
}

/// Fail closed (exit 2) when the rg-passthrough route would silently drop an explicit
/// `--gpu-device-ids` request (`command_ripgrep_args` has no GPU field). Names the combined
/// flags and the remedy; message style mirrors `exit_native_structured_flag_dropped`'s
/// non-structured arm.
fn exit_gpu_dropped_on_rg_passthrough(flag_names: &[&'static str]) {
    let flags = flag_names.join(", ");
    eprintln!(
        "error: --gpu-device-ids combined with {flags} cannot be honored: the ripgrep passthrough \
         that carries {flags} has no GPU field, so the explicit GPU request would be silently \
         dropped; refusing rather than silently ignoring it. Drop --gpu-device-ids, or drop \
         {flags}."
    );
    std::process::exit(2);
}

fn search_prefers_ripgrep_passthrough(
    args: &SearchArgs,
    request: &ResolvedSearchRequest,
    rg_available: bool,
) -> bool {
    if search_requires_ripgrep_passthrough(args) {
        return true;
    }
    if args.json
        || args.ndjson
        || args.index
        || args.force_cpu
        || !args.gpu_device_ids.is_empty()
        || detect_warm_index_state(args, request).0.exists
    {
        return false;
    }
    rg_available
        && (args.count
            || args.no_ignore
            || args.no_ignore_vcs
            || !args.globs.is_empty()
            || (args.fixed_strings && request.patterns.len() > 1))
}

fn search_has_context(args: &SearchArgs) -> bool {
    args.context.is_some() || args.before_context.is_some() || args.after_context.is_some()
}

fn search_before_context(args: &SearchArgs) -> usize {
    args.before_context.or(args.context).unwrap_or(0)
}

fn search_after_context(args: &SearchArgs) -> usize {
    args.after_context.or(args.context).unwrap_or(0)
}

fn search_effective_context(args: &SearchArgs) -> Option<usize> {
    args.context
        .or_else(|| match (args.before_context, args.after_context) {
            (Some(before), Some(after)) => Some(before.max(after)),
            (Some(before), None) => Some(before),
            (None, Some(after)) => Some(after),
            (None, None) => None,
        })
}

fn native_search_config_for_positional(
    cli: &PositionalCli,
    pattern: &str,
    paths: &[String],
    decision: RoutingDecision,
) -> NativeSearchConfig {
    NativeSearchConfig {
        pattern: pattern.to_string(),
        paths: paths.iter().map(PathBuf::from).collect(),
        routing_backend: decision.routing_backend(),
        routing_reason: decision.reason,
        sidecar_used: decision.sidecar_used(),
        requested_gpu_device_ids: Vec::new(),
        ignore_case: cli.ignore_case,
        smart_case: false,
        fixed_strings: cli.fixed_strings,
        word_boundary: cli.word_regexp,
        invert_match: cli.invert_match,
        count: cli.count,
        max_count: cli.max_count.map(|value| value as u64),
        no_ignore: true,
        json: cli.json,
        ndjson: cli.ndjson,
        verbose: cli.verbose,
        text: false,
        line_number: cli.line_number && !cli.no_line_number,
        only_matching: cli.only_matching,
        replace: cli.replace.clone(),
        // `cli.path` is the RAW user-supplied PATH positionals before `implicit_search_paths`
        // substitutes stdin/"." -- empty means the caller gave no explicit path (audit #105,
        // mirrors `positional_ripgrep_args`'s `path_was_implicit: cli.path.is_empty()`).
        path_was_implicit: cli.path.is_empty(),
        ..NativeSearchConfig::default()
    }
}

fn native_search_config_for_command(
    args: &SearchArgs,
    pattern: &str,
    paths: &[String],
    path_was_implicit: bool,
    decision: RoutingDecision,
) -> NativeSearchConfig {
    NativeSearchConfig {
        pattern: pattern.to_string(),
        paths: paths.iter().map(PathBuf::from).collect(),
        routing_backend: decision.routing_backend(),
        routing_reason: decision.reason,
        sidecar_used: decision.sidecar_used(),
        requested_gpu_device_ids: Vec::new(),
        ignore_case: args.ignore_case,
        smart_case: args.smart_case,
        fixed_strings: args.fixed_strings,
        word_boundary: args.word_regexp,
        invert_match: args.invert_match,
        before_context: search_before_context(args),
        after_context: search_after_context(args),
        max_count: args.max_count.map(|value| value as u64),
        glob: args.globs.clone(),
        hidden: args.hidden,
        max_depth: args.max_depth,
        count: args.count,
        no_ignore: args.no_ignore,
        // Task #267: without this, `--json`/`--ndjson` (this function's `structured_output`
        // callers) silently dropped `--no-ignore-vcs` -- `build_walk_builder` had no field to
        // read it from -- while the identical flag on the non-structured-output route (real
        // `rg` via `command_ripgrep_args`/`root_ignore_file_args`) honored it correctly. An
        // output-format flag must never change the file set.
        no_ignore_vcs: args.no_ignore_vcs,
        json: args.json,
        ndjson: args.ndjson,
        verbose: args.verbose,
        text: args.text,
        line_number: args.line_number && !args.no_line_number,
        only_matching: args.only_matching,
        replace: args.replace.clone(),
        // Audit #105: threaded from `ResolvedSearchRequest::path_was_implicit` at every call
        // site (mirrors `command_ripgrep_args`'s `path_was_implicit: request.path_was_implicit`)
        // so this engine's own implicit-walk-ceiling gate (`native_search::
        // check_native_implicit_walk_ceiling`) can fire for `--json`/`--force-cpu`/single-pattern
        // `--fixed-strings`/rg-unavailable routing, none of which pass through
        // `execute_ripgrep_search`'s #100 gate.
        path_was_implicit,
        ..NativeSearchConfig::default()
    }
}

fn native_search_config_for_gpu_params(
    params: &GpuSearchParams<'_>,
    pattern: &str,
    decision: RoutingDecision,
) -> NativeSearchConfig {
    NativeSearchConfig {
        pattern: pattern.to_string(),
        paths: vec![PathBuf::from(params.path)],
        routing_backend: decision.routing_backend(),
        routing_reason: decision.reason,
        sidecar_used: decision.sidecar_used(),
        requested_gpu_device_ids: params.gpu_device_ids.to_vec(),
        ignore_case: params.ignore_case,
        smart_case: params.smart_case,
        fixed_strings: params.fixed_strings,
        word_boundary: params.word_regexp,
        invert_match: params.invert_match,
        before_context: params.context.unwrap_or(0),
        after_context: params.context.unwrap_or(0),
        max_count: params.max_count.map(|value| value as u64),
        glob: params.globs.clone(),
        hidden: params.hidden,
        max_depth: params.max_depth,
        count: params.count,
        no_ignore: params.no_ignore,
        // Task #267: same gap as `native_search_config_for_command` -- this is the
        // explicit-`--gpu-device-ids`-fallback-to-CPU route, which already carries
        // `params.no_ignore_vcs` (used by the GPU engine itself) but was never threading it
        // into the CPU-fallback `NativeSearchConfig`, silently dropping `--no-ignore-vcs` on
        // this route the same way.
        no_ignore_vcs: params.no_ignore_vcs,
        json: params.json,
        ndjson: params.ndjson,
        verbose: params.verbose,
        text: params.text,
        line_number: params.line_number,
        // Task #131 F3: `NativeSearchConfig` already carries `only_matching`/`replace` (see the
        // sibling `native_search_config_for_command`/`native_search_config_for_positional`, which
        // set them from `args`/`cli` directly) -- this mapper simply never copied them over, so
        // the GPU CPU-fallback route silently dropped `-o`/`--replace` even though the CPU engine
        // it delegates to is fully capable of honoring them.
        only_matching: params.only_matching,
        replace: params.replace.clone(),
        // Audit #105: threaded from `GpuSearchParams::path_was_implicit` (see that field's doc
        // comment -- this is the explicit-`--gpu-device-ids`-fallback-to-CPU route, which used to
        // have no way to know whether the PATH was implicit at all).
        path_was_implicit: params.path_was_implicit,
        ..NativeSearchConfig::default()
    }
}

fn execute_native_search(config: NativeSearchConfig) -> anyhow::Result<SearchStats> {
    if let Ok(message) = env::var("TG_TEST_NATIVE_SEARCH_FORCE_ERROR") {
        anyhow::bail!(message);
    }

    run_native_search(config)
}

/// Mirrors `ResolvedSearchRequest::path_display`'s join-with-space convention (this module, used
/// by the SearchArgs/positional-CLI request types) so a refusal's `path` field matches what a
/// SUCCESSFUL result on the same invocation would have shown, rather than inventing a second
/// "path" convention just for this one envelope.
fn native_search_config_path_display(paths: &[PathBuf]) -> String {
    if paths.is_empty() {
        ".".to_string()
    } else {
        paths
            .iter()
            .map(|path| path.to_string_lossy().into_owned())
            .collect::<Vec<_>>()
            .join(" ")
    }
}

/// Task #17 (2026-07-30): pure envelope builder, kept separate from the `println!` wrapper below
/// so it can be unit-tested without needing to capture process stdout or trigger
/// `std::process::exit`. Mirrors the Python CLI's `_emit_broad_scan_refusal` JSON payload
/// (`cli/main.py`) field for field -- `version`, `path`, `total_matches: 0`, `total_files: 0`,
/// `matches: []`, `truncated: true`, `result_incomplete: true`, `incomplete_reason`,
/// `incomplete_reason_class: "scan_limit"`, and an `error` object carrying `code:
/// "broad_scan_refused"`, `message`, `retryable: false`. Two front doors refusing the same thing
/// in two different shapes is exactly the drift AGENTS.md warns about, so this is not a new
/// vocabulary -- it is the existing one, reproduced.
fn broad_scan_refusal_json_envelope(path: &str, message: &str) -> serde_json::Value {
    serde_json::json!({
        "version": JSON_OUTPUT_VERSION,
        "path": path,
        "total_matches": 0,
        "total_files": 0,
        "matches": [],
        "truncated": true,
        "result_incomplete": true,
        "incomplete_reason": message,
        "incomplete_reason_class": "scan_limit",
        "error": {
            "code": "broad_scan_refused",
            "message": message,
            "retryable": false,
        },
    })
}

/// Task #17: the stdout half of the fix below. A no-op unless BOTH `json` is set AND `err` is
/// specifically the shared implicit-walk-ceiling refusal (`is_unbounded_implicit_search_walk_
/// refusal`) -- every other native-search error keeps its pre-existing `--json` behavior
/// untouched (handled by `exit_json_search_runtime_error_if_needed`'s own code-recognition, which
/// deliberately does not recognize this refusal's text). Never prints to stderr itself; the
/// caller's own `eprintln!` is unconditional and unchanged so text-mode output cannot drift.
fn emit_broad_scan_refusal_json_if_needed(json: bool, path: &str, err: &anyhow::Error) {
    if !json {
        return;
    }
    let message = err.to_string();
    if !is_unbounded_implicit_search_walk_refusal(&message) {
        return;
    }
    println!("{}", broad_scan_refusal_json_envelope(path, &message));
}

/// Audit #105: `collect_native_multi_pattern_matches`'s two fallible native-search calls (the
/// AhoCorasick fast path and the per-pattern regex loop below) both funnel any `Err` through
/// this helper instead of a bare `?`. Every one of this function's 4 call sites (the single- and
/// multi-`-e` `tg search` routes, and the two GPU-explicit-`--gpu-device-ids` CPU-fallback
/// routes) would otherwise let an implicit-walk-ceiling refusal `Err` propagate all the way to
/// `main()`'s default `Result` termination, which exits 1 -- the "exit-1-vs-exit-2 no-match
/// ambiguity bug" (audit #81 #7) -- instead of the fast-bounded exit-2 refusal every other
/// native-CPU route already gets via `run_native_search_with_optional_rg_fallback`'s generic Err
/// handling. Deliberately mirrors `execute_ripgrep_search`'s OWN refusal (rg_passthrough.rs) on
/// stderr: the exact same plain `eprintln!` text, byte-for-byte, regardless of which internal
/// engine produced it -- that half of the symmetry is untouched and must stay untouched.
///
/// Task #17 (2026-07-30), reversing the second half of this comment's old claim: this used to end
/// "never a structured JSON error object, even under `--json`" -- coherent only while NEITHER
/// front door emitted one. #851 gave the Python CLI's own emitter (`_emit_broad_scan_refusal`,
/// cli/main.py) a machine-readable `error.code: "broad_scan_refused"` envelope on stdout for this
/// exact refusal family, which broke the symmetry this rationale protected: pip users got the
/// envelope, standalone-binary/Homebrew/winget users still got 0 stdout bytes under `--json`. This
/// helper (and its single-pattern sibling in `run_native_search_with_optional_rg_fallback`) now
/// ALSO emits that same envelope, field-for-field, via `emit_broad_scan_refusal_json_if_needed`,
/// while leaving the stderr line completely unchanged. Any OTHER native-search error (bad path,
/// bad pattern, ...) is returned completely unchanged; this must not alter exit-code or `--json`
/// behavior for pre-existing error kinds.
fn exit_on_native_multi_pattern_ceiling_refusal(
    err: anyhow::Error,
    json: bool,
    path: &str,
) -> anyhow::Error {
    if !is_unbounded_implicit_search_walk_refusal(&err.to_string()) {
        return err;
    }
    eprintln!("{err}");
    emit_broad_scan_refusal_json_if_needed(json, path, &err);
    std::process::exit(2);
}

fn collect_native_multi_pattern_matches(
    patterns: &[String],
    mut base_config: NativeSearchConfig,
) -> anyhow::Result<(Vec<SearchMatchJson>, Option<usize>)> {
    let include_pattern_metadata = patterns.len() > 1;
    // Task #17: captured BEFORE the per-pattern loop below force-clears `base_config.json` (so
    // each per-pattern `execute_native_search` call doesn't render its own partial envelope) --
    // `exit_on_native_multi_pattern_ceiling_refusal` needs the CALLER's original `--json`
    // request, not that internal "don't render yet" override, or the second call site a few
    // lines down would always see `json = false` and silently drop the envelope it exists to add.
    let json_output = base_config.json;
    let refusal_path = native_search_config_path_display(&base_config.paths);
    let fast_path_matches = run_native_fixed_multi_pattern_search(base_config.clone(), patterns)
        .map_err(|err| {
            exit_on_native_multi_pattern_ceiling_refusal(err, json_output, &refusal_path)
        })?;
    if let Some(matches) = fast_path_matches {
        // The AhoCorasick fast path owns no `SearchStats`, so it genuinely cannot report a
        // count -- `None`, never `Some(0)`. Tracked as the residual of task 317.
        return Ok((
            matches
                .into_iter()
                .map(|matched| {
                    let (text, bytes) = native_json_text_fields(&matched.raw);
                    let text = text.map(str::to_string);
                    SearchMatchJson {
                        file: matched.path.to_string_lossy().into_owned(),
                        line: matched.line_number as usize,
                        text,
                        bytes,
                        raw: matched.raw,
                        range: None,
                        meta_variables: None,
                        pattern_id: include_pattern_metadata.then_some(matched.pattern_id),
                        pattern_text: include_pattern_metadata.then_some(matched.pattern_text),
                    }
                })
                .collect(),
            None,
        ));
    }

    base_config.json = false;
    base_config.ndjson = false;
    base_config.count = false;
    base_config.output_target = NativeOutputTarget::Buffer(Arc::new(Mutex::new(Vec::new())));

    let mut matches = Vec::new();
    // Task 276 finding 5: this loop already HAD `stats` and used only `stats.matches`, so every
    // per-pattern walk error was discarded right here.
    let mut walk_errors = 0usize;
    for (pattern_id, pattern) in patterns.iter().enumerate() {
        let mut pattern_config = base_config.clone();
        pattern_config.pattern = pattern.clone();
        let stats = execute_native_search(pattern_config).map_err(|err| {
            exit_on_native_multi_pattern_ceiling_refusal(err, json_output, &refusal_path)
        })?;
        walk_errors += stats.walk_errors;
        matches.extend(stats.matches.into_iter().map(|matched| {
            let (text, bytes) = native_json_text_fields(&matched.raw);
            let text = text.map(str::to_string);
            SearchMatchJson {
                file: matched.path.to_string_lossy().into_owned(),
                line: matched.line_number.unwrap_or(0) as usize,
                text,
                bytes,
                raw: matched.raw,
                range: None,
                meta_variables: None,
                pattern_id: include_pattern_metadata.then_some(pattern_id),
                pattern_text: include_pattern_metadata.then(|| pattern.clone()),
            }
        }));
    }

    Ok((matches, Some(walk_errors)))
}

struct NativeSearchOutputOptions<'a> {
    decision: RoutingDecision,
    query: &'a str,
    path: &'a str,
    requested_gpu_device_ids: &'a [i32],
    json: bool,
    ndjson: bool,
    count: bool,
    line_number: bool,
    /// Task #26. Threaded from `ResolvedSearchRequest::path_was_implicit` at every construction
    /// site -- the same signal the broad-scan probe already gates on, reused rather than
    /// re-derived, so the two cannot disagree about whether the caller chose the scope.
    path_was_implicit: bool,
}

fn emit_multi_pattern_native_results(
    options: NativeSearchOutputOptions<'_>,
    matches: Vec<SearchMatchJson>,
    incomplete_paths: Option<usize>,
) -> anyhow::Result<()> {
    let has_matches = !matches.is_empty();
    if options.json {
        emit_json_search_results(
            options.decision,
            options.query,
            options.path,
            options.requested_gpu_device_ids,
            matches,
            incomplete_paths,
            options.path_was_implicit,
        )?;
    } else if options.ndjson {
        emit_ndjson_search_results(
            options.decision,
            options.query,
            options.path,
            options.requested_gpu_device_ids,
            matches,
            incomplete_paths,
            options.path_was_implicit,
        )?;
    } else if options.count {
        emit_count_search_matches(options.path, &matches)?;
    } else {
        emit_plain_search_matches_with_line_number(options.path, &matches, options.line_number)?;
    }

    // Task 276 task 6 (exit-code parity). Checked BEFORE the no-match branch, in the SAME order
    // the single-pattern native route already resolves these two questions in
    // `run_native_search_with_optional_rg_fallback`. "I could not finish looking" outranks "I
    // found nothing", because the second is only trustworthy if the first is false.
    //
    // Until this guard, every one of this function's four callers passed a REAL walk-error count
    // (each one reads it straight out of `collect_native_multi_pattern_matches`), so the envelope
    // said `result_incomplete: true` and the process then exited 0 -- or, on a zero-match
    // incomplete scan, fell into the branch below and exited 1, which reads as an authoritative
    // "no matches exist". That is the exact lie #276 exists to stop, in the twin of the route
    // whose comment says the ordering exists to prevent it.
    if walk_was_incomplete(incomplete_paths) {
        std::process::exit(2);
    }
    if !has_matches {
        std::process::exit(1);
    }

    Ok(())
}

/// Exit code for a consumer-closed pipe, measured against rg for THE SHAPE THIS ROUTE ADMITS.
///
/// The mechanism matters, because an earlier revision of this comment stated it wrongly ("1 is
/// specifically rg's broken-pipe code"). It is not. rg's actual broken-pipe path in `main()`
/// returns **0**: it walks `err.chain()` for `BrokenPipe` and exits successfully. The 1 observed
/// here is a different thing entirely -- for a SINGLE-FILE search rg breaks out of its loop before
/// recording a match, so `matched` stays false and it reports its ordinary "no match" code.
/// Receipts that separate the two: `rg -c needle dense.txt` early-close -> rc=0 (5/5), and
/// `rg needle m1.txt m2.txt` early-close -> rc=0 (5/5).
///
/// So this constant is SHAPE-BOUND. It is correct for the currently-admitted subset -- exactly one
/// explicit regular file, no `-c` -- and would be WRONG the moment that subset widened to multiple
/// paths or `--count`. Anyone widening `native_can_serve_plain_text` must revisit it.
/// `test_early_closing_consumer_matches_ripgrep` asserts PARITY WITH RG rather than this constant,
/// so a widening (or a platform whose rg differs) fails loudly instead of silently inheriting the
/// wrong code.
const BROKEN_PIPE_EXIT_CODE: i32 = 1;

/// Does any link in this error chain carry `ErrorKind::BrokenPipe`?
///
/// The kind is PRESERVED at the source: `native_search::sink_io_error` copies the original
/// `ErrorKind` when converting to the `io::Error` that `grep_searcher::Sink::Error` requires, and
/// `grep_searcher` propagates that error unwrapped, so the typed walk below finds `BrokenPipe`
/// intact. Every link is still checked rather than just the outermost, because the chain also
/// carries the anyhow context `search_path`'s caller attaches.
///
/// (An earlier revision of this comment described the opposite mechanism -- that
/// `io::Error::other` flattened the kind to `Other` and only the innermost link still said
/// `BrokenPipe`. That was true of the code at the time and became false when `sink_io_error`
/// replaced those 13 call sites; it is corrected here rather than left to contradict the code and
/// the comment nine lines below it.)
fn error_chain_has_broken_pipe(err: &anyhow::Error) -> bool {
    let mut saw_io_error = false;
    for cause in err.chain() {
        if let Some(io_err) = cause.downcast_ref::<io::Error>() {
            saw_io_error = true;
            if io_err.kind() == io::ErrorKind::BrokenPipe {
                return true;
            }
        }
    }
    if saw_io_error {
        // A typed `io::Error` was present and said something OTHER than BrokenPipe. Trust it: this
        // is a real failure and must reach the structured-error and rg-fallback paths below.
        //
        // Guarding the string match on this is what stops a SILENT SWALLOW: the anyhow context
        // embeds the searched path (`native standard output search failed for <path>`), so without
        // it a file whose path merely contains "broken pipe" would turn any genuine error into a
        // quiet exit(1) "no matches" -- skipping the JSON error and the rg fallback both. Narrow,
        // but silent-swallow is the class this repo fails closed on.
        return false;
    }
    // Fallback for the case where no typed `io::Error` survives the chain at all. Kept because an
    // earlier revision relied on the typed walk alone and CI proved it did not fire on Linux;
    // `io::Error(BrokenPipe)` renders as "Broken pipe (os error 32)" on Unix and "The pipe is
    // being closed. (os error 232)" on Windows.
    let rendered = format!("{err:#}").to_ascii_lowercase();
    rendered.contains("broken pipe") || rendered.contains("pipe is being closed")
}

fn run_native_search_with_optional_rg_fallback(
    config: NativeSearchConfig,
    rg_fallback: Option<RipgrepSearchArgs>,
) -> anyhow::Result<()> {
    let json = config.json;
    let ndjson = config.ndjson;
    let verbose = config.verbose;
    // Task #17: captured before `config` moves into `execute_native_search` below -- this is the
    // shared chokepoint for BOTH single-pattern front doors (`tg search PATTERN` and the bare
    // positional `tg PATTERN`), so a ceiling refusal reached through either one needs a `path`
    // for the same `--json` envelope `exit_on_native_multi_pattern_ceiling_refusal` emits for the
    // multi-`-e` route.
    let refusal_path = native_search_config_path_display(&config.paths);
    match execute_native_search(config) {
        Ok(stats) => {
            // Task #276 slice C. An incomplete walk exits 2 -- and it is checked BEFORE the
            // no-match branch on purpose, because the two answer different questions and rg
            // resolves them in this order too. `rg needle .` over a tree with one access-denied
            // subdirectory exits 2 whether or not it matched anything elsewhere: "I could not
            // finish looking" outranks "I found nothing", since the second is only trustworthy
            // if the first is false. Reversing these would let a zero-match incomplete scan exit
            // 1, which reads as an authoritative "no matches exist" -- the exact lie #276 exists
            // to stop.
            //
            // 🔴 MERGE PRECONDITION -- NOT satisfied history. PRs #792 and #793 make the six
            // exit-code consumers three-state aware (agent_readiness x3, both benchmark
            // harnesses, the byte-fidelity e2e, mcp_server; crossover.rs verified unreachable).
            // AS OF THIS COMMIT BOTH ARE STILL OPEN, and `origin/main` still has
            // `agent_readiness.py` rejecting any exit not in {0,1}. THIS BRANCH MUST NOT MERGE
            // BEFORE THEM: doing so breaks `tg calibrate` (tg spawns itself and bails on
            // non-zero) and `tg dogfood` (a shipped command), and reds windows-agent-readiness.
            //
            // An earlier revision of this comment stated C0 "landed first" as completed fact.
            // It had not. A comment asserting a merge that never happened is worse than no
            // comment -- it is what the next reader trusts instead of re-checking.
            if stats.walk_errors > 0 {
                std::process::exit(2);
            }
            if stats.total_matches == 0 && stats.binary_match_files == 0 {
                std::process::exit(1);
            }
            Ok(())
        }
        Err(err) => {
            // A BROKEN PIPE is the consumer terminating normally (`tg ... | head -1`, `| less`, an
            // agent that reads N lines and stops), NOT a search failure. It only became reachable
            // for plain text because this route moves ownership of the write loop out of an
            // `Stdio::inherit()` subprocess and into this process: rg absorbs EPIPE itself, while
            // the native sink surfaces it as an error. Without this guard the fallback branch
            // below would print the `warning: native CPU search failed...` line that
            // `test_native_plain_text_route_emits_no_extra_stderr` swears never leaks, and then
            // RE-RUN the entire search into an already-closed pipe.
            //
            // Measured on the shipped v1.98.3 + rg 15.1.0 (Windows), 279 KB dense fixture,
            // consumer `readline(); close()`, 3/3 runs stable, with a full-drain control showing
            // rg 0 / native 0 (i.e. no divergence when nothing closes early):
            //     rg           -> rc=1, stderr ''
            //     tg (native)  -> rc=2, stderr 'native standard output search failed for <path>'
            // Checked BEFORE the structured-error and fallback branches: once the consumer is
            // gone, emitting a JSON error or re-running the search is pointless on every route,
            // not just this one.
            if error_chain_has_broken_pipe(&err) {
                std::process::exit(BROKEN_PIPE_EXIT_CODE);
            }
            exit_json_search_runtime_error_if_needed(json, ndjson, &err);
            if let Some(rg_args) = rg_fallback {
                eprintln!("warning: native CPU search failed, falling back to ripgrep: {err}");
                if !json && !ndjson && verbose {
                    emit_verbose_metadata(RoutingDecision::ripgrep());
                }
                let exit_code = execute_ripgrep_search(&rg_args)?;
                if exit_code != 0 {
                    std::process::exit(exit_code.max(1));
                }
                return Ok(());
            }

            eprintln!("{err}");
            // Task #17: this is the dominant real-world path for the shared implicit-walk-ceiling
            // refusal -- a bare `tg search PAT --json` (or the positional `tg PAT --json`) on a
            // large implicit root always has `rg_fallback = None` under `--json` (every
            // structured-output `RoutingDecision` sets `allow_rg_fallback = false`), so it falls
            // straight through to this catch-all. A no-op for every other error kind and for text
            // mode; see `emit_broad_scan_refusal_json_if_needed`'s doc comment.
            emit_broad_scan_refusal_json_if_needed(json, &refusal_path, &err);
            std::process::exit(2);
        }
    }
}

fn handle_ripgrep_search(args: SearchArgs) -> anyhow::Result<()> {
    if args.version {
        println!("tg {}", env!("CARGO_PKG_VERSION"));
        return Ok(());
    }
    if args.pcre2_version {
        require_ripgrep_or_exit(ripgrep_is_available(), "--pcre2-version");
        let exit_code = execute_ripgrep_pcre2_version()?;
        if exit_code != 0 {
            std::process::exit(exit_code.max(1));
        }
        return Ok(());
    }
    if args.type_list {
        require_ripgrep_or_exit(ripgrep_is_available(), "--type-list");
        let exit_code = execute_ripgrep_type_list()?;
        if exit_code != 0 {
            std::process::exit(exit_code.max(1));
        }
        return Ok(());
    }

    let request = resolve_search_request(&args)?;
    exit_json_search_input_error_if_needed(
        args.json,
        args.ndjson,
        &request.patterns,
        &request.paths,
    );
    let query = request.query_display();
    let path_display = request.path_display();
    let rg_available = ripgrep_is_available();
    #[cfg_attr(not(feature = "cuda"), allow(unused_variables))]
    let structured_output = args.json || args.ndjson;
    let auto_gpu_ids: [i32; 0] = [];

    // Fail closed instead of silently swapping --pcre2 to the native regex engine (which does not
    // support PCRE2 syntax) when no rg is present. Checked before route_search, whose `config.pcre2
    // && config.rg_available` gate otherwise falls through to NativeCpu/"rg_unavailable" with no
    // signal that PCRE2 semantics were dropped (audit #81 #9).
    if args.pcre2 {
        require_ripgrep_or_exit(rg_available, "--pcre2");
    }

    if search_args_need_broad_generated_guard(&args) {
        let generated_dirs = generated_scan_dir_names(&request.paths, false);
        if !generated_dirs.is_empty() {
            eprintln!("{}", format_broad_generated_scan_error(&generated_dirs));
            std::process::exit(2);
        }
    }

    // P5·H2 extension (audit/h2 follow-up): refuse gpu + count/files combos on the SEARCH form
    // BEFORE the rg-passthrough early return, so this gate fires in EVERY environment.
    // `search_requires_ripgrep_passthrough`'s hard-flag list diverts all three flags to
    // `command_ripgrep_args`, which threads them into rg's own argv but has NO
    // `--gpu-device-ids` field -- with rg present the combo silently dropped the explicit GPU
    // request (exit 0, the defect), and with rg absent the passthrough block's own
    // `require_ripgrep_or_exit` exited 2 with the generic "requires the ripgrep (`rg`) backend"
    // wording. Airtight-ordering argument: this statement sits BEFORE that block, so for these
    // combos it is the first gate to fire in BOTH environments and the "refusing" message below
    // is deterministic -- CI's rg-absent test-rust-core lanes NEVER reach the rg-required gate
    // for them. The gate also closes the front-door-rewrite shadow: `SEARCH_OPTION_FIRST_FLAGS`
    // includes `--count-matches`, so the positional `tg PAT . --gpu-device-ids 0 --count-matches`
    // normalizes into `tg search ...` and never reaches `run_positional_cli`'s validator.
    // Positional behavior is otherwise UNCHANGED by this commit: `--files-without-match` /
    // `--files-with-matches` long forms are in neither rewrite list and `PositionalCli` has no
    // `-l` spelling, so those positional file-flag routes stay exactly as pre-existing P5·H2 left
    // them (the positional validator refuses `--count-matches` only); they are a separate,
    // unchanged boundary, not silently claimed as covered here. `!args.index` preserves the
    // explicit-index path's OWN `IndexFlagPolicy::Refuse` message for these flags (mirrors the
    // structured validator's carve-out below). Pure `--count-matches`/`-l` without
    // `--gpu-device-ids` stays on its honored rg passthrough (the predicate returns empty).
    if !args.index {
        let dropped = rg_passthrough_gpu_dropped_search_flags(&args);
        if !dropped.is_empty() {
            exit_gpu_dropped_on_rg_passthrough(&dropped);
        }
    }

    if search_prefers_ripgrep_passthrough(&args, &request, rg_available) {
        // search_requires_ripgrep_passthrough (checked first inside the call above) can return
        // true regardless of rg_available -- e.g. --max-depth with TG_DISABLE_RG=1. Without this
        // guard execute_ripgrep_search's Err bubbles via `?` to main()'s default Result
        // termination, which exits 1 -- indistinguishable from a genuine no-match (audit #81 #7).
        require_ripgrep_or_exit(rg_available, "this search's flag combination");
        // Bug #88/#480/#100: the implicit-walk-ceiling refusal used to live here, gated on
        // `request.path_was_implicit && (!args.globs.is_empty() || !args.file_type.is_empty())`.
        // It is now HOISTED into `execute_ripgrep_search` itself (rg_passthrough.rs) as that
        // function's first statement, before `resolve_ripgrep_binary()` -- a single chokepoint
        // every caller of `execute_ripgrep_search` passes through (this call site below, the
        // native frontdoor's `-e` arm, the positional CLI, tg-search-fast, and the PyO3 FFI
        // bridge), closing the native-frontdoor bypass audit #100 found (the frontdoor's `-e` arm
        // defaulted `paths` to `["."]` with no `path_was_implicit` record, walking unbounded with
        // zero ceiling checks). `command_ripgrep_args` below threads `request.path_was_implicit`
        // into the `RipgrepSearchArgs` passed to `execute_ripgrep_search`, so this duplicate
        // check is redundant -- deleted rather than left to drift out of sync with the hoisted
        // one. The hoisted gate also drops the `--glob`/`--file_type` requirement this block had
        // (fires on `path_was_implicit` alone, still bounded by the same 1500-file ceiling walk),
        // closing #105 FOR THE RG-PASSTHROUGH ENGINE only (a bare unfiltered implicit-path search on a huge root). SCOPE CAVEAT (audit #100 Opus gate 2026-07-10): this bounds only callers of execute_ripgrep_search; the native-CPU engine (run_native_search, reached via --json / --force-cpu / word / fixed / rg-unavailable) does NOT pass through here and remains an unbounded implicit-walk vector -- tracked as the #105 residual (generalize the ceiling before engine selection, or replicate it at the native-CPU entry).
        if args.verbose {
            emit_verbose_metadata(RoutingDecision::ripgrep());
        }
        let exit_code = execute_ripgrep_search(&command_ripgrep_args(&args, &request))?;
        if exit_code != 0 {
            std::process::exit(exit_code.max(1));
        }
        return Ok(());
    }

    // P5·H2 (audit Finding 2 hoist): refuse the structured-native count/files combos HERE, before
    // `count_search_corpus_bytes` walks the whole tree on CUDA builds (an invalid request used to
    // traverse every byte before the in-arm refusal, later in this match, exited). The predicate
    // returns empty for every HONORED route (non-JSON count/files go to rg passthrough above;
    // `--format rg --json`'s passthrough is caught by `search_requires_ripgrep_passthrough`), so
    // hoisting changes no honored path. `!args.index` keeps the explicit-index path's OWN refusal:
    // `route_search` routes `index == true` to `TrigramIndex` unconditionally, where
    // `handle_index_search`'s `IndexFlagPolicy::Refuse` for count/files (message pinned) fires --
    // reaching past that here would shadow its message. The in-arm calls this replaces were the
    // ORIGINAL landing spot and are removed as redundant; the wiring is covered end-to-end by
    // `rust_core/tests/test_h2_native_structured_refusal.rs` (CARGO_BIN_EXE_tg: each refused
    // combo must exit 2) and in-process by `native_structured_refusal_validator_returns_refusal_set`.
    if !args.index {
        if let Some(dropped) = validate_search_native_structured_refusals(&args) {
            exit_native_structured_flag_dropped(&dropped, args.json || args.ndjson);
        }
    }

    if request.paths.len() != 1 && !args.gpu_device_ids.is_empty() {
        anyhow::bail!("GPU search currently supports exactly one path root");
    }

    #[cfg(feature = "cuda")]
    let (corpus_bytes, corpus_bytes_known) =
        match count_search_corpus_bytes(&request.path_bufs(), args.no_ignore, &args.globs) {
            Ok(bytes) => (bytes, true),
            Err(err) => {
                eprintln!("warning: corpus size probe failed: {err}");
                (0, false)
            }
        };
    #[cfg(not(feature = "cuda"))]
    let (corpus_bytes, corpus_bytes_known) = (0u64, false);

    let (index_state, warm_loaded_index) = detect_warm_index_state(&args, &request);

    // Perf lever (see `native_can_serve_plain_text`): decide ONCE whether this plain-text request
    // can skip the `rg` subprocess. Computed after the passthrough/guard gates above so an
    // rg-required request has already left; a warm compatible `.tg_index` still wins inside
    // `route_search`, which checks the index before this flag is consulted.
    let plain_text = plain_text_native_request_for_search(&args, &request, stdout_is_terminal());
    let native_plain_text = native_can_serve_plain_text(&plain_text);

    #[cfg(feature = "cuda")]
    let gpu_auto_supported = request.paths.len() == 1
        && gpu_native_fallback_reason(&GpuSearchParams {
            patterns: &request.patterns,
            query: &query,
            path: request.primary_path(),
            line_number: args.line_number && !args.no_line_number,
            ignore_case: args.ignore_case,
            smart_case: args.smart_case,
            fixed_strings: args.fixed_strings,
            invert_match: args.invert_match,
            count: args.count,
            context: search_effective_context(&args),
            max_count: args.max_count,
            word_regexp: args.word_regexp,
            globs: args.globs.clone(),
            hidden: args.hidden,
            max_depth: args.max_depth,
            text: args.text,
            no_ignore: args.no_ignore,
            gpu_device_ids: &auto_gpu_ids,
            json: args.json,
            ndjson: args.ndjson,
            verbose: args.verbose,
            replace: args.replace.clone(),
            only_matching: args.only_matching,
            max_filesize: args.max_filesize.clone(),
            color: args.color.clone(),
            no_ignore_vcs: args.no_ignore_vcs,
            path_was_implicit: request.path_was_implicit,
        })
        .is_none();

    #[cfg(not(feature = "cuda"))]
    let gpu_auto_supported = false;

    #[cfg(feature = "cuda")]
    let calibration = load_search_routing_calibration(Path::new(request.primary_path()));
    #[cfg(not(feature = "cuda"))]
    let calibration: Option<SearchRoutingCalibration> = None;

    #[cfg(feature = "cuda")]
    let gpu_available = auto_gpu_available_for_routing();
    #[cfg(not(feature = "cuda"))]
    let gpu_available = false;

    let decision = route_search(
        &SearchRoutingConfig {
            explicit_index: args.index,
            explicit_gpu_device_ids: !args.gpu_device_ids.is_empty(),
            force_cpu: args.force_cpu,
            ast_command: false,
            json: args.json,
            ndjson: args.ndjson,
            rg_available,
            corpus_bytes,
            corpus_bytes_known,
            gpu_auto_supported,
            prefer_rg_passthrough: search_has_context(&args) && !args.json && !args.ndjson,
            pcre2: args.pcre2,
            native_plain_text,
        },
        calibration.as_ref(),
        index_state,
        gpu_available,
    );

    match decision.selection {
        BackendSelection::TrigramIndex => {
            handle_index_search(&args, &request, &query, warm_loaded_index)
        }
        BackendSelection::NativeGpu => {
            let gpu_device_ids = if args.gpu_device_ids.is_empty() {
                &auto_gpu_ids
            } else {
                args.gpu_device_ids.as_slice()
            };
            let params = GpuSearchParams {
                patterns: &request.patterns,
                query: &query,
                path: request.primary_path(),
                line_number: args.line_number && !args.no_line_number,
                ignore_case: args.ignore_case,
                smart_case: args.smart_case,
                fixed_strings: args.fixed_strings,
                invert_match: args.invert_match,
                count: args.count,
                context: search_effective_context(&args),
                max_count: args.max_count,
                word_regexp: args.word_regexp,
                globs: args.globs.clone(),
                hidden: args.hidden,
                max_depth: args.max_depth,
                text: args.text,
                no_ignore: args.no_ignore,
                gpu_device_ids,
                json: args.json,
                ndjson: args.ndjson,
                verbose: args.verbose,
                replace: args.replace.clone(),
                only_matching: args.only_matching,
                max_filesize: args.max_filesize.clone(),
                color: args.color.clone(),
                no_ignore_vcs: args.no_ignore_vcs,
                path_was_implicit: request.path_was_implicit,
            };

            #[cfg(feature = "cuda")]
            if decision.reason == RoutingDecision::native_gpu_auto().reason {
                let fallback_decision =
                    RoutingDecision::native_cpu_gpu_fallback(rg_available, structured_output);
                let rg_fallback = fallback_decision
                    .allow_rg_fallback
                    .then(|| command_ripgrep_args(&args, &request));
                return handle_auto_gpu_search(
                    params,
                    native_search_config_for_command(
                        &args,
                        &request.patterns[0],
                        &request.paths,
                        request.path_was_implicit,
                        fallback_decision,
                    ),
                    rg_fallback,
                );
            }

            handle_gpu_search(params)
        }
        BackendSelection::NativeCpu => {
            if decision.reason
                == RoutingDecision::native_cpu_gpu_fallback(rg_available, structured_output).reason
            {
                eprintln!(
                    "warning: CUDA is unavailable: no usable GPU devices were found; falling back to native CPU search; this CPU fallback output is not GPU acceleration proof"
                );
            }
            if args.verbose {
                emit_verbose_metadata(decision);
            }

            let rg_fallback = decision
                .allow_rg_fallback
                .then(|| command_ripgrep_args(&args, &request));

            if request.patterns.len() > 1 {
                let (matches, incomplete_paths) = match collect_native_multi_pattern_matches(
                    &request.patterns,
                    native_search_config_for_command(
                        &args,
                        &request.patterns[0],
                        &request.paths,
                        request.path_was_implicit,
                        decision,
                    ),
                ) {
                    Ok(collected) => collected,
                    Err(err) => {
                        exit_json_search_runtime_error_if_needed(args.json, args.ndjson, &err);
                        return Err(err);
                    }
                };
                return emit_multi_pattern_native_results(
                    NativeSearchOutputOptions {
                        decision,
                        query: &query,
                        path: &path_display,
                        requested_gpu_device_ids: &[],
                        json: args.json,
                        ndjson: args.ndjson,
                        count: args.count,
                        line_number: args.line_number && !args.no_line_number,
                        path_was_implicit: request.path_was_implicit,
                    },
                    matches,
                    incomplete_paths,
                );
            }

            run_native_search_with_optional_rg_fallback(
                native_search_config_for_command(
                    &args,
                    &request.patterns[0],
                    &request.paths,
                    request.path_was_implicit,
                    decision,
                ),
                rg_fallback,
            )
        }
        BackendSelection::Ripgrep => {
            if args.verbose {
                emit_verbose_metadata(decision);
            }

            let exit_code = execute_ripgrep_search(&command_ripgrep_args(&args, &request))?;
            if exit_code != 0 {
                std::process::exit(exit_code.max(1));
            }
            Ok(())
        }
        _ => anyhow::bail!("unsupported search routing decision: {}", decision.reason),
    }
}

fn resolve_index_path(search_path: &str) -> PathBuf {
    let root = Path::new(search_path);
    if root.is_file() {
        root.parent().unwrap_or(Path::new(".")).join(".tg_index")
    } else {
        root.join(".tg_index")
    }
}

/// Persists `index` to `index_path` under the write-serializing index lock -- the ONLY place in
/// the search path that acquires it. Readers (`TrigramIndex::load`, `detect_warm_index_state`)
/// never take this lock; a wrong lock on the read path would deadlock (or at minimum
/// unnecessarily serialize) EVERY `tg` invocation against a warm index (audit #138 item #2
/// critical trap). Per the Backend Fail-Closed Contract (AGENTS.md), a failed PERSIST must never
/// fail the SEARCH: on a lock-acquire timeout (another writer holding it past the 12s budget) or
/// an actual write/rename failure, this warns to stderr and returns without persisting -- the
/// caller's in-memory `index` (already fully built) is unaffected and is what actually answers
/// the search.
fn save_index_locked(index: &TrigramIndex, index_path: &Path, verbose: bool) {
    match tensor_grep_rs::index_lock::IndexLockGuard::acquire(index_path) {
        Ok(_guard) => {
            if let Err(e) = index.save(index_path) {
                eprintln!("[index] warning: failed to persist index: {e}");
            } else if verbose {
                eprintln!("[index] persisted index to {}", index_path.display());
            }
        }
        Err(timeout) => {
            eprintln!(
                "[index] warning: {timeout}; skipping persistence for this run (search \
                 results are unaffected -- a later invocation will retry)"
            );
        }
    }
}

fn handle_index_search(
    args: &SearchArgs,
    request: &ResolvedSearchRequest,
    query: &str,
    preloaded_index: Option<TrigramIndex>,
) -> anyhow::Result<()> {
    if request.paths.len() != 1 {
        anyhow::bail!("index search currently supports exactly one path root");
    }

    // Backend Fail-Closed Contract (audit H1a, superseded by audit fix #1 2026-07-11): H1a
    // originally hand-listed the 6 flags below because route_search() (routing.rs) selects
    // TrigramIndex for --index before any compatibility checks run, and run_index_query()
    // only ever reads a handful of fields -- every OTHER search flag was silently dropped
    // instead of honored or refused (e.g. --index -v used to return the NON-inverted set with
    // exit 0). `index_flag_violations` (above `detect_warm_index_state`) replaces the ad-hoc
    // list with an exhaustive per-field classification covering every `SearchArgs` field, not
    // just these 6, and is shared with `detect_warm_index_state`'s warm-auto-routing gate so
    // the two can't drift apart again. Deliberately excludes the pattern-length and
    // non-ASCII-ignore-case checks detect_warm_index_state also has -- those (H1b/H1c) are
    // handled as a transparent full-scan fallback inside
    // TrigramIndex::search/fixed_string_candidate_selection instead of a refusal, since the
    // index can still honor them correctly, just without the trigram prefilter.
    let unsupported_with_index = index_flag_violations(args, request);
    if !unsupported_with_index.is_empty() {
        anyhow::bail!(
            "--index does not support {} yet; rerun without --index (or without the \
             flag(s) above) to search without the trigram index accelerator",
            unsupported_with_index.join(", ")
        );
    }

    let search_path = Path::new(request.primary_path());
    if !search_path.exists() {
        anyhow::bail!(
            "index search path does not exist: {}",
            search_path.display()
        );
    }

    let index_path = resolve_index_path(request.primary_path());

    // M17 (audit-m17): never serve a mismatched root. The preloaded index arrived via
    // `detect_warm_index_state`'s routing gate, which now marks a root-mismatched index
    // stale (so this arm is normally never reached with one); the filter is the
    // defense-in-depth invariant that `handle_index_search` itself never serves a wrong
    // tree even if a future caller bypasses that gate. A filtered-out index falls through
    // to the disk-load branch below, which repeats the check and full-rebuilds.
    let index = if let Some(loaded) = preloaded_index.filter(|loaded| {
        loaded
            .root_servability_reason(Path::new(request.primary_path()))
            .is_none()
    }) {
        // audit #138 item #3 (load-once): `detect_warm_index_state` already loaded this index
        // (and, by construction, only ever hands one to us via the `should_route_to_index()` /
        // warm_index() routing arm, which requires `!is_stale`) -- reuse it instead of reading
        // and re-deserializing the same `.tg_index` file a second time.
        if args.verbose {
            eprintln!(
                "[index] loaded cached index: {} files, {} trigrams",
                loaded.file_count(),
                loaded.trigram_count()
            );
        }
        loaded
    } else if index_path.exists() {
        let loaded = match TrigramIndex::load(&index_path) {
            Ok(idx) => idx,
            Err(e) => {
                eprintln!("[index] warning: failed to load index: {e}, rebuilding...");
                let started = Instant::now();
                let fresh = TrigramIndex::build_with_options(
                    Path::new(request.primary_path()),
                    args.no_ignore,
                )?;
                save_index_locked(&fresh, &index_path, args.verbose);
                if args.verbose {
                    eprintln!(
                        "[index] full rebuild complete in {:?}: {} files, {} trigrams, {} postings",
                        started.elapsed(),
                        fresh.file_count(),
                        fresh.trigram_count(),
                        fresh.total_postings()
                    );
                }
                return run_index_query(args, request, query, &fresh);
            }
        };
        // M17 (audit-m17): the ROOT check runs BEFORE `staleness_reason`/incremental
        // update -- the per-file walk asks the STORED root for its health, so on a
        // mismatched tree it must not be the decision maker. A mismatch full-rebuilds from
        // the QUERY root (never incremental: file identity from a different tree makes
        // incremental both wasteful and semantically confusing), disclosing the reason
        // through the same "[index] stale" channel as every other rebuild decision.
        if let Some(reason) = loaded.root_servability_reason(Path::new(request.primary_path())) {
            if args.verbose {
                eprintln!("[index] stale: {reason}");
            }
            let started = Instant::now();
            let fresh = TrigramIndex::build_with_options(
                Path::new(request.primary_path()),
                args.no_ignore,
            )?;
            save_index_locked(&fresh, &index_path, args.verbose);
            if args.verbose {
                eprintln!(
                    "[index] full rebuild complete in {:?}: {} files, {} trigrams, {} postings",
                    started.elapsed(),
                    fresh.file_count(),
                    fresh.trigram_count(),
                    fresh.total_postings()
                );
            }
            return run_index_query(args, request, query, &fresh);
        }
        if let Some(reason) = loaded.staleness_reason(args.no_ignore) {
            if args.verbose {
                eprintln!("[index] stale: {reason}");
            }
            let started = Instant::now();
            let update = loaded.rebuild_incremental_with_options(
                Path::new(request.primary_path()),
                args.no_ignore,
            )?;
            save_index_locked(&update.index, &index_path, args.verbose);
            if args.verbose {
                eprintln!(
                    "[index] incremental update complete in {:?}: reused {} unchanged files, added {}, modified {}, deleted {}; {} files, {} trigrams, {} postings",
                    started.elapsed(),
                    update.stats.reused_files,
                    update.stats.added_files,
                    update.stats.modified_files,
                    update.stats.deleted_files,
                    update.index.file_count(),
                    update.index.trigram_count(),
                    update.index.total_postings()
                );
            }
            update.index
        } else {
            if args.verbose {
                eprintln!(
                    "[index] loaded cached index: {} files, {} trigrams",
                    loaded.file_count(),
                    loaded.trigram_count()
                );
            }
            loaded
        }
    } else {
        if args.verbose {
            eprintln!(
                "[index] full rebuild: building index for {}...",
                request.primary_path()
            );
        }
        let started = Instant::now();
        let fresh =
            TrigramIndex::build_with_options(Path::new(request.primary_path()), args.no_ignore)?;
        save_index_locked(&fresh, &index_path, args.verbose);
        if args.verbose {
            eprintln!(
                "[index] full rebuild complete in {:?}: {} files, {} trigrams, {} postings",
                started.elapsed(),
                fresh.file_count(),
                fresh.trigram_count(),
                fresh.total_postings()
            );
        }
        fresh
    };

    run_index_query(args, request, query, &index)
}

fn run_index_query(
    args: &SearchArgs,
    request: &ResolvedSearchRequest,
    query: &str,
    index: &TrigramIndex,
) -> anyhow::Result<()> {
    if args.verbose {
        emit_verbose_metadata(RoutingDecision::warm_index());
    }

    let include_pattern_metadata = request.patterns.len() > 1;
    let mut matches = Vec::new();
    for (pattern_id, pattern) in request.patterns.iter().enumerate() {
        // H1e (audit): resolve smart-case (-S) per pattern before querying the index.
        // -S is NOT diverted to ripgrep in JSON/ndjson mode (search_requires_ripgrep_
        // passthrough gates it behind !json && !ndjson), so it reaches the index here;
        // passing only args.ignore_case (false for -S) would search case-sensitively and
        // silently miss uppercase matches an all-lowercase -S pattern must find. Honoring
        // it (smart-case IS index-doable) rather than refusing avoids a UX regression, and
        // reuses the same ignore_case path -- including the H1b/H1c full-scan safety nets
        // in index.rs -- for the resolved case. This single chokepoint covers BOTH explicit
        // --index and warm auto-routing (both reach run_index_query).
        let ignore_case = args.ignore_case
            || (args.smart_case && smart_case_pattern_is_case_insensitive(pattern));
        let results = index.search(pattern, ignore_case, args.fixed_strings)?;
        // EXEMPT from the raw-bytes/base64-fallback treatment (task #266): `TrigramIndex`
        // persists and returns plain `String`s, so `result.text` is already guaranteed valid
        // UTF-8 by construction -- there is no raw byte source here to preserve losslessly, and
        // extending the persisted index format itself is a materially different, larger change
        // than this fix's scope (the shared native walk emitter).
        matches.extend(results.into_iter().map(|result| {
            let (text, bytes, raw) = guaranteed_utf8_match_fields(result.text);
            // M17 F3: the index dereferenced through the canonical root (sound), but the
            // EMITTED path re-projects back through the QUERY's original spelling so a
            // relative / differently-spelled query sees its own path space (tree/a.txt),
            // matching what the native/rg routes emit for the same invocation.
            let file = index
                .display_path(Path::new(request.primary_path()), &result.file)
                .to_string_lossy()
                .into_owned();
            SearchMatchJson {
                file,
                line: result.line,
                text,
                bytes,
                raw,
                range: None,
                meta_variables: None,
                pattern_id: include_pattern_metadata.then_some(pattern_id),
                pattern_text: include_pattern_metadata.then(|| pattern.clone()),
            }
        }));
    }

    if args.json {
        return emit_json_search_results(
            RoutingDecision::warm_index(),
            query,
            request.primary_path(),
            &[],
            matches,
            // Task 276: this route observed no walk of its own, so it cannot report a
            // count. `None` means "cannot report", NEVER "complete".
            None,
            request.path_was_implicit,
        );
    }

    if args.ndjson {
        return emit_ndjson_search_results(
            RoutingDecision::warm_index(),
            query,
            request.primary_path(),
            &[],
            matches,
            // Task 276: this route observed no walk of its own, so it cannot report a
            // count. `None` means "cannot report", NEVER "complete".
            None,
            request.path_was_implicit,
        );
    }

    if args.count {
        // Audit fix #1 must-fix (Opus adversarial gate on PR #541): emit per-file `path:count`
        // via the SAME emit_count_search_matches the sibling native aggregate path already uses
        // (emit_multi_pattern_native_results below), NOT a bare aggregate total. The old
        // `println!("{unique_count}")` printed a single number (e.g. `3`) while every other count
        // emitter -- `rg -c`, the native CPU engine's append_count_output_bytes, and
        // emit_count_search_matches -- prints per-file counts. Because `count` is (correctly) an
        // Honor flag, the WARM auto-index path reaches here too, so a plain `tg search -c <pat>
        // <dir>` silently changed output shape (per-file -> bare aggregate) the moment a `.tg_index`
        // happened to exist -- exactly the silent-wrong-shape-with-exit-0 this validator exists to
        // prevent. emit_count_search_matches omits zero-count files, byte-matching `rg -c` (the
        // rg-compat target); the native CPU engine's separate grep-style zero-count emission is its
        // own pre-existing divergence, deliberately not replicated on the index fast path.
        emit_count_search_matches(request.primary_path(), &matches)?;
        // fold-in (a): rg exit-parity. `rg -c` (and the native CPU / multi-pattern engines) exit 1
        // on zero matches; run_index_query never did, so `--index --count` on a no-match query
        // exited 0 -- indistinguishable from a successful search. `matches.is_empty()` is
        // equivalent to the old `unique_count == 0` (unique_line_matches never empties a non-empty
        // set), and emit_count_search_matches prints nothing for an empty set on a dir target,
        // matching `rg -c`'s empty-stdout-plus-exit-1 no-match contract.
        if matches.is_empty() {
            std::process::exit(1);
        }
        return Ok(());
    }

    // fold-in (b): thread `-N`/`--no-line-number` the same way the native/rg-passthrough
    // configs already do (`args.line_number && !args.no_line_number`, see e.g.
    // native_search_config_for_command) instead of emit_plain_search_matches's hardcoded
    // `true`, which made `-N` a no-op on the index path.
    emit_plain_search_matches_with_line_number(
        request.primary_path(),
        &matches,
        args.line_number && !args.no_line_number,
    )?;

    // fold-in (a): see the --count arm above for why this matches native/GPU exit-parity.
    if matches.is_empty() {
        std::process::exit(1);
    }

    Ok(())
}

#[derive(Serialize)]
struct SearchResultJson<'a> {
    version: u32,
    routing_backend: &'static str,
    routing_reason: &'static str,
    sidecar_used: bool,
    requested_gpu_device_ids: Vec<i32>,
    routing_gpu_device_ids: Vec<i32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    gpu_evidence_status: Option<&'static str>,
    #[serde(skip_serializing_if = "Option::is_none")]
    gpu_proof: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    native_gpu_unavailable: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    not_gpu_proof_reason: Option<String>,
    query: &'a str,
    path: &'a str,
    total_files: usize,
    total_matches: usize,
    matched_file_paths: Vec<String>,
    match_counts_by_file: std::collections::BTreeMap<String, usize>,
    matches: Vec<SearchMatchJson>,
    // Task 276 (#314). This is the SECOND envelope -- parallel to the native one at
    // `native_search.rs:2489-2491` but, until now, carrying none of its disclosure.
    // Omit-when-complete, so a complete search stays byte-identical.
    #[serde(skip_serializing_if = "Option::is_none")]
    result_incomplete: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    incomplete_reason_class: Option<&'static str>,
    #[serde(skip_serializing_if = "Option::is_none")]
    incomplete_paths_count: Option<usize>,
    // v1.101.22 dogfood: "PATH note is stderr-only -- bare `--json` still returns empty aggregate
    // JSON with no warnings/notes field; agents that ignore stderr can miss it."
    //
    // THE BINARY MUST STAMP THIS, not Python. `--json` is a supported trigger for native
    // delegation and `_run_native_tg_search` STREAMS this document straight through
    // (`_streaming_passthrough_returncode`), so Python never holds it and cannot inject a field
    // without buffering -- which would break streaming for large result sets to fix a zero-match
    // case. Same reasoning that put the broad-scan refusal envelope on this side in #867: the
    // surface that owns the document owns its disclosure.
    //
    // DELIBERATELY NOT part of the incompleteness family above. A search whose PATH defaulted to
    // the cwd RAN TO COMPLETION -- it answered a narrower question than the caller may have meant.
    // Setting `result_incomplete` would be false AND would flip the exit code to 2, breaking the
    // closed 0/1/2 contract. Advisory only; exit stays 1.
    //
    // Omit-when-inapplicable, matching every field above: absent when the caller gave an explicit
    // PATH, so an existing consumer's payload stays byte-identical.
    #[serde(skip_serializing_if = "Option::is_none")]
    path_was_defaulted: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    scope_note: Option<&'static str>,
}

/// The `--ndjson` TERMINAL SUMMARY record (task 276 slice B2b).
///
/// `--ndjson` emitted one record per match and nothing else, so there was nowhere for an
/// incompleteness marker to live -- not a missing field, a missing RECORD. Emitted on EVERY
/// run, complete or not: a summary that appears only when something went wrong is one a
/// streaming reader never learns to expect. `type` is the discriminator.
#[derive(Serialize)]
struct SearchSummaryNdjson {
    #[serde(rename = "type")]
    record_type: &'static str,
    version: u32,
    total_matches: usize,
    #[serde(skip_serializing_if = "Option::is_none")]
    result_incomplete: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    incomplete_reason_class: Option<&'static str>,
    #[serde(skip_serializing_if = "Option::is_none")]
    incomplete_paths_count: Option<usize>,
    // Task #26, same pair as `SearchResultJson`. A streaming reader that never sees a match record
    // gets ONLY this summary, so leaving the scope disclosure out of it makes `--ndjson` the
    // quietest surface of all -- an empty stream followed by a summary saying nothing.
    #[serde(skip_serializing_if = "Option::is_none")]
    path_was_defaulted: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    scope_note: Option<&'static str>,
}

#[cfg(feature = "cuda")]
#[derive(Serialize)]
struct GpuNativeSearchResultJson<'a> {
    version: u32,
    routing_backend: &'static str,
    routing_reason: &'static str,
    sidecar_used: bool,
    query: &'a str,
    path: &'a str,
    total_matches: usize,
    total_files: usize,
    requested_gpu_device_ids: Vec<i32>,
    routing_gpu_device_ids: Vec<i32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    gpu_evidence_status: Option<&'static str>,
    #[serde(skip_serializing_if = "Option::is_none")]
    gpu_proof: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    native_gpu_unavailable: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    not_gpu_proof_reason: Option<String>,
    // Task 316: the same incompleteness triple the CPU envelopes carry, filled by the SHARED
    // `incomplete_envelope_fields` so the GPU route cannot drift from them. `skip_serializing_if`
    // keeps a complete GPU scan byte-identical to the pre-316 payload -- the omit-when-complete
    // convention `result_incomplete` already follows everywhere else.
    #[serde(skip_serializing_if = "Option::is_none")]
    result_incomplete: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    incomplete_reason_class: Option<&'static str>,
    #[serde(skip_serializing_if = "Option::is_none")]
    incomplete_paths_count: Option<usize>,
    // Task #26, third and last member of the native `--json` envelope population (the others are
    // `SearchResultJson` and `SearchSummaryNdjson`). Enumerated, not sampled: this symptom has
    // taken four fixes precisely because each one closed the route that happened to be reported.
    #[serde(skip_serializing_if = "Option::is_none")]
    path_was_defaulted: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    scope_note: Option<&'static str>,
    pipeline: &'a GpuPipelineStats,
    matches: Vec<SearchMatchJson>,
}

#[derive(Serialize)]
struct ApplyVerifyJson<'a> {
    version: u32,
    routing_backend: &'static str,
    routing_reason: &'static str,
    sidecar_used: bool,
    checkpoint: Option<&'a CheckpointCreateSummary>,
    audit_manifest: Option<&'a AuditManifestSummary>,
    plan: &'a tensor_grep_rs::backend_ast::RewritePlan,
    verification: Option<&'a tensor_grep_rs::backend_ast::VerifyResult>,
    validation: Option<&'a ValidationSummary>,
    #[serde(skip_serializing_if = "Option::is_none")]
    rollback: Option<&'a ValidationRollbackSummary>,
}

#[derive(Serialize)]
struct RewriteDiffJson<'a> {
    version: u32,
    routing_backend: &'static str,
    routing_reason: &'static str,
    sidecar_used: bool,
    plan: &'a tensor_grep_rs::backend_ast::RewritePlan,
    diff: String,
}

#[derive(Serialize)]
struct BatchApplyVerifyJson<'a> {
    version: u32,
    routing_backend: &'static str,
    routing_reason: &'static str,
    sidecar_used: bool,
    checkpoint: Option<&'a CheckpointCreateSummary>,
    audit_manifest: Option<&'a AuditManifestSummary>,
    plan: &'a BatchRewritePlan,
    verification: Option<&'a tensor_grep_rs::backend_ast::VerifyResult>,
    validation: Option<&'a ValidationSummary>,
    #[serde(skip_serializing_if = "Option::is_none")]
    rollback: Option<&'a ValidationRollbackSummary>,
}

#[derive(Serialize)]
struct BatchRewriteDiffJson<'a> {
    version: u32,
    routing_backend: &'static str,
    routing_reason: &'static str,
    sidecar_used: bool,
    plan: &'a BatchRewritePlan,
    diff: String,
}

#[derive(Debug, Clone, Serialize)]
struct CheckpointCreateSummary {
    checkpoint_id: String,
    mode: String,
    root: String,
    scope: String,
    original_path: String,
    created_at: String,
    file_count: usize,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct CheckpointIndexRecord {
    version: u32,
    checkpoint_id: String,
    mode: String,
    root: String,
    created_at: String,
    file_count: usize,
}

#[derive(Debug, Clone, Serialize)]
struct CheckpointMetadata {
    version: u32,
    checkpoint_id: String,
    mode: String,
    root: String,
    scope: String,
    original_path: String,
    created_at: String,
    file_count: usize,
    entries: BTreeMap<String, bool>,
}

#[derive(Debug, Clone, Serialize)]
struct ValidationSummary {
    success: bool,
    commands: Vec<ValidationCommandResult>,
    /// True when at least one of --lint-cmd/--test-cmd had more edited-file targets than
    /// --max-validation-targets allowed and some targets were skipped (audit #34). Fail-closed
    /// VISIBLE: the cap silently dropping targets would otherwise look like a clean pass.
    validation_targets_truncated: bool,
    /// The real number of edited-file validation targets discovered before any cap was applied
    /// (the max across --lint-cmd/--test-cmd, since both usually see the same edited-file set).
    validation_targets_total: usize,
}

#[derive(Debug, Clone, Serialize)]
struct ValidationRollbackSummary {
    triggered_by: &'static str,
    success: bool,
    files_restored: Vec<String>,
    errors: Vec<String>,
}

#[derive(Debug, Clone, Serialize)]
struct ValidationCommandResult {
    kind: &'static str,
    command: String,
    success: bool,
    exit_code: Option<i32>,
    stdout: String,
    stderr: String,
}

#[derive(Debug, Clone, Serialize)]
struct AuditManifestSummary {
    path: String,
    file_count: usize,
    applied_edit_count: usize,
    signed: bool,
    signature_kind: Option<&'static str>,
}

#[derive(Debug, Clone, Serialize)]
struct RewriteAuditManifest {
    version: u32,
    kind: &'static str,
    created_at: String,
    lang: String,
    path: String,
    plan_total_edits: usize,
    applied_edit_ids: Vec<String>,
    previous_manifest_sha256: Option<String>,
    checkpoint: Option<CheckpointCreateSummary>,
    validation: Option<ValidationSummary>,
    files: Vec<RewriteAuditManifestFile>,
    #[serde(skip_serializing_if = "Option::is_none")]
    manifest_sha256: Option<String>,
    signature: Option<AuditManifestSignature>,
}

#[derive(Debug, Clone, Serialize)]
struct RewriteAuditManifestFile {
    path: String,
    edit_ids: Vec<String>,
    before_sha256: String,
    after_sha256: String,
}

#[derive(Debug, Clone, Serialize)]
struct AuditManifestSignature {
    kind: &'static str,
    key_path: String,
    value: String,
}

#[derive(Debug, Clone, Deserialize)]
struct RewriteAuditManifestRead {
    kind: String,
    previous_manifest_sha256: Option<String>,
    manifest_sha256: Option<String>,
    signature: Option<AuditManifestSignatureRead>,
}

#[derive(Debug, Clone, Deserialize)]
struct AuditManifestSignatureRead {
    kind: String,
    // Retained for deserialization/forward-compatibility but intentionally NOT used for
    // verification: the key must be supplied out-of-band via --signing-key, never read
    // from the manifest being verified (audit S2).
    #[allow(dead_code)]
    key_path: String,
    value: String,
}

#[derive(Debug, Clone, Serialize)]
struct AuditManifestVerifyChecks {
    digest_valid: bool,
    chain_valid: bool,
    signature_valid: bool,
}

#[derive(Debug, Clone, Serialize)]
struct AuditManifestVerifyJson {
    version: u32,
    routing_backend: &'static str,
    routing_reason: &'static str,
    sidecar_used: bool,
    manifest_path: String,
    signing_key_path: Option<String>,
    previous_manifest_path: Option<String>,
    kind: Option<String>,
    manifest_sha256: Option<String>,
    previous_manifest_sha256: Option<String>,
    checks: AuditManifestVerifyChecks,
    signature_kind: Option<String>,
    valid: bool,
    errors: Vec<String>,
}

#[derive(Debug, Clone)]
struct BatchRewriteConfig {
    rewrites: Vec<BatchRewriteRule>,
    verify: bool,
}

/// `text`/`bytes` mirror `NativeJsonMatch`'s rg-parity protocol (task #266): valid-UTF-8 content
/// in `text`, otherwise a base64 `bytes` fallback -- exactly one present, computed via
/// `native_json_text_fields`. `raw` is the byte-exact source of truth every producer must
/// populate (`#[serde(skip)]`: never itself part of the JSON/NDJSON wire shape) -- both the
/// plain-text writer (`emit_plain_search_matches_with_line_number`) and the dedup key
/// (`unique_line_matches`) read `raw` directly instead of `text`, so a producer that supplies
/// genuinely non-UTF-8 bytes (currently: the multi-pattern native path, `NativeSearchMatch`/
/// `NativeMultiPatternMatch`) is never silently corrupted OR silently deduplicated against an
/// unrelated match that merely shares the same `text: None`. A producer whose own source is
/// ALWAYS valid UTF-8 by construction (TrigramIndex, AST, the GPU sidecar/native paths) simply
/// sets `raw` to `text`'s own UTF-8 bytes -- `native_json_text_fields` then always returns
/// `Some(text)`/`None`, identical to this struct's pre-#266 behavior for those producers.
#[derive(Debug, Clone, Serialize, PartialEq, Eq, PartialOrd, Ord)]
struct SearchMatchJson {
    file: String,
    line: usize,
    #[serde(skip_serializing_if = "Option::is_none")]
    text: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    bytes: Option<String>,
    #[serde(skip)]
    raw: Vec<u8>,
    #[serde(skip_serializing_if = "Option::is_none")]
    range: Option<SearchRangeJson>,
    #[serde(rename = "metaVariables", skip_serializing_if = "Option::is_none")]
    meta_variables: Option<SearchMetaVariablesJson>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pattern_id: Option<usize>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pattern_text: Option<String>,
}

/// Builds the `text`/`bytes`/`raw` trio for a `SearchMatchJson` (or `SearchMatchNdjson`) from a
/// producer whose own source `String` is ALWAYS valid UTF-8 by construction -- TrigramIndex
/// (persists `String`s), AST (`std::fs::read_to_string` fails closed on invalid UTF-8 before this
/// point), and the GPU sidecar/native paths (JSON-deserialized, itself UTF-8). `native_json_text_
/// fields` always returns `Some(text)`/`None` for these, so this is equivalent to (and clearer
/// than) routing them through that helper -- it exists so each of those 4 call sites states its
/// own exemption inline instead of repeating the same `native_json_text_fields(s.as_bytes())`
/// call whose `None` branch can never actually fire for them.
fn guaranteed_utf8_match_fields(text: String) -> (Option<String>, Option<String>, Vec<u8>) {
    let raw = text.clone().into_bytes();
    (Some(text), None, raw)
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq, PartialOrd, Ord)]
struct SearchRangeJson {
    #[serde(rename = "byteOffset")]
    byte_offset: SearchByteOffsetJson,
    start: SearchPositionJson,
    end: SearchPositionJson,
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq, PartialOrd, Ord)]
struct SearchByteOffsetJson {
    start: usize,
    end: usize,
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq, PartialOrd, Ord)]
struct SearchPositionJson {
    line: usize,
    column: usize,
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq, PartialOrd, Ord)]
struct SearchMetaVariablesJson {
    #[serde(skip_serializing_if = "BTreeMap::is_empty")]
    single: BTreeMap<String, SearchMetaVariableJson>,
    #[serde(skip_serializing_if = "BTreeMap::is_empty")]
    multi: BTreeMap<String, Vec<SearchMetaVariableJson>>,
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq, PartialOrd, Ord)]
struct SearchMetaVariableJson {
    text: String,
    range: SearchRangeJson,
}

#[derive(Debug, Clone)]
struct AstSourceContext {
    line_starts: Vec<usize>,
}

#[derive(Serialize)]
struct SearchMatchNdjson<'a> {
    version: u32,
    routing_backend: &'static str,
    routing_reason: &'static str,
    sidecar_used: bool,
    requested_gpu_device_ids: Vec<i32>,
    routing_gpu_device_ids: Vec<i32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    gpu_evidence_status: Option<&'static str>,
    #[serde(skip_serializing_if = "Option::is_none")]
    gpu_proof: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    native_gpu_unavailable: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    not_gpu_proof_reason: Option<String>,
    query: &'a str,
    path: &'a str,
    file: &'a str,
    line: usize,
    // Same text/bytes protocol as `SearchMatchJson` above (task #266).
    #[serde(skip_serializing_if = "Option::is_none")]
    text: Option<&'a str>,
    #[serde(skip_serializing_if = "Option::is_none")]
    bytes: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pattern_id: Option<usize>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pattern_text: Option<&'a str>,
}

#[derive(Deserialize)]
struct GpuSidecarSearchPayload {
    total_matches: usize,
    total_files: usize,
    matches: Vec<GpuSidecarSearchMatch>,
    #[serde(default)]
    routing_gpu_device_ids: Vec<u32>,
}

#[derive(Deserialize)]
struct GpuSidecarSearchMatch {
    file: String,
    line_number: usize,
    text: String,
    #[serde(default)]
    pattern_id: Option<usize>,
    #[serde(default)]
    pattern_text: Option<String>,
}

/// The PATH `tg run` will search, paired with whether the caller actually supplied one.
///
/// Returned as a PAIR, not as two functions. A sibling `run_search_path_was_implicit` would have
/// to re-implement the same `pattern_option`-dependent positional-index selection, and two copies
/// of one rule drifting apart is precisely the failure task #26 exists to close -- the "bare
/// search is silent" symptom took four separate fixes because four routes each derived the same
/// fact independently.
fn run_search_path_with_origin(args: &RunArgs) -> (&str, bool) {
    let explicit = if args.pattern_option.is_some() {
        args.positional.first()
    } else {
        args.positional.get(1)
    };
    (
        explicit.map(String::as_str).unwrap_or("."),
        explicit.is_none(),
    )
}

fn run_batch_path(args: &RunArgs) -> anyhow::Result<&str> {
    if args.positional.len() > 1 {
        anyhow::bail!("tg run --batch-rewrite accepts exactly one PATH argument")
    }

    Ok(args.positional.first().map(String::as_str).unwrap_or("."))
}

fn run_pattern(args: &RunArgs) -> anyhow::Result<&str> {
    if let Some(pattern) = args.pattern_option.as_deref() {
        if args.positional.len() > 1 {
            anyhow::bail!("tg run --pattern accepts at most one positional PATH argument");
        }
        return Ok(pattern);
    }
    match args.positional.first().map(String::as_str) {
        Some(pattern) => Ok(pattern),
        None => anyhow::bail!(
            "tg run requires --pattern <PATTERN> or positional PATTERN unless --batch-rewrite <config.json> is provided"
        ),
    }
}

fn build_search_line_starts(source: &str) -> Vec<usize> {
    let mut line_starts = vec![0];
    for (index, byte) in source.as_bytes().iter().enumerate() {
        if *byte == b'\n' {
            line_starts.push(index + 1);
        }
    }
    line_starts
}

fn zero_based_position_for_byte(line_starts: &[usize], byte_offset: usize) -> SearchPositionJson {
    let line_index = line_starts
        .partition_point(|start| *start <= byte_offset)
        .saturating_sub(1);
    SearchPositionJson {
        line: line_index,
        column: byte_offset - line_starts[line_index],
    }
}

fn search_range_json(
    line_starts: &[usize],
    byte_range: &std::ops::Range<usize>,
) -> SearchRangeJson {
    SearchRangeJson {
        byte_offset: SearchByteOffsetJson {
            start: byte_range.start,
            end: byte_range.end,
        },
        start: zero_based_position_for_byte(line_starts, byte_range.start),
        end: zero_based_position_for_byte(line_starts, byte_range.end),
    }
}

fn search_meta_variables_json(
    meta_variables: &AstMetaVariables,
    line_starts: &[usize],
) -> Option<SearchMetaVariablesJson> {
    if meta_variables.single.is_empty() && meta_variables.multi.is_empty() {
        return None;
    }

    let single = meta_variables
        .single
        .iter()
        .map(|(name, capture)| {
            (
                name.clone(),
                SearchMetaVariableJson {
                    text: capture.text.clone(),
                    range: search_range_json(line_starts, &capture.byte_range),
                },
            )
        })
        .collect();
    let multi = meta_variables
        .multi
        .iter()
        .map(|(name, captures)| {
            (
                name.clone(),
                captures
                    .iter()
                    .map(|capture| SearchMetaVariableJson {
                        text: capture.text.clone(),
                        range: search_range_json(line_starts, &capture.byte_range),
                    })
                    .collect(),
            )
        })
        .collect();

    Some(SearchMetaVariablesJson { single, multi })
}

fn ast_match_to_search_json(
    matched: &AstMatch,
    source_contexts: &mut BTreeMap<PathBuf, AstSourceContext>,
) -> anyhow::Result<SearchMatchJson> {
    if !source_contexts.contains_key(&matched.file) {
        let source = std::fs::read_to_string(&matched.file).with_context(|| {
            format!("failed to read AST source file {}", matched.file.display())
        })?;
        source_contexts.insert(
            matched.file.clone(),
            AstSourceContext {
                line_starts: build_search_line_starts(&source),
            },
        );
    }

    let context = source_contexts
        .get(&matched.file)
        .expect("AST source context should be present");
    // EXEMPT from the raw-bytes/base64-fallback treatment (task #266): the source file was just
    // read via `std::fs::read_to_string` above, which itself fails closed (returns `Err`, never
    // reaches this point) on invalid UTF-8 -- an AST match can never carry non-UTF-8 bytes.
    let (text, bytes, raw) = guaranteed_utf8_match_fields(matched.matched_text.clone());
    Ok(SearchMatchJson {
        file: matched.file.to_string_lossy().into_owned(),
        line: matched.line,
        text,
        bytes,
        raw,
        range: Some(search_range_json(
            &context.line_starts,
            &matched.candidate.byte_range,
        )),
        meta_variables: search_meta_variables_json(&matched.meta_variables, &context.line_starts),
        pattern_id: None,
        pattern_text: None,
    })
}

fn validate_run_args(args: &RunArgs) -> anyhow::Result<()> {
    if args.batch_rewrite.is_some() && args.pattern_option.is_some() {
        anyhow::bail!("tg run --batch-rewrite uses the positional argument as PATH and does not accept --pattern");
    }
    if args.stdin_flag && run_has_path_arg(args) {
        anyhow::bail!("tg run --stdin cannot be combined with a PATH argument");
    }
    if args.stdin_flag && args.files_with_matches {
        anyhow::bail!("tg run --stdin cannot be combined with --files-with-matches");
    }
    if args.files_with_matches && args.json {
        anyhow::bail!("tg run --files-with-matches is a read-only text output mode");
    }
    if ast_run_requires_python_passthrough(args)
        && args.pattern_option.is_none()
        && args.positional.len() == 1
        && Path::new(&args.positional[0]).exists()
    {
        anyhow::bail!(
            "tg run ast-grep semantic options require --pattern <PATTERN> before PATH; positional arguments without --pattern are treated as PATTERN"
        );
    }
    if ast_run_requires_python_passthrough(args) && run_has_mutating_options(args) {
        anyhow::bail!(
            "ast-grep semantic run options are read-only in tg run; use ast-grep directly for semantic rewrites"
        );
    }
    if args.files_with_matches
        && (args.rewrite.is_some()
            || args.batch_rewrite.is_some()
            || args.apply
            || args.diff
            || args.verify
            || args.checkpoint
            || args.audit_manifest.is_some()
            || args.audit_signing_key.is_some()
            || !args.apply_edit_ids.is_empty()
            || !args.reject_edit_ids.is_empty()
            || args.lint_cmd.is_some()
            || args.test_cmd.is_some())
    {
        anyhow::bail!("tg run --files-with-matches is a read-only search output mode");
    }
    if (args.lint_cmd.is_some() || args.test_cmd.is_some()) && !args.apply {
        anyhow::bail!("--lint-cmd and --test-cmd require --apply");
    }
    if args.checkpoint && !args.apply {
        anyhow::bail!("--checkpoint requires --apply");
    }
    Ok(())
}

fn ast_run_requires_python_passthrough(args: &RunArgs) -> bool {
    args.selector.is_some()
        || args.strictness.is_some()
        || args.stdin_flag
        || !args.globs.is_empty()
}

fn run_has_path_arg(args: &RunArgs) -> bool {
    if args.pattern_option.is_some() {
        return !args.positional.is_empty();
    }
    args.positional.len() > 1
}

fn run_has_mutating_options(args: &RunArgs) -> bool {
    args.rewrite.is_some()
        || args.batch_rewrite.is_some()
        || args.apply
        || args.update_all
        || args.diff
        || args.verify
        || args.checkpoint
        || args.audit_manifest.is_some()
        || args.audit_signing_key.is_some()
        || !args.apply_edit_ids.is_empty()
        || !args.reject_edit_ids.is_empty()
        || args.lint_cmd.is_some()
        || args.test_cmd.is_some()
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum RewriteApplyMode {
    PlanThenApply,
    OneShotFastPath,
}

fn can_use_one_shot_apply_fast_path(args: &RunArgs) -> bool {
    args.rewrite.is_some()
        && args.batch_rewrite.is_none()
        && args.apply
        && !args.diff
        && !args.json
        && !args.verify
        && args.lint_cmd.is_none()
        && args.test_cmd.is_none()
        && !args.checkpoint
        && args.audit_manifest.is_none()
        && args.audit_signing_key.is_none()
        && args.apply_edit_ids.is_empty()
        && args.reject_edit_ids.is_empty()
}

fn select_rewrite_apply_mode(args: &RunArgs) -> RewriteApplyMode {
    if can_use_one_shot_apply_fast_path(args) {
        RewriteApplyMode::OneShotFastPath
    } else {
        RewriteApplyMode::PlanThenApply
    }
}

fn checkpoint_timestamp_string() -> String {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_secs().to_string())
        .unwrap_or_else(|_| "0".to_string())
}

/// Format a UNIX epoch (seconds) as an ISO-8601 / RFC-3339 UTC instant, e.g.
/// `2026-06-10T12:34:56Z`. The audit manifest `created_at` MUST use this form so the
/// Python verifier (`_parse_timestamp` -> `datetime.fromisoformat`) and `audit-history`
/// time-ordering accept it; a bare epoch string parses to `None` and breaks chronological
/// sorting (audit M5). Uses Howard Hinnant's `civil_from_days` algorithm so we depend only
/// on `std` (no `chrono`/`time` crate, which would force a dependency bump + rebuild).
fn epoch_seconds_to_iso8601_utc(epoch_secs: u64) -> String {
    let days = (epoch_secs / 86_400) as i64;
    let secs_of_day = epoch_secs % 86_400;
    let hour = secs_of_day / 3_600;
    let minute = (secs_of_day % 3_600) / 60;
    let second = secs_of_day % 60;

    // civil_from_days: convert days since 1970-01-01 to (year, month, day).
    let z = days + 719_468;
    let era = if z >= 0 { z } else { z - 146_096 } / 146_097;
    let doe = z - era * 146_097; // [0, 146096]
    let yoe = (doe - doe / 1_460 + doe / 36_524 - doe / 146_096) / 365; // [0, 399]
    let year = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100); // [0, 365]
    let mp = (5 * doy + 2) / 153; // [0, 11]
    let day = doy - (153 * mp + 2) / 5 + 1; // [1, 31]
    let month = if mp < 10 { mp + 3 } else { mp - 9 }; // [1, 12]
    let year = if month <= 2 { year + 1 } else { year };

    format!("{year:04}-{month:02}-{day:02}T{hour:02}:{minute:02}:{second:02}Z")
}

/// `created_at` for the rewrite audit manifest, as an ISO-8601 UTC string (audit C1/M5).
fn audit_manifest_timestamp_string() -> String {
    let epoch_secs = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_secs())
        .unwrap_or(0);
    epoch_seconds_to_iso8601_utc(epoch_secs)
}

fn checkpoint_storage_dir(root: &Path) -> PathBuf {
    root.join(".tensor-grep").join("checkpoints")
}

fn checkpoint_snapshot_dir(root: &Path, checkpoint_id: &str) -> PathBuf {
    checkpoint_storage_dir(root)
        .join(checkpoint_id)
        .join("snapshot")
}

fn checkpoint_metadata_path(root: &Path, checkpoint_id: &str) -> PathBuf {
    checkpoint_storage_dir(root)
        .join(checkpoint_id)
        .join("metadata.json")
}

fn checkpoint_index_path(root: &Path) -> PathBuf {
    checkpoint_storage_dir(root).join("index.json")
}

#[derive(Debug, Clone)]
struct CheckpointScope {
    root: PathBuf,
    mode: String,
    original_path: PathBuf,
    target_relative: Option<String>,
}

impl CheckpointScope {
    fn scope_kind(&self) -> &'static str {
        if self.target_relative.is_some() {
            "file"
        } else {
            "tree"
        }
    }
}

fn checkpoint_rel_path(path: &Path) -> String {
    path.to_string_lossy().replace('\\', "/")
}

fn checkpoint_display_path(path: &Path) -> String {
    let text = path.to_string_lossy();
    text.strip_prefix(r"\\?\").unwrap_or(&text).to_string()
}

fn checkpoint_absolute_path(path: &Path) -> PathBuf {
    path.canonicalize().unwrap_or_else(|_| {
        if path.is_absolute() {
            path.to_path_buf()
        } else {
            env::current_dir()
                .unwrap_or_else(|_| PathBuf::from("."))
                .join(path)
        }
    })
}

fn detect_checkpoint_scope(path: &str) -> CheckpointScope {
    let candidate = Path::new(path);
    let resolved = checkpoint_absolute_path(candidate);
    if resolved.is_file() || (!resolved.exists() && resolved.extension().is_some()) {
        let root = resolved
            .parent()
            .map(Path::to_path_buf)
            .unwrap_or_else(|| env::current_dir().unwrap_or_else(|_| PathBuf::from(".")));
        let target_relative = resolved
            .file_name()
            .map(PathBuf::from)
            .map(|relative| checkpoint_rel_path(&relative));
        return CheckpointScope {
            root,
            mode: "filesystem-snapshot".to_string(),
            original_path: resolved,
            target_relative,
        };
    }

    let probe_root = if resolved.is_dir() {
        resolved.clone()
    } else {
        resolved
            .parent()
            .map(Path::to_path_buf)
            .unwrap_or_else(|| PathBuf::from("."))
    };

    match Command::new("git")
        .args([
            "-C",
            &probe_root.to_string_lossy(),
            "rev-parse",
            "--show-toplevel",
        ])
        .output()
    {
        Ok(output) if output.status.success() => {
            let git_root = String::from_utf8_lossy(&output.stdout).trim().to_string();
            if git_root.is_empty() {
                CheckpointScope {
                    root: probe_root,
                    mode: "filesystem-snapshot".to_string(),
                    original_path: resolved,
                    target_relative: None,
                }
            } else if Path::new(&git_root) == resolved.as_path() {
                CheckpointScope {
                    root: PathBuf::from(git_root),
                    mode: "git-worktree-snapshot".to_string(),
                    original_path: resolved,
                    target_relative: None,
                }
            } else {
                CheckpointScope {
                    root: probe_root,
                    mode: "filesystem-snapshot".to_string(),
                    original_path: resolved,
                    target_relative: None,
                }
            }
        }
        _ => CheckpointScope {
            root: probe_root,
            mode: "filesystem-snapshot".to_string(),
            original_path: resolved,
            target_relative: None,
        },
    }
}

fn collect_git_checkpoint_entries(root: &Path) -> anyhow::Result<BTreeMap<String, bool>> {
    let tracked = Command::new("git")
        .args(["-C", &root.to_string_lossy(), "ls-files", "-z"])
        .output()
        .context("failed to enumerate git tracked files for checkpoint")?;
    anyhow::ensure!(
        tracked.status.success(),
        "git ls-files failed while building checkpoint"
    );
    let untracked = Command::new("git")
        .args([
            "-C",
            &root.to_string_lossy(),
            "ls-files",
            "--others",
            "--exclude-standard",
            "-z",
        ])
        .output()
        .context("failed to enumerate git untracked files for checkpoint")?;
    anyhow::ensure!(
        untracked.status.success(),
        "git ls-files --others failed while building checkpoint"
    );

    let mut entries = BTreeMap::new();
    for raw in tracked
        .stdout
        .split(|byte| *byte == 0)
        .chain(untracked.stdout.split(|byte| *byte == 0))
    {
        if raw.is_empty() {
            continue;
        }
        let rel = String::from_utf8_lossy(raw).to_string();
        if rel.split('/').any(|component| component == ".tensor-grep") {
            continue;
        }
        entries.insert(rel.clone(), root.join(&rel).exists());
    }
    Ok(entries)
}

fn should_skip_checkpoint_dir(name: &str) -> bool {
    matches!(
        name,
        ".git"
            | ".hg"
            | ".svn"
            | ".venv"
            | "node_modules"
            | "__pycache__"
            | ".pytest_cache"
            | ".mypy_cache"
            | ".ruff_cache"
            | ".tensor-grep"
    )
}

fn collect_filesystem_checkpoint_entries(root: &Path) -> anyhow::Result<BTreeMap<String, bool>> {
    let mut entries = BTreeMap::new();
    for result in walkdir::WalkDir::new(root) {
        let entry = result.context("failed to walk checkpoint filesystem tree")?;
        if entry.file_type().is_dir() {
            continue;
        }
        let relative = entry
            .path()
            .strip_prefix(root)
            .context("checkpoint path escaped snapshot root")?;
        if relative
            .components()
            .any(|component| should_skip_checkpoint_dir(&component.as_os_str().to_string_lossy()))
        {
            continue;
        }
        entries.insert(relative.to_string_lossy().replace('\\', "/"), true);
    }
    Ok(entries)
}

fn collect_checkpoint_entries(scope: &CheckpointScope) -> anyhow::Result<BTreeMap<String, bool>> {
    if let Some(relative) = &scope.target_relative {
        let mut entries = BTreeMap::new();
        entries.insert(relative.clone(), scope.root.join(relative).exists());
        Ok(entries)
    } else if scope.mode == "git-worktree-snapshot" {
        collect_git_checkpoint_entries(&scope.root)
    } else {
        collect_filesystem_checkpoint_entries(&scope.root)
    }
}

/// Copies one checkpoint source entry into the snapshot, mirroring
/// `checkpoint_store.py::create_checkpoint`'s `shutil.copy2(source, destination,
/// follow_symlinks=False)` (checkpoint_store.py:853-855, audit HIGH -- symlink disclosure).
/// `std::fs::copy` FOLLOWS symlinks, so a plain copy here would read a source symlink's
/// (possibly out-of-root) TARGET content and bake it into the snapshot -- an out-of-root
/// disclosure (audit #178). Recreate the symlink AS a symlink instead of following it; fall
/// back to a normal file copy for every non-symlink entry.
///
/// Round-trip contract (verified against `checkpoint_store.py::undo_checkpoint`, the only
/// restore path -- `tg checkpoint` list/undo is a full Python passthrough, see
/// `Commands::Checkpoint` below). Storing the link AS a link (not its target's bytes) is what
/// keeps an out-of-root symlink FAIL-CLOSED rather than a disclosure on undo -- but it does NOT
/// make every symlink restorable, and this comment must not claim a full round-trip. undo's
/// read-only pre-flight `_resolve_within_root` (checkpoint_store.py:124-139, called at
/// :1232-1235) RESOLVES each stored snapshot symlink and, before touching any working-tree
/// file, REFUSES it (ValueError) when the resolved target escapes the snapshot root; the later
/// missing-source probe (:1246-1257) raises CheckpointCorruptError for a dangling target. So an
/// out-of-root OR dangling symlink checkpoint is refused fail-closed on undo -- no disclosure,
/// no data loss, working tree left intact, but NOT restorable. Only an in-root symlink whose
/// resolved target exists inside the snapshot is actually restored. That fail-closed refusal is
/// the correct, safe behavior; the alternative (following the link here) would materialize
/// out-of-root content into the snapshot and then into the tree on undo.
fn copy_checkpoint_entry(source: &Path, destination: &Path) -> std::io::Result<()> {
    if std::fs::symlink_metadata(source)?.file_type().is_symlink() {
        let link_target = std::fs::read_link(source)?;
        create_checkpoint_symlink(&link_target, destination)
    } else {
        std::fs::copy(source, destination).map(|_| ())
    }
}

/// Recreates a symlink (never its target's content) at `link`, pointing at the same raw
/// `target` text the original symlink stored (relative or absolute, copied verbatim -- exactly
/// what `os.readlink()` + `os.symlink()` do on the Python side, so a relative target resolves
/// the same way it always would relative to a symlink's own directory; this is an existing,
/// accepted property of symlink-preserving copies -- e.g. Python's own `shutil.copytree(...,
/// symlinks=True)` -- not a new gap introduced here).
fn create_checkpoint_symlink(target: &Path, link: &Path) -> std::io::Result<()> {
    #[cfg(unix)]
    {
        std::os::unix::fs::symlink(target, link)
    }
    #[cfg(windows)]
    {
        // Windows symlinks are typed (file vs. directory reparse point). Resolve the target
        // relative to the link's own parent directory to pick the right type; a target that
        // cannot be resolved (dangling symlink) defaults to a file symlink, same as Python's
        // `os.symlink(target, dst)` (which defaults `target_is_directory=False`).
        let resolved_is_dir = link
            .parent()
            .map(|parent| parent.join(target))
            .and_then(|candidate| std::fs::metadata(candidate).ok())
            .map(|metadata| metadata.is_dir())
            .unwrap_or(false);
        let result = if resolved_is_dir {
            std::os::windows::fs::symlink_dir(target, link)
        } else {
            std::os::windows::fs::symlink_file(target, link)
        };
        // audit #178 F2a: creating a symlink on Windows needs SeCreateSymbolicLinkPrivilege
        // (admin, or Developer Mode). Without it the OS returns ERROR_PRIVILEGE_NOT_HELD (1314),
        // which the caller's `.with_context("failed to copy ... into checkpoint snapshot ...")`
        // would otherwise surface as a bare, misleading "copy failed". Re-message that one errno
        // so the real cause (a privilege gap, not a copy fault) is visible; leave every other
        // error untouched.
        result.map_err(|err| {
            const ERROR_PRIVILEGE_NOT_HELD: i32 = 1314;
            if err.raw_os_error() == Some(ERROR_PRIVILEGE_NOT_HELD) {
                std::io::Error::new(
                    err.kind(),
                    format!(
                        "creating checkpoint symlink {} requires administrator privileges or \
                         Windows Developer Mode (ERROR_PRIVILEGE_NOT_HELD)",
                        link.display()
                    ),
                )
            } else {
                err
            }
        })
    }
    #[cfg(not(any(unix, windows)))]
    {
        let _ = (target, link);
        Err(std::io::Error::new(
            std::io::ErrorKind::Unsupported,
            "symlinks not supported on this platform",
        ))
    }
}

fn create_checkpoint(path: &str) -> anyhow::Result<CheckpointCreateSummary> {
    let scope = detect_checkpoint_scope(path);
    let root = scope.root.clone();
    let mode = scope.mode.clone();
    let created_at = checkpoint_timestamp_string();
    let unique = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_nanos())
        .unwrap_or(0);
    let checkpoint_id = format!("ckpt-{created_at}-{unique:x}");
    let entries = collect_checkpoint_entries(&scope)?;

    let snapshot_dir = checkpoint_snapshot_dir(&root, &checkpoint_id);
    std::fs::create_dir_all(&snapshot_dir).with_context(|| {
        format!(
            "failed to create checkpoint snapshot dir {}",
            snapshot_dir.display()
        )
    })?;

    // NIT (v1.76 #602 gate, "NIT-3"): mirror checkpoint_store.py::create_checkpoint's
    // `except BaseException: shutil.rmtree(snapshot_dir.parent, ignore_errors=True); raise`
    // (audit #125a, shipped in #602; widened by audit #178 to also cover the index write, so
    // it is now a true mirror end-to-end -- see checkpoint_store.py::create_checkpoint) on the
    // Rust side. Everything from here on is fallible (a file copy, the metadata.json write,
    // the index.json read/parse/write) and, before this fix, a failure at ANY of those steps
    // propagated the error via `?` while leaving the just-created random-id checkpoint
    // directory -- `checkpoint_dir`, holding both snapshot/ and metadata.json -- behind on disk
    // forever. Run the rest of the write sequence in a closure (stable Rust has no `try`
    // blocks) and remove that whole directory before propagating any error; the success (`Ok`)
    // path below is byte-identical to before.
    let checkpoint_dir = checkpoint_storage_dir(&root).join(&checkpoint_id);

    let write_checkpoint_body = || -> anyhow::Result<CheckpointCreateSummary> {
        for (rel_path, exists) in &entries {
            if !exists {
                continue;
            }
            let source = root.join(rel_path);
            let destination = snapshot_dir.join(rel_path);
            if let Some(parent) = destination.parent() {
                std::fs::create_dir_all(parent).with_context(|| {
                    format!(
                        "failed to create checkpoint parent dir {}",
                        parent.display()
                    )
                })?;
            }
            copy_checkpoint_entry(&source, &destination).with_context(|| {
                format!(
                    "failed to copy {} into checkpoint snapshot {}",
                    source.display(),
                    destination.display()
                )
            })?;
        }

        let summary = CheckpointCreateSummary {
            checkpoint_id: checkpoint_id.clone(),
            mode: mode.clone(),
            root: checkpoint_display_path(&root),
            scope: scope.scope_kind().to_string(),
            original_path: checkpoint_display_path(&scope.original_path),
            created_at: created_at.clone(),
            file_count: entries.len(),
        };
        let metadata = CheckpointMetadata {
            version: JSON_OUTPUT_VERSION,
            checkpoint_id: checkpoint_id.clone(),
            mode: mode.clone(),
            root: summary.root.clone(),
            scope: summary.scope.clone(),
            original_path: summary.original_path.clone(),
            created_at: created_at.clone(),
            file_count: entries.len(),
            entries,
        };
        let metadata_path = checkpoint_metadata_path(&root, &checkpoint_id);
        if let Some(parent) = metadata_path.parent() {
            std::fs::create_dir_all(parent)
                .with_context(|| format!("failed to create {}", parent.display()))?;
        }
        write_bytes_refuse_symlink(&metadata_path, &serde_json::to_vec_pretty(&metadata)?)
            .with_context(|| format!("failed to write {}", metadata_path.display()))?;

        let index_path = checkpoint_index_path(&root);
        let mut records: Vec<CheckpointIndexRecord> = if index_path.exists() {
            serde_json::from_slice(
                &std::fs::read(&index_path)
                    .with_context(|| format!("failed to read {}", index_path.display()))?,
            )
            .with_context(|| format!("failed to parse {}", index_path.display()))?
        } else {
            Vec::new()
        };
        records.insert(
            0,
            CheckpointIndexRecord {
                version: JSON_OUTPUT_VERSION,
                checkpoint_id: checkpoint_id.clone(),
                mode,
                root: summary.root.clone(),
                created_at,
                file_count: summary.file_count,
            },
        );
        if let Some(parent) = index_path.parent() {
            std::fs::create_dir_all(parent)
                .with_context(|| format!("failed to create {}", parent.display()))?;
        }
        write_bytes_refuse_symlink(&index_path, &serde_json::to_vec_pretty(&records)?)
            .with_context(|| format!("failed to write {}", index_path.display()))?;

        Ok(summary)
    };

    let result = write_checkpoint_body();
    if result.is_err() {
        // Best-effort: already on the error path, so a cleanup failure must never mask (or
        // panic over) the original error being returned.
        let _ = std::fs::remove_dir_all(&checkpoint_dir);
    }
    result
}

fn load_batch_rewrite_config(config_path: &Path) -> anyhow::Result<BatchRewriteConfig> {
    let contents = std::fs::read_to_string(config_path).with_context(|| {
        format!(
            "failed to read batch rewrite config {}",
            config_path.display()
        )
    })?;
    let value: serde_json::Value = serde_json::from_str(&contents).with_context(|| {
        format!(
            "failed to parse batch rewrite config {}",
            config_path.display()
        )
    })?;
    parse_batch_rewrite_config_value(&value)
}

fn parse_batch_rewrite_config_value(
    value: &serde_json::Value,
) -> anyhow::Result<BatchRewriteConfig> {
    let object = value.as_object().ok_or_else(|| {
        // audit M4: name the required shape instead of the cryptic `$` JSON-pointer root.
        anyhow::anyhow!(
            "--batch-rewrite config must be a JSON object like \
             {{\"rewrites\": [{{\"pattern\": ..., \"replacement\": ..., \"lang\": ...}}], \"verify\": false}}"
        )
    })?;

    for key in object.keys() {
        if key != "rewrites" && key != "verify" {
            anyhow::bail!("invalid batch rewrite config field `{key}`: unknown field");
        }
    }

    let rewrites_value = object.get("rewrites").ok_or_else(|| {
        anyhow::anyhow!("invalid batch rewrite config field `rewrites`: missing required field")
    })?;
    let rewrites_array = rewrites_value.as_array().ok_or_else(|| {
        anyhow::anyhow!("invalid batch rewrite config field `rewrites`: expected array")
    })?;
    if rewrites_array.is_empty() {
        anyhow::bail!(
            "invalid batch rewrite config field `rewrites`: expected at least one rewrite rule"
        );
    }

    let verify = match object.get("verify") {
        Some(serde_json::Value::Bool(value)) => *value,
        Some(_) => anyhow::bail!("invalid batch rewrite config field `verify`: expected boolean"),
        None => false,
    };

    let mut rewrites = Vec::with_capacity(rewrites_array.len());
    for (index, rule_value) in rewrites_array.iter().enumerate() {
        let field_prefix = format!("rewrites[{index}]");
        let rule_object = rule_value.as_object().ok_or_else(|| {
            anyhow::anyhow!("invalid batch rewrite config field `{field_prefix}`: expected object")
        })?;

        for key in rule_object.keys() {
            if key != "pattern" && key != "replacement" && key != "lang" {
                anyhow::bail!(
                    "invalid batch rewrite config field `{field_prefix}.{key}`: unknown field"
                );
            }
        }

        let pattern = read_batch_rewrite_string_field(rule_object, &field_prefix, "pattern")?;
        let replacement =
            read_batch_rewrite_string_field(rule_object, &field_prefix, "replacement")?;
        let lang = read_batch_rewrite_string_field(rule_object, &field_prefix, "lang")?;

        rewrites.push(BatchRewriteRule {
            pattern,
            replacement,
            lang,
        });
    }

    Ok(BatchRewriteConfig { rewrites, verify })
}

fn read_batch_rewrite_string_field(
    object: &serde_json::Map<String, serde_json::Value>,
    field_prefix: &str,
    field_name: &str,
) -> anyhow::Result<String> {
    let field_path = format!("{field_prefix}.{field_name}");
    let value = object.get(field_name).ok_or_else(|| {
        anyhow::anyhow!("invalid batch rewrite config field `{field_path}`: missing required field")
    })?;
    let string_value = value.as_str().ok_or_else(|| {
        anyhow::anyhow!("invalid batch rewrite config field `{field_path}`: expected string")
    })?;
    if string_value.is_empty() {
        anyhow::bail!(
            "invalid batch rewrite config field `{field_path}`: expected non-empty string"
        );
    }
    Ok(string_value.to_string())
}

fn validate_edit_id_selector(ids: &[String], flag_name: &str) -> anyhow::Result<()> {
    let mut seen = std::collections::BTreeSet::new();
    for id in ids {
        if id.is_empty() {
            anyhow::bail!("{flag_name} requires non-empty edit ids");
        }
        if !seen.insert(id.clone()) {
            anyhow::bail!("duplicate edit id `{id}` provided via {flag_name}");
        }
    }
    Ok(())
}

fn filter_rewrite_edits(
    edits: &[tensor_grep_rs::backend_ast::RewriteEdit],
    args: &RunArgs,
) -> anyhow::Result<Vec<tensor_grep_rs::backend_ast::RewriteEdit>> {
    validate_edit_id_selector(&args.apply_edit_ids, "--apply-edit-ids")?;
    validate_edit_id_selector(&args.reject_edit_ids, "--reject-edit-ids")?;

    let known_ids: std::collections::BTreeSet<&str> =
        edits.iter().map(|edit| edit.id.as_str()).collect();
    for id in &args.apply_edit_ids {
        if !known_ids.contains(id.as_str()) {
            anyhow::bail!("unknown edit id `{id}` provided via --apply-edit-ids");
        }
    }
    for id in &args.reject_edit_ids {
        if !known_ids.contains(id.as_str()) {
            anyhow::bail!("unknown edit id `{id}` provided via --reject-edit-ids");
        }
    }

    if !args.apply_edit_ids.is_empty() {
        let allowed: std::collections::BTreeSet<&str> =
            args.apply_edit_ids.iter().map(String::as_str).collect();
        return Ok(edits
            .iter()
            .filter(|edit| allowed.contains(edit.id.as_str()))
            .cloned()
            .collect());
    }

    if !args.reject_edit_ids.is_empty() {
        let rejected: std::collections::BTreeSet<&str> =
            args.reject_edit_ids.iter().map(String::as_str).collect();
        return Ok(edits
            .iter()
            .filter(|edit| !rejected.contains(edit.id.as_str()))
            .cloned()
            .collect());
    }

    Ok(edits.to_vec())
}

fn filter_rewrite_plan(
    plan: &tensor_grep_rs::backend_ast::RewritePlan,
    args: &RunArgs,
) -> anyhow::Result<tensor_grep_rs::backend_ast::RewritePlan> {
    let edits = filter_rewrite_edits(&plan.edits, args)?;
    let mut filtered = plan.clone();
    filtered.total_edits = edits.len();
    filtered.edits = edits;
    Ok(filtered)
}

fn filter_batch_rewrite_plan(
    plan: &BatchRewritePlan,
    args: &RunArgs,
) -> anyhow::Result<BatchRewritePlan> {
    let edits = filter_rewrite_edits(&plan.edits, args)?;
    let mut filtered = plan.clone();
    filtered.total_edits = edits.len();
    filtered.edits = edits;
    Ok(filtered)
}

/// Split a validation command TEMPLATE into argv tokens, honoring "double" and 'single' quotes.
/// A validation command is spawned directly (never through `sh -c` / `cmd /C`), so shell
/// metacharacters in a token are literal data, not operators — there is nothing to escape or reject.
fn split_validation_command_argv(command: &str) -> Vec<String> {
    let mut argv = Vec::new();
    let mut current = String::new();
    let mut started = false; // distinguishes an empty quoted token "" from "no token here"
    let mut in_quotes = false;
    let mut quote_char = '\0';

    for character in command.chars() {
        if matches!(character, '"' | '\'') {
            if in_quotes && character == quote_char {
                in_quotes = false;
                quote_char = '\0';
                continue;
            }
            if !in_quotes {
                in_quotes = true;
                quote_char = character;
                started = true;
                continue;
            }
        }
        if character.is_whitespace() && !in_quotes {
            if started || !current.is_empty() {
                argv.push(std::mem::take(&mut current));
                started = false;
            }
            continue;
        }
        started = true;
        current.push(character);
    }
    if in_quotes {
        // Unterminated quote: refuse to guess token boundaries. Returning an empty argv routes to
        // the clear "empty or unbalanced quotes" error in run_validation_command rather than
        // spawning a mis-split program.
        return Vec::new();
    }
    if started || !current.is_empty() {
        argv.push(current);
    }
    argv
}

/// Build the argv used to EXECUTE a validation command. The TEMPLATE is split into argv first, then
/// the raw file path is substituted into the `$file` / `{file}` placeholder token(s). Because the
/// path lands in a single argv element and the command is spawned directly (no shell), a file whose
/// name contains shell metacharacters cannot inject commands. SECURITY: this replaces the previous
/// model that string-substituted the path into a `sh -c` / `cmd /S /C` command line.
fn validation_command_argv(template: &str, file_path: Option<&str>) -> Vec<String> {
    let mut argv = split_validation_command_argv(template);
    if let Some(path) = file_path {
        for token in &mut argv {
            if token.contains("$file") || token.contains("{file}") {
                *token = token.replace("$file", path).replace("{file}", path);
            }
        }
    }
    argv
}

fn validation_working_dir(path: &str) -> PathBuf {
    let path = Path::new(path);
    if path.is_dir() {
        return path.to_path_buf();
    }
    if path.is_file() {
        return path
            .parent()
            .map(Path::to_path_buf)
            .unwrap_or_else(|| PathBuf::from("."));
    }
    path.parent()
        .map(Path::to_path_buf)
        .unwrap_or_else(|| PathBuf::from("."))
}

/// Resolve the --lint-cmd/--test-cmd subprocess timeout: --validation-timeout-ms flag takes
/// precedence over the TG_VALIDATION_TIMEOUT_MS environment variable, which takes precedence over
/// DEFAULT_VALIDATION_TIMEOUT_MS (mirrors the TG_RESIDENT_AST env-var-fallback convention).
fn validation_timeout_ms(args: &RunArgs) -> u64 {
    resolve_validation_timeout_ms(
        args.validation_timeout_ms,
        std::env::var(TG_VALIDATION_TIMEOUT_MS_ENV).ok(),
    )
}

/// Env lookup is threaded in as a plain `Option<String>` (rather than read directly with
/// `std::env::var` inside this function) so precedence can be unit-tested deterministically --
/// mutating a real process-wide env var from parallel `cargo test` threads would be racy.
fn resolve_validation_timeout_ms(flag: Option<u64>, env_value: Option<String>) -> u64 {
    if let Some(explicit) = flag {
        return explicit;
    }
    if let Some(raw) = env_value {
        if let Ok(parsed) = raw.trim().parse::<u64>() {
            return parsed;
        }
    }
    DEFAULT_VALIDATION_TIMEOUT_MS
}

fn run_validation_command(
    kind: &'static str,
    template: &str,
    file_path: Option<&str>,
    display_command: &str,
    working_dir: &Path,
    timeout_ms: u64,
) -> ValidationCommandResult {
    // Validate the template can run as a direct program invocation BEFORE substituting the file
    // path: an empty/blank program, unbalanced quotes, or the $file placeholder in program position
    // would otherwise spawn the wrong thing (e.g. an attacker-named file as the program).
    let tokens = split_validation_command_argv(template);
    if tokens.first().is_none_or(|token| token.is_empty()) {
        return ValidationCommandResult {
            kind,
            command: display_command.to_string(),
            success: false,
            exit_code: None,
            stdout: String::new(),
            stderr: "validation command is empty or has unbalanced quotes".to_string(),
        };
    }
    if file_path.is_some()
        && tokens.len() == 1
        && (tokens[0].contains("$file") || tokens[0].contains("{file}"))
    {
        return ValidationCommandResult {
            kind,
            command: display_command.to_string(),
            success: false,
            exit_code: None,
            stdout: String::new(),
            stderr: "validation command must name a program before the $file/{file} placeholder"
                .to_string(),
        };
    }

    let argv = validation_command_argv(template, file_path);
    let Some((program, args)) = argv.split_first() else {
        return ValidationCommandResult {
            kind,
            command: display_command.to_string(),
            success: false,
            exit_code: None,
            stdout: String::new(),
            stderr: "validation command is empty".to_string(),
        };
    };

    // A deadlocked/interactive/infinite-looping validation command must never hang `tg run
    // --apply` forever (the #400 hang class, applied to the validation path -- audit #10). We
    // spawn with piped stdout/stderr and bound the wait with `process_control`'s
    // `controlled_with_output`, which drains the pipes WHILE timing out. Do NOT replace this with
    // a hand-rolled `spawn` + `wait_timeout` + `wait_with_output`: if the child fills the OS pipe
    // buffer before exiting, it blocks on write() until someone reads, but `wait_timeout` never
    // reads the pipes -- the parent and child deadlock before the timeout can fire (rust-lang#45572).
    // SENTINEL RETIREMENT (2026-08-01 backlog campaign, mirrors apply_policy.py): no `--`
    // separator here either -- `program`/`args` are an operator-authored complete validation
    // command, not our flags plus an untrusted positional, so the CWE-88 argv-sentinel census
    // does not apply to this construction site. Adversarial gate note (corrected 2026-08-01: the
    // prior comment overclaimed): `validation_template_file_path` (below) does NOT always
    // absolutize the substituted `$file`/`{file}` token -- when `std::env::current_dir()` fails,
    // its fallback joins the candidate against `PathBuf::from(".")`, which stays relative. The
    // invariant that actually holds on BOTH arms is narrower but still sufficient: the result is
    // either genuinely absolute or dot-PREFIXED (`./...`), and neither shape can ever lead with
    // `-`, so the sibling Python bug (a repo file named e.g. `-cevil.ini` parsing as a flag,
    // fixed in `apply_policy.py::_policy_file_arg`) cannot recur here either way. Do NOT
    // "simplify" this by substituting a bare relative path with no dot-prefix guarantee -- the
    // dot-prefix-or-absolute guarantee, not absolutization per se, is load-bearing.
    let mut command = Command::new(program);
    command
        .args(args)
        .current_dir(working_dir)
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped());

    let child = match command.spawn() {
        Ok(child) => child,
        Err(error) => {
            return ValidationCommandResult {
                kind,
                command: display_command.to_string(),
                success: false,
                exit_code: None,
                stdout: String::new(),
                stderr: format!(
                    "failed to spawn validation command in {}: {error}",
                    working_dir.display()
                ),
            };
        }
    };

    match child
        .controlled_with_output()
        .time_limit(Duration::from_millis(timeout_ms))
        .terminate_for_timeout()
        .wait()
    {
        Ok(Some(output)) => ValidationCommandResult {
            kind,
            command: display_command.to_string(),
            success: output.status.success(),
            exit_code: output.status.code().map(|code| code as i32),
            stdout: String::from_utf8_lossy(&output.stdout).to_string(),
            stderr: String::from_utf8_lossy(&output.stderr).to_string(),
        },
        // `terminate_for_timeout()` makes `wait()` return `Ok(None)` when the time limit expires:
        // the crate has already terminated (and reaped) the child, so there is no zombie left
        // behind. Report this as a FAILED result (never a panic) so the caller's on_failure
        // rollback path runs, exactly like any other failed validation command.
        Ok(None) => ValidationCommandResult {
            kind,
            command: display_command.to_string(),
            success: false,
            exit_code: None,
            stdout: String::new(),
            stderr: format!("validation command exceeded {timeout_ms}ms timeout"),
        },
        Err(error) => ValidationCommandResult {
            kind,
            command: display_command.to_string(),
            success: false,
            exit_code: None,
            stdout: String::new(),
            stderr: format!(
                "failed to wait for validation command in {}: {error}",
                working_dir.display()
            ),
        },
    }
}

fn validation_template_file_path(path: &str) -> String {
    let candidate = Path::new(path);
    let absolute = if candidate.is_absolute() {
        candidate.to_path_buf()
    } else {
        std::env::current_dir()
            .unwrap_or_else(|_| PathBuf::from("."))
            .join(candidate)
    };
    absolute.to_string_lossy().to_string()
}

fn expand_validation_command_template(command: &str, path: &str) -> String {
    if !validation_command_uses_file_placeholder(command) {
        return command.to_string();
    }
    let file_path = validation_template_file_path(path);
    command
        .replace("$file", &file_path)
        .replace("{file}", &file_path)
}

fn validation_command_uses_file_placeholder(command: &str) -> bool {
    command.contains("$file") || command.contains("{file}")
}

fn validation_template_targets_for_command(
    command: &str,
    path: &str,
    edits: &[tensor_grep_rs::backend_ast::RewriteEdit],
) -> Vec<String> {
    if !validation_command_uses_file_placeholder(command) {
        return vec![path.to_string()];
    }

    let edited_files: Vec<String> = edits
        .iter()
        .map(|edit| edit.file.clone())
        .collect::<BTreeSet<_>>()
        .into_iter()
        .map(|file| file.to_string_lossy().to_string())
        .collect();
    if edited_files.is_empty() {
        vec![path.to_string()]
    } else {
        edited_files
    }
}

/// Cap the per-command validation target list to at most `max_targets` entries (audit #34: an
/// 800-edited-file `--batch-rewrite ... --test-cmd 'pytest {file}'` would otherwise fan out 800
/// serial subprocess spawns). `max_targets == 0` disables the cap (mirrors ClassifyArgs.max_lines's
/// "0 disables the cap" convention). Returns the possibly-truncated list, whether truncation
/// occurred, and the real pre-cap target count so the caller can report it (fail-closed VISIBLE:
/// a silently-dropped target must never look like a clean, complete validation pass).
fn cap_validation_targets(
    mut targets: Vec<String>,
    max_targets: usize,
) -> (Vec<String>, bool, usize) {
    let total = targets.len();
    if max_targets == 0 || total <= max_targets {
        return (targets, false, total);
    }
    targets.truncate(max_targets);
    (targets, true, total)
}

fn run_post_apply_validation(
    args: &RunArgs,
    path: &str,
    edits: &[tensor_grep_rs::backend_ast::RewriteEdit],
) -> Option<ValidationSummary> {
    let mut commands = Vec::new();
    let working_dir = validation_working_dir(path);
    let timeout_ms = validation_timeout_ms(args);
    let max_targets = args.max_validation_targets;
    let mut targets_truncated = false;
    let mut targets_total = 0usize;

    if let Some(command) = &args.lint_cmd {
        let (targets, truncated, total) = cap_validation_targets(
            validation_template_targets_for_command(command, path, edits),
            max_targets,
        );
        targets_truncated |= truncated;
        targets_total = targets_total.max(total);
        for target in targets {
            let expanded = expand_validation_command_template(command, &target);
            let file_path = validation_command_uses_file_placeholder(command)
                .then(|| validation_template_file_path(&target));
            commands.push(run_validation_command(
                "lint",
                command,
                file_path.as_deref(),
                &expanded,
                &working_dir,
                timeout_ms,
            ));
        }
    }
    if let Some(command) = &args.test_cmd {
        let (targets, truncated, total) = cap_validation_targets(
            validation_template_targets_for_command(command, path, edits),
            max_targets,
        );
        targets_truncated |= truncated;
        targets_total = targets_total.max(total);
        for target in targets {
            let expanded = expand_validation_command_template(command, &target);
            let file_path = validation_command_uses_file_placeholder(command)
                .then(|| validation_template_file_path(&target));
            commands.push(run_validation_command(
                "test",
                command,
                file_path.as_deref(),
                &expanded,
                &working_dir,
                timeout_ms,
            ));
        }
    }

    if commands.is_empty() {
        return None;
    }

    Some(ValidationSummary {
        success: commands.iter().all(|command| command.success),
        commands,
        validation_targets_truncated: targets_truncated,
        validation_targets_total: targets_total,
    })
}

fn sha256_hex(bytes: &[u8]) -> String {
    let mut hasher = Sha256::new();
    hasher.update(bytes);
    let digest = hasher.finalize();
    let mut output = String::with_capacity(digest.len() * 2);
    for byte in digest {
        output.push_str(&format!("{byte:02x}"));
    }
    output
}

fn canonical_manifest_bytes(manifest: &RewriteAuditManifest) -> anyhow::Result<Vec<u8>> {
    let mut value = serde_json::to_value(manifest)?;
    value
        .as_object_mut()
        .expect("rewrite audit manifest should serialize as an object")
        .remove("manifest_sha256");
    value
        .as_object_mut()
        .expect("rewrite audit manifest should serialize as an object")
        .remove("signature");
    Ok(serde_json::to_vec_pretty(&value)?)
}

fn previous_manifest_digest(path: &Path) -> anyhow::Result<String> {
    let previous_bytes = std::fs::read(path)
        .with_context(|| format!("failed to read previous audit manifest {}", path.display()))?;
    let previous_value: Option<serde_json::Value> = serde_json::from_slice(&previous_bytes).ok();
    Ok(previous_value
        .as_ref()
        .and_then(|value| value.get("manifest_sha256"))
        .and_then(|value| value.as_str())
        .map(ToOwned::to_owned)
        .unwrap_or_else(|| sha256_hex(&previous_bytes)))
}

fn collect_pre_apply_hashes(
    edits: &[tensor_grep_rs::backend_ast::RewriteEdit],
) -> anyhow::Result<BTreeMap<String, String>> {
    let mut hashes = BTreeMap::new();
    for file in edits
        .iter()
        .map(|edit| edit.file.clone())
        .collect::<std::collections::BTreeSet<_>>()
    {
        let bytes = std::fs::read(&file)
            .with_context(|| format!("failed to read {} for audit manifest", file.display()))?;
        hashes.insert(file.to_string_lossy().to_string(), sha256_hex(&bytes));
    }
    Ok(hashes)
}

fn collect_validation_rollback_snapshots(
    edits: &[tensor_grep_rs::backend_ast::RewriteEdit],
) -> anyhow::Result<BTreeMap<String, Vec<u8>>> {
    let mut snapshots = BTreeMap::new();
    for file in edits
        .iter()
        .map(|edit| edit.file.clone())
        .collect::<std::collections::BTreeSet<_>>()
    {
        let bytes = std::fs::read(&file).with_context(|| {
            format!("failed to read {} for validation rollback", file.display())
        })?;
        snapshots.insert(file.to_string_lossy().to_string(), bytes);
    }
    Ok(snapshots)
}

fn restore_validation_rollback_snapshots(
    snapshots: &BTreeMap<String, Vec<u8>>,
) -> ValidationRollbackSummary {
    let mut files_restored = Vec::new();
    let mut errors = Vec::new();

    for (file, bytes) in snapshots {
        match write_bytes_refuse_symlink(Path::new(file), bytes) {
            Ok(()) => files_restored.push(file.clone()),
            Err(error) => errors.push(format!("failed to restore {file}: {error}")),
        }
    }

    ValidationRollbackSummary {
        triggered_by: "validation",
        success: errors.is_empty(),
        files_restored,
        errors,
    }
}

fn emit_rollback_status(summary: &ValidationRollbackSummary) {
    if summary.success {
        eprintln!(
            "[rollback] restored {} file(s) after failed validation",
            summary.files_restored.len()
        );
    } else {
        eprintln!(
            "[rollback] failed to restore {} file(s) after failed validation",
            summary.errors.len()
        );
    }
}

/// Delegates to the single implementation in the lib crate
/// (`tensor_grep_rs::safe_write`). It lived HERE, in the binary crate, which is why
/// `backend_ast::direct_write_file` (lib crate) could not reach it and used a bare
/// `std::fs::write` -- the `--apply`-writes-through-a-symlink hole. Kept as a thin alias so the
/// existing audit-manifest / checkpoint / rollback call sites read unchanged.
fn write_bytes_refuse_symlink(path: &Path, bytes: &[u8]) -> anyhow::Result<()> {
    tensor_grep_rs::safe_write::write_bytes_refuse_symlink(path, bytes)
}

struct AuditManifestWriteInput<'a> {
    path: &'a Path,
    lang: &'a str,
    root_path: &'a str,
    edits: &'a [tensor_grep_rs::backend_ast::RewriteEdit],
    plan_total_edits: usize,
    checkpoint: Option<&'a CheckpointCreateSummary>,
    validation: Option<&'a ValidationSummary>,
    before_hashes: &'a BTreeMap<String, String>,
    signing_key_path: Option<&'a Path>,
}

fn write_audit_manifest_for_plan(
    input: AuditManifestWriteInput<'_>,
) -> anyhow::Result<AuditManifestSummary> {
    let AuditManifestWriteInput {
        path,
        lang,
        root_path,
        edits,
        plan_total_edits,
        checkpoint,
        validation,
        before_hashes,
        signing_key_path,
    } = input;
    let previous_manifest_sha256 = if path.exists() {
        Some(previous_manifest_digest(path)?)
    } else {
        None
    };

    let mut by_file: BTreeMap<String, Vec<String>> = BTreeMap::new();
    for edit in edits {
        by_file
            .entry(edit.file.to_string_lossy().to_string())
            .or_default()
            .push(edit.id.clone());
    }

    let mut files = Vec::with_capacity(by_file.len());
    for (file, edit_ids) in by_file {
        let after_bytes = std::fs::read(&file)
            .with_context(|| format!("failed to read {} for audit manifest", file))?;
        let before_sha256 = before_hashes
            .get(&file)
            .cloned()
            .ok_or_else(|| anyhow::anyhow!("missing pre-apply hash for {file}"))?;
        files.push(RewriteAuditManifestFile {
            path: file.clone(),
            edit_ids,
            before_sha256,
            after_sha256: sha256_hex(&after_bytes),
        });
    }

    let manifest = RewriteAuditManifest {
        version: JSON_OUTPUT_VERSION,
        kind: "rewrite-audit-manifest",
        // ISO-8601 UTC (audit C1/M5): matches the Python checkpoint `created_at` format and
        // keeps `audit-history` time-ordering working (`_parse_timestamp`). NOT a bare epoch.
        created_at: audit_manifest_timestamp_string(),
        lang: lang.to_string(),
        path: root_path.to_string(),
        plan_total_edits,
        applied_edit_ids: edits.iter().map(|edit| edit.id.clone()).collect(),
        previous_manifest_sha256,
        checkpoint: checkpoint.cloned(),
        validation: validation.cloned(),
        files,
        manifest_sha256: None,
        signature: None,
    };

    let mut manifest = manifest;
    let canonical_bytes = canonical_manifest_bytes(&manifest)?;
    manifest.manifest_sha256 = Some(sha256_hex(&canonical_bytes));
    if let Some(signing_key_path) = signing_key_path {
        let key_bytes = std::fs::read(signing_key_path).with_context(|| {
            format!(
                "failed to read audit signing key {}",
                signing_key_path.display()
            )
        })?;
        let mut mac = Hmac::<Sha256>::new_from_slice(&key_bytes)
            .map_err(|_| anyhow::anyhow!("invalid audit signing key"))?;
        mac.update(&canonical_bytes);
        let signature_bytes = mac.finalize().into_bytes();
        let mut signature_value = String::with_capacity(signature_bytes.len() * 2);
        for byte in signature_bytes {
            signature_value.push_str(&format!("{byte:02x}"));
        }
        manifest.signature = Some(AuditManifestSignature {
            kind: "hmac-sha256",
            key_path: signing_key_path.to_string_lossy().to_string(),
            value: signature_value,
        });
    }

    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)
            .with_context(|| format!("failed to create audit manifest dir {}", parent.display()))?;
    }
    write_bytes_refuse_symlink(path, &serde_json::to_vec_pretty(&manifest)?)
        .with_context(|| format!("failed to write audit manifest {}", path.display()))?;

    Ok(AuditManifestSummary {
        path: path.to_string_lossy().to_string(),
        file_count: manifest.files.len(),
        applied_edit_count: manifest.applied_edit_ids.len(),
        signed: manifest.signature.is_some(),
        signature_kind: manifest.signature.as_ref().map(|signature| signature.kind),
    })
}

fn verify_audit_manifest_payload(
    args: &AuditVerifyArgs,
) -> anyhow::Result<AuditManifestVerifyJson> {
    let manifest_path = args.manifest_path.canonicalize().with_context(|| {
        format!(
            "failed to resolve audit manifest {}",
            args.manifest_path.display()
        )
    })?;
    let manifest_bytes = std::fs::read(&manifest_path)
        .with_context(|| format!("failed to read audit manifest {}", manifest_path.display()))?;
    let manifest: RewriteAuditManifestRead = serde_json::from_slice(&manifest_bytes)
        .with_context(|| format!("failed to parse audit manifest {}", manifest_path.display()))?;

    let mut manifest_value: serde_json::Value = serde_json::from_slice(&manifest_bytes)
        .with_context(|| format!("failed to parse audit manifest {}", manifest_path.display()))?;
    let object = manifest_value
        .as_object_mut()
        .ok_or_else(|| anyhow::anyhow!("audit manifest must be a JSON object"))?;
    object.remove("manifest_sha256");
    object.remove("signature");
    let canonical_bytes = serde_json::to_vec_pretty(&manifest_value)?;

    let expected_digest = sha256_hex(&canonical_bytes);
    let digest_valid = manifest
        .manifest_sha256
        .as_ref()
        .map(|digest| digest == &expected_digest)
        .unwrap_or(false);

    let previous_manifest_path = args
        .previous_manifest
        .as_ref()
        .map(|path| path.canonicalize())
        .transpose()
        .with_context(|| {
            args.previous_manifest
                .as_ref()
                .map(|path| format!("failed to resolve previous manifest {}", path.display()))
                .unwrap_or_default()
        })?;
    let mut chain_valid = true;
    let mut errors = Vec::new();
    if let Some(previous_digest) = manifest.previous_manifest_sha256.as_ref() {
        if let Some(previous_path) = previous_manifest_path.as_ref() {
            let actual_previous_digest = previous_manifest_digest(previous_path)?;
            if previous_digest != &actual_previous_digest {
                chain_valid = false;
                errors.push(
                    "Previous manifest digest does not match previous_manifest_sha256.".to_string(),
                );
            }
        } else {
            chain_valid = false;
            errors.push(
                "Manifest chain digest is present but no previous manifest was provided."
                    .to_string(),
            );
        }
    }

    let signing_key_path = args
        .signing_key
        .as_ref()
        .map(|path| path.canonicalize())
        .transpose()
        .with_context(|| {
            args.signing_key
                .as_ref()
                .map(|path| format!("failed to resolve signing key {}", path.display()))
                .unwrap_or_default()
        })?;
    let mut signature_valid = true;
    let signature_kind = manifest
        .signature
        .as_ref()
        .map(|signature| signature.kind.clone());
    if let Some(signature) = manifest.signature.as_ref() {
        if signature.kind != "hmac-sha256" {
            signature_valid = false;
            errors.push(format!("Unsupported signature kind: {}", signature.kind));
        } else if let Some(key_path) = signing_key_path.as_deref() {
            let key_bytes = std::fs::read(key_path).with_context(|| {
                format!("failed to read audit signing key {}", key_path.display())
            })?;
            let mut mac = Hmac::<Sha256>::new_from_slice(&key_bytes)
                .map_err(|_| anyhow::anyhow!("invalid audit signing key"))?;
            mac.update(&canonical_bytes);
            let actual_signature = mac.finalize().into_bytes();
            let mut actual_signature_hex = String::with_capacity(actual_signature.len() * 2);
            for byte in actual_signature {
                actual_signature_hex.push_str(&format!("{byte:02x}"));
            }
            if actual_signature_hex != signature.value {
                signature_valid = false;
                errors.push(
                    "Manifest signature does not match the supplied signing key.".to_string(),
                );
            }
        } else {
            // Never derive the verification key from inside the manifest being verified:
            // a tampered manifest could point key_path at an attacker-controlled key and
            // forge a matching HMAC, defeating tamper-evidence for the default (no
            // --signing-key) invocation. Require an out-of-band key; treat
            // signature.key_path as informational only (audit S2).
            signature_valid = false;
            errors.push(
                "Manifest is hmac-sha256 signed but no --signing-key was provided; refusing to \
                 trust the key_path embedded in the manifest."
                    .to_string(),
            );
        }
    } else if signing_key_path.is_some() {
        signature_valid = false;
        errors.push("Signing key was provided but the manifest is unsigned.".to_string());
    }

    if !digest_valid {
        errors.insert(
            0,
            "Manifest digest does not match manifest_sha256.".to_string(),
        );
    }

    Ok(AuditManifestVerifyJson {
        version: JSON_OUTPUT_VERSION,
        routing_backend: "AuditManifest",
        routing_reason: "audit-manifest-verify",
        sidecar_used: false,
        manifest_path: manifest_path.to_string_lossy().to_string(),
        signing_key_path: signing_key_path.map(|path| path.to_string_lossy().to_string()),
        previous_manifest_path: previous_manifest_path
            .map(|path| path.to_string_lossy().to_string()),
        kind: Some(manifest.kind),
        manifest_sha256: manifest.manifest_sha256,
        previous_manifest_sha256: manifest.previous_manifest_sha256,
        checks: AuditManifestVerifyChecks {
            digest_valid,
            chain_valid,
            signature_valid,
        },
        signature_kind,
        valid: digest_valid && chain_valid && signature_valid,
        errors,
    })
}

fn emit_validation_status(summary: &ValidationSummary) {
    if summary.validation_targets_truncated {
        eprintln!(
            "[validation] {} edited-file validation target(s) found; only the first --max-validation-targets were run. Rerun with a higher --max-validation-targets to validate the rest.",
            summary.validation_targets_total
        );
    }
    for result in &summary.commands {
        if result.success {
            eprintln!(
                "[validation:{}] passed{}",
                result.kind,
                result
                    .exit_code
                    .map(|code| format!(" (exit code {code})"))
                    .unwrap_or_default()
            );
        } else {
            eprintln!(
                "[validation:{}] failed{}",
                result.kind,
                result
                    .exit_code
                    .map(|code| format!(" (exit code {code})"))
                    .unwrap_or_else(|| " (no exit code)".to_string())
            );
        }
    }
}

fn warn_windows_single_quote_ast_pattern(pattern: &str) {
    if !cfg!(windows) {
        return;
    }
    let trimmed = pattern.trim();
    if trimmed.len() >= 2 && trimmed.starts_with('\'') && trimmed.ends_with('\'') {
        eprintln!(
            "No AST matches found. cmd.exe treats single quotes literally; use double quotes in cmd.exe or run this pattern from PowerShell/Git Bash where single quotes quote literal text."
        );
    }
}

fn handle_ast_run(mut args: RunArgs) -> anyhow::Result<()> {
    if args.update_all {
        if args.rewrite.is_none() {
            anyhow::bail!("tg run --update-all requires --rewrite");
        }
        args.apply = true;
    }
    validate_run_args(&args)?;
    if ast_run_requires_python_passthrough(&args) {
        let passthrough_args = ast_run_python_passthrough_args(&args)?;
        if args.stdin_flag {
            let mut stdin_bytes = Vec::new();
            io::stdin().read_to_end(&mut stdin_bytes)?;
            return handle_python_passthrough_with_stdin("run", passthrough_args, stdin_bytes);
        }
        return handle_python_passthrough("run", passthrough_args);
    }
    let backend = AstBackend::new();

    if let Some(config_path) = &args.batch_rewrite {
        let config = load_batch_rewrite_config(config_path)?;
        let path = run_batch_path(&args)?;
        if args.apply && !args.diff {
            return handle_ast_batch_rewrite_apply(&backend, &args, &config, path);
        }
        return handle_ast_batch_rewrite(&backend, &args, &config, path);
    }

    let (path, path_was_implicit) = run_search_path_with_origin(&args);

    if let Some(replacement) = &args.rewrite {
        if args.apply && !args.diff {
            return handle_ast_rewrite_apply(&backend, &args, replacement, path);
        }
        return handle_ast_rewrite(&backend, &args, replacement, path);
    }

    let pattern = run_pattern(&args)?;

    if args.json {
        let matches = backend.search(pattern, &args.lang, path)?;
        let match_count = matches.len();
        let mut source_contexts = BTreeMap::new();
        emit_json_search_results(
            RoutingDecision::ast(),
            pattern,
            path,
            &[],
            matches
                .iter()
                .map(|matched| ast_match_to_search_json(matched, &mut source_contexts))
                .collect::<anyhow::Result<Vec<_>>>()?,
            // Task 276: this route observed no walk of its own, so it cannot report a
            // count. `None` means "cannot report", NEVER "complete".
            None,
            path_was_implicit,
        )?;
        if match_count == 0 {
            warn_windows_single_quote_ast_pattern(pattern);
            std::process::exit(1);
        }
        return Ok(());
    }

    let matches = backend.search_for_cli(pattern, &args.lang, path)?;
    let match_count: usize = matches
        .iter()
        .map(|file_matches| file_matches.matches.len())
        .sum();

    if args.verbose {
        emit_verbose_metadata(RoutingDecision::ast());
    }

    let stdout = io::stdout();
    let mut stdout = io::BufWriter::new(stdout.lock());
    if args.files_with_matches {
        for file_matches in matches {
            if !file_matches.matches.is_empty() {
                writeln!(stdout, "{}", file_matches.file.display())?;
            }
        }
        if match_count == 0 {
            warn_windows_single_quote_ast_pattern(pattern);
            std::process::exit(1);
        }
        return Ok(());
    }

    for file_matches in matches {
        for matched in file_matches.matches {
            writeln!(
                stdout,
                "{}:{}:{}",
                file_matches.file.display(),
                matched.line,
                matched.matched_text
            )?;
        }
    }

    if match_count == 0 {
        warn_windows_single_quote_ast_pattern(pattern);
        std::process::exit(1);
    }

    Ok(())
}

fn ast_run_python_passthrough_args(args: &RunArgs) -> anyhow::Result<Vec<String>> {
    let mut passthrough_args = vec!["--lang".to_string(), args.lang.clone()];
    let pattern = run_pattern(args)?.to_string();
    passthrough_args.push("--pattern".to_string());
    passthrough_args.push(pattern);

    if let Some(path) = run_optional_path(args) {
        passthrough_args.push(path.to_string());
    }
    if args.json {
        passthrough_args.push("--json".to_string());
    }
    if args.files_with_matches {
        passthrough_args.push("--files-with-matches".to_string());
    }
    if let Some(selector) = &args.selector {
        passthrough_args.push("--selector".to_string());
        passthrough_args.push(selector.clone());
    }
    if let Some(strictness) = &args.strictness {
        passthrough_args.push("--strictness".to_string());
        passthrough_args.push(strictness.clone());
    }
    if args.stdin_flag {
        passthrough_args.push("--stdin".to_string());
    }
    for glob in &args.globs {
        passthrough_args.push("--globs".to_string());
        passthrough_args.push(glob.clone());
    }
    Ok(passthrough_args)
}

fn run_optional_path(args: &RunArgs) -> Option<&str> {
    if args.pattern_option.is_some() {
        return args.positional.first().map(String::as_str);
    }
    args.positional.get(1).map(String::as_str)
}

fn handle_ast_rewrite(
    backend: &AstBackend,
    args: &RunArgs,
    replacement: &str,
    path: &str,
) -> anyhow::Result<()> {
    if args.verbose {
        emit_verbose_metadata(RoutingDecision::ast());
    }

    let pattern = run_pattern(args)?;
    let plan = backend.plan_rewrites(pattern, replacement, &args.lang, path)?;
    let plan = filter_rewrite_plan(&plan, args)?;

    if !plan.rejected_overlaps.is_empty() {
        eprintln!(
            "[rewrite] {} overlapping edit(s) rejected",
            plan.rejected_overlaps.len()
        );
    }

    if plan.edits.is_empty() {
        if args.diff && args.json {
            let payload = RewriteDiffJson {
                version: plan.version,
                routing_backend: plan.routing_backend,
                routing_reason: plan.routing_reason,
                sidecar_used: plan.sidecar_used,
                plan: &plan,
                diff: String::new(),
            };
            println!("{}", serde_json::to_string_pretty(&payload)?);
            return Ok(());
        }
        if args.json {
            println!("{}", serde_json::to_string_pretty(&plan)?);
            return Ok(());
        }
        eprintln!("[rewrite] no matches found, nothing to rewrite");
        return Ok(());
    }

    if args.diff {
        let diff = plan.generate_diff()?;
        if args.json {
            let payload = RewriteDiffJson {
                version: plan.version,
                routing_backend: plan.routing_backend,
                routing_reason: plan.routing_reason,
                sidecar_used: plan.sidecar_used,
                plan: &plan,
                diff,
            };
            println!("{}", serde_json::to_string_pretty(&payload)?);
        } else {
            print!("{diff}");
        }
        return Ok(());
    }

    if !args.apply {
        println!("{}", serde_json::to_string_pretty(&plan)?);
        return Ok(());
    }

    let files_written = AstBackend::apply_rewrites(&plan)?;
    eprintln!(
        "[rewrite] applied {} edit(s) across {} file(s)",
        plan.edits.len(),
        files_written
    );

    Ok(())
}

fn handle_ast_rewrite_apply(
    backend: &AstBackend,
    args: &RunArgs,
    replacement: &str,
    path: &str,
) -> anyhow::Result<()> {
    if args.verbose {
        emit_verbose_metadata(RoutingDecision::ast());
    }

    let pattern = run_pattern(args)?;
    let apply_mode = select_rewrite_apply_mode(args);
    let plan = match apply_mode {
        RewriteApplyMode::OneShotFastPath => {
            backend.plan_and_apply(pattern, replacement, &args.lang, path)?
        }
        RewriteApplyMode::PlanThenApply => {
            let plan = backend.plan_rewrites(pattern, replacement, &args.lang, path)?;
            filter_rewrite_plan(&plan, args)?
        }
    };

    if plan.edits.is_empty() && plan.rejected_overlaps.is_empty() {
        if args.json {
            let payload = ApplyVerifyJson {
                version: plan.version,
                routing_backend: plan.routing_backend,
                routing_reason: plan.routing_reason,
                sidecar_used: plan.sidecar_used,
                checkpoint: None,
                audit_manifest: None,
                plan: &plan,
                verification: None,
                validation: None,
                rollback: None,
            };
            println!("{}", serde_json::to_string_pretty(&payload)?);
        } else {
            eprintln!("[rewrite] no matches found, nothing to rewrite");
        }
        return Ok(());
    }

    let checkpoint = if args.checkpoint {
        let checkpoint = create_checkpoint(path)?;
        if !args.json {
            eprintln!(
                "[checkpoint] created {} ({}, files={})",
                checkpoint.checkpoint_id, checkpoint.mode, checkpoint.file_count
            );
        }
        Some(checkpoint)
    } else {
        None
    };

    let rollback_snapshots = if args.lint_cmd.is_some() || args.test_cmd.is_some() {
        Some(collect_validation_rollback_snapshots(&plan.edits)?)
    } else {
        None
    };

    let before_hashes = if args.audit_manifest.is_some() {
        Some(collect_pre_apply_hashes(&plan.edits)?)
    } else {
        None
    };

    if apply_mode == RewriteApplyMode::PlanThenApply {
        AstBackend::apply_rewrites(&plan)?;
    }

    if !plan.rejected_overlaps.is_empty() && !args.json {
        eprintln!(
            "[rewrite] {} overlapping edit(s) rejected",
            plan.rejected_overlaps.len()
        );
    }

    if !args.json {
        eprintln!("[rewrite] applied {} edit(s)", plan.edits.len(),);
    }

    let verification = if args.verify {
        let v = plan.verify(backend)?;
        if !args.json {
            if v.mismatches.is_empty() {
                eprintln!("[verify] {}/{} edits verified", v.verified, v.total_edits);
            } else {
                eprintln!(
                    "[verify] {}/{} edits verified, {} mismatches",
                    v.verified,
                    v.total_edits,
                    v.mismatches.len()
                );
            }
        }
        Some(v)
    } else {
        None
    };

    let validation = run_post_apply_validation(args, path, &plan.edits);
    if !args.json {
        if let Some(summary) = &validation {
            emit_validation_status(summary);
        }
    }

    let rollback = if let Some(summary) = &validation {
        if !summary.success {
            let rollback = rollback_snapshots
                .as_ref()
                .map(restore_validation_rollback_snapshots);
            if !args.json {
                if let Some(rollback_summary) = &rollback {
                    emit_rollback_status(rollback_summary);
                }
            }
            rollback
        } else {
            None
        }
    } else {
        None
    };

    let audit_manifest = if let Some(audit_manifest_path) = &args.audit_manifest {
        Some(write_audit_manifest_for_plan(AuditManifestWriteInput {
            path: audit_manifest_path,
            lang: &args.lang,
            root_path: path,
            edits: &plan.edits,
            plan_total_edits: plan.total_edits,
            checkpoint: checkpoint.as_ref(),
            validation: validation.as_ref(),
            before_hashes: before_hashes
                .as_ref()
                .expect("pre-apply hashes should exist when audit manifest requested"),
            signing_key_path: args.audit_signing_key.as_deref(),
        })?)
    } else {
        None
    };

    if args.json {
        let payload = ApplyVerifyJson {
            version: plan.version,
            routing_backend: plan.routing_backend,
            routing_reason: plan.routing_reason,
            sidecar_used: plan.sidecar_used,
            checkpoint: checkpoint.as_ref(),
            audit_manifest: audit_manifest.as_ref(),
            plan: &plan,
            verification: verification.as_ref(),
            validation: validation.as_ref(),
            rollback: rollback.as_ref(),
        };
        println!("{}", serde_json::to_string_pretty(&payload)?);
    }

    if let Some(summary) = &validation {
        if !summary.success {
            anyhow::bail!("post-apply validation failed");
        }
    }

    Ok(())
}

fn handle_ast_batch_rewrite(
    backend: &AstBackend,
    args: &RunArgs,
    config: &BatchRewriteConfig,
    path: &str,
) -> anyhow::Result<()> {
    if args.verbose {
        emit_verbose_metadata(RoutingDecision::ast());
    }

    let plan = backend.plan_batch_rewrites(&config.rewrites, path)?;
    let plan = filter_batch_rewrite_plan(&plan, args)?;

    if !plan.rejected_overlaps.is_empty() {
        eprintln!(
            "[rewrite] {} overlapping edit(s) rejected",
            plan.rejected_overlaps.len()
        );
    }

    if plan.edits.is_empty() && plan.rejected_overlaps.is_empty() {
        if args.diff && args.json {
            let payload = BatchRewriteDiffJson {
                version: plan.version,
                routing_backend: plan.routing_backend,
                routing_reason: plan.routing_reason,
                sidecar_used: plan.sidecar_used,
                plan: &plan,
                diff: String::new(),
            };
            println!("{}", serde_json::to_string_pretty(&payload)?);
            return Ok(());
        }
        if args.json {
            println!("{}", serde_json::to_string_pretty(&plan)?);
            return Ok(());
        }
        eprintln!("[rewrite] no matches found, nothing to rewrite");
        return Ok(());
    }

    if args.diff {
        if plan.edits.is_empty() {
            eprintln!("[rewrite] no non-overlapping matches found, nothing to diff");
            return Ok(());
        }
        let diff = plan.generate_diff()?;
        if args.json {
            let payload = BatchRewriteDiffJson {
                version: plan.version,
                routing_backend: plan.routing_backend,
                routing_reason: plan.routing_reason,
                sidecar_used: plan.sidecar_used,
                plan: &plan,
                diff,
            };
            println!("{}", serde_json::to_string_pretty(&payload)?);
        } else {
            print!("{diff}");
        }
        return Ok(());
    }

    if !args.apply {
        println!("{}", serde_json::to_string_pretty(&plan)?);
        return Ok(());
    }

    let files_written = AstBackend::apply_batch_rewrites(&plan)?;
    if plan.edits.is_empty() {
        eprintln!("[rewrite] no non-overlapping edits applied");
    } else {
        eprintln!(
            "[rewrite] applied {} edit(s) across {} file(s)",
            plan.edits.len(),
            files_written
        );
    }

    Ok(())
}

fn handle_ast_batch_rewrite_apply(
    backend: &AstBackend,
    args: &RunArgs,
    config: &BatchRewriteConfig,
    path: &str,
) -> anyhow::Result<()> {
    if args.verbose {
        emit_verbose_metadata(RoutingDecision::ast());
    }

    let plan = backend.plan_batch_rewrites(&config.rewrites, path)?;
    let plan = filter_batch_rewrite_plan(&plan, args)?;

    if plan.edits.is_empty() && plan.rejected_overlaps.is_empty() {
        if args.json {
            let payload = BatchApplyVerifyJson {
                version: plan.version,
                routing_backend: plan.routing_backend,
                routing_reason: plan.routing_reason,
                sidecar_used: plan.sidecar_used,
                checkpoint: None,
                audit_manifest: None,
                plan: &plan,
                verification: None,
                validation: None,
                rollback: None,
            };
            println!("{}", serde_json::to_string_pretty(&payload)?);
        } else {
            eprintln!("[rewrite] no matches found, nothing to rewrite");
        }
        return Ok(());
    }

    let checkpoint = if args.checkpoint {
        let checkpoint = create_checkpoint(path)?;
        if !args.json {
            eprintln!(
                "[checkpoint] created {} ({}, files={})",
                checkpoint.checkpoint_id, checkpoint.mode, checkpoint.file_count
            );
        }
        Some(checkpoint)
    } else {
        None
    };

    let rollback_snapshots = if args.lint_cmd.is_some() || args.test_cmd.is_some() {
        Some(collect_validation_rollback_snapshots(&plan.edits)?)
    } else {
        None
    };

    let before_hashes = if args.audit_manifest.is_some() {
        Some(collect_pre_apply_hashes(&plan.edits)?)
    } else {
        None
    };

    AstBackend::apply_batch_rewrites(&plan)?;

    if !plan.rejected_overlaps.is_empty() && !args.json {
        eprintln!(
            "[rewrite] {} overlapping edit(s) rejected",
            plan.rejected_overlaps.len()
        );
    }

    if !args.json {
        if plan.edits.is_empty() {
            eprintln!("[rewrite] no non-overlapping edits applied");
        } else {
            eprintln!("[rewrite] applied {} edit(s)", plan.edits.len());
        }
    }

    let verification = if config.verify || args.verify {
        let result = plan.verify(backend)?;
        if !args.json {
            if result.mismatches.is_empty() {
                eprintln!(
                    "[verify] {}/{} edits verified",
                    result.verified, result.total_edits
                );
            } else {
                eprintln!(
                    "[verify] {}/{} edits verified, {} mismatches",
                    result.verified,
                    result.total_edits,
                    result.mismatches.len()
                );
            }
        }
        Some(result)
    } else {
        None
    };

    let validation = run_post_apply_validation(args, path, &plan.edits);
    if !args.json {
        if let Some(summary) = &validation {
            emit_validation_status(summary);
        }
    }

    let rollback = if let Some(summary) = &validation {
        if !summary.success {
            let rollback = rollback_snapshots
                .as_ref()
                .map(restore_validation_rollback_snapshots);
            if !args.json {
                if let Some(rollback_summary) = &rollback {
                    emit_rollback_status(rollback_summary);
                }
            }
            rollback
        } else {
            None
        }
    } else {
        None
    };

    let audit_manifest = if let Some(audit_manifest_path) = &args.audit_manifest {
        Some(write_audit_manifest_for_plan(AuditManifestWriteInput {
            path: audit_manifest_path,
            lang: &args.lang,
            root_path: path,
            edits: &plan.edits,
            plan_total_edits: plan.total_edits,
            checkpoint: checkpoint.as_ref(),
            validation: validation.as_ref(),
            before_hashes: before_hashes
                .as_ref()
                .expect("pre-apply hashes should exist when audit manifest requested"),
            signing_key_path: args.audit_signing_key.as_deref(),
        })?)
    } else {
        None
    };

    if args.json {
        let payload = BatchApplyVerifyJson {
            version: plan.version,
            routing_backend: plan.routing_backend,
            routing_reason: plan.routing_reason,
            sidecar_used: plan.sidecar_used,
            checkpoint: checkpoint.as_ref(),
            audit_manifest: audit_manifest.as_ref(),
            plan: &plan,
            verification: verification.as_ref(),
            validation: validation.as_ref(),
            rollback: rollback.as_ref(),
        };
        println!("{}", serde_json::to_string_pretty(&payload)?);
    }

    if let Some(summary) = &validation {
        if !summary.success {
            anyhow::bail!("post-apply validation failed");
        }
    }

    Ok(())
}

struct GpuSearchParams<'a> {
    patterns: &'a [String],
    query: &'a str,
    path: &'a str,
    #[cfg_attr(not(feature = "cuda"), allow(dead_code))]
    line_number: bool,
    ignore_case: bool,
    smart_case: bool,
    fixed_strings: bool,
    invert_match: bool,
    count: bool,
    context: Option<usize>,
    max_count: Option<usize>,
    word_regexp: bool,
    globs: Vec<String>,
    hidden: bool,
    max_depth: Option<usize>,
    text: bool,
    no_ignore: bool,
    gpu_device_ids: &'a [i32],
    json: bool,
    ndjson: bool,
    verbose: bool,
    // Task #131 F3 (Backend Fail-Closed Contract): these 5 fields used to have no home on this
    // struct at all, so every GPU-routed request silently dropped them -- `tg PAT
    // --gpu-device-ids 0 --replace X` printed "falling back to native CPU" and ran WITHOUT
    // --replace, exit 0. `replace`/`only_matching` mirror `NativeSearchConfig`'s own fields (it
    // CAN express them); `max_filesize`/`color`/`no_ignore_vcs` have no `NativeSearchConfig`
    // equivalent at all, so the CPU-fallback route must redirect to the rg passthrough or refuse
    // outright when one of those three is set (see `gpu_cpu_fallback_unhonorable_flag`) -- never
    // silently run without them.
    replace: Option<String>,
    only_matching: bool,
    max_filesize: Option<String>,
    color: Option<String>,
    no_ignore_vcs: bool,
    // Audit #105: whether the caller omitted an explicit PATH positional. Threaded into
    // `native_search_config_for_gpu_params`'s `NativeSearchConfig::path_was_implicit` (and the
    // rg_fallback `RipgrepSearchArgs` in `handle_gpu_native_search`) so the CPU fallback this
    // struct eventually reaches, when GPU routing is explicitly requested via
    // `--gpu-device-ids` but unavailable, still gets the native-CPU implicit-walk-ceiling gate.
    // "GPU search requires exactly one path root" (`request.paths.len() != 1`) does NOT imply
    // explicit -- the implicit default is itself a single `["."]` root, so that check alone
    // cannot be used as a stand-in for this field.
    path_was_implicit: bool,
}

#[cfg(feature = "cuda")]
fn handle_gpu_search(params: GpuSearchParams<'_>) -> anyhow::Result<()> {
    if let Some(reason) = gpu_native_fallback_reason(&params) {
        if params.verbose {
            eprintln!("[gpu-native] falling back to Python sidecar: {reason}");
        }
        return handle_gpu_sidecar_search(params);
    }

    handle_gpu_native_search(params)
}

#[cfg(not(feature = "cuda"))]
fn handle_gpu_search(params: GpuSearchParams<'_>) -> anyhow::Result<()> {
    if explicit_gpu_sidecar_is_available() {
        return handle_gpu_sidecar_search(params);
    }

    handle_gpu_unavailable_cpu_fallback(
        params,
        "native GPU unavailable in this binary; no CUDA-native front door is available",
    )
}

/// N3 (task #131 F3): mirror `index_flag_violations`'s established `--color` rule. The native CPU
/// engine (`NativeSearchConfig`, which has no `color` field at all) is a plain-text emitter that
/// never writes ANSI escapes, so `--color never`/`--color auto` restate what it already does and
/// are honorable no-ops; only `--color always` (or any unrecognized value) demands coloring this
/// engine cannot produce. Keeping this identical to the index path avoids two divergent `--color`
/// contracts for the same plain-text emitter.
fn gpu_color_is_unhonorable(color: Option<&str>) -> bool {
    matches!(color, Some(mode) if mode != "never" && mode != "auto")
}

/// Task #131 F3 (Backend Fail-Closed Contract): `NativeSearchConfig` -- the engine backing every
/// CPU fallback in `handle_gpu_unavailable_cpu_fallback` -- has no field for any of these three
/// (see its definition in `native_search.rs`), unlike `replace`/`only_matching` which it DOES
/// carry (threaded via `native_search_config_for_gpu_params`). Silently building a
/// `NativeSearchConfig` from a `GpuSearchParams` that has one of these three set would run the
/// search successfully while dropping the flag (exit 0, wrong output). Returns the flag's CLI
/// spelling so the caller can redirect to the rg passthrough (which DOES carry all three, see
/// `RipgrepSearchArgs`) or refuse outright -- never silently ignore it. `--color never`/`auto` are
/// deliberately excluded as honorable no-ops (see `gpu_color_is_unhonorable`).
fn gpu_cpu_fallback_unhonorable_flag(params: &GpuSearchParams<'_>) -> Option<&'static str> {
    if params.max_filesize.is_some() {
        Some("--max-filesize")
    } else if gpu_color_is_unhonorable(params.color.as_deref()) {
        Some("--color")
    } else if params.no_ignore_vcs {
        Some("--no-ignore-vcs")
    } else {
        None
    }
}

/// Fail closed (exit 2, mirrors `require_ripgrep_or_exit`'s `--pcre2` convention) when `flag_name`
/// is set on a `--gpu-device-ids` search that fell back to CPU, `NativeSearchConfig` cannot
/// express it, and the rg-passthrough escape hatch this fallback would otherwise use is not
/// usable here. Never silently drops `flag_name` (Backend Fail-Closed Contract, same class as
/// `--pcre2`).
fn exit_gpu_cpu_fallback_flag_unhonorable(flag_name: &str, rg_available: bool) {
    if rg_available {
        eprintln!(
            "error: {flag_name} is not supported by native GPU-fallback CPU search, and ripgrep's \
             output shape cannot represent structured --json/--ndjson output here; refusing rather \
             than silently ignoring {flag_name}. Drop --json/--ndjson, or drop {flag_name}."
        );
    } else {
        eprintln!(
            "error: {flag_name} is not supported by native GPU-fallback CPU search, and the \
             ripgrep (`rg`) backend is unavailable to honor it instead; refusing rather than \
             silently ignoring {flag_name}. Install `rg`, set TG_RG_PATH, or drop {flag_name}."
        );
    }
    std::process::exit(2);
}

/// Builds a `RipgrepSearchArgs` from a `GpuSearchParams` for the fail-closed CPU-fallback redirect
/// (task #131 F3). Every field this function does NOT set is one `GpuSearchParams` itself never
/// carries -- by construction, any flag with that property already routed the request away from
/// the GPU branch entirely before `GpuSearchParams` was built (`search_requires_ripgrep_passthrough`
/// / the disjoint PositionalCli/SearchArgs surfaces), so defaulting them here is not a second
/// silent drop.
fn ripgrep_args_for_gpu_params(params: &GpuSearchParams<'_>) -> RipgrepSearchArgs {
    RipgrepSearchArgs {
        patterns: params.patterns.to_vec(),
        paths: if params.path_was_implicit {
            Vec::new()
        } else {
            vec![params.path.to_string()]
        },
        path_was_implicit: params.path_was_implicit,
        ignore_case: params.ignore_case,
        smart_case: params.smart_case,
        fixed_strings: params.fixed_strings,
        invert_match: params.invert_match,
        count: params.count,
        line_number: params.line_number,
        no_line_number: !params.line_number,
        only_matching: params.only_matching,
        max_count: params.max_count,
        // M1 (gate must-fix): GpuSearchParams carries the single collapsed `context`
        // (`search_effective_context(&args)` at the tg-search sites). Forward it here so a
        // `--gpu-device-ids N -C 3 --color always` search redirected to rg does not silently drop
        // `-C 3` -- the native CPU fallback honored it via
        // `native_search_config_for_gpu_params` (before/after_context = params.context.unwrap_or(0)),
        // so dropping it in this rg redirect would trade a color-drop for a context-drop = the same
        // silent-wrong-output class this PR closes. Leaving before_context/after_context at their
        // `None` default mirrors that symmetric native behavior (GpuSearchParams has no separate
        // before/after; the collapsed value already folds `-A`/`-B` via search_effective_context).
        context: params.context,
        word_regexp: params.word_regexp,
        globs: params.globs.clone(),
        no_ignore: params.no_ignore,
        hidden: params.hidden,
        max_depth: params.max_depth,
        text: params.text,
        color: params.color.clone(),
        replace: params.replace.clone(),
        no_ignore_vcs: params.no_ignore_vcs,
        max_filesize: params.max_filesize.clone(),
        ..RipgrepSearchArgs::default()
    }
}

fn handle_gpu_unavailable_cpu_fallback(
    params: GpuSearchParams<'_>,
    warning: &str,
) -> anyhow::Result<()> {
    eprintln!(
        "warning: {warning}; falling back to native CPU search; this CPU fallback output is not GPU acceleration proof"
    );
    let rg_available = ripgrep_is_available();
    let decision =
        RoutingDecision::native_cpu_gpu_fallback(rg_available, params.json || params.ndjson);

    // Task #131 F3: `--max-filesize`/`--color`/`--no-ignore-vcs` cannot be expressed by the
    // native CPU engine this fallback would otherwise silently run. Redirect to rg (when its
    // output shape is usable here) or refuse outright -- never drop the flag.
    if let Some(flag_name) = gpu_cpu_fallback_unhonorable_flag(&params) {
        if !decision.allow_rg_fallback {
            exit_gpu_cpu_fallback_flag_unhonorable(flag_name, rg_available);
        }
        if params.verbose {
            emit_verbose_metadata(RoutingDecision::ripgrep());
        }
        let rg_args = ripgrep_args_for_gpu_params(&params);
        let exit_code = execute_ripgrep_search(&rg_args)?;
        if exit_code != 0 {
            std::process::exit(exit_code.max(1));
        }
        return Ok(());
    }

    let pattern = params.patterns.first().map_or(params.query, String::as_str);
    let cpu_config = native_search_config_for_gpu_params(&params, pattern, decision);
    if cpu_config.verbose {
        emit_verbose_metadata(decision);
    }
    if params.patterns.len() > 1 {
        let (matches, incomplete_paths) =
            collect_native_multi_pattern_matches(params.patterns, cpu_config)?;
        return emit_multi_pattern_native_results(
            NativeSearchOutputOptions {
                decision,
                query: params.query,
                path: params.path,
                requested_gpu_device_ids: params.gpu_device_ids,
                json: params.json,
                ndjson: params.ndjson,
                count: params.count,
                line_number: params.line_number,
                path_was_implicit: params.path_was_implicit,
            },
            matches,
            incomplete_paths,
        );
    }
    run_native_search_with_optional_rg_fallback(cpu_config, None)
}

#[cfg(not(feature = "cuda"))]
fn explicit_gpu_sidecar_is_available() -> bool {
    if env::var_os("TG_SIDECAR_SCRIPT").is_some() {
        return true;
    }
    env::var_os("TG_SIDECAR_PYTHON")
        .map(PathBuf::from)
        .is_some_and(|path| path.exists())
}

// GPU Phase-0 gate-nit #172 NIT-4 / MF-1: `GpuRouteFailureKind`, `GpuRouteFailure`,
// `sanitize_cuda_detail`, and `classify_gpu_route_failure` below are gated `any(feature = "cuda",
// test)` rather than plain `feature = "cuda"` so `cargo test` (default features, no cuda) can
// compile and RUN the 3 `classify_gpu_route_failure_*` tests in `mod tests` above -- previously
// those tests were ALSO `#[cfg(feature = "cuda")]`-gated, so a default `cargo test` silently
// never executed them at all. A bare un-gate of just the tests would not compile (the classifier
// and its types would still be absent by default); a bare un-gate of the classifier alone would
// leave it with zero callers in the default build (its production callers stay cuda-gated) and
// fail `cargo clippy -- -D warnings` on `dead_code`. Gating the definitions themselves on
// `any(feature = "cuda", test)` solves both: present whenever cuda is enabled (unchanged
// production behavior) OR whenever `cfg(test)` is set (so the tests below have something to call
// and are not themselves dead code), absent in the default clippy/release build (no dead_code).
#[cfg(any(feature = "cuda", test))]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum GpuRouteFailureKind {
    Unavailable,
    Fatal,
}

#[cfg(any(feature = "cuda", test))]
struct GpuRouteFailure {
    kind: GpuRouteFailureKind,
    message: String,
}

#[cfg(feature = "cuda")]
fn gpu_native_fallback_reason(params: &GpuSearchParams<'_>) -> Option<&'static str> {
    if gpu_params_require_case_insensitive_matching(params) {
        Some("case-insensitive searches are not yet supported by native GPU routing")
    } else if params.text {
        Some("binary-as-text searches are not yet supported by native GPU routing")
    } else if params
        .patterns
        .iter()
        .any(|pattern| pattern_contains_line_terminator(pattern))
    {
        Some("line-terminator patterns require CPU or sidecar routing")
    } else if params.invert_match {
        Some("invert-match searches are not yet supported by native GPU routing")
    } else if params.context.is_some() {
        Some("context line searches are not yet supported by native GPU routing")
    } else if params.max_count.is_some() {
        Some("max-count searches are not yet supported by native GPU routing")
    } else if params.word_regexp {
        Some("word-boundary searches are not yet supported by native GPU routing")
    } else if !params.fixed_strings && patterns_require_regex_engine(params.patterns) {
        Some("regex patterns still require the Python GPU sidecar")
    } else if params.replace.is_some() {
        // Task #131 F3: `GpuSearchParams` used to have no `replace` field at all, so this check
        // was structurally impossible before -- any `--replace` combined with `--gpu-device-ids`
        // silently reached native-GPU routing (or its CPU fallback) with the replacement dropped.
        Some("--replace searches are not yet supported by native GPU routing")
    } else if params.only_matching {
        Some("--only-matching searches are not yet supported by native GPU routing")
    } else if params.max_filesize.is_some() {
        Some("--max-filesize is not yet supported by native GPU routing")
    } else if params.color.is_some() {
        Some("--color is not yet supported by native GPU routing")
    } else if params.no_ignore_vcs {
        Some("--no-ignore-vcs is not yet supported by native GPU routing")
    } else {
        None
    }
}

#[cfg(feature = "cuda")]
fn pattern_contains_line_terminator(pattern: &str) -> bool {
    pattern
        .as_bytes()
        .iter()
        .any(|byte| matches!(byte, b'\n' | b'\r'))
}

#[cfg(feature = "cuda")]
fn gpu_params_require_case_insensitive_matching(params: &GpuSearchParams<'_>) -> bool {
    if params.ignore_case {
        return true;
    }
    params.smart_case
        && params
            .patterns
            .iter()
            .any(|pattern| smart_case_pattern_is_case_insensitive(pattern))
}

#[cfg(feature = "cuda")]
fn patterns_require_regex_engine(patterns: &[String]) -> bool {
    patterns
        .iter()
        .any(|pattern| pattern_requires_regex_engine(pattern))
}

#[cfg(feature = "cuda")]
fn pattern_requires_regex_engine(pattern: &str) -> bool {
    let mut escaped = false;
    for ch in pattern.chars() {
        if escaped {
            return true;
        }
        match ch {
            '\\' => escaped = true,
            '.' | '^' | '$' | '*' | '+' | '?' | '(' | ')' | '[' | ']' | '{' | '}' | '|' => {
                return true;
            }
            _ => {}
        }
    }
    escaped
}

#[cfg(feature = "cuda")]
fn simulated_gpu_route_failure() -> Option<GpuRouteFailure> {
    let value = env::var("TG_TEST_CUDA_BEHAVIOR").ok()?;
    let trimmed = value.trim();
    if trimmed.eq_ignore_ascii_case("no-devices") {
        return Some(GpuRouteFailure {
            kind: GpuRouteFailureKind::Unavailable,
            message: "CUDA is unavailable: no usable GPU devices were found".to_string(),
        });
    }
    if let Some(reason) = trimmed.strip_prefix("init-failure:") {
        return Some(GpuRouteFailure {
            kind: GpuRouteFailureKind::Fatal,
            message: format!(
                "CUDA initialization failed: {}",
                sanitize_cuda_detail(reason)
            ),
        });
    }
    if let Some(reason) = trimmed.strip_prefix("nvrtc-failure:") {
        return Some(GpuRouteFailure {
            kind: GpuRouteFailureKind::Fatal,
            message: format!("CUDA kernel compilation failed: {}", reason.trim()),
        });
    }
    if trimmed.eq_ignore_ascii_case("timeout") {
        return Some(GpuRouteFailure {
            kind: GpuRouteFailureKind::Fatal,
            message: "GPU operation timed out after 30s".to_string(),
        });
    }
    if trimmed.eq_ignore_ascii_case("oom") {
        return Some(GpuRouteFailure {
            kind: GpuRouteFailureKind::Fatal,
            message: "CUDA out of memory while allocating the requested GPU buffer".to_string(),
        });
    }
    if let Some(request) = trimmed.strip_prefix("oom:") {
        return Some(GpuRouteFailure {
            kind: GpuRouteFailureKind::Fatal,
            message: format!("CUDA out of memory while allocating {}", request.trim()),
        });
    }
    if let Some(duration) = trimmed.strip_prefix("timeout:") {
        return Some(GpuRouteFailure {
            kind: GpuRouteFailureKind::Fatal,
            message: format!("GPU operation timed out after {}", duration.trim()),
        });
    }
    if trimmed.eq_ignore_ascii_case("device-in-use") {
        return Some(GpuRouteFailure {
            kind: GpuRouteFailureKind::Fatal,
            message: "CUDA initialization failed: the selected GPU is currently in use".to_string(),
        });
    }
    None
}

#[cfg(any(feature = "cuda", test))]
fn sanitize_cuda_detail(raw: &str) -> String {
    let compact = raw.replace(['\r', '\n'], " ");
    let lower = compact.to_ascii_lowercase();
    if lower.contains("driver") && lower.contains("version") {
        return "driver version is too old".to_string();
    }
    if lower.contains("busy") || lower.contains("in use") {
        return "the selected GPU is currently in use".to_string();
    }
    if compact.contains("CUDA_ERROR") || compact.contains("DriverError") {
        return "the CUDA runtime reported an initialization error".to_string();
    }
    compact
        .trim()
        .trim_matches(|ch| ch == ':' || ch == '.')
        .to_string()
}

#[cfg(any(feature = "cuda", test))]
fn classify_gpu_route_failure(raw_message: &str) -> GpuRouteFailure {
    if raw_message.starts_with("CUDA is unavailable:") {
        return GpuRouteFailure {
            kind: GpuRouteFailureKind::Unavailable,
            message: raw_message.to_string(),
        };
    }
    // GPU-P0-3 (#171): `validate_requested_cuda_device_ids` raises this exact prefix for an
    // out-of-range --gpu-device-ids request. It is a Fatal (user-input) reason in its own right
    // -- catching it here, before the generic "CUDA initialization failed:" arm and the
    // lowercase-substring fallback below, keeps the catch-all from relabeling it as a driver/
    // hardware initialization problem it never was.
    if raw_message.starts_with("invalid CUDA device id") {
        return GpuRouteFailure {
            kind: GpuRouteFailureKind::Fatal,
            message: raw_message.to_string(),
        };
    }
    if raw_message.starts_with("CUDA initialization failed:") {
        return GpuRouteFailure {
            kind: GpuRouteFailureKind::Fatal,
            message: raw_message.to_string(),
        };
    }
    if raw_message.starts_with("CUDA kernel compilation failed:") {
        return GpuRouteFailure {
            kind: GpuRouteFailureKind::Fatal,
            message: raw_message.to_string(),
        };
    }
    if raw_message.starts_with("CUDA out of memory") {
        return GpuRouteFailure {
            kind: GpuRouteFailureKind::Fatal,
            message: raw_message.to_string(),
        };
    }
    if raw_message.starts_with("GPU operation timed out") {
        return GpuRouteFailure {
            kind: GpuRouteFailureKind::Fatal,
            message: raw_message.to_string(),
        };
    }

    let lower = raw_message.to_ascii_lowercase();
    if lower.contains("no usable gpu devices")
        || lower.contains("no cuda devices")
        || lower.contains("no device")
        || lower.contains("cuda is unavailable")
    {
        return GpuRouteFailure {
            kind: GpuRouteFailureKind::Unavailable,
            message: "CUDA is unavailable: no usable GPU devices were found".to_string(),
        };
    }
    if lower.contains("out of memory") || lower.contains("cuda_error_out_of_memory") {
        let detail = raw_message.trim();
        return GpuRouteFailure {
            kind: GpuRouteFailureKind::Fatal,
            message: if detail.is_empty() {
                "CUDA out of memory while allocating the requested GPU buffer".to_string()
            } else {
                format!("CUDA out of memory: {detail}")
            },
        };
    }

    GpuRouteFailure {
        kind: GpuRouteFailureKind::Fatal,
        message: format!(
            "CUDA initialization failed: {}",
            sanitize_cuda_detail(raw_message)
        ),
    }
}

// GPU-P0 gate-nit #172 NIT-3: `classify_gpu_route_failure`'s `Fatal` kind is deliberately coarse
// (kind + human message only) -- both emission sites below used to collapse EVERY Fatal straight
// to one native error kind ("gpu_fatal"), including the out-of-range --gpu-device-ids arm above
// (already given its own message-level branch in `classify_gpu_route_failure` so it is never
// relabeled as an init failure). The doctor/agent-capsule Python layer maps native error kinds to
// a status; "gpu_fatal" reads as "GPU unavailable", so a typo'd device id misreported as a
// capability gap instead of a user-input error. This is a thin, pure, directly-testable string
// check on the ALREADY-CLASSIFIED Fatal message -- deliberately NOT a 3rd `GpuRouteFailureKind`
// variant, which would force every `match failure.kind` call site's Fatal arm to add a case for a
// distinction only the WIRE error-kind string needs to make; the two enum variants remain the
// coarse "should we CPU-fallback or hard-fail" signal.
#[cfg(any(feature = "cuda", test))]
fn gpu_fatal_native_error_kind(message: &str) -> &'static str {
    if message.starts_with("invalid CUDA device id") {
        "gpu_invalid_device_id"
    } else {
        "gpu_fatal"
    }
}

#[cfg(feature = "cuda")]
fn gpu_native_config_from_internal_args(args: &GpuNativeStatsArgs) -> GpuNativeSearchConfig {
    GpuNativeSearchConfig {
        patterns: args.patterns.clone(),
        paths: vec![args.path.clone()],
        no_ignore: args.no_ignore,
        glob: args.globs.clone(),
        hidden: false,
        max_depth: None,
        max_batch_bytes: args.max_batch_bytes,
        // `GpuNativeStatsArgs::path` is a required `#[arg(long)]` (no default_value) -- this
        // diagnostic command always has an explicit, deliberately-scoped PATH, never an implicit
        // one, so the #109 ceiling must never fire here regardless of tree size.
        path_was_implicit: false,
    }
}

#[cfg(feature = "cuda")]
fn gpu_native_config_from_graph_args(args: &GpuCudaGraphArgs) -> GpuNativeSearchConfig {
    GpuNativeSearchConfig {
        patterns: args.patterns.clone(),
        paths: vec![args.path.clone()],
        no_ignore: args.no_ignore,
        glob: args.globs.clone(),
        hidden: false,
        max_depth: None,
        max_batch_bytes: args.max_batch_bytes,
        // `GpuCudaGraphArgs::path` is a required `#[arg(long)]` (no default_value) -- this
        // benchmark command always has an explicit, deliberately-scoped PATH, never an implicit
        // one, so the #109 ceiling must never fire here regardless of tree size.
        path_was_implicit: false,
    }
}

#[cfg(feature = "cuda")]
fn handle_gpu_native_stats_command(args: GpuNativeStatsArgs) -> anyhow::Result<()> {
    let mut stats = gpu_native_search_paths_multi(
        &gpu_native_config_from_internal_args(&args),
        &args.gpu_device_ids,
    )?;
    if args.summary_only {
        stats.matches.clear();
    }
    println!("{}", serde_json::to_string_pretty(&stats)?);
    Ok(())
}

#[cfg(feature = "cuda")]
fn handle_gpu_transfer_benchmark_command(args: GpuTransferBenchArgs) -> anyhow::Result<()> {
    let benchmark = match args.memory_kind {
        GpuTransferMemoryKind::Pinned => benchmark_pinned_transfer_throughput(
            args.device_id,
            args.total_bytes,
            args.batch_bytes,
        )?,
        GpuTransferMemoryKind::Pageable => benchmark_pageable_transfer_throughput(
            args.device_id,
            args.total_bytes,
            args.batch_bytes,
        )?,
    };
    println!("{}", serde_json::to_string_pretty(&benchmark)?);
    Ok(())
}

#[cfg(feature = "cuda")]
fn handle_gpu_cuda_graph_benchmark_command(args: GpuCudaGraphArgs) -> anyhow::Result<()> {
    let benchmark = benchmark_cuda_graph_search_paths(
        &gpu_native_config_from_graph_args(&args),
        args.device_id,
    )?;
    println!("{}", serde_json::to_string_pretty(&benchmark)?);
    Ok(())
}

#[cfg(feature = "cuda")]
fn handle_gpu_oom_probe_command(args: GpuOomProbeArgs) -> anyhow::Result<()> {
    match probe_device_allocation(args.device_id, args.bytes) {
        Ok(()) => {
            println!(
                "{}",
                serde_json::to_string_pretty(&serde_json::json!({
                    "status": "PASS",
                    "device_id": args.device_id,
                    "bytes": args.bytes,
                }))?
            );
            Ok(())
        }
        Err(err) => {
            let failure = classify_gpu_route_failure(&err.to_string());
            eprintln!("{}", failure.message);
            std::process::exit(2);
        }
    }
}

#[cfg(feature = "cuda")]
fn execute_gpu_native_route(
    params: &GpuSearchParams<'_>,
    decision: RoutingDecision,
    device_ids: &[i32],
) -> anyhow::Result<()> {
    if let Some(simulated) = simulated_gpu_route_failure() {
        anyhow::bail!(simulated.message);
    }

    if params.verbose {
        emit_verbose_metadata(decision);
    }

    let config = GpuNativeSearchConfig {
        patterns: params.patterns.to_vec(),
        paths: vec![PathBuf::from(params.path)],
        no_ignore: params.no_ignore,
        glob: params.globs.clone(),
        hidden: params.hidden,
        max_depth: params.max_depth,
        max_batch_bytes: None,
        // Audit #109 fix: this field did not exist on `GpuNativeSearchConfig` at all, so the
        // GPU-native engine had no equivalent of the #105 native-CPU implicit-walk ceiling --
        // `params.path_was_implicit` was already threaded into the CPU-fallback redirects
        // (`ripgrep_args_for_gpu_params`, `native_search_config_for_gpu_params`) but silently
        // dropped here, the one construction site that feeds the actual cuda kernel walk.
        path_was_implicit: params.path_was_implicit,
    };

    let stats = gpu_native_search_paths_multi(&config, device_ids)?;
    if params.verbose {
        emit_gpu_native_verbose(&stats);
    }

    if params.json {
        emit_gpu_native_json_results(decision, params, &stats)?;
    } else if params.ndjson {
        emit_ndjson_search_results(
            decision,
            params.query,
            params.path,
            params.gpu_device_ids,
            gpu_native_match_json_entries(&stats),
            // Task 316: this used to pass `None` with a comment claiming "this route observed no
            // walk of its own". That was FALSE -- `gpu_native_search_paths_multi` walks via
            // `collect_search_files` -> `collect_walked_files`; the count simply had nowhere to go.
            // It does now, and a comment asserting a false fact about its own route is exactly the
            // failure the gpu_native.rs note warned about. `None` still means "cannot report",
            // never "complete", so passing the real count is strictly more honest.
            Some(stats.walk_errors),
            params.path_was_implicit,
        )?;
    } else if params.count {
        emit_gpu_native_count_results(params, &stats)?;
    } else {
        emit_gpu_native_plain_results(params, &stats)?;
    }

    // Task 316, mirroring #818 on the multi-pattern route: an incomplete walk is checked BEFORE
    // the no-match branch, because a scan that could not read part of the tree must never report
    // a confident "0 matches" (exit 1) -- that reads as a genuine absence and is the exact
    // green-light-to-delete-live-code this campaign exists to prevent. Same shared predicate as
    // the envelope above, so disclosure and exit code cannot be derived independently.
    if walk_was_incomplete(Some(stats.walk_errors)) {
        std::process::exit(2);
    }

    if stats.total_matches == 0 {
        std::process::exit(1);
    }

    Ok(())
}

#[cfg(feature = "cuda")]
fn handle_auto_gpu_search(
    params: GpuSearchParams<'_>,
    cpu_fallback_config: NativeSearchConfig,
    rg_fallback: Option<RipgrepSearchArgs>,
) -> anyhow::Result<()> {
    let auto_device_ids = [0];
    match execute_gpu_native_route(
        &params,
        RoutingDecision::native_gpu_auto(),
        &auto_device_ids,
    ) {
        Ok(()) => Ok(()),
        Err(err) => {
            let failure = classify_gpu_route_failure(&err.to_string());
            match failure.kind {
                GpuRouteFailureKind::Unavailable => {
                    eprintln!(
                        "warning: {}; falling back to native CPU search; this CPU fallback output is not GPU acceleration proof",
                        failure.message
                    );
                    if cpu_fallback_config.verbose {
                        emit_verbose_metadata(RoutingDecision::native_cpu_gpu_fallback(
                            ripgrep_is_available(),
                            cpu_fallback_config.json || cpu_fallback_config.ndjson,
                        ));
                    }
                    if params.patterns.len() > 1 {
                        let (matches, incomplete_paths) = collect_native_multi_pattern_matches(
                            params.patterns,
                            cpu_fallback_config,
                        )?;
                        return emit_multi_pattern_native_results(
                            NativeSearchOutputOptions {
                                decision: RoutingDecision::native_cpu_gpu_fallback(
                                    ripgrep_is_available(),
                                    params.json || params.ndjson,
                                ),
                                query: params.query,
                                path: params.path,
                                requested_gpu_device_ids: params.gpu_device_ids,
                                json: params.json,
                                ndjson: params.ndjson,
                                count: params.count,
                                line_number: params.line_number,
                                path_was_implicit: params.path_was_implicit,
                            },
                            matches,
                            incomplete_paths,
                        );
                    }
                    run_native_search_with_optional_rg_fallback(cpu_fallback_config, rg_fallback)
                }
                GpuRouteFailureKind::Fatal => {
                    exit_structured_search_error_if_needed(
                        params.json,
                        params.ndjson,
                        gpu_fatal_native_error_kind(&failure.message),
                        failure.message,
                    );
                }
            }
        }
    }
}

#[cfg(feature = "cuda")]
fn handle_gpu_native_search(params: GpuSearchParams<'_>) -> anyhow::Result<()> {
    if params.gpu_device_ids.is_empty() {
        return handle_gpu_sidecar_search(params);
    }

    match execute_gpu_native_route(
        &params,
        RoutingDecision::native_gpu_explicit(),
        params.gpu_device_ids,
    ) {
        Ok(()) => Ok(()),
        Err(err) => {
            let failure = classify_gpu_route_failure(&err.to_string());
            match failure.kind {
                GpuRouteFailureKind::Unavailable => {
                    eprintln!(
                        "warning: {}; falling back to native CPU search; this CPU fallback output is not GPU acceleration proof",
                        failure.message
                    );
                    let rg_available = ripgrep_is_available();
                    let fallback_decision = RoutingDecision::native_cpu_gpu_fallback(
                        rg_available,
                        params.json || params.ndjson,
                    );
                    let cpu_config = native_search_config_for_gpu_params(
                        &params,
                        &params.patterns[0],
                        fallback_decision,
                    );
                    let rg_fallback =
                        fallback_decision
                            .allow_rg_fallback
                            .then(|| RipgrepSearchArgs {
                                files: false,
                                json: false,
                                ignore_case: params.ignore_case,
                                fixed_strings: params.fixed_strings,
                                no_fixed_strings: false,
                                invert_match: params.invert_match,
                                no_invert_match: false,
                                count: params.count,
                                count_matches: false,
                                line_number: params.line_number,
                                no_line_number: false,
                                column: false,
                                only_matching: false,
                                context: params.context,
                                before_context: None,
                                after_context: None,
                                max_count: params.max_count,
                                word_regexp: params.word_regexp,
                                smart_case: params.smart_case,
                                globs: params.globs.clone(),
                                ignore: false,
                                no_ignore: params.no_ignore,
                                no_ignore_dot: false,
                                no_ignore_exclude: false,
                                no_ignore_files: false,
                                no_ignore_global: false,
                                no_ignore_parent: false,
                                hidden: params.hidden,
                                require_git: false,
                                no_hidden: false,
                                follow: false,
                                text: params.text,
                                files_with_matches: false,
                                files_without_match: false,
                                file_types: Vec::new(),
                                color: None,
                                path_separator: None,
                                replace: None,
                                vimgrep: false,
                                passthru: false,
                                no_config: false,
                                sort: None,
                                sort_reverse: None,
                                sort_files: false,
                                max_depth: params.max_depth,
                                null: false,
                                null_data: false,
                                multiline: false,
                                no_multiline: false,
                                multiline_dotall: false,
                                no_multiline_dotall: false,
                                patterns: params.patterns.to_vec(),
                                paths: vec![params.path.to_string()],
                                // Audit #105 fix: this was hardcoded `false` under the incorrect
                                // assumption that "GPU search requires exactly one path root"
                                // (`request.paths.len() != 1` rejected upstream when
                                // `--gpu-device-ids` is set) implies the path is always explicit.
                                // It does not -- an implicit/defaulted root is itself a single
                                // `["."]` path, so `paths.len() != 1` never fires for it, and this
                                // rg fallback would have silently walked an implicit huge root
                                // unbounded. Now threaded from the real signal.
                                path_was_implicit: params.path_was_implicit,
                                no_ignore_vcs: false,
                                pcre2: false,
                                no_pcre2: false,
                                pcre2_unicode: false,
                                no_pcre2_unicode: false,
                                no_crlf: false,
                                no_encoding: false,
                                no_mmap: false,
                                no_pre: false,
                                no_search_zip: false,
                                auto_hybrid_regex: false,
                                no_auto_hybrid_regex: false,
                                unicode: false,
                                no_text: false,
                                no_binary: false,
                                no_follow: false,
                                no_glob_case_insensitive: false,
                                no_ignore_file_case_insensitive: false,
                                ignore_dot: false,
                                ignore_exclude: false,
                                ignore_files: false,
                                ignore_global: false,
                                ignore_messages: false,
                                ignore_parent: false,
                                ignore_vcs: false,
                                no_one_file_system: false,
                                no_block_buffered: false,
                                no_byte_offset: false,
                                no_column: false,
                                no_context_separator: false,
                                no_include_zero: false,
                                no_line_buffered: false,
                                no_max_columns_preview: false,
                                no_trim: false,
                                no_json: false,
                                messages: false,
                                no_stats: false,
                                max_filesize: None,
                            });
                    if cpu_config.verbose {
                        emit_verbose_metadata(fallback_decision);
                    }
                    if params.patterns.len() > 1 {
                        let (matches, incomplete_paths) =
                            collect_native_multi_pattern_matches(params.patterns, cpu_config)?;
                        return emit_multi_pattern_native_results(
                            NativeSearchOutputOptions {
                                decision: fallback_decision,
                                query: params.query,
                                path: params.path,
                                requested_gpu_device_ids: params.gpu_device_ids,
                                json: params.json,
                                ndjson: params.ndjson,
                                count: params.count,
                                line_number: params.line_number,
                                path_was_implicit: params.path_was_implicit,
                            },
                            matches,
                            incomplete_paths,
                        );
                    }
                    run_native_search_with_optional_rg_fallback(cpu_config, rg_fallback)
                }
                GpuRouteFailureKind::Fatal => {
                    exit_structured_search_error_if_needed(
                        params.json,
                        params.ndjson,
                        gpu_fatal_native_error_kind(&failure.message),
                        failure.message,
                    );
                }
            }
        }
    }
}

/// Builds the JSON payload sent to the Python GPU sidecar. Extracted to a pure function (task
/// #131 F3) so a unit test can assert field completeness without spawning the sidecar process --
/// this used to omit `replace`/`only_matching`/`max_filesize`/`color`/`no_ignore_vcs`/
/// `line_number` entirely, because `GpuSearchParams` had no fields to read them from.
fn gpu_sidecar_search_payload(params: &GpuSearchParams<'_>) -> serde_json::Value {
    serde_json::json!({
        "pattern": params.patterns.first().cloned().unwrap_or_default(),
        "patterns": params.patterns,
        "path": params.path,
        "ignore_case": params.ignore_case,
        "smart_case": params.smart_case,
        "fixed_strings": params.fixed_strings,
        "invert_match": params.invert_match,
        "count": params.count,
        "context": params.context,
        "max_count": params.max_count,
        "word_regexp": params.word_regexp,
        "globs": params.globs,
        "hidden": params.hidden,
        "max_depth": params.max_depth,
        "text": params.text,
        "no_ignore": params.no_ignore,
        "gpu_device_ids": params.gpu_device_ids,
        "json": params.json || params.ndjson,
        "line_number": params.line_number,
        "replace": params.replace,
        "only_matching": params.only_matching,
        "max_filesize": params.max_filesize,
        "color": params.color,
        "no_ignore_vcs": params.no_ignore_vcs,
    })
}

fn handle_gpu_sidecar_search(params: GpuSearchParams) -> anyhow::Result<()> {
    if params.verbose {
        emit_verbose_metadata(RoutingDecision::gpu_sidecar());
    }

    let payload = gpu_sidecar_search_payload(&params);

    match execute_sidecar_command("gpu_search", vec![], Some(payload)) {
        Ok(result) => {
            if result.exit_code != 0 {
                if let Some(reason) =
                    classify_gpu_sidecar_unavailable(&result.stderr, "Python sidecar exited")
                {
                    let warning = format!("native GPU unavailable: {reason}");
                    return handle_gpu_unavailable_cpu_fallback(params, &warning);
                }
                if !result.stdout.is_empty() {
                    print!("{}", result.stdout);
                }
                if !result.stderr.is_empty() {
                    eprint!("{}", result.stderr);
                }
                std::process::exit(result.exit_code.max(1));
            }
            if !result.stdout.is_empty() {
                if params.ndjson {
                    // EXEMPT from the raw-bytes/base64-fallback treatment (task #266): `entry`
                    // was JSON-deserialized from the Python sidecar's own stdout, which is
                    // itself a UTF-8-only wire format -- a non-UTF-8 byte could never have
                    // survived the sidecar's own JSON encoding to reach this point.
                    let matches = parse_gpu_sidecar_search_payload(&result.stdout)?
                        .matches
                        .into_iter()
                        .map(|entry| {
                            let (text, bytes, raw) = guaranteed_utf8_match_fields(entry.text);
                            SearchMatchJson {
                                file: entry.file,
                                line: entry.line_number,
                                text,
                                bytes,
                                raw,
                                range: None,
                                meta_variables: None,
                                pattern_id: entry.pattern_id,
                                pattern_text: entry.pattern_text,
                            }
                        })
                        .collect();
                    emit_ndjson_search_results(
                        RoutingDecision::gpu_sidecar(),
                        params.query,
                        params.path,
                        params.gpu_device_ids,
                        matches,
                        // Task 276: this route observed no walk of its own, so it cannot
                        // report a count. `None` means "cannot report", NEVER "complete".
                        None,
                        params.path_was_implicit,
                    )?;
                } else if params.json {
                    let normalized = normalize_gpu_sidecar_json(
                        &result.stdout,
                        params.gpu_device_ids,
                        params.path_was_implicit,
                    )?;
                    println!("{}", serde_json::to_string_pretty(&normalized)?);
                } else {
                    print!("{}", result.stdout);
                }
            }
            if !result.stderr.is_empty() {
                eprint!("{}", result.stderr);
            }
            Ok(())
        }
        Err(err) => {
            if let Some(reason) = classify_gpu_sidecar_unavailable(&err.stderr, &err.message) {
                let warning = format!("native GPU unavailable: {reason}");
                return handle_gpu_unavailable_cpu_fallback(params, &warning);
            }
            exit_with_sidecar_error(err)
        }
    }
}

fn classify_gpu_sidecar_unavailable(stderr: &str, message: &str) -> Option<String> {
    let mut raw = String::new();
    if !stderr.trim().is_empty() {
        raw.push_str(stderr.trim());
    }
    if !message.trim().is_empty() {
        if !raw.is_empty() {
            raw.push(' ');
        }
        raw.push_str(message.trim());
    }
    let lower = raw.to_ascii_lowercase();
    let unavailable = lower.contains("cuda_visible_devices is empty")
        || lower.contains("no gpus are visible")
        || lower.contains("cuda is unavailable")
        || lower.contains("no usable gpu devices")
        || lower.contains("no cuda devices")
        || lower.contains("available device ids: none")
        || (lower.contains("requested gpu device ids") && lower.contains("not available"));
    if unavailable {
        Some(if raw.is_empty() {
            "sidecar reported no usable GPU devices".to_string()
        } else {
            raw
        })
    } else {
        None
    }
}

// #92: classify's --stdin/--text share the sidecar's pre-existing `payload["content"]` path
// (sidecar.py:_classify_payload already prefers payload content over the positional file
// argument) -- no new IPC protocol, just wiring these two flags into the existing Optional
// payload parameter that execute_sidecar_command has always accepted.
fn handle_classify_command(args: ClassifyArgs) -> anyhow::Result<()> {
    if args.stdin_flag && args.text.is_some() {
        anyhow::bail!("tg classify --stdin cannot be combined with --text");
    }
    if args.stdin_flag && args.file_path.is_some() {
        anyhow::bail!("tg classify --stdin cannot be combined with a file path argument");
    }
    if args.text.is_some() && args.file_path.is_some() {
        anyhow::bail!("tg classify --text cannot be combined with a file path argument");
    }

    let sidecar_args = vec![
        "--format".to_string(),
        args.format,
        "--max-lines".to_string(),
        args.max_lines.to_string(),
    ];

    if args.stdin_flag {
        // Read to EOF; an empty/closed pipe yields an empty string rather than hanging, and
        // the sidecar's existing empty-content branch degrades cleanly (exit 1 with a message
        // instead of a silent hang or crash) -- see sidecar.py's `if not lines:` branch.
        let mut stdin_bytes = Vec::new();
        io::stdin().read_to_end(&mut stdin_bytes)?;
        let content = String::from_utf8_lossy(&stdin_bytes).into_owned();
        let payload = serde_json::json!({ "content": content });
        return handle_sidecar_command("classify", sidecar_args, Some(payload));
    }

    if let Some(text) = args.text {
        let payload = serde_json::json!({ "content": text });
        return handle_sidecar_command("classify", sidecar_args, Some(payload));
    }

    let file_path = args.file_path.ok_or_else(|| {
        anyhow::anyhow!("classify requires a file path, or --stdin, or --text <literal>")
    })?;
    if !Path::new(&file_path).exists() {
        anyhow::bail!(
            "classify expects a file path; use --text for a literal string or --stdin to read from stdin. Received: {}",
            file_path
        );
    }
    let mut full_args = sidecar_args;
    full_args.push(file_path);
    handle_sidecar_command("classify", full_args, None)
}

fn handle_sidecar_command(
    command: &str,
    args: Vec<String>,
    payload: Option<serde_json::Value>,
) -> anyhow::Result<()> {
    match execute_sidecar_command(command, args, payload) {
        Ok(result) => {
            let _ = result.sidecar_pid;
            if !result.stdout.is_empty() {
                print!("{}", result.stdout);
            }
            if !result.stderr.is_empty() {
                eprint!("{}", result.stderr);
            }
            if result.exit_code != 0 {
                std::process::exit(result.exit_code.max(1));
            }
            Ok(())
        }
        Err(err) => exit_with_sidecar_error(err),
    }
}

fn handle_python_passthrough(command: &str, args: Vec<String>) -> anyhow::Result<()> {
    match execute_python_passthrough_command(command, args) {
        Ok(exit_code) => {
            if exit_code != 0 {
                std::process::exit(exit_code.max(1));
            }
            Ok(())
        }
        Err(err) => exit_with_sidecar_error(err),
    }
}

fn handle_python_passthrough_with_stdin(
    command: &str,
    args: Vec<String>,
    stdin_bytes: Vec<u8>,
) -> anyhow::Result<()> {
    match execute_python_passthrough_command_with_stdin(command, args, stdin_bytes) {
        Ok(exit_code) => {
            if exit_code != 0 {
                std::process::exit(exit_code.max(1));
            }
            Ok(())
        }
        Err(err) => exit_with_sidecar_error(err),
    }
}

fn exit_with_sidecar_error(err: SidecarError) -> anyhow::Result<()> {
    if !err.stderr.is_empty() {
        eprint!("{}", err.stderr);
    }
    eprintln!("{}", err.message);
    std::process::exit(err.exit_code.max(1));
}

/// Task 276 task 6: the ONE predicate for "this walk did not finish".
///
/// `None` means "this route observed no walk of its own, so it cannot report a count" -- NEVER
/// "complete". Only a `Some(count)` above zero is an affirmative incompleteness claim.
///
/// Both the envelope fields below AND the exit code read this, so a route cannot say
/// `result_incomplete: true` in its payload and then exit 0. That divergence was the shape of
/// task 6: the disclosure and the exit code were derived independently, so one twin could ship
/// without the other.
fn walk_was_incomplete(incomplete_paths: Option<usize>) -> bool {
    matches!(incomplete_paths, Some(count) if count > 0)
}

/// Task 276: turn a walk-error count into the three envelope fields.
///
/// ONE place, so the `--json` envelope and the `--ndjson` summary cannot drift -- the #276
/// family is a long record of one route disclosing while its twin stayed silent.
fn incomplete_envelope_fields(
    incomplete_paths: Option<usize>,
) -> (Option<bool>, Option<&'static str>, Option<usize>) {
    if walk_was_incomplete(incomplete_paths) {
        return (Some(true), Some("unreadable_path"), incomplete_paths);
    }
    (None, None, None)
}

fn emit_json_search_results(
    decision: RoutingDecision,
    pattern: &str,
    path: &str,
    requested_gpu_device_ids: &[i32],
    matches: Vec<SearchMatchJson>,
    incomplete_paths: Option<usize>,
    path_was_implicit: bool,
) -> anyhow::Result<()> {
    let proof_fields = gpu_proof_fields(
        requested_gpu_device_ids,
        decision.routing_backend(),
        decision.sidecar_used(),
    );
    let mut match_counts_by_file = std::collections::BTreeMap::<String, usize>::new();
    for matched in &matches {
        *match_counts_by_file
            .entry(matched.file.clone())
            .or_insert(0) += 1;
    }
    let matched_file_paths = match_counts_by_file.keys().cloned().collect::<Vec<_>>();
    let (result_incomplete, incomplete_reason_class, incomplete_paths_count) =
        incomplete_envelope_fields(incomplete_paths);
    let (path_was_defaulted, scope_note) = defaulted_scope_fields(path_was_implicit, matches.len());
    let payload = SearchResultJson {
        version: JSON_OUTPUT_VERSION,
        routing_backend: decision.routing_backend(),
        routing_reason: decision.reason,
        sidecar_used: decision.sidecar_used(),
        requested_gpu_device_ids: requested_gpu_device_ids.to_vec(),
        routing_gpu_device_ids: Vec::new(),
        gpu_evidence_status: proof_fields.gpu_evidence_status,
        gpu_proof: proof_fields.gpu_proof,
        native_gpu_unavailable: proof_fields.native_gpu_unavailable,
        not_gpu_proof_reason: proof_fields.not_gpu_proof_reason,
        query: pattern,
        path,
        total_files: matched_file_paths.len(),
        total_matches: matches.len(),
        matched_file_paths,
        match_counts_by_file,
        matches,
        result_incomplete,
        incomplete_reason_class,
        incomplete_paths_count,
        path_was_defaulted,
        scope_note,
    };

    println!("{}", serde_json::to_string(&payload)?);
    Ok(())
}

fn unique_line_matches(matches: &[SearchMatchJson]) -> Vec<SearchMatchJson> {
    let mut seen = std::collections::BTreeSet::new();
    let mut unique = Vec::new();
    for matched in matches {
        // `raw`, not `text` (task #266): two matches with genuinely different non-UTF-8 content
        // both report `text: None`, so keying on `text` would wrongly collapse them into a
        // single deduped entry -- `raw` is the byte-exact source of truth every producer
        // populates, valid-UTF-8 or not.
        let key = (matched.file.clone(), matched.line, matched.raw.clone());
        if seen.insert(key) {
            let mut deduped = matched.clone();
            deduped.pattern_id = None;
            deduped.pattern_text = None;
            unique.push(deduped);
        }
    }
    unique
}

/// Writes each matched line's raw bytes directly to stdout (never through `println!`/`Display`
/// on `matched.text`), so a genuinely non-UTF-8 match -- reachable via the multi-pattern native
/// path and, since task #273, the CUDA-gated GPU-native path -- prints byte-for-byte instead of
/// via a lossy `Option<String>` unwrap (task #266: the same byte-fidelity guarantee
/// `append_standard_match_bytes` gives the single-pattern native emitter, extended to this shared
/// plain-text writer).
///
/// A closed output pipe (`tg ... | head -1`) is a normal, expected termination and returns
/// `Ok(())` quietly; any OTHER write failure (disk full, I/O error) is a real error and is
/// propagated rather than silently swallowed. `emit_count_search_matches` below applies the
/// identical rule, so the two plain-text emitters no longer disagree on write-failure behavior.
fn emit_plain_search_matches_with_line_number(
    path: &str,
    matches: &[SearchMatchJson],
    line_number: bool,
) -> anyhow::Result<()> {
    let unique = unique_line_matches(matches);
    let with_filename = unique
        .iter()
        .map(|matched| matched.file.as_str())
        .collect::<std::collections::BTreeSet<_>>()
        .len()
        > 1
        || Path::new(path).is_dir();
    let stdout = io::stdout();
    let mut stdout = stdout.lock();
    for matched in unique {
        let mut line = Vec::with_capacity(matched.raw.len() + matched.file.len() + 32);
        match (with_filename, line_number) {
            (true, true) => {
                write!(line, "{}:{}:", matched.file, matched.line)?;
            }
            (true, false) => {
                write!(line, "{}:", matched.file)?;
            }
            (false, true) => {
                write!(line, "{}:", matched.line)?;
            }
            (false, false) => {}
        }
        line.extend_from_slice(&matched.raw);
        line.push(b'\n');
        if let Err(err) = stdout.write_all(&line) {
            if err.kind() == io::ErrorKind::BrokenPipe {
                return Ok(());
            }
            return Err(err.into());
        }
    }
    Ok(())
}

/// Same closed-pipe-is-quiet / other-errors-propagate rule as
/// `emit_plain_search_matches_with_line_number` above (task #266 cleanup): this used to be bare
/// `println!`, which PANICS on any write failure (the stdlib `print!`/`println!` macros
/// `.expect(...)` internally) -- including the routine closed-pipe case every other emitter in
/// this file already handles quietly.
fn emit_count_search_matches(path: &str, matches: &[SearchMatchJson]) -> anyhow::Result<()> {
    let unique = unique_line_matches(matches);
    let with_filename = unique
        .iter()
        .map(|matched| matched.file.as_str())
        .collect::<std::collections::BTreeSet<_>>()
        .len()
        > 1
        || Path::new(path).is_dir();
    let mut counts = std::collections::BTreeMap::<String, usize>::new();
    for matched in unique {
        *counts.entry(matched.file).or_default() += 1;
    }

    let stdout = io::stdout();
    let mut stdout = stdout.lock();
    let write_result = if with_filename {
        counts
            .into_iter()
            .try_for_each(|(file, count)| writeln!(stdout, "{file}:{count}"))
    } else {
        writeln!(stdout, "{}", counts.values().copied().next().unwrap_or(0))
    };
    if let Err(err) = write_result {
        if err.kind() == io::ErrorKind::BrokenPipe {
            return Ok(());
        }
        return Err(err.into());
    }
    Ok(())
}

#[cfg(feature = "cuda")]
fn gpu_native_match_json_entries(stats: &GpuNativeSearchStats) -> Vec<SearchMatchJson> {
    // task #273: this used to be the one remaining NOT-exempt-but-treated-as-exempt producer --
    // `GpuNativeSearchMatch.text` was built in `gpu_native.rs` via
    // `String::from_utf8_lossy(line_bytes).trim_end_matches('\r')`, the identical
    // lossy-conversion + over-trim defect class task #266/#746 fixed in the CPU native-search
    // emitter, then routed through `guaranteed_utf8_match_fields` (a straight type-compatibility
    // wrap that does NOT certify losslessness the way it does for TrigramIndex/AST/the GPU
    // sidecar). `GpuNativeSearchMatch` now carries `raw: Vec<u8>` instead, produced without any
    // lossy conversion in `gpu_native.rs`, so this mirrors the multi-pattern native path's own
    // `native_json_text_fields(&matched.raw)` call below (task #271) rather than
    // `guaranteed_utf8_match_fields`.
    //
    // CI COVERAGE, precisely: `cuda-feature-check` (.github/workflows/ci.yml) runs
    // `cargo check --features cuda --all-targets`. `test-rust-core` builds DEFAULT features and
    // never compiles `gpu_native` at all (it is gated at lib.rs by `#[cfg(feature = "cuda")]`),
    // so this job is the ONLY oracle for cuda-gated code. `--all-targets` is LOAD-BEARING, not
    // tidiness: a bare `cargo check` compiles only normal targets, leaving `gpu_native.rs`'s
    // `#[cfg(test)]` module and the `tests/test_gpu_native_*.rs` integration targets entirely
    // un-type-checked -- which is exactly how eight `.text` reads survived this PR's first cut
    // and surfaced only as `E0609` once `--all-targets` was added. Do not drop it.
    //
    // What that still does NOT buy: those tests are type-checked but NEVER EXECUTED anywhere in
    // CI -- checking is not running, and no CUDA runner exists. Task #279 tracks whether
    // `cargo test --features cuda` can link on a GPU-less runner; until it is answered, treat
    // cuda-gated tests as compile-time protection only.
    stats
        .matches
        .iter()
        .map(|matched| {
            let (text, bytes) = native_json_text_fields(&matched.raw);
            let text = text.map(str::to_string);
            SearchMatchJson {
                file: matched.path.to_string_lossy().into_owned(),
                line: matched.line_number,
                text,
                bytes,
                raw: matched.raw.clone(),
                range: None,
                meta_variables: None,
                pattern_id: (stats.pattern_count > 1).then_some(matched.pattern_id),
                pattern_text: (stats.pattern_count > 1).then(|| matched.pattern_text.clone()),
            }
        })
        .collect()
}

#[cfg(feature = "cuda")]
fn emit_gpu_native_json_results(
    decision: RoutingDecision,
    params: &GpuSearchParams<'_>,
    stats: &GpuNativeSearchStats,
) -> anyhow::Result<()> {
    let proof_fields = gpu_proof_fields(
        params.gpu_device_ids,
        decision.routing_backend(),
        decision.sidecar_used(),
    );
    // Task 316. Reuse the helper the CPU envelopes use rather than re-deriving the triple here:
    // re-deriving truncation locally is the exact defect class this campaign closes (task 332
    // found 3 of 3 repo_map gates deadline-blind for that reason), and `walk_was_incomplete`
    // below reads the SAME `Some(stats.walk_errors)` so the payload and the exit code cannot
    // disagree.
    let (result_incomplete, incomplete_reason_class, incomplete_paths_count) =
        incomplete_envelope_fields(Some(stats.walk_errors));
    let (path_was_defaulted, scope_note) =
        defaulted_scope_fields(params.path_was_implicit, stats.total_matches);
    let payload = GpuNativeSearchResultJson {
        version: JSON_OUTPUT_VERSION,
        routing_backend: decision.routing_backend(),
        routing_reason: decision.reason,
        sidecar_used: decision.sidecar_used(),
        query: params.query,
        path: params.path,
        total_matches: stats.total_matches,
        total_files: stats.matched_files,
        requested_gpu_device_ids: params.gpu_device_ids.to_vec(),
        routing_gpu_device_ids: stats
            .selected_devices
            .iter()
            .map(|device| device.device_id)
            .collect(),
        gpu_evidence_status: proof_fields.gpu_evidence_status,
        gpu_proof: proof_fields.gpu_proof,
        native_gpu_unavailable: proof_fields.native_gpu_unavailable,
        not_gpu_proof_reason: proof_fields.not_gpu_proof_reason,
        result_incomplete,
        incomplete_reason_class,
        incomplete_paths_count,
        path_was_defaulted,
        scope_note,
        pipeline: &stats.pipeline,
        matches: gpu_native_match_json_entries(stats),
    };

    println!("{}", serde_json::to_string_pretty(&payload)?);
    Ok(())
}

#[cfg(feature = "cuda")]
fn emit_gpu_native_plain_results(
    params: &GpuSearchParams<'_>,
    stats: &GpuNativeSearchStats,
) -> anyhow::Result<()> {
    // Task #131 F3: this used to call the now-removed `emit_plain_search_matches`, which
    // hardcoded `line_number: true` regardless of `-N`/`--no-line-number` -- the double bug
    // (`GpuSearchParams::line_number` was ALSO hardcoded at every construction site until this
    // same fix). Thread the real, derived value through instead.
    let matches = gpu_native_match_json_entries(stats);
    emit_plain_search_matches_with_line_number(params.path, &matches, params.line_number)
}

#[cfg(feature = "cuda")]
fn emit_gpu_native_count_results(
    params: &GpuSearchParams<'_>,
    stats: &GpuNativeSearchStats,
) -> anyhow::Result<()> {
    let matches = gpu_native_match_json_entries(stats);
    emit_count_search_matches(params.path, &matches)
}

#[cfg(feature = "cuda")]
fn emit_gpu_native_verbose(stats: &GpuNativeSearchStats) {
    if stats.selected_devices.len() <= 1 {
        eprintln!(
            "[gpu-native] selected_gpu_device_id={} selected_gpu_device_name={} gpu_batch_files={} gpu_transfer_bytes={} gpu_streams={} gpu_double_buffered={} pinned_host_buffers={} gpu_batch_count={} gpu_overlap_batches={} gpu_pattern_count={} gpu_pattern_batches={} gpu_single_dispatch={} gpu_transfer_time_ms={:.3} gpu_kernel_time_ms={:.3} gpu_host_file_read_time_ms={:.3} gpu_host_preprocess_time_ms={:.3} gpu_host_to_pinned_copy_time_ms={:.3} gpu_cpu_staging_bytes={} gpu_pageable_host_staging_bytes={} gpu_transfer_throughput_gbps={:.2}",
            stats.selected_device.device_id,
            stats.selected_device.name,
            stats.searched_files,
            stats.transfer_bytes,
            stats.pipeline.stream_count,
            stats.pipeline.double_buffered,
            stats.pipeline.pinned_host_buffers,
            stats.pipeline.batch_count,
            stats.pipeline.overlapped_batches,
            stats.pipeline.pattern_count,
            stats.pipeline.pattern_batch_count,
            stats.pipeline.single_dispatch,
            stats.pipeline.transfer_time_ms,
            stats.pipeline.kernel_time_ms,
            stats.pipeline.host_file_read_time_ms,
            stats.pipeline.host_preprocess_time_ms,
            stats.pipeline.host_to_pinned_copy_time_ms,
            stats.pipeline.cpu_staging_bytes,
            stats.pipeline.pageable_host_staging_bytes,
            stats.pipeline.transfer_throughput_bytes_s / 1_000_000_000.0
        );
        return;
    }
    eprintln!(
        "[gpu-native] selected_gpu_device_ids={} selected_gpu_device_names={} gpu_batch_files={} gpu_transfer_bytes={} gpu_streams={} gpu_double_buffered={} pinned_host_buffers={} gpu_batch_count={} gpu_overlap_batches={} gpu_pattern_count={} gpu_pattern_batches={} gpu_single_dispatch={} gpu_transfer_time_ms={:.3} gpu_kernel_time_ms={:.3} gpu_host_file_read_time_ms={:.3} gpu_host_preprocess_time_ms={:.3} gpu_host_to_pinned_copy_time_ms={:.3} gpu_cpu_staging_bytes={} gpu_pageable_host_staging_bytes={} gpu_transfer_throughput_gbps={:.2}",
        stats.selected_devices.iter().map(|d| d.device_id.to_string()).collect::<Vec<_>>().join(","),
        stats.selected_devices.iter().map(|d| d.name.as_str()).collect::<Vec<_>>().join(" | "),
        stats.searched_files,
        stats.transfer_bytes,
        stats.pipeline.stream_count,
        stats.pipeline.double_buffered,
        stats.pipeline.pinned_host_buffers,
        stats.pipeline.batch_count,
        stats.pipeline.overlapped_batches,
        stats.pipeline.pattern_count,
        stats.pipeline.pattern_batch_count,
        stats.pipeline.single_dispatch,
        stats.pipeline.transfer_time_ms,
        stats.pipeline.kernel_time_ms,
        stats.pipeline.host_file_read_time_ms,
        stats.pipeline.host_preprocess_time_ms,
        stats.pipeline.host_to_pinned_copy_time_ms,
        stats.pipeline.cpu_staging_bytes,
        stats.pipeline.pageable_host_staging_bytes,
        stats.pipeline.transfer_throughput_bytes_s / 1_000_000_000.0
    );

    for device_stats in &stats.device_stats {
        eprintln!(
            "[gpu-native] gpu_device_id={} gpu_device_name={} gpu_device_files={} gpu_device_matches={} gpu_device_transfer_bytes={} gpu_device_streams={} gpu_device_batch_count={} gpu_device_transfer_time_ms={:.3} gpu_device_kernel_time_ms={:.3} gpu_device_host_file_read_time_ms={:.3} gpu_device_host_preprocess_time_ms={:.3} gpu_device_host_to_pinned_copy_time_ms={:.3} gpu_device_cpu_staging_bytes={} gpu_device_pageable_host_staging_bytes={} gpu_device_transfer_throughput_gbps={:.2}",
            device_stats.device.device_id,
            device_stats.device.name,
            device_stats.searched_files,
            device_stats.total_matches,
            device_stats.transfer_bytes,
            device_stats.pipeline.stream_count,
            device_stats.pipeline.batch_count,
            device_stats.pipeline.transfer_time_ms,
            device_stats.pipeline.kernel_time_ms,
            device_stats.pipeline.host_file_read_time_ms,
            device_stats.pipeline.host_preprocess_time_ms,
            device_stats.pipeline.host_to_pinned_copy_time_ms,
            device_stats.pipeline.cpu_staging_bytes,
            device_stats.pipeline.pageable_host_staging_bytes,
            device_stats.pipeline.transfer_throughput_bytes_s / 1_000_000_000.0
        );
    }
}

fn emit_ndjson_search_results(
    decision: RoutingDecision,
    pattern: &str,
    path: &str,
    requested_gpu_device_ids: &[i32],
    matches: Vec<SearchMatchJson>,
    incomplete_paths: Option<usize>,
    path_was_implicit: bool,
) -> anyhow::Result<()> {
    let total_matches = matches.len();
    for matched in matches {
        let proof_fields = gpu_proof_fields(
            requested_gpu_device_ids,
            decision.routing_backend(),
            decision.sidecar_used(),
        );
        let payload = SearchMatchNdjson {
            version: JSON_OUTPUT_VERSION,
            routing_backend: decision.routing_backend(),
            routing_reason: decision.reason,
            sidecar_used: decision.sidecar_used(),
            requested_gpu_device_ids: requested_gpu_device_ids.to_vec(),
            routing_gpu_device_ids: Vec::new(),
            gpu_evidence_status: proof_fields.gpu_evidence_status,
            gpu_proof: proof_fields.gpu_proof,
            native_gpu_unavailable: proof_fields.native_gpu_unavailable,
            not_gpu_proof_reason: proof_fields.not_gpu_proof_reason,
            query: pattern,
            path,
            file: &matched.file,
            line: matched.line,
            // Reuse the fields the producer already computed (via `native_json_text_fields` or
            // `guaranteed_utf8_match_fields`) -- no need to re-derive from `matched.raw` here.
            text: matched.text.as_deref(),
            bytes: matched.bytes.clone(),
            pattern_id: matched.pattern_id,
            pattern_text: matched.pattern_text.as_deref(),
        };
        println!("{}", serde_json::to_string(&payload)?);
    }

    let (result_incomplete, incomplete_reason_class, incomplete_paths_count) =
        incomplete_envelope_fields(incomplete_paths);
    let (path_was_defaulted, scope_note) = defaulted_scope_fields(path_was_implicit, total_matches);
    println!(
        "{}",
        serde_json::to_string(&SearchSummaryNdjson {
            record_type: "summary",
            version: JSON_OUTPUT_VERSION,
            total_matches,
            result_incomplete,
            incomplete_reason_class,
            incomplete_paths_count,
            path_was_defaulted,
            scope_note,
        })?
    );

    Ok(())
}

fn parse_gpu_sidecar_search_payload(stdout: &str) -> anyhow::Result<GpuSidecarSearchPayload> {
    serde_json::from_str(stdout).map_err(|err| {
        anyhow::anyhow!(
            "GPU sidecar returned malformed search JSON payload: expected {{total_matches, total_files, matches[]}} with string file/text fields and integer line_number values ({err})"
        )
    })
}

/// Task #26, THE FIFTH ENVELOPE -- and the one a structural sweep cannot see.
///
/// The other four (`NativeJsonOutput`, `SearchResultJson`, `SearchSummaryNdjson`,
/// `GpuNativeSearchResultJson`) are `#[derive(Serialize)]` structs, so adding a field there is a
/// visible, type-checked edit. This one is a HAND-BUILT `serde_json::json!()` value: it emits the
/// same document shape while sharing no type with any of them, so it silently kept its pre-#26
/// shape while all four siblings gained the disclosure. It is also NOT cuda-gated -- it is live in
/// every build, on the GPU-sidecar route.
///
/// The lesson, and the reason this comment is long: **enumerate EMITTERS, not derive-macros.** A
/// census keyed on `#[derive(Serialize)]` reports "4 of 4 covered" and is wrong by one.
fn normalize_gpu_sidecar_json(
    stdout: &str,
    requested_gpu_device_ids: &[i32],
    path_was_implicit: bool,
) -> anyhow::Result<serde_json::Value> {
    let payload = parse_gpu_sidecar_search_payload(stdout)?;
    let proof_fields = gpu_proof_fields(
        requested_gpu_device_ids,
        RoutingDecision::gpu_sidecar().routing_backend(),
        RoutingDecision::gpu_sidecar().sidecar_used(),
    );
    let requested_gpu_device_ids = requested_gpu_device_ids
        .iter()
        .copied()
        .filter(|device_id| *device_id >= 0)
        .map(|device_id| device_id as u32)
        .collect::<Vec<_>>();

    let normalized_matches = payload
        .matches
        .into_iter()
        .map(|entry| {
            let mut value = serde_json::json!({
                "file": entry.file,
                "line_number": entry.line_number,
                "text": entry.text,
            });
            if let Some(pattern_id) = entry.pattern_id {
                value["pattern_id"] = serde_json::json!(pattern_id);
            }
            if let Some(pattern_text) = entry.pattern_text {
                value["pattern_text"] = serde_json::json!(pattern_text);
            }
            value
        })
        .collect::<Vec<_>>();

    let mut value = serde_json::json!({
        "version": JSON_OUTPUT_VERSION,
        "routing_backend": RoutingDecision::gpu_sidecar().routing_backend(),
        "routing_reason": RoutingDecision::gpu_sidecar().reason,
        "sidecar_used": RoutingDecision::gpu_sidecar().sidecar_used(),
        "total_matches": payload.total_matches,
        "total_files": payload.total_files,
        "requested_gpu_device_ids": requested_gpu_device_ids,
        "routing_gpu_device_ids": payload.routing_gpu_device_ids,
        "matches": normalized_matches,
    });
    if let Some(gpu_evidence_status) = proof_fields.gpu_evidence_status {
        value["gpu_evidence_status"] = serde_json::json!(gpu_evidence_status);
    }
    if let Some(gpu_proof) = proof_fields.gpu_proof {
        value["gpu_proof"] = serde_json::json!(gpu_proof);
    }
    if let Some(native_gpu_unavailable) = proof_fields.native_gpu_unavailable {
        value["native_gpu_unavailable"] = serde_json::json!(native_gpu_unavailable);
    }
    if let Some(not_gpu_proof_reason) = proof_fields.not_gpu_proof_reason {
        value["not_gpu_proof_reason"] = serde_json::json!(not_gpu_proof_reason);
    }
    // Task #26. Same shared gate as every sibling envelope, so this route cannot drift from them
    // -- which is exactly what it had already done by being invisible to a derive-macro census.
    // Insert-when-applicable mirrors the siblings' `skip_serializing_if`: an explicitly-scoped
    // search adds neither key and stays byte-identical for existing consumers.
    let (path_was_defaulted, scope_note) =
        defaulted_scope_fields(path_was_implicit, payload.total_matches);
    if let Some(path_was_defaulted) = path_was_defaulted {
        value["path_was_defaulted"] = serde_json::json!(path_was_defaulted);
    }
    if let Some(scope_note) = scope_note {
        value["scope_note"] = serde_json::json!(scope_note);
    }
    Ok(value)
}

fn emit_verbose_metadata(decision: RoutingDecision) {
    eprintln!(
        "[routing] routing_backend={} routing_reason={} sidecar_used={}",
        decision.routing_backend(),
        decision.reason,
        decision.sidecar_used()
    );
}

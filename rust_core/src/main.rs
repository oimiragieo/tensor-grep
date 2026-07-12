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
use std::ffi::OsString;
#[cfg(feature = "cuda")]
use std::fs;
use std::io::{self, Read, Write};
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::{Arc, Mutex};
use std::time::Instant;
use std::time::{Duration, SystemTime, UNIX_EPOCH};
use tensor_grep_rs::backend_ast::{
    AstBackend, AstMatch, AstMetaVariables, BatchRewritePlan, BatchRewriteRule,
};
use tensor_grep_rs::crossover::{run_crossover_calibration, write_crossover_config};
#[cfg(feature = "cuda")]
use tensor_grep_rs::gpu_native::{
    benchmark_cuda_graph_search_paths, benchmark_pageable_transfer_throughput,
    benchmark_pinned_transfer_throughput, enumerate_cuda_devices, gpu_native_search_paths_multi,
    probe_device_allocation, GpuNativeSearchConfig, GpuNativeSearchStats, GpuPipelineStats,
};
use tensor_grep_rs::index::TrigramIndex;
use tensor_grep_rs::native_search::{
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
    gpu_proof_fields, route_search, BackendSelection, IndexRoutingState, RoutingDecision,
    SearchRoutingCalibration, SearchRoutingConfig,
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
pub struct CalibrateArgs {}

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

    /// The log file to classify
    pub file_path: String,
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
            "version": 1,
            "schema_version": 1,
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

    #[cfg(feature = "cuda")]
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
    fn platform_large_stdout_command() -> (&'static str, Vec<String>) {
        if cfg!(windows) {
            (
                "powershell",
                vec![
                    "-NoProfile".to_string(),
                    "-NonInteractive".to_string(),
                    "-Command".to_string(),
                    "$s = 'A' * 65536; for ($i = 0; $i -lt 40; $i++) { [Console]::Out.Write($s) }"
                        .to_string(),
                ],
            )
        } else {
            (
                "dd",
                vec![
                    "if=/dev/zero".to_string(),
                    "bs=65536".to_string(),
                    "count=40".to_string(),
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
        let (program, args) = platform_large_stdout_command();
        let template = command_template(program, &args);
        // Generous but bounded: if the pipe-fill deadlock footgun (rust-lang#45572) were
        // reintroduced (e.g. a hand-rolled spawn + wait_timeout + wait_with_output instead of
        // process_control's drain-while-timing-out wait), the child would block writing to a
        // full, undrained pipe and this call would hit the timeout and report failure instead of
        // completing quickly -- this is a regression guard, not just a happy-path check.
        let timeout_ms = 15_000;

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
        assert!(
            elapsed < Duration::from_secs(10),
            "expected the large-output command to finish well under the timeout (no deadlock), took {elapsed:?}"
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
    fn orient_is_a_known_python_command_not_a_search_pattern() {
        // `tg orient PATH` must be recognized as a passthrough command so the native front door
        // delegates to the Python `orient` handler instead of treating "orient" as a ripgrep
        // pattern via run_positional_cli().
        assert!(is_known_python_command("orient"));
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
        assert_eq!(run_search_path(&args), "fixture.py");
    }

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
}

fn run_command_cli(cli: CommandCli) -> anyhow::Result<()> {
    match cli.command {
        Commands::Search(args) => handle_ripgrep_search(args),
        Commands::Calibrate(args) => handle_calibrate_command(args),
        Commands::Upgrade => handle_python_passthrough("upgrade", vec![]),
        Commands::AuditVerify(args) => handle_audit_verify_command(args),
        Commands::Audit { args } => handle_python_passthrough("audit", args),
        Commands::Mcp => handle_python_passthrough("mcp", vec![]),
        Commands::Classify(args) => {
            if !Path::new(&args.file_path).exists() {
                anyhow::bail!(
                    "classify expects a file path; --text/stdin literal classification is not supported yet. Received: {}",
                    args.file_path
                );
            }
            handle_sidecar_command(
                "classify",
                vec![
                    "--format".to_string(),
                    args.format,
                    "--max-lines".to_string(),
                    args.max_lines.to_string(),
                    args.file_path,
                ],
            )
        }
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
        Commands::BlastRadius { args } => handle_python_passthrough("blast-radius", args),
        Commands::BlastRadiusRender { args } => {
            handle_python_passthrough("blast-radius-render", args)
        }
        Commands::BlastRadiusPlan { args } => handle_python_passthrough("blast-radius-plan", args),
        Commands::EditPlan { args } => handle_python_passthrough("edit-plan", args),
        Commands::Agent { args } => handle_python_passthrough("agent", args),
        Commands::ContextRender { args } => handle_python_passthrough("context-render", args),
        Commands::AstInfo { args } => handle_python_passthrough("ast-info", args),
        Commands::Rulesets { args } => handle_python_passthrough("rulesets", args),
        Commands::AuditHistory { args } => handle_python_passthrough("audit-history", args),
        Commands::AuditDiff { args } => handle_python_passthrough("audit-diff", args),
        Commands::ReviewBundle { args } => handle_python_passthrough("review-bundle", args),
        Commands::Evidence { args } => handle_python_passthrough("evidence", args),
        Commands::Devices { args } => handle_python_passthrough("devices", args),
        Commands::Context { args } => handle_python_passthrough("context", args),
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

fn handle_calibrate_command(_args: CalibrateArgs) -> anyhow::Result<()> {
    let executable = env::current_exe().context("failed to resolve current tg executable")?;
    match run_crossover_calibration(&executable) {
        Ok(config) => {
            write_crossover_config(&config, None)?;
            println!("{}", serde_json::to_string_pretty(&config)?);
            Ok(())
        }
        Err(err) => {
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
            line_number: true,
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
                line_number: true,
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

fn is_known_python_command(token: &str) -> bool {
    const RAW_PY: &str = include_str!("../../src/tensor_grep/cli/commands.py");
    RAW_PY.lines().any(|line| {
        let t = line.trim();
        t.starts_with('"')
            && (t.ends_with(r#"","#) || t.ends_with(r#"""#))
            && t.contains(&format!("\"{token}\""))
    })
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

fn detect_warm_index_state(
    args: &SearchArgs,
    request: &ResolvedSearchRequest,
) -> IndexRoutingState {
    if args.index
        || request.paths.len() != 1
        || request.patterns.len() != 1
        || request.patterns[0].len() < 3
        || !index_flag_violations(args, request).is_empty()
    {
        return IndexRoutingState::default();
    }

    let index_path = resolve_index_path(request.primary_path());
    if !index_path.exists() {
        return IndexRoutingState::default();
    }

    match TrigramIndex::load(&index_path) {
        Ok(index) => IndexRoutingState {
            exists: true,
            // H1d (audit): a query's --no-ignore mode that disagrees with the mode this
            // index was built under must be treated as stale so auto-routing does not
            // silently serve gitignored content the query didn't ask for (or silently
            // omit gitignored content a --no-ignore query did ask for).
            is_stale: index.is_stale(args.no_ignore),
            pattern_compatible: true,
        },
        Err(_) => IndexRoutingState {
            exists: true,
            is_stale: true,
            pattern_compatible: true,
        },
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
        || detect_warm_index_state(args, request).exists
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
        json: params.json,
        ndjson: params.ndjson,
        verbose: params.verbose,
        text: params.text,
        line_number: params.line_number,
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

/// Audit #105: `collect_native_multi_pattern_matches`'s two fallible native-search calls (the
/// AhoCorasick fast path and the per-pattern regex loop below) both funnel any `Err` through
/// this helper instead of a bare `?`. Every one of this function's 4 call sites (the single- and
/// multi-`-e` `tg search` routes, and the two GPU-explicit-`--gpu-device-ids` CPU-fallback
/// routes) would otherwise let an implicit-walk-ceiling refusal `Err` propagate all the way to
/// `main()`'s default `Result` termination, which exits 1 -- the "exit-1-vs-exit-2 no-match
/// ambiguity bug" (audit #81 #7) -- instead of the fast-bounded exit-2 refusal every other
/// native-CPU route already gets via `run_native_search_with_optional_rg_fallback`'s generic Err
/// handling. Deliberately mirrors `execute_ripgrep_search`'s OWN refusal (rg_passthrough.rs):
/// always a plain `eprintln!`, never a structured JSON error object, even under `--json` -- so
/// this refusal reads identically regardless of which internal engine produced it. Any OTHER
/// native-search error (bad path, bad pattern, ...) is returned completely unchanged; this must
/// not alter exit-code behavior for pre-existing error kinds.
fn exit_on_native_multi_pattern_ceiling_refusal(err: anyhow::Error) -> anyhow::Error {
    if !is_unbounded_implicit_search_walk_refusal(&err.to_string()) {
        return err;
    }
    eprintln!("{err}");
    std::process::exit(2);
}

fn collect_native_multi_pattern_matches(
    patterns: &[String],
    mut base_config: NativeSearchConfig,
) -> anyhow::Result<Vec<SearchMatchJson>> {
    let include_pattern_metadata = patterns.len() > 1;
    let fast_path_matches = run_native_fixed_multi_pattern_search(base_config.clone(), patterns)
        .map_err(exit_on_native_multi_pattern_ceiling_refusal)?;
    if let Some(matches) = fast_path_matches {
        return Ok(matches
            .into_iter()
            .map(|matched| SearchMatchJson {
                file: matched.path.to_string_lossy().into_owned(),
                line: matched.line_number as usize,
                text: matched.text,
                range: None,
                meta_variables: None,
                pattern_id: include_pattern_metadata.then_some(matched.pattern_id),
                pattern_text: include_pattern_metadata.then_some(matched.pattern_text),
            })
            .collect());
    }

    base_config.json = false;
    base_config.ndjson = false;
    base_config.count = false;
    base_config.output_target = NativeOutputTarget::Buffer(Arc::new(Mutex::new(Vec::new())));

    let mut matches = Vec::new();
    for (pattern_id, pattern) in patterns.iter().enumerate() {
        let mut pattern_config = base_config.clone();
        pattern_config.pattern = pattern.clone();
        let stats = execute_native_search(pattern_config)
            .map_err(exit_on_native_multi_pattern_ceiling_refusal)?;
        matches.extend(stats.matches.into_iter().map(|matched| SearchMatchJson {
            file: matched.path.to_string_lossy().into_owned(),
            line: matched.line_number.unwrap_or(0) as usize,
            text: matched.text,
            range: None,
            meta_variables: None,
            pattern_id: include_pattern_metadata.then_some(pattern_id),
            pattern_text: include_pattern_metadata.then(|| pattern.clone()),
        }));
    }

    Ok(matches)
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
}

fn emit_multi_pattern_native_results(
    options: NativeSearchOutputOptions<'_>,
    matches: Vec<SearchMatchJson>,
) -> anyhow::Result<()> {
    let has_matches = !matches.is_empty();
    if options.json {
        emit_json_search_results(
            options.decision,
            options.query,
            options.path,
            options.requested_gpu_device_ids,
            matches,
        )?;
    } else if options.ndjson {
        emit_ndjson_search_results(
            options.decision,
            options.query,
            options.path,
            options.requested_gpu_device_ids,
            matches,
        )?;
    } else if options.count {
        emit_count_search_matches(options.path, &matches);
    } else {
        emit_plain_search_matches_with_line_number(options.path, &matches, options.line_number);
    }

    if !has_matches {
        std::process::exit(1);
    }

    Ok(())
}

fn run_native_search_with_optional_rg_fallback(
    config: NativeSearchConfig,
    rg_fallback: Option<RipgrepSearchArgs>,
) -> anyhow::Result<()> {
    let json = config.json;
    let ndjson = config.ndjson;
    let verbose = config.verbose;
    match execute_native_search(config) {
        Ok(stats) => {
            if stats.total_matches == 0 && stats.binary_match_files == 0 {
                std::process::exit(1);
            }
            Ok(())
        }
        Err(err) => {
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

    let index_state = detect_warm_index_state(&args, &request);

    #[cfg(feature = "cuda")]
    let gpu_auto_supported = request.paths.len() == 1
        && gpu_native_fallback_reason(&GpuSearchParams {
            patterns: &request.patterns,
            query: &query,
            path: request.primary_path(),
            line_number: false,
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
        },
        calibration.as_ref(),
        index_state,
        gpu_available,
    );

    match decision.selection {
        BackendSelection::TrigramIndex => handle_index_search(&args, &request, &query),
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
                line_number: false,
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
                let matches = match collect_native_multi_pattern_matches(
                    &request.patterns,
                    native_search_config_for_command(
                        &args,
                        &request.patterns[0],
                        &request.paths,
                        request.path_was_implicit,
                        decision,
                    ),
                ) {
                    Ok(matches) => matches,
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
                    },
                    matches,
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

fn handle_index_search(
    args: &SearchArgs,
    request: &ResolvedSearchRequest,
    query: &str,
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

    let index = if index_path.exists() {
        let loaded = match TrigramIndex::load(&index_path) {
            Ok(idx) => idx,
            Err(e) => {
                eprintln!("[index] warning: failed to load index: {e}, rebuilding...");
                let started = Instant::now();
                let fresh = TrigramIndex::build_with_options(
                    Path::new(request.primary_path()),
                    args.no_ignore,
                )?;
                fresh.save(&index_path)?;
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
        if let Some(reason) = loaded.staleness_reason(args.no_ignore) {
            if args.verbose {
                eprintln!("[index] stale: {reason}");
            }
            let started = Instant::now();
            let update = loaded.rebuild_incremental_with_options(
                Path::new(request.primary_path()),
                args.no_ignore,
            )?;
            update.index.save(&index_path)?;
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
        fresh.save(&index_path)?;
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
        matches.extend(results.into_iter().map(|result| SearchMatchJson {
            file: result.file.to_string_lossy().into_owned(),
            line: result.line,
            text: result.text,
            range: None,
            meta_variables: None,
            pattern_id: include_pattern_metadata.then_some(pattern_id),
            pattern_text: include_pattern_metadata.then(|| pattern.clone()),
        }));
    }

    if args.json {
        return emit_json_search_results(
            RoutingDecision::warm_index(),
            query,
            request.primary_path(),
            &[],
            matches,
        );
    }

    if args.ndjson {
        return emit_ndjson_search_results(
            RoutingDecision::warm_index(),
            query,
            request.primary_path(),
            &[],
            matches,
        );
    }

    if args.count {
        let unique_count = unique_line_matches(&matches).len();
        println!("{unique_count}");
        // fold-in (a): rg exit-parity. The native CPU (run_native_search_with_optional_rg_
        // fallback) and multi-pattern (emit_multi_pattern_native_results) engines both already
        // exit(1) on zero matches; run_index_query never did, so `--index --count` on a
        // no-match query printed "0" but exited 0 -- indistinguishable from "found nothing but
        // still succeeded" instead of rg's "no match" signal.
        if unique_count == 0 {
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
    );

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

#[derive(Debug, Clone, Serialize, PartialEq, Eq, PartialOrd, Ord)]
struct SearchMatchJson {
    file: String,
    line: usize,
    text: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    range: Option<SearchRangeJson>,
    #[serde(rename = "metaVariables", skip_serializing_if = "Option::is_none")]
    meta_variables: Option<SearchMetaVariablesJson>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pattern_id: Option<usize>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pattern_text: Option<String>,
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
    text: &'a str,
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

fn run_search_path(args: &RunArgs) -> &str {
    if args.pattern_option.is_some() {
        args.positional.first().map(String::as_str).unwrap_or(".")
    } else {
        args.positional.get(1).map(String::as_str).unwrap_or(".")
    }
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
    Ok(SearchMatchJson {
        file: matched.file.to_string_lossy().into_owned(),
        line: matched.line,
        text: matched.matched_text.clone(),
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
        std::fs::copy(&source, &destination).with_context(|| {
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
    std::fs::write(&metadata_path, serde_json::to_vec_pretty(&metadata)?)
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
    std::fs::write(&index_path, serde_json::to_vec_pretty(&records)?)
        .with_context(|| format!("failed to write {}", index_path.display()))?;

    Ok(summary)
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
        match std::fs::write(file, bytes) {
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

/// Writes `bytes` to `path`, refusing to follow a symlink/reparse point at the final path
/// component (audit #110 Gap 1). Closes a cross-process TOCTOU: the Python front door
/// resolves and confines the `--audit-manifest` target before invoking this native binary,
/// but a symlink swapped into that path between the Python check and this write was
/// previously followed by a plain `std::fs::write` -- a confined write could escape its
/// anchor. Mirrors the confine-then-open discipline `_write_json_refuse_symlink` already
/// uses on the Python side (`src/tensor_grep/cli/main.py`).
fn write_bytes_refuse_symlink(path: &Path, bytes: &[u8]) -> anyhow::Result<()> {
    use std::fs::OpenOptions;

    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        // O_NOFOLLOW makes the open() itself fail (ELOOP) if the final path component is
        // a symlink -- atomic, no separate check->open window.
        let mut file = OpenOptions::new()
            .write(true)
            .create(true)
            .truncate(true)
            .custom_flags(libc::O_NOFOLLOW)
            .open(path)
            .with_context(|| format!("refusing to write through symlink at {}", path.display()))?;
        file.write_all(bytes)?;
        Ok(())
    }

    #[cfg(windows)]
    {
        use std::os::windows::fs::{MetadataExt, OpenOptionsExt};
        // Not in a dependency here (neither `windows` nor `winapi` is in Cargo.toml) -- these
        // are the real documented values (winnt.h / fileapi.h), kept as local consts rather
        // than pulling in a crate for two flag bits.
        const FILE_FLAG_OPEN_REPARSE_POINT: u32 = 0x0020_0000;
        const FILE_ATTRIBUTE_REPARSE_POINT: u32 = 0x0000_0400;

        // FILE_FLAG_OPEN_REPARSE_POINT makes CreateFile open the reparse point entry
        // itself instead of traversing it -- so if `path` is a symlink, we open the link,
        // not its target. Deliberately no truncate-at-open: on a real reparse point that
        // would touch the reparse buffer before we get to check it below.
        let mut file = OpenOptions::new()
            .write(true)
            .create(true)
            .custom_flags(FILE_FLAG_OPEN_REPARSE_POINT)
            .open(path)
            .with_context(|| format!("failed to open {}", path.display()))?;
        let attributes = file.metadata()?.file_attributes();
        if attributes & FILE_ATTRIBUTE_REPARSE_POINT != 0 {
            anyhow::bail!(
                "refusing to write through symlink/reparse point at {}",
                path.display()
            );
        }
        // Confirmed a regular file (or a freshly created one) -- now safe to truncate and
        // write, preserving create-or-overwrite semantics for a legitimate rerun.
        file.set_len(0)?;
        file.write_all(bytes)?;
        Ok(())
    }

    #[cfg(not(any(unix, windows)))]
    {
        if std::fs::symlink_metadata(path)
            .map(|metadata| metadata.file_type().is_symlink())
            .unwrap_or(false)
        {
            anyhow::bail!("refusing to write through symlink at {}", path.display());
        }
        std::fs::write(path, bytes)?;
        Ok(())
    }
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

    let path = run_search_path(&args);

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

fn handle_gpu_unavailable_cpu_fallback(
    params: GpuSearchParams<'_>,
    warning: &str,
) -> anyhow::Result<()> {
    eprintln!(
        "warning: {warning}; falling back to native CPU search; this CPU fallback output is not GPU acceleration proof"
    );
    let decision = RoutingDecision::native_cpu_gpu_fallback(
        ripgrep_is_available(),
        params.json || params.ndjson,
    );
    let pattern = params.patterns.first().map_or(params.query, String::as_str);
    let cpu_config = native_search_config_for_gpu_params(&params, pattern, decision);
    if cpu_config.verbose {
        emit_verbose_metadata(decision);
    }
    if params.patterns.len() > 1 {
        let matches = collect_native_multi_pattern_matches(params.patterns, cpu_config)?;
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
            },
            matches,
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

#[cfg(feature = "cuda")]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum GpuRouteFailureKind {
    Unavailable,
    Fatal,
}

#[cfg(feature = "cuda")]
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

#[cfg(feature = "cuda")]
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

#[cfg(feature = "cuda")]
fn classify_gpu_route_failure(raw_message: &str) -> GpuRouteFailure {
    if raw_message.starts_with("CUDA is unavailable:") {
        return GpuRouteFailure {
            kind: GpuRouteFailureKind::Unavailable,
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
        )?;
    } else if params.count {
        emit_gpu_native_count_results(params, &stats);
    } else {
        emit_gpu_native_plain_results(params, &stats);
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
                        let matches = collect_native_multi_pattern_matches(
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
                            },
                            matches,
                        );
                    }
                    run_native_search_with_optional_rg_fallback(cpu_fallback_config, rg_fallback)
                }
                GpuRouteFailureKind::Fatal => {
                    exit_structured_search_error_if_needed(
                        params.json,
                        params.ndjson,
                        "gpu_fatal",
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
                        let matches =
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
                            },
                            matches,
                        );
                    }
                    run_native_search_with_optional_rg_fallback(cpu_config, rg_fallback)
                }
                GpuRouteFailureKind::Fatal => {
                    exit_structured_search_error_if_needed(
                        params.json,
                        params.ndjson,
                        "gpu_fatal",
                        failure.message,
                    );
                }
            }
        }
    }
}

fn handle_gpu_sidecar_search(params: GpuSearchParams) -> anyhow::Result<()> {
    if params.verbose {
        emit_verbose_metadata(RoutingDecision::gpu_sidecar());
    }

    let payload = serde_json::json!({
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
    });

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
                    let matches = parse_gpu_sidecar_search_payload(&result.stdout)?
                        .matches
                        .into_iter()
                        .map(|entry| SearchMatchJson {
                            file: entry.file,
                            line: entry.line_number,
                            text: entry.text,
                            range: None,
                            meta_variables: None,
                            pattern_id: entry.pattern_id,
                            pattern_text: entry.pattern_text,
                        })
                        .collect();
                    emit_ndjson_search_results(
                        RoutingDecision::gpu_sidecar(),
                        params.query,
                        params.path,
                        params.gpu_device_ids,
                        matches,
                    )?;
                } else if params.json {
                    let normalized =
                        normalize_gpu_sidecar_json(&result.stdout, params.gpu_device_ids)?;
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

fn handle_sidecar_command(command: &str, args: Vec<String>) -> anyhow::Result<()> {
    match execute_sidecar_command(command, args, None) {
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

fn emit_json_search_results(
    decision: RoutingDecision,
    pattern: &str,
    path: &str,
    requested_gpu_device_ids: &[i32],
    matches: Vec<SearchMatchJson>,
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
    };

    println!("{}", serde_json::to_string(&payload)?);
    Ok(())
}

fn unique_line_matches(matches: &[SearchMatchJson]) -> Vec<SearchMatchJson> {
    let mut seen = std::collections::BTreeSet::new();
    let mut unique = Vec::new();
    for matched in matches {
        let key = (matched.file.clone(), matched.line, matched.text.clone());
        if seen.insert(key) {
            let mut deduped = matched.clone();
            deduped.pattern_id = None;
            deduped.pattern_text = None;
            unique.push(deduped);
        }
    }
    unique
}

// Audit fix #1 (2026-07-11): `run_index_query`'s plain-output arm used to be this function's
// only non-cuda caller, hardcoding `line_number: true` regardless of `-N`/`--no-line-number`
// (fold-in b). It now calls `emit_plain_search_matches_with_line_number` directly with the same
// `line_number && !no_line_number` expression the native/rg-passthrough configs already use, so
// this wrapper's only remaining caller is the cuda-only `emit_gpu_native_plain_results` below.
#[cfg(feature = "cuda")]
fn emit_plain_search_matches(path: &str, matches: &[SearchMatchJson]) {
    emit_plain_search_matches_with_line_number(path, matches, true);
}

fn emit_plain_search_matches_with_line_number(
    path: &str,
    matches: &[SearchMatchJson],
    line_number: bool,
) {
    let unique = unique_line_matches(matches);
    let with_filename = unique
        .iter()
        .map(|matched| matched.file.as_str())
        .collect::<std::collections::BTreeSet<_>>()
        .len()
        > 1
        || Path::new(path).is_dir();
    for matched in unique {
        match (with_filename, line_number) {
            (true, true) => println!("{}:{}:{}", matched.file, matched.line, matched.text),
            (true, false) => println!("{}:{}", matched.file, matched.text),
            (false, true) => println!("{}:{}", matched.line, matched.text),
            (false, false) => println!("{}", matched.text),
        }
    }
}

fn emit_count_search_matches(path: &str, matches: &[SearchMatchJson]) {
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

    if with_filename {
        for (file, count) in counts {
            println!("{file}:{count}");
        }
    } else {
        println!("{}", counts.values().copied().next().unwrap_or(0));
    }
}

#[cfg(feature = "cuda")]
fn gpu_native_match_json_entries(stats: &GpuNativeSearchStats) -> Vec<SearchMatchJson> {
    stats
        .matches
        .iter()
        .map(|matched| SearchMatchJson {
            file: matched.path.to_string_lossy().into_owned(),
            line: matched.line_number,
            text: matched.text.clone(),
            range: None,
            meta_variables: None,
            pattern_id: (stats.pattern_count > 1).then_some(matched.pattern_id),
            pattern_text: (stats.pattern_count > 1).then(|| matched.pattern_text.clone()),
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
        pipeline: &stats.pipeline,
        matches: gpu_native_match_json_entries(stats),
    };

    println!("{}", serde_json::to_string_pretty(&payload)?);
    Ok(())
}

#[cfg(feature = "cuda")]
fn emit_gpu_native_plain_results(params: &GpuSearchParams<'_>, stats: &GpuNativeSearchStats) {
    let matches = gpu_native_match_json_entries(stats);
    emit_plain_search_matches(params.path, &matches);
}

#[cfg(feature = "cuda")]
fn emit_gpu_native_count_results(params: &GpuSearchParams<'_>, stats: &GpuNativeSearchStats) {
    let matches = gpu_native_match_json_entries(stats);
    emit_count_search_matches(params.path, &matches);
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

    let device_ids = stats
        .selected_devices
        .iter()
        .map(|device| device.device_id.to_string())
        .collect::<Vec<_>>()
        .join(",");
    let device_names = stats
        .selected_devices
        .iter()
        .map(|device| device.name.as_str())
        .collect::<Vec<_>>()
        .join(" | ");

    eprintln!(
        "[gpu-native] selected_gpu_device_ids={} selected_gpu_device_names={} gpu_batch_files={} gpu_transfer_bytes={} gpu_streams={} gpu_double_buffered={} pinned_host_buffers={} gpu_batch_count={} gpu_overlap_batches={} gpu_pattern_count={} gpu_pattern_batches={} gpu_single_dispatch={} gpu_transfer_time_ms={:.3} gpu_kernel_time_ms={:.3} gpu_host_file_read_time_ms={:.3} gpu_host_preprocess_time_ms={:.3} gpu_host_to_pinned_copy_time_ms={:.3} gpu_cpu_staging_bytes={} gpu_pageable_host_staging_bytes={} gpu_transfer_throughput_gbps={:.2}",
        device_ids,
        device_names,
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
) -> anyhow::Result<()> {
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
            text: &matched.text,
            pattern_id: matched.pattern_id,
            pattern_text: matched.pattern_text.as_deref(),
        };
        println!("{}", serde_json::to_string(&payload)?);
    }

    Ok(())
}

fn parse_gpu_sidecar_search_payload(stdout: &str) -> anyhow::Result<GpuSidecarSearchPayload> {
    serde_json::from_str(stdout).map_err(|err| {
        anyhow::anyhow!(
            "GPU sidecar returned malformed search JSON payload: expected {{total_matches, total_files, matches[]}} with string file/text fields and integer line_number values ({err})"
        )
    })
}

fn normalize_gpu_sidecar_json(
    stdout: &str,
    requested_gpu_device_ids: &[i32],
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

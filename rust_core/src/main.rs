use anyhow::Context;
#[cfg(feature = "cuda")]
use clap::ValueEnum;
use clap::{Args, Parser, Subcommand};
use hmac::{Hmac, Mac};
#[cfg(feature = "cuda")]
use ignore::{overrides::OverrideBuilder, WalkBuilder};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;
use std::env;
use std::ffi::OsString;
#[cfg(feature = "cuda")]
use std::fs;
use std::io::{self, Write};
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::{Arc, Mutex};
use std::time::Instant;
use std::time::{SystemTime, UNIX_EPOCH};
use tensor_grep_rs::backend_ast::{
    AstBackend, AstMatch, AstMetaVariables, BatchRewritePlan, BatchRewriteRule,
};
use tensor_grep_rs::crossover::{run_crossover_calibration, write_crossover_config};
#[cfg(feature = "cuda")]
use tensor_grep_rs::gpu_native::{
    benchmark_cuda_graph_search_paths, benchmark_pageable_transfer_throughput,
    benchmark_pinned_transfer_throughput, enumerate_cuda_devices, gpu_native_search_paths_multi,
    probe_device_allocation, GpuNativeSearchConfig, GpuNativeSearchStats,
};
use tensor_grep_rs::index::TrigramIndex;
use tensor_grep_rs::native_search::{
    run_native_search, NativeOutputTarget, NativeSearchConfig, SearchStats,
};
use tensor_grep_rs::python_sidecar::{
    execute_python_passthrough_command, execute_sidecar_command, SidecarError,
};
use tensor_grep_rs::rg_passthrough::{
    execute_ripgrep_search, ripgrep_is_available, RipgrepSearchArgs,
};
use tensor_grep_rs::routing::{
    route_search, BackendSelection, IndexRoutingState, RoutingDecision, SearchRoutingCalibration,
    SearchRoutingConfig,
};

const ENVIRONMENT_OVERRIDES_HELP: &str = "Environment overrides:\n  TG_SIDECAR_PYTHON  Path to the Python executable used for sidecar-backed commands.\n  TG_RG_PATH         Path to the ripgrep executable used for text-search passthrough.";
const JSON_OUTPUT_VERSION: u32 = 1;
const TG_RUST_EARLY_RG_ENV: &str = "TG_RUST_EARLY_RG";
const TG_RUST_EARLY_POSITIONAL_RG_ENV: &str = "TG_RUST_EARLY_POSITIONAL_RG";

#[derive(Parser, Debug)]
#[command(name = "tg")]
#[command(version)]
#[command(about = "tensor-grep: native search, rewrite, and repository analysis CLI")]
#[command(after_help = ENVIRONMENT_OVERRIDES_HELP)]
pub struct CommandCli {
    #[command(subcommand)]
    pub command: Commands,
}

#[derive(Parser, Debug)]
#[command(name = "tg")]
#[command(version)]
#[command(about = "tensor-grep: native search, rewrite, and repository analysis CLI")]
#[command(after_help = ENVIRONMENT_OVERRIDES_HELP)]
pub struct PositionalCli {
    /// The search pattern (regex or string)
    pub pattern: Option<String>,

    /// Path to search
    pub path: Option<String>,

    /// Count matching lines
    #[arg(short = 'c', long)]
    pub count: bool,

    /// Show line numbers
    #[arg(short = 'n', long)]
    pub line_number: bool,

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

    /// Print only the matched parts of a line
    #[arg(short = 'o', long)]
    pub only_matching: bool,

    /// Emit machine-readable routing metadata as JSON
    #[arg(long, conflicts_with = "ndjson")]
    pub json: bool,

    /// Emit one JSON object per matching line (newline-delimited)
    #[arg(long, conflicts_with = "json")]
    pub ndjson: bool,

    /// Emit routing metadata on stderr before executing the search
    #[arg(long)]
    pub verbose: bool,
}

#[derive(Args, Debug, Clone)]
pub struct SearchArgs {
    /// Case insensitive search
    #[arg(short = 'i', long)]
    pub ignore_case: bool,

    /// Fixed string matching (disable regex)
    #[arg(short = 'F', long)]
    pub fixed_strings: bool,

    /// Invert match (select non-matching lines)
    #[arg(short = 'v', long)]
    pub invert_match: bool,

    /// Count matching lines
    #[arg(short = 'c', long)]
    pub count: bool,

    /// Show line numbers
    #[arg(short = 'n', long)]
    pub line_number: bool,

    /// Replace matches in emitted output (ripgrep-style)
    #[arg(short = 'r', long)]
    pub replace: Option<String>,

    /// Show NUM context lines before and after each match
    #[arg(short = 'C', long)]
    pub context: Option<usize>,

    /// Stop after NUM matching lines per file
    #[arg(short = 'm', long)]
    pub max_count: Option<usize>,

    /// Show matches with word boundaries
    #[arg(short = 'w', long)]
    pub word_regexp: bool,

    /// Include/exclude files matching glob
    #[arg(short = 'g', long = "glob")]
    pub globs: Vec<String>,

    /// Ignore .gitignore / ignore files
    #[arg(long = "no-ignore")]
    pub no_ignore: bool,

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

    /// Print only the matched parts of a line
    #[arg(short = 'o', long)]
    pub only_matching: bool,

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
    #[arg(short = 'e', long = "regexp")]
    pub regexp: Vec<String>,

    /// The search pattern (regex or string)
    #[arg(required_unless_present = "regexp")]
    pub pattern: Option<String>,

    /// Path to search
    pub path: Option<String>,
}

#[derive(Args, Debug, Clone)]
pub struct RunArgs {
    /// The AST language to use
    #[arg(long, default_value = "python")]
    pub lang: String,

    /// Rewrite matched nodes with this replacement pattern (metavar substitution supported)
    #[arg(long, conflicts_with = "batch_rewrite")]
    pub rewrite: Option<String>,

    /// Apply multiple rewrite rules from a JSON config file
    #[arg(long = "batch-rewrite", conflicts_with = "rewrite")]
    pub batch_rewrite: Option<PathBuf>,

    /// Apply rewrite edits to files (requires --rewrite)
    #[arg(long)]
    pub apply: bool,

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

    /// The structural ast-grep pattern
    pub pattern: Option<String>,

    /// File or directory to search
    pub path: Option<String>,
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

#[derive(Subcommand, Debug)]
pub enum Commands {
    /// Search for a regex pattern with ripgrep-compatible flags
    Search(SearchArgs),
    /// Measure CPU vs GPU crossover thresholds and persist smart-routing calibration
    Calibrate(CalibrateArgs),
    /// Upgrade tensor-grep via the managed Python package path
    #[command(alias = "update")]
    Upgrade,
    /// Verify a rewrite audit manifest digest, chain, and optional signature
    #[command(name = "audit-verify")]
    AuditVerify(AuditVerifyArgs),
    /// Start the AI-assistant Model Context Protocol (MCP) server
    Mcp,
    /// Run semantic NLP threat classification on logs via cyBERT
    Classify { file_path: String },
    /// Run GPU-accelerated AST structural queries (ast-grep parity)
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
    Lsp,
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
    #[command(name = "map", disable_help_flag = true)]
    Map {
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
        args: Vec<String>,
    },
    #[command(name = "session", disable_help_flag = true)]
    Session {
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
        args: Vec<String>,
    },
    #[command(name = "doctor", disable_help_flag = true)]
    Doctor {
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
        args: Vec<String>,
    },
    #[command(name = "checkpoint", disable_help_flag = true)]
    Checkpoint {
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
        args: Vec<String>,
    },
    #[command(name = "source", disable_help_flag = true)]
    Source {
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
        args: Vec<String>,
    },
    #[command(name = "impact", disable_help_flag = true)]
    Impact {
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
        args: Vec<String>,
    },
    #[command(name = "callers", disable_help_flag = true)]
    Callers {
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
        args: Vec<String>,
    },
    #[command(name = "blast-radius", disable_help_flag = true)]
    BlastRadius {
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
        args: Vec<String>,
    },
    #[command(name = "blast-radius-render", disable_help_flag = true)]
    BlastRadiusRender {
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
        args: Vec<String>,
    },
    #[command(name = "blast-radius-plan", disable_help_flag = true)]
    BlastRadiusPlan {
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
        args: Vec<String>,
    },
    #[command(name = "edit-plan", disable_help_flag = true)]
    EditPlan {
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
        args: Vec<String>,
    },
    #[command(name = "context-render", disable_help_flag = true)]
    ContextRender {
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
        args: Vec<String>,
    },
    #[command(name = "rulesets", disable_help_flag = true)]
    Rulesets {
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
        args: Vec<String>,
    },
    #[command(name = "audit-history", disable_help_flag = true)]
    AuditHistory {
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
        args: Vec<String>,
    },
    #[command(name = "audit-diff", disable_help_flag = true)]
    AuditDiff {
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
        args: Vec<String>,
    },
    #[command(name = "review-bundle", disable_help_flag = true)]
    ReviewBundle {
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
        args: Vec<String>,
    },
    #[command(name = "devices", disable_help_flag = true)]
    Devices {
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
        args: Vec<String>,
    },
    #[command(name = "defs", disable_help_flag = true)]
    Defs {
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
        args: Vec<String>,
    },
    #[command(name = "refs", disable_help_flag = true)]
    Refs {
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
        args: Vec<String>,
    },
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
    path: String,
}

impl ResolvedSearchRequest {
    fn query_display(&self) -> String {
        if self.patterns.len() == 1 {
            self.patterns[0].clone()
        } else {
            self.patterns.join(" | ")
        }
    }
}

fn main() -> anyhow::Result<()> {
    let raw_args: Vec<OsString> = std::env::args_os().collect();

    if raw_args.len() <= 1 {
        use clap::CommandFactory;

        let mut cmd = CommandCli::command();
        cmd.print_help()?;
        return Ok(());
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

fn should_use_early_ripgrep_fast_path(args: &RipgrepSearchArgs) -> bool {
    args.globs.is_empty() && !args.word_regexp && !args.fixed_strings
}

fn parse_early_ripgrep_args(raw_args: &[OsString]) -> Option<RipgrepSearchArgs> {
    let mut args = RipgrepSearchArgs {
        ignore_case: false,
        fixed_strings: false,
        invert_match: false,
        count: false,
        line_number: false,
        only_matching: false,
        context: None,
        max_count: None,
        word_regexp: false,
        globs: Vec::new(),
        no_ignore: false,
        color: None,
        replace: None,
        patterns: Vec::new(),
        path: String::new(),
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
            "-v" | "--invert-match" => args.invert_match = true,
            "-c" | "--count" => args.count = true,
            "-n" | "--line-number" => args.line_number = true,
            "-o" | "--only-matching" => args.only_matching = true,
            "-w" | "--word-regexp" => args.word_regexp = true,
            "--no-ignore" => args.no_ignore = true,
            "-C" | "--context" => {
                index += 1;
                let value = tokens.get(index)?.parse::<usize>().ok()?;
                args.context = Some(value);
            }
            "-m" | "--max-count" => {
                index += 1;
                let value = tokens.get(index)?.parse::<usize>().ok()?;
                args.max_count = Some(value);
            }
            "--color" => {
                index += 1;
                args.color = Some(tokens.get(index)?.clone());
            }
            "-g" | "--glob" => {
                index += 1;
                args.globs.push(tokens.get(index)?.clone());
            }
            _ if token.starts_with("--glob=") => {
                args.globs
                    .push(token.split_once('=').map(|(_, value)| value.to_string())?);
            }
            _ if token.starts_with('-') => return None,
            _ => positionals.push(token.clone()),
        }
        index += 1;
    }

    if positionals.len() != 2 {
        return None;
    }
    args.patterns.push(positionals[0].clone());
    args.path = positionals[1].clone();
    Some(args)
}

fn parse_default_search_frontdoor_args(raw_args: &[OsString]) -> Option<RipgrepSearchArgs> {
    if raw_args.get(1).and_then(|arg| arg.to_str()) != Some("search") {
        return None;
    }
    let args = parse_early_ripgrep_args(raw_args)?;
    should_use_early_ripgrep_fast_path(&args).then_some(args)
}

fn parse_early_positional_ripgrep_args(raw_args: &[OsString]) -> Option<RipgrepSearchArgs> {
    let cli = PositionalCli::try_parse_from(raw_args).ok()?;
    let pattern = cli.pattern.clone()?;
    let path = cli.path.clone()?;

    if cli.replace.is_some() || cli.force_cpu || !cli.gpu_device_ids.is_empty() {
        return None;
    }
    if cli.json || cli.ndjson || cli.verbose {
        return None;
    }

    Some(positional_ripgrep_args(&cli, &pattern, &path))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn parse_args(tokens: &[&str]) -> RipgrepSearchArgs {
        let raw_args = tokens
            .iter()
            .map(|token| OsString::from(token))
            .collect::<Vec<_>>();
        parse_early_ripgrep_args(&raw_args).expect("expected early rg args to parse")
    }

    #[test]
    fn early_ripgrep_fast_path_rejects_glob_fixed_and_word_cases() {
        let glob = parse_args(&["tg", "search", "--glob=*.log", "ERROR", "bench_data"]);
        let fixed = parse_args(&["tg", "search", "-F", "[ERROR]", "bench_data"]);
        let word = parse_args(&["tg", "search", "-w", "timeout", "bench_data"]);

        assert!(!should_use_early_ripgrep_fast_path(&glob));
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
    fn early_positional_ripgrep_args_parse_plain_shapes() {
        let raw_args = ["tg", "-i", "warning", "bench_data"]
            .into_iter()
            .map(OsString::from)
            .collect::<Vec<_>>();

        let parsed = parse_early_positional_ripgrep_args(&raw_args)
            .expect("expected early positional rg args to parse");

        assert!(parsed.ignore_case);
        assert_eq!(parsed.patterns, vec!["warning".to_string()]);
        assert_eq!(parsed.path, "bench_data".to_string());
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
        assert_eq!(parsed.path, "bench_data".to_string());
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
        assert_eq!(parsed.path, "bench_data".to_string());
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
        assert_eq!(parsed_ignore_case.path, "bench_data".to_string());

        let parsed_max_count = parse_default_search_frontdoor_args(&max_count)
            .expect("expected default search frontdoor max-count args to parse");
        assert_eq!(parsed_max_count.max_count, Some(5));
        assert_eq!(parsed_max_count.patterns, vec!["ERROR".to_string()]);
        assert_eq!(parsed_max_count.path, "bench_data".to_string());
    }

    #[test]
    fn default_search_frontdoor_rejects_structured_and_advanced_shapes() {
        let structured = ["tg", "search", "--json", "ERROR", "bench_data"]
            .into_iter()
            .map(OsString::from)
            .collect::<Vec<_>>();
        let glob = ["tg", "search", "--glob=*.log", "ERROR", "bench_data"]
            .into_iter()
            .map(OsString::from)
            .collect::<Vec<_>>();

        assert!(parse_default_search_frontdoor_args(&structured).is_none());
        assert!(parse_default_search_frontdoor_args(&glob).is_none());
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
}

fn run_command_cli(cli: CommandCli) -> anyhow::Result<()> {
    match cli.command {
        Commands::Search(args) => handle_ripgrep_search(args),
        Commands::Calibrate(args) => handle_calibrate_command(args),
        Commands::Upgrade => handle_python_passthrough("upgrade", vec![]),
        Commands::AuditVerify(args) => handle_audit_verify_command(args),
        Commands::Mcp => handle_python_passthrough("mcp", vec![]),
        Commands::Classify { file_path } => handle_sidecar_command("classify", vec![file_path]),
        Commands::Run(args) => handle_ast_run(args),
        Commands::Scan { args } => {
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
        Commands::Lsp => handle_python_passthrough("lsp", vec![]),
        #[cfg(feature = "cuda")]
        Commands::GpuNativeStats(args) => handle_gpu_native_stats_command(args),
        #[cfg(feature = "cuda")]
        Commands::GpuTransferBench(args) => handle_gpu_transfer_benchmark_command(args),
        #[cfg(feature = "cuda")]
        Commands::GpuCudaGraphs(args) => handle_gpu_cuda_graph_benchmark_command(args),
        #[cfg(feature = "cuda")]
        Commands::GpuOomProbe(args) => handle_gpu_oom_probe_command(args),
        Commands::Map { args } => handle_python_passthrough("map", args),
        Commands::Session { args } => handle_python_passthrough("session", args),
        Commands::Doctor { args } => handle_python_passthrough("doctor", args),
        Commands::Checkpoint { args } => handle_python_passthrough("checkpoint", args),
        Commands::Defs { args } => handle_python_passthrough("defs", args),
        Commands::Refs { args } => handle_python_passthrough("refs", args),
        Commands::Source { args } => handle_python_passthrough("source", args),
        Commands::Impact { args } => handle_python_passthrough("impact", args),
        Commands::Callers { args } => handle_python_passthrough("callers", args),
        Commands::BlastRadius { args } => handle_python_passthrough("blast-radius", args),
        Commands::BlastRadiusRender { args } => {
            handle_python_passthrough("blast-radius-render", args)
        }
        Commands::BlastRadiusPlan { args } => handle_python_passthrough("blast-radius-plan", args),
        Commands::EditPlan { args } => handle_python_passthrough("edit-plan", args),
        Commands::ContextRender { args } => handle_python_passthrough("context-render", args),
        Commands::Rulesets { args } => handle_python_passthrough("rulesets", args),
        Commands::AuditHistory { args } => handle_python_passthrough("audit-history", args),
        Commands::AuditDiff { args } => handle_python_passthrough("audit-diff", args),
        Commands::ReviewBundle { args } => handle_python_passthrough("review-bundle", args),
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
    if cli.pattern.is_none() || cli.path.is_none() {
        use clap::CommandFactory;
        let mut cmd = PositionalCli::command();
        cmd.print_help()?;
        return Ok(());
    }

    let pattern = cli.pattern.clone().unwrap();
    let path = cli.path.clone().unwrap();

    let rg_available = ripgrep_is_available();
    #[cfg_attr(not(feature = "cuda"), allow(unused_variables))]
    let structured_output = cli.json || cli.ndjson;
    let explicit_gpu = !cli.gpu_device_ids.is_empty();
    let auto_gpu_ids: [i32; 0] = [];

    #[cfg(feature = "cuda")]
    let corpus_bytes = count_search_corpus_bytes(&[PathBuf::from(&path)], true, &[]).unwrap_or(0);
    #[cfg(not(feature = "cuda"))]
    let corpus_bytes = 0u64;

    #[cfg(feature = "cuda")]
    let gpu_auto_supported = gpu_native_fallback_reason(&GpuSearchParams {
        patterns: std::slice::from_ref(&pattern),
        query: &pattern,
        path: &path,
        line_number: true,
        ignore_case: cli.ignore_case,
        fixed_strings: cli.fixed_strings,
        invert_match: cli.invert_match,
        count: cli.count,
        context: None,
        max_count: cli.max_count,
        word_regexp: false,
        globs: Vec::new(),
        no_ignore: true,
        gpu_device_ids: &auto_gpu_ids,
        json: cli.json,
        ndjson: cli.ndjson,
        verbose: cli.verbose,
    })
    .is_none();

    #[cfg(not(feature = "cuda"))]
    let gpu_auto_supported = false;

    #[cfg(feature = "cuda")]
    let calibration = load_search_routing_calibration(Path::new(&path));
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
            gpu_auto_supported,
            prefer_rg_passthrough: false,
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
                path: &path,
                line_number: true,
                ignore_case: cli.ignore_case,
                fixed_strings: cli.fixed_strings,
                invert_match: cli.invert_match,
                count: cli.count,
                context: None,
                max_count: cli.max_count,
                word_regexp: false,
                globs: Vec::new(),
                no_ignore: true,
                gpu_device_ids,
                json: cli.json,
                ndjson: cli.ndjson,
                verbose: cli.verbose,
            };

            #[cfg(feature = "cuda")]
            if decision.reason == RoutingDecision::native_gpu_auto().reason {
                let fallback_decision =
                    RoutingDecision::native_cpu_gpu_fallback(rg_available, structured_output);
                let rg_fallback = fallback_decision
                    .allow_rg_fallback
                    .then(|| positional_ripgrep_args(&cli, &pattern, &path));
                return handle_auto_gpu_search(
                    params,
                    native_search_config_for_positional(&cli, &pattern, &path, fallback_decision),
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
                    "warning: CUDA is unavailable: no usable GPU devices were found; falling back to native CPU search"
                );
            }
            if cli.verbose {
                emit_verbose_metadata(decision);
            }

            let rg_fallback = decision
                .allow_rg_fallback
                .then(|| positional_ripgrep_args(&cli, &pattern, &path));

            run_native_search_with_optional_rg_fallback(
                native_search_config_for_positional(&cli, &pattern, &path, decision),
                rg_fallback,
            )
        }
        BackendSelection::Ripgrep => {
            if cli.verbose {
                emit_verbose_metadata(decision);
            }

            let exit_code =
                execute_ripgrep_search(&positional_ripgrep_args(&cli, &pattern, &path))?;
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
        const RAW_PY: &str = include_str!("../../src/tensor_grep/cli/commands.py");
        let is_known = RAW_PY.lines().any(|line| {
            let t = line.trim();
            t.starts_with('"')
                && (t.ends_with(r#"","#) || t.ends_with(r#"""#))
                && t.contains(&format!("\"{token}\""))
        });
        return !is_known;
    }

    false
}

fn resolve_search_request(args: &SearchArgs) -> anyhow::Result<ResolvedSearchRequest> {
    let mut patterns = args.regexp.clone();
    let path = if args.regexp.is_empty() {
        if let Some(pattern) = args.pattern.as_ref() {
            patterns.push(pattern.clone());
        }
        args.path.clone().unwrap_or_else(|| ".".to_string())
    } else {
        match (&args.pattern, &args.path) {
            (Some(first), Some(path)) => {
                patterns.push(first.clone());
                path.clone()
            }
            (Some(path), None) => path.clone(),
            (None, Some(path)) => path.clone(),
            (None, None) => ".".to_string(),
        }
    };

    if patterns.is_empty() {
        anyhow::bail!("search requires a pattern or at least one -e/--regexp pattern");
    }

    Ok(ResolvedSearchRequest { patterns, path })
}

fn detect_warm_index_state(
    args: &SearchArgs,
    request: &ResolvedSearchRequest,
) -> IndexRoutingState {
    if args.index
        || args.invert_match
        || args.context.is_some()
        || args.max_count.is_some()
        || args.word_regexp
        || !args.globs.is_empty()
        || request.patterns.len() != 1
        || request.patterns[0].len() < 3
    {
        return IndexRoutingState::default();
    }

    let index_path = resolve_index_path(&request.path);
    if !index_path.exists() {
        return IndexRoutingState::default();
    }

    match TrigramIndex::load(&index_path) {
        Ok(index) => IndexRoutingState {
            exists: true,
            is_stale: index.is_stale(),
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

fn positional_ripgrep_args(cli: &PositionalCli, pattern: &str, path: &str) -> RipgrepSearchArgs {
    RipgrepSearchArgs {
        ignore_case: cli.ignore_case,
        fixed_strings: cli.fixed_strings,
        invert_match: cli.invert_match,
        count: cli.count,
        line_number: cli.line_number,
        only_matching: cli.only_matching,
        context: None,
        max_count: cli.max_count,
        word_regexp: false,
        globs: Vec::new(),
        no_ignore: true,
        color: cli.color.clone(),
        replace: cli.replace.clone(),
        patterns: vec![pattern.to_string()],
        path: path.to_string(),
    }
}

fn command_ripgrep_args(args: &SearchArgs, request: &ResolvedSearchRequest) -> RipgrepSearchArgs {
    RipgrepSearchArgs {
        ignore_case: args.ignore_case,
        fixed_strings: args.fixed_strings,
        invert_match: args.invert_match,
        count: args.count,
        line_number: args.line_number,
        only_matching: args.only_matching,
        context: args.context,
        max_count: args.max_count,
        word_regexp: args.word_regexp,
        globs: args.globs.clone(),
        no_ignore: args.no_ignore,
        color: args.color.clone(),
        replace: args.replace.clone(),
        patterns: request.patterns.clone(),
        path: request.path.clone(),
    }
}

fn native_search_config_for_positional(
    cli: &PositionalCli,
    pattern: &str,
    path: &str,
    decision: RoutingDecision,
) -> NativeSearchConfig {
    NativeSearchConfig {
        pattern: pattern.to_string(),
        paths: vec![PathBuf::from(path)],
        routing_backend: decision.routing_backend(),
        routing_reason: decision.reason,
        sidecar_used: decision.sidecar_used(),
        ignore_case: cli.ignore_case,
        fixed_strings: cli.fixed_strings,
        invert_match: cli.invert_match,
        count: cli.count,
        max_count: cli.max_count.map(|value| value as u64),
        no_ignore: true,
        json: cli.json,
        ndjson: cli.ndjson,
        verbose: cli.verbose,
        line_number: cli.line_number,
        only_matching: cli.only_matching,
        replace: cli.replace.clone(),
        ..NativeSearchConfig::default()
    }
}

fn native_search_config_for_command(
    args: &SearchArgs,
    pattern: &str,
    path: &str,
    decision: RoutingDecision,
) -> NativeSearchConfig {
    NativeSearchConfig {
        pattern: pattern.to_string(),
        paths: vec![PathBuf::from(path)],
        routing_backend: decision.routing_backend(),
        routing_reason: decision.reason,
        sidecar_used: decision.sidecar_used(),
        ignore_case: args.ignore_case,
        fixed_strings: args.fixed_strings,
        word_boundary: args.word_regexp,
        invert_match: args.invert_match,
        before_context: args.context.unwrap_or(0),
        after_context: args.context.unwrap_or(0),
        max_count: args.max_count.map(|value| value as u64),
        glob: args.globs.clone(),
        count: args.count,
        no_ignore: args.no_ignore,
        json: args.json,
        ndjson: args.ndjson,
        verbose: args.verbose,
        line_number: false,
        only_matching: args.only_matching,
        replace: args.replace.clone(),
        ..NativeSearchConfig::default()
    }
}

#[cfg(feature = "cuda")]
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
        ignore_case: params.ignore_case,
        fixed_strings: params.fixed_strings,
        word_boundary: params.word_regexp,
        invert_match: params.invert_match,
        before_context: params.context.unwrap_or(0),
        after_context: params.context.unwrap_or(0),
        max_count: params.max_count.map(|value| value as u64),
        glob: params.globs.clone(),
        count: params.count,
        no_ignore: params.no_ignore,
        json: params.json,
        ndjson: params.ndjson,
        verbose: params.verbose,
        line_number: params.line_number,
        ..NativeSearchConfig::default()
    }
}

fn execute_native_search(config: NativeSearchConfig) -> anyhow::Result<SearchStats> {
    if let Ok(message) = env::var("TG_TEST_NATIVE_SEARCH_FORCE_ERROR") {
        anyhow::bail!(message);
    }

    run_native_search(config)
}

fn collect_native_multi_pattern_matches(
    patterns: &[String],
    mut base_config: NativeSearchConfig,
) -> anyhow::Result<Vec<SearchMatchJson>> {
    let include_pattern_metadata = patterns.len() > 1;
    base_config.json = false;
    base_config.ndjson = false;
    base_config.count = false;
    base_config.output_target = NativeOutputTarget::Buffer(Arc::new(Mutex::new(Vec::new())));

    let mut matches = Vec::new();
    for (pattern_id, pattern) in patterns.iter().enumerate() {
        let mut pattern_config = base_config.clone();
        pattern_config.pattern = pattern.clone();
        let stats = execute_native_search(pattern_config)?;
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

fn emit_multi_pattern_native_results(
    decision: RoutingDecision,
    query: &str,
    path: &str,
    json: bool,
    ndjson: bool,
    count: bool,
    matches: Vec<SearchMatchJson>,
) -> anyhow::Result<()> {
    let has_matches = !matches.is_empty();
    if json {
        emit_json_search_results(decision, query, path, matches)?;
    } else if ndjson {
        emit_ndjson_search_results(decision, query, path, matches)?;
    } else if count {
        emit_count_search_matches(path, &matches);
    } else {
        emit_plain_search_matches(path, &matches);
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
    let request = resolve_search_request(&args)?;
    let query = request.query_display();
    let rg_available = ripgrep_is_available();
    #[cfg_attr(not(feature = "cuda"), allow(unused_variables))]
    let structured_output = args.json || args.ndjson;
    let auto_gpu_ids: [i32; 0] = [];

    #[cfg(feature = "cuda")]
    let corpus_bytes =
        count_search_corpus_bytes(&[PathBuf::from(&request.path)], args.no_ignore, &args.globs)
            .unwrap_or(0);
    #[cfg(not(feature = "cuda"))]
    let corpus_bytes = 0u64;

    let index_state = detect_warm_index_state(&args, &request);

    #[cfg(feature = "cuda")]
    let gpu_auto_supported = gpu_native_fallback_reason(&GpuSearchParams {
        patterns: &request.patterns,
        query: &query,
        path: &request.path,
        line_number: false,
        ignore_case: args.ignore_case,
        fixed_strings: args.fixed_strings,
        invert_match: args.invert_match,
        count: args.count,
        context: args.context,
        max_count: args.max_count,
        word_regexp: args.word_regexp,
        globs: args.globs.clone(),
        no_ignore: args.no_ignore,
        gpu_device_ids: &auto_gpu_ids,
        json: args.json,
        ndjson: args.ndjson,
        verbose: args.verbose,
    })
    .is_none();

    #[cfg(not(feature = "cuda"))]
    let gpu_auto_supported = false;

    #[cfg(feature = "cuda")]
    let calibration = load_search_routing_calibration(Path::new(&request.path));
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
            gpu_auto_supported,
            prefer_rg_passthrough: args.context.is_some() && !args.json && !args.ndjson,
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
                path: &request.path,
                line_number: false,
                ignore_case: args.ignore_case,
                fixed_strings: args.fixed_strings,
                invert_match: args.invert_match,
                count: args.count,
                context: args.context,
                max_count: args.max_count,
                word_regexp: args.word_regexp,
                globs: args.globs.clone(),
                no_ignore: args.no_ignore,
                gpu_device_ids,
                json: args.json,
                ndjson: args.ndjson,
                verbose: args.verbose,
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
                        &request.path,
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
                    "warning: CUDA is unavailable: no usable GPU devices were found; falling back to native CPU search"
                );
            }
            if args.verbose {
                emit_verbose_metadata(decision);
            }

            let rg_fallback = decision
                .allow_rg_fallback
                .then(|| command_ripgrep_args(&args, &request));

            if request.patterns.len() > 1 {
                let matches = collect_native_multi_pattern_matches(
                    &request.patterns,
                    native_search_config_for_command(
                        &args,
                        &request.patterns[0],
                        &request.path,
                        decision,
                    ),
                )?;
                return emit_multi_pattern_native_results(
                    decision,
                    &query,
                    &request.path,
                    args.json,
                    args.ndjson,
                    args.count,
                    matches,
                );
            }

            run_native_search_with_optional_rg_fallback(
                native_search_config_for_command(
                    &args,
                    &request.patterns[0],
                    &request.path,
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
    let search_path = Path::new(&request.path);
    if !search_path.exists() {
        anyhow::bail!(
            "index search path does not exist: {}",
            search_path.display()
        );
    }

    let index_path = resolve_index_path(&request.path);

    let index = if index_path.exists() {
        let loaded = match TrigramIndex::load(&index_path) {
            Ok(idx) => idx,
            Err(e) => {
                eprintln!("[index] warning: failed to load index: {e}, rebuilding...");
                let started = Instant::now();
                let fresh =
                    TrigramIndex::build_with_options(Path::new(&request.path), args.no_ignore)?;
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
        if let Some(reason) = loaded.staleness_reason() {
            if args.verbose {
                eprintln!("[index] stale: {reason}");
            }
            let started = Instant::now();
            let update = loaded
                .rebuild_incremental_with_options(Path::new(&request.path), args.no_ignore)?;
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
                request.path
            );
        }
        let started = Instant::now();
        let fresh = TrigramIndex::build_with_options(Path::new(&request.path), args.no_ignore)?;
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
        let results = index.search(pattern, args.ignore_case, args.fixed_strings)?;
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
            &request.path,
            matches,
        );
    }

    if args.ndjson {
        return emit_ndjson_search_results(
            RoutingDecision::warm_index(),
            query,
            &request.path,
            matches,
        );
    }

    if args.count {
        println!("{}", unique_line_matches(&matches).len());
        return Ok(());
    }

    emit_plain_search_matches(&request.path, &matches);

    Ok(())
}

#[derive(Serialize)]
struct SearchResultJson<'a> {
    version: u32,
    routing_backend: &'static str,
    routing_reason: &'static str,
    sidecar_used: bool,
    query: &'a str,
    path: &'a str,
    total_matches: usize,
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
    routing_gpu_device_ids: Vec<i32>,
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
}

#[derive(Debug, Clone, Serialize)]
struct CheckpointCreateSummary {
    checkpoint_id: String,
    mode: String,
    root: String,
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
    created_at: String,
    file_count: usize,
    entries: BTreeMap<String, bool>,
}

#[derive(Debug, Clone, Serialize)]
struct ValidationSummary {
    success: bool,
    commands: Vec<ValidationCommandResult>,
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
    args.path.as_deref().unwrap_or(".")
}

fn run_batch_path(args: &RunArgs) -> anyhow::Result<&str> {
    if args.path.is_some() {
        anyhow::bail!("tg run --batch-rewrite accepts exactly one PATH argument")
    }

    Ok(args.pattern.as_deref().unwrap_or("."))
}

fn run_pattern(args: &RunArgs) -> anyhow::Result<&str> {
    args.pattern.as_deref().ok_or_else(|| {
        anyhow::anyhow!("tg run requires PATTERN unless --batch-rewrite <config.json> is provided")
    })
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
    if (args.lint_cmd.is_some() || args.test_cmd.is_some()) && !args.apply {
        anyhow::bail!("--lint-cmd and --test-cmd require --apply");
    }
    if args.checkpoint && !args.apply {
        anyhow::bail!("--checkpoint requires --apply");
    }
    Ok(())
}

fn checkpoint_timestamp_string() -> String {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_secs().to_string())
        .unwrap_or_else(|_| "0".to_string())
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

fn detect_checkpoint_root(path: &str) -> (PathBuf, String) {
    let candidate = Path::new(path);
    let resolved = candidate
        .canonicalize()
        .unwrap_or_else(|_| candidate.to_path_buf());
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
                (probe_root, "filesystem-snapshot".to_string())
            } else {
                (PathBuf::from(git_root), "git-worktree-snapshot".to_string())
            }
        }
        _ => (probe_root, "filesystem-snapshot".to_string()),
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

fn collect_checkpoint_entries(root: &Path, mode: &str) -> anyhow::Result<BTreeMap<String, bool>> {
    if mode == "git-worktree-snapshot" {
        collect_git_checkpoint_entries(root)
    } else {
        collect_filesystem_checkpoint_entries(root)
    }
}

fn create_checkpoint(path: &str) -> anyhow::Result<CheckpointCreateSummary> {
    let (root, mode) = detect_checkpoint_root(path);
    let created_at = checkpoint_timestamp_string();
    let unique = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_nanos())
        .unwrap_or(0);
    let checkpoint_id = format!("ckpt-{created_at}-{unique:x}");
    let entries = collect_checkpoint_entries(&root, &mode)?;

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
        root: root.to_string_lossy().to_string(),
        created_at: created_at.clone(),
        file_count: entries.len(),
    };
    let metadata = CheckpointMetadata {
        version: JSON_OUTPUT_VERSION,
        checkpoint_id: checkpoint_id.clone(),
        mode: mode.clone(),
        root: summary.root.clone(),
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
        anyhow::anyhow!("invalid batch rewrite config field `$`: expected object")
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

#[cfg(windows)]
fn build_validation_shell_command(command: &str) -> Command {
    let mut process = Command::new("cmd");
    process.args(["/C", command]);
    process
}

#[cfg(not(windows))]
fn build_validation_shell_command(command: &str) -> Command {
    let mut process = Command::new("sh");
    process.args(["-c", command]);
    process
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

fn run_validation_command(
    kind: &'static str,
    command: &str,
    working_dir: &Path,
) -> ValidationCommandResult {
    match build_validation_shell_command(command)
        .current_dir(working_dir)
        .output()
    {
        Ok(output) => ValidationCommandResult {
            kind,
            command: command.to_string(),
            success: output.status.success(),
            exit_code: output.status.code(),
            stdout: String::from_utf8_lossy(&output.stdout).to_string(),
            stderr: String::from_utf8_lossy(&output.stderr).to_string(),
        },
        Err(error) => ValidationCommandResult {
            kind,
            command: command.to_string(),
            success: false,
            exit_code: None,
            stdout: String::new(),
            stderr: format!(
                "failed to spawn validation command in {}: {error}",
                working_dir.display()
            ),
        },
    }
}

fn run_post_apply_validation(args: &RunArgs, path: &str) -> Option<ValidationSummary> {
    let mut commands = Vec::new();
    let working_dir = validation_working_dir(path);

    if let Some(command) = &args.lint_cmd {
        commands.push(run_validation_command("lint", command, &working_dir));
    }
    if let Some(command) = &args.test_cmd {
        commands.push(run_validation_command("test", command, &working_dir));
    }

    if commands.is_empty() {
        return None;
    }

    Some(ValidationSummary {
        success: commands.iter().all(|command| command.success),
        commands,
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
        created_at: checkpoint_timestamp_string(),
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
    std::fs::write(path, serde_json::to_vec_pretty(&manifest)?)
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
        } else {
            let key_path = signing_key_path
                .as_deref()
                .unwrap_or_else(|| Path::new(&signature.key_path));
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

fn handle_ast_run(args: RunArgs) -> anyhow::Result<()> {
    validate_run_args(&args)?;
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
        let mut source_contexts = BTreeMap::new();
        return emit_json_search_results(
            RoutingDecision::ast(),
            pattern,
            path,
            matches
                .iter()
                .map(|matched| ast_match_to_search_json(matched, &mut source_contexts))
                .collect::<anyhow::Result<Vec<_>>>()?,
        );
    }

    let matches = backend.search_for_cli(pattern, &args.lang, path)?;

    if args.verbose {
        emit_verbose_metadata(RoutingDecision::ast());
    }

    let stdout = io::stdout();
    let mut stdout = io::BufWriter::new(stdout.lock());
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

    Ok(())
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
        eprintln!("[rewrite] no matches found, nothing to rewrite");
        return Ok(());
    }

    if args.diff {
        print!("{}", plan.generate_diff()?);
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
    let plan = backend.plan_rewrites(pattern, replacement, &args.lang, path)?;
    let plan = filter_rewrite_plan(&plan, args)?;

    if plan.edits.is_empty() && plan.rejected_overlaps.is_empty() {
        eprintln!("[rewrite] no matches found, nothing to rewrite");
        return Ok(());
    }

    let checkpoint = if args.checkpoint {
        let checkpoint = create_checkpoint(path)?;
        eprintln!(
            "[checkpoint] created {} ({}, files={})",
            checkpoint.checkpoint_id, checkpoint.mode, checkpoint.file_count
        );
        Some(checkpoint)
    } else {
        None
    };

    let before_hashes = if args.audit_manifest.is_some() {
        Some(collect_pre_apply_hashes(&plan.edits)?)
    } else {
        None
    };

    AstBackend::apply_rewrites(&plan)?;

    if !plan.rejected_overlaps.is_empty() {
        eprintln!(
            "[rewrite] {} overlapping edit(s) rejected",
            plan.rejected_overlaps.len()
        );
    }

    eprintln!("[rewrite] applied {} edit(s)", plan.edits.len(),);

    let verification = if args.verify {
        let v = plan.verify(backend)?;
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
        Some(v)
    } else {
        None
    };

    let validation = run_post_apply_validation(args, path);
    if let Some(summary) = &validation {
        emit_validation_status(summary);
    }

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
        eprintln!("[rewrite] no matches found, nothing to rewrite");
        return Ok(());
    }

    if args.diff {
        if plan.edits.is_empty() {
            eprintln!("[rewrite] no non-overlapping matches found, nothing to diff");
            return Ok(());
        }
        print!("{}", plan.generate_diff()?);
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
        eprintln!("[rewrite] no matches found, nothing to rewrite");
        return Ok(());
    }

    let checkpoint = if args.checkpoint {
        let checkpoint = create_checkpoint(path)?;
        eprintln!(
            "[checkpoint] created {} ({}, files={})",
            checkpoint.checkpoint_id, checkpoint.mode, checkpoint.file_count
        );
        Some(checkpoint)
    } else {
        None
    };

    let before_hashes = if args.audit_manifest.is_some() {
        Some(collect_pre_apply_hashes(&plan.edits)?)
    } else {
        None
    };

    AstBackend::apply_batch_rewrites(&plan)?;

    if !plan.rejected_overlaps.is_empty() {
        eprintln!(
            "[rewrite] {} overlapping edit(s) rejected",
            plan.rejected_overlaps.len()
        );
    }

    if plan.edits.is_empty() {
        eprintln!("[rewrite] no non-overlapping edits applied");
    } else {
        eprintln!("[rewrite] applied {} edit(s)", plan.edits.len());
    }

    let verification = if config.verify || args.verify {
        let result = plan.verify(backend)?;
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
        Some(result)
    } else {
        None
    };

    let validation = run_post_apply_validation(args, path);
    if let Some(summary) = &validation {
        emit_validation_status(summary);
    }

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
    fixed_strings: bool,
    invert_match: bool,
    count: bool,
    context: Option<usize>,
    max_count: Option<usize>,
    word_regexp: bool,
    globs: Vec<String>,
    no_ignore: bool,
    gpu_device_ids: &'a [i32],
    json: bool,
    ndjson: bool,
    verbose: bool,
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
    handle_gpu_sidecar_search(params)
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
    if params.ignore_case {
        Some("ignore-case searches are not yet supported by native GPU routing")
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
                        "warning: {}; falling back to native CPU search",
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
                            RoutingDecision::native_cpu_gpu_fallback(
                                ripgrep_is_available(),
                                params.json || params.ndjson,
                            ),
                            params.query,
                            params.path,
                            params.json,
                            params.ndjson,
                            params.count,
                            matches,
                        );
                    }
                    run_native_search_with_optional_rg_fallback(cpu_fallback_config, rg_fallback)
                }
                GpuRouteFailureKind::Fatal => {
                    eprintln!("{}", failure.message);
                    std::process::exit(2);
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
                        "warning: {}; falling back to native CPU search",
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
                                ignore_case: params.ignore_case,
                                fixed_strings: params.fixed_strings,
                                invert_match: params.invert_match,
                                count: params.count,
                                line_number: params.line_number,
                                only_matching: false,
                                context: params.context,
                                max_count: params.max_count,
                                word_regexp: params.word_regexp,
                                globs: params.globs.clone(),
                                no_ignore: params.no_ignore,
                                color: None,
                                patterns: params.patterns.to_vec(),
                                path: params.path.to_string(),
                            });
                    if cpu_config.verbose {
                        emit_verbose_metadata(fallback_decision);
                    }
                    if params.patterns.len() > 1 {
                        let matches =
                            collect_native_multi_pattern_matches(params.patterns, cpu_config)?;
                        return emit_multi_pattern_native_results(
                            fallback_decision,
                            params.query,
                            params.path,
                            params.json,
                            params.ndjson,
                            params.count,
                            matches,
                        );
                    }
                    run_native_search_with_optional_rg_fallback(cpu_config, rg_fallback)
                }
                GpuRouteFailureKind::Fatal => {
                    eprintln!("{}", failure.message);
                    std::process::exit(2);
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
        "fixed_strings": params.fixed_strings,
        "invert_match": params.invert_match,
        "count": params.count,
        "context": params.context,
        "max_count": params.max_count,
        "word_regexp": params.word_regexp,
        "globs": params.globs,
        "no_ignore": params.no_ignore,
        "gpu_device_ids": params.gpu_device_ids,
        "json": params.json || params.ndjson,
    });

    match execute_sidecar_command("gpu_search", vec![], Some(payload)) {
        Ok(result) => {
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
                        matches,
                    )?;
                } else if params.json {
                    let normalized = normalize_gpu_sidecar_json(&result.stdout)?;
                    println!("{}", serde_json::to_string_pretty(&normalized)?);
                } else {
                    print!("{}", result.stdout);
                }
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
    matches: Vec<SearchMatchJson>,
) -> anyhow::Result<()> {
    let payload = SearchResultJson {
        version: JSON_OUTPUT_VERSION,
        routing_backend: decision.routing_backend(),
        routing_reason: decision.reason,
        sidecar_used: decision.sidecar_used(),
        query: pattern,
        path,
        total_matches: matches.len(),
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

fn emit_plain_search_matches(path: &str, matches: &[SearchMatchJson]) {
    let unique = unique_line_matches(matches);
    let with_filename = unique
        .iter()
        .map(|matched| matched.file.as_str())
        .collect::<std::collections::BTreeSet<_>>()
        .len()
        > 1
        || Path::new(path).is_dir();
    for matched in unique {
        if with_filename {
            println!("{}:{}:{}", matched.file, matched.line, matched.text);
        } else {
            println!("{}:{}", matched.line, matched.text);
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
    let payload = GpuNativeSearchResultJson {
        version: JSON_OUTPUT_VERSION,
        routing_backend: decision.routing_backend(),
        routing_reason: decision.reason,
        sidecar_used: decision.sidecar_used(),
        query: params.query,
        path: params.path,
        total_matches: stats.total_matches,
        total_files: stats.matched_files,
        routing_gpu_device_ids: stats
            .selected_devices
            .iter()
            .map(|device| device.device_id)
            .collect(),
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
            "[gpu-native] selected_gpu_device_id={} selected_gpu_device_name={} gpu_batch_files={} gpu_transfer_bytes={} gpu_streams={} gpu_double_buffered={} pinned_host_buffers={} gpu_batch_count={} gpu_overlap_batches={} gpu_pattern_count={} gpu_pattern_batches={} gpu_single_dispatch={} gpu_transfer_throughput_gbps={:.2}",
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
        "[gpu-native] selected_gpu_device_ids={} selected_gpu_device_names={} gpu_batch_files={} gpu_transfer_bytes={} gpu_streams={} gpu_double_buffered={} pinned_host_buffers={} gpu_batch_count={} gpu_overlap_batches={} gpu_pattern_count={} gpu_pattern_batches={} gpu_single_dispatch={} gpu_transfer_throughput_gbps={:.2}",
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
        stats.pipeline.transfer_throughput_bytes_s / 1_000_000_000.0
    );

    for device_stats in &stats.device_stats {
        eprintln!(
            "[gpu-native] gpu_device_id={} gpu_device_name={} gpu_device_files={} gpu_device_matches={} gpu_device_transfer_bytes={} gpu_device_streams={} gpu_device_batch_count={} gpu_device_transfer_throughput_gbps={:.2}",
            device_stats.device.device_id,
            device_stats.device.name,
            device_stats.searched_files,
            device_stats.total_matches,
            device_stats.transfer_bytes,
            device_stats.pipeline.stream_count,
            device_stats.pipeline.batch_count,
            device_stats.pipeline.transfer_throughput_bytes_s / 1_000_000_000.0
        );
    }
}

fn emit_ndjson_search_results(
    decision: RoutingDecision,
    pattern: &str,
    path: &str,
    matches: Vec<SearchMatchJson>,
) -> anyhow::Result<()> {
    for matched in matches {
        let payload = SearchMatchNdjson {
            version: JSON_OUTPUT_VERSION,
            routing_backend: decision.routing_backend(),
            routing_reason: decision.reason,
            sidecar_used: decision.sidecar_used(),
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

fn normalize_gpu_sidecar_json(stdout: &str) -> anyhow::Result<serde_json::Value> {
    let payload = parse_gpu_sidecar_search_payload(stdout)?;

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

    Ok(serde_json::json!({
        "version": JSON_OUTPUT_VERSION,
        "routing_backend": RoutingDecision::gpu_sidecar().routing_backend(),
        "routing_reason": RoutingDecision::gpu_sidecar().reason,
        "sidecar_used": RoutingDecision::gpu_sidecar().sidecar_used(),
        "total_matches": payload.total_matches,
        "total_files": payload.total_files,
        "routing_gpu_device_ids": payload.routing_gpu_device_ids,
        "matches": normalized_matches,
    }))
}

fn emit_verbose_metadata(decision: RoutingDecision) {
    eprintln!(
        "[routing] routing_backend={} routing_reason={} sidecar_used={}",
        decision.routing_backend(),
        decision.reason,
        decision.sidecar_used()
    );
}

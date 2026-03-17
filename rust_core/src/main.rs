use anyhow::Context;
use clap::{Args, Parser, Subcommand};
#[cfg(feature = "cuda")]
use ignore::{overrides::OverrideBuilder, WalkBuilder};
use serde::{Deserialize, Serialize};
use std::env;
use std::ffi::OsString;
#[cfg(feature = "cuda")]
use std::fs;
use std::path::{Path, PathBuf};
use std::time::Instant;
use tensor_grep_rs::backend_ast::{AstBackend, BatchRewritePlan, BatchRewriteRule};
use tensor_grep_rs::backend_cpu::CpuBackend;
#[cfg(feature = "cuda")]
use tensor_grep_rs::gpu_native::{gpu_native_search_paths, GpuNativeSearchConfig, GpuNativeSearchStats};
use tensor_grep_rs::index::TrigramIndex;
use tensor_grep_rs::native_search::{run_native_search, NativeSearchConfig, SearchStats};
use tensor_grep_rs::python_sidecar::{
    execute_python_passthrough_command, execute_sidecar_command, SidecarError,
};
use tensor_grep_rs::rg_passthrough::{
    execute_ripgrep_search, ripgrep_is_available, RipgrepSearchArgs,
};

const ENVIRONMENT_OVERRIDES_HELP: &str = "Environment overrides:\n  TG_SIDECAR_PYTHON  Path to the Python executable used for sidecar-backed commands.\n  TG_RG_PATH         Path to the ripgrep executable used for text-search passthrough.";
const JSON_OUTPUT_VERSION: u32 = 1;
#[cfg(feature = "cuda")]
const GPU_AUTO_ROUTE_THRESHOLD_BYTES: u64 = 50 * 1024 * 1024;

#[derive(Parser, Debug)]
#[command(name = "tg")]
#[command(version = "0.2.0")]
#[command(about = "tensor-grep: GPU-Accelerated Log Parsing CLI")]
#[command(after_help = ENVIRONMENT_OVERRIDES_HELP)]
pub struct CommandCli {
    #[command(subcommand)]
    pub command: Commands,
}

#[derive(Parser, Debug)]
#[command(name = "tg")]
#[command(version = "0.2.0")]
#[command(about = "tensor-grep: GPU-Accelerated Log Parsing CLI")]
#[command(after_help = ENVIRONMENT_OVERRIDES_HELP)]
pub struct PositionalCli {
    /// The search pattern (regex or string)
    pub pattern: Option<String>,

    /// Path to search
    pub path: Option<String>,

    /// Count matching lines
    #[arg(short = 'c', long)]
    pub count: bool,

    /// Fixed string matching (disable regex)
    #[arg(short = 'F', long)]
    pub fixed_strings: bool,

    /// Invert match (select non-matching lines)
    #[arg(short = 'v', long)]
    pub invert_match: bool,

    /// Case insensitive search
    #[arg(short = 'i', long)]
    pub ignore_case: bool,

    /// Find and Replace in-place
    #[arg(long)]
    pub replace: Option<String>,

    /// Force the native CPU engine
    #[arg(long = "cpu", alias = "force-cpu")]
    pub force_cpu: bool,

    /// Route search to GPU backends via Python sidecar (comma-separated device IDs)
    #[arg(long = "gpu-device-ids", value_delimiter = ',')]
    pub gpu_device_ids: Vec<i32>,

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

    /// Emit machine-readable routing metadata as JSON
    #[arg(long, conflicts_with = "ndjson")]
    pub json: bool,

    /// Emit one JSON object per matching line (newline-delimited)
    #[arg(long, conflicts_with = "json")]
    pub ndjson: bool,

    /// Emit routing metadata on stderr before executing the search
    #[arg(long)]
    pub verbose: bool,

    /// The search pattern (regex or string)
    pub pattern: String,

    /// Path to search
    #[arg(default_value = ".")]
    pub path: String,
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

#[derive(Subcommand, Debug)]
pub enum Commands {
    /// Search for a regex pattern with ripgrep-compatible flags
    Search(SearchArgs),
    /// Start the AI-assistant Model Context Protocol (MCP) server
    Mcp,
    /// Run semantic NLP threat classification on logs via cyBERT
    Classify { file_path: String },
    /// Run GPU-accelerated AST structural queries (ast-grep parity)
    Run(RunArgs),
    /// Scan code by configuration
    Scan,
    /// Test AST rules
    Test,
    /// Create new ast-grep project
    New,
    /// Start Language Server
    Lsp,
}

#[derive(Clone, Copy)]
struct RoutingDecision {
    backend: &'static str,
    reason: &'static str,
    sidecar_used: bool,
}

impl RoutingDecision {
    const NATIVE_CPU_FORCE: Self = Self {
        backend: "NativeCpuBackend",
        reason: "force_cpu",
        sidecar_used: false,
    };

    const NATIVE_CPU_JSON: Self = Self {
        backend: "NativeCpuBackend",
        reason: "json_output",
        sidecar_used: false,
    };

    const NATIVE_CPU_RG_UNAVAILABLE: Self = Self {
        backend: "NativeCpuBackend",
        reason: "rg_unavailable",
        sidecar_used: false,
    };

    const RIPGREP: Self = Self {
        backend: "RipgrepBackend",
        reason: "rg_passthrough",
        sidecar_used: false,
    };

    const AST: Self = Self {
        backend: "AstBackend",
        reason: "ast-native",
        sidecar_used: false,
    };

    const GPU_SIDECAR: Self = Self {
        backend: "GpuSidecar",
        reason: "gpu-device-ids-explicit",
        sidecar_used: true,
    };

    #[cfg(feature = "cuda")]
    const GPU_NATIVE: Self = Self {
        backend: "gpu_native",
        reason: "gpu-device-ids-explicit-native",
        sidecar_used: false,
    };

    #[cfg(feature = "cuda")]
    const GPU_AUTO: Self = Self {
        backend: "gpu_native",
        reason: "gpu-auto-size-threshold",
        sidecar_used: false,
    };

    const NATIVE_CPU_AUTO: Self = Self {
        backend: "NativeCpuBackend",
        reason: "cpu-auto-size-threshold",
        sidecar_used: false,
    };

    #[cfg(feature = "cuda")]
    const NATIVE_CPU_GPU_FALLBACK: Self = Self {
        backend: "NativeCpuBackend",
        reason: "gpu-auto-fallback-cpu",
        sidecar_used: false,
    };

    const INDEX: Self = Self {
        backend: "TrigramIndex",
        reason: "index-accelerated",
        sidecar_used: false,
    };
}

fn main() -> anyhow::Result<()> {
    let raw_args: Vec<OsString> = std::env::args_os().collect();

    if raw_args.len() <= 1 {
        use clap::CommandFactory;

        let mut cmd = CommandCli::command();
        cmd.print_help()?;
        return Ok(());
    }

    if should_use_positional_cli(&raw_args) {
        return run_positional_cli(PositionalCli::parse_from(raw_args));
    }

    let cli = CommandCli::parse_from(raw_args);

    run_command_cli(cli)
}

fn run_command_cli(cli: CommandCli) -> anyhow::Result<()> {
    match cli.command {
        Commands::Search(args) => handle_ripgrep_search(args),
        Commands::Mcp => handle_python_passthrough("mcp", vec![]),
        Commands::Classify { file_path } => handle_sidecar_command("classify", vec![file_path]),
        Commands::Run(args) => handle_ast_run(args),
        Commands::Scan => handle_sidecar_command("scan", vec![]),
        Commands::Test => handle_sidecar_command("test", vec![]),
        Commands::New => handle_sidecar_command("new", vec![]),
        Commands::Lsp => handle_python_passthrough("lsp", vec![]),
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

    if !cli.gpu_device_ids.is_empty() {
        return handle_gpu_search(GpuSearchParams {
            pattern: &pattern,
            path: &path,
            line_number: true,
            ignore_case: cli.ignore_case,
            fixed_strings: cli.fixed_strings,
            invert_match: cli.invert_match,
            count: cli.count,
            context: None,
            max_count: None,
            word_regexp: false,
            globs: Vec::new(),
            no_ignore: true,
            gpu_device_ids: &cli.gpu_device_ids,
            json: cli.json,
            ndjson: cli.ndjson,
            verbose: cli.verbose,
        });
    }

    if let Some(replacement) = cli.replace {
        let backend = CpuBackend::new();
        backend.replace_in_place(
            &pattern,
            &replacement,
            &path,
            cli.ignore_case,
            cli.fixed_strings,
        )?;
        println!("Replaced matches with '{}'", replacement);
        return Ok(());
    }

    if explicit_rg_override_requested() && !cli.force_cpu && !cli.json && !cli.ndjson {
        if cli.verbose {
            emit_verbose_metadata(RoutingDecision::RIPGREP);
        }

        let exit_code = execute_ripgrep_search(&positional_ripgrep_args(&cli, &pattern, &path))?;
        if exit_code != 0 {
            std::process::exit(exit_code.max(1));
        }
        return Ok(());
    }

    let rg_available = ripgrep_is_available();
    let cpu_decision = select_native_search_routing(
        cli.force_cpu,
        cli.json,
        cli.ndjson,
        cli.verbose,
        rg_available,
    );

    #[cfg(feature = "cuda")]
    let corpus_bytes = count_search_corpus_bytes(&[PathBuf::from(&path)], true, &[]).unwrap_or(0);

    #[cfg(feature = "cuda")]
    if should_attempt_auto_gpu(cli.force_cpu, corpus_bytes) {
        let auto_gpu_ids: [i32; 0] = [];
        let params = GpuSearchParams {
            pattern: &pattern,
            path: &path,
            line_number: true,
            ignore_case: cli.ignore_case,
            fixed_strings: cli.fixed_strings,
            invert_match: cli.invert_match,
            count: cli.count,
            context: None,
            max_count: None,
            word_regexp: false,
            globs: Vec::new(),
            no_ignore: true,
            gpu_device_ids: &auto_gpu_ids,
            json: cli.json,
            ndjson: cli.ndjson,
            verbose: cli.verbose,
        };

        if gpu_native_fallback_reason(&params).is_none() {
            let fallback_decision = cpu_decision.unwrap_or(RoutingDecision::NATIVE_CPU_GPU_FALLBACK);
            let rg_fallback = should_allow_rg_fallback(fallback_decision, cli.json, cli.ndjson, rg_available)
                .then(|| positional_ripgrep_args(&cli, &pattern, &path));
            return handle_auto_gpu_search(
                params,
                native_search_config_for_positional(&cli, &pattern, &path, RoutingDecision::NATIVE_CPU_GPU_FALLBACK),
                rg_fallback,
            );
        }
    }

    if let Some(cpu_decision) = cpu_decision {
        if cli.verbose {
            emit_verbose_metadata(cpu_decision);
        }

        let rg_fallback = should_allow_rg_fallback(cpu_decision, cli.json, cli.ndjson, rg_available)
            .then(|| positional_ripgrep_args(&cli, &pattern, &path));

        return run_native_search_with_optional_rg_fallback(
            native_search_config_for_positional(&cli, &pattern, &path, cpu_decision),
            rg_fallback,
        );
    }

    if cli.verbose {
        emit_verbose_metadata(RoutingDecision::RIPGREP);
    }

    let exit_code = execute_ripgrep_search(&positional_ripgrep_args(&cli, &pattern, &path))?;
    if exit_code != 0 {
        std::process::exit(exit_code.max(1));
    }
    Ok(())
}

fn should_use_positional_cli(raw_args: &[OsString]) -> bool {
    const SUBCOMMANDS: &[&str] = &["search", "mcp", "classify", "run", "scan", "test", "new", "lsp"];

    for arg in raw_args.iter().skip(1) {
        let token = arg.to_string_lossy();
        if token == "--help" || token == "-h" || token == "--version" || token == "-V" {
            return false;
        }
        if token.starts_with('-') {
            continue;
        }
        return !SUBCOMMANDS.contains(&token.as_ref());
    }

    false
}

fn select_native_search_routing(
    force_cpu: bool,
    json: bool,
    ndjson: bool,
    verbose: bool,
    rg_available: bool,
) -> Option<RoutingDecision> {
    if force_cpu {
        Some(RoutingDecision::NATIVE_CPU_FORCE)
    } else if json || ndjson {
        Some(RoutingDecision::NATIVE_CPU_JSON)
    } else if !rg_available {
        Some(RoutingDecision::NATIVE_CPU_RG_UNAVAILABLE)
    } else if verbose {
        Some(RoutingDecision::NATIVE_CPU_AUTO)
    } else {
        None
    }
}

#[cfg(feature = "cuda")]
fn count_search_corpus_bytes(paths: &[PathBuf], no_ignore: bool, globs: &[String]) -> anyhow::Result<u64> {
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
            overrides.add(glob).with_context(|| format!("failed to add glob override '{glob}'"))?;
        }
        builder.overrides(
            overrides
                .build()
                .context("failed to build glob override matcher")?,
        );
    }

    for entry in builder.build() {
        let entry = entry?;
        if entry.file_type().map(|kind| kind.is_file()).unwrap_or(false) {
            total_bytes = total_bytes.saturating_add(entry.metadata()?.len());
        }
    }

    Ok(total_bytes)
}

#[cfg(feature = "cuda")]
fn should_attempt_auto_gpu(force_cpu: bool, corpus_bytes: u64) -> bool {
    !force_cpu && corpus_bytes > GPU_AUTO_ROUTE_THRESHOLD_BYTES
}

fn should_allow_rg_fallback(decision: RoutingDecision, json: bool, ndjson: bool, rg_available: bool) -> bool {
    !json
        && !ndjson
        && rg_available
        && matches!(
            decision.reason,
            "cpu-auto-size-threshold" | "gpu-auto-fallback-cpu" | "rg_unavailable"
        )
}

fn positional_ripgrep_args(cli: &PositionalCli, pattern: &str, path: &str) -> RipgrepSearchArgs {
    RipgrepSearchArgs {
        ignore_case: cli.ignore_case,
        fixed_strings: cli.fixed_strings,
        invert_match: cli.invert_match,
        count: cli.count,
        line_number: true,
        context: None,
        max_count: None,
        word_regexp: false,
        globs: Vec::new(),
        no_ignore: true,
        pattern: pattern.to_string(),
        path: path.to_string(),
    }
}

fn explicit_rg_override_requested() -> bool {
    env::var_os("TG_RG_PATH").is_some() || env::var_os("TG_RG_BINARY").is_some()
}

fn command_ripgrep_args(args: &SearchArgs) -> RipgrepSearchArgs {
    RipgrepSearchArgs {
        ignore_case: args.ignore_case,
        fixed_strings: args.fixed_strings,
        invert_match: args.invert_match,
        count: args.count,
        line_number: false,
        context: args.context,
        max_count: args.max_count,
        word_regexp: args.word_regexp,
        globs: args.globs.clone(),
        no_ignore: args.no_ignore,
        pattern: args.pattern.clone(),
        path: args.path.clone(),
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
        routing_backend: decision.backend,
        routing_reason: decision.reason,
        sidecar_used: decision.sidecar_used,
        ignore_case: cli.ignore_case,
        fixed_strings: cli.fixed_strings,
        invert_match: cli.invert_match,
        count: cli.count,
        no_ignore: true,
        json: cli.json,
        ndjson: cli.ndjson,
        verbose: cli.verbose,
        line_number: true,
        ..NativeSearchConfig::default()
    }
}

fn native_search_config_for_command(args: &SearchArgs, decision: RoutingDecision) -> NativeSearchConfig {
    NativeSearchConfig {
        pattern: args.pattern.clone(),
        paths: vec![PathBuf::from(&args.path)],
        routing_backend: decision.backend,
        routing_reason: decision.reason,
        sidecar_used: decision.sidecar_used,
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
        ..NativeSearchConfig::default()
    }
}

#[cfg(feature = "cuda")]
fn native_search_config_for_gpu_params(
    params: &GpuSearchParams<'_>,
    decision: RoutingDecision,
) -> NativeSearchConfig {
    NativeSearchConfig {
        pattern: params.pattern.to_string(),
        paths: vec![PathBuf::from(params.path)],
        routing_backend: decision.backend,
        routing_reason: decision.reason,
        sidecar_used: decision.sidecar_used,
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
                    emit_verbose_metadata(RoutingDecision::RIPGREP);
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
    if args.index {
        return handle_index_search(&args);
    }

    if !args.gpu_device_ids.is_empty() {
        return handle_gpu_search(GpuSearchParams {
            pattern: &args.pattern,
            path: &args.path,
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
            gpu_device_ids: &args.gpu_device_ids,
            json: args.json,
            ndjson: args.ndjson,
            verbose: args.verbose,
        });
    }

    if explicit_rg_override_requested() && !args.force_cpu && !args.json && !args.ndjson {
        if args.verbose {
            emit_verbose_metadata(RoutingDecision::RIPGREP);
        }

        let exit_code = execute_ripgrep_search(&command_ripgrep_args(&args))?;
        if exit_code != 0 {
            std::process::exit(exit_code.max(1));
        }
        return Ok(());
    }

    if !args.force_cpu
        && !args.json
        && !args.ndjson
        && !args.index
        && !args.invert_match
        && args.context.is_none()
        && args.max_count.is_none()
        && !args.word_regexp && args.globs.is_empty()
    {
        let index_path = resolve_index_path(&args.path);
        if index_path.exists() {
            if let Ok(loaded) = TrigramIndex::load(&index_path) {
                if !loaded.is_stale() && args.pattern.len() >= 3 {
                    if args.verbose {
                        eprintln!(
                            "[routing] warm index found ({} files), using index-accelerated path",
                            loaded.file_count()
                        );
                    }
                    return run_index_query(&args, &loaded);
                }
            }
        }
    }

    let rg_available = ripgrep_is_available();
    let cpu_decision = select_native_search_routing(
        args.force_cpu,
        args.json,
        args.ndjson,
        args.verbose,
        rg_available,
    );

    #[cfg(feature = "cuda")]
    let corpus_bytes = count_search_corpus_bytes(&[PathBuf::from(&args.path)], args.no_ignore, &args.globs)
        .unwrap_or(0);

    #[cfg(feature = "cuda")]
    if should_attempt_auto_gpu(args.force_cpu, corpus_bytes) {
        let auto_gpu_ids: [i32; 0] = [];
        let params = GpuSearchParams {
            pattern: &args.pattern,
            path: &args.path,
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
        };

        if gpu_native_fallback_reason(&params).is_none() {
            let fallback_decision = cpu_decision.unwrap_or(RoutingDecision::NATIVE_CPU_GPU_FALLBACK);
            let rg_fallback = should_allow_rg_fallback(fallback_decision, args.json, args.ndjson, rg_available)
                .then(|| command_ripgrep_args(&args));
            return handle_auto_gpu_search(
                params,
                native_search_config_for_command(&args, RoutingDecision::NATIVE_CPU_GPU_FALLBACK),
                rg_fallback,
            );
        }
    }

    if let Some(cpu_decision) = cpu_decision {
        if args.verbose {
            emit_verbose_metadata(cpu_decision);
        }

        let rg_fallback = should_allow_rg_fallback(cpu_decision, args.json, args.ndjson, rg_available)
            .then(|| command_ripgrep_args(&args));

        return run_native_search_with_optional_rg_fallback(
            native_search_config_for_command(&args, cpu_decision),
            rg_fallback,
        );
    }

    if args.verbose {
        emit_verbose_metadata(RoutingDecision::RIPGREP);
    }

    let exit_code = execute_ripgrep_search(&command_ripgrep_args(&args))?;
    if exit_code != 0 {
        std::process::exit(exit_code.max(1));
    }
    Ok(())
}

fn resolve_index_path(search_path: &str) -> PathBuf {
    let root = Path::new(search_path);
    if root.is_file() {
        root.parent()
            .unwrap_or(Path::new("."))
            .join(".tg_index")
    } else {
        root.join(".tg_index")
    }
}

fn handle_index_search(args: &SearchArgs) -> anyhow::Result<()> {
    let index_path = resolve_index_path(&args.path);

    let index = if index_path.exists() {
        let loaded = match TrigramIndex::load(&index_path) {
            Ok(idx) => idx,
            Err(e) => {
                eprintln!("[index] warning: failed to load index: {e}, rebuilding...");
                let started = Instant::now();
                let fresh = TrigramIndex::build_with_options(Path::new(&args.path), args.no_ignore)?;
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
                return run_index_query(args, &fresh);
            }
        };
        if let Some(reason) = loaded.staleness_reason() {
            if args.verbose {
                eprintln!("[index] stale: {reason}");
            }
            let started = Instant::now();
            let update =
                loaded.rebuild_incremental_with_options(Path::new(&args.path), args.no_ignore)?;
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
            eprintln!("[index] full rebuild: building index for {}...", args.path);
        }
        let started = Instant::now();
        let fresh = TrigramIndex::build_with_options(Path::new(&args.path), args.no_ignore)?;
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

    run_index_query(args, &index)
}

fn run_index_query(args: &SearchArgs, index: &TrigramIndex) -> anyhow::Result<()> {
    if args.verbose {
        emit_verbose_metadata(RoutingDecision::INDEX);
    }

    let results = index.search(&args.pattern, args.ignore_case, args.fixed_strings)?;

    if args.json {
        return emit_json_search_results(
            RoutingDecision::INDEX,
            &args.pattern,
            &args.path,
            results
                .into_iter()
                .map(|result| SearchMatchJson {
                    file: result.file.to_string_lossy().into_owned(),
                    line: result.line,
                    text: result.text,
                })
                .collect(),
        );
    }

    if args.ndjson {
        return emit_ndjson_search_results(
            RoutingDecision::INDEX,
            &args.pattern,
            &args.path,
            results
                .into_iter()
                .map(|result| SearchMatchJson {
                    file: result.file.to_string_lossy().into_owned(),
                    line: result.line,
                    text: result.text,
                })
                .collect(),
        );
    }

    if args.count {
        println!("{}", results.len());
        return Ok(());
    }

    for result in &results {
        println!("{}:{}:{}", result.file.display(), result.line, result.text);
    }

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
    plan: &'a tensor_grep_rs::backend_ast::RewritePlan,
    verification: Option<&'a tensor_grep_rs::backend_ast::VerifyResult>,
}

#[derive(Serialize)]
struct BatchApplyVerifyJson<'a> {
    version: u32,
    routing_backend: &'static str,
    routing_reason: &'static str,
    sidecar_used: bool,
    plan: &'a BatchRewritePlan,
    verification: Option<&'a tensor_grep_rs::backend_ast::VerifyResult>,
}

#[derive(Debug, Clone)]
struct BatchRewriteConfig {
    rewrites: Vec<BatchRewriteRule>,
    verify: bool,
}

#[derive(Serialize)]
struct SearchMatchJson {
    file: String,
    line: usize,
    text: String,
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

fn load_batch_rewrite_config(config_path: &Path) -> anyhow::Result<BatchRewriteConfig> {
    let contents = std::fs::read_to_string(config_path)
        .with_context(|| format!("failed to read batch rewrite config {}", config_path.display()))?;
    let value: serde_json::Value = serde_json::from_str(&contents)
        .with_context(|| format!("failed to parse batch rewrite config {}", config_path.display()))?;
    parse_batch_rewrite_config_value(&value)
}

fn parse_batch_rewrite_config_value(value: &serde_json::Value) -> anyhow::Result<BatchRewriteConfig> {
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
        anyhow::bail!("invalid batch rewrite config field `rewrites`: expected at least one rewrite rule");
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
        let replacement = read_batch_rewrite_string_field(rule_object, &field_prefix, "replacement")?;
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
        anyhow::bail!("invalid batch rewrite config field `{field_path}`: expected non-empty string");
    }
    Ok(string_value.to_string())
}

fn handle_ast_run(args: RunArgs) -> anyhow::Result<()> {
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

    let matches = backend.search(pattern, &args.lang, path)?;

    if args.json {
        return emit_json_search_results(
            RoutingDecision::AST,
            pattern,
            path,
            matches
                .into_iter()
                .map(|matched| SearchMatchJson {
                    file: matched.file.to_string_lossy().into_owned(),
                    line: matched.line,
                    text: matched.matched_text,
                })
                .collect(),
        );
    }

    if args.verbose {
        emit_verbose_metadata(RoutingDecision::AST);
    }

    for matched in matches {
        println!("{}", matched.format_for_cli());
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
        emit_verbose_metadata(RoutingDecision::AST);
    }

    let pattern = run_pattern(args)?;
    let plan = backend.plan_rewrites(pattern, replacement, &args.lang, path)?;

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
        emit_verbose_metadata(RoutingDecision::AST);
    }

    let pattern = run_pattern(args)?;
    let plan = backend.plan_and_apply(pattern, replacement, &args.lang, path)?;

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

    eprintln!(
        "[rewrite] applied {} edit(s)",
        plan.edits.len(),
    );

    let verification = if args.verify {
        let v = plan.verify(backend)?;
        if v.mismatches.is_empty() {
            eprintln!(
                "[verify] {}/{} edits verified",
                v.verified, v.total_edits
            );
        } else {
            eprintln!(
                "[verify] {}/{} edits verified, {} mismatches",
                v.verified, v.total_edits, v.mismatches.len()
            );
        }
        Some(v)
    } else {
        None
    };

    if args.json {
        let payload = ApplyVerifyJson {
            version: plan.version,
            routing_backend: plan.routing_backend,
            routing_reason: plan.routing_reason,
            sidecar_used: plan.sidecar_used,
            plan: &plan,
            verification: verification.as_ref(),
        };
        println!("{}", serde_json::to_string_pretty(&payload)?);
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
        emit_verbose_metadata(RoutingDecision::AST);
    }

    let plan = backend.plan_batch_rewrites(&config.rewrites, path)?;

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
        emit_verbose_metadata(RoutingDecision::AST);
    }

    let plan = backend.plan_and_apply_batch(&config.rewrites, path)?;

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

    if plan.edits.is_empty() {
        eprintln!("[rewrite] no non-overlapping edits applied");
    } else {
        eprintln!("[rewrite] applied {} edit(s)", plan.edits.len());
    }

    let verification = if config.verify || args.verify {
        let result = plan.verify(backend)?;
        if result.mismatches.is_empty() {
            eprintln!("[verify] {}/{} edits verified", result.verified, result.total_edits);
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

    if args.json {
        let payload = BatchApplyVerifyJson {
            version: plan.version,
            routing_backend: plan.routing_backend,
            routing_reason: plan.routing_reason,
            sidecar_used: plan.sidecar_used,
            plan: &plan,
            verification: verification.as_ref(),
        };
        println!("{}", serde_json::to_string_pretty(&payload)?);
    }

    Ok(())
}

struct GpuSearchParams<'a> {
    pattern: &'a str,
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
    } else if !params.fixed_strings && pattern_requires_regex_engine(params.pattern) {
        Some("regex patterns still require the Python GPU sidecar")
    } else {
        None
    }
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
            message: format!("CUDA initialization failed: {}", sanitize_cuda_detail(reason)),
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
    compact.trim().trim_matches(|ch| ch == ':' || ch == '.').to_string()
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

    GpuRouteFailure {
        kind: GpuRouteFailureKind::Fatal,
        message: format!(
            "CUDA initialization failed: {}",
            sanitize_cuda_detail(raw_message)
        ),
    }
}

#[cfg(feature = "cuda")]
fn execute_gpu_native_route(
    params: &GpuSearchParams<'_>,
    decision: RoutingDecision,
    device_id: i32,
) -> anyhow::Result<()> {
    if let Some(simulated) = simulated_gpu_route_failure() {
        anyhow::bail!(simulated.message);
    }

    if params.verbose {
        emit_verbose_metadata(decision);
        if !params.gpu_device_ids.is_empty() && params.gpu_device_ids.len() > 1 {
            eprintln!(
                "[gpu-native] multi-GPU routing is not implemented yet; using device {} from {:?}",
                device_id, params.gpu_device_ids
            );
        }
    }

    let config = GpuNativeSearchConfig {
        pattern: params.pattern.to_string(),
        paths: vec![PathBuf::from(params.path)],
        no_ignore: params.no_ignore,
        glob: params.globs.clone(),
        max_batch_bytes: None,
    };

    let stats = gpu_native_search_paths(&config, device_id)?;
    if params.verbose {
        emit_gpu_native_verbose(&stats);
    }

    if params.json {
        emit_gpu_native_json_results(decision, params, &stats)?;
    } else if params.ndjson {
        emit_ndjson_search_results(
            decision,
            params.pattern,
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
    match execute_gpu_native_route(&params, RoutingDecision::GPU_AUTO, 0) {
        Ok(()) => Ok(()),
        Err(err) => {
            let failure = classify_gpu_route_failure(&err.to_string());
            match failure.kind {
                GpuRouteFailureKind::Unavailable => {
                    eprintln!("warning: {}; falling back to native CPU search", failure.message);
                    if cpu_fallback_config.verbose {
                        emit_verbose_metadata(RoutingDecision::NATIVE_CPU_GPU_FALLBACK);
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
    let Some(&device_id) = params.gpu_device_ids.first() else {
        return handle_gpu_sidecar_search(params);
    };

    match execute_gpu_native_route(&params, RoutingDecision::GPU_NATIVE, device_id) {
        Ok(()) => Ok(()),
        Err(err) => {
            let failure = classify_gpu_route_failure(&err.to_string());
            match failure.kind {
                GpuRouteFailureKind::Unavailable => {
                    eprintln!("warning: {}; falling back to native CPU search", failure.message);
                    let rg_available = ripgrep_is_available();
                    let cpu_config = native_search_config_for_gpu_params(
                        &params,
                        RoutingDecision::NATIVE_CPU_GPU_FALLBACK,
                    );
                    let rg_fallback = should_allow_rg_fallback(
                        RoutingDecision::NATIVE_CPU_GPU_FALLBACK,
                        params.json,
                        params.ndjson,
                        rg_available,
                    )
                    .then(|| RipgrepSearchArgs {
                        ignore_case: params.ignore_case,
                        fixed_strings: params.fixed_strings,
                        invert_match: params.invert_match,
                        count: params.count,
                        line_number: params.line_number,
                        context: params.context,
                        max_count: params.max_count,
                        word_regexp: params.word_regexp,
                        globs: params.globs.clone(),
                        no_ignore: params.no_ignore,
                        pattern: params.pattern.to_string(),
                        path: params.path.to_string(),
                    });
                    if cpu_config.verbose {
                        emit_verbose_metadata(RoutingDecision::NATIVE_CPU_GPU_FALLBACK);
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
        emit_verbose_metadata(RoutingDecision::GPU_SIDECAR);
    }

    let payload = serde_json::json!({
        "pattern": params.pattern,
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
                        })
                        .collect();
                    emit_ndjson_search_results(
                        RoutingDecision::GPU_SIDECAR,
                        params.pattern,
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
        routing_backend: decision.backend,
        routing_reason: decision.reason,
        sidecar_used: decision.sidecar_used,
        query: pattern,
        path,
        total_matches: matches.len(),
        matches,
    };

    println!("{}", serde_json::to_string(&payload)?);
    Ok(())
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
        routing_backend: decision.backend,
        routing_reason: decision.reason,
        sidecar_used: decision.sidecar_used,
        query: params.pattern,
        path: params.path,
        total_matches: stats.total_matches,
        total_files: stats.matched_files,
        routing_gpu_device_ids: vec![stats.selected_device.device_id],
        matches: gpu_native_match_json_entries(stats),
    };

    println!("{}", serde_json::to_string_pretty(&payload)?);
    Ok(())
}

#[cfg(feature = "cuda")]
fn emit_gpu_native_plain_results(params: &GpuSearchParams<'_>, stats: &GpuNativeSearchStats) {
    let with_filename = stats.searched_files > 1 || Path::new(params.path).is_dir();
    for matched in &stats.matches {
        if with_filename {
            println!(
                "{}:{}:{}",
                matched.path.display(),
                matched.line_number,
                matched.text
            );
        } else {
            println!("{}:{}", matched.line_number, matched.text);
        }
    }
}

#[cfg(feature = "cuda")]
fn emit_gpu_native_count_results(params: &GpuSearchParams<'_>, stats: &GpuNativeSearchStats) {
    let with_filename = stats.searched_files > 1 || Path::new(params.path).is_dir();
    let mut counts = std::collections::BTreeMap::<String, usize>::new();
    for matched in &stats.matches {
        *counts
            .entry(matched.path.to_string_lossy().into_owned())
            .or_default() += 1;
    }

    if with_filename {
        for (file, count) in counts {
            println!("{file}:{count}");
        }
    } else {
        println!("{}", stats.total_matches);
    }
}

#[cfg(feature = "cuda")]
fn emit_gpu_native_verbose(stats: &GpuNativeSearchStats) {
    eprintln!(
        "[gpu-native] selected_gpu_device_id={} selected_gpu_device_name={} gpu_batch_files={} gpu_transfer_bytes={} gpu_streams={} gpu_double_buffered={} pinned_host_buffers={} gpu_batch_count={} gpu_overlap_batches={} gpu_transfer_throughput_gbps={:.2}",
        stats.selected_device.device_id,
        stats.selected_device.name,
        stats.searched_files,
        stats.transfer_bytes,
        stats.pipeline.stream_count,
        stats.pipeline.double_buffered,
        stats.pipeline.pinned_host_buffers,
        stats.pipeline.batch_count,
        stats.pipeline.overlapped_batches,
        stats.pipeline.transfer_throughput_bytes_s / 1_000_000_000.0
    );
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
            routing_backend: decision.backend,
            routing_reason: decision.reason,
            sidecar_used: decision.sidecar_used,
            query: pattern,
            path,
            file: &matched.file,
            line: matched.line,
            text: &matched.text,
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
            serde_json::json!({
                "file": entry.file,
                "line_number": entry.line_number,
                "text": entry.text,
            })
        })
        .collect::<Vec<_>>();

    Ok(serde_json::json!({
        "version": JSON_OUTPUT_VERSION,
        "routing_backend": RoutingDecision::GPU_SIDECAR.backend,
        "routing_reason": RoutingDecision::GPU_SIDECAR.reason,
        "sidecar_used": RoutingDecision::GPU_SIDECAR.sidecar_used,
        "total_matches": payload.total_matches,
        "total_files": payload.total_files,
        "routing_gpu_device_ids": payload.routing_gpu_device_ids,
        "matches": normalized_matches,
    }))
}

fn emit_verbose_metadata(decision: RoutingDecision) {
    eprintln!(
        "[routing] routing_backend={} routing_reason={} sidecar_used={}",
        decision.backend, decision.reason, decision.sidecar_used
    );
}

use anyhow::Context;
#[cfg(feature = "cuda")]
use clap::ValueEnum;
use clap::{Args, Parser, Subcommand};
#[cfg(feature = "cuda")]
use ignore::{overrides::OverrideBuilder, WalkBuilder};
use serde::{Deserialize, Serialize};
use std::env;
use std::ffi::OsString;
#[cfg(feature = "cuda")]
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::time::Instant;
use tensor_grep_rs::backend_ast::{AstBackend, BatchRewritePlan, BatchRewriteRule};
use tensor_grep_rs::backend_cpu::CpuBackend;
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

#[derive(Subcommand, Debug)]
pub enum Commands {
    /// Search for a regex pattern with ripgrep-compatible flags
    Search(SearchArgs),
    /// Measure CPU vs GPU crossover thresholds and persist smart-routing calibration
    Calibrate(CalibrateArgs),
    /// Upgrade tensor-grep via the managed Python package path
    #[command(alias = "update")]
    Upgrade,
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

    if should_use_positional_cli(&raw_args) {
        return run_positional_cli(PositionalCli::parse_from(raw_args));
    }

    let cli = CommandCli::parse_from(raw_args);

    run_command_cli(cli)
}

fn run_command_cli(cli: CommandCli) -> anyhow::Result<()> {
    match cli.command {
        Commands::Search(args) => handle_ripgrep_search(args),
        Commands::Calibrate(args) => handle_calibrate_command(args),
        Commands::Upgrade => handle_python_passthrough("upgrade", vec![]),
        Commands::Mcp => handle_python_passthrough("mcp", vec![]),
        Commands::Classify { file_path } => handle_sidecar_command("classify", vec![file_path]),
        Commands::Run(args) => handle_ast_run(args),
        Commands::Scan => handle_sidecar_command("scan", vec![]),
        Commands::Test => handle_sidecar_command("test", vec![]),
        Commands::New => handle_sidecar_command("new", vec![]),
        Commands::Lsp => handle_python_passthrough("lsp", vec![]),
        #[cfg(feature = "cuda")]
        Commands::GpuNativeStats(args) => handle_gpu_native_stats_command(args),
        #[cfg(feature = "cuda")]
        Commands::GpuTransferBench(args) => handle_gpu_transfer_benchmark_command(args),
        #[cfg(feature = "cuda")]
        Commands::GpuCudaGraphs(args) => handle_gpu_cuda_graph_benchmark_command(args),
        #[cfg(feature = "cuda")]
        Commands::GpuOomProbe(args) => handle_gpu_oom_probe_command(args),
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

fn run_positional_cli(cli: PositionalCli) -> anyhow::Result<()> {
    if cli.pattern.is_none() || cli.path.is_none() {
        use clap::CommandFactory;
        let mut cmd = PositionalCli::command();
        cmd.print_help()?;
        return Ok(());
    }

    let pattern = cli.pattern.clone().unwrap();
    let path = cli.path.clone().unwrap();

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
        max_count: None,
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
                max_count: None,
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
    const SUBCOMMANDS: &[&str] = &[
        "search",
        "calibrate",
        "upgrade",
        "update",
        "mcp",
        "classify",
        "run",
        "scan",
        "test",
        "new",
        "lsp",
        "__gpu-native-stats",
        "__gpu-transfer-bench",
        "__gpu-cuda-graphs",
        "__gpu-oom-probe",
    ];

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
        line_number: true,
        context: None,
        max_count: None,
        word_regexp: false,
        globs: Vec::new(),
        no_ignore: true,
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
        line_number: false,
        context: args.context,
        max_count: args.max_count,
        word_regexp: args.word_regexp,
        globs: args.globs.clone(),
        no_ignore: args.no_ignore,
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
        no_ignore: true,
        json: cli.json,
        ndjson: cli.ndjson,
        verbose: cli.verbose,
        line_number: true,
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

#[derive(Debug, Clone, Serialize, PartialEq, Eq, PartialOrd, Ord)]
struct SearchMatchJson {
    file: String,
    line: usize,
    text: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pattern_id: Option<usize>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pattern_text: Option<String>,
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
            RoutingDecision::ast(),
            pattern,
            path,
            matches
                .into_iter()
                .map(|matched| SearchMatchJson {
                    file: matched.file.to_string_lossy().into_owned(),
                    line: matched.line,
                    text: matched.matched_text,
                    pattern_id: None,
                    pattern_text: None,
                })
                .collect(),
        );
    }

    if args.verbose {
        emit_verbose_metadata(RoutingDecision::ast());
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
        emit_verbose_metadata(RoutingDecision::ast());
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
        emit_verbose_metadata(RoutingDecision::ast());
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
        emit_verbose_metadata(RoutingDecision::ast());
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
        emit_verbose_metadata(RoutingDecision::ast());
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
                                context: params.context,
                                max_count: params.max_count,
                                word_regexp: params.word_regexp,
                                globs: params.globs.clone(),
                                no_ignore: params.no_ignore,
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

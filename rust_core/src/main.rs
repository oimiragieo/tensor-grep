use clap::{Args, Parser, Subcommand};
use serde::Serialize;
use std::ffi::OsString;
use tensor_grep_rs::backend_ast::AstBackend;
use tensor_grep_rs::backend_cpu::CpuBackend;
use tensor_grep_rs::python_sidecar::{
    execute_python_passthrough_command, execute_sidecar_command, SidecarError,
};
use tensor_grep_rs::rg_passthrough::{
    execute_ripgrep_search, ripgrep_is_available, RipgrepSearchArgs,
};

const ENVIRONMENT_OVERRIDES_HELP: &str = "Environment overrides:\n  TG_SIDECAR_PYTHON  Path to the Python executable used for sidecar-backed commands.\n  TG_RG_PATH         Path to the ripgrep executable used for text-search passthrough.";

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

    /// Force CPU fallback
    #[arg(long)]
    pub force_cpu: bool,

    /// Route search to GPU backends via Python sidecar (comma-separated device IDs)
    #[arg(long = "gpu-device-ids", value_delimiter = ',')]
    pub gpu_device_ids: Vec<i32>,

    /// Emit machine-readable routing metadata as JSON
    #[arg(long)]
    pub json: bool,

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

    /// Route search to GPU backends via Python sidecar (comma-separated device IDs)
    #[arg(long = "gpu-device-ids", value_delimiter = ',')]
    pub gpu_device_ids: Vec<i32>,

    /// Emit machine-readable routing metadata as JSON
    #[arg(long)]
    pub json: bool,

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
    #[arg(long)]
    pub rewrite: Option<String>,

    /// Apply rewrite edits to files (requires --rewrite)
    #[arg(long)]
    pub apply: bool,

    /// Emit machine-readable routing metadata as JSON
    #[arg(long)]
    pub json: bool,

    /// Emit routing metadata on stderr before executing the search
    #[arg(long)]
    pub verbose: bool,

    /// The structural ast-grep pattern
    pub pattern: String,

    /// File or directory to search
    #[arg(default_value = ".")]
    pub path: String,
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
    const CPU: Self = Self {
        backend: "CpuBackend",
        reason: "cpu-native",
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
}

#[derive(Serialize)]
struct RoutingMetadata<'a> {
    routing_backend: &'static str,
    routing_reason: &'static str,
    sidecar_used: bool,
    query: &'a str,
    path: &'a str,
    total_matches: usize,
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

    let pattern = cli.pattern.unwrap();
    let path = cli.path.unwrap();

    if !cli.gpu_device_ids.is_empty() {
        return handle_gpu_sidecar_search(GpuSearchParams {
            pattern: &pattern,
            path: &path,
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
            verbose: cli.verbose,
        });
    }

    if cli.json {
        let backend = CpuBackend::new();
        let total_matches = backend.count_matches(
            &pattern,
            &path,
            cli.ignore_case,
            cli.fixed_strings,
            cli.invert_match,
        )?;
        return emit_json_metadata(RoutingDecision::CPU, &pattern, &path, total_matches);
    }

    if cli.verbose {
        emit_verbose_metadata(RoutingDecision::CPU);
    }

    if !cli.force_cpu && cli.replace.is_none() && ripgrep_is_available() {
        let exit_code = execute_ripgrep_search(&RipgrepSearchArgs {
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
            pattern,
            path,
        })?;

        if exit_code != 0 {
            std::process::exit(exit_code.max(1));
        }

        return Ok(());
    }

    // Keep the plain text hot path in pure Rust. Python is initialized only for
    // explicit Python-backed subcommands until the GPU sidecar routing lands.
    let backend = CpuBackend::new();

    if let Some(replacement) = cli.replace {
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

    if cli.count {
        // The inner CpuBackend supports counting extremely fast via memmap
        let count = backend.count_matches(
            &pattern,
            &path,
            cli.ignore_case,
            cli.fixed_strings,
            cli.invert_match,
        )?;
        println!("{}", count);
        return Ok(());
    }

    let results = backend.search(
        &pattern,
        &path,
        cli.ignore_case,
        cli.fixed_strings,
        cli.invert_match,
    )?;

    for (line_num, text) in results {
        println!("{}:{}", line_num, text);
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

fn handle_ripgrep_search(args: SearchArgs) -> anyhow::Result<()> {
    if !args.gpu_device_ids.is_empty() {
        return handle_gpu_sidecar_search(GpuSearchParams {
            pattern: &args.pattern,
            path: &args.path,
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
            verbose: args.verbose,
        });
    }

    if args.json {
        let backend = CpuBackend::new();
        let total_matches = backend.count_matches(
            &args.pattern,
            &args.path,
            args.ignore_case,
            args.fixed_strings,
            args.invert_match,
        )?;
        return emit_json_metadata(RoutingDecision::CPU, &args.pattern, &args.path, total_matches);
    }

    if args.verbose {
        emit_verbose_metadata(RoutingDecision::CPU);
    }

    let exit_code = execute_ripgrep_search(&RipgrepSearchArgs {
        ignore_case: args.ignore_case,
        fixed_strings: args.fixed_strings,
        invert_match: args.invert_match,
        count: args.count,
        line_number: false,
        context: args.context,
        max_count: args.max_count,
        word_regexp: args.word_regexp,
        globs: args.globs,
        no_ignore: args.no_ignore,
        pattern: args.pattern,
        path: args.path,
    })?;

    if exit_code != 0 {
        std::process::exit(exit_code.max(1));
    }

    Ok(())
}

fn handle_ast_run(args: RunArgs) -> anyhow::Result<()> {
    let backend = AstBackend::new();

    if let Some(replacement) = &args.rewrite {
        return handle_ast_rewrite(&backend, &args, replacement);
    }

    let matches = backend.search(&args.pattern, &args.lang, &args.path)?;

    if args.json {
        return emit_json_metadata(
            RoutingDecision::AST,
            &args.pattern,
            &args.path,
            matches.len(),
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
) -> anyhow::Result<()> {
    if args.verbose {
        emit_verbose_metadata(RoutingDecision::AST);
    }

    let plan = backend.plan_rewrites(&args.pattern, replacement, &args.lang, &args.path)?;

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

    if args.json || !args.apply {
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

struct GpuSearchParams<'a> {
    pattern: &'a str,
    path: &'a str,
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
    verbose: bool,
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
        "json": params.json,
    });

    match execute_sidecar_command("gpu_search", vec![], Some(payload)) {
        Ok(result) => {
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

fn emit_json_metadata(
    decision: RoutingDecision,
    pattern: &str,
    path: &str,
    total_matches: usize,
) -> anyhow::Result<()> {
    let payload = RoutingMetadata {
        routing_backend: decision.backend,
        routing_reason: decision.reason,
        sidecar_used: decision.sidecar_used,
        query: pattern,
        path,
        total_matches,
    };

    println!("{}", serde_json::to_string(&payload)?);
    Ok(())
}

fn emit_verbose_metadata(decision: RoutingDecision) {
    eprintln!(
        "[routing] routing_backend={} routing_reason={} sidecar_used={}",
        decision.backend, decision.reason, decision.sidecar_used
    );
}

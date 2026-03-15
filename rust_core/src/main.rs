mod rg_passthrough;

use clap::{Args, Parser, Subcommand};
use std::ffi::OsString;
use tensor_grep_rs::backend_cpu::CpuBackend;
use tensor_grep_rs::python_sidecar::{
    execute_python_passthrough_command, execute_sidecar_command, SidecarError,
};
use rg_passthrough::{execute_ripgrep_search, ripgrep_is_available, RipgrepSearchArgs};

#[derive(Parser, Debug)]
#[command(name = "tg")]
#[command(version = "0.2.0")]
#[command(about = "tensor-grep: GPU-Accelerated Log Parsing CLI")]
pub struct CommandCli {
    #[command(subcommand)]
    pub command: Commands,
}

#[derive(Parser, Debug)]
#[command(name = "tg")]
#[command(version = "0.2.0")]
#[command(about = "tensor-grep: GPU-Accelerated Log Parsing CLI")]
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

    /// The search pattern (regex or string)
    pub pattern: String,

    /// Path to search
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
    Run {
        pattern: String,
        path: Option<String>,
    },
    /// Scan code by configuration
    Scan,
    /// Test AST rules
    Test,
    /// Create new ast-grep project
    New,
    /// Start Language Server
    Lsp,
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
        Commands::Run { pattern, path } => {
            let mut args = vec![pattern];
            if let Some(p) = path {
                args.push(p);
            }
            handle_sidecar_command("run", args)
        }
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

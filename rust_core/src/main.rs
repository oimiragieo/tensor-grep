use clap::{Parser, Subcommand};
use tensor_grep_rs::backend_cpu::CpuBackend;
use tensor_grep_rs::backend_gpu::{
    CliFlags, execute_gpu_pipeline, execute_python_module_fallback, should_use_gpu_pipeline,
};

#[derive(Parser, Debug)]
#[command(name = "tg")]
#[command(version = "0.5.0")]
#[command(about = "tensor-grep: GPU-Accelerated Log Parsing CLI")]
pub struct Cli {
    #[command(subcommand)]
    pub command: Option<Commands>,

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

#[derive(Subcommand, Debug)]
pub enum Commands {
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
    // Note: Due to pyo3 auto-initialize feature, Python interpreter initializes on start seamlessly
    let cli = Cli::parse();

    if let Some(cmd) = &cli.command {
        match cmd {
            Commands::Mcp => return execute_python_module_fallback("mcp_server", vec![]),
            Commands::Classify { file_path } => {
                return execute_python_module_fallback("classify", vec![file_path.clone()]);
            }
            Commands::Run { pattern, path } => {
                let mut args = vec![pattern.clone()];
                if let Some(p) = path {
                    args.push(p.clone());
                }
                return execute_python_module_fallback("run", args);
            }
            Commands::Scan => return execute_python_module_fallback("scan", vec![]),
            Commands::Test => return execute_python_module_fallback("test", vec![]),
            Commands::New => return execute_python_module_fallback("new", vec![]),
            Commands::Lsp => return execute_python_module_fallback("lsp", vec![]),
        }
    }

    if cli.pattern.is_none() || cli.path.is_none() {
        use clap::CommandFactory;
        let mut cmd = Cli::command();
        cmd.print_help()?;
        return Ok(());
    }

    let pattern = cli.pattern.unwrap();
    let path = cli.path.unwrap();

    let flags = CliFlags {
        count: cli.count,
        fixed_strings: cli.fixed_strings,
        invert_match: cli.invert_match,
        ignore_case: cli.ignore_case,
    };

    // Check if we should execute in Python/GPU land
    // We strictly force CPU execution if a replacement query is passed, since the Python GPU bindings don't support file mutability yet
    if !cli.force_cpu && cli.replace.is_none() && should_use_gpu_pipeline() {
        return execute_gpu_pipeline(&pattern, &path, &flags);
    }

    // Fallback to ultra-fast zero-copy Rust tier
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

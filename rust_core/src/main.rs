use clap::Parser;
use tensor_grep_rs::backend_cpu::CpuBackend;
use tensor_grep_rs::backend_gpu::{CliFlags, execute_gpu_pipeline, should_use_gpu_pipeline};

#[derive(Parser, Debug)]
#[command(name = "tg")]
#[command(version = "0.4.0")]
#[command(about = "tensor-grep: GPU-Accelerated Log Parsing CLI")]
pub struct Cli {
    /// The search pattern (regex or string)
    pub pattern: String,

    /// Path to search
    pub path: String,

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

fn main() -> anyhow::Result<()> {
    // Note: Due to pyo3 auto-initialize feature, Python interpreter initializes on start seamlessly
    let cli = Cli::parse();

    let flags = CliFlags {
        count: cli.count,
        fixed_strings: cli.fixed_strings,
        invert_match: cli.invert_match,
        ignore_case: cli.ignore_case,
    };

    // Check if we should execute in Python/GPU land
    if !cli.force_cpu && should_use_gpu_pipeline() {
        return execute_gpu_pipeline(&cli.pattern, &cli.path, &flags);
    }

    // Fallback to ultra-fast zero-copy Rust tier
    let backend = CpuBackend::new();

    if let Some(replacement) = cli.replace {
        backend.replace_in_place(
            &cli.pattern,
            &replacement,
            &cli.path,
            cli.ignore_case,
            cli.fixed_strings,
        )?;
        println!("Replaced matches with '{}'", replacement);
        return Ok(());
    }

    if cli.count {
        // The inner CpuBackend supports counting extremely fast via memmap
        let count = backend.count_matches(
            &cli.pattern,
            &cli.path,
            cli.ignore_case,
            cli.fixed_strings,
            cli.invert_match,
        )?;
        println!("{}", count);
        return Ok(());
    }

    let results = backend.search(
        &cli.pattern,
        &cli.path,
        cli.ignore_case,
        cli.fixed_strings,
        cli.invert_match,
    )?;

    for (line_num, text) in results {
        println!("{}:{}", line_num, text);
    }

    Ok(())
}

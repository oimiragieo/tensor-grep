pub mod backend_ast;
pub mod backend_cpu;
// pub mod backend_gpu;
pub mod cli;

use backend_ast::AstBackend;
use backend_cpu::CpuBackend;
use clap::Parser;
use cli::{Cli, Commands};

fn main() -> anyhow::Result<()> {
    let cli = Cli::parse();

    match cli.command {
        Commands::Search {
            pattern,
            path,
            ignore_case,
            fixed_strings,
            context: _,
        } => {
            let backend = CpuBackend::new();
            backend.search(&pattern, &path, ignore_case, fixed_strings)?;
        }
        Commands::Classify { file } => {
            println!("Classify stub: {}", file);
            // let backend = GpuBackend::new();
            // backend.classify(&file)?;
        }
        Commands::Run {
            pattern,
            lang,
            path,
        } => {
            let backend = AstBackend::new();
            backend.run(&pattern, &lang, &path)?;
        }
    }

    Ok(())
}

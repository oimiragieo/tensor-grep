use clap::{Parser, Subcommand};

#[derive(Parser, Debug)]
#[command(name = "tensor-grep-rs")]
#[command(about = "High-Performance Multi-GPU Log Parsing and Structural Code Retrieval", long_about = None)]
pub struct Cli {
    #[command(subcommand)]
    pub command: Commands,
}

#[derive(Subcommand, Debug)]
pub enum Commands {
    /// Search for a regex pattern (GPU accelerated)
    Search {
        /// A regular expression used for searching.
        pattern: String,

        /// A file or directory to search.
        #[arg(default_value = ".")]
        path: String,

        /// Case insensitive search
        #[arg(short = 'i', long)]
        ignore_case: bool,

        /// Treat pattern as a literal string
        #[arg(short = 'F', long)]
        fixed_strings: bool,

        /// Number of context lines
        #[arg(short = 'C', long)]
        context: Option<usize>,
    },
    /// Classify logs using cyBERT semantic NLP
    Classify {
        /// The log file to classify
        file: String,
    },
    /// Structural AST Code Search
    Run {
        /// Structural AST query (e.g., 'if ($A) { return $B; }')
        pattern: String,

        /// Language of the AST
        #[arg(long, default_value = "python")]
        lang: String,

        /// A file or directory to search.
        #[arg(default_value = ".")]
        path: String,
    },
}

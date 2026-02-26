use anyhow::Result;
use tree_sitter::{Parser, Query, QueryCursor, StreamingIterator};

pub struct AstBackend;

impl Default for AstBackend {
    fn default() -> Self {
        Self::new()
    }
}

impl AstBackend {
    pub fn new() -> Self {
        Self
    }

    pub fn run(&self, pattern: &str, lang: &str, path: &str) -> Result<()> {
        println!("Initializing Tree-sitter for language: {}", lang);

        let mut parser = Parser::new();

        // For this implementation we'll default to python if requested
        let language = if lang.to_lowercase() == "python" {
            tree_sitter_python::LANGUAGE.into()
        } else {
            anyhow::bail!("Unsupported language: {}", lang);
        };

        parser.set_language(&language)?;

        let code = std::fs::read_to_string(path)?;
        let tree = parser
            .parse(&code, None)
            .ok_or_else(|| anyhow::anyhow!("Failed to parse code"))?;

        println!("AST parsed successfully. Total bytes: {}", code.len());

        // Execute the structural query
        let query = Query::new(&language, pattern)?;
        let mut cursor = QueryCursor::new();
        let mut matches = cursor.matches(&query, tree.root_node(), code.as_bytes());

        let mut count = 0;
        while let Some(m) = matches.next() {
            // Safe access to the first capture
            if let Some(capture) = m.captures.first() {
                println!("Match found at byte range: {:?}", capture.node.byte_range());
                count += 1;
            }
        }

        println!("Found {} structural matches.", count);

        Ok(())
    }
}

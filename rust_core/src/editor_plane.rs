use crate::backend_ast::{resolve_language, AstBackend};
use crate::backend_ast_workflow::{AstWorkflowOrchestrator, ProjectDataV6};
use anyhow::Result;
use ast_grep_core::tree_sitter::LanguageExt;
use serde::{Deserialize, Serialize};
use std::collections::HashSet;
use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct SymbolDefinition {
    pub name: String,
    pub kind: String,
    pub file: PathBuf,
    pub line: usize,
    pub end_line: usize,
}

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct SymbolReference {
    pub name: String,
    pub kind: String,
    pub file: PathBuf,
    pub line: usize,
    pub text: String,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct DefsResponse {
    pub symbol: String,
    pub path: PathBuf,
    pub definitions: Vec<SymbolDefinition>,
    pub files: Vec<PathBuf>,
    pub related_paths: Vec<PathBuf>,
    pub graph_completeness: String,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct RefsResponse {
    pub symbol: String,
    pub path: PathBuf,
    pub references: Vec<SymbolReference>,
    pub files: Vec<PathBuf>,
    pub related_paths: Vec<PathBuf>,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct ContextResponse {
    pub query: String,
    pub path: PathBuf,
    pub definitions: Vec<SymbolDefinition>,
    pub references: Vec<SymbolReference>,
    pub symbols: Vec<SymbolDefinition>,
    pub files: Vec<PathBuf>,
    pub related_paths: Vec<PathBuf>,
}

pub fn handle_defs(path: PathBuf, symbol: String, _provider: String, json: bool) -> Result<()> {
    let config_path = find_config_file(&path)?;
    let orchestrator = AstWorkflowOrchestrator::new(config_path.as_ref().and_then(|p| p.to_str()))?;
    let data = orchestrator.load_project_data()?;
    let backend = AstBackend::new();

    let mut stdout = std::io::stdout();
    execute_defs_core(&path, &symbol, &data, &backend, json, &mut stdout)
}

/// Assemble the `defs` response.
///
/// Extracted from `execute_defs_core` (2026-09-12) so the `graph_completeness` wiring is
/// reachable from a test. Previously every test called `graph_completeness_for` DIRECTLY, so
/// reverting this construction to a hardcoded `"strong"` would have left the whole suite
/// green -- a mutation control that passes in both arms is not a control. See
/// `the_response_itself_is_wired_to_the_derived_value`.
fn build_defs_response(
    symbol: &str,
    path: &Path,
    definitions: Vec<SymbolDefinition>,
) -> DefsResponse {
    let mut definition_files = HashSet::new();
    for d in &definitions {
        definition_files.insert(d.file.clone());
    }

    let mut files: Vec<PathBuf> = definition_files.into_iter().collect();
    files.sort();

    DefsResponse {
        symbol: symbol.to_string(),
        graph_completeness: graph_completeness_for(definitions.len()).to_string(),
        path: path.to_path_buf(),
        definitions,
        files: files.clone(),
        related_paths: files,
    }
}

pub fn execute_defs_core(
    path: &Path,
    symbol: &str,
    data: &ProjectDataV6,
    backend: &AstBackend,
    json: bool,
    writer: &mut dyn Write,
) -> Result<()> {
    let definitions = find_definitions(backend, data, symbol)?;
    let response = build_defs_response(symbol, path, definitions);

    if json {
        writeln!(writer, "{}", serde_json::to_string_pretty(&response)?)?;
    } else {
        writeln!(writer, "Definitions for {} in {:?}", symbol, path)?;
        for d in &response.definitions {
            writeln!(writer, "{}:{}: {}", d.file.display(), d.line, d.name)?;
        }
        writeln!(
            writer,
            "definitions={} files={}",
            response.definitions.len(),
            response.files.len()
        )?;
    }

    Ok(())
}

pub fn handle_refs(path: PathBuf, symbol: String, _provider: String, json: bool) -> Result<()> {
    let config_path = find_config_file(&path)?;
    let orchestrator = AstWorkflowOrchestrator::new(config_path.as_ref().and_then(|p| p.to_str()))?;
    let data = orchestrator.load_project_data()?;
    let backend = AstBackend::new();

    let mut stdout = std::io::stdout();
    execute_refs_core(&path, &symbol, &data, &backend, json, &mut stdout)
}

pub fn execute_refs_core(
    path: &Path,
    symbol: &str,
    data: &ProjectDataV6,
    backend: &AstBackend,
    json: bool,
    writer: &mut dyn Write,
) -> Result<()> {
    let references = find_references(backend, data, symbol)?;

    let mut reference_files = HashSet::new();
    for r in &references {
        reference_files.insert(r.file.clone());
    }

    let mut files: Vec<PathBuf> = reference_files.into_iter().collect();
    files.sort();

    let response = RefsResponse {
        symbol: symbol.to_string(),
        path: path.to_path_buf(),
        references,
        files: files.clone(),
        related_paths: files,
    };

    if json {
        writeln!(writer, "{}", serde_json::to_string_pretty(&response)?)?;
    } else {
        writeln!(writer, "References for {} in {:?}", symbol, path)?;
        for r in &response.references {
            writeln!(writer, "{}:{}: {}", r.file.display(), r.line, r.text.trim())?;
        }
        writeln!(
            writer,
            "references={} files={}",
            response.references.len(),
            response.files.len()
        )?;
    }

    Ok(())
}

pub fn handle_context(path: PathBuf, query: String, json: bool) -> Result<()> {
    let config_path = find_config_file(&path)?;
    let orchestrator = AstWorkflowOrchestrator::new(config_path.as_ref().and_then(|p| p.to_str()))?;
    let data = orchestrator.load_project_data()?;
    let backend = AstBackend::new();

    let mut stdout = std::io::stdout();
    execute_context_core(&path, &query, &data, &backend, json, &mut stdout)
}

pub fn execute_context_core(
    path: &Path,
    query: &str,
    data: &ProjectDataV6,
    backend: &AstBackend,
    json: bool,
    writer: &mut dyn Write,
) -> Result<()> {
    let definitions = find_definitions(backend, data, query)?;
    let references = find_references(backend, data, query)?;

    let mut all_files = HashSet::new();
    for d in &definitions {
        all_files.insert(d.file.clone());
    }
    for r in &references {
        all_files.insert(r.file.clone());
    }

    let mut files: Vec<PathBuf> = all_files.into_iter().collect();
    files.sort();

    let response = ContextResponse {
        query: query.to_string(),
        path: path.to_path_buf(),
        definitions: definitions.clone(),
        references,
        symbols: definitions,
        files: files.clone(),
        related_paths: files,
    };

    if json {
        writeln!(writer, "{}", serde_json::to_string_pretty(&response)?)?;
    } else {
        writeln!(writer, "Context for {} in {:?}", query, path)?;
        writeln!(
            writer,
            "definitions={} references={} files={}",
            response.definitions.len(),
            response.references.len(),
            response.files.len()
        )?;
    }

    Ok(())
}

/// Honest `graph_completeness` for the editor-plane defs surface.
///
/// This was hardcoded to `"strong"`, so the session-daemon stream claimed a strong symbol
/// graph even when it found NOTHING. Agents consume this value to decide how much to trust a
/// result, which makes a fixed maximum the worst possible default.
///
/// The vocabulary is shared with the Python door (`cli/repo_map.py` uses
/// `strong` / `moderate` / `partial` / `empty`), so this maps into it rather than inventing
/// terms: no definitions -> `empty` (the same word repo_map.py uses for a nil result); one or
/// more -> `moderate`.
///
/// `moderate`, not `strong`, is deliberate. The Python defs path earns `strong` only AFTER
/// LSP-proof rows and import filtering; this path is ast-grep pattern matching over a single
/// configured language, in-file, with no LSP proof and no cross-file resolution. Reporting the
/// same word for strictly weaker evidence is the overclaim being removed here.
fn graph_completeness_for(definition_count: usize) -> &'static str {
    if definition_count == 0 {
        "empty"
    } else {
        "moderate"
    }
}

/// Reject a symbol that would change the MEANING of an ast-grep pattern instead of being
/// matched literally inside it.
///
/// `find_definitions` interpolates the caller-supplied symbol straight into ast-grep DSL
/// patterns (`format!("def {}($$$ARGS): $$$BODY", symbol)`) and `find_references` hands it
/// to the matcher AS the pattern. ast-grep treats `$NAME` / `$$$ARGS` as METAVARIABLES, so a
/// symbol carrying those sigils is not a stricter query -- it is a WIDER one. `tg defs . '$$$'`
/// would expand to `def $$$($$$ARGS): $$$BODY` and match every function in the tree while
/// still reporting `name: "$$$"`, i.e. a confidently-wrong answer rather than an error.
///
/// A symbol is an identifier, so this fails closed on anything that is not one. Unicode
/// identifiers stay allowed (`char::is_alphanumeric`), because rejecting non-ASCII names
/// would be a correctness regression for real code; only the DSL-significant and
/// whitespace/punctuation shapes are refused.
/// Combining marks (Unicode general category Mn/Mc) that are legal identifier CONTINUATION
/// characters but are NOT `char::is_alphanumeric`.
///
/// Without this, `"e\u{301}"` -- an `e` followed by COMBINING ACUTE ACCENT, which Python's own
/// `str.isidentifier()` accepts and which renders identically to a precomposed `é` -- was
/// refused as "not valid in an identifier". That was a FALSE REFUSAL shipped in v1.119.5: the
/// guard errs closed, so it never widened a pattern, but it did reject legitimate
/// non-precomposed Unicode identifiers.
///
/// SCOPE, stated rather than implied: these are the combining-diacritic blocks, not the full
/// `XID_Continue` set. Rust's std exposes no Unicode general-category API, and adding a
/// `unicode-ident` dependency is a supply-chain and lockfile change this fix does not need.
/// A `XID_Continue` character outside these blocks is still refused -- fail-closed, and a
/// narrower gap than before rather than a claim of completeness.
fn is_combining_mark(c: char) -> bool {
    matches!(c,
        '\u{0300}'..='\u{036F}'   // Combining Diacritical Marks
        | '\u{1AB0}'..='\u{1AFF}' // Combining Diacritical Marks Extended
        | '\u{1DC0}'..='\u{1DFF}' // Combining Diacritical Marks Supplement
        | '\u{20D0}'..='\u{20FF}' // Combining Diacritical Marks for Symbols
        | '\u{FE20}'..='\u{FE2F}' // Combining Half Marks
    )
}

fn validate_symbol_is_not_a_pattern(symbol: &str) -> Result<()> {
    if symbol.is_empty() {
        anyhow::bail!("symbol must not be empty");
    }
    if let Some(bad) = symbol
        .chars()
        .find(|c| !(c.is_alphanumeric() || is_combining_mark(*c) || *c == '_' || *c == '$'))
    {
        anyhow::bail!(
            "symbol {symbol:?} contains {bad:?}, which is not valid in an identifier;              refusing rather than interpolating it into an ast-grep pattern"
        );
    }
    if symbol.contains('$') {
        anyhow::bail!(
            "symbol {symbol:?} contains '$', which ast-grep reads as a metavariable sigil;              refusing rather than silently widening the pattern to match unrelated code"
        );
    }
    if symbol.chars().next().is_some_and(|c| c.is_ascii_digit()) {
        anyhow::bail!("symbol {symbol:?} starts with a digit, which is not a valid identifier");
    }
    // A combining mark is a legal identifier CONTINUATION but never a legal START -- it has
    // nothing to combine with. Without this, widening the predicate above would have newly
    // ACCEPTED a leading "\u{301}", trading a false refusal for a false acceptance.
    if symbol.chars().next().is_some_and(is_combining_mark) {
        anyhow::bail!(
            "symbol {symbol:?} starts with a combining mark, which is not a valid identifier start"
        );
    }
    Ok(())
}

fn find_definitions(
    backend: &AstBackend,
    data: &ProjectDataV6,
    symbol: &str,
) -> Result<Vec<SymbolDefinition>> {
    validate_symbol_is_not_a_pattern(symbol)?;
    let mut definitions = Vec::new();
    let lang_str = data
        .project_cfg
        .get("language")
        .and_then(|v| v.as_str())
        .unwrap_or("python");
    let language = resolve_language(lang_str)?;

    let patterns = match lang_str.to_lowercase().as_str() {
        "python" | "py" => vec![
            (format!("def {}($$$ARGS): $$$BODY", symbol), "function"),
            (format!("class {}: $$$BODY", symbol), "class"),
            (format!("class {}($$$BASE): $$$BODY", symbol), "class"),
        ],
        "javascript" | "js" | "typescript" | "ts" => vec![
            (
                format!("function {}($$$ARGS) {{ $$$BODY }}", symbol),
                "function",
            ),
            (format!("class {} {{ $$$BODY }}", symbol), "class"),
            (format!("const {} = $$$VAL", symbol), "variable"),
            (format!("let {} = $$$VAL", symbol), "variable"),
            (format!("var {} = $$$VAL", symbol), "variable"),
        ],
        "rust" | "rs" => vec![
            (format!("fn {}($$$ARGS) {{ $$$BODY }}", symbol), "function"),
            (format!("struct {} {{ $$$BODY }}", symbol), "struct"),
            (format!("enum {} {{ $$$BODY }}", symbol), "enum"),
            (format!("trait {} {{ $$$BODY }}", symbol), "trait"),
            (format!("type {} = $$$VAL", symbol), "type"),
        ],
        _ => vec![(symbol.to_string(), "definition")],
    };

    for file_path_str in &data.candidate_files {
        let file_path = Path::new(file_path_str);
        if !file_matches_language_type(file_path, language) {
            continue;
        }

        for (pattern, kind) in &patterns {
            let matches = backend.search_for_cli(pattern, lang_str, file_path_str)?;
            for file_match in matches {
                for m in file_match.matches {
                    definitions.push(SymbolDefinition {
                        name: symbol.to_string(),
                        kind: kind.to_string(),
                        file: file_path.to_path_buf(),
                        line: m.line,
                        end_line: m.line,
                    });
                }
            }
        }
    }
    Ok(definitions)
}

fn find_references(
    _backend: &AstBackend,
    data: &ProjectDataV6,
    symbol: &str,
) -> Result<Vec<SymbolReference>> {
    validate_symbol_is_not_a_pattern(symbol)?;
    let mut references = Vec::new();
    let lang_str = data
        .project_cfg
        .get("language")
        .and_then(|v| v.as_str())
        .unwrap_or("python");
    let language = resolve_language(lang_str)?;

    let pattern = symbol;

    let def_kinds: HashSet<&str> = match lang_str.to_lowercase().as_str() {
        "python" | "py" => ["function_definition", "class_definition"]
            .iter()
            .cloned()
            .collect(),
        "javascript" | "js" | "typescript" | "ts" => [
            "function_declaration",
            "class_declaration",
            "variable_declarator",
        ]
        .iter()
        .cloned()
        .collect(),
        "rust" | "rs" => [
            "function_item",
            "struct_item",
            "enum_item",
            "trait_item",
            "type_item",
        ]
        .iter()
        .cloned()
        .collect(),
        _ => HashSet::new(),
    };

    for file_path_str in &data.candidate_files {
        let file_path = Path::new(file_path_str);
        if !file_matches_language_type(file_path, language) {
            continue;
        }

        let source = fs::read_to_string(file_path)?;
        let ast = language.ast_grep(&source);
        let root = ast.root();

        for matched in root.find_all(pattern) {
            let mut is_definition = false;

            if let Some(parent) = matched.parent() {
                let kind_owned = parent.kind();
                let kind: &str = kind_owned.as_ref();
                if def_kinds.contains(kind) {
                    if let Some(name_node) = parent.field("name") {
                        if name_node.range() == matched.range() {
                            is_definition = true;
                        }
                    } else {
                        is_definition = true;
                    }
                }

                if !is_definition
                    && (kind == "import_from_statement"
                        || kind == "import_statement"
                        || kind == "aliased_import"
                        || kind == "dotted_name")
                {
                    is_definition = true;
                }
            }

            if !is_definition {
                let range = matched.range();
                let line = source[..range.start].lines().count();
                let text = source
                    .lines()
                    .nth(line.saturating_sub(1))
                    .unwrap_or("")
                    .to_string();

                references.push(SymbolReference {
                    name: symbol.to_string(),
                    kind: "reference".to_string(),
                    file: file_path.to_path_buf(),
                    line,
                    text,
                });
            }
        }
    }
    Ok(references)
}

fn file_matches_language_type(path: &Path, lang: ast_grep_language::SupportLang) -> bool {
    let extension = path.extension().and_then(|ext| ext.to_str());
    matches!(
        (lang, extension),
        (
            ast_grep_language::SupportLang::Python,
            Some("py" | "py3" | "pyi" | "bzl")
        ) | (
            ast_grep_language::SupportLang::JavaScript,
            Some("js" | "jsx" | "cjs" | "mjs")
        ) | (
            ast_grep_language::SupportLang::TypeScript,
            Some("ts" | "cts" | "mts")
        ) | (ast_grep_language::SupportLang::Rust, Some("rs"))
    )
}

fn find_config_file(path: &Path) -> Result<Option<PathBuf>> {
    let mut current = if path.is_absolute() {
        path.to_path_buf()
    } else {
        std::env::current_dir()?.join(path)
    };

    loop {
        let config = current.join("sgconfig.yml");
        if config.exists() {
            return Ok(Some(config));
        }
        let config_yaml = current.join("sgconfig.yaml");
        if config_yaml.exists() {
            return Ok(Some(config_yaml));
        }

        if let Some(parent) = current.parent() {
            current = parent.to_path_buf();
        } else {
            break;
        }
    }

    Ok(None)
}

#[cfg(test)]
mod symbol_guard_tests {
    use super::validate_symbol_is_not_a_pattern;

    #[test]
    fn metavariable_sigils_are_refused_before_pattern_interpolation() {
        // The whole point: `$$$` interpolated into `def {}($$$ARGS): $$$BODY` matches EVERY
        // function while still reporting name "$$$" -- a confidently-wrong answer.
        for hostile in ["$$$", "$NAME", "$$$ARGS", "foo$", "$"] {
            let err = validate_symbol_is_not_a_pattern(hostile)
                .expect_err("a metavariable-bearing symbol must be refused");
            let message = err.to_string();
            assert!(
                message.contains("refusing"),
                "refusal for {hostile:?} must say it refused, got: {message}"
            );
        }
    }

    #[test]
    fn pattern_punctuation_and_whitespace_are_refused() {
        for hostile in ["foo(", "foo bar", "a.b", "a::b", "*", "foo\nbar", ""] {
            assert!(
                validate_symbol_is_not_a_pattern(hostile).is_err(),
                "{hostile:?} is not an identifier and must be refused"
            );
        }
    }

    #[test]
    fn real_identifiers_still_pass() {
        // MUTATION CONTROL: if the guard rejected everything it would look "secure" while
        // breaking the product. These must keep working, including non-ASCII identifiers.
        for good in [
            "foo",
            "_private",
            "CamelCase",
            "snake_case_2",
            "handle_defs",
            "\u{e9}l\u{e9}ment",
        ] {
            validate_symbol_is_not_a_pattern(good)
                .unwrap_or_else(|e| panic!("{good:?} is a valid identifier but was refused: {e}"));
        }
    }

    #[test]
    fn a_leading_digit_is_refused() {
        assert!(validate_symbol_is_not_a_pattern("1foo").is_err());
    }
}

#[cfg(test)]
mod graph_completeness_tests {
    use super::{build_defs_response, graph_completeness_for, SymbolDefinition};
    use std::path::{Path, PathBuf};

    #[test]
    fn no_definitions_is_reported_as_empty_not_strong() {
        // The bug: this surface reported "strong" even when it found zero definitions.
        assert_eq!(graph_completeness_for(0), "empty");
    }

    #[test]
    fn found_definitions_are_moderate_not_strong() {
        // ast-grep pattern matching, in-file, single language, no LSP proof -- strictly weaker
        // than the Python defs path that earns "strong", so it must not claim the same word.
        for n in [1usize, 2, 17] {
            assert_eq!(graph_completeness_for(n), "moderate");
        }
    }

    #[test]
    fn the_value_varies_with_the_result_and_is_never_strong() {
        // MUTATION CONTROL: swapping one hardcoded constant for another would still be
        // dishonest and would pass the two tests above if they were written loosely. This
        // pins that the value actually DEPENDS on what was found, and that the overclaimed
        // word is gone from every arm.
        let values: Vec<&str> = [0usize, 1, 5]
            .iter()
            .map(|n| graph_completeness_for(*n))
            .collect();
        assert!(values.iter().all(|v| *v != "strong"), "{values:?}");
        assert!(
            values[0] != values[1],
            "value must depend on the definition count: {values:?}"
        );
    }

    #[test]
    fn the_response_itself_is_wired_to_the_derived_value() {
        // MUTATION CONTROL (independent validator finding, 2026-09-12). The three tests above
        // exercise `graph_completeness_for` DIRECTLY, so reverting the DefsResponse
        // construction to `"strong".to_string()` would have kept every one of them green --
        // they proved the helper, never that the shipped response uses it. This pins the
        // RESPONSE, which is the thing a caller actually receives.
        let empty = build_defs_response("thing", Path::new("/x"), Vec::new());
        assert_eq!(empty.graph_completeness, "empty");

        let found = build_defs_response(
            "thing",
            Path::new("/x"),
            vec![SymbolDefinition {
                name: "thing".to_string(),
                kind: "function".to_string(),
                file: PathBuf::from("/x/a.py"),
                line: 1,
                end_line: 2,
            }],
        );
        assert_eq!(found.graph_completeness, "moderate");
        assert_ne!(
            empty.graph_completeness, found.graph_completeness,
            "the response's value must track the definition count, not a constant"
        );
    }
}

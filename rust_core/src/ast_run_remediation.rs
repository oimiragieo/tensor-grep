//! Static, zero-match guidance and Python return-type advisory for the native `tg run` route.
//!
//! Keep this catalog scoped to `tg run`: a clean AST scan with no findings is not a failed search.

use std::collections::{HashMap, HashSet};
use std::fs;
use std::io::Read;
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

use ignore::WalkBuilder;
use tensor_grep_rs::backend_ast::AstCliFileMatches;

const IDIOMS: &str = "def $NAME($$$ARGS): $$$BODY | def $NAME($$$ARGS) -> $RET: $$$BODY | function $NAME($$$) { $$$ }";
const PYTHON_RETURN_TYPE_ADVISORY_HINT: &str = "Typed Python functions (`def ... -> ...:`) were found that did not match this pattern; pass `--selector function_definition` or use `def $NAME($$$ARGS) -> $RET: $$$BODY` to match return-annotated definitions.";
const MAX_ADVISORY_FILES: usize = 64;
const MAX_ADVISORY_DURATION: Duration = Duration::from_millis(250);
const MAX_ADVISORY_FILE_BYTES: u64 = 256 * 1024;

fn hints(pattern: &str, lang_was_implicit: bool) -> Vec<String> {
    let mut lines = vec![
        format!("No AST matches found. Common idiom shapes: {IDIOMS}"),
        "Run `tg ast-info` to list supported LANGUAGES.".to_string(),
        "For Python functions with return annotations, try `def $NAME($$$ARGS) -> $RET: $$$BODY` or pass `--selector function_definition` to match any function definition.".to_string(),
    ];
    if !pattern.contains('$') {
        lines.push(
            "The pattern has no metavariable ($) -- add one like $NAME to match structurally varying code."
                .to_string(),
        );
    }
    if lang_was_implicit {
        lines.push(
            "No --lang was passed -- pass --lang <language> to parse against the right grammar."
                .to_string(),
        );
    }
    lines
}

/// Detect whether the current `tg run` invocation supplied `--lang`, including `--lang=value`.
pub fn lang_was_implicit() -> bool {
    for arg in std::env::args_os().skip(1) {
        if arg == "--" {
            break;
        }
        if arg == "--lang" {
            return false;
        }
        if arg.to_string_lossy().starts_with("--lang=") {
            return false;
        }
    }
    true
}

pub fn json_value(pattern: &str, lang_was_implicit: bool) -> serde_json::Value {
    serde_json::json!({"hints": hints(pattern, lang_was_implicit)})
}

pub fn json_for_search(
    pattern: &str,
    match_count: usize,
    routing_reason: &str,
) -> Option<serde_json::Value> {
    (match_count == 0 && routing_reason == "ast-native")
        .then(|| json_value(pattern, lang_was_implicit()))
}

pub fn emit_text_for_search(pattern: &str, match_count: usize) {
    if match_count == 0 {
        eprintln!("{}", hints(pattern, lang_was_implicit()).join("\n"));
    }
}

pub fn warn_windows_single_quote_ast_pattern(pattern: &str) {
    if !cfg!(windows) {
        return;
    }
    let trimmed = pattern.trim();
    if trimmed.len() >= 2 && trimmed.starts_with('\'') && trimmed.ends_with('\'') {
        eprintln!(
            "No AST matches found. cmd.exe treats single quotes literally; use double quotes in cmd.exe or run this pattern from PowerShell/Git Bash where single quotes quote literal text."
        );
    }
}

fn contains_word_def(pattern: &str) -> bool {
    let bytes = pattern.as_bytes();
    let mut i = 0;
    while let Some(offset) = pattern[i..].find("def") {
        let start = i + offset;
        let end = start + 3;
        let left_ok =
            start == 0 || !(bytes[start - 1].is_ascii_alphanumeric() || bytes[start - 1] == b'_');
        let right_ok =
            end == bytes.len() || !(bytes[end].is_ascii_alphanumeric() || bytes[end] == b'_');
        if left_ok && right_ok {
            return true;
        }
        i = start + 1;
    }
    false
}

fn extract_python_def_target_name(pattern: &str) -> Option<&str> {
    let bytes = pattern.as_bytes();
    let mut i = 0;
    while let Some(offset) = pattern[i..].find("def") {
        let start = i + offset;
        let end = start + 3;
        let left_ok =
            start == 0 || !(bytes[start - 1].is_ascii_alphanumeric() || bytes[start - 1] == b'_');
        let right_ws = end < bytes.len() && bytes[end].is_ascii_whitespace();
        if left_ok && right_ws {
            let mut cursor = end;
            while cursor < bytes.len() && bytes[cursor].is_ascii_whitespace() {
                cursor += 1;
            }
            if cursor < bytes.len() && bytes[cursor] == b'$' {
                return None;
            }
            if cursor < bytes.len()
                && (bytes[cursor].is_ascii_alphabetic() || bytes[cursor] == b'_')
            {
                let name_start = cursor;
                cursor += 1;
                while cursor < bytes.len()
                    && (bytes[cursor].is_ascii_alphanumeric() || bytes[cursor] == b'_')
                {
                    cursor += 1;
                }
                return Some(&pattern[name_start..cursor]);
            }
            return None;
        }
        i = start + 1;
    }
    None
}

fn normalize_path(path: &Path) -> PathBuf {
    fs::canonicalize(path).unwrap_or_else(|_| path.to_path_buf())
}

fn is_python_path(path: &Path) -> bool {
    path.extension()
        .and_then(|ext| ext.to_str())
        .is_some_and(|ext| ext.eq_ignore_ascii_case("py") || ext.eq_ignore_ascii_case("pyi"))
}

fn source_has_unmatched_typed_def(
    parser: &mut tree_sitter::Parser,
    source: &str,
    matched_lines: Option<&HashSet<usize>>,
    target_name: Option<&str>,
) -> bool {
    let Some(tree) = parser.parse(source, None) else {
        return false;
    };
    let mut stack = vec![tree.root_node()];
    while let Some(node) = stack.pop() {
        if node.kind() == "function_definition" && node.child_by_field_name("return_type").is_some()
        {
            let name_matches = match target_name {
                Some(expected) => node
                    .child_by_field_name("name")
                    .and_then(|name_node| name_node.utf8_text(source.as_bytes()).ok())
                    .is_some_and(|actual| actual == expected),
                None => true,
            };
            let line_1based = node.start_position().row + 1;
            let already_matched = matched_lines.is_some_and(|lines| lines.contains(&line_1based));
            if name_matches && !already_matched {
                return true;
            }
        }
        let mut cursor = node.walk();
        for child in node.children(&mut cursor) {
            stack.push(child);
        }
    }
    false
}

fn collect_python_return_type_advisory_hints<'a>(
    lang: &str,
    selector: Option<&str>,
    pattern: &str,
    search_path: &str,
    matched_locations: impl Iterator<Item = (&'a Path, usize)>,
) -> Vec<String> {
    let deadline = Instant::now() + MAX_ADVISORY_DURATION;
    let lang_norm = lang.trim().to_ascii_lowercase();
    if selector == Some("function_definition")
        || (lang_norm != "py" && lang_norm != "python")
        || pattern.contains("->")
        || !contains_word_def(pattern)
    {
        return Vec::new();
    }

    let target_name = extract_python_def_target_name(pattern);
    let mut matched_lines_by_file: HashMap<PathBuf, HashSet<usize>> = HashMap::new();
    let mut matched_files: Vec<PathBuf> = Vec::new();
    for (file, line) in matched_locations {
        if Instant::now() >= deadline {
            return Vec::new();
        }
        let norm = normalize_path(file);
        if !matched_lines_by_file.contains_key(&norm) {
            matched_files.push(file.to_path_buf());
        }
        matched_lines_by_file.entry(norm).or_default().insert(line);
    }

    let mut parser = tree_sitter::Parser::new();
    if parser
        .set_language(&tree_sitter_python::LANGUAGE.into())
        .is_err()
    {
        return Vec::new();
    }

    let root = Path::new(search_path);
    let explicit_file = root.is_file();
    let mut seen: HashSet<PathBuf> = HashSet::new();
    let mut inspected = 0usize;

    let mut inspect_file = |file_path: &Path| -> Option<bool> {
        if inspected >= MAX_ADVISORY_FILES || Instant::now() >= deadline {
            return None;
        }
        if !explicit_file && !is_python_path(file_path) {
            return Some(false);
        }
        let norm = normalize_path(file_path);
        if !seen.insert(norm.clone()) {
            return Some(false);
        }
        inspected += 1;
        let Ok(file) = fs::File::open(file_path) else {
            return Some(false);
        };
        let mut bytes = Vec::new();
        if Read::take(file, MAX_ADVISORY_FILE_BYTES + 1)
            .read_to_end(&mut bytes)
            .is_err()
        {
            return Some(false);
        }
        if bytes.len() > MAX_ADVISORY_FILE_BYTES as usize {
            return Some(false);
        }
        let Ok(source) = String::from_utf8(bytes) else {
            return Some(false);
        };
        Some(source_has_unmatched_typed_def(
            &mut parser,
            &source,
            matched_lines_by_file.get(&norm),
            target_name,
        ))
    };

    for file_path in matched_files {
        match inspect_file(&file_path) {
            Some(true) => return vec![PYTHON_RETURN_TYPE_ADVISORY_HINT.to_string()],
            Some(false) => {}
            None => return Vec::new(),
        }
    }

    if explicit_file {
        if let Some(true) = inspect_file(root) {
            return vec![PYTHON_RETURN_TYPE_ADVISORY_HINT.to_string()];
        }
        return Vec::new();
    }

    if root.is_dir() {
        for entry in WalkBuilder::new(root).build() {
            if Instant::now() >= deadline {
                break;
            }
            let Ok(entry) = entry else {
                continue;
            };
            if entry.file_type().is_some_and(|ft| ft.is_file()) {
                match inspect_file(entry.path()) {
                    Some(true) => return vec![PYTHON_RETURN_TYPE_ADVISORY_HINT.to_string()],
                    Some(false) => {}
                    None => break,
                }
            }
        }
    }
    Vec::new()
}

pub fn python_return_type_advisory_for_json(
    ast_lang: Option<&str>,
    selector: Option<&str>,
    pattern: &str,
    search_path: &str,
    matches: &[super::SearchMatchJson],
) -> Vec<String> {
    let Some(lang) = ast_lang else {
        return Vec::new();
    };
    collect_python_return_type_advisory_hints(
        lang,
        selector,
        pattern,
        search_path,
        matches.iter().map(|m| (Path::new(&m.file), m.line)),
    )
}

pub fn emit_python_return_type_advisory_for_cli(
    lang: &str,
    selector: Option<&str>,
    pattern: &str,
    search_path: &str,
    matches: &[AstCliFileMatches],
) {
    let hints = collect_python_return_type_advisory_hints(
        lang,
        selector,
        pattern,
        search_path,
        matches
            .iter()
            .flat_map(|fm| fm.matches.iter().map(move |m| (fm.file.as_path(), m.line))),
    );
    for hint in hints {
        eprintln!("advisory_hints: {hint}");
    }
}

pub fn emit_cli_run_advisories(
    lang: &str,
    selector: Option<&str>,
    pattern: &str,
    search_path: &str,
    matches: &[AstCliFileMatches],
    match_count: usize,
) {
    if match_count == 0 {
        emit_text_for_search(pattern, match_count);
    }
    emit_python_return_type_advisory_for_cli(lang, selector, pattern, search_path, matches);
    if match_count == 0 {
        warn_windows_single_quote_ast_pattern(pattern);
    }
}

#[cfg(test)]
mod tests {
    use std::fs;
    use tempfile::tempdir;

    use super::{
        collect_python_return_type_advisory_hints, extract_python_def_target_name, hints,
        MAX_ADVISORY_FILE_BYTES,
    };

    #[test]
    fn hints_include_python_return_typed_function_and_selector() {
        let lines = hints("def $NAME($$$ARGS): $$$BODY", false);
        let joined = lines.join("\n");
        assert!(joined.contains("def $NAME($$$ARGS) -> $RET: $$$BODY"));
        assert!(joined.contains("--selector function_definition"));
    }

    #[test]
    fn extract_python_def_target_name_distinguishes_literal_and_metavariable() {
        assert_eq!(
            extract_python_def_target_name("def $NAME($$$ARGS): $$$BODY"),
            None
        );
        assert_eq!(
            extract_python_def_target_name("async def foo(x): return x"),
            Some("foo")
        );
        assert_eq!(extract_python_def_target_name("def foo("), Some("foo"));
    }

    #[test]
    fn selector_function_definition_suppresses_python_return_type_advisory() {
        let dir = tempdir().unwrap();
        let file = dir.path().join("typed.py");
        fs::write(&file, "def typed(x: int) -> int:\n    return x\n").unwrap();

        let unsuppressed = collect_python_return_type_advisory_hints(
            "python",
            None,
            "def $NAME($$$ARGS): $$$BODY",
            file.to_str().unwrap(),
            std::iter::empty(),
        );
        assert_eq!(unsuppressed.len(), 1);

        let suppressed = collect_python_return_type_advisory_hints(
            "python",
            Some("function_definition"),
            "def $NAME($$$ARGS): $$$BODY",
            file.to_str().unwrap(),
            std::iter::empty(),
        );
        assert!(suppressed.is_empty());
    }

    #[test]
    fn oversized_python_files_are_skipped_cleanly() {
        let dir = tempdir().unwrap();
        let huge = dir.path().join("huge.py");
        let padding = "# ".to_string() + &"a".repeat(MAX_ADVISORY_FILE_BYTES as usize + 64);
        fs::write(
            &huge,
            format!("{padding}\ndef typed(x: int) -> int:\n    return x\n"),
        )
        .unwrap();

        let skipped = collect_python_return_type_advisory_hints(
            "python",
            None,
            "def $NAME($$$ARGS): $$$BODY",
            huge.to_str().unwrap(),
            std::iter::empty(),
        );
        assert!(skipped.is_empty());
    }
}

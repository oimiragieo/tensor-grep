//! Static, zero-match guidance for the native `tg run` route.
//!
//! Keep this catalog scoped to `tg run`: a clean AST scan with no findings is not a failed search.

const IDIOMS: &str = "def $NAME($$$ARGS): $$$BODY | def $NAME($$$ARGS) -> $RET: $$$BODY | function $NAME($$$) { $$$ }";

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

#[cfg(test)]
mod tests {
    use super::hints;

    #[test]
    fn hints_include_python_return_typed_function_and_selector() {
        let lines = hints("def $NAME($$$ARGS): $$$BODY", false);
        let joined = lines.join("\n");
        assert!(joined.contains("def $NAME($$$ARGS) -> $RET: $$$BODY"));
        assert!(joined.contains("--selector function_definition"));
    }
}

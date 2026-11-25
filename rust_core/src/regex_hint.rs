//! Bounded diagnostics for code accidentally supplied as a regular expression.

use regex_syntax::ast::{parse::Parser, ErrorKind};

pub const LITERAL_PATTERN_HINT: &str =
    "Hint: If you intended literal code, retry with --fixed-strings (-F).";
const MAX_HINT_PATTERN_BYTES: usize = 16 * 1024;

fn has_identifier_before(pattern: &str, offset: usize) -> bool {
    let Some(prefix) = pattern.as_bytes().get(..offset) else {
        return false;
    };
    let mut i = prefix.len();
    while i > 0 && (prefix[i - 1].is_ascii_alphanumeric() || prefix[i - 1] == b'_') {
        i -= 1;
    }
    i < prefix.len() && (prefix[i].is_ascii_alphabetic() || prefix[i] == b'_')
}

/// Inspect syntax only, after the real engine has failed. Never change the pattern
/// or treat a search error as a successful literal search.
pub fn literal_pattern_hint(pattern: &str) -> Option<&'static str> {
    if pattern.len() > MAX_HINT_PATTERN_BYTES {
        return None;
    }
    let error = Parser::new().parse(pattern).err()?;
    let delimiter_error = match error.kind() {
        ErrorKind::ClassUnclosed
        | ErrorKind::RepetitionCountDecimalEmpty
        | ErrorKind::RepetitionCountUnclosed => true,
        ErrorKind::RepetitionMissing => {
            pattern.as_bytes().get(error.span().start.offset) == Some(&b'{')
        }
        ErrorKind::GroupUnclosed => has_identifier_before(pattern, error.span().start.offset),
        _ => false,
    };
    delimiter_error.then_some(LITERAL_PATTERN_HINT)
}

pub fn native_pattern_error_detail(pattern: &str, fixed_strings: bool) -> String {
    let detail = format!("failed to compile native search pattern '{pattern}'");
    match literal_pattern_hint(pattern) {
        Some(hint) if !fixed_strings => format!("{detail}\n{hint}"),
        _ => detail,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn delimiter_errors_get_a_conditional_literal_hint() {
        for pattern in [
            "KNOWN_COMMANDS = {",
            "items[",
            "x{2",
            "[",
            "{",
            "def foo(bar",
            "compute_tax(",
        ] {
            assert_eq!(
                literal_pattern_hint(pattern),
                Some(LITERAL_PATTERN_HINT),
                "{pattern}"
            );
        }
    }

    #[test]
    fn valid_or_unrelated_syntax_never_gets_a_literal_hint() {
        for pattern in [
            r"x\{",
            r"items\[",
            "x{2}",
            "[abc]",
            "[[:alpha:]]",
            "[a&&[b]]",
            "(?x)x # {",
            r"\p{Letter}",
            "(",
            "*",
            "(?=x)",
        ] {
            assert_eq!(literal_pattern_hint(pattern), None, "{pattern}");
        }
        assert_eq!(
            literal_pattern_hint(&"[".repeat(MAX_HINT_PATTERN_BYTES + 1)),
            None
        );
    }
}

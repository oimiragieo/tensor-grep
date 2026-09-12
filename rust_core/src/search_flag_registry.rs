use std::ffi::OsString;

pub(crate) const SEARCH_OPTION_FIRST_FLAGS: &[&str] = &[
    "--count-matches",
    "--format",
    "--sort",
    "--sortr",
    "--sort-files",
    "--no-sort-files",
    "--glob",
    "-g",
    "--files",
    "-l",
    "--files-with-matches",
    "-U",
    "--multiline",
    "--hidden",
    "-0",
    "--null",
    "-d",
    "--max-depth",
    "-S",
    "--smart-case",
    "-H",
    "--with-filename",
    "-I",
    "--no-filename",
    "-q",
    "--quiet",
    "-n",
    "--line-number",
    "--engine",
    "-s",
    "--case-sensitive",
    "-x",
    "--line-regexp",
    "-j",
    "--threads",
    "-t",
    "--type",
    "--iglob",
    "-T",
    "--type-not",
    "-u",
    "--unrestricted",
    "--stats",
    "--debug",
    "--trace",
    "--pcre2-unicode",
    "--no-pcre2-unicode",
    "--no-auto-hybrid-regex",
    "--no-text",
    "--no-binary",
    "--no-follow",
    "--no-glob-case-insensitive",
    "--no-ignore-file-case-insensitive",
    "--ignore",
    "--no-ignore",
    "--ignore-dot",
    "--ignore-exclude",
    "--ignore-files",
    "--ignore-global",
    "--ignore-messages",
    "--ignore-parent",
    "--ignore-vcs",
    "--no-ignore-vcs",
    "--messages",
    "--require-git",
    "-C",
    "--context",
    "-A",
    "--after-context",
    "-B",
    "--before-context",
    "--no-hidden",
    "--no-one-file-system",
    "--no-block-buffered",
    "--no-byte-offset",
    "--no-column",
    "--no-crlf",
    "--no-encoding",
    "--no-fixed-strings",
    "--no-invert-match",
    "--no-mmap",
    "--no-multiline",
    "--no-multiline-dotall",
    "--no-pcre2",
    "--no-pre",
    "--no-search-zip",
    "--no-context-separator",
    "--no-include-zero",
    "--no-line-buffered",
    "--no-max-columns-preview",
    "--no-trim",
    "--no-json",
    "--no-stats",
];

/// Flags that route a search to the Python passthrough front door rather than being handled by
/// the native fast path. Exact tokens and attached values for listed short flags are accepted;
/// unrecognized flags are caught fail-closed by `parse_early_ripgrep_args`'s catch-all arm
/// returning `None`.
pub(crate) const SEARCH_PYTHON_PASSTHROUGH_FLAGS: &[&str] = &[
    "-H",
    "--with-filename",
    "-I",
    "--no-filename",
    "-q",
    "--quiet",
    "-N",
    "--no-line-number",
    "--engine",
    "-s",
    "--case-sensitive",
    "-x",
    "--line-regexp",
    "-j",
    "--threads",
    "--iglob",
    "-T",
    "--type-not",
    "-u",
    "--unrestricted",
    "--stats",
    "--debug",
    "--trace",
    "-f",
    "--file",
    "--pre",
    "--pre-glob",
    "-z",
    "--search-zip",
    "--crlf",
    "--dfa-size-limit",
    "-E",
    "--encoding",
    "--mmap",
    "--no-unicode",
    "--regex-size-limit",
    "--stop-on-nonmatch",
    "--binary",
    "--glob-case-insensitive",
    "--ignore-file",
    "--ignore-file-case-insensitive",
    "--no-ignore-file-case-insensitive",
    "--no-require-git",
    "--pcre2-unicode",
    "--no-pcre2-unicode",
    "--no-auto-hybrid-regex",
    "--no-text",
    "--no-binary",
    "--no-follow",
    "--no-glob-case-insensitive",
    "--ignore",
    "--ignore-dot",
    "--ignore-exclude",
    "--ignore-files",
    "--ignore-global",
    "--ignore-messages",
    "--ignore-parent",
    "--ignore-vcs",
    "--messages",
    "--require-git",
    "--no-hidden",
    "--one-file-system",
    "--no-one-file-system",
    "--type-add",
    "--type-clear",
    "--block-buffered",
    "--no-block-buffered",
    "-b",
    "--byte-offset",
    "--no-byte-offset",
    "--no-crlf",
    "--no-encoding",
    "--no-fixed-strings",
    "--no-invert-match",
    "--no-mmap",
    "--no-multiline",
    "--no-multiline-dotall",
    "--no-pcre2",
    "--no-pre",
    "--no-search-zip",
    "--colors",
    "--context-separator",
    "--no-context-separator",
    "--field-context-separator",
    "--field-match-separator",
    "--heading",
    "--no-heading",
    "--hostname-bin",
    "--hyperlink-format",
    "--include-zero",
    "--no-include-zero",
    "--line-buffered",
    "--no-line-buffered",
    "-M",
    "--max-columns",
    "--max-columns-preview",
    "--no-max-columns-preview",
    "-p",
    "--pretty",
    "--trim",
    "--no-trim",
    "--no-json",
    "--no-stats",
    "--no-ignore-messages",
    "--no-messages",
    "--generate",
    "--lang",
    // BM25 re-ranking is a Python-side post-process; route --rank/--bm25 searches to the sidecar
    // so the native front door does not clap-reject the unknown flag.
    "--rank",
    "--bm25",
    // Local hybrid semantic search (RRF fusion of BM25 + dense embeddings) is also a Python-side
    // post-process (roadmap #27, Path B Stage 1) -- same reasoning as --rank/--bm25 above.
    "--semantic",
    // --ltl is a Python-side temporal-query post-process (CPUBackend::_search_ltl); route it
    // to the sidecar so the native front door does not clap-reject the unknown flag. Paired
    // with bootstrap.py::_TG_ONLY_SEARCH_FLAGS (the 2-front-door law).
    "--ltl",
];

pub(crate) fn raw_args_contain_any_flag(raw_args: &[OsString], flags: &[&str]) -> bool {
    raw_args.iter().skip(1).any(|arg| {
        let token = arg.to_string_lossy();
        token_matches_any_flag(&token, flags)
    })
}

pub(crate) fn search_args_contain_any_flag(args: &[String], flags: &[&str]) -> bool {
    args.iter()
        .any(|token| token_matches_any_flag(token.as_str(), flags))
}

pub(crate) fn token_matches_any_flag(token: &str, flags: &[&str]) -> bool {
    flags.iter().any(|flag| {
        token == *flag
            || (flag.starts_with("--") && token.starts_with(&format!("{flag}=")))
            || (flag.len() == 2 && flag.starts_with('-') && token.starts_with(flag))
                && token.len() > flag.len()
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{normalize_top_level_search_args, parse_early_ripgrep_args};
    fn normalized_root_search(raw: &[&str]) -> Option<Vec<String>> {
        let raw_args = raw.iter().map(OsString::from).collect::<Vec<_>>();
        normalize_top_level_search_args(&raw_args).map(|argv| {
            argv.iter()
                .map(|a| a.to_string_lossy().to_string())
                .collect()
        })
    }

    // ---------------------------------------------------------------------------
    // C1/C2 RED (2026-09-12, dogfooded on the SHIPPED binary `tg 1.119.4`):
    //   tg needle --glob '*.py' src   -> error: unexpected argument '--glob' found
    //   tg search needle --glob '*.py' src -> works
    // The root (option-first) door refuses rg-style flags the explicit `search` form accepts,
    // because `SEARCH_OPTION_FIRST_FLAGS` carries the NEGATIVE inverses (`--no-hidden`,
    // `--no-multiline`, `--no-glob-case-insensitive`, ...) but not these POSITIVE forms, and
    // `token_matches_any_flag` matches only an EXACT token or a `--flag=value` form -- never an
    // attached short value (`-tpy`, `-C2`, `-g*.py`), which the Python bootstrap door DOES
    // handle. The two front doors must agree on this flag set.
    //
    // DESIGN CONSTRAINT (bug #88 walk-DoS): the fix must REWRITE
    // `tg <pat> --glob X <path>` into the `tg search <pat> --glob X <path>` form -- it must NOT
    // promote a walk-scope filter onto an rg/native fast path that skips the bare-no-PATH
    // implicit-walk guard. Both halves are pinned below.
    // ---------------------------------------------------------------------------

    #[test]
    fn normalize_top_level_search_args_rewrites_every_missing_rg_style_root_flag() {
        // Each (flag, value) pair: the value is None for boolean flags. Every one of these
        // spellings works after the explicit `search` subcommand today and must therefore be
        // RECOGNIZED by the root door (rewritten into the `tg search` form) too.
        for (flag, value) in [
            ("--glob", Some("*.py")),
            ("-g", Some("*.py")),
            ("--files", None),
            ("-l", None),
            ("--files-with-matches", None),
            ("-U", None),
            ("--multiline", None),
            ("--hidden", None),
            ("-0", None),
            ("--null", None),
            ("-d", Some("3")),
            ("--max-depth", Some("3")),
            ("-S", None),
            ("--smart-case", None),
        ] {
            let mut raw = vec!["tg", "needle", flag];
            if let Some(value) = value {
                raw.push(value);
            }
            raw.push("src");

            let normalized = normalized_root_search(&raw).unwrap_or_else(|| {
                panic!(
                    "root door must recognize {flag} as a search (rewrite to `tg search`), \
                     got None (clap will reject the argv with 'unexpected argument')"
                )
            });
            assert_eq!(
                normalized.get(0..2).map(|s| s.join(" ")),
                Some("tg search".to_string()),
                "argv must normalize to the explicit `tg search` subcommand form for {flag}"
            );
            // The rewrite is a SHAPE change, not a value change: the pattern, the flag (and
            // any value), and the PATH must survive byte-for-byte.
            let expected_tail: Vec<String> = raw[1..].iter().map(|s| s.to_string()).collect();
            assert_eq!(
                &normalized[2..],
                &expected_tail[..],
                "rewrite must preserve the original args for {flag}"
            );
        }
    }

    #[test]
    fn token_matches_any_flag_accepts_attached_short_values() {
        // C2: the Python bootstrap door walks bundled/attached short-flag values
        // (`-tpy` == `-t py`, `-g*.py` == `-g *.py`, `-C2` == `-C 2`); the native door's
        // matcher treated any attached form as an unknown token, so the root door rejected it.
        // `-C` is ALREADY listed in SEARCH_OPTION_FIRST_FLAGS -- this pins the MATCHER defect
        // (attached values), not a missing list entry.
        assert!(
            token_matches_any_flag("-tpy", SEARCH_OPTION_FIRST_FLAGS),
            "-tpy must match listed flag -t (attached short value)"
        );
        assert!(
            token_matches_any_flag("-C2", SEARCH_OPTION_FIRST_FLAGS),
            "-C2 must match listed flag -C (attached short value)"
        );
        assert!(
            token_matches_any_flag("-g*.py", SEARCH_OPTION_FIRST_FLAGS),
            "-g*.py must match listed flag -g (attached short value)"
        );
    }

    #[test]
    fn normalize_top_level_search_args_rewrites_attached_short_value_forms() {
        for raw in [
            vec!["tg", "needle", "-tpy", "src"],
            vec!["tg", "needle", "-C2", "src"],
            vec!["tg", "needle", "-g*.py", "src"],
        ] {
            let normalized = normalized_root_search(&raw).unwrap_or_else(|| {
                panic!(
                    "attached short-value form must rewrite to the `tg search` form: {raw:?} \
                     (root door currently rejects it with 'unexpected argument')"
                )
            });
            assert_eq!(
                normalized.get(0..2).map(|s| s.join(" ")),
                Some("tg search".to_string()),
                "attached short-value form must normalize to the search form: {raw:?}"
            );
        }
    }

    #[test]
    fn normalize_top_level_search_args_returns_none_for_genuinely_unknown_flag() {
        // MUTATION CONTROL (A61): the rewrite tests above are only meaningful if the
        // assertion can fail. A genuinely unknown flag must STILL return None (clap then
        // rejects it fail-closed), and a token that merely SHARES a prefix with a known flag
        // (`--globby` vs `--glob`) must not be rewritten either -- the matcher is exact-token
        // or `--flag=value`, never a bare prefix scan.
        assert!(normalized_root_search(&["tg", "needle", "--zzz-not-a-flag", "src"]).is_none());
        assert!(normalized_root_search(&["tg", "needle", "--zzz-not-a-flag=x", "src"]).is_none());
        assert!(normalized_root_search(&["tg", "needle", "--globby", "src"]).is_none());
    }

    #[test]
    fn walk_scope_root_flag_rewrite_preserves_implicit_path_walk_guard_trigger() {
        // Half 2 of the C1 contract (the bug #88 walk-DoS must stay closed): the root door
        // fix must land walk-scope filters on the GUARDED search route, never on a fast path.
        //
        // Bare (no-PATH) form: after the rewrite, the fast-path parser must still REFUSE it
        // (it requires >= 2 positionals: pattern + path), so the invocation falls through to
        // the guarded search route where `implicit_search_walk_exceeds_ceiling` fires.
        // Explicit-PATH form: the rewrite parses, and `path_was_implicit` is false, so the
        // walk ceiling is not needed there. If a "fix" instead promoted `--glob` onto an
        // unguarded fast path, the bare arm would start parsing -> this test REDs.
        let bare = ["tg", "needle", "--glob", "*.py"]
            .iter()
            .map(OsString::from)
            .collect::<Vec<_>>();
        let normalized_bare = normalize_top_level_search_args(&bare)
            .expect("a walk-scope root form must still rewrite to the `tg search` form");
        assert!(
            parse_early_ripgrep_args(&normalized_bare).is_none(),
            "a bare (no-PATH) --glob search must NOT be fast-path parseable -- it must land on \
             the guarded search route where the implicit-walk ceiling fires"
        );

        let explicit = ["tg", "needle", "--glob", "*.py", "src"]
            .iter()
            .map(OsString::from)
            .collect::<Vec<_>>();
        let normalized_explicit = normalize_top_level_search_args(&explicit)
            .expect("a walk-scope root form with an explicit PATH must rewrite");
        let parsed = parse_early_ripgrep_args(&normalized_explicit)
            .expect("explicit-PATH walk-scope form should parse on the fast-path parser");
        assert!(
            !parsed.path_was_implicit,
            "an explicit trailing PATH must record path_was_implicit = false"
        );
    }
}

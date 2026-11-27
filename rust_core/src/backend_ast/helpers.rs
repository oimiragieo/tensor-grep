#![allow(unused_imports)]
use super::*;

pub(super) fn build_match<'tree>(
    file: &Path,
    source: &str,
    line_starts: &[usize],
    matched: NodeMatch<'tree, ast_grep_core::tree_sitter::StrDoc<SupportLang>>,
) -> AstMatch {
    let byte_range = matched.range();
    let matched_text = matched.text().to_string();
    let file_path = file.to_path_buf();
    let meta_variables = extract_structured_meta_variables(matched.get_env());
    let candidate = EditCandidate {
        file: file_path.clone(),
        byte_range: byte_range.clone(),
        metavar_env: extract_metavar_env(source, matched.get_env()),
    };

    AstMatch {
        file: file_path,
        line: line_number_for_byte(line_starts, byte_range.start),
        matched_text,
        candidate,
        meta_variables,
    }
}

pub(super) fn compile_ast_pattern(
    pattern: &str,
    language: SupportLang,
) -> Result<CompiledAstPattern> {
    let compiled_pattern = match Pattern::try_new(pattern, language) {
        Ok(compiled_pattern) if !compiled_pattern.has_error() => compiled_pattern,
        Ok(compiled_pattern) => {
            build_js_ts_method_contextual_pattern(pattern, language).unwrap_or(compiled_pattern)
        }
        Err(err) => build_js_ts_method_contextual_pattern(pattern, language)
            .ok_or_else(|| anyhow::anyhow!("Invalid pattern: {err}"))?,
    };
    let fallback = build_rust_function_signature_fallback(pattern, language);
    if compiled_pattern.has_error() && fallback.is_none() {
        anyhow::bail!("Invalid pattern: parse error");
    }
    Ok(CompiledAstPattern {
        pattern: compiled_pattern,
        rust_function_signature_fallback: fallback,
    })
}

pub(super) fn build_js_ts_method_contextual_pattern(
    pattern: &str,
    language: SupportLang,
) -> Option<Pattern> {
    if !matches!(language, SupportLang::JavaScript | SupportLang::TypeScript) {
        return None;
    }

    let trimmed = pattern.trim();
    if trimmed.is_empty()
        || trimmed.starts_with("class ")
        || trimmed.starts_with("function ")
        || !trimmed.contains('(')
        || !trimmed.contains('{')
        || !trimmed.contains('}')
    {
        return None;
    }

    let contextual_source = format!("class __TgContext {{\n  {trimmed}\n}}");
    match Pattern::contextual(&contextual_source, "method_definition", language) {
        Ok(contextual_pattern) if !contextual_pattern.has_error() => Some(contextual_pattern),
        _ => None,
    }
}

pub(super) fn build_rust_function_signature_fallback(
    pattern: &str,
    language: SupportLang,
) -> Option<RustFunctionSignatureFallback> {
    if language != SupportLang::Rust {
        return None;
    }

    let trimmed = pattern.trim();
    if trimmed.contains('{')
        || trimmed.contains('}')
        || trimmed.contains("->")
        || trimmed.contains("where")
        || trimmed.contains('<')
        || trimmed.contains(';')
    {
        return None;
    }

    let (visibility, remainder) = if let Some(rest) = trimmed.strip_prefix("pub ") {
        (Some(String::from("pub")), rest.trim_start())
    } else {
        (None, trimmed)
    };

    let remainder = remainder.strip_prefix("fn ")?;
    let remainder = remainder.trim_end();
    let open_paren = remainder.find('(')?;
    if !remainder.ends_with(')') {
        return None;
    }

    let name = remainder[..open_paren].trim();
    if name.is_empty() {
        return None;
    }
    let params = &remainder[open_paren + 1..remainder.len() - 1];

    let name_pattern = match Pattern::try_new(name, language) {
        Ok(p) => p,
        Err(_) => return None,
    };
    let raw_params = params.trim().to_string();
    let parameters_pattern = if raw_params == "$$$ARGS" || raw_params.is_empty() {
        None
    } else {
        match Pattern::contextual(
            &format!("fn placeholder({params}) {{}}"),
            "parameters",
            language,
        ) {
            Ok(p) => Some(p),
            Err(_) => return None,
        }
    };

    Some(RustFunctionSignatureFallback {
        visibility,
        name_pattern,
        parameters_pattern,
        raw_params,
    })
}

pub(super) fn merge_metavar_env(
    target: &mut HashMap<String, String>,
    additions: HashMap<String, String>,
) {
    for (name, value) in additions {
        target.insert(name, value);
    }
}

pub(super) fn collect_rust_function_signature_matches(
    fallback: &RustFunctionSignatureFallback,
    root: Node<'_, ast_grep_core::tree_sitter::StrDoc<SupportLang>>,
    file: &Path,
    source: &str,
    line_starts: &[usize],
) -> Vec<AstMatch> {
    let file_path = file.to_path_buf();
    let mut matches = Vec::new();

    for node in root.dfs() {
        if node.kind().as_ref() != "function_item" {
            continue;
        }

        if let Some(expected_visibility) = fallback.visibility.as_deref() {
            let Some(visibility_node) = node.field("visibility_modifier") else {
                continue;
            };
            if visibility_node.text() != expected_visibility {
                continue;
            }
        }

        let Some(name_node) = node.field("name") else {
            continue;
        };
        let Some(name_match) = name_node.find(fallback.name_pattern.clone()) else {
            continue;
        };

        let Some(parameters_node) = node.field("parameters") else {
            continue;
        };

        let mut metavar_env = extract_metavar_env(source, name_match.get_env());
        let mut meta_variables = extract_structured_meta_variables(name_match.get_env());

        if let Some(parameters_pattern) = &fallback.parameters_pattern {
            let Some(parameters_match) = parameters_node.find(parameters_pattern.clone()) else {
                continue;
            };
            merge_metavar_env(
                &mut metavar_env,
                extract_metavar_env(source, parameters_match.get_env()),
            );
            merge_structured_meta_variables(
                &mut meta_variables,
                extract_structured_meta_variables(parameters_match.get_env()),
            );
        } else if fallback.raw_params == "$$$ARGS" {
            let text = parameters_node.text();
            let mut inner = text.trim();
            if inner.starts_with('(') && inner.ends_with(')') {
                inner = &inner[1..inner.len() - 1];
            }
            metavar_env.insert("ARGS".to_string(), inner.to_string());
            let parameter_range = parameters_node.range();
            if parameter_range.end > parameter_range.start + 1 && !inner.is_empty() {
                meta_variables.multi.insert(
                    "ARGS".to_string(),
                    vec![AstMetaVariableCapture {
                        text: inner.to_string(),
                        byte_range: (parameter_range.start + 1)..(parameter_range.end - 1),
                    }],
                );
            }
        } else if fallback.raw_params.is_empty() {
            let text = parameters_node.text();
            let inner = text.trim();
            if inner != "()" {
                continue;
            }
        }

        let byte_range = node.range();
        matches.push(AstMatch {
            file: file_path.clone(),
            line: line_number_for_byte(line_starts, byte_range.start),
            matched_text: node.text().to_string(),
            candidate: EditCandidate {
                file: file_path.clone(),
                byte_range,
                metavar_env,
            },
            meta_variables,
        });
    }

    matches
}

pub(super) fn search_path_with_prefilter(
    pattern: &CompiledAstPattern,
    lang: SupportLang,
    path: &Path,
    prefilter_enabled: bool,
) -> Result<Vec<AstMatch>> {
    if !path.exists() {
        anyhow::bail!("Path not found: {}", path.display());
    }

    let prefilter_literal = prefilter_enabled
        .then(|| extract_prefilter_literal(&pattern.pattern))
        .flatten();

    if path.is_file() {
        return AstBackend::search_file_static(pattern, lang, prefilter_literal.as_deref(), path);
    }

    let files = collect_ast_search_files(path, lang)?;
    let per_file_matches: Result<Vec<Vec<AstMatch>>> = files
        .par_iter()
        .map(|file| {
            AstBackend::search_file_static(pattern, lang, prefilter_literal.as_deref(), file)
        })
        .collect();

    let mut matches = Vec::new();
    for file_matches in per_file_matches? {
        matches.extend(file_matches);
    }
    Ok(matches)
}

pub(super) fn search_path_for_cli(
    pattern: &CompiledAstPattern,
    lang: SupportLang,
    path: &Path,
    prefilter_enabled: bool,
) -> Result<Vec<AstCliFileMatches>> {
    if !path.exists() {
        anyhow::bail!("Path not found: {}", path.display());
    }

    let prefilter_literal = prefilter_enabled
        .then(|| extract_prefilter_literal(&pattern.pattern))
        .flatten();

    if path.is_file() {
        let matches = AstBackend::search_file_for_cli_static(
            pattern,
            lang,
            prefilter_literal.as_deref(),
            path,
        )?;
        if matches.is_empty() {
            return Ok(Vec::new());
        }
        return Ok(vec![AstCliFileMatches {
            file: path.to_path_buf(),
            matches,
        }]);
    }

    let files = collect_ast_search_files(path, lang)?;
    let per_file_matches: Result<Vec<Vec<AstCliMatch>>> = files
        .par_iter()
        .map(|file| {
            AstBackend::search_file_for_cli_static(
                pattern,
                lang,
                prefilter_literal.as_deref(),
                file,
            )
        })
        .collect();

    Ok(files
        .into_iter()
        .zip(per_file_matches?)
        .filter_map(|(file, matches)| {
            (!matches.is_empty()).then_some(AstCliFileMatches { file, matches })
        })
        .collect())
}

/// Collects AST candidate files with ignore/type filters before parallel parsing.
pub(super) fn collect_ast_search_files(path: &Path, lang: SupportLang) -> Result<Vec<PathBuf>> {
    if !path.exists() {
        anyhow::bail!("Path not found: {}", path.display());
    }

    if path.is_file() {
        return Ok(vec![path.to_path_buf()]);
    }

    let mut files: Vec<PathBuf> = build_ast_search_walk_builder(path, lang)?
        .build()
        .filter_map(|entry| entry.ok())
        .filter(|entry| entry.file_type().is_some_and(|kind| kind.is_file()))
        .map(|entry| entry.into_path())
        .collect();
    files.sort_unstable();
    Ok(files)
}

pub(super) fn build_ast_search_walk_builder(path: &Path, lang: SupportLang) -> Result<WalkBuilder> {
    let mut builder = WalkBuilder::new(path);
    builder.hidden(true);
    builder.git_ignore(true);
    builder.threads(ast_search_walk_threads());
    builder.types(build_ast_search_types(lang)?);

    for ignore_name in [".ignore", ".gitignore", ".rgignore"] {
        let ignore_path = path.join(ignore_name);
        if ignore_path.is_file() {
            builder.add_ignore(ignore_path);
        }
    }

    Ok(builder)
}

pub(super) fn build_ast_search_types(lang: SupportLang) -> Result<Types> {
    let mut builder = TypesBuilder::new();
    let (type_name, globs): (&str, &[&str]) = match lang {
        SupportLang::Python => (
            "tgpythonast",
            &[
                "*.py", "*.PY", "*.py3", "*.PY3", "*.pyi", "*.PYI", "*.pyw", "*.PYW", "*.bzl",
                "*.BZL",
            ],
        ),
        SupportLang::JavaScript => (
            "tgjavascriptast",
            &[
                "*.js", "*.JS", "*.jsx", "*.JSX", "*.cjs", "*.CJS", "*.mjs", "*.MJS",
            ],
        ),
        SupportLang::TypeScript => (
            "tgtypescriptast",
            &[
                "*.ts", "*.TS", "*.tsx", "*.TSX", "*.cts", "*.CTS", "*.mts", "*.MTS",
            ],
        ),
        SupportLang::Rust => ("tgrustast", &["*.rs", "*.RS"]),
        _ => anyhow::bail!("Unsupported language type filter"),
    };

    for glob in globs {
        builder
            .add(type_name, glob)
            .with_context(|| format!("failed to register AST file type glob '{glob}'"))?;
    }
    builder.select(type_name);
    builder
        .build()
        .context("failed to build AST file type filter")
}

pub(super) fn ast_search_walk_threads() -> usize {
    std::thread::available_parallelism()
        .map(|count| count.get().min(12))
        .unwrap_or(1)
}

pub(super) fn extract_prefilter_literal(pattern: &Pattern) -> Option<String> {
    let fixed = pattern.fixed_string();
    let candidate = fixed.trim();
    if candidate.is_empty() {
        return None;
    }
    if !candidate
        .chars()
        .any(|ch| ch.is_alphanumeric() || ch == '_')
    {
        return None;
    }
    Some(candidate.to_string())
}

pub(super) fn load_search_source(file: &Path) -> Result<Option<String>> {
    let bytes = std::fs::read(file)
        .with_context(|| format!("failed to read source file {}", file.display()))?;
    if bytes.is_empty() || has_binary_bytes(&bytes) {
        return Ok(None);
    }
    let source = String::from_utf8(bytes)
        .map_err(|e| anyhow::anyhow!("invalid UTF-8 in {}: {e}", file.display()))?;
    Ok(Some(source))
}

pub(super) fn has_binary_bytes(bytes: &[u8]) -> bool {
    bytes[..bytes.len().min(BINARY_SCAN_BYTES)].contains(&0)
}

pub fn resolve_language(lang: &str) -> Result<SupportLang> {
    let language = lang
        .parse::<SupportLang>()
        .map_err(|_| anyhow::anyhow!("Unsupported language: {lang}"))?;

    match language {
        SupportLang::Python
        | SupportLang::JavaScript
        | SupportLang::TypeScript
        | SupportLang::Rust => Ok(language),
        _ => anyhow::bail!("Unsupported language: {lang}"),
    }
}

pub(super) fn collect_source_files(path: &Path, lang: SupportLang) -> Result<Vec<PathBuf>> {
    if !path.exists() {
        anyhow::bail!("Path not found: {}", path.display());
    }

    if path.is_file() {
        return Ok(vec![path.to_path_buf()]);
    }

    let files: Vec<PathBuf> = WalkBuilder::new(path)
        .hidden(true)
        .git_ignore(true)
        .build()
        .filter_map(|entry| entry.ok())
        .filter(|entry| entry.file_type().is_some_and(|ft| ft.is_file()))
        .map(|entry| entry.into_path())
        .filter(|file| file_matches_language(file, lang))
        .collect();

    Ok(files)
}

pub(super) fn collect_batch_source_files(path: &Path, lang: SupportLang) -> Result<Vec<PathBuf>> {
    if !path.exists() {
        anyhow::bail!("Path not found: {}", path.display());
    }

    if path.is_file() {
        if file_matches_language(path, lang) {
            return Ok(vec![path.to_path_buf()]);
        }
        return Ok(Vec::new());
    }

    collect_source_files(path, lang)
}

pub(super) fn file_matches_language(path: &Path, lang: SupportLang) -> bool {
    let extension = path.extension().and_then(|ext| ext.to_str());
    matches!(
        (lang, extension),
        (SupportLang::Python, Some("py" | "py3" | "pyi" | "bzl"))
            | (SupportLang::JavaScript, Some("js" | "jsx" | "cjs" | "mjs"))
            | (SupportLang::TypeScript, Some("ts" | "cts" | "mts"))
            | (SupportLang::Rust, Some("rs"))
    )
}

pub(super) fn build_line_starts(source: &str) -> Vec<usize> {
    let mut line_starts = vec![0];
    for (index, byte) in source.as_bytes().iter().enumerate() {
        if *byte == b'\n' {
            line_starts.push(index + 1);
        }
    }
    line_starts
}

pub(super) fn line_number_for_byte(line_starts: &[usize], byte_offset: usize) -> usize {
    line_starts.partition_point(|start| *start <= byte_offset)
}

pub(super) fn extract_metavar_env(
    source: &str,
    env: &ast_grep_core::meta_var::MetaVarEnv<'_, ast_grep_core::tree_sitter::StrDoc<SupportLang>>,
) -> HashMap<String, String> {
    let mut extracted = HashMap::new();

    for variable in env.get_matched_variables() {
        match variable {
            MetaVariable::Capture(name, _) => {
                if let Some(node) = env.get_match(&name) {
                    extracted.insert(name, node.text().to_string());
                }
            }
            MetaVariable::MultiCapture(name) => {
                let nodes = env.get_multiple_matches(&name);
                if let (Some(first), Some(last)) = (nodes.first(), nodes.last()) {
                    extracted.insert(
                        name,
                        source[first.range().start..last.range().end].to_string(),
                    );
                }
            }
            MetaVariable::Dropped(_) | MetaVariable::Multiple => {}
        }
    }

    extracted
}

pub(super) fn extract_structured_meta_variables(
    env: &ast_grep_core::meta_var::MetaVarEnv<'_, ast_grep_core::tree_sitter::StrDoc<SupportLang>>,
) -> AstMetaVariables {
    let mut extracted = AstMetaVariables::default();

    for variable in env.get_matched_variables() {
        match variable {
            MetaVariable::Capture(name, _) => {
                if let Some(node) = env.get_match(&name) {
                    extracted.single.insert(
                        name,
                        AstMetaVariableCapture {
                            text: node.text().to_string(),
                            byte_range: node.range(),
                        },
                    );
                }
            }
            MetaVariable::MultiCapture(name) => {
                let nodes = env.get_multiple_matches(&name);
                let captures: Vec<AstMetaVariableCapture> = nodes
                    .into_iter()
                    .map(|node| AstMetaVariableCapture {
                        text: node.text().to_string(),
                        byte_range: node.range(),
                    })
                    .collect();
                if !captures.is_empty() {
                    extracted.multi.insert(name, captures);
                }
            }
            MetaVariable::Dropped(_) | MetaVariable::Multiple => {}
        }
    }

    extracted
}

pub(super) fn merge_structured_meta_variables(
    target: &mut AstMetaVariables,
    other: AstMetaVariables,
) {
    target.single.extend(other.single);
    for (name, captures) in other.multi {
        target.multi.entry(name).or_default().extend(captures);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::path::PathBuf;
    use std::sync::atomic::{AtomicUsize, Ordering as AtomicOrdering};
    use tempfile::tempdir;

    fn temp_entries(dir: &Path) -> Vec<String> {
        fs::read_dir(dir)
            .unwrap()
            .map(|entry| entry.unwrap().file_name().to_string_lossy().into_owned())
            .collect()
    }

    #[test]
    fn rewrite_source_skips_files_over_the_existing_size_limit() {
        let dir = tempdir().unwrap();
        let file = dir.path().join("large.py");
        fs::File::create(&file)
            .unwrap()
            .set_len(MAX_REWRITE_FILE_BYTES + 1)
            .unwrap();
        let target = crate::safe_write::AnchoredTarget::open(&file).unwrap();

        assert!(load_rewrite_source(&file, &target).unwrap().is_none());
    }

    #[test]
    fn atomic_write_replaces_target_and_cleans_temp_files() {
        let dir = tempdir().unwrap();
        let file_path = dir.path().join("fixture.py");
        fs::write(&file_path, "before\n").unwrap();

        atomic_write_file(&file_path, b"after\n").unwrap();

        assert_eq!(fs::read_to_string(&file_path).unwrap(), "after\n");
        assert!(
            temp_entries(dir.path())
                .into_iter()
                .all(|entry| !entry.starts_with(".tg_tmp_")),
            "temporary files should be removed after success"
        );
    }

    #[test]
    fn atomic_write_cleans_temp_files_after_failure() {
        let dir = tempdir().unwrap();
        let file_path = dir.path().join("fixture.py");
        fs::write(&file_path, "before\n").unwrap();

        let error = atomic_write_file_with_hook(&file_path, b"after\n", |_| {
            anyhow::bail!("injected rename failure")
        })
        .unwrap_err();

        assert!(
            error.to_string().contains("injected rename failure"),
            "unexpected error: {error:#}"
        );
        assert_eq!(fs::read_to_string(&file_path).unwrap(), "before\n");
        assert!(
            temp_entries(dir.path())
                .into_iter()
                .all(|entry| !entry.starts_with(".tg_tmp_")),
            "temporary files should be removed after failure"
        );
    }

    #[test]
    fn direct_write_overwrites_target_without_creating_temp_files() {
        let dir = tempdir().unwrap();
        let file_path = dir.path().join("fixture.py");
        fs::write(&file_path, "before\n").unwrap();

        direct_write_file(&file_path, b"after\n").unwrap();

        assert_eq!(fs::read_to_string(&file_path).unwrap(), "after\n");
        assert!(
            temp_entries(dir.path())
                .into_iter()
                .all(|entry| !entry.starts_with(".tg_tmp_")),
            "direct-write path should not leave temporary files behind"
        );
    }

    #[test]
    fn apply_edit_set_stops_after_injected_failure_and_leaves_remaining_files_unmodified() {
        let dir = tempdir().unwrap();
        let first_file = dir.path().join("a.py");
        let second_file = dir.path().join("b.py");
        let third_file = dir.path().join("c.py");
        fs::write(&first_file, "def a(x): return x\n").unwrap();
        fs::write(&second_file, "def b(y): return y\n").unwrap();
        fs::write(&third_file, "def c(z): return z\n").unwrap();

        let backend = AstBackend::new();
        let plan = backend
            .plan_rewrites(
                "def $F($$$ARGS): return $EXPR",
                "lambda $$$ARGS: $EXPR",
                "python",
                dir.path().to_str().unwrap(),
            )
            .unwrap();

        let apply_attempts = AtomicUsize::new(0);
        let error = apply_edit_set_with_writer(
            &plan.edits,
            None,
            ApplyExecution::Sequential,
            &|file, contents| {
                if apply_attempts.fetch_add(1, AtomicOrdering::SeqCst) == 1 {
                    anyhow::bail!("injected apply failure for {}", file.display());
                }
                atomic_write_file(file, contents)
            },
        )
        .unwrap_err();

        let message = format!("{error:#}");
        assert!(
            message.contains("injected apply failure"),
            "unexpected error: {message}"
        );
        assert_eq!(fs::read_to_string(&first_file).unwrap(), "lambda x: x\n");
        assert_eq!(
            fs::read_to_string(&second_file).unwrap(),
            "def b(y): return y\n"
        );
        assert_eq!(
            fs::read_to_string(&third_file).unwrap(),
            "def c(z): return z\n"
        );
        assert!(
            temp_entries(dir.path())
                .into_iter()
                .all(|entry| !entry.starts_with(".tg_tmp_")),
            "temporary files should be removed after mid-apply failure"
        );
    }

    fn write_search_fixture(
        dir: &std::path::Path,
        relative_path: &str,
        contents: impl AsRef<[u8]>,
    ) -> PathBuf {
        let path = dir.join(relative_path);
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent).unwrap();
        }
        fs::write(&path, contents).unwrap();
        path
    }

    fn matched_files(matches: &[AstMatch]) -> Vec<PathBuf> {
        matches.iter().map(|entry| entry.file.clone()).collect()
    }

    fn search_type_matches(path: &std::path::Path, lang: SupportLang) -> bool {
        build_ast_search_types(lang)
            .unwrap()
            .matched(path, false)
            .is_whitelist()
    }

    #[test]
    fn search_prefilter_false_negative_three_category() {
        let dir = tempdir().unwrap();
        let matching = write_search_fixture(dir.path(), "matching.py", "def keep(x): return x\n");
        write_search_fixture(
            dir.path(),
            "literal_only.py",
            "def keep(x):\n    value = return_value(x)\n    return_value = x\n",
        );
        write_search_fixture(dir.path(), "neither.py", "print('nothing to see here')\n");

        let backend = AstBackend::new();
        let matches = backend
            .search(
                "def $F($$$ARGS): return $EXPR",
                "python",
                dir.path().to_str().unwrap(),
            )
            .unwrap();

        assert_eq!(matched_files(&matches), vec![matching]);
    }

    #[test]
    fn search_respects_gitignore_for_parallel_walk() {
        let dir = tempdir().unwrap();
        write_search_fixture(dir.path(), ".gitignore", "*.generated.py\n");
        let visible = write_search_fixture(dir.path(), "visible.py", "def keep(x): return x\n");
        write_search_fixture(
            dir.path(),
            "ignored.generated.py",
            "def ignored(x): return x\n",
        );

        let backend = AstBackend::new();
        let matches = backend
            .search(
                "def $F($$$ARGS): return $EXPR",
                "python",
                dir.path().to_str().unwrap(),
            )
            .unwrap();

        assert_eq!(matched_files(&matches), vec![visible]);
    }

    #[test]
    fn collect_ast_search_files_applies_type_filters_and_gitignore() {
        let dir = tempdir().unwrap();
        let nested = dir.path().join("nested");
        fs::create_dir_all(&nested).unwrap();
        write_search_fixture(dir.path(), ".gitignore", "*.generated.py\n");
        let uppercase = write_search_fixture(dir.path(), "UPPER.PY", "def upper(x): return x\n");
        let pyw = write_search_fixture(dir.path(), "window.pyw", "def gui(x): return x\n");
        let nested_py = write_search_fixture(&nested, "nested.py", "def nested(x): return x\n");
        write_search_fixture(
            dir.path(),
            "ignored.generated.py",
            "def ignored(x): return x\n",
        );
        write_search_fixture(dir.path(), "notes.txt", "def text(x): return x\n");
        write_search_fixture(dir.path(), "no_extension", "def plain(x): return x\n");

        let files = collect_ast_search_files(dir.path(), SupportLang::Python).unwrap();
        let mut expected = vec![nested_py, uppercase, pyw];
        expected.sort_unstable();

        assert_eq!(files, expected);
    }

    #[test]
    fn search_type_filter_handles_python_edge_cases() {
        let dir = tempdir().unwrap();
        let uppercase = write_search_fixture(dir.path(), "UPPER.PY", "def upper(x): return x\n");
        let pyw = write_search_fixture(dir.path(), "window.pyw", "def gui(x): return x\n");
        write_search_fixture(dir.path(), "no_extension", "def plain(x): return x\n");
        write_search_fixture(dir.path(), "notes.txt", "def text(x): return x\n");

        let backend = AstBackend::new();
        let matches = backend
            .search(
                "def $F($$$ARGS): return $EXPR",
                "python",
                dir.path().to_str().unwrap(),
            )
            .unwrap();

        assert_eq!(matched_files(&matches), vec![uppercase, pyw]);
        assert!(!search_type_matches(
            std::path::Path::new(""),
            SupportLang::Python
        ));
    }

    #[test]
    fn search_prefilter_matches_results_without_prefilter() {
        let dir = tempdir().unwrap();
        write_search_fixture(dir.path(), "keep.py", "def keep(x): return x\n");
        write_search_fixture(
            dir.path(),
            "literal_only.py",
            "def literal_only(x):\n    return_value = x\n    return return_value\n",
        );
        write_search_fixture(dir.path(), "skip.py", "print('no return literal here')\n");

        let backend = AstBackend::new();
        let with_prefilter = backend
            .search_with_prefilter(
                "def $F($$$ARGS): return $EXPR",
                "python",
                dir.path().to_str().unwrap(),
                true,
            )
            .unwrap();
        let without_prefilter = backend
            .search_with_prefilter(
                "def $F($$$ARGS): return $EXPR",
                "python",
                dir.path().to_str().unwrap(),
                false,
            )
            .unwrap();

        assert_eq!(with_prefilter, without_prefilter);
    }

    #[test]
    fn search_prefilter_falls_back_for_wildcard_only_patterns() {
        let pattern = compile_ast_pattern("$F($$$ARGS)", SupportLang::Python).unwrap();
        assert_eq!(extract_prefilter_literal(&pattern.pattern), None);

        let dir = tempdir().unwrap();
        write_search_fixture(dir.path(), "calls.py", "first(a)\nsecond(b, c)\n");

        let backend = AstBackend::new();
        let with_prefilter = backend
            .search_with_prefilter("$F($$$ARGS)", "python", dir.path().to_str().unwrap(), true)
            .unwrap();
        let without_prefilter = backend
            .search_with_prefilter("$F($$$ARGS)", "python", dir.path().to_str().unwrap(), false)
            .unwrap();

        assert_eq!(with_prefilter, without_prefilter);
        assert!(!with_prefilter.is_empty());
    }

    #[test]
    fn search_for_cli_matches_backend_search_projection() {
        let dir = tempdir().unwrap();
        let alpha = write_search_fixture(
            dir.path(),
            "alpha.py",
            "def keep(x): return x\ndef also_keep(y): return y\n",
        );
        let beta = write_search_fixture(dir.path(), "beta.py", "def beta(z): return z\n");

        let backend = AstBackend::new();
        let full_matches = backend
            .search(
                "def $F($$$ARGS): return $EXPR",
                "python",
                dir.path().to_str().unwrap(),
            )
            .unwrap();
        let cli_matches = backend
            .search_for_cli(
                "def $F($$$ARGS): return $EXPR",
                "python",
                dir.path().to_str().unwrap(),
            )
            .unwrap();

        let projected_full: Vec<(PathBuf, usize, String)> = full_matches
            .into_iter()
            .map(|matched| (matched.file, matched.line, matched.matched_text))
            .collect();
        let projected_cli: Vec<(PathBuf, usize, String)> = cli_matches
            .into_iter()
            .flat_map(|file_matches| {
                let file = file_matches.file;
                file_matches
                    .matches
                    .into_iter()
                    .map(move |matched| (file.clone(), matched.line, matched.matched_text))
            })
            .collect();

        assert_eq!(projected_cli, projected_full);
        assert_eq!(projected_cli[0].0, alpha);
        assert_eq!(projected_cli[2].0, beta);
    }

    #[test]
    fn search_skips_binary_files_without_error() {
        let dir = tempdir().unwrap();
        write_search_fixture(
            dir.path(),
            "binary.py",
            b"def hidden(x): return x\0garbage\n",
        );
        let text = write_search_fixture(dir.path(), "text.py", "def shown(x): return x\n");

        let backend = AstBackend::new();
        let matches = backend
            .search(
                "def $F($$$ARGS): return $EXPR",
                "python",
                dir.path().to_str().unwrap(),
            )
            .unwrap();

        assert_eq!(matched_files(&matches), vec![text]);
    }

    #[test]
    fn search_handles_empty_dir_and_no_matching_files() {
        let empty_dir = tempdir().unwrap();
        let no_match_dir = tempdir().unwrap();
        write_search_fixture(no_match_dir.path(), "notes.txt", "def text(x): return x\n");

        let backend = AstBackend::new();
        let empty_matches = backend
            .search(
                "def $F($$$ARGS): return $EXPR",
                "python",
                empty_dir.path().to_str().unwrap(),
            )
            .unwrap();
        let no_match_results = backend
            .search(
                "def $F($$$ARGS): return $EXPR",
                "python",
                no_match_dir.path().to_str().unwrap(),
            )
            .unwrap();

        assert!(empty_matches.is_empty());
        assert!(no_match_results.is_empty());
    }

    #[test]
    fn search_handles_utf8_bom_prefixed_python_files() {
        let dir = tempdir().unwrap();
        let path = dir.path().join("bom.py");
        let mut bytes = UTF8_BOM.to_vec();
        bytes.extend_from_slice(b"def bom(x): return x\n");
        fs::write(&path, bytes).unwrap();

        let backend = AstBackend::new();
        let matches = backend
            .search(
                "def $F($$$ARGS): return $EXPR",
                "python",
                dir.path().to_str().unwrap(),
            )
            .unwrap();

        assert_eq!(matches.len(), 1);
        assert_eq!(matches[0].file, path);
        assert_eq!(matches[0].line, 1);
        assert_eq!(matches[0].candidate.byte_range.start, UTF8_BOM.len());
    }

    #[test]
    fn search_handles_large_python_files() {
        let dir = tempdir().unwrap();
        let large_file = dir.path().join("large.py");
        let mut source = String::from("def target(x): return x\n");
        while source.len() <= 11 * 1024 * 1024 {
            source.push_str("print('filler line to keep the parser busy')\n");
        }
        fs::write(&large_file, source).unwrap();

        let backend = AstBackend::new();
        let matches = backend
            .search(
                "def $F($$$ARGS): return $EXPR",
                "python",
                dir.path().to_str().unwrap(),
            )
            .unwrap();

        assert_eq!(matches.len(), 1);
        assert_eq!(matches[0].file, large_file);
    }

    #[test]
    fn search_results_are_sorted_deterministically() {
        let dir = tempdir().unwrap();
        let a_path = write_search_fixture(
            dir.path(),
            "a.py",
            "def first(x): return x\ndef second(y): return y\n",
        );
        let z_path = write_search_fixture(dir.path(), "z.py", "def last(z): return z\n");

        let backend = AstBackend::new();
        let first = backend
            .search(
                "def $F($$$ARGS): return $EXPR",
                "python",
                dir.path().to_str().unwrap(),
            )
            .unwrap();
        let second = backend
            .search(
                "def $F($$$ARGS): return $EXPR",
                "python",
                dir.path().to_str().unwrap(),
            )
            .unwrap();

        assert_eq!(first, second);
        assert_eq!(first.len(), 3);
        assert_eq!(first[0].file, a_path);
        assert_eq!(first[0].line, 1);
        assert_eq!(first[1].file, a_path);
        assert_eq!(first[1].line, 2);
        assert_eq!(first[2].file, z_path);
        assert_eq!(first[2].line, 1);
    }

    #[test]
    fn verify_uses_byte_offsets_after_atomic_apply() {
        let dir = tempdir().unwrap();
        let file_path = dir.path().join("fixture.py");
        fs::write(
            &file_path,
            "def f(x): return x\ndef g(a, b, c): return a + b + c\n",
        )
        .unwrap();

        let backend = AstBackend::new();
        let plan = backend
            .plan_and_apply(
                "def $F($$$ARGS): return $EXPR",
                "lambda $$$ARGS: $EXPR",
                "python",
                file_path.to_str().unwrap(),
            )
            .unwrap();

        let verification = plan.verify(&backend).unwrap();
        assert_eq!(verification.total_edits, 2);
        assert_eq!(verification.verified, 2);
        assert!(verification.mismatches.is_empty());
    }
}

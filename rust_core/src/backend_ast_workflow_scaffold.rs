use anyhow::Result;
use ast_grep_core::{tree_sitter::LanguageExt, Pattern};
use ast_grep_language::SupportLang;
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::Path;

pub(super) fn validate_new_name(name: &str) -> Result<()> {
    if name.is_empty()
        || name == "."
        || name == ".."
        || !name
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || "_.~#-".contains(c))
    {
        anyhow::bail!("Invalid item name {name:?}; use letters, digits, _, ., ~, #, or -.");
    }
    Ok(())
}

pub(super) fn validate_language_arg(language: &str) -> Result<()> {
    if language.trim().is_empty() {
        anyhow::bail!(
            "Invalid --lang {}; use a bare language name.",
            serde_json::to_string(language)?
        );
    }
    Ok(())
}

pub(super) fn write_new_file(path: &Path, contents: &[u8], description: &str) -> Result<()> {
    if fs::symlink_metadata(path).is_ok() {
        anyhow::bail!("{description} {:?} already exists.", path);
    }
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(path)
        .map_err(|error| {
            if error.kind() == std::io::ErrorKind::AlreadyExists {
                anyhow::anyhow!("{description} {:?} already exists.", path)
            } else {
                error.into()
            }
        })?;
    file.write_all(contents)?;
    Ok(())
}

pub(super) fn write_rule(base_dir: &Path, name: &str, language: &str) -> Result<()> {
    let language = normalize_language(language)?;
    let (pattern, _, _) = rule_examples(language)?;
    let rules_dir = base_dir.join("rules");
    fs::create_dir_all(&rules_dir)?;
    let path = rules_dir.join(format!("{name}.yml"));
    let yaml_pattern = serde_json::to_string(pattern)?;
    let yaml_name = serde_json::to_string(name)?;
    write_new_file(
        &path,
        format!("id: {yaml_name}\nlanguage: {language}\nrule:\n  pattern: {yaml_pattern}\n")
            .as_bytes(),
        "Rule file",
    )?;
    println!("Created rule scaffold in {:?}", path);
    Ok(())
}

fn project_default_language(base_dir: &Path) -> Result<String> {
    let config_path = base_dir.join("sgconfig.yml");
    match fs::metadata(&config_path) {
        Ok(metadata) if !metadata.is_file() => {
            anyhow::bail!("sgconfig.yml must be a regular file")
        }
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            return Ok("python".to_string())
        }
        Err(error) => return Err(error.into()),
        Ok(_) => {}
    }
    match fs::read_to_string(&config_path) {
        Ok(contents) => {
            let mut config: serde_yaml::Value =
                serde_yaml::from_str(&contents).map_err(|error| {
                    let message = error.to_string();
                    if let Some(entry) = message.strip_prefix("duplicate entry with key ") {
                        let key = entry
                            .split(" at line ")
                            .next()
                            .unwrap_or(entry)
                            .trim_matches('"');
                        anyhow::anyhow!("cannot parse sgconfig.yml: duplicate key '{key}'")
                    } else {
                        anyhow::anyhow!("cannot parse sgconfig.yml: invalid YAML syntax")
                    }
                })?;
            config
                .apply_merge()
                .map_err(|_| anyhow::anyhow!("cannot parse sgconfig.yml: invalid YAML syntax"))?;
            Ok(config
                .get("language")
                .and_then(serde_yaml::Value::as_str)
                .map(str::trim)
                .filter(|value| !value.is_empty())
                .unwrap_or("python")
                .to_lowercase())
        }
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok("python".to_string()),
        Err(error) => Err(error.into()),
    }
}

pub(super) fn write_test(
    base_dir: &Path,
    name: &str,
    explicit_language: Option<&str>,
) -> Result<()> {
    let linked_rule = base_dir.join("rules").join(format!("{name}.yml"));
    let language = if let Some(language) = explicit_language {
        language.trim().to_lowercase()
    } else {
        let label = format!("rules/{name}.yml");
        match fs::symlink_metadata(&linked_rule) {
            Ok(metadata) if !metadata.file_type().is_file() => {
                anyhow::bail!("linked rule must be a regular file: {label}")
            }
            Ok(_) => {
                let contents = fs::read_to_string(&linked_rule).map_err(|error| {
                    if error.kind() == std::io::ErrorKind::InvalidData {
                        anyhow::anyhow!("cannot parse linked rule {label}: invalid UTF-8")
                    } else {
                        error.into()
                    }
                })?;
                let mut rule: serde_yaml::Value =
                    serde_yaml::from_str(&contents).map_err(|error| {
                        if error
                            .to_string()
                            .starts_with("duplicate entry with key \"language\"")
                        {
                            anyhow::anyhow!(
                                "cannot parse linked rule {label}: duplicate key 'language'"
                            )
                        } else {
                            anyhow::anyhow!("cannot parse linked rule {label}: invalid YAML syntax")
                        }
                    })?;
                rule.apply_merge().map_err(|_| {
                    anyhow::anyhow!("cannot parse linked rule {label}: invalid YAML syntax")
                })?;
                if !rule.is_mapping() {
                    anyhow::bail!("cannot parse linked rule {label}: expected a YAML mapping")
                }
                rule.get("language")
                    .and_then(serde_yaml::Value::as_str)
                    .map(str::trim)
                    .filter(|value| !value.is_empty())
                    .map(str::to_lowercase)
                    .map(Ok)
                    .unwrap_or_else(|| project_default_language(base_dir))?
            }
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
                project_default_language(base_dir)?
            }
            Err(error) => return Err(error.into()),
        }
    };
    let language = normalize_language(&language)?;
    let (_, valid, invalid) = rule_examples(language)?;
    let tests_dir = base_dir.join("tests");
    fs::create_dir_all(&tests_dir)?;
    let path = tests_dir.join(format!("{name}.yml"));
    let valid_line = serde_json::to_string(valid)?;
    let invalid_line = serde_json::to_string(invalid)?;
    let yaml_name = serde_json::to_string(name)?;
    write_new_file(
        &path,
        format!(
            "id: {yaml_name}\nruleId: {yaml_name}\nvalid:\n  - {valid_line}\ninvalid:\n  - {invalid_line}\n"
        )
        .as_bytes(),
        "Test file",
    )?;
    println!(
        "Created test scaffold in {}.",
        path.strip_prefix(".").unwrap_or(&path).display()
    );
    Ok(())
}

fn normalize_language(language: &str) -> Result<&'static str> {
    validate_language_arg(language)?;
    let normalized = language.trim().to_lowercase();
    let canonical = match normalized.as_str() {
        "bash" | "sh" => "bash",
        "c" => "c",
        "cc" | "c++" | "cpp" | "cxx" => "cpp",
        "c-sharp" | "c#" | "c_sharp" | "cs" | "csharp" => "csharp",
        "css" => "css",
        "elixir" | "ex" => "elixir",
        "go" | "golang" => "go",
        "haskell" | "hs" => "haskell",
        "hcl" => "hcl",
        "html" => "html",
        "java" => "java",
        "javascript" | "jsx" | "js" => "javascript",
        "json" => "json",
        "kotlin" | "kt" => "kotlin",
        "lua" => "lua",
        "nix" => "nix",
        "php" => "php",
        "python" | "py" => "python",
        "ruby" | "rb" => "ruby",
        "rust" | "rs" => "rust",
        "scala" => "scala",
        "sol" | "solidity" => "solidity",
        "swift" => "swift",
        "typescript" | "ts" => "typescript",
        "tsx" => "tsx",
        "yaml" | "yml" => "yaml",
        _ => anyhow::bail!("Cannot scaffold AST rule for language {language:?}; run `tg ast-info` for supported languages."),
    };
    Ok(canonical)
}

fn rule_examples(language: &str) -> Result<(&'static str, &'static str, &'static str)> {
    match language {
        "bash" | "c" | "cpp" | "css" | "elixir" | "go" | "haskell" | "hcl"
        | "html" | "java" | "kotlin" | "lua" | "nix" | "php" | "ruby"
        | "scala" | "solidity" | "swift" | "tsx" | "yaml" => {
            Ok(("TODO_REPLACE_ME", "OTHER_IDENTIFIER", "TODO_REPLACE_ME"))
        }
        "csharp" => Ok(("TODO_REPLACE_ME()", "OTHER_IDENTIFIER()", "TODO_REPLACE_ME()")),
        "json" => Ok(("\"TODO_REPLACE_ME\"", "\"OTHER_IDENTIFIER\"", "\"TODO_REPLACE_ME\"")),
        "javascript" | "python" | "rust" | "typescript" => {
            Ok(("TODO_REPLACE_ME($$$ARGS)", "other(1)", "TODO_REPLACE_ME(1)"))
        }
        _ => anyhow::bail!("Cannot scaffold AST rule for language {language:?}: no validated pattern example is available for `tg test` on both front doors."),
    }
}

pub(super) fn match_test_snippets(
    pattern: &str,
    language: &str,
    snippets: &[String],
) -> Result<Vec<bool>> {
    let language = normalize_language(language)?;
    let parser = language.parse::<SupportLang>()?;
    let pattern = Pattern::try_new(pattern, parser)
        .map_err(|error| anyhow::anyhow!("Invalid pattern: {error}"))?;
    if pattern.has_error() {
        anyhow::bail!("Invalid pattern: parse error");
    }
    Ok(snippets
        .iter()
        .map(|snippet| {
            parser
                .ast_grep(snippet)
                .root()
                .find(pattern.clone())
                .is_some()
        })
        .collect())
}

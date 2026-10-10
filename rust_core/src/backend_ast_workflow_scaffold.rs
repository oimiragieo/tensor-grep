use anyhow::Result;
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::Path;

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
    let (pattern, _, _) = rule_examples(language)?;
    let rules_dir = base_dir.join("rules");
    fs::create_dir_all(&rules_dir)?;
    let path = rules_dir.join(format!("{name}.yml"));
    let yaml_pattern = if matches!(language.to_ascii_lowercase().as_str(), "css" | "json") {
        format!("'{pattern}'")
    } else {
        pattern.to_string()
    };
    write_new_file(
        &path,
        format!("id: {name}\nlanguage: {language}\nrule:\n  pattern: {yaml_pattern}\n").as_bytes(),
        "Rule file",
    )?;
    println!("Created rule scaffold in {:?}", path);
    Ok(())
}

pub(super) fn write_test(base_dir: &Path, name: &str, language: &str) -> Result<()> {
    let (_, valid, invalid) = rule_examples(language)?;
    let tests_dir = base_dir.join("tests");
    fs::create_dir_all(&tests_dir)?;
    let path = tests_dir.join(format!("{name}.yml"));
    let (valid_line, invalid_line) =
        if matches!(language.to_ascii_lowercase().as_str(), "css" | "html" | "json" | "yaml") {
            (format!("'{valid}'"), format!("'{invalid}'"))
        } else {
            (valid.to_string(), invalid.to_string())
        };
    write_new_file(
        &path,
        format!(
            "id: {name}\nruleId: {name}\nvalid:\n  - {valid_line}\ninvalid:\n  - {invalid_line}\n"
        )
        .as_bytes(),
        "Test file",
    )?;
    println!("Created test scaffold in {:?}", path);
    Ok(())
}

fn rule_examples(language: &str) -> Result<(&'static str, &'static str, &'static str)> {
    match language.to_ascii_lowercase().as_str() {
        "javascript" | "python" | "rust" | "typescript" => {
            Ok(("TODO_REPLACE_ME($$$ARGS)", "other(1)", "TODO_REPLACE_ME(1)"))
        }
        _ => anyhow::bail!("Cannot scaffold AST rule for language {language:?}: no validated pattern example is available for `tg test` on both front doors."),
    }
}

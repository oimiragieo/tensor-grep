use anyhow::Result;
use std::fs;
use std::path::Path;

pub(super) fn write_rule(base_dir: &Path, name: &str, language: &str) -> Result<()> {
    let rules_dir = base_dir.join("rules");
    fs::create_dir_all(&rules_dir)?;
    let path = rules_dir.join(format!("{name}.yml"));
    if path.exists() {
        anyhow::bail!("Rule file {:?} already exists.", path);
    }
    fs::write(
        &path,
        format!("id: {name}\nlanguage: {language}\nrule:\n  pattern: TODO_REPLACE_ME($$$ARGS)\n"),
    )?;
    println!("Created rule scaffold in {:?}", path);
    Ok(())
}

pub(super) fn write_test(base_dir: &Path, name: &str) -> Result<()> {
    let tests_dir = base_dir.join("tests");
    fs::create_dir_all(&tests_dir)?;
    let path = tests_dir.join(format!("{name}.yml"));
    if path.exists() {
        anyhow::bail!("Test file {:?} already exists.", path);
    }
    fs::write(
        &path,
        format!(
            "id: {name}\nruleId: {name}\nvalid:\n  - other(1)\ninvalid:\n  - TODO_REPLACE_ME(1)\n"
        ),
    )?;
    println!("Created test scaffold in {:?}", path);
    Ok(())
}

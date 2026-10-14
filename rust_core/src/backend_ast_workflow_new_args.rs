use super::{scaffold, AstNewKind, AstNewRequest};
use anyhow::{Context, Result};
use std::path::PathBuf;

pub(super) fn parse_ast_new_args(args: &[String]) -> Result<AstNewRequest> {
    let mut kind = AstNewKind::Project;
    let mut kind_seen = false;
    let mut name: Option<String> = None;
    let mut lang = None;
    let mut base_dir = PathBuf::from(".");
    let mut index = 0usize;

    while index < args.len() {
        match args[index].as_str() {
            "project" if !kind_seen => {
                kind = AstNewKind::Project;
                kind_seen = true;
            }
            "rule" if !kind_seen => {
                kind = AstNewKind::Rule;
                kind_seen = true;
            }
            "test" if !kind_seen => {
                kind = AstNewKind::Test;
                kind_seen = true;
            }
            "util" if !kind_seen => {
                kind = AstNewKind::Util;
                kind_seen = true;
            }
            "--lang" | "-l" => {
                index += 1;
                lang = Some(
                    args.get(index)
                        .cloned()
                        .context("--lang requires a language value")?,
                );
            }
            value if value.starts_with("--lang=") => {
                lang = Some(
                    value
                        .split_once('=')
                        .map(|(_, value)| value.to_string())
                        .context("--lang requires a language value")?,
                );
            }
            "--base-dir" | "-b" => {
                index += 1;
                base_dir = PathBuf::from(
                    args.get(index)
                        .context("--base-dir requires a directory value")?,
                );
            }
            value if value.starts_with("--base-dir=") => {
                base_dir = PathBuf::from(
                    value
                        .split_once('=')
                        .map(|(_, value)| value)
                        .context("--base-dir requires a directory value")?,
                );
            }
            "--yes" | "-y" => {}
            value if value.starts_with('-') => {
                anyhow::bail!("Unsupported tg new option {value:?}");
            }
            value => {
                if name.is_some() {
                    anyhow::bail!("Unexpected extra tg new argument {value:?}");
                }
                scaffold::validate_new_name(value)?;
                name = Some(value.to_string());
            }
        }
        index += 1;
    }

    if kind != AstNewKind::Project && name.is_none() {
        anyhow::bail!("tg new {:?} requires a name", kind);
    }
    if let Some(language) = lang.as_deref() {
        scaffold::validate_language_arg(language)?;
    }
    Ok(AstNewRequest {
        kind,
        name,
        lang,
        base_dir,
    })
}

//! Classify the supported Windows managed-runtime executable paths.
#![cfg(windows)]

use std::io;
use std::path::Path;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ManagedEntryRole {
    Standalone,
    StableLauncher,
    Generation,
}

pub fn classify_managed_entry(
    current_executable: &Path,
    profile_directory: &Path,
) -> io::Result<ManagedEntryRole> {
    let current = path_key(current_executable)?;
    let profile = path_key(profile_directory)?;
    let root = format!("{profile}\\.tensor-grep");
    if current == format!("{root}\\bin\\tg.exe") {
        return Ok(ManagedEntryRole::StableLauncher);
    }
    if let Some(relative) = current.strip_prefix(&format!("{root}\\runtimes\\")) {
        let mut components = relative.split('\\');
        let generation = components.next().unwrap_or_default();
        if valid_generation(generation)
            && components.next() == Some("bin")
            && components.next() == Some("tg.exe")
            && components.next().is_none()
        {
            return Ok(ManagedEntryRole::Generation);
        }
    }
    Ok(ManagedEntryRole::Standalone)
}

pub(crate) fn path_key(path: &Path) -> io::Result<String> {
    let value = path
        .to_str()
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidData, "managed path is not Unicode"))?
        .replace('/', "\\");
    let value = value.strip_prefix("\\\\?\\").unwrap_or(&value);
    Ok(value.trim_end_matches('\\').to_ascii_lowercase())
}

fn valid_generation(value: &str) -> bool {
    value.len() == 32
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

#[cfg(test)]
mod tests {
    use super::{classify_managed_entry, path_key, ManagedEntryRole};
    use std::path::Path;

    #[test]
    fn entry_classifier_accepts_only_canonical_managed_shapes() {
        let profile = Path::new(r"C:\Users\u");

        assert_eq!(
            classify_managed_entry(Path::new(r"C:\Users\u\.tensor-grep\bin\tg.exe"), profile)
                .unwrap(),
            ManagedEntryRole::StableLauncher
        );
        assert_eq!(
            classify_managed_entry(
                Path::new(
                    r"C:\Users\u\.tensor-grep\runtimes\0123456789abcdef0123456789abcdef\bin\tg.exe"
                ),
                profile,
            )
            .unwrap(),
            ManagedEntryRole::Generation
        );
        assert_eq!(
            classify_managed_entry(Path::new(r"C:\tools\tg.exe"), profile).unwrap(),
            ManagedEntryRole::Standalone
        );
        assert_eq!(
            classify_managed_entry(
                Path::new(r"C:\Users\u\.tensor-grep\runtimes\bad\bin\tg.exe"),
                profile,
            )
            .unwrap(),
            ManagedEntryRole::Standalone
        );
    }

    #[test]
    fn normalized_final_paths_match_case_and_extended_prefix() {
        assert_eq!(
            path_key(Path::new(r"\\?\C:\Users\U\TG.EXE")).unwrap(),
            path_key(Path::new(r"c:\users\u\tg.exe")).unwrap()
        );
    }
}

use std::env;
use std::path::{Path, PathBuf};

const MAX_ANCESTOR_DEPTH: usize = 4;

pub fn resolve_explicit_file_override(env_var: &str) -> Option<PathBuf> {
    let path = env::var_os(env_var)?;
    let candidate = PathBuf::from(&path);

    if candidate.is_file() {
        return Some(candidate);
    }

    eprintln!(
        "warning: ignoring {env_var} override `{}` because it is not a file",
        candidate.display()
    );
    None
}

pub fn resolve_existing_relative_to_current_exe(
    relative_candidates: &[&[&str]],
) -> Option<PathBuf> {
    let current_exe = env::current_exe().ok()?;
    resolve_existing_relative_to_exe(&current_exe, relative_candidates)
}

pub(crate) fn resolve_existing_relative_to_exe(
    exe_path: &Path,
    relative_candidates: &[&[&str]],
) -> Option<PathBuf> {
    let exe_dir = exe_path.parent()?;

    for base in exe_dir.ancestors().take(MAX_ANCESTOR_DEPTH + 1) {
        for relative_candidate in relative_candidates {
            let candidate = relative_candidate
                .iter()
                .fold(base.to_path_buf(), |path, segment| path.join(segment));

            if candidate.is_file() {
                return Some(candidate);
            }
        }
    }

    None
}

#[cfg(test)]
mod tests {
    use super::resolve_existing_relative_to_exe;
    use std::fs;
    use tempfile::tempdir;

    #[test]
    fn resolves_existing_files_relative_to_current_exe_and_ancestors() {
        let dir = tempdir().unwrap();
        let install_dir = dir.path().join("install");
        let exe_dir = install_dir.join("bin");
        let exe_path = exe_dir.join(if cfg!(windows) { "tg.exe" } else { "tg" });
        let python_path = install_dir
            .join(".venv")
            .join(if cfg!(windows) { "Scripts" } else { "bin" })
            .join(if cfg!(windows) {
                "python.exe"
            } else {
                "python"
            });

        fs::create_dir_all(exe_dir).unwrap();
        fs::create_dir_all(python_path.parent().unwrap()).unwrap();
        fs::write(&exe_path, b"binary").unwrap();
        fs::write(&python_path, b"python").unwrap();

        let resolved = resolve_existing_relative_to_exe(
            &exe_path,
            &[
                &[if cfg!(windows) {
                    "python.exe"
                } else {
                    "python"
                }],
                &[
                    ".venv",
                    if cfg!(windows) { "Scripts" } else { "bin" },
                    if cfg!(windows) {
                        "python.exe"
                    } else {
                        "python"
                    },
                ],
            ],
        );

        assert_eq!(resolved.as_deref(), Some(python_path.as_path()));
    }

    #[test]
    fn returns_none_when_no_candidates_exist() {
        let dir = tempdir().unwrap();
        let exe_dir = dir.path().join("bin");
        let exe_path = exe_dir.join(if cfg!(windows) { "tg.exe" } else { "tg" });

        fs::create_dir_all(&exe_dir).unwrap();
        fs::write(&exe_path, b"binary").unwrap();

        let resolved = resolve_existing_relative_to_exe(
            &exe_path,
            &[&[if cfg!(windows) {
                "python.exe"
            } else {
                "python"
            }]],
        );

        assert!(resolved.is_none());
    }

    #[test]
    fn does_not_walk_more_than_four_ancestor_levels() {
        let dir = tempdir().unwrap();
        let exe_dir = dir
            .path()
            .join("level0")
            .join("level1")
            .join("level2")
            .join("level3")
            .join("level4")
            .join("bin");
        let exe_path = exe_dir.join(if cfg!(windows) { "tg.exe" } else { "tg" });
        let out_of_bounds_python = dir.path().join(if cfg!(windows) {
            "python.exe"
        } else {
            "python"
        });

        fs::create_dir_all(&exe_dir).unwrap();
        fs::write(&exe_path, b"binary").unwrap();
        fs::write(&out_of_bounds_python, b"python").unwrap();

        let resolved = resolve_existing_relative_to_exe(
            &exe_path,
            &[&[if cfg!(windows) {
                "python.exe"
            } else {
                "python"
            }]],
        );

        assert!(
            resolved.is_none(),
            "expected no candidate beyond the four-level ancestor search window, got {resolved:?}"
        );
    }
}

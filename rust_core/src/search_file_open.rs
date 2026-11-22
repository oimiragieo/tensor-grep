use std::fs::{self, File};
use std::io;
use std::path::{Component, Path, PathBuf};

use anyhow::{Context, Result};
use grep_matcher::Matcher;
use grep_searcher::{Searcher, Sink};

use crate::safe_write::AnchoredDirectory;

#[derive(Debug)]
pub(crate) struct NativeSearchOpenFailure(pub(crate) PathBuf);

impl std::fmt::Display for NativeSearchOpenFailure {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(
            formatter,
            "failed to open native search path {}",
            self.0.display()
        )
    }
}

impl std::error::Error for NativeSearchOpenFailure {}

pub(crate) fn open_native_search_file(path: &Path) -> Result<File> {
    open_search_file_nofollow(path).with_context(|| NativeSearchOpenFailure(path.to_path_buf()))
}

pub(crate) fn resolve_explicit_search_root(path: &Path) -> Result<PathBuf> {
    let metadata =
        fs::symlink_metadata(path).with_context(|| NativeSearchOpenFailure(path.to_path_buf()))?;
    let link = metadata.file_type().is_symlink();
    #[cfg(windows)]
    let link = {
        use std::os::windows::fs::MetadataExt;
        link || metadata.file_attributes() & 0x400 != 0
    };
    if link {
        Ok(fs::canonicalize(path).with_context(|| NativeSearchOpenFailure(path.to_path_buf()))?)
    } else {
        Ok(path.to_path_buf())
    }
}

pub(crate) fn verify_directory_ancestry_nofollow(dir: &Path) -> Result<()> {
    let abs = absolute_path(dir).with_context(|| NativeSearchOpenFailure(dir.to_path_buf()))?;
    open_parent_directory_nofollow(&abs)
        .map(|_| ())
        .with_context(|| NativeSearchOpenFailure(dir.to_path_buf()))
}

pub(crate) fn resolve_search_target_with_policy(
    path: &Path,
    follow_explicit_leaf_link: bool,
) -> Result<(PathBuf, fs::Metadata)> {
    if follow_explicit_leaf_link {
        let resolved = resolve_explicit_search_root(path)
            .with_context(|| NativeSearchOpenFailure(path.to_path_buf()))?;
        let meta = resolved
            .metadata()
            .with_context(|| NativeSearchOpenFailure(path.to_path_buf()))?;
        Ok((resolved, meta))
    } else {
        let meta = fs::symlink_metadata(path)
            .with_context(|| NativeSearchOpenFailure(path.to_path_buf()))?;
        Ok((path.to_path_buf(), meta))
    }
}

pub(crate) fn build_cpu_regex(
    pattern: &str,
    ignore_case: bool,
    fixed_strings: bool,
) -> Result<regex::bytes::Regex> {
    let source = if fixed_strings {
        regex::escape(pattern)
    } else {
        pattern.to_string()
    };
    Ok(regex::bytes::RegexBuilder::new(&source)
        .case_insensitive(ignore_case)
        .build()?)
}

pub(crate) fn collect_search_targets(
    path: &Path,
    follow_explicit_leaf_link: bool,
) -> Result<Vec<PathBuf>> {
    let (path_obj, meta) = resolve_search_target_with_policy(path, follow_explicit_leaf_link)?;
    if meta.is_file() {
        return Ok(vec![path_obj]);
    }
    if meta.is_dir() {
        verify_directory_ancestry_nofollow(&path_obj)?;
        let mut files = Vec::new();
        for entry in walkdir::WalkDir::new(path_obj) {
            let entry = entry.with_context(|| NativeSearchOpenFailure(path.to_path_buf()))?;
            if entry.file_type().is_file() {
                files.push(entry.into_path());
            }
        }
        return Ok(files);
    }
    Err(anyhow::anyhow!(
        "refusing non-regular search target: {}",
        path.display()
    ))
    .with_context(|| NativeSearchOpenFailure(path.to_path_buf()))
}

pub(crate) fn search_opened_path<M, S>(
    searcher: &mut Searcher,
    matcher: M,
    path: &Path,
    sink: S,
    context: &str,
) -> Result<()>
where
    M: Matcher,
    S: Sink<Error = io::Error>,
{
    let file = open_native_search_file(path)?;
    searcher
        .search_file(matcher, &file, sink)
        .with_context(|| format!("{context} for {}", path.display()))
}

pub(crate) fn sink_io_error(err: anyhow::Error) -> io::Error {
    for cause in err.chain() {
        if let Some(io_err) = cause.downcast_ref::<io::Error>() {
            return io::Error::new(io_err.kind(), err.to_string());
        }
    }
    io::Error::other(err)
}

/// Open a search input without following leaf or parent links/reparse points.
///
/// Parents are opened through retained directory handles / relative opens so a
/// component cannot be swapped between validation and the leaf open. The returned
/// `File` is the opened object; keep it alive for any reader or mmap that consumes it.
pub(crate) fn open_search_file_nofollow(path: &Path) -> anyhow::Result<File> {
    let absolute = absolute_path(path)?;
    let file_name = absolute
        .file_name()
        .ok_or_else(|| {
            anyhow::anyhow!(
                "refusing search target without a file name {}",
                path.display()
            )
        })?
        .to_os_string();
    let parent_path = absolute.parent().ok_or_else(|| {
        anyhow::anyhow!(
            "refusing search target without a parent directory {}",
            path.display()
        )
    })?;
    let parent = open_parent_directory_nofollow(parent_path)?;
    parent.open_regular_child_file(&file_name)
}

fn absolute_path(path: &Path) -> anyhow::Result<PathBuf> {
    if path.is_absolute() {
        Ok(path.to_path_buf())
    } else {
        Ok(std::env::current_dir()?.join(path))
    }
}

fn open_parent_directory_nofollow(parent: &Path) -> anyhow::Result<AnchoredDirectory> {
    #[cfg(unix)]
    {
        // Darwin exposes these system directories through aliases under /private.
        #[cfg(target_os = "macos")]
        let normalized_parent;
        #[cfg(target_os = "macos")]
        let target = if parent.starts_with("/var")
            || parent.starts_with("/tmp")
            || parent.starts_with("/etc")
        {
            normalized_parent = Path::new("/private").join(parent.strip_prefix("/")?);
            normalized_parent.as_path()
        } else {
            parent
        };
        #[cfg(not(target_os = "macos"))]
        let target = parent;

        // Walk every component with retained descriptors and pop on `..` so a
        // concurrent parent rename cannot redirect `openat(current_fd, "..")`.
        let mut stack: Vec<AnchoredDirectory> = Vec::new();
        for component in target.components() {
            match component {
                Component::RootDir => {
                    stack.clear();
                    stack.push(AnchoredDirectory::open(Path::new("/"))?);
                }
                Component::Normal(name) => {
                    let current = stack.last().ok_or_else(|| {
                        anyhow::anyhow!(
                            "refusing non-rooted search parent path {}",
                            parent.display()
                        )
                    })?;
                    stack.push(current.open_child(name)?);
                }
                Component::CurDir => {}
                Component::ParentDir => {
                    if stack.len() > 1 {
                        stack.pop();
                    } else if stack.is_empty() {
                        anyhow::bail!(
                            "refusing non-rooted search parent path {}",
                            parent.display()
                        );
                    }
                }
                Component::Prefix(_) => {
                    anyhow::bail!(
                        "refusing unexpected prefix in Unix search path {}",
                        parent.display()
                    );
                }
            }
        }
        stack.pop().ok_or_else(|| {
            anyhow::anyhow!("refusing search parent without root {}", parent.display())
        })
    }
    #[cfg(windows)]
    {
        // Open the volume root by path once, then every further directory via a
        // handle-relative NtCreateFile so intermediate junctions cannot redirect.
        let mut components = parent.components();
        let mut root = PathBuf::new();
        let mut saw_root = false;
        for component in components.by_ref() {
            root.push(component.as_os_str());
            match component {
                Component::Prefix(_) => {}
                Component::RootDir => {
                    saw_root = true;
                    break;
                }
                Component::CurDir => {}
                Component::ParentDir | Component::Normal(_) => {
                    anyhow::bail!(
                        "refusing non-rooted search parent path {}",
                        parent.display()
                    );
                }
            }
        }
        anyhow::ensure!(
            saw_root,
            "refusing search parent without a root directory {}",
            parent.display()
        );
        let mut directory = AnchoredDirectory::open(&root)?;
        for component in components {
            match component {
                Component::Normal(name) => {
                    directory = directory.open_child(name)?;
                }
                Component::CurDir => {}
                Component::ParentDir => directory = directory.open_parent()?,
                Component::Prefix(_) | Component::RootDir => {
                    anyhow::bail!(
                        "refusing unexpected root component in search path {}",
                        parent.display()
                    );
                }
            }
        }
        Ok(directory)
    }
    #[cfg(not(any(unix, windows)))]
    {
        let _ = parent;
        anyhow::bail!("no-follow search open is unsupported on this platform");
    }
}

#[cfg(test)]
mod tests {
    use super::open_search_file_nofollow;

    #[test]
    fn nofollow_open_rejects_a_leaf_link_after_selection() {
        let dir = tempfile::tempdir().unwrap();
        let target = dir.path().join("target.txt");
        let link = dir.path().join("link.txt");
        std::fs::write(&target, "sentinel").unwrap();
        #[cfg(unix)]
        let linked = std::os::unix::fs::symlink(&target, &link).is_ok();
        #[cfg(windows)]
        let linked = std::os::windows::fs::symlink_file(&target, &link).is_ok();
        #[cfg(not(any(unix, windows)))]
        let linked = false;
        if linked {
            assert!(open_search_file_nofollow(&link).is_err());
        } else {
            eprintln!("skipping leaf-link refusal: links unavailable");
        }
    }
}

use std::fs::{self, File};
use std::io;
#[cfg(windows)]
use std::path::Component;
use std::path::{Path, PathBuf};

use anyhow::{Context, Result};
use grep_matcher::Matcher;
use grep_searcher::{Searcher, Sink};

use crate::safe_write::AnchoredDirectory;

pub(crate) fn open_native_search_file(path: &Path) -> Result<File> {
    open_search_file_nofollow(path)
        .with_context(|| format!("failed to open native search path {}", path.display()))
}

pub(crate) fn resolve_explicit_search_root(path: &Path) -> Result<PathBuf> {
    let metadata = fs::symlink_metadata(path)?;
    let mut link = metadata.file_type().is_symlink();
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        link |= metadata.file_attributes() & 0x400 != 0;
    }
    if link {
        Ok(fs::canonicalize(path)?)
    } else {
        Ok(path.to_path_buf())
    }
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
        // Unix AnchoredDirectory::open walks from `/` with openat(O_NOFOLLOW).
        AnchoredDirectory::open(parent)
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

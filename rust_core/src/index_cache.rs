//! Repo-root-anchored `.tg_cache/index_v1/` resolution and safe `.gitignore` creation.
//!
//! Keeps `.tg_index` and its `.lock`/`.lock.os` sidecars out of tracked subdirectories while
//! preserving read-only fallback for pre-existing legacy `<dir>/.tg_index` files.

use crate::safe_write::AnchoredDirectory;
use sha2::{Digest, Sha256};
use std::ffi::OsStr;
use std::path::{Path, PathBuf};

const CACHE_DIR_NAME: &str = ".tg_cache";
const INDEX_SUBDIR_NAME: &str = "index_v1";
const GITIGNORE_NAME: &str = ".gitignore";
const GITIGNORE_CONTENT: &[u8] = b"*\n";

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct IndexCachePaths {
    pub repo_root: PathBuf,
    pub cache_dir: PathBuf,
    pub index_dir: PathBuf,
    pub write_path: PathBuf,
    pub read_path: PathBuf,
    pub legacy_path: PathBuf,
}

fn raw_scope_dir(search_path: &Path) -> PathBuf {
    if search_path.is_file() {
        search_path
            .parent()
            .filter(|parent| !parent.as_os_str().is_empty())
            .unwrap_or_else(|| Path::new("."))
            .to_path_buf()
    } else {
        search_path.to_path_buf()
    }
}

fn has_git_marker(dir: &Path) -> bool {
    dir.join(".git").symlink_metadata().is_ok()
}

fn has_project_marker(dir: &Path) -> bool {
    ["pyproject.toml", "Cargo.toml"].iter().any(|marker| {
        dir.join(marker)
            .symlink_metadata()
            .map(|meta| meta.is_file())
            .unwrap_or(false)
    })
}

/// Walks up from `canonical_scope` to find the repository/project root.
/// Prefers `.git` (directory or gitfile) over nested `pyproject.toml`/`Cargo.toml` manifests so
/// workspace sub-packages still anchor at the enclosing repository root.
pub fn resolve_repo_root(canonical_scope: &Path) -> PathBuf {
    if let Some(git_root) = canonical_scope
        .ancestors()
        .find(|ancestor| has_git_marker(ancestor))
    {
        return git_root.to_path_buf();
    }
    if let Some(project_root) = canonical_scope
        .ancestors()
        .find(|ancestor| has_project_marker(ancestor))
    {
        return project_root.to_path_buf();
    }
    canonical_scope.to_path_buf()
}

/// Returns the deterministic SHA-256 hex digest for a canonical scope path.
pub fn scope_hash(canonical_scope: &Path) -> String {
    let digest = Sha256::digest(canonical_scope.to_string_lossy().as_bytes());
    let mut hex = String::with_capacity(digest.len() * 2);
    for byte in digest {
        use std::fmt::Write as _;
        let _ = write!(hex, "{byte:02x}");
    }
    hex
}

/// Resolves both the repo-anchored write path (`<repo_root>/.tg_cache/index_v1/<hash>.tg_index`)
/// and the read path (preferring `write_path`, falling back read-only to legacy `<dir>/.tg_index`
/// if it already exists).
pub fn resolve_index_paths(search_path: &str) -> IndexCachePaths {
    let raw_dir = raw_scope_dir(Path::new(search_path));
    let canonical_scope = std::fs::canonicalize(&raw_dir).unwrap_or_else(|_| raw_dir.clone());
    let repo_root = resolve_repo_root(&canonical_scope);
    let cache_dir = repo_root.join(CACHE_DIR_NAME);
    let index_dir = cache_dir.join(INDEX_SUBDIR_NAME);
    let file_name = format!("{}.tg_index", scope_hash(&canonical_scope));
    let write_path = index_dir.join(file_name);
    let legacy_path = raw_dir.join(".tg_index");
    let read_path = if confined_regular_file_exists(&write_path) {
        write_path.clone()
    } else if confined_regular_file_exists(&legacy_path) {
        legacy_path.clone()
    } else {
        write_path.clone()
    };

    IndexCachePaths {
        repo_root,
        cache_dir,
        index_dir,
        write_path,
        read_path,
        legacy_path,
    }
}

/// Convenience helper for read-only callers such as warm-index detection.
pub fn resolve_read_index_path(search_path: &str) -> PathBuf {
    resolve_index_paths(search_path).read_path
}

/// Opens an index file as a regular file through `AnchoredDirectory`, refusing symlinks and
/// junctions on both parent directories and the leaf file.
pub fn open_confined_regular_file(path: &Path) -> anyhow::Result<std::fs::File> {
    let absolute = if path.is_absolute() {
        path.to_path_buf()
    } else {
        std::env::current_dir()?.join(path)
    };
    let file_name = absolute
        .file_name()
        .ok_or_else(|| anyhow::anyhow!("{} has no file name", path.display()))?;
    let parent = absolute
        .parent()
        .ok_or_else(|| anyhow::anyhow!("{} has no parent directory", path.display()))?;
    let dir = AnchoredDirectory::open(parent)?;
    Ok(dir.open_regular_file(file_name)?)
}

/// Returns `true` only if `path` can be opened as a regular file through `AnchoredDirectory`
/// without traversing any symlink or junction.
pub fn confined_regular_file_exists(path: &Path) -> bool {
    open_confined_regular_file(path).is_ok()
}

/// Creates `<repo_root>/.tg_cache`, `<repo_root>/.tg_cache/.gitignore` (`*\n`, no-clobber), and
/// `<repo_root>/.tg_cache/index_v1` through `AnchoredDirectory`, returning the retained
/// `AnchoredDirectory` handle for `index_v1`.
pub fn ensure_cache_dir_anchored(repo_root: &Path) -> anyhow::Result<AnchoredDirectory> {
    let root_dir = AnchoredDirectory::open(repo_root)?;
    let cache_dir = root_dir.open_or_create_child(OsStr::new(CACHE_DIR_NAME))?;
    let gitignore = cache_dir.target(OsStr::new(GITIGNORE_NAME))?;
    if gitignore.metadata().is_err() {
        if let Err(err) = gitignore.publish_atomic_new(GITIGNORE_CONTENT) {
            let recheck = cache_dir.target(OsStr::new(GITIGNORE_NAME))?;
            if recheck.metadata().is_err() {
                return Err(err.error);
            }
        }
    }
    cache_dir.open_or_create_child(OsStr::new(INDEX_SUBDIR_NAME))
}

/// Creates `<repo_root>/.tg_cache`, `<repo_root>/.tg_cache/.gitignore` (`*\n`, no-clobber), and
/// `<repo_root>/.tg_cache/index_v1` through `AnchoredDirectory` so symlinks and junctions are
/// refused.
pub fn ensure_cache_dir_and_gitignore(repo_root: &Path) -> anyhow::Result<PathBuf> {
    Ok(ensure_cache_dir_anchored(repo_root)?.path().to_path_buf())
}

/// Ensures `dir` and any missing parent directories exist by opening the deepest valid
/// anchored ancestor and creating each missing component via
/// [`AnchoredDirectory::open_or_create_child`], refusing symlinks and junctions at every step.
pub fn ensure_dir_anchored(dir: &Path) -> anyhow::Result<AnchoredDirectory> {
    let absolute = if dir.is_absolute() {
        dir.to_path_buf()
    } else {
        std::env::current_dir()?.join(dir)
    };
    let first_err = match AnchoredDirectory::open(&absolute) {
        Ok(handle) => return Ok(handle),
        Err(err) => err,
    };
    for ancestor in absolute.ancestors().skip(1) {
        if let Ok(mut current) = AnchoredDirectory::open(ancestor) {
            let suffix = absolute.strip_prefix(ancestor).map_err(|_| {
                anyhow::anyhow!(
                    "failed to compute relative path from {} to {}",
                    ancestor.display(),
                    absolute.display()
                )
            })?;
            for component in suffix.components() {
                match component {
                    std::path::Component::Normal(name) => {
                        current = current.open_or_create_child(name)?;
                    }
                    std::path::Component::CurDir => continue,
                    _ => {
                        anyhow::bail!(
                            "refusing non-normal path component in {}",
                            absolute.display()
                        );
                    }
                }
            }
            return Ok(current);
        }
    }
    Err(first_err)
}

/// Ensures the `.tg_cache/.gitignore` and `index_v1/` hierarchy exist for a `write_path`
/// returned by [`resolve_index_paths`], returning the retained `AnchoredDirectory` handle.
pub fn ensure_cache_parent_anchored(write_path: &Path) -> anyhow::Result<AnchoredDirectory> {
    let index_dir = write_path.parent().ok_or_else(|| {
        anyhow::anyhow!("index write path has no parent: {}", write_path.display())
    })?;
    let cache_dir = index_dir
        .parent()
        .ok_or_else(|| anyhow::anyhow!("index dir has no parent: {}", index_dir.display()))?;
    let repo_root = cache_dir
        .parent()
        .ok_or_else(|| anyhow::anyhow!("cache dir has no parent: {}", cache_dir.display()))?;
    ensure_cache_dir_anchored(repo_root)
}

/// Ensures the `.tg_cache/.gitignore` and `index_v1/` hierarchy exist for a `write_path`
/// returned by [`resolve_index_paths`].
pub fn ensure_cache_parent(write_path: &Path) -> anyhow::Result<()> {
    ensure_cache_parent_anchored(write_path)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::tempdir;

    #[test]
    fn index_cache_confinement_resolves_git_and_manifest_roots() {
        let dir = tempdir().unwrap();
        let repo = dir.path().join("repo");
        let sub = repo.join("src").join("nested");
        std::fs::create_dir_all(repo.join(".git")).unwrap();
        std::fs::create_dir_all(&sub).unwrap();
        std::fs::write(repo.join("Cargo.toml"), "[package]\nname = \"demo\"\n").unwrap();

        let paths = resolve_index_paths(sub.to_str().unwrap());
        let expected_root = std::fs::canonicalize(&repo).unwrap();
        assert_eq!(paths.repo_root, expected_root);
        assert_eq!(paths.cache_dir, expected_root.join(".tg_cache"));
        assert_eq!(
            paths.index_dir,
            expected_root.join(".tg_cache").join("index_v1")
        );
        assert!(paths.write_path.starts_with(&paths.index_dir));
        assert!(!paths
            .write_path
            .starts_with(std::fs::canonicalize(&sub).unwrap()));
    }

    fn create_dir_link_or_junction(link: &Path, target: &Path) -> bool {
        #[cfg(unix)]
        {
            std::os::unix::fs::symlink(target, link).is_ok()
        }
        #[cfg(windows)]
        {
            if std::os::windows::fs::symlink_dir(target, link).is_ok() {
                return true;
            }
            std::process::Command::new("cmd")
                .args(["/c", "mklink", "/J"])
                .arg(link)
                .arg(target)
                .output()
                .is_ok_and(|output| output.status.success())
        }
    }

    #[test]
    fn index_cache_confinement_no_clobber_gitignore_and_refuses_symlink() {
        let dir = tempdir().unwrap();
        let repo = dir.path().join("repo");
        std::fs::create_dir_all(repo.join(".git")).unwrap();

        let index_dir = ensure_cache_dir_and_gitignore(&repo).unwrap();
        assert!(index_dir.is_dir());
        let gitignore_path = repo.join(".tg_cache").join(".gitignore");
        assert_eq!(std::fs::read(&gitignore_path).unwrap(), b"*\n");

        // No-clobber: existing custom .gitignore is preserved.
        std::fs::write(&gitignore_path, b"*\n!keep\n").unwrap();
        ensure_cache_dir_and_gitignore(&repo).unwrap();
        assert_eq!(std::fs::read(&gitignore_path).unwrap(), b"*\n!keep\n");

        // Real symlink / Windows junction refusal on `.tg_cache`.
        let symlink_repo = dir.path().join("symlink_repo");
        let outside = dir.path().join("outside");
        std::fs::create_dir_all(symlink_repo.join(".git")).unwrap();
        std::fs::create_dir_all(outside.join("index_v1")).unwrap();
        std::fs::write(
            outside.join("index_v1").join("escaped.tg_index"),
            b"outside",
        )
        .unwrap();
        let cache_link = symlink_repo.join(".tg_cache");
        assert!(
            create_dir_link_or_junction(&cache_link, &outside),
            "test fixture must create a directory symlink or Windows junction"
        );

        assert!(ensure_cache_dir_and_gitignore(&symlink_repo).is_err());
        assert!(ensure_cache_dir_anchored(&symlink_repo).is_err());
        assert!(!outside.join(".gitignore").exists());
        let escaped_index = cache_link.join("index_v1").join("escaped.tg_index");
        assert!(open_confined_regular_file(&escaped_index).is_err());
        assert!(!confined_regular_file_exists(&escaped_index));
    }

    #[test]
    fn index_cache_confinement_refuses_symlink_or_junction_on_index_reads_and_writes() {
        let dir = tempdir().unwrap();
        let repo = dir.path().join("repo");
        let outside = dir.path().join("outside");
        std::fs::create_dir_all(repo.join(".git")).unwrap();
        std::fs::create_dir_all(&outside).unwrap();
        let outside_index = outside.join("outside.tg_index");
        std::fs::write(&outside_index, b"outside-index").unwrap();

        // Junction/symlink on legacy `<dir>/.tg_index` parent directory is refused.
        let linked_sub = repo.join("linked_sub");
        assert!(
            create_dir_link_or_junction(&linked_sub, &outside),
            "test fixture must create a directory symlink or Windows junction"
        );
        std::fs::write(outside.join(".tg_index"), b"legacy-outside").unwrap();
        let legacy_through_link = linked_sub.join(".tg_index");
        assert!(open_confined_regular_file(&legacy_through_link).is_err());
        assert!(!confined_regular_file_exists(&legacy_through_link));

        // File symlink on `.tg_cache/index_v1/<hash>.tg_index` leaf is refused when permitted.
        let index_dir = ensure_cache_dir_anchored(&repo).unwrap();
        let leaf_name = OsStr::new("leaf.tg_index");
        let leaf_path = index_dir.path().join(leaf_name);
        #[cfg(unix)]
        let leaf_linked = std::os::unix::fs::symlink(&outside_index, &leaf_path).is_ok();
        #[cfg(windows)]
        let leaf_linked = match std::os::windows::fs::symlink_file(&outside_index, &leaf_path) {
            Ok(()) => true,
            Err(err) if err.raw_os_error() == Some(1314) => false,
            Err(err) => panic!("unexpected symlink_file error: {err}"),
        };
        if leaf_linked {
            assert!(open_confined_regular_file(&leaf_path).is_err());
            assert!(!confined_regular_file_exists(&leaf_path));
            assert!(index_dir.target(leaf_name).is_err());
        }
    }

    #[test]
    fn index_cache_confinement_legacy_read_fallback_never_writes_legacy() {
        let dir = tempdir().unwrap();
        let repo = dir.path().join("repo");
        let sub = repo.join("src");
        std::fs::create_dir_all(repo.join(".git")).unwrap();
        std::fs::create_dir_all(&sub).unwrap();
        let legacy = sub.join(".tg_index");
        std::fs::write(&legacy, b"legacy").unwrap();

        let paths = resolve_index_paths(sub.to_str().unwrap());
        assert_eq!(paths.read_path, legacy);
        assert_ne!(paths.write_path, legacy);
        assert!(paths.write_path.starts_with(
            std::fs::canonicalize(&repo)
                .unwrap()
                .join(".tg_cache")
                .join("index_v1")
        ));
    }

    #[test]
    fn ensure_dir_anchored_creates_nested_dirs_and_refuses_intermediate_symlink() {
        let dir = tempdir().unwrap();
        let nested = dir.path().join("a").join("b").join("c");
        let handle = ensure_dir_anchored(&nested).unwrap();
        assert!(nested.is_dir());
        handle
            .target(OsStr::new("probe.txt"))
            .unwrap()
            .publish_atomic(b"ok")
            .unwrap();
        assert_eq!(std::fs::read(nested.join("probe.txt")).unwrap(), b"ok");

        let outside = dir.path().join("outside");
        std::fs::create_dir_all(&outside).unwrap();
        let link = dir.path().join("link_parent");
        assert!(
            create_dir_link_or_junction(&link, &outside),
            "test fixture must create a directory symlink or Windows junction"
        );
        let escaped_nested = link.join("created_inside_outside").join("leaf");
        assert!(ensure_dir_anchored(&escaped_nested).is_err());
        assert!(!outside.join("created_inside_outside").exists());
    }
}

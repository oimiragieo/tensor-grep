//! Anchored filesystem mutations shared by the binary and library.

use std::ffi::{OsStr, OsString};
#[cfg(windows)]
use std::fs::OpenOptions;
use std::fs::{File, Metadata};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::AtomicU64;
use std::sync::Mutex;

mod leaf_identity;
#[path = "safe_write/leaf_removal.rs"]
mod leaf_removal;
use leaf_identity::FileIdentity;

static TEMP_FILE_COUNTER: AtomicU64 = AtomicU64::new(0);

/// A validated target whose parent directory chain stays open through each operation.
pub struct AnchoredTarget {
    display_path: PathBuf,
    name: OsString,
    parent: AnchoredDirectory,
    leaf_identity: Mutex<Option<FileIdentity>>,
    pinned_leaf: Mutex<Option<File>>,
}

/// An atomic publication error reports whether the replacement became visible.
#[derive(Debug)]
pub struct AtomicPublishError {
    pub published: bool,
    pub error: anyhow::Error,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum AtomicPublishStage {
    CreateTemp,
    Write,
    Flush,
    Replace,
    Validated,
    ParentSync,
}

impl std::fmt::Display for AtomicPublishError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        self.error.fmt(formatter)
    }
}

impl std::error::Error for AtomicPublishError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        self.error.source()
    }
}

impl AnchoredTarget {
    /// Opens every parent without following reparse points and keeps those handles alive.
    pub fn open(path: &Path) -> anyhow::Result<Self> {
        let absolute = if path.is_absolute() {
            path.to_path_buf()
        } else {
            std::env::current_dir()?.join(path)
        };
        let name = absolute
            .file_name()
            .ok_or_else(|| anyhow::anyhow!("{} has no file name", path.display()))?
            .to_os_string();
        let parent_path = absolute
            .parent()
            .ok_or_else(|| anyhow::anyhow!("{} has no parent directory", path.display()))?;
        let target = Self {
            display_path: path.to_path_buf(),
            name,
            parent: AnchoredDirectory::open(parent_path)?,
            leaf_identity: Mutex::new(None),
            pinned_leaf: Mutex::new(None),
        };
        target.capture_leaf_identity()?;
        Ok(target)
    }

    fn capture_leaf_identity(&self) -> anyhow::Result<()> {
        #[cfg(target_os = "linux")]
        let (identity, pinned) = match self.open_leaf(libc::O_PATH, 0) {
            Ok(file) => {
                require_regular_file(&file.metadata()?, &self.display_path)?;
                (Some(leaf_identity::from_file(&file)?), Some(file))
            }
            Err(error)
                if error
                    .downcast_ref::<std::io::Error>()
                    .is_some_and(|error| error.kind() == std::io::ErrorKind::NotFound) =>
            {
                (None, None)
            }
            Err(error) => return Err(error),
        };
        #[cfg(all(unix, not(target_os = "linux")))]
        let (identity, pinned) = match self.open_leaf(libc::O_RDONLY, 0) {
            Ok(file) => {
                require_regular_file(&file.metadata()?, &self.display_path)?;
                (Some(leaf_identity::from_file(&file)?), Some(file))
            }
            Err(error)
                if error
                    .downcast_ref::<std::io::Error>()
                    .is_some_and(|error| error.kind() == std::io::ErrorKind::NotFound) =>
            {
                (None, None)
            }
            Err(error)
                if error
                    .downcast_ref::<std::io::Error>()
                    .is_some_and(|error| error.kind() == std::io::ErrorKind::PermissionDenied) =>
            {
                match self.open_leaf(libc::O_WRONLY, 0) {
                    Ok(file) => {
                        require_regular_file(&file.metadata()?, &self.display_path)?;
                        (Some(leaf_identity::from_file(&file)?), Some(file))
                    }
                    Err(error)
                        if error.downcast_ref::<std::io::Error>().is_some_and(|error| {
                            matches!(
                                error.kind(),
                                std::io::ErrorKind::PermissionDenied | std::io::ErrorKind::NotFound
                            )
                        }) =>
                    {
                        (self.leaf_identity_if_present()?, None)
                    }
                    Err(error) => return Err(error),
                }
            }
            Err(error) => return Err(error),
        };
        #[cfg(windows)]
        let (identity, pinned) = match self.open_leaf_metadata() {
            Ok(file) => {
                require_regular_file(&file.metadata()?, &self.display_path)?;
                (Some(leaf_identity::from_file(&file)?), Some(file))
            }
            Err(error)
                if error
                    .downcast_ref::<std::io::Error>()
                    .is_some_and(|error| error.kind() == std::io::ErrorKind::NotFound) =>
            {
                (None, None)
            }
            Err(error) => return Err(error),
        };
        #[cfg(not(any(unix, windows)))]
        let (identity, pinned) = (self.leaf_identity_if_present()?, None);
        let mut current = self
            .leaf_identity
            .lock()
            .map_err(|_| anyhow::anyhow!("target identity lock poisoned"))?;
        *current = identity;
        *self
            .pinned_leaf
            .lock()
            .map_err(|_| anyhow::anyhow!("target file handle lock poisoned"))? = pinned;
        Ok(())
    }

    fn expected_leaf_identity(&self) -> anyhow::Result<Option<FileIdentity>> {
        self.leaf_identity
            .lock()
            .map(|identity| *identity)
            .map_err(|_| anyhow::anyhow!("target identity lock poisoned"))
    }

    fn verify_opened_leaf_identity(
        &self,
        file: &File,
        expected: Option<FileIdentity>,
    ) -> anyhow::Result<FileIdentity> {
        let metadata = file.metadata()?;
        require_regular_file(&metadata, &self.display_path)?;
        let actual = leaf_identity::from_file(file)?;
        let pinned_leaf = self
            .pinned_leaf
            .lock()
            .map_err(|_| anyhow::anyhow!("target file handle lock poisoned"))?;
        if let Some(pinned) = pinned_leaf.as_ref() {
            if leaf_identity::from_file(pinned)? != actual {
                anyhow::bail!(
                    "refusing changed target object {}",
                    self.display_path.display()
                );
            }
        } else if expected.is_some() {
            anyhow::bail!(
                "refusing unpinned target identity {}",
                self.display_path.display()
            );
        }
        match expected {
            Some(expected) if expected != actual => anyhow::bail!(
                "refusing changed target identity {}",
                self.display_path.display()
            ),
            None => anyhow::bail!(
                "refusing target created after anchor {}",
                self.display_path.display()
            ),
            _ => Ok(actual),
        }
    }

    fn record_new_leaf_identity(&self, file: &File) -> anyhow::Result<()> {
        let metadata = file.metadata()?;
        require_regular_file(&metadata, &self.display_path)?;
        leaf_identity::require_single_link(file, &self.display_path)?;
        let identity = leaf_identity::from_file(file)?;
        let pinned = file.try_clone()?;
        let mut expected = self
            .leaf_identity
            .lock()
            .map_err(|_| anyhow::anyhow!("target identity lock poisoned"))?;
        if expected.is_some() {
            anyhow::bail!(
                "target identity changed while creating {}",
                self.display_path.display()
            );
        }
        *expected = Some(identity);
        *self
            .pinned_leaf
            .lock()
            .map_err(|_| anyhow::anyhow!("target file handle lock poisoned"))? = Some(pinned);
        Ok(())
    }

    /// Updates this target's identity after an atomic publisher replaced its leaf.
    pub(crate) fn update_leaf_identity_from_file(&self, file: &File) -> anyhow::Result<()> {
        let metadata = file.metadata()?;
        require_regular_file(&metadata, &self.display_path)?;
        let identity = leaf_identity::from_file(file)?;
        let pinned = self.open_published_leaf(file)?;
        let mut expected = self
            .leaf_identity
            .lock()
            .map_err(|_| anyhow::anyhow!("target identity lock poisoned"))?;
        *expected = Some(identity);
        *self
            .pinned_leaf
            .lock()
            .map_err(|_| anyhow::anyhow!("target file handle lock poisoned"))? = Some(pinned);
        Ok(())
    }

    fn open_published_leaf(&self, source: &File) -> anyhow::Result<File> {
        #[cfg(unix)]
        let pinned = source.try_clone()?;
        #[cfg(windows)]
        let pinned = {
            use std::os::windows::fs::OpenOptionsExt;
            OpenOptions::new()
                .read(true)
                .access_mode(0)
                .share_mode(windows::SHARE_READ | windows::SHARE_WRITE | windows::SHARE_DELETE)
                .custom_flags(windows::OPEN_REPARSE_POINT)
                .open(self.parent.path.join(&self.name))?
        };
        anyhow::ensure!(
            leaf_identity::from_file(&pinned)? == leaf_identity::from_file(source)?,
            "published target changed before its opened handle was retained"
        );
        Ok(pinned)
    }

    fn leaf_identity_if_present(&self) -> anyhow::Result<Option<FileIdentity>> {
        #[cfg(unix)]
        {
            use std::ffi::CString;
            use std::os::unix::ffi::OsStrExt;
            let name = CString::new(self.name.as_os_str().as_bytes())?;
            let mut metadata = unsafe { std::mem::zeroed::<libc::stat>() };
            let result = unsafe {
                libc::fstatat(
                    self.parent.raw_fd(),
                    name.as_ptr(),
                    &mut metadata,
                    libc::AT_SYMLINK_NOFOLLOW,
                )
            };
            if result == 0 {
                if metadata.st_mode & libc::S_IFMT != libc::S_IFREG {
                    anyhow::bail!(
                        "refusing non-regular or symlink target {}",
                        self.display_path.display()
                    );
                }
                return Ok(Some(FileIdentity {
                    device: metadata.st_dev as u64,
                    inode: metadata.st_ino as u64,
                }));
            }
            let error = std::io::Error::last_os_error();
            if error.kind() == std::io::ErrorKind::NotFound {
                Ok(None)
            } else {
                Err(error.into())
            }
        }
        #[cfg(windows)]
        {
            match self.open_leaf_metadata() {
                Ok(file) => {
                    let metadata = file.metadata()?;
                    require_regular_file(&metadata, &self.display_path)?;
                    Ok(Some(leaf_identity::from_file(&file)?))
                }
                Err(error)
                    if error
                        .downcast_ref::<std::io::Error>()
                        .is_some_and(|io_error| {
                            io_error.kind() == std::io::ErrorKind::NotFound
                        }) =>
                {
                    Ok(None)
                }
                Err(error) => Err(error),
            }
        }
        #[cfg(not(any(unix, windows)))]
        {
            anyhow::bail!("anchored file identity checks are unsupported on this platform")
        }
    }

    /// Reads a regular file through the anchored parent.
    pub fn read(&self) -> anyhow::Result<Vec<u8>> {
        let mut file = self.open_leaf_read()?;
        let metadata = file.metadata()?;
        require_regular_file(&metadata, &self.display_path)?;
        let mut bytes = Vec::new();
        file.read_to_end(&mut bytes)?;
        Ok(bytes)
    }

    /// Reads an anchored regular file while refusing content above `max_bytes`.
    pub fn read_bounded(&self, max_bytes: u64) -> anyhow::Result<Vec<u8>> {
        let file = self.open_leaf_read()?;
        let metadata = file.metadata()?;
        require_regular_file(&metadata, &self.display_path)?;
        if metadata.len() > max_bytes {
            anyhow::bail!(
                "file exceeds {max_bytes}-byte read limit: {}",
                self.display_path.display()
            );
        }
        let read_limit = max_bytes.saturating_add(1);
        let mut bytes = Vec::new();
        file.take(read_limit).read_to_end(&mut bytes)?;
        if bytes.len() as u64 > max_bytes {
            anyhow::bail!(
                "file exceeds {max_bytes}-byte read limit: {}",
                self.display_path.display()
            );
        }
        Ok(bytes)
    }

    /// Overwrites the opened regular leaf without following a leaf or parent reparse point.
    pub fn write_direct(&self, bytes: &[u8]) -> anyhow::Result<()> {
        let mut file = self.open_leaf_write()?;
        let metadata = file.metadata()?;
        require_regular_file(&metadata, &self.display_path)?;
        file.set_len(0)?;
        file.write_all(bytes)?;
        Ok(())
    }

    /// Copies a regular source file into this anchored leaf and retains its file permissions.
    pub fn copy_regular_file_from(&self, source: &Path) -> std::io::Result<()> {
        let source = open_regular_source(source)?;
        self.copy_opened_regular_file_from(source, u64::MAX)
            .map(|_| ())
    }

    /// Copies an already-opened regular source file without writing beyond `max_bytes`.
    pub fn copy_opened_regular_file_from(
        &self,
        mut source: File,
        max_bytes: u64,
    ) -> std::io::Result<u64> {
        let source_metadata = source.metadata()?;
        require_regular_file(&source_metadata, &self.display_path)
            .map_err(|error| std::io::Error::other(error.to_string()))?;
        if source_metadata.len() > max_bytes {
            return Err(std::io::Error::other(format!(
                "source exceeds the {max_bytes}-byte checkpoint copy limit"
            )));
        }
        let permissions = source_metadata.permissions();
        let mut destination = self
            .open_leaf_create_new()
            .map_err(|error| std::io::Error::other(error.to_string()))?;
        let metadata = destination.metadata()?;
        require_regular_file(&metadata, &self.display_path)
            .map_err(|error| std::io::Error::other(error.to_string()))?;
        destination.set_len(0)?;
        let copied = copy_with_limit(&mut source, &mut destination, max_bytes)?;
        #[cfg(windows)]
        windows::copy_file_dacl(&source, &destination)
            .map_err(|error| std::io::Error::other(error.to_string()))?;
        destination.set_permissions(permissions)?;
        Ok(copied)
    }

    /// Creates the leaf with exclusive, no-follow semantics under the retained parent.
    pub fn create_new_file(&self) -> std::io::Result<File> {
        #[cfg(unix)]
        {
            use std::os::unix::ffi::OsStrExt;
            use std::os::unix::io::FromRawFd;
            let name = std::ffi::CString::new(self.name.as_os_str().as_bytes())
                .map_err(|error| std::io::Error::new(std::io::ErrorKind::InvalidInput, error))?;
            let fd = unsafe {
                libc::openat(
                    self.parent.raw_fd(),
                    name.as_ptr(),
                    libc::O_WRONLY
                        | libc::O_CREAT
                        | libc::O_EXCL
                        | libc::O_CLOEXEC
                        | libc::O_NOFOLLOW,
                    0o666,
                )
            };
            if fd < 0 {
                return Err(std::io::Error::last_os_error());
            }
            let file = unsafe { File::from_raw_fd(fd) };
            self.record_new_leaf_identity(&file)
                .map_err(|error| std::io::Error::other(error.to_string()))?;
            Ok(file)
        }
        #[cfg(windows)]
        {
            use std::os::windows::fs::OpenOptionsExt;
            let file = OpenOptions::new()
                .write(true)
                .create_new(true)
                .share_mode(windows::SHARE_READ | windows::SHARE_WRITE | windows::SHARE_DELETE)
                .custom_flags(windows::OPEN_REPARSE_POINT)
                .open(self.parent.path.join(&self.name))?;
            self.record_new_leaf_identity(&file)
                .map_err(|error| std::io::Error::other(error.to_string()))?;
            Ok(file)
        }
        #[cfg(not(any(unix, windows)))]
        {
            Err(std::io::Error::new(
                std::io::ErrorKind::Unsupported,
                "anchored file creation is unsupported on this platform",
            ))
        }
    }

    /// Returns metadata for an anchored regular leaf.
    pub fn metadata(&self) -> anyhow::Result<Metadata> {
        let file = self.open_leaf_read()?;
        let metadata = file.metadata()?;
        require_regular_file(&metadata, &self.display_path)?;
        Ok(metadata)
    }

    /// Refreshes the anchored leaf's modification time without reopening its parent path.
    pub fn touch(&self) -> std::io::Result<()> {
        let file = self
            .open_leaf_write_existing()
            .map_err(|error| std::io::Error::other(error.to_string()))?;
        let metadata = file.metadata()?;
        require_regular_file(&metadata, &self.display_path)
            .map_err(|error| std::io::Error::other(error.to_string()))?;
        file.set_times(std::fs::FileTimes::new().set_modified(std::time::SystemTime::now()))
    }

    /// Creates a symlink leaf under the retained parent without replacing an existing entry.
    pub fn create_symlink(&self, target: &Path, is_directory: bool) -> std::io::Result<()> {
        #[cfg(unix)]
        {
            use std::os::unix::ffi::OsStrExt;
            let target = std::ffi::CString::new(target.as_os_str().as_bytes())
                .map_err(|error| std::io::Error::new(std::io::ErrorKind::InvalidInput, error))?;
            let name = std::ffi::CString::new(self.name.as_os_str().as_bytes())
                .map_err(|error| std::io::Error::new(std::io::ErrorKind::InvalidInput, error))?;
            let _ = is_directory;
            if unsafe { libc::symlinkat(target.as_ptr(), self.parent.raw_fd(), name.as_ptr()) } != 0
            {
                return Err(std::io::Error::last_os_error());
            }
            Ok(())
        }
        #[cfg(windows)]
        {
            if is_directory {
                std::os::windows::fs::symlink_dir(target, self.parent.path.join(&self.name))
            } else {
                std::os::windows::fs::symlink_file(target, self.parent.path.join(&self.name))
            }
        }
        #[cfg(not(any(unix, windows)))]
        {
            let _ = (target, is_directory);
            Err(std::io::Error::new(
                std::io::ErrorKind::Unsupported,
                "anchored symlink creation is unsupported on this platform",
            ))
        }
    }

    /// Atomically publishes relative to the retained parent after rechecking the anchored leaf.
    /// Existing-target replacement is name-based; see `docs/CONTRACTS.md` for its writer boundary.
    /// Unix syncs the parent directory; Windows has no portable directory-entry durability
    /// guarantee. Post-rename errors have `published == true`; callers must preserve the new object.
    pub fn publish_atomic(&self, bytes: &[u8]) -> Result<(), AtomicPublishError> {
        self.atomic_write_inner(bytes, |_| Ok(()), true, false, true, |_| Ok(()))
    }

    /// Atomically publishes the leaf while copying its existing mode or DACL when present.
    pub fn publish_atomic_preserving_permissions(
        &self,
        bytes: &[u8],
    ) -> Result<(), AtomicPublishError> {
        self.atomic_write_inner(bytes, |_| Ok(()), true, true, true, |_| Ok(()))
    }

    /// Publishes a new leaf only if the name is absent, then flushes the file and parent.
    /// This is used for checkpoint metadata inside a newly admitted checkpoint directory.
    #[doc(hidden)]
    pub fn publish_atomic_new(&self, bytes: &[u8]) -> Result<(), AtomicPublishError> {
        self.atomic_write_inner(bytes, |_| Ok(()), true, false, false, |_| Ok(()))
    }

    pub(crate) fn write_atomic_with_hook<F>(
        &self,
        bytes: &[u8],
        before_publish: F,
    ) -> anyhow::Result<()>
    where
        F: FnOnce(&Path) -> anyhow::Result<()>,
    {
        self.atomic_write_inner(bytes, before_publish, false, false, true, |_| Ok(()))
            .map_err(|failure| failure.error)
    }

    pub(crate) fn validate_leaf_matches_expected(&self) -> anyhow::Result<()> {
        let expected = self.expected_leaf_identity()?;
        let pinned_leaf = self
            .pinned_leaf
            .lock()
            .map_err(|_| anyhow::anyhow!("target file handle lock poisoned"))?;
        match (expected, pinned_leaf.as_ref()) {
            (Some(expected), Some(pinned)) if leaf_identity::from_file(pinned)? != expected => {
                anyhow::bail!(
                    "refusing changed pinned target identity {}",
                    self.display_path.display()
                );
            }
            (Some(_), None) => anyhow::bail!(
                "refusing unpinned target identity {}",
                self.display_path.display()
            ),
            (None, Some(_)) => anyhow::bail!(
                "refusing inconsistent pinned target identity {}",
                self.display_path.display()
            ),
            _ => {}
        }
        let actual = self.leaf_identity_if_present()?;
        anyhow::ensure!(
            expected == actual,
            "refusing changed target identity {}",
            self.display_path.display()
        );
        Ok(())
    }

    #[cfg(unix)]
    fn open_leaf_read(&self) -> anyhow::Result<File> {
        let file = self.open_leaf(libc::O_RDONLY, 0)?;
        self.verify_opened_leaf_identity(&file, self.expected_leaf_identity()?)?;
        Ok(file)
    }

    #[cfg(unix)]
    fn open_leaf_write(&self) -> anyhow::Result<File> {
        self.open_leaf_write_with_create(true)
    }

    #[cfg(unix)]
    fn open_leaf_write_existing(&self) -> anyhow::Result<File> {
        self.open_leaf_write_with_create(false)
    }

    #[cfg(unix)]
    fn open_leaf_create_new(&self) -> anyhow::Result<File> {
        anyhow::ensure!(
            self.expected_leaf_identity()?.is_none(),
            "refusing existing copy destination {}",
            self.display_path.display()
        );
        let file = self.open_leaf(libc::O_WRONLY | libc::O_CREAT | libc::O_EXCL, 0o600)?;
        self.record_new_leaf_identity(&file)?;
        Ok(file)
    }

    #[cfg(unix)]
    fn open_leaf_write_with_create(&self, create_missing: bool) -> anyhow::Result<File> {
        let expected = self.expected_leaf_identity()?;
        let file = match expected {
            Some(_) => self.open_leaf(libc::O_WRONLY, 0)?,
            None if create_missing => {
                self.open_leaf(libc::O_WRONLY | libc::O_CREAT | libc::O_EXCL, 0o666)?
            }
            None => anyhow::bail!(
                "refusing target that was absent when anchored: {}",
                self.display_path.display()
            ),
        };
        match expected {
            Some(_) => {
                self.verify_opened_leaf_identity(&file, expected)?;
                leaf_identity::require_single_link(&file, &self.display_path)?;
            }
            None => self.record_new_leaf_identity(&file)?,
        }
        Ok(file)
    }

    #[cfg(unix)]
    fn open_leaf(&self, access: libc::c_int, mode: libc::mode_t) -> anyhow::Result<File> {
        use std::ffi::CString;
        use std::os::unix::ffi::OsStrExt;
        use std::os::unix::io::FromRawFd;
        let name = CString::new(self.name.as_os_str().as_bytes())?;
        let flags = access | libc::O_CLOEXEC | libc::O_NOFOLLOW | libc::O_NONBLOCK;
        let fd = unsafe {
            libc::openat(
                self.parent.raw_fd(),
                name.as_ptr(),
                flags,
                mode as libc::c_uint,
            )
        };
        if fd < 0 {
            return Err(Into::into(std::io::Error::last_os_error()));
        }
        Ok(unsafe { File::from_raw_fd(fd) })
    }

    #[cfg(windows)]
    fn open_leaf_metadata(&self) -> anyhow::Result<File> {
        use std::os::windows::fs::OpenOptionsExt;
        OpenOptions::new()
            .read(true)
            .access_mode(0)
            .share_mode(windows::SHARE_READ | windows::SHARE_WRITE | windows::SHARE_DELETE)
            .custom_flags(windows::OPEN_REPARSE_POINT | windows::BACKUP_SEMANTICS)
            .open(self.parent.path.join(&self.name))
            .map_err(Into::into)
    }

    #[cfg(windows)]
    fn open_leaf_read(&self) -> anyhow::Result<File> {
        use std::os::windows::fs::OpenOptionsExt;
        let file = OpenOptions::new()
            .read(true)
            .share_mode(windows::SHARE_READ | windows::SHARE_WRITE | windows::SHARE_DELETE)
            .custom_flags(windows::OPEN_REPARSE_POINT | windows::BACKUP_SEMANTICS)
            .open(self.parent.path.join(&self.name))
            .map_err(anyhow::Error::from)?;
        self.verify_opened_leaf_identity(&file, self.expected_leaf_identity()?)?;
        Ok(file)
    }

    #[cfg(windows)]
    fn open_leaf_write(&self) -> anyhow::Result<File> {
        self.open_leaf_write_with_create(true)
    }

    #[cfg(windows)]
    fn open_leaf_write_existing(&self) -> anyhow::Result<File> {
        self.open_leaf_write_with_create(false)
    }

    #[cfg(windows)]
    fn open_leaf_create_new(&self) -> anyhow::Result<File> {
        use std::os::windows::fs::OpenOptionsExt;
        anyhow::ensure!(
            self.expected_leaf_identity()?.is_none(),
            "refusing existing copy destination {}",
            self.display_path.display()
        );
        let file = OpenOptions::new()
            .write(true)
            .access_mode(windows::GENERIC_WRITE | windows::WRITE_DAC | windows::READ_CONTROL)
            .create_new(true)
            .share_mode(windows::SHARE_READ | windows::SHARE_WRITE | windows::SHARE_DELETE)
            .custom_flags(windows::OPEN_REPARSE_POINT)
            .open(self.parent.path.join(&self.name))?;
        self.record_new_leaf_identity(&file)?;
        Ok(file)
    }

    #[cfg(windows)]
    fn open_leaf_write_with_create(&self, create_missing: bool) -> anyhow::Result<File> {
        use std::os::windows::fs::OpenOptionsExt;
        let expected = self.expected_leaf_identity()?;
        let mut options = OpenOptions::new();
        options
            .write(true)
            .share_mode(windows::SHARE_READ | windows::SHARE_WRITE | windows::SHARE_DELETE)
            .custom_flags(windows::OPEN_REPARSE_POINT);
        let file = match expected {
            Some(_) => options.open(self.parent.path.join(&self.name))?,
            None if create_missing => options
                .create_new(true)
                .open(self.parent.path.join(&self.name))?,
            None => anyhow::bail!(
                "refusing target that was absent when anchored: {}",
                self.display_path.display()
            ),
        };
        match expected {
            Some(_) => {
                self.verify_opened_leaf_identity(&file, expected)?;
                leaf_identity::require_single_link(&file, &self.display_path)?;
            }
            None => self.record_new_leaf_identity(&file)?,
        }
        Ok(file)
    }

    #[cfg(not(any(unix, windows)))]
    fn open_leaf_read(&self) -> anyhow::Result<File> {
        anyhow::bail!("anchored reads are unsupported on this platform")
    }

    #[cfg(not(any(unix, windows)))]
    fn open_leaf_write(&self) -> anyhow::Result<File> {
        anyhow::bail!("anchored writes are unsupported on this platform")
    }

    #[cfg(not(any(unix, windows)))]
    fn open_leaf_write_existing(&self) -> anyhow::Result<File> {
        anyhow::bail!("anchored writes are unsupported on this platform")
    }

    #[cfg(not(any(unix, windows)))]
    fn open_leaf_create_new(&self) -> anyhow::Result<File> {
        anyhow::bail!("anchored writes are unsupported on this platform")
    }

    fn atomic_write_inner<F, H>(
        &self,
        bytes: &[u8],
        before_publish: F,
        durable: bool,
        preserve_permissions: bool,
        replace_existing: bool,
        stage_hook: H,
    ) -> Result<(), AtomicPublishError>
    where
        F: FnOnce(&Path) -> anyhow::Result<()>,
        H: FnMut(AtomicPublishStage) -> anyhow::Result<()>,
    {
        let replace_existing = if replace_existing {
            match self.expected_leaf_identity() {
                Ok(Some(_)) => true,
                Ok(None) => false,
                Err(error) => {
                    return Err(AtomicPublishError {
                        published: false,
                        error,
                    })
                }
            }
        } else {
            false
        };
        #[cfg(unix)]
        {
            unix::atomic_write(
                self,
                bytes,
                before_publish,
                durable,
                preserve_permissions,
                replace_existing,
                stage_hook,
            )
        }
        #[cfg(windows)]
        {
            windows::atomic_write(
                self,
                bytes,
                before_publish,
                durable,
                preserve_permissions,
                replace_existing,
                stage_hook,
            )
        }
        #[cfg(not(any(unix, windows)))]
        {
            let _ = (
                bytes,
                before_publish,
                durable,
                preserve_permissions,
                replace_existing,
                stage_hook,
            );
            Err(AtomicPublishError {
                published: false,
                error: anyhow::anyhow!("anchored atomic writes are unsupported on this platform"),
            })
        }
    }

    #[cfg(unix)]
    fn copy_existing_permissions_to(&self, destination: &File) -> anyhow::Result<()> {
        let source = match self.open_leaf_read() {
            Ok(file) => file,
            Err(error)
                if error
                    .downcast_ref::<std::io::Error>()
                    .is_some_and(|io_error| io_error.kind() == std::io::ErrorKind::NotFound) =>
            {
                return Ok(())
            }
            Err(error) => return Err(error),
        };
        let metadata = source.metadata()?;
        require_regular_file(&metadata, &self.display_path)?;
        destination.set_permissions(metadata.permissions())?;
        Ok(())
    }
}

fn require_regular_file(metadata: &Metadata, path: &Path) -> anyhow::Result<()> {
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        if metadata.file_attributes() & windows::ATTRIBUTE_REPARSE_POINT != 0 {
            anyhow::bail!("refusing reparse-point target {}", path.display());
        }
    }
    if !metadata.is_file() {
        anyhow::bail!("refusing non-regular target {}", path.display());
    }
    Ok(())
}

fn copy_with_limit<R: Read, W: Write>(
    source: &mut R,
    destination: &mut W,
    max_bytes: u64,
) -> std::io::Result<u64> {
    let mut copied = 0u64;
    let mut buffer = [0u8; 64 * 1024];
    loop {
        let remaining = max_bytes - copied;
        let request = usize::try_from(remaining.saturating_add(1))
            .unwrap_or(usize::MAX)
            .min(buffer.len());
        let size = match source.read(&mut buffer[..request]) {
            Err(error) if error.kind() == std::io::ErrorKind::Interrupted => continue,
            result => result?,
        };
        if size == 0 {
            return Ok(copied);
        }
        if size as u64 > remaining {
            return Err(std::io::Error::other(format!(
                "source exceeds the {max_bytes}-byte checkpoint copy limit"
            )));
        }
        destination.write_all(&buffer[..size])?;
        copied += size as u64;
    }
}

fn open_regular_source(path: &Path) -> std::io::Result<File> {
    #[cfg(unix)]
    let file = {
        use std::os::unix::fs::OpenOptionsExt;
        std::fs::OpenOptions::new()
            .read(true)
            .custom_flags(libc::O_CLOEXEC | libc::O_NOFOLLOW)
            .open(path)?
    };
    #[cfg(windows)]
    let file = {
        use std::os::windows::fs::OpenOptionsExt;
        OpenOptions::new()
            .read(true)
            .custom_flags(windows::OPEN_REPARSE_POINT)
            .open(path)?
    };
    #[cfg(not(any(unix, windows)))]
    let file = {
        let _ = path;
        return Err(std::io::Error::new(
            std::io::ErrorKind::Unsupported,
            "anchored file copies are unsupported on this platform",
        ));
    };
    let metadata = file.metadata()?;
    require_regular_file(&metadata, path)
        .map_err(|error| std::io::Error::other(error.to_string()))?;
    Ok(file)
}

/// Preserves the existing path-based public helper while anchoring before its first mutation.
pub fn write_bytes_refuse_symlink(path: &Path, bytes: &[u8]) -> anyhow::Result<()> {
    AnchoredTarget::open(path)?.write_direct(bytes)
}

#[cfg(unix)]
pub struct AnchoredDirectory {
    path: PathBuf,
    handles: Vec<File>,
    owned_name: Option<OsString>,
}

#[cfg(unix)]
impl AnchoredDirectory {
    pub fn open(path: &Path) -> anyhow::Result<Self> {
        use std::ffi::CString;
        use std::os::unix::ffi::OsStrExt;
        use std::os::unix::io::{AsRawFd, FromRawFd};
        let mut handles = vec![File::open("/")?];
        for component in path.components() {
            let name = match component {
                std::path::Component::RootDir | std::path::Component::CurDir => continue,
                std::path::Component::Normal(value) => value,
                std::path::Component::ParentDir => std::ffi::OsStr::new(".."),
                std::path::Component::Prefix(_) => continue,
            };
            let name = CString::new(name.as_bytes())?;
            let fd = unsafe {
                libc::openat(
                    handles.last().unwrap().as_raw_fd(),
                    name.as_ptr(),
                    libc::O_RDONLY | libc::O_DIRECTORY | libc::O_CLOEXEC | libc::O_NOFOLLOW,
                )
            };
            if fd < 0 {
                return Err(Into::into(std::io::Error::last_os_error()));
            }
            handles.push(unsafe { File::from_raw_fd(fd) });
        }
        Ok(Self {
            path: path.to_path_buf(),
            handles,
            owned_name: None,
        })
    }

    fn raw_fd(&self) -> std::os::unix::io::RawFd {
        use std::os::unix::io::AsRawFd;
        self.handles.last().unwrap().as_raw_fd()
    }

    pub fn sync(&self) -> std::io::Result<()> {
        self.handles.last().unwrap().sync_all()
    }
}

#[cfg(windows)]
pub struct AnchoredDirectory {
    path: PathBuf,
    handles: Vec<File>,
    owned_name: Option<OsString>,
}

#[cfg(windows)]
impl AnchoredDirectory {
    pub fn open(path: &Path) -> anyhow::Result<Self> {
        use std::path::Component;
        let mut current = PathBuf::new();
        let mut handles = Vec::new();
        for component in path.components() {
            current.push(component.as_os_str());
            match component {
                Component::Prefix(_) | Component::CurDir => continue,
                Component::RootDir | Component::ParentDir | Component::Normal(_) => {
                    let handle = windows::open_directory(&current)?;
                    handles.push(handle);
                }
            }
        }
        if handles.is_empty() {
            anyhow::bail!("{} is not an absolute directory path", path.display());
        }
        Ok(Self {
            path: path.to_path_buf(),
            handles,
            owned_name: None,
        })
    }

    pub fn sync(&self) -> std::io::Result<()> {
        // Windows does not provide a portable directory-entry flush guarantee for ordinary filesystems.
        Ok(())
    }
}

#[cfg(not(any(unix, windows)))]
pub struct AnchoredDirectory {
    path: PathBuf,
    owned_name: Option<OsString>,
}

#[cfg(not(any(unix, windows)))]
impl AnchoredDirectory {
    pub fn open(_path: &Path) -> anyhow::Result<Self> {
        anyhow::bail!("anchored writes are unsupported on this platform")
    }

    pub fn sync(&self) -> std::io::Result<()> {
        Err(std::io::Error::new(
            std::io::ErrorKind::Unsupported,
            "anchored writes are unsupported on this platform",
        ))
    }
}

impl AnchoredDirectory {
    pub fn try_clone(&self) -> anyhow::Result<Self> {
        anchored_directory::clone(self)
    }

    /// Opens one child directory without following a symlink or reparse point.
    pub fn open_child(&self, name: &OsStr) -> anyhow::Result<Self> {
        require_child_name(name)?;
        anchored_directory::open_child_directory(self, name)
    }

    /// Reads a symlink target relative to this retained directory, or returns `None` for other
    /// leaf types. Unsupported or changed leaves fail closed.
    pub fn read_link(&self, name: &OsStr) -> std::io::Result<Option<PathBuf>> {
        require_child_name(name).map_err(|error| {
            std::io::Error::new(std::io::ErrorKind::InvalidInput, error.to_string())
        })?;
        anchored_directory::read_link_child(self, name)
    }

    /// Opens a regular file relative to this retained directory without following a reparse leaf.
    pub fn open_regular_file(&self, name: &OsStr) -> std::io::Result<File> {
        let target = self
            .target(name)
            .map_err(|error| std::io::Error::other(error.to_string()))?;
        target
            .open_leaf_read()
            .map_err(|error| std::io::Error::other(error.to_string()))
    }

    /// Returns a child directory while keeping the parent chain anchored.
    pub fn open_or_create_child(&self, name: &OsStr) -> anyhow::Result<Self> {
        require_child_name(name)?;
        anchored_directory::open_or_create_child(self, name)
    }

    /// Creates a new child directory exclusively and returns its retained owner handle.
    pub fn create_child(&self, name: &OsStr) -> anyhow::Result<Self> {
        require_child_name(name)?;
        anchored_directory::create_child(self, name)
    }

    /// Opens a child directory for anchored recursive removal.
    pub fn child_for_removal(&self, name: &OsStr) -> anyhow::Result<Self> {
        require_child_name(name)?;
        anchored_directory::child_for_removal(self, name)
    }

    /// Builds a file target directly from this retained directory handle.
    pub fn target(&self, name: &OsStr) -> anyhow::Result<AnchoredTarget> {
        require_child_name(name)?;
        let target = AnchoredTarget {
            display_path: self.path.join(name),
            name: name.to_os_string(),
            parent: anchored_directory::clone(self)?,
            leaf_identity: Mutex::new(None),
            pinned_leaf: Mutex::new(None),
        };
        target.capture_leaf_identity()?;
        Ok(target)
    }

    /// Removes this owned child tree through retained handles and no-follow traversal.
    pub fn remove_owned_tree(&self) -> anyhow::Result<()> {
        let name = self
            .owned_name
            .as_deref()
            .ok_or_else(|| anyhow::anyhow!("refusing to remove a directory without ownership"))?;
        anchored_directory::remove_owned_tree(self, name)
    }

    pub fn path(&self) -> &Path {
        &self.path
    }

    /// Refuses if the path now resolves to a different directory or through a reparse point.
    pub fn validate_current(&self) -> anyhow::Result<()> {
        anchored_directory::validate_current(self)
    }
}

fn require_child_name(name: &OsStr) -> anyhow::Result<()> {
    let mut components = Path::new(name).components();
    anyhow::ensure!(
        matches!(components.next(), Some(std::path::Component::Normal(_)))
            && components.next().is_none(),
        "directory child name must be one normal path component"
    );
    Ok(())
}

#[path = "safe_write/anchored_directory.rs"]
mod anchored_directory;

#[cfg(unix)]
mod unix {
    use super::{AnchoredTarget, AtomicPublishError, AtomicPublishStage};
    use anyhow::Context;
    use std::ffi::CString;
    use std::io::Write;
    use std::os::unix::ffi::OsStrExt;
    use std::path::Path;

    pub(super) fn atomic_write<F, H>(
        target: &AnchoredTarget,
        bytes: &[u8],
        before_publish: F,
        durable: bool,
        preserve_permissions: bool,
        replace_existing: bool,
        mut stage_hook: H,
    ) -> Result<(), AtomicPublishError>
    where
        F: FnOnce(&Path) -> anyhow::Result<()>,
        H: FnMut(AtomicPublishStage) -> anyhow::Result<()>,
    {
        let parent_fd = target.parent.raw_fd();
        let (temp_name, temp_path, mut temp_file) = match create_temp(target) {
            Ok(temp) => temp,
            Err(error) => {
                return Err(AtomicPublishError {
                    published: false,
                    error: error.context(format!(
                        "failed to create temp file alongside {}",
                        target.display_path.display()
                    )),
                })
            }
        };
        let mut published = false;
        let result = (|| -> anyhow::Result<()> {
            stage_hook(AtomicPublishStage::CreateTemp)?;
            temp_file
                .write_all(bytes)
                .with_context(|| format!("failed to write temp file {}", temp_path.display()))?;
            stage_hook(AtomicPublishStage::Write)?;
            temp_file
                .flush()
                .with_context(|| format!("failed to flush temp file {}", temp_path.display()))?;
            stage_hook(AtomicPublishStage::Flush)?;
            if preserve_permissions {
                target.copy_existing_permissions_to(&temp_file)?;
            }
            if durable {
                temp_file.sync_all()?;
            }
            before_publish(&temp_path)?;
            stage_hook(AtomicPublishStage::Replace)?;
            target.validate_leaf_matches_expected()?;
            stage_hook(AtomicPublishStage::Validated)?;
            let name = CString::new(target.name.as_os_str().as_bytes())?;
            let temp = CString::new(temp_name.as_os_str().as_bytes())?;
            if replace_existing {
                if unsafe { libc::renameat(parent_fd, temp.as_ptr(), parent_fd, name.as_ptr()) }
                    != 0
                {
                    return Err(std::io::Error::last_os_error()).with_context(|| {
                        format!(
                            "failed to rename temp file {} to {}",
                            temp_path.display(),
                            target.display_path.display()
                        )
                    });
                }
                published = true;
            } else {
                // A same-directory hard link is an atomic no-replace publication. If the name
                // already exists, linkat fails without changing it; unsupported hard links fail
                // closed instead of falling back to replacement.
                if unsafe { libc::linkat(parent_fd, temp.as_ptr(), parent_fd, name.as_ptr(), 0) }
                    != 0
                {
                    return Err(std::io::Error::last_os_error()).with_context(|| {
                        format!(
                            "failed to publish new file {} without replacing an existing leaf",
                            target.display_path.display()
                        )
                    });
                }
                published = true;
                if unsafe { libc::unlinkat(parent_fd, temp.as_ptr(), 0) } != 0 {
                    return Err(std::io::Error::last_os_error()).with_context(|| {
                        format!(
                            "published {} but could not unlink its temporary name",
                            target.display_path.display()
                        )
                    });
                }
            }
            target.update_leaf_identity_from_file(&temp_file)?;
            if durable {
                stage_hook(AtomicPublishStage::ParentSync)?;
                target.parent.sync()?;
            }
            Ok(())
        })();
        if let Err(error) = result {
            if !published {
                let temp = CString::new(temp_name.as_os_str().as_bytes()).unwrap();
                unsafe { libc::unlinkat(parent_fd, temp.as_ptr(), 0) };
            }
            return Err(AtomicPublishError { published, error });
        }
        Ok(())
    }

    fn create_temp(
        target: &AnchoredTarget,
    ) -> anyhow::Result<(std::ffi::OsString, std::path::PathBuf, std::fs::File)> {
        use std::os::unix::io::FromRawFd;
        for _ in 0..64 {
            let nonce = super::TEMP_FILE_COUNTER.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
            let name: std::ffi::OsString =
                format!(".tg_tmp_{:x}_{nonce:x}", std::process::id()).into();
            let c_name = CString::new(name.as_os_str().as_bytes()).unwrap();
            let fd = unsafe {
                libc::openat(
                    target.parent.raw_fd(),
                    c_name.as_ptr(),
                    libc::O_WRONLY
                        | libc::O_CREAT
                        | libc::O_EXCL
                        | libc::O_CLOEXEC
                        | libc::O_NOFOLLOW,
                    0o666,
                )
            };
            if fd >= 0 {
                let path = target.parent.path.join(&name);
                return Ok((name, path, unsafe { std::fs::File::from_raw_fd(fd) }));
            }
            let error = std::io::Error::last_os_error();
            if error.kind() != std::io::ErrorKind::AlreadyExists {
                return Err(error.into());
            }
        }
        anyhow::bail!(
            "failed to allocate temporary file alongside {}",
            target.display_path.display()
        )
    }
}

#[cfg(windows)]
#[path = "safe_write/windows.rs"]
mod windows;

#[cfg(test)]
#[path = "safe_write/tests.rs"]
mod tests;

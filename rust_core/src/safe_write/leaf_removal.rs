use super::{leaf_identity, AnchoredTarget, File, FileIdentity};
#[cfg(unix)]
use super::{require_regular_file, TEMP_FILE_COUNTER};
#[cfg(unix)]
use std::ffi::OsString;
#[cfg(windows)]
use std::fs::OpenOptions;

impl AnchoredTarget {
    /// Opens or exclusively creates the persistent OS-lock sidecar beside this target.
    pub(crate) fn open_os_lock_file(&self) -> std::io::Result<File> {
        #[cfg(unix)]
        {
            use std::os::unix::ffi::OsStrExt;
            use std::os::unix::io::FromRawFd;
            let expected = self
                .expected_leaf_identity()
                .map_err(|error| std::io::Error::other(error.to_string()))?;
            let name = std::ffi::CString::new(self.name.as_os_str().as_bytes())
                .map_err(|error| std::io::Error::new(std::io::ErrorKind::InvalidInput, error))?;
            let mut flags = libc::O_RDWR | libc::O_CLOEXEC | libc::O_NOFOLLOW | libc::O_NONBLOCK;
            if expected.is_none() {
                flags |= libc::O_CREAT | libc::O_EXCL;
            }
            let fd = unsafe { libc::openat(self.parent.raw_fd(), name.as_ptr(), flags, 0o600) };
            if fd < 0 {
                return Err(std::io::Error::last_os_error());
            }
            let file = unsafe { File::from_raw_fd(fd) };
            match expected {
                Some(expected) => {
                    let actual = self
                        .verify_opened_leaf_identity(&file, Some(expected))
                        .map_err(|error| std::io::Error::other(error.to_string()))?;
                    leaf_identity::require_single_link(&file, &self.display_path)
                        .map_err(|error| std::io::Error::other(error.to_string()))?;
                    if actual != expected {
                        return Err(std::io::Error::other("OS lock sidecar identity changed"));
                    }
                }
                None => self
                    .record_new_leaf_identity(&file)
                    .map_err(|error| std::io::Error::other(error.to_string()))?,
            }
            Ok(file)
        }
        #[cfg(windows)]
        {
            use std::os::windows::fs::OpenOptionsExt;
            let expected = self
                .expected_leaf_identity()
                .map_err(|error| std::io::Error::other(error.to_string()))?;
            let mut options = OpenOptions::new();
            options
                .read(true)
                .write(true)
                .share_mode(super::windows::SHARE_READ | super::windows::SHARE_WRITE)
                .custom_flags(super::windows::OPEN_REPARSE_POINT);
            let file = if expected.is_some() {
                options.open(self.parent.path.join(&self.name))?
            } else {
                options
                    .create_new(true)
                    .open(self.parent.path.join(&self.name))?
            };
            match expected {
                Some(expected) => {
                    self.verify_opened_leaf_identity(&file, Some(expected))
                        .map_err(|error| std::io::Error::other(error.to_string()))?;
                    leaf_identity::require_single_link(&file, &self.display_path)
                        .map_err(|error| std::io::Error::other(error.to_string()))?;
                }
                None => self
                    .record_new_leaf_identity(&file)
                    .map_err(|error| std::io::Error::other(error.to_string()))?,
            }
            Ok(file)
        }
        #[cfg(not(any(unix, windows)))]
        {
            Err(std::io::Error::new(
                std::io::ErrorKind::Unsupported,
                "anchored OS lock sidecars are unsupported on this platform",
            ))
        }
    }

    /// Removes the anchored leaf itself without following it or any parent component.
    pub fn remove_file(&self) -> std::io::Result<()> {
        self.remove_file_inner(|| Ok(()))
    }

    #[cfg(test)]
    pub(crate) fn remove_file_with_hook<F>(&self, after_identity_check: F) -> std::io::Result<()>
    where
        F: FnOnce() -> std::io::Result<()>,
    {
        self.remove_file_inner(after_identity_check)
    }

    fn remove_file_inner<F>(&self, after_identity_check: F) -> std::io::Result<()>
    where
        F: FnOnce() -> std::io::Result<()>,
    {
        if self
            .expected_leaf_identity()
            .map_err(|error| std::io::Error::other(error.to_string()))?
            .is_some()
            && self
                .pinned_leaf
                .lock()
                .map_err(|_| std::io::Error::other("target file handle lock poisoned"))?
                .is_none()
        {
            return Err(std::io::Error::other("refusing unpinned target identity"));
        }
        #[cfg(unix)]
        {
            self.remove_file_unix(after_identity_check)
        }
        #[cfg(windows)]
        {
            self.remove_file_windows(after_identity_check)
        }
        #[cfg(not(any(unix, windows)))]
        {
            let _ = after_identity_check;
            Err(std::io::Error::new(
                std::io::ErrorKind::Unsupported,
                "anchored file removal is unsupported on this platform",
            ))
        }
    }

    #[cfg(unix)]
    fn remove_file_unix<F>(&self, after_identity_check: F) -> std::io::Result<()>
    where
        F: FnOnce() -> std::io::Result<()>,
    {
        use std::ffi::CString;
        use std::os::unix::ffi::OsStrExt;
        use std::os::unix::io::FromRawFd;

        let expected = self
            .expected_leaf_identity()
            .map_err(|error| std::io::Error::other(error.to_string()))?;
        let name = CString::new(self.name.as_os_str().as_bytes())
            .map_err(|error| std::io::Error::new(std::io::ErrorKind::InvalidInput, error))?;
        let Some(expected_identity) = expected else {
            if self
                .leaf_identity_if_present()
                .map_err(|error| std::io::Error::other(error.to_string()))?
                .is_some()
            {
                return Err(std::io::Error::other(format!(
                    "refusing changed target identity {}",
                    self.display_path.display()
                )));
            }
            return Ok(());
        };

        for _ in 0..64 {
            let nonce = TEMP_FILE_COUNTER.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
            let quarantine_name =
                OsString::from(format!(".tg_remove_{:x}_{nonce:x}", std::process::id()));
            let quarantine = CString::new(quarantine_name.as_os_str().as_bytes())
                .map_err(|error| std::io::Error::new(std::io::ErrorKind::InvalidInput, error))?;
            match rename_noreplace_at(self.parent.raw_fd(), &name, &quarantine) {
                Ok(()) => {}
                Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => continue,
                Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
                    self.clear_leaf_identity(Some(expected_identity))
                        .map_err(|error| std::io::Error::other(error.to_string()))?;
                    return Ok(());
                }
                Err(error) => return Err(error),
            }

            let fd = unsafe {
                libc::openat(
                    self.parent.raw_fd(),
                    quarantine.as_ptr(),
                    libc::O_RDONLY | libc::O_CLOEXEC | libc::O_NOFOLLOW | libc::O_NONBLOCK,
                    0,
                )
            };
            let moved = if fd < 0 {
                Err(std::io::Error::last_os_error())
            } else {
                Ok(unsafe { File::from_raw_fd(fd) })
            };
            let actual_identity = match moved {
                Ok(file) => {
                    let identity = (|| -> std::io::Result<FileIdentity> {
                        let metadata = file.metadata()?;
                        require_regular_file(&metadata, &self.display_path)
                            .map_err(|error| std::io::Error::other(error.to_string()))?;
                        leaf_identity::require_single_link(&file, &self.display_path)
                            .map_err(|error| std::io::Error::other(error.to_string()))?;
                        leaf_identity::from_file(&file)
                            .map_err(|error| std::io::Error::other(error.to_string()))
                    })();
                    match identity {
                        Ok(identity) => identity,
                        Err(error) => {
                            return Err(restore_quarantined_leaf(
                                self.parent.raw_fd(),
                                &quarantine,
                                &name,
                                error,
                            ));
                        }
                    }
                }
                Err(error) => {
                    return Err(restore_quarantined_leaf(
                        self.parent.raw_fd(),
                        &quarantine,
                        &name,
                        error,
                    ));
                }
            };
            let pinned_identity = self
                .pinned_leaf
                .lock()
                .map_err(|_| std::io::Error::other("target file handle lock poisoned"))?
                .as_ref()
                .map(leaf_identity::from_file)
                .transpose()
                .map_err(|error| std::io::Error::other(error.to_string()))?;
            if actual_identity != expected_identity || pinned_identity != Some(actual_identity) {
                return Err(restore_quarantined_leaf(
                    self.parent.raw_fd(),
                    &quarantine,
                    &name,
                    std::io::Error::other(format!(
                        "refusing changed target identity {}",
                        self.display_path.display()
                    )),
                ));
            }

            if let Err(error) = after_identity_check() {
                return Err(restore_quarantined_leaf(
                    self.parent.raw_fd(),
                    &quarantine,
                    &name,
                    error,
                ));
            }
            if unsafe { libc::unlinkat(self.parent.raw_fd(), quarantine.as_ptr(), 0) } != 0 {
                return Err(std::io::Error::last_os_error());
            }
            self.clear_leaf_identity(Some(expected_identity))
                .map_err(|error| std::io::Error::other(error.to_string()))?;
            return Ok(());
        }
        Err(std::io::Error::new(
            std::io::ErrorKind::AlreadyExists,
            "could not allocate a no-replace quarantine name for anchored removal",
        ))
    }

    #[cfg(windows)]
    fn remove_file_windows<F>(&self, after_identity_check: F) -> std::io::Result<()>
    where
        F: FnOnce() -> std::io::Result<()>,
    {
        let expected = self
            .expected_leaf_identity()
            .map_err(|error| std::io::Error::other(error.to_string()))?;
        let actual = self
            .leaf_identity_if_present()
            .map_err(|error| std::io::Error::other(error.to_string()))?;
        if expected != actual {
            if expected.is_some() && actual.is_none() {
                self.clear_leaf_identity(expected)
                    .map_err(|error| std::io::Error::other(error.to_string()))?;
                return Ok(());
            }
            return Err(std::io::Error::other(format!(
                "refusing changed target identity {}",
                self.display_path.display()
            )));
        }
        let Some(expected_identity) = expected else {
            return Ok(());
        };
        after_identity_check()?;
        let file = super::windows::open_target_for_delete(self)
            .map_err(|error| std::io::Error::other(error.to_string()))?;
        self.verify_opened_leaf_identity(&file, Some(expected_identity))
            .map_err(|error| std::io::Error::other(error.to_string()))?;
        leaf_identity::require_single_link(&file, &self.display_path)
            .map_err(|error| std::io::Error::other(error.to_string()))?;
        super::windows::delete_opened_file(&file)?;
        self.clear_leaf_identity(Some(expected_identity))
            .map_err(|error| std::io::Error::other(error.to_string()))?;
        Ok(())
    }

    fn clear_leaf_identity(&self, expected: Option<FileIdentity>) -> anyhow::Result<()> {
        let mut current = self
            .leaf_identity
            .lock()
            .map_err(|_| anyhow::anyhow!("target identity lock poisoned"))?;
        anyhow::ensure!(
            *current == expected,
            "target identity changed while removing {}",
            self.display_path.display()
        );
        *current = None;
        *self
            .pinned_leaf
            .lock()
            .map_err(|_| anyhow::anyhow!("target file handle lock poisoned"))? = None;
        Ok(())
    }
}
#[cfg(unix)]
#[cfg(target_os = "macos")]
unsafe extern "C" {
    fn renameatx_np(
        from_fd: libc::c_int,
        from: *const libc::c_char,
        to_fd: libc::c_int,
        to: *const libc::c_char,
        flags: libc::c_uint,
    ) -> libc::c_int;
}

#[cfg(unix)]
fn rename_noreplace_at(
    parent_fd: std::os::unix::io::RawFd,
    source: &std::ffi::CString,
    destination: &std::ffi::CString,
) -> std::io::Result<()> {
    #[cfg(target_os = "linux")]
    let result = unsafe {
        libc::renameat2(
            parent_fd,
            source.as_ptr(),
            parent_fd,
            destination.as_ptr(),
            libc::RENAME_NOREPLACE,
        )
    };
    #[cfg(target_os = "macos")]
    let result = unsafe {
        const RENAME_EXCL: libc::c_uint = 0x0000_0004;
        renameatx_np(
            parent_fd,
            source.as_ptr(),
            parent_fd,
            destination.as_ptr(),
            RENAME_EXCL,
        )
    };
    #[cfg(not(any(target_os = "linux", target_os = "macos")))]
    {
        let _ = (parent_fd, source, destination);
        return Err(std::io::Error::new(
            std::io::ErrorKind::Unsupported,
            "no-replace anchored rename is unsupported on this Unix platform",
        ));
    }
    if result == 0 {
        return Ok(());
    }
    let error = std::io::Error::last_os_error();
    #[cfg(target_os = "linux")]
    if matches!(error.raw_os_error(), Some(libc::ENOSYS | libc::EINVAL)) {
        return Err(std::io::Error::new(
            std::io::ErrorKind::Unsupported,
            format!("filesystem does not support no-replace anchored rename: {error}"),
        ));
    }
    Err(error)
}

#[cfg(unix)]
fn restore_quarantined_leaf(
    parent_fd: std::os::unix::io::RawFd,
    quarantine: &std::ffi::CString,
    name: &std::ffi::CString,
    cause: std::io::Error,
) -> std::io::Error {
    match rename_noreplace_at(parent_fd, quarantine, name) {
        Ok(()) => cause,
        Err(restore) => std::io::Error::other(format!(
            "{cause}; quarantined leaf could not be restored safely ({restore})"
        )),
    }
}

use std::fs::{File, OpenOptions};
use std::path::Path;

/// Open a search input without following a leaf link or reparse point.
pub(crate) fn open_search_file_nofollow(path: &Path) -> anyhow::Result<File> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        let file = OpenOptions::new()
            .read(true)
            .custom_flags(libc::O_CLOEXEC | libc::O_NOFOLLOW | libc::O_NONBLOCK)
            .open(path)?;
        if !file.metadata()?.is_file() {
            anyhow::bail!("refusing non-regular search target {}", path.display());
        }
        Ok(file)
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::{MetadataExt, OpenOptionsExt};
        const OPEN_REPARSE_POINT: u32 = 0x0020_0000;
        const ATTRIBUTE_REPARSE_POINT: u32 = 0x0000_0400;
        let file = OpenOptions::new()
            .read(true)
            .custom_flags(OPEN_REPARSE_POINT)
            .open(path)?;
        let metadata = file.metadata()?;
        if metadata.file_attributes() & ATTRIBUTE_REPARSE_POINT != 0 || !metadata.is_file() {
            anyhow::bail!("refusing non-regular search target {}", path.display());
        }
        Ok(file)
    }
    #[cfg(not(any(unix, windows)))]
    {
        let _ = path;
        anyhow::bail!("no-follow search open is unsupported on this platform");
    }
}

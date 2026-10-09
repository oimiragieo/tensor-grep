//! Keep Windows library names bound to verified objects while loading and scoring.

#[cfg(windows)]
use anyhow::bail;
use anyhow::Result;
use std::fs::File;
use std::path::Path;

#[cfg(windows)]
pub fn pin(runtime: &Path) -> Result<Vec<File>> {
    use std::fs::OpenOptions;
    use std::os::windows::fs::{MetadataExt, OpenOptionsExt};
    let mut files = Vec::new();
    let parent = runtime
        .parent()
        .ok_or_else(|| anyhow::anyhow!("missing runtime parent"))?;
    let mut directories: Vec<_> = parent.ancestors().collect();
    directories.reverse();
    for directory in directories {
        let handle = OpenOptions::new()
            .read(true)
            .access_mode(0)
            .share_mode(3)
            .custom_flags(0x02200000)
            .open(directory)?;
        let metadata = handle.metadata()?;
        if !metadata.is_dir() || metadata.file_attributes() & 0x400 != 0 {
            bail!("cross-encoder runtime parent is linked or not a directory");
        }
        files.push(handle);
    }
    for &(name, _, _) in crate::cross_encoder_pins::LIBRARIES {
        let handle = OpenOptions::new()
            .read(true)
            .share_mode(1)
            .custom_flags(0x00200000)
            .open(parent.join(name))?;
        let metadata = handle.metadata()?;
        if !metadata.is_file() || metadata.file_attributes() & 0x400 != 0 {
            bail!("cross-encoder runtime leaf is linked or not a file");
        }
        files.push(handle);
    }
    Ok(files)
}

#[cfg(not(windows))]
pub fn pin(_runtime: &Path) -> Result<Vec<File>> {
    Ok(Vec::new())
}

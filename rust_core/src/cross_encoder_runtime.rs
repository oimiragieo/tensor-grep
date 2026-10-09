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

#[cfg(windows)]
#[link(name = "kernel32")]
unsafe extern "system" {
    fn LoadLibraryExW(
        path: *const u16,
        reserved: *mut std::ffi::c_void,
        flags: u32,
    ) -> *mut std::ffi::c_void;
    fn FreeLibrary(module: *mut std::ffi::c_void) -> i32;
}

#[cfg(windows)]
pub struct LoadedLibraries(Vec<usize>);

#[cfg(windows)]
impl Drop for LoadedLibraries {
    fn drop(&mut self) {
        for &handle in self.0.iter().rev() {
            unsafe { FreeLibrary(handle as *mut std::ffi::c_void) };
        }
    }
}

#[cfg(windows)]
pub fn preload(runtime: &Path) -> Result<LoadedLibraries> {
    use std::os::windows::ffi::OsStrExt;
    let parent = runtime
        .parent()
        .ok_or_else(|| anyhow::anyhow!("missing runtime parent"))?;
    let mut libraries = LoadedLibraries(Vec::new());
    // All these files have been pinned and checksummed by the caller. Preload the
    // provider before the main library, restricting every dependency to System32.
    // ORT's later same-path load reuses these images; cwd/PATH DLLs are never searched.
    for &(name, _, _) in crate::cross_encoder_pins::LIBRARIES.iter().rev() {
        let mut path: Vec<u16> = parent.join(name).as_os_str().encode_wide().collect();
        if path.contains(&0) {
            bail!("invalid runtime library path");
        }
        path.push(0);
        let handle = unsafe { LoadLibraryExW(path.as_ptr(), std::ptr::null_mut(), 0x800) };
        if handle.is_null() {
            bail!(
                "safe ONNX loader failed (requires system Visual C++ runtime): {}",
                std::io::Error::last_os_error()
            );
        }
        libraries.0.push(handle as usize);
    }
    Ok(libraries)
}

use std::fs::File;
use std::path::Path;

#[cfg(unix)]
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(super) struct FileIdentity {
    pub(super) device: u64,
    pub(super) inode: u64,
}

#[cfg(windows)]
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(super) struct FileIdentity {
    pub(super) volume_serial: u64,
    pub(super) file_id: [u8; 16],
}

#[cfg(not(any(unix, windows)))]
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(super) struct FileIdentity;

#[cfg(unix)]
pub(super) fn from_file(file: &File) -> anyhow::Result<FileIdentity> {
    use std::os::unix::fs::MetadataExt;
    let metadata = file.metadata()?;
    Ok(FileIdentity {
        device: metadata.dev(),
        inode: metadata.ino(),
    })
}

#[cfg(windows)]
pub(super) fn from_file(file: &File) -> anyhow::Result<FileIdentity> {
    Ok(windows_file_identity(file)?)
}

#[cfg(not(any(unix, windows)))]
pub(super) fn from_file(_file: &File) -> anyhow::Result<FileIdentity> {
    anyhow::bail!("anchored file identity checks are unsupported on this platform")
}

#[cfg(windows)]
#[repr(C)]
#[derive(Default)]
struct WindowsFileTime {
    low: u32,
    high: u32,
}

#[cfg(windows)]
#[repr(C)]
#[derive(Default)]
struct WindowsFileInformation {
    _attributes: u32,
    _creation_time: WindowsFileTime,
    _access_time: WindowsFileTime,
    _write_time: WindowsFileTime,
    volume: u32,
    _size_high: u32,
    _size_low: u32,
    links: u32,
    file_index_high: u32,
    file_index_low: u32,
}

#[cfg(windows)]
#[repr(C)]
#[derive(Clone, Copy, Default)]
struct WindowsFileIdInfo {
    volume_serial: u64,
    file_id: [u8; 16],
}

#[cfg(windows)]
#[link(name = "kernel32")]
unsafe extern "system" {
    fn GetFileInformationByHandle(
        handle: *mut std::ffi::c_void,
        information: *mut WindowsFileInformation,
    ) -> i32;
    fn GetFileInformationByHandleEx(
        handle: *mut std::ffi::c_void,
        information_class: i32,
        information: *mut std::ffi::c_void,
        buffer_size: u32,
    ) -> i32;
}

#[cfg(windows)]
const FILE_ID_INFO_CLASS: i32 = 18;

#[cfg(windows)]
fn windows_file_identity(file: &File) -> std::io::Result<FileIdentity> {
    use std::os::windows::io::AsRawHandle;
    // ReFS identity checks require the full 128-bit file ID.
    let mut info = WindowsFileIdInfo::default();
    if unsafe {
        GetFileInformationByHandleEx(
            file.as_raw_handle(),
            FILE_ID_INFO_CLASS,
            (&mut info as *mut WindowsFileIdInfo).cast(),
            std::mem::size_of::<WindowsFileIdInfo>() as u32,
        )
    } == 0
    {
        return Err(std::io::Error::last_os_error());
    }
    Ok(identity_from_file_id_info(info))
}

#[cfg(windows)]
fn identity_from_file_id_info(info: WindowsFileIdInfo) -> FileIdentity {
    FileIdentity {
        volume_serial: info.volume_serial,
        file_id: info.file_id,
    }
}

#[cfg(windows)]
fn windows_file_link_count(file: &File) -> std::io::Result<u32> {
    use std::os::windows::io::AsRawHandle;
    let mut information = WindowsFileInformation::default();
    if unsafe { GetFileInformationByHandle(file.as_raw_handle(), &mut information) } == 0 {
        return Err(std::io::Error::last_os_error());
    }
    Ok(information.links)
}

pub(super) fn require_single_link(file: &File, path: &Path) -> anyhow::Result<()> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        if file.metadata()?.nlink() != 1 {
            anyhow::bail!("refusing multiply-linked target {}", path.display());
        }
    }
    #[cfg(windows)]
    {
        if windows_file_link_count(file)? != 1 {
            anyhow::bail!("refusing multiply-linked target {}", path.display());
        }
    }
    #[cfg(not(any(unix, windows)))]
    {
        let _ = (file, path);
        anyhow::bail!("anchored link-count checks are unsupported on this platform")
    }
    Ok(())
}

#[cfg(all(test, windows))]
mod windows_identity_tests {
    use super::{identity_from_file_id_info, FileIdentity, WindowsFileIdInfo};

    #[test]
    fn identity_compares_all_128_file_id_bits_and_volume() {
        let base = identity_from_file_id_info(WindowsFileIdInfo {
            volume_serial: 7,
            file_id: [0; 16],
        });
        let same = identity_from_file_id_info(WindowsFileIdInfo {
            volume_serial: 7,
            file_id: [0; 16],
        });
        let mut high_bit = [0; 16];
        high_bit[15] = 1;
        let different_file = identity_from_file_id_info(WindowsFileIdInfo {
            volume_serial: 7,
            file_id: high_bit,
        });
        let different_volume = identity_from_file_id_info(WindowsFileIdInfo {
            volume_serial: 8,
            file_id: [0; 16],
        });

        assert_eq!(base, same);
        assert_ne!(base, different_file);
        assert_ne!(base, different_volume);
        let _: FileIdentity = base;
    }
}

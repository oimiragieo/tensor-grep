//! Handle-bound reads only; callers must separately establish ACL, ancestry and executable trust.
#![cfg(windows)]

use std::ffi::OsString;
use std::fs::{File, OpenOptions};
use std::io::{self, Read};
use std::os::windows::ffi::OsStringExt;
use std::os::windows::fs::{MetadataExt, OpenOptionsExt};
use std::os::windows::io::{AsHandle, AsRawHandle, BorrowedHandle};
use std::path::{Path, PathBuf};

use windows_sys::Win32::Storage::FileSystem::{
    FileIdInfo, GetFileInformationByHandle, GetFileInformationByHandleEx,
    GetFinalPathNameByHandleW, BY_HANDLE_FILE_INFORMATION, FILE_ATTRIBUTE_DIRECTORY,
    FILE_ATTRIBUTE_REPARSE_POINT, FILE_FLAG_BACKUP_SEMANTICS, FILE_FLAG_OPEN_REPARSE_POINT,
    FILE_ID_INFO, FILE_NAME_OPENED,
};

const MAX_RECORD_BYTES: usize = 512;
const MAX_FINAL_PATH_UNITS: usize = 32_768;
const FILE_SHARE_READ_WRITE: u32 = 0x3;
const FILE_SHARE_READ_DELETE: u32 = 0x5;
const FILE_SHARE_READ: u32 = 0x1;

#[derive(Debug)]
pub struct OpenedSelectionFile {
    file: File,
}

impl OpenedSelectionFile {
    /// Return the opened file's bounded, normalized DOS path.
    pub fn final_path(&self) -> io::Result<PathBuf> {
        final_path_for_handle(self.file.as_handle())
    }

    pub fn as_handle(&self) -> BorrowedHandle<'_> {
        self.file.as_handle()
    }
}

pub fn final_path_for_handle(handle: BorrowedHandle<'_>) -> io::Result<PathBuf> {
    path_for_handle(handle, 0)
}

fn path_for_handle(handle: BorrowedHandle<'_>, flags: u32) -> io::Result<PathBuf> {
    let mut buffer = vec![0u16; MAX_FINAL_PATH_UNITS];
    let length = unsafe {
        GetFinalPathNameByHandleW(
            handle.as_raw_handle(),
            buffer.as_mut_ptr(),
            buffer.len() as u32,
            flags,
        )
    };
    if length == 0 {
        return Err(io::Error::last_os_error());
    }
    if length as usize >= buffer.len() {
        return Err(invalid_data(
            "opened file final path exceeds 32767 UTF-16 units",
        ));
    }
    Ok(PathBuf::from(OsString::from_wide(
        &buffer[..length as usize],
    )))
}

fn path_key(path: &Path) -> io::Result<String> {
    let value = path
        .to_str()
        .ok_or_else(|| invalid_data("path is not Unicode"))?
        .replace('/', "\\");
    Ok(value
        .strip_prefix("\\\\?\\")
        .unwrap_or(&value)
        .trim_end_matches('\\')
        .to_ascii_lowercase())
}

/// The caller must validate the local path, open the child by that exact requested path, and pin
/// ordinary no-follow parent and child handles. The comparison does not establish ACL trust.
pub fn is_held_direct_child(
    parent: BorrowedHandle<'_>,
    child: BorrowedHandle<'_>,
    requested: &Path,
) -> io::Result<bool> {
    let parent_path = final_path_for_handle(parent)?;
    let final_path = final_path_for_handle(child)?;
    let opened_path = path_for_handle(child, FILE_NAME_OPENED)?;
    let child_parent = final_path
        .parent()
        .ok_or_else(|| invalid_data("child has no parent"))?;
    let parent_matches = path_key(child_parent)? == path_key(&parent_path)?;
    let name_matches = path_key(&opened_path)? == path_key(requested)?;
    #[cfg(test)]
    if !parent_matches || !name_matches {
        eprintln!("path validation: component_depth={} short_alias_shaped={} normalized_parent_matches={parent_matches} opened_path_matches={name_matches}", requested.components().count(), requested.file_name().and_then(|name| name.to_str()).is_some_and(|name| name.contains('~')));
    }
    Ok(parent_matches && name_matches)
}

impl OpenedSelectionFile {
    pub fn read_record(&mut self) -> io::Result<Vec<u8>> {
        let mut record = Vec::with_capacity(MAX_RECORD_BYTES + 1);
        (&mut self.file)
            .take((MAX_RECORD_BYTES + 1) as u64)
            .read_to_end(&mut record)?;
        if record.len() > MAX_RECORD_BYTES {
            return Err(invalid_data("selection record exceeds 512 bytes"));
        }
        Ok(record)
    }
}

/// Open a selector below an already validated and pinned parent directory.
///
/// The caller must supply a local ordinary-filesystem path. This primitive does not validate
/// arbitrary device, pipe, alternate-stream, or UNC paths; the resolver must constrain its
/// root and fixed filename before calling it.
pub fn open_selector(path: &Path) -> io::Result<Option<OpenedSelectionFile>> {
    let file = match OpenOptions::new()
        .read(true)
        .share_mode(FILE_SHARE_READ_DELETE)
        .custom_flags(FILE_FLAG_OPEN_REPARSE_POINT)
        .open(path)
    {
        Ok(file) => file,
        Err(error) if error.raw_os_error() == Some(2) => return Ok(None),
        Err(error) => return Err(error),
    };
    validate_opened_type(&file, false)?;
    validate_single_link(&file)?;
    Ok(Some(OpenedSelectionFile { file }))
}

/// Open and type-check a directory below an already validated and pinned parent.
///
/// The caller must supply a local ordinary-filesystem path; this primitive does not establish
/// trusted ancestry or validate arbitrary device, pipe, alternate-stream, or UNC paths.
pub fn open_directory(path: &Path) -> io::Result<File> {
    let file = OpenOptions::new()
        .read(true)
        .share_mode(FILE_SHARE_READ_WRITE)
        .custom_flags(FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_BACKUP_SEMANTICS)
        .open(path)?;
    validate_opened_type(&file, true)?;
    Ok(file)
}

/// Open a regular executable while preventing writes, deletes, and replacement until drop.
pub fn open_executable(path: &Path) -> io::Result<File> {
    let file = OpenOptions::new()
        .read(true)
        .share_mode(FILE_SHARE_READ)
        .custom_flags(FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_BACKUP_SEMANTICS)
        .open(path)?;
    validate_opened_type(&file, false)?;
    validate_single_link(&file)?;
    Ok(file)
}

/// Open an existing file without following a reparse leaf for file-identity comparison.
pub fn open_file_for_identity(path: &Path) -> io::Result<File> {
    let file = OpenOptions::new()
        .read(true)
        .share_mode(FILE_SHARE_READ_WRITE | 0x4)
        .custom_flags(FILE_FLAG_OPEN_REPARSE_POINT)
        .open(path)?;
    validate_opened_type(&file, false)?;
    Ok(file)
}

pub fn same_file_identity(left: BorrowedHandle<'_>, right: BorrowedHandle<'_>) -> io::Result<bool> {
    let mut left_info = FILE_ID_INFO::default();
    let mut right_info = FILE_ID_INFO::default();
    if unsafe {
        GetFileInformationByHandleEx(
            left.as_raw_handle(),
            FileIdInfo,
            (&mut left_info as *mut FILE_ID_INFO).cast(),
            std::mem::size_of::<FILE_ID_INFO>() as u32,
        )
    } == 0
        || unsafe {
            GetFileInformationByHandleEx(
                right.as_raw_handle(),
                FileIdInfo,
                (&mut right_info as *mut FILE_ID_INFO).cast(),
                std::mem::size_of::<FILE_ID_INFO>() as u32,
            )
        } == 0
    {
        return Err(io::Error::last_os_error());
    }
    Ok(
        left_info.VolumeSerialNumber == right_info.VolumeSerialNumber
            && left_info.FileId.Identifier == right_info.FileId.Identifier,
    )
}

fn validate_opened_type(file: &File, expect_directory: bool) -> io::Result<()> {
    let attributes = file.metadata()?.file_attributes();
    let is_directory = attributes & FILE_ATTRIBUTE_DIRECTORY != 0;
    if attributes & FILE_ATTRIBUTE_REPARSE_POINT != 0 || is_directory != expect_directory {
        return Err(invalid_data(
            "opened path has an unexpected type or is a reparse point",
        ));
    }
    Ok(())
}

fn validate_single_link(file: &File) -> io::Result<()> {
    let mut information = BY_HANDLE_FILE_INFORMATION::default();
    let succeeded = unsafe { GetFileInformationByHandle(file.as_raw_handle(), &mut information) };
    if succeeded == 0 {
        return Err(io::Error::last_os_error());
    }
    if information.nNumberOfLinks != 1 {
        return Err(invalid_data(
            "managed runtime file must have exactly one hard link",
        ));
    }
    Ok(())
}

fn invalid_data(message: &'static str) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, message)
}

#[cfg(all(test, windows))]
mod tests {
    use super::{open_directory, open_file_for_identity, open_selector, same_file_identity};
    use std::fs::{self, File, OpenOptions};
    use std::io::ErrorKind;
    use std::os::windows::fs::{MetadataExt, OpenOptionsExt};
    use std::os::windows::io::AsHandle;
    use std::os::windows::process::CommandExt;

    const VALID: &[u8] = b"{\"schema_version\":1,\"generation\":\"0123456789abcdef0123456789abcdef\",\"version\":\"1.2.3\"}";

    include!("managed_runtime_windows_alias_tests.rs");

    #[test]
    fn open_selector_valid_record_reads_from_opened_file() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("runtime-selection.json");
        fs::write(&path, VALID).unwrap();
        let mut selector = open_selector(&path).unwrap().unwrap();

        let record = selector.read_record().unwrap();

        assert_eq!(record, VALID);
    }

    #[test]
    fn open_selector_512_byte_record_accepts_boundary() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("runtime-selection.json");
        let mut record = VALID.to_vec();
        record.resize(512, b' ');
        fs::write(&path, &record).unwrap();
        let mut selector = open_selector(&path).unwrap().unwrap();

        let read = selector.read_record().unwrap();

        assert_eq!(read.len(), 512);
        assert_eq!(read, record);
    }

    #[test]
    fn open_selector_513_byte_record_refuses_oversize() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("runtime-selection.json");
        fs::write(&path, [b'x'; 513]).unwrap();
        let mut selector = open_selector(&path).unwrap().unwrap();

        let result = selector.read_record();

        assert_eq!(result.unwrap_err().kind(), ErrorKind::InvalidData);
    }

    #[test]
    fn open_selector_missing_leaf_returns_none() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("runtime-selection.json");

        let selector = open_selector(&path).unwrap();

        assert!(selector.is_none());
    }

    #[test]
    fn open_selector_missing_parent_returns_error() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory
            .path()
            .join("missing")
            .join("runtime-selection.json");

        let result = open_selector(&path);

        assert_eq!(result.unwrap_err().raw_os_error(), Some(3));
    }

    #[test]
    fn open_selector_directory_leaf_returns_error() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("runtime-selection.json");
        fs::create_dir(&path).unwrap();

        let result = open_selector(&path);

        assert!(result.is_err());
    }

    #[test]
    fn open_selector_hard_link_refuses_multiple_links() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("runtime-selection.json");
        let alias = directory.path().join("alias.json");
        fs::write(&path, VALID).unwrap();
        fs::hard_link(&path, &alias).unwrap();

        let result = open_selector(&path);

        assert_eq!(result.unwrap_err().kind(), ErrorKind::InvalidData);
    }

    #[test]
    fn open_selector_handle_denies_writes_until_drop() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("runtime-selection.json");
        fs::write(&path, VALID).unwrap();
        let selector = open_selector(&path).unwrap().unwrap();
        let blocked = OpenOptions::new().write(true).share_mode(7).open(&path);

        assert_eq!(blocked.unwrap_err().raw_os_error(), Some(32));
        drop(selector);
        let writable = OpenOptions::new().write(true).share_mode(7).open(&path);

        assert!(writable.is_ok());
    }

    #[test]
    fn open_directory_handle_blocks_rename_until_drop() {
        let directory = tempfile::tempdir().unwrap();
        let source = directory.path().join("runtime");
        let destination = directory.path().join("renamed");
        fs::create_dir(&source).unwrap();
        let handle = open_directory(&source).unwrap();
        let blocked = fs::rename(&source, &destination);

        assert_eq!(blocked.unwrap_err().raw_os_error(), Some(32));
        drop(handle);
        fs::rename(&source, &destination).unwrap();

        assert!(destination.is_dir());
    }

    #[test]
    fn open_selector_final_path_matches_canonical_file_path() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("runtime-selection.json");
        fs::write(&path, VALID).unwrap();
        let canonical = fs::canonicalize(&path).unwrap();
        let selector = open_selector(&path).unwrap().unwrap();

        let final_path = selector.final_path().unwrap();

        assert_eq!(final_path, canonical);
    }

    #[test]
    fn open_directory_regular_directory_is_accepted() {
        let directory = tempfile::tempdir().unwrap();

        let result = open_directory(directory.path());

        assert!(result.is_ok());
    }

    #[test]
    fn file_identity_matches_same_file_and_rejects_distinct_file() {
        let directory = tempfile::tempdir().unwrap();
        let first = directory.path().join("first.exe");
        let second = directory.path().join("second.exe");
        fs::write(&first, b"first").unwrap();
        fs::write(&second, b"second").unwrap();
        let first_handle = open_file_for_identity(&first).unwrap();
        let same_handle = open_file_for_identity(&first).unwrap();
        let different_handle = open_file_for_identity(&second).unwrap();

        assert!(same_file_identity(first_handle.as_handle(), same_handle.as_handle()).unwrap());
        assert!(
            !same_file_identity(first_handle.as_handle(), different_handle.as_handle()).unwrap()
        );
    }

    #[test]
    fn open_directory_file_is_refused() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("file");
        File::create(&path).unwrap();

        let result = open_directory(&path);

        assert_eq!(result.unwrap_err().kind(), ErrorKind::InvalidData);
    }
    #[test]
    fn open_directory_refuses_real_junction() {
        let directory = tempfile::tempdir().unwrap();
        let target = directory.path().join("target");
        let junction = directory.path().join("junction");
        fs::create_dir(&target).unwrap();
        let output = std::process::Command::new("cmd")
            .creation_flags(0x08000000)
            .args(["/d", "/c", "mklink", "/J"])
            .arg(&junction)
            .arg(&target)
            .output()
            .unwrap();

        assert!(output.status.success());
        assert_ne!(
            fs::symlink_metadata(&junction).unwrap().file_attributes() & 0x400,
            0
        );
        let result = open_directory(&junction);

        assert_eq!(result.unwrap_err().kind(), ErrorKind::InvalidData);
        fs::remove_dir(&junction).unwrap();
    }
    #[test]
    fn open_selector_exclusive_existing_handle_returns_sharing_error_not_missing() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("runtime-selection.json");
        fs::write(&path, VALID).unwrap();
        let held = OpenOptions::new()
            .read(true)
            .share_mode(0)
            .open(&path)
            .unwrap();

        let blocked = open_selector(&path);

        assert_eq!(blocked.unwrap_err().raw_os_error(), Some(32));
        drop(held);
        let available = open_selector(&path).unwrap();

        assert!(available.is_some());
    }
}

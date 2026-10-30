// Test-fixture cleanup only; no recursion, and never retry uncertain operations.
use std::os::windows::io::{AsRawHandle, IntoRawHandle};
use windows_sys::Win32::Foundation::CloseHandle;
use windows_sys::Win32::Storage::FileSystem::{
    FileAttributeTagInfo, FileDispositionInfo, FileIdInfo, GetFileInformationByHandle,
    GetFileInformationByHandleEx, SetFileInformationByHandle, BY_HANDLE_FILE_INFORMATION,
    FILE_ATTRIBUTE_TAG_INFO, FILE_DISPOSITION_INFO, FILE_ID_INFO,
};

#[derive(Clone, Copy)]
enum FixtureKind {
    File,
    Directory,
    Junction,
}

fn fixture_id(file: &File, kind: FixtureKind) -> std::io::Result<(u64, [u8; 16])> {
    let mut tag = FILE_ATTRIBUTE_TAG_INFO::default();
    let mut id = FILE_ID_INFO::default();
    for (class, pointer, size) in [
        (
            FileAttributeTagInfo,
            (&mut tag as *mut FILE_ATTRIBUTE_TAG_INFO).cast(),
            std::mem::size_of::<FILE_ATTRIBUTE_TAG_INFO>(),
        ),
        (
            FileIdInfo,
            (&mut id as *mut FILE_ID_INFO).cast(),
            std::mem::size_of::<FILE_ID_INFO>(),
        ),
    ] {
        if unsafe {
            GetFileInformationByHandleEx(file.as_raw_handle(), class, pointer, size as u32)
        } == 0
        {
            return Err(std::io::Error::last_os_error());
        }
    }
    let bits = tag.FileAttributes & 0x410;
    let valid = match kind {
        FixtureKind::File => bits == 0,
        FixtureKind::Directory => bits == 0x10,
        FixtureKind::Junction => bits == 0x410 && tag.ReparseTag == 0xa000_0003,
    };
    if !valid || id.VolumeSerialNumber == 0 || id.FileId.Identifier == [0; 16] {
        return Err(std::io::Error::new(
            ErrorKind::InvalidData,
            "fixture type, tag, or identity mismatch",
        ));
    }
    Ok((id.VolumeSerialNumber, id.FileId.Identifier))
}

fn fixture_links(file: &File) -> std::io::Result<u32> {
    let mut info = BY_HANDLE_FILE_INFORMATION::default();
    if unsafe { GetFileInformationByHandle(file.as_raw_handle(), &mut info) } == 0 {
        return Err(std::io::Error::last_os_error());
    }
    Ok(info.nNumberOfLinks)
}

fn close_fixture(file: File) -> std::io::Result<()> {
    if unsafe { CloseHandle(file.into_raw_handle()) } == 0 {
        return Err(std::io::Error::last_os_error());
    }
    Ok(())
}

fn remove_fixture_object(
    parent: &File,
    path: &std::path::Path,
    expected: (u64, [u8; 16]),
    kind: FixtureKind,
) -> std::io::Result<()> {
    let file = OpenOptions::new()
        .read(true)
        .access_mode(0x8001_0000)
        .share_mode(1)
        .custom_flags(0x0220_0000)
        .open(path)?;
    let result = (|| {
        if fixture_id(&file, kind)? != expected
            || !super::is_held_direct_child(parent.as_handle(), file.as_handle(), path)?
        {
            return Err(std::io::Error::new(
                ErrorKind::InvalidData,
                "fixture identity or parent changed",
            ));
        }
        let disposition = FILE_DISPOSITION_INFO { DeleteFile: true };
        if unsafe {
            SetFileInformationByHandle(
                file.as_raw_handle(),
                FileDispositionInfo,
                (&disposition as *const FILE_DISPOSITION_INFO).cast(),
                std::mem::size_of::<FILE_DISPOSITION_INFO>() as u32,
            )
        } == 0
        {
            return Err(std::io::Error::last_os_error());
        }
        Ok(())
    })();
    let closed = close_fixture(file);
    if let Err(error) = closed {
        return Err(std::io::Error::other(format!(
            "fixture cleanup close unverified: {error}; operation: {result:?}"
        )));
    }
    result?;
    match fs::symlink_metadata(path) {
        Err(error) if error.raw_os_error() == Some(2) => Ok(()),
        Err(error) => Err(error),
        Ok(_) => Err(std::io::Error::other(
            "fixture cleanup did not remove the expected name",
        )),
    }
}

#[test]
fn fixture_cleanup_refuses_wrong_identity_and_kind() {
    let fixture = tempfile::tempdir().unwrap();
    let path = fixture.path().join("canary");
    fs::write(&path, b"preserve").unwrap();
    let parent = open_directory(fixture.path()).unwrap();
    let pin = open_file_for_identity(&path).unwrap();
    let id = fixture_id(&pin, FixtureKind::File).unwrap();
    close_fixture(pin).unwrap();
    let writer = OpenOptions::new()
        .write(true)
        .share_mode(7)
        .open(&path)
        .unwrap();
    assert_eq!(
        remove_fixture_object(&parent, &path, id, FixtureKind::File)
            .unwrap_err()
            .raw_os_error(),
        Some(32)
    );
    drop(writer);
    let mut wrong = id;
    wrong.1[0] ^= 1;
    assert_eq!(
        remove_fixture_object(&parent, &path, wrong, FixtureKind::File)
            .unwrap_err()
            .kind(),
        ErrorKind::InvalidData
    );
    assert_eq!(
        remove_fixture_object(&parent, &path, id, FixtureKind::Directory)
            .unwrap_err()
            .kind(),
        ErrorKind::InvalidData
    );
    assert_eq!(fs::read(&path).unwrap(), b"preserve");
    remove_fixture_object(&parent, &path, id, FixtureKind::File).unwrap();
    assert_eq!(
        fs::symlink_metadata(path).unwrap_err().kind(),
        ErrorKind::NotFound
    );
}

#[test]
fn fixture_cleanup_removes_one_hardlink_and_preserves_the_other() {
    let fixture = tempfile::tempdir().unwrap();
    let path = fixture.path().join("original");
    let alias = fixture.path().join("hardlink");
    fs::write(&path, b"preserve").unwrap();
    fs::hard_link(&path, &alias).unwrap();
    let parent = open_directory(fixture.path()).unwrap();
    let pin = open_file_for_identity(&path).unwrap();
    let id = fixture_id(&pin, FixtureKind::File).unwrap();
    assert_eq!(fixture_links(&pin).unwrap(), 2);
    close_fixture(pin).unwrap();
    remove_fixture_object(&parent, &alias, id, FixtureKind::File).unwrap();
    let survivor = open_file_for_identity(&path).unwrap();
    assert_eq!(fixture_id(&survivor, FixtureKind::File).unwrap(), id);
    assert_eq!(fixture_links(&survivor).unwrap(), 1);
    assert_eq!(fs::read(&path).unwrap(), b"preserve");
    close_fixture(survivor).unwrap();
    remove_fixture_object(&parent, &path, id, FixtureKind::File).unwrap();
}

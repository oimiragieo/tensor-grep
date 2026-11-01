use super::{
    managed_runtime_acl_reader, managed_runtime_windows_token,
    recovery_directory_create::{verified_dacl, AnchoredDirectory},
    windows, windows_file,
};
use sha2::{Digest, Sha256};
use std::collections::BTreeSet;
use std::fs::{self, File};
use std::io::{self, Read};
use std::os::windows::fs::MetadataExt;
use std::os::windows::io::{AsHandle, AsRawHandle};
use std::path::Path;
use windows_sys::Win32::Storage::FileSystem::{
    FileIdInfo, GetFileInformationByHandle, GetFileInformationByHandleEx, GetFileType,
    BY_HANDLE_FILE_INFORMATION, FILE_ATTRIBUTE_REPARSE_POINT, FILE_ID_INFO, FILE_TYPE_DISK,
};

const ANCHOR: &str = ".tg-recovery-anchor";

/// Retains verified objects and borrows their stage; this does not authorize execution.
/// ACL changes, alternate streams, and late names remain possible.
pub(super) struct HeldVerifiedTree<'a> {
    _stage: &'a AnchoredDirectory,
    _pins: Vec<File>,
}

#[derive(Clone, Copy)]
struct Expected<'a> {
    files: usize,
    dirs: usize,
    bytes: u64,
    max_file: u64,
    max_depth: usize,
    max_path: usize,
    digest: &'a str,
}

const PYTHON: Expected<'static> = Expected {
    files: 3388,
    dirs: 259,
    bytes: 149_886_264,
    max_file: 22_695_936,
    max_depth: 10,
    max_path: 105,
    digest: "95ab1429eb4b698ba8577bcd7e9f803fff9ba9a24f5702a989a5601449dc2a5e",
};

fn refuse(message: &'static str) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, message)
}

fn check_anchor(stage: &AnchoredDirectory, root: &Path, root_final: &Path) -> io::Result<()> {
    let user = managed_runtime_windows_token::current_token_sids()?;
    for file in [stage.directory(), stage.anchor()] {
        let acl = managed_runtime_acl_reader::read_handle_acl_details(file.as_handle())?;
        if acl.acl.owner != user.user_sid() {
            return Err(refuse("anchor or directory owner changed"));
        }
        verified_dacl(user.user_sid(), &acl)?;
    }
    let metadata = stage.anchor().metadata()?;
    let directory_metadata = stage.directory().metadata()?;
    let mut anchor_info = BY_HANDLE_FILE_INFORMATION::default();
    let mut anchor_id = FILE_ID_INFO::default();
    let mut directory_id = FILE_ID_INFO::default();
    if unsafe { GetFileInformationByHandle(stage.anchor().as_raw_handle(), &mut anchor_info) } == 0
        || unsafe {
            GetFileInformationByHandleEx(
                stage.anchor().as_raw_handle(),
                FileIdInfo,
                (&mut anchor_id as *mut FILE_ID_INFO).cast(),
                std::mem::size_of::<FILE_ID_INFO>() as u32,
            )
        } == 0
        || unsafe {
            GetFileInformationByHandleEx(
                stage.directory().as_raw_handle(),
                FileIdInfo,
                (&mut directory_id as *mut FILE_ID_INFO).cast(),
                std::mem::size_of::<FILE_ID_INFO>() as u32,
            )
        } == 0
    {
        return Err(io::Error::last_os_error());
    }
    if !metadata.is_file()
        || metadata.len() != 0
        || metadata.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT != 0
        || !directory_metadata.is_dir()
        || directory_metadata.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT != 0
        || anchor_info.nNumberOfLinks != 1
        || anchor_id.VolumeSerialNumber != directory_id.VolumeSerialNumber
        || unsafe { GetFileType(stage.anchor().as_raw_handle()) } != FILE_TYPE_DISK
        || unsafe { GetFileType(stage.directory().as_raw_handle()) } != FILE_TYPE_DISK
        || windows::path_key(&windows_file::final_path_for_handle(
            stage.directory().as_handle(),
        )?)? != windows::path_key(root_final)?
    {
        return Err(refuse("anchor identity invalid"));
    }
    let path = root.join(ANCHOR);
    let named = windows_file::open_file_for_identity(&path)?;
    if !windows_file::is_held_direct_child(stage.directory().as_handle(), named.as_handle(), &path)?
        || !windows_file::same_file_identity(stage.anchor().as_handle(), named.as_handle())?
    {
        return Err(refuse("anchor name changed"));
    }
    Ok(())
}

pub(super) fn pin_root(path: &Path) -> io::Result<Vec<File>> {
    let (mut current, parts) = windows::local_parts(path)?;
    let mut pins = vec![windows_file::open_directory(&current)?];
    let root_final = windows_file::final_path_for_handle(pins[0].as_handle())?;
    if windows::path_key(&root_final)? != windows::path_key(&current)? {
        return Err(refuse("drive root changed"));
    }
    for part in parts {
        current.push(&part);
        let file = windows_file::open_directory(&current)?;
        if !windows_file::is_held_direct_child(
            pins.last().unwrap().as_handle(),
            file.as_handle(),
            &current,
        )? {
            return Err(refuse("directory changed"));
        }
        pins.push(file);
    }
    Ok(pins)
}

fn verify_expected(root: &Path, expected: &Expected<'_>) -> io::Result<()> {
    verify_with_hook(root, expected, |_| Ok(()))
}

fn verify_with_hook(
    root: &Path,
    expected: &Expected<'_>,
    after_directory: impl FnMut(&Path) -> io::Result<()>,
) -> io::Result<()> {
    verify_tree(root, expected, None, after_directory).map(drop)
}

fn verify_tree(
    root: &Path,
    expected: &Expected<'_>,
    stage: Option<&AnchoredDirectory>,
    mut after_directory: impl FnMut(&Path) -> io::Result<()>,
) -> io::Result<Vec<File>> {
    let mut pins = pin_root(root)?;
    let root_final = windows_file::final_path_for_handle(pins.last().unwrap().as_handle())?;
    if let Some(stage) = stage {
        if !windows_file::same_file_identity(
            pins.last().unwrap().as_handle(),
            stage.directory().as_handle(),
        )? {
            return Err(refuse("anchored directory identity changed"));
        }
        check_anchor(stage, root, &root_final)?;
    }
    let mut pending = vec![(root.to_path_buf(), String::new(), pins.len() - 1, 0usize)];
    let mut seen = BTreeSet::new();
    let mut directories = BTreeSet::new();
    let mut required_directories = BTreeSet::new();
    let mut records = Vec::new();
    let mut visited = Vec::new();
    let mut total = 0u64;
    let mut anchor_seen = false;
    while let Some((directory, prefix, parent, depth)) = pending.pop() {
        for entry in fs::read_dir(&directory)? {
            let entry = entry?;
            let name = entry.file_name();
            let text = name.to_str().ok_or_else(|| refuse("non-Unicode name"))?;
            if depth == 0 && stage.is_some() && text.eq_ignore_ascii_case(ANCHOR) {
                if text != ANCHOR || anchor_seen {
                    return Err(refuse("anchor name is ambiguous"));
                }
                anchor_seen = true;
                check_anchor(stage.unwrap(), root, &root_final)?;
                continue;
            }
            if !text.is_ascii() || !windows::safe_component(&name) {
                return Err(refuse("unsafe name"));
            }
            let relative = if prefix.is_empty() {
                text.to_owned()
            } else {
                format!("{prefix}/{text}")
            };
            if depth + 1 > expected.max_depth
                || relative.len() > expected.max_path
                || !seen.insert(relative.to_ascii_lowercase())
                || seen.len() > expected.files + expected.dirs
            {
                return Err(refuse("tree name or count limit exceeded"));
            }
            let kind = entry.file_type()?;
            if kind.is_dir() && !kind.is_symlink() {
                if directories.len() >= expected.dirs {
                    return Err(refuse("directory count exceeded"));
                }
                let file = windows_file::open_directory(&entry.path())?;
                if unsafe { GetFileType(file.as_raw_handle()) } != FILE_TYPE_DISK
                    || !windows_file::is_held_direct_child(
                        pins[parent].as_handle(),
                        file.as_handle(),
                        &entry.path(),
                    )?
                {
                    return Err(refuse("unsafe directory"));
                }
                directories.insert(relative.clone());
                pending.push((entry.path(), relative, pins.len(), depth + 1));
                pins.push(file);
            } else if kind.is_file() && !kind.is_symlink() {
                if records.len() >= expected.files {
                    return Err(refuse("file count exceeded"));
                }
                let mut file = windows_file::open_executable(&entry.path())?;
                if unsafe { GetFileType(file.as_raw_handle()) } != FILE_TYPE_DISK
                    || !windows_file::is_held_direct_child(
                        pins[parent].as_handle(),
                        file.as_handle(),
                        &entry.path(),
                    )?
                {
                    return Err(refuse("unsafe file"));
                }
                let size = file.metadata()?.len();
                if size > expected.max_file || size > expected.bytes.saturating_sub(total) {
                    return Err(refuse("file size exceeded"));
                }
                let mut file_hash = Sha256::new();
                let mut read = 0u64;
                let mut limited = (&mut file).take(size + 1);
                let mut buffer = [0u8; 64 * 1024];
                loop {
                    let count = limited.read(&mut buffer)?;
                    if count == 0 {
                        break;
                    }
                    read += count as u64;
                    file_hash.update(&buffer[..count]);
                }
                if read != size {
                    return Err(refuse("file changed size"));
                }
                total += size;
                let mut ancestor = relative.as_str();
                while let Some((parent, _)) = ancestor.rsplit_once('/') {
                    required_directories.insert(parent.to_owned());
                    ancestor = parent;
                }
                records.push((relative, size, format!("{:x}", file_hash.finalize())));
                pins.push(file);
            } else {
                return Err(refuse("unsupported tree entry"));
            }
        }
        visited.push((directory.clone(), prefix));
        after_directory(&directory)?;
    }
    if (stage.is_some() && !anchor_seen)
        || records.len() != expected.files
        || directories.len() != expected.dirs
        || directories != required_directories
        || total != expected.bytes
    {
        return Err(refuse("tree population mismatch"));
    }
    let mut seen_again = BTreeSet::new();
    let mut anchor_seen_again = false;
    for (directory, prefix) in visited {
        for entry in fs::read_dir(directory)? {
            let name = entry?.file_name();
            let text = name.to_str().ok_or_else(|| refuse("non-Unicode name"))?;
            if prefix.is_empty() && stage.is_some() && text.eq_ignore_ascii_case(ANCHOR) {
                if text != ANCHOR || anchor_seen_again {
                    return Err(refuse("anchor name is ambiguous"));
                }
                anchor_seen_again = true;
                continue;
            }
            let relative = if prefix.is_empty() {
                text.to_owned()
            } else {
                format!("{prefix}/{text}")
            };
            if !text.is_ascii()
                || !windows::safe_component(&name)
                || !seen_again.insert(relative.to_ascii_lowercase())
                || seen_again.len() > seen.len()
            {
                return Err(refuse("tree changed during verification"));
            }
        }
    }
    if seen_again != seen || (stage.is_some() && !anchor_seen_again) {
        return Err(refuse("tree changed during verification"));
    }
    records.sort_unstable_by(|left, right| left.0.cmp(&right.0));
    let mut digest = Sha256::new();
    for (name, size, hash) in records {
        digest.update(name.as_bytes());
        digest.update(b"\0");
        digest.update(size.to_string().as_bytes());
        digest.update(b"\0");
        digest.update(hash.as_bytes());
        digest.update(b"\n");
    }
    if format!("{:x}", digest.finalize()) != expected.digest {
        return Err(refuse("tree digest mismatch"));
    }
    if let Some(stage) = stage {
        check_anchor(stage, root, &root_final)?;
    }
    Ok(pins)
}

pub(super) fn verify(root: &Path) -> io::Result<()> {
    verify_expected(root, &PYTHON)
}

pub(super) fn verify_anchored<'a>(
    root: &Path,
    stage: &'a AnchoredDirectory,
) -> io::Result<HeldVerifiedTree<'a>> {
    verify_anchored_expected(root, stage, &PYTHON)
}

fn verify_anchored_expected<'a>(
    root: &Path,
    stage: &'a AnchoredDirectory,
    expected: &Expected<'_>,
) -> io::Result<HeldVerifiedTree<'a>> {
    let pins = verify_tree(root, expected, Some(stage), |_| Ok(()))?;
    Ok(HeldVerifiedTree {
        _stage: stage,
        _pins: pins,
    })
}

#[cfg(test)]
mod tests {
    use super::super::{recovery_directory_create, windows_file};
    use super::{
        pin_root, verify_anchored_expected, verify_expected, verify_tree, verify_with_hook,
        Expected,
    };
    use std::fs::{self, OpenOptions};
    use std::io::Write;
    use std::os::windows::ffi::{OsStrExt, OsStringExt};
    use std::os::windows::fs::OpenOptionsExt;
    use std::os::windows::io::AsRawHandle;
    use std::os::windows::process::CommandExt;
    use std::path::{Path, PathBuf};

    const FIXTURE: Expected<'static> = Expected {
        files: 1,
        dirs: 1,
        bytes: 5,
        max_file: 5,
        max_depth: 2,
        max_path: 12,
        digest: "5a3c8dcc33029de3666d76af511cd76a7ee8adbe4ff8aff047b93c55dcbe030b",
    };

    fn fixture() -> tempfile::TempDir {
        let root = tempfile::tempdir().unwrap();
        fs::create_dir(root.path().join("python")).unwrap();
        fs::write(root.path().join("python/a.txt"), b"alpha").unwrap();
        root
    }

    fn anchored_fixture() -> (
        tempfile::TempDir,
        recovery_directory_create::AnchoredDirectory,
        PathBuf,
    ) {
        let root = tempfile::tempdir().unwrap();
        let parent = windows_file::open_directory(root.path()).unwrap();
        let stage = recovery_directory_create::create_anchored_child(&parent, "stage").unwrap();
        let path = root.path().join("stage");
        fs::create_dir(path.join("python")).unwrap();
        fs::write(path.join("python/a.txt"), b"alpha").unwrap();
        (root, stage, path)
    }

    #[test]
    fn anchored_tree_is_held_and_strict_verifier_still_refuses_anchor() {
        let (_fixture, stage, path) = anchored_fixture();
        assert!(verify_expected(&path, &FIXTURE).is_err());
        let held = verify_anchored_expected(&path, &stage, &FIXTURE).unwrap();
        match OpenOptions::new()
            .write(true)
            .share_mode(7)
            .open(path.join("python/a.txt"))
        {
            Err(error) => assert_eq!(error.raw_os_error(), Some(32)),
            Ok(file) => {
                drop(file);
                panic!("held verified tree permitted payload writer");
            }
        }
        drop(held);
        drop(
            OpenOptions::new()
                .write(true)
                .share_mode(7)
                .open(path.join("python/a.txt"))
                .unwrap(),
        );
        drop(verify_anchored_expected(&path, &stage, &FIXTURE).unwrap());
        drop(stage);
        // The marker is now readable: strict refusal must not depend on sharing conflicts.
        assert!(verify_expected(&path, &FIXTURE).is_err());
        fs::remove_file(path.join(super::ANCHOR)).unwrap();
        verify_expected(&path, &FIXTURE).unwrap();
        eprintln!("held anchored tree payload lifetime controls exercised");
    }

    #[test]
    fn anchored_tree_refuses_wrong_root_and_extra_file() {
        let (_fixture, stage, path) = anchored_fixture();
        drop(verify_anchored_expected(&path, &stage, &FIXTURE).unwrap());
        let other = fixture();
        assert_eq!(
            verify_anchored_expected(other.path(), &stage, &FIXTURE)
                .err()
                .unwrap()
                .to_string(),
            "anchored directory identity changed"
        );
        fs::write(path.join("python/extra"), b"x").unwrap();
        assert!(verify_anchored_expected(&path, &stage, &FIXTURE).is_err());
        fs::remove_file(path.join("python/extra")).unwrap();
        drop(verify_anchored_expected(&path, &stage, &FIXTURE).unwrap());
    }

    #[test]
    fn anchored_tree_refuses_changed_anchor_and_hardlink() {
        let (root_fixture, stage, path) = anchored_fixture();
        drop(verify_anchored_expected(&path, &stage, &FIXTURE).unwrap());
        let mut writer = stage.anchor().try_clone().unwrap();
        writer.write_all(b"x").unwrap();
        assert_eq!(
            verify_anchored_expected(&path, &stage, &FIXTURE)
                .err()
                .unwrap()
                .to_string(),
            "anchor identity invalid"
        );
        writer.set_len(0).unwrap();
        drop(verify_anchored_expected(&path, &stage, &FIXTURE).unwrap());
        drop(writer);
        let alias = root_fixture.path().join("anchor-alias");
        fs::hard_link(path.join(super::ANCHOR), &alias).unwrap();
        assert_eq!(
            verify_anchored_expected(&path, &stage, &FIXTURE)
                .err()
                .unwrap()
                .to_string(),
            "anchor identity invalid"
        );
        drop(stage);
        fs::remove_file(alias).unwrap();
        let (_fresh, stage, path) = anchored_fixture();
        drop(verify_anchored_expected(&path, &stage, &FIXTURE).unwrap());
    }

    #[test]
    fn anchored_tree_rechecks_anchor_after_enumeration() {
        let (_fixture, stage, path) = anchored_fixture();
        drop(verify_anchored_expected(&path, &stage, &FIXTURE).unwrap());
        let mut writer = stage.anchor().try_clone().unwrap();
        let result = verify_tree(&path, &FIXTURE, Some(&stage), |directory| {
            if directory == path {
                writer.write_all(b"x")?;
            }
            Ok(())
        });
        assert_eq!(result.err().unwrap().to_string(), "anchor identity invalid");
        writer.set_len(0).unwrap();
        drop(writer);
        drop(verify_anchored_expected(&path, &stage, &FIXTURE).unwrap());
    }

    #[test]
    fn anchored_tree_refuses_foreign_grants_and_missing_inherit_flags() {
        use super::super::{
            managed_runtime_acl_reader, managed_runtime_windows_sid::LocalAllocation,
            managed_runtime_windows_token,
        };
        use std::os::windows::io::AsHandle;
        use std::ptr::null_mut;
        use windows_sys::Win32::Security::Authorization::{
            ConvertStringSecurityDescriptorToSecurityDescriptorW, SetSecurityInfo, SE_FILE_OBJECT,
        };
        use windows_sys::Win32::Security::{
            GetSecurityDescriptorDacl, DACL_SECURITY_INFORMATION,
            PROTECTED_DACL_SECURITY_INFORMATION,
        };
        use windows_sys::Win32::Storage::FileSystem::FILE_READ_DATA;
        let (_fixture, stage, path) = anchored_fixture();
        drop(verify_anchored_expected(&path, &stage, &FIXTURE).unwrap());
        let writer = OpenOptions::new()
            .access_mode(0x0006_0080)
            .share_mode(7)
            .open(path.join(super::ANCHOR))
            .unwrap();
        let user = managed_runtime_windows_token::current_token_sids().unwrap();
        let apply = |text: &str| {
            let sddl: Vec<u16> = text.encode_utf16().chain(Some(0)).collect();
            let mut descriptor = null_mut();
            assert_ne!(
                unsafe {
                    ConvertStringSecurityDescriptorToSecurityDescriptorW(
                        sddl.as_ptr(),
                        1,
                        &mut descriptor,
                        null_mut(),
                    )
                },
                0
            );
            let _allocation = unsafe { LocalAllocation::from_raw(descriptor.cast()) }.unwrap();
            let (mut present, mut defaulted, mut dacl) = (0, 0, null_mut());
            assert_ne!(
                unsafe {
                    GetSecurityDescriptorDacl(descriptor, &mut present, &mut dacl, &mut defaulted)
                },
                0
            );
            assert_ne!(present, 0);
            assert!(!dacl.is_null());
            assert_eq!(
                unsafe {
                    SetSecurityInfo(
                        writer.as_raw_handle(),
                        SE_FILE_OBJECT,
                        DACL_SECURITY_INFORMATION | PROTECTED_DACL_SECURITY_INFORMATION,
                        null_mut(),
                        null_mut(),
                        dacl,
                        null_mut(),
                    )
                },
                0
            );
        };
        let good = format!("D:P(A;OICI;FA;;;{})", user.user_sid());
        let bad = format!("{good}(A;;FR;;;WD)");
        apply(&bad);
        let foreign = managed_runtime_acl_reader::read_handle_acl(writer.as_handle()).unwrap();
        assert!(foreign
            .allow_grants
            .iter()
            .any(|(sid, mask)| sid == "S-1-1-0" && *mask & FILE_READ_DATA != 0));
        assert_eq!(
            verify_anchored_expected(&path, &stage, &FIXTURE)
                .err()
                .unwrap()
                .to_string(),
            "DACL differs from the protected user-only descriptor"
        );
        apply(&good);
        let restored =
            managed_runtime_acl_reader::read_handle_acl_details(writer.as_handle()).unwrap();
        assert_eq!(restored.acl.owner, user.user_sid());
        assert!(!restored.acl.allow_grants.is_empty());
        assert!(restored
            .acl
            .allow_grants
            .iter()
            .all(|(sid, _)| sid == user.user_sid()));
        assert!(restored.dacl_protected);
        assert_eq!(restored.ace_count, 1);
        assert_eq!(restored.acl.allow_grants[0].1, 0x001f_01ff);
        assert_eq!(restored.allow_grant_flags.len(), 1);
        assert_ne!(restored.allow_grant_flags[0] & 0x03, 0x03);
        // Applying the DACL to this marker file drops OI/CI, so it is still not the required ACL.
        assert_eq!(
            verify_anchored_expected(&path, &stage, &FIXTURE)
                .err()
                .unwrap()
                .to_string(),
            "DACL differs from the protected user-only descriptor"
        );
        drop(writer);
        drop(stage);
        let (_fresh, stage, path) = anchored_fixture();
        drop(verify_anchored_expected(&path, &stage, &FIXTURE).unwrap());
    }

    #[test]
    fn anchored_tree_accepts_root_short_alias_when_available() {
        use windows_sys::Win32::Storage::FileSystem::GetShortPathNameW;
        let (_fixture, stage, path) = anchored_fixture();
        drop(verify_anchored_expected(&path, &stage, &FIXTURE).unwrap());
        let wide: Vec<u16> = path.as_os_str().encode_wide().chain(Some(0)).collect();
        let mut buffer = vec![0u16; 32768];
        let length =
            unsafe { GetShortPathNameW(wide.as_ptr(), buffer.as_mut_ptr(), buffer.len() as u32) }
                as usize;
        if length == 0 || length >= buffer.len() {
            eprintln!("anchored tree short-directory alias unavailable");
            return;
        }
        let short = PathBuf::from(std::ffi::OsString::from_wide(&buffer[..length]));
        if short
            .to_string_lossy()
            .eq_ignore_ascii_case(&path.to_string_lossy())
        {
            eprintln!("anchored tree short-directory alias unavailable");
            return;
        }
        drop(verify_anchored_expected(&short, &stage, &FIXTURE).unwrap());
        eprintln!("anchored tree short-directory alias verified");
    }

    #[test]
    fn matches_known_fixture() {
        let root = fixture();
        verify_expected(root.path(), &FIXTURE).unwrap();
    }

    #[test]
    fn sorts_independently_of_enumeration_order() {
        let root = fixture();
        fs::write(root.path().join("python/b.txt"), b"beta").unwrap();
        let expected = Expected {
            files: 2,
            bytes: 9,
            digest: "8fa7a87911bdabe4356dff4ff63030124e965c3634c5b65cae520220bd9454df",
            ..FIXTURE
        };
        verify_expected(root.path(), &expected).unwrap();
    }

    #[test]
    fn verifies_files_under_distinct_held_parent_directories() {
        let root = fixture();
        fs::create_dir_all(root.path().join("else/sub")).unwrap();
        fs::write(root.path().join("else/sub/b"), b"beta").unwrap();
        let expected = Expected {
            files: 2,
            dirs: 3,
            bytes: 9,
            max_depth: 3,
            digest: "1029f5ffe7b1ff5e656dd525e3e1899ac9156e08fd36933cc78a729d5f1e73e8",
            ..FIXTURE
        };
        verify_expected(root.path(), &expected).unwrap();
    }

    #[test]
    fn verifies_tree_via_short_directory_alias_when_available() {
        use std::os::windows::ffi::{OsStrExt, OsStringExt};
        use windows_sys::Win32::Storage::FileSystem::GetShortPathNameW;

        let root = fixture();
        let wide: Vec<u16> = root
            .path()
            .as_os_str()
            .encode_wide()
            .chain(Some(0))
            .collect();
        let mut buffer = vec![0u16; 32768];
        let length =
            unsafe { GetShortPathNameW(wide.as_ptr(), buffer.as_mut_ptr(), buffer.len() as u32) }
                as usize;
        if length == 0 || length >= buffer.len() {
            eprintln!("tree short-directory alias unavailable");
            return;
        }
        let short = std::path::PathBuf::from(std::ffi::OsString::from_wide(&buffer[..length]));
        if short
            .to_string_lossy()
            .eq_ignore_ascii_case(&root.path().to_string_lossy())
        {
            eprintln!("tree short-directory alias unavailable");
            return;
        }
        verify_expected(&short, &FIXTURE).unwrap();
        eprintln!("tree short-directory alias verified");
    }

    #[test]
    fn refuses_changed_missing_and_extra_files() {
        let root = fixture();
        fs::write(root.path().join("python/a.txt"), b"omega").unwrap();
        assert_eq!(
            verify_expected(root.path(), &FIXTURE)
                .unwrap_err()
                .to_string(),
            "tree digest mismatch"
        );
        fs::remove_file(root.path().join("python/a.txt")).unwrap();
        assert_eq!(
            verify_expected(root.path(), &FIXTURE)
                .unwrap_err()
                .to_string(),
            "tree population mismatch"
        );
        fs::write(root.path().join("python/a.txt"), b"alpha").unwrap();
        fs::write(root.path().join("python/b.txt"), b"beta").unwrap();
        assert!(verify_expected(root.path(), &FIXTURE).is_err());
    }

    #[test]
    fn refuses_empty_directory_and_size_limits() {
        let root = fixture();
        fs::create_dir(root.path().join("empty")).unwrap();
        let expected = Expected { dirs: 2, ..FIXTURE };
        assert_eq!(
            verify_expected(root.path(), &expected)
                .unwrap_err()
                .to_string(),
            "tree population mismatch"
        );
        fs::remove_dir(root.path().join("empty")).unwrap();
        let expected = Expected {
            max_file: 4,
            ..FIXTURE
        };
        assert_eq!(
            verify_expected(root.path(), &expected)
                .unwrap_err()
                .to_string(),
            "file size exceeded"
        );
        let expected = Expected {
            bytes: 4,
            ..FIXTURE
        };
        assert_eq!(
            verify_expected(root.path(), &expected)
                .unwrap_err()
                .to_string(),
            "file size exceeded"
        );
    }

    #[test]
    fn refuses_path_depth_length_and_non_ascii_names() {
        let root = fixture();
        let depth = Expected {
            max_depth: 1,
            ..FIXTURE
        };
        assert_eq!(
            verify_expected(root.path(), &depth)
                .unwrap_err()
                .to_string(),
            "tree name or count limit exceeded"
        );
        let length = Expected {
            max_path: 11,
            ..FIXTURE
        };
        assert_eq!(
            verify_expected(root.path(), &length)
                .unwrap_err()
                .to_string(),
            "tree name or count limit exceeded"
        );
        fs::write(root.path().join("python/é.txt"), b"x").unwrap();
        let extended = Expected {
            files: 2,
            bytes: 6,
            max_path: 20,
            ..FIXTURE
        };
        assert_eq!(
            verify_expected(root.path(), &extended)
                .unwrap_err()
                .to_string(),
            "unsafe name"
        );
    }

    #[test]
    fn refuses_hardlinks_and_active_writers() {
        let root = fixture();
        let outside = tempfile::tempdir().unwrap();
        fs::hard_link(
            root.path().join("python/a.txt"),
            outside.path().join("alias"),
        )
        .unwrap();
        assert!(verify_expected(root.path(), &FIXTURE)
            .unwrap_err()
            .to_string()
            .contains("exactly one hard link"));
        fs::remove_file(outside.path().join("alias")).unwrap();
        let writer = OpenOptions::new()
            .write(true)
            .share_mode(0)
            .open(root.path().join("python/a.txt"))
            .unwrap();
        assert_eq!(
            verify_expected(root.path(), &FIXTURE)
                .unwrap_err()
                .raw_os_error(),
            Some(32)
        );
        drop(writer);
        verify_expected(root.path(), &FIXTURE).unwrap();
    }

    #[test]
    fn pins_root_and_refuses_parent_junction() {
        let parent = tempfile::tempdir().unwrap();
        let target = parent.path().join("target");
        let moved = parent.path().join("moved");
        let junction = parent.path().join("junction");
        fs::create_dir(&target).unwrap();
        let pins = pin_root(&target).unwrap();
        assert!(fs::rename(&target, &moved).is_err());
        drop(pins);
        fs::rename(&target, &moved).unwrap();
        fs::rename(&moved, &target).unwrap();
        let cmd = Path::new(&std::env::var_os("SystemRoot").unwrap())
            .join("System32")
            .join("cmd.exe");
        let output = std::process::Command::new(cmd)
            .creation_flags(0x08000000)
            .args(["/d", "/c", "mklink", "/J"])
            .arg(&junction)
            .arg(&target)
            .output()
            .unwrap();
        assert!(output.status.success());
        assert!(pin_root(&junction).is_err());
        fs::remove_dir(junction).unwrap();
    }

    #[test]
    fn refuses_new_file_added_after_directory_enumeration() {
        let root = fixture();
        let python = root.path().join("python");
        let result = verify_with_hook(root.path(), &FIXTURE, |directory| {
            if directory == python {
                fs::write(python.join("late.pth"), b"late")?;
            }
            Ok(())
        });
        assert!(result.is_err());
    }
}

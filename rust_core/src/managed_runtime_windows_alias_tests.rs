fn short_name(path: &std::path::Path) -> Option<std::path::PathBuf> {
    use std::os::windows::ffi::{OsStrExt, OsStringExt};
    use windows_sys::Win32::Storage::FileSystem::GetShortPathNameW;
    let input: Vec<u16> = path.as_os_str().encode_wide().chain(Some(0)).collect();
    let mut output = vec![0u16; 32_768];
    let len = unsafe { GetShortPathNameW(input.as_ptr(), output.as_mut_ptr(), output.len() as u32) }
        as usize;
    if len == 0 || len >= output.len() {
        return None;
    }
    Some(std::path::PathBuf::from(std::ffi::OsString::from_wide(
        &output[..len],
    )))
}

#[test]
fn held_child_checks_normalized_parent_and_opened_name() {
    let fixture = tempfile::tempdir().unwrap();
    let path = fixture.path().join("launcher.exe");
    fs::write(&path, b"fixture").unwrap();
    let parent = open_directory(fixture.path()).unwrap();
    let child = super::open_executable(&path).unwrap();
    assert!(super::is_held_direct_child(parent.as_handle(), child.as_handle(), &path).unwrap());
    assert!(!super::is_held_direct_child(
        parent.as_handle(),
        child.as_handle(),
        &fixture.path().join("other.exe")
    )
    .unwrap());
    let other = tempfile::tempdir().unwrap();
    let other_parent = open_directory(other.path()).unwrap();
    assert!(
        !super::is_held_direct_child(other_parent.as_handle(), child.as_handle(), &path).unwrap()
    );
}

#[test]
fn non_unicode_requested_and_opened_paths_fail_closed() {
    use std::os::windows::ffi::OsStringExt;
    let fixture = tempfile::tempdir().unwrap();
    let normal = fixture.path().join("normal.exe");
    let malformed = fixture
        .path()
        .join(std::ffi::OsString::from_wide(&[0xd800]));
    fs::write(&normal, b"fixture").unwrap();
    let parent = open_directory(fixture.path()).unwrap();
    let child = super::open_executable(&normal).unwrap();
    assert_eq!(
        super::is_held_direct_child(parent.as_handle(), child.as_handle(), &malformed)
            .unwrap_err()
            .kind(),
        ErrorKind::InvalidData
    );
    match fs::write(&malformed, b"fixture") {
        Ok(()) => {
            let child = super::open_executable(&malformed).unwrap();
            assert_eq!(
                super::is_held_direct_child(parent.as_handle(), child.as_handle(), &normal)
                    .unwrap_err()
                    .kind(),
                ErrorKind::InvalidData
            );
        }
        Err(error) if error.raw_os_error() == Some(123) => {
            eprintln!("malformed filename control unavailable on this filesystem")
        }
        Err(error) => panic!("malformed fixture creation failed: {error}"),
    }
}

#[test]
fn short_ancestor_preserves_junction_and_hardlink_refusals() {
    let scratch = std::path::PathBuf::from(r"C:\ProgramData");
    if !scratch.is_dir() {
        eprintln!("short ancestor unavailable: scratch parent is absent");
        return;
    }
    let scratch_pin = open_directory(&scratch).unwrap();
    assert_eq!(
        super::path_key(&super::final_path_for_handle(scratch_pin.as_handle()).unwrap()).unwrap(),
        super::path_key(&scratch).unwrap()
    );
    let short_scratch = short_name(&scratch).expect("short path query failed");
    if super::path_key(&short_scratch).unwrap() == super::path_key(&scratch).unwrap() {
        eprintln!("short ancestor unavailable: no distinct alias");
        return;
    }
    let short_pin = open_directory(&short_scratch).unwrap();
    assert!(same_file_identity(scratch_pin.as_handle(), short_pin.as_handle()).unwrap());
    let fixture = match tempfile::Builder::new()
        .prefix("tg-short-alias-")
        .tempdir_in(&scratch)
    {
        Ok(fixture) => fixture.keep(),
        Err(error) if error.kind() == ErrorKind::PermissionDenied => {
            eprintln!("short ancestor unavailable: scratch parent is not writable");
            return;
        }
        Err(error) => panic!("scratch fixture creation failed: {error}"),
    };
    eprintln!("retained disposable fixture: {}", fixture.display());
    let short_root = short_scratch.join(fixture.file_name().unwrap());
    let long_file = fixture.join("launcher.exe");
    let short_file = short_root.join("launcher.exe");
    fs::write(&long_file, b"fixture").unwrap();
    let parent = open_directory(&fixture).unwrap();
    let child = super::open_executable(&short_file).unwrap();
    assert!(
        super::is_held_direct_child(parent.as_handle(), child.as_handle(), &short_file).unwrap()
    );
    assert!(same_file_identity(
        child.as_handle(),
        open_file_for_identity(&long_file).unwrap().as_handle()
    )
    .unwrap());
    drop(child);
    let hardlink = fixture.join("hardlink.exe");
    fs::hard_link(&long_file, &hardlink).unwrap();
    for path in [&long_file, &short_file] {
        assert_eq!(
            super::open_executable(path).unwrap_err().to_string(),
            "managed runtime file must have exactly one hard link"
        );
    }
    let target = fixture.join("target");
    let junction = fixture.join("junction");
    fs::create_dir(&target).unwrap();
    let _target_pin = open_directory(&target).unwrap();
    fs::write(target.join("canary"), b"preserve").unwrap();
    assert_eq!(
        fs::symlink_metadata(&junction).unwrap_err().kind(),
        ErrorKind::NotFound
    );
    // The local OS system directory and its pinned cmd.exe are trusted for this fixture only.
    use std::os::windows::ffi::OsStringExt;
    use windows_sys::Win32::System::SystemInformation::GetSystemDirectoryW;
    let mut buffer = vec![0u16; 32_768];
    let length = unsafe { GetSystemDirectoryW(buffer.as_mut_ptr(), buffer.len() as u32) } as usize;
    assert!(length > 0 && length < buffer.len());
    let system = std::path::PathBuf::from(std::ffi::OsString::from_wide(&buffer[..length]));
    let system_pin = open_directory(&system).unwrap();
    let cmd = system.join("cmd.exe");
    let cmd_pin = OpenOptions::new()
        .read(true)
        .share_mode(1)
        .custom_flags(0x0020_0000)
        .open(&cmd)
        .unwrap();
    let cmd_metadata = cmd_pin.metadata().unwrap();
    assert!(cmd_metadata.is_file());
    assert_eq!(cmd_metadata.file_attributes() & 0x400, 0);
    assert!(
        super::is_held_direct_child(system_pin.as_handle(), cmd_pin.as_handle(), &cmd).unwrap()
    );
    let status = std::process::Command::new(cmd)
        .creation_flags(0x08000000)
        .args(["/d", "/c", "mklink", "/J"])
        .arg(&junction)
        .arg(&target)
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .status()
        .unwrap();
    assert!(
        status.success(),
        "junction creation failed (status {:?})",
        status.code()
    );
    assert_ne!(
        fs::symlink_metadata(&junction).unwrap().file_attributes() & 0x400,
        0
    );
    for path in [junction.clone(), short_root.join("junction")] {
        assert_eq!(
            open_directory(&path).unwrap_err().to_string(),
            "opened path has an unexpected type or is a reparse point"
        );
    }
    assert_eq!(fs::read(target.join("canary")).unwrap(), b"preserve");
    eprintln!("alias hardlink and junction controls completed");
}

#[test]
fn actual_short_leaf_has_same_opened_identity() {
    let fixture = tempfile::tempdir().unwrap();
    let path = fixture.path().join("long-fixture-launcher-name.exe");
    fs::write(&path, b"fixture").unwrap();
    let short = short_name(&path).expect("short path query failed");
    if short
        .file_name()
        .unwrap()
        .to_string_lossy()
        .eq_ignore_ascii_case(&path.file_name().unwrap().to_string_lossy())
    {
        eprintln!("short-leaf control unavailable: scratch filesystem supplied no alias");
        return;
    }
    let child = super::open_executable(&short).unwrap();
    let long = open_file_for_identity(&path).unwrap();
    let parent = open_directory(fixture.path()).unwrap();
    assert!(same_file_identity(child.as_handle(), long.as_handle()).unwrap());
    assert!(super::is_held_direct_child(parent.as_handle(), child.as_handle(), &short).unwrap());
}

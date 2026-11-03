//! A handle-returning creation primitive; callers must independently trust and pin the parent.
use super::{
    managed_runtime_acl_reader, managed_runtime_windows_sid::LocalAllocation, windows, windows_file,
};
use std::ffi::OsStr;
use std::fs::File;
use std::io;
use std::os::windows::ffi::OsStrExt;
use std::os::windows::fs::MetadataExt;
use std::os::windows::io::{AsHandle, AsRawHandle, FromRawHandle};
use std::ptr::null_mut;
use windows_sys::Win32::Foundation::{GENERIC_READ, GENERIC_WRITE, HANDLE, INVALID_HANDLE_VALUE};
use windows_sys::Win32::Security::Authorization::ConvertStringSecurityDescriptorToSecurityDescriptorW;
use windows_sys::Win32::Security::SECURITY_ATTRIBUTES;
use windows_sys::Win32::Storage::FileSystem::{
    FileIdInfo, GetFileInformationByHandle, GetFileInformationByHandleEx, GetFileType,
    BY_HANDLE_FILE_INFORMATION, CREATE_NEW, FILE_ATTRIBUTE_NORMAL, FILE_ATTRIBUTE_REPARSE_POINT,
    FILE_ID_INFO, FILE_LIST_DIRECTORY, FILE_READ_ATTRIBUTES, FILE_SHARE_READ, FILE_TYPE_DISK,
    READ_CONTROL, SYNCHRONIZE,
};
use windows_sys::Win32::System::LibraryLoader::{GetModuleHandleW, GetProcAddress};

type CreateDirectory2 =
    unsafe extern "system" fn(*const u16, u32, u32, u32, *const SECURITY_ATTRIBUTES) -> HANDLE;

const FILE_ALL_ACCESS_MASK: u32 = 0x001f_01ff;
const DIRECTORY_INHERIT_FLAGS: u8 = 0x03;

#[repr(C)]
struct CreateFile3Parameters {
    size: u32,
    attributes: u32,
    flags: u32,
    qos: u32,
    security: *const SECURITY_ATTRIBUTES,
    template: HANDLE,
}

type CreateFile3 =
    unsafe extern "system" fn(*const u16, u32, u32, u32, *const CreateFile3Parameters) -> HANDLE;

pub(super) struct AnchoredDirectory {
    directory: File,
    anchor: File,
}

impl AnchoredDirectory {
    pub(super) fn directory(&self) -> &File {
        &self.directory
    }

    pub(super) fn anchor(&self) -> &File {
        &self.anchor
    }
}

fn file_api() -> Option<CreateFile3> {
    let module: Vec<u16> = "kernel32.dll\0".encode_utf16().collect();
    let handle = unsafe { GetModuleHandleW(module.as_ptr()) };
    if handle.is_null() {
        return None;
    }
    let function = unsafe { GetProcAddress(handle, b"CreateFile3\0".as_ptr()) }?;
    // SAFETY: the named export has the documented five-argument CreateFile3 ABI.
    Some(unsafe { std::mem::transmute(function) })
}

/// The caller must independently pin and trust the whole parent ancestry.
/// These retained handles do not authorize later execution or protect against every ACL change.
pub(super) fn create_anchored_child(parent: &File, name: &str) -> io::Result<AnchoredDirectory> {
    create_anchored_with(parent, name, api(), file_api(), |_| Ok(()))
}

fn create_anchored_with(
    parent: &File,
    name: &str,
    directory_api: Option<CreateDirectory2>,
    anchor_api: Option<CreateFile3>,
    after_directory: impl FnOnce(&File) -> io::Result<()>,
) -> io::Result<AnchoredDirectory> {
    let anchor_api = anchor_api
        .ok_or_else(|| io::Error::new(io::ErrorKind::Unsupported, "CreateFile3 unavailable"))?;
    let directory_api = directory_api.ok_or_else(|| {
        io::Error::new(io::ErrorKind::Unsupported, "CreateDirectory2W unavailable")
    })?;
    let identity = super::managed_runtime_windows_token::current_token_sids()?;
    let directory = create_with(Some(directory_api), parent, name, &identity)?;
    let result = create_anchor(&directory, anchor_api, &identity, after_directory);
    let anchor =
        result.map_err(|_| invalid("created directory could not be anchored and may remain"))?;
    Ok(AnchoredDirectory { directory, anchor })
}

fn invalid(message: &'static str) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, message)
}

fn stage_owner_is_trusted(owner: &str, user: &str) -> bool {
    !user.is_empty() && owner == user
}

fn api() -> Option<CreateDirectory2> {
    let module: Vec<u16> = "kernel32.dll\0".encode_utf16().collect();
    let handle = unsafe { GetModuleHandleW(module.as_ptr()) };
    if handle.is_null() {
        return None;
    }
    let function = unsafe { GetProcAddress(handle, b"CreateDirectory2W\0".as_ptr()) }?;
    // SAFETY: the named Kernel32 export has the five-argument CreateDirectory2W ABI.
    Some(unsafe { std::mem::transmute(function) })
}

pub(super) fn verified_dacl(
    user: &str,
    acl: &managed_runtime_acl_reader::HandleAclDetails,
) -> io::Result<()> {
    if !acl.dacl_protected {
        return Err(invalid("DACL is not protected"));
    }
    if acl.ace_count != 1
        || acl.acl.allow_grants.len() != 1
        || acl.acl.allow_grants[0].0.as_str() != user
        || acl.acl.allow_grants[0].1 != FILE_ALL_ACCESS_MASK
        || acl.allow_grant_flags.as_slice() != &[DIRECTORY_INHERIT_FLAGS]
    {
        return Err(invalid(
            "DACL differs from the protected user-only descriptor",
        ));
    }
    Ok(())
}

fn create_anchor(
    directory: &File,
    create: CreateFile3,
    identity: &super::managed_runtime_windows_token::VerifiedTokenIdentity,
    before_create: impl FnOnce(&File) -> io::Result<()>,
) -> io::Result<File> {
    const ANCHOR: &str = ".tg-recovery-anchor";
    const DISALLOW_REDIRECTS: u32 = 0x0001_0000;
    let parent_path = windows_file::final_path_for_handle(directory.as_handle())?;
    let info = directory.metadata()?;
    if !info.is_dir()
        || info.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT != 0
        || unsafe { GetFileType(directory.as_raw_handle()) } != FILE_TYPE_DISK
    {
        return Err(invalid("anchor parent is not an ordinary directory"));
    }
    let wide: Vec<u16> = parent_path
        .join(ANCHOR)
        .as_os_str()
        .encode_wide()
        .chain(Some(0))
        .collect();
    if wide.len() > 32_768 {
        return Err(invalid("anchor path too long"));
    }
    let user = identity.user_sid();
    let sddl: Vec<u16> = format!("O:{user}D:P(A;OICI;FA;;;{user})\0")
        .encode_utf16()
        .collect();
    let mut descriptor = null_mut();
    if unsafe {
        ConvertStringSecurityDescriptorToSecurityDescriptorW(
            sddl.as_ptr(),
            1,
            &mut descriptor,
            null_mut(),
        )
    } == 0
    {
        return Err(io::Error::last_os_error());
    }
    let _descriptor = unsafe { LocalAllocation::from_raw(descriptor.cast()) }
        .ok_or_else(|| invalid("descriptor unavailable"))?;
    let attributes = SECURITY_ATTRIBUTES {
        nLength: std::mem::size_of::<SECURITY_ATTRIBUTES>() as u32,
        lpSecurityDescriptor: descriptor,
        bInheritHandle: 0,
    };
    let parameters = CreateFile3Parameters {
        size: std::mem::size_of::<CreateFile3Parameters>() as u32,
        attributes: FILE_ATTRIBUTE_NORMAL,
        flags: DISALLOW_REDIRECTS,
        qos: 0,
        security: &attributes,
        template: null_mut(),
    };
    before_create(directory)?;
    let raw = unsafe {
        create(
            wide.as_ptr(),
            GENERIC_READ | GENERIC_WRITE,
            FILE_SHARE_READ,
            CREATE_NEW,
            &parameters,
        )
    };
    if raw.is_null() || raw == INVALID_HANDLE_VALUE {
        return Err(io::Error::last_os_error());
    }
    // SAFETY: a successful CreateFile3 transfers its newly opened handle to this File.
    let anchor = unsafe { File::from_raw_handle(raw) };
    let metadata = anchor.metadata()?;
    let parent_metadata = directory.metadata()?;
    let mut attributes = BY_HANDLE_FILE_INFORMATION::default();
    let mut parent_id = FILE_ID_INFO::default();
    let mut anchor_id = FILE_ID_INFO::default();
    if !metadata.is_file()
        || metadata.len() != 0
        || metadata.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT != 0
        || !parent_metadata.is_dir()
        || parent_metadata.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT != 0
        || unsafe { GetFileType(anchor.as_raw_handle()) } != FILE_TYPE_DISK
        || unsafe { GetFileInformationByHandle(anchor.as_raw_handle(), &mut attributes) } == 0
        || attributes.nNumberOfLinks != 1
        || unsafe {
            GetFileInformationByHandleEx(
                directory.as_raw_handle(),
                FileIdInfo,
                (&mut parent_id as *mut FILE_ID_INFO).cast(),
                std::mem::size_of::<FILE_ID_INFO>() as u32,
            )
        } == 0
        || unsafe {
            GetFileInformationByHandleEx(
                anchor.as_raw_handle(),
                FileIdInfo,
                (&mut anchor_id as *mut FILE_ID_INFO).cast(),
                std::mem::size_of::<FILE_ID_INFO>() as u32,
            )
        } == 0
        || parent_id.VolumeSerialNumber != anchor_id.VolumeSerialNumber
        || windows::path_key(&windows_file::final_path_for_handle(directory.as_handle())?)?
            != windows::path_key(&parent_path)?
        || !windows_file::is_held_direct_child(
            directory.as_handle(),
            anchor.as_handle(),
            &parent_path.join(ANCHOR),
        )?
    {
        return Err(invalid("anchor identity invalid"));
    }
    for file in [directory, &anchor] {
        let acl = managed_runtime_acl_reader::read_handle_acl_details(file.as_handle())?;
        if !stage_owner_is_trusted(&acl.acl.owner, user) {
            return Err(invalid("anchor or directory owner invalid"));
        }
        verified_dacl(user, &acl)?;
    }
    Ok(anchor)
}

fn create_with(
    create: Option<CreateDirectory2>,
    parent: &File,
    name: &str,
    identity: &super::managed_runtime_windows_token::VerifiedTokenIdentity,
) -> io::Result<File> {
    create_with_check(create, parent, name, identity, |_, _| Ok(()))
}

fn create_with_check(
    create: Option<CreateDirectory2>,
    parent: &File,
    name: &str,
    identity: &super::managed_runtime_windows_token::VerifiedTokenIdentity,
    extra_check: impl FnOnce(&File, &str) -> io::Result<()>,
) -> io::Result<File> {
    let user = identity.user_sid();
    let create = create.ok_or_else(|| {
        io::Error::new(io::ErrorKind::Unsupported, "CreateDirectory2W unavailable")
    })?;
    if name.is_empty()
        || !name.is_ascii()
        || name.contains(['\\', '/'])
        || !windows::safe_component(OsStr::new(name))
        || user.len() > 184
        || !user.starts_with("S-1-")
        || !user
            .bytes()
            .all(|byte| byte.is_ascii_digit() || byte == b'-' || byte == b'S')
    {
        return Err(invalid("invalid child name or identity"));
    }
    let metadata = parent.metadata()?;
    if !metadata.is_dir()
        || metadata.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT != 0
        || unsafe { GetFileType(parent.as_raw_handle()) } != FILE_TYPE_DISK
    {
        return Err(invalid("parent is not an ordinary disk directory"));
    }
    let parent_path = windows_file::final_path_for_handle(parent.as_handle())?;
    let path = parent_path.join(name);
    let wide: Vec<u16> = path.as_os_str().encode_wide().chain(Some(0)).collect();
    if wide.len() > 32_768 {
        return Err(invalid("child path too long"));
    }
    let sddl: Vec<u16> = format!("O:{user}D:P(A;OICI;FA;;;{user})\0")
        .encode_utf16()
        .collect();
    let mut descriptor = null_mut();
    if unsafe {
        ConvertStringSecurityDescriptorToSecurityDescriptorW(
            sddl.as_ptr(),
            1,
            &mut descriptor,
            null_mut(),
        )
    } == 0
    {
        return Err(io::Error::last_os_error());
    }
    let _descriptor = unsafe { LocalAllocation::from_raw(descriptor.cast()) }
        .ok_or_else(|| invalid("descriptor unavailable"))?;
    let attributes = SECURITY_ATTRIBUTES {
        nLength: std::mem::size_of::<SECURITY_ATTRIBUTES>() as u32,
        lpSecurityDescriptor: descriptor,
        bInheritHandle: 0,
    };
    // Do not add a pathname fallback: redirects or preexisting entries must fail before creation.
    let raw = unsafe {
        create(
            wide.as_ptr(),
            FILE_LIST_DIRECTORY | FILE_READ_ATTRIBUTES | READ_CONTROL | SYNCHRONIZE,
            FILE_SHARE_READ,
            1,
            &attributes,
        )
    };
    if raw.is_null() || raw == INVALID_HANDLE_VALUE {
        return Err(io::Error::last_os_error());
    }
    // SAFETY: successful CreateDirectory2W transfers ownership of the returned handle.
    let file = unsafe { File::from_raw_handle(raw) };
    let check = || -> io::Result<()> {
        let info = file.metadata()?;
        if !info.is_dir()
            || info.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT != 0
            || unsafe { GetFileType(file.as_raw_handle()) } != FILE_TYPE_DISK
            || !windows_file::is_held_direct_child(parent.as_handle(), file.as_handle(), &path)?
        {
            return Err(invalid("created directory identity invalid"));
        }
        let acl = managed_runtime_acl_reader::read_handle_acl_details(file.as_handle())?;
        if !stage_owner_is_trusted(&acl.acl.owner, user) {
            return Err(invalid("created directory owner invalid"));
        }
        verified_dacl(user, &acl)?;
        extra_check(&file, user)
    };
    check().map_err(|_| invalid("created directory could not be validated and may remain"))?;
    Ok(file)
}

pub(super) fn create_child(parent: &File, name: &str) -> io::Result<File> {
    let identity = super::managed_runtime_windows_token::current_token_sids()?;
    create_with(api(), parent, name, &identity)
}

#[cfg(test)]
mod tests {
    use super::super::{
        managed_runtime_windows_sid::LocalAllocation,
        managed_runtime_windows_token::current_token_sids, windows_file,
    };
    use std::fs;
    use std::os::windows::ffi::OsStrExt;
    use std::os::windows::io::{AsHandle, FromRawHandle};
    use std::os::windows::process::CommandExt;
    use std::path::Path;

    #[test]
    fn anchored_child_is_created_and_both_objects_remain_held() {
        use std::os::windows::fs::OpenOptionsExt;
        if super::api().is_none() || super::file_api().is_none() {
            eprintln!("required creation API unavailable; anchor control not exercised");
            return;
        }
        let root = tempfile::tempdir().unwrap();
        let parent = windows_file::open_directory(root.path()).unwrap();
        let held = super::create_anchored_child(&parent, "stage").unwrap();
        let root_path = windows_file::final_path_for_handle(parent.as_handle()).unwrap();
        let stage = windows_file::final_path_for_handle(held.directory().as_handle()).unwrap();
        let anchor = stage.join(".tg-recovery-anchor");
        assert!(held.directory().metadata().unwrap().is_dir());
        assert_eq!(held.anchor().metadata().unwrap().len(), 0);
        assert!(windows_file::is_held_direct_child(
            held.directory().as_handle(),
            held.anchor().as_handle(),
            &anchor,
        )
        .unwrap());
        let reader = fs::OpenOptions::new()
            .read(true)
            .share_mode(7)
            .open(&anchor)
            .unwrap();
        assert_eq!(reader.metadata().unwrap().len(), 0);
        drop(reader);
        match fs::OpenOptions::new()
            .write(true)
            .share_mode(7)
            .open(&anchor)
        {
            Err(error) => assert_eq!(error.raw_os_error(), Some(32)),
            Ok(file) => {
                drop(file);
                panic!("held anchor allowed a writer");
            }
        }
        assert_eq!(
            fs::remove_file(&anchor).unwrap_err().raw_os_error(),
            Some(32)
        );
        assert_eq!(
            fs::rename(&stage, root_path.join("moved"))
                .unwrap_err()
                .raw_os_error(),
            Some(32)
        );
        drop(held);
        drop(fs::OpenOptions::new().write(true).open(&anchor).unwrap());
        fs::remove_file(anchor).unwrap();
        fs::rename(stage, root_path.join("moved")).unwrap();
        eprintln!("anchored directory and anchor lifetime controls exercised");
    }

    #[test]
    fn anchoring_requires_both_apis_before_creation() {
        let root = tempfile::tempdir().unwrap();
        let parent = windows_file::open_directory(root.path()).unwrap();
        for (directory_api, anchor_api) in [(None, super::file_api()), (super::api(), None)] {
            let error = super::create_anchored_with(
                &parent,
                "stage",
                directory_api,
                anchor_api,
                |_| Ok(()),
            )
            .err()
            .unwrap();
            assert_eq!(error.kind(), std::io::ErrorKind::Unsupported);
            assert!(!root.path().join("stage").exists());
        }
    }

    #[test]
    fn anchoring_preserves_a_planted_file_and_reports_residue() {
        if super::api().is_none() || super::file_api().is_none() {
            return;
        }
        let root = tempfile::tempdir().unwrap();
        let parent = windows_file::open_directory(root.path()).unwrap();
        let planted = root.path().join("stage/.tg-recovery-anchor");
        let error =
            super::create_anchored_with(&parent, "stage", super::api(), super::file_api(), |_| {
                fs::write(&planted, b"preserve")
            })
            .err()
            .unwrap();
        assert_eq!(
            error.to_string(),
            "created directory could not be anchored and may remain"
        );
        assert_eq!(fs::read(planted).unwrap(), b"preserve");
        drop(super::create_anchored_child(&parent, "valid").unwrap());
    }

    #[test]
    fn anchoring_failure_leaves_the_created_directory_in_place() {
        if super::api().is_none() || super::file_api().is_none() {
            return;
        }
        let root = tempfile::tempdir().unwrap();
        let parent = windows_file::open_directory(root.path()).unwrap();
        let error =
            super::create_anchored_with(&parent, "stage", super::api(), super::file_api(), |_| {
                Err(std::io::Error::other("injected"))
            })
            .err()
            .unwrap();
        assert_eq!(
            error.to_string(),
            "created directory could not be anchored and may remain"
        );
        assert!(root.path().join("stage").is_dir());
        assert!(!root.path().join("stage/.tg-recovery-anchor").exists());
        drop(super::create_anchored_child(&parent, "valid").unwrap());
    }

    #[test]
    fn existing_junction_is_not_followed() {
        if super::api().is_none() || super::file_api().is_none() {
            return;
        }
        let root = tempfile::tempdir().unwrap();
        let outside = tempfile::tempdir().unwrap();
        let canary = outside.path().join("canary");
        fs::write(&canary, b"preserve").unwrap();
        let junction = root.path().join("linked");
        let cmd = Path::new(&std::env::var_os("SystemRoot").unwrap())
            .join("System32")
            .join("cmd.exe");
        let result = std::process::Command::new(cmd)
            .creation_flags(0x08000000)
            .args(["/d", "/c", "mklink", "/J"])
            .arg(&junction)
            .arg(outside.path())
            .output()
            .unwrap();
        assert!(
            result.status.success(),
            "junction fixture command failed: {:?}; stdout={:?}; stderr={:?}",
            result.status,
            String::from_utf8_lossy(&result.stdout),
            String::from_utf8_lossy(&result.stderr)
        );
        let parent = windows_file::open_directory(root.path()).unwrap();
        let error = super::create_anchored_child(&parent, "linked")
            .err()
            .unwrap();
        assert_eq!(error.raw_os_error(), Some(183));
        assert!(!outside.path().join(".tg-recovery-anchor").exists());
        assert_eq!(fs::read(canary).unwrap(), b"preserve");
        drop(super::create_anchored_child(&parent, "valid").unwrap());
        drop(parent);
        fs::remove_dir(junction).unwrap();
    }

    #[test]
    fn anchor_creation_supplies_owner_and_redirect_safe_arguments() {
        use std::sync::atomic::{AtomicBool, Ordering};
        use windows_sys::Win32::Security::{GetSecurityDescriptorOwner, IsValidSid};
        static VALID: AtomicBool = AtomicBool::new(false);
        unsafe extern "system" fn stub(
            _: *const u16,
            access: u32,
            share: u32,
            disposition: u32,
            parameters: *const super::CreateFile3Parameters,
        ) -> super::HANDLE {
            if parameters.is_null() {
                return super::INVALID_HANDLE_VALUE;
            }
            let params = unsafe { &*parameters };
            if params.security.is_null() {
                return super::INVALID_HANDLE_VALUE;
            }
            let attributes = unsafe { &*params.security };
            let (mut owner, mut defaulted) = (std::ptr::null_mut(), 0);
            let valid = !attributes.lpSecurityDescriptor.is_null()
                && unsafe {
                    GetSecurityDescriptorOwner(
                        attributes.lpSecurityDescriptor,
                        &mut owner,
                        &mut defaulted,
                    )
                } != 0
                && !owner.is_null()
                && unsafe { IsValidSid(owner) } != 0;
            let sid = valid
                .then(|| unsafe {
                    super::super::managed_runtime_windows_sid::sid_to_string(
                        owner,
                        "owner unavailable",
                    )
                    .ok()
                })
                .flatten();
            VALID.store(
                access == (super::GENERIC_READ | super::GENERIC_WRITE)
                    && share == super::FILE_SHARE_READ
                    && disposition == super::CREATE_NEW
                    && params.size == std::mem::size_of::<super::CreateFile3Parameters>() as u32
                    && params.attributes == super::FILE_ATTRIBUTE_NORMAL
                    && params.flags == 0x0001_0000
                    && params.qos == 0
                    && params.template.is_null()
                    && attributes.nLength
                        == std::mem::size_of::<super::SECURITY_ATTRIBUTES>() as u32
                    && attributes.bInheritHandle == 0
                    && sid
                        .zip(current_token_sids().ok())
                        .is_some_and(|(sid, identity)| {
                            sid == identity.user_sid() && defaulted == 0
                        }),
                Ordering::SeqCst,
            );
            super::INVALID_HANDLE_VALUE
        }
        let root = tempfile::tempdir().unwrap();
        let parent = windows_file::open_directory(root.path()).unwrap();
        let identity = current_token_sids().unwrap();
        assert!(super::create_anchor(&parent, stub, &identity, |_| Ok(())).is_err());
        assert!(
            VALID.load(Ordering::SeqCst),
            "anchor owner or redirect-safe arguments missing"
        );
        assert_eq!(fs::read_dir(root.path()).unwrap().count(), 0);
    }

    #[test]
    fn stage_owner_must_be_the_current_user_even_if_default_owner_is_a_group() {
        let user = "S-1-5-21-1";
        assert!(super::stage_owner_is_trusted(user, user));
        assert!(!super::stage_owner_is_trusted("S-1-5-32-544", user));
        assert!(!super::stage_owner_is_trusted("S-1-5-21-2", user));
        assert!(!super::stage_owner_is_trusted("", ""));
    }

    #[test]
    fn directory_creation_explicitly_supplies_current_user_owner() {
        use std::sync::atomic::{AtomicBool, Ordering};
        use windows_sys::Win32::Security::{GetSecurityDescriptorOwner, IsValidSid};
        static OWNER: AtomicBool = AtomicBool::new(false);
        unsafe extern "system" fn stub(
            _: *const u16,
            _: u32,
            _: u32,
            _: u32,
            attributes: *const super::SECURITY_ATTRIBUTES,
        ) -> super::HANDLE {
            let descriptor = unsafe { (*attributes).lpSecurityDescriptor };
            let (mut owner, mut defaulted) = (std::ptr::null_mut(), 0);
            let valid =
                unsafe { GetSecurityDescriptorOwner(descriptor, &mut owner, &mut defaulted) } != 0
                    && !owner.is_null()
                    && unsafe { IsValidSid(owner) } != 0;
            let actual = valid.then(|| unsafe {
                super::super::managed_runtime_windows_sid::sid_to_string(owner, "owner unavailable")
                    .ok()
            });
            OWNER.store(
                actual
                    .flatten()
                    .zip(current_token_sids().ok())
                    .is_some_and(|(sid, identity)| sid == identity.user_sid() && defaulted == 0),
                Ordering::SeqCst,
            );
            super::INVALID_HANDLE_VALUE
        }
        let root = tempfile::tempdir().unwrap();
        let parent = windows_file::open_directory(root.path()).unwrap();
        let identity = current_token_sids().unwrap();
        assert!(super::create_with(Some(stub), &parent, "stage", &identity).is_err());
        assert!(
            OWNER.load(Ordering::SeqCst),
            "directory creation did not explicitly set user owner"
        );
        assert_eq!(fs::read_dir(root.path()).unwrap().count(), 0);
    }

    #[test]
    fn creates_protected_child_and_holds_its_identity() {
        let root = tempfile::tempdir().unwrap();
        let parent = windows_file::open_directory(root.path()).unwrap();
        if super::api().is_none() {
            assert_eq!(
                super::create_child(&parent, "stage").unwrap_err().kind(),
                std::io::ErrorKind::Unsupported
            );
            assert!(!root.path().join("stage").exists());
            return;
        }
        let child = super::create_child(&parent, "stage").unwrap();
        assert!(child.metadata().unwrap().is_dir());
        let nested = super::create_child(&child, "nested").unwrap();
        let stage = root.path().join("stage");
        let nested_path = stage.join("nested");
        assert!(nested.metadata().unwrap().is_dir());
        assert_eq!(
            fs::rename(&stage, root.path().join("moved"))
                .unwrap_err()
                .raw_os_error(),
            Some(32)
        );
        assert_eq!(
            fs::rename(&nested_path, stage.join("other"))
                .unwrap_err()
                .raw_os_error(),
            Some(32)
        );
        drop(nested);
        assert_eq!(
            fs::rename(&nested_path, stage.join("other"))
                .unwrap_err()
                .raw_os_error(),
            Some(32)
        );
        drop(child);
        fs::rename(&nested_path, stage.join("other")).unwrap();
        fs::rename(root.path().join("stage"), root.path().join("moved")).unwrap();
        eprintln!("protected directory and nested handle controls exercised");
    }

    #[test]
    fn refuses_invalid_child_names_before_any_creation() {
        if super::api().is_none() {
            return;
        }
        let root = tempfile::tempdir().unwrap();
        std::fs::create_dir(root.path().join("inner")).unwrap();
        let parent = windows_file::open_directory(root.path()).unwrap();
        drop(super::create_child(&parent, "valid").unwrap());
        for name in ["", "inner\\escape", "inner/escape", "..", "NUL", "C:escape"] {
            assert_eq!(
                super::create_child(&parent, name).unwrap_err().kind(),
                std::io::ErrorKind::InvalidData
            );
            assert!(!root.path().join("inner/escape").exists());
        }
    }

    #[test]
    fn missing_api_fails_without_creating_a_child() {
        let root = tempfile::tempdir().unwrap();
        let parent = windows_file::open_directory(root.path()).unwrap();
        let identity = current_token_sids().unwrap();
        assert_eq!(
            super::create_with(None, &parent, "stage", &identity)
                .unwrap_err()
                .kind(),
            std::io::ErrorKind::Unsupported
        );
        assert!(!root.path().join("stage").exists());
    }

    #[test]
    fn collisions_and_dangling_junction_are_not_followed() {
        if super::api().is_none() {
            return;
        }
        let root = tempfile::tempdir().unwrap();
        let outside = tempfile::tempdir().unwrap();
        fs::create_dir(root.path().join("existing-dir")).unwrap();
        fs::write(root.path().join("existing-file"), b"preserve").unwrap();
        let junction = root.path().join("dangling");
        let missing = outside.path().join("missing");
        let cmd = Path::new(&std::env::var_os("SystemRoot").unwrap())
            .join("System32")
            .join("cmd.exe");
        let output = std::process::Command::new(cmd)
            .creation_flags(0x08000000)
            .args(["/d", "/c", "mklink", "/J"])
            .arg(&junction)
            .arg(&missing)
            .output()
            .unwrap();
        assert!(output.status.success());
        let parent = windows_file::open_directory(root.path()).unwrap();
        drop(super::create_child(&parent, "valid").unwrap());
        for name in ["existing-dir", "existing-file", "dangling"] {
            assert_eq!(
                super::create_child(&parent, name)
                    .unwrap_err()
                    .raw_os_error(),
                Some(183),
                "unexpected error for {name}"
            );
        }
        assert_eq!(
            fs::read(root.path().join("existing-file")).unwrap(),
            b"preserve"
        );
        assert!(!missing.exists());
        drop(parent);
        fs::remove_dir(junction).unwrap();
    }

    #[test]
    fn dacl_check_rejects_foreign_inherit_only_grants() {
        if super::api().is_none() {
            return;
        }
        let identity = current_token_sids().unwrap();
        let root = tempfile::tempdir().unwrap();
        let parent = windows_file::open_directory(root.path()).unwrap();
        let good = super::create_child(&parent, "good").unwrap();
        let acl =
            super::managed_runtime_acl_reader::read_handle_acl_details(good.as_handle()).unwrap();
        super::verified_dacl(identity.user_sid(), &acl).unwrap();
        drop(good);
        let sddl: Vec<u16> = format!(
            "D:P(A;OICI;FA;;;{})(A;OICIIO;FA;;;WD)\0",
            identity.user_sid()
        )
        .encode_utf16()
        .collect();
        let mut descriptor = std::ptr::null_mut();
        assert_ne!(
            unsafe {
                super::ConvertStringSecurityDescriptorToSecurityDescriptorW(
                    sddl.as_ptr(),
                    1,
                    &mut descriptor,
                    std::ptr::null_mut(),
                )
            },
            0
        );
        let _allocation = unsafe { LocalAllocation::from_raw(descriptor.cast()) }.unwrap();
        let attributes = super::SECURITY_ATTRIBUTES {
            nLength: std::mem::size_of::<super::SECURITY_ATTRIBUTES>() as u32,
            lpSecurityDescriptor: descriptor,
            bInheritHandle: 0,
        };
        let path: Vec<u16> = root
            .path()
            .join("foreign")
            .as_os_str()
            .encode_wide()
            .chain(Some(0))
            .collect();
        let raw = unsafe {
            super::api().unwrap()(
                path.as_ptr(),
                super::FILE_LIST_DIRECTORY
                    | super::FILE_READ_ATTRIBUTES
                    | super::READ_CONTROL
                    | super::SYNCHRONIZE,
                super::FILE_SHARE_READ,
                1,
                &attributes,
            )
        };
        assert!(!raw.is_null() && raw != super::INVALID_HANDLE_VALUE);
        let file = unsafe { std::fs::File::from_raw_handle(raw) };
        let acl =
            super::managed_runtime_acl_reader::read_handle_acl_details(file.as_handle()).unwrap();
        assert!(super::verified_dacl(identity.user_sid(), &acl).is_err());
    }

    #[test]
    fn validation_failure_leaves_the_created_directory_in_place() {
        if super::api().is_none() {
            return;
        }
        let root = tempfile::tempdir().unwrap();
        let parent = windows_file::open_directory(root.path()).unwrap();
        let identity = current_token_sids().unwrap();
        let result = super::create_with_check(super::api(), &parent, "stage", &identity, |_, _| {
            Err(std::io::Error::other("injected validation failure"))
        });
        assert_eq!(
            result.unwrap_err().to_string(),
            "created directory could not be validated and may remain"
        );
        assert!(root.path().join("stage").is_dir());
    }
}

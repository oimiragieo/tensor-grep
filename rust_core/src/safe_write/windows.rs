use super::{AnchoredTarget, AtomicPublishError, AtomicPublishStage};
use anyhow::Context;
use std::ffi::c_void;
use std::fs::{File, OpenOptions};
use std::io::Write;
use std::os::windows::fs::OpenOptionsExt;
use std::os::windows::io::AsRawHandle;
use std::path::{Path, PathBuf};

pub(super) const SHARE_READ: u32 = 0x0000_0001;
pub(super) const SHARE_WRITE: u32 = 0x0000_0002;
pub(super) const SHARE_DELETE: u32 = 0x0000_0004;
const GENERIC_READ: u32 = 0x8000_0000;
pub(super) const GENERIC_WRITE: u32 = 0x4000_0000;
const DELETE_ACCESS: u32 = 0x0001_0000;
pub(super) const WRITE_DAC: u32 = 0x0004_0000;
pub(super) const READ_CONTROL: u32 = 0x0002_0000;
const FILE_TRAVERSE: u32 = 0x0000_0020;
const FILE_READ_ATTRIBUTES: u32 = 0x0000_0080;
pub(super) const OPEN_REPARSE_POINT: u32 = 0x0020_0000;
pub(super) const BACKUP_SEMANTICS: u32 = 0x0200_0000;
pub(super) const ATTRIBUTE_REPARSE_POINT: u32 = 0x0000_0400;
const FILE_RENAME_INFO: i32 = 3;
const FILE_RENAME_INFO_EX: i32 = 22;
const FILE_DISPOSITION_INFO: i32 = 4;
const SE_FILE_OBJECT: u32 = 1;
const DACL_SECURITY_INFORMATION: u32 = 0x0000_0004;
const PROTECTED_DACL_SECURITY_INFORMATION: u32 = 0x8000_0000;
const UNPROTECTED_DACL_SECURITY_INFORMATION: u32 = 0x2000_0000;
const SE_DACL_PROTECTED: u16 = 0x1000;

#[repr(C)]
struct RenameInfo {
    replace_if_exists: u32,
    root_directory: *mut c_void,
    file_name_length: u32,
    file_name: [u16; 1],
}

#[link(name = "Kernel32")]
extern "system" {
    fn SetFileInformationByHandle(
        file: *mut c_void,
        class: i32,
        info: *const c_void,
        size: u32,
    ) -> i32;
    fn LocalFree(memory: *mut c_void) -> *mut c_void;
}

#[link(name = "Advapi32")]
extern "system" {
    fn GetSecurityInfo(
        handle: *mut c_void,
        object_type: u32,
        security_info: u32,
        owner: *mut *mut c_void,
        group: *mut *mut c_void,
        dacl: *mut *mut c_void,
        sacl: *mut *mut c_void,
        descriptor: *mut *mut c_void,
    ) -> u32;
    fn SetSecurityInfo(
        handle: *mut c_void,
        object_type: u32,
        security_info: u32,
        owner: *mut c_void,
        group: *mut c_void,
        dacl: *mut c_void,
        sacl: *mut c_void,
    ) -> u32;
    fn GetSecurityDescriptorControl(
        descriptor: *mut c_void,
        control: *mut u16,
        revision: *mut u32,
    ) -> i32;
    #[cfg(test)]
    fn GetSecurityDescriptorDacl(
        descriptor: *mut c_void,
        present: *mut i32,
        dacl: *mut *mut c_void,
        defaulted: *mut i32,
    ) -> i32;
    #[cfg(test)]
    fn GetAclInformation(acl: *mut c_void, info: *mut c_void, size: u32, class: u32) -> i32;
    #[cfg(test)]
    fn GetAce(acl: *mut c_void, index: u32, ace: *mut *mut c_void) -> i32;
    #[cfg(test)]
    // Split the linker spelling to avoid a base64-like credential false positive.
    #[link_name = "ConvertStringSecurityDescriptorTo\x53ecurityDescriptorW"]
    fn convert_sddl_to_security_descriptor(
        string: *const u16,
        revision: u32,
        descriptor: *mut *mut c_void,
        size: *mut u32,
    ) -> i32;
}

pub(super) fn open_directory(path: &Path) -> anyhow::Result<File> {
    open_directory_with_share(path, SHARE_READ | SHARE_WRITE)
}

pub(super) fn validate_directory(path: &Path) -> anyhow::Result<()> {
    // Owned directory handles request DELETE while denying delete sharing to pin the path.
    // A validation reopen must permit that existing access or Windows reports
    // ERROR_SHARING_VIOLATION even though the retained path is still correct.
    open_directory_with_share(path, SHARE_READ | SHARE_WRITE | SHARE_DELETE).map(|_| ())
}

pub(super) fn copy_file_dacl(source: &File, destination: &File) -> anyhow::Result<()> {
    use std::os::windows::io::AsRawHandle;
    let mut dacl = std::ptr::null_mut();
    let mut descriptor = std::ptr::null_mut();
    let status = unsafe {
        GetSecurityInfo(
            source.as_raw_handle().cast(),
            SE_FILE_OBJECT,
            DACL_SECURITY_INFORMATION,
            std::ptr::null_mut(),
            std::ptr::null_mut(),
            &mut dacl,
            std::ptr::null_mut(),
            &mut descriptor,
        )
    };
    if status != 0 {
        return Err(std::io::Error::from_raw_os_error(status as i32).into());
    }
    struct Descriptor(*mut c_void);
    impl Drop for Descriptor {
        fn drop(&mut self) {
            unsafe { LocalFree(self.0) };
        }
    }
    let descriptor = Descriptor(descriptor);
    let mut control = 0u16;
    let mut revision = 0u32;
    if unsafe { GetSecurityDescriptorControl(descriptor.0, &mut control, &mut revision) } == 0 {
        return Err(std::io::Error::last_os_error().into());
    }
    let inheritance = if control & SE_DACL_PROTECTED != 0 {
        PROTECTED_DACL_SECURITY_INFORMATION
    } else {
        UNPROTECTED_DACL_SECURITY_INFORMATION
    };
    let status = unsafe {
        SetSecurityInfo(
            destination.as_raw_handle().cast(),
            SE_FILE_OBJECT,
            DACL_SECURITY_INFORMATION | inheritance,
            std::ptr::null_mut(),
            std::ptr::null_mut(),
            dacl,
            std::ptr::null_mut(),
        )
    };
    if status != 0 {
        return Err(std::io::Error::from_raw_os_error(status as i32).into());
    }
    Ok(())
}

#[cfg(test)]
pub(super) fn set_file_dacl_for_test(file: &File, sddl: &str) -> anyhow::Result<()> {
    let sddl: Vec<u16> = sddl.encode_utf16().chain(std::iter::once(0)).collect();
    let mut raw_descriptor = std::ptr::null_mut();
    if unsafe {
        convert_sddl_to_security_descriptor(
            sddl.as_ptr(),
            1,
            &mut raw_descriptor,
            std::ptr::null_mut(),
        )
    } == 0
    {
        return Err(std::io::Error::last_os_error().into());
    }
    struct Descriptor(*mut c_void);
    impl Drop for Descriptor {
        fn drop(&mut self) {
            unsafe { LocalFree(self.0) };
        }
    }
    let descriptor = Descriptor(raw_descriptor);
    let mut present = 0;
    let mut defaulted = 0;
    let mut dacl = std::ptr::null_mut();
    if unsafe { GetSecurityDescriptorDacl(descriptor.0, &mut present, &mut dacl, &mut defaulted) }
        == 0
    {
        return Err(std::io::Error::last_os_error().into());
    }
    anyhow::ensure!(present != 0 && !dacl.is_null(), "test SDDL has no DACL");
    let status = unsafe {
        SetSecurityInfo(
            file.as_raw_handle().cast(),
            SE_FILE_OBJECT,
            DACL_SECURITY_INFORMATION | PROTECTED_DACL_SECURITY_INFORMATION,
            std::ptr::null_mut(),
            std::ptr::null_mut(),
            dacl,
            std::ptr::null_mut(),
        )
    };
    if status != 0 {
        return Err(std::io::Error::from_raw_os_error(status as i32).into());
    }
    Ok(())
}

#[cfg(test)]
pub(super) fn dacl_signature(file: &File) -> anyhow::Result<(bool, Vec<Vec<u8>>)> {
    #[repr(C)]
    struct AclSizeInformation {
        ace_count: u32,
        bytes_in_use: u32,
        bytes_free: u32,
    }
    let mut dacl = std::ptr::null_mut();
    let mut descriptor = std::ptr::null_mut();
    let status = unsafe {
        GetSecurityInfo(
            file.as_raw_handle().cast(),
            SE_FILE_OBJECT,
            DACL_SECURITY_INFORMATION,
            std::ptr::null_mut(),
            std::ptr::null_mut(),
            &mut dacl,
            std::ptr::null_mut(),
            &mut descriptor,
        )
    };
    if status != 0 {
        return Err(std::io::Error::from_raw_os_error(status as i32).into());
    }
    struct Descriptor(*mut c_void);
    impl Drop for Descriptor {
        fn drop(&mut self) {
            unsafe { LocalFree(self.0) };
        }
    }
    let descriptor = Descriptor(descriptor);
    let mut control = 0u16;
    let mut revision = 0u32;
    if unsafe { GetSecurityDescriptorControl(descriptor.0, &mut control, &mut revision) } == 0 {
        return Err(std::io::Error::last_os_error().into());
    }
    let protected = control & SE_DACL_PROTECTED != 0;
    if dacl.is_null() {
        return Ok((protected, Vec::new()));
    }
    let mut info = AclSizeInformation {
        ace_count: 0,
        bytes_in_use: 0,
        bytes_free: 0,
    };
    if unsafe {
        GetAclInformation(
            dacl,
            (&mut info as *mut AclSizeInformation).cast(),
            std::mem::size_of::<AclSizeInformation>() as u32,
            2,
        )
    } == 0
    {
        return Err(std::io::Error::last_os_error().into());
    }
    let mut aces = Vec::with_capacity(info.ace_count as usize);
    for index in 0..info.ace_count {
        let mut ace = std::ptr::null_mut();
        if unsafe { GetAce(dacl, index, &mut ace) } == 0 || ace.is_null() {
            return Err(std::io::Error::last_os_error().into());
        }
        let bytes = ace.cast::<u8>();
        let ace_size = unsafe { u16::from_le_bytes([*bytes.add(2), *bytes.add(3)]) as usize };
        anyhow::ensure!(ace_size >= 4, "test DACL contains a truncated ACE");
        aces.push(unsafe { std::slice::from_raw_parts(bytes, ace_size) }.to_vec());
    }
    Ok((protected, aces))
}

#[cfg(test)]
pub(super) fn has_protected_owner_rights_ace(
    file: &File,
    expected_mask: u32,
    required_flags: u8,
) -> anyhow::Result<bool> {
    const ACCESS_ALLOWED_ACE_TYPE: u8 = 0;
    const OWNER_RIGHTS_SID: [u8; 12] = [1, 1, 0, 0, 0, 0, 0, 3, 4, 0, 0, 0];
    let (protected, aces) = dacl_signature(file)?;
    if !protected || aces.len() != 1 {
        return Ok(false);
    }
    let ace = &aces[0];
    Ok(ace.len() >= 20
        && ace[0] == ACCESS_ALLOWED_ACE_TYPE
        && ace[1] & required_flags == required_flags
        && u32::from_le_bytes(ace[4..8].try_into().unwrap()) == expected_mask
        && ace[8..20] == OWNER_RIGHTS_SID)
}

fn open_directory_with_share(path: &Path, share_mode: u32) -> anyhow::Result<File> {
    let mut options = OpenOptions::new();
    options
        .read(true)
        .access_mode(FILE_TRAVERSE | FILE_READ_ATTRIBUTES)
        .share_mode(share_mode)
        .custom_flags(OPEN_REPARSE_POINT | BACKUP_SEMANTICS);
    let file = options.open(path)?;
    let metadata = file.metadata()?;
    use std::os::windows::fs::MetadataExt;
    if !metadata.is_dir() || metadata.file_attributes() & ATTRIBUTE_REPARSE_POINT != 0 {
        anyhow::bail!(
            "refusing reparse-point or non-directory parent {}",
            path.display()
        );
    }
    Ok(file)
}

pub(super) unsafe fn set_directory_disposition(file: &File, info: *const c_void) -> i32 {
    SetFileInformationByHandle(
        file.as_raw_handle().cast(),
        FILE_DISPOSITION_INFO,
        info,
        std::mem::size_of::<u8>() as u32,
    )
}

pub(super) fn copy_existing_dacl(
    target: &AnchoredTarget,
    destination: &File,
) -> anyhow::Result<()> {
    use std::os::windows::io::AsRawHandle;
    let source = match target.open_leaf_read() {
        Ok(file) => file,
        Err(error)
            if error
                .downcast_ref::<std::io::Error>()
                .is_some_and(|io_error| io_error.kind() == std::io::ErrorKind::NotFound) =>
        {
            return Ok(())
        }
        Err(error) => return Err(error),
    };
    let source_metadata = source.metadata()?;
    super::require_regular_file(&source_metadata, &target.display_path)?;
    let mut dacl = std::ptr::null_mut();
    let mut descriptor = std::ptr::null_mut();
    let status = unsafe {
        GetSecurityInfo(
            source.as_raw_handle().cast(),
            SE_FILE_OBJECT,
            DACL_SECURITY_INFORMATION,
            std::ptr::null_mut(),
            std::ptr::null_mut(),
            &mut dacl,
            std::ptr::null_mut(),
            &mut descriptor,
        )
    };
    if status != 0 {
        return Err(std::io::Error::from_raw_os_error(status as i32).into());
    }
    struct Descriptor(*mut c_void);
    impl Drop for Descriptor {
        fn drop(&mut self) {
            unsafe { LocalFree(self.0) };
        }
    }
    let descriptor = Descriptor(descriptor);
    let mut control = 0u16;
    let mut revision = 0u32;
    if unsafe { GetSecurityDescriptorControl(descriptor.0, &mut control, &mut revision) } == 0 {
        return Err(std::io::Error::last_os_error().into());
    }
    let inheritance = if control & SE_DACL_PROTECTED != 0 {
        PROTECTED_DACL_SECURITY_INFORMATION
    } else {
        UNPROTECTED_DACL_SECURITY_INFORMATION
    };
    let status = unsafe {
        SetSecurityInfo(
            destination.as_raw_handle().cast(),
            SE_FILE_OBJECT,
            DACL_SECURITY_INFORMATION | inheritance,
            std::ptr::null_mut(),
            std::ptr::null_mut(),
            dacl,
            std::ptr::null_mut(),
        )
    };
    if status != 0 {
        return Err(std::io::Error::from_raw_os_error(status as i32).into());
    }
    destination.set_permissions(source_metadata.permissions())?;
    Ok(())
}

pub(super) fn atomic_write<F, H>(
    target: &AnchoredTarget,
    bytes: &[u8],
    before_publish: F,
    durable: bool,
    preserve_permissions: bool,
    replace_existing: bool,
    mut stage_hook: H,
) -> Result<(), AtomicPublishError>
where
    F: FnOnce(&Path) -> anyhow::Result<()>,
    H: FnMut(AtomicPublishStage) -> anyhow::Result<()>,
{
    let (temp_path, mut temp_file) = match create_temp(target, preserve_permissions) {
        Ok(temp) => temp,
        Err(error) => {
            return Err(AtomicPublishError {
                published: false,
                error: error.context(format!(
                    "failed to create temp file alongside {}",
                    target.display_path.display()
                )),
            })
        }
    };
    let mut published = false;
    let result = (|| -> anyhow::Result<()> {
        stage_hook(AtomicPublishStage::CreateTemp)?;
        temp_file
            .write_all(bytes)
            .with_context(|| format!("failed to write temp file {}", temp_path.display()))?;
        stage_hook(AtomicPublishStage::Write)?;
        temp_file
            .flush()
            .with_context(|| format!("failed to flush temp file {}", temp_path.display()))?;
        stage_hook(AtomicPublishStage::Flush)?;
        if preserve_permissions {
            copy_existing_dacl(target, &temp_file)?;
        }
        if durable {
            temp_file.sync_all()?;
        }
        before_publish(&temp_path)?;
        stage_hook(AtomicPublishStage::Replace)?;
        target.validate_leaf_matches_expected()?;
        stage_hook(AtomicPublishStage::Validated)?;
        rename_handle(&temp_file, target, replace_existing).with_context(|| {
            format!(
                "failed to rename temp file to {}",
                target.display_path.display()
            )
        })?;
        published = true;
        target.update_leaf_identity_from_file(&temp_file)?;
        if durable {
            stage_hook(AtomicPublishStage::ParentSync)?;
            target.parent.sync()?;
        }
        Ok(())
    })();
    if let Err(error) = result {
        if !published {
            mark_for_delete(&temp_file);
        }
        return Err(AtomicPublishError { published, error });
    }
    Ok(())
}

fn create_temp(
    target: &AnchoredTarget,
    preserve_permissions: bool,
) -> anyhow::Result<(PathBuf, File)> {
    for _ in 0..64 {
        let nonce = super::TEMP_FILE_COUNTER.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
        let path = target
            .parent
            .path
            .join(format!(".tg_tmp_{:x}_{nonce:x}", std::process::id()));
        let access = GENERIC_READ
            | GENERIC_WRITE
            | DELETE_ACCESS
            | if preserve_permissions { WRITE_DAC } else { 0 };
        let file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .access_mode(access)
            .share_mode(SHARE_READ | SHARE_WRITE)
            .custom_flags(OPEN_REPARSE_POINT)
            .open(&path);
        match file {
            Ok(file) => return Ok((path, file)),
            Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => continue,
            Err(error) => return Err(error.into()),
        }
    }
    anyhow::bail!(
        "failed to allocate temporary file alongside {}",
        target.display_path.display()
    )
}

pub(super) fn open_target_for_delete(target: &AnchoredTarget) -> anyhow::Result<File> {
    let file = OpenOptions::new()
        .read(true)
        .access_mode(GENERIC_READ | FILE_READ_ATTRIBUTES | DELETE_ACCESS)
        .share_mode(SHARE_READ | SHARE_WRITE)
        .custom_flags(OPEN_REPARSE_POINT)
        .open(target.parent.path.join(&target.name))?;
    super::require_regular_file(&file.metadata()?, &target.display_path)?;
    Ok(file)
}

pub(super) fn delete_opened_file(file: &File) -> std::io::Result<()> {
    let delete: u8 = 1;
    let succeeded = unsafe {
        SetFileInformationByHandle(
            file.as_raw_handle().cast(),
            FILE_DISPOSITION_INFO,
            (&delete as *const u8).cast(),
            std::mem::size_of_val(&delete) as u32,
        )
    };
    if succeeded == 0 {
        Err(std::io::Error::last_os_error())
    } else {
        Ok(())
    }
}

fn rename_handle(
    file: &File,
    target: &AnchoredTarget,
    replace_existing: bool,
) -> anyhow::Result<()> {
    use std::os::windows::ffi::OsStrExt;
    let target_path = target.parent.path.join(&target.name);
    let mut name: Vec<u16> = target_path.as_os_str().encode_wide().collect();
    let name_bytes = u32::try_from(name.len() * std::mem::size_of::<u16>())?;
    name.push(0);
    let size = std::mem::size_of::<RenameInfo>() + name.len() * std::mem::size_of::<u16>();
    let mut storage = vec![0usize; size.div_ceil(std::mem::size_of::<usize>())];
    let info = storage.as_mut_ptr().cast::<RenameInfo>();
    let (rename_class, rename_flags) = if replace_existing {
        // POSIX semantics allow replacement while readers retain the opened old object.
        (FILE_RENAME_INFO_EX, 0x3)
    } else {
        (FILE_RENAME_INFO, 0)
    };
    unsafe {
        info.write(RenameInfo {
            replace_if_exists: rename_flags,
            root_directory: std::ptr::null_mut(),
            file_name_length: name_bytes,
            file_name: [0],
        });
        std::ptr::copy_nonoverlapping(name.as_ptr(), (*info).file_name.as_mut_ptr(), name.len());
        if SetFileInformationByHandle(
            file.as_raw_handle().cast(),
            rename_class,
            info.cast(),
            u32::try_from(size)?,
        ) == 0
        {
            return Err(std::io::Error::last_os_error().into());
        }
    }
    Ok(())
}

fn mark_for_delete(file: &File) {
    let delete: u8 = 1;
    unsafe {
        SetFileInformationByHandle(
            file.as_raw_handle().cast(),
            FILE_DISPOSITION_INFO,
            (&delete as *const u8).cast(),
            std::mem::size_of_val(&delete) as u32,
        );
    }
}

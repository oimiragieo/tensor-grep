#![cfg(all(test, windows))]

use crate::managed_runtime_windows_sid::LocalAllocation;
use std::fs::{File, OpenOptions};
use std::io::{self, Write};
use std::os::windows::fs::OpenOptionsExt;
use std::os::windows::io::{AsHandle, AsRawHandle, BorrowedHandle};
use std::path::PathBuf;
use windows_sys::Win32::Security::Authorization::{
    ConvertStringSecurityDescriptorToSecurityDescriptorW, GetSecurityInfo, SetSecurityInfo,
    SE_FILE_OBJECT,
};
use windows_sys::Win32::Security::{
    AclSizeInformation, GetAclInformation, GetSecurityDescriptorControl, GetSecurityDescriptorDacl,
    IsValidAcl, ACL, ACL_SIZE_INFORMATION, DACL_SECURITY_INFORMATION,
    PROTECTED_DACL_SECURITY_INFORMATION, PSECURITY_DESCRIPTOR, SE_DACL_PRESENT, SE_DACL_PROTECTED,
    UNPROTECTED_DACL_SECURITY_INFORMATION,
};
use windows_sys::Win32::Storage::FileSystem::{READ_CONTROL, WRITE_DAC};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum FixtureDaclShape {
    Null,
    Empty,
    NonEmpty,
}

pub(crate) struct AclFixture {
    file: File,
    _original_descriptor: LocalAllocation,
    original_dacl: *mut ACL,
    original_control: u16,
    restored: bool,
    directory: tempfile::TempDir,
}

impl AclFixture {
    pub(crate) fn new() -> io::Result<Self> {
        let directory = tempfile::tempdir()?;
        let path = directory.path().join("managed-runtime-acl-fixture.bin");
        File::create(&path)?;
        // Open with restore rights before DACL changes; retain the descriptor and handle.
        let file = OpenOptions::new()
            .access_mode(READ_CONTROL | WRITE_DAC)
            .share_mode(7)
            .open(&path)?;
        let mut dacl: *mut ACL = std::ptr::null_mut();
        let mut descriptor: PSECURITY_DESCRIPTOR = std::ptr::null_mut();
        let status = unsafe {
            GetSecurityInfo(
                file.as_raw_handle(),
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
            return Err(io::Error::from_raw_os_error(status as i32));
        }
        let descriptor_owner = unsafe { LocalAllocation::from_raw(descriptor.cast()) }
            .ok_or_else(|| invalid_fixture("original security descriptor is null"))?;
        let mut control = 0u16;
        let mut revision = 0u32;
        if unsafe { GetSecurityDescriptorControl(descriptor, &mut control, &mut revision) } == 0 {
            return Err(io::Error::last_os_error());
        }
        if control & SE_DACL_PRESENT == 0 || dacl.is_null() {
            return Err(invalid_fixture(
                "temporary file did not have a restorable DACL",
            ));
        }
        Ok(Self {
            file,
            _original_descriptor: descriptor_owner,
            original_dacl: dacl,
            original_control: control,
            restored: false,
            directory,
        })
    }

    pub(crate) fn path(&self) -> PathBuf {
        self.directory
            .path()
            .join("managed-runtime-acl-fixture.bin")
    }

    pub(crate) fn handle(&self) -> BorrowedHandle<'_> {
        self.file.as_handle()
    }

    pub(crate) fn set_null_dacl(&mut self) -> io::Result<()> {
        self.set_protected_dacl(std::ptr::null())
    }

    pub(crate) fn set_sddl_dacl(&mut self, sddl: &str) -> io::Result<()> {
        let wide = sddl
            .encode_utf16()
            .chain(std::iter::once(0))
            .collect::<Vec<_>>();
        let mut descriptor: PSECURITY_DESCRIPTOR = std::ptr::null_mut();
        let mut descriptor_size = 0u32;
        if unsafe {
            ConvertStringSecurityDescriptorToSecurityDescriptorW(
                wide.as_ptr(),
                1,
                &mut descriptor,
                &mut descriptor_size,
            )
        } == 0
        {
            return Err(io::Error::last_os_error());
        }
        let _descriptor_owner = unsafe { LocalAllocation::from_raw(descriptor.cast()) }
            .ok_or_else(|| invalid_fixture("test security descriptor is null"))?;
        let mut present = 0;
        let mut dacl: *mut ACL = std::ptr::null_mut();
        let mut defaulted = 0;
        if unsafe { GetSecurityDescriptorDacl(descriptor, &mut present, &mut dacl, &mut defaulted) }
            == 0
        {
            return Err(io::Error::last_os_error());
        }
        if present == 0 || dacl.is_null() {
            return Err(invalid_fixture("test SDDL did not produce a non-null DACL"));
        }
        self.set_protected_dacl(dacl)
    }

    pub(crate) fn open_with_access(&self, access: u32) -> io::Result<File> {
        OpenOptions::new()
            .access_mode(access)
            .share_mode(7)
            .open(self.path())
    }

    pub(crate) fn query_dacl_shape(&self) -> io::Result<FixtureDaclShape> {
        let mut dacl: *mut ACL = std::ptr::null_mut();
        let mut descriptor: PSECURITY_DESCRIPTOR = std::ptr::null_mut();
        let status = unsafe {
            GetSecurityInfo(
                self.handle().as_raw_handle(),
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
            return Err(io::Error::from_raw_os_error(status as i32));
        }
        let _descriptor_owner = unsafe { LocalAllocation::from_raw(descriptor.cast()) }
            .ok_or_else(|| invalid_fixture("queried security descriptor is null"))?;
        if dacl.is_null() {
            return Ok(FixtureDaclShape::Null);
        }
        if unsafe { IsValidAcl(dacl) } == 0 {
            return Err(invalid_fixture("queried DACL is invalid"));
        }
        let mut information = ACL_SIZE_INFORMATION::default();
        if unsafe {
            GetAclInformation(
                dacl,
                (&mut information as *mut ACL_SIZE_INFORMATION).cast(),
                std::mem::size_of::<ACL_SIZE_INFORMATION>() as u32,
                AclSizeInformation,
            )
        } == 0
        {
            return Err(io::Error::last_os_error());
        }
        if information.AceCount == 0 {
            Ok(FixtureDaclShape::Empty)
        } else {
            Ok(FixtureDaclShape::NonEmpty)
        }
    }

    pub(crate) fn restore(&mut self) -> io::Result<()> {
        if self.restored {
            return Ok(());
        }
        let inheritance = if self.original_control & SE_DACL_PROTECTED != 0 {
            PROTECTED_DACL_SECURITY_INFORMATION
        } else {
            UNPROTECTED_DACL_SECURITY_INFORMATION
        };
        let status = unsafe {
            SetSecurityInfo(
                self.file.as_raw_handle(),
                SE_FILE_OBJECT,
                DACL_SECURITY_INFORMATION | inheritance,
                std::ptr::null_mut(),
                std::ptr::null_mut(),
                self.original_dacl,
                std::ptr::null_mut(),
            )
        };
        if status != 0 {
            return Err(io::Error::from_raw_os_error(status as i32));
        }
        self.restored = true;
        Ok(())
    }

    fn set_protected_dacl(&mut self, dacl: *const ACL) -> io::Result<()> {
        self.restored = false;
        let status = unsafe {
            SetSecurityInfo(
                self.file.as_raw_handle(),
                SE_FILE_OBJECT,
                DACL_SECURITY_INFORMATION | PROTECTED_DACL_SECURITY_INFORMATION,
                std::ptr::null_mut(),
                std::ptr::null_mut(),
                dacl,
                std::ptr::null_mut(),
            )
        };
        if status == 0 {
            Ok(())
        } else {
            Err(io::Error::from_raw_os_error(status as i32))
        }
    }
}

fn invalid_fixture(message: &'static str) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, message)
}

impl Drop for AclFixture {
    fn drop(&mut self) {
        if self.restore().is_err() {
            if std::thread::panicking() {
                let _ = writeln!(
                    std::io::stderr().lock(),
                    "ACL fixture cleanup failed during panic; original DACL restoration failed"
                );
            } else {
                panic!("ACL fixture cleanup failed; original DACL restoration failed");
            }
        }
    }
}

#[cfg(test)]
mod characterization_tests {
    use super::{AclFixture, FixtureDaclShape};
    use windows_sys::Win32::Storage::FileSystem::FILE_READ_ATTRIBUTES;

    #[test]
    fn sdk_query_distinguishes_null_dacl_from_empty_dacl() {
        let mut fixture = AclFixture::new().unwrap();
        fixture.set_null_dacl().unwrap();

        let null_shape = fixture.query_dacl_shape().unwrap();

        assert_eq!(null_shape, FixtureDaclShape::Null);
        fixture.set_sddl_dacl("D:P").unwrap();
        let empty_shape = fixture.query_dacl_shape().unwrap();

        assert_eq!(empty_shape, FixtureDaclShape::Empty);
        fixture.restore().unwrap();
    }

    #[test]
    fn empty_dacl_denies_new_read_and_restoration_reenables_it() {
        let mut fixture = AclFixture::new().unwrap();
        fixture.restore().unwrap();
        fixture.set_sddl_dacl("D:P").unwrap();
        let denied = fixture.open_with_access(FILE_READ_ATTRIBUTES);

        assert!(denied.is_err());
        assert_eq!(denied.unwrap_err().raw_os_error(), Some(5));
        fixture.restore().unwrap();
        let readable = fixture.open_with_access(FILE_READ_ATTRIBUTES);

        assert!(readable.is_ok());
    }
}

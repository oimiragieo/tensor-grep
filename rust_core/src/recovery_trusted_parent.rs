//! Read-only parent observation; retained handles do not freeze ACL changes by existing handles.
//! User/default-owner/Administrators ownership is considered only for legacy observation.
//! This is not stage-owner policy, writable-access proof, witness ACL/content trust,
//! a clean runtime, or authority to stage, execute, or activate.
use super::{
    managed_runtime_acl_policy, managed_runtime_acl_reader, managed_runtime_windows_token,
    recovery_python_tree, windows, windows_file,
};
use std::fs::File;
use std::io;
use std::os::windows::ffi::OsStrExt;
use std::os::windows::io::AsHandle;
use std::path::Path;
use std::ptr::null_mut;
use windows_sys::Win32::Storage::FileSystem::GetVolumeInformationW;

const ADMINISTRATORS: &str = "S-1-5-32-544";
const PERSISTENT_ACLS: u32 = 8;

struct PinnedRecoveryParent {
    pins: Vec<File>,
    _witness: File,
}

fn invalid(message: &'static str) -> io::Error {
    io::Error::new(io::ErrorKind::PermissionDenied, message)
}

fn ntfs_with_acls(path: &Path) -> io::Result<()> {
    let (root, _) = windows::local_parts(path)?;
    let wide: Vec<u16> = root.as_os_str().encode_wide().chain(Some(0)).collect();
    let mut filesystem = [0u16; 32];
    let mut flags = 0u32;
    if unsafe {
        GetVolumeInformationW(
            wide.as_ptr(),
            null_mut(),
            0,
            null_mut(),
            null_mut(),
            &mut flags,
            filesystem.as_mut_ptr(),
            filesystem.len() as u32,
        )
    } == 0
    {
        return Err(io::Error::last_os_error());
    }
    let end = filesystem
        .iter()
        .position(|&unit| unit == 0)
        .unwrap_or(filesystem.len());
    if String::from_utf16_lossy(&filesystem[..end]) != "NTFS" || flags & PERSISTENT_ACLS == 0 {
        return Err(invalid("recovery requires local NTFS with persistent ACLs"));
    }
    Ok(())
}

fn root_acl_is_trusted(
    owner: &str,
    grants: Option<&[(String, u32)]>,
    user: &str,
    default_owner: &str,
) -> bool {
    !owner.is_empty()
        && !user.is_empty()
        && (owner == user || owner == default_owner || owner == ADMINISTRATORS)
        && managed_runtime_acl_policy::parent_grants_are_trusted(grants, user)
}

fn open_at(root: &Path) -> io::Result<PinnedRecoveryParent> {
    ntfs_with_acls(root)?;
    let identity = managed_runtime_windows_token::current_token_sids()?;
    let pins = recovery_python_tree::pin_root(root)?;
    for (index, directory) in pins.iter().enumerate().skip(1) {
        let acl = managed_runtime_acl_reader::read_handle_acl(directory.as_handle())?;
        let grants = Some(acl.allow_grants.as_slice());
        let trusted = if index + 1 == pins.len() {
            root_acl_is_trusted(
                &acl.owner,
                grants,
                identity.user_sid(),
                identity.default_owner_sid(),
            )
        } else {
            managed_runtime_acl_policy::ancestor_is_trusted(&acl.owner, grants, identity.user_sid())
        };
        if !trusted {
            return Err(invalid("recovery parent ancestry ACL is untrusted"));
        }
    }
    let root_pin = pins
        .last()
        .ok_or_else(|| invalid("recovery parent pin missing"))?;
    let witness_path = root.join(".venv");
    let witness = windows_file::open_directory(&witness_path)?;
    if !windows_file::is_held_direct_child(
        root_pin.as_handle(),
        witness.as_handle(),
        &witness_path,
    )? {
        return Err(invalid("legacy runtime witness is not a direct child"));
    }
    Ok(PinnedRecoveryParent {
        pins,
        _witness: witness,
    })
}

fn open() -> io::Result<PinnedRecoveryParent> {
    open_at(&managed_runtime_windows_token::current_profile_directory()?.join(".tensor-grep"))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs::{self, OpenOptions};
    use std::os::windows::fs::{MetadataExt, OpenOptionsExt};
    use std::os::windows::io::AsRawHandle;
    use std::os::windows::process::CommandExt;
    use windows_sys::Win32::Security::Authorization::{
        ConvertStringSecurityDescriptorToSecurityDescriptorW, SetSecurityInfo, SE_FILE_OBJECT,
    };
    use windows_sys::Win32::Security::{
        GetSecurityDescriptorDacl, DACL_SECURITY_INFORMATION, PROTECTED_DACL_SECURITY_INFORMATION,
    };
    use windows_sys::Win32::Storage::FileSystem::{
        FILE_ADD_SUBDIRECTORY, FILE_ATTRIBUTE_REPARSE_POINT,
    };

    fn fixture() -> (tempfile::TempDir, std::path::PathBuf) {
        let fixture = tempfile::tempdir_in(
            managed_runtime_windows_token::current_profile_directory().unwrap(),
        )
        .unwrap();
        let root = fixture.path().join("managed");
        fs::create_dir(&root).unwrap();
        fs::create_dir(root.join(".venv")).unwrap();
        fs::write(root.join("key"), b"preserve").unwrap();
        (fixture, root)
    }

    fn acl_writer(root: &Path) -> File {
        OpenOptions::new()
            .access_mode(0x60080)
            .share_mode(7)
            .custom_flags(0x02200000)
            .open(root)
            .unwrap()
    }

    fn grant_foreign_write(writer: &File) {
        let user = managed_runtime_windows_token::current_token_sids().unwrap();
        let sddl: Vec<u16> = format!("D:P(A;OICI;FA;;;{})(A;;0x4;;;WD)\0", user.user_sid())
            .encode_utf16()
            .collect();
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
        let _allocation = unsafe {
            super::super::managed_runtime_windows_sid::LocalAllocation::from_raw(descriptor.cast())
        }
        .unwrap();
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
        let acl = managed_runtime_acl_reader::read_handle_acl(writer.as_handle()).unwrap();
        assert!(acl
            .allow_grants
            .iter()
            .any(|(sid, mask)| sid == "S-1-1-0" && *mask & FILE_ADD_SUBDIRECTORY != 0));
    }

    #[test]
    fn pins_disposable_legacy_parent_and_witness_without_mutation() {
        let (fixture, root) = fixture();
        let pinned = open_at(&root).unwrap();
        assert!(!pinned.pins.is_empty());
        assert_eq!(
            fs::rename(&root, fixture.path().join("moved"))
                .unwrap_err()
                .raw_os_error(),
            Some(32)
        );
        assert_eq!(
            fs::rename(root.join(".venv"), root.join("other"))
                .unwrap_err()
                .raw_os_error(),
            Some(32)
        );
        assert_eq!(fs::read(root.join("key")).unwrap(), b"preserve");
        drop(pinned);
        fs::rename(root.join(".venv"), root.join("other")).unwrap();
        fs::rename(&root, fixture.path().join("moved")).unwrap();
        assert_eq!(
            fs::read(fixture.path().join("moved/key")).unwrap(),
            b"preserve"
        );
        eprintln!("read-only legacy parent and witness lifetime controls exercised");
    }

    #[test]
    fn refuses_missing_witness() {
        let (_fixture, root) = fixture();
        drop(open_at(&root).unwrap());
        fs::remove_dir(root.join(".venv")).unwrap();
        assert_eq!(
            open_at(&root).err().unwrap().kind(),
            io::ErrorKind::NotFound
        );
        assert!(!root.join(".venv").exists());
        assert_eq!(fs::read(root.join("key")).unwrap(), b"preserve");
    }

    #[test]
    fn refuses_parent_with_preexisting_delete_handle() {
        let (_fixture, root) = fixture();
        drop(open_at(&root).unwrap());
        // A production share3 pin would prevent setting up the preexisting DELETE handle.
        let identity = OpenOptions::new()
            .read(true)
            .share_mode(7)
            .custom_flags(0x02200000)
            .open(&root)
            .unwrap();
        let metadata = identity.metadata().unwrap();
        assert!(
            metadata.is_dir() && metadata.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT == 0
        );
        let writer = OpenOptions::new()
            .access_mode(0x10080)
            .share_mode(7)
            .custom_flags(0x02200000)
            .open(&root)
            .unwrap();
        assert!(
            windows_file::same_file_identity(identity.as_handle(), writer.as_handle()).unwrap()
        );
        assert_eq!(open_at(&root).err().unwrap().raw_os_error(), Some(32));
        drop(writer);
        drop(identity);
        drop(open_at(&root).unwrap());
    }

    #[test]
    fn refuses_foreign_write_acl_after_positive_control() {
        let (_fixture, root) = fixture();
        drop(open_at(&root).unwrap());
        let writer = acl_writer(&root);
        grant_foreign_write(&writer);
        let refusal = open_at(&root);
        assert!(
            refusal.is_err(),
            "legacy parent foreign write grant was accepted"
        );
        assert_eq!(
            refusal.err().unwrap().to_string(),
            "recovery parent ancestry ACL is untrusted"
        );
        assert_eq!(fs::read(root.join("key")).unwrap(), b"preserve");
    }

    #[test]
    fn preopened_write_dac_can_change_acl_while_parent_is_pinned() {
        let (_fixture, root) = fixture();
        let writer = acl_writer(&root);
        let pinned = open_at(&root).unwrap();
        assert!(windows_file::same_file_identity(
            writer.as_handle(),
            pinned.pins.last().unwrap().as_handle(),
        )
        .unwrap());
        grant_foreign_write(&writer);
        assert_eq!(
            open_at(&root).err().unwrap().to_string(),
            "recovery parent ancestry ACL is untrusted"
        );
        assert_eq!(fs::read(root.join("key")).unwrap(), b"preserve");
        drop(pinned);
    }

    #[test]
    fn refuses_junction_witness_after_positive_control() {
        let (fixture, root) = fixture();
        let outside = fixture.path().join("outside");
        fs::create_dir(&outside).unwrap();
        fs::write(outside.join("key"), b"preserve").unwrap();
        drop(open_at(&root).unwrap());
        fs::remove_dir(root.join(".venv")).unwrap();
        let cmd = std::path::PathBuf::from(std::env::var_os("SystemRoot").unwrap())
            .join("System32")
            .join("cmd.exe");
        let output = std::process::Command::new(cmd)
            .args(["/d", "/c", "mklink", "/J"])
            .arg(root.join(".venv"))
            .arg(&outside)
            .creation_flags(0x08000000)
            .output()
            .unwrap();
        assert!(
            output.status.success(),
            "junction fixture failed: {:?} {:?}",
            output.stdout,
            output.stderr
        );
        assert_eq!(
            open_at(&root).err().unwrap().to_string(),
            "opened path has an unexpected type or is a reparse point"
        );
        assert_eq!(fs::read(outside.join("key")).unwrap(), b"preserve");
        fs::remove_dir(root.join(".venv")).unwrap();
    }

    #[test]
    fn accepts_administrator_owner_only_with_safe_grants() {
        let user = "S-1-5-21-1";
        let default_owner = "S-1-5-21-2";
        let world = [("S-1-1-0".to_owned(), FILE_ADD_SUBDIRECTORY)];
        for owner in [user, default_owner, ADMINISTRATORS] {
            assert!(root_acl_is_trusted(owner, Some(&[]), user, default_owner));
            assert!(!root_acl_is_trusted(owner, None, user, default_owner));
            assert!(!root_acl_is_trusted(
                owner,
                Some(&world),
                user,
                default_owner
            ));
        }
        assert!(!root_acl_is_trusted(
            "S-1-5-21-3",
            Some(&[]),
            user,
            default_owner
        ));
        assert!(!root_acl_is_trusted(
            default_owner,
            Some(&[]),
            "",
            default_owner
        ));
    }
}

//! Bounded owner and allow-grant reads from a Windows file handle.
#![cfg(windows)]

use crate::managed_runtime_windows_sid::{sid_to_string, LocalAllocation};
use std::ffi::c_void;
use std::io;
use std::os::windows::io::{AsRawHandle, BorrowedHandle};
use windows_sys::Win32::Security::Authorization::{GetSecurityInfo, SE_FILE_OBJECT};
use windows_sys::Win32::Security::{
    AclSizeInformation, GetAce, GetAclInformation, GetSecurityDescriptorDacl,
    GetSecurityDescriptorLength, GetSecurityDescriptorOwner, IsValidAcl, IsValidSid,
    ACCESS_ALLOWED_ACE, ACE_HEADER, ACL_SIZE_INFORMATION, DACL_SECURITY_INFORMATION,
    OWNER_SECURITY_INFORMATION,
};

const MAX_DESCRIPTOR_BYTES: usize = 65_536;
const MAX_ACE_COUNT: u32 = 1024;
const ACCESS_ALLOWED_ACE_TYPE: u8 = 0;
const INHERIT_ONLY_ACE: u8 = 0x08;
const KNOWN_ALLOW_ACE_FLAGS: u8 = 0x1f;
const ACL_HEADER_BYTES: usize = 8;

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct HandleAcl {
    pub owner: String,
    pub allow_grants: Vec<(String, u32)>,
}

/// Read an opened file object's owner and effective allow grants; unsupported ACL semantics fail closed.
pub fn read_handle_acl(handle: BorrowedHandle<'_>) -> io::Result<HandleAcl> {
    let mut owner = std::ptr::null_mut();
    let mut dacl = std::ptr::null_mut();
    let mut descriptor = std::ptr::null_mut();
    let status = unsafe {
        GetSecurityInfo(
            handle.as_raw_handle(),
            SE_FILE_OBJECT,
            OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION,
            &mut owner,
            std::ptr::null_mut(),
            &mut dacl,
            std::ptr::null_mut(),
            &mut descriptor,
        )
    };
    if status != 0 {
        return Err(io::Error::from_raw_os_error(status as i32));
    }
    let _owner = unsafe { LocalAllocation::from_raw(descriptor.cast()) }
        .ok_or_else(|| invalid_acl("security descriptor is unavailable"))?;
    let length = unsafe { GetSecurityDescriptorLength(descriptor) } as usize;
    if !(20..=MAX_DESCRIPTOR_BYTES).contains(&length) {
        return Err(invalid_acl("security descriptor length is invalid"));
    }
    // SAFETY: GetSecurityInfo returned this LocalFree-owned buffer, and the validated length
    // bounds every byte view below. `_owner` keeps the allocation alive for this function.
    let descriptor_bytes = unsafe { std::slice::from_raw_parts(descriptor.cast::<u8>(), length) };
    let start = descriptor as usize;
    let mut queried_owner = std::ptr::null_mut();
    let mut owner_defaulted = 0;
    if unsafe { GetSecurityDescriptorOwner(descriptor, &mut queried_owner, &mut owner_defaulted) }
        == 0
        || queried_owner != owner
    {
        return Err(invalid_acl("security descriptor owner is unavailable"));
    }
    let owner_offset = (queried_owner as usize)
        .checked_sub(start)
        .ok_or_else(|| invalid_acl("security descriptor owner is outside its allocation"))?;
    let owner_bytes = descriptor_bytes
        .get(owner_offset..)
        .ok_or_else(|| invalid_acl("security descriptor owner is outside its allocation"))?;
    let owner_sid = sid_string(owner_bytes)?;

    let mut dacl_present = 0;
    let mut queried_dacl = std::ptr::null_mut();
    let mut dacl_defaulted = 0;
    if unsafe {
        GetSecurityDescriptorDacl(
            descriptor,
            &mut dacl_present,
            &mut queried_dacl,
            &mut dacl_defaulted,
        )
    } == 0
        || dacl_present == 0
        || queried_dacl.is_null()
        || queried_dacl != dacl
    {
        return Err(invalid_acl("DACL is unavailable or null"));
    }
    let acl_start = (queried_dacl as usize)
        .checked_sub(start)
        .ok_or_else(|| invalid_acl("DACL lies outside its security descriptor"))?;
    let acl_header_end = acl_start
        .checked_add(ACL_HEADER_BYTES)
        .filter(|value| *value <= descriptor_bytes.len())
        .ok_or_else(|| invalid_acl("DACL lies outside its security descriptor"))?;
    let acl_header = descriptor_bytes
        .get(acl_start..acl_header_end)
        .ok_or_else(|| invalid_acl("DACL lies outside its security descriptor"))?;
    let acl_size = u16::from_ne_bytes([acl_header[2], acl_header[3]]) as usize;
    if acl_size < ACL_HEADER_BYTES {
        return Err(invalid_acl("DACL is too short"));
    }
    let acl_end = acl_start
        .checked_add(acl_size)
        .filter(|value| *value <= descriptor_bytes.len())
        .ok_or_else(|| invalid_acl("DACL range is invalid"))?;
    if unsafe { IsValidAcl(queried_dacl) } == 0 {
        return Err(invalid_acl("DACL is malformed"));
    }
    let mut information = ACL_SIZE_INFORMATION::default();
    if unsafe {
        GetAclInformation(
            queried_dacl,
            (&mut information as *mut ACL_SIZE_INFORMATION).cast(),
            std::mem::size_of::<ACL_SIZE_INFORMATION>() as u32,
            AclSizeInformation,
        )
    } == 0
        || information.AceCount > MAX_ACE_COUNT
    {
        return Err(invalid_acl("DACL ACE count is unavailable or excessive"));
    }

    let mut grants = Vec::with_capacity(information.AceCount as usize);
    for index in 0..information.AceCount {
        let mut raw_ace = std::ptr::null_mut();
        if unsafe { GetAce(queried_dacl, index, &mut raw_ace) } == 0 || raw_ace.is_null() {
            return Err(invalid_acl("DACL ACE is unavailable"));
        }
        let ace_start = (raw_ace as usize)
            .checked_sub(start)
            .ok_or_else(|| invalid_acl("ACE lies outside its security descriptor"))?;
        let header_end = checked_ace_header_end(acl_start + ACL_HEADER_BYTES, acl_end, ace_start)?;
        let header = descriptor_bytes
            .get(ace_start..header_end)
            .ok_or_else(|| invalid_acl("ACE header lies outside its DACL"))?;
        let ace_size = u16::from_ne_bytes([header[2], header[3]]) as usize;
        let ace_end = ace_start
            .checked_add(ace_size)
            .filter(|value| *value <= acl_end)
            .ok_or_else(|| invalid_acl("ACE range is invalid"))?;
        if ace_end < header_end {
            return Err(invalid_acl("ACE range is invalid"));
        }
        let ace_bytes = descriptor_bytes
            .get(ace_start..ace_end)
            .ok_or_else(|| invalid_acl("ACE range is invalid"))?;
        if let Some((sid_offset, mask)) = parse_allow_ace(ace_bytes)? {
            let sid = ace_bytes
                .get(sid_offset..)
                .ok_or_else(|| invalid_acl("allow ACE SID lies outside its ACE"))?;
            grants.push((sid_string(sid)?, mask));
        }
    }
    Ok(HandleAcl {
        owner: owner_sid,
        allow_grants: grants,
    })
}

fn checked_ace_header_end(
    acl_body_start: usize,
    acl_end: usize,
    ace_start: usize,
) -> io::Result<usize> {
    if ace_start < acl_body_start {
        return Err(invalid_acl("ACE header starts before the DACL body"));
    }
    ace_start
        .checked_add(std::mem::size_of::<ACE_HEADER>())
        .filter(|value| *value <= acl_end)
        .ok_or_else(|| invalid_acl("ACE header lies outside its DACL"))
}

fn parse_allow_ace(ace: &[u8]) -> io::Result<Option<(usize, u32)>> {
    if ace.len() < std::mem::size_of::<ACE_HEADER>() {
        return Err(invalid_acl("ACE header is truncated"));
    }
    let ace_size = u16::from_ne_bytes([ace[2], ace[3]]) as usize;
    if ace_size != ace.len()
        || ace[0] != ACCESS_ALLOWED_ACE_TYPE
        || ace[1] & !KNOWN_ALLOW_ACE_FLAGS != 0
    {
        return Err(invalid_acl("DACL contains unsupported ACE semantics"));
    }
    let sid_offset = std::mem::offset_of!(ACCESS_ALLOWED_ACE, SidStart);
    let sid = ace
        .get(sid_offset..)
        .filter(|bytes| bytes.len() >= 8)
        .ok_or_else(|| invalid_acl("allow ACE is too short to contain a SID"))?;
    let sid_length = 8 + (sid[1] as usize) * 4;
    if sid[0] != 1 || sid[1] > 15 || sid_length != sid.len() {
        return Err(invalid_acl("allow ACE SID is malformed or exceeds its ACE"));
    }
    if ace[1] & INHERIT_ONLY_ACE != 0 {
        return Ok(None);
    }
    let mask = u32::from_ne_bytes(ace[4..8].try_into().expect("ACE header has mask bytes"));
    Ok(Some((sid_offset, mask)))
}

fn sid_string(sid: &[u8]) -> io::Result<String> {
    let sid_header = sid
        .get(..8)
        .ok_or_else(|| invalid_acl("SID header is truncated"))?;
    let sid_length = 8usize
        .checked_add(
            (sid_header[1] as usize)
                .checked_mul(4)
                .ok_or_else(|| invalid_acl("SID length overflowed"))?,
        )
        .filter(|_| sid_header[0] == 1 && sid_header[1] <= 15)
        .ok_or_else(|| invalid_acl("SID header is malformed"))?;
    let sid = sid
        .get(..sid_length)
        .ok_or_else(|| invalid_acl("SID lies outside its security descriptor"))?;
    let sid_pointer = sid.as_ptr() as *mut c_void;
    if unsafe { IsValidSid(sid_pointer) } == 0 {
        return Err(invalid_acl("SID is malformed"));
    }
    if unsafe { windows_sys::Win32::Security::GetLengthSid(sid_pointer) } as usize != sid_length {
        return Err(invalid_acl("SID length is inconsistent"));
    }
    unsafe { sid_to_string(sid_pointer, "SID could not be converted") }
}

fn invalid_acl(message: &'static str) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, message)
}

#[cfg(test)]
mod tests {
    use super::{checked_ace_header_end, parse_allow_ace};
    use std::io;

    #[test]
    fn inherit_only_allow_ace_rejects_sid_extent_beyond_ace() {
        let mut ace = vec![0; 16];
        ace[0] = 0;
        ace[1] = 0x08;
        ace[2..4].copy_from_slice(&16u16.to_le_bytes());
        ace[8] = 1;
        ace[9] = 1; // SID claims one 4-byte subauthority; only 8 SID bytes remain.

        let error = parse_allow_ace(&ace).unwrap_err();

        assert_eq!(error.kind(), io::ErrorKind::InvalidData);
    }

    #[test]
    fn ace_header_range_rejects_pointer_before_dacl_body() {
        let error = checked_ace_header_end(0x1008, 0x1100, 0x1004).unwrap_err();

        assert_eq!(error.kind(), io::ErrorKind::InvalidData);
    }
}

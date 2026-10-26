#![cfg(windows)]

use std::ffi::c_void;
use std::io;
use windows_sys::Win32::Foundation::{LocalFree, HLOCAL};
use windows_sys::Win32::Security::Authorization::ConvertSidToStringSidW;

const MAX_SID_STRING_UNITS: usize = 184;

/// Converts a caller-validated SID while preserving the returned Windows allocation's owner.
///
/// # Safety
/// The caller must prove `sid` points to a live, valid SID for the duration of this call, and
/// must validate the complete SID byte span before calling this function.
pub(crate) unsafe fn sid_to_string(
    sid: *mut c_void,
    invalid_message: &'static str,
) -> io::Result<String> {
    let mut wide = std::ptr::null_mut();
    if unsafe { ConvertSidToStringSidW(sid, &mut wide) } == 0 {
        return Err(io::Error::last_os_error());
    }
    let _allocation = unsafe { LocalAllocation::from_raw(wide.cast()) }
        .ok_or_else(|| invalid_data(invalid_message))?;
    let length = (0..=MAX_SID_STRING_UNITS)
        .find(|index| unsafe { wide.add(*index).read() == 0 })
        .ok_or_else(|| invalid_data(invalid_message))?;
    String::from_utf16(unsafe { std::slice::from_raw_parts(wide, length) })
        .map_err(|_| invalid_data(invalid_message))
}

pub(crate) struct LocalAllocation(HLOCAL);

impl LocalAllocation {
    /// Takes unique ownership of a LocalFree-compatible allocation; NULL returns None.
    ///
    /// # Safety
    /// If non-null, `raw` must be an allocation returned by a Windows API documented to use
    /// `LocalFree`, and no other owner may free it.
    pub(crate) unsafe fn from_raw(raw: HLOCAL) -> Option<Self> {
        if raw.is_null() {
            None
        } else {
            Some(Self(raw))
        }
    }
}

impl Drop for LocalAllocation {
    fn drop(&mut self) {
        unsafe {
            let _ = LocalFree(self.0);
        }
    }
}

fn invalid_data(message: &'static str) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, message)
}

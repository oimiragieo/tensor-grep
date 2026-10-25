#![cfg(windows)]

use crate::managed_runtime_windows_sid::sid_to_string as shared_sid_to_string;
use std::ffi::c_void;
use std::fmt;
use std::io;
use std::mem::size_of;
use std::os::windows::io::{AsRawHandle, FromRawHandle, OwnedHandle};

use windows_sys::Win32::Foundation::HANDLE;
#[cfg(test)]
use windows_sys::Win32::Foundation::{GetLastError, SetLastError};
use windows_sys::Win32::Security::{
    GetLengthSid, GetTokenInformation, IsValidSid, TokenOwner, TokenUser, TOKEN_OWNER, TOKEN_QUERY,
    TOKEN_USER,
};
use windows_sys::Win32::System::Threading::{GetCurrentProcess, OpenProcessToken};
const MAX_TOKEN_INFO_BYTES: usize = 4096;
const ERROR_INSUFFICIENT_BUFFER: i32 = 122;
const SID_HEADER_BYTES: usize = 8;
const SID_MAX_SUB_AUTHORITIES: usize = 15;

pub struct VerifiedTokenIdentity {
    user_sid: String,
    default_owner_sid: String,
}

impl fmt::Debug for VerifiedTokenIdentity {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("VerifiedTokenIdentity { .. }")
    }
}

impl VerifiedTokenIdentity {
    pub fn user_sid(&self) -> &str {
        &self.user_sid
    }

    pub fn default_owner_sid(&self) -> &str {
        &self.default_owner_sid
    }
}

pub fn current_token_sids() -> io::Result<VerifiedTokenIdentity> {
    let token = open_current_process_token()?;
    let (user_buffer, user_length) =
        token_information(token.as_raw_handle(), TokenUser, size_of::<TOKEN_USER>())?;
    // The token buffer is usize-aligned and token_information checked the returned header size.
    let user_record = unsafe { user_buffer.as_ptr().cast::<TOKEN_USER>().read() };
    let user_sid = sid_string_from_token_buffer(&user_buffer, user_length, user_record.User.Sid)?;

    let (owner_buffer, owner_length) =
        token_information(token.as_raw_handle(), TokenOwner, size_of::<TOKEN_OWNER>())?;
    let owner_record = unsafe { owner_buffer.as_ptr().cast::<TOKEN_OWNER>().read() };
    let default_owner_sid =
        sid_string_from_token_buffer(&owner_buffer, owner_length, owner_record.Owner)?;

    Ok(VerifiedTokenIdentity {
        user_sid,
        default_owner_sid,
    })
}

fn open_current_process_token() -> io::Result<OwnedHandle> {
    // GetCurrentProcess returns a borrowed pseudo-handle; only the opened token is owned.
    let process = unsafe { GetCurrentProcess() };
    let mut raw_token: HANDLE = std::ptr::null_mut();
    if call_open_process_token(process, TOKEN_QUERY, &mut raw_token) == 0 {
        return Err(io::Error::last_os_error());
    }
    if raw_token.is_null() {
        return Err(invalid_token_data());
    }
    // Transfer the successful OpenProcessToken handle exactly once into its RAII owner.
    Ok(unsafe { OwnedHandle::from_raw_handle(raw_token) })
}

fn call_open_process_token(process: HANDLE, access: u32, token: *mut HANDLE) -> i32 {
    #[cfg(test)]
    if fault_is(FaultPoint::OpenProcessTokenAccessDenied) {
        unsafe { SetLastError(5) };
        return 0;
    }
    unsafe { OpenProcessToken(process, access, token) }
}

fn call_get_token_information(
    token: std::os::windows::io::RawHandle,
    information_class: i32,
    buffer: *mut c_void,
    buffer_length: u32,
    returned_length: *mut u32,
) -> i32 {
    #[cfg(test)]
    if information_class == TokenUser {
        if buffer.is_null() {
            if fault_is(FaultPoint::TokenUserProbeAccessDenied) {
                unsafe { SetLastError(5) };
                return 0;
            }
        } else if fault_is(FaultPoint::TokenUserFillAccessDenied) {
            unsafe { SetLastError(5) };
            return 0;
        }
    }

    let succeeded = unsafe {
        GetTokenInformation(
            token,
            information_class,
            buffer,
            buffer_length,
            returned_length,
        )
    };
    #[cfg(test)]
    let native_last_error = unsafe { GetLastError() };

    #[cfg(test)]
    if information_class == TokenUser && !returned_length.is_null() {
        if buffer.is_null() && succeeded == 0 {
            match test_fault() {
                Some(FaultPoint::TokenUserProbeOversized) => unsafe { *returned_length = 4097 },
                Some(FaultPoint::TokenUserProbeZero) => unsafe { *returned_length = 0 },
                Some(FaultPoint::TokenUserProbePadded) => unsafe {
                    *returned_length = (*returned_length).saturating_add(8)
                },
                _ => {}
            }
        } else if !buffer.is_null() && succeeded != 0 {
            match test_fault() {
                Some(FaultPoint::TokenUserFillReportedShort) => unsafe {
                    *returned_length = (size_of::<TOKEN_USER>() - 1) as u32
                },
                Some(FaultPoint::TokenUserFillReportedTypedHeader) => unsafe {
                    *returned_length = size_of::<TOKEN_USER>() as u32
                },
                Some(FaultPoint::TokenUserFillReportedOneByteShort) => unsafe {
                    *returned_length = (*returned_length).saturating_sub(1)
                },
                _ => {}
            }
        }
    }
    #[cfg(test)]
    unsafe {
        SetLastError(native_last_error);
    }
    succeeded
}

fn token_information(
    token: std::os::windows::io::RawHandle,
    information_class: i32,
    minimum_size: usize,
) -> io::Result<(Vec<usize>, usize)> {
    let mut needed = 0u32;
    let probe_succeeded = call_get_token_information(
        token,
        information_class,
        std::ptr::null_mut(),
        0,
        &mut needed,
    );
    let probe_error = io::Error::last_os_error();
    if probe_succeeded == 0 && probe_error.raw_os_error() != Some(ERROR_INSUFFICIENT_BUFFER) {
        return Err(probe_error);
    }
    if probe_succeeded != 0 || needed == 0 || (needed as usize) < minimum_size {
        return Err(invalid_token_data());
    }
    let requested = needed as usize;
    if requested > MAX_TOKEN_INFO_BYTES {
        return Err(invalid_token_data());
    }

    let word_size = size_of::<usize>();
    let word_count = requested
        .checked_add(word_size - 1)
        .ok_or_else(invalid_token_data)?
        / word_size;
    // usize storage provides native alignment; the SDK still receives only `requested` bytes.
    let mut buffer = vec![0usize; word_count];
    let mut returned = 0u32;
    if call_get_token_information(
        token,
        information_class,
        buffer.as_mut_ptr().cast::<c_void>(),
        needed,
        &mut returned,
    ) == 0
    {
        return Err(io::Error::last_os_error());
    }
    if (returned as usize) < minimum_size || (returned as usize) > requested {
        return Err(invalid_token_data());
    }
    Ok((buffer, returned as usize))
}

fn sid_string_from_token_buffer(
    buffer: &[usize],
    returned_length: usize,
    sid: *mut c_void,
) -> io::Result<String> {
    let declared_sid = checked_sid_span(buffer, returned_length, sid)?;
    #[cfg(test)]
    SID_API_CALLS.with(|calls| calls.set(calls.get() + 1));
    if unsafe { IsValidSid(sid) } == 0 {
        return Err(invalid_token_data());
    }
    #[cfg(test)]
    SID_API_CALLS.with(|calls| calls.set(calls.get() + 1));
    if unsafe { GetLengthSid(sid) } as usize != declared_sid.len() {
        return Err(invalid_token_data());
    }
    sid_to_string(sid)
}

fn checked_sid_span(
    buffer: &[usize],
    returned_length: usize,
    sid: *mut c_void,
) -> io::Result<&[u8]> {
    let buffer_bytes = buffer
        .len()
        .checked_mul(size_of::<usize>())
        .ok_or_else(invalid_token_data)?;
    if sid.is_null() || returned_length > buffer_bytes {
        return Err(invalid_token_data());
    }
    let start = buffer.as_ptr() as usize;
    let end = start
        .checked_add(returned_length)
        .ok_or_else(invalid_token_data)?;
    let sid_start = sid as usize;
    let header_end = sid_start
        .checked_add(SID_HEADER_BYTES)
        .ok_or_else(invalid_token_data)?;
    if sid_start < start || header_end > end {
        return Err(invalid_token_data());
    }
    // Validate the pointer's complete fixed header range before creating a borrowed slice.
    let header = unsafe { std::slice::from_raw_parts(sid.cast::<u8>(), SID_HEADER_BYTES) };
    if header[0] != 1 || header[1] as usize > SID_MAX_SUB_AUTHORITIES {
        return Err(invalid_token_data());
    }
    let sid_length = SID_HEADER_BYTES
        .checked_add(
            (header[1] as usize)
                .checked_mul(size_of::<u32>())
                .ok_or_else(invalid_token_data)?,
        )
        .ok_or_else(invalid_token_data)?;
    let sid_end = sid_start
        .checked_add(sid_length)
        .ok_or_else(invalid_token_data)?;
    if sid_end > end {
        return Err(invalid_token_data());
    }
    Ok(unsafe { std::slice::from_raw_parts(sid.cast::<u8>(), sid_length) })
}

fn sid_to_string(sid: *mut c_void) -> io::Result<String> {
    #[cfg(test)]
    SID_API_CALLS.with(|calls| calls.set(calls.get() + 1));
    // The caller checked the live SID span and both SDK validity/length predicates.
    unsafe { shared_sid_to_string(sid, "process token data is invalid") }
}

fn invalid_token_data() -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, "process token data is invalid")
}

#[cfg(test)]
fn fault_is(point: FaultPoint) -> bool {
    TEST_FAULT.with(|fault| fault.get() == Some(point))
}

#[cfg(test)]
fn test_fault() -> Option<FaultPoint> {
    TEST_FAULT.with(std::cell::Cell::get)
}

#[cfg(test)]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum FaultPoint {
    OpenProcessTokenAccessDenied,
    /// Forward the real zero-capacity probe but report exactly 4097 required bytes.
    TokenUserProbeOversized,
    TokenUserProbeZero,
    TokenUserProbeAccessDenied,
    TokenUserProbePadded,
    TokenUserFillAccessDenied,
    TokenUserFillReportedShort,
    TokenUserFillReportedTypedHeader,
    TokenUserFillReportedOneByteShort,
}

#[cfg(test)]
thread_local! {
    static TEST_FAULT: std::cell::Cell<Option<FaultPoint>> = const { std::cell::Cell::new(None) };
    static SID_API_CALLS: std::cell::Cell<usize> = const { std::cell::Cell::new(0) };
}

#[cfg(test)]
struct FaultGuard {
    previous_fault: Option<FaultPoint>,
    previous_sid_calls: usize,
}

#[cfg(test)]
impl FaultGuard {
    fn activate(fault: FaultPoint) -> Self {
        let previous_fault = TEST_FAULT.with(|value| value.replace(Some(fault)));
        let previous_sid_calls = SID_API_CALLS.with(|value| value.replace(0));
        Self {
            previous_fault,
            previous_sid_calls,
        }
    }

    fn sid_api_calls() -> usize {
        SID_API_CALLS.with(std::cell::Cell::get)
    }
}

#[cfg(test)]
impl Drop for FaultGuard {
    fn drop(&mut self) {
        TEST_FAULT.with(|value| value.set(self.previous_fault));
        SID_API_CALLS.with(|value| value.set(self.previous_sid_calls));
    }
}

#[cfg(all(test, windows))]
mod tests {
    use super::{current_token_sids, FaultGuard, FaultPoint};
    use std::io::ErrorKind;

    #[test]
    fn current_token_sids_returns_nonempty_canonical_user_and_default_owner() {
        let identity = current_token_sids().unwrap();

        assert!(identity.user_sid().starts_with("S-1-"));
        assert!(identity.default_owner_sid().starts_with("S-1-"));
        assert!(!identity.user_sid().is_empty());
        assert!(!identity.default_owner_sid().is_empty());
    }

    #[test]
    fn current_token_sids_debug_output_redacts_both_sid_values() {
        let identity = current_token_sids().unwrap();

        let debug = format!("{identity:?}");

        assert!(debug == "VerifiedTokenIdentity { .. }");
    }

    #[test]
    fn current_token_sids_rejects_oversized_token_user_probe() {
        let _fault = FaultGuard::activate(FaultPoint::TokenUserProbeOversized);

        let result = current_token_sids();

        assert_eq!(result.unwrap_err().kind(), ErrorKind::InvalidData);
    }

    #[test]
    fn current_token_sids_propagates_token_user_probe_access_denied() {
        let _fault = FaultGuard::activate(FaultPoint::TokenUserProbeAccessDenied);

        let result = current_token_sids();

        assert_eq!(result.unwrap_err().raw_os_error(), Some(5));
    }

    #[test]
    fn current_token_sids_accepts_forwarded_probe_with_extra_capacity() {
        let _fault = FaultGuard::activate(FaultPoint::TokenUserProbePadded);

        let result = current_token_sids();

        assert!(result.is_ok());
    }

    #[test]
    fn current_token_sids_rejects_fill_reported_as_typed_header_before_sid_calls() {
        let _fault = FaultGuard::activate(FaultPoint::TokenUserFillReportedTypedHeader);

        let result = current_token_sids();

        assert_eq!(result.unwrap_err().kind(), ErrorKind::InvalidData);
        assert_eq!(FaultGuard::sid_api_calls(), 0);
    }

    #[test]
    fn current_token_sids_rejects_fill_reported_one_byte_short_before_sid_calls() {
        let _fault = FaultGuard::activate(FaultPoint::TokenUserFillReportedOneByteShort);

        let result = current_token_sids();

        assert_eq!(result.unwrap_err().kind(), ErrorKind::InvalidData);
        assert_eq!(FaultGuard::sid_api_calls(), 0);
    }

    #[test]
    fn current_token_sids_rejects_zero_token_user_probe() {
        let _fault = FaultGuard::activate(FaultPoint::TokenUserProbeZero);

        let result = current_token_sids();

        assert_eq!(result.unwrap_err().kind(), ErrorKind::InvalidData);
    }

    #[test]
    fn current_token_sids_reports_token_user_fill_access_denied() {
        let _fault = FaultGuard::activate(FaultPoint::TokenUserFillAccessDenied);

        let result = current_token_sids();

        assert_eq!(result.unwrap_err().raw_os_error(), Some(5));
    }

    #[test]
    fn current_token_sids_fault_guard_restores_real_token_calls() {
        {
            let _fault = FaultGuard::activate(FaultPoint::OpenProcessTokenAccessDenied);

            let result = current_token_sids();

            assert_eq!(result.unwrap_err().raw_os_error(), Some(5));
        }
        let identity = current_token_sids().unwrap();

        assert!(identity.user_sid().starts_with("S-1-"));
        assert!(identity.default_owner_sid().starts_with("S-1-"));
    }

    #[test]
    fn current_token_sids_rejects_short_forwarded_fill_before_sid_validation() {
        let _fault = FaultGuard::activate(FaultPoint::TokenUserFillReportedShort);

        let result = current_token_sids();

        assert_eq!(result.unwrap_err().kind(), ErrorKind::InvalidData);
        assert_eq!(FaultGuard::sid_api_calls(), 0);
    }
}

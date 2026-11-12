//! Checkpoint admission and retention budgets shared with the Python limits.

use std::path::Path;

pub(super) const MAX_RECORDS_ENV: &str = "TG_CHECKPOINT_MAX";
pub(super) const MAX_FILE_BYTES_ENV: &str = "TG_CHECKPOINT_MAX_FILE_BYTES";
pub(super) const MAX_TOTAL_BYTES_ENV: &str = "TG_CHECKPOINT_MAX_TOTAL_BYTES";
pub(super) const FREE_MARGIN_ENV: &str = "TG_CHECKPOINT_FREE_SPACE_MARGIN_BYTES";

const DEFAULT_MAX_RECORDS: u64 = 64;
const DEFAULT_MAX_FILE_BYTES: u64 = 512 * 1024 * 1024;
const DEFAULT_MAX_TOTAL_BYTES: u64 = 4 * 1024 * 1024 * 1024;
const DEFAULT_FREE_MARGIN_BYTES: u64 = 256 * 1024 * 1024;

#[derive(Debug, Clone, Copy)]
pub(super) struct CheckpointLimits {
    pub(super) max_records: usize,
    pub(super) max_file_bytes: u64,
    pub(super) max_total_bytes: u64,
    pub(super) free_margin_bytes: u64,
}

/// Read a positive integer env var; a missing, malformed or non-positive value uses `default`.
///
/// Deliberately NOT the fail-closed `parse_limit` above: `core/env_flags.configured_positive_int`
/// -- the one definition the Python twins share -- falls back to the default rather than
/// erroring, so a typo cannot make creating a checkpoint impossible.
fn positive_or_default(name: &str, default: u64) -> u64 {
    std::env::var(name)
        .ok()
        .and_then(|raw| raw.trim().parse::<u64>().ok())
        .filter(|value| *value > 0)
        .unwrap_or(default)
}

pub(super) fn checkpoint_limits() -> CheckpointLimits {
    CheckpointLimits {
        max_records: positive_or_default(MAX_RECORDS_ENV, DEFAULT_MAX_RECORDS) as usize,
        max_file_bytes: positive_or_default(MAX_FILE_BYTES_ENV, DEFAULT_MAX_FILE_BYTES),
        max_total_bytes: positive_or_default(MAX_TOTAL_BYTES_ENV, DEFAULT_MAX_TOTAL_BYTES),
        free_margin_bytes: positive_or_default(FREE_MARGIN_ENV, DEFAULT_FREE_MARGIN_BYTES),
    }
}

/// Free bytes available to this user at `path`, or `None` when the platform cannot say.
///
/// `None` must NOT block a checkpoint: Python's `shutil.disk_usage` failure path returns without
/// refusing (`checkpoint_retention._check_checkpoint_disk_budget`), and the per-file/total caps
/// still apply. Hence the two tiny FFI shims rather than a new crate.
pub(super) fn available_bytes(path: &Path) -> Option<u64> {
    #[cfg(unix)]
    {
        use std::os::unix::ffi::OsStrExt;
        let c_path = std::ffi::CString::new(path.as_os_str().as_bytes()).ok()?;
        // SAFETY: `statvfs` only writes into the struct we pass and reads the valid C string.
        let mut stats: libc::statvfs = unsafe { std::mem::zeroed() };
        if unsafe { libc::statvfs(c_path.as_ptr(), &mut stats) } != 0 {
            return None;
        }
        let block_size = if stats.f_frsize != 0 {
            stats.f_frsize
        } else {
            stats.f_bsize
        };
        Some((stats.f_bavail as u64).saturating_mul(block_size as u64))
    }

    #[cfg(windows)]
    {
        use std::os::windows::ffi::OsStrExt;
        #[link(name = "kernel32")]
        extern "system" {
            fn GetDiskFreeSpaceExW(
                directory_name: *const u16,
                free_bytes_available_to_caller: *mut u64,
                total_number_of_bytes: *mut u64,
                total_number_of_free_bytes: *mut u64,
            ) -> i32;
        }
        let wide: Vec<u16> = path
            .as_os_str()
            .encode_wide()
            .chain(std::iter::once(0))
            .collect();
        let mut free_to_caller: u64 = 0;
        // SAFETY: `wide` is NUL-terminated and outlives the call; the two size out-params are
        // documented as optional, so null is allowed for them.
        let ok = unsafe {
            GetDiskFreeSpaceExW(
                wide.as_ptr(),
                &mut free_to_caller,
                std::ptr::null_mut(),
                std::ptr::null_mut(),
            )
        };
        (ok != 0).then_some(free_to_caller)
    }

    #[cfg(not(any(unix, windows)))]
    {
        let _ = path;
        None
    }
}

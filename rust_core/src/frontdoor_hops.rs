//! Cross-door delegation depth (mirrors `src/tensor_grep/cli/frontdoor_hops.py`).
//!
//! The native door re-execs `python -m tensor_grep` for flags it routes to Python, and the Python
//! door delegates some searches back to the native binary. Neither routing table proves the other
//! side will not bounce the request straight back (2026-10-05: `-s`/`-N` + `--json` ping-ponged
//! 842 processes in ~60 s). `TG_REEXEC_GUARD` is a boolean the NATIVE door never reads, so it
//! cannot stop a second native hop from re-exec'ing Python again; this counter is the generic
//! backstop for every cycle nobody has found yet.
//!
//! Contract: `TG_FRONTDOOR_HOPS` is the number of cross-door hops already taken (absent means 0).
//! A door spawning the OTHER door passes `hops + 1`. At `hops >= MAX_FRONTDOOR_HOPS`, or on a
//! malformed value, the door refuses with exit 2 and a message naming the variable, BEFORE it
//! spawns anything.

use std::env;
use std::ffi::OsString;
use std::process::Command;

use crate::python_sidecar::SidecarError;

pub const TG_FRONTDOOR_HOPS_ENV: &str = "TG_FRONTDOOR_HOPS";
/// tg -> py -> tg -> py is the deepest legitimate chain; 4 keeps one hop of headroom and bounds
/// any runaway to a handful of processes.
pub const MAX_FRONTDOOR_HOPS: u32 = 4;

fn refusal(detail: String) -> SidecarError {
    SidecarError {
        exit_code: 2,
        message: format!(
            "tensor-grep: refusing to delegate between the native and Python front doors: \
             {detail}. This is a routing loop; report the exact command line. Output was not \
             produced."
        ),
        stderr: String::new(),
    }
}

/// The hop count to stamp on the child, or a fail-closed exit-2 refusal.
pub fn next_frontdoor_hops(raw: Option<OsString>) -> Result<u32, SidecarError> {
    let current = match raw {
        None => 0,
        Some(value) => {
            let text = value.to_string_lossy();
            let trimmed = text.trim();
            if trimmed.is_empty() {
                0
            } else {
                trimmed.parse::<u32>().map_err(|_| {
                    refusal(format!(
                        "{TG_FRONTDOOR_HOPS_ENV}={text:?} is not a non-negative integer"
                    ))
                })?
            }
        }
    };
    if current >= MAX_FRONTDOOR_HOPS {
        return Err(refusal(format!(
            "{TG_FRONTDOOR_HOPS_ENV}={current} reached the limit of {MAX_FRONTDOOR_HOPS}"
        )));
    }
    Ok(current + 1)
}

/// Stamp the next hop on a Python child, or refuse. Call this FIRST: a refusal must spawn nothing.
pub fn stamp_python_child(command: &mut Command) -> Result<(), SidecarError> {
    let hops = next_frontdoor_hops(env::var_os(TG_FRONTDOOR_HOPS_ENV))?;
    command.env(TG_FRONTDOOR_HOPS_ENV, hops.to_string());
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn hops(raw: &str) -> Result<u32, SidecarError> {
        next_frontdoor_hops(Some(OsString::from(raw)))
    }

    #[test]
    fn absent_and_blank_count_from_zero() {
        assert_eq!(next_frontdoor_hops(None).unwrap(), 1);
        assert_eq!(hops("").unwrap(), 1);
        assert_eq!(hops(" 2 ").unwrap(), 3);
    }

    #[test]
    fn last_hop_below_the_cap_is_allowed_and_the_cap_refuses() {
        assert_eq!(hops("3").unwrap(), MAX_FRONTDOOR_HOPS);
        let err = hops("4").unwrap_err();
        assert_eq!(err.exit_code, 2);
        assert!(err.message.contains(TG_FRONTDOOR_HOPS_ENV));
    }

    #[test]
    fn malformed_values_fail_closed() {
        for bad in ["abc", "-1", "1.5", "0x1"] {
            let err = hops(bad).unwrap_err();
            assert_eq!(err.exit_code, 2, "{bad}");
            assert!(err.message.contains(TG_FRONTDOOR_HOPS_ENV), "{bad}");
        }
    }
}

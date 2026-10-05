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
//! malformed value, the door refuses with exit 2 and an ASCII-only message naming the variable,
//! BEFORE it spawns anything.
//!
//! Grammar (identical on both doors): 1-9 ASCII digits, nothing else. No whitespace, sign,
//! underscore or Unicode digit; the empty string is malformed (only "absent" means 0).

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

fn parse_hops(text: &str) -> Option<u32> {
    let bytes = text.as_bytes();
    if bytes.is_empty() || bytes.len() > 9 || !bytes.iter().all(|b| b.is_ascii_digit()) {
        return None;
    }
    text.parse::<u32>().ok()
}

/// The hop count to stamp on the child, or a fail-closed exit-2 refusal.
pub fn next_frontdoor_hops(raw: Option<OsString>) -> Result<u32, SidecarError> {
    let current = match raw {
        None => 0,
        Some(value) => {
            let text = value.to_string_lossy();
            match parse_hops(&text) {
                Some(parsed) => parsed,
                None => {
                    let head: String = text.chars().take(64).collect();
                    let shown = head.escape_default().to_string();
                    return Err(refusal(format!(
                        "{TG_FRONTDOOR_HOPS_ENV}=\"{shown}\" is not 1-9 ASCII digits"
                    )));
                }
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
    fn absent_counts_from_zero_and_ascii_digits_are_allowed() {
        assert_eq!(next_frontdoor_hops(None).unwrap(), 1);
        assert_eq!(hops("0").unwrap(), 1);
        assert_eq!(hops("1").unwrap(), 2);
        assert_eq!(hops("3").unwrap(), MAX_FRONTDOOR_HOPS);
    }

    #[test]
    fn the_cap_refuses() {
        let err = hops("4").unwrap_err();
        assert_eq!(err.exit_code, 2);
        assert!(err.message.contains(TG_FRONTDOOR_HOPS_ENV));
    }

    #[test]
    fn malformed_values_fail_closed_with_an_ascii_message() {
        let long = "1".repeat(5000);
        for bad in [
            "abc",
            "-1",
            "+1",
            "1.5",
            "0x1",
            "0_0",
            " 1",
            "1 ",
            "",
            "\u{660}",
            "\u{663}",
            "abc\u{2603}",
            long.as_str(),
        ] {
            let err = hops(bad).unwrap_err();
            assert_eq!(err.exit_code, 2, "{bad:?}");
            assert!(err.message.contains(TG_FRONTDOOR_HOPS_ENV), "{bad:?}");
            assert!(err.message.is_ascii(), "{bad:?}");
        }
    }
}

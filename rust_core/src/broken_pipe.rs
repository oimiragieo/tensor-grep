//! Broken-pipe detection for the native search front door.
//!
//! Split out of `main.rs` (2026-09-12) under the file-size ratchet, whose own message is
//! "An allowlisted file may shrink, never grow. Reduce it, or split it." `main.rs` needed
//! headroom for a new `tg freshness` clap variant and dispatch arm.
//!
//! Chosen by asking for a top-level `fn` with ZERO other main.rs-local dependencies: one call
//! site (`run_native_search_with_optional_rg_fallback`), no `.tg-registration.toml` pin, and
//! referenced elsewhere only in DOC COMMENTS (`native_search.rs`, `routing.rs`) that describe
//! the guard rather than calling it -- so those stay accurate.

use std::io;

/// Does this error chain represent a downstream reader closing the pipe?
///
/// Returns `true` only for a genuine broken pipe, because the caller treats `true` as "exit
/// quietly" -- a false positive here is a SILENT SWALLOW of a real search failure.
pub fn error_chain_has_broken_pipe(err: &anyhow::Error) -> bool {
    let mut saw_io_error = false;
    for cause in err.chain() {
        if let Some(io_err) = cause.downcast_ref::<io::Error>() {
            saw_io_error = true;
            if io_err.kind() == io::ErrorKind::BrokenPipe {
                return true;
            }
        }
    }
    if saw_io_error {
        // A typed `io::Error` was present and said something OTHER than BrokenPipe. Trust it: this
        // is a real failure and must reach the structured-error and rg-fallback paths below.
        //
        // Guarding the string match on this is what stops a SILENT SWALLOW: the anyhow context
        // embeds the searched path (`native standard output search failed for <path>`), so without
        // it a file whose path merely contains "broken pipe" would turn any genuine error into a
        // quiet exit(1) "no matches" -- skipping the JSON error and the rg fallback both. Narrow,
        // but silent-swallow is the class this repo fails closed on.
        return false;
    }
    // Fallback for the case where no typed `io::Error` survives the chain at all. Kept because an
    // earlier revision relied on the typed walk alone and CI proved it did not fire on Linux;
    // `io::Error(BrokenPipe)` renders as "Broken pipe (os error 32)" on Unix and "The pipe is
    // being closed. (os error 232)" on Windows.
    let rendered = format!("{err:#}").to_ascii_lowercase();
    rendered.contains("broken pipe") || rendered.contains("pipe is being closed")
}

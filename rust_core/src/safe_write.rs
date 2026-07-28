//! One symlink-refusing file write, shared by the binary and the lib.
//!
//! This guard used to live only in `main.rs` (the `tg` binary crate), so `backend_ast`'s
//! `direct_write_file` -- which is in the LIB crate -- could not reach it and used a bare
//! `std::fs::write`. `tg run --rewrite --apply` therefore wrote THROUGH a git-tracked symlink to
//! its target: an ordinary mode-120000 blob pointing at `~/.ssh/config`, a sibling project's
//! `.env`, or any absolute path redirected the rewrite out of the repo. That is the write-side
//! twin of the read-side disclosure fixed in #847.
//!
//! Moved here rather than copied: two implementations of a security guard drift, and the repo's
//! duplication lens calls that out explicitly. `main.rs` now delegates to this one.

use std::io::Write;
use std::path::Path;

use anyhow::Context;

/// Writes `bytes` to `path`, refusing to follow a symlink/reparse point at the final path
/// component (audit #110 Gap 1). Closes a cross-process TOCTOU: the Python front door
/// resolves and confines the `--audit-manifest` target before invoking this native binary,
/// but a symlink swapped into that path between the Python check and this write was
/// previously followed by a plain `std::fs::write` -- a confined write could escape its
/// anchor. Mirrors the confine-then-open discipline `_write_json_refuse_symlink` already
/// uses on the Python side (`src/tensor_grep/cli/main.py`).
///
/// audit #115: also guards `create_checkpoint`'s metadata.json + the shared/predictable
/// index.json write, and `restore_validation_rollback_snapshots`'s per-file restore write --
/// the same TOCTOU class, just with the checkpoint/rollback root standing in for
/// `--audit-manifest`'s confined target.
pub fn write_bytes_refuse_symlink(path: &Path, bytes: &[u8]) -> anyhow::Result<()> {
    use std::fs::OpenOptions;

    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        // O_NOFOLLOW makes the open() itself fail (ELOOP) if the final path component is
        // a symlink -- atomic, no separate check->open window.
        let mut file = OpenOptions::new()
            .write(true)
            .create(true)
            .truncate(true)
            .custom_flags(libc::O_NOFOLLOW)
            .open(path)
            .with_context(|| format!("refusing to write through symlink at {}", path.display()))?;
        file.write_all(bytes)?;
        Ok(())
    }

    #[cfg(windows)]
    {
        use std::os::windows::fs::{MetadataExt, OpenOptionsExt};
        // Not in a dependency here (neither `windows` nor `winapi` is in Cargo.toml) -- these
        // are the real documented values (winnt.h / fileapi.h), kept as local consts rather
        // than pulling in a crate for two flag bits.
        const FILE_FLAG_OPEN_REPARSE_POINT: u32 = 0x0020_0000;
        const FILE_ATTRIBUTE_REPARSE_POINT: u32 = 0x0000_0400;

        // FILE_FLAG_OPEN_REPARSE_POINT makes CreateFile open the reparse point entry
        // itself instead of traversing it -- so if `path` is a symlink, we open the link,
        // not its target. Deliberately no truncate-at-open: on a real reparse point that
        // would touch the reparse buffer before we get to check it below.
        let mut file = OpenOptions::new()
            .write(true)
            .create(true)
            .custom_flags(FILE_FLAG_OPEN_REPARSE_POINT)
            .open(path)
            .with_context(|| format!("failed to open {}", path.display()))?;
        let attributes = file.metadata()?.file_attributes();
        if attributes & FILE_ATTRIBUTE_REPARSE_POINT != 0 {
            anyhow::bail!(
                "refusing to write through symlink/reparse point at {}",
                path.display()
            );
        }
        // Confirmed a regular file (or a freshly created one) -- now safe to truncate and
        // write, preserving create-or-overwrite semantics for a legitimate rerun.
        file.set_len(0)?;
        file.write_all(bytes)?;
        Ok(())
    }

    #[cfg(not(any(unix, windows)))]
    {
        if std::fs::symlink_metadata(path)
            .map(|metadata| metadata.file_type().is_symlink())
            .unwrap_or(false)
        {
            anyhow::bail!("refusing to write through symlink at {}", path.display());
        }
        std::fs::write(path, bytes)?;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::write_bytes_refuse_symlink;

    /// Best-effort symlink creation. Unprivileged Windows cannot make one; skip rather than
    /// silently pass, mirroring the Python fixture in
    /// `tests/unit/test_scanner_skips_symlinked_files.py`.
    fn try_symlink(target: &std::path::Path, link: &std::path::Path) -> bool {
        #[cfg(unix)]
        {
            std::os::unix::fs::symlink(target, link).is_ok()
        }
        #[cfg(windows)]
        {
            std::os::windows::fs::symlink_file(target, link).is_ok()
        }
        #[cfg(not(any(unix, windows)))]
        {
            let _ = (target, link);
            false
        }
    }

    #[test]
    fn refuses_to_write_through_a_symlink_and_leaves_the_target_untouched() {
        let dir = tempfile::tempdir().unwrap();
        let outside = dir.path().join("secret.txt");
        std::fs::write(&outside, b"SECRET_MARKER_998877\n").unwrap();
        let link = dir.path().join("link.txt");
        if !try_symlink(&outside, &link) {
            eprintln!("skipping: cannot create a symlink on this host");
            return;
        }

        let result = write_bytes_refuse_symlink(&link, b"ATTACKER\n");

        assert!(result.is_err(), "writing through a symlink must be refused");
        // The point of the guard: the TARGET must be byte-identical afterwards. Asserting only
        // on the Err would pass for an implementation that wrote the bytes and then errored.
        assert_eq!(
            std::fs::read(&outside).unwrap(),
            b"SECRET_MARKER_998877\n",
            "the symlink target was modified despite the refusal"
        );
    }

    #[test]
    fn still_writes_an_ordinary_file() {
        // CONTROL ARM: without it, a guard that refused EVERY write would satisfy the test above.
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("plain.txt");
        write_bytes_refuse_symlink(&path, b"first\n").unwrap();
        assert_eq!(std::fs::read(&path).unwrap(), b"first\n");

        // Overwrite must truncate, not leave a tail of the longer previous contents.
        write_bytes_refuse_symlink(&path, b"second_shorter\n").unwrap();
        assert_eq!(std::fs::read(&path).unwrap(), b"second_shorter\n");
    }
}

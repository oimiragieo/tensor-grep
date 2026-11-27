use crate::search_file_open::{build_cpu_regex, collect_search_targets};
use memchr::{memchr, memchr_iter, memmem};
use memmap2::{MmapMut, MmapOptions};
use rayon::prelude::*;
use regex::bytes::RegexBuilder;
use std::fs::{File, OpenOptions};
use std::path::{Path, PathBuf};
use walkdir::WalkDir;

/// Large-file intra-file parallel search threshold (bytes). Matches
/// `native_search.rs::LARGE_FILE_CHUNK_THRESHOLD_BYTES` (rust_core/src/native_search.rs:28) so
/// "large file" means the same thing across tg's native CPU search paths. Below this size a
/// file is scanned by a single thread exactly as before (see `scan_lines_memmem`/
/// `scan_lines_regex` below, which is the same code either way); at or above it, the file is
/// split into line-aligned chunks and each chunk is scanned in parallel with rayon -- the same
/// crate this module already uses for `count_file_memmem`/`count_file_regex`'s `par_split`, and
/// the same idiom `native_search.rs::search_file_chunk_parallel` already ships for
/// `--json`/`--count`/`--quiet` output.
///
/// Deliberately NOT applied to the default plain-text / `--ndjson` streaming output modes in
/// native_search.rs: those have an explicit, tested "first match must stream well before the
/// full scan completes" contract
/// (rust_core/tests/test_native_search.rs::test_native_search_default_output_streams_before_search_completion
/// and `..._ndjson_output_streams_before_search_completion`, asserting >=25ms between first byte
/// and process exit) that a genuine multi-core speedup would put at real risk of shrinking below
/// -- reconciling true parallelism with that streaming/incremental-output guarantee is a
/// separate design problem, not addressed here. `search_with_paths`'s contract has no such
/// constraint: it collects every match into a `Vec` and returns it once, so there is nothing a
/// parallel scan could disturb except which thread does the comparing.
const LARGE_FILE_PARALLEL_THRESHOLD_BYTES: usize = 50 * 1024 * 1024;

/// One line-aligned byte range of a large file, tagged with the 1-based line number of its
/// first line. Every boundary is snapped forward to the byte just after a `\n` (or EOF), so a
/// chunk can never start or end mid-line: a match can never be split, duplicated, or dropped at
/// a seam. `first_line` lets each chunk be searched independently, on any thread, in any order,
/// while still producing globally-correct, ascending line numbers once every chunk's results
/// are stitched back together in original (byte-ascending) order.
#[derive(Debug, Clone, Copy)]
struct LineAlignedChunk {
    start: usize,
    end: usize,
    first_line: usize,
}

/// Split `contents` into up to `requested_chunks` contiguous, line-aligned byte ranges. Returns
/// an empty `Vec` when there is nothing worth splitting (empty content or `requested_chunks <=
/// 1`) -- callers must fall back to a single whole-buffer scan in that case.
///
/// Mirrors `native_search.rs::plan_file_chunks`/`align_chunk_end_to_newline`
/// (rust_core/src/native_search.rs:1613-1669), independently re-implemented here because
/// backend_cpu.rs's match/line model (`CpuMatch`) and matcher types (`memmem` / `regex::bytes`)
/// are local to this module, not shared with the `grep-searcher`-based native front door.
fn plan_line_aligned_chunks(contents: &[u8], requested_chunks: usize) -> Vec<LineAlignedChunk> {
    if contents.is_empty() || requested_chunks <= 1 {
        return Vec::new();
    }

    let target_chunk_size = contents.len().div_ceil(requested_chunks);
    let mut chunks = Vec::new();
    let mut start = 0usize;
    let mut first_line = 1usize;

    while start < contents.len() {
        let tentative_end = start.saturating_add(target_chunk_size).min(contents.len());
        let end = align_chunk_end_to_newline(contents, tentative_end);
        if end <= start {
            break;
        }
        chunks.push(LineAlignedChunk {
            start,
            end,
            first_line,
        });
        first_line += count_newlines(&contents[start..end]);
        start = end;
    }

    chunks
}

/// Snap `tentative_end` forward to the first byte after the next `\n`, so a chunk boundary can
/// never land inside a line. If `tentative_end` already sits exactly at such a boundary (the
/// preceding byte is `\n`), it is returned unchanged; if no further `\n` exists, the chunk runs
/// to EOF.
fn align_chunk_end_to_newline(contents: &[u8], tentative_end: usize) -> usize {
    if tentative_end == 0 || tentative_end >= contents.len() {
        return contents.len();
    }
    if contents[tentative_end - 1] == b'\n' {
        return tentative_end;
    }
    match memchr(b'\n', &contents[tentative_end..]) {
        Some(relative_offset) => tentative_end + relative_offset + 1,
        None => contents.len(),
    }
}

fn count_newlines(contents: &[u8]) -> usize {
    memchr_iter(b'\n', contents).count()
}

/// Requested chunk count for intra-file parallel search: one chunk per available core. There
/// is no override knob (backend_cpu.rs has no config struct, only plain method arguments), so
/// this always reflects the live core count -- mirrors `native_search.rs`'s
/// `configured_chunk_parallelism_threads` fallback when no explicit override is configured.
fn requested_parallel_chunk_count() -> usize {
    std::thread::available_parallelism()
        .map(|count| count.get())
        .unwrap_or(1)
}

/// Scan `contents` line-by-line for a fixed-string `pattern` (via `memmem`), producing
/// `CpuMatch` entries numbered from `first_line_number`. This is the ONE place that decides
/// whether a line matches: both the whole-file single-pass scan (`first_line_number == 1`, the
/// pre-existing behavior for files under the parallel threshold) and every parallel chunk of a
/// large file (`first_line_number` = that chunk's first line, from `plan_line_aligned_chunks`)
/// call this same function, so the parallel path can never diverge from serial -- it is the
/// identical per-line predicate, just fed a different byte range and line offset. Matching is
/// already strictly per-line (a match never looks past a `\n`), so a line-aligned split of the
/// input can never change WHICH lines match -- it only changes how many threads do the
/// checking.
fn scan_lines_memmem(
    contents: &[u8],
    pattern: &[u8],
    invert_match: bool,
    first_line_number: usize,
    path: &Path,
) -> Vec<CpuMatch> {
    let mut results = Vec::new();
    let mut line_num = first_line_number;
    let mut start = 0;

    for i in memchr_iter(b'\n', contents) {
        let line_bytes = &contents[start..i];
        let is_match = memmem::find(line_bytes, pattern).is_some();
        let should_include = if invert_match { !is_match } else { is_match };
        if should_include {
            results.push(CpuMatch {
                file: path.to_path_buf(),
                line: line_num,
                text: String::from_utf8_lossy(line_bytes).into_owned(),
            });
        }
        start = i + 1;
        line_num += 1;
    }

    if start < contents.len() {
        let line_bytes = &contents[start..];
        let is_match = memmem::find(line_bytes, pattern).is_some();
        let should_include = if invert_match { !is_match } else { is_match };
        if should_include {
            results.push(CpuMatch {
                file: path.to_path_buf(),
                line: line_num,
                text: String::from_utf8_lossy(line_bytes).into_owned(),
            });
        }
    }

    results
}

/// Regex sibling of `scan_lines_memmem` -- identical per-line, first-line-number-offset
/// contract, so the same "chunking cannot change which lines match" argument applies.
fn scan_lines_regex(
    contents: &[u8],
    re: &regex::bytes::Regex,
    invert_match: bool,
    first_line_number: usize,
    path: &Path,
) -> Vec<CpuMatch> {
    let mut results = Vec::new();
    let mut line_num = first_line_number;
    let mut start = 0;

    for i in memchr_iter(b'\n', contents) {
        let line_bytes = &contents[start..i];
        let is_match = re.is_match(line_bytes);
        let should_include = if invert_match { !is_match } else { is_match };
        if should_include {
            results.push(CpuMatch {
                file: path.to_path_buf(),
                line: line_num,
                text: String::from_utf8_lossy(line_bytes).into_owned(),
            });
        }
        start = i + 1;
        line_num += 1;
    }

    if start < contents.len() {
        let line_bytes = &contents[start..];
        let is_match = re.is_match(line_bytes);
        let should_include = if invert_match { !is_match } else { is_match };
        if should_include {
            results.push(CpuMatch {
                file: path.to_path_buf(),
                line: line_num,
                text: String::from_utf8_lossy(line_bytes).into_owned(),
            });
        }
    }

    results
}

/// Search `contents` for a fixed-string `pattern`, transparently using the large-file
/// line-aligned parallel-chunk path when `contents` is at/above
/// `LARGE_FILE_PARALLEL_THRESHOLD_BYTES` and more than one chunk results; otherwise (small
/// file, single-core box, or a file with too few line breaks to usefully split) falls back to
/// exactly the same single-pass `scan_lines_memmem` call used today. Chunk results are
/// collected via `Vec<LineAlignedChunk>::par_iter().map(..).collect::<Vec<_>>()`: rayon
/// guarantees a `collect()` over an `IndexedParallelIterator` (which a `Vec`'s `par_iter()`
/// always is) reassembles results in the same order as the source, i.e. ascending
/// `chunk.start` / ascending line order -- so flattening the per-chunk `Vec<CpuMatch>` results
/// in that order reproduces exactly the serial scan's match order, regardless of which thread
/// finished first.
fn search_contents_memmem_maybe_parallel(
    contents: &[u8],
    pattern: &[u8],
    invert_match: bool,
    path: &Path,
) -> Vec<CpuMatch> {
    if contents.len() >= LARGE_FILE_PARALLEL_THRESHOLD_BYTES {
        let chunks = plan_line_aligned_chunks(contents, requested_parallel_chunk_count());
        if chunks.len() > 1 {
            let chunk_results: Vec<Vec<CpuMatch>> = chunks
                .par_iter()
                .map(|chunk| {
                    scan_lines_memmem(
                        &contents[chunk.start..chunk.end],
                        pattern,
                        invert_match,
                        chunk.first_line,
                        path,
                    )
                })
                .collect();
            return chunk_results.into_iter().flatten().collect();
        }
    }

    scan_lines_memmem(contents, pattern, invert_match, 1, path)
}

/// Regex sibling of `search_contents_memmem_maybe_parallel` -- identical dispatch/ordering
/// contract.
fn search_contents_regex_maybe_parallel(
    contents: &[u8],
    re: &regex::bytes::Regex,
    invert_match: bool,
    path: &Path,
) -> Vec<CpuMatch> {
    if contents.len() >= LARGE_FILE_PARALLEL_THRESHOLD_BYTES {
        let chunks = plan_line_aligned_chunks(contents, requested_parallel_chunk_count());
        if chunks.len() > 1 {
            let chunk_results: Vec<Vec<CpuMatch>> = chunks
                .par_iter()
                .map(|chunk| {
                    scan_lines_regex(
                        &contents[chunk.start..chunk.end],
                        re,
                        invert_match,
                        chunk.first_line,
                        path,
                    )
                })
                .collect();
            return chunk_results.into_iter().flatten().collect();
        }
    }

    scan_lines_regex(contents, re, invert_match, 1, path)
}

/// Test-only fault-injection knobs for `replace_in_place`'s directory-mode fault tests (Task 5).
/// Gated on `#[cfg(test)]` so this struct, its field, and every check against it compile away
/// entirely from the normal `rlib` build that `rust_core/tests/*.rs` integration tests and every
/// downstream consumer link against -- an external test cannot reach this seam even by name,
/// only the `#[cfg(test)] mod tests` unit tests inside this file can, because they are compiled
/// as part of the SAME crate build that has `--cfg test` enabled.
#[cfg(test)]
#[derive(Default)]
struct ReplaceFaultInjection {
    /// Force the directory-walk collection step to fail before any child file is touched.
    force_walk_failure: bool,
    /// Force the next literal-mode child-file replace/write to fail.
    force_literal_child_failure: bool,
    /// Force the next regex-mode child-file replace/write to fail.
    force_regex_child_failure: bool,
    /// Force the symlink_metadata guard's stat call to fail, without needing a real
    /// unreadable path. Proves the guard fails CLOSED rather than falling through.
    force_symlink_metadata_failure: bool,

    /// Event gate for the residual-TOCTOU characterization pin. The writer signals on
    /// `reached_guard` once it is PAST the no-follow guard and BEFORE the writer opens the
    /// path, then blocks on `resume` until the second actor acknowledges its swap. Both are
    /// capacity-1 channels: a `send` never blocks, a `recv` is always `recv_timeout`-bounded,
    /// so this is a handshake, not a sleep and not a hang vector.
    swap_gate: Option<SwapGate>,
}

#[cfg(test)]
struct SwapGate {
    reached_guard: std::sync::mpsc::SyncSender<()>,
    resume: std::sync::mpsc::Receiver<()>,
}

#[cfg(test)]
const SWAP_GATE_TIMEOUT: std::time::Duration = std::time::Duration::from_secs(2);

/// Replace the leaf `path` with a symlink pointing at `attacker_target`, exactly as an
/// attacker who wins the TOCTOU race would. Returns false when this environment cannot
/// create a symlink at all -- CANNOT_MEASURE, not a failed swap (A61).
#[cfg(test)]
fn swap_leaf_to_symlink_for_test(path: &Path, attacker_target: &Path) -> bool {
    if let Err(err) = std::fs::remove_file(path) {
        if std::env::var_os("TG_REQUIRE_SYMLINK_TESTS").is_some() {
            panic!("TG_REQUIRE_SYMLINK_TESTS set: cannot unlink the leaf: {err}");
        }
        eprintln!("skipping the residual-TOCTOU pin: cannot unlink the leaf: {err}");
        return false;
    }
    #[cfg(unix)]
    {
        match std::os::unix::fs::symlink(attacker_target, path) {
            Ok(()) => true,
            Err(err) => {
                if std::env::var_os("TG_REQUIRE_SYMLINK_TESTS").is_some() {
                    panic!("TG_REQUIRE_SYMLINK_TESTS set: symlink failed: {err}");
                }
                eprintln!("skipping the residual-TOCTOU pin: symlink failed: {err}");
                false
            }
        }
    }
    #[cfg(windows)]
    {
        match std::os::windows::fs::symlink_file(attacker_target, path) {
            Ok(()) => true,
            Err(err) => {
                if std::env::var_os("TG_REQUIRE_SYMLINK_TESTS").is_some() {
                    panic!("TG_REQUIRE_SYMLINK_TESTS set: cannot create a Windows symlink: {err}");
                }
                eprintln!(
                    "skipping the residual-TOCTOU pin: cannot create a Windows symlink \
                     in this environment: {err}"
                );
                false
            }
        }
    }
}

pub struct CpuBackend {
    #[cfg(test)]
    replace_fault_injection: std::sync::Mutex<ReplaceFaultInjection>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CpuMatch {
    pub file: PathBuf,
    pub line: usize,
    pub text: String,
}

struct ReplacementOp {
    start: usize,
    end: usize,
    bytes: Vec<u8>,
}

impl Default for CpuBackend {
    fn default() -> Self {
        Self::new()
    }
}

impl CpuBackend {
    pub fn new() -> Self {
        Self {
            #[cfg(test)]
            replace_fault_injection: std::sync::Mutex::new(ReplaceFaultInjection::default()),
        }
    }

    pub fn search_with_paths(
        &self,
        pattern: &str,
        path: &str,
        ignore_case: bool,
        fixed_strings: bool,
        invert_match: bool,
    ) -> anyhow::Result<Vec<CpuMatch>> {
        self.search_policy(
            pattern,
            path,
            ignore_case,
            fixed_strings,
            invert_match,
            true,
        )
    }

    pub fn search_nofollow(
        &self,
        pattern: &str,
        path: &str,
        ignore_case: bool,
        fixed_strings: bool,
        invert_match: bool,
    ) -> anyhow::Result<Vec<(usize, String)>> {
        Ok(self
            .search_policy(
                pattern,
                path,
                ignore_case,
                fixed_strings,
                invert_match,
                false,
            )?
            .into_iter()
            .map(|r| (r.line, r.text))
            .collect())
    }

    fn search_policy(
        &self,
        pattern: &str,
        path: &str,
        ignore_case: bool,
        fixed_strings: bool,
        invert_match: bool,
        follow_explicit_leaf_link: bool,
    ) -> anyhow::Result<Vec<CpuMatch>> {
        let mut results = Vec::new();
        let target_path = Path::new(path);
        if fixed_strings && !ignore_case {
            let pat_bytes = pattern.as_bytes();
            for target in collect_search_targets(target_path, follow_explicit_leaf_link)? {
                results.extend(self.search_file_memmem(pat_bytes, &target, invert_match)?);
            }
            return Ok(results);
        }
        let re = build_cpu_regex(pattern, ignore_case, fixed_strings)?;
        for target in collect_search_targets(target_path, follow_explicit_leaf_link)? {
            results.extend(self.search_file_regex(&re, &target, invert_match)?);
        }
        Ok(results)
    }

    pub fn search(
        &self,
        pattern: &str,
        path: &str,
        ignore_case: bool,
        fixed_strings: bool,
        invert_match: bool,
    ) -> anyhow::Result<Vec<(usize, String)>> {
        Ok(self
            .search_with_paths(pattern, path, ignore_case, fixed_strings, invert_match)?
            .into_iter()
            .map(|result| (result.line, result.text))
            .collect())
    }

    fn search_file_memmem(
        &self,
        pattern: &[u8],
        path: &Path,
        invert_match: bool,
    ) -> anyhow::Result<Vec<CpuMatch>> {
        let file = crate::search_file_open::open_native_search_file(path)?;
        let mmap = unsafe { MmapOptions::new().map(&file)? };

        Ok(search_contents_memmem_maybe_parallel(
            &mmap[..],
            pattern,
            invert_match,
            path,
        ))
    }

    fn search_file_regex(
        &self,
        re: &regex::bytes::Regex,
        path: &Path,
        invert_match: bool,
    ) -> anyhow::Result<Vec<CpuMatch>> {
        let file = crate::search_file_open::open_native_search_file(path)?;
        let mmap = unsafe { MmapOptions::new().map(&file)? };

        Ok(search_contents_regex_maybe_parallel(
            &mmap[..],
            re,
            invert_match,
            path,
        ))
    }

    pub fn replace_in_place(
        &self,
        pattern: &str,
        replacement: &str,
        path: &str,
        ignore_case: bool,
        fixed_strings: bool,
    ) -> anyhow::Result<()> {
        let path_obj = Path::new(path);

        // RUST-REPLACE-SYMLINK: refuse a symlinked target rather than writing THROUGH it.
        // Path::is_file() and OpenOptions::open both FOLLOW symlinks, so without this an
        // attacker-planted link redirects an in-place rewrite to a destination the caller
        // never named (GNU sed CVE-2026-5958, uutils GHSA-239g-2685-54x3, Capgo
        // CVE-2026-56236). symlink_metadata does NOT follow.
        //
        // RESIDUAL TOCTOU, deliberately not claimed as closed: the path can be swapped
        // between this stat and the open below. Closing it needs O_NOFOLLOW (POSIX) plus
        // FILE_FLAG_OPEN_REPARSE_POINT (Windows) at the open site -- machinery already
        // implemented in rust_core/src/safe_write.rs (see its OpenOptions setup). This guard
        // turns a
        // reliable static LEAF-symlink overwrite into a race the attacker must win; it does
        // NOT cover a symlink in a NON-LEAF path component (symlink_metadata lstats the leaf
        // only -- an attacker-controlled ancestor directory link still redirects statically,
        // with no race; component-wise openat(O_NOFOLLOW) / handle-relative opens are filed
        // under RUST-REPLACE-TOCTOU). See
        // docs/design/2026-08-13-replace-in-place-symlink-threat-model.md.
        //
        // Directory mode's STATIC symlink exposure is unaffected: walk_directory_entries uses
        // WalkDir's default follow_links(false) (the call is at the WalkDir::new site inside
        // walk_directory_entries) and both directory routes skip non-is_file() entries. Two
        // residual windows remain, both owned by RUST-REPLACE-TOCTOU and deliberately
        // unpinned here: (1) children are re-opened by pathname after enumeration, so a child
        // swapped for a symlink between enumeration and open is still followed; (2) the
        // DIRECTORY ROOT itself is swapped between this guard's stat and the is_dir() below,
        // WalkDir's follow_root_links(true) default (walkdir 2.5.0) descends into the
        // attacker's directory and rewrites its whole tree -- the widest blast radius in this
        // surface. The swap-gate pin below characterizes the FILE-LEAF window only; the
        // root window is named here rather than silently implied. Closing the leaf race at
        // the open sites would use the O_NOFOLLOW / FILE_FLAG_OPEN_REPARSE_POINT machinery
        // already implemented in rust_core/src/safe_write.rs -- deliberately not applied in
        // this PR because it touches both open sites AND every directory-child open: applying it
        // there would convert a raced child follow into an Err rather than a silent write -- safe
        // for legitimate use (WalkDir's follow_links(false) already filters static symlink
        // children) but a semantic change to the directory route that belongs in the TOCTOU row's
        // design pass, not this guard's.
        //
        // FAILS CLOSED. `if let Ok(meta) = ..` would fail OPEN: on any stat error -- a
        // permission denial, a reparse point whose filter driver refuses the query, an EIO --
        // the guard would silently do nothing and the follow behaviour would return, with
        // nothing observable separating "checked and safe" from "could not check".
        //
        // The test fault is injected as an Err INTO the same map_err path below (never a
        // bail! before the stat), so the fail-closed test observes the real wiring: a guard
        // rewritten fail-open (`.ok()` + if-let) reds that test.
        //
        // The stat uses a trailing-separator-stripped path: on POSIX, lstat("<dirlink>/")
        // resolves THROUGH the final symlink, so a raw caller string with a trailing slash
        // would pass an is_symlink() check on the unstripped path and hand the link root to
        // WalkDir . components() normalization strips trailing
        // separators without resolving anything; an empty result falls back to the raw path.
        let guard_path = {
            let stripped: std::path::PathBuf = path_obj.components().collect();
            if stripped.as_os_str().is_empty() {
                path_obj.to_path_buf()
            } else {
                stripped
            }
        };
        let meta = {
            #[cfg(test)]
            let stat_result = if self
                .replace_fault_injection
                .lock()
                .unwrap()
                .force_symlink_metadata_failure
            {
                Err(std::io::Error::other("injected test fault"))
            } else {
                std::fs::symlink_metadata(&guard_path)
            };
            #[cfg(not(test))]
            let stat_result = std::fs::symlink_metadata(&guard_path);
            stat_result.map_err(|err| {
                anyhow::anyhow!(
                    "replace_in_place: cannot determine whether {} is a symlink: {}",
                    path_obj.display(),
                    err
                )
            })?
        };
        if meta.file_type().is_symlink() {
            anyhow::bail!(
                "replace_in_place: refusing to follow symlink {}; pass the resolved target explicitly",
                path_obj.display()
            );
        }
        // A38/A48: the leaf check above and a swap-resistant WRITER are separate contracts.
        // This PR ships the leaf check only. The gate below is the seam that lets a test stand
        // INSIDE the residual window -- past the guard, before the open -- so the window is an
        // asserted property (the characterization pin, Step 4b) rather than a doc sentence.
        //
        // The gate is TAKEN out from under the lock and the lock DROPPED before either wait:
        // holding the injection mutex across the blocking `recv_timeout` would deadlock the
        // second actor out of the same struct. take() also makes it fire exactly once.
        #[cfg(test)]
        {
            let gate = self
                .replace_fault_injection
                .lock()
                .unwrap()
                .swap_gate
                .take();
            if let Some(gate) = gate {
                // Non-blocking: the channel has capacity 1 and this is its only message.
                gate.reached_guard.send(()).expect(
                    "CANNOT_MEASURE: swap-gate signal channel closed before the writer arrived",
                );
                gate.resume.recv_timeout(SWAP_GATE_TIMEOUT).expect(
                    "CANNOT_MEASURE: no swap acknowledgment within 2s (timed out, or the writer \
                     side of the handshake disconnected)",
                );
            }
        }

        if fixed_strings && !ignore_case && !pattern.is_empty() {
            if path_obj.is_file() {
                self.replace_file_literal(pattern.as_bytes(), replacement.as_bytes(), path_obj)?;
            } else if path_obj.is_dir() {
                self.replace_directory_literal(
                    path_obj,
                    pattern.as_bytes(),
                    replacement.as_bytes(),
                )?;
            }

            return Ok(());
        }

        let re = if fixed_strings {
            RegexBuilder::new(&regex::escape(pattern))
                .case_insensitive(ignore_case)
                .build()?
        } else {
            RegexBuilder::new(pattern)
                .case_insensitive(ignore_case)
                .build()?
        };

        if path_obj.is_file() {
            self.replace_file_regex(&re, replacement, path_obj)?;
        } else if path_obj.is_dir() {
            self.replace_directory_regex(path_obj, &re, replacement)?;
        }

        Ok(())
    }

    /// Collect the directory's file entries up front (never process while walking) so a
    /// walk-level failure is caught distinctly from a per-child replace failure, and so that,
    /// once collected, the exact set of files processed cannot change under us mid-walk. This is
    /// the single directory-mode entry point both `replace_directory_literal` and
    /// `replace_directory_regex` delegate through -- the same core the walk-failure fault test
    /// exercises via the public `replace_in_place`.
    fn walk_directory_entries(&self, path_obj: &Path) -> anyhow::Result<Vec<walkdir::DirEntry>> {
        #[cfg(test)]
        if self
            .replace_fault_injection
            .lock()
            .unwrap()
            .force_walk_failure
        {
            anyhow::bail!(
                "replace_in_place: directory walk failed for {}: injected test fault",
                path_obj.display()
            );
        }

        WalkDir::new(path_obj)
            .into_iter()
            .collect::<Result<Vec<_>, _>>()
            .map_err(|err| {
                anyhow::anyhow!(
                    "replace_in_place: directory walk failed for {}: {}",
                    path_obj.display(),
                    err
                )
            })
    }

    fn replace_directory_literal(
        &self,
        path_obj: &Path,
        pattern: &[u8],
        replacement: &[u8],
    ) -> anyhow::Result<()> {
        let entries = self.walk_directory_entries(path_obj)?;

        for entry in entries {
            if !entry.file_type().is_file() {
                continue;
            }
            let child_path = entry.path().to_path_buf();
            self.replace_file_literal(pattern, replacement, &child_path)
                .map_err(|err| {
                    anyhow::anyhow!(
                        "replace_in_place: literal replace failed for {}: {}",
                        child_path.display(),
                        err
                    )
                })?;
        }

        Ok(())
    }

    fn replace_directory_regex(
        &self,
        path_obj: &Path,
        re: &regex::bytes::Regex,
        replacement: &str,
    ) -> anyhow::Result<()> {
        let entries = self.walk_directory_entries(path_obj)?;

        for entry in entries {
            if !entry.file_type().is_file() {
                continue;
            }
            let child_path = entry.path().to_path_buf();
            self.replace_file_regex(re, replacement, &child_path)
                .map_err(|err| {
                    anyhow::anyhow!(
                        "replace_in_place: regex replace failed for {}: {}",
                        child_path.display(),
                        err
                    )
                })?;
        }

        Ok(())
    }

    fn replace_file_literal(
        &self,
        pattern: &[u8],
        replacement: &[u8],
        path: &Path,
    ) -> anyhow::Result<()> {
        #[cfg(test)]
        if self
            .replace_fault_injection
            .lock()
            .unwrap()
            .force_literal_child_failure
        {
            anyhow::bail!(
                "literal replace failed for {}: injected test fault",
                path.display()
            );
        }

        let file = OpenOptions::new().read(true).write(true).open(path)?;

        let source = unsafe { MmapOptions::new().map(&file)? };
        let original_len = source.len();
        if original_len == 0 {
            return Ok(());
        }

        let match_starts: Vec<usize> = memmem::find_iter(&source[..], pattern).collect();
        if match_starts.is_empty() {
            return Ok(());
        }

        if replacement.len() == pattern.len() {
            drop(source);
            let mut mmap = unsafe { MmapOptions::new().map_mut(&file)? };
            for start in match_starts {
                let end = start + replacement.len();
                mmap[start..end].copy_from_slice(replacement);
            }
            mmap.flush()?;
            return Ok(());
        }

        let replacements: Vec<ReplacementOp> = match_starts
            .into_iter()
            .map(|start| ReplacementOp {
                start,
                end: start + pattern.len(),
                bytes: replacement.to_vec(),
            })
            .collect();

        drop(source);

        self.write_replacements_with_mmap(&file, original_len, &replacements)
    }

    fn replace_file_regex(
        &self,
        re: &regex::bytes::Regex,
        replacement: &str,
        path: &Path,
    ) -> anyhow::Result<()> {
        #[cfg(test)]
        if self
            .replace_fault_injection
            .lock()
            .unwrap()
            .force_regex_child_failure
        {
            anyhow::bail!(
                "regex replace failed for {}: injected test fault",
                path.display()
            );
        }

        let file = OpenOptions::new().read(true).write(true).open(path)?;

        let source = unsafe { MmapOptions::new().map(&file)? };
        let original_len = source.len();
        if original_len == 0 {
            return Ok(());
        }

        let replacements = self.collect_replacements(re, replacement, &source[..])?;
        if replacements.is_empty() {
            return Ok(());
        }

        drop(source);

        self.write_replacements_with_mmap(&file, original_len, &replacements)
    }

    fn write_replacements_with_mmap(
        &self,
        file: &File,
        original_len: usize,
        replacements: &[ReplacementOp],
    ) -> anyhow::Result<()> {
        let (new_len, max_extra_growth) = self.plan_replacements(original_len, replacements)?;

        let required_len = original_len + max_extra_growth;
        if required_len > original_len {
            file.set_len(required_len as u64)?;
        }

        {
            let mut mmap = unsafe { MmapOptions::new().map_mut(file)? };
            self.apply_replacements_in_place(&mut mmap, original_len, replacements)?;
        }

        if new_len < required_len {
            file.set_len(new_len as u64)?;
        }

        Ok(())
    }

    fn collect_replacements(
        &self,
        re: &regex::bytes::Regex,
        replacement: &str,
        source: &[u8],
    ) -> anyhow::Result<Vec<ReplacementOp>> {
        let mut replacements = Vec::new();

        for captures in re.captures_iter(source) {
            let matched = captures
                .get(0)
                .ok_or_else(|| anyhow::anyhow!("regex capture missing full match"))?;

            let mut expanded = Vec::new();
            captures.expand(replacement.as_bytes(), &mut expanded);

            replacements.push(ReplacementOp {
                start: matched.start(),
                end: matched.end(),
                bytes: expanded,
            });
        }

        Ok(replacements)
    }

    fn plan_replacements(
        &self,
        original_len: usize,
        replacements: &[ReplacementOp],
    ) -> anyhow::Result<(usize, usize)> {
        let mut final_len = original_len;
        let mut running_delta: isize = 0;
        let mut max_extra_growth = 0usize;

        for replacement in replacements {
            final_len = final_len
                .checked_sub(replacement.end - replacement.start)
                .ok_or_else(|| anyhow::anyhow!("replacement length underflow"))?;
            final_len = final_len
                .checked_add(replacement.bytes.len())
                .ok_or_else(|| anyhow::anyhow!("replacement length overflow"))?;

            let replacement_delta =
                replacement.bytes.len() as isize - (replacement.end - replacement.start) as isize;
            running_delta += replacement_delta;
            max_extra_growth = max_extra_growth.max(running_delta.max(0) as usize);
        }

        Ok((final_len, max_extra_growth))
    }

    fn apply_replacements_in_place(
        &self,
        mmap: &mut MmapMut,
        original_len: usize,
        replacements: &[ReplacementOp],
    ) -> anyhow::Result<()> {
        let mut current_len = original_len;
        let mut delta: isize = 0;

        for replacement in replacements {
            let start = (replacement.start as isize + delta) as usize;
            let end = (replacement.end as isize + delta) as usize;
            let old_match_len = end - start;
            let new_match_len = replacement.bytes.len();
            let tail_start = end;

            if new_match_len > old_match_len {
                let grow_by = new_match_len - old_match_len;
                mmap.copy_within(tail_start..current_len, tail_start + grow_by);
                current_len += grow_by;
                delta += grow_by as isize;
            } else if new_match_len < old_match_len {
                let shrink_by = old_match_len - new_match_len;
                mmap.copy_within(tail_start..current_len, tail_start - shrink_by);
                current_len -= shrink_by;
                delta -= shrink_by as isize;
            }

            let write_end = start + new_match_len;
            mmap[start..write_end].copy_from_slice(&replacement.bytes);
        }

        mmap.flush()?;
        Ok(())
    }

    pub fn count_matches(
        &self,
        pattern: &str,
        path: &str,
        ignore_case: bool,
        fixed_strings: bool,
        invert_match: bool,
    ) -> anyhow::Result<usize> {
        self.count_policy(
            pattern,
            path,
            ignore_case,
            fixed_strings,
            invert_match,
            true,
        )
    }

    pub fn count_matches_nofollow(
        &self,
        pattern: &str,
        path: &str,
        ignore_case: bool,
        fixed_strings: bool,
        invert_match: bool,
    ) -> anyhow::Result<usize> {
        self.count_policy(
            pattern,
            path,
            ignore_case,
            fixed_strings,
            invert_match,
            false,
        )
    }

    fn count_policy(
        &self,
        pattern: &str,
        path: &str,
        ignore_case: bool,
        fixed_strings: bool,
        invert_match: bool,
        follow_explicit_leaf_link: bool,
    ) -> anyhow::Result<usize> {
        let mut total_count = 0;
        let target_path = Path::new(path);
        if fixed_strings && !ignore_case {
            let pat_bytes = pattern.as_bytes();
            for target in collect_search_targets(target_path, follow_explicit_leaf_link)? {
                total_count += self.count_file_memmem(pat_bytes, &target, invert_match)?;
            }
            return Ok(total_count);
        }
        let re = build_cpu_regex(pattern, ignore_case, fixed_strings)?;
        for target in collect_search_targets(target_path, follow_explicit_leaf_link)? {
            total_count += self.count_file_regex(&re, &target, invert_match)?;
        }
        Ok(total_count)
    }

    fn count_file_memmem(
        &self,
        pattern: &[u8],
        path: &Path,
        invert_match: bool,
    ) -> anyhow::Result<usize> {
        let file = crate::search_file_open::open_native_search_file(path)?;
        let mmap = unsafe { MmapOptions::new().map(&file)? };

        // For ripgrep parity on count matches, we count MATCHING LINES, not total occurrences.
        // grep's line model: a trailing '\n' terminates the last line without adding a phantom
        // empty line, but interior empty lines ARE lines and must be counted under -v. Strip a
        // single trailing '\n', then never drop empty lines (an empty line cannot match a
        // non-empty pattern, so non-invert counts are unaffected). audit MED.
        if mmap.is_empty() {
            return Ok(0);
        }
        let content: &[u8] = if mmap.last() == Some(&b'\n') {
            &mmap[..mmap.len() - 1]
        } else {
            &mmap[..]
        };
        let count = content
            .par_split(|&b| b == b'\n')
            .filter(|line_bytes| {
                let is_match = memmem::find(line_bytes, pattern).is_some();
                if invert_match {
                    !is_match
                } else {
                    is_match
                }
            })
            .count();

        Ok(count)
    }

    fn count_file_regex(
        &self,
        re: &regex::bytes::Regex,
        path: &Path,
        invert_match: bool,
    ) -> anyhow::Result<usize> {
        let file = crate::search_file_open::open_native_search_file(path)?;
        let mmap = unsafe { MmapOptions::new().map(&file)? };

        // See count_file_memmem: strip a single trailing '\n', keep interior empties so -v
        // counts blank lines (audit MED).
        if mmap.is_empty() {
            return Ok(0);
        }
        let content: &[u8] = if mmap.last() == Some(&b'\n') {
            &mmap[..mmap.len() - 1]
        } else {
            &mmap[..]
        };
        let count = content
            .par_split(|&b| b == b'\n')
            .filter(|line_bytes| {
                let is_match = re.is_match(line_bytes);
                if invert_match {
                    !is_match
                } else {
                    is_match
                }
            })
            .count();

        Ok(count)
    }
}

#[cfg(test)]
mod tests;

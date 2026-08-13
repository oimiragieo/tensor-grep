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
        let path_obj = Path::new(path);
        let mut results = Vec::new();

        if fixed_strings && !ignore_case {
            let pat_bytes = pattern.as_bytes();
            if path_obj.is_file() {
                results.extend(self.search_file_memmem(
                    pat_bytes,
                    &path_obj.to_path_buf(),
                    invert_match,
                )?);
            } else if path_obj.is_dir() {
                for entry in WalkDir::new(path_obj)
                    .into_iter()
                    .collect::<Result<Vec<_>, _>>()?
                {
                    if entry.file_type().is_file() {
                        results.extend(self.search_file_memmem(
                            pat_bytes,
                            &entry.path().to_path_buf(),
                            invert_match,
                        )?);
                    }
                }
            }
            return Ok(results);
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
            results.extend(self.search_file_regex(&re, &path_obj.to_path_buf(), invert_match)?);
        } else if path_obj.is_dir() {
            for entry in WalkDir::new(path_obj)
                .into_iter()
                .collect::<Result<Vec<_>, _>>()?
            {
                if entry.file_type().is_file() {
                    results.extend(self.search_file_regex(
                        &re,
                        &entry.path().to_path_buf(),
                        invert_match,
                    )?);
                }
            }
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
        path: &PathBuf,
        invert_match: bool,
    ) -> anyhow::Result<Vec<CpuMatch>> {
        let file = File::open(path)?;
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
        path: &PathBuf,
        invert_match: bool,
    ) -> anyhow::Result<Vec<CpuMatch>> {
        let file = File::open(path)?;
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
        // WalkDir (opus gate r4 F1, measured). components() normalization strips trailing
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
                self.replace_file_literal(
                    pattern.as_bytes(),
                    replacement.as_bytes(),
                    &path_obj.to_path_buf(),
                )?;
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
            self.replace_file_regex(&re, replacement, &path_obj.to_path_buf())?;
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
        path: &PathBuf,
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
        path: &PathBuf,
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
        let path_obj = Path::new(path);
        let mut total_count = 0;

        // Extreme fast path: fixed strings, case sensitive, use memmem
        if fixed_strings && !ignore_case {
            let pat_bytes = pattern.as_bytes();
            if path_obj.is_file() {
                total_count +=
                    self.count_file_memmem(pat_bytes, &path_obj.to_path_buf(), invert_match)?;
            } else if path_obj.is_dir() {
                for entry in WalkDir::new(path_obj)
                    .into_iter()
                    .collect::<Result<Vec<_>, _>>()?
                {
                    if entry.file_type().is_file() {
                        total_count += self.count_file_memmem(
                            pat_bytes,
                            &entry.path().to_path_buf(),
                            invert_match,
                        )?;
                    }
                }
            }
            return Ok(total_count);
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
            total_count += self.count_file_regex(&re, &path_obj.to_path_buf(), invert_match)?;
        } else if path_obj.is_dir() {
            for entry in WalkDir::new(path_obj)
                .into_iter()
                .collect::<Result<Vec<_>, _>>()?
            {
                if entry.file_type().is_file() {
                    total_count +=
                        self.count_file_regex(&re, &entry.path().to_path_buf(), invert_match)?;
                }
            }
        }

        Ok(total_count)
    }

    fn count_file_memmem(
        &self,
        pattern: &[u8],
        path: &PathBuf,
        invert_match: bool,
    ) -> anyhow::Result<usize> {
        let file = File::open(path)?;
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
        path: &PathBuf,
        invert_match: bool,
    ) -> anyhow::Result<usize> {
        let file = File::open(path)?;
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
mod tests {
    use super::*;

    // --- Intra-file parallel search: chunk planning + per-line scan correctness -------------
    //
    // These are unit tests of the private helpers (`plan_line_aligned_chunks`,
    // `scan_lines_memmem`/`scan_lines_regex`, `search_contents_*_maybe_parallel`), so they live
    // inline rather than in `rust_core/tests/test_search.rs` (which only sees `CpuBackend`'s
    // public API). The crux property under test throughout: a chunked-and-reassembled scan
    // must be BYTE-IDENTICAL to a single-pass whole-buffer scan -- same matches, same line
    // numbers, same order, no dup/missed match at a chunk seam.

    fn line_bytes(lines: &[&str]) -> Vec<u8> {
        let mut bytes = Vec::new();
        for line in lines {
            bytes.extend_from_slice(line.as_bytes());
            bytes.push(b'\n');
        }
        bytes
    }

    #[test]
    fn plan_line_aligned_chunks_empty_content_returns_empty() {
        let chunks = plan_line_aligned_chunks(b"", 4);
        assert!(chunks.is_empty());
    }

    #[test]
    fn plan_line_aligned_chunks_single_requested_chunk_returns_empty() {
        // requested_chunks <= 1 must signal "use the whole-buffer fallback", not a 1-item plan
        // -- callers rely on `chunks.len() > 1` to decide whether to go parallel at all.
        let content = line_bytes(&["a", "b", "c"]);
        let chunks = plan_line_aligned_chunks(&content, 1);
        assert!(chunks.is_empty());
    }

    #[test]
    fn plan_line_aligned_chunks_boundaries_never_split_a_line() {
        // 400 short, fixed-width lines so the target chunk size lands mid-line for most chunk
        // counts unless the newline-snap logic in `align_chunk_end_to_newline` kicks in.
        let lines: Vec<String> = (0..400).map(|i| format!("line-{i:04}")).collect();
        let line_refs: Vec<&str> = lines.iter().map(String::as_str).collect();
        let content = line_bytes(&line_refs);

        let chunks = plan_line_aligned_chunks(&content, 7);
        assert!(
            chunks.len() > 1,
            "expected multiple chunks for this fixture"
        );

        assert_eq!(chunks[0].start, 0);
        assert_eq!(chunks.last().unwrap().end, content.len());
        for pair in chunks.windows(2) {
            assert_eq!(
                pair[0].end, pair[1].start,
                "chunk boundaries must be contiguous: no gap, no overlap"
            );
        }
        for chunk in &chunks {
            assert!(
                chunk.end == content.len() || content[chunk.end - 1] == b'\n',
                "chunk end {} does not sit immediately after a newline",
                chunk.end
            );
            assert!(
                chunk.start == 0 || content[chunk.start - 1] == b'\n',
                "chunk start {} does not sit immediately after a newline",
                chunk.start
            );
            let expected_first_line = 1 + count_newlines(&content[..chunk.start]);
            assert_eq!(chunk.first_line, expected_first_line);
        }
    }

    #[test]
    fn plan_line_aligned_chunks_full_coverage_across_many_chunk_counts() {
        // Broader sweep than the single-fixture test above: variable-length lines (to stress
        // many different alignment offsets) checked against every requested chunk count from 2
        // to 16. For every count, the plan must cover the whole buffer exactly once with no
        // gap, no overlap, and every boundary landing right after a newline.
        let lines: Vec<String> = (0..777)
            .map(|i| format!("line-{i:04}-{}", "y".repeat(i % 5)))
            .collect();
        let line_refs: Vec<&str> = lines.iter().map(String::as_str).collect();
        let content = line_bytes(&line_refs);

        for requested_chunks in 2..=16 {
            let chunks = plan_line_aligned_chunks(&content, requested_chunks);
            assert!(
                !chunks.is_empty(),
                "requested_chunks={requested_chunks} unexpectedly produced no plan"
            );
            assert_eq!(chunks[0].start, 0, "requested_chunks={requested_chunks}");
            assert_eq!(
                chunks.last().unwrap().end,
                content.len(),
                "requested_chunks={requested_chunks}"
            );
            for pair in chunks.windows(2) {
                assert_eq!(
                    pair[0].end, pair[1].start,
                    "gap/overlap at requested_chunks={requested_chunks}"
                );
            }
            for chunk in &chunks {
                assert!(
                    chunk.end == content.len() || content[chunk.end - 1] == b'\n',
                    "boundary not newline-aligned at requested_chunks={requested_chunks}"
                );
            }
        }
    }

    #[test]
    fn align_chunk_end_to_newline_snaps_forward_when_mid_line() {
        let content = b"aaaa\nbbbb\ncccc\n";
        // tentative_end = 2 lands inside "aaaa" -- must snap to right after the first '\n'.
        assert_eq!(align_chunk_end_to_newline(content, 2), 5);
    }

    #[test]
    fn align_chunk_end_to_newline_unchanged_when_already_at_boundary() {
        let content = b"aaaa\nbbbb\ncccc\n";
        assert_eq!(align_chunk_end_to_newline(content, 5), 5);
    }

    #[test]
    fn align_chunk_end_to_newline_runs_to_eof_when_no_more_newlines() {
        let content = b"aaaa\nbbbb\ncccc"; // no trailing newline
        assert_eq!(align_chunk_end_to_newline(content, 12), content.len());
    }

    #[test]
    fn scan_lines_memmem_chunked_matches_whole_buffer_serial_scan() {
        // Needles placed at the very first line, the very last line, and immediately around
        // every internal chunk boundary computed for `REQUESTED_CHUNKS` -- exactly the
        // seam-adjacent positions a chunking bug would get wrong.
        const LINE_BYTES: usize = 64;
        const TOTAL_LINES: usize = 500;
        const REQUESTED_CHUNKS: usize = 5;
        const NEEDLE: &[u8] = b"NEEDLE";

        let lines_per_chunk = TOTAL_LINES / REQUESTED_CHUNKS;
        let needle_lines: Vec<usize> = vec![
            1,
            lines_per_chunk,
            lines_per_chunk + 1,
            lines_per_chunk * 2,
            lines_per_chunk * 2 + 1,
            lines_per_chunk * 3,
            lines_per_chunk * 3 + 1,
            lines_per_chunk * 4,
            lines_per_chunk * 4 + 1,
            TOTAL_LINES,
        ];

        let mut content = Vec::with_capacity(LINE_BYTES * TOTAL_LINES);
        for line_number in 1..=TOTAL_LINES {
            let mut line = if needle_lines.contains(&line_number) {
                format!("L{line_number:04} NEEDLE")
            } else {
                format!("L{line_number:04} filler")
            };
            assert!(line.len() < LINE_BYTES);
            line.push_str(&"x".repeat(LINE_BYTES - line.len() - 1));
            line.push('\n');
            content.extend_from_slice(line.as_bytes());
        }

        let path = Path::new("chunk-boundary-fixture.log");
        let serial = scan_lines_memmem(&content, NEEDLE, false, 1, path);

        let chunks = plan_line_aligned_chunks(&content, REQUESTED_CHUNKS);
        assert!(chunks.len() > 1, "fixture must produce multiple chunks");

        let mut parallel = Vec::new();
        for chunk in &chunks {
            parallel.extend(scan_lines_memmem(
                &content[chunk.start..chunk.end],
                NEEDLE,
                false,
                chunk.first_line,
                path,
            ));
        }

        assert_eq!(
            serial, parallel,
            "chunked-and-reassembled matches must be byte-identical to the whole-buffer serial scan"
        );
        assert_eq!(serial.len(), needle_lines.len());
        assert_eq!(
            serial.iter().map(|m| m.line).collect::<Vec<_>>(),
            needle_lines
        );
        for matched in &serial {
            assert!(matched.text.contains("NEEDLE"));
        }
    }

    #[test]
    fn scan_lines_memmem_invert_match_chunked_matches_serial() {
        const LINE_BYTES: usize = 32;
        const TOTAL_LINES: usize = 300;
        const REQUESTED_CHUNKS: usize = 4;

        let mut content = Vec::new();
        for line_number in 1..=TOTAL_LINES {
            let mut line = if line_number % 3 == 0 {
                format!("L{line_number:04} SKIP")
            } else {
                format!("L{line_number:04} keep")
            };
            assert!(line.len() < LINE_BYTES);
            line.push_str(&"x".repeat(LINE_BYTES - line.len() - 1));
            line.push('\n');
            content.extend_from_slice(line.as_bytes());
        }

        let path = Path::new("invert-fixture.log");
        let serial = scan_lines_memmem(&content, b"SKIP", true, 1, path);

        let chunks = plan_line_aligned_chunks(&content, REQUESTED_CHUNKS);
        assert!(chunks.len() > 1, "fixture must produce multiple chunks");

        let mut parallel = Vec::new();
        for chunk in &chunks {
            parallel.extend(scan_lines_memmem(
                &content[chunk.start..chunk.end],
                b"SKIP",
                true,
                chunk.first_line,
                path,
            ));
        }

        assert_eq!(serial, parallel);
        assert_eq!(serial.len(), TOTAL_LINES - TOTAL_LINES / 3);
    }

    #[test]
    fn scan_lines_regex_chunked_matches_serial() {
        const LINE_BYTES: usize = 32;
        const TOTAL_LINES: usize = 300;
        const REQUESTED_CHUNKS: usize = 4;

        let mut content = Vec::new();
        for line_number in 1..=TOTAL_LINES {
            let mut line = if line_number % 5 == 0 {
                format!("L{line_number:04} ERR{line_number}")
            } else {
                format!("L{line_number:04} ok")
            };
            assert!(line.len() < LINE_BYTES);
            line.push_str(&"x".repeat(LINE_BYTES - line.len() - 1));
            line.push('\n');
            content.extend_from_slice(line.as_bytes());
        }

        let re = regex::bytes::RegexBuilder::new(r"ERR\d+").build().unwrap();
        let path = Path::new("regex-fixture.log");
        let serial = scan_lines_regex(&content, &re, false, 1, path);

        let chunks = plan_line_aligned_chunks(&content, REQUESTED_CHUNKS);
        assert!(chunks.len() > 1, "fixture must produce multiple chunks");

        let mut parallel = Vec::new();
        for chunk in &chunks {
            parallel.extend(scan_lines_regex(
                &content[chunk.start..chunk.end],
                &re,
                false,
                chunk.first_line,
                path,
            ));
        }

        assert_eq!(serial, parallel);
        assert_eq!(serial.len(), TOTAL_LINES / 5);
    }

    #[test]
    fn scan_lines_memmem_no_match_chunked_matches_serial_empty_result() {
        // The task's explicit "no-match" edge case: a large multi-line, multi-chunk buffer
        // that contains the needle nowhere must produce an empty result on both the serial
        // whole-buffer scan and every parallel chunk -- no false positive introduced by
        // chunking, and no panic/hang on an all-empty-per-chunk result set.
        const LINE_BYTES: usize = 40;
        const TOTAL_LINES: usize = 400;
        const REQUESTED_CHUNKS: usize = 6;

        let mut content = Vec::new();
        for line_number in 1..=TOTAL_LINES {
            let mut line = format!("L{line_number:04} nothing-to-see-here");
            assert!(line.len() < LINE_BYTES);
            line.push_str(&"x".repeat(LINE_BYTES - line.len() - 1));
            line.push('\n');
            content.extend_from_slice(line.as_bytes());
        }

        let path = Path::new("no-match-fixture.log");
        let serial = scan_lines_memmem(&content, b"NEEDLE", false, 1, path);
        assert!(serial.is_empty(), "serial scan must find nothing");

        let chunks = plan_line_aligned_chunks(&content, REQUESTED_CHUNKS);
        assert!(chunks.len() > 1, "fixture must produce multiple chunks");

        let mut parallel = Vec::new();
        for chunk in &chunks {
            parallel.extend(scan_lines_memmem(
                &content[chunk.start..chunk.end],
                b"NEEDLE",
                false,
                chunk.first_line,
                path,
            ));
        }

        assert_eq!(serial, parallel);
        assert!(
            parallel.is_empty(),
            "no chunk may introduce a false-positive match"
        );

        // And through the real dispatcher (whichever path this machine takes):
        let dispatched = search_contents_memmem_maybe_parallel(&content, b"NEEDLE", false, path);
        assert!(dispatched.is_empty());
    }

    #[test]
    fn search_contents_memmem_maybe_parallel_small_buffer_matches_direct_scan() {
        // Below LARGE_FILE_PARALLEL_THRESHOLD_BYTES: must behave exactly like calling
        // `scan_lines_memmem` directly -- the "small files stay on the unchanged serial path"
        // contract.
        let content = line_bytes(&["alpha", "NEEDLE here", "beta", "NEEDLE again"]);
        let path = Path::new("small.log");

        let expected = scan_lines_memmem(&content, b"NEEDLE", false, 1, path);
        let actual = search_contents_memmem_maybe_parallel(&content, b"NEEDLE", false, path);

        assert_eq!(actual, expected);
        assert_eq!(actual.len(), 2);
    }

    #[test]
    fn search_contents_regex_maybe_parallel_small_buffer_matches_direct_scan() {
        let content = line_bytes(&["alpha", "ERR42 here", "beta", "ERR7 again"]);
        let re = regex::bytes::RegexBuilder::new(r"ERR\d+").build().unwrap();
        let path = Path::new("small-regex.log");

        let expected = scan_lines_regex(&content, &re, false, 1, path);
        let actual = search_contents_regex_maybe_parallel(&content, &re, false, path);

        assert_eq!(actual, expected);
        assert_eq!(actual.len(), 2);
    }

    #[test]
    fn search_contents_memmem_maybe_parallel_uses_chunking_on_a_large_buffer() {
        // End-to-end proof at the real size threshold: build a buffer at/above
        // LARGE_FILE_PARALLEL_THRESHOLD_BYTES, confirm the dispatcher's own chunk plan really
        // does produce more than one chunk on this machine (otherwise this test would silently
        // exercise only the fallback), and assert the dispatched result is identical to a
        // direct whole-buffer scan.
        if requested_parallel_chunk_count() < 2 {
            return;
        }

        const LINE_BYTES: usize = 64;
        let total_lines = (LARGE_FILE_PARALLEL_THRESHOLD_BYTES / LINE_BYTES) + 1000;
        let mut filler_line = b"filler".to_vec();
        filler_line.resize(LINE_BYTES - 1, b'x');
        filler_line.push(b'\n');
        let mut needle_line = b"NEEDLE".to_vec();
        needle_line.resize(LINE_BYTES - 1, b'x');
        needle_line.push(b'\n');
        let needle_line_numbers = [1usize, total_lines / 2, total_lines];

        let mut content = Vec::with_capacity(LINE_BYTES * total_lines);
        for line_number in 1..=total_lines {
            if needle_line_numbers.contains(&line_number) {
                content.extend_from_slice(&needle_line);
            } else {
                content.extend_from_slice(&filler_line);
            }
        }
        assert!(content.len() >= LARGE_FILE_PARALLEL_THRESHOLD_BYTES);

        let chunks = plan_line_aligned_chunks(&content, requested_parallel_chunk_count());
        assert!(
            chunks.len() > 1,
            "fixture must be large enough to actually engage chunking on this machine"
        );

        let path = Path::new("large-buffer-fixture.log");
        let expected = scan_lines_memmem(&content, b"NEEDLE", false, 1, path);
        let actual = search_contents_memmem_maybe_parallel(&content, b"NEEDLE", false, path);

        assert_eq!(actual, expected);
        assert_eq!(actual.len(), needle_line_numbers.len());
    }

    // --- replace_in_place directory-mode fault injection (Task 5) --------------------------
    //
    // These reach a PRIVATE seam (`replace_fault_injection`) that `rust_core/tests/test_replace.rs`
    // cannot see: that file links against the normal (non-`--cfg test`) build of this crate, where
    // the field and every check against it compile away entirely. Only these in-file unit tests,
    // compiled as part of the SAME `--cfg test` crate build, can set it. Each test drives the
    // fault through the PUBLIC `replace_in_place` entry point -- not a parallel/shadow helper --
    // proving the injected seam actually fired through the real delegated core
    // (`walk_directory_entries` / `replace_directory_literal` / `replace_directory_regex`).

    fn write_fixture_file(path: &Path, contents: &str) {
        std::fs::write(path, contents).unwrap();
    }

    #[test]
    fn test_replace_in_place_directory_walk_failure_propagates_as_err_with_path_context() {
        let dir = tempfile::tempdir().unwrap();
        write_fixture_file(&dir.path().join("a.txt"), "needle");

        let backend = CpuBackend::new();
        backend
            .replace_fault_injection
            .lock()
            .unwrap()
            .force_walk_failure = true;

        let result =
            backend.replace_in_place("needle", "found", dir.path().to_str().unwrap(), false, true);

        let err = result.expect_err(
            "a walk failure must surface as Err through the public replace_in_place, not be swallowed",
        );
        let message = format!("{err:#}");
        assert!(
            message.contains("directory walk failed"),
            "error should name the walk operation that failed, got: {message}"
        );
        assert!(
            message.contains(dir.path().to_str().unwrap()),
            "error should carry the directory path, got: {message}"
        );

        // Prove the seam fired through the real core, not a shadow path: the child file was
        // never touched because the walk failed before any file was processed.
        assert_eq!(
            std::fs::read_to_string(dir.path().join("a.txt")).unwrap(),
            "needle",
            "no child file should be modified when the walk itself fails"
        );
    }

    #[test]
    fn test_replace_in_place_directory_literal_child_failure_propagates_as_err_with_path_context() {
        let dir = tempfile::tempdir().unwrap();
        let child = dir.path().join("a.txt");
        write_fixture_file(&child, "needle");

        let backend = CpuBackend::new();
        backend
            .replace_fault_injection
            .lock()
            .unwrap()
            .force_literal_child_failure = true;

        let result =
            backend.replace_in_place("needle", "found", dir.path().to_str().unwrap(), false, true);

        let err = result.expect_err(
            "a literal-mode child replace failure must surface as Err through the public \
             replace_in_place, not be swallowed by a discarded `let _ = ...`",
        );
        let message = format!("{err:#}");
        assert!(
            message.contains("literal replace failed"),
            "error should name the literal-replace operation that failed, got: {message}"
        );
        assert!(
            message.contains(child.to_str().unwrap()),
            "error should carry the failing child path, got: {message}"
        );
    }

    #[test]
    fn test_replace_in_place_directory_regex_child_failure_propagates_as_err_with_path_context() {
        let dir = tempfile::tempdir().unwrap();
        let child = dir.path().join("a.txt");
        write_fixture_file(&child, "id:1");

        let backend = CpuBackend::new();
        backend
            .replace_fault_injection
            .lock()
            .unwrap()
            .force_regex_child_failure = true;

        let result = backend.replace_in_place(
            r"id:(\d+)",
            "ID#$1",
            dir.path().to_str().unwrap(),
            false,
            false,
        );

        let err = result.expect_err(
            "a regex-mode child replace failure must surface as Err through the public \
             replace_in_place, not be swallowed by a discarded `let _ = ...`",
        );
        let message = format!("{err:#}");
        assert!(
            message.contains("regex replace failed"),
            "error should name the regex-replace operation that failed, got: {message}"
        );
        assert!(
            message.contains(child.to_str().unwrap()),
            "error should carry the failing child path, got: {message}"
        );
    }

    #[test]
    fn test_replace_in_place_fails_closed_when_symlink_metadata_errors() {
        // The guard's FIRST draft was `if let Ok(meta) = symlink_metadata(..)`, which fails
        // OPEN: on any stat error the guard silently does nothing and the follow behaviour
        // returns, with nothing observable distinguishing "checked and safe" from "could not
        // check". This is the arm that discriminates those two.
        let dir = tempfile::tempdir().unwrap();
        let target = dir.path().join("plain.txt");
        write_fixture_file(&target, "needle");

        let backend = CpuBackend::new();
        backend
            .replace_fault_injection
            .lock()
            .unwrap()
            .force_symlink_metadata_failure = true;

        let result =
            backend.replace_in_place("needle", "found", target.to_str().unwrap(), false, true);

        let err = result.expect_err(
            "a symlink_metadata failure must fail CLOSED as Err, never fall through to the rewrite",
        );
        let msg = format!("{err:#}");
        assert!(
            msg.contains(&target.display().to_string()),
            "the error must name the path it could not stat; got {msg}"
        );
        assert_eq!(
            std::fs::read_to_string(&target).unwrap(),
            "needle",
            "FAILED OPEN: the file was rewritten even though the guard could not stat it"
        );
    }

    #[test]
    fn test_replace_in_place_rewrites_normally_when_no_metadata_fault_is_injected() {
        // The other direction of the bidirectional pair. Without this, a guard that refused
        // on EVERY stat outcome would pass the test above and be indistinguishable from a
        // correct fail-closed guard. Same fixture, same call, fault flag OFF.
        let dir = tempfile::tempdir().unwrap();
        let target = dir.path().join("plain.txt");
        write_fixture_file(&target, "needle");

        let backend = CpuBackend::new();
        backend
            .replace_in_place("needle", "found", target.to_str().unwrap(), false, true)
            .expect("with no injected fault a regular file must still be rewritten");

        assert_eq!(std::fs::read_to_string(&target).unwrap(), "found");
    }

    #[test]
    fn test_replace_in_place_on_a_missing_path_still_errors_with_the_path_named() {
        // Contract-change pin, not a compatibility pin (opus gate r3 F1): a nonexistent path
        // was a SILENT Ok(()) no-op BEFORE the guard (the old pin test proved it) and is now
        // Err via the fail-closed symlink_metadata. The contract this PR deliberately changes
        // and must hold going forward is "Err, naming the path" -- not which producer made it.
        // Pinning the contract rather than the message keeps this from becoming a
        // change-detector test.
        let dir = tempfile::tempdir().unwrap();
        let missing = dir.path().join("does-not-exist.txt");

        let backend = CpuBackend::new();
        let err = backend
            .replace_in_place("needle", "found", missing.to_str().unwrap(), false, true)
            .expect_err("a missing path must still be an Err");

        assert!(
            format!("{err:#}").contains(&missing.display().to_string()),
            "the error must name the missing path"
        );
    }

    #[test]
    fn test_replace_in_place_leaf_swapped_between_guard_and_open_characterizes_the_residual_window()
    {
        // CHARACTERIZATION PIN, NOT A RED. A38/A48: the leaf check and a swap-resistant WRITER
        // are SEPARATE contracts, and this PR ships the first only. This test asserts the
        // CURRENT behaviour -- the window is OPEN, and a leaf swapped between the guard's stat
        // and the writer's open still redirects the write -- so the residual is an enforced
        // property of the code rather than a sentence in a doc that nothing checks.
        //
        // WHEN RUST-REPLACE-TOCTOU LANDS, THIS PIN MUST FLIP: the writer will open with
        // O_NOFOLLOW / FILE_FLAG_OPEN_REPARSE_POINT, the swap will stop redirecting, and this
        // assertion will fail. That failure is the REOPEN SIGNAL for that row -- invert the
        // assertion then, do not delete the test.
        //
        // Bounded by construction: a two-party capacity-1 handshake with a 2s cap on every
        // wait, no spin loop and no wall-clock race. An unbounded race test on a shared CI
        // runner is a flake generator, not evidence.
        let dir = tempfile::tempdir().unwrap();
        let target = dir.path().join("plain.txt");
        let attacker = dir.path().join("attacker-owned.txt");
        write_fixture_file(&target, "needle");
        write_fixture_file(&attacker, "needle");

        let (reached_tx, reached_rx) = std::sync::mpsc::sync_channel::<()>(1);
        let (resume_tx, resume_rx) = std::sync::mpsc::sync_channel::<()>(1);

        let backend = CpuBackend::new();
        backend.replace_fault_injection.lock().unwrap().swap_gate = Some(SwapGate {
            reached_guard: reached_tx,
            resume: resume_rx,
        });

        // ACTOR 1: the writer. The backend is MOVED into the thread (the test keeps nothing
        // but the channel endpoints); CpuBackend is Send (and, via Mutex, Sync), so the spawn
        // is sound on either auto-trait.
        let writer_path = target.clone();
        let writer = std::thread::spawn(move || {
            backend.replace_in_place(
                "needle",
                "found",
                writer_path.to_str().unwrap(),
                false,
                true,
            )
        });

        // Bounded wait: the writer must reach the post-guard point, or this test measured
        // nothing at all.
        reached_rx.recv_timeout(SWAP_GATE_TIMEOUT).expect(
            "CANNOT_MEASURE: the writer never signalled the post-guard point within 2s; the \
             gate is not wired at the guard site",
        );

        // ACTOR 2: the attacker, deterministically inside the window.
        let swapped = swap_leaf_to_symlink_for_test(&target, &attacker);

        // Release the writer either way -- an un-acknowledged writer would sit until its own
        // 2s bound and panic in a thread, turning a skip into a confusing failure. The send
        // itself cannot block (capacity 1); it errors immediately only if the writer is gone.
        resume_tx
            .send(())
            .expect("CANNOT_MEASURE: the writer thread vanished before acknowledgment");
        let write_result = writer.join().expect("the writer thread panicked");

        if !swapped {
            // The skip line is already on stderr from the helper. No verdict from this node.
            return;
        }

        assert!(
            write_result.is_ok(),
            "the writer errored instead of following the swapped-in link; the window may have \
             moved -- re-derive before changing this pin"
        );
        // THE PIN ITSELF: the window is OPEN, so the swap redirected the write.
        assert_eq!(
            std::fs::read_to_string(&attacker).unwrap(),
            "found",
            "the swap did not redirect the write; the window may have CLOSED (a RUST-REPLACE-TOCTOU \
             fix landing flips this pin -- invert the assertion and close that row)"
        );
        // The original leaf was REMOVED by the swap helper and replaced by a symlink, so
        // read_to_string(&target) would FOLLOW it -- that reads the attacker's file, not the
        // old leaf. The correct invariant is structural: the swapped-in link must still BE a
        // symlink, proving the write went THROUGH it rather than replacing it.
        assert!(
            std::fs::symlink_metadata(&target)
                .unwrap()
                .file_type()
                .is_symlink(),
            "the swapped-in link was replaced by a regular file; the write did not go THROUGH it"
        );
    }
}

#[cfg(test)]
mod tests_h1 {
    use super::*;

    #[test]
    #[cfg(unix)]
    fn unreadable_walk_entry_is_an_error_not_a_silent_empty_match() {
        // H1 audit: a walk that hits an unreadable directory entry must surface as an error
        // (which rust_backend.py converts to BackendExecutionError -> visible CPU fallback),
        // never as a silent Ok(empty) 0-match from `filter_map(|e| e.ok())`.
        use std::os::unix::fs::PermissionsExt;
        let dir = std::env::temp_dir().join(format!("tg_h1_err_{}", std::process::id()));
        let blocked = dir.join("blocked");
        std::fs::create_dir_all(&blocked).unwrap();
        std::fs::set_permissions(&blocked, std::fs::Permissions::from_mode(0o000)).unwrap();
        let backend = CpuBackend::default();
        let result = backend.search_with_paths("needle", dir.to_str().unwrap(), false, true, false);
        let _ = std::fs::set_permissions(&blocked, std::fs::Permissions::from_mode(0o755));
        let _ = std::fs::remove_dir_all(&dir);
        assert!(
            result.is_err(),
            "an unreadable walk entry must surface as Err, never a silent Ok(empty) 0-match"
        );
    }

    #[test]
    fn happy_path_still_matches() {
        let file = std::env::temp_dir().join(format!("tg_h1_ok_{}.txt", std::process::id()));
        std::fs::write(&file, "some needle text\n").unwrap();
        let backend = CpuBackend::default();
        let result = backend
            .search_with_paths("needle", file.to_str().unwrap(), false, true, false)
            .unwrap();
        let _ = std::fs::remove_file(&file);
        assert!(
            !result.is_empty(),
            "a readable file containing the pattern must still match after the fail-closed fix"
        );
    }
}

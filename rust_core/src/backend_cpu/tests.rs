#![allow(unused_imports)]
use super::*;

#[allow(clippy::module_inception)]
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
        // Contract-change pin, not a compatibility pin : a nonexistent path
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

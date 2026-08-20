use super::*;
use std::fmt::Write as _;
use std::fs;
use tempfile::tempdir;

fn write_test_file(dir: &Path, name: &str, content: &str) {
    fs::write(dir.join(name), content).unwrap();
}

/// Sets a file's modified time to an exact `SystemTime` via std (stable FileTimes API).
/// Used by the M17 F1 metadata-preserving-swap test to make a swapped-in file
/// byte-for-byte identical in mtime (and size, by construction) to the indexed original.
fn set_modified_time(path: &Path, time: SystemTime) {
    let file = fs::OpenOptions::new().write(true).open(path).unwrap();
    file.set_times(std::fs::FileTimes::new().set_modified(time))
        .unwrap();
}

#[test]
fn bincode_deserialize_rejects_hostile_length_prefix_without_oom() {
    // A crafted index declaring ~4 billion file entries but supplying no data must fail
    // with a clean error, not pre-allocate a multi-GB Vec and OOM-abort. Without the
    // bounded_capacity clamp this is Vec::with_capacity(u32::MAX) -> allocation abort;
    // with it, the read loop fails on the first missing entry and returns Err (audit MED).
    let mut data = Vec::new();
    data.extend_from_slice(INDEX_MAGIC);
    data.push(INDEX_FORMAT_VERSION);
    data.extend_from_slice(&0u32.to_le_bytes()); // root_len = 0
    data.extend_from_slice(&u32::MAX.to_le_bytes()); // files_count = hostile
                                                     // no file data follows (truncated)

    let result = bincode_deserialize(&data);
    assert!(result.is_err(), "hostile length prefix must error, not OOM");
}

fn serialize_legacy_v1(index: &TrigramIndex) -> Vec<u8> {
    let mut buf = Vec::new();
    buf.extend_from_slice(INDEX_MAGIC);
    buf.push(1);

    let root_bytes = index.root.to_string_lossy().as_bytes().to_vec();
    buf.extend_from_slice(&(root_bytes.len() as u32).to_le_bytes());
    buf.extend_from_slice(&root_bytes);

    buf.extend_from_slice(&(index.files.len() as u32).to_le_bytes());
    for entry in &index.files {
        let path_bytes = entry.path.to_string_lossy().as_bytes().to_vec();
        buf.extend_from_slice(&(path_bytes.len() as u32).to_le_bytes());
        buf.extend_from_slice(&path_bytes);
        buf.extend_from_slice(&entry.mtime_ns.to_le_bytes());
        buf.extend_from_slice(&entry.size.to_le_bytes());
    }

    buf.extend_from_slice(&(index.postings.len() as u32).to_le_bytes());
    for (trigram, postings) in &index.postings {
        buf.extend_from_slice(trigram);
        buf.extend_from_slice(&(postings.len() as u32).to_le_bytes());
        for posting in postings {
            buf.extend_from_slice(&posting.file_id.to_le_bytes());
            buf.extend_from_slice(&posting.line.to_le_bytes());
        }
    }

    buf
}

fn write_size_reduction_corpus(dir: &Path, file_count: usize) {
    for file_idx in 0..file_count {
        let mut contents = String::new();
        for line_idx in 0..24 {
            writeln!(
                &mut contents,
                "shared needle alpha beta gamma file_{file_idx:04} line_{line_idx:02}"
            )
            .unwrap();
            writeln!(
                &mut contents,
                "error repeated payload delta epsilon zeta file_{file_idx:04} line_{line_idx:02}"
            )
            .unwrap();
        }
        write_test_file(dir, &format!("file_{file_idx:04}.txt"), &contents);
    }
}

#[test]
fn test_build_index_and_search_fixed_string() {
    let dir = tempdir().unwrap();
    write_test_file(dir.path(), "a.txt", "hello world\nfoo bar baz\ngoodbye\n");
    write_test_file(dir.path(), "b.txt", "nothing here\nhello again\n");

    let index = TrigramIndex::build(dir.path()).unwrap();
    assert!(index.file_count() >= 2);
    assert!(index.trigram_count() > 0);

    let results = index.search("hello", false, true).unwrap();
    assert_eq!(results.len(), 2);
    assert!(results.iter().any(|r| r.text.contains("hello world")));
    assert!(results.iter().any(|r| r.text.contains("hello again")));
}

#[test]
fn test_index_case_insensitive_search() {
    let dir = tempdir().unwrap();
    write_test_file(dir.path(), "a.txt", "Hello World\nFOO BAR\n");

    let index = TrigramIndex::build(dir.path()).unwrap();
    let results = index.search("hello", true, true).unwrap();
    assert_eq!(results.len(), 1);
    assert!(results[0].text.contains("Hello World"));
}

#[test]
fn test_index_no_match_returns_empty() {
    let dir = tempdir().unwrap();
    write_test_file(dir.path(), "a.txt", "hello world\n");

    let index = TrigramIndex::build(dir.path()).unwrap();
    let results = index.search("zzzzz", false, true).unwrap();
    assert!(results.is_empty());
}

#[test]
fn test_index_persistence_round_trip() {
    let dir = tempdir().unwrap();
    write_test_file(dir.path(), "a.txt", "hello world\nfoo bar\n");

    let index = TrigramIndex::build(dir.path()).unwrap();
    let index_path = dir.path().join(".tg_index");
    index.save(&index_path).unwrap();

    let loaded = TrigramIndex::load(&index_path).unwrap();
    assert_eq!(loaded.file_count(), index.file_count());
    assert_eq!(loaded.trigram_count(), index.trigram_count());

    let results = loaded.search("hello", false, true).unwrap();
    assert_eq!(results.len(), 1);
}

// -- Audit #138 item #1: atomic save -----------------------------------------------------

#[test]
fn test_save_leaves_no_temp_file_behind_after_success() {
    let dir = tempdir().unwrap();
    write_test_file(dir.path(), "a.txt", "hello world\n");
    let index = TrigramIndex::build(dir.path()).unwrap();
    let index_path = dir.path().join(".tg_index");
    index.save(&index_path).unwrap();

    assert!(index_path.exists());
    let stray_tmp_files: Vec<_> = fs::read_dir(dir.path())
        .unwrap()
        .filter_map(|entry| entry.ok())
        .filter(|entry| entry.file_name().to_string_lossy().contains(".tmp"))
        .collect();
    assert!(
        stray_tmp_files.is_empty(),
        "a successful save must not leave a temp file behind: {stray_tmp_files:?}"
    );
}

#[test]
fn test_save_overwrite_fully_replaces_previous_content_not_a_merge() {
    let dir = tempdir().unwrap();
    write_test_file(dir.path(), "a.txt", "hello world\n");
    let index_path = dir.path().join(".tg_index");

    let first = TrigramIndex::build(dir.path()).unwrap();
    first.save(&index_path).unwrap();
    assert_eq!(TrigramIndex::load(&index_path).unwrap().file_count(), 1);

    write_test_file(dir.path(), "b.txt", "goodbye moon\n");
    let second = TrigramIndex::build(dir.path()).unwrap();
    second.save(&index_path).unwrap();

    let reloaded = TrigramIndex::load(&index_path).unwrap();
    assert_eq!(
        reloaded.file_count(),
        2,
        "the second save must fully replace the destination's content"
    );
}

#[test]
fn atomic_write_bytes_rename_failure_cleans_up_temp_and_returns_err() {
    // Cross-platform deterministic failure injection: renaming a regular file onto a path
    // that is an existing DIRECTORY fails on both POSIX (EISDIR) and Windows -- regardless
    // of the temp file's randomly-generated name, so this does not need to predict it.
    let dir = tempdir().unwrap();
    let path = dir.path().join(".tg_index");
    fs::create_dir(&path).unwrap();

    let result = atomic_write_bytes(&path, b"NEW_CONTENT_MUST_NOT_LAND");
    assert!(
        result.is_err(),
        "rename onto an existing directory must fail"
    );
    assert!(
        path.is_dir(),
        "a failed atomic_write_bytes must not have disturbed the destination"
    );

    let stray_tmp_files: Vec<_> = fs::read_dir(dir.path())
        .unwrap()
        .filter_map(|entry| entry.ok())
        .filter(|entry| entry.file_name().to_string_lossy().contains(".tmp"))
        .collect();
    assert!(
        stray_tmp_files.is_empty(),
        "a failed atomic_write_bytes must clean up its own temp file: {stray_tmp_files:?}"
    );
}

#[test]
fn test_compressed_index_round_trip_preserves_results() {
    let dir = tempdir().unwrap();
    write_test_file(
        dir.path(),
        "a.txt",
        "alpha beta gamma\nerror: something failed\nregex-target-123\n",
    );
    write_test_file(
        dir.path(),
        "b.txt",
        "alpha beta gamma\nwarning: ok\nregex-target-999\n",
    );

    let index = TrigramIndex::build(dir.path()).unwrap();
    let index_path = dir.path().join(".tg_index");
    index.save(&index_path).unwrap();

    let loaded = TrigramIndex::load(&index_path).unwrap();

    let fixed_original = index.search("alpha beta", false, true).unwrap();
    let fixed_loaded = loaded.search("alpha beta", false, true).unwrap();
    assert_eq!(fixed_loaded.len(), fixed_original.len());
    assert_eq!(
        fixed_loaded
            .iter()
            .map(|r| (&r.file, r.line, &r.text))
            .collect::<Vec<_>>(),
        fixed_original
            .iter()
            .map(|r| (&r.file, r.line, &r.text))
            .collect::<Vec<_>>()
    );

    let regex_original = index.search(r"regex-target-\d+", false, false).unwrap();
    let regex_loaded = loaded.search(r"regex-target-\d+", false, false).unwrap();
    assert_eq!(regex_loaded.len(), regex_original.len());
    assert_eq!(
        regex_loaded
            .iter()
            .map(|r| (&r.file, r.line, &r.text))
            .collect::<Vec<_>>(),
        regex_original
            .iter()
            .map(|r| (&r.file, r.line, &r.text))
            .collect::<Vec<_>>()
    );
}

#[test]
fn test_index_staleness_detection() {
    let dir = tempdir().unwrap();
    write_test_file(dir.path(), "a.txt", "hello\n");

    let index = TrigramIndex::build(dir.path()).unwrap();
    assert!(!index.is_stale(false));

    std::thread::sleep(std::time::Duration::from_millis(50));
    write_test_file(dir.path(), "a.txt", "modified\n");
    assert!(index.is_stale(false));
}

#[test]
fn test_index_regex_search() {
    let dir = tempdir().unwrap();
    write_test_file(
        dir.path(),
        "a.txt",
        "error: something failed\nwarning: ok\nerror: again\n",
    );

    let index = TrigramIndex::build(dir.path()).unwrap();
    let results = index.search("error.*failed", false, false).unwrap();
    assert_eq!(results.len(), 1);
    assert!(results[0].text.contains("something failed"));
}

#[test]
fn test_short_pattern_returns_empty() {
    let dir = tempdir().unwrap();
    write_test_file(dir.path(), "a.txt", "ab\n");

    let index = TrigramIndex::build(dir.path()).unwrap();
    let candidates = index.query_candidates("ab", false);
    assert!(
        candidates.is_empty(),
        "patterns shorter than 3 bytes cannot use trigram index"
    );
}

#[test]
fn test_regex_prefilter_literals_cover_alternation_classes_and_unicode() {
    let alternation = select_regex_prefilter_literals(r"(foo|bar)", false).unwrap();
    assert_eq!(alternation.literals, vec![b"bar".to_vec(), b"foo".to_vec()]);

    let char_class = select_regex_prefilter_literals(r"de[ab]f", false).unwrap();
    assert_eq!(
        char_class.literals,
        vec![b"deaf".to_vec(), b"debf".to_vec()]
    );

    let unicode = select_regex_prefilter_literals(r"(東京|大阪)", false).unwrap();
    assert_eq!(
        unicode.literals,
        vec!["大阪".as_bytes().to_vec(), "東京".as_bytes().to_vec()]
    );
}

#[test]
fn test_regex_prefilter_literals_fallback_for_unsafe_patterns() {
    assert!(select_regex_prefilter_literals(r"(foo|ab)", false).is_none());
    assert!(select_regex_prefilter_literals(r"[a-z]{3}", false).is_none());
    assert!(select_regex_prefilter_literals("東京", true).is_none());
}

#[test]
fn test_staleness_detects_content_change() {
    let dir = tempdir().unwrap();
    write_test_file(dir.path(), "a.txt", "hello world\n");
    let index = TrigramIndex::build(dir.path()).unwrap();
    assert!(index.staleness_reason(false).is_none());

    std::thread::sleep(std::time::Duration::from_millis(50));
    write_test_file(dir.path(), "a.txt", "changed content\n");

    let reason = index.staleness_reason(false).unwrap();
    assert!(reason.contains("a.txt"), "reason={reason}");
}

#[test]
fn test_staleness_detects_file_deletion() {
    let dir = tempdir().unwrap();
    write_test_file(dir.path(), "a.txt", "hello\n");
    write_test_file(dir.path(), "b.txt", "world\n");
    let index = TrigramIndex::build(dir.path()).unwrap();

    fs::remove_file(dir.path().join("b.txt")).unwrap();
    let reason = index.staleness_reason(false).unwrap();
    assert!(reason.contains("deleted"), "reason={reason}");
    assert!(reason.contains("b.txt"), "reason={reason}");
}

#[test]
fn test_staleness_detects_new_file() {
    let dir = tempdir().unwrap();
    write_test_file(dir.path(), "a.txt", "hello\n");
    let index = TrigramIndex::build(dir.path()).unwrap();
    assert!(index.staleness_reason(false).is_none());

    write_test_file(dir.path(), "b.txt", "new file\n");
    let reason = index.staleness_reason(false).unwrap();
    assert!(reason.contains("new file"), "reason={reason}");
}

#[test]
fn test_staleness_detects_size_change_same_mtime() {
    let dir = tempdir().unwrap();
    write_test_file(dir.path(), "a.txt", "short\n");
    let index = TrigramIndex::build(dir.path()).unwrap();

    std::thread::sleep(std::time::Duration::from_millis(50));
    write_test_file(
        dir.path(),
        "a.txt",
        "much longer content here to change size\n",
    );
    let reason = index.staleness_reason(false);
    assert!(reason.is_some(), "should detect change");
}

#[test]
fn test_format_version_in_binary() {
    let dir = tempdir().unwrap();
    write_test_file(dir.path(), "a.txt", "hello\n");
    let index = TrigramIndex::build(dir.path()).unwrap();
    let index_path = dir.path().join(".tg_index");
    index.save(&index_path).unwrap();

    let data = fs::read(&index_path).unwrap();
    assert_eq!(&data[0..4], b"TGI\x00", "magic bytes");
    // M17 gate (audit): 6 adds the tree_fingerprint u64 and drops the build-spelling root
    // byte; an older index fails the version gate and is rebuilt from scratch (safe, by the
    // same rationale the 3->4 no_ignore bump and the 4->5 canonical-root bump used).
    assert_eq!(data[4], 6, "format version should be 6");
}

#[test]
fn test_no_ignore_mode_change_is_stale() {
    let dir = tempdir().unwrap();
    write_test_file(dir.path(), "a.txt", "hello\n");

    let index = TrigramIndex::build_with_options(dir.path(), false).unwrap();
    assert!(
        !index.is_stale(false),
        "same no_ignore mode should not be stale"
    );
    assert!(
        index.is_stale(true),
        "a query requesting a different no_ignore mode must be treated as stale"
    );

    let reason = index.staleness_reason(true).unwrap();
    assert!(
        reason.contains("no_ignore"),
        "staleness reason should name the no_ignore mismatch: reason={reason}"
    );
}

#[test]
fn test_m17_stored_canonical_root_matches_canonicalized_build_root() {
    // M17 (audit-m17): the identity a reuse compares is the CANONICALIZED build root,
    // persisted through save/load. Pre-fix the `canonical_root()` accessor does not
    // exist at all -- this test is a compile-time RED on the old code. Post-fix it
    // pins the canonical identity end to end.
    let dir = tempdir().unwrap();
    write_test_file(dir.path(), "a.txt", "hello world\n");

    let index = TrigramIndex::build(dir.path()).unwrap();
    let canonical = dir.path().canonicalize().unwrap();
    assert_eq!(
        index.canonical_root(),
        canonical.as_path(),
        "the stored identity must be the canonicalized build root"
    );

    // The persisted form round-trips: a reuse decision after load compares against it.
    let index_path = dir.path().join(".tg_index");
    index.save(&index_path).unwrap();
    let loaded = TrigramIndex::load(&index_path).unwrap();
    assert_eq!(
        loaded.canonical_root(),
        canonical.as_path(),
        "canonical root must survive the save/load round trip"
    );

    // A rebuild (incremental path) must re-record the identity of the tree it rebuilt
    // from, or the rebuilt index would refuse the very tree it just built.
    let updated = loaded
        .rebuild_incremental_with_options(dir.path(), false)
        .unwrap();
    assert_eq!(
        updated.index.canonical_root(),
        canonical.as_path(),
        "an incremental rebuild must re-record the canonical root"
    );
}

#[test]
fn test_m17_root_servability_refuses_different_tree_but_serves_same_tree() {
    // M17 (audit-m17) decision seam. Pre-fix `root_servability_reason` does not exist
    // -- the reuse path in main.rs has no root comparison at all, so calling it is a
    // compile-time RED on the old code (the strongest possible failure: the seam is a
    // structural absence). Post-fix:
    //   - a DIFFERENT tree's index must refuse to serve (caller rebuilds);
    //   - the SAME tree via the same spelling, and via the canonicalized spelling (the
    //     aliased-form control), must serve -- canonicalize-vs-canonicalize is what
    //     keeps legitimate re-spellings of one tree from looking like mismatches.
    let tree_a = tempdir().unwrap();
    let tree_b = tempdir().unwrap();
    write_test_file(tree_a.path(), "a.txt", "hello from tree A\n");
    write_test_file(tree_b.path(), "b.txt", "hello from tree B\n");

    let index = TrigramIndex::build(tree_a.path()).unwrap();

    assert!(
        index.root_servability_reason(tree_a.path()).is_none(),
        "same root, same spelling must serve"
    );
    assert!(
        index
            .root_servability_reason(&tree_a.path().canonicalize().unwrap())
            .is_none(),
        "the canonicalized spelling of the SAME tree must still serve (alias control)"
    );

    let reason = index
        .root_servability_reason(tree_b.path())
        .expect("a different tree's index must never serve");
    assert!(
        reason.contains("root mismatch"),
        "the refusal must disclose the rebuild reason: reason={reason}"
    );
}

#[test]
fn test_m17_empty_canonical_root_fails_closed() {
    // M17 (audit-m17): an index without a stored canonical root (the legacy JSON form
    // never persisted one) must refuse to serve rather than guess -- fail-closed
    // toward a rebuild.
    let dir = tempdir().unwrap();
    write_test_file(dir.path(), "a.txt", "hello world\n");
    let mut index = TrigramIndex::build(dir.path()).unwrap();
    index.canonical_root = PathBuf::new(); // same-module field access, test-only reach

    let reason = index
        .root_servability_reason(dir.path())
        .expect("an empty stored canonical root must refuse to serve");
    assert!(
        reason.contains("no stored canonical root"),
        "reason={reason}"
    );
}

#[test]
fn test_m17_f1_tree_fingerprint_detects_metadata_preserving_swap() {
    // M17 F1 (audit-m17 gate): per-file mtime/size checks cannot see a wholesale tree swap
    // at the SAME path whose names/sizes/mtimes are preserved -- the boundary check the
    // gate found missing. This test builds an index, then replaces every file with a
    // SAME-NAME, SAME-SIZE, SAME-MTIME, DIFFERENT-CONTENT version (the metadata-preserving
    // swap) and asserts staleness_reason reports the FINGERPRINT, not a file-level reason.
    //
    // Structural argument: the swap defeats the mtime/size loop BY CONSTRUCTION (equal
    // values), it leaves the file set exactly as indexed (no new/deleted names), so the
    // ONLY remaining detector is `tree_fingerprint`; the replacement content bytes differ,
    // so the SHA-256 digest differs. Pre-fix (gate's F1), no fingerprint existed and the
    // swap read as fresh -- the index served the old postings against the new tree.
    let dir = tempdir().unwrap();
    write_test_file(dir.path(), "a.txt", "hello world\n"); // 12 bytes
    write_test_file(dir.path(), "b.txt", "another line\n"); // 13 bytes
    let index = TrigramIndex::build(dir.path()).unwrap();
    assert!(
        index.staleness_reason(false).is_none(),
        "a fresh index must not be stale"
    );

    let mtime_a = fs::metadata(dir.path().join("a.txt"))
        .unwrap()
        .modified()
        .unwrap();
    let mtime_b = fs::metadata(dir.path().join("b.txt"))
        .unwrap()
        .modified()
        .unwrap();

    fs::remove_file(dir.path().join("a.txt")).unwrap();
    fs::remove_file(dir.path().join("b.txt")).unwrap();
    write_test_file(dir.path(), "a.txt", "swapped out\n"); // 12 bytes, different content
    write_test_file(dir.path(), "b.txt", "fresh sender\n"); // 13 bytes, different content
    set_modified_time(&dir.path().join("a.txt"), mtime_a);
    set_modified_time(&dir.path().join("b.txt"), mtime_b);

    let reason = index
        .staleness_reason(false)
        .expect("the metadata-preserving swap must be detected");
    assert!(
        reason.contains("fingerprint"),
        "the swap must be reported as a tree-identity change: reason={reason}"
    );
    assert!(
        !reason.contains("modified") && !reason.contains("deleted") && !reason.contains("new file"),
        "the per-file checks must not be the detector here (they were defeated by design): reason={reason}"
    );
}

#[test]
fn test_m17_f1_fingerprint_detects_change_beyond_4096_bytes() {
    // M17 F1 (gate round 2): the initial fingerprint sampled only the first 4 KiB of each
    // file, so a same-size/same-mtime edit PAST offset 4096 in a sampled file was
    // invisible to every check (per-file loop sees size/mtime only; the walk sees the
    // same names). With full-content hashing of the sampled files this closes: bytes
    // beyond 4096 are part of the digest.
    //
    // Structural argument: the first 4096 bytes are IDENTICAL (so the old 4 KiB sample
    // digest would have been unchanged -- the exact old evasion), size and mtime are
    // preserved (so the per-file loop passes), the file set is unchanged (so the walk
    // passes) -- only FULL-content hashing can see the tail change.
    let dir = tempdir().unwrap();
    let content_before = format!("{}OLD_TAIL_MARKER", "x".repeat(7000));
    let content_after = format!("{}NEW_TAIL_MARKER", "x".repeat(7000));
    assert_eq!(content_before.len(), content_after.len());

    write_test_file(dir.path(), "big.txt", &content_before);
    let index = TrigramIndex::build(dir.path()).unwrap();
    assert!(
        index.staleness_reason(false).is_none(),
        "a fresh index must not be stale"
    );

    let mtime = fs::metadata(dir.path().join("big.txt"))
        .unwrap()
        .modified()
        .unwrap();
    write_test_file(dir.path(), "big.txt", &content_after);
    set_modified_time(&dir.path().join("big.txt"), mtime);

    let reason = index
        .staleness_reason(false)
        .expect("a change beyond byte 4096 must be detected");
    assert!(
        reason.contains("fingerprint"),
        "the beyond-4096 change must be reported via the tree fingerprint: reason={reason}"
    );
    assert!(
        !reason.contains("modified") && !reason.contains("deleted") && !reason.contains("new file"),
        "size/mtime/name checks were all preserved by construction: reason={reason}"
    );
}

#[test]
fn test_m17_f1_fingerprint_slots_not_consumed_by_tg_index() {
    // M17 F1 (gate round 2): `.tg_index` must be excluded BEFORE the sampling cap --
    // if it counted toward the 32 sampled slots, an index persisted into a root with
    // exactly 32 other top-level entries would displace one REAL file from the sample,
    // so a change to that displaced file would evade the fingerprint. Structurally:
    // persistence happens after build, so the fingerprint computed at build (no
    // `.tg_index`) would be recomputed at staleness WITH `.tg_index` present; the
    // pre-cap exclusion makes both digest inputs identical.
    let dir = tempdir().unwrap();
    for i in 0..40 {
        write_test_file(dir.path(), &format!("f{i:03}.txt"), "same content\n");
    }
    let index = TrigramIndex::build(dir.path()).unwrap();
    assert!(
        index.staleness_reason(false).is_none(),
        "a fresh 40-file index must not be reported stale by its own persisted index file"
    );

    // Save into the tree, then re-check: the just-written `.tg_index` must not trip the
    // fingerprint (it is filtered before sampling, so the sampled set is unchanged).
    let index_path = dir.path().join(".tg_index");
    index.save(&index_path).unwrap();
    assert!(
        index.staleness_reason(false).is_none(),
        "the persisted .tg_index must not consume a fingerprint slot"
    );
}

#[test]
fn test_m17_f2_fingerprint_ignores_leftover_index_machinery_files() {
    // M17 F2 (gate round 3): the atomic-save temp namespace `..tg_index.<token>.tmp`
    // (`atomic_write_bytes`) and the write-lock file `..tg_index.lock`
    // (`index_lock::lock_path_for`) live in the index's own top-level namespace. A
    // leftover temp (crash between write and rename) or lock (hard crash) persists on
    // disk; WITHOUT exclusion it sorts first (`.` < `f`) and consumes one of the 32
    // sample slots, flipping the digest into a FALSE staleness transition on a healthy
    // tree. With exclusion the sampled set is unchanged.
    //
    // Structural argument: the artifacts are never indexed (so the per-file loop skips
    // them) and are `.`-hidden (so the new-file walk skips them) -- the fingerprint is
    // the ONLY check that could see them, and this test isolates exactly that surface.
    let dir = tempdir().unwrap();
    for i in 0..40 {
        write_test_file(dir.path(), &format!("f{i:03}.txt"), "same content\n");
    }
    let index = TrigramIndex::build(dir.path()).unwrap();
    let digest_before = compute_tree_fingerprint(dir.path(), false);

    // Fixture premise: the leftover artifacts must actually be visible to read_dir.
    // This is a read_dir enumeration, not a walk -- the walk-error-discard ratchet
    // (task #276) counts WALK sites, so use the non-ratcheted binding here to keep the
    // census at the audited walk sites only.
    write_test_file(dir.path(), "..tg_index.deadbeef.tmp", "crash leftover\n");
    write_test_file(dir.path(), "..tg_index.lock", "stale token\n");
    let names: Vec<String> = fs::read_dir(dir.path())
        .unwrap()
        .filter_map(|entry| entry.ok())
        .map(|entry| entry.file_name().to_string_lossy().into_owned())
        .collect();
    assert!(
        names.iter().any(|n| n.starts_with("..tg_index.")),
        "fixture premise: the leftover temp/lock must be present in the top-level listing"
    );

    assert_eq!(
        compute_tree_fingerprint(dir.path(), false),
        digest_before,
        "the leftover index-machinery files must not change the fingerprint digest"
    );
    assert!(
        index.staleness_reason(false).is_none(),
        "a leftover atomic-save temp or lock must not produce a false stale transition"
    );
}

#[test]
fn fingerprint_ignores_top_level_directories() {
    // M17 round-3 (codex audit): the fingerprint must select FILES first -- a top-level
    // directory added after build must neither flip the digest (the walks only ever see
    // files) nor consume one of the 32 sampled slots (which would displace a real file
    // and weaken F1 coverage). The symlink variant is Unix-gated separately below.
    let dir = tempdir().unwrap();
    write_test_file(dir.path(), "keep.txt", "kept\n");
    let index = TrigramIndex::build(dir.path()).unwrap();
    let digest_before = compute_tree_fingerprint(dir.path(), false);

    fs::create_dir(dir.path().join("added_dir")).unwrap();
    assert_eq!(
        compute_tree_fingerprint(dir.path(), false),
        digest_before,
        "an added empty directory must not change the fingerprint"
    );
    assert!(
        index.staleness_reason(false).is_none(),
        "an added empty directory must not trigger staleness"
    );
}

#[test]
fn fingerprint_cap_not_consumed_by_early_sorting_directories() {
    // M17 round-3 (codex audit): 32 directories that sort before the sampled files must
    // not displace every real file from the 32-slot representative sample -- otherwise a
    // metadata-preserving swap in the displaced file would evade the fingerprint.
    // The directories sort FIRST (adir* < zzz_target.txt), so the pre-fix fingerprint
    // (raw path sort + take(32)) would sample the 32 dirs and drop the real file --
    // making this test genuinely RED on the pre-fix code.
    let dir = tempdir().unwrap();
    for i in 0..32 {
        fs::create_dir(dir.path().join(format!("adir{i:02}"))).unwrap();
    }
    write_test_file(dir.path(), "zzz_target.txt", "before\n");
    let index = TrigramIndex::build(dir.path()).unwrap();
    assert!(index.staleness_reason(false).is_none());

    // Metadata-preserving swap on the only real file: same size, same mtime.
    let mtime = fs::metadata(dir.path().join("zzz_target.txt"))
        .unwrap()
        .modified()
        .unwrap();
    write_test_file(dir.path(), "zzz_target.txt", "after!\n"); // 7 bytes, same as "before\n"
    set_modified_time(&dir.path().join("zzz_target.txt"), mtime);

    let reason = index
        .staleness_reason(false)
        .expect("the swap must be detected via the fingerprint");
    assert!(reason.contains("fingerprint"), "reason={reason}");
}

#[cfg(unix)]
#[test]
fn fingerprint_ignores_top_level_symlink() {
    // M17 round-3 (codex audit): a top-level symlink must not flip the fingerprint (the
    // walks only ever yield files). Unix-gated: creating a symlink needs privileges on
    // Windows CI, so the symlink arm only runs where std::os::unix::fs::symlink exists.
    use std::os::unix::fs::symlink;
    let dir = tempdir().unwrap();
    write_test_file(dir.path(), "keep.txt", "kept\n");
    let index = TrigramIndex::build(dir.path()).unwrap();
    let digest_before = compute_tree_fingerprint(dir.path(), false);

    symlink(dir.path().join("keep.txt"), dir.path().join("link.txt")).unwrap();
    assert_eq!(
        compute_tree_fingerprint(dir.path(), false),
        digest_before,
        "an added top-level symlink must not change the fingerprint"
    );
    assert!(
        index.staleness_reason(false).is_none(),
        "an added top-level symlink must not trigger staleness"
    );
}

#[test]
fn test_m17_f2_entries_relative_and_deref_through_canonical_root() {
    // M17 F2 (audit-m17 gate): entries must be stored canonical-root-RELATIVE and every
    // result path must dereference through the verified canonical root. This is the
    // invariant that makes the cross-cwd escape structurally impossible: nothing in the
    // index is ever a cwd-dependent spelling, so no query process can re-root a stored
    // path at its own working directory.
    //
    // M17 F3 (gate round 2): DEREFERENCE is canonical (asserted below); DISPLAY is a
    // separate contract -- `display_path` re-projects through the QUERY's original
    // spelling so relative queries see relative output (asserted at the end).
    let dir = tempdir().unwrap();
    write_test_file(dir.path(), "a.txt", "hello world\n");
    fs::create_dir(dir.path().join("sub")).unwrap();
    write_test_file(dir.path(), "sub/b.txt", "nested content\n");

    let index = TrigramIndex::build(dir.path()).unwrap();
    let canonical = dir.path().canonicalize().unwrap();

    assert!(
        index.files.iter().all(|entry| entry.path.is_relative()),
        "entries must be stored canonical-root-relative; got {:?}",
        index
            .files
            .iter()
            .map(|e| e.path.display().to_string())
            .collect::<Vec<_>>()
    );
    assert_eq!(
        index.root(),
        index.canonical_root(),
        "the build-spelling root is retired; a built index's root IS its canonical root"
    );

    let results = index.search("hello", false, true).unwrap();
    assert_eq!(results.len(), 1, "deref must find the real file content");
    for result in &results {
        assert!(
            result.file.is_absolute(),
            "search must dereference canonically: {}",
            result.file.display()
        );
        assert!(
            result.file.starts_with(&canonical),
            "search results must be rooted at the canonical root: {} vs {}",
            result.file.display(),
            canonical.display()
        );
    }

    // M17 F3: the DISPLAY projection uses the QUERY's spelling while reads stay
    // canonical -- a query typed as a relative `tree` emits `tree/a.txt`, not the
    // canonical absolute path.
    let displayed = index.display_path(Path::new("tree"), &results[0].file);
    assert!(
        displayed.is_relative(),
        "display must re-project through the query spelling: {}",
        displayed.display()
    );
    assert_eq!(
        displayed,
        Path::new("tree").join("a.txt"),
        "display = query_spelling.join(rel)"
    );
    // The same spelling-but-different-casing/full query form still emits the user's form.
    let displayed_abs = index.display_path(Path::new("TREE"), &results[0].file);
    assert_eq!(
        displayed_abs,
        Path::new("TREE").join("a.txt"),
        "display preserves the caller's spelling even when it differs from canonical"
    );
    // Dereference is untouched by the display projection.
    assert_eq!(
        index.deref_path(Path::new("a.txt")),
        index.canonical_root().join("a.txt")
    );

    // Round trip: a loaded index dereferences identically (no stored spelling survives).
    let index_path = dir.path().join(".tg_index");
    index.save(&index_path).unwrap();
    let loaded = TrigramIndex::load(&index_path).unwrap();
    let loaded_results = loaded.search("hello", false, true).unwrap();
    assert_eq!(loaded_results[0].file, results[0].file);
    assert!(loaded.files.iter().all(|entry| entry.path.is_relative()));
}

#[test]
fn test_m17_f3_uncanonicalizable_query_root_is_unconditional_refusal() {
    // M17 F3 (audit-m17 gate): a query root that cannot be canonicalized must be refused
    // UNCONDITIONALLY -- the earlier raw-spelling fallback comparison would let an
    // unverifiable spelling pass "by coincidence". A nonexistent path cannot be
    // canonicalized on any platform.
    let dir = tempdir().unwrap();
    write_test_file(dir.path(), "a.txt", "hello world\n");
    let index = TrigramIndex::build(dir.path()).unwrap();

    let missing = dir.path().join("does-not-exist");
    let reason = index
        .root_servability_reason(&missing)
        .expect("an uncanonicalizable query root must never serve");
    assert!(
        reason.contains("cannot be canonicalized"),
        "the refusal must name the canonicalize failure: reason={reason}"
    );
}

#[test]
fn test_m17_f4_legacy_json_loaded_index_is_not_searchable() {
    // M17 F4 (audit-m17 gate): `load_json` returns an index with NO verified canonical
    // root; a library consumer must not be able to search it directly, bypassing
    // `root_servability_reason`. The public serving surface refuses with an error.
    let dir = tempdir().unwrap();
    write_test_file(dir.path(), "a.txt", "hello world\n");
    let index = TrigramIndex::build(dir.path()).unwrap();
    let json_path = dir.path().join("legacy.json");
    index.save_json(&json_path).unwrap();
    let legacy = TrigramIndex::load_json(&json_path).unwrap();

    let err = legacy.search("hello", false, true).unwrap_err();
    assert!(
        err.to_string().contains("no verified root"),
        "search must refuse an unverified index: {err}"
    );
    assert!(
        legacy.query_candidates_checked("hello", false).is_err(),
        "the checked candidate surface must refuse an unverified index"
    );
    assert!(
        legacy.query_candidates("hello", false).is_empty(),
        "the legacy compatibility wrapper degrades to an empty candidate set (documented)"
    );
    assert!(
        legacy.root_servability_reason(dir.path()).is_some(),
        "root_servability already refuses the legacy form"
    );
}

#[test]
fn test_m17_f5_non_utf8_canonical_root_fails_load_closed() {
    // M17 F5 (audit-m17 gate): to_string_lossy/from_utf8_lossy collapse DISTINCT non-UTF-8
    // paths into one identity (the alias collision). Build rejects non-UTF-8 roots (the
    // build-side arm; a non-UTF-8 tempdir is not portable to create), and the load side
    // rejects a hand-crafted wire format whose canonical root bytes are invalid UTF-8 --
    // fail closed in both directions, never a lossy identity.
    let dir = tempdir().unwrap();
    let index_path = dir.path().join("crafted.tg_index");
    let (mut buf, root_pos) = craft_v6_index_header(1);
    buf[root_pos] = 0xFF; // invalid UTF-8 canonical root byte
    fs::write(&index_path, &buf).unwrap();
    let err = TrigramIndex::load(&index_path).unwrap_err();
    assert!(
        err.to_string().contains("not valid UTF-8"),
        "a lossy identity must never be accepted: {err}"
    );
}

/// Crafts the header of a v6 index file with ONE declared file entry. Returns
/// `(buffer, canonical_root_bytes_pos)`; the buffer is
/// magic + version + no_ignore + canonical_root_len + canonical_root("X")
/// + tree_fingerprint(0) + files_count(1), with the entry payload appended separately.
fn craft_v6_index_header(canonical_root_len: u32) -> (Vec<u8>, usize) {
    let mut buf = Vec::new();
    buf.extend_from_slice(INDEX_MAGIC);
    buf.push(INDEX_FORMAT_VERSION);
    buf.push(0); // no_ignore
    buf.extend_from_slice(&canonical_root_len.to_le_bytes());
    let root_pos = buf.len();
    buf.extend_from_slice(b"X"); // canonical root placeholder byte
    buf.extend_from_slice(&0u64.to_le_bytes()); // tree_fingerprint = 0
    buf.extend_from_slice(&1u32.to_le_bytes()); // files_count = 1
    (buf, root_pos)
}

/// Appends one file entry (path + mtime + size + deleted) to a crafted v6 buffer.
fn append_crafted_entry(buf: &mut Vec<u8>, path_bytes: &[u8]) {
    buf.extend_from_slice(&(path_bytes.len() as u32).to_le_bytes());
    buf.extend_from_slice(path_bytes);
    buf.extend_from_slice(&0u128.to_le_bytes()); // mtime_ns
    buf.extend_from_slice(&0u64.to_le_bytes()); // size
    buf.push(0); // deleted
}

#[test]
fn test_m17_f2_load_rejects_unconfined_entry_paths() {
    // M17 F2 (gate round 2): loaded entries must be strictly relative and confined --
    // absolute, prefix, and `..` paths must REJECT the whole index so
    // `canonical_root.join(rel)` is provably inside the verified root. The per-entry
    // decode is also STRICT UTF-8 (round-2 F5 extension for entry paths).
    let dir = tempdir().unwrap();
    let index_path = dir.path().join("crafted.tg_index");

    // (a) non-UTF-8 entry name: reject, never a lossy decode.
    let (mut buf, _) = craft_v6_index_header(1);
    append_crafted_entry(&mut buf, &[0xFF]);
    fs::write(&index_path, &buf).unwrap();
    let err = TrigramIndex::load(&index_path).unwrap_err();
    assert!(
        err.to_string().contains("not valid UTF-8"),
        "a non-UTF-8 entry name must reject the index: {err}"
    );

    // (b) absolute/rooted entry path: reject (join would escape the canonical root by root).
    // Note: on Windows `/etc/passwd` is rooted-but-"relative" (no drive prefix), so the
    // refusal can fire on EITHER check -- accept both stable halves of the message.
    let (mut buf, _) = craft_v6_index_header(1);
    append_crafted_entry(&mut buf, b"/etc/passwd");
    fs::write(&index_path, &buf).unwrap();
    let err = TrigramIndex::load(&index_path).unwrap_err().to_string();
    assert!(
        err.contains("not relative") || err.contains("absolute component"),
        "a rooted/absolute entry must reject the index: {err}"
    );

    // (c) `..` escape component: reject.
    let (mut buf, _) = craft_v6_index_header(1);
    append_crafted_entry(&mut buf, b"../outside.txt");
    fs::write(&index_path, &buf).unwrap();
    let err = TrigramIndex::load(&index_path).unwrap_err();
    assert!(
        err.to_string().contains("escapes the canonical root"),
        "a `..` entry must reject the index: {err}"
    );

    // (d) a confined relative entry loads fine (the control arm), with the empty
    // postings section (trigram_count = 0) that the success path requires.
    let (mut buf, _) = craft_v6_index_header(1);
    append_crafted_entry(&mut buf, b"sub/a.txt");
    buf.extend_from_slice(&0u32.to_le_bytes()); // trigram_count = 0
    fs::write(&index_path, &buf).unwrap();
    let loaded = TrigramIndex::load(&index_path).unwrap();
    assert_eq!(loaded.file_count(), 1);
    assert_eq!(loaded.files[0].path, PathBuf::from("sub/a.txt"));
}

#[test]
fn test_compressed_index_is_at_least_40_percent_smaller_than_legacy_format_on_1000_files() {
    let dir = tempdir().unwrap();
    write_size_reduction_corpus(dir.path(), 1000);

    let index = TrigramIndex::build(dir.path()).unwrap();
    let legacy = serialize_legacy_v1(&index);
    let compressed = bincode_serialize(&index).unwrap();

    assert!(
        compressed.len() * 100 <= legacy.len() * 60,
        "expected compressed index to be >= 40% smaller than legacy format; compressed={} legacy={}",
        compressed.len(),
        legacy.len()
    );
}

#[test]
fn test_load_rejects_bad_magic() {
    let dir = tempdir().unwrap();
    let index_path = dir.path().join(".tg_index");
    fs::write(&index_path, b"BADMAGIC").unwrap();

    let result = TrigramIndex::load(&index_path);
    assert!(result.is_err());
    let err = result.unwrap_err().to_string();
    assert!(err.contains("magic"), "err={err}");
}

#[test]
fn test_load_rejects_future_version() {
    let dir = tempdir().unwrap();
    write_test_file(dir.path(), "a.txt", "hello\n");
    let index = TrigramIndex::build(dir.path()).unwrap();
    let index_path = dir.path().join(".tg_index");
    index.save(&index_path).unwrap();

    let mut data = fs::read(&index_path).unwrap();
    data[4] = 99;
    fs::write(&index_path, &data).unwrap();

    let result = TrigramIndex::load(&index_path);
    assert!(result.is_err());
    let err = result.unwrap_err().to_string();
    assert!(err.contains("version"), "err={err}");
}

#[test]
fn test_load_rejects_truncated_file() {
    let dir = tempdir().unwrap();
    let index_path = dir.path().join(".tg_index");
    fs::write(&index_path, b"TGI").unwrap();

    let result = TrigramIndex::load(&index_path);
    assert!(result.is_err());
}

#[test]
fn test_rebuild_after_staleness_produces_correct_results() {
    let dir = tempdir().unwrap();
    write_test_file(dir.path(), "a.txt", "hello world\n");
    let index1 = TrigramIndex::build(dir.path()).unwrap();
    let r1 = index1.search("hello", false, true).unwrap();
    assert_eq!(r1.len(), 1);

    std::thread::sleep(std::time::Duration::from_millis(50));
    write_test_file(dir.path(), "a.txt", "goodbye world\n");
    assert!(index1.is_stale(false));

    let index2 = TrigramIndex::build(dir.path()).unwrap();
    let r2_hello = index2.search("hello", false, true).unwrap();
    assert!(
        r2_hello.is_empty(),
        "old content should not match after rebuild"
    );
    let r2_goodbye = index2.search("goodbye", false, true).unwrap();
    assert_eq!(r2_goodbye.len(), 1);
}

#[test]
fn test_incremental_update_detects_file_addition_and_reuses_unchanged_files() {
    let dir = tempdir().unwrap();
    write_test_file(dir.path(), "a.txt", "alpha keep\nshared term\n");
    write_test_file(dir.path(), "b.txt", "beta keep\nshared term\n");

    let index = TrigramIndex::build(dir.path()).unwrap();

    std::thread::sleep(std::time::Duration::from_millis(50));
    write_test_file(dir.path(), "c.txt", "gamma addition\nshared term\n");

    let update = index
        .rebuild_incremental_with_options(dir.path(), false)
        .unwrap();
    assert_eq!(update.stats.added_files, 1);
    assert_eq!(update.stats.modified_files, 0);
    assert_eq!(update.stats.deleted_files, 0);
    assert_eq!(update.stats.reused_files, 2);

    let results = update.index.search("gamma addition", false, true).unwrap();
    assert_eq!(results.len(), 1);
    assert!(results[0].file.ends_with("c.txt"));

    let preserved = update.index.search("alpha keep", false, true).unwrap();
    assert_eq!(preserved.len(), 1);
    assert!(preserved[0].file.ends_with("a.txt"));
}

#[test]
fn test_incremental_update_detects_file_removal_and_drops_stale_entries() {
    let dir = tempdir().unwrap();
    write_test_file(dir.path(), "a.txt", "alpha keep\nshared term\n");
    write_test_file(dir.path(), "b.txt", "remove only needle\nshared term\n");

    let index = TrigramIndex::build(dir.path()).unwrap();

    std::thread::sleep(std::time::Duration::from_millis(50));
    fs::remove_file(dir.path().join("b.txt")).unwrap();

    let update = index
        .rebuild_incremental_with_options(dir.path(), false)
        .unwrap();
    assert_eq!(update.stats.added_files, 0);
    assert_eq!(update.stats.modified_files, 0);
    assert_eq!(update.stats.deleted_files, 1);
    assert_eq!(update.stats.reused_files, 1);

    let removed = update
        .index
        .search("remove only needle", false, true)
        .unwrap();
    assert!(
        removed.is_empty(),
        "removed file content should disappear from the index"
    );

    let preserved = update.index.search("alpha keep", false, true).unwrap();
    assert_eq!(preserved.len(), 1);
    assert!(preserved[0].file.ends_with("a.txt"));
}

#[test]
fn test_incremental_update_detects_file_modification_and_reindexes_only_changed_file() {
    let dir = tempdir().unwrap();
    write_test_file(dir.path(), "a.txt", "old needle\nshared term\n");
    write_test_file(dir.path(), "b.txt", "preserved needle\nshared term\n");

    let index = TrigramIndex::build(dir.path()).unwrap();

    std::thread::sleep(std::time::Duration::from_millis(50));
    write_test_file(dir.path(), "a.txt", "new needle\nshared term\n");

    let update = index
        .rebuild_incremental_with_options(dir.path(), false)
        .unwrap();
    assert_eq!(update.stats.added_files, 0);
    assert_eq!(update.stats.modified_files, 1);
    assert_eq!(update.stats.deleted_files, 0);
    assert_eq!(update.stats.reused_files, 1);

    let old_results = update.index.search("old needle", false, true).unwrap();
    assert!(
        old_results.is_empty(),
        "stale postings for modified files should be removed"
    );

    let new_results = update.index.search("new needle", false, true).unwrap();
    assert_eq!(new_results.len(), 1);
    assert!(new_results[0].file.ends_with("a.txt"));

    let preserved = update
        .index
        .search("preserved needle", false, true)
        .unwrap();
    assert_eq!(preserved.len(), 1);
    assert!(preserved[0].file.ends_with("b.txt"));
}

#[test]
fn test_incremental_update_handles_mixed_changes() {
    let dir = tempdir().unwrap();
    write_test_file(dir.path(), "a.txt", "alpha original\nshared term\n");
    write_test_file(dir.path(), "b.txt", "beta remove\nshared term\n");
    write_test_file(dir.path(), "c.txt", "gamma keep\nshared term\n");

    let index = TrigramIndex::build(dir.path()).unwrap();

    std::thread::sleep(std::time::Duration::from_millis(50));
    write_test_file(dir.path(), "a.txt", "alpha updated\nshared term\n");
    fs::remove_file(dir.path().join("b.txt")).unwrap();
    write_test_file(dir.path(), "d.txt", "delta added\nshared term\n");

    let update = index
        .rebuild_incremental_with_options(dir.path(), false)
        .unwrap();
    assert_eq!(update.stats.added_files, 1);
    assert_eq!(update.stats.modified_files, 1);
    assert_eq!(update.stats.deleted_files, 1);
    assert_eq!(update.stats.reused_files, 1);

    assert!(update
        .index
        .search("beta remove", false, true)
        .unwrap()
        .is_empty());

    let updated = update.index.search("alpha updated", false, true).unwrap();
    assert_eq!(updated.len(), 1);
    assert!(updated[0].file.ends_with("a.txt"));

    let added = update.index.search("delta added", false, true).unwrap();
    assert_eq!(added.len(), 1);
    assert!(added[0].file.ends_with("d.txt"));

    let preserved = update.index.search("gamma keep", false, true).unwrap();
    assert_eq!(preserved.len(), 1);
    assert!(preserved[0].file.ends_with("c.txt"));
}

// -- #127: index-build silently no-ops a root .gitignore outside a git repo ------------
//
// Both index-build WalkBuilders (collect_file_entries + staleness_reason's new-file scan)
// set `.git_ignore(!no_ignore)` but never called `.add_ignore(..)`. The `ignore` crate only
// auto-discovers per-directory `.gitignore` files once it has detected an actual git repo
// (a `.git`/`.jj` marker in some ancestor); outside one, `.git_ignore(true)` alone is a
// no-op and gitignored files leak into the index. Fix: mirror the sibling `add_ignore` trio
// already used by `tg search`'s own walkers (main.rs / native_search.rs) -- explicitly
// added ignore files are honored by the `ignore` crate unconditionally, git repo or not.
// Deliberately NOT `.require_git(false)`: that would additionally pull in nested/global
// gitignores outside git, diverging from the root-only add_ignore behavior of `tg search`
// (BACKLOG #127).

fn names_of(entries: &[FileEntry]) -> Vec<String> {
    entries
        .iter()
        .map(|e| e.path.file_name().unwrap().to_string_lossy().into_owned())
        .collect()
}

#[test]
fn collect_file_entries_honors_root_gitignore_outside_git_repo() {
    let dir = tempdir().unwrap();
    assert!(
        !dir.path().join(".git").exists(),
        "sanity: a bare tempdir must not already look like a git repo"
    );
    write_test_file(dir.path(), ".gitignore", "ignoreme.py\n");
    write_test_file(dir.path(), "ignoreme.py", "excluded\n");
    write_test_file(dir.path(), "keep.py", "kept\n");

    let names = names_of(&collect_file_entries(dir.path(), false));

    assert!(
        !names.contains(&"ignoreme.py".to_string()),
        "root .gitignore must be honored outside a git repo: names={names:?}"
    );
    assert!(
        names.contains(&"keep.py".to_string()),
        "non-ignored files must still be indexed: names={names:?}"
    );
}

#[test]
fn collect_file_entries_honors_root_gitignore_inside_git_repo() {
    // Positive control: must stay green both before and after the fix. Inside a git repo,
    // .gitignore was already honored via the `ignore` crate's native git-repo
    // auto-discovery. Mirrors the crate's own test-suite idiom of a bare `mkdirp(.git)`
    // marker (dir.rs) rather than a real `git init` -- the crate detects "is a repo" purely
    // by the existence of a `.git`/`.jj` entry, not by its contents.
    let dir = tempdir().unwrap();
    fs::create_dir(dir.path().join(".git")).unwrap();
    write_test_file(dir.path(), ".gitignore", "ignoreme.py\n");
    write_test_file(dir.path(), "ignoreme.py", "excluded\n");
    write_test_file(dir.path(), "keep.py", "kept\n");

    let names = names_of(&collect_file_entries(dir.path(), false));

    assert!(
        !names.contains(&"ignoreme.py".to_string()),
        "root .gitignore must be honored inside a git repo: names={names:?}"
    );
    assert!(
        names.contains(&"keep.py".to_string()),
        "non-ignored files must still be indexed: names={names:?}"
    );
}

#[test]
fn collect_file_entries_no_ignore_still_includes_gitignored_file_outside_git_repo() {
    // --no-ignore must keep overriding gitignore entirely (unchanged behavior) -- the fix
    // must gate the new add_ignore loop on `!no_ignore`, not add it unconditionally.
    let dir = tempdir().unwrap();
    write_test_file(dir.path(), ".gitignore", "ignoreme.py\n");
    write_test_file(dir.path(), "ignoreme.py", "excluded\n");

    let names = names_of(&collect_file_entries(dir.path(), true));

    assert!(
        names.contains(&"ignoreme.py".to_string()),
        "--no-ignore must still include the gitignored file: names={names:?}"
    );
}

#[test]
fn staleness_new_file_scan_honors_root_gitignore_outside_git_repo() {
    // Sibling site: staleness_reason's own WalkBuilder (the new-file scan) must not
    // disagree with collect_file_entries -- a gitignored new file must not be reported as
    // "new" (and therefore must not force a rebuild) outside a git repo either.
    let dir = tempdir().unwrap();
    write_test_file(dir.path(), ".gitignore", "ignoreme.py\n");
    write_test_file(dir.path(), "keep.py", "kept\n");
    let index = TrigramIndex::build_with_options(dir.path(), false).unwrap();
    assert!(index.staleness_reason(false).is_none());

    write_test_file(dir.path(), "ignoreme.py", "should stay invisible\n");
    assert!(
        index.staleness_reason(false).is_none(),
        "a gitignored new file must not trigger staleness outside a git repo"
    );
}

#[test]
fn staleness_new_file_scan_honors_root_gitignore_inside_git_repo() {
    // Positive control for the new-file-scan site: must stay green before and after.
    let dir = tempdir().unwrap();
    fs::create_dir(dir.path().join(".git")).unwrap();
    write_test_file(dir.path(), ".gitignore", "ignoreme.py\n");
    write_test_file(dir.path(), "keep.py", "kept\n");
    let index = TrigramIndex::build_with_options(dir.path(), false).unwrap();
    assert!(index.staleness_reason(false).is_none());

    write_test_file(dir.path(), "ignoreme.py", "should stay invisible\n");
    assert!(
        index.staleness_reason(false).is_none(),
        "a gitignored new file must not trigger staleness inside a git repo either"
    );
}

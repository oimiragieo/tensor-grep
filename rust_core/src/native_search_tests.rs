use super::*;

// --- Task #276: the --json envelope must ADMIT an incomplete walk -----------------------
//
// The plan's SS6 demands a bidirectional oracle, and this branch is the argument for it: an
// earlier commit here shipped a `Drop` fast path that silently dropped `walk_errors`, which
// no amount of diff-reading caught. The CONTROL arm below is the load-bearing half -- it
// fails on the pre-B2 tree, where the keys could not be emitted at all.

fn envelope_for(stats: SearchStats) -> serde_json::Value {
    let buffer = Arc::new(Mutex::new(Vec::new()));
    let config = NativeSearchConfig {
        output_target: NativeOutputTarget::Buffer(Arc::clone(&buffer)),
        ..NativeSearchConfig::default()
    };
    emit_json_matches(&config, &stats).expect("emit_json_matches must succeed");
    let bytes = buffer.lock().expect("buffer lock").clone();
    serde_json::from_slice(&bytes).expect("envelope must be valid JSON")
}

#[test]
fn json_envelope_admits_an_incomplete_walk() {
    // TREATMENT: the walk skipped something, so the envelope must say so -- and say it in
    // the vocabulary the Python routes already emit (json_fmt.py:127/:140), not a synonym.
    let envelope = envelope_for(SearchStats {
        walk_errors: 2,
        ..SearchStats::default()
    });
    assert_eq!(envelope["result_incomplete"], serde_json::json!(true));
    assert_eq!(
        envelope["incomplete_reason_class"],
        serde_json::json!("unreadable_path")
    );
    assert_eq!(envelope["incomplete_paths_count"], serde_json::json!(2));
}

#[test]
fn json_envelope_is_byte_identical_when_the_walk_was_complete() {
    // CONTROL -- the arm that makes the pair mean anything. All three keys must be ABSENT,
    // not present-and-false: `skip_serializing_if` is what keeps a complete envelope
    // byte-identical to every prior release, and a `false`/`null` would be a new key on the
    // happy path, breaking the additive-by-construction promise B2 makes.
    //
    // If this ever passes with the keys present, the fix has become a shape change and the
    // rg byte-fidelity gate (TG_REQUIRE_RG_PARITY) is the next thing to go red.
    let envelope = envelope_for(SearchStats::default());
    assert!(
        envelope.get("result_incomplete").is_none(),
        "a COMPLETE walk must not carry result_incomplete: {envelope}"
    );
    assert!(
        envelope.get("incomplete_reason_class").is_none(),
        "a COMPLETE walk must not carry incomplete_reason_class: {envelope}"
    );
    assert!(
        envelope.get("incomplete_paths_count").is_none(),
        "a COMPLETE walk must not carry incomplete_paths_count: {envelope}"
    );
}

// --- Task #26: the --json envelope must NAME the scope a zero-result search covered -------
//
// The v1.101.22 dogfood: "PATH note is stderr-only -- bare `--json` still returns empty
// aggregate JSON with no warnings/notes field; agents that ignore stderr can miss it."
//
// `defaulted_scope_fields` has THREE inputs' worth of behaviour (implicit x matches), and the
// arms below cover all of it. That matters more than usual here: this exact symptom has taken
// four separate fixes because each one closed the one route that happened to be reported, so
// a test that only exercises the treatment arm would look like coverage and be sampling.

fn envelope_for_scope(
    path_was_implicit: bool,
    matches: Vec<NativeSearchMatch>,
) -> serde_json::Value {
    let buffer = Arc::new(Mutex::new(Vec::new()));
    let config = NativeSearchConfig {
        output_target: NativeOutputTarget::Buffer(Arc::clone(&buffer)),
        path_was_implicit,
        ..NativeSearchConfig::default()
    };
    let total_matches = matches.len();
    let stats = SearchStats {
        total_matches,
        matches,
        ..SearchStats::default()
    };
    emit_json_matches(&config, &stats).expect("emit_json_matches must succeed");
    let bytes = buffer.lock().expect("buffer lock").clone();
    serde_json::from_slice(&bytes).expect("envelope must be valid JSON")
}

fn one_match() -> Vec<NativeSearchMatch> {
    vec![NativeSearchMatch {
        path: PathBuf::from("a.rs"),
        line_number: Some(1),
        raw: b"needle".to_vec(),
    }]
}

#[test]
fn json_envelope_names_the_scope_when_a_defaulted_search_found_nothing() {
    // TREATMENT. This is the only combination that carries information: the caller did not
    // choose the scope AND the answer was empty, so "empty" may be an artefact of the scope
    // rather than a fact about the repository.
    let envelope = envelope_for_scope(true, Vec::new());
    assert_eq!(envelope["path_was_defaulted"], serde_json::json!(true));
    assert_eq!(
        envelope["scope_note"],
        serde_json::json!(DEFAULTED_SCOPE_NOTE),
        "the envelope must carry the SHARED note text, not a local paraphrase"
    );
}

#[test]
fn json_envelope_stays_silent_when_the_caller_chose_the_scope() {
    // CONTROL ARM 1. An explicit PATH that found nothing is an authoritative zero -- the
    // caller asked exactly this question and got the answer. Annotating it would be noise,
    // and it would also break the byte-identical promise for every existing consumer that
    // passes a PATH (which is the documented, recommended usage).
    let envelope = envelope_for_scope(false, Vec::new());
    assert!(
        envelope.get("path_was_defaulted").is_none(),
        "an explicitly-scoped search must not carry path_was_defaulted: {envelope}"
    );
    assert!(
        envelope.get("scope_note").is_none(),
        "an explicitly-scoped search must not carry scope_note: {envelope}"
    );
}

#[test]
fn json_envelope_stays_silent_when_a_defaulted_search_found_something() {
    // CONTROL ARM 2, and the one that keeps the field worth reading. Without it, gating on
    // `path_was_implicit` alone would pass the treatment test while stamping the note onto
    // the overwhelmingly common case -- a successful bare search -- which trains every
    // consumer to ignore the key and puts us back where the dogfood started.
    let envelope = envelope_for_scope(true, one_match());
    assert_eq!(envelope["total_matches"], serde_json::json!(1));
    assert!(
        envelope.get("path_was_defaulted").is_none(),
        "a defaulted search that FOUND matches must not carry the note: {envelope}"
    );
    assert!(
        envelope.get("scope_note").is_none(),
        "a defaulted search that FOUND matches must not carry scope_note: {envelope}"
    );
}

#[test]
fn defaulted_scope_fields_is_gated_on_both_inputs() {
    // The helper itself, exhaustively -- the envelope tests above go through
    // `emit_json_matches`, so a bug in the gate could in principle be masked by the payload
    // builder. Four combinations, one truth table, no sampling.
    assert_eq!(
        defaulted_scope_fields(true, 0),
        (Some(true), Some(DEFAULTED_SCOPE_NOTE))
    );
    assert_eq!(defaulted_scope_fields(true, 1), (None, None));
    assert_eq!(defaulted_scope_fields(false, 0), (None, None));
    assert_eq!(defaulted_scope_fields(false, 1), (None, None));
}

fn worker_for(shared: &Arc<Mutex<SearchStats>>) -> ParallelWalkWorker {
    let config = Arc::new(NativeSearchConfig {
        pattern: "needle".to_string(),
        ..NativeSearchConfig::default()
    });
    ParallelWalkWorker::new(config, Arc::clone(shared)).expect("worker must build")
}

#[test]
fn drop_merges_a_worker_whose_only_contribution_is_walk_errors() {
    // Regression guard for the defect this branch itself introduced. `Drop` carries a
    // "nothing to contribute, skip the lock" fast path that predates `walk_errors`. Under
    // build_parallel() a worker can legitimately be handed ONLY unreadable entries: it
    // searches no files and matches nothing, so every counter in the old guard is zero and
    // `std::mem::take` never ran -- the count vanished and the envelope reported a COMPLETE
    // scan of an INCOMPLETE walk, which is the exact defect #276 exists to fix.
    //
    // Driven through a REAL drop rather than by asserting the guard's boolean, so it stays
    // honest if the short-circuit is ever restructured.
    let shared = Arc::new(Mutex::new(SearchStats::default()));
    {
        let mut worker = worker_for(&shared);
        worker.local_stats.walk_errors = 3;
    }
    assert_eq!(
        shared.lock().expect("shared lock").walk_errors,
        3,
        "a walk-error-only worker must still merge on drop"
    );
}

#[test]
fn drop_still_skips_the_lock_for_a_genuinely_empty_worker() {
    // The guard's other side. Without this, "delete the fast path entirely" would pass the
    // test above -- so the pair, not either test alone, pins where the boundary sits.
    let shared = Arc::new(Mutex::new(SearchStats::default()));
    drop(worker_for(&shared));
    assert_eq!(
        *shared.lock().expect("shared lock"),
        SearchStats::default(),
        "an empty worker must contribute nothing"
    );
}

/// Task 319: every countable field must make `is_empty()` false on its own.
///
/// This asserts the INVARIANT that keeps `ParallelWalkWorker::drop`'s fast path honest,
/// rather than staging a worker in a state production cannot reach. The guard used to
/// enumerate five of six fields inline; a sixth field added to the struct and not to
/// `is_empty` fails here.
///
/// Deliberately NOT written as "a binary-match-only worker is dropped": that state is
/// unreachable in production, because every production writer of `binary_match_files` is
/// preceded by `searched_files += 1` (:544/:556, :1121/:1133, and `merge_search_stats` at
/// :1348/:1352). A test that reaches it only by assigning the field directly would go
/// red-then-green while proving nothing about production -- the discrimination failure this
/// codebase keeps re-learning.
///
/// This test DOES assign fields directly, and that is legitimate precisely because it claims
/// to test `is_empty`'s contract, not to reproduce a production state.
#[test]
fn search_stats_is_empty_covers_every_countable_field() {
    assert!(
        SearchStats::default().is_empty(),
        "a fresh SearchStats must be empty"
    );

    let mutators: [(&str, fn(&mut SearchStats)); 7] = [
        ("searched_files", |s| s.searched_files = 1),
        ("matched_files", |s| s.matched_files = 1),
        ("total_matches", |s| s.total_matches = 1),
        ("skipped_binary_files", |s| s.skipped_binary_files = 1),
        ("binary_match_files", |s| s.binary_match_files = 1),
        // Added when this branch rebased onto task 276 slice A (#795), which introduced
        // `walk_errors` as the 7th countable field. Without this row the table would still
        // pass -- it would simply never check the one field whose loss is a SILENT
        // INCOMPLETE SCAN rather than a miscount, which is the whole point of task 276.
        // An enumeration that grows only when someone remembers is the drift this test
        // exists to stop, so the arity is pinned at 7 and the compiler enforces it.
        ("walk_errors", |s| s.walk_errors = 1),
        // Constructed explicitly: NativeSearchMatch does NOT derive Default (:43), so
        // `::default()` would not compile. Verified by reading the derive list rather than
        // assumed -- Rust here is CI-only, so a wrong constructor costs a whole cycle.
        ("matches", |s| {
            s.matches.push(NativeSearchMatch {
                path: PathBuf::from("a.rs"),
                line_number: Some(1),
                raw: b"hit".to_vec(),
            })
        }),
    ];

    for (field, mutate) in mutators {
        let mut stats = SearchStats::default();
        mutate(&mut stats);
        assert!(
            !stats.is_empty(),
            "SearchStats::is_empty() ignores `{field}` -- a worker whose only contribution \
             is that field would be dropped without merging. Add the field to is_empty()."
        );
    }
}

// --- Audit #105: native-CPU implicit-walk-ceiling gate ----------------------------------
// Mirrors rg_passthrough.rs's audit #100 test suite for `check_implicit_walk_ceiling`. #100
// hoisted a walk-ceiling gate into `execute_ripgrep_search` (the rg-passthrough engine) but
// left `run_native_search` (reached via `--json`, `--force-cpu`, single-pattern
// `--fixed-strings`, and rg-unavailable routing) with NO ceiling at all -- `NativeSearchConfig`
// did not even have a `path_was_implicit` field, so a bare implicit-path search on a huge
// root walked unbounded through `search_walk_roots_parallel`/`collect_walked_files`.

fn make_stub_file_dir(dir: &Path, file_count: usize) {
    for index in 0..file_count {
        fs::write(
            dir.join(format!("stub_{index}.py")),
            "nothing interesting\n",
        )
        .unwrap();
    }
}

fn config_with_paths(paths: Vec<PathBuf>, path_was_implicit: bool) -> NativeSearchConfig {
    NativeSearchConfig {
        pattern: "TODO".to_string(),
        paths,
        path_was_implicit,
        ..NativeSearchConfig::default()
    }
}

#[test]
fn check_native_implicit_walk_ceiling_refuses_oversized_implicit_walk() {
    // RED-before-fix: this is the exact shape of the #105 bypass -- an implicit-path search
    // (no explicit PATH positional) on a root over the 1500-file ceiling.
    let dir = tempfile::tempdir().unwrap();
    make_stub_file_dir(dir.path(), 1600);
    let roots = vec![dir.path().to_path_buf()];
    let config = config_with_paths(roots.clone(), true);

    let refusal = check_native_implicit_walk_ceiling(&config, &roots);

    assert!(
        refusal.is_some(),
        "an oversized implicit-path walk must be refused"
    );
}

#[test]
fn check_native_implicit_walk_ceiling_allows_explicit_path_even_when_oversized() {
    // Non-regression (Trap #3 parity, mirrors rg_passthrough.rs): an EXPLICIT,
    // deliberately-scoped PATH must never be refused regardless of size.
    let dir = tempfile::tempdir().unwrap();
    make_stub_file_dir(dir.path(), 1600);
    let roots = vec![dir.path().to_path_buf()];
    let config = config_with_paths(roots.clone(), false);

    let refusal = check_native_implicit_walk_ceiling(&config, &roots);

    assert!(
        refusal.is_none(),
        "an explicit path must run uninhibited even when the walk exceeds the ceiling"
    );
}

#[test]
fn check_native_implicit_walk_ceiling_allows_implicit_path_under_ceiling() {
    // Normal-case non-regression: an implicit path under the ceiling is unaffected -- a
    // typical repo must never be refused.
    let dir = tempfile::tempdir().unwrap();
    make_stub_file_dir(dir.path(), 50);
    let roots = vec![dir.path().to_path_buf()];
    let config = config_with_paths(roots.clone(), true);

    let refusal = check_native_implicit_walk_ceiling(&config, &roots);

    assert!(
        refusal.is_none(),
        "a 50-file implicit root must not be refused"
    );
}

#[test]
fn run_native_search_refuses_oversized_implicit_walk_before_enumerating() {
    // Hermetic end-to-end test of the actual `run_native_search` entry point the #105 audit
    // named. Bounded per anti-hang-test-protocol: run on a joined worker thread with an
    // explicit timeout so a regression (the gate silently stops firing, or stops running
    // before the real walk) that falls through to the unbounded parallel walk cannot hang
    // the test runner -- it fails fast with a clear panic message instead.
    let dir = tempfile::tempdir().unwrap();
    make_stub_file_dir(dir.path(), 1600);
    let config = config_with_paths(vec![dir.path().to_path_buf()], true);

    let (tx, rx) = std::sync::mpsc::channel();
    std::thread::spawn(move || {
        let result = run_native_search(config).map_err(|error| error.to_string());
        let _ = tx.send(result);
    });
    let result = rx.recv_timeout(std::time::Duration::from_secs(10)).expect(
        "run_native_search must return well within 10s -- a hang here means the \
         walk-ceiling gate did not fire before an unbounded parallel walk",
    );

    let err = result.expect_err("an oversized implicit-path walk must be refused, not Ok");
    assert!(
        crate::rg_passthrough::is_unbounded_implicit_search_walk_refusal(&err),
        "unexpected error (expected the walk-ceiling refusal): {err}"
    );
}

#[test]
fn run_native_search_does_not_refuse_explicit_oversized_path() {
    // Non-regression: an explicit PATH (even oversized) must complete normally, not be
    // refused -- fail-open for explicit scoping is the whole point of the guard (Trap #3
    // parity). Bounded per anti-hang-test-protocol.
    let dir = tempfile::tempdir().unwrap();
    make_stub_file_dir(dir.path(), 1600);
    let config = config_with_paths(vec![dir.path().to_path_buf()], false);

    let (tx, rx) = std::sync::mpsc::channel();
    std::thread::spawn(move || {
        let result = run_native_search(config).map_err(|error| error.to_string());
        let _ = tx.send(result);
    });
    let result = rx
        .recv_timeout(std::time::Duration::from_secs(20))
        .expect("run_native_search must return well within 20s for an explicit path");

    result.expect("an explicit oversized path must not be refused");
}

// --- Task #267: `--no-ignore-vcs` must not be dropped by the structured-output route -----
// Before this field existed, `NativeSearchConfig` had no `no_ignore_vcs` at all, so
// `build_walk_builder` unconditionally added a root `.gitignore` to the walker whenever
// `no_ignore` was false -- REGARDLESS of `--no-ignore-vcs`. Since this engine is exactly the
// one `--json`/`--ndjson` route to (`route_search`'s `structured_output` arm,
// `RoutingDecision::native_cpu_json`), a bare output-format flag silently changed the file
// set: `tg search --no-ignore-vcs PATTERN .` correctly re-included a `.gitignore`-matched
// file via the rg-passthrough engine, but `tg search --json --no-ignore-vcs PATTERN .`
// silently kept excluding it. Live-binary repro (task, not this test): the published
// v1.98.8 CLI (`tg-windows-amd64-cpu.exe`, which already carries the same
// `build_walk_builder` body -- unchanged since v1.98.3, verified via `git diff`) reproduces
// both directions: `--no-ignore-vcs` alone re-includes the `.gitignore`-matched file
// (`routing_backend=RipgrepBackend`), while `--json --no-ignore-vcs` returns the exact same
// 2-file set as bare `--json` (`routing_backend=NativeCpuBackend`) -- the divergence this
// test locks shut at the unit level. `--no-ignore` (the blanket disable) was NOT affected --
// `build_walk_builder` already threaded that field correctly -- only the VCS-scoped flag was
// dropped, so a non-regression case for `--no-ignore` is included too.

fn write_fixture_file(dir: &Path, relative: &str, contents: &str) {
    let path = dir.join(relative);
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).unwrap();
    }
    fs::write(path, contents).unwrap();
}

fn walked_file_names(dir: &Path, config: &NativeSearchConfig) -> Vec<String> {
    let roots = vec![dir.to_path_buf()];
    let mut names: Vec<String> = collect_walked_files(config, &roots)
        .expect("collect_walked_files must not error on a small fixture dir")
        .files
        .iter()
        .filter_map(|path| {
            path.file_name()
                .map(|name| name.to_string_lossy().into_owned())
        })
        .collect();
    names.sort();
    names
}

/// Task 276 slice B3 (task 315). BIDIRECTIONAL BY CONSTRUCTION -- both arms run in THIS
/// test, in this process:
///   ARM B (control): a fully readable tree MUST report `walk_errors == 0`.
///   ARM A: a tree with an unreadable subdirectory MUST report `walk_errors >= 1`.
/// Either arm alone is not verification. ARM A alone would pass against a counter wired to
/// a constant; ARM B alone would pass against the pre-slice-B3 code, which had no counter
/// at all and let the error die at the `eprintln!`.
///
/// Unix-only: this needs a directory the walker genuinely cannot read, and Windows ACL
/// denial is not reachable through `PermissionsExt`. On GitHub-hosted ubuntu/macos runners
/// the job user is NOT root, so ARM A executes there -- if this test ever prints the
/// root-skip below on hosted CI, the runner image changed and the arm has gone inert.
#[cfg(unix)]
#[test]
fn collect_walked_files_counts_an_unreadable_dir_and_reports_zero_on_a_clean_tree() {
    use std::os::unix::fs::PermissionsExt;

    // ARM B (control) first, so a failure here reads as "the counter is always hot" rather
    // than as a missing error in ARM A.
    let clean = tempfile::tempdir().unwrap();
    write_fixture_file(clean.path(), "readable.txt", "sentinel\n");
    let clean_config = config_with_paths(vec![clean.path().to_path_buf()], false);
    let clean_roots = vec![clean.path().to_path_buf()];
    let clean_walk = collect_walked_files(&clean_config, &clean_roots)
        .expect("a readable fixture tree must not error");
    assert_eq!(
        clean_walk.walk_errors, 0,
        "a fully readable tree must report ZERO walk errors; a non-zero count here means \
         the counter fires on something other than an unreadable entry, and ARM A below \
         would prove nothing"
    );

    // ARM A.
    let dir = tempfile::tempdir().unwrap();
    write_fixture_file(dir.path(), "top.txt", "sentinel\n");
    let locked = dir.path().join("locked");
    fs::create_dir_all(&locked).unwrap();
    fs::write(locked.join("hidden.txt"), "sentinel\n").unwrap();
    fs::set_permissions(&locked, fs::Permissions::from_mode(0o000)).unwrap();

    // PREMISE: the setup must actually deny THIS process. Under root (some container
    // images) the mode bits are ignored, the walk is never obstructed, and asserting on it
    // would be an inert check wearing a green badge. Restore and bail loudly instead.
    if fs::read_dir(&locked).is_ok() {
        fs::set_permissions(&locked, fs::Permissions::from_mode(0o755)).unwrap();
        eprintln!(
            "SKIP unreadable-dir arm: mode 0o000 did not deny this process (running as \
             root?), so the walk would not have been obstructed"
        );
        return;
    }

    let config = config_with_paths(vec![dir.path().to_path_buf()], false);
    let roots = vec![dir.path().to_path_buf()];
    let walked = collect_walked_files(&config, &roots);

    // Restore before asserting, so a failing assertion does not also leak an undeletable
    // temp directory into the runner.
    fs::set_permissions(&locked, fs::Permissions::from_mode(0o755)).unwrap();

    let walked = walked.expect("an unreadable subdirectory must degrade the walk, not abort it");
    assert!(
        walked.walk_errors >= 1,
        "an unreadable subdirectory must be COUNTED so the `--json` envelope can mark the \
         result incomplete (:2489); got walk_errors={}",
        walked.walk_errors
    );
    assert!(
        walked
            .files
            .iter()
            .any(|path| path.file_name().is_some_and(|name| name == "top.txt")),
        "the readable sibling must still be returned -- the contract is keep-partial, not \
         abort-on-first-error"
    );
}

#[test]
fn build_walk_builder_honors_root_gitignore_by_default() {
    // Non-regression: the pre-existing default behavior (no `--no-ignore-vcs`) must keep
    // excluding a `.gitignore`-matched file, exactly like the rg-passthrough engine does.
    let dir = tempfile::tempdir().unwrap();
    write_fixture_file(dir.path(), ".gitignore", "*.log\n");
    write_fixture_file(dir.path(), "keep.txt", "sentinel\n");
    write_fixture_file(dir.path(), "ignored.log", "sentinel\n");
    let config = config_with_paths(vec![dir.path().to_path_buf()], false);

    let names = walked_file_names(dir.path(), &config);

    assert_eq!(
        names,
        vec!["keep.txt".to_string()],
        "default routing (no_ignore_vcs=false) must still exclude the .gitignore-matched file"
    );
}

#[test]
fn build_walk_builder_no_ignore_vcs_reincludes_gitignore_matched_file() {
    // RED-before-fix (structural, not executed -- see the section header above for the
    // live-binary repro that establishes the failing direction: cargo is forbidden on this
    // box, see AGENTS.md CPU-SAFE). Before the `no_ignore_vcs` field and this
    // `if ignore_name == ".gitignore" && config.no_ignore_vcs { continue; }` guard existed,
    // `build_walk_builder` had no way to read this flag at all and would have kept
    // `ignored.log` OUT of the walk -- this assertion would fail against that code.
    let dir = tempfile::tempdir().unwrap();
    write_fixture_file(dir.path(), ".gitignore", "*.log\n");
    write_fixture_file(dir.path(), "keep.txt", "sentinel\n");
    write_fixture_file(dir.path(), "ignored.log", "sentinel\n");
    let mut config = config_with_paths(vec![dir.path().to_path_buf()], false);
    config.no_ignore_vcs = true;

    let names = walked_file_names(dir.path(), &config);

    assert_eq!(
        names,
        vec!["ignored.log".to_string(), "keep.txt".to_string()],
        "--no-ignore-vcs must re-include the .gitignore-matched file on the SAME engine \
         --json/--ndjson route to -- an output-format flag must never change the file set"
    );
}

#[test]
fn build_walk_builder_no_ignore_vcs_does_not_affect_dot_ignore_file() {
    // Scope check (mirrors rg_passthrough.rs's `root_ignore_file_args_no_ignore_vcs_skips_
    // only_gitignore`): rg's own docs restrict `--no-ignore-vcs` to source-control ignore
    // files. A `.ignore`-matched file must stay excluded even when `no_ignore_vcs` is set --
    // only `.gitignore` is in scope for this flag.
    let dir = tempfile::tempdir().unwrap();
    write_fixture_file(dir.path(), ".ignore", "*.dat\n");
    write_fixture_file(dir.path(), "keep.txt", "sentinel\n");
    write_fixture_file(dir.path(), "ignored.dat", "sentinel\n");
    let mut config = config_with_paths(vec![dir.path().to_path_buf()], false);
    config.no_ignore_vcs = true;

    let names = walked_file_names(dir.path(), &config);

    assert_eq!(
        names,
        vec!["keep.txt".to_string()],
        "--no-ignore-vcs must not resurrect a .ignore-matched file -- only .gitignore is \
         VCS-scoped"
    );
}

#[test]
fn build_walk_builder_no_ignore_still_overrides_no_ignore_vcs() {
    // Non-regression: the blanket `--no-ignore` disable (already correctly threaded before
    // this fix) must keep working unchanged when combined with `no_ignore_vcs`.
    let dir = tempfile::tempdir().unwrap();
    write_fixture_file(dir.path(), ".gitignore", "*.log\n");
    write_fixture_file(dir.path(), "keep.txt", "sentinel\n");
    write_fixture_file(dir.path(), "ignored.log", "sentinel\n");
    let mut config = config_with_paths(vec![dir.path().to_path_buf()], false);
    config.no_ignore = true;
    config.no_ignore_vcs = true;

    let names = walked_file_names(dir.path(), &config);

    assert_eq!(
        names,
        vec!["ignored.log".to_string(), "keep.txt".to_string()],
        "--no-ignore must still disable all ignore-file honoring regardless of no_ignore_vcs"
    );
}

// --- Task #267 BLOCKING-1 (independent gate on the first cut): the git-repo case --------
// The 4 tests above all use a bare `tempfile::tempdir()` -- never a git repository -- so
// `WalkBuilder`'s own `require_git(true)`-gated git machinery stays dormant for all of them
// and `add_ignore` is the ONLY mechanism exercised. That topology cannot distinguish "the
// fix works" from "the fix's filename guard happens to be a no-op here" -- inside a git
// repo, `WalkBuilder`'s native `git_ignore`/`git_global`/`git_exclude` knobs (all `true` by
// default) already apply the root `.gitignore` on their own, so skipping `add_ignore(
// ".gitignore")` changes nothing unless those knobs are ALSO flipped. These two tests use a
// git-repo topology instead: a root `.gitignore`, a `.git` marker directory (sufficient for
// the `ignore` crate's own repo-root detection), and the search ROOT set to a child `pkg/`
// directory that carries no ignore files of its own -- so `add_ignore` (which only ever
// joins `root.join(ignore_name)` for the exact search root it is given) can never see the
// parent `.gitignore` at all, and any exclusion observed here MUST come from the walker's
// own native git machinery. This isolates the mechanism the first cut's fix omitted.

fn write_git_marker(dir: &Path) {
    fs::create_dir(dir.join(".git")).unwrap();
}

#[test]
fn build_walk_builder_honors_root_gitignore_inside_git_repo_via_native_git_path() {
    // Non-regression / mechanism-isolation: proves the native git path is live for a child
    // dir with no ignore files of its own (the exact topology the bug-fix test below
    // reuses), independent of `add_ignore` (which can only ever see `pkg/` itself, never the
    // parent `.gitignore`).
    let dir = tempfile::tempdir().unwrap();
    write_git_marker(dir.path());
    write_fixture_file(dir.path(), ".gitignore", "*.log\n");
    write_fixture_file(dir.path(), "pkg/keep.txt", "sentinel\n");
    write_fixture_file(dir.path(), "pkg/ignored.log", "sentinel\n");
    let config = config_with_paths(vec![dir.path().join("pkg")], false);

    let names = walked_file_names(&dir.path().join("pkg"), &config);

    assert_eq!(
        names,
        vec!["keep.txt".to_string()],
        "default routing inside a git repo must exclude the git-ignored file via the \
         walker's OWN git machinery, not add_ignore (pkg/ has no ignore files of its own)"
    );
}

#[test]
fn build_walk_builder_no_ignore_vcs_reincludes_gitignore_matched_file_inside_git_repo() {
    // RED-before-BLOCKING-1-fix (structural, not executed -- cargo forbidden on this box,
    // see AGENTS.md CPU-SAFE): before `git_ignore(false)`/`git_global(false)`/
    // `git_exclude(false)` were added to the `config.no_ignore_vcs` branch, this exact
    // scenario returned only `["keep.txt"]` -- the `.gitignore` skip in the `add_ignore`
    // loop is a no-op here (pkg/ has no `.gitignore` of its own to skip), so the walker's
    // OWN git-aware gitignore machinery was the only thing excluding `ignored.log`, and
    // nothing in the first cut of this fix touched it. Live-binary repro of the identical
    // shape (git repo, root `.gitignore`, child dir with no ignore files) is in the task
    // record for this fix.
    let dir = tempfile::tempdir().unwrap();
    write_git_marker(dir.path());
    write_fixture_file(dir.path(), ".gitignore", "*.log\n");
    write_fixture_file(dir.path(), "pkg/keep.txt", "sentinel\n");
    write_fixture_file(dir.path(), "pkg/ignored.log", "sentinel\n");
    let mut config = config_with_paths(vec![dir.path().join("pkg")], false);
    config.no_ignore_vcs = true;

    let names = walked_file_names(&dir.path().join("pkg"), &config);

    assert_eq!(
        names,
        vec!["ignored.log".to_string(), "keep.txt".to_string()],
        "--no-ignore-vcs must re-include the git-ignored file INSIDE a git repo too -- the \
         native git_ignore/git_global/git_exclude knobs must be disabled, not just the \
         add_ignore filename skip"
    );
}

// --- Chunk-parallel binary detection parity ---------------------------------------------
// `search_file_chunk_parallel` used to hardcode `binary_detected: false` unconditionally in
// both its --count and match-collecting branches, bypassing the binary detection the serial
// (non-chunked) path performs via `BinaryAwareSink` + `build_searcher`'s
// `BinaryDetection::quit(b'\x00')`. A binary file above the chunk-parallel threshold would
// fall through to the parallel per-chunk scan and emit raw byte "matches" (mojibake) instead
// of being flagged/skipped like the serial path. These tests force the real multi-chunk
// branch (`chunk_parallelism_threads: Some(4)` over a newline-rich fixture, sanity-checked via
// `plan_file_chunks`) and assert parity against the serial leaf functions the fix mirrors
// (`search_file_collect_matches_with_searcher` / `search_file_count_with_searcher`).

fn force_multi_chunk_config(pattern: &str, count: bool) -> NativeSearchConfig {
    NativeSearchConfig {
        pattern: pattern.to_string(),
        chunk_parallelism_threads: Some(4),
        count,
        ..NativeSearchConfig::default()
    }
}

fn write_fixture(dir: &Path, name: &str, content: &[u8]) -> PathBuf {
    let path = dir.join(name);
    fs::write(&path, content).unwrap();
    path
}

/// Text content only (no NUL byte anywhere), but large/newline-rich enough that
/// `chunk_parallelism_threads: Some(4)` plans more than one chunk. Every line contains
/// `needle` exactly once.
fn multi_chunk_text_fixture(needle: &str) -> Vec<u8> {
    let mut content = Vec::new();
    for i in 0..1200 {
        content.extend_from_slice(format!("filler line {i:05} of {needle} data\n").as_bytes());
    }
    content
}

/// Same shape as `multi_chunk_text_fixture`, but with a run of NUL bytes spliced into the
/// middle -- binary content, still comfortably within the 64 KiB guaranteed-detection prefix
/// (`BINARY_DETECTION_PREFIX_BYTES`) so both the serial and chunk-parallel paths are expected
/// to detect it. Embeds `needle` in the surrounding text (same as `multi_chunk_text_fixture`)
/// on purpose: if a regression silently stops flagging this content as binary, the pattern
/// still lexically occurs on every line, so the old hardcoded `binary_detected: false` code
/// path would report 1200 spurious mojibake matches here -- not a vacuous `match_count == 0`
/// that would hold either way regardless of whether detection actually ran.
fn multi_chunk_binary_fixture(needle: &str) -> Vec<u8> {
    let mut content = Vec::new();
    for i in 0..1200 {
        content.extend_from_slice(format!("filler line {i:05} of {needle} data\n").as_bytes());
    }
    let splice_at = content.len() / 2;
    content.splice(splice_at..splice_at, std::iter::repeat(0u8).take(16));
    content
}

/// Sanity precondition shared by the parity tests below: confirms the fixture actually forces
/// the real multi-chunk branch under test. Without this, a future change to the fixture size
/// or `plan_file_chunks`'s alignment could silently degrade these tests into only exercising
/// the `chunk_plan.len() <= 1` fallback (which was never buggy) instead of the parallel
/// fan-out this bug lived in.
fn assert_forces_multi_chunk(config: &NativeSearchConfig, content: &[u8]) {
    let requested_chunks = configured_chunk_parallelism_threads(config);
    let chunk_plan = plan_file_chunks(content, requested_chunks, config.count);
    assert!(
        chunk_plan.len() > 1,
        "fixture must produce multiple chunks to exercise the parallel branch, got {}",
        chunk_plan.len()
    );
}

#[test]
fn search_file_chunk_parallel_flags_binary_content_like_the_serial_path() {
    let dir = tempfile::tempdir().unwrap();
    let content = multi_chunk_binary_fixture("payload");
    let path = write_fixture(dir.path(), "binary.dat", &content);
    let config = force_multi_chunk_config("payload", false);
    let matcher = build_matcher(&config).unwrap();
    assert_forces_multi_chunk(&config, &content);

    let chunk_parallel_result = search_file_chunk_parallel(&config, &matcher, &path).unwrap();
    let mut serial_searcher = build_searcher(&config, true);
    let serial_result =
        search_file_collect_matches_with_searcher(&config, &matcher, &path, &mut serial_searcher)
            .unwrap();

    assert!(
        chunk_parallel_result.binary_detected,
        "a binary file above the chunk-parallel threshold must be flagged binary, not \
         silently searched for raw-byte matches"
    );
    assert_eq!(
        chunk_parallel_result.binary_detected, serial_result.binary_detected,
        "chunk-parallel binary_detected must match the serial path for identical content"
    );
    assert_eq!(
        chunk_parallel_result.binary_match_detected, serial_result.binary_match_detected,
        "chunk-parallel binary_match_detected must match the serial path"
    );
    assert_eq!(chunk_parallel_result.match_count, 0);
    assert!(chunk_parallel_result.matches.is_empty());
    assert_eq!(chunk_parallel_result.match_count, serial_result.match_count);
}

#[test]
fn search_file_chunk_parallel_count_mode_flags_binary_content_like_the_serial_path() {
    let dir = tempfile::tempdir().unwrap();
    let content = multi_chunk_binary_fixture("payload");
    let path = write_fixture(dir.path(), "binary_count.dat", &content);
    let config = force_multi_chunk_config("payload", true);
    let matcher = build_matcher(&config).unwrap();
    assert_forces_multi_chunk(&config, &content);

    let chunk_parallel_result = search_file_chunk_parallel(&config, &matcher, &path).unwrap();
    let mut serial_searcher = build_searcher(&config, true);
    let serial_result =
        search_file_count_with_searcher(&matcher, &path, &mut serial_searcher).unwrap();

    assert!(
        chunk_parallel_result.binary_detected,
        "--count mode must also flag a binary file above the chunk-parallel threshold"
    );
    assert_eq!(
        chunk_parallel_result.binary_detected, serial_result.binary_detected,
        "chunk-parallel binary_detected must match the serial --count path"
    );
    assert_eq!(chunk_parallel_result.match_count, 0);
    assert_eq!(chunk_parallel_result.match_count, serial_result.match_count);
}

#[test]
fn search_file_chunk_parallel_matches_text_content_unchanged() {
    let dir = tempfile::tempdir().unwrap();
    let content = multi_chunk_text_fixture("payload");
    let path = write_fixture(dir.path(), "text.txt", &content);
    let config = force_multi_chunk_config("payload", false);
    let matcher = build_matcher(&config).unwrap();
    assert_forces_multi_chunk(&config, &content);

    let chunk_parallel_result = search_file_chunk_parallel(&config, &matcher, &path).unwrap();
    let mut serial_searcher = build_searcher(&config, true);
    let serial_result =
        search_file_collect_matches_with_searcher(&config, &matcher, &path, &mut serial_searcher)
            .unwrap();

    assert!(
        !chunk_parallel_result.binary_detected,
        "a plain text file must never be flagged binary"
    );
    assert_eq!(chunk_parallel_result.match_count, 1200);
    assert_eq!(
        chunk_parallel_result.match_count, serial_result.match_count,
        "chunk-parallel match_count must match the serial path for identical text content"
    );
    assert_eq!(
        chunk_parallel_result.matches.len(),
        serial_result.matches.len()
    );
}

#[test]
fn detect_binary_prefix_finds_nul_byte_within_the_guaranteed_prefix() {
    let config = NativeSearchConfig::default();
    let mut contents = vec![b'a'; 100];
    contents[42] = 0u8;

    assert_eq!(detect_binary_prefix(&config, &contents), Some(42));
}

#[test]
fn detect_binary_prefix_returns_none_under_text_mode_even_with_a_nul_byte() {
    let config = NativeSearchConfig {
        text: true,
        ..NativeSearchConfig::default()
    };
    let mut contents = vec![b'a'; 100];
    contents[42] = 0u8;

    assert_eq!(
        detect_binary_prefix(&config, &contents),
        None,
        "--text must disable binary detection entirely, mirroring BinaryDetection::none()"
    );
}

#[test]
fn detect_binary_prefix_does_not_scan_past_the_guaranteed_prefix() {
    // Documents the intentional parity limit with grep_searcher's own guaranteed floor for
    // mmap-backed binary detection (`BinaryDetection::quit`'s docs): only the fixed-size
    // prefix at the beginning of the contents is guaranteed to be scanned. A NUL byte placed
    // past that prefix must not be detected by this helper -- scanning further would make the
    // chunk-parallel path MORE aggressive than the serial path for the same content, which is
    // its own divergent-detection bug.
    let config = NativeSearchConfig::default();
    let mut contents = vec![b'a'; BINARY_DETECTION_PREFIX_BYTES + 10];
    contents[BINARY_DETECTION_PREFIX_BYTES + 5] = 0u8;

    assert_eq!(detect_binary_prefix(&config, &contents), None);
}

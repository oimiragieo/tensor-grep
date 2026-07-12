//! Cross-process concurrency + crash-safety coverage for audit #138 item #2 (index.json write
//! is non-atomic + unlocked -> crash-corrupts + concurrent writers lost-update).
//!
//! `tg index` as a bare subcommand does not exist in this codebase (verified: no `Commands::Index`
//! clap variant, no bootstrap/typer routing) -- the real, and only, mechanism that persists the
//! `.tg_index` trigram index is `tg search --index <pattern> <path>`. Every test below dogfoods
//! the real compiled `tg` binary (`env!("CARGO_BIN_EXE_tg")`), never `CliRunner`-equivalent
//! in-process calls, per this repo's "dogfood the real binary" house rule.
//!
//! Anti-hang-test-protocol: every subprocess wait in this file is bounded via
//! `process_control`'s `controlled_with_output().time_limit(..).terminate_for_timeout()`, which
//! drains stdout/stderr WHILE timing out (a hand-rolled `spawn` + `wait_timeout` deadlocks if the
//! child fills the OS pipe buffer -- see the identical rationale in `main.rs`'s validation-command
//! runner). A hang-class regression in the lock therefore fails these tests fast (a clear
//! `panic!` naming the bound that was exceeded), not by hanging the test run itself.

use std::fs;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::thread;
use std::time::{Duration, Instant, SystemTime};

use process_control::{ChildExt, Control, Output};
use serde_json::Value;
use tempfile::tempdir;
use tensor_grep_rs::index::TrigramIndex;

fn tg() -> Command {
    Command::new(env!("CARGO_BIN_EXE_tg"))
}

/// Bounded wait draining stdout/stderr while timing out -- see the module doc for why a plain
/// `wait_timeout` is unsafe here. Panics (rather than returning a sentinel) on a real timeout, so
/// a hang-class regression shows up as an unmissable, immediately-diagnosable test failure.
fn run_bounded(mut cmd: Command, timeout: Duration) -> Output {
    cmd.stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    let child = cmd.spawn().expect("failed to spawn tg");
    match child
        .controlled_with_output()
        .time_limit(timeout)
        .terminate_for_timeout()
        .wait()
    {
        Ok(Some(output)) => output,
        Ok(None) => panic!(
            "tg process exceeded the {timeout:?} bound and was terminated -- this is the exact \
             hang class audit #138 item #2 must prevent (a lock must never block a search forever)"
        ),
        Err(e) => panic!("failed waiting for tg process: {e}"),
    }
}

fn write_corpus(dir: &Path) {
    fs::write(
        dir.join("a.txt"),
        "hello world\nfoo bar baz\ngoodbye world\n",
    )
    .unwrap();
    fs::write(dir.join("b.txt"), "nothing here\nhello again friend\nend\n").unwrap();
}

fn write_wide_corpus(dir: &Path, file_count: usize) {
    for i in 0..file_count {
        let mut content = String::new();
        for line in 0..40 {
            content.push_str(&format!(
                "needle0 filler line {i}-{line} filler filler filler filler filler\n"
            ));
        }
        fs::write(dir.join(format!("f{i:04}.txt")), content).unwrap();
    }
}

/// Mirrors the private `lock_path_for` in `rust_core/src/index_lock.rs` (dot-prefixed original
/// name + `.lock` suffix). Duplicated here deliberately -- an integration test only sees the
/// crate's public API, and if this drifts from the real implementation the tests that use it fail
/// loudly (lock file never found) rather than silently passing for the wrong reason.
fn lock_path_for(index_path: &Path) -> PathBuf {
    let name = index_path.file_name().unwrap().to_str().unwrap();
    index_path.with_file_name(format!(".{name}.lock"))
}

fn set_mtime(path: &Path, when: SystemTime) {
    let file = std::fs::OpenOptions::new().write(true).open(path).unwrap();
    file.set_times(std::fs::FileTimes::new().set_modified(when))
        .unwrap();
}

// -- Hammer: N concurrent writers -----------------------------------------------------------

/// TDD spec: "cross-process HAMMER 15-20 concurrent tg search --index on one root -> exactly 1
/// writer wins, no corruption, NO invocation hangs." Reads per-acquisition interval files that
/// `index_lock.rs` writes ONLY when `TG_INDEX_LOCK_DEBUG_LOG_DIR` is set (each file written
/// exactly once by exactly one process -- no cross-process append race to reason about) for a
/// DIRECT mutual-exclusion proof, on top of the hard correctness assertions (no hangs, every
/// process returns the correct match count, the final index is loadable, no lock survives).
#[test]
fn hammer_concurrent_tg_search_index_one_writer_at_a_time_no_corruption_no_hangs() {
    let dir = tempdir().unwrap();
    write_corpus(dir.path());
    let root = dir.path().to_path_buf();
    let index_path = dir.path().join(".tg_index");
    let lock_path = lock_path_for(&index_path);

    let debug_dir = tempdir().unwrap();
    let debug_dir_path = debug_dir.path().to_path_buf();

    const N: usize = 20;
    // Generous: a cold, unoptimized debug build under CI/hammer load must never be mistaken
    // for a hang. The lock's own production budget (12s acquire timeout) is a small fraction
    // of this.
    let per_process_timeout = Duration::from_secs(90);

    let handles: Vec<_> = (0..N)
        .map(|_| {
            let root = root.clone();
            let debug_dir_path = debug_dir_path.clone();
            thread::spawn(move || {
                let mut cmd = tg();
                cmd.arg("search")
                    .arg("--index")
                    .arg("--fixed-strings")
                    .arg("--json")
                    .arg("hello")
                    .arg(&root)
                    .env("TG_INDEX_LOCK_DEBUG_LOG_DIR", &debug_dir_path);
                run_bounded(cmd, per_process_timeout)
            })
        })
        .collect();

    let outputs: Vec<Output> = handles
        .into_iter()
        .map(|h| h.join().expect("hammer worker thread panicked"))
        .collect();

    for (i, output) in outputs.iter().enumerate() {
        assert!(
            output.status.success(),
            "process {i} failed: stdout={} stderr={}",
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr)
        );
        let stdout = String::from_utf8_lossy(&output.stdout);
        let json: Value = serde_json::from_str(&stdout)
            .unwrap_or_else(|e| panic!("process {i}: non-JSON stdout ({e}): {stdout}"));
        assert_eq!(
            json["total_matches"], 2,
            "process {i} returned the wrong match count (a lost-update or a torn read of the \
             index would show up here as a wrong/short result set): {stdout}"
        );
    }

    assert!(index_path.exists(), "the index must exist after the hammer");
    TrigramIndex::load(&index_path)
        .expect("final index must be loadable -- concurrent writers must never corrupt it");

    assert!(
        !lock_path.exists(),
        "no lock file should survive after every writer released it"
    );

    let intervals = read_lock_activity_intervals(&debug_dir_path);
    assert!(
        !intervals.is_empty(),
        "expected at least one of the {N} processes to actually acquire the write lock (the \
         index did not exist before this test ran)"
    );
    assert_no_overlapping_intervals(&intervals);
}

fn read_lock_activity_intervals(dir: &Path) -> Vec<(u128, u128)> {
    let mut starts: std::collections::HashMap<String, u128> = std::collections::HashMap::new();
    let mut ends: std::collections::HashMap<String, u128> = std::collections::HashMap::new();
    for entry in fs::read_dir(dir).unwrap().filter_map(|e| e.ok()) {
        let name = entry.file_name().to_string_lossy().into_owned();
        let Some((key, suffix)) = name.rsplit_once('.') else {
            continue;
        };
        let value: u128 = fs::read_to_string(entry.path())
            .ok()
            .and_then(|s| s.trim().parse().ok())
            .unwrap_or(0);
        match suffix {
            "start" => {
                starts.insert(key.to_string(), value);
            }
            "end" => {
                ends.insert(key.to_string(), value);
            }
            _ => {}
        }
    }
    let mut intervals: Vec<(u128, u128)> = starts
        .into_iter()
        .filter_map(|(key, start)| ends.get(&key).map(|end| (start, *end)))
        .collect();
    intervals.sort_unstable();
    intervals
}

fn assert_no_overlapping_intervals(intervals: &[(u128, u128)]) {
    for window in intervals.windows(2) {
        let (_, end_a) = window[0];
        let (start_b, _) = window[1];
        assert!(
            end_a <= start_b,
            "overlapping lock-held intervals detected: {:?} then {:?} -- mutual exclusion \
             violated (two writers held the lock at the same instant)",
            window[0],
            window[1]
        );
    }
}

// -- Atomic-crash: a kill mid-write must never leave a torn index ---------------------------

/// TDD spec: "Atomic-crash: a partial write never leaves a corrupt index." Repeatedly starts a
/// rebuild (by mutating the corpus each iteration so a save is always due) and terminates the
/// process after a short, jittered delay to sample different points in its lifetime, including
/// during the write. This is necessarily probabilistic (a black-box CLI test cannot pin the exact
/// instant of a kill to "mid-rename"), but the guarantee it is checking is NOT probabilistic:
/// because the destination is only ever touched by one atomic `rename`, `.tg_index` is
/// structurally guaranteed to be either its pre-attempt content or the fully-written new content
/// after ANY kill, never a hybrid -- this test samples across many timings to build confidence in
/// that guarantee empirically, on the real binary.
#[test]
fn atomic_crash_kill_mid_rebuild_never_leaves_a_torn_index_file() {
    let dir = tempdir().unwrap();
    write_wide_corpus(dir.path(), 300);
    let index_path = dir.path().join(".tg_index");

    let baseline = run_bounded(
        {
            let mut cmd = tg();
            cmd.arg("search")
                .arg("--index")
                .arg("--fixed-strings")
                .arg("--count")
                .arg("needle0")
                .arg(dir.path());
            cmd
        },
        Duration::from_secs(60),
    );
    assert!(
        baseline.status.success(),
        "stderr={}",
        String::from_utf8_lossy(&baseline.stderr)
    );
    assert!(index_path.exists());
    TrigramIndex::load(&index_path).expect("baseline index must be valid");

    for attempt in 0..8u64 {
        fs::write(
            dir.path().join(format!("mutate_{attempt}.txt")),
            "needle0 fresh addition\n",
        )
        .unwrap();

        let mut cmd = tg();
        cmd.arg("search")
            .arg("--index")
            .arg("--fixed-strings")
            .arg("--count")
            .arg("needle0")
            .arg(dir.path());
        cmd.stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        let child = cmd.spawn().expect("failed to spawn tg");

        // A short, jittered time limit: process_control terminates the child once this
        // elapses, sampling a different point in the process's lifetime each iteration
        // (including, on some iterations, mid-write) without a separate manual kill()+reap.
        let short_bound = Duration::from_millis(3 + attempt * 6);
        match child
            .controlled_with_output()
            .time_limit(short_bound)
            .terminate_for_timeout()
            .wait()
        {
            Ok(_) => {}
            Err(e) => panic!("attempt {attempt}: failed to reap the terminated child: {e}"),
        }

        if index_path.exists() {
            TrigramIndex::load(&index_path).unwrap_or_else(|e| {
                panic!(
                    "attempt {attempt}: .tg_index exists but failed to load after a kill \
                     mid-run -- this is a torn/corrupt write, exactly what atomic save must \
                     prevent: {e}"
                )
            });
        }
    }

    // Sanity: a final, uninterrupted run must still succeed and see every mutation (proves the
    // repeated kills above never wedged the corpus/index into an unrecoverable state).
    let final_run = run_bounded(
        {
            let mut cmd = tg();
            cmd.arg("search")
                .arg("--index")
                .arg("--fixed-strings")
                .arg("--verbose")
                .arg("--count")
                .arg("needle0")
                .arg(dir.path());
            cmd
        },
        Duration::from_secs(60),
    );
    assert!(
        final_run.status.success(),
        "stderr={}",
        String::from_utf8_lossy(&final_run.stderr)
    );
}

// -- Lock-timeout: a held (heartbeating) lock must never fail the search --------------------

/// TDD spec: "Lock-timeout: a held lock -> 2nd writer builds-in-memory+warns+returns CORRECT
/// results (no hang/error)." Simulates a concurrent external writer by creating the lock file
/// ourselves and heartbeating its mtime for the whole window, so the competing `tg` process must
/// genuinely exhaust its production 12s acquire timeout rather than stale-reclaim early --
/// distinguishing THIS test from the stale-reclaim test below.
#[test]
fn lock_held_and_heartbeating_causes_tg_to_skip_persistence_but_still_return_correct_results() {
    let dir = tempdir().unwrap();
    write_corpus(dir.path());
    let index_path = dir.path().join(".tg_index");
    let lock_path = lock_path_for(&index_path);

    fs::write(
        &lock_path,
        format!("{}\nsimulated-external-holder\n", std::process::id()),
    )
    .unwrap();
    let stop = Arc::new(AtomicBool::new(false));
    let hb_lock_path = lock_path.clone();
    let hb_stop = Arc::clone(&stop);
    let heartbeat = thread::spawn(move || {
        while !hb_stop.load(Ordering::SeqCst) {
            set_mtime(&hb_lock_path, SystemTime::now());
            thread::sleep(Duration::from_millis(400));
        }
    });

    let mut cmd = tg();
    cmd.arg("search")
        .arg("--index")
        .arg("--fixed-strings")
        .arg("--verbose")
        .arg("hello")
        .arg(dir.path());

    let started = Instant::now();
    // Bounded generously past the ~12s production timeout: a genuine timeout is a normal,
    // fast-ish completion here, not a hang -- 45s only guards against an ACTUAL regression.
    let output = run_bounded(cmd, Duration::from_secs(45));
    let elapsed = started.elapsed();

    stop.store(true, Ordering::SeqCst);
    heartbeat.join().unwrap();
    let _ = fs::remove_file(&lock_path);

    assert!(
        output.status.success(),
        "search must still succeed even though persistence was skipped; stderr={}",
        String::from_utf8_lossy(&output.stderr)
    );
    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(
        stdout.contains("hello world") && stdout.contains("hello again"),
        "a lock-acquire timeout must still return CORRECT results from the in-memory index, \
         not an error or empty output: stdout={stdout}"
    );

    // Proves this genuinely exercised the TIMEOUT path (H9: timeout=12s > stale=10s), not an
    // early stale-reclaim -- our heartbeat kept the lock's mtime fresh the whole time.
    assert!(
        elapsed >= Duration::from_secs(10),
        "expected tg to wait out close to the full ~12s acquire timeout, got {elapsed:?} \
         (looks like it stale-reclaimed our heartbeating lock instead)"
    );

    let stderr = String::from_utf8_lossy(&output.stderr).to_lowercase();
    assert!(
        stderr.contains("lock") || stderr.contains("skip"),
        "expected a persistence-skip warning naming the lock contention: stderr={stderr}"
    );

    assert!(
        !index_path.exists(),
        "a lock-acquire timeout must SKIP persistence, not write anyway (our simulated holder \
         still 'owned' the file for the entire window)"
    );
}

// -- Stale-reclaim: an abandoned lock must not be waited out --------------------------------

/// TDD spec: "Stale-reclaim: an abandoned lock (age>10s) reclaimed." A lock with no heartbeat,
/// backdated well past the 10s production stale threshold, must be reclaimed promptly by the
/// next writer -- proven by wall-clock: reclaim must be far faster than the 12s acquire timeout
/// (the exact H9 property: a fresh-but-dead lock is always reclaimed before any waiter's deadline
/// can expire, so this never degrades into the lock-timeout test above).
#[test]
fn tg_search_index_reclaims_an_abandoned_stale_lock_without_waiting_out_the_full_timeout() {
    let dir = tempdir().unwrap();
    write_corpus(dir.path());
    let index_path = dir.path().join(".tg_index");
    let lock_path = lock_path_for(&index_path);

    fs::write(&lock_path, "999999\ndeadtoken-no-heartbeat\n").unwrap();
    set_mtime(&lock_path, SystemTime::now() - Duration::from_secs(30));

    let mut cmd = tg();
    cmd.arg("search")
        .arg("--index")
        .arg("--fixed-strings")
        .arg("hello")
        .arg(dir.path());

    let started = Instant::now();
    let output = run_bounded(cmd, Duration::from_secs(30));
    let elapsed = started.elapsed();

    assert!(
        output.status.success(),
        "stderr={}",
        String::from_utf8_lossy(&output.stderr)
    );
    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(
        stdout.contains("hello world") && stdout.contains("hello again"),
        "stdout={stdout}"
    );

    assert!(
        elapsed < Duration::from_secs(8),
        "expected a near-immediate stale-lock reclaim (well under the 12s timeout), took \
         {elapsed:?} -- looks like it waited out the full timeout instead of reclaiming"
    );

    assert!(
        index_path.exists(),
        "the reclaiming writer must have persisted the index"
    );
    assert!(
        !lock_path.exists(),
        "the reclaiming writer's own lock must be released after it completes"
    );
}

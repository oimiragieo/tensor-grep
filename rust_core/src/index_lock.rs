//! Cross-process advisory locking for the persisted trigram index file, mirroring
//! `src/tensor_grep/cli/_index_lock.py` (the Python checkpoint-store lock primitives) so the
//! Rust-side `.tg_index` persistence gets the same fail-closed guarantees: an `O_CREAT | O_EXCL`
//! lockfile records an owner (pid + random token), a lock older than [`DEFAULT_STALE_AFTER`] is
//! reclaimed from a presumed-dead holder, and a live holder keeps its lock fresh via a heartbeat
//! thread so a waiter never mistakes "slow" for "dead" mid-hold.
//!
//! Audit #138 item #2 (index.json write is non-atomic + unlocked -> crash-corrupts + concurrent
//! writers lost-update). This module supplies ONLY the locking + retried-rename primitives (like
//! `_index_lock.py`); the atomic-write-of-index-bytes sequence itself lives next to its caller in
//! `index.rs::save`/`save_json`, and the decision of WHERE to acquire the lock (write sections
//! only -- readers stay lock-free) lives in `main.rs`'s `save_index_locked`.

use std::collections::hash_map::RandomState;
use std::fmt;
use std::fs::OpenOptions;
use std::hash::{BuildHasher, Hasher};
use std::io;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Condvar, Mutex};
use std::thread;
use std::time::{Duration, Instant, SystemTime};

/// A lock older than this is presumed abandoned by a dead holder and is reclaimed by the next
/// waiter. RMW-scaled (bounded by how long a save can plausibly take), NOT scaled to any
/// daemon-launch or process-startup latency.
pub const DEFAULT_STALE_AFTER: Duration = Duration::from_secs(10);

/// H9 invariant (mirrors `_index_lock.py`'s `_TIMEOUT_S` comment verbatim): this MUST exceed
/// [`DEFAULT_STALE_AFTER`]. A holder killed mid-write can leave a lock younger than
/// `DEFAULT_STALE_AFTER` at the moment a waiter starts polling; if the waiter's own deadline
/// could expire first (an old timeout < stale split), that fresh-but-dead lock would NEVER be
/// reclaimed within the wait window -- every waiter would raise a timeout instead of
/// self-healing. Keeping timeout > stale guarantees any lock already past (or about to pass) the
/// staleness threshold is reclaimed before a waiter gives up. Enforced below at compile time, not
/// just documented.
pub const DEFAULT_ACQUIRE_TIMEOUT: Duration = Duration::from_secs(12);

const DEFAULT_POLL_INTERVAL: Duration = Duration::from_millis(20);

/// Bound on how long [`IndexLockGuard`]'s drop waits for the heartbeat thread to acknowledge a
/// stop signal before giving up on it and releasing the lock anyway -- mirrors
/// `_index_lock.py`'s `heartbeat.join(timeout=1.0)`: bounded, so a wedged heartbeat thread can
/// never hang lock release (and therefore never hang the search it is guarding).
const HEARTBEAT_JOIN_BOUND: Duration = Duration::from_millis(1000);

const _H9_TIMEOUT_EXCEEDS_STALE: () = assert!(
    DEFAULT_ACQUIRE_TIMEOUT.as_nanos() > DEFAULT_STALE_AFTER.as_nanos(),
    "H9 invariant violated: DEFAULT_ACQUIRE_TIMEOUT must exceed DEFAULT_STALE_AFTER, else a \
     fresh-but-dead lock can outlive every waiter's deadline and never self-heal"
);

/// Raised when a lock could not be acquired within the timeout. Per the Backend Fail-Closed
/// Contract (AGENTS.md) this governs the on-disk CACHE, not search RESULTS: callers must treat
/// this as "skip persisting this run, keep serving the freshly-built in-memory index," never as a
/// reason to fail the search itself. A genuinely dead lock is reclaimed via mtime staleness
/// before this fires, so in practice this only fires under sustained LIVE contention.
#[derive(Debug)]
pub struct IndexLockTimeoutError {
    pub lock_path: PathBuf,
    pub timeout: Duration,
}

impl fmt::Display for IndexLockTimeoutError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(
            f,
            "could not acquire index lock {} within {:?}",
            self.lock_path.display(),
            self.timeout
        )
    }
}

impl std::error::Error for IndexLockTimeoutError {}

/// Dot-prefixed + `.lock` suffix, mirroring `_index_lock.py::_lock_path_for` -- never matched by
/// any `.tg_index` / checkpoint / session discovery glob.
fn lock_path_for(index_path: &Path) -> PathBuf {
    let name = index_path
        .file_name()
        .and_then(|n| n.to_str())
        .unwrap_or("index");
    index_path.with_file_name(format!(".{name}.lock"))
}

/// Process-local, non-cryptographic randomness for lock ownership tokens and temp-file name
/// disambiguation -- collision-avoidance only (mutual exclusion itself comes from
/// `O_CREAT | O_EXCL`, not from this being unguessable). Deliberately std-only (no new crate
/// dependency): `RandomState::new()` mixes a per-thread call counter into an OS-random-seeded key
/// on every construction, so hashing through a fresh instance yields a different digest each call
/// within the same process (and a different seed across processes/threads).
pub fn random_token() -> String {
    let a = RandomState::new().build_hasher().finish();
    let b = RandomState::new().build_hasher().finish();
    format!("{a:016x}{b:016x}")
}

/// Reads back the ownership token written by [`IndexLockGuard::acquire_with`] (the second line of
/// `{pid}\n{token}\n`). Returns `None` if the file is gone, unreadable, or lacks a token line
/// (e.g. a partially-written or legacy lock) -- callers must treat `None` as "not mine," never as
/// a match.
fn token_for_lock(lock_path: &Path) -> Option<String> {
    let content = std::fs::read_to_string(lock_path).ok()?;
    let mut lines = content.lines();
    let _pid_line = lines.next()?;
    let token_line = lines.next()?;
    let token = token_line.trim();
    if token.is_empty() {
        None
    } else {
        Some(token.to_string())
    }
}

/// Ownership-token-guarded release: unlink `lock_path` ONLY if it still carries `token` (i.e.
/// this guard still owns it). If a waiter reclaimed the lock as stale while this holder was still
/// slow-but-alive, the token on disk no longer matches -- leave that live lock alone instead of
/// deleting it out from under the new owner (the lost-update / two-holders race). Tolerates the
/// lock already being gone.
fn release_lock(lock_path: &Path, token: &str) {
    if token_for_lock(lock_path).as_deref() != Some(token) {
        return;
    }
    let _ = std::fs::remove_file(lock_path);
}

/// Test-only observability hook (zero-cost/no-op unless `TG_INDEX_LOCK_DEBUG_LOG_DIR` is set):
/// records a held-interval endpoint (`"start"` or `"end"`) as a nanosecond timestamp in its own
/// per-acquisition file (`{pid}_{token}.{suffix}`). Each file is written exactly once by exactly
/// one process, so -- unlike a single shared append log -- there is no cross-process write race
/// to reason about. Lets a concurrency test assert mutual exclusion DIRECTLY (no two logged
/// [start, end) intervals overlap) instead of only inferring it from absence-of-corruption.
fn debug_log_event(token: &str, suffix: &str) {
    let Ok(dir) = std::env::var("TG_INDEX_LOCK_DEBUG_LOG_DIR") else {
        return;
    };
    let now_nanos = SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or(0);
    let file_name = format!("{}_{token}.{suffix}", std::process::id());
    let _ = std::fs::write(Path::new(&dir).join(file_name), now_nanos.to_string());
}

/// Sets the lock file's mtime to now, mirroring `_index_lock.py`'s `os.utime(lock_path, None)`
/// heartbeat touch.
fn touch(path: &Path) -> io::Result<()> {
    let file = OpenOptions::new().write(true).open(path)?;
    let times = std::fs::FileTimes::new().set_modified(SystemTime::now());
    file.set_times(times)
}

/// Well under `stale_after` so a live-but-slow holder's mtime never crosses the staleness
/// threshold between beats; floored at `poll_interval` so a tiny custom `stale_after` (tests)
/// can't drive this to ~0 and busy-loop the heartbeat thread.
fn default_heartbeat_interval(stale_after: Duration, poll_interval: Duration) -> Duration {
    let third = stale_after / 3;
    if third > poll_interval {
        third
    } else {
        poll_interval
    }
}

struct HeartbeatState {
    stop: bool,
    finished: bool,
}

struct HeartbeatControl {
    state: Mutex<HeartbeatState>,
    condvar: Condvar,
}

/// Spawns the heartbeat thread and returns the control handle used to signal + bounded-wait for
/// its stop on release. The thread re-checks ownership (via [`token_for_lock`]) before every
/// touch so a heartbeat that outlives its own lock (release already ran, or -- defensively --
/// someone else reclaimed) never props up a DIFFERENT holder's lock.
fn spawn_heartbeat(lock_path: PathBuf, token: String, interval: Duration) -> Arc<HeartbeatControl> {
    let control = Arc::new(HeartbeatControl {
        state: Mutex::new(HeartbeatState {
            stop: false,
            finished: false,
        }),
        condvar: Condvar::new(),
    });
    let thread_control = Arc::clone(&control);
    thread::spawn(move || loop {
        let guard = thread_control
            .state
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        // Check `stop` BEFORE waiting, not just after: if the guard's Drop already ran (set
        // stop=true and notify_all()'d) before this thread reached its first wait_timeout --
        // entirely possible for a short-held lock, since thread::spawn does not guarantee the
        // new thread runs before the spawning thread continues -- that notification is
        // otherwise MISSED (Condvar::notify_all only wakes threads already waiting at the
        // moment it's called), and this thread would fall through to waiting out a full
        // `interval` before ever re-checking `stop`. Checking here first closes that lost-
        // wakeup race: whoever next acquires the mutex always observes the latest `stop`
        // value regardless of notification timing.
        if guard.stop {
            let mut guard = guard;
            guard.finished = true;
            thread_control.condvar.notify_all();
            return;
        }
        let (mut guard, timeout_result) = thread_control
            .condvar
            .wait_timeout(guard, interval)
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        if guard.stop {
            guard.finished = true;
            thread_control.condvar.notify_all();
            return;
        }
        drop(guard);
        if timeout_result.timed_out() {
            if token_for_lock(&lock_path).as_deref() != Some(token.as_str()) {
                let mut guard = thread_control
                    .state
                    .lock()
                    .unwrap_or_else(|poisoned| poisoned.into_inner());
                guard.finished = true;
                thread_control.condvar.notify_all();
                return;
            }
            let _ = touch(&lock_path); // transient failure (e.g. Windows delete-pending); next beat retries
        }
    });
    control
}

/// RAII write-lock for the persisted index file. Acquire around the SAVE only -- never around a
/// read/load -- per the fail-closed contract's "reads stay lock-free" invariant. Dropping the
/// guard releases the lock (token-guarded) after signaling the heartbeat thread to stop and
/// bounded-waiting (up to [`HEARTBEAT_JOIN_BOUND`]) for it to acknowledge.
pub struct IndexLockGuard {
    lock_path: PathBuf,
    token: String,
    heartbeat: Arc<HeartbeatControl>,
}

impl IndexLockGuard {
    /// Acquires the write lock for `index_path` using the default production timing (12s
    /// timeout, 10s stale threshold, 20ms poll -- the H9-verified values).
    pub fn acquire(index_path: &Path) -> Result<Self, IndexLockTimeoutError> {
        Self::acquire_with(
            index_path,
            DEFAULT_ACQUIRE_TIMEOUT,
            DEFAULT_STALE_AFTER,
            DEFAULT_POLL_INTERVAL,
        )
    }

    /// Same as [`Self::acquire`] with caller-supplied timing, for tests that need a fast,
    /// deterministic stale/timeout window instead of waiting out the real 10s/12s production
    /// values. Production callers should use [`Self::acquire`].
    pub fn acquire_with(
        index_path: &Path,
        timeout: Duration,
        stale_after: Duration,
        poll_interval: Duration,
    ) -> Result<Self, IndexLockTimeoutError> {
        let lock_path = lock_path_for(index_path);
        if let Some(parent) = lock_path.parent() {
            let _ = std::fs::create_dir_all(parent);
        }
        let deadline = Instant::now() + timeout;
        loop {
            match OpenOptions::new()
                .write(true)
                .create_new(true) // O_CREAT | O_EXCL
                .open(&lock_path)
            {
                Ok(mut file) => {
                    let token = random_token();
                    use std::io::Write as _;
                    // Best-effort content write, matching _index_lock.py: an OSError writing the
                    // freshly-created lock file is exceptional (not a contention signal) and
                    // isn't specially handled there either -- a missing/short token line just
                    // makes token_for_lock() return None, which every guard (release/heartbeat)
                    // already treats as "not mine, don't touch it": fails closed, not silently.
                    let _ = writeln!(file, "{}", std::process::id());
                    let _ = writeln!(file, "{token}");
                    drop(file);
                    debug_log_event(&token, "start");
                    let hb_interval = default_heartbeat_interval(stale_after, poll_interval);
                    let heartbeat = spawn_heartbeat(lock_path.clone(), token.clone(), hb_interval);
                    return Ok(IndexLockGuard {
                        lock_path,
                        token,
                        heartbeat,
                    });
                }
                Err(e) if e.kind() == io::ErrorKind::AlreadyExists => {
                    // Lock is held. Reclaim it if stale (dead holder), else fall through to wait.
                    if let Ok(meta) = std::fs::metadata(&lock_path) {
                        if let Ok(modified) = meta.modified() {
                            if SystemTime::now()
                                .duration_since(modified)
                                .unwrap_or_default()
                                > stale_after
                            {
                                // GUARDED: two racing reclaimers must not crash the loser.
                                let _ = std::fs::remove_file(&lock_path);
                                continue;
                            }
                        }
                    }
                }
                Err(e) if e.kind() == io::ErrorKind::PermissionDenied => {
                    // Windows delete-pending race: a concurrent reclaimer just removed the lock,
                    // so the name is in a "delete pending" state and create_new() raises
                    // access-denied instead of POSIX's plain "already exists". Transient -- fall
                    // through to wait/retry; a genuine permission error self-limits by continuing
                    // to fail here and hitting the deadline below, never a raw leak.
                }
                Err(_) => {
                    // Any other error (e.g. the parent directory itself is unwritable) is not a
                    // contention signal; fall through to the same timeout/retry loop rather than
                    // a distinct error type, matching _index_lock.py (which only special-cases
                    // FileExistsError/PermissionError and otherwise retries to its own deadline).
                }
            }
            if Instant::now() >= deadline {
                return Err(IndexLockTimeoutError { lock_path, timeout });
            }
            thread::sleep(poll_interval);
        }
    }
}

impl Drop for IndexLockGuard {
    fn drop(&mut self) {
        {
            // Mutate through the same MutexGuard handed to wait_timeout_while, so there is no
            // window where another thread could observe stop=false after we set it here.
            let mut guard = self
                .heartbeat
                .state
                .lock()
                .unwrap_or_else(|poisoned| poisoned.into_inner());
            guard.stop = true;
            self.heartbeat.condvar.notify_all();
            let _ = self
                .heartbeat
                .condvar
                .wait_timeout_while(guard, HEARTBEAT_JOIN_BOUND, |s| !s.finished)
                .unwrap_or_else(|poisoned| poisoned.into_inner());
        }
        debug_log_event(&self.token, "end");
        release_lock(&self.lock_path, &self.token);
    }
}

/// `std::fs::rename` retried on the Windows-only transient permission error that fires when the
/// destination is momentarily held open by a concurrent reader / AV scanner / the search indexer.
/// On POSIX, rename is atomic and essentially never raises this, so the retry loop is a no-op
/// there. Fails CLOSED: the last error is returned after exhausting attempts, never silently
/// leaving a stale/torn index in place. Mirrors `_index_lock.py::replace_with_retry`.
pub fn replace_with_retry(src: &Path, dst: &Path) -> io::Result<()> {
    replace_with_retry_params(src, dst, 10, Duration::from_millis(20))
}

/// Same as [`replace_with_retry`] with caller-supplied attempt count/delay, for tests that need a
/// fast, bounded retry window.
pub fn replace_with_retry_params(
    src: &Path,
    dst: &Path,
    attempts: u32,
    delay: Duration,
) -> io::Result<()> {
    let attempts = attempts.max(1);
    let mut last_err = None;
    for attempt in 0..attempts {
        match std::fs::rename(src, dst) {
            Ok(()) => return Ok(()),
            Err(e) if e.kind() == io::ErrorKind::PermissionDenied => {
                last_err = Some(e);
                if attempt + 1 == attempts {
                    break;
                }
                thread::sleep(delay);
            }
            Err(e) => return Err(e),
        }
    }
    Err(last_err.unwrap_or_else(|| io::Error::other("replace_with_retry: rename failed")))
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::tempdir;

    fn tiny_timing() -> (Duration, Duration, Duration) {
        // (timeout, stale_after, poll_interval) -- deterministic for tests, while preserving
        // the H9 invariant (timeout > stale_after). Deliberately NOT as tiny as the name
        // suggests: this races a live heartbeat thread against real OS scheduling, and a
        // previous 300ms/120ms pairing was observed to flake under `cargo test`'s parallel
        // load (the heartbeat thread got starved past stale_after, so the "held" lock looked
        // abandoned and got reclaimed instead of timing out). 800ms stale_after tolerates far
        // more scheduling jitter before that can happen again.
        (
            Duration::from_millis(1000),
            Duration::from_millis(800),
            Duration::from_millis(10),
        )
    }

    #[test]
    fn h9_invariant_holds_for_production_defaults() {
        assert!(
            DEFAULT_ACQUIRE_TIMEOUT > DEFAULT_STALE_AFTER,
            "acquire timeout must exceed the stale threshold"
        );
    }

    #[test]
    fn acquire_then_release_leaves_no_lock_file_behind() {
        let dir = tempdir().unwrap();
        let index_path = dir.path().join(".tg_index");
        let lock_path = lock_path_for(&index_path);

        {
            let _guard = IndexLockGuard::acquire_with(
                &index_path,
                Duration::from_secs(2),
                Duration::from_secs(1),
                Duration::from_millis(10),
            )
            .expect("uncontended acquire must succeed");
            assert!(lock_path.exists(), "lock file should exist while held");
            let content = std::fs::read_to_string(&lock_path).unwrap();
            let mut lines = content.lines();
            let pid_line: u32 = lines.next().unwrap().parse().unwrap();
            assert_eq!(pid_line, std::process::id());
            assert!(
                !lines.next().unwrap().trim().is_empty(),
                "token line must be non-empty"
            );
        }
        assert!(
            !lock_path.exists(),
            "lock file must be removed after the guard drops"
        );
    }

    #[test]
    fn second_acquire_times_out_while_first_is_held() {
        let dir = tempdir().unwrap();
        let index_path = dir.path().join(".tg_index");
        let (timeout, stale_after, poll) = tiny_timing();

        let _holder = IndexLockGuard::acquire_with(&index_path, timeout, stale_after, poll)
            .expect("first acquire must succeed");

        let started = Instant::now();
        let result = IndexLockGuard::acquire_with(&index_path, timeout, stale_after, poll);
        let elapsed = started.elapsed();

        assert!(
            result.is_err(),
            "second acquire must time out while the first guard is alive"
        );
        assert!(
            elapsed >= timeout,
            "must not return before the timeout elapsed: {elapsed:?} < {timeout:?}"
        );
        // Bounded upper check too, so a regression that busy-spins forever fails the test
        // instead of hanging it (anti-hang-test-protocol).
        assert!(
            elapsed < timeout * 4,
            "timed out far later than expected: {elapsed:?}"
        );
    }

    #[test]
    fn stale_lock_is_reclaimed_without_waiting_out_the_full_timeout() {
        let dir = tempdir().unwrap();
        let index_path = dir.path().join(".tg_index");
        let lock_path = lock_path_for(&index_path);

        // Simulate an abandoned lock from a dead holder: write it directly (no heartbeat), then
        // backdate its mtime past stale_after.
        std::fs::write(&lock_path, b"999999\ndeadtoken\n").unwrap();
        let stale_after = Duration::from_millis(50);
        let backdated = SystemTime::now() - (stale_after + Duration::from_millis(200));
        let file = OpenOptions::new().write(true).open(&lock_path).unwrap();
        file.set_times(std::fs::FileTimes::new().set_modified(backdated))
            .unwrap();

        let timeout = Duration::from_secs(5); // generous; the assertion is on ELAPSED, not this
        let started = Instant::now();
        let guard = IndexLockGuard::acquire_with(
            &index_path,
            timeout,
            stale_after,
            Duration::from_millis(10),
        )
        .expect("a stale lock must be reclaimed, not waited out");
        let elapsed = started.elapsed();

        assert!(
            elapsed < timeout / 2,
            "reclaim should be near-immediate, not close to the full timeout: {elapsed:?}"
        );
        drop(guard);
    }

    #[test]
    fn heartbeat_keeps_a_slow_holder_alive_past_the_stale_threshold() {
        let dir = tempdir().unwrap();
        let index_path = dir.path().join(".tg_index");
        // Deliberately WIDE margins: this test depends on the heartbeat thread actually getting
        // scheduled promptly, which is not guaranteed under real OS contention (e.g. a
        // `cargo test` run where 100 tests run in parallel across a handful of cores). A tight
        // stale_after (previously 80ms, heartbeat interval ~27ms) was observed to flake under
        // that exact load -- the heartbeat thread got starved past 80ms and its lock was
        // (correctly, per the mechanism) reclaimed as stale, failing this test for a scheduling
        // reason unrelated to the property it's checking. 1.2s stale_after (a ~400ms heartbeat
        // interval) tolerates several missed/delayed beats before ever approaching staleness.
        let stale_after = Duration::from_millis(1200);
        let poll = Duration::from_millis(20);

        let holder =
            IndexLockGuard::acquire_with(&index_path, Duration::from_secs(10), stale_after, poll)
                .unwrap();

        // Outlive the stale threshold several times over while the ORIGINAL guard is still
        // alive; the heartbeat thread must keep touching the lock's mtime so a waiter never
        // reclaims a live holder's lock.
        std::thread::sleep(stale_after * 3);

        let started = Instant::now();
        let result = IndexLockGuard::acquire_with(
            &index_path,
            Duration::from_millis(500),
            stale_after,
            poll,
        );
        assert!(
            result.is_err(),
            "a live, heartbeating holder's lock must never be stolen"
        );
        assert!(started.elapsed() < Duration::from_secs(2), "must not hang");
        drop(holder);
    }

    #[test]
    fn replace_with_retry_succeeds_on_a_plain_rename() {
        let dir = tempdir().unwrap();
        let src = dir.path().join("src.tmp");
        let dst = dir.path().join("dst");
        std::fs::write(&src, b"payload").unwrap();
        replace_with_retry(&src, &dst).unwrap();
        assert_eq!(std::fs::read(&dst).unwrap(), b"payload");
        assert!(!src.exists());
    }

    #[test]
    fn replace_with_retry_overwrites_an_existing_destination() {
        let dir = tempdir().unwrap();
        let src = dir.path().join("src.tmp");
        let dst = dir.path().join("dst");
        std::fs::write(&dst, b"old").unwrap();
        std::fs::write(&src, b"new").unwrap();
        replace_with_retry(&src, &dst).unwrap();
        assert_eq!(std::fs::read(&dst).unwrap(), b"new");
    }

    #[test]
    fn random_token_is_not_constant_across_calls() {
        let tokens: std::collections::HashSet<String> = (0..32).map(|_| random_token()).collect();
        assert!(
            tokens.len() > 1,
            "random_token() must vary across calls (got {} unique of 32)",
            tokens.len()
        );
    }

    #[test]
    fn two_threads_never_observe_the_lock_simultaneously() {
        // In-process complement to the cross-process hammer test (rust_core/tests/): proves
        // mutual exclusion at the thread level using a shared counter that must never exceed 1.
        let dir = tempdir().unwrap();
        let index_path = Arc::new(dir.path().join(".tg_index"));
        let active = Arc::new(std::sync::atomic::AtomicUsize::new(0));
        let max_observed = Arc::new(std::sync::atomic::AtomicUsize::new(0));

        let mut handles = Vec::new();
        for _ in 0..8 {
            let index_path = Arc::clone(&index_path);
            let active = Arc::clone(&active);
            let max_observed = Arc::clone(&max_observed);
            handles.push(thread::spawn(move || {
                for _ in 0..20 {
                    let guard = IndexLockGuard::acquire_with(
                        &index_path,
                        Duration::from_secs(5),
                        Duration::from_millis(500),
                        Duration::from_millis(2),
                    )
                    .expect("acquire must eventually succeed for every thread");
                    let now = active.fetch_add(1, std::sync::atomic::Ordering::SeqCst) + 1;
                    max_observed.fetch_max(now, std::sync::atomic::Ordering::SeqCst);
                    std::thread::sleep(Duration::from_micros(200));
                    active.fetch_sub(1, std::sync::atomic::Ordering::SeqCst);
                    drop(guard);
                }
            }));
        }
        for handle in handles {
            handle.join().unwrap();
        }
        assert_eq!(
            max_observed.load(std::sync::atomic::Ordering::SeqCst),
            1,
            "at most one thread must hold the lock at any instant"
        );
    }
}

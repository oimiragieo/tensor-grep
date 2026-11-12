//! Cross-process advisory locking for the persisted trigram index file, mirroring
//! `src/tensor_grep/cli/_index_lock.py` (the Python checkpoint-store lock primitives) so the
//! Rust-side `.tg_index` persistence gets the same fail-closed guarantees: a persistent
//! `.lock.os` byte-range sidecar is held through the lifecycle while an `O_CREAT | O_EXCL`
//! lockfile records an owner (pid + random token). A lock older than [`DEFAULT_STALE_AFTER`] is
//! reclaimed from a presumed-dead holder, and a live holder keeps its lock fresh via a heartbeat
//! thread so a waiter never mistakes "slow" for "dead" mid-hold.
//!
//! Audit #138 item #2 (index.json write is non-atomic + unlocked -> crash-corrupts + concurrent
//! writers lost-update). This module supplies ONLY the locking + retried-rename primitives (like
//! `_index_lock.py`); the atomic-write-of-index-bytes sequence itself lives next to its caller in
//! `index.rs::save`/`save_json`, and the decision of WHERE to acquire the lock (write sections
//! only -- readers stay lock-free) lives in `main.rs`'s `save_index_locked`.

use crate::safe_write::{AnchoredDirectory, AnchoredTarget};
use std::collections::hash_map::RandomState;
use std::fmt;
use std::fs::{File, OpenOptions, TryLockError};
use std::hash::{BuildHasher, Hasher};
use std::io::{self, Read, Write};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Condvar, Mutex};
use std::thread;
use std::time::{Duration, Instant, SystemTime};

#[allow(dead_code)]
mod sidecar;

fn write_owner_record(file: &mut impl Write, token: &str) -> io::Result<()> {
    let pid = writeln!(file, "{}", std::process::id());
    let token = writeln!(file, "{token}");
    pid.and(token)
}

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
// The pid and 32-hex ownership token fit comfortably below this bound.
const MAX_LOCK_METADATA_BYTES: u64 = 4 * 1024;

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

/// Persistent OS lock shared byte-for-byte with Python's `_os_lock_path_for` sidecar.
fn os_lock_path_for(lock_path: &Path) -> PathBuf {
    let mut name = lock_path
        .file_name()
        .unwrap_or_else(|| std::ffi::OsStr::new(".index.lock"))
        .to_os_string();
    name.push(".os");
    lock_path.with_file_name(name)
}

/// Holds the persistent Python-compatible sidecar lock until the handle closes. Rust maps
/// `File::try_lock` to Unix `flock` and Windows `LockFileEx`; the Windows whole-file range
/// overlaps Python's `_locking` byte-0 range.
struct OsFileLock {
    _file: File,
}

impl OsFileLock {
    fn try_acquire(file: File) -> io::Result<Option<Self>> {
        match file.try_lock() {
            Ok(()) => Ok(Some(Self { _file: file })),
            Err(TryLockError::WouldBlock) => Ok(None),
            Err(TryLockError::Error(error)) => Err(error),
        }
    }
}

fn try_os_file_lock_target(target: &AnchoredTarget) -> io::Result<Option<OsFileLock>> {
    let file = match target.open_os_lock_file() {
        Ok(file) => file,
        Err(error) if error.kind() == io::ErrorKind::AlreadyExists => return Ok(None),
        Err(error) => return Err(error),
    };
    let Some(lock) = OsFileLock::try_acquire(file)? else {
        return Ok(None);
    };
    if target.validate_leaf_matches_expected().is_err() {
        drop(lock);
        return Ok(None);
    }
    Ok(Some(lock))
}

fn acquire_os_sidecar_with_hook<F>(
    directory: &AnchoredDirectory,
    name: &std::ffi::OsStr,
    deadline: Instant,
    poll_interval: Duration,
    mut on_contention: F,
) -> Option<OsFileLock>
where
    F: FnMut(),
{
    loop {
        let attempt = directory
            .target(name)
            .map_err(|_| ())
            .and_then(|target| try_os_file_lock_target(&target).map_err(|_| ()))
            .ok()
            .flatten();
        if attempt.is_some() {
            return attempt;
        }
        on_contention();
        if Instant::now() >= deadline {
            return None;
        }
        thread::sleep(poll_interval);
    }
}

/// Process-local, non-cryptographic randomness for lock ownership tokens and temp-file name
/// disambiguation -- collision-avoidance only (mutual exclusion comes from the OS sidecar lock,
/// not from this being unguessable). Deliberately std-only (no new crate
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
#[derive(Clone)]
struct LockLocation {
    path: PathBuf,
    target: Option<Arc<AnchoredTarget>>,
}

fn token_for_lock(location: &LockLocation) -> Option<String> {
    let bytes = match &location.target {
        Some(target) => target.read_bounded(MAX_LOCK_METADATA_BYTES).ok()?,
        None => {
            let file = File::open(&location.path).ok()?;
            if file.metadata().ok()?.len() > MAX_LOCK_METADATA_BYTES {
                return None;
            }
            let read_limit = MAX_LOCK_METADATA_BYTES.checked_add(1)?;
            let mut bytes = Vec::new();
            file.take(read_limit).read_to_end(&mut bytes).ok()?;
            if bytes.len() as u64 > MAX_LOCK_METADATA_BYTES {
                return None;
            }
            bytes
        }
    };
    let content = String::from_utf8(bytes).ok()?;
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
fn release_lock(location: &LockLocation, token: &str) {
    let _ = release_lock_with(location, token, || Ok(()));
}

fn release_lock_with<F>(
    location: &LockLocation,
    token: &str,
    after_token_check: F,
) -> io::Result<()>
where
    F: FnOnce() -> io::Result<()>,
{
    if token_for_lock(location).as_deref() != Some(token) {
        return Ok(());
    }
    if let Some(target) = &location.target {
        #[cfg(test)]
        return target.remove_file_with_hook(after_token_check);
        #[cfg(not(test))]
        {
            after_token_check()?;
            target.remove_file()
        }
    } else {
        after_token_check()?;
        match std::fs::remove_file(&location.path) {
            Ok(()) => Ok(()),
            Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(()),
            Err(error) => Err(error),
        }
    }
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
fn touch(location: &LockLocation) -> io::Result<()> {
    if let Some(target) = &location.target {
        target.touch()
    } else {
        let file = OpenOptions::new().write(true).open(&location.path)?;
        let times = std::fs::FileTimes::new().set_modified(SystemTime::now());
        file.set_times(times)
    }
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
fn spawn_heartbeat(
    location: LockLocation,
    token: String,
    interval: Duration,
) -> Arc<HeartbeatControl> {
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
            if token_for_lock(&location).as_deref() != Some(token.as_str()) {
                let mut guard = thread_control
                    .state
                    .lock()
                    .unwrap_or_else(|poisoned| poisoned.into_inner());
                guard.finished = true;
                thread_control.condvar.notify_all();
                return;
            }
            let _ = touch(&location); // transient failure (e.g. Windows delete-pending); next beat retries
        }
    });
    control
}

/// RAII write-lock for the persisted index file. Acquire around the SAVE only -- never around a
/// read/load -- per the fail-closed contract's "reads stay lock-free" invariant. Dropping the
/// guard releases the lock (token-guarded) after signaling the heartbeat thread to stop and
/// bounded-waiting (up to [`HEARTBEAT_JOIN_BOUND`]) for it to acknowledge.
pub struct IndexLockGuard {
    location: LockLocation,
    token: String,
    heartbeat: Arc<HeartbeatControl>,
    _sidecar_lock: OsFileLock,
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
        Self::acquire_with_writer(
            index_path,
            timeout,
            stale_after,
            poll_interval,
            write_owner_record,
        )
    }

    fn acquire_with_writer(
        index_path: &Path,
        timeout: Duration,
        stale_after: Duration,
        poll_interval: Duration,
        writer: impl FnOnce(&mut std::fs::File, &str) -> io::Result<()>,
    ) -> Result<Self, IndexLockTimeoutError> {
        let lock_path = lock_path_for(index_path);
        let parent = lock_path
            .parent()
            .filter(|path| !path.as_os_str().is_empty())
            .unwrap_or_else(|| Path::new("."));
        let _ = std::fs::create_dir_all(parent);
        let absolute_parent = if parent.is_absolute() {
            parent.to_path_buf()
        } else {
            std::env::current_dir()
                .unwrap_or_else(|_| PathBuf::from("."))
                .join(parent)
        };
        let directory =
            AnchoredDirectory::open(&absolute_parent).map_err(|_| IndexLockTimeoutError {
                lock_path: lock_path.clone(),
                timeout,
            })?;
        Self::acquire_at(
            &lock_path,
            &directory,
            timeout,
            stale_after,
            poll_interval,
            writer,
        )
    }

    /// Acquires the same Python-compatible sidecar through an already-validated directory.
    pub fn acquire_anchored(
        index_path: &Path,
        directory: &AnchoredDirectory,
    ) -> Result<Self, IndexLockTimeoutError> {
        let lock_path = lock_path_for(index_path);
        let directory = directory.try_clone().map_err(|_| IndexLockTimeoutError {
            lock_path: lock_path.clone(),
            timeout: DEFAULT_ACQUIRE_TIMEOUT,
        })?;
        Self::acquire_at(
            &lock_path,
            &directory,
            DEFAULT_ACQUIRE_TIMEOUT,
            DEFAULT_STALE_AFTER,
            DEFAULT_POLL_INTERVAL,
            write_owner_record,
        )
    }

    fn acquire_at(
        lock_path: &Path,
        directory: &AnchoredDirectory,
        timeout: Duration,
        stale_after: Duration,
        poll_interval: Duration,
        writer: impl FnOnce(&mut std::fs::File, &str) -> io::Result<()>,
    ) -> Result<Self, IndexLockTimeoutError> {
        Self::acquire_at_with_contention_hook(
            lock_path,
            directory,
            timeout,
            stale_after,
            poll_interval,
            writer,
            || {},
        )
    }

    fn acquire_at_with_contention_hook<F>(
        lock_path: &Path,
        directory: &AnchoredDirectory,
        timeout: Duration,
        stale_after: Duration,
        poll_interval: Duration,
        writer: impl FnOnce(&mut std::fs::File, &str) -> io::Result<()>,
        on_sidecar_contention: F,
    ) -> Result<Self, IndexLockTimeoutError>
    where
        F: FnMut(),
    {
        let lock_path = lock_path.to_path_buf();
        let deadline = Instant::now() + timeout;
        let sidecar_path = os_lock_path_for(&lock_path);
        let sidecar_name = sidecar_path
            .file_name()
            .expect("sidecar path always has a file name");
        let sidecar_lock = acquire_os_sidecar_with_hook(
            directory,
            sidecar_name,
            deadline,
            poll_interval,
            on_sidecar_contention,
        )
        .ok_or_else(|| IndexLockTimeoutError {
            lock_path: lock_path.clone(),
            timeout,
        })?;
        let lock_name = lock_path
            .file_name()
            .expect("legacy lock path always has a file name");
        loop {
            let target = match directory.target(lock_name) {
                Ok(target) => Arc::new(target),
                Err(_) => {
                    if Instant::now() >= deadline {
                        return Err(IndexLockTimeoutError { lock_path, timeout });
                    }
                    thread::sleep(poll_interval);
                    continue;
                }
            };
            let location = LockLocation {
                path: lock_path.clone(),
                target: Some(Arc::clone(&target)),
            };
            let creation = target.create_new_file();
            match creation {
                Ok(mut file) => {
                    let token = random_token();
                    // Best-effort content write, matching _index_lock.py: an OSError writing the
                    // freshly-created lock file is exceptional (not a contention signal) and
                    // isn't specially handled there either -- a missing/short token line just
                    // makes token_for_lock() return None, which every guard (release/heartbeat)
                    // already treats as "not mine, don't touch it": fails closed, not silently.
                    let _ = writer(&mut file, &token);
                    drop(file);
                    debug_log_event(&token, "start");
                    let hb_interval = default_heartbeat_interval(stale_after, poll_interval);
                    let heartbeat = spawn_heartbeat(location.clone(), token.clone(), hb_interval);
                    return Ok(IndexLockGuard {
                        location,
                        token,
                        heartbeat,
                        _sidecar_lock: sidecar_lock,
                    });
                }
                Err(e) if e.kind() == io::ErrorKind::AlreadyExists => {
                    // Lock is held. Reclaim it if stale (dead holder), else fall through to wait.
                    if let Ok(meta) = target.metadata() {
                        if let Ok(modified) = meta.modified() {
                            if SystemTime::now()
                                .duration_since(modified)
                                .unwrap_or_default()
                                > stale_after
                            {
                                // GUARDED: two racing reclaimers must not crash the loser.
                                let _ = target.remove_file();
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
        release_lock(&self.location, &self.token);
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
    fn oversized_lock_metadata_is_not_ownership_evidence() {
        let dir = tempdir().unwrap();
        for (name, anchored) in [("anchored.lock", true), ("legacy.lock", false)] {
            let path = dir.path().join(name);
            std::fs::write(&path, b"123\nsmall-token\n").unwrap();
            let target = anchored.then(|| Arc::new(AnchoredTarget::open(&path).unwrap()));
            let location = LockLocation {
                path: path.clone(),
                target,
            };
            assert_eq!(token_for_lock(&location).as_deref(), Some("small-token"));

            let mut file = OpenOptions::new().append(true).open(&path).unwrap();
            file.write_all(&vec![b'x'; MAX_LOCK_METADATA_BYTES as usize])
                .unwrap();
            drop(file);
            let oversized = std::fs::read(&path).unwrap();
            assert!(oversized.len() as u64 > MAX_LOCK_METADATA_BYTES);
            assert_eq!(token_for_lock(&location), None);

            let mut removal_check_ran = false;
            release_lock_with(&location, "small-token", || {
                removal_check_ran = true;
                Ok(())
            })
            .unwrap();
            assert!(!removal_check_ran);
            assert_eq!(std::fs::read(&path).unwrap(), oversized);
        }
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
        drop(file);

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
        // NO WALL-CLOCK ASSERTION HERE, deliberately. This previously carried
        // `assert!(started.elapsed() < Duration::from_secs(2), "must not hang")` -- a 2s budget
        // over a 500ms acquire timeout -- and it failed on main run 32557799696
        // (test-rust-core windows-latest/stable: 188 passed, 1 failed) on a contended runner,
        // SKIPPING the entire release chain: Semantic Release, both build-pypi jobs, publish-pypi
        // and validate-pypi-artifacts all reported `skipped`. The correctness assertion above
        // PASSED in that run; only the clock lost.
        //
        // A hard wall-clock budget is a contention detector, not a correctness test: it snaps
        // first when a shared runner loads, while everything else merely slows and still passes.
        // Widening the budget is NOT the fix -- the same move was tried on a sibling lane on
        // 2026-07-27, bought 4x the wasted wall-clock, and was reverted the same day. Note this
        // test's `stale_after` was ALREADY widened once (80ms -> 1200ms, see the comment above)
        // for the same class of flake; a second widening is the pattern, not a remedy.
        //
        // What the removed assertion actually guarded is not lost. A genuine hang -- the failure
        // it was named for -- means `acquire_with` never returns, so `result` is never bound and
        // the whole suite fails on cargo's own timeout rather than on this line. The `is_err()`
        // above already proves the call RETURNED and REFUSED, which is the property this test
        // exists to establish.
        //
        // The narrow gap this leaves: a regression where the timeout is ignored but the call
        // eventually errors anyway would now pass here. That is a deadline-ENFORCEMENT property,
        // and per the house rule it belongs in a deterministic arm (inject a short deadline, stub
        // the worker, assert the path taken) rather than in a wall-clock bound on a gating lane.
        // See docs/BACKLOG.md RUST-INDEX-LOCK-WALLCLOCK-FLAKE.
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

    #[test]
    fn simultaneous_first_sidecar_creation_retries_with_a_fresh_target() {
        let dir = tempdir().unwrap();
        let directory = AnchoredDirectory::open(dir.path()).unwrap();
        let lock_path = lock_path_for(&dir.path().join(".tg_index"));
        let sidecar_path = os_lock_path_for(&lock_path);
        let name = sidecar_path.file_name().unwrap();
        let first = directory.target(name).unwrap();
        let second = directory.target(name).unwrap();
        let barrier = Arc::new(std::sync::Barrier::new(3));
        let (result_tx, result_rx) = std::sync::mpsc::channel();

        for target in [first, second] {
            let barrier = Arc::clone(&barrier);
            let result_tx = result_tx.clone();
            thread::spawn(move || {
                barrier.wait();
                let result = try_os_file_lock_target(&target);
                drop(target);
                result_tx.send(result).unwrap();
            });
        }
        barrier.wait();

        let first_result = result_rx
            .recv_timeout(Duration::from_secs(5))
            .expect("first sidecar create attempt must finish")
            .expect("first sidecar create attempt must not fail");
        let second_result = result_rx
            .recv_timeout(Duration::from_secs(5))
            .expect("second sidecar create attempt must finish")
            .expect("EEXIST or byte-lock contention must request a retry");
        let (held, retry_needed) = match (first_result, second_result) {
            (Some(held), None) | (None, Some(held)) => (held, true),
            _ => panic!("exactly one simultaneous first acquisition must hold the sidecar"),
        };
        assert!(retry_needed);

        assert!(
            try_os_file_lock_target(&directory.target(name).unwrap())
                .unwrap()
                .is_none(),
            "a fresh opener must observe the held byte-range lock"
        );
        drop(held);
        let refreshed = directory.target(name).unwrap();
        assert!(
            try_os_file_lock_target(&refreshed).unwrap().is_some(),
            "re-anchoring after EEXIST must open and lock the persistent sidecar"
        );
        assert!(sidecar_path.exists());
    }

    #[test]
    fn waiter_reanchors_legacy_lock_after_sidecar_owner_releases() {
        let dir = tempdir().unwrap();
        let index_path = dir.path().join(".tg_index");
        let lock_path = lock_path_for(&index_path);
        let holder = IndexLockGuard::acquire_with(
            &index_path,
            Duration::from_secs(3),
            Duration::from_secs(1),
            Duration::from_millis(5),
        )
        .unwrap();
        let directory = AnchoredDirectory::open(dir.path()).unwrap();
        let (contended_tx, contended_rx) = std::sync::mpsc::channel();
        let (acquired_tx, acquired_rx) = std::sync::mpsc::channel();
        let waiter = thread::spawn(move || {
            let result = IndexLockGuard::acquire_at_with_contention_hook(
                &lock_path,
                &directory,
                Duration::from_secs(4),
                Duration::from_secs(1),
                Duration::from_millis(5),
                write_owner_record,
                || {
                    let _ = contended_tx.send(());
                },
            );
            acquired_tx.send(result.is_ok()).unwrap();
        });

        contended_rx
            .recv_timeout(Duration::from_secs(2))
            .expect("waiter must prove it attempted the held sidecar");
        drop(holder);
        assert!(
            acquired_rx
                .recv_timeout(Duration::from_secs(4))
                .expect("waiter must finish after release"),
            "waiter must refresh the old lock leaf anchor and acquire successfully"
        );
        waiter.join().unwrap();
    }

    #[test]
    fn token_checked_release_preserves_a_substituted_successor_lock() {
        let dir = tempdir().unwrap();
        let index_path = dir.path().join(".tg_index");
        let lock_path = lock_path_for(&index_path);
        #[cfg(windows)]
        let displaced_path = dir.path().join("displaced-lock");
        let successor = b"999999\nsuccessor-token\n";
        let guard = IndexLockGuard::acquire_with(
            &index_path,
            Duration::from_secs(3),
            Duration::from_secs(1),
            Duration::from_millis(5),
        )
        .unwrap();
        #[cfg(windows)]
        let owned_bytes = std::fs::read(&lock_path).unwrap();

        let result = release_lock_with(&guard.location, &guard.token, || {
            #[cfg(unix)]
            std::fs::write(&lock_path, successor)?;
            #[cfg(windows)]
            {
                std::fs::rename(&lock_path, &displaced_path)?;
                std::fs::write(&lock_path, successor)?;
            }
            Ok(())
        });

        #[cfg(unix)]
        assert!(result.is_ok());
        #[cfg(windows)]
        assert!(result.is_err(), "the successor identity must be refused");
        assert_eq!(std::fs::read(&lock_path).unwrap(), successor);
        drop(guard);
        assert_eq!(std::fs::read(&lock_path).unwrap(), successor);
        #[cfg(windows)]
        assert_eq!(std::fs::read(displaced_path).unwrap(), owned_bytes);
    }
}

#[cfg(test)]
mod token_write_tests {
    include!("../tests/support/index_lock_token_write.rs");
}

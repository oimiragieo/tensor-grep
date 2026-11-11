//! Native, bounded pair tokenization and ONNX CPU scoring. No network or Python inference.

use anyhow::{anyhow, bail, Context, Result};
use ort::{
    session::{RunOptions, Session},
    value::Tensor,
};
use sha2::{Digest, Sha256};
use std::fs::{File, OpenOptions};
use std::io::Read;
use std::path::{Path, PathBuf};
use std::sync::{mpsc, Arc, Mutex, OnceLock, TryLockError};
use std::time::{Duration, Instant};
use tokenizers::{Tokenizer, TruncationParams};

struct Scorer {
    runtime: PathBuf,
    _runtime_handles: Vec<std::fs::File>,
    _runtime_assets: Vec<(PathBuf, PinnedAsset)>,
    model: PinnedAsset,
    tokenizer_asset: PinnedAsset,
    session: Session,
    tokenizer: Tokenizer,
    #[cfg(windows)]
    _runtime_modules: crate::cross_encoder_runtime::LoadedLibraries,
}

static SCORER: OnceLock<Mutex<Option<Scorer>>> = OnceLock::new();

#[cfg(test)]
thread_local! {
    static ASSET_HASHES: std::cell::Cell<usize> = const { std::cell::Cell::new(0) };
    static ASSET_BYTES_READ: std::cell::Cell<usize> = const { std::cell::Cell::new(0) };
}

#[derive(Clone, Debug, Eq, PartialEq)]
struct AssetIdentity {
    size: u64,
    #[cfg(unix)]
    dev: u64,
    #[cfg(unix)]
    ino: u64,
    #[cfg(unix)]
    mtime: i64,
    #[cfg(unix)]
    mtime_nsec: i64,
    #[cfg(unix)]
    ctime: i64,
    #[cfg(unix)]
    ctime_nsec: i64,
    #[cfg(windows)]
    volume: u32,
    #[cfg(windows)]
    file_index: u64,
    #[cfg(windows)]
    creation_time: u64,
    #[cfg(windows)]
    last_write_time: u64,
    #[cfg(windows)]
    attributes: u32,
    #[cfg(not(any(unix, windows)))]
    modified: Option<std::time::SystemTime>,
}

#[derive(Debug)]
struct PinnedAsset {
    _file: File,
    identity: AssetIdentity,
}

#[derive(Debug)]
struct VerifiedAsset {
    pinned: PinnedAsset,
    bytes: Vec<u8>,
}

#[cfg(windows)]
#[repr(C)]
#[derive(Default)]
struct WinFileTime {
    low: u32,
    high: u32,
}

#[cfg(windows)]
#[repr(C)]
#[derive(Default)]
struct WinFileInformation {
    attributes: u32,
    creation_time: WinFileTime,
    access_time: WinFileTime,
    write_time: WinFileTime,
    volume: u32,
    size_high: u32,
    size_low: u32,
    links: u32,
    file_index_high: u32,
    file_index_low: u32,
}

#[cfg(windows)]
#[link(name = "kernel32")]
unsafe extern "system" {
    fn GetFileInformationByHandle(
        file: *mut std::ffi::c_void,
        information: *mut WinFileInformation,
    ) -> i32;
}

#[cfg(windows)]
fn windows_file_identity(file: &File) -> Result<(u32, u64)> {
    use std::os::windows::io::AsRawHandle;
    let mut information = WinFileInformation::default();
    if unsafe { GetFileInformationByHandle(file.as_raw_handle(), &mut information) } == 0 {
        return Err(std::io::Error::last_os_error().into());
    }
    Ok((
        information.volume,
        (u64::from(information.file_index_high) << 32) | u64::from(information.file_index_low),
    ))
}

fn identity(metadata: &std::fs::Metadata, _file: Option<&File>) -> Result<AssetIdentity> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        Ok(AssetIdentity {
            size: metadata.len(),
            dev: metadata.dev(),
            ino: metadata.ino(),
            mtime: metadata.mtime(),
            mtime_nsec: metadata.mtime_nsec(),
            ctime: metadata.ctime(),
            ctime_nsec: metadata.ctime_nsec(),
        })
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        let (volume, file_index) = windows_file_identity(
            _file.context("cross-encoder asset identity requires an open handle")?,
        )?;
        Ok(AssetIdentity {
            size: metadata.file_size(),
            volume,
            file_index,
            creation_time: metadata.creation_time(),
            last_write_time: metadata.last_write_time(),
            attributes: metadata.file_attributes(),
        })
    }
    #[cfg(not(any(unix, windows)))]
    {
        Ok(AssetIdentity {
            size: metadata.len(),
            modified: metadata.modified().ok(),
        })
    }
}

fn metadata_matches(identity: &AssetIdentity, metadata: &std::fs::Metadata) -> Result<bool> {
    #[cfg(unix)]
    {
        Ok(*identity == self::identity(metadata, None)?)
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        Ok(identity.size == metadata.file_size()
            && identity.creation_time == metadata.creation_time()
            && identity.last_write_time == metadata.last_write_time()
            && identity.attributes == metadata.file_attributes())
    }
    #[cfg(not(any(unix, windows)))]
    {
        Ok(*identity == self::identity(metadata, None)?)
    }
}

fn check_regular(metadata: &std::fs::Metadata, size: usize) -> Result<()> {
    if !metadata.is_file() || metadata.len() != size as u64 {
        bail!("cross-encoder refuses nonregular or wrong-size asset");
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        if metadata.file_attributes() & 0x400 != 0 {
            bail!("cross-encoder refuses reparse asset");
        }
    }
    Ok(())
}

fn open_asset(path: &Path, size: usize) -> Result<PinnedAsset> {
    let before = std::fs::symlink_metadata(path)?;
    check_regular(&before, size)?;
    let mut options = OpenOptions::new();
    options.read(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK);
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::OpenOptionsExt;
        // Keep initialized assets immutable while their verified objects are cached.
        options.custom_flags(0x00200000).share_mode(1);
    }
    let opened = options.open(path)?;
    let metadata = opened.metadata()?;
    check_regular(&metadata, size)?;
    let opened_identity = identity(&metadata, Some(&opened))?;
    if !metadata_matches(&opened_identity, &before)?
        || !metadata_matches(&opened_identity, &std::fs::symlink_metadata(path)?)?
    {
        bail!("cross-encoder asset changed while opening");
    }
    Ok(PinnedAsset {
        _file: opened,
        identity: opened_identity,
    })
}

fn check_deadline(deadline: Option<Instant>) -> Result<()> {
    if deadline.is_some_and(|end| Instant::now() >= end) {
        bail!("cross-encoder shared deadline exceeded during asset verification");
    }
    Ok(())
}

fn verified_asset(
    path: &Path,
    size: usize,
    digest: &str,
    deadline: Option<Instant>,
) -> Result<VerifiedAsset> {
    check_deadline(deadline)?;
    let mut pinned = open_asset(path, size)?;
    let mut bytes = Vec::with_capacity(size);
    let mut hasher = Sha256::new();
    let mut buffer = [0_u8; 65536];
    let max_bytes = size.saturating_add(1);
    while bytes.len() < max_bytes {
        check_deadline(deadline)?;
        let limit = (max_bytes - bytes.len()).min(buffer.len());
        let count = pinned._file.read(&mut buffer[..limit])?;
        if count == 0 {
            break;
        }
        bytes.extend_from_slice(&buffer[..count]);
        hasher.update(&buffer[..count]);
        #[cfg(test)]
        ASSET_BYTES_READ.with(|total| total.set(total.get() + count));
    }
    check_deadline(deadline)?;
    let actual = format!("{:x}", hasher.finalize());
    #[cfg(test)]
    ASSET_HASHES.with(|count| count.set(count.get() + 1));
    if bytes.len() != size || actual != digest {
        bail!("cross-encoder asset checksum mismatch");
    }
    if pinned.identity != identity(&pinned._file.metadata()?, Some(&pinned._file))?
        || !metadata_matches(&pinned.identity, &std::fs::symlink_metadata(path)?)?
    {
        bail!("cross-encoder asset changed while verifying");
    }
    Ok(VerifiedAsset { pinned, bytes })
}

#[cfg(test)]
fn verified_bytes(path: &str, size: usize, digest: &str) -> Result<Vec<u8>> {
    Ok(verified_asset(Path::new(path), size, digest, None)?.bytes)
}

fn create_session(model: &[u8]) -> Result<Session> {
    Ok(Session::builder()?
        .with_intra_threads(1)
        .map_err(|e| anyhow!(e.to_string()))?
        .with_inter_threads(1)
        .map_err(|e| anyhow!(e.to_string()))?
        .commit_from_memory(model)?)
}

fn create_tokenizer(bytes: &[u8]) -> Result<Tokenizer> {
    let mut tokenizer = Tokenizer::from_bytes(bytes).map_err(|e| anyhow!(e))?;
    tokenizer.with_padding(None);
    tokenizer
        .with_truncation(Some(TruncationParams {
            max_length: 256,
            ..Default::default()
        }))
        .map_err(|e| anyhow!(e))?;
    Ok(tokenizer)
}

fn verify_cached_runtime(scorer: &Scorer, deadline: Instant) -> Result<()> {
    for (index, &(name, size, digest)) in crate::cross_encoder_pins::LIBRARIES.iter().enumerate() {
        check_deadline(Some(deadline))?;
        let (path, pinned) = &scorer._runtime_assets[index];
        let current = open_asset(path, size)?;
        if current.identity != pinned.identity {
            drop(current);
            let _replacement = verified_asset(path, size, digest, Some(deadline))?;
            bail!("cross-encoder runtime changed; restart the process");
        }
        if path.file_name().is_none_or(|file| file != name) {
            bail!("cross-encoder cached runtime identity mismatch");
        }
    }
    Ok(())
}

fn verified_replacement_if_changed(
    path: &Path,
    size: usize,
    digest: &str,
    pinned_identity: &AssetIdentity,
    deadline: Instant,
) -> Result<Option<VerifiedAsset>> {
    check_deadline(Some(deadline))?;
    let current = open_asset(path, size)?;
    check_deadline(Some(deadline))?;
    if current.identity == *pinned_identity {
        return Ok(None);
    }
    drop(current);
    Ok(Some(verified_asset(path, size, digest, Some(deadline))?))
}

fn prefix(text: &str) -> &str {
    let mut end = text.len().min(16384);
    while !text.is_char_boundary(end) {
        end -= 1;
    }
    &text[..end]
}

pub fn scores(
    model: &str,
    tokenizer: &str,
    runtime: &str,
    query: &str,
    documents: &[String],
    budget_seconds: f64,
) -> Result<Vec<f32>> {
    if documents.len() > 20 || query.len() > 65536 || documents.iter().any(|d| d.len() > 1_048_576)
    {
        bail!("cross-encoder input exceeds candidate or text budget");
    }
    if !budget_seconds.is_finite() || budget_seconds <= 0.0 || budget_seconds > 10.0 {
        bail!("cross-encoder invalid time budget");
    }
    let end = Instant::now() + Duration::from_secs_f64(budget_seconds);
    let deadline = end.min(Instant::now() + Duration::from_secs(2));
    let mutex = SCORER.get_or_init(|| Mutex::new(None));
    let mut guard = loop {
        match mutex.try_lock() {
            Ok(guard) => break guard,
            Err(TryLockError::Poisoned(_)) => bail!("cross-encoder session lock poisoned"),
            Err(TryLockError::WouldBlock) if Instant::now() < deadline => {
                std::thread::sleep(Duration::from_millis(2));
            }
            Err(_) => bail!("cross-encoder session lock deadline exceeded"),
        }
    };
    let runtime_path = std::fs::canonicalize(runtime)?;
    if guard.is_none() {
        let model_asset = verified_asset(
            Path::new(model),
            91011230,
            "5d3e70fd0c9ff14b9b5169a51e957b7a9c74897afd0a35ce4bd318150c1d4d4a", // model sha256
            Some(end),
        )?;
        let tokenizer_asset = verified_asset(
            Path::new(tokenizer),
            711396,
            "d241a60d5e8f04cc1b2b3e9ef7a4921b27bf526d9f6050ab90f9267a1f9e5c66", // tokenizer sha256
            Some(end),
        )?;
        let runtime_handles = crate::cross_encoder_runtime::pin(&runtime_path)?;
        let runtime_parent = runtime_path.parent().context("runtime has no parent")?;
        #[cfg(not(windows))]
        let runtime_copy = tempfile::Builder::new()
            .prefix("tg-onnx-runtime-")
            .tempdir()?;
        if crate::cross_encoder_pins::LIBRARIES.is_empty() {
            bail!("no pinned cross-encoder runtime for this platform");
        }
        let mut runtime_assets = Vec::with_capacity(crate::cross_encoder_pins::LIBRARIES.len());
        for &(name, size, digest) in crate::cross_encoder_pins::LIBRARIES {
            let path = runtime_parent.join(name);
            let asset = verified_asset(&path, size, digest, Some(end))?;
            #[cfg(not(windows))]
            std::fs::write(runtime_copy.path().join(name), &asset.bytes)?;
            runtime_assets.push((path, asset.pinned));
        }
        let name = runtime_path
            .file_name()
            .context("runtime has no filename")?;
        if !crate::cross_encoder_pins::LIBRARIES
            .iter()
            .any(|(n, _, _)| name == *n)
        {
            bail!("refusing an unpinned runtime library name");
        }
        #[cfg(windows)]
        let load_path = runtime_path.clone();
        #[cfg(not(windows))]
        let load_path = runtime_copy.path().join(name);
        #[cfg(windows)]
        let runtime_modules = crate::cross_encoder_runtime::preload(&runtime_path)?;
        ort::init_from(load_path)?
            .with_name("tensor-grep-cross-encoder")
            .with_telemetry(false)
            .commit();
        // POSIX permits unlinking a loaded library; the loader retains its verified inode.
        #[cfg(not(windows))]
        drop(runtime_copy);
        let session = create_session(&model_asset.bytes)?;
        let vocabulary = create_tokenizer(&tokenizer_asset.bytes)?;
        *guard = Some(Scorer {
            runtime: runtime_path.clone(),
            _runtime_handles: runtime_handles,
            _runtime_assets: runtime_assets,
            model: model_asset.pinned,
            tokenizer_asset: tokenizer_asset.pinned,
            session,
            tokenizer: vocabulary,
            #[cfg(windows)]
            _runtime_modules: runtime_modules,
        });
    }
    let scorer = guard.as_mut().context("missing cross-encoder session")?;
    if scorer.runtime != runtime_path {
        bail!("cross-encoder runtime changed; restart the process");
    }
    verify_cached_runtime(scorer, end)?;
    let replacement_model = verified_replacement_if_changed(
        Path::new(model),
        91011230,
        "5d3e70fd0c9ff14b9b5169a51e957b7a9c74897afd0a35ce4bd318150c1d4d4a", // model sha256
        &scorer.model.identity,
        end,
    )?;
    let replacement_vocab_asset = verified_replacement_if_changed(
        Path::new(tokenizer),
        711396,
        "d241a60d5e8f04cc1b2b3e9ef7a4921b27bf526d9f6050ab90f9267a1f9e5c66", // tokenizer sha256
        &scorer.tokenizer_asset.identity,
        end,
    )?;
    if replacement_model.is_some() || replacement_vocab_asset.is_some() {
        let replacement_session = replacement_model
            .as_ref()
            .map(|asset| create_session(&asset.bytes))
            .transpose()?;
        let replacement_vocabulary = replacement_vocab_asset
            .as_ref()
            .map(|asset| create_tokenizer(&asset.bytes))
            .transpose()?;
        if let (Some(asset), Some(session)) = (replacement_model, replacement_session) {
            scorer.model = asset.pinned;
            scorer.session = session;
        }
        if let (Some(asset), Some(tokenizer)) = (replacement_vocab_asset, replacement_vocabulary) {
            scorer.tokenizer_asset = asset.pinned;
            scorer.tokenizer = tokenizer;
        }
    }
    if Instant::now() >= end {
        bail!("cross-encoder shared deadline exceeded during initialization");
    }
    if documents.is_empty() {
        return Ok(Vec::new());
    }
    let options = Arc::new(RunOptions::new()?);
    let cancel = Arc::clone(&options);
    let (finished, waiting) = mpsc::channel::<()>();
    let remaining = end.saturating_duration_since(Instant::now());
    // Disconnecting the sender on any return path immediately stops this timer. ONNX
    // cancellation is cooperative; callers still need an external process timeout.
    let _timer = std::thread::spawn(move || {
        if matches!(
            waiting.recv_timeout(remaining),
            Err(mpsc::RecvTimeoutError::Timeout)
        ) {
            let _ = cancel.terminate();
        }
    });
    let mut scores = Vec::with_capacity(documents.len());
    for document in documents {
        if Instant::now() >= end {
            bail!("cross-encoder shared deadline exceeded");
        }
        let encoding = scorer
            .tokenizer
            .encode((prefix(query), prefix(document)), true)
            .map_err(|e| anyhow!(e))?;
        let length = encoding.len();
        if length == 0 || length > 256 {
            bail!("cross-encoder tokenizer violated token budget");
        }
        let ids: Vec<i64> = encoding.get_ids().iter().map(|&v| i64::from(v)).collect();
        let mask: Vec<i64> = encoding
            .get_attention_mask()
            .iter()
            .map(|&v| i64::from(v))
            .collect();
        let types: Vec<i64> = encoding
            .get_type_ids()
            .iter()
            .map(|&v| i64::from(v))
            .collect();
        let output = scorer.session.run_with_options(
            ort::inputs![
                "input_ids" => Tensor::from_array(([1_usize, length], ids))?,
                "attention_mask" => Tensor::from_array(([1_usize, length], mask))?,
                "token_type_ids" => Tensor::from_array(([1_usize, length], types))?,
            ],
            &options,
        )?;
        let (_, values) = output[0].try_extract_tensor::<f32>()?;
        if values.len() != 1 || !values[0].is_finite() {
            bail!("cross-encoder model returned invalid logits");
        }
        scores.push(values[0]);
    }
    drop(finished);
    if Instant::now() >= end {
        bail!("cross-encoder shared deadline exceeded after inference");
    }
    Ok(scores)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn budgets_refuse_before_opening_assets() {
        let documents = vec![String::new(); 21];
        let err = scores("missing", "missing", "missing", "query", &documents, 10.0).unwrap_err();
        assert!(err.to_string().contains("budget"));
    }

    #[test]
    fn prefix_preserves_utf8_and_bound() {
        let text = "界".repeat(6000);
        let bounded = prefix(&text);
        assert_eq!(bounded.len(), 16383);
    }

    #[test]
    fn verified_read_has_a_valid_positive_control() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("asset");
        std::fs::write(&path, b"known").unwrap();
        let digest = format!("{:x}", Sha256::digest(b"known"));
        assert_eq!(
            verified_bytes(path.to_str().unwrap(), 5, &digest).unwrap(),
            b"known"
        );
        std::fs::write(&path, b"other").unwrap();
        assert!(verified_bytes(path.to_str().unwrap(), 5, &digest)
            .unwrap_err()
            .to_string()
            .contains("checksum"));
    }

    #[test]
    fn unchanged_identity_check_does_not_hash_asset_contents() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("asset");
        std::fs::write(&path, b"known").unwrap();
        let digest = format!("{:x}", Sha256::digest(b"known"));
        ASSET_HASHES.with(|count| count.set(0));
        ASSET_BYTES_READ.with(|count| count.set(0));
        let verified = verified_asset(&path, 5, &digest, None).unwrap();
        let identity = verified.pinned.identity.clone();
        drop(verified);
        assert_eq!(open_asset(&path, 5).unwrap().identity, identity);
        ASSET_HASHES.with(|count| assert_eq!(count.get(), 1));
        ASSET_BYTES_READ.with(|count| assert_eq!(count.get(), 5));
    }

    #[test]
    fn unchanged_scorer_asset_needs_no_full_read_or_hash() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("asset");
        std::fs::write(&path, b"known").unwrap();
        let digest = format!("{:x}", Sha256::digest(b"known"));
        ASSET_HASHES.with(|count| count.set(0));
        ASSET_BYTES_READ.with(|count| count.set(0));
        let verified = verified_asset(&path, 5, &digest, None).unwrap();
        let identity = verified.pinned.identity.clone();
        assert_eq!(ASSET_HASHES.with(|count| count.get()), 1);
        assert_eq!(ASSET_BYTES_READ.with(|count| count.get()), 5);

        let warm = verified_replacement_if_changed(
            &path,
            5,
            &digest,
            &identity,
            Instant::now() + Duration::from_secs(1),
        )
        .unwrap();
        assert!(warm.is_none());
        assert_eq!(ASSET_HASHES.with(|count| count.get()), 1);
        assert_eq!(ASSET_BYTES_READ.with(|count| count.get()), 5);
    }

    #[test]
    fn changed_scorer_asset_is_reverified_and_deletion_is_refused() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("asset");
        let replacement = directory.path().join("replacement");
        std::fs::write(&path, b"known").unwrap();
        let digest = format!("{:x}", Sha256::digest(b"known"));
        let verified = verified_asset(&path, 5, &digest, None).unwrap();
        let identity = verified.pinned.identity.clone();
        let modified = std::fs::metadata(&path).unwrap().modified().unwrap();
        drop(verified);

        std::fs::write(&replacement, b"other").unwrap();
        OpenOptions::new()
            .write(true)
            .open(&replacement)
            .unwrap()
            .set_times(std::fs::FileTimes::new().set_modified(modified))
            .unwrap();
        #[cfg(windows)]
        std::fs::remove_file(&path).unwrap();
        std::fs::rename(&replacement, &path).unwrap();
        assert!(verified_replacement_if_changed(
            &path,
            5,
            &digest,
            &identity,
            Instant::now() + Duration::from_secs(1),
        )
        .unwrap_err()
        .to_string()
        .contains("checksum"));

        std::fs::remove_file(&path).unwrap();
        assert!(verified_replacement_if_changed(
            &path,
            5,
            &digest,
            &identity,
            Instant::now() + Duration::from_secs(1),
        )
        .is_err());
    }

    #[test]
    fn expired_verification_deadline_refuses_before_reading() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("asset");
        std::fs::write(&path, b"known").unwrap();
        let digest = format!("{:x}", Sha256::digest(b"known"));
        ASSET_HASHES.with(|count| count.set(0));
        ASSET_BYTES_READ.with(|count| count.set(0));
        assert!(verified_asset(
            &path,
            5,
            &digest,
            Some(Instant::now() - Duration::from_secs(1))
        )
        .unwrap_err()
        .to_string()
        .contains("shared deadline"));
        ASSET_HASHES.with(|count| assert_eq!(count.get(), 0));
        ASSET_BYTES_READ.with(|count| assert_eq!(count.get(), 0));
    }

    #[test]
    fn real_assets_reuse_the_verified_scorer_when_configured() {
        let Ok(root) = std::env::var("TG_CROSS_ENCODER_TEST_ASSETS") else {
            return;
        };
        let root = PathBuf::from(root);
        let model = root.join("model.onnx");
        let tokenizer = root.join("tokenizer.json");
        let runtime = root.join(crate::cross_encoder_pins::LIBRARIES[0].0);
        let query = "retry failed requests";
        let documents = vec![
            "def retry_request(): return backoff()".to_owned(),
            "class Invoice: pass".to_owned(),
        ];
        ASSET_HASHES.with(|count| count.set(0));
        ASSET_BYTES_READ.with(|count| count.set(0));

        let cold_start = Instant::now();
        let cold = scores(
            model.to_str().unwrap(),
            tokenizer.to_str().unwrap(),
            runtime.to_str().unwrap(),
            query,
            &documents,
            10.0,
        )
        .unwrap();
        let cold_seconds = cold_start.elapsed().as_secs_f64();
        let cold_hashes = ASSET_HASHES.with(std::cell::Cell::get);
        let cold_bytes = ASSET_BYTES_READ.with(std::cell::Cell::get);
        assert_eq!(cold_hashes, crate::cross_encoder_pins::LIBRARIES.len() + 2);
        assert_eq!(
            cold_bytes,
            91011230
                + 711396
                + crate::cross_encoder_pins::LIBRARIES
                    .iter()
                    .map(|x| x.1)
                    .sum::<usize>()
        );

        let warm_start = Instant::now();
        let warm = scores(
            model.to_str().unwrap(),
            tokenizer.to_str().unwrap(),
            runtime.to_str().unwrap(),
            query,
            &documents,
            10.0,
        )
        .unwrap();
        let warm_seconds = warm_start.elapsed().as_secs_f64();
        assert_eq!(cold, warm);
        assert_eq!(ASSET_HASHES.with(std::cell::Cell::get), cold_hashes);
        assert_eq!(ASSET_BYTES_READ.with(std::cell::Cell::get), cold_bytes);
        eprintln!(
            "real cross-encoder assets: cold={cold_seconds:.3}s warm={warm_seconds:.3}s, verified_reads={cold_hashes}, verified_bytes={cold_bytes}"
        );

        assert!(scores(
            model.to_str().unwrap(),
            tokenizer.to_str().unwrap(),
            runtime.to_str().unwrap(),
            query,
            &[],
            10.0,
        )
        .unwrap()
        .is_empty());
        assert_eq!(ASSET_HASHES.with(std::cell::Cell::get), cold_hashes);
        assert_eq!(ASSET_BYTES_READ.with(std::cell::Cell::get), cold_bytes);

        let directory = tempfile::tempdir().unwrap();
        let corrupt_model = directory.path().join("corrupt-model.onnx");
        let mut file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&corrupt_model)
            .unwrap();
        file.set_len(91011230).unwrap();
        std::io::Write::write_all(&mut file, b"x").unwrap();
        drop(file);
        assert!(scores(
            corrupt_model.to_str().unwrap(),
            tokenizer.to_str().unwrap(),
            runtime.to_str().unwrap(),
            query,
            &[],
            10.0,
        )
        .unwrap_err()
        .to_string()
        .contains("checksum"));
        assert_eq!(ASSET_HASHES.with(std::cell::Cell::get), cold_hashes + 1);
        assert_eq!(
            ASSET_BYTES_READ.with(std::cell::Cell::get),
            cold_bytes + 91011230
        );
        assert!(scores(
            directory
                .path()
                .join("deleted-model.onnx")
                .to_str()
                .unwrap(),
            tokenizer.to_str().unwrap(),
            runtime.to_str().unwrap(),
            query,
            &[],
            10.0,
        )
        .is_err());
        assert!(scores(
            model.to_str().unwrap(),
            tokenizer.to_str().unwrap(),
            model.to_str().unwrap(),
            query,
            &[],
            10.0,
        )
        .unwrap_err()
        .to_string()
        .contains("runtime changed"));
    }

    #[test]
    fn same_size_same_mtime_replacement_and_deletion_are_detected() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("asset");
        let replacement = directory.path().join("replacement");
        std::fs::write(&path, b"known").unwrap();
        let old_identity = open_asset(&path, 5).unwrap().identity;
        let modified = std::fs::metadata(&path).unwrap().modified().unwrap();
        std::fs::write(&replacement, b"other").unwrap();
        OpenOptions::new()
            .write(true)
            .open(&replacement)
            .unwrap()
            .set_times(std::fs::FileTimes::new().set_modified(modified))
            .unwrap();
        assert_eq!(
            std::fs::metadata(&replacement).unwrap().modified().unwrap(),
            modified
        );
        #[cfg(windows)]
        std::fs::remove_file(&path).unwrap();
        std::fs::rename(&replacement, &path).unwrap();
        assert_ne!(open_asset(&path, 5).unwrap().identity, old_identity);
        let digest = format!("{:x}", Sha256::digest(b"known"));
        assert!(verified_asset(&path, 5, &digest, None)
            .unwrap_err()
            .to_string()
            .contains("checksum"));
        std::fs::remove_file(&path).unwrap();
        assert!(open_asset(&path, 5).is_err());
    }

    #[cfg(unix)]
    #[test]
    fn fifo_is_refused_without_waiting_for_a_writer() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("fifo");
        let name = std::ffi::CString::new(path.to_str().unwrap()).unwrap();
        assert_eq!(unsafe { libc::mkfifo(name.as_ptr(), 0o600) }, 0);
        assert!(verified_bytes(path.to_str().unwrap(), 5, "unused")
            .unwrap_err()
            .to_string()
            .contains("nonregular"));
    }
}

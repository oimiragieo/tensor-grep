//! Native, bounded pair tokenization and ONNX CPU scoring. No network or Python inference.

use anyhow::{anyhow, bail, Context, Result};
use ort::{
    session::{RunOptions, Session},
    value::Tensor,
};
use sha2::{Digest, Sha256};
use std::fs::OpenOptions;
use std::io::Read;
use std::path::PathBuf;
use std::sync::{mpsc, Arc, Mutex, OnceLock, TryLockError};
use std::time::{Duration, Instant};
use tokenizers::{Tokenizer, TruncationParams};

struct Scorer {
    runtime: PathBuf,
    _runtime_handles: Vec<std::fs::File>,
    session: Session,
    tokenizer: Tokenizer,
}

static SCORER: OnceLock<Mutex<Option<Scorer>>> = OnceLock::new();

fn verified_bytes(path: &str, size: usize, digest: &str) -> Result<Vec<u8>> {
    let before = std::fs::symlink_metadata(path)?;
    if !before.is_file() || before.len() != size as u64 {
        bail!("cross-encoder refuses nonregular or wrong-size asset");
    }
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
        options.custom_flags(0x00200000);
    }
    let opened = options.open(path)?;
    let metadata = opened.metadata()?;
    if !metadata.is_file() || metadata.len() != size as u64 {
        bail!("cross-encoder refuses nonregular or wrong-size opened asset");
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        if metadata.file_attributes() & 0x400 != 0 {
            bail!("cross-encoder refuses reparse asset");
        }
    }
    let mut bytes = Vec::with_capacity(size);
    opened.take((size + 1) as u64).read_to_end(&mut bytes)?;
    if bytes.len() != size || format!("{:x}", Sha256::digest(&bytes)) != digest {
        bail!("cross-encoder asset checksum mismatch");
    }
    Ok(bytes)
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
    let model_bytes = verified_bytes(
        model,
        91011230,
        "5d3e70fd0c9ff14b9b5169a51e957b7a9c74897afd0a35ce4bd318150c1d4d4a",
    )?;
    let tokenizer_bytes = verified_bytes(
        tokenizer,
        711396,
        "d241a60d5e8f04cc1b2b3e9ef7a4921b27bf526d9f6050ab90f9267a1f9e5c66",
    )?;
    let runtime_path = std::fs::canonicalize(runtime)?;
    if guard.is_none() {
        let runtime_handles = crate::cross_encoder_runtime::pin(&runtime_path)?;
        let runtime_parent = runtime_path.parent().context("runtime has no parent")?;
        #[cfg(not(windows))]
        let runtime_copy = tempfile::Builder::new()
            .prefix("tg-onnx-runtime-")
            .tempdir()?;
        if crate::cross_encoder_pins::LIBRARIES.is_empty() {
            bail!("no pinned cross-encoder runtime for this platform");
        }
        for &(name, size, digest) in crate::cross_encoder_pins::LIBRARIES {
            let bytes = verified_bytes(
                runtime_parent
                    .join(name)
                    .to_str()
                    .context("invalid runtime path")?,
                size,
                digest,
            )?;
            #[cfg(not(windows))]
            std::fs::write(runtime_copy.path().join(name), bytes)?;
            #[cfg(windows)]
            drop(bytes);
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
        ort::init_from(load_path)?
            .with_name("tensor-grep-cross-encoder")
            .with_telemetry(false)
            .commit();
        // POSIX permits unlinking a loaded library; the loader retains its verified inode.
        #[cfg(not(windows))]
        drop(runtime_copy);
        let session = Session::builder()?
            .with_intra_threads(1)
            .map_err(|e| anyhow!(e.to_string()))?
            .with_inter_threads(1)
            .map_err(|e| anyhow!(e.to_string()))?
            .commit_from_memory(&model_bytes)?;
        let mut tokenizer = Tokenizer::from_bytes(tokenizer_bytes).map_err(|e| anyhow!(e))?;
        tokenizer.with_padding(None);
        tokenizer
            .with_truncation(Some(TruncationParams {
                max_length: 256,
                ..Default::default()
            }))
            .map_err(|e| anyhow!(e))?;
        *guard = Some(Scorer {
            runtime: runtime_path.clone(),
            _runtime_handles: runtime_handles,
            session,
            tokenizer,
        });
    }
    let scorer = guard.as_mut().context("missing cross-encoder session")?;
    if scorer.runtime != runtime_path {
        bail!("cross-encoder runtime changed; restart the process");
    }
    if Instant::now() >= end {
        bail!("cross-encoder shared deadline exceeded during initialization");
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

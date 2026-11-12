use super::create_checkpoint;
#[cfg(windows)]
use super::{create_checkpoint_with_publisher, CheckpointArtifact, MAX_CHECKPOINT_INDEX_BYTES};
use std::fs;
#[cfg(windows)]
use std::io::{BufRead, BufReader, Read, Write};
use std::path::{Path, PathBuf};
#[cfg(windows)]
use std::process::{Child, Command, ExitStatus, Stdio};
#[cfg(windows)]
use std::sync::mpsc;
#[cfg(windows)]
use std::thread;
#[cfg(windows)]
use std::time::{Duration, Instant};
use tempfile::TempDir;
use tensor_grep_rs::safe_write::AnchoredDirectory;
#[cfg(windows)]
use tensor_grep_rs::safe_write::AtomicPublishError;

#[cfg(windows)]
const CREATED_AT: &str = "2026-11-07T12:00:00Z";

fn project() -> (TempDir, PathBuf, PathBuf) {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path().to_path_buf();
    let file = root.join("fixture.txt");
    fs::write(&file, b"snapshot contents\n").unwrap();
    (dir, root, file)
}

fn storage(root: &Path) -> PathBuf {
    root.join(".tensor-grep").join("checkpoints")
}

#[cfg(windows)]
fn index_path(root: &Path) -> PathBuf {
    storage(root).join("index.json")
}

#[cfg(windows)]
fn record(id: &str, root: &Path) -> serde_json::Value {
    serde_json::json!({
        "version": 1,
        "checkpoint_id": id,
        "mode": "filesystem-snapshot",
        "root": root.to_string_lossy(),
        "created_at": "2020-01-01T00:00:00Z",
        "file_count": 1,
        "label": "preserve this field"
    })
}

#[cfg(windows)]
fn seed_index(root: &Path) -> Vec<u8> {
    let path = index_path(root);
    fs::create_dir_all(path.parent().unwrap()).unwrap();
    let bytes = serde_json::to_vec_pretty(&vec![record("prior-checkpoint", root)]).unwrap();
    fs::write(path, &bytes).unwrap();
    bytes
}

#[cfg(windows)]
fn create_with<F>(
    file: &Path,
    id: &str,
    publish: F,
) -> anyhow::Result<super::super::CheckpointCreateSummary>
where
    F: FnMut(
        CheckpointArtifact,
        &tensor_grep_rs::safe_write::AnchoredTarget,
        &[u8],
    ) -> Result<(), AtomicPublishError>,
{
    create_checkpoint_with_publisher(file.to_str().unwrap(), id, CREATED_AT, publish)
}

#[cfg(windows)]
#[test]
fn successful_publication_keeps_snapshot_metadata_and_prior_record_consistent() {
    let (_dir, root, file) = project();
    let prior = record("prior-checkpoint", &root);
    let original = seed_index(&root);
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(index_path(&root), fs::Permissions::from_mode(0o600)).unwrap();
    }

    let summary = create_with(
        &file,
        "ckpt-success-1",
        |artifact, target, bytes| match artifact {
            CheckpointArtifact::Metadata => target.publish_atomic_new(bytes),
            CheckpointArtifact::Index => target.publish_atomic_preserving_permissions(bytes),
        },
    )
    .unwrap();

    assert_eq!(summary.checkpoint_id, "ckpt-success-1");
    let records: Vec<serde_json::Value> =
        serde_json::from_slice(&fs::read(index_path(&root)).unwrap()).unwrap();
    assert_eq!(records.len(), 2);
    assert!(
        records.contains(&prior),
        "unknown prior fields must survive the RMW"
    );
    assert_eq!(records[0]["checkpoint_id"], summary.checkpoint_id);
    let checkpoint_dir = storage(&root).join(&summary.checkpoint_id);
    let metadata: serde_json::Value =
        serde_json::from_slice(&fs::read(checkpoint_dir.join("metadata.json")).unwrap()).unwrap();
    assert_eq!(metadata["checkpoint_id"], summary.checkpoint_id);
    assert_eq!(metadata["file_count"], summary.file_count);
    assert_eq!(
        fs::read(checkpoint_dir.join("snapshot").join("fixture.txt")).unwrap(),
        b"snapshot contents\n"
    );
    assert!(!storage(&root).join(".index.json.lock").exists());
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        assert_eq!(
            fs::metadata(index_path(&root))
                .unwrap()
                .permissions()
                .mode()
                & 0o777,
            0o600
        );
    }
    #[cfg(not(unix))]
    assert!(fs::read(index_path(&root)).unwrap().len() > original.len());
}

#[cfg(windows)]
#[test]
fn oversized_checkpoint_catalog_is_refused_without_changing_visible_state() {
    let (_dir, root, file) = project();
    let index = index_path(&root);
    fs::create_dir_all(index.parent().unwrap()).unwrap();
    let mut prior = record("prior-checkpoint", &root);
    prior["label"] = serde_json::Value::String("x".repeat(MAX_CHECKPOINT_INDEX_BYTES as usize));
    let old_index = serde_json::to_vec_pretty(&vec![prior]).unwrap();
    assert!(old_index.len() as u64 > MAX_CHECKPOINT_INDEX_BYTES);
    fs::write(&index, &old_index).unwrap();

    let mut publications = Vec::new();
    let result = create_with(&file, "ckpt-oversized-index", |artifact, target, bytes| {
        publications.push(artifact);
        match artifact {
            CheckpointArtifact::Metadata => target.publish_atomic_new(bytes),
            CheckpointArtifact::Index => target.publish_atomic_preserving_permissions(bytes),
        }
    });

    let error = result.expect_err("oversized catalog must be refused before index publication");
    assert!(format!("{error:#}").contains("read limit"), "{error:#}");
    assert_eq!(publications, [CheckpointArtifact::Metadata]);
    assert_eq!(fs::read(&index).unwrap(), old_index);
    assert!(!storage(&root).join("ckpt-oversized-index").exists());
}

#[test]
#[cfg(unix)]
fn checkpoint_create_ownership_refuses_before_storage_mutation() {
    let (_dir, root, file) = project();
    let error = create_checkpoint(file.to_str().unwrap()).expect_err("Unix must fail closed");
    assert!(format!("{error:#}").contains("unsupported on this platform"));
    assert!(!root.join(".tensor-grep").exists());
}

#[cfg(windows)]
#[test]
fn checkpoint_create_ownership_keeps_native_windows_creation() {
    let (_dir, root, file) = project();
    let summary = create_checkpoint(file.to_str().unwrap()).expect("Windows native create works");
    let checkpoint_dir = storage(&root).join(&summary.checkpoint_id);
    let records: Vec<serde_json::Value> =
        serde_json::from_slice(&fs::read(index_path(&root)).unwrap()).unwrap();
    assert_eq!(records[0]["checkpoint_id"], summary.checkpoint_id);
    assert_eq!(
        fs::read(checkpoint_dir.join("snapshot").join("fixture.txt")).unwrap(),
        b"snapshot contents\n"
    );
}

#[cfg(windows)]
#[test]
fn metadata_failure_removes_only_when_platform_proves_cleanup_ownership() {
    let (_dir, root, file) = project();
    let old_index = seed_index(&root);
    let result = create_with(&file, "ckpt-metadata-fail", |artifact, _, _| {
        assert_eq!(artifact, CheckpointArtifact::Metadata);
        Err(AtomicPublishError {
            published: false,
            error: anyhow::anyhow!("injected metadata temp creation failure"),
        })
    });

    let _error = result.expect_err("metadata publication fault must be returned");
    assert_eq!(fs::read(index_path(&root)).unwrap(), old_index);
    let checkpoint_dir = storage(&root).join("ckpt-metadata-fail");
    #[cfg(windows)]
    assert!(!checkpoint_dir.exists());
    #[cfg(unix)]
    {
        assert!(checkpoint_dir.exists());
        let displayed = format!("{_error:#}");
        assert!(displayed.contains(&checkpoint_dir.display().to_string()));
        assert!(displayed.contains("unsupported"));
    }
}

#[cfg(windows)]
#[test]
fn preexisting_metadata_leaf_is_not_replaced_before_index_publication() {
    let (_dir, root, file) = project();
    let checkpoint_id = "ckpt-metadata-collision";
    let metadata_path = storage(&root).join(checkpoint_id).join("metadata.json");
    let result = create_with(&file, checkpoint_id, |artifact, target, bytes| {
        if artifact == CheckpointArtifact::Metadata {
            fs::write(&metadata_path, b"unowned metadata sentinel").unwrap();
            target.publish_atomic_new(bytes)
        } else {
            target.publish_atomic_preserving_permissions(bytes)
        }
    });

    assert!(result.is_err(), "metadata collision must abort publication");
    assert!(
        !index_path(&root).exists(),
        "no index record may point at collided metadata"
    );
    #[cfg(windows)]
    assert!(!storage(&root).join(checkpoint_id).exists());
    #[cfg(unix)]
    {
        assert_eq!(
            fs::read(&metadata_path).unwrap(),
            b"unowned metadata sentinel"
        );
    }
}

#[cfg(windows)]
#[test]
fn prepublication_publisher_error_preserves_old_json_and_prior_records() {
    let (_dir, root, file) = project();
    let old_index = seed_index(&root);
    let result = create_with(&file, "ckpt-prepublish-fail", |artifact, target, bytes| {
        if artifact == CheckpointArtifact::Metadata {
            return target.publish_atomic_new(bytes);
        }
        Err(AtomicPublishError {
            published: false,
            error: anyhow::anyhow!("injected unpublished index publisher error"),
        })
    });

    let _error = result.expect_err("unpublished index failure must abort checkpoint creation");
    let index_bytes = fs::read(index_path(&root)).unwrap();
    assert_eq!(
        index_bytes, old_index,
        "unpublished error changed the old index"
    );
    let records: Vec<serde_json::Value> = serde_json::from_slice(&index_bytes).unwrap();
    assert_eq!(records, [record("prior-checkpoint", &root)]);
    let checkpoint_dir = storage(&root).join("ckpt-prepublish-fail");
    #[cfg(windows)]
    assert!(!checkpoint_dir.exists());
    #[cfg(unix)]
    {
        assert!(checkpoint_dir.exists());
        assert!(format!("{_error:#}").contains(&checkpoint_dir.display().to_string()));
    }
    assert!(!storage(&root).join(".index.json.lock").exists());
}

#[cfg(windows)]
#[test]
fn reported_postpublication_error_never_deletes_a_visible_index_target() {
    let (_dir, root, file) = project();
    seed_index(&root);
    let result = create_with(
        &file,
        "ckpt-postsync-fail",
        |artifact, target, bytes| match artifact {
            CheckpointArtifact::Metadata => target.publish_atomic_new(bytes),
            CheckpointArtifact::Index => {
                target.publish_atomic_preserving_permissions(bytes)?;
                Err(AtomicPublishError {
                    published: true,
                    error: anyhow::anyhow!(
                        "injected error returned after visible index replacement"
                    ),
                })
            }
        },
    );

    assert!(result.is_err());
    let records: Vec<serde_json::Value> =
        serde_json::from_slice(&fs::read(index_path(&root)).unwrap()).unwrap();
    assert_eq!(records.len(), 2);
    assert_eq!(records[0]["checkpoint_id"], "ckpt-postsync-fail");
    let checkpoint_dir = storage(&root).join("ckpt-postsync-fail");
    assert!(checkpoint_dir.join("metadata.json").is_file());
    assert_eq!(
        fs::read(checkpoint_dir.join("snapshot").join("fixture.txt")).unwrap(),
        b"snapshot contents\n"
    );
}

#[cfg(windows)]
#[test]
fn collided_checkpoint_directory_is_not_cleaned_or_reused() {
    let (_dir, root, file) = project();
    let collided = storage(&root).join("ckpt-collision");
    fs::create_dir_all(&collided).unwrap();
    fs::write(collided.join("owner.txt"), b"unowned sentinel").unwrap();

    let result = create_with(
        &file,
        "ckpt-collision",
        |artifact, target, bytes| match artifact {
            CheckpointArtifact::Metadata => target.publish_atomic_new(bytes),
            CheckpointArtifact::Index => target.publish_atomic_preserving_permissions(bytes),
        },
    );

    assert!(result.is_err());
    assert_eq!(
        fs::read(collided.join("owner.txt")).unwrap(),
        b"unowned sentinel"
    );
    assert!(!index_path(&root).exists());
}

#[cfg(windows)]
#[test]
fn cleanup_after_checkpoint_parent_replacement_stays_on_the_owned_directory() {
    let (_dir, root, file) = project();
    let storage = storage(&root);
    let outside = root.join("outside");
    let moved = root.join("moved-checkpoints");
    let checkpoint_id = "ckpt-parent-swap";
    fs::create_dir_all(outside.join(checkpoint_id)).unwrap();
    fs::write(
        outside.join(checkpoint_id).join("owner.txt"),
        b"outside sentinel",
    )
    .unwrap();
    let storage_anchor = AnchoredDirectory::open(&root)
        .unwrap()
        .open_or_create_child(std::ffi::OsStr::new(".tensor-grep"))
        .unwrap()
        .open_or_create_child(std::ffi::OsStr::new("checkpoints"))
        .unwrap();

    let result = create_with(&file, checkpoint_id, |artifact, target, bytes| {
        if artifact == CheckpointArtifact::Index {
            return target.publish_atomic_preserving_permissions(bytes);
        }
        target.publish_atomic_new(bytes)?;
        #[cfg(unix)]
        {
            fs::rename(&storage, &moved)?;
            std::os::unix::fs::symlink(&outside, &storage)?;
            assert!(storage_anchor.validate_current().is_err());
            Ok(())
        }
        #[cfg(windows)]
        {
            assert!(fs::rename(&storage, &moved).is_err());
            assert!(storage_anchor.validate_current().is_ok());
            Err(AtomicPublishError {
                published: true,
                error: anyhow::anyhow!("parent rename refused while anchored"),
            })
        }
    });

    assert!(result.is_err());
    #[cfg(windows)]
    assert!(!moved.join(checkpoint_id).exists());
    #[cfg(unix)]
    {
        assert!(moved.join(checkpoint_id).is_dir());
        assert!(moved.join(checkpoint_id).join("metadata.json").is_file());
    }
    #[cfg(windows)]
    assert!(!storage.join(checkpoint_id).exists());
    assert_eq!(
        fs::read(outside.join(checkpoint_id).join("owner.txt")).unwrap(),
        b"outside sentinel"
    );
    assert!(!outside.join(".index.json.lock").exists());
}

#[test]
fn anchored_lock_uses_the_python_sidecar_spelling() {
    let dir = tempfile::tempdir().unwrap();
    let index = dir.path().join("index.json");
    let anchor = AnchoredDirectory::open(dir.path()).unwrap();
    let lock_path = dir.path().join(".index.json.lock");
    {
        let _guard = tensor_grep_rs::index_lock::IndexLockGuard::acquire_anchored(&index, &anchor)
            .expect("anchored sidecar lock must acquire");
        assert!(lock_path.exists());
        let content = fs::read_to_string(&lock_path).unwrap();
        assert!(content.contains('\n'));
    }
    assert!(!lock_path.exists());
}

#[cfg(windows)]
fn wait_child_bounded(child: &mut Child, timeout: Duration) -> std::io::Result<ExitStatus> {
    let deadline = Instant::now() + timeout;
    loop {
        if let Some(status) = child.try_wait()? {
            return Ok(status);
        }
        if Instant::now() >= deadline {
            let _ = child.kill();
            let _ = child.wait();
            return Err(std::io::Error::new(
                std::io::ErrorKind::TimedOut,
                format!("checkpoint Python child exceeded {timeout:?}"),
            ));
        }
        thread::park_timeout(Duration::from_millis(10));
    }
}

#[cfg(windows)]
#[test]
fn concurrent_python_and_native_creation_keep_both_index_records() {
    // Both real checkpoint paths rendezvous through bounded event channels before racing.
    let (_dir, root, file) = project();
    let package_root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..");
    let python = std::env::var_os("UV_PROJECT_ENVIRONMENT")
        .map(PathBuf::from)
        .map(|environment| {
            if cfg!(windows) {
                environment.join("Scripts").join("python.exe")
            } else {
                environment.join("bin").join("python")
            }
        })
        .unwrap_or_else(|| PathBuf::from("python3"));
    let script = "import sys\nfrom tensor_grep.cli import checkpoint_store\nsys.stdout.write('READY\\n'); sys.stdout.flush()\nsys.stdin.buffer.read(1)\nsys.stdout.write('GO\\n'); sys.stdout.flush()\nprint(checkpoint_store.create_checkpoint(sys.argv[1]).checkpoint_id)\n";
    let mut child = Command::new(python)
        .args(["-c", script, root.to_str().unwrap()])
        .env("PYTHONPATH", package_root.join("src"))
        .env("PYTHONNOUSERSITE", "1")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit())
        .spawn()
        .unwrap();
    let stdout = child.stdout.take().unwrap();
    let (ready_tx, ready_rx) = mpsc::sync_channel(1);
    let (go_tx, go_rx) = mpsc::sync_channel(1);
    let (output_tx, output_rx) = mpsc::sync_channel(1);
    thread::spawn(move || {
        let mut stdout = BufReader::new(stdout);
        let mut ready = String::new();
        let ready_result = stdout.read_line(&mut ready).map(|_| ready);
        if ready_tx.send(ready_result).is_err() {
            return;
        }
        let mut go = String::new();
        let go_result = stdout.read_line(&mut go).map(|_| go);
        if go_tx.send(go_result).is_err() {
            return;
        }
        let mut output = String::new();
        let _ = output_tx.send(stdout.read_to_string(&mut output).map(|_| output));
    });
    let ready = match ready_rx.recv_timeout(Duration::from_secs(5)) {
        Ok(Ok(ready)) => ready,
        Ok(Err(error)) => {
            let _ = child.kill();
            let _ = child.wait();
            panic!("could not read Python readiness event: {error}");
        }
        Err(error) => {
            let _ = child.kill();
            let _ = child.wait();
            panic!("Python checkpoint child did not become ready within 5s: {error}");
        }
    };
    assert_eq!(ready.trim(), "READY");

    let (native_ready_tx, native_ready_rx) = mpsc::sync_channel(1);
    let (native_start_tx, native_start_rx) = mpsc::sync_channel(1);
    let (native_done_tx, native_done_rx) = mpsc::sync_channel(1);
    let native_file = file.clone();
    thread::spawn(move || {
        if native_ready_tx.send(()).is_err() {
            return;
        }
        let result = native_start_rx
            .recv_timeout(Duration::from_secs(5))
            .map_err(|error| anyhow::anyhow!("native checkpoint start gate failed: {error}"))
            .and_then(|()| create_checkpoint(native_file.to_str().unwrap()));
        let _ = native_done_tx.send(result);
    });
    native_ready_rx
        .recv_timeout(Duration::from_secs(5))
        .expect("native checkpoint worker did not reach its start gate");
    let mut stdin = child.stdin.take().unwrap();
    stdin.write_all(b"x").unwrap();
    drop(stdin);
    let go = match go_rx.recv_timeout(Duration::from_secs(5)) {
        Ok(Ok(go)) => go,
        Ok(Err(error)) => {
            let _ = child.kill();
            let _ = child.wait();
            panic!("could not read Python checkpoint start event: {error}");
        }
        Err(error) => {
            let _ = child.kill();
            let _ = child.wait();
            panic!("Python checkpoint did not reach its create call within 5s: {error}");
        }
    };
    assert_eq!(go.trim(), "GO");
    native_start_tx
        .send(())
        .expect("native checkpoint start gate disconnected");
    let status = wait_child_bounded(&mut child, Duration::from_secs(30))
        .expect("Python checkpoint process must finish within 30s");
    let output = output_rx
        .recv_timeout(Duration::from_secs(5))
        .expect("Python checkpoint output reader did not finish")
        .expect("could not read Python checkpoint output");
    assert!(status.success(), "python checkpoint failed: {output}");
    let python_id = output.lines().last().unwrap().trim();
    let native_id = native_done_rx
        .recv_timeout(Duration::from_secs(30))
        .expect("native checkpoint process did not finish within 30s")
        .expect("native checkpoint creation failed")
        .checkpoint_id;
    let records: Vec<serde_json::Value> =
        serde_json::from_slice(&fs::read(index_path(&root)).unwrap()).unwrap();
    let ids: Vec<&str> = records
        .iter()
        .filter_map(|record| record["checkpoint_id"].as_str())
        .collect();
    assert_eq!(records.len(), 2);
    assert!(ids.contains(&python_id));
    assert!(ids.contains(&native_id.as_str()));
}

#[cfg(windows)]
#[test]
fn existing_restricted_index_dacl_survives_atomic_replacement_when_icacls_is_available() {
    let (_dir, root, file) = project();
    let path = index_path(&root);
    fs::create_dir_all(path.parent().unwrap()).unwrap();
    fs::write(&path, b"[]").unwrap();
    let user = match std::env::var("USERNAME") {
        Ok(user) if !user.is_empty() => user,
        _ => return,
    };
    let acl = Command::new("icacls.exe")
        .arg(&path)
        .args(["/inheritance:r", "/grant:r"])
        .arg(format!("{user}:(M)"))
        .output();
    if !acl.is_ok_and(|output| output.status.success()) {
        eprintln!("skipping DACL check because icacls could not set a restricted test ACL");
        return;
    }
    let entries = |path: &Path| -> Option<Vec<String>> {
        let output = Command::new("icacls.exe").arg(path).output().ok()?;
        if !output.status.success() {
            return None;
        }
        Some(
            String::from_utf8_lossy(&output.stdout)
                .lines()
                .map(str::trim)
                .filter(|line| line.contains(":("))
                .map(str::to_owned)
                .collect(),
        )
    };
    let before = entries(&path).expect("read the restricted test DACL");
    create_with(
        &file,
        "ckpt-acl",
        |artifact, target, bytes| match artifact {
            CheckpointArtifact::Metadata => target.publish_atomic_new(bytes),
            CheckpointArtifact::Index => target.publish_atomic_preserving_permissions(bytes),
        },
    )
    .unwrap();
    assert_eq!(entries(&path).unwrap(), before);
}

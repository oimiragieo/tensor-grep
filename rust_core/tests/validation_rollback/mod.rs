use super::{
    collect_validation_rollback_snapshots, restore_validation_rollback_snapshots,
    retained_validation_targets, AnchoredTarget, AstBackend, ValidationRollbackSnapshot,
};
use std::collections::BTreeMap;
use std::path::Path;
use std::sync::mpsc;
use std::time::Duration;
use tensor_grep_rs::backend_ast::MAX_REWRITE_FILE_BYTES;

fn try_symlink_file(target: &Path, link: &Path) -> std::io::Result<()> {
    #[cfg(unix)]
    {
        std::os::unix::fs::symlink(target, link)
    }
    #[cfg(windows)]
    {
        std::os::windows::fs::symlink_file(target, link)
    }
    #[cfg(not(any(unix, windows)))]
    {
        let _ = (target, link);
        Err(std::io::Error::new(
            std::io::ErrorKind::Unsupported,
            "symlinks not supported on this platform",
        ))
    }
}

#[test]
fn validation_rollback_refuses_leaf_symlink_and_restores_regular_file() {
    let dir = tempfile::tempdir().unwrap();
    let file = dir.path().join("fixture.py");
    let original = b"def add(x, y): return x + y\n".to_vec();
    std::fs::write(&file, &original).unwrap();
    let outside = tempfile::tempdir().unwrap();
    let sentinel = outside.path().join("sentinel");
    std::fs::write(&sentinel, b"OUTSIDE_SENTINEL").unwrap();
    let mut snapshots = BTreeMap::new();
    snapshots.insert(
        file.to_string_lossy().to_string(),
        ValidationRollbackSnapshot {
            path: file.clone(),
            target: std::sync::Arc::new(AnchoredTarget::open(&file).unwrap()),
            bytes: original.clone(),
        },
    );

    std::fs::write(&file, b"rewritten\n").unwrap();
    assert!(restore_validation_rollback_snapshots(&snapshots).success);
    assert_eq!(std::fs::read(&file).unwrap(), original);
    std::fs::remove_file(&file).unwrap();
    if let Err(error) = try_symlink_file(&sentinel, &file) {
        eprintln!("skipping leaf symlink control because creation failed: {error}");
        return;
    }
    let summary = restore_validation_rollback_snapshots(&snapshots);
    assert!(!summary.success);
    assert_eq!(std::fs::read(&sentinel).unwrap(), b"OUTSIDE_SENTINEL");
}

#[test]
fn validation_rollback_snapshot_refuses_same_inode_growth_above_rewrite_limit() {
    let dir = tempfile::tempdir().unwrap();
    let file = dir.path().join("fixture.py");
    std::fs::write(&file, "def add(x, y): return x + y\n").unwrap();
    let plan = AstBackend::new()
        .plan_rewrites(
            "def $F($$$ARGS): return $EXPR",
            "lambda $$$ARGS: $EXPR",
            "python",
            file.to_str().unwrap(),
        )
        .unwrap();
    assert_eq!(plan.edits.len(), 1);
    let positive_control = collect_validation_rollback_snapshots(&plan.edits).unwrap();
    assert_eq!(positive_control.len(), 1);
    drop(positive_control);

    std::fs::OpenOptions::new()
        .write(true)
        .open(&file)
        .unwrap()
        .set_len(MAX_REWRITE_FILE_BYTES + 1)
        .unwrap();
    let error = collect_validation_rollback_snapshots(&plan.edits)
        .err()
        .expect("rollback snapshot must refuse content grown past the rewrite limit");
    assert!(format!("{error:#}").contains("read limit"), "{error:#}");
    assert_eq!(
        std::fs::metadata(&file).unwrap().len(),
        MAX_REWRITE_FILE_BYTES + 1
    );
}

#[test]
fn validation_rollback_stays_with_pinned_parent_after_swap() {
    let dir = tempfile::tempdir().unwrap();
    let repo = dir.path().join("repo");
    let moved = dir.path().join("moved-repo");
    let outside = dir.path().join("outside");
    std::fs::create_dir(&repo).unwrap();
    std::fs::create_dir(&outside).unwrap();
    let file = repo.join("fixture.py");
    let outside_file = outside.join("fixture.py");
    let original = b"def add(x, y): return x + y\n";
    std::fs::write(&file, original).unwrap();
    std::fs::write(&outside_file, b"OUTSIDE_SENTINEL\n").unwrap();
    let plan = AstBackend::new()
        .plan_rewrites(
            "def $F($$$ARGS): return $EXPR",
            "lambda $$$ARGS: $EXPR",
            "python",
            file.to_str().unwrap(),
        )
        .unwrap();
    let snapshots = collect_validation_rollback_snapshots(&plan.edits).unwrap();
    let targets = retained_validation_targets(&snapshots);
    assert_eq!(
        AstBackend::apply_rewrites_with_targets(&plan, &targets).unwrap(),
        1
    );

    #[cfg(unix)]
    {
        std::fs::rename(&repo, &moved).unwrap();
        std::os::unix::fs::symlink(&outside, &repo).unwrap();
    }
    #[cfg(windows)]
    assert!(std::fs::rename(&repo, &moved).is_err());

    let summary = restore_validation_rollback_snapshots(&snapshots);
    assert!(summary.success, "rollback failed: {:?}", summary.errors);
    assert_eq!(std::fs::read(&outside_file).unwrap(), b"OUTSIDE_SENTINEL\n");
    #[cfg(unix)]
    assert_eq!(std::fs::read(moved.join("fixture.py")).unwrap(), original);
    #[cfg(windows)]
    assert_eq!(std::fs::read(file).unwrap(), original);
}

#[test]
fn failed_validation_rollback_refuses_hardlink_leaf_swap() {
    let dir = tempfile::tempdir().unwrap();
    let repo = dir.path().join("repo");
    let outside = dir.path().join("outside");
    std::fs::create_dir(&repo).unwrap();
    std::fs::create_dir(&outside).unwrap();
    let file = repo.join("fixture.py");
    let outside_file = outside.join("fixture.py");
    let original = b"def add(x, y): return x + y\n";
    std::fs::write(&file, original).unwrap();
    std::fs::write(&outside_file, b"OUTSIDE_SENTINEL\n").unwrap();
    let plan = AstBackend::new()
        .plan_rewrites(
            "def $F($$$ARGS): return $EXPR",
            "lambda $$$ARGS: $EXPR",
            "python",
            file.to_str().unwrap(),
        )
        .unwrap();
    let snapshots = collect_validation_rollback_snapshots(&plan.edits).unwrap();
    let targets = retained_validation_targets(&snapshots);
    assert_eq!(
        AstBackend::apply_rewrites_with_targets(&plan, &targets).unwrap(),
        1
    );
    assert_ne!(std::fs::read(&file).unwrap(), original);

    let (ready_tx, ready_rx) = mpsc::channel();
    let (go_tx, go_rx) = mpsc::channel();
    let worker = std::thread::spawn(move || {
        ready_tx.send(()).unwrap();
        go_rx.recv_timeout(Duration::from_secs(2)).unwrap();
        restore_validation_rollback_snapshots(&snapshots)
    });
    ready_rx.recv_timeout(Duration::from_secs(2)).unwrap();
    std::fs::remove_file(&file).unwrap();
    std::fs::hard_link(&outside_file, &file).unwrap();
    go_tx.send(()).unwrap();
    let summary = worker.join().unwrap();

    assert!(
        !summary.success,
        "rollback must refuse a substituted hardlink"
    );
    assert_eq!(std::fs::read(&outside_file).unwrap(), b"OUTSIDE_SENTINEL\n");
    assert_eq!(std::fs::read(&file).unwrap(), b"OUTSIDE_SENTINEL\n");
}

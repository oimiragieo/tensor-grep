use super::{
    copy_with_limit, write_bytes_refuse_symlink, AnchoredDirectory, AnchoredTarget,
    AtomicPublishStage,
};
use std::io::{Cursor, Read};
use std::path::Path;
use std::sync::mpsc;
use std::time::Duration;

fn try_directory_reparse(target: &Path, link: &Path) -> std::io::Result<()> {
    #[cfg(unix)]
    {
        std::os::unix::fs::symlink(target, link)
    }
    #[cfg(windows)]
    {
        let output = std::process::Command::new("cmd.exe")
            .args(["/C", "mklink", "/J"])
            .arg(link)
            .arg(target)
            .output()?;
        if output.status.success() {
            Ok(())
        } else {
            Err(std::io::Error::other(
                String::from_utf8_lossy(&output.stderr).into_owned(),
            ))
        }
    }
    #[cfg(not(any(unix, windows)))]
    {
        let _ = (target, link);
        Err(std::io::Error::new(
            std::io::ErrorKind::Unsupported,
            "unsupported",
        ))
    }
}

#[test]
fn refuses_reparse_parent_and_preserves_ordinary_write_control() {
    let dir = tempfile::tempdir().unwrap();
    let outside = dir.path().join("outside");
    let link = dir.path().join("linked-parent");
    std::fs::create_dir(&outside).unwrap();
    let victim = outside.join("victim.py");
    std::fs::write(&victim, b"OUTSIDE_SENTINEL\n").unwrap();
    try_directory_reparse(&outside, &link).expect("directory reparse control must be available");

    let ordinary = dir.path().join("ordinary.py");
    std::fs::write(&ordinary, b"before\n").unwrap();
    write_bytes_refuse_symlink(&ordinary, b"after\n").unwrap();
    assert_eq!(std::fs::read(&ordinary).unwrap(), b"after\n");

    let result = write_bytes_refuse_symlink(&link.join("victim.py"), b"ATTACKER\n");
    assert!(result.is_err(), "parent reparse points must be refused");
    assert_eq!(std::fs::read(&victim).unwrap(), b"OUTSIDE_SENTINEL\n");
}

#[test]
fn anchored_parent_swap_is_bounded_and_never_writes_through_reparse() {
    let dir = tempfile::tempdir().unwrap();
    let parent = dir.path().join("parent");
    let moved = dir.path().join("moved-parent");
    let outside = dir.path().join("outside");
    std::fs::create_dir(&parent).unwrap();
    std::fs::create_dir(&outside).unwrap();
    std::fs::write(parent.join("victim"), b"inside-before").unwrap();
    std::fs::write(outside.join("victim"), b"OUTSIDE_SENTINEL").unwrap();
    let path = parent.join("victim");
    let (ready_tx, ready_rx) = mpsc::channel();
    let (go_tx, go_rx) = mpsc::channel();
    let worker_path = path.clone();
    let worker = std::thread::spawn(move || {
        let target = AnchoredTarget::open(&worker_path).unwrap();
        ready_tx.send(()).unwrap();
        go_rx.recv_timeout(Duration::from_secs(2)).unwrap();
        target.write_direct(b"inside-after")
    });
    ready_rx.recv_timeout(Duration::from_secs(2)).unwrap();

    #[cfg(unix)]
    {
        std::fs::rename(&parent, &moved).unwrap();
        std::os::unix::fs::symlink(&outside, &parent).unwrap();
    }
    #[cfg(windows)]
    {
        assert!(
            std::fs::rename(&parent, &moved).is_err(),
            "retained directory handles must deny delete sharing"
        );
    }
    go_tx.send(()).unwrap();
    worker.join().unwrap().unwrap();
    assert_eq!(
        std::fs::read(outside.join("victim")).unwrap(),
        b"OUTSIDE_SENTINEL"
    );
    #[cfg(unix)]
    assert_eq!(
        std::fs::read(moved.join("victim")).unwrap(),
        b"inside-after"
    );
    #[cfg(windows)]
    assert_eq!(
        std::fs::read(parent.join("victim")).unwrap(),
        b"inside-after"
    );
}

#[test]
fn leaf_swap_to_directory_reparse_is_refused() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("victim");
    let outside = dir.path().join("outside");
    std::fs::create_dir(&outside).unwrap();
    std::fs::write(&path, b"before").unwrap();
    std::fs::write(outside.join("victim"), b"OUTSIDE_SENTINEL").unwrap();
    let (ready_tx, ready_rx) = mpsc::channel();
    let (go_tx, go_rx) = mpsc::channel();
    let worker_path = path.clone();
    let worker = std::thread::spawn(move || {
        let target = AnchoredTarget::open(&worker_path).unwrap();
        ready_tx.send(()).unwrap();
        go_rx.recv_timeout(Duration::from_secs(2)).unwrap();
        target.write_direct(b"ATTACKER")
    });
    ready_rx.recv_timeout(Duration::from_secs(2)).unwrap();
    std::fs::remove_file(&path).unwrap();
    try_directory_reparse(&outside, &path).expect("leaf reparse control must be available");
    go_tx.send(()).unwrap();
    assert!(worker.join().unwrap().is_err());
    assert_eq!(
        std::fs::read(outside.join("victim")).unwrap(),
        b"OUTSIDE_SENTINEL"
    );
}

#[test]
fn atomic_publication_replaces_regular_file_and_reports_visible_commit() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("target.json");
    std::fs::write(&path, b"before").unwrap();
    let target = AnchoredTarget::open(&path).unwrap();
    target.publish_atomic(b"after").unwrap();
    assert_eq!(std::fs::read(&path).unwrap(), b"after");
}

#[test]
fn anchored_bounded_read_accepts_at_limit_and_refuses_oversized_file() {
    let dir = tempfile::tempdir().unwrap();
    let accepted = dir.path().join("accepted.json");
    std::fs::write(&accepted, b"exact").unwrap();
    assert_eq!(
        AnchoredTarget::open(&accepted)
            .unwrap()
            .read_bounded(5)
            .unwrap(),
        b"exact"
    );

    let oversized = dir.path().join("oversized.json");
    std::fs::write(&oversized, b"one byte over").unwrap();
    let error = AnchoredTarget::open(&oversized)
        .unwrap()
        .read_bounded(12)
        .expect_err("bounded read must refuse content above its limit");
    assert!(format!("{error:#}").contains("read limit"));
}

#[test]
fn atomic_new_publication_refuses_an_existing_leaf_without_changing_valid_json() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("metadata.json");
    let old_json = br#"{"checkpoint_id":"unowned"}"#;
    std::fs::write(&path, old_json).unwrap();
    let target = AnchoredTarget::open(&path).unwrap();

    let failure = target
        .publish_atomic_new(br#"{"checkpoint_id":"ours"}"#)
        .expect_err("new-only publication must refuse an existing target");

    assert!(!failure.published);
    assert_eq!(target.read().unwrap(), old_json);
    let _: serde_json::Value = serde_json::from_slice(&target.read().unwrap()).unwrap();
    for entry in std::fs::read_dir(dir.path()).unwrap() {
        assert!(
            !entry
                .unwrap()
                .file_name()
                .to_string_lossy()
                .starts_with(".tg_tmp_"),
            "failed new-only publication must remove its temp"
        );
    }
}

#[test]
fn atomic_new_publication_creates_a_valid_leaf_when_absent() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("metadata.json");
    let target = AnchoredTarget::open(&path).unwrap();
    target
        .publish_atomic_new(br#"{"checkpoint_id":"ours"}"#)
        .unwrap();
    let bytes = target.read().unwrap();
    let json: serde_json::Value = serde_json::from_slice(&bytes).unwrap();
    assert_eq!(json["checkpoint_id"], "ours");
}

#[test]
fn atomic_prepublication_stage_faults_clean_temp_and_preserve_valid_old_json() {
    let stages = [
        AtomicPublishStage::CreateTemp,
        AtomicPublishStage::Write,
        AtomicPublishStage::Flush,
        AtomicPublishStage::Replace,
        AtomicPublishStage::Validated,
    ];
    let old_json = br#"{"records":["old"]}"#;
    let new_json = br#"{"records":["new"]}"#;

    for failed_stage in stages {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("index.json");
        std::fs::write(&path, old_json).unwrap();
        let target = AnchoredTarget::open(&path).unwrap();
        let failure = target
            .atomic_write_inner(
                new_json,
                |_| Ok(()),
                true,
                true,
                true,
                |stage| {
                    if stage == failed_stage {
                        anyhow::bail!("injected failure at {stage:?}");
                    }
                    Ok(())
                },
            )
            .expect_err("the selected stage hook must fail publication");

        assert!(
            !failure.published,
            "{failed_stage:?} must remain unpublished"
        );
        assert!(
            format!("{:#}", failure.error).contains(&format!("{failed_stage:?}")),
            "the failed stage must remain identifiable"
        );
        assert_eq!(target.read().unwrap(), old_json);
        let _: serde_json::Value = serde_json::from_slice(&target.read().unwrap()).unwrap();
        for entry in std::fs::read_dir(dir.path()).unwrap() {
            let name = entry.unwrap().file_name();
            assert!(
                !name.to_string_lossy().starts_with(".tg_tmp_"),
                "temporary publication file survived {failed_stage:?}: {name:?}"
            );
        }
    }
}

#[test]
fn atomic_parent_sync_fault_is_published_and_keeps_valid_new_json() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("index.json");
    let old_json = br#"{"records":["old"]}"#;
    let new_json = br#"{"records":["new"]}"#;
    std::fs::write(&path, old_json).unwrap();
    let target = AnchoredTarget::open(&path).unwrap();
    let failure = target
        .atomic_write_inner(
            new_json,
            |_| Ok(()),
            true,
            true,
            true,
            |stage| {
                if stage == AtomicPublishStage::ParentSync {
                    anyhow::bail!("injected parent sync failure");
                }
                Ok(())
            },
        )
        .expect_err("the post-replacement parent sync hook must fail");

    assert!(failure.published);
    assert!(format!("{:#}", failure.error).contains("parent sync failure"));
    assert_eq!(target.read().unwrap(), new_json);
    let _: serde_json::Value = serde_json::from_slice(&target.read().unwrap()).unwrap();
    for entry in std::fs::read_dir(dir.path()).unwrap() {
        let name = entry.unwrap().file_name();
        assert!(!name.to_string_lossy().starts_with(".tg_tmp_"));
    }
}

#[test]
fn atomic_hook_parent_swap_stays_in_the_anchored_directory() {
    let dir = tempfile::tempdir().unwrap();
    let parent = dir.path().join("parent");
    let moved = dir.path().join("moved-parent");
    let outside = dir.path().join("outside");
    std::fs::create_dir(&parent).unwrap();
    std::fs::create_dir(&outside).unwrap();
    std::fs::write(parent.join("target"), b"before").unwrap();
    std::fs::write(outside.join("target"), b"OUTSIDE_SENTINEL").unwrap();
    let path = parent.join("target");
    let target = AnchoredTarget::open(&path).unwrap();
    target
        .write_atomic_with_hook(b"after", |_| {
            #[cfg(unix)]
            {
                std::fs::rename(&parent, &moved)?;
                std::os::unix::fs::symlink(&outside, &parent)?;
            }
            #[cfg(windows)]
            assert!(std::fs::rename(&parent, &moved).is_err());
            Ok(())
        })
        .unwrap();
    assert_eq!(
        std::fs::read(outside.join("target")).unwrap(),
        b"OUTSIDE_SENTINEL"
    );
    #[cfg(unix)]
    assert_eq!(std::fs::read(moved.join("target")).unwrap(), b"after");
    #[cfg(windows)]
    assert_eq!(std::fs::read(parent.join("target")).unwrap(), b"after");
}

#[test]
fn direct_write_refuses_bounded_hardlink_swap_before_truncation() {
    let dir = tempfile::tempdir().unwrap();
    let target_path = dir.path().join("target");
    let outside_path = dir.path().join("outside-sentinel");
    std::fs::write(&target_path, b"inside-before").unwrap();
    std::fs::write(&outside_path, b"OUTSIDE_SENTINEL").unwrap();

    let (ready_tx, ready_rx) = mpsc::channel();
    let (go_tx, go_rx) = mpsc::channel();
    let worker_path = target_path.clone();
    let worker = std::thread::spawn(move || {
        let target = AnchoredTarget::open(&worker_path).unwrap();
        ready_tx.send(()).unwrap();
        go_rx.recv_timeout(Duration::from_secs(5)).unwrap();
        target.write_direct(b"ATTACKER")
    });
    ready_rx.recv_timeout(Duration::from_secs(5)).unwrap();
    std::fs::remove_file(&target_path).unwrap();
    std::fs::hard_link(&outside_path, &target_path).unwrap();
    go_tx.send(()).unwrap();

    assert!(worker.join().unwrap().is_err());
    assert_eq!(std::fs::read(&outside_path).unwrap(), b"OUTSIDE_SENTINEL");
    assert_eq!(std::fs::read(&target_path).unwrap(), b"OUTSIDE_SENTINEL");

    let initially_linked = dir.path().join("initially-linked");
    std::fs::hard_link(&outside_path, &initially_linked).unwrap();
    assert!(AnchoredTarget::open(&initially_linked)
        .unwrap()
        .write_direct(b"ATTACKER")
        .is_err());
    assert_eq!(std::fs::read(&outside_path).unwrap(), b"OUTSIDE_SENTINEL");

    let ordinary = dir.path().join("ordinary");
    std::fs::write(&ordinary, b"before").unwrap();
    AnchoredTarget::open(&ordinary)
        .unwrap()
        .write_direct(b"after")
        .unwrap();
    assert_eq!(std::fs::read(&ordinary).unwrap(), b"after");
}

#[test]
fn direct_write_refuses_reused_leaf_identity_and_keeps_positive_control() {
    let dir = tempfile::tempdir().unwrap();
    let target_path = dir.path().join("target");
    let replacement_path = dir.path().join("replacement");
    std::fs::write(&target_path, b"before").unwrap();
    let target = AnchoredTarget::open(&target_path).unwrap();
    target.write_direct(b"published").unwrap();

    std::fs::write(&replacement_path, b"UNRELATED_SENTINEL").unwrap();
    std::fs::remove_file(&target_path).unwrap();
    std::fs::rename(&replacement_path, &target_path).unwrap();
    let replacement = std::fs::File::open(&target_path).unwrap();
    let reused_identity = super::leaf_identity::from_file(&replacement).unwrap();
    *target.leaf_identity.lock().unwrap() = Some(reused_identity);

    let rollback = target.write_direct(b"rollback");
    assert_eq!(std::fs::read(&target_path).unwrap(), b"UNRELATED_SENTINEL");
    assert!(
        rollback.is_err(),
        "a forced identity collision must not authorize the unrelated leaf"
    );

    AnchoredTarget::open(&target_path)
        .unwrap()
        .write_direct(b"legitimate rollback")
        .unwrap();
    assert_eq!(std::fs::read(&target_path).unwrap(), b"legitimate rollback");
}

#[test]
fn direct_create_is_exclusive_and_copy_rejects_hardlink_substitution() {
    let dir = tempfile::tempdir().unwrap();
    let source = dir.path().join("source");
    let missing = dir.path().join("missing");
    let outside_path = dir.path().join("outside-sentinel");
    std::fs::write(&source, b"copy-source").unwrap();
    std::fs::write(&outside_path, b"OUTSIDE_SENTINEL").unwrap();

    let new_target = AnchoredTarget::open(&missing).unwrap();
    new_target.write_direct(b"created").unwrap();
    assert_eq!(std::fs::read(&missing).unwrap(), b"created");

    let injected = dir.path().join("injected");
    let absent_anchor = AnchoredTarget::open(&injected).unwrap();
    std::fs::hard_link(&outside_path, &injected).unwrap();
    assert!(absent_anchor.write_direct(b"ATTACKER").is_err());
    assert_eq!(std::fs::read(&outside_path).unwrap(), b"OUTSIDE_SENTINEL");

    let copied = dir.path().join("copied");
    let copy_target = AnchoredTarget::open(&copied).unwrap();
    copy_target.copy_regular_file_from(&source).unwrap();
    assert_eq!(std::fs::read(&copied).unwrap(), b"copy-source");

    let existing = dir.path().join("existing-copy-target");
    std::fs::write(&existing, b"preserve-existing").unwrap();
    assert!(AnchoredTarget::open(&existing)
        .unwrap()
        .copy_regular_file_from(&source)
        .is_err());
    assert_eq!(std::fs::read(&existing).unwrap(), b"preserve-existing");

    let swapped = dir.path().join("swapped");
    std::fs::write(&swapped, b"inside").unwrap();
    let swapped_anchor = AnchoredTarget::open(&swapped).unwrap();
    std::fs::remove_file(&swapped).unwrap();
    std::fs::hard_link(&outside_path, &swapped).unwrap();
    assert!(swapped_anchor.copy_regular_file_from(&source).is_err());
    assert_eq!(std::fs::read(&outside_path).unwrap(), b"OUTSIDE_SENTINEL");

    let copy_injected = dir.path().join("copy-injected");
    let absent_copy_anchor = AnchoredTarget::open(&copy_injected).unwrap();
    std::fs::hard_link(&outside_path, &copy_injected).unwrap();
    assert!(absent_copy_anchor.copy_regular_file_from(&source).is_err());
    assert_eq!(std::fs::read(&outside_path).unwrap(), b"OUTSIDE_SENTINEL");
}

#[cfg(unix)]
#[test]
fn copy_destination_is_private_from_the_first_open() {
    use std::os::unix::fs::PermissionsExt;

    let dir = tempfile::tempdir().unwrap();
    let destination = dir.path().join("private-copy");
    let file = AnchoredTarget::open(&destination)
        .unwrap()
        .open_leaf_create_new()
        .unwrap();

    assert_eq!(file.metadata().unwrap().permissions().mode() & 0o777, 0o600);
}

struct GrowingReader {
    stage: u8,
}

impl Read for GrowingReader {
    fn read(&mut self, buffer: &mut [u8]) -> std::io::Result<usize> {
        let bytes: &[u8] = match self.stage {
            0 => b"1234",
            1 => b"5",
            _ => return Ok(0),
        };
        self.stage += 1;
        let size = bytes.len().min(buffer.len());
        buffer[..size].copy_from_slice(&bytes[..size]);
        Ok(size)
    }
}

#[test]
fn bounded_copy_refuses_growth_without_writing_past_the_limit() {
    let mut source = GrowingReader { stage: 0 };
    let mut destination = Vec::new();

    assert!(copy_with_limit(&mut source, &mut destination, 4).is_err());
    assert_eq!(destination, b"1234");

    let mut source = Cursor::new(b"1234");
    let mut destination = Vec::new();
    assert_eq!(
        copy_with_limit(&mut source, &mut destination, 4).unwrap(),
        4
    );
    assert_eq!(destination, b"1234");
}

#[test]
fn anchored_source_parent_swap_keeps_reads_inside_the_opened_directory() {
    use std::ffi::OsStr;
    use std::fs;

    let dir = tempfile::tempdir().unwrap();
    let root = dir.path().join("root");
    let source = root.join("source");
    let moved = root.join("moved-source");
    let outside = dir.path().join("outside");
    fs::create_dir_all(&source).unwrap();
    fs::create_dir(&outside).unwrap();
    fs::write(source.join("payload"), b"inside-root").unwrap();
    fs::write(outside.join("payload"), b"OUTSIDE_SENTINEL").unwrap();
    let root_anchor = AnchoredDirectory::open(&root).unwrap();
    let source_anchor = root_anchor.open_child(OsStr::new("source")).unwrap();

    #[cfg(unix)]
    {
        fs::rename(&source, &moved).unwrap();
        std::os::unix::fs::symlink(&outside, &source).unwrap();
    }
    #[cfg(windows)]
    assert!(fs::rename(&source, &moved).is_err());

    let mut opened = source_anchor
        .open_regular_file(OsStr::new("payload"))
        .unwrap();
    let mut bytes = Vec::new();
    opened.read_to_end(&mut bytes).unwrap();
    assert_eq!(bytes, b"inside-root");
    assert_eq!(
        fs::read(outside.join("payload")).unwrap(),
        b"OUTSIDE_SENTINEL"
    );
}

#[test]
fn anchored_read_link_refuses_names_outside_the_retained_directory() {
    let dir = tempfile::tempdir().unwrap();
    let anchor = AnchoredDirectory::open(dir.path()).unwrap();

    for name in [
        Path::new(".."),
        Path::new("../outside-link"),
        Path::new("/outside-link"),
    ] {
        assert!(anchor.read_link(name.as_os_str()).is_err());
    }
}

#[cfg(windows)]
#[test]
fn new_checkpoint_directories_use_a_protected_owner_only_dacl() {
    use std::ffi::OsStr;

    let dir = tempfile::tempdir().unwrap();
    let root = AnchoredDirectory::open(dir.path()).unwrap();
    let child = root.create_child(OsStr::new("private-checkpoint")).unwrap();
    let handle = child.handles.last().unwrap();

    assert!(super::windows::has_protected_owner_rights_ace(&handle, 0x001f_01ff, 0x03).unwrap());
}

#[cfg(windows)]
#[test]
fn opened_source_dacl_is_copied_with_its_protected_state_and_owner_rights_mask() {
    use std::ffi::OsStr;
    use std::fs::{self, OpenOptions};
    use std::os::windows::fs::OpenOptionsExt;

    const FILE_GENERIC_READ: u32 = 0x0012_0089;
    let dir = tempfile::tempdir().unwrap();
    let source_path = dir.path().join("source");
    let destination_path = dir.path().join("destination");
    fs::write(&source_path, b"source bytes").unwrap();
    let source_acl_handle = OpenOptions::new()
        .read(true)
        .write(true)
        .access_mode(
            super::windows::GENERIC_WRITE
                | super::windows::WRITE_DAC
                | super::windows::READ_CONTROL,
        )
        .open(&source_path)
        .unwrap();
    super::windows::set_file_dacl_for_test(&source_acl_handle, "D:P(A;;FR;;;OW)").unwrap();
    drop(source_acl_handle);

    let source_anchor = AnchoredDirectory::open(dir.path()).unwrap();
    let source = source_anchor
        .open_regular_file(OsStr::new("source"))
        .unwrap();
    let destination = AnchoredTarget::open(&destination_path).unwrap();
    destination
        .copy_opened_regular_file_from(source, 1024)
        .unwrap();

    let source_handle = fs::File::open(&source_path).unwrap();
    let destination_handle = fs::File::open(&destination_path).unwrap();
    let source_dacl = super::windows::dacl_signature(&source_handle).unwrap();
    let destination_dacl = super::windows::dacl_signature(&destination_handle).unwrap();
    assert_eq!(destination_dacl, source_dacl);
    assert!(
        super::windows::has_protected_owner_rights_ace(&source_handle, FILE_GENERIC_READ, 0)
            .unwrap()
    );
    assert!(super::windows::has_protected_owner_rights_ace(
        &destination_handle,
        FILE_GENERIC_READ,
        0
    )
    .unwrap());
}

#[test]
fn atomic_rewrite_of_hardlink_replaces_only_selected_name() {
    let dir = tempfile::tempdir().unwrap();
    let selected = dir.path().join("selected");
    let outside_path = dir.path().join("outside-sentinel");
    std::fs::write(&outside_path, b"OUTSIDE_SENTINEL").unwrap();
    std::fs::hard_link(&outside_path, &selected).unwrap();

    AnchoredTarget::open(&selected)
        .unwrap()
        .write_atomic_with_hook(b"replacement", |_| Ok(()))
        .unwrap();

    assert_eq!(std::fs::read(&selected).unwrap(), b"replacement");
    assert_eq!(std::fs::read(&outside_path).unwrap(), b"OUTSIDE_SENTINEL");
}

#[test]
fn atomic_replace_refuses_regular_leaf_swapped_at_last_prepublication_hook() {
    let dir = tempfile::tempdir().unwrap();
    let target_path = dir.path().join("index.json");
    let original_path = dir.path().join("original.json");
    let original = br#"{"records":["original"]}"#;
    let successor = br#"{"records":["SUCCESSOR_SENTINEL"]}"#;
    std::fs::write(&target_path, original).unwrap();
    let target = AnchoredTarget::open(&target_path).unwrap();

    let failure = target
        .atomic_write_inner(
            br#"{"records":["replacement"]}"#,
            |_| Ok(()),
            false,
            false,
            true,
            |stage| {
                if stage == AtomicPublishStage::Replace {
                    std::fs::rename(&target_path, &original_path)?;
                    std::fs::write(&target_path, successor)?;
                }
                Ok(())
            },
        )
        .expect_err("a leaf swapped after the last hook must fail identity validation");

    assert!(!failure.published);
    assert!(format!("{:#}", failure.error).contains("changed target identity"));
    assert_eq!(std::fs::read(&original_path).unwrap(), original);
    assert_eq!(std::fs::read(&target_path).unwrap(), successor);
    assert!(std::fs::read_dir(dir.path()).unwrap().all(|entry| {
        !entry
            .unwrap()
            .file_name()
            .to_string_lossy()
            .starts_with(".tg_tmp_")
    }));
}

#[test]
fn atomic_create_does_not_replace_a_leaf_created_after_identity_validation() {
    let dir = tempfile::tempdir().unwrap();
    let target_path = dir.path().join("new-index.json");
    let successor = br#"{"owner":"SUCCESSOR_SENTINEL"}"#;
    let target = AnchoredTarget::open(&target_path).unwrap();

    let failure = target
        .atomic_write_inner(
            br#"{"owner":"ours"}"#,
            |_| Ok(()),
            false,
            false,
            true,
            |stage| {
                if stage == AtomicPublishStage::Validated {
                    std::fs::write(&target_path, successor)?;
                }
                Ok(())
            },
        )
        .expect_err("publication must not replace a leaf that appeared after validation");

    assert!(!failure.published);
    assert_eq!(std::fs::read(&target_path).unwrap(), successor);
    assert!(std::fs::read_dir(dir.path()).unwrap().all(|entry| {
        !entry
            .unwrap()
            .file_name()
            .to_string_lossy()
            .starts_with(".tg_tmp_")
    }));
}

#[test]
fn anchored_remove_preserves_a_regular_successor_swapped_after_identity_check() {
    let dir = tempfile::tempdir().unwrap();
    let target_path = dir.path().join(".tg_index.lock");
    #[cfg(windows)]
    let displaced_path = dir.path().join("displaced-lock");
    let successor = b"999999\nsuccessor-token\n";
    std::fs::write(&target_path, b"123456\nowned-token\n").unwrap();
    let target = AnchoredTarget::open(&target_path).unwrap();

    let result = target.remove_file_with_hook(|| {
        #[cfg(unix)]
        std::fs::write(&target_path, successor)?;
        #[cfg(windows)]
        {
            std::fs::rename(&target_path, &displaced_path)?;
            std::fs::write(&target_path, successor)?;
        }
        Ok(())
    });

    #[cfg(unix)]
    assert!(result.is_ok());
    #[cfg(windows)]
    assert!(result.is_err(), "the changed leaf identity must be refused");
    assert_eq!(std::fs::read(&target_path).unwrap(), successor);
    #[cfg(windows)]
    assert_eq!(
        std::fs::read(&displaced_path).unwrap(),
        b"123456\nowned-token\n"
    );
}

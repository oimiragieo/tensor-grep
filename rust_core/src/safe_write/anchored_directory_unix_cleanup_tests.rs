use super::super::AnchoredDirectory;
use std::ffi::OsStr;
use std::fs;

#[test]
fn creation_succeeds_but_cleanup_refusal_preserves_moved_and_outside_sentinels() {
    let temp = tempfile::tempdir().unwrap();
    let parent = temp.path().join("checkpoints");
    let moved = temp.path().join("moved-checkpoints");
    let outside = temp.path().join("outside");
    fs::create_dir(&parent).unwrap();
    fs::create_dir_all(outside.join("owned")).unwrap();
    fs::write(outside.join("owned").join("owner.txt"), b"OUTSIDE_SENTINEL").unwrap();

    let anchor = AnchoredDirectory::open(&parent).unwrap();
    let owned = anchor.create_child(OsStr::new("owned")).unwrap();
    fs::write(owned.path().join("created.txt"), b"created through anchor").unwrap();
    assert!(
        owned.path().is_dir(),
        "legitimate anchored creation succeeds"
    );

    fs::rename(&parent, &moved).unwrap();
    std::os::unix::fs::symlink(&outside, &parent).unwrap();
    let refusal = owned
        .remove_owned_tree()
        .expect_err("Unix must refuse recursive cleanup without atomic directory ownership");

    assert!(format!("{refusal:#}").contains("unsupported"));
    assert_eq!(
        fs::read(moved.join("owned").join("created.txt")).unwrap(),
        b"created through anchor"
    );
    assert_eq!(
        fs::read(outside.join("owned").join("owner.txt")).unwrap(),
        b"OUTSIDE_SENTINEL"
    );
}

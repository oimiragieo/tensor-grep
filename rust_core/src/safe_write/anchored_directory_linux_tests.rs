use super::super::AnchoredDirectory;
use std::ffi::OsStr;
use std::fs;
use std::os::unix::fs::symlink;

#[test]
fn read_link_uses_the_opened_symlink_after_a_leaf_swap() {
    let temp = tempfile::tempdir().unwrap();
    let link = temp.path().join("link");
    let moved = temp.path().join("moved-link");
    symlink("first-target", &link).unwrap();
    let anchor = AnchoredDirectory::open(temp.path()).unwrap();
    assert_eq!(
        anchor.read_link(OsStr::new("link")).unwrap().unwrap(),
        "first-target"
    );

    let target = super::unix_impl::read_link_child_with_hook(&anchor, OsStr::new("link"), || {
        fs::rename(&link, &moved)?;
        symlink("successor-target", &link)
    })
    .unwrap()
    .expect("the opened leaf was a symlink");

    assert_eq!(target, "first-target");
    assert_eq!(fs::read_link(&link).unwrap(), "successor-target");
    assert_eq!(fs::read_link(&moved).unwrap(), "first-target");
}

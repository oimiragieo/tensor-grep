use super::super::AnchoredDirectory;
use std::ffi::OsStr;
use std::fs;
use std::path::Path;
use std::process::Command;

fn create_junction(link: &Path, target: &Path) -> bool {
    Command::new("cmd")
        .args(["/c", "mklink", "/J"])
        .arg(link)
        .arg(target)
        .output()
        .is_ok_and(|output| output.status.success())
}

#[test]
fn relative_open_if_and_exclusive_create_return_usable_handles() {
    let temp = tempfile::tempdir().unwrap();
    let anchor = AnchoredDirectory::open(temp.path()).unwrap();

    let shared = anchor
        .open_or_create_child(OsStr::new("shared"))
        .expect("guarded open-or-create creates a child directory");
    assert!(shared.path().is_dir());
    assert!(
        shared.remove_owned_tree().is_err(),
        "open-or-create does not claim deletion ownership"
    );

    let shared_again = anchor
        .open_or_create_child(OsStr::new("shared"))
        .expect("an existing child can be opened while another anchor is retained");
    assert!(shared_again.path().is_dir());

    let owned = anchor
        .create_child(OsStr::new("owned"))
        .expect("guarded FILE_CREATE returns the owner handle");
    assert!(
        anchor.create_child(OsStr::new("owned")).is_err(),
        "exclusive creation must refuse an existing child"
    );
    assert!(
        anchor.child_for_removal(OsStr::new("absent")).is_err(),
        "opening a missing child for removal must fail"
    );
    fs::create_dir(owned.path().join("nested")).unwrap();
    fs::write(
        owned.path().join("nested").join("marker.txt"),
        b"owned child",
    )
    .unwrap();
    owned
        .remove_owned_tree()
        .expect("the exclusive handle and recursive child opens retain cleanup authority");
    drop(owned);
    assert!(!temp.path().join("owned").exists());
}

#[test]
fn staged_child_with_one_character_name_is_published() {
    let temp = tempfile::tempdir().unwrap();
    let parent = AnchoredDirectory::open(temp.path()).unwrap();
    let child = parent
        .create_child(OsStr::new("x"))
        .expect("short target names meet the native rename buffer minimum");

    assert!(child.path().is_dir());
    child.remove_owned_tree().unwrap();
    drop(child);
    drop(parent);
    assert!(!temp.path().join("x").exists());
}

#[test]
fn no_replace_staging_rename_preserves_a_dangling_successor_junction() {
    use std::os::windows::fs::MetadataExt;

    let temp = tempfile::tempdir().unwrap();
    let parent = AnchoredDirectory::open(temp.path()).unwrap();
    let stage = super::windows_impl::create_staging_child_for_test(&parent).unwrap();
    let missing_target = temp.path().join("missing-target");
    let successor = temp.path().join("requested");
    let link_created = std::os::windows::fs::symlink_dir(&missing_target, &successor).is_ok()
        || create_junction(&successor, &missing_target);
    if !link_created {
        let _ = super::windows_impl::discard_staging_child_for_test(&stage);
        if std::env::var_os("TG_REQUIRE_SYMLINK_TESTS").is_some() {
            panic!("TG_REQUIRE_SYMLINK_TESTS set: cannot create a dangling directory link");
        }
        eprintln!("Windows did not permit a dangling directory-link fixture");
        return;
    }

    let publication = super::windows_impl::publish_staging_child_for_test(
        &stage,
        &parent,
        OsStr::new("requested"),
    );
    let publication_error =
        publication.expect_err("no-replace rename must report the successor leaf");
    let error_code = publication_error.chain().find_map(|cause| {
        cause
            .downcast_ref::<std::io::Error>()
            .and_then(std::io::Error::raw_os_error)
    });
    assert!(
        matches!(error_code, Some(80 | 183)),
        "expected an already-exists collision, got {publication_error:#}"
    );
    assert_eq!(
        fs::symlink_metadata(&successor).unwrap().file_attributes() & 0x0000_0400,
        0x0000_0400,
        "the successor junction remains at the requested name"
    );
    assert!(
        !missing_target.exists(),
        "publication must not create a target through the successor junction"
    );
    super::windows_impl::discard_staging_child_for_test(&stage).unwrap();
}

#[test]
fn staging_name_collision_with_dangling_junction_is_refused_before_create() {
    use std::os::windows::fs::MetadataExt;

    let temp = tempfile::tempdir().unwrap();
    let parent = AnchoredDirectory::open(temp.path()).unwrap();
    let missing_target = temp.path().join("missing-stage-target");
    let collision = temp.path().join(".tg_stage_controlled_collision");
    let link_created = std::os::windows::fs::symlink_dir(&missing_target, &collision).is_ok()
        || create_junction(&collision, &missing_target);
    if !link_created {
        if std::env::var_os("TG_REQUIRE_SYMLINK_TESTS").is_some() {
            panic!("TG_REQUIRE_SYMLINK_TESTS set: cannot create a dangling directory link");
        }
        eprintln!("Windows did not permit a dangling directory-link fixture");
        return;
    }

    let refusal = match super::windows_impl::create_staged_child_with_names_for_test(
        &parent,
        OsStr::new("destination"),
        || Ok(OsStr::new(".tg_stage_controlled_collision").to_os_string()),
    ) {
        Ok(_) => panic!("the no-follow stage-name probe accepted an occupied reparse leaf"),
        Err(error) => error,
    };

    assert!(format!("{refusal:#}").contains("reparse"));
    assert_ne!(
        fs::symlink_metadata(&collision).unwrap().file_attributes() & 0x0000_0400,
        0,
        "the existing staging-name reparse point remains unchanged"
    );
    assert!(!missing_target.exists());
    assert!(!temp.path().join("destination").exists());
}

#[test]
fn relative_open_if_refuses_existing_and_dangling_reparse_children() {
    use std::os::windows::fs::MetadataExt;

    let temp = tempfile::tempdir().unwrap();
    let outside = temp.path().join("outside");
    fs::create_dir(&outside).unwrap();
    fs::write(outside.join("sentinel.txt"), b"OUTSIDE_SENTINEL").unwrap();
    let junction = temp.path().join("junction");
    if !create_junction(&junction, &outside) {
        if std::env::var_os("TG_REQUIRE_SYMLINK_TESTS").is_some() {
            panic!("TG_REQUIRE_SYMLINK_TESTS set: cannot create a Windows junction");
        }
        eprintln!("skipping Windows reparse test: junction creation is unavailable");
        return;
    }
    let attributes = fs::symlink_metadata(&junction).unwrap().file_attributes();
    assert_ne!(
        attributes & 0x0000_0400,
        0,
        "fixture must be a reparse point"
    );

    let anchor = AnchoredDirectory::open(temp.path()).unwrap();
    assert!(
        anchor.open_or_create_child(OsStr::new("junction")).is_err(),
        "handle-bound reparse open must detect a junction"
    );
    assert_eq!(
        fs::read(outside.join("sentinel.txt")).unwrap(),
        b"OUTSIDE_SENTINEL"
    );

    let missing_target = temp.path().join("missing-target");
    let dangling = temp.path().join("dangling");
    let symlink_result = std::os::windows::fs::symlink_dir(&missing_target, &dangling);
    let dangling_created = symlink_result.is_ok()
        || fs::symlink_metadata(&dangling).is_ok()
        || create_junction(&dangling, &missing_target);
    if dangling_created {
        assert_ne!(
            fs::symlink_metadata(&dangling).unwrap().file_attributes() & 0x0000_0400,
            0,
            "dangling-link fixture must be a reparse point"
        );
        assert!(
            anchor.open_or_create_child(OsStr::new("dangling")).is_err(),
            "guarded no-follow open must refuse a dangling directory reparse point"
        );
        assert!(
            !missing_target.exists(),
            "guarded create must not create a directory through a dangling reparse point"
        );
    } else if std::env::var_os("TG_REQUIRE_SYMLINK_TESTS").is_some() {
        panic!("TG_REQUIRE_SYMLINK_TESTS set: cannot create a dangling directory link");
    } else {
        eprintln!("Windows did not permit a dangling directory-link fixture");
    }
}

#[test]
fn read_link_uses_the_opened_reparse_object_during_a_leaf_swap_attempt() {
    let temp = tempfile::tempdir().unwrap();
    let first_target = temp.path().join("first-target");
    let second_target = temp.path().join("second-target");
    let link = temp.path().join("link");
    let moved = temp.path().join("moved-link");
    fs::create_dir(&first_target).unwrap();
    fs::create_dir(&second_target).unwrap();
    if !create_junction(&link, &first_target) {
        if std::env::var_os("TG_REQUIRE_SYMLINK_TESTS").is_some() {
            panic!("TG_REQUIRE_SYMLINK_TESTS set: cannot create a Windows junction");
        }
        eprintln!("skipping Windows reparse test: junction creation is unavailable");
        return;
    }
    let anchor = AnchoredDirectory::open(temp.path()).unwrap();

    let replaced =
        super::windows_impl::read_link_child_with_hook(&anchor, OsStr::new("link"), || {
            fs::rename(&link, &moved)?;
            assert!(create_junction(&link, &second_target));
            Ok(())
        })
        .unwrap()
        .expect("the opened junction is a reparse point");

    assert_eq!(replaced, first_target);
    assert_eq!(fs::read_link(&link).unwrap(), second_target);
    assert_eq!(fs::read_link(&moved).unwrap(), first_target);
}

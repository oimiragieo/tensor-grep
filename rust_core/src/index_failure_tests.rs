use super::super::*;
use std::fs;
use tempfile::tempdir;

#[derive(Clone, Copy)]
enum Failure {
    Walk,
    EntryMetadata,
    Open,
    MmapMetadata,
    Map,
}

fn collect_with_failure(root: &Path, no_ignore: bool, failure: Failure) -> Result<Vec<FileEntry>> {
    match failure {
        Failure::Walk => collect_file_entries_from(
            root,
            std::iter::once(Err(std::io::Error::other("injected walk failure"))),
        ),
        Failure::EntryMetadata => {
            let entries = index_walker(root, no_ignore).build().collect::<Vec<_>>();
            fs::remove_file(root.join("a.txt"))?;
            collect_file_entries_from(root, entries.into_iter())
        }
        _ => collect_file_entries(root, no_ignore),
    }
}

fn extract_with_failure(path: &Path, failure: Failure) -> Result<FileTrigramHits> {
    match failure {
        Failure::Open => extract_file_trigrams(&path.with_file_name("missing.txt")),
        Failure::MmapMetadata => extract_file_trigrams_with(
            path,
            |_| Err(std::io::Error::other("injected metadata failure")),
            |_| unreachable!(),
        ),
        Failure::Map => extract_file_trigrams_with(
            path,
            |file| file.metadata(),
            |_| Err(std::io::Error::other("injected map failure")),
        ),
        _ => extract_file_trigrams(path),
    }
}

#[test]
fn full_and_incremental_builds_propagate_injected_walk_metadata_open_and_map_errors() {
    for failure in [
        Failure::Walk,
        Failure::EntryMetadata,
        Failure::Open,
        Failure::MmapMetadata,
        Failure::Map,
    ] {
        for incremental in [false, true] {
            let dir = tempdir().unwrap();
            let file = dir.path().join("a.txt");
            fs::write(&file, "oldneedle value\n").unwrap();
            let saved = dir.path().join(".tg_index");
            let positive = TrigramIndex::build(dir.path()).unwrap();
            assert_eq!(positive.search("oldneedle", false, true).unwrap().len(), 1);
            positive.save(&saved).unwrap();
            let before = fs::read(&saved).unwrap();
            if incremental {
                fs::write(&file, "newneedle updated value\n").unwrap();
            }

            let error = if incremental {
                TrigramIndex::load(&saved)
                    .unwrap()
                    .rebuild_incremental_with_operations(
                        dir.path(),
                        false,
                        |root, no_ignore| collect_with_failure(root, no_ignore, failure),
                        |path| extract_with_failure(path, failure),
                    )
                    .unwrap_err()
            } else {
                TrigramIndex::build_with_operations(
                    dir.path(),
                    false,
                    |root, no_ignore| collect_with_failure(root, no_ignore, failure),
                    |path| extract_with_failure(path, failure),
                )
                .unwrap_err()
            };
            let diagnostic = format!("{error:#}");
            let expected = match failure {
                Failure::Walk => "injected walk failure",
                Failure::EntryMetadata => "failed to read index file metadata",
                Failure::Open => "failed to open index file",
                Failure::MmapMetadata => "failed to read index file metadata",
                Failure::Map => "failed to map index file",
            };
            assert!(diagnostic.contains(expected), "{diagnostic}");
            assert_eq!(fs::read(&saved).unwrap(), before);
        }
    }
}

#[cfg(windows)]
#[test]
fn index_build_fails_closed_when_a_walked_file_cannot_be_opened() {
    use std::os::windows::fs::OpenOptionsExt;

    let dir = tempdir().unwrap();
    let file = dir.path().join("a.txt");
    fs::write(&file, "needle content\n").unwrap();
    let saved = dir.path().join(".tg_index");
    let positive = TrigramIndex::build(dir.path()).unwrap();
    assert_eq!(positive.search("needle", false, true).unwrap().len(), 1);
    positive.save(&saved).unwrap();
    let before = fs::read(&saved).unwrap();
    let metadata_before = fs::metadata(&file).unwrap();

    let blocker = fs::OpenOptions::new()
        .read(true)
        .share_mode(0)
        .open(&file)
        .unwrap();
    let result = TrigramIndex::build(dir.path());
    drop(blocker);

    let error = result.expect_err("an incomplete index build must fail");
    assert!(error.to_string().contains("a.txt"), "{error:#}");
    assert_eq!(fs::read(&saved).unwrap(), before);
    let metadata_after = fs::metadata(&file).unwrap();
    assert_eq!(metadata_after.len(), metadata_before.len());
    assert_eq!(
        metadata_after.modified().unwrap(),
        metadata_before.modified().unwrap()
    );
    assert_eq!(
        TrigramIndex::build(dir.path())
            .unwrap()
            .search("needle", false, true)
            .unwrap()
            .len(),
        1,
        "the same file remains indexable after the transient open failure"
    );
}

#[cfg(windows)]
#[test]
fn incremental_index_failure_keeps_saved_index_and_recovers_without_tree_changes() {
    use std::os::windows::fs::OpenOptionsExt;

    let dir = tempdir().unwrap();
    let file = dir.path().join("a.txt");
    fs::write(&file, "oldneedle value\n").unwrap();
    let saved = dir.path().join(".tg_index");
    TrigramIndex::build(dir.path())
        .unwrap()
        .save(&saved)
        .unwrap();
    let before = fs::read(&saved).unwrap();

    fs::write(&file, "newneedle updated value\n").unwrap();
    let changed_metadata = fs::metadata(&file).unwrap();
    let blocker = fs::OpenOptions::new()
        .read(true)
        .share_mode(0)
        .open(&file)
        .unwrap();
    let failed = TrigramIndex::load(&saved)
        .unwrap()
        .rebuild_incremental_with_options(dir.path(), false);
    drop(blocker);

    let error = failed.expect_err("an incomplete incremental rebuild must fail");
    assert!(error.to_string().contains("a.txt"), "{error:#}");
    assert_eq!(fs::read(&saved).unwrap(), before);
    let recovered_metadata = fs::metadata(&file).unwrap();
    assert_eq!(recovered_metadata.len(), changed_metadata.len());
    assert_eq!(
        recovered_metadata.modified().unwrap(),
        changed_metadata.modified().unwrap()
    );

    let recovered = TrigramIndex::load(&saved)
        .unwrap()
        .rebuild_incremental_with_options(dir.path(), false)
        .unwrap();
    assert_eq!(recovered.stats.modified_files, 1);
    assert_eq!(
        recovered
            .index
            .search("newneedle", false, true)
            .unwrap()
            .len(),
        1
    );
    assert!(recovered
        .index
        .search("oldneedle", false, true)
        .unwrap()
        .is_empty());
    assert!(recovered
        .index
        .search("missingmarker", false, true)
        .unwrap()
        .is_empty());
}

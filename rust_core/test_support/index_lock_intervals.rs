use std::collections::HashMap;
use std::fs;
use std::path::Path;

pub(super) fn read_lock_activity_intervals(dir: &Path) -> Vec<(u128, u128)> {
    let mut starts: HashMap<String, u128> = HashMap::new();
    let mut ends: HashMap<String, u128> = HashMap::new();

    for entry in fs::read_dir(dir).expect("failed to read lock activity directory") {
        let entry = entry.expect("failed to read lock activity directory entry");
        assert!(
            entry
                .file_type()
                .expect("failed to inspect lock activity entry type")
                .is_file(),
            "lock activity entry is not a regular file: {:?}",
            entry.path()
        );
        let name = entry
            .file_name()
            .into_string()
            .expect("lock activity filename is not valid UTF-8");
        let (key, suffix) = name
            .rsplit_once('.')
            .expect("lock activity filename has no suffix");
        assert!(!key.is_empty(), "lock activity filename has an empty key");
        let records = match suffix {
            "start" => &mut starts,
            "end" => &mut ends,
            _ => panic!("unknown lock activity filename suffix: {suffix:?}"),
        };
        let value: u128 = fs::read_to_string(entry.path())
            .expect("failed to read lock activity record")
            .trim()
            .parse()
            .expect("lock activity record is not a u128 timestamp");
        assert_ne!(value, 0, "lock activity timestamp must be nonzero");
        records.insert(key.to_owned(), value);
    }

    let mut intervals = Vec::with_capacity(starts.len());
    for (key, start) in starts {
        let end = ends
            .remove(&key)
            .unwrap_or_else(|| panic!("lock activity start has no matching end: {key:?}"));
        assert!(
            start <= end,
            "lock activity interval is reversed for {key:?}"
        );
        intervals.push((start, end));
    }
    assert!(
        ends.is_empty(),
        "lock activity end has no matching start: {:?}",
        ends.keys().next().expect("nonempty ends map")
    );
    intervals.sort_unstable();
    intervals
}

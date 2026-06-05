use std::fs;
use std::io::Write;

use arrow_array::Array;
use tempfile::tempdir;
use tensor_grep_rs::mmap_arrow::create_arrow_string_array_from_mmap;

#[test]
fn valid_utf8_mmap_produces_zero_copy_string_array() {
    let dir = tempdir().unwrap();
    let path = dir.path().join("valid.log");
    fs::write(&path, "hello\nworld\n").unwrap();

    let array = create_arrow_string_array_from_mmap(path.to_str().unwrap()).unwrap();
    assert_eq!(array.len(), 2);
    assert_eq!(array.value(0), "hello\n");
    assert_eq!(array.value(1), "world\n");
}

#[test]
fn valid_utf8_without_trailing_newline_preserves_final_line() {
    let dir = tempdir().unwrap();
    let path = dir.path().join("no_trailing_newline.log");
    fs::write(&path, "alpha\nbeta").unwrap();

    let array = create_arrow_string_array_from_mmap(path.to_str().unwrap()).unwrap();
    assert_eq!(array.len(), 2);
    assert_eq!(array.value(0), "alpha\n");
    assert_eq!(array.value(1), "beta");
}

#[test]
fn invalid_utf8_mmap_falls_back_to_lossy_string_array() {
    let dir = tempdir().unwrap();
    let path = dir.path().join("invalid.log");
    let mut file = fs::File::create(&path).unwrap();
    file.write_all(b"valid line\n").unwrap();
    file.write_all(&[0xFF, 0xFE, b'b', b'a', b'd', b'\n'])
        .unwrap();

    let array = create_arrow_string_array_from_mmap(path.to_str().unwrap()).unwrap();
    assert_eq!(array.len(), 2);
    assert_eq!(array.value(0), "valid line\n");
    assert!(array.value(1).contains("bad"));
    assert!(std::str::from_utf8(array.value(1).as_bytes()).is_ok());
}

#[test]
fn empty_mmap_file_produces_empty_string_array() {
    let dir = tempdir().unwrap();
    let path = dir.path().join("empty.log");
    fs::write(&path, "").unwrap();

    let array = create_arrow_string_array_from_mmap(path.to_str().unwrap()).unwrap();
    assert_eq!(array.len(), 0);
}

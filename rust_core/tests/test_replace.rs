use std::fs::File;
use std::io::{Read, Write};
use std::path::Path;
use tempfile::tempdir;
use tensor_grep_rs::backend_cpu::CpuBackend;

fn read_file(path: &Path) -> String {
    let mut content = String::new();
    File::open(path)
        .unwrap()
        .read_to_string(&mut content)
        .unwrap();
    content
}

fn read_backend_source() -> String {
    std::fs::read_to_string(concat!(env!("CARGO_MANIFEST_DIR"), "/src/backend_cpu.rs")).unwrap()
}

fn extract_function_body<'a>(source: &'a str, function_name: &str) -> &'a str {
    let signature = format!("fn {function_name}");
    let fn_start = source.find(&signature).unwrap();
    let body_start = source[fn_start..].find('{').unwrap() + fn_start + 1;

    let mut brace_depth = 1usize;
    let mut in_line_comment = false;
    let mut in_block_comment = false;
    let bytes = source.as_bytes();
    let mut index = body_start;

    while index < bytes.len() {
        let current = bytes[index];
        let next = bytes.get(index + 1).copied();

        if in_line_comment {
            if current == b'\n' {
                in_line_comment = false;
            }
            index += 1;
            continue;
        }

        if in_block_comment {
            if current == b'*' && next == Some(b'/') {
                in_block_comment = false;
                index += 2;
            } else {
                index += 1;
            }
            continue;
        }

        if current == b'/' && next == Some(b'/') {
            in_line_comment = true;
            index += 2;
            continue;
        }

        if current == b'/' && next == Some(b'*') {
            in_block_comment = true;
            index += 2;
            continue;
        }

        if current == b'{' {
            brace_depth += 1;
        } else if current == b'}' {
            brace_depth -= 1;
            if brace_depth == 0 {
                return &source[body_start..index];
            }
        }

        index += 1;
    }

    panic!("function body should be balanced");
}

#[test]
fn test_replace_path_uses_mutable_memmap_instead_of_full_file_reads() {
    let backend_source = read_backend_source();
    let literal_replace_body = extract_function_body(&backend_source, "replace_file_literal");
    let regex_replace_body = extract_function_body(&backend_source, "replace_file_regex");
    let mmap_write_body = extract_function_body(&backend_source, "write_replacements_with_mmap");
    let apply_body = extract_function_body(&backend_source, "apply_replacements_in_place");

    assert!(
        mmap_write_body.contains("map_mut"),
        "replace path should use MmapMut for byte mutations"
    );
    assert!(
        apply_body.contains("flush"),
        "replace path should flush the mutable mmap before drop"
    );
    assert!(
        !literal_replace_body.contains("std::fs::read("),
        "literal replace path should avoid full-file std::fs::read allocation"
    );
    assert!(
        !regex_replace_body.contains("std::fs::read("),
        "replace path should avoid full-file std::fs::read allocation"
    );
}

#[test]
fn test_rust_replace_in_place_literal() {
    let dir = tempdir().unwrap();
    let file_path = dir.path().join("test_replace.txt");
    let mut file = File::create(&file_path).unwrap();
    writeln!(file, "Hello world\nThis is a test\nHello again").unwrap();

    let backend = CpuBackend::new();
    // Replace "Hello" with "Goodbye"
    backend
        .replace_in_place("Hello", "Goodbye", file_path.to_str().unwrap(), false, true)
        .unwrap();

    assert_eq!(
        read_file(&file_path),
        "Goodbye world\nThis is a test\nGoodbye again\n"
    );
}

#[test]
fn test_rust_replace_in_place_fixed_strings_treats_dollar_as_literal() {
    let dir = tempdir().unwrap();
    let file_path = dir.path().join("test_fixed_string_dollar.txt");
    let mut file = File::create(&file_path).unwrap();
    writeln!(file, "TOKEN TOKEN").unwrap();

    let backend = CpuBackend::new();
    backend
        .replace_in_place("TOKEN", "$0", file_path.to_str().unwrap(), false, true)
        .unwrap();

    assert_eq!(read_file(&file_path), "$0 $0\n");
}

#[test]
fn test_rust_replace_in_place_regex_capture_groups() {
    let dir = tempdir().unwrap();
    let file_path = dir.path().join("test_regex.txt");
    let mut file = File::create(&file_path).unwrap();
    // Typical usecase: Swapping function argument order
    writeln!(file, "def foo(a, b):\n    pass\ndef bar(x, y):\n    pass").unwrap();

    let backend = CpuBackend::new();
    // Regex looking for function arguments and capturing them.
    backend
        .replace_in_place(
            r"def (\w+)\((\w+), (\w+)\):",
            "def $1($3, $2):",
            file_path.to_str().unwrap(),
            false,
            false,
        )
        .unwrap();

    assert_eq!(
        read_file(&file_path),
        "def foo(b, a):\n    pass\ndef bar(y, x):\n    pass\n"
    );
}

#[test]
fn test_rust_replace_preserves_formatting() {
    let dir = tempdir().unwrap();
    let file_path = dir.path().join("test_format.txt");
    let mut file = File::create(&file_path).unwrap();
    let original = "    let x = 10;\n\n\tlet y = 20;\n";
    write!(file, "{}", original).unwrap();

    let backend = CpuBackend::new();
    backend
        .replace_in_place("10", "15", file_path.to_str().unwrap(), false, true)
        .unwrap();

    assert_eq!(read_file(&file_path), "    let x = 15;\n\n\tlet y = 20;\n");
}

#[test]
fn test_rust_replace_handles_mixed_growth_and_shrink_matches() {
    let dir = tempdir().unwrap();
    let file_path = dir.path().join("test_mixed_lengths.txt");
    let mut file = File::create(&file_path).unwrap();
    write!(file, "A:1234\nLONGNAME:5\nBB:67\n").unwrap();

    let backend = CpuBackend::new();
    backend
        .replace_in_place(
            r"([A-Z]+):(\d+)",
            "$2$2",
            file_path.to_str().unwrap(),
            false,
            false,
        )
        .unwrap();

    assert_eq!(read_file(&file_path), "12341234\n55\n6767\n");
}

#[test]
fn test_rust_replace_in_place_empty_file_is_no_op() {
    let dir = tempdir().unwrap();
    let file_path = dir.path().join("empty.txt");
    File::create(&file_path).unwrap();

    let backend = CpuBackend::new();
    backend
        .replace_in_place(
            "anything",
            "something",
            file_path.to_str().unwrap(),
            false,
            true,
        )
        .unwrap();

    assert_eq!(read_file(&file_path), "");
}

#[test]
fn test_rust_replace_in_place_allows_empty_replacement_for_deletion() {
    let dir = tempdir().unwrap();
    let file_path = dir.path().join("delete.txt");
    let mut file = File::create(&file_path).unwrap();
    write!(file, "abc123abc").unwrap();

    let backend = CpuBackend::new();
    backend
        .replace_in_place("abc", "", file_path.to_str().unwrap(), false, true)
        .unwrap();

    assert_eq!(read_file(&file_path), "123");
}

#[test]
fn test_rust_replace_in_place_can_replace_entire_file_contents() {
    let dir = tempdir().unwrap();
    let file_path = dir.path().join("entire-file.txt");
    let mut file = File::create(&file_path).unwrap();
    write!(file, "whole file").unwrap();

    let backend = CpuBackend::new();
    backend
        .replace_in_place(
            "whole file",
            "updated",
            file_path.to_str().unwrap(),
            false,
            true,
        )
        .unwrap();

    assert_eq!(read_file(&file_path), "updated");
}

#[test]
fn test_rust_replace_in_place_handles_single_byte_files() {
    let dir = tempdir().unwrap();
    let file_path = dir.path().join("single-byte.txt");
    let mut file = File::create(&file_path).unwrap();
    write!(file, "a").unwrap();

    let backend = CpuBackend::new();
    backend
        .replace_in_place("a", "b", file_path.to_str().unwrap(), false, true)
        .unwrap();

    assert_eq!(read_file(&file_path), "b");
}

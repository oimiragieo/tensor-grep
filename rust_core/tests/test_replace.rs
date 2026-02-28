use tensor_grep_rs::backend_cpu::CpuBackend;
use std::fs::File;
use std::io::{Read, Write};
use tempfile::tempdir;

#[test]
fn test_rust_replace_in_place_literal() {
    let dir = tempdir().unwrap();
    let file_path = dir.path().join("test_replace.txt");
    let mut file = File::create(&file_path).unwrap();
    writeln!(file, "Hello world\nThis is a test\nHello again").unwrap();

    let backend = CpuBackend::new();
    // Replace "Hello" with "Goodbye"
    backend.replace_in_place("Hello", "Goodbye", file_path.to_str().unwrap(), false, true).unwrap();

    let mut new_content = String::new();
    File::open(&file_path).unwrap().read_to_string(&mut new_content).unwrap();
    
    assert_eq!(new_content, "Goodbye world\nThis is a test\nGoodbye again\n");
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
    backend.replace_in_place(r"def (\w+)\((\w+), (\w+)\):", "def $1($3, $2):", file_path.to_str().unwrap(), false, false).unwrap();

    let mut new_content = String::new();
    File::open(&file_path).unwrap().read_to_string(&mut new_content).unwrap();
    
    assert_eq!(new_content, "def foo(b, a):\n    pass\ndef bar(y, x):\n    pass\n");
}

#[test]
fn test_rust_replace_preserves_formatting() {
    let dir = tempdir().unwrap();
    let file_path = dir.path().join("test_format.txt");
    let mut file = File::create(&file_path).unwrap();
    let original = "    let x = 10;\n\n\tlet y = 20;\n";
    write!(file, "{}", original).unwrap();

    let backend = CpuBackend::new();
    backend.replace_in_place("10", "15", file_path.to_str().unwrap(), false, true).unwrap();

    let mut new_content = String::new();
    File::open(&file_path).unwrap().read_to_string(&mut new_content).unwrap();
    
    assert_eq!(new_content, "    let x = 15;\n\n\tlet y = 20;\n");
}

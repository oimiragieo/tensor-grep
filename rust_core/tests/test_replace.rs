use std::fs::File;
use std::io::{Read, Write};
use std::path::Path;
use tempfile::tempdir;
use tensor_grep_rs::backend_cpu::CpuBackend;

// Compile-time public-API signature guard (Task 5, Step 2.1): pins `replace_in_place`'s exact
// argument shape and return type. A future change to either -- e.g. widening the return type to
// something `.unwrap()` callers would still accept -- fails the BUILD here rather than silently
// changing behavior for downstream `rlib` consumers this crate cannot see.
const _: fn(&CpuBackend, &str, &str, &str, bool, bool) -> anyhow::Result<()> =
    CpuBackend::replace_in_place;

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

// --- Characterization controls (Task 5, Step 2.2) ----------------------------------------
//
// These pin the PUBLIC contract's current behavior before/after the directory-mode refactor:
// direct-file failure already propagates via `?` (this was true on the pre-refactor code and
// must stay true), and directory mode -- both literal and regex -- succeeds end-to-end on a
// real multi-file tree with no error swallowed. None of these were previously exercised.

// W3B / A1.4: fail-closed symlink_metadata turns a missing path into Err naming the path
// (threat model section 6). Pre-fix this is RED — the intended phase-1 signal.
#[test]
fn test_rust_replace_in_place_direct_file_nonexistent_path_errors_with_the_path_named() {
    let dir = tempdir().unwrap();
    let missing_path = dir.path().join("does-not-exist.txt");
    let missing_display = missing_path.display().to_string();

    let backend = CpuBackend::new();
    let result = backend.replace_in_place("a", "b", missing_path.to_str().unwrap(), false, true);
    let err = result
        .expect_err("a nonexistent direct-file path must error with the path named (literal arm)");
    assert!(
        format!("{err}").contains(&missing_display),
        "literal-arm error must name the missing path; got: {err}"
    );

    let result = backend.replace_in_place("a", "b", missing_path.to_str().unwrap(), false, false);
    let err = result
        .expect_err("a nonexistent direct-file path must error with the path named (regex arm)");
    assert!(
        format!("{err}").contains(&missing_display),
        "regex-arm error must name the missing path; got: {err}"
    );
}

// This is an arm that DOES already propagate via `?` today, deterministically and without
// relying on OS permission bits: an invalid regex pattern fails `RegexBuilder::build()?` after
// the symlink guard's stat (which passes for a real file) but before the regex is built. It runs
// against a real existing file to isolate the failure to the regex build step rather than the
// path-existence branch characterized above.
#[test]
fn test_rust_replace_in_place_direct_file_invalid_regex_returns_err() {
    let dir = tempdir().unwrap();
    let file_path = dir.path().join("invalid-regex-target.txt");
    write!(File::create(&file_path).unwrap(), "content").unwrap();

    let backend = CpuBackend::new();
    let result = backend.replace_in_place(
        "(unterminated",
        "b",
        file_path.to_str().unwrap(),
        false,
        false, // regex path, not fixed_strings
    );
    assert!(
        result.is_err(),
        "direct-file mode already propagates a regex build failure via `?`"
    );
}

#[test]
fn test_rust_replace_in_place_directory_mode_literal_succeeds_across_files() {
    let dir = tempdir().unwrap();
    let file_a = dir.path().join("a.txt");
    let file_b = dir.path().join("b.txt");
    write!(File::create(&file_a).unwrap(), "needle one").unwrap();
    write!(File::create(&file_b).unwrap(), "needle two").unwrap();

    let backend = CpuBackend::new();
    backend
        .replace_in_place("needle", "found", dir.path().to_str().unwrap(), false, true)
        .unwrap();

    assert_eq!(read_file(&file_a), "found one");
    assert_eq!(read_file(&file_b), "found two");
}

#[test]
fn test_rust_replace_in_place_directory_mode_regex_succeeds_across_files() {
    let dir = tempdir().unwrap();
    let file_a = dir.path().join("a.txt");
    let file_b = dir.path().join("b.txt");
    write!(File::create(&file_a).unwrap(), "id:1").unwrap();
    write!(File::create(&file_b).unwrap(), "id:2").unwrap();

    let backend = CpuBackend::new();
    backend
        .replace_in_place(
            r"id:(\d+)",
            "ID#$1",
            dir.path().to_str().unwrap(),
            false,
            false,
        )
        .unwrap();

    assert_eq!(read_file(&file_a), "ID#1");
    assert_eq!(read_file(&file_b), "ID#2");
}

#[test]
fn replace_in_place_refuses_to_follow_a_symlinked_file_target() {
    // RUST-REPLACE-SYMLINK: Path::is_file() and OpenOptions::open both FOLLOW, so an
    // attacker-planted symlink redirects an in-place replace to a destination the caller
    // never named (sed CVE-2026-5958 / uutils GHSA-239g-2685-54x3 class).
    let dir = tempfile::tempdir().unwrap();
    let real = dir.path().join("real.txt");
    let link = dir.path().join("link.txt");
    std::fs::write(&real, b"needle here").unwrap();

    #[cfg(unix)]
    std::os::unix::fs::symlink(&real, &link).unwrap();
    #[cfg(windows)]
    // CANNOT_MEASURE, not RED: symlink_file needs SeCreateSymbolicLinkPrivilege (or Developer
    // Mode). An unprivileged runner would panic HERE, in the fixture, and the CI log would read
    // as a failing security test (A61: the RED reason must be the pinned behavioral assertion,
    // never a setup crash). Same shape as tests/test_ast_rewrite.rs.
    if let Err(err) = std::os::windows::fs::symlink_file(&real, &link) {
        if std::env::var_os("TG_REQUIRE_SYMLINK_TESTS").is_some() {
            panic!(
                "TG_REQUIRE_SYMLINK_TESTS set: cannot create a Windows symlink in this environment: {err}"
            );
        }
        eprintln!(
            "skipping replace_in_place_refuses_to_follow_a_symlinked_file_target: \
             cannot create a Windows symlink in this environment: {err}"
        );
        return;
    }

    // A88: prove the hostile fixture actually BITES before trusting the verdict.
    let meta =
        std::fs::symlink_metadata(&link).expect("CANNOT_MEASURE: symlink was not created at all");
    assert!(
        meta.file_type().is_symlink(),
        "CANNOT_MEASURE: symlink creation did not produce a symlink; this test proves nothing"
    );

    let backend = CpuBackend::new();
    let result = backend.replace_in_place("needle", "found", link.to_str().unwrap(), false, true);

    assert!(
        result.is_err(),
        "replace_in_place must refuse a symlinked target, got Ok"
    );
    let contents = std::fs::read_to_string(&real).unwrap();
    assert_eq!(
        contents, "needle here",
        "the symlink target was rewritten through the link -- the guard did not hold"
    );
}

#[test]
fn replace_in_place_still_rewrites_a_regular_file() {
    // Positive control for the symlink guard: a guard that refuses EVERYTHING would pass
    // the RED arm above and be indistinguishable from a correct fix.
    let dir = tempfile::tempdir().unwrap();
    let target = dir.path().join("plain.txt");
    std::fs::write(&target, b"needle here").unwrap();

    let backend = CpuBackend::new();
    backend
        .replace_in_place("needle", "found", target.to_str().unwrap(), false, true)
        .expect("a regular file must still be replaced");

    assert_eq!(std::fs::read_to_string(&target).unwrap(), "found here");
}

#[test]
fn replace_directory_mode_skips_symlinked_entries() {
    // Pins a property that is currently INCIDENTAL (walkdir's follow_links(false) default
    // plus DirEntry::file_type().is_file() being false for a symlink). Unpinned, it is one
    // refactor away from becoming the vulnerability the test above guards.
    let dir = tempfile::tempdir().unwrap();
    let outside = dir.path().join("outside.txt");
    std::fs::write(&outside, b"needle here").unwrap();

    let tree = dir.path().join("tree");
    std::fs::create_dir(&tree).unwrap();
    let link = tree.join("link.txt");
    #[cfg(unix)]
    std::os::unix::fs::symlink(&outside, &link).unwrap();
    #[cfg(windows)]
    // CANNOT_MEASURE, not RED -- see the note under the refuse test.
    if let Err(err) = std::os::windows::fs::symlink_file(&outside, &link) {
        if std::env::var_os("TG_REQUIRE_SYMLINK_TESTS").is_some() {
            panic!(
                "TG_REQUIRE_SYMLINK_TESTS set: cannot create a Windows symlink in this environment: {err}"
            );
        }
        eprintln!(
            "skipping replace_directory_mode_skips_symlinked_entries: \
             cannot create a Windows symlink in this environment: {err}"
        );
        return;
    }
    assert!(
        std::fs::symlink_metadata(&link)
            .unwrap()
            .file_type()
            .is_symlink(),
        "CANNOT_MEASURE: symlink creation did not produce a symlink"
    );

    let backend = CpuBackend::new();
    backend
        .replace_in_place("needle", "found", tree.to_str().unwrap(), false, true)
        .expect("directory mode must succeed while skipping the symlink");

    assert_eq!(
        std::fs::read_to_string(&outside).unwrap(),
        "needle here",
        "directory mode followed a symlink out of the tree"
    );
}

#[test]
fn replace_in_place_refuses_a_directory_symlink_root() {
    // W3A council amendment A1.3: walkdir's follow_root_links(true) default means a
    // symlink ROOT handed to directory mode is FOLLOWED today; the pre-branch guard must
    // refuse it before is_dir() can hand it to WalkDir.
    let dir = tempfile::tempdir().unwrap();
    let target_dir = dir.path().join("target");
    std::fs::create_dir(&target_dir).unwrap();
    std::fs::write(target_dir.join("inside.txt"), b"needle here").unwrap();
    let link = dir.path().join("dirlink");

    #[cfg(unix)]
    std::os::unix::fs::symlink(&target_dir, &link).unwrap();
    #[cfg(windows)]
    if let Err(err) = std::os::windows::fs::symlink_dir(&target_dir, &link) {
        if std::env::var_os("TG_REQUIRE_SYMLINK_TESTS").is_some() {
            panic!(
                "TG_REQUIRE_SYMLINK_TESTS set: cannot create a Windows directory symlink in this environment: {err}"
            );
        }
        eprintln!(
            "skipping replace_in_place_refuses_a_directory_symlink_root: \
             cannot create a Windows directory symlink in this environment: {err}"
        );
        return;
    }
    assert!(
        std::fs::symlink_metadata(&link)
            .unwrap()
            .file_type()
            .is_symlink(),
        "CANNOT_MEASURE: directory-symlink creation did not produce a symlink"
    );

    let backend = CpuBackend::new();
    let result = backend.replace_in_place("needle", "found", link.to_str().unwrap(), false, true);
    assert!(
        result.is_err(),
        "replace_in_place must refuse a symlink root, got Ok"
    );
    assert_eq!(
        std::fs::read_to_string(target_dir.join("inside.txt")).unwrap(),
        "needle here",
        "directory mode followed the root link and rewrote the target"
    );

    // Trailing-separator arm (opus gate r4 F1): a trailing separator on the raw path must not
    // defeat the root refusal (POSIX lstat("<link>/") resolves through the final symlink;
    // Windows normalizes trailing separators in attribute queries but the arm is harmless and
    // keeps the guard contract pinned on both platforms).
    {
        let trailing = format!("{}/", link.display());
        let result = backend.replace_in_place("needle", "found", &trailing, false, true);
        assert!(
            result.is_err(),
            "a trailing-separator path must still refuse a symlink root, got Ok"
        );
        assert_eq!(
            std::fs::read_to_string(target_dir.join("inside.txt")).unwrap(),
            "needle here",
            "the trailing-separator path followed the root link and rewrote the target"
        );
    }
}

#[test]
#[cfg(windows)]
fn replace_in_place_refuses_a_directory_junction_root() {
    // W3A council amendment A1.2 / GATE-W3A-1 outcome (a) REFUSE: the bounded toolchain
    // probe (threat model section 5) shows junctions report is_symlink()==true on the
    // pinned Rust, so the guard refuses them. This pins that on a REAL junction
    // (mklink /J, no privilege needed). Fixture must BITE (A88).
    let dir = tempfile::tempdir().unwrap();
    let target_dir = dir.path().join("target");
    std::fs::create_dir(&target_dir).unwrap();
    std::fs::write(target_dir.join("inside.txt"), b"needle here").unwrap();
    let link = dir.path().join("junclink");

    let status = std::process::Command::new("cmd")
        .args(["/c", "mklink", "/J"])
        .arg(&link)
        .arg(&target_dir)
        .status();
    match status {
        Ok(s) if s.success() => {}
        _ => {
            if std::env::var_os("TG_REQUIRE_SYMLINK_TESTS").is_some() {
                panic!(
                    "TG_REQUIRE_SYMLINK_TESTS set: cannot create a Windows junction in this environment"
                );
            }
            eprintln!(
                "skipping replace_in_place_refuses_a_directory_junction_root: \
                 cannot create a Windows junction in this environment"
            );
            return;
        }
    }
    assert!(
        std::fs::symlink_metadata(&link)
            .unwrap()
            .file_type()
            .is_symlink(),
        "CANNOT_MEASURE: junction creation did not produce a symlink-reporting reparse point"
    );

    let backend = CpuBackend::new();
    let result = backend.replace_in_place("needle", "found", link.to_str().unwrap(), false, true);
    assert!(
        result.is_err(),
        "replace_in_place must refuse a junction root, got Ok"
    );
    assert_eq!(
        std::fs::read_to_string(target_dir.join("inside.txt")).unwrap(),
        "needle here",
        "directory mode followed the junction and rewrote the target"
    );
}

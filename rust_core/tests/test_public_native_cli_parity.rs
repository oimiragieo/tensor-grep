#![allow(unused_imports, dead_code)]
use serde_json::Value;
use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::{Command, Output, Stdio};
use tempfile::tempdir;
#[path = "common/public_native_cli_parity_helpers.rs"]
mod common;
use common::*;

#[test]
fn test_search_no_path_piped_stdin_forwards_no_default_path_to_rg() {
    let dir = tempdir().unwrap();
    let fake_rg = fake_rg_exact_args_script(dir.path(), &["-e", "needle"], "stdin needle\n");

    let mut child = tg()
        .current_dir(dir.path())
        .args(["search", "needle"])
        .env("TG_RG_PATH", &fake_rg)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap();
    child
        .stdin
        .as_mut()
        .unwrap()
        .write_all(b"stdin needle\n")
        .unwrap();
    let output = child.wait_with_output().unwrap();

    assert!(
        output.status.success(),
        "status={:?}\nstdout={}\nstderr={}",
        output.status.code(),
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    assert_eq!(
        String::from_utf8_lossy(&output.stdout).replace("\r\n", "\n"),
        "stdin needle\n"
    );
}

#[test]
fn test_search_explicit_path_keeps_path_when_stdin_is_piped() {
    let dir = tempdir().unwrap();
    let fake_rg = fake_rg_exact_args_script(
        dir.path(),
        &["-e", "needle", "--", "fixture.txt"],
        "fixture.txt:needle file\n",
    );
    fs::write(dir.path().join("fixture.txt"), "needle file\n").unwrap();

    let mut child = tg()
        .current_dir(dir.path())
        .args(["search", "needle", "fixture.txt"])
        .env("TG_RG_PATH", &fake_rg)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap();
    // The write result is deliberately IGNORED. The fake rg used to drain stdin
    // (`sys.stdin.buffer.read()`); on the native route nothing reads it, so if `tg` exits before
    // this write lands the parent gets EPIPE. 13 bytes fit any pipe buffer, so the window is
    // narrow -- but a narrow race is still a flake, and this suite has been de-flaked before. A
    // failed write is CONSISTENT with the invariant under test (stdin must not be consumed), so
    // discarding the error removes the timing dependency without weakening anything.
    let _ = child.stdin.as_mut().unwrap().write_all(b"stdin needle\n");
    drop(child.stdin.take());
    let output = child.wait_with_output().unwrap();

    assert!(
        output.status.success(),
        "status={:?}\nstdout={}\nstderr={}",
        output.status.code(),
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    // THE INVARIANT THIS TEST NAMES: with stdin piped, an explicit PATH still wins -- the file is
    // searched and the piped stdin is NOT. That is asserted directly below, so it holds under
    // either routing.
    //
    // It used to be asserted only as `== "fixture.txt:needle file\n"`, i.e. by requiring that the
    // FAKE rg above was invoked and its canned stdout passed through. That is a mechanism, not the
    // contract: the plain-text native route answers this shape in-process, so the fake rg is never
    // spawned and the canned prefix never appears. Real `rg` prints `needle file` here with no
    // prefix (measured, ripgrep 15.1.0, identical whether stdin is piped or /dev/null), so the
    // prefix was an artifact of the fake, not ripgrep behavior.
    //
    // The fake rg's exact-argv assertion is still live and still fires if the request DOES reach
    // rg, so the forwarding guarantee is not lost.
    //
    // To be precise about what this now pins, because an earlier version of this comment claimed
    // the opposite: it DOES mandate the native engine. The fake rg's canned stdout is
    // `"fixture.txt:needle file\n"` (see the `fake_rg_exact_args_script` call above), so the
    // `assert_eq!` below fails if the request reaches rg at all. That is intended -- this shape is
    // squarely inside the admitted subset, so a silent fall back to the subprocess is a routing
    // regression worth failing on -- but "only stops the test from mandating WHICH engine answers"
    // was simply wrong.
    let stdout = String::from_utf8_lossy(&output.stdout).replace("\r\n", "\n");
    assert!(
        !stdout.contains("stdin needle"),
        "an explicit PATH must win over piped stdin; stdout={stdout}"
    );
    // Asserted as EQUALITY, not a disjunction. An earlier revision also accepted
    // `fixture.txt:needle file`, which would have ACCEPTED a native emitter that wrongly prefixes
    // the filename on a single-file search. Measured on rg 15.1.0, both spellings:
    //     printf 'stdin needle' | rg -e needle -- fixture.txt  -> "needle file"  rc=0
    //     rg -e needle -- fixture.txt < /dev/null              -> "needle file"  rc=0
    // so there is exactly one correct answer and the test now demands it.
    assert_eq!(stdout, "needle file\n");
}

#[test]
fn test_root_no_path_piped_stdin_forwards_no_default_path_to_rg() {
    let dir = tempdir().unwrap();
    let fake_rg = fake_rg_exact_args_script(dir.path(), &["-e", "needle"], "stdin needle\n");

    let mut child = tg()
        .current_dir(dir.path())
        .arg("needle")
        .env("TG_RG_PATH", &fake_rg)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap();
    child
        .stdin
        .as_mut()
        .unwrap()
        .write_all(b"stdin needle\n")
        .unwrap();
    let output = child.wait_with_output().unwrap();

    assert!(
        output.status.success(),
        "status={:?}\nstdout={}\nstderr={}",
        output.status.code(),
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    assert_eq!(
        String::from_utf8_lossy(&output.stdout).replace("\r\n", "\n"),
        "stdin needle\n"
    );
}

#[test]
fn test_search_accepts_multiline_flags_on_public_native_frontdoor() {
    let dir = tempdir().unwrap();
    let fake_rg = fake_rg_script(dir.path(), "invoice.py:def create_invoice\n");
    fs::write(
        dir.path().join("invoice.py"),
        "def create_invoice(subtotal):\n    tax = subtotal * 0.1\n    return subtotal + tax\n",
    )
    .unwrap();

    for flag in ["--multiline", "-U"] {
        let output = tg()
            .current_dir(dir.path())
            .args(["search", flag, r"create_invoice[\s\S]*return", "."])
            .env("TG_RG_PATH", &fake_rg)
            .output()
            .unwrap();

        assert!(
            output.status.success(),
            "flag={flag} status={:?}\nstdout={}\nstderr={}",
            output.status.code(),
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr)
        );
        assert!(
            String::from_utf8_lossy(&output.stdout).contains("create_invoice"),
            "flag={flag} stdout={}",
            String::from_utf8_lossy(&output.stdout)
        );
    }
}

#[test]
fn test_search_editor_output_flags_are_accepted_on_public_native_frontdoor() {
    let dir = tempdir().unwrap();
    let fake_rg = fake_rg_asserting_args_script(
        dir.path(),
        &["--vimgrep", "--path-separator", "/"],
        "src/app.log:1:1:ERROR failed\n",
    );
    fs::create_dir(dir.path().join("src")).unwrap();
    fs::write(dir.path().join("src").join("app.log"), "ERROR failed\n").unwrap();

    for args in [
        vec!["search", "--vimgrep", "--path-separator", "/", "ERROR", "."],
        vec![
            "--format",
            "rg",
            "--vimgrep",
            "--path-separator",
            "/",
            "ERROR",
            ".",
        ],
    ] {
        let output = tg()
            .current_dir(dir.path())
            .args(&args)
            .env("TG_RG_PATH", &fake_rg)
            .output()
            .unwrap();

        assert!(
            output.status.success(),
            "args={args:?} status={:?}\nstdout={}\nstderr={}",
            output.status.code(),
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr)
        );
        assert!(
            String::from_utf8_lossy(&output.stdout).contains("src/app.log:1:1:ERROR failed"),
            "args={args:?} stdout={}",
            String::from_utf8_lossy(&output.stdout)
        );
        assert!(
            !String::from_utf8_lossy(&output.stderr).contains("unexpected argument"),
            "args={args:?} stderr={}",
            String::from_utf8_lossy(&output.stderr)
        );
    }
}

#[test]
fn test_lsp_provider_args_are_forwarded_on_public_native_frontdoor() {
    let dir = tempdir().unwrap();
    let fake_python = fake_python_passthrough_asserting_args_script(
        dir.path(),
        &["-m", "tensor_grep", "lsp", "--provider", "native"],
        "lsp passthrough ok\n",
    );

    let output = tg()
        .current_dir(dir.path())
        .args(["lsp", "--provider", "native"])
        .env("TG_SIDECAR_PYTHON", &fake_python)
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "status={:?}\nstdout={}\nstderr={}",
        output.status.code(),
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(
        String::from_utf8_lossy(&output.stdout).contains("lsp passthrough ok"),
        "stdout={}",
        String::from_utf8_lossy(&output.stdout)
    );
    assert!(
        !String::from_utf8_lossy(&output.stderr).contains("unexpected argument"),
        "stderr={}",
        String::from_utf8_lossy(&output.stderr)
    );
}

#[test]
fn test_lsp_help_is_forwarded_to_python_help_on_public_native_frontdoor() {
    let dir = tempdir().unwrap();
    let fake_python = fake_python_passthrough_asserting_args_script(
        dir.path(),
        &["-m", "tensor_grep", "lsp", "--help"],
        "lsp help with --provider hybrid experimental\n",
    );

    let output = tg()
        .current_dir(dir.path())
        .args(["lsp", "--help"])
        .env("TG_SIDECAR_PYTHON", &fake_python)
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "status={:?}\nstdout={}\nstderr={}",
        output.status.code(),
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(stdout.contains("--provider hybrid"), "stdout={stdout}");
    assert!(stdout.contains("experimental"), "stdout={stdout}");
    assert!(
        !String::from_utf8_lossy(&output.stderr).contains("unexpected argument"),
        "stderr={}",
        String::from_utf8_lossy(&output.stderr)
    );
}

#[test]
fn test_search_files_and_null_path_output_work_on_public_native_frontdoor() {
    let dir = tempdir().unwrap();
    let fake_python = fake_python_passthrough_script(dir.path(), "visible.rs");
    let fake_rg = fake_rg_script(dir.path(), "app.log\0");
    fs::write(dir.path().join("visible.rs"), "fn main() {}\n").unwrap();
    fs::write(dir.path().join("app.log"), "INFO ok\nERROR failed\n").unwrap();

    let files_output = tg()
        .current_dir(dir.path())
        .args(["search", "--files", ".", "--hidden", "--glob", "*.rs"])
        .env("TG_SIDECAR_PYTHON", &fake_python)
        .output()
        .unwrap();
    assert!(
        files_output.status.success(),
        "status={:?}\nstdout={}\nstderr={}",
        files_output.status.code(),
        String::from_utf8_lossy(&files_output.stdout),
        String::from_utf8_lossy(&files_output.stderr)
    );
    assert!(
        String::from_utf8_lossy(&files_output.stdout).contains("visible.rs"),
        "stdout={}",
        String::from_utf8_lossy(&files_output.stdout)
    );

    let null_output = tg()
        .current_dir(dir.path())
        .args([
            "search",
            "--fixed-strings",
            "ERROR",
            ".",
            "--files-with-matches",
            "--null",
        ])
        .env("TG_RG_PATH", &fake_rg)
        .output()
        .unwrap();
    assert!(
        null_output.status.success(),
        "status={:?}\nstdout={}\nstderr={}",
        null_output.status.code(),
        String::from_utf8_lossy(&null_output.stdout),
        String::from_utf8_lossy(&null_output.stderr)
    );
    assert!(
        null_output.stdout.contains(&0),
        "expected NUL-separated path output, stdout bytes={:?}",
        null_output.stdout
    );
}

#[test]
fn test_search_help_advertised_rg_flags_are_accepted_on_public_native_frontdoor() {
    let dir = tempdir().unwrap();
    let fake_rg = fake_rg_script(dir.path(), "accepted\n");
    let fake_python = fake_python_passthrough_script(dir.path(), "accepted");
    fs::write(dir.path().join("app.log"), "ERROR failed\nINFO ok\n").unwrap();

    let flag_cases: &[(&str, &[&str])] = &[
        ("-H", &["search", "-H", "ERROR", "."]),
        (
            "--with-filename",
            &["search", "--with-filename", "ERROR", "."],
        ),
        ("-I", &["search", "-I", "ERROR", "."]),
        ("--no-filename", &["search", "--no-filename", "ERROR", "."]),
        ("-q", &["search", "-q", "ERROR", "."]),
        ("--quiet", &["search", "--quiet", "ERROR", "."]),
        ("-N", &["search", "-N", "ERROR", "."]),
        (
            "--no-line-number",
            &["search", "--no-line-number", "ERROR", "."],
        ),
        ("--engine", &["search", "--engine", "auto", "ERROR", "."]),
        ("-s", &["search", "-s", "ERROR", "."]),
        ("-x", &["search", "-x", "ERROR", "."]),
        ("-j", &["search", "-j", "1", "ERROR", "."]),
        ("--iglob", &["search", "--iglob", "*.log", "ERROR", "."]),
        ("-T", &["search", "-T", "rust", "ERROR", "."]),
        ("-u", &["search", "-u", "ERROR", "."]),
        ("--stats", &["search", "--stats", "ERROR", "."]),
        ("--debug", &["search", "--debug", "ERROR", "."]),
        ("--trace", &["search", "--trace", "ERROR", "."]),
        ("--passthru", &["search", "--passthru", "ERROR", "."]),
        ("--passthrough", &["search", "--passthrough", "ERROR", "."]),
        ("--unicode", &["search", "--unicode", "ERROR", "."]),
        (
            "--auto-hybrid-regex",
            &["search", "--auto-hybrid-regex", "ERROR", "."],
        ),
        (
            "--no-auto-hybrid-regex",
            &["search", "--no-auto-hybrid-regex", "ERROR", "."],
        ),
        (
            "--no-pcre2-unicode",
            &["search", "--no-pcre2-unicode", "ERROR", "."],
        ),
        ("--no-text", &["search", "--no-text", "ERROR", "."]),
        ("--no-binary", &["search", "--no-binary", "ERROR", "."]),
        ("--no-follow", &["search", "--no-follow", "ERROR", "."]),
        (
            "--no-glob-case-insensitive",
            &["search", "--no-glob-case-insensitive", "ERROR", "."],
        ),
        (
            "--no-ignore-file-case-insensitive",
            &["search", "--no-ignore-file-case-insensitive", "ERROR", "."],
        ),
        (
            "--no-ignore-dot",
            &["search", "--no-ignore-dot", "ERROR", "."],
        ),
        ("--ignore-dot", &["search", "--ignore-dot", "ERROR", "."]),
        (
            "--no-ignore-exclude",
            &["search", "--no-ignore-exclude", "ERROR", "."],
        ),
        (
            "--ignore-exclude",
            &["search", "--ignore-exclude", "ERROR", "."],
        ),
        (
            "--no-ignore-files",
            &["search", "--no-ignore-files", "ERROR", "."],
        ),
        (
            "--ignore-files",
            &["search", "--ignore-files", "ERROR", "."],
        ),
        (
            "--no-ignore-global",
            &["search", "--no-ignore-global", "ERROR", "."],
        ),
        (
            "--ignore-global",
            &["search", "--ignore-global", "ERROR", "."],
        ),
        (
            "--ignore-messages",
            &["search", "--ignore-messages", "ERROR", "."],
        ),
        (
            "--no-ignore-parent",
            &["search", "--no-ignore-parent", "ERROR", "."],
        ),
        (
            "--ignore-parent",
            &["search", "--ignore-parent", "ERROR", "."],
        ),
        ("--ignore-vcs", &["search", "--ignore-vcs", "ERROR", "."]),
        ("--ignore", &["search", "--ignore", "ERROR", "."]),
        ("--messages", &["search", "--messages", "ERROR", "."]),
        ("--require-git", &["search", "--require-git", "ERROR", "."]),
        ("--no-hidden", &["search", "--no-hidden", "ERROR", "."]),
        (
            "--pcre2-unicode",
            &["search", "--pcre2-unicode", "ERROR", "."],
        ),
        (
            "--no-one-file-system",
            &["search", "--no-one-file-system", "ERROR", "."],
        ),
        (
            "--no-block-buffered",
            &["search", "--no-block-buffered", "ERROR", "."],
        ),
        (
            "--no-byte-offset",
            &["search", "--no-byte-offset", "ERROR", "."],
        ),
        ("--no-column", &["search", "--no-column", "ERROR", "."]),
        ("--no-crlf", &["search", "--no-crlf", "ERROR", "."]),
        ("--no-encoding", &["search", "--no-encoding", "ERROR", "."]),
        (
            "--no-fixed-strings",
            &["search", "--no-fixed-strings", "ERROR", "."],
        ),
        (
            "--no-invert-match",
            &["search", "--no-invert-match", "ERROR", "."],
        ),
        ("--no-mmap", &["search", "--no-mmap", "ERROR", "."]),
        (
            "--no-multiline",
            &["search", "--no-multiline", "ERROR", "."],
        ),
        (
            "--no-multiline-dotall",
            &["search", "--no-multiline-dotall", "ERROR", "."],
        ),
        ("--no-pcre2", &["search", "--no-pcre2", "ERROR", "."]),
        ("--no-pre", &["search", "--no-pre", "ERROR", "."]),
        (
            "--no-search-zip",
            &["search", "--no-search-zip", "ERROR", "."],
        ),
        (
            "--no-context-separator",
            &["search", "--no-context-separator", "ERROR", "."],
        ),
        (
            "--no-include-zero",
            &["search", "--no-include-zero", "ERROR", "."],
        ),
        (
            "--no-line-buffered",
            &["search", "--no-line-buffered", "ERROR", "."],
        ),
        (
            "--no-max-columns-preview",
            &["search", "--no-max-columns-preview", "ERROR", "."],
        ),
        ("--no-trim", &["search", "--no-trim", "ERROR", "."]),
        ("--no-json", &["search", "--no-json", "ERROR", "."]),
        ("--no-stats", &["search", "--no-stats", "ERROR", "."]),
        ("--no-config", &["search", "--no-config", "ERROR", "."]),
        ("--type-list", &["search", "--type-list"]),
        ("--pcre2-version", &["search", "--pcre2-version"]),
    ];

    for (flag, args) in flag_cases {
        let output = tg()
            .current_dir(dir.path())
            .args(*args)
            .env("TG_RG_PATH", &fake_rg)
            .env("TG_SIDECAR_PYTHON", &fake_python)
            .output()
            .unwrap();

        assert!(
            output.status.success(),
            "flag={flag} status={:?}\nstdout={}\nstderr={}",
            output.status.code(),
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr)
        );
        assert!(
            !String::from_utf8_lossy(&output.stderr).contains("unexpected argument"),
            "flag={flag} stderr={}",
            String::from_utf8_lossy(&output.stderr)
        );
    }
}

#[test]
fn test_search_version_and_top_level_version_do_not_enter_search_parser() {
    for args in [&["--version"][..], &["search", "--version"][..]] {
        let output = tg().args(args).output().unwrap();

        assert!(
            output.status.success(),
            "args={args:?} status={:?}\nstdout={}\nstderr={}",
            output.status.code(),
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr)
        );
        assert!(
            String::from_utf8_lossy(&output.stdout).contains("tg "),
            "stdout={}",
            String::from_utf8_lossy(&output.stdout)
        );
    }
}

#[test]
fn test_search_frontdoor_forwards_rg_inverse_config_override_flags() {
    let dir = tempdir().unwrap();
    let expected = [
        "--no-auto-hybrid-regex",
        "--no-pcre2-unicode",
        "--no-text",
        "--no-binary",
        "--no-follow",
        "--no-glob-case-insensitive",
        "--no-ignore-file-case-insensitive",
        "--ignore-dot",
        "--ignore-exclude",
        "--ignore-files",
        "--ignore-global",
        "--ignore-messages",
        "--ignore-parent",
        "--ignore-vcs",
        "--no-one-file-system",
        "--no-block-buffered",
        "--no-byte-offset",
        "--no-column",
        "--no-crlf",
        "--no-encoding",
        "--no-fixed-strings",
        "--no-invert-match",
        "--no-mmap",
        "--no-multiline",
        "--no-multiline-dotall",
        "--no-pcre2",
        "--no-pre",
        "--no-search-zip",
        "--no-context-separator",
        "--no-include-zero",
        "--no-line-buffered",
        "--no-max-columns-preview",
        "--no-trim",
        "--no-json",
        "--no-stats",
        "-e",
        "ERROR",
        ".",
    ];
    let fake_rg = fake_rg_asserting_args_script(dir.path(), &expected, "accepted\n");
    fs::write(dir.path().join("app.log"), "ERROR failed\nINFO ok\n").unwrap();

    let output = tg()
        .current_dir(dir.path())
        .args([
            "search",
            "--format",
            "rg",
            "--no-auto-hybrid-regex",
            "--no-pcre2-unicode",
            "--no-text",
            "--no-binary",
            "--no-follow",
            "--no-glob-case-insensitive",
            "--no-ignore-file-case-insensitive",
            "--ignore-dot",
            "--ignore-exclude",
            "--ignore-files",
            "--ignore-global",
            "--ignore-messages",
            "--ignore-parent",
            "--ignore-vcs",
            "--no-one-file-system",
            "--no-block-buffered",
            "--no-byte-offset",
            "--no-column",
            "--no-crlf",
            "--no-encoding",
            "--no-fixed-strings",
            "--no-invert-match",
            "--no-mmap",
            "--no-multiline",
            "--no-multiline-dotall",
            "--no-pcre2",
            "--no-pre",
            "--no-search-zip",
            "--no-context-separator",
            "--no-include-zero",
            "--no-line-buffered",
            "--no-max-columns-preview",
            "--no-trim",
            "--no-json",
            "--no-stats",
            "ERROR",
            ".",
        ])
        .env("TG_RG_PATH", &fake_rg)
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "status={:?}\nstdout={}\nstderr={}",
        output.status.code(),
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    assert_eq!(
        String::from_utf8_lossy(&output.stdout).replace("\r\n", "\n"),
        "accepted\n"
    );
}

#[test]
fn test_search_frontdoor_forwards_rg_config_override_flags() {
    let dir = tempdir().unwrap();
    let fake_rg = fake_rg_asserting_args_script(
        dir.path(),
        &[
            "--pcre2-unicode",
            "--require-git",
            "--ignore",
            "--no-hidden",
            "--messages",
            "-e",
            "ERROR",
            ".",
        ],
        "accepted\n",
    );
    fs::write(dir.path().join("app.log"), "ERROR failed\nINFO ok\n").unwrap();

    let output = tg()
        .current_dir(dir.path())
        .args([
            "search",
            "--format",
            "rg",
            "--pcre2-unicode",
            "--ignore",
            "--messages",
            "--require-git",
            "--no-hidden",
            "ERROR",
            ".",
        ])
        .env("TG_RG_PATH", &fake_rg)
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "status={:?}\nstdout={}\nstderr={}",
        output.status.code(),
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    assert_eq!(
        String::from_utf8_lossy(&output.stdout).replace("\r\n", "\n"),
        "accepted\n"
    );
}

#[test]
fn test_option_first_root_search_flags_forward_to_search_frontdoor() {
    let dir = tempdir().unwrap();
    let fake_rg = fake_rg_asserting_args_script(
        dir.path(),
        &["--sort", "path", "-n", "-F", "-e", "ERROR", "."],
        "accepted\n",
    );
    fs::write(dir.path().join("app.log"), "ERROR failed\nINFO ok\n").unwrap();

    let output = tg()
        .current_dir(dir.path())
        .args(["--sort", "path", "-n", "-F", "ERROR", "."])
        .env("TG_RG_PATH", &fake_rg)
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "status={:?}\nstdout={}\nstderr={}",
        output.status.code(),
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    assert_eq!(
        String::from_utf8_lossy(&output.stdout).replace("\r\n", "\n"),
        "accepted\n"
    );
}

#[test]
fn test_option_first_root_context_flags_forward_to_search_frontdoor() {
    let dir = tempdir().unwrap();
    let fake_rg = fake_rg_asserting_args_script(
        dir.path(),
        &["-n", "-C", "2", "-e", "ERROR", "."],
        "accepted\n",
    );
    fs::write(
        dir.path().join("app.log"),
        "INFO before\nERROR failed\nINFO after\n",
    )
    .unwrap();

    let output = tg()
        .current_dir(dir.path())
        .args(["-n", "-C", "2", "ERROR", "."])
        .env("TG_RG_PATH", &fake_rg)
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "status={:?}\nstdout={}\nstderr={}",
        output.status.code(),
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    assert_eq!(
        String::from_utf8_lossy(&output.stdout).replace("\r\n", "\n"),
        "accepted\n"
    );
}

#[test]
fn test_unbounded_no_ignore_search_allows_project_root_generated_child_dirs() {
    let dir = tempdir().unwrap();
    let fake_rg = fake_rg_script(dir.path(), "rg-route\n");
    fs::write(dir.path().join("app.log"), "ERROR failed\n").unwrap();
    fs::create_dir(dir.path().join("AppData")).unwrap();

    let output = tg()
        .current_dir(dir.path())
        .args(["search", "ERROR", ".", "--hidden", "--no-ignore"])
        .env("TG_RG_PATH", &fake_rg)
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "status={:?}\nstdout={}\nstderr={}",
        output.status.code(),
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    assert_eq!(
        String::from_utf8_lossy(&output.stdout).replace("\r\n", "\n"),
        "rg-route\n"
    );
}

#[test]
fn test_unbounded_no_ignore_search_still_refuses_generated_root_itself() {
    let dir = tempdir().unwrap();
    let venv = dir.path().join(".venv");
    fs::create_dir(&venv).unwrap();
    fs::write(venv.join("app.log"), "ERROR failed\n").unwrap();
    let fake_rg = fake_rg_script(dir.path(), "rg-route\n");

    let output = tg()
        .current_dir(dir.path())
        .args(["search", "ERROR", ".venv", "--hidden", "--no-ignore"])
        .env("TG_RG_PATH", &fake_rg)
        .output()
        .unwrap();

    assert!(
        !output.status.success(),
        "status={:?}\nstdout={}\nstderr={}",
        output.status.code(),
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    assert_eq!(output.status.code(), Some(2));
    assert_eq!(
        String::from_utf8_lossy(&output.stdout).replace("\r\n", "\n"),
        ""
    );
    assert!(
        String::from_utf8_lossy(&output.stderr).contains("broad generated-root scan refused"),
        "stderr={}",
        String::from_utf8_lossy(&output.stderr)
    );
}

#[test]
fn test_early_rg_no_ignore_search_allows_project_root_generated_child_dirs() {
    let dir = tempdir().unwrap();
    let fake_rg = fake_rg_script(dir.path(), "rg-route\n");
    fs::write(dir.path().join("app.log"), "ERROR failed\n").unwrap();
    fs::create_dir(dir.path().join("AppData")).unwrap();

    let output = tg()
        .current_dir(dir.path())
        .args(["search", "ERROR", ".", "--hidden", "--no-ignore"])
        .env("TG_RG_PATH", &fake_rg)
        .env("TG_RUST_EARLY_RG", "1")
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "status={:?}\nstdout={}\nstderr={}",
        output.status.code(),
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    assert_eq!(
        String::from_utf8_lossy(&output.stdout).replace("\r\n", "\n"),
        "rg-route\n"
    );
}

#[test]
fn test_option_first_root_type_flag_forwards_to_search_frontdoor() {
    let dir = tempdir().unwrap();
    let fake_rg =
        fake_rg_asserting_args_script(dir.path(), &["-t", "js", "-e", "ERROR", "."], "accepted\n");
    fs::write(dir.path().join("app.js"), "console.error('ERROR');\n").unwrap();

    let output = tg()
        .current_dir(dir.path())
        .args(["-t", "js", "ERROR", "."])
        .env("TG_RG_PATH", &fake_rg)
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "status={:?}\nstdout={}\nstderr={}",
        output.status.code(),
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    assert_eq!(
        String::from_utf8_lossy(&output.stdout).replace("\r\n", "\n"),
        "accepted\n"
    );
}

#[test]
fn test_option_first_root_count_matches_forwards_to_search_frontdoor() {
    let dir = tempdir().unwrap();
    let fake_rg = fake_rg_asserting_args_script(
        dir.path(),
        &["--count-matches", "-e", "ERROR", "."],
        "app.js:2\n",
    );
    fs::write(
        dir.path().join("app.js"),
        "console.error('ERROR');\nconsole.error('ERROR');\n",
    )
    .unwrap();

    let output = tg()
        .current_dir(dir.path())
        .args(["--count-matches", "ERROR", "."])
        .env("TG_RG_PATH", &fake_rg)
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "status={:?}\nstdout={}\nstderr={}",
        output.status.code(),
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    assert_eq!(
        String::from_utf8_lossy(&output.stdout).replace("\r\n", "\n"),
        "app.js:2\n"
    );
}

#[test]
fn test_option_first_root_search_forwards_no_line_number_to_rg() {
    let dir = tempdir().unwrap();
    let fake_rg =
        fake_rg_asserting_args_script(dir.path(), &["-N", "-F", "ERROR", "."], "accepted\n");
    fs::write(dir.path().join("app.log"), "ERROR failed\nINFO ok\n").unwrap();

    let output = tg()
        .current_dir(dir.path())
        .args(["-N", "-F", "ERROR", "."])
        .env("TG_RG_PATH", &fake_rg)
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "status={:?}\nstdout={}\nstderr={}",
        output.status.code(),
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    assert_eq!(
        String::from_utf8_lossy(&output.stdout).replace("\r\n", "\n"),
        "accepted\n"
    );
}

#[test]
fn test_search_version_is_accepted_on_public_native_frontdoor() {
    let dir = tempdir().unwrap();

    let output = tg()
        .current_dir(dir.path())
        .args(["search", "--version"])
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "status={:?}\nstdout={}\nstderr={}",
        output.status.code(),
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(
        String::from_utf8_lossy(&output.stdout).contains("tg "),
        "stdout={}",
        String::from_utf8_lossy(&output.stdout)
    );
}

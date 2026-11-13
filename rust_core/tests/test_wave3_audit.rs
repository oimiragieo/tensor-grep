//! Native index completeness, binary policy, ignore semantics, and search-error regressions.
use std::collections::HashSet;
use std::fs::{self, File, FileTimes};
use std::io::{BufWriter, Write};
use std::path::Path;
use std::process::{Command, Stdio};
use std::time::{Duration, UNIX_EPOCH};

use process_control::{ChildExt, Control, Output};
use serde_json::Value;
use tempfile::tempdir;
use tensor_grep_rs::index::{IndexQueryResult, TrigramIndex};

fn command(root: &Path, args: &[&str]) -> Command {
    let mut cmd = Command::new(env!("CARGO_BIN_EXE_tg"));
    cmd.current_dir(root)
        .env_remove("TG_RG_PATH")
        .env_remove("RIPGREP_CONFIG_PATH")
        .args(args);
    cmd
}

fn bounded(mut cmd: Command) -> Output {
    cmd.stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    cmd.spawn()
        .expect("SETUP: spawn native tg")
        .controlled_with_output()
        .time_limit(Duration::from_secs(30))
        .terminate_for_timeout()
        .wait()
        .expect("SETUP: wait for native tg")
        .expect("native tg exceeded 30-second bound")
}

fn run(root: &Path, args: &[&str]) -> Output {
    bounded(command(root, args))
}

fn payload(output: &Output) -> Value {
    serde_json::from_slice(&output.stdout).unwrap_or_else(|_| {
        panic!(
            "expected JSON; status={:?}, stdout_bytes={}, stderr={}",
            output.status.code(),
            output.stdout.len(),
            String::from_utf8_lossy(&output.stderr)
        )
    })
}

fn json_search(root: &Path, flags: &[&str], needle: &str) -> Value {
    let mut args = vec!["search", "--json", "-F"];
    args.extend_from_slice(flags);
    args.extend_from_slice(&[needle, "."]);
    let output = run(root, &args);
    assert_eq!(output.status.code(), Some(0));
    let value = payload(&output);
    assert_eq!(value["sidecar_used"], false);
    value
}

fn assert_rows(rows: &[IndexQueryResult], names: &[&str]) {
    assert_eq!(rows.len(), names.len(), "unexpected number of text rows");
    let actual: HashSet<_> = rows
        .iter()
        .map(|row| {
            assert_eq!(row.line, 1);
            row.file.file_name().unwrap().to_str().unwrap()
        })
        .collect();
    assert_eq!(actual.len(), rows.len(), "duplicate file/line rows");
    assert_eq!(actual, names.iter().copied().collect());
}

fn large_corpus(root: &Path) {
    fs::write(root.join("small.txt"), "sharedneedle smallpositive\n").unwrap();
    fs::write(root.join("empty.txt"), "").unwrap();
    let mut writer = BufWriter::new(File::create(root.join("big.log")).unwrap());
    let header = b"uniquebigneedle sharedneedle\n";
    writer.write_all(header).unwrap();
    // ASCII, not sparse/NUL: binary detection must not explain away the oversized file.
    let filler = [b'x'; 4095];
    let mut written = header.len();
    while written <= 10 * 1024 * 1024 {
        writer.write_all(&filler).unwrap();
        writer.write_all(b"\n").unwrap();
        written += filler.len() + 1;
    }
    writer.flush().unwrap();
    assert!(fs::metadata(root.join("big.log")).unwrap().len() > 10 * 1024 * 1024);
}

fn require_rg(root: &Path) {
    let mut cmd = Command::new("rg");
    cmd.current_dir(root)
        .arg("--version")
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    let child = cmd
        .spawn()
        .expect("BLOCKED_SETUP: real rg must be available on PATH");
    let output = child
        .controlled_with_output()
        .time_limit(Duration::from_secs(30))
        .terminate_for_timeout()
        .wait()
        .unwrap()
        .expect("rg --version timed out");
    assert!(
        output.status.success(),
        "BLOCKED_SETUP: rg --version failed"
    );
}

fn early_close(mut cmd: Command) -> (Vec<u8>, Output) {
    use std::io::BufRead;
    use std::sync::mpsc;
    cmd.stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    let mut child = cmd.spawn().expect("SETUP: early-close child spawn");
    let stdout = child.stdout.take().unwrap();
    let (tx, rx) = mpsc::channel();
    let reader = std::thread::spawn(move || {
        let mut line = Vec::new();
        let result = std::io::BufReader::new(stdout).read_until(b'\n', &mut line);
        // BufReader (and its pipe handle) drops before the consumer acknowledges the first line.
        let _ = tx.send(result.map(|_| line));
    });
    let first = match rx.recv_timeout(Duration::from_secs(30)) {
        Ok(result) => result.expect("first-line read failed"),
        Err(error) => {
            let _ = child.kill();
            let _ = child
                .controlled_with_output()
                .time_limit(Duration::from_secs(5))
                .terminate_for_timeout()
                .wait();
            drop(reader); // A descendant may still hold stdout open after the child is killed.
            panic!("first-line read exceeded bound: {error}");
        }
    };
    reader.join().unwrap();
    assert!(
        !first.is_empty(),
        "fixture must produce a match before pipe closes"
    );
    let output = child
        .controlled_with_output()
        .time_limit(Duration::from_secs(30))
        .terminate_for_timeout()
        .wait()
        .unwrap()
        .expect("early-close child timed out");
    (first, output)
}

#[test]
fn oversized_unique_empty_postings_and_persisted_index() {
    let root = tempdir().unwrap();
    let cache = tempdir().unwrap();
    large_corpus(root.path());
    let index = TrigramIndex::build(root.path()).unwrap();
    index.save(&cache.path().join("index")).unwrap();
    let loaded = TrigramIndex::load(&cache.path().join("index")).unwrap();
    for index in [&index, &loaded] {
        assert_rows(
            &index.search("smallpositive", false, true).unwrap(),
            &["small.txt"],
        );
        assert_rows(
            &index.search("uniquebigneedle", false, true).unwrap(),
            &["big.log"],
        );
        assert!(index
            .search("absentneedle", false, true)
            .unwrap()
            .is_empty());
        assert_rows(
            &index.search("^uniquebigneedle", false, false).unwrap(),
            &["big.log"],
        );
        assert_rows(&index.search("^u", false, false).unwrap(), &["big.log"]);
    }
}

#[test]
fn oversized_shared_postings_return_each_file_once() {
    let root = tempdir().unwrap();
    large_corpus(root.path());
    let index = TrigramIndex::build(root.path()).unwrap();
    assert_rows(
        &index.search("sharedneedle", false, true).unwrap(),
        &["big.log", "small.txt"],
    );
}

#[test]
fn oversized_native_cold_explicit_and_warm_index() {
    let root = tempdir().unwrap();
    large_corpus(root.path());
    let cold = json_search(root.path(), &[], "uniquebigneedle");
    assert_eq!(cold["routing_backend"], "NativeCpuBackend");
    assert_eq!(cold["total_matches"], 1);
    let indexed = json_search(root.path(), &["--index"], "uniquebigneedle");
    assert_eq!(indexed["routing_backend"], "TrigramIndex");
    assert_eq!(indexed["total_matches"], 1);
    let warm = json_search(root.path(), &[], "sharedneedle");
    assert_eq!(warm["routing_backend"], "TrigramIndex");
    assert_eq!(warm["total_matches"], 2);
    let rows = warm["matches"].as_array().unwrap();
    let identities: HashSet<_> = rows
        .iter()
        .map(|v| (v["file"].as_str().unwrap(), v["line"].as_u64().unwrap()))
        .collect();
    assert_eq!(identities.len(), rows.len());
    assert_eq!(
        json_search(root.path(), &["--index"], "smallpositive")["total_matches"],
        1
    );
    assert_eq!(
        json_search(root.path(), &["--index"], "absentneedle")["total_matches"],
        0
    );
}

#[test]
fn no_ignore_build_honors_dot_ignore_override() {
    let root = tempdir().unwrap();
    fs::write(root.path().join(".ignore"), "secret.txt\n").unwrap();
    fs::write(root.path().join("secret.txt"), "secretneedle\n").unwrap();
    fs::write(root.path().join("visible.txt"), "visiblepositive\n").unwrap();
    let normal = TrigramIndex::build_with_options(root.path(), false).unwrap();
    assert!(normal
        .search("secretneedle", false, true)
        .unwrap()
        .is_empty());
    assert_rows(
        &normal.search("visiblepositive", false, true).unwrap(),
        &["visible.txt"],
    );
    let unrestricted = TrigramIndex::build_with_options(root.path(), true).unwrap();
    assert_rows(
        &unrestricted.search("secretneedle", false, true).unwrap(),
        &["secret.txt"],
    );
}

#[test]
fn saved_no_ignore_cache_discovers_previously_omitted_file() {
    let root = tempdir().unwrap();
    fs::write(root.path().join(".ignore"), "secret.txt\n").unwrap();
    fs::write(root.path().join("visible.txt"), "visiblepositive\n").unwrap();
    // Save before secret exists: no byte patch or proposed API is needed for this migration.
    let initial = TrigramIndex::build_with_options(root.path(), true).unwrap();
    initial.save(&root.path().join(".tg_index")).unwrap();
    fs::write(root.path().join("secret.txt"), "secretneedle\n").unwrap();
    let cold = json_search(root.path(), &["--cpu", "--no-ignore"], "secretneedle");
    assert_eq!(cold["routing_backend"], "NativeCpuBackend");
    assert_eq!(cold["total_matches"], 1);
    let cached = json_search(root.path(), &["--index", "--no-ignore"], "secretneedle");
    assert_eq!(cached["routing_backend"], "TrigramIndex");
    assert_eq!(cached["total_matches"], 1);
    assert_eq!(
        json_search(root.path(), &["--index", "--no-ignore"], "visiblepositive")["total_matches"],
        1
    );
}

#[test]
fn public_index_search_keeps_vec_contract_without_binary_text() {
    let root = tempdir().unwrap();
    for offset in [0, 65535] {
        let mut bytes = vec![b'x'; offset];
        bytes.extend_from_slice(b"\0binaryneedle\n");
        fs::write(root.path().join(format!("blob-{offset}.bin")), bytes).unwrap();
    }
    fs::write(root.path().join("ordinary.txt"), "ordinarypositive\n").unwrap();
    let index = TrigramIndex::build(root.path()).unwrap();
    assert_rows(
        &index.search("ordinarypositive", false, true).unwrap(),
        &["ordinary.txt"],
    );
    let rows: Vec<IndexQueryResult> = index.search("binaryneedle", false, true).unwrap();
    assert!(rows.is_empty(), "binary file must not be exposed as text");
    let rows: Vec<IndexQueryResult> = index.search("^x", false, false).unwrap();
    assert!(
        rows.is_empty(),
        "full-scan route must classify binary files too"
    );
}

#[test]
fn native_binary_policy_matches_cold_explicit_and_warm_json() {
    let root = tempdir().unwrap();
    fs::write(root.path().join("blob.bin"), b"binaryneedle\0payload\n").unwrap();
    fs::write(root.path().join("ordinary.txt"), "ordinarypositive\n").unwrap();
    for (step, flags) in [&[][..], &["--index"][..], &[][..]].into_iter().enumerate() {
        let value = json_search(root.path(), flags, "binaryneedle");
        assert_eq!(
            value["routing_backend"],
            if step == 0 {
                "NativeCpuBackend"
            } else {
                "TrigramIndex"
            },
            "step={step} flags={flags:?} payload={value:?}"
        );
        assert_eq!(
            value["binary_match_files"], 1,
            "step={step} flags={flags:?} payload={value:?}"
        );
        assert_eq!(
            value["total_matches"], 0,
            "step={step} flags={flags:?} payload={value:?}"
        );
        assert!(
            value["matches"].as_array().unwrap().is_empty(),
            "step={step} flags={flags:?} payload={value:?}"
        );
    }
    assert_eq!(
        json_search(root.path(), &["--index"], "ordinarypositive")["total_matches"],
        1
    );
}

#[test]
fn indexed_invalid_utf8_remains_lossy_without_changing_cold_byte_policy() {
    let root = tempdir().unwrap();
    fs::write(root.path().join("bad.txt"), b"needle\xff\n").unwrap();
    let index = TrigramIndex::build(root.path()).unwrap();
    let rows: Vec<IndexQueryResult> = index.search("needle", false, true).unwrap();
    assert_eq!(rows.len(), 1);
    assert_eq!(rows[0].text, "needle\u{fffd}");
    let value = json_search(root.path(), &["--index"], "needle");
    assert_eq!(value["matches"][0]["text"], "needle\u{fffd}");
    assert!(value["matches"][0].get("bytes").is_none());
}

#[test]
fn indexed_count_and_binary_ndjson_keep_existing_output_shapes() {
    let root = tempdir().unwrap();
    fs::write(root.path().join("a.txt"), "needle\nneedle\n").unwrap();
    let count = run(root.path(), &["search", "--index", "-c", "needle", "."]);
    assert_eq!(count.status.code(), Some(0));
    let text = String::from_utf8_lossy(&count.stdout);
    assert!(text.trim_end().ends_with("a.txt:2"));
    let no_match = run(
        root.path(),
        &["search", "--index", "-c", "missingneedle", "."],
    );
    assert_eq!(no_match.status.code(), Some(1));
    let structured = json_search(root.path(), &["--index", "-c"], "needle");
    assert_eq!(structured["total_matches"], 2);
    let binary = tempdir().unwrap();
    fs::write(binary.path().join("blob.bin"), b"binaryneedle\0payload\n").unwrap();
    for flags in [&[][..], &["--index"][..], &[][..]] {
        let mut args = vec!["search", "--ndjson", "-F"];
        args.extend_from_slice(flags);
        args.extend_from_slice(&["binaryneedle", "."]);
        let output = run(binary.path(), &args);
        assert_eq!(output.status.code(), Some(0));
        let records: Vec<Value> = std::str::from_utf8(&output.stdout)
            .unwrap()
            .lines()
            .map(|line| serde_json::from_str(line).unwrap())
            .collect();
        assert_eq!(records.len(), 1, "binary data must not produce text rows");
        let summary = &records[0];
        assert_eq!(summary["type"], "summary");
        assert_eq!(summary["binary_match_files"], 1);
        assert_eq!(summary["total_matches"], 0);
    }
}

#[test]
fn failed_rg_spawn_exits_two_in_search_and_root_plain_forms() {
    let root = tempdir().unwrap();
    fs::write(root.path().join("a.txt"), "needle\n").unwrap();
    let bad = root.path().join("not-executable.txt");
    fs::write(&bad, "owned ordinary text; never a foreign executable\n").unwrap();
    for prefix in [&["search"][..], &[][..]] {
        let mut args = prefix.to_vec();
        args.extend_from_slice(&["-P", "needle", "."]);
        let mut cmd = command(root.path(), &args);
        cmd.env("TG_RG_PATH", &bad);
        let output = bounded(cmd);
        assert_eq!(
            output.status.code(),
            Some(2),
            "stdout={}; stderr={}",
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr)
        );
        assert!(output.stdout.is_empty());
        assert!(!output.stderr.is_empty());
    }
}

#[test]
fn unsupported_structured_pcre_request_refuses_with_envelope() {
    let root = tempdir().unwrap();
    fs::write(root.path().join("a.txt"), "needle\n").unwrap();
    fs::write(root.path().join("rg"), "--format\n--format=rg\n").unwrap();
    let bad = root.path().join("not-executable.txt");
    fs::write(&bad, "owned ordinary nonexecutable\n").unwrap();
    // Aggregate PCRE requests require an explicit unsupported-semantics refusal before execution.
    for (prefix, early) in [
        (&["search"][..], false),
        (&["search"][..], true),
        (&[][..], false),
        (&[][..], true),
    ] {
        for (format, explicit_format) in [
            ("--json", &[][..]),
            ("--ndjson", &[][..]),
            ("--ndjson", &["--format", "rg"][..]),
            ("--ndjson", &["--format=rg"][..]),
        ] {
            for query in [
                &["needle", "."][..],
                &["-e", "--format", "rg"][..],
                &["--regexp", "--format", "rg"][..],
                &["--regexp=--format", "rg"][..],
                &["-e", "--format=rg", "rg"][..],
                &["--regexp=--format=rg", "rg"][..],
                &["--", "--format", "rg"][..],
            ] {
                let mut args = prefix.to_vec();
                args.extend_from_slice(&[format, "-P"]);
                args.extend_from_slice(explicit_format);
                args.extend_from_slice(query);
                let mut cmd = command(root.path(), &args);
                cmd.env("TG_RUST_EARLY_RG", if early { "1" } else { "0" })
                    .env("TG_RUST_EARLY_POSITIONAL_RG", if early { "1" } else { "0" });
                cmd.env("TG_RG_PATH", &bad);
                let output = bounded(cmd);
                assert_eq!(
                    output.status.code(),
                    Some(2),
                    "stdout={}; stderr={}",
                    String::from_utf8_lossy(&output.stdout),
                    String::from_utf8_lossy(&output.stderr)
                );
                let value = payload(&output);
                assert_eq!(value["version"], 1);
                if format == "--json" {
                    assert_eq!(value["ok"], false);
                } else {
                    assert_eq!(value["type"], "error");
                }
                let code = value["error"].as_str().expect("structured refusal code");
                assert!(code.starts_with("unsupported"));
                assert_ne!(
                    code, "backend_error",
                    "this is a policy refusal, not spawn proof"
                );
                let detail = value["detail"].as_str().unwrap().to_ascii_lowercase();
                assert!(
                    detail.contains("refus")
                        || detail.contains("unsupported")
                        || detail.contains("not support")
                );
                assert!(detail.contains("pcre") || detail.contains("-p"));
            }
        }
    }
}

#[test]
fn generic_index_build_failure_has_backend_error_envelope() {
    let root = tempdir().unwrap();
    let path = root.path().join("a.txt");
    fs::write(&path, "needle\n").unwrap();
    // Cache persistence errors intentionally warn and serve; use a real metadata build error.
    let before_epoch = UNIX_EPOCH - Duration::from_secs(1);
    let file = File::options().write(true).open(&path).unwrap();
    file.set_times(FileTimes::new().set_modified(before_epoch))
        .expect("BLOCKED_SETUP: filesystem cannot set pre-epoch mtime");
    assert!(
        fs::metadata(&path).unwrap().modified().unwrap() < UNIX_EPOCH,
        "BLOCKED_SETUP: filesystem clamped pre-epoch mtime"
    );
    for format in ["--json", "--ndjson"] {
        let output = run(
            root.path(),
            &["search", "--index", format, "-F", "needle", "."],
        );
        assert_eq!(output.status.code(), Some(2));
        let value = payload(&output);
        assert_eq!(value["version"], 1);
        if format == "--json" {
            assert_eq!(value["ok"], false);
        } else {
            assert_eq!(value["type"], "error");
        }
        assert_eq!(value["error"], "backend_error");
        assert!(value["detail"].as_str().is_some_and(|s| !s.is_empty()));
    }
    file.set_times(FileTimes::new().set_modified(UNIX_EPOCH + Duration::from_secs(60)))
        .unwrap();
    assert_eq!(
        json_search(root.path(), &["--index"], "needle")["total_matches"],
        1
    );
}

#[test]
fn multiple_patterns_keep_explicit_index_refusal() {
    let root = tempdir().unwrap();
    fs::write(root.path().join("a.txt"), "foo bar\n").unwrap();
    let output = run(
        root.path(),
        &[
            "search", "--index", "--json", "-F", "-e", "foo", "-e", "bar", ".",
        ],
    );
    assert_eq!(output.status.code(), Some(2));
    let diagnostic = format!(
        "{}{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(diagnostic.contains("multiple patterns"));
}

#[test]
fn supported_output_and_no_match_and_missing_path_controls() {
    let root = tempdir().unwrap();
    fs::write(root.path().join("a.txt"), "needle\n").unwrap();
    let positive = run(root.path(), &["search", "-F", "needle", "."]);
    assert_eq!(positive.status.code(), Some(0));
    assert!(String::from_utf8_lossy(&positive.stdout).contains("needle"));
    let no_match = run(root.path(), &["search", "-F", "missingneedle", "."]);
    assert_eq!(no_match.status.code(), Some(1));
    assert_eq!(json_search(root.path(), &[], "needle")["total_matches"], 1);
    assert_eq!(
        json_search(root.path(), &["--index"], "missingneedle")["total_matches"],
        0
    );
    let ndjson = run(root.path(), &["search", "--ndjson", "-F", "needle", "."]);
    assert_eq!(ndjson.status.code(), Some(0));
    let records: Vec<Value> = std::str::from_utf8(&ndjson.stdout)
        .unwrap()
        .lines()
        .map(|line| serde_json::from_str(line).unwrap())
        .collect();
    let summary = records.last().unwrap();
    assert_eq!(summary["type"], "summary");
    assert_eq!(summary["total_matches"], 1);
    for format in ["--json", "--ndjson"] {
        let output = run(
            root.path(),
            &["search", format, "-F", "needle", "missing-path"],
        );
        assert_eq!(output.status.code(), Some(2));
        let value = payload(&output);
        assert_eq!(value["version"], 1);
        if format == "--json" {
            assert_eq!(value["ok"], false);
        } else {
            assert_eq!(value["type"], "error");
        }
        assert_eq!(value["error"], "path_not_found");
    }
}

#[test]
fn parsed_literals_and_end_of_options_do_not_activate_pcre_or_json() {
    let root = tempdir().unwrap();
    fs::write(root.path().join("a.txt"), "literal --json and -P\n").unwrap();
    fs::write(root.path().join("-P"), "needle\n").unwrap();
    for args in [
        vec!["search", "--json", "-F", "-e", "-P", "a.txt"],
        vec!["search", "--json", "-F", "--", "-P", "a.txt"],
        vec!["search", "--json", "-F", "needle", "--", "-P"],
    ] {
        let output = run(root.path(), &args);
        assert_eq!(output.status.code(), Some(0));
        assert_eq!(payload(&output)["total_matches"], 1);
    }
    let literal_json = run(root.path(), &["search", "-F", "--", "--json", "a.txt"]);
    assert_eq!(literal_json.status.code(), Some(0));
    assert!(String::from_utf8_lossy(&literal_json.stdout).contains("literal --json"));
    assert!(!literal_json.stdout.starts_with(b"{"));
}

#[test]
fn explicit_rg_json_keeps_raw_protocol_with_pcre() {
    let root = tempdir().unwrap();
    require_rg(root.path());
    let mut capability = Command::new("rg");
    capability.current_dir(root.path()).arg("--pcre2-version");
    assert!(
        bounded(capability).status.success(),
        "BLOCKED_SETUP: installed rg must support PCRE2 for raw-protocol control"
    );
    fs::write(root.path().join("a.txt"), "needle\n").unwrap();
    let marker = root.path().join("sidecar.marker");
    let sentinel = root.path().join(if cfg!(windows) {
        "sidecar.cmd"
    } else {
        "sidecar.sh"
    });
    let body = if cfg!(windows) {
        "@echo off\r\necho called>\"%TG_TEST_SIDECAR_MARKER%\"\r\nexit /b 73\r\n"
    } else {
        "#!/bin/sh\nprintf called > \"$TG_TEST_SIDECAR_MARKER\"\nexit 73\n"
    };
    fs::write(&sentinel, body).unwrap();
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(&sentinel, fs::Permissions::from_mode(0o755)).unwrap();
    }
    let mut calibration = command(root.path(), &["ast-info"]);
    calibration
        .env("TG_SIDECAR_PYTHON", &sentinel)
        .env("TG_TEST_SIDECAR_MARKER", &marker);
    assert_eq!(bounded(calibration).status.code(), Some(73));
    assert!(
        marker.exists(),
        "SETUP: sidecar sentinel must run before route discrimination"
    );
    fs::remove_file(&marker).unwrap();
    for (prefix, early) in [
        (&["search"][..], false),
        (&["search"][..], true),
        (&[][..], false),
        (&[][..], true),
    ] {
        for format in [&["--format", "rg"][..], &["--format=rg"][..]] {
            for options in [&["-P"][..], &["-P", "--no-mmap"][..], &["--no-mmap"][..]] {
                let mut args = prefix.to_vec();
                args.extend_from_slice(format);
                args.push("--json");
                args.extend_from_slice(options);
                args.extend_from_slice(&["needle", "a.txt"]);
                let mut cmd = command(root.path(), &args);
                cmd.env("TG_RUST_EARLY_RG", if early { "1" } else { "0" })
                    .env("TG_RUST_EARLY_POSITIONAL_RG", if early { "1" } else { "0" });
                if options == ["--no-mmap"] {
                    cmd.env("TG_SIDECAR_PYTHON", &sentinel)
                        .env("TG_TEST_SIDECAR_MARKER", &marker);
                }
                let output = bounded(cmd);
                assert_eq!(output.status.code(), Some(0));
                assert!(
                    !marker.exists(),
                    "raw route unexpectedly invoked the sidecar"
                );
                let records: Vec<Value> = std::str::from_utf8(&output.stdout)
                    .unwrap()
                    .lines()
                    .map(|line| serde_json::from_str(line).unwrap())
                    .collect();
                assert!(records.iter().any(|value| value["type"] == "match"));
                for value in records {
                    assert!(value["type"].is_string());
                    assert!(value.get("routing_backend").is_none());
                    assert!(value.get("ok").is_none());
                }
            }
        }
    }
}

#[test]
fn closed_pipe_preserves_house_single_file_parity_and_typed_error() {
    let error = anyhow::Error::new(std::io::Error::from(std::io::ErrorKind::BrokenPipe));
    assert!(tensor_grep_rs::broken_pipe::error_chain_has_broken_pipe(
        &error
    ));
    assert!(tensor_grep_rs::exit_codes::exit_with_run_error(error).is_err());
    let root = tempdir().unwrap();
    require_rg(root.path());
    let body = "needle repeated line\n".repeat(65536);
    fs::write(root.path().join("dense.txt"), &body).unwrap();
    let drained = run(root.path(), &["search", "needle", "dense.txt"]);
    assert_eq!(drained.status.code(), Some(0));
    assert!(
        drained.stdout.len() > 200_000,
        "fixture must exceed pipe buffers"
    );
    let mut rg = Command::new("rg");
    rg.current_dir(root.path())
        .env_remove("RIPGREP_CONFIG_PATH")
        .args(["needle", "dense.txt"]);
    let (rg_line, rg_output) = early_close(rg);
    let (tg_line, tg_output) =
        early_close(command(root.path(), &["search", "needle", "dense.txt"]));
    assert_eq!(tg_line, rg_line);
    assert_eq!(tg_output.status.code(), rg_output.status.code());
    assert_eq!(tg_output.stderr, rg_output.stderr);
    assert!(tg_output.stderr.is_empty(), "broken pipe must remain quiet");
}

fn binary_count(value: &Value) -> u64 {
    value
        .get("binary_match_files")
        .and_then(Value::as_u64)
        .unwrap_or(0)
}

#[test]
fn binary_detection_is_not_a_query_match_cold_explicit_and_warm() {
    let root = tempdir().unwrap();
    let text = tempdir().unwrap();
    require_rg(root.path());
    fs::write(root.path().join("matching.bin"), b"binaryonlymatch\0tail\n").unwrap();
    fs::write(root.path().join("nonmatching.bin"), b"unrelated\0tail\n").unwrap();
    fs::write(text.path().join("ordinary.txt"), "ordinarypositive\n").unwrap();
    for (step, flags) in [&[][..], &["--index"][..], &[][..]].into_iter().enumerate() {
        let matched = json_search(root.path(), flags, "binaryonlymatch");
        assert_eq!(
            matched["routing_backend"],
            if step == 0 {
                "NativeCpuBackend"
            } else {
                "TrigramIndex"
            },
            "step={step} flags={flags:?} payload={matched:?}"
        );
        assert_eq!(
            binary_count(&matched),
            1,
            "nonmatching binary was counted as a hit; step={step} flags={flags:?} payload={matched:?}"
        );
        assert_eq!(
            matched["total_matches"], 0,
            "step={step} flags={flags:?} payload={matched:?}"
        );
        assert!(
            matched["matches"].as_array().unwrap().is_empty(),
            "step={step} flags={flags:?} payload={matched:?}"
        );
        let mut args = vec!["search", "--json", "-F"];
        args.extend_from_slice(flags);
        args.extend_from_slice(&["genuinelyabsentneedle", "."]);
        let absent = run(root.path(), &args);
        // Preserve existing cold no-match exit1 versus indexed aggregate empty exit0.
        assert_eq!(absent.status.code(), Some(if step == 0 { 1 } else { 0 }));
        let empty = payload(&absent);
        assert_eq!(
            binary_count(&empty),
            0,
            "step={step} flags={flags:?} payload={empty:?}"
        );
        assert_eq!(
            empty["total_matches"], 0,
            "step={step} flags={flags:?} payload={empty:?}"
        );
        assert!(
            empty["matches"].as_array().unwrap().is_empty(),
            "step={step} flags={flags:?} payload={empty:?}"
        );
        let mut plain = vec!["search", "-F"];
        plain.extend_from_slice(flags);
        plain.extend_from_slice(&["genuinelyabsentneedle", "."]);
        let plain = run(root.path(), &plain);
        assert_eq!(plain.status.code(), Some(1));
        assert!(plain.stdout.is_empty());
        assert!(plain.stderr.is_empty());
        let ordinary = json_search(text.path(), flags, "ordinarypositive");
        assert_eq!(
            ordinary["total_matches"], 1,
            "step={step} flags={flags:?} payload={ordinary:?}"
        );
        assert_eq!(
            binary_count(&ordinary),
            0,
            "step={step} flags={flags:?} payload={ordinary:?}"
        );
    }
}

#[test]
fn oversized_binary_uses_bounded_probe_not_unbounded_tail_match() {
    use std::io::{Seek, SeekFrom};
    const CAP: u64 = 64 * 1024 * 1024;
    let root = tempdir().unwrap();
    let path = root.path().join("large.bin");
    let mut file = File::create(&path).unwrap();
    file.write_all(b"\0").unwrap();
    file.seek(SeekFrom::Start(CAP - 64)).unwrap();
    file.write_all(b"nearcapneedle\n").unwrap();
    file.seek(SeekFrom::Start(CAP + 64)).unwrap();
    file.write_all(b"beyondcapneedle\n").unwrap();
    file.flush().unwrap();
    assert!(fs::metadata(&path).unwrap().len() > CAP);
    let index = TrigramIndex::build(root.path()).unwrap();
    assert!(index
        .search("nearcapneedle", false, true)
        .unwrap()
        .is_empty());
    assert!(index
        .search("nearcapneedle$", false, false)
        .unwrap()
        .is_empty());
    for (step, flags) in [&[][..], &["--index"][..], &[][..]].into_iter().enumerate() {
        let hit = json_search(root.path(), flags, "nearcapneedle");
        assert_eq!(
            hit["routing_backend"],
            if step == 0 {
                "NativeCpuBackend"
            } else {
                "TrigramIndex"
            },
            "step={step} flags={flags:?} payload={hit:?}"
        );
        assert_eq!(
            binary_count(&hit),
            1,
            "step={step} flags={flags:?} payload={hit:?}"
        );
        assert_eq!(
            hit["total_matches"], 0,
            "step={step} flags={flags:?} payload={hit:?}"
        );
        assert!(
            hit["matches"].as_array().unwrap().is_empty(),
            "step={step} flags={flags:?} payload={hit:?}"
        );
        let mut args = vec!["search", "--json", "-F"];
        args.extend_from_slice(flags);
        args.extend_from_slice(&["beyondcapneedle", "."]);
        let absent = run(root.path(), &args);
        assert_eq!(absent.status.code(), Some(if step == 0 { 1 } else { 0 }));
        let value = payload(&absent);
        assert_eq!(
            binary_count(&value),
            0,
            "binary match probe crossed its 64MiB cap; step={step} flags={flags:?} payload={value:?}"
        );
        assert_eq!(
            value["total_matches"], 0,
            "step={step} flags={flags:?} payload={value:?}"
        );
        assert!(
            value["matches"].as_array().unwrap().is_empty(),
            "step={step} flags={flags:?} payload={value:?}"
        );
    }
}

fn isolated(root: &Path, args: &[&str], home: &Path) -> Command {
    let mut cmd = command(root, args);
    cmd.env("HOME", home)
        .env("USERPROFILE", home)
        .env("XDG_CONFIG_HOME", home.join("xdg"))
        .env("GIT_CONFIG_GLOBAL", home.join(".gitconfig"))
        .env("GIT_CONFIG_SYSTEM", home.join("empty-system-config"))
        .env("GIT_CONFIG_NOSYSTEM", "1");
    cmd
}

fn isolated_home(home: &Path) {
    fs::create_dir_all(home.join("xdg/git")).unwrap();
    fs::write(home.join(".gitconfig"), "").unwrap();
    fs::write(home.join("empty-system-config"), "").unwrap();
}

fn assert_ignore_override(root: &Path, home: &Path) {
    let positive = bounded(isolated(
        root,
        &["search", "--index", "--json", "-F", "visiblepositive", "."],
        home,
    ));
    assert!(positive.status.success());
    assert_eq!(payload(&positive)["total_matches"], 1);
    let ignored = bounded(isolated(
        root,
        &["search", "--index", "--json", "-F", "secretneedle", "."],
        home,
    ));
    assert!(ignored.status.success());
    assert_eq!(
        payload(&ignored)["total_matches"],
        0,
        "SETUP: owned ignore rule did not exclude its target"
    );
    let unrestricted = bounded(isolated(
        root,
        &[
            "search",
            "--index",
            "--no-ignore",
            "--json",
            "-F",
            "secretneedle",
            ".",
        ],
        home,
    ));
    assert!(unrestricted.status.success());
    let value = payload(&unrestricted);
    assert_eq!(value["routing_backend"], "TrigramIndex");
    assert_eq!(value["sidecar_used"], false);
    assert_eq!(value["total_matches"], 1);
}

#[test]
fn no_ignore_covers_owned_parent_nested_and_root_ignore_rules() {
    for kind in [
        "parent-gitignore",
        "parent-ignore",
        "nested-gitignore",
        "root-rgignore",
    ] {
        let fixture = tempdir().unwrap();
        let home = tempdir().unwrap();
        isolated_home(home.path());
        let root = fixture.path().join("subtree");
        fs::create_dir_all(root.join("nested")).unwrap();
        fs::create_dir(fixture.path().join(".git")).unwrap();
        fs::write(root.join("visible.txt"), "visiblepositive\n").unwrap();
        fs::write(root.join("nested/secret.txt"), "secretneedle\n").unwrap();
        let rule = match kind {
            "parent-gitignore" => fixture.path().join(".gitignore"),
            "parent-ignore" => fixture.path().join(".ignore"),
            "nested-gitignore" => root.join("nested/.gitignore"),
            _ => root.join(".rgignore"),
        };
        fs::write(rule, "secret.txt\n").unwrap();
        assert_ignore_override(&root, home.path());
    }
}

#[test]
fn no_ignore_covers_owned_git_info_exclude() {
    let fixture = tempdir().unwrap();
    let home = tempdir().unwrap();
    isolated_home(home.path());
    fs::create_dir_all(fixture.path().join(".git/info")).unwrap();
    fs::write(fixture.path().join(".git/info/exclude"), "secret.txt\n").unwrap();
    fs::write(fixture.path().join("visible.txt"), "visiblepositive\n").unwrap();
    fs::write(fixture.path().join("secret.txt"), "secretneedle\n").unwrap();
    assert_ignore_override(fixture.path(), home.path());
}

#[test]
fn no_ignore_covers_owned_global_git_excludes_without_user_config() {
    let fixture = tempdir().unwrap();
    // The ignore dependency expands any tilde in excludesFile, including Windows short paths.
    // Keep this owned HOME beneath the manifest directory to exercise the ignore override.
    let home = tempfile::Builder::new()
        .tempdir_in(env!("CARGO_MANIFEST_DIR"))
        .unwrap();
    isolated_home(home.path());
    fs::create_dir(fixture.path().join(".git")).unwrap();
    let excludes = home.path().join("owned-ignore");
    fs::write(&excludes, "secret.txt\n").unwrap();
    let filename = excludes.to_str().unwrap().replace('\\', "/");
    fs::write(
        home.path().join(".gitconfig"),
        format!("[core]\nexcludesFile = \"{filename}\"\n"),
    )
    .unwrap();
    fs::write(fixture.path().join("visible.txt"), "visiblepositive\n").unwrap();
    fs::write(fixture.path().join("secret.txt"), "secretneedle\n").unwrap();
    assert_ignore_override(fixture.path(), home.path());
}

#[test]
fn plain_pcre_positive_discriminates_owned_bad_executable_refusal() {
    let root = tempdir().unwrap();
    require_rg(root.path());
    let mut capability = Command::new("rg");
    capability.current_dir(root.path()).arg("--pcre2-version");
    assert!(
        bounded(capability).status.success(),
        "BLOCKED_SETUP: PCRE2-capable rg required"
    );
    fs::write(root.path().join("a.txt"), "needle\n").unwrap();
    let bad = root.path().join("not-executable.txt");
    fs::write(&bad, "ordinary owned text, never executable\n").unwrap();
    for prefix in [&["search"][..], &[][..]] {
        let mut args = prefix.to_vec();
        args.extend_from_slice(&["-P", "(?<=n)eedle", "a.txt"]);
        let positive = run(root.path(), &args);
        assert_eq!(positive.status.code(), Some(0));
        assert!(String::from_utf8_lossy(&positive.stdout).contains("needle"));
        let mut cmd = command(root.path(), &args);
        cmd.env("TG_RG_PATH", &bad);
        let negative = bounded(cmd);
        assert_eq!(
            negative.status.code(),
            Some(2),
            "stdout={}; stderr={}",
            String::from_utf8_lossy(&negative.stdout),
            String::from_utf8_lossy(&negative.stderr)
        );
        assert!(negative.stdout.is_empty());
        assert!(String::from_utf8_lossy(&negative.stderr)
            .to_ascii_lowercase()
            .contains("failed to execute ripgrep"));
    }
}

#[test]
fn structured_ndjson_closed_consumer_remains_quiet() {
    let root = tempdir().unwrap();
    fs::write(
        root.path().join("dense.txt"),
        "needle repeated line\n".repeat(65536),
    )
    .unwrap();
    let args = ["search", "--ndjson", "-F", "needle", "dense.txt"];
    let positive = run(root.path(), &args);
    assert_eq!(positive.status.code(), Some(0));
    assert!(
        positive.stdout.len() > 200_000,
        "fixture must exceed pipe buffers"
    );
    let first = std::str::from_utf8(&positive.stdout)
        .unwrap()
        .lines()
        .next()
        .unwrap();
    let first = serde_json::from_str::<Value>(first).unwrap();
    assert_eq!(first["line"], 1);
    assert_eq!(first["text"], "needle repeated line");
    assert_eq!(first["routing_backend"], "NativeCpuBackend");
    assert_eq!(first["sidecar_used"], false);
    let (first, closed) = early_close(command(root.path(), &args));
    let first = serde_json::from_slice::<Value>(&first).unwrap();
    assert_eq!(first["line"], 1);
    assert_eq!(first["text"], "needle repeated line");
    assert_eq!(first["routing_backend"], "NativeCpuBackend");
    assert_eq!(first["sidecar_used"], false);
    assert_eq!(closed.status.code(), Some(1));
    assert!(
        closed.stderr.is_empty(),
        "broken pipe must not emit a structured backend error or retry"
    );
}

#[test]
fn late_binary_detection_matches_cold_public_index_and_warm_routes() {
    for (name, earlier, suffix, expected) in [
        ("late", false, &b"needle\0payload\n"[..], None),
        ("earlier", true, &b"needle\0payload\n"[..], None),
        (
            "plain",
            false,
            &b"needle plaincontrol\n"[..],
            Some("needle plaincontrol"),
        ),
        (
            "other",
            false,
            &b"needle normal\nother\0payload\n"[..],
            Some("needle normal"),
        ),
    ] {
        let root = tempdir().unwrap();
        let path = root.path().join("sample.txt");
        let mut bytes = if earlier {
            b"needle earlier\n".to_vec()
        } else {
            Vec::new()
        };
        bytes.extend(("x".repeat(1023) + "\n").repeat(80).bytes());
        bytes.extend_from_slice(suffix);
        assert!(
            !bytes[..65536].contains(&0),
            "{name}: guaranteed prefix must be clean"
        );
        fs::write(&path, bytes).unwrap();
        let cold = json_search(root.path(), &[], "needle");
        assert_eq!(cold["routing_backend"], "NativeCpuBackend", "{name}");
        let expected = if name == "other" && cold["binary_match_files"] == 1 {
            None
        } else {
            expected
        };
        let index = TrigramIndex::build(root.path()).unwrap();
        // Every literal is shorter than a trigram, so the regex arm must full-scan.
        for (pattern, fixed) in [("needle", true), ("n.e.le", false)] {
            let rows: Vec<IndexQueryResult> = index.search(pattern, false, fixed).unwrap();
            assert_eq!(
                rows.len(),
                usize::from(expected.is_some()),
                "{name}: {pattern}"
            );
            if let Some(text) = expected {
                assert_eq!(rows[0].file.file_name().unwrap(), "sample.txt");
                assert_eq!(rows[0].line, 81);
                assert_eq!(rows[0].text, text);
            }
        }
        for (step, value) in [
            cold,
            json_search(root.path(), &["--index"], "needle"),
            json_search(root.path(), &[], "needle"),
        ]
        .into_iter()
        .enumerate()
        {
            assert_eq!(
                value["routing_backend"],
                if step == 0 {
                    "NativeCpuBackend"
                } else {
                    "TrigramIndex"
                },
                "{name}: step={step}"
            );
            assert_eq!(
                value["total_matches"],
                usize::from(expected.is_some()),
                "{name}: step={step}"
            );
            assert_eq!(
                value["matches"].as_array().unwrap().len(),
                usize::from(expected.is_some())
            );
            if let Some(text) = expected {
                assert_eq!(value["matches"][0]["line"], 81);
                assert_eq!(value["matches"][0]["text"], text);
                assert!(value.get("binary_match_files").is_none());
            } else {
                assert_eq!(value["binary_match_files"], 1);
            }
        }
    }
}

use std::fs;
use std::path::{Path, PathBuf};
use std::process::{Command, Output};
use std::thread;
use std::time::Duration;

use serde_json::Value;
use tempfile::{tempdir, TempDir};

const RG_SENTINEL: &str = "TG_RG_ROUTING_SENTINEL";

fn tg() -> Command {
    Command::new(env!("CARGO_BIN_EXE_tg"))
}

fn repo_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap()
        .to_path_buf()
}

fn repo_python() -> PathBuf {
    let windows = repo_root().join(".venv").join("Scripts").join("python.exe");
    if windows.exists() {
        return windows;
    }

    repo_root().join(".venv").join("bin").join("python")
}

fn write_text_corpus(dir: &Path) {
    fs::write(
        dir.join("a.txt"),
        "hello world\nfoo bar baz\ngoodbye world\n",
    )
    .unwrap();
    fs::write(
        dir.join("b.txt"),
        "nothing here\nhello again friend\nend\n",
    )
    .unwrap();
    fs::write(dir.join("notes.md"), "hello from markdown\n").unwrap();
}

fn write_python_source() -> (TempDir, PathBuf) {
    let dir = tempdir().unwrap();
    let file_path = dir.path().join("fixture.py");
    fs::write(&file_path, "def add(a, b):\n    return a + b\n").unwrap();
    (dir, file_path)
}

fn build_index(dir: &Path) {
    let output = tg()
        .arg("search")
        .arg("--index")
        .arg("--fixed-strings")
        .arg("--count")
        .arg("hello")
        .arg(dir)
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "status={:?}\nstdout={}\nstderr={}",
        output.status.code(),
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
}

fn write_rg_wrapper(dir: &Path) -> PathBuf {
    if cfg!(windows) {
        let script = dir.join("rg-wrapper.cmd");
        fs::write(&script, format!("@echo off\r\necho {RG_SENTINEL}\r\n")).unwrap();
        script
    } else {
        let script = dir.join("rg-wrapper.sh");
        fs::write(&script, format!("#!/bin/sh\nprintf '{RG_SENTINEL}\\n'\n")).unwrap();
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;

            let mut permissions = fs::metadata(&script).unwrap().permissions();
            permissions.set_mode(0o755);
            fs::set_permissions(&script, permissions).unwrap();
        }
        script
    }
}

fn write_mock_gpu_sidecar_script(dir: &Path, matched_file: &Path, marker: &Path) -> PathBuf {
    let script = dir.join("mock_gpu_sidecar.py");
    fs::write(
        &script,
        format!(
            "import json\nimport os\nimport pathlib\nimport sys\nrequest = json.loads(sys.stdin.buffer.read())\npathlib.Path(r\"{}\").write_text('invoked', encoding='utf-8')\nresponse = {{\"stdout\": json.dumps({{\"total_matches\": 1, \"total_files\": 1, \"matches\": [{{\"file\": {:?}, \"line_number\": 1, \"text\": \"hello world\"}}]}}) + '\\n', \"stderr\": \"\", \"exit_code\": 0, \"pid\": os.getpid()}}\nsys.stdout.write(json.dumps(response))\n",
            marker.display(),
            matched_file.display().to_string(),
        ),
    )
    .unwrap();
    script
}

fn assert_verbose_routing(stderr: &str, backend: &str, reason: &str, sidecar_used: bool) {
    assert!(stderr.contains(&format!("routing_backend={backend}")), "stderr={stderr}");
    assert!(stderr.contains(&format!("routing_reason={reason}")), "stderr={stderr}");
    assert!(
        stderr.contains(&format!("sidecar_used={sidecar_used}")),
        "stderr={stderr}"
    );
}

fn assert_rg_passthrough(output: &Output) {
    assert!(
        output.status.success(),
        "status={:?}\nstdout={}\nstderr={}",
        output.status.code(),
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );

    let stdout = String::from_utf8_lossy(&output.stdout);
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert_verbose_routing(&stderr, "RipgrepBackend", "rg_passthrough", false);
    assert_eq!(stdout.trim(), RG_SENTINEL, "stdout={stdout}");
}

fn assert_json_routing(output: &Output, backend: &str, reason: &str, sidecar_used: bool) -> Value {
    assert!(
        output.status.success(),
        "status={:?}\nstdout={}\nstderr={}",
        output.status.code(),
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );

    let payload: Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(payload["routing_backend"], backend);
    assert_eq!(payload["routing_reason"], reason);
    assert_eq!(payload["sidecar_used"], sidecar_used);
    payload
}

fn assert_ndjson_routing(
    output: &Output,
    backend: &str,
    reason: &str,
    sidecar_used: bool,
) -> Vec<Value> {
    assert!(
        output.status.success(),
        "status={:?}\nstdout={}\nstderr={}",
        output.status.code(),
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );

    let stdout = String::from_utf8_lossy(&output.stdout);
    let payloads = stdout
        .lines()
        .filter(|line| !line.trim().is_empty())
        .map(|line| serde_json::from_str::<Value>(line).unwrap())
        .collect::<Vec<_>>();

    assert!(!payloads.is_empty(), "stdout={stdout}");

    for payload in &payloads {
        assert_eq!(payload["routing_backend"], backend);
        assert_eq!(payload["routing_reason"], reason);
        assert_eq!(payload["sidecar_used"], sidecar_used);
    }

    payloads
}

#[test]
fn test_routing_default_search_uses_ripgrep_passthrough() {
    let dir = tempdir().unwrap();
    write_text_corpus(dir.path());
    let rg_wrapper = write_rg_wrapper(dir.path());

    let output = tg()
        .arg("search")
        .arg("--verbose")
        .arg("hello")
        .arg(dir.path())
        .env("TG_RG_PATH", &rg_wrapper)
        .output()
        .unwrap();

    assert_rg_passthrough(&output);
}

#[test]
fn test_search_ndjson_emits_one_parseable_json_object_per_match() {
    let dir = tempdir().unwrap();
    write_text_corpus(dir.path());

    let output = tg()
        .arg("search")
        .arg("--fixed-strings")
        .arg("--ndjson")
        .arg("hello")
        .arg(dir.path())
        .output()
        .unwrap();

    let payloads = assert_ndjson_routing(&output, "NativeCpuBackend", "json_output", false);
    assert_eq!(payloads.len(), 3);

    let mut actual = payloads
        .iter()
        .map(|payload| {
            let object = payload.as_object().unwrap();
            assert!(object.contains_key("query"));
            assert!(object.contains_key("path"));
            assert!(object.contains_key("file"));
            assert!(object.contains_key("line"));
            assert!(object.contains_key("text"));
            assert!(!object.contains_key("matches"));
            assert!(!object.contains_key("total_matches"));
            (
                payload["file"].as_str().unwrap().to_owned(),
                payload["line"].as_u64().unwrap(),
                payload["text"].as_str().unwrap().to_owned(),
            )
        })
        .collect::<Vec<_>>();
    actual.sort();

    let mut expected = vec![
        (
            dir.path().join("a.txt").display().to_string(),
            1,
            "hello world".to_string(),
        ),
        (
            dir.path().join("b.txt").display().to_string(),
            2,
            "hello again friend".to_string(),
        ),
        (
            dir.path().join("notes.md").display().to_string(),
            1,
            "hello from markdown".to_string(),
        ),
    ];
    expected.sort();

    assert_eq!(actual, expected);

}

#[test]
fn test_search_ndjson_keeps_stdout_json_when_binary_warning_is_emitted() {
    let dir = tempdir().unwrap();
    let text_path = dir.path().join("text.log");
    let binary_path = dir.path().join("binary.bin");
    fs::write(&text_path, "ERROR visible\n").unwrap();
    fs::write(&binary_path, b"\0ERROR hidden\0").unwrap();

    let output = tg()
        .arg("search")
        .arg("--cpu")
        .arg("--fixed-strings")
        .arg("--ndjson")
        .arg("ERROR")
        .arg(dir.path())
        .output()
        .unwrap();

    let payloads = assert_ndjson_routing(&output, "NativeCpuBackend", "force_cpu", false);
    assert_eq!(payloads.len(), 1, "stdout={}", String::from_utf8_lossy(&output.stdout));
    assert_eq!(payloads[0]["file"], text_path.display().to_string());

    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(
        stderr.contains(&format!("Binary file {} matches", binary_path.display())),
        "stderr={stderr}"
    );
}

#[test]
fn test_search_single_binary_file_emits_stderr_warning_and_exit_zero() {
    let dir = tempdir().unwrap();
    let binary_path = dir.path().join("binary.bin");
    fs::write(&binary_path, b"\0ERROR hidden\0").unwrap();

    let output = tg()
        .arg("search")
        .arg("--cpu")
        .arg("--fixed-strings")
        .arg("ERROR")
        .arg(&binary_path)
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "status={:?}\nstdout={}\nstderr={}",
        output.status.code(),
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(output.stdout.is_empty(), "stdout={}", String::from_utf8_lossy(&output.stdout));

    let stderr = String::from_utf8_lossy(&output.stderr);
    assert_eq!(stderr.trim(), format!("Binary file {} matches", binary_path.display()));
}

#[test]
fn test_routing_force_cpu_routes_to_native_search_even_when_rg_is_available() {
    let dir = tempdir().unwrap();
    write_text_corpus(dir.path());
    let rg_wrapper = write_rg_wrapper(dir.path());

    let output = tg()
        .arg("search")
        .arg("--cpu")
        .arg("--fixed-strings")
        .arg("--json")
        .arg("hello")
        .arg(dir.path())
        .env("TG_RG_PATH", &rg_wrapper)
        .output()
        .unwrap();

    let payload = assert_json_routing(&output, "NativeCpuBackend", "force_cpu", false);
    assert_eq!(payload["total_matches"], 3);
    assert_ne!(String::from_utf8_lossy(&output.stdout).trim(), RG_SENTINEL);
}

#[test]
fn test_routing_force_cpu_alias_is_accepted() {
    let dir = tempdir().unwrap();
    write_text_corpus(dir.path());
    let rg_wrapper = write_rg_wrapper(dir.path());

    let output = tg()
        .arg("search")
        .arg("--force-cpu")
        .arg("--fixed-strings")
        .arg("--json")
        .arg("hello")
        .arg(dir.path())
        .env("TG_RG_PATH", &rg_wrapper)
        .output()
        .unwrap();

    let payload = assert_json_routing(&output, "NativeCpuBackend", "force_cpu", false);
    assert_eq!(payload["total_matches"], 3);
}

#[test]
fn test_routing_falls_back_to_native_when_ripgrep_is_unavailable() {
    let dir = tempdir().unwrap();
    write_text_corpus(dir.path());

    let output = tg()
        .arg("search")
        .arg("--fixed-strings")
        .arg("--verbose")
        .arg("hello")
        .arg(dir.path())
        .env("PATH", "")
        .env("TG_DISABLE_RG", "1")
        .output()
        .unwrap();

    assert!(output.status.success(), "status={:?}\nstdout={}\nstderr={}", output.status.code(), String::from_utf8_lossy(&output.stdout), String::from_utf8_lossy(&output.stderr));

    let stdout = String::from_utf8_lossy(&output.stdout);
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert_verbose_routing(&stderr, "NativeCpuBackend", "rg_unavailable", false);
    assert!(stdout.contains("hello world"), "stdout={stdout}");
    assert!(stdout.contains("hello again friend"), "stdout={stdout}");
    assert!(!stdout.contains(RG_SENTINEL), "stdout={stdout}");
}

#[test]
fn test_search_json_and_ndjson_are_mutually_exclusive() {
    let dir = tempdir().unwrap();
    write_text_corpus(dir.path());

    let output = tg()
        .arg("search")
        .arg("--json")
        .arg("--ndjson")
        .arg("hello")
        .arg(dir.path())
        .output()
        .unwrap();

    assert!(!output.status.success(), "stdout={}", String::from_utf8_lossy(&output.stdout));

    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(stderr.contains("--json"), "stderr={stderr}");
    assert!(stderr.contains("--ndjson"), "stderr={stderr}");
}

#[test]
fn test_routing_explicit_index_uses_trigram_index_json() {
    let dir = tempdir().unwrap();
    write_text_corpus(dir.path());

    let output = tg()
        .arg("search")
        .arg("--index")
        .arg("--fixed-strings")
        .arg("--json")
        .arg("hello")
        .arg(dir.path())
        .output()
        .unwrap();

    let payload = assert_json_routing(&output, "TrigramIndex", "index-accelerated", false);
    assert_eq!(payload["total_matches"], 3);
}

#[test]
fn test_routing_json_prefers_native_engine_even_when_warm_index_is_available() {
    let dir = tempdir().unwrap();
    write_text_corpus(dir.path());
    build_index(dir.path());

    let output = tg()
        .arg("search")
        .arg("--fixed-strings")
        .arg("--json")
        .arg("hello")
        .arg(dir.path())
        .output()
        .unwrap();

    let payload = assert_json_routing(&output, "NativeCpuBackend", "json_output", false);
    assert_eq!(payload["total_matches"], 3);
}

#[test]
fn test_routing_warm_index_is_bypassed_by_invert_match() {
    let dir = tempdir().unwrap();
    write_text_corpus(dir.path());
    build_index(dir.path());
    let rg_wrapper = write_rg_wrapper(dir.path());

    let output = tg()
        .arg("search")
        .arg("--fixed-strings")
        .arg("-v")
        .arg("--verbose")
        .arg("hello")
        .arg(dir.path())
        .env("TG_RG_PATH", &rg_wrapper)
        .output()
        .unwrap();

    assert_rg_passthrough(&output);
}

#[test]
fn test_routing_warm_index_is_bypassed_by_context_lines() {
    let dir = tempdir().unwrap();
    write_text_corpus(dir.path());
    build_index(dir.path());
    let rg_wrapper = write_rg_wrapper(dir.path());

    let output = tg()
        .arg("search")
        .arg("--fixed-strings")
        .arg("-C")
        .arg("1")
        .arg("--verbose")
        .arg("hello")
        .arg(dir.path())
        .env("TG_RG_PATH", &rg_wrapper)
        .output()
        .unwrap();

    assert_rg_passthrough(&output);
}

#[test]
fn test_routing_explicit_gpu_device_ids_use_gpu_sidecar() {
    let dir = tempdir().unwrap();
    write_text_corpus(dir.path());
    let marker = dir.path().join("gpu-sidecar-marker.txt");
    let matched_file = dir.path().join("a.txt");
    let sidecar_script = write_mock_gpu_sidecar_script(dir.path(), &matched_file, &marker);

    let output = tg()
        .current_dir(repo_root())
        .arg("search")
        .arg("--gpu-device-ids")
        .arg("0")
        .arg("--json")
        .arg("hello")
        .arg(dir.path())
        .env("TG_SIDECAR_PYTHON", repo_python())
        .env("TG_SIDECAR_SCRIPT", &sidecar_script)
        .output()
        .unwrap();

    if cfg!(feature = "cuda") {
        let payload = assert_json_routing(&output, "gpu_native", "gpu-device-ids-explicit-native", false);
        assert_eq!(payload["total_matches"], 4);
        assert!(!marker.exists(), "native GPU routing should not invoke the Python sidecar");
    } else {
        let payload = assert_json_routing(&output, "GpuSidecar", "gpu-device-ids-explicit", true);
        assert_eq!(payload["total_matches"], 1);
        assert!(marker.exists(), "expected mock GPU sidecar invocation");
    }
}

#[test]
fn test_routing_tg_run_uses_ast_backend() {
    let (_dir, file_path) = write_python_source();

    let output = tg()
        .arg("run")
        .arg("--lang")
        .arg("python")
        .arg("--json")
        .arg("def $F($$$ARGS): $$$BODY")
        .arg(&file_path)
        .output()
        .unwrap();

    let payload = assert_json_routing(&output, "AstBackend", "ast-native", false);
    assert_eq!(payload["total_matches"], 1);
}

#[test]
fn test_tg_run_rewrite_rejects_ndjson_without_python() {
    let bogus_python_home = tempdir().unwrap();
    let (_dir, file_path) = write_python_source();

    let output = tg()
        .arg("run")
        .arg("--lang")
        .arg("python")
        .arg("--rewrite")
        .arg("lambda $$$ARGS: $EXPR")
        .arg("--ndjson")
        .arg("def $F($$$ARGS): return $EXPR")
        .arg(&file_path)
        .env("PYTHONHOME", bogus_python_home.path())
        .output()
        .unwrap();

    assert!(!output.status.success(), "stdout={}", String::from_utf8_lossy(&output.stdout));

    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(stderr.contains("--ndjson"), "stderr={stderr}");
    assert!(
        stderr.contains("unexpected") || stderr.contains("unknown") || stderr.contains("found argument"),
        "stderr={stderr}"
    );
}

#[test]
fn test_routing_warm_index_is_bypassed_by_short_pattern() {
    let dir = tempdir().unwrap();
    write_text_corpus(dir.path());
    build_index(dir.path());
    let rg_wrapper = write_rg_wrapper(dir.path());

    let output = tg()
        .arg("search")
        .arg("--fixed-strings")
        .arg("--verbose")
        .arg("he")
        .arg(dir.path())
        .env("TG_RG_PATH", &rg_wrapper)
        .output()
        .unwrap();

    assert_rg_passthrough(&output);
}

#[test]
fn test_routing_warm_index_is_bypassed_by_word_regexp() {
    let dir = tempdir().unwrap();
    write_text_corpus(dir.path());
    build_index(dir.path());
    let rg_wrapper = write_rg_wrapper(dir.path());

    let output = tg()
        .arg("search")
        .arg("--fixed-strings")
        .arg("-w")
        .arg("--verbose")
        .arg("hello")
        .arg(dir.path())
        .env("TG_RG_PATH", &rg_wrapper)
        .output()
        .unwrap();

    assert_rg_passthrough(&output);
}

#[test]
fn test_routing_warm_index_is_bypassed_by_glob_filter() {
    let dir = tempdir().unwrap();
    write_text_corpus(dir.path());
    build_index(dir.path());
    let rg_wrapper = write_rg_wrapper(dir.path());

    let output = tg()
        .arg("search")
        .arg("--fixed-strings")
        .arg("-g")
        .arg("*.txt")
        .arg("--verbose")
        .arg("hello")
        .arg(dir.path())
        .env("TG_RG_PATH", &rg_wrapper)
        .output()
        .unwrap();

    assert_rg_passthrough(&output);
}

#[test]
fn test_routing_warm_index_is_bypassed_by_max_count() {
    let dir = tempdir().unwrap();
    write_text_corpus(dir.path());
    build_index(dir.path());
    let rg_wrapper = write_rg_wrapper(dir.path());

    let output = tg()
        .arg("search")
        .arg("--fixed-strings")
        .arg("--max-count")
        .arg("1")
        .arg("--verbose")
        .arg("hello")
        .arg(dir.path())
        .env("TG_RG_PATH", &rg_wrapper)
        .output()
        .unwrap();

    assert_rg_passthrough(&output);
}

#[test]
fn test_routing_stale_index_with_explicit_index_rebuilds() {
    let dir = tempdir().unwrap();
    write_text_corpus(dir.path());
    build_index(dir.path());

    let index_path = dir.path().join(".tg_index");
    let before = index_path.metadata().unwrap().modified().unwrap();

    thread::sleep(Duration::from_millis(50));
    fs::write(dir.path().join("fresh.txt"), "hello from rebuilt index\n").unwrap();

    let output = tg()
        .arg("search")
        .arg("--index")
        .arg("--fixed-strings")
        .arg("--json")
        .arg("--verbose")
        .arg("hello")
        .arg(dir.path())
        .output()
        .unwrap();

    let payload = assert_json_routing(&output, "TrigramIndex", "index-accelerated", false);
    assert_eq!(payload["total_matches"], 4);

    let after = index_path.metadata().unwrap().modified().unwrap();
    assert!(after > before, "expected stale index rebuild to update mtime");

    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(stderr.contains("stale") || stderr.contains("rebuilding"), "stderr={stderr}");
}

#[test]
fn test_routing_explicit_index_rebuilds_corrupt_index() {
    let dir = tempdir().unwrap();
    write_text_corpus(dir.path());
    fs::write(dir.path().join(".tg_index"), b"corrupt-index").unwrap();

    let output = tg()
        .arg("search")
        .arg("--index")
        .arg("--fixed-strings")
        .arg("--json")
        .arg("--verbose")
        .arg("hello")
        .arg(dir.path())
        .output()
        .unwrap();

    let payload = assert_json_routing(&output, "TrigramIndex", "index-accelerated", false);
    assert_eq!(payload["total_matches"], 3);

    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(
        stderr.contains("failed to load index") || stderr.contains("rebuilding"),
        "stderr={stderr}"
    );
}

#[test]
fn test_routing_explicit_gpu_device_ids_override_warm_index() {
    let dir = tempdir().unwrap();
    let corpus_dir = dir.path().join("corpus");
    fs::create_dir(&corpus_dir).unwrap();
    write_text_corpus(&corpus_dir);
    build_index(&corpus_dir);

    let marker = dir.path().join("gpu-sidecar-priority-marker.txt");
    let matched_file = corpus_dir.join("a.txt");
    let sidecar_script = write_mock_gpu_sidecar_script(dir.path(), &matched_file, &marker);

    let output = tg()
        .current_dir(repo_root())
        .arg("search")
        .arg("--fixed-strings")
        .arg("--gpu-device-ids")
        .arg("0")
        .arg("--json")
        .arg("hello")
        .arg(&corpus_dir)
        .env("TG_SIDECAR_PYTHON", repo_python())
        .env("TG_SIDECAR_SCRIPT", &sidecar_script)
        .output()
        .unwrap();

    if cfg!(feature = "cuda") {
        let payload = assert_json_routing(&output, "gpu_native", "gpu-device-ids-explicit-native", false);
        assert_eq!(payload["total_matches"], 3);
        assert!(!marker.exists(), "native GPU routing should bypass the Python sidecar");
    } else {
        let payload = assert_json_routing(&output, "GpuSidecar", "gpu-device-ids-explicit", true);
        assert_eq!(payload["total_matches"], 1);
        assert!(marker.exists(), "expected mock GPU sidecar invocation");
    }
}

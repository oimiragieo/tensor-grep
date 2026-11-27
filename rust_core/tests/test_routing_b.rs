#![allow(unused_imports, dead_code)]
use routing_support::{combined_output, repo_python, repo_root, tg};
use serde_json::Value;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::{Command, Output};
use std::thread;
use std::time::Duration;
use std::time::{SystemTime, UNIX_EPOCH};
use tempfile::{tempdir, TempDir};
#[path = "common/routing_helpers.rs"]
mod common;
use common::*;

#[test]
fn test_pcre2_version_without_ripgrep_exits_2_not_1() {
    let output = tg()
        .arg("--pcre2-version")
        .env("PATH", "")
        .env("TG_DISABLE_RG", "1")
        .output()
        .unwrap();

    assert_eq!(
        output.status.code(),
        Some(2),
        "status={:?}\nstdout={}\nstderr={}",
        output.status.code(),
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
}

#[test]
fn test_search_pcre2_version_without_ripgrep_exits_2_not_1() {
    let output = tg()
        .arg("search")
        .arg("--pcre2-version")
        .env("PATH", "")
        .env("TG_DISABLE_RG", "1")
        .output()
        .unwrap();

    assert_eq!(
        output.status.code(),
        Some(2),
        "status={:?}\nstdout={}\nstderr={}",
        output.status.code(),
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
}

#[test]
fn test_search_type_list_without_ripgrep_exits_2_not_1() {
    let output = tg()
        .arg("search")
        .arg("--type-list")
        .env("PATH", "")
        .env("TG_DISABLE_RG", "1")
        .output()
        .unwrap();

    assert_eq!(
        output.status.code(),
        Some(2),
        "status={:?}\nstdout={}\nstderr={}",
        output.status.code(),
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
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

    assert!(
        !output.status.success(),
        "stdout={}",
        String::from_utf8_lossy(&output.stdout)
    );

    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(stderr.contains("--json"), "stderr={stderr}");
    assert!(stderr.contains("--ndjson"), "stderr={stderr}");
}

#[test]
fn test_search_json_reports_empty_pattern_error() {
    let dir = tempdir().unwrap();
    write_text_corpus(dir.path());

    let output = tg()
        .arg("search")
        .arg("--json")
        .arg("")
        .arg(dir.path())
        .output()
        .unwrap();

    assert!(
        !output.status.success(),
        "output={}",
        combined_output(&output)
    );
    assert_eq!(output.status.code(), Some(2));
    let payload: Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(payload["ok"], false);
    assert_eq!(payload["error"], "empty_pattern");
    assert!(payload["detail"]
        .as_str()
        .unwrap()
        .contains("PATTERN must not be empty"));
}

#[test]
fn test_root_json_reports_empty_pattern_error() {
    let dir = tempdir().unwrap();
    write_text_corpus(dir.path());

    let output = tg().arg("--json").arg("").arg(dir.path()).output().unwrap();

    assert!(
        !output.status.success(),
        "output={}",
        combined_output(&output)
    );
    assert_eq!(output.status.code(), Some(2));
    let payload: Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(payload["ok"], false);
    assert_eq!(payload["error"], "empty_pattern");
}

#[test]
fn test_search_json_reports_missing_path_error() {
    let dir = tempdir().unwrap();
    let missing = dir.path().join("missing.txt");

    let output = tg()
        .arg("search")
        .arg("--json")
        .arg("hello")
        .arg(&missing)
        .output()
        .unwrap();

    assert!(
        !output.status.success(),
        "output={}",
        combined_output(&output)
    );
    assert_eq!(output.status.code(), Some(2));
    let payload: Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(payload["ok"], false);
    assert_eq!(payload["error"], "path_not_found");
    assert!(payload["detail"]
        .as_str()
        .unwrap()
        .contains(&missing.to_string_lossy().to_string()));
}

#[test]
fn test_search_json_reports_invalid_regex_error() {
    let dir = tempdir().unwrap();
    write_text_corpus(dir.path());

    let output = tg()
        .arg("search")
        .arg("--json")
        .arg("(")
        .arg(dir.path())
        .output()
        .unwrap();

    assert!(
        !output.status.success(),
        "output={}",
        combined_output(&output)
    );
    assert_eq!(output.status.code(), Some(2));
    let payload: Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(payload["ok"], false);
    assert_eq!(payload["error"], "invalid_regex");
    assert!(payload["detail"]
        .as_str()
        .unwrap()
        .to_lowercase()
        .contains("invalid regex"));
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
fn test_routing_json_prefers_warm_index_even_when_json_is_requested() {
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

    let payload = assert_json_routing(&output, "TrigramIndex", "index-accelerated", false);
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

    assert!(
        output.status.success(),
        "stderr={}",
        String::from_utf8_lossy(&output.stderr)
    );
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert_verbose_routing(&stderr, "RipgrepBackend", "rg_passthrough", false);
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

    assert!(
        output.status.success(),
        "stderr={}",
        String::from_utf8_lossy(&output.stderr)
    );
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert_verbose_routing(&stderr, "RipgrepBackend", "rg_passthrough", false);
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
        let payload = assert_json_routing(
            &output,
            "NativeGpuBackend",
            "gpu-device-ids-explicit-native",
            false,
        );
        assert_eq!(payload["total_matches"], 4);
        assert!(
            !marker.exists(),
            "native GPU routing should not invoke the Python sidecar"
        );
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

    assert!(
        !output.status.success(),
        "stdout={}",
        String::from_utf8_lossy(&output.stdout)
    );

    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(stderr.contains("--ndjson"), "stderr={stderr}");
    assert!(
        stderr.contains("unexpected")
            || stderr.contains("unknown")
            || stderr.contains("found argument"),
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
        .arg(dir.path().join("a.txt"))
        .env("TG_RG_PATH", &rg_wrapper)
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "stderr={}",
        String::from_utf8_lossy(&output.stderr)
    );
    let stderr = String::from_utf8_lossy(&output.stderr);
    // THE INVARIANT THIS TEST NAMES is that a warm index is BYPASSED for a short pattern -- not
    // that the bypass lands on ripgrep specifically. The native plain-text route bypasses the
    // index just as completely (route_search checks the index BEFORE the plain-text clause, so
    // reaching this reason at all proves the index was not selected). The sibling
    // `_by_invert_match` / `_by_context_lines` / `_by_word_regexp` / `_by_glob_filter` /
    // `_by_max_count` tests all target a DIRECTORY or an excluded flag, so they still assert
    // ripgrep and keep that arm of the bypass guarantee pinned.
    assert_verbose_routing(&stderr, "NativeCpuBackend", "plain-text-native", false);
    assert!(
        !stderr.contains("routing_backend=TrigramIndex"),
        "the warm index must be bypassed entirely; stderr={stderr}"
    );
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

    assert!(
        output.status.success(),
        "stderr={}",
        String::from_utf8_lossy(&output.stderr)
    );
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert_verbose_routing(&stderr, "RipgrepBackend", "rg_passthrough", false);
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

    assert!(
        output.status.success(),
        "stderr={}",
        String::from_utf8_lossy(&output.stderr)
    );
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert_verbose_routing(&stderr, "RipgrepBackend", "rg_passthrough", false);
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

    assert!(
        output.status.success(),
        "stderr={}",
        String::from_utf8_lossy(&output.stderr)
    );
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert_verbose_routing(&stderr, "RipgrepBackend", "rg_passthrough", false);
}

#[test]
fn test_routing_stale_index_with_explicit_index_rebuilds() {
    let dir = tempdir().unwrap();
    write_text_corpus(dir.path());
    build_index(dir.path());

    let index_path =
        tensor_grep_rs::index_cache::resolve_index_paths(dir.path().to_str().unwrap()).write_path;
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
    assert!(
        after > before,
        "expected stale index rebuild to update mtime"
    );

    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(
        stderr.contains("stale") || stderr.contains("rebuilding"),
        "stderr={stderr}"
    );
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
fn test_routing_directory_count_search_uses_native_cpu_without_fallback() {
    let dir = tempdir().unwrap();
    write_text_corpus(dir.path());

    let output = tg()
        .arg("search")
        .arg("--verbose")
        .arg("-c")
        .arg("hello")
        .arg(dir.path())
        .output()
        .unwrap();

    assert!(output.status.success());
    let stderr = String::from_utf8_lossy(&output.stderr);

    if stderr.contains("routing_backend=RipgrepBackend") {
        assert_verbose_routing(&stderr, "RipgrepBackend", "rg_passthrough", false);
    } else if stderr.contains("routing_reason=rg_unavailable") {
        assert_verbose_routing(&stderr, "NativeCpuBackend", "rg_unavailable", false);
    } else {
        // UNREACHABLE ARM -- not coverage of the new route, despite naming its reason string.
        // These tests pass a DIRECTORY, which `native_can_serve_plain_text` refuses (walking
        // diverges from rg on binary-file notices and on emission order), so the plain-text route
        // can never answer them. The arm was already dead on origin/main for a different reason:
        // it needed `rg_available == true`, but reaching it required falling through a branch that
        // with `rg_available` true demands `structured_output == true`, which the line above
        // catches. Only the reason STRING changed here, as part of the
        // `cpu-auto-size-threshold` -> `plain-text-native` rename; no protection was added or
        // removed. Real coverage of the new route lives in
        // `test_routing_default_search_prefers_ripgrep_cold_path` (file arm) and
        // `tests/e2e/test_native_plain_text_parity.py`.
        assert_verbose_routing(&stderr, "NativeCpuBackend", "plain-text-native", false);
    }

    // Should NOT contain the fallback warning
    assert!(
        !stderr.contains("warning: native CPU search failed, falling back to ripgrep"),
        "Detected unexpected native CPU fallback in stderr: {}",
        stderr
    );

    // Verify output matches
    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(
        stdout.contains(":1\n") || stdout.contains(":1\r\n"),
        "stdout={}",
        stdout
    );
}

#[test]
fn test_routing_external_editor_plane_commands_are_forwarded() {
    let scenarios = vec!["map", "session", "doctor", "dogfood", "audit"];
    let dir = tempdir().unwrap();
    let python_wrapper = write_python_wrapper(dir.path());

    for command in scenarios {
        let output = tg()
            .current_dir(repo_root())
            .arg(command)
            .arg("--help")
            .env("TG_SIDECAR_PYTHON", &python_wrapper)
            .output()
            .unwrap();

        let stdout = combined_output(&output);
        assert!(
            stdout.contains(&format!("-m tensor_grep {}", command)),
            "External command '{}' was not forwarded properly. stdout={}",
            command,
            stdout
        );
    }
}

#[test]
fn test_routing_native_editor_plane_commands() {
    let dir = tempdir().unwrap();
    let python_wrapper = write_python_wrapper(dir.path());

    for command in ["defs", "refs", "context"] {
        let output = tg()
            .current_dir(repo_root())
            .arg(command)
            .arg("--help")
            .env("TG_SIDECAR_PYTHON", &python_wrapper)
            .output()
            .unwrap();

        let stdout = combined_output(&output);
        assert!(
            stdout.contains(&format!("-m tensor_grep {}", command)),
            "Native editor-plane command '{}' was not forwarded properly. output={}",
            command,
            stdout
        );
        assert!(
            stdout.contains("--help"),
            "Native editor-plane command '{}' did not forward the help flag. output={}",
            command,
            stdout
        );
    }
}

#[test]
fn test_routing_ast_workflow_commands_are_native() {
    for command in ["scan", "test", "new"] {
        let output = tg().arg(command).arg("--help").output().unwrap();

        let stdout = String::from_utf8_lossy(&output.stdout);
        assert!(stdout.to_lowercase().contains("usage:"));
        assert!(stdout.contains(command));
    }
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
        let payload = assert_json_routing(
            &output,
            "NativeGpuBackend",
            "gpu-device-ids-explicit-native",
            false,
        );
        assert_eq!(payload["total_matches"], 3);
        assert!(
            !marker.exists(),
            "native GPU routing should bypass the Python sidecar"
        );
    } else {
        let payload = assert_json_routing(&output, "GpuSidecar", "gpu-device-ids-explicit", true);
        assert_eq!(payload["total_matches"], 1);
        assert!(marker.exists(), "expected mock GPU sidecar invocation");
    }
}

#[cfg(not(feature = "cuda"))]
#[test]
fn test_public_gpu_request_without_explicit_sidecar_falls_back_to_native_cpu() {
    let dir = tempdir().unwrap();
    write_text_corpus(dir.path());

    let output = tg()
        .arg("search")
        .arg("--fixed-strings")
        .arg("--gpu-device-ids")
        .arg("0")
        .arg("--json")
        .arg("hello")
        .arg(dir.path())
        .env("TG_DISABLE_RG", "1")
        .env("PATH", "")
        .env("TG_SIDECAR_PYTHON", "definitely-missing-python")
        .output()
        .unwrap();

    let payload = assert_json_routing(&output, "NativeCpuBackend", "gpu-auto-fallback-cpu", false);
    assert_eq!(payload["total_matches"], 3);
    assert_eq!(payload["requested_gpu_device_ids"], serde_json::json!([0]));
    assert_eq!(payload["routing_gpu_device_ids"], serde_json::json!([]));
    assert_eq!(payload["gpu_evidence_status"], "unsupported");
    assert_eq!(payload["gpu_proof"], false);
    assert_eq!(payload["native_gpu_unavailable"], true);
    assert!(
        payload["not_gpu_proof_reason"]
            .as_str()
            .unwrap_or_default()
            .contains("not GPU acceleration proof"),
        "payload={payload}"
    );

    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(stderr.contains("native GPU unavailable"), "stderr={stderr}");
    assert!(
        stderr.contains("falling back to native CPU search"),
        "stderr={stderr}"
    );
    assert!(
        !stderr.contains("routing_backend=NativeGpuBackend"),
        "stderr={stderr}"
    );
}

#[cfg(not(feature = "cuda"))]
#[test]
fn test_public_gpu_ndjson_fallback_reports_no_routed_gpu_devices() {
    let dir = tempdir().unwrap();
    write_text_corpus(dir.path());

    let output = tg()
        .arg("search")
        .arg("--fixed-strings")
        .arg("--gpu-device-ids")
        .arg("0")
        .arg("--ndjson")
        .arg("hello")
        .arg(dir.path())
        .env("TG_DISABLE_RG", "1")
        .env("PATH", "")
        .env("TG_SIDECAR_PYTHON", "definitely-missing-python")
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let stdout = String::from_utf8_lossy(&output.stdout);
    let mut rows = 0usize;
    for line in stdout.lines() {
        let payload: Value = serde_json::from_str(line).unwrap();
        assert_eq!(payload["routing_backend"], "NativeCpuBackend");
        assert_eq!(payload["routing_reason"], "gpu-auto-fallback-cpu");
        assert_eq!(payload["sidecar_used"], false);
        assert_eq!(payload["requested_gpu_device_ids"], serde_json::json!([0]));
        assert_eq!(payload["routing_gpu_device_ids"], serde_json::json!([]));
        assert_eq!(payload["gpu_evidence_status"], "unsupported");
        assert_eq!(payload["gpu_proof"], false);
        assert_eq!(payload["native_gpu_unavailable"], true);
        assert!(
            payload["not_gpu_proof_reason"]
                .as_str()
                .unwrap_or_default()
                .contains("not GPU acceleration proof"),
            "payload={payload}"
        );
        rows += 1;
    }
    assert_eq!(rows, 3);
}

#[test]
fn test_rust_control_plane_plain_explicit() {
    let dir = tempdir().unwrap();
    write_text_corpus(dir.path());
    let rg_wrapper = write_rg_wrapper(dir.path());

    let output = tg()
        .arg("search")
        .arg("hello")
        .arg(dir.path())
        .env("TG_RG_PATH", &rg_wrapper)
        .output()
        .unwrap();

    assert!(output.status.success());
    let stdout = String::from_utf8_lossy(&output.stdout);
    // Should use the Rust control plane and dispatch to rg
    assert!(stdout.contains(RG_SENTINEL));
}

#[test]
fn test_rust_control_plane_plain_positional() {
    let dir = tempdir().unwrap();
    write_text_corpus(dir.path());
    let rg_wrapper = write_rg_wrapper(dir.path());

    let output = tg()
        .arg("hello")
        .arg(dir.path())
        .env("TG_RG_PATH", &rg_wrapper)
        .output()
        .unwrap();

    assert!(output.status.success());
    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(stdout.contains(RG_SENTINEL));
}

#[test]
fn test_rust_control_plane_positional_word_regexp_uses_word_boundaries() {
    let dir = tempdir().unwrap();
    let file = dir.path().join("words.txt");
    fs::write(&file, "word\nwordplay\nsword\nword\n").unwrap();

    let output = tg()
        .arg("--cpu")
        .arg("-w")
        .arg("word")
        .arg(&file)
        .env("TG_DISABLE_RG", "1")
        .env("PATH", "")
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "stderr={}",
        String::from_utf8_lossy(&output.stderr)
    );
    assert_eq!(
        normalize_newlines(&String::from_utf8_lossy(&output.stdout)),
        "word\nword\n"
    );
}

#[test]
fn test_native_cpu_fixed_multi_pattern_uses_single_pass_fast_path() {
    let dir = tempdir().unwrap();
    let file = dir.path().join("multi.txt");
    fs::write(
        &file,
        "alpha and beta\n\nbeta after blank\nalpha after beta\n",
    )
    .unwrap();

    let output = tg()
        .arg("search")
        .arg("--fixed-strings")
        .arg("--json")
        .arg("-e")
        .arg("alpha")
        .arg("-e")
        .arg("beta")
        .arg(&file)
        .env("TG_DISABLE_RG", "1")
        .env("PATH", "")
        .env(
            "TG_TEST_NATIVE_SEARCH_FORCE_ERROR",
            "sequential native search path used",
        )
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "stdout={}\nstderr={}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let payload: Value = serde_json::from_slice(&output.stdout).unwrap();
    let matches = payload["matches"].as_array().unwrap();
    let tuples = matches
        .iter()
        .map(|entry| {
            (
                entry["line"].as_u64().unwrap(),
                entry["text"].as_str().unwrap().to_string(),
                entry["pattern_id"].as_u64().unwrap(),
                entry["pattern_text"].as_str().unwrap().to_string(),
            )
        })
        .collect::<Vec<_>>();

    assert_eq!(
        tuples,
        vec![
            (1, "alpha and beta".to_string(), 0, "alpha".to_string()),
            (1, "alpha and beta".to_string(), 1, "beta".to_string()),
            (3, "beta after blank".to_string(), 1, "beta".to_string()),
            (4, "alpha after beta".to_string(), 0, "alpha".to_string()),
            (4, "alpha after beta".to_string(), 1, "beta".to_string()),
        ]
    );
}

#[test]
fn test_native_cpu_fixed_multi_pattern_plain_output_omits_line_numbers_by_default() {
    let dir = tempdir().unwrap();
    let file = dir.path().join("multi.txt");
    fs::write(
        &file,
        "alpha and beta\n\nbeta after blank\nalpha after beta\n",
    )
    .unwrap();

    let output = tg()
        .arg("search")
        .arg("--fixed-strings")
        .arg("-e")
        .arg("alpha")
        .arg("-e")
        .arg("beta")
        .arg(&file)
        .env("TG_DISABLE_RG", "1")
        .env("PATH", "")
        .env(
            "TG_TEST_NATIVE_SEARCH_FORCE_ERROR",
            "sequential native search path used",
        )
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "stdout={}\nstderr={}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    assert_eq!(
        normalize_newlines(&String::from_utf8_lossy(&output.stdout)),
        "alpha and beta\nbeta after blank\nalpha after beta\n"
    );
}

#[test]
fn test_native_cpu_fixed_multi_pattern_plain_output_honors_line_number_flag() {
    let dir = tempdir().unwrap();
    let file = dir.path().join("multi.txt");
    fs::write(
        &file,
        "alpha and beta\n\nbeta after blank\nalpha after beta\n",
    )
    .unwrap();

    let output = tg()
        .arg("search")
        .arg("--fixed-strings")
        .arg("--line-number")
        .arg("-e")
        .arg("alpha")
        .arg("-e")
        .arg("beta")
        .arg(&file)
        .env("TG_DISABLE_RG", "1")
        .env("PATH", "")
        .env(
            "TG_TEST_NATIVE_SEARCH_FORCE_ERROR",
            "sequential native search path used",
        )
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "stdout={}\nstderr={}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    assert_eq!(
        normalize_newlines(&String::from_utf8_lossy(&output.stdout)),
        "1:alpha and beta\n3:beta after blank\n4:alpha after beta\n"
    );
}

#[test]
fn test_native_cpu_fixed_multi_pattern_count_counts_matching_lines_once() {
    let dir = tempdir().unwrap();
    let file = dir.path().join("multi.txt");
    fs::write(
        &file,
        "alpha and beta\n\nbeta after blank\nalpha after beta\n",
    )
    .unwrap();

    let output = tg()
        .arg("search")
        .arg("--fixed-strings")
        .arg("--count")
        .arg("-e")
        .arg("alpha")
        .arg("-e")
        .arg("beta")
        .arg(&file)
        .env("TG_DISABLE_RG", "1")
        .env("PATH", "")
        .env(
            "TG_TEST_NATIVE_SEARCH_FORCE_ERROR",
            "sequential native search path used",
        )
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "stdout={}\nstderr={}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    assert_eq!(
        normalize_newlines(&String::from_utf8_lossy(&output.stdout)),
        "3\n"
    );
}

#[test]
fn test_rust_control_plane_rejection() {
    let dir = tempdir().unwrap();
    write_text_corpus(dir.path());
    let rg_wrapper = write_rg_wrapper(dir.path());

    // -F should fall through to full stack.
    // Full stack with --verbose will show a reason.
    let output_f = tg()
        .arg("--verbose")
        .arg("-F")
        .arg("hello")
        .arg(dir.path())
        .env("TG_RG_PATH", &rg_wrapper)
        .output()
        .unwrap();

    let stderr = String::from_utf8_lossy(&output_f.stderr);
    // If it fell through to Clap, it will process --verbose and print routing info.
    // The fast path would have silently dispatched without printing [routing]...
    assert!(stderr.contains("[routing]"));
    assert!(stderr.contains("routing_backend=RipgrepBackend"));

    // --help should fall through
    let output_help = tg().arg("--help").output().unwrap();
    assert!(String::from_utf8_lossy(&output_help.stdout).contains("Usage:"));

    // scan should fall through
    let output_scan = tg().arg("scan").arg("--help").output().unwrap();
    let stdout_scan = String::from_utf8_lossy(&output_scan.stdout);
    assert!(stdout_scan.to_lowercase().contains("usage:"));
    assert!(stdout_scan.contains("scan"));
}

#[test]
fn test_rust_control_plane_native_fallback() {
    let dir = tempdir().unwrap();
    write_text_corpus(dir.path());

    let output = tg()
        .arg("hello")
        .arg(dir.path())
        .env("TG_DISABLE_RG", "1")
        .env("PATH", "") // Ensure rg is not in path
        .output()
        .unwrap();

    assert!(output.status.success());
    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(stdout.contains("hello world"));
    assert!(!stdout.contains(RG_SENTINEL));
}

#[test]
fn test_rust_control_plane_no_match_exit_code() {
    let dir = tempdir().unwrap();
    write_text_corpus(dir.path());

    let output = tg()
        .arg("non_existent_pattern")
        .arg(dir.path())
        .output()
        .unwrap();

    assert_eq!(output.status.code(), Some(1));
}

#[test]
fn test_rust_control_plane_combined_flags() {
    let dir = tempdir().unwrap();
    write_text_corpus(dir.path());
    let rg_wrapper = write_rg_wrapper(dir.path());

    let output = tg()
        .arg("-iv")
        .arg("non_existent")
        .arg(dir.path())
        .env("TG_RG_PATH", &rg_wrapper)
        .output()
        .unwrap();

    assert!(output.status.success());
    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(stdout.contains(RG_SENTINEL));
}

#[test]
fn test_rust_control_plane_no_ignore() {
    let dir = tempdir().unwrap();
    write_text_corpus(dir.path());
    let rg_wrapper = write_rg_wrapper(dir.path());

    let output = tg()
        .arg("search")
        .arg("--no-ignore")
        .arg("hello")
        .arg(dir.path())
        .env("TG_RG_PATH", &rg_wrapper)
        .output()
        .unwrap();

    assert!(output.status.success());
    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(stdout.contains(RG_SENTINEL));
}

#[test]
fn test_rust_control_plane_search_accepts_rg_sort_files_and_maxdepth_aliases() {
    let dir = tempdir().unwrap();
    write_text_corpus(dir.path());
    let rg_wrapper = write_rg_wrapper(dir.path());

    let output = tg()
        .arg("search")
        .arg("--format")
        .arg("rg")
        .arg("--sort-files")
        .arg("--maxdepth")
        .arg("1")
        .arg("hello")
        .arg(dir.path())
        .env("TG_RG_PATH", &rg_wrapper)
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
    assert!(stdout.contains(RG_SENTINEL));
}

#[test]
fn test_root_rg_style_forwarding_accepts_sort_files_and_maxdepth_aliases() {
    let dir = tempdir().unwrap();
    write_text_corpus(dir.path());
    let rg_wrapper = write_rg_wrapper(dir.path());

    let output = tg()
        .arg("--format")
        .arg("rg")
        .arg("--sort-files")
        .arg("--maxdepth")
        .arg("1")
        .arg("hello")
        .arg(dir.path())
        .env("TG_RG_PATH", &rg_wrapper)
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
    assert!(stdout.contains(RG_SENTINEL));
}

#[test]
fn test_rust_control_plane_case_insensitive_search_dispatches_to_ripgrep() {
    let dir = tempdir().unwrap();
    write_text_corpus(dir.path());
    let rg_wrapper = write_rg_wrapper(dir.path());

    let output = tg()
        .arg("search")
        .arg("-i")
        .arg("warning")
        .arg(dir.path())
        .env("TG_RG_PATH", &rg_wrapper)
        .output()
        .unwrap();

    assert!(output.status.success());
    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(stdout.contains(RG_SENTINEL));
}

#[test]
fn test_rust_control_plane_max_count_search_dispatches_to_ripgrep() {
    let dir = tempdir().unwrap();
    write_text_corpus(dir.path());
    let rg_wrapper = write_rg_wrapper(dir.path());

    let output = tg()
        .arg("search")
        .arg("-m")
        .arg("5")
        .arg("ERROR")
        .arg(dir.path())
        .env("TG_RG_PATH", &rg_wrapper)
        .output()
        .unwrap();

    assert!(output.status.success());
    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(stdout.contains(RG_SENTINEL));
}

#[test]
fn test_rust_control_plane_count_search_dispatches_to_ripgrep() {
    let dir = tempdir().unwrap();
    write_text_corpus(dir.path());
    let rg_wrapper = write_rg_wrapper(dir.path());

    let output = tg()
        .arg("search")
        .arg("--count")
        .arg("ERROR")
        .arg(dir.path())
        .env("TG_RG_PATH", &rg_wrapper)
        .output()
        .unwrap();

    assert!(output.status.success());
    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(stdout.contains(RG_SENTINEL));
}

#[test]
fn test_rust_control_plane_glob_search_dispatches_to_ripgrep() {
    let dir = tempdir().unwrap();
    write_text_corpus(dir.path());
    let rg_wrapper = write_rg_wrapper(dir.path());

    let output = tg()
        .arg("search")
        .arg("--glob")
        .arg("*.txt")
        .arg("ERROR")
        .arg(dir.path())
        .env("TG_RG_PATH", &rg_wrapper)
        .output()
        .unwrap();

    assert!(output.status.success());
    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(stdout.contains(RG_SENTINEL));
}

#[test]
fn test_rust_control_plane_many_fixed_patterns_dispatch_to_ripgrep_by_default() {
    let dir = tempdir().unwrap();
    write_text_corpus(dir.path());
    let rg_wrapper = write_rg_wrapper(dir.path());

    let output = tg()
        .arg("search")
        .arg("--fixed-strings")
        .arg("-e")
        .arg("ERROR")
        .arg("-e")
        .arg("WARN")
        .arg(dir.path())
        .env("TG_RG_PATH", &rg_wrapper)
        .output()
        .unwrap();

    assert!(output.status.success());
    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(stdout.contains(RG_SENTINEL));
}

#[test]
fn test_rust_control_plane_version() {
    let expected = format!("tg {}", env!("CARGO_PKG_VERSION"));

    let output_v_long = tg().arg("--version").output().unwrap();
    assert!(String::from_utf8_lossy(&output_v_long.stdout).contains(&expected));

    let output_v_short = tg().arg("-V").output().unwrap();
    assert!(String::from_utf8_lossy(&output_v_short.stdout).contains(&expected));
}

//! Round-60 Task 2A dedicated CI nodes: direct-native process-level doors.
//!
//! These live as an integration test so ``CARGO_BIN_EXE_tg`` is valid. Each
//! node is ``#[ignore]`` and executed via ``--exact --include-ignored`` by the
//! stable Rust runner. Pattern-file oversize cap stays distinct from PCRE2
//! refusal; below-cap native JSON success is distinct from "exactly once"
//! matcher construction (that leaf lives in ``native_search`` unit tests).

use serde_json::Value;
use std::path::{Path, PathBuf};
use std::process::Command;

const MAX_PATTERN_OR_IGNORE_RULE_BYTES: usize = 16 << 10;

fn write_spawn_canary(dir: &Path, name: &str) -> (PathBuf, PathBuf) {
    let marker = dir.join(format!("{name}-started"));
    let canary = dir.join(if cfg!(windows) {
        format!("{name}-canary.cmd")
    } else {
        format!("{name}-canary")
    });
    #[cfg(windows)]
    {
        std::fs::write(
            &canary,
            format!(
                "@echo off\r\necho started > \"{}\"\r\nexit /b 0\r\n",
                marker.display()
            ),
        )
        .unwrap();
    }
    #[cfg(not(windows))]
    {
        std::fs::write(
            &canary,
            format!("#!/bin/sh\necho started > '{}'\nexit 0\n", marker.display()),
        )
        .unwrap();
        use std::os::unix::fs::PermissionsExt;
        let mut perms = std::fs::metadata(&canary).unwrap().permissions();
        perms.set_mode(0o755);
        std::fs::set_permissions(&canary, perms).unwrap();
    }
    (canary, marker)
}

/// Dedicated closed-world CI node: over-cap pattern-file direct-native refusal.
#[test]
#[ignore = "task2a round60 dedicated CI node; run via --exact --include-ignored"]
fn pattern_file_search_input_limit_direct_native() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path().join("repo");
    std::fs::create_dir_all(&root).unwrap();
    std::fs::write(root.join("a.txt"), "needle\n").unwrap();
    let patterns_path = dir.path().join("patterns.txt");
    let oversize_rule = "x".repeat(MAX_PATTERN_OR_IGNORE_RULE_BYTES + 1);
    std::fs::write(&patterns_path, format!("{oversize_rule}\n")).unwrap();
    let (rg_canary, rg_marker) = write_spawn_canary(dir.path(), "rg");
    let (sidecar_canary, sidecar_marker) = write_spawn_canary(dir.path(), "sidecar");
    let bin = env!("CARGO_BIN_EXE_tg");
    let output = Command::new(bin)
        .args([
            "search",
            "--json",
            "-f",
            patterns_path.to_str().unwrap(),
            root.to_str().unwrap(),
        ])
        .env("TG_DISABLE_RG", "1")
        .env("TG_RG_PATH", &rg_canary)
        .env("TG_SIDECAR_PYTHON", &sidecar_canary)
        .output()
        .expect("spawn tg binary");
    let stdout = String::from_utf8_lossy(&output.stdout);
    let stderr = String::from_utf8_lossy(&output.stderr);
    let blob = format!("{stdout}\n{stderr}");
    assert_eq!(
        output.status.code(),
        Some(2),
        "direct-native over-cap refusal must exit 2; out={blob}"
    );
    assert!(
        blob.contains("search_input_limit"),
        "direct-native over-cap refusal must emit literal search_input_limit; out={blob}"
    );
    assert!(
        !rg_marker.exists(),
        "direct-native over-cap refusal must not start rg canary"
    );
    assert!(
        !sidecar_marker.exists(),
        "direct-native over-cap refusal must not start sidecar canary"
    );
}

/// Dedicated closed-world CI node: below-cap pattern-file direct-native JSON success.
///
/// Proves NativeCpuBackend JSON success + zero rg/sidecar — not matcher
/// construction exactly-once (that is the separate native_search leaf).
#[test]
#[ignore = "task2a round60 dedicated CI node; run via --exact --include-ignored"]
fn pattern_file_below_cap_native_json_success() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path().join("repo");
    std::fs::create_dir_all(&root).unwrap();
    std::fs::write(root.join("a.txt"), "needle\n").unwrap();
    let patterns_path = dir.path().join("patterns.txt");
    std::fs::write(&patterns_path, "needle\n").unwrap();
    let (rg_canary, rg_marker) = write_spawn_canary(dir.path(), "rg");
    let (sidecar_canary, sidecar_marker) = write_spawn_canary(dir.path(), "sidecar");
    let bin = env!("CARGO_BIN_EXE_tg");
    let output = Command::new(bin)
        .args([
            "search",
            "--json",
            "-f",
            patterns_path.to_str().unwrap(),
            root.to_str().unwrap(),
        ])
        .env("TG_DISABLE_RG", "1")
        .env("TG_RG_PATH", &rg_canary)
        .env("TG_SIDECAR_PYTHON", &sidecar_canary)
        .output()
        .expect("spawn tg binary");
    let stdout = String::from_utf8_lossy(&output.stdout);
    let stderr = String::from_utf8_lossy(&output.stderr);
    let blob = format!("{stdout}\n{stderr}");
    let code = output.status.code().unwrap_or(2);
    assert!(
        code == 0 || code == 1,
        "below-cap direct-native must exit 0 or 1 (non-incomplete), got {code}; out={blob}"
    );
    assert!(
        !blob.contains("search_input_limit"),
        "below-cap must not emit search_input_limit; out={blob}"
    );
    let payload: Value = serde_json::from_str(stdout.trim()).unwrap_or_else(|err| {
        panic!("below-cap direct-native must emit aggregate JSON stdout: {err}; out={blob}")
    });
    assert_eq!(
        payload.get("routing_backend").and_then(|v| v.as_str()),
        Some("NativeCpuBackend"),
        "below-cap must route through NativeCpuBackend; out={blob}"
    );
    let total = payload
        .get("total_matches")
        .and_then(|v| v.as_u64())
        .unwrap_or(0);
    assert!(
        total >= 1,
        "below-cap must report successful match semantics (total_matches>=1); out={blob}"
    );
    let matches = payload
        .get("matches")
        .and_then(|v| v.as_array())
        .cloned()
        .unwrap_or_default();
    assert!(
        !matches.is_empty(),
        "below-cap must include at least one match row; out={blob}"
    );
    assert!(!rg_marker.exists(), "below-cap must not start rg canary");
    assert!(
        !sidecar_marker.exists(),
        "below-cap must not start sidecar canary"
    );
}

/// Dedicated closed-world CI node: uninstrumented PCRE2 refused on direct-native.
///
/// Matcher/compiler construction (zero before refusal) is asserted by the
/// in-process ``MatcherConstructionObserver`` oracle in ``native_search`` tests —
/// not a production env canary.
#[test]
#[ignore = "task2a round60 dedicated CI node; run via --exact --include-ignored"]
fn pcre2_search_input_limit_direct_native() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path().join("repo");
    std::fs::create_dir_all(&root).unwrap();
    std::fs::write(root.join("a.txt"), "needle\n").unwrap();
    let (rg_canary, rg_marker) = write_spawn_canary(dir.path(), "rg");
    let (sidecar_canary, sidecar_marker) = write_spawn_canary(dir.path(), "sidecar");
    let bin = env!("CARGO_BIN_EXE_tg");
    let output = Command::new(bin)
        .args([
            "search",
            "--json",
            "--pcre2",
            "needle",
            root.to_str().unwrap(),
        ])
        .env("TG_DISABLE_RG", "1")
        .env("TG_RG_PATH", &rg_canary)
        .env("TG_SIDECAR_PYTHON", &sidecar_canary)
        .output()
        .expect("spawn tg binary");
    let stdout = String::from_utf8_lossy(&output.stdout);
    let stderr = String::from_utf8_lossy(&output.stderr);
    let blob = format!("{stdout}\n{stderr}");
    assert_eq!(
        output.status.code(),
        Some(2),
        "direct-native PCRE2 refusal must exit 2; out={blob}"
    );
    assert!(
        blob.contains("search_input_limit"),
        "direct-native PCRE2 refusal must emit literal search_input_limit; out={blob}"
    );
    assert!(
        !rg_marker.exists(),
        "direct-native PCRE2 refusal must not start rg canary"
    );
    assert!(
        !sidecar_marker.exists(),
        "direct-native PCRE2 refusal must not start sidecar canary"
    );
}

/// Dedicated closed-world CI node: below-cap non-PCRE2 direct-native JSON success.
///
/// Exactly-once matcher construction is asserted by the in-process
/// ``MatcherConstructionObserver`` oracle in ``native_search`` tests — not a
/// production env canary.
#[test]
#[ignore = "task2a round60 dedicated CI node; run via --exact --include-ignored"]
fn below_cap_non_pcre2_direct_native_json_success() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path().join("repo");
    std::fs::create_dir_all(&root).unwrap();
    std::fs::write(root.join("a.txt"), "needle\n").unwrap();
    let (rg_canary, rg_marker) = write_spawn_canary(dir.path(), "rg");
    let (sidecar_canary, sidecar_marker) = write_spawn_canary(dir.path(), "sidecar");
    let bin = env!("CARGO_BIN_EXE_tg");
    let output = Command::new(bin)
        .args(["search", "--json", "needle", root.to_str().unwrap()])
        .env("TG_DISABLE_RG", "1")
        .env("TG_RG_PATH", &rg_canary)
        .env("TG_SIDECAR_PYTHON", &sidecar_canary)
        .output()
        .expect("spawn tg binary");
    let stdout = String::from_utf8_lossy(&output.stdout);
    let stderr = String::from_utf8_lossy(&output.stderr);
    let blob = format!("{stdout}\n{stderr}");
    let code = output.status.code().unwrap_or(2);
    assert!(
        code == 0 || code == 1,
        "below-cap non-PCRE2 direct-native must exit 0 or 1; got {code}; out={blob}"
    );
    assert!(
        !blob.contains("search_input_limit"),
        "below-cap must not emit search_input_limit; out={blob}"
    );
    let payload: Value = serde_json::from_str(stdout.trim()).unwrap_or_else(|err| {
        panic!("below-cap must emit aggregate JSON: {err}; out={blob}")
    });
    assert_eq!(
        payload.get("routing_backend").and_then(|v| v.as_str()),
        Some("NativeCpuBackend"),
        "below-cap must route NativeCpuBackend; out={blob}"
    );
    assert!(
        payload
            .get("total_matches")
            .and_then(|v| v.as_u64())
            .unwrap_or(0)
            >= 1
    );
    assert!(!rg_marker.exists());
    assert!(!sidecar_marker.exists());
}

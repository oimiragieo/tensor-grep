#![cfg(all(windows, target_arch = "x86_64", not(target_feature = "crt-static")))]

use std::{fs, process::Command};

#[test]
fn dynamic_crt_build_refuses_verification_before_opening_inputs() {
    let dir = tempfile::tempdir().unwrap();
    let poison = dir.path().join("poison");
    fs::create_dir(&poison).unwrap();
    for name in ["python.exe", "uv.exe"] {
        fs::write(poison.join(name), b"not an executable").unwrap();
    }
    let output = Command::new(env!("CARGO_BIN_EXE_tg-recovery-preflight"))
        .current_dir(dir.path())
        .env("PATH", &poison)
        .env("TG_SIDECAR_PYTHON", poison.join("python.exe"))
        .args(["--python-archive", "missing", "--uv-wheel", "missing"])
        .output()
        .unwrap();
    assert!(!output.status.success());
    assert!(String::from_utf8_lossy(&output.stderr).contains("static CRT required"));
    assert!(output.stdout.is_empty());
}

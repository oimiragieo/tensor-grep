#![allow(unused_imports, dead_code)]
use std::fs;
use std::path::{Path, PathBuf};
use std::process::{Command, Output};
use std::thread;
use std::time::Duration;
use std::time::{SystemTime, UNIX_EPOCH};

use serde_json::Value;
use tempfile::{tempdir, TempDir};

#[path = "../support/routing.rs"]
pub mod routing_support;

use routing_support::{combined_output, repo_python, repo_root, tg};

pub const RG_SENTINEL: &str = "TG_RG_ROUTING_SENTINEL";

pub fn normalize_newlines(text: &str) -> String {
    text.replace("\r\n", "\n")
}

pub fn tg_fast() -> Command {
    Command::new(env!("CARGO_BIN_EXE_tg-search-fast"))
}

pub fn write_text_corpus(dir: &Path) {
    fs::write(
        dir.join("a.txt"),
        "hello world\nfoo bar baz\ngoodbye world\n",
    )
    .unwrap();
    fs::write(dir.join("b.txt"), "nothing here\nhello again friend\nend\n").unwrap();
    fs::write(dir.join("notes.md"), "hello from markdown\n").unwrap();
}

pub fn write_sized_routing_corpus(dir: &Path, target_bytes: usize) -> PathBuf {
    let corpus = dir.join(format!("corpus-{target_bytes}"));
    fs::create_dir(&corpus).unwrap();

    let chunk = b"INFO steady state\nERROR gpu auto route\nWARN retry later\n";
    let mut bytes = Vec::new();
    while bytes.len() < target_bytes {
        bytes.extend_from_slice(chunk);
    }

    let file_count = 4usize;
    let chunk_size = bytes.len().div_ceil(file_count);
    for index in 0..file_count {
        let start = index * chunk_size;
        if start >= bytes.len() {
            break;
        }
        let end = ((index + 1) * chunk_size).min(bytes.len());
        fs::write(
            corpus.join(format!("chunk-{index}.log")),
            &bytes[start..end],
        )
        .unwrap();
    }

    corpus
}

pub fn unix_timestamp_now() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_secs()
}

pub fn write_crossover_config(
    path: &Path,
    breakpoint_bytes: u64,
    cpu_median_ms: f64,
    gpu_median_ms: f64,
    recommendation: &str,
    calibration_timestamp: u64,
) {
    let payload = serde_json::json!({
        "version": 1,
        "routing_backend": "Calibration",
        "routing_reason": "manual-calibrate",
        "sidecar_used": false,
        "corpus_size_breakpoint_bytes": breakpoint_bytes,
        "cpu_median_ms": cpu_median_ms,
        "gpu_median_ms": gpu_median_ms,
        "recommendation": recommendation,
        "calibration_timestamp": calibration_timestamp,
        "device_name": "Mock RTX 4070",
        "measurements": [
            {
                "size_bytes": breakpoint_bytes,
                "cpu_median_ms": cpu_median_ms,
                "gpu_median_ms": gpu_median_ms,
                "cpu_samples_ms": [cpu_median_ms],
                "gpu_samples_ms": [gpu_median_ms]
            }
        ]
    });

    fs::create_dir_all(path.parent().unwrap()).unwrap();
    fs::write(path, serde_json::to_vec_pretty(&payload).unwrap()).unwrap();
}

pub fn write_python_source() -> (TempDir, PathBuf) {
    let dir = tempdir().unwrap();
    let file_path = dir.path().join("fixture.py");
    fs::write(&file_path, "def add(a, b):\n    return a + b\n").unwrap();
    (dir, file_path)
}

pub fn write_python_wrapper(dir: &Path) -> PathBuf {
    if cfg!(windows) {
        let script = dir.join("python-wrapper.cmd");
        fs::write(&script, "@echo off\r\necho %*\r\n").unwrap();
        script
    } else {
        let script = dir.join("python-wrapper.sh");
        fs::write(&script, "#!/bin/sh\nprintf '%s\\n' \"$*\"\n").unwrap();
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

pub fn build_index(dir: &Path) {
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

pub fn write_rg_wrapper(dir: &Path) -> PathBuf {
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

pub fn write_rg_required_args_wrapper(dir: &Path, required_args: &[&str]) -> PathBuf {
    if cfg!(windows) {
        let script = dir.join("rg-required-wrapper.cmd");
        let mut body = String::from("@echo off\r\n");
        for (index, required) in required_args.iter().enumerate() {
            body.push_str(&format!("set FOUND_{index}=\r\n"));
            body.push_str("for %%A in (%*) do (\r\n");
            body.push_str(&format!(
                "  if \"%%~A\"==\"{required}\" set FOUND_{index}=1\r\n"
            ));
            body.push_str(")\r\n");
            body.push_str(&format!(
                "if not \"%FOUND_{index}%\"==\"1\" echo missing {required} 1>&2 & exit /b 9\r\n"
            ));
        }
        body.push_str(&format!("echo {RG_SENTINEL}\r\n"));
        fs::write(&script, body).unwrap();
        script
    } else {
        let script = dir.join("rg-required-wrapper.sh");
        let mut body = String::from("#!/bin/sh\n");
        for required in required_args {
            body.push_str("found=0\n");
            body.push_str("for arg in \"$@\"; do\n");
            body.push_str(&format!(
                "  if [ \"$arg\" = '{}' ]; then found=1; fi\n",
                required.replace('\'', "'\\''")
            ));
            body.push_str("done\n");
            body.push_str(&format!(
                "if [ \"$found\" != 1 ]; then echo 'missing {}' >&2; exit 9; fi\n",
                required.replace('\'', "'\\''")
            ));
        }
        body.push_str(&format!("printf '{RG_SENTINEL}\\n'\n"));
        fs::write(&script, body).unwrap();
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

pub fn write_mock_gpu_sidecar_script(dir: &Path, matched_file: &Path, marker: &Path) -> PathBuf {
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

pub fn assert_verbose_routing(stderr: &str, backend: &str, reason: &str, sidecar_used: bool) {
    assert!(
        stderr.contains(&format!("routing_backend={backend}")),
        "stderr={stderr}"
    );
    assert!(
        stderr.contains(&format!("routing_reason={reason}")),
        "stderr={stderr}"
    );
    assert!(
        stderr.contains(&format!("sidecar_used={sidecar_used}")),
        "stderr={stderr}"
    );
}

pub fn assert_json_routing(
    output: &Output,
    backend: &str,
    reason: &str,
    sidecar_used: bool,
) -> Value {
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

pub fn assert_ndjson_routing(
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

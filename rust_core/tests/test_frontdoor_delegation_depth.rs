//! RED tests: the native <-> Python front-door mutual-delegation loop (P0, 2026-10-05).
//!
//! Observed on a shared Windows server: `tg.exe search -s --json -- hello a.txt` re-exec'd
//! `python -m tensor_grep search ...`, whose full CLI delegated the same argv back to `tg.exe`,
//! and so on: 842 processes in ~60 s. The native half: `-s` / `-N` are in
//! `SEARCH_PYTHON_PASSTHROUGH_FLAGS` (search_flag_registry.rs), so
//! `search_format_python_passthrough_args` (main.rs) hands the search to
//! `execute_python_passthrough_command`, and `configure_python_child_environment`
//! (python_sidecar.rs) stamps only the boolean `TG_REEXEC_GUARD=1` -- which the native door itself
//! never reads, so a second, third, ... native hop re-execs Python again without limit.
//!
//! Proposed contract (both doors): `TG_FRONTDOOR_HOPS` counts cross-door hops already taken
//! (absent == 0); a door spawning the other door passes `hops + 1`; at `hops >= cap` (cap <= 8)
//! or on a malformed value it refuses with exit 2 and a message naming the variable.
//!
//! These tests never spawn a real Python or a second `tg`: `TG_SIDECAR_PYTHON` points at a fake
//! interpreter that records its argv and the two marker variables to a JSON file and exits 0.

use std::fs;
use std::path::{Path, PathBuf};
use std::process::{Command, Output};

use serde_json::Value;
use tempfile::tempdir;

const HOPS_ENV: &str = "TG_FRONTDOOR_HOPS";
const RECORD_ENV: &str = "TG_LOOPBUG_FAKE_PYTHON_RECORD";

/// The fake interpreter. Python, because the existing suites already depend on a `python` on
/// PATH for their fake rg / fake sidecar scripts (test_public_native_cli_parity.rs).
const FAKE_PYTHON_BODY: &str = r#"import json, os, sys
record = os.environ["TG_LOOPBUG_FAKE_PYTHON_RECORD"]
with open(record, "w", encoding="utf-8") as handle:
    json.dump(
        {
            "argv": sys.argv[1:],
            "hops": os.environ.get("TG_FRONTDOOR_HOPS"),
            "guard": os.environ.get("TG_REEXEC_GUARD"),
        },
        handle,
    )
sys.stdout.write('{"total_matches": 1, "matches": []}\n')
"#;

fn write_executable_script(dir: &Path, name: &str, body: &str) -> PathBuf {
    let script = dir.join(name);
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

fn fake_python(dir: &Path) -> PathBuf {
    let script = dir.join("fake-python-recorder.py");
    fs::write(&script, FAKE_PYTHON_BODY).unwrap();
    if cfg!(windows) {
        write_executable_script(
            dir,
            "fake-python-recorder.cmd",
            &format!("@echo off\r\npython \"{}\" %*\r\n", script.display()),
        )
    } else {
        write_executable_script(
            dir,
            "fake-python-recorder",
            &format!("#!/bin/sh\nexec python \"{}\" \"$@\"\n", script.display()),
        )
    }
}

struct Run {
    output: Output,
    record: Option<Value>,
}

/// `tg search <flag> --json -- hello a.txt` -- the exact observed shape -- with every
/// front-door / routing variable scrubbed, then `extra_env` applied.
fn run_observed_shape(flag: &str, extra_env: &[(&str, &str)]) -> Run {
    let dir = tempdir().unwrap();
    fs::write(dir.path().join("a.txt"), "hello world\nHello again\n").unwrap();
    let python = fake_python(dir.path());
    let record = dir.path().join("fake-python-record.json");

    let mut command = Command::new(env!("CARGO_BIN_EXE_tg"));
    command
        .current_dir(dir.path())
        .args(["search", flag, "--json", "--", "hello", "a.txt"])
        .env("TG_SIDECAR_PYTHON", &python)
        .env(RECORD_ENV, &record);
    for key in [
        HOPS_ENV,
        "TG_REEXEC_GUARD",
        "TG_NATIVE_TG_BINARY",
        "TG_SIDECAR_SCRIPT",
        "TG_SIDECAR_MODULE",
        "TG_RUST_EARLY_RG",
        "TG_RUST_EARLY_POSITIONAL_RG",
        "TG_PASSTHROUGH_TIMEOUT_MS",
    ] {
        command.env_remove(key);
    }
    for (key, value) in extra_env {
        command.env(key, value);
    }
    let output = command.output().unwrap();
    let record = fs::read_to_string(&record)
        .ok()
        .map(|text| serde_json::from_str::<Value>(&text).expect("fake python wrote JSON"));
    Run { output, record }
}

fn describe(run: &Run) -> String {
    format!(
        "status={:?}\nrecord={:?}\nstdout={}\nstderr={}",
        run.output.status.code(),
        run.record,
        String::from_utf8_lossy(&run.output.stdout),
        String::from_utf8_lossy(&run.output.stderr)
    )
}

#[test]
fn observed_loop_flags_still_route_to_the_python_door() {
    // Positive control (GREEN on main): the observed shapes DO reach the Python passthrough, so
    // the hop assertions below test a live route, not a dead one. If routing changes so that
    // native handles -s/-N itself, this fails first and says so.
    for flag in ["-s", "-N"] {
        let run = run_observed_shape(flag, &[]);
        assert!(
            run.output.status.success(),
            "flag={flag}\n{}",
            describe(&run)
        );
        let record = run
            .record
            .as_ref()
            .unwrap_or_else(|| panic!("flag={flag}: fake python never ran\n{}", describe(&run)));
        let argv: Vec<&str> = record["argv"]
            .as_array()
            .unwrap()
            .iter()
            .filter_map(Value::as_str)
            .collect();
        assert_eq!(&argv[..3], ["-m", "tensor_grep", "search"], "flag={flag}");
        assert!(argv.contains(&flag), "flag={flag} argv={argv:?}");
        assert_eq!(
            record["guard"], "1",
            "flag={flag}: TG_REEXEC_GUARD no longer stamped"
        );
    }
}

#[test]
fn python_passthrough_stamps_the_first_cross_door_hop() {
    // RED on main: only the boolean TG_REEXEC_GUARD is stamped; no depth reaches Python.
    for flag in ["-s", "-N"] {
        let run = run_observed_shape(flag, &[]);
        let record = run
            .record
            .as_ref()
            .unwrap_or_else(|| panic!("flag={flag}: fake python never ran\n{}", describe(&run)));
        assert_eq!(record["hops"], "1", "flag={flag}\n{}", describe(&run));
    }
}

#[test]
fn python_passthrough_continues_an_existing_chain() {
    // RED on main: a native door spawned BY Python (hops=2 inherited) must pass 3, not reset.
    let run = run_observed_shape("-s", &[(HOPS_ENV, "2")]);
    let record = run
        .record
        .as_ref()
        .unwrap_or_else(|| panic!("fake python never ran\n{}", describe(&run)));
    assert_eq!(record["hops"], "3", "{}", describe(&run));
}

#[test]
fn native_refuses_python_passthrough_at_the_hop_cap() {
    // RED on main: the native door re-execs Python at ANY depth -- the 842-process loop. At/over
    // the cap it must refuse with exit 2, spawn nothing, and name the variable.
    for flag in ["-s", "-N"] {
        let run = run_observed_shape(flag, &[(HOPS_ENV, "99"), ("TG_REEXEC_GUARD", "1")]);
        assert!(
            run.record.is_none(),
            "flag={flag}: re-exec'd Python at hop 99\n{}",
            describe(&run)
        );
        assert_eq!(
            run.output.status.code(),
            Some(2),
            "flag={flag}\n{}",
            describe(&run)
        );
        assert!(
            String::from_utf8_lossy(&run.output.stderr).contains(HOPS_ENV),
            "flag={flag}: refusal must name {HOPS_ENV}\n{}",
            describe(&run)
        );
    }
}

#[test]
fn native_refuses_python_passthrough_on_a_malformed_hop_count() {
    // RED on main: a malformed marker must fail closed, not be read as 0 (which would let a
    // corrupted chain restart its count and loop again).
    for bad in ["abc", "-1", "1.5"] {
        let run = run_observed_shape("-s", &[(HOPS_ENV, bad)]);
        assert!(run.record.is_none(), "hops={bad:?}\n{}", describe(&run));
        assert_eq!(
            run.output.status.code(),
            Some(2),
            "hops={bad:?}\n{}",
            describe(&run)
        );
    }
}

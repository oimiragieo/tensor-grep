use super::command_for_executable;
use std::ffi::OsStr;

#[test]
fn command_for_executable_never_wraps_batch_shim_in_cmd() {
    // H3 audit: a resolved .bat/.cmd Python interpreter must be launched via Command::new
    // so std applies the CVE-fixed per-arg escaping -- never `cmd /d /c <path>`, which would
    // let a caller-supplied &/|/% re-parse as an injected command (BatBadBut CVE-2024-24576).
    let shim: &OsStr = if cfg!(windows) {
        OsStr::new("C:\\py\\python.bat")
    } else {
        OsStr::new("python.bat")
    };
    let command = command_for_executable(shim);
    let program = command.get_program().to_string_lossy().to_lowercase();
    assert!(
        program.ends_with("python.bat"),
        "expected the shim as the program, got {program}"
    );
    assert_ne!(program, "cmd", "must not wrap the shim in cmd.exe");
}

#[test]
fn command_for_executable_plain_program_untouched() {
    let program = OsStr::new("python");
    assert_eq!(command_for_executable(program).get_program(), program);
}

use std::env;
use std::io;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::OnceLock;

pub(crate) const DEFAULT_SIDECAR_MODULE: &str = "tensor_grep.sidecar";

pub(crate) struct ManagedRuntimePython {
    pub(crate) python: PathBuf,
    pub(crate) native: PathBuf,
}

static MANAGED_RUNTIME_PYTHON: OnceLock<ManagedRuntimePython> = OnceLock::new();

#[derive(Debug, Eq, PartialEq)]
pub(crate) enum PythonLaunchTarget {
    Module(String),
    Script(PathBuf),
}

pub(crate) fn managed_runtime() -> Option<&'static ManagedRuntimePython> {
    MANAGED_RUNTIME_PYTHON.get()
}

pub fn activate_managed_runtime(python: &Path, native: &Path) -> io::Result<()> {
    if !python.is_absolute() || !native.is_absolute() {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "managed runtime executable paths must be absolute",
        ));
    }
    let runtime = ManagedRuntimePython {
        python: python.to_path_buf(),
        native: native.to_path_buf(),
    };
    MANAGED_RUNTIME_PYTHON.set(runtime).map_err(|_| {
        io::Error::new(
            io::ErrorKind::AlreadyExists,
            "managed runtime Python context was already initialized",
        )
    })?;
    Ok(())
}

pub(crate) fn command_for_executable_with_isolation(
    program: &std::ffi::OsStr,
    managed: bool,
) -> Command {
    let mut command = Command::new(program);
    if managed {
        command.arg("-I");
    }
    command
}

pub(crate) fn configure_managed_runtime_command(
    command: &mut Command,
    python: &Path,
    native: &Path,
) {
    command.env("TG_SIDECAR_PYTHON", python);
    command.env("TG_NATIVE_TG_BINARY", native);
    for name in managed_python_environment_overrides() {
        command.env_remove(name);
    }
    command.env("PYTHONNOUSERSITE", "1");
}

pub(crate) fn configure_active_runtime_command(command: &mut Command) -> bool {
    let Some(runtime) = managed_runtime() else {
        return false;
    };
    configure_managed_runtime_command(command, &runtime.python, &runtime.native);
    true
}

fn managed_python_environment_overrides() -> [&'static str; 6] {
    [
        "PYTHONHOME",
        "PYTHONPATH",
        "PYTHONUSERBASE",
        "PYTHONEXECUTABLE",
        "TG_SIDECAR_SCRIPT",
        "TG_SIDECAR_MODULE",
    ]
}

pub(crate) fn resolve_sidecar_target_for(
    managed: bool,
    script: Option<PathBuf>,
    module: Option<String>,
) -> PythonLaunchTarget {
    if managed {
        return PythonLaunchTarget::Module(DEFAULT_SIDECAR_MODULE.to_string());
    }
    if let Some(script) = script {
        return PythonLaunchTarget::Script(script);
    }
    PythonLaunchTarget::Module(module.unwrap_or_else(|| DEFAULT_SIDECAR_MODULE.to_string()))
}

pub(crate) fn resolve_sidecar_target() -> PythonLaunchTarget {
    resolve_sidecar_target_for(
        managed_runtime().is_some(),
        env::var_os("TG_SIDECAR_SCRIPT").map(PathBuf::from),
        env::var("TG_SIDECAR_MODULE").ok(),
    )
}

#[cfg(test)]
mod tests {
    use super::{
        command_for_executable_with_isolation, configure_managed_runtime_command,
        resolve_sidecar_target_for, PythonLaunchTarget, DEFAULT_SIDECAR_MODULE,
    };
    use std::ffi::OsStr;
    use std::fs;
    use std::path::Path;
    use tempfile::tempdir;

    #[test]
    fn managed_command_is_pinned_and_discards_untrusted_environment() {
        let target = resolve_sidecar_target_for(
            true,
            Some(Path::new("hostile.py").to_path_buf()),
            Some("hostile.module".to_string()),
        );
        assert_eq!(
            target,
            PythonLaunchTarget::Module(DEFAULT_SIDECAR_MODULE.to_string())
        );
        let mut command = command_for_executable_with_isolation(OsStr::new("python"), true);
        command.env("PYTHONPATH", "hostile-path");
        command.env("PYTHONHOME", "hostile-home");
        command.env("TG_SIDECAR_SCRIPT", "hostile.py");
        command.env("TG_SIDECAR_MODULE", "hostile.module");
        configure_managed_runtime_command(
            &mut command,
            Path::new("C:\\trusted\\python.exe"),
            Path::new("C:\\trusted\\tg.exe"),
        );
        assert_eq!(command.get_args().next(), Some(OsStr::new("-I")));
        assert!(command
            .get_envs()
            .any(|(key, value)| key == OsStr::new("TG_SIDECAR_PYTHON")
                && value == Some(OsStr::new("C:\\trusted\\python.exe"))));
        assert!(command
            .get_envs()
            .any(|(key, value)| key == OsStr::new("TG_NATIVE_TG_BINARY")
                && value == Some(OsStr::new("C:\\trusted\\tg.exe"))));
        for name in [
            "PYTHONPATH",
            "PYTHONHOME",
            "TG_SIDECAR_SCRIPT",
            "TG_SIDECAR_MODULE",
        ] {
            assert!(command
                .get_envs()
                .any(|(key, value)| key == OsStr::new(name) && value.is_none()));
        }
        assert!(command
            .get_envs()
            .any(|(key, value)| key == OsStr::new("PYTHONNOUSERSITE")
                && value == Some(OsStr::new("1"))));
    }

    #[cfg(windows)]
    #[test]
    fn isolated_python_child_cannot_import_cwd_or_pythonpath_sentinels() {
        let root = tempdir().unwrap();
        let cwd = root.path().join("hostile-cwd");
        let pythonpath = root.path().join("hostile-pythonpath");
        fs::create_dir_all(&cwd).unwrap();
        fs::create_dir_all(&pythonpath).unwrap();
        fs::write(cwd.join("tg_cwd_sentinel.py"), "VALUE = 'cwd'\n").unwrap();
        fs::write(
            pythonpath.join("tg_path_sentinel.py"),
            "VALUE = 'pythonpath'\n",
        )
        .unwrap();

        let python = std::env::var_os("PYTHON").unwrap_or_else(|| "python".into());
        let mut command = command_for_executable_with_isolation(&python, true);
        command
            .current_dir(&cwd)
            .env("PYTHONPATH", &pythonpath)
            .arg("-c")
            .arg(concat!(
                "import importlib.util; ",
                "print(importlib.util.find_spec('tg_cwd_sentinel') is None); ",
                "print(importlib.util.find_spec('tg_path_sentinel') is None)"
            ));
        configure_managed_runtime_command(
            &mut command,
            Path::new(r"C:\trusted\python.exe"),
            Path::new(r"C:\trusted\tg.exe"),
        );
        let output = command.output().expect("spawn isolated Python child");
        assert!(
            output.status.success(),
            "isolated Python exited unsuccessfully: {}",
            String::from_utf8_lossy(&output.stderr)
        );
        assert_eq!(
            String::from_utf8_lossy(&output.stdout)
                .replace("\r\n", "\n")
                .trim(),
            "True\nTrue"
        );
    }
}

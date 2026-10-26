//! Select and activate the trusted Windows managed runtime before CLI routing.
#![cfg(windows)]

use crate::frontdoor_hops::{next_frontdoor_hops, TG_FRONTDOOR_HOPS_ENV};
use crate::managed_runtime_entry::{
    classify_managed_entry, path_key, valid_generation, ManagedEntryRole,
};
use crate::managed_runtime_handoff::{
    fail_stop_unsettled, launch_selected_native, LaunchFailure, UnsettledChild,
};
use crate::managed_runtime_python::activate_managed_runtime;
use crate::managed_runtime_resolver::{read_managed_runtime_at, ManagedRuntime};
use crate::managed_runtime_windows_file::open_file_for_identity;
use crate::managed_runtime_windows_token::current_profile_directory;
use crate::python_sidecar::SidecarError;
use std::env;
use std::ffi::OsString;
use std::fs;
use std::io;
use std::os::windows::io::AsHandle;
use std::path::{Path, PathBuf};

#[derive(Debug)]
pub enum StartupOutcome {
    Standalone,
    Legacy,
    Active(ManagedRuntime),
    Exit(i32),
}

#[derive(Debug)]
pub enum DispatchError {
    Io(io::Error),
    Hop(SidecarError),
    UnsettledChild(Box<UnsettledChild>),
}

impl std::fmt::Display for DispatchError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Io(error) => write!(formatter, "{error}"),
            Self::Hop(error) => write!(formatter, "{}", error.message),
            Self::UnsettledChild(_) => {
                write!(formatter, "termination requested; child settlement unproven")
            }
        }
    }
}

impl std::error::Error for DispatchError {}

impl From<io::Error> for DispatchError {
    fn from(error: io::Error) -> Self {
        Self::Io(error)
    }
}

pub fn prepare_startup(args: &[OsString]) -> Result<StartupOutcome, DispatchError> {
    let current = env::current_exe()?;
    prepare_startup_for(&current, args, current_profile_directory)
}

pub fn prepare_main_startup(args: &[OsString]) -> anyhow::Result<Option<ManagedRuntime>> {
    let outcome = match prepare_startup(args) {
        Ok(outcome) => outcome,
        Err(DispatchError::UnsettledChild(child)) => fail_stop_unsettled(*child),
        Err(error) => return Err(anyhow::Error::new(error)),
    };
    match outcome {
        StartupOutcome::Standalone | StartupOutcome::Legacy => Ok(None),
        StartupOutcome::Active(runtime) => {
            activate_managed_runtime(&runtime.python, &runtime.native)
                .map_err(anyhow::Error::new)?;
            Ok(Some(runtime))
        }
        StartupOutcome::Exit(code) => std::process::exit(code),
    }
}

pub(crate) fn prepare_startup_for(
    current: &Path,
    args: &[OsString],
    discover_profile: impl FnOnce() -> io::Result<PathBuf>,
) -> Result<StartupOutcome, DispatchError> {
    if !has_managed_suffix(current)? {
        return Ok(StartupOutcome::Standalone);
    }
    let lexical = path_key(current)?;
    let profile = discover_profile()?;
    let canonical_profile = fs::canonicalize(&profile)?;
    if path_key(&profile)? != path_key(&canonical_profile)? {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "OS profile directory traverses a reparse point",
        )
        .into());
    }
    let current = fs::canonicalize(current)?;
    let role = classify_managed_entry(&current, &profile)?;
    if role == ManagedEntryRole::Standalone {
        let lexical_role = classify_managed_entry(Path::new(&lexical), &profile)?;
        if lexical_role != ManagedEntryRole::Standalone {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "managed entry canonical path escaped its expected profile location",
            )
            .into());
        }
        return Ok(StartupOutcome::Standalone);
    }
    prepare_from_root(
        &profile.join(".tensor-grep"),
        &current,
        args,
        role,
        env::var_os(TG_FRONTDOOR_HOPS_ENV),
    )
}

fn has_managed_suffix(path: &Path) -> io::Result<bool> {
    let key = path_key(path)?;
    if key.ends_with("\\.tensor-grep\\bin\\tg.exe") {
        return Ok(true);
    }
    let Some((_, generation)) = key.rsplit_once("\\.tensor-grep\\runtimes\\") else {
        return Ok(false);
    };
    let parts = generation.split('\\').collect::<Vec<_>>();
    Ok(parts.len() == 3 && valid_generation(parts[0]) && parts[1] == "bin" && parts[2] == "tg.exe")
}

pub(crate) fn prepare_from_root(
    root: &Path,
    current_executable: &Path,
    args: &[OsString],
    role: ManagedEntryRole,
    hops: Option<OsString>,
) -> Result<StartupOutcome, DispatchError> {
    let Some(runtime) = read_managed_runtime_at(root)? else {
        return missing_selector_outcome(role);
    };
    let current = open_file_for_identity(current_executable)?;
    if runtime.current_executable_is_native(current.as_handle())? {
        return Ok(StartupOutcome::Active(runtime));
    }
    let next_hops = next_frontdoor_hops(hops).map_err(DispatchError::Hop)?;
    match launch_selected_native(runtime, args, next_hops) {
        Ok(exit_code) => Ok(StartupOutcome::Exit(exit_code)),
        Err(LaunchFailure::Io(error)) => Err(DispatchError::Io(error)),
        Err(LaunchFailure::Unsettled(unsettled)) => {
            Err(DispatchError::UnsettledChild(unsettled))
        }
    }
}

fn missing_selector_outcome(role: ManagedEntryRole) -> Result<StartupOutcome, DispatchError> {
    if role == ManagedEntryRole::StableLauncher {
        Ok(StartupOutcome::Legacy)
    } else {
        Err(DispatchError::Io(io::Error::new(
            io::ErrorKind::InvalidData,
            "managed generation has no runtime selector",
        )))
    }
}

#[cfg(test)]
mod tests {
    use super::{prepare_startup_for, StartupOutcome};
    use std::cell::Cell;
    use std::io;
    use std::path::Path;

    #[test]
    fn standalone_does_not_query_profile_directory() {
        let called = Cell::new(false);

        let result = prepare_startup_for(Path::new(r"C:\tools\tg.exe"), &[], || {
            called.set(true);
            Err(io::Error::other("profile query must not run"))
        })
        .unwrap();

        assert!(matches!(result, StartupOutcome::Standalone));
        assert!(!called.get());
    }

    #[test]
    fn outside_profile_managed_shaped_path_remains_standalone() {
        let outside = tempfile::tempdir().unwrap();
        let profile = tempfile::tempdir().unwrap();
        let canonical_profile = std::fs::canonicalize(profile.path()).unwrap();
        let canonical_profile = std::fs::canonicalize(profile.path()).unwrap();
        let executable = outside
            .path()
            .join(".tensor-grep")
            .join("bin")
            .join("tg.exe");
        std::fs::create_dir_all(executable.parent().unwrap()).unwrap();
        std::fs::write(&executable, b"standalone").unwrap();
        let outcome = prepare_startup_for(&executable, &[], || Ok(canonical_profile)).unwrap();
        assert!(matches!(outcome, StartupOutcome::Standalone));
    }
}

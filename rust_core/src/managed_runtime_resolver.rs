//! Windows-only fail-closed reader for the managed runtime selector.
#![cfg(windows)]

use crate::managed_runtime_acl_policy::{
    ancestor_is_trusted, parent_is_trusted, selector_is_trusted,
};
use crate::managed_runtime_acl_reader::read_handle_acl;
use crate::managed_runtime_selection::{decode_selection, ManagedRuntimeSelection};
use crate::managed_runtime_windows_file::{
    final_path_for_handle, open_directory, open_executable, open_selector, same_file_identity,
    OpenedSelectionFile,
};
use crate::managed_runtime_windows_token::{current_profile_directory, current_token_sids};
use std::fs::File;
use std::io;
use std::os::windows::io::AsHandle;
use std::os::windows::io::BorrowedHandle;
use std::path::{Component, Path, PathBuf, Prefix};

const SELECTOR_NAME: &str = "runtime-selection.json";

#[derive(Debug)]
pub struct ManagedRuntime {
    pub selection: ManagedRuntimeSelection,
    pub native: PathBuf,
    pub python: PathBuf,
    _directories: Vec<File>,
    _executables: Vec<File>,
    _selector: OpenedSelectionFile,
}

impl ManagedRuntime {
    /// Compare a separately opened current-executable handle with the pinned native image.
    pub fn current_executable_is_native(&self, current: BorrowedHandle<'_>) -> io::Result<bool> {
        let native = self
            ._executables
            .first()
            .ok_or_else(|| invalid_root("pinned native executable handle is missing"))?;
        same_file_identity(current, native.as_handle())
    }
}

/// Discover the standard per-user managed root and read its trusted selector.
pub fn read_managed_runtime() -> io::Result<Option<ManagedRuntime>> {
    read_managed_runtime_at(&discovered_managed_root()?)
}

fn discovered_managed_root() -> io::Result<PathBuf> {
    Ok(current_profile_directory()?.join(".tensor-grep"))
}

/// Read a selector below a local root while holding every opened ancestor against rename.
pub fn read_managed_runtime_at(root: &Path) -> io::Result<Option<ManagedRuntime>> {
    let (anchor, components) = local_root_parts(root)?;
    let identity = current_token_sids()?;
    let user_sid = identity.user_sid();
    let mut directories = Vec::with_capacity(components.len() + 1);
    let parent = open_directory(&anchor)?;
    if path_key(&final_path_for_handle(parent.as_handle())?) != path_key(&anchor) {
        return Err(invalid_root("drive root final path changed"));
    }
    directories.push(parent);
    let mut current = anchor;

    for (index, component) in components.iter().enumerate() {
        current.push(component);
        let child = open_directory(&current)?;
        let parent_handle = directories
            .last()
            .ok_or_else(|| invalid_root("managed root ancestry is missing"))?;
        let parent_final = final_path_for_handle(parent_handle.as_handle())?;
        let child_final = final_path_for_handle(child.as_handle())?;
        if !is_direct_child(&child_final, &parent_final, Path::new(component)) {
            return Err(invalid_root(
                "directory is not a direct child of its pinned parent",
            ));
        }
        let acl = read_handle_acl(child.as_handle())?;
        let trusted = if index + 1 == components.len() {
            parent_is_trusted(
                &acl.owner,
                Some(&acl.allow_grants),
                user_sid,
                Some(identity.default_owner_sid()),
            )
        } else {
            ancestor_is_trusted(&acl.owner, Some(&acl.allow_grants), user_sid)
        };
        if !trusted {
            return Err(invalid_root("managed root ancestry ACL is untrusted"));
        }
        directories.push(child);
    }

    let root_handle = directories
        .last()
        .ok_or_else(|| invalid_root("managed root is missing"))?;
    let root_final = final_path_for_handle(root_handle.as_handle())?;
    let selector_path = root.join(SELECTOR_NAME);
    let Some(mut selector) = open_selector(&selector_path)? else {
        return Ok(None);
    };
    if !is_direct_child(
        &selector.final_path()?,
        &root_final,
        Path::new(SELECTOR_NAME),
    ) {
        return Err(invalid_root(
            "selector is not below its pinned managed root",
        ));
    }
    let selector_acl = read_handle_acl(selector.as_handle())?;
    if !selector_is_trusted(
        &selector_acl.owner,
        Some(&selector_acl.allow_grants),
        user_sid,
        Some(identity.default_owner_sid()),
    ) {
        return Err(invalid_root("selector ACL is untrusted"));
    }
    let record = selector.read_record()?;
    let selection = decode_selection(&record)
        .map_err(|_| io::Error::new(io::ErrorKind::InvalidData, "runtime selector is invalid"))?;
    let mut selected_directories = Vec::with_capacity(5);
    let mut directory_path = root.to_path_buf();
    for component in ["runtimes", selection.generation()] {
        directory_path.push(component);
        let directory = open_trusted_directory(
            &directory_path,
            directories
                .last()
                .ok_or_else(|| invalid_root("selected root handle is missing"))?,
            component,
            user_sid,
            identity.default_owner_sid(),
        )?;
        directories.push(directory);
    }
    let generation_root = directory_path;
    let bin_path = generation_root.join("bin");
    selected_directories.push(open_trusted_directory(
        &bin_path,
        directories
            .last()
            .ok_or_else(|| invalid_root("generation handle is missing"))?,
        "bin",
        user_sid,
        identity.default_owner_sid(),
    )?);
    let native = open_trusted_executable(
        &bin_path.join("tg.exe"),
        selected_directories
            .last()
            .ok_or_else(|| invalid_root("bin handle is missing"))?,
        "tg.exe",
        user_sid,
        identity.default_owner_sid(),
    )?;
    let venv_path = generation_root.join(".venv");
    selected_directories.push(open_trusted_directory(
        &venv_path,
        directories
            .last()
            .ok_or_else(|| invalid_root("generation handle is missing"))?,
        ".venv",
        user_sid,
        identity.default_owner_sid(),
    )?);
    let scripts_path = venv_path.join("Scripts");
    selected_directories.push(open_trusted_directory(
        &scripts_path,
        selected_directories
            .last()
            .ok_or_else(|| invalid_root("venv handle is missing"))?,
        "Scripts",
        user_sid,
        identity.default_owner_sid(),
    )?);
    let python = open_trusted_executable(
        &scripts_path.join("python.exe"),
        selected_directories
            .last()
            .ok_or_else(|| invalid_root("scripts handle is missing"))?,
        "python.exe",
        user_sid,
        identity.default_owner_sid(),
    )?;
    let native_path = final_path_for_handle(native.as_handle())?;
    let python_path = final_path_for_handle(python.as_handle())?;
    directories.extend(selected_directories);
    Ok(Some(ManagedRuntime {
        selection,
        native: native_path,
        python: python_path,
        _directories: directories,
        _executables: vec![native, python],
        _selector: selector,
    }))
}

fn open_trusted_directory(
    path: &Path,
    parent: &File,
    name: &str,
    user_sid: &str,
    owner_sid: &str,
) -> io::Result<File> {
    let child = open_directory(path)?;
    if !is_direct_child(
        &final_path_for_handle(child.as_handle())?,
        &final_path_for_handle(parent.as_handle())?,
        Path::new(name),
    ) {
        return Err(invalid_root("selected directory is not a direct child"));
    }
    let acl = read_handle_acl(child.as_handle())?;
    if !parent_is_trusted(
        &acl.owner,
        Some(&acl.allow_grants),
        user_sid,
        Some(owner_sid),
    ) {
        return Err(invalid_root("selected directory ACL is untrusted"));
    }
    Ok(child)
}

fn open_trusted_executable(
    path: &Path,
    parent: &File,
    name: &str,
    user_sid: &str,
    owner_sid: &str,
) -> io::Result<File> {
    let executable = open_executable(path)?;
    if !is_direct_child(
        &final_path_for_handle(executable.as_handle())?,
        &final_path_for_handle(parent.as_handle())?,
        Path::new(name),
    ) {
        return Err(invalid_root("selected executable is not a direct child"));
    }
    let acl = read_handle_acl(executable.as_handle())?;
    if !selector_is_trusted(
        &acl.owner,
        Some(&acl.allow_grants),
        user_sid,
        Some(owner_sid),
    ) {
        return Err(invalid_root("selected executable ACL is untrusted"));
    }
    Ok(executable)
}

fn local_root_parts(root: &Path) -> io::Result<(PathBuf, Vec<std::ffi::OsString>)> {
    let text = root
        .to_str()
        .ok_or_else(|| invalid_root("managed root path is not valid Unicode"))?;
    if text.starts_with('\\')
        || text.contains('\0')
        || text
            .replace('/', "\\")
            .split('\\')
            .any(|part| part == "." || part == "..")
    {
        return Err(invalid_root(
            "managed root path is not a local normalized path",
        ));
    }
    let mut parts = root.components();
    let prefix = match parts.next() {
        Some(Component::Prefix(prefix)) if matches!(prefix.kind(), Prefix::Disk(_)) => prefix,
        _ => return Err(invalid_root("managed root must use a local drive path")),
    };
    if parts.next() != Some(Component::RootDir) {
        return Err(invalid_root("managed root must be drive-rooted"));
    }
    let components = parts
        .map(|part| match part {
            Component::Normal(value) if !value.to_string_lossy().contains(':') => {
                Ok(value.to_os_string())
            }
            _ => Err(invalid_root(
                "managed root contains an unsupported path component",
            )),
        })
        .collect::<io::Result<Vec<_>>>()?;
    if components.is_empty() || components.len() > 63 {
        return Err(invalid_root("managed root depth is invalid"));
    }
    let anchor = PathBuf::from(format!("{}\\", prefix.as_os_str().to_string_lossy()));
    Ok((anchor, components))
}

fn path_key(path: &Path) -> String {
    let value = path.to_string_lossy().replace('/', "\\");
    let value = value.strip_prefix("\\\\?\\").unwrap_or(&value);
    value.trim_end_matches('\\').to_ascii_lowercase()
}

fn is_direct_child(child: &Path, parent: &Path, name: &Path) -> bool {
    let expected = format!(
        "{}\\{}",
        path_key(parent),
        name.to_string_lossy().to_ascii_lowercase()
    );
    path_key(child) == expected
}

fn invalid_root(message: &'static str) -> io::Error {
    io::Error::new(io::ErrorKind::PermissionDenied, message)
}

#[cfg(test)]
mod tests {
    use super::discovered_managed_root;
    use crate::managed_runtime_windows_token::current_profile_directory;
    use std::process::Command;

    #[test]
    fn automatic_root_ignores_conflicting_profile_environment_in_child() {
        const PROBE: &str = "TG_CURRENT_PROFILE_CHILD_PROBE";
        if std::env::var_os(PROBE).is_some() {
            assert_eq!(
                discovered_managed_root().unwrap(),
                current_profile_directory().unwrap().join(".tensor-grep")
            );
            return;
        }
        let status = Command::new(std::env::current_exe().unwrap())
            .args([
                "--exact",
                "managed_runtime_resolver::tests::automatic_root_ignores_conflicting_profile_environment_in_child",
            ])
            .env(PROBE, "1")
            .env("USERPROFILE", r"C:\untrusted\profile")
            .env("HOME", r"C:\untrusted\home")
            .status()
            .unwrap();
        assert!(status.success());
    }
}

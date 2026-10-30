//! Read-only verification of explicitly supplied Windows recovery inputs.

#[cfg(windows)]
#[allow(dead_code)]
#[path = "../managed_runtime_windows_file.rs"]
mod windows_file;

#[cfg(all(windows, target_arch = "x86_64"))]
mod windows {
    use super::windows_file;
    use sha2::{Digest, Sha256};
    use std::ffi::OsStr;
    use std::fs::File;
    use std::io::{self, Read};
    use std::os::windows::ffi::OsStrExt;
    use std::os::windows::io::{AsHandle, AsRawHandle};
    use std::path::{Component, Path, PathBuf, Prefix};
    use windows_sys::Win32::Storage::FileSystem::{GetDriveTypeW, GetFileType, FILE_TYPE_DISK};

    const PYTHON_SIZE: u64 = 47_113_364;
    const PYTHON_SHA: &str = "590dfcbe3a34c07301e6332e13e2188b98655cd072b08afa45a7998dc4c22bc5";
    const UV_SIZE: u64 = 15_331_833;
    const UV_SHA: &str = "b2ff80397273fff0e02c20eacae6882834c811827d6fc08f658db156fe754386";
    const DRIVE_FIXED: u32 = 3;

    struct HeldInput {
        file: File,
        _parents: Vec<File>,
    }

    fn refusal(message: &'static str) -> io::Error {
        io::Error::new(io::ErrorKind::InvalidData, message)
    }

    fn path_key(path: &Path) -> io::Result<String> {
        let value = path
            .to_str()
            .ok_or_else(|| refusal("path is not Unicode"))?
            .replace('/', "\\");
        Ok(value
            .strip_prefix("\\\\?\\")
            .unwrap_or(&value)
            .trim_end_matches('\\')
            .to_ascii_lowercase())
    }

    fn safe_component(name: &OsStr) -> bool {
        let Some(name) = name.to_str() else {
            return false;
        };
        if name.ends_with([' ', '.'])
            || name
                .chars()
                .any(|c| c.is_control() || matches!(c, ':' | '<' | '>' | '"' | '|' | '?' | '*'))
        {
            return false;
        }
        let base = name
            .split('.')
            .next()
            .unwrap_or_default()
            .trim_end_matches(' ')
            .to_ascii_uppercase();
        if matches!(
            base.as_str(),
            "CON" | "PRN" | "AUX" | "NUL" | "CONIN$" | "CONOUT$" | "CLOCK$"
        ) {
            return false;
        }
        for prefix in ["COM", "LPT"] {
            if let Some(suffix) = base.strip_prefix(prefix) {
                if matches!(
                    suffix,
                    "0" | "1" | "2" | "3" | "4" | "5" | "6" | "7" | "8" | "9" | "¹" | "²" | "³"
                ) {
                    return false;
                }
            }
        }
        true
    }

    fn local_parts(path: &Path) -> io::Result<(PathBuf, Vec<std::ffi::OsString>)> {
        let text = path
            .to_str()
            .ok_or_else(|| refusal("path is not Unicode"))?;
        if text.starts_with('\\')
            || text.contains('\0')
            || text
                .replace('/', "\\")
                .split('\\')
                .any(|p| p == "." || p == "..")
        {
            return Err(refusal("path is not normalized and local"));
        }
        let mut parts = path.components();
        let prefix = match parts.next() {
            Some(Component::Prefix(prefix)) if matches!(prefix.kind(), Prefix::Disk(_)) => prefix,
            _ => return Err(refusal("path must use a local drive")),
        };
        if parts.next() != Some(Component::RootDir) {
            return Err(refusal("path must be drive-rooted"));
        }
        let components = parts
            .map(|part| match part {
                Component::Normal(name) if safe_component(name) => Ok(name.to_os_string()),
                _ => Err(refusal("unsupported path component")),
            })
            .collect::<io::Result<Vec<_>>>()?;
        if components.is_empty() || components.len() > 64 {
            return Err(refusal("path depth is invalid"));
        }
        let root = PathBuf::from(format!("{}\\", prefix.as_os_str().to_string_lossy()));
        let wide: Vec<u16> = root.as_os_str().encode_wide().chain(Some(0)).collect();
        if unsafe { GetDriveTypeW(wide.as_ptr()) } != DRIVE_FIXED {
            return Err(refusal("path must be on a fixed local drive"));
        }
        Ok((root, components))
    }

    fn open_input(path: &Path) -> io::Result<HeldInput> {
        let (mut current, parts) = local_parts(path)?;
        let mut parents = vec![windows_file::open_directory(&current)?];
        let parent_final = windows_file::final_path_for_handle(parents[0].as_handle())?;
        if path_key(&parent_final)? != path_key(&current)? {
            return Err(refusal("drive root resolved to a different location"));
        }
        for name in &parts[..parts.len() - 1] {
            current.push(name);
            let directory = windows_file::open_directory(&current)?;
            if !windows_file::is_held_direct_child(
                parents.last().unwrap().as_handle(),
                directory.as_handle(),
                &current,
            )? {
                return Err(refusal("directory changed during validation"));
            }
            parents.push(directory);
        }
        let name = parts.last().ok_or_else(|| refusal("missing file name"))?;
        current.push(name);
        let file = windows_file::open_executable(&current)?;
        if unsafe { GetFileType(file.as_raw_handle()) } != FILE_TYPE_DISK
            || !windows_file::is_held_direct_child(
                parents.last().unwrap().as_handle(),
                file.as_handle(),
                &current,
            )?
        {
            return Err(refusal("input is not a local direct-child file"));
        }
        Ok(HeldInput {
            file,
            _parents: parents,
        })
    }

    fn verify_input(
        input: &mut HeldInput,
        expected_size: u64,
        expected_sha: &str,
    ) -> io::Result<()> {
        if input.file.metadata()?.len() != expected_size {
            return Err(refusal("input size mismatch"));
        }
        let mut digest = Sha256::new();
        let mut total = 0u64;
        let mut limited = (&mut input.file).take(expected_size + 1);
        let mut buffer = [0u8; 64 * 1024];
        loop {
            let count = limited.read(&mut buffer)?;
            if count == 0 {
                break;
            }
            total += count as u64;
            digest.update(&buffer[..count]);
        }
        if total != expected_size || format!("{:x}", digest.finalize()) != expected_sha {
            return Err(refusal("input checksum mismatch"));
        }
        Ok(())
    }

    pub fn run(args: Vec<std::ffi::OsString>) -> Result<(), &'static str> {
        let mut archive = None;
        let mut wheel = None;
        let mut args = args.into_iter();
        while let Some(flag) = args.next() {
            let slot = if flag == "--python-archive" {
                &mut archive
            } else if flag == "--uv-wheel" {
                &mut wheel
            } else {
                return Err("expected --python-archive and --uv-wheel");
            };
            if slot.is_some() {
                return Err("duplicate input option");
            }
            *slot = Some(PathBuf::from(args.next().ok_or("missing input path")?));
        }
        let archive = archive.ok_or("missing Python archive")?;
        let wheel = wheel.ok_or("missing uv wheel")?;
        let mut archive =
            open_input(&archive).map_err(|_| "Python archive cannot be opened safely")?;
        let mut wheel = open_input(&wheel).map_err(|_| "uv wheel cannot be opened safely")?;
        verify_input(&mut archive, PYTHON_SIZE, PYTHON_SHA)
            .map_err(|_| "Python archive integrity check failed")?;
        verify_input(&mut wheel, UV_SIZE, UV_SHA).map_err(|_| "uv wheel integrity check failed")?;
        println!("Recovery inputs match the pinned Python and uv artifacts. Recovery has not been performed; inputs must be reverified before use.");
        Ok(())
    }

    #[cfg(test)]
    mod tests {
        use super::{local_parts, open_input, run, verify_input};
        use sha2::{Digest, Sha256};
        use std::fs;
        use std::os::windows::fs::OpenOptionsExt;
        use std::os::windows::process::CommandExt;
        use std::path::Path;

        #[test]
        fn ordinary_local_file_is_readable_and_verified() {
            let dir = tempfile::tempdir().unwrap();
            let file = dir.path().join("artifact");
            fs::write(&file, b"trusted fixture").unwrap();
            let mut input = open_input(&file).unwrap();
            let digest = format!("{:x}", Sha256::digest(b"trusted fixture"));
            verify_input(&mut input, 15, &digest).unwrap();
        }

        #[test]
        fn existing_file_with_short_directory_alias_is_readable() {
            use std::os::windows::ffi::{OsStrExt, OsStringExt};
            use std::os::windows::io::AsHandle;
            use std::path::PathBuf;
            use windows_sys::Win32::Storage::FileSystem::GetShortPathNameW;

            let long = PathBuf::from(std::env::var_os("ProgramFiles").unwrap_or_default())
                .join("Git/bin/git.exe");
            if !long.is_absolute() || !long.is_file() {
                eprintln!("short-name control unavailable: Git fixture is absent");
                return;
            }
            let wide: Vec<u16> = long.as_os_str().encode_wide().chain(Some(0)).collect();
            let mut buffer = vec![0u16; 32_768];
            let count = unsafe {
                GetShortPathNameW(wide.as_ptr(), buffer.as_mut_ptr(), buffer.len() as u32)
            } as usize;
            assert!(count > 0 && count < buffer.len(), "short path query failed");
            let short = PathBuf::from(std::ffi::OsString::from_wide(&buffer[..count]));
            if super::path_key(short.parent().unwrap()).unwrap()
                == super::path_key(long.parent().unwrap()).unwrap()
            {
                eprintln!(
                    "short-ancestor control unavailable: filesystem supplied no ancestor alias"
                );
                return;
            }
            let long_input = open_input(&long).unwrap();
            let short_input = open_input(&short).unwrap();
            assert!(super::windows_file::same_file_identity(
                long_input.file.as_handle(),
                short_input.file.as_handle()
            )
            .unwrap());
            eprintln!("preflight short ancestor alias verified");
        }

        #[test]
        fn hardlinked_input_is_refused_without_changing_either_name() {
            let dir = tempfile::tempdir().unwrap();
            let file = dir.path().join("artifact");
            let alias = dir.path().join("alias");
            fs::write(&file, b"fixture").unwrap();
            fs::hard_link(&file, &alias).unwrap();
            let error = open_input(&file)
                .err()
                .expect("hardlinked input was accepted");
            assert_eq!(
                error.to_string(),
                "managed runtime file must have exactly one hard link"
            );
            assert_eq!(fs::read(&file).unwrap(), b"fixture");
            assert_eq!(fs::read(&alias).unwrap(), b"fixture");
        }

        #[test]
        fn pinned_input_refuses_writes_and_rename() {
            let dir = tempfile::tempdir().unwrap();
            let file = dir.path().join("artifact");
            fs::write(&file, b"fixture").unwrap();
            let _held = open_input(&file).unwrap();
            assert!(fs::write(&file, b"changed").is_err());
            assert!(fs::rename(&file, dir.path().join("other")).is_err());
            assert!(fs::rename(dir.path(), dir.path().with_extension("moved")).is_err());
        }

        #[test]
        fn existing_writer_is_refused_then_file_is_readable() {
            let dir = tempfile::tempdir().unwrap();
            let file = dir.path().join("artifact");
            fs::write(&file, b"fixture").unwrap();
            let writer = fs::OpenOptions::new()
                .write(true)
                .share_mode(7)
                .open(&file)
                .unwrap();
            let error = open_input(&file).err().expect("writer was accepted");
            assert_eq!(error.raw_os_error(), Some(32));
            drop(writer);
            assert!(open_input(&file).is_ok());
        }

        #[test]
        fn parent_junction_is_refused_without_changing_target() {
            let dir = tempfile::tempdir().unwrap();
            let target = dir.path().join("target");
            let junction = dir.path().join("junction");
            fs::create_dir(&target).unwrap();
            fs::write(target.join("artifact"), b"fixture").unwrap();
            let cmd = Path::new(&std::env::var_os("SystemRoot").unwrap())
                .join("System32")
                .join("cmd.exe");
            let result = std::process::Command::new(cmd)
                .creation_flags(0x08000000)
                .args(["/d", "/c", "mklink", "/J"])
                .arg(&junction)
                .arg(&target)
                .output()
                .unwrap();
            assert!(
                result.status.success(),
                "junction creation failed: {:?}",
                result.stderr
            );
            assert_eq!(fs::read(junction.join("artifact")).unwrap(), b"fixture");
            let error = open_input(&junction.join("artifact"))
                .err()
                .expect("junction accepted");
            assert_eq!(
                error.to_string(),
                "opened path has an unexpected type or is a reparse point"
            );
            assert_eq!(fs::read(target.join("artifact")).unwrap(), b"fixture");
            fs::remove_dir(&junction).unwrap();
        }

        #[test]
        fn size_and_hash_mismatches_are_refused() {
            let dir = tempfile::tempdir().unwrap();
            let file = dir.path().join("artifact");
            fs::write(&file, b"fixture").unwrap();
            assert_eq!(
                verify_input(&mut open_input(&file).unwrap(), 6, "bad")
                    .unwrap_err()
                    .to_string(),
                "input size mismatch"
            );
            assert_eq!(
                verify_input(&mut open_input(&file).unwrap(), 8, "bad")
                    .unwrap_err()
                    .to_string(),
                "input size mismatch"
            );
            assert_eq!(
                verify_input(&mut open_input(&file).unwrap(), 7, "bad")
                    .unwrap_err()
                    .to_string(),
                "input checksum mismatch"
            );
        }

        #[test]
        fn rejects_nonlocal_ambiguous_or_stream_paths() {
            for path in [
                r"\\server\share\file",
                r"\\?\C:\file",
                r"C:relative",
                r"C:\a\..\file",
                r"C:\a\.\file",
                r"C:\file:stream",
            ] {
                assert!(local_parts(Path::new(path)).is_err(), "accepted {path}");
            }
        }

        #[test]
        fn rejects_dos_device_names_before_open() {
            for name in [
                "NUL",
                "con.txt",
                "COM1",
                "lPt9.tar",
                "COM¹",
                "LPT².log",
                "CONIN$",
                "CONOUT$",
                "AUX .txt",
                "plain.",
            ] {
                let path = format!("C:\\fixture\\{name}");
                assert!(local_parts(Path::new(&path)).is_err(), "accepted {path}");
            }
            assert!(local_parts(Path::new(r"C:\fixture\normal-file.whl")).is_ok());
        }

        #[test]
        fn missing_option_is_rejected_before_opening_any_input() {
            let args = ["--python-archive", "C:\\definitely-missing\\artifact"]
                .into_iter()
                .map(Into::into)
                .collect();
            assert_eq!(run(args), Err("missing uv wheel"));
        }
    }
}

fn main() {
    let args: Vec<_> = std::env::args_os().skip(1).collect();
    if args.len() == 1 && (args[0] == "--help" || args[0] == "-h") {
        println!("Usage: tg-recovery-preflight --python-archive ABSOLUTE_PATH --uv-wheel ABSOLUTE_PATH\nVerifies pinned local inputs only; does not perform recovery.");
        return;
    }
    #[cfg(all(windows, target_arch = "x86_64"))]
    {
        if !cfg!(target_feature = "crt-static") {
            eprintln!("recovery preflight: static CRT required");
            std::process::exit(1);
        }
        if let Err(message) = windows::run(args) {
            eprintln!("recovery preflight: {message}");
            std::process::exit(1);
        }
    }
    #[cfg(not(all(windows, target_arch = "x86_64")))]
    {
        let _ = args;
        eprintln!("recovery preflight requires Windows x86-64");
        std::process::exit(1);
    }
}

use super::{windows, windows_file};
use sha2::{Digest, Sha256};
use std::collections::BTreeSet;
use std::fs::{self, File};
use std::io::{self, Read};
use std::os::windows::io::{AsHandle, AsRawHandle};
use std::path::Path;
use windows_sys::Win32::Storage::FileSystem::{GetFileType, FILE_TYPE_DISK};

#[derive(Clone, Copy)]
struct Expected<'a> {
    files: usize,
    dirs: usize,
    bytes: u64,
    max_file: u64,
    max_depth: usize,
    max_path: usize,
    digest: &'a str,
}

const PYTHON: Expected<'static> = Expected {
    files: 3388,
    dirs: 259,
    bytes: 149_886_264,
    max_file: 22_695_936,
    max_depth: 10,
    max_path: 105,
    digest: "95ab1429eb4b698ba8577bcd7e9f803fff9ba9a24f5702a989a5601449dc2a5e",
};

fn refuse(message: &'static str) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, message)
}

fn pin_root(path: &Path) -> io::Result<Vec<File>> {
    let (mut current, parts) = windows::local_parts(path)?;
    let mut pins = vec![windows_file::open_directory(&current)?];
    let root_final = windows_file::final_path_for_handle(pins[0].as_handle())?;
    if windows::path_key(&root_final)? != windows::path_key(&current)? {
        return Err(refuse("drive root changed"));
    }
    for part in parts {
        current.push(&part);
        let file = windows_file::open_directory(&current)?;
        if !windows_file::is_held_direct_child(
            pins.last().unwrap().as_handle(),
            file.as_handle(),
            &current,
        )? {
            return Err(refuse("directory changed"));
        }
        pins.push(file);
    }
    Ok(pins)
}

fn verify_expected(root: &Path, expected: &Expected<'_>) -> io::Result<()> {
    verify_with_hook(root, expected, |_| Ok(()))
}

fn verify_with_hook(
    root: &Path,
    expected: &Expected<'_>,
    mut after_directory: impl FnMut(&Path) -> io::Result<()>,
) -> io::Result<()> {
    let mut pins = pin_root(root)?;
    let mut pending = vec![(root.to_path_buf(), String::new(), pins.len() - 1, 0usize)];
    let mut seen = BTreeSet::new();
    let mut directories = BTreeSet::new();
    let mut required_directories = BTreeSet::new();
    let mut records = Vec::new();
    let mut visited = Vec::new();
    let mut total = 0u64;
    while let Some((directory, prefix, parent, depth)) = pending.pop() {
        for entry in fs::read_dir(&directory)? {
            let entry = entry?;
            let name = entry.file_name();
            let text = name.to_str().ok_or_else(|| refuse("non-Unicode name"))?;
            if !text.is_ascii() || !windows::safe_component(&name) {
                return Err(refuse("unsafe name"));
            }
            let relative = if prefix.is_empty() {
                text.to_owned()
            } else {
                format!("{prefix}/{text}")
            };
            if depth + 1 > expected.max_depth
                || relative.len() > expected.max_path
                || !seen.insert(relative.to_ascii_lowercase())
                || seen.len() > expected.files + expected.dirs
            {
                return Err(refuse("tree name or count limit exceeded"));
            }
            let kind = entry.file_type()?;
            if kind.is_dir() && !kind.is_symlink() {
                if directories.len() >= expected.dirs {
                    return Err(refuse("directory count exceeded"));
                }
                let file = windows_file::open_directory(&entry.path())?;
                if unsafe { GetFileType(file.as_raw_handle()) } != FILE_TYPE_DISK
                    || !windows_file::is_held_direct_child(
                        pins[parent].as_handle(),
                        file.as_handle(),
                        &entry.path(),
                    )?
                {
                    return Err(refuse("unsafe directory"));
                }
                directories.insert(relative.clone());
                pending.push((entry.path(), relative, pins.len(), depth + 1));
                pins.push(file);
            } else if kind.is_file() && !kind.is_symlink() {
                if records.len() >= expected.files {
                    return Err(refuse("file count exceeded"));
                }
                let mut file = windows_file::open_executable(&entry.path())?;
                if unsafe { GetFileType(file.as_raw_handle()) } != FILE_TYPE_DISK
                    || !windows_file::is_held_direct_child(
                        pins[parent].as_handle(),
                        file.as_handle(),
                        &entry.path(),
                    )?
                {
                    return Err(refuse("unsafe file"));
                }
                let size = file.metadata()?.len();
                if size > expected.max_file || size > expected.bytes.saturating_sub(total) {
                    return Err(refuse("file size exceeded"));
                }
                let mut file_hash = Sha256::new();
                let mut read = 0u64;
                let mut limited = (&mut file).take(size + 1);
                let mut buffer = [0u8; 64 * 1024];
                loop {
                    let count = limited.read(&mut buffer)?;
                    if count == 0 {
                        break;
                    }
                    read += count as u64;
                    file_hash.update(&buffer[..count]);
                }
                if read != size {
                    return Err(refuse("file changed size"));
                }
                total += size;
                let mut ancestor = relative.as_str();
                while let Some((parent, _)) = ancestor.rsplit_once('/') {
                    required_directories.insert(parent.to_owned());
                    ancestor = parent;
                }
                records.push((relative, size, format!("{:x}", file_hash.finalize())));
                pins.push(file);
            } else {
                return Err(refuse("unsupported tree entry"));
            }
        }
        visited.push((directory.clone(), prefix));
        after_directory(&directory)?;
    }
    if records.len() != expected.files
        || directories.len() != expected.dirs
        || directories != required_directories
        || total != expected.bytes
    {
        return Err(refuse("tree population mismatch"));
    }
    let mut seen_again = BTreeSet::new();
    for (directory, prefix) in visited {
        for entry in fs::read_dir(directory)? {
            let name = entry?.file_name();
            let text = name.to_str().ok_or_else(|| refuse("non-Unicode name"))?;
            let relative = if prefix.is_empty() {
                text.to_owned()
            } else {
                format!("{prefix}/{text}")
            };
            if !text.is_ascii()
                || !windows::safe_component(&name)
                || !seen_again.insert(relative.to_ascii_lowercase())
                || seen_again.len() > seen.len()
            {
                return Err(refuse("tree changed during verification"));
            }
        }
    }
    if seen_again != seen {
        return Err(refuse("tree changed during verification"));
    }
    records.sort_unstable_by(|left, right| left.0.cmp(&right.0));
    let mut digest = Sha256::new();
    for (name, size, hash) in records {
        digest.update(name.as_bytes());
        digest.update(b"\0");
        digest.update(size.to_string().as_bytes());
        digest.update(b"\0");
        digest.update(hash.as_bytes());
        digest.update(b"\n");
    }
    if format!("{:x}", digest.finalize()) != expected.digest {
        return Err(refuse("tree digest mismatch"));
    }
    Ok(())
}

pub(super) fn verify(root: &Path) -> io::Result<()> {
    verify_expected(root, &PYTHON)
}

#[cfg(test)]
mod tests {
    use super::{pin_root, verify_expected, verify_with_hook, Expected};
    use std::fs::{self, OpenOptions};
    use std::os::windows::fs::OpenOptionsExt;
    use std::os::windows::process::CommandExt;
    use std::path::Path;

    const FIXTURE: Expected<'static> = Expected {
        files: 1,
        dirs: 1,
        bytes: 5,
        max_file: 5,
        max_depth: 2,
        max_path: 12,
        digest: "5a3c8dcc33029de3666d76af511cd76a7ee8adbe4ff8aff047b93c55dcbe030b",
    };

    fn fixture() -> tempfile::TempDir {
        let root = tempfile::tempdir().unwrap();
        fs::create_dir(root.path().join("python")).unwrap();
        fs::write(root.path().join("python/a.txt"), b"alpha").unwrap();
        root
    }

    #[test]
    fn matches_known_fixture() {
        let root = fixture();
        verify_expected(root.path(), &FIXTURE).unwrap();
    }

    #[test]
    fn sorts_independently_of_enumeration_order() {
        let root = fixture();
        fs::write(root.path().join("python/b.txt"), b"beta").unwrap();
        let expected = Expected {
            files: 2,
            bytes: 9,
            digest: "8fa7a87911bdabe4356dff4ff63030124e965c3634c5b65cae520220bd9454df",
            ..FIXTURE
        };
        verify_expected(root.path(), &expected).unwrap();
    }

    #[test]
    fn verifies_files_under_distinct_held_parent_directories() {
        let root = fixture();
        fs::create_dir_all(root.path().join("else/sub")).unwrap();
        fs::write(root.path().join("else/sub/b"), b"beta").unwrap();
        let expected = Expected {
            files: 2,
            dirs: 3,
            bytes: 9,
            max_depth: 3,
            digest: "1029f5ffe7b1ff5e656dd525e3e1899ac9156e08fd36933cc78a729d5f1e73e8",
            ..FIXTURE
        };
        verify_expected(root.path(), &expected).unwrap();
    }

    #[test]
    fn verifies_tree_via_short_directory_alias_when_available() {
        use std::os::windows::ffi::{OsStrExt, OsStringExt};
        use windows_sys::Win32::Storage::FileSystem::GetShortPathNameW;

        let root = fixture();
        let wide: Vec<u16> = root
            .path()
            .as_os_str()
            .encode_wide()
            .chain(Some(0))
            .collect();
        let mut buffer = vec![0u16; 32768];
        let length =
            unsafe { GetShortPathNameW(wide.as_ptr(), buffer.as_mut_ptr(), buffer.len() as u32) }
                as usize;
        if length == 0 || length >= buffer.len() {
            eprintln!("tree short-directory alias unavailable");
            return;
        }
        let short = std::path::PathBuf::from(std::ffi::OsString::from_wide(&buffer[..length]));
        if short
            .to_string_lossy()
            .eq_ignore_ascii_case(&root.path().to_string_lossy())
        {
            eprintln!("tree short-directory alias unavailable");
            return;
        }
        verify_expected(&short, &FIXTURE).unwrap();
        eprintln!("tree short-directory alias verified");
    }

    #[test]
    fn refuses_changed_missing_and_extra_files() {
        let root = fixture();
        fs::write(root.path().join("python/a.txt"), b"omega").unwrap();
        assert_eq!(
            verify_expected(root.path(), &FIXTURE)
                .unwrap_err()
                .to_string(),
            "tree digest mismatch"
        );
        fs::remove_file(root.path().join("python/a.txt")).unwrap();
        assert_eq!(
            verify_expected(root.path(), &FIXTURE)
                .unwrap_err()
                .to_string(),
            "tree population mismatch"
        );
        fs::write(root.path().join("python/a.txt"), b"alpha").unwrap();
        fs::write(root.path().join("python/b.txt"), b"beta").unwrap();
        assert!(verify_expected(root.path(), &FIXTURE).is_err());
    }

    #[test]
    fn refuses_empty_directory_and_size_limits() {
        let root = fixture();
        fs::create_dir(root.path().join("empty")).unwrap();
        let expected = Expected { dirs: 2, ..FIXTURE };
        assert_eq!(
            verify_expected(root.path(), &expected)
                .unwrap_err()
                .to_string(),
            "tree population mismatch"
        );
        fs::remove_dir(root.path().join("empty")).unwrap();
        let expected = Expected {
            max_file: 4,
            ..FIXTURE
        };
        assert_eq!(
            verify_expected(root.path(), &expected)
                .unwrap_err()
                .to_string(),
            "file size exceeded"
        );
        let expected = Expected {
            bytes: 4,
            ..FIXTURE
        };
        assert_eq!(
            verify_expected(root.path(), &expected)
                .unwrap_err()
                .to_string(),
            "file size exceeded"
        );
    }

    #[test]
    fn refuses_path_depth_length_and_non_ascii_names() {
        let root = fixture();
        let depth = Expected {
            max_depth: 1,
            ..FIXTURE
        };
        assert_eq!(
            verify_expected(root.path(), &depth)
                .unwrap_err()
                .to_string(),
            "tree name or count limit exceeded"
        );
        let length = Expected {
            max_path: 11,
            ..FIXTURE
        };
        assert_eq!(
            verify_expected(root.path(), &length)
                .unwrap_err()
                .to_string(),
            "tree name or count limit exceeded"
        );
        fs::write(root.path().join("python/é.txt"), b"x").unwrap();
        let extended = Expected {
            files: 2,
            bytes: 6,
            max_path: 20,
            ..FIXTURE
        };
        assert_eq!(
            verify_expected(root.path(), &extended)
                .unwrap_err()
                .to_string(),
            "unsafe name"
        );
    }

    #[test]
    fn refuses_hardlinks_and_active_writers() {
        let root = fixture();
        let outside = tempfile::tempdir().unwrap();
        fs::hard_link(
            root.path().join("python/a.txt"),
            outside.path().join("alias"),
        )
        .unwrap();
        assert!(verify_expected(root.path(), &FIXTURE)
            .unwrap_err()
            .to_string()
            .contains("exactly one hard link"));
        fs::remove_file(outside.path().join("alias")).unwrap();
        let writer = OpenOptions::new()
            .write(true)
            .share_mode(0)
            .open(root.path().join("python/a.txt"))
            .unwrap();
        assert_eq!(
            verify_expected(root.path(), &FIXTURE)
                .unwrap_err()
                .raw_os_error(),
            Some(32)
        );
        drop(writer);
        verify_expected(root.path(), &FIXTURE).unwrap();
    }

    #[test]
    fn pins_root_and_refuses_parent_junction() {
        let parent = tempfile::tempdir().unwrap();
        let target = parent.path().join("target");
        let moved = parent.path().join("moved");
        let junction = parent.path().join("junction");
        fs::create_dir(&target).unwrap();
        let pins = pin_root(&target).unwrap();
        assert!(fs::rename(&target, &moved).is_err());
        drop(pins);
        fs::rename(&target, &moved).unwrap();
        fs::rename(&moved, &target).unwrap();
        let cmd = Path::new(&std::env::var_os("SystemRoot").unwrap())
            .join("System32")
            .join("cmd.exe");
        let output = std::process::Command::new(cmd)
            .creation_flags(0x08000000)
            .args(["/d", "/c", "mklink", "/J"])
            .arg(&junction)
            .arg(&target)
            .output()
            .unwrap();
        assert!(output.status.success());
        assert!(pin_root(&junction).is_err());
        fs::remove_dir(junction).unwrap();
    }

    #[test]
    fn refuses_new_file_added_after_directory_enumeration() {
        let root = fixture();
        let python = root.path().join("python");
        let result = verify_with_hook(root.path(), &FIXTURE, |directory| {
            if directory == python {
                fs::write(python.join("late.pth"), b"late")?;
            }
            Ok(())
        });
        assert!(result.is_err());
    }
}

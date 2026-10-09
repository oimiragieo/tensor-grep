//! Windows loader regression using only pinned ONNX and genuine System32 code.
#![cfg(windows)]

use sha2::{Digest, Sha256};
use std::ffi::{c_void, OsString};
use std::os::windows::ffi::{OsStrExt, OsStringExt};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

#[link(name = "kernel32")]
unsafe extern "system" {
    fn GetSystemDirectoryW(buffer: *mut u16, size: u32) -> u32;
    fn LoadLibraryExW(path: *const u16, reserved: *mut c_void, flags: u32) -> *mut c_void;
    fn FreeLibrary(module: *mut c_void) -> i32;
}

fn system_directory() -> PathBuf {
    let mut buffer = vec![0u16; 32768];
    let length = unsafe { GetSystemDirectoryW(buffer.as_mut_ptr(), buffer.len() as u32) };
    assert!(length > 0 && (length as usize) < buffer.len());
    PathBuf::from(OsString::from_wide(&buffer[..length as usize]))
}

fn patch_import(bytes: &mut [u8]) {
    fn u16_at(b: &[u8], n: usize) -> usize {
        u16::from_le_bytes(b[n..n + 2].try_into().unwrap()) as usize
    }
    fn u32_at(b: &[u8], n: usize) -> usize {
        u32::from_le_bytes(b[n..n + 4].try_into().unwrap()) as usize
    }
    assert_eq!(&bytes[..2], b"MZ");
    let pe = u32_at(bytes, 0x3c);
    assert_eq!(&bytes[pe..pe + 4], b"PE\0\0");
    let optional = pe + 24;
    assert_eq!(u16_at(bytes, optional), 0x20b);
    let sections = optional + u16_at(bytes, pe + 20);
    let count = u16_at(bytes, pe + 6);
    let offset = |rva: usize| -> usize {
        for index in 0..count {
            let section = sections + index * 40;
            let address = u32_at(bytes, section + 12);
            let size = u32_at(bytes, section + 16);
            if rva >= address && rva - address < size {
                return u32_at(bytes, section + 20) + rva - address;
            }
        }
        panic!("import RVA is outside file-backed sections");
    };
    let descriptors = offset(u32_at(bytes, optional + 120));
    let size = u32_at(bytes, optional + 124);
    assert!(size > 0 && size <= 4096 * 20);
    let mut names = Vec::new();
    for index in 0..size / 20 {
        let descriptor = descriptors + index * 20;
        if bytes[descriptor..descriptor + 20].iter().all(|&v| v == 0) {
            break;
        }
        let name = offset(u32_at(bytes, descriptor + 12));
        if bytes.get(name..name + 13) == Some(b"MSVCP140.dll\0") {
            names.push(name);
        }
    }
    assert_eq!(names.len(), 1, "expected one exact MSVCP import descriptor");
    bytes[names[0]..names[0] + 12].copy_from_slice(b"TGDEP140.dll");
}

#[test]
fn windows_loader_probe_child() {
    let Ok(mode) = std::env::var("TG_LOADER_TEST_MODE") else {
        return;
    };
    let runtime = PathBuf::from(std::env::var_os("TG_LOADER_TEST_RUNTIME").unwrap());
    if mode == "safe" {
        let error = crate::cross_encoder_runtime::preload(&runtime)
            .err()
            .expect("cwd dependency loaded");
        assert!(error.to_string().contains("safe ONNX loader failed"));
    } else {
        assert_eq!(mode, "legacy");
        let path: Vec<u16> = runtime.as_os_str().encode_wide().chain(Some(0)).collect();
        let handle = unsafe { LoadLibraryExW(path.as_ptr(), std::ptr::null_mut(), 0) };
        assert!(
            !handle.is_null(),
            "legacy positive control: {}",
            std::io::Error::last_os_error()
        );
        assert_ne!(unsafe { FreeLibrary(handle) }, 0);
    }
}

#[test]
fn windows_loader_excludes_fixture_cwd_dependency() {
    let Some(assets) = std::env::var_os("TG_CROSS_ENCODER_DIR") else {
        eprintln!("SKIP loader proof: pinned runtime assets unavailable");
        return;
    };
    let fixture = tempfile::tempdir().unwrap();
    let libraries = fixture.path().join("libraries");
    std::fs::create_dir(&libraries).unwrap();
    let system = system_directory();
    assert!(!system.join("TGDEP140.dll").exists());
    std::fs::copy(
        system.join("MSVCP140.dll"),
        fixture.path().join("TGDEP140.dll"),
    )
    .unwrap();
    for &(name, size, checksum) in crate::cross_encoder_pins::LIBRARIES {
        let mut bytes = std::fs::read(Path::new(&assets).join(name)).unwrap();
        assert_eq!(bytes.len(), size);
        assert_eq!(format!("{:x}", Sha256::digest(&bytes)), checksum);
        if name == "onnxruntime.dll" {
            patch_import(&mut bytes);
        }
        std::fs::write(libraries.join(name), bytes).unwrap();
    }
    // Private helper intentionally bypasses public SHA checks; only the import name changes.
    for mode in ["safe", "legacy"] {
        let mut child = Command::new(std::env::current_exe().unwrap())
            .args([
                "--exact",
                "cross_encoder_loader_tests::windows_loader_probe_child",
                "--nocapture",
            ])
            .env("TG_LOADER_TEST_MODE", mode)
            .env("TG_LOADER_TEST_RUNTIME", libraries.join("onnxruntime.dll"))
            .current_dir(fixture.path())
            .stdin(Stdio::null())
            .spawn()
            .unwrap();
        let started = Instant::now();
        loop {
            if let Some(status) = child.try_wait().unwrap() {
                assert!(status.success(), "{mode} loader child failed: {status}");
                break;
            }
            if started.elapsed() >= Duration::from_secs(120) {
                child.kill().unwrap();
                child.wait().unwrap();
                panic!("{mode} loader child exceeded external deadline");
            }
            std::thread::sleep(Duration::from_millis(20));
        }
    }
}

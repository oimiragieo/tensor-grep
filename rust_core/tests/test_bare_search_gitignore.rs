use std::fs;
use std::process::Command;

use tempfile::tempdir;

fn tg() -> Command {
    Command::new(env!("CARGO_BIN_EXE_tg"))
}

#[test]
fn gitignore_bare_search() {
    let dir = tempdir().unwrap();
    fs::write(dir.path().join(".gitignore"), "ignored_dir/\n").unwrap();
    fs::create_dir(dir.path().join("ignored_dir")).unwrap();
    fs::write(
        dir.path().join("ignored_dir").join("secret.py"),
        "TOKEN = 'needle'\n",
    )
    .unwrap();
    fs::write(dir.path().join("visible.py"), "TOKEN = 'needle'\n").unwrap();

    // Bare `tg <pattern> .` must respect `.gitignore` on both default and `--cpu` paths.
    for extra in [&[][..], &["--cpu"][..]] {
        let mut args = vec!["needle", "."];
        args.extend_from_slice(extra);
        let output = tg().current_dir(dir.path()).args(&args).output().unwrap();
        let stdout = String::from_utf8_lossy(&output.stdout);
        let stderr = String::from_utf8_lossy(&output.stderr);
        if extra.contains(&"--cpu") && stderr.contains("requires the native extension on Windows") {
            continue;
        }
        assert!(
            output.status.success(),
            "args={args:?} status={:?}\nstdout={stdout}\nstderr={stderr}",
            output.status.code()
        );
        assert!(
            stdout.contains("visible.py"),
            "args={args:?} expected visible.py in stdout: {stdout}"
        );
        assert!(
            !stdout.contains("secret.py"),
            "args={args:?} must exclude .gitignored ignored_dir/secret.py: {stdout}"
        );
    }

    // Passing `--no-ignore` must include `ignored_dir/secret.py`.
    for extra in [&["--no-ignore"][..], &["--cpu", "--no-ignore"][..]] {
        let mut args = vec!["needle", "."];
        args.extend_from_slice(extra);
        let output = tg().current_dir(dir.path()).args(&args).output().unwrap();
        let stdout = String::from_utf8_lossy(&output.stdout);
        let stderr = String::from_utf8_lossy(&output.stderr);
        if extra.contains(&"--cpu") && stderr.contains("requires the native extension on Windows") {
            continue;
        }
        assert!(
            output.status.success(),
            "args={args:?} status={:?}\nstdout={stdout}\nstderr={stderr}",
            output.status.code()
        );
        assert!(
            stdout.contains("visible.py") && stdout.contains("secret.py"),
            "args={args:?} expected both visible.py and secret.py in stdout: {stdout}"
        );
    }

    // Passing `--no-ignore --ignore` must honor the last flag (`--ignore`) and exclude secret.py.
    for extra in [
        &["--no-ignore", "--ignore"][..],
        &["--cpu", "--no-ignore", "--ignore"][..],
    ] {
        let mut args = vec!["needle", "."];
        args.extend_from_slice(extra);
        let output = tg().current_dir(dir.path()).args(&args).output().unwrap();
        let stdout = String::from_utf8_lossy(&output.stdout);
        let stderr = String::from_utf8_lossy(&output.stderr);
        if extra.contains(&"--cpu") && stderr.contains("requires the native extension on Windows") {
            continue;
        }
        assert!(
            output.status.success(),
            "args={args:?} status={:?}\nstdout={stdout}\nstderr={stderr}",
            output.status.code()
        );
        assert!(
            stdout.contains("TOKEN = 'needle'") && !stdout.contains("secret.py"),
            "args={args:?} --no-ignore --ignore must honor final --ignore: {stdout}"
        );
    }

    // Inverse ordering `--ignore --no-ignore` must honor final `--no-ignore` across all routes,
    // including when `=` options precede the flags.
    for extra in [
        &["needle", ".", "--ignore", "--no-ignore"][..],
        &["needle", ".", "--cpu", "--ignore", "--no-ignore"][..],
        &["search", "-F", "--ignore", "--no-ignore", "needle", "."][..],
        &[
            "search",
            "--format=rg",
            "--ignore",
            "--no-ignore",
            "needle",
            ".",
        ][..],
    ] {
        let output = tg().current_dir(dir.path()).args(extra).output().unwrap();
        let stdout = String::from_utf8_lossy(&output.stdout);
        let stderr = String::from_utf8_lossy(&output.stderr);
        if extra.contains(&"--cpu") && stderr.contains("requires the native extension on Windows") {
            continue;
        }
        assert!(
            output.status.success(),
            "args={extra:?} status={:?}\nstdout={stdout}\nstderr={stderr}",
            output.status.code()
        );
        assert!(
            stdout.contains("visible.py") && stdout.contains("secret.py"),
            "args={extra:?} --ignore --no-ignore must honor final --no-ignore: {stdout}"
        );
    }
}

use crate::SearchArgs;
use anyhow::{Context, Result};
use ignore::{overrides::OverrideBuilder, WalkBuilder, WalkState};
use std::collections::BTreeSet;
use std::io::{self, BufWriter, Write};
use std::path::{Path, PathBuf};
use std::sync::Mutex;

const BROAD_GENERATED_SCAN_DIR_NAMES: &[&str] = &[
    "__pycache__",
    ".claude",
    ".cache",
    ".cargo",
    ".git",
    ".gradle",
    ".mypy_cache",
    ".npm",
    ".nuget",
    ".pytest_cache",
    ".ruff_cache",
    ".rustup",
    ".tox",
    ".venv",
    "AppData",
    "artifacts",
    "build",
    "coverage",
    "dist",
    "node_modules",
    "target",
    "venv",
];

pub(crate) fn is_broad_generated_scan_dir_name(name: &str) -> bool {
    BROAD_GENERATED_SCAN_DIR_NAMES
        .iter()
        .any(|candidate| candidate.eq_ignore_ascii_case(name))
}

pub(crate) fn generated_scan_dir_names(paths: &[String], include_child_dirs: bool) -> Vec<String> {
    let mut found = BTreeSet::new();
    for raw_path in paths {
        if raw_path.is_empty() || raw_path == "-" || raw_path.starts_with('-') {
            continue;
        }
        let path = Path::new(raw_path);
        if !path.is_dir() {
            continue;
        }
        if let Some(name) = path.file_name().and_then(|name| name.to_str()) {
            if is_broad_generated_scan_dir_name(name) {
                found.insert(name.to_string());
            }
        }
        if !include_child_dirs {
            continue;
        }
        let entries = match std::fs::read_dir(path) {
            Ok(entries) => entries,
            Err(_) => continue,
        };
        for entry in entries.flatten() {
            let is_dir = entry
                .file_type()
                .map(|file_type| file_type.is_dir())
                .unwrap_or(false);
            if !is_dir {
                continue;
            }
            let name = entry.file_name().to_string_lossy().to_string();
            if is_broad_generated_scan_dir_name(&name) {
                found.insert(name);
            }
        }
    }
    found.into_iter().collect()
}

pub(crate) fn format_broad_generated_scan_error(generated_dirs: &[String]) -> String {
    let mut visible_dirs = generated_dirs
        .iter()
        .take(8)
        .cloned()
        .collect::<Vec<_>>()
        .join(", ");
    if generated_dirs.len() > 8 {
        visible_dirs.push_str(", ...");
    }
    format!(
        "Error: broad generated-root scan refused as a safety guard, not a zero-match result: \
path contains generated, cache, \
or dependency directories ({visible_dirs}). Scope the path, add --glob, --type, \
or --max-depth, or pass --allow-broad-generated-scan to opt in.\n\
For bounded output:\n\
tg search --files <path> --hidden --max-depth <N>\n\
For intentional broad scans:\n\
--allow-broad-generated-scan"
    )
}

fn collect_files_paths(args: &SearchArgs) -> Vec<String> {
    let mut paths = Vec::new();
    if let Some(first) = args.pattern.as_ref() {
        paths.push(first.clone());
    }
    paths.extend(args.path.iter().cloned());
    if paths.is_empty() {
        paths.push(".".to_string());
    }
    paths
}

fn format_walked_path(path: &Path, strip_dot_prefix: bool) -> String {
    let display = if strip_dot_prefix {
        path.strip_prefix(".").unwrap_or(path)
    } else {
        path
    };
    display.to_string_lossy().into_owned()
}

pub(crate) fn supports_native_files_search(args: &SearchArgs) -> bool {
    args.file_type.is_empty()
        && args.sort.is_none()
        && args.sort_reverse.is_none()
        && !args.sort_files
        && args.path_separator.is_none()
        && args.gpu_device_ids.is_empty()
        && !args.json
        && !args.ndjson
        && !args.vimgrep
        && !args.count
        && !args.count_matches
        && !args.files_with_matches
        && !args.files_without_match
        && !args.pcre2
}

pub(crate) fn run_native_files_search(args: &SearchArgs) -> Result<()> {
    use std::sync::atomic::{AtomicUsize, Ordering};

    let paths = collect_files_paths(args);
    let hidden = args.hidden && !args.no_hidden;
    let bounded = !args.globs.is_empty() || args.max_depth.is_some();
    if !args.allow_broad_generated_scan && !bounded {
        let check_nested = hidden || args.no_ignore || args.no_ignore_files || args.no_ignore_vcs;
        let generated_dirs = generated_scan_dir_names(&paths, check_nested);
        if !generated_dirs.is_empty() {
            eprintln!("{}", format_broad_generated_scan_error(&generated_dirs));
            std::process::exit(2);
        }
    }

    let roots: Vec<PathBuf> = paths.iter().map(PathBuf::from).collect();
    for root in &roots {
        if !root.exists() {
            anyhow::bail!("search path does not exist: {}", root.display());
        }
    }

    let first_root = roots[0].clone();
    let strip_dot_prefix = paths.len() == 1 && paths[0] == ".";
    let mut builder = WalkBuilder::new(&first_root);
    for root in roots.iter().skip(1) {
        builder.add(root);
    }
    builder.hidden(!hidden);
    builder.max_depth(args.max_depth);
    builder.follow_links(args.follow);
    builder.threads(0);

    if args.no_ignore {
        builder.ignore(false);
        builder.git_ignore(false);
        builder.git_global(false);
        builder.git_exclude(false);
        builder.parents(false);
    } else {
        if args.no_ignore_vcs {
            builder.git_ignore(false);
            builder.git_global(false);
            builder.git_exclude(false);
        }
        if args.no_ignore_dot {
            builder.ignore(false);
        }
        if args.no_ignore_exclude {
            builder.git_exclude(false);
        }
        if args.no_ignore_global {
            builder.git_global(false);
        }
        if args.no_ignore_parent {
            builder.parents(false);
        }
        for root in &roots {
            for ignore_name in [".ignore", ".gitignore", ".rgignore"] {
                if ignore_name == ".gitignore" && args.no_ignore_vcs {
                    continue;
                }
                if ignore_name != ".gitignore" && args.no_ignore_dot {
                    continue;
                }
                let ignore_path = root.join(ignore_name);
                if std::fs::symlink_metadata(&ignore_path).is_ok_and(|meta| meta.is_file()) {
                    builder.add_ignore(ignore_path);
                }
            }
        }
    }

    if !args.globs.is_empty() {
        let mut overrides = OverrideBuilder::new(&first_root);
        for glob in &args.globs {
            overrides
                .add(glob)
                .with_context(|| format!("failed to add glob override '{glob}'"))?;
        }
        builder.overrides(
            overrides
                .build()
                .context("failed to build ignore override matcher")?,
        );
    }

    let terminator = if args.null {
        b"\0".as_slice()
    } else {
        b"\n".as_slice()
    };
    let writer = Mutex::new(BufWriter::new(io::stdout()));
    let write_error: Mutex<Option<io::Error>> = Mutex::new(None);
    let emitted = AtomicUsize::new(0);
    let errors = AtomicUsize::new(0);

    fn record_write_err(slot: &Mutex<Option<io::Error>>, err: io::Error) {
        if let Ok(mut guard) = slot.lock() {
            if guard.is_none() {
                *guard = Some(err);
            }
        }
    }

    struct ThreadBuf<'a> {
        local: Vec<u8>,
        shared: &'a Mutex<BufWriter<io::Stdout>>,
        write_error: &'a Mutex<Option<io::Error>>,
        emitted: &'a AtomicUsize,
        errors: &'a AtomicUsize,
        terminator: &'static [u8],
    }

    impl Drop for ThreadBuf<'_> {
        fn drop(&mut self) {
            if !self.local.is_empty() {
                match self.shared.lock() {
                    Ok(mut guard) => {
                        if let Err(err) = guard.write_all(&self.local) {
                            record_write_err(self.write_error, err);
                        }
                    }
                    Err(_) => record_write_err(
                        self.write_error,
                        io::Error::other("stdout writer mutex poisoned"),
                    ),
                }
            }
        }
    }

    builder.build_parallel().run(|| {
        let mut state = ThreadBuf {
            local: Vec::with_capacity(8192),
            shared: &writer,
            write_error: &write_error,
            emitted: &emitted,
            errors: &errors,
            terminator,
        };
        Box::new(move |entry| {
            match entry {
                Ok(entry) => {
                    if entry.file_type().is_some_and(|ft| ft.is_file()) {
                        let formatted = format_walked_path(entry.path(), strip_dot_prefix);
                        state.local.extend_from_slice(formatted.as_bytes());
                        state.local.extend_from_slice(state.terminator);
                        state.emitted.fetch_add(1, Ordering::Relaxed);
                        if state.local.len() >= 16384 {
                            match state.shared.lock() {
                                Ok(mut guard) => {
                                    if let Err(err) = guard.write_all(&state.local) {
                                        record_write_err(state.write_error, err);
                                        state.local.clear();
                                        return WalkState::Quit;
                                    }
                                }
                                Err(_) => {
                                    record_write_err(
                                        state.write_error,
                                        io::Error::other("stdout writer mutex poisoned"),
                                    );
                                    state.local.clear();
                                    return WalkState::Quit;
                                }
                            }
                            state.local.clear();
                        }
                    }
                }
                Err(err) => {
                    if state.errors.fetch_add(1, Ordering::Relaxed) < 5 {
                        eprintln!("tg: file walk error: {err}");
                    }
                }
            }
            WalkState::Continue
        })
    });

    if let Ok(mut err_guard) = write_error.lock() {
        if let Some(err) = err_guard.take() {
            return Err(err.into());
        }
    }
    writer
        .lock()
        .map_err(|_| anyhow::anyhow!("stdout writer mutex poisoned"))?
        .flush()?;
    let total_emitted = emitted.load(Ordering::Relaxed);
    let total_errors = errors.load(Ordering::Relaxed);
    if total_errors > 0 {
        eprintln!("tg: {total_errors} traversal error(s) encountered during --files walk");
    }
    if total_emitted == 0 {
        std::process::exit(if total_errors > 0 { 2 } else { 1 });
    }
    Ok(())
}

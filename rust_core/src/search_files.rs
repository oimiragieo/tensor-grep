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

pub(crate) fn run_native_files_search(args: &SearchArgs) -> Result<()> {
    let paths = collect_files_paths(args);
    let hidden = args.hidden && !args.no_hidden;
    let bounded = !args.globs.is_empty() || args.max_depth.is_some();
    if !args.allow_broad_generated_scan
        && !bounded
        && (hidden || args.no_ignore || args.no_ignore_files || args.no_ignore_vcs)
    {
        let generated_dirs = generated_scan_dir_names(&paths, true);
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

    struct ThreadFiles<'a> {
        local: Vec<String>,
        shared: &'a Mutex<Vec<String>>,
    }

    impl Drop for ThreadFiles<'_> {
        fn drop(&mut self) {
            if !self.local.is_empty() {
                if let Ok(mut guard) = self.shared.lock() {
                    guard.append(&mut self.local);
                }
            }
        }
    }

    let collected = Mutex::new(Vec::<String>::new());
    builder.build_parallel().run(|| {
        let mut state = ThreadFiles {
            local: Vec::new(),
            shared: &collected,
        };
        Box::new(move |entry| {
            if let Ok(entry) = entry {
                if entry.file_type().is_some_and(|ft| ft.is_file()) {
                    state
                        .local
                        .push(format_walked_path(entry.path(), strip_dot_prefix));
                }
            }
            WalkState::Continue
        })
    });

    let files = collected.into_inner().unwrap_or_default();
    if files.is_empty() {
        std::process::exit(1);
    }

    let stdout = io::stdout();
    let mut writer = BufWriter::new(stdout.lock());
    let terminator = if args.null { "\0" } else { "\n" };
    for file in &files {
        writer.write_all(file.as_bytes())?;
        writer.write_all(terminator.as_bytes())?;
    }
    writer.flush()?;
    Ok(())
}

#![cfg(windows)]

use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;

use tempfile::tempdir;

struct Scenario {
    name: &'static str,
    args: &'static [&'static str],
    golden_file: &'static str,
    compare_mode: CompareMode,
}

#[derive(Clone, Copy)]
enum CompareMode {
    SortedLines,
    SortedGroups,
}

fn repo_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap()
        .to_path_buf()
}

fn scenarios() -> Vec<Scenario> {
    vec![
        Scenario {
            name: "simple string match",
            args: &[
                "search",
                "--no-ignore",
                "ERROR",
                "tests/golden/fixture_data",
            ],
            golden_file: "simple_string_match.txt",
            compare_mode: CompareMode::SortedLines,
        },
        Scenario {
            name: "case insensitive match",
            args: &[
                "search",
                "--no-ignore",
                "-i",
                "warning",
                "tests/golden/fixture_data",
            ],
            golden_file: "case_insensitive_match.txt",
            compare_mode: CompareMode::SortedLines,
        },
        Scenario {
            name: "regex match",
            args: &[
                "search",
                "--no-ignore",
                "ERROR.*timeout",
                "tests/golden/fixture_data",
            ],
            golden_file: "regex_match.txt",
            compare_mode: CompareMode::SortedLines,
        },
        Scenario {
            name: "invert match",
            args: &[
                "search",
                "--no-ignore",
                "-v",
                "INFO",
                "tests/golden/fixture_data",
            ],
            golden_file: "invert_match.txt",
            compare_mode: CompareMode::SortedLines,
        },
        Scenario {
            name: "count matches",
            args: &[
                "search",
                "--no-ignore",
                "-c",
                "ERROR",
                "tests/golden/fixture_data",
            ],
            golden_file: "count_matches.txt",
            compare_mode: CompareMode::SortedLines,
        },
        Scenario {
            name: "context lines",
            args: &[
                "search",
                "--no-ignore",
                "-C",
                "2",
                "CRITICAL",
                "tests/golden/fixture_data",
            ],
            golden_file: "context_lines.txt",
            compare_mode: CompareMode::SortedGroups,
        },
        Scenario {
            name: "max count limit",
            args: &[
                "search",
                "--no-ignore",
                "-m",
                "5",
                "ERROR",
                "tests/golden/fixture_data",
            ],
            golden_file: "max_count_limit.txt",
            compare_mode: CompareMode::SortedLines,
        },
        Scenario {
            name: "file glob filtering",
            args: &[
                "search",
                "--no-ignore",
                "--glob=*.log",
                "ERROR",
                "tests/golden/fixture_data",
            ],
            golden_file: "file_glob_filtering.txt",
            compare_mode: CompareMode::SortedLines,
        },
    ]
}

fn normalize_for_compare(text: &str, compare_mode: CompareMode) -> String {
    let normalized_text = text.replace("\r\n", "\n");
    match compare_mode {
        CompareMode::SortedLines => {
            let mut lines: Vec<&str> = normalized_text
                .lines()
                .filter(|line| !line.is_empty())
                .collect();
            lines.sort_unstable();
            let mut normalized = lines.join("\n");
            normalized.push('\n');
            normalized
        }
        CompareMode::SortedGroups => {
            let mut groups: Vec<String> = normalized_text
                .trim()
                .split("\n--\n")
                .map(|group| group.trim().to_string())
                .filter(|group| !group.is_empty())
                .collect();
            groups.sort_unstable();
            let mut normalized = groups.join("\n--\n");
            normalized.push('\n');
            normalized
        }
    }
}

#[test]
fn test_search_subcommand_matches_recorded_golden_outputs() {
    let repo_root = repo_root();
    let bogus_python_home = tempdir().unwrap();

    for scenario in scenarios() {
        let output = Command::new(env!("CARGO_BIN_EXE_tg"))
            .current_dir(&repo_root)
            .args(scenario.args)
            .env("PYTHONHOME", bogus_python_home.path())
            .output()
            .unwrap();

        assert!(
            output.status.success(),
            "scenario={} status={:?}\nstdout={}\nstderr={}",
            scenario.name,
            output.status.code(),
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr)
        );

        let expected =
            fs::read_to_string(repo_root.join("tests/golden").join(scenario.golden_file)).unwrap();
        let actual = String::from_utf8(output.stdout).unwrap();
        let normalized_expected = normalize_for_compare(&expected, scenario.compare_mode);
        let normalized_actual = normalize_for_compare(&actual, scenario.compare_mode);

        assert_eq!(
            normalized_actual,
            normalized_expected,
            "scenario={} stderr={} ",
            scenario.name,
            String::from_utf8_lossy(&output.stderr)
        );
    }
}

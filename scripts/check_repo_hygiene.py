from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

REQUIRED_TRACKED_FILES = ("rust_core/Cargo.lock",)

EXPLICIT_FORBIDDEN_TRACKED_PATHS = {
    ".coverage",
    ".env",
    "agent_out.json",
    "dummy.py",
    "src/tensor_grep/cli/commands.txt",
    "src/tensor_grep/core/import_trace.txt",
    "stream_test.py",
    "test_group2.py",
}

ROOT_GENERATED_DIR_PREFIXES = (
    ".tensor-grep/",
    ".tmp",
    "bench_ast_data/",
    "bench_data/",
    "benchmarks/corpus/",
    "benchmarks/dummy_replace_data/",
    "gpu_bench_data/",
    "group2_many_files/",
    "many_files/",
    "rg-temp/",
    "ripgrep-",
    "scripts/ci_logs/",
    "tg_rg_probe_",
    "tmp_rg_probe/",
    "tmp_rg_tg_probe/",
)

NESTED_ROOT_SCRATCH_FILE_RE = re.compile(r"^(\.factory|_archive)/[^/]+\.(log|patch|txt)$")
ROOT_SCRATCH_FILE_RE = re.compile(r"^[^/]+\.(log|patch|txt)$")
GENERATED_ARTIFACT_RE = re.compile(
    r"(^|/)(__pycache__/|.*\.pyc$|.*\.pyd$|artifacts/|src/tensor_grep/core/profile_stats$)"
)
BROAD_GITIGNORE_RE = re.compile(r"^(rust_core/Cargo\.lock|\*\.(log|patch|txt))$")


def _git_ls_files(repo_root: Path) -> list[str]:
    completed = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=repo_root,
        check=True,
        capture_output=True,
    )
    return [
        path.replace("\\", "/")
        for path in completed.stdout.decode("utf-8", errors="replace").split("\0")
        if path
    ]


def _gitignore_lines(repo_root: Path) -> list[str]:
    return (repo_root / ".gitignore").read_text(encoding="utf-8").splitlines()


def _forbidden_tracked_reason(path: str) -> str | None:
    if path in EXPLICIT_FORBIDDEN_TRACKED_PATHS:
        return "tracked scratch/debug artifact"
    if NESTED_ROOT_SCRATCH_FILE_RE.fullmatch(path):
        return "tracked scratch output in local artifact directory"
    if ROOT_SCRATCH_FILE_RE.fullmatch(path):
        return "tracked root scratch output"
    if GENERATED_ARTIFACT_RE.search(path):
        return "tracked generated artifact"
    if any(path.startswith(prefix) for prefix in ROOT_GENERATED_DIR_PREFIXES):
        return "tracked generated or debug directory"
    return None


def check_repo_hygiene(*, tracked_paths: list[str], gitignore_lines: list[str]) -> list[str]:
    normalized_paths = [path.replace("\\", "/") for path in tracked_paths]
    tracked_set = set(normalized_paths)
    errors: list[str] = []

    forbidden = [
        f"{path} ({reason})"
        for path in sorted(normalized_paths)
        if (reason := _forbidden_tracked_reason(path)) is not None
    ]
    if forbidden:
        errors.append("Tracked generated or scratch artifacts detected:\n" + "\n".join(forbidden))

    missing_required = [path for path in REQUIRED_TRACKED_FILES if path not in tracked_set]
    if missing_required:
        errors.append(
            "Required reproducibility files are not tracked:\n" + "\n".join(missing_required)
        )

    broad_ignores = [
        f"{line_number}:{line.strip()}"
        for line_number, line in enumerate(gitignore_lines, start=1)
        if BROAD_GITIGNORE_RE.fullmatch(line.strip())
    ]
    if broad_ignores:
        errors.append(
            ".gitignore contains broad generated-artifact ignores that can hide fixtures "
            "or lockfiles:\n" + "\n".join(broad_ignores)
        )

    return errors


def main(argv: list[str] | None = None) -> int:
    _ = argv
    try:
        errors = check_repo_hygiene(
            tracked_paths=_git_ls_files(ROOT),
            gitignore_lines=_gitignore_lines(ROOT),
        )
    except subprocess.CalledProcessError as exc:
        print(f"repo hygiene check failed to inspect tracked files: {exc}", file=sys.stderr)
        return 2

    if errors:
        print("\n\n".join(errors), file=sys.stderr)
        return 1
    print("Repo hygiene guard passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

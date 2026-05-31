import importlib.util
from pathlib import Path


def _load_module():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "check_repo_hygiene.py"
    spec = importlib.util.spec_from_file_location("check_repo_hygiene", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_repo_hygiene_allows_expected_project_files() -> None:
    module = _load_module()

    errors = module.check_repo_hygiene(
        tracked_paths=[
            "README.md",
            "pyproject.toml",
            "rust_core/Cargo.lock",
            "tests/golden/simple_string_match.txt",
            "tests/e2e/snapshots/output.txt",
        ],
        gitignore_lines=[
            "/.tmp*/",
            "/*.txt",
            "/*.log",
            "!tests/golden/**/*.txt",
        ],
    )

    assert errors == []


def test_repo_hygiene_blocks_tracked_ai_scratch_outputs() -> None:
    module = _load_module()

    errors = module.check_repo_hygiene(
        tracked_paths=[
            ".env",
            "dummy.py",
            "out.txt",
            "release_fail.log",
            ".factory/diff.txt",
            ".tmp_repro/output.json",
            "_archive/main_entry.txt",
            "artifacts/bench/result.json",
            "benchmarks/corpus/file.txt",
            "ripgrep-v14/README.md",
            "stream_test.py",
            "rust_core/Cargo.lock",
        ],
        gitignore_lines=["/*.txt", "/*.log"],
    )

    joined = "\n".join(errors)
    assert "dummy.py" in joined
    assert "out.txt" in joined
    assert "release_fail.log" in joined
    assert ".factory/diff.txt" in joined
    assert ".tmp_repro/output.json" in joined
    assert "_archive/main_entry.txt" in joined
    assert "artifacts/bench/result.json" in joined
    assert "benchmarks/corpus/file.txt" in joined
    assert "ripgrep-v14/README.md" in joined
    assert "stream_test.py" in joined


def test_repo_hygiene_requires_rust_lockfile_and_rejects_broad_ignores() -> None:
    module = _load_module()

    errors = module.check_repo_hygiene(
        tracked_paths=["pyproject.toml"],
        gitignore_lines=[
            "*.txt",
            "*.log",
            "*.patch",
            "rust_core/Cargo.lock",
        ],
    )

    joined = "\n".join(errors)
    assert "rust_core/Cargo.lock" in joined
    assert "*.txt" in joined
    assert "*.log" in joined
    assert "*.patch" in joined

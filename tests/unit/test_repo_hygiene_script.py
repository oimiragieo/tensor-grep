import importlib.util
import subprocess
from pathlib import Path

import pytest


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


def test_repo_hygiene_requires_rust_lockfile_version_to_match_cargo_toml() -> None:
    module = _load_module()

    errors = module.check_repo_hygiene(
        tracked_paths=["pyproject.toml", "rust_core/Cargo.lock"],
        gitignore_lines=["/*.txt", "/*.log"],
        cargo_toml_text='[package]\nname = "tensor_grep_rs"\nversion = "1.13.25"\n',
        cargo_lock_text=('[[package]]\nname = "tensor_grep_rs"\nversion = "1.13.24"\n'),
    )

    assert (
        "rust_core/Cargo.lock tensor_grep_rs version 1.13.24 "
        "!= rust_core/Cargo.toml package version 1.13.25"
    ) in errors


@pytest.mark.parametrize(
    "path",
    [
        "GATES.md",
        "GEMINI.md",
        "new-session-notes.md",
        "private/research.json",
        ".new-agent/settings.json",
        ".github/random-session.md",
        ".github/.env.production",
        "src/.env.local",
        "benchmarks/.gemini/settings.json",
        "scripts/MEMORY.md",
        "tests/fixtures/SESSION_HANDOFF.md",
        "docs/new-location/notes.md",
        "docs/audits/review.json",
        "docs/plans/private.md",
        ".claude/skills/private-procedure/SKILL.md",
        ".claude/skills/tensor-grep/internal-notes.md",
    ],
)
def test_publication_boundary_rejects_unapproved_files_in_any_location(path: str) -> None:
    module = _load_module()
    errors = module.check_repo_hygiene(
        tracked_paths=["rust_core/Cargo.lock", path], gitignore_lines=[]
    )
    assert any(path in error for error in errors)


def test_publication_boundary_keeps_explicit_public_fixtures_and_source() -> None:
    module = _load_module()
    paths = [
        *module.APPROVED_ROOT_FILES,
        *module.APPROVED_AGENT_FILES,
        *module.APPROVED_DOC_FILES,
        *module.APPROVED_ASSISTANT_FIXTURES,
        "rust_core/Cargo.lock",
        "src/tensor_grep/new_feature.py",
        "tests/fixtures/example/settings.json",
        "benchmarks/fixtures/gemini/skills/tensor-grep/SKILL.md",
    ]
    assert module.check_repo_hygiene(tracked_paths=paths, gitignore_lines=[]) == []


def test_forced_git_add_cannot_bypass_publication_guard(tmp_path: Path) -> None:
    module = _load_module()
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text("*.local\n", encoding="utf-8")
    (tmp_path / ".env.local").write_text("EXAMPLE=dummy\n", encoding="utf-8")
    subprocess.run(["git", "add", "--force", ".env.local"], cwd=tmp_path, check=True)
    errors = module.check_repo_hygiene(
        tracked_paths=["rust_core/Cargo.lock", *module._git_ls_files(tmp_path)],
        gitignore_lines=[],
    )
    assert any(".env.local" in error for error in errors)


@pytest.mark.parametrize(
    "path",
    [
        ".env",
        ".env.local",
        ".env.production",
        ".superpowers/notes.md",
        ".gemini/settings.json",
        "docs/design/private.md",
        "docs/BACKLOG.md",
        "docs/TASK_BOARD.md",
        "docs/FIX_PLAN_new.md",
        "docs/PAPER.md",
        "docs/DOGFOOD_new.md",
        "docs/world_class_plan.md",
        "features/private.md",
    ],
)
def test_private_local_files_are_ignored(path: str) -> None:
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        ["git", "check-ignore", "--no-index", "--", path], cwd=root, capture_output=True
    )
    assert result.returncode == 0


def test_docker_context_excludes_private_configuration_and_operating_state() -> None:
    root = Path(__file__).resolve().parents[2]
    rules = set((root / ".dockerignore").read_text(encoding="utf-8").splitlines())
    assert {
        "**/.env",
        "**/.env.*",
        "**/.claude/",
        "**/.gemini/",
        "**/.superpowers/",
        "**/.codex/",
        "**/.cursor/",
        "**/.opencode/",
        "**/.factory/",
        "**/_archive/",
        "docs/design/",
        "docs/audits/",
        "docs/plans/",
        "docs/planning/",
        "docs/BACKLOG.md",
        "docs/TASK_BOARD.md",
        "docs/PAPER.md",
        "docs/FIX_PLAN*.md",
        "docs/DOGFOOD*.md",
        "GEMINI.md",
        "features/",
    } <= rules

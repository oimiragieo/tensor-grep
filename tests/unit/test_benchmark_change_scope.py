import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CI = ROOT / ".github/workflows/ci.yml"
SCRIPT = ROOT / "scripts/benchmark_change_scope.py"
IDENTITY = "-c user.name=Fixture -c user.email=a@b.c -c commit.gpgsign=false".split()


def git(root, *args):
    env = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
    run = subprocess.run(
        ["git", *args], cwd=root, env=env, capture_output=True, text=True, timeout=20
    )
    assert run.returncode == 0, run.stderr
    return run.stdout.strip()


def commit(root):
    git(root, "add", ".")
    git(root, *IDENTITY, "commit", "--allow-empty", "-qm", "fixture")
    return git(root, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init", "--template=", "-q")
    (tmp_path / "src").mkdir()
    (tmp_path / "src/old.py").write_text("baseline\n")
    path = tmp_path / ".github/workflows/ci.yml"
    path.parent.mkdir(parents=True)
    path.write_bytes(CI.read_bytes())
    return tmp_path, commit(tmp_path)


def decision(root, base, event="push", raw=None, flags=()):
    head = git(root, "rev-parse", "HEAD") if (root / ".git").exists() else base
    payload = {"before": base, "after": head}
    if event == "pull_request":
        payload = {"pull_request": {"base": {"sha": base}, "head": {"sha": head}}}
    path = root.parent / "event.json"
    path.write_text(raw if raw is not None else json.dumps(payload))
    env = dict(
        os.environ, GITHUB_WORKSPACE=str(root), GITHUB_EVENT_NAME=event, GITHUB_EVENT_PATH=str(path)
    )
    command = [sys.executable, *flags, str(SCRIPT)]
    run = subprocess.run(command, cwd=root, env=env, capture_output=True, text=True, timeout=20)
    assert run.returncode == 0, run.stderr
    assert run.stdout in ("true\n", "false\n"), run.stdout
    return run.stdout == "true\n"


def test_path_scope_and_pr_payload(repo):
    root, _ = repo
    relevant = "src/x.py src/input.md rust_core/src/x.rs rust_core/src/input.md benchmarks/input.txt benchmarks/input.md tests/unit/fixture.bin tests/unit/helper.py tests/unit/nested/test_scope_sample.py pyproject.toml uv.lock rust_core/Cargo.toml rust_core/Cargo.lock rust_core/rust-toolchain.toml .github/actions/shared/action.yml unknown.dat".split()
    for path in [
        *relevant,
        "README.md",
        "docs/guide.md",
        "tests/unit/test_unrelated.py",
        "tests/unit/test_independent_scope_sample.py",
        "scripts/benchmark_change_scope.py",
    ]:
        base = git(root, "rev-parse", "HEAD")
        changed = root / path
        changed.parent.mkdir(parents=True, exist_ok=True)
        changed.write_text("changed\n")
        commit(root)
        for event in ("push", "pull_request"):
            assert decision(root, base, event) is (path in relevant), (path, event)


@pytest.mark.parametrize("action", ["rename", "delete"])
def test_old_relevant_name_is_not_lost(repo, action):
    root, base = repo
    if action == "rename":
        git(root, "mv", "src/old.py", "renamed-doc.md")
    else:
        (root / "src/old.py").unlink()
    commit(root)
    assert decision(root, base)


def test_uncertain_events_and_git_failures_run(repo):
    root, base = repo
    (root / "docs.md").write_text("changed\n")
    head = commit(root)
    for invalid in (head, "", "missing", "0" * 40):
        assert decision(root, invalid)
    for event in ("schedule", "workflow_dispatch", "unknown"):
        assert decision(root, base, event)
    assert decision(root, base, raw="{")
    missing = root / "not-a-repo"
    missing.mkdir()
    assert decision(missing, base)

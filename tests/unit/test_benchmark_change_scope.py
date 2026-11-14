import ast
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

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


@pytest.mark.parametrize(
    "change",
    "unrelated eligibility workguard upload runner command env uses with global malformed both-malformed deep-malformed no-parser".split(),
)
def test_ci_semantics_ignore_only_nonmeasurement_changes(repo, change):
    root, base = repo
    path = root / ".github/workflows/ci.yml"
    doc = yaml.safe_load(path.read_text())
    job = doc["jobs"]["benchmark-regression"]
    if change in ("unrelated", "no-parser"):
        doc["jobs"]["smoke"]["name"] = "Unrelated"
    elif change == "eligibility":
        job["if"] = "needs.changes.outputs.benchmark == 'true'"
    elif change == "workguard":
        step = next(s for s in job["steps"] if s.get("name") == "Run core benchmark suite")
        step["if"] = (
            "needs.changes.outputs.benchmark == 'true' && (github.event_name != 'schedule')"
        )
        base_doc = yaml.safe_load(path.read_text())
        next(
            s
            for s in base_doc["jobs"]["benchmark-regression"]["steps"]
            if s.get("name") == "Run core benchmark suite"
        )["if"] = "github.event_name != 'schedule'"
        path.write_text(yaml.safe_dump(base_doc))
        base = commit(root)
    elif change == "upload":
        next(s for s in job["steps"] if s.get("name") == "Upload benchmark artifacts")["if"] = (
            "always()"
        )
    elif change == "both-malformed":
        path.write_text("{invalid-before")
        base = commit(root)
    elif change == "runner":
        job["runs-on"] = "windows-latest"
    elif change == "command":
        next(s for s in job["steps"] if "run" in s)["run"] += "\necho changed"
    elif change in ("env", "global"):
        (doc if change == "global" else job)["env"] = {"MEASURE": "changed"}
    elif change == "uses":
        job["steps"][0]["uses"] = "actions/checkout@changed"
    elif change == "with":
        job["steps"][0]["with"]["fetch-depth"] = 1
    text = "{" if change in ("malformed", "both-malformed") else yaml.safe_dump(doc)
    path.write_text("[" * 1024 + "]" * 1024 if change == "deep-malformed" else text)
    commit(root)
    assert decision(root, base, flags=("-S",) if change == "no-parser" else ()) is (
        change not in ("unrelated", "eligibility", "workguard", "upload")
    )


@pytest.mark.parametrize("event", ["push", "pull_request"])
@pytest.mark.parametrize(
    "code,benchmark", [(False, False), (True, False), (False, True), (True, True)]
)
def test_actual_workflow_gate(event, code, benchmark):
    job = yaml.safe_load(CI.read_text())["jobs"]["benchmark-regression"]
    work = next(s for s in job["steps"] if s.get("name") == "Run core benchmark suite")
    job_runs = evaluate_gate(job.get("if", "True"), event, code, benchmark)
    if event == "push":
        assert job_runs, "main job must succeed so release.needs can proceed"
    assert (job_runs and evaluate_gate(work.get("if", "True"), event, code, benchmark)) is benchmark


def evaluate_gate(expression, event, code, benchmark):
    for name in ("code", "benchmark"):
        expression = expression.replace(f"needs.changes.outputs.{name}", name)
    expression = expression.replace("github.event_name", "event").strip("${} ")
    expression = expression.replace("||", " or ").replace("&&", " and ")
    tree = ast.parse(expression, mode="eval")
    allowed = "Expression BoolOp Or And Compare Eq NotEq Name Load Constant".split()
    assert all(type(n).__name__ in allowed for n in ast.walk(tree))
    values = {"event": event, "code": str(code).lower(), "benchmark": str(benchmark).lower()}
    assert all(n.id in values for n in ast.walk(tree) if isinstance(n, ast.Name))
    return eval(compile(tree, "<workflow-gate>", "eval"), {"__builtins__": {}}, values)


def test_dedicated_scope_is_produced():
    assert "benchmark" in yaml.safe_load(CI.read_text())["jobs"]["changes"]["outputs"]


def test_artifacts_survive_a_failed_benchmark():
    steps = yaml.safe_load(CI.read_text())["jobs"]["benchmark-regression"]["steps"]
    upload = next(s for s in steps if s.get("name") == "Upload benchmark artifacts")
    assert upload.get("if") == "always()"

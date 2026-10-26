"""Regression coverage for the frozen git-history retrieval evaluation."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

pytestmark = pytest.mark.eval

ROOT = Path(__file__).resolve().parents[2]
HARNESS = ROOT / "benchmarks/git_history_retrieval/harness.py"
MANIFEST = ROOT / "benchmarks/git_history_retrieval/manifest.json"
BASELINE = ROOT / "benchmarks/git_history_retrieval/baseline.json"


def harness_module() -> ModuleType:
    root = harness_root()
    path = root / "benchmarks/git_history_retrieval/harness.py"
    assert path.is_file(), "git-history retrieval harness entrypoint is missing"
    spec = importlib.util.spec_from_file_location("git_history_retrieval_harness", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def harness_root() -> Path:
    return Path(os.environ.get("TG_EVAL_HARNESS_ROOT", ROOT))


def git(repo: Path, *argv: str, date: str | None = None) -> str:
    env = os.environ.copy()
    if date:
        env.update(GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date)
    result = subprocess.run(
        ["git", "-C", str(repo), *argv], capture_output=True, check=True, env=env
    )
    return result.stdout.decode("utf-8", errors="strict").strip()


def commit(repo: Path, subject: str, *, date: str | None = None) -> str:
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", subject, date=date)
    return git(repo, "rev-parse", "HEAD")


def fixture_repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "source"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "eval@example.test")
    git(repo, "config", "user.name", "Eval")
    for path in ("src/a.py", "tests/test_a.py", "src/delete.py", "src/rename.py"):
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("initial\n", encoding="utf-8")
    commit(repo, "chore: initial", date="2025-04-01T00:00:00+0000")
    (repo / "src/a.py").write_text("changed\n", encoding="utf-8")
    (repo / "tests/test_a.py").write_text("assert True\n", encoding="utf-8")
    (repo / "src/delete.py").unlink()
    git(repo, "mv", "src/rename.py", "src/renamed.py")
    (repo / "src/new.py").write_text("new\n", encoding="utf-8")
    sha = commit(repo, "fix(core): Repair target selection (#42)", date="2025-03-01T00:00:00+0000")
    git(repo, "update-ref", "refs/remotes/origin/main", sha)
    return repo, sha


def generated(tmp_path: Path) -> tuple[ModuleType, Path, str, dict[str, object], Path]:
    harness = harness_module()
    repo, sha = fixture_repo(tmp_path)
    out = tmp_path / "dataset"
    manifest = harness.generate_dataset(repo, sha, out)
    return harness, repo, sha, manifest, out


def test_git_history_harness_entrypoint_exists() -> None:
    harness_module()


@pytest.mark.skipif(
    not MANIFEST.is_file() or not BASELINE.is_file(),
    reason="leaf 11a is report-only until manifest.json and baseline.json exist",
)
@pytest.mark.skipif(
    sys.platform != "linux" or not os.environ.get("GITHUB_ACTIONS"),
    reason="the enforcing gate runs only in Linux CI with Docker",
)
def test_frozen_heldout_container_gate(tmp_path: Path) -> None:
    harness = harness_module()
    report = harness.orchestrate(
        ROOT, MANIFEST, tmp_path / "report.json", subject="head", enforce=True
    )
    assert report["confinement"] == "container"


def test_only_modified_parent_paths_are_labels_and_inputs(tmp_path: Path) -> None:
    _, _, sha, manifest, _ = generated(tmp_path)
    case = manifest["cases"][0]
    assert case["sha"] == sha
    assert case["gold_files"] == ["src/a.py", "tests/test_a.py"]
    assert case["gold_tests"] == ["tests/test_a.py"]
    assert case["input_paths"] == ["src/a.py"]
    assert case["diff_impact_eligible"] is True


def test_query_tokens_and_exclusion_counts(tmp_path: Path) -> None:
    harness, repo, sha, manifest, _ = generated(tmp_path)
    assert harness.query_from_subject("fix(core):  Repair   target  (#42)") == "Repair target"
    assert harness.search_tokens("ABC -- abc def_def a ! 123 xyz more next last ninth") == [
        "abc",
        "def_def",
        "123",
        "xyz",
        "more",
        "next",
        "last",
        "ninth",
    ]
    assert manifest["exclusion_counts"] == dict.fromkeys(harness.EXCLUSION_REASONS, 0)
    (repo / "src/a.py").write_text("empty query\n", encoding="utf-8")
    commit(repo, "fix:   ")
    (repo / "src/a.py").write_text("no tokens\n", encoding="utf-8")
    commit(repo, "fix: a bb")
    (repo / "only_added.py").write_text("new\n", encoding="utf-8")
    last = commit(repo, "fix: only add file")
    git(repo, "update-ref", "refs/remotes/origin/main", last)
    excluded = harness.generate_dataset(repo, last, tmp_path / "excluded")
    assert excluded["exclusion_counts"]["empty_query"] == 1
    assert excluded["exclusion_counts"]["no_tokens"] == 1
    assert excluded["exclusion_counts"]["empty_gold"] == 1
    assert sha != last


def test_classifier_boundaries_are_frozen_at_generation() -> None:
    harness = harness_module()
    paths = ["tests/x.py", "x_test.go", "x.test.jsx", "spec/x.rb", "testdata/x.py"]
    assert harness._classify_tests(paths, sys.executable) == set(paths[:3])


def test_split_uses_sequence_not_timestamp(tmp_path: Path) -> None:
    harness = harness_module()
    repo, first = fixture_repo(tmp_path)
    (repo / "src/a.py").write_text("later\n", encoding="utf-8")
    (repo / "tests/test_a.py").write_text("assert False\n", encoding="utf-8")
    last = commit(repo, "fix: second regression", date="2024-01-01T00:00:00+0000")
    git(repo, "update-ref", "refs/remotes/origin/main", last)
    manifest = harness.generate_dataset(repo, last, tmp_path / "ordered")
    assert [(c["sha"], c["split"]) for c in manifest["cases"]] == [
        (first, "tune"),
        (last, "heldout"),
    ]


def test_committed_patch_hash_and_applied_paths(tmp_path: Path) -> None:
    harness, repo, sha, manifest, out = generated(tmp_path)
    case = manifest["cases"][0]
    patch = (out / "patches" / f"{sha}.patch").read_bytes()
    assert harness._sha256(patch) == case["input_patch_sha256"]
    target = tmp_path / "parent"
    harness.create_parent_repo(repo, case["parent"], sha, target)
    harness._git(target, "apply", "--check", input_bytes=patch)
    harness._git(target, "apply", input_bytes=patch)
    changed = set(
        harness._out(harness._git(target, "diff", "--name-only", "HEAD", "--")).splitlines()
    )
    assert changed == set(case["input_paths"])
    assert changed.isdisjoint(case["gold_tests"])


def test_real_diff_impact_head_reports_exact_input_paths(tmp_path: Path) -> None:
    harness, source, sha, manifest, out = generated(tmp_path)
    case = manifest["cases"][0]
    repo = tmp_path / "patched"
    harness.create_parent_repo(source, case["parent"], sha, repo)
    harness._git(repo, "apply", input_bytes=(out / "patches" / f"{sha}.patch").read_bytes())
    env = {key: value for key, value in os.environ.items() if not key.startswith("TG_")}
    env["PYTHONNOUSERSITE"] = "1"
    result = subprocess.run(
        [sys.executable, "-m", "tensor_grep", "diff-impact", "HEAD", "--json"],
        cwd=repo,
        env=env,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    payload = json.loads(result.stdout.decode("utf-8", errors="strict"))
    assert set(payload["changed_files"]) == set(case["input_paths"])
    scored = harness.score_payload(payload, "diff_impact", case, repo, harness.load_scoring())
    assert "affected_tests.set_precision" in scored


def test_generation_is_byte_identical_and_future_safe(tmp_path: Path) -> None:
    harness, repo, sha, _, first = generated(tmp_path)
    identity = harness._source_identity(repo)
    second = tmp_path / "second"
    harness.generate_dataset(repo, sha, second)
    assert (first / "manifest.json").read_bytes() == (second / "manifest.json").read_bytes()
    assert (first / "patches" / f"{sha}.patch").read_bytes() == (
        second / "patches" / f"{sha}.patch"
    ).read_bytes()
    (repo / "future.py").write_text("future\n", encoding="utf-8")
    later = commit(repo, "fix: future only")
    git(repo, "update-ref", "refs/remotes/origin/main", later)
    third = tmp_path / "third"
    harness.generate_dataset(repo, sha, third)
    assert (first / "manifest.json").read_bytes() == (third / "manifest.json").read_bytes()
    assert harness._source_identity(repo) != identity


def test_set_precision_penalizes_false_positive_test() -> None:
    harness = harness_module()
    assert harness.score_set({"tests/a.py"}, {"tests/a.py"})["set_precision"] == 1.0
    assert (
        harness.score_set({"tests/a.py", "tests/wrong.py"}, {"tests/a.py"})["set_precision"] == 0.5
    )
    assert harness.metric_specs()["diff_impact.affected_tests.set_precision"]["gated"] is True
    assert harness.score_set(set(), {"tests/a.py"}) == {"set_recall": 0, "set_precision": 0}


def test_changed_files_mismatch_is_harness_error(tmp_path: Path) -> None:
    harness, _, _, manifest, _ = generated(tmp_path)
    case = manifest["cases"][0]
    with pytest.raises(harness.HarnessError, match="changed_files_mismatch"):
        harness.score_payload(
            {"changed_files": [], "affected_tests": [], "affected_files": []},
            "diff_impact",
            case,
            tmp_path,
            harness.load_scoring(),
        )


def test_metric_direction_and_gate_failures(tmp_path: Path) -> None:
    harness = harness_module()
    specs = harness.metric_specs()
    baseline = {
        "metrics": {key: {**spec, "value": 0.5} for key, spec in specs.items()},
        "environment": {"base_image_digest": "old"},
        "evaluator_sha256": "old",
        "case_set": {"find": ["sha"]},
    }
    current = {
        "metrics": {key: {**spec, "value": 0.5} for key, spec in specs.items()},
        "case_set": {"find": ["sha"]},
    }
    current["metrics"]["find.false_primary_rate"]["value"] = 0.8
    current["metrics"]["find.f1@10"]["value"] = 0.4
    failures = harness.compare_gate(
        baseline, current, evaluator="new", environment={"base_image_digest": "new"}
    )
    assert "evaluator_changed" in failures
    assert "environment_changed" in failures
    assert "below_baseline:find.f1@10" in failures
    assert "below_baseline:find.false_primary_rate" not in failures
    assert specs["find.false_primary_rate"]["direction"] == "lower_better"
    assert harness.metric_is_worse(0.8, 0.5, "lower_better") is True
    with pytest.raises(harness.HarnessError, match="empty_heldout"):
        harness.aggregate_scores([], {"cases": []})
    report = {
        "metrics": current["metrics"],
        "environment": {"base_image_digest": "new"},
        "evaluator_sha256": "new",
        "case_set": current["case_set"],
    }
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps(baseline), encoding="utf-8")
    assert "environment_changed" in harness.gate_report(report, path)


def test_harness_import_closure_and_cache_variable_census() -> None:
    harness = harness_module()
    harness.assert_stdlib_import_closure(HARNESS)
    source = "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for parent in (ROOT / "src", ROOT / "rust_core")
        for path in parent.rglob("*.py")
    )
    source += "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in (ROOT / "rust_core").rglob("*.rs")
    )
    discovered = {
        name
        for name in re.findall(r"\bTG_[A-Z0-9_]+\b", source)
        if name.endswith(("_DIR", "_ROOT", "_PATH"))
    }
    assert discovered - {"TG_RG_PATH"} == set(harness.PATH_ENV)
    assert "TG_SESSION_DAEMON_AUTOSTART" in HARNESS.read_text(encoding="utf-8")


def test_scoring_import_closure_rejects_external_dependency(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = harness_module()
    scoring = tmp_path / "retrieval_scoring.py"
    scoring.write_text("import external_scoring_package\n", encoding="utf-8")
    monkeypatch.setattr(harness, "SCORING", scoring)
    with pytest.raises(harness.HarnessError, match="non_stdlib_import: external_scoring_package"):
        harness.assert_stdlib_import_closure(HARNESS)


def test_case_environment_confines_home_and_scrubs_inherited_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = harness_module()
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "outside-python"))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "outside-xdg"))
    monkeypatch.setenv("TG_SEMANTIC_MODEL_DIR", str(tmp_path / "outside-model"))
    home = tmp_path / "run/home"
    env = harness.case_environment(home, tmp_path / "build/head/venv")
    assert "PYTHONPATH" not in env
    assert env["HOME"] == str(home) == env["USERPROFILE"]
    assert env["XDG_RUNTIME_DIR"].startswith(str(home))
    assert env["TG_SEMANTIC_MODEL_DIR"].startswith(str(home))
    assert env["TG_SESSION_DAEMON_AUTOSTART"] == "0"


def test_report_only_until_baseline_exists(tmp_path: Path) -> None:
    harness = harness_module()
    assert harness.gate_report({}, tmp_path / "missing.json", enforce=False) == [
        "report_only: baseline_missing"
    ]
    with pytest.raises(harness.HarnessError, match="baseline_missing"):
        harness.gate_report({}, tmp_path / "missing.json")


@pytest.mark.skipif(
    sys.platform != "linux" or not os.environ.get("GITHUB_ACTIONS"),
    reason="container confinement checks run only in Linux CI",
)
def test_container_mount_smoke_and_negative_controls(tmp_path: Path) -> None:
    harness = harness_module()
    case = tmp_path / "case"
    build = tmp_path / "build"
    case.mkdir()
    build.mkdir()
    image = "ubuntu:24.04@sha256:f610ab94648195aa356059f5b41d6085c9d4d903c072430cdd1af7bdb646106b"
    script = (
        "test ! -e /source && test ! -e /work/patches && "
        "test ! -e /work/runs && test ! -e /work/diff-impact && "
        "touch /work/run/smoke && ! touch /work/build/denied && "
        "! touch /opt/denied && ! touch /home/denied"
    )
    harness._docker_run(
        image,
        network="none",
        read_only=True,
        mounts=[(case, "/work/run", "rw"), (build, "/work/build", "readonly")],
        arguments=["sh", "-c", script],
    )
    assert (case / "smoke").is_file()


def test_merge_fix_is_excluded_from_first_parent_cases(tmp_path: Path) -> None:
    harness = harness_module()
    repo, first = fixture_repo(tmp_path)
    git(repo, "branch", "topic")
    (repo / "src/a.py").write_text("main change\n", encoding="utf-8")
    commit(repo, "chore: main work")
    git(repo, "checkout", "-q", "topic")
    (repo / "topic.py").write_text("topic\n", encoding="utf-8")
    commit(repo, "chore: topic work")
    git(repo, "checkout", "-q", "main")
    git(repo, "merge", "--no-ff", "-m", "fix: merge topic", "topic")
    last = git(repo, "rev-parse", "HEAD")
    git(repo, "update-ref", "refs/remotes/origin/main", last)
    manifest = harness.generate_dataset(repo, last, tmp_path / "merge")
    assert [case["sha"] for case in manifest["cases"]] == [first]
    assert manifest["exclusion_counts"]["not_single_parent"] == 1


def test_copy_status_never_enters_gold_or_inputs(tmp_path: Path) -> None:
    harness = harness_module()
    repo, _ = fixture_repo(tmp_path)
    source = repo / "src/source_long.py"
    original = "".join(f"line_{index} = {index}\n" for index in range(100))
    source.write_text(original, encoding="utf-8")
    commit(repo, "chore: copy source")
    parent = git(repo, "rev-parse", "HEAD")
    (repo / "src/copied.py").write_text(original, encoding="utf-8")
    source.write_text(original + "last = 1\n", encoding="utf-8")
    (repo / "tests/test_a.py").write_text("assert 'copy'\n", encoding="utf-8")
    sha = commit(repo, "fix: copy regression")
    status = git(repo, "diff", "--name-status", "-C", parent, sha, "--")
    assert any(line.startswith("C") and "src/copied.py" in line for line in status.splitlines())
    git(repo, "update-ref", "refs/remotes/origin/main", sha)
    manifest = harness.generate_dataset(repo, sha, tmp_path / "copy")
    case = manifest["cases"][-1]
    assert "src/copied.py" not in case["gold_files"]
    assert "src/copied.py" not in case["input_paths"]


def test_diff_eligibility_reasons_and_empty_path_guard(tmp_path: Path) -> None:
    harness = harness_module()
    repo, _ = fixture_repo(tmp_path)
    (repo / "src/a.py").write_text("source only\n", encoding="utf-8")
    commit(repo, "fix: source only")
    (repo / "tests/test_a.py").write_text("assert 1\n", encoding="utf-8")
    commit(repo, "fix: tests only")
    (repo / "src/binary.bin").write_bytes(b"\0old\0")
    commit(repo, "chore: binary seed")
    (repo / "src/binary.bin").write_bytes(b"\0new\0")
    (repo / "tests/test_a.py").write_text("assert 2\n", encoding="utf-8")
    commit(repo, "fix: binary payload")
    (repo / "src/a.py").write_text("last eligible\n", encoding="utf-8")
    (repo / "tests/test_a.py").write_text("assert 3\n", encoding="utf-8")
    last = commit(repo, "fix: final eligible")
    git(repo, "update-ref", "refs/remotes/origin/main", last)
    manifest = harness.generate_dataset(repo, last, tmp_path / "reasons")
    reasons = [case["diff_exclusion_reason"] for case in manifest["cases"]]
    assert reasons == ["none", "no_gold_tests", "no_input_paths", "diff_binary", "none"]
    assert manifest["diff_exclusion_counts"]["diff_binary"] == 1
    assert manifest["diff_impact_eligible_count"] == 1
    assert all(
        "input_patch_sha256" not in case
        for case in manifest["cases"]
        if not case["diff_impact_eligible"]
    )
    with pytest.raises(harness.HarnessError, match="no_input_paths"):
        harness.patch_bytes(repo, manifest["cases"][0]["parent"], last, [])


def test_prepare_runs_are_independent_and_source_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness, repo, sha, manifest, out = generated(tmp_path)
    monkeypatch.setattr(harness, "HERE", out)
    before = harness._source_identity(repo)
    work = tmp_path / "work"
    harness.worker_prepare(repo, work, manifest, "head")
    assert harness._source_identity(repo) == before
    clean = work / "runs" / sha / "head" / "find" / "first" / "repo"
    rerun = work / "runs" / sha / "head" / "find" / "repeat" / "repo"
    patched = work / "runs" / sha / "head" / "diff_impact" / "first" / "repo"
    assert clean != rerun and clean != patched
    assert harness._out(harness._git(clean, "status", "--porcelain")) == ""
    assert harness._out(harness._git(rerun, "status", "--porcelain")) == ""
    assert harness._out(harness._git(clean, "rev-parse", "HEAD")) == manifest["cases"][0]["parent"]
    assert harness._out(harness._git(patched, "diff", "--name-only", "HEAD", "--")) == "src/a.py"
    assert not (work / "runs" / sha / "head" / "find" / "first" / "input.patch").exists()
    assert (work / "patches" / sha / "input.patch").is_file()


def test_empty_json_ranking_scores_zero_and_command_errors_fail() -> None:
    harness = harness_module()
    scoring = harness.load_scoring()
    values = harness.score_ranking([], {"src/a.py"}, scoring)
    assert all(value == 0.0 for key, value in values.items() if key != "false_primary_rate")
    assert values["false_primary_rate"] == 1.0
    completed = subprocess.CompletedProcess(["tg"], 1, b'{"total_matches":0,"matches":[]}', b"")
    assert harness._tg_payload(completed, "find")["matches"] == []
    with pytest.raises(harness.HarnessError, match="command_failure"):
        harness._tg_payload(subprocess.CompletedProcess(["tg"], 2, b"{}", b""), "find")


@pytest.mark.parametrize("arm", ["find", "search_rank", "diff_impact"])
@pytest.mark.parametrize(
    "failure", [{"ok": False}, {"error": "failed"}, {"result_incomplete": True}]
)
def test_failed_payload_cannot_enter_scoring(arm: str, failure: dict[str, object]) -> None:
    harness = harness_module()
    payload = {"total_matches": 0, "matches": [], **failure}
    result = subprocess.CompletedProcess(["tg"], 1, json.dumps(payload).encode("utf-8"), b"")
    with pytest.raises(harness.HarnessError, match=r"command_failure|incomplete_result"):
        harness._tg_payload(result, arm)


def test_diff_impact_exit_one_cannot_score_perfect_metrics(tmp_path: Path) -> None:
    harness = harness_module()
    case = {"input_paths": ["src/a.py"], "gold_tests": ["tests/a.py"], "gold_files": ["src/a.py"]}
    payload = {
        "total_matches": 0,
        "matches": [],
        "changed_files": ["src/a.py"],
        "affected_tests": ["tests/a.py"],
        "affected_files": ["src/a.py"],
    }
    result = subprocess.CompletedProcess(["tg"], 1, json.dumps(payload).encode("utf-8"), b"")
    with pytest.raises(harness.HarnessError, match="command_failure:diff_impact"):
        harness.score_payload(
            harness._tg_payload(result, "diff_impact"), "diff_impact", case, tmp_path, None
        )


@pytest.mark.parametrize(
    "failure", [{"ok": False}, {"error": "failed"}, {"result_incomplete": True}]
)
def test_failed_diff_payload_cannot_score_perfect_sets(
    tmp_path: Path, failure: dict[str, object]
) -> None:
    harness = harness_module()
    case = {"input_paths": ["src/a.py"], "gold_tests": ["tests/a.py"], "gold_files": ["src/a.py"]}
    payload = {
        "changed_files": ["src/a.py"],
        "affected_tests": ["tests/a.py"],
        "affected_files": ["src/a.py"],
        **failure,
    }
    with pytest.raises(harness.HarnessError, match="incomplete_result:diff_impact"):
        harness.score_payload(payload, "diff_impact", case, tmp_path, None)


def test_container_absolute_paths_are_relative_to_case_repo(tmp_path: Path) -> None:
    harness = harness_module()
    assert harness._unique_paths(["/work/run/repo/src/a.py"], tmp_path / "host/repo") == [
        "src/a.py"
    ]


def test_standalone_gate_recomputes_evaluator_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = harness_module()
    report = {"evaluator_sha256": "stale", "environment": {}, "metrics": {}, "case_set": {}}
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps(report), encoding="utf-8")
    monkeypatch.setattr(harness, "evaluator_sha256", lambda: "current")
    assert "evaluator_changed" in harness.gate_report(report, baseline)


def test_ranked_paths_keep_emitted_order_and_normalize(tmp_path: Path) -> None:
    harness = harness_module()
    payload = {
        "matches": [
            {"file": str(tmp_path / "b.py")},
            {"file": "src/../a.py"},
            {"file": str(tmp_path / "b.py")},
        ]
    }
    assert harness.extract_ranking(payload, "find", tmp_path) == ["b.py", "a.py"]


def test_diff_not_applicable_is_counted_in_real_git_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = harness_module()
    repo, first = fixture_repo(tmp_path)
    (repo / "src/a.py").write_text("second change\n", encoding="utf-8")
    (repo / "tests/test_a.py").write_text("assert False\n", encoding="utf-8")
    last = commit(repo, "fix: second eligible")
    git(repo, "update-ref", "refs/remotes/origin/main", last)
    original_git = harness._git
    refused = False

    def one_refusal(path: Path, *args: str, **kwargs: object) -> object:
        nonlocal refused
        if args[:2] == ("apply", "--check") and not refused:
            refused = True
            return subprocess.CompletedProcess(["git"], 1, b"", b"fixture refusal")
        return original_git(path, *args, **kwargs)

    monkeypatch.setattr(harness, "_git", one_refusal)
    manifest = harness.generate_dataset(repo, last, tmp_path / "inapplicable")
    assert manifest["cases"][0]["sha"] == first
    assert manifest["cases"][0]["diff_exclusion_reason"] == "diff_not_applicable"
    assert "input_patch_sha256" not in manifest["cases"][0]
    assert manifest["diff_exclusion_counts"]["diff_not_applicable"] == 1


def test_manifest_rejects_path_traversal_before_prepare(tmp_path: Path) -> None:
    harness, _, _, manifest, _ = generated(tmp_path)
    manifest["cases"][0]["input_paths"] = ["../outside.py"]
    with pytest.raises(harness.HarnessError, match="invalid_manifest_paths"):
        harness.validate_manifest(manifest)


def test_manifest_sha_missing_fails_before_container_build(tmp_path: Path) -> None:
    harness, repo, _, manifest, _ = generated(tmp_path)
    harness.preflight_source(repo, manifest)
    manifest["cases"][0]["sha"] = "0" * 40
    with pytest.raises(harness.HarnessError, match="manifest_sha_missing"):
        harness.preflight_source(repo, manifest)


def test_prepare_rejects_patch_hash_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness, repo, sha, manifest, out = generated(tmp_path)
    monkeypatch.setattr(harness, "HERE", out)
    (out / "patches" / f"{sha}.patch").write_bytes(b"tampered")
    with pytest.raises(harness.HarnessError, match="patch_hash_mismatch"):
        harness.worker_prepare(repo, tmp_path / "work", manifest, "head")


def test_runtime_identity_requires_noneditable_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = harness_module()
    subject = tmp_path / "build" / "baseline"
    (subject / "venv" / "bin").mkdir(parents=True)
    (subject / "tg").write_bytes(b"binary")
    package_file = tmp_path / "source/tensor_grep/__init__.py"

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        if argv[1:] == ["--version"]:
            output = b"tg 1.126.0"
        elif argv[1] == "-c":
            output = json.dumps({
                "file": str(package_file),
                "version": "1.126.0",
            }).encode()
        else:
            output = b'{"version":"1.126.0"}'
        return subprocess.CompletedProcess(argv, 0, output, b"")

    monkeypatch.setattr(harness, "_run", fake_run)
    with pytest.raises(harness.HarnessError, match="editable_or_wrong_package_tree"):
        harness._runtime_identity("baseline", tmp_path / "build")
    package_file = subject / "venv/lib/python3.12/site-packages/tensor_grep/__init__.py"
    package_file.parent.mkdir(parents=True)
    package_file.write_text("", encoding="utf-8")
    _, _, identity = harness._runtime_identity("baseline", tmp_path / "build")
    assert identity["package_version"] == "1.126.0"


def test_protected_workflow_and_patch_bytes_contract() -> None:
    harness_module()
    workflow = (ROOT / ".github/workflows/git-history-retrieval-protection.yml").read_text(
        encoding="utf-8"
    )
    assert "pull_request_target:" in workflow
    assert "opened, synchronize, reopened, labeled, unlabeled" in workflow
    assert "protected_change_unlabelled" in workflow
    assert "actions/checkout" not in workflow
    assert "path === '.gitattributes'" in workflow
    attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8")
    assert "benchmarks/git_history_retrieval/patches/** -text -diff" in attributes
    gate_workflow = (ROOT / ".github/workflows/git-history-retrieval.yml").read_text(
        encoding="utf-8"
    )
    assert "continue-on-error:" in gate_workflow
    assert "baseline_update_pr" in gate_workflow
    assert "baseline_delta.md" in gate_workflow


def test_report_rejects_replaced_baseline_binary_after_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = harness_module()
    work = tmp_path / "work"
    binary = work / "build/baseline/tg"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"original baseline executable")
    runtime = {"binary_version": "9.9.9", "binary_sha256": harness._sha256(binary.read_bytes())}
    harness._write_json(work / "build/baseline/runtime.json", runtime)
    binary.write_bytes(b"replacement that reports version 9.9.9")
    sha = "a" * 40
    run_dir = work / "runs" / sha / "baseline/find/first"
    (run_dir / "repo").mkdir(parents=True)
    harness._write_json(
        run_dir / "case.json",
        {"sha": sha, "parent": "b" * 40, "query": "query", "tokens": ["query"]},
    )
    harness._write_json(work / "environment.json", {})
    executed: list[bytes] = []

    def fake_git(repo: Path, *args: str, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        output = b"b" * 40 if args[:2] == ("rev-parse", "HEAD") else b""
        return subprocess.CompletedProcess([], 0, output, b"")

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        executed.append(Path(argv[0]).read_bytes())
        return subprocess.CompletedProcess(argv, 1, b'{"total_matches":0,"matches":[]}', b"")

    monkeypatch.setattr(harness, "_git", fake_git)
    monkeypatch.setattr(harness, "_run", fake_run)
    row = harness.worker_evaluate(
        run_dir, work / "build", subject="baseline", arm="find", confined=False
    )
    assert executed == [b"replacement that reports version 9.9.9"]
    assert row["runtime"]["binary_sha256"] == runtime["binary_sha256"]
    monkeypatch.setattr(harness, "_selected_runs", lambda *args: [(run_dir, "find")])
    monkeypatch.setattr(harness, "load_scoring", lambda: None)
    monkeypatch.setattr(harness, "score_payload", lambda *args: {})
    monkeypatch.setattr(harness, "aggregate_scores", lambda *args: {})
    monkeypatch.setattr(harness, "evaluator_sha256", lambda: "fixed")
    manifest = {"cases": [{"sha": sha, "split": "heldout", "diff_impact_eligible": False}]}
    with pytest.raises(harness.HarnessError, match="runtime_binary_hash_mismatch:baseline"):
        harness._collect_report(manifest, "baseline", work)


def test_report_rejects_mutated_baseline_package_after_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = harness_module()
    work = tmp_path / "work"
    subject = work / "build/baseline"
    package_file = subject / "venv/lib/python3.12/site-packages/tensor_grep/__init__.py"
    package_file.parent.mkdir(parents=True)
    package_file.write_text("original package\n", encoding="utf-8")
    (subject / "tg").write_bytes(b"original binary")

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        if argv[1:] == ["--version"]:
            output = b"tg 1.126.0"
        elif argv[1] == "-c":
            output = json.dumps({"file": str(package_file), "version": "1.126.0"}).encode("utf-8")
        else:
            output = b'{"version":"1.126.0"}'
        return subprocess.CompletedProcess(argv, 0, output, b"")

    monkeypatch.setattr(harness, "_run", fake_run)
    runtime = harness._runtime_identity("baseline", work / "build")[2]
    harness._write_json(subject / "runtime.json", runtime)
    sha = "a" * 40
    run_dir = work / "runs" / sha / "baseline/find/first"
    harness._write_json(
        run_dir / "result.json",
        {"sha": sha, "arm": "find", "payload": {}, "prediction": [], "runtime": runtime},
    )
    monkeypatch.setattr(harness, "_selected_runs", lambda *args: [(run_dir, "find")])
    monkeypatch.setattr(harness, "load_scoring", lambda: None)
    monkeypatch.setattr(harness, "score_payload", lambda *args: {})
    monkeypatch.setattr(harness, "aggregate_scores", lambda *args: {})
    monkeypatch.setattr(harness, "evaluator_sha256", lambda: "fixed")
    harness._write_json(work / "environment.json", {})
    package_file.write_text("modified package\n", encoding="utf-8")
    manifest = {"cases": [{"sha": sha, "split": "heldout", "diff_impact_eligible": False}]}
    with pytest.raises(harness.HarnessError, match="runtime_package_hash_mismatch:baseline"):
        harness._collect_report(manifest, "baseline", work)


def test_ranked_search_uses_one_pattern_with_v1126_rank(tmp_path: Path) -> None:
    harness = harness_module()
    argv = harness._arm_argv(
        tmp_path / "tg", "search_rank", {"tokens": ["first", "second"]}, tmp_path
    )
    assert argv.count("-e") <= 1, "ranked_search_multiple_patterns_unsupported_flag"
    assert "--rank" in argv
    assert "first" in " ".join(argv) and "second" in " ".join(argv)


def test_case_checkout_contains_complete_parent_history(tmp_path: Path) -> None:
    harness = harness_module()
    source = tmp_path / "source"
    source.mkdir()
    git(source, "init", "-q", "-b", "main")
    git(source, "config", "user.email", "eval@example.test")
    git(source, "config", "user.name", "Eval")
    for index in range(3):
        (source / "history.txt").write_text(str(index), encoding="utf-8")
        commit(source, f"fix: ancestor {index}")
    parent = git(source, "rev-parse", "HEAD")
    (source / "history.txt").write_text("future", encoding="utf-8")
    sha = commit(source, "fix: heldout future")
    target = tmp_path / "parent-history"
    harness.create_parent_repo(source, parent, sha, target)
    assert harness._out(harness._git(target, "rev-list", "HEAD")) == harness._out(
        harness._git(source, "rev-list", parent)
    ), "parent_history_truncated"
    assert harness._git(target, "cat-file", "-e", sha, check=False).returncode != 0


def test_repeat_prediction_mismatch_fails_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = harness_module()
    work = tmp_path / "work"
    binary = work / "build/head/tg"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"head executable")
    runtime = {"binary_sha256": harness._sha256(binary.read_bytes())}
    harness._write_json(work / "build/head/runtime.json", runtime)
    sha = "a" * 40
    runs = [work / "runs" / sha / "head/find" / name for name in ("first", "repeat")]
    for run_dir, prediction in zip(runs, (["src/a.py"], ["src/b.py"]), strict=True):
        harness._write_json(
            run_dir / "result.json",
            {
                "sha": sha,
                "arm": "find",
                "payload": {},
                "prediction": prediction,
                "runtime": runtime,
            },
        )
    monkeypatch.setattr(harness, "_selected_runs", lambda *args: [(run, "find") for run in runs])
    monkeypatch.setattr(harness, "load_scoring", lambda: None)
    monkeypatch.setattr(harness, "score_payload", lambda *args: {})
    manifest = {"cases": [{"sha": sha, "split": "heldout", "diff_impact_eligible": False}]}
    with pytest.raises(harness.HarnessError, match=f"determinism_mismatch:{sha}:find"):
        harness._collect_report(manifest, "head", work)


def test_baseline_build_rejects_wrong_binary_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = harness_module()
    subject = tmp_path / "build/baseline"
    package_file = subject / "venv/lib/python3.12/site-packages/tensor_grep/__init__.py"
    package_file.parent.mkdir(parents=True)
    (subject / "tg").write_bytes(b"replacement")

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        if argv[1:] == ["--version"]:
            output = b"tg 9.9.9"
        elif argv[1] == "-c":
            output = json.dumps({"file": str(package_file), "version": "9.9.9"}).encode("utf-8")
        else:
            output = b'{"version":"9.9.9"}'
        return subprocess.CompletedProcess(argv, 0, output, b"")

    monkeypatch.setattr(harness, "_run", fake_run)
    with pytest.raises(harness.HarnessError, match="baseline_binary_version_mismatch"):
        harness._runtime_identity("baseline", tmp_path / "build")


def test_head_build_cannot_replace_baseline_executable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = harness_module()
    work = tmp_path / "work"
    for subject in ("baseline", "head"):
        (work / "build" / subject / "tree").mkdir(parents=True)

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        tree = Path(kwargs["cwd"])
        target = tree.parent
        if argv[0] == "cargo":
            built = target / "cargo-target/release/tg"
            built.parent.mkdir(parents=True)
            built.write_bytes(b"baseline" if target.name == "baseline" else b"head")
            if target.name == "head":
                (work / "build/baseline/tg").write_bytes(b"replacement")
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    def fake_identity(subject: str, build: Path) -> tuple[Path, Path, dict[str, str]]:
        target = build / subject
        return (
            target / "tg",
            target / "venv",
            {
                "binary_version": "1.126.0",
                "binary_sha256": harness._sha256((target / "tg").read_bytes()),
            },
        )

    monkeypatch.setattr(harness, "_run", fake_run)
    monkeypatch.setattr(harness, "_runtime_identity", fake_identity)
    monkeypatch.setattr(harness, "environment_identity", lambda: {})
    monkeypatch.setattr(
        harness, "ensure_harness_venv", lambda build: build / "harness-venv/bin/python"
    )
    monkeypatch.setattr(harness, "_read_only_tree", lambda *args: None)
    with pytest.raises(harness.HarnessError, match="runtime_binary_hash_mismatch:baseline"):
        harness.worker_build(tmp_path, work)


def test_head_build_cannot_replace_baseline_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = harness_module()
    work = tmp_path / "work"
    package_relpath = "lib/python3.12/site-packages/tensor_grep/__init__.py"
    for subject in ("baseline", "head"):
        (work / "build" / subject / "tree").mkdir(parents=True)

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        target = Path(kwargs["cwd"]).parent
        if argv[0] == "uv":
            package_file = target / "venv" / package_relpath
            package_file.parent.mkdir(parents=True)
            package_file.write_bytes(b"original baseline package")
        elif argv[0] == "cargo":
            built = target / "cargo-target/release/tg"
            built.parent.mkdir(parents=True)
            built.write_bytes(target.name.encode("utf-8"))
            if target.name == "head":
                (work / "build/baseline/venv" / package_relpath).write_bytes(
                    b"modified baseline package"
                )
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    def fake_identity(subject: str, build: Path) -> tuple[Path, Path, dict[str, str]]:
        target = build / subject
        content = (target / "venv" / package_relpath).read_bytes()
        package_digest = hashlib.sha256(
            b"__init__.py\0" + hashlib.sha256(content).digest()
        ).hexdigest()
        return (
            target / "tg",
            target / "venv",
            {
                "binary_sha256": harness._sha256((target / "tg").read_bytes()),
                "package_relpath": package_relpath,
                "package_sha256": package_digest,
            },
        )

    monkeypatch.setattr(harness, "_run", fake_run)
    monkeypatch.setattr(harness, "_runtime_identity", fake_identity)
    monkeypatch.setattr(harness, "environment_identity", lambda: {})
    monkeypatch.setattr(
        harness, "ensure_harness_venv", lambda build: build / "harness-venv/bin/python"
    )
    monkeypatch.setattr(harness, "_read_only_tree", lambda *args: None)
    with pytest.raises(harness.HarnessError, match="runtime_package_hash_mismatch:baseline"):
        harness.worker_build(tmp_path, work)


def test_ensure_harness_venv_is_stdlib_only(tmp_path: Path) -> None:
    harness = harness_module()
    build = tmp_path / "build"
    python = harness.ensure_harness_venv(build)
    venv = build / "harness-venv"
    assert python.is_file()
    prefix = subprocess.run(
        [str(python), "-c", "import sys; print(sys.prefix)"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert Path(prefix).resolve() == venv.resolve()
    assert "include-system-site-packages = false" in (venv / "pyvenv.cfg").read_text(
        encoding="utf-8"
    )
    assert not any(venv.rglob("tensor_grep"))


def test_worker_prefers_harness_venv_interpreter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    worker_path = harness_root() / "benchmarks/git_history_retrieval/worker.py"
    spec = importlib.util.spec_from_file_location("git_history_retrieval_worker", worker_path)
    assert spec and spec.loader
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)
    harness_venv = tmp_path / "harness-venv/bin/python"
    harness_venv.parent.mkdir(parents=True)
    harness_venv.write_text("", encoding="utf-8")
    monkeypatch.setattr(worker, "HARNESS_VENV_PYTHON", harness_venv)
    launched: list[str] = []
    monkeypatch.setattr(worker.sys, "argv", [str(worker_path), "evaluate"])
    monkeypatch.setattr(worker.os, "execv", lambda python, argv: launched.append(python))
    worker.main()
    assert launched == [str(harness_venv)], "evaluator_must_use_harness_venv"


def test_protected_label_check_has_required_ruleset_documentation() -> None:
    workflow = (
        harness_root() / ".github/workflows/git-history-retrieval-protection.yml"
    ).read_text(encoding="utf-8")
    docs = (harness_root() / "docs/benchmarks.md").read_text(encoding="utf-8")
    assert "protected-change-label:" in workflow
    assert "!labels.includes('eval-baseline-update')" in workflow
    assert "path === 'docs/benchmarks.md'" in workflow
    assert "Git history retrieval protection / protected-change-label" in docs
    assert "Require status checks to pass" in docs
    assert "eval-baseline-update" in docs


def test_protection_workflow_requires_label_for_every_protected_pr() -> None:
    workflow = (
        harness_root() / ".github/workflows/git-history-retrieval-protection.yml"
    ).read_text(encoding="utf-8")
    assert "pull_request_target:" in workflow
    assert "types: [opened, synchronize, reopened, labeled, unlabeled]" in workflow
    assert "continue-on-error" not in workflow
    assert "path === '.github/workflows/git-history-retrieval-protection.yml'" in workflow
    assert (
        "const labels = context.payload.pull_request.labels.map((label) => label.name);" in workflow
    )
    assert "if (changed.length && !labels.includes('eval-baseline-update'))" in workflow
    assert "core.setFailed('protected_change_unlabelled: '" in workflow


def test_prepare_mounts_patches_at_worker_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness, source, _, manifest, out = generated(tmp_path)
    harness._write_json(out / "manifest.json", manifest)
    monkeypatch.setattr(harness, "preflight_source", lambda *args: None)
    monkeypatch.setattr(
        harness, "_run", lambda *args, **kwargs: subprocess.CompletedProcess([], 0, b"", b"")
    )

    def fake_checkout(source: Path, revision: str, target: Path) -> None:
        (target / ".git").mkdir(parents=True)

    def inspect_mount(
        image: str,
        *,
        network: str,
        mounts: list[tuple[Path, str, str]],
        arguments: list[str],
        read_only: bool = False,
    ) -> None:
        if arguments[0] == "prepare":
            assert any(
                host == out / "patches"
                and container == "/opt/checkout/benchmarks/git_history_retrieval/patches"
                and mode == "readonly"
                for host, container, mode in mounts
            ), "patch_mount_misses_worker_here"
            raise harness.HarnessError("mount_checked")

    monkeypatch.setattr(harness, "_revision_checkout", fake_checkout)
    monkeypatch.setattr(harness, "_docker_run", inspect_mount)
    with pytest.raises(harness.HarnessError, match="mount_checked"):
        harness.orchestrate(source, out / "manifest.json", tmp_path / "report.json", subject="head")


def test_environment_identity_changes_with_base_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = harness_module()

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        output = b"git=1\npython3=2\n" if argv[0] == "dpkg-query" else b"pinned"
        return subprocess.CompletedProcess(argv, 0, output, b"")

    monkeypatch.setattr(harness, "_run", fake_run)
    monkeypatch.setattr(harness, "HERE", tmp_path)
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("FROM ubuntu:24.04@sha256:" + "a" * 64 + "\n", encoding="utf-8")
    first = harness.environment_identity()
    dockerfile.write_text("FROM ubuntu:24.04@sha256:" + "b" * 64 + "\n", encoding="utf-8")
    second = harness.environment_identity()
    assert first["base_image_digest"] != second["base_image_digest"]
    assert first["dockerfile_sha256"] != second["dockerfile_sha256"]


def test_environment_identity_records_executed_toolchains(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = harness_module()
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("FROM ubuntu:24.04@sha256:" + "a" * 64 + "\n", encoding="utf-8")
    monkeypatch.setattr(harness, "HERE", tmp_path)

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        output = b"git=1\npython3=2\n" if argv[0] == "dpkg-query" else argv[0].encode("utf-8")
        return subprocess.CompletedProcess(argv, 0, output, b"")

    monkeypatch.setattr(harness, "_run", fake_run)
    identity = harness.environment_identity()
    assert identity["cargo_version"] == "cargo"
    assert identity["python_version"] == "python3"
    assert identity["git_version"] == "git"


def test_docker_mounts_keep_only_case_writable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = harness_module()
    case = tmp_path / "case"
    build = tmp_path / "build"
    case.mkdir()
    build.mkdir()
    calls: list[list[str]] = []

    def capture(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    monkeypatch.setattr(harness, "_run", capture)
    harness._docker_run(
        "image",
        network="none",
        read_only=True,
        mounts=[(case, "/work/run", "rw"), (build, "/work/build", "readonly")],
        arguments=["evaluate"],
    )
    argv = calls[0]
    assert "--read-only" in argv and argv[argv.index("--network") + 1] == "none"
    mounts = [argv[index + 1] for index, part in enumerate(argv) if part == "--mount"]
    assert len(mounts) == 2
    assert mounts[0].endswith("dst=/work/run")
    assert mounts[1].endswith("dst=/work/build,readonly")
    assert "/source" not in " ".join(argv)


def test_baseline_diff_impact_incompatibility_blocks_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness, repo, _, manifest, out = generated(tmp_path)
    baseline_tg = tmp_path / "v1.126.0-tg"
    original_run = harness._run
    seen: list[str] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        if argv[0] != str(baseline_tg):
            return original_run(argv, **kwargs)
        arm = argv[1]
        seen.append(arm)
        if arm == "diff-impact":
            return subprocess.CompletedProcess(argv, 2, b"{}", b"unsupported")
        return subprocess.CompletedProcess(argv, 1, b'{"total_matches":0,"matches":[]}', b"")

    monkeypatch.setattr(harness, "_run", fake_run)
    with pytest.raises(harness.HarnessError, match="baseline_arm_incompatible:diff_impact"):
        harness._compatibility_check(manifest, repo, out / "patches", baseline_tg)
    assert seen == ["find", "search", "diff-impact"]


def test_baseline_delta_reports_each_metric_with_direction() -> None:
    harness = harness_module()
    metrics = {key: {**spec, "value": 0.5} for key, spec in harness.metric_specs().items()}
    previous = {"metrics": metrics}
    updated = {"metrics": {key: {**value, "value": 0.75} for key, value in metrics.items()}}
    markdown = harness.metric_delta_markdown(previous, updated)
    assert len(markdown.splitlines()) == len(metrics) + 2
    assert (
        "| find.false_primary_rate | 0.500000 | 0.750000 | +0.250000 | lower_better | False |"
        in markdown
    )


def test_advisory_worker_cannot_write_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = harness_module()
    seen: list[bool] = []

    def fake_evaluate(*args: object, **kwargs: object) -> dict[str, object]:
        seen.append(bool(kwargs["confined"]))
        return {}

    monkeypatch.setattr(harness, "worker_evaluate", fake_evaluate)
    assert (
        harness.main([
            "worker",
            "evaluate",
            "--subject",
            "head",
            "--arm",
            "find",
            "--advisory",
            "--run-dir",
            str(tmp_path),
            "--build-dir",
            str(tmp_path),
        ])
        == 0
    )
    assert seen == [False]
    with pytest.raises(SystemExit, match="2"):
        harness.main([
            "worker",
            "evaluate",
            "--advisory",
            "--baseline-output",
            str(tmp_path / "baseline.json"),
        ])


def test_subject_run_cannot_read_gold_or_heldout_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness, source, sha, manifest, out = generated(tmp_path)
    monkeypatch.setattr(harness, "HERE", out)
    work = tmp_path / "work"
    harness.worker_prepare(source, work, manifest, "head")
    run_dir = work / "runs" / sha / "head" / "find" / "first"
    exposed = json.loads((run_dir / "case.json").read_text(encoding="utf-8"))
    assert not {"gold_files", "gold_tests", "input_patch_sha256"} & exposed.keys(), (
        "subject_case_exposes_gold"
    )
    assert harness._git(run_dir / "repo", "cat-file", "-e", sha, check=False).returncode != 0, (
        "subject_repo_exposes_heldout_commit"
    )


def test_evaluation_image_does_not_copy_frozen_dataset() -> None:
    dockerfile = (harness_root() / "benchmarks/git_history_retrieval/Dockerfile").read_text(
        encoding="utf-8"
    )
    sources = {
        line.split()[1]
        for line in dockerfile.splitlines()
        if line.startswith("COPY benchmarks/git_history_retrieval/")
    }
    assert sources == {
        "benchmarks/git_history_retrieval/harness.py",
        "benchmarks/git_history_retrieval/worker.py",
        "benchmarks/git_history_retrieval/Dockerfile",
    }, "image_copies_heldout_patches"


@pytest.mark.skipif(
    shutil.which("docker") is None,
    reason="container confinement check requires Docker",
)
def test_confined_subject_cannot_read_labels_or_heldout_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness, source, sha, manifest, out = generated(tmp_path)
    monkeypatch.setattr(harness, "HERE", out)
    work = tmp_path / "work"
    harness.worker_prepare(source, work, manifest, "head")
    run_dir = work / "runs" / sha / "head" / "find" / "first"
    build = work / "build"
    build.mkdir()
    image = "ubuntu:24.04@sha256:f610ab94648195aa356059f5b41d6085c9d4d903c072430cdd1af7bdb646106b"
    script = (
        "test ! -e /source && test ! -e /work/patches && "
        "! grep -q gold_files /work/run/case.json && "
        "test ! -e /work/run/repo/.git/shallow && "
        "! touch /work/build/denied"
    )
    harness._docker_run(
        image,
        network="none",
        read_only=True,
        mounts=[(run_dir, "/work/run", "rw"), (build, "/work/build", "readonly")],
        arguments=["sh", "-c", script],
    )
    assert harness._git(run_dir / "repo", "cat-file", "-e", sha, check=False).returncode != 0


def test_build_checkout_does_not_retain_prior_history(tmp_path: Path) -> None:
    harness, source, sha, _, _ = generated(tmp_path)
    target = tmp_path / "checkout"
    harness._revision_checkout(source, "HEAD", target)
    parent = git(source, "rev-parse", f"{sha}^")
    assert harness._git(target, "cat-file", "-e", parent, check=False).returncode != 0, (
        "build_tree_exposes_prior_history"
    )


def test_build_container_receives_sanitized_snapshots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness, source, _, manifest, out = generated(tmp_path)
    harness._write_json(out / "manifest.json", manifest)
    monkeypatch.setattr(harness, "preflight_source", lambda *args: None)
    monkeypatch.setattr(
        harness,
        "_run",
        lambda *args, **kwargs: subprocess.CompletedProcess([], 0, b"", b""),
    )

    def fake_checkout(source: Path, revision: str, target: Path) -> None:
        (target / ".git").mkdir(parents=True)
        dataset = target / "benchmarks/git_history_retrieval"
        (dataset / "patches").mkdir(parents=True)
        (dataset / "manifest.json").write_text("gold", encoding="utf-8")
        (dataset / "baseline.json").write_text("gold", encoding="utf-8")
        (dataset / "patches/gold.patch").write_text("gold", encoding="utf-8")

    def inspect_build(
        image: str,
        *,
        network: str,
        mounts: list[tuple[Path, str, str]],
        arguments: list[str],
        read_only: bool = False,
    ) -> None:
        assert arguments == ["build"]
        assert all(container != "/source" for _, container, _ in mounts), (
            "build_mount_exposes_full_source"
        )
        build = mounts[0][0] / "build"
        for subject in ("baseline", "head"):
            tree = build / subject / "tree"
            assert not (tree / ".git").exists(), "build_mount_exposes_history"
            assert not (tree / "benchmarks/git_history_retrieval/manifest.json").exists(), (
                "build_mount_exposes_gold"
            )
            assert not (tree / "benchmarks/git_history_retrieval/patches").exists(), (
                "build_mount_exposes_patches"
            )
        raise harness.HarnessError("build_snapshot_checked")

    monkeypatch.setattr(harness, "_revision_checkout", fake_checkout)
    monkeypatch.setattr(harness, "_docker_run", inspect_build)
    with pytest.raises(harness.HarnessError, match="build_snapshot_checked"):
        harness.orchestrate(source, out / "manifest.json", tmp_path / "report.json", subject="head")


def test_evaluator_interpreter_is_outside_subject_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    worker_path = harness_root() / "benchmarks/git_history_retrieval/worker.py"
    spec = importlib.util.spec_from_file_location("git_history_retrieval_worker", worker_path)
    assert spec and spec.loader
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)
    marker = tmp_path / "pth-ran"
    site = tmp_path / "build/harness-venv/lib/python3.12/site-packages"
    site.mkdir(parents=True)
    (site / "subject.pth").write_text(
        f"import pathlib; pathlib.Path({str(marker)!r}).write_text('poison')\n",
        encoding="utf-8",
    )
    launched: list[str] = []
    monkeypatch.setattr(worker.sys, "argv", [str(worker_path), "evaluate"])
    monkeypatch.setattr(worker.os, "execv", lambda python, argv: launched.append(python))
    worker.main()
    assert launched == [sys.executable], "subject_pth_can_run_before_evaluator_startup"
    assert not marker.exists(), "subject_pth_executed"
    harness = harness_module()
    fresh_build = tmp_path / "fresh-harness-venv"
    harness.ensure_harness_venv(fresh_build)
    harness_venv_site = fresh_build / "harness-venv/lib/python3.12/site-packages"
    if harness_venv_site.exists():
        assert not list(harness_venv_site.glob("*.pth")), "subject_pth_survives_venv_creation"


def test_subject_pth_positive_control_does_not_run_in_evaluator(tmp_path: Path) -> None:
    worker = harness_root() / "benchmarks/git_history_retrieval/worker.py"
    site = tmp_path / "build/subject/venv/lib/python3.12/site-packages"
    site.mkdir(parents=True)
    marker = tmp_path / "pth-executed"
    (site / "subject.pth").write_text(
        f"import pathlib; pathlib.Path({str(marker)!r}).write_text('poison')\n",
        encoding="utf-8",
    )
    probe = subprocess.run(
        [sys.executable, "-c", f"import site; site.addsitedir({str(site)!r})"],
        capture_output=True,
        check=False,
    )
    assert probe.returncode == 0, probe.stderr.decode("utf-8", errors="replace")
    assert marker.read_text(encoding="utf-8") == "poison"
    marker.unlink()
    subprocess.run([sys.executable, str(worker), "evaluate"], capture_output=True, check=False)
    assert not marker.exists(), "subject_pth_executed_in_evaluator"


def test_subject_pth_isolation_runs_successful_worker_subprocess(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    worker = tmp_path / "worker.py"
    shutil.copy2(harness_root() / "benchmarks/git_history_retrieval/worker.py", worker)
    result_file = tmp_path / "worker-result"
    (tmp_path / "harness.py").write_text(
        "import pathlib,sys\nassert sys.flags.isolated, 'evaluator_interpreter_not_isolated'\n"
        "pathlib.Path(sys.argv[-1]).write_text('evaluated')\n",
        encoding="utf-8",
    )
    site = tmp_path / "build/subject/venv/lib/python3.12/site-packages"
    site.mkdir(parents=True)
    marker = tmp_path / "pth-executed"
    (site / "subject.pth").write_text(
        f"import pathlib; pathlib.Path({str(marker)!r}).write_text('poison')\n",
        encoding="utf-8",
    )
    probe = subprocess.run(
        [sys.executable, "-c", f"import site; site.addsitedir({str(site)!r})"],
        capture_output=True,
        check=False,
    )
    assert probe.returncode == 0, probe.stderr.decode("utf-8", errors="replace")
    assert marker.read_text(encoding="utf-8") == "poison"
    marker.unlink()
    spec = importlib.util.spec_from_file_location("git_history_retrieval_worker_probe", worker)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    launched: list[str] = []
    monkeypatch.setattr(module.sys, "argv", [str(worker), "evaluate", str(result_file)])
    monkeypatch.setattr(module.os, "execv", lambda python, argv: launched.extend(argv))
    module.main()
    assert launched[0] == sys.executable
    evaluation = subprocess.run(launched, capture_output=True, check=False)
    assert evaluation.returncode == 0, evaluation.stderr.decode("utf-8", errors="replace")
    assert result_file.read_text(encoding="utf-8") == "evaluated"
    assert not marker.exists(), "subject_pth_executed_in_evaluator"


@pytest.mark.parametrize(
    ("status", "filename", "previous_filename"),
    [
        ("renamed", "safe/new.py", "benchmarks/git_history_retrieval/manifest.json"),
        ("modified", "benchmarks/git_history_retrieval/harness.py", None),
    ],
)
def test_protected_workflow_detects_protected_paths(
    status: str, filename: str, previous_filename: str | None
) -> None:
    workflow = (
        harness_root() / ".github/workflows/git-history-retrieval-protection.yml"
    ).read_text(encoding="utf-8")
    script = workflow.split("          script: |\n", 1)[1]
    script = "\n".join(line[12:] for line in script.splitlines())
    changed = json.dumps({
        "filename": filename,
        "previous_filename": previous_filename,
        "status": status,
    })
    if shutil.which("node") is None:
        pytest.skip("node is required to evaluate the protection workflow predicate")
    probe = (
        "let failure = ''; const core = {setFailed: x => failure = x}; "
        "const context = {repo: {owner:'o',repo:'r'}, issue:{number:1}, "
        "payload:{pull_request:{labels:[]}}}; "
        f"const github = {{paginate: async () => [{changed}], "
        "rest:{pulls:{listFiles:{}}}}; "
        "(async () => {" + script + "\nif (!failure.includes('protected_change_unlabelled')) "
        "throw new Error('protected_change_accepted');})().catch(e=>{console.error(e.message);process.exit(1)});"
    )
    result = subprocess.run(["node", "-e", probe], capture_output=True, check=False)
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")


@pytest.mark.parametrize("protected", [False, True])
def test_protection_workflow_fails_without_required_main_check(protected: bool) -> None:
    workflow = (
        harness_root() / ".github/workflows/git-history-retrieval-protection.yml"
    ).read_text(encoding="utf-8")
    script = workflow.split("          script: |\n", 1)[1]
    script = "\n".join(line[12:] for line in script.splitlines())
    if shutil.which("node") is None:
        pytest.skip("node is required to evaluate the protection workflow predicate")
    probe = (
        "let failure = ''; const core = {setFailed: x => failure = x}; "
        "const context = {repo: {owner:'o',repo:'r'}, issue:{number:1}, "
        "payload:{pull_request:{labels:[]}}}; "
        "const github = {paginate: async () => [], rest:{pulls:{listFiles:{}}, repos:{"
        f"getBranch: async () => ({{data:{{protected:{str(protected).lower()}}}}}), "
        "getRequiredStatusChecks: async () => ({data:{contexts:[],checks:[]}})}}}; "
        "(async () => {" + script + "\nif (!failure.includes('main_protection_unenforced')) "
        "throw new Error('unprotected_main_accepted');})().catch(e=>{console.error(e.message);process.exit(1)});"
    )
    result = subprocess.run(["node", "-e", probe], capture_output=True, check=False)
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")


def test_protection_workflow_accepts_required_main_check() -> None:
    workflow = (
        harness_root() / ".github/workflows/git-history-retrieval-protection.yml"
    ).read_text(encoding="utf-8")
    script = workflow.split("          script: |\n", 1)[1]
    script = "\n".join(line[12:] for line in script.splitlines())
    if shutil.which("node") is None:
        pytest.skip("node is required to evaluate the protection workflow predicate")
    expected = "Git history retrieval protection / protected-change-label"
    probe = (
        "let failure = ''; const core = {setFailed: x => failure = x}; "
        "const context = {repo: {owner:'o',repo:'r'}, issue:{number:1}, "
        "payload:{pull_request:{labels:[]}}}; "
        "const github = {paginate: async () => [], rest:{pulls:{listFiles:{}}, repos:{"
        "getBranch: async () => ({data:{protected:true}}), "
        f"getRequiredStatusChecks: async () => ({{data:{{contexts:[{json.dumps(expected)}],checks:[]}}}})"
        "}}}; "
        "(async () => {" + script + "\nif (failure) "
        "throw new Error(failure);})().catch(e=>{console.error(e.message);process.exit(1)});"
    )
    result = subprocess.run(["node", "-e", probe], capture_output=True, check=False)
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")


def test_host_cleanup_ignores_unremovable_build_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness, source, _, manifest, out = generated(tmp_path)
    harness._write_json(out / "manifest.json", manifest)

    class UnremovableTree:
        def __init__(self, *, prefix: str, ignore_cleanup_errors: bool = False) -> None:
            self.ignore_cleanup_errors = ignore_cleanup_errors

        def __enter__(self) -> str:
            work = tmp_path / "root-owned-work"
            work.mkdir()
            return str(work)

        def __exit__(self, *args: object) -> None:
            if not self.ignore_cleanup_errors:
                raise PermissionError("root_owned_build_cleanup_can_fail_gate")

    def stop(*args: object, **kwargs: object) -> None:
        raise harness.HarnessError("stop_after_cleanup_probe")

    monkeypatch.setattr(harness.tempfile, "TemporaryDirectory", UnremovableTree)
    monkeypatch.setattr(harness, "preflight_source", lambda *args: None)
    monkeypatch.setattr(
        harness, "_run", lambda *args, **kwargs: subprocess.CompletedProcess([], 0, b"", b"")
    )
    monkeypatch.setattr(harness, "_revision_checkout", stop)
    monkeypatch.setattr(harness, "_docker_run", stop)
    with pytest.raises(harness.HarnessError, match="stop_after_cleanup_probe"):
        harness.orchestrate(source, out / "manifest.json", tmp_path / "report.json", subject="head")


def test_host_cleanup_preserves_evaluation_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness, source, _, manifest, out = generated(tmp_path)
    harness._write_json(out / "manifest.json", manifest)

    class RootOwnedTree:
        def __init__(self, *, prefix: str, ignore_cleanup_errors: bool = False) -> None:
            self.ignore_cleanup_errors = ignore_cleanup_errors

        def __enter__(self) -> str:
            work = tmp_path / "root-owned-work"
            work.mkdir()
            return str(work)

        def __exit__(self, *args: object) -> None:
            if not self.ignore_cleanup_errors:
                raise PermissionError("root_owned_cleanup_masked_evaluation_failure")

    def stop(*args: object, **kwargs: object) -> None:
        raise harness.HarnessError("evaluation_failure")

    monkeypatch.setattr(harness.tempfile, "TemporaryDirectory", RootOwnedTree)
    monkeypatch.setattr(harness, "preflight_source", lambda *args: None)
    monkeypatch.setattr(
        harness, "_run", lambda *args, **kwargs: subprocess.CompletedProcess([], 0, b"", b"")
    )
    monkeypatch.setattr(harness, "_revision_checkout", stop)
    with pytest.raises(harness.HarnessError, match="evaluation_failure"):
        harness.orchestrate(source, out / "manifest.json", tmp_path / "report.json", subject="head")


def test_prepare_normalizes_case_tree_mtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness, source, sha, manifest, out = generated(tmp_path)
    monkeypatch.setattr(harness, "HERE", out)
    work = tmp_path / "work"
    harness.worker_prepare(source, work, manifest, "head")
    repo = work / "runs" / sha / "head" / "find" / "first" / "repo"
    timestamp = int(git(source, "show", "-s", "--format=%ct", manifest["cases"][0]["parent"]))
    assert int((repo / "src/a.py").stat().st_mtime) == timestamp, "mtime_normalization_missing"


def test_confined_evaluation_runs_tg_writer_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = harness_module()
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    harness._write_json(
        run_dir / "case.json",
        {
            "parent": "a" * 40,
            "sha": "b" * 40,
            "query": "query",
            "tokens": ["query"],
            "gold_files": ["src/a.py"],
            "gold_tests": ["tests/a.py"],
            "input_paths": ["src/a.py"],
        },
    )
    (run_dir / "repo").mkdir()
    (tmp_path / "build/head/venv").mkdir(parents=True)
    harness._write_json(tmp_path / "build/head/runtime.json", {})
    seen: list[str] = []
    monkeypatch.setattr(harness, "confinement_controls", lambda *args: None)
    monkeypatch.setattr(harness, "tg_launched_write_control", lambda *args: seen.append("writer"))

    def fake_git(repo: Path, *args: str, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        output = b"a" * 40 if args[:2] == ("rev-parse", "HEAD") else b""
        return subprocess.CompletedProcess([], 0, output, b"")

    monkeypatch.setattr(harness, "_git", fake_git)
    monkeypatch.setattr(
        harness,
        "_run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            [], 1, b'{"total_matches":0,"matches":[]}', b""
        ),
    )
    harness.worker_evaluate(run_dir, tmp_path / "build", subject="head", arm="find", confined=True)
    assert seen == ["writer"], "tg_launched_write_control_missing"


def test_generate_command_enforces_compatibility(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = harness_module()
    seen: list[str] = []
    monkeypatch.setattr(harness, "generate_dataset", lambda *args, **kwargs: {"cases": []})
    monkeypatch.setattr(harness, "_compatibility_check", lambda *args: seen.append("compatibility"))
    harness.main([
        "generate",
        "--repo",
        str(tmp_path),
        "--cutoff",
        "HEAD",
        "--out-dir",
        str(tmp_path),
        "--baseline-tg",
        str(tmp_path / "tg"),
    ])
    assert seen == ["compatibility"], "generation_compatibility_check_missing"

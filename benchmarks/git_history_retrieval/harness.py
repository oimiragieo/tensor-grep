"""Frozen git-history retrieval evaluation and container worker."""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import json
import math
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
SCORING = ROOT / "src/tensor_grep/core/retrieval_scoring.py"
ARMS = ("find", "search_rank", "diff_impact")
SEARCH_ARMS = ("find", "search_rank")
DIFF_REASONS = ("none", "no_gold_tests", "no_input_paths", "diff_binary", "diff_not_applicable")
EXCLUSION_REASONS = ("empty_gold", "empty_query", "no_tokens", "not_single_parent")
PATH_ENV = (
    "TG_CROSS_ENCODER_DIR",
    "TG_CROSSOVER_CACHE_DIR",
    "TG_CROSSOVER_CONFIG_PATH",
    "TG_CUDA_PTX_CACHE_DIR",
    "TG_DAEMON_SECRET_DIR",
    "TG_INDEX_LOCK_DEBUG_LOG_DIR",
    "TG_MCP_ROOT",
    "TG_RERANK_MODEL_DIR",
    "TG_ROUTE_TELEMETRY_PATH",
    "TG_SEMANTIC_INDEX_DIR",
    "TG_SEMANTIC_MODEL_DIR",
)
RUNS = ("first", "repeat")


class HarnessError(RuntimeError):
    pass


def _run(
    argv: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    input_bytes: bytes | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    result = subprocess.run(
        argv, cwd=cwd, env=env, input=input_bytes, capture_output=True, check=False
    )
    if check and result.returncode:
        message = result.stderr.decode("utf-8", errors="replace").strip()
        raise HarnessError(f"command_failure: {argv[0]} exited {result.returncode}: {message}")
    return result


def _git(
    repo: Path,
    *args: str,
    input_bytes: bytes | None = None,
    check: bool = True,
    safe_source: Path | None = None,
) -> subprocess.CompletedProcess[bytes]:
    env = os.environ.copy()
    env.update(
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_TERMINAL_PROMPT="0",
        GIT_NO_REPLACE_OBJECTS="1",
        GIT_CONFIG_COUNT="2" if safe_source is not None else "1",
        GIT_CONFIG_KEY_0="safe.directory",
        GIT_CONFIG_VALUE_0=str(repo.resolve()),
    )
    if safe_source is not None:
        env.update(GIT_CONFIG_KEY_1="safe.directory", GIT_CONFIG_VALUE_1=str(safe_source.resolve()))
    return _run(["git", "-C", str(repo), *args], env=env, input_bytes=input_bytes, check=check)


def _out(result: subprocess.CompletedProcess[bytes]) -> str:
    return result.stdout.decode("utf-8", errors="strict").strip()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _package_sha256(package_file: Path, venv: Path) -> str:
    package = package_file.parent
    if package.is_symlink() or package_file.is_symlink():
        raise HarnessError("runtime_package_symlink")
    if package.name != "tensor_grep" or not package_file.is_file():
        raise HarnessError("runtime_package_missing")
    if not package.resolve().is_relative_to(venv.resolve()):
        raise HarnessError("runtime_package_outside_venv")
    digest = hashlib.sha256()
    paths = sorted(package.rglob("*"))
    if not paths:
        raise HarnessError("runtime_package_missing")
    for path in paths:
        if path.is_symlink():
            raise HarnessError("runtime_package_symlink")
        if path.is_file():
            digest.update(path.relative_to(package).as_posix().encode("utf-8"))
            digest.update(b"\0")
            digest.update(bytes.fromhex(_sha256(path.read_bytes())))
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise HarnessError(f"malformed_json: {path}")
    return value


def _tg_payload(result: subprocess.CompletedProcess[bytes], arm: str) -> dict[str, Any]:
    try:
        value = json.loads(result.stdout.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise HarnessError(f"malformed_json:{arm}") from error
    if not isinstance(value, dict):
        raise HarnessError(f"malformed_json:{arm}")
    if value.get("ok") is False or value.get("error") or value.get("result_incomplete") is True:
        raise HarnessError(f"incomplete_result:{arm}")
    no_matches = (
        arm in SEARCH_ARMS
        and result.returncode == 1
        and value.get("total_matches") == 0
        and value.get("matches") == []
    )
    if result.returncode and not no_matches:
        raise HarnessError(f"command_failure:{arm}:exit={result.returncode}")
    return value


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def evaluator_sha256(root: Path = ROOT) -> str:
    digest = hashlib.sha256()
    for path in (
        root / "benchmarks/git_history_retrieval/harness.py",
        root / "benchmarks/git_history_retrieval/worker.py",
        root / "benchmarks/git_history_retrieval/manifest.json",
        root / "benchmarks/git_history_retrieval/Dockerfile",
        root / "src/tensor_grep/core/retrieval_scoring.py",
    ):
        if not path.is_file():
            raise HarnessError(f"evaluator_file_missing: {path}")
        digest.update(path.read_bytes())
    return digest.hexdigest()


def validate_manifest(manifest: dict[str, Any]) -> None:
    cases = manifest.get("cases")
    if not isinstance(cases, list) or not re.fullmatch(
        r"[0-9a-f]{40}", str(manifest.get("cutoff_sha", ""))
    ):
        raise HarnessError("invalid_manifest")
    if (
        not isinstance(manifest.get("cap"), int)
        or manifest["cap"] <= 0
        or len(cases) > manifest["cap"]
    ):
        raise HarnessError("invalid_manifest_cap")
    seen: set[str] = set()
    for case in cases:
        if not isinstance(case, dict):
            raise HarnessError("invalid_manifest_case")
        sha, parent = case.get("sha"), case.get("parent")
        if (
            not all(
                isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40}", value)
                for value in (sha, parent)
            )
            or sha in seen
        ):
            raise HarnessError("invalid_manifest_sha")
        seen.add(sha)
        for key in ("gold_files", "gold_tests", "input_paths"):
            paths = case.get(key)
            if (
                not isinstance(paths, list)
                or not all(
                    isinstance(path, str)
                    and path
                    and "\\" not in path
                    and not Path(path).is_absolute()
                    and ".." not in Path(path).parts
                    for path in paths
                )
                or len(paths) != len(set(paths))
            ):
                raise HarnessError(f"invalid_manifest_paths:{sha}:{key}")
        if (
            not case["gold_files"]
            or not isinstance(case.get("query"), str)
            or not case["query"].strip()
        ):
            raise HarnessError(f"invalid_manifest_query_or_gold:{sha}")
        if case.get("tokens") != search_tokens(case["query"]) or not case["tokens"]:
            raise HarnessError(f"invalid_manifest_tokens:{sha}")
        if not set(case["gold_tests"]).issubset(case["gold_files"]) or set(case["input_paths"]) != (
            set(case["gold_files"]) - set(case["gold_tests"])
        ):
            raise HarnessError(f"invalid_manifest_labels:{sha}")
        if case.get("split") not in ("tune", "heldout"):
            raise HarnessError(f"invalid_manifest_split:{sha}")
        reason = case.get("diff_exclusion_reason")
        if reason not in DIFF_REASONS or case.get("diff_impact_eligible") != (reason == "none"):
            raise HarnessError(f"invalid_manifest_diff_eligibility:{sha}")
        if reason == "none":
            if (
                not case["input_paths"]
                or not case["gold_tests"]
                or not re.fullmatch(r"[0-9a-f]{64}", str(case.get("input_patch_sha256", "")))
            ):
                raise HarnessError(f"invalid_manifest_patch:{sha}")
        elif "input_patch_sha256" in case:
            raise HarnessError(f"ineligible_patch_hash:{sha}")
    heldout = [case for case in cases if case["split"] == "heldout"]
    eligible = sum(bool(case["diff_impact_eligible"]) for case in heldout)
    if not heldout or not eligible:
        raise HarnessError("empty_heldout_or_diff_impact_eligible")
    if cases[-len(heldout) :] != heldout:
        raise HarnessError("invalid_manifest_split_order")
    if len(heldout) != manifest.get("heldout_count") or eligible != manifest.get(
        "diff_impact_eligible_count"
    ):
        raise HarnessError("manifest_count_mismatch")


def preflight_source(source: Path, manifest: dict[str, Any]) -> None:
    cutoff = manifest["cutoff_sha"]
    if _git(source, "merge-base", "--is-ancestor", cutoff, "origin/main", check=False).returncode:
        raise HarnessError("cutoff_not_origin_main_ancestor")
    sequence = _out(_git(source, "rev-list", "--first-parent", "--reverse", cutoff)).splitlines()
    positions = {sha: index for index, sha in enumerate(sequence)}
    previous = -1
    for case in manifest["cases"]:
        sha, parent = case["sha"], case["parent"]
        if (
            _git(source, "cat-file", "-e", sha, check=False).returncode
            or _git(source, "cat-file", "-e", parent, check=False).returncode
        ):
            raise HarnessError(f"manifest_sha_missing:{sha}")
        actual = _out(_git(source, "rev-list", "--parents", "-n", "1", sha)).split()
        position = positions.get(sha, -1)
        if actual != [sha, parent] or position <= previous:
            raise HarnessError(f"manifest_history_mismatch:{sha}")
        previous = position


def load_scoring(path: Path = SCORING) -> Any:
    spec = importlib.util.spec_from_file_location("git_history_retrieval_scoring", path)
    if spec is None or spec.loader is None:
        raise HarnessError(f"scoring_file_missing: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def assert_stdlib_import_closure(path: Path = Path(__file__)) -> None:
    stdlib = sys.stdlib_module_names
    for source in (path, SCORING):
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for name in names:
                if name.split(".")[0] not in stdlib:
                    raise HarnessError(f"non_stdlib_import: {name}")


def query_from_subject(subject: str) -> str:
    query = re.sub(r"^[A-Za-z][A-Za-z0-9_-]*(?:\([^)]*\))?:\s*", "", subject)
    query = re.sub(r"\s*\(#\d+\)\s*$", "", query)
    return " ".join(query.split())


def search_tokens(query: str) -> list[str]:
    return list(
        dict.fromkeys(
            token for token in re.split(r"[^A-Za-z0-9_]+", query.lower()) if len(token) >= 3
        )
    )[:8]


def _modified_paths(repo: Path, parent: str, sha: str) -> list[str]:
    raw = _git(repo, "diff", "--name-status", "-z", "-M", "-C", parent, sha, "--").stdout
    fields = raw.split(b"\0")
    paths: list[str] = []
    index = 0
    while index < len(fields) and fields[index]:
        status = fields[index].decode("ascii", errors="strict")
        index += 1
        path = fields[index].decode("utf-8", errors="strict")
        index += 1
        if status.startswith(("R", "C")):
            index += 1
        if (
            status == "M"
            and _git(repo, "cat-file", "-e", f"{parent}:{path}", check=False).returncode == 0
        ):
            paths.append(path)
    return paths


def _classify_tests(paths: list[str], python: str) -> set[str]:
    script = (
        "import json,sys; from pathlib import Path; "
        "from tensor_grep.cli.repo_map_test_paths import _is_test_file; "
        "print(json.dumps([p for p in json.load(sys.stdin) if _is_test_file(Path(p))]))"
    )
    result = _run([python, "-c", script], input_bytes=json.dumps(paths).encode("utf-8"))
    value = json.loads(_out(result))
    return set(value)


def patch_bytes(repo: Path, parent: str, sha: str, paths: list[str]) -> bytes:
    if not paths:
        raise HarnessError("no_input_paths: refusing unrestricted diff")
    return _git(
        repo,
        "-c",
        "core.abbrev=40",
        "-c",
        "diff.renames=false",
        "-c",
        "diff.algorithm=myers",
        "diff",
        "--full-index",
        "--binary",
        "--no-color",
        "--no-ext-diff",
        "--no-textconv",
        "--no-renames",
        "--diff-algorithm=myers",
        parent,
        sha,
        "--",
        *paths,
    ).stdout


def _source_identity(repo: Path) -> tuple[bytes, bytes, bytes]:
    return (
        _git(repo, "show-ref", check=False).stdout,
        _git(repo, "config", "--local", "--list").stdout,
        _git(repo, "count-objects", "-v").stdout,
    )


def create_parent_repo(source: Path, parent: str, sha: str, target: Path) -> None:
    if target.exists():
        raise HarnessError(f"run_directory_exists: {target}")
    target.mkdir(parents=True)
    _git(target, "init", "-q")
    _git(
        target,
        "fetch",
        "--no-tags",
        "--upload-pack=git -c uploadpack.allowAnySHA1InWant=true upload-pack",
        str(source),
        f"{parent}:refs/heads/eval",
        safe_source=source,
    )
    _git(target, "checkout", "-q", "eval")
    actual = set(_out(_git(target, "rev-list", "--all")).splitlines())
    expected = set(_out(_git(source, "rev-list", parent)).splitlines())
    if actual != expected or _git(target, "cat-file", "-e", sha, check=False).returncode == 0:
        raise HarnessError(f"history_leak: {sha}")
    if _out(_git(target, "rev-parse", "HEAD")) != parent:
        raise HarnessError(f"wrong_parent: {sha}")


def _patch_is_binary(repo: Path, parent: str, sha: str, paths: list[str]) -> bool:
    raw = _git(repo, "diff", "--numstat", "-z", "--no-renames", parent, sha, "--", *paths).stdout
    return any(field.startswith(b"-\t-\t") for field in raw.split(b"\0") if field)


def generate_dataset(
    source: Path, cutoff: str, out_dir: Path, *, cap: int = 200, python: str = sys.executable
) -> dict[str, Any]:
    source = source.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    if cap <= 0:
        raise HarnessError("invalid_cap")
    cutoff = _out(_git(source, "rev-parse", "--verify", f"{cutoff}^{{commit}}"))
    if _git(source, "merge-base", "--is-ancestor", cutoff, "origin/main", check=False).returncode:
        raise HarnessError("cutoff_not_origin_main_ancestor")
    before = _source_identity(source)
    exclusions: Counter[str] = Counter(dict.fromkeys(EXCLUSION_REASONS, 0))
    diff_exclusions: Counter[str] = Counter(dict.fromkeys(DIFF_REASONS, 0))
    sequence = _out(_git(source, "rev-list", "--first-parent", "--reverse", cutoff)).splitlines()
    candidates: list[dict[str, Any]] = []
    patches: dict[str, bytes] = {}
    try:
        for sha in sequence:
            parent_line = _out(_git(source, "rev-list", "--parents", "-n", "1", sha)).split()
            subject = _out(_git(source, "show", "-s", "--format=%s", sha))
            if not subject.startswith(("fix:", "fix(")):
                continue
            if len(parent_line) != 2:
                exclusions["not_single_parent"] += 1
                continue
            parent = parent_line[1]
            gold = _modified_paths(source, parent, sha)
            if not gold:
                exclusions["empty_gold"] += 1
                continue
            query = query_from_subject(subject)
            if not query:
                exclusions["empty_query"] += 1
                continue
            tokens = search_tokens(query)
            if not tokens:
                exclusions["no_tokens"] += 1
                continue
            tests = _classify_tests(gold, python)
            inputs = [path for path in gold if path not in tests]
            reason = "none"
            patch = b""
            if not tests:
                reason = "no_gold_tests"
            elif not inputs:
                reason = "no_input_paths"
            elif _patch_is_binary(source, parent, sha, inputs):
                reason = "diff_binary"
            else:
                patch = patch_bytes(source, parent, sha, inputs)
                with tempfile.TemporaryDirectory(dir=out_dir) as temp:
                    repo = Path(temp) / "repo"
                    create_parent_repo(source, parent, sha, repo)
                    if _git(repo, "apply", "--check", input_bytes=patch, check=False).returncode:
                        reason = "diff_not_applicable"
            diff_exclusions[reason] += 1
            case: dict[str, Any] = {
                "sha": sha,
                "parent": parent,
                "query": query,
                "tokens": tokens,
                "gold_files": gold,
                "gold_tests": [p for p in gold if p in tests],
                "input_paths": inputs,
                "diff_impact_eligible": reason == "none",
                "diff_exclusion_reason": reason,
            }
            if reason == "none":
                case["input_patch_sha256"] = _sha256(patch)
                patches[sha] = patch
            candidates.append(case)
        selected = candidates[-cap:]
        heldout_count = math.ceil(len(selected) * 0.2)
        if heldout_count == 0:
            raise HarnessError("empty_heldout")
        tune_count = len(selected) - heldout_count
        for index, case in enumerate(selected):
            case["split"] = "tune" if index < tune_count else "heldout"
        for heldout in selected[tune_count:]:
            for tune in selected[:tune_count]:
                if (
                    _git(
                        source,
                        "merge-base",
                        "--is-ancestor",
                        heldout["sha"],
                        tune["parent"],
                        check=False,
                    ).returncode
                    == 0
                ):
                    raise HarnessError("ancestry_unsafe_split")
        eligible_count = sum(bool(c["diff_impact_eligible"]) for c in selected[tune_count:])
        if eligible_count == 0:
            raise HarnessError("empty_diff_impact_eligible")
        manifest = {
            "cutoff_sha": cutoff,
            "cap": cap,
            "cases": selected,
            "exclusion_counts": dict(exclusions),
            "diff_exclusion_counts": dict(diff_exclusions),
            "heldout_count": heldout_count,
            "diff_impact_eligible_count": eligible_count,
        }
        validate_manifest(manifest)
        patch_dir = out_dir / "patches"
        patch_dir.mkdir(parents=True, exist_ok=True)
        for sha, patch in patches.items():
            if any(case["sha"] == sha for case in selected):
                (patch_dir / f"{sha}.patch").write_bytes(patch)
        _write_json(out_dir / "manifest.json", manifest)
        return manifest
    finally:
        if _source_identity(source) != before:
            raise HarnessError("source_repository_changed")


def metric_specs() -> dict[str, dict[str, str | bool]]:
    specs: dict[str, dict[str, str | bool]] = {}
    for arm in SEARCH_ARMS:
        for name in (
            "mrr@10",
            "f1@10",
            "recall@10",
            "precision@10",
            "ndcg@10",
            "false_primary_rate",
        ):
            specs[f"{arm}.{name}"] = {
                "direction": "lower_better" if name == "false_primary_rate" else "higher_better",
                "gated": name in {"mrr@10", "f1@10"},
            }
    for target in ("affected_tests", "affected_files"):
        for name in ("set_recall", "set_precision"):
            specs[f"diff_impact.{target}.{name}"] = {
                "direction": "higher_better",
                "gated": target == "affected_tests",
            }
    return specs


def _unique_paths(items: Any, root: Path) -> list[str]:
    if not isinstance(items, list):
        raise HarnessError("malformed_json: expected ranking list")
    paths: list[str] = []
    for item in items:
        if not isinstance(item, str):
            raise HarnessError("malformed_json: expected path string")
        raw = item.replace("\\", "/")
        if raw.startswith("/work/run/repo/"):
            raw = raw.removeprefix("/work/run/repo/")
            if raw.startswith("/"):
                raise HarnessError(f"ranking_path_outside_case: {item}")
        elif raw.startswith("/") and os.name == "nt":
            raise HarnessError(f"ranking_path_outside_case: {item}")
        path = Path(raw)
        if path.is_absolute():
            try:
                path = path.resolve().relative_to(root.resolve())
            except ValueError as error:
                raise HarnessError(f"ranking_path_outside_case: {item}") from error
        else:
            normalized = os.path.normpath(str(path)).replace("\\", "/")
            if normalized in (".", "..") or normalized.startswith("../"):
                raise HarnessError(f"ranking_path_outside_case: {item}")
            path = Path(normalized)
        value = path.as_posix()
        if value not in paths:
            paths.append(value)
    return paths


def extract_ranking(payload: dict[str, Any], arm: str, root: Path) -> list[str]:
    raw = payload.get("matches", payload.get("results"))
    if not isinstance(raw, list):
        raise HarnessError(f"malformed_json: {arm} lacks results")
    paths: list[str] = []
    for item in raw:
        if not isinstance(item, dict):
            raise HarnessError(f"malformed_json: {arm} result is not an object")
        candidate = item.get("file", item.get("path", item.get("file_path")))
        paths.extend(_unique_paths([candidate], root))
    return list(dict.fromkeys(paths))


def score_ranking(ranked: list[str], gold: set[str], scoring: Any) -> dict[str, float]:
    if not gold:
        raise HarnessError("empty_gold")
    recall = scoring.recall_at_k(ranked, gold, top_k=10) if ranked else 0.0
    precision = scoring.precision_at_k(ranked, gold, top_k=10) if ranked else 0.0
    return {
        "mrr@10": scoring.mean_reciprocal_rank_at_k(ranked, gold, top_k=10),
        "f1@10": scoring.f1_score(precision, recall),
        "recall@10": recall,
        "precision@10": precision,
        "ndcg@10": scoring.ndcg_at_k(ranked, gold, top_k=10) if ranked else 0.0,
        "false_primary_rate": float(not ranked or ranked[0] not in gold),
    }


def score_set(predicted: set[str], gold: set[str]) -> dict[str, float]:
    if not gold:
        raise HarnessError("empty_gold_set")
    hits = len(predicted & gold)
    return {
        "set_recall": hits / len(gold),
        "set_precision": hits / len(predicted) if predicted else 0.0,
    }


def score_payload(
    payload: dict[str, Any], arm: str, case: dict[str, Any], repo: Path, scoring: Any
) -> dict[str, float]:
    if (
        payload.get("ok") is False
        or payload.get("error")
        or payload.get("result_incomplete") is True
    ):
        raise HarnessError(f"incomplete_result:{arm}")
    if arm in SEARCH_ARMS:
        return score_ranking(extract_ranking(payload, arm, repo), set(case["gold_files"]), scoring)
    if set(_unique_paths(payload.get("changed_files"), repo)) != set(case["input_paths"]):
        raise HarnessError("changed_files_mismatch")
    result: dict[str, float] = {}
    for target, gold_key in (("affected_tests", "gold_tests"), ("affected_files", "gold_files")):
        predicted = set(_unique_paths(payload.get(target), repo))
        result.update({
            f"{target}.{name}": value
            for name, value in score_set(predicted, set(case[gold_key])).items()
        })
    return result


def aggregate_scores(
    rows: list[dict[str, Any]], manifest: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    heldout = [case for case in manifest["cases"] if case["split"] == "heldout"]
    if not heldout or not any(case["diff_impact_eligible"] for case in heldout):
        raise HarnessError("empty_heldout_or_diff_impact_eligible")
    expected = {
        (case["sha"], arm)
        for case in heldout
        for arm in ARMS
        if arm != "diff_impact" or case["diff_impact_eligible"]
    }
    observed = {(row["sha"], row["arm"]) for row in rows}
    if observed != expected or len(rows) != len(expected):
        raise HarnessError("evaluated_case_set_mismatch")
    result: dict[str, dict[str, Any]] = {}
    for key, spec in metric_specs().items():
        arm, name = key.split(".", 1)
        values = [row["metrics"][name] for row in rows if row["arm"] == arm]
        if not values or any(
            not isinstance(value, (int, float)) or not math.isfinite(value) for value in values
        ):
            raise HarnessError(f"non_finite_or_missing_metric: {key}")
        result[key] = {**spec, "value": sum(values) / len(values), "count": len(values)}
    return result


def metric_is_worse(current: float, baseline: float, direction: str) -> bool:
    if direction == "higher_better":
        return current < baseline
    if direction == "lower_better":
        return current > baseline
    raise HarnessError(f"invalid_metric_direction:{direction}")


def compare_gate(
    baseline: dict[str, Any],
    current: dict[str, Any],
    *,
    evaluator: str,
    environment: dict[str, Any],
) -> list[str]:
    failures: list[str] = []
    if baseline.get("evaluator_sha256") != evaluator:
        failures.append("evaluator_changed")
    if baseline.get("environment") != environment:
        failures.append("environment_changed")
    specs = metric_specs()
    baseline_metrics = baseline.get("metrics", {})
    current_metrics = current.get("metrics", {})
    for key, spec in specs.items():
        old = baseline_metrics.get(key)
        new = current_metrics.get(key)
        if (
            not isinstance(old, dict)
            or not isinstance(new, dict)
            or any(
                old.get(field) != value or new.get(field) != value for field, value in spec.items()
            )
            or old.get("count") != new.get("count")
        ):
            failures.append(f"metric_contract_changed:{key}")
            continue
        old_value, new_value = old.get("value"), new.get("value")
        if not all(
            isinstance(v, (int, float)) and math.isfinite(v) for v in (old_value, new_value)
        ):
            failures.append(f"non_finite_metric:{key}")
        elif spec["gated"] and metric_is_worse(new_value, old_value, str(spec["direction"])):
            failures.append(f"below_baseline:{key}")
    if current.get("case_set") != baseline.get("case_set"):
        failures.append("evaluated_case_set_mismatch")
    return failures


def case_environment(home: Path, venv: Path) -> dict[str, str]:
    home.mkdir(parents=True, exist_ok=True)
    env = {
        key: value
        for key, value in os.environ.items()
        if key != "PYTHONPATH" and not key.startswith(("TG_", "XDG_"))
    }
    for key in (
        "HOME",
        "USERPROFILE",
        "LOCALAPPDATA",
        "APPDATA",
        "TMP",
        "TEMP",
        "TMPDIR",
        "XDG_CACHE_HOME",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "XDG_STATE_HOME",
        "XDG_RUNTIME_DIR",
        "XDG_CONFIG_DIRS",
        "XDG_DATA_DIRS",
    ):
        env[key] = str(home if key in ("HOME", "USERPROFILE") else home / key.lower())
        Path(env[key]).mkdir(parents=True, exist_ok=True)
    for key in PATH_ENV:
        env[key] = str(home / key.lower())
    env.update(
        TG_SESSION_DAEMON_AUTOSTART="0",
        PYTHONNOUSERSITE="1",
        TG_SIDECAR_PYTHON=str(venv / "bin/python"),
        TG_NATIVE_TG_BINARY=str(venv.parent / "tg"),
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL=os.devnull,
        VIRTUAL_ENV=str(venv),
        PATH=str(venv / "bin") + os.pathsep + env.get("PATH", ""),
    )
    return env


def environment_identity() -> dict[str, Any]:
    dockerfile = HERE / "Dockerfile"
    from_line = dockerfile.read_text(encoding="utf-8").splitlines()[0]
    image = re.fullmatch(r"FROM (ubuntu:24\.04@sha256:[0-9a-f]{64})", from_line)
    if image is None:
        raise HarnessError("unpinned_base_image")
    versions = _out(
        _run([
            "dpkg-query",
            "-W",
            "-f=${binary:Package}=${Version}\\n",
            "git",
            "python3",
            "python3-venv",
            "rustc",
            "cargo",
        ])
    )
    return {
        "base_image_digest": image.group(1).split("@", 1)[1],
        "dockerfile_sha256": _sha256(dockerfile.read_bytes()),
        "packages": dict(line.split("=", 1) for line in versions.splitlines()),
        "rust_toolchain": _out(_run(["rustc", "--version"])),
        "cargo_version": _out(_run(["cargo", "--version"])),
        "python_version": _out(_run(["python3", "--version"])),
        "git_version": _out(_run(["git", "--version"])),
        "uv_version": _out(_run(["uv", "--version"])),
    }


def _revision_checkout(source: Path, revision: str, target: Path) -> None:
    sha = _out(_git(source, "rev-parse", "--verify", f"{revision}^{{commit}}"))
    target.mkdir(parents=True)
    _git(target, "init", "-q")
    _git(
        target,
        "fetch",
        "--depth=1",
        "--no-tags",
        "--upload-pack=git -c uploadpack.allowAnySHA1InWant=true upload-pack",
        str(source),
        f"{sha}:refs/heads/eval",
        safe_source=source,
    )
    _git(target, "checkout", "-q", "eval")
    if _out(_git(target, "rev-parse", "HEAD")) != sha:
        raise HarnessError("revision_checkout_mismatch")


def _read_only_tree(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        if not path.is_symlink():
            mode = path.stat().st_mode
            path.chmod(mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))
    root.chmod(root.stat().st_mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))


def _harness_venv_python(venv: Path) -> Path:
    if os.name == "nt":
        return venv / "Scripts" / "python.exe"
    return venv / "bin" / "python"


def ensure_harness_venv(build: Path) -> Path:
    venv = build / "harness-venv"
    python = _harness_venv_python(venv)
    if python.is_file():
        return python
    _run([sys.executable, "-m", "venv", str(venv)])
    if not python.is_file():
        raise HarnessError("harness_venv_missing")
    return python


def worker_build(source: Path, work: Path) -> dict[str, Any]:
    build = work / "build"
    baseline_runtime: dict[str, str] | None = None
    for subject in ("baseline", "head"):
        target = build / subject
        tree = target / "tree"
        if not tree.is_dir():
            raise HarnessError(f"build_tree_missing:{subject}")
        venv = target / "venv"
        env = os.environ.copy()
        env["UV_PROJECT_ENVIRONMENT"] = str(venv)
        env["UV_CACHE_DIR"] = str(build / "uv-cache")
        _run(
            ["uv", "sync", "--frozen", "--no-editable", "--extra", "dev", "--extra", "ast"],
            cwd=tree,
            env=env,
        )
        env["CARGO_TARGET_DIR"] = str(target / "cargo-target")
        _run(
            [
                "cargo",
                "build",
                "--release",
                "--no-default-features",
                "--manifest-path",
                str(tree / "rust_core/Cargo.toml"),
            ],
            cwd=tree,
            env=env,
        )
        shutil.copy2(target / "cargo-target/release/tg", target / "tg")
        (target / "tg").chmod(0o755)
        _, _, runtime = _runtime_identity(subject, build)
        _write_json(target / "runtime.json", runtime)
        if subject == "baseline":
            baseline_runtime = runtime.copy()
    if baseline_runtime is None or (
        _sha256((build / "baseline/tg").read_bytes()) != baseline_runtime["binary_sha256"]
        or _read_json(build / "baseline/runtime.json") != baseline_runtime
    ):
        raise HarnessError("runtime_binary_hash_mismatch:baseline")
    if "package_sha256" in baseline_runtime:
        package_file = build / "baseline/venv" / baseline_runtime["package_relpath"]
        if (
            _package_sha256(package_file, build / "baseline/venv")
            != baseline_runtime["package_sha256"]
        ):
            raise HarnessError("runtime_package_hash_mismatch:baseline")
    ensure_harness_venv(build)
    identity = environment_identity()
    _write_json(work / "environment.json", identity)
    _read_only_tree(build)
    return identity


def _normalize_mtimes(repo: Path, timestamp: int) -> None:
    for path in repo.rglob("*"):
        if ".git" not in path.parts and not path.is_symlink():
            os.utime(path, (timestamp, timestamp))


def worker_prepare(source: Path, work: Path, manifest: dict[str, Any], subject: str) -> None:
    validate_manifest(manifest)
    before = _source_identity(source)
    before_status = _git(source, "status", "--porcelain").stdout
    for case in manifest["cases"]:
        for sha in (case["sha"], case["parent"]):
            if _git(source, "cat-file", "-e", sha, check=False).returncode:
                raise HarnessError(f"manifest_sha_missing:{sha}")
    heldout = [case for case in manifest["cases"] if case["split"] == "heldout"]
    if not heldout or not any(case["diff_impact_eligible"] for case in heldout):
        raise HarnessError("empty_heldout_or_diff_impact_eligible")
    try:
        for index, case in enumerate(heldout):
            sha, parent = case["sha"], case["parent"]
            patch: bytes | None = None
            if case["diff_impact_eligible"]:
                patch_path = HERE / "patches" / f"{sha}.patch"
                patch = patch_path.read_bytes()
                if _sha256(patch) != case["input_patch_sha256"]:
                    raise HarnessError(f"patch_hash_mismatch:{sha}")
                target_patch = work / "patches" / sha / "input.patch"
                target_patch.parent.mkdir(parents=True)
                target_patch.write_bytes(patch)
            timestamp = int(_out(_git(source, "show", "-s", "--format=%ct", parent)))
            arms = (*SEARCH_ARMS, "diff_impact") if patch is not None else SEARCH_ARMS
            for arm in arms:
                for run in RUNS if index < 5 else RUNS[:1]:
                    run_dir = work / "runs" / sha / subject / arm / run
                    repo = run_dir / "repo"
                    create_parent_repo(source, parent, sha, repo)
                    if arm == "diff_impact":
                        assert patch is not None
                        _git(repo, "apply", "--check", input_bytes=patch)
                        _git(repo, "apply", input_bytes=patch)
                        changed = set(
                            _out(_git(repo, "diff", "--name-only", "HEAD", "--")).splitlines()
                        )
                        if changed != set(case["input_paths"]) or changed & set(case["gold_tests"]):
                            raise HarnessError(f"applied_paths_mismatch:{sha}")
                    elif _out(_git(repo, "status", "--porcelain")):
                        raise HarnessError(f"dirty_search_repo:{sha}")
                    _normalize_mtimes(repo, timestamp)
                    _write_json(
                        run_dir / "case.json",
                        {key: case[key] for key in ("sha", "parent", "query", "tokens")},
                    )
    finally:
        if (
            _source_identity(source) != before
            or _git(source, "status", "--porcelain").stdout != before_status
        ):
            raise HarnessError("source_repository_changed")


def _assert_absent(path: Path) -> None:
    if path.exists():
        raise HarnessError(f"forbidden_mount_present:{path}")


def _assert_write_denied(path: Path) -> None:
    try:
        with path.open("wb") as stream:
            stream.write(b"probe")
    except OSError as error:
        if error.errno not in (13, 30):
            raise HarnessError(f"unexpected_write_errno:{path}:{error.errno}") from error
    else:
        path.unlink()
        raise HarnessError(f"confinement_write_succeeded:{path}")


def confinement_controls(run_dir: Path, arm: str) -> None:
    probe = run_dir / "write-smoke"
    probe.write_bytes(b"ok")
    if probe.read_bytes() != b"ok":
        raise HarnessError("confinement_smoke_failed")
    probe.unlink()
    for path in (Path("/source"), Path("/work/patches"), Path("/work/runs")):
        _assert_absent(path)
    _assert_write_denied(Path("/work/build/write-negative-control"))
    _assert_write_denied(Path("/opt/write-negative-control"))
    _assert_write_denied(Path("/home/write-negative-control"))
    if arm in SEARCH_ARMS:
        _assert_absent(Path("/work/diff-impact"))


def tg_launched_write_control(run_dir: Path, venv: Path, env: dict[str, str]) -> None:
    writer = run_dir / "write-control"
    marker = run_dir / "write-control-result.json"
    writer.write_text(
        "#!/usr/bin/python3\n"
        "import json, pathlib\n"
        "codes = []\n"
        "for target in ('/opt/tg-write-control', '/home/tg-write-control'):\n"
        "    try:\n"
        "        pathlib.Path(target).write_bytes(b'bad')\n"
        "    except OSError as error:\n"
        "        codes.append(error.errno)\n"
        f"pathlib.Path({str(marker)!r}).write_text(json.dumps(codes))\n"
        "raise SystemExit(1)\n",
        encoding="utf-8",
        newline="\n",
    )
    writer.chmod(0o755)
    try:
        probe_env = {**env, "TG_DISABLE_NATIVE_TG": "1", "TG_RG_PATH": str(writer)}
        _run([str(venv / "bin/tg"), "--pcre2-version"], cwd=run_dir, env=probe_env, check=False)
        codes = json.loads(marker.read_text(encoding="utf-8")) if marker.is_file() else []
        if len(codes) != 2 or any(code not in (13, 30) for code in codes):
            raise HarnessError(f"tg_launched_writer_not_denied:{codes}")
    finally:
        writer.unlink(missing_ok=True)
        marker.unlink(missing_ok=True)


def _runtime_identity(subject: str, build: Path) -> tuple[Path, Path, dict[str, str]]:
    subject_dir = build / subject
    venv = subject_dir / "venv"
    tg = subject_dir / "tg"
    python = venv / "bin/python"
    version_text = _out(_run([str(tg), "--version"]))
    match = re.search(r"\d+\.\d+\.\d+", version_text)
    if match is None:
        raise HarnessError("binary_version_missing")
    expected_version = match.group()
    if subject == "baseline" and expected_version != "1.126.0":
        raise HarnessError("baseline_binary_version_mismatch")
    script = (
        "import importlib.metadata,json,tensor_grep; "
        "print(json.dumps({'file':tensor_grep.__file__,"
        "'version':importlib.metadata.version('tensor-grep')}))"
    )
    package = json.loads(_out(_run([str(python), "-c", script])))
    if not Path(package["file"]).resolve().is_relative_to(venv.resolve()):
        raise HarnessError("editable_or_wrong_package_tree")
    if package["version"] != expected_version:
        raise HarnessError("binary_package_version_mismatch")
    doctor_env = case_environment(subject_dir / "doctor-home", venv)
    doctor = json.loads(
        _out(
            _run(
                [str(tg), "doctor", "--json", "--no-lsp"],
                env=doctor_env,
            )
        )
    )
    if not isinstance(doctor, dict) or doctor.get("version") != expected_version:
        raise HarnessError("doctor_malformed_json")
    return (
        tg,
        venv,
        {
            "binary_version": expected_version,
            "package_version": package["version"],
            "package_file": package["file"],
            "package_relpath": str(Path(package["file"]).resolve().relative_to(venv.resolve())),
            "package_sha256": _package_sha256(Path(package["file"]), venv),
            "binary_sha256": _sha256(tg.read_bytes()),
        },
    )


def _arm_argv(tg: Path, arm: str, case: dict[str, Any], repo: Path) -> list[str]:
    if arm == "find":
        return [
            str(tg),
            "find",
            case["query"],
            str(repo),
            "--json",
            "--grounding",
            "off",
            "--rerank",
            "off",
        ]
    if arm == "search_rank":
        if not case["tokens"]:
            raise HarnessError("empty_search_tokens")
        return [
            str(tg),
            "search",
            "-i",
            "|".join(re.escape(token) for token in case["tokens"]),
            str(repo),
            "--rank",
            "--json",
        ]
    return [str(tg), "diff-impact", "HEAD", "--json"]


def worker_evaluate(
    run_dir: Path, build: Path, *, subject: str, arm: str, confined: bool
) -> dict[str, Any]:
    case = _read_json(run_dir / "case.json")
    repo = run_dir / "repo"
    if confined:
        confinement_controls(run_dir, arm)
    if _out(_git(repo, "rev-parse", "HEAD")) != case["parent"]:
        raise HarnessError("wrong_parent_before_scoring")
    if arm in SEARCH_ARMS and _out(_git(repo, "status", "--porcelain")):
        raise HarnessError("dirty_search_repo_before_scoring")
    tg = build / subject / "tg"
    venv = build / subject / "venv"
    runtime = _read_json(build / subject / "runtime.json")
    home = run_dir / "home"
    if home.exists():
        raise HarnessError("run_home_reused")
    env = case_environment(home, venv)
    if confined:
        tg_launched_write_control(run_dir, venv, env)
    argv = _arm_argv(tg, arm, case, repo)
    result = _run(argv, cwd=repo, env=env, check=False)
    payload = _tg_payload(result, arm)
    if arm in SEARCH_ARMS:
        prediction: Any = extract_ranking(payload, arm, repo)
    else:
        prediction = {
            name: sorted(_unique_paths(payload.get(name), repo))
            for name in ("changed_files", "affected_tests", "affected_files")
        }
    row = {
        "sha": case["sha"],
        "arm": arm,
        "payload": payload,
        "prediction": prediction,
        "runtime": runtime,
        "confinement": "container" if confined else "none (advisory)",
    }
    _write_json(run_dir / "result.json", row)
    return row


def _docker_run(
    image: str,
    *,
    network: str,
    mounts: list[tuple[Path, str, str]],
    arguments: list[str],
    read_only: bool = False,
) -> None:
    argv = ["docker", "run", "--rm", "--network", network]
    if read_only:
        argv.extend(["--read-only", "--tmpfs", "/tmp:rw,nosuid,nodev"])
    for host, container, mode in mounts:
        if mode not in ("rw", "readonly"):
            raise HarnessError(f"invalid_mount_mode:{mode}")
        suffix = ",readonly" if mode == "readonly" else ""
        argv.extend(["--mount", f"type=bind,src={host.resolve()},dst={container}{suffix}"])
    argv.extend([image, *arguments])
    _run(argv)


def _selected_runs(manifest: dict[str, Any], subject: str, work: Path) -> list[tuple[Path, str]]:
    selected: list[tuple[Path, str]] = []
    for index, case in enumerate(c for c in manifest["cases"] if c["split"] == "heldout"):
        arms = (*SEARCH_ARMS, "diff_impact") if case["diff_impact_eligible"] else SEARCH_ARMS
        for arm in arms:
            for run in RUNS if index < 5 else RUNS[:1]:
                selected.append((work / "runs" / case["sha"] / subject / arm / run, arm))
    return selected


def _collect_report(manifest: dict[str, Any], subject: str, work: Path) -> dict[str, Any]:
    first: list[dict[str, Any]] = []
    repeats: dict[tuple[str, str], dict[str, Any]] = {}
    cases = {case["sha"]: case for case in manifest["cases"]}
    scoring = load_scoring()
    saved_runtime = _read_json(work / "build" / subject / "runtime.json")
    binary = work / "build" / subject / "tg"
    if _sha256(binary.read_bytes()) != saved_runtime.get("binary_sha256"):
        raise HarnessError(f"runtime_binary_hash_mismatch:{subject}")
    if subject == "baseline":
        relpath = saved_runtime.get("package_relpath")
        package_hash = saved_runtime.get("package_sha256")
        if not isinstance(relpath, str) or not isinstance(package_hash, str):
            raise HarnessError("runtime_package_hash_mismatch:baseline")
        venv = work / "build/baseline/venv"
        if _package_sha256(venv / relpath, venv) != package_hash:
            raise HarnessError("runtime_package_hash_mismatch:baseline")
    for run_dir, arm in _selected_runs(manifest, subject, work):
        row = _read_json(run_dir / "result.json")
        if row["sha"] != run_dir.parents[2].name or row["arm"] != arm:
            raise HarnessError("result_identity_mismatch")
        if row.get("runtime") != saved_runtime:
            raise HarnessError(f"runtime_row_mismatch:{subject}")
        row["metrics"] = score_payload(
            row.pop("payload"), arm, cases[row["sha"]], run_dir / "repo", scoring
        )
        if run_dir.name == "repeat":
            repeats[(row["sha"], arm)] = row
        else:
            first.append(row)
    for row in first:
        repeat = repeats.get((row["sha"], row["arm"]))
        if repeat is not None and (
            repeat["metrics"] != row["metrics"] or repeat["prediction"] != row["prediction"]
        ):
            raise HarnessError(f"determinism_mismatch:{row['sha']}:{row['arm']}")
    metrics = aggregate_scores(first, manifest)
    heldout = [c["sha"] for c in manifest["cases"] if c["split"] == "heldout"]
    case_set = {
        arm: [
            c["sha"]
            for c in manifest["cases"]
            if c["split"] == "heldout" and (arm != "diff_impact" or c["diff_impact_eligible"])
        ]
        for arm in ARMS
    }
    if set(case_set["find"]) != set(heldout):
        raise HarnessError("evaluated_case_set_mismatch")
    runtime = saved_runtime
    return {
        "metrics": metrics,
        "case_set": case_set,
        "runtime": runtime,
        "environment": _read_json(work / "environment.json"),
        "evaluator_sha256": evaluator_sha256(),
        "confinement": "container",
        "subject": subject,
    }


@contextmanager
def _host_work_directory(image: str) -> Iterator[Path]:
    with tempfile.TemporaryDirectory(prefix="tg-git-history-", ignore_cleanup_errors=True) as temp:
        work = Path(temp)
        try:
            yield work
        finally:
            if os.name == "posix" and (work / "build").exists():
                cleanup = _run(
                    [
                        "docker",
                        "run",
                        "--rm",
                        "--network",
                        "none",
                        "--mount",
                        f"type=bind,src={work.resolve()},dst=/work",
                        "--entrypoint",
                        "/bin/sh",
                        image,
                        "-c",
                        f"chown -R {os.getuid()}:{os.getgid()} /work && chmod -R u+rwX /work",
                    ],
                    check=False,
                )
                if cleanup.returncode and sys.exc_info()[0] is None:
                    detail = cleanup.stderr.decode("utf-8", errors="replace").strip()
                    raise HarnessError(f"host_cleanup_failed:{detail}")


def gate_report(report: dict[str, Any], baseline_path: Path, *, enforce: bool = True) -> list[str]:
    if not baseline_path.is_file():
        if enforce:
            raise HarnessError("baseline_missing")
        return ["report_only: baseline_missing"]
    baseline = _read_json(baseline_path)
    try:
        actual_evaluator = evaluator_sha256()
    except HarnessError:
        actual_evaluator = None
    failures = compare_gate(
        baseline, report, evaluator=actual_evaluator, environment=report["environment"]
    )
    if actual_evaluator is None:
        failures.append("evaluator_unavailable")
    if report.get("evaluator_sha256") != actual_evaluator:
        failures.append("report_evaluator_mismatch")
    return failures


def metric_delta_markdown(previous: dict[str, Any], updated: dict[str, Any]) -> str:
    lines = [
        "| Metric | Previous | Updated | Delta | Direction | Gated |",
        "| --- | ---: | ---: | ---: | --- | --- |",
    ]
    for key, spec in metric_specs().items():
        old = previous.get("metrics", {}).get(key, {}).get("value")
        new = updated["metrics"][key]["value"]
        if not isinstance(old, (int, float)) or not math.isfinite(old):
            raise HarnessError(f"previous_baseline_metric_missing:{key}")
        lines.append(
            f"| {key} | {old:.6f} | {new:.6f} | {new - old:+.6f} | "
            f"{spec['direction']} | {spec['gated']} |"
        )
    return "\n".join(lines) + "\n"


def orchestrate(
    source: Path,
    manifest_path: Path,
    output: Path,
    *,
    subject: str,
    baseline_output: Path | None = None,
    enforce: bool = False,
) -> dict[str, Any]:
    if baseline_output is not None and subject != "baseline":
        raise HarnessError("head_cannot_write_baseline")
    manifest = _read_json(manifest_path)
    validate_manifest(manifest)
    preflight_source(source, manifest)
    image = "tg-git-history-retrieval:local"
    _run(["docker", "build", "-f", str(HERE / "Dockerfile"), "-t", image, str(ROOT)])
    with _host_work_directory(image) as work:
        for revision, subject_name in (("v1.126.0", "baseline"), ("HEAD", "head")):
            tree = work / "build" / subject_name / "tree"
            tree.parent.mkdir(parents=True, exist_ok=True)
            _revision_checkout(source, revision, tree)
            shutil.rmtree(tree / ".git")
            (tree / "benchmarks/git_history_retrieval/manifest.json").unlink(missing_ok=True)
            (tree / "benchmarks/git_history_retrieval/baseline.json").unlink(missing_ok=True)
            patches = tree / "benchmarks/git_history_retrieval/patches"
            if patches.is_symlink():
                patches.unlink()
            elif patches.exists():
                shutil.rmtree(patches)
        _docker_run(
            image,
            network="bridge",
            mounts=[(work, "/work", "rw")],
            arguments=["build"],
        )
        _docker_run(
            image,
            network="none",
            read_only=True,
            mounts=[
                (source, "/source", "readonly"),
                (work, "/work", "rw"),
                (work / "build", "/work/build", "readonly"),
                (manifest_path, "/work/manifest.json", "readonly"),
                (
                    manifest_path.parent / "patches",
                    "/opt/checkout/benchmarks/git_history_retrieval/patches",
                    "readonly",
                ),
            ],
            arguments=["prepare", "--subject", subject],
        )
        for run_dir, arm in _selected_runs(manifest, subject, work):
            _docker_run(
                image,
                network="none",
                read_only=True,
                mounts=[(run_dir, "/work/run", "rw"), (work / "build", "/work/build", "readonly")],
                arguments=["evaluate", "--subject", subject, "--arm", arm],
            )
        report = _collect_report(manifest, subject, work)
        _write_json(output, report)
        if baseline_output is not None:
            baseline = {**report, "generated_by_run_id": os.environ.get("GITHUB_RUN_ID", "local")}
            _write_json(baseline_output, baseline)
            previous_path = HERE / "baseline.json"
            if previous_path.is_file():
                baseline_output.with_name("baseline_delta.md").write_text(
                    metric_delta_markdown(_read_json(previous_path), baseline),
                    encoding="utf-8",
                    newline="\n",
                )
        elif subject == "head":
            failures = gate_report(report, HERE / "baseline.json", enforce=enforce)
            if failures and failures != ["report_only: baseline_missing"]:
                raise HarnessError(",".join(failures))
        return report


def _compatibility_check(
    manifest: dict[str, Any], source: Path, patches: Path, baseline_tg: Path
) -> None:
    scoring = load_scoring()
    for case in manifest["cases"]:
        arms = (*SEARCH_ARMS, "diff_impact") if case["diff_impact_eligible"] else SEARCH_ARMS
        for arm in arms:
            with tempfile.TemporaryDirectory(dir=patches.parent) as temp:
                repo = Path(temp) / "repo"
                create_parent_repo(source, case["parent"], case["sha"], repo)
                if arm == "diff_impact":
                    _git(repo, "apply", input_bytes=(patches / f"{case['sha']}.patch").read_bytes())
                result = _run(_arm_argv(baseline_tg, arm, case, repo), cwd=repo, check=False)
                try:
                    payload = _tg_payload(result, arm)
                    score_payload(payload, arm, case, repo, scoring)
                except HarnessError as error:
                    raise HarnessError(
                        f"baseline_arm_incompatible:{arm}:{case['sha']}:{error}"
                    ) from error


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    generate = commands.add_parser("generate")
    generate.add_argument("--repo", type=Path, required=True)
    generate.add_argument("--cutoff", required=True)
    generate.add_argument("--out-dir", type=Path, required=True)
    generate.add_argument("--cap", type=int, default=200)
    generate.add_argument("--baseline-tg", type=Path, required=True)
    orchestrator = commands.add_parser("orchestrate")
    orchestrator.add_argument("--repo", type=Path, default=ROOT)
    orchestrator.add_argument("--manifest", type=Path, default=HERE / "manifest.json")
    orchestrator.add_argument("--output", type=Path, required=True)
    orchestrator.add_argument("--subject", choices=("baseline", "head"), required=True)
    orchestrator.add_argument("--baseline-output", type=Path)
    orchestrator.add_argument("--enforce", action="store_true")
    worker = commands.add_parser("worker")
    worker.add_argument("phase", choices=("build", "prepare", "evaluate"))
    worker.add_argument("--subject", choices=("baseline", "head"), default="head")
    worker.add_argument("--arm", choices=ARMS)
    worker.add_argument("--run-dir", type=Path, default=Path("/work/run"))
    worker.add_argument("--build-dir", type=Path, default=Path("/work/build"))
    worker.add_argument("--advisory", action="store_true")
    gate = commands.add_parser("gate")
    gate.add_argument("--report", type=Path, required=True)
    gate.add_argument("--baseline", type=Path, default=HERE / "baseline.json")
    gate.add_argument("--report-only", action="store_true")
    args = parser.parse_args(argv)
    assert_stdlib_import_closure()
    if args.command == "generate":
        manifest = generate_dataset(args.repo, args.cutoff, args.out_dir, cap=args.cap)
        _compatibility_check(manifest, args.repo, args.out_dir / "patches", args.baseline_tg)
    elif args.command == "orchestrate":
        orchestrate(
            args.repo,
            args.manifest,
            args.output,
            subject=args.subject,
            baseline_output=args.baseline_output,
            enforce=args.enforce,
        )
    elif args.command == "gate":
        failures = gate_report(_read_json(args.report), args.baseline, enforce=not args.report_only)
        if failures and failures != ["report_only: baseline_missing"]:
            raise HarnessError(",".join(failures))
        print(json.dumps({"status": "report_only" if failures else "pass", "notes": failures}))
    elif args.phase == "build":
        worker_build(Path("/source"), Path("/work"))
    elif args.phase == "prepare":
        worker_prepare(
            Path("/source"), Path("/work"), _read_json(Path("/work/manifest.json")), args.subject
        )
    else:
        if args.arm is None:
            raise HarnessError("arm_required")
        worker_evaluate(
            args.run_dir,
            args.build_dir,
            subject=args.subject,
            arm=args.arm,
            confined=not args.advisory,
        )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except HarnessError as error:
        print(f"harness_error: {error}", file=sys.stderr)
        raise SystemExit(1) from error

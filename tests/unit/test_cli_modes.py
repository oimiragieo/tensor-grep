import hashlib
import hmac
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import types
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

import pytest
from typer.completion import get_completion_script
from typer.testing import CliRunner

from tensor_grep.cli import agent_capsule, repo_map
from tensor_grep.cli import main as cli_main
from tensor_grep.cli.main import (
    _candidate_versions_from_pip_index_output,
    _candidate_versions_from_pypi_json,
    _candidate_versions_from_pypi_simple_index,
    _highest_tensor_grep_version,
    _safe_stdout_line,
    _select_ast_backend_for_pattern,
    _should_refuse_unbounded_generated_scan,
    _should_refuse_unbounded_workspace_root_scan,
    _write_path_list,
    app,
)
from tensor_grep.core.config import SearchConfig
from tensor_grep.core.hardware.device_detect import DeviceInfo
from tensor_grep.core.hardware.device_inventory import DeviceInventory
from tensor_grep.core.result import MatchLine, SearchResult


def _strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


TOP_LEVEL_HELP_REQUIRED_SNIPPETS = (
    "Fast text, AST, indexed, and GPU-aware search CLI",
    "Common usage",
    "Environment overrides",
    "tg PATTERN [PATH ...]",
    "upgrade",
    "update",
    "repair-launcher",
    "dogfood",
    "lsp-setup",
    "checkpoint",
    "TG_SIDECAR_PYTHON",
    "TG_NATIVE_TG_BINARY",
    "TG_RG_PATH",
    "TG_FORCE_CPU",
    "TG_SIDECAR_TIMEOUT_MS",
    "TENSOR_GREP_DEVICE_IDS",
    "TENSOR_GREP_CLASSIFY_PROVIDER",
    "TENSOR_GREP_TRITON_TIMEOUT_SECONDS",
    "TENSOR_GREP_LSP_OPERATION_BUDGET_SECONDS",
    "--smart-case",
    "--hidden",
    "--max-depth",
    "--text",
    "--allow-foreign-rename",
    "native GPU falls back",
    "gpu_acceleration",
    "sidecar-routed GPU results",
    "searches follow ripgrep",
    "PowerShell double quotes expand $NAME",
)


SEARCH_HELP_REQUIRED_SNIPPETS = (
    "Usage:",
    "search [OPTIONS]",
    "PATTERN",
    "validated common rg-compatible subset",
    "--format rg --json",
    "--maxdepth",
    "--sort-files",
    "local heuristics by default",
    "--gpu-device-ids",
)


@dataclass
class _FakeBackend:
    results_by_file: dict[str, SearchResult]

    def search(self, file_path: str, pattern: str, config=None) -> SearchResult:
        return self.results_by_file.get(
            file_path, SearchResult(matches=[], total_files=0, total_matches=0)
        )


@dataclass
class _FakePipeline:
    backend: _FakeBackend

    def __init__(self, force_cpu=False, config=None):
        global _LAST_PIPELINE_CONFIG
        _LAST_PIPELINE_CONFIG = config
        self.backend = _FAKE_BACKEND
        self.selected_backend_name = "FakeBackend"
        self.selected_backend_reason = "unit_test_fake_pipeline"
        self.selected_gpu_device_ids = []
        self.selected_gpu_chunk_plan_mb = []

    def get_backend(self):
        return self.backend


def _canonical_manifest_bytes(manifest: dict[str, object]) -> bytes:
    canonical = dict(manifest)
    canonical.pop("manifest_sha256", None)
    canonical.pop("signature", None)
    return json.dumps(canonical, indent=2, sort_keys=True).encode("utf-8")


def _write_audit_manifest(
    path: Path,
    *,
    previous_manifest_sha256: str | None = None,
    project_root: Path | None = None,
    signing_key: bytes | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "version": 1,
        "kind": "rewrite-audit-manifest",
        "created_at": "2026-03-23T12:00:00Z",
        "lang": "python",
        "path": str(project_root or path.parent),
        "plan_total_edits": 1,
        "applied_edit_ids": ["edit-1"],
        "checkpoint": None,
        "validation": None,
        "files": [
            {
                "path": "src/sample.py",
                "edit_ids": ["edit-1"],
                "before_sha256": "a" * 64,
                "after_sha256": "b" * 64,
            }
        ],
        "previous_manifest_sha256": previous_manifest_sha256,
    }
    payload["manifest_sha256"] = hashlib.sha256(_canonical_manifest_bytes(payload)).hexdigest()
    if signing_key is not None:
        payload["signature"] = {
            "kind": "hmac-sha256",
            "key_path": str(path.with_suffix(".key")),
            "value": hmac.new(
                signing_key,
                _canonical_manifest_bytes(payload),
                hashlib.sha256,
            ).hexdigest(),
        }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def _write_scan_results(path: Path) -> dict[str, object]:
    payload = {
        "version": 1,
        "routing_backend": "AstBackend",
        "routing_reason": "builtin-ruleset-scan",
        "sidecar_used": False,
        "ruleset": "auth-safe",
        "rule_count": 1,
        "matched_rules": 1,
        "total_matches": 1,
        "findings": [
            {
                "rule_id": "python-eval",
                "language": "python",
                "severity": "high",
                "matches": 1,
                "files": ["src/sample.py"],
                "evidence": [{"file": "src/sample.py", "match_count": 1}],
            }
        ],
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def _assert_audit_manifest_envelope(payload: dict[str, object], *, routing_reason: str) -> None:
    assert payload["version"] == 1
    assert payload["routing_backend"] == "AuditManifest"
    assert payload["routing_reason"] == routing_reason
    assert payload["sidecar_used"] is False


def _assert_enriched_edit_plan_seed(
    edit_plan_seed: dict[str, object],
    *,
    primary_file: Path | None = None,
    primary_symbol_name: str | None = None,
) -> None:
    if primary_file is not None:
        assert edit_plan_seed["primary_file"] == str(primary_file.resolve())
    else:
        assert isinstance(edit_plan_seed["primary_file"], str)
    if primary_symbol_name is not None:
        assert edit_plan_seed["primary_symbol"]["name"] == primary_symbol_name
    else:
        assert isinstance(edit_plan_seed["primary_symbol"]["name"], str)
    assert {"start_line", "end_line"} <= set(edit_plan_seed["primary_span"])
    assert edit_plan_seed["primary_span"]["start_line"] >= 1
    assert (
        edit_plan_seed["primary_span"]["end_line"] >= edit_plan_seed["primary_span"]["start_line"]
    )
    assert isinstance(edit_plan_seed["related_spans"], list)
    for related_span in edit_plan_seed["related_spans"]:
        assert {"file", "symbol", "start_line", "end_line", "depth", "score", "reasons"} <= set(
            related_span
        )
        assert related_span["end_line"] >= related_span["start_line"]
    assert isinstance(edit_plan_seed["dependent_files"], list)
    assert isinstance(edit_plan_seed["edit_ordering"], list)
    if primary_file is not None:
        assert edit_plan_seed["edit_ordering"][0] == str(primary_file.resolve())
    else:
        assert all(isinstance(path, str) for path in edit_plan_seed["edit_ordering"])
    assert 0.0 <= edit_plan_seed["rollback_risk"] <= 1.0
    assert isinstance(edit_plan_seed["validation_plan"], list)
    assert edit_plan_seed["validation_plan"]
    for step in edit_plan_seed["validation_plan"]:
        assert {"command", "scope", "runner", "confidence", "detection"} <= set(step)
        assert isinstance(step["command"], str)
        assert step["scope"] in {"symbol", "file", "repo"}
        assert isinstance(step["runner"], str)
        assert step["detection"] in {"detected", "heuristic", "generic"}
        assert 0.0 <= step["confidence"] <= 1.0


def _assert_navigation_pack(
    navigation_pack: dict[str, object],
    *,
    primary_file: Path | None = None,
    primary_symbol_name: str | None = None,
) -> None:
    assert {
        "primary_target",
        "follow_up_reads",
        "parallel_read_groups",
        "related_tests",
        "validation_commands",
        "edit_ordering",
        "rollback_risk",
    } <= set(navigation_pack)
    primary_target = navigation_pack["primary_target"]
    assert {"file", "symbol", "start_line", "end_line", "mention_ref", "reasons"} <= set(
        primary_target
    )
    if primary_file is not None:
        assert primary_target["file"] == str(primary_file.resolve())
    else:
        assert isinstance(primary_target["file"], str)
    if primary_symbol_name is not None:
        assert primary_target["symbol"] == primary_symbol_name
    else:
        assert isinstance(primary_target["symbol"], str)
    assert primary_target["mention_ref"].startswith(primary_target["file"])
    assert "#L" in primary_target["mention_ref"]
    assert isinstance(navigation_pack["follow_up_reads"], list)
    assert navigation_pack["follow_up_reads"]
    for item in navigation_pack["follow_up_reads"]:
        assert {
            "file",
            "symbol",
            "start_line",
            "end_line",
            "mention_ref",
            "role",
            "rationale",
        } <= set(item)
        assert item["mention_ref"].startswith(item["file"])
        assert "#L" in item["mention_ref"]
        assert item["role"] in {"primary", "related", "test"}
    assert isinstance(navigation_pack["related_tests"], list)
    assert isinstance(navigation_pack["validation_commands"], list)
    assert navigation_pack["validation_commands"]
    assert isinstance(navigation_pack["parallel_read_groups"], list)
    assert navigation_pack["parallel_read_groups"]
    expected_phase = 0
    for group in navigation_pack["parallel_read_groups"]:
        assert {"phase", "label", "can_parallelize", "mentions", "files", "roles"} <= set(group)
        assert group["phase"] == expected_phase
        expected_phase += 1
        assert group["label"] in {"primary", "related", "test"}
        assert isinstance(group["can_parallelize"], bool)
        assert isinstance(group["mentions"], list)
        assert group["mentions"]
        assert isinstance(group["files"], list)
        assert group["files"]
        assert isinstance(group["roles"], list)
        assert group["roles"]
    assert isinstance(navigation_pack["edit_ordering"], list)
    assert 0.0 <= navigation_pack["rollback_risk"] <= 1.0


class _FakeScanner:
    def __init__(self, config=None):
        pass

    def walk(self, path):
        yield from _FAKE_WALK.get(path, [])


class _FakeGpuPipeline(_FakePipeline):
    def __init__(self, force_cpu=False, config=None):
        super().__init__(force_cpu=force_cpu, config=config)
        self.selected_gpu_device_ids = [7, 3]
        self.selected_gpu_chunk_plan_mb = [(7, 256), (3, 512)]


class _FakeGpuPlanOnlyPipeline(_FakePipeline):
    def __init__(self, force_cpu=False, config=None):
        super().__init__(force_cpu=force_cpu, config=config)
        self.selected_backend_name = "RipgrepBackend"
        self.selected_backend_reason = "gpu_explicit_ids_no_gpu_backend_fallback"
        self.selected_gpu_device_ids = []
        self.selected_gpu_chunk_plan_mb = [(7, 256), (3, 512)]


@dataclass
class _FakeRipgrepBackend:
    called: bool = False
    seen_paths: list[str] | None = None
    seen_pattern: str | None = None

    def search_passthrough(self, paths, pattern, config=None):
        self.called = True
        self.seen_paths = list(paths)
        self.seen_pattern = pattern
        return 0


class RipgrepBackend:
    def __init__(self, result: SearchResult):
        self._result = result

    def search(self, file_path, pattern, config=None) -> SearchResult:
        return self._result

    def search_passthrough(self, paths, pattern, config=None):
        return 0


_FAKE_BACKEND = _FakeBackend(results_by_file={})
_FAKE_WALK: dict[str, list[str]] = {}
_LAST_PIPELINE_CONFIG = None


def _patch_cli_dependencies(monkeypatch):
    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakePipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeScanner)
    monkeypatch.setattr(
        "tensor_grep.backends.ripgrep_backend.RipgrepBackend.is_available",
        lambda self: False,
    )


class _FakeRipgrepPipeline:
    def __init__(self, force_cpu=False, config=None):
        self.backend = RipgrepBackend(
            SearchResult(
                matches=[],
                matched_file_paths=["a.py"],
                total_files=1,
                total_matches=3,
                routing_backend="RipgrepBackend",
                routing_reason="rg_count",
            )
        )
        self.selected_backend_name = "RipgrepBackend"
        self.selected_backend_reason = "rg_count"
        self.selected_gpu_device_ids = []
        self.selected_gpu_chunk_plan_mb = []

    def get_backend(self):
        return self.backend


def test_files_mode_lists_candidates(monkeypatch):
    global _FAKE_WALK
    _FAKE_WALK = {".": ["a.py", "b.py"]}
    _patch_cli_dependencies(monkeypatch)

    runner = CliRunner()
    result = runner.invoke(app, ["search", "x", ".", "--files"])

    assert result.exit_code == 0
    assert result.stdout.strip().splitlines() == ["a.py", "b.py"]


def test_files_mode_lists_candidates_without_pattern(monkeypatch):
    global _FAKE_WALK
    _FAKE_WALK = {".": ["a.py", "b.py"]}
    _patch_cli_dependencies(monkeypatch)

    runner = CliRunner()
    result = runner.invoke(app, ["search", "--files", "."])

    assert result.exit_code == 0
    assert result.stdout.strip().splitlines() == ["a.py", "b.py"]


def test_ripgrep_backend_builds_regexp_patterns_as_e_options(monkeypatch):
    from tensor_grep.backends.ripgrep_backend import RipgrepBackend

    monkeypatch.setattr(RipgrepBackend, "_get_binary_name", lambda self: "rg")

    cmd = RipgrepBackend()._build_cmd(
        file_path=["."],
        pattern="-needle",
        config=SearchConfig(regexp=["-needle", "plain"], sort_by="path", line_number=None),
        json_mode=False,
    )

    pattern_index = cmd.index("-needle")
    assert cmd[pattern_index - 1 : pattern_index + 3] == ["-e", "-needle", "-e", "plain"]
    assert cmd[-1] == "."


def test_files_mode_refuses_unbounded_broad_generated_root_scan(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('ok')\n", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "pkg.js").write_text("console.log('dep')\n", encoding="utf-8")

    result = CliRunner().invoke(app, ["search", "--files", str(tmp_path), "--hidden"])

    assert result.exit_code == 2
    assert "broad generated-root scan refused" in result.output
    assert "safety guard, not a zero-match result" in result.output
    assert "node_modules" in result.output
    assert "--glob" in result.output
    assert "--max-depth" in result.output
    assert "--allow-broad-generated-scan" in result.output
    assert "For bounded output:" in result.output
    assert "tg search --files <path> --hidden --max-depth" in result.output
    assert "For intentional broad scans:" in result.output
    assert "--allow-broad-generated-scan" in result.output


def test_plain_search_refuses_unbounded_multi_project_workspace_root(tmp_path: Path):
    workspace = tmp_path / "projects"
    workspace.mkdir()
    for project_name, marker_name in (
        ("alpha", "pyproject.toml"),
        ("beta", "package.json"),
        ("gamma", "Cargo.toml"),
    ):
        project = workspace / project_name
        (project / "src").mkdir(parents=True)
        (project / marker_name).write_text("", encoding="utf-8")
        (project / "src" / "app.py").write_text("needle\n", encoding="utf-8")

    result = CliRunner().invoke(app, ["search", "needle", str(workspace)])

    assert result.exit_code == 2
    assert "broad workspace-root scan refused" in result.output
    assert "safety guard, not a zero-match result" in result.output
    assert "alpha" in result.output
    assert "beta" in result.output
    assert "gamma" in result.output
    assert "--glob" in result.output
    assert "--max-depth" in result.output
    assert "--allow-broad-generated-scan" in result.output


def test_files_mode_refuses_generated_root_before_rg_passthrough(monkeypatch, tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('ok')\n", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "pkg.js").write_text("console.log('dep')\n", encoding="utf-8")
    monkeypatch.setattr(
        "tensor_grep.backends.ripgrep_backend.RipgrepBackend.is_available",
        lambda self: True,
    )
    monkeypatch.setattr(
        "tensor_grep.backends.ripgrep_backend.RipgrepBackend.search_passthrough",
        lambda self, paths, pattern, config=None: pytest.fail(
            "generated-root guard should run before rg passthrough"
        ),
    )

    result = CliRunner().invoke(app, ["search", "--files", str(tmp_path), "--hidden"])

    assert result.exit_code == 2
    assert "broad generated-root scan refused" in result.output


def test_files_mode_json_does_not_passthrough_to_rg(monkeypatch, tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('ok')\n", encoding="utf-8")
    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakePipeline)
    monkeypatch.setattr(
        "tensor_grep.backends.ripgrep_backend.RipgrepBackend.is_available",
        lambda self: True,
    )
    monkeypatch.setattr(
        "tensor_grep.backends.ripgrep_backend.RipgrepBackend.search_passthrough",
        lambda self, paths, pattern, config=None: pytest.fail(
            "--files --json should keep tensor-grep files-mode semantics"
        ),
    )

    result = CliRunner().invoke(app, ["search", "--files", "--json", str(tmp_path)])

    assert result.exit_code == 0
    assert "app.py" in result.stdout


def test_files_mode_refuses_cwd_generated_root_scan(monkeypatch, tmp_path: Path):
    venv_root = tmp_path / ".venv"
    package_dir = venv_root / "Lib" / "site-packages" / "pkg"
    package_dir.mkdir(parents=True)
    (package_dir / "module.py").write_text("print('dep')\n", encoding="utf-8")
    monkeypatch.chdir(venv_root)

    result = CliRunner().invoke(app, ["search", "--files", ".", "--hidden", "--no-ignore"])

    assert result.exit_code == 2
    assert "broad generated-root scan refused" in result.output
    assert ".venv" in result.output


def test_files_mode_allows_bounded_broad_generated_root_scan(monkeypatch, tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('ok')\n", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "pkg.js").write_text("console.log('dep')\n", encoding="utf-8")
    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakePipeline)
    monkeypatch.setattr(
        "tensor_grep.backends.ripgrep_backend.RipgrepBackend.is_available",
        lambda self: False,
    )

    result = CliRunner().invoke(
        app,
        ["search", "--files", str(tmp_path), "--hidden", "--glob", "*.py"],
    )

    assert result.exit_code == 0
    assert "src" in result.stdout
    assert "app.py" in result.stdout
    assert "node_modules" not in result.stdout


def test_files_mode_allows_explicit_broad_generated_root_scan(monkeypatch, tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('ok')\n", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "pkg.js").write_text("console.log('dep')\n", encoding="utf-8")
    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakePipeline)
    monkeypatch.setattr(
        "tensor_grep.backends.ripgrep_backend.RipgrepBackend.is_available",
        lambda self: False,
    )

    result = CliRunner().invoke(
        app,
        [
            "search",
            "--files",
            str(tmp_path),
            "--hidden",
            "--no-ignore",
            "--allow-broad-generated-scan",
        ],
    )

    assert result.exit_code == 0
    assert "src" in result.stdout
    assert "app.py" in result.stdout
    assert "node_modules" in result.stdout
    assert "pkg.js" in result.stdout


def test_plain_hidden_search_does_not_trigger_broad_generated_root_guard(
    tmp_path: Path,
):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('ok')\n", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()

    refused, generated_dirs = _should_refuse_unbounded_generated_scan(
        [str(tmp_path)],
        SearchConfig(hidden=True),
        allow_broad_generated_scan=False,
        files_mode=False,
    )

    assert refused is False
    assert generated_dirs == []


def test_no_ignore_content_search_allows_generated_child_dirs(
    tmp_path: Path,
):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('ok')\n", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()

    refused, generated_dirs = _should_refuse_unbounded_generated_scan(
        [str(tmp_path)],
        SearchConfig(no_ignore=True),
        allow_broad_generated_scan=False,
        files_mode=False,
    )

    assert refused is False
    assert generated_dirs == []


def test_no_ignore_content_search_allows_windows_appdata_child_dir(
    tmp_path: Path,
):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('ok')\n", encoding="utf-8")
    (tmp_path / "AppData").mkdir()

    refused, generated_dirs = _should_refuse_unbounded_generated_scan(
        [str(tmp_path)],
        SearchConfig(no_ignore=True, hidden=True),
        allow_broad_generated_scan=False,
        files_mode=False,
    )

    assert refused is False
    assert generated_dirs == []


def test_normal_no_ignore_search_allows_broad_generated_child_before_rg_passthrough(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr("tensor_grep.cli.main.resolve_native_tg_binary", lambda: None)
    (tmp_path / "AppData").mkdir()
    (tmp_path / "AppData" / "hit.txt").write_text("foo\n", encoding="utf-8")
    (tmp_path / "normal.txt").write_text("foo\n", encoding="utf-8")

    result = CliRunner().invoke(
        app,
        ["search", "foo", str(tmp_path), "--hidden", "--no-ignore", "--cpu"],
    )

    assert result.exit_code == 0, result.output
    assert "normal.txt" in result.stdout
    assert "AppData" in result.stdout


def test_no_ignore_search_treats_cwd_generated_root_as_broad_generated_scan(
    monkeypatch, tmp_path: Path
):
    venv_root = tmp_path / ".venv"
    venv_root.mkdir()
    monkeypatch.chdir(venv_root)

    refused, generated_dirs = _should_refuse_unbounded_generated_scan(
        ["."],
        SearchConfig(no_ignore=True),
        allow_broad_generated_scan=False,
        files_mode=False,
    )

    assert refused is True
    assert generated_dirs == [".venv"]


def test_workspace_root_guard_allows_bounded_workspace_scan(tmp_path: Path):
    workspace = tmp_path / "projects"
    workspace.mkdir()
    for project_name in ("alpha", "beta", "gamma"):
        project = workspace / project_name
        project.mkdir()
        (project / "pyproject.toml").write_text("", encoding="utf-8")

    refused, project_dirs = _should_refuse_unbounded_workspace_root_scan(
        [str(workspace)],
        SearchConfig(glob=["*.py"]),
        allow_broad_generated_scan=False,
    )

    assert refused is False
    assert project_dirs == []


def test_workspace_root_guard_allows_explicit_workspace_scan(tmp_path: Path):
    workspace = tmp_path / "projects"
    workspace.mkdir()
    for project_name in ("alpha", "beta", "gamma"):
        project = workspace / project_name
        project.mkdir()
        (project / "pyproject.toml").write_text("", encoding="utf-8")

    refused, project_dirs = _should_refuse_unbounded_workspace_root_scan(
        [str(workspace)],
        SearchConfig(),
        allow_broad_generated_scan=True,
    )

    assert refused is False
    assert project_dirs == []


def test_workspace_root_guard_allows_real_repo_root(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text("", encoding="utf-8")
    for project_name in ("alpha", "beta", "gamma"):
        project = repo / project_name
        project.mkdir()
        (project / "pyproject.toml").write_text("", encoding="utf-8")

    refused, project_dirs = _should_refuse_unbounded_workspace_root_scan(
        [str(repo)],
        SearchConfig(),
        allow_broad_generated_scan=False,
    )

    assert refused is False
    assert project_dirs == []


def test_glob_case_insensitive_matches_case_folded_paths(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
):
    from tensor_grep.backends.ripgrep_backend import RipgrepBackend

    if not RipgrepBackend().is_available():
        pytest.skip("rg is not available")

    target = tmp_path / "sample.TXT"
    target.write_text("hello\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["search", "hello", str(tmp_path), "--glob-case-insensitive", "--glob", "*.txt"],
    )
    captured = capfd.readouterr()

    assert result.exit_code == 0
    assert "sample.TXT" in captured.out


def test_debug_passthrough_keeps_stdout_match_only(tmp_path: Path):
    from tensor_grep.backends.ripgrep_backend import RipgrepBackend

    if not RipgrepBackend().is_available():
        pytest.skip("rg is not available")

    target = tmp_path / "sample.txt"
    target.write_text("hello\n", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, "-m", "tensor_grep.cli.main", "search", "hello", str(target), "--debug"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout == "hello\n"
    assert "routing.backend=RipgrepBackend" not in result.stdout


def test_stats_passthrough_matches_ripgrep_stdout_contract(tmp_path: Path):
    from tensor_grep.backends.ripgrep_backend import RipgrepBackend
    from tensor_grep.cli.runtime_paths import resolve_ripgrep_binary

    if not RipgrepBackend().is_available():
        pytest.skip("rg is not available")

    target = tmp_path / "sample.txt"
    target.write_text("hello\n", encoding="utf-8")
    rg_binary = resolve_ripgrep_binary()
    assert rg_binary is not None

    expected = subprocess.run(
        [str(rg_binary), "--stats", "hello", str(target)],
        capture_output=True,
        text=True,
        check=False,
    )
    result = subprocess.run(
        [sys.executable, "-m", "tensor_grep.cli.main", "search", "hello", str(target), "--stats"],
        capture_output=True,
        text=True,
        check=False,
    )

    def _normalize_stats_timing(text: str) -> str:
        normalized_lines: list[str] = []
        for line in text.splitlines():
            if line.endswith(" seconds spent searching"):
                normalized_lines.append("<SEARCH_TIME>")
                continue
            if re.fullmatch(r"\d+\.\d+ seconds(?: total)?", line):
                normalized_lines.append("<TOTAL_TIME>")
                continue
            normalized_lines.append(line)
        return "\n".join(normalized_lines)

    assert result.returncode == expected.returncode == 0
    assert _normalize_stats_timing(result.stdout) == _normalize_stats_timing(expected.stdout)
    assert result.stderr == expected.stderr


@pytest.mark.parametrize(
    ("generator", "shell"),
    [
        ("complete-bash", "bash"),
        ("complete-zsh", "zsh"),
        ("complete-fish", "fish"),
        ("complete-powershell", "powershell"),
    ],
)
def test_search_generate_should_emit_shell_completion_script(generator: str, shell: str) -> None:
    runner = CliRunner()

    result = runner.invoke(app, ["search", "--generate", generator], prog_name="tg")

    assert result.exit_code == 0
    assert result.output.strip() == get_completion_script(
        prog_name="tg",
        complete_var="_TG_COMPLETE",
        shell=shell,
    )


def test_search_generate_should_reject_unsupported_generator() -> None:
    runner = CliRunner()

    result = runner.invoke(app, ["search", "--generate", "complete-elvish"], prog_name="tg")

    assert result.exit_code == 2
    assert "Unsupported" in result.output
    assert "complete-elvish" in result.output
    assert "complete-powershell" in result.output


def test_search_generate_help_lists_only_supported_generators() -> None:
    result = CliRunner().invoke(app, ["search", "--help"])

    assert result.exit_code == 0
    help_text = _strip_ansi(result.stdout)
    assert "complete-bash" in help_text
    assert "e.g. man" not in help_text


def test_search_pcre2_version_should_run_special_action_without_pattern(
    monkeypatch, tmp_path: Path
) -> None:
    rg_binary = tmp_path / "rg.exe"
    rg_binary.write_text("", encoding="utf-8")
    seen: dict[str, object] = {}
    monkeypatch.setattr("tensor_grep.cli.main.resolve_native_tg_binary", lambda: None)
    monkeypatch.setattr("tensor_grep.cli.main.resolve_ripgrep_binary", lambda: rg_binary)

    def _fake_run(cmd, capture_output=False, text=False):
        seen["cmd"] = list(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="PCRE2 10.42\n", stderr="")

    monkeypatch.setattr("tensor_grep.cli.main.subprocess.run", _fake_run)

    result = CliRunner().invoke(app, ["search", "--pcre2-version"])

    assert result.exit_code == 0
    assert seen["cmd"] == [str(rg_binary), "--pcre2-version"]
    assert "PCRE2 10.42" in result.stdout


def test_search_type_list_should_run_special_action_without_pattern(
    monkeypatch, tmp_path: Path
) -> None:
    rg_binary = tmp_path / "rg.exe"
    rg_binary.write_text("", encoding="utf-8")
    seen: dict[str, object] = {}
    monkeypatch.setattr("tensor_grep.cli.main.resolve_native_tg_binary", lambda: None)
    monkeypatch.setattr("tensor_grep.cli.main.resolve_ripgrep_binary", lambda: rg_binary)

    def _fake_run(cmd, capture_output=False, text=False):
        seen["cmd"] = list(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="rust: *.rs\n", stderr="")

    monkeypatch.setattr("tensor_grep.cli.main.subprocess.run", _fake_run)

    result = CliRunner().invoke(app, ["search", "--type-list"])

    assert result.exit_code == 0
    assert seen["cmd"] == [str(rg_binary), "--type-list"]
    assert "rust: *.rs" in result.stdout


def test_search_type_list_should_use_builtin_fallback_without_native_or_rg(monkeypatch) -> None:
    monkeypatch.setattr("tensor_grep.cli.main.resolve_native_tg_binary", lambda: None)
    monkeypatch.setattr("tensor_grep.cli.main.resolve_ripgrep_binary", lambda: None)

    result = CliRunner().invoke(app, ["search", "--type-list"])

    assert result.exit_code == 0
    assert "python: *.py" in result.stdout
    assert "rust: *.rs" in result.stdout


def test_search_type_list_should_not_mask_backend_failure(monkeypatch, tmp_path) -> None:
    native_binary = tmp_path / "tg.exe"
    native_binary.write_text("binary", encoding="utf-8")
    monkeypatch.setattr("tensor_grep.cli.main.resolve_native_tg_binary", lambda: native_binary)
    monkeypatch.setattr("tensor_grep.cli.main.resolve_ripgrep_binary", lambda: None)

    def _fake_run(cmd, capture_output=False, text=False):
        return subprocess.CompletedProcess(cmd, 2, stdout="", stderr="backend failed")

    monkeypatch.setattr("tensor_grep.cli.main.subprocess.run", _fake_run)

    result = CliRunner().invoke(app, ["search", "--type-list"])

    assert result.exit_code == 2
    assert "backend failed" in result.stderr
    assert "python: *.py" not in result.stdout


def test_new_rule_should_respect_base_dir_and_requested_name(tmp_path: Path) -> None:
    runner = CliRunner()
    base_dir = tmp_path / "ast-project"

    result = runner.invoke(
        app,
        [
            "new",
            "rule",
            "demo",
            "--lang",
            "python",
            "--yes",
            "--base-dir",
            str(base_dir),
        ],
    )

    assert result.exit_code == 0, result.output
    assert (base_dir / "rules" / "demo.yml").exists()
    assert not (tmp_path / "sgconfig.yml").exists()
    assert not (base_dir / "rules" / "sample-rule.yml").exists()


def test_new_project_name_should_scaffold_named_directory() -> None:
    runner = CliRunner()

    with runner.isolated_filesystem():
        result = runner.invoke(app, ["new", "project", "demo"])

        assert result.exit_code == 0, result.output
        assert (Path("demo") / "sgconfig.yml").exists()
        assert (Path("demo") / "rules" / "sample-rule.yml").exists()
        assert (Path("demo") / "tests" / "sample-test.yml").exists()
        assert not Path("sgconfig.yml").exists()


def test_new_project_name_with_base_dir_should_scaffold_under_named_directory(
    tmp_path: Path,
) -> None:
    runner = CliRunner()

    result = runner.invoke(app, ["new", "project", "demo", "--base-dir", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert (tmp_path / "demo" / "sgconfig.yml").exists()
    assert (tmp_path / "demo" / "rules" / "sample-rule.yml").exists()
    assert (tmp_path / "demo" / "tests" / "sample-test.yml").exists()
    assert not (tmp_path / "sgconfig.yml").exists()


def test_new_unknown_scaffold_kind_should_reject_before_writing(tmp_path: Path) -> None:
    runner = CliRunner()

    result = runner.invoke(app, ["new", "widget", "demo", "--base-dir", str(tmp_path)])

    assert result.exit_code == 1
    assert "Unsupported scaffold kind" in result.stderr
    assert not (tmp_path / "sgconfig.yml").exists()
    assert not (tmp_path / "widget").exists()
    assert not (tmp_path / "rules").exists()
    assert not (tmp_path / "tests").exists()


def test_session_daemon_help_lists_lifecycle_commands() -> None:
    runner = CliRunner()

    result = runner.invoke(app, ["session", "daemon", "--help"])

    assert result.exit_code == 0
    assert "start" in result.stdout
    assert "status" in result.stdout
    assert "stop" in result.stdout


def test_session_context_help_mentions_daemon_flag() -> None:
    runner = CliRunner()

    result = runner.invoke(app, ["session", "context", "--help"])

    assert result.exit_code == 0
    normalized_output = re.sub(r"\s+", " ", re.sub(r"\x1b\[[0-9;]*m", "", result.stdout))
    assert "-daemon" in normalized_output
    assert "warm localhost" in normalized_output
    assert "session daemon" in normalized_output


def test_lsp_help_mentions_provider_modes() -> None:
    runner = CliRunner()

    result = runner.invoke(app, ["lsp", "--help"])

    assert result.exit_code == 0
    help_text = _strip_ansi(result.stdout)
    normalized_help = re.sub(r"\s+", " ", re.sub(r"[╭╮╰╯─│]+", " ", help_text))
    assert "--provider" in help_text
    assert "native=repo-map only" in normalized_help
    assert "experimental" in help_text.lower()
    assert "Examples:" in help_text
    assert "--provider hybrid" in normalized_help
    assert "--debug-trace" in help_text


def test_lsp_rejects_unknown_provider_mode() -> None:
    runner = CliRunner()

    result = runner.invoke(app, ["lsp", "--provider", "remote"])

    assert result.exit_code != 0
    combined_output = _strip_ansi(result.stdout + result.stderr)
    assert "Unsupported LSP provider mode" in combined_output
    assert "native, lsp, hybrid" in combined_output


def test_lsp_debug_trace_emits_json_probe_payload(monkeypatch, tmp_path) -> None:
    from tensor_grep.cli.lsp_external_provider import ExternalLSPProviderManager

    def _fake_debug_trace(self, *, language, workspace_root, probe_timeout_seconds=None):
        return {
            "schema_version": 1,
            "language": language,
            "workspace_root": str(Path(workspace_root).resolve()),
            "probe_timeout_seconds": probe_timeout_seconds,
            "status": {"health_status": "ready", "lsp_proof": True},
            "trace": [{"event": "send_request", "method": "initialize"}],
            "stderr_tail": [],
        }

    monkeypatch.setattr(ExternalLSPProviderManager, "provider_debug_trace", _fake_debug_trace)

    result = CliRunner().invoke(
        app,
        [
            "lsp",
            "--debug-trace",
            "python",
            "--path",
            str(tmp_path),
            "--probe-timeout-seconds",
            "0.5",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == 1
    assert payload["language"] == "python"
    assert payload["probe_timeout_seconds"] == 0.5
    assert payload["trace"][0]["method"] == "initialize"


def test_lsp_setup_help_mentions_managed_provider_install() -> None:
    runner = CliRunner()

    result = runner.invoke(app, ["lsp-setup", "--help"], color=False)
    help_text = _strip_ansi(result.stdout)

    assert result.exit_code == 0
    assert "--json" in help_text
    assert "managed external LSP providers" in re.sub(r"\s+", " ", help_text)
    normalized_help = re.sub(r"\s+", " ", re.sub(r"[╭╮╰╯─│┌┐└┘]+", " ", help_text))
    assert "does not prove semantic navigation" in normalized_help
    assert "health_status" in normalized_help


def test_doctor_help_mentions_lsp_and_json() -> None:
    runner = CliRunner()

    result = runner.invoke(app, ["doctor", "--help"])
    help_text = _strip_ansi(result.stdout)

    assert result.exit_code == 0
    assert "--with-lsp" in help_text
    assert "--json" in help_text
    assert "--config" in help_text
    # Handle rich text wrapping that may split phrases
    normalized_help = re.sub(r"\s+", " ", re.sub(r"[╭╮╰╯─│┌┐└┘]+", " ", help_text))
    assert "system, GPU, cache" in normalized_help
    assert "provider-proof diagnostics" in normalized_help
    assert "provider availability is not navigation proof" in normalized_help.lower()
    assert "health_status" in normalized_help
    assert "health_check" in normalized_help
    assert "AST" in normalized_help
    assert "PowerShell" in normalized_help
    assert "cmd.exe" in normalized_help
    assert "literal patterns" in normalized_help


def test_doctor_lsp_probe_timeout_defaults_to_windows_budget(monkeypatch) -> None:
    monkeypatch.delenv("TG_DOCTOR_LSP_PROBE_TIMEOUT_SECONDS", raising=False)
    monkeypatch.setattr(cli_main.sys, "platform", "win32")

    assert cli_main._doctor_lsp_probe_timeout_seconds() == pytest.approx(15.0)


def test_doctor_lsp_probe_timeout_defaults_to_provider_budget_on_posix(monkeypatch) -> None:
    monkeypatch.delenv("TG_DOCTOR_LSP_PROBE_TIMEOUT_SECONDS", raising=False)
    monkeypatch.setattr(cli_main.sys, "platform", "linux")

    assert cli_main._doctor_lsp_probe_timeout_seconds() == pytest.approx(15.0)


def test_doctor_lsp_probe_timeout_allows_env_override(monkeypatch) -> None:
    monkeypatch.setenv("TG_DOCTOR_LSP_PROBE_TIMEOUT_SECONDS", "7.5")
    monkeypatch.setattr(cli_main.sys, "platform", "win32")

    assert cli_main._doctor_lsp_probe_timeout_seconds() == pytest.approx(7.5)


def test_doctor_lsp_probe_timeout_ignores_invalid_env(monkeypatch) -> None:
    monkeypatch.setenv("TG_DOCTOR_LSP_PROBE_TIMEOUT_SECONDS", "slow")
    monkeypatch.setattr(cli_main.sys, "platform", "win32")

    assert cli_main._doctor_lsp_probe_timeout_seconds() == pytest.approx(15.0)


def test_doctor_json_includes_runtime_session_and_lsp(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("tensor_grep.cli.main._doctor_installed_version", lambda: "9.9.9")
    monkeypatch.setattr(
        "tensor_grep.cli.main.resolve_native_tg_binary",
        lambda: tmp_path / "rust_core" / "target" / "debug" / "tg.exe",
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_session_daemon_status",
        lambda path: {"running": True, "host": "127.0.0.1", "port": 43123, "pid": 9001},
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_lsp_provider_statuses",
        lambda path: [
            {
                "language": "python",
                "available": True,
                "running": False,
                "command": ["pyright-langserver", "--stdio"],
                "command_source": "managed",
                "managed_provider_root": str(tmp_path / "providers"),
                "last_error": None,
                "health_status": "ready",
                "health_check": "probe",
                "lsp_proof": True,
            }
        ],
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_ast_grep_status",
        lambda: {
            "schema_version": 1,
            "available": True,
            "binary": "ast-grep",
            "wrapper_backend": "AstGrepWrapperBackend",
            "required_for": "tg run ast-grep semantic options",
            "semantic_run_options": ["--selector", "--strictness", "--stdin", "--globs"],
            "timeout_env": "TG_AST_GREP_TIMEOUT_SECONDS",
            "timeout_seconds": 60.0,
        },
    )
    monkeypatch.setenv("TG_RUST_EARLY_RG", "1")
    monkeypatch.setenv("TG_RUST_EARLY_POSITIONAL_RG", "1")
    monkeypatch.setenv("TG_FORCE_CPU", "1")
    monkeypatch.setenv("TG_RESIDENT_AST", "1")
    monkeypatch.setenv("TG_DOCTOR_LSP_PROBE_TIMEOUT_SECONDS", "6.5")

    runner = CliRunner()
    result = runner.invoke(app, ["doctor", str(tmp_path), "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["version"] == "9.9.9"
    assert payload["schema_version"] == 2
    assert payload["doctor_schema_version"] == 2
    assert payload["root"] == str(tmp_path.resolve())
    assert payload["native_tg_binary_exists"] is True
    assert payload["env"]["TG_RUST_EARLY_RG"] == "1"
    assert payload["env"]["TG_RUST_EARLY_POSITIONAL_RG"] == "1"
    assert payload["env"]["TG_FORCE_CPU"] == "1"
    assert payload["env"]["TG_RESIDENT_AST"] == "1"
    assert payload["session_daemon"]["running"] is True
    assert payload["lsp"]["enabled"] is True
    assert payload["lsp"]["schema_version"] == 2
    assert payload["lsp"]["probe_timeout_seconds"] == pytest.approx(6.5)
    assert payload["lsp"]["providers"][0]["language"] == "python"
    assert payload["lsp"]["providers"][0]["command_source"] == "managed"
    assert payload["lsp"]["providers"][0]["managed_provider_root"] == str(tmp_path / "providers")
    assert payload["lsp"]["providers"][0]["health_status"] == "ready"
    assert payload["lsp"]["providers"][0]["health_check"] == "probe"
    assert payload["lsp"]["providers"][0]["lsp_proof"] is True
    assert payload["lsp_provider_items"] == payload["lsp"]["providers"]
    assert payload["lsp"]["providers_by_language"]["python"]["health"] == "ready"
    assert payload["lsp"]["providers_by_language"]["python"]["health_status"] == "ready"
    assert payload["lsp_providers"]["python"]["health"] == "ready"
    guidance = payload["shell_escaping_guidance"]
    assert guidance["platform"] == "windows"
    assert "PowerShell double quotes expand $NAME" in guidance["powershell"]["summary"]
    assert "single quotes" in guidance["powershell"]["recommendation"]
    assert guidance["powershell"]["literal_pattern_example"] == "tg search '$NAME' ."
    assert "|" in guidance["cmd"]["metacharacters"]
    assert "^" in guidance["cmd"]["recommendation"]
    assert payload["ast_grep"]["available"] is True
    assert payload["ast_grep"]["semantic_run_options"] == [
        "--selector",
        "--strictness",
        "--stdin",
        "--globs",
    ]
    assert payload["ast_grep"]["timeout_env"] == "TG_AST_GREP_TIMEOUT_SECONDS"


def test_doctor_json_no_lsp_keeps_empty_schema_compatibility(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("tensor_grep.cli.main._doctor_installed_version", lambda: "9.9.9")
    monkeypatch.setattr("tensor_grep.cli.main.resolve_native_tg_binary", lambda: None)
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_session_daemon_status",
        lambda path: {"running": False},
    )

    result = CliRunner().invoke(app, ["doctor", str(tmp_path), "--json", "--no-lsp"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == 2
    assert payload["doctor_schema_version"] == 2
    assert payload["lsp"]["enabled"] is False
    assert payload["lsp"]["schema_version"] == 2
    assert payload["lsp"]["providers"] == []
    assert payload["lsp"]["providers_by_language"] == {}
    assert payload["lsp_provider_items"] == []
    assert payload["lsp_providers"] == {}


def test_doctor_text_reports_ast_grep_availability(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("tensor_grep.cli.main._doctor_installed_version", lambda: "9.9.9")
    monkeypatch.setattr("tensor_grep.cli.main.resolve_native_tg_binary", lambda: None)
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_session_daemon_status",
        lambda path: {"running": False},
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_ast_grep_status",
        lambda: {
            "schema_version": 1,
            "available": True,
            "binary": "ast-grep",
            "wrapper_backend": "AstGrepWrapperBackend",
            "required_for": "tg run ast-grep semantic options",
            "semantic_run_options": ["--selector", "--strictness", "--stdin", "--globs"],
            "timeout_env": "TG_AST_GREP_TIMEOUT_SECONDS",
            "timeout_seconds": 60.0,
        },
    )

    result = CliRunner().invoke(app, ["doctor", str(tmp_path), "--no-lsp"])

    assert result.exit_code == 0
    assert "ast_grep: available=True binary=ast-grep" in result.stdout
    assert "semantic_run_options=--selector/--strictness/--stdin/--globs" in result.stdout


def test_doctor_json_includes_gpu_search_runtime_probe(monkeypatch, tmp_path: Path) -> None:
    native_tg = tmp_path / "tg.exe"
    native_tg.write_text("native", encoding="utf-8")
    monkeypatch.setattr("tensor_grep.cli.main._doctor_installed_version", lambda: "9.9.9")
    monkeypatch.setattr("tensor_grep.cli.main.resolve_native_tg_binary", lambda: native_tg)
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_session_daemon_status",
        lambda path: {"running": False},
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_gpu_search_runtime_probe",
        lambda binary: {
            "status": "unsupported",
            "requested_gpu_device_ids": [0],
            "command": f"{binary} search --gpu-device-ids 0 --json -F tg doctor gpu runtime probe",
            "routing_backend": "GpuSidecar",
            "routing_reason": "gpu-device-ids-explicit",
            "sidecar_used": True,
            "routing_gpu_device_ids": [],
            "error": (
                "GPU route did not use NativeGpuBackend "
                "(routing_backend=GpuSidecar, sidecar_used=True)."
            ),
        },
    )

    result = CliRunner().invoke(app, ["doctor", str(tmp_path), "--json", "--no-lsp"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    probe = payload["gpu"]["search_runtime_probe"]
    assert probe["status"] == "unsupported"
    assert probe["requested_gpu_device_ids"] == [0]
    assert probe["routing_backend"] == "GpuSidecar"
    assert probe["sidecar_used"] is True
    assert "NativeGpuBackend" in probe["error"]


def test_doctor_gpu_runtime_probe_redacts_temp_probe_path(monkeypatch, tmp_path: Path) -> None:
    native_tg = tmp_path / "tg.exe"
    native_tg.write_text("native", encoding="utf-8")

    def _fake_run(command, **_kwargs):
        payload = {
            "routing_backend": "GpuSidecar",
            "routing_reason": "gpu-device-ids-explicit",
            "sidecar_used": True,
            "routing_gpu_device_ids": [],
            "path": str(command[-1]),
            "matches": [{"file": str(command[-1]), "line": 1, "text": "probe"}],
        }
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    monkeypatch.setattr("tensor_grep.cli.main.subprocess.run", _fake_run)

    probe = cli_main._doctor_gpu_search_runtime_probe(native_tg)
    serialized = json.dumps(probe)

    assert probe["status"] == "unsupported"
    assert "tg-doctor-gpu-probe" not in serialized
    assert "probe.log" not in serialized
    assert "<doctor-gpu-probe-file>" in probe["command"]


def test_doctor_json_reports_native_version_mismatch(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("tensor_grep.cli.main._doctor_installed_version", lambda: "1.8.1")
    monkeypatch.setattr(
        "tensor_grep.cli.main.resolve_native_tg_binary",
        lambda: tmp_path / "rust_core" / "target" / "release" / "tg.exe",
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_rust_binary_version",
        lambda _binary: "tg 1.8.0",
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_session_daemon_status",
        lambda path: {"running": False},
    )

    runner = CliRunner()
    result = runner.invoke(app, ["doctor", str(tmp_path), "--json", "--no-lsp"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["version"] == "1.8.1"
    assert payload["rust_binary_version"] == "tg 1.8.0"
    assert payload["rust_binary_version_matches"] is False
    assert payload["rust_binary_expected_version"] == "1.8.1"


def test_doctor_json_reports_stale_in_tree_native_binary_as_skipped(
    monkeypatch,
    tmp_path: Path,
) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    native_binary = repo_root / "rust_core" / "target" / "debug" / "tg.exe"

    monkeypatch.setattr("tensor_grep.cli.main._doctor_installed_version", lambda: "1.8.19")
    monkeypatch.setattr("tensor_grep.cli.main.resolve_native_tg_binary", lambda: None)
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_skipped_native_tg_binaries",
        lambda _expected_version, _selected_binary: [
            {
                "path": str(native_binary),
                "kind": "in-tree-debug",
                "version": "tg 1.8.14",
                "version_status": "stale",
            }
        ],
        raising=False,
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_rust_binary_version",
        lambda _binary: "tg 1.8.14",
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_session_daemon_status",
        lambda path: {"running": False},
    )

    runner = CliRunner()
    result = runner.invoke(app, ["doctor", str(tmp_path), "--json", "--no-lsp"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["native_tg_binary"] is None
    assert payload["native_tg_binary_kind"] == "missing"
    assert payload["search_acceleration_backend"] in {"rust-core-extension", "python"}
    assert payload["rust_binary_version_status"] == "stale-skipped"
    assert payload["skipped_native_tg_binaries"] == [
        {
            "path": str(native_binary),
            "kind": "in-tree-debug",
            "version": "tg 1.8.14",
            "version_status": "stale",
        }
    ]
    assert "ignored stale in-tree native tg binary" in payload["rust_binary_version_warning"]
    assert "TG_NATIVE_TG_BINARY" in payload["rust_binary_remediation"]


def test_doctor_json_reports_path_tg_candidates(monkeypatch, tmp_path: Path) -> None:
    stale_tg = tmp_path / "Python314" / "Scripts" / "tg.exe"
    current_tg = tmp_path / "bin" / "tg.cmd"
    stale_tg.parent.mkdir(parents=True)
    current_tg.parent.mkdir(parents=True)
    stale_tg.write_text("stale\n", encoding="utf-8")
    current_tg.write_text("current\n", encoding="utf-8")

    monkeypatch.setattr("tensor_grep.cli.main._doctor_installed_version", lambda: "1.8.11")
    monkeypatch.setattr("tensor_grep.cli.main.resolve_native_tg_binary", lambda: None)
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_rust_core_extension_available",
        lambda: True,
        raising=False,
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_session_daemon_status",
        lambda path: {"running": False},
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_lsp_provider_statuses",
        lambda path: [],
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_path_tg_candidates",
        lambda: [
            {"path": str(stale_tg), "version": "tensor-grep 1.8.0"},
            {"path": str(current_tg), "version": "tensor-grep 1.8.11"},
        ],
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_fresh_shell_path_tg_candidates",
        lambda: [],
    )

    runner = CliRunner()
    result = runner.invoke(app, ["doctor", str(tmp_path), "--json", "--no-lsp"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["path_tg_candidates"] == [
        {"path": str(stale_tg), "version": "tensor-grep 1.8.0"},
        {"path": str(current_tg), "version": "tensor-grep 1.8.11"},
    ]
    assert payload["path_tg_first_version_matches"] is False
    assert payload["path_tg_first_version"] == "tensor-grep 1.8.0"
    assert payload["path_tg_first_launcher_kind"] == "python-entrypoint"


def test_doctor_path_tg_candidates_splits_windows_pathext_on_semicolon(
    monkeypatch,
    tmp_path: Path,
) -> None:

    monkeypatch.chdir(tmp_path)
    bridge_tg = Path("Python314") / "Scripts" / "tg.com"
    bridge_tg.parent.mkdir(parents=True)
    bridge_tg.write_text("tensor-grep bridge\n", encoding="utf-8")

    monkeypatch.setattr(cli_main.sys, "platform", "win32")
    monkeypatch.setattr(cli_main.os, "pathsep", ":")
    monkeypatch.setenv("PATHEXT", ".COM;.EXE;.BAT;.CMD")
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_tg_candidate_version",
        lambda _candidate: "tg 1.9.7",
    )

    candidates = cli_main._doctor_path_tg_candidates(str(bridge_tg.parent))

    assert candidates == [{"path": str(bridge_tg.resolve()), "version": "tg 1.9.7"}]


def test_doctor_path_tg_candidates_includes_powershell_shim_when_not_in_pathext(
    monkeypatch,
    tmp_path: Path,
) -> None:
    bin_dir = tmp_path / "bin"
    native_tg = bin_dir / "tg.exe"
    shim_tg = bin_dir / "tg.ps1"
    bin_dir.mkdir(parents=True)
    native_tg.write_text("native\n", encoding="utf-8")
    shim_tg.write_text("& $PSScriptRoot/tg.exe @args\n", encoding="utf-8")

    monkeypatch.setattr(cli_main.sys, "platform", "win32")
    monkeypatch.setenv("PATHEXT", ".COM;.EXE;.BAT;.CMD")
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_tg_candidate_version",
        lambda candidate: (
            "tensor-grep 1.13.12" if Path(candidate).suffix.lower() == ".ps1" else "tg 1.13.12"
        ),
    )

    candidates = cli_main._doctor_path_tg_candidates(str(bin_dir))

    assert candidates == [
        {"path": str(native_tg.resolve()), "version": "tg 1.13.12"},
        {"path": str(shim_tg.resolve()), "version": "tensor-grep 1.13.12"},
    ]


def test_doctor_fresh_shell_path_uses_windows_registry_separator(monkeypatch) -> None:
    import types

    fake_winreg = types.SimpleNamespace()
    fake_winreg.HKEY_LOCAL_MACHINE = object()
    fake_winreg.HKEY_CURRENT_USER = object()

    class _FakeKey:
        def __init__(self, root: object) -> None:
            self.root = root

        def __enter__(self) -> "_FakeKey":
            return self

        def __exit__(self, *_exc_info: object) -> bool:
            return False

    def _open_key(root: object, _subkey: str) -> _FakeKey:
        return _FakeKey(root)

    def _query_value_ex(key: _FakeKey, _value_name: str) -> tuple[str, int]:
        if key.root is fake_winreg.HKEY_LOCAL_MACHINE:
            return (r"C:\MachineA;C:\MachineB", 0)
        return (r"C:\UserBin", 0)

    fake_winreg.OpenKey = _open_key
    fake_winreg.QueryValueEx = _query_value_ex

    monkeypatch.setitem(sys.modules, "winreg", fake_winreg)
    monkeypatch.setattr(cli_main.sys, "platform", "win32")
    monkeypatch.setattr(cli_main.os, "pathsep", ":")

    assert cli_main._doctor_fresh_shell_path_value() == r"C:\MachineA;C:\MachineB;C:\UserBin"


def test_doctor_json_reports_foreign_first_path_tg_remediation(monkeypatch, tmp_path: Path) -> None:
    foreign_tg = tmp_path / "Python314" / "Scripts" / "tg.exe"
    managed_tg = tmp_path / ".tensor-grep" / "bin" / "tg.exe"
    foreign_tg.parent.mkdir(parents=True)
    managed_tg.parent.mkdir(parents=True)
    foreign_tg.write_text("foreign\n", encoding="utf-8")
    managed_tg.write_text("managed\n", encoding="utf-8")

    monkeypatch.setattr("tensor_grep.cli.main._doctor_installed_version", lambda: "1.9.4")
    monkeypatch.setattr("tensor_grep.cli.main.resolve_native_tg_binary", lambda: managed_tg)
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_rust_binary_version",
        lambda _binary: "tg 1.9.4",
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_rust_core_extension_available",
        lambda: True,
        raising=False,
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_session_daemon_status",
        lambda path: {"running": False},
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_lsp_provider_statuses",
        lambda path: [],
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_path_tg_candidates",
        lambda: [
            {"path": str(foreign_tg), "version": "Together CLI (v2.12.0)"},
            {"path": str(managed_tg), "version": "tg 1.9.4"},
        ],
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_fresh_shell_path_tg_candidates",
        lambda: [
            {"path": str(foreign_tg), "version": "Together CLI (v2.12.0)"},
            {"path": str(managed_tg), "version": "tg 1.9.4"},
        ],
        raising=False,
    )

    result = CliRunner().invoke(app, ["doctor", str(tmp_path), "--json", "--no-lsp"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["path_tg_first_launcher_kind"] == "foreign"
    assert payload["fresh_shell_path_tg_first_launcher_kind"] == "foreign"
    assert payload["path_tg_first_is_foreign"] is True
    assert payload["fresh_shell_path_tg_first_is_foreign"] is True
    assert "Together CLI" in payload["path_tg_foreign_warning"]
    assert str(foreign_tg) in payload["path_tg_foreign_warning"]
    assert str(managed_tg.parent) in payload["path_tg_foreign_remediation"]
    assert "delete" not in payload["path_tg_foreign_remediation"].lower()


def test_doctor_tg_candidate_version_sanitizes_sidecar_python_env(
    monkeypatch, tmp_path: Path
) -> None:

    candidate = tmp_path / "Python314" / "Scripts" / "tg.exe"
    candidate.parent.mkdir(parents=True)
    candidate.write_text("foreign\n", encoding="utf-8")
    seen: dict[str, object] = {}

    def _fake_run(command, **kwargs):  # type: ignore[no-untyped-def]
        seen["command"] = command
        seen["env"] = kwargs.get("env")
        return subprocess.CompletedProcess(command, 0, stdout="2.12.0\n", stderr="")

    monkeypatch.setenv("PYTHONHOME", r"C:\managed-sidecar-python")
    monkeypatch.setenv("PYTHONPATH", r"C:\managed-sidecar-python\Lib")
    monkeypatch.setenv("VIRTUAL_ENV", r"C:\managed-sidecar")
    monkeypatch.setenv("__PYVENV_LAUNCHER__", r"C:\managed-sidecar\python.exe")
    monkeypatch.setattr(cli_main.subprocess, "run", _fake_run)

    assert cli_main._doctor_tg_candidate_version(candidate) == "2.12.0"
    assert seen["command"] == [str(candidate), "--version"]
    env = seen["env"]
    assert isinstance(env, dict)
    assert "PYTHONHOME" not in env
    assert "PYTHONPATH" not in env
    assert "VIRTUAL_ENV" not in env
    assert "__PYVENV_LAUNCHER__" not in env


def test_doctor_launcher_kind_classifies_virtualenv_console_entrypoint(tmp_path: Path) -> None:

    venv_tg = tmp_path / ".venv" / "Scripts" / "tg.exe"
    python_scripts_tg = tmp_path / "Python314" / "Scripts" / "tg.exe"

    assert cli_main._doctor_tg_launcher_kind(str(venv_tg)) == "python-entrypoint"
    assert (
        cli_main._doctor_tg_launcher_kind(str(python_scripts_tg), "tensor-grep 1.10.9")
        == "python-entrypoint"
    )
    assert cli_main._doctor_tg_launcher_kind(str(python_scripts_tg), "tg 1.10.9") == "native-exe"


def test_doctor_launcher_kind_classifies_windows_com_bridge(tmp_path: Path) -> None:

    bridge_tg = tmp_path / "Python314" / "Scripts" / "tg.com"

    assert cli_main._doctor_tg_launcher_kind(str(bridge_tg), "tg 1.9.5") == "native-exe"
    assert cli_main._doctor_tg_launcher_kind(str(bridge_tg), "2.12.0") == "foreign"
    assert cli_main._doctor_tg_launcher_kind(str(bridge_tg), None) == "foreign"


def test_doctor_json_with_unversioned_bridge_emits_foreign_warning(
    monkeypatch, tmp_path: Path
) -> None:
    foreign_tg = tmp_path / "Python314" / "Scripts" / "tg.com"
    managed_tg = tmp_path / ".tensor-grep" / "bin" / "tg.exe"
    foreign_tg.parent.mkdir(parents=True)
    managed_tg.parent.mkdir(parents=True)
    foreign_tg.write_text("bridge\n", encoding="utf-8")
    managed_tg.write_text("managed\n", encoding="utf-8")

    monkeypatch.setattr("tensor_grep.cli.main._doctor_installed_version", lambda: "1.9.4")
    monkeypatch.setattr("tensor_grep.cli.main.resolve_native_tg_binary", lambda: managed_tg)
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_rust_binary_version",
        lambda _binary: "tg 1.9.4",
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_rust_core_extension_available",
        lambda: True,
        raising=False,
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_session_daemon_status",
        lambda path: {"running": False},
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_lsp_provider_statuses",
        lambda path: [],
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_path_tg_candidates",
        lambda: [
            {"path": str(foreign_tg), "version": None},
            {"path": str(managed_tg), "version": "tg 1.9.4"},
        ],
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_fresh_shell_path_tg_candidates",
        lambda: [
            {"path": str(managed_tg), "version": "tg 1.9.4"},
        ],
        raising=False,
    )

    result = CliRunner().invoke(app, ["doctor", str(tmp_path), "--json", "--no-lsp"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["path_tg_first_launcher_kind"] == "foreign"
    assert payload["path_tg_first_version"] is None
    assert payload["path_tg_first_is_foreign"] is True
    assert payload["path_tg_first_version_matches"] is None
    assert "not tensor-grep" in payload["path_tg_foreign_warning"]
    assert "no recognizable --version output" in payload["path_tg_foreign_warning"]
    assert payload["path_tg_foreign_remediation"] is not None
    assert str(managed_tg.parent) in payload["path_tg_foreign_remediation"]


def test_doctor_json_warns_when_current_path_hits_compat_shim_before_fresh_native(
    monkeypatch, tmp_path: Path
) -> None:
    shim_tg = tmp_path / "bin" / "tg.cmd"
    native_tg = tmp_path / ".tensor-grep" / "bin" / "tg.exe"
    shim_tg.parent.mkdir(parents=True)
    native_tg.parent.mkdir(parents=True)
    shim_tg.write_text("@echo off\n", encoding="utf-8")
    native_tg.write_text("native\n", encoding="utf-8")

    monkeypatch.setattr("tensor_grep.cli.main._doctor_installed_version", lambda: "1.8.31")
    monkeypatch.setattr("tensor_grep.cli.main.resolve_native_tg_binary", lambda: native_tg)
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_rust_binary_version",
        lambda _binary: "tensor-grep 1.8.31",
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_rust_core_extension_available",
        lambda: True,
        raising=False,
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_session_daemon_status",
        lambda path: {"running": False},
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_lsp_provider_statuses",
        lambda path: [],
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_path_tg_candidates",
        lambda: [
            {"path": str(shim_tg), "version": "tensor-grep 1.8.31"},
            {"path": str(native_tg), "version": "tensor-grep 1.8.31"},
        ],
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_fresh_shell_path_tg_candidates",
        lambda: [{"path": str(native_tg), "version": "tensor-grep 1.8.31"}],
        raising=False,
    )

    result = CliRunner().invoke(app, ["doctor", str(tmp_path), "--json", "--no-lsp"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["path_tg_first_launcher_kind"] == "cmd-shim"
    assert payload["fresh_shell_path_tg_first_launcher_kind"] == "managed-native"
    assert payload["fresh_shell_path_tg_first_version_matches"] is True
    assert (
        "current process PATH resolves a compatibility shim" in payload["path_tg_launcher_warning"]
    )
    assert "restart the shell" in payload["path_tg_launcher_warning"]


def test_doctor_json_reports_mcp_stdio_launcher_warning_for_powershell_shim(
    monkeypatch, tmp_path: Path
) -> None:
    shim_tg = tmp_path / "bin" / "tg.ps1"
    native_tg = tmp_path / ".tensor-grep" / "bin" / "tg.exe"
    shim_tg.parent.mkdir(parents=True)
    native_tg.parent.mkdir(parents=True)
    shim_tg.write_text("& $PSScriptRoot/tg.exe @args\n", encoding="utf-8")
    native_tg.write_text("native\n", encoding="utf-8")

    monkeypatch.setattr("tensor_grep.cli.main._doctor_installed_version", lambda: "1.12.52")
    monkeypatch.setattr("tensor_grep.cli.main.resolve_native_tg_binary", lambda: native_tg)
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_rust_binary_version",
        lambda _binary: "tg 1.12.52",
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_rust_core_extension_available",
        lambda: True,
        raising=False,
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_session_daemon_status",
        lambda path: {"running": False},
    )
    monkeypatch.setattr("tensor_grep.cli.main._doctor_lsp_provider_statuses", lambda path: [])
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_path_tg_candidates",
        lambda: [
            {"path": str(shim_tg), "version": "tensor-grep 1.12.52"},
            {"path": str(native_tg), "version": "tg 1.12.52"},
        ],
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_fresh_shell_path_tg_candidates",
        lambda: [{"path": str(native_tg), "version": "tg 1.12.52"}],
        raising=False,
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_python_subprocess_path_tg_candidate",
        lambda path_value=None: {"path": str(shim_tg), "version": "tensor-grep 1.12.52"},
        raising=False,
    )

    result = CliRunner().invoke(app, ["doctor", str(tmp_path), "--json", "--no-lsp"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["path_tg_first_launcher_kind"] == "powershell-shim"
    assert payload["python_subprocess_path_tg_first_launcher_kind"] == "powershell-shim"
    warning = payload["mcp_stdio_launcher_warning"]
    assert "MCP stdio" in warning
    assert "Start-Process" in warning
    assert "managed native tg.exe directly" in warning
    assert "not `tg.ps1`" in warning
    assert str(native_tg) in warning
    assert "pwsh -NoProfile -File" in warning
    assert str(shim_tg) in warning


def test_doctor_json_reports_mcp_stdio_launcher_warning_from_candidate_native(
    monkeypatch, tmp_path: Path
) -> None:
    shim_tg = tmp_path / "bin" / "tg.ps1"
    native_tg = tmp_path / "bin" / "tg.exe"
    shim_tg.parent.mkdir(parents=True)
    shim_tg.write_text("& $PSScriptRoot/tg.exe @args\n", encoding="utf-8")
    native_tg.write_text("native\n", encoding="utf-8")

    monkeypatch.setattr("tensor_grep.cli.main._doctor_installed_version", lambda: "1.13.1")
    monkeypatch.setattr("tensor_grep.cli.main.resolve_native_tg_binary", lambda: None)
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_rust_core_extension_available",
        lambda: True,
        raising=False,
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_session_daemon_status",
        lambda path: {"running": False},
    )
    monkeypatch.setattr("tensor_grep.cli.main._doctor_lsp_provider_statuses", lambda path: [])
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_path_tg_candidates",
        lambda: [
            {"path": str(shim_tg), "version": "tensor-grep 1.13.1"},
            {"path": str(native_tg), "version": "tg 1.13.1"},
        ],
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_fresh_shell_path_tg_candidates",
        lambda: [
            {"path": str(shim_tg), "version": "tensor-grep 1.13.1"},
            {"path": str(native_tg), "version": "tg 1.13.1"},
        ],
        raising=False,
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_python_subprocess_path_tg_candidate",
        lambda path_value=None: {"path": str(shim_tg), "version": "tensor-grep 1.13.1"},
        raising=False,
    )

    result = CliRunner().invoke(app, ["doctor", str(tmp_path), "--json", "--no-lsp"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    warning = payload["mcp_stdio_launcher_warning"]
    assert "Start-Process" in warning
    assert "not `tg.ps1`" in warning
    assert str(native_tg) in warning
    assert str(shim_tg) in warning


def test_doctor_mcp_stdio_warning_flags_ps1_path_candidate_without_version(
    tmp_path: Path,
) -> None:
    native_tg = tmp_path / ".tensor-grep" / "bin" / "tg.exe"
    shim_tg = tmp_path / "bin" / "tg.ps1"
    native_tg.parent.mkdir(parents=True)
    shim_tg.parent.mkdir(parents=True)
    native_tg.write_text("native\n", encoding="utf-8")
    shim_tg.write_text("& $PSScriptRoot/tg.exe @args\n", encoding="utf-8")

    warning = cli_main._doctor_mcp_stdio_launcher_warning(
        native_tg_binary=native_tg,
        launchers=[("PATH", "managed-native", str(native_tg))],
        path_tg_candidates=[{"path": str(shim_tg), "version": None}],
    )

    assert warning is not None
    assert "Start-Process" in warning
    assert "not `tg.ps1`" in warning
    assert str(native_tg) in warning
    assert str(shim_tg) in warning


def test_doctor_text_reports_mcp_stdio_launcher_warning(monkeypatch, tmp_path: Path) -> None:
    shim_tg = tmp_path / "bin" / "tg.ps1"
    native_tg = tmp_path / ".tensor-grep" / "bin" / "tg.exe"
    shim_tg.parent.mkdir(parents=True)
    native_tg.parent.mkdir(parents=True)
    shim_tg.write_text("& $PSScriptRoot/tg.exe @args\n", encoding="utf-8")
    native_tg.write_text("native\n", encoding="utf-8")

    monkeypatch.setattr("tensor_grep.cli.main._doctor_installed_version", lambda: "1.12.52")
    monkeypatch.setattr("tensor_grep.cli.main.resolve_native_tg_binary", lambda: native_tg)
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_rust_binary_version",
        lambda _binary: "tg 1.12.52",
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_rust_core_extension_available",
        lambda: True,
        raising=False,
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_session_daemon_status",
        lambda path: {"running": False},
    )
    monkeypatch.setattr("tensor_grep.cli.main._doctor_lsp_provider_statuses", lambda path: [])
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_path_tg_candidates",
        lambda: [{"path": str(shim_tg), "version": "tensor-grep 1.12.52"}],
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_fresh_shell_path_tg_candidates",
        lambda: [{"path": str(native_tg), "version": "tg 1.12.52"}],
        raising=False,
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_python_subprocess_path_tg_candidate",
        lambda path_value=None: None,
        raising=False,
    )

    result = CliRunner().invoke(app, ["doctor", str(tmp_path), "--no-lsp"])

    assert result.exit_code == 0
    assert "mcp_stdio_launcher_warning:" in result.stdout
    assert "Start-Process" in result.stdout
    assert "managed native tg.exe directly" in result.stdout
    assert "not `tg.ps1`" in result.stdout
    assert "pwsh -NoProfile -File" in result.stdout


def test_doctor_json_reports_python_subprocess_foreign_tg_exe(monkeypatch, tmp_path: Path) -> None:
    foreign_tg = tmp_path / "Python314" / "Scripts" / "tg.exe"
    managed_tg = tmp_path / ".tensor-grep" / "bin" / "tg.exe"
    foreign_tg.parent.mkdir(parents=True)
    managed_tg.parent.mkdir(parents=True)
    foreign_tg.write_text("foreign\n", encoding="utf-8")
    managed_tg.write_text("managed\n", encoding="utf-8")

    monkeypatch.setattr("tensor_grep.cli.main._doctor_installed_version", lambda: "1.10.5")
    monkeypatch.setattr("tensor_grep.cli.main.resolve_native_tg_binary", lambda: managed_tg)
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_rust_binary_version",
        lambda _binary: "tg 1.10.5",
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_rust_core_extension_available",
        lambda: True,
        raising=False,
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_session_daemon_status",
        lambda path: {"running": False},
    )
    monkeypatch.setattr("tensor_grep.cli.main._doctor_lsp_provider_statuses", lambda path: [])
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_path_tg_candidates",
        lambda path_value=None: [
            {"path": str(managed_tg), "version": "tg 1.10.5"},
            {"path": str(foreign_tg), "version": "Together CLI (v2.12.0)"},
        ],
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_fresh_shell_path_tg_candidates",
        lambda: [{"path": str(managed_tg), "version": "tg 1.10.5"}],
        raising=False,
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_python_subprocess_path_tg_candidate",
        lambda path_value=None: {
            "path": str(foreign_tg),
            "version": "Together CLI (v2.12.0)",
        },
        raising=False,
    )

    result = CliRunner().invoke(app, ["doctor", str(tmp_path), "--json", "--no-lsp"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["python_subprocess_path_tg_first_launcher_kind"] == "foreign"
    assert payload["python_subprocess_path_tg_first_is_foreign"] is True
    assert payload["python_subprocess_path_tg_first_version_matches"] is False
    assert "Python subprocess" in payload["python_subprocess_path_tg_foreign_warning"]
    assert str(foreign_tg) in payload["python_subprocess_path_tg_foreign_warning"]
    assert str(managed_tg.parent) in payload["python_subprocess_path_tg_foreign_remediation"]
    assert "Machine PATH" in payload["python_subprocess_path_tg_foreign_remediation"]
    assert (
        "repair-launcher --allow-foreign-rename"
        in payload["python_subprocess_path_tg_foreign_remediation"]
    )
    assert "delete" not in payload["python_subprocess_path_tg_foreign_remediation"].lower()


def test_lsp_setup_runs_managed_provider_installer(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    seen: dict[str, object] = {}

    def _fake_install(
        *,
        python_executable: str,
        managed_root: Path | None,
        include_toolchain_providers: bool,
    ) -> dict[str, object]:
        seen["python_executable"] = python_executable
        seen["managed_root"] = managed_root
        seen["include_toolchain_providers"] = include_toolchain_providers
        return {
            "managed_provider_root": str(tmp_path / "providers"),
            "include_toolchain_providers": include_toolchain_providers,
            "node": {"installed": True},
            "providers": {
                "python": {
                    "command": [str(tmp_path / "providers" / "pyright-langserver"), "--stdio"],
                    "available": True,
                    "command_source": "managed",
                },
                "php": {
                    "command": [str(tmp_path / "providers" / "intelephense"), "--stdio"],
                    "available": True,
                    "command_source": "managed",
                },
                "go": {
                    "command": [str(tmp_path / "providers" / "gopls")],
                    "available": True,
                    "command_source": "managed",
                },
            },
        }

    monkeypatch.setattr("tensor_grep.cli.main.install_managed_lsp_providers", _fake_install)

    runner = CliRunner()
    result = runner.invoke(app, ["lsp-setup", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["managed_provider_root"] == str(tmp_path / "providers")
    assert payload["providers"]["python"]["command"][0].endswith("pyright-langserver")
    assert payload["providers"]["php"]["command"][0].endswith("intelephense")
    assert payload["providers"]["go"]["command"][0].endswith("gopls")
    assert seen["python_executable"] == sys.executable
    assert seen["managed_root"] is None
    assert seen["include_toolchain_providers"] is False


def test_lsp_setup_can_enable_toolchain_provider_install(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    seen: dict[str, object] = {}

    def _fake_install(
        *,
        python_executable: str,
        managed_root: Path | None,
        include_toolchain_providers: bool,
    ) -> dict[str, object]:
        seen["include_toolchain_providers"] = include_toolchain_providers
        return {
            "managed_provider_root": str(tmp_path / "providers"),
            "include_toolchain_providers": include_toolchain_providers,
            "node": {"installed": True},
            "providers": {},
        }

    monkeypatch.setattr("tensor_grep.cli.main.install_managed_lsp_providers", _fake_install)

    runner = CliRunner()
    result = runner.invoke(app, ["lsp-setup", "--json", "--include-toolchain-providers"])

    assert result.exit_code == 0
    assert seen["include_toolchain_providers"] is True


def test_lsp_setup_json_exits_nonzero_when_install_errors(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def _fake_install(
        *,
        python_executable: str,
        managed_root: Path | None,
        include_toolchain_providers: bool,
    ) -> dict[str, object]:
        return {
            "managed_provider_root": str(tmp_path / "providers"),
            "include_toolchain_providers": include_toolchain_providers,
            "node": {"installed": False},
            "providers": {
                "python": {
                    "command": None,
                    "available": False,
                    "command_source": "missing",
                    "install_error": "network unavailable",
                }
            },
            "install_errors": {"node": "network unavailable"},
        }

    monkeypatch.setattr("tensor_grep.cli.main.install_managed_lsp_providers", _fake_install)

    runner = CliRunner()
    result = runner.invoke(app, ["lsp-setup", "--json"])

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["install_errors"]["node"] == "network unavailable"


def test_doctor_json_passes_non_default_config_to_payload_builder(
    monkeypatch, tmp_path: Path
) -> None:
    seen: dict[str, object] = {}

    def _fake_build(path: str, *, config: str | None, with_lsp: bool) -> dict[str, object]:
        seen.update({"path": path, "config": config, "with_lsp": with_lsp})
        return {"ok": True}

    monkeypatch.setattr("tensor_grep.cli.main._build_doctor_payload", _fake_build)

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["doctor", str(tmp_path), "--config", "configs/custom.yml", "--json", "--no-lsp"],
    )

    assert result.exit_code == 0
    assert json.loads(result.stdout) == {"ok": True}
    assert seen == {
        "path": str(tmp_path),
        "config": "configs/custom.yml",
        "with_lsp": False,
    }


def test_doctor_text_reports_disabled_lsp_and_stopped_daemon(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("tensor_grep.cli.main._doctor_installed_version", lambda: "1.2.3")
    monkeypatch.setattr("tensor_grep.cli.main.resolve_native_tg_binary", lambda: None)
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_session_daemon_status",
        lambda path: {"running": False},
    )

    runner = CliRunner()
    result = runner.invoke(app, ["doctor", str(tmp_path), "--no-lsp"])

    assert result.exit_code == 0
    assert "tensor-grep doctor" in result.stdout
    assert "version: 1.2.3" in result.stdout
    assert "native_tg_binary: missing" in result.stdout
    assert "session_daemon: stopped" in result.stdout
    assert "lsp_providers: disabled" in result.stdout
    assert "shell_escaping_guidance:" in result.stdout
    assert "PowerShell double quotes expand $NAME" in result.stdout
    assert "cmd.exe metacharacters" in result.stdout


def test_doctor_text_reports_lsp_health_and_proof_fields(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("tensor_grep.cli.main._doctor_installed_version", lambda: "1.2.3")
    monkeypatch.setattr("tensor_grep.cli.main.resolve_native_tg_binary", lambda: None)
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_session_daemon_status",
        lambda path: {"running": False},
    )
    monkeypatch.setenv("TG_DOCTOR_LSP_PROBE_TIMEOUT_SECONDS", "4.5")
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_lsp_provider_statuses",
        lambda path: [
            {
                "language": "python",
                "available": True,
                "running": False,
                "command": ["pyright-langserver", "--stdio"],
                "command_source": "managed",
                "managed_provider_root": str(tmp_path / "providers"),
                "last_error": None,
                "health_status": "available_unverified",
                "health_check": "not_run",
                "lsp_proof": False,
                "not_lsp_proof_reason": "Provider binary is available but health was not verified.",
            }
        ],
    )

    result = CliRunner().invoke(app, ["doctor", str(tmp_path)])

    assert result.exit_code == 0
    assert "lsp_probe_timeout_seconds: 4.5" in result.stdout
    assert "health=available_unverified" in result.stdout
    assert "health_check=not_run" in result.stdout
    assert "lsp_proof=False" in result.stdout
    assert "not_lsp_proof_reason=" in result.stdout


def test_doctor_json_explains_rust_core_extension_when_standalone_binary_missing(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("tensor_grep.cli.main._doctor_installed_version", lambda: "1.8.2")
    monkeypatch.setattr("tensor_grep.cli.main.resolve_native_tg_binary", lambda: None)
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_rust_core_extension_available",
        lambda: True,
        raising=False,
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_session_daemon_status",
        lambda path: {"running": False},
    )
    monkeypatch.setattr("tensor_grep.cli.main._doctor_lsp_provider_statuses", lambda path: [])

    runner = CliRunner()
    result = runner.invoke(app, ["doctor", str(tmp_path), "--json", "--no-lsp"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["native_tg_binary"] is None
    assert payload["native_tg_binary_exists"] is False
    assert payload["rust_core_extension_available"] is True
    assert payload["search_acceleration_backend"] == "rust-core-extension"


def test_cli_should_parse_gpu_device_ids_into_search_config(monkeypatch):
    global _FAKE_WALK, _FAKE_BACKEND, _LAST_PIPELINE_CONFIG
    _FAKE_WALK = {".": ["a.log"]}
    _FAKE_BACKEND = _FakeBackend(
        results_by_file={
            "a.log": SearchResult(
                matches=[MatchLine(line_number=1, text="ERROR", file="a.log")],
                total_files=1,
                total_matches=1,
            )
        }
    )
    _LAST_PIPELINE_CONFIG = None
    _patch_cli_dependencies(monkeypatch)

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["search", "ERROR", ".", "--ltl", "--gpu-device-ids", "3,7,7"],
    )

    assert result.exit_code == 0
    assert _LAST_PIPELINE_CONFIG is not None
    assert _LAST_PIPELINE_CONFIG.gpu_device_ids == [3, 7]


def test_cli_should_fail_fast_on_invalid_gpu_device_ids(monkeypatch):
    global _FAKE_WALK, _FAKE_BACKEND
    _FAKE_WALK = {".": ["a.log"]}
    _FAKE_BACKEND = _FakeBackend(results_by_file={})
    _patch_cli_dependencies(monkeypatch)

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["search", "ERROR", ".", "--gpu-device-ids", "0,foo"],
    )

    assert result.exit_code == 2
    assert "Invalid GPU device id 'foo'" in result.output


def test_cli_should_delegate_force_cpu_search_to_native_binary(monkeypatch):
    seen: dict[str, object] = {}

    monkeypatch.setattr("tensor_grep.cli.main.resolve_native_tg_binary", lambda: Path("tg.exe"))
    monkeypatch.setattr(
        "tensor_grep.cli.main._can_delegate_to_native_tg_search",
        lambda *args, **kwargs: True,
    )

    def _fake_run(cmd, check=False):
        seen["cmd"] = list(cmd)
        seen["check"] = check
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("tensor_grep.cli.main.subprocess.run", _fake_run)

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["search", "ERROR", ".", "--cpu", "-F", "-c", "-g", "*.log", "--no-ignore"],
    )

    assert result.exit_code == 0
    assert seen["cmd"] == [
        "tg.exe",
        "search",
        "--cpu",
        "-F",
        "-c",
        "-g",
        "*.log",
        "--no-ignore",
        "ERROR",
        ".",
    ]
    assert seen["check"] is False


def test_cli_should_force_cpu_pipeline_when_env_override_is_enabled(monkeypatch):
    global _FAKE_WALK, _FAKE_BACKEND, _LAST_PIPELINE_CONFIG
    _FAKE_WALK = {".": ["a.log"]}
    _FAKE_BACKEND = _FakeBackend(
        results_by_file={
            "a.log": SearchResult(
                matches=[MatchLine(line_number=1, text="ERROR", file="a.log")],
                total_files=1,
                total_matches=1,
            )
        }
    )
    _LAST_PIPELINE_CONFIG = None
    _patch_cli_dependencies(monkeypatch)
    monkeypatch.setenv("TG_FORCE_CPU", "1")
    monkeypatch.setattr("tensor_grep.cli.main.resolve_native_tg_binary", lambda: None)

    runner = CliRunner()
    result = runner.invoke(app, ["search", "ERROR", "."])

    assert result.exit_code == 0
    assert _LAST_PIPELINE_CONFIG is not None
    assert _LAST_PIPELINE_CONFIG.force_cpu is True


def test_cli_search_no_line_number_overrides_line_number(monkeypatch):
    global _FAKE_WALK, _FAKE_BACKEND, _LAST_PIPELINE_CONFIG
    _FAKE_WALK = {".": ["a.log"]}
    _FAKE_BACKEND = _FakeBackend(
        results_by_file={
            "a.log": SearchResult(
                matches=[MatchLine(line_number=1, text="ERROR", file="a.log")],
                total_files=1,
                total_matches=1,
            )
        }
    )
    _LAST_PIPELINE_CONFIG = None
    _patch_cli_dependencies(monkeypatch)
    monkeypatch.setattr("tensor_grep.cli.main.resolve_native_tg_binary", lambda: None)

    result = CliRunner().invoke(app, ["search", "-n", "-N", "--cpu", "ERROR", "."])

    assert result.exit_code == 0
    assert _LAST_PIPELINE_CONFIG is not None
    assert _LAST_PIPELINE_CONFIG.line_number is False


def test_cli_search_without_path_defaults_to_current_directory(monkeypatch):
    global _FAKE_WALK, _FAKE_BACKEND, _LAST_PIPELINE_CONFIG
    _FAKE_WALK = {".": ["a.log"]}
    _FAKE_BACKEND = _FakeBackend(
        results_by_file={
            "a.log": SearchResult(
                matches=[MatchLine(line_number=1, text="safeParseJSON", file="a.log")],
                matched_file_paths=["a.log"],
                match_counts_by_file={"a.log": 1},
                total_files=1,
                total_matches=1,
            )
        }
    )
    _LAST_PIPELINE_CONFIG = None
    _patch_cli_dependencies(monkeypatch)
    monkeypatch.setattr("tensor_grep.cli.main.resolve_native_tg_binary", lambda: None)

    runner = CliRunner()
    result = runner.invoke(app, ["search", "safeParseJSON", "--cpu"])

    assert result.exit_code == 0
    assert _LAST_PIPELINE_CONFIG is not None
    assert "safeParseJSON" in result.stdout


def test_cli_json_no_match_emits_valid_empty_payload(monkeypatch):
    global _FAKE_WALK, _FAKE_BACKEND
    _FAKE_WALK = {".": ["a.log"]}
    _FAKE_BACKEND = _FakeBackend(
        results_by_file={
            "a.log": SearchResult(
                matches=[],
                matched_file_paths=[],
                match_counts_by_file={},
                total_files=0,
                total_matches=0,
            )
        }
    )
    _patch_cli_dependencies(monkeypatch)
    monkeypatch.setattr("tensor_grep.cli.main.resolve_native_tg_binary", lambda: None)

    runner = CliRunner()
    result = runner.invoke(app, ["search", "__missing__", ".", "--json"])

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["total_matches"] == 0
    assert payload["total_files"] == 0
    assert payload["matches"] == []


def test_cli_invalid_regex_reports_diagnostic_and_error_exit(monkeypatch):
    from tensor_grep.backends.cpu_backend import InvalidRegexError

    global _FAKE_WALK, _FAKE_BACKEND
    _FAKE_WALK = {".": ["a.log"]}
    _FAKE_BACKEND = _FakeBackend(results_by_file={})
    _patch_cli_dependencies(monkeypatch)
    monkeypatch.setattr("tensor_grep.cli.main.resolve_native_tg_binary", lambda: None)

    class _InvalidRegexBackend:
        def search(self, file_path, pattern, config=None):
            raise InvalidRegexError("invalid regex pattern: missing ), unterminated subpattern")

    class _InvalidRegexPipeline(_FakePipeline):
        def __init__(self, force_cpu=False, config=None):
            super().__init__(force_cpu=force_cpu, config=config)
            self.backend = _InvalidRegexBackend()

    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _InvalidRegexPipeline)

    runner = CliRunner()
    result = runner.invoke(app, ["search", "(", ".", "--cpu"])

    assert result.exit_code == 2
    assert "invalid regex" in result.stderr.lower()
    assert "-P (PCRE2)" in result.stderr
    assert "--fixed-strings (-F)" in result.stderr


def test_cli_invalid_regex_is_rejected_before_native_delegation(monkeypatch):
    seen: dict[str, object] = {}

    monkeypatch.setattr("tensor_grep.cli.main.resolve_native_tg_binary", lambda: Path("tg.exe"))
    monkeypatch.setattr(
        "tensor_grep.cli.main._can_delegate_to_native_tg_search",
        lambda *args, **kwargs: True,
    )

    def _fake_run(cmd, check=False):
        seen["cmd"] = list(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("tensor_grep.cli.main.subprocess.run", _fake_run)

    result = CliRunner().invoke(app, ["search", "(", "."])

    assert result.exit_code == 2
    assert "invalid regex" in result.stderr.lower()
    assert "-P (PCRE2)" in result.stderr
    assert "--fixed-strings (-F)" in result.stderr
    assert "cmd" not in seen


def test_cli_invalid_regex_reports_json_error_before_native_delegation(monkeypatch):
    seen: dict[str, object] = {}

    monkeypatch.setattr("tensor_grep.cli.main.resolve_native_tg_binary", lambda: Path("tg.exe"))
    monkeypatch.setattr(
        "tensor_grep.cli.main._can_delegate_to_native_tg_search",
        lambda *args, **kwargs: True,
    )

    def _fake_run(cmd, check=False):
        seen["cmd"] = list(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("tensor_grep.cli.main.subprocess.run", _fake_run)

    result = CliRunner().invoke(app, ["search", "(", ".", "--json"])

    assert result.exit_code == 2
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["error"] == "invalid_regex"
    assert "invalid regex" in payload["detail"].lower()
    assert "cmd" not in seen


def test_cli_invalid_regex_is_rejected_before_scanning(monkeypatch):
    global _FAKE_WALK, _FAKE_BACKEND
    _FAKE_WALK = {".": ["a.log"]}
    _FAKE_BACKEND = _FakeBackend(results_by_file={})
    _patch_cli_dependencies(monkeypatch)
    monkeypatch.setattr("tensor_grep.cli.main.resolve_native_tg_binary", lambda: None)

    class _UnexpectedScanner:
        def __init__(self, config=None):
            raise AssertionError("invalid regex should fail before walking broad roots")

    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _UnexpectedScanner)

    result = CliRunner().invoke(app, ["search", "(", ".", "--cpu"])

    assert result.exit_code == 2
    assert "invalid regex" in result.stderr.lower()


def test_cli_later_invalid_regexp_is_rejected_before_native_delegation(monkeypatch):
    seen: dict[str, object] = {}

    monkeypatch.setattr("tensor_grep.cli.main.resolve_native_tg_binary", lambda: Path("tg.exe"))
    monkeypatch.setattr(
        "tensor_grep.cli.main._can_delegate_to_native_tg_search",
        lambda *args, **kwargs: True,
    )

    def _fake_run(cmd, check=False):
        seen["cmd"] = list(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("tensor_grep.cli.main.subprocess.run", _fake_run)

    result = CliRunner().invoke(app, ["search", "-e", "safe", "-e", "(", "."])

    assert result.exit_code == 2
    assert "invalid regex" in result.stderr.lower()
    assert "cmd" not in seen


def test_cli_broad_claude_json_uses_python_guardrails_before_native(monkeypatch):
    global _FAKE_WALK, _FAKE_BACKEND
    _FAKE_WALK = {".claude": [".claude/lib/utils.cjs"]}
    _FAKE_BACKEND = _FakeBackend(
        results_by_file={
            ".claude/lib/utils.cjs": SearchResult(
                matches=[
                    MatchLine(
                        line_number=1,
                        text="safeParseJSON(value)",
                        file=".claude/lib/utils.cjs",
                    )
                ],
                matched_file_paths=[".claude/lib/utils.cjs"],
                match_counts_by_file={".claude/lib/utils.cjs": 1},
                total_files=1,
                total_matches=1,
            )
        }
    )
    _patch_cli_dependencies(monkeypatch)
    monkeypatch.setattr("tensor_grep.cli.main.resolve_native_tg_binary", lambda: Path("tg.exe"))
    monkeypatch.setattr(
        "tensor_grep.cli.main._can_delegate_to_native_tg_search",
        lambda *args, **kwargs: True,
    )

    def _fake_run(cmd, check=False):
        raise AssertionError("broad .claude JSON search needs Python scanner guardrails")

    monkeypatch.setattr("tensor_grep.cli.main.subprocess.run", _fake_run)

    result = CliRunner().invoke(
        app,
        ["search", "safeParseJSON", ".claude", "--json"],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["total_matches"] == 1
    assert payload["matches"][0]["file"] == ".claude/lib/utils.cjs"


def test_cli_broad_claude_ripgrep_backend_adds_guard_excludes(monkeypatch):
    seen: dict[str, object] = {}
    global _FAKE_WALK
    _FAKE_WALK = {".claude": [".claude/lib/utils.cjs"]}

    class RipgrepBackend:
        def is_available(self):
            return True

        def search_passthrough(self, paths, pattern, config=None):
            raise AssertionError("broad .claude should not use rg passthrough")

        def search(self, paths, pattern, config=None):
            seen["paths"] = list(paths)
            seen["glob"] = list(config.glob or [])
            return SearchResult(
                matches=[
                    MatchLine(
                        line_number=1,
                        text="safeParseJSON(value)",
                        file=".claude/lib/utils.cjs",
                    )
                ],
                matched_file_paths=[".claude/lib/utils.cjs"],
                match_counts_by_file={".claude/lib/utils.cjs": 1},
                total_files=1,
                total_matches=1,
                routing_backend="RipgrepBackend",
                routing_reason="rg_json",
            )

    class _RipgrepPipeline:
        def __init__(self, force_cpu=False, config=None):
            self.backend = RipgrepBackend()
            self.selected_backend_name = "RipgrepBackend"
            self.selected_backend_reason = "rg_json"
            self.selected_gpu_device_ids = []
            self.selected_gpu_chunk_plan_mb = []

        def get_backend(self):
            return self.backend

    monkeypatch.setattr("tensor_grep.backends.ripgrep_backend.RipgrepBackend", RipgrepBackend)
    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _RipgrepPipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeScanner)
    monkeypatch.setattr("tensor_grep.cli.main.resolve_native_tg_binary", lambda: None)

    result = CliRunner().invoke(
        app,
        ["search", "safeParseJSON", ".claude", "--json"],
    )

    assert result.exit_code == 0
    assert seen["paths"] == [".claude"]
    assert "!context/**" in seen["glob"]
    assert "!**/context/**" in seen["glob"]


def test_cli_wrapped_rg_regex_parse_error_reports_diagnostic(monkeypatch):
    global _FAKE_WALK, _FAKE_BACKEND
    _FAKE_WALK = {".": ["a.log"]}
    _FAKE_BACKEND = _FakeBackend(results_by_file={})
    _patch_cli_dependencies(monkeypatch)
    monkeypatch.setattr("tensor_grep.cli.main.resolve_native_tg_binary", lambda: None)

    class _WrappedRgInvalidRegexBackend:
        def search(self, file_path, pattern, config=None):
            raise RuntimeError("rg failed with exit code 2: error parsing regex: missing )")

    class _WrappedRgInvalidRegexPipeline(_FakePipeline):
        def __init__(self, force_cpu=False, config=None):
            super().__init__(force_cpu=force_cpu, config=config)
            self.backend = _WrappedRgInvalidRegexBackend()

    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _WrappedRgInvalidRegexPipeline)

    runner = CliRunner()
    result = runner.invoke(app, ["search", "(", ".", "--cpu"])

    assert result.exit_code == 2
    assert "error parsing regex" in result.stderr.lower()
    assert "-P (PCRE2)" in result.stderr
    assert "--fixed-strings (-F)" in result.stderr


def test_cli_should_delegate_ndjson_search_to_native_binary_and_preserve_exit_code(monkeypatch):
    seen: dict[str, object] = {}

    monkeypatch.setattr("tensor_grep.cli.main.resolve_native_tg_binary", lambda: Path("tg.exe"))
    monkeypatch.setattr(
        "tensor_grep.cli.main._can_delegate_to_native_tg_search",
        lambda *args, **kwargs: True,
    )

    def _fake_run(cmd, check=False):
        seen["cmd"] = list(cmd)
        return subprocess.CompletedProcess(cmd, 2, stdout="", stderr="")

    monkeypatch.setattr("tensor_grep.cli.main.subprocess.run", _fake_run)

    runner = CliRunner()
    result = runner.invoke(app, ["search", "ERROR", ".", "--ndjson"])

    assert result.exit_code == 2
    assert seen["cmd"] == ["tg.exe", "search", "--ndjson", "ERROR", "."]


def test_cli_should_emit_ndjson_without_native_binary(monkeypatch):
    global _FAKE_WALK, _FAKE_BACKEND
    _FAKE_WALK = {".": ["a.log"]}
    _FAKE_BACKEND = _FakeBackend(
        results_by_file={
            "a.log": SearchResult(
                matches=[MatchLine(line_number=1, text="ERROR visible", file="a.log")],
                matched_file_paths=["a.log"],
                match_counts_by_file={"a.log": 1},
                total_files=1,
                total_matches=1,
            )
        }
    )
    _patch_cli_dependencies(monkeypatch)
    monkeypatch.setattr("tensor_grep.cli.main.resolve_native_tg_binary", lambda: None)

    runner = CliRunner()
    result = runner.invoke(app, ["search", "ERROR", ".", "--ndjson"])

    assert result.exit_code == 0
    rows = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    assert len(rows) == 1
    assert rows[0]["version"] == 1
    assert rows[0]["file"] == "a.log"
    assert rows[0]["line_number"] == 1
    assert rows[0]["text"] == "ERROR visible"
    assert rows[0]["routing_backend"] == "FakeBackend"
    assert rows[0]["routing_reason"] == "unit_test_fake_pipeline"


def test_cli_should_treat_regexp_as_pattern_when_glob_precedes_path(monkeypatch):
    global _FAKE_WALK, _FAKE_BACKEND, _LAST_PIPELINE_CONFIG
    _FAKE_WALK = {".": ["a.log"]}
    _FAKE_BACKEND = _FakeBackend(
        results_by_file={
            "a.log": SearchResult(
                matches=[MatchLine(line_number=1, text="runCursorWorker()", file="a.log")],
                matched_file_paths=["a.log"],
                match_counts_by_file={"a.log": 1},
                total_files=1,
                total_matches=1,
            )
        }
    )
    _LAST_PIPELINE_CONFIG = None
    _patch_cli_dependencies(monkeypatch)
    monkeypatch.setattr("tensor_grep.cli.main.resolve_native_tg_binary", lambda: None)

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["search", "--json", "--glob", "scripts/agents/**", "-e", "runCursorWorker", "."],
    )

    assert result.exit_code == 0
    assert _LAST_PIPELINE_CONFIG is not None
    assert _LAST_PIPELINE_CONFIG.regexp == ["runCursorWorker"]
    assert _LAST_PIPELINE_CONFIG.glob == ["scripts/agents/**"]
    assert "runCursorWorker()" in result.stdout


def test_cli_should_delegate_json_search_to_native_binary(monkeypatch):
    seen: dict[str, object] = {}
    _patch_cli_dependencies(monkeypatch)
    monkeypatch.setattr("tensor_grep.cli.main.resolve_native_tg_binary", lambda: Path("tg.exe"))

    def _fake_run(cmd, check=False):
        seen["cmd"] = list(cmd)
        seen["check"] = check
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("tensor_grep.cli.main.subprocess.run", _fake_run)

    runner = CliRunner()
    result = runner.invoke(app, ["search", "ERROR", ".", "--json"])

    assert result.exit_code == 0
    assert seen["cmd"] == ["tg.exe", "search", "--json", "ERROR", "."]
    assert seen["check"] is False


def test_cli_should_delegate_native_rg_output_flags(monkeypatch):
    seen: dict[str, object] = {}
    _patch_cli_dependencies(monkeypatch)
    monkeypatch.setattr("tensor_grep.cli.main.resolve_native_tg_binary", lambda: Path("tg.exe"))

    def _fake_run(cmd, check=False):
        seen["cmd"] = list(cmd)
        seen["check"] = check
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("tensor_grep.cli.main.subprocess.run", _fake_run)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "search",
            "ERROR",
            ".",
            "--json",
            "--column",
            "--vimgrep",
            "--path-separator",
            "/",
        ],
    )

    assert result.exit_code == 0
    assert seen["cmd"] == [
        "tg.exe",
        "search",
        "--column",
        "--path-separator",
        "/",
        "--vimgrep",
        "--json",
        "ERROR",
        ".",
    ]
    assert seen["check"] is False


def test_search_help_should_describe_json_as_aggregate_json() -> None:
    result = CliRunner().invoke(app, ["search", "--help"])

    assert result.exit_code == 0
    help_text = _strip_ansi(result.stdout)
    normalized_help = re.sub(r"\s+", " ", re.sub(r"[│┌┐└┘─]+", " ", help_text))
    assert "--json" in help_text
    assert "tensor-grep aggregate JSON object, not rg JSON Lines." in normalized_help
    assert "streaming" in help_text
    assert "Print results in JSON Lines format." not in help_text


def test_python_search_accepts_advertised_rg_compatibility_flags(monkeypatch, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "app.log").write_text("ERROR failed\n", encoding="utf-8")
    seen: dict[str, object] = {}

    def _fake_passthrough(self, paths, pattern, config=None):
        seen["paths"] = list(paths)
        seen["pattern"] = pattern
        seen["config"] = config
        return 0

    monkeypatch.setattr(
        "tensor_grep.backends.ripgrep_backend.RipgrepBackend.is_available",
        lambda self: True,
    )
    monkeypatch.setattr(
        "tensor_grep.backends.ripgrep_backend.RipgrepBackend.search_passthrough",
        _fake_passthrough,
    )

    result = CliRunner().invoke(
        app,
        [
            "search",
            "--passthrough",
            "--unicode",
            "--pcre2-unicode",
            "--auto-hybrid-regex",
            "--no-auto-hybrid-regex",
            "--no-pcre2-unicode",
            "--no-text",
            "--no-binary",
            "--no-follow",
            "--no-glob-case-insensitive",
            "--no-ignore-file-case-insensitive",
            "--ignore-dot",
            "--ignore-exclude",
            "--ignore-files",
            "--ignore-global",
            "--ignore-messages",
            "--ignore-parent",
            "--ignore-vcs",
            "--ignore",
            "--messages",
            "--require-git",
            "--no-hidden",
            "--no-one-file-system",
            "--no-block-buffered",
            "--no-byte-offset",
            "--no-column",
            "--no-crlf",
            "--no-encoding",
            "--no-fixed-strings",
            "--no-invert-match",
            "--no-mmap",
            "--no-multiline",
            "--no-multiline-dotall",
            "--no-pcre2",
            "--no-pre",
            "--no-search-zip",
            "--no-context-separator",
            "--no-include-zero",
            "--no-line-buffered",
            "--no-max-columns-preview",
            "--no-trim",
            "--no-json",
            "--no-stats",
            "--sort-files",
            "--maxdepth",
            "2",
            "ERROR",
            str(project),
        ],
    )

    assert result.exit_code == 0
    assert seen["paths"] == [str(project)]
    assert seen["pattern"] == "ERROR"
    config = seen["config"]
    assert config.passthru is True
    assert config.unicode is True
    assert config.pcre2_unicode is True
    assert config.auto_hybrid_regex is True
    assert config.no_auto_hybrid_regex is True
    assert config.no_pcre2_unicode is True
    assert config.no_text is True
    assert config.no_binary is True
    assert config.no_follow is True
    assert config.no_glob_case_insensitive is True
    assert config.no_ignore_file_case_insensitive is True
    assert config.ignore_dot is True
    assert config.ignore_exclude is True
    assert config.ignore_files is True
    assert config.ignore_global is True
    assert config.ignore_messages is True
    assert config.ignore_parent is True
    assert config.ignore_vcs is True
    assert config.ignore is True
    assert config.messages is True
    assert config.require_git is True
    assert config.no_hidden is True
    assert config.no_one_file_system is True
    assert config.no_block_buffered is True
    assert config.no_byte_offset is True
    assert config.no_column is True
    assert config.no_crlf is True
    assert config.no_encoding is True
    assert config.no_fixed_strings is True
    assert config.no_invert_match is True
    assert config.no_mmap is True
    assert config.no_multiline is True
    assert config.no_multiline_dotall is True
    assert config.no_pcre2 is True
    assert config.no_pre is True
    assert config.no_search_zip is True
    assert config.no_context_separator is True
    assert config.no_include_zero is True
    assert config.no_line_buffered is True
    assert config.no_max_columns_preview is True
    assert config.no_trim is True
    assert config.no_json is True
    assert config.no_stats is True
    assert config.sort_files is True
    assert config.max_depth == 2


def test_python_search_treats_file_option_as_pattern_file_not_regex(monkeypatch, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "app.log").write_text("ERROR failed\n", encoding="utf-8")
    windows_pattern_file = r"C:\Users\oimir\patterns.txt"
    seen: dict[str, object] = {}

    def _fake_passthrough(self, paths, pattern, config=None):
        seen["paths"] = list(paths)
        seen["pattern"] = pattern
        seen["config"] = config
        return 0

    monkeypatch.setattr(
        "tensor_grep.backends.ripgrep_backend.RipgrepBackend.is_available",
        lambda self: True,
    )
    monkeypatch.setattr(
        "tensor_grep.backends.ripgrep_backend.RipgrepBackend.search_passthrough",
        _fake_passthrough,
    )

    result = CliRunner().invoke(
        app,
        [
            "search",
            "--format",
            "rg",
            "--file",
            windows_pattern_file,
            str(project),
        ],
    )

    assert result.exit_code == 0, result.output
    assert seen["paths"] == [str(project)]
    assert seen["pattern"] == ""
    config = seen["config"]
    assert config.file_patterns == [windows_pattern_file]


def test_search_version_should_run_from_python_search_entrypoint() -> None:
    result = CliRunner().invoke(app, ["search", "--version"])

    assert result.exit_code == 0
    assert "tensor-grep" in result.stdout


def test_search_help_should_describe_rg_format_as_public_exact_output() -> None:
    result = CliRunner().invoke(app, ["search", "--help"])

    assert result.exit_code == 0
    help_text = _strip_ansi(result.stdout)
    normalized_help = re.sub(r"\s+", " ", re.sub(r"[│┌┐└┘─]+", " ", help_text))
    assert "--format" in help_text
    assert "Output format: rg, json, table, or csv." in normalized_help
    assert "Use rg for exact ripgrep-style text output." in normalized_help
    assert "Internal formatter" not in help_text


def test_safe_stdout_line_writes_utf8_when_console_encoding_rejects_unicode(monkeypatch):
    class _FailingStdout:
        encoding = "cp1252"

        def __init__(self) -> None:
            self.buffer = io.BytesIO()

        def write(self, text: str) -> int:
            raise UnicodeEncodeError("cp1252", text, 0, 1, "simulated")

        def flush(self) -> None:
            return None

    stdout = _FailingStdout()
    monkeypatch.setattr(sys, "stdout", stdout)

    _safe_stdout_line("symbol: \u25cf")

    assert stdout.buffer.getvalue() == "symbol: \u25cf\n".encode()


def test_safe_stdout_line_prefers_utf8_buffer_for_non_utf_text(monkeypatch):
    class _ReplacingStdout:
        encoding = "cp437"

        def __init__(self) -> None:
            self.buffer = io.BytesIO()
            self.writes: list[str] = []

        def write(self, text: str) -> int:
            self.writes.append(text.encode(self.encoding, errors="replace").decode(self.encoding))
            return len(text)

        def flush(self) -> None:
            return None

    stdout = _ReplacingStdout()
    monkeypatch.setattr(sys, "stdout", stdout)

    _safe_stdout_line("a \u2014 b")

    assert stdout.writes == []
    assert stdout.buffer.getvalue() == "a \u2014 b\n".encode()


def test_write_path_list_prefers_utf8_buffer_for_non_utf_paths(monkeypatch):
    class _ReplacingStdout:
        encoding = "cp437"

        def __init__(self) -> None:
            self.buffer = io.BytesIO()
            self.writes: list[str] = []

        def write(self, text: str) -> int:
            self.writes.append(text.encode(self.encoding, errors="replace").decode(self.encoding))
            return len(text)

        def flush(self) -> None:
            return None

    stdout = _ReplacingStdout()
    monkeypatch.setattr(sys, "stdout", stdout)

    _write_path_list(["ascii.txt", "unicode/\u25cf.py"], use_nul=False)

    assert stdout.writes == []
    assert stdout.buffer.getvalue() == "ascii.txt\nunicode/\u25cf.py\n".encode()


def test_cli_should_delegate_explicit_gpu_device_ids_to_native_binary(monkeypatch):
    seen: dict[str, object] = {}
    _patch_cli_dependencies(monkeypatch)
    monkeypatch.setattr("tensor_grep.cli.main.resolve_native_tg_binary", lambda: Path("tg.exe"))

    def _fake_run(cmd, check=False):
        seen["cmd"] = list(cmd)
        seen["check"] = check
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("tensor_grep.cli.main.subprocess.run", _fake_run)

    runner = CliRunner()
    result = runner.invoke(app, ["search", "ERROR", ".", "--gpu-device-ids", "3,7,7"])

    assert result.exit_code == 0
    assert seen["cmd"] == ["tg.exe", "search", "--gpu-device-ids", "3,7", "ERROR", "."]
    assert seen["check"] is False


def test_map_json_emits_repo_inventory_envelope(tmp_path):
    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"
    src_dir.mkdir(parents=True)
    tests_dir.mkdir()

    module_path = src_dir / "sample.py"
    module_path.write_text(
        "import json\n\nclass Widget:\n    pass\n\ndef add(x, y):\n    return x + y\n",
        encoding="utf-8",
    )
    test_path = tests_dir / "test_sample.py"
    test_path.write_text("from src.sample import add\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(app, ["map", "--json", str(project)])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)

    assert payload["version"] == 1
    assert payload["routing_backend"] == "RepoMap"
    assert payload["routing_reason"] == "repo-map"
    assert payload["sidecar_used"] is False
    assert payload["path"] == str(project.resolve())
    assert payload["scan_limit"]["max_repo_files"] == 512
    assert payload["scan_limit"]["possibly_truncated"] is False
    assert str(module_path.resolve()) in payload["files"]
    assert str(test_path.resolve()) in payload["tests"]
    assert any(
        symbol["name"] == "Widget"
        and symbol["kind"] == "class"
        and symbol["file"] == str(module_path.resolve())
        for symbol in payload["symbols"]
    )
    assert any(
        symbol["name"] == "add"
        and symbol["kind"] == "function"
        and symbol["file"] == str(module_path.resolve())
        for symbol in payload["symbols"]
    )
    assert any(
        entry["file"] == str(module_path.resolve()) and "json" in entry["imports"]
        for entry in payload["imports"]
    )
    assert str(module_path.resolve()) in payload["related_paths"]


def test_map_json_accepts_agent_output_bounds(tmp_path):
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    first_path = src_dir / "alpha.py"
    first_path.write_text("def alpha():\n    return 1\n", encoding="utf-8")
    (src_dir / "beta.py").write_text("def beta():\n    return 2\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(app, ["map", "--json", "--max-files", "1", str(project)])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["files"] == [str(first_path.resolve())]
    assert payload["output_limit"] == {
        "max_files": 1,
        "emitted_files": 1,
        "original_files": 2,
        "possibly_truncated": True,
    }


def test_context_json_ranks_related_files_symbols_and_tests(tmp_path):
    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"
    src_dir.mkdir(parents=True)
    tests_dir.mkdir()

    module_path = src_dir / "payments.py"
    module_path.write_text(
        "import decimal\n\n"
        "class PaymentService:\n"
        "    pass\n\n"
        "def create_invoice(total, tax):\n"
        "    return total + tax\n",
        encoding="utf-8",
    )
    other_path = src_dir / "users.py"
    other_path.write_text("def load_user(user_id):\n    return user_id\n", encoding="utf-8")
    test_path = tests_dir / "test_payments.py"
    test_path.write_text("from src.payments import create_invoice\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["context", "--query", "invoice payment", "--json", str(project)],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)

    assert payload["version"] == 1
    assert payload["routing_backend"] == "RepoMap"
    assert payload["routing_reason"] == "context-pack"
    assert payload["sidecar_used"] is False
    assert payload["query"] == "invoice payment"
    assert payload["path"] == str(project.resolve())
    assert payload["files"][0] == str(module_path.resolve())
    assert payload["tests"][0] == str(test_path.resolve())
    assert any(
        symbol["name"] == "create_invoice" and symbol["score"] > 0 for symbol in payload["symbols"]
    )
    assert any(
        symbol["name"] == "PaymentService" and symbol["score"] > 0 for symbol in payload["symbols"]
    )
    assert payload["related_paths"][0] == str(module_path.resolve())
    assert str(test_path.resolve()) in payload["related_paths"]


def test_context_json_accepts_agent_output_bounds(tmp_path):
    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"
    src_dir.mkdir(parents=True)
    tests_dir.mkdir()

    module_path = src_dir / "payments.py"
    module_path.write_text(
        "def create_invoice(total, tax):\n    return total + tax\n",
        encoding="utf-8",
    )
    other_path = src_dir / "users.py"
    other_path.write_text("def invoice_user(user_id):\n    return user_id\n", encoding="utf-8")
    test_path = tests_dir / "test_payments.py"
    test_path.write_text("from src.payments import create_invoice\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["context", "--query", "invoice payment", "--json", "--max-files", "1", str(project)],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["files"] == [str(module_path.resolve())]
    assert str(other_path.resolve()) not in payload["files"]
    assert payload["tests"] == [str(test_path.resolve())]
    assert payload["output_limit"] == {
        "max_files": 1,
        "emitted_files": 1,
        "original_files": 2,
        "possibly_truncated": True,
    }


def test_defs_json_returns_exact_symbol_definitions(tmp_path):
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)

    module_path = src_dir / "payments.py"
    module_path.write_text(
        "class PaymentService:\n"
        "    pass\n\n"
        "def create_invoice(total, tax):\n"
        "    return total + tax\n",
        encoding="utf-8",
    )

    runner = CliRunner()
    result = runner.invoke(app, ["defs", "--symbol", "create_invoice", "--json", str(project)])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["routing_backend"] == "RepoMap"
    assert payload["routing_reason"] == "symbol-defs"
    assert payload["symbol"] == "create_invoice"
    assert len(payload["definitions"]) == 1
    assert payload["definitions"][0]["name"] == "create_invoice"
    assert payload["definitions"][0]["file"] == str(module_path.resolve())
    assert payload["files"] == [str(module_path.resolve())]
    assert [symbol["name"] for symbol in payload["symbols"]] == ["create_invoice"]


def test_symbol_commands_accept_path_symbol_positional_alias(tmp_path):
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)

    module_path = src_dir / "payments.py"
    module_path.write_text(
        "def create_invoice(total, tax):\n    return total + tax\n",
        encoding="utf-8",
    )

    runner = CliRunner()
    expected_file = str(module_path.resolve())

    def _has_expected_file(value):
        if isinstance(value, str):
            return expected_file in value
        if isinstance(value, dict):
            return any(_has_expected_file(item) for item in value.values())
        if isinstance(value, list):
            return any(_has_expected_file(item) for item in value)
        return False

    commands = {
        "defs": "symbol-defs",
        "source": "symbol-source",
        "impact": "symbol-impact",
        "refs": "symbol-refs",
        "callers": "symbol-callers",
        "blast-radius": "symbol-blast-radius",
        "blast-radius-render": "symbol-blast-radius-render",
        "blast-radius-plan": "symbol-blast-radius-plan",
    }
    # refs and callers exit 1 when the symbol has no call sites (L1: exit 1 on zero
    # results).  The symbol `create_invoice` is only defined in the test file —
    # it is never called, so references/callers are empty and the command exits 1.
    # All other commands find non-empty results (defs, source, impact) or do not
    # use _emit_symbol_command_result (blast-radius variants) and still exit 0.
    commands_that_exit_1_on_empty = {"refs", "callers"}

    for command, routing_reason in commands.items():
        result = runner.invoke(app, [command, str(project), "create_invoice", "--json"])
        expected_exit = 1 if command in commands_that_exit_1_on_empty else 0
        assert result.exit_code == expected_exit, (
            f"command={command!r} expected exit {expected_exit}, got {result.exit_code}:\n"
            + result.output
        )
        assert result.stderr == ""
        payload = json.loads(result.stdout)
        assert payload["routing_reason"] == routing_reason
        assert payload["symbol"] == "create_invoice"
        assert _has_expected_file(payload)


def test_symbol_commands_warn_for_legacy_symbol_option(tmp_path):
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)

    module_path = src_dir / "payments.py"
    module_path.write_text(
        "def create_invoice(total, tax):\n    return total + tax\n",
        encoding="utf-8",
    )

    runner = CliRunner()
    expected_file = str(module_path.resolve())

    def _has_expected_file(value):
        if isinstance(value, str):
            return expected_file in value
        if isinstance(value, dict):
            return any(_has_expected_file(item) for item in value.values())
        if isinstance(value, list):
            return any(_has_expected_file(item) for item in value)
        return False

    commands = {
        "defs": "symbol-defs",
        "source": "symbol-source",
        "impact": "symbol-impact",
        "refs": "symbol-refs",
        "callers": "symbol-callers",
        "blast-radius": "symbol-blast-radius",
        "blast-radius-render": "symbol-blast-radius-render",
        "blast-radius-plan": "symbol-blast-radius-plan",
    }
    # refs and callers exit 1 when the symbol has no call sites (L1: exit 1 on zero
    # results).  The symbol `create_invoice` is only defined in the test file —
    # it is never called, so references/callers are empty and the command exits 1.
    commands_that_exit_1_on_empty = {"refs", "callers"}

    for command, routing_reason in commands.items():
        result = runner.invoke(
            app,
            [command, "--symbol", "create_invoice", str(project), "--json"],
        )
        expected_exit = 1 if command in commands_that_exit_1_on_empty else 0
        assert result.exit_code == expected_exit, (
            f"command={command!r} expected exit {expected_exit}, got {result.exit_code}:\n"
            + result.output
        )
        assert f"Warning: --symbol is deprecated for tg {command}" in result.stderr
        assert f"for example: tg {command} <PATH> <SYMBOL>" in result.stderr
        payload = json.loads(result.stdout)
        assert payload["routing_reason"] == routing_reason
        assert payload["symbol"] == "create_invoice"
        assert _has_expected_file(payload)


def test_symbol_command_help_hides_legacy_symbol_option():
    runner = CliRunner()

    for command in (
        "defs",
        "source",
        "impact",
        "refs",
        "callers",
        "blast-radius",
        "blast-radius-render",
        "blast-radius-plan",
    ):
        result = runner.invoke(app, [command, "--help"])
        assert result.exit_code == 0, result.output
        assert "--symbol" not in _strip_ansi(result.stdout)


def test_symbol_commands_reject_positional_and_flag_symbol(tmp_path):
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    (src_dir / "payments.py").write_text(
        "def create_invoice(total, tax):\n    return total + tax\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        app,
        ["defs", str(project), "create_invoice", "--symbol", "other", "--json"],
    )

    assert result.exit_code == 1
    assert "Use either positional SYMBOL or --symbol" in result.output


def test_defs_text_lists_definition_locations(tmp_path):
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)

    module_path = src_dir / "payments.py"
    module_path.write_text(
        "class PaymentService:\n"
        "    pass\n\n"
        "def create_invoice(total, tax):\n"
        "    return total + tax\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(app, ["defs", "--symbol", "create_invoice", str(project)])

    assert result.exit_code == 0
    assert "definitions=1" in result.stdout
    assert f"{module_path.resolve()}:4" in result.stdout
    assert "create_invoice" in result.stdout


def test_defs_auto_corrects_reversed_symbol_path_positionals(tmp_path):
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    module_path = src_dir / "payments.py"
    module_path.write_text(
        "def create_invoice(total, tax):\n    return total + tax\n",
        encoding="utf-8",
    )

    # Reversed `<SYMBOL> <PATH>` order (grep muscle memory / older docs): the
    # first positional is not a path but the second one is, so it should be
    # transparently swapped instead of failing with `Path not found`.
    result = CliRunner().invoke(app, ["defs", "create_invoice", str(project)])

    assert result.exit_code == 0, result.output
    assert "Path not found" not in result.output
    assert "interpreting as `tg defs <PATH> <SYMBOL>`" in result.output
    assert "definitions=1" in result.stdout
    assert f"{module_path.resolve()}:1" in result.stdout


def test_defs_does_not_swap_when_first_positional_is_a_real_path(tmp_path):
    # When the first positional is a real path we must honor the caller's
    # explicit `<PATH> <SYMBOL>` request even if the symbol shares a name with
    # an existing path; the anti-swap guard must not fire.
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    (src_dir / "payments.py").write_text(
        "def create_invoice(total, tax):\n    return total + tax\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(app, ["defs", str(project), "create_invoice"])

    assert result.exit_code == 0, result.output
    assert "interpreting as" not in result.output
    assert "definitions=1" in result.stdout


def test_impact_json_returns_ranked_files_and_tests_for_symbol(tmp_path):
    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"
    src_dir.mkdir(parents=True)
    tests_dir.mkdir()

    module_path = src_dir / "payments.py"
    module_path.write_text(
        "import decimal\n\ndef create_invoice(total, tax):\n    return total + tax\n",
        encoding="utf-8",
    )
    other_path = src_dir / "billing.py"
    other_path.write_text(
        "from src.payments import create_invoice\n\n"
        "def invoice_total():\n"
        "    return create_invoice(10, 2)\n",
        encoding="utf-8",
    )
    test_path = tests_dir / "test_payments.py"
    test_path.write_text("from src.payments import create_invoice\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(app, ["impact", "--symbol", "create_invoice", "--json", str(project)])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["routing_backend"] == "RepoMap"
    assert payload["routing_reason"] == "symbol-impact"
    assert payload["symbol"] == "create_invoice"
    assert payload["definitions"][0]["name"] == "create_invoice"
    assert payload["files"][0] == str(module_path.resolve())
    assert str(other_path.resolve()) in payload["files"]
    assert payload["tests"][0] == str(test_path.resolve())
    assert str(test_path.resolve()) in payload["related_paths"]
    assert payload["preferred_command"] == "blast-radius"
    assert (
        payload["preferred_command_reason"]
        == "direct symbol impact is better served by blast-radius"
    )
    assert payload["trust_level"] in {"planning-signal", "heuristic"}


def test_impact_json_no_match_includes_preferred_command_metadata(tmp_path):
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)

    (src_dir / "payments.py").write_text(
        "def create_invoice(total, tax):\n    return total + tax\n",
        encoding="utf-8",
    )

    runner = CliRunner()
    result = runner.invoke(app, ["impact", "--symbol", "missing", "--json", str(project)])

    # L1: symbol commands exit 1 on zero results; "missing" resolves to no files.
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["routing_reason"] == "symbol-impact"
    assert payload["no_match"] is True
    # L1: not_found annotated by _emit_symbol_command_result
    assert payload["not_found"] is True
    assert payload["preferred_command"] == "blast-radius"
    assert (
        payload["preferred_command_reason"]
        == "direct symbol impact is better served by blast-radius"
    )
    assert payload["trust_level"] in {"planning-signal", "heuristic"}
    # H5: impact now includes a top-level "callers" key (empty list on no-match)
    assert "callers" in payload
    assert payload["callers"] == []


def test_impact_text_guides_direct_symbol_impact_to_blast_radius(tmp_path):
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)

    (src_dir / "payments.py").write_text(
        "def create_invoice(total, tax):\n    return total + tax\n",
        encoding="utf-8",
    )

    runner = CliRunner()
    result = runner.invoke(app, ["impact", "--symbol", "create_invoice", str(project)])

    assert result.exit_code == 0
    assert "preferred=blast-radius for direct symbol impact" in result.stdout


def test_source_json_returns_exact_symbol_source_blocks(tmp_path):
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)

    module_path = src_dir / "payments.py"
    module_path.write_text(
        "def create_invoice(total, tax):\n    subtotal = total + tax\n    return subtotal\n",
        encoding="utf-8",
    )

    runner = CliRunner()
    result = runner.invoke(app, ["source", "--symbol", "create_invoice", "--json", str(project)])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["routing_backend"] == "RepoMap"
    assert payload["routing_reason"] == "symbol-source"
    assert payload["symbol"] == "create_invoice"
    assert payload["definitions"][0]["file"] == str(module_path.resolve())
    assert payload["sources"][0]["file"] == str(module_path.resolve())
    assert payload["sources"][0]["start_line"] == 1
    assert payload["sources"][0]["end_line"] == 3
    assert "subtotal = total + tax" in payload["sources"][0]["source"]
    assert [symbol["name"] for symbol in payload["symbols"]] == ["create_invoice"]


def test_symbol_source_json_omits_unrelated_symbol_inventory(tmp_path):
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)

    module_path = src_dir / "worker.cjs"
    module_path.write_text(
        "\n".join([
            "function safeParseJSON(raw) {",
            "  return JSON.parse(raw);",
            "}",
            "",
            *[f"function unrelatedSymbol{i}() {{ return {i}; }}" for i in range(50)],
            "",
        ]),
        encoding="utf-8",
    )

    defs_payload = repo_map.build_symbol_defs("safeParseJSON", project)
    source_payload = repo_map.build_symbol_source("safeParseJSON", project)

    for payload in (defs_payload, source_payload):
        assert payload.get("no_match") is not True
        assert payload["definitions"][0]["file"] == str(module_path.resolve())
        assert [symbol["name"] for symbol in payload["symbols"]] == ["safeParseJSON"]
        assert "unrelatedSymbol49" not in json.dumps(payload)


def test_symbol_no_match_outputs_are_compact(tmp_path):
    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"
    src_dir.mkdir(parents=True)
    tests_dir.mkdir()
    (src_dir / "worker.py").write_text(
        "def run_cursor_worker():\n    return True\n", encoding="utf-8"
    )
    (tests_dir / "test_worker.py").write_text(
        "from src.worker import run_cursor_worker\n", encoding="utf-8"
    )

    defs_payload = repo_map.build_symbol_defs("safeParseJSON", project)
    source_payload = repo_map.build_symbol_source("safeParseJSON", project)

    for payload in (defs_payload, source_payload):
        assert payload["no_match"] is True
        assert payload["definitions"] == []
        assert payload["files"] == []
        assert payload["symbols"] == []
        assert payload["imports"] == []
        assert payload["tests"] == []
        assert payload["related_paths"] == []
        assert "No exact definition found" in payload["message"]
    assert source_payload["sources"] == []


def test_refs_json_returns_python_references_for_symbol(tmp_path):
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)

    module_path = src_dir / "payments.py"
    module_path.write_text(
        "def create_invoice(total, tax):\n    return total + tax\n",
        encoding="utf-8",
    )
    other_path = src_dir / "billing.py"
    other_path.write_text(
        "from src.payments import create_invoice\n\nresult = create_invoice(10, 2)\n",
        encoding="utf-8",
    )

    runner = CliRunner()
    result = runner.invoke(app, ["refs", "--symbol", "create_invoice", "--json", str(project)])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["routing_backend"] == "RepoMap"
    assert payload["routing_reason"] == "symbol-refs"
    assert payload["symbol"] == "create_invoice"
    assert any(ref["file"] == str(other_path.resolve()) for ref in payload["references"])
    assert str(other_path.resolve()) in payload["files"]


def test_refs_text_lists_reference_locations(tmp_path):
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)

    module_path = src_dir / "payments.py"
    module_path.write_text(
        "def create_invoice(total, tax):\n    return total + tax\n",
        encoding="utf-8",
    )
    other_path = src_dir / "billing.py"
    other_path.write_text(
        "from src.payments import create_invoice\n\nresult = create_invoice(10, 2)\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(app, ["refs", "--symbol", "create_invoice", str(project)])

    assert result.exit_code == 0
    assert "references=" in result.stdout
    assert f"{other_path.resolve()}:3" in result.stdout
    assert "result = create_invoice(10, 2)" in result.stdout


def test_refs_json_deduplicates_parser_call_references(tmp_path):
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)

    module_path = src_dir / "worker.cjs"
    module_path.write_text(
        "function prepareCursorWorkerInvocation(input) {\n"
        "  return input;\n"
        "}\n"
        "\n"
        "function runCursorWorker() {\n"
        "  return prepareCursorWorkerInvocation({});\n"
        "}\n"
        "\n"
        "module.exports = { prepareCursorWorkerInvocation, runCursorWorker };\n",
        encoding="utf-8",
    )

    payload = repo_map.build_symbol_refs("prepareCursorWorkerInvocation", project)

    keys = [(str(ref["file"]), int(ref["line"]), str(ref["text"])) for ref in payload["references"]]
    assert len(keys) == len(set(keys))
    assert keys == [
        (
            str(module_path.resolve()),
            6,
            "  return prepareCursorWorkerInvocation({});",
        )
    ]


def test_callers_json_returns_python_call_sites_for_symbol(tmp_path):
    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"
    src_dir.mkdir(parents=True)
    tests_dir.mkdir()

    module_path = src_dir / "payments.py"
    module_path.write_text(
        "def create_invoice(total, tax):\n    return total + tax\n",
        encoding="utf-8",
    )
    other_path = src_dir / "billing.py"
    other_path.write_text(
        "from src.payments import create_invoice\n\n"
        "def invoice_total():\n"
        "    return create_invoice(10, 2)\n",
        encoding="utf-8",
    )
    test_path = tests_dir / "test_payments.py"
    test_path.write_text(
        "from src.payments import create_invoice\n\nassert create_invoice(1, 2) == 3\n",
        encoding="utf-8",
    )

    runner = CliRunner()
    result = runner.invoke(app, ["callers", "--symbol", "create_invoice", "--json", str(project)])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["routing_backend"] == "RepoMap"
    assert payload["routing_reason"] == "symbol-callers"
    assert payload["symbol"] == "create_invoice"
    assert any(caller["file"] == str(other_path.resolve()) for caller in payload["callers"])
    assert str(other_path.resolve()) in payload["files"]
    assert payload["tests"][0] == str(test_path.resolve())


def test_callers_text_lists_caller_locations(tmp_path):
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)

    module_path = src_dir / "payments.py"
    module_path.write_text(
        "def create_invoice(total, tax):\n    return total + tax\n",
        encoding="utf-8",
    )
    other_path = src_dir / "billing.py"
    other_path.write_text(
        "from src.payments import create_invoice\n\n"
        "def invoice_total():\n"
        "    return create_invoice(10, 2)\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(app, ["callers", "--symbol", "create_invoice", str(project)])

    assert result.exit_code == 0
    assert "callers=1" in result.stdout
    assert f"{other_path.resolve()}:4" in result.stdout
    assert "return create_invoice(10, 2)" in result.stdout


def test_blast_radius_json_returns_transitive_symbol_radius(tmp_path):
    runner = CliRunner()
    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"
    src_dir.mkdir(parents=True)
    tests_dir.mkdir()

    module_path = src_dir / "payments.py"
    module_path.write_text("def create_invoice(total):\n    return total + 1\n", encoding="utf-8")
    service_path = src_dir / "service.py"
    service_path.write_text(
        "from src.payments import create_invoice\n\n"
        "def build_invoice(total):\n"
        "    return create_invoice(total)\n",
        encoding="utf-8",
    )
    api_path = src_dir / "api.py"
    api_path.write_text(
        "from src.service import build_invoice\n\n"
        "def post_invoice(total):\n"
        "    return build_invoice(total)\n",
        encoding="utf-8",
    )
    test_path = tests_dir / "test_api.py"
    test_path.write_text(
        "from src.api import post_invoice\n\n"
        "def test_post_invoice():\n"
        "    assert post_invoice(2) == 3\n",
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        ["blast-radius", "--symbol", "create_invoice", "--max-depth", "2", "--json", str(project)],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["routing_backend"] == "RepoMap"
    assert payload["routing_reason"] == "symbol-blast-radius"
    assert payload["symbol"] == "create_invoice"
    assert payload["max_depth"] == 2
    assert payload["definitions"][0]["file"] == str(module_path.resolve())
    assert any(caller["file"] == str(service_path.resolve()) for caller in payload["callers"])
    assert payload["files"][0] == str(module_path.resolve())
    assert payload["affected_files"] == payload["files"]
    assert payload["blast_radius_score"] is not None
    assert 0.0 <= payload["blast_radius_score"] <= 1.0
    assert str(service_path.resolve()) in payload["files"]
    assert str(api_path.resolve()) in payload["files"]
    assert payload["tests"][0] == str(test_path.resolve())
    assert any(level["depth"] == 0 for level in payload["caller_tree"])
    assert any(level["depth"] == 1 for level in payload["caller_tree"])
    assert "Depth 0:" in payload["rendered_caller_tree"]


def test_blast_radius_prioritizes_source_dirs_before_bounded_scan_cap(tmp_path):
    project = tmp_path / "project"
    archive_dir = project / "aaa_archive"
    source_dir = project / "scripts" / "agents"
    archive_dir.mkdir(parents=True)
    source_dir.mkdir(parents=True)
    for index in range(5):
        (archive_dir / f"note_{index}.md").write_text(f"# note {index}\n", encoding="utf-8")
    source_file = source_dir / "worker.cjs"
    source_file.write_text(
        "function prepareCursorWorkerInvocation(input) {\n  return input;\n}\n",
        encoding="utf-8",
    )

    payload = repo_map.build_symbol_blast_radius(
        "prepareCursorWorkerInvocation",
        project,
        max_repo_files=1,
    )

    assert payload.get("no_match") is not True
    assert payload["definitions"][0]["file"] == str(source_file.resolve())
    assert payload["scan_limit"] == {
        "max_repo_files": 1,
        "scanned_files": 1,
        "possibly_truncated": True,
    }


def test_blast_radius_skips_build_artifacts_before_bounded_scan_cap(tmp_path):
    project = tmp_path / "project"
    build_dir = project / "rust_core" / "target" / "debug"
    source_dir = project / "src" / "tensor_grep" / "cli"
    build_dir.mkdir(parents=True)
    source_dir.mkdir(parents=True)
    for index in range(5):
        (build_dir / f"artifact_{index}.rs").write_text(
            f"fn generated_{index}() {{}}\n",
            encoding="utf-8",
        )
    source_file = source_dir / "main.py"
    source_file.write_text(
        "def main_entry() -> None:\n    pass\n",
        encoding="utf-8",
    )

    payload = repo_map.build_symbol_blast_radius(
        "main_entry",
        project,
        max_repo_files=1,
    )

    assert payload.get("no_match") is not True
    assert payload["definitions"][0]["file"] == str(source_file.resolve())


def test_blast_radius_defers_root_files_before_bounded_source_scan(tmp_path):
    project = tmp_path / "project"
    source_dir = project / "src"
    source_dir.mkdir(parents=True)
    for index in range(5):
        (project / f"root_note_{index}.md").write_text(
            f"# root clutter {index}\n",
            encoding="utf-8",
        )
    source_file = source_dir / "worker.py"
    source_file.write_text(
        "def runCursorWorker() -> None:\n    pass\n",
        encoding="utf-8",
    )

    payload = repo_map.build_symbol_blast_radius(
        "runCursorWorker",
        project,
        max_repo_files=1,
    )

    assert payload.get("no_match") is not True
    assert payload["definitions"][0]["file"] == str(source_file.resolve())


def test_blast_radius_samples_sibling_source_trees_before_bounded_scan_cap(tmp_path):
    project = tmp_path / "project"
    claude_dir = project / ".claude" / "tools"
    source_dir = project / "scripts" / "agents"
    claude_dir.mkdir(parents=True)
    source_dir.mkdir(parents=True)
    for index in range(8):
        (claude_dir / f"tool_{index}.cjs").write_text(
            f"function unrelatedTool{index}() {{ return {index}; }}\n",
            encoding="utf-8",
        )
    source_file = source_dir / "worker.cjs"
    source_file.write_text(
        "function prepareCursorWorkerInvocation(input) {\n  return input;\n}\n",
        encoding="utf-8",
    )

    payload = repo_map.build_symbol_blast_radius(
        "prepareCursorWorkerInvocation",
        project,
        max_repo_files=5,
    )

    assert payload.get("no_match") is not True
    assert payload["definitions"][0]["file"] == str(source_file.resolve())


def test_blast_radius_seeds_literal_symbol_file_when_source_bucket_hits_cap(tmp_path):
    project = tmp_path / "project"
    source_dir = project / ".claude" / "lib"
    source_dir.mkdir(parents=True)
    for index in range(20):
        (source_dir / f"aaa_unrelated_{index:02}.cjs").write_text(
            f"function unrelatedTool{index}() {{ return {index}; }}\n",
            encoding="utf-8",
        )
    source_file = source_dir / "zzz_safe_parse.cjs"
    source_file.write_text(
        "function safeParseJSON(value) {\n  return JSON.parse(value);\n}\n",
        encoding="utf-8",
    )

    payload = repo_map.build_symbol_blast_radius(
        "safeParseJSON",
        project,
        max_repo_files=5,
    )

    assert payload.get("no_match") is not True
    assert payload["definitions"][0]["file"] == str(source_file.resolve())
    assert payload["scan_limit"]["possibly_truncated"] is True
    assert str(source_file.resolve()) in payload["scan_limit"]["literal_seed_files"]


def test_blast_radius_literal_seed_scan_stays_bounded(monkeypatch, tmp_path):
    project = tmp_path / "project"
    source_dir = project / ".claude" / "lib"
    source_dir.mkdir(parents=True)
    for index in range(20):
        (source_dir / f"aaa_unrelated_{index:02}.cjs").write_text(
            f"function unrelatedTool{index}() {{ return {index}; }}\n",
            encoding="utf-8",
        )
    source_file = source_dir / "zzz_safe_parse.cjs"
    source_file.write_text(
        "function safeParseJSON(value) {\n  return JSON.parse(value);\n}\n",
        encoding="utf-8",
    )
    original_iter_repo_files = repo_map._iter_repo_files
    unbounded_walks = 0

    def _bounded_iter_guard(root, **kwargs):
        nonlocal unbounded_walks
        if Path(root).resolve() == project.resolve() and kwargs.get("max_files") is None:
            unbounded_walks += 1
            raise AssertionError("literal symbol seed scan must stay bounded")
        return original_iter_repo_files(root, **kwargs)

    monkeypatch.setattr(repo_map, "_iter_repo_files", _bounded_iter_guard)

    payload = repo_map.build_symbol_blast_radius(
        "safeParseJSON",
        project,
        max_repo_files=5,
    )

    assert payload.get("no_match") is not True
    assert payload["definitions"][0]["file"] == str(source_file.resolve())
    assert unbounded_walks == 0


def test_blast_radius_output_limit_reports_omitted_counts():
    payload = {
        "symbol": "safeParseJSON",
        "callers": [{"file": f"caller_{index}.cjs"} for index in range(4)],
        "caller_tree": [{"depth": 1, "files": [f"caller_{index}.cjs" for index in range(4)]}],
        "files": [f"file_{index}.cjs" for index in range(5)],
        "file_matches": [{"path": f"file_{index}.cjs"} for index in range(5)],
        "file_summaries": [{"path": f"file_{index}.cjs", "symbols": []} for index in range(5)],
        "tests": [],
        "test_matches": [],
        "related_paths": [f"file_{index}.cjs" for index in range(5)],
        "symbols": [],
        "imports": [],
    }

    limited = repo_map._apply_blast_radius_output_limits(
        payload,
        max_callers=2,
        max_files=3,
    )

    assert limited["output_limit"] == {
        "max_callers": 2,
        "max_files": 3,
        "callers_truncated": True,
        "files_truncated": True,
        "total_callers": 4,
        "returned_callers": 2,
        "omitted_callers": 2,
        "total_files": 5,
        "returned_files": 3,
        "omitted_files": 2,
    }


def test_context_render_json_includes_enriched_edit_plan_seed_fields(tmp_path):
    runner = CliRunner()
    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"
    src_dir.mkdir(parents=True)
    tests_dir.mkdir()

    module_path = src_dir / "payments.py"
    module_path.write_text(
        "class PaymentService:\n"
        "    pass\n\n"
        "def create_invoice(total, tax):\n"
        "    return total + tax\n",
        encoding="utf-8",
    )
    test_path = tests_dir / "test_payments.py"
    test_path.write_text(
        "from src.payments import create_invoice\n\n"
        "def test_create_invoice():\n"
        "    assert create_invoice(1, 2) == 3\n",
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        ["context-render", "--query", "create invoice", "--json", str(project)],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["routing_reason"] == "context-render"
    assert payload["edit_plan_seed"]["primary_test"] == str(test_path.resolve())
    _assert_enriched_edit_plan_seed(
        payload["edit_plan_seed"],
        primary_file=module_path,
        primary_symbol_name="create_invoice",
    )


def test_agent_context_commands_accept_path_query_positional_alias(tmp_path):
    runner = CliRunner()
    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"
    src_dir.mkdir(parents=True)
    tests_dir.mkdir()

    module_path = src_dir / "payments.py"
    module_path.write_text(
        "def create_invoice(total, tax):\n    return total + tax\n",
        encoding="utf-8",
    )
    (tests_dir / "test_payments.py").write_text(
        "from src.payments import create_invoice\n",
        encoding="utf-8",
    )
    expected_file = str(module_path.resolve())

    def _has_expected_file(value):
        if isinstance(value, str):
            return expected_file in value
        if isinstance(value, dict):
            return any(_has_expected_file(item) for item in value.values())
        if isinstance(value, list):
            return any(_has_expected_file(item) for item in value)
        return False

    commands = {
        "context": "context-pack",
        "context-render": "context-render",
        "agent": "agent-context-capsule",
        "edit-plan": "context-edit-plan",
    }

    for command, routing_reason in commands.items():
        result = runner.invoke(app, [command, str(project), "create invoice", "--json"])
        assert result.exit_code == 0, result.output
        assert result.stderr == ""
        payload = json.loads(result.stdout)
        assert payload["routing_reason"] == routing_reason
        assert payload["query"] == "create invoice"
        assert _has_expected_file(payload)


def test_agent_context_commands_warn_for_legacy_query_option(tmp_path):
    runner = CliRunner()
    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"
    src_dir.mkdir(parents=True)
    tests_dir.mkdir()

    module_path = src_dir / "payments.py"
    module_path.write_text(
        "def create_invoice(total, tax):\n    return total + tax\n",
        encoding="utf-8",
    )
    (tests_dir / "test_payments.py").write_text(
        "from src.payments import create_invoice\n",
        encoding="utf-8",
    )
    expected_file = str(module_path.resolve())

    def _has_expected_file(value):
        if isinstance(value, str):
            return expected_file in value
        if isinstance(value, dict):
            return any(_has_expected_file(item) for item in value.values())
        if isinstance(value, list):
            return any(_has_expected_file(item) for item in value)
        return False

    commands = {
        "context": "context-pack",
        "context-render": "context-render",
        "agent": "agent-context-capsule",
        "edit-plan": "context-edit-plan",
    }

    for command, routing_reason in commands.items():
        result = runner.invoke(
            app,
            [command, "--query", "create invoice", str(project), "--json"],
        )
        assert result.exit_code == 0, result.output
        assert f"Warning: --query is deprecated for tg {command}" in result.stderr
        payload = json.loads(result.stdout)
        assert payload["routing_reason"] == routing_reason
        assert payload["query"] == "create invoice"
        assert _has_expected_file(payload)


def test_agent_context_help_hides_legacy_query_option():
    runner = CliRunner()

    for command in ("context", "context-render", "agent", "edit-plan"):
        result = runner.invoke(app, [command, "--help"])
        assert result.exit_code == 0, result.output
        assert "--query" not in _strip_ansi(result.stdout)


def test_agent_context_commands_reject_positional_and_flag_query(tmp_path):
    project = tmp_path / "project"
    project.mkdir()

    result = CliRunner().invoke(
        app,
        ["edit-plan", str(project), "create invoice", "--query", "other", "--json"],
    )

    assert result.exit_code == 1
    assert "Use either positional QUERY or --query" in result.output


def test_agent_capsule_json_returns_actionable_context_capsule(tmp_path):
    runner = CliRunner()
    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"
    src_dir.mkdir(parents=True)
    tests_dir.mkdir()

    module_path = src_dir / "payments.py"
    module_path.write_text(
        "def create_invoice(total, tax):\n    subtotal = total + tax\n    return subtotal\n",
        encoding="utf-8",
    )
    (tests_dir / "test_payments.py").write_text(
        "from src.payments import create_invoice\n\n"
        "def test_create_invoice():\n"
        "    assert create_invoice(10, 2) == 12\n",
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "agent",
            "--query",
            "change invoice tax calculation",
            "--max-tokens",
            "160",
            "--json",
            str(project),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["routing_reason"] == "agent-context-capsule"
    assert payload["capsule_version"] == 1
    assert payload["capsule_kind"] == "actionable_context"
    assert payload["primary_target"]["file"] == str(module_path.resolve())
    assert payload["primary_target"]["symbol"] == "create_invoice"
    assert payload["snippets"][0]["file"] == str(module_path.resolve())
    assert "subtotal = total + tax" in payload["snippets"][0]["source"]
    assert payload["snippets"][0]["line_map"][0]["line"] == 1
    assert payload["related_call_sites"] == []
    assert payload["validation_commands"]
    assert payload["edit_order"][0] == str(module_path.resolve())
    assert [row["command"] for row in payload["validation_plan"]] == payload["validation_commands"]
    assert all("detection" in row for row in payload["validation_plan"])
    assert payload["rollback"]["checkpoint_recommended"] is True
    assert payload["omissions"]["token_budget"] == 160
    assert "follow_up_reads" in payload["omissions"]
    assert payload["raw_context_ref"]["command"].startswith("tg context-render")
    assert payload["ask_user_before_editing"]["required"] is False


def test_agent_capsule_collects_bounded_call_site_evidence_for_explicit_symbol(tmp_path):
    runner = CliRunner()
    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"
    src_dir.mkdir(parents=True)
    tests_dir.mkdir()

    module_path = src_dir / "payments.py"
    module_path.write_text(
        "def create_invoice(total, tax):\n    subtotal = total + tax\n    return subtotal\n",
        encoding="utf-8",
    )
    service_path = src_dir / "billing.py"
    service_path.write_text(
        "from src.payments import create_invoice\n\n"
        "def settle_invoice():\n"
        "    return create_invoice(10, 2)\n",
        encoding="utf-8",
    )
    test_path = tests_dir / "test_payments.py"
    test_path.write_text(
        "from src.payments import create_invoice\n\n"
        "def test_create_invoice():\n"
        "    assert create_invoice(10, 2) == 12\n",
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "agent",
            "--query",
            "change create_invoice tax calculation",
            "--max-files",
            "2",
            "--max-tokens",
            "500",
            "--json",
            str(project),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["primary_target"]["file"] == str(module_path.resolve())
    assert payload["primary_target"]["symbol"] == "create_invoice"
    assert payload["call_site_evidence"]["status"] == "collected"
    assert payload["call_site_evidence"]["symbol"] == "create_invoice"
    assert payload["call_site_evidence"]["returned_call_sites"] >= 1
    assert payload["call_site_evidence"]["max_callers"] == 4
    call_site_files = {row["file"] for row in payload["related_call_sites"]}
    assert str(service_path.resolve()) in call_site_files
    assert str(test_path.resolve()) in call_site_files
    assert all(row["line"] >= 1 for row in payload["related_call_sites"])
    assert all(
        row["reason"] == "direct caller of primary target" for row in payload["related_call_sites"]
    )
    assert any(item["strategy"] == "blast-radius-call-sites" for item in payload["route_rationale"])


def test_agent_capsule_skips_call_site_collection_when_symbol_not_explicit(
    monkeypatch,
    tmp_path,
):
    runner = CliRunner()
    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"
    src_dir.mkdir(parents=True)
    tests_dir.mkdir()

    module_path = src_dir / "payments.py"
    module_path.write_text(
        "def create_invoice(total, tax):\n    subtotal = total + tax\n    return subtotal\n",
        encoding="utf-8",
    )
    (tests_dir / "test_payments.py").write_text(
        "from src.payments import create_invoice\n\n"
        "def test_create_invoice():\n"
        "    assert create_invoice(10, 2) == 12\n",
        encoding="utf-8",
    )

    def _fail_unbounded_collection(*_args, **_kwargs):
        raise AssertionError("fuzzy capsule query should not collect call-site evidence")

    monkeypatch.setattr(
        agent_capsule.repo_map, "build_symbol_blast_radius", _fail_unbounded_collection
    )

    result = runner.invoke(
        app,
        [
            "agent",
            "--query",
            "change invoice tax calculation",
            "--max-tokens",
            "500",
            "--json",
            str(project),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["primary_target"]["file"] == str(module_path.resolve())
    assert payload["primary_target"]["symbol"] == "create_invoice"
    assert payload["related_call_sites"] == []
    assert payload["call_site_evidence"] == {
        "status": "skipped",
        "reason": "primary symbol was not explicitly requested by query",
    }


def test_agent_capsule_gpu_evidence_uses_native_route(monkeypatch, tmp_path):
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    target_path = src_dir / "payments.py"
    target_path.write_text(
        "def create_invoice(total, tax):\n    subtotal = total + tax\n    return subtotal\n",
        encoding="utf-8",
    )
    calls: list[list[str]] = []

    def _fake_gpu_run(command, **_kwargs):
        calls.append([str(part) for part in command])
        if len(calls) == 1:
            payload = {
                "routing_backend": "NativeGpuBackend",
                "routing_reason": "gpu-device-ids-explicit-native",
                "sidecar_used": False,
                "total_matches": 1,
                "matches": [{"file": "probe.log", "line": 1, "text": "probe"}],
            }
        else:
            payload = {
                "routing_backend": "NativeGpuBackend",
                "routing_reason": "gpu-device-ids-explicit-native",
                "sidecar_used": False,
                "total_matches": 2,
                "matches": [
                    {
                        "file": str(target_path.resolve()),
                        "line": 1,
                        "text": "def create_invoice(total, tax):",
                        "pattern_text": "invoice",
                    }
                ],
            }
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    monkeypatch.setattr(agent_capsule.subprocess, "run", _fake_gpu_run)

    payload = agent_capsule.build_agent_capsule(
        "change invoice tax calculation",
        project,
        gpu_device_ids=[0],
        gpu_timeout_s=1,
    )

    acceleration = payload["gpu_acceleration"]
    assert acceleration["status"] == "used"
    assert acceleration["requested_device_ids"] == [0]
    assert acceleration["routing_backend"] == "NativeGpuBackend"
    assert acceleration["sidecar_used"] is False
    assert acceleration["matched_files"] == [str(target_path.resolve())]
    assert payload["context_consistency"]["gpu_evidence_primary_file_matched"] is True
    assert any(item["strategy"] == "gpu-native-evidence" for item in payload["route_rationale"])
    assert any("-e" in call for call in calls)


def test_agent_capsule_gpu_evidence_reads_native_output_as_utf8(monkeypatch, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "app.py").write_text("def create_invoice():\n    return 'é'\n", encoding="utf-8")
    calls: list[list[str]] = []
    kwargs_seen: list[dict[str, object]] = []

    def _fake_gpu_run(command, **kwargs):
        calls.append([str(part) for part in command])
        kwargs_seen.append(dict(kwargs))
        payload = {
            "routing_backend": "NativeGpuBackend",
            "routing_reason": "gpu-device-ids-explicit-native",
            "sidecar_used": False,
            "total_matches": 0,
            "matches": [],
        }
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    monkeypatch.setattr(agent_capsule.subprocess, "run", _fake_gpu_run)

    payload = agent_capsule.build_agent_capsule(
        "change invoice tax calculation",
        project,
        gpu_device_ids=[0],
        gpu_timeout_s=1,
    )

    assert payload["gpu_acceleration"]["status"] == "ready_no_matches"
    gpu_kwargs = [
        kwargs
        for kwargs, command in zip(kwargs_seen, calls, strict=True)
        if "--gpu-device-ids" in [str(part) for part in command]
    ]
    assert gpu_kwargs
    assert all(kwargs["encoding"] == "utf-8" for kwargs in gpu_kwargs)
    assert all(kwargs["errors"] == "replace" for kwargs in gpu_kwargs)


def test_agent_capsule_gpu_evidence_payload_is_bounded(monkeypatch, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    target_path = project / "payments.py"
    target_path.write_text("def create_invoice():\n    return 1\n", encoding="utf-8")
    calls = 0

    def _fake_gpu_run(command, **_kwargs):
        nonlocal calls
        calls += 1
        matches = [
            {
                "file": str(target_path.resolve()),
                "line": index + 1,
                "text": f"def create_invoice_{index}():",
                "pattern_text": "invoice",
            }
            for index in range(12)
        ]
        payload = {
            "version": 1,
            "routing_backend": "NativeGpuBackend",
            "routing_reason": "gpu-device-ids-explicit-native",
            "sidecar_used": False,
            "total_matches": len(matches),
            "total_files": 1,
            "requested_gpu_device_ids": [0],
            "routing_gpu_device_ids": [0],
            "pipeline": {"pattern_count": 1, "kernel_time_ms": 0.1},
            "matches": matches if calls > 1 else matches[:1],
        }
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    monkeypatch.setattr(agent_capsule.subprocess, "run", _fake_gpu_run)

    payload = agent_capsule.build_agent_capsule(
        "change invoice tax calculation",
        project,
        gpu_device_ids=[0],
        gpu_timeout_s=1,
    )

    acceleration = payload["gpu_acceleration"]
    assert acceleration["status"] == "used"
    evidence_payload = acceleration["evidence"]["payload"]
    assert "matches" not in evidence_payload
    assert len(evidence_payload["matches_preview"]) == 3
    assert evidence_payload["matches_omitted"] == 9
    assert evidence_payload["total_matches"] == 12


def test_agent_capsule_gpu_probe_uses_resolved_native_tg(monkeypatch, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "app.py").write_text(
        "def create_invoice(total):\n    return total\n",
        encoding="utf-8",
    )
    native_tg = tmp_path / "managed" / "tg.exe"
    native_tg.parent.mkdir()
    native_tg.write_text("", encoding="utf-8")
    calls: list[list[str]] = []

    def _fake_gpu_run(command, **_kwargs):
        calls.append([str(part) for part in command])
        payload = {
            "routing_backend": "GpuSidecar",
            "routing_reason": "gpu-device-ids-explicit",
            "sidecar_used": True,
            "total_matches": 1,
            "matches": [{"file": "probe.log", "line": 1, "text": "probe"}],
        }
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    monkeypatch.setattr(agent_capsule, "resolve_native_tg_binary", lambda: native_tg)
    monkeypatch.setattr(agent_capsule.subprocess, "run", _fake_gpu_run)

    payload = agent_capsule.build_agent_capsule(
        "change invoice tax calculation",
        project,
        gpu_device_ids=[0],
        gpu_timeout_s=1,
    )

    assert calls
    assert calls[0][0] == str(native_tg)
    assert payload["gpu_acceleration"]["status"] == "unsupported"


def test_agent_capsule_gpu_evidence_rejects_sidecar_route(monkeypatch, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "app.py").write_text(
        "def create_invoice(total):\n    return total\n",
        encoding="utf-8",
    )
    calls: list[list[str]] = []

    def _fake_gpu_run(command, **_kwargs):
        calls.append([str(part) for part in command])
        payload = {
            "routing_backend": "GpuSidecar",
            "routing_reason": "gpu-device-ids-explicit",
            "sidecar_used": True,
            "total_matches": 1,
            "matches": [{"file": "probe.log", "line": 1, "text": "probe"}],
        }
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    monkeypatch.setattr(agent_capsule, "resolve_native_tg_binary", lambda: None)
    monkeypatch.setattr(agent_capsule.subprocess, "run", _fake_gpu_run)

    payload = agent_capsule.build_agent_capsule(
        "change invoice tax calculation",
        project,
        gpu_device_ids=[0],
        gpu_timeout_s=1,
    )

    acceleration = payload["gpu_acceleration"]
    assert acceleration["status"] == "unsupported"
    assert acceleration["routing_backend"] == "GpuSidecar"
    assert acceleration["sidecar_used"] is True
    assert "sidecar-routed" in acceleration["reason"]
    assert len(calls) == 1


def test_agent_capsule_gpu_probe_summary_redacts_probe_paths(monkeypatch, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "app.py").write_text(
        "def create_invoice(total):\n    return total\n",
        encoding="utf-8",
    )

    def _fake_gpu_run(command, **_kwargs):
        payload = {
            "routing_backend": "GpuSidecar",
            "routing_reason": "gpu-device-ids-explicit",
            "sidecar_used": True,
            "path": str(command[-1]),
            "total_matches": 1,
            "matches": [{"file": str(command[-1]) + "/probe.log", "line": 1, "text": "probe"}],
        }
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    monkeypatch.setattr(agent_capsule, "resolve_native_tg_binary", lambda: None)
    monkeypatch.setattr(agent_capsule.subprocess, "run", _fake_gpu_run)

    payload = agent_capsule.build_agent_capsule(
        "change invoice tax calculation",
        project,
        gpu_device_ids=[0],
        gpu_timeout_s=1,
    )

    probe = payload["gpu_acceleration"]["probe"]
    serialized = json.dumps(probe)
    assert "tg-agent-gpu-probe" not in serialized
    assert "probe.log" not in serialized
    assert probe["payload"]["path"] == "<agent-gpu-probe-root>"
    assert probe["payload"]["matches_preview"][0]["file"] == "<agent-gpu-probe-file>"


def test_agent_capsule_gpu_probe_failure_redacts_probe_command_path(tmp_path):
    probe_root = tmp_path / "tg-agent-gpu-probe-secret"
    probe = agent_capsule._summarize_agent_gpu_json_result(
        {
            "status": "timeout",
            "command": f"tg search --json {probe_root}",
            "argv": ["tg", "search", "--json", str(probe_root)],
        },
        redact_probe_paths=True,
    )
    serialized = json.dumps(probe)

    assert "tg-agent-gpu-probe" not in serialized
    assert probe["argv"][-1] == "<agent-gpu-probe-root>"


def test_agent_capsule_cli_accepts_gpu_device_ids(monkeypatch, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "app.py").write_text(
        "def create_invoice(total):\n    return total\n",
        encoding="utf-8",
    )

    def _fake_gpu_run(command, **_kwargs):
        payload = {
            "routing_backend": "GpuSidecar",
            "routing_reason": "gpu-device-ids-explicit",
            "sidecar_used": True,
            "total_matches": 1,
            "matches": [{"file": "probe.log", "line": 1, "text": "probe"}],
        }
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    monkeypatch.setattr(agent_capsule.subprocess, "run", _fake_gpu_run)

    result = CliRunner().invoke(
        app,
        [
            "agent",
            "--query",
            "change invoice tax calculation",
            "--gpu-device-ids",
            "0,1",
            "--json",
            str(project),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["gpu_acceleration"]["requested_device_ids"] == [0, 1]
    assert payload["gpu_acceleration"]["status"] == "unsupported"


def _write_mixed_invoice_fixture(tmp_path: Path, *, package_json: bool = False) -> dict[str, Path]:
    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"
    src_dir.mkdir(parents=True)
    tests_dir.mkdir()

    python_path = src_dir / "payments.py"
    python_path.write_text(
        "TAX_RATE = 0.0825\n\n"
        "def create_invoice(subtotal):\n"
        "    tax = subtotal * TAX_RATE\n"
        "    total = subtotal + tax\n"
        "    return {'subtotal': subtotal, 'tax': tax, 'total': total}\n",
        encoding="utf-8",
    )
    python_test_path = tests_dir / "test_payments.py"
    python_test_path.write_text(
        "from src.payments import TAX_RATE, create_invoice\n\n"
        "def test_create_invoice_tax_calculation():\n"
        "    invoice = create_invoice(100)\n"
        "    assert invoice['tax'] == 100 * TAX_RATE\n"
        "    assert invoice['total'] == 100 + 100 * TAX_RATE\n",
        encoding="utf-8",
    )
    ts_path = src_dir / "app.ts"
    ts_path.write_text(
        "export function createInvoice(subtotal: number): number {\n"
        "  const serviceFee = 0;\n"
        "  return subtotal + serviceFee;\n"
        "}\n",
        encoding="utf-8",
    )
    if package_json:
        (project / "package.json").write_text(
            json.dumps({
                "name": "mixed-invoice",
                "devDependencies": {"vitest": "^1.0.0"},
            }),
            encoding="utf-8",
        )
    return {
        "project": project,
        "python": python_path,
        "python_test": python_test_path,
        "typescript": ts_path,
    }


def _write_invoice_service_ambiguity_fixture(tmp_path: Path) -> dict[str, Path]:
    project = tmp_path / "project"
    src_dir = project / "src"
    billing_dir = src_dir / "billing"
    tests_dir = project / "tests"
    billing_dir.mkdir(parents=True)
    tests_dir.mkdir()
    (src_dir / "__init__.py").write_text("", encoding="utf-8")
    (billing_dir / "__init__.py").write_text("", encoding="utf-8")

    payments_path = src_dir / "payments.py"
    payments_path.write_text(
        "TAX_RATE = 0.0825\n\n"
        "def create_invoice(subtotal):\n"
        "    tax = subtotal * TAX_RATE\n"
        "    total = subtotal + tax\n"
        "    return {'subtotal': subtotal, 'tax': tax, 'total': total}\n",
        encoding="utf-8",
    )
    service_path = billing_dir / "service.py"
    service_path.write_text(
        "from src.payments import create_invoice\n\n"
        "def render_invoice_tax_summary(subtotal):\n"
        "    invoice = create_invoice(subtotal)\n"
        "    return f\"invoice tax calculation: {invoice['tax']}\"\n",
        encoding="utf-8",
    )
    test_path = tests_dir / "test_payments.py"
    test_path.write_text(
        "from src.payments import TAX_RATE, create_invoice\n\n"
        "def test_create_invoice_tax_calculation():\n"
        "    invoice = create_invoice(100)\n"
        "    assert invoice['tax'] == 100 * TAX_RATE\n",
        encoding="utf-8",
    )
    return {
        "project": project,
        "payments": payments_path,
        "service": service_path,
        "test": test_path,
    }


def _agent_capsule_payload_for_query(project: Path, query: str) -> dict[str, object]:
    result = CliRunner().invoke(
        app,
        ["agent", "--query", query, "--json", str(project)],
    )
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def test_agent_capsule_python_invoice_tax_query_selects_python_evidence(tmp_path):
    paths = _write_mixed_invoice_fixture(tmp_path)

    payload = _agent_capsule_payload_for_query(
        paths["project"],
        "python invoice tax calculation",
    )

    assert payload["primary_target"]["file"] == str(paths["python"].resolve())
    assert payload["primary_target"]["symbol"] == "create_invoice"
    assert payload["context_consistency"]["query_language_hints"] == ["python"]
    assert payload["context_consistency"]["primary_target_language"] == "python"


def test_agent_capsule_python_invoice_tax_query_keeps_python_target_with_js_manifest(tmp_path):
    paths = _write_mixed_invoice_fixture(tmp_path, package_json=True)

    payload = _agent_capsule_payload_for_query(
        paths["project"],
        "python invoice tax calculation",
    )

    assert payload["primary_target"]["file"] == str(paths["python"].resolve())
    assert payload["primary_target"]["symbol"] == "create_invoice"
    assert payload["context_consistency"]["query_language_hints"] == ["python"]
    assert payload["context_consistency"]["primary_target_language"] == "python"
    assert any("pytest" in command for command in payload["validation_commands"])
    assert payload["ask_user_before_editing"]["required"] is False


def test_agent_capsule_language_hint_beats_cross_language_lexical_noise(tmp_path):
    paths = _write_mixed_invoice_fixture(tmp_path, package_json=True)
    paths["typescript"].write_text(
        "export function createInvoice(subtotal: number): number {\n"
        "  const taxCalculation = subtotal * 0.0825;\n"
        "  const invoiceTaxCalculation = subtotal + taxCalculation;\n"
        "  return invoiceTaxCalculation;\n"
        "}\n",
        encoding="utf-8",
    )

    payload = _agent_capsule_payload_for_query(
        paths["project"],
        "python invoice tax calculation",
    )

    assert payload["primary_target"]["file"] == str(paths["python"].resolve())
    assert payload["primary_target"]["symbol"] == "create_invoice"
    assert payload["context_consistency"]["primary_target_language"] == "python"
    assert payload["ask_user_before_editing"]["required"] is False


def test_agent_capsule_file_name_hint_beats_cross_language_symbol_similarity(tmp_path):
    paths = _write_mixed_invoice_fixture(tmp_path, package_json=True)
    paths["typescript"].write_text(
        "export function createInvoice(subtotal: number): number {\n"
        "  const taxCalculation = subtotal * 0.0825;\n"
        "  return subtotal + taxCalculation;\n"
        "}\n",
        encoding="utf-8",
    )

    payload = _agent_capsule_payload_for_query(
        paths["project"],
        "payments.py invoice tax calculation",
    )

    assert payload["primary_target"]["file"] == str(paths["python"].resolve())
    assert payload["primary_target"]["symbol"] == "create_invoice"


def test_agent_context_commands_prefer_invoice_implementation_over_service_mentions(tmp_path):
    paths = _write_invoice_service_ambiguity_fixture(tmp_path)

    context_result = CliRunner().invoke(
        app,
        [
            "context-render",
            "--query",
            "change invoice tax calculation",
            "--json",
            str(paths["project"]),
        ],
    )
    edit_result = CliRunner().invoke(
        app,
        [
            "edit-plan",
            "--query",
            "change invoice tax calculation",
            "--json",
            str(paths["project"]),
        ],
    )
    agent_payload = _agent_capsule_payload_for_query(
        paths["project"],
        "change invoice tax calculation",
    )

    assert context_result.exit_code == 0, context_result.output
    context_payload = json.loads(context_result.stdout)
    assert context_payload["edit_plan_seed"]["primary_file"] == str(paths["payments"].resolve())
    assert context_payload["navigation_pack"]["primary_target"]["file"] == str(
        paths["payments"].resolve()
    )
    assert edit_result.exit_code == 0, edit_result.output
    edit_payload = json.loads(edit_result.stdout)
    assert edit_payload["edit_plan_seed"]["primary_file"] == str(paths["payments"].resolve())
    assert edit_payload["navigation_pack"]["primary_target"]["file"] == str(
        paths["payments"].resolve()
    )
    assert agent_payload["primary_target"]["file"] == str(paths["payments"].resolve())
    assert agent_payload["primary_target"]["symbol"] == "create_invoice"
    assert agent_payload["ask_user_before_editing"]["required"] is False


def test_agent_capsule_exact_symbol_query_prefers_exact_symbol_over_prefix(tmp_path):
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    module_path = src_dir / "native_search.py"
    module_path.write_text(
        "def run_native_search_files():\n"
        "    total = 0\n"
        "    total += 1\n"
        "    total += 2\n"
        "    return total\n\n"
        "def run_native_search():\n"
        "    return 1\n",
        encoding="utf-8",
    )

    payload = _agent_capsule_payload_for_query(project, "run_native_search")

    assert payload["primary_target"]["file"] == str(module_path.resolve())
    assert payload["primary_target"]["symbol"] == "run_native_search"


def test_agent_capsule_filters_file_only_alternative_targets(monkeypatch, tmp_path):
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    primary_path = src_dir / "payments.py"
    alternative_path = src_dir / "notes.py"
    primary_path.write_text("def create_invoice():\n    return 1\n", encoding="utf-8")
    alternative_path.write_text("invoice notes\n", encoding="utf-8")

    def _fake_context_render(*_args, **_kwargs):
        return {
            "navigation_pack": {
                "primary_target": {
                    "file": str(primary_path),
                    "symbol": "create_invoice",
                    "kind": "function",
                    "start_line": 1,
                    "end_line": 2,
                },
                "follow_up_reads": [],
                "validation_commands": [],
            },
            "edit_plan_seed": {
                "primary_file": str(primary_path),
                "primary_symbol": {"name": "create_invoice", "kind": "function"},
                "primary_span": {"start_line": 1, "end_line": 2},
                "confidence": {"overall": 0.9},
                "validation_plan": [],
                "validation_commands": [],
                "edit_ordering": [str(primary_path)],
            },
            "candidate_edit_targets": {
                "files": [str(alternative_path)],
                "symbols": [],
            },
            "file_matches": [
                {
                    "path": str(alternative_path),
                    "score": 80,
                    "reasons": ["source"],
                    "provenance": ["heuristic"],
                }
            ],
            "validation_commands": [],
            "sources": [
                {
                    "file": str(primary_path),
                    "symbol": "create_invoice",
                    "start_line": 1,
                    "end_line": 2,
                    "source": primary_path.read_text(encoding="utf-8"),
                }
            ],
            "context_consistency": {"primary_file_included": True},
        }

    monkeypatch.setattr(agent_capsule.repo_map, "build_context_render", _fake_context_render)

    payload = agent_capsule.build_agent_capsule(
        "create_invoice",
        project,
        include_blast_radius=False,
        max_tokens=400,
    )

    assert payload["alternative_targets"] == []


def test_context_pack_uses_repo_map_imports_for_direct_validation_evidence(
    monkeypatch, tmp_path: Path
):
    paths = _write_invoice_service_ambiguity_fixture(tmp_path)
    payload = repo_map.build_repo_map(paths["project"])

    def _fail_if_context_scoring_reparses_tests(*_args, **_kwargs):
        raise AssertionError("context scoring should reuse repo-map imports")

    monkeypatch.setattr(
        repo_map,
        "_file_imports_symbol_from_definition",
        _fail_if_context_scoring_reparses_tests,
    )

    context_payload = repo_map.build_context_pack_from_map(
        payload,
        "change invoice tax calculation",
    )

    assert context_payload["files"][0] == str(paths["payments"].resolve())
    primary_match = context_payload["file_matches"][0]
    assert primary_match["path"] == str(paths["payments"].resolve())
    assert "validation-direct-definition" in primary_match["reasons"]


def test_agent_capsule_change_invoice_tax_query_prefers_python_body_and_tests(tmp_path):
    paths = _write_mixed_invoice_fixture(tmp_path)

    payload = _agent_capsule_payload_for_query(
        paths["project"],
        "change invoice tax calculation",
    )

    assert payload["primary_target"]["file"] == str(paths["python"].resolve())
    assert payload["primary_target"]["symbol"] == "create_invoice"
    assert any(
        command.startswith("uv run pytest tests/test_payments.py")
        for command in payload["validation_commands"]
    )
    assert payload["ask_user_before_editing"]["required"] is False
    ambiguity = payload["ambiguity"]
    assert ambiguity["status"] == "tie_resolved"
    assert ambiguity["resolved_by"] == "targeted-validation"
    assert any(
        command.startswith("uv run pytest tests/test_payments.py")
        for command in ambiguity["resolution_evidence"]
    )
    assert ambiguity["requires_confirmation"] is False
    assert ambiguity["tie_count"] == 1
    assert ambiguity["tied_alternative_targets"][0]["file"] == str(paths["typescript"].resolve())


def test_agent_capsule_ambiguous_invoice_tax_query_surfaces_cross_language_alternatives(tmp_path):
    paths = _write_mixed_invoice_fixture(tmp_path)

    payload = _agent_capsule_payload_for_query(
        paths["project"],
        "change invoice tax calculation",
    )

    alternatives = payload["alternative_targets"]
    assert any(
        item["file"] == str(paths["typescript"].resolve())
        and item["symbol"] == "createInvoice"
        and item["language"] == "typescript"
        for item in alternatives
    )
    assert all(item["file"] != payload["primary_target"]["file"] for item in alternatives)


def test_agent_capsule_alternative_confidence_does_not_exceed_selected_primary(tmp_path):
    paths = _write_mixed_invoice_fixture(tmp_path, package_json=True)
    paths["typescript"].write_text(
        "export function createInvoice(subtotal: number): number {\n"
        "  const taxCalculation = subtotal * 0.0825;\n"
        "  const invoiceTaxCalculation = subtotal + taxCalculation;\n"
        "  return invoiceTaxCalculation;\n"
        "}\n",
        encoding="utf-8",
    )

    payload = _agent_capsule_payload_for_query(
        paths["project"],
        "change invoice tax calculation",
    )

    assert payload["alternative_targets"]
    primary_confidence = payload["primary_target"]["confidence"]
    assert all(item["confidence"] <= primary_confidence for item in payload["alternative_targets"])


def test_agent_capsule_equal_confidence_alternative_requires_confirmation(monkeypatch, tmp_path):
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    python_path = src_dir / "payments.py"
    python_path.write_text(
        "def create_invoice(subtotal):\n    tax = subtotal * 0.0825\n    return subtotal + tax\n",
        encoding="utf-8",
    )
    typescript_path = src_dir / "app.ts"
    typescript_path.write_text(
        "export function createInvoice(subtotal: number): number {\n"
        "  const taxCalculation = subtotal * 0.0825;\n"
        "  const invoiceTaxCalculation = subtotal + taxCalculation;\n"
        "  return invoiceTaxCalculation;\n"
        "}\n",
        encoding="utf-8",
    )

    def _fake_context_render(*_args, **_kwargs):
        return {
            "navigation_pack": {
                "primary_target": {
                    "file": str(python_path),
                    "symbol": "create_invoice",
                    "kind": "function",
                    "start_line": 1,
                    "end_line": 3,
                },
                "follow_up_reads": [],
                "validation_commands": [],
            },
            "edit_plan_seed": {
                "primary_file": str(python_path),
                "primary_symbol": {"name": "create_invoice", "kind": "function"},
                "primary_span": {"start_line": 1, "end_line": 3},
                "confidence": {"overall": 0.9},
                "validation_plan": [],
                "validation_commands": [],
                "edit_ordering": [str(python_path)],
            },
            "candidate_edit_targets": {
                "symbols": [
                    {
                        "file": str(typescript_path),
                        "name": "createInvoice",
                        "kind": "function",
                        "line": 1,
                        "score": 90,
                    }
                ]
            },
            "file_matches": [
                {
                    "path": str(typescript_path),
                    "score": 90,
                    "reasons": ["source"],
                    "provenance": ["heuristic"],
                }
            ],
            "validation_commands": [],
            "sources": [
                {
                    "file": str(python_path),
                    "symbol": "create_invoice",
                    "start_line": 1,
                    "end_line": 3,
                    "source": python_path.read_text(encoding="utf-8"),
                }
            ],
            "context_consistency": {"primary_file_included": True},
        }

    monkeypatch.setattr(agent_capsule.repo_map, "build_context_render", _fake_context_render)

    payload = agent_capsule.build_agent_capsule(
        "change invoice tax calculation",
        project,
        max_tokens=400,
    )

    assert payload["alternative_targets"]
    assert payload["context_consistency"]["alternative_confidence_tie"] is True
    assert payload["context_consistency"]["tied_alternative_targets"]
    ambiguity = payload["ambiguity"]
    assert ambiguity["status"] == "tie_requires_confirmation"
    assert ambiguity["requires_confirmation"] is True
    assert ambiguity["tie_count"] == 1
    assert ambiguity["tied_alternative_targets"]
    assert payload["confidence"]["overall"] <= 0.74
    assert payload["primary_target"]["confidence"] <= 0.74
    assert payload["ask_user_before_editing"]["required"] is True
    assert (
        "alternative target confidence ties primary target"
        in payload["ask_user_before_editing"]["reasons"]
    )


def test_agent_capsule_unrequested_marker_helper_tie_requires_confirmation(monkeypatch, tmp_path):
    project = tmp_path / "project"
    python_path = project / "src" / "tensor_grep" / "cli" / "main.py"
    rust_path = project / "rust_core" / "src" / "python_sidecar.rs"
    python_path.parent.mkdir(parents=True)
    rust_path.parent.mkdir(parents=True)
    python_path.write_text(
        "def _write_windows_exe_bridge_marker(root):\n    return root / 'tg.com'\n",
        encoding="utf-8",
    )
    rust_path.write_text(
        "pub fn is_managed_windows_exe_bridge(path: &std::path::Path) -> bool {\n    true\n}\n",
        encoding="utf-8",
    )

    validation_plan = [
        {
            "command": "uv run pytest tests/unit/test_cli_modes.py -q",
            "runner": "pytest",
            "detection": "detected",
        }
    ]

    def _fake_context_render(*_args, **_kwargs):
        return {
            "navigation_pack": {
                "primary_target": {
                    "file": str(python_path),
                    "symbol": "_write_windows_exe_bridge_marker",
                    "kind": "function",
                    "start_line": 1,
                    "end_line": 2,
                },
                "follow_up_reads": [],
                "validation_commands": [validation_plan[0]["command"]],
            },
            "edit_plan_seed": {
                "primary_file": str(python_path),
                "primary_symbol": {
                    "name": "_write_windows_exe_bridge_marker",
                    "kind": "function",
                },
                "primary_span": {"start_line": 1, "end_line": 2},
                "confidence": {"overall": 0.9},
                "validation_plan": validation_plan,
                "validation_commands": [validation_plan[0]["command"]],
                "edit_ordering": [str(python_path)],
            },
            "candidate_edit_targets": {
                "symbols": [
                    {
                        "file": str(rust_path),
                        "name": "is_managed_windows_exe_bridge",
                        "kind": "function",
                        "line": 1,
                        "score": 90,
                    }
                ]
            },
            "file_matches": [
                {
                    "path": str(rust_path),
                    "score": 90,
                    "reasons": ["source"],
                    "provenance": ["parser-backed"],
                }
            ],
            "validation_commands": [validation_plan[0]["command"]],
            "sources": [
                {
                    "file": str(python_path),
                    "symbol": "_write_windows_exe_bridge_marker",
                    "start_line": 1,
                    "end_line": 2,
                    "source": python_path.read_text(encoding="utf-8"),
                }
            ],
            "context_consistency": {"primary_file_included": True},
        }

    monkeypatch.setattr(agent_capsule.repo_map, "build_context_render", _fake_context_render)

    payload = agent_capsule.build_agent_capsule(
        "harden Windows subprocess exe bridge",
        project,
        max_tokens=400,
    )

    assert payload["ambiguity"]["status"] == "tie_requires_confirmation"
    assert payload["ask_user_before_editing"]["required"] is True
    assert (
        "primary target is an unrequested marker helper with equal-confidence alternatives"
        in payload["context_consistency"]["downgrade_reasons"]
    )


def test_agent_capsule_exact_camel_symbol_stays_above_snake_case_bridge(tmp_path):
    paths = _write_mixed_invoice_fixture(tmp_path, package_json=True)

    payload = _agent_capsule_payload_for_query(
        paths["project"],
        "createInvoice tax calculation",
    )

    assert payload["primary_target"]["file"] == str(paths["typescript"].resolve())
    assert payload["primary_target"]["symbol"] == "createInvoice"
    assert payload["context_consistency"]["primary_target_language"] == "typescript"


def test_agent_capsule_exact_snake_symbol_keeps_python_target(tmp_path):
    paths = _write_mixed_invoice_fixture(tmp_path, package_json=True)

    payload = _agent_capsule_payload_for_query(
        paths["project"],
        "create_invoice tax calculation",
    )

    assert payload["primary_target"]["file"] == str(paths["python"].resolve())
    assert payload["primary_target"]["symbol"] == "create_invoice"
    assert payload["context_consistency"]["primary_target_language"] == "python"


def test_agent_capsule_conflicting_language_and_exact_symbol_requires_confirmation(tmp_path):
    paths = _write_mixed_invoice_fixture(tmp_path, package_json=True)

    payload = _agent_capsule_payload_for_query(
        paths["project"],
        "python createInvoice tax calculation",
    )

    assert payload["primary_target"]["file"] == str(paths["typescript"].resolve())
    assert payload["context_consistency"]["query_language_hints"] == ["python"]
    assert payload["context_consistency"]["primary_target_language"] == "typescript"
    assert payload["confidence"]["overall"] <= 0.55
    assert payload["primary_target"]["confidence"] <= 0.55
    assert payload["ask_user_before_editing"]["required"] is True
    assert any(
        "language intent" in reason for reason in payload["ask_user_before_editing"]["reasons"]
    )


def test_query_language_hints_are_token_bounded() -> None:
    assert repo_map._query_language_hints("python invoice tax") == ["python"]
    assert repo_map._query_language_hints("py ts js rs") == [
        "python",
        "typescript",
        "javascript",
        "rust",
    ]
    assert repo_map._query_language_hints("cryptography typescriptish") == []


def test_context_render_filters_pytest_only_validation_for_typescript_primary(tmp_path):
    paths = _write_mixed_invoice_fixture(tmp_path)

    result = CliRunner().invoke(
        app,
        [
            "context-render",
            "--query",
            "createInvoice tax calculation",
            "--json",
            str(paths["project"]),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["edit_plan_seed"]["primary_file"] == str(paths["typescript"].resolve())
    assert payload["edit_plan_seed"]["validation_plan"] == []
    assert payload["edit_plan_seed"]["validation_commands"] == []
    assert payload["validation_commands"] == []
    assert payload["navigation_pack"]["validation_commands"] == []
    alignment = payload["edit_plan_seed"]["validation_alignment"]
    assert alignment["primary_target_language"] == "typescript"
    assert alignment["status"] == "mismatch-filtered"
    assert alignment["filtered_count"] >= 1
    assert any("pytest" in issue for issue in alignment["issues"])
    assert payload["context_consistency"]["validation_filtered_count"] >= 1


def test_edit_plan_filters_pytest_only_validation_for_typescript_primary(tmp_path):
    paths = _write_mixed_invoice_fixture(tmp_path)

    result = CliRunner().invoke(
        app,
        [
            "edit-plan",
            "--query",
            "createInvoice tax calculation",
            "--json",
            str(paths["project"]),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["edit_plan_seed"]["primary_file"] == str(paths["typescript"].resolve())
    assert payload["edit_plan_seed"]["validation_plan"] == []
    assert payload["edit_plan_seed"]["validation_commands"] == []
    assert payload["validation_commands"] == []
    assert payload["navigation_pack"]["validation_commands"] == []
    alignment = payload["edit_plan_seed"]["validation_alignment"]
    assert alignment["primary_target_language"] == "typescript"
    assert alignment["status"] == "mismatch-filtered"
    assert alignment["filtered_count"] >= 1
    assert any("pytest" in issue for issue in alignment["issues"])


def test_validation_alignment_uses_primary_file_when_primary_symbol_is_missing(
    tmp_path,
):
    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"
    src_dir.mkdir(parents=True)
    tests_dir.mkdir()
    ts_primary = src_dir / "app.ts"
    ts_primary.write_text("export const invoiceTotal = 1;\n", encoding="utf-8")
    py_test = tests_dir / "test_payments.py"
    py_test.write_text("def test_invoice_total():\n    assert True\n", encoding="utf-8")

    plan, alignment = repo_map._validation_plan_and_alignment_for_tests(
        [str(py_test)],
        repo_root=project,
        primary_test=str(py_test),
        primary_symbol=None,
        primary_file=ts_primary,
        query="invoice total",
    )

    assert plan == []
    assert alignment["primary_target_language"] == "typescript"
    assert alignment["status"] == "mismatch-filtered"
    assert alignment["filtered_count"] >= 1


def test_validation_alignment_filters_javascript_commands_for_python_primary_file(
    tmp_path,
):
    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"
    src_dir.mkdir(parents=True)
    tests_dir.mkdir()
    python_primary = src_dir / "payments.py"
    python_primary.write_text("TAX_RATE = 0.08\n", encoding="utf-8")
    ts_test = tests_dir / "payments.test.ts"
    ts_test.write_text(
        'import { test } from "vitest";\ntest("invoice tax", () => {\n  expect(1).toBe(1);\n});\n',
        encoding="utf-8",
    )
    (project / "package.json").write_text(
        json.dumps({"devDependencies": {"vitest": "^1.0.0"}}),
        encoding="utf-8",
    )

    plan, alignment = repo_map._validation_plan_and_alignment_for_tests(
        [str(ts_test)],
        repo_root=project,
        primary_test=str(ts_test),
        primary_symbol=None,
        primary_file=python_primary,
        query="python invoice tax",
    )

    assert plan == []
    assert alignment["primary_target_language"] == "python"
    assert alignment["status"] == "mismatch-filtered"
    assert alignment["filtered_count"] >= 1
    assert any("vitest" in issue for issue in alignment["issues"])


def test_agent_capsule_filters_pytest_only_validation_for_typescript_primary(
    monkeypatch,
    tmp_path,
):
    project = tmp_path / "project"
    project.mkdir()
    ts_path = project / "src" / "app.ts"
    ts_path.parent.mkdir()
    ts_path.write_text(
        "export function createInvoice(subtotal: number): number {\n  return subtotal;\n}\n",
        encoding="utf-8",
    )
    test_path = project / "tests" / "test_payments.py"
    test_path.parent.mkdir()
    test_path.write_text("def test_create_invoice():\n    assert True\n", encoding="utf-8")

    def _fake_context_render(*_args, **_kwargs):
        return {
            "navigation_pack": {
                "primary_target": {
                    "file": str(ts_path),
                    "symbol": "createInvoice",
                    "kind": "function",
                    "start_line": 1,
                    "end_line": 3,
                },
                "follow_up_reads": [],
                "validation_commands": [f"uv run pytest {test_path} -q"],
            },
            "edit_plan_seed": {
                "primary_file": str(ts_path),
                "primary_symbol": {"name": "createInvoice", "kind": "function"},
                "primary_span": {"start_line": 1, "end_line": 3},
                "confidence": {"overall": 0.94},
                "validation_plan": [
                    {
                        "command": f"uv run pytest {test_path} -q",
                        "scope": "file",
                        "runner": "pytest",
                        "target": str(test_path),
                        "confidence": 0.82,
                        "detection": "detected",
                    }
                ],
                "validation_commands": [f"uv run pytest {test_path} -q"],
                "edit_ordering": [str(ts_path)],
            },
            "validation_commands": [f"uv run pytest {test_path} -q"],
            "sources": [
                {
                    "file": str(ts_path),
                    "symbol": "createInvoice",
                    "start_line": 1,
                    "end_line": 3,
                    "source": ts_path.read_text(encoding="utf-8"),
                }
            ],
            "context_consistency": {"primary_file_included": True},
        }

    monkeypatch.setattr(agent_capsule.repo_map, "build_context_render", _fake_context_render)

    payload = agent_capsule.build_agent_capsule(
        "createInvoice tax calculation",
        project,
        max_tokens=400,
    )

    assert payload["primary_target"]["file"] == str(ts_path)
    assert payload["validation_plan"] == []
    assert payload["validation_commands"] == []
    assert payload["context_consistency"]["validation_alignment"]["status"] == "mismatch-filtered"
    assert payload["context_consistency"]["validation_filtered_count"] == 1
    assert payload["confidence"]["overall"] <= 0.65
    assert payload["primary_target"]["confidence"] <= 0.65
    assert payload["ask_user_before_editing"]["required"] is True
    assert any("validation" in reason for reason in payload["ask_user_before_editing"]["reasons"])


def test_agent_capsule_json_preserves_original_line_map_after_compaction(tmp_path):
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    module_path = src_dir / "payments.py"
    module_path.write_text(
        "def create_invoice(total, tax):\n"
        "    # bookkeeping noise\n"
        "    subtotal = total + tax\n"
        "    return subtotal\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        app,
        [
            "agent",
            "--query",
            "change invoice tax calculation",
            "--json",
            str(project),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    snippet = next(
        item for item in payload["snippets"] if item["file"] == str(module_path.resolve())
    )
    assert "# bookkeeping noise" not in snippet["source"]
    assert "subtotal = total + tax" in snippet["source"]
    assert snippet["line_map"] == [
        {"line": 1, "text": "def create_invoice(total, tax):"},
        {"line": 3, "text": "    subtotal = total + tax"},
        {"line": 4, "text": "    return subtotal"},
    ]


def test_agent_capsule_json_reports_omissions_and_follow_up_reads_when_budget_is_tight(
    tmp_path,
):
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    for index in range(3):
        (src_dir / f"invoice_{index}.py").write_text(
            f"def create_invoice_{index}(total, tax):\n"
            f"    subtotal = total + tax + {index}\n"
            "    return subtotal\n",
            encoding="utf-8",
        )

    result = CliRunner().invoke(
        app,
        [
            "agent",
            "--query",
            "invoice",
            "--max-tokens",
            "40",
            "--max-files",
            "3",
            "--json",
            str(project),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["omissions"]["omitted_section_count"] >= 1
    assert payload["omissions"]["follow_up_reads"]
    assert all(
        "tg source" in item["command"] or "tg context-render" in item["command"]
        for item in payload["omissions"]["follow_up_reads"]
    )
    assert all("argv" in item for item in payload["omissions"]["follow_up_reads"])
    assert payload["confidence"]["overall"] < 0.95


def test_agent_capsule_json_emits_argv_safe_recovery_commands_for_spaced_paths(tmp_path):
    project = tmp_path / "project with spaces"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    module_path = src_dir / "payments.py"
    module_path.write_text(
        "def create_invoice(total, tax):\n    subtotal = total + tax\n    return subtotal\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        app,
        [
            "agent",
            "--query",
            'change invoice "tax" calculation',
            "--max-tokens",
            "1",
            "--json",
            str(project),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    raw_ref = payload["raw_context_ref"]
    assert raw_ref["argv"] == [
        "tg",
        "context-render",
        "--query",
        'change invoice "tax" calculation',
        "--json",
        str(project.resolve()),
        "--max-files",
        "3",
        "--max-sources",
        "5",
        "--max-tokens",
        "1",
        "--max-repo-files",
        "512",
    ]
    assert f'"{project.resolve()}"' in raw_ref["command"]
    rollback = payload["rollback"]
    assert rollback["argv"] == ["tg", "checkpoint", "create", str(project.resolve())]
    assert f'"{project.resolve()}"' in rollback["command"]
    follow_up_reads = payload["omissions"]["follow_up_reads"]
    assert follow_up_reads
    assert any(read["argv"][-1] == str(project.resolve()) for read in follow_up_reads)
    assert any(f'"{project.resolve()}"' in read["command"] for read in follow_up_reads)


def test_agent_capsule_json_requires_user_confirmation_without_validation_commands(tmp_path):
    src_dir = tmp_path / "standalone"
    src_dir.mkdir()
    (src_dir / "helper.py").write_text(
        "def update_helper(value):\n    return value.strip()\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        app,
        [
            "agent",
            "--query",
            "update standalone helper",
            "--json",
            str(src_dir),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["validation_commands"] == []
    assert payload["validation_plan"] == []
    assert payload["ask_user_before_editing"]["required"] is True
    assert "no validation command evidence" in payload["ask_user_before_editing"]["reasons"]


def test_agent_capsule_json_reports_primary_consistency_and_downgrades_when_primary_is_omitted(
    tmp_path,
):
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    primary_path = src_dir / "invoice.py"
    primary_path.write_text(
        "def create_invoice(total, tax):\n"
        "    subtotal = total\n"
        + "".join(f"    subtotal = subtotal + tax + {index}\n" for index in range(80))
        + "    return subtotal\n",
        encoding="utf-8",
    )
    secondary_path = src_dir / "related.py"
    secondary_path.write_text(
        'def invoice_note():\n    return "invoice"\n',
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        app,
        [
            "agent",
            "--query",
            "create invoice tax",
            "--max-tokens",
            "12",
            "--max-files",
            "2",
            "--json",
            str(project),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert "context_consistency" in payload
    assert payload["primary_target"]["file"] == str(primary_path.resolve())
    assert payload["context_consistency"]["primary_file"] == payload["primary_target"]["file"]
    assert payload["snippets"]
    assert str(primary_path.resolve()) not in {snippet["file"] for snippet in payload["snippets"]}
    assert str(secondary_path.resolve()) in {snippet["file"] for snippet in payload["snippets"]}
    assert payload["context_consistency"]["capsule_primary_file_in_snippets"] is False
    assert payload["context_consistency"]["capsule_primary_file_in_follow_up_reads"] is True
    assert payload["context_consistency"]["capsule_primary_file_omitted"] is True
    assert payload["confidence"]["overall"] < 0.75
    assert (
        "primary file omitted from capsule snippets by token budget"
        in payload["confidence"]["downgrade_reasons"]
    )
    assert payload["ask_user_before_editing"]["required"] is True
    assert (
        "primary file omitted from capsule snippets"
        in payload["ask_user_before_editing"]["reasons"]
    )


def test_agent_capsule_text_summary_names_primary_target_and_validation(tmp_path):
    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"
    src_dir.mkdir(parents=True)
    tests_dir.mkdir()
    (src_dir / "payments.py").write_text(
        "def create_invoice(total, tax):\n    subtotal = total + tax\n    return subtotal\n",
        encoding="utf-8",
    )
    (tests_dir / "test_payments.py").write_text(
        "from src.payments import create_invoice\n\n"
        "def test_create_invoice():\n"
        "    assert create_invoice(10, 2) == 12\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        app,
        ["agent", "--query", "change invoice tax calculation", str(project)],
    )

    assert result.exit_code == 0, result.output
    assert "Agent capsule for" in result.stdout
    assert "primary=" in result.stdout
    assert "validation=" in result.stdout
    assert "confidence=" in result.stdout


def test_build_context_render_can_skip_edit_plan_seed_for_bounded_roots(monkeypatch, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    module_path = project / "hybrid-search.cjs"
    module_path.write_text(
        "function HybridSearch() {\n  return 'ok';\n}\n",
        encoding="utf-8",
    )
    seen: dict[str, bool] = {"called": False}

    def _unexpected_attach(*args, **kwargs):
        seen["called"] = True
        raise AssertionError("_attach_edit_plan_metadata should be skipped for bounded roots")

    monkeypatch.setattr(repo_map, "_attach_edit_plan_metadata", _unexpected_attach)

    payload = repo_map.build_context_render(
        "hybrid search",
        project,
        max_repo_files=25,
        include_edit_plan_seed=False,
    )

    assert payload["routing_reason"] == "context-render"
    assert payload["edit_plan_seed"] == {}
    assert payload["edit_plan_seed_skipped"] is True
    assert payload["navigation_pack"]["primary_target"]["file"] == str(module_path.resolve())
    assert payload["navigation_pack"]["validation_commands"] == []
    assert seen["called"] is False


def test_build_context_render_full_seed_reuses_bounded_repo_map(monkeypatch, tmp_path):
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    module_path = src_dir / "payments.py"
    module_path.write_text("def create_invoice(total):\n    return total + 1\n", encoding="utf-8")
    service_path = src_dir / "service.py"
    service_path.write_text(
        "from src.payments import create_invoice\n"
        "\n"
        "def build_receipt(total):\n"
        "    return create_invoice(total)\n",
        encoding="utf-8",
    )
    (src_dir / "z_outside_cap.py").write_text(
        "from src.payments import create_invoice\n"
        "\n"
        "def outside_receipt(total):\n"
        "    return create_invoice(total)\n",
        encoding="utf-8",
    )
    original_iter_repo_files = repo_map._iter_repo_files
    unbounded_walks = 0

    def _bounded_iter_guard(root, **kwargs):
        nonlocal unbounded_walks
        if kwargs.get("max_files") is None:
            unbounded_walks += 1
            raise AssertionError("bounded context-render must not recrawl the physical repo")
        return original_iter_repo_files(root, **kwargs)

    monkeypatch.setattr(repo_map, "_iter_repo_files", _bounded_iter_guard)

    payload = repo_map.build_context_render(
        "create invoice",
        project,
        max_repo_files=2,
        include_edit_plan_seed=True,
        max_files=2,
    )

    assert payload["edit_plan_seed"]["primary_file"] == str(module_path.resolve())
    assert payload["edit_plan_seed"]["dependent_files"] == [str(service_path.resolve())]
    assert unbounded_walks == 0


def test_build_context_edit_plan_uses_bounded_repo_map(monkeypatch, tmp_path):
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    module_path = src_dir / "payments.py"
    module_path.write_text("def create_invoice(total):\n    return total + 1\n", encoding="utf-8")
    service_path = src_dir / "service.py"
    service_path.write_text(
        "from src.payments import create_invoice\n"
        "\n"
        "def build_receipt(total):\n"
        "    return create_invoice(total)\n",
        encoding="utf-8",
    )
    (src_dir / "z_outside_cap.py").write_text(
        "from src.payments import create_invoice\n"
        "\n"
        "def outside_receipt(total):\n"
        "    return create_invoice(total)\n",
        encoding="utf-8",
    )
    original_iter_repo_files = repo_map._iter_repo_files
    unbounded_walks = 0

    def _bounded_iter_guard(root, **kwargs):
        nonlocal unbounded_walks
        if kwargs.get("max_files") is None:
            unbounded_walks += 1
            raise AssertionError("bounded edit-plan must not recrawl the physical repo")
        return original_iter_repo_files(root, **kwargs)

    monkeypatch.setattr(repo_map, "_iter_repo_files", _bounded_iter_guard)

    payload = repo_map.build_context_edit_plan(
        "create invoice",
        project,
        max_repo_files=2,
        max_files=2,
    )

    assert payload["edit_plan_seed"]["primary_file"] == str(module_path.resolve())
    assert payload["edit_plan_seed"]["dependent_files"] == [str(service_path.resolve())]
    assert payload["scan_limit"]["max_repo_files"] == 2
    assert unbounded_walks == 0


def test_build_context_edit_plan_caps_file_summary_symbols(tmp_path):
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    module_path = src_dir / "payments.py"
    module_path.write_text(
        "\n".join([
            "def create_invoice(total):",
            "    return total + 1",
            "",
            "def invoice_tax(total):",
            "    return total * 0.1",
            "",
            "def invoice_discount(total):",
            "    return total - 1",
            "",
            "def invoice_receipt(total):",
            "    return str(total)",
            "",
        ]),
        encoding="utf-8",
    )

    payload = repo_map.build_context_edit_plan(
        "invoice",
        project,
        max_files=1,
        max_symbols=2,
        max_sources=1,
    )

    assert payload["file_summaries"][0]["path"] == str(module_path.resolve())
    assert len(payload["file_summaries"][0]["symbols"]) <= 2


def test_context_render_skips_blast_radius_for_low_confidence_fuzzy_symbol(
    monkeypatch, tmp_path: Path
):
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    module_path = src_dir / "session_store.py"
    module_path.write_text(
        "def _empty_changeset():\n    return {'added': [], 'modified': [], 'removed': []}\n",
        encoding="utf-8",
    )

    def _unexpected_blast_radius(*_args, **_kwargs):
        raise AssertionError("low-confidence fuzzy symbols should not build blast radius")

    monkeypatch.setattr(
        repo_map,
        "build_symbol_blast_radius_from_map",
        _unexpected_blast_radius,
    )

    payload = repo_map.build_context_render(
        "change invoice tax calculation",
        project,
        include_edit_plan_seed=True,
    )

    assert payload["edit_plan_seed"]["primary_file"] == str(module_path.resolve())
    assert payload["edit_plan_seed"]["primary_symbol"]["name"] == "_empty_changeset"
    assert payload["edit_plan_seed"]["dependent_files"] == []


def test_context_render_json_reports_bounded_repo_scan(tmp_path):
    runner = CliRunner()
    project = tmp_path / "project"
    project.mkdir()
    (project / "first.py").write_text("def first():\n    return 1\n", encoding="utf-8")
    (project / "second.py").write_text("def second():\n    return 2\n", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "context-render",
            "--query",
            "first",
            "--max-repo-files",
            "1",
            "--json",
            str(project),
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["scan_limit"] == {
        "max_repo_files": 1,
        "scanned_files": 1,
        "possibly_truncated": True,
    }


def test_context_render_json_includes_markdown_file_sources(tmp_path):
    runner = CliRunner()
    docs = tmp_path / "docs"
    docs.mkdir()
    guide_path = docs / "routing_policy.md"
    guide_path.write_text(
        "# Routing Policy\n\n"
        "The routing policy explains how tensor-grep chooses native CPU, rg, and GPU paths.\n",
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "context-render",
            "--query",
            "routing policy native GPU",
            "--json",
            str(docs),
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ranking_quality"] == "strong"
    assert payload["sources"][0]["kind"] == "file"
    assert payload["sources"][0]["file"] == str(guide_path.resolve())
    assert "Routing Policy" in payload["rendered_context"]


def test_context_render_llm_profile_omits_full_inventories(tmp_path):
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    for index in range(8):
        (src_dir / f"worker_{index}.py").write_text(
            f"def create_invoice_{index}(total):\n"
            f"    subtotal = total + {index}\n"
            "    return subtotal\n",
            encoding="utf-8",
        )

    payload = repo_map.build_context_render(
        "create invoice",
        project,
        max_files=2,
        max_sources=2,
        optimize_context=True,
        render_profile="llm",
    )

    assert payload["render_profile"] == "llm"
    assert payload["context_payload_profile"] == "llm-compact"
    assert "symbols" not in payload
    assert "imports" not in payload
    assert "related_paths" not in payload
    assert "candidate_edit_targets" not in payload
    assert "file_matches" not in payload
    assert "file_summaries" not in payload
    assert "test_matches" not in payload
    assert all("source" not in source for source in payload["sources"])
    assert all("rendered_source" in source for source in payload["sources"])
    assert payload["navigation_pack"]["primary_target"]["file"] in payload["files"]


def test_context_render_llm_profile_compacts_agent_metadata(tmp_path):
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    target_path = src_dir / "target.py"
    target_path.write_text(
        "def create_invoice(total):\n    return total + 1\n",
        encoding="utf-8",
    )
    for index in range(12):
        (src_dir / f"caller_{index}.py").write_text(
            "from src.target import create_invoice\n\n"
            f"def caller_{index}(total):\n"
            "    return create_invoice(total)\n",
            encoding="utf-8",
        )

    payload = repo_map.build_context_render(
        "create invoice",
        project,
        max_files=1,
        max_sources=1,
        max_render_chars=1200,
        optimize_context=True,
        render_profile="llm",
    )

    assert payload["context_payload_profile"] == "llm-compact"
    assert "validation_commands" in payload
    assert payload["validation_commands"] == payload["navigation_pack"]["validation_commands"]
    assert len(payload["edit_plan_seed"]["edit_ordering"]) <= 2
    assert len(payload["navigation_pack"]["edit_ordering"]) <= 2
    assert len(payload["edit_plan_seed"]["related_spans"]) <= 1
    assert len(payload["edit_plan_seed"]["suggested_edits"]) <= 1
    assert len(json.dumps(payload)) < 9_000


def test_context_render_json_defaults_to_agent_compact_payload(tmp_path):
    runner = CliRunner()
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    (src_dir / "target.py").write_text(
        "def create_invoice(total):\n    return total + 1\n",
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "context-render",
            "--query",
            "create invoice",
            "--max-files",
            "1",
            "--max-sources",
            "1",
            "--max-render-chars",
            "1200",
            "--json",
            str(project),
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["render_profile"] == "llm"
    assert payload["context_payload_profile"] == "llm-compact"
    assert "source" not in payload["sources"][0]
    assert "validation_commands" in payload


def test_context_render_json_llm_profile_uses_compact_wire_format(tmp_path):
    runner = CliRunner()
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    (src_dir / "target.py").write_text(
        "def create_invoice(total):\n    return total + 1\n",
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "context-render",
            "--query",
            "create invoice",
            "--json",
            str(project),
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["render_profile"] == "llm"
    assert '\n  "' not in result.stdout
    assert len(result.stdout) < len(json.dumps(payload, indent=2))


def test_context_render_profile_exposes_public_profile_metadata(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "target.py").write_text(
        "def create_invoice(total):\n    return total + 1\n",
        encoding="utf-8",
    )

    payload = repo_map.build_context_render(
        "create invoice",
        project,
        max_files=1,
        max_sources=1,
        render_profile="llm",
        profile=True,
    )

    assert "profile" in payload
    assert payload["profile"]["enabled"] is True
    assert payload["profile"]["total_elapsed_s"] >= 0
    assert payload["_profiling"]["total_elapsed_s"] == payload["profile"]["total_elapsed_s"]


def test_blast_radius_json_supports_output_limits(tmp_path):
    runner = CliRunner()
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    (src_dir / "target.py").write_text(
        "def create_invoice(total):\n    return total + 1\n"
        + "\n".join(f"def helper_{index}():\n    return {index}\n" for index in range(12)),
        encoding="utf-8",
    )
    for index in range(6):
        (src_dir / f"caller_{index}.py").write_text(
            "from src.target import create_invoice\n\n"
            f"def caller_{index}(total):\n"
            "    return create_invoice(total)\n",
            encoding="utf-8",
        )

    result = runner.invoke(
        app,
        [
            "blast-radius",
            "--symbol",
            "create_invoice",
            "--max-callers",
            "2",
            "--max-files",
            "2",
            "--json",
            str(project),
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert len(payload["callers"]) <= 2
    assert len(payload["files"]) <= 2
    assert len(payload["file_matches"]) <= 2
    assert all(len(level.get("files", [])) <= 2 for level in payload["caller_tree"])
    assert all(
        path in payload["files"]
        for level in payload["caller_tree"]
        for path in level.get("files", [])
    )
    assert all(len(summary.get("symbols", [])) <= 3 for summary in payload["file_summaries"])
    assert all(path in payload["rendered_caller_tree"] for path in payload["files"])
    assert payload["output_limit"] == {
        "max_callers": 2,
        "max_files": 2,
        "callers_truncated": True,
        "files_truncated": True,
        "total_callers": 6,
        "returned_callers": 2,
        "omitted_callers": 4,
        "total_files": 7,
        "returned_files": 2,
        "omitted_files": 5,
    }


def test_blast_radius_json_defaults_to_bounded_agent_output(tmp_path):
    runner = CliRunner()
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    (src_dir / "target.py").write_text(
        "def safe_parse_json(value):\n    return value\n",
        encoding="utf-8",
    )
    for index in range(60):
        (src_dir / f"caller_{index:02}.py").write_text(
            "from src.target import safe_parse_json\n\n"
            f"def caller_{index}(value):\n"
            "    return safe_parse_json(value)\n",
            encoding="utf-8",
        )

    result = runner.invoke(
        app,
        [
            "blast-radius",
            "--symbol",
            "safe_parse_json",
            "--json",
            str(project),
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert len(payload["callers"]) <= 25
    assert len(payload["files"]) <= 25
    assert payload["output_limit"]["max_callers"] == 25
    assert payload["output_limit"]["max_files"] == 25
    assert payload["output_limit"]["total_callers"] == 60
    assert payload["output_limit"]["omitted_callers"] == 35
    assert len(result.stdout.encode("utf-8")) < 80_000


def test_blast_radius_caller_scan_prefilters_files_without_symbol_literal(
    monkeypatch,
    tmp_path,
):
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    target_path = src_dir / "target.py"
    target_path.write_text(
        "def safe_parse_json(value):\n    return value\n",
        encoding="utf-8",
    )
    caller_paths = []
    for index in range(3):
        caller_path = src_dir / f"caller_{index}.py"
        caller_path.write_text(
            "from src.target import safe_parse_json\n\n"
            f"def caller_{index}(value):\n"
            "    return safe_parse_json(value)\n",
            encoding="utf-8",
        )
        caller_paths.append(caller_path.resolve())
    for index in range(40):
        (src_dir / f"unrelated_{index}.py").write_text(
            f"def unrelated_{index}():\n    return {index}\n",
            encoding="utf-8",
        )

    scanned_python_files: list[Path] = []
    original_python_references_and_calls = repo_map._python_references_and_calls

    def _tracked_python_references_and_calls(path: Path, symbol: str):
        scanned_python_files.append(path.resolve())
        return original_python_references_and_calls(path, symbol)

    monkeypatch.setattr(
        repo_map,
        "_python_references_and_calls",
        _tracked_python_references_and_calls,
    )

    payload = repo_map.build_symbol_blast_radius(
        "safe_parse_json",
        project,
        max_repo_files=1000,
        max_callers=2,
        max_files=2,
    )

    allowed_scans = {target_path.resolve(), *caller_paths}
    assert set(scanned_python_files) <= allowed_scans
    assert len(payload["callers"]) <= 2
    assert payload["output_limit"]["callers_truncated"] is True


def test_commonjs_repo_map_extracts_exported_function_symbols(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    module_path = project / "worker.cjs"
    module_path.write_text(
        "const fs = require('fs');\n"
        "\n"
        "function prepareCursorWorkerInvocation(input) {\n"
        "  return input;\n"
        "}\n"
        "\n"
        "module.exports = {\n"
        "  prepareCursorWorkerInvocation,\n"
        "  safeParseJSON: function safeParseJSON(value) {\n"
        "    return JSON.parse(value);\n"
        "  },\n"
        "  runCursorWorker: async function runCursorWorker() {\n"
        "    return prepareCursorWorkerInvocation({});\n"
        "  },\n"
        "};\n"
        "\n"
        "exports.waitForHandoff = async function waitForHandoff() {\n"
        "  return true;\n"
        "};\n",
        encoding="utf-8",
    )

    payload = repo_map.build_repo_map(project)

    names = {str(symbol["name"]) for symbol in payload["symbols"]}
    assert {
        "prepareCursorWorkerInvocation",
        "safeParseJSON",
        "runCursorWorker",
        "waitForHandoff",
    } <= names
    assert not any(name.startswith(("module", "exports", "function")) for name in names)

    source_payload = repo_map.build_symbol_source("safeParseJSON", project)
    assert source_payload["sources"][0]["file"] == str(module_path.resolve())
    assert "return JSON.parse(value);" in source_payload["sources"][0]["source"]


def test_js_repo_map_uses_byte_safe_symbol_names(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    module_path = project / "unicode-prefix.mjs"
    module_path.write_text(
        "const label = 'ééé';\nclass Engine {\n  run() {\n    return label;\n  }\n}\n",
        encoding="utf-8",
    )

    payload = repo_map.build_repo_map(project)

    names = {str(symbol["name"]) for symbol in payload["symbols"]}
    assert "Engine" in names
    assert not any(" " in name or "{" in name or "\n" in name for name in names)


def test_test_only_repo_map_keeps_files_non_empty_for_agent_inventory(tmp_path):
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    test_path = tests_dir / "test_worker.py"
    test_path.write_text("def test_worker():\n    assert True\n", encoding="utf-8")

    payload = repo_map.build_repo_map(tests_dir)

    assert payload["files"] == [str(test_path.resolve())]
    assert payload["tests"] == [str(test_path.resolve())]
    assert payload["related_paths"] == [str(test_path.resolve())]


def test_iter_repo_files_does_not_resolve_every_child_file(monkeypatch, tmp_path):
    # L8/repo-map: the gitignore-aware walk matches paths AS WALKED against the
    # once-resolved root, so it must NOT call path.resolve() on every child — that would
    # be an O(files) stat/symlink syscall regression on large trees (~384k files on a
    # workspace root). Resolving the root itself is fine; resolving children is not.
    project = tmp_path / "project"
    project.mkdir()
    (project / "a.py").write_text("print('ok')\n", encoding="utf-8")
    expected = project.resolve() / "a.py"
    original_resolve = repo_map.Path.resolve

    def _guarded_resolve(self, *args, **kwargs):
        if self.name == "a.py":
            raise AssertionError("repo-map walk must not resolve() child files")
        return original_resolve(self, *args, **kwargs)

    monkeypatch.setattr(repo_map.Path, "resolve", _guarded_resolve)

    files = repo_map._iter_repo_files(project)

    assert files == [expected]


def test_repo_map_file_universe_does_not_resolve_child_files(monkeypatch, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    project_root = project.resolve()
    child_file = project_root / "a.py"
    child_file.write_text("print('ok')\n", encoding="utf-8")
    original_resolve = repo_map.Path.resolve

    def _guarded_resolve(self, *args, **kwargs):
        if self.name == "a.py":
            raise AssertionError("repo-map child paths should preserve map identity")
        return original_resolve(self, *args, **kwargs)

    monkeypatch.setattr(repo_map.Path, "resolve", _guarded_resolve)

    files = repo_map._repo_map_file_universe({
        "path": str(project_root),
        "files": [str(child_file)],
        "tests": [],
    })

    assert files == [child_file]


def test_detect_validation_runners_caps_repo_scan(monkeypatch, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    seen: dict[str, object] = {}
    original_iter_repo_files = repo_map._iter_repo_files

    def _wrapped_iter(root, **kwargs):
        seen["max_files"] = kwargs.get("max_files")
        return original_iter_repo_files(root, **kwargs)

    monkeypatch.setattr(repo_map, "_iter_repo_files", _wrapped_iter)

    repo_map._detect_validation_runners.cache_clear()
    try:
        repo_map._detect_validation_runners(str(project))
    finally:
        repo_map._detect_validation_runners.cache_clear()

    assert seen["max_files"] == repo_map._VALIDATION_RUNNER_SCAN_LIMIT


def test_edit_plan_json_returns_machine_readable_plan_bundle(tmp_path):
    runner = CliRunner()
    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"
    src_dir.mkdir(parents=True)
    tests_dir.mkdir()

    module_path = src_dir / "payments.py"
    module_path.write_text(
        "def create_invoice(total, tax):\n    return total + tax\n",
        encoding="utf-8",
    )
    test_path = tests_dir / "test_payments.py"
    test_path.write_text(
        "from src.payments import create_invoice\n\n"
        "def test_create_invoice():\n"
        "    assert create_invoice(1, 2) == 3\n",
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        ["edit-plan", "--query", "create invoice", "--json", str(project)],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["routing_reason"] == "context-edit-plan"
    assert payload["scan_limit"]["max_repo_files"] == 512
    assert "rendered_context" not in payload
    assert "sources" not in payload
    assert payload["candidate_edit_targets"]["files"][0] == str(module_path.resolve())
    assert payload["candidate_edit_targets"]["spans"][0]["file"] == str(module_path.resolve())
    assert payload["candidate_edit_targets"]["spans"][0]["symbol"] == "create_invoice"
    assert payload["candidate_edit_targets"]["spans"][0]["depth"] == 0
    assert payload["primary_target"] == payload["navigation_pack"]["primary_target"]
    assert payload["primary_target"]["file"] == str(module_path.resolve())
    assert payload["edit_order"] == payload["edit_plan_seed"]["edit_ordering"]
    assert payload["plan"]["primary_file"] == str(module_path.resolve())
    assert payload["plan"]["primary_symbol"]["name"] == "create_invoice"
    assert payload["plan"]["edit_order"] == payload["edit_order"]
    assert "rendered_context" not in payload["plan"]
    assert "sources" not in payload["plan"]
    _assert_enriched_edit_plan_seed(
        payload["edit_plan_seed"],
        primary_file=module_path,
        primary_symbol_name="create_invoice",
    )
    _assert_navigation_pack(
        payload["navigation_pack"],
        primary_file=module_path,
        primary_symbol_name="create_invoice",
    )
    assert "validation_commands" in payload
    assert payload["validation_commands"] == payload["navigation_pack"]["validation_commands"]
    assert payload["validation_commands"] == payload["edit_plan_seed"]["validation_commands"]


def test_edit_plan_json_accepts_agent_budget_flags(tmp_path):
    runner = CliRunner()
    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"
    src_dir.mkdir(parents=True)
    tests_dir.mkdir()

    module_path = src_dir / "payments.py"
    module_path.write_text(
        "def create_invoice(total, tax):\n    return total + tax\n",
        encoding="utf-8",
    )
    (tests_dir / "test_payments.py").write_text(
        "from src.payments import create_invoice\n\n"
        "def test_create_invoice():\n"
        "    assert create_invoice(1, 2) == 3\n",
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "edit-plan",
            "--query",
            "create invoice",
            "--max-files",
            "2",
            "--max-repo-files",
            "2",
            "--max-sources",
            "1",
            "--max-tokens",
            "64",
            "--json",
            str(project),
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["max_files"] == 2
    assert payload["scan_limit"]["max_repo_files"] == 2
    assert payload["max_sources"] == 1
    assert payload["max_tokens"] == 64
    assert "rendered_context" not in payload
    assert "sources" not in payload
    assert len(payload["edit_plan_seed"]["related_spans"]) <= 1
    assert len(payload["edit_plan_seed"]["suggested_edits"]) <= 1
    assert payload["edit_plan_seed"]["primary_file"] == str(module_path.resolve())


def test_blast_radius_render_json_returns_prompt_ready_radius_bundle(tmp_path):
    runner = CliRunner()
    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"
    src_dir.mkdir(parents=True)
    tests_dir.mkdir()

    module_path = src_dir / "payments.py"
    module_path.write_text("def create_invoice(total):\n    return total + 1\n", encoding="utf-8")
    service_path = src_dir / "service.py"
    service_path.write_text(
        "from src.payments import create_invoice\n\n"
        "def build_invoice(total):\n"
        "    return create_invoice(total)\n",
        encoding="utf-8",
    )
    test_path = tests_dir / "test_service.py"
    test_path.write_text(
        "from src.service import build_invoice\n\n"
        "def test_build_invoice():\n"
        "    assert build_invoice(2) == 3\n",
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "blast-radius-render",
            "--symbol",
            "create_invoice",
            "--max-depth",
            "1",
            "--max-render-chars",
            "400",
            "--json",
            str(project),
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["routing_reason"] == "symbol-blast-radius-render"
    assert payload["symbol"] == "create_invoice"
    assert payload["max_depth"] == 1
    assert payload["sources"][0]["name"] == "create_invoice"
    assert any(section["kind"] == "source" for section in payload["sections"])
    assert payload["edit_plan_seed"]["primary_test"] == str(test_path.resolve())
    _assert_enriched_edit_plan_seed(
        payload["edit_plan_seed"],
        primary_file=module_path,
        primary_symbol_name="create_invoice",
    )
    assert str(module_path.resolve()) in payload["rendered_context"]
    assert "create_invoice" in payload["rendered_context"]


def test_blast_radius_plan_json_returns_machine_readable_radius_bundle(tmp_path):
    runner = CliRunner()
    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"
    src_dir.mkdir(parents=True)
    tests_dir.mkdir()

    module_path = src_dir / "payments.py"
    module_path.write_text("def create_invoice(total):\n    return total + 1\n", encoding="utf-8")
    service_path = src_dir / "service.py"
    service_path.write_text(
        "from src.payments import create_invoice\n\n"
        "def build_invoice(total):\n"
        "    return create_invoice(total)\n",
        encoding="utf-8",
    )
    test_path = tests_dir / "test_service.py"
    test_path.write_text(
        "from src.service import build_invoice\n\n"
        "def test_build_invoice():\n"
        "    assert build_invoice(2) == 3\n",
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "blast-radius-plan",
            "--symbol",
            "create_invoice",
            "--max-depth",
            "1",
            "--json",
            str(project),
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["routing_reason"] == "symbol-blast-radius-plan"
    assert "rendered_context" not in payload
    assert "sources" not in payload
    assert payload["edit_plan_seed"]["primary_test"] == str(test_path.resolve())
    assert payload["candidate_edit_targets"]["spans"][0]["file"] == str(module_path.resolve())
    assert payload["candidate_edit_targets"]["spans"][0]["symbol"] == "create_invoice"
    _assert_enriched_edit_plan_seed(
        payload["edit_plan_seed"],
        primary_file=module_path,
        primary_symbol_name="create_invoice",
    )


def test_edit_plan_json_prefers_targeted_vitest_validation_commands(tmp_path):
    runner = CliRunner()
    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"
    src_dir.mkdir(parents=True)
    tests_dir.mkdir()

    (project / "package.json").write_text(
        json.dumps({
            "name": "vitest-project",
            "devDependencies": {"vitest": "^1.0.0"},
        }),
        encoding="utf-8",
    )
    module_path = src_dir / "payments.ts"
    module_path.write_text(
        "export function createInvoice(total: number, tax: number): number {\n"
        "  return total + tax;\n"
        "}\n",
        encoding="utf-8",
    )
    test_path = tests_dir / "payments.test.ts"
    test_path.write_text(
        'import { describe, expect, test } from "vitest";\n'
        'import { createInvoice } from "../src/payments";\n\n'
        'describe("payments", () => {\n'
        '  test("createInvoice adds tax", () => {\n'
        "    expect(createInvoice(1, 2)).toBe(3);\n"
        "  });\n"
        "});\n",
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "edit-plan",
            "--query",
            "create invoice",
            "--json",
            str(project),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)

    assert payload["edit_plan_seed"]["validation_plan"][0]["runner"] == "vitest"
    assert payload["edit_plan_seed"]["validation_plan"][0]["scope"] == "symbol"
    assert payload["edit_plan_seed"]["validation_plan"][0]["command"] == (
        'npx vitest run tests/payments.test.ts -t "createInvoice adds tax"'
    )
    assert payload["edit_plan_seed"]["validation_commands"][0] == (
        'npx vitest run tests/payments.test.ts -t "createInvoice adds tax"'
    )


def test_edit_plan_json_prefers_ancestor_package_script_for_nested_ts_subdir(tmp_path):
    runner = CliRunner()
    project = tmp_path / "project"
    package_root = project / "packages" / "core"
    nested_src_dir = package_root / "src" / "tools"
    tests_dir = package_root / "tests"
    nested_src_dir.mkdir(parents=True)
    tests_dir.mkdir(parents=True)

    (package_root / "package.json").write_text(
        json.dumps({
            "name": "nested-vitest-project",
            "devDependencies": {"vitest": "^1.0.0"},
            "scripts": {"test": "vitest run"},
        }),
        encoding="utf-8",
    )
    module_path = nested_src_dir / "glob.ts"
    module_path.write_text(
        "export function createGlobMatcher(pattern: string): string {\n  return pattern;\n}\n",
        encoding="utf-8",
    )
    (tests_dir / "glob.test.ts").write_text(
        'import { describe, expect, test } from "vitest";\n'
        'import { createGlobMatcher } from "../src/tools/glob";\n\n'
        'describe("glob", () => {\n'
        '  test("createGlobMatcher returns the input pattern", () => {\n'
        '    expect(createGlobMatcher("*.ts")).toBe("*.ts");\n'
        "  });\n"
        "});\n",
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "edit-plan",
            "--query",
            "create glob matcher",
            "--json",
            str(nested_src_dir),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["edit_plan_seed"]["validation_plan"][0]["runner"] == "javascript"
    assert payload["edit_plan_seed"]["validation_plan"][0]["scope"] == "repo"
    assert payload["edit_plan_seed"]["validation_commands"][0] == "npm test"


def test_edit_plan_json_omits_js_fallback_for_manifest_free_tsx_subdir(tmp_path):
    runner = CliRunner()
    project = tmp_path / "project"
    src_dir = project / "src" / "components" / "permissions"
    src_dir.mkdir(parents=True)
    module_path = src_dir / "FileWriteToolDiff.tsx"
    module_path.write_text(
        'export function FileWriteToolDiff(): string {\n  return "diff";\n}\n',
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "edit-plan",
            "--query",
            "file write diff",
            "--json",
            str(src_dir),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["edit_plan_seed"]["validation_commands"] == []
    assert payload["edit_plan_seed"]["validation_plan"] == []


def test_edit_plan_json_does_not_escape_manifest_free_repo_boundary(tmp_path):
    runner = CliRunner()
    outer_root = tmp_path / "outer"
    external_root = outer_root / "copied-agent"
    src_dir = external_root / "src" / "components" / "permissions"
    src_dir.mkdir(parents=True)
    (outer_root / "pyproject.toml").write_text(
        "[project]\nname = 'outer'\nversion = '0.1.0'\n", encoding="utf-8"
    )
    (external_root / "README.md").write_text("# copied agent\n", encoding="utf-8")
    (external_root / ".gitignore").write_text("node_modules/\n", encoding="utf-8")
    module_path = src_dir / "FileWriteToolDiff.tsx"
    module_path.write_text(
        "export function FileWriteToolDiff(): string {\n"
        '  return "file write diff read before write token budget";\n'
        "}\n",
        encoding="utf-8",
    )
    sibling_path = src_dir / "FileWritePermissionRequest.tsx"
    sibling_path.write_text(
        "export function FileWritePermissionRequest(): string {\n"
        '  return "file write permission request read before write token budget";\n'
        "}\n",
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "edit-plan",
            "--query",
            "file write diff read before write token budget",
            "--json",
            str(src_dir),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["edit_plan_seed"]["validation_commands"] == []
    assert payload["edit_plan_seed"]["validation_plan"] == []


def test_edit_plan_json_prefers_js_repo_fallback_over_pytest_for_mixed_repo_without_tests(tmp_path):
    runner = CliRunner()
    project = tmp_path / "project"
    cli_dir = project / ".claude" / "tools" / "cli"
    cli_dir.mkdir(parents=True)
    (project / "package.json").write_text(
        json.dumps({
            "name": "agent-studio-like",
            "packageManager": "pnpm@10.0.0",
            "scripts": {"test": "pnpm test"},
        }),
        encoding="utf-8",
    )
    (project / "scripts").mkdir()
    (project / "scripts" / "helper.py").write_text(
        "def helper():\n    return True\n", encoding="utf-8"
    )
    module_path = cli_dir / "hybrid-search.cjs"
    module_path.write_text(
        "function supportsDaemonCommand(command) {\n"
        "  return command !== '--help' && command !== '-h';\n"
        "}\n"
        "function shouldUseDaemon(command) {\n"
        "  if (!supportsDaemonCommand(command)) return false;\n"
        "  return true;\n"
        "}\n",
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "edit-plan",
            "--query",
            "hybrid search daemon command",
            "--json",
            str(cli_dir),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["edit_plan_seed"]["validation_commands"][0] == "pnpm test"
    assert "uv run pytest -q" not in payload["edit_plan_seed"]["validation_commands"]


def test_navigation_pack_prefetches_single_same_directory_related_read_into_primary_phase(tmp_path):
    from tensor_grep.cli import repo_map

    src_dir = tmp_path / "src" / "components" / "permissions"
    src_dir.mkdir(parents=True)
    module_path = src_dir / "FileWriteToolDiff.tsx"
    sibling_path = src_dir / "FileWritePermissionRequest.tsx"
    module_path.write_text(
        "export function FileWriteToolDiff(): string { return 'diff'; }\n", encoding="utf-8"
    )
    sibling_path.write_text(
        "export function FileWritePermissionRequest(): string { return 'request'; }\n",
        encoding="utf-8",
    )

    payload = {
        "edit_plan_seed": {
            "primary_file": str(module_path.resolve()),
            "primary_symbol": {"name": "FileWriteToolDiff"},
            "primary_span": {"start_line": 1, "end_line": 1},
            "reasons": ["primary-symbol"],
            "confidence": {"overall": 0.9},
            "validation_tests": [],
            "validation_commands": ["npm test"],
            "edit_ordering": [str(module_path.resolve()), str(sibling_path.resolve())],
            "rollback_risk": 0.2,
        },
        "candidate_edit_targets": {
            "spans": [
                {
                    "file": str(module_path.resolve()),
                    "symbol": "FileWriteToolDiff",
                    "start_line": 1,
                    "end_line": 1,
                    "rationale": "primary",
                },
                {
                    "file": str(sibling_path.resolve()),
                    "symbol": "FileWritePermissionRequest",
                    "start_line": 1,
                    "end_line": 1,
                    "rationale": "related",
                },
            ]
        },
    }

    navigation_pack = repo_map._navigation_pack({}, payload, max_reads=4)

    groups = navigation_pack["parallel_read_groups"]
    assert len(groups) == 1
    assert groups[0]["label"] == "primary"
    assert sorted(groups[0]["roles"]) == ["primary", "related"]
    assert str(module_path.resolve()) in groups[0]["files"]
    assert str(sibling_path.resolve()) in groups[0]["files"]


def test_navigation_pack_prefetches_same_directory_related_and_test_reads_into_primary_phase(
    tmp_path,
):
    from tensor_grep.cli import repo_map

    src_dir = tmp_path / "src" / "tools"
    src_dir.mkdir(parents=True)
    module_path = src_dir / "glob.ts"
    sibling_a = src_dir / "grep.ts"
    sibling_b = src_dir / "read-many-files.ts"
    test_path = src_dir / "glob.test.ts"
    for path, text in (
        (module_path, "export function globTool(): string { return 'glob'; }\n"),
        (sibling_a, "export function grepTool(): string { return 'grep'; }\n"),
        (sibling_b, "export function readManyFiles(): string { return 'read'; }\n"),
        (test_path, "export function testGlobTool(): string { return 'test'; }\n"),
    ):
        path.write_text(text, encoding="utf-8")

    repo_fixture = {
        "symbols": [
            {
                "name": "testGlobTool",
                "file": str(test_path.resolve()),
                "path": str(test_path.resolve()),
                "start_line": 1,
                "end_line": 1,
                "kind": "function",
            }
        ]
    }
    payload = {
        "edit_plan_seed": {
            "primary_file": str(module_path.resolve()),
            "primary_symbol": {"name": "globTool"},
            "primary_span": {"start_line": 1, "end_line": 1},
            "reasons": ["primary-symbol"],
            "confidence": {"overall": 0.95},
            "validation_tests": [str(test_path.resolve())],
            "validation_commands": ["npx vitest run"],
            "edit_ordering": [
                str(module_path.resolve()),
                str(sibling_a.resolve()),
                str(sibling_b.resolve()),
            ],
            "rollback_risk": 0.15,
        },
        "candidate_edit_targets": {
            "spans": [
                {
                    "file": str(module_path.resolve()),
                    "symbol": "globTool",
                    "start_line": 1,
                    "end_line": 1,
                    "rationale": "primary",
                },
                {
                    "file": str(sibling_a.resolve()),
                    "symbol": "grepTool",
                    "start_line": 1,
                    "end_line": 1,
                    "rationale": "related",
                },
                {
                    "file": str(sibling_b.resolve()),
                    "symbol": "readManyFiles",
                    "start_line": 1,
                    "end_line": 1,
                    "rationale": "related",
                },
            ]
        },
    }

    navigation_pack = repo_map._navigation_pack(repo_fixture, payload, max_reads=5)

    groups = navigation_pack["parallel_read_groups"]
    assert len(groups) == 1
    assert groups[0]["label"] == "primary"
    assert sorted(groups[0]["roles"]) == ["primary", "related", "related", "test"]
    assert sorted(groups[0]["files"]) == sorted([
        str(module_path.resolve()),
        str(sibling_a.resolve()),
        str(sibling_b.resolve()),
        str(test_path.resolve()),
    ])


def test_files_with_matches_lists_unique_matched_files(monkeypatch):
    global _FAKE_WALK, _FAKE_BACKEND
    _FAKE_WALK = {".": ["a.py", "b.py"]}
    _FAKE_BACKEND = _FakeBackend(
        results_by_file={
            "a.py": SearchResult(
                matches=[MatchLine(line_number=1, text="ERROR here", file="a.py")],
                total_files=1,
                total_matches=1,
            ),
            "b.py": SearchResult(matches=[], total_files=0, total_matches=0),
        }
    )
    _patch_cli_dependencies(monkeypatch)

    runner = CliRunner()
    result = runner.invoke(app, ["search", "ERROR", ".", "--files-with-matches"])

    assert result.exit_code == 0
    assert result.stdout.strip() == "a.py"


def test_files_with_matches_preserves_discovery_order(monkeypatch):
    global _FAKE_WALK, _FAKE_BACKEND
    _FAKE_WALK = {".": ["b.py", "a.py"]}
    _FAKE_BACKEND = _FakeBackend(
        results_by_file={
            "b.py": SearchResult(
                matches=[MatchLine(line_number=1, text="ERROR first", file="b.py")],
                total_files=1,
                total_matches=1,
            ),
            "a.py": SearchResult(
                matches=[MatchLine(line_number=1, text="ERROR second", file="a.py")],
                total_files=1,
                total_matches=1,
            ),
        }
    )
    _patch_cli_dependencies(monkeypatch)

    runner = CliRunner()
    result = runner.invoke(app, ["search", "ERROR", ".", "--files-with-matches"])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == ["b.py", "a.py"]


def test_files_with_matches_should_respect_total_files_without_materialized_matches(monkeypatch):
    global _FAKE_WALK, _FAKE_BACKEND
    _FAKE_WALK = {".": ["a.py", "b.py"]}
    _FAKE_BACKEND = _FakeBackend(
        results_by_file={
            "a.py": SearchResult(matches=[], total_files=1, total_matches=3),
            "b.py": SearchResult(matches=[], total_files=0, total_matches=0),
        }
    )
    _patch_cli_dependencies(monkeypatch)

    runner = CliRunner()
    result = runner.invoke(app, ["search", "ERROR", ".", "--files-with-matches", "-c"])

    assert result.exit_code == 0
    assert result.stdout.strip() == "a.py"


def test_cli_stats_should_respect_count_only_ripgrep_results(monkeypatch):
    global _FAKE_WALK
    _FAKE_WALK = {".": ["a.py", "b.py"]}
    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakeRipgrepPipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeScanner)
    monkeypatch.setattr(
        "tensor_grep.backends.ripgrep_backend.RipgrepBackend.is_available",
        lambda self: False,
    )

    runner = CliRunner()
    result = runner.invoke(app, ["search", "ERROR", ".", "--stats", "-c"])

    assert result.exit_code == 0
    assert "[stats] scanned_files=2 matched_files=1 total_matches=3" in result.output


def test_files_with_matches_should_use_count_only_ripgrep_file_paths(monkeypatch):
    global _FAKE_WALK
    _FAKE_WALK = {".": ["a.py", "b.py"]}
    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakeRipgrepPipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeScanner)

    runner = CliRunner()
    result = runner.invoke(app, ["search", "ERROR", ".", "--files-with-matches", "-c"])

    assert result.exit_code == 0
    assert result.stdout.strip() == "a.py"


def test_files_with_matches_ripgrep_backend_searches_roots_not_expanded_candidates(
    monkeypatch,
):
    seen: dict[str, object] = {}
    global _FAKE_WALK
    _FAKE_WALK = {".": [f"src/file_{index}.py" for index in range(5000)]}

    class RipgrepBackend:
        def search(self, file_path, pattern, config=None) -> SearchResult:
            seen["paths"] = list(file_path)
            seen["pattern"] = pattern
            seen["fixed_strings"] = config.fixed_strings
            seen["null"] = config.null
            return SearchResult(
                matches=[],
                matched_file_paths=["src/file_1.py"],
                total_files=1,
                total_matches=1,
                routing_backend="RipgrepBackend",
                routing_reason="rg_files_with_matches",
            )

    class _RipgrepPipeline:
        def __init__(self, force_cpu=False, config=None):
            self.backend = RipgrepBackend()
            self.selected_backend_name = "RipgrepBackend"
            self.selected_backend_reason = "rg_files_with_matches"
            self.selected_gpu_device_ids = []
            self.selected_gpu_chunk_plan_mb = []

        def get_backend(self):
            return self.backend

    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _RipgrepPipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeScanner)
    monkeypatch.setattr(
        "tensor_grep.backends.ripgrep_backend.RipgrepBackend.is_available",
        lambda self: False,
    )

    runner = CliRunner()
    result = runner.invoke(
        app, ["search", "--fixed-strings", "ERROR", ".", "--files-with-matches", "-0"]
    )

    assert result.exit_code == 0
    assert seen["paths"] == ["."]
    assert seen["pattern"] == "ERROR"
    assert seen["fixed_strings"] is True
    assert seen["null"] is True
    assert result.stdout == "src/file_1.py\x00"


def test_cli_uses_ripgrep_passthrough_for_files_with_matches(monkeypatch):
    calls: dict[str, object] = {}

    def _fake_passthrough(self, paths, pattern, config=None):
        calls["paths"] = list(paths)
        calls["pattern"] = pattern
        calls["files_with_matches"] = config.files_with_matches
        calls["fixed_strings"] = config.fixed_strings
        return 0

    monkeypatch.setattr(
        "tensor_grep.backends.ripgrep_backend.RipgrepBackend.is_available",
        lambda self: True,
    )
    monkeypatch.setattr(
        "tensor_grep.backends.ripgrep_backend.RipgrepBackend.search_passthrough",
        _fake_passthrough,
    )

    runner = CliRunner()
    result = runner.invoke(app, ["search", "--fixed-strings", "ERROR", ".", "--files-with-matches"])

    assert result.exit_code == 0
    assert calls == {
        "paths": ["."],
        "pattern": "ERROR",
        "files_with_matches": True,
        "fixed_strings": True,
    }


def test_cli_pcre2_rg_format_is_passthrough_eligible() -> None:
    from tensor_grep.cli.main import _can_passthrough_rg

    config = SearchConfig(pcre2=True, sort_by="path")

    assert _can_passthrough_rg(
        config,
        format_type="rg",
        explicit_rg_format=False,
        json_mode=False,
        ndjson_mode=False,
        files_mode=False,
        files_with_matches=False,
        files_without_match=False,
        only_matching=False,
        stats_mode=False,
    )


def test_cli_uses_ripgrep_passthrough_for_explicit_rg_json(monkeypatch):
    calls: dict[str, object] = {}

    def _fake_passthrough(self, paths, pattern, config=None):
        calls["paths"] = list(paths)
        calls["pattern"] = pattern
        calls["json_mode"] = config.json_mode
        calls["fixed_strings"] = config.fixed_strings
        return 0

    monkeypatch.setattr(
        "tensor_grep.backends.ripgrep_backend.RipgrepBackend.is_available",
        lambda self: True,
    )
    monkeypatch.setattr(
        "tensor_grep.backends.ripgrep_backend.RipgrepBackend.search_passthrough",
        _fake_passthrough,
    )

    runner = CliRunner()
    monkeypatch.setattr(
        sys,
        "argv",
        ["tg", "search", "--format", "rg", "--json", "--fixed-strings", "ERROR", "."],
    )
    result = runner.invoke(
        app,
        ["search", "--format", "rg", "--json", "--fixed-strings", "ERROR", "."],
    )

    assert result.exit_code == 0
    assert calls == {
        "paths": ["."],
        "pattern": "ERROR",
        "json_mode": True,
        "fixed_strings": True,
    }


def test_cli_does_not_treat_default_json_as_rg_json_passthrough() -> None:
    from tensor_grep.cli.main import _can_passthrough_rg

    config = SearchConfig(json_mode=True)

    assert not _can_passthrough_rg(
        config,
        format_type="rg",
        explicit_rg_format=False,
        json_mode=True,
        ndjson_mode=False,
        files_mode=False,
        files_with_matches=False,
        files_without_match=False,
        only_matching=False,
        stats_mode=False,
    )


def test_cli_uses_implicit_rg_root_for_no_path_files_with_matches(monkeypatch):
    calls: dict[str, object] = {}

    def _fake_passthrough(self, paths, pattern, config=None):
        calls["paths"] = list(paths)
        calls["pattern"] = pattern
        calls["files_with_matches"] = config.files_with_matches
        calls["fixed_strings"] = config.fixed_strings
        return 0

    monkeypatch.setattr(
        "tensor_grep.backends.ripgrep_backend.RipgrepBackend.is_available",
        lambda self: True,
    )
    monkeypatch.setattr(
        "tensor_grep.backends.ripgrep_backend.RipgrepBackend.search_passthrough",
        _fake_passthrough,
    )

    runner = CliRunner()
    result = runner.invoke(app, ["search", "--fixed-strings", "ERROR", "--files-with-matches"])

    assert result.exit_code == 0
    assert calls == {
        "paths": [],
        "pattern": "ERROR",
        "files_with_matches": True,
        "fixed_strings": True,
    }


def test_files_without_match_lists_unmatched_files(monkeypatch):
    global _FAKE_WALK, _FAKE_BACKEND
    _FAKE_WALK = {".": ["a.py", "b.py"]}
    _FAKE_BACKEND = _FakeBackend(
        results_by_file={
            "a.py": SearchResult(
                matches=[MatchLine(line_number=1, text="ERROR here", file="a.py")],
                total_files=1,
                total_matches=1,
            ),
            "b.py": SearchResult(matches=[], total_files=0, total_matches=0),
        }
    )
    _patch_cli_dependencies(monkeypatch)

    runner = CliRunner()
    result = runner.invoke(app, ["search", "ERROR", ".", "--files-without-match"])

    assert result.exit_code == 0
    assert result.stdout.strip() == "b.py"


def test_files_without_match_respects_scanned_candidates_for_hidden_relative_root(
    monkeypatch,
):
    from tensor_grep.backends.ripgrep_backend import RipgrepBackend

    if not RipgrepBackend().is_available():
        pytest.skip("rg is not available")

    with tempfile.TemporaryDirectory(dir=Path.cwd(), prefix=".fixture-") as temp_dir:
        hidden_root = Path(temp_dir)
        (hidden_root / "large.txt").write_text("NEEDLE\n" * 5, encoding="utf-8")
        (hidden_root / "empty.txt").write_text("other\n", encoding="utf-8")

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "tensor_grep.cli.main",
                "search",
                "NEEDLE",
                hidden_root.name,
                "--files-without-match",
            ],
            cwd=Path.cwd(),
            capture_output=True,
            text=True,
            check=False,
        )

    assert result.returncode == 0
    assert result.stdout.strip() == str(Path(hidden_root.name) / "empty.txt")


def test_files_without_match_skips_gitignored_directories(tmp_path: Path):
    subprocess.run(["git", "init"], cwd=tmp_path, check=False, capture_output=True, text=True)
    (tmp_path / ".gitignore").write_text("build/\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "build").mkdir()

    kept = tmp_path / "src" / "empty.txt"
    ignored = tmp_path / "build" / "ignored.txt"
    matched = tmp_path / "src" / "matched.txt"

    kept.write_text("other\n", encoding="utf-8")
    ignored.write_text("other\n", encoding="utf-8")
    matched.write_text("NEEDLE\n", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "tensor_grep.cli.main",
            "search",
            "NEEDLE",
            str(tmp_path),
            "--files-without-match",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert str(kept) in result.stdout
    assert str(ignored) not in result.stdout


def test_search_rejects_empty_pattern(tmp_path: Path):
    target = tmp_path / "sample.py"
    target.write_text("print('hello')\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(app, ["search", "", str(target)])

    assert result.exit_code == 2
    assert "PATTERN must not be empty" in result.output


def test_search_json_reports_empty_pattern_error(tmp_path: Path):
    target = tmp_path / "sample.py"
    target.write_text("print('hello')\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(app, ["search", "--json", "", str(target)])

    assert result.exit_code == 2
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["error"] == "empty_pattern"
    assert "PATTERN must not be empty" in payload["detail"]


def test_search_reports_missing_input_paths(tmp_path: Path):
    missing = tmp_path / "missing.py"

    runner = CliRunner()
    result = runner.invoke(app, ["search", "hello", str(missing)])

    assert result.exit_code == 2
    assert str(missing) in result.output
    assert "does not exist" in result.output


def test_search_json_reports_missing_input_path_error(tmp_path: Path):
    missing = tmp_path / "missing.py"

    runner = CliRunner()
    result = runner.invoke(app, ["search", "--json", "hello", str(missing)])

    assert result.exit_code == 2
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["error"] == "path_not_found"
    assert str(missing) in payload["detail"]
    assert "does not exist" in payload["detail"]


def test_files_with_matches_null_outputs_nul_separator(tmp_path: Path):
    target = tmp_path / "sample.txt"
    target.write_text("hello\n", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, "-m", "tensor_grep.cli.main", "search", "hello", str(target), "-l", "-0"],
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout.endswith(b"\x00")
    assert b"\r\n" not in result.stdout


def test_files_with_matches_text_outputs_single_platform_newline(tmp_path: Path):
    target = tmp_path / "sample.txt"
    target.write_text("hello\n", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, "-m", "tensor_grep.cli.main", "search", "hello", str(target), "-l"],
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout.rstrip(b"\r\n") == os.fsencode(str(target))
    assert result.stdout.endswith(b"\n")
    assert not result.stdout.endswith(b"\r\r\n")
    assert result.stdout.count(b"\n") == 1


def test_only_matching_outputs_token_not_whole_line(monkeypatch):
    global _FAKE_WALK, _FAKE_BACKEND
    _FAKE_WALK = {".": ["a.py"]}
    _FAKE_BACKEND = _FakeBackend(
        results_by_file={
            "a.py": SearchResult(
                matches=[MatchLine(line_number=1, text="prefix ERROR suffix", file="a.py")],
                total_files=1,
                total_matches=1,
            )
        }
    )
    _patch_cli_dependencies(monkeypatch)

    runner = CliRunner()
    result = runner.invoke(app, ["search", "ERROR", ".", "-o"])

    assert result.exit_code == 0
    assert result.stdout.strip() == "ERROR"


def test_tg_run_uses_typer_help():
    from tensor_grep.cli.main import app

    runner = CliRunner()
    result = runner.invoke(app, ["run", "--help"])
    assert result.exit_code == 0
    assert "Usage: " in result.stdout
    assert "Options" in result.stdout
    assert "positional arguments:" not in result.stdout


def test_tg_run_help_should_position_ast_as_validated_slice_not_ast_grep_parity():
    runner = CliRunner()

    result = runner.invoke(app, ["run", "--help"])

    assert result.exit_code == 0
    help_text = _strip_ansi(result.stdout)
    normalized_help = re.sub(r"\s+", " ", help_text)
    assert "validated AST slice" in normalized_help
    assert "PowerShell users should single-quote AST patterns" in normalized_help
    assert "--selector" in help_text
    assert "--strictness" in help_text
    assert "--stdin" in help_text
    assert "--globs" in help_text
    assert "ast-grep parity" not in help_text
    assert "ast-grep replacement" not in help_text


def test_tg_search_help_should_not_claim_tg_run_ast_grep_parity():
    runner = CliRunner()

    result = runner.invoke(app, ["search", "--help"])

    assert result.exit_code == 0
    help_text = _strip_ansi(result.stdout)
    normalized_help = re.sub(r"\s+", " ", help_text)
    assert "tg run: Run a validated AST slice" in normalized_help
    assert "ast-grep parity" not in help_text


@pytest.mark.parametrize("command", ["scan", "test", "new"])
def test_ast_workflow_help_should_position_commands_as_bounded_ast_slice(command: str) -> None:
    runner = CliRunner()

    result = runner.invoke(app, [command, "--help"])

    assert result.exit_code == 0
    help_text = _strip_ansi(result.stdout)
    normalized_help = re.sub(r"\s+", " ", help_text)
    assert "bounded AST" in normalized_help
    assert "ast-grep replacement" not in help_text
    assert "full ast-grep" not in help_text


def test_cli_uses_ripgrep_passthrough_fast_path(monkeypatch):
    calls: dict[str, object] = {}

    def _fake_passthrough(self, paths, pattern, config=None):
        calls["paths"] = list(paths)
        calls["pattern"] = pattern
        return 0

    monkeypatch.setattr(
        "tensor_grep.backends.ripgrep_backend.RipgrepBackend.is_available", lambda self: True
    )
    monkeypatch.setattr(
        "tensor_grep.backends.ripgrep_backend.RipgrepBackend.search_passthrough",
        _fake_passthrough,
    )

    runner = CliRunner()
    result = runner.invoke(app, ["search", "ERROR", "."])

    assert result.exit_code == 0
    assert calls["pattern"] == "ERROR"
    assert calls["paths"] == ["."]


def test_cli_disables_ripgrep_passthrough_for_ltl_mode(monkeypatch):
    global _FAKE_WALK, _FAKE_BACKEND
    _FAKE_WALK = {".": ["a.log"]}
    _FAKE_BACKEND = _FakeBackend(
        results_by_file={
            "a.log": SearchResult(
                matches=[
                    MatchLine(line_number=1, text="AUTH_FAIL", file="a.log"),
                    MatchLine(line_number=3, text="DB_TIMEOUT", file="a.log"),
                ],
                total_files=1,
                total_matches=1,
            )
        }
    )
    _patch_cli_dependencies(monkeypatch)

    called = {"passthrough": False}

    def _fake_passthrough(self, paths, pattern, config=None):
        called["passthrough"] = True
        return 0

    monkeypatch.setattr(
        "tensor_grep.backends.ripgrep_backend.RipgrepBackend.is_available", lambda self: True
    )
    monkeypatch.setattr(
        "tensor_grep.backends.ripgrep_backend.RipgrepBackend.search_passthrough",
        _fake_passthrough,
    )

    runner = CliRunner()
    result = runner.invoke(app, ["search", "AUTH_FAIL -> eventually DB_TIMEOUT", ".", "--ltl"])

    assert result.exit_code == 0
    assert called["passthrough"] is False


def test_cli_uses_ripgrep_passthrough_for_replace_mode(monkeypatch):
    global _FAKE_WALK, _FAKE_BACKEND
    _FAKE_WALK = {".": ["a.log"]}
    _FAKE_BACKEND = _FakeBackend(
        results_by_file={
            "a.log": SearchResult(
                matches=[MatchLine(line_number=1, text="REPLACED", file="a.log")],
                total_files=1,
                total_matches=1,
            )
        }
    )
    _patch_cli_dependencies(monkeypatch)

    called = {"passthrough": False}

    def _fake_passthrough(self, paths, pattern, config=None):
        called["passthrough"] = True
        return 0

    monkeypatch.setattr(
        "tensor_grep.backends.ripgrep_backend.RipgrepBackend.is_available", lambda self: True
    )
    monkeypatch.setattr(
        "tensor_grep.backends.ripgrep_backend.RipgrepBackend.search_passthrough",
        _fake_passthrough,
    )

    runner = CliRunner()
    result = runner.invoke(app, ["search", "ERROR", ".", "--replace", "REPLACED"])

    assert result.exit_code == 0
    assert called["passthrough"] is True


def test_cli_uses_ripgrep_passthrough_for_short_replace_mode(monkeypatch):
    global _FAKE_WALK, _FAKE_BACKEND
    _FAKE_WALK = {".": ["a.log"]}
    _FAKE_BACKEND = _FakeBackend(
        results_by_file={
            "a.log": SearchResult(
                matches=[MatchLine(line_number=1, text="REPLACED", file="a.log")],
                total_files=1,
                total_matches=1,
            )
        }
    )
    _patch_cli_dependencies(monkeypatch)

    called = {"passthrough": False}

    def _fake_passthrough(self, paths, pattern, config=None):
        called["passthrough"] = True
        return 0

    monkeypatch.setattr(
        "tensor_grep.backends.ripgrep_backend.RipgrepBackend.is_available", lambda self: True
    )
    monkeypatch.setattr(
        "tensor_grep.backends.ripgrep_backend.RipgrepBackend.search_passthrough",
        _fake_passthrough,
    )

    runner = CliRunner()
    result = runner.invoke(app, ["search", "ERROR", ".", "-r", "REPLACED"])

    assert result.exit_code == 0
    assert called["passthrough"] is True


def test_cli_replaces_rg_capture_groups_in_output(monkeypatch):
    global _FAKE_WALK, _FAKE_BACKEND
    _FAKE_WALK = {".": ["a.log"]}
    _FAKE_BACKEND = _FakeBackend(
        results_by_file={
            "a.log": SearchResult(
                matches=[MatchLine(line_number=1, text="abc123", file="a.log")],
                total_files=1,
                total_matches=1,
            )
        }
    )
    _patch_cli_dependencies(monkeypatch)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "search",
            "(?P<letters>[a-z]+)(?P<digits>[0-9]+)",
            ".",
            "--replace",
            "$digits-${letters}-$1-$2-$$-$0-${1}a-$1a",
        ],
    )

    assert result.exit_code == 0
    assert result.stdout.strip() == "123-abc-abc-123-$-abc123-abca-"


def test_cli_replaces_rg_capture_groups_for_fixed_strings(monkeypatch):
    global _FAKE_WALK, _FAKE_BACKEND
    _FAKE_WALK = {".": ["a.log"]}
    _FAKE_BACKEND = _FakeBackend(
        results_by_file={
            "a.log": SearchResult(
                matches=[MatchLine(line_number=1, text="hello world", file="a.log")],
                total_files=1,
                total_matches=1,
            )
        }
    )
    _patch_cli_dependencies(monkeypatch)

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["search", "hello", ".", "-F", "--replace", "$0-${1}a-$1-$$"],
    )

    assert result.exit_code == 0
    assert result.stdout.strip() == "hello-a--$ world"


def test_cli_keeps_non_ascii_replacement_tokens_literal(monkeypatch):
    arabic_digit_one = "\u0661"
    global _FAKE_WALK, _FAKE_BACKEND
    _FAKE_WALK = {".": ["a.log"]}
    _FAKE_BACKEND = _FakeBackend(
        results_by_file={
            "a.log": SearchResult(
                matches=[MatchLine(line_number=1, text="abc123", file="a.log")],
                total_files=1,
                total_matches=1,
            )
        }
    )
    _patch_cli_dependencies(monkeypatch)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "search",
            "(?P<letters>[a-z]+)(?P<digits>[0-9]+)",
            ".",
            "--replace",
            f"$digits-$ébar-${arabic_digit_one}-$$",
        ],
    )

    assert result.exit_code == 0
    assert result.stdout.strip() == f"123-$ébar-${arabic_digit_one}-$"


def test_upgrade_uses_uv_when_available(monkeypatch):
    calls: list[list[str]] = []

    def _fake_run(cmd, capture_output=True, text=True, check=True):
        calls.append(list(cmd))
        if cmd[0] == "uv":
            return subprocess.CompletedProcess(cmd, 0, stdout="Installed 1 package", stderr="")
        if cmd[:2] == ["python", "-c"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="0.32.0\n", stderr="")
        raise AssertionError("pip fallback should not be used when uv succeeds")

    versions = iter(["0.31.0", "0.32.0"])

    monkeypatch.setattr("sys.executable", "python")
    monkeypatch.setattr("importlib.metadata.version", lambda _name: next(versions))
    monkeypatch.setattr("subprocess.run", _fake_run)
    monkeypatch.setattr(
        "tensor_grep.cli.main._latest_pypi_tensor_grep_version",
        lambda: "0.32.0",
        raising=False,
    )

    runner = CliRunner()
    result = runner.invoke(app, ["upgrade"])

    assert result.exit_code == 0
    assert calls[0][0] == "uv"
    assert "Successfully upgraded tensor-grep via uv!" in result.stdout


def test_upgrade_restarts_preexisting_session_daemon_after_handoff_loss(monkeypatch):
    calls: list[list[str]] = []
    daemon_statuses = iter([
        {
            "running": True,
            "root": r"C:\dev\projects\tensor-grep",
            "host": "127.0.0.1",
            "port": 43123,
            "pid": 9001,
        },
        {
            "running": False,
            "root": r"C:\dev\projects\tensor-grep",
            "stale_metadata": True,
        },
    ])
    restarted: list[str] = []

    def _fake_run(cmd, capture_output=True, text=True, check=True):
        calls.append(list(cmd))
        if cmd[0] == "uv":
            return subprocess.CompletedProcess(cmd, 0, stdout="Installed 1 package", stderr="")
        if cmd[:2] == ["python", "-c"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="0.32.0\n", stderr="")
        raise AssertionError("pip fallback should not be used when uv succeeds")

    monkeypatch.setattr("sys.executable", "python")
    monkeypatch.setattr("importlib.metadata.version", lambda _name: "0.31.0")
    monkeypatch.setattr("subprocess.run", _fake_run)
    monkeypatch.setattr(
        "tensor_grep.cli.main._latest_pypi_tensor_grep_version",
        lambda: "0.32.0",
        raising=False,
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_session_daemon_status",
        lambda _path: next(daemon_statuses),
    )
    monkeypatch.setattr(
        "tensor_grep.cli.session_daemon.start_session_daemon",
        lambda path: (
            restarted.append(path) or {"running": True, "root": path, "auto_started": True}
        ),
    )

    runner = CliRunner()
    result = runner.invoke(app, ["upgrade"])

    assert result.exit_code == 0
    assert calls[0][0] == "uv"
    assert restarted == [r"C:\dev\projects\tensor-grep"]
    assert "Session daemon restarted after upgrade" in result.stdout


def test_upgrade_does_not_start_session_daemon_when_none_was_running(monkeypatch):
    calls: list[list[str]] = []

    def _fake_run(cmd, capture_output=True, text=True, check=True):
        calls.append(list(cmd))
        if cmd[0] == "uv":
            return subprocess.CompletedProcess(cmd, 0, stdout="Installed 1 package", stderr="")
        if cmd[:2] == ["python", "-c"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="0.32.0\n", stderr="")
        raise AssertionError("pip fallback should not be used when uv succeeds")

    monkeypatch.setattr("sys.executable", "python")
    monkeypatch.setattr("importlib.metadata.version", lambda _name: "0.31.0")
    monkeypatch.setattr("subprocess.run", _fake_run)
    monkeypatch.setattr(
        "tensor_grep.cli.main._latest_pypi_tensor_grep_version",
        lambda: "0.32.0",
        raising=False,
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_session_daemon_status",
        lambda _path: {"running": False, "root": r"C:\dev\projects\tensor-grep"},
    )
    monkeypatch.setattr(
        "tensor_grep.cli.session_daemon.start_session_daemon",
        lambda _path: (_ for _ in ()).throw(
            AssertionError("upgrade should not start a daemon that was not already running")
        ),
    )

    runner = CliRunner()
    result = runner.invoke(app, ["upgrade"])

    assert result.exit_code == 0
    assert calls[0][0] == "uv"
    assert "Session daemon restarted after upgrade" not in result.stdout


def test_upgrade_pins_exact_latest_pypi_version_when_local_metadata_is_stale(monkeypatch):
    calls: list[list[str]] = []

    def _fake_run(cmd, capture_output=True, text=True, check=True):
        calls.append(list(cmd))
        if cmd[0] == "uv":
            return subprocess.CompletedProcess(cmd, 0, stdout="Installed 1 package", stderr="")
        if cmd[:2] == ["python", "-c"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="0.33.0\n", stderr="")
        raise AssertionError("pip fallback should not be used when uv succeeds")

    monkeypatch.setattr("sys.executable", "python")
    monkeypatch.setattr("importlib.metadata.version", lambda _name: "0.32.0")
    monkeypatch.setattr("subprocess.run", _fake_run)
    monkeypatch.setattr(
        "tensor_grep.cli.main._latest_pypi_tensor_grep_version",
        lambda: "0.33.0",
        raising=False,
    )

    runner = CliRunner()
    result = runner.invoke(app, ["upgrade"])

    assert result.exit_code == 0
    assert calls[0][0] == "uv"
    assert any("tensor-grep==0.33.0" in cmd for cmd in calls)
    assert calls[0][-1] == "tensor-grep==0.33.0"
    assert "Successfully upgraded tensor-grep via uv!" in result.stdout


def test_upgrade_latest_version_candidates_skip_yanked_pypi_releases():
    payload = {
        "info": {"version": "0.34.0"},
        "releases": {
            "0.32.0": [{"yanked": False}],
            "0.33.0": [{"yanked": False}],
            "0.34.0": [{"yanked": True}],
        },
    }
    simple_index = """
    <a href="tensor_grep-0.33.0-py3-none-any.whl">tensor_grep-0.33.0-py3-none-any.whl</a>
    <a href="tensor_grep-0.34.0-py3-none-any.whl" data-yanked="bad release">tensor_grep-0.34.0-py3-none-any.whl</a>
    """

    candidates = [
        *_candidate_versions_from_pypi_json(payload),
        *_candidate_versions_from_pypi_simple_index(simple_index),
    ]

    assert _highest_tensor_grep_version(candidates) == "0.33.0"


def test_upgrade_latest_version_candidates_include_pip_index_output():
    pip_output = """
    tensor-grep (0.34.0)
    Available versions: 0.34.0, 0.33.0, 0.32.0
      INSTALLED: 0.32.0
      LATEST:    0.34.0
    """

    assert (
        _highest_tensor_grep_version(_candidate_versions_from_pip_index_output(pip_output))
        == "0.34.0"
    )


def test_latest_pypi_probe_uses_pip_index_when_json_and_simple_are_stale(monkeypatch):
    class _FakeResponse:
        def __init__(self, body: str) -> None:
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self) -> bytes:
            return self.body.encode("utf-8")

    stale_json = json.dumps({
        "info": {"version": "0.33.0"},
        "releases": {
            "0.32.0": [{"yanked": False}],
            "0.33.0": [{"yanked": False}],
        },
    })
    stale_simple = """
    <a href="tensor_grep-0.33.0-py3-none-any.whl">tensor_grep-0.33.0-py3-none-any.whl</a>
    """
    calls: list[list[str]] = []

    def _fake_urlopen(request, timeout=None):
        url = request.get_full_url()
        if url.endswith("/json"):
            return _FakeResponse(stale_json)
        if url.endswith("/simple/tensor-grep/"):
            return _FakeResponse(stale_simple)
        raise AssertionError(f"unexpected url: {url}")

    def _fake_run(cmd, **_kwargs):
        calls.append([str(part) for part in cmd])
        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout=(
                "tensor-grep (0.34.0)\n"
                "Available versions: 0.34.0, 0.33.0, 0.32.0\n"
                "  LATEST:    0.34.0\n"
            ),
            stderr="",
        )

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    monkeypatch.setattr(cli_main.subprocess, "run", _fake_run)

    assert cli_main._latest_pypi_tensor_grep_version(timeout_seconds=1.0) == "0.34.0"
    assert calls
    assert calls[0][1:5] == ["-m", "pip", "index", "versions"]
    assert "--no-cache-dir" in calls[0]


def test_upgrade_reports_latest_pypi_version_when_verified_version_matches_latest(monkeypatch):
    calls: list[list[str]] = []

    def _fake_run(cmd, capture_output=True, text=True, check=True):
        calls.append(list(cmd))
        if cmd[0] == "uv":
            return subprocess.CompletedProcess(cmd, 0, stdout="Installed 1 package", stderr="")
        if cmd[:2] == ["python", "-c"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="0.32.0\n", stderr="")
        raise AssertionError("pip fallback should not be used when uv succeeds")

    monkeypatch.setattr("sys.executable", "python")
    monkeypatch.setattr("importlib.metadata.version", lambda _name: "0.32.0")
    monkeypatch.setattr("subprocess.run", _fake_run)
    monkeypatch.setattr(
        "tensor_grep.cli.main._latest_pypi_tensor_grep_version",
        lambda: "0.32.0",
        raising=False,
    )

    runner = CliRunner()
    result = runner.invoke(app, ["upgrade"])

    assert result.exit_code == 0
    assert calls[0][0] == "uv"
    assert any("tensor-grep==0.32.0" in cmd for cmd in calls)
    assert "tensor-grep is already at the latest PyPI version (0.32.0)." in result.stdout


def test_native_frontdoor_asset_candidates_default_to_cpu_even_when_host_has_nvidia(monkeypatch):

    def _fake_run(cmd, capture_output=True, text=True, check=False, timeout=None):
        raise AssertionError(f"default asset selection should not probe hardware: {cmd}")

    monkeypatch.delenv("TENSOR_GREP_NATIVE_FRONTDOOR_FLAVOR", raising=False)
    monkeypatch.delenv("TG_NATIVE_FRONTDOOR_REQUESTED_FLAVOR", raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr("platform.machine", lambda: "x86_64")
    monkeypatch.setattr(
        cli_main.shutil, "which", lambda name: name if name == "nvidia-smi" else None
    )
    monkeypatch.setattr(cli_main.subprocess, "run", _fake_run)

    candidates = cli_main._native_frontdoor_asset_candidates()

    assert [(candidate.flavor, candidate.asset_name) for candidate in candidates] == [
        ("cpu", "tg-linux-amd64-cpu"),
    ]


def test_native_frontdoor_asset_candidates_prefer_nvidia_only_when_requested(monkeypatch):

    monkeypatch.setenv("TENSOR_GREP_NATIVE_FRONTDOOR_FLAVOR", "nvidia")
    monkeypatch.delenv("TG_NATIVE_FRONTDOOR_REQUESTED_FLAVOR", raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr("platform.machine", lambda: "x86_64")

    candidates = cli_main._native_frontdoor_asset_candidates()

    assert [(candidate.flavor, candidate.asset_name) for candidate in candidates] == [
        ("nvidia", "tg-linux-amd64-nvidia"),
        ("cpu", "tg-linux-amd64-cpu"),
    ]


def test_upgrade_falls_back_to_cpu_native_asset_when_nvidia_asset_is_unavailable(
    monkeypatch, tmp_path
):
    install_dir = tmp_path / ".tensor-grep"
    python_executable = install_dir / ".venv" / "Scripts" / "python.exe"
    native_binary = install_dir / "bin" / "tg.exe"
    python_executable.parent.mkdir(parents=True)
    native_binary.parent.mkdir(parents=True)
    python_executable.write_text("", encoding="utf-8")
    native_binary.write_text("old native", encoding="utf-8")
    downloads: list[str] = []

    def _fake_run(cmd, capture_output=True, text=True, check=True, timeout=None):
        command = [str(part) for part in cmd]
        if command[0] == "uv":
            return subprocess.CompletedProcess(cmd, 0, stdout="Installed 1 package", stderr="")
        if command[:2] == [str(python_executable), "-c"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="0.33.0\n", stderr="")
        if command[0] == str(native_binary):
            version = (
                "0.33.0" if native_binary.read_text(encoding="utf-8") == "new native" else "0.32.0"
            )
            return subprocess.CompletedProcess(cmd, 0, stdout=f"tg {version}\n", stderr="")
        if command[0].endswith(".tmp"):
            return subprocess.CompletedProcess(cmd, 0, stdout="tg 0.33.0\n", stderr="")
        raise AssertionError(f"unexpected command: {command}")

    def _fake_urlretrieve(url, filename):
        downloads.append(str(url))
        if str(url).endswith("tg-windows-amd64-nvidia.exe"):
            raise OSError("404 Not Found")
        Path(filename).write_text("new native", encoding="utf-8")
        return filename, None

    monkeypatch.setattr("sys.executable", str(python_executable))
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr("platform.machine", lambda: "AMD64")
    monkeypatch.setenv("TENSOR_GREP_NATIVE_FRONTDOOR_FLAVOR", "nvidia")
    monkeypatch.setattr("importlib.metadata.version", lambda _name: "0.32.0")
    monkeypatch.setattr("subprocess.run", _fake_run)
    monkeypatch.setattr("urllib.request.urlretrieve", _fake_urlretrieve)
    monkeypatch.setattr(
        "tensor_grep.cli.main._latest_pypi_tensor_grep_version",
        lambda: "0.33.0",
        raising=False,
    )

    runner = CliRunner()
    result = runner.invoke(app, ["upgrade"])

    assert result.exit_code == 0
    assert downloads == [
        "https://github.com/oimiragieo/tensor-grep/releases/download/v0.33.0/"
        "tg-windows-amd64-nvidia.exe",
        "https://github.com/oimiragieo/tensor-grep/releases/download/v0.33.0/"
        "tg-windows-amd64-cpu.exe",
    ]
    assert native_binary.read_text(encoding="utf-8") == "new native"
    metadata = json.loads(
        native_binary.with_name("tg-native-metadata.json").read_text(encoding="utf-8")
    )
    assert metadata == {
        "artifact": "tensor_grep_native_frontdoor_metadata",
        "asset_flavor": "cpu",
        "asset_name": "tg-windows-amd64-cpu.exe",
        "requested_asset_flavor": "nvidia",
        "version": "0.33.0",
    }
    assert "Native tg front door refreshed to 0.33.0." in result.stdout
    assert "Native asset flavor: cpu." in result.stdout
    assert "GPU promotion" not in result.stdout


def test_upgrade_falls_back_to_cpu_native_asset_when_nvidia_asset_smoke_fails(
    monkeypatch, tmp_path
):
    install_dir = tmp_path / ".tensor-grep"
    python_executable = install_dir / ".venv" / "Scripts" / "python.exe"
    native_binary = install_dir / "bin" / "tg.exe"
    python_executable.parent.mkdir(parents=True)
    native_binary.parent.mkdir(parents=True)
    python_executable.write_text("", encoding="utf-8")
    native_binary.write_text("old native", encoding="utf-8")
    downloads: list[str] = []
    temp_versions: dict[str, str] = {}

    def _fake_run(cmd, capture_output=True, text=True, check=True, timeout=None):
        command = [str(part) for part in cmd]
        if command[0] == "uv":
            return subprocess.CompletedProcess(cmd, 0, stdout="Installed 1 package", stderr="")
        if command[:2] == [str(python_executable), "-c"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="0.33.0\n", stderr="")
        if command[0] == str(native_binary):
            version = (
                "0.33.0" if native_binary.read_text(encoding="utf-8") == "new native" else "0.32.0"
            )
            return subprocess.CompletedProcess(cmd, 0, stdout=f"tg {version}\n", stderr="")
        if command[0].endswith(".tmp"):
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout=f"tg {temp_versions.get(command[0], '0.33.0')}\n",
                stderr="",
            )
        raise AssertionError(f"unexpected command: {command}")

    def _fake_urlretrieve(url, filename):
        downloads.append(str(url))
        path = Path(filename)
        if str(url).endswith("tg-windows-amd64-nvidia.exe"):
            path.write_text("wrong native", encoding="utf-8")
            temp_versions[str(path)] = "0.32.0"
        else:
            path.write_text("new native", encoding="utf-8")
            temp_versions[str(path)] = "0.33.0"
        return filename, None

    monkeypatch.setattr("sys.executable", str(python_executable))
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr("platform.machine", lambda: "AMD64")
    monkeypatch.setenv("TENSOR_GREP_NATIVE_FRONTDOOR_FLAVOR", "nvidia")
    monkeypatch.setattr("importlib.metadata.version", lambda _name: "0.32.0")
    monkeypatch.setattr("subprocess.run", _fake_run)
    monkeypatch.setattr("urllib.request.urlretrieve", _fake_urlretrieve)
    monkeypatch.setattr(
        "tensor_grep.cli.main._latest_pypi_tensor_grep_version",
        lambda: "0.33.0",
        raising=False,
    )

    runner = CliRunner()
    result = runner.invoke(app, ["upgrade"])

    assert result.exit_code == 0
    assert downloads == [
        "https://github.com/oimiragieo/tensor-grep/releases/download/v0.33.0/"
        "tg-windows-amd64-nvidia.exe",
        "https://github.com/oimiragieo/tensor-grep/releases/download/v0.33.0/"
        "tg-windows-amd64-cpu.exe",
    ]
    assert native_binary.read_text(encoding="utf-8") == "new native"
    assert "Native tg front door refreshed to 0.33.0." in result.stdout
    assert "Native asset flavor: cpu." in result.stdout


def test_upgrade_restores_previous_native_binary_when_install_verification_fails(
    monkeypatch, tmp_path
):
    install_dir = tmp_path / ".tensor-grep"
    python_executable = install_dir / ".venv" / "Scripts" / "python.exe"
    native_binary = install_dir / "bin" / "tg.exe"
    python_executable.parent.mkdir(parents=True)
    native_binary.parent.mkdir(parents=True)
    python_executable.write_text("", encoding="utf-8")
    native_binary.write_text("old native", encoding="utf-8")
    downloads: list[str] = []

    def _fake_run(cmd, capture_output=True, text=True, check=True, timeout=None):
        command = [str(part) for part in cmd]
        if command[0] == "uv":
            return subprocess.CompletedProcess(cmd, 0, stdout="Installed 1 package", stderr="")
        if command[:2] == [str(python_executable), "-c"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="0.33.0\n", stderr="")
        if command[0] == str(native_binary):
            version = (
                "0.33.0"
                if native_binary.read_text(encoding="utf-8") == "verified native"
                else "0.32.0"
            )
            return subprocess.CompletedProcess(cmd, 0, stdout=f"tg {version}\n", stderr="")
        if command[0].endswith(".tmp"):
            return subprocess.CompletedProcess(cmd, 0, stdout="tg 0.33.0\n", stderr="")
        raise AssertionError(f"unexpected command: {command}")

    def _fake_urlretrieve(url, filename):
        downloads.append(str(url))
        Path(filename).write_text("bad installed native", encoding="utf-8")
        return filename, None

    monkeypatch.setattr("sys.executable", str(python_executable))
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr("platform.machine", lambda: "AMD64")
    monkeypatch.setenv("TENSOR_GREP_NATIVE_FRONTDOOR_FLAVOR", "cpu")
    monkeypatch.setattr("importlib.metadata.version", lambda _name: "0.32.0")
    monkeypatch.setattr("subprocess.run", _fake_run)
    monkeypatch.setattr("urllib.request.urlretrieve", _fake_urlretrieve)
    monkeypatch.setattr(
        "tensor_grep.cli.main._latest_pypi_tensor_grep_version",
        lambda: "0.33.0",
        raising=False,
    )

    runner = CliRunner()
    result = runner.invoke(app, ["upgrade"])

    assert result.exit_code == 1
    assert downloads == [
        "https://github.com/oimiragieo/tensor-grep/releases/download/v0.33.0/"
        "tg-windows-amd64-cpu.exe",
    ]
    assert native_binary.read_text(encoding="utf-8") == "old native"
    assert "release-native front-door asset install failed" in result.stderr


def test_upgrade_refreshes_managed_native_frontdoor_after_package_upgrade(monkeypatch, tmp_path):
    install_dir = tmp_path / ".tensor-grep"
    python_executable = install_dir / ".venv" / "Scripts" / "python.exe"
    native_binary = install_dir / "bin" / "tg.exe"
    python_executable.parent.mkdir(parents=True)
    native_binary.parent.mkdir(parents=True)
    python_executable.write_text("", encoding="utf-8")
    native_binary.write_text("old native", encoding="utf-8")
    unrelated_native_env = tmp_path / "other" / "bin" / "tg.exe"
    unrelated_native_env.parent.mkdir(parents=True)
    unrelated_native_env.write_text("other native", encoding="utf-8")
    downloads: list[str] = []

    def _fake_run(cmd, capture_output=True, text=True, check=True, timeout=None):
        command = [str(part) for part in cmd]
        if command[0] == "uv":
            return subprocess.CompletedProcess(cmd, 0, stdout="Installed 1 package", stderr="")
        if command[:2] == [str(python_executable), "-c"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="0.33.0\n", stderr="")
        if command[0] == str(native_binary):
            version = (
                "0.33.0" if native_binary.read_text(encoding="utf-8") == "new native" else "0.32.0"
            )
            return subprocess.CompletedProcess(cmd, 0, stdout=f"tg {version}\n", stderr="")
        if command[0].endswith(".tmp"):
            return subprocess.CompletedProcess(cmd, 0, stdout="tg 0.33.0\n", stderr="")
        raise AssertionError(f"unexpected command: {command}")

    def _fake_urlretrieve(url, filename):
        downloads.append(str(url))
        Path(filename).write_text("new native", encoding="utf-8")
        return filename, None

    monkeypatch.setattr("sys.executable", str(python_executable))
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr("platform.machine", lambda: "AMD64")
    monkeypatch.setenv("TENSOR_GREP_NATIVE_FRONTDOOR_FLAVOR", "cpu")
    monkeypatch.setenv("TG_NATIVE_TG_BINARY", str(unrelated_native_env))
    monkeypatch.setattr("importlib.metadata.version", lambda _name: "0.32.0")
    monkeypatch.setattr("subprocess.run", _fake_run)
    monkeypatch.setattr("urllib.request.urlretrieve", _fake_urlretrieve)
    monkeypatch.setattr(
        "tensor_grep.cli.main._latest_pypi_tensor_grep_version",
        lambda: "0.33.0",
        raising=False,
    )

    runner = CliRunner()
    result = runner.invoke(app, ["upgrade"])

    assert result.exit_code == 0
    assert downloads == [
        "https://github.com/oimiragieo/tensor-grep/releases/download/v0.33.0/"
        "tg-windows-amd64-cpu.exe"
    ]
    assert native_binary.read_text(encoding="utf-8") == "new native"
    metadata = json.loads(
        native_binary.with_name("tg-native-metadata.json").read_text(encoding="utf-8")
    )
    assert metadata["asset_flavor"] == "cpu"
    assert metadata["requested_asset_flavor"] == "cpu"
    assert metadata["asset_name"] == "tg-windows-amd64-cpu.exe"
    assert metadata["version"] == "0.33.0"
    assert "Successfully upgraded tensor-grep via uv!" in result.stdout
    assert "Native tg front door refreshed to 0.33.0." in result.stdout


def test_upgrade_repairs_windows_path_order_for_python_subprocess_tg(monkeypatch, tmp_path):
    install_dir = tmp_path / ".tensor-grep"
    python_executable = install_dir / ".venv" / "Scripts" / "python.exe"
    native_binary = install_dir / "bin" / "tg.exe"
    foreign_dir = tmp_path / "Python314" / "Scripts"
    python_executable.parent.mkdir(parents=True)
    native_binary.parent.mkdir(parents=True)
    foreign_dir.mkdir(parents=True)
    python_executable.write_text("", encoding="utf-8")
    native_binary.write_text("new native", encoding="utf-8")
    foreign_tg = foreign_dir / "tg.exe"
    foreign_tg.write_text("Together CLI", encoding="utf-8")
    managed_dir = native_binary.parent
    user_path = {"value": f"{foreign_dir};{managed_dir}"}

    class _FakeKey:
        def __init__(self, root, subkey):
            self.root = root
            self.subkey = subkey

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    fake_winreg = types.SimpleNamespace()
    fake_winreg.HKEY_CURRENT_USER = object()
    fake_winreg.HKEY_LOCAL_MACHINE = object()
    fake_winreg.KEY_SET_VALUE = 2
    fake_winreg.REG_EXPAND_SZ = 2
    fake_winreg.REG_SZ = 1
    fake_winreg.OpenKey = lambda root, subkey, *_args: _FakeKey(root, subkey)

    def _query_value_ex(key, name):
        if name != "Path" or key.root is not fake_winreg.HKEY_CURRENT_USER:
            raise OSError("missing registry value")
        return user_path["value"], fake_winreg.REG_EXPAND_SZ

    def _set_value_ex(key, name, _reserved, _value_type, value):
        assert key.root is fake_winreg.HKEY_CURRENT_USER
        assert name == "Path"
        user_path["value"] = value

    fake_winreg.QueryValueEx = _query_value_ex
    fake_winreg.SetValueEx = _set_value_ex

    def _fake_run(cmd, capture_output=True, text=True, check=True, timeout=None):
        command = [str(part) for part in cmd]
        if command[0] == "uv":
            return subprocess.CompletedProcess(cmd, 0, stdout="Audited 1 package", stderr="")
        if command[:2] == [str(python_executable), "-c"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="0.33.0\n", stderr="")
        if command[0] == str(native_binary):
            return subprocess.CompletedProcess(cmd, 0, stdout="tg 0.33.0\n", stderr="")
        raise AssertionError(f"unexpected command: {command}")

    def _fake_candidate_version(path):
        candidate = Path(path)
        if candidate == native_binary:
            return "tg 0.33.0"
        if candidate == foreign_tg:
            return "Together CLI (v2.12.0)"
        return None

    monkeypatch.setattr("sys.executable", str(python_executable))
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("PATH", f"{foreign_dir};{managed_dir}")
    monkeypatch.setitem(sys.modules, "winreg", fake_winreg)
    monkeypatch.setattr("importlib.metadata.version", lambda _name: "0.33.0")
    monkeypatch.setattr("subprocess.run", _fake_run)
    monkeypatch.setattr(
        "tensor_grep.cli.main._latest_pypi_tensor_grep_version",
        lambda: "0.33.0",
        raising=False,
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_tg_candidate_version", _fake_candidate_version
    )

    result = CliRunner().invoke(app, ["upgrade"])

    assert result.exit_code == 0
    assert user_path["value"].split(";")[0] == str(managed_dir)
    assert os.environ["PATH"].split(";")[0] == str(managed_dir)
    assert "Windows PATH now prefers managed native tg.exe" in result.stdout


def test_windows_path_repair_reports_machine_path_python_subprocess_blocker(
    monkeypatch,
    tmp_path,
):
    from tensor_grep.cli import main as cli_main

    install_dir = tmp_path / ".tensor-grep"
    native_binary = install_dir / "bin" / "tg.exe"
    foreign_dir = tmp_path / "MachinePython314" / "Scripts"
    native_binary.parent.mkdir(parents=True)
    foreign_dir.mkdir(parents=True)
    native_binary.write_text("managed native", encoding="utf-8")
    foreign_tg = foreign_dir / "tg.exe"
    foreign_tg.write_text("Together CLI", encoding="utf-8")

    user_path = {"value": str(native_binary.parent)}
    machine_path = {"value": str(foreign_dir)}

    class _FakeKey:
        def __init__(self, root, subkey):
            self.root = root
            self.subkey = subkey

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    fake_winreg = types.SimpleNamespace()
    fake_winreg.HKEY_CURRENT_USER = object()
    fake_winreg.HKEY_LOCAL_MACHINE = object()
    fake_winreg.KEY_SET_VALUE = 2
    fake_winreg.REG_EXPAND_SZ = 2
    fake_winreg.REG_SZ = 1
    fake_winreg.OpenKey = lambda root, subkey, *_args: _FakeKey(root, subkey)

    def _query_value_ex(key, name):
        if name != "Path":
            raise OSError("missing registry value")
        if key.root is fake_winreg.HKEY_CURRENT_USER:
            return user_path["value"], fake_winreg.REG_EXPAND_SZ
        if key.root is fake_winreg.HKEY_LOCAL_MACHINE:
            return machine_path["value"], fake_winreg.REG_EXPAND_SZ
        raise OSError("missing registry value")

    def _set_value_ex(key, name, _reserved, _value_type, value):
        assert key.root is fake_winreg.HKEY_CURRENT_USER
        assert name == "Path"
        user_path["value"] = value

    def _fake_candidate_version(path):
        candidate = Path(path)
        if candidate == native_binary:
            return "tg 0.33.0"
        if candidate == foreign_tg:
            return "Together CLI (v2.12.0)"
        return None

    fake_winreg.QueryValueEx = _query_value_ex
    fake_winreg.SetValueEx = _set_value_ex

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("PATH", f"{foreign_dir};{native_binary.parent}")
    monkeypatch.setitem(sys.modules, "winreg", fake_winreg)
    monkeypatch.setattr(cli_main, "_doctor_tg_candidate_version", _fake_candidate_version)

    message = cli_main._ensure_windows_managed_native_first_on_path(native_binary)

    assert message is not None
    assert "Python subprocess" in message
    assert "Machine PATH" in message
    assert str(foreign_dir) in message
    assert str(native_binary.parent) in message
    assert "Do not remove unrelated launchers" in message
    assert "repair-launcher --allow-foreign-rename" in message
    assert "Windows PATH now prefers managed native tg.exe" not in message


def test_upgrade_removes_stale_tensor_grep_python_scripts_launcher(
    monkeypatch,
    tmp_path,
):
    install_dir = tmp_path / ".tensor-grep"
    native_binary = install_dir / "bin" / "tg.exe"
    stale_dir = tmp_path / "Python314" / "Scripts"
    foreign_dir = tmp_path / "ForeignPython" / "Scripts"
    native_binary.parent.mkdir(parents=True)
    stale_dir.mkdir(parents=True)
    foreign_dir.mkdir(parents=True)
    native_binary.write_text("managed native", encoding="utf-8")
    stale_tg = stale_dir / "tg.exe"
    stale_tg.write_text("stale tensor-grep launcher", encoding="utf-8")
    stale_python = stale_dir.parent / "python.exe"
    stale_python.write_text("", encoding="utf-8")
    package_location = stale_dir.parent / "Lib" / "site-packages"
    package_launcher = os.path.relpath(stale_tg, package_location)
    foreign_tg = foreign_dir / "tg.exe"
    foreign_tg.write_text("foreign launcher", encoding="utf-8")
    calls: list[list[str]] = []

    def _fake_candidate_version(path):
        candidate = Path(path)
        if candidate == native_binary:
            return "tg 0.33.0"
        if candidate == stale_tg:
            return "tensor-grep 0.32.0"
        if candidate == foreign_tg:
            return "Together CLI (v2.12.0)"
        return None

    def _fake_run(cmd, capture_output=True, text=True, timeout=None, **_kwargs):
        command = [str(part) for part in cmd]
        calls.append(command)
        if command[:5] == [str(stale_python), "-m", "pip", "show", "-f"]:
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout=(
                    "Name: tensor-grep\n"
                    "Version: 0.32.0\n"
                    f"Location: {package_location}\n"
                    "Files:\n"
                    f"{package_launcher}\n"
                ),
                stderr="",
            )
        if command[:4] == [str(stale_python), "-m", "pip", "uninstall"]:
            stale_tg.unlink(missing_ok=True)
            return subprocess.CompletedProcess(cmd, 0, stdout="uninstalled\n", stderr="")
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("PATH", f"{stale_dir};{native_binary.parent};{foreign_dir}")
    monkeypatch.setattr(cli_main, "_doctor_fresh_shell_path_value", lambda: str(stale_dir))
    monkeypatch.setattr(cli_main, "_doctor_tg_candidate_version", _fake_candidate_version)
    monkeypatch.setattr("subprocess.run", _fake_run)

    message = cli_main._remove_windows_stale_tensor_grep_python_launchers(
        "0.33.0",
        native_binary,
    )

    assert message is not None
    assert "Removed stale tensor-grep Python package launchers" in message
    assert str(stale_tg) in message
    assert not stale_tg.exists()
    assert foreign_tg.exists()
    assert native_binary.exists()
    assert [str(stale_python), "-m", "pip", "show", "-f", "tensor-grep"] in calls
    assert [str(stale_python), "-m", "pip", "uninstall", "-y", "tensor-grep"] in calls


def test_upgrade_removes_shadowing_tensor_grep_python_scripts_launcher_even_when_current(
    monkeypatch,
    tmp_path,
):
    native_binary = tmp_path / ".tensor-grep" / "bin" / "tg.exe"
    stale_dir = tmp_path / "Python314" / "Scripts"
    native_binary.parent.mkdir(parents=True)
    stale_dir.mkdir(parents=True)
    native_binary.write_text("managed native", encoding="utf-8")
    stale_tg = stale_dir / "tg.exe"
    stale_tg.write_text("shadowing tensor-grep launcher", encoding="utf-8")
    stale_python = stale_dir.parent / "python.exe"
    stale_python.write_text("", encoding="utf-8")
    package_location = stale_dir.parent / "Lib" / "site-packages"
    package_launcher = os.path.relpath(stale_tg, package_location)
    calls: list[list[str]] = []

    def _fake_candidate_version(path):
        candidate = Path(path)
        if candidate == native_binary:
            return "tg 0.33.0"
        if candidate == stale_tg:
            return "tensor-grep 0.33.0"
        return None

    def _fake_run(cmd, capture_output=True, text=True, timeout=None, **_kwargs):
        command = [str(part) for part in cmd]
        calls.append(command)
        if command[:5] == [str(stale_python), "-m", "pip", "show", "-f"]:
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout=(
                    "Name: tensor-grep\n"
                    "Version: 0.33.0\n"
                    f"Location: {package_location}\n"
                    "Files:\n"
                    f"{package_launcher}\n"
                ),
                stderr="",
            )
        if command[:4] == [str(stale_python), "-m", "pip", "uninstall"]:
            stale_tg.unlink(missing_ok=True)
            return subprocess.CompletedProcess(cmd, 0, stdout="uninstalled\n", stderr="")
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("PATH", f"{native_binary.parent};{stale_dir}")
    monkeypatch.setattr(
        cli_main,
        "_doctor_fresh_shell_path_value",
        lambda: f"{stale_dir};{native_binary.parent}",
    )
    monkeypatch.setattr(cli_main, "_doctor_tg_candidate_version", _fake_candidate_version)
    monkeypatch.setattr("subprocess.run", _fake_run)

    message = cli_main._remove_windows_stale_tensor_grep_python_launchers(
        "0.33.0",
        native_binary,
    )

    assert message is not None
    assert "Removed stale tensor-grep Python package launchers" in message
    assert not stale_tg.exists()
    assert [str(stale_python), "-m", "pip", "show", "-f", "tensor-grep"] in calls
    assert [str(stale_python), "-m", "pip", "uninstall", "-y", "tensor-grep"] in calls


def test_upgrade_removes_broken_tensor_grep_python_scripts_launcher_by_package_owner(
    monkeypatch,
    tmp_path,
):
    native_binary = tmp_path / ".tensor-grep" / "bin" / "tg.exe"
    stale_dir = tmp_path / "Python314" / "Scripts"
    native_binary.parent.mkdir(parents=True)
    stale_dir.mkdir(parents=True)
    native_binary.write_text("managed native", encoding="utf-8")
    stale_tg = stale_dir / "tg.exe"
    stale_tg.write_text("broken tensor-grep launcher", encoding="utf-8")
    stale_python = stale_dir.parent / "python.exe"
    stale_python.write_text("", encoding="utf-8")
    package_location = stale_dir.parent / "Lib" / "site-packages"
    package_launcher = os.path.relpath(stale_tg, package_location)
    calls: list[list[str]] = []

    def _fake_candidate_version(path):
        candidate = Path(path)
        if candidate == native_binary:
            return "tg 0.33.0"
        if candidate == stale_tg:
            return None
        return None

    def _fake_run(cmd, capture_output=True, text=True, timeout=None, **_kwargs):
        command = [str(part) for part in cmd]
        calls.append(command)
        if command[:5] == [str(stale_python), "-m", "pip", "show", "-f"]:
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout=(
                    "Name: tensor-grep\n"
                    "Version: 0.32.0\n"
                    f"Location: {package_location}\n"
                    "Files:\n"
                    f"{package_launcher}\n"
                ),
                stderr="",
            )
        if command[:4] == [str(stale_python), "-m", "pip", "uninstall"]:
            stale_tg.unlink(missing_ok=True)
            return subprocess.CompletedProcess(cmd, 0, stdout="uninstalled\n", stderr="")
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("PATH", f"{stale_dir};{native_binary.parent}")
    monkeypatch.setattr(cli_main, "_doctor_fresh_shell_path_value", lambda: str(stale_dir))
    monkeypatch.setattr(cli_main, "_doctor_tg_candidate_version", _fake_candidate_version)
    monkeypatch.setattr("subprocess.run", _fake_run)

    message = cli_main._remove_windows_stale_tensor_grep_python_launchers(
        "0.33.0",
        native_binary,
    )

    assert message is not None
    assert not stale_tg.exists()
    assert [str(stale_python), "-m", "pip", "show", "-f", "tensor-grep"] in calls
    assert [str(stale_python), "-m", "pip", "uninstall", "-y", "tensor-grep"] in calls


def test_upgrade_detects_owned_python_scripts_launcher_without_python_named_root(
    monkeypatch,
    tmp_path,
):
    native_binary = tmp_path / ".tensor-grep" / "bin" / "tg.exe"
    stale_dir = tmp_path / "miniconda3" / "Scripts"
    native_binary.parent.mkdir(parents=True)
    stale_dir.mkdir(parents=True)
    native_binary.write_text("managed native", encoding="utf-8")
    stale_tg = stale_dir / "tg.exe"
    stale_tg.write_text("stale tensor-grep launcher", encoding="utf-8")
    stale_python = stale_dir.parent / "python.exe"
    stale_python.write_text("", encoding="utf-8")
    package_location = stale_dir.parent / "Lib" / "site-packages"
    package_launcher = os.path.relpath(stale_tg, package_location)

    def _fake_candidate_version(path):
        candidate = Path(path)
        if candidate == native_binary:
            return "tg 0.33.0"
        if candidate == stale_tg:
            return "tensor-grep 0.32.0"
        return None

    def _fake_run(cmd, capture_output=True, text=True, timeout=None, **_kwargs):
        command = [str(part) for part in cmd]
        if command[:5] == [str(stale_python), "-m", "pip", "show", "-f"]:
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout=(
                    "Name: tensor-grep\n"
                    "Version: 0.32.0\n"
                    f"Location: {package_location}\n"
                    "Files:\n"
                    f"{package_launcher}\n"
                ),
                stderr="",
            )
        if command[:4] == [str(stale_python), "-m", "pip", "uninstall"]:
            stale_tg.unlink(missing_ok=True)
            return subprocess.CompletedProcess(cmd, 0, stdout="uninstalled\n", stderr="")
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("PATH", f"{stale_dir};{native_binary.parent}")
    monkeypatch.setattr(cli_main, "_doctor_fresh_shell_path_value", lambda: "")
    monkeypatch.setattr(cli_main, "_doctor_tg_candidate_version", _fake_candidate_version)
    monkeypatch.setattr("subprocess.run", _fake_run)

    message = cli_main._remove_windows_stale_tensor_grep_python_launchers(
        "0.33.0",
        native_binary,
    )

    assert message is not None
    assert not stale_tg.exists()


def test_upgrade_does_not_unlink_owned_python_launcher_when_uninstall_fails(
    monkeypatch,
    tmp_path,
):
    native_binary = tmp_path / ".tensor-grep" / "bin" / "tg.exe"
    stale_dir = tmp_path / "Python314" / "Scripts"
    native_binary.parent.mkdir(parents=True)
    stale_dir.mkdir(parents=True)
    native_binary.write_text("managed native", encoding="utf-8")
    stale_tg = stale_dir / "tg.exe"
    stale_tg.write_text("stale tensor-grep launcher", encoding="utf-8")
    stale_python = stale_dir.parent / "python.exe"
    stale_python.write_text("", encoding="utf-8")
    package_location = stale_dir.parent / "Lib" / "site-packages"
    package_launcher = os.path.relpath(stale_tg, package_location)

    def _fake_candidate_version(path):
        candidate = Path(path)
        if candidate == native_binary:
            return "tg 0.33.0"
        if candidate == stale_tg:
            return "tensor-grep 0.32.0"
        return None

    def _fake_run(cmd, capture_output=True, text=True, timeout=None, **_kwargs):
        command = [str(part) for part in cmd]
        if command[:5] == [str(stale_python), "-m", "pip", "show", "-f"]:
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout=(
                    "Name: tensor-grep\n"
                    "Version: 0.32.0\n"
                    f"Location: {package_location}\n"
                    "Files:\n"
                    f"{package_launcher}\n"
                ),
                stderr="",
            )
        if command[:4] == [str(stale_python), "-m", "pip", "uninstall"]:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="permission denied\n")
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("PATH", f"{stale_dir};{native_binary.parent}")
    monkeypatch.setattr(cli_main, "_doctor_fresh_shell_path_value", lambda: "")
    monkeypatch.setattr(cli_main, "_doctor_tg_candidate_version", _fake_candidate_version)
    monkeypatch.setattr("subprocess.run", _fake_run)

    message = cli_main._remove_windows_stale_tensor_grep_python_launchers(
        "0.33.0",
        native_binary,
    )

    assert message is not None
    assert "WARNING: stale tensor-grep Python package launchers remain" in message
    assert "permission denied" in message
    assert stale_tg.exists()


def test_upgrade_does_not_remove_unowned_broken_python_scripts_launcher(
    monkeypatch,
    tmp_path,
):
    native_binary = tmp_path / ".tensor-grep" / "bin" / "tg.exe"
    tool_dir = tmp_path / "Python314" / "Scripts"
    native_binary.parent.mkdir(parents=True)
    tool_dir.mkdir(parents=True)
    native_binary.write_text("managed native", encoding="utf-8")
    tool_tg = tool_dir / "tg.exe"
    tool_tg.write_text("foreign broken launcher", encoding="utf-8")
    tool_python = tool_dir.parent / "python.exe"
    tool_python.write_text("", encoding="utf-8")
    calls: list[list[str]] = []

    def _fake_candidate_version(path):
        candidate = Path(path)
        if candidate == native_binary:
            return "tg 0.33.0"
        if candidate == tool_tg:
            return None
        return None

    def _fake_run(cmd, capture_output=True, text=True, timeout=None, **_kwargs):
        command = [str(part) for part in cmd]
        calls.append(command)
        if command[:5] == [str(tool_python), "-m", "pip", "show", "-f"]:
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout=(
                    "Name: tensor-grep\n"
                    "Version: 0.32.0\n"
                    f"Location: {tool_dir.parent / 'Lib' / 'site-packages'}\n"
                    "Files:\n"
                    "tensor_grep\\__main__.py\n"
                ),
                stderr="",
            )
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("PATH", f"{tool_dir};{native_binary.parent}")
    monkeypatch.setattr(cli_main, "_doctor_fresh_shell_path_value", lambda: str(tool_dir))
    monkeypatch.setattr(cli_main, "_doctor_tg_candidate_version", _fake_candidate_version)
    monkeypatch.setattr("subprocess.run", _fake_run)

    message = cli_main._remove_windows_stale_tensor_grep_python_launchers(
        "0.33.0",
        native_binary,
    )

    assert message is not None
    assert "package ownership could not be verified" in message
    assert tool_tg.exists()
    assert [str(tool_python), "-m", "pip", "uninstall", "-y", "tensor-grep"] not in calls


def test_upgrade_ignores_foreign_python_scripts_launcher(
    monkeypatch,
    tmp_path,
):
    native_binary = tmp_path / ".tensor-grep" / "bin" / "tg.exe"
    tool_dir = tmp_path / "Python314" / "Scripts"
    native_binary.parent.mkdir(parents=True)
    tool_dir.mkdir(parents=True)
    native_binary.write_text("managed native", encoding="utf-8")
    tool_tg = tool_dir / "tg.exe"
    tool_tg.write_text("foreign launcher", encoding="utf-8")
    tool_python = tool_dir.parent / "python.exe"
    tool_python.write_text("", encoding="utf-8")
    calls: list[list[str]] = []

    def _fake_candidate_version(path):
        candidate = Path(path)
        if candidate == native_binary:
            return "tg 0.33.0"
        if candidate == tool_tg:
            return "together 0.32.0"
        return None

    def _fake_run(cmd, capture_output=True, text=True, timeout=None, **_kwargs):
        command = [str(part) for part in cmd]
        calls.append(command)
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("PATH", f"{tool_dir};{native_binary.parent}")
    monkeypatch.setattr(cli_main, "_doctor_fresh_shell_path_value", lambda: str(tool_dir))
    monkeypatch.setattr(cli_main, "_doctor_tg_candidate_version", _fake_candidate_version)
    monkeypatch.setattr("subprocess.run", _fake_run)

    message = cli_main._remove_windows_stale_tensor_grep_python_launchers(
        "0.33.0",
        native_binary,
    )

    assert message is None
    assert tool_tg.exists()
    assert not list(tool_dir.glob("tg.exe.orphaned-tensor-grep-*.bak"))
    assert calls == []


def test_upgrade_backs_up_readable_unowned_tensor_grep_python_scripts_launcher(
    monkeypatch,
    tmp_path,
):
    native_binary = tmp_path / ".tensor-grep" / "bin" / "tg.exe"
    tool_dir = tmp_path / "Python314" / "Scripts"
    native_binary.parent.mkdir(parents=True)
    tool_dir.mkdir(parents=True)
    native_binary.write_text("managed native", encoding="utf-8")
    tool_tg = tool_dir / "tg.exe"
    tool_tg.write_text("manually copied tensor-grep-looking launcher", encoding="utf-8")
    tool_python = tool_dir.parent / "python.exe"
    tool_python.write_text("", encoding="utf-8")
    calls: list[list[str]] = []

    def _fake_candidate_version(path):
        candidate = Path(path)
        if candidate == native_binary:
            return "tg 0.33.0"
        if candidate == tool_tg:
            return "tensor-grep 0.32.0"
        return None

    def _fake_run(cmd, capture_output=True, text=True, timeout=None, **_kwargs):
        command = [str(part) for part in cmd]
        calls.append(command)
        if command[:5] == [str(tool_python), "-m", "pip", "show", "-f"]:
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout=(
                    "Name: tensor-grep\n"
                    "Version: 0.32.0\n"
                    f"Location: {tool_dir.parent / 'Lib' / 'site-packages'}\n"
                    "Files:\n"
                    "tensor_grep\\__main__.py\n"
                ),
                stderr="",
            )
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("PATH", f"{tool_dir};{native_binary.parent}")
    monkeypatch.setattr(cli_main, "_doctor_fresh_shell_path_value", lambda: str(tool_dir))
    monkeypatch.setattr(cli_main, "_doctor_tg_candidate_version", _fake_candidate_version)
    monkeypatch.setattr("subprocess.run", _fake_run)

    message = cli_main._remove_windows_stale_tensor_grep_python_launchers(
        "0.33.0",
        native_binary,
    )

    assert message is not None
    assert "Backed up orphaned tensor-grep Python Scripts launchers" in message
    assert not tool_tg.exists()
    backups = list(tool_dir.glob("tg.exe.orphaned-tensor-grep-*.bak"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == "manually copied tensor-grep-looking launcher"
    assert [str(tool_python), "-m", "pip", "show", "-f", "tensor-grep"] in calls
    assert [str(tool_python), "-m", "pip", "uninstall", "-y", "tensor-grep"] not in calls


def test_managed_frontdoor_refresh_runs_stale_python_launcher_cleanup(
    monkeypatch,
    tmp_path,
):
    install_dir = tmp_path / ".tensor-grep"
    python_executable = install_dir / ".venv" / "Scripts" / "python.exe"
    native_binary = install_dir / "bin" / "tg.exe"
    python_executable.parent.mkdir(parents=True)
    native_binary.parent.mkdir(parents=True)
    python_executable.write_text("", encoding="utf-8")
    native_binary.write_text("managed native", encoding="utf-8")
    cleanup_calls: list[tuple[str, Path]] = []

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "executable", str(python_executable))
    monkeypatch.setattr(cli_main, "_native_tg_version", lambda path: "tg 0.33.0")
    monkeypatch.setattr(cli_main, "_ensure_windows_managed_native_first_on_path", lambda path: None)
    monkeypatch.setattr(cli_main, "_windows_stale_tensor_grep_com_bridges", lambda *_args: [])
    monkeypatch.setattr(cli_main, "_refresh_windows_tensor_grep_com_bridges", lambda *_args: [])

    def _fake_cleanup(expected_version, native_path):
        cleanup_calls.append((expected_version, native_path))
        return "Removed stale tensor-grep Python package launchers from PATH:\n- stale"

    monkeypatch.setattr(
        cli_main,
        "_remove_windows_stale_tensor_grep_python_launchers",
        _fake_cleanup,
    )

    message = cli_main._refresh_managed_native_frontdoor("0.33.0")

    assert cleanup_calls == [("0.33.0", native_binary)]
    assert message is not None
    assert "Removed stale tensor-grep Python package launchers" in message


def test_managed_frontdoor_refresh_uses_default_install_when_upgrade_runs_from_external_python(
    monkeypatch,
    tmp_path,
):
    install_dir = tmp_path / ".tensor-grep"
    external_python = tmp_path / "Python314" / "python.exe"
    native_binary = install_dir / "bin" / "tg.exe"
    external_python.parent.mkdir(parents=True)
    native_binary.parent.mkdir(parents=True)
    external_python.write_text("", encoding="utf-8")
    native_binary.write_text("managed native", encoding="utf-8")
    cleanup_calls: list[tuple[str, Path]] = []

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "executable", str(external_python))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.delenv("TG_NATIVE_TG_BINARY", raising=False)
    monkeypatch.delenv("TG_SIDECAR_PYTHON", raising=False)
    monkeypatch.setattr(cli_main, "_native_tg_version", lambda path: "tg 0.33.0")
    monkeypatch.setattr(cli_main, "_ensure_windows_managed_native_first_on_path", lambda path: None)
    monkeypatch.setattr(cli_main, "_windows_stale_tensor_grep_com_bridges", lambda *_args: [])
    monkeypatch.setattr(cli_main, "_refresh_windows_tensor_grep_com_bridges", lambda *_args: [])

    def _fake_cleanup(expected_version, native_path):
        cleanup_calls.append((expected_version, native_path))
        return "Removed stale tensor-grep Python package launchers from PATH:\n- stale"

    monkeypatch.setattr(
        cli_main,
        "_remove_windows_stale_tensor_grep_python_launchers",
        _fake_cleanup,
    )

    message = cli_main._refresh_managed_native_frontdoor("0.33.0")

    assert cleanup_calls == [("0.33.0", native_binary)]
    assert message is not None
    assert "Removed stale tensor-grep Python package launchers" in message


def test_repair_launcher_requires_explicit_foreign_rename(monkeypatch, tmp_path):
    install_dir = tmp_path / ".tensor-grep"
    native_binary = install_dir / "bin" / "tg.exe"
    foreign_dir = tmp_path / "MachinePython314" / "Scripts"
    native_binary.parent.mkdir(parents=True)
    foreign_dir.mkdir(parents=True)
    native_binary.write_text("managed native", encoding="utf-8")
    foreign_tg = foreign_dir / "tg.exe"
    foreign_tg.write_text("Together CLI", encoding="utf-8")

    def _fake_candidate_version(path):
        text = Path(path).read_text(encoding="utf-8")
        if text == "managed native":
            return "tg 0.33.0"
        if text == "Together CLI":
            return "Together CLI (v2.12.0)"
        return None

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("PATH", f"{foreign_dir};{native_binary.parent}")
    monkeypatch.setattr(cli_main, "resolve_native_tg_binary", lambda: native_binary)
    monkeypatch.setattr(cli_main, "_doctor_installed_version", lambda: "0.33.0")
    monkeypatch.setattr(cli_main, "_doctor_tg_candidate_version", _fake_candidate_version)

    blocked = cli_main._repair_windows_python_subprocess_launcher(allow_foreign_rename=False)

    assert blocked["status"] == "blocked_requires_allow_foreign_rename"
    assert foreign_tg.read_text(encoding="utf-8") == "Together CLI"
    assert "allow-foreign-rename" in str(blocked["message"])

    repaired = cli_main._repair_windows_python_subprocess_launcher(allow_foreign_rename=True)

    assert repaired["status"] == "repaired"
    assert Path(str(repaired["replaced_path"])) == foreign_tg
    backup_path = Path(str(repaired["backup_path"]))
    assert backup_path.is_file()
    assert backup_path.read_text(encoding="utf-8") == "Together CLI"
    assert foreign_tg.read_text(encoding="utf-8") == "managed native"
    assert repaired["post_repair_version"] == "tg 0.33.0"


def test_repair_launcher_removes_owned_python_scripts_entrypoint(monkeypatch, tmp_path):
    install_dir = tmp_path / ".tensor-grep"
    native_binary = install_dir / "bin" / "tg.exe"
    scripts_dir = tmp_path / "Python314" / "Scripts"
    native_binary.parent.mkdir(parents=True)
    scripts_dir.mkdir(parents=True)
    native_binary.write_text("managed native", encoding="utf-8")
    python_tg = scripts_dir / "tg.exe"
    python_tg.write_text("tensor-grep console launcher", encoding="utf-8")
    python_executable = scripts_dir.parent / "python.exe"
    python_executable.write_text("", encoding="utf-8")
    package_location = scripts_dir.parent / "Lib" / "site-packages"
    package_launcher = os.path.relpath(python_tg, package_location)
    calls: list[list[str]] = []

    def _fake_candidate_version(path):
        candidate = Path(path)
        if candidate == native_binary:
            return "tg 0.33.0"
        if candidate == python_tg:
            return "tensor-grep 0.33.0"
        return None

    def _fake_run(cmd, capture_output=True, text=True, timeout=None, **_kwargs):
        command = [str(part) for part in cmd]
        calls.append(command)
        if command[:5] == [str(python_executable), "-m", "pip", "show", "-f"]:
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout=(
                    "Name: tensor-grep\n"
                    "Version: 0.33.0\n"
                    f"Location: {package_location}\n"
                    "Files:\n"
                    f"{package_launcher}\n"
                ),
                stderr="",
            )
        if command[:4] == [str(python_executable), "-m", "pip", "uninstall"]:
            python_tg.unlink(missing_ok=True)
            return subprocess.CompletedProcess(cmd, 0, stdout="uninstalled\n", stderr="")
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("PATH", f"{scripts_dir};{native_binary.parent}")
    monkeypatch.setattr(cli_main, "resolve_native_tg_binary", lambda: native_binary)
    monkeypatch.setattr(cli_main, "_doctor_installed_version", lambda: "0.33.0")
    monkeypatch.setattr(cli_main, "_doctor_tg_candidate_version", _fake_candidate_version)
    monkeypatch.setattr(cli_main, "_doctor_fresh_shell_path_value", lambda: "")
    monkeypatch.setattr("subprocess.run", _fake_run)

    repaired = cli_main._repair_windows_python_subprocess_launcher(allow_foreign_rename=False)

    assert repaired["status"] == "repaired"
    assert repaired["replaced_path"] == str(python_tg)
    assert repaired["post_repair_version"] == "tg 0.33.0"
    assert not python_tg.exists()
    assert [str(python_executable), "-m", "pip", "uninstall", "-y", "tensor-grep"] in calls


def test_repair_launcher_backs_up_orphaned_tensor_grep_python_scripts_entrypoint(
    monkeypatch,
    tmp_path,
):
    install_dir = tmp_path / ".tensor-grep"
    native_binary = install_dir / "bin" / "tg.exe"
    scripts_dir = tmp_path / "Python314" / "Scripts"
    native_binary.parent.mkdir(parents=True)
    scripts_dir.mkdir(parents=True)
    native_binary.write_text("managed native", encoding="utf-8")
    python_tg = scripts_dir / "tg.exe"
    python_tg.write_text("orphaned tensor-grep launcher", encoding="utf-8")
    python_executable = scripts_dir.parent / "python.exe"
    python_executable.write_text("", encoding="utf-8")

    def _fake_candidate_version(path):
        candidate = Path(path)
        if candidate == native_binary:
            return "tg 0.33.0"
        if candidate == python_tg:
            return "tensor-grep 0.32.0"
        return None

    def _fake_run(cmd, capture_output=True, text=True, timeout=None, **_kwargs):
        command = [str(part) for part in cmd]
        if command[:5] == [str(python_executable), "-m", "pip", "show", "-f"]:
            return subprocess.CompletedProcess(
                cmd,
                1,
                stdout="",
                stderr="WARNING: Package(s) not found: tensor-grep\n",
            )
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("PATH", f"{scripts_dir};{native_binary.parent}")
    monkeypatch.setattr(cli_main, "resolve_native_tg_binary", lambda: native_binary)
    monkeypatch.setattr(cli_main, "_doctor_installed_version", lambda: "0.33.0")
    monkeypatch.setattr(cli_main, "_doctor_tg_candidate_version", _fake_candidate_version)
    monkeypatch.setattr(cli_main, "_doctor_fresh_shell_path_value", lambda: "")
    monkeypatch.setattr("subprocess.run", _fake_run)

    repaired = cli_main._repair_windows_python_subprocess_launcher(allow_foreign_rename=False)

    assert repaired["status"] == "repaired"
    assert repaired["replaced_path"] == str(python_tg)
    assert repaired["post_repair_version"] == "tg 0.33.0"
    assert not python_tg.exists()
    backups = list(scripts_dir.glob("tg.exe.orphaned-tensor-grep-*.bak"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == "orphaned tensor-grep launcher"


def test_repair_launcher_command_emits_json_and_nonzero_when_blocked(
    monkeypatch,
    tmp_path,
):
    native_binary = tmp_path / ".tensor-grep" / "bin" / "tg.exe"
    foreign_tg = tmp_path / "Python314" / "Scripts" / "tg.exe"
    native_binary.parent.mkdir(parents=True)
    foreign_tg.parent.mkdir(parents=True)
    native_binary.write_text("managed native", encoding="utf-8")
    foreign_tg.write_text("Together CLI", encoding="utf-8")

    def _fake_candidate_version(path):
        return "Together CLI (v2.12.0)" if Path(path) == foreign_tg else "tg 0.33.0"

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("PATH", f"{foreign_tg.parent};{native_binary.parent}")
    monkeypatch.setattr(cli_main, "resolve_native_tg_binary", lambda: native_binary)
    monkeypatch.setattr(cli_main, "_doctor_installed_version", lambda: "0.33.0")
    monkeypatch.setattr(cli_main, "_doctor_tg_candidate_version", _fake_candidate_version)

    result = CliRunner().invoke(app, ["repair-launcher", "--json"])

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["status"] == "blocked_requires_allow_foreign_rename"
    assert payload["foreign_path"] == str(foreign_tg.resolve())
    assert "allow-foreign-rename" in payload["message"]


def test_upgrade_does_not_treat_repo_dev_venv_as_managed_frontdoor(monkeypatch, tmp_path):
    from tensor_grep.cli import main as cli_main

    project = tmp_path / "project"
    python_executable = project / ".venv" / "Scripts" / "python.exe"
    native_binary = project / "bin" / "tg.exe"
    python_executable.parent.mkdir(parents=True)
    native_binary.parent.mkdir(parents=True)
    python_executable.write_text("", encoding="utf-8")
    native_binary.write_text("", encoding="utf-8")

    monkeypatch.setattr("sys.executable", str(python_executable))
    monkeypatch.setattr(sys, "platform", "win32")

    assert cli_main._managed_native_frontdoor_path_from_env() is None


def test_upgrade_refreshes_stale_tensor_grep_com_bridge_after_native_update(monkeypatch, tmp_path):
    install_dir = tmp_path / ".tensor-grep"
    python_executable = install_dir / ".venv" / "Scripts" / "python.exe"
    native_binary = install_dir / "bin" / "tg.exe"
    bridge_tg = tmp_path / "Python314" / "Scripts" / "tg.com"
    repaired_tg = tmp_path / "MachinePython314" / "Scripts" / "tg.exe"
    foreign_tg = tmp_path / "ForeignPython" / "Scripts" / "tg.com"
    python_executable.parent.mkdir(parents=True)
    native_binary.parent.mkdir(parents=True)
    bridge_tg.parent.mkdir(parents=True)
    repaired_tg.parent.mkdir(parents=True)
    foreign_tg.parent.mkdir(parents=True)
    python_executable.write_text("", encoding="utf-8")
    native_binary.write_text("old native", encoding="utf-8")
    bridge_tg.write_text("old native", encoding="utf-8")
    repaired_tg.write_text("old native", encoding="utf-8")
    foreign_tg.write_text("foreign", encoding="utf-8")

    def _fake_run(cmd, capture_output=True, text=True, check=True, timeout=None, env=None):
        command = [str(part) for part in cmd]
        if command[0] == "uv":
            return subprocess.CompletedProcess(cmd, 0, stdout="Installed 1 package", stderr="")
        if command[:2] == [str(python_executable), "-c"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="0.33.0\n", stderr="")
        if command[0] in {str(native_binary), str(bridge_tg), str(repaired_tg)}:
            version = (
                "0.33.0"
                if Path(command[0]).read_text(encoding="utf-8") == "new native"
                else "0.32.0"
            )
            return subprocess.CompletedProcess(cmd, 0, stdout=f"tg {version}\n", stderr="")
        if command[0] == str(foreign_tg):
            return subprocess.CompletedProcess(cmd, 0, stdout="2.12.0\n", stderr="")
        if command[0].endswith(".tmp"):
            return subprocess.CompletedProcess(cmd, 0, stdout="tg 0.33.0\n", stderr="")
        raise AssertionError(f"unexpected command: {command}")

    def _fake_urlretrieve(url, filename):
        Path(filename).write_text("new native", encoding="utf-8")
        return filename, None

    monkeypatch.setattr("sys.executable", str(python_executable))
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv(
        "PATH",
        os.pathsep.join([str(bridge_tg.parent), str(repaired_tg.parent), str(foreign_tg.parent)]),
    )
    monkeypatch.setattr("platform.machine", lambda: "AMD64")
    monkeypatch.setattr("importlib.metadata.version", lambda _name: "0.32.0")
    monkeypatch.setattr("subprocess.run", _fake_run)
    monkeypatch.setattr("urllib.request.urlretrieve", _fake_urlretrieve)
    monkeypatch.setattr(
        "tensor_grep.cli.main._latest_pypi_tensor_grep_version",
        lambda: "0.33.0",
        raising=False,
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_fresh_shell_path_value",
        lambda: str(bridge_tg.parent),
        raising=False,
    )

    runner = CliRunner()
    result = runner.invoke(app, ["upgrade"])

    assert result.exit_code == 0
    assert native_binary.read_text(encoding="utf-8") == "new native"
    assert bridge_tg.read_text(encoding="utf-8") == "new native"
    assert repaired_tg.read_text(encoding="utf-8") == "new native"
    assert foreign_tg.read_text(encoding="utf-8") == "foreign"
    assert "Refreshed 2 PATH tensor-grep front-door copies to 0.33.0." in result.stdout
    assert str(bridge_tg) in result.stdout
    assert str(repaired_tg) in result.stdout


def test_upgrade_targets_current_cmd_shim_dir_for_python_subprocess_bridge(
    monkeypatch,
    tmp_path,
):
    from tensor_grep.cli import main as cli_main

    install_dir = tmp_path / ".tensor-grep"
    native_binary = install_dir / "bin" / "tg.exe"
    shim_dir = tmp_path / "bin"
    shim_cmd = shim_dir / "tg.cmd"
    native_binary.parent.mkdir(parents=True)
    shim_dir.mkdir(parents=True)
    native_binary.write_text("new native", encoding="utf-8")
    shim_cmd.write_text("@echo off\n", encoding="utf-8")

    def _fake_candidate_version(path):
        candidate = Path(path)
        if candidate in {native_binary, shim_cmd, shim_dir / "tg.exe"}:
            return "tg 0.33.0"
        return None

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.delenv("HOME", raising=False)
    monkeypatch.setenv("PATH", str(shim_dir))
    monkeypatch.setattr(cli_main, "_doctor_fresh_shell_path_value", lambda: None)
    monkeypatch.setattr(cli_main, "_doctor_tg_candidate_version", _fake_candidate_version)
    monkeypatch.setattr(cli_main, "_native_tg_version", lambda path: _fake_candidate_version(path))

    targets = cli_main._windows_stale_tensor_grep_com_bridges("0.33.0", native_binary)

    assert targets == [shim_dir / "tg.exe"]
    refreshed = cli_main._refresh_windows_tensor_grep_com_bridges(
        "0.33.0",
        native_binary,
        targets,
    )
    assert refreshed == [shim_dir / "tg.exe"]
    assert (shim_dir / "tg.exe").read_text(encoding="utf-8") == "new native"
    assert (shim_dir / "tg.exe.tensor-grep-bridge").read_text(encoding="ascii") == (
        "tensor-grep managed tg.exe bridge\n"
    )


def test_upgrade_does_not_create_python_subprocess_bridge_for_foreign_cmd(
    monkeypatch,
    tmp_path,
):
    from tensor_grep.cli import main as cli_main

    native_binary = tmp_path / ".tensor-grep" / "bin" / "tg.exe"
    shim_dir = tmp_path / "bin"
    foreign_cmd = shim_dir / "tg.cmd"
    native_binary.parent.mkdir(parents=True)
    shim_dir.mkdir(parents=True)
    native_binary.write_text("new native", encoding="utf-8")
    foreign_cmd.write_text("@echo off\n", encoding="utf-8")

    def _fake_candidate_version(path):
        if Path(path) == foreign_cmd:
            return "Together CLI (v2.12.0)"
        if Path(path) == native_binary:
            return "tg 0.33.0"
        return None

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.delenv("HOME", raising=False)
    monkeypatch.setenv("PATH", str(shim_dir))
    monkeypatch.setattr(cli_main, "_doctor_fresh_shell_path_value", lambda: None)
    monkeypatch.setattr(cli_main, "_doctor_tg_candidate_version", _fake_candidate_version)

    targets = cli_main._windows_stale_tensor_grep_com_bridges("0.33.0", native_binary)

    assert targets == []
    assert not (shim_dir / "tg.exe").exists()


def test_upgrade_does_not_create_python_subprocess_bridge_outside_managed_shim_dirs(
    monkeypatch,
    tmp_path,
):
    from tensor_grep.cli import main as cli_main

    native_binary = tmp_path / ".tensor-grep" / "bin" / "tg.exe"
    tool_dir = tmp_path / "tools"
    wrapper_cmd = tool_dir / "tg.cmd"
    native_binary.parent.mkdir(parents=True)
    tool_dir.mkdir(parents=True)
    native_binary.write_text("new native", encoding="utf-8")
    wrapper_cmd.write_text("@echo off\n", encoding="utf-8")

    def _fake_candidate_version(path):
        if Path(path) == wrapper_cmd:
            return "tg 0.33.0"
        if Path(path) == native_binary:
            return "tg 0.33.0"
        return None

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.delenv("HOME", raising=False)
    monkeypatch.setenv("PATH", str(tool_dir))
    monkeypatch.setattr(cli_main, "_doctor_fresh_shell_path_value", lambda: None)
    monkeypatch.setattr(cli_main, "_doctor_tg_candidate_version", _fake_candidate_version)

    targets = cli_main._windows_stale_tensor_grep_com_bridges("0.33.0", native_binary)

    assert targets == []
    assert not (tool_dir / "tg.exe").exists()


def test_upgrade_refreshes_stale_com_bridge_when_native_frontdoor_is_current(monkeypatch, tmp_path):
    install_dir = tmp_path / ".tensor-grep"
    python_executable = install_dir / ".venv" / "Scripts" / "python.exe"
    native_binary = install_dir / "bin" / "tg.exe"
    bridge_tg = tmp_path / "Python314" / "Scripts" / "tg.com"
    python_executable.parent.mkdir(parents=True)
    native_binary.parent.mkdir(parents=True)
    bridge_tg.parent.mkdir(parents=True)
    python_executable.write_text("", encoding="utf-8")
    native_binary.write_text("new native", encoding="utf-8")
    bridge_tg.write_text("old native", encoding="utf-8")
    downloads: list[str] = []

    def _fake_run(cmd, capture_output=True, text=True, check=True, timeout=None, env=None):
        command = [str(part) for part in cmd]
        if command[0] == "uv":
            return subprocess.CompletedProcess(cmd, 0, stdout="Audited 1 package", stderr="")
        if command[:2] == [str(python_executable), "-c"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="0.33.0\n", stderr="")
        if command[0] in {str(native_binary), str(bridge_tg)}:
            version = (
                "0.33.0"
                if Path(command[0]).read_text(encoding="utf-8") == "new native"
                else "0.32.0"
            )
            return subprocess.CompletedProcess(cmd, 0, stdout=f"tg {version}\n", stderr="")
        raise AssertionError(f"unexpected command: {command}")

    def _fake_urlretrieve(url, filename):
        downloads.append(str(url))
        Path(filename).write_text("new native", encoding="utf-8")
        return filename, None

    monkeypatch.setattr("sys.executable", str(python_executable))
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("PATH", str(bridge_tg.parent))
    monkeypatch.setattr("importlib.metadata.version", lambda _name: "0.33.0")
    monkeypatch.setattr("subprocess.run", _fake_run)
    monkeypatch.setattr("urllib.request.urlretrieve", _fake_urlretrieve)
    monkeypatch.setattr(
        "tensor_grep.cli.main._latest_pypi_tensor_grep_version",
        lambda: "0.33.0",
        raising=False,
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_fresh_shell_path_value",
        lambda: str(bridge_tg.parent),
        raising=False,
    )

    runner = CliRunner()
    result = runner.invoke(app, ["upgrade"])

    assert result.exit_code == 0
    assert downloads == []
    assert native_binary.read_text(encoding="utf-8") == "new native"
    assert bridge_tg.read_text(encoding="utf-8") == "new native"
    assert "tensor-grep is already at the latest PyPI version (0.33.0)." in result.stdout
    assert "Refreshed 1 PATH tg.com bridge to 0.33.0." in result.stdout


def test_upgrade_refreshes_stale_native_frontdoor_when_python_package_is_latest(
    monkeypatch, tmp_path
):
    install_dir = tmp_path / ".tensor-grep"
    python_executable = install_dir / ".venv" / "Scripts" / "python.exe"
    native_binary = install_dir / "bin" / "tg.exe"
    python_executable.parent.mkdir(parents=True)
    native_binary.parent.mkdir(parents=True)
    python_executable.write_text("", encoding="utf-8")
    native_binary.write_text("old native", encoding="utf-8")
    downloads: list[str] = []

    def _fake_run(cmd, capture_output=True, text=True, check=True, timeout=None):
        command = [str(part) for part in cmd]
        if command[0] == "uv":
            return subprocess.CompletedProcess(cmd, 0, stdout="Audited 1 package", stderr="")
        if command[:2] == [str(python_executable), "-c"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="0.33.0\n", stderr="")
        if command[0] == str(native_binary):
            version = (
                "0.33.0" if native_binary.read_text(encoding="utf-8") == "new native" else "0.32.0"
            )
            return subprocess.CompletedProcess(cmd, 0, stdout=f"tg {version}\n", stderr="")
        if command[0].endswith(".tmp"):
            return subprocess.CompletedProcess(cmd, 0, stdout="tg 0.33.0\n", stderr="")
        raise AssertionError(f"unexpected command: {command}")

    def _fake_urlretrieve(url, filename):
        downloads.append(str(url))
        Path(filename).write_text("new native", encoding="utf-8")
        return filename, None

    monkeypatch.setattr("sys.executable", str(python_executable))
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr("platform.machine", lambda: "AMD64")
    monkeypatch.setenv("TENSOR_GREP_NATIVE_FRONTDOOR_FLAVOR", "cpu")
    monkeypatch.setattr("importlib.metadata.version", lambda _name: "0.33.0")
    monkeypatch.setattr("subprocess.run", _fake_run)
    monkeypatch.setattr("urllib.request.urlretrieve", _fake_urlretrieve)
    monkeypatch.setattr(
        "tensor_grep.cli.main._latest_pypi_tensor_grep_version",
        lambda: "0.33.0",
        raising=False,
    )

    runner = CliRunner()
    result = runner.invoke(app, ["upgrade"])

    assert result.exit_code == 0
    assert downloads
    assert native_binary.read_text(encoding="utf-8") == "new native"
    assert "tensor-grep is already at the latest PyPI version (0.33.0)." in result.stdout
    assert "Native tg front door refreshed to 0.33.0." in result.stdout


def test_upgrade_schedules_native_frontdoor_refresh_when_windows_exe_is_locked(
    monkeypatch, tmp_path
):
    install_dir = tmp_path / ".tensor-grep"
    python_executable = install_dir / ".venv" / "Scripts" / "python.exe"
    native_binary = install_dir / "bin" / "tg.exe"
    bridge_tg = tmp_path / "Python314" / "Scripts" / "tg.com"
    python_executable.parent.mkdir(parents=True)
    native_binary.parent.mkdir(parents=True)
    bridge_tg.parent.mkdir(parents=True)
    python_executable.write_text("", encoding="utf-8")
    native_binary.write_text("old native", encoding="utf-8")
    bridge_tg.write_text("old native", encoding="utf-8")
    popen_calls: list[list[str]] = []

    class _LockedExeError(PermissionError):
        winerror = 32

    def _fake_run(cmd, capture_output=True, text=True, check=True, timeout=None, env=None):
        command = [str(part) for part in cmd]
        if command[0] == "uv":
            return subprocess.CompletedProcess(cmd, 0, stdout="Audited 1 package", stderr="")
        if command[:2] == [str(python_executable), "-c"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="0.33.0\n", stderr="")
        if command[0] == str(native_binary):
            return subprocess.CompletedProcess(cmd, 0, stdout="tg 0.32.0\n", stderr="")
        if command[0] == str(bridge_tg):
            return subprocess.CompletedProcess(cmd, 0, stdout="tg 0.32.0\n", stderr="")
        if command[0].endswith(".tmp"):
            return subprocess.CompletedProcess(cmd, 0, stdout="tg 0.33.0\n", stderr="")
        raise AssertionError(f"unexpected command: {command}")

    def _fake_urlretrieve(url, filename):
        Path(filename).write_text("new native", encoding="utf-8")
        return filename, None

    def _fake_replace(src, dst):
        if Path(dst) == native_binary:
            raise _LockedExeError("The process cannot access the file")
        os.replace(src, dst)

    class _FakePopen:
        def __init__(
            self,
            cmd,
            stdout=None,
            stderr=None,
            stdin=None,
            close_fds=None,
            creationflags=0,
        ):
            popen_calls.append([str(part) for part in cmd])

    monkeypatch.setattr("sys.executable", str(python_executable))
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("PATH", str(bridge_tg.parent))
    monkeypatch.setattr("platform.machine", lambda: "AMD64")
    monkeypatch.setenv("TENSOR_GREP_NATIVE_FRONTDOOR_FLAVOR", "nvidia")
    monkeypatch.setattr("importlib.metadata.version", lambda _name: "0.33.0")
    monkeypatch.setattr("subprocess.run", _fake_run)
    monkeypatch.setattr("subprocess.Popen", _FakePopen)
    monkeypatch.setattr("urllib.request.urlretrieve", _fake_urlretrieve)
    monkeypatch.setattr("tensor_grep.cli.main.os.replace", _fake_replace)
    monkeypatch.setattr(
        "tensor_grep.cli.main._latest_pypi_tensor_grep_version",
        lambda: "0.33.0",
        raising=False,
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_fresh_shell_path_value",
        lambda: str(bridge_tg.parent),
        raising=False,
    )

    runner = CliRunner()
    result = runner.invoke(app, ["upgrade"])

    assert result.exit_code == 0
    assert native_binary.read_text(encoding="utf-8") == "old native"
    assert popen_calls
    assert "urlretrieve" in popen_calls[0][2]
    assert "0.33.0" in popen_calls[0]
    helper_assets = json.loads(popen_calls[0][7])
    assert helper_assets == [
        {
            "url": (
                "https://github.com/oimiragieo/tensor-grep/releases/download/v0.33.0/"
                "tg-windows-amd64-nvidia.exe"
            ),
            "flavor": "nvidia",
            "asset_name": "tg-windows-amd64-nvidia.exe",
            "requested_flavor": "nvidia",
        },
        {
            "url": (
                "https://github.com/oimiragieo/tensor-grep/releases/download/v0.33.0/"
                "tg-windows-amd64-cpu.exe"
            ),
            "flavor": "cpu",
            "asset_name": "tg-windows-amd64-cpu.exe",
            "requested_flavor": "nvidia",
        },
    ]
    assert json.loads(popen_calls[0][8]) == [str(bridge_tg)]
    assert "Native tg front door refresh scheduled for 0.33.0." in result.stdout


def test_managed_native_frontdoor_path_uses_unix_native_binary_when_env_is_absent(
    monkeypatch, tmp_path
):

    install_dir = tmp_path / ".tensor-grep"
    python_executable = install_dir / ".venv" / "bin" / "python"
    python_executable.parent.mkdir(parents=True)
    python_executable.write_text("", encoding="utf-8")

    monkeypatch.delenv("TG_NATIVE_TG_BINARY", raising=False)
    monkeypatch.delenv("TG_SIDECAR_PYTHON", raising=False)
    monkeypatch.setattr("sys.executable", str(python_executable))
    monkeypatch.setattr(sys, "platform", "linux")

    assert cli_main._managed_native_frontdoor_path_from_env() == install_dir / "bin" / "tg-native"


def test_upgrade_falls_back_to_ensurepip_then_pip(monkeypatch):
    calls: list[list[str]] = []
    pip_attempts = {"count": 0}

    def _fake_run(cmd, capture_output=True, text=True, check=True):
        calls.append(list(cmd))
        if cmd[0] == "uv":
            raise FileNotFoundError("uv not found")
        if cmd[:3] == ["python", "-m", "ensurepip"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="ensurepip ok", stderr="")
        if cmd[:3] == ["python", "-m", "pip"]:
            pip_attempts["count"] += 1
            if pip_attempts["count"] == 1:
                raise subprocess.CalledProcessError(
                    returncode=1, cmd=cmd, stderr="No module named pip"
                )
            return subprocess.CompletedProcess(cmd, 0, stdout="Successfully installed", stderr="")
        if cmd[:2] == ["python", "-c"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="0.32.0\n", stderr="")
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr("sys.executable", "python")
    versions = iter(["0.31.0", "0.32.0"])

    monkeypatch.setattr("importlib.metadata.version", lambda _name: next(versions))
    monkeypatch.setattr("subprocess.run", _fake_run)
    monkeypatch.setattr(
        "tensor_grep.cli.main._latest_pypi_tensor_grep_version",
        lambda: "0.32.0",
        raising=False,
    )

    runner = CliRunner()
    result = runner.invoke(app, ["upgrade"])

    assert result.exit_code == 0
    assert any(cmd[:3] == ["python", "-m", "ensurepip"] for cmd in calls)
    assert pip_attempts["count"] == 2
    assert "Successfully upgraded tensor-grep via pip+ensurepip!" in result.stdout


def test_upgrade_fails_when_post_upgrade_python_cannot_import_tensor_grep(monkeypatch):
    calls: list[list[str]] = []

    def _fake_run(cmd, capture_output=True, text=True, check=True):
        calls.append(list(cmd))
        if cmd[0] == "uv":
            return subprocess.CompletedProcess(cmd, 0, stdout="Installed 1 package", stderr="")
        if cmd[:2] == ["python", "-c"]:
            raise subprocess.CalledProcessError(
                returncode=1,
                cmd=cmd,
                stderr="No module named tensor_grep",
            )
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr("sys.executable", "python")
    monkeypatch.setattr("importlib.metadata.version", lambda _name: "0.32.0")
    monkeypatch.setattr("subprocess.run", _fake_run)
    monkeypatch.setattr(
        "tensor_grep.cli.main._latest_pypi_tensor_grep_version",
        lambda: "0.33.0",
        raising=False,
    )

    runner = CliRunner()
    result = runner.invoke(app, ["upgrade"])

    assert result.exit_code == 1
    assert any("tensor-grep==0.33.0" in cmd for cmd in calls)
    assert "post-upgrade verification failed" in result.output
    assert "No module named tensor_grep" in result.output
    assert "already at the latest PyPI version" not in result.output


def test_upgrade_fails_with_clear_error_messages_when_uv_and_pip_fail(monkeypatch):
    calls: list[list[str]] = []

    def _fake_run(cmd, capture_output=True, text=True, check=True):
        calls.append(list(cmd))
        if cmd[0] == "uv":
            raise FileNotFoundError("uv not found")
        if cmd[:3] == ["python", "-m", "pip"]:
            raise subprocess.CalledProcessError(
                returncode=1,
                cmd=cmd,
                stderr="network timeout while contacting package index",
            )
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr("sys.executable", "python")
    monkeypatch.setattr("subprocess.run", _fake_run)
    monkeypatch.setattr(
        "tensor_grep.cli.main._latest_pypi_tensor_grep_version",
        lambda: "0.32.0",
        raising=False,
    )

    runner = CliRunner()
    result = runner.invoke(app, ["upgrade"])

    assert result.exit_code == 1
    assert calls[0][0] == "uv"
    assert any(cmd[:3] == ["python", "-m", "pip"] for cmd in calls)
    assert "Error occurred while upgrading tensor-grep." in result.output
    assert "uv:" in result.output
    assert "pip:" in result.output
    assert "network timeout while contacting package index" in result.output


def test_upgrade_schedules_windows_helper_when_tg_exe_is_locked(monkeypatch, tmp_path):
    calls: list[list[str]] = []
    popen_calls: list[list[str]] = []

    locked_error = (
        "failed to remove file `C:\\Users\\oimir\\.tensor-grep\\.venv\\Scripts\\tg.exe`: "
        "The process cannot access the file because it is being used by another process. "
        "(os error 32)"
    )

    def _fake_run(cmd, capture_output=True, text=True, check=True):
        command = list(cmd)
        calls.append(command)
        if command[0] == "uv":
            raise subprocess.CalledProcessError(returncode=1, cmd=command, stderr=locked_error)
        if command[:3] == ["python", "-m", "pip"]:
            raise subprocess.CalledProcessError(returncode=1, cmd=command, stderr=locked_error)
        raise AssertionError(f"unexpected command: {command}")

    class _FakePopen:
        def __init__(
            self,
            cmd,
            stdout=None,
            stderr=None,
            stdin=None,
            close_fds=None,
            creationflags=0,
        ):
            popen_calls.append(list(cmd))

    monkeypatch.setattr("subprocess.run", _fake_run)
    monkeypatch.setattr("subprocess.Popen", _FakePopen)
    monkeypatch.setattr("sys.executable", "python")
    monkeypatch.setattr("importlib.metadata.version", lambda _name: "0.31.0")
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setattr(
        "tensor_grep.cli.main._latest_pypi_tensor_grep_version",
        lambda: "0.32.0",
        raising=False,
    )

    runner = CliRunner()
    result = runner.invoke(app, ["upgrade"])

    assert result.exit_code == 0
    assert calls[0][0] == "uv"
    assert any(cmd[:3] == ["python", "-m", "pip"] for cmd in calls)
    assert popen_calls
    assert popen_calls[0][0] == "python"
    assert popen_calls[0][1] == "-c"
    helper_code = popen_calls[0][2]
    assert "def _verify_installed_version" in helper_code
    assert "import tensor_grep" in helper_code
    assert "post-upgrade verification failed" in helper_code
    assert "tensor-grep==0.32.0" in popen_calls[0][5]
    assert popen_calls[0][6] == "0.32.0"
    assert "Windows is still using tg.exe" in result.output
    assert "Wait a few seconds, then run `tg --version` again." in result.output
    assert "Upgrade log:" in result.output


def test_upgrade_scheduled_windows_helper_restarts_preexisting_session_daemon(
    monkeypatch, tmp_path
):
    popen_calls: list[list[str]] = []
    daemon_root = r"C:\dev\projects\tensor-grep"
    locked_error = (
        "failed to remove file `C:\\Users\\oimir\\.tensor-grep\\.venv\\Scripts\\tg.exe`: "
        "The process cannot access the file because it is being used by another process. "
        "(os error 32)"
    )

    def _fake_run(cmd, capture_output=True, text=True, check=True):
        command = list(cmd)
        if command[0] == "uv":
            raise subprocess.CalledProcessError(returncode=1, cmd=command, stderr=locked_error)
        if command[:3] == ["python", "-m", "pip"]:
            raise subprocess.CalledProcessError(returncode=1, cmd=command, stderr=locked_error)
        raise AssertionError(f"unexpected command: {command}")

    class _FakePopen:
        def __init__(
            self,
            cmd,
            stdout=None,
            stderr=None,
            stdin=None,
            close_fds=None,
            creationflags=0,
        ):
            popen_calls.append([str(part) for part in cmd])

    monkeypatch.setattr("subprocess.run", _fake_run)
    monkeypatch.setattr("subprocess.Popen", _FakePopen)
    monkeypatch.setattr("sys.executable", "python")
    monkeypatch.setattr("importlib.metadata.version", lambda _name: "0.31.0")
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setattr(
        "tensor_grep.cli.main._latest_pypi_tensor_grep_version",
        lambda: "0.32.0",
        raising=False,
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_session_daemon_status",
        lambda _path: {"running": True, "root": daemon_root},
    )

    runner = CliRunner()
    result = runner.invoke(app, ["upgrade"])

    assert result.exit_code == 0
    assert popen_calls
    helper_code = popen_calls[0][2]
    compile(helper_code, "<scheduled-upgrade-helper>", "exec")
    assert popen_calls[0][-1] == daemon_root
    assert "def _restart_session_daemon_after_upgrade" in helper_code
    assert '"daemon"' in helper_code
    assert '"start"' in helper_code
    assert "daemon_root" in helper_code


def test_upgrade_scheduled_windows_helper_refreshes_stale_com_bridge(monkeypatch, tmp_path):
    install_dir = tmp_path / ".tensor-grep"
    python_executable = install_dir / ".venv" / "Scripts" / "python.exe"
    native_binary = install_dir / "bin" / "tg.exe"
    bridge_tg = tmp_path / "Python314" / "Scripts" / "tg.com"
    python_executable.parent.mkdir(parents=True)
    native_binary.parent.mkdir(parents=True)
    bridge_tg.parent.mkdir(parents=True)
    python_executable.write_text("", encoding="utf-8")
    native_binary.write_text("new native", encoding="utf-8")
    bridge_tg.write_text("old native", encoding="utf-8")
    popen_calls: list[list[str]] = []

    locked_error = (
        "failed to remove file `C:\\Users\\oimir\\.tensor-grep\\.venv\\Scripts\\tg.exe`: "
        "The process cannot access the file because it is being used by another process. "
        "(os error 32)"
    )

    def _fake_run(
        cmd,
        capture_output=True,
        text=True,
        check=True,
        timeout=None,
        env=None,
    ):
        command = [str(part) for part in cmd]
        if command[0] == "uv":
            raise subprocess.CalledProcessError(returncode=1, cmd=command, stderr=locked_error)
        if command[:3] == [str(python_executable), "-m", "pip"]:
            raise subprocess.CalledProcessError(returncode=1, cmd=command, stderr=locked_error)
        if command[0] == str(bridge_tg):
            return subprocess.CompletedProcess(cmd, 0, stdout="tg 0.32.0\n", stderr="")
        if command[0] == str(native_binary):
            return subprocess.CompletedProcess(cmd, 0, stdout="tg 0.33.0\n", stderr="")
        raise AssertionError(f"unexpected command: {command}")

    class _FakePopen:
        def __init__(
            self,
            cmd,
            stdout=None,
            stderr=None,
            stdin=None,
            close_fds=None,
            creationflags=0,
        ):
            popen_calls.append([str(part) for part in cmd])

    monkeypatch.setattr("subprocess.run", _fake_run)
    monkeypatch.setattr("subprocess.Popen", _FakePopen)
    monkeypatch.setattr("sys.executable", str(python_executable))
    monkeypatch.setattr("importlib.metadata.version", lambda _name: "0.32.0")
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("PATH", str(bridge_tg.parent))
    monkeypatch.setattr("platform.machine", lambda: "AMD64")
    monkeypatch.setenv("TENSOR_GREP_NATIVE_FRONTDOOR_FLAVOR", "cpu")
    monkeypatch.setattr(
        "tensor_grep.cli.main._latest_pypi_tensor_grep_version",
        lambda: "0.33.0",
        raising=False,
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_fresh_shell_path_value",
        lambda: str(bridge_tg.parent),
        raising=False,
    )

    runner = CliRunner()
    result = runner.invoke(app, ["upgrade"])

    assert result.exit_code == 0
    assert popen_calls
    helper_code = popen_calls[0][2]
    compile(helper_code, "<scheduled-upgrade-helper>", "exec")
    assert "refresh native front door, stale PATH copies, and stale Python launchers" in helper_code
    assert '"show",' in helper_code
    assert "Removed stale tensor-grep Python package launchers from PATH" in helper_code
    assert popen_calls[0][7] == str(native_binary)
    helper_assets = json.loads(popen_calls[0][8])
    assert helper_assets == [
        {
            "url": (
                "https://github.com/oimiragieo/tensor-grep/releases/download/v0.33.0/"
                "tg-windows-amd64-cpu.exe"
            ),
            "flavor": "cpu",
        }
    ]
    assert json.loads(popen_calls[0][9]) == [str(bridge_tg)]


def test_upgrade_schedules_windows_helper_for_realworld_uv_pip_ensurepip_lock(
    monkeypatch, tmp_path
):
    calls: list[list[str]] = []
    popen_calls: list[list[str]] = []

    uv_locked_error = (
        "uv: Using Python 3.12.12 environment at: .tensor-grep\\.venv\n"
        "Resolved 57 packages in 3.09s\n"
        "Downloading tensor-grep (3.1MiB)\n"
        "Downloading cryptography (3.3MiB)\n"
        " Downloaded tensor-grep\n"
        " Downloaded cryptography\n"
        "Prepared 14 packages in 1.00s\n"
        "error: failed to remove file "
        "`C:\\Users\\oimir\\.tensor-grep\\.venv\\Lib\\site-packages\\../../Scripts\\tg.exe`: "
        "The process cannot access the file because it is being used by another process. "
        "(os error 32)"
    )
    pip_missing_error = (
        "C:\\Users\\oimir\\.tensor-grep\\.venv\\Scripts\\python.exe: No module named pip"
    )
    ensurepip_locked_error = (
        "ERROR: Could not install packages due to an OSError: [WinError 32] "
        "The process cannot access the file because it is being used by another process: "
        "'c:\\users\\oimir\\.tensor-grep\\.venv\\scripts\\tg.exe'\n"
        "Check the permissions."
    )

    def _fake_run(cmd, capture_output=True, text=True, check=True):
        command = list(cmd)
        calls.append(command)
        if command[0] == "uv":
            raise subprocess.CalledProcessError(
                returncode=1,
                cmd=command,
                stderr=uv_locked_error,
            )
        if command[:3] == ["python", "-m", "pip"]:
            raise subprocess.CalledProcessError(
                returncode=1,
                cmd=command,
                stderr=pip_missing_error,
            )
        if command[:3] == ["python", "-m", "ensurepip"]:
            raise subprocess.CalledProcessError(
                returncode=1,
                cmd=command,
                stderr=ensurepip_locked_error,
            )
        raise AssertionError(f"unexpected command: {command}")

    class _FakePopen:
        def __init__(
            self,
            cmd,
            stdout=None,
            stderr=None,
            stdin=None,
            close_fds=None,
            creationflags=0,
        ):
            popen_calls.append(list(cmd))

    monkeypatch.setattr("subprocess.run", _fake_run)
    monkeypatch.setattr("subprocess.Popen", _FakePopen)
    monkeypatch.setattr("sys.executable", "python")
    monkeypatch.setattr("importlib.metadata.version", lambda _name: "0.31.0")
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setattr(
        "tensor_grep.cli.main._latest_pypi_tensor_grep_version",
        lambda: "0.32.0",
        raising=False,
    )

    runner = CliRunner()
    result = runner.invoke(app, ["upgrade"])

    assert result.exit_code == 0
    assert calls[0][0] == "uv"
    assert any(cmd[:3] == ["python", "-m", "pip"] for cmd in calls)
    assert any(cmd[:3] == ["python", "-m", "ensurepip"] for cmd in calls)
    assert popen_calls
    assert "Windows is still using tg.exe" in result.output
    assert "Wait a few seconds, then run `tg --version` again." in result.output
    assert "Upgrade log:" in result.output


def test_cli_debug_prints_pipeline_routing_reason(monkeypatch):
    global _FAKE_WALK, _FAKE_BACKEND
    _FAKE_WALK = {".": ["a.log"]}
    _FAKE_BACKEND = _FakeBackend(
        results_by_file={
            "a.log": SearchResult(
                matches=[MatchLine(line_number=1, text="ERROR", file="a.log")],
                total_files=1,
                total_matches=1,
            )
        }
    )
    _patch_cli_dependencies(monkeypatch)

    runner = CliRunner()
    result = runner.invoke(app, ["search", "ERROR", ".", "--debug", "--ltl"])

    assert result.exit_code == 0
    assert "[debug] routing.backend=FakeBackend reason=unit_test_fake_pipeline" in result.output


def test_cli_debug_passthrough_does_not_emit_tg_routing_banner(monkeypatch):
    def _fake_passthrough(self, paths, pattern, config=None):
        return 0

    monkeypatch.setattr(
        "tensor_grep.backends.ripgrep_backend.RipgrepBackend.is_available", lambda self: True
    )
    monkeypatch.setattr(
        "tensor_grep.backends.ripgrep_backend.RipgrepBackend.search_passthrough",
        _fake_passthrough,
    )

    runner = CliRunner()
    result = runner.invoke(app, ["search", "ERROR", ".", "--debug"])

    assert result.exit_code == 0
    assert "routing.backend=RipgrepBackend" not in result.output


def test_cli_stats_prints_summary_when_matches_found(monkeypatch):
    global _FAKE_WALK, _FAKE_BACKEND
    _FAKE_WALK = {".": ["a.log"]}
    _FAKE_BACKEND = _FakeBackend(
        results_by_file={
            "a.log": SearchResult(
                matches=[MatchLine(line_number=1, text="ERROR", file="a.log")],
                total_files=1,
                total_matches=1,
            )
        }
    )
    _patch_cli_dependencies(monkeypatch)

    runner = CliRunner()
    result = runner.invoke(app, ["search", "ERROR", ".", "--stats", "--ltl"])

    assert result.exit_code == 0
    assert "[stats] scanned_files=1 matched_files=1 total_matches=1" in result.output
    assert "[stats] backend=FakeBackend reason=unit_test_fake_pipeline" in result.output


def test_cli_debug_prints_gpu_routing_details_when_available(monkeypatch):
    global _FAKE_WALK, _FAKE_BACKEND
    _FAKE_WALK = {".": ["a.log"]}
    _FAKE_BACKEND = _FakeBackend(
        results_by_file={
            "a.log": SearchResult(
                matches=[MatchLine(line_number=1, text="ERROR", file="a.log")],
                total_files=1,
                total_matches=1,
            )
        }
    )
    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakeGpuPipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeScanner)

    runner = CliRunner()
    result = runner.invoke(app, ["search", "ERROR", ".", "--debug", "--ltl"])

    assert result.exit_code == 0
    assert "[debug] routing.gpu_device_ids=[7, 3]" in result.output
    assert "routing.gpu_chunk_plan_mb=[(7, 256), (3, 512)]" in result.output


def test_cli_stats_prints_gpu_routing_details_when_available(monkeypatch):
    global _FAKE_WALK, _FAKE_BACKEND
    _FAKE_WALK = {".": ["a.log"]}
    _FAKE_BACKEND = _FakeBackend(
        results_by_file={
            "a.log": SearchResult(
                matches=[MatchLine(line_number=1, text="ERROR", file="a.log")],
                total_files=1,
                total_matches=1,
            )
        }
    )
    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakeGpuPipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeScanner)

    runner = CliRunner()
    result = runner.invoke(app, ["search", "ERROR", ".", "--stats", "--ltl"])

    assert result.exit_code == 0
    assert "[stats] gpu_device_ids=[7, 3]" in result.output
    assert "gpu_chunk_plan_mb=[(7, 256), (3, 512)]" in result.output


def test_cli_json_output_includes_routing_metadata_fields(monkeypatch):
    global _FAKE_WALK, _FAKE_BACKEND
    _FAKE_WALK = {".": ["a.log"]}
    _FAKE_BACKEND = _FakeBackend(
        results_by_file={
            "a.log": SearchResult(
                matches=[MatchLine(line_number=1, text="ERROR", file="a.log")],
                total_files=1,
                total_matches=1,
            )
        }
    )
    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakeGpuPipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeScanner)

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["search", "ERROR", ".", "--gpu-device-ids", "7,3", "--ltl", "--format", "json"],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["version"] == 1
    assert payload["sidecar_used"] is False
    assert payload["routing_backend"] == "FakeBackend"
    assert payload["routing_reason"] == "unit_test_fake_pipeline"
    assert payload["requested_gpu_device_ids"] == [7, 3]
    assert payload["routing_gpu_device_ids"] == [7, 3]
    assert payload["routing_gpu_chunk_plan_mb"] == [
        {"device_id": 7, "chunk_mb": 256},
        {"device_id": 3, "chunk_mb": 512},
    ]
    assert payload["routing_distributed"] is True
    assert payload["routing_worker_count"] == 2


def test_cli_json_output_should_surface_distributed_worker_metadata_from_backend(monkeypatch):
    global _FAKE_WALK, _FAKE_BACKEND
    _FAKE_WALK = {".": ["a.log"]}
    _FAKE_BACKEND = _FakeBackend(
        results_by_file={
            "a.log": SearchResult(
                matches=[MatchLine(line_number=1, text="ERROR", file="a.log")],
                total_files=1,
                total_matches=1,
                routing_distributed=True,
                routing_worker_count=2,
            )
        }
    )
    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakeGpuPipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeScanner)

    runner = CliRunner()
    result = runner.invoke(app, ["search", "ERROR", ".", "--ltl", "--format", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["version"] == 1
    assert payload["sidecar_used"] is False
    assert payload["routing_backend"] == "FakeBackend"
    assert payload["routing_reason"] == "unit_test_fake_pipeline"
    assert payload["routing_gpu_device_ids"] == [7, 3]
    assert payload["routing_gpu_chunk_plan_mb"] == [
        {"device_id": 7, "chunk_mb": 256},
        {"device_id": 3, "chunk_mb": 512},
    ]
    assert payload["routing_distributed"] is True
    assert payload["routing_worker_count"] == 2


def test_cli_json_output_should_include_aggregated_matched_file_metadata(monkeypatch):
    global _FAKE_WALK, _FAKE_BACKEND
    _FAKE_WALK = {".": ["a.log", "b.log"]}
    _FAKE_BACKEND = _FakeBackend(
        results_by_file={
            "a.log": SearchResult(
                matches=[MatchLine(line_number=1, text="ERROR one", file="a.log")],
                total_files=1,
                total_matches=1,
            ),
            "b.log": SearchResult(
                matches=[MatchLine(line_number=2, text="ERROR two", file="b.log")],
                total_files=1,
                total_matches=1,
            ),
        }
    )
    _patch_cli_dependencies(monkeypatch)

    runner = CliRunner()
    result = runner.invoke(app, ["search", "ERROR", ".", "--format", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert sorted(payload["matched_file_paths"]) == ["a.log", "b.log"]
    assert payload["match_counts_by_file"] == {"a.log": 1, "b.log": 1}


def test_cli_json_output_should_preserve_ast_range_and_meta_variables(monkeypatch):
    global _FAKE_WALK, _FAKE_BACKEND
    _FAKE_WALK = {".": ["a.py"]}
    _FAKE_BACKEND = _FakeBackend(
        results_by_file={
            "a.py": SearchResult(
                matches=[
                    MatchLine(
                        line_number=1,
                        text="def hello(name):",
                        file="a.py",
                        range={
                            "byteOffset": {"start": 0, "end": 16},
                            "start": {"line": 0, "column": 0},
                            "end": {"line": 0, "column": 16},
                        },
                        meta_variables={
                            "single": {"F": {"text": "hello"}},
                            "multi": {"ARGS": [{"text": "name"}]},
                        },
                    )
                ],
                total_files=1,
                total_matches=1,
            )
        }
    )
    _patch_cli_dependencies(monkeypatch)

    runner = CliRunner()
    result = runner.invoke(app, ["search", "def $F($$$ARGS):", ".", "--ast", "--format", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["matches"][0]["range"] == {
        "byteOffset": {"start": 0, "end": 16},
        "start": {"line": 0, "column": 0},
        "end": {"line": 0, "column": 16},
    }
    assert payload["matches"][0]["metaVariables"] == {
        "single": {"F": {"text": "hello"}},
        "multi": {"ARGS": [{"text": "name"}]},
    }


def test_cli_json_output_should_prefer_runtime_backend_metadata_over_pipeline_selection(
    monkeypatch,
):
    global _FAKE_WALK, _FAKE_BACKEND
    _FAKE_WALK = {".": ["a.log"]}
    _FAKE_BACKEND = _FakeBackend(
        results_by_file={
            "a.log": SearchResult(
                matches=[MatchLine(line_number=1, text="ERROR", file="a.log")],
                total_files=1,
                total_matches=1,
                routing_backend="CPUBackend",
                routing_reason="torch_regex_cpu_fallback",
                routing_gpu_device_ids=[],
                routing_gpu_chunk_plan_mb=[],
                routing_distributed=False,
                routing_worker_count=1,
            )
        }
    )
    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakeGpuPipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeScanner)

    runner = CliRunner()
    result = runner.invoke(app, ["search", "ERROR", ".", "--ltl", "--format", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["routing_backend"] == "CPUBackend"
    assert payload["routing_reason"] == "torch_regex_cpu_fallback"
    assert payload["routing_gpu_device_ids"] == []
    assert payload["routing_gpu_chunk_plan_mb"] == []
    assert payload["routing_distributed"] is False
    assert payload["routing_worker_count"] == 1


def test_cli_debug_should_print_runtime_routing_when_backend_falls_back(monkeypatch):
    global _FAKE_WALK, _FAKE_BACKEND
    _FAKE_WALK = {".": ["a.log"]}
    _FAKE_BACKEND = _FakeBackend(
        results_by_file={
            "a.log": SearchResult(
                matches=[MatchLine(line_number=1, text="ERROR", file="a.log")],
                total_files=1,
                total_matches=1,
                routing_backend="CPUBackend",
                routing_reason="torch_regex_cpu_fallback",
                routing_gpu_device_ids=[],
                routing_gpu_chunk_plan_mb=[],
                routing_distributed=False,
                routing_worker_count=1,
            )
        }
    )
    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakeGpuPipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeScanner)

    runner = CliRunner()
    result = runner.invoke(app, ["search", "ERROR", ".", "--debug", "--ltl"])

    assert result.exit_code == 0
    assert "[debug] routing.backend=FakeBackend reason=unit_test_fake_pipeline" in result.output
    assert (
        "[debug] routing.runtime backend=CPUBackend reason=torch_regex_cpu_fallback"
        in result.output
    )


def test_cli_stats_should_prefer_runtime_backend_metadata_when_backend_falls_back(monkeypatch):
    global _FAKE_WALK, _FAKE_BACKEND
    _FAKE_WALK = {".": ["a.log"]}
    _FAKE_BACKEND = _FakeBackend(
        results_by_file={
            "a.log": SearchResult(
                matches=[MatchLine(line_number=1, text="ERROR", file="a.log")],
                total_files=1,
                total_matches=1,
                routing_backend="CPUBackend",
                routing_reason="torch_regex_cpu_fallback",
                routing_gpu_device_ids=[],
                routing_gpu_chunk_plan_mb=[],
                routing_distributed=False,
                routing_worker_count=1,
            )
        }
    )
    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakeGpuPipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeScanner)

    runner = CliRunner()
    result = runner.invoke(app, ["search", "ERROR", ".", "--stats", "--ltl"])

    assert result.exit_code == 0
    assert "[stats] backend=CPUBackend reason=torch_regex_cpu_fallback" in result.output
    assert "[stats] gpu_device_ids=" not in result.output


def test_cli_debug_should_print_gpu_chunk_plan_when_pipeline_selected_fallback_has_no_device_ids(
    monkeypatch,
):
    global _FAKE_WALK, _FAKE_BACKEND
    _FAKE_WALK = {".": ["a.log"]}
    _FAKE_BACKEND = _FakeBackend(
        results_by_file={
            "a.log": SearchResult(
                matches=[MatchLine(line_number=1, text="ERROR", file="a.log")],
                total_files=1,
                total_matches=1,
            )
        }
    )
    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakeGpuPlanOnlyPipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeScanner)

    runner = CliRunner()
    result = runner.invoke(app, ["search", "ERROR", ".", "--debug", "--ltl"])

    assert result.exit_code == 0
    assert (
        "[debug] routing.gpu_device_ids=[] routing.gpu_chunk_plan_mb=[(7, 256), (3, 512)]"
        in result.output
    )


def test_cli_stats_should_print_gpu_chunk_plan_when_pipeline_selected_fallback_has_no_device_ids(
    monkeypatch,
):
    global _FAKE_WALK, _FAKE_BACKEND
    _FAKE_WALK = {".": ["a.log"]}
    _FAKE_BACKEND = _FakeBackend(
        results_by_file={
            "a.log": SearchResult(
                matches=[MatchLine(line_number=1, text="ERROR", file="a.log")],
                total_files=1,
                total_matches=1,
            )
        }
    )
    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakeGpuPlanOnlyPipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeScanner)

    runner = CliRunner()
    result = runner.invoke(app, ["search", "ERROR", ".", "--stats"])

    assert result.exit_code == 0
    assert (
        "[stats] backend=RipgrepBackend reason=gpu_explicit_ids_no_gpu_backend_fallback"
        in result.output
    )
    assert (
        "[stats] gpu_device_ids=[] gpu_chunk_plan_mb=[(7, 256), (3, 512)] distributed=True workers=2"
        in result.output
    )


def test_cli_json_output_should_prefer_runtime_single_worker_gpu_metadata_over_selected_plan(
    monkeypatch,
):
    global _FAKE_WALK, _FAKE_BACKEND
    _FAKE_WALK = {".": ["a.log"]}
    _FAKE_BACKEND = _FakeBackend(
        results_by_file={
            "a.log": SearchResult(
                matches=[MatchLine(line_number=1, text="ERROR", file="a.log")],
                total_files=1,
                total_matches=1,
                routing_backend="CuDFBackend",
                routing_reason="cudf_chunked_single_worker_plan",
                routing_gpu_device_ids=[3],
                routing_gpu_chunk_plan_mb=[(3, 1)],
                routing_distributed=False,
                routing_worker_count=1,
            )
        }
    )
    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakeGpuPipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeScanner)

    runner = CliRunner()
    result = runner.invoke(app, ["search", "ERROR", ".", "--ltl", "--format", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["routing_backend"] == "CuDFBackend"
    assert payload["routing_reason"] == "cudf_chunked_single_worker_plan"
    assert payload["routing_gpu_device_ids"] == [3]
    assert payload["routing_gpu_chunk_plan_mb"] == [{"device_id": 3, "chunk_mb": 1}]
    assert payload["routing_distributed"] is False
    assert payload["routing_worker_count"] == 1


def test_cli_debug_should_prefer_runtime_single_worker_gpu_metadata_over_selected_plan(monkeypatch):
    global _FAKE_WALK, _FAKE_BACKEND
    _FAKE_WALK = {".": ["a.log"]}
    _FAKE_BACKEND = _FakeBackend(
        results_by_file={
            "a.log": SearchResult(
                matches=[MatchLine(line_number=1, text="ERROR", file="a.log")],
                total_files=1,
                total_matches=1,
                routing_backend="CuDFBackend",
                routing_reason="cudf_chunked_single_worker_plan",
                routing_gpu_device_ids=[3],
                routing_gpu_chunk_plan_mb=[(3, 1)],
                routing_distributed=False,
                routing_worker_count=1,
            )
        }
    )
    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakeGpuPipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeScanner)

    runner = CliRunner()
    result = runner.invoke(app, ["search", "ERROR", ".", "--debug", "--ltl"])

    assert result.exit_code == 0
    assert (
        "[debug] routing.runtime backend=CuDFBackend reason=cudf_chunked_single_worker_plan"
        in result.output
    )
    assert (
        "[debug] routing.runtime.gpu_device_ids=[3] routing.runtime.gpu_chunk_plan_mb=[(3, 1)] distributed=False workers=1"
        in result.output
    )


def test_cli_stats_should_prefer_runtime_single_worker_gpu_metadata_over_selected_plan(
    monkeypatch,
):
    global _FAKE_WALK, _FAKE_BACKEND
    _FAKE_WALK = {".": ["a.log"]}
    _FAKE_BACKEND = _FakeBackend(
        results_by_file={
            "a.log": SearchResult(
                matches=[MatchLine(line_number=1, text="ERROR", file="a.log")],
                total_files=1,
                total_matches=1,
                routing_backend="CuDFBackend",
                routing_reason="cudf_chunked_single_worker_plan",
                routing_gpu_device_ids=[3],
                routing_gpu_chunk_plan_mb=[(3, 1)],
                routing_distributed=False,
                routing_worker_count=1,
            )
        }
    )
    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakeGpuPipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeScanner)

    runner = CliRunner()
    result = runner.invoke(app, ["search", "ERROR", ".", "--stats", "--ltl"])

    assert result.exit_code == 0
    assert "[stats] backend=CuDFBackend reason=cudf_chunked_single_worker_plan" in result.output
    assert (
        "[stats] gpu_device_ids=[3] gpu_chunk_plan_mb=[(3, 1)] distributed=False workers=1"
        in result.output
    )


def test_cli_stats_prints_summary_when_no_matches(monkeypatch):
    global _FAKE_WALK, _FAKE_BACKEND
    _FAKE_WALK = {".": ["a.log"]}
    _FAKE_BACKEND = _FakeBackend(
        results_by_file={
            "a.log": SearchResult(
                matches=[],
                total_files=0,
                total_matches=0,
            )
        }
    )
    _patch_cli_dependencies(monkeypatch)

    runner = CliRunner()
    result = runner.invoke(app, ["search", "ERROR", ".", "--stats", "--ltl"])

    assert result.exit_code == 1
    assert "[stats] scanned_files=1 matched_files=0 total_matches=0" in result.output
    assert "[stats] backend=FakeBackend reason=unit_test_fake_pipeline" in result.output


class _FakeAstBackend:
    def search(self, file_path: str, pattern: str, config=None) -> SearchResult:
        try:
            content = open(file_path, encoding="utf-8").read()
        except OSError:
            content = ""
        has_match = pattern in content
        matches = (
            [
                MatchLine(
                    line_number=1, text=content.splitlines()[0] if content else "", file=file_path
                )
            ]
            if has_match
            else []
        )
        return SearchResult(
            matches=matches, total_files=1 if has_match else 0, total_matches=len(matches)
        )


class AstGrepWrapperBackend(_FakeAstBackend):
    search_many_calls: ClassVar[int] = 0
    search_project_calls: ClassVar[int] = 0

    def is_available(self):
        return True

    def search_many(self, file_paths: list[str], pattern: str, config=None) -> SearchResult:
        AstGrepWrapperBackend.search_many_calls += 1
        total_matches = 0
        matched_file_paths: list[str] = []
        expanded_paths: list[str] = []
        for file_path in file_paths:
            candidate = Path(file_path)
            if candidate.is_dir():
                expanded_paths.extend(
                    str(path) for path in sorted(candidate.rglob("*")) if path.is_file()
                )
            else:
                expanded_paths.append(file_path)
        for file_path in expanded_paths:
            result = self.search(file_path, pattern, config=config)
            total_matches += result.total_matches
            if result.total_matches > 0:
                matched_file_paths.append(file_path)
        return SearchResult(
            matches=[],
            matched_file_paths=matched_file_paths,
            total_files=len(matched_file_paths),
            total_matches=total_matches,
            routing_backend="AstGrepWrapperBackend",
            routing_reason="ast_grep_json",
            routing_distributed=False,
            routing_worker_count=1,
        )

    def search_project(self, root_path: str, config_path: str) -> dict[str, SearchResult]:
        _ = root_path
        _ = config_path
        AstGrepWrapperBackend.search_project_calls += 1
        return {
            "error-rule": SearchResult(
                matches=[],
                matched_file_paths=["a.py"],
                total_files=1,
                total_matches=1,
                routing_backend="AstGrepWrapperBackend",
                routing_reason="ast_grep_project_scan_json",
                routing_distributed=False,
                routing_worker_count=1,
            )
        }


class _FakeCountOnlyAstBackend:
    def search(self, file_path: str, pattern: str, config=None) -> SearchResult:
        try:
            content = open(file_path, encoding="utf-8").read()
        except OSError:
            content = ""
        has_match = pattern in content
        return SearchResult(
            matches=[],
            matched_file_paths=[file_path] if has_match else [],
            total_files=1 if has_match else 0,
            total_matches=1 if has_match else 0,
        )


class _FakeAstPipeline:
    def __init__(self, force_cpu=False, config=None):
        self._backend = _FakeAstBackend()

    def get_backend(self):
        return self._backend


class _FakeAstWrapperPipeline:
    def __init__(self, force_cpu=False, config=None):
        self._backend = AstGrepWrapperBackend()

    def get_backend(self):
        return self._backend


class _FakeCountOnlyAstPipeline:
    def __init__(self, force_cpu=False, config=None):
        self._backend = _FakeCountOnlyAstBackend()

    def get_backend(self):
        return self._backend


class _CapturingAstPipeline:
    last_config = None
    seen_configs: ClassVar[list[object]] = []
    init_count: ClassVar[int] = 0

    def __init__(self, force_cpu=False, config=None):
        _ = force_cpu
        _CapturingAstPipeline.init_count += 1
        _CapturingAstPipeline.last_config = config
        _CapturingAstPipeline.seen_configs.append(config)
        self._backend = _FakeAstBackend()

    def get_backend(self):
        return self._backend


class _FakeDirectNativeAstBackend:
    def is_available(self):
        return True


class _FakeUnavailableAstBackend:
    def is_available(self):
        return False


class _FakeDirectWrapperAstBackend:
    def is_available(self):
        return True


def _patch_direct_native_execution(monkeypatch):
    FakeAvailableAstBackend = type(
        "AstBackend",
        (_FakeAstBackend,),
        {"is_available": lambda self: True},
    )

    monkeypatch.setattr(
        "tensor_grep.backends.ast_backend.AstBackend",
        FakeAvailableAstBackend,
    )
    monkeypatch.setattr(
        "tensor_grep.backends.ast_wrapper_backend.AstGrepWrapperBackend",
        _FakeUnavailableAstBackend,
    )


def _patch_direct_wrapper_selection(monkeypatch):
    monkeypatch.setattr(
        "tensor_grep.backends.ast_backend.AstBackend",
        _FakeUnavailableAstBackend,
    )
    monkeypatch.setattr(
        "tensor_grep.backends.ast_wrapper_backend.AstGrepWrapperBackend",
        AstGrepWrapperBackend,
    )


class _FakeAstScanner:
    walk_calls: ClassVar[int] = 0

    def __init__(self, config=None):
        pass

    def walk(self, path):
        _FakeAstScanner.walk_calls += 1
        yield "a.py"
        yield "b.py"


class _ExplodingAstScanner:
    def __init__(self, config=None):
        pass

    def walk(self, path):
        raise AssertionError(f"scan guard should run before walking {path}")


_NO_GPU_INVENTORY = DeviceInventory(
    platform="windows",
    has_gpu=False,
    device_count=0,
    routable_device_ids=[],
    devices=[],
)

_MULTI_GPU_INVENTORY = DeviceInventory(
    platform="windows",
    has_gpu=True,
    device_count=2,
    routable_device_ids=[7, 3],
    devices=[
        DeviceInfo(device_id=7, vram_capacity_mb=12288),
        DeviceInfo(device_id=3, vram_capacity_mb=24576),
    ],
)


def test_scan_executes_rules_from_sgconfig(monkeypatch):
    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakeAstPipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeAstScanner)

    runner = CliRunner()
    with runner.isolated_filesystem():
        from pathlib import Path

        Path("sgconfig.yml").write_text(
            "ruleDirs:\n  - rules\nlanguage: python\n", encoding="utf-8"
        )
        Path("rules").mkdir()
        Path("rules/error.yml").write_text(
            "id: error-rule\nlanguage: python\nrule:\n  pattern: ERROR\n",
            encoding="utf-8",
        )
        Path("a.py").write_text("ERROR in file\n", encoding="utf-8")
        Path("b.py").write_text("ok\n", encoding="utf-8")

        result = runner.invoke(app, ["scan", "--config", "sgconfig.yml"])

    assert result.exit_code == 0
    assert "[scan] rule=error-rule lang=python matches=1 files=1" in result.output
    assert "Scan completed. rules=1 matched_rules=1 total_matches=1" in result.output


def test_rulesets_json_lists_builtin_rule_packs():
    runner = CliRunner()

    result = runner.invoke(app, ["rulesets", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == payload["version"]
    rulesets = {ruleset["name"]: ruleset for ruleset in payload["rulesets"]}
    assert set(rulesets) == {
        "auth-safe",
        "crypto-safe",
        "deserialization-safe",
        "secrets-basic",
        "subprocess-safe",
        "tls-safe",
    }
    assert rulesets["auth-safe"]["category"] == "security"
    assert "python" in rulesets["auth-safe"]["languages"]
    assert rulesets["auth-safe"]["rule_count"] >= 1


def test_scan_executes_builtin_ruleset(monkeypatch):
    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakeAstPipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeAstScanner)

    runner = CliRunner()
    with runner.isolated_filesystem():
        from pathlib import Path

        Path("a.py").write_text("hashlib.md5($$$ARGS)\n", encoding="utf-8")
        Path("b.py").write_text("ok\n", encoding="utf-8")

        result = runner.invoke(
            app,
            ["scan", "--ruleset", "crypto-safe", "--language", "python", "--path", "."],
        )

    assert result.exit_code == 0
    assert "Scanning project using built-in ruleset crypto-safe (python)" in result.output
    assert "[scan] rule=python-hashlib-md5 lang=python matches=1 files=1" in result.output
    assert "[scan] rule=python-hashlib-sha1 lang=python matches=0 files=0" in result.output
    assert "Scan completed. rules=2 matched_rules=1 total_matches=1" in result.output


def test_scan_ruleset_refuses_direct_temp_root_before_walking(monkeypatch, tmp_path: Path):
    temp_root = tmp_path / "Temp"
    temp_root.mkdir()
    (temp_root / "a.py").write_text("API_KEY = 'secret'\n", encoding="utf-8")
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _ExplodingAstScanner)

    result = CliRunner().invoke(
        app,
        [
            "scan",
            "--ruleset",
            "secrets-basic",
            "--language",
            "python",
            "--path",
            str(temp_root),
            "--json",
        ],
    )

    assert result.exit_code == 2
    assert "broad AST scan refused" in result.output
    assert "safety guard, not a zero-match result" in result.output
    assert "Temp" in result.output
    assert "--max-depth" in result.output
    assert "--allow-broad-generated-scan" in result.output


def test_scan_ruleset_allows_depth_bounded_temp_root(monkeypatch, tmp_path: Path):
    temp_root = tmp_path / "Temp"
    temp_root.mkdir()
    monkeypatch.chdir(temp_root)
    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakeAstPipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeAstScanner)

    Path("a.py").write_text('password = "$SECRET"\n', encoding="utf-8")
    Path("b.py").write_text("ok\n", encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "scan",
            "--ruleset",
            "secrets-basic",
            "--language",
            "python",
            "--path",
            str(temp_root),
            "--max-depth",
            "1",
            "--json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["routing_reason"] == "builtin-ruleset-scan"
    assert payload["ruleset"] == "secrets-basic"
    assert payload["total_matches"] >= 1


def test_scan_builtin_ruleset_can_emit_json(monkeypatch):
    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakeAstPipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeAstScanner)

    runner = CliRunner()
    with runner.isolated_filesystem():
        from pathlib import Path

        Path("a.py").write_text("hashlib.md5($$$ARGS)\n", encoding="utf-8")
        Path("b.py").write_text("ok\n", encoding="utf-8")

        result = runner.invoke(
            app,
            [
                "scan",
                "--ruleset",
                "crypto-safe",
                "--language",
                "python",
                "--path",
                ".",
                "--json",
            ],
        )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == payload["version"]
    assert payload["routing_reason"] == "builtin-ruleset-scan"
    assert payload["ruleset"] == "crypto-safe"
    assert payload["rule_count"] == 2
    assert payload["matched_rules"] == 1
    assert payload["total_matches"] == 1
    assert payload["findings"][0]["rule_id"] == "python-hashlib-md5"
    assert payload["findings"][0]["severity"] == "high"
    assert "hashlib.md5" in payload["findings"][0]["message"]
    assert (
        payload["findings"][0]["fingerprint"]
        == hashlib.sha256(
            json.dumps(
                {
                    "rule_id": "python-hashlib-md5",
                    "language": "python",
                    "files": ["a.py"],
                },
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
    )
    assert payload["findings"][0]["files"] == ["a.py"]
    assert payload["findings"][0]["evidence"] == [{"file": "a.py", "match_count": 1}]


def test_scan_builtin_ruleset_can_emit_evidence_snippets(monkeypatch):
    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakeAstPipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeAstScanner)

    runner = CliRunner()
    with runner.isolated_filesystem():
        from pathlib import Path

        Path("a.py").write_text("hashlib.md5($$$ARGS)\n", encoding="utf-8")
        Path("b.py").write_text("ok\n", encoding="utf-8")

        result = runner.invoke(
            app,
            [
                "scan",
                "--ruleset",
                "crypto-safe",
                "--language",
                "python",
                "--path",
                ".",
                "--json",
                "--include-evidence-snippets",
                "--max-evidence-snippets-per-file",
                "1",
                "--max-evidence-snippet-chars",
                "12",
            ],
        )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["findings"][0]["evidence"][0]["snippets"] == [
        {"text": "hashlib.md5(", "truncated": True}
    ]


def test_scan_supports_inline_rules_text(monkeypatch, tmp_path: Path) -> None:
    class AstGrepWrapperBackend:
        def search_many(self, file_paths: list[str], pattern: str, config=None) -> SearchResult:
            _ = config
            matches: list[MatchLine] = []
            matched_file_paths: list[str] = []

            for file_path in file_paths:
                candidate = Path(file_path)
                expanded_paths = (
                    sorted(str(path) for path in candidate.rglob("*") if path.is_file())
                    if candidate.is_dir()
                    else [file_path]
                )
                for expanded_path in expanded_paths:
                    content = Path(expanded_path).read_text(encoding="utf-8")
                    if pattern == "print($A)" and "print(" in content:
                        matched_file_paths.append(expanded_path)
                        matches.append(
                            MatchLine(line_number=1, text="print('hello')", file=expanded_path)
                        )

            return SearchResult(
                matches=matches,
                matched_file_paths=matched_file_paths,
                total_files=len(matched_file_paths),
                total_matches=len(matches),
                routing_backend="AstGrepWrapperBackend",
                routing_reason="ast_grep_json",
                routing_distributed=False,
                routing_worker_count=1,
            )

    monkeypatch.setattr(
        "tensor_grep.cli.main._select_ast_backend_for_pattern",
        lambda *_args, **_kwargs: AstGrepWrapperBackend(),
    )

    (tmp_path / "app.py").write_text("print('hello')\n", encoding="utf-8")
    inline_rules = "\n".join([
        "id: no-print",
        "language: python",
        "rule:",
        "  pattern: print($A)",
    ])
    runner = CliRunner()

    result = runner.invoke(
        app,
        ["scan", "--inline-rules", inline_rules, "--path", str(tmp_path)],
    )

    assert result.exit_code == 0
    assert "[scan] rule=no-print lang=python matches=1 files=1" in result.output
    assert "Scan completed. rules=1 matched_rules=1 total_matches=1" in result.output


def test_scan_supports_single_rule_file_and_positional_path(monkeypatch, tmp_path: Path) -> None:
    class AstGrepWrapperBackend:
        def search_many(self, file_paths: list[str], pattern: str, config=None) -> SearchResult:
            _ = config
            matches: list[MatchLine] = []
            matched_file_paths: list[str] = []

            for file_path in file_paths:
                candidate = Path(file_path)
                expanded_paths = (
                    sorted(str(path) for path in candidate.rglob("*") if path.is_file())
                    if candidate.is_dir()
                    else [file_path]
                )
                for expanded_path in expanded_paths:
                    content = Path(expanded_path).read_text(encoding="utf-8")
                    if pattern == "print($A)" and "print(" in content:
                        matched_file_paths.append(expanded_path)
                        matches.append(
                            MatchLine(line_number=1, text="print('hello')", file=expanded_path)
                        )

            return SearchResult(
                matches=matches,
                matched_file_paths=matched_file_paths,
                total_files=len(matched_file_paths),
                total_matches=len(matches),
                routing_backend="AstGrepWrapperBackend",
                routing_reason="ast_grep_json",
                routing_distributed=False,
                routing_worker_count=1,
            )

    monkeypatch.setattr(
        "tensor_grep.cli.main._select_ast_backend_for_pattern",
        lambda *_args, **_kwargs: AstGrepWrapperBackend(),
    )

    rule_file = tmp_path / "no_print.yml"
    rule_file.write_text(
        "\n".join([
            "id: no-print",
            "language: python",
            "rule:",
            "  pattern: print($A)",
        ]),
        encoding="utf-8",
    )
    source_root = tmp_path / "src"
    source_root.mkdir()
    (source_root / "app.py").write_text("print('hello')\n", encoding="utf-8")
    runner = CliRunner()

    result = runner.invoke(app, ["scan", "--rule", str(rule_file), str(source_root), "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["routing_reason"] == "ast-single-rule-scan"
    assert payload["findings"][0]["rule_id"] == "no-print"
    assert payload["findings"][0]["matches"] == 1


def test_scan_filter_limits_project_rules(monkeypatch, tmp_path: Path) -> None:
    class AstGrepWrapperBackend:
        def search_many(self, file_paths: list[str], pattern: str, config=None) -> SearchResult:
            _ = file_paths
            _ = config
            total = 1 if pattern == "print($A)" else 0
            return SearchResult(
                matches=[],
                matched_file_paths=["app.py"] if total else [],
                total_files=1 if total else 0,
                total_matches=total,
                routing_backend="AstGrepWrapperBackend",
                routing_reason="ast_grep_json",
                routing_distributed=False,
                routing_worker_count=1,
            )

    monkeypatch.setattr(
        "tensor_grep.cli.main._select_ast_backend_for_pattern",
        lambda *_args, **_kwargs: AstGrepWrapperBackend(),
    )

    (tmp_path / "sgconfig.yml").write_text(
        "ruleDirs:\n  - rules\nlanguage: python\n", encoding="utf-8"
    )
    (tmp_path / "rules").mkdir()
    (tmp_path / "rules" / "no_print.yml").write_text(
        "id: no-print\nlanguage: python\nrule:\n  pattern: print($A)\n",
        encoding="utf-8",
    )
    (tmp_path / "rules" / "no_eval.yml").write_text(
        "id: no-eval\nlanguage: python\nrule:\n  pattern: eval($A)\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        app,
        ["scan", "--config", str(tmp_path / "sgconfig.yml"), "--filter", "print", "--json"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["rule_count"] == 1
    assert [finding["rule_id"] for finding in payload["findings"]] == ["no-print"]


def test_scan_project_filter_respects_positional_scan_paths(monkeypatch, tmp_path: Path) -> None:
    class CountingAstBackend:
        def search(self, file_path: str, pattern: str, config=None) -> SearchResult:
            _ = config
            try:
                lines = Path(file_path).read_text(encoding="utf-8").splitlines()
            except OSError:
                lines = []
            matches = [
                MatchLine(line_number=line_number, text=line, file=file_path)
                for line_number, line in enumerate(lines, start=1)
                if pattern in line
            ]
            total_matches = sum(line.count(pattern) for line in lines)
            return SearchResult(
                matches=matches,
                matched_file_paths=[file_path] if total_matches else [],
                total_files=1 if total_matches else 0,
                total_matches=total_matches,
                routing_backend="AstBackend",
                routing_reason="ast_native",
                routing_distributed=False,
                routing_worker_count=1,
            )

    monkeypatch.setattr(
        "tensor_grep.cli.main._select_ast_backend_for_pattern",
        lambda *_args, **_kwargs: CountingAstBackend(),
    )

    src_dir = tmp_path / "src"
    rules_dir = tmp_path / "rules"
    src_dir.mkdir()
    rules_dir.mkdir()
    (tmp_path / "sgconfig.yml").write_text(
        "ruleDirs:\n  - rules\nlanguage: python\n", encoding="utf-8"
    )
    (rules_dir / "no-pass.yml").write_text(
        "\n".join([
            "id: no-pass",
            "language: python",
            "message: avoid pass",
            "rule:",
            "  pattern: pass",
        ]),
        encoding="utf-8",
    )
    (src_dir / "sample.py").write_text("def f():\n    pass\n", encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "scan",
            "--config",
            str(tmp_path / "sgconfig.yml"),
            "--filter",
            "no-pass",
            str(src_dir),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["scan_paths"] == [str(src_dir.resolve())]
    assert payload["total_matches"] == 1
    assert payload["findings"][0]["files"] == [str((src_dir / "sample.py").resolve())]
    assert all(
        "rules" not in Path(file_path).parts for file_path in payload["findings"][0]["files"]
    )


def test_scan_inline_rules_json_preserves_rule_metadata(monkeypatch, tmp_path: Path) -> None:
    class AstGrepWrapperBackend:
        def search_many(self, file_paths: list[str], pattern: str, config=None) -> SearchResult:
            _ = config
            matches: list[MatchLine] = []
            matched_file_paths: list[str] = []

            for file_path in file_paths:
                candidate = Path(file_path)
                expanded_paths = (
                    sorted(str(path) for path in candidate.rglob("*") if path.is_file())
                    if candidate.is_dir()
                    else [file_path]
                )
                for expanded_path in expanded_paths:
                    content = Path(expanded_path).read_text(encoding="utf-8")
                    if pattern == "print($A)" and "print(" in content:
                        matched_file_paths.append(expanded_path)
                        matches.append(
                            MatchLine(line_number=1, text="print('hello')", file=expanded_path)
                        )

            return SearchResult(
                matches=matches,
                matched_file_paths=matched_file_paths,
                total_files=len(matched_file_paths),
                total_matches=len(matches),
                routing_backend="AstGrepWrapperBackend",
                routing_reason="ast_grep_json",
                routing_distributed=False,
                routing_worker_count=1,
            )

    monkeypatch.setattr(
        "tensor_grep.cli.main._select_ast_backend_for_pattern",
        lambda *_args, **_kwargs: AstGrepWrapperBackend(),
    )

    (tmp_path / "app.py").write_text("print('hello')\n", encoding="utf-8")
    inline_rules = "\n".join([
        "id: no-print",
        "language: python",
        "severity: warning",
        "message: Avoid print in library code.",
        "rule:",
        "  pattern: print($A)",
    ])
    runner = CliRunner()

    result = runner.invoke(
        app,
        ["scan", "--inline-rules", inline_rules, "--path", str(tmp_path), "--json"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    finding = payload["findings"][0]
    assert finding["rule_id"] == "no-print"
    assert finding["severity"] == "warning"
    assert finding["message"] == "Avoid print in library code."


@pytest.mark.parametrize(
    ("ast_grep_language", "normalized_language"),
    [
        ("Python", "python"),
        ("JavaScript", "javascript"),
        ("TypeScript", "typescript"),
        ("Tsx", "tsx"),
        ("Go", "go"),
        ("Rust", "rust"),
    ],
)
def test_scan_inline_rules_normalizes_ast_grep_language_names(
    monkeypatch,
    tmp_path: Path,
    ast_grep_language: str,
    normalized_language: str,
) -> None:
    seen_config_languages: list[str | None] = []

    class AstGrepWrapperBackend:
        def search_many(self, file_paths: list[str], pattern: str, config=None) -> SearchResult:
            _ = file_paths
            _ = pattern
            seen_config_languages.append(config.lang if config is not None else None)
            return SearchResult(
                matches=[],
                matched_file_paths=[],
                total_files=0,
                total_matches=0,
                routing_backend="AstGrepWrapperBackend",
                routing_reason="ast_grep_json",
                routing_distributed=False,
                routing_worker_count=1,
            )

    monkeypatch.setattr(
        "tensor_grep.cli.main._select_ast_backend_for_pattern",
        lambda *_args, **_kwargs: AstGrepWrapperBackend(),
    )

    inline_rules = "\n".join([
        "id: normalized-language",
        f"language: {ast_grep_language}",
        "rule:",
        "  pattern: ERROR",
    ])

    result = CliRunner().invoke(
        app,
        ["scan", "--inline-rules", inline_rules, "--path", str(tmp_path), "--json"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["findings"][0]["language"] == normalized_language
    assert seen_config_languages == [normalized_language]


def test_scan_inline_rules_rejects_unsupported_language(tmp_path: Path) -> None:
    inline_rules = "\n".join([
        "id: unsupported-language",
        "language: Dart",
        "rule:",
        "  pattern: print($A)",
    ])

    result = CliRunner().invoke(
        app,
        ["scan", "--inline-rules", inline_rules, "--path", str(tmp_path)],
    )

    assert result.exit_code == 1
    assert "Error: Unsupported AST language Dart" in result.output
    assert "Traceback" not in result.output


def test_scan_inline_rules_reports_invalid_yaml_without_traceback(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app,
        ["scan", "--inline-rules", "id: broken\nrule: [", "--path", str(tmp_path)],
    )

    assert result.exit_code == 1
    assert "Error:" in result.output
    assert "YAML" in result.output
    assert "Traceback" not in result.output


def test_scan_rule_file_reports_invalid_yaml_without_traceback(tmp_path: Path) -> None:
    rule_file = tmp_path / "broken.yml"
    rule_file.write_text("id: broken\nrule: [", encoding="utf-8")

    result = CliRunner().invoke(app, ["scan", "--rule", str(rule_file), str(tmp_path)])

    assert result.exit_code == 1
    assert "Error:" in result.output
    assert "YAML" in result.output
    assert "Traceback" not in result.output


def test_scan_wrapper_runtime_errors_do_not_show_traceback(monkeypatch, tmp_path: Path) -> None:
    class AstGrepWrapperBackend:
        def search_many(self, file_paths: list[str], pattern: str, config=None) -> SearchResult:
            _ = file_paths
            _ = pattern
            _ = config
            raise RuntimeError("ast-grep failed with exit code 8: invalid language")

    monkeypatch.setattr(
        "tensor_grep.cli.main._select_ast_backend_for_pattern",
        lambda *_args, **_kwargs: AstGrepWrapperBackend(),
    )
    inline_rules = "\n".join([
        "id: wrapper-error",
        "language: Python",
        "rule:",
        "  pattern: print($A)",
    ])

    result = CliRunner().invoke(
        app,
        ["scan", "--inline-rules", inline_rules, "--path", str(tmp_path)],
    )

    assert result.exit_code == 1
    assert "Error: ast-grep failed with exit code 8: invalid language" in result.output
    assert "Traceback" not in result.output


def test_scan_builtin_ruleset_can_compare_and_write_baseline(monkeypatch):
    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakeAstPipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeAstScanner)

    runner = CliRunner()
    with runner.isolated_filesystem():
        from pathlib import Path

        Path("a.py").write_text("hashlib.md5($$$ARGS)\n", encoding="utf-8")
        Path("old-baseline.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "kind": "ruleset-scan-baseline",
                    "ruleset": "crypto-safe",
                    "language": "python",
                    "fingerprints": [
                        hashlib.sha256(
                            json.dumps(
                                {
                                    "rule_id": "python-hashlib-md5",
                                    "language": "python",
                                    "files": ["a.py"],
                                },
                                sort_keys=True,
                            ).encode("utf-8")
                        ).hexdigest(),
                        "resolved-fingerprint",
                    ],
                },
                indent=2,
            ),
            encoding="utf-8",
        )

        result = runner.invoke(
            app,
            [
                "scan",
                "--ruleset",
                "crypto-safe",
                "--language",
                "python",
                "--path",
                ".",
                "--json",
                "--baseline",
                "old-baseline.json",
                "--write-baseline",
                "new-baseline.json",
            ],
        )

        written = json.loads(Path("new-baseline.json").read_text(encoding="utf-8"))

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["findings"][0]["status"] == "existing"
    assert payload["findings"][1]["status"] == "clear"
    assert payload["baseline"]["new_findings"] == 0
    assert payload["baseline"]["existing_findings"] == 1
    assert payload["baseline"]["resolved_findings"] == 1
    assert payload["baseline"]["resolved_fingerprints"] == ["resolved-fingerprint"]
    assert payload["baseline_written"]["count"] == 1
    assert written["kind"] == "ruleset-scan-baseline"
    assert written["fingerprints"] == [payload["findings"][0]["fingerprint"]]


def test_scan_builtin_ruleset_can_apply_suppressions(monkeypatch):
    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakeAstPipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeAstScanner)

    runner = CliRunner()
    with runner.isolated_filesystem():
        from pathlib import Path

        Path("a.py").write_text("hashlib.md5($$$ARGS)\n", encoding="utf-8")
        fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "rule_id": "python-hashlib-md5",
                    "language": "python",
                    "files": ["a.py"],
                },
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        Path("suppressions.json").write_text(
            json.dumps(
                {"version": 1, "kind": "ruleset-scan-suppressions", "fingerprints": [fingerprint]},
                indent=2,
            ),
            encoding="utf-8",
        )

        result = runner.invoke(
            app,
            [
                "scan",
                "--ruleset",
                "crypto-safe",
                "--language",
                "python",
                "--path",
                ".",
                "--json",
                "--suppressions",
                "suppressions.json",
            ],
        )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["findings"][0]["status"] == "suppressed"
    assert payload["findings"][1]["status"] == "clear"
    assert payload["suppressions"]["suppressed_findings"] == 1


def test_scan_builtin_ruleset_can_write_suppressions(monkeypatch):
    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakeAstPipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeAstScanner)

    runner = CliRunner()
    with runner.isolated_filesystem():
        from pathlib import Path

        Path("a.py").write_text("hashlib.md5($$$ARGS)\n", encoding="utf-8")

        result = runner.invoke(
            app,
            [
                "scan",
                "--ruleset",
                "crypto-safe",
                "--language",
                "python",
                "--path",
                ".",
                "--json",
                "--write-suppressions",
                "written-suppressions.json",
                "--justification",
                "Approved suppression for fixture coverage.",
            ],
        )

        written = json.loads(Path("written-suppressions.json").read_text(encoding="utf-8"))

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["suppressions_written"]["count"] == 1
    assert written["kind"] == "ruleset-scan-suppressions"
    assert written["entries"][0]["fingerprint"] == payload["findings"][0]["fingerprint"]
    assert written["entries"][0]["justification"] == "Approved suppression for fixture coverage."
    assert written["entries"][0]["created_at"].endswith("Z")


def test_scan_builtin_ruleset_write_suppressions_requires_justification(monkeypatch):
    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakeAstPipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeAstScanner)

    runner = CliRunner()
    with runner.isolated_filesystem():
        from pathlib import Path

        Path("a.py").write_text("hashlib.md5($$$ARGS)\n", encoding="utf-8")

        result = runner.invoke(
            app,
            [
                "scan",
                "--ruleset",
                "crypto-safe",
                "--language",
                "python",
                "--path",
                ".",
                "--json",
                "--write-suppressions",
                "written-suppressions.json",
            ],
        )

    assert result.exit_code == 1
    assert "justification" in result.output


def test_scan_executes_secrets_ruleset(monkeypatch):
    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakeAstPipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeAstScanner)

    runner = CliRunner()
    with runner.isolated_filesystem():
        from pathlib import Path

        Path("a.py").write_text('password = "$SECRET"\n', encoding="utf-8")
        Path("b.py").write_text("ok\n", encoding="utf-8")

        result = runner.invoke(
            app,
            ["scan", "--ruleset", "secrets-basic", "--language", "python", "--path", "."],
        )

    assert result.exit_code == 0
    assert "Scanning project using built-in ruleset secrets-basic (python)" in result.output
    assert "[scan] rule=python-hardcoded-password lang=python matches=1 files=1" in result.output
    assert "[scan] rule=python-hardcoded-api-key lang=python matches=0 files=0" in result.output
    assert (
        "[scan] rule=python-hardcoded-api-key-uppercase lang=python matches=0 files=0"
        in result.output
    )
    assert "[scan] rule=python-hardcoded-token lang=python matches=0 files=0" in result.output
    assert (
        "[scan] rule=python-hardcoded-provider-token lang=python matches=0 files=0" in result.output
    )
    assert (
        "[scan] rule=python-hardcoded-named-api-key lang=python matches=0 files=0" in result.output
    )
    assert "Scan completed. rules=6 matched_rules=1 total_matches=1" in result.output


def test_scan_executes_secrets_ruleset_uppercase_api_key(monkeypatch):
    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakeAstPipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeAstScanner)

    runner = CliRunner()
    with runner.isolated_filesystem():
        from pathlib import Path

        Path("a.py").write_text('API_KEY = "$SECRET"\n', encoding="utf-8")
        Path("b.py").write_text("ok\n", encoding="utf-8")

        result = runner.invoke(
            app,
            ["scan", "--ruleset", "secrets-basic", "--language", "python", "--path", "."],
        )

    assert result.exit_code == 0
    assert (
        "[scan] rule=python-hardcoded-api-key-uppercase lang=python matches=1 files=1"
        in result.output
    )


def test_scan_executes_secrets_ruleset_api_key_pattern(monkeypatch):
    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakeAstPipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeAstScanner)

    runner = CliRunner()
    with runner.isolated_filesystem():
        from pathlib import Path

        Path("a.py").write_text('const apiKey = "$SECRET"\n', encoding="utf-8")
        Path("b.py").write_text("ok\n", encoding="utf-8")

        result = runner.invoke(
            app,
            ["scan", "--ruleset", "secrets-basic", "--language", "javascript", "--path", "."],
        )

    assert result.exit_code == 0
    assert (
        "[scan] rule=javascript-hardcoded-api-key lang=javascript matches=1 files=1"
        in result.output
    )


def test_scan_executes_secrets_ruleset_generic_provider_token_regex(monkeypatch):
    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakeAstPipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeAstScanner)

    runner = CliRunner()
    with runner.isolated_filesystem():
        from pathlib import Path

        Path("a.py").write_text('stripe_secret = "sk_live_1234567890abcdef"\n', encoding="utf-8")
        Path("b.py").write_text("# leaked token sk_live_abcdef1234567890\n", encoding="utf-8")

        result = runner.invoke(
            app,
            ["scan", "--ruleset", "secrets-basic", "--language", "python", "--path", "."],
        )

    assert result.exit_code == 0
    assert (
        "[scan] rule=python-hardcoded-provider-token lang=python matches=2 files=2" in result.output
    )


def test_scan_executes_secrets_ruleset_prefixed_api_key_regex(monkeypatch):
    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakeAstPipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeAstScanner)

    runner = CliRunner()
    with runner.isolated_filesystem():
        from pathlib import Path

        Path("a.py").write_text(
            'OPENAI_API_KEY = "fake_test_key_123456"\nHEADER_NAME = "not-a-secret-value"\n',
            encoding="utf-8",
        )

        result = runner.invoke(
            app,
            ["scan", "--ruleset", "secrets-basic", "--language", "python", "--path", "."],
        )

    assert result.exit_code == 0
    assert (
        "[scan] rule=python-hardcoded-named-api-key lang=python matches=1 files=1" in result.output
    )
    assert "HEADER_NAME" not in result.output


def test_scan_executes_secrets_ruleset_fake_api_key_snake_case(monkeypatch):
    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakeAstPipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeAstScanner)

    runner = CliRunner()
    with runner.isolated_filesystem():
        from pathlib import Path

        Path("a.py").write_text('fake_api_key = "fake_test_key_123456"\n', encoding="utf-8")

        result = runner.invoke(
            app,
            ["scan", "--ruleset", "secrets-basic", "--language", "python", "--path", "."],
        )

    assert result.exit_code == 0
    assert (
        "[scan] rule=python-hardcoded-named-api-key lang=python matches=1 files=1" in result.output
    )


def test_scan_executes_tls_ruleset(monkeypatch):
    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakeAstPipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeAstScanner)

    runner = CliRunner()
    with runner.isolated_filesystem():
        from pathlib import Path

        Path("a.py").write_text("ssl._create_unverified_context()\n", encoding="utf-8")
        Path("b.py").write_text("ok\n", encoding="utf-8")

        result = runner.invoke(
            app,
            ["scan", "--ruleset", "tls-safe", "--language", "python", "--path", "."],
        )

    assert result.exit_code == 0
    assert "Scanning project using built-in ruleset tls-safe (python)" in result.output
    assert (
        "[scan] rule=python-unverified-ssl-context lang=python matches=1 files=1" in result.output
    )
    assert "[scan] rule=python-requests-verify-false lang=python matches=0 files=0" in result.output
    assert (
        "[scan] rule=python-requests-post-verify-false lang=python matches=0 files=0"
        in result.output
    )
    assert "Scan completed. rules=3 matched_rules=1 total_matches=1" in result.output


def test_scan_executes_tls_ruleset_requests_post_pattern(monkeypatch):
    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakeAstPipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeAstScanner)

    runner = CliRunner()
    with runner.isolated_filesystem():
        from pathlib import Path

        Path("a.py").write_text("requests.post($URL, verify=False)\n", encoding="utf-8")
        Path("b.py").write_text("ok\n", encoding="utf-8")

        result = runner.invoke(
            app,
            ["scan", "--ruleset", "tls-safe", "--language", "python", "--path", "."],
        )

    assert result.exit_code == 0
    assert (
        "[scan] rule=python-requests-post-verify-false lang=python matches=1 files=1"
        in result.output
    )


def test_scan_should_not_claim_gnns_when_ast_wrapper_backend_selected(monkeypatch):
    _patch_direct_wrapper_selection(monkeypatch)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeAstScanner)
    AstGrepWrapperBackend.search_many_calls = 0
    AstGrepWrapperBackend.search_project_calls = 0
    _FakeAstScanner.walk_calls = 0

    runner = CliRunner()
    with runner.isolated_filesystem():
        from pathlib import Path

        Path("sgconfig.yml").write_text(
            "ruleDirs:\n  - rules\nlanguage: python\n", encoding="utf-8"
        )
        Path("rules").mkdir()
        Path("rules/error.yml").write_text(
            "id: error-rule\nlanguage: python\nrule:\n  pattern: ERROR\n",
            encoding="utf-8",
        )
        Path("a.py").write_text("ERROR in file\n", encoding="utf-8")
        Path("b.py").write_text("ok\n", encoding="utf-8")

        result = runner.invoke(app, ["scan", "--config", "sgconfig.yml"])

    assert result.exit_code == 0
    assert "GPU-Accelerated GNNs" not in result.output
    assert AstGrepWrapperBackend.search_project_calls == 1
    assert AstGrepWrapperBackend.search_many_calls == 0
    assert _FakeAstScanner.walk_calls == 1


def test_scan_json_should_use_wrapper_project_fast_path(monkeypatch):
    _patch_direct_wrapper_selection(monkeypatch)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeAstScanner)
    AstGrepWrapperBackend.search_many_calls = 0
    AstGrepWrapperBackend.search_project_calls = 0
    _FakeAstScanner.walk_calls = 0

    runner = CliRunner()
    with runner.isolated_filesystem():
        from pathlib import Path

        Path("sgconfig.yml").write_text(
            "ruleDirs:\n  - rules\nlanguage: python\n", encoding="utf-8"
        )
        Path("rules").mkdir()
        Path("rules/error.yml").write_text(
            "id: error-rule\nlanguage: python\nrule:\n  pattern: ERROR\n",
            encoding="utf-8",
        )
        Path("a.py").write_text("ERROR in file\n", encoding="utf-8")

        result = runner.invoke(app, ["scan", "--config", "sgconfig.yml", "--json"])

    assert result.exit_code == 0
    assert AstGrepWrapperBackend.search_project_calls == 1
    assert AstGrepWrapperBackend.search_many_calls == 0
    assert _FakeAstScanner.walk_calls == 1


def test_scan_should_count_files_from_count_only_ast_results(monkeypatch):
    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakeCountOnlyAstPipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeAstScanner)

    runner = CliRunner()
    with runner.isolated_filesystem():
        from pathlib import Path

        Path("sgconfig.yml").write_text(
            "ruleDirs:\n  - rules\nlanguage: python\n", encoding="utf-8"
        )
        Path("rules").mkdir()
        Path("rules/error.yml").write_text(
            "id: error-rule\nlanguage: python\nrule:\n  pattern: ERROR\n",
            encoding="utf-8",
        )
        Path("a.py").write_text("ERROR in file\n", encoding="utf-8")
        Path("b.py").write_text("ok\n", encoding="utf-8")

        result = runner.invoke(app, ["scan", "--config", "sgconfig.yml"])

    assert result.exit_code == 0
    assert "[scan] rule=error-rule lang=python matches=1 files=1" in result.output


def test_run_should_not_warn_when_ast_wrapper_backend_selected(monkeypatch):
    _patch_direct_wrapper_selection(monkeypatch)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeAstScanner)
    AstGrepWrapperBackend.search_many_calls = 0
    _FakeAstScanner.walk_calls = 0

    runner = CliRunner()
    with runner.isolated_filesystem():
        from pathlib import Path

        Path("a.py").write_text("ERROR in file\n", encoding="utf-8")
        Path("b.py").write_text("ok\n", encoding="utf-8")

        result = runner.invoke(app, ["run", "ERROR", "."])

    assert result.exit_code == 0
    assert "Warning:" not in result.output
    assert AstGrepWrapperBackend.search_many_calls == 1
    assert _FakeAstScanner.walk_calls == 0


def test_run_should_report_ast_wrapper_backend_mode(monkeypatch):
    _patch_direct_wrapper_selection(monkeypatch)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeAstScanner)

    runner = CliRunner()
    with runner.isolated_filesystem():
        from pathlib import Path

        Path("a.py").write_text("ERROR in file\n", encoding="utf-8")
        Path("b.py").write_text("ok\n", encoding="utf-8")

        result = runner.invoke(app, ["run", "ERROR", "."])

    assert result.exit_code == 0
    assert "Executing ast-grep structural matching run..." in result.output
    assert "GPU-Accelerated AST-Grep Run" not in result.output


def test_run_should_use_native_first_ast_policy(monkeypatch):
    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _CapturingAstPipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeAstScanner)
    _CapturingAstPipeline.seen_configs = []

    runner = CliRunner()
    with runner.isolated_filesystem():
        from pathlib import Path

        Path("a.py").write_text("ERROR in file\n", encoding="utf-8")
        Path("b.py").write_text("ok\n", encoding="utf-8")

        result = runner.invoke(app, ["run", "ERROR", "."])

    assert result.exit_code == 0
    assert _CapturingAstPipeline.last_config is not None
    assert _CapturingAstPipeline.last_config.ast_prefer_native is True
    assert _CapturingAstPipeline.last_config.query_pattern == "ERROR"


def test_run_should_report_native_ast_backend_mode_without_gnns(monkeypatch):
    _patch_direct_native_execution(monkeypatch)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeAstScanner)

    runner = CliRunner()
    with runner.isolated_filesystem():
        from pathlib import Path

        Path("a.py").write_text("ERROR in file\n", encoding="utf-8")
        Path("b.py").write_text("ok\n", encoding="utf-8")

        result = runner.invoke(app, ["run", "ERROR", "."])

    assert result.exit_code == 0
    assert "Executing native AST matching run..." in result.output
    assert "GPU-Accelerated GNNs" not in result.output


def test_run_should_emit_rewrite_plan_without_apply(monkeypatch):
    from tensor_grep.cli import ast_workflows

    seen: dict[str, str] = {}

    def _fake_execute_rewrite_plan_json(
        *,
        pattern: str,
        replacement: str,
        lang: str,
        path: str,
    ) -> tuple[str, int]:
        seen.update({
            "pattern": pattern,
            "replacement": replacement,
            "lang": lang,
            "path": path,
        })
        return '{"total_edits": 1, "edits": []}', 0

    monkeypatch.setattr(ast_workflows, "execute_rewrite_plan_json", _fake_execute_rewrite_plan_json)

    runner = CliRunner()
    with runner.isolated_filesystem():
        from pathlib import Path

        Path("a.py").write_text("def add(x, y): return x + y\n", encoding="utf-8")
        result = runner.invoke(
            app,
            [
                "run",
                "--lang",
                "python",
                "--rewrite",
                "lambda $$$ARGS: $EXPR",
                "def $F($$$ARGS): return $EXPR",
                "a.py",
            ],
        )

    assert result.exit_code == 0
    assert '"total_edits": 1' in result.output
    assert "Executing " not in result.output
    assert seen == {
        "pattern": "def $F($$$ARGS): return $EXPR",
        "replacement": "lambda $$$ARGS: $EXPR",
        "lang": "python",
        "path": "a.py",
    }


def test_ast_rust_language_support_matrix(monkeypatch):
    from tensor_grep.cli.ast_workflows import _select_ast_backend_name_for_pattern

    monkeypatch.setattr("tensor_grep.cli.ast_workflows._check_backend_available", lambda name: True)

    # Native S-expression for Rust (supported by PyO3/tree-sitter)
    backend_native = _select_ast_backend_name_for_pattern("(function_item) @match", "rust")
    assert backend_native == "AstBackend"

    # Ast-grep specific string query with variadic params (supported by ast-grep CLI wrapper)
    backend_wrapper = _select_ast_backend_name_for_pattern("fn $F($$$ARGS)", "rust")
    assert backend_wrapper == "AstGrepWrapperBackend"


def test_test_command_should_report_ast_wrapper_backend_mode(monkeypatch):
    _patch_direct_wrapper_selection(monkeypatch)
    AstGrepWrapperBackend.search_many_calls = 0

    runner = CliRunner()
    with runner.isolated_filesystem():
        from pathlib import Path

        Path("sgconfig.yml").write_text(
            "ruleDirs:\n  - rules\ntestDirs:\n  - tests\nlanguage: python\n",
            encoding="utf-8",
        )
        Path("rules").mkdir()
        Path("tests").mkdir()
        Path("rules/error.yml").write_text(
            "id: error-rule\nlanguage: python\nrule:\n  pattern: ERROR\n",
            encoding="utf-8",
        )
        Path("tests/error.yml").write_text(
            "id: error-test\nruleId: error-rule\nvalid:\n  - ok\ninvalid:\n  - ERROR in file\n",
            encoding="utf-8",
        )

        result = runner.invoke(app, ["test", "--config", "sgconfig.yml"])

    assert result.exit_code == 0
    assert "Testing AST rules using ast-grep structural matching" in result.output
    assert AstGrepWrapperBackend.search_many_calls == 1


def test_test_command_should_report_native_ast_backend_mode_without_gnns(monkeypatch):
    _patch_direct_native_execution(monkeypatch)

    runner = CliRunner()
    with runner.isolated_filesystem():
        from pathlib import Path

        Path("sgconfig.yml").write_text(
            "ruleDirs:\n  - rules\ntestDirs:\n  - tests\nlanguage: python\n",
            encoding="utf-8",
        )
        Path("rules").mkdir()
        Path("tests").mkdir()
        Path("rules/error.yml").write_text(
            "id: error-rule\nlanguage: python\nrule:\n  pattern: ERROR\n",
            encoding="utf-8",
        )
        Path("tests/error.yml").write_text(
            "id: error-test\nruleId: error-rule\nvalid:\n  - ok\ninvalid:\n  - ERROR in file\n",
            encoding="utf-8",
        )

        result = runner.invoke(app, ["test", "--config", "sgconfig.yml"])

    assert result.exit_code == 0
    assert "Testing AST rules using native AST matching" in result.output
    assert "GPU-Accelerated GNNs" not in result.output


def test_test_command_should_batch_wrapper_backend_once_per_case(monkeypatch):
    _patch_direct_wrapper_selection(monkeypatch)
    AstGrepWrapperBackend.search_many_calls = 0

    runner = CliRunner()
    with runner.isolated_filesystem():
        from pathlib import Path

        Path("sgconfig.yml").write_text(
            "ruleDirs:\n  - rules\ntestDirs:\n  - tests\nlanguage: python\n",
            encoding="utf-8",
        )
        Path("rules").mkdir()
        Path("tests").mkdir()
        Path("rules/error.yml").write_text(
            "id: error-rule\nlanguage: python\nrule:\n  pattern: ERROR\n",
            encoding="utf-8",
        )
        Path("tests/error.yml").write_text(
            "id: error-test\nruleId: error-rule\nvalid:\n  - ok\n  - all good\ninvalid:\n  - ERROR in file\n  - another ERROR\n",
            encoding="utf-8",
        )

        result = runner.invoke(app, ["test", "--config", "sgconfig.yml"])

    assert result.exit_code == 0
    assert "All tests passed. cases=4" in result.output
    assert AstGrepWrapperBackend.search_many_calls == 1


def test_test_command_should_batch_wrapper_backend_across_cases_for_same_rule(monkeypatch):
    _patch_direct_wrapper_selection(monkeypatch)
    AstGrepWrapperBackend.search_many_calls = 0

    runner = CliRunner()
    with runner.isolated_filesystem():
        from pathlib import Path

        Path("sgconfig.yml").write_text(
            "ruleDirs:\n  - rules\ntestDirs:\n  - tests\nlanguage: python\n",
            encoding="utf-8",
        )
        Path("rules").mkdir()
        Path("tests").mkdir()
        Path("rules/error.yml").write_text(
            "id: error-rule\nlanguage: python\nrule:\n  pattern: ERROR\n",
            encoding="utf-8",
        )
        Path("tests/error.yml").write_text(
            "tests:\n"
            "  - id: error-test-1\n"
            "    ruleId: error-rule\n"
            "    valid:\n"
            "      - ok\n"
            "    invalid:\n"
            "      - ERROR in file\n"
            "  - id: error-test-2\n"
            "    ruleId: error-rule\n"
            "    valid:\n"
            "      - still ok\n"
            "    invalid:\n"
            "      - another ERROR\n",
            encoding="utf-8",
        )

        result = runner.invoke(app, ["test", "--config", "sgconfig.yml"])

    assert result.exit_code == 0
    assert "All tests passed. cases=4" in result.output
    assert AstGrepWrapperBackend.search_many_calls == 1


def test_scan_should_prefer_native_ast_backend_policy(monkeypatch):
    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _CapturingAstPipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeAstScanner)
    _CapturingAstPipeline.seen_configs = []
    _CapturingAstPipeline.init_count = 0

    runner = CliRunner()
    with runner.isolated_filesystem():
        from pathlib import Path

        Path("sgconfig.yml").write_text(
            "ruleDirs:\n  - rules\nlanguage: python\n", encoding="utf-8"
        )
        Path("rules").mkdir()
        Path("rules/error.yml").write_text(
            "id: error-rule\nlanguage: python\nrule:\n  pattern: ERROR\n",
            encoding="utf-8",
        )
        Path("a.py").write_text("ERROR in file\n", encoding="utf-8")
        Path("b.py").write_text("ok\n", encoding="utf-8")

        result = runner.invoke(app, ["scan", "--config", "sgconfig.yml"])

    assert result.exit_code == 0
    assert _CapturingAstPipeline.last_config is not None
    assert _CapturingAstPipeline.last_config.ast_prefer_native is True
    assert any(cfg and cfg.query_pattern == "ERROR" for cfg in _CapturingAstPipeline.seen_configs)


def test_test_command_should_prefer_native_ast_backend_policy(monkeypatch):
    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _CapturingAstPipeline)
    _CapturingAstPipeline.seen_configs = []
    _CapturingAstPipeline.init_count = 0

    runner = CliRunner()
    with runner.isolated_filesystem():
        from pathlib import Path

        Path("sgconfig.yml").write_text(
            "ruleDirs:\n  - rules\ntestDirs:\n  - tests\nlanguage: python\n",
            encoding="utf-8",
        )
        Path("rules").mkdir()
        Path("tests").mkdir()
        Path("rules/error.yml").write_text(
            "id: error-rule\nlanguage: python\nrule:\n  pattern: ERROR\n",
            encoding="utf-8",
        )
        Path("tests/error.yml").write_text(
            "id: error-test\nruleId: error-rule\nvalid:\n  - ok\ninvalid:\n  - ERROR in file\n",
            encoding="utf-8",
        )

        result = runner.invoke(app, ["test", "--config", "sgconfig.yml"])

    assert result.exit_code == 0
    assert _CapturingAstPipeline.last_config is not None
    assert _CapturingAstPipeline.last_config.ast_prefer_native is True
    assert any(cfg and cfg.query_pattern == "ERROR" for cfg in _CapturingAstPipeline.seen_configs)


def test_scan_should_reuse_native_ast_backend_selection_for_multiple_native_patterns(monkeypatch):
    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _CapturingAstPipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeAstScanner)
    _CapturingAstPipeline.seen_configs = []
    _CapturingAstPipeline.init_count = 0

    runner = CliRunner()
    with runner.isolated_filesystem():
        from pathlib import Path

        Path("sgconfig.yml").write_text(
            "ruleDirs:\n  - rules\nlanguage: python\n", encoding="utf-8"
        )
        Path("rules").mkdir()
        Path("rules/rule_a.yml").write_text(
            "id: rule-a\nlanguage: python\nrule:\n  pattern: function_definition\n",
            encoding="utf-8",
        )
        Path("rules/rule_b.yml").write_text(
            "id: rule-b\nlanguage: python\nrule:\n  pattern: class_definition\n",
            encoding="utf-8",
        )
        Path("a.py").write_text("function_definition\nclass_definition\n", encoding="utf-8")
        Path("b.py").write_text("ok\n", encoding="utf-8")

        result = runner.invoke(app, ["scan", "--config", "sgconfig.yml"])

    assert result.exit_code == 0
    assert _CapturingAstPipeline.init_count == 1


def test_test_command_should_reuse_wrapper_backend_selection_for_multiple_ast_grep_patterns(
    monkeypatch,
):
    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _CapturingAstPipeline)
    _CapturingAstPipeline.seen_configs = []
    _CapturingAstPipeline.init_count = 0

    runner = CliRunner()
    with runner.isolated_filesystem():
        from pathlib import Path

        Path("sgconfig.yml").write_text(
            "ruleDirs:\n  - rules\ntestDirs:\n  - tests\nlanguage: python\n",
            encoding="utf-8",
        )
        Path("rules").mkdir()
        Path("tests").mkdir()
        Path("rules/a.yml").write_text(
            "id: rule-a\nlanguage: python\nrule:\n  pattern: 'def $FUNC():'\n",
            encoding="utf-8",
        )
        Path("rules/b.yml").write_text(
            "id: rule-b\nlanguage: python\nrule:\n  pattern: 'class $NAME:'\n",
            encoding="utf-8",
        )
        Path("tests/a.yml").write_text(
            "id: test-a\nruleId: rule-a\nvalid:\n  - ok\ninvalid:\n  - 'def $FUNC():'\n",
            encoding="utf-8",
        )
        Path("tests/b.yml").write_text(
            "id: test-b\nruleId: rule-b\nvalid:\n  - ok\ninvalid:\n  - 'class $NAME:'\n",
            encoding="utf-8",
        )

        result = runner.invoke(app, ["test", "--config", "sgconfig.yml"])

    assert result.exit_code == 0
    assert _CapturingAstPipeline.init_count == 1


def test_ast_selection_should_skip_pipeline_for_native_backend(monkeypatch):
    monkeypatch.setattr(
        "tensor_grep.backends.ast_backend.AstBackend",
        _FakeDirectNativeAstBackend,
    )
    monkeypatch.setattr(
        "tensor_grep.backends.ast_wrapper_backend.AstGrepWrapperBackend",
        _FakeDirectWrapperAstBackend,
    )
    monkeypatch.setattr(
        "tensor_grep.core.pipeline.Pipeline.__init__",
        lambda self, force_cpu=False, config=None: (_ for _ in ()).throw(
            AssertionError("Pipeline construction should be skipped for direct AST selection")
        ),
    )

    backend = _select_ast_backend_for_pattern(
        SearchConfig(query_pattern="function_definition", ast=True, ast_prefer_native=True),
        "function_definition",
        {},
    )

    assert isinstance(backend, _FakeDirectNativeAstBackend)


def test_ast_selection_should_skip_pipeline_for_wrapper_backend(monkeypatch):
    monkeypatch.setattr(
        "tensor_grep.backends.ast_backend.AstBackend",
        _FakeDirectNativeAstBackend,
    )
    monkeypatch.setattr(
        "tensor_grep.backends.ast_wrapper_backend.AstGrepWrapperBackend",
        _FakeDirectWrapperAstBackend,
    )
    monkeypatch.setattr(
        "tensor_grep.core.pipeline.Pipeline.__init__",
        lambda self, force_cpu=False, config=None: (_ for _ in ()).throw(
            AssertionError("Pipeline construction should be skipped for direct AST selection")
        ),
    )

    backend = _select_ast_backend_for_pattern(
        SearchConfig(query_pattern="def $FUNC():", ast=True, ast_prefer_native=True),
        "def $FUNC():",
        {},
    )

    assert isinstance(backend, _FakeDirectWrapperAstBackend)


def test_test_command_should_use_total_file_contract_for_match_detection(monkeypatch):
    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakeCountOnlyAstPipeline)

    runner = CliRunner()
    with runner.isolated_filesystem():
        from pathlib import Path

        Path("sgconfig.yml").write_text(
            "ruleDirs:\n  - rules\ntestDirs:\n  - tests\nlanguage: python\n",
            encoding="utf-8",
        )
        Path("rules").mkdir()
        Path("tests").mkdir()
        Path("rules/error.yml").write_text(
            "id: error-rule\nlanguage: python\nrule:\n  pattern: ERROR\n",
            encoding="utf-8",
        )
        Path("tests/error.yml").write_text(
            "id: error-test\nruleId: error-rule\nvalid:\n  - ok\ninvalid:\n  - ERROR in file\n",
            encoding="utf-8",
        )

        result = runner.invoke(app, ["test", "--config", "sgconfig.yml"])

    assert result.exit_code == 0
    assert "All tests passed. cases=2" in result.output


def test_devices_command_reports_no_gpu_when_none_detected(monkeypatch):
    monkeypatch.setattr(
        "tensor_grep.core.hardware.device_inventory.collect_device_inventory",
        lambda: _NO_GPU_INVENTORY,
    )

    runner = CliRunner()
    result = runner.invoke(app, ["devices"])

    assert result.exit_code == 0
    assert "No routable GPUs detected." in result.output


def test_devices_command_json_outputs_routable_device_inventory(monkeypatch):
    import json

    monkeypatch.setattr(
        "tensor_grep.core.hardware.device_inventory.collect_device_inventory",
        lambda: _MULTI_GPU_INVENTORY,
    )

    runner = CliRunner()
    result = runner.invoke(app, ["devices", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["platform"] == "windows"
    assert payload["has_gpu"] is True
    assert payload["device_count"] == 2
    assert payload["routable_device_ids"] == [7, 3]
    assert payload["devices"] == [
        {"device_id": 7, "vram_capacity_mb": 12288},
        {"device_id": 3, "vram_capacity_mb": 24576},
    ]


def test_devices_command_text_outputs_device_lines(monkeypatch):
    monkeypatch.setattr(
        "tensor_grep.core.hardware.device_inventory.collect_device_inventory",
        lambda: _MULTI_GPU_INVENTORY,
    )

    runner = CliRunner()
    result = runner.invoke(app, ["devices"])

    assert result.exit_code == 0
    assert "Detected 2 routable GPU(s):" in result.output
    assert "- gpu:7 vram_mb=12288" in result.output
    assert "- gpu:3 vram_mb=24576" in result.output


def test_devices_command_format_json_outputs_inventory(monkeypatch):
    monkeypatch.setattr(
        "tensor_grep.core.hardware.device_inventory.collect_device_inventory",
        lambda: _MULTI_GPU_INVENTORY,
    )

    runner = CliRunner()
    result = runner.invoke(app, ["devices", "--format", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["platform"] == "windows"
    assert payload["device_count"] == 2


def test_devices_command_should_fail_on_unsupported_format(monkeypatch):
    monkeypatch.setattr(
        "tensor_grep.core.hardware.device_inventory.collect_device_inventory",
        lambda: _MULTI_GPU_INVENTORY,
    )

    runner = CliRunner()
    result = runner.invoke(app, ["devices", "--format", "xml"])

    assert result.exit_code == 2
    assert "must be one of: text, json" in result.output


def test_rule_test_command_executes_valid_and_invalid_cases(monkeypatch):
    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakeAstPipeline)

    runner = CliRunner()
    with runner.isolated_filesystem():
        from pathlib import Path

        Path("sgconfig.yml").write_text(
            "ruleDirs:\n  - rules\ntestDirs:\n  - tests\nlanguage: python\n",
            encoding="utf-8",
        )
        Path("rules").mkdir()
        Path("tests").mkdir()
        Path("rules/no_bad.yml").write_text(
            "id: no-bad\nlanguage: python\nrule:\n  pattern: BAD\n",
            encoding="utf-8",
        )
        Path("tests/no_bad_test.yml").write_text(
            (
                "tests:\n"
                "  - id: no-bad-basic\n"
                "    ruleId: no-bad\n"
                "    valid:\n"
                "      - 'all good'\n"
                "    invalid:\n"
                "      - 'contains BAD token'\n"
            ),
            encoding="utf-8",
        )

        result = runner.invoke(app, ["test", "--config", "sgconfig.yml"])

    assert result.exit_code == 0
    assert "All tests passed. cases=2" in result.output


def test_new_rule_uses_configured_rule_directory(tmp_path: Path) -> None:
    config_path = tmp_path / "sgconfig.yml"
    config_path.write_text(
        "ruleDirs:\n  - custom-rules\ntestDirs:\n  - custom-tests\nutilsDir: custom-utils\n"
        "language: python\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        app,
        ["new", "rule", "demo", "--config", str(config_path), "--lang", "python", "--yes"],
    )

    assert result.exit_code == 0, result.output
    assert (tmp_path / "custom-rules" / "demo.yml").exists()
    assert not (tmp_path / "rules" / "demo.yml").exists()


def test_run_update_all_aliases_apply_for_rewrite(monkeypatch, tmp_path: Path) -> None:
    seen: dict[str, object] = {}

    def fake_run_command(pattern: str, path: str | None = None, **kwargs: object) -> int:
        seen["pattern"] = pattern
        seen["path"] = path
        seen["kwargs"] = kwargs
        return 0

    monkeypatch.setattr("tensor_grep.cli.ast_workflows.run_command", fake_run_command)

    result = CliRunner().invoke(
        app,
        [
            "run",
            "--pattern",
            "print($A)",
            "--rewrite",
            "logger.info($A)",
            "--update-all",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert seen["pattern"] == "print($A)"
    assert seen["path"] == str(tmp_path)
    assert seen["kwargs"]["apply"] is True


def test_run_ast_grep_semantic_flags_are_forwarded_to_run_workflow(monkeypatch) -> None:
    seen: dict[str, object] = {}

    def fake_run_command(pattern: str, path: str | None = None, **kwargs: object) -> int:
        seen["pattern"] = pattern
        seen["path"] = path
        seen["kwargs"] = kwargs
        return 0

    monkeypatch.setattr("tensor_grep.cli.ast_workflows.run_command", fake_run_command)

    result = CliRunner().invoke(
        app,
        [
            "run",
            "--pattern",
            "print($A)",
            "--selector",
            "call",
            "--strictness",
            "relaxed",
            "--globs",
            "*.py",
            "src",
        ],
    )

    assert result.exit_code == 0, result.output
    assert seen["pattern"] == "print($A)"
    assert seen["path"] == "src"
    assert seen["kwargs"]["selector"] == "call"
    assert seen["kwargs"]["strictness"] == "relaxed"
    assert seen["kwargs"]["globs"] == ["*.py"]


def test_run_ast_grep_semantic_rewrite_combinations_fail_explicitly() -> None:
    result = CliRunner().invoke(
        app,
        ["run", "--pattern", "print($A)", "--selector", "call", "--rewrite", "logger.info($A)"],
    )

    assert result.exit_code == 1
    assert "ast-grep semantic run options are read-only" in result.output


def test_main_entry_should_not_rewrite_devices_subcommand(monkeypatch):

    seen: dict[str, list[str]] = {}

    def _fake_app(*_args, **_kwargs):
        seen["argv"] = list(sys.argv)

    monkeypatch.setattr(cli_main, "app", _fake_app)
    monkeypatch.setattr(sys, "argv", ["tg", "devices", "--json"])

    cli_main.main_entry()

    assert seen["argv"] == ["tg", "devices", "--json"]


def test_main_entry_should_disable_click_windows_arg_expansion_for_globs(monkeypatch):

    seen: dict[str, object] = {}

    def _fake_app(*_args, **kwargs):
        seen["argv"] = list(sys.argv)
        seen["kwargs"] = dict(kwargs)

    monkeypatch.setattr(cli_main, "app", _fake_app)
    monkeypatch.setattr(
        sys,
        "argv",
        ["tg", "search", "--json", "--glob", "src/tensor_grep/cli/**", "-e", "needle", "."],
    )

    cli_main.main_entry()

    assert seen["argv"] == [
        "tg",
        "search",
        "--json",
        "--glob",
        "src/tensor_grep/cli/**",
        "-e",
        "needle",
        ".",
    ]
    assert seen["kwargs"]["windows_expand_args"] is False
    assert seen["kwargs"]["prog_name"] == "tg"


def test_main_entry_should_not_rewrite_map_subcommand(monkeypatch):

    seen: dict[str, list[str]] = {}

    def _fake_app(*_args, **_kwargs):
        seen["argv"] = list(sys.argv)

    monkeypatch.setattr(cli_main, "app", _fake_app)
    monkeypatch.setattr(sys, "argv", ["tg", "map", "--json"])

    cli_main.main_entry()

    assert seen["argv"] == ["tg", "map", "--json"]


def test_main_entry_should_not_rewrite_doctor_subcommand(monkeypatch):

    seen: dict[str, list[str]] = {}

    def _fake_app(*_args, **_kwargs):
        seen["argv"] = list(sys.argv)

    monkeypatch.setattr(cli_main, "app", _fake_app)
    monkeypatch.setattr(sys, "argv", ["tg", "doctor", "--json"])

    cli_main.main_entry()

    assert seen["argv"] == ["tg", "doctor", "--json"]


def test_main_entry_should_not_rewrite_checkpoint_subcommand(monkeypatch):

    seen: dict[str, list[str]] = {}

    def _fake_app(*_args, **_kwargs):
        seen["argv"] = list(sys.argv)

    monkeypatch.setattr(cli_main, "app", _fake_app)
    monkeypatch.setattr(sys, "argv", ["tg", "checkpoint", "list"])

    cli_main.main_entry()

    assert seen["argv"] == ["tg", "checkpoint", "list"]


def test_checkpoint_undo_existing_path_reports_last_hint_json(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()

    result = CliRunner().invoke(app, ["checkpoint", "undo", str(project), "--json"])

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["error"] == "checkpoint_not_found"
    assert payload["checkpoint_id"] == str(project)
    assert payload["path"] == "."
    assert "parsed as CHECKPOINT_ID" in payload["detail"]
    assert "tg checkpoint undo --last" in payload["detail"]


def test_main_entry_should_not_rewrite_dogfood_subcommand(monkeypatch):

    seen: dict[str, list[str]] = {}

    def _fake_app(*_args, **_kwargs):
        seen["argv"] = list(sys.argv)

    monkeypatch.setattr(cli_main, "app", _fake_app)
    monkeypatch.setattr(sys, "argv", ["tg", "dogfood", "--json"])

    cli_main.main_entry()

    assert seen["argv"] == ["tg", "dogfood", "--json"]


def test_main_entry_should_not_rewrite_session_subcommand(monkeypatch):

    seen: dict[str, list[str]] = {}

    def _fake_app(*_args, **_kwargs):
        seen["argv"] = list(sys.argv)

    monkeypatch.setattr(cli_main, "app", _fake_app)
    monkeypatch.setattr(sys, "argv", ["tg", "session", "list"])

    cli_main.main_entry()

    assert seen["argv"] == ["tg", "session", "list"]


def test_main_entry_should_not_rewrite_calibrate_subcommand(monkeypatch):

    seen: dict[str, list[str]] = {}

    def _fake_app(*_args, **_kwargs):
        seen["argv"] = list(sys.argv)

    monkeypatch.setattr(cli_main, "app", _fake_app)
    monkeypatch.setattr(sys, "argv", ["tg", "calibrate"])

    cli_main.main_entry()

    assert seen["argv"] == ["tg", "calibrate"]


def test_main_entry_should_not_rewrite_top_level_help(monkeypatch):

    seen: dict[str, list[str]] = {}

    def _fake_app(*_args, **_kwargs):
        seen["argv"] = list(sys.argv)

    monkeypatch.setattr(cli_main, "app", _fake_app)
    monkeypatch.setattr(sys, "argv", ["tg", "--help"])

    cli_main.main_entry()

    assert seen["argv"] == ["tg", "--help"]


@pytest.mark.skipif(
    not sys.platform.startswith("win"),
    reason="Rich legacy Windows pipe workaround is Windows-specific.",
)
def test_main_module_should_disable_rich_when_windows_stdout_is_redirected():
    env = dict(os.environ)
    env.pop("TYPER_USE_RICH", None)

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            ("import os; import tensor_grep.cli.main; print(os.environ.get('TYPER_USE_RICH'))"),
        ],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout.strip() == "0"


def test_main_entry_should_not_rewrite_empty_argv(monkeypatch):

    seen: dict[str, list[str]] = {}

    def _fake_app(*_args, **_kwargs):
        seen["argv"] = list(sys.argv)

    monkeypatch.setattr(cli_main, "app", _fake_app)
    monkeypatch.setattr(sys, "argv", ["tg"])

    cli_main.main_entry()

    assert seen["argv"] == ["tg"]


def test_bootstrap_main_entry_should_route_scan_ruleset_through_full_cli(monkeypatch):
    from tensor_grep.cli import bootstrap as cli_bootstrap

    seen: dict[str, object] = {}

    def _fake_full_cli() -> None:
        seen["full_cli"] = True

    def _fake_ast_workflow_cli(argv: list[str]) -> None:
        seen["ast_workflow_argv"] = list(argv)

    monkeypatch.setattr(cli_bootstrap, "_run_full_cli", _fake_full_cli)
    monkeypatch.setattr(cli_bootstrap, "_run_ast_workflow_cli", _fake_ast_workflow_cli)
    monkeypatch.setattr(sys, "argv", ["tg", "scan", "--ruleset", "auth-safe"])

    cli_bootstrap.main_entry()

    assert seen == {"full_cli": True}


def test_bootstrap_run_help_should_not_expose_config_option(monkeypatch, capsys):
    from tensor_grep.cli import bootstrap as cli_bootstrap

    monkeypatch.setattr(sys, "argv", ["tg", "run", "--help"])

    with pytest.raises(SystemExit):
        cli_bootstrap.main_entry()

    help_text = capsys.readouterr().out
    assert "--config" not in help_text


def test_full_cli_run_help_should_not_expose_config_option() -> None:
    runner = CliRunner()

    result = runner.invoke(app, ["run", "--help"])

    assert result.exit_code == 0
    help_text = _strip_ansi(result.stdout)
    assert "--config" not in help_text
    assert "--pattern" in help_text
    assert "--files-with-matches" in help_text


def test_full_cli_run_accepts_ast_grep_pattern_option(monkeypatch, tmp_path: Path) -> None:
    seen: dict[str, object] = {}

    def fake_run_command(pattern: str, path: str | None = None, **kwargs: object) -> int:
        seen["pattern"] = pattern
        seen["path"] = path
        seen["kwargs"] = kwargs
        return 0

    monkeypatch.setattr("tensor_grep.cli.ast_workflows.run_command", fake_run_command)

    result = CliRunner().invoke(
        app,
        [
            "run",
            "--pattern",
            "class $NAME: $$$BODY",
            "--files-with-matches",
            str(tmp_path),
            "--lang",
            "python",
        ],
    )

    assert result.exit_code == 0
    assert seen["pattern"] == "class $NAME: $$$BODY"
    assert seen["path"] == str(tmp_path)
    assert seen["kwargs"]["files_with_matches"] is True


def test_app_help_should_expose_the_python_public_top_level_surface():
    runner = CliRunner()

    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    help_text = _strip_ansi(result.stdout)
    for snippet in TOP_LEVEL_HELP_REQUIRED_SNIPPETS:
        assert snippet in help_text
    normalized_help = re.sub(r"\s+", " ", _strip_ansi(result.stdout))
    assert (
        "Lexical repo-map retrieval bridges camelCase, snake_case, and source-term planning queries."
        in normalized_help
    )
    assert "tg doctor --with-lsp" in result.stdout
    assert "doctor" in result.stdout
    assert "symbol" in result.stdout


def test_search_help_should_render_python_search_help_smoke() -> None:
    runner = CliRunner()

    result = runner.invoke(app, ["search", "--help"])

    assert result.exit_code == 0
    help_text = _strip_ansi(result.stdout)
    for snippet in SEARCH_HELP_REQUIRED_SNIPPETS:
        assert snippet in help_text
    normalized_help = re.sub(r"\s+", " ", re.sub(r"[│┌┐└┘─]+", " ", help_text))
    assert "multi-project workspace roots" in normalized_help


def test_worker_help_should_render_dedicated_hidden_command_help() -> None:
    runner = CliRunner()

    result = runner.invoke(app, ["worker", "--help"])
    help_text = _strip_ansi(result.stdout)

    assert result.exit_code == 0
    assert "Resident AST Worker" in help_text
    assert "--port" in help_text
    assert "--stop" in help_text


def test_update_alias_calls_upgrade(monkeypatch) -> None:
    seen = {"called": False}

    def _fake_upgrade() -> None:
        seen["called"] = True

    monkeypatch.setattr("tensor_grep.cli.main.upgrade", _fake_upgrade)

    runner = CliRunner()
    result = runner.invoke(app, ["update"])

    assert result.exit_code == 0
    assert seen["called"] is True


def test_audit_verify_json_reports_valid_signed_manifest(tmp_path):
    runner = CliRunner()
    manifest_path = tmp_path / "rewrite-audit.json"
    signing_key_path = tmp_path / "audit.key"
    signing_key = b"top-secret"
    signing_key_path.write_bytes(signing_key)
    payload = _write_audit_manifest(manifest_path, signing_key=signing_key)

    result = runner.invoke(
        app,
        [
            "audit-verify",
            str(manifest_path),
            "--signing-key",
            str(signing_key_path),
            "--json",
        ],
    )

    assert result.exit_code == 0
    parsed = json.loads(result.stdout)
    assert parsed["routing_reason"] == "audit-manifest-verify"
    assert parsed["manifest_sha256"] == payload["manifest_sha256"]
    assert parsed["checks"] == {
        "digest_valid": True,
        "chain_valid": True,
        "signature_valid": True,
    }
    assert parsed["valid"] is True
    assert parsed["errors"] == []


def test_audit_history_json_lists_manifests_newest_first_and_updates_index(tmp_path):
    runner = CliRunner()
    project = tmp_path / "project"
    audit_dir = project / ".tensor-grep" / "audit"
    audit_dir.mkdir(parents=True)
    first_payload = _write_audit_manifest(
        audit_dir / "first.json",
        previous_manifest_sha256=None,
    )
    second_payload = _write_audit_manifest(
        audit_dir / "second.json",
        previous_manifest_sha256=str(first_payload["manifest_sha256"]),
    )

    result = runner.invoke(app, ["audit-history", str(project), "--json"])

    assert result.exit_code == 0
    parsed = json.loads(result.stdout)
    _assert_audit_manifest_envelope(parsed, routing_reason="audit-manifest-history")
    assert [entry["manifest_sha256"] for entry in parsed["history"]] == [
        second_payload["manifest_sha256"],
        first_payload["manifest_sha256"],
    ]
    index_path = project / ".tensor-grep" / "audit" / "index.json"
    assert index_path.exists()


def test_audit_history_json_returns_empty_array_for_empty_audit_directory(tmp_path):
    runner = CliRunner()
    project = tmp_path / "project"
    (project / ".tensor-grep" / "audit").mkdir(parents=True)

    result = runner.invoke(app, ["audit-history", str(project), "--json"])

    assert result.exit_code == 0
    parsed = json.loads(result.stdout)
    _assert_audit_manifest_envelope(parsed, routing_reason="audit-manifest-history")
    assert parsed["history"] == []


def test_audit_diff_json_reports_added_removed_and_changed_fields(tmp_path):
    runner = CliRunner()
    left_path = tmp_path / "left.json"
    right_path = tmp_path / "right.json"
    _write_audit_manifest(left_path)
    right_payload = _write_audit_manifest(
        right_path,
        previous_manifest_sha256="f" * 64,
    )
    parsed_right = json.loads(right_path.read_text(encoding="utf-8"))
    parsed_right["kind"] = "rewrite-plan-manifest"
    parsed_right["reviewer"] = "alice"
    parsed_right["files"][0]["after_sha256"] = "c" * 64
    parsed_right["manifest_sha256"] = hashlib.sha256(
        _canonical_manifest_bytes(parsed_right)
    ).hexdigest()
    right_path.write_text(json.dumps(parsed_right, indent=2), encoding="utf-8")

    result = runner.invoke(app, ["audit-diff", str(left_path), str(right_path), "--json"])

    assert result.exit_code == 0
    parsed = json.loads(result.stdout)
    _assert_audit_manifest_envelope(parsed, routing_reason="audit-manifest-diff")
    assert parsed["added"] == {"reviewer": "alice"}
    assert parsed["removed"] == {}
    assert parsed["changed"] == {
        "kind": {"old": "rewrite-audit-manifest", "new": "rewrite-plan-manifest"},
        "files[0].after_sha256": {"old": "b" * 64, "new": "c" * 64},
        "previous_manifest_sha256": {"old": None, "new": "f" * 64},
    }
    assert right_payload["manifest_sha256"] != parsed_right["manifest_sha256"]


def test_audit_diff_default_output_is_human_readable(tmp_path):
    runner = CliRunner()
    left_path = tmp_path / "left.json"
    right_path = tmp_path / "right.json"
    _write_audit_manifest(left_path)
    parsed_right = _write_audit_manifest(right_path)
    parsed_right["reviewer"] = "alice"
    parsed_right["manifest_sha256"] = hashlib.sha256(
        _canonical_manifest_bytes(parsed_right)
    ).hexdigest()
    right_path.write_text(json.dumps(parsed_right, indent=2), encoding="utf-8")

    result = runner.invoke(app, ["audit-diff", str(left_path), str(right_path)])

    assert result.exit_code == 0
    assert "Audit diff:" in result.stdout
    assert "Added" in result.stdout
    assert "reviewer" in result.stdout
    assert "Changed" in result.stdout


def test_audit_diff_json_returns_empty_sections_for_identical_manifests(tmp_path):
    runner = CliRunner()
    manifest_path = tmp_path / "rewrite-audit.json"
    _write_audit_manifest(manifest_path)

    result = runner.invoke(app, ["audit-diff", str(manifest_path), str(manifest_path), "--json"])

    assert result.exit_code == 0
    parsed = json.loads(result.stdout)
    _assert_audit_manifest_envelope(parsed, routing_reason="audit-manifest-diff")
    assert parsed["added"] == {}
    assert parsed["removed"] == {}
    assert parsed["changed"] == {}


def test_audit_diff_json_reports_not_found_error(tmp_path):
    runner = CliRunner()
    missing_left = tmp_path / "missing-left.json"
    missing_right = tmp_path / "missing-right.json"

    result = runner.invoke(app, ["audit-diff", str(missing_left), str(missing_right), "--json"])

    assert result.exit_code == 1
    parsed = json.loads(result.stdout)
    assert parsed["error"]["code"] == "not_found"
    assert "Audit manifest not found" in parsed["error"]["message"]


def test_audit_diff_json_reports_invalid_json_error(tmp_path):
    runner = CliRunner()
    left_path = tmp_path / "left.json"
    right_path = tmp_path / "right.json"
    _write_audit_manifest(left_path)
    right_path.write_text("{not valid json", encoding="utf-8")

    result = runner.invoke(app, ["audit-diff", str(left_path), str(right_path), "--json"])

    assert result.exit_code == 1
    parsed = json.loads(result.stdout)
    assert parsed["error"]["code"] == "invalid_json"


def test_audit_verify_json_reports_chain_failure(tmp_path):
    runner = CliRunner()
    previous_manifest_path = tmp_path / "previous-audit.json"
    previous_payload = _write_audit_manifest(previous_manifest_path)
    wrong_previous = "f" * 64
    manifest_path = tmp_path / "rewrite-audit.json"
    _write_audit_manifest(manifest_path, previous_manifest_sha256=wrong_previous)

    result = runner.invoke(
        app,
        [
            "audit-verify",
            str(manifest_path),
            "--previous-manifest",
            str(previous_manifest_path),
            "--json",
        ],
    )

    # H1: audit-verify --json exits 1 when valid:false
    assert result.exit_code == 1
    parsed = json.loads(result.stdout)
    assert parsed["checks"]["digest_valid"] is True
    assert parsed["checks"]["chain_valid"] is False
    assert parsed["checks"]["signature_valid"] is True
    assert parsed["valid"] is False
    assert "Previous manifest digest does not match previous_manifest_sha256." in parsed["errors"]
    assert parsed["previous_manifest_sha256"] == wrong_previous
    assert previous_payload["manifest_sha256"] != wrong_previous


def test_review_bundle_create_json_packages_artifacts_and_writes_bundle_file(tmp_path):
    from tensor_grep.cli.checkpoint_store import create_checkpoint

    runner = CliRunner()
    project = tmp_path / "project"
    audit_dir = project / ".tensor-grep" / "audit"
    audit_dir.mkdir(parents=True)
    (project / "src").mkdir(parents=True)
    (project / "src" / "sample.py").write_text("print('hello')\n", encoding="utf-8")

    previous_path = audit_dir / "previous.json"
    previous_payload = _write_audit_manifest(previous_path, project_root=project)
    current_path = audit_dir / "current.json"
    _write_audit_manifest(
        current_path,
        previous_manifest_sha256=str(previous_payload["manifest_sha256"]),
        project_root=project,
    )
    scan_path = project / "scan.json"
    scan_payload = _write_scan_results(scan_path)
    checkpoint = create_checkpoint(str(project))
    bundle_path = tmp_path / "review-bundle.json"

    result = runner.invoke(
        app,
        [
            "review-bundle",
            "create",
            "--manifest",
            str(current_path),
            "--scan",
            str(scan_path),
            "--checkpoint-id",
            checkpoint.checkpoint_id,
            "--previous-manifest",
            str(previous_path),
            "--output",
            str(bundle_path),
            "--json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["routing_reason"] == "review-bundle-create"
    assert payload["scan_results"] == scan_payload
    assert payload["checkpoint_metadata"]["checkpoint_id"] == checkpoint.checkpoint_id
    assert payload["diff"]["changed"]["previous_manifest_sha256"] == {
        "old": None,
        "new": previous_payload["manifest_sha256"],
    }
    assert json.loads(bundle_path.read_text(encoding="utf-8")) == payload


def test_review_bundle_verify_json_reports_invalid_integrity(tmp_path):
    from tensor_grep.cli import audit_manifest as audit_manifest_module

    runner = CliRunner()
    project = tmp_path / "project"
    audit_dir = project / ".tensor-grep" / "audit"
    audit_dir.mkdir(parents=True)
    (project / "src").mkdir(parents=True)
    (project / "src" / "sample.py").write_text("print('hello')\n", encoding="utf-8")
    manifest_path = audit_dir / "current.json"
    _write_audit_manifest(manifest_path, project_root=project)
    bundle_path = tmp_path / "review-bundle.json"
    audit_manifest_module.create_review_bundle(manifest_path, output_path=bundle_path)

    tampered = json.loads(bundle_path.read_text(encoding="utf-8"))
    tampered["bundle_sha256"] = "0" * 64
    bundle_path.write_text(json.dumps(tampered, indent=2), encoding="utf-8")

    result = runner.invoke(app, ["review-bundle", "verify", str(bundle_path), "--json"])

    # H1: review-bundle verify --json exits 1 when valid:false
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["routing_reason"] == "review-bundle-verify"
    assert payload["checks"]["audit_manifest"]["valid"] is True
    assert payload["bundle_integrity"]["valid"] is False
    assert payload["valid"] is False


def test_calibrate_command_delegates_to_native_tg(monkeypatch):

    seen: dict[str, object] = {}

    class _Completed:
        returncode = 0

    monkeypatch.setattr(cli_main, "resolve_native_tg_binary", lambda: Path("tg.exe"))
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, check=False: seen.update({"cmd": list(cmd), "check": check}) or _Completed(),
    )

    runner = CliRunner()
    result = runner.invoke(app, ["calibrate"])

    assert result.exit_code == 0
    assert seen == {"cmd": ["tg.exe", "calibrate"], "check": False}


def test_main_entry_should_rewrite_raw_pattern_to_search_subcommand(monkeypatch):

    seen: dict[str, list[str]] = {}

    def _fake_app(*_args, **_kwargs):
        seen["argv"] = list(sys.argv)

    monkeypatch.setattr(cli_main, "app", _fake_app)
    monkeypatch.setattr(sys, "argv", ["tg", "ERROR", "."])

    cli_main.main_entry()

    assert seen["argv"] == ["tg", "search", "ERROR", "."]


def test_main_entry_should_fallback_to_pyproject_version_when_metadata_missing(monkeypatch, capsys):
    import importlib.metadata as importlib_metadata

    def _raise_version(_dist_name: str) -> str:
        raise RuntimeError("metadata unavailable")

    monkeypatch.setattr(sys, "argv", ["tg", "--version"])
    monkeypatch.setattr(importlib_metadata, "version", _raise_version)
    monkeypatch.setattr(cli_main, "_read_project_version_fallback", lambda: "0.31.4")

    with pytest.raises(SystemExit) as excinfo:
        cli_main.main_entry()

    assert excinfo.value.code == 0
    assert capsys.readouterr().out == "tensor-grep 0.31.4\n"


def test_main_entry_should_keep_verbose_version_details_when_requested(monkeypatch, capsys):
    import importlib.metadata as importlib_metadata

    def _raise_version(_dist_name: str) -> str:
        raise RuntimeError("metadata unavailable")

    monkeypatch.setattr(sys, "argv", ["tg", "--version", "--verbose"])
    monkeypatch.setattr(importlib_metadata, "version", _raise_version)
    monkeypatch.setattr(cli_main, "_read_project_version_fallback", lambda: "0.31.4")

    with pytest.raises(SystemExit) as excinfo:
        cli_main.main_entry()

    output = capsys.readouterr().out
    assert excinfo.value.code == 0
    assert output.startswith("tensor-grep 0.31.4\n\n")
    assert "features:+gpu-cudf,+gpu-torch,+rust-core" in output
    assert "Arrow Zero-Copy IPC is available" in output


def test_main_entry_should_delegate_top_level_pcre2_version_to_native_binary(
    monkeypatch, tmp_path: Path, capsys
):

    native_binary = tmp_path / ("tg.exe" if sys.platform.startswith("win") else "tg")
    native_binary.write_text("binary", encoding="utf-8")
    seen: dict[str, object] = {}

    def _fake_run(cmd, capture_output, text):
        seen["cmd"] = list(cmd)
        seen["capture_output"] = capture_output
        seen["text"] = text
        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout="PCRE2 10.42 is available (JIT is available)\n",
            stderr="",
        )

    monkeypatch.setattr(sys, "argv", ["tg", "--pcre2-version"])
    monkeypatch.setattr(cli_main, "resolve_native_tg_binary", lambda: native_binary)
    monkeypatch.setattr(cli_main.subprocess, "run", _fake_run)

    with pytest.raises(SystemExit) as excinfo:
        cli_main.main_entry()

    assert excinfo.value.code == 0
    assert seen == {
        "cmd": [str(native_binary), "--pcre2-version"],
        "capture_output": True,
        "text": True,
    }
    assert "PCRE2 10.42" in capsys.readouterr().out


def test_main_entry_should_fail_pcre2_version_when_no_backend_is_available(monkeypatch, capsys):

    monkeypatch.setattr(sys, "argv", ["tg", "--pcre2-version"])
    monkeypatch.setattr(cli_main, "resolve_native_tg_binary", lambda: None)
    monkeypatch.setattr(cli_main, "resolve_ripgrep_binary", lambda: None)

    with pytest.raises(SystemExit) as excinfo:
        cli_main.main_entry()

    captured = capsys.readouterr()
    assert excinfo.value.code == 1
    assert "PCRE2 version unavailable" in captured.err


def test_tg_test_uses_typer_help():
    from typer.testing import CliRunner

    from tensor_grep.cli.main import app

    runner = CliRunner()
    result = runner.invoke(app, ["test", "--help"])
    assert result.exit_code == 0
    assert "Usage: " in result.stdout
    assert "Options" in result.stdout.lower() or "options" in result.stdout.lower()
    assert "positional arguments:" not in result.stdout

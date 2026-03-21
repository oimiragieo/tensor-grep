import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

import pytest
from typer.testing import CliRunner

from tensor_grep.cli.main import _select_ast_backend_for_pattern, app
from tensor_grep.core.config import SearchConfig
from tensor_grep.core.hardware.device_detect import DeviceInfo
from tensor_grep.core.hardware.device_inventory import DeviceInventory
from tensor_grep.core.result import MatchLine, SearchResult


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

    monkeypatch.setattr("tensor_grep.cli.main._resolve_native_tg_binary", lambda: Path("tg.exe"))
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


def test_cli_should_delegate_ndjson_search_to_native_binary_and_preserve_exit_code(monkeypatch):
    seen: dict[str, object] = {}

    monkeypatch.setattr("tensor_grep.cli.main._resolve_native_tg_binary", lambda: Path("tg.exe"))
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
    assert seen["cmd"] == ["tg.exe", "search", "--cpu", "--ndjson", "ERROR", "."]


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


def test_source_json_returns_exact_symbol_source_blocks(tmp_path):
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)

    module_path = src_dir / "payments.py"
    module_path.write_text(
        "def create_invoice(total, tax):\n"
        "    subtotal = total + tax\n"
        "    return subtotal\n",
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


def test_resolve_native_tg_binary_should_ignore_legacy_benchmark_binary(monkeypatch, tmp_path):
    from tensor_grep.cli import main as cli_main

    repo_root = tmp_path / "repo"
    cli_path = repo_root / "src" / "tensor_grep" / "cli" / "main.py"
    cli_path.parent.mkdir(parents=True, exist_ok=True)
    cli_path.write_text("# stub\n", encoding="utf-8")

    legacy_binary = repo_root / "benchmarks" / "tg_rust.exe"
    legacy_binary.parent.mkdir(parents=True, exist_ok=True)
    legacy_binary.write_text("legacy\n", encoding="utf-8")

    monkeypatch.setattr(cli_main, "__file__", str(cli_path))
    monkeypatch.delenv("TG_NATIVE_TG_BINARY", raising=False)
    cli_main._resolve_native_tg_binary.cache_clear()

    assert cli_main._resolve_native_tg_binary() is None


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
    result = runner.invoke(app, ["search", "ERROR", ".", "--only-matching"])

    assert result.exit_code == 0
    assert result.stdout.strip() == "a.py:1:ERROR"


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


def test_cli_disables_ripgrep_passthrough_for_replace_mode(monkeypatch):
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
    assert called["passthrough"] is False


def test_upgrade_uses_uv_when_available(monkeypatch):
    calls: list[list[str]] = []

    def _fake_run(cmd, capture_output=True, text=True, check=True):
        calls.append(list(cmd))
        if cmd[0] == "uv":
            return subprocess.CompletedProcess(cmd, 0, stdout="Installed 1 package", stderr="")
        raise AssertionError("pip fallback should not be used when uv succeeds")

    monkeypatch.setattr("subprocess.run", _fake_run)

    runner = CliRunner()
    result = runner.invoke(app, ["upgrade"])

    assert result.exit_code == 0
    assert calls[0][0] == "uv"
    assert "Successfully upgraded tensor-grep via uv!" in result.stdout


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
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr("sys.executable", "python")
    monkeypatch.setattr("subprocess.run", _fake_run)

    runner = CliRunner()
    result = runner.invoke(app, ["upgrade"])

    assert result.exit_code == 0
    assert any(cmd[:3] == ["python", "-m", "ensurepip"] for cmd in calls)
    assert pip_attempts["count"] == 2
    assert "Successfully upgraded tensor-grep via pip+ensurepip!" in result.stdout


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

    runner = CliRunner()
    result = runner.invoke(app, ["upgrade"])

    assert result.exit_code == 1
    assert calls[0][0] == "uv"
    assert any(cmd[:3] == ["python", "-m", "pip"] for cmd in calls)
    assert "Error occurred while upgrading tensor-grep." in result.output
    assert "uv:" in result.output
    assert "pip:" in result.output
    assert "network timeout while contacting package index" in result.output


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


def test_cli_debug_prints_passthrough_routing_reason(monkeypatch):
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
    assert (
        "[debug] routing.backend=RipgrepBackend reason=rg_passthrough_cli_fast_path"
        in result.output
    )


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
    result = runner.invoke(app, ["search", "ERROR", ".", "--ltl", "--format", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
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
    payload = json.loads(result.output)
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
    payload = json.loads(result.output)
    assert sorted(payload["matched_file_paths"]) == ["a.log", "b.log"]
    assert payload["match_counts_by_file"] == {"a.log": 1, "b.log": 1}


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
    payload = json.loads(result.output)
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
    payload = json.loads(result.output)
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
    assert AstGrepWrapperBackend.search_project_calls == 0
    assert AstGrepWrapperBackend.search_many_calls == 1
    assert _FakeAstScanner.walk_calls == 0


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


def test_run_should_keep_wrapper_first_ast_policy(monkeypatch):
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
    assert _CapturingAstPipeline.last_config.ast_prefer_native is False
    assert _CapturingAstPipeline.last_config.query_pattern == "ERROR"


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
    payload = json.loads(result.output)
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
    payload = json.loads(result.output)
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


def test_main_entry_should_not_rewrite_devices_subcommand(monkeypatch):
    from tensor_grep.cli import main as cli_main

    seen: dict[str, list[str]] = {}

    def _fake_app():
        seen["argv"] = list(sys.argv)

    monkeypatch.setattr(cli_main, "app", _fake_app)
    monkeypatch.setattr(sys, "argv", ["tg", "devices", "--json"])

    cli_main.main_entry()

    assert seen["argv"] == ["tg", "devices", "--json"]


def test_main_entry_should_not_rewrite_calibrate_subcommand(monkeypatch):
    from tensor_grep.cli import main as cli_main

    seen: dict[str, list[str]] = {}

    def _fake_app():
        seen["argv"] = list(sys.argv)

    monkeypatch.setattr(cli_main, "app", _fake_app)
    monkeypatch.setattr(sys, "argv", ["tg", "calibrate"])

    cli_main.main_entry()

    assert seen["argv"] == ["tg", "calibrate"]


def test_main_entry_should_not_rewrite_top_level_help(monkeypatch):
    from tensor_grep.cli import main as cli_main

    seen: dict[str, list[str]] = {}

    def _fake_app():
        seen["argv"] = list(sys.argv)

    monkeypatch.setattr(cli_main, "app", _fake_app)
    monkeypatch.setattr(sys, "argv", ["tg", "--help"])

    cli_main.main_entry()

    assert seen["argv"] == ["tg", "--help"]


def test_main_entry_should_not_rewrite_empty_argv(monkeypatch):
    from tensor_grep.cli import main as cli_main

    seen: dict[str, list[str]] = {}

    def _fake_app():
        seen["argv"] = list(sys.argv)

    monkeypatch.setattr(cli_main, "app", _fake_app)
    monkeypatch.setattr(sys, "argv", ["tg"])

    cli_main.main_entry()

    assert seen["argv"] == ["tg"]


def test_app_help_should_list_upgrade_update_checkpoint_and_symbol_commands():
    runner = CliRunner()

    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "Fast text, AST, indexed, and GPU-aware search CLI" in result.stdout
    assert "Common usage" in result.stdout
    assert "tg PATTERN [PATH ...]" in result.stdout
    assert "upgrade" in result.stdout
    assert "update" in result.stdout
    assert "checkpoint" in result.stdout
    assert "session" in result.stdout
    assert "defs" in result.stdout
    assert "source" in result.stdout
    assert "impact" in result.stdout
    assert "refs" in result.stdout
    assert "callers" in result.stdout
    assert "Run semantic log classification" in result.stdout


def test_calibrate_command_delegates_to_native_tg(monkeypatch):
    from tensor_grep.cli import main as cli_main

    seen: dict[str, object] = {}

    class _Completed:
        returncode = 0

    monkeypatch.setattr(cli_main, "_resolve_native_tg_binary", lambda: Path("tg.exe"))
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
    from tensor_grep.cli import main as cli_main

    seen: dict[str, list[str]] = {}

    def _fake_app():
        seen["argv"] = list(sys.argv)

    monkeypatch.setattr(cli_main, "app", _fake_app)
    monkeypatch.setattr(sys, "argv", ["tg", "ERROR", "."])

    cli_main.main_entry()

    assert seen["argv"] == ["tg", "search", "ERROR", "."]


def test_main_entry_should_fallback_to_pyproject_version_when_metadata_missing(monkeypatch, capsys):
    import importlib.metadata as importlib_metadata

    from tensor_grep.cli import main as cli_main

    def _raise_version(_dist_name: str) -> str:
        raise RuntimeError("metadata unavailable")

    monkeypatch.setattr(sys, "argv", ["tg", "--version"])
    monkeypatch.setattr(importlib_metadata, "version", _raise_version)
    monkeypatch.setattr(cli_main, "_read_project_version_fallback", lambda: "0.31.4")

    with pytest.raises(SystemExit) as excinfo:
        cli_main.main_entry()

    assert excinfo.value.code == 0
    assert "tensor-grep 0.31.4" in capsys.readouterr().out

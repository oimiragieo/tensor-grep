import hashlib
import hmac
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

import pytest
from typer.testing import CliRunner

from tensor_grep.cli.main import (
    app,
)
from tensor_grep.core.hardware.device_detect import DeviceInfo
from tensor_grep.core.hardware.device_inventory import DeviceInventory
from tensor_grep.core.result import MatchLine, SearchResult


def _test_globals():
    """W4-d split fixup (mechanical, not a test-behavior change): before the split, every
    test in this file and the `_FakeScanner`/`_FakePipeline` fakes below shared ONE module's
    globals for `_FAKE_WALK` / `_FAKE_BACKEND` / `_LAST_PIPELINE_CONFIG`. Splitting into
    sibling modules turned each `global NAME; NAME = ...` in a test body into a REBIND of
    that sibling module's own global -- the fakes here, still defined in this shared module,
    would silently read/write a permanently-stale copy. Walk the call stack back to the
    nearest `tests.unit.test_cli_modes*` frame (the calling test's own module globals) so
    reads/writes go through the same namespace the test's `global` statement targets.
    """
    frame = sys._getframe(1)
    while frame is not None:
        mod_name = frame.f_globals.get("__name__", "")
        # Match on the FINAL dotted component, not a fully-qualified prefix. `tests/` has no
        # `__init__.py`, so pytest's default prepend import mode names these modules by basename
        # -- measured: `test_cli_modes_blast_radius`, NOT `tests.unit.test_cli_modes_blast_radius`.
        # A `startswith("tests.unit.test_cli_modes")` check therefore matched NOTHING here, the
        # walk fell through to `return globals()`, and the fakes read this shared module's stale
        # copy: exactly the failure the shim was written to prevent, silently, because falling
        # back to a real namespace looks like success. Both spellings now resolve, since the same
        # file is reachable as `tests.unit.test_cli_modes_*` when another module imports it by
        # that path.
        leaf_name = mod_name.rpartition(".")[2]
        if leaf_name.startswith("test_cli_modes") and mod_name != __name__:
            return frame.f_globals
        frame = frame.f_back
    return globals()


@pytest.fixture(autouse=True)
def _doctor_offline(monkeypatch):
    """A90 PATH-honesty: `tg doctor` now probes PyPI for pypi_latest (bounded 15s). That network
    call must NOT run in unit tests. `TG_DOCTOR_OFFLINE=1` (a documented escape hatch on
    _latest_pypi_tensor_grep_version) short-circuits to None (unknown_pypi), which is fast and
    offline-deterministic. The test that exercises the REAL probe (test_latest_pypi_probe_*)
    deletes this env var so it hits the network-mock path."""
    monkeypatch.setenv("TG_DOCTOR_OFFLINE", "1")


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
    "TG_MCP_ALLOW_VALIDATION_COMMANDS",
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
        _test_globals()["_LAST_PIPELINE_CONFIG"] = config
        self.backend = _test_globals().get("_FAKE_BACKEND", _FAKE_BACKEND)
        self.selected_backend_name = "FakeBackend"
        self.selected_backend_reason = "unit_test_fake_pipeline"
        self.selected_gpu_device_ids = []
        self.selected_gpu_chunk_plan_mb = []

    def get_backend(self):
        return self.backend


def _symlink_or_skip(link_path: Path, target: Path) -> None:
    try:
        link_path.symlink_to(target)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"cannot create a symlink on this host: {exc}")


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
    # H6 audit: `rollback_risk` is always `round(min(1.0, max(0.0, risk)), 3)`
    # (repo_map.py:13489-13512) -- a `0.0 <= x <= 1.0` bound check can never fail; the
    # clamp is proven load-bearing by
    # test_edit_plan_seed.py::test_rollback_risk_clamp_is_load_bearing_{upper,lower}_bound.
    # This helper is shared by call sites with genuinely different fixtures (a bare
    # `create_invoice` with 0 rollback risk vs a `build_invoice -> create_invoice` caller
    # chain with nonzero risk), so a single exact pin here would be wrong for one of them
    # -- assert the property this shared helper CAN check: the field is really a float.
    assert isinstance(edit_plan_seed["rollback_risk"], float)
    assert isinstance(edit_plan_seed["validation_plan"], list)
    assert edit_plan_seed["validation_plan"]
    for step in edit_plan_seed["validation_plan"]:
        assert {"command", "scope", "runner", "confidence", "detection"} <= set(step)
        assert isinstance(step["command"], str)
        assert step["scope"] in {"symbol", "file", "repo"}
        assert isinstance(step["runner"], str)
        assert step["detection"] in {"detected", "heuristic", "generic"}
        # H6 audit: step confidence is always `round(min(1.0, max(0.0, confidence)), 3)`
        # (repo_map.py:12013-12030, same clamp shape proven load-bearing by
        # test_edit_plan_seed.py::test_confidence_from_score_clamp_is_load_bearing) -- a
        # `0.0 <= x <= 1.0` bound check can never fail. This helper is shared across
        # fixtures with different validation plans, so assert the property it CAN check.
        assert isinstance(step["confidence"], float)


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
    # H6 audit: same clamp as edit_plan_seed['rollback_risk'] above -- unlike that shared
    # helper, `_assert_navigation_pack` has exactly one call site (test_cli_modes.py) with
    # one deterministic fixture, so pin the exact value (verified 3x): 0.0.
    assert navigation_pack["rollback_risk"] == 0.0


class _FakeScanner:
    def __init__(self, config=None):
        # Task #276 slice 1: `cli/main.py`'s CPU/native search branch reads these attributes
        # directly (a bare read, deliberately not `getattr(..., default)` -- see that call
        # site's comment) as the ONLY signal for whether the directory walk was truncated, so
        # this fake must carry the same shape a real `DirectoryScanner` does, defaulted to
        # "nothing was truncated" for every existing test that doesn't care about this.
        self.scan_truncated = False
        self.scan_truncation_cause = None
        self.unreadable_path_count = 0
        self.unreadable_path_sample: list[str] = []
        self.max_scan_entries = 200_000

    def walk(self, path):
        yield from _test_globals().get("_FAKE_WALK", _FAKE_WALK).get(path, [])


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
    # HARNESS DEFECT found during the task-22 investigation, and independent of that task's
    # outcome. This fixture describes a fully-mocked Python-engine environment
    # (FakePipeline/FakeScanner/no rg) but never pinned `resolve_native_tg_binary` -- so any
    # caller satisfying `_can_delegate_to_native_tg_search`'s eligibility gate silently took a
    # DIFFERENT dispatch route depending on whether the MACHINE happened to have a built native
    # binary. A dev box that has never run `cargo build` gets `None` and exercises the mocks
    # above; a runner that has one `sys.exit`s out of `search_command` via a real subprocess
    # before any mock is consulted. Same test, same code, two routes, decided by ambient state.
    #
    # Pinning to `None` makes the route EXPLICIT (the hermetic Python route). A test that
    # deliberately wants delegation patches it itself AFTER calling this fixture (e.g.
    # `test_cli_should_delegate_json_search_to_native_binary`) -- monkeypatch applies
    # immediately, so the later, more specific call wins.
    monkeypatch.setattr("tensor_grep.cli.main.resolve_native_tg_binary", lambda: None)


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


def _make_stub_file_repo(root: Path, file_count: int) -> None:
    """A single-project, non-vendored root -- matches NEITHER the workspace guard (needs
    >=3 sibling project dirs) NOR the vendored-root guard (needs a top-level vendored dir
    name), the exact shape that slipped past both existing guards (F6, dogfood v1.42.0)."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "pyproject.toml").write_text("", encoding="utf-8")
    src = root / "src"
    src.mkdir()
    for index in range(file_count):
        (src / f"stub_{index}.py").write_text("TODO placeholder\n", encoding="utf-8")


def _route_test_agreeing_target(target_file: Path) -> dict:
    return {
        "file": str(target_file.resolve()),
        "symbol": "create_invoice",
        "line": 1,
        "confidence": {"file": 0.9, "symbol": 0.9},
    }


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


_STUB_ASSET_SHA256 = "a" * 64  # deterministic stand-in used by _allow_native_frontdoor_checksum


def _allow_native_frontdoor_checksum(monkeypatch):
    """Audit HIGH (2026-06-24): tg upgrade now verifies the downloaded native asset
    against the published CHECKSUMS.txt and refuses an unverified binary. These
    fallback/refresh/restore tests don't exercise that gate, so stub it as verified
    (the gate itself is covered by tests/unit/test_native_frontdoor_checksum.py).

    Audit HIGH (2026-06-28): the deferred helpers now also verify checksums, with
    the parent side embedding the expected sha256 into each payload entry via
    _fetch_native_frontdoor_checksums + _expected_asset_sha256.  Stub both so
    tests remain network-free and payload assertions can use _STUB_ASSET_SHA256."""
    monkeypatch.setattr(
        "tensor_grep.cli.main._fetch_native_frontdoor_checksums",
        lambda version: "stub-checksums-manifest",
        raising=False,
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._native_frontdoor_checksum_error",
        lambda asset_path, asset_name, checksums_text: None,
        raising=False,
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._expected_asset_sha256",
        lambda checksums_text, asset_name: _STUB_ASSET_SHA256,
        raising=False,
    )


class _FakeUrlopenResponse:
    """Stand-in for the context-managed object urlopen() returns, used to fake
    `_download_native_frontdoor_asset`'s streamed transfer in these upgrade tests
    (frontdoor-download-held-fd task: the download reads via `urlopen` + a chunked read loop
    into its already-claimed fd, replacing the old `urlretrieve(..., reporthook=...)` call --
    `.read(n)` yields the whole payload on the first call, then empty bytes (EOF), matching a
    real short response)."""

    def __init__(self, payload: bytes) -> None:
        self._payload: bytes | None = payload

    def read(self, _size: int = -1) -> bytes:
        if self._payload is None:
            return b""
        payload, self._payload = self._payload, None
        return payload

    def __enter__(self) -> "_FakeUrlopenResponse":
        return self

    def __exit__(self, *_exc_info: object) -> bool:
        return False


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


__all__ = [
    "SEARCH_HELP_REQUIRED_SNIPPETS",
    "TOP_LEVEL_HELP_REQUIRED_SNIPPETS",
    "_FAKE_BACKEND",
    "_LAST_PIPELINE_CONFIG",
    "_MULTI_GPU_INVENTORY",
    "_NO_GPU_INVENTORY",
    "_STUB_ASSET_SHA256",
    "AstGrepWrapperBackend",
    "RipgrepBackend",
    "_CapturingAstPipeline",
    "_ExplodingAstScanner",
    "_FakeAstBackend",
    "_FakeAstPipeline",
    "_FakeAstScanner",
    "_FakeAstWrapperPipeline",
    "_FakeBackend",
    "_FakeCountOnlyAstBackend",
    "_FakeCountOnlyAstPipeline",
    "_FakeDirectNativeAstBackend",
    "_FakeDirectWrapperAstBackend",
    "_FakeGpuPipeline",
    "_FakeGpuPlanOnlyPipeline",
    "_FakePipeline",
    "_FakeRipgrepBackend",
    "_FakeRipgrepPipeline",
    "_FakeScanner",
    "_FakeUnavailableAstBackend",
    "_FakeUrlopenResponse",
    "_agent_capsule_payload_for_query",
    "_allow_native_frontdoor_checksum",
    "_assert_audit_manifest_envelope",
    "_assert_enriched_edit_plan_seed",
    "_assert_navigation_pack",
    "_canonical_manifest_bytes",
    "_doctor_offline",
    "_make_stub_file_repo",
    "_patch_cli_dependencies",
    "_patch_direct_native_execution",
    "_patch_direct_wrapper_selection",
    "_route_test_agreeing_target",
    "_strip_ansi",
    "_symlink_or_skip",
    "_test_globals",
    "_write_audit_manifest",
    "_write_invoice_service_ambiguity_fixture",
    "_write_mixed_invoice_fixture",
    "_write_scan_results",
]

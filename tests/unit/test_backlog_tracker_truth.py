"""Closed-world, deterministic contract for the live backlog status index.

GitHub state is intentionally absent from this module. Live PR, issue, CI, and release facts belong
in the dated reconciliation audit; this test only proves that committed tracker claims are complete,
unambiguous, and consistent with source-controlled contracts.
"""

from __future__ import annotations

import dataclasses
import json
import re
from pathlib import Path
from typing import ClassVar

import pytest
from typer.testing import CliRunner

from tensor_grep.cli import main as cli_main
from tensor_grep.cli.formatters.json_fmt import gpu_request_unhonoured
from tensor_grep.cli.main import app
from tensor_grep.core.result import MatchLine, SearchResult

ROOT = Path(__file__).resolve().parents[2]
BOARD_PATH = ROOT / "docs" / "TASK_BOARD.md"
BACKLOG_PATH = ROOT / "docs" / "BACKLOG.md"
HANDOFF_PATH = ROOT / "docs" / "SESSION_HANDOFF.md"
CONTRACTS_PATH = ROOT / "docs" / "CONTRACTS.md"
MAIN_PATH = ROOT / "src" / "tensor_grep" / "cli" / "main.py"
LEDGER_STORE_PATH = ROOT / "src" / "tensor_grep" / "cli" / "ledger_store.py"
AUDIT_859_PATH = ROOT / "docs" / "audits" / "2026-08-01-backlog-verification-receipts.md"
CEO_AUDIT_PATH = ROOT / "docs" / "audits" / "2026-08-13-ceo-backlog-update.md"

STATUS_HEADING = "## Canonical status index"
VERSION_PREFIX = "Canonical status index version:"
VERSION_RE = re.compile(r"^Canonical status index version: \d{4}-\d{2}-\d{2}\.\d+$")
ROW_RE = re.compile(
    r"^- \[(?P<checked>[ x])\] \*\*(?P<id>[#A-Z0-9-]+)\*\* — "
    r"Status: (?P<status>[A-Z_]+); PR: (?P<pr>[^;]+); Trigger: (?P<trigger>.+)$"
)
STATUSES = {
    "IN_FLIGHT",
    "READY",
    "BLOCKED",
    "CEO_GATED",
    "DEMAND_GATED",
    "SHIPPED",
    "RETIRED",
}
TERMINAL = {"SHIPPED", "RETIRED"}
PR_STATUSES = {"SHIPPED", "IN_FLIGHT"}
EXPECTED_IDS = {
    "#22",
    "F2",
    "#36",
    "#37",
    "#48",
    "#72",
    "#77",
    "#89",
    "#90",
    "#109",
    "#131",
    "#169",
    "#255",
    "#859",
    "F5",
    "F6",
    "F7",
    "F8",
    "MCP-SURFACE",
    "CPU-BACKEND",
    "REF-CALL-REGISTRY",
    "F10",
    "DD-004",
    "DD-006",
    "AST-DSL-PARITY",
    "MCP-LEAN-DEFAULT",
    "CONTINUOUS-REFRESH",
    "RUST-REPLACE-SYMLINK",
    "RUST-REPLACE-TOCTOU",
}
CEO_IDS = {"#72", "#77", "#131", "#169"}
DEMAND_IDS = {
    "#255",
    "DD-006",
    "AST-DSL-PARITY",
    "MCP-LEAN-DEFAULT",
    "CONTINUOUS-REFRESH",
    "RUST-REPLACE-TOCTOU",
}
PROGRAM_OWNERS = {
    "MCP-SURFACE": "Task 4",
    "CPU-BACKEND": "Task 5",
    "F6": "Tasks 6-7",
    "F5": "Task 8",
    "REF-CALL-REGISTRY": "Task 9",
    "F7": "Tasks 10-11",
    "F8": "Tasks 12-13",
}
LIFECYCLE_IDS = set(PROGRAM_OWNERS) | {"#89", "#90", "#859"}
IMPLEMENTATION_PRS_RE = re.compile(
    r"(?:^|; )Implementation PRs: (?P<prs>PR #[1-9]\d*(?:, PR #[1-9]\d*)*)(?:;|$)"
)
CLOSURE_PR_RE = re.compile(r"(?:^|; )Closure PR: (?P<pr>PR #[1-9]\d*)(?:;|$)")
MERGED_SHA_RE = re.compile(r"(?:^|; )Merged SHA: (?P<sha>[0-9a-f]{40})(?:;|$)")
WINDOWS_ACCOUNT_PATH_RE = re.compile(r"[A-Za-z]:[\\/]+Users[\\/]+(?!<)[^\\/\s]+", re.IGNORECASE)


@dataclasses.dataclass(frozen=True)
class StatusRow:
    item_id: str
    status: str
    pr: str
    trigger: str
    checked: bool


@dataclasses.dataclass(frozen=True)
class StatusIndex:
    version: str
    rows: dict[str, StatusRow]


def _section(text: str) -> str:
    heading_count = sum(line == STATUS_HEADING for line in text.splitlines())
    if heading_count != 1:
        raise AssertionError(f"expected one {STATUS_HEADING!r}, found {heading_count}")
    tail = text.split(f"{STATUS_HEADING}\n", 1)[1]
    return tail.split("\n## ", 1)[0]


def _parse_status_index(
    text: str,
    *,
    expected_ids: set[str] | None = None,
    lifecycle_ids: set[str] | None = None,
) -> StatusIndex:
    metadata_candidates = [line for line in text.splitlines() if line.startswith(VERSION_PREFIX)]
    if len(metadata_candidates) != 1:
        raise AssertionError(
            f"expected one canonical version line, found {len(metadata_candidates)}"
        )
    metadata = metadata_candidates[0]
    if VERSION_RE.fullmatch(metadata) is None:
        raise AssertionError(f"malformed canonical version line: {metadata!r}")

    nonblank = [line for line in _section(text).splitlines() if line.strip()]
    if not nonblank or nonblank[0] != metadata:
        raise AssertionError("canonical version must be the section's first nonblank line")

    rows: dict[str, StatusRow] = {}
    for line in nonblank[1:]:
        match = ROW_RE.fullmatch(line)
        if match is None:
            raise AssertionError(f"malformed or multiline canonical row: {line!r}")
        item_id = match.group("id")
        if item_id in rows:
            raise AssertionError(f"duplicate canonical ID: {item_id}")
        status = match.group("status")
        if status not in STATUSES:
            raise AssertionError(f"unknown canonical status: {status}")
        checked = match.group("checked") == "x"
        if checked != (status in TERMINAL):
            raise AssertionError(f"checkbox/status disagreement for {item_id}")
        pr = match.group("pr")
        if status in PR_STATUSES:
            if re.fullmatch(r"PR #[1-9]\d*", pr) is None:
                raise AssertionError(f"{item_id} requires one literal PR #NNN field")
        elif pr != "none":
            raise AssertionError(f"{item_id} must use PR: none for status {status}")
        trigger = match.group("trigger").strip()
        if not trigger:
            raise AssertionError(f"empty trigger for {item_id}")
        if trigger == "none" and status not in TERMINAL:
            raise AssertionError(f"nonterminal {item_id} requires a reopen trigger")
        if lifecycle_ids is not None and item_id in lifecycle_ids:
            implementation_matches = list(IMPLEMENTATION_PRS_RE.finditer(trigger))
            closure_matches = list(CLOSURE_PR_RE.finditer(trigger))
            merged_sha_matches = list(MERGED_SHA_RE.finditer(trigger))
            if status in {"IN_FLIGHT", "SHIPPED"}:
                if len(implementation_matches) != 1 or trigger.count("Implementation PRs:") != 1:
                    raise AssertionError(f"{item_id} requires an ordered implementation-PR list")
                implementation_match = implementation_matches[0]
                implementation_prs = implementation_match.group("prs").split(", ")
                if len(implementation_prs) != len(set(implementation_prs)):
                    raise AssertionError(f"{item_id} repeats an implementation PR")
                if implementation_prs[-1] != pr:
                    raise AssertionError(f"{item_id} final implementation PR must equal PR field")
            elif implementation_matches or "Implementation PRs:" in trigger:
                raise AssertionError(f"{item_id} cannot carry implementation PRs before IN_FLIGHT")
            if status == "SHIPPED" and (
                len(closure_matches) != 1 or trigger.count("Closure PR:") != 1
            ):
                raise AssertionError(f"{item_id} requires one closure PR")
            if status == "SHIPPED" and closure_matches[0].group("pr") in implementation_prs:
                raise AssertionError(
                    f"{item_id} closure PR must be separate from implementation PRs"
                )
            if status != "SHIPPED" and (closure_matches or "Closure PR:" in trigger):
                raise AssertionError(f"{item_id} cannot carry a closure PR before SHIPPED")
            if status == "SHIPPED" and (
                len(merged_sha_matches) != 1 or trigger.count("Merged SHA:") != 1
            ):
                raise AssertionError(f"{item_id} requires one merged implementation SHA")
            if status != "SHIPPED" and (merged_sha_matches or "Merged SHA:" in trigger):
                raise AssertionError(f"{item_id} cannot carry a merged SHA before SHIPPED")
        rows[item_id] = StatusRow(item_id, status, pr, trigger, checked)

    if not rows:
        raise AssertionError("canonical status index has no rows")
    if expected_ids is not None and set(rows) != expected_ids:
        missing = sorted(expected_ids - set(rows))
        extra = sorted(set(rows) - expected_ids)
        raise AssertionError(f"closed-world population drift: missing={missing}, extra={extra}")
    return StatusIndex(metadata.removeprefix(f"{VERSION_PREFIX} "), rows)


def _board_index() -> StatusIndex:
    return _parse_status_index(
        BOARD_PATH.read_text(encoding="utf-8"),
        expected_ids=EXPECTED_IDS,
        lifecycle_ids=LIFECYCLE_IDS,
    )


def _replace_once(text: str, old: str, new: str) -> str:
    assert text.count(old) == 1, old
    return text.replace(old, new, 1)


def _valid_document() -> str:
    return (
        "# synthetic\n\n"
        f"{STATUS_HEADING}\n\n"
        "Canonical status index version: 2026-08-02.1\n"
        "- [ ] **X** — Status: READY; PR: none; Trigger: first implementation PR\n\n"
        "## Historical narrative\n\n"
        "- [ ] **OLD** — Status: BROKEN; PR: #9; Trigger: outside parser\n"
    )


def _assert_status_ownership(index: StatusIndex) -> None:
    actual_ceo = {item_id for item_id, row in index.rows.items() if row.status == "CEO_GATED"}
    actual_demand = {item_id for item_id, row in index.rows.items() if row.status == "DEMAND_GATED"}
    assert actual_ceo == CEO_IDS
    assert actual_demand == DEMAND_IDS
    assert actual_ceo.isdisjoint(actual_demand)


def test_minimal_valid_synthetic_document() -> None:
    index = _parse_status_index(_valid_document(), expected_ids={"X"})
    assert index.version == "2026-08-02.1"
    assert index.rows["X"].status == "READY"


def test_missing_version_metadata_is_rejected() -> None:
    text = _valid_document().replace("Canonical status index version: 2026-08-02.1\n", "")
    with pytest.raises(AssertionError, match="version"):
        _parse_status_index(text)


def test_duplicate_version_metadata_is_rejected() -> None:
    text = _valid_document().replace(
        "Canonical status index version: 2026-08-02.1\n",
        "Canonical status index version: 2026-08-02.1\n"
        "Canonical status index version: 2026-08-02.2\n",
    )
    with pytest.raises(AssertionError, match="found 2"):
        _parse_status_index(text)


def test_malformed_version_metadata_is_rejected() -> None:
    text = _valid_document().replace("2026-08-02.1", "version-one")
    with pytest.raises(AssertionError, match="malformed"):
        _parse_status_index(text)


def test_missing_canonical_section_is_rejected() -> None:
    text = _valid_document().replace(STATUS_HEADING, "## Status notes")
    with pytest.raises(AssertionError, match="found 0"):
        _parse_status_index(text)


def test_duplicate_canonical_section_is_rejected() -> None:
    text = _valid_document() + f"\n{STATUS_HEADING}\n"
    with pytest.raises(AssertionError, match="found 2"):
        _parse_status_index(text)


def test_duplicate_ids_are_rejected() -> None:
    row = "- [ ] **X** — Status: READY; PR: none; Trigger: first implementation PR\n"
    text = _valid_document().replace(row, row + row)
    with pytest.raises(AssertionError, match="duplicate canonical ID"):
        _parse_status_index(text)


def test_malformed_row_is_rejected() -> None:
    text = _valid_document().replace("Status: READY;", "Status READY;")
    with pytest.raises(AssertionError, match="malformed"):
        _parse_status_index(text)


def test_multiline_row_is_rejected() -> None:
    text = _valid_document().replace(
        "Trigger: first implementation PR",
        "Trigger: first implementation PR\n  continuation is forbidden",
    )
    with pytest.raises(AssertionError, match="malformed"):
        _parse_status_index(text)


def test_checkbox_status_mismatch_is_rejected() -> None:
    text = _valid_document().replace("- [ ] **X**", "- [x] **X**")
    with pytest.raises(AssertionError, match="checkbox/status disagreement"):
        _parse_status_index(text)


def test_missing_pr_is_rejected() -> None:
    text = _valid_document().replace("Status: READY; PR: none", "Status: IN_FLIGHT; PR: none")
    with pytest.raises(AssertionError, match="requires one literal"):
        _parse_status_index(text)


def test_multiple_prs_are_rejected() -> None:
    text = _valid_document().replace(
        "Status: READY; PR: none", "Status: IN_FLIGHT; PR: PR #1, PR #2"
    )
    with pytest.raises(AssertionError, match="requires one literal"):
        _parse_status_index(text)


def test_nonliteral_pr_value_is_rejected() -> None:
    text = _valid_document().replace("Status: READY; PR: none", "Status: IN_FLIGHT; PR: #123")
    with pytest.raises(AssertionError, match="requires one literal"):
        _parse_status_index(text)


def test_in_flight_lifecycle_requires_ordered_implementation_prs() -> None:
    text = _valid_document().replace(
        "Status: READY; PR: none; Trigger: first implementation PR",
        "Status: IN_FLIGHT; PR: PR #9; Trigger: receipt deliberately omitted",
    )
    with pytest.raises(AssertionError, match="ordered implementation-PR list"):
        _parse_status_index(text, lifecycle_ids={"X"})


def test_in_flight_lifecycle_final_pr_must_match_pr_field() -> None:
    text = _valid_document().replace(
        "Status: READY; PR: none; Trigger: first implementation PR",
        "Status: IN_FLIGHT; PR: PR #9; Trigger: Implementation PRs: PR #7, PR #8",
    )
    with pytest.raises(AssertionError, match="must equal PR field"):
        _parse_status_index(text, lifecycle_ids={"X"})


def test_in_flight_lifecycle_rejects_duplicate_prs() -> None:
    text = _valid_document().replace(
        "Status: READY; PR: none; Trigger: first implementation PR",
        "Status: IN_FLIGHT; PR: PR #9; Trigger: Implementation PRs: PR #9, PR #9",
    )
    with pytest.raises(AssertionError, match="repeats an implementation PR"):
        _parse_status_index(text, lifecycle_ids={"X"})


def test_in_flight_lifecycle_rejects_leading_zero_pr_alias() -> None:
    text = _valid_document().replace(
        "Status: READY; PR: none; Trigger: first implementation PR",
        "Status: IN_FLIGHT; PR: PR #9; Trigger: Implementation PRs: PR #09, PR #9",
    )
    with pytest.raises(AssertionError, match="ordered implementation-PR list"):
        _parse_status_index(text, lifecycle_ids={"X"})


def test_shipped_lifecycle_requires_separate_closure_pr() -> None:
    text = _valid_document().replace(
        "- [ ] **X** — Status: READY; PR: none; Trigger: first implementation PR",
        "- [x] **X** — Status: SHIPPED; PR: PR #9; Trigger: Implementation PRs: PR #7, PR #9",
    )
    with pytest.raises(AssertionError, match="requires one closure PR"):
        _parse_status_index(text, lifecycle_ids={"X"})


def test_shipped_lifecycle_preserves_ordered_implementation_and_closure_prs() -> None:
    text = _valid_document().replace(
        "- [ ] **X** — Status: READY; PR: none; Trigger: first implementation PR",
        "- [x] **X** — Status: SHIPPED; PR: PR #9; "
        "Trigger: Implementation PRs: PR #7, PR #9; Closure PR: PR #10; "
        "Merged SHA: 0123456789abcdef0123456789abcdef01234567; treatment green",
    )
    index = _parse_status_index(text, lifecycle_ids={"X"})
    assert index.rows["X"].pr == "PR #9"


def test_shipped_lifecycle_rejects_closure_pr_reused_as_implementation_pr() -> None:
    text = _valid_document().replace(
        "- [ ] **X** — Status: READY; PR: none; Trigger: first implementation PR",
        "- [x] **X** — Status: SHIPPED; PR: PR #9; "
        "Trigger: Implementation PRs: PR #7, PR #9; Closure PR: PR #9; "
        "Merged SHA: 0123456789abcdef0123456789abcdef01234567",
    )
    with pytest.raises(AssertionError, match="closure PR must be separate"):
        _parse_status_index(text, lifecycle_ids={"X"})


def test_shipped_lifecycle_requires_merged_implementation_sha() -> None:
    text = _valid_document().replace(
        "- [ ] **X** — Status: READY; PR: none; Trigger: first implementation PR",
        "- [x] **X** — Status: SHIPPED; PR: PR #9; "
        "Trigger: Implementation PRs: PR #7, PR #9; Closure PR: PR #10",
    )
    with pytest.raises(AssertionError, match="requires one merged implementation SHA"):
        _parse_status_index(text, lifecycle_ids={"X"})


def test_unknown_status_is_rejected() -> None:
    text = _valid_document().replace("Status: READY", "Status: WAITING")
    with pytest.raises(AssertionError, match="unknown canonical status"):
        _parse_status_index(text)


def test_empty_trigger_is_rejected() -> None:
    text = _valid_document().replace("Trigger: first implementation PR", "Trigger: ")
    with pytest.raises(AssertionError, match="malformed"):
        _parse_status_index(text)


def test_ceo_demand_duplication_is_rejected() -> None:
    index = _board_index()
    duplicate = dataclasses.replace(index.rows["#48"], status="DEMAND_GATED")
    mutated = dataclasses.replace(index, rows={**index.rows, "#48": duplicate})
    with pytest.raises(AssertionError):
        _assert_status_ownership(mutated)


def test_closed_world_population_drift_is_rejected() -> None:
    with pytest.raises(AssertionError, match="closed-world population drift"):
        _parse_status_index(_valid_document(), expected_ids={"X", "Y"})


def test_historical_prose_is_not_parsed() -> None:
    index = _parse_status_index(_valid_document(), expected_ids={"X"})
    assert set(index.rows) == {"X"}


def test_live_board_parser_controls_are_green() -> None:
    index = _board_index()
    assert set(index.rows) == EXPECTED_IDS


def test_exit_contract_retirement() -> None:
    row = _board_index().rows["#22"]
    assert row.status == "RETIRED"
    assert all(token in row.trigger for token in ("exit 0", "exit 1", "exit 2"))
    contracts = CONTRACTS_PATH.read_text(encoding="utf-8")
    assert (
        "An unhonoured explicit GPU request does not independently change that exit code"
        in contracts
    )
    main_source = MAIN_PATH.read_text(encoding="utf-8")
    assert "gpu_request_unhonoured" in main_source
    assert "does NOT independently force exit 2" in main_source
    result = SearchResult(matches=[], total_matches=0, total_files=0)
    result.requested_gpu_device_ids = [0]
    result.routing_backend = "CPUBackend"
    result.sidecar_used = False
    assert gpu_request_unhonoured(result) is True
    assert result.result_incomplete is False


def test_exit_contract_executes_match_no_match_and_incomplete(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from tensor_grep.cli import bootstrap

    monkeypatch.setattr(cli_main, "resolve_native_tg_binary", lambda: None)
    monkeypatch.setattr(bootstrap, "resolve_native_tg_binary", lambda: None)
    (tmp_path / "sample.txt").write_text("needle\n", encoding="utf-8")
    runner = CliRunner()

    matched = runner.invoke(app, ["search", "needle", str(tmp_path), "--cpu"])
    no_match = runner.invoke(app, ["search", "absent", str(tmp_path), "--cpu"])

    class IncompleteBackend:
        def search(self, file_path: str, pattern: str, config: object = None) -> SearchResult:
            del pattern, config
            return SearchResult(
                matches=[MatchLine(line_number=1, text="needle", file=file_path)],
                matched_file_paths=[file_path],
                match_counts_by_file={file_path: 1},
                total_files=1,
                total_matches=1,
                result_incomplete=True,
                incomplete_reason="bounded test scan stopped before full coverage",
            )

    class IncompletePipeline:
        def __init__(self, force_cpu: bool = False, config: object = None) -> None:
            del force_cpu, config

        def get_backend(self) -> IncompleteBackend:
            return IncompleteBackend()

    class OneFileScanner:
        scan_truncated = False
        scan_truncation_cause = None
        unreadable_path_count = 0
        unreadable_path_sample: ClassVar[list[str]] = []
        max_scan_entries = 200_000

        def __init__(self, config: object = None) -> None:
            del config

        def walk(self, path: str) -> list[str]:
            return [str(Path(path) / "sample.txt")]

    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", IncompletePipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", OneFileScanner)
    monkeypatch.setattr(
        "tensor_grep.backends.ripgrep_backend.RipgrepBackend.is_available",
        lambda self: False,
    )
    incomplete = runner.invoke(app, ["search", "needle", str(tmp_path), "--cpu", "--json"])

    assert matched.exit_code == 0, matched.output
    assert no_match.exit_code == 1, no_match.output
    assert incomplete.exit_code == 2, incomplete.output
    assert '"result_incomplete": true' in incomplete.stdout.lower()


def test_unhonoured_explicit_gpu_request_executes_with_complete_exit_codes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from tensor_grep.cli import bootstrap

    class CpuFallbackBackend:
        def search(self, file_path: str, pattern: str, config: object = None) -> SearchResult:
            del config
            matches = (
                [MatchLine(line_number=1, text="needle", file=file_path)]
                if pattern == "needle"
                else []
            )
            return SearchResult(
                matches=matches,
                matched_file_paths=[file_path] if matches else [],
                match_counts_by_file={file_path: 1} if matches else {},
                total_files=1 if matches else 0,
                total_matches=len(matches),
                routing_backend="CPUBackend",
                routing_reason="gpu-explicit-request-cpu-fallback",
            )

    class CpuFallbackPipeline:
        selected_backend_name = "CPUBackend"
        selected_backend_reason = "gpu-explicit-request-cpu-fallback"
        selected_gpu_device_ids: ClassVar[list[int]] = []
        selected_gpu_chunk_plan_mb: ClassVar[list[tuple[int, int]]] = []

        def __init__(self, force_cpu: bool = False, config: object = None) -> None:
            del force_cpu, config

        def get_backend(self) -> CpuFallbackBackend:
            return CpuFallbackBackend()

    class OneFileScanner:
        scan_truncated = False
        scan_truncation_cause = None
        unreadable_path_count = 0
        unreadable_path_sample: ClassVar[list[str]] = []
        max_scan_entries = 200_000

        def __init__(self, config: object = None) -> None:
            del config

        def walk(self, path: str) -> list[str]:
            return [str(Path(path) / "sample.txt")]

    monkeypatch.setattr(cli_main, "resolve_native_tg_binary", lambda: None)
    monkeypatch.setattr(bootstrap, "resolve_native_tg_binary", lambda: None)
    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", CpuFallbackPipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", OneFileScanner)
    monkeypatch.setattr(
        "tensor_grep.backends.ripgrep_backend.RipgrepBackend.is_available",
        lambda self: False,
    )
    (tmp_path / "sample.txt").write_text("needle\n", encoding="utf-8")
    runner = CliRunner()

    matched = runner.invoke(
        app,
        ["search", "needle", str(tmp_path), "--gpu-device-ids", "0", "--json"],
    )
    no_match = runner.invoke(
        app,
        ["search", "absent", str(tmp_path), "--gpu-device-ids", "0", "--json"],
    )

    assert matched.exit_code == 0, matched.output
    assert no_match.exit_code == 1, no_match.output
    for result in (matched, no_match):
        payload = json.loads(result.stdout)
        assert payload["gpu_evidence_status"] == "unsupported"
        assert payload["gpu_proof"] is False
        assert payload["native_gpu_unavailable"] is True
        assert payload.get("result_incomplete", False) is False


def test_legacy_agent_id_retirement() -> None:
    row = _board_index().rows["F2"]
    assert row.status == "RETIRED"
    assert "legacy" in row.trigger.lower()
    ledger_source = LEDGER_STORE_PATH.read_text(encoding="utf-8")
    assert "Refusing outright was also rejected" in ledger_source
    assert "return _DEFAULT_AGENT_ID" in ledger_source


def test_f10_maxsim_retirement() -> None:
    row = _board_index().rows["F10"]
    assert row.status == "RETIRED"
    assert "TG_LATE_RERANK=1" in row.trigger
    assert "0.068" in row.trigger and "0.305" in row.trigger
    backlog = BACKLOG_PATH.read_text(encoding="utf-8")
    assert "F10 MaxSim: caller/installability census + RETIRE disposition" in backlog
    assert "Disposition: RETIRE" in backlog
    late = (ROOT / "src" / "tensor_grep" / "core" / "retrieval_late.py").read_text(encoding="utf-8")
    assert "RETIRED 2026-08-05 (task F10" in late


def test_dd004_typed_boundary_retirement() -> None:
    row = _board_index().rows["DD-004"]
    assert row.status == "RETIRED"
    assert "Backend Fail-Closed Contract" in row.trigger
    backlog = BACKLOG_PATH.read_text(encoding="utf-8")
    assert "DD-004 typed backend-error boundary: RETIRE disposition" in backlog
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert "## Backend Fail-Closed Contract" in agents


def test_shipped_receipts() -> None:
    rows = _board_index().rows
    assert rows["#109"].status == "SHIPPED" and rows["#109"].pr == "PR #605"
    assert rows["#36"].status == "SHIPPED" and rows["#36"].pr == "PR #903"
    assert rows["#37"].status == "SHIPPED" and rows["#37"].pr == "PR #908"
    board = BOARD_PATH.read_text(encoding="utf-8")
    hardware = board.split("## BLOCKED — environment", 1)[1].split("\n## ", 1)[0]
    for item_id in ("#109", "#36", "#37"):
        assert re.search(rf"^- \[ \] \*\*{re.escape(item_id)}\*\*", hardware, re.MULTILINE) is None


def test_mixed_90_reproduction_is_blocked_on_task2_program() -> None:
    """#90 stays reproduced, but READY was a false build license (BACKLOG reconcile).

    Renamed from `test_mixed_90_reproduction_is_ready`: the reproduction facts remain load-bearing;
    the status pin must match the Task 2A→2B/2C ownership gate, not invite a premature product GREEN.
    """
    row = _board_index().rows["#90"]
    assert row.status == "BLOCKED"
    assert "PR #571" in row.trigger
    assert "matched_rules=0" in row.trigger
    assert "total_matches=6" in row.trigger
    assert "Task 2A" in row.trigger
    audit = (ROOT / "docs" / "audits" / "2026-08-02-backlog-reconciliation.md").read_text(
        encoding="utf-8"
    )
    assert "RAW_SCAN_RC=0" in audit
    assert "TRANSLATED_SCAN_RC=0" in audit
    assert '"matched_rules":1' in audit and '"total_matches":6' in audit
    assert "skipped unreadable paths during ast scan" in audit
    hardware = (
        BOARD_PATH
        .read_text(encoding="utf-8")
        .split("## BLOCKED — environment", 1)[1]
        .split("\n## ", 1)[0]
    )
    assert re.search(r"^- \[ \] \*\*#90\*\*", hardware, re.MULTILINE) is None
    program = (
        BOARD_PATH
        .read_text(encoding="utf-8")
        .split("## BLOCKED — program", 1)[1]
        .split("\n## ", 1)[0]
    )
    assert re.search(r"^- \[ \] \*\*#90\*\*", program, re.MULTILINE) is not None


def test_89_reproduced_path_domain_defect_is_blocked_on_task2_program() -> None:
    row = _board_index().rows["#89"]
    assert row.status == "BLOCKED"
    assert "WSL-to-Windows path-domain" in row.trigger
    assert "Task 2A" in row.trigger
    audit = (ROOT / "docs" / "audits" / "2026-08-02-backlog-reconciliation.md").read_text(
        encoding="utf-8"
    )
    assert '"error":"path_not_found"' in audit
    assert "ls -ld /mnt/c/dev/projects/tensor-grep/src" in audit
    assert "RUST_BINARY=<windows-user>/bin/tg" in audit
    assert re.search(r"Linux DESKTOP-[^\s]+", audit) is None
    assert re.search(r"/home/(?!<)[^/\s]+", audit) is None
    assert re.search(r"/mnt/c/Users/(?!<)[^/\s]+", audit, re.IGNORECASE) is None
    assert WINDOWS_ACCOUNT_PATH_RE.search(audit) is None


@pytest.mark.parametrize(
    "candidate",
    [
        r"C:\Users\alice\bin\tg.exe",
        r'"path":"C:\\Users\\alice\\bin\\tg.exe"',
        "C:/Users/alice/bin/tg.exe",
    ],
)
def test_windows_account_path_privacy_guard_positive_controls(candidate: str) -> None:
    assert WINDOWS_ACCOUNT_PATH_RE.search(candidate) is not None


def test_859_is_shipped_with_audit_correction_retained() -> None:
    """#859 is SHIPPED, and the audit correction that shaped it must survive that.

    Renamed from `test_859_is_ready_with_audit_correction`: the READY pin was correct until the
    work landed (instance fix plus the class-level ratchet, merged as 211d850) and would otherwise
    have blocked its own reconciliation. The audit assertions below are NOT relaxed -- they are the
    load-bearing half. That correction records that a codemap-only test did NOT satisfy the
    class-level population contract, which is precisely why the shipped ratchet censuses every
    write callsite and FAILS on an unresolved candidate instead of letting it drop out of the
    population. Losing that text would let the same undersized test be re-proposed as sufficient.
    """
    row = _board_index().rows["#859"]
    assert row.status == "SHIPPED"
    assert "Task 3" in row.trigger
    audit = AUDIT_859_PATH.read_text(encoding="utf-8")
    assert "APPENDED CORRECTION — #859" in audit
    assert "codemap-only test did not satisfy the class-level population contract" in audit


def test_program_ownership_and_ready_statuses() -> None:
    """A program row keeps its owning task forever, and may LAWFULLY progress past READY.

    This previously asserted `row.status == "READY"` unconditionally, which forbade the exact
    transition every one of these rows documents in its own trigger ("first implementation PR moves
    this row to IN_FLIGHT"). The board could therefore never be reconciled after a program's first
    PR without editing this test -- so the board went stale instead, which is the failure mode the
    whole tracker exists to prevent.

    This is NOT a relaxation. Every PROGRAM_OWNERS id is in LIFECYCLE_IDS (see its definition), so
    the moment a row leaves READY it comes under the STRICTER lifecycle contract enforced in
    `_parse_status_index`: exactly one ordered `Implementation PRs:` list whose final entry equals
    the `PR:` field, plus -- for SHIPPED -- exactly one `Closure PR:` that is NOT one of the
    implementation PRs, and exactly one 40-hex `Merged SHA:`. A row that moves without those fails
    there. What is dropped here is only the blanket freeze; what replaces it is a stricter gate.
    """
    rows = _board_index().rows
    for item_id, task_text in PROGRAM_OWNERS.items():
        row = rows[item_id]
        # Ownership is invariant across the whole lifecycle: a program row never changes hands.
        assert task_text in row.trigger, f"{item_id} lost its owning task text"
        if row.status == "READY":
            assert "first implementation PR" in row.trigger, (
                f"{item_id} is READY but does not say what moves it"
            )
        elif row.status == "BLOCKED":
            # Lawful when the row is owned but not buildable on this desktop (rust/e2e ban,
            # Task 2C ladder, etc.). READY would be a false build license (A71/A76).
            assert "blocked" in row.trigger.lower() or "BLOCKED" in row.trigger, (
                f"{item_id} is BLOCKED but trigger does not name the blocker"
            )
        else:
            assert row.status in {"IN_FLIGHT", "SHIPPED"}, (
                f"{item_id} left READY into an unlawful status {row.status!r}; a program row may "
                "progress to BLOCKED, IN_FLIGHT, or SHIPPED"
            )


def test_ceo_and_demand_ownership() -> None:
    index = _board_index()
    _assert_status_ownership(index)
    rust_row = index.rows["RUST-REPLACE-SYMLINK"]
    assert "untrusted-destination threat model" in rust_row.trigger
    assert "compatibility" in rust_row.trigger


def test_handoff_version_and_current_prose() -> None:
    board_version = _board_index().version
    handoff = HANDOFF_PATH.read_text(encoding="utf-8")
    metadata = [line for line in handoff.splitlines() if line.startswith(VERSION_PREFIX)]
    assert len(metadata) == 1
    assert VERSION_RE.fullmatch(metadata[0])
    assert metadata[0].removeprefix(f"{VERSION_PREFIX} ") == board_version
    current = handoff.split("## Current Backlog Closeout", 1)[1].split("\n## ", 1)[0]
    assert "v1.45" not in current
    assert "v1.9.1" not in current
    assert "Tasks 3\u201315" in current
    board = BOARD_PATH.read_text(encoding="utf-8")
    live = board.split("## Live campaign snapshot", 1)[1].split("\n## ", 1)[0]
    # 2026-08-12 stamp retarget (A79): the snapshot now narrates Task 2A's advanced-but-blocked
    # state rather than the completed Task-2 reconciliation checkpoint it replaced.
    assert "Task 2A RED remains correctly blocked" in live
    assert "canonical index" in live
    assert CEO_AUDIT_PATH.name in live
    ceo_audit = CEO_AUDIT_PATH.read_text(encoding="utf-8")
    assert "Every unfinished backlog item (17)" in ceo_audit
    assert "Blocked — not build licenses (6)" in ceo_audit
    assert "CEO decision-gated — nonfinancial (4)" in ceo_audit
    assert "CEO financial stop (1)" in ceo_audit
    assert "Demand / research gated (6)" in ceo_audit
    assert "Terminal rows (12)" in ceo_audit
    assert "A77" in ceo_audit and "A82" in ceo_audit

"""Closed-world, deterministic contract for the live backlog status index.

GitHub state is intentionally absent from this module. Live PR, issue, CI, and release facts belong
in the dated reconciliation audit; this test only proves that committed tracker claims are complete,
unambiguous, and consistent with source-controlled contracts.
"""

from __future__ import annotations

import dataclasses
import re
from pathlib import Path

import pytest

from tensor_grep.cli.formatters.json_fmt import gpu_request_unhonoured
from tensor_grep.core.result import SearchResult

ROOT = Path(__file__).resolve().parents[2]
BOARD_PATH = ROOT / "docs" / "TASK_BOARD.md"
BACKLOG_PATH = ROOT / "docs" / "BACKLOG.md"
HANDOFF_PATH = ROOT / "docs" / "SESSION_HANDOFF.md"
CONTRACTS_PATH = ROOT / "docs" / "CONTRACTS.md"
MAIN_PATH = ROOT / "src" / "tensor_grep" / "cli" / "main.py"
LEDGER_STORE_PATH = ROOT / "src" / "tensor_grep" / "cli" / "ledger_store.py"
AUDIT_859_PATH = ROOT / "docs" / "audits" / "2026-08-01-backlog-verification-receipts.md"

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
}
CEO_IDS = {"#48", "#72", "#77", "#131", "#169"}
DEMAND_IDS = {
    "#255",
    "F10",
    "DD-004",
    "DD-006",
    "AST-DSL-PARITY",
    "MCP-LEAN-DEFAULT",
    "CONTINUOUS-REFRESH",
    "RUST-REPLACE-SYMLINK",
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
            if re.fullmatch(r"PR #\d+", pr) is None:
                raise AssertionError(f"{item_id} requires one literal PR #NNN field")
        elif pr != "none":
            raise AssertionError(f"{item_id} must use PR: none for status {status}")
        trigger = match.group("trigger").strip()
        if not trigger:
            raise AssertionError(f"empty trigger for {item_id}")
        if trigger == "none" and status not in TERMINAL:
            raise AssertionError(f"nonterminal {item_id} requires a reopen trigger")
        rows[item_id] = StatusRow(item_id, status, pr, trigger, checked)

    if not rows:
        raise AssertionError("canonical status index has no rows")
    if expected_ids is not None and set(rows) != expected_ids:
        missing = sorted(expected_ids - set(rows))
        extra = sorted(set(rows) - expected_ids)
        raise AssertionError(f"closed-world population drift: missing={missing}, extra={extra}")
    return StatusIndex(metadata.removeprefix(f"{VERSION_PREFIX} "), rows)


def _board_index() -> StatusIndex:
    return _parse_status_index(BOARD_PATH.read_text(encoding="utf-8"), expected_ids=EXPECTED_IDS)


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


def test_legacy_agent_id_retirement() -> None:
    row = _board_index().rows["F2"]
    assert row.status == "RETIRED"
    assert "legacy" in row.trigger.lower()
    ledger_source = LEDGER_STORE_PATH.read_text(encoding="utf-8")
    assert "Refusing outright was also rejected" in ledger_source
    assert "return _DEFAULT_AGENT_ID" in ledger_source


def test_shipped_receipts() -> None:
    rows = _board_index().rows
    assert rows["#109"].status == "SHIPPED" and rows["#109"].pr == "PR #605"
    assert rows["#36"].status == "SHIPPED" and rows["#36"].pr == "PR #903"
    assert rows["#37"].status == "SHIPPED" and rows["#37"].pr == "PR #908"
    board = BOARD_PATH.read_text(encoding="utf-8")
    hardware = board.split("## BLOCKED — environment", 1)[1].split("\n## ", 1)[0]
    for item_id in ("#109", "#36", "#37"):
        assert re.search(rf"^- \[ \] \*\*{re.escape(item_id)}\*\*", hardware, re.MULTILINE) is None


def test_mixed_90_retirement() -> None:
    row = _board_index().rows["#90"]
    assert row.status == "RETIRED"
    assert "PR #571" in row.trigger
    assert "non-reproducing" in row.trigger
    assert "non-defect" in row.trigger
    blocked = (
        BOARD_PATH
        .read_text(encoding="utf-8")
        .split("## BLOCKED — environment", 1)[1]
        .split("\n## ", 1)[0]
    )
    assert re.search(r"^- \[ \] \*\*#90\*\*", blocked, re.MULTILINE) is None


def test_89_reproduced_path_domain_defect_is_ready() -> None:
    row = _board_index().rows["#89"]
    assert row.status == "READY"
    assert "WSL-to-Windows path-domain" in row.trigger
    audit = (ROOT / "docs" / "audits" / "2026-08-02-backlog-reconciliation.md").read_text(
        encoding="utf-8"
    )
    assert '"error":"path_not_found"' in audit
    assert "ls -ld /mnt/c/dev/projects/tensor-grep/src" in audit
    assert "RUST_BINARY=/mnt/c/Users/oimir/bin/tg" in audit


def test_859_is_ready_with_audit_correction() -> None:
    row = _board_index().rows["#859"]
    assert row.status == "READY"
    assert "Task 3" in row.trigger
    audit = AUDIT_859_PATH.read_text(encoding="utf-8")
    assert "APPENDED CORRECTION — #859" in audit
    assert "codemap-only test did not satisfy the class-level population contract" in audit


def test_program_ownership_and_ready_statuses() -> None:
    rows = _board_index().rows
    for item_id, task_text in PROGRAM_OWNERS.items():
        row = rows[item_id]
        assert row.status == "READY"
        assert task_text in row.trigger
        assert "first implementation PR" in row.trigger


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
    assert "Tasks 2\u201315" in current

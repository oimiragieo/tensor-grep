"""Real-binary TDD for `tg prepare` (CEO #5 flagship): a single edit-readiness CUJ call that
replaces the orient -> search -> agent -> route-test -> callers -> evidence -> ledger loop.

Real subprocess (`python -m tensor_grep`), not CliRunner, per AGENTS.md's "dogfood the real
binary" rule and the anti-hang-test-protocol skill: a subprocess `timeout=` is a genuine
OS-level kill if a deadline regresses back to unbounded, whereas an in-process CliRunner hang
would hang the whole pytest run (and every other queued test) with it. Mirrors
`tests/integration/test_agent_cold_deadline_tail_sla_220.py`'s harness shape.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

# Anti-hang (two independent layers, per the anti-hang-test-protocol skill): this subprocess
# timeout is the inner, OS-level kill -- a regression back to fully-unbounded behavior fails FAST
# here (TimeoutExpired) instead of hanging the whole pytest run.
_SUBPROCESS_TIMEOUT_S = 90.0

_BILLING_MODULE = (
    '"""Monthly billing helpers."""\n\n\n'
    "def calculate_late_fee(balance, days_late):\n"
    '    """Compute the late fee owed on an overdue balance."""\n'
    "    return balance * 0.01 * days_late\n\n\n"
    "def apply_late_fee(account):\n"
    '    """Apply the computed late fee to an account balance."""\n'
    '    fee = calculate_late_fee(account["balance"], account["days_late"])\n'
    '    account["balance"] += fee\n'
    "    return account\n\n\n"
    "def process_billing_cycle(accounts):\n"
    '    """Run the monthly billing cycle across all accounts."""\n'
    "    return [apply_late_fee(account) for account in accounts]\n"
)
_RUN_MODULE = (
    "from billing import process_billing_cycle\n\n\n"
    "def main():\n"
    "    return process_billing_cycle([])\n\n\n"
    'if __name__ == "__main__":\n'
    "    main()\n"
)
_TEST_MODULE = (
    "from billing import calculate_late_fee\n\n\n"
    "def test_calculate_late_fee():\n"
    "    assert calculate_late_fee(100, 2) == 2.0\n"
)
_PYPROJECT = (
    "[project]\n"
    'name = "billing-fixture"\n'
    'version = "0.1.0"\n\n'
    "[tool.pytest.ini_options]\n"
    'testpaths = ["tests"]\n'
)
_ORPHAN_MODULE = (
    '"""Fixture module for the blast-radius floor zero-caller control."""\n\n\n'
    "def calculate_early_payment_discount(balance, days_early):\n"
    '    """Compute an early-payment discount.\n\n'
    "    Intentionally never called anywhere in this fixture -- the zero-caller control for\n"
    "    blast_radius_floor's honesty contract (see test_prepare_floor_reports_zero_callers_\n"
    '    honestly).\n    """\n'
    "    return balance * 0.02 * days_early\n"
)
_ORPHAN_PYPROJECT = '[project]\nname = "orphan-fixture"\nversion = "0.1.0"\n'


def _run_tg(
    args: list[str], *, cwd: Path, timeout: float = _SUBPROCESS_TIMEOUT_S
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    # Force daemon autostart off so a real subprocess run never leaks a background
    # session-daemon process tied to this fixture's temp directory (mirrors the #220 SLA test's
    # own rationale -- a leaked daemon would silently reroute a later cold-path call).
    env["TG_SESSION_DAEMON_AUTOSTART"] = "0"
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return subprocess.run(
        [sys.executable, "-m", "tensor_grep", *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _write_billing_fixture(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "pyproject.toml").write_text(_PYPROJECT, encoding="utf-8")
    (root / "billing.py").write_text(_BILLING_MODULE, encoding="utf-8")
    (root / "run.py").write_text(_RUN_MODULE, encoding="utf-8")
    tests_dir = root / "tests"
    tests_dir.mkdir(exist_ok=True)
    (tests_dir / "test_billing.py").write_text(_TEST_MODULE, encoding="utf-8")


@pytest.fixture(scope="module")
def billing_repo(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A tiny, unambiguous repo: 3 billing functions that each call the next (so EVERY
    plausible primary target has >=1 real caller regardless of which one the ranker picks) plus
    a pyproject.toml + tests/ dir for Python validation-command detection."""
    root = tmp_path_factory.mktemp("prepare_cuj") / "billing"
    _write_billing_fixture(root)
    return root


@pytest.fixture(scope="module")
def large_billing_repo(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Same billing fixture, padded with many small unrelated files so a full repo-map scan
    takes measurably longer than a sub-second --deadline -- mirrors
    test_agent_cold_deadline_tail_sla_220.py's manifest_heavy_repo rationale: a too-small fixture
    can complete a scan inside even a tight deadline on a fast runner, making a truncation test
    flaky. Padding content is deliberately generic (no billing/fee/payment terms) so it never
    competes with billing.py for ranking on a billing-themed query."""
    root = tmp_path_factory.mktemp("prepare_cuj_large") / "billing"
    _write_billing_fixture(root)
    padding_root = root / "bulk"
    for project_index in range(60):
        project = padding_root / f"proj{project_index:03d}"
        project.mkdir(parents=True)
        for file_index in range(4):
            (project / f"mod{file_index}.py").write_text(
                f"def helper_{project_index}_{file_index}():\n    return {file_index}\n",
                encoding="utf-8",
            )
    return root


@pytest.fixture(scope="module")
def orphan_repo(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A repo containing exactly one symbol that is DEFINED but has NO callers anywhere -- the
    zero-caller control (bidirectional-oracle half) for
    test_prepare_floor_keyed_on_selected_symbol_not_query's positive (callers_count>=1)
    assertion: proves blast_radius_floor reports 0 honestly instead of vacuously always finding
    >=1 caller. Deliberately a SEPARATE, isolated fixture (not sharing billing_repo) so this
    orphan symbol can never become a ranking candidate for any OTHER test's query."""
    root = tmp_path_factory.mktemp("prepare_cuj_orphan") / "orphan"
    root.mkdir(parents=True)
    (root / "pyproject.toml").write_text(_ORPHAN_PYPROJECT, encoding="utf-8")
    (root / "orphan.py").write_text(_ORPHAN_MODULE, encoding="utf-8")
    return root


@pytest.fixture(scope="module")
def prepare_named_symbol_payload(billing_repo: Path) -> dict[str, object]:
    """One `tg prepare` call with QUERY set to the exact symbol name -- exercises the
    'capsule already collected call-site evidence' reuse path (agent_capsule.py:536/546 gate
    PASSES, `_target_symbol_was_explicitly_requested` is True). Shared across the tests that only
    read this same complete payload, to avoid a second real subprocess invocation."""
    result = _run_tg(
        ["prepare", str(billing_repo), "calculate_late_fee", "--json"], cwd=billing_repo
    )
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert isinstance(payload, dict)
    return payload


def test_prepare_complete_run_has_all_five_outputs(
    prepare_named_symbol_payload: dict[str, object],
) -> None:
    """Test 1: primary_target, blast_radius_floor.callers_count>=1, non-empty
    validation_commands, coordination.claim.argv, and an ABSENT honesty tail all in one call."""
    payload = prepare_named_symbol_payload
    primary_target = payload["primary_target"]
    assert isinstance(primary_target, dict)
    assert primary_target.get("symbol") == "calculate_late_fee", payload

    floor = payload["blast_radius_floor"]
    assert isinstance(floor, dict)
    assert floor.get("callers_count", 0) >= 1, floor

    validation_commands = payload["validation_commands"]
    assert isinstance(validation_commands, list)
    assert validation_commands, payload

    coordination = payload["coordination"]
    assert isinstance(coordination, dict)
    claim = coordination["claim"]
    assert isinstance(claim, dict)
    assert claim.get("argv"), coordination

    for key in ("partial", "partial_reason", "deadline_limit", "result_incomplete"):
        assert key not in payload, f"{key} unexpectedly present on a complete run: {payload}"


def test_prepare_validation_commands_are_python_shaped(
    prepare_named_symbol_payload: dict[str, object],
) -> None:
    """Test 2: a Python primary target must never silently get npm/yarn validation commands
    (CONTRACTS.md:94's validation_alignment contract) -- prepare must not corrupt what
    build_agent_capsule already gets right when it reuses validation_commands verbatim."""
    payload = prepare_named_symbol_payload
    validation_commands = payload["validation_commands"]
    assert isinstance(validation_commands, list)
    commands_blob = " ".join(str(item) for item in validation_commands).lower()
    assert "npm" not in commands_blob, validation_commands
    assert "yarn" not in commands_blob, validation_commands
    assert any(marker in commands_blob for marker in ("pytest", "py_compile")), validation_commands


def test_prepare_floor_keyed_on_selected_symbol_not_query(billing_repo: Path) -> None:
    """Test 3: an NL query that never spells out any billing symbol's exact name must still get
    a real blast-radius floor via the supplementary scan, proving the agent_capsule.py:536
    explicit-request gate was bypassed rather than silently leaving the floor empty."""
    query = "the billing job should skip accounts that already paid earlier this month"
    # None of the 3 real symbol names ("calculate_late_fee", "apply_late_fee",
    # "process_billing_cycle") appear verbatim as a whole token in this query.
    for symbol_name in ("calculate_late_fee", "apply_late_fee", "process_billing_cycle"):
        assert symbol_name not in query.split(), query

    result = _run_tg(["prepare", str(billing_repo), query, "--json"], cwd=billing_repo)
    assert result.returncode in (0, 2), result.stdout + result.stderr
    payload = json.loads(result.stdout)

    primary_target = payload["primary_target"]
    assert isinstance(primary_target, dict)
    selected_symbol = primary_target.get("symbol")
    assert selected_symbol, payload

    floor = payload["blast_radius_floor"]
    assert isinstance(floor, dict)
    assert floor.get("symbol") == selected_symbol, (floor, selected_symbol)
    assert floor.get("source") == "supplementary_blast_radius", floor
    assert floor.get("callers_count", 0) >= 1, floor


def test_prepare_floor_reports_zero_callers_honestly(orphan_repo: Path) -> None:
    """Zero-caller control (bidirectional-oracle half) pairing with
    test_prepare_floor_keyed_on_selected_symbol_not_query's positive: a symbol that is DEFINED
    but has NO callers anywhere must report callers_count == 0 through a REAL floor source (not
    an error), and the run must still exit 0 -- a complete scan that genuinely finds zero
    callers is not a truncation. Without this control, a floor implementation that always
    reported callers_count >= 1 regardless of the real caller graph would pass the positive
    test's `callers_count >= 1` assertion vacuously."""
    result = _run_tg(
        ["prepare", str(orphan_repo), "calculate_early_payment_discount", "--json"],
        cwd=orphan_repo,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)

    primary_target = payload["primary_target"]
    assert isinstance(primary_target, dict)
    assert primary_target.get("symbol") == "calculate_early_payment_discount", payload

    floor = payload["blast_radius_floor"]
    assert isinstance(floor, dict)
    assert floor.get("callers_count") == 0, floor
    assert floor.get("top_callers") == [], floor
    assert floor.get("source") in ("capsule_call_site_evidence", "supplementary_blast_radius"), (
        floor
    )
    assert "error" not in floor, floor

    for key in ("partial", "partial_reason", "deadline_limit", "result_incomplete"):
        assert key not in payload, f"{key} unexpectedly present on a complete run: {payload}"


def test_prepare_claim_emit_only_by_default(billing_repo: Path) -> None:
    """Test 4a: without --claim, coordination.claim.submitted stays false and prepare (a
    read-oriented command) has NO write side effect -- the ledger directory must not exist."""
    ledger_dir = billing_repo / ".tensor-grep" / "ledger"
    result = _run_tg(
        ["prepare", str(billing_repo), "calculate_late_fee", "--json"], cwd=billing_repo
    )
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    coordination = payload["coordination"]
    assert isinstance(coordination, dict)
    claim = coordination["claim"]
    assert isinstance(claim, dict)
    assert claim.get("submitted") is False, claim
    assert not ledger_dir.exists(), "tg prepare without --claim must not write the ledger"


def test_prepare_claim_flag_submits(billing_repo: Path) -> None:
    """Test 4b: --claim opts into an actual ledger_store.submit_claim call: submitted becomes
    true, a claim_id comes back, and the ledger directory is created."""
    result = _run_tg(
        ["prepare", str(billing_repo), "calculate_late_fee", "--claim", "--json"],
        cwd=billing_repo,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    coordination = payload["coordination"]
    assert isinstance(coordination, dict)
    claim = coordination["claim"]
    assert isinstance(claim, dict)
    assert claim.get("submitted") is True, claim
    claim_result = claim.get("result")
    assert isinstance(claim_result, dict)
    submitted_claim = claim_result.get("claim")
    assert isinstance(submitted_claim, dict)
    assert submitted_claim.get("claim_id"), claim
    assert (billing_repo / ".tensor-grep" / "ledger").exists()


def test_prepare_exits_2_on_truncation(large_billing_repo: Path) -> None:
    """Test 5: a tiny --deadline must truncate the (padded) scan, exit 2, and still print the
    full honest JSON -- never a silent exit 0 that reads as a complete result."""
    result = _run_tg(
        [
            "prepare",
            str(large_billing_repo),
            "calculate_late_fee",
            "--deadline",
            "0.1",
            "--json",
        ],
        cwd=large_billing_repo,
    )
    assert result.returncode == 2, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload.get("partial") is True, payload
    assert payload.get("partial_reason") == "deadline", payload
    assert payload.get("deadline_limit", {}).get("deadline_exceeded") is True, payload


def test_prepare_bounded_wall_clock(large_billing_repo: Path) -> None:
    """Test 6: --deadline 3 must keep wall-to-exit bounded (deadline + generous slack), on the
    SAME padded fixture used to prove truncation actually happens -- outcome-agnostic (mirrors
    test_agent_cold_deadline_tail_sla_220.py: a fast runner may legitimately finish under the
    deadline, which is also a valid, honest pass)."""
    deadline = 3.0
    started_at = time.monotonic()
    result = _run_tg(
        [
            "prepare",
            str(large_billing_repo),
            "calculate_late_fee",
            "--deadline",
            str(deadline),
            "--json",
        ],
        cwd=large_billing_repo,
        timeout=deadline + 60.0,
    )
    elapsed = time.monotonic() - started_at

    assert elapsed < deadline + 10.0, (
        f"tg prepare ran {elapsed:.1f}s against a {deadline}s --deadline -- looks unbounded again"
    )
    payload = json.loads(result.stdout)
    if result.returncode == 2:
        assert payload.get("partial") is True, result.stdout
        assert payload.get("deadline_limit", {}).get("deadline_exceeded") is True, result.stdout
    else:
        assert result.returncode == 0, result.stdout + result.stderr
        assert payload.get("partial") is not True, (
            "a run that beat the deadline should not ALSO claim a deadline cutoff -- "
            f"{result.stdout}"
        )


def test_prepare_no_deadline_runs_unbounded(billing_repo: Path) -> None:
    """--no-deadline must disable the default 60s bound entirely (mirrors route-test's own
    contract) -- a sanity smoke on the tiny fixture, not a timing assertion."""
    result = _run_tg(
        ["prepare", str(billing_repo), "calculate_late_fee", "--no-deadline", "--json"],
        cwd=billing_repo,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload.get("partial") is not True, payload

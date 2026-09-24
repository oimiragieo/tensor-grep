"""dogfood v1.122.1 remediation slice 1: `scripts/agent_readiness.py`'s outer timeout budget.

Split out of `test_agent_readiness_script.py` to stay under the test file-size ratchet
(`scripts/file_size_allowlist.json`); shares that file's `_load_script_module` pattern.
"""

import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest


def _load_script_module():
    root = Path(__file__).resolve().parents[2]
    module_path = root / "scripts" / "agent_readiness.py"
    spec = importlib.util.spec_from_file_location("agent_readiness_script_budget", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _plan_sum(module, checks) -> float:
    return sum(
        module.effective_budget_s(check)
        * (1 + min(max(0, check.retry_on_timeout), module._MAX_TIMEOUT_RETRIES))
        for check in checks
    )


def test_validator_only_checks_declare_a_budget_covering_their_subprocess_case_count(
    tmp_path,
) -> None:
    """A command=[] check's budget_s must cover its validator's own worst-case subprocess
    time (case count x per-case timeout), never just `timeout_s`. PRE-FIX RED: the sweep has
    ~29 cases x 30s against timeout_s=60; the launcher has 2 cases x 30s against timeout_s=30.
    """
    module = _load_script_module()
    checks = module.build_check_plan(
        repo_root=tmp_path,
        expected_version="1.122.1",
        include_shell_probes=True,
        include_wsl_probe=False,
        only_shell_probes=True,
    )
    by_name = {check.name: check for check in checks if check.command == []}

    sweep = by_name["public-search-advertised-flag-sweep"]
    sweep_worst_case = (
        len(module._public_search_flag_sweep_cases(Path("."))) + 1
    ) * module._READINESS_SUBPROCESS_CASE_TIMEOUT_S
    assert module.effective_budget_s(sweep) >= sweep_worst_case

    launcher = by_name["public-windows-launcher-quoted-patterns"]
    launcher_worst_case = (
        len(module._WINDOWS_LAUNCHER_QUOTED_CASE_LABELS)
        * module._READINESS_SUBPROCESS_CASE_TIMEOUT_S
    )
    assert module.effective_budget_s(launcher) >= launcher_worst_case


@pytest.mark.parametrize("is_windows", [True, False])
def test_total_timeout_budget_covers_the_full_check_plan(monkeypatch, tmp_path, is_windows) -> None:
    """derived budget >= plan sum under BOTH IS_WINDOWS branches, incl. bounded retries."""
    module = _load_script_module()
    monkeypatch.setattr(module, "IS_WINDOWS", is_windows)
    checks = module.build_check_plan(
        repo_root=tmp_path,
        expected_version="1.122.1",
        include_shell_probes=True,
        include_wsl_probe=False,
    )
    assert module.total_timeout_budget_s(checks) >= _plan_sum(module, checks)


def test_fixed_170s_dogfood_timeout_is_below_the_real_check_plan_sum(tmp_path) -> None:
    """RED control: the OLD hardcoded 170s must be provably too small for a real plan."""
    module = _load_script_module()
    checks = module.build_check_plan(
        repo_root=tmp_path,
        expected_version="1.122.1",
        include_shell_probes=True,
        include_wsl_probe=False,
    )
    assert 170.0 < _plan_sum(module, checks)


@pytest.mark.parametrize(
    "flags", [[], ["--no-shell-probes"], ["--no-wsl-probe"], ["--only-shell-probes"]]
)
def test_print_timeout_budget_flag_reports_json_and_exits_without_running_checks(
    tmp_path, flags
) -> None:
    module = _load_script_module()
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "fixture"\nversion = "1.0.0"\n', encoding="utf-8"
    )
    argv = ["--root", str(tmp_path), "--print-timeout-budget", *flags]
    buffer = io.StringIO()
    real_stdout = sys.stdout
    sys.stdout = buffer
    try:
        exit_code = module.main(argv)
    finally:
        sys.stdout = real_stdout
    assert exit_code == 0
    payload = json.loads(buffer.getvalue())
    assert payload["budget_s"] > 0

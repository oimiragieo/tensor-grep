"""The enterprise file-size budget gate, and proof that it can actually fail.

`scripts/file_size_budget.py` enforces the CEO's 2026-08-19 size standard via a
grandfathered ratchet. This suite is deliberately weighted toward MUTATION
CONTROLS rather than toward the happy path, because the happy path is the part
that cannot tell you anything: a gate that has only ever been observed passing is
indistinguishable from a gate that cannot fail.

Each control injects one specific defect the gate claims to catch, asserts the
gate names it, and asserts the clean baseline stays green -- so a future edit
that quietly defangs one rule turns exactly one test red instead of sailing
through on an all-green suite.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
_MODULE_PATH = REPO_ROOT / "scripts" / "file_size_budget.py"

_spec = importlib.util.spec_from_file_location("file_size_budget", _MODULE_PATH)
assert _spec is not None and _spec.loader is not None
budget = importlib.util.module_from_spec(_spec)
sys.modules["file_size_budget"] = budget
_spec.loader.exec_module(budget)


def _record(path: str, category: str, lines: int):
    return budget.FileRecord(path=path, category=category, lines=lines)


# --------------------------------------------------------------------------
# Positive controls: prove the instrument can see anything at all.
# --------------------------------------------------------------------------


def test_census_sees_a_substantial_population() -> None:
    """An empty census would make every downstream 'no violations' claim vacuous.

    This is the bidirectional-oracle floor: the gate reporting zero regressions
    means nothing unless we independently know it scanned a real corpus.
    """
    records = budget.census()
    assert len(records) > 500, (
        f"census scanned only {len(records)} files; expected the full tracked "
        "source tree. A shrunken population silently weakens every rule below."
    )
    categories = {r.category for r in records}
    assert {"core", "test"} <= categories, f"missing categories, saw {categories}"


def test_census_counts_only_git_tracked_files() -> None:
    """Untracked scratch and the repo's 54 worktrees must not pollute the census."""
    tracked = set(
        subprocess.run(
            ["git", "ls-files"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.splitlines()
    )
    for record in budget.census():
        assert record.path in tracked, f"{record.path} is not git-tracked"


def test_known_giant_is_measured_and_classified() -> None:
    """A named, independently-verified file anchors the measurement.

    If this drifts, either the file was split (good -- update the assertion and
    retire its allowlist entry) or the counter broke (bad).
    """
    records = {r.path: r for r in budget.census()}
    repo_map = records.get("src/tensor_grep/cli/repo_map.py")
    assert repo_map is not None, "repo_map.py vanished from the census"
    assert repo_map.category == "core"
    assert repo_map.limit == budget.CORE_LIMIT
    assert repo_map.lines > 1500, "repo_map.py is expected to be a known violation"


# --------------------------------------------------------------------------
# Classification.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("src/tensor_grep/core/result.py", "contract"),
        ("src/tensor_grep/cli/rg_contract.py", "contract"),
        ("tests/schemas/tg_output.schema.json", "contract"),
        ("tests/fixtures/audits/codemap_pre_859.py", "fixture"),
        ("tests/conftest.py", "fixture"),
        ("tests/unit/test_cli_modes.py", "test"),
        ("rust_core/tests/test_routing.rs", "test"),
        ("src/tensor_grep/cli/main.py", "core"),
        ("rust_core/src/main.rs", "core"),
        ("scripts/validate_release_assets.py", "core"),
        ("benchmarks/run_gpu_benchmarks.py", "core"),
        ("docs/architecture.md", None),
        ("README.md", None),
    ],
)
def test_classification(path: str, expected: str | None) -> None:
    assert budget.classify(path) == expected


def test_fixture_beats_test_and_contract_beats_core() -> None:
    """Precedence is load-bearing: a mis-ordered rule silently re-tiers files.

    A contract misfiled as core gets a 3x looser limit; a fixture misfiled as
    test happens to match today but would diverge the moment the limits differ.
    """
    assert budget.classify("tests/fixtures/big.py") == "fixture"
    assert budget.classify("src/tensor_grep/core/result.py") == "contract"


# --------------------------------------------------------------------------
# MUTATION CONTROLS -- each proves one rule can fire.
# --------------------------------------------------------------------------


def test_control_new_unpinned_violation_fails() -> None:
    """Rule 1: a brand-new oversized file is rejected."""
    records = [_record("src/tensor_grep/cli/brand_new.py", "core", 1501)]
    failures = budget.evaluate(records, allowlist={})
    assert len(failures) == 1
    assert "NEW VIOLATION" in failures[0]
    assert "brand_new.py" in failures[0]


def test_control_one_line_under_the_limit_passes() -> None:
    """The boundary is `>`, not `>=`. Exactly at the limit is compliant."""
    assert budget.evaluate([_record("src/a.py", "core", 1500)], allowlist={}) == []
    assert len(budget.evaluate([_record("src/a.py", "core", 1501)], allowlist={})) == 1


def test_control_allowlisted_file_growing_by_one_line_fails() -> None:
    """Rule 2: the ratchet. This is the rule that makes it a ratchet at all."""
    records = [_record("src/tensor_grep/cli/repo_map.py", "core", 19709)]
    failures = budget.evaluate(records, {"src/tensor_grep/cli/repo_map.py": 19708})
    assert len(failures) == 1
    assert "RATCHET REGRESSION" in failures[0]

    # ...and the same file at its pinned baseline is green, so the control
    # discriminates rather than failing for an unrelated reason.
    baseline = [_record("src/tensor_grep/cli/repo_map.py", "core", 19708)]
    assert budget.evaluate(baseline, {"src/tensor_grep/cli/repo_map.py": 19708}) == []


def test_control_allowlisted_file_may_shrink_while_still_over_limit() -> None:
    """A partial split must not be punished -- that would discourage progress."""
    records = [_record("src/tensor_grep/cli/repo_map.py", "core", 9000)]
    assert budget.evaluate(records, {"src/tensor_grep/cli/repo_map.py": 19708}) == []


def test_control_shrunk_below_limit_must_retire_its_exception() -> None:
    """Rule 3: an exception may not outlive the violation it documents.

    Without this, the allowlist becomes a permanent dumping ground and the gate
    degrades into a comment.
    """
    records = [_record("src/tensor_grep/cli/repo_map.py", "core", 400)]
    failures = budget.evaluate(records, {"src/tensor_grep/cli/repo_map.py": 19708})
    assert len(failures) == 1
    assert "RETIRE EXCEPTION" in failures[0]


def test_control_deleted_file_leaves_a_stale_exception() -> None:
    """Rule 4: allowlisting a path that no longer exists is a governance failure."""
    failures = budget.evaluate([], {"src/tensor_grep/cli/deleted.py": 5000})
    assert len(failures) == 1
    assert "STALE EXCEPTION" in failures[0]


def test_control_multiple_independent_defects_are_all_reported() -> None:
    """The gate must not stop at the first failure and hide the rest."""
    records = [
        _record("src/new.py", "core", 1600),
        _record("src/grown.py", "core", 2000),
    ]
    failures = budget.evaluate(records, {"src/grown.py": 1900, "src/gone.py": 1700})
    assert len(failures) == 3
    joined = "\n".join(failures)
    assert "NEW VIOLATION" in joined
    assert "RATCHET REGRESSION" in joined
    assert "STALE EXCEPTION" in joined


# --------------------------------------------------------------------------
# The live gate.
# --------------------------------------------------------------------------


def test_live_repository_holds_the_ratchet() -> None:
    """The real gate over the real tree. Must be green; failures name the file."""
    failures = budget.evaluate(budget.census(), budget.load_allowlist())
    assert failures == [], "file-size ratchet broken:\n" + "\n".join(failures)


def test_allowlist_is_exactly_the_live_violation_set() -> None:
    """Belt-and-braces: the two sets must agree in BOTH directions.

    Checked independently of `evaluate` so a bug in `evaluate` cannot hide a
    drifted allowlist -- two methods that share an assumption are one method.
    """
    live = set(budget.violations(budget.census()))
    pinned = set(budget.load_allowlist())
    assert live == pinned, (
        f"unpinned violations: {sorted(live - pinned)}\nstale exceptions: {sorted(pinned - live)}"
    )


def test_allowlist_entries_match_measured_line_counts() -> None:
    """A pinned number that drifts below the real count silently loosens the gate."""
    records = {r.path: r for r in budget.census()}
    for path, pinned in budget.load_allowlist().items():
        assert path in records, f"{path} allowlisted but absent from census"
        assert records[path].lines <= pinned, (
            f"{path} measures {records[path].lines} but is pinned at {pinned}"
        )


def test_cli_surfaces_the_verdict_through_its_exit_code() -> None:
    """End-to-end: the process exit code, not just the library return value.

    CI branches on the exit code; a function returning failures that main()
    forgets to surface would be a false green at the only layer that matters.
    """
    result = subprocess.run(
        [sys.executable, str(_MODULE_PATH), "--report"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "file-size budget OK" in result.stdout

"""Agent-accuracy eval gate: a measurable, deterministic golden-set check that ``tg prepare``
resolves the CORRECT ``primary_target`` file for a set of unambiguous (task -> expected file)
pairs on tensor-grep's OWN repo.

Why this exists: "Agent accuracy gate: Missing" was flagged as a world-class gap -- there was no
automated way to notice a capsule-ranking regression (a change to ``agent_capsule.py`` /
``repo_map.py`` that silently starts pointing agents at the wrong file) short of a human noticing
a bad ``tg prepare`` call in the wild. This is that gate.

Design notes (each choice was verified empirically against the real binary, not assumed):

- Real subprocess (``python -m tensor_grep``), never ``CliRunner``, per AGENTS.md's "Dogfood the
  Real Binary, Not CliRunner" rule and the anti-hang-test-protocol skill -- a subprocess
  ``timeout=`` is a genuine OS-level kill if ``--deadline`` ever regresses back to unbounded,
  mirroring ``tests/integration/test_prepare_oneshot_cuj.py``'s harness shape.
- Scoped to ``src/tensor_grep`` (not the whole repo). This repo dogfoods itself, so its own
  ``.claude/skills/``, ``tests/``, and ``docs/`` trees are full of prose that mentions the same
  symbol/domain words as the golden set; scanning the whole repo reintroduces exactly the
  vendor/skill-tree ranking noise ``tg agent --ignore`` exists to route around. Scoping the scan
  path to the source package removes that ambiguity at the source instead of fighting it with
  ``--ignore`` globs, and is also faster (a real 60s-class deadline never gets close to tripping).
- A GENEROUS fixed ``--deadline`` (60s -- ``tg prepare``'s own documented cold-path default, see
  ``DEFAULT_AGENT_CLI_DEADLINE_SECONDS`` in ``agent_capsule.py``) so a normal run always completes
  without truncation; the slowest of the golden-set tasks measured ~14s against this same repo
  during development, so 60s leaves wide margin for a slower CI runner. Truncation flakiness
  would be a correctness bug in THIS test, not a signal about capsule accuracy, so a truncated
  task fails loudly (see ``_run_tg_prepare``) instead of being silently scored.
- HIT = the expected file is ``primary_target.file`` OR appears anywhere in
  ``alternative_targets`` (which ``agent_capsule.py``'s own ``_alternative_targets(..., limit=
  None)[:4]`` caps at <=4 entries -- there is no arbitrary extra "top-N" window here, this is
  simply "everything the capsule already surfaced"). A real engineer would accept a correct
  top-of-list alternative as "found it", and this absorbs benign tie-break churn from an unrelated
  ranking tweak without weakening the floor.
- PER-TASK PINNING, not a floor (task #252, re-measured 2026-07-21). Every golden task is asserted
  individually in ``test_agent_accuracy_gate`` -- a single task losing its ``primary_target`` (and
  every ``alternative_targets`` entry) is a real capsule-ranking regression and REDS the gate
  immediately. This replaces an earlier floor design (a removed ``_ACCURACY_FLOOR_HITS`` constant,
  "N of 16 must hit") whose 3-task slack was sized against an UNTESTED hypothesis -- "a different
  OS's directory-walk order ... does not red the gate" -- never actually measured cross-OS (the
  ``eval`` marker is excluded from every CI OS's ``test-python`` job, so no automated run ever
  exercised this claim). The floor's real consequence: a 1-3-task regression, including the exact
  single-task #250-class regression this gate exists to catch, passed SILENTLY. Re-measured across
  5 independent same-repo runs (2 by the #693 author across two commits, 3 fresh runs during #252)
  -- all 16 tasks resolved via ``primary_target`` alone, byte-identical every time; zero
  ``alternative_targets`` fallback use, zero flips. The walk-order-variance concern also does not
  hold up against ``repo_map.py``'s own ranking code: every ranking-relevant sort
  (``_symbol_rank_key``, the ``_repo_walk_*_sort_key`` family, file-score tie-breaks) keys on the
  symbol/file NAME string, never on raw ``os.scandir`` order, so the underlying filesystem's native
  enumeration order cannot change the final ranked order -- only an actual ranking-logic change
  can. No task in this set is known to be platform-variant, so none carries a widened allowlist; if
  a future re-baseline ever finds a genuinely platform-variant task, widen THAT task's own
  ``expected_files`` with a cited repro instead of reintroducing a blanket floor.

This is a MEASUREMENT gate, not a brittle exact-match gate: on a failure, read the printed
per-task table (``pytest -s``) to see exactly which task(s) regressed -- every task is pinned
individually, so any single miss is real signal, never noise to be absorbed.

Marked ``eval`` + ``slow`` (opt-in / isolable from the flaky-sensitive main suite -- see
``pyproject.toml``'s ``markers`` and the CI workflow's ``-m "not eval"`` exclusion on the main
``test-python`` job): this suite makes one real subprocess call per ``GOLDEN_SET`` entry against
the live ranking pipeline, which is exactly the kind of test whose failure should never silently
mask unrelated unit-test failures via ``pytest``'s repo-wide ``-x`` fail-fast. Run explicitly with
``pytest tests/eval -m eval -v -s``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

pytestmark = [pytest.mark.eval, pytest.mark.slow]

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC_TENSOR_GREP = _REPO_ROOT / "src" / "tensor_grep"

# Generous on purpose: this is tg prepare's own documented cold-path default (see
# DEFAULT_AGENT_CLI_DEADLINE_SECONDS in agent_capsule.py), passed explicitly so this test's
# behavior does not silently change if that default is ever tuned. Measured wall time for every
# golden-set task against this same src/tensor_grep subtree was 10-14s during development.
_TG_PREPARE_DEADLINE_S = 60.0
# Anti-hang (anti-hang-test-protocol skill): the OS-level subprocess kill, well above the deadline
# bound so a real timeout always reads as "tg prepare honored --deadline and returned" rather than
# "pytest's own subprocess.run timed out first" -- and comfortably below "this hangs pytest".
_SUBPROCESS_TIMEOUT_S = 150.0

# Task #252 (2026-07-21): the floor is GONE -- every task below is pinned individually in
# ``test_agent_accuracy_gate`` (see the module docstring's "PER-TASK PINNING" note for the full
# rationale). History for context: 15/15 (initial golden set, v1.91.0-era capsule ranking) -> 16/16
# (task #250 added "fix the ledger claim TTL logic" back after fixing the thin-CLI-dispatcher
# ranking bug it had originally exposed) -- both measured on Windows during #693's development,
# plus 3 additional fresh Windows runs during #252, all 16/16 byte-identical (same primary_target
# file per task, zero alternative-window fallback use). There is no floor constant to update on a
# re-baseline; if a task is ever found to be genuinely platform-variant, widen that task's own
# ``expected_files`` with a cited repro (see the module docstring) rather than reintroducing a
# blanket floor.

# Each entry: a task phrased the way an engineer would file it, and the small set of files a
# competent engineer would consider correct. Every (task, file) pair below was verified two ways
# before being committed to this set: (1) reading the real source to confirm the file is actually
# the right target for the task (see the `notes`), and (2) running the real `tg prepare` binary
# against this repo to confirm it resolves there today (see the module docstring's baseline).
# `expected_files` is intentionally a small set (1-2 entries) -- never a large fuzzy net -- so a
# hit is still a meaningful precision signal, not a rubber stamp.
GOLDEN_SET: list[dict[str, Any]] = [
    {
        "task": "harden the ReDoS gate in the CPU backend",
        "expected_files": ["backends/cpu_backend.py"],
        "notes": (
            "cpu_backend.py's ReDoS-protection comment block routes complex Python-regex "
            "requests to the native Rust regex crate specifically to avoid catastrophic "
            "backtracking; this is the single file that owns that gate."
        ),
    },
    {
        "task": "add a new governing-doc exemption to docs coverage checking",
        "expected_files": ["cli/docs_coverage.py"],
        "notes": (
            "docs_coverage.py owns _is_governing_doc/_matches_ignore/build_docs_coverage -- the "
            "whole `tg docs-coverage` implementation lives in this one file."
        ),
    },
    {
        "task": "bump the MCP server tool contract version",
        "expected_files": ["cli/mcp_server.py"],
        "notes": (
            "mcp_server.py defines _TG_MCP_SERVER_CONTRACT_VERSION and every tool envelope reads "
            "it via _envelope_base; AGENTS.md's registration-sites section names this exact "
            "constant as the one to bump when a tool's request/response shape changes."
        ),
    },
    {
        "task": "change the session daemon idle shutdown timeout",
        "expected_files": ["cli/session_daemon.py"],
        "notes": (
            "session_daemon.py defines _DAEMON_IDLE_SHUTDOWN_SECONDS_ENV and the idle-shutdown "
            "bound (audit I7); no other file owns daemon lifetime."
        ),
    },
    {
        "task": "handle a corrupted checkpoint file gracefully",
        "expected_files": ["cli/checkpoint_store.py"],
        "notes": "checkpoint_store.py defines CheckpointCorruptError and the checkpoint I/O path.",
    },
    {
        "task": "validate that a scan ruleset policy allows a given apply command",
        "expected_files": ["cli/apply_policy.py"],
        "notes": (
            "apply_policy.py defines RulesetScanPolicy/ApplyPolicy and "
            "PolicyCommandsNotAllowedError -- the whole apply-policy validation surface."
        ),
    },
    {
        "task": "parse git porcelain status for the evidence receipt repo revision",
        "expected_files": ["cli/evidence_receipt.py"],
        "notes": (
            "evidence_receipt.py defines _parse_porcelain_z and _repo_revision_identity; "
            "deliberately paired with the Ed25519-signing task below (evidence_signing.py) "
            "because the two filenames are easy to confuse -- both resolve correctly today, "
            "which is itself a useful regression indicator if that ever stops being true."
        ),
    },
    {
        "task": "resolve the trusted Ed25519 public keys used to verify signed evidence",
        "expected_files": ["cli/evidence_signing.py"],
        "notes": "evidence_signing.py defines resolve_trusted_public_keys and the Ed25519 signing helpers.",
    },
    {
        "task": "the ripgrep subprocess timeout configuration",
        "expected_files": ["cli/subprocess_policy.py"],
        "notes": (
            "subprocess_policy.py defines configured_ripgrep_timeout_seconds/run_subprocess -- "
            "the one file owning subprocess timeout policy for every backend that shells out."
        ),
    },
    {
        "task": "prevent symlink-follow disclosure in the directory scanner",
        "expected_files": ["io/directory_scanner.py"],
        "notes": (
            "directory_scanner.py's DirectoryScanner is the walker AGENTS.md's security-hardening "
            "section names for symlink-follow disclosure (no followlinks)."
        ),
    },
    {
        "task": "extend the Bm25Index class to support per-field score boosting",
        "expected_files": ["core/retrieval_bm25.py"],
        "notes": "retrieval_bm25.py is the sole definition site of the Bm25Index class.",
    },
    {
        "task": "fix a weighting bug in reciprocal_rank_fusion between BM25 and dense scores",
        "expected_files": ["core/retrieval_fusion.py"],
        "notes": "retrieval_fusion.py is the sole definition site of reciprocal_rank_fusion.",
    },
    {
        "task": "speed up the maxsim_scores late-interaction rerank computation",
        "expected_files": ["core/retrieval_late.py"],
        "notes": "retrieval_late.py defines maxsim_scores/rank_by_maxsim/LateReranker.",
    },
    {
        "task": "the RipgrepBackend class that shells out to rg and decodes its JSON fields",
        "expected_files": ["backends/ripgrep_backend.py"],
        "notes": "ripgrep_backend.py is the sole definition site of the RipgrepBackend class.",
    },
    {
        "task": "add a new --flag to tg search",
        "expected_files": ["cli/bootstrap.py", "cli/main.py"],
        "notes": (
            "Deliberately a 2-file legitimate case, not a cop-out: AGENTS.md's 'Adding a Command "
            "or Flag' section is explicit that a new search flag needs BOTH front doors -- "
            "bootstrap._TG_ONLY_SEARCH_FLAGS (bootstrap.py) so the Python front door does not "
            "forward it to ripgrep, AND the flag's actual typer.Option lives on the `search` "
            "command in main.py. A competent engineer touches both; this task exercises that this "
            "eval's scoring correctly treats either as a hit rather than forcing a single answer."
        ),
    },
    {
        "task": "fix the ledger claim TTL logic",
        "expected_files": ["cli/ledger_store.py"],
        "notes": (
            "task #250: this task was DROPPED from the original golden set because it exposed a "
            "real ranking bug -- `ledger_claim`, the thin `@ledger_app.command('claim')` Typer "
            "dispatcher in cli/main.py, exact-lexically-matched both substantive query words "
            "('ledger' + 'claim') at once and outranked the real implementation. ledger_store.py "
            "owns the actual TTL logic (_DEFAULT_TTL_SECONDS/_TTL_ENV/_configured_ttl_seconds) "
            "and the ClaimRecord.ttl_seconds field; ledger_claim's body is a single call-through "
            "to ledger_store.submit_claim. Fixed by down-weighting a provable thin CLI-dispatcher "
            "call-through against the implementation module it calls "
            "(agent_capsule._prefer_implementation_over_cli_dispatcher_helper)."
        ),
    },
]


def _run_tg_prepare(task: str) -> subprocess.CompletedProcess[str]:
    """Real-binary subprocess call, mirroring test_prepare_oneshot_cuj.py's ``_run_tg`` shape.

    Relies on ``tests/conftest.py`` (a parent conftest, always loaded) having already put this
    worktree's ``src/`` on ``PYTHONPATH`` -- see that file's module-level ``os.environ["PYTHONPATH"]``
    setup, which every subprocess in this suite inherits via ``os.environ.copy()``.
    """
    env = os.environ.copy()
    # Never let a real subprocess run autostart a background session-daemon tied to this repo's
    # own working directory (mirrors test_prepare_oneshot_cuj.py / test_agent_cold_deadline_tail_
    # sla_220.py's own rationale).
    env["TG_SESSION_DAEMON_AUTOSTART"] = "0"
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "tensor_grep",
            "prepare",
            str(_SRC_TENSOR_GREP),
            task,
            "--deadline",
            str(_TG_PREPARE_DEADLINE_S),
            "--json",
        ],
        cwd=_SRC_TENSOR_GREP,
        env=env,
        capture_output=True,
        text=True,
        timeout=_SUBPROCESS_TIMEOUT_S,
    )


def _file_matches(returned_file: object, expected_rel: str) -> bool:
    """True when ``returned_file`` (an absolute path string from the capsule JSON) resolves to
    the same file as ``expected_rel`` (a path relative to ``src/tensor_grep``). Resolving both
    sides absorbs absolute-vs-relative and path-separator differences across OSes."""
    if not isinstance(returned_file, str) or not returned_file:
        return False
    try:
        return Path(returned_file).resolve() == (_SRC_TENSOR_GREP / expected_rel).resolve()
    except OSError:
        return False


def _score_task(payload: dict[str, Any], expected_files: list[str]) -> tuple[bool, str]:
    """HIT if any expected file is the primary_target, else if any expected file appears in
    alternative_targets (already capped at <=4 by agent_capsule.py). Returns (hit, detail) where
    detail is a human-readable line for the per-task report."""
    primary = payload.get("primary_target")
    primary_file = primary.get("file") if isinstance(primary, dict) else None
    for expected in expected_files:
        if _file_matches(primary_file, expected):
            return True, f"primary_target matched {expected!r}"

    alternatives = payload.get("alternative_targets")
    alt_files = (
        [alt.get("file") for alt in alternatives if isinstance(alt, dict)]
        if isinstance(alternatives, list)
        else []
    )
    for expected in expected_files:
        for alt_file in alt_files:
            if _file_matches(alt_file, expected):
                return (
                    True,
                    f"alternative_targets matched {expected!r} (primary was {primary_file!r})",
                )

    return False, f"MISS -- primary={primary_file!r} alternatives={alt_files!r}"


@pytest.fixture(scope="module")
def golden_set_results() -> list[dict[str, Any]]:
    """Run every golden-set task through the real `tg prepare` binary exactly once (module-scoped
    so both tests below share the same ``len(GOLDEN_SET)`` subprocess calls instead of doubling the
    run)."""
    results: list[dict[str, Any]] = []
    for item in GOLDEN_SET:
        task = item["task"]
        result = _run_tg_prepare(task)
        # A genuine crash (not a truncation, which is exit 2 and still prints full honest JSON
        # per the output-before-exit contract) is a test-infrastructure failure, not a ranking
        # miss -- fail loudly here rather than let a JSON-decode error read as a silent 0-score.
        assert result.returncode in (0, 2), (
            f"tg prepare crashed for task {task!r} (exit {result.returncode}):\n"
            f"{result.stdout}\n{result.stderr}"
        )
        payload = json.loads(result.stdout)
        hit, detail = _score_task(payload, item["expected_files"])
        if payload.get("partial"):
            detail += " [PARTIAL -- deadline truncated despite the generous bound, investigate]"
        results.append({"task": task, "hit": hit, "detail": detail})
    return results


def test_golden_set_targets_exist() -> None:
    """Fast, subprocess-free sanity check: every golden-set expected file must still exist on
    disk. Fails fast and clearly (rather than as a confusing ranking MISS) if a golden-set target
    is ever renamed or moved -- maintaining this list is an expected cost of a self-referential
    eval, not a bug in the eval itself."""
    missing = [
        (item["task"], expected)
        for item in GOLDEN_SET
        for expected in item["expected_files"]
        if not (_SRC_TENSOR_GREP / expected).is_file()
    ]
    assert not missing, f"golden-set target file(s) no longer exist, update GOLDEN_SET: {missing}"


def test_agent_accuracy_gate(golden_set_results: list[dict[str, Any]]) -> None:
    """The gate: every golden task is pinned INDIVIDUALLY (task #252) -- a MISS on any single task
    fails this test, no floor slack. Always prints the full per-task report first (pytest -s) so a
    failure is immediately diagnosable without a re-run, exactly like the prior floor-based design;
    only the pass/fail rule changed, from "hits >= floor" to "zero misses"."""
    total = len(golden_set_results)
    hits = sum(1 for result in golden_set_results if result["hit"])
    report_lines = [
        f"  [{'HIT ' if result['hit'] else 'MISS'}] {result['task']!r}: {result['detail']}"
        for result in golden_set_results
    ]
    report = "\n".join(report_lines)
    print(
        f"\nAgent-accuracy golden set: {hits}/{total} (per-task pinned -- every task must hit)\n"
        f"{report}"
    )

    misses = [result for result in golden_set_results if not result["hit"]]
    assert not misses, (
        f"agent-accuracy gate: {len(misses)}/{total} golden task(s) MISSED (per-task pinning -- "
        "every task must hit, no floor slack) -- failing task(s):\n"
        + "\n".join(f"  {m['task']!r}: {m['detail']}" for m in misses)
        + f"\n\nfull per-task detail:\n{report}"
    )

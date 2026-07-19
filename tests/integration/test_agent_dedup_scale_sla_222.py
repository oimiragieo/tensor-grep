"""Real-binary, real-wall-clock proof of the #222 fix (real-workspace-scale residual of
#220/#669): `tg agent <root> <query> --deadline N` stays bounded even on a fixture shaped so
`_detect_vendored_subtrees`'s outermost-nested-chain dedup loop cannot cheaply short-circuit.

Why this is a DIFFERENT fixture shape than `test_agent_cold_deadline_tail_sla_220.py`'s
`manifest_heavy_repo`: that fixture's `_NESTED_PACKAGES_PER_PROJECT` packages nest UNDER each
project's own top-level manifest, so the (depth-sorted) dedup loop accepts the shallow project
root FIRST and every nested package short-circuits against it almost immediately -- it never
grows `subtree_rel_roots` large enough to expose the #222 cost (each individual outer iteration's
own comparison cost, which the OLD code paid via TWO real `Path.resolve()` filesystem syscalls
per candidate pair). `independent_package_repo` below deliberately gives every candidate package
NO common absorbing ancestor (wrapped in a plain `deps/` dir that is itself neither a STRONG-0
vendor name nor manifest-bearing), so the dedup loop must pairwise-compare against the full,
ever-growing accepted list -- exactly the shape the real ~50k-file/40-sibling-project workspace
that motivated #222 has (many independent, non-nested manifest-bearing directories: a monorepo
`packages/*/package.json` shape, or -- as here -- a vendored dependency tree).

Pre-fix magnitude on this exact shape (measured via a direct, single-call, non-subprocess probe
against origin/main before this fix, at this fixture's exact candidate count): a SINGLE unbounded
`_detect_vendored_subtrees` call took **102.6s** at ~500 independent candidates (120s at 200,
40.9s at 304 -- the super-linear/near-quadratic curve is documented in
`tests/unit/test_agent_vendored_subtree_dedup_scale_222.py`'s module docstring) -- and `tg agent`
calls it TWICE per invocation (call-1 in agent_capsule, call-2 inside repo_map's own
`auto_deweight` pass), so the pre-fix real-binary wall-to-exit at this fixture's scale was
confirmed to blow both this module's deadline*2 bound AND the 90s subprocess timeout outright
(verified directly against a pre-fix source copy before this test was finalized -- deliberately
NOT re-run as part of the normal suite, since doing so would mean an intentionally-hanging test).
Post-fix (this module's actual, currently-passing expectation): the SAME ~500-candidate call
dropped to **0.71s** (a ~144x speedup), so the real-binary wall-to-exit now stays close to the
requested --deadline at every tested budget, exactly like #220's own SLA test expects of ITS
fixture.

Real subprocess (`python -m tensor_grep`), not CliRunner, per AGENTS.md's "dogfood the real
binary" rule and the anti-hang-test-protocol skill: a subprocess `timeout=` is a genuine OS-level
kill if this regresses back to unbounded, whereas an in-process CliRunner hang would hang the
whole pytest run (and every other queued test) with it. OUTCOME-AGNOSTIC on returncode (the #669
CI lesson, run 29671609290): a fast CI runner may legitimately finish complete inside a given
--deadline; the property under test is "wall-to-exit is bounded," not "the deadline must trip."
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
# timeout is the inner, OS-level kill -- a regression back to fully-unbounded dedup-loop behavior
# fails FAST here (TimeoutExpired) instead of hanging the whole pytest run. Callers running this
# file in CI should also wrap the invocation in an outer shell-level `timeout` per that skill.
_SUBPROCESS_TIMEOUT_S = 90.0

# Independent (non-absorbed) candidate count -- see the module docstring for why this shape (not
# #220's project-nested one) is required to exercise the #222 dedup-loop cost. Deliberately large
# enough (~500) that the PRE-FIX code measurably fails this module's assertions (102.6s per call,
# see module docstring) while the POST-FIX code stays fast (0.71s per call) -- a smaller count
# (e.g. 200) was verified NOT to discriminate: the OLD per-iteration deadline check still bounds
# wall-to-exit tightly when the loop is cut off early in its cheap phase, so the bug only shows up
# once the checkpoint granularity itself becomes coarse deep in the O(candidate_roots^2) curve.
_INDEPENDENT_PACKAGE_COUNT = 500


def _run_tg(
    args: list[str], *, cwd: Path, timeout: float = _SUBPROCESS_TIMEOUT_S
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    # Force daemon autostart off (mirrors test_agent_cold_deadline_tail_sla_220.py) -- this
    # module is about the COLD path specifically, and a leaked warm daemon from a prior test
    # could silently reroute a later no-explicit-`--deadline` call onto the warm path instead.
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


@pytest.fixture(scope="module")
def independent_package_repo(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """`_INDEPENDENT_PACKAGE_COUNT` sibling manifest-bearing packages under a plain `deps/`
    wrapper that carries NO manifest of its own and is not a STRONG-0 vendor name -- so none of
    them nest under a common already-accepted dedup-loop ancestor. See the module docstring."""
    root = tmp_path_factory.mktemp("independent_package_repo") / "workspace"
    root.mkdir()
    for i in range(_INDEPENDENT_PACKAGE_COUNT):
        pkg = root / "deps" / f"pkg_{i:05d}"
        pkg.mkdir(parents=True)
        (pkg / "package.json").write_text('{"name": "pkg"}\n', encoding="utf-8")
        (pkg / "index.js").write_text(
            "module.exports = function stub() { return 1; };\n", encoding="utf-8"
        )
    (root / "main.py").write_text("def main():\n    print('entry')\n", encoding="utf-8")
    return root


@pytest.mark.parametrize("deadline", [3.0, 8.0])
def test_agent_dedup_scale_wall_to_exit_bounded(
    independent_package_repo: Path, deadline: float
) -> None:
    """The core #222 SLA: wall-to-exit stays within `deadline * 2` (generous -- catches a
    regression back to "the dedup loop's real-resolve()-per-pair cost is unbounded again", which
    would show as several times the deadline or the subprocess timeout firing outright, not a
    tight deadline-adherence claim) at TWO different deadlines, on a fixture shaped so the
    dedup loop cannot cheaply short-circuit via a common absorbing ancestor.

    OUTCOME-AGNOSTIC by design (mirrors test_agent_cold_deadline_tail_sla_220.py, same CI lesson
    from PR #669 run 29671609290): a fast runner may finish complete well inside the deadline
    (returncode 0); a slower one may genuinely trip it (returncode 2, partial). Both are honest
    and both must satisfy the wall bound."""
    started_at = time.monotonic()
    result = _run_tg(
        [
            "agent",
            str(independent_package_repo),
            "find the main entry points",
            "--deadline",
            str(deadline),
            "--json",
        ],
        cwd=independent_package_repo,
    )
    elapsed = time.monotonic() - started_at

    assert elapsed <= deadline * 2.0, (
        f"tg agent ran {elapsed:.1f}s against a {deadline}s --deadline on a "
        f"{_INDEPENDENT_PACKAGE_COUNT}-independent-candidate dependency tree -- the vendored-"
        "subtree dedup loop looks super-linear again (#222 regression)"
    )
    payload = json.loads(result.stdout)
    if result.returncode == 2:
        # The deadline actually tripped -- honesty must survive the bound: still explicitly
        # flagged, never a silent partial-less exit.
        assert payload.get("partial") is True, result.stdout
        assert payload.get("deadline_limit", {}).get("deadline_exceeded") is True, result.stdout
    else:
        # The whole run completed naturally under the deadline -- also a valid pass.
        assert result.returncode == 0, result.stdout + result.stderr
        assert payload.get("partial") is not True, (
            "a run that beat the deadline should not ALSO claim a deadline cutoff -- "
            f"{result.stdout}"
        )


def test_agent_no_deadline_pressure_is_bounded_and_byte_identical(
    independent_package_repo: Path,
) -> None:
    """Regression guard for the "no-deadline-pressure path unchanged" discipline (#205/#220
    pattern): with an ample --deadline, no assembly stage may be reported skipped, AND the
    RESULT (`suggested_ignore` / `target` / `alternatives`) must be byte-identical to a run with
    NO --deadline flag at all -- proving the #222 dedup-loop rewrite is a pure performance
    change, never a correctness regression. (This fixture's packages are single-file stubs with
    no internal require() edges, so `_detect_vendored_subtrees` legitimately reports NOTHING for
    them -- `deps/` matches no STRONG-0 name and no import-island evidence -- so the property
    under test here is identical-output, not non-empty output; the positive-detection case is
    covered at the function level by
    tests/unit/test_agent_vendored_subtree_dedup_scale_222.py::
    test_dedup_still_subsumes_nested_manifest_under_strong0_root.)"""
    ample = _run_tg(
        [
            "agent",
            str(independent_package_repo),
            "find the main entry points",
            "--deadline",
            "60",
            "--json",
        ],
        cwd=independent_package_repo,
    )
    no_flag = _run_tg(
        ["agent", str(independent_package_repo), "find the main entry points", "--json"],
        cwd=independent_package_repo,
    )
    ample_payload = json.loads(ample.stdout)
    no_flag_payload = json.loads(no_flag.stdout)
    assert ample_payload.get("deadline_limit", {}).get("assembly_stages_skipped") is None, (
        "an ample --deadline must never skip an assembly stage -- got "
        f"{ample_payload.get('deadline_limit')}"
    )
    for key in ("suggested_ignore", "target", "alternatives", "suggested_scope"):
        assert ample_payload.get(key) == no_flag_payload.get(key), (
            f"ample-deadline vs no-deadline-flag diverged on {key!r}: "
            f"{ample_payload.get(key)!r} != {no_flag_payload.get(key)!r}"
        )

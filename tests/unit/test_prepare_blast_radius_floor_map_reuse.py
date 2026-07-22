"""opt10 campaign ranked-queue item #2: `tg prepare` used to build the repo map TWICE for the
common natural-language-query CUJ -- once in `build_agent_capsule` (via `_build_prepare_payload`)
and again inside `_build_prepare_blast_radius_floor`'s supplementary scan, which always called the
FS-backed `build_symbol_blast_radius` (a fresh `build_repo_map` walk+parse) even though an
equivalent map had just been built moments earlier.

The fix threads the already-built `rm` through `_build_prepare_payload` into
`_build_prepare_blast_radius_floor`, which now reuses it via the map-reusing sibling
`build_symbol_blast_radius_from_map` -- UNLESS `rm['scan_limit']['possibly_truncated']` is True
(a repo bigger than `DEFAULT_AGENT_REPO_MAP_LIMIT`, 2000 files), in which case the exact pre-fix
uncapped FS-backed rescan is preserved so blast-radius recall never silently narrows on a
large repo (the load-bearing guard the ranked-queue item calls out explicitly).

Four tests, cheapest/most-precise first:
  1/2. Gate unit tests directly on `_build_prepare_blast_radius_floor` (monkeypatched repo_map
       functions, synthetic `rm` dicts) -- fast, deterministic proof of the ROUTING decision
       itself: not-truncated routes to `build_symbol_blast_radius_from_map` and NEVER touches the
       FS-backed function; possibly_truncated routes to the FS-backed function and NEVER touches
       `_from_map`.
  3. Small real tree via CliRunner + a `build_repo_map` call counter: `tg prepare` with a
     natural-language query now builds the repo map exactly ONCE, and the resulting
     `blast_radius_floor`'s caller member set is byte-identical to calling the pre-fix FS-backed
     `build_symbol_blast_radius` directly against the same fixture/symbol (recall-identical, not
     just faster).
  4. Real >2000-file tree, `_build_prepare_blast_radius_floor` called directly against a REAL,
     genuinely-truncated `rm` (real `build_repo_map` scan, not a synthetic dict): proves the
     load-bearing guard on real data -- a caller deliberately placed outside the 2000-file-capped
     scan window is still found, because `possibly_truncated=True` correctly routes to the
     preserved uncapped rescan. Bypasses `tg prepare`'s own capsule-ranking/validation-detection
     machinery (unrelated to this item, and expensive enough at 2000+ files on a shared box to
     make a full CliRunner run of it flaky) -- see that test's own docstring for the measurement.
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

import tensor_grep.cli.repo_map as repo_map
from tensor_grep.cli.main import _build_prepare_blast_radius_floor, app


def _skipped_not_requested_evidence() -> dict[str, object]:
    """The `call_site_evidence` shape `_collect_capsule_call_site_evidence` returns for a
    natural-language query that never names the primary symbol (agent_capsule.py:737-741) --
    the exact precondition under which `_build_prepare_blast_radius_floor` reaches the
    reuse-vs-fallback branch this test file targets."""
    return {
        "status": "skipped",
        "reason": "primary symbol was not explicitly requested by query",
    }


def test_prepare_floor_reuses_map_when_not_truncated(monkeypatch) -> None:
    """Gate unit test, positive half: rm['scan_limit']['possibly_truncated'] is False -> the
    floor MUST route through build_symbol_blast_radius_from_map and MUST NOT call the FS-backed
    build_symbol_blast_radius at all (that would be the pre-#2 double-scan this item removes)."""
    from_map_calls: list[tuple[object, ...]] = []
    fs_backed_calls: list[tuple[object, ...]] = []

    def _fake_from_map(rm, symbol, **kwargs):
        from_map_calls.append((rm, symbol, kwargs))
        return {
            "no_match": False,
            "callers": [
                {"file": "in_window.py", "line": 3, "symbol": symbol, "provenance": "python-ast"}
            ],
            "output_limit": {"omitted_callers": 0},
            "graph_trust_summary": {},
            "resolution_gaps": [],
            "partial": False,
        }

    def _fake_fs_backed(symbol, path, **kwargs):
        fs_backed_calls.append((symbol, path, kwargs))
        raise AssertionError(
            "build_symbol_blast_radius (FS-backed, uncapped rescan) must NOT run when rm is "
            "not possibly_truncated -- this is the #2 regression this test guards against"
        )

    monkeypatch.setattr(repo_map, "build_symbol_blast_radius_from_map", _fake_from_map)
    monkeypatch.setattr(repo_map, "build_symbol_blast_radius", _fake_fs_backed)

    not_truncated_rm = {
        "path": "/repo",
        "scan_limit": {
            "max_repo_files": 2000,
            "scanned_files": 4,
            "possibly_truncated": False,
            "truncation_cause": None,
        },
    }
    floor, deadline_partial = _build_prepare_blast_radius_floor(
        path="/repo",
        rm=not_truncated_rm,
        target={"symbol": "my_symbol"},
        call_site_evidence=_skipped_not_requested_evidence(),
        related_call_sites=[],
        deadline_monotonic=None,
    )

    assert len(from_map_calls) == 1, from_map_calls
    assert len(fs_backed_calls) == 0, fs_backed_calls
    assert floor["source"] == "supplementary_blast_radius", floor
    assert floor["callers_count"] == 1, floor
    assert deadline_partial is False


def test_prepare_floor_preserves_fs_rescan_when_map_possibly_truncated(monkeypatch) -> None:
    """Gate unit test, negative half -- THE load-bearing guard: rm['scan_limit']
    ['possibly_truncated'] is True -> the floor MUST preserve the pre-#2 uncapped FS-backed
    build_symbol_blast_radius rescan and MUST NOT reuse the (possibly-missing-files) map via
    build_symbol_blast_radius_from_map. A blind reuse here would silently narrow blast-radius
    recall on a >2000-file repo -- a correctness regression, not just a missed speedup."""
    from_map_calls: list[tuple[object, ...]] = []
    fs_backed_calls: list[tuple[object, ...]] = []

    def _fake_from_map(rm, symbol, **kwargs):
        from_map_calls.append((rm, symbol, kwargs))
        raise AssertionError(
            "build_symbol_blast_radius_from_map must NOT run when rm is possibly_truncated -- "
            "it may be missing the very caller this floor exists to find"
        )

    def _fake_fs_backed(symbol, path, **kwargs):
        fs_backed_calls.append((symbol, path, kwargs))
        assert "max_repo_files" not in kwargs, (
            "the uncapped rescan must never receive a max_repo_files cap (main.py's own "
            "'deliberately OMITTED' comment) -- got kwargs=" + repr(kwargs)
        )
        return {
            "no_match": False,
            "callers": [
                {
                    "file": "beyond_the_cap.py",
                    "line": 7,
                    "symbol": symbol,
                    "provenance": "python-ast",
                }
            ],
            "output_limit": {"omitted_callers": 0},
            "graph_trust_summary": {},
            "resolution_gaps": [],
            "partial": False,
        }

    monkeypatch.setattr(repo_map, "build_symbol_blast_radius_from_map", _fake_from_map)
    monkeypatch.setattr(repo_map, "build_symbol_blast_radius", _fake_fs_backed)

    truncated_rm = {
        "path": "/repo",
        "scan_limit": {
            "max_repo_files": 2000,
            "scanned_files": 2000,
            "possibly_truncated": True,
            "truncation_cause": "project-files",
        },
    }
    floor, deadline_partial = _build_prepare_blast_radius_floor(
        path="/repo",
        rm=truncated_rm,
        target={"symbol": "my_symbol"},
        call_site_evidence=_skipped_not_requested_evidence(),
        related_call_sites=[],
        deadline_monotonic=None,
    )

    assert len(fs_backed_calls) == 1, fs_backed_calls
    assert len(from_map_calls) == 0, from_map_calls
    assert floor["source"] == "supplementary_blast_radius", floor
    assert floor["callers_count"] == 1, floor
    assert deadline_partial is False


# Proven-shape fixture (mirrors tests/integration/test_prepare_oneshot_cuj.py's billing_repo,
# which the maintained test suite already relies on to resolve a symbol-level -- not file-level
# -- primary_target for a natural-language query): a 3-function call chain across 2 files gives
# the ranker enough signal to commit to `process_billing_cycle` specifically, and the query below
# never names it, so `_target_symbol_was_explicitly_requested` fails and this floor's
# reuse-vs-fallback branch is reached.
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
_PREPARE_NL_QUERY = "the billing job should skip accounts that already paid earlier this month"


def _make_small_billing_repo(root: Path) -> None:
    (root / "pyproject.toml").write_text(_PYPROJECT, encoding="utf-8")
    (root / "billing.py").write_text(_BILLING_MODULE, encoding="utf-8")
    (root / "run.py").write_text(_RUN_MODULE, encoding="utf-8")
    tests_dir = root / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_billing.py").write_text(_TEST_MODULE, encoding="utf-8")


def test_prepare_builds_repo_map_once_and_matches_fs_backed_reference(
    tmp_path: Path, monkeypatch
) -> None:
    """(b) TDD: on a small (non-truncated) repo with a natural-language query, `tg prepare` must
    build the repo map EXACTLY ONCE (not twice, the pre-#2 behavior), and the resulting
    blast_radius_floor's caller member set must be byte-identical to calling the pre-#2 FS-backed
    build_symbol_blast_radius directly against the same fixture/symbol -- proving the reuse is
    recall-identical, not just faster."""
    _make_small_billing_repo(tmp_path)
    for symbol_name in ("calculate_late_fee", "apply_late_fee", "process_billing_cycle"):
        assert symbol_name not in _PREPARE_NL_QUERY.split(), _PREPARE_NL_QUERY

    call_count = {"n": 0}
    original_build_repo_map = repo_map.build_repo_map

    def _counting_build_repo_map(*args, **kwargs):
        call_count["n"] += 1
        return original_build_repo_map(*args, **kwargs)

    monkeypatch.setattr(repo_map, "build_repo_map", _counting_build_repo_map)

    result = CliRunner().invoke(app, ["prepare", str(tmp_path), _PREPARE_NL_QUERY, "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)

    primary_target = payload["primary_target"]
    # sanity: the ranker found a SPECIFIC symbol (not a file-level best-effort target) -- this is
    # the precondition for the floor's reuse-vs-fallback branch to run at all.
    assert primary_target.get("symbol"), payload

    floor = payload["blast_radius_floor"]
    assert floor.get("source") == "supplementary_blast_radius", floor
    assert call_count["n"] == 1, (
        f"build_repo_map invoked {call_count['n']} times for tg prepare's natural-language-query "
        "path -- expected exactly 1 (opt10 #2: the blast-radius floor must reuse the capsule's "
        "already-built map instead of rebuilding it)"
    )

    monkeypatch.setattr(repo_map, "build_repo_map", original_build_repo_map)
    reference = repo_map.build_symbol_blast_radius(
        str(primary_target["symbol"]), str(tmp_path), max_depth=1, max_callers=8, max_files=8
    )
    # Compare on FILE alone, not the raw callers[].get("symbol") field -- that field names
    # whatever the caller record's own AST node carried (may be None/the enclosing function),
    # not the target symbol; `_related_call_site_record` (used by both the reuse and fallback
    # branches identically) is what normalizes it to the target symbol for `top_callers`, so
    # comparing raw `callers[]` on that field would not be apples-to-apples.
    reference_members = {str(c.get("file")) for c in reference.get("callers", [])}
    actual_members = {str(c.get("file")) for c in floor.get("top_callers", [])}
    assert actual_members, f"expected at least one real caller in this fixture: {floor}"
    assert actual_members == reference_members, (actual_members, reference_members)
    assert all(c.get("symbol") == primary_target["symbol"] for c in floor.get("top_callers", [])), (
        floor
    )
    assert floor.get("callers_count") == len(reference.get("callers", [])), floor


# >= 2000 same-dir files sort before the caller -> its within-dir index exceeds the max
# round-robin round reachable before the 2000-file cap, so the caller is guaranteed dropped.
_LARGE_TREE_BURY_FILE_COUNT = 2100


def _make_large_truncated_repo(root: Path) -> None:
    """The same proven billing shape as `_make_small_billing_repo`, padded to comfortably exceed
    `DEFAULT_AGENT_REPO_MAP_LIMIT` (2000 files) with `zzzz_run.py` -- the ONE caller of the
    selected primary target `process_billing_cycle` -- deliberately placed OUTSIDE the capped scan
    window, so a naive always-reuse fix would silently report zero callers while the correct
    (guarded) behavior still finds it via the preserved uncapped rescan.

    The definition stays IN-window while the caller is buried OUT-of-window, both by construction:

    - `billing.py` (holding the `process_billing_cycle` definition) and `pyproject.toml` are
      top-level FILES -- bucket-group 2 in `_repo_walk_path_sort_key` -- and `tests/` is
      bucket-group 1 (`_TEST_DIR_NAMES`); all three are scanned before any bucket-group-3
      directory, so the definition is always inside the 2000-file cap regardless of padding size.

    - The caller is buried at a within-directory sorted index >= 2000. The repo-map walk is
      round-robin across directories, drawing AT MOST one file per directory per round, so the
      maximum round reached before the 2000-file cap bites is <= 2000. Any file at within-dir
      index >= 2000 is therefore GUARANTEED dropped -- independent of how many sibling directories
      exist. We put `zzzz_run.py` in a single `zzz_caller/` dir behind `_LARGE_TREE_BURY_FILE_COUNT`
      (>= 2000) `pad#####.py` files that all sort before it (`pad#####` < `zzzz_run`), so the
      caller's within-dir index is exactly `_LARGE_TREE_BURY_FILE_COUNT`.

    This is the gate-#714 fidelity fix over the original 1-file-per-padding-dir layout, whose
    lone `zzz_caller/` file landed in round 0 and SURVIVED the cap -- leaving the caller actually
    in-window, so a naive always-reuse would ALSO have found it and the recall claim went
    untested. `test_prepare_recall_preserved_on_large_truncated_repo` now asserts BOTH halves: a
    naive from-map reuse MISSES the buried caller, and the guarded floor still FINDS it via the
    uncapped rescan (`scan_limit.possibly_truncated=True`)."""
    _make_small_billing_repo(root)
    # billing.py's own pre-existing top-level run.py (always-scanned group 2) would ALSO be a
    # caller of process_billing_cycle and defeat the "outside the window" setup.
    (root / "run.py").unlink()
    # ROBUST out-of-window placement (gate #714 fix): the repo-map walk draws AT MOST one file
    # per directory per round-robin round, so the maximum round reached before the 2000-file cap
    # bites is <= 2000. Therefore ANY file at within-directory sorted index >= 2000 is GUARANTEED
    # dropped, independent of how many sibling directories exist -- unlike a 1-file `zzz_caller/`
    # dir whose sole file lands in round 0 and survives (the original fixture's latent bug: the
    # caller was actually IN-window, so a naive always-reuse would ALSO have found it and the
    # recall claim went untested). Bury the caller behind >=2000 same-directory pad files that
    # sort before it; the definition (`billing.py`, top-level group 2) stays in-window so the
    # symbol still resolves.
    caller_dir = root / "zzz_caller"
    caller_dir.mkdir(parents=True)
    for file_index in range(_LARGE_TREE_BURY_FILE_COUNT):
        (caller_dir / f"pad{file_index:05d}.py").write_text(
            f"def pad_fn_{file_index:05d}():\n    return {file_index}\n",
            encoding="utf-8",
        )
    # `zzzz_run.py` sorts AFTER every `pad#####.py` -> within-dir index == _LARGE_TREE_BURY_FILE_
    # COUNT (>= 2000) -> guaranteed outside the capped window.
    (caller_dir / "zzzz_run.py").write_text(_RUN_MODULE, encoding="utf-8")


def test_prepare_recall_preserved_on_large_truncated_repo(tmp_path: Path, monkeypatch) -> None:
    """(a) TDD -- the load-bearing guard on a REAL >2000-file tree: `rm` is genuinely
    possibly_truncated (a real `build_repo_map` scan, 2000-file cap bites), yet the caller living
    outside that cap window is still found via `_build_prepare_blast_radius_floor`, proving the
    uncapped FS-backed rescan still fires instead of a naive from-map reuse silently
    under-reporting recall.

    Calls `_build_prepare_blast_radius_floor` directly (rather than the full `tg prepare` CLI)
    with a `target`/`call_site_evidence` shape matching what `_build_prepare_payload` would pass
    for this exact fixture/query (empirically confirmed via `test_prepare_builds_repo_map_once_
    and_matches_fs_backed_reference`'s identical shape one tree size down, and via a manual
    `tg prepare` run against this same fixture during opt10 #2 development). This isolates the
    mechanism this ranked-queue item actually changes (the floor's routing decision + the real
    uncapped rescan's recall) from `tg prepare`'s unrelated, pre-existing capsule-ranking +
    validation-runner-detection cost (`_precomputed_validation_files_for_root`, Windows
    `nt.stat`/`_getfinalpathname` traffic that scales with file count and was observed to vary
    ~16s-60s+ run to run on this shared box during opt10 #2 profiling) -- exercising THAT
    unrelated subsystem at 2000+ files would make this a flaky test of code this item does not
    touch, not a reliable correctness check of the guard it adds."""
    _make_large_truncated_repo(tmp_path)
    total_files = 3 + _LARGE_TREE_BURY_FILE_COUNT + 1  # billing/pyproject/test + pads + caller
    assert total_files > 2000, total_files  # sanity: this really is a >2000-file tree

    for symbol_name in ("calculate_late_fee", "apply_late_fee", "process_billing_cycle"):
        assert symbol_name not in _PREPARE_NL_QUERY.split(), _PREPARE_NL_QUERY

    call_count = {"n": 0}
    original_build_repo_map = repo_map.build_repo_map

    def _counting_build_repo_map(*args, **kwargs):
        call_count["n"] += 1
        return original_build_repo_map(*args, **kwargs)

    monkeypatch.setattr(repo_map, "build_repo_map", _counting_build_repo_map)

    rm = repo_map.build_repo_map(
        str(tmp_path), max_repo_files=repo_map.DEFAULT_AGENT_REPO_MAP_LIMIT
    )
    scan_limit = rm.get("scan_limit") or {}
    assert scan_limit.get("possibly_truncated") is True, (
        "test setup sanity check failed -- rm was not actually truncated, so this test cannot "
        f"exercise the load-bearing guard at all: {scan_limit}"
    )

    # Gate #714 CONTRAST -- the load-bearing half of this test's fidelity: prove the fixture
    # genuinely buries `zzzz_run.py` OUTSIDE the 2000-file capped scan window. A naive "always
    # reuse the capped map" fix (calling build_symbol_blast_radius_from_map straight on the
    # truncated `rm`, exactly as the floor's reuse branch does -- main.py, max_depth=1) MUST MISS
    # the caller: that file was dropped from the scan and is therefore absent from the map's
    # caller universe (build_symbol_callers_from_map draws strictly from the map, no fresh FS
    # scan). This is precisely the silent recall regression the possibly_truncated guard exists to
    # prevent; without asserting the naive path misses it, the "guard finds it" check below could
    # pass even if the caller were accidentally in-window (the original fixture's latent bug).
    naive_reuse = repo_map.build_symbol_blast_radius_from_map(
        rm, "process_billing_cycle", max_depth=1
    )
    naive_caller_files = {str(c.get("file")) for c in naive_reuse.get("callers", [])}
    assert not any("run.py" in current for current in naive_caller_files), (
        "fixture no longer buries the caller out-of-window -- a naive from-map reuse already "
        f"finds it, so this test would NOT exercise the guard: {naive_caller_files}"
    )

    floor, deadline_partial = _build_prepare_blast_radius_floor(
        path=str(tmp_path),
        rm=rm,
        target={"symbol": "process_billing_cycle"},
        call_site_evidence=_skipped_not_requested_evidence(),
        related_call_sites=[],
        deadline_monotonic=None,
    )

    assert floor.get("source") == "supplementary_blast_radius", floor
    assert floor.get("callers_count", 0) >= 1, (
        "blast-radius recall regressed on a >2000-file repo -- the caller living outside the "
        f"capped scan window was not found: {floor}"
    )
    caller_files = {str(c.get("file")) for c in floor.get("top_callers", [])}
    assert any("run.py" in current for current in caller_files), floor
    assert deadline_partial is False

    # The guard preserved the uncapped rescan, which is itself a second build_repo_map call (the
    # explicit one above for `rm`, plus one more inside the floor's own fallback) -- confirms the
    # fallback genuinely ran rather than the caller coincidentally already being in the capped
    # window some other way.
    assert call_count["n"] == 2, (
        f"build_repo_map invoked {call_count['n']} times -- expected exactly 2 (the explicit "
        "capped build above + the preserved uncapped fallback rescan) on a possibly_truncated rm"
    )

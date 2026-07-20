"""CEO /goal #4 (P0, "completeness you can trust"): a PERMANENT bidirectional-oracle regression
gate proving the documented three-state exit-code contract (docs/CONTRACTS.md:112-113) actually
holds for `tg importers`, `tg callers`, and `tg blast-radius`:

    0 = complete result (trust it -- every real edge is present)
    1 = genuine not-found on a COMPLETE scan
    2 = INCOMPLETE -- a --deadline cut or a --max-repo-files cap dropped real data; a zero or
        small count is NOT the full answer

Three fixtures, each run against all three commands:

  * Fixture A (KNOWN-COMPLETE): a tiny 3-file repo -- util.py defines `f`; a.py and b.py both
    import util and call `f`. No cap, no deadline. Every real edge (a.py, b.py) must be present
    and the exit code must be 0.
  * Fixture B (KNOWN-TRUNCATED by cap): filler files that sort alphabetically BEFORE the real
    repo (mirrors tests/unit/test_result_incomplete_payload_layer.py:18-24's
    ``_write_filler_files`` idiom) + ``--max-repo-files 1`` so the scan window keeps only a
    filler and drops util.py/a.py/b.py entirely. Exit code must be 2, ``scan_limit.
    possibly_truncated`` must be True, and the full JSON payload must still print (never
    swallowed by the exit).
  * Fixture C (KNOWN-TRUNCATED by deadline): Fixture A's repo, but the ``--deadline`` ->
    absolute-monotonic-deadline conversion (``repo_map._deadline_monotonic_from_seconds``) is
    monkeypatched to always return an ALREADY-EXPIRED value. This is DETERMINISTIC CLOCK
    INJECTION, not a real tight ``--deadline`` -- a real wall-clock race is OS-speed-fragile in CI
    (see tests/integration/test_agent_cold_deadline_tail_sla_220.py's documented CI finding at
    lines 34-46, and the anti-hang-test-protocol skill) and Click's ``--deadline`` option enforces
    ``min=0.1`` so an already-past RELATIVE value can't even be passed on the CLI. Patching the
    one seam every graph builder converts a relative --deadline through (mirroring the
    already-expired idiom in tests/unit/test_repo_map_deadline.py:29, applied transparently
    through the CLI flag) makes any positive ``--deadline`` resolve to an expired absolute
    deadline regardless of real execution speed. Exit code must be 2, ``partial`` must be True.

LOAD-BEARING ASSERTION (the anti-false-complete ratchet): every Fixture B/C case additionally
asserts ``exit_code != 0`` alongside ``exit_code == 2`` -- a wrong/dropped-edge answer must FAIL
this gate, not just a correct answer PASS it. This is the bidirectional half of the oracle: a
one-sided "does it work on a clean repo" smoke test would miss a regression that silently starts
reporting exit 0 on a truncated scan (the exact "exit 2 sometimes means 0 hits, maybe incomplete"
trust gap CEO /goal #4 calls out).

The whole-suite autouse fixture in tests/conftest.py (``_disable_session_daemon_autostart_by_
default``) already forces ``TG_SESSION_DAEMON_AUTOSTART=0`` for every test in this file, so these
CliRunner invocations always take the COLD path -- no background session-daemon subprocess is
spawned by running this module (`callers`/`blast-radius` have a warm-daemon fast path that would
otherwise auto-start one; `importers` has no daemon fast path at all).

A companion section at the bottom (Piece 2) pins the callers-likely-first-under-deadline parity
fix: below CALLER_SCAN_FILE_CEILING, ``_cap_caller_scan_files`` now orders caller-scan candidates
(literal-hit-first, source/test-interleaved) whenever a ``--deadline`` is in play, not only once
the ceiling is exceeded -- mirroring the unconditional ordering ``tg importers`` already gets via
``_tier_reverse_importer_candidates`` (#221). Without this, a deadline-cut scan of a repo well
under the 2000-file ceiling scanned source-first and could strand a late-sorting/test-file caller
past the cut with no likely-first protection at all (mirrors the importers dogfood flap that
motivated repo_map.py's proximity tiering, see the docstring at repo_map.py:16011-16030).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

import tensor_grep.cli.repo_map as repo_map
from tensor_grep.cli import session_daemon
from tensor_grep.cli.main import app
from tests.unit.test_symbol_daemon_autostart import (
    _autostart_env,
    _probe_fake_for,
    _real_daemon,
    _serve,
)

runner = CliRunner()


# ---------------------------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------------------------


def _write_complete_repo(root: Path) -> None:
    """Fixture A (KNOWN-COMPLETE): util.py defines `f`; a.py and b.py both import util and call
    it. Every real edge: {a.py, b.py} -> f. No filler noise, no cap, no deadline needed to find
    everything."""
    (root / "util.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (root / "a.py").write_text(
        "from util import f\n\n\ndef use_a():\n    return f()\n", encoding="utf-8"
    )
    (root / "b.py").write_text(
        "from util import f\n\n\ndef use_b():\n    return f()\n", encoding="utf-8"
    )


def _write_truncated_by_cap_repo(root: Path, filler_count: int = 3) -> None:
    """Fixture B (KNOWN-TRUNCATED by cap): filler files that sort alphabetically BEFORE the real
    repo (mirrors test_result_incomplete_payload_layer.py:18-24) plus the same complete repo from
    Fixture A -- a ``--max-repo-files 1`` scan then keeps only a filler and drops util.py/a.py/
    b.py (and every edge) entirely."""
    for index in range(filler_count):
        (root / f"000_filler_{index}.py").write_text(
            f"def filler_{index}():\n    return {index}\n", encoding="utf-8"
        )
    _write_complete_repo(root)


def _force_deadline_conversion_expired(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fixture C's deterministic clock injection. Patches ``_deadline_monotonic_from_seconds`` --
    the ONE seam every graph builder (``build_file_importers``, ``build_symbol_callers``,
    ``build_symbol_blast_radius``) converts a relative ``--deadline`` through -- so ANY positive
    ``--deadline`` value supplied on the CLI (Click enforces ``min=0.1``; an already-past relative
    value cannot be passed directly) resolves to an already-EXPIRED absolute monotonic deadline,
    deterministically and independent of real wall-clock speed. ``deadline_seconds=None`` still
    maps to ``None`` (golden-parity: a command that never asked for a deadline is unaffected)."""

    def _expired(deadline_seconds: float | None) -> float | None:
        if deadline_seconds is None:
            return None
        return time.monotonic() - 1.0

    monkeypatch.setattr(repo_map, "_deadline_monotonic_from_seconds", _expired)


def _edge_file_names(payload: dict[str, Any], key: str) -> set[str]:
    return {Path(str(item["file"])).name for item in payload.get(key, []) or []}


def _is_incomplete(payload: dict[str, Any]) -> bool:
    """The exact condition the CLI's exit-code decision reads (mirrors main.py's own
    ``_scan_incomplete`` / ``_emit_symbol_command_result`` gate) -- used for the Fixture A
    "no incompleteness signal" assertion so it stays accurate even though ``result_incomplete``
    is always present-but-False on a complete CLI response, never simply absent."""
    return bool(payload.get("partial")) or bool(payload.get("result_incomplete"))


def _invoke(command: str, *args: str) -> Any:
    return runner.invoke(app, [command, *args, "--json"])


# ---------------------------------------------------------------------------------------------
# Fixture A -- KNOWN-COMPLETE: exit 0, every real edge present, no incompleteness signal.
# ---------------------------------------------------------------------------------------------


def test_importers_complete_scan_exits_0_with_every_real_edge(tmp_path: Path) -> None:
    _write_complete_repo(tmp_path)

    result = _invoke("importers", str(tmp_path / "util.py"), str(tmp_path))

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert _edge_file_names(payload, "importers") == {"a.py", "b.py"}
    assert not _is_incomplete(payload)


def test_callers_complete_scan_exits_0_with_every_real_edge(tmp_path: Path) -> None:
    _write_complete_repo(tmp_path)

    result = _invoke("callers", str(tmp_path), "f")

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert _edge_file_names(payload, "callers") == {"a.py", "b.py"}
    assert not _is_incomplete(payload)


def test_blast_radius_complete_scan_exits_0_with_every_real_edge(tmp_path: Path) -> None:
    _write_complete_repo(tmp_path)

    result = _invoke("blast-radius", str(tmp_path), "f")

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert _edge_file_names(payload, "callers") == {"a.py", "b.py"}
    assert not _is_incomplete(payload)


# ---------------------------------------------------------------------------------------------
# Fixture B -- KNOWN-TRUNCATED by cap: exit 2 (LOAD-BEARING: never 0), scan_limit truncated,
# the full JSON payload still printed (never swallowed by the exit).
# ---------------------------------------------------------------------------------------------


def test_importers_cap_truncated_exits_2_never_0_full_json_printed(tmp_path: Path) -> None:
    _write_truncated_by_cap_repo(tmp_path)

    result = _invoke("importers", str(tmp_path / "util.py"), str(tmp_path), "--max-repo-files", "1")

    assert result.exit_code == 2, result.output
    assert result.exit_code != 0, "a truncated scan must never report exit 0"
    payload = json.loads(result.stdout)
    assert payload["scan_limit"]["possibly_truncated"] is True
    assert "importers" in payload  # full payload printed, not swallowed by the exit


def test_callers_cap_truncated_exits_2_never_0_full_json_printed(tmp_path: Path) -> None:
    _write_truncated_by_cap_repo(tmp_path)

    result = _invoke("callers", str(tmp_path), "f", "--max-repo-files", "1")

    assert result.exit_code == 2, result.output
    assert result.exit_code != 0, "a truncated scan must never report exit 0"
    payload = json.loads(result.stdout)
    assert payload["scan_limit"]["possibly_truncated"] is True
    assert "callers" in payload


def test_blast_radius_cap_truncated_exits_2_never_0_full_json_printed(tmp_path: Path) -> None:
    _write_truncated_by_cap_repo(tmp_path)

    result = _invoke("blast-radius", str(tmp_path), "f", "--max-repo-files", "1")

    assert result.exit_code == 2, result.output
    assert result.exit_code != 0, "a truncated scan must never report exit 0"
    payload = json.loads(result.stdout)
    assert payload["scan_limit"]["possibly_truncated"] is True
    assert "callers" in payload


# ---------------------------------------------------------------------------------------------
# Fixture C -- KNOWN-TRUNCATED by deadline (deterministic clock injection, never a real tight
# --deadline): exit 2 (LOAD-BEARING: never 0), partial:true.
# ---------------------------------------------------------------------------------------------


def test_importers_deadline_truncated_exits_2_never_0(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_complete_repo(tmp_path)
    _force_deadline_conversion_expired(monkeypatch)

    result = _invoke("importers", str(tmp_path / "util.py"), str(tmp_path), "--deadline", "5")

    assert result.exit_code == 2, result.output
    assert result.exit_code != 0, "a truncated scan must never report exit 0"
    payload = json.loads(result.stdout)
    assert payload.get("partial") is True
    assert "importers" in payload  # full payload printed, not swallowed by the exit


def test_callers_deadline_truncated_exits_2_never_0(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_complete_repo(tmp_path)
    _force_deadline_conversion_expired(monkeypatch)

    result = _invoke("callers", str(tmp_path), "f", "--deadline", "5")

    assert result.exit_code == 2, result.output
    assert result.exit_code != 0, "a truncated scan must never report exit 0"
    payload = json.loads(result.stdout)
    assert payload.get("partial") is True
    assert "callers" in payload


def test_blast_radius_deadline_truncated_exits_2_never_0(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_complete_repo(tmp_path)
    _force_deadline_conversion_expired(monkeypatch)

    result = _invoke("blast-radius", str(tmp_path), "f", "--deadline", "5")

    assert result.exit_code == 2, result.output
    assert result.exit_code != 0, "a truncated scan must never report exit 0"
    payload = json.loads(result.stdout)
    assert payload.get("partial") is True
    assert "callers" in payload


# ---------------------------------------------------------------------------------------------
# Piece 2 -- callers likely-first-under-deadline parity fix.
#
# _cap_caller_scan_files (repo_map.py:1617) only ordered candidates via
# _order_caller_scan_candidates when the file universe exceeded CALLER_SCAN_FILE_CEILING (2000).
# A deadline-cut scan of a repo WELL UNDER that ceiling therefore got NO likely-first protection
# at all -- unlike `tg importers`, whose _tier_reverse_importer_candidates (#221) always orders
# regardless of ceiling. The fix: order whenever a deadline is in play and there are tests to
# interleave, even below the ceiling.
# ---------------------------------------------------------------------------------------------


def _write_widget_repo_with_late_sorting_test_caller(root: Path, filler_count: int) -> Path:
    """`target.py` defines `widget`; `filler_count` source files reference nothing; one TEST file
    (`tests/test_widget.py`, sorts alphabetically AFTER every `filler_*.py` and `target.py`) is
    widget's ONLY real caller. Total universe stays well under CALLER_SCAN_FILE_CEILING."""
    src = root / "src"
    src.mkdir(parents=True)
    target = src / "target.py"
    target.write_text("def widget():\n    return 1\n", encoding="utf-8")
    for index in range(filler_count):
        (src / f"filler_{index:02d}.py").write_text(f"x_{index} = {index}\n", encoding="utf-8")
    tests_dir = root / "tests"
    tests_dir.mkdir(parents=True)
    test_file = tests_dir / "test_widget.py"
    test_file.write_text(
        "from src.target import widget\n\n\ndef test_it():\n    assert widget() == 1\n",
        encoding="utf-8",
    )
    return test_file


def test_cap_caller_scan_files_orders_below_ceiling_when_deadline_supplied(
    tmp_path: Path,
) -> None:
    """Direct unit proof of the fix (mirrors test_file_deps.py's
    test_tier_reverse_importer_candidates_orders_by_proximity for the importers analog): a
    universe well under CALLER_SCAN_FILE_CEILING, with a deadline in play (not yet expired),
    now gets the SAME literal-hit-first / source-test-interleaved ordering the ceiling-exceeded
    branch already applies -- and never drops a file doing it (ceiling_exceeded stays False)."""
    test_file = _write_widget_repo_with_late_sorting_test_caller(tmp_path, filler_count=6)
    target = tmp_path / "src" / "target.py"
    fillers = [tmp_path / "src" / f"filler_{index:02d}.py" for index in range(6)]
    all_files = [target, *fillers, test_file]
    assert len(all_files) <= repo_map.CALLER_SCAN_FILE_CEILING

    bounded, ceiling_hit = repo_map._cap_caller_scan_files(
        all_files,
        symbol="widget",
        test_files=[test_file],
        deadline_monotonic=time.monotonic() + 5.0,  # in play, not expired
    )

    assert ceiling_hit is False  # pure reorder -- nothing dropped
    assert set(bounded) == set(all_files)  # same membership
    assert bounded[0] == test_file, (
        "the late-sorting test-file caller must be ordered to the front once a deadline is in "
        f"play, not stranded at the tail: got {[p.name for p in bounded]}"
    )


def test_cap_caller_scan_files_unordered_without_deadline_golden_parity(tmp_path: Path) -> None:
    """Golden-parity guard: with NO deadline supplied, behavior is BYTE-IDENTICAL to before this
    fix -- a plain prefix return in whatever order the caller passed, even though `test_files`
    is present. Only `deadline_monotonic is not None` newly triggers ordering below the ceiling."""
    test_file = _write_widget_repo_with_late_sorting_test_caller(tmp_path, filler_count=6)
    target = tmp_path / "src" / "target.py"
    fillers = [tmp_path / "src" / f"filler_{index:02d}.py" for index in range(6)]
    all_files = [target, *fillers, test_file]

    bounded, ceiling_hit = repo_map._cap_caller_scan_files(
        all_files, symbol="widget", test_files=[test_file], deadline_monotonic=None
    )

    assert ceiling_hit is False
    assert bounded == all_files  # unordered -- identical to the input order


def test_callers_deadline_cut_surfaces_late_test_file_caller_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end proof at the CONFIRMED-SET level (mirrors the importers flap test at
    test_file_deps.py's test_build_file_importers_ceiling_bounded_scan_finds_same_repo_importers_
    first, motivated by repo_map.py:16011-16030's docstring): a deadline-cut scan of a repo well
    under the 2000-file ceiling still finds widget's only caller -- a test file that sorts dead
    last among 10 filler source files plus the definition file -- because the fix orders it to
    the front of the caller-scan candidate list instead of stranding it behind every filler.

    Deterministic clock injection (anti-hang-test-protocol): `time.monotonic` is patched to a
    STATIC value that only advances via a targeted hook on `_file_may_contain_literal_symbol`
    (the per-candidate cost both the ordering probe and the main scan loop's first check pay),
    mirroring the advancing-clock idiom in tests/unit/test_repo_map_deadline.py. `build_repo_map`
    itself pays no ticks (nothing hooks its own internal checks), so the full repo parses cleanly
    and only the CALLER-SCAN phase this fix touches gets cut short.
    """
    filler_count = 10
    test_file = _write_widget_repo_with_late_sorting_test_caller(tmp_path, filler_count)

    base = 1000.0
    clock = {"t": base}
    monkeypatch.setattr(repo_map.time, "monotonic", lambda: clock["t"])
    original_literal_check = repo_map._file_may_contain_literal_symbol

    def _advancing_literal_check(path: Path, symbol: str) -> bool:
        clock["t"] += 1.0
        return bool(original_literal_check(path, symbol))

    monkeypatch.setattr(repo_map, "_file_may_contain_literal_symbol", _advancing_literal_check)

    # Budget: enough for the ordering probe to visit every one of the 12 universe files (one tick
    # each) plus a handful of main-loop files -- comfortably NOT enough to reach all 12 in the
    # main loop unordered, which is exactly the regime this fix targets.
    total_universe = filler_count + 2  # target.py + fillers + test_widget.py
    deadline_seconds = float(total_universe) + 5.0

    result = repo_map.build_symbol_callers(
        "widget", str(tmp_path), deadline_seconds=deadline_seconds
    )

    assert result.get("partial") is True, "fixture must actually be deadline-truncated"
    scanned = result["deadline_limit"]["caller_files_scanned"]
    assert scanned < total_universe, f"expected a genuine mid-scan cut, scanned={scanned}"
    caller_files = {Path(str(item["file"])).name for item in result["callers"]}
    assert caller_files == {test_file.name}, (
        "the late-sorting test-file caller must be found despite the truncated scan, not "
        f"stranded past the cut: found callers from {caller_files}"
    )


# ---------------------------------------------------------------------------------------------
# Piece 3 (#245) -- warm-daemon route coverage for callers/blast-radius.
#
# IMPORTANT SCOPE NOTE (verified against the real code, not assumed): callers/blast-radius
# --deadline UNCONDITIONALLY forces the cold path. main.py's callers()/blast_radius() commands
# only attempt `_maybe_symbol_command_via_running_daemon` `if deadline is None else None`
# (main.py:11134-11145 / :11468-11479, both carrying the comment "a warm session's cached
# repo_map cannot honor a fresh per-request scan deadline"), and session_daemon.py:61-66
# documents the same fact from the daemon side: "The 5 symbol commands (defs/impact/refs/
# callers/blast_radius) ... still run unbounded on THIS daemon path -- that residual is #390,
# still open." There is therefore NO code path today where a REAL warm/daemon-SERVED
# callers/blast-radius response is itself deadline-truncated -- "deadline truncation ON the
# warm route" is unreachable by design, not an untested gap, and a test that tried to fake one
# (e.g. by hand-crafting a mock daemon response with partial=True) would only be re-proving the
# CLI's own `_scan_incomplete` gate (already covered by test_render_daemon_exit_codes.py), not
# anything about the warm route's deadline handling.
#
# The closest bounded, genuinely-valuable warm-route coverage instead: prove the safety gate
# itself (--deadline forces cold) holds even with a REAL warm daemon alive and reachable -- not
# just "cold because no daemon exists", which is all Fixture C above can exercise (this whole
# file's autouse fixture forces TG_SESSION_DAEMON_AUTOSTART=0, so no daemon is ever running
# there). A regression that dropped or weakened the `if deadline is None` gate could silently
# serve a stale, non-deadline-aware CACHED answer instead of honoring --deadline -- exactly the
# class of silent completeness lie #200/#390 are about, just one seam further upstream than
# Fixture C covers. Uses the same real in-process _ThreadedSessionDaemon harness
# test_orient_agent_daemon.py reuses from test_symbol_daemon_autostart.py, and the same
# deterministic-clock deadline-expiry injection (_force_deadline_conversion_expired) Fixture C
# uses above -- never a real tight --deadline (OS-speed-fragile per
# test_agent_cold_deadline_tail_sla_220.py:34-46).
# ---------------------------------------------------------------------------------------------


def _assert_deadline_forces_cold_despite_live_daemon(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    command: str,
    warm_routing_reason: str,
) -> None:
    _write_complete_repo(tmp_path)

    server = _real_daemon(tmp_path)
    _serve(server)
    try:
        monkeypatch.setattr(session_daemon, "_probe_daemon", _probe_fake_for(server, "test-token"))
        _autostart_env(monkeypatch, enabled=True)

        # Sanity precondition: the daemon really is alive, reachable, and WOULD serve a
        # no-deadline request warm -- otherwise "still forces cold despite a live daemon" below
        # would be vacuously true (there'd be nothing live to bypass).
        warm_probe = runner.invoke(app, [command, str(tmp_path), "f", "--json"])
        assert warm_probe.exit_code == 0, warm_probe.output
        assert json.loads(warm_probe.stdout)["routing_reason"] == warm_routing_reason, (
            "precondition failed: the warm daemon route did not actually serve the undeadlined "
            "sanity probe, so this test cannot prove anything about bypassing it"
        )

        # Spy (not a hard fail) on the daemon RPC seam so a real regression is diagnosable via
        # the assertion message below rather than an opaque AssertionError from inside a
        # monkeypatched stub.
        calls: list[dict[str, Any]] = []
        original_request = session_daemon.request_running_session_daemon

        def _spy_request(path: str, request: dict[str, Any]) -> dict[str, Any] | None:
            calls.append(request)
            return original_request(path, request)

        monkeypatch.setattr(session_daemon, "request_running_session_daemon", _spy_request)
        _force_deadline_conversion_expired(monkeypatch)

        result = runner.invoke(app, [command, str(tmp_path), "f", "--json", "--deadline", "5"])
    finally:
        server.shutdown()
        server.server_close()

    assert not calls, (
        f"{command} --deadline must never contact the daemon, even with a live reachable "
        f"daemon standing by -- got {len(calls)} daemon request(s): {calls}"
    )
    assert result.exit_code == 2, result.output
    assert result.exit_code != 0, "a deadline-truncated scan must never report exit 0"
    payload = json.loads(result.stdout)
    assert payload.get("partial") is True
    assert payload.get("routing_reason") != warm_routing_reason, (
        "a --deadline response must never carry the warm-daemon routing_reason"
    )


def test_callers_warm_daemon_alive_but_deadline_still_forces_cold_partial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _assert_deadline_forces_cold_despite_live_daemon(
        monkeypatch, tmp_path, command="callers", warm_routing_reason="session-callers"
    )


def test_blast_radius_warm_daemon_alive_but_deadline_still_forces_cold_partial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _assert_deadline_forces_cold_despite_live_daemon(
        monkeypatch, tmp_path, command="blast-radius", warm_routing_reason="session-blast-radius"
    )

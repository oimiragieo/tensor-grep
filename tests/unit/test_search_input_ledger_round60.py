"""Round-60 RED group 2: SearchInputLedger caps + real Python route doors.

Task 2A / #89 Step 1. Independently runnable. Asserts inclusive cap-1/cap/cap+1
for every numeric dimension, separate pattern vs ignore 65,536 totals, and real
bootstrap / full_cli doors with injectable child-start seams.

Native doors (direct_native / native_to_rg / native_to_sidecar) live in stable
Rust RED tests that drive main_inner / execute_ripgrep_search /
python_sidecar early passthrough — not Python surrogates.

These tests MUST fail against unmodified / behaviorless seams. Do not weaken.
"""

from __future__ import annotations

import io
import json
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from tensor_grep.cli import bootstrap as bootstrap_mod
from tensor_grep.cli import search_input_ledger as ledger_mod

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "search_input_ledger_v1.json"

_AGG_CAP = ledger_mod.MAX_COMBINED_DECODED_BYTES
_FILE_CAP = ledger_mod.MAX_PATTERN_OR_IGNORE_FILE_BYTES
_RULE_CAP = ledger_mod.MAX_PATTERN_OR_IGNORE_RULE_BYTES
_MEM_CAP = ledger_mod.MAX_COMPILED_MATCHER_LIVE_MEMORY_BYTES
_TRANS_CAP = ledger_mod.MAX_MATCHER_TRANSITIONS
_DEADLINE = ledger_mod.REQUEST_DEADLINE_SECONDS


def task2a_owned(fn):  # type: ignore[no-untyped-def]
    """Ownership marker for closed-world AST census (must match helper name)."""
    fn._task2a_owned = True  # type: ignore[attr-defined]
    return fn


def _fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _install_child_start_hooks(
    monkeypatch: pytest.MonkeyPatch, counters: ledger_mod.RouteProcessCounters
) -> None:
    """Wire producer child-start seams; block actual OS starts while counting."""

    def _hook(kind: str, argv: list[str]) -> None:
        _ = argv
        counters.record(kind)
        raise RuntimeError(f"child start blocked for observation: {kind}")

    monkeypatch.setattr(bootstrap_mod, "_CHILD_START_HOOK", _hook)
    # Full CLI lives in main.py; import errors must surface, never become exit 2.
    from tensor_grep.cli import main as main_mod

    monkeypatch.setattr(main_mod, "_CHILD_START_HOOK", _hook)

    def _blocked_popen(argv: list[str]):
        counters.record("rg" if "rg" in Path(argv[0]).name else "native")
        raise RuntimeError("popen blocked")

    monkeypatch.setattr(bootstrap_mod, "_popen_child", _blocked_popen)


def _extract_envelope(stdout: str, stderr: str, code: int) -> dict:
    """Best-effort envelope from exit/JSON/text; producers lack this today → RED."""
    blob = "\n".join([stdout or "", stderr or ""])
    for line in blob.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and (
            "incomplete_reason_class" in payload or "result_incomplete" in payload
        ):
            payload = dict(payload)
            payload.setdefault("exit", code)
            return payload
    return {
        "exit": code,
        "result_incomplete": False,
        "incomplete_reason_class": None,
        "raw": blob[:500],
    }


def _invoke_bootstrap(
    monkeypatch: pytest.MonkeyPatch,
    counters: ledger_mod.RouteProcessCounters,
    tmp_path: Path,
    *,
    argv_tail: list[str],
) -> dict:
    root = tmp_path / "repo"
    root.mkdir(exist_ok=True)
    (root / "a.txt").write_text("needle\n", encoding="utf-8")
    _install_child_start_hooks(monkeypatch, counters)
    monkeypatch.setattr(bootstrap_mod, "resolve_native_tg_binary", lambda: None)
    monkeypatch.setattr(bootstrap_mod, "resolve_ripgrep_binary", lambda: "rg")
    monkeypatch.setattr(
        bootstrap_mod,
        "_run_full_cli",
        lambda: (_ for _ in ()).throw(RuntimeError("full_cli should not run for bootstrap door")),
    )
    monkeypatch.setattr(sys, "argv", ["tg", *argv_tail, str(root)])
    out = io.StringIO()
    err = io.StringIO()
    code = 0
    try:
        with redirect_stdout(out), redirect_stderr(err):
            bootstrap_mod.main_entry()
    except SystemExit as se:
        code = int(se.code or 0)
    except RuntimeError as rt:
        # Child-start observation hook fired — record as non-refusal (not exit-2).
        # Import/setup errors must NOT be translated into exit 2.
        if "child start blocked" not in str(rt) and "observed child start" not in str(rt):
            raise
        code = 0
        err.write(f"{rt}\n")
    return _extract_envelope(out.getvalue(), err.getvalue(), code)


def _invoke_full_cli(
    monkeypatch: pytest.MonkeyPatch,
    counters: ledger_mod.RouteProcessCounters,
    tmp_path: Path,
    *,
    argv_tail: list[str],
) -> dict:
    root = tmp_path / "repo"
    root.mkdir(exist_ok=True)
    (root / "a.txt").write_text("needle\n", encoding="utf-8")
    _install_child_start_hooks(monkeypatch, counters)
    monkeypatch.setattr(bootstrap_mod, "resolve_native_tg_binary", lambda: None)
    # Force full CLI door (TG-only --cpu). Import/setup errors re-raise immediately.
    monkeypatch.setattr(sys, "argv", ["tg", *argv_tail, str(root), "--cpu"])
    out = io.StringIO()
    err = io.StringIO()
    code = 0
    try:
        with redirect_stdout(out), redirect_stderr(err):
            bootstrap_mod._run_full_cli()
    except SystemExit as se:
        code = int(se.code or 0)
    except RuntimeError as rt:
        # Child-start observation hook fired — record as non-refusal (not exit-2).
        if "child start blocked" not in str(rt) and "observed child start" not in str(rt):
            raise
        code = 0
        err.write(f"{rt}\n")
    return _extract_envelope(out.getvalue(), err.getvalue(), code)


@task2a_owned
def test_fixture_pins_round60_caps() -> None:
    data = _fixture()
    assert data["incomplete_reason_class"] == "search_input_limit"
    caps = data["caps"]
    assert caps["max_pattern_or_ignore_file_bytes"] == ledger_mod.MAX_PATTERN_OR_IGNORE_FILE_BYTES
    assert caps["max_combined_pattern_ignore_files"] == ledger_mod.MAX_COMBINED_PATTERN_IGNORE_FILES
    assert caps["max_combined_decoded_bytes"] == ledger_mod.MAX_COMBINED_DECODED_BYTES
    assert caps["max_pattern_or_ignore_rule_bytes"] == ledger_mod.MAX_PATTERN_OR_IGNORE_RULE_BYTES
    assert caps["max_combined_patterns"] == ledger_mod.MAX_COMBINED_PATTERNS
    assert caps["max_combined_ignore_rules"] == ledger_mod.MAX_COMBINED_IGNORE_RULES
    assert (
        caps["max_compiled_matcher_live_memory_bytes"]
        == ledger_mod.MAX_COMPILED_MATCHER_LIVE_MEMORY_BYTES
    )
    assert caps["max_matcher_transitions"] == ledger_mod.MAX_MATCHER_TRANSITIONS
    assert caps["request_deadline_seconds"] == ledger_mod.REQUEST_DEADLINE_SECONDS
    assert caps["max_combined_patterns"] == caps["max_combined_ignore_rules"] == 65_536
    assert list(data["route_doors"]) == list(ledger_mod.ROUTE_DOORS)


@task2a_owned
def test_ledger_installed_before_bootstrap_door(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    entered: list[str] = []
    monkeypatch.setattr(ledger_mod, "on_public_route_entry", lambda r: entered.append(r))
    counters = ledger_mod.RouteProcessCounters()
    _invoke_bootstrap(monkeypatch, counters, tmp_path, argv_tail=["search", "--pcre2", "needle"])
    assert entered == ["bootstrap"], (
        f"SearchInputLedger must be installed via on_public_route_entry before "
        f"route selection on bootstrap; observed entries={entered!r}"
    )
    assert counters.any_started() is False


@task2a_owned
def test_ledger_installed_before_full_cli_door(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    entered: list[str] = []
    monkeypatch.setattr(ledger_mod, "on_public_route_entry", lambda r: entered.append(r))
    counters = ledger_mod.RouteProcessCounters()
    _invoke_full_cli(monkeypatch, counters, tmp_path, argv_tail=["search", "--pcre2", "needle"])
    assert entered == ["full_cli"], (
        f"SearchInputLedger must be installed via on_public_route_entry before "
        f"route selection on full_cli; observed entries={entered!r}"
    )
    assert counters.any_started() is False


@task2a_owned
@pytest.mark.parametrize(
    ("size", "expect_ok"),
    [
        (_FILE_CAP - 1, True),
        (_FILE_CAP, True),
        (_FILE_CAP + 1, False),
    ],
    ids=["per_file_cap_minus_1", "per_file_cap", "per_file_cap_plus_1"],
)
def test_per_file_bytes_inclusive_cap(size: int, expect_ok: bool) -> None:
    ledger = ledger_mod.SearchInputLedger()
    if expect_ok:
        ledger.admit_file(size_bytes=size, source="pattern")
        assert ledger.file_count == 1
        assert ledger.decoded_bytes == size
        return
    with pytest.raises(ledger_mod.SearchInputLimitExceeded) as excinfo:
        ledger.admit_file(size_bytes=size, source="ignore")
    assert excinfo.value.incomplete_reason_class == ledger_mod.SEARCH_INPUT_LIMIT_REASON
    assert excinfo.value.limit == _FILE_CAP


@task2a_owned
@pytest.mark.parametrize(
    ("files", "bytes_each", "expect_ok"),
    [
        (31, 100, True),
        (32, 100, True),
        (33, 100, False),
    ],
    ids=["files_cap_minus_1", "files_cap", "files_cap_plus_1"],
)
def test_combined_file_count_inclusive_cap(files: int, bytes_each: int, expect_ok: bool) -> None:
    ledger = ledger_mod.SearchInputLedger()
    if expect_ok:
        for i in range(files):
            source = "pattern" if i % 2 == 0 else "ignore"
            ledger.admit_file(size_bytes=bytes_each, source=source)
        assert ledger.file_count == files
        return
    with pytest.raises(ledger_mod.SearchInputLimitExceeded) as excinfo:
        for i in range(files):
            source = "pattern" if i % 2 == 0 else "ignore"
            ledger.admit_file(size_bytes=bytes_each, source=source)
    assert excinfo.value.incomplete_reason_class == ledger_mod.SEARCH_INPUT_LIMIT_REASON
    assert excinfo.value.limit == ledger_mod.MAX_COMBINED_PATTERN_IGNORE_FILES


@task2a_owned
@pytest.mark.parametrize(
    ("sizes", "expect_ok"),
    [
        ([_FILE_CAP, _FILE_CAP, _FILE_CAP, _FILE_CAP - 1], True),
        ([_FILE_CAP, _FILE_CAP, _FILE_CAP, _FILE_CAP], True),
        ([_FILE_CAP, _FILE_CAP, _FILE_CAP, _FILE_CAP, 1], False),
    ],
    ids=["agg_bytes_cap_minus_1", "agg_bytes_cap", "agg_bytes_cap_plus_1"],
)
def test_combined_decoded_bytes_inclusive_cap(sizes: list[int], expect_ok: bool) -> None:
    assert all(s <= _FILE_CAP for s in sizes), "aggregate cases must not trip per-file first"
    ledger = ledger_mod.SearchInputLedger()
    if expect_ok:
        for i, size in enumerate(sizes):
            source = "pattern" if i % 2 == 0 else "ignore"
            ledger.admit_file(size_bytes=size, source=source)
        assert ledger.decoded_bytes == sum(sizes)
        assert ledger.decoded_bytes <= _AGG_CAP
        return
    with pytest.raises(ledger_mod.SearchInputLimitExceeded) as excinfo:
        for i, size in enumerate(sizes):
            source = "pattern" if i % 2 == 0 else "ignore"
            ledger.admit_file(size_bytes=size, source=source)
    assert excinfo.value.incomplete_reason_class == ledger_mod.SEARCH_INPUT_LIMIT_REASON
    assert excinfo.value.limit == _AGG_CAP


@task2a_owned
@pytest.mark.parametrize(
    ("size", "expect_ok"),
    [
        (_RULE_CAP - 1, True),
        (_RULE_CAP, True),
        (_RULE_CAP + 1, False),
    ],
    ids=["rule_bytes_cap_minus_1", "rule_bytes_cap", "rule_bytes_cap_plus_1"],
)
def test_per_rule_bytes_inclusive_cap(size: int, expect_ok: bool) -> None:
    ledger = ledger_mod.SearchInputLedger()
    if expect_ok:
        ledger.admit_rule_bytes(size_bytes=size, kind="pattern")
        return
    with pytest.raises(ledger_mod.SearchInputLimitExceeded) as excinfo:
        ledger.admit_rule_bytes(size_bytes=size, kind="ignore")
    assert excinfo.value.incomplete_reason_class == ledger_mod.SEARCH_INPUT_LIMIT_REASON
    assert excinfo.value.limit == _RULE_CAP


@task2a_owned
@pytest.mark.parametrize(
    ("count", "expect_ok"),
    [
        (65_535, True),
        (65_536, True),
        (65_537, False),
    ],
    ids=["patterns_cap_minus_1", "patterns_cap", "patterns_cap_plus_1"],
)
def test_pattern_total_inclusive_cap(count: int, expect_ok: bool) -> None:
    ledger = ledger_mod.SearchInputLedger()
    if expect_ok:
        ledger.admit_patterns(count, source="positional+-e+-f")
        assert ledger.pattern_count == count
        return
    with pytest.raises(ledger_mod.SearchInputLimitExceeded) as excinfo:
        ledger.admit_patterns(count, source="positional+-e+-f")
    assert excinfo.value.incomplete_reason_class == ledger_mod.SEARCH_INPUT_LIMIT_REASON
    assert excinfo.value.limit == ledger_mod.MAX_COMBINED_PATTERNS


@task2a_owned
@pytest.mark.parametrize(
    ("count", "expect_ok"),
    [
        (65_535, True),
        (65_536, True),
        (65_537, False),
    ],
    ids=["ignores_cap_minus_1", "ignores_cap", "ignores_cap_plus_1"],
)
def test_ignore_total_inclusive_cap(count: int, expect_ok: bool) -> None:
    ledger = ledger_mod.SearchInputLedger()
    if expect_ok:
        half = count // 2
        ledger.admit_ignore_rules(half or count, source="explicit")
        if count > 1:
            ledger.admit_ignore_rules(count - ledger.ignore_rule_count, source="generated")
        assert ledger.ignore_rule_count == count
        return
    with pytest.raises(ledger_mod.SearchInputLimitExceeded) as excinfo:
        ledger.admit_ignore_rules(count, source="explicit+generated")
    assert excinfo.value.incomplete_reason_class == ledger_mod.SEARCH_INPUT_LIMIT_REASON
    assert excinfo.value.limit == ledger_mod.MAX_COMBINED_IGNORE_RULES


@task2a_owned
def test_split_counter_patterns_and_ignores_are_independent() -> None:
    ledger = ledger_mod.SearchInputLedger()
    ledger.admit_patterns(ledger_mod.MAX_COMBINED_PATTERNS, source="positional+-e+-f")
    ledger.admit_ignore_rules(ledger_mod.MAX_COMBINED_IGNORE_RULES // 2, source="explicit")
    ledger.admit_ignore_rules(
        ledger_mod.MAX_COMBINED_IGNORE_RULES - ledger.ignore_rule_count,
        source="generated",
    )
    assert ledger.pattern_count == ledger_mod.MAX_COMBINED_PATTERNS
    assert ledger.ignore_rule_count == ledger_mod.MAX_COMBINED_IGNORE_RULES


@task2a_owned
@pytest.mark.parametrize(
    ("bytes_", "expect_ok"),
    [
        (_MEM_CAP - 1, True),
        (_MEM_CAP, True),
        (_MEM_CAP + 1, False),
    ],
    ids=["compiled_mem_cap_minus_1", "compiled_mem_cap", "compiled_mem_cap_plus_1"],
)
def test_compiled_live_memory_inclusive_cap(bytes_: int, expect_ok: bool) -> None:
    ledger = ledger_mod.SearchInputLedger()
    if expect_ok:
        ledger.charge_matcher_construction(live_memory_bytes=bytes_)
        assert ledger.compiled_live_memory_bytes == bytes_
        return
    with pytest.raises(ledger_mod.SearchInputLimitExceeded) as excinfo:
        ledger.charge_matcher_construction(live_memory_bytes=bytes_)
    assert excinfo.value.incomplete_reason_class == ledger_mod.SEARCH_INPUT_LIMIT_REASON
    assert excinfo.value.limit == _MEM_CAP


@task2a_owned
@pytest.mark.parametrize(
    ("count", "expect_ok"),
    [
        (_TRANS_CAP - 1, True),
        (_TRANS_CAP, True),
        (_TRANS_CAP + 1, False),
    ],
    ids=["transitions_cap_minus_1", "transitions_cap", "transitions_cap_plus_1"],
)
def test_matcher_transitions_inclusive_cap(count: int, expect_ok: bool) -> None:
    ledger = ledger_mod.SearchInputLedger()
    if expect_ok:
        ledger.charge_matcher_transitions(count)
        assert ledger.matcher_transitions == count
        return
    with pytest.raises(ledger_mod.SearchInputLimitExceeded) as excinfo:
        ledger.charge_matcher_transitions(count)
    assert excinfo.value.incomplete_reason_class == ledger_mod.SEARCH_INPUT_LIMIT_REASON
    assert excinfo.value.limit == _TRANS_CAP


@task2a_owned
@pytest.mark.parametrize(
    ("elapsed", "expect_ok"),
    [
        (_DEADLINE - 0.001, True),
        (float(_DEADLINE), True),
        (_DEADLINE + 0.001, False),
    ],
    ids=["deadline_cap_minus_1", "deadline_cap", "deadline_cap_plus_1"],
)
def test_deadline_inclusive_cap(elapsed: float, expect_ok: bool) -> None:
    ledger = ledger_mod.SearchInputLedger()
    assert ledger.deadline_seconds == _DEADLINE
    if expect_ok:
        ledger.check_deadline(elapsed_seconds=elapsed)
        return
    with pytest.raises(ledger_mod.SearchInputLimitExceeded) as excinfo:
        ledger.check_deadline(elapsed_seconds=elapsed)
    assert excinfo.value.incomplete_reason_class == ledger_mod.SEARCH_INPUT_LIMIT_REASON
    assert excinfo.value.limit == _DEADLINE


@task2a_owned
def test_uninstrumented_pcre2_refused_on_bootstrap(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Exact bootstrap node: exit-2 search_input_limit envelope + zero child starts."""
    counters = ledger_mod.RouteProcessCounters()
    envelope = _invoke_bootstrap(
        monkeypatch, counters, tmp_path, argv_tail=["search", "--pcre2", "needle"]
    )
    assert envelope.get("exit") == 2
    assert envelope.get("result_incomplete") is True
    assert envelope.get("incomplete_reason_class") == ledger_mod.SEARCH_INPUT_LIMIT_REASON
    assert counters.any_started() is False, (
        f"bootstrap must refuse uninstrumented PCRE2 before any "
        f"compiler/native/rg/sidecar/matcher start; counters={counters!r}"
    )


@task2a_owned
def test_uninstrumented_pcre2_refused_on_full_cli(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Exact full_cli node: exit-2 search_input_limit envelope + zero child starts."""
    counters = ledger_mod.RouteProcessCounters()
    envelope = _invoke_full_cli(
        monkeypatch, counters, tmp_path, argv_tail=["search", "--pcre2", "needle"]
    )
    assert envelope.get("exit") == 2
    assert envelope.get("result_incomplete") is True
    assert envelope.get("incomplete_reason_class") == ledger_mod.SEARCH_INPUT_LIMIT_REASON
    assert counters.any_started() is False, (
        f"full_cli must refuse uninstrumented PCRE2 before any "
        f"compiler/native/rg/sidecar/matcher start; counters={counters!r}"
    )


@task2a_owned
def test_below_cap_non_pcre2_bootstrap_starts_producer_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Below-cap normal positive: bootstrap must start rg exactly once, non-incomplete.

    A reject-all SearchInputLedger implementation must fail this control.
    Observation is via the real ``_popen_child`` producer start (A62), not a
    pre-start production self-attest.
    """
    counters = ledger_mod.RouteProcessCounters()
    starts: list[str] = []

    def _popen(argv: list[str]):
        kind = "rg" if "rg" in Path(argv[0]).name or argv[0] == "rg" else "native"
        starts.append(kind)
        counters.record(kind)
        raise RuntimeError(f"observed child start: {kind} {argv[:3]}")

    monkeypatch.setattr(bootstrap_mod, "_popen_child", _popen)
    monkeypatch.setattr(bootstrap_mod, "resolve_native_tg_binary", lambda: None)
    monkeypatch.setattr(bootstrap_mod, "resolve_ripgrep_binary", lambda: "rg")
    monkeypatch.setattr(
        bootstrap_mod,
        "_run_full_cli",
        lambda: (_ for _ in ()).throw(RuntimeError("full_cli should not run")),
    )
    root = tmp_path / "repo"
    root.mkdir()
    (root / "a.txt").write_text("needle\n", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["tg", "search", "needle", str(root)])
    out = io.StringIO()
    err = io.StringIO()
    code = 0
    try:
        with redirect_stdout(out), redirect_stderr(err):
            bootstrap_mod.main_entry()
    except SystemExit as se:
        code = int(se.code or 0)
    except RuntimeError as err_rt:
        # Child-start popen fired — count that as the observed start.
        assert "observed child start" in str(err_rt)
        code = 0
    envelope = _extract_envelope(out.getvalue(), err.getvalue(), code)
    assert starts == ["rg"], (
        f"bootstrap must start exact producer kind rg once and zero every other; starts={starts!r}"
    )
    assert starts.count("native") == 0
    assert starts.count("sidecar") == 0
    assert starts.count("compiler") == 0
    assert envelope.get("result_incomplete") is not True
    assert envelope.get("incomplete_reason_class") != ledger_mod.SEARCH_INPUT_LIMIT_REASON


@task2a_owned
def test_below_cap_non_pcre2_full_cli_starts_producer_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Below-cap normal positive: full_cli must start its producer exactly once.

    Observation is after actual search/backend start (A62 / Sol R2 HIGH#7), not
    Pipeline construction alone.
    """
    counters = ledger_mod.RouteProcessCounters()
    starts: list[str] = []

    class _Backend:
        def search(self, *args, **kwargs):  # noqa: ANN002, ANN003
            _ = args, kwargs
            starts.append("cpu")
            counters.record("cpu")
            raise RuntimeError("observed child start: cpu")

        def search_many(self, *args, **kwargs):  # noqa: ANN002, ANN003
            return self.search(*args, **kwargs)

        def search_passthrough(self, *args, **kwargs):  # noqa: ANN002, ANN003
            _ = args, kwargs
            raise AssertionError("passthrough must not run in this arm")

    class _FakePipeline:
        selected_backend_name = "CPUBackend"
        selected_backend_reason = "force_cpu"
        selected_gpu_device_ids: list[int] = []
        selected_gpu_chunk_plan_mb: list[int] = []
        fallback_reason = None

        def __init__(self, *args, **kwargs):
            _ = args, kwargs

        def get_backend(self):
            return _Backend()

    from tensor_grep.cli import main as main_mod

    monkeypatch.setattr(main_mod, "Pipeline", _FakePipeline, raising=False)
    import tensor_grep.core.pipeline as pipeline_mod

    monkeypatch.setattr(pipeline_mod, "Pipeline", _FakePipeline)
    monkeypatch.setattr(bootstrap_mod, "resolve_native_tg_binary", lambda: None)
    root = tmp_path / "repo"
    root.mkdir()
    (root / "a.txt").write_text("needle\n", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["tg", "search", "needle", str(root), "--cpu"])
    out = io.StringIO()
    err = io.StringIO()
    code = 0
    try:
        with redirect_stdout(out), redirect_stderr(err):
            bootstrap_mod._run_full_cli()
    except SystemExit as se:
        code = int(se.code or 0)
    except RuntimeError as err_rt:
        assert "observed child start" in str(err_rt)
        code = 0
    envelope = _extract_envelope(out.getvalue(), err.getvalue(), code)
    assert starts == ["cpu"], (
        f"full_cli --cpu must start exact producer kind cpu once; starts={starts!r}"
    )
    for other in ("rg", "native", "sidecar", "compiler", "matcher"):
        assert starts.count(other) == 0, (
            f"full_cli must zero every other producer; starts={starts!r}"
        )
    assert envelope.get("result_incomplete") is not True
    assert envelope.get("incomplete_reason_class") != ledger_mod.SEARCH_INPUT_LIMIT_REASON


@task2a_owned
def test_producer_hook_does_not_self_attest_before_actual_start(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """HIGH#9 / A62: production emit must fire only after real producer start."""
    order: list[str] = []

    def _hook(kind: str, argv: list[str]) -> None:
        _ = argv
        order.append(f"hook:{kind}")

    def _popen(argv: list[str]):
        order.append("popen")
        # Minimal stand-in so wait() is never reached (hook may raise).
        raise RuntimeError(f"blocked after start: {argv[0]}")

    monkeypatch.setattr(bootstrap_mod, "_CHILD_START_HOOK", _hook)
    monkeypatch.setattr(bootstrap_mod, "_popen_child", _popen)
    monkeypatch.setattr(bootstrap_mod, "resolve_native_tg_binary", lambda: None)
    monkeypatch.setattr(bootstrap_mod, "resolve_ripgrep_binary", lambda: "rg")
    monkeypatch.setattr(
        bootstrap_mod,
        "_run_full_cli",
        lambda: (_ for _ in ()).throw(RuntimeError("full_cli should not run")),
    )
    root = tmp_path / "repo"
    root.mkdir()
    (root / "a.txt").write_text("needle\n", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["tg", "search", "needle", str(root)])
    try:
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            bootstrap_mod.main_entry()
    except (SystemExit, RuntimeError):
        pass
    assert order == ["popen"], (
        f"pre-start self-attest forbidden; expected popen-only before hook "
        f"(hook must not fire when popen raises); order={order!r}"
    )
    # Positive control: when popen succeeds, hook fires AFTER popen.
    order.clear()

    class _Proc:
        def poll(self):
            return 0

        def wait(self, timeout=None):
            _ = timeout
            return 0

        def terminate(self):
            return None

        def kill(self):
            return None

    def _popen_ok(argv: list[str]):
        _ = argv
        order.append("popen")
        return _Proc()

    monkeypatch.setattr(bootstrap_mod, "_popen_child", _popen_ok)
    monkeypatch.setattr(sys, "argv", ["tg", "search", "needle", str(root)])
    try:
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            bootstrap_mod.main_entry()
    except SystemExit:
        pass
    assert order[:2] == ["popen", "hook:rg"], (
        f"hook must fire only after actual popen start; order={order!r}"
    )


@task2a_owned
def test_pattern_file_refuses_unbounded_read_before_ledger(tmp_path: Path) -> None:
    """HIGH#10 / A67: -f/--file must not unbounded-read before ledger/size gate."""
    oversize = tmp_path / "patterns.txt"
    # Cap+1 bytes — refuse without materialising via unbounded read_text.
    oversize.write_bytes(b"x" * (ledger_mod.MAX_PATTERN_OR_IGNORE_FILE_BYTES + 1))
    with pytest.raises(ledger_mod.SearchInputLimitExceeded) as ei:
        ledger_mod.read_pattern_or_ignore_file_bounded(oversize)
    assert ei.value.dimension == "pattern_or_ignore_file_bytes"
    assert ei.value.observed == ledger_mod.MAX_PATTERN_OR_IGNORE_FILE_BYTES + 1
    assert ei.value.limit == ledger_mod.MAX_PATTERN_OR_IGNORE_FILE_BYTES
    assert ei.value.incomplete_reason_class == ledger_mod.SEARCH_INPUT_LIMIT_REASON

    # Positive control: at-cap file reads after size gate.
    ok = tmp_path / "ok.txt"
    ok.write_bytes(b"a" * ledger_mod.MAX_PATTERN_OR_IGNORE_FILE_BYTES)
    text = ledger_mod.read_pattern_or_ignore_file_bounded(ok)
    assert len(text.encode("utf-8")) == ledger_mod.MAX_PATTERN_OR_IGNORE_FILE_BYTES

    # Ledger admit is required BEFORE bytes when a ledger is supplied (no-refund).
    ledger = ledger_mod.SearchInputLedger()
    tiny = tmp_path / "tiny.txt"
    tiny.write_text("needle\n", encoding="utf-8")
    text2 = ledger_mod.read_pattern_or_ignore_file_bounded(tiny, ledger=ledger)
    assert text2.replace("\r\n", "\n") == "needle\n"
    assert ledger.file_count == 1
    assert ledger.decoded_bytes == len(text2.encode("utf-8"))

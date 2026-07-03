"""Round-2 DoS/hardening batch (#26):
1. CPUBackend._search_ltl was O(n^2) — a `A -> eventually B` query where A matches often and
   B rarely re-scanned to EOF per left hit. Now a single backward pass makes it O(n).
2. scan_guardrails treated ANY non-None max_depth as a sufficient traversal bound, so
   `--max-depth 1000000` rubber-stamped a hostile-root scan (defeating the broad-scan refusal).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tensor_grep.backends.cpu_backend import CPUBackend
from tensor_grep.cli.scan_guardrails import _has_scan_bound, _is_bounded_depth
from tensor_grep.core.config import SearchConfig

# --- LTL O(n^2) -> O(n), semantics preserved ---


def test_ltl_finds_eventually_sequence(tmp_path: Path) -> None:
    f = tmp_path / "seq.txt"
    f.write_text("has A here\nnothing\nhas B here\n", encoding="utf-8")
    result = CPUBackend()._search_ltl(f, "A -> eventually B", SearchConfig())
    assert result.total_matches == 1
    assert result.matches[0].line_number == 1  # the A line
    assert result.matches[1].line_number == 3  # the first B strictly after it


def test_ltl_no_right_match_returns_zero(tmp_path: Path) -> None:
    f = tmp_path / "noright.txt"
    f.write_text("A\nA\nA\n", encoding="utf-8")
    result = CPUBackend()._search_ltl(f, "A -> eventually ZZZ", SearchConfig())
    assert result.total_matches == 0


def test_ltl_right_scan_is_linear_not_quadratic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    n = 400
    f = tmp_path / "patho.txt"
    f.write_text("A\n" * n, encoding="utf-8")  # every line matches left, none matches right
    backend = CPUBackend()
    calls = {"right": 0}
    orig = CPUBackend._compile_ltl

    def _spy(pattern: str, flags: int):
        left, right = orig(pattern, flags)

        class _Counting:
            def search(self, text: str):
                calls["right"] += 1
                return right.search(text)

        return left, _Counting()

    monkeypatch.setattr(backend, "_compile_ltl", _spy)
    result = backend._search_ltl(f, "A -> eventually ZZZ", SearchConfig())
    assert result.total_matches == 0
    # O(n): exactly one right-regex probe per line (the backward pass). The old nested scan
    # was ~n*(n-1)/2 right probes (~80k for n=400) — a hang vector on large files.
    assert calls["right"] <= n + 1


# --- scan_guardrails: a huge max_depth no longer rubber-stamps a hostile-root scan ---


def test_is_bounded_depth_rejects_rubber_stamp_values() -> None:
    assert _is_bounded_depth(0) is True  # depth 0 = the given dir only, genuinely bounded
    assert _is_bounded_depth(3) is True
    assert _is_bounded_depth(50) is True
    assert _is_bounded_depth(51) is False  # over the reasonable threshold
    assert _is_bounded_depth(1_000_000) is False
    assert _is_bounded_depth(None) is False


def test_has_scan_bound_ignores_huge_depth_but_honors_glob() -> None:
    # TODAY (pre-fix): max_depth=1_000_000 -> True (the bug). After: False.
    assert _has_scan_bound(globs=None, file_types=None, max_depth=1_000_000) is False
    assert _has_scan_bound(globs=None, file_types=None, max_depth=None) is False
    assert _has_scan_bound(globs=None, file_types=None, max_depth=3) is True
    # a glob is still a genuine bound regardless of depth.
    assert _has_scan_bound(globs=["*.py"], file_types=None, max_depth=1_000_000) is True

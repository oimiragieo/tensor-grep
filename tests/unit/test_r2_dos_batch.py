"""Round-2 DoS/hardening batch (#26):
1. CPUBackend._search_ltl was O(n^2) — a `A -> eventually B` query where A matches often and
   B rarely re-scanned to EOF per left hit. Now a single backward pass makes it O(n).
2. scan_guardrails treated ANY non-None max_depth as a sufficient traversal bound, so
   `--max-depth 1000000` rubber-stamped a hostile-root scan (defeating the broad-scan refusal).
"""

from __future__ import annotations

import types
from pathlib import Path
from unittest.mock import patch

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


def test_ltl_right_scan_is_linear_not_quadratic(tmp_path: Path) -> None:
    # Audit #6/#16 (ReDoS gate bypass): --ltl no longer probes `right_regex.search()` per line
    # at all -- both sub-expressions are resolved to a match-SET via exactly ONE call each to
    # the linear-time Rust engine, then the existing O(n) backward pass (see `_search_ltl`)
    # does pure set-membership lookups. This is a STRONGER guarantee than "not quadratic": the
    # old nested per-left-match rescan (~n*(n-1)/2 probes for n=400, a hang vector on large
    # files) is now architecturally impossible, since there is no per-line regex re-scan.
    n = 400
    f = tmp_path / "patho.txt"
    f.write_text("A\n" * n, encoding="utf-8")  # every line matches left, none matches right
    backend = CPUBackend()

    rust_mod = types.ModuleType("tensor_grep.rust_core")
    call_counts: dict[str, int] = {}

    class CountingRustBackend:
        def search(self, **kwargs):
            call_counts[kwargs["pattern"]] = call_counts.get(kwargs["pattern"], 0) + 1
            if kwargs["pattern"] == "A":
                return [(i, "A") for i in range(1, n + 1)]
            return []

    rust_mod.RustBackend = CountingRustBackend

    with patch.dict("sys.modules", {"tensor_grep.rust_core": rust_mod}):
        result = backend._search_ltl(f, "A -> eventually ZZZ", SearchConfig())

    assert result.total_matches == 0
    # Exactly one Rust call per sub-expression (not once per left-match).
    assert call_counts == {"A": 1, "ZZZ": 1}


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

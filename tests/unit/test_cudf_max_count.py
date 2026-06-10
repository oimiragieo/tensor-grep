"""CuDFBackend must honor --max-count like every other backend (audit B1).

The GPU search paths returned ALL matches regardless of ``config.max_count``, silently
inflating ``total_matches`` and breaking ripgrep parity. ``_cap_to_max_count`` caps the
result by line order (matching ripgrep ``-m``). This tests the cap logic directly (the
GPU search itself needs CUDA/cuDF and is not exercised here).
"""

from __future__ import annotations

from tensor_grep.backends.cudf_backend import CuDFBackend
from tensor_grep.core.config import SearchConfig
from tensor_grep.core.result import MatchLine, SearchResult


def _result(line_numbers: list[int]) -> SearchResult:
    matches = [MatchLine(line_number=n, text=f"m{n}", file="f.txt") for n in line_numbers]
    return SearchResult(matches=matches, total_files=1, total_matches=len(matches))


def test_cap_truncates_to_max_count_in_line_order() -> None:
    result = _result([5, 1, 9, 3, 7])  # deliberately unsorted
    capped = CuDFBackend._cap_to_max_count(result, SearchConfig(max_count=2))
    assert capped.total_matches == 2
    assert [m.line_number for m in capped.matches] == [1, 3]


def test_cap_noop_when_under_limit() -> None:
    result = _result([1, 2])
    capped = CuDFBackend._cap_to_max_count(result, SearchConfig(max_count=5))
    assert capped.total_matches == 2
    assert [m.line_number for m in capped.matches] == [1, 2]


def test_cap_noop_when_no_max_count() -> None:
    result = _result([1, 2, 3])
    capped = CuDFBackend._cap_to_max_count(result, SearchConfig())
    assert capped is result
    assert capped.total_matches == 3


def test_cap_noop_when_config_none() -> None:
    result = _result([1, 2, 3])
    capped = CuDFBackend._cap_to_max_count(result, None)
    assert capped.total_matches == 3

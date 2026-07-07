"""L7: rg --count / -l must recover partial results on a timeout, not hard-crash.

`RipgrepBackend.search()` already recovers whatever rg flushed before a `subprocess.TimeoutExpired`
(returns partial + `result_incomplete`). The `_search_counts` / `_search_files_with_matches` paths
used to let the timeout propagate through the generic `except Exception -> raise RuntimeError`, so a
`tg search --count` / `tg search -l` on a huge tree crashed instead of the graceful exit-2 partial UX.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from tensor_grep.backends import ripgrep_backend as rb
from tensor_grep.backends.ripgrep_backend import RipgrepBackend
from tensor_grep.core.config import SearchConfig


def test_search_counts_recovers_partial_tally_on_timeout(monkeypatch, tmp_path: Path) -> None:
    def _timeout(cmd, **kwargs):  # type: ignore[no-untyped-def]
        raise subprocess.TimeoutExpired(cmd, timeout=1, output="src/a.py:3\nsrc/b.py:2\n")

    monkeypatch.setattr(rb, "run_subprocess", _timeout)
    result = RipgrepBackend()._search_counts(str(tmp_path), "x", SearchConfig())

    # Recovered, not raised: the two flushed per-file counts are tallied.
    assert result.result_incomplete is True
    assert result.incomplete_reason is not None and "timed out" in result.incomplete_reason
    assert result.total_matches == 5
    assert result.total_files == 2
    assert result.match_counts_by_file == {"src/a.py": 3, "src/b.py": 2}
    assert result.routing_backend == "RipgrepBackend"


def test_search_files_with_matches_recovers_partial_list_on_timeout(
    monkeypatch, tmp_path: Path
) -> None:
    def _timeout(cmd, **kwargs):  # type: ignore[no-untyped-def]
        raise subprocess.TimeoutExpired(cmd, timeout=1, output="src/a.py\nsrc/b.py\n")

    monkeypatch.setattr(rb, "run_subprocess", _timeout)
    result = RipgrepBackend()._search_files_with_matches(str(tmp_path), "x", SearchConfig())

    assert result.result_incomplete is True
    assert result.incomplete_reason is not None and "timed out" in result.incomplete_reason
    assert result.matched_file_paths == ["src/a.py", "src/b.py"]
    assert result.total_matches == 2
    assert result.routing_backend == "RipgrepBackend"


def test_search_counts_timeout_with_no_flushed_output_is_empty_not_crash(
    monkeypatch, tmp_path: Path
) -> None:
    def _timeout(cmd, **kwargs):  # type: ignore[no-untyped-def]
        raise subprocess.TimeoutExpired(cmd, timeout=1, output=None)

    monkeypatch.setattr(rb, "run_subprocess", _timeout)
    result = RipgrepBackend()._search_counts(str(tmp_path), "x", SearchConfig())

    assert result.result_incomplete is True
    assert result.total_matches == 0

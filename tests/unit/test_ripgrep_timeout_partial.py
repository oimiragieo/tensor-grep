"""L7: rg --count / -l must recover partial results on a timeout, not hard-crash.

`RipgrepBackend.search()` already recovers whatever rg flushed before a `subprocess.TimeoutExpired`
(returns partial + `result_incomplete`). The `_search_counts` / `_search_files_with_matches` paths
used to let the timeout propagate through the generic `except Exception -> raise RuntimeError`, so a
`tg search --count` / `tg search -l` on a huge tree crashed instead of the graceful exit-2 partial UX.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tensor_grep.backends import ripgrep_backend as rb
from tensor_grep.backends.ripgrep_backend import RipgrepBackend
from tensor_grep.core.config import SearchConfig


@pytest.fixture(autouse=True)
def _mock_rg_binary(monkeypatch):
    # rg need not be installed on the CI runner for these unit tests: mock binary resolution so
    # _build_cmd succeeds and the (monkeypatched) run_subprocess raises the TimeoutExpired whose
    # recovery is under test. Without this, _build_cmd raises "requires the 'rg' binary" on a clean
    # runner before the timeout path runs (the resolve-a-real-binary-via-PATH CI false-fail class).
    monkeypatch.setattr(RipgrepBackend, "_get_binary_name", lambda self: "rg")


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


def test_search_files_with_matches_discards_unterminated_timeout_fragment(
    monkeypatch, tmp_path: Path
) -> None:
    def _timeout(cmd, **kwargs):  # type: ignore[no-untyped-def]
        raise subprocess.TimeoutExpired(cmd, timeout=1, output=b"complete\0torn")

    monkeypatch.setattr(rb, "run_subprocess", _timeout)
    result = RipgrepBackend()._search_files_with_matches(
        str(tmp_path), "x", SearchConfig(null=True)
    )
    assert result.matched_file_paths == ["complete"]
    assert result.result_incomplete is True


def test_ndjson_timeout_keeps_complete_records_before_torn_invalid_utf8(
    monkeypatch, tmp_path: Path
) -> None:
    import json

    record = {
        "type": "match",
        "data": {
            "path": {"text": "src/a.py"},
            "lines": {"text": "needle\n"},
            "line_number": 1,
            "submatches": [],
        },
    }

    def _timeout(cmd, **kwargs):  # type: ignore[no-untyped-def]
        raise subprocess.TimeoutExpired(
            cmd, timeout=1, output=json.dumps(record).encode() + b"\n{\xff"
        )

    monkeypatch.setattr(rb, "run_subprocess", _timeout)
    result = RipgrepBackend().search(str(tmp_path), "needle", SearchConfig())
    assert result.result_incomplete is True
    assert result.total_matches == 1
    assert result.matches[0].file == "src/a.py"


def test_search_counts_timeout_with_no_flushed_output_is_empty_not_crash(
    monkeypatch, tmp_path: Path
) -> None:
    def _timeout(cmd, **kwargs):  # type: ignore[no-untyped-def]
        raise subprocess.TimeoutExpired(cmd, timeout=1, output=None)

    monkeypatch.setattr(rb, "run_subprocess", _timeout)
    result = RipgrepBackend()._search_counts(str(tmp_path), "x", SearchConfig())

    assert result.result_incomplete is True
    assert result.total_matches == 0


@pytest.mark.parametrize(
    ("config", "file_path", "output"),
    [
        (
            SearchConfig(count=True, null=True),
            ["one", "two"],
            b"one\0 2\ntwo\0 12",
        ),
        (SearchConfig(count=True), ["one", "two"], b"one:2\ntwo:12"),
    ],
)
def test_count_timeout_discards_unterminated_final_record(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    config: SearchConfig,
    file_path: list[str],
    output: bytes,
) -> None:
    def _timeout(cmd, **kwargs):  # type: ignore[no-untyped-def]
        raise subprocess.TimeoutExpired(cmd, timeout=1, output=output)

    monkeypatch.setattr(rb, "run_subprocess", _timeout)
    result = RipgrepBackend()._search_counts(file_path, "x", config)

    assert result.total_matches == 2
    assert result.match_counts_by_file == {"one": 2}

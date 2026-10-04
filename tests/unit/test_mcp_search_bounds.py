"""MCP search output bounds: per-match windows, byte caps, final-envelope budget, lazy rendering,
and the AST pattern preflight that rides on the same tools.

Split out of test_mcp_server_search.py (file-size ratchet)."""

import contextlib
import json
from unittest.mock import MagicMock, patch

import pytest

from tensor_grep.core.result import MatchLine, SearchResult


@contextlib.contextmanager
def _stub_rg_search(matches):
    """Run ``tg_search`` over a stubbed ripgrep backend returning ``matches``."""
    from tensor_grep.backends.ripgrep_backend import RipgrepBackend

    backend = RipgrepBackend()
    files = sorted({m.file for m in matches}) or ["min.js"]
    backend.search = MagicMock(
        return_value=SearchResult(
            matches=matches,
            matched_file_paths=files,
            total_files=len(files),
            total_matches=len(matches),
            routing_backend="RipgrepBackend",
            routing_reason="rg_json",
        )
    )
    pipeline_patch = patch("tensor_grep.cli.mcp_server.Pipeline")
    scanner_patch = patch("tensor_grep.cli.mcp_server.DirectoryScanner")
    with pipeline_patch as mp, scanner_patch as ms:
        p = mp.return_value
        p.get_backend.return_value = backend
        p.selected_backend_name = "RipgrepBackend"
        p.selected_backend_reason = "rg_json"
        p.selected_gpu_device_ids = []
        p.selected_gpu_chunk_plan_mb = []
        ms.return_value.walk.return_value = files
        yield p


def _run_tg_search_with_line(line, start_byte):
    from tensor_grep.cli import mcp_server

    hit = MatchLine(
        line_number=1,
        text=line,
        file="min.js",
        submatches=({"match": {"text": "NEEDLE"}, "start": start_byte, "end": start_byte + 6},),
    )
    with _stub_rg_search([hit]):
        return mcp_server.tg_search("NEEDLE", ".")


def _run_tg_search_many(count, text, **kwargs):
    from tensor_grep.cli import mcp_server

    hits = [MatchLine(line_number=i + 1, text=text, file="big.txt") for i in range(count)]
    with _stub_rg_search(hits):
        return mcp_server.tg_search("NEEDLE", ".", max_results=5000, **kwargs)


def _run_tg_ast_search_many(count, text, structured_json):
    from tensor_grep.cli import mcp_server

    hits = [MatchLine(line_number=i + 1, text=text, file="a.py") for i in range(count)]
    fake = type(
        "AstGrepWrapperBackend",
        (),
        {
            "search": MagicMock(
                return_value=SearchResult(
                    matches=hits,
                    matched_file_paths=["a.py"],
                    total_files=1,
                    total_matches=count,
                    routing_backend="AstGrepWrapperBackend",
                    routing_reason="ast",
                )
            )
        },
    )()
    with (
        patch("tensor_grep.cli.mcp_server.Pipeline") as mp,
        patch("tensor_grep.cli.mcp_server.DirectoryScanner") as ms,
    ):
        mp.return_value.get_backend.return_value = fake
        ms.return_value.walk.return_value = ["a.py"]
        return mcp_server.tg_ast_search("$A", "python", ".", structured_json=structured_json)


def test_tg_search_bounds_minified_line_width_and_keeps_the_match():
    line = ("a" * 1_500_000) + "NEEDLE" + ("b" * 1_500_000)
    out = _run_tg_search_with_line(line, 1_500_000)
    assert len(out) < 20_000
    row = json.loads(out)["matches"][0]
    assert row["text_truncated"] is True
    assert row["text_chars"] == len(line)
    assert "NEEDLE" in row["text"]
    assert len(row["text"]) <= 400


def test_tg_search_window_centres_on_match_after_multibyte_prefix():
    prefix = "é" * 2000  # 4000 bytes, 2000 chars
    line = prefix + "NEEDLE" + ("b" * 2000)
    row = json.loads(_run_tg_search_with_line(line, len(prefix.encode("utf-8"))))["matches"][0]
    assert "NEEDLE" in row["text"]


def test_tg_search_total_match_bytes_cap_sets_truncated():
    from tensor_grep.cli import mcp_search_bounds

    rows = [{"file": "f", "line_number": i, "text": "x" * 400} for i in range(2000)]
    kept, capped = mcp_search_bounds._cap_match_rows(rows)
    assert capped is True
    assert len(kept) < len(rows)
    short = [{"file": "f", "line_number": 1, "text": "ok"}]
    assert mcp_search_bounds._cap_match_rows(short) == (short, False)


def test_tg_search_json_output_is_byte_capped_and_says_so():

    out = _run_tg_search_many(2000, "x" * 400)
    assert len(out.encode("utf-8")) <= 262144 + 8192
    payload = json.loads(out)
    assert payload["output_truncated"] is True
    assert payload["truncated"] is True
    assert len(payload["matches"]) < 2000
    assert payload["rendered_match_count"] == len(payload["matches"])
    assert payload["omitted_matches"] == 2000 - len(payload["matches"])


def test_tg_search_plain_text_output_is_byte_capped_with_ascii_notice():

    out = _run_tg_search_many(2000, "x" * 400, structured_json=False)
    assert len(out.encode("utf-8")) <= 262144 + 8192
    assert "output truncated at 262144 bytes" in out
    out.encode("ascii")  # the notice must stay ASCII (CLI output law)


def test_tg_search_plain_text_cap_counts_bytes_not_chars():

    out = _run_tg_search_many(2000, "é" * 400, structured_json=False)
    assert len(out.encode("utf-8")) <= 262144 + 8192
    assert "output truncated at 262144 bytes" in out


def test_tg_search_short_matches_are_not_capped_or_truncated():
    out = _run_tg_search_many(10, "short line")
    payload = json.loads(out)
    assert not payload.get("output_truncated")
    assert len(payload["matches"]) == 10
    assert all("text_truncated" not in row for row in payload["matches"])
    plain = _run_tg_search_many(10, "short line", structured_json=False)
    assert "output truncated at" not in plain


def test_tg_ast_search_json_output_is_byte_capped_and_says_so():

    # 150 rows (the tool's hard rendered limit) of 400 two-byte chars: ~2,400 rendered bytes each
    # under ensure_ascii, ~360 KB in total, so the cap MUST fire.
    out = _run_tg_ast_search_many(150, "é" * 400, structured_json=True)
    assert len(out.encode("utf-8")) <= 262144 + 8192
    payload = json.loads(out)
    assert payload["output_truncated"] is True
    assert len(payload["matches"]) < 150


def test_tg_ast_search_plain_text_truncates_each_line_and_stays_bounded():
    """Plain text renders at most 15 files x 10 matches, so the cumulative byte cap is defence in
    depth the current limits cannot reach; assert the per-line bound and the byte bound only."""

    out = _run_tg_ast_search_many(10, "z" * 5000, structured_json=False)
    assert len(out.encode("utf-8")) <= 262144 + 8192
    assert max(len(line) for line in out.splitlines()) < 500
    short = _run_tg_ast_search_many(3, "ok", structured_json=False)
    assert "output truncated at" not in short
    assert "  1: ok" in short


def _real_ast_search(tmp_path, monkeypatch, pattern):
    import pytest

    from tensor_grep.backends.ast_wrapper_backend import AstGrepWrapperBackend
    from tensor_grep.cli import mcp_server

    if not AstGrepWrapperBackend().is_available():
        pytest.skip("ast-grep binary not installed")
    monkeypatch.chdir(tmp_path)
    (tmp_path / "m.py").write_text("def f():\n    pass\n", encoding="utf-8")
    return json.loads(mcp_server.tg_ast_search(pattern, "python", ".", structured_json=True))


def test_real_backend_multiline_malformed_pattern_with_zero_matches_is_invalid_input(
    tmp_path, monkeypatch
):
    out = _real_ast_search(tmp_path, monkeypatch, "def (\n    pass")
    assert out["error"]["code"] == "invalid_input"
    assert "multiline" in out["error"]["message"] or "ERROR node" in out["error"]["message"]
    assert str(tmp_path) not in out["error"]["message"]


def test_real_backend_valid_multiline_pattern_with_matches_is_returned_normally(
    tmp_path, monkeypatch
):
    out = _real_ast_search(tmp_path, monkeypatch, "def $A():\n    pass")
    assert "error" not in out
    assert out["total_matches"] == 1


# --- Codex round 1, finding 2: the FINAL serialized response is bounded, not just matches[] ---

_BIG_PATTERN = "(?x)#" + "a" * 1_000_000 + "\nNEEDLE"
_FINAL_BUDGET = 262144 + 8192


def _assert_bounded(out):
    assert len(out.encode("utf-8")) <= _FINAL_BUDGET, len(out.encode("utf-8"))


def _run_tg_search_big_pattern(matches, **kwargs):
    from tensor_grep.cli import mcp_server

    with _stub_rg_search(matches):
        return mcp_server.tg_search(_BIG_PATTERN, ".", **kwargs)


def test_tg_search_json_with_an_oversized_pattern_is_bounded_and_flagged():
    hit = MatchLine(line_number=1, text="NEEDLE", file="a.txt")
    for matches in ([hit], []):
        out = _run_tg_search_big_pattern(matches)
        _assert_bounded(out)
        payload = json.loads(out)
        assert payload["output_truncated"] is True
        assert payload["pattern_truncated"] is True
        assert payload["pattern"].startswith("(?x)#aaaa")
        assert len(payload["pattern"]) <= 1024


def test_tg_search_plain_text_with_an_oversized_pattern_is_bounded():
    for matches in ([MatchLine(line_number=1, text="NEEDLE", file="a.txt")], []):
        out = _run_tg_search_big_pattern(matches, structured_json=False)
        _assert_bounded(out)
        assert "a" * 5000 not in out


def test_tg_search_error_response_with_an_oversized_pattern_is_bounded(tmp_path, monkeypatch):
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    outside = str(tmp_path.parent)
    out = mcp_server.tg_search(_BIG_PATTERN, outside)
    _assert_bounded(out)
    payload = json.loads(out)
    assert payload["error"]["code"] == "invalid_input"
    assert payload["output_truncated"] is True
    assert payload["pattern_truncated"] is True


def _run_tg_ast_search_pattern(pattern, hits, warning, structured_json):
    from tensor_grep.cli import mcp_server

    fake = type(
        "AstGrepWrapperBackend",
        (),
        {
            "search": MagicMock(
                return_value=SearchResult(
                    matches=hits,
                    matched_file_paths=["a.py"] if hits else [],
                    total_files=1 if hits else 0,
                    total_matches=len(hits),
                    routing_backend="AstGrepWrapperBackend",
                    routing_reason="ast",
                )
            ),
            "pattern_warning": MagicMock(return_value=warning),
        },
    )()
    with (
        patch("tensor_grep.cli.mcp_server.Pipeline") as mp,
        patch("tensor_grep.cli.mcp_server.DirectoryScanner") as ms,
    ):
        mp.return_value.get_backend.return_value = fake
        ms.return_value.walk.return_value = ["a.py"]
        return mcp_server.tg_ast_search(pattern, "python", ".", structured_json=structured_json)


def test_tg_ast_search_json_with_an_oversized_pattern_is_bounded_and_flagged():
    hit = MatchLine(line_number=1, text="x", file="a.py")
    for hits in ([hit], []):
        out = _run_tg_ast_search_pattern(_BIG_PATTERN, hits, None, True)
        _assert_bounded(out)
        payload = json.loads(out)
        assert payload["output_truncated"] is True
        assert payload["pattern_truncated"] is True


def test_tg_ast_search_plain_text_with_an_oversized_pattern_is_bounded():
    hit = MatchLine(line_number=1, text="x", file="a.py")
    for hits in ([hit], []):
        out = _run_tg_ast_search_pattern(_BIG_PATTERN, hits, None, False)
        _assert_bounded(out)


def test_tg_ast_search_invalid_pattern_error_with_oversized_echo_is_bounded():
    warning = "Warning: Pattern contains an ERROR node " + "w" * 1_000_000
    out = _run_tg_ast_search_pattern(_BIG_PATTERN, [], warning, True)
    _assert_bounded(out)
    payload = json.loads(out)
    assert payload["error"]["code"] == "invalid_input"
    assert payload["output_truncated"] is True
    assert payload["pattern_truncated"] is True
    assert payload["error"]["message_truncated"] is True
    plain = _run_tg_ast_search_pattern(_BIG_PATTERN, [], warning, False)
    _assert_bounded(plain)


def test_small_responses_are_returned_untouched_by_the_final_bound():
    out = _run_tg_search_many(10, "short line")
    payload = json.loads(out)
    assert "pattern_truncated" not in payload
    assert not payload.get("output_truncated")


def test_final_row_trim_recomputes_file_and_omission_counters_exactly():
    from tensor_grep.cli import mcp_server

    hits = [MatchLine(line_number=1, text="x" * 400, file=f"f{i}.txt") for i in range(1000)]
    big_pattern = "\U0001f600" * 1024  # 1024 chars, ~12 KB escaped: not clipped, eats the budget
    with _stub_rg_search(hits):
        out = mcp_server.tg_search(big_pattern, ".", max_results=5000, max_files=5000)
    _assert_bounded(out)
    payload = json.loads(out)
    kept = payload["matches"]
    assert payload["output_truncated"] is True
    assert 0 < len(kept) < 1000
    assert payload["rendered_match_count"] == len(kept)
    assert payload["rendered_file_count"] == len({row["file"] for row in kept})
    assert payload["omitted_matches"] == 1000 - len(kept)
    assert payload["omitted_files"] == 1000 - len({row["file"] for row in kept})
    assert payload["truncated"] is True
    assert payload["incomplete"]["status"] is True
    assert payload["incomplete"]["cause"] == "truncated"


# --- Codex round 7: per-match truncation must keep the match or say so; no empty windows ---


def _plain_search_one_line(line, start_byte):
    from tensor_grep.cli import mcp_server

    hit = MatchLine(
        line_number=1,
        text=line,
        file="min.js",
        submatches=({"match": {"text": "NEEDLE"}, "start": start_byte, "end": start_byte + 6},),
    )
    with _stub_rg_search([hit]):
        return mcp_server.tg_search("NEEDLE", ".", structured_json=False)


def test_plain_text_clipped_row_keeps_the_match_and_says_it_was_truncated():
    out = _plain_search_one_line("a" * 1500 + "NEEDLE" + "b" * 300, 1500)
    assert "NEEDLE" in out
    assert "[truncated" in out
    out.encode("ascii")  # the marker stays ASCII


def test_plain_text_clipped_row_without_offsets_carries_the_marker():
    out = _run_tg_ast_search_many(1, "z" * 1000, False)
    assert "[truncated" in out
    assert "z" * 400 in out
    assert "z" * 1000 not in out


def test_short_rows_are_unchanged_in_plain_text_and_json():
    plain = _plain_search_one_line("short NEEDLE line", 6)
    assert "  1: short NEEDLE line" in plain
    assert "[truncated" not in plain
    row = json.loads(_run_tg_search_with_line("short NEEDLE line", 6))["matches"][0]
    assert row["text"] == "short NEEDLE line"
    assert "text_truncated" not in row


def test_json_window_without_offsets_uses_the_stripped_text_not_an_empty_slice():
    from tensor_grep.cli import mcp_server

    hit = MatchLine(line_number=1, text=" " * 1000 + "NEEDLE" + "b" * 500, file="a.txt")
    with _stub_rg_search([hit]):
        row = json.loads(mcp_server.tg_search("NEEDLE", "."))["matches"][0]
    assert row["text"] != ""
    assert "NEEDLE" in row["text"]
    assert row["text_truncated"] is True
    assert len(row["text"]) <= 400


def test_json_window_with_offsets_and_leading_whitespace_keeps_the_match():
    line = " " * 1000 + "NEEDLE" + "b" * 500
    row = json.loads(_run_tg_search_with_line(line, 1000))["matches"][0]
    assert "NEEDLE" in row["text"]
    assert row["text_truncated"] is True
    assert row["text_chars"] == len(line)


# --- Codex round 8: a whitespace-only first submatch must not blank the window ---


def _rg_hit(line, spans):
    return MatchLine(
        line_number=1,
        text=line,
        file="a.txt",
        submatches=tuple({"match": {"text": "x"}, "start": a, "end": b} for a, b in spans),
    )


_WS_LINE = " " * 1000 + "NEEDLE" + "b" * 500


def test_whitespace_first_submatch_still_shows_the_later_real_match_in_json_and_plain():
    from tensor_grep.cli import mcp_server

    hit = _rg_hit(_WS_LINE, [(0, 1000), (1000, 1006)])  # regex ` +|NEEDLE`
    with _stub_rg_search([hit]):
        row = json.loads(mcp_server.tg_search(" +|NEEDLE", "."))["matches"][0]
    assert "NEEDLE" in row["text"]
    assert row["text_truncated"] is True
    with _stub_rg_search([hit]):
        plain = mcp_server.tg_search(" +|NEEDLE", ".", structured_json=False)
    assert "NEEDLE" in plain
    assert "[truncated" in plain


def test_only_whitespace_submatches_fall_back_to_the_first_visible_character():
    from tensor_grep.cli import mcp_server

    hit = _rg_hit(_WS_LINE, [(0, 1000)])
    with _stub_rg_search([hit]):
        row = json.loads(mcp_server.tg_search(" +", "."))["matches"][0]
    assert "NEEDLE" in row["text"]
    assert row["text_truncated"] is True


def test_all_whitespace_wide_line_is_empty_honestly_and_flagged():
    from tensor_grep.cli import mcp_server

    hit = _rg_hit(" " * 2000, [(0, 2000)])
    with _stub_rg_search([hit]):
        row = json.loads(mcp_server.tg_search(" +", "."))["matches"][0]
    assert row["text"] == ""
    assert row["text_truncated"] is True
    assert row["text_chars"] == 2000
    with _stub_rg_search([hit]):
        plain = mcp_server.tg_search(" +", ".", structured_json=False)
    assert "[truncated 2000 chars]" in plain


def test_normal_single_submatch_window_is_unchanged():
    line = "a" * 1500 + "NEEDLE" + "b" * 300
    row = json.loads(_run_tg_search_with_line(line, 1500))["matches"][0]
    assert "NEEDLE" in row["text"]
    assert row["text_truncated"] is True
    assert row["text"].startswith("a" * 100 + "NEEDLE")


# --- Codex round 9: linear-time window anchor; pattern validity independent of scanned files ---


def test_window_anchor_over_128k_whitespace_submatches_is_linear_and_keeps_the_match(
    monkeypatch,
):
    import time

    from tensor_grep.cli import mcp_search_bounds

    calls = {"encode": 0}
    real_utf8 = getattr(mcp_search_bounds, "_utf8", lambda text: text.encode("utf-8"))

    def counting_utf8(text):
        calls["encode"] += 1
        return real_utf8(text)

    monkeypatch.setattr(mcp_search_bounds, "_utf8", counting_utf8, raising=False)
    spaces = 128_000
    line = " " * spaces + "NEEDLE" + "b" * 500
    subs = (
        *({"match": {"text": " "}, "start": i, "end": i + 1} for i in range(spaces)),
        {"match": {"text": "NEEDLE"}, "start": spaces, "end": spaces + 6},
    )
    hit = MatchLine(line_number=1, text=line, file="a.txt", submatches=subs)
    started = time.perf_counter()
    row = mcp_search_bounds._bounded_match_row("a.txt", hit)
    elapsed = time.perf_counter() - started
    assert "NEEDLE" in row["text"]
    assert row["text_truncated"] is True
    assert calls["encode"] <= 1  # the line is encoded at most once, not once per submatch
    assert elapsed < 1.0, elapsed  # was ~3.4 s when quadratic; generous bound for a loaded CI box


def test_window_anchor_normal_cases_are_unchanged_by_the_bounded_scan():
    from tensor_grep.cli import mcp_search_bounds

    line = "a" * 1500 + "NEEDLE" + "b" * 300
    hit = MatchLine(
        line_number=1,
        text=line,
        file="a.txt",
        submatches=({"match": {"text": "NEEDLE"}, "start": 1500, "end": 1506},),
    )
    row = mcp_search_bounds._bounded_match_row("a.txt", hit)
    assert row["text"].startswith("a" * 100 + "NEEDLE")
    # a later real submatch beyond the examined prefix still anchors via the visible-char fallback
    ws = " " * 3000 + "NEEDLE" + "b" * 500
    many = (*({"start": i, "end": i + 1} for i in range(3000)), {"start": 3000, "end": 3006})
    row2 = mcp_search_bounds._bounded_match_row(
        "a.txt", MatchLine(line_number=1, text=ws, file="a.txt", submatches=many)
    )
    assert "NEEDLE" in row2["text"]


def _real_ast_search_empty_dir(tmp_path, monkeypatch, pattern):
    import pytest

    from tensor_grep.backends.ast_wrapper_backend import AstGrepWrapperBackend
    from tensor_grep.cli import mcp_server

    if not AstGrepWrapperBackend().is_available():
        pytest.skip("ast-grep binary not installed")
    monkeypatch.chdir(tmp_path)
    return json.loads(mcp_server.tg_ast_search(pattern, "python", ".", structured_json=True))


def test_malformed_multiline_pattern_on_an_empty_directory_is_invalid_input(tmp_path, monkeypatch):
    out = _real_ast_search_empty_dir(tmp_path, monkeypatch, "def (\n    pass")
    assert out["error"]["code"] == "invalid_input"
    assert "ERROR node" in out["error"]["message"]


def test_valid_multiline_pattern_on_an_empty_directory_is_a_clean_empty_result(
    tmp_path, monkeypatch
):
    out = _real_ast_search_empty_dir(tmp_path, monkeypatch, "def $A():\n    pass")
    assert "error" not in out
    assert out["total_matches"] == 0
    assert out["result_incomplete"] is False


def test_malformed_single_line_pattern_on_an_empty_directory_still_invalid_input(
    tmp_path, monkeypatch
):
    out = _real_ast_search_empty_dir(tmp_path, monkeypatch, "def (")
    assert out["error"]["code"] == "invalid_input"


# --- Codex round 10: probe failures are not "valid"; Unicode whitespace is not "visible" ---


@pytest.mark.parametrize(("ws_char", "width"), [(chr(0x2003), 3), (chr(0x00A0), 2)])
def test_unicode_whitespace_submatch_does_not_hide_the_visible_match(ws_char, width):
    from tensor_grep.cli import mcp_server

    line = "x" + ws_char * 1000 + "NEEDLE" + "b" * 500
    ws_end = 1 + 1000 * width
    hit = _rg_hit(line, [(1, ws_end), (ws_end, ws_end + 6)])  # regex \s+|NEEDLE
    with _stub_rg_search([hit]):
        row = json.loads(mcp_server.tg_search(r"\s+|NEEDLE", "."))["matches"][0]
    assert "NEEDLE" in row["text"]
    assert row["text_truncated"] is True
    with _stub_rg_search([hit]):
        plain = mcp_server.tg_search(r"\s+|NEEDLE", ".", structured_json=False)
    assert "NEEDLE" in plain
    assert "[truncated" in plain


def test_probe_timeout_is_an_error_not_an_empty_success(tmp_path, monkeypatch):
    from tensor_grep.backends.ast_wrapper_backend import AstGrepWrapperBackend
    from tensor_grep.backends.base import BackendExecutionError
    from tensor_grep.cli import mcp_server

    if not AstGrepWrapperBackend().is_available():
        pytest.skip("ast-grep binary not installed")
    monkeypatch.chdir(tmp_path)

    def timed_out(self, cmd, *, input_text=None):
        raise BackendExecutionError("ast-grep command timed out after 1s")

    monkeypatch.setattr(AstGrepWrapperBackend, "_run_ast_grep_command", timed_out)
    out = json.loads(mcp_server.tg_ast_search("def $A():", "python", ".", structured_json=True))
    assert "error" in out
    assert out.get("total_matches") != 0 or "error" in out
    assert out["error"]["code"] != "invalid_input"


def test_empty_pattern_on_an_empty_directory_is_invalid_input(tmp_path, monkeypatch):
    out = _real_ast_search_empty_dir(tmp_path, monkeypatch, "")
    assert out["error"]["code"] == "invalid_input"


def test_valid_pattern_on_an_empty_directory_is_still_a_clean_empty_result(tmp_path, monkeypatch):
    out = _real_ast_search_empty_dir(tmp_path, monkeypatch, "zzz($A)")
    assert "error" not in out
    assert out["total_matches"] == 0
    assert out["result_incomplete"] is False


# --- Codex round 11: the final trim must keep the completeness envelope self-consistent ---


def test_final_envelope_trim_alone_reports_the_response_as_incomplete():
    from tensor_grep.cli import mcp_server

    hits = [MatchLine(line_number=i + 1, text="x" * 400, file="f") for i in range(548)]
    with _stub_rg_search(hits):
        out = mcp_server.tg_search(chr(0x1F600) * 1024, ".", max_results=548)
    _assert_bounded(out)
    payload = json.loads(out)
    kept = payload["matches"]
    assert 0 < len(kept) < 548
    assert payload["output_truncated"] is True
    assert payload["truncated"] is True
    assert payload["omitted_matches"] == 548 - len(kept)
    assert payload["incomplete"]["status"] is True
    assert payload["incomplete"]["cause"] == "truncated"
    assert payload["incomplete"]["budget_remediable"] is False


def test_trimmed_response_incomplete_envelope_matches_what_the_stamping_helper_derives():
    from tensor_grep.cli.incompleteness import unified_incomplete_envelope

    hits = [MatchLine(line_number=1, text="x" * 400, file=f"f{i}.txt") for i in range(1000)]
    from tensor_grep.cli import mcp_server

    with _stub_rg_search(hits):
        out = mcp_server.tg_search(chr(0x1F600) * 1024, ".", max_results=5000, max_files=5000)
    payload = json.loads(out)
    assert payload["incomplete"] == unified_incomplete_envelope(payload)
    assert payload["incomplete"]["status"] is True


def test_untrimmed_response_keeps_a_clear_incomplete_envelope():
    payload = json.loads(_run_tg_search_many(10, "short line"))
    assert "output_truncated" not in payload
    assert payload["incomplete"]["status"] is False
    assert payload["incomplete"]["cause"] is None


# --- Codex round 12: the byte cap must bound the RENDERING work, not just the output ---


def _count_calls(monkeypatch, name):
    from tensor_grep.cli import mcp_search_bounds

    calls = {"n": 0}
    real = getattr(mcp_search_bounds, name)

    def counting(*args, **kwargs):
        calls["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(mcp_search_bounds, name, counting)
    return calls


def test_json_rendering_work_is_bounded_by_the_retained_rows_not_by_max_results(monkeypatch):
    from tensor_grep.cli import mcp_server
    from tensor_grep.cli.incompleteness import unified_incomplete_envelope

    calls = _count_calls(monkeypatch, "_bounded_match_row")
    hits = [MatchLine(line_number=i + 1, text="x" * 400, file="f") for i in range(20_000)]
    with _stub_rg_search(hits):
        out = mcp_server.tg_search("x", ".", max_results=20_000)
    payload = json.loads(out)
    kept = payload["matches"]
    assert 0 < len(kept) < 20_000
    assert calls["n"] <= len(kept) + 1, calls["n"]  # not 20,000 render calls
    assert len(out.encode("utf-8")) <= 262144 + 8192
    assert payload["rendered_match_count"] == len(kept)
    assert payload["rendered_file_count"] == 1
    assert payload["omitted_matches"] == 20_000 - len(kept)
    assert payload["omitted_files"] == 0
    assert payload["truncated"] is True
    assert payload["output_truncated"] is True
    assert payload["incomplete"] == unified_incomplete_envelope(payload)
    assert payload["incomplete"]["status"] is True


def test_plain_text_rendering_work_is_bounded_by_the_retained_rows(monkeypatch):
    from tensor_grep.cli import mcp_server

    calls = _count_calls(monkeypatch, "_plain_match_text")
    hits = [MatchLine(line_number=i + 1, text="x" * 400, file="f") for i in range(20_000)]
    with _stub_rg_search(hits):
        out = mcp_server.tg_search("x", ".", max_results=20_000, structured_json=False)
    retained = sum(1 for line in out.splitlines() if line.startswith("  ") and ": x" in line)
    assert 0 < retained < 20_000
    assert calls["n"] <= retained + 1, calls["n"]
    assert len(out.encode("utf-8")) <= 262144 + 8192
    assert "output truncated" in out


def test_max_results_far_above_the_ceiling_stops_at_the_ceiling(monkeypatch):
    from tensor_grep.cli import mcp_search_bounds, mcp_server

    ceiling = mcp_search_bounds._MCP_MAX_RENDERED_ROWS
    calls = _count_calls(monkeypatch, "_bounded_match_row")
    hits = [MatchLine(line_number=i + 1, text="x", file="f") for i in range(ceiling + 3000)]
    with _stub_rg_search(hits):
        payload = json.loads(mcp_server.tg_search("x", ".", max_results=10**9))
    assert len(payload["matches"]) <= ceiling
    assert calls["n"] <= ceiling + 1
    assert payload["omitted_matches"] == len(hits) - len(payload["matches"])
    assert payload["truncated"] is True
    assert payload["output_truncated"] is True
    assert payload["incomplete"]["status"] is True


def test_small_results_are_unchanged_by_lazy_rendering(monkeypatch):
    calls = _count_calls(monkeypatch, "_bounded_match_row")
    payload = json.loads(_run_tg_search_many(10, "short line"))
    assert len(payload["matches"]) == 10
    assert calls["n"] == 10
    assert payload["rendered_match_count"] == 10
    assert payload["omitted_matches"] == 0
    assert "output_truncated" not in payload
    assert payload["incomplete"]["status"] is False
    plain = _run_tg_search_many(10, "short line", structured_json=False)
    assert plain.count("short line") == 10
    assert "output truncated" not in plain

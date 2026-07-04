"""tg context --max-tokens: bound the pack so it's prompt-injection-ready (dogfood v1.19.9: an
unbounded pack ballooned to >1MB). File-driven + coherent (each retained file keeps its symbols)."""

import json

from tensor_grep.cli.repo_map import _apply_context_token_budget, _estimate_payload_tokens


def _big_payload(n: int = 60, symbols_per_file: int = 20) -> dict:
    files = [f"src/f{i}.py" for i in range(n)]
    symbols = [
        {"name": f"s{i}_{j}", "kind": "function", "file": f"src/f{i}.py", "score": n - i}
        for i in range(n)
        for j in range(symbols_per_file)
    ]
    return {
        "files": list(files),
        "symbols": symbols,
        "imports": [{"file": f, "imports": []} for f in files],
        "tests": [],
        "related_paths": list(files),
        "file_matches": [],
        "test_matches": [],
        "query": "q",
        "path": ".",
    }


def test_budget_bounds_the_pack_and_marks_truncated():
    payload = _big_payload(60)
    full_tokens = _estimate_payload_tokens(payload)
    capped = _apply_context_token_budget(payload, max_tokens=2000)
    assert capped["token_budget"]["truncated"] is True
    assert capped["token_budget"]["estimated_tokens"] < full_tokens
    assert len(capped["files"]) < 60  # actually bounded


def test_budget_keeps_symbols_coherent_with_files():
    # File-driven trim: every retained symbol's file must still be in the (trimmed) files list.
    capped = _apply_context_token_budget(_big_payload(60), max_tokens=2000)
    files = set(capped["files"])
    assert all(str(s["file"]) in files for s in capped["symbols"])
    assert capped["symbols"], "a bounded pack should still carry the top file's symbols, not zero"


def test_budget_opt_out_is_a_noop():
    payload = _big_payload(60)
    for max_tokens in (None, 0, -1):
        result = _apply_context_token_budget(payload, max_tokens=max_tokens)
        assert result is payload  # unbounded opt-out: returned unchanged


def test_under_budget_marks_not_truncated():
    payload = {"files": ["a.py"], "symbols": [{"name": "x", "kind": "fn", "file": "a.py"}]}
    capped = _apply_context_token_budget(payload, max_tokens=1_000_000)
    assert capped["token_budget"]["truncated"] is False
    assert capped["token_budget"]["estimated_tokens"] <= 1_000_000
    assert json.loads(json.dumps(capped))  # still serializable

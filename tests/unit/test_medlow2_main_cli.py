"""Regression tests for a second pass of confirmed MED/LOW dogfood bugs in
cli/main.py.

Covered fixes (this file owns the cli/main.py portion only):

- M14b The default Rust/`re` engine rejects a mid-pattern inline flag group
       (e.g. ``start(?s).*end``) that PCRE2 accepts. When the user did NOT
       explicitly pick a non-PCRE2 engine, ``tg search`` now transparently
       retries the search under PCRE2 (``-P``) and prints a one-line note on
       stderr instead of erroring. A genuinely invalid pattern still errors,
       and ``-F`` (literal intent) is never silently upgraded.
- L3-cli ``tg defs <repo> <COMMON_NAME>`` accepts a new ``--class TEXT`` option
       that filters definitions to those whose enclosing class matches
       (case-insensitive), threading the per-definition ``class`` field already
       returned by repo_map.build_symbol_defs.

The tests use CliRunner / direct helper calls so they stay import-light and do
not require a GPU or the native CUDA binary (the regex fallback and the class
filter are pure-Python paths in cli/main.py). ``rg`` / ``ast-grep`` on PATH are
needed for the L3-cli symbol-extraction tests, matching the dogfood environment.
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from tensor_grep.cli.main import (
    _apply_defs_class_filter,
    _eligible_for_pcre2_inline_flag_fallback,
    _engine_is_explicit_pcre2,
    _is_inline_flag_regex_error,
    app,
)
from tensor_grep.core.config import SearchConfig

INLINE_FLAG_PATTERN = "start(?s).*end"


# ---------------------------------------------------------------------------
# M14b - inline-flag detection + PCRE2-eligibility predicates (unit level)
# ---------------------------------------------------------------------------


def test_m14b_inline_flag_error_detection() -> None:
    # The exact message CPython's `re` raises for `start(?s).*end`.
    assert _is_inline_flag_regex_error(
        "error parsing regex: global flags not at the start of the expression at position 5"
    )
    # A genuinely-different parse error must NOT be classified as inline-flag.
    assert not _is_inline_flag_regex_error(
        "error parsing regex: missing ), unterminated subpattern at position 3"
    )


def test_m14b_explicit_pcre2_engine_detection() -> None:
    assert _engine_is_explicit_pcre2(SearchConfig(pcre2=True))
    assert _engine_is_explicit_pcre2(SearchConfig(engine="pcre2"))
    assert _engine_is_explicit_pcre2(SearchConfig(engine="PCRE2"))
    assert not _engine_is_explicit_pcre2(SearchConfig())
    assert not _engine_is_explicit_pcre2(SearchConfig(engine="auto"))


def test_m14b_fallback_eligibility_rules() -> None:
    # Default / unset engine and explicit auto opt in.
    assert _eligible_for_pcre2_inline_flag_fallback(SearchConfig())
    assert _eligible_for_pcre2_inline_flag_fallback(SearchConfig(engine="auto"))
    # Explicit PCRE2 already routes through PCRE2; -F is a literal intent.
    assert not _eligible_for_pcre2_inline_flag_fallback(SearchConfig(pcre2=True))
    assert not _eligible_for_pcre2_inline_flag_fallback(SearchConfig(engine="pcre2"))
    assert not _eligible_for_pcre2_inline_flag_fallback(SearchConfig(fixed_strings=True))


# ---------------------------------------------------------------------------
# M14b - end-to-end CLI behavior
# ---------------------------------------------------------------------------


def test_m14b_inline_flag_pattern_falls_back_to_pcre2(tmp_path: Path) -> None:
    target = tmp_path / "f.txt"
    target.write_text("start foo\nbar end\nstart middle end\n", encoding="utf-8")

    from tensor_grep.cli.main import _pcre2_fallback_backend_available

    runner = CliRunner()
    result = runner.invoke(app, ["search", INLINE_FLAG_PATTERN, str(target)])

    if _pcre2_fallback_backend_available():
        # PCRE2-capable rg: the inline-flag pattern transparently retries under PCRE2
        # (exit 0 = matches found) and the switch is announced on stderr, not silent.
        assert result.exit_code == 0, result.stdout + result.stderr
        assert "retried with PCRE2" in result.stderr
    else:
        # No PCRE2-capable rg (e.g. most CI images): do NOT blindly fall back to an
        # unavailable engine — keep the original error with the actionable -P remediation
        # instead of a confusing "PCRE2 unavailable" ConfigurationError.
        assert result.exit_code == 2, result.stdout + result.stderr
        combined = (result.stdout + result.stderr).lower()
        assert "invalid regex" in combined
        assert "pcre2" in combined


def test_m14b_genuinely_invalid_pattern_still_errors(tmp_path: Path) -> None:
    target = tmp_path / "f.txt"
    target.write_text("hello\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(app, ["search", "foo(", str(target)])

    # An unterminated subpattern is not an inline-flag case: it must still error,
    # and keep pointing at -P / -F in the remediation hint (audit M14).
    assert result.exit_code == 2, result.stdout
    combined = result.stdout + result.stderr
    assert "invalid regex" in combined.lower()
    assert "-P" in combined
    assert "retried with PCRE2" not in result.stderr


def test_m14b_fixed_strings_inline_flag_is_literal_not_fallback(tmp_path: Path) -> None:
    target = tmp_path / "f.txt"
    target.write_text(f"{INLINE_FLAG_PATTERN} literal line\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(app, ["search", INLINE_FLAG_PATTERN, str(target), "-F"])

    # -F means "search this literal text"; the literal line matches (exit 0) and no
    # PCRE2 fallback should fire. (Match text lands on the inherited fd 1, not the
    # CliRunner buffer, so we assert exit code + the absence of the fallback note.)
    assert result.exit_code == 0, result.stdout + result.stderr
    assert "retried with PCRE2" not in result.stderr


# ---------------------------------------------------------------------------
# L3-cli - defs --class filter (unit level on the helper)
# ---------------------------------------------------------------------------


def _defs_payload() -> dict:
    return {
        "symbol": "search",
        "path": ".",
        "definitions": [
            {"file": "a.py", "line": 10, "class": "RipgrepBackend", "kind": "function"},
            {"file": "b.py", "line": 20, "class": "CPUBackend", "kind": "function"},
            {"file": "c.rs", "line": 30, "class": None, "kind": "function"},
        ],
    }


def test_l3_class_filter_narrows_to_matching_class() -> None:
    payload = _defs_payload()
    _apply_defs_class_filter(payload, "RipgrepBackend")
    assert [d["file"] for d in payload["definitions"]] == ["a.py"]
    assert payload["class_filter"] == "RipgrepBackend"
    assert payload["class_filter_matched"] == 1


def test_l3_class_filter_is_case_insensitive() -> None:
    payload = _defs_payload()
    _apply_defs_class_filter(payload, "ripgrepbackend")
    assert len(payload["definitions"]) == 1
    assert payload["definitions"][0]["class"] == "RipgrepBackend"


def test_l3_class_filter_no_match_empties_definitions() -> None:
    payload = _defs_payload()
    _apply_defs_class_filter(payload, "NoSuchClass")
    assert payload["definitions"] == []
    assert payload["class_filter_matched"] == 0
    # Existing top-level keys are preserved (additive-only change).
    assert payload["symbol"] == "search"
    assert payload["path"] == "."


# ---------------------------------------------------------------------------
# L3-cli - defs --class filter end-to-end on a tiny repo
# ---------------------------------------------------------------------------


def test_l3_defs_class_filter_end_to_end(tmp_path: Path) -> None:
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "ripgrep_backend.py").write_text(
        "class RipgrepBackend:\n    def search(self):\n        return 1\n",
        encoding="utf-8",
    )
    (pkg / "cpu_backend.py").write_text(
        "class CPUBackend:\n    def search(self):\n        return 2\n",
        encoding="utf-8",
    )

    runner = CliRunner()
    unfiltered = runner.invoke(
        app, ["defs", str(tmp_path), "search", "--max-repo-files", "50", "--json"]
    )
    assert unfiltered.exit_code == 0, unfiltered.stdout + unfiltered.stderr
    all_defs = json.loads(unfiltered.stdout)["definitions"]
    classes = {str(d.get("class")) for d in all_defs}
    assert {"RipgrepBackend", "CPUBackend"} <= classes

    filtered = runner.invoke(
        app,
        [
            "defs",
            str(tmp_path),
            "search",
            "--class",
            "RipgrepBackend",
            "--max-repo-files",
            "50",
            "--json",
        ],
    )
    assert filtered.exit_code == 0, filtered.stdout + filtered.stderr
    payload = json.loads(filtered.stdout)
    assert payload["class_filter"] == "RipgrepBackend"
    assert len(payload["definitions"]) >= 1
    assert all(d.get("class") == "RipgrepBackend" for d in payload["definitions"])

    missing = runner.invoke(
        app,
        [
            "defs",
            str(tmp_path),
            "search",
            "--class",
            "DoesNotExist",
            "--max-repo-files",
            "50",
            "--json",
        ],
    )
    # No definitions in that class -> not_found convention -> exit 1 (audit L1).
    assert missing.exit_code == 1, missing.stdout
    missing_payload = json.loads(missing.stdout)
    assert missing_payload["definitions"] == []
    assert missing_payload["not_found"] is True

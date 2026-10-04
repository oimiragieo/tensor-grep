"""#299: `tg scan --ruleset` must not report findings as complete over files it could not read.

A skipped file contributes no findings. Before #299 the payload said nothing, so a security
ruleset read as "no violations" for a file nobody opened -- and a CI gate keyed on that passed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# `engine: "regex"` is the discriminator at main.py:6459 that routes a rule to the regex leg.
# The builtin packs (auth-safe etc.) are all AST metavar patterns and never reach it -- an
# earlier draft of this test used auth-safe and exercised NOTHING, which is why the rule below
# is hand-built rather than resolved from a pack.
_REGEX_RULE = {
    "id": "test-regex-token",
    "engine": "regex",
    "pattern": "SENTINEL_TOKEN",
    "language": "python",
    "severity": "high",
    "message": "sentinel",
}


def _scan(root: Path) -> dict:
    from tensor_grep.cli.main import _run_ast_scan_payload

    rules = [dict(_REGEX_RULE)]
    return _run_ast_scan_payload(
        {
            "config_path": "builtin:auth-safe",
            "root_dir": root,
            "rule_dirs": [],
            "test_dirs": [],
            "language": "python",
        },
        rules,
        routing_reason="builtin-ruleset-scan",
        ruleset_name="test-regex",
    )


def _scan_with_rules(root: Path, rule_count: int) -> dict:
    """`_scan` with N distinct regex rules, so the leg opens each file N times."""
    from tensor_grep.cli.main import _run_ast_scan_payload

    rules = []
    for i in range(rule_count):
        rule = dict(_REGEX_RULE)
        rule["id"] = f"{_REGEX_RULE['id']}-{i}"
        rules.append(rule)
    return _run_ast_scan_payload(
        {
            "config_path": "builtin:auth-safe",
            "root_dir": root,
            "rule_dirs": [],
            "test_dirs": [],
            "language": "python",
        },
        rules,
        routing_reason="builtin-ruleset-scan",
        ruleset_name="test-regex",
    )


def _read_text_raiser(canary: str, pristine):
    def fake(self, *args, **kwargs):
        if self.name == canary:
            raise PermissionError(13, "Permission denied", str(self))
        return pristine(self, *args, **kwargs)

    return fake


def test_scan_discloses_a_file_the_rules_could_not_read(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "clean.py").write_text("SENTINEL_TOKEN = 1\n", encoding="utf-8")
    (tmp_path / "locked.py").write_text("SENTINEL_TOKEN = 2\n", encoding="utf-8")

    # CONTROL: an all-readable scan must NOT claim to be partial. Without this arm, a payload
    # that always carried the marker would satisfy the treatment assertions below.
    control = _scan(tmp_path)
    assert "unreadable_paths" not in control, (
        "premise: a fully readable scan must carry NO unreadable marker, else the field is "
        "decoration and the treatment proves nothing"
    )
    assert control.get("partial") is not True
    # PREMISE: the rule must actually MATCH, else the scan is trivially empty and "the locked
    # file contributed nothing" would be true for every file, fix or no fix.
    assert control["total_matches"] >= 2, (
        f"expected the sentinel to match in both files, got {control['total_matches']}"
    )

    monkeypatch.setattr(Path, "read_text", _read_text_raiser("locked.py", Path.read_text))
    payload = _scan(tmp_path)
    monkeypatch.undo()

    assert payload["unreadable_paths"]["count"] >= 1
    assert any("locked.py" in s for s in payload["unreadable_paths"]["sample"])
    assert payload["partial"] is True
    assert payload["partial_reason"] == "unreadable_path"
    # Must not send the reader to a budget knob: no cap increase makes a file readable.
    assert "does NOT prove they are clean" in payload["remediation"]


def test_the_unreadable_sample_names_distinct_places_not_repeated_attempts(
    tmp_path: Path, monkeypatch
) -> None:
    """`scan` runs two backends, so ONE unreadable file raises TWO OSErrors.

    `_UnreadablePathFlag.record` increments per error and appends per error, so the pre-fix
    sample was `[locked.py, locked.py]` and the prose read "2 file(s) in scope could not be
    read" -- a false statement about the world, found by dogfooding the real front door against
    an ACL-denied fixture holding exactly ONE blocked file.

    The event COUNT is deliberately left alone (other `_UnreadablePathFlag` consumers count
    `os.scandir` failures, where per-event is right, and task 320 settled the same question for
    the native counter by documenting it). The SAMPLE is a list of PLACES, so it de-duplicates,
    and the prose no longer claims files.
    """
    (tmp_path / "clean.py").write_text("SENTINEL_TOKEN = 1\n", encoding="utf-8")
    (tmp_path / "locked.py").write_text("SENTINEL_TOKEN = 2\n", encoding="utf-8")

    pristine = Path.read_text
    monkeypatch.setattr(Path, "read_text", _read_text_raiser("locked.py", pristine))
    # TWO rules, so the regex leg opens each file twice -- the same double-attempt shape the real
    # command produces by running the ast-grep wrapper AND the regex leg over one file. With a
    # single rule only one OSError is raised and the duplicate condition never occurs, which the
    # premise assertion below would (and did) catch.
    payload = _scan_with_rules(tmp_path, 2)

    unreadable = payload["unreadable_paths"]
    # PREMISE: the double-attempt really happened, otherwise this test proves nothing about
    # de-duplication -- it would just be observing a one-element list that was never at risk.
    assert unreadable["count"] >= 2, (
        "premise failed: only one read attempt was recorded, so the duplicate-sample condition "
        "this test exists for was never reached"
    )
    assert len(unreadable["sample"]) == len(set(unreadable["sample"])), (
        f"sample repeats a path: {unreadable['sample']}"
    )
    assert len(unreadable["sample"]) == 1  # exactly one file was ever blocked
    # And the prose must not render an EVENT count as a FILE count.
    assert "file(s) in scope could not be read" not in payload["remediation"]
    assert "read attempt(s) in scope failed" in payload["remediation"]


# ---------------------------------------------------------------------------
# K1.3 (H-05): ast-grep (behind AstGrepWrapperBackend) skips non-UTF-8 files silently; the scan
# must disclose them instead of reading as a clean pass.
# ---------------------------------------------------------------------------


class AstGrepWrapperBackend:  # matched by type name in _run_ast_scan_payload
    def search(self, file_path, pattern, config=None):
        from tensor_grep.core.result import SearchResult

        return SearchResult(matches=[], total_files=0, total_matches=0)


class _NativeBackend:
    def search(self, file_path, pattern, config=None):
        from tensor_grep.core.result import SearchResult

        return SearchResult(matches=[], total_files=0, total_matches=0)


def _ast_scan(root, monkeypatch, language="python", rule_backends=None):
    """Run an AST-only scan. `rule_backends` is a list of (language, backend) per rule."""
    import tensor_grep.cli.ast_workflows as aw
    from tensor_grep.cli.main import _run_ast_scan_payload

    specs = rule_backends or [(language, AstGrepWrapperBackend())]
    rules = [
        {
            "id": f"r{i}",
            "language": lang,
            "pattern": "print($A)",
            "severity": "high",
            "message": "m",
        }
        for i, (lang, _backend) in enumerate(specs)
    ]
    by_rule_id = {f"r{i}": backend for i, (_lang, backend) in enumerate(specs)}
    monkeypatch.setattr(
        aw, "_select_ast_backend_for_rule", lambda cfg, rule, cache: by_rule_id[rule["id"]]
    )
    return _run_ast_scan_payload(
        {
            "config_path": "builtin:x",
            "root_dir": root,
            "rule_dirs": [],
            "test_dirs": [],
            "language": language,
        },
        rules,
        routing_reason="t",
        ruleset_name="t",
    )


def test_scan_discloses_non_utf8_file_the_ast_leg_cannot_read(tmp_path, monkeypatch):
    (tmp_path / "ok.py").write_text("x = 1\n", encoding="utf-8")
    assert "unreadable_paths" not in _ast_scan(tmp_path, monkeypatch)  # control
    (tmp_path / "lat.py").write_bytes(b"# caf\xe9\nx = 2\n")
    payload = _ast_scan(tmp_path, monkeypatch)
    assert payload["partial"] is True and payload["partial_reason"] == "unreadable_path"
    assert any("lat.py" in s for s in payload["unreadable_paths"]["sample"])
    assert not any("ok.py" in s for s in payload["unreadable_paths"]["sample"])
    assert "does NOT prove they are clean" in payload["remediation"]
    assert "AST rules skip files that are not valid UTF-8" in payload["remediation"]


def test_non_utf8_outside_the_rule_language_is_not_flagged(tmp_path, monkeypatch):
    (tmp_path / "ok.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "lat.js").write_bytes(b"// caf\xe9\n")
    (tmp_path / "data.txt").write_bytes(b"caf\xe9\n")
    assert "unreadable_paths" not in _ast_scan(tmp_path, monkeypatch)


def test_native_only_rules_never_count_non_utf8_as_skipped(tmp_path, monkeypatch):
    # K1.5 makes the native backend decode lossily, so a native-only language is not skipped.
    (tmp_path / "lat.py").write_bytes(b"# caf\xe9\nx = 2\n")
    payload = _ast_scan(tmp_path, monkeypatch, rule_backends=[("python", _NativeBackend())])
    assert "unreadable_paths" not in payload


def test_mixed_backend_scan_flags_only_the_wrapper_language(tmp_path, monkeypatch):
    (tmp_path / "lat.py").write_bytes(b"# caf\xe9\nx = 2\n")
    (tmp_path / "lat.js").write_bytes(b"// caf\xe9\n")
    payload = _ast_scan(
        tmp_path,
        monkeypatch,
        rule_backends=[("python", _NativeBackend()), ("javascript", AstGrepWrapperBackend())],
    )
    sample = payload["unreadable_paths"]["sample"]
    assert any("lat.js" in s for s in sample)
    assert not any("lat.py" in s for s in sample)


def test_same_language_wrapper_then_native_still_discloses(tmp_path, monkeypatch):
    (tmp_path / "lat.py").write_bytes(b"# caf\xe9\nx = 2\n")
    payload = _ast_scan(
        tmp_path,
        monkeypatch,
        rule_backends=[("python", AstGrepWrapperBackend()), ("python", _NativeBackend())],
    )
    assert any("lat.py" in s for s in payload["unreadable_paths"]["sample"])


def test_same_language_native_then_wrapper_still_discloses(tmp_path, monkeypatch):
    (tmp_path / "lat.py").write_bytes(b"# caf\xe9\nx = 2\n")
    payload = _ast_scan(
        tmp_path,
        monkeypatch,
        rule_backends=[("python", _NativeBackend()), ("python", AstGrepWrapperBackend())],
    )
    assert any("lat.py" in s for s in payload["unreadable_paths"]["sample"])


@pytest.mark.parametrize(
    ("language", "name", "data"),
    [
        ("ruby", "bad.rb", b"# caf\xe9\nputs(1)\n"),
        ("ruby", "bad.gemspec", b"# caf\xe9\nputs(1)\n"),
        ("kotlin", "bad.kt", b"// caf\xe9\nfun f() {}\n"),
        ("kotlin", "bad.kts", b"// caf\xe9\nfun f() {}\n"),
        ("swift", "bad.swift", b"// caf\xe9\nlet x = 1\n"),
        ("scala", "bad.scala", b"// caf\xe9\nobject A\n"),
        ("lua", "bad.lua", b"-- caf\xe9\nprint(1)\n"),
        ("bash", "bad.sh", b"# caf\xe9\necho 1\n"),
        ("elixir", "bad.ex", b"# caf\xe9\nIO.puts(1)\n"),
        ("haskell", "bad.hs", b"-- caf\xe9\nmain = print 1\n"),
        ("css", "bad.css", b"/* caf\xe9 */\na {}\n"),
        ("html", "bad.html", b"<!-- caf\xe9 --><p></p>\n"),
        ("yaml", "bad.yml", b"# caf\xe9\na: 1\n"),
        ("json", "bad.json", b'{"a": "caf\xe9"}\n'),
        ("solidity", "bad.sol", b"// caf\xe9\ncontract A {}\n"),
        ("hcl", "bad.hcl", b"# caf\xe9\na = 1\n"),
        ("bash", "bad.tmux", b"# caf\xe9\necho 1\n"),
        ("bash", "install.sh.in", b"# caf\xe9\necho 1\n"),
        ("nix", "bad.nix", b"# caf\xe9\n{}\n"),
    ],
)
def test_scan_discloses_non_utf8_file_for_wrapper_only_languages(
    tmp_path, monkeypatch, language, name, data
):
    # Codex audit: `_target_language_for_path` knows only the symbol-graph languages, so an
    # unreadable Ruby/Kotlin/... file was silently omitted and the rule read `clear`.
    (tmp_path / name).write_bytes(data)
    payload = _ast_scan(tmp_path, monkeypatch, language=language)
    assert payload["partial"] is True and payload["partial_reason"] == "unreadable_path"
    assert any(name in s for s in payload["unreadable_paths"]["sample"])


def test_non_utf8_ruby_file_under_a_python_only_rule_is_not_flagged(tmp_path, monkeypatch):
    (tmp_path / "ok.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "bad.rb").write_bytes(b"# caf\xe9\nputs(1)\n")
    (tmp_path / "bad.kt").write_bytes(b"// caf\xe9\n")
    assert "unreadable_paths" not in _ast_scan(tmp_path, monkeypatch, language="python")


def test_non_utf8_kotlin_file_under_a_ruby_rule_is_not_flagged(tmp_path, monkeypatch):
    (tmp_path / "bad.kt").write_bytes(b"// caf\xe9\n")
    assert "unreadable_paths" not in _ast_scan(tmp_path, monkeypatch, language="ruby")


def test_non_utf8_header_under_a_cpp_only_rule_is_not_flagged(tmp_path, monkeypatch):
    # ast-grep's cpp extensions exclude .h (it belongs to c); flagging it is a false `partial`.
    (tmp_path / "bad.h").write_bytes(b"// caf\xe9\n")
    (tmp_path / "bad.hxx").write_bytes(b"// caf\xe9\n")
    assert "unreadable_paths" not in _ast_scan(tmp_path, monkeypatch, language="cpp")
    # positive control: a real cpp extension IS flagged
    (tmp_path / "bad.cpp").write_bytes(b"// caf\xe9\n")
    payload = _ast_scan(tmp_path, monkeypatch, language="cpp")
    assert [s for s in payload["unreadable_paths"]["sample"] if s.endswith("bad.cpp")]
    assert not [s for s in payload["unreadable_paths"]["sample"] if s.endswith((".h", ".hxx"))]


def test_non_utf8_header_under_a_c_rule_is_flagged(tmp_path, monkeypatch):
    (tmp_path / "bad.h").write_bytes(b"// caf\xe9\n")
    payload = _ast_scan(tmp_path, monkeypatch, language="c")
    assert any(s.endswith("bad.h") for s in payload["unreadable_paths"]["sample"])


def test_non_utf8_terraform_file_under_an_hcl_rule_is_not_flagged(tmp_path, monkeypatch):
    (tmp_path / "bad.tf").write_bytes(b"# caf\xe9\n")
    (tmp_path / "bad.tfvars").write_bytes(b"# caf\xe9\n")
    assert "unreadable_paths" not in _ast_scan(tmp_path, monkeypatch, language="hcl")


def test_ast_grep_extension_table_is_pinned_to_the_official_reference():
    # Source: https://ast-grep.github.io/reference/languages.html ("File Extension" column),
    # fetched 2026-10-04. Editing the table must be a deliberate change to this pin too.
    from tensor_grep.cli import ast_scan

    expected = {
        "bash": {
            ".bash",
            ".bats",
            ".cgi",
            ".command",
            ".env",
            ".fcgi",
            ".ksh",
            ".sh",
            ".tmux",
            ".tool",
            ".zsh",
        },
        "c": {".c", ".h"},
        "cpp": {".cc", ".hpp", ".cpp", ".c++", ".hh", ".cxx", ".cu", ".ino"},
        "csharp": {".cs"},
        "css": {".css"},
        "elixir": {".ex", ".exs"},
        "go": {".go"},
        "haskell": {".hs"},
        "hcl": {".hcl"},
        "html": {".html", ".htm", ".xhtml"},
        "java": {".java"},
        "javascript": {".cjs", ".js", ".mjs", ".jsx"},
        "json": {".json"},
        "kotlin": {".kt", ".ktm", ".kts"},
        "lua": {".lua"},
        "markdown": {".markdown", ".md"},
        "nix": {".nix"},
        "php": {".php"},
        "python": {".py", ".py3", ".pyi", ".bzl"},
        "ruby": {".rb", ".rbw", ".gemspec"},
        "rust": {".rs"},
        "scala": {".scala", ".sc", ".sbt"},
        "solidity": {".sol"},
        "swift": {".swift"},
        "tsx": {".tsx"},
        "typescript": {".ts", ".cts", ".mts"},
        "yaml": {".yml", ".yaml"},
    }
    actual = {k: set(v) for k, v in ast_scan._AST_GREP_LANGUAGE_SUFFIXES.items()}
    assert actual == expected
    assert ast_scan._AST_GREP_LANGUAGE_NAME_ENDINGS == {"bash": (".sh.in",)}


def test_ast_grep_language_applies_matches_double_suffix_and_markdown():
    from tensor_grep.cli.ast_scan import _ast_grep_language_applies

    assert _ast_grep_language_applies({"bash"}, "dir/install.sh.in")
    assert not _ast_grep_language_applies({"bash"}, "dir/data.in")
    assert _ast_grep_language_applies({"markdown"}, "README.md")
    assert _ast_grep_language_applies({"markdown"}, "x.MARKDOWN")
    assert not _ast_grep_language_applies({"python"}, "README.md")

"""#299: `tg scan --ruleset` must not report findings as complete over files it could not read.

A skipped file contributes no findings. Before #299 the payload said nothing, so a security
ruleset read as "no violations" for a file nobody opened -- and a CI gate keyed on that passed.
"""

from __future__ import annotations

from pathlib import Path

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

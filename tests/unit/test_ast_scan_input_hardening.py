from __future__ import annotations

from pathlib import Path

import pytest

from tensor_grep.cli import ast_scan


def test_inline_suppression_survives_non_utf8_bytes(tmp_path: Path) -> None:
    (tmp_path / "lat.py").write_bytes(b"# tg-ignore: rule-x\nx = '\xe9'\n")
    cache: dict[str, list[str]] = {}
    assert (
        ast_scan._occurrence_has_inline_suppression(
            occurrence_file="lat.py",
            occurrence_line=2,
            rule_id="rule-x",
            language="python",
            root_dir=tmp_path,
            source_cache=cache,
        )
        is True
    )
    assert (
        ast_scan._occurrence_has_inline_suppression(
            occurrence_file="lat.py",
            occurrence_line=2,
            rule_id="other",
            language="python",
            root_dir=tmp_path,
            source_cache=cache,
        )
        is False
    )


@pytest.mark.parametrize("loader", ["_load_ruleset_baseline", "_load_ruleset_suppressions"])
def test_missing_dir_or_bad_json_ruleset_input_is_clean_value_error(
    tmp_path: Path, loader: str
) -> None:
    fn = getattr(ast_scan, loader)
    with pytest.raises(ValueError, match="could not be read"):
        fn(str(tmp_path / "nope.json"))
    with pytest.raises(ValueError, match="could not be read"):
        fn(str(tmp_path))  # a directory
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match="not valid JSON"):
        fn(str(bad))

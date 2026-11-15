from pathlib import Path

from tensor_grep.core.config import SearchConfig
from tensor_grep.io.directory_scanner import DirectoryScanner
from tensor_grep.io.file_types import BUILTIN_TYPE_LIST


def _names(tmp_path, type_name: str) -> set[str]:
    return {
        Path(path).name
        for path in DirectoryScanner(SearchConfig(file_type=[type_name], ignore=False)).walk(
            tmp_path
        )
    }


def test_json_and_jsonl_match_ripgrep_builtin_types(tmp_path) -> None:
    for name in ("data.json", "report.sarif", "events.jsonl", "composer.lock", "other.lock"):
        (tmp_path / name).write_text("value\n", encoding="utf-8")

    assert _names(tmp_path, "json") == {"data.json", "report.sarif", "composer.lock"}
    assert _names(tmp_path, "jsonl") == {"events.jsonl"}


def test_type_list_preserves_exact_json_basename() -> None:
    lines = {line.partition(":")[0]: line for line in BUILTIN_TYPE_LIST}
    assert lines["json"] == "json: *.json, *.sarif, composer.lock"
    assert lines["jsonl"] == "jsonl: *.jsonl"

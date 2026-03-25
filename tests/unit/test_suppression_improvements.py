import json
from pathlib import Path

import pytest

from tensor_grep.cli.main import _load_ruleset_suppressions, _run_ast_scan_payload
from tensor_grep.core.result import MatchLine, SearchResult


class _LineAwareFakeAstBackend:
    def search(self, file_path: str, pattern: str, config=None) -> SearchResult:
        _ = config
        try:
            lines = Path(file_path).read_text(encoding="utf-8").splitlines()
        except OSError:
            lines = []
        matches = [
            MatchLine(line_number=index, text=line, file=file_path)
            for index, line in enumerate(lines, start=1)
            if pattern in line
        ]
        return SearchResult(
            matches=matches,
            total_files=1 if matches else 0,
            total_matches=len(matches),
        )


class _LineAwareFakeAstPipeline:
    def __init__(self, force_cpu: bool = False, config=None) -> None:
        _ = force_cpu
        _ = config
        self._backend = _LineAwareFakeAstBackend()

    def get_backend(self) -> _LineAwareFakeAstBackend:
        return self._backend


class _RecursiveScanner:
    def __init__(self, config=None) -> None:
        _ = config

    def walk(self, path: str):
        root = Path(path)
        for candidate in sorted(root.rglob("*")):
            if candidate.is_file():
                yield candidate.relative_to(root).as_posix()


def _scan_rules(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    files: dict[str, str],
    rules: list[dict[str, str]],
    language: str = "python",
    suppressions_path: str | None = None,
    write_suppressions_path: str | None = None,
    suppression_justification: str | None = None,
) -> dict[str, object]:
    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _LineAwareFakeAstPipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _RecursiveScanner)
    monkeypatch.chdir(tmp_path)

    for relative_path, content in files.items():
        path = tmp_path / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    project_cfg = {
        "config_path": "builtin:test-pack",
        "root_dir": tmp_path,
        "rule_dirs": [],
        "test_dirs": [],
        "language": language,
    }
    return _run_ast_scan_payload(
        project_cfg,
        rules,
        routing_reason="builtin-ruleset-scan",
        ruleset_name="test-pack",
        suppressions_path=suppressions_path,
        write_suppressions_path=write_suppressions_path,
        suppression_justification=suppression_justification,
    )


def test_load_ruleset_suppressions_accepts_entries_schema(tmp_path: Path) -> None:
    suppressions_path = tmp_path / "suppressions.json"
    suppressions_path.write_text(
        json.dumps(
            {
                "version": 1,
                "kind": "ruleset-scan-suppressions",
                "entries": [
                    {
                        "fingerprint": "fp-1",
                        "justification": "Accepted risk for generated fixture.",
                        "created_at": "2026-03-25T00:00:00Z",
                        "file": "a.py",
                        "line": 3,
                        "rule_id": "rule-a",
                    }
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    payload = _load_ruleset_suppressions(str(suppressions_path))

    assert payload["warnings"] == []
    assert payload["entries"] == [
        {
            "fingerprint": "fp-1",
            "justification": "Accepted risk for generated fixture.",
            "created_at": "2026-03-25T00:00:00Z",
            "file": "a.py",
            "line": 3,
            "rule_id": "rule-a",
        }
    ]


@pytest.mark.parametrize(
    "entries",
    [
        [{"fingerprint": "fp-1", "created_at": "2026-03-25T00:00:00Z"}],
        [
            {
                "fingerprint": "fp-1",
                "justification": "   ",
                "created_at": "2026-03-25T00:00:00Z",
            }
        ],
        [
            {
                "fingerprint": "fp-1",
                "justification": "ok",
                "created_at": "not-a-timestamp",
            }
        ],
    ],
)
def test_load_ruleset_suppressions_rejects_invalid_entries(
    tmp_path: Path, entries: list[dict[str, object]]
) -> None:
    suppressions_path = tmp_path / "suppressions.json"
    suppressions_path.write_text(
        json.dumps({"version": 1, "kind": "ruleset-scan-suppressions", "entries": entries}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Ruleset suppressions"):
        _load_ruleset_suppressions(str(suppressions_path))


def test_load_ruleset_suppressions_legacy_format_is_supported_with_warning(
    tmp_path: Path,
) -> None:
    suppressions_path = tmp_path / "suppressions.json"
    suppressions_path.write_text(
        json.dumps(
            {
                "version": 1,
                "kind": "ruleset-scan-suppressions",
                "fingerprints": ["fp-1", "fp-2"],
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    payload = _load_ruleset_suppressions(str(suppressions_path))

    assert payload["entries"] == [{"fingerprint": "fp-1"}, {"fingerprint": "fp-2"}]
    assert payload["warnings"] == [
        "Legacy suppression format using 'fingerprints' is deprecated; use 'entries' instead."
    ]


def test_write_suppressions_requires_non_empty_justification(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with pytest.raises(ValueError, match="justification"):
        _scan_rules(
            monkeypatch,
            tmp_path,
            files={"a.py": "danger\n"},
            rules=[{"id": "rule-a", "pattern": "danger", "language": "python"}],
            write_suppressions_path="written-suppressions.json",
        )


def test_write_suppressions_writes_entries_with_justification_and_timestamp(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    payload = _scan_rules(
        monkeypatch,
        tmp_path,
        files={"a.py": "danger\n"},
        rules=[{"id": "rule-a", "pattern": "danger", "language": "python"}],
        write_suppressions_path="written-suppressions.json",
        suppression_justification="Legacy dependency false positive.",
    )

    written = json.loads((tmp_path / "written-suppressions.json").read_text(encoding="utf-8"))

    assert payload["suppressions_written"]["count"] == 1
    assert written["kind"] == "ruleset-scan-suppressions"
    assert written["entries"][0]["fingerprint"] == payload["findings"][0]["fingerprint"]
    assert written["entries"][0]["justification"] == "Legacy dependency false positive."
    assert written["entries"][0]["created_at"].endswith("Z")


def test_inline_python_comment_on_previous_line_marks_finding_inline_suppressed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    payload = _scan_rules(
        monkeypatch,
        tmp_path,
        files={"a.py": "# tg-ignore: rule-a\ndanger\n"},
        rules=[{"id": "rule-a", "pattern": "danger", "language": "python"}],
    )

    assert payload["findings"][0]["status"] == "inline-suppressed"
    assert payload["findings"][0]["occurrences"] == [
        {"file": "a.py", "line": 2, "status": "inline-suppressed"}
    ]


def test_inline_python_comment_on_same_line_marks_finding_inline_suppressed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    payload = _scan_rules(
        monkeypatch,
        tmp_path,
        files={"a.py": "danger  # tg-ignore: rule-a\n"},
        rules=[{"id": "rule-a", "pattern": "danger", "language": "python"}],
    )

    assert payload["findings"][0]["status"] == "inline-suppressed"
    assert payload["findings"][0]["occurrences"][0]["line"] == 1


@pytest.mark.parametrize(
    ("language", "filename"),
    [
        ("javascript", "a.js"),
        ("typescript", "a.ts"),
        ("rust", "a.rs"),
    ],
)
def test_inline_slash_comment_suppresses_javascript_typescript_and_rust(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, language: str, filename: str
) -> None:
    payload = _scan_rules(
        monkeypatch,
        tmp_path,
        language=language,
        files={filename: "danger(); // tg-ignore: rule-a\n"},
        rules=[{"id": "rule-a", "pattern": "danger()", "language": language}],
    )

    assert payload["findings"][0]["status"] == "inline-suppressed"


def test_inline_suppression_requires_matching_rule_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    payload = _scan_rules(
        monkeypatch,
        tmp_path,
        files={"a.py": "# tg-ignore: rule-b\ndanger\n"},
        rules=[{"id": "rule-a", "pattern": "danger", "language": "python"}],
    )

    assert payload["findings"][0]["status"] == "new"


def test_inline_wildcard_suppresses_all_rules_on_line(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    payload = _scan_rules(
        monkeypatch,
        tmp_path,
        files={"a.py": "# tg-ignore: *\ndanger token\n"},
        rules=[
            {"id": "rule-a", "pattern": "danger", "language": "python"},
            {"id": "rule-b", "pattern": "token", "language": "python"},
        ],
    )

    assert [finding["status"] for finding in payload["findings"]] == [
        "inline-suppressed",
        "inline-suppressed",
    ]


def test_inline_multi_rule_comment_trims_whitespace(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    payload = _scan_rules(
        monkeypatch,
        tmp_path,
        files={"a.py": "#  tg-ignore :  rule-a ,   rule-b  \ndanger token\n"},
        rules=[
            {"id": "rule-a", "pattern": "danger", "language": "python"},
            {"id": "rule-b", "pattern": "token", "language": "python"},
        ],
    )

    assert payload["findings"][0]["status"] == "inline-suppressed"
    assert payload["findings"][1]["status"] == "inline-suppressed"


def test_location_targeted_suppression_only_suppresses_matching_occurrence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    initial_payload = _scan_rules(
        monkeypatch,
        tmp_path,
        files={"a.py": "danger\n", "b.py": "danger\n"},
        rules=[{"id": "rule-a", "pattern": "danger", "language": "python"}],
    )
    fingerprint = initial_payload["findings"][0]["fingerprint"]
    suppressions_path = tmp_path / "suppressions.json"
    suppressions_path.write_text(
        json.dumps(
            {
                "version": 1,
                "kind": "ruleset-scan-suppressions",
                "entries": [
                    {
                        "fingerprint": fingerprint,
                        "justification": "Known false positive in a.py only.",
                        "created_at": "2026-03-25T00:00:00Z",
                        "file": "a.py",
                        "line": 1,
                        "rule_id": "rule-a",
                    }
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    payload = _scan_rules(
        monkeypatch,
        tmp_path,
        files={"a.py": "danger\n", "b.py": "danger\n"},
        rules=[{"id": "rule-a", "pattern": "danger", "language": "python"}],
        suppressions_path=str(suppressions_path),
    )

    assert payload["findings"][0]["status"] == "new"
    assert payload["findings"][0]["occurrences"] == [
        {"file": "a.py", "line": 1, "status": "suppressed"},
        {"file": "b.py", "line": 1, "status": "new"},
    ]
    assert payload["suppressions"]["suppressed_occurrences"] == 1


def test_fingerprint_only_entry_suppresses_all_matching_occurrences(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    initial_payload = _scan_rules(
        monkeypatch,
        tmp_path,
        files={"a.py": "danger\n"},
        rules=[{"id": "rule-a", "pattern": "danger", "language": "python"}],
    )
    fingerprint = initial_payload["findings"][0]["fingerprint"]
    suppressions_path = tmp_path / "suppressions.json"
    suppressions_path.write_text(
        json.dumps(
            {
                "version": 1,
                "kind": "ruleset-scan-suppressions",
                "entries": [
                    {
                        "fingerprint": fingerprint,
                        "justification": "Global suppression for fixture.",
                        "created_at": "2026-03-25T00:00:00Z",
                    }
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    payload = _scan_rules(
        monkeypatch,
        tmp_path,
        files={"a.py": "danger\n"},
        rules=[{"id": "rule-a", "pattern": "danger", "language": "python"}],
        suppressions_path=str(suppressions_path),
    )

    assert payload["findings"][0]["status"] == "suppressed"
    assert payload["findings"][0]["occurrences"] == [
        {"file": "a.py", "line": 1, "status": "suppressed"}
    ]


def test_legacy_fingerprint_suppressions_still_work_with_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    initial_payload = _scan_rules(
        monkeypatch,
        tmp_path,
        files={"a.py": "danger\n"},
        rules=[{"id": "rule-a", "pattern": "danger", "language": "python"}],
    )
    fingerprint = initial_payload["findings"][0]["fingerprint"]
    suppressions_path = tmp_path / "suppressions.json"
    suppressions_path.write_text(
        json.dumps(
            {
                "version": 1,
                "kind": "ruleset-scan-suppressions",
                "fingerprints": [fingerprint],
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    payload = _scan_rules(
        monkeypatch,
        tmp_path,
        files={"a.py": "danger\n"},
        rules=[{"id": "rule-a", "pattern": "danger", "language": "python"}],
        suppressions_path=str(suppressions_path),
    )

    assert payload["findings"][0]["status"] == "suppressed"
    assert payload["suppressions"]["warnings"] == [
        "Legacy suppression format using 'fingerprints' is deprecated; use 'entries' instead."
    ]

"""Round-5 Q1+Q2: rg NDJSON must split on newline only (not str.splitlines, which also breaks on
U+2028/U+2029/U+0085 that rg emits unescaped inside match text), and file-list membership must be
O(1). CI-safe: mocks run_subprocess, never spawns real rg."""

import json
from types import SimpleNamespace
from unittest.mock import patch

from tensor_grep.backends.ripgrep_backend import RipgrepBackend
from tensor_grep.core.config import SearchConfig

U2028 = chr(0x2028)  # LINE SEPARATOR: str.splitlines() splits here; str.split(chr(10)) does not
U0085 = chr(0x0085)  # NEXT LINE: same class of bug


def _fake(returncode: int, stdout: str, stderr: str = "") -> SimpleNamespace:
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def _search(stdout: str, config: SearchConfig | None = None):
    be = RipgrepBackend()
    with (
        patch.object(be, "_get_binary_name", return_value="rg"),
        patch(
            "tensor_grep.backends.ripgrep_backend.run_subprocess",
            return_value=_fake(0, stdout),
        ),
    ):
        return be.search("a.py", "foo", config or SearchConfig())


def _match(path: str, text: str = "x", line: int = 1) -> str:
    # ensure_ascii=False replicates rg/serde_json emitting the raw separator (not a unicode escape).
    return json.dumps(
        {
            "type": "match",
            "data": {
                "path": {"text": path},
                "lines": {"text": text},
                "line_number": line,
                "submatches": [{"match": {"text": "foo"}, "start": 0, "end": 3}],
            },
        },
        ensure_ascii=False,
    )


class TestQ1UnicodeLineBoundarySplit:
    def test_u2028_in_match_text_is_not_dropped(self) -> None:
        rec = _match("a.py", text="foo" + U2028 + "bar")
        assert U2028 in rec and "\n" not in rec  # raw separator inside a single JSON record
        res = _search(rec + "\n")
        # str.splitlines() fractures this record at U+2028 into two invalid-JSON halves -> both
        # fail json.loads -> silently dropped -> 0 matches. split on newline keeps it whole -> 1.
        assert res.total_matches == 1
        assert res.matches and res.matches[0].file == "a.py"

    def test_u0085_next_line_char_is_not_dropped(self) -> None:
        rec = _match("a.py", text="foo" + U0085 + "baz")
        assert U0085 in rec and "\n" not in rec
        res = _search(rec + "\n")
        assert res.total_matches == 1


class TestQ2MembershipOrderAndCounts:
    def test_first_seen_order_preserved_no_dupes(self) -> None:
        stdout = "\n".join([_match("z.py"), _match("a.py"), _match("z.py")]) + "\n"
        res = _search(stdout)
        assert res.matched_file_paths == ["z.py", "a.py"]  # first-seen order, z not duplicated
        assert res.match_counts_by_file == {"z.py": 2, "a.py": 1}
        assert res.total_matches == 3

    def test_single_file_many_matches(self) -> None:
        stdout = "\n".join(_match("a.py", line=i) for i in range(1, 6)) + "\n"
        res = _search(stdout)
        assert res.matched_file_paths == ["a.py"]
        assert res.match_counts_by_file == {"a.py": 5}
        assert res.total_matches == 5

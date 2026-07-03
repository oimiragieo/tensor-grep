"""TDD suite for ``tg diff-docs`` (round-4 [e], 3-lens design-council test list).

Precision is the whole game: a single false positive per page makes an agent distrust the tool
(DocPrism: naive matching = 0.62 precision). Most tests assert we DON'T flag things.
"""

import json

import pytest

from tensor_grep.cli.diff_docs import build_doc_drift, render_doc_drift_text


def _repo(tmp_path, doc_body: str, *, doc_name: str = "guide.md"):
    code = tmp_path / "code"
    code.mkdir()
    (code / "mod.py").write_text(
        "def make_invoice(invoice_id):\n    return invoice_id\n\n\nclass InvoiceBuilder:\n    pass\n",
        encoding="utf-8",
    )
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / doc_name).write_text(doc_body, encoding="utf-8")
    return build_doc_drift(str(docs), code_path=str(code))


def _refs(payload) -> set[str]:
    return {f["reference_text"] for f in payload["findings"]}


class TestFailClosedAndSchema:
    def test_nonexistent_path_fails_closed(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            build_doc_drift(str(tmp_path / "nope"))

    def test_schema_envelope(self, tmp_path):
        out = _repo(tmp_path, "# doc\n\n```python\nmake_invoice(1)\n```\n")
        assert out["version"] == 1 and out["schema_version"] == 1
        assert out["coverage"]["language_scope"] == ["python", "javascript", "typescript", "rust"]
        assert "scan_limit" in out


class TestCoreDrift:
    def test_resolved_symbol_produces_no_finding(self, tmp_path):
        out = _repo(tmp_path, "```python\nmake_invoice(1)\nInvoiceBuilder()\n```\n")
        assert out["findings"] == []

    def test_removed_symbol_in_fenced_block_flagged_unresolved(self, tmp_path):
        out = _repo(tmp_path, "```python\nremoved_helper()\n```\n")
        assert "removed_helper" in _refs(out)
        f = next(f for f in out["findings"] if f["reference_text"] == "removed_helper")
        assert f["kind"] == "unresolved-symbol-reference"  # never "removed"
        assert "removed" not in f["reason"].lower() or "no definition" in f["reason"].lower()

    def test_prose_mention_excluded(self, tmp_path):
        # removed_helper only in prose (no code span) -> never scanned.
        out = _repo(tmp_path, "The removed_helper function used to exist but is gone now.\n")
        assert out["findings"] == []


class TestPrecisionGuards:
    def test_tg_command_name_token_not_flagged(self, tmp_path):
        out = _repo(tmp_path, "```python\nsearch\nmap\nrun\n```\n")
        assert out["findings"] == []

    def test_shell_fence_skipped_entirely(self, tmp_path):
        # bash is out-of-scope -> not resolved; the doc counts as out-of-scope, no findings.
        out = _repo(tmp_path, "```bash\ngit commit -m 'x'\nnonexistent_cmd\n```\n")
        assert out["findings"] == []
        assert out["coverage"]["docs_files_out_of_scope"] == 1

    def test_cli_flag_token_not_flagged(self, tmp_path):
        out = _repo(tmp_path, "```python\nsearch(--json)\n```\n")
        assert not any(f["reference_text"].startswith("-") for f in out["findings"])

    def test_language_keyword_and_builtin_not_flagged(self, tmp_path):
        out = _repo(tmp_path, "```python\nfor return class\nlen print range\n```\n")
        assert out["findings"] == []

    def test_third_party_dotted_last_segment_common_skipped(self, tmp_path):
        # requests.get -> 'get' is a builtin/short -> skipped, not a false positive.
        out = _repo(tmp_path, "```python\nrequests.get(url)\n```\n")
        assert "requests.get" not in _refs(out)

    def test_short_common_word_not_flagged(self, tmp_path):
        out = _repo(tmp_path, "```python\ndata value self true\n```\n")
        assert out["findings"] == []

    def test_fence_info_line_not_scanned(self, tmp_path):
        # the ```python info-string itself must not be tokenized as a symbol 'python'.
        out = _repo(tmp_path, "```python\nmake_invoice(1)\n```\n")
        assert "python" not in _refs(out)


class TestScopeHonesty:
    def test_non_python_doc_surfaced_in_coverage_not_zero_drift(self, tmp_path):
        out = _repo(tmp_path, "```go\nfunc Foo() {}\nBarBaz()\n```\n")
        assert out["findings"] == []  # Go symbols are not resolvable -> never flagged
        assert out["coverage"]["docs_files_out_of_scope"] == 1  # surfaced, not silent "clean"

    def test_paper_md_reference_downgraded_to_low_confidence(self, tmp_path):
        out = _repo(tmp_path, "```python\nRejectedPrototypeApi()\n```\n", doc_name="PAPER.md")
        f = next(
            (f for f in out["findings"] if f["reference_text"] == "RejectedPrototypeApi"), None
        )
        assert f is not None and f["confidence"] == "low"


class TestConfidenceAndRendering:
    def test_qualified_fenced_symbol_is_high_confidence(self, tmp_path):
        out = _repo(tmp_path, "```python\nRemovedClassName()\n```\n")
        f = next(f for f in out["findings"] if f["reference_text"] == "RemovedClassName")
        assert f["confidence"] == "high"  # PascalCase + fenced + len>=6

    def test_inline_span_is_low_confidence(self, tmp_path):
        out = _repo(tmp_path, "See `removed_inline_symbol` for details.\n")
        f = next(f for f in out["findings"] if f["reference_text"] == "removed_inline_symbol")
        assert f["span_kind"] == "inline-code" and f["confidence"] == "low"

    def test_render_text_and_json_roundtrip(self, tmp_path):
        out = _repo(tmp_path, "```python\nremoved_helper()\n```\n")
        assert "diff-docs:" in render_doc_drift_text(out)
        assert json.loads(json.dumps(out))["findings"]  # JSON-serializable

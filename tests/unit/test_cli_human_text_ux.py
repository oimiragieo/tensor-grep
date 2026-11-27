"""Comprehensive tests for Wave 7 Track 7C: human-first plain-text UX and DF2-12 fixes."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from tensor_grep.cli import ast_workflows
from tensor_grep.cli.evidence_signing import generate_keypair, sign_receipt
from tensor_grep.cli.formatters.ripgrep_fmt import RipgrepFormatter
from tensor_grep.cli.main import app
from tensor_grep.core.config import SearchConfig
from tensor_grep.core.result import MatchLine, SearchResult


def test_classify_defaults_to_human_text_and_json_flag_emits_json(monkeypatch) -> None:
    monkeypatch.delenv("TENSOR_GREP_CLASSIFY_PROVIDER", raising=False)
    runner = CliRunner()

    human = runner.invoke(app, ["classify", "--text", "CRITICAL OutOfMemoryError"])
    assert human.exit_code == 0, human.output
    assert "critical" in human.stdout
    assert "OutOfMemoryError" in human.stdout
    assert not human.stdout.lstrip().startswith("{")

    as_json = runner.invoke(app, ["classify", "--json", "--text", "CRITICAL OutOfMemoryError"])
    assert as_json.exit_code == 0, as_json.output
    payload = json.loads(as_json.stdout)
    assert payload["classifications"][0]["label"] == "critical"


def test_route_test_defaults_to_human_text_when_json_omitted(tmp_path: Path) -> None:
    src = tmp_path / "mod.py"
    src.write_text("def target_fn():\n    return 1\n", encoding="utf-8")

    runner = CliRunner()
    res = runner.invoke(app, ["route-test", str(tmp_path), "target_fn"])
    assert res.exit_code == 0, res.output
    assert "Route test for" in res.stdout
    assert "agreement=" in res.stdout
    assert not res.stdout.lstrip().startswith("{")


def test_run_rewrite_defaults_to_unified_diff_and_json_emits_json(
    monkeypatch, tmp_path: Path
) -> None:
    def _fake_plan_json(*, pattern: str, replacement: str, lang: str, path: str) -> tuple[str, int]:
        return (
            json.dumps({
                "total_edits": 1,
                "edits": [
                    {
                        "file": "a.py",
                        "line": 1,
                        "original_text": "def add(x, y): return x + y",
                        "replacement_text": "lambda x, y: x + y",
                    }
                ],
            }),
            0,
        )

    monkeypatch.setattr(ast_workflows, "execute_rewrite_plan_json", _fake_plan_json)
    (tmp_path / "a.py").write_text("def add(x, y): return x + y\n", encoding="utf-8")
    runner = CliRunner()

    human = runner.invoke(
        app,
        [
            "run",
            "--lang",
            "python",
            "--rewrite",
            "lambda $$$ARGS: $EXPR",
            "def $F($$$ARGS): return $EXPR",
            str(tmp_path / "a.py"),
        ],
    )
    assert human.exit_code == 0, human.output
    assert "--- a/a.py" in human.stdout
    assert "-def add(x, y): return x + y" in human.stdout
    assert "+lambda x, y: x + y" in human.stdout

    as_json = runner.invoke(
        app,
        [
            "run",
            "--lang",
            "python",
            "--rewrite",
            "lambda $$$ARGS: $EXPR",
            "--json",
            "def $F($$$ARGS): return $EXPR",
            str(tmp_path / "a.py"),
        ],
    )
    assert as_json.exit_code == 0, as_json.output
    assert json.loads(as_json.stdout)["total_edits"] == 1


def test_context_edit_plan_blast_radius_and_plan_list_top_items(
    monkeypatch, tmp_path: Path
) -> None:
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    (tmp_path / "test_a.py").write_text(
        "from a import foo\ndef test_foo():\n    assert foo() == 1\n", encoding="utf-8"
    )
    runner = CliRunner()

    ctx_res = runner.invoke(app, ["context", str(tmp_path), "foo"])
    assert ctx_res.exit_code == 0, ctx_res.output
    assert "Top files:" in ctx_res.stdout

    ep_res = runner.invoke(app, ["edit-plan", str(tmp_path), "foo"])
    assert ep_res.exit_code == 0, ep_res.output
    assert "Top files:" in ep_res.stdout

    br_res = runner.invoke(app, ["blast-radius", str(tmp_path), "foo"])
    assert br_res.exit_code == 0, br_res.output
    assert "Top callers:" in br_res.stdout or "Top files:" in br_res.stdout

    brp_res = runner.invoke(app, ["blast-radius-plan", str(tmp_path), "foo"])
    assert brp_res.exit_code == 0, brp_res.output
    assert "Top files:" in brp_res.stdout


def test_tty_default_max_columns_300_and_zero_disables(monkeypatch, tmp_path: Path) -> None:
    fmt_300 = RipgrepFormatter(config=SearchConfig(max_columns=300, with_filename=True))
    long_line = "x" * 500
    res = SearchResult(
        matches=[MatchLine(file="big.min.js", line_number=1, text=long_line)],
        total_matches=1,
        total_files=1,
    )
    out_300 = fmt_300.format(res)
    assert len(out_300) < 350
    assert out_300.endswith("...")

    fmt_0 = RipgrepFormatter(config=SearchConfig(max_columns=None, with_filename=True))
    out_0 = fmt_0.format(res)
    assert long_line in out_0

    captured: list[int | None] = []

    class _DummyPipeline:
        def __init__(self, config: SearchConfig | None = None, **kwargs: object) -> None:
            captured.append(config.max_columns if config else None)

        def execute(self, *args: object, **kwargs: object) -> SearchResult:
            return SearchResult()

    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _DummyPipeline)
    monkeypatch.setattr(
        "tensor_grep.backends.ripgrep_backend.RipgrepBackend.is_available", lambda self: False
    )
    monkeypatch.setattr("tensor_grep.cli.main.resolve_native_tg_binary", lambda: None)
    monkeypatch.setattr("tensor_grep.cli.main._stdout_isatty", lambda: True)
    (tmp_path / "f.txt").write_text("needle\n", encoding="utf-8")

    CliRunner().invoke(app, ["search", "needle", str(tmp_path)])
    assert captured[-1] == 300

    CliRunner().invoke(app, ["search", "--max-columns", "0", "needle", str(tmp_path)])
    assert captured[-1] is None


def test_audit_history_empty_human_message(tmp_path: Path) -> None:
    res = CliRunner().invoke(app, ["audit-history", str(tmp_path)])
    assert res.exit_code == 0, res.output
    assert "No audit manifests found." in res.stdout


def test_scan_zero_findings_compact_unless_verbose(monkeypatch, tmp_path: Path) -> None:
    (tmp_path / "clean.py").write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setattr(
        "tensor_grep.cli.main._run_ast_scan_payload",
        lambda *a, **kw: {
            "rule_count": 2,
            "matched_rules": 0,
            "total_matches": 0,
            "backends": ["AstGrepWrapperBackend"],
            "findings": [],
        },
    )
    runner = CliRunner()
    compact = runner.invoke(
        app, ["scan", "--ruleset", "crypto-safe", "--language", "python", "--path", str(tmp_path)]
    )
    assert compact.exit_code == 0, compact.output
    lines = [line for line in compact.stdout.splitlines() if line.strip()]
    assert len(lines) == 1
    assert lines[0].startswith("Scan completed.")

    verbose = runner.invoke(
        app,
        [
            "scan",
            "--ruleset",
            "crypto-safe",
            "--language",
            "python",
            "--path",
            str(tmp_path),
            "--verbose",
        ],
    )
    assert verbose.exit_code == 0, verbose.output
    assert "Scanning project using built-in ruleset" in verbose.stdout


def test_find_silences_model2vec_stderr_unless_verbose(monkeypatch, tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("def invoice_total():\n    return 42\n", encoding="utf-8")
    monkeypatch.setattr(
        "tensor_grep.core.retrieval_dense.dense_available",
        lambda: (False, "semantic ranking unavailable: model2vec not installed -- test stub"),
    )
    runner = CliRunner()

    quiet = runner.invoke(app, ["find", "invoice", str(tmp_path)])
    assert quiet.exit_code == 0, quiet.output
    assert "model2vec" not in quiet.stderr

    as_json = runner.invoke(app, ["find", "invoice", str(tmp_path), "--json"])
    assert as_json.exit_code == 0, as_json.output
    payload = json.loads(as_json.stdout)
    assert "model2vec" in payload["rank_fallback_reason"]
    assert "install_state" in payload

    verbose = runner.invoke(app, ["find", "invoice", str(tmp_path), "--verbose"])
    assert verbose.exit_code == 0, verbose.output
    assert "model2vec" in verbose.stderr


def test_audit_verify_missing_json_emits_structured_json_error(tmp_path: Path) -> None:
    missing = tmp_path / "missing.json"
    res = CliRunner().invoke(app, ["audit-verify", str(missing), "--json"])
    assert res.exit_code == 1
    payload = json.loads(res.stdout)
    assert payload["valid"] is False
    assert payload["error"]["code"] == "not_found"


def test_evidence_verify_auto_loads_pub_file(tmp_path: Path) -> None:
    key_info = generate_keypair(tmp_path / "signing.key")
    pub_path = Path(key_info["public_key_path"])
    receipt_path = tmp_path / "receipt.json"
    receipt = {
        "schema_version": 1,
        "kind": "evidence-receipt",
        "command": "test",
        "payload": {"ok": True},
    }
    signed = sign_receipt(receipt, private_key_path=key_info["private_key_path"])
    receipt_path.write_text(json.dumps(signed), encoding="utf-8")

    res = CliRunner().invoke(
        app,
        [
            "evidence",
            "verify",
            str(receipt_path),
            "--trusted-key",
            str(pub_path),
            "--require-trusted",
            "--json",
        ],
    )
    assert res.exit_code == 0, res.output
    payload = json.loads(res.stdout)
    assert payload["valid"] is True
    assert payload["checks"]["key_trusted"] is True


def test_venv_direct_search_refused_without_no_ignore(tmp_path: Path) -> None:
    venv = tmp_path / ".venv"
    venv.mkdir()
    (venv / "dep.py").write_text("ERROR\n", encoding="utf-8")

    res = CliRunner().invoke(app, ["search", "ERROR", str(venv)])
    assert res.exit_code == 2
    assert "broad generated-root scan refused" in res.output


def test_evidence_signing_handles_base64_slash_and_rejects_oversize_non_utf8_pub(
    tmp_path: Path,
) -> None:
    import base64

    import pytest

    from tensor_grep.cli.evidence_signing import EvidenceSigningError, resolve_trusted_public_keys

    slash_b64 = base64.b64encode(b"\xff" * 31 + b"\x00").decode("ascii")
    assert "/" in slash_b64
    assert resolve_trusted_public_keys([slash_b64]) == [slash_b64]

    bad_utf8 = tmp_path / "bad.pub"
    bad_utf8.write_bytes(b"\xff\xfe\xfd")
    with pytest.raises(EvidenceSigningError, match="UTF-8"):
        resolve_trusted_public_keys([str(bad_utf8)])

    oversize = tmp_path / "huge.pub"
    oversize.write_bytes(b"A" * 5000)
    with pytest.raises(EvidenceSigningError, match="4096"):
        resolve_trusted_public_keys([str(oversize)])

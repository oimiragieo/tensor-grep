import json
import sys
import types

from typer.testing import CliRunner


def test_sidecar_classify_defaults_to_fast_local_heuristics(monkeypatch):
    from tensor_grep.sidecar import _classify_payload

    class _ExplodingBackend:
        def __init__(self) -> None:
            raise AssertionError("default classify should not probe CyBERT")

    monkeypatch.delenv("TENSOR_GREP_CLASSIFY_PROVIDER", raising=False)
    monkeypatch.setitem(
        sys.modules,
        "tensor_grep.backends.cybert_backend",
        types.SimpleNamespace(CybertBackend=_ExplodingBackend),
    )

    stdout, stderr, exit_code = _classify_payload(
        ["--format", "json"],
        {"content": "INFO startup ok\nERROR database failed\n"},
    )

    assert stderr == ""
    assert exit_code == 0
    payload = json.loads(stdout)
    assert payload["classifications"] == [
        {
            "label": "info",
            "confidence": 0.8,
            "file": None,
            "path": None,
            "line": 1,
            "snippet": "INFO startup ok",
        },
        {
            "label": "error",
            "confidence": 0.95,
            "file": None,
            "path": None,
            "line": 2,
            "snippet": "ERROR database failed",
        },
    ]


def test_sidecar_classify_file_path_uses_fast_local_heuristics_by_default(monkeypatch, tmp_path):
    from tensor_grep.sidecar import _classify_payload

    class _ExplodingBackend:
        def __init__(self) -> None:
            raise AssertionError("default classify should not probe CyBERT")

    log_path = tmp_path / "app.log"
    log_path.write_text("WARNING latency is high\n", encoding="utf-8")
    monkeypatch.delenv("TENSOR_GREP_CLASSIFY_PROVIDER", raising=False)
    monkeypatch.setitem(
        sys.modules,
        "tensor_grep.backends.cybert_backend",
        types.SimpleNamespace(CybertBackend=_ExplodingBackend),
    )

    stdout, stderr, exit_code = _classify_payload(
        ["--format=json", str(log_path)],
        None,
    )

    assert stderr == ""
    assert exit_code == 0
    payload = json.loads(stdout)
    assert payload["classifications"] == [
        {
            "label": "warn",
            "confidence": 0.85,
            "file": str(log_path.resolve()),
            "path": str(log_path.resolve()),
            "line": 1,
            "snippet": "WARNING latency is high",
        }
    ]


def test_python_cli_classify_defaults_to_fast_local_heuristics(monkeypatch, tmp_path):
    from tensor_grep.cli.main import app

    class _ExplodingBackend:
        def __init__(self) -> None:
            raise AssertionError("default classify should not probe CyBERT")

    log_path = tmp_path / "app.log"
    log_path.write_text("fatal exception: cannot allocate memory\n", encoding="utf-8")
    monkeypatch.delenv("TENSOR_GREP_CLASSIFY_PROVIDER", raising=False)
    monkeypatch.setitem(
        sys.modules,
        "tensor_grep.backends.cybert_backend",
        types.SimpleNamespace(CybertBackend=_ExplodingBackend),
    )

    result = CliRunner().invoke(app, ["classify", "--format", "json", str(log_path)])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["classifications"] == [
        {
            "label": "error",
            "confidence": 0.95,
            "file": str(log_path.resolve()),
            "path": str(log_path.resolve()),
            "line": 1,
            "snippet": "fatal exception: cannot allocate memory",
        }
    ]


def test_sidecar_classify_uses_cybert_only_when_provider_is_explicit(monkeypatch):
    from tensor_grep.sidecar import _classify_payload

    class _CybertBackend:
        def classify(self, lines):
            return [{"label": "warn", "confidence": 0.77} for _line in lines]

    monkeypatch.setenv("TENSOR_GREP_CLASSIFY_PROVIDER", "cybert")
    monkeypatch.setitem(
        sys.modules,
        "tensor_grep.backends.cybert_backend",
        types.SimpleNamespace(CybertBackend=_CybertBackend),
    )

    stdout, stderr, exit_code = _classify_payload(
        ["--format=json"],
        {"content": "WARNING latency is high\n"},
    )

    assert stderr == ""
    assert exit_code == 0
    payload = json.loads(stdout)
    assert payload["classifications"] == [
        {
            "label": "warn",
            "confidence": 0.77,
            "file": None,
            "path": None,
            "line": 1,
            "snippet": "WARNING latency is high",
        }
    ]

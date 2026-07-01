import json
import sys
import types

from typer.testing import CliRunner


def test_sidecar_classify_empty_content_reports_diagnostic(monkeypatch):
    """Audit MED: classifying empty content returned exit 1 with NO stdout AND no stderr —
    zero diagnostic, unlike every other early-return branch in _classify_payload. It must
    populate stderr so a caller (e.g. classifying a zero-byte log) gets a real message."""
    from tensor_grep.sidecar import _classify_payload

    monkeypatch.delenv("TENSOR_GREP_CLASSIFY_PROVIDER", raising=False)

    stdout, stderr, exit_code = _classify_payload(["--format", "json"], {"content": ""})

    assert exit_code == 1
    assert stderr.strip()  # a non-empty explanatory message, not a silent failure


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


def test_sidecar_classify_json_reports_local_provider_metadata(monkeypatch):
    from tensor_grep.sidecar import _classify_payload

    monkeypatch.delenv("TENSOR_GREP_CLASSIFY_PROVIDER", raising=False)
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    monkeypatch.delenv("TRANSFORMERS_OFFLINE", raising=False)

    stdout, stderr, exit_code = _classify_payload(
        ["--format=json"],
        {"content": "INFO startup ok\n"},
    )

    assert stderr == ""
    assert exit_code == 0
    payload = json.loads(stdout)
    assert payload["classification_backend"] == {
        "provider_requested": "heuristic",
        "provider_used": "heuristic",
        "provider_status": "local",
        "fallback_reason": None,
        "cache": {
            "status": "not_applicable",
            "offline": {"enabled": False, "sources": []},
        },
    }


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
    assert payload["classification_backend"]["provider_status"] == "local"
    assert payload["classification_backend"]["provider_used"] == "heuristic"


def test_python_cli_classify_caps_json_output_by_default(monkeypatch, tmp_path):
    from tensor_grep.cli.main import app

    log_path = tmp_path / "app.log"
    log_path.write_text(
        "".join(f"INFO line {index}\n" for index in range(505)),
        encoding="utf-8",
    )
    monkeypatch.delenv("TENSOR_GREP_CLASSIFY_PROVIDER", raising=False)

    result = CliRunner().invoke(app, ["classify", "--format", "json", str(log_path)])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert len(payload["classifications"]) == 500
    assert payload["line_budget"] == {
        "max_lines": 500,
        "total_lines": 505,
        "emitted_lines": 500,
        "omitted_lines": 5,
        "truncated": True,
    }


def test_python_cli_classify_reports_clear_error_for_literal_input():
    from tensor_grep.cli.main import app

    result = CliRunner().invoke(
        app,
        ["classify", "--format", "json", "2026-05-26 ERROR payment retry failed"],
    )

    assert result.exit_code == 1
    assert "classify expects a file path" in result.stderr
    assert "--text/stdin literal classification is not supported yet" in result.stderr


def test_sidecar_classify_accepts_explicit_max_lines(monkeypatch):
    from tensor_grep.sidecar import _classify_payload

    monkeypatch.delenv("TENSOR_GREP_CLASSIFY_PROVIDER", raising=False)

    stdout, stderr, exit_code = _classify_payload(
        ["--format=json", "--max-lines", "2"],
        {"content": "INFO one\nWARN two\nERROR three\n"},
    )

    assert stderr == ""
    assert exit_code == 0
    payload = json.loads(stdout)
    assert len(payload["classifications"]) == 2
    assert payload["line_budget"] == {
        "max_lines": 2,
        "total_lines": 3,
        "emitted_lines": 2,
        "omitted_lines": 1,
        "truncated": True,
    }


def test_sidecar_classify_uses_cybert_only_when_provider_is_explicit(monkeypatch, tmp_path):
    from tensor_grep.sidecar import _classify_payload

    class _CybertBackend:
        def classify(self, lines):
            return [{"label": "warn", "confidence": 0.77} for _line in lines]

    monkeypatch.setenv("TENSOR_GREP_CLASSIFY_PROVIDER", "cybert")
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "missing-hf-cache"))
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    monkeypatch.delenv("TRANSFORMERS_OFFLINE", raising=False)
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
    assert payload["classification_backend"] == {
        "provider_requested": "cybert",
        "provider_used": "cybert",
        "provider_status": "provider",
        "fallback_reason": None,
        "cache": {
            "status": "missing",
            "path": str(tmp_path / "missing-hf-cache"),
            "source": "HF_HUB_CACHE",
            "offline": {"enabled": False, "sources": []},
            "local_files_only": True,
        },
    }


def test_sidecar_classify_reports_quiet_cybert_fallback_metadata(monkeypatch, tmp_path):
    from tensor_grep.sidecar import _classify_payload

    class _ExplodingCybertBackend:
        def __init__(self) -> None:
            raise RuntimeError("triton offline")

    monkeypatch.setenv("TENSOR_GREP_CLASSIFY_PROVIDER", "cybert")
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "missing-hf-cache"))
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    monkeypatch.delenv("TRANSFORMERS_OFFLINE", raising=False)
    monkeypatch.setitem(
        sys.modules,
        "tensor_grep.backends.cybert_backend",
        types.SimpleNamespace(CybertBackend=_ExplodingCybertBackend),
    )

    stdout, stderr, exit_code = _classify_payload(
        ["--format=json"],
        {"content": "WARNING latency is high\n"},
    )

    assert stderr == ""
    assert exit_code == 0
    payload = json.loads(stdout)
    assert payload["classifications"][0]["label"] == "warn"
    assert payload["classification_backend"]["provider_requested"] == "cybert"
    assert payload["classification_backend"]["provider_used"] == "heuristic"
    assert payload["classification_backend"]["provider_status"] == "fallback"
    assert "RuntimeError: triton offline" in payload["classification_backend"]["fallback_reason"]
    assert payload["classification_backend"]["cache"]["status"] == "missing"
    assert payload["classification_backend"]["cache"]["offline"] == {
        "enabled": False,
        "sources": [],
    }


def test_sidecar_classify_suppresses_provider_stdout_stderr_noise(monkeypatch, tmp_path):
    from tensor_grep.sidecar import _classify_payload

    class _NoisyCybertBackend:
        def classify(self, lines):
            print("provider stdout noise")
            print("provider stderr noise", file=sys.stderr)
            return [{"label": "warn", "confidence": 0.77} for _line in lines]

    monkeypatch.setenv("TENSOR_GREP_CLASSIFY_PROVIDER", "cybert")
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "missing-hf-cache"))
    monkeypatch.setitem(
        sys.modules,
        "tensor_grep.backends.cybert_backend",
        types.SimpleNamespace(CybertBackend=_NoisyCybertBackend),
    )

    stdout, stderr, exit_code = _classify_payload(
        ["--format=json"],
        {"content": "WARNING latency is high\n"},
    )

    assert stderr == ""
    assert exit_code == 0
    assert "provider stdout noise" not in stdout
    assert "provider stderr noise" not in stdout
    payload = json.loads(stdout)
    assert payload["classifications"][0]["label"] == "warn"


def test_sidecar_classify_reports_hf_cache_and_offline_status(monkeypatch, tmp_path):
    from tensor_grep.sidecar import _classify_payload

    class _CybertBackend:
        def classify(self, lines):
            return [{"label": "warn", "confidence": 0.77} for _line in lines]

    cache_dir = tmp_path / "hf-cache"
    cache_dir.mkdir()
    monkeypatch.setenv("TENSOR_GREP_CLASSIFY_PROVIDER", "cybert")
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("HF_HUB_CACHE", str(cache_dir))
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
    assert payload["classification_backend"]["cache"] == {
        "status": "available",
        "path": str(cache_dir),
        "source": "HF_HUB_CACHE",
        "offline": {"enabled": True, "sources": ["HF_HUB_OFFLINE"]},
        "local_files_only": True,
    }

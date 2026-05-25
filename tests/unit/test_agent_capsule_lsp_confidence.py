from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tensor_grep.cli import agent_capsule, repo_map


def _context_payload(
    *,
    primary_file: Path,
    alternative_file: Path,
    lsp_primary: bool,
    lsp_alternative: bool = False,
    include_validation: bool = True,
) -> dict[str, Any]:
    symbol = "create_invoice"
    primary_symbol: dict[str, Any] = {
        "name": symbol,
        "kind": "function",
        "file": str(primary_file),
        "line": 1,
        "start_line": 1,
        "end_line": 2,
        "score": 11,
    }
    if lsp_primary:
        primary_symbol.update({
            "semantic_provider": "hybrid",
            "provenance": "lsp-python",
            "lsp_provider_response": True,
            "lsp_proof": True,
            "lsp_operation": "textDocument/definition",
            "lsp_resolution_basis": "native-definition-anchor",
        })
    validation_plan = (
        [
            {
                "runner": "pytest",
                "scope": "symbol",
                "target": symbol,
                "command": "python -m pytest tests/test_invoice.py",
                "confidence": 0.8,
            }
        ]
        if include_validation
        else []
    )
    validation_alignment = {
        "status": "aligned",
        "kept_count": 1,
        "filtered_count": 0,
    }
    return {
        "routing_backend": "RepoMap",
        "routing_reason": "context-render",
        "semantic_provider": "hybrid" if lsp_primary else "native",
        "files": [str(primary_file), str(alternative_file)],
        "file_matches": [
            {
                "path": str(alternative_file),
                "score": 11,
                "reasons": ["definition"],
                "provenance": ["parser-backed", "heuristic"],
            }
        ],
        "sources": [
            {
                "file": str(primary_file),
                "symbol": symbol,
                "name": symbol,
                "start_line": 1,
                "end_line": 2,
                "source": "def create_invoice():\n    return None\n",
            }
        ],
        "validation_commands": ["python -m pytest tests/test_invoice.py"]
        if include_validation
        else [],
        "edit_plan_seed": {
            "primary_file": str(primary_file),
            "primary_symbol": primary_symbol,
            "primary_span": {"start_line": 1, "end_line": 2},
            "confidence": {"overall": 0.9},
            "validation_plan": validation_plan,
            "validation_commands": ["python -m pytest tests/test_invoice.py"]
            if include_validation
            else [],
            "validation_alignment": validation_alignment,
            "edit_ordering": [str(primary_file)],
        },
        "navigation_pack": {
            "primary_target": {
                "file": str(primary_file),
                "symbol": symbol,
                "kind": "function",
                "start_line": 1,
                "end_line": 2,
                "confidence": {"overall": 0.9},
                **{
                    key: value
                    for key, value in primary_symbol.items()
                    if key
                    in {
                        "semantic_provider",
                        "provenance",
                        "lsp_provider_response",
                        "lsp_proof",
                        "lsp_operation",
                        "lsp_resolution_basis",
                    }
                },
            },
            "follow_up_reads": [],
        },
        "candidate_edit_targets": {
            "files": [str(primary_file), str(alternative_file)],
            "symbols": [
                {
                    "name": symbol,
                    "kind": "function",
                    "file": str(alternative_file),
                    "line": 1,
                    "start_line": 1,
                    "end_line": 2,
                    "score": 11,
                    **(
                        {
                            "semantic_provider": "hybrid",
                            "provenance": "lsp-python",
                            "lsp_provider_response": True,
                            "lsp_proof": True,
                            "lsp_operation": "textDocument/definition",
                            "lsp_resolution_basis": "native-definition-anchor",
                        }
                        if lsp_alternative
                        else {}
                    ),
                }
            ],
            "tests": [],
            "spans": [],
        },
        "context_consistency": {
            "primary_file_included": True,
            "rendered_context_includes_primary": True,
        },
    }


def test_capsule_lsp_boost_resolves_single_lsp_backed_tie(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    primary_file = tmp_path / "payments.py"
    alternative_file = tmp_path / "billing.py"
    primary_file.write_text("def create_invoice():\n    return None\n", encoding="utf-8")
    alternative_file.write_text("def create_invoice():\n    return None\n", encoding="utf-8")
    observed_provider: list[str] = []

    def fake_context_render(*args: object, **kwargs: object) -> dict[str, Any]:
        _ = args
        observed_provider.append(str(kwargs.get("semantic_provider")))
        return _context_payload(
            primary_file=primary_file.resolve(),
            alternative_file=alternative_file.resolve(),
            lsp_primary=True,
        )

    monkeypatch.setenv(agent_capsule._CAPSULE_LSP_CONFIDENCE_BOOST_ENV, "1")
    monkeypatch.setattr(repo_map, "build_context_render", fake_context_render)

    payload = agent_capsule.build_agent_capsule(
        "change create_invoice behavior",
        tmp_path,
        include_blast_radius=False,
        max_tokens=None,
    )

    assert observed_provider == ["hybrid"]
    assert payload["semantic_provider"] == "hybrid"
    assert payload["primary_target"]["lsp_proof"] is True
    assert payload["primary_target"]["lsp_provider_response"] is True
    assert "lsp-confirmed" in payload["primary_target"]["evidence"]
    assert payload["confidence"]["overall"] == 0.85
    assert payload["primary_target"]["confidence"] == 0.85
    assert payload["ambiguity"]["status"] == "tie_resolved"
    assert payload["ambiguity"]["resolved_by"] == "lsp"
    assert payload["ambiguity"]["requires_confirmation"] is False
    assert payload["ask_user_before_editing"]["required"] is False
    assert payload["context_consistency"]["lsp_confidence_boost_eligible"] is True


def test_capsule_lsp_boost_is_feature_gated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    primary_file = tmp_path / "payments.py"
    alternative_file = tmp_path / "billing.py"
    primary_file.write_text("def create_invoice():\n    return None\n", encoding="utf-8")
    alternative_file.write_text("def create_invoice():\n    return None\n", encoding="utf-8")
    monkeypatch.delenv(agent_capsule._CAPSULE_LSP_CONFIDENCE_BOOST_ENV, raising=False)
    monkeypatch.setattr(
        repo_map,
        "build_context_render",
        lambda *args, **kwargs: _context_payload(
            primary_file=primary_file.resolve(),
            alternative_file=alternative_file.resolve(),
            lsp_primary=True,
            include_validation=False,
        ),
    )

    payload = agent_capsule.build_agent_capsule(
        "change create_invoice behavior",
        tmp_path,
        include_blast_radius=False,
        max_tokens=None,
    )

    assert payload["confidence"]["overall"] == 0.74
    assert payload["ambiguity"]["status"] == "tie_requires_confirmation"
    assert payload["context_consistency"]["lsp_confidence_boost_enabled"] is False
    assert payload["context_consistency"]["lsp_confidence_boost_eligible"] is False


def test_capsule_lsp_boost_keeps_confirmation_when_tied_alternative_is_lsp_backed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    primary_file = tmp_path / "payments.py"
    alternative_file = tmp_path / "billing.py"
    primary_file.write_text("def create_invoice():\n    return None\n", encoding="utf-8")
    alternative_file.write_text("def create_invoice():\n    return None\n", encoding="utf-8")
    monkeypatch.setenv(agent_capsule._CAPSULE_LSP_CONFIDENCE_BOOST_ENV, "1")
    monkeypatch.setattr(
        repo_map,
        "build_context_render",
        lambda *args, **kwargs: _context_payload(
            primary_file=primary_file.resolve(),
            alternative_file=alternative_file.resolve(),
            lsp_primary=True,
            lsp_alternative=True,
            include_validation=False,
        ),
    )

    payload = agent_capsule.build_agent_capsule(
        "change create_invoice behavior",
        tmp_path,
        include_blast_radius=False,
        max_tokens=None,
    )

    assert payload["ambiguity"]["status"] == "tie_requires_confirmation"
    assert payload["ambiguity"]["requires_confirmation"] is True
    assert payload["ambiguity"]["tie_count"] == 1
    tied_targets = payload["ambiguity"]["tied_alternative_targets"]
    assert tied_targets[0]["lsp_proof"] is True
    assert tied_targets[0]["lsp_provider_response"] is True
    assert payload["ask_user_before_editing"]["required"] is True
    assert payload["context_consistency"]["alternative_confidence_tie"] is True


def test_capsule_lsp_boost_requires_supported_target_language(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    primary_file = tmp_path / "payments.go"
    alternative_file = tmp_path / "billing.go"
    primary_file.write_text("func create_invoice() {}\n", encoding="utf-8")
    alternative_file.write_text("func create_invoice() {}\n", encoding="utf-8")
    monkeypatch.setenv(agent_capsule._CAPSULE_LSP_CONFIDENCE_BOOST_ENV, "1")
    monkeypatch.setattr(
        repo_map,
        "build_context_render",
        lambda *args, **kwargs: _context_payload(
            primary_file=primary_file.resolve(),
            alternative_file=alternative_file.resolve(),
            lsp_primary=True,
            include_validation=False,
        ),
    )

    payload = agent_capsule.build_agent_capsule(
        "change create_invoice behavior",
        tmp_path,
        include_blast_radius=False,
        max_tokens=None,
    )

    assert payload["context_consistency"]["primary_target_language"] is None
    assert payload["context_consistency"]["lsp_confidence_boost_eligible"] is False
    assert payload["confidence"]["overall"] == 0.74
    assert payload["ambiguity"]["status"] == "tie_requires_confirmation"


def test_capsule_lsp_boost_does_not_override_lower_trust_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    primary_file = tmp_path / "payments.py"
    alternative_file = tmp_path / "billing.py"
    primary_file.write_text("def create_invoice():\n    return None\n", encoding="utf-8")
    alternative_file.write_text("def create_invoice():\n    return None\n", encoding="utf-8")
    monkeypatch.setenv(agent_capsule._CAPSULE_LSP_CONFIDENCE_BOOST_ENV, "1")
    monkeypatch.setattr(
        repo_map,
        "build_context_render",
        lambda *args, **kwargs: _context_payload(
            primary_file=primary_file.resolve(),
            alternative_file=alternative_file.resolve(),
            lsp_primary=True,
        ),
    )

    payload = agent_capsule.build_agent_capsule(
        "javascript create_invoice behavior",
        tmp_path,
        include_blast_radius=False,
        max_tokens=None,
    )

    assert payload["context_consistency"]["lsp_confidence_boost_eligible"] is True
    assert payload["context_consistency"]["confidence_cap"] == 0.55
    assert payload["confidence"]["overall"] == 0.55
    assert payload["primary_target"]["confidence"] == 0.55

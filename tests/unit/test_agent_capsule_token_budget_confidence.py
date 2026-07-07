"""F4: a token-budget primary-file omission must not be treated the same as a genuine misroute.

`_confidence` clamps `overall` to 0.55 whenever the primary file is missing from the capsule's
rendered snippets -- this is the v1.17.13 degrade-to-ask safety floor and it correctly catches a
primary target that ranking never selected/rendered at all. But it ALSO fires when the primary
WAS correctly identified and its source simply didn't fit the capsule's own (default 1200,
here 200) token budget -- a much weaker signal that should not, by itself, force a human
confirmation when the primary is independently corroborated (query names it explicitly AND
blast-radius finds real callers).

These tests build a real two-file project so blast-radius call-site collection
(`_collect_capsule_call_site_evidence`) runs for real, while `repo_map.build_context_render` is
monkeypatched to deterministically control which source lands in vs. out of the capsule's token
budget (mirrors the pattern in test_agent_capsule_lsp_confidence.py).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tensor_grep.cli import agent_capsule, repo_map

_PRIMARY_SYMBOL = "handle_widget_request"
_CALLER_SYMBOL = "process_incoming_request"
_OVERSIZED_SOURCE = "def handle_widget_request(payload):\n" + ("    pass  # padding\n" * 100)
_SMALL_CALLER_SOURCE = (
    "def process_incoming_request(payload):\n    return handle_widget_request(payload)\n"
)


def _write_project(tmp_path: Path) -> dict[str, Path]:
    project = tmp_path / "workspace"
    project.mkdir()
    (project / "pyproject.toml").write_text(
        '[project]\nname = "sample"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    handler_file = project / "handler.py"
    handler_file.write_text(
        "def handle_widget_request(payload):\n    return payload\n",
        encoding="utf-8",
    )
    caller_file = project / "caller.py"
    caller_file.write_text(_SMALL_CALLER_SOURCE, encoding="utf-8")
    return {"project": project, "handler": handler_file, "caller": caller_file}


def _context_payload(
    *,
    primary_file: Path,
    caller_file: Path,
    primary_symbol: str,
) -> dict[str, Any]:
    return {
        "routing_backend": "RepoMap",
        "routing_reason": "context-render",
        "semantic_provider": "native",
        "files": [str(primary_file), str(caller_file)],
        "sources": [
            {
                "file": str(primary_file),
                "symbol": primary_symbol,
                "name": primary_symbol,
                "start_line": 1,
                "end_line": 1,
                "source": _OVERSIZED_SOURCE,
            },
            {
                "file": str(caller_file),
                "symbol": _CALLER_SYMBOL,
                "name": _CALLER_SYMBOL,
                "start_line": 1,
                "end_line": 2,
                "source": _SMALL_CALLER_SOURCE,
            },
        ],
        "validation_commands": ["uv run pytest -q"],
        "edit_plan_seed": {
            "primary_file": str(primary_file),
            "primary_symbol": {"name": primary_symbol, "kind": "function"},
            "primary_span": {"start_line": 1, "end_line": 1},
            "confidence": {"overall": 0.9},
            "validation_plan": [
                {
                    "runner": "pytest",
                    "scope": "repo",
                    "target": "",
                    "command": "uv run pytest -q",
                    "confidence": 0.55,
                    "detection": "detected",
                }
            ],
            "validation_commands": ["uv run pytest -q"],
            "validation_alignment": {"status": "aligned", "kept_count": 1, "filtered_count": 0},
            "edit_ordering": [str(primary_file)],
        },
        "navigation_pack": {
            "primary_target": {
                "file": str(primary_file),
                "symbol": primary_symbol,
                "kind": "function",
                "start_line": 1,
                "end_line": 1,
                "confidence": {"overall": 0.9},
            },
            "follow_up_reads": [],
        },
        "candidate_edit_targets": {"files": [str(primary_file)], "symbols": [], "tests": []},
        "context_consistency": {
            "primary_file_included": True,
            "rendered_context_includes_primary": True,
        },
    }


def test_capsule_uplifts_confidence_for_corroborated_token_budget_omission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _write_project(tmp_path)
    monkeypatch.setattr(
        repo_map,
        "build_context_render",
        lambda *args, **kwargs: _context_payload(
            primary_file=paths["handler"].resolve(),
            caller_file=paths["caller"].resolve(),
            primary_symbol=_PRIMARY_SYMBOL,
        ),
    )

    payload = agent_capsule.build_agent_capsule(
        _PRIMARY_SYMBOL,
        paths["project"],
        max_tokens=200,
    )

    # The primary file's snippet really was cut by the tight token budget (not a genuine
    # ranking miss) -- this is the mechanism F4 targets.
    consistency = payload["context_consistency"]
    assert consistency["capsule_primary_file_omitted"] is True
    assert consistency["capsule_primary_file_omission_reason"] == "token budget exhausted"
    assert consistency["primary_file_included"] is True
    assert consistency["rendered_context_includes_primary"] is True

    # Corroboration held (query names the symbol explicitly + blast-radius found a real caller),
    # so the capsule uplifts past the 0.55 safety floor and stops demanding confirmation for it.
    assert payload["call_site_evidence"]["status"] == "collected"
    assert payload["confidence"]["overall"] >= 0.75
    assert payload["primary_target"]["confidence"] >= 0.75
    assert payload["ask_user_before_editing"]["required"] is False


def test_capsule_keeps_safety_floor_when_symbol_not_named_and_no_call_sites(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same token-budget-omission mechanism, but the query never names the primary symbol, so
    blast-radius call-site collection is skipped (no corroboration). The 0.55 degrade-to-ask
    floor MUST still hold -- this is the TRAP the fix guards against (uplifting on the bare
    string "token budget" without corroboration would reopen a confident-wrong-target hole).
    """
    paths = _write_project(tmp_path)
    monkeypatch.setattr(
        repo_map,
        "build_context_render",
        lambda *args, **kwargs: _context_payload(
            primary_file=paths["handler"].resolve(),
            caller_file=paths["caller"].resolve(),
            primary_symbol=_PRIMARY_SYMBOL,
        ),
    )

    payload = agent_capsule.build_agent_capsule(
        "update the request handler",  # does not name handle_widget_request explicitly
        paths["project"],
        max_tokens=200,
    )

    assert payload["context_consistency"]["capsule_primary_file_omitted"] is True
    assert payload["call_site_evidence"]["status"] != "collected"
    assert payload["confidence"]["overall"] <= 0.55
    assert payload["ask_user_before_editing"]["required"] is True


def test_capsule_keeps_safety_floor_for_genuine_misroute_even_with_corroboration_signals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A genuine misroute (primary never ranked/selected at all -- `primary_file_included` is
    False) must stay at the 0.55 floor even when the query names the symbol and a caller exists
    -- corroboration must never override the "never ranked" signal, only the "cut by budget"
    signal.
    """
    paths = _write_project(tmp_path)

    def fake_context_render(*args: object, **kwargs: object) -> dict[str, Any]:
        payload = _context_payload(
            primary_file=paths["handler"].resolve(),
            caller_file=paths["caller"].resolve(),
            primary_symbol=_PRIMARY_SYMBOL,
        )
        payload["context_consistency"] = {
            "primary_file_included": False,
            "rendered_context_includes_primary": False,
        }
        return payload

    monkeypatch.setattr(repo_map, "build_context_render", fake_context_render)

    payload = agent_capsule.build_agent_capsule(
        _PRIMARY_SYMBOL,
        paths["project"],
        max_tokens=200,
    )

    assert payload["context_consistency"]["primary_file_included"] is False
    assert payload["confidence"]["overall"] <= 0.55
    assert payload["ask_user_before_editing"]["required"] is True

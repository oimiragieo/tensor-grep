"""CodeAnchor-style inline structural annotation (arXiv 2606.26979, "How Much Static Structure Do
Code Agents Need?"): the `tg agent` capsule renders a compact caller/fan-in fact as a plain-text
comment INSIDE the primary target's rendered source excerpt, ambient to the code an agent already
reads, instead of requiring a separate tool call. The paper's own reported win was +3.4pp Pass@1
and halved run-to-run variance from exactly this ambient placement (not a structured sibling
field), and it targets the cross-paper "agents skip the graph tool 58% of the time" adoption gap
(CodeCompass 2602.20048).

SCOPE (verify-plan-against-code finding, see `agent_capsule._apply_inline_caller_annotation`'s
docstring): this capsule already collects verified call-site evidence for the PRIMARY target ONLY
(`_collect_capsule_call_site_evidence[_from_map]`, gated on confidence>=0.75 + an
explicitly-requested symbol) via a blast-radius scan the capsule pays for regardless of this
feature. Annotating that one already-evidenced snippet is a pure rendering-layer change; every
other rendered snippet is deliberately left unannotated rather than paying for a fresh per-symbol
blast-radius scan this function does not already run.

THE TRAP these tests guard against: DAR (`_collect_outbound_dependencies`) resolves call-token line
numbers as `start_line + offset` into the PRIMARY snippet's own rendered `source`
(`agent_capsule._outbound_dependency_call_tokens`). Prepending the annotation line before DAR runs
would shift every subsequent line off by one in DAR's own arithmetic -- `test_inline_caller_
annotation_runs_after_dar_ordering_contract` pins the fix (annotate strictly after DAR) as an
ordering contract, not an implementation detail.

Most tests here build REAL on-disk fixture projects and drive the full `build_agent_capsule`
pipeline (real repo scan, real blast-radius call-site evidence, real `_enclosing_symbol_for_line`
resolution) -- the same "prove it against real data" discipline `test_agent_capsule_outbound_deps.py`
uses for DAR. A handful of pure-function tests exercise the small rendering helpers directly for
determinism (language-comment mapping, annotation text variants, multi-snippet targeting).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from tensor_grep.cli import agent_capsule

_TARGET_SYMBOL = "target_fn"
_CALLER_SYMBOL = "process_incoming_request"


def _write_project_with_one_caller(tmp_path: Path) -> dict[str, Path]:
    project = tmp_path / "workspace"
    project.mkdir()
    (project / "pyproject.toml").write_text(
        '[project]\nname = "sample"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    handler = project / "handler.py"
    handler.write_text(
        f"def {_TARGET_SYMBOL}(payload):\n    return payload\n",
        encoding="utf-8",
    )
    caller = project / "caller.py"
    caller.write_text(
        f"from handler import {_TARGET_SYMBOL}\n\n\n"
        f"def {_CALLER_SYMBOL}(payload):\n    return {_TARGET_SYMBOL}(payload)\n",
        encoding="utf-8",
    )
    return {"project": project, "handler": handler, "caller": caller}


def _write_project_with_uncalled_leaf(tmp_path: Path) -> dict[str, Path]:
    project = tmp_path / "workspace"
    project.mkdir()
    (project / "pyproject.toml").write_text(
        '[project]\nname = "sample"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    handler = project / "handler.py"
    handler.write_text(
        "def unreferenced_leaf_fn(payload):\n    return payload\n",
        encoding="utf-8",
    )
    return {"project": project, "handler": handler}


# ---------------------------------------------------------------------------------------------
# (1) Opt-in flag: defaults OFF, toggled by TG_CAPSULE_INLINE_CALLERS.
# ---------------------------------------------------------------------------------------------


def test_inline_caller_annotation_defaults_off_when_env_unset() -> None:
    saved = os.environ.pop("TG_CAPSULE_INLINE_CALLERS", None)
    try:
        assert agent_capsule._capsule_inline_caller_annotation_enabled() is False
        os.environ["TG_CAPSULE_INLINE_CALLERS"] = "1"
        assert agent_capsule._capsule_inline_caller_annotation_enabled() is True
        os.environ["TG_CAPSULE_INLINE_CALLERS"] = "0"
        assert agent_capsule._capsule_inline_caller_annotation_enabled() is False
    finally:
        if saved is None:
            os.environ.pop("TG_CAPSULE_INLINE_CALLERS", None)
        else:
            os.environ["TG_CAPSULE_INLINE_CALLERS"] = saved


def test_inline_caller_annotation_absent_by_default_leaves_source_untouched(
    tmp_path: Path,
) -> None:
    paths = _write_project_with_one_caller(tmp_path)
    os.environ.pop("TG_CAPSULE_INLINE_CALLERS", None)

    payload = agent_capsule.build_agent_capsule(_TARGET_SYMBOL, paths["project"])

    snippet = payload["snippets"][0]
    assert snippet["source"] == f"def {_TARGET_SYMBOL}(payload):\n    return payload\n"
    assert "inline_structural_annotation" not in snippet


# ---------------------------------------------------------------------------------------------
# (2) Real end-to-end: one real caller -> honest count + resolved top-caller name.
# ---------------------------------------------------------------------------------------------


def test_inline_caller_annotation_renders_count_and_top_caller_for_real_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _write_project_with_one_caller(tmp_path)
    monkeypatch.setenv("TG_CAPSULE_INLINE_CALLERS", "1")

    payload = agent_capsule.build_agent_capsule(_TARGET_SYMBOL, paths["project"])

    assert payload["call_site_evidence"]["status"] == "collected"
    snippet = payload["snippets"][0]
    expected_annotation = f"# tg: callers=1 (top: {_CALLER_SYMBOL})"
    assert snippet["source"] == (
        f"{expected_annotation}\ndef {_TARGET_SYMBOL}(payload):\n    return payload\n"
    )
    # The real code stays byte-identical AFTER the one prepended comment line -- copy-usability
    # is preserved, the fact lives strictly in a comment, never inside the code.
    assert snippet["source"].split("\n", 1)[1] == (
        f"def {_TARGET_SYMBOL}(payload):\n    return payload\n"
    )

    line_map = snippet["line_map"]
    assert line_map[0] == {"line": None, "text": expected_annotation}
    assert line_map[1]["line"] == 1
    assert line_map[2]["line"] == 2

    annotation = snippet["inline_structural_annotation"]
    assert annotation == {
        "applied": True,
        "kind": "callers",
        "callers_returned": 1,
        "callers_truncated": False,
        "top_callers": [_CALLER_SYMBOL],
    }

    # Token accounting stays honest: the snippet's own token_estimate grows by exactly the
    # annotation line's own estimate (never silently absorbed/ignored).
    base_source = f"def {_TARGET_SYMBOL}(payload):\n    return payload\n"
    from tensor_grep.cli import repo_map

    assert snippet["token_estimate"] == repo_map._estimate_tokens(
        base_source
    ) + repo_map._estimate_tokens(expected_annotation)


def test_inline_caller_annotation_cli_json_round_trips(tmp_path: Path) -> None:
    """The feature must survive a real --json serialization (the JSON contract stays intact --
    additive metadata, not a shape break)."""
    paths = _write_project_with_one_caller(tmp_path)
    os.environ["TG_CAPSULE_INLINE_CALLERS"] = "1"
    try:
        payload = agent_capsule.build_agent_capsule(_TARGET_SYMBOL, paths["project"])
        encoded = json.dumps(payload)
        decoded = json.loads(encoded)
        assert decoded["snippets"][0]["inline_structural_annotation"]["callers_returned"] == 1
        assert decoded["snippets"][0]["line_map"][0]["line"] is None
    finally:
        os.environ.pop("TG_CAPSULE_INLINE_CALLERS", None)


# ---------------------------------------------------------------------------------------------
# (3) Real end-to-end: a genuinely uncalled symbol -> honest "callers=0", never fabricated.
# ---------------------------------------------------------------------------------------------


def test_inline_caller_annotation_renders_honest_zero_when_no_callers_found(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _write_project_with_uncalled_leaf(tmp_path)
    monkeypatch.setenv("TG_CAPSULE_INLINE_CALLERS", "1")

    payload = agent_capsule.build_agent_capsule("unreferenced_leaf_fn", paths["project"])

    assert payload["call_site_evidence"]["status"] == "collected_no_call_sites"
    snippet = payload["snippets"][0]
    assert snippet["source"].startswith("# tg: callers=0\n")
    assert snippet["inline_structural_annotation"]["callers_returned"] == 0
    assert snippet["inline_structural_annotation"]["top_callers"] == []


# ---------------------------------------------------------------------------------------------
# (4) Graceful absence: evidence never collected -> no annotation, no fabricated "callers=0".
# ---------------------------------------------------------------------------------------------


def test_inline_caller_annotation_absent_when_call_site_evidence_disabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _write_project_with_one_caller(tmp_path)
    monkeypatch.setenv("TG_CAPSULE_INLINE_CALLERS", "1")

    payload = agent_capsule.build_agent_capsule(
        _TARGET_SYMBOL,
        paths["project"],
        include_blast_radius=False,
    )

    assert payload["call_site_evidence"]["status"] == "disabled"
    snippet = payload["snippets"][0]
    assert snippet["source"] == f"def {_TARGET_SYMBOL}(payload):\n    return payload\n"
    assert "inline_structural_annotation" not in snippet


# ---------------------------------------------------------------------------------------------
# (5) Token budget: fail closed rather than silently exceed --max-tokens.
# ---------------------------------------------------------------------------------------------


def test_inline_caller_annotation_fails_closed_on_tight_token_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _write_project_with_one_caller(tmp_path)
    monkeypatch.setenv("TG_CAPSULE_INLINE_CALLERS", "1")
    from tensor_grep.cli import repo_map

    base_source = f"def {_TARGET_SYMBOL}(payload):\n    return payload\n"
    exact_fit_tokens = repo_map._estimate_tokens(base_source)

    payload = agent_capsule.build_agent_capsule(
        _TARGET_SYMBOL,
        paths["project"],
        max_tokens=exact_fit_tokens,
    )

    assert payload["call_site_evidence"]["status"] == "collected"
    snippet = payload["snippets"][0]
    # The base snippet still fits and renders -- only the annotation is dropped, never the code.
    assert snippet["source"] == base_source
    assert "inline_structural_annotation" not in snippet


def test_inline_caller_annotation_fits_when_budget_has_room(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _write_project_with_one_caller(tmp_path)
    monkeypatch.setenv("TG_CAPSULE_INLINE_CALLERS", "1")

    payload = agent_capsule.build_agent_capsule(
        _TARGET_SYMBOL,
        paths["project"],
        max_tokens=200,
    )

    snippet = payload["snippets"][0]
    assert snippet["source"].startswith("# tg: callers=1")
    assert snippet["inline_structural_annotation"]["applied"] is True


# ---------------------------------------------------------------------------------------------
# (6) Kill switch: explicit off-values match the unset-env baseline byte-for-byte.
# ---------------------------------------------------------------------------------------------


def test_inline_caller_annotation_kill_switch_matches_default_off_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _write_project_with_one_caller(tmp_path)
    os.environ.pop("TG_CAPSULE_INLINE_CALLERS", None)
    payload_unset = agent_capsule.build_agent_capsule(_TARGET_SYMBOL, paths["project"])

    for off_value in ("0", "false", "False", "no", "off", ""):
        monkeypatch.setenv("TG_CAPSULE_INLINE_CALLERS", off_value)
        payload_off = agent_capsule.build_agent_capsule(_TARGET_SYMBOL, paths["project"])
        assert json.dumps(payload_off, sort_keys=True) == json.dumps(
            payload_unset, sort_keys=True
        ), off_value


# ---------------------------------------------------------------------------------------------
# (7) Isolation: never mutates confidence/consistency/ask-user/related_call_sites state.
# ---------------------------------------------------------------------------------------------


def test_inline_caller_annotation_never_mutates_confidence_or_consistency_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _write_project_with_one_caller(tmp_path)

    os.environ.pop("TG_CAPSULE_INLINE_CALLERS", None)
    payload_off = agent_capsule.build_agent_capsule(_TARGET_SYMBOL, paths["project"])

    monkeypatch.setenv("TG_CAPSULE_INLINE_CALLERS", "1")
    payload_on = agent_capsule.build_agent_capsule(_TARGET_SYMBOL, paths["project"])

    assert payload_on["snippets"][0]["source"] != payload_off["snippets"][0]["source"]
    assert payload_on["confidence"] == payload_off["confidence"]
    assert payload_on["ask_user_before_editing"] == payload_off["ask_user_before_editing"]
    assert payload_on["context_consistency"] == payload_off["context_consistency"]
    assert payload_on["primary_target"] == payload_off["primary_target"]
    assert payload_on["related_call_sites"] == payload_off["related_call_sites"]
    assert payload_on["call_site_evidence"] == payload_off["call_site_evidence"]


# ---------------------------------------------------------------------------------------------
# (8) Ordering contract: MUST run after DAR, so DAR's own line arithmetic over the primary
# snippet's source is never disturbed by the prepended annotation line.
# ---------------------------------------------------------------------------------------------


def test_inline_caller_annotation_runs_after_dar_ordering_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "m.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    monkeypatch.setenv("TG_CAPSULE_INLINE_CALLERS", "1")

    call_order: list[str] = []
    original_dar = agent_capsule._collect_outbound_dependencies
    original_annotate = agent_capsule._apply_inline_caller_annotation

    def _spy_dar(*args: Any, **kwargs: Any) -> Any:
        call_order.append("dar")
        return original_dar(*args, **kwargs)

    def _spy_annotate(*args: Any, **kwargs: Any) -> Any:
        call_order.append("annotate")
        return original_annotate(*args, **kwargs)

    monkeypatch.setattr(agent_capsule, "_collect_outbound_dependencies", _spy_dar)
    monkeypatch.setattr(agent_capsule, "_apply_inline_caller_annotation", _spy_annotate)

    agent_capsule.build_agent_capsule("f", str(tmp_path))

    assert call_order == ["dar", "annotate"]


def test_dar_call_token_line_numbers_would_shift_if_annotation_ran_first() -> None:
    """Documents the MECHANISM the ordering contract above guards against: DAR's own tokenizer
    computes each call token's absolute line as `start_line + offset`-within-`source`. Prepending
    a line to `source` before this runs shifts every subsequent line off by one."""
    base_source = "def f():\n    return g()\n"
    start_line = 10
    tokens_unannotated = agent_capsule._outbound_dependency_call_tokens(base_source, start_line)
    assert ("g", 11) in tokens_unannotated

    annotated_source = "# tg: callers=1 (top: caller)\n" + base_source
    tokens_if_annotated_first = agent_capsule._outbound_dependency_call_tokens(
        annotated_source, start_line
    )
    # Off by one -- exactly the corruption the ordering contract prevents.
    assert ("g", 12) in tokens_if_annotated_first
    assert ("g", 11) not in tokens_if_annotated_first


# ---------------------------------------------------------------------------------------------
# (9) Direct helper tests: comment-prefix language mapping (fail-closed on unknown languages).
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("file_name", "expected_prefix"),
    [
        ("mod.py", "#"),
        ("mod.js", "//"),
        ("mod.jsx", "//"),
        ("mod.ts", "//"),
        ("mod.tsx", "//"),
        ("mod.mjs", "//"),
        ("mod.cjs", "//"),
        ("mod.rs", "//"),
        ("mod.go", None),
        ("mod.md", None),
        ("mod.json", None),
        ("mod.txt", None),
        ("mod", None),
    ],
)
def test_inline_annotation_comment_prefix_by_language(
    file_name: str, expected_prefix: str | None
) -> None:
    assert agent_capsule._inline_annotation_comment_prefix(f"/repo/{file_name}") == expected_prefix


# ---------------------------------------------------------------------------------------------
# (10) Direct helper tests: annotation text variants (never fabricates on non-collected status).
# ---------------------------------------------------------------------------------------------


def test_build_inline_caller_annotation_text_collected_with_names() -> None:
    evidence = {"status": "collected", "returned_call_sites": 2, "omitted_call_sites": 0}
    text = agent_capsule._build_inline_caller_annotation_text("#", evidence, ["foo", "bar"])
    assert text == "# tg: callers=2 (top: foo, bar)"


def test_build_inline_caller_annotation_text_collected_truncated() -> None:
    evidence = {"status": "collected", "returned_call_sites": 8, "omitted_call_sites": 3}
    text = agent_capsule._build_inline_caller_annotation_text("//", evidence, ["foo"])
    assert text == "// tg: callers=8+ (top: foo)"


def test_build_inline_caller_annotation_text_collected_no_names() -> None:
    evidence = {"status": "collected", "returned_call_sites": 1, "omitted_call_sites": 0}
    text = agent_capsule._build_inline_caller_annotation_text("#", evidence, [])
    assert text == "# tg: callers=1"


def test_build_inline_caller_annotation_text_zero_callers() -> None:
    evidence = {
        "status": "collected_no_call_sites",
        "returned_call_sites": 0,
        "omitted_call_sites": 0,
    }
    text = agent_capsule._build_inline_caller_annotation_text("#", evidence, [])
    assert text == "# tg: callers=0"


@pytest.mark.parametrize("status", ["skipped", "disabled", "error"])
def test_build_inline_caller_annotation_text_none_when_not_collected(status: str) -> None:
    evidence = {"status": status}
    assert agent_capsule._build_inline_caller_annotation_text("#", evidence, ["foo"]) is None


def test_build_inline_caller_annotation_text_partial_forces_truncated_marker() -> None:
    evidence = {
        "status": "collected",
        "returned_call_sites": 1,
        "omitted_call_sites": 0,
        "partial": True,
    }
    text = agent_capsule._build_inline_caller_annotation_text("#", evidence, [])
    assert text == "# tg: callers=1+"


# ---------------------------------------------------------------------------------------------
# (11) Direct helper tests: top-caller name resolution (dedupe, cap, skip-unresolved).
# ---------------------------------------------------------------------------------------------


def test_top_caller_symbol_names_dedupes_caps_and_skips_unresolved() -> None:
    rm = {
        "symbols": [
            {
                "file": "/repo/caller.py",
                "name": "outer_a",
                "kind": "function",
                "start_line": 1,
                "end_line": 5,
            },
            {
                "file": "/repo/caller.py",
                "name": "outer_b",
                "kind": "function",
                "start_line": 10,
                "end_line": 15,
            },
        ],
    }
    related_call_sites = [
        {"file": "/repo/caller.py", "line": 3},  # -> outer_a
        {"file": "/repo/caller.py", "line": 4},  # -> outer_a again (dedupe)
        {"file": "/repo/caller.py", "line": 12},  # -> outer_b
        {"file": "/repo/caller.py", "line": 999},  # unresolved (no enclosing symbol) -> skipped
        {"file": "/repo/unknown.py", "line": 1},  # unresolved file -> skipped
    ]

    names = agent_capsule._top_caller_symbol_names(rm, related_call_sites, limit=2)

    assert names == ["outer_a", "outer_b"]


def test_top_caller_symbol_names_respects_limit() -> None:
    rm = {
        "symbols": [
            {
                "file": "/repo/caller.py",
                "name": f"outer_{index}",
                "kind": "function",
                "start_line": index * 10,
                "end_line": index * 10 + 5,
            }
            for index in range(5)
        ],
    }
    related_call_sites = [{"file": "/repo/caller.py", "line": index * 10 + 2} for index in range(5)]

    names = agent_capsule._top_caller_symbol_names(rm, related_call_sites, limit=2)

    assert len(names) == 2


# ---------------------------------------------------------------------------------------------
# (12) Direct helper tests: `_apply_inline_caller_annotation` targets ONLY the matching primary
# snippet among several, and is a no-op on every documented fail-safe path.
# ---------------------------------------------------------------------------------------------


def _make_snippet(*, file: str, symbol: str, source: str) -> dict[str, Any]:
    return {
        "file": file,
        "symbol": symbol,
        "start_line": 1,
        "end_line": max(1, len(source.splitlines())),
        "source": source,
        "line_map": [
            {"line": index + 1, "text": line} for index, line in enumerate(source.splitlines())
        ],
        "token_estimate": 5,
        "evidence": ["parser-backed", "heuristic"],
    }


def test_apply_inline_caller_annotation_only_touches_matching_primary_snippet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TG_CAPSULE_INLINE_CALLERS", "1")
    primary = _make_snippet(
        file="/repo/primary.py", symbol="primary_fn", source="def primary_fn():\n    pass\n"
    )
    other = _make_snippet(
        file="/repo/other.py", symbol="other_fn", source="def other_fn():\n    pass\n"
    )
    other_source_before = other["source"]
    target = {"file": "/repo/primary.py", "symbol": "primary_fn"}
    call_site_evidence = {"status": "collected", "returned_call_sites": 1, "omitted_call_sites": 0}
    related_call_sites = [{"file": "/repo/caller.py", "line": 3}]
    rm: dict[str, Any] = {"symbols": []}

    agent_capsule._apply_inline_caller_annotation(
        [primary, other],
        target,
        call_site_evidence,
        related_call_sites,
        rm,
        max_tokens=None,
        used_tokens=0,
    )

    assert primary["source"].startswith("# tg: callers=1")
    assert primary["inline_structural_annotation"]["applied"] is True
    assert other["source"] == other_source_before
    assert "inline_structural_annotation" not in other


def test_apply_inline_caller_annotation_noop_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TG_CAPSULE_INLINE_CALLERS", raising=False)
    primary = _make_snippet(
        file="/repo/primary.py", symbol="primary_fn", source="def primary_fn():\n    pass\n"
    )
    source_before = primary["source"]
    target = {"file": "/repo/primary.py", "symbol": "primary_fn"}
    call_site_evidence = {"status": "collected", "returned_call_sites": 1, "omitted_call_sites": 0}

    agent_capsule._apply_inline_caller_annotation(
        [primary],
        target,
        call_site_evidence,
        [],
        {"symbols": []},
        max_tokens=None,
        used_tokens=0,
    )

    assert primary["source"] == source_before
    assert "inline_structural_annotation" not in primary


def test_apply_inline_caller_annotation_noop_when_no_snippet_matches_primary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TG_CAPSULE_INLINE_CALLERS", "1")
    other = _make_snippet(
        file="/repo/other.py", symbol="other_fn", source="def other_fn():\n    pass\n"
    )
    source_before = other["source"]
    target = {"file": "/repo/primary.py", "symbol": "primary_fn"}  # not in snippets
    call_site_evidence = {"status": "collected", "returned_call_sites": 1, "omitted_call_sites": 0}

    agent_capsule._apply_inline_caller_annotation(
        [other],
        target,
        call_site_evidence,
        [],
        {"symbols": []},
        max_tokens=None,
        used_tokens=0,
    )

    assert other["source"] == source_before
    assert "inline_structural_annotation" not in other

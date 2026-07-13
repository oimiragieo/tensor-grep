"""Tests for `suggested_scope` on a genuine ambiguity TIE (dogfood: `tg agent <large-repo-root>`
on a big/ambiguous repo returns `ambiguity.status = "tie_requires_confirmation"` -- multiple tied
targets -- with 0 actionable validation commands AND `suggested_scope: null`, a dead end: the
caller has no hint to narrow and recover).

`suggested_scope` (`build_agent_capsule_from_map`, agent_capsule.py) was previously populated ONLY
when the underlying repo scan itself hit a `--max-repo-files` LIMIT (`rm["scan_limit"]
["possibly_truncated"]`); a genuine confirmation-tie left it null even on a small, non-truncated
scan, even though the tie itself often implicates a narrower subdirectory. This adds a pure-
additive fallback: when `ambiguity.requires_confirmation` is True and `suggested_scope` is still
empty, reuse `orient_capsule._suggested_scope_from_map`'s whole-repo centrality rollup first; if
that whole-repo signal is flat/tied (declines to answer), fall back to the tied candidates' own
deepest common parent directory via `_suggested_scope_from_tied_targets` -- never a guess, and
never the scan root itself.
"""

from pathlib import Path
from typing import Any

import pytest

from tensor_grep.cli import repo_map
from tensor_grep.cli.agent_capsule import _suggested_scope_from_tied_targets, build_agent_capsule

# ---------------------------------------------------------------------------
# Unit tests: _suggested_scope_from_tied_targets (pure function, no filesystem)
# ---------------------------------------------------------------------------


def test_fallback_common_subdirectory_of_primary_and_tied_alternative() -> None:
    root = Path("/repo")
    target = {"file": str(root / "sub" / "primary.py")}
    tied = [{"file": str(root / "sub" / "alt.py")}]
    assert _suggested_scope_from_tied_targets(root, target, tied) == {
        "dirs": [str(root / "sub")],
        "confidence": "heuristic",
    }


def test_fallback_deepest_common_parent_when_nested_two_levels() -> None:
    # The tied files diverge two levels below root (sub/mid/a vs sub/mid/b) -- the common parent
    # must be the DEEPEST shared directory (sub/mid), narrower than a top-level-only rollup could
    # ever suggest, not just the first path component under root.
    root = Path("/repo")
    target = {"file": str(root / "sub" / "mid" / "a" / "primary.py")}
    tied = [{"file": str(root / "sub" / "mid" / "b" / "alt.py")}]
    assert _suggested_scope_from_tied_targets(root, target, tied) == {
        "dirs": [str(root / "sub" / "mid")],
        "confidence": "heuristic",
    }


def test_fallback_multiple_tied_alternatives_common_parent() -> None:
    root = Path("/repo")
    target = {"file": str(root / "svc" / "a.py")}
    tied = [
        {"file": str(root / "svc" / "b.py")},
        {"file": str(root / "svc" / "nested" / "c.py")},
    ]
    assert _suggested_scope_from_tied_targets(root, target, tied) == {
        "dirs": [str(root / "svc")],
        "confidence": "heuristic",
    }


def test_fallback_none_when_common_parent_is_scan_root() -> None:
    # Disjoint top-level directories -- the only shared ancestor is the scan root itself. Never
    # suggest the root back to the caller (that is not a narrowing hint).
    root = Path("/repo")
    target = {"file": str(root / "dira" / "primary.py")}
    tied = [{"file": str(root / "dirb" / "alt.py")}]
    assert _suggested_scope_from_tied_targets(root, target, tied) is None


def test_fallback_none_with_no_tied_targets_and_root_level_primary() -> None:
    root = Path("/repo")
    target = {"file": str(root / "primary.py")}
    assert _suggested_scope_from_tied_targets(root, target, []) is None


def test_fallback_none_when_common_parent_outside_root() -> None:
    # Defensive: a common parent that is an ANCESTOR of root (not a descendant) must never be
    # suggested -- it is nonsensical for a re-scoped `tg agent <suggested_scope>` re-run.
    root = Path("/repo/nested")
    target = {"file": str(Path("/repo/other/primary.py"))}
    tied = [{"file": str(Path("/repo/other2/alt.py"))}]
    assert _suggested_scope_from_tied_targets(root, target, tied) is None


def test_fallback_none_when_dotdot_escapes_root() -> None:
    # Defense-in-depth (Opus gate): a ``..``-prefixed path lexically "starts with" root but
    # escapes it. The unhardened guard (plain ``relative_to``) would emit ``/repo/../../etc/x``;
    # the normpath collapse + ``..``-in-parts check must reject it. Unreachable in the current
    # wiring (all callers pre-resolve + skip symlinks) but the confinement guard must be
    # self-enforcing regardless of upstream invariants.
    root = Path("/repo")
    target = {"file": str(Path("/repo/../../etc/secret/primary.py"))}
    tied = [{"file": str(Path("/repo/../../etc/secret/alt.py"))}]
    assert _suggested_scope_from_tied_targets(root, target, tied) is None


# ---------------------------------------------------------------------------
# Integration tests: build_agent_capsule end-to-end (task's 3 required scenarios)
# ---------------------------------------------------------------------------


def _write_tie_project(tmp_path: Path, *, primary_rel: str, alt_rel: str) -> dict[str, Path]:
    project = tmp_path / "workspace"
    project.mkdir()
    (project / "pyproject.toml").write_text(
        '[project]\nname = "sample"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    primary_file = project / primary_rel
    primary_file.parent.mkdir(parents=True, exist_ok=True)
    primary_file.write_text("def process_widget(payload):\n    return payload\n", encoding="utf-8")
    alt_file = project / alt_rel
    alt_file.parent.mkdir(parents=True, exist_ok=True)
    alt_file.write_text(
        "def process_widget_archive(payload):\n    return payload\n", encoding="utf-8"
    )
    return {"project": project, "primary": primary_file, "alt": alt_file}


def _tied_context_payload(*, primary_file: Path, alt_file: Path) -> dict[str, Any]:
    return {
        "routing_backend": "RepoMap",
        "routing_reason": "context-render",
        "semantic_provider": "native",
        "files": [str(primary_file), str(alt_file)],
        "file_matches": [
            {
                "path": str(alt_file),
                "score": 95,
                "reasons": ["symbol"],
                "provenance": ["heuristic"],
            }
        ],
        "sources": [
            {
                "file": str(primary_file),
                "symbol": "process_widget",
                "name": "process_widget",
                "start_line": 1,
                "end_line": 1,
                "source": "def process_widget(payload):\n    return payload\n",
            },
        ],
        "validation_commands": ["uv run pytest -q"],
        "edit_plan_seed": {
            "primary_file": str(primary_file),
            "primary_symbol": {"name": "process_widget", "kind": "function"},
            "primary_span": {"start_line": 1, "end_line": 1},
            # No "confidence.overall" -- matches real repo_map output; _primary_target falls back
            # to the raw 0.9 seed default (same convention as test_token_budget.py's T2 fixture).
            "validation_plan": [],
            "validation_commands": ["uv run pytest -q"],
            "validation_alignment": {"status": "aligned", "kept_count": 1, "filtered_count": 0},
            "edit_ordering": [str(primary_file)],
        },
        "navigation_pack": {
            "primary_target": {
                "file": str(primary_file),
                "symbol": "process_widget",
                "kind": "function",
                "start_line": 1,
                "end_line": 1,
            },
            "follow_up_reads": [],
        },
        "candidate_edit_targets": {
            "files": [str(primary_file)],
            "symbols": [
                {
                    "file": str(alt_file),
                    "name": "process_widget_archive",
                    "kind": "function",
                    "line": 1,
                    "score": 95,
                }
            ],
            "tests": [],
        },
        "context_consistency": {
            "primary_file_included": True,
            "rendered_context_includes_primary": True,
        },
    }


def test_tie_under_common_subdirectory_emits_non_null_suggested_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Task scenario 1: tied targets both live under `workspace/sub/` -- the capsule must narrow
    `suggested_scope` to that shared subdirectory instead of leaving it null."""
    paths = _write_tie_project(tmp_path, primary_rel="sub/primary.py", alt_rel="sub/alt.py")
    monkeypatch.setattr(
        repo_map,
        "build_context_render_from_map",
        lambda *args, **kwargs: _tied_context_payload(
            primary_file=paths["primary"].resolve(), alt_file=paths["alt"].resolve()
        ),
    )

    payload = build_agent_capsule("process widget", paths["project"], max_tokens=8000)

    assert payload["ambiguity"]["status"] == "tie_requires_confirmation"
    assert payload["ambiguity"]["requires_confirmation"] is True
    assert payload.get("suggested_scope") is not None
    assert payload["suggested_scope"]["confidence"] == "heuristic"
    assert payload["suggested_scope"]["dirs"] == [str((paths["project"] / "sub").resolve())]


def test_non_tie_confident_capsule_unchanged_no_spurious_suggested_scope(tmp_path: Path) -> None:
    """Task scenario 2: an ordinary, confident (non-tie) capsule on a small, non-truncated repo
    must NOT gain a `suggested_scope` it never had -- the fix is scoped to genuine confirmation
    ties (`ambiguity.requires_confirmation`); scan-limit truncation (a separate, pre-existing gate)
    is untouched by this fix and is covered by test_agent_suggested_scope.py."""
    project = tmp_path / "workspace"
    project.mkdir()
    (project / "a.py").write_text("def solo_widget():\n    return 1\n", encoding="utf-8")

    payload = build_agent_capsule("solo_widget", project, max_tokens=8000)

    assert payload["ambiguity"]["status"] == "none"
    assert payload["ambiguity"]["requires_confirmation"] is False
    assert "suggested_scope" not in payload


def test_tie_across_disjoint_top_level_dirs_suggested_scope_stays_null(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Task scenario 3: tied targets live in sibling top-level directories (`dira/`, `dirb/`) whose
    only common ancestor is the scan root itself -- `suggested_scope` must stay null (no
    fabrication, and the fix must never suggest the root back to the caller)."""
    paths = _write_tie_project(tmp_path, primary_rel="dira/primary.py", alt_rel="dirb/alt.py")
    monkeypatch.setattr(
        repo_map,
        "build_context_render_from_map",
        lambda *args, **kwargs: _tied_context_payload(
            primary_file=paths["primary"].resolve(), alt_file=paths["alt"].resolve()
        ),
    )

    payload = build_agent_capsule("process widget", paths["project"], max_tokens=8000)

    assert payload["ambiguity"]["status"] == "tie_requires_confirmation"
    assert payload["ambiguity"]["requires_confirmation"] is True
    assert "suggested_scope" not in payload


def test_tie_never_overwrites_a_suggested_scope_already_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Precedence/no-clobber: when the inner render already carries a `suggested_scope` (e.g. the
    pre-existing scan-limit-truncation path set it before this fix's tie check runs), the tie
    fallback must leave it exactly as-is -- additive only, never replaces an existing hint."""
    paths = _write_tie_project(tmp_path, primary_rel="sub/primary.py", alt_rel="sub/alt.py")
    pre_existing_hint = {"dirs": [str(paths["project"] / "elsewhere")], "confidence": "heuristic"}

    def _render_with_pre_existing_hint(*args: Any, **kwargs: Any) -> dict[str, Any]:
        rendered = _tied_context_payload(
            primary_file=paths["primary"].resolve(), alt_file=paths["alt"].resolve()
        )
        rendered["suggested_scope"] = pre_existing_hint
        return rendered

    monkeypatch.setattr(repo_map, "build_context_render_from_map", _render_with_pre_existing_hint)

    payload = build_agent_capsule("process widget", paths["project"], max_tokens=8000)

    assert payload["ambiguity"]["status"] == "tie_requires_confirmation"
    assert payload["suggested_scope"] == pre_existing_hint

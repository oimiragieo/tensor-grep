"""Unit tests for ``tensor_grep.cli.ledger_store`` -- ``tg ledger`` Slice 1 (claims).

Store-level coverage (no CLI/Typer): default-inert until the first write, TTL expiry
pruning, traversal refusal, overlap detection, release by claim-id and by symbol, list
filtering, agent-id resolution fallback chain, and revision-identity capture. CLI wiring
(exit codes, JSON envelope) is covered in ``test_ledger_cli.py``; cross-thread RMW safety is
covered in ``test_ledger_concurrency.py`` -- mirrors ``test_session_containment.py`` /
``test_index_lock_concurrency.py``'s own file split for the sibling session store.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tensor_grep.cli import ledger_store


def _make_project(tmp_path: Path, name: str = "project") -> Path:
    root = tmp_path / name
    root.mkdir()
    (root / "mod.py").write_text("def value():\n    return 1\n", encoding="utf-8")
    return root


def _git_init(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=root, check=True)


def _index_path(root: Path) -> Path:
    return root / ".tensor-grep" / "ledger" / "claims" / "index.json"


def _rewrite_expires_at(index_path: Path, *, when: datetime, index: int = 0) -> None:
    records = json.loads(index_path.read_text(encoding="utf-8"))
    records[index]["expires_at"] = when.isoformat()
    index_path.write_text(json.dumps(records), encoding="utf-8")


# --------------------------------------------------------------------------------------
# default-inert: nothing is written to disk until the first claim
# --------------------------------------------------------------------------------------


def test_no_ledger_dir_created_until_first_claim(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    assert not (root / ".tensor-grep").exists()

    result = ledger_store.list_claims(str(root))
    assert result == {"claims": [], "count": 0}
    assert not (root / ".tensor-grep").exists()


def test_release_on_empty_ledger_does_not_create_dir(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    result = ledger_store.release_claim(str(root), claim_id="claim-does-not-exist")
    assert result == {
        "released": [],
        "released_count": 0,
        "listed_scope": ".",
        "unmatched_reason": "No live claims exist for this repository.",
        "live_claims_elsewhere": [],
        "live_claims_elsewhere_count": 0,
        "live_claims_elsewhere_truncated": False,
    }
    assert not (root / ".tensor-grep").exists()


def test_first_claim_creates_ledger_dir_and_index(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    ledger_store.submit_claim(str(root), symbols=["value"], agent_id="agent-a")
    assert _index_path(root).exists()


# --------------------------------------------------------------------------------------
# claim shape + agent-id resolution
# --------------------------------------------------------------------------------------


def test_claim_requires_symbol_or_files(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    with pytest.raises(ledger_store.LedgerUsageError):
        ledger_store.submit_claim(str(root), agent_id="agent-a")
    assert not (root / ".tensor-grep").exists()


def test_claim_shape_and_schema_fields(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    result = ledger_store.submit_claim(str(root), symbols=["value"], agent_id="agent-a")
    claim = result["claim"]
    assert claim["claim_id"].startswith("claim-")
    assert claim["ledger_schema_version"] == ledger_store.LEDGER_SCHEMA_VERSION == 1
    assert claim["kind"] == "claim"
    assert claim["agent_id"] == "agent-a"
    assert claim["symbols"] == ["value"]
    assert claim["files"] == []
    assert claim["intent"] == "edit"
    assert claim["ttl_seconds"] == 900
    assert "revision" in claim
    assert result["overlaps"] == []


def test_claim_ttl_override(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    result = ledger_store.submit_claim(
        str(root), symbols=["value"], agent_id="agent-a", ttl_seconds=42
    )
    assert result["claim"]["ttl_seconds"] == 42


def test_claim_ttl_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TG_LEDGER_CLAIM_TTL_SECONDS", "123")
    root = _make_project(tmp_path)
    result = ledger_store.submit_claim(str(root), symbols=["value"], agent_id="agent-a")
    assert result["claim"]["ttl_seconds"] == 123


def test_agent_id_resolution_fallback_chain(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TG_LEDGER_AGENT_ID", raising=False)
    monkeypatch.delenv("TG_EVIDENCE_AGENT_ID", raising=False)
    assert ledger_store.resolve_agent_id(None) == "anonymous"
    assert ledger_store.resolve_agent_id("   ") == "anonymous"
    assert ledger_store.resolve_agent_id("explicit") == "explicit"

    monkeypatch.setenv("TG_EVIDENCE_AGENT_ID", "from-evidence-env")
    assert ledger_store.resolve_agent_id(None) == "from-evidence-env"

    monkeypatch.setenv("TG_LEDGER_AGENT_ID", "from-ledger-env")
    assert ledger_store.resolve_agent_id(None) == "from-ledger-env"
    # explicit flag always wins over both env vars
    assert ledger_store.resolve_agent_id("explicit") == "explicit"


def test_claim_dedupes_symbols_and_files_preserving_order(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    result = ledger_store.submit_claim(
        str(root),
        symbols=["b", "a", "b"],
        files=["mod.py", "mod.py"],
        agent_id="agent-a",
    )
    assert result["claim"]["symbols"] == ["b", "a"]
    assert result["claim"]["files"] == ["mod.py"]


def test_claim_note_recorded_verbatim_and_blank_becomes_none(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    with_note = ledger_store.submit_claim(
        str(root), symbols=["value"], agent_id="agent-a", note="refactoring signature"
    )
    assert with_note["claim"]["note"] == "refactoring signature"

    blank_note = ledger_store.submit_claim(
        str(root), symbols=["other"], agent_id="agent-a", note="   "
    )
    assert blank_note["claim"]["note"] is None


# --------------------------------------------------------------------------------------
# traversal refusal: a --files entry outside the repo root is refused, nothing written
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "evil_path",
    ["../escape.py", "../../escape.py", "sub/../../escape.py", ".."],
)
def test_claim_refuses_files_outside_root(tmp_path: Path, evil_path: str) -> None:
    root = _make_project(tmp_path)
    with pytest.raises(ledger_store.LedgerTraversalError):
        ledger_store.submit_claim(str(root), files=[evil_path], agent_id="agent-a")
    # Fail-closed: nothing written at all -- not even the ledger directory.
    assert not (root / ".tensor-grep").exists()


def test_claim_refuses_absolute_files_path(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    victim = tmp_path / "victim.py"
    with pytest.raises(ledger_store.LedgerTraversalError):
        ledger_store.submit_claim(str(root), files=[str(victim)], agent_id="agent-a")
    assert not (root / ".tensor-grep").exists()


def test_claim_allows_legitimate_nested_relative_file(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    (root / "src").mkdir()
    result = ledger_store.submit_claim(str(root), files=["src/mod.py"], agent_id="agent-a")
    assert result["claim"]["files"] == ["src/mod.py"]


# --------------------------------------------------------------------------------------
# TTL expiry: an expired claim is gone from list / does not count as an overlap / is
# pruned from disk on the next write
# --------------------------------------------------------------------------------------


def test_list_excludes_expired_claims(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    ledger_store.submit_claim(str(root), symbols=["value"], agent_id="agent-a", ttl_seconds=1)
    assert ledger_store.list_claims(str(root))["count"] == 1

    _rewrite_expires_at(_index_path(root), when=datetime.now(UTC) - timedelta(seconds=5))

    assert ledger_store.list_claims(str(root)) == {"claims": [], "count": 0}


def test_expired_claim_is_not_reported_as_overlap(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    ledger_store.submit_claim(str(root), symbols=["value"], agent_id="agent-a", ttl_seconds=1)
    _rewrite_expires_at(_index_path(root), when=datetime.now(UTC) - timedelta(seconds=5))

    result = ledger_store.submit_claim(str(root), symbols=["value"], agent_id="agent-b")
    assert result["overlaps"] == []  # agent-a's claim already expired -- no live overlap


def test_expired_claim_pruned_on_next_write(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    ledger_store.submit_claim(str(root), symbols=["value"], agent_id="agent-a", ttl_seconds=1)
    _rewrite_expires_at(_index_path(root), when=datetime.now(UTC) - timedelta(seconds=5))

    ledger_store.submit_claim(str(root), symbols=["other"], agent_id="agent-b")
    on_disk = json.loads(_index_path(root).read_text(encoding="utf-8"))
    # Only agent-b's fresh claim remains -- agent-a's expired record was pruned on write.
    assert len(on_disk) == 1
    assert on_disk[0]["agent_id"] == "agent-b"


def test_malformed_expires_at_is_treated_as_expired(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    ledger_store.submit_claim(str(root), symbols=["value"], agent_id="agent-a")

    records = json.loads(_index_path(root).read_text(encoding="utf-8"))
    records[0]["expires_at"] = "not-a-timestamp"
    _index_path(root).write_text(json.dumps(records), encoding="utf-8")

    assert ledger_store.list_claims(str(root)) == {"claims": [], "count": 0}


# --------------------------------------------------------------------------------------
# overlap detection: agent A claims symbol S, agent B claims S -> B's overlaps names A;
# claim() never raises on overlap (advisory, never blocks)
# --------------------------------------------------------------------------------------


def test_overlap_detected_across_agents_on_same_symbol(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    a = ledger_store.submit_claim(str(root), symbols=["value"], agent_id="agent-a", intent="edit")
    assert a["overlaps"] == []

    b = ledger_store.submit_claim(str(root), symbols=["value"], agent_id="agent-b", intent="review")
    assert len(b["overlaps"]) == 1
    overlap = b["overlaps"][0]
    assert overlap["claim_id"] == a["claim"]["claim_id"]
    assert overlap["agent_id"] == "agent-a"
    assert overlap["symbols"] == ["value"]
    assert overlap["intent"] == "edit"
    assert "expires_at" in overlap
    assert "revision_matches" in overlap


def test_overlap_detected_on_shared_file(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    a = ledger_store.submit_claim(str(root), files=["mod.py"], agent_id="agent-a")
    b = ledger_store.submit_claim(str(root), files=["mod.py"], agent_id="agent-b")
    assert len(b["overlaps"]) == 1
    assert b["overlaps"][0]["claim_id"] == a["claim"]["claim_id"]
    assert b["overlaps"][0]["files"] == ["mod.py"]


def test_no_overlap_reported_for_same_agent(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    ledger_store.submit_claim(str(root), symbols=["value"], agent_id="agent-a")
    second = ledger_store.submit_claim(str(root), symbols=["value"], agent_id="agent-a")
    assert second["overlaps"] == []  # self-overlap is not a coordination conflict


def test_no_overlap_for_disjoint_symbols(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    ledger_store.submit_claim(str(root), symbols=["alpha"], agent_id="agent-a")
    result = ledger_store.submit_claim(str(root), symbols=["beta"], agent_id="agent-b")
    assert result["overlaps"] == []


def test_claim_never_raises_on_overlap_only_reports(tmp_path: Path) -> None:
    """The ADVISORY contract: overlap is reported, never blocks / never raises."""
    root = _make_project(tmp_path)
    ledger_store.submit_claim(str(root), symbols=["value"], agent_id="agent-a")
    result = ledger_store.submit_claim(str(root), symbols=["value"], agent_id="agent-b")
    assert result["claim"]["claim_id"]  # succeeded normally despite the live overlap


def test_revision_matches_true_within_same_commit(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    _git_init(root)
    a = ledger_store.submit_claim(str(root), symbols=["value"], agent_id="agent-a")
    assert a["claim"]["revision"]["status"] == "present"

    b = ledger_store.submit_claim(str(root), symbols=["value"], agent_id="agent-b")
    assert b["overlaps"][0]["revision_matches"] is True


def test_revision_matches_none_when_unavailable(tmp_path: Path) -> None:
    root = _make_project(tmp_path)  # never git-inited
    a = ledger_store.submit_claim(str(root), symbols=["value"], agent_id="agent-a")
    assert a["claim"]["revision"]["status"] == "unavailable"

    b = ledger_store.submit_claim(str(root), symbols=["value"], agent_id="agent-b")
    # Never fabricated: unavailable revision on either side -> None, not True/False.
    assert b["overlaps"][0]["revision_matches"] is None


# --------------------------------------------------------------------------------------
# release: by claim-id (any caller) and by symbol (scoped to the resolved agent)
# --------------------------------------------------------------------------------------


def test_release_by_claim_id(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    claimed = ledger_store.submit_claim(str(root), symbols=["value"], agent_id="agent-a")
    claim_id = claimed["claim"]["claim_id"]

    result = ledger_store.release_claim(str(root), claim_id=claim_id)
    assert result["released_count"] == 1
    assert result["released"][0]["claim_id"] == claim_id
    assert ledger_store.list_claims(str(root)) == {"claims": [], "count": 0}


def test_release_by_symbol_scoped_to_agent(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    ledger_store.submit_claim(str(root), symbols=["value"], agent_id="agent-a")
    ledger_store.submit_claim(str(root), symbols=["value"], agent_id="agent-b")

    # agent-a releasing --symbol value must only drop ITS OWN claim, not agent-b's.
    result = ledger_store.release_claim(str(root), symbol="value", agent_id="agent-a")
    assert result["released_count"] == 1
    assert result["released"][0]["agent_id"] == "agent-a"

    remaining = ledger_store.list_claims(str(root))
    assert remaining["count"] == 1
    assert remaining["claims"][0]["agent_id"] == "agent-b"


def test_release_requires_claim_id_or_symbol(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    with pytest.raises(ledger_store.LedgerUsageError):
        ledger_store.release_claim(str(root))


def test_release_nonexistent_claim_is_not_an_error(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    result = ledger_store.release_claim(str(root), claim_id="claim-nonexistent")
    assert result == {
        "released": [],
        "released_count": 0,
        "listed_scope": ".",
        "unmatched_reason": "No live claims exist for this repository.",
        "live_claims_elsewhere": [],
        "live_claims_elsewhere_count": 0,
        "live_claims_elsewhere_truncated": False,
    }


def test_release_by_symbol_does_not_cross_agents(tmp_path: Path) -> None:
    """Release-mismatch honesty: agent-b's release matches nothing (the live claim belongs to
    agent-a) -- `released_count` stays 0 (unchanged contract), but the response now NAMES what
    IS live instead of a bare, indistinguishable zero."""
    root = _make_project(tmp_path)
    claimed = ledger_store.submit_claim(str(root), symbols=["value"], agent_id="agent-a")
    claim_id = claimed["claim"]["claim_id"]

    result = ledger_store.release_claim(str(root), symbol="value", agent_id="agent-b")
    assert result["released"] == []
    assert result["released_count"] == 0
    assert result["listed_scope"] == "."
    assert result["unmatched_reason"] is not None
    assert "1 live claim(s)" in result["unmatched_reason"]
    assert result["live_claims_elsewhere_count"] == 1
    assert result["live_claims_elsewhere_truncated"] is False
    assert len(result["live_claims_elsewhere"]) == 1
    elsewhere = result["live_claims_elsewhere"][0]
    assert elsewhere["claim_id"] == claim_id
    assert elsewhere["agent_id"] == "agent-a"
    assert elsewhere["scope"] == "."
    assert elsewhere["symbols"] == ["value"]
    assert ledger_store.list_claims(str(root))["count"] == 1


# --------------------------------------------------------------------------------------
# PATH-scope footgun fix (CEO v1.92.1 dogfood #1): claim/list/release canonicalize to the
# SAME repository root regardless of which subtree PATH names; `list` rolls scope up; a
# `release` that matches nothing names what IS live. Reproduces the exact dogfood sequence:
# `tg ledger claim core/hooks ...` then `tg ledger list` (or `list .`) used to return EMPTY,
# and `tg ledger release` from a different PATH used to return `released_count: 0` while the
# claim silently lived on.
# --------------------------------------------------------------------------------------


def test_claim_subpath_rolls_up_into_root_list(tmp_path: Path) -> None:
    """THE dogfood repro: `claim core/hooks` then `list .` (here: `list(str(root))`, the
    absolute equivalent of `.` from `root`'s own cwd) must show it -- pre-fix this returned
    EMPTY because `core/hooks` and `.` resolved to two different physical index.json files."""
    root = _make_project(tmp_path)
    _git_init(root)
    (root / "core" / "hooks").mkdir(parents=True)

    claimed = ledger_store.submit_claim(
        str(root / "core" / "hooks"), symbols=["open_session"], agent_id="agent-a"
    )
    assert claimed["claim"]["scope"] == "core/hooks"

    listed = ledger_store.list_claims(str(root))
    assert listed["count"] == 1
    assert listed["claims"][0]["scope"] == "core/hooks"
    assert listed["claims"][0]["claim_id"] == claimed["claim"]["claim_id"]


def test_claim_subpath_rolls_up_into_intermediate_ancestor_list(tmp_path: Path) -> None:
    """Rollup is not root-only: listing ANY ancestor of the claimed scope (not just the repo
    root) must also show it."""
    root = _make_project(tmp_path)
    _git_init(root)
    (root / "core" / "hooks").mkdir(parents=True)
    ledger_store.submit_claim(
        str(root / "core" / "hooks"), symbols=["open_session"], agent_id="agent-a"
    )

    listed = ledger_store.list_claims(str(root / "core"))
    assert listed["count"] == 1
    assert listed["claims"][0]["scope"] == "core/hooks"


def test_root_scoped_claim_not_shown_in_narrower_list(tmp_path: Path) -> None:
    """The rollup is one-directional (list docstring / module docstring): a claim scoped to
    the repo root (or any ancestor of the listed path) does NOT roll DOWN into a narrower
    listing -- "Keep exact behavior for claims outside the listed subtree."""
    root = _make_project(tmp_path)
    _git_init(root)
    (root / "sub").mkdir()
    ledger_store.submit_claim(str(root), symbols=["whole_repo_symbol"], agent_id="agent-a")

    narrow = ledger_store.list_claims(str(root / "sub"))
    assert narrow == {"claims": [], "count": 0}
    # ...but it's still visible from the root itself.
    assert ledger_store.list_claims(str(root))["count"] == 1


def test_disjoint_sibling_subpath_not_shown(tmp_path: Path) -> None:
    """A claim scoped to one subtree must not appear when listing a DISJOINT sibling
    subtree."""
    root = _make_project(tmp_path)
    _git_init(root)
    (root / "core" / "hooks").mkdir(parents=True)
    (root / "docs").mkdir()
    ledger_store.submit_claim(str(root / "core" / "hooks"), symbols=["foo"], agent_id="agent-a")

    listed = ledger_store.list_claims(str(root / "docs"))
    assert listed == {"claims": [], "count": 0}


def test_scope_containment_is_segment_wise_not_string_prefix(tmp_path: Path) -> None:
    """False-prefix trap: a claim scoped to `core/hoodie` must NOT match a list of
    `core/ho` -- a lexical (segment-wise) containment check, never a raw string-prefix
    test."""
    root = _make_project(tmp_path)
    _git_init(root)
    (root / "core" / "hoodie").mkdir(parents=True)
    (root / "core" / "ho").mkdir(parents=True)
    ledger_store.submit_claim(str(root / "core" / "hoodie"), symbols=["foo"], agent_id="agent-a")

    listed = ledger_store.list_claims(str(root / "core" / "ho"))
    assert listed == {"claims": [], "count": 0}


def test_scope_normalizes_dot_slash_prefix_and_trailing_slash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Path normalization traps: a `./`-prefixed relative PATH and a trailing-slash PATH must
    both normalize to the same clean, POSIX-separated scope as the plain form. Requires a
    `.git` anchor at `root` -- without one, canonicalization is a no-op (see
    `test_non_git_sibling_directories_keep_isolated_roots`) and the physical root would stop
    AT the claimed subdirectory itself, making every scope trivially "."."""
    root = _make_project(tmp_path)
    _git_init(root)
    (root / "core" / "hooks").mkdir(parents=True)
    monkeypatch.chdir(root)

    dot_slash = ledger_store.submit_claim("./core/hooks", symbols=["a"], agent_id="agent-a")
    assert dot_slash["claim"]["scope"] == "core/hooks"

    trailing_slash = ledger_store.submit_claim("core/hooks/", symbols=["b"], agent_id="agent-a")
    assert trailing_slash["claim"]["scope"] == "core/hooks"


@pytest.mark.skipif(sys.platform != "win32", reason="backslash is only a separator on Windows")
def test_scope_normalizes_windows_separators(tmp_path: Path) -> None:
    """Windows-separator normalization trap: a backslash-separated (or mixed) PATH must
    normalize to the same clean POSIX scope as a forward-slash PATH. Requires a `.git` anchor
    at `root` for the same reason as the `./`-prefix test above."""
    root = _make_project(tmp_path)
    _git_init(root)
    (root / "core" / "hooks").mkdir(parents=True)

    mixed = ledger_store.submit_claim(str(root) + "\\core/hooks", symbols=["c"], agent_id="agent-a")
    assert mixed["claim"]["scope"] == "core/hooks"

    backslash = ledger_store.submit_claim(
        str(root) + "\\core\\hooks", symbols=["d"], agent_id="agent-a"
    )
    assert backslash["claim"]["scope"] == "core/hooks"


def test_release_right_selector_succeeds_from_different_subpath(tmp_path: Path) -> None:
    """Physical unification proof (the release half of the dogfood repro): a claim filed
    under `core/hooks` is releasable by `--symbol`/`--agent-id` from a DIFFERENT subpath of
    the SAME repository ('.') -- pre-fix this returned `released_count: 0` because `core/
    hooks` and `.` were different physical index.json files."""
    root = _make_project(tmp_path)
    _git_init(root)
    (root / "core" / "hooks").mkdir(parents=True)
    ledger_store.submit_claim(
        str(root / "core" / "hooks"), symbols=["open_session"], agent_id="agent-a"
    )

    result = ledger_store.release_claim(str(root), symbol="open_session", agent_id="agent-a")
    assert result["released_count"] == 1
    assert result["released"][0]["scope"] == "core/hooks"
    assert ledger_store.list_claims(str(root))["count"] == 0


def test_release_by_claim_id_succeeds_from_different_subpath(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    _git_init(root)
    (root / "core" / "hooks").mkdir(parents=True)
    claimed = ledger_store.submit_claim(
        str(root / "core" / "hooks"), symbols=["foo"], agent_id="agent-a"
    )
    claim_id = claimed["claim"]["claim_id"]

    result = ledger_store.release_claim(str(root), claim_id=claim_id)
    assert result["released_count"] == 1


def test_non_git_sibling_directories_keep_isolated_roots(tmp_path: Path) -> None:
    """Fallback safety net: two SIBLING non-git directories must NOT be unified into one
    ledger just because they share a parent -- canonicalization only kicks in when a `.git`
    boundary is actually found; without one, today's exact `_resolve_root`-only (per-literal
    -path) behavior is preserved unchanged."""
    project_a = _make_project(tmp_path, name="project_a")
    project_b = _make_project(tmp_path, name="project_b")
    ledger_store.submit_claim(str(project_a), symbols=["only_in_a"], agent_id="agent-a")

    assert ledger_store.list_claims(str(project_b)) == {"claims": [], "count": 0}
    assert ledger_store.list_claims(str(project_a))["count"] == 1


def test_old_format_claim_missing_scope_defaults_to_root_and_stays_visible(
    tmp_path: Path,
) -> None:
    """Backward-read: an on-disk claim written by a pre-fix `tg` (no `scope` key at all) must
    not silently vanish from `list` after upgrading -- it defaults to `"."` (repo-root-wide,
    the maximally-visible default) and is stamped onto the returned entry."""
    root = _make_project(tmp_path)
    ledger_store.submit_claim(str(root), symbols=["value"], agent_id="agent-a")

    records = json.loads(_index_path(root).read_text(encoding="utf-8"))
    del records[0]["scope"]
    _index_path(root).write_text(json.dumps(records), encoding="utf-8")

    listed = ledger_store.list_claims(str(root))
    assert listed["count"] == 1
    assert listed["claims"][0]["scope"] == "."


# --------------------------------------------------------------------------------------
# list filters by symbol / agent-id
# --------------------------------------------------------------------------------------


def test_list_filters_by_symbol(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    ledger_store.submit_claim(str(root), symbols=["alpha"], agent_id="agent-a")
    ledger_store.submit_claim(str(root), symbols=["beta"], agent_id="agent-b")

    result = ledger_store.list_claims(str(root), symbol="alpha")
    assert result["count"] == 1
    assert result["claims"][0]["agent_id"] == "agent-a"


def test_list_filters_by_agent_id(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    ledger_store.submit_claim(str(root), symbols=["alpha"], agent_id="agent-a")
    ledger_store.submit_claim(str(root), symbols=["beta"], agent_id="agent-b")

    result = ledger_store.list_claims(str(root), agent_id="agent-b")
    assert result["count"] == 1
    assert result["claims"][0]["symbols"] == ["beta"]


def test_list_filters_combine_as_and(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    ledger_store.submit_claim(str(root), symbols=["alpha"], agent_id="agent-a")
    ledger_store.submit_claim(str(root), symbols=["alpha"], agent_id="agent-b")

    result = ledger_store.list_claims(str(root), symbol="alpha", agent_id="agent-b")
    assert result["count"] == 1
    assert result["claims"][0]["agent_id"] == "agent-b"


def test_list_is_read_only_and_does_not_prune_disk(tmp_path: Path) -> None:
    """list_claims must never write -- expired-pruning for display is not the same as
    physically rewriting index.json (that happens lazily on the next claim/release)."""
    root = _make_project(tmp_path)
    ledger_store.submit_claim(str(root), symbols=["value"], agent_id="agent-a", ttl_seconds=1)
    _rewrite_expires_at(_index_path(root), when=datetime.now(UTC) - timedelta(seconds=5))

    before_mtime = _index_path(root).stat().st_mtime_ns
    assert ledger_store.list_claims(str(root)) == {"claims": [], "count": 0}
    after_mtime = _index_path(root).stat().st_mtime_ns
    assert before_mtime == after_mtime


# --------------------------------------------------------------------------------------
# eviction cap: a DoS bound distinct from TTL pruning (still-live claims over the cap)
# --------------------------------------------------------------------------------------


def test_eviction_caps_live_claims_at_max(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _make_project(tmp_path)
    monkeypatch.setattr(ledger_store, "_MAX_LIVE_CLAIMS", 3)
    for i in range(5):
        ledger_store.submit_claim(str(root), symbols=[f"sym{i}"], agent_id=f"agent-{i}")
    result = ledger_store.list_claims(str(root))
    assert result["count"] == 3
    kept_agents = {c["agent_id"] for c in result["claims"]}
    # oldest (agent-0, agent-1) evicted; newest 3 survive
    assert kept_agents == {"agent-2", "agent-3", "agent-4"}


# --------------------------------------------------------------------------------------
# bounded/corrupt index reads fail closed (never silently read as "no claims")
# --------------------------------------------------------------------------------------


def test_oversized_index_refuses_to_parse(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _make_project(tmp_path)
    ledger_store.submit_claim(str(root), symbols=["value"], agent_id="agent-a")
    monkeypatch.setattr(ledger_store, "_MAX_INDEX_FILE_BYTES", 4)
    with pytest.raises(ledger_store.LedgerIndexTooLargeError):
        ledger_store.list_claims(str(root))


def test_corrupt_index_fails_closed_not_silently_empty(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    ledger_store.submit_claim(str(root), symbols=["value"], agent_id="agent-a")
    _index_path(root).write_text("{not valid json", encoding="utf-8")
    with pytest.raises(ledger_store.LedgerCorruptIndexError):
        ledger_store.list_claims(str(root))


def test_non_array_index_fails_closed(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    ledger_store.submit_claim(str(root), symbols=["value"], agent_id="agent-a")
    _index_path(root).write_text(json.dumps({"not": "an array"}), encoding="utf-8")
    with pytest.raises(ledger_store.LedgerCorruptIndexError):
        ledger_store.list_claims(str(root))


# ========================================================================================
# Slice 2: findings -- content-addressed artifact reuse (tg ledger record / find)
# ========================================================================================


def _findings_index_path(root: Path) -> Path:
    return root / ".tensor-grep" / "ledger" / "findings" / "index.json"


def _findings_blobs_dir(root: Path) -> Path:
    return root / ".tensor-grep" / "ledger" / "findings" / "blobs"


def _write_artifact_json(tmp_path: Path, name: str, payload: dict) -> Path:
    artifact_path = tmp_path / name
    artifact_path.write_text(json.dumps(payload), encoding="utf-8")
    return artifact_path


def _rewrite_finding_expires_at(index_path: Path, *, when: datetime, index: int = 0) -> None:
    records = json.loads(index_path.read_text(encoding="utf-8"))
    records[index]["expires_at"] = when.isoformat()
    index_path.write_text(json.dumps(records), encoding="utf-8")


# --------------------------------------------------------------------------------------
# default-inert: nothing is written to disk until the first record
# --------------------------------------------------------------------------------------


def test_no_findings_dir_created_until_first_record(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    assert not (root / ".tensor-grep").exists()

    result = ledger_store.find_findings(str(root), symbol="value")
    assert result == {"findings": [], "count": 0, "any_fresh": False}
    assert not (root / ".tensor-grep").exists()


def test_first_record_creates_findings_dir_index_and_blob(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    artifact = _write_artifact_json(tmp_path, "artifact.json", {"hello": "world"})
    result = ledger_store.record_finding(
        str(root), receipt_path=str(artifact), symbol="value", agent_id="agent-a"
    )
    assert _findings_index_path(root).exists()
    sha = result["finding"]["receipt_sha256"]
    assert (_findings_blobs_dir(root) / f"{sha}.json").exists()


# --------------------------------------------------------------------------------------
# record: usage/validation errors -- fail closed, nothing written
# --------------------------------------------------------------------------------------


def test_record_requires_receipt_path(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    with pytest.raises(ledger_store.LedgerUsageError):
        ledger_store.record_finding(str(root), agent_id="agent-a")
    assert not (root / ".tensor-grep").exists()


def test_record_rejects_invalid_artifact_kind(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    artifact = _write_artifact_json(tmp_path, "artifact.json", {"x": 1})
    with pytest.raises(ledger_store.LedgerUsageError):
        ledger_store.record_finding(
            str(root), receipt_path=str(artifact), artifact_kind="not-a-real-kind"
        )
    assert not (root / ".tensor-grep").exists()


def test_record_missing_receipt_file_raises_artifact_error(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    with pytest.raises(ledger_store.LedgerArtifactError):
        ledger_store.record_finding(str(root), receipt_path=str(tmp_path / "nope.json"))
    assert not (root / ".tensor-grep").exists()


def test_record_non_json_receipt_raises_artifact_error(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    bad = tmp_path / "bad.json"
    bad.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(ledger_store.LedgerArtifactError):
        ledger_store.record_finding(str(root), receipt_path=str(bad))


def test_record_oversized_receipt_raises_artifact_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _make_project(tmp_path)
    artifact = _write_artifact_json(tmp_path, "artifact.json", {"x": "y" * 1000})
    monkeypatch.setattr(ledger_store, "_MAX_ARTIFACT_FILE_BYTES", 4)
    with pytest.raises(ledger_store.LedgerArtifactError):
        ledger_store.record_finding(str(root), receipt_path=str(artifact))


def test_record_non_object_receipt_raises_artifact_error(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    artifact = tmp_path / "array.json"
    artifact.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    with pytest.raises(ledger_store.LedgerArtifactError):
        ledger_store.record_finding(str(root), receipt_path=str(artifact))


# --------------------------------------------------------------------------------------
# record: shape, dedup, signed metadata
# --------------------------------------------------------------------------------------


def test_record_shape_and_schema_fields(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    artifact = _write_artifact_json(tmp_path, "artifact.json", {"a": 1})
    result = ledger_store.record_finding(
        str(root),
        receipt_path=str(artifact),
        artifact_kind="blast-radius",
        symbol="value",
        agent_id="agent-a",
    )
    finding = result["finding"]
    assert finding["finding_id"].startswith("finding-")
    assert finding["ledger_schema_version"] == ledger_store.LEDGER_SCHEMA_VERSION == 1
    assert finding["kind"] == "finding"
    assert finding["agent_id"] == "agent-a"
    assert finding["artifact_kind"] == "blast-radius"
    assert finding["symbol"] == "value"
    assert finding["signed"] is False
    assert finding["key_id"] is None
    assert finding["blob_relpath"] == f"findings/blobs/{finding['receipt_sha256']}.json"
    assert "revision" in finding
    assert finding["ttl_seconds"] == 86400


def test_record_default_artifact_kind_is_evidence_receipt(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    artifact = _write_artifact_json(tmp_path, "artifact.json", {"a": 1})
    result = ledger_store.record_finding(str(root), receipt_path=str(artifact))
    assert result["finding"]["artifact_kind"] == "evidence-receipt"


def test_record_blank_symbol_becomes_none(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    artifact = _write_artifact_json(tmp_path, "artifact.json", {"a": 1})
    result = ledger_store.record_finding(str(root), receipt_path=str(artifact), symbol="   ")
    assert result["finding"]["symbol"] is None


def test_record_ttl_override_and_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _make_project(tmp_path)
    artifact = _write_artifact_json(tmp_path, "artifact.json", {"a": 1})
    result = ledger_store.record_finding(str(root), receipt_path=str(artifact), ttl_seconds=42)
    assert result["finding"]["ttl_seconds"] == 42

    monkeypatch.setenv("TG_LEDGER_FINDING_TTL_SECONDS", "123")
    result2 = ledger_store.record_finding(str(root), receipt_path=str(artifact))
    assert result2["finding"]["ttl_seconds"] == 123


def test_record_dedupes_identical_artifact_to_one_blob(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    artifact = _write_artifact_json(tmp_path, "artifact.json", {"same": "content"})

    first = ledger_store.record_finding(
        str(root), receipt_path=str(artifact), symbol="alpha", agent_id="agent-a"
    )
    second = ledger_store.record_finding(
        str(root), receipt_path=str(artifact), symbol="beta", agent_id="agent-b"
    )
    assert first["finding"]["receipt_sha256"] == second["finding"]["receipt_sha256"]
    assert first["finding"]["finding_id"] != second["finding"]["finding_id"]  # two pointers...
    blob_files = list(_findings_blobs_dir(root).iterdir())
    assert len(blob_files) == 1  # ...one blob


def test_record_signed_artifact_captures_signed_true_and_key_id(tmp_path: Path) -> None:
    from tensor_grep.cli import evidence_signing

    root = _make_project(tmp_path)
    key_path = tmp_path / "signing.key"
    keypair = evidence_signing.generate_keypair(key_path)
    signed_artifact = evidence_signing.sign_receipt(
        {"kind": "evidence-receipt"}, private_key_path=key_path
    )
    artifact_path = _write_artifact_json(tmp_path, "signed.json", signed_artifact)

    result = ledger_store.record_finding(str(root), receipt_path=str(artifact_path), symbol="value")
    assert result["finding"]["signed"] is True
    assert result["finding"]["key_id"] == keypair["key_id"]


# --------------------------------------------------------------------------------------
# find: usage errors, empty results
# --------------------------------------------------------------------------------------


def test_find_requires_symbol(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    with pytest.raises(ledger_store.LedgerUsageError):
        ledger_store.find_findings(str(root), symbol="")
    with pytest.raises(ledger_store.LedgerUsageError):
        ledger_store.find_findings(str(root), symbol="   ")


def test_find_no_match_returns_empty(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    artifact = _write_artifact_json(tmp_path, "artifact.json", {"a": 1})
    ledger_store.record_finding(str(root), receipt_path=str(artifact), symbol="alpha")

    result = ledger_store.find_findings(str(root), symbol="beta")
    assert result == {"findings": [], "count": 0, "any_fresh": False}


def test_find_filters_by_artifact_kind(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    artifact_a = _write_artifact_json(tmp_path, "a.json", {"kind": "a"})
    artifact_b = _write_artifact_json(tmp_path, "b.json", {"kind": "b"})
    ledger_store.record_finding(
        str(root), receipt_path=str(artifact_a), artifact_kind="blast-radius", symbol="value"
    )
    ledger_store.record_finding(
        str(root), receipt_path=str(artifact_b), artifact_kind="repo-map", symbol="value"
    )

    only_blast = ledger_store.find_findings(str(root), symbol="value", artifact_kind="blast-radius")
    assert only_blast["count"] == 1
    assert only_blast["findings"][0]["artifact_kind"] == "blast-radius"

    both = ledger_store.find_findings(str(root), symbol="value")
    assert both["count"] == 2


# --------------------------------------------------------------------------------------
# find: freshness -- revision-primary, never fabricated, never poisoned by the ledger's own
# on-disk footprint (record itself writes under .tensor-grep/ledger/findings/, which must NOT
# make the very next find() see the repo as dirty against itself)
# --------------------------------------------------------------------------------------


def test_find_fresh_true_for_matching_revision(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    _git_init(root)
    artifact = _write_artifact_json(tmp_path, "artifact.json", {"a": 1})
    ledger_store.record_finding(str(root), receipt_path=str(artifact), symbol="value")

    result = ledger_store.find_findings(str(root), symbol="value")
    assert result["count"] == 1
    assert result["findings"][0]["fresh"] is True
    assert result["any_fresh"] is True


def test_find_fresh_false_when_revision_unavailable(tmp_path: Path) -> None:
    root = _make_project(tmp_path)  # never git-inited -- revision status stays "unavailable"
    artifact = _write_artifact_json(tmp_path, "artifact.json", {"a": 1})
    ledger_store.record_finding(str(root), receipt_path=str(artifact), symbol="value")

    result = ledger_store.find_findings(str(root), symbol="value")
    assert result["findings"][0]["fresh"] is False  # never fabricated as fresh
    assert result["any_fresh"] is False


def test_find_freshness_flips_false_after_dirty_tree_change(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    _git_init(root)
    artifact = _write_artifact_json(tmp_path, "artifact.json", {"a": 1})
    ledger_store.record_finding(str(root), receipt_path=str(artifact), symbol="value")

    before = ledger_store.find_findings(str(root), symbol="value")
    assert before["findings"][0]["fresh"] is True

    (root / "mod.py").write_text("def value():\n    return 2\n", encoding="utf-8")  # real edit

    after = ledger_store.find_findings(str(root), symbol="value")
    assert after["findings"][0]["fresh"] is False
    assert after["any_fresh"] is False


def test_find_fresh_only_excludes_stale(tmp_path: Path) -> None:
    root = _make_project(tmp_path)  # revision unavailable -> never fresh
    artifact = _write_artifact_json(tmp_path, "artifact.json", {"a": 1})
    ledger_store.record_finding(str(root), receipt_path=str(artifact), symbol="value")

    result = ledger_store.find_findings(str(root), symbol="value", fresh_only=True)
    assert result == {"findings": [], "count": 0, "any_fresh": False}


# --------------------------------------------------------------------------------------
# find: integrity -- fail-closed, never serve tampered/corrupted data
# --------------------------------------------------------------------------------------


def test_find_tampered_blob_raises_integrity_error(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    artifact = _write_artifact_json(tmp_path, "artifact.json", {"a": 1})
    result = ledger_store.record_finding(str(root), receipt_path=str(artifact), symbol="value")
    sha = result["finding"]["receipt_sha256"]
    blob_path = _findings_blobs_dir(root) / f"{sha}.json"

    blob_path.write_text(json.dumps({"a": 2}), encoding="utf-8")  # tamper: different content

    with pytest.raises(ledger_store.LedgerIntegrityError):
        ledger_store.find_findings(str(root), symbol="value")


def test_find_missing_blob_raises_integrity_error(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    artifact = _write_artifact_json(tmp_path, "artifact.json", {"a": 1})
    result = ledger_store.record_finding(str(root), receipt_path=str(artifact), symbol="value")
    sha = result["finding"]["receipt_sha256"]
    (_findings_blobs_dir(root) / f"{sha}.json").unlink()

    with pytest.raises(ledger_store.LedgerIntegrityError):
        ledger_store.find_findings(str(root), symbol="value")


def test_find_ignores_tampered_blob_relpath_and_uses_content_address(tmp_path: Path) -> None:
    """NIT 1 regression: the blob READ path is derived from the recorded receipt_sha256 via
    _blob_path, never trusted from the index's own blob_relpath string.

    Setup: leave the REAL content-addressed blob (named by the correct sha256) untouched and
    correct, but corrupt ONLY blob_relpath in the index to point at a DECOY file with
    DIFFERENT content. If find_findings ever read from blob_relpath instead of the content
    address, the decoy's digest would not match receipt_sha256 and this would raise
    LedgerIntegrityError. It must NOT raise -- proving the read is content-addressed, not
    index-relpath-addressed.
    """
    root = _make_project(tmp_path)
    artifact = _write_artifact_json(tmp_path, "artifact.json", {"a": 1})
    ledger_store.record_finding(str(root), receipt_path=str(artifact), symbol="value")

    decoy_path = _findings_blobs_dir(root) / "decoy.json"
    decoy_path.write_text(json.dumps({"decoy": True}), encoding="utf-8")

    records = json.loads(_findings_index_path(root).read_text(encoding="utf-8"))
    records[0]["blob_relpath"] = "findings/blobs/decoy.json"
    _findings_index_path(root).write_text(json.dumps(records), encoding="utf-8")

    result = ledger_store.find_findings(str(root), symbol="value")  # must NOT raise
    assert result["count"] == 1


def test_find_refuses_malformed_receipt_sha256(tmp_path: Path) -> None:
    """NIT 1 regression: a hand-tampered receipt_sha256 that is not a well-formed 64-char hex
    digest (e.g. a path-traversal-shaped string) is refused outright before _blob_path is ever
    constructed from it."""
    root = _make_project(tmp_path)
    artifact = _write_artifact_json(tmp_path, "artifact.json", {"a": 1})
    ledger_store.record_finding(str(root), receipt_path=str(artifact), symbol="value")

    records = json.loads(_findings_index_path(root).read_text(encoding="utf-8"))
    records[0]["receipt_sha256"] = "../../../../elsewhere"
    _findings_index_path(root).write_text(json.dumps(records), encoding="utf-8")

    with pytest.raises(ledger_store.LedgerIntegrityError, match="malformed receipt_sha256"):
        ledger_store.find_findings(str(root), symbol="value")


def test_find_does_not_integrity_check_filtered_out_stale_findings(tmp_path: Path) -> None:
    """A tampered blob for a finding EXCLUDED by --fresh-only must not fail the whole call --
    only findings actually being served are integrity-checked."""
    root = _make_project(tmp_path)  # revision unavailable -> never fresh, always excluded
    artifact = _write_artifact_json(tmp_path, "artifact.json", {"a": 1})
    result = ledger_store.record_finding(str(root), receipt_path=str(artifact), symbol="value")
    sha = result["finding"]["receipt_sha256"]
    (_findings_blobs_dir(root) / f"{sha}.json").write_text(json.dumps({"a": 2}), encoding="utf-8")

    out = ledger_store.find_findings(str(root), symbol="value", fresh_only=True)
    assert out == {"findings": [], "count": 0, "any_fresh": False}


# --------------------------------------------------------------------------------------
# find: signed / key_trusted
# --------------------------------------------------------------------------------------


def test_find_signed_finding_key_trusted_true_with_pinned_key(tmp_path: Path) -> None:
    from tensor_grep.cli import evidence_signing

    root = _make_project(tmp_path)
    key_path = tmp_path / "signing.key"
    keypair = evidence_signing.generate_keypair(key_path)
    signed_artifact = evidence_signing.sign_receipt(
        {"kind": "evidence-receipt"}, private_key_path=key_path
    )
    artifact_path = _write_artifact_json(tmp_path, "signed.json", signed_artifact)
    ledger_store.record_finding(str(root), receipt_path=str(artifact_path), symbol="value")

    trusted = ledger_store.find_findings(
        str(root), symbol="value", trusted_public_keys=[keypair["public_key"]]
    )
    assert trusted["findings"][0]["key_trusted"] is True

    untrusted = ledger_store.find_findings(str(root), symbol="value")
    assert untrusted["findings"][0]["key_trusted"] is None  # no trusted keys configured


def test_find_unsigned_finding_key_trusted_is_none(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    artifact = _write_artifact_json(tmp_path, "artifact.json", {"a": 1})
    ledger_store.record_finding(str(root), receipt_path=str(artifact), symbol="value")

    result = ledger_store.find_findings(str(root), symbol="value")
    assert result["findings"][0]["signed"] is False
    assert result["findings"][0]["key_trusted"] is None


# --------------------------------------------------------------------------------------
# cap eviction (count + total blob bytes) and blob GC (expired + orphaned)
# --------------------------------------------------------------------------------------


def test_eviction_caps_live_findings_at_max_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _make_project(tmp_path)
    monkeypatch.setattr(ledger_store, "_MAX_LIVE_FINDINGS", 3)
    for i in range(5):
        artifact = _write_artifact_json(tmp_path, f"artifact{i}.json", {"i": i})
        ledger_store.record_finding(
            str(root), receipt_path=str(artifact), symbol=f"sym{i}", agent_id=f"agent-{i}"
        )
    records = json.loads(_findings_index_path(root).read_text(encoding="utf-8"))
    assert len(records) == 3
    kept_symbols = {r["symbol"] for r in records}
    assert kept_symbols == {"sym2", "sym3", "sym4"}  # oldest 2 evicted


def test_eviction_over_byte_cap_evicts_oldest_and_gcs_blob(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _make_project(tmp_path)
    artifact_payload = {"a": "x" * 200}
    # Derive the cap from the REAL on-disk blob size (not a guessed constant): room for exactly
    # one artifact's blob plus headroom, but not two -- deterministic regardless of exact JSON
    # serialization details. The byte cap is floored at _MAX_ARTIFACT_FILE_BYTES (NIT 2 fix: a
    # cap below one artifact's size must never silently evict the record just written), so
    # exercising byte-cap eviction with small fixture artifacts also requires shrinking that
    # floor -- otherwise the real 8 MiB default would swallow both tiny artifacts and nothing
    # would ever be evicted.
    one_blob_bytes = len(json.dumps(artifact_payload, indent=2).encode("utf-8"))
    monkeypatch.setattr(ledger_store, "_MAX_ARTIFACT_FILE_BYTES", one_blob_bytes + 50)
    monkeypatch.setenv("TG_LEDGER_MAX_BLOB_BYTES", str(one_blob_bytes + 50))

    artifact_a = _write_artifact_json(tmp_path, "a.json", artifact_payload)
    result_a = ledger_store.record_finding(str(root), receipt_path=str(artifact_a), symbol="alpha")
    sha_a = result_a["finding"]["receipt_sha256"]
    assert (_findings_blobs_dir(root) / f"{sha_a}.json").exists()

    artifact_b = _write_artifact_json(tmp_path, "b.json", {"b": "y" * 200})
    ledger_store.record_finding(str(root), receipt_path=str(artifact_b), symbol="beta")

    records = json.loads(_findings_index_path(root).read_text(encoding="utf-8"))
    assert {r["symbol"] for r in records} == {"beta"}  # alpha evicted (oldest)
    assert not (_findings_blobs_dir(root) / f"{sha_a}.json").exists()  # ...and its blob GC'd


def test_record_survives_pathologically_small_byte_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """NIT 2 regression: TG_LEDGER_MAX_BLOB_BYTES configured well below a single artifact's
    size must never silently evict the record `record_finding` just wrote in the SAME call --
    the byte cap is floored at _MAX_ARTIFACT_FILE_BYTES so one artifact always fits, no matter
    how small the configured/env cap is. Before the fix, this exact scenario made `record`
    return a "success" finding payload for a record that was immediately evicted + GC'd."""
    root = _make_project(tmp_path)
    monkeypatch.setenv("TG_LEDGER_MAX_BLOB_BYTES", "1")  # pathologically small
    artifact = _write_artifact_json(tmp_path, "artifact.json", {"a": 1})

    result = ledger_store.record_finding(str(root), receipt_path=str(artifact), symbol="value")
    sha = result["finding"]["receipt_sha256"]

    records = json.loads(_findings_index_path(root).read_text(encoding="utf-8"))
    assert len(records) == 1
    assert records[0]["symbol"] == "value"
    assert (_findings_blobs_dir(root) / f"{sha}.json").exists()


def test_expired_findings_pruned_and_orphaned_blob_gc_on_next_record(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    artifact_a = _write_artifact_json(tmp_path, "a.json", {"a": 1})
    result_a = ledger_store.record_finding(
        str(root), receipt_path=str(artifact_a), symbol="alpha", ttl_seconds=1
    )
    sha_a = result_a["finding"]["receipt_sha256"]
    _rewrite_finding_expires_at(
        _findings_index_path(root), when=datetime.now(UTC) - timedelta(seconds=5)
    )

    artifact_b = _write_artifact_json(tmp_path, "b.json", {"b": 1})
    ledger_store.record_finding(str(root), receipt_path=str(artifact_b), symbol="beta")

    records = json.loads(_findings_index_path(root).read_text(encoding="utf-8"))
    assert len(records) == 1
    assert records[0]["symbol"] == "beta"
    assert not (_findings_blobs_dir(root) / f"{sha_a}.json").exists()


def test_shared_blob_not_gcd_while_any_referencing_finding_survives(tmp_path: Path) -> None:
    """Two findings pointing at the SAME dedup'd blob: expiring/evicting ONE must not delete
    the blob out from under the other."""
    root = _make_project(tmp_path)
    artifact = _write_artifact_json(tmp_path, "same.json", {"same": True})
    first = ledger_store.record_finding(
        str(root), receipt_path=str(artifact), symbol="alpha", ttl_seconds=1
    )
    ledger_store.record_finding(str(root), receipt_path=str(artifact), symbol="beta")
    sha = first["finding"]["receipt_sha256"]

    _rewrite_finding_expires_at(
        _findings_index_path(root), when=datetime.now(UTC) - timedelta(seconds=5), index=0
    )
    artifact_c = _write_artifact_json(tmp_path, "c.json", {"c": 1})
    ledger_store.record_finding(str(root), receipt_path=str(artifact_c), symbol="gamma")

    records = json.loads(_findings_index_path(root).read_text(encoding="utf-8"))
    assert {r["symbol"] for r in records} == {"beta", "gamma"}  # alpha's pointer gone (expired)
    assert (_findings_blobs_dir(root) / f"{sha}.json").exists()  # ...but the SHARED blob lives


# --------------------------------------------------------------------------------------
# bounded/corrupt findings index reads fail closed (never silently read as "no findings")
# --------------------------------------------------------------------------------------


def test_oversized_findings_index_refuses_to_parse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _make_project(tmp_path)
    artifact = _write_artifact_json(tmp_path, "artifact.json", {"a": 1})
    ledger_store.record_finding(str(root), receipt_path=str(artifact), symbol="value")
    monkeypatch.setattr(ledger_store, "_MAX_INDEX_FILE_BYTES", 4)
    with pytest.raises(ledger_store.LedgerIndexTooLargeError):
        ledger_store.find_findings(str(root), symbol="value")


def test_corrupt_findings_index_fails_closed(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    artifact = _write_artifact_json(tmp_path, "artifact.json", {"a": 1})
    ledger_store.record_finding(str(root), receipt_path=str(artifact), symbol="value")
    _findings_index_path(root).write_text("{not valid json", encoding="utf-8")
    with pytest.raises(ledger_store.LedgerCorruptIndexError):
        ledger_store.find_findings(str(root), symbol="value")


def test_non_array_findings_index_fails_closed(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    artifact = _write_artifact_json(tmp_path, "artifact.json", {"a": 1})
    ledger_store.record_finding(str(root), receipt_path=str(artifact), symbol="value")
    _findings_index_path(root).write_text(json.dumps({"not": "an array"}), encoding="utf-8")
    with pytest.raises(ledger_store.LedgerCorruptIndexError):
        ledger_store.find_findings(str(root), symbol="value")


# --------------------------------------------------------------------------------------
# findings and claims coexist without cross-contamination (separate index.json each)
# --------------------------------------------------------------------------------------


def test_findings_and_claims_indices_are_independent(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    ledger_store.submit_claim(str(root), symbols=["value"], agent_id="agent-a")
    artifact = _write_artifact_json(tmp_path, "artifact.json", {"a": 1})
    ledger_store.record_finding(str(root), receipt_path=str(artifact), symbol="value")

    assert ledger_store.list_claims(str(root))["count"] == 1
    assert ledger_store.find_findings(str(root), symbol="value")["count"] == 1
    assert _findings_index_path(root) != ledger_store._index_path(root)

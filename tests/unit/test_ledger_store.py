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
    assert result == {"released": [], "released_count": 0}
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
    assert result == {"released": [], "released_count": 0}


def test_release_by_symbol_does_not_cross_agents(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    ledger_store.submit_claim(str(root), symbols=["value"], agent_id="agent-a")
    result = ledger_store.release_claim(str(root), symbol="value", agent_id="agent-b")
    assert result == {"released": [], "released_count": 0}
    assert ledger_store.list_claims(str(root))["count"] == 1


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

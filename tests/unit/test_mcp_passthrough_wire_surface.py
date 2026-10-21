"""A pass-through MCP handler makes the producer it wraps an MCP WIRE SURFACE.

Task 336's `budget_remediable` reached the wire twice. The first time (#762, `tg_search`) it was
written in `mcp_server.py` and the contract went 1.4.0 -> 1.5.0 correctly. The second time (#826)
it was added to `build_repo_map` in `repo_map.py` -- a CLI-shaped change that names MCP nowhere --
and no bump happened, because the repo's rule is phrased around "a new MCP tool is a registration
site". #826 added no tool. It edited a payload builder that `tg_repo_map` returns VERBATIM:

    return _inject_mcp_contract_fields(json.dumps(build_repo_map(path, ...), indent=2))

so every `scan_limit` field the CLI gained, MCP gained in the same commit, and shipped for several
releases advertising a contract version that promised only `tg_search`'s copy of the field.

Measured on `origin/main` before the bump: `build_repo_map(<dir>, max_repo_files=3)` returns
`scan_limit = {..., "truncation_cause": "project-files", "budget_remediable": True}` at contract
1.6.0.

This test pins the CONSEQUENCE rather than the instance: the fields a pass-through handler can put
on the wire are enumerated here, so adding one to the producer fails until it is declared -- the
declaration being the moment someone has to ask whether the contract version owes a bump.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any, cast

import pytest
from mcp import types

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "src"))

from tensor_grep.cli import checkpoint_store, mcp_server  # noqa: E402
from tensor_grep.cli.mcp_server import (  # noqa: E402
    _TG_MCP_SERVER_CONTRACT_VERSION,
    _inject_mcp_contract_fields,
)
from tensor_grep.cli.repo_map import build_repo_map  # noqa: E402

# Every key `build_repo_map`'s `scan_limit` may put on the MCP wire via `tg_repo_map`. Adding one
# to the producer must land here DELIBERATELY, and that edit is the prompt to ask whether
# `_TG_MCP_SERVER_CONTRACT_VERSION` owes a bump. Do not widen this to a subset check: the whole
# point is that an undeclared field fails.
_DECLARED_SCAN_LIMIT_KEYS = {
    "max_repo_files",
    "scanned_files",
    "possibly_truncated",
    "truncation_cause",
    "budget_remediable",
}

# The version in force when the key set above was last reviewed. This is not a "bump me every
# release" pin -- it moves only when the wire shape changes, which is exactly when a reviewer
# should be looking.
_CONTRACT_AT_LAST_WIRE_REVIEW = "1.20.0"


@pytest.fixture
def checkpoint_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    mcp_root = tmp_path / "mcp-root"
    project = mcp_root / "project"
    project.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (project / "sample.py").write_text("value = 'before'\n", encoding="utf-8")
    monkeypatch.setenv("TG_MCP_ROOT", str(mcp_root))
    return project, outside


def _call_checkpoint(tool: str, arguments: dict[str, object]) -> tuple[dict[str, Any], bool]:
    result = asyncio.run(mcp_server.mcp.call_tool(tool, arguments))
    if isinstance(result, types.CallToolResult):
        payload = cast(dict[str, Any], json.loads(result.content[0].text))
        return payload, result.isError
    content, _metadata = result
    payload = cast(dict[str, Any], json.loads(cast(Any, content)[0].text))
    return payload, isinstance(payload.get("error"), dict) and bool(payload["error"])


def _assert_checkpoint_envelope(payload: dict[str, Any]) -> None:
    assert payload["incomplete"] == {
        "status": False,
        "cause": None,
        "budget_remediable": False,
    }
    assert payload["mcp_contract_version"] == mcp_server._TG_MCP_SERVER_CONTRACT_VERSION
    serialized = json.dumps(payload, indent=2)
    assert _inject_mcp_contract_fields(serialized) == serialized


def _refused_checkpoint_arguments(tool: str, action: str, outside: Path) -> dict[str, object]:
    if tool == "tg_checkpoint":
        return {"action": action, "path": str(outside), "checkpoint_id": "missing"}
    if action == "undo":
        return {"path": str(outside), "checkpoint_id": "missing"}
    return {"path": str(outside)}


def _capped_scan_limit(tmp_path: Path) -> dict:
    """A payload from a scan that really was capped, not a hand-built dict.

    A literal would pass whatever the producer did, which is the failure mode this file exists
    for. `max_repo_files=1` against a directory with several files guarantees the cap bites.
    """
    for i in range(4):
        (tmp_path / f"mod_{i}.py").write_text(f"def f_{i}():\n    return {i}\n", encoding="utf-8")
    payload = build_repo_map(str(tmp_path), max_repo_files=1)
    scan_limit = payload.get("scan_limit")
    assert isinstance(scan_limit, dict), (
        f"premise failed: no scan_limit on the payload, so the cap never bit "
        f"(payload keys: {sorted(payload)}) -- this test would prove nothing"
    )
    assert scan_limit.get("possibly_truncated") is True, (
        f"premise failed: scan_limit present but not truncated ({scan_limit}); the capped "
        "branch that emits the wire fields was never reached"
    )
    return scan_limit


def test_repo_map_scan_limit_puts_no_undeclared_field_on_the_mcp_wire(tmp_path: Path) -> None:
    scan_limit = _capped_scan_limit(tmp_path)
    undeclared = set(scan_limit) - _DECLARED_SCAN_LIMIT_KEYS
    assert not undeclared, (
        f"build_repo_map's scan_limit gained {sorted(undeclared)}, which `tg_repo_map` returns "
        "VERBATIM -- so this is an MCP wire change made from a file that never mentions MCP. "
        "Declare it above, then decide whether _TG_MCP_SERVER_CONTRACT_VERSION owes a bump."
    )


def test_budget_remediable_is_actually_on_the_wire_at_the_declared_contract(
    tmp_path: Path,
) -> None:
    # The regression itself, pinned in both directions: the field must be PRESENT (so a future
    # change cannot quietly drop the machine-branchable flag), and the contract version must be
    # the one under which that presence was reviewed.
    scan_limit = _capped_scan_limit(tmp_path)
    assert "budget_remediable" in scan_limit, (
        "budget_remediable vanished from repo_map's capped scan_limit; MCP clients branch on it "
        "to decide whether a retry with a bigger budget is worth attempting"
    )
    assert isinstance(scan_limit["budget_remediable"], bool)
    assert _TG_MCP_SERVER_CONTRACT_VERSION == _CONTRACT_AT_LAST_WIRE_REVIEW, (
        f"the MCP contract moved to {_TG_MCP_SERVER_CONTRACT_VERSION} since this wire shape was "
        f"last reviewed at {_CONTRACT_AT_LAST_WIRE_REVIEW}. Re-check the declared key set above "
        "against what the pass-through handlers actually serve, then update this constant."
    )


def test_CheckpointCall_ValidCreateListUndo_EmitsCompleteEnvelope(
    checkpoint_roots: tuple[Path, Path],
) -> None:
    project, _outside = checkpoint_roots
    source = project / "sample.py"

    created, create_error = _call_checkpoint(
        "tg_checkpoint_create", {"path": str(project), "label": "wire checkpoint"}
    )
    listed, list_error = _call_checkpoint("tg_checkpoint_list", {"path": str(project)})
    source.write_text("value = 'after'\n", encoding="utf-8")
    undone, undo_error = _call_checkpoint(
        "tg_checkpoint_undo",
        {"path": str(project), "checkpoint_id": created["checkpoint_id"]},
    )

    assert not create_error and not list_error and not undo_error
    assert created["label"] == "wire checkpoint"
    assert len(listed["checkpoints"]) == 1
    assert listed["checkpoints"][0]["checkpoint_id"] == created["checkpoint_id"]
    assert source.read_text(encoding="utf-8") == "value = 'before'\n"
    assert undone["checkpoint_id"] == created["checkpoint_id"]
    _assert_checkpoint_envelope(created)
    _assert_checkpoint_envelope(listed)
    _assert_checkpoint_envelope(undone)


def test_CheckpointMeta_HealthyList_ReturnsCompleteStructuredJSON(
    checkpoint_roots: tuple[Path, Path],
) -> None:
    project, _outside = checkpoint_roots
    created, create_error = _call_checkpoint(
        "tg_checkpoint_create", {"path": str(project), "label": "listed checkpoint"}
    )

    listed, list_error = _call_checkpoint("tg_checkpoint", {"action": "list", "path": str(project)})

    assert not create_error and not list_error
    assert listed["checkpoints"][0]["checkpoint_id"] == created["checkpoint_id"]
    _assert_checkpoint_envelope(listed)


def test_CheckpointMeta_ListFailure_ReturnsSanitizedJSONAndLogsDetail(
    checkpoint_roots: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    project, _outside = checkpoint_roots
    secret_detail = "private checkpoint backend detail"

    def fail_list(*, path: str) -> str:
        raise RuntimeError(secret_detail)

    monkeypatch.setattr(mcp_server, "tg_checkpoint_list", fail_list)

    payload, is_error = _call_checkpoint("tg_checkpoint", {"action": "list", "path": str(project)})

    error_output = json.dumps(payload)
    captured = capsys.readouterr()

    assert is_error
    assert payload["error"]["code"] == "internal_error"
    assert (
        payload["error"]["message"]
        == "tg_checkpoint failed due to an internal error (RuntimeError)."
    )
    assert payload["incomplete"] == {"status": False, "cause": None, "budget_remediable": False}
    assert payload["mcp_contract_version"] == "1.20.0"
    assert secret_detail not in error_output
    assert secret_detail in captured.err
    assert not (project / ".tensor-grep").exists()


@pytest.mark.parametrize(
    ("tool", "action"),
    [
        ("tg_checkpoint_create", "create"),
        ("tg_checkpoint_list", "list"),
        ("tg_checkpoint_undo", "undo"),
        ("tg_checkpoint", "create"),
        ("tg_checkpoint", "list"),
        ("tg_checkpoint", "undo"),
    ],
)
def test_CheckpointCall_RefusedInput_PreservesErrorAndCompleteness(
    checkpoint_roots: tuple[Path, Path], tool: str, action: str
) -> None:
    project, outside = checkpoint_roots
    source = project / "sample.py"
    before = source.read_bytes()

    payload, is_error = _call_checkpoint(tool, _refused_checkpoint_arguments(tool, action, outside))

    assert is_error is True
    assert payload["error"]["code"] == "invalid_input"
    assert source.read_bytes() == before
    assert not (outside / ".tensor-grep").exists()
    _assert_checkpoint_envelope(payload)


def test_CheckpointCall_CorruptRealStore_PreservesSanitizedFailureEnvelope(
    checkpoint_roots: tuple[Path, Path],
) -> None:
    project, _outside = checkpoint_roots
    source = project / "sample.py"
    checkpoint = checkpoint_store.create_checkpoint(str(project), label="corrupt-me")
    source.write_text("value = 'after'\n", encoding="utf-8")
    checkpoint_store._metadata_path(project, checkpoint.checkpoint_id).write_text(
        "{", encoding="utf-8"
    )

    payload, is_error = _call_checkpoint(
        "tg_checkpoint_undo",
        {"path": str(project), "checkpoint_id": checkpoint.checkpoint_id},
    )

    assert is_error is True
    assert payload["error"]["code"] == "internal_error"
    assert "Expecting property name" not in payload["error"]["message"]
    assert source.read_text(encoding="utf-8") == "value = 'after'\n"
    _assert_checkpoint_envelope(payload)


def test_CheckpointCall_MetaAndLegacy_EmitEquivalentCompleteness(
    checkpoint_roots: tuple[Path, Path],
) -> None:
    project, _outside = checkpoint_roots

    meta, meta_error = _call_checkpoint(
        "tg_checkpoint", {"action": "create", "path": str(project), "label": "meta"}
    )
    legacy, legacy_error = _call_checkpoint(
        "tg_checkpoint_create", {"path": str(project), "label": "legacy"}
    )

    assert not meta_error and not legacy_error
    assert meta["label"] == "meta"
    assert legacy["label"] == "legacy"
    assert meta["incomplete"] == legacy["incomplete"]
    _assert_checkpoint_envelope(meta)
    _assert_checkpoint_envelope(legacy)


def test_CheckpointMeta_MissingDispatch_ReturnsSanitizedFailure(
    checkpoint_roots: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    project, _outside = checkpoint_roots
    monkeypatch.delattr(mcp_server, "_checkpoint_meta")

    response = mcp_server.tg_checkpoint(action="list", path=str(project))

    assert response == (
        "tg_checkpoint failed: internal error (AttributeError). See server logs for detail."
    )
    assert not (project / ".tensor-grep").exists()

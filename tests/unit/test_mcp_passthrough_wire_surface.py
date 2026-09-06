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

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "src"))

from tensor_grep.cli.mcp_server import _TG_MCP_SERVER_CONTRACT_VERSION  # noqa: E402
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
_CONTRACT_AT_LAST_WIRE_REVIEW = "1.8.0"


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

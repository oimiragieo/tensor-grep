"""The docs must cite the LIVE MCP contract version, not a snapshot of it.

Measured 2026-07-29, with the constant at `1.7.0`:

    docs/harness_api.md   said "currently 1.2.0"   (5 minor versions stale)
    docs/architecture.md  said "= \\"1.0.0\\""       (7 minor versions stale, at a rotted anchor)

Both had been updated by hand before and gone stale again, which is the tell that a prose
re-statement of a constant needs a machine check rather than another careful edit. `harness_api.md`
even carried "re-check the constant before citing a version number, it has already moved once" --
an instruction to a human, in a file no human re-reads on a bump.

Why this matters beyond tidiness: `serverInfo.version` is what a harness negotiates against. A doc
claiming 1.0.0 tells an integrator the payload is byte-identical to the 1.0.0 body, when 1.5.0
added `truncation_cause`/`budget_remediable`, 1.6.0 added `incomplete_reason_class`, and 1.7.0
covered a pass-through wire surface. Every one of those is an ADDITIVE field an integrator would
not know to read.

The tool count is pinned for the same reason and has a sharper trap: `architecture.md` said 45
`@mcp.tool()`-decorated functions. There are 12 decorators. Most tools register through
`_register_legacy_tool`, which calls `mcp.tool()` only when legacy tools are enabled -- so grepping
the decorator undercounts by ~46 and the real total is `len(_MCP_TOOL_CAPABILITIES)` = 58. Anyone
"correcting" the doc by grepping would have made it worse.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_DOCS = ("docs/harness_api.md", "docs/architecture.md")


def _live_contract_version() -> str:
    source = (_ROOT / "src" / "tensor_grep" / "cli" / "mcp_server.py").read_text(encoding="utf-8")
    match = re.search(r'^_TG_MCP_SERVER_CONTRACT_VERSION\s*=\s*"([^"]+)"', source, re.MULTILINE)
    assert match, "could not find _TG_MCP_SERVER_CONTRACT_VERSION; update this guard with it"
    return match.group(1)


def test_the_live_constant_is_parseable() -> None:
    """PREMISE: everything below compares against this. If the parse breaks, the other tests would
    compare against nothing and pass for the wrong reason."""
    version = _live_contract_version()
    assert re.fullmatch(r"\d+\.\d+\.\d+", version), f"implausible contract version: {version!r}"


@pytest.mark.parametrize("doc", _DOCS)
def test_docs_do_not_cite_a_stale_contract_version(doc: str) -> None:
    """THE DEFECT: both docs asserted a version the code had long since passed.

    Scoped to lines that MENTION the constant by name, so the ordinary prose references to
    historical contract levels ("contract `1.5.0`+ carries truncation_cause") stay legal -- those
    are correct statements about when a field appeared, not claims about the current version.
    """
    live = _live_contract_version()
    text = (_ROOT / doc).read_text(encoding="utf-8")
    lines = text.splitlines()

    # WINDOW, not the single line. The first cut of this guard scanned only the line naming the
    # constant and PASSED on the very docs that were stale, because `harness_api.md` wraps the
    # sentence: the constant is named on one line and "currently `1.2.0`" lands on the next. A
    # guard that cannot fail on the drift it exists to catch is decoration -- caught here only
    # because the control arm was run against the pre-fix docs.
    offenders = []
    for index, line in enumerate(lines):
        if "_TG_MCP_SERVER_CONTRACT_VERSION" not in line:
            continue
        window = "\n".join(lines[index : index + 3])
        for cited in re.findall(r"`(\d+\.\d+\.\d+)`", window):
            if cited != live:
                offenders.append((index + 1, cited))

    assert not offenders, (
        f"{doc} cites contract version(s) {offenders} on a line naming "
        f"_TG_MCP_SERVER_CONTRACT_VERSION, but the live constant is {live}. "
        "serverInfo.version is what a harness negotiates against -- a stale number tells an "
        "integrator the payload is byte-identical to a body that has since gained fields."
    )


def test_the_architecture_tool_count_matches_the_registry() -> None:
    """CONTROL for the count, and a trap: the number is NOT the decorator count.

    Pinned against `_MCP_TOOL_CAPABILITIES`, the authoritative registry. Grepping `@mcp.tool()`
    yields 12 and would "fix" the doc to a number that is wrong in the other direction.
    """
    from tensor_grep.cli.mcp_server import _MCP_TOOL_CAPABILITIES

    live_count = len(_MCP_TOOL_CAPABILITIES)
    text = (_ROOT / "docs" / "architecture.md").read_text(encoding="utf-8")

    match = re.search(r"exposing (\d+)\s*\ntools", text) or re.search(r"exposing (\d+) ", text)
    assert match, "could not find the 'exposing N tools' claim in architecture.md"
    claimed = int(match.group(1))

    assert claimed == live_count, (
        f"architecture.md claims {claimed} MCP tools; the registry has {live_count}. "
        "Use len(_MCP_TOOL_CAPABILITIES), NOT a grep of @mcp.tool() -- most tools register via "
        "_register_legacy_tool and the decorator count is ~46 short."
    )

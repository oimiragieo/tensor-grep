from __future__ import annotations

import sys
from pathlib import Path

import pytest

from tensor_grep.cli.rg_contract import RG_CONTRACT_ROWS

TESTS_DIR = Path(__file__).resolve().parents[1]
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))


def _helpers():
    # Import after extending sys.path to keep the helper local to the tests tree.
    from helpers import rg_parity

    return rg_parity


@pytest.mark.characterization
@pytest.mark.parametrize(
    "case",
    _helpers().build_rg_parity_cases(RG_CONTRACT_ROWS),
    ids=lambda case: case.row["id"],
)
def test_rg_contract_row_matches_tg(case, tmp_path: Path) -> None:
    helpers = _helpers()
    corpus = helpers.create_rg_parity_corpus(tmp_path / "rg-parity")
    rg_binary = helpers.resolve_pinned_rg_binary()
    if rg_binary is None:
        pytest.skip("ripgrep binary not available for parity coverage")

    if case.needs_follow and not corpus.follow_supported:
        pytest.skip("symlink support is unavailable for follow parity coverage on this machine")

    result = helpers.run_parity_case(case=case, corpus=corpus, rg_binary=rg_binary)

    assert result.tg.returncode == result.rg.returncode, helpers.format_parity_mismatch(
        result=result,
        corpus=corpus,
        detail="exit-code mismatch",
    )
    assert helpers.normalize_stderr(result.tg.stderr, corpus=corpus) == helpers.normalize_stderr(
        result.rg.stderr,
        corpus=corpus,
    ), helpers.format_parity_mismatch(
        result=result,
        corpus=corpus,
        detail="stderr mismatch",
    )
    assert helpers.normalize_output(
        result.tg.stdout,
        case=case,
        tool="tg",
        corpus=corpus,
    ) == helpers.normalize_output(
        result.rg.stdout,
        case=case,
        tool="rg",
        corpus=corpus,
    ), helpers.format_parity_mismatch(
        result=result,
        corpus=corpus,
        detail="stdout mismatch",
    )

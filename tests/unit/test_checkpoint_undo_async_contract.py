"""Checkpoint undo documentation must not promise recovery after interruption."""

from pathlib import Path


def test_interrupted_undo_contract_is_fail_closed() -> None:
    contract = Path("docs/CONTRACTS.md").read_text(encoding="utf-8")

    for required in (
        "repository state **UNKNOWN**",
        "There is no automatic retry, replay, or background reclaimer",
        "Do not claim that recovery data or original working bytes were retained",
    ):
        assert required in contract

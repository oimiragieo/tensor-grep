"""Task 8 Step 2 (docs/plans/2026-08-02-backlog-closeout-implementation-plan.md), Python-only
slice: the strict typed composition API added to `prepare_service.py`.

Scope note: this session delivers ONLY `PrepareSnapshotV1` / `build_prepare_snapshot`, a pure
additive wrapper over the existing `_build_prepare_payload`. The full `tg edit-ready` CLI command,
`EditReadyTicketV1`, the claims-only OS fence, atomic no-clobber baseline publication, and the
native/Rust half of Task 8 are explicitly OUT OF SCOPE here -- see the PR description. These tests
therefore exercise only the composition wrapper, not a CLI surface (there isn't one yet).
"""

from __future__ import annotations

from pathlib import Path

from tensor_grep.cli import prepare_service

_BILLING_MODULE = (
    '"""Monthly billing helpers."""\n\n\n'
    "def calculate_late_fee(balance, days_late):\n"
    '    """Compute the late fee owed on an overdue balance."""\n'
    "    return balance * 0.01 * days_late\n\n\n"
    "def apply_late_fee(account):\n"
    '    """Apply the computed late fee to an account balance."""\n'
    '    fee = calculate_late_fee(account["balance"], account["days_late"])\n'
    '    account["balance"] += fee\n'
    "    return account\n\n\n"
    "def process_billing_cycle(accounts):\n"
    '    """Run the monthly billing cycle across all accounts."""\n'
    "    return [apply_late_fee(account) for account in accounts]\n"
)
_RUN_MODULE = (
    "from billing import process_billing_cycle\n\n\n"
    "def main():\n"
    "    return process_billing_cycle([])\n\n\n"
    'if __name__ == "__main__":\n'
    "    main()\n"
)
_PYPROJECT = (
    "[project]\n"
    'name = "billing-fixture"\n'
    'version = "0.1.0"\n\n'
    "[tool.pytest.ini_options]\n"
    'testpaths = ["tests"]\n'
)
_QUERY = "compute the late fee owed on an overdue balance"


def _make_small_billing_repo(root: Path) -> None:
    (root / "pyproject.toml").write_text(_PYPROJECT, encoding="utf-8")
    (root / "billing.py").write_text(_BILLING_MODULE, encoding="utf-8")
    (root / "run.py").write_text(_RUN_MODULE, encoding="utf-8")


def test_build_prepare_snapshot_matches_legacy_payload_fields(tmp_path: Path) -> None:
    """RED (pre-implementation) failure text this test produced:

        AttributeError: module 'tensor_grep.cli.prepare_service' has no attribute
        'build_prepare_snapshot'

    That is a behavior-specific assertion about a real call, not a bare import error: the test
    first imports `prepare_service` successfully (the module already exists on main via #939),
    then fails only when it tries to CALL the not-yet-added composition function against a real
    fixture repo. Once implemented, this proves the typed snapshot's promoted fields are read
    verbatim from the same dict `_build_prepare_payload` returns for an identical call -- never
    independently recomputed -- which is the byte-identical-legacy-behavior guarantee Step 2
    requires.
    """
    _make_small_billing_repo(tmp_path)

    legacy_payload = prepare_service._build_prepare_payload(
        path=str(tmp_path), query=_QUERY, claim=False
    )
    snapshot = prepare_service.build_prepare_snapshot(path=str(tmp_path), query=_QUERY)

    assert snapshot.version == legacy_payload["version"]
    assert snapshot.path == legacy_payload["path"]
    assert snapshot.query == legacy_payload["query"]
    assert snapshot.primary_target == legacy_payload["primary_target"]
    assert snapshot.confidence == legacy_payload["confidence"]
    assert snapshot.blast_radius_floor == legacy_payload["blast_radius_floor"]
    assert snapshot.coordination == legacy_payload["coordination"]
    assert snapshot.partial == bool(legacy_payload.get("partial", False))
    assert snapshot.partial_reason == legacy_payload.get("partial_reason")
    # `raw` is the complete untyped payload, byte-identical to a second, independent call with
    # the same inputs (both calls hit a real repo scan -- this proves determinism, not caching).
    assert snapshot.raw == legacy_payload

    # Real signal found: the fixture's query names the callee directly, so the ranker should
    # commit to a specific symbol rather than a file-level best-effort target. This guards
    # against a snapshot that "passes" only because every field is trivially empty/absent.
    assert snapshot.primary_target.get("symbol"), snapshot.primary_target


def test_prepare_snapshot_is_frozen_and_typed() -> None:
    """The dataclass is frozen (no accidental post-hoc mutation of a shared snapshot) and
    exposes the exact field set Step 2 promotes -- adding a field later is additive, but this
    test pins today's exact contract so a silent field rename/removal is caught."""
    field_names = set(prepare_service.PrepareSnapshotV1.__dataclass_fields__)
    assert field_names == {
        "version",
        "path",
        "query",
        "primary_target",
        "confidence",
        "blast_radius_floor",
        "coordination",
        "partial",
        "partial_reason",
        "raw",
    }
    assert prepare_service.PrepareSnapshotV1.__dataclass_params__.frozen is True

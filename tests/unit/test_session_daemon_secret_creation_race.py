"""Concurrent first use must not overwrite the shared secret (PR #1197 round 4).

Two roots starting daemons with no existing secret both passed the "absent" check and both published
with ``os.replace``; the second secret invalidated attestations the first daemon had already signed.
Creation is now serialized under the repo's lock primitive (``index_lock``) in the same trusted
directory, absence is re-checked UNDER the lock, and the secret is published WITHOUT replacing
(hard-link no-clobber); if one appeared meanwhile it is read and used after the normal trust checks.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path
from typing import Any

import pytest

from tensor_grep.cli import session_daemon_trust as trust


@pytest.fixture(autouse=True)
def _secret_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TG_DAEMON_SECRET_DIR", str(tmp_path / "secret"))


def test_two_concurrent_first_uses_end_up_with_the_same_secret_and_attestations_survive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    barrier = threading.Barrier(2, timeout=30)
    calls: list[str] = []

    def _both_have_passed_the_absent_check(parent: Path) -> None:
        calls.append("pinned")
        barrier.wait()  # neither creator proceeds until BOTH are about to create

    monkeypatch.setattr(trust, "_after_parent_pinned", _both_have_passed_the_absent_check)
    results: list[Any] = [None, None]
    errors: list[BaseException] = []

    def _worker(index: int) -> None:
        try:
            results[index] = trust._load_or_create_user_secret()
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=_worker, args=(i,)) for i in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert not errors, errors
    assert len(calls) == 2, "both creators must have reached the creation point: test is vacuous"
    first, second = results
    assert first is not None and second is not None
    assert first == second, "the two creators ended up with DIFFERENT secrets"
    final = trust._read_user_secret(trust._daemon_secret_path())
    assert final == first
    # an attestation signed by the first creator still verifies afterwards
    root = Path(__file__).parent.resolve()
    sig = trust._attestation_hmac(first, 7, 1234.5, 4242, str(root), "9.9.9")
    meta = {
        "pid": 7,
        "create_time": 1234.5,
        "port": 4242,
        "package_version": "9.9.9",
        "attestation": sig,
    }
    assert trust._verify_attestation(meta, root) is True


def test_creation_never_replaces_an_existing_trusted_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = trust._load_or_create_user_secret()
    assert original is not None
    path = trust._daemon_secret_path()
    before = path.read_bytes()
    # force the writers to run even though the file exists (as if the absent check had raced)
    payload = {"secret": "f" * 64}
    with pytest.raises(OSError):
        if sys.platform == "win32":
            trust._write_secret_windows(path, payload)
        else:
            trust._write_secret_posix(path, payload)
    assert path.read_bytes() == before
    assert trust._read_user_secret(path) == original
    assert [p.name for p in path.parent.iterdir() if p.name.endswith(".tmp")] == []


def test_a_secret_that_appears_after_the_absent_check_is_used_not_replaced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The loser of the race reads the winner's secret (after the normal trust checks)."""
    path = trust._daemon_secret_path()
    real_hook = trust._after_parent_pinned
    winner: dict[str, Any] = {}

    def _winner_creates_first(parent: Path) -> None:
        # runs after OUR absent check and before OUR creation: someone else wins the race
        monkeypatch.setattr(trust, "_after_parent_pinned", real_hook)
        winner["secret"] = trust._load_or_create_user_secret()

    monkeypatch.setattr(trust, "_after_parent_pinned", _winner_creates_first)
    got = trust._load_or_create_user_secret()
    assert winner.get("secret") is not None, "the winner never ran: the test is vacuous"
    assert got == winner["secret"]
    assert trust._read_user_secret(path) == got

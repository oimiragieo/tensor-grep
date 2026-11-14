"""Best-effort cleanup failures preserve authenticated daemon results."""

import pytest

from tensor_grep.cli import session_daemon as sd
from tests.unit.test_session_daemon_active_payload_lifetime import _handle
from tests.unit.test_session_daemon_active_payload_lifetime import owned_daemon as owned_daemon


@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_authenticated_result_survives_exact_owned_cleanup_failure(
    owned_daemon, monkeypatch, cleanup_fails
):
    owned = owned_daemon
    assert _handle(owned, 20)["ok"] is True
    first_id = next(iter(owned.payloads))
    canonical_root = next(iter(owned.server.implicit_session_ids))[0]
    for budget in range(21, 36):
        assert _handle(owned, budget)["ok"] is True
    untouched_id = next(reversed(owned.payloads))
    untouched = owned.payloads[untouched_id]
    untouched.write_text('{"owned":"replacement"}', encoding="utf-8")
    unrelated_identity = (canonical_root, untouched_id)
    owned.server._implicit_cleanup_owned.add(unrelated_identity)
    remove = sd._remove_implicit_session_payload
    removals = []

    def cleanup(root, session_id, *, deadline_monotonic=None):
        removals.append((str(root), session_id))
        if cleanup_fails:
            raise RuntimeError("controlled cleanup failure")
        return remove(root, session_id, deadline_monotonic=deadline_monotonic)

    monkeypatch.setattr(sd, "_remove_implicit_session_payload", cleanup)
    response = _handle(owned, 36)
    assert response["ok"] is True
    assert removals == [(canonical_root, first_id)]
    assert owned.server._implicit_cleanup_owned == {unrelated_identity}
    assert untouched.read_text(encoding="utf-8") == '{"owned":"replacement"}'
    assert owned.payloads[first_id].exists() is cleanup_fails
    assert owned.payloads[next(reversed(owned.payloads))].exists()
    assert not owned.server._implicit_active_pins
    assert not owned.server._implicit_pending_evictions
    assert not owned.server._implicit_inflight

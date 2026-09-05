from __future__ import annotations

from tensor_grep.cli.prepare_service import (
    _build_prepare_payload,
)


def test_build_prepare_payload_next_action_default_off() -> None:
    payload = _build_prepare_payload(path="src", query="def", claim=False)
    assert "next_action" not in payload


def test_build_prepare_payload_next_action_contract() -> None:
    payload = _build_prepare_payload(path="src", query="def", claim=False, include_next_action=True)
    assert "next_action" in payload
    na = payload["next_action"]
    assert isinstance(na, dict)
    assert "action" in na
    assert "on_success" in na
    assert "on_failure" in na

    success_env = na["on_success"]
    assert success_env["deadline_seconds"] > 0
    assert success_env["max_output_bytes"] > 0
    assert success_env["allow_network"] is False
    assert success_env["fail_closed_on_timeout"] is True

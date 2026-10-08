from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest

from tensor_grep.cli import subprocess_policy


def test_cli_decoder_exports_preserve_core_function_identity() -> None:
    from tensor_grep.core import subprocess_decoding

    assert subprocess_policy.decode_protocol_output is subprocess_decoding.decode_protocol_output
    assert (
        subprocess_policy.decode_diagnostic_output is subprocess_decoding.decode_diagnostic_output
    )
    assert subprocess_policy.decode_path_record is subprocess_decoding.decode_path_record


def test_subprocess_output_policies_separate_protocol_diagnostics_and_paths() -> None:
    assert subprocess_policy.decode_protocol_output(b'{"ok": true}') == '{"ok": true}'
    assert subprocess_policy.decode_protocol_output('{"ok": true}') == '{"ok": true}'
    assert subprocess_policy.decode_diagnostic_output(b"bad\xffmessage") == "bad\ufffdmessage"
    assert subprocess_policy.decode_path_record(b"filename") == os.fsdecode(b"filename")
    if os.name != "nt":
        assert subprocess_policy.decode_path_record(b"name\xff") == os.fsdecode(b"name\xff")
    try:
        subprocess_policy.decode_protocol_output(b"{\xff}")
    except UnicodeDecodeError:
        pass
    else:
        raise AssertionError("machine protocol output must reject malformed UTF-8")


def test_ripgrep_timeout_defaults_to_60s(monkeypatch) -> None:
    # Fail-fast default: ripgrep does GB/s, so a >60s search is pathological; an agent must never
    # hang ~10 minutes (the old 600s default) before getting an actionable error.
    monkeypatch.delenv("TG_RG_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("TG_SIDECAR_TIMEOUT_MS", raising=False)
    assert subprocess_policy.configured_ripgrep_timeout_seconds() == 60.0


def test_ripgrep_timeout_env_override(monkeypatch) -> None:
    monkeypatch.setenv("TG_RG_TIMEOUT_SECONDS", "120")
    monkeypatch.delenv("TG_SIDECAR_TIMEOUT_MS", raising=False)
    assert subprocess_policy.configured_ripgrep_timeout_seconds() == 120.0


def test_run_subprocess_honors_timeout(monkeypatch) -> None:
    monkeypatch.setenv("TG_SUBPROCESS_TIMEOUT_SECONDS", "1")

    try:
        subprocess_policy.run_subprocess(
            ["python", "-c", "import time; time.sleep(5)"],
            capture_output=True,
            text=True,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return

    raise AssertionError("expected subprocess timeout")


def test_default_child_gets_eof_while_parent_stdin_stays_open() -> None:
    code = (
        "import sys; from tensor_grep.cli.subprocess_policy import run_subprocess; "
        "p = run_subprocess([sys.executable, '-c', "
        "'import sys; print(repr(sys.stdin.buffer.read()))'], "
        "capture_output=True, timeout_seconds=2); "
        "sys.stdout.buffer.write(p.stdout)"
    )
    # Leave this pipe open: inheriting it makes the inner child's read block, just
    # as an MCP request stream does. communicate() would close it and hide the defect.
    with subprocess.Popen(
        [sys.executable, "-c", code],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ) as parent:
        try:
            parent.wait(timeout=8)
        finally:
            if parent.poll() is None:
                parent.kill()
                parent.wait(timeout=5)
        stdout, stderr = parent.communicate()
    assert parent.returncode == 0, stderr.decode("utf-8", errors="replace")
    assert stdout.strip() == b"b''"


@pytest.mark.parametrize("stdin", [None, subprocess.DEVNULL, subprocess.PIPE])
def test_explicit_stdin_is_preserved(monkeypatch, stdin) -> None:
    captured = {}

    def run(args, **kwargs):
        captured.update(kwargs)
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(subprocess_policy.subprocess, "run", run)
    subprocess_policy.run_subprocess(["child"], stdin=stdin)
    assert captured["stdin"] is stdin


@pytest.mark.parametrize("payload", [b"round trip", b"", "round trip", ""])
def test_supplied_input_round_trips(payload) -> None:
    result = subprocess_policy.run_subprocess(
        [sys.executable, "-c", "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())"],
        input=payload,
        text=isinstance(payload, str),
        capture_output=True,
        timeout_seconds=5,
    )
    assert result.stdout == payload


def test_explicit_stdin_file_round_trips(tmp_path) -> None:
    source = tmp_path / "stdin.bin"
    source.write_bytes(b"explicit file input")
    with source.open("rb") as stdin:
        result = subprocess_policy.run_subprocess(
            [sys.executable, "-c", "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())"],
            stdin=stdin,
            capture_output=True,
            timeout_seconds=5,
        )
    assert result.stdout == source.read_bytes()


def test_input_none_still_gets_devnull(monkeypatch) -> None:
    captured = {}

    def run(args, **kwargs):
        captured.update(kwargs)
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(subprocess_policy.subprocess, "run", run)
    subprocess_policy.run_subprocess(["child"], input=None)
    assert captured["stdin"] == subprocess.DEVNULL


# ---------------------------------------------------------------------------
# deadline_capped_timeout_seconds: tg-codemap 90s-timeout root cause. A subprocess call (e.g.
# git status/rev-parse/ls-files) is atomic -- no per-iteration deadline check is possible
# mid-call -- so the only lever to keep it from blowing a caller's --deadline is capping the
# timeout PASSED IN before the call starts. Deterministic via monkeypatched time.monotonic
# (anti-hang-test-protocol: never a real sleep/wall-clock race).
# ---------------------------------------------------------------------------


def test_deadline_capped_timeout_none_deadline_is_byte_identical_noop() -> None:
    # Every pre-existing caller passes deadline_monotonic=None (the default) -- must return
    # base_timeout_seconds completely unchanged, not merely equal by value coincidence.
    assert (
        subprocess_policy.deadline_capped_timeout_seconds(120.0, deadline_monotonic=None) == 120.0
    )
    assert subprocess_policy.deadline_capped_timeout_seconds(0.5, deadline_monotonic=None) == 0.5


def test_deadline_capped_timeout_caps_to_remaining_budget(monkeypatch) -> None:
    monkeypatch.setattr(time, "monotonic", lambda: 1000.0)
    deadline_monotonic = 1000.0 + 5.0  # 5s of real budget remains

    capped = subprocess_policy.deadline_capped_timeout_seconds(
        120.0, deadline_monotonic=deadline_monotonic
    )

    assert capped == 5.0, "must cap to the remaining budget, not the 120s git-timeout default"


def test_deadline_capped_timeout_never_exceeds_base_when_budget_is_generous(monkeypatch) -> None:
    monkeypatch.setattr(time, "monotonic", lambda: 1000.0)
    deadline_monotonic = 1000.0 + 500.0  # ample budget remains, far more than base_timeout

    capped = subprocess_policy.deadline_capped_timeout_seconds(
        120.0, deadline_monotonic=deadline_monotonic
    )

    assert capped == 120.0, "must not WIDEN the timeout past the configured base"


def test_deadline_capped_timeout_already_expired_returns_none(monkeypatch) -> None:
    monkeypatch.setattr(time, "monotonic", lambda: 1000.0)
    deadline_monotonic = 1000.0 - 1.0  # already 1s past deadline

    capped = subprocess_policy.deadline_capped_timeout_seconds(
        120.0, deadline_monotonic=deadline_monotonic
    )

    assert capped is None, "an already-expired deadline must signal 'skip the call', not 0/negative"


def test_deadline_capped_timeout_exactly_at_deadline_returns_none(monkeypatch) -> None:
    # remaining == 0 must degrade the same as remaining < 0 (never invoke a subprocess with a
    # non-positive timeout -- subprocess.run rejects timeout<=0 outright).
    monkeypatch.setattr(time, "monotonic", lambda: 1000.0)

    capped = subprocess_policy.deadline_capped_timeout_seconds(120.0, deadline_monotonic=1000.0)

    assert capped is None

"""`tg install-dense` (CEO#7, P1 -- "semantic find that works out of the box"): the one-shot
command that installs the `semantic` extra (model2vec + numpy, both torch/GPU-free) via the same
uv-tool -> uv pip -> pip cascade `tg upgrade` uses, then fetches the checksum-pinned
potion-code-16M model via the already-hardened `retrieval_dense.fetch_dense_model` -- closing the
gap where `tg find` / `tg search --semantic` silently degrade to BM25-only forever because nothing
tells a user how to get the dense leg in one step.

NO real network access and NO real pip/uv invocation anywhere in this file: `subprocess.run` is
always monkeypatched (mirrors test_cli_modes.py's `tg upgrade` tests), and
`urllib.request.urlopen` is always monkeypatched for any test that reaches the real
`fetch_dense_model` (mirrors test_retrieval_dense_fetch.py's `_make_fake_urlopen` pattern).
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import urllib.request
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from tensor_grep.cli.main import app


class _FakeHTTPResponse:
    """Duck-typed stand-in for the context-managed object `urllib.request.urlopen` returns.

    Mirrors test_retrieval_dense_fetch.py's identical fixture -- kept local (not imported cross-
    file) so this module stays self-contained under pytest's `--import-mode=importlib`.
    """

    def __init__(self, data: bytes) -> None:
        self._data = data
        self._pos = 0

    def read(self, n: int = -1) -> bytes:
        if n < 0:
            chunk = self._data[self._pos :]
            self._pos = len(self._data)
            return chunk
        chunk = self._data[self._pos : self._pos + n]
        self._pos += len(chunk)
        return chunk

    def __enter__(self) -> _FakeHTTPResponse:
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None


def _make_fake_urlopen(payloads: dict[str, bytes]) -> Any:
    """Build a fake `urlopen(request, timeout=...)` keyed by the request URL's filename."""

    def fake_urlopen(request: urllib.request.Request, timeout: float | None = None) -> Any:
        filename = request.full_url.rsplit("/", 1)[-1]
        return _FakeHTTPResponse(payloads[filename])

    return fake_urlopen


def _fake_run_ok(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(cmd, 0, stdout="Installed 2 packages", stderr="")


def _small_fake_manifest() -> tuple[dict[str, bytes], dict[str, tuple[str, int]]]:
    """A small synthetic payload set standing in for the real ~64.3MB/1.0MB/97B manifest -- keyed
    off the REAL `_FETCH_MANIFEST` filenames so the fetch's per-file loop still runs unmodified."""
    from tensor_grep.core import retrieval_dense

    payloads = {
        name: f"synthetic content for {name}".encode() for name in retrieval_dense._FETCH_MANIFEST
    }
    manifest = {
        name: (hashlib.sha256(data).hexdigest(), len(data)) for name, data in payloads.items()
    }
    return payloads, manifest


# ---------------------------------------------------------------------------------------------
# Registration completeness (AGENTS.md 4-site checklist) -- miss one and `install-dense` silently
# misroutes to ripgrep on one launcher or the routing-parity CI gate goes red.
# ---------------------------------------------------------------------------------------------


def test_install_dense_registered_in_all_four_sites() -> None:
    from tensor_grep.core.registration_check import extract_members

    repo_root = Path(__file__).resolve().parents[2]

    # Site 1: main.py's actual Typer registration (the live app, not just source text).
    typer_command_names = {
        cmd.name or cmd.callback.__name__  # type: ignore
        for cmd in app.registered_commands
    }
    assert "install-dense" in typer_command_names

    # Site 2: commands.py KNOWN_COMMANDS -- the shared Python/Rust source of truth. Uses the
    # repo's own registration_check text-scanner (the exact machinery `.tg-registration.toml`'s
    # CI gate runs) rather than importing the set, so this test also catches a malformed literal.
    commands_py_members = extract_members(
        str(repo_root / "src" / "tensor_grep" / "cli" / "commands.py"), "KNOWN_COMMANDS"
    )
    assert "install-dense" in commands_py_members

    # Site 3: tests/e2e/test_routing_parity.py PUBLIC_TOP_LEVEL_COMMANDS.
    parity_members = extract_members(
        str(repo_root / "tests" / "e2e" / "test_routing_parity.py"), "PUBLIC_TOP_LEVEL_COMMANDS"
    )
    assert "install-dense" in parity_members

    # Site 4: rust_core/src/main.rs -- not a flat string array (a clap enum variant + a dispatch
    # match arm), so extract_members does not apply; direct text search instead (mirrors
    # test_cli_bootstrap.py::test_rust_core_uses_source_of_truth's raw-text-read pattern).
    rust_main = (repo_root / "rust_core" / "src" / "main.rs").read_text(encoding="utf-8")
    assert '#[command(name = "install-dense", disable_help_flag = true)]' in rust_main
    assert "InstallDense {" in rust_main
    assert (
        'Commands::InstallDense { args } => handle_python_passthrough("install-dense", args)'
        in rust_main
    )


def test_install_dense_argv_does_not_forward_to_search() -> None:
    """Registration site 1 (commands.py KNOWN_COMMANDS): a miss here would silently misroute
    `tg install-dense` into a ripgrep search for the literal pattern "install-dense" instead of
    the real command -- mirrors test_cli_bootstrap.py's identical `codemap` regression pin."""
    from tensor_grep.cli import bootstrap

    assert bootstrap._normalize_search_invocation(["install-dense"]) is None
    assert bootstrap._normalize_search_invocation(["install-dense", "--json"]) is None


# ---------------------------------------------------------------------------------------------
# pip cascade step (mirrors test_cli_modes.py's `tg upgrade` tests) -- NEVER a real pip/uv call.
# ---------------------------------------------------------------------------------------------


def test_install_dense_pip_cascade_targets_semantic_extra(monkeypatch, tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def _fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="Installed 2 packages", stderr="")

    monkeypatch.setattr("subprocess.run", _fake_run)
    monkeypatch.setattr(
        "tensor_grep.core.retrieval_dense.fetch_dense_model",
        lambda *a, **kw: tmp_path / "model-dest",
    )

    result = CliRunner().invoke(app, ["install-dense", "--json"])

    assert result.exit_code == 0, result.output
    assert calls, "subprocess.run was never invoked -- the pip cascade never ran"
    first_call = calls[0]
    assert first_call[0] == "uv", f"uv must be tried first (upgrade's cascade order), got {calls}"
    assert "tensor-grep[semantic]" in first_call, (
        f"the semantic extra spec must be threaded through, got {first_call}"
    )
    assert first_call[-1] == "tensor-grep[semantic]", (
        "the package spec (last positional arg to `uv pip install`) must be the [semantic] "
        f"extra, not a bare tensor-grep re-upgrade -- got {first_call}"
    )


def test_install_dense_falls_back_through_cascade_when_uv_missing(
    monkeypatch, tmp_path: Path
) -> None:
    """Mirrors `tg upgrade`'s own uv-missing-falls-back-to-pip coverage, proving `install-dense`
    reuses the identical `_run_upgrade` retry loop (not a hand-rolled duplicate)."""
    calls: list[list[str]] = []

    def _fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(list(cmd))
        if cmd[0] == "uv":
            raise FileNotFoundError("uv not found")
        return subprocess.CompletedProcess(cmd, 0, stdout="Installed 2 packages", stderr="")

    monkeypatch.setattr("subprocess.run", _fake_run)
    monkeypatch.setattr(
        "tensor_grep.core.retrieval_dense.fetch_dense_model",
        lambda *a, **kw: tmp_path / "model-dest",
    )

    result = CliRunner().invoke(app, ["install-dense", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["steps"]["pip_install"]["method"] == "pip"
    assert len(calls) == 2, f"expected uv (failed) then pip (succeeded), got {calls}"


def test_install_dense_pip_cascade_exhausted_skips_fetch_never_touches_network(
    monkeypatch,
) -> None:
    def _raising_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError(f"{cmd[0]}: not found")

    monkeypatch.setattr("subprocess.run", _raising_run)

    def _must_not_be_called(request: object, timeout: float | None = None) -> None:
        raise AssertionError(
            "the model fetch must never attempt a network call when the pip cascade failed"
        )

    monkeypatch.setattr(urllib.request, "urlopen", _must_not_be_called)

    result = CliRunner().invoke(app, ["install-dense", "--json"])

    assert result.exit_code == 1, result.output
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["steps"]["pip_install"]["status"] == "failed"
    assert payload["steps"]["fetch_model"]["status"] == "skipped"
    assert payload["dense_model_dir"] is None
    assert "tensor-grep[semantic]" in payload["message"]


def test_install_dense_pip_cascade_exhausted_text_mode_exits_nonzero(monkeypatch) -> None:
    def _raising_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError(f"{cmd[0]}: not found")

    monkeypatch.setattr("subprocess.run", _raising_run)

    result = CliRunner().invoke(app, ["install-dense"])

    assert result.exit_code == 1, result.output
    assert "failed" in result.stdout.lower() or "failed" in result.stderr.lower()


# ---------------------------------------------------------------------------------------------
# Fetch step: offline fail-closed (network mocked to fail / checksum mismatch) -- never hangs
# (the fetch is deadline-bounded internally by `fetch_dense_model`), never leaves partial files.
# ---------------------------------------------------------------------------------------------


def test_install_dense_fetch_network_failure_is_fail_closed(monkeypatch, tmp_path: Path) -> None:
    dest = tmp_path / "model-dest"
    monkeypatch.setenv("TG_SEMANTIC_MODEL_DIR", str(dest))
    monkeypatch.setattr("subprocess.run", _fake_run_ok)

    def _raising_urlopen(request: object, timeout: float | None = None) -> None:
        raise OSError("simulated connection reset")

    monkeypatch.setattr(urllib.request, "urlopen", _raising_urlopen)

    result = CliRunner().invoke(app, ["install-dense", "--json"])

    assert result.exit_code == 1, result.output
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["steps"]["pip_install"]["status"] == "ok"
    assert payload["steps"]["fetch_model"]["status"] == "failed"
    assert "simulated connection reset" in payload["steps"]["fetch_model"]["detail"]
    assert payload["dense_model_dir"] is None
    assert not dest.exists(), "a failed fetch must leave no partial model directory"


def test_install_dense_fetch_checksum_mismatch_is_fail_closed_no_partial_files(
    monkeypatch, tmp_path: Path
) -> None:
    from tensor_grep.core import retrieval_dense

    dest = tmp_path / "model-dest"
    monkeypatch.setenv("TG_SEMANTIC_MODEL_DIR", str(dest))
    monkeypatch.setattr("subprocess.run", _fake_run_ok)

    wrong_payloads = {
        name: b"wrong bytes, not the pinned content for " + name.encode()
        for name in retrieval_dense._FETCH_MANIFEST
    }
    monkeypatch.setattr(urllib.request, "urlopen", _make_fake_urlopen(wrong_payloads))

    result = CliRunner().invoke(app, ["install-dense", "--json"])

    assert result.exit_code == 1, result.output
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert "checksum mismatch" in payload["steps"]["fetch_model"]["detail"]
    assert not dest.exists()
    assert not dest.parent.exists() or list(dest.parent.iterdir()) == [], (
        "fail-closed: no partial/temp download directory left behind"
    )


def test_install_dense_fetch_deadline_exceeded_is_fail_closed_never_hangs(
    monkeypatch, tmp_path: Path
) -> None:
    """Proves the command surfaces the fetch's own wall-clock deadline (never a hand-rolled
    timeout) -- mirrors test_retrieval_dense_fetch.py's slow-drip deadline test but driven through
    the `install-dense` command end-to-end. No real sleep: `time.monotonic` is monkeypatched to a
    counter that jumps 1000s on every call, so the deadline trips on the very first post-chunk
    check NO MATTER how many extraneous `time.monotonic()` calls happen elsewhere in the CLI
    dispatch path before `_download_bounded` runs (unlike a short fixed value-sequence, this stays
    correct regardless of call count -- anti-hang-test-protocol: never a hang, and here also never
    a false pass/fail from an uncontrolled call count). The drip response is deliberately FINITE
    (one real chunk then EOF): if the deadline check were ever missing or broken, the response
    still drains normally and the test fails on an assertion mismatch instead of hanging."""
    import itertools

    from tensor_grep.core import retrieval_dense

    dest = tmp_path / "model-dest"
    monkeypatch.setenv("TG_SEMANTIC_MODEL_DIR", str(dest))
    monkeypatch.setenv("TG_SEMANTIC_FETCH_DEADLINE_S", "1")
    monkeypatch.setattr("subprocess.run", _fake_run_ok)

    class _DripResponse:
        def __init__(self) -> None:
            self._chunks = [b"a" * 8, b""]
            self._idx = 0

        def read(self, n: int = -1) -> bytes:
            chunk = self._chunks[self._idx] if self._idx < len(self._chunks) else b""
            self._idx += 1
            return chunk

        def __enter__(self) -> _DripResponse:
            return self

        def __exit__(self, *exc_info: object) -> None:
            return None

    monkeypatch.setattr(urllib.request, "urlopen", lambda request, timeout=None: _DripResponse())
    call_counter = itertools.count()
    monkeypatch.setattr(retrieval_dense.time, "monotonic", lambda: next(call_counter) * 1000.0)

    result = CliRunner().invoke(app, ["install-dense", "--json"])

    assert result.exit_code == 1, result.output
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert "deadline" in payload["steps"]["fetch_model"]["detail"].lower()
    assert not dest.exists()


# ---------------------------------------------------------------------------------------------
# End-to-end success.
# ---------------------------------------------------------------------------------------------


def test_install_dense_end_to_end_success_json(monkeypatch, tmp_path: Path) -> None:
    from tensor_grep.core import retrieval_dense

    dest = tmp_path / "model-dest"
    monkeypatch.setenv("TG_SEMANTIC_MODEL_DIR", str(dest))
    monkeypatch.setattr("subprocess.run", _fake_run_ok)
    payloads, manifest = _small_fake_manifest()
    monkeypatch.setattr(retrieval_dense, "_FETCH_MANIFEST", manifest)
    monkeypatch.setattr(urllib.request, "urlopen", _make_fake_urlopen(payloads))

    result = CliRunner().invoke(app, ["install-dense", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["steps"]["pip_install"]["status"] == "ok"
    assert payload["steps"]["pip_install"]["method"] == "uv"
    assert payload["steps"]["fetch_model"]["status"] == "ok"
    assert payload["dense_model_dir"] == str(dest)
    for name, data in payloads.items():
        assert (dest / name).read_bytes() == data


def test_install_dense_text_mode_reports_steps_and_exits_zero(monkeypatch, tmp_path: Path) -> None:
    from tensor_grep.core import retrieval_dense

    dest = tmp_path / "model-dest"
    monkeypatch.setenv("TG_SEMANTIC_MODEL_DIR", str(dest))
    monkeypatch.setattr("subprocess.run", _fake_run_ok)
    payloads, manifest = _small_fake_manifest()
    monkeypatch.setattr(retrieval_dense, "_FETCH_MANIFEST", manifest)
    monkeypatch.setattr(urllib.request, "urlopen", _make_fake_urlopen(payloads))

    result = CliRunner().invoke(app, ["install-dense"])

    assert result.exit_code == 0, result.output
    assert "install-dense complete" in result.stdout
    assert "pip_install: ok" in result.stdout
    assert "fetch_model: ok" in result.stdout


# ---------------------------------------------------------------------------------------------
# Gate-B friendly degrade message (`tg find`'s dense leg): names `tg install-dense`, never the
# raw `python -m tensor_grep.core.retrieval_dense --fetch` module CLI.
# ---------------------------------------------------------------------------------------------


def test_friendly_dense_unavailable_message_names_install_dense(tmp_path: Path) -> None:
    from tensor_grep.cli.main import _friendly_dense_unavailable_message
    from tensor_grep.core.retrieval_dense import DenseUnavailableError, load_dense_model

    try:
        load_dense_model(tmp_path / "does-not-exist")
        raise AssertionError("expected DenseUnavailableError")
    except DenseUnavailableError as exc:
        message = _friendly_dense_unavailable_message(exc)

    assert "tg install-dense" in message
    assert "python -m tensor_grep.core.retrieval_dense --fetch" not in message
    assert "not fetched" in message  # the rest of the message is preserved, only the hint changes


def test_friendly_dense_unavailable_message_is_noop_when_hint_absent() -> None:
    """A dim-mismatch/malformed-shape `DenseUnavailableError` never mentions the fetch command --
    the rewrite must be a targeted substitution, not a blanket rewrite that could corrupt an
    unrelated message."""
    from tensor_grep.cli.main import _friendly_dense_unavailable_message

    exc = RuntimeError("semantic ranking unavailable: query embedding dim 5 does not match 4")
    assert _friendly_dense_unavailable_message(exc) == str(exc)


def test_find_gate_b_degrade_message_names_install_dense_end_to_end(
    tmp_path: Path, monkeypatch
) -> None:
    """End-to-end companion: drives the REAL `load_dense_model` "not fetched" path (only
    `dense_available` is stubbed, to avoid requiring the real model2vec package) through `tg find`
    and proves the CLI-facing `rank_fallback_reason` / stderr say `tg install-dense`."""
    monkeypatch.setattr("tensor_grep.core.retrieval_dense.dense_available", lambda: (True, None))
    monkeypatch.setenv("TG_SEMANTIC_MODEL_DIR", str(tmp_path / "not-fetched-model-dir"))
    (tmp_path / "invoice.py").write_text(
        "def make_invoice(invoice_id):\n    invoice = invoice_id\n    return invoice\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(app, ["find", "invoice", str(tmp_path), "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    reason = payload.get("rank_fallback_reason") or ""
    assert "tg install-dense" in reason
    assert "python -m tensor_grep.core.retrieval_dense --fetch" not in reason
    assert "tg: tg install-dense" not in result.stderr  # no doubled "tg:" prefix
    assert "tg install-dense" in result.stderr


# ---------------------------------------------------------------------------------------------
# `tg doctor` dense_model field.
# ---------------------------------------------------------------------------------------------


def test_doctor_dense_model_status_reports_not_fetched(tmp_path: Path, monkeypatch) -> None:
    from tensor_grep.cli.main import _doctor_dense_model_status

    model_dir = tmp_path / "not-fetched"
    monkeypatch.setenv("TG_SEMANTIC_MODEL_DIR", str(model_dir))

    status = _doctor_dense_model_status()

    assert status["fetched"] is False
    assert status["dir"] == str(model_dir)
    assert "tg install-dense" in status["install_hint"]


def test_doctor_dense_model_status_reports_fetched(tmp_path: Path, monkeypatch) -> None:
    from tensor_grep.cli.main import _doctor_dense_model_status

    model_dir = tmp_path / "fetched-model"
    model_dir.mkdir()
    monkeypatch.setenv("TG_SEMANTIC_MODEL_DIR", str(model_dir))

    status = _doctor_dense_model_status()

    assert status["fetched"] is True
    assert status["dir"] == str(model_dir)
    assert "install_hint" not in status


def test_doctor_json_includes_dense_model_field(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("tensor_grep.cli.main._doctor_installed_version", lambda: "9.9.9")
    monkeypatch.setattr("tensor_grep.cli.main.resolve_native_tg_binary", lambda: None)
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_session_daemon_status",
        lambda path: {"running": False},
    )
    monkeypatch.setenv("TG_SEMANTIC_MODEL_DIR", str(tmp_path / "not-fetched"))

    result = CliRunner().invoke(app, ["doctor", str(tmp_path), "--json", "--no-lsp"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["dense_model"]["fetched"] is False
    assert payload["dense_model"]["dir"] == str(tmp_path / "not-fetched")
